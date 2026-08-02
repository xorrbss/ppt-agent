"""Read-only, fail-closed preflight for one Template V2 catalog candidate.

The command accepts proposed local catalog and rights manifests so an operator
can vet a candidate before replacing the repository manifests. It never writes
the database, native source storage, manifests, or rights evidence and never
prints approval identities, attestation values, or secret material.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import select, text

from services.template_v2_native_storage import (
    TemplateV2NativeStorageError,
    resolve_native_source,
)
from services.template_v2_pptx_operations import template_v2_database_safety
from templates.v2.catalog import (
    DEFAULT_CATALOG_PATH,
    DEFAULT_RIGHTS_PATH,
    TemplateRightsRecord,
    TemplateV2CatalogEntry,
    evaluate_rights_readiness,
    get_rights_attestation_key,
    get_trusted_rights_approvers,
    load_template_rights_manifest,
    load_template_v2_catalog,
)
from templates.v2.policy import get_structured_template_policy
from templates.v2.publication import evaluate_template_v2_publication


def _expected_alembic_heads() -> set[str]:
    fastapi_root = Path(__file__).resolve().parents[1]
    config = Config(str(fastapi_root / "alembic.ini"))
    config.set_main_option("script_location", str(fastapi_root / "alembic"))
    return set(ScriptDirectory.from_config(config).get_heads())


def _candidate_source_is_bound(
    entry: TemplateV2CatalogEntry,
    record: TemplateRightsRecord,
    assets: object,
) -> bool:
    """Apply the runtime source binding to caller-selected manifests."""

    candidate_files = [
        item for item in entry.source.files if item.role == "candidate_pptx"
    ]
    if len(candidate_files) != 1 or not isinstance(assets, Mapping):
        return False
    native_source = assets.get("native_pptx")
    if not isinstance(native_source, Mapping):
        return False
    candidate = candidate_files[0]
    return (
        record.template_id == entry.id
        and native_source.get("preservation_mode") == entry.preservation_mode
        and native_source.get("source_sha256") == candidate.sha256
        and native_source.get("source_size_bytes")
        == record.source.candidate_pptx_size_bytes
        and record.source.candidate_pptx_sha256 == candidate.sha256
        and record.source.candidate_pptx_path == candidate.path
    )


def _native_source_metadata(assets: object) -> Mapping[str, Any] | None:
    if not isinstance(assets, Mapping):
        return None
    native_source = assets.get("native_pptx")
    return native_source if isinstance(native_source, Mapping) else None


def _effective_revision(template: object, local_state: object | None) -> int | None:
    revision = (
        getattr(local_state, "revision", None)
        if local_state is not None
        else getattr(template, "revision", None)
    )
    if isinstance(revision, bool) or not isinstance(revision, int) or revision <= 0:
        return None
    return revision


async def _run_live_candidate_checks(
    entry: TemplateV2CatalogEntry,
    record: TemplateRightsRecord,
) -> tuple[dict[str, object], bool]:
    """Verify schema, shared database row, and retained package without writes."""

    from models.sql.template_v2 import TemplateV2
    from models.sql.template_v2_local_state import TemplateV2LocalState
    from services.database import async_session_maker, dispose_engines

    payload: dict[str, object] = {
        "database_reachable": False,
        "schema_at_head": False,
        "database_row_ready": False,
        "publication_evidence_ready": False,
        "source_binding_ready": False,
        "native_source_ready": False,
    }
    try:
        try:
            async with async_session_maker() as session:
                await session.execute(text("SELECT 1"))
                payload["database_reachable"] = True
                revisions = set(
                    (
                        await session.execute(
                            text("SELECT version_num FROM alembic_version")
                        )
                    )
                    .scalars()
                    .all()
                )
                schema_at_head = revisions == _expected_alembic_heads()
                payload["schema_at_head"] = schema_at_head
                if not schema_at_head:
                    payload["live_code"] = "template_v2_schema_head_mismatch"
                    return payload, False

                template = (
                    (
                        await session.execute(
                            select(TemplateV2)
                            .where(TemplateV2.id == entry.id)
                            .execution_options(skip_owner_scope=True)
                        )
                    )
                    .scalars()
                    .one_or_none()
                )
                if template is None:
                    payload["live_code"] = "template_v2_catalog_row_missing"
                    return payload, False
                if template.owner_id is not None:
                    payload["live_code"] = "template_v2_catalog_row_not_shared"
                    return payload, False

                local_state = (
                    (
                        await session.execute(
                            select(TemplateV2LocalState)
                            .where(TemplateV2LocalState.template_id == entry.id)
                            .execution_options(skip_owner_scope=True)
                        )
                    )
                    .scalars()
                    .one_or_none()
                )
                revision = _effective_revision(template, local_state)
                if revision is None:
                    payload["live_code"] = "template_v2_catalog_revision_invalid"
                    return payload, False
                payload["database_row_ready"] = True
                assets = template.assets
        except Exception:  # noqa: BLE001 - expose only a stable failure code
            payload["live_code"] = (
                "template_v2_database_connection_or_candidate_check_failed"
            )
            return payload, False

        publication = evaluate_template_v2_publication(assets)
        payload["publication_evidence_ready"] = publication.publishable
        payload["publication_code"] = publication.reason_code
        if not publication.publishable:
            payload["live_code"] = publication.reason_code
            return payload, False

        source_bound = _candidate_source_is_bound(entry, record, assets)
        payload["source_binding_ready"] = source_bound
        if not source_bound:
            payload["live_code"] = "template_v2_catalog_source_binding_mismatch"
            return payload, False

        native_source = _native_source_metadata(assets)
        if native_source is None:
            payload["live_code"] = "template_v2_native_source_metadata_missing"
            return payload, False
        try:
            resolve_native_source(
                native_source.get("storage_key"),
                template_id=entry.id,
                revision=revision,
                expected_sha256=native_source.get("source_sha256"),
                expected_size_bytes=native_source.get("source_size_bytes"),
            )
        except (OSError, TypeError, TemplateV2NativeStorageError):
            payload["live_code"] = "template_v2_native_source_verification_failed"
            return payload, False

        payload["native_source_ready"] = True
        payload["live_code"] = "template_v2_catalog_candidate_live_ready"
        return payload, True
    finally:
        await dispose_engines()


def _blocked_payload(code: str, *, template_id: str) -> dict[str, object]:
    return {
        "schema_version": "presenton.template-v2-catalog-preflight/v1",
        "template_id": template_id,
        "ready": False,
        "code": code,
    }


def _select_candidate(
    *,
    template_id: str,
    catalog_path: Path,
    rights_path: Path,
) -> tuple[TemplateV2CatalogEntry, TemplateRightsRecord]:
    catalog = load_template_v2_catalog(catalog_path)
    rights = load_template_rights_manifest(rights_path)
    entry = next((item for item in catalog.entries if item.id == template_id), None)
    if entry is None:
        raise LookupError("template_v2_catalog_candidate_missing")
    record = next(
        (item for item in rights.records if item.template_id == template_id),
        None,
    )
    if record is None:
        raise LookupError("template_v2_catalog_rights_record_missing")
    return entry, record


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Read-only preflight for one Template V2 catalog candidate."
    )
    parser.add_argument("--template-id", required=True)
    parser.add_argument(
        "--catalog-manifest",
        type=Path,
        default=DEFAULT_CATALOG_PATH,
    )
    parser.add_argument(
        "--rights-manifest",
        type=Path,
        default=DEFAULT_RIGHTS_PATH,
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    payload = _blocked_payload(
        "template_v2_catalog_candidate_preflight_failed",
        template_id=args.template_id,
    )
    try:
        entry, record = _select_candidate(
            template_id=args.template_id,
            catalog_path=args.catalog_manifest,
            rights_path=args.rights_manifest,
        )
    except LookupError as error:
        payload["code"] = str(error)
        print(json.dumps(payload, sort_keys=True))
        return 2
    except (OSError, ValueError, json.JSONDecodeError):
        payload["code"] = "template_v2_catalog_manifest_invalid"
        print(json.dumps(payload, sort_keys=True))
        return 2

    policy = get_structured_template_policy()
    rollout = policy.canary_readiness()
    candidate_allowlisted = rollout.ready and policy.can_discover(entry.id)
    database_safety = template_v2_database_safety()
    trusted_approvers = get_trusted_rights_approvers()
    attestation_key = get_rights_attestation_key()
    try:
        rights = evaluate_rights_readiness(
            entry,
            record,
            evidence_root=args.rights_manifest.parent,
            trusted_approvers=trusted_approvers,
            attestation_key=attestation_key,
        )
    except (OSError, ValueError):
        payload["code"] = "template_v2_catalog_rights_check_failed"
        print(json.dumps(payload, sort_keys=True))
        return 2

    payload.update(
        {
            "catalog_status_ready": entry.publication_status == "published",
            "rollout_ready": rollout.ready,
            "rollout_code": rollout.code,
            "candidate_allowlisted": candidate_allowlisted,
            "database_safe": database_safety.safe,
            "database_code": database_safety.code,
            "rights_approvers_configured": bool(trusted_approvers),
            "rights_attestation_key_configured": attestation_key is not None,
            "rights_ready": rights.ready_for_publication_review,
            "rights_blockers": list(rights.blockers),
        }
    )
    if entry.publication_status != "published":
        payload["code"] = "template_v2_catalog_candidate_not_published"
    elif not rights.ready_for_publication_review:
        payload["code"] = "template_v2_catalog_rights_not_ready"
    elif not rollout.ready:
        payload["code"] = rollout.code
    elif not candidate_allowlisted:
        payload["code"] = "template_v2_catalog_candidate_not_allowlisted"
    elif not database_safety.safe:
        payload["code"] = database_safety.code
    else:
        try:
            live_payload, live_ready = asyncio.run(
                _run_live_candidate_checks(entry, record)
            )
        except Exception:  # noqa: BLE001 - never expose connection/source details
            live_payload = {
                "live_code": "template_v2_catalog_candidate_live_check_failed"
            }
            live_ready = False
        payload.update(live_payload)
        if live_ready:
            payload["ready"] = True
            payload["code"] = "template_v2_catalog_candidate_ready"
        else:
            payload["code"] = str(
                live_payload.get("live_code")
                or "template_v2_catalog_candidate_live_check_failed"
            )

    print(json.dumps(payload, sort_keys=True))
    return 0 if payload["ready"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
