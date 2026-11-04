"""Pure public-operation admission value objects and input planners.

This small facade keeps the durable admission service focused on persistence
and provider-call state transitions while preserving its import surface.
"""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import re
import uuid

from utils.owner_context import (
    get_current_owner_id,
    get_current_tenant_id,
    get_current_trace_id,
)
from services.public_policy_versions import PUBLIC_DEFAULT_POLICY_VERSION
from services.source_images.budget import SOURCE_IMAGE_PHYSICAL_CALL_LIMIT


CONTENT_BRIEF_RETRY_REQUESTS = 1
# The outline page-count retry has its own allowance so it never takes a slot
# from the per-slide validation retries.
OUTLINE_RETRY_REQUESTS = 1
PUBLIC_PHASE2_DEFAULT_POLICY_VERSION = PUBLIC_DEFAULT_POLICY_VERSION
_TERMINAL_STATES = frozenset(
    {"succeeded", "failed", "cancelled", "budget_exhausted", "deadline_exceeded"}
)
_HASH_RE = re.compile(r"^[a-f0-9]{64}$")
_PHASE2_COUNTER_FIELDS = frozenset({"queued_resource_units", "active_resource_units"})
_PHASE2_ADMISSION_FIELDS = frozenset({"resource_units", "deadline_at", "policy_digest", "fence"})
_PHASE2_COMMIT_RESERVE_SECONDS = 10
_PUBLIC_OPERATION_CONTRACT_VERSION = "public-operation/v2"
_PUBLIC_OPERATION_EXECUTION_PROFILE_VERSION = "public-generation-export/v2"
PUBLIC_PROVIDER_STAGES = frozenset(
    {
        "outline",
        "content_brief",
        "deck_plan",
        "header",
        "author",
        "illustration",
        "repair",
        "vqa",
    }
)
_SAFE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$")


@dataclass(frozen=True, slots=True)
class PrincipalContext:
    """Identity already verified by the external session trust boundary."""

    owner_id: uuid.UUID
    tenant_id: str
    trace_id: str


@dataclass(frozen=True, slots=True)
class ProviderRequestPlan:
    slide_count: int
    image_budget: int
    repair_budget: int
    vqa_budget: int
    source_image_budget: int = 0
    header_title_budget: int = 0
    header_design_budget: int = 0
    representative_body_budget: int = 0

    def __post_init__(self) -> None:
        if type(self.header_title_budget) is not int or self.header_title_budget not in (0, 1):
            raise ValueError("svg_common_header_title_budget_invalid")
        if type(self.header_design_budget) is not int or not 0 <= self.header_design_budget <= 4:
            raise ValueError("svg_common_header_design_budget_invalid")
        if type(self.representative_body_budget) is not int or not 0 <= self.representative_body_budget <= 2:
            raise ValueError("svg_common_header_representative_body_budget_invalid")

    @property
    def initial_request_limit(self) -> int:
        return (
            4
            + self.slide_count
            + min(self.image_budget, self.slide_count)
            + self.repair_budget
            + self.vqa_budget
            + (SOURCE_IMAGE_PHYSICAL_CALL_LIMIT * (self.source_image_budget + 1)
               if self.source_image_budget else 0)
            + min(self.slide_count, self.source_image_budget)
            + self.header_title_budget
            + min(2, self.header_design_budget)
            + min(1, self.representative_body_budget)
        )

    @property
    def retry_request_limit(self) -> int:
        """One bounded validation retry per authored slide, plus the dedicated
        content brief and outline page-count retries."""
        return (self.slide_count + CONTENT_BRIEF_RETRY_REQUESTS
                + OUTLINE_RETRY_REQUESTS
                + max(0, self.header_design_budget - 2)
                + max(0, self.representative_body_budget - 1))


@dataclass(frozen=True, slots=True)
class WeightedResourcePlan:
    """Content-free scheduler estimate persisted as resource units."""

    pixel_count: int
    device_pixel_ratio_milli: int
    dom_node_count: int
    font_count: int
    svg_complexity: int
    estimated_memory_mib: int
    fidelity_mode: str

    @property
    def resource_units(self) -> int:
        mode_weight = {"standard": 0, "fidelity": 2, "hybrid": 4}.get(
            self.fidelity_mode
        )
        if mode_weight is None:
            raise ValueError("public_fidelity_mode_invalid")
        dpr_squared_milli = self.device_pixel_ratio_milli**2
        pixel_units = (self.pixel_count * dpr_squared_milli + 999_999_999_999) // 1_000_000_000_000
        return max(
            1,
            pixel_units
            + ((self.dom_node_count + 999) // 1_000)
            + ((self.font_count + 7) // 8)
            + ((self.svg_complexity + 249) // 250)
            + ((self.estimated_memory_mib + 127) // 128)
            + mode_weight,
        )


class PublicAdmissionRejected(RuntimeError):
    def __init__(self, code: str, *, retry_after_seconds: int | None = None) -> None:
        super().__init__(code)
        self.code = code
        self.retry_after_seconds = retry_after_seconds
        self.retryable = code not in {
            "public_provider_request_limit_exceeded",
            "public_provider_retry_limit_exceeded",
            "public_provider_estimated_cost_limit_exceeded",
            "public_provider_actual_cost_limit_exceeded",
            "public_provider_inflight_replay_unsupported",
            "public_provider_terminal_replay_unsupported",
        }


@dataclass(frozen=True, slots=True)
class PublicProviderCallLease:
    logical_call_id: uuid.UUID
    admission_id: uuid.UUID
    provider_idempotency_key: str
    state: str
    reused: bool = False

    @property
    def is_terminal_reuse(self) -> bool:
        return self.reused and self.state != "reserved"

    @property
    def is_inflight_reuse(self) -> bool:
        return self.reused and self.state == "reserved"


def _logical_call_ordinal(
    *, operation_id: uuid.UUID, stage: str, slide_index: int,
    logical_call_ordinal: int | None, logical_call_key: str | None,
) -> int:
    if logical_call_key is None:
        if logical_call_ordinal is None or logical_call_ordinal < 1:
            raise ValueError("logical_call_ordinal_must_be_positive")
        return logical_call_ordinal
    if not _SAFE_ID_RE.fullmatch(logical_call_key):
        raise ValueError("public_provider_logical_call_key_invalid")
    digest = sha256(
        f"public-provider-slot/v1:{operation_id}:{stage}:{slide_index}:"
        f"{logical_call_key}".encode("utf-8")
    ).digest()
    return max(1, int.from_bytes(digest[:4], "big") & 0x7FFF_FFFF)


def _nonnegative(value: int, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{field}_must_be_nonnegative")
    return value


def _principal(principal: PrincipalContext) -> PrincipalContext:
    if not isinstance(principal.owner_id, uuid.UUID):
        raise ValueError("principal_owner_id_required")
    if not _SAFE_ID_RE.fullmatch(principal.tenant_id):
        raise ValueError("principal_tenant_id_invalid")
    if not _SAFE_ID_RE.fullmatch(principal.trace_id):
        raise ValueError("principal_trace_id_invalid")
    return principal


def current_principal_context() -> PrincipalContext:
    owner_id = get_current_owner_id()
    if owner_id is None:
        raise PublicAdmissionRejected("verified_principal_required")
    return _principal(
        PrincipalContext(
            owner_id=owner_id,
            tenant_id=get_current_tenant_id() or "default",
            trace_id=get_current_trace_id() or f"operation-{uuid.uuid4()}",
        )
    )


def plan_provider_requests(*, slide_count: int, image_budget: int,
                           repair_budget: int, vqa_budget: int,
                           source_image_budget: int = 0,
                           header_title_budget: int = 0, header_design_budget: int = 0,
                           representative_body_budget: int = 0) -> ProviderRequestPlan:
    plan = ProviderRequestPlan(
        slide_count=_nonnegative(slide_count, "slide_count"),
        image_budget=_nonnegative(image_budget, "image_budget"),
        repair_budget=_nonnegative(repair_budget, "repair_budget"),
        vqa_budget=_nonnegative(vqa_budget, "vqa_budget"),
        source_image_budget=_nonnegative(source_image_budget, "source_image_budget"),
        header_title_budget=header_title_budget,
        header_design_budget=header_design_budget,
        representative_body_budget=representative_body_budget,
    )
    if plan.slide_count < 1:
        raise ValueError("slide_count_must_be_positive")
    return plan


def plan_weighted_resources(*, pixel_count: int, device_pixel_ratio_milli: int,
                            dom_node_count: int, font_count: int,
                            svg_complexity: int, estimated_memory_mib: int,
                            fidelity_mode: str) -> WeightedResourcePlan:
    plan = WeightedResourcePlan(
        pixel_count=_nonnegative(pixel_count, "pixel_count"),
        device_pixel_ratio_milli=_nonnegative(device_pixel_ratio_milli, "device_pixel_ratio_milli"),
        dom_node_count=_nonnegative(dom_node_count, "dom_node_count"),
        font_count=_nonnegative(font_count, "font_count"),
        svg_complexity=_nonnegative(svg_complexity, "svg_complexity"),
        estimated_memory_mib=_nonnegative(estimated_memory_mib, "estimated_memory_mib"),
        fidelity_mode=fidelity_mode,
    )
    if plan.device_pixel_ratio_milli < 1:
        raise ValueError("device_pixel_ratio_milli_must_be_positive")
    _ = plan.resource_units
    return plan
