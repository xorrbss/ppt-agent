"""Durable immutable Scene-envelope and one-repair budget checkpoints."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
from threading import RLock
from typing import Any, Iterable, Mapping
import uuid

from services.authored_checkpoint_artifacts import atomic_write, checkpoint_root
from services.authored_vnext.generation.scene_envelope import (
    AuthoredSlideSceneEnvelope,
)


SCHEMA_VERSION = "authored-scene-checkpoint/v1"
_STATE_LOCK_STRIPES = tuple(RLock() for _ in range(64))


class SceneRepairBudgetExhausted(RuntimeError):
    def __init__(self, *, repeated: bool) -> None:
        super().__init__(
            "authored_scene_repair_fingerprint_repeated"
            if repeated
            else "authored_scene_repair_budget_exhausted"
        )
        self.repeated = repeated


def _canonical_json(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _digest(value: object) -> str:
    payload = value if isinstance(value, str) else _canonical_json(value)
    return "sha256:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()


def repair_failure_fingerprint(
    envelope: AuthoredSlideSceneEnvelope,
    *,
    error_code: str,
    issues: Iterable[Mapping[str, Any]],
) -> str:
    messages = sorted(
        str(issue.get("message") or "").strip()
        for issue in issues
        if str(issue.get("message") or "").strip()
    )
    return _digest(
        {
            "sceneDigest": envelope.scene_digest,
            "errorCode": error_code,
            "issues": messages,
        }
    )


@dataclass(frozen=True, slots=True)
class AuthoredSceneCheckpointState:
    envelope: AuthoredSlideSceneEnvelope
    repair_count: int = 0
    failure_fingerprint: str | None = None

    def to_payload(self) -> dict[str, Any]:
        body = {
            "schemaVersion": SCHEMA_VERSION,
            "slideIndex": self.envelope.slide_index,
            "envelope": self.envelope.to_payload(),
            "repairCount": self.repair_count,
            "failureFingerprint": self.failure_fingerprint,
        }
        return {**body, "stateDigest": _digest(body)}

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any]) -> "AuthoredSceneCheckpointState":
        if payload.get("schemaVersion") != SCHEMA_VERSION:
            raise ValueError("authored_scene_checkpoint_schema_invalid")
        body = {key: value for key, value in payload.items() if key != "stateDigest"}
        if payload.get("stateDigest") != _digest(body):
            raise ValueError("authored_scene_checkpoint_digest_mismatch")
        envelope_payload = payload.get("envelope")
        if not isinstance(envelope_payload, Mapping):
            raise ValueError("authored_scene_checkpoint_envelope_missing")
        envelope = AuthoredSlideSceneEnvelope.from_payload(envelope_payload)
        repair_count = int(payload.get("repairCount", -1))
        fingerprint = payload.get("failureFingerprint")
        if repair_count not in {0, 1}:
            raise ValueError("authored_scene_repair_budget_invalid")
        if repair_count == 0 and fingerprint is not None:
            raise ValueError("authored_scene_repair_fingerprint_unclaimed")
        if repair_count == 1 and not isinstance(fingerprint, str):
            raise ValueError("authored_scene_repair_fingerprint_missing")
        return cls(
            envelope=envelope,
            repair_count=repair_count,
            failure_fingerprint=fingerprint,
        )


def _state_path(job_id: uuid.UUID, slide_index: int) -> Path:
    if slide_index < 0:
        raise ValueError("authored_scene_checkpoint_slide_invalid")
    directory = checkpoint_root() / str(job_id)
    directory.mkdir(parents=True, exist_ok=True)
    resolved = directory.resolve()
    if resolved.parent != checkpoint_root() or resolved.name != str(job_id):
        raise ValueError("authored_scene_checkpoint_path_invalid")
    return resolved / f"slide-{slide_index}.scene-state.json"


@contextmanager
def _scene_state_lock(job_id: uuid.UUID, slide_index: int):
    state_path = _state_path(job_id, slide_index)
    stripe = _STATE_LOCK_STRIPES[
        int.from_bytes(
            hashlib.sha256(str(state_path).encode("utf-8")).digest()[:8],
            "big",
        )
        % len(_STATE_LOCK_STRIPES)
    ]
    lock_path = state_path.with_suffix(".lock")
    with stripe, lock_path.open("a+b") as handle:
        handle.seek(0, os.SEEK_END)
        if handle.tell() == 0:
            handle.write(b"0")
            handle.flush()
        handle.seek(0)
        if os.name == "nt":
            import msvcrt

            msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            handle.seek(0)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def read_scene_checkpoint_state(
    job_id: uuid.UUID,
    slide_index: int,
) -> AuthoredSceneCheckpointState | None:
    path = _state_path(job_id, slide_index)
    if path.is_symlink():
        raise ValueError("authored_scene_checkpoint_path_invalid")
    try:
        payload = json.loads(path.read_text("utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("authored_scene_checkpoint_unreadable") from exc
    if not isinstance(payload, Mapping):
        raise ValueError("authored_scene_checkpoint_payload_invalid")
    state = AuthoredSceneCheckpointState.from_payload(payload)
    if state.envelope.slide_index != slide_index:
        raise ValueError("authored_scene_checkpoint_slide_mismatch")
    return state


def write_scene_checkpoint_state(
    job_id: uuid.UUID,
    state: AuthoredSceneCheckpointState,
) -> None:
    path = _state_path(job_id, state.envelope.slide_index)
    payload = state.to_payload()
    content = _canonical_json(payload).encode("utf-8")
    immutable = path.with_name(
        f".{path.stem}.r{state.envelope.revision}."
        f"{str(payload['stateDigest']).removeprefix('sha256:')[:16]}.json"
    )
    if immutable.exists():
        if immutable.read_bytes() != content:
            raise ValueError("authored_scene_checkpoint_immutable_conflict")
    else:
        atomic_write(immutable, content)
    atomic_write(path, content)


def persist_scene_envelope(
    job_id: uuid.UUID,
    envelope: AuthoredSlideSceneEnvelope,
) -> AuthoredSceneCheckpointState:
    with _scene_state_lock(job_id, envelope.slide_index):
        current = read_scene_checkpoint_state(job_id, envelope.slide_index)
        if (
            current is not None
            and current.envelope.scene_digest == envelope.scene_digest
        ):
            return current
        state = AuthoredSceneCheckpointState(
            envelope=envelope,
            repair_count=current.repair_count if current is not None else 0,
            failure_fingerprint=(
                current.failure_fingerprint if current is not None else None
            ),
        )
        write_scene_checkpoint_state(job_id, state)
        return state


def claim_scene_repair(
    job_id: uuid.UUID,
    envelope: AuthoredSlideSceneEnvelope,
    *,
    error_code: str,
    issues: Iterable[Mapping[str, Any]],
) -> AuthoredSceneCheckpointState:
    fingerprint = repair_failure_fingerprint(
        envelope,
        error_code=error_code,
        issues=issues,
    )
    with _scene_state_lock(job_id, envelope.slide_index):
        current = read_scene_checkpoint_state(job_id, envelope.slide_index)
        if current is None:
            current = AuthoredSceneCheckpointState(envelope=envelope)
        elif current.envelope.scene_digest != envelope.scene_digest:
            current = AuthoredSceneCheckpointState(
                envelope=envelope,
                repair_count=current.repair_count,
                failure_fingerprint=current.failure_fingerprint,
            )
        if current.repair_count >= 1:
            raise SceneRepairBudgetExhausted(
                repeated=current.failure_fingerprint == fingerprint
            )
        claimed = AuthoredSceneCheckpointState(
            envelope=envelope,
            repair_count=1,
            failure_fingerprint=fingerprint,
        )
        write_scene_checkpoint_state(job_id, claimed)
        return claimed


def persist_repaired_scene_envelope(
    job_id: uuid.UUID,
    envelope: AuthoredSlideSceneEnvelope,
    claimed: AuthoredSceneCheckpointState,
) -> AuthoredSceneCheckpointState:
    if claimed.repair_count != 1 or claimed.failure_fingerprint is None:
        raise ValueError("authored_scene_repair_claim_required")
    with _scene_state_lock(job_id, envelope.slide_index):
        state = AuthoredSceneCheckpointState(
            envelope=envelope,
            repair_count=1,
            failure_fingerprint=claimed.failure_fingerprint,
        )
        write_scene_checkpoint_state(job_id, state)
        return state


__all__ = [
    "AuthoredSceneCheckpointState",
    "SceneRepairBudgetExhausted",
    "claim_scene_repair",
    "persist_repaired_scene_envelope",
    "persist_scene_envelope",
    "read_scene_checkpoint_state",
    "repair_failure_fingerprint",
]
