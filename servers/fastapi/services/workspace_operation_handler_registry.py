"""Public handler contract and registry for workspace operation kinds."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol
import uuid


@dataclass(frozen=True, slots=True)
class WorkspaceOperationClaim:
    operation_id: uuid.UUID
    owner_id: uuid.UUID | None
    owner_scope: str
    conversation_id: uuid.UUID
    presentation_id: uuid.UUID
    kind: str
    request_payload: Mapping[str, Any]
    expected_revision: int | None
    attempt_number: int
    attempt_token: str
    fencing_token: int


@dataclass(frozen=True, slots=True)
class WorkspaceOperationResult:
    payload: Mapping[str, Any] = field(default_factory=dict)
    result_revision: int | None = None


class WorkspaceOperationExecutionContext(Protocol):
    claim: WorkspaceOperationClaim

    async def progress(
        self,
        *,
        stage: str,
        current: int | None = None,
        total: int | None = None,
        percent: int | None = None,
        message: str | None = None,
    ) -> bool: ...

    async def emit_durable_event(
        self,
        *,
        event_type: str,
        payload: Mapping[str, Any],
    ) -> bool:
        """Append one allow-listed, fenced public runtime event.

        Implementations must reject unknown event types and sanitize payloads;
        callers may never use this boundary for provider traces or hidden
        reasoning.  Transient token deltas use the runtime broadcaster instead.
        """
        ...

    async def cancellation_requested(self) -> bool: ...

    async def raise_if_cancelled(self) -> None: ...


class WorkspaceOperationHandler(Protocol):
    async def execute(
        self, context: WorkspaceOperationExecutionContext
    ) -> WorkspaceOperationResult | Mapping[str, Any] | None: ...


WorkspaceOperationHandlerCallable = Callable[
    [WorkspaceOperationExecutionContext],
    Awaitable[WorkspaceOperationResult | Mapping[str, Any] | None],
]

WorkspaceOperationRecoveryPolicy = Literal["automatic", "manual"]
_VALID_RECOVERY_POLICIES = frozenset({"automatic", "manual"})
# The existing presentation generation adapter resumes a separately durable,
# idempotent generation job.  Keep that established recovery behavior even
# when recovery runs before application startup has registered handlers.
_BUILTIN_RECOVERY_POLICIES: dict[str, WorkspaceOperationRecoveryPolicy] = {
    "presentation.generate": "automatic",
    "presentation.delete": "automatic",
    "template.apply": "automatic",
}


class WorkspaceOperationExecutionError(RuntimeError):
    def __init__(
        self,
        code: str,
        *,
        retryable: bool,
        detail: Mapping[str, Any] | None = None,
    ) -> None:
        self.code = code
        self.retryable = retryable
        self.detail = dict(detail or {})
        super().__init__(code)


class RetryableWorkspaceOperationError(WorkspaceOperationExecutionError):
    def __init__(
        self, code: str, *, detail: Mapping[str, Any] | None = None
    ) -> None:
        super().__init__(code, retryable=True, detail=detail)


class NonRetryableWorkspaceOperationError(WorkspaceOperationExecutionError):
    def __init__(
        self, code: str, *, detail: Mapping[str, Any] | None = None
    ) -> None:
        super().__init__(code, retryable=False, detail=detail)


class WorkspaceOperationBlocked(WorkspaceOperationExecutionError):
    """A safe, resumable wait state such as missing transient credentials."""

    def __init__(
        self, code: str, *, detail: Mapping[str, Any] | None = None
    ) -> None:
        super().__init__(code, retryable=False, detail=detail)


class WorkspaceOperationCancelled(RuntimeError):
    pass


class WorkspaceOperationLeaseLost(RuntimeError):
    pass


class WorkspaceOperationHandlerRegistry:
    def __init__(self) -> None:
        self._handlers: dict[
            str, WorkspaceOperationHandler | WorkspaceOperationHandlerCallable
        ] = {}
        self._recovery_policies: dict[str, WorkspaceOperationRecoveryPolicy] = {}

    def register(
        self,
        kind: str,
        handler: WorkspaceOperationHandler | WorkspaceOperationHandlerCallable,
        *,
        replace: bool = False,
        recovery_policy: WorkspaceOperationRecoveryPolicy | None = None,
    ) -> None:
        normalized = kind.strip()
        if not normalized:
            raise ValueError("workspace operation kind must not be empty")
        if normalized in self._handlers and not replace:
            raise ValueError(f"handler already registered for {normalized}")
        resolved_policy = recovery_policy or _BUILTIN_RECOVERY_POLICIES.get(
            normalized, "manual"
        )
        if resolved_policy not in _VALID_RECOVERY_POLICIES:
            raise ValueError(
                "workspace operation recovery policy must be automatic or manual"
            )
        self._handlers[normalized] = handler
        self._recovery_policies[normalized] = resolved_policy

    def unregister(self, kind: str) -> None:
        self._handlers.pop(kind, None)
        self._recovery_policies.pop(kind, None)

    def resolve(
        self, kind: str
    ) -> WorkspaceOperationHandler | WorkspaceOperationHandlerCallable | None:
        return self._handlers.get(kind)

    def recovery_policy(self, kind: str) -> WorkspaceOperationRecoveryPolicy:
        """Return the crash-recovery policy, defaulting unknown kinds safely."""

        normalized = kind.strip()
        return self._recovery_policies.get(
            normalized,
            _BUILTIN_RECOVERY_POLICIES.get(normalized, "manual"),
        )

    def clear(self) -> None:
        self._handlers.clear()
        self._recovery_policies.clear()

    @property
    def kinds(self) -> tuple[str, ...]:
        return tuple(sorted(self._handlers))


workspace_operation_handler_registry = WorkspaceOperationHandlerRegistry()


def register_workspace_operation_handler(
    kind: str,
    handler: WorkspaceOperationHandler | WorkspaceOperationHandlerCallable,
    *,
    replace: bool = False,
    recovery_policy: WorkspaceOperationRecoveryPolicy | None = None,
) -> None:
    workspace_operation_handler_registry.register(
        kind,
        handler,
        replace=replace,
        recovery_policy=recovery_policy,
    )


def unregister_workspace_operation_handler(kind: str) -> None:
    workspace_operation_handler_registry.unregister(kind)
