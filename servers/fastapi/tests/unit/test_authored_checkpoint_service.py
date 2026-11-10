from contextlib import contextmanager
from datetime import timedelta
from io import BytesIO
import uuid

from PIL import Image
from sqlalchemy import update
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlmodel import SQLModel

from models.sql.async_presentation_generation_status import (
    AsyncPresentationGenerationTaskModel,
)
from models.sql.authored_slide_checkpoint import AuthoredSlideCheckpoint
from models.sql.presentation_generation_job import PresentationGenerationJob
from models.sql.presentation import PresentationModel
from models.sql.public_admission import PublicOperationAdmission, PublicPolicyProfile
from models.sql.user import User
from models.sql.workspace import WorkspaceConversation, WorkspaceOperation
from models.presentation_outline_model import (
    PresentationOutlineModel,
    SlideOutlineModel,
)
from utils.datetime_utils import get_current_utc_datetime


def _png(color):
    stream = BytesIO()
    Image.new("RGB", (1280, 720), color).save(stream, format="PNG")
    return stream.getvalue()


_STALE_PNG = _png("red")
_RENDERED_PNG = _png("blue")


# The complete foreign-key closure of the checkpoint tables. Rows that name a
# parent (operation -> conversation -> presentation/user, admission -> policy)
# are seeded with real parents, so the same fixtures hold on a database that
# enforces every constraint.
_CHECKPOINT_TABLES = [
    AsyncPresentationGenerationTaskModel.__table__,
    PresentationGenerationJob.__table__,
    AuthoredSlideCheckpoint.__table__,
    WorkspaceOperation.__table__,
    # A render checkpoint asks the admission authority whether the
    # generation is public before it writes its artifacts, so the
    # scratch schema needs the table even for a local job.
    PublicOperationAdmission.__table__,
    PublicPolicyProfile.__table__,
    WorkspaceConversation.__table__,
    PresentationModel.__table__,
    User.__table__,
]


async def _checkpoint_database(tmp_path, monkeypatch):
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path / 'authored-checkpoints.db'}"
    )
    async with engine.begin() as connection:
        await connection.run_sync(
            SQLModel.metadata.create_all,
            tables=_CHECKPOINT_TABLES,
        )
    maker = async_sessionmaker(engine, expire_on_commit=False)
    # svg calls the shared row writes directly; the fence primitives open
    # their own sessions on the paths that do not receive one.
    from services import authored_checkpoint_fence, generation_checkpoint_rows

    monkeypatch.setattr(generation_checkpoint_rows, "async_session_maker", maker)
    monkeypatch.setattr(authored_checkpoint_fence, "async_session_maker", maker)
    monkeypatch.setenv(
        "AUTHORED_CHECKPOINT_DIRECTORY", str(tmp_path / "render-artifacts")
    )
    return engine, maker


async def _running_authored_job(maker, *, total_slides=0):
    status = AsyncPresentationGenerationTaskModel(
        status="running",
        message="Generating authored presentation",
    )
    job = PresentationGenerationJob(
        task_id=status.id,
        presentation_id=uuid.uuid4(),
        request_sha256="a" * 64,
        request_payload={"template": "authored"},
        state="running",
        attempt_number=3,
        attempt_token="attempt-a",
        total_slides=total_slides,
    )
    async with maker() as session:
        # The ORM flushes unrelated mappers alphabetically, not by foreign
        # key, so the parent row is written first on purpose.
        session.add(status)
        await session.flush()
        session.add(job)
        await session.commit()
    return status, job


async def _linked_operation(
    maker, job, *, owner_id=None, admitted=False, fence=9, attempt="workspace-attempt"
):
    """Link ``job`` to a running workspace operation through real parent rows.

    ``admitted`` adds the public v2 contract and its policy/admission rows, so
    the fence also has to agree with the admission authority. Parents are
    flushed one level at a time: the ORM does not order unrelated mappers by
    foreign key. Returns ``(operation_id, admission_id)``.
    """
    deadline = get_current_utc_datetime() + timedelta(hours=1)
    scope = str(owner_id) if owner_id is not None else "anonymous"
    contract = (
        {
            "contract_version": "public-operation/v2",
            "execution_profile_version": "public-generation-export/v2",
            "phase": "active",
            "deadline_at": deadline,
        }
        if admitted
        else {}
    )
    assert owner_id is not None or not admitted, "an admission is bound to a real owner"
    admission_id = None
    async with maker() as session:
        if owner_id is not None:
            session.add(
                User(id=owner_id, username=f"owner-{owner_id}", hashed_password="unused")
            )
        session.add(
            PresentationModel(
                id=job.presentation_id,
                content="checkpoint fence",
                n_slides=1,
                language="en",
                layout={"name": "test"},
                mode="svg",
            )
        )
        await session.flush()
        conversation = WorkspaceConversation(
            presentation_id=job.presentation_id, owner_id=owner_id, owner_scope=scope
        )
        session.add(conversation)
        await session.flush()
        operation = WorkspaceOperation(
            conversation_id=conversation.id,
            presentation_id=job.presentation_id,
            owner_id=owner_id,
            owner_scope=scope,
            anchor_position=1,
            kind="presentation.generate",
            state="running",
            idempotency_key_hash="d" * 64,
            request_hash="e" * 64,
            request_payload={},
            attempt_token=attempt,
            fencing_token=fence,
            **contract,
        )
        session.add(operation)
        await session.flush()
        if admitted:
            policy_values = {
                column.name: 100
                for column in PublicPolicyProfile.__table__.columns
                if not column.nullable
                and column.default is None
                and column.server_default is None
            }
            policy_values["version"] = "checkpoint-fence-policy"
            session.add(PublicPolicyProfile(**policy_values))
            await session.flush()
            admission = PublicOperationAdmission(
                operation_id=operation.id,
                policy_version="checkpoint-fence-policy",
                owner_id=owner_id,
                tenant_id="tenant-a",
                trace_id_hash="b" * 64,
                operation_kind="generation",
                resource_units=1,
                policy_digest="c" * 64,
                deadline_at=deadline,
                state="active",
                fence=fence,
                slide_count=1,
                planned_request_limit=4,
                retry_request_limit=4,
                estimated_cost_limit_microunits=100,
                actual_cost_limit_microunits=100,
            )
            session.add(admission)
            await session.flush()
            admission_id = admission.id
        stored = await session.get(PresentationGenerationJob, job.id)
        stored.workspace_operation_id = operation.id
        stored.attempt_token = attempt
        session.add(stored)
        await session.commit()
        operation_id = operation.id
    job.workspace_operation_id = operation_id
    job.attempt_token = attempt
    return operation_id, admission_id


async def _set_columns(maker, model, row_id, **values):
    async with maker() as session:
        await session.execute(update(model).where(model.id == row_id).values(**values))
        await session.commit()


@contextmanager
def _principal(owner_id, tenant_id="tenant-a"):
    """Run the block as one authenticated owner/tenant, as the request layer does."""
    from utils.owner_context import (
        reset_current_owner_id,
        reset_current_tenant_id,
        set_current_owner_id,
        set_current_tenant_id,
    )

    owner_token = set_current_owner_id(owner_id)
    tenant_token = set_current_tenant_id(tenant_id)
    try:
        yield
    finally:
        reset_current_tenant_id(tenant_token)
        reset_current_owner_id(owner_token)


def _one_slide_outline():
    return PresentationOutlineModel(
        slides=[SlideOutlineModel(content="checkpoint resume")]
    )


def test_budget_exhaustion_keeps_the_first_attempt_s_reason() -> None:
    """A retry must not replace *why* a slide failed with *that it ran out*.

    The second attempt only knows the slide already spent its provider calls.
    Recording that alone overwrites the one field that says where to look, so
    the first attempt's bounded diagnostic is carried forward beside it.
    """

    from services.authored_checkpoint_errors import AuthoredStageError

    first_attempt = {
        "code": "authored_slide_author_failed",
        "exception_type": "ValueError",
        "cause": "authored_batch_slide_missing",
        "fingerprint": "4f4d2e88899a67f8",
        "slide_index": 34,
        "html": "<!doctype html><html>...</html>",
    }
    exhausted = AuthoredStageError(
        slide_index=34,
        stage="author",
        code="authored_provider_attempt_budget_exhausted",
        retryable=False,
        provider_attempt_count=2,
        prior_failure=first_attempt,
    )

    recorded = exhausted.as_safe_dict()

    assert recorded["code"] == "authored_provider_attempt_budget_exhausted"
    assert recorded["prior_failure"] == {
        "code": "authored_slide_author_failed",
        "exception_type": "ValueError",
        "cause": "authored_batch_slide_missing",
        "fingerprint": "4f4d2e88899a67f8",
    }


def test_a_carried_reason_is_bounded_and_never_widens_the_record() -> None:
    from services.authored_checkpoint_errors import AuthoredStageError

    carried = AuthoredStageError(
        slide_index=0,
        stage="author",
        code="authored_provider_attempt_budget_exhausted",
        prior_failure={"cause": "x" * 900, "session_token": "secret", "code": 12},
    ).as_safe_dict()

    assert set(carried["prior_failure"]) == {"cause"}
    assert len(carried["prior_failure"]["cause"]) == 200


def test_no_prior_failure_leaves_the_record_unchanged() -> None:
    from services.authored_checkpoint_errors import AuthoredStageError

    plain = AuthoredStageError(
        slide_index=0, stage="author", code="authored_slide_author_failed"
    ).as_safe_dict()

    assert "prior_failure" not in plain
