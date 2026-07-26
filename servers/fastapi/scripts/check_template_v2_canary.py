"""Print a content-free, fail-closed Template V2 canary preflight decision."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import text

from services.template_v2_pptx_operations import (
    get_template_v2_operational_status,
    template_v2_database_safety,
)
from services.template_v2_pptx_storage import (
    get_malware_scan_health,
    get_private_storage_health,
)
from templates.v2.policy import get_structured_template_policy


def _expected_alembic_heads() -> set[str]:
    fastapi_root = Path(__file__).resolve().parents[1]
    config = Config(str(fastapi_root / "alembic.ini"))
    config.set_main_option("script_location", str(fastapi_root / "alembic"))
    return set(ScriptDirectory.from_config(config).get_heads())


async def _run_live_preflight() -> tuple[dict[str, object], bool]:
    """Check the live database, schema, storage, and aggregate operations state."""

    from services.database import async_session_maker, dispose_engines

    try:
        try:
            async with async_session_maker() as session:
                await session.execute(text("SELECT 1"))
                database_revisions = set(
                    (
                        await session.execute(
                            text("SELECT version_num FROM alembic_version")
                        )
                    )
                    .scalars()
                    .all()
                )
        except Exception:  # noqa: BLE001 - fail closed at the operational boundary
            return (
                {
                    "database_reachable": False,
                    "database_check_code": (
                        "template_v2_database_connection_or_schema_check_failed"
                    ),
                    "schema_at_head": False,
                    "schema_code": "template_v2_schema_check_not_completed",
                    "preflight_code": (
                        "template_v2_database_connection_or_schema_check_failed"
                    ),
                },
                False,
            )

        expected_heads = _expected_alembic_heads()
        schema_at_head = database_revisions == expected_heads
        payload: dict[str, object] = {
            "database_reachable": True,
            "database_check_code": "template_v2_database_reachable",
            "schema_at_head": schema_at_head,
            "schema_code": (
                "template_v2_schema_at_head"
                if schema_at_head
                else "template_v2_schema_head_mismatch"
            ),
            "preflight_code": (
                "template_v2_preflight_ready"
                if schema_at_head
                else "template_v2_schema_head_mismatch"
            ),
        }
        if not schema_at_head:
            return payload, False

        storage = get_private_storage_health()
        payload.update(storage.as_dict())
        if not storage.ready:
            payload["preflight_code"] = storage.code
            return payload, False

        malware_scan = get_malware_scan_health()
        payload.update(malware_scan.as_dict())
        if not malware_scan.ready:
            payload["preflight_code"] = malware_scan.code
            return payload, False

        try:
            operational_status = await get_template_v2_operational_status()
        except Exception:  # noqa: BLE001 - expose only a stable health code
            payload["operational_health_code"] = "template_v2_operations_check_failed"
            payload["preflight_code"] = "template_v2_operations_check_failed"
            return payload, False
        payload.update(operational_status.as_dict())
        payload["preflight_code"] = (
            "template_v2_preflight_ready"
            if operational_status.healthy
            else operational_status.health_code
        )
        return payload, operational_status.healthy
    finally:
        await dispose_engines()


def main() -> int:
    readiness = get_structured_template_policy().canary_readiness()
    database_safety = template_v2_database_safety()
    payload: dict[str, object] = {
        **readiness.as_dict(),
        "database_safe": database_safety.safe,
        "database_code": database_safety.code,
        "deployment_tier": database_safety.deployment_tier,
        "database_backend": database_safety.database_backend,
    }
    if not readiness.ready:
        print(json.dumps(payload, sort_keys=True))
        return 2
    if not database_safety.safe:
        payload["ready"] = False
        payload["code"] = database_safety.code
        print(json.dumps(payload, sort_keys=True))
        return 2

    try:
        live_payload, live_ready = asyncio.run(_run_live_preflight())
    except Exception:  # noqa: BLE001 - keep preflight output content-free
        live_payload = {
            "database_reachable": False,
            "database_check_code": "template_v2_preflight_failed",
        }
        live_ready = False
    payload.update(live_payload)
    if not live_ready:
        payload["ready"] = False
        payload["code"] = str(
            live_payload.get("preflight_code")
            or live_payload.get("database_check_code")
            or "template_v2_preflight_failed"
        )
    print(json.dumps(payload, sort_keys=True))
    return 0 if payload["ready"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
