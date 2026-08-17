"""Emit a read-only authored-only migration census and checksum inventory.

The script reflects the current database so it can inspect malformed legacy
rows without instantiating ORM models.  It never mutates the database or files.
"""

from __future__ import annotations

import argparse
import asyncio
from collections import Counter
from datetime import datetime, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from typing import Any

from sqlalchemy import MetaData, Table, create_engine, inspect, select
from sqlalchemy.ext.asyncio import create_async_engine

FASTAPI_ROOT = Path(__file__).resolve().parents[1]
if str(FASTAPI_ROOT) not in sys.path:
    sys.path.insert(0, str(FASTAPI_ROOT))

from services.compatibility_migration.census import build_census  # noqa: E402
from services.compatibility_migration.inventory import (  # noqa: E402
    build_presentation_inventory,
)

CLASSIFICATIONS = (
    "legacy",
    "adaptive",
    "smart",
    "template_v2",
    "authored",
    "mixed",
    "invalid",
)
_EVIDENCE_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")


def _string(value: object) -> str:
    return str(value) if value is not None else ""


def _rows_for(rows: list[dict[str, Any]], field: str, value: object) -> list[dict[str, Any]]:
    expected = _string(value)
    return [row for row in rows if _string(row.get(field)) == expected]


def _reference_rows(
    presentation: dict[str, Any],
    snapshots: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    identifiers = {_string(value) for value in presentation.get("reference_snapshot_ids") or []}
    return [row for row in snapshots if _string(row.get("id")) in identifiers]


def _asset_rows(
    references: list[dict[str, Any]],
    assets: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    identifiers: set[str] = set()
    for snapshot in references:
        for binding in snapshot.get("asset_bindings") or []:
            if isinstance(binding, dict):
                identifier = binding.get("assetId", binding.get("asset_id"))
                if identifier is not None:
                    identifiers.add(_string(identifier))
    return [row for row in assets if _string(row.get("id")) in identifiers]


def _strings(value: object) -> set[str]:
    if isinstance(value, str):
        return {value}
    if isinstance(value, dict):
        return {item for child in value.values() for item in _strings(child)}
    if isinstance(value, (list, tuple)):
        return {item for child in value for item in _strings(child)}
    return set()


def _legacy_image_rows(
    presentation_slides: list[dict[str, Any]],
    image_assets: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    references = _strings(presentation_slides)
    result: list[dict[str, Any]] = []
    for row in image_assets:
        path = _string(row.get("path"))
        canonical_id = _string(row.get("canonical_asset_id"))
        if path not in references and (not canonical_id or canonical_id not in references):
            continue
        normalized = dict(row)
        normalized["storage_key"] = path
        extras = row.get("extras")
        normalized["sha256"] = extras.get("sha256") if isinstance(extras, dict) else None
        result.append(normalized)
    return result


def _original_rows(
    presentation_id: object,
    imports: list[dict[str, Any]],
    templates: list[dict[str, Any]],
    local_states: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    template_by_id = {_string(row.get("id")): row for row in templates}
    template_ids = {
        template_id
        for template_id, row in template_by_id.items()
        if _string(row.get("presentation_id")) == _string(presentation_id)
    }
    template_ids.update(
        _string(row.get("template_id"))
        for row in local_states
        if _string(row.get("presentation_id")) == _string(presentation_id)
    )
    result: list[dict[str, Any]] = []
    for row in imports:
        template_id = _string(row.get("draft_template_id"))
        if template_id not in template_ids:
            continue
        owned = dict(row)
        owned.setdefault("owner_id", template_by_id.get(template_id, {}).get("owner_id"))
        result.append(owned)
    return result


def build_authored_only_report(
    tables: dict[str, list[dict[str, Any]]],
    *,
    storage_root: Path | None = None,
) -> dict[str, Any]:
    presentations = tables.get("presentations", [])
    slides = tables.get("slides", [])
    census = build_census(presentations, slides)
    census_by_id = {entry.presentation_id: entry for entry in census}
    report_rows: list[dict[str, Any]] = []

    for presentation in presentations:
        presentation_id = presentation.get("id")
        presentation_slides = _rows_for(slides, "presentation", presentation_id)
        references = _reference_rows(
            presentation,
            tables.get("reference_snapshots", []),
        )
        assets = _asset_rows(references, tables.get("workspace_assets", []))
        assets.extend(
            _legacy_image_rows(
                presentation_slides,
                tables.get("imageasset", tables.get("image_assets", [])),
            )
        )
        jobs = _rows_for(
            tables.get("presentation_generation_jobs", []),
            "presentation_id",
            presentation_id,
        )
        job_ids = {_string(row.get("id")) for row in jobs}
        checkpoints = [
            row
            for row in tables.get("authored_slide_checkpoints", [])
            if _string(row.get("job_id")) in job_ids
        ]
        operations = _rows_for(
            tables.get("workspace_operations", []),
            "presentation_id",
            presentation_id,
        )
        workspace_artifacts = _rows_for(
            tables.get("workspace_artifacts", []),
            "presentation_id",
            presentation_id,
        )
        archives = [
            row
            for row in workspace_artifacts
            if "archive" in _string(row.get("kind")).lower()
        ]
        artifacts = [row for row in workspace_artifacts if row not in archives]
        originals = _original_rows(
            presentation_id,
            tables.get("template_v2_pptx_imports", []),
            tables.get("template_v2", []),
            tables.get("template_v2_local_state", []),
        )
        inventory = build_presentation_inventory(
            presentation,
            presentation_slides,
            reference_snapshots=references,
            assets=assets,
            original_pptx=originals,
            generation_jobs=jobs,
            workspace_operations=operations,
            checkpoints=checkpoints,
            artifacts=artifacts,
            archives=archives,
            storage_root=storage_root,
        )
        report_rows.append(
            {
                "census": census_by_id[_string(presentation_id)].as_dict(),
                "inventory": inventory.as_dict(),
            }
        )

    counts = Counter(row["census"]["classification"] for row in report_rows)
    classification_counts = {name: counts[name] for name in CLASSIFICATIONS}
    quarantined = [
        {
            "presentation_id": row["census"]["presentation_id"],
            "classification": row["census"]["classification"],
            "reason": row["census"]["quarantine_reason"],
        }
        for row in report_rows
        if row["census"]["quarantine"]
    ]
    drain = [
        {
            "presentation_id": row["census"]["presentation_id"],
            **job,
        }
        for row in report_rows
        for job in row["inventory"]["active_jobs"]
    ]
    deletion_manifest: list[dict[str, Any]] = []
    for row in report_rows:
        census_row = row["census"]
        inventory_row = row["inventory"]
        blockers: list[str] = []
        if census_row["classification"] != "authored":
            blockers.append("non_authored_compatibility_required")
        if inventory_row["archive_count"] == 0:
            blockers.append("archive_manifest_missing")
        elif not inventory_row["archive_checksum_complete"]:
            blockers.append("archive_digest_unverified")
        if inventory_row["active_jobs"]:
            blockers.append("active_job_drain_incomplete")
        if inventory_row["checkpoint_count"]:
            blockers.append("checkpoint_retention_present")
        if not inventory_row["owner_isolation_ok"]:
            blockers.append("owner_isolation_failed")
        if any(
            item["checksum_status"] in {"mismatch", "missing", "unreadable"}
            for item in inventory_row["items"]
        ):
            blockers.append("inventory_checksum_unverified")
        deletion_manifest.append(
            {
                "presentation_id": census_row["presentation_id"],
                "classification": census_row["classification"],
                "eligible": not blockers,
                "blockers": blockers,
            }
        )
    deletion_blockers = sorted(
        {blocker for entry in deletion_manifest for blocker in entry["blockers"]}
    )
    report = {
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "read_only": True,
        "total_presentations": len(report_rows),
        "classification_counts": classification_counts,
        "coverage_complete": sum(classification_counts.values()) == len(report_rows),
        "quarantine": quarantined,
        "active_job_drain": {
            "required": bool(drain),
            "count": len(drain),
            "jobs": drain,
        },
        "owner_isolation_ok": all(
            row["inventory"]["owner_isolation_ok"] for row in report_rows
        ),
        "deletion_readiness": {
            # Census has no authority to approve or execute deletion. Keep this
            # blocked even when all per-presentation gates happen to pass.
            "status": "blocked",
            "approval_required": True,
            "physical_deletion_performed": False,
            "eligible_count": sum(
                1 for entry in deletion_manifest if entry["eligible"]
            ),
            "blocked_count": sum(
                1 for entry in deletion_manifest if not entry["eligible"]
            ),
            "blockers": deletion_blockers,
            "manifest": deletion_manifest,
        },
        "presentations": report_rows,
    }
    report["evidence_sha256"] = sha256(
        json.dumps(report, sort_keys=True, separators=(",", ":"), default=str).encode(
            "utf-8"
        )
    ).hexdigest()
    return report


def _require_evidence_id(value: str, label: str) -> str:
    normalized = value.strip()
    if _EVIDENCE_ID_PATTERN.fullmatch(normalized) is None:
        raise SystemExit(f"{label} must match {_EVIDENCE_ID_PATTERN.pattern}")
    return normalized


def _git_commit() -> str:
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=FASTAPI_ROOT.parent.parent,
        capture_output=True,
        check=False,
        text=True,
    )
    candidate = result.stdout.strip().lower()
    if result.returncode != 0 or re.fullmatch(r"[0-9a-f]{40}", candidate) is None:
        raise SystemExit("unable to resolve a 40-character git commit for census evidence")
    return candidate


def build_run_metadata(
    rows: dict[str, list[dict[str, Any]]],
    *,
    run_id: str,
    release_id: str,
    build_id: str | None,
    git_commit: str,
) -> dict[str, Any]:
    revisions = sorted(
        _string(row.get("version_num"))
        for row in rows.get("alembic_version", [])
        if row.get("version_num") is not None
    )
    salt = os.getenv("AUTHORED_ONLY_ROLLOUT_SALT", "authored-only-default-v1")
    return {
        "run_id": _require_evidence_id(run_id, "run ID"),
        "release_id": _require_evidence_id(release_id, "release ID"),
        "build_id": _require_evidence_id(build_id, "build ID") if build_id else None,
        "git_commit": _require_evidence_id(git_commit, "git commit"),
        "database_schema_revisions": revisions,
        "flags": {
            "admission": os.getenv(
                "AUTHORED_ONLY_NEW_PRESENTATION_ADMISSION", "false"
            ),
            "canary_stage": os.getenv("AUTHORED_ONLY_CANARY_STAGE", "internal"),
            "rollout_salt_sha256": sha256(salt.encode("utf-8")).hexdigest(),
        },
    }


def _read_connection(connection) -> dict[str, list[dict[str, Any]]]:
    table_names = set(inspect(connection).get_table_names())
    wanted = {
        "presentations",
        "slides",
        "reference_snapshots",
        "workspace_assets",
        "imageasset",
        "image_assets",
        "presentation_generation_jobs",
        "authored_slide_checkpoints",
        "workspace_operations",
        "workspace_artifacts",
        "template_v2_pptx_imports",
        "template_v2",
        "template_v2_local_state",
        "alembic_version",
    }
    result: dict[str, list[dict[str, Any]]] = {}
    for name in sorted(wanted & table_names):
        table = Table(name, MetaData(), autoload_with=connection)
        result[name] = [dict(row) for row in connection.execute(select(table)).mappings()]
    return result


async def _read_async_database(database_url: str) -> dict[str, list[dict[str, Any]]]:
    engine = create_async_engine(database_url)
    try:
        async with engine.connect() as connection:
            return await connection.run_sync(_read_connection)
    finally:
        await engine.dispose()


def _read_database(database_url: str) -> dict[str, list[dict[str, Any]]]:
    if "+aiosqlite:" in database_url or "+asyncpg:" in database_url:
        return asyncio.run(_read_async_database(database_url))
    engine = create_engine(database_url)
    try:
        with engine.connect() as connection:
            return _read_connection(connection)
    finally:
        engine.dispose()


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Read-only authored-only presentation census and inventory"
    )
    parser.add_argument(
        "--database-url",
        default=os.getenv("DATABASE_URL"),
        help="SQLAlchemy database URL (or DATABASE_URL)",
    )
    parser.add_argument("--storage-root", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--pretty", action="store_true")
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--release-id", required=True)
    parser.add_argument("--build-id")
    parser.add_argument("--git-commit")
    return parser


def main() -> int:
    args = _parser().parse_args()
    if not args.database_url:
        raise SystemExit("database URL is required via --database-url or DATABASE_URL")
    rows = _read_database(args.database_url)
    report = build_authored_only_report(
        rows,
        storage_root=args.storage_root,
    )
    report["run_metadata"] = build_run_metadata(
        rows,
        run_id=args.run_id,
        release_id=args.release_id,
        build_id=args.build_id,
        git_commit=args.git_commit or _git_commit(),
    )
    report["evidence_sha256"] = sha256(
        json.dumps(
            {key: value for key, value in report.items() if key != "evidence_sha256"},
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        ).encode("utf-8")
    ).hexdigest()
    payload = json.dumps(
        report,
        ensure_ascii=False,
        indent=2 if args.pretty else None,
        sort_keys=True,
    )
    if args.output is not None:
        args.output.write_text(payload + "\n", encoding="utf-8")
    else:
        print(payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
