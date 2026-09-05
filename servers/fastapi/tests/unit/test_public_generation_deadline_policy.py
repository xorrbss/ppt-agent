"""Slide-count scaled public generation deadline contract.

Covers the pure formula boundaries, the deadline actually pinned by v3
admission, the fact that retry/resume never re-grants a whole budget, and the
untouched fixed budget of the already-pinned v1/v2 policies.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
import uuid

import pytest
from sqlalchemy import create_engine, event, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlmodel import SQLModel
from alembic import command
from alembic.config import Config

from models.sql.presentation import PresentationModel
from models.sql.public_admission import (
    PublicOperationAdmission,
    PublicPolicyProfile,
    PublicProviderLogicalCall,
    PublicQuotaCounter,
    PublicResourceLease,
)
from models.sql.user import User
from models.sql.workspace import WorkspaceConversation, WorkspaceOperation
from services.public_generation_deadline_policy import (
    PUBLIC_GENERATION_DEADLINE_BASE_SECONDS,
    PUBLIC_GENERATION_DEADLINE_MAX_SECONDS,
    PUBLIC_GENERATION_DEADLINE_MIN_SECONDS,
    PUBLIC_GENERATION_DEADLINE_PER_WAVE_SECONDS,
    public_generation_budgeted_wave_count,
    public_generation_deadline_max_seconds,
    public_generation_deadline_seconds,
    public_generation_preamble_seconds,
    public_generation_remake_cap,
    public_generation_remediation_seconds,
    public_generation_review_seconds,
    public_generation_wave_count,
    public_generation_wave_seconds,
)
from services.public_operation_admission import (
    PUBLIC_DEFAULT_POLICY_VERSION,
    PUBLIC_PHASE2_DEFAULT_POLICY_VERSION,
    PrincipalContext,
    WeightedResourcePlan,
    activate_public_operation_v2,
    admit_public_operation_compatible_v2,
    plan_provider_requests,
    requeue_public_operation_v2,
)


TABLES = [
    User.__table__,
    PresentationModel.__table__,
    WorkspaceConversation.__table__,
    WorkspaceOperation.__table__,
    PublicPolicyProfile.__table__,
    PublicQuotaCounter.__table__,
    PublicOperationAdmission.__table__,
    PublicProviderLogicalCall.__table__,
    PublicResourceLease.__table__,
]

LEGACY_FIXED_DEADLINE_SECONDS = 900

# The ceiling column the v3 policy row was migrated with.  It is NOT
# ``PUBLIC_GENERATION_DEADLINE_MAX_SECONDS`` any more: admission takes
# ``min(policy ceiling, formula)``, so raising the code clamp alone does not
# lift the ceiling a deployed database already pins.  See the module note on
# ``test_v3_policy_row_ceiling_still_caps_large_decks``.
V3_POLICY_ROW_CEILING_SECONDS = 7200

# Shipped token profile: one slide per call, one warm-up call, then two calls
# per wave; a one-slide wave is budgeted at 60 + 80 s.
TOKEN_ONE_SLIDE_WAVE_SECONDS = 140
# The explicit three-slide shape the formula was calibrated on keeps its
# five-minute wave.
THREE_SLIDE_WAVE_SECONDS = 300

# The concurrency-independent part of every budget below: the residual base
# (600 s) plus the preamble (brief 900 + outline 420 + header 90).
FIXED_TERMS_SECONDS = 2010

_PROFILE_KNOBS = (
    "AUTHORED_BATCH_SLIDES_PER_CALL",
    "AUTHORED_BATCH_WAVE_CONCURRENCY",
    "AUTHORED_AUTHOR_CONCURRENCY",
    "CONTENT_BRIEF_TIMEOUT_MAX_SECONDS",
    "CONTENT_BRIEF_TOTAL_BUDGET_SECONDS",
    "PUBLIC_GENERATION_DEADLINE_PER_SLIDE_SECONDS",
    "PUBLIC_GENERATION_DEADLINE_CALL_OVERHEAD_SECONDS",
    "PUBLIC_GENERATION_DEADLINE_BASE_SECONDS",
    "PUBLIC_GENERATION_DEADLINE_OUTLINE_SECONDS",
    "PUBLIC_GENERATION_DEADLINE_HEADER_SECONDS",
    "PUBLIC_GENERATION_DEADLINE_REMEDIATION_WAVE_SECONDS",
    "PUBLIC_GENERATION_DEADLINE_REVIEW_PER_SLIDE_SECONDS",
    "PUBLIC_GENERATION_DEADLINE_REVIEW_PER_REMAKE_SECONDS",
    "PUBLIC_GENERATION_DEADLINE_BUDGETED_WAVE_CONCURRENCY",
    "PUBLIC_GENERATION_DEADLINE_MAX_SECONDS",
)


@pytest.fixture(autouse=True)
def _shipped_token_rate_profile(monkeypatch):
    """Every budget below starts from the shipped token profile defaults.

    The deploying environment may widen waves or batch calls; the contract is
    checked against the code defaults, and tests that need another shape ask
    for it explicitly.
    """

    monkeypatch.setenv("AUTHORED_PROVIDER_RATE_PROFILE", "token")
    for knob in _PROFILE_KNOBS:
        monkeypatch.delenv(knob, raising=False)


@pytest.fixture
def _three_slide_calls(monkeypatch):
    monkeypatch.setenv("AUTHORED_BATCH_SLIDES_PER_CALL", "3")


def test_public_default_policy_version_is_v3() -> None:
    assert PUBLIC_DEFAULT_POLICY_VERSION == "public-default/v3"
    assert PUBLIC_PHASE2_DEFAULT_POLICY_VERSION == "public-default/v3"


def test_wave_budget_scales_with_the_slides_one_call_returns(monkeypatch) -> None:
    """A wave costs its slowest call, so the allowance follows ``slides_per_call``."""

    assert public_generation_wave_seconds() == TOKEN_ONE_SLIDE_WAVE_SECONDS
    monkeypatch.setenv("AUTHORED_BATCH_SLIDES_PER_CALL", "2")
    assert public_generation_wave_seconds() == 220
    monkeypatch.setenv("AUTHORED_BATCH_SLIDES_PER_CALL", "3")
    assert public_generation_wave_seconds() == THREE_SLIDE_WAVE_SECONDS
    assert PUBLIC_GENERATION_DEADLINE_PER_WAVE_SECONDS == THREE_SLIDE_WAVE_SECONDS


def test_preamble_term_is_derived_from_the_content_brief_stage_budget(
    monkeypatch,
) -> None:
    """The brief part is read from the brief policy, never repeated as a constant.

    The two policies drifted apart before this: the brief's own wall ceiling
    (900 s) equalled the whole deck's base budget, so a slow brief alone could
    exhaust a small deck.  Raising the brief's stage budget now raises the
    deck's preamble term with it.
    """

    # 900 (content brief total budget) + 420 (outline) + 90 (header contract).
    assert public_generation_preamble_seconds() == 1410
    assert FIXED_TERMS_SECONDS == (
        PUBLIC_GENERATION_DEADLINE_BASE_SECONDS + public_generation_preamble_seconds()
    )

    monkeypatch.setenv("CONTENT_BRIEF_TOTAL_BUDGET_SECONDS", "1200")
    assert public_generation_preamble_seconds() == 1710
    assert public_generation_deadline_seconds(24) == 6322


def test_remediation_and_review_terms_follow_the_remake_cap() -> None:
    """Both terms are bounded by what the repair rounds are allowed to spend.

    ``MAX_TERMINAL_REMAKES_PER_DECK`` (8) and ``MAX_REMAKES_PER_SLIDE`` (2) are
    read from ``services/authored_terminal_slide_fallback``, so a deck of one
    slide can only pay for two remakes.
    """

    assert public_generation_remake_cap(1) == 2
    assert public_generation_remake_cap(6) == 8
    assert public_generation_remake_cap(100) == 8

    # ceil(cap / 2 budgeted calls per wave) waves at 280 s a remediation wave.
    assert public_generation_remediation_seconds(1) == 280
    assert public_generation_remediation_seconds(24) == 1120

    # 8 s per slide reviewed, plus 110 s to re-render each remade slide.
    assert public_generation_review_seconds(24) == 1072
    assert public_generation_review_seconds(90) == 1600


@pytest.mark.parametrize(
    ("slide_count", "expected_waves", "expected_seconds"),
    [
        (1, 1, 2658),
        (6, 4, 4618),
        (20, 11, 5710),
        (24, 13, 6022),
        (35, 18, 6810),
        (90, 46, 11170),
        (100, 51, 11950),
    ],
)
def test_token_profile_budget_pins_the_measured_terms(
    slide_count: int, expected_waves: int, expected_seconds: int
) -> None:
    """Exact budgets for the shipped token profile: one slide per call, two a wave.

    The numbers are pinned literals on purpose.  The previous version of this
    test re-derived the production formula and therefore agreed with it no
    matter what the formula said -- including while it was budgeting the
    authoring waves alone and killing decks.
    """

    assert public_generation_wave_count(slide_count) == expected_waves
    assert public_generation_budgeted_wave_count(slide_count) == expected_waves
    assert public_generation_deadline_seconds(slide_count) == expected_seconds


@pytest.mark.parametrize(
    ("slide_count", "expected_seconds"),
    [
        (1, 2658),
        (6, 4478),
        (20, 5570),
        (24, 5882),
        (35, 6810),
        (90, 11030),
        (100, 11810),
    ],
)
def test_api_profile_budget_pins_the_measured_terms(
    monkeypatch, slide_count: int, expected_seconds: int
) -> None:
    """The api profile only differs in its warm-up wave: it starts two wide, not one.

    Its configured wave of five is not credited with a proportional speed-up --
    the authoring wall was measured at wave concurrency 2 and it is the
    provider rate limit, not the scheduler, that bounds throughput.
    """

    monkeypatch.setenv("AUTHORED_PROVIDER_RATE_PROFILE", "api")
    assert public_generation_deadline_seconds(slide_count) == expected_seconds


def test_api_profile_scheduler_shape_is_reported_unclamped(monkeypatch) -> None:
    """``public_generation_wave_count`` still describes the real scheduler.

    Only ``public_generation_budgeted_wave_count`` applies the measured-
    concurrency clamp, so the worker-lane assertions that compare recorded
    waves against this function keep their meaning.
    """

    monkeypatch.setenv("AUTHORED_PROVIDER_RATE_PROFILE", "api")

    assert public_generation_wave_seconds() == TOKEN_ONE_SLIDE_WAVE_SECONDS
    assert public_generation_wave_count(5) == 1
    assert public_generation_wave_count(20) == 4
    assert public_generation_budgeted_wave_count(20) == 10


@pytest.mark.parametrize("profile_name", ["token", "api"])
@pytest.mark.parametrize("slide_count", [1, 6, 20, 24, 35, 90, 100])
def test_widening_the_wave_never_reduces_the_budget(
    monkeypatch, profile_name: str, slide_count: int
) -> None:
    """Widening ``AUTHORED_BATCH_WAVE_CONCURRENCY`` must never shrink a budget.

    This replaces ``test_widening_the_wave_reduces_the_budget_for_the_same_deck``,
    which pinned the opposite contract and blessed the production defect: the
    deploying environment runs the knob at 5, and under the old formula that
    alone cut a 20-slide budget from 2440 s to 1600 s.  The preamble,
    remediation and review terms do not shrink with concurrency, and the
    authoring term is capped at the concurrency its measurement was taken at,
    so a wider wave leaves the budget exactly where it was.
    """

    monkeypatch.setenv("AUTHORED_PROVIDER_RATE_PROFILE", profile_name)
    narrow = public_generation_deadline_seconds(slide_count)

    monkeypatch.setenv("AUTHORED_BATCH_WAVE_CONCURRENCY", "5")
    wide = public_generation_deadline_seconds(slide_count)

    assert wide >= narrow
    assert wide == narrow


def test_narrowing_the_wave_widens_the_budget(monkeypatch) -> None:
    """Fewer calls in flight really do take longer, and the budget follows."""

    assert public_generation_deadline_seconds(20) == 5710
    monkeypatch.setenv("AUTHORED_BATCH_WAVE_CONCURRENCY", "1")
    assert public_generation_deadline_seconds(20) == 8090


def test_healthy_measured_wall_fits_with_real_margin() -> None:
    """The healthy 24-slide bench run finished in 2577 s; the old budget was 2720 s.

    A 143 s margin on a 43-minute run is not a margin.  The rebuilt budget has
    to leave room for the stages that run only sometimes -- up to eight remakes
    and the review re-renders they cause -- which is exactly what the healthy
    run did not pay for.
    """

    healthy_measured_wall_seconds = 2577
    budget = public_generation_deadline_seconds(24)
    assert budget == 6022
    assert budget - healthy_measured_wall_seconds == 3445
    assert budget > healthy_measured_wall_seconds * 2


def test_resume_that_repeats_the_preamble_fits_at_its_measured_walls() -> None:
    """The 20-slide resume that died: preamble paid twice, then a full deck.

    Bench run: a provider 500 during the outline forced a resume, the resume
    re-ran the whole preamble, and authoring only began 1590 s into a 2440 s
    budget.  Measured against real stage walls the same deck now fits with room
    to spare.

    OUT OF SCOPE, deliberately: the *worst-case* repeated preamble, i.e. two
    preambles each spending their full 1410 s allowance.  That is 2820 s of a
    5710 s budget and does not fit alongside the worst-case authoring,
    remediation and review terms.  Budgeting for it would make every healthy
    deck carry a spare preamble it never uses, and the deadline is pinned at
    admission so a resume is never re-granted one.  The right fix is to persist
    the content brief so a resume does not repeat it at all; the outline is
    already pinned on ``PresentationGenerationJob.authored_outline``.
    """

    preamble_paid_twice_seconds = 1590  # measured, including the failed attempt
    authoring_seconds = 11 * 89  # 11 waves at the p90 authoring call
    remediation_seconds = 555  # measured worst four-remake round
    review_seconds = 20 * 8 + 4 * 110  # per slide, plus four re-rendered remakes

    measured_wall = (
        preamble_paid_twice_seconds
        + authoring_seconds
        + remediation_seconds
        + review_seconds
    )
    assert measured_wall == 3724
    assert public_generation_deadline_seconds(20) == 5710
    assert measured_wall < public_generation_deadline_seconds(20)


@pytest.mark.usefixtures("_three_slide_calls")
@pytest.mark.parametrize(
    ("slide_count", "expected_seconds"),
    [
        (1, 2818),
        (3, 3834),
        (4, 4642),
        (9, 4682),
        (10, 4990),
        (15, 5030),
        (35, 6390),
        (100, 10210),
    ],
)
def test_generation_deadline_scales_with_explicit_three_slide_batch_waves(
    slide_count: int, expected_seconds: int
) -> None:
    """A larger call lengthens each wave: 300 s a wave instead of 140 s."""

    assert public_generation_deadline_seconds(slide_count) == expected_seconds


@pytest.mark.usefixtures("_three_slide_calls")
def test_generation_deadline_wave_count_and_ceiling_are_bounded() -> None:
    assert public_generation_wave_count(1) == 1
    assert public_generation_wave_count(4) == 2
    assert public_generation_wave_count(35) == 7
    assert public_generation_wave_count(100) == 18
    # A deck far past the 100-slide policy cap still cannot exceed the ceiling.
    assert (
        public_generation_deadline_seconds(10_000)
        == PUBLIC_GENERATION_DEADLINE_MAX_SECONDS
    )
    with pytest.raises(ValueError, match="slide_count_must_be_nonnegative"):
        public_generation_deadline_seconds(-1)


def test_stuck_job_ceiling_is_a_knob_that_no_realistic_deck_reaches(
    monkeypatch,
) -> None:
    """The clamp bounds a stuck job; the formula decides for every admitted deck.

    At its former 7200 s the clamp silently killed decks from 40 slides up, and
    production really does generate 90-slide decks.
    """

    assert public_generation_deadline_max_seconds() == PUBLIC_GENERATION_DEADLINE_MAX_SECONDS
    assert PUBLIC_GENERATION_DEADLINE_MAX_SECONDS == 14400
    assert public_generation_deadline_seconds(100) < PUBLIC_GENERATION_DEADLINE_MAX_SECONDS

    monkeypatch.setenv("PUBLIC_GENERATION_DEADLINE_MAX_SECONDS", "5000")
    assert public_generation_deadline_max_seconds() == 5000
    assert public_generation_deadline_seconds(100) == 5000
    assert public_generation_deadline_seconds(1) == 2658

    # The ceiling is applied last, so it also wins over the floor; admission
    # rejects a budget this small rather than starting the operation.
    assert PUBLIC_GENERATION_DEADLINE_MIN_SECONDS == 1200
    monkeypatch.setenv("PUBLIC_GENERATION_DEADLINE_MAX_SECONDS", "60")
    assert public_generation_deadline_seconds(24) == 60


def test_v3_policy_row_ceiling_still_caps_large_decks() -> None:
    """Admission takes ``min(policy ceiling, formula)`` -- and the row is 7200 s.

    The formula now decides for every deck the code clamp admits, but the
    ``public-default/v3`` row that production migrated in still pins 7200 s, so
    the ceiling and not the formula decides from 40 slides up.  Closing that
    needs a ``public-default/v4`` row plus a default-version flip, which is a
    control-plane contract change and is deliberately not made here.
    """

    assert public_generation_deadline_seconds(39) == 7122
    assert min(V3_POLICY_ROW_CEILING_SECONDS, public_generation_deadline_seconds(39)) == 7122

    assert public_generation_deadline_seconds(40) == 7270
    assert min(V3_POLICY_ROW_CEILING_SECONDS, public_generation_deadline_seconds(40)) == (
        V3_POLICY_ROW_CEILING_SECONDS
    )
    assert min(V3_POLICY_ROW_CEILING_SECONDS, public_generation_deadline_seconds(90)) == (
        V3_POLICY_ROW_CEILING_SECONDS
    )


def test_every_term_is_overridable_at_runtime(monkeypatch) -> None:
    monkeypatch.setenv("PUBLIC_GENERATION_DEADLINE_PER_SLIDE_SECONDS", "100")
    assert public_generation_wave_seconds() == 160
    assert public_generation_deadline_seconds(20) == 5930

    monkeypatch.setenv("PUBLIC_GENERATION_DEADLINE_CALL_OVERHEAD_SECONDS", "40")
    assert public_generation_wave_seconds() == 140
    assert public_generation_deadline_seconds(20) == 5710

    monkeypatch.setenv("PUBLIC_GENERATION_DEADLINE_BASE_SECONDS", "900")
    monkeypatch.setenv("PUBLIC_GENERATION_DEADLINE_OUTLINE_SECONDS", "500")
    monkeypatch.setenv("PUBLIC_GENERATION_DEADLINE_HEADER_SECONDS", "100")
    assert public_generation_preamble_seconds() == 1500
    monkeypatch.setenv("PUBLIC_GENERATION_DEADLINE_REMEDIATION_WAVE_SECONDS", "300")
    monkeypatch.setenv("PUBLIC_GENERATION_DEADLINE_REVIEW_PER_SLIDE_SECONDS", "10")
    monkeypatch.setenv("PUBLIC_GENERATION_DEADLINE_REVIEW_PER_REMAKE_SECONDS", "120")
    assert public_generation_remediation_seconds(20) == 1200
    assert public_generation_review_seconds(20) == 1160
    assert public_generation_deadline_seconds(20) == 900 + 1500 + 1540 + 1200 + 1160

    # A non-positive override falls back to the code default.  (A non-integer
    # value is rejected earlier, when the runtime settings snapshot coerces
    # registry integers, so it never reaches this policy.)
    monkeypatch.setenv("PUBLIC_GENERATION_DEADLINE_PER_SLIDE_SECONDS", "0")
    monkeypatch.setenv("PUBLIC_GENERATION_DEADLINE_CALL_OVERHEAD_SECONDS", "-5")
    assert public_generation_wave_seconds() == TOKEN_ONE_SLIDE_WAVE_SECONDS


def _policy(version: str, *, generation_deadline_seconds: int) -> PublicPolicyProfile:
    return PublicPolicyProfile(
        version=version,
        max_slides=100,
        global_active_generation=4,
        global_queued_generation=8,
        owner_active_generation=2,
        owner_queued_generation=4,
        tenant_active_generation=4,
        tenant_queued_generation=8,
        global_active_export=4,
        global_queued_export=8,
        owner_active_export=2,
        owner_queued_export=4,
        tenant_active_export=4,
        tenant_queued_export=8,
        global_active_resource_units=10_000_000,
        global_queued_resource_units=10_000_000,
        tenant_active_resource_units=10_000_000,
        tenant_queued_resource_units=10_000_000,
        owner_active_resource_units=10_000_000,
        owner_queued_resource_units=10_000_000,
        retry_request_limit=4,
        estimated_cost_limit_microunits=50_000_000,
        actual_cost_limit_microunits=50_000_000,
        generation_deadline_seconds=generation_deadline_seconds,
        export_deadline_seconds=600,
    )


async def _database(path: Path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{path}")

    @event.listens_for(engine.sync_engine, "connect")
    def _sqlite_safety(connection, _record):
        connection.execute("PRAGMA foreign_keys=ON")

    async with engine.begin() as connection:
        await connection.run_sync(
            lambda sync_connection: SQLModel.metadata.create_all(
                sync_connection, tables=TABLES
            )
        )
    return engine, async_sessionmaker(engine, expire_on_commit=False)


async def _seed_operation(maker, owner_id: uuid.UUID, *, key: str) -> uuid.UUID:
    async with maker() as session, session.begin():
        presentation = PresentationModel(
            owner_id=owner_id,
            content="scaled deadline deck",
            n_slides=35,
            language="ko",
            mode="authored",
            theme={},
        )
        session.add(presentation)
        await session.flush()
        conversation = WorkspaceConversation(
            owner_id=owner_id,
            owner_scope=str(owner_id),
            presentation_id=presentation.id,
        )
        session.add(conversation)
        await session.flush()
        operation = WorkspaceOperation(
            owner_id=owner_id,
            owner_scope=str(owner_id),
            conversation_id=conversation.id,
            presentation_id=presentation.id,
            anchor_position=1,
            kind="presentation.generate",
            idempotency_key_hash=key * 64,
            request_hash=key * 64,
            request_payload={},
        )
        session.add(operation)
        await session.flush()
        return operation.id


_RESOURCE_PLAN = WeightedResourcePlan(
    pixel_count=1,
    device_pixel_ratio_milli=1000,
    dom_node_count=1,
    font_count=1,
    svg_complexity=0,
    estimated_memory_mib=1,
    fidelity_mode="fidelity",
)


def _naive_utc(value):
    """SQLite hands datetimes back without a tzinfo; compare instants only."""

    return value.replace(tzinfo=None)


def _plan(slide_count: int):
    return plan_provider_requests(
        slide_count=slide_count,
        image_budget=slide_count,
        repair_budget=slide_count,
        vqa_budget=0,
    )


def test_v3_admission_pins_scaled_deadline_and_retry_never_regrants_it(
    monkeypatch, tmp_path: Path
) -> None:
    """A 35-slide job gets its budget once; requeue and replay keep that instant.

    Under the shipped token profile 35 one-slide calls are 18 budgeted waves:
    600 base + 1410 preamble + 140 * 18 + 1120 remediation + 1160 review =
    6810 s, below the ceiling this fixture grants, so the formula decides.
    """

    # SQLite is used only as a deterministic contract fixture; PostgreSQL
    # authority is covered by the control-plane race gate.
    monkeypatch.setattr(
        "services.public_operation_admission._require_phase2_authority",
        lambda _session: PublicResourceLease,
    )

    async def scenario() -> None:
        engine, maker = await _database(tmp_path / "public-scaled-deadline.db")
        owner_id = uuid.uuid4()
        principal = PrincipalContext(owner_id, "tenant-deadline", "trace-deadline")
        try:
            async with maker() as session, session.begin():
                session.add(
                    User(id=owner_id, username="deadline-owner", hashed_password="test")
                )
                session.add(
                    _policy(
                        PUBLIC_PHASE2_DEFAULT_POLICY_VERSION,
                        generation_deadline_seconds=(
                            PUBLIC_GENERATION_DEADLINE_MAX_SECONDS
                        ),
                    )
                )
                session.add(
                    _policy(
                        "public-default/v2",
                        generation_deadline_seconds=LEGACY_FIXED_DEADLINE_SECONDS,
                    )
                )

            operation_id = await _seed_operation(maker, owner_id, key="a")

            async with maker() as session, session.begin():
                admitted = await admit_public_operation_compatible_v2(
                    session,
                    principal=principal,
                    operation_id=operation_id,
                    operation_kind="generation",
                    request_plan=_plan(35),
                    resource_plan=_RESOURCE_PLAN,
                )
                # ``queued_at`` and ``deadline_at`` are stamped from the same
                # admission instant, so the delta is the exact granted budget.
                granted = (
                    _naive_utc(admitted.deadline_at) - _naive_utc(admitted.queued_at)
                ).total_seconds()
                assert granted == 6810
                assert granted == public_generation_deadline_seconds(35)
                assert granted < PUBLIC_GENERATION_DEADLINE_MAX_SECONDS
                assert admitted.policy_version == "public-default/v3"
                operation = await session.get(WorkspaceOperation, operation_id)
                assert _naive_utc(operation.deadline_at) == _naive_utc(admitted.deadline_at)
                pinned_deadline = admitted.deadline_at
                admission_id = admitted.id

            # Resume: a second admission for the same operation replays the
            # pinned row instead of computing a new budget.
            async with maker() as session, session.begin():
                replayed = await admit_public_operation_compatible_v2(
                    session,
                    principal=principal,
                    operation_id=operation_id,
                    operation_kind="generation",
                    request_plan=_plan(35),
                    resource_plan=_RESOURCE_PLAN,
                )
                assert replayed.id == admission_id
                assert _naive_utc(replayed.deadline_at) == _naive_utc(pinned_deadline)

            # Retry: activate, requeue, re-activate.  The fence advances, the
            # deadline does not move.
            async with maker() as session, session.begin():
                active = await activate_public_operation_v2(
                    session,
                    admission_id=admission_id,
                    lease_owner_hash="c" * 64,
                    lease_seconds=60,
                )
                assert _naive_utc(active.deadline_at) == _naive_utc(pinned_deadline)
                active_fence = active.fence
                requeued = await requeue_public_operation_v2(
                    session, admission_id=admission_id, expected_fence=active_fence
                )
                assert requeued.fence == active_fence + 1
                assert _naive_utc(requeued.deadline_at) == _naive_utc(pinned_deadline)
                reactivated = await activate_public_operation_v2(
                    session,
                    admission_id=admission_id,
                    lease_owner_hash="c" * 64,
                    lease_seconds=60,
                )
                assert _naive_utc(reactivated.deadline_at) == _naive_utc(pinned_deadline)
                operation = await session.get(WorkspaceOperation, operation_id)
                assert _naive_utc(operation.deadline_at) == _naive_utc(pinned_deadline)

            # An operation pinned to the legacy v2 policy keeps its fixed
            # 15-minute budget even for the same 35-slide request.
            legacy_operation_id = await _seed_operation(maker, owner_id, key="b")
            async with maker() as session, session.begin():
                legacy = await admit_public_operation_compatible_v2(
                    session,
                    principal=principal,
                    operation_id=legacy_operation_id,
                    operation_kind="generation",
                    request_plan=_plan(35),
                    resource_plan=_RESOURCE_PLAN,
                    policy_version="public-default/v2",
                )
                assert (
                    _naive_utc(legacy.deadline_at) - _naive_utc(legacy.queued_at)
                ).total_seconds() == LEGACY_FIXED_DEADLINE_SECONDS
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_v3_policy_migration_adds_row_without_touching_v1_and_v2(tmp_path: Path) -> None:
    """The new row raises only the generation ceiling; older rows stay pinned."""

    database_url = f"sqlite:///{(tmp_path / 'public-policy-v3.db').as_posix()}"
    config = Config()
    config.set_main_option(
        "script_location", str(Path(__file__).resolve().parents[2] / "alembic")
    )
    config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(config, "head")

    engine = create_engine(database_url)
    try:
        with engine.connect() as connection:
            rows = {
                row["version"]: row
                for row in connection.execute(
                    text(
                        """
                        SELECT version, max_slides, generation_deadline_seconds,
                               export_deadline_seconds, retry_request_limit
                        FROM public_policy_profiles
                        ORDER BY version
                        """
                    )
                )
                .mappings()
                .all()
            }
        assert set(rows) == {
            "public-default/v1",
            "public-default/v2",
            "public-default/v3",
        }
        for legacy in ("public-default/v1", "public-default/v2"):
            assert rows[legacy]["generation_deadline_seconds"] == (
                LEGACY_FIXED_DEADLINE_SECONDS
            )
        v3 = rows["public-default/v3"]
        # KNOWN GAP.  The v3 row was migrated with the code clamp of the day
        # (7200 s) and admission takes ``min(policy ceiling, formula)``, so
        # raising ``PUBLIC_GENERATION_DEADLINE_MAX_SECONDS`` in code does not
        # lift the ceiling a deployed database already pins.  Decks from about
        # 40 slides up are still capped at 7200 s until a ``public-default/v4``
        # row carrying the new ceiling is migrated in and made the default
        # policy version.
        assert v3["generation_deadline_seconds"] == V3_POLICY_ROW_CEILING_SECONDS
        assert V3_POLICY_ROW_CEILING_SECONDS < PUBLIC_GENERATION_DEADLINE_MAX_SECONDS
        assert v3["export_deadline_seconds"] == 600
        assert v3["max_slides"] == rows["public-default/v2"]["max_slides"] == 100
        assert (
            v3["retry_request_limit"]
            == rows["public-default/v2"]["retry_request_limit"]
        )
    finally:
        engine.dispose()
