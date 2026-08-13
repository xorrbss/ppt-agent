"""Provider call lease value object kept separate from admission policy."""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from services.provider_runtime_control import ProviderRuntimeControl


class ProviderCallLease:
    """One admitted provider call; ``finish`` must be called exactly once."""

    __slots__ = (
        "_control",
        "_finished",
        "_half_open_probe",
        "job_id",
        "request_id",
        "is_retry",
    )

    def __init__(
        self,
        control: ProviderRuntimeControl,
        *,
        job_id: str,
        request_id: str,
        is_retry: bool,
        half_open_probe: bool,
    ) -> None:
        self._control = control
        self._finished = False
        self._half_open_probe = half_open_probe
        self.job_id = job_id
        self.request_id = request_id
        self.is_retry = is_retry

    async def finish(
        self,
        *,
        actual_cost_microunits: int | None,
        outcome: Literal["success", "failure", "retryable_failure"],
    ) -> None:
        """Reconcile actual cost and release concurrency."""

        await self._control._finish_lease(
            self,
            actual_cost_microunits=actual_cost_microunits,
            outcome=outcome,
        )

    async def __aenter__(self) -> ProviderCallLease:
        return self

    async def __aexit__(self, exc_type, exc, traceback) -> None:
        if self._finished:
            return
        outcome = "success" if exc_type is None else "failure"
        await self.finish(actual_cost_microunits=None, outcome=outcome)
