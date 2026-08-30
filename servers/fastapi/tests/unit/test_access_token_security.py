from datetime import datetime, timezone
import uuid

from api.v1.auth.token import _view
from models.sql.access_token import (
    AccessToken,
    access_token_reference_prefix,
    hash_access_token,
    issue_access_token,
)


def test_api_key_is_stored_as_digest_and_listed_as_non_authenticating_reference():
    raw_token, token_hash = issue_access_token()
    assert raw_token.startswith("sk-presenton-")
    assert raw_token not in token_hash
    assert token_hash == hash_access_token(raw_token)

    record = AccessToken(
        token_hash=token_hash,
        user_id=uuid.uuid4(),
        created_at=datetime.now(timezone.utc),
    )
    listed = _view(record)
    assert listed.token != raw_token
    assert access_token_reference_prefix(listed.token) == token_hash[:24]

    created = _view(record, token=raw_token)
    assert created.token == raw_token
