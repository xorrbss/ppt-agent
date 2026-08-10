"""Presentation agent runtime contracts and adapters.

The runtime is deliberately persistence-free: it receives an immutable
``RunContext`` and emits a typed ``AgentRunResult``.  Workspace publication is
the only layer allowed to make the result, mutations, artifacts and revision
visible together.
"""

from services.agent_runtime.contracts import (
    AgentRunResult,
    ApprovalRequest,
    OperationArtifactScope,
    RunAsset,
    RunContext,
    RunMessage,
    RunViewport,
    RuntimeArtifact,
    SmartSelection,
    ToolSummary,
    TypedMutation,
)
from services.agent_runtime.events import (
    BoundedCheckpointEventSink,
    CompositeEventSink,
    EventSink,
    NullEventSink,
    RuntimeEventFenceLost,
    TRANSIENT_RUN_EVENTS,
    TransientRunEventBroadcaster,
)
from services.agent_runtime.runtime import PresentationAgentRuntime

__all__ = [
    "AgentRunResult",
    "ApprovalRequest",
    "BoundedCheckpointEventSink",
    "CompositeEventSink",
    "EventSink",
    "NullEventSink",
    "OperationArtifactScope",
    "PresentationAgentRuntime",
    "RunAsset",
    "RunContext",
    "RunMessage",
    "RunViewport",
    "RuntimeEventFenceLost",
    "RuntimeArtifact",
    "SmartSelection",
    "TRANSIENT_RUN_EVENTS",
    "ToolSummary",
    "TransientRunEventBroadcaster",
    "TypedMutation",
]
