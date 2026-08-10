from __future__ import annotations

import mimetypes
import uuid
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal


RunRole = Literal["user", "assistant", "system", "tool"]
GenerationMode = Literal["standard", "smart"]
_ASSISTANT_MESSAGE_NAMESPACE = uuid.UUID("ee5b6470-ec94-4f63-a4ba-351b6c645c9a")


def assistant_message_id_for_run(run_id: uuid.UUID) -> uuid.UUID:
    """Stable publication identity used to make crash/retry append idempotent."""

    return uuid.uuid5(_ASSISTANT_MESSAGE_NAMESPACE, str(run_id))


@dataclass(frozen=True, slots=True)
class RunMessage:
    role: RunRole
    content: str


@dataclass(frozen=True, slots=True)
class RunViewport:
    width: int
    height: int
    zoom: float = 1.0
    slide_id: uuid.UUID | None = None
    slide_index: int | None = None


@dataclass(frozen=True, slots=True)
class SmartSelection:
    """Allow-listed editor selection supplied only for a DB-backed Smart deck."""

    html: str
    selected_text: str | None = None
    slide_id: uuid.UUID | None = None
    slide_index: int | None = None


@dataclass(frozen=True, slots=True)
class RunAsset:
    """Authoritative metadata for an already scanned, ready workspace asset."""

    id: uuid.UUID
    name: str
    media_type: str | None
    size_bytes: int
    sha256: str


@dataclass(frozen=True, slots=True)
class RuntimeArtifact:
    """A provider-created file that remains private until publication."""

    path: Path
    name: str
    media_type: str | None
    size_bytes: int


@dataclass(frozen=True, slots=True)
class OperationArtifactScope:
    """One attempt-owned output directory; no process-global state is changed."""

    directory: Path

    def __post_init__(self) -> None:
        resolved = self.directory.resolve()
        if resolved == resolved.parent:
            raise ValueError("agent_artifact_scope_invalid")
        object.__setattr__(self, "directory", resolved)

    def discover(self) -> tuple[RuntimeArtifact, ...]:
        root = self.directory
        if not root.exists():
            return ()
        if root.is_symlink() or not root.is_dir():
            raise ValueError("agent_artifact_scope_invalid")

        artifacts: list[RuntimeArtifact] = []
        for child in sorted(root.iterdir(), key=lambda value: value.name):
            # Provider output is intentionally flat.  Never follow a link or a
            # nested tree into unrelated user data.
            if child.is_symlink() or not child.is_file():
                continue
            resolved = child.resolve()
            try:
                resolved.relative_to(root)
            except ValueError as error:
                raise ValueError("agent_artifact_scope_escape") from error
            artifacts.append(
                RuntimeArtifact(
                    path=resolved,
                    name=child.name,
                    media_type=mimetypes.guess_type(child.name)[0],
                    size_bytes=child.stat().st_size,
                )
            )
        return tuple(artifacts)


@dataclass(frozen=True, slots=True)
class ToolSummary:
    name: str
    status: Literal["completed", "failed"]
    summary: str
    round: int


@dataclass(frozen=True, slots=True)
class TypedMutation:
    kind: str
    tool: str
    slide_id: uuid.UUID | None = None
    slide_index: int | None = None


@dataclass(frozen=True, slots=True)
class ApprovalRequest:
    kind: str
    reason: str
    expires_at: str | None = None


CancellationCheck = Callable[[], Awaitable[None]]


@dataclass(frozen=True, slots=True)
class RunContext:
    run_id: uuid.UUID
    presentation_id: uuid.UUID
    conversation_id: uuid.UUID
    user_message: str
    expected_revision: int
    generation_mode: GenerationMode
    history: tuple[RunMessage, ...] = ()
    viewport: RunViewport | None = None
    smart_selection: SmartSelection | None = None
    assets: tuple[RunAsset, ...] = ()
    artifact_scope: OperationArtifactScope | None = None
    cancellation_check: CancellationCheck | None = field(
        default=None, compare=False, repr=False
    )

    @property
    def assistant_message_id(self) -> uuid.UUID:
        return assistant_message_id_for_run(self.run_id)

    async def raise_if_cancelled(self) -> None:
        if self.cancellation_check is not None:
            await self.cancellation_check()


@dataclass(frozen=True, slots=True)
class AgentRunResult:
    conversation_id: uuid.UUID
    assistant_output: str
    tool_calls: tuple[str, ...] = ()
    mutations: tuple[TypedMutation, ...] = ()
    artifacts: tuple[RuntimeArtifact, ...] = ()
    tool_summaries: tuple[ToolSummary, ...] = ()
    approval_request: ApprovalRequest | None = None
    page_remake_slide_index: int | None = None

    def public_metadata(self) -> Mapping[str, Any]:
        """Return bounded metadata; provider payloads and reasoning never enter it."""

        return {
            "tool_calls": list(self.tool_calls),
            "mutations": [mutation.kind for mutation in self.mutations],
            "artifact_count": len(self.artifacts),
        }
