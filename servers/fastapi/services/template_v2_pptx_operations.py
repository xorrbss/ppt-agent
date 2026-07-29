"""Content-free operational gates and health for Template V2 PPTX imports."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
import json
import logging
import os
from typing import Mapping

from sqlalchemy import func, or_
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession as SQLModelAsyncSession

from utils.datetime_utils import get_current_utc_datetime
from utils.get_env import get_database_url_env


LOGGER = logging.getLogger(__name__)
TEMPLATE_V2_DEPLOYMENT_TIER = "TEMPLATE_V2_DEPLOYMENT_TIER"
_LOCAL_TIERS = frozenset({"local", "development", "test"})
_MANAGED_TIERS = frozenset({"staging", "production"})
_KNOWN_TIERS = _LOCAL_TIERS | _MANAGED_TIERS
_ROLLBACK_BLOCKING_STATES = (
    "queued",
    "processing",
    "finalizing",
    "confirming",
    "review_required",
    "failed",
)
_ACTIVE_STATES = ("processing", "finalizing")
_SOURCE_CLEANUP_STATES = ("review_required", "failed", "cancelled")


def _get_session_maker():
    # Keep canary readiness lightweight: importing this module must not create an
    # engine or attempt a database connection.
    from services.database import sql_engine

    return async_sessionmaker(
        sql_engine,
        class_=SQLModelAsyncSession,
        expire_on_commit=False,
    )


@dataclass(frozen=True, slots=True)
class TemplateV2DatabaseSafety:
    safe: bool
    code: str
    deployment_tier: str
    database_backend: str

    def as_dict(self) -> dict[str, bool | str]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class TemplateV2OperationalStatus:
    healthy: bool
    health_code: str
    rollback_safe: bool
    rollback_code: str
    rollback_blocking_count: int
    active_count: int
    stale_active_count: int
    failed_count: int
    review_required_count: int
    overdue_cleanup_count: int

    def as_dict(self) -> dict[str, bool | int | str]:
        return asdict(self)


def template_v2_database_safety(
    environ: Mapping[str, str] | None = None,
    *,
    configured_database_url: str | None = None,
) -> TemplateV2DatabaseSafety:
    """Require PostgreSQL for explicitly managed staging/production canaries."""

    values = os.environ if environ is None else environ
    tier = (values.get(TEMPLATE_V2_DEPLOYMENT_TIER) or "local").strip().lower()
    try:
        backend = make_url(
            configured_database_url
            or get_database_url_env()
            or "sqlite:///local.db"
        ).get_backend_name()
    except Exception:
        backend = "invalid"
    if backend == "invalid":
        code = "template_v2_database_url_invalid"
    elif tier not in _KNOWN_TIERS:
        code = "template_v2_deployment_tier_invalid"
    elif tier in _MANAGED_TIERS and backend != "postgresql":
        code = "template_v2_managed_canary_requires_postgresql"
    else:
        code = "template_v2_database_safe"
    return TemplateV2DatabaseSafety(
        safe=code == "template_v2_database_safe",
        code=code,
        deployment_tier=tier,
        database_backend=backend,
    )


def require_template_v2_database_safety(
    *,
    feature_enabled: bool,
    environ: Mapping[str, str] | None = None,
    configured_database_url: str | None = None,
) -> TemplateV2DatabaseSafety:
    """Fail startup for an enabled managed canary backed by SQLite/MySQL."""

    safety = template_v2_database_safety(
        environ,
        configured_database_url=configured_database_url,
    )
    if feature_enabled and not safety.safe:
        raise RuntimeError(safety.code)
    return safety


async def get_template_v2_operational_status(
    *,
    now: datetime | None = None,
) -> TemplateV2OperationalStatus:
    """Return aggregate-only queue, rollback, and retention health."""

    from models.sql.template_v2_pptx_import import TemplateV2PptxImport

    observed_at = now or get_current_utc_datetime()
    async with _get_session_maker()() as session:
        state_counts = dict(
            (
                await session.exec(
                    select(
                        TemplateV2PptxImport.state,
                        func.count(TemplateV2PptxImport.id),
                    ).group_by(TemplateV2PptxImport.state)
                )
            ).all()
        )
        stale_active_count = int(
            (
                await session.exec(
                    select(func.count(TemplateV2PptxImport.id)).where(
                        TemplateV2PptxImport.state.in_(_ACTIVE_STATES),
                        or_(
                            TemplateV2PptxImport.lease_expires_at.is_(None),
                            TemplateV2PptxImport.lease_expires_at <= observed_at,
                        ),
                    )
                )
            ).one()
        )
        overdue_cleanup_count = int(
            (
                await session.exec(
                    select(func.count(TemplateV2PptxImport.id)).where(
                        TemplateV2PptxImport.state.in_(_SOURCE_CLEANUP_STATES),
                        TemplateV2PptxImport.attempt_token.is_(None),
                        TemplateV2PptxImport.source_deleted_at.is_(None),
                        TemplateV2PptxImport.source_retention_expires_at.is_not(
                            None
                        ),
                        TemplateV2PptxImport.source_retention_expires_at
                        <= observed_at,
                    )
                )
            ).one()
        )

    rollback_blocking_count = sum(
        int(state_counts.get(state, 0)) for state in _ROLLBACK_BLOCKING_STATES
    )
    active_count = sum(int(state_counts.get(state, 0)) for state in _ACTIVE_STATES)
    failed_count = int(state_counts.get("failed", 0))
    review_required_count = int(state_counts.get("review_required", 0))
    if stale_active_count:
        health_code = "template_v2_stale_imports_detected"
    elif failed_count:
        health_code = "template_v2_failed_imports_require_attention"
    elif review_required_count:
        health_code = "template_v2_review_required_imports_require_attention"
    elif overdue_cleanup_count:
        health_code = "template_v2_private_source_cleanup_overdue"
    else:
        health_code = "template_v2_operations_healthy"
    rollback_safe = rollback_blocking_count == 0
    return TemplateV2OperationalStatus(
        healthy=health_code == "template_v2_operations_healthy",
        health_code=health_code,
        rollback_safe=rollback_safe,
        rollback_code=(
            "template_v2_rollback_drain_complete"
            if rollback_safe
            else "template_v2_rollback_drain_required"
        ),
        rollback_blocking_count=rollback_blocking_count,
        active_count=active_count,
        stale_active_count=stale_active_count,
        failed_count=failed_count,
        review_required_count=review_required_count,
        overdue_cleanup_count=overdue_cleanup_count,
    )


async def log_template_v2_operational_health(
    *,
    logger: logging.Logger = LOGGER,
) -> TemplateV2OperationalStatus:
    """Emit degraded queue health at WARNING so it survives WARNING log level."""

    status = await get_template_v2_operational_status()
    payload = json.dumps(
        status.as_dict(),
        sort_keys=True,
        separators=(",", ":"),
    )
    log = logger.info if status.healthy else logger.warning
    log("template_v2_pptx_health %s", payload)
    return status
