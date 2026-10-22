import base64
import hashlib
import hmac
import json
import secrets
import time
from typing import Optional

from fastapi import Request

from api.v1.auth.secret_store import (
    clear_auth_secret,
    get_or_create_auth_secret,
    peek_auth_secret,
    rotate_auth_secret,
)
from utils.get_env import get_user_config_path_env
from utils.user_config_store import read_user_config_file, update_user_config_file

SESSION_COOKIE_NAME = "presenton_session"
PBKDF2_ITERATIONS = 200_000
SESSION_TTL_SECONDS = 60 * 60 * 24 * 30
# The signing secret is owned by `api.v1.auth.secret_store`; this module only
# persists the credential pair and clears the legacy secret field on reset.
AUTH_CONFIG_FIELDS = ("AUTH_USERNAME", "AUTH_PASSWORD_HASH", "AUTH_SECRET_KEY")
_PERSISTED_CREDENTIAL_FIELDS = ("AUTH_USERNAME", "AUTH_PASSWORD_HASH")


def _base64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("utf-8")


def _base64url_decode(value: str) -> bytes:
    padded = value + "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(padded.encode("utf-8"))


def _load_user_config() -> dict:
    user_config_path = get_user_config_path_env()
    if not user_config_path:
        return {}

    try:
        return read_user_config_file(user_config_path)
    except Exception:
        return {}


def _save_user_config(config: dict, removed_keys: tuple[str, ...] = ()) -> None:
    user_config_path = get_user_config_path_env()
    if not user_config_path:
        raise ValueError("USER_CONFIG_PATH is not set")

    auth_config = {
        key: config[key]
        for key in _PERSISTED_CREDENTIAL_FIELDS
        if key in config
    }

    def merge_auth_config(existing: dict) -> dict:
        existing.update(auth_config)
        for key in removed_keys:
            existing.pop(key, None)
        return existing

    update_user_config_file(user_config_path, merge_auth_config)


def _hash_password(password: str, salt: bytes) -> bytes:
    return hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt, PBKDF2_ITERATIONS
    )


def _encode_password_hash(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = _hash_password(password, salt)
    salt_encoded = _base64url_encode(salt)
    digest_encoded = _base64url_encode(digest)
    return (
        f"pbkdf2_sha256${PBKDF2_ITERATIONS}${salt_encoded}${digest_encoded}"
    )


def _verify_password_hash(password: str, encoded_hash: str) -> bool:
    try:
        algorithm, iterations_str, salt_encoded, digest_encoded = encoded_hash.split("$")
        if algorithm != "pbkdf2_sha256":
            return False

        iterations = int(iterations_str)
        salt = _base64url_decode(salt_encoded)
        expected_digest = _base64url_decode(digest_encoded)
        actual_digest = hashlib.pbkdf2_hmac(
            "sha256", password.encode("utf-8"), salt, iterations
        )
        return hmac.compare_digest(actual_digest, expected_digest)
    except Exception:
        return False


def is_auth_configured() -> bool:
    config = _load_user_config()
    return bool(config.get("AUTH_USERNAME") and config.get("AUTH_PASSWORD_HASH"))


def get_configured_auth_username() -> Optional[str]:
    config = _load_user_config()
    username = config.get("AUTH_USERNAME")
    if isinstance(username, str) and username.strip():
        return username.strip()
    return None


def setup_initial_credentials(username: str, password: str) -> None:
    cleaned_username = (username or "").strip()
    if len(cleaned_username) < 3:
        raise ValueError("Username must be at least 3 characters")

    if len(password or "") < 6:
        raise ValueError("Password must be at least 6 characters")

    config = _load_user_config()
    if config.get("AUTH_USERNAME") and config.get("AUTH_PASSWORD_HASH"):
        raise ValueError("Credentials already configured")

    config["AUTH_USERNAME"] = cleaned_username
    config["AUTH_PASSWORD_HASH"] = _encode_password_hash(password)
    _save_user_config(config)
    get_or_create_auth_secret()


def force_set_credentials(username: str, password: str) -> None:
    """Overwrite stored credentials; used by env-based preseed/override."""
    cleaned_username = (username or "").strip()
    if len(cleaned_username) < 3:
        raise ValueError("Username must be at least 3 characters")

    if len(password or "") < 6:
        raise ValueError("Password must be at least 6 characters")

    config = _load_user_config()
    config["AUTH_USERNAME"] = cleaned_username
    config["AUTH_PASSWORD_HASH"] = _encode_password_hash(password)
    _save_user_config(config)
    rotate_auth_secret()


def clear_stored_credentials() -> None:
    """Remove stored credentials; next boot will request setup again."""
    config = _load_user_config()
    if [key for key in AUTH_CONFIG_FIELDS if config.pop(key, None) is not None]:
        _save_user_config(config, removed_keys=AUTH_CONFIG_FIELDS)
    # Also clears the vault entry when a credential backend is configured.
    clear_auth_secret()


def _sign_payload(payload_encoded: str, secret: str) -> str:
    signature = hmac.new(
        secret.encode("utf-8"), payload_encoded.encode("utf-8"), hashlib.sha256
    ).digest()
    return _base64url_encode(signature)


def create_session_token(username: str) -> str:
    secret = get_or_create_auth_secret()
    issued_at = int(time.time())
    payload = {
        "v": 1,
        "u": username,
        "iat": issued_at,
        "exp": issued_at + SESSION_TTL_SECONDS,
    }
    payload_encoded = _base64url_encode(
        json.dumps(payload, separators=(",", ":")).encode("utf-8")
    )
    signature_encoded = _sign_payload(payload_encoded, secret)
    return f"{payload_encoded}.{signature_encoded}"


def validate_session_token(token: Optional[str]) -> Optional[str]:
    if not token:
        return None

    stored_username = _load_user_config().get("AUTH_USERNAME")
    secret = peek_auth_secret()
    if not stored_username or not secret:
        return None

    try:
        payload_encoded, signature_encoded = token.split(".", 1)
    except ValueError:
        return None

    expected_signature = _sign_payload(payload_encoded, secret)
    if not hmac.compare_digest(signature_encoded, expected_signature):
        return None

    try:
        payload = json.loads(_base64url_decode(payload_encoded))
    except Exception:
        return None

    username = payload.get("u")
    version = payload.get("v")
    expires_at = payload.get("exp")
    if not isinstance(username, str) or not isinstance(expires_at, int):
        return None
    if version != 1 or not hmac.compare_digest(username, stored_username):
        return None
    if expires_at < int(time.time()):
        return None
    return username


def get_session_token_from_request(request: Request) -> Optional[str]:
    cookie_token = request.cookies.get(SESSION_COOKIE_NAME)
    if cookie_token:
        return cookie_token

    auth_header = request.headers.get("Authorization", "")
    if auth_header.lower().startswith("bearer "):
        return auth_header[7:].strip() or None
    return None

