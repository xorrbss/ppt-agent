"""Owner-bound, explicit recovery episodes for an existing SVG presentation.

The compatibility job is unique per presentation. Before its normal retry
transition, its complete prior episode is sealed in the new operation's DB
payload. Provider history remains attached to the original operations.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
import uuid

from sqlalchemy import Text, cast, func, or_, select
from sqlalchemy.orm import load_only

from models.sql.authored_slide_checkpoint import AuthoredSlideCheckpoint
from models.sql.presentation_generation_job import PresentationGenerationJob
from models.sql.public_admission import AuthoredDeckPlanCheckpoint, PublicOperationAdmission, PublicProviderLogicalCall, PublicResourceLease
from models.sql.provider_runtime_reservation import ProviderRuntimeBudget, ProviderRuntimeReservation
from models.sql.provider_usage_receipt import ProviderUsageReceipt
from models.sql.workspace import WorkspaceOperation
from services.svg_cached_author_render_recovery import _unknown_provider, author_witness, digest
from services.svg_timeout_repurchase import _live
from utils.datetime_utils import get_current_utc_datetime
from utils.hashing import sha256_hex

REVISION = "svg-partial-recovery/v1"
LINEAGE_KEY = "_svg_partial_recovery"
ARCHIVE_KEY = "_svg_partial_recovery_archive"
ARCHIVE_V1 = "svg-partial-recovery-archive/v1"
ARCHIVE_V2 = "svg-partial-recovery-archive/v2"
OPERATION_REFERENCE = "svg-partial-recovery-operation-ref/v1"
PAYLOAD_HASH_ALGORITHM = "sha256/postgresql-json-text-utf8/v1"
CODE_FILES = (
    "services/svg_partial_recovery.py",
    "services/generation_core/drivers/svg/collect.py",
    "services/generation_core/drivers/svg/author.py",
    "services/generation_core/drivers/svg/render.py",
    "services/generation_core/drivers/svg/quality.py",
    "services/generation_core/quality/vision_advisory.py",
    "services/generation_core/quality/vision_advisory_prompt.py",
    "services/svg_source_layout.py",
    "services/document_canvas_generation.py",
    "services/presentation_generation_staging.py",
    "services/presentation_generation_pipeline.py",
    "services/workspace_service.py",
    "services/svg_authoring_profile.py",
    "services/provider_durable_admission.py",
    "services/provider_usage_instrumentation.py",
    "services/provider_runtime_job_registration.py",
    "services/generation_core/drivers/svg/partial_quality_recovery.py",
    "services/svg_presentation_prepare.py",
    "services/svg_presentation_checkpoint.py",
    "services/svg_generation_assets.py",
    "services/svg_default_fonts.py",
    "services/svg_contract.py",
    "services/svg_renderer.py",
    "services/svg_master_furniture.py",
    "services/generation_core/drivers/svg/native_declarations.py",
    "services/generation_core/drivers/svg/asset_slots.py",
    "services/generation_core/drivers/svg/embed_assets.py",
    "services/generation_core/drivers/svg/icon_flatten.py",
    "services/generation_core/drivers/svg/icon_geometry.py",
    "services/generation_core/assets/offer.py",
    "utils/authored_image_slots.py",
    "utils/codex_stream_adapter.py",
    "utils/slide_capture_result.py",
)


class PartialRecoveryRejected(ValueError):
    retryable = False

    def __init__(self, code):
        super().__init__(code)
        self.code = code


def reject(code="svg_partial_recovery_unproven"):
    raise PartialRecoveryRejected(code)


def _dump(row):
    return row.model_dump(mode="json")


def _operation_control(row):
    return row.model_dump(mode="json", exclude={"request_payload"})


def _sha256_hex_valid(value):
    return isinstance(value, str) and len(value) == 64 and all(char in "0123456789abcdef" for char in value)


async def _payload_storage_seals(session, operation_ids):
    """Hash actual PostgreSQL JSON bytes without transferring ancestor bodies."""
    if session.get_bind().dialect.name != "postgresql":
        reject("svg_partial_recovery_archive_v2_postgresql_required")

    def sha_text(value):
        return func.encode(func.sha256(func.convert_to(cast(value, Text), "UTF8")), "hex")

    result = await session.execute(select(
        WorkspaceOperation.id,
        sha_text(WorkspaceOperation.request_payload).label("payload_sha256"),
        sha_text(WorkspaceOperation.request_payload[ARCHIVE_KEY]).label("archive_storage_sha256"),
        WorkspaceOperation.request_payload[ARCHIVE_KEY]["schema"].as_string().label("archive_schema"),
        WorkspaceOperation.request_payload["svg_partial_recovery_manifest"]["archive_digest"].as_string().label("archive_canonical_sha256"),
        WorkspaceOperation.request_payload["principal_tenant_id"].as_string().label("tenant_id"),
    ).where(WorkspaceOperation.id.in_(operation_ids)))
    return {str(row["id"]): dict(row) for row in result.mappings()}


def _operation_reference(row, storage_seal, *, tenant):
    """Seal one full payload by immutable DB reference, never recursive copy."""
    payload = row.request_payload
    prior_archive = payload.get(ARCHIVE_KEY)
    if (storage_seal is None or storage_seal.get("tenant_id") != tenant
            or not _sha256_hex_valid(storage_seal.get("payload_sha256"))):
        reject("svg_partial_recovery_archive_reference_unbound")
    archive_schema = archive_digest = None
    if ARCHIVE_KEY in payload:
        if not isinstance(prior_archive, dict) or prior_archive.get("schema") not in {ARCHIVE_V1, ARCHIVE_V2}:
            reject("svg_partial_recovery_archive_schema_invalid")
        archive_schema, archive_digest = prior_archive["schema"], digest(prior_archive)
        prior_manifest = payload.get("svg_partial_recovery_manifest") or {}
        if (not isinstance(prior_manifest, dict)
                or not _sha256_hex_valid(storage_seal.get("archive_storage_sha256"))
                or prior_manifest.get("archive_digest") != archive_digest
                or payload.get("svg_partial_recovery_manifest_hash") != digest(prior_manifest)
                or storage_seal.get("archive_schema") != archive_schema
                or storage_seal.get("archive_canonical_sha256") != archive_digest):
            reject("svg_partial_recovery_history_changed")
    elif any(storage_seal.get(key) is not None for key in (
            "archive_storage_sha256", "archive_schema", "archive_canonical_sha256")):
        reject("svg_partial_recovery_archive_reference_unbound")
    control = _operation_control(row)
    return {"schema": OPERATION_REFERENCE, "id": str(row.id),
        "control": control, "control_digest": digest(control),
        "payload_ref": {"algorithm": PAYLOAD_HASH_ALGORITHM,
            "operation_id": str(row.id), "owner_id": str(row.owner_id),
            "owner_scope": row.owner_scope, "tenant_id": tenant,
            "request_hash": row.request_hash,
            "payload_sha256": storage_seal["payload_sha256"],
            "archive_schema": archive_schema, "archive_canonical_sha256": archive_digest,
            "archive_storage_sha256": storage_seal["archive_storage_sha256"]}}


async def _verify_operation_references(session, operation, manifest, references):
    if not isinstance(references, list) or not references:
        reject("svg_partial_recovery_archive_reference_unbound")
    ids = []
    expected = str(operation.retry_of_operation_id)
    for entry in references:
        if not isinstance(entry, dict):
            reject("svg_partial_recovery_archive_reference_unbound")
        control, ref = entry.get("control") or {}, entry.get("payload_ref") or {}
        if (not isinstance(control, dict) or not isinstance(ref, dict)
                or entry.get("schema") != OPERATION_REFERENCE or entry.get("id") != expected
                or control.get("id") != expected or digest(control) != entry.get("control_digest")
                or "request_payload" in control or ref.get("algorithm") != PAYLOAD_HASH_ALGORITHM
                or ref.get("operation_id") != expected or ref.get("owner_id") != manifest["owner_id"]
                or not _sha256_hex_valid(ref.get("payload_sha256"))
                or ref.get("owner_scope") != operation.owner_scope or ref.get("tenant_id") != manifest["tenant_id"]
                or ref.get("request_hash") != control.get("request_hash")
                or control.get("owner_id") != manifest["owner_id"]
                or control.get("owner_scope") != operation.owner_scope
                or control.get("presentation_id") != str(operation.presentation_id)
                or control.get("conversation_id") != str(operation.conversation_id)
                or control.get("kind") != "presentation.generate"
                or control.get("state") not in {"failed", "cancelled", "dead_letter"}
                or expected in ids):
            reject("svg_partial_recovery_archive_reference_unbound")
        if (ref.get("archive_schema") is not None and (
                ref["archive_schema"] not in {ARCHIVE_V1, ARCHIVE_V2}
                or not _sha256_hex_valid(ref.get("archive_storage_sha256"))
                or not _sha256_hex_valid(ref.get("archive_canonical_sha256")))
                or ref.get("archive_schema") is None and any(ref.get(key) is not None
                    for key in ("archive_storage_sha256", "archive_canonical_sha256"))):
            reject("svg_partial_recovery_archive_reference_unbound")
        ids.append(expected)
        parent = control.get("retry_of_operation_id")
        expected = str(parent) if parent is not None else None
    if expected is not None or ids[-1] != manifest["source_operation_id"]:
        reject("svg_partial_recovery_lineage_conflict")
    columns = [getattr(WorkspaceOperation, column.key) for column in WorkspaceOperation.__table__.columns
               if column.key != "request_payload"]
    originals = {str(row.id): row for row in await session.scalars(select(WorkspaceOperation)
        .options(load_only(*columns, raiseload=True))
        .where(WorkspaceOperation.id.in_([uuid.UUID(value) for value in ids])))}
    seals = await _payload_storage_seals(session, [uuid.UUID(value) for value in ids])
    for entry in references:
        original, seal = originals.get(entry["id"]), seals.get(entry["id"])
        ref = entry["payload_ref"]
        if (original is None or seal is None
                or digest(_operation_control(original)) != entry["control_digest"]
                or seal.get("tenant_id") != ref["tenant_id"]
                or any(seal.get(key) != ref.get(key) for key in (
                    "payload_sha256", "archive_storage_sha256", "archive_schema", "archive_canonical_sha256"))):
            reject("svg_partial_recovery_history_changed")


def _code_digest():
    root = Path(__file__).resolve().parents[1]
    return digest([{ "path": name, "sha256": sha256_hex((root / name).read_bytes())}
                   for name in CODE_FILES])


def _frozen_payload(payload):
    return {key: value for key, value in payload.items() if key != LINEAGE_KEY}


def _undispatched_episode_page(payload, job, operation, owner, tenant):
    """Only a new, server-sealed episode can prove that SDK I/O never began."""
    if (not job.request_payload.get(LINEAGE_KEY) or payload.get("attempts")
            or payload.get("svg_content") or payload.get("inflight") is not None):
        return False
    records = payload.get("provider_terminal_responses")
    if not isinstance(records, dict) or not records:
        return False
    for record in records.values():
        if not isinstance(record, dict):
            return False
        unsigned = {key: value for key, value in record.items() if key != "receipt_digest"}
        if (record.get("schema") != "svg-provider-terminal/v1"
                or record.get("terminal_event") != "dispatch.stopped"
                or record.get("receipt_digest") != digest(unsigned)
                or record.get("operation_id") != str(operation.id)
                or record.get("job_id") != str(job.id) or record.get("owner_id") != str(owner)
                or record.get("tenant_id") != tenant or not record.get("input_digest")
                or record.get("input_digest") != payload.get("input_digest")
                or record.get("response_id") is not None or "raw_response" in record or "usage" in record):
            return False
    return True


def _completed_response(payload, job, operation, owner, tenant):
    records = payload.get("provider_terminal_responses") or {}
    if not isinstance(records, dict):
        return None
    for record in reversed(list(records.values())):
        if not isinstance(record, dict) or record.get("terminal_event") != "response.completed":
            continue
        unsigned = {key: value for key, value in record.items() if key != "receipt_digest"}
        raw = record.get("raw_response")
        if (record.get("schema") == "svg-provider-terminal/v1"
                and record.get("receipt_digest") == digest(unsigned)
                and record.get("operation_id") == str(operation.id)
                and record.get("job_id") == str(job.id) and record.get("owner_id") == str(owner)
                and record.get("tenant_id") == tenant and record.get("input_digest")
                and record.get("input_digest") == payload.get("input_digest")
                and isinstance(raw, str) and record.get("output_digest") == sha256_hex(raw.encode())
                and isinstance(record.get("usage"), dict)):
            return record
    return None


def _frozen_vector_offer(seed_row, job, operation, owner, tenant):
    """Read only the DB-frozen catalogue and its content-bound library files."""
    from services.generation_core.assets.offer import AssetOffer
    from services.generation_core.drivers.svg.asset_slots import SEED_KEY, _ink
    from services.generation_core.drivers.svg.embed_assets import flatten_icon_svg, IconFlattenBudgetExceeded
    from utils.authored_image_slots import source_slot_id_for_asset
    from utils.get_env import get_app_data_directory_env

    seed = (seed_row.svg_checkpoint or {}).get(SEED_KEY)
    if seed is None:
        return [{} for _ in range(job.total_slides)], None
    if seed_row.job_id != job.id or seed_row.slide_index != 0 or job.workspace_operation_id != operation.id:
        reject("svg_partial_recovery_vector_seed_unbound")
    offer = AssetOffer.from_json(seed, root=get_app_data_directory_env())
    maps = [{} for _ in range(job.total_slides)]
    files = []
    for slot in offer.slots:
        if type(slot.page_index) is not int or not 0 <= slot.page_index < job.total_slides:
            reject("svg_partial_recovery_vector_page_unbound")
        if not slot.is_vector:
            continue
        if (slot.kind != "icon" or slot.origin != "library" or slot.role != "figure-icon"
                or slot.media_type != "image/svg+xml" or Path(slot.path).suffix.lower() != ".svg"
                or type(slot.width) is not int or type(slot.height) is not int
                or slot.width <= 0 or slot.height <= 0 or Path(slot.path).stat().st_size > 2 * 1024 * 1024):
            reject("svg_partial_recovery_vector_metadata_unbound")
        payload = slot.verify()
        text = payload.decode("utf-8")
        files.append({"page_index": slot.page_index, "sha256": slot.sha256})
        try:
            icon = flatten_icon_svg(text, color=_ink(text))
        except IconFlattenBudgetExceeded:
            # Original projection represents this as an owned raster slot.
            # Its raster authority stays in the source asset manifest.
            continue
        slot_id = source_slot_id_for_asset(f"vector:{job.presentation_id}:{slot.sha256}")
        maps[slot.page_index][slot_id] = icon
    return maps, {"owner_id": str(owner), "tenant_id": tenant,
        "operation_id": str(operation.id), "job_id": str(job.id),
        "seed_row_id": str(seed_row.id), "seed_row_digest": digest(_dump(seed_row)),
        "plan_digest": digest(seed), "files": files}


def _author_render_chain(payload, raw, receipt, *, request, job, asset, index,
                         row=None, completed=None, vectors=None, vector_seed_witness=None):
    """Replay the existing local transforms; hashes alone never authorize bytes."""
    import json
    import xml.etree.ElementTree as ET
    from services.authored_canvas import request_canvas_contract
    from services.svg_contract import normalize_svg_response, finalize_svg
    from services.svg_default_fonts import normalize_model_font_families
    from services.generation_core.drivers.svg.native_declarations import (
        extract_native_declarations, declaration_receipt,
    )

    canvas = request_canvas_contract(request)
    offered = next((page.slot_ids for page in asset.offered_pages if page.page_index == index), ())
    slots = {slot.slot_id: slot for slot in asset.slots if slot.slot_id in offered}
    page_assets = {key: slot.model_dump(mode="json", by_alias=True) for key, slot in slots.items()}
    expected_input = digest({"content": job.authored_outline["slides"][index]["content"],
        "style": job.svg_style_snapshot, "index": index, "count": job.total_slides,
        "instructions": request.instructions, "language": (request.language or "").strip() or None,
        "preset": request.codex_generation_preset})
    expected_input = digest({"page": expected_input, "assets": page_assets})
    if request.source_mode is None and payload.get("input_digest") != expected_input:
        reject("svg_partial_recovery_page_input_changed")
    # The current author transforms declarations and serializes XML before
    # its receipt, then the worker normalizes fonts and adds master furniture.
    # Canonical asset hrefs stay opaque; unproved vector substitutions cannot
    # be reconstructed merely from a checkpoint's self-reported used list.
    if request.source_mode == "source_content_restyle":
        author_svg = payload.get("svg_content")
        if str(receipt.get("source_digest", "")).removeprefix("sha256:") != sha256_hex(author_svg.encode()):
            reject("svg_partial_recovery_source_transform_unproven")
        return {"author_svg_sha256": sha256_hex(author_svg.encode()),
                "render_svg_sha256": sha256_hex(author_svg.encode()), "transform": "source-preserved"}
    vectors = vectors or {}
    vector_offer = (payload.get("assetOffer") or {}).get("vectorIcons") or {}
    recorded_used = receipt.get("vector_icons_used") or []
    if (vectors or recorded_used or vector_offer.get("offered")) and vector_seed_witness is None:
        reject("svg_partial_recovery_vector_transform_unproven")
    try:
        root = ET.fromstring(normalize_svg_response(raw))
    except ET.ParseError as error:
        raise PartialRecoveryRejected("svg_partial_recovery_author_transform_unproven") from error
    declarations = extract_native_declarations(root)
    if declaration_receipt(declarations).get("native_declarations") != receipt.get("native_declarations"):
        reject("svg_partial_recovery_author_declarations_changed")
    from services.generation_core.drivers.svg.embed_assets import replace_vector_icon_images

    used = replace_vector_icon_images(root, vectors) if vectors else set()
    if (not isinstance(recorded_used, list) or len(recorded_used) != len(set(recorded_used))
            or used != set(recorded_used)
            or (vectors or recorded_used or vector_offer) and (
                not isinstance(vector_offer.get("offered"), list)
                or not isinstance(vector_offer.get("used"), list)
                or len(vector_offer["offered"]) != len(set(vector_offer["offered"]))
                or len(vector_offer["used"]) != len(set(vector_offer["used"]))
                or set(vector_offer["offered"]) != set(vectors)
                or set(vector_offer["used"]) != used)):
        reject("svg_partial_recovery_vector_transform_unproven")
    for node in root.iter():
        if node.tag.rsplit("}", 1)[-1] == "image" and node.get("href") not in slots:
            reject("svg_partial_recovery_author_asset_unproven")
    author_svg = ET.tostring(root, encoding="unicode")
    author_digest = sha256_hex(author_svg.encode())
    if str(receipt.get("source_digest", "")).removeprefix("sha256:") != author_digest:
        reject("svg_partial_recovery_author_transform_unproven")
    normalized, advisories = normalize_model_font_families(author_svg)
    if (payload.get("fontAdvisories") or []) != advisories:
        reject("svg_partial_recovery_font_transform_unproven")
    contract = {key: {"required": slot.policy.required, "allowOmit": slot.policy.allow_omit}
                for key, slot in slots.items()}
    first = finalize_svg(normalized, index=index, n=job.total_slides,
        width=canvas.width_px, height=canvas.height_px, style_snapshot=job.svg_style_snapshot,
        source_manifest=contract)
    stored = payload["svg_content"]
    if stored == normalized and row is not None and row.render_status == "pending":
        from utils.owner_context import get_current_owner_id, get_current_tenant_id

        terminal = completed or {}
        unsigned = {key: value for key, value in terminal.items() if key != "receipt_digest"}
        if (row.author_status == "completed" and row.persist_status == "pending"
                and row.render_path is None and row.render_sha256 is None
                and payload.get("quality") in (None, {}) and payload.get("initial_quality") in (None, {})
                and payload.get("png_sha256") is None and payload.get("png_path") is None
                and payload.get("source_digest") in (None, sha256_hex(stored.encode()), "sha256:" + sha256_hex(stored.encode()))
                and payload.get("last_valid_svg") in (None, stored)
                and payload.get("local_readability_repair") in (None, {})
                and terminal.get("schema") == "svg-provider-terminal/v1"
                and terminal.get("terminal_event") == "response.completed"
                and terminal.get("receipt_digest") == digest(unsigned)
                and terminal.get("raw_response") == raw
                and terminal.get("output_digest") == sha256_hex(raw.encode())
                and terminal.get("job_id") == str(job.id)
                and terminal.get("operation_id") == str(job.workspace_operation_id)
                and terminal.get("owner_id") == str(get_current_owner_id())
                and terminal.get("tenant_id") == get_current_tenant_id()
                and terminal.get("input_digest") == payload.get("input_digest")):
            return {"phase": "author-only", "author_svg_sha256": author_digest,
                "source_svg_sha256": sha256_hex(stored.encode()),
                "render_svg_sha256": sha256_hex(first.encode()),
                "first_render_svg_sha256": sha256_hex(first.encode()),
                "completed_receipt_digest": terminal["receipt_digest"],
                "vector_seed_witness": vector_seed_witness,
                "transform": "author-xml/default-font/planned-master-furniture"}
    if first != stored:
        from services.document_canvas_generation import minimal_svg_readability_repair

        initial = payload.get("initial_quality") or {}
        if str(initial.get("source_digest", "")).removeprefix("sha256:") != sha256_hex(first.encode()):
            reject("svg_partial_recovery_initial_render_unbound")
        repaired = minimal_svg_readability_repair(first, initial)
        candidate = repaired.pop("svg", None)
        saved_repair = {key: value for key, value in (payload.get("local_readability_repair") or {}).items()
                       if key not in {"remeasured", "remeasurement_passed"}}
        if not candidate or repaired != saved_repair:
            reject("svg_partial_recovery_render_transform_unproven")
        replayed = finalize_svg(candidate, index=index, n=job.total_slides,
            width=canvas.width_px, height=canvas.height_px, style_snapshot=job.svg_style_snapshot,
            source_manifest=contract)
        if replayed != stored:
            reject("svg_partial_recovery_render_transform_unproven")
    stored_digest = sha256_hex(stored.encode())
    if payload.get("source_digest") not in (None, stored_digest, "sha256:" + stored_digest):
        reject("svg_partial_recovery_render_source_unbound")
    if payload.get("last_valid_svg") not in (None, stored):
        reject("svg_partial_recovery_last_valid_source_unbound")
    quality = payload.get("quality") or {}
    metrics_transform = "identity"
    if quality:
        if str(quality.get("source_digest", "")).removeprefix("sha256:") != stored_digest:
            reject("svg_partial_recovery_render_measurement_unbound")
        metrics = quality.get("browser_metrics")
        metrics_digest = sha256_hex(json.dumps(metrics, sort_keys=True, separators=(",", ":"),
            ensure_ascii=False, default=str).encode()) if metrics is not None else None
        recorded_metrics_digest = str(quality.get("metrics_digest", "")).removeprefix("sha256:")
        if metrics_digest is not None and recorded_metrics_digest != metrics_digest:
            # svg_renderer stores PostgreSQL-safe probe keys (NUL -> U+241F),
            # while RenderCapture seals the metrics before that conversion.
            # Reverse precisely that existing codec on a local copy and prove
            # both its original capture digest and its roundtrip to DB bytes.
            recovered = json.loads(json.dumps(metrics).replace("\\u241f", "\\u0000"))
            recovered_digest = sha256_hex(json.dumps(recovered, sort_keys=True,
                separators=(",", ":"), ensure_ascii=False, default=str).encode())
            roundtrip = json.loads(json.dumps(recovered).replace("\\u0000", "\\u241f"))
            if recovered_digest != recorded_metrics_digest or roundtrip != metrics:
                reject("svg_partial_recovery_render_metrics_unbound")
            metrics_transform = "svg-renderer/postgres-probe-nul-sanitization"
        if payload.get("png_sha256") and str(quality.get("png_digest", "")).removeprefix("sha256:") != payload["png_sha256"]:
            reject("svg_partial_recovery_render_png_unbound")
    return {"phase": "finalized", "author_svg_sha256": author_digest,
            "source_svg_sha256": stored_digest, "render_svg_sha256": stored_digest,
            "first_render_svg_sha256": sha256_hex(first.encode()),
            "metrics_transform": metrics_transform,
            "vector_seed_witness": vector_seed_witness,
            "transform": "author-xml/default-font/master-furniture"}


@dataclass
class PartialRecovery:
    manifest: dict
    archive: dict
    rows: list
    manifest_hash: str
    approved_budget: int | None = None


def decision_nonce(manifest_hash):
    return digest({"schema": "svg-partial-recovery-decision/v1", "manifest_hash": manifest_hash})


def review_payload(recovery):
    """Only scalar binding, counts, and explicit-decision fields leave the API."""
    manifest = recovery.manifest
    return {"recovery_revision": REVISION, "manifest_hash": recovery.manifest_hash,
        "nonce": decision_nonce(recovery.manifest_hash),
        **{key: manifest[key] for key in ("operation_id", "operation_version", "presentation_id",
            "presentation_revision", "expected_page_count", "known_indices", "new_indices",
            "unknown_indices", "historical_actual_cost_unbounded", "proven_remaining_budget_microunits",
            "additional_budget_limit_microunits", "prospective_author_request_count",
            "prospective_initial_request_limit", "prospective_retry_request_limit")}}


async def build_manifest(session, operation, *, presentation_revision):
    """Read-only review surface; locks are released by the caller transaction."""
    from models.sql.async_presentation_generation_status import AsyncPresentationGenerationTaskModel
    from models.sql.presentation import PresentationModel
    from models.sql.source_content import SourceContentManifestRecord
    from services.presentation_generation_staging import _request_from_durable_payload
    from services.source_content.manifest_store import admit_source_content_manifest_pin
    from services.svg_asset_persistence import load_source_manifest_pointer
    from services.artifact_store import configured_public_artifact_service
    from services.svg_source_layout import verify_source_receipt
    from services.authored_canvas import request_canvas_metadata
    from services.svg_presentation_checkpoint import _read_png
    from services.svg_asset_manifest import ArtifactPointer
    from utils.owner_context import get_current_owner_id, get_current_tenant_id

    owner, tenant = get_current_owner_id(), get_current_tenant_id()
    now = get_current_utc_datetime()
    if (owner is None or operation.owner_id != owner or not tenant
            or operation.kind != "presentation.generate"
            or operation.state not in {"failed", "cancelled", "dead_letter"}
            or operation.result_revision is not None or _live(operation.lease_expires_at, now)):
        reject()
    presentation = await session.scalar(select(PresentationModel).where(
        PresentationModel.id == operation.presentation_id,
        PresentationModel.owner_id == owner).with_for_update())
    job = await session.scalar(select(PresentationGenerationJob).where(
        PresentationGenerationJob.presentation_id == operation.presentation_id,
        PresentationGenerationJob.workspace_operation_id == operation.id).with_for_update())
    if (presentation is None or presentation.revision != presentation_revision
            or job is None or job.state != "failed" or job.total_slides < 1
            or job.request_payload.get("strategy") != "svg"
            or not job.svg_style_snapshot or _live(job.lease_expires_at, now)
            or operation.request_payload.get("generation_request") != job.request_payload):
        reject("svg_partial_recovery_episode_conflict")
    if not isinstance(job.authored_outline, dict) or len(job.authored_outline.get("slides") or []) != job.total_slides:
        reject("svg_partial_recovery_frozen_outline_missing")
    task = await session.get(AsyncPresentationGenerationTaskModel, job.task_id, with_for_update=True)
    if task is None:
        reject("svg_partial_recovery_task_missing")
    request = _request_from_durable_payload(job.request_payload)
    pin = request.source_content_manifest_pin
    if (request.source_mode not in (None, "source_content_restyle")
            or request.source_mode == "source_content_restyle" and pin is None
            or request.web_search or request.n_slides != job.total_slides):
        reject("svg_partial_recovery_source_plan_invalid")
    record = source = None
    if pin is not None:
        record = await session.scalar(select(SourceContentManifestRecord).where(
            SourceContentManifestRecord.manifest_id == pin.manifest_id,
            SourceContentManifestRecord.owner_id == owner).with_for_update())
        if record is None:
            reject("svg_partial_recovery_source_missing")
        source = await admit_source_content_manifest_pin(session, owner_id=owner,
            manifest_id=pin.manifest_id, digest=pin.digest)
        if len(source.units) != job.total_slides:
            reject("svg_partial_recovery_source_count_changed")
    rows = list(await session.scalars(select(AuthoredSlideCheckpoint).where(
        AuthoredSlideCheckpoint.job_id == job.id).order_by(
        AuthoredSlideCheckpoint.slide_index).with_for_update()))
    if [row.slide_index for row in rows] != list(range(job.total_slides)):
        reject("svg_partial_recovery_checkpoint_set_incomplete")
    try:
        vector_maps, vector_seed_witness = _frozen_vector_offer(rows[0], job, operation, owner, tenant)
    except PartialRecoveryRejected:
        raise
    except (OSError, ValueError, TypeError, KeyError) as error:
        raise PartialRecoveryRejected("svg_partial_recovery_vector_transform_unproven") from error
    ancestors, admissions, calls, leases = [], [], [], []
    current, seen = operation, set()
    while current is not None:
        if (current.id in seen or current.owner_id != owner
                or current.owner_scope != operation.owner_scope
                or current.presentation_id != operation.presentation_id
                or current.conversation_id != operation.conversation_id
                or current.kind != "presentation.generate"
                or current.state not in {"failed", "cancelled", "dead_letter"}
                or _live(current.lease_expires_at, now)):
            reject("svg_partial_recovery_lineage_conflict")
        seen.add(current.id)
        ancestors.append(current)
        admission = await session.scalar(select(PublicOperationAdmission).where(
            PublicOperationAdmission.operation_id == current.id).with_for_update())
        if (admission is None or admission.owner_id != owner or admission.tenant_id != tenant
                or admission.state not in {"failed", "cancelled", "budget_exhausted", "deadline_exceeded"}
                or admission.terminal_at is None):
            reject("svg_partial_recovery_admission_unresolved")
        admissions.append(admission)
        calls.extend(await session.scalars(select(PublicProviderLogicalCall).where(
            PublicProviderLogicalCall.admission_id == admission.id).order_by(
            PublicProviderLogicalCall.id).with_for_update()))
        leases.extend(await session.scalars(select(PublicResourceLease).where(
            PublicResourceLease.admission_id == admission.id).order_by(
            PublicResourceLease.id).with_for_update()))
        current = await session.get(WorkspaceOperation, current.retry_of_operation_id,
            with_for_update=True) if current.retry_of_operation_id else None
    if any(lease.state not in {"released", "cancelled", "expired"} for lease in leases):
        reject("svg_partial_recovery_live_lease")
    usage = list(await session.scalars(select(ProviderUsageReceipt).where(
        ProviderUsageReceipt.job_id == job.id).order_by(ProviderUsageReceipt.id).with_for_update()))
    budgets = list(await session.scalars(select(ProviderRuntimeBudget).where(or_(
        ProviderRuntimeBudget.job_id == job.id,
        ProviderRuntimeBudget.workspace_operation_id.in_(seen))).order_by(
        ProviderRuntimeBudget.id).with_for_update()))
    reservations = list(await session.scalars(select(ProviderRuntimeReservation).where(
        ProviderRuntimeReservation.budget_id.in_([row.id for row in budgets])).order_by(
        ProviderRuntimeReservation.id).with_for_update())) if budgets else []
    if any(row.state == "reserved" for row in reservations):
        reject("svg_partial_recovery_active_reservation")
    pages, binding = [], None
    for row in rows:
        payload = row.svg_checkpoint or {}
        svg = payload.get("svg_content")
        prior_lineage = job.request_payload.get(LINEAGE_KEY) or {}
        undispatched = _undispatched_episode_page(payload, job, operation, owner, tenant)
        completed = _completed_response(payload, job, operation, owner, tenant)
        if completed and not (isinstance(svg, str) and svg and payload.get("attempts")):
            reject("svg_partial_recovery_local_output_requires_repair")
        episode_admissions = ({admissions[0].id} if prior_lineage else {item.id for item in admissions})
        unknown_calls = [call for call in calls if call.slide_index == row.slide_index
            and call.admission_id in episode_admissions
            and (call.outcome == "unknown" or call.state in {"reserved", "uncertain"}
                 or call.actual_cost_microunits is None and call.outcome != "succeeded")]
        is_unknown = not undispatched and completed is None and (
            _unknown_provider(payload) or payload.get("inflight") is not None or bool(unknown_calls))
        if is_unknown:
            category = "unknown"
        elif row.author_status == "completed" and isinstance(svg, str) and svg:
            category = "known"
        elif undispatched or (not payload.get("attempts") and not svg and not payload.get("provider_terminal_responses")
              and not any(call.slide_index == row.slide_index and call.stage == "author" for call in calls)
              and row.render_status == "pending" and row.persist_status == "pending"):
            category = "unstarted"
        else:
            # A response.completed raw-only output is a known local failure,
            # never evidence that another provider call may be purchased.
            reject("svg_partial_recovery_local_output_requires_repair")
        witness = {"slide_index": row.slide_index, "category": category,
                   "row_digest": digest(_dump(row))}
        if category == "known":
            attempts = payload.get("attempts") or []
            receipt = attempts[-1].get("receipt") or {} if attempts else {}
            raw = attempts[-1].get("raw_response") if attempts else None
            good_calls = [call for call in calls if call.slide_index == row.slide_index
                and call.stage == "author" and call.state in {"reported", "unavailable"}
                and call.outcome == "succeeded" and call.reconciled_at is not None]
            quality = payload.get("quality") or {}
            png_digest = payload.get("png_sha256")
            png = _read_png(payload)
            if (not good_calls and completed is None or not attempts or not isinstance(raw, str)
                    or attempts[-1].get("kind") != "provider"
                    or receipt.get("contract_version") not in {"svg-static/v1", "svg-static/v2"}
                    or not receipt.get("provider") or not receipt.get("requested_model")
                    or receipt.get("style_digest") != job.svg_style_snapshot.get("digest")
                    or receipt.get("authoring_profile") != job.svg_style_snapshot.get("authoring_profile")
                    or receipt.get("validation_error") is not None
                    or str(receipt.get("raw_digest", "")).removeprefix("sha256:") != sha256_hex(raw.encode())
                    or completed is not None and completed.get("raw_response") != raw
                    or not payload.get("input_digest")):
                reject("svg_partial_recovery_local_output_requires_repair"
                       if completed is not None else "svg_partial_recovery_known_output_unproven")
            if good_calls and not any(call.provider_id == receipt["provider"]
                    and call.model_id == receipt["requested_model"] for call in good_calls):
                reject("svg_partial_recovery_author_model_unbound")
            service = configured_public_artifact_service()
            pointer = ArtifactPointer.model_validate(payload.get("source_manifest_ref"))
            reference = await service.resolve_ref(session, artifact_id=pointer.ref,
                owner_id=owner, tenant_id=tenant, expected_digest=pointer.sha256)
            asset = await load_source_manifest_pointer(session, service,
                pointer=payload.get("source_manifest_ref"), owner_id=owner, tenant_id=tenant)
            current_binding = {"pointer": deepcopy(payload.get("source_manifest_ref")),
                "digest": asset.digest, "presentation_id": str(asset.presentation_id),
                "source_revision": asset.source_revision,
                "origin_operation_id": str(reference.created_by_operation_id)}
            if asset.digest != payload.get("source_manifest_digest") or binding not in (None, current_binding):
                reject("svg_partial_recovery_asset_binding_changed")
            binding = current_binding
            try:
                transform_witness = _author_render_chain(payload, raw, receipt,
                    request=request, job=job, asset=asset, index=row.slide_index,
                    row=row, completed=completed, vectors=vector_maps[row.slide_index],
                    vector_seed_witness=vector_seed_witness)
            except PartialRecoveryRejected:
                raise
            except (ValueError, TypeError, KeyError) as error:
                raise PartialRecoveryRejected("svg_partial_recovery_render_transform_unproven") from error
            if source is not None:
                verify_source_receipt(svg, payload.get("source_preservation"), source, row.slide_index)
            witness.update(svg_sha256=transform_witness["render_svg_sha256"],
                source_svg_sha256=sha256_hex(svg.encode()),
                final_render_svg_sha256=transform_witness["render_svg_sha256"],
                phase=transform_witness.get("phase", "finalized"), png_sha256=png_digest,
                historical_png_verified=png is not None and sha256_hex(png) == png_digest,
                historical_geometry_passed=quality.get("passed") is True and quality.get("measurement_complete") is True,
                author_render_chain=transform_witness,
                input_digest=payload["input_digest"], author_receipt_digest=author_witness(payload),
                source_font_digest=digest((payload.get("source_preservation") or {}).get("font_profile")))
        pages.append(witness)
    if binding is None:
        reject("svg_partial_recovery_known_source_required")
    deck_plans = list(await session.scalars(select(AuthoredDeckPlanCheckpoint).where(
        AuthoredDeckPlanCheckpoint.generation_job_id == job.id).order_by(
        AuthoredDeckPlanCheckpoint.id).with_for_update()))
    from services.public_operation_admission import load_public_policy, _policy_digest

    policy = await load_public_policy(session)
    if policy is None or not policy.enabled:
        reject("svg_partial_recovery_policy_unavailable")
    from services.svg_authoring_profile import read_quality_recovery_plan, svg_quality_review_admission_budget

    review_calls = svg_quality_review_admission_budget(request, job.total_slides)
    author_calls = sum(page["category"] != "known" for page in pages)
    quality_plan = read_quality_recovery_plan(job.svg_style_snapshot)
    repair_calls = (job.total_slides if quality_plan and quality_plan["review_round_limit"] == 2
                    else author_calls)
    storage_seals = await _payload_storage_seals(session, [row.id for row in ancestors])
    archive = {"schema": ARCHIVE_V2, "operation_refs": [
        _operation_reference(row, storage_seals.get(str(row.id)), tenant=tenant) for row in ancestors],
        "job": _dump(job), "task": _dump(task), "checkpoints": [_dump(row) for row in rows],
        "admissions": [_dump(row) for row in admissions], "logical_calls": [_dump(row) for row in calls],
        "usage": [_dump(row) for row in usage], "resource_leases": [_dump(row) for row in leases],
        "runtime_budgets": [_dump(row) for row in budgets],
        "runtime_reservations": [_dump(row) for row in reservations],
        "deck_plans": [_dump(row) for row in deck_plans],
        "source_record": _dump(record) if record is not None else None}
    # Missing actual cost has no finite upper bound in the historical ledger.
    uncertain_cost = (any(call.actual_cost_microunits is None for call in calls)
        or any(item.actual_cost_unavailable_count for item in admissions)
        or any(item.state == "uncertain" and item.actual_cost is None for item in reservations))
    remaining = None
    used_estimate = used_actual = 0
    for admission in admissions:
        related = [call for call in calls if call.admission_id == admission.id]
        used_estimate += max(sum(call.estimated_cost_microunits for call in related),
            admission.estimated_consumed_microunits + admission.estimated_reserved_microunits)
        if not uncertain_cost:
            used_actual += max(sum(call.actual_cost_microunits for call in related),
                admission.actual_consumed_microunits + admission.actual_reserved_microunits)
            bound = min(admission.estimated_cost_limit_microunits - used_estimate,
                        admission.actual_cost_limit_microunits - used_actual)
            remaining = bound if remaining is None else min(remaining, bound)
    manifest = {"recovery_revision": REVISION, "operation_id": str(operation.id),
        "source_operation_id": str(ancestors[-1].id), "operation_version": operation.version,
        "presentation_id": str(operation.presentation_id), "presentation_revision": presentation_revision,
        "owner_id": str(owner), "tenant_id": tenant, "job_id": str(job.id),
        "expected_page_count": job.total_slides, "archive_digest": digest(archive),
        "archive_schema": ARCHIVE_V2,
        "frozen_request_digest": digest(_frozen_payload(job.request_payload)),
        "frozen_style_digest": digest(job.svg_style_snapshot),
        "canvas_digest": digest(request_canvas_metadata(request)),
        "source_pin_digest": digest(pin.model_dump(mode="json")) if pin is not None else None,
        "source_manifest_digest": source.manifest_digest if source is not None else binding["digest"],
        "source_asset_binding": binding,
        "vector_offer_binding": vector_seed_witness,
        "code_digest": _code_digest(), "pages": pages,
        "prospective_policy_version": policy.version, "prospective_policy_digest": _policy_digest(policy),
        "known_indices": [page["slide_index"] for page in pages if page["category"] == "known"],
        "new_indices": [page["slide_index"] for page in pages if page["category"] == "unstarted"],
        "unknown_indices": [page["slide_index"] for page in pages if page["category"] == "unknown"],
        "historical_actual_cost_unbounded": uncertain_cost,
        "proven_remaining_budget_microunits": max(0, remaining or 0) if not uncertain_cost else None,
        "additional_budget_limit_microunits": min(policy.estimated_cost_limit_microunits, policy.actual_cost_limit_microunits),
        "prospective_author_request_count": author_calls,
        "prospective_initial_request_limit": author_calls + review_calls,
        # The standard generation plan already permits one correction per
        # slide; policy.retry_request_limit is an additional transient pool.
        # An active two-review plan owns one repair round across the entire
        # deck, even when every initial author output is already reusable.
        "prospective_retry_request_limit": repair_calls}
    return PartialRecovery(manifest, archive, rows, digest(manifest))


async def verify_action(session, operation, request, *, presentation_revision):
    if (request.action != "retry" or request.recovery_revision != REVISION
            or request.expected_operation_version != operation.version
            or request.expected_revision != presentation_revision):
        reject("svg_partial_recovery_cas_required")
    decision = request.recovery_decision
    if decision is None:
        reject("svg_partial_recovery_explicit_decision_required")
    recovery = await build_manifest(session, operation, presentation_revision=presentation_revision)
    if decision.manifest_hash != recovery.manifest_hash:
        reject("svg_partial_recovery_manifest_changed")
    if decision.nonce != decision_nonce(recovery.manifest_hash):
        reject("svg_partial_recovery_nonce_invalid")
    if sorted(decision.acknowledged_unknown_indices) != recovery.manifest["unknown_indices"]:
        reject("svg_partial_recovery_unknown_ack_required")
    if decision.additional_budget_microunits is None:
        if recovery.manifest["historical_actual_cost_unbounded"]:
            reject("svg_partial_recovery_additional_budget_required")
        recovery.approved_budget = recovery.manifest["proven_remaining_budget_microunits"]
    else:
        recovery.approved_budget = decision.additional_budget_microunits
        if recovery.approved_budget > recovery.manifest["additional_budget_limit_microunits"]:
            reject("svg_partial_recovery_budget_exceeds_policy")
    if not recovery.approved_budget or recovery.approved_budget < 1:
        reject("svg_partial_recovery_budget_unavailable")
    return recovery


def cached_page_payload(row, recovery, operation_id):
    page = recovery.manifest["pages"][row.slide_index]
    marker = {"schema": "svg-partial-recovery-page/v1", "operation_id": str(operation_id),
        "manifest_hash": recovery.manifest_hash, "slide_index": row.slide_index,
        "source_row_digest": page["row_digest"], "category": page["category"]}
    if page["category"] != "known":
        return {"partial_recovery": marker}
    payload = deepcopy(row.svg_checkpoint)
    for key in ("quality", "initial_quality", "browser_metrics", "png_sha256", "png_path",
                "terminal", "quality_recovery_local_evidence", "quality_receipt", "qualityReceipt",
                "cache_only_remeasurement", "append_only_cache", "cached_author_render",
                "cached_author_render_archive", "classification_remeasurement_archive",
                "last_valid_svg", "quality_recovery", "quality_recovery_fallback",
                "quality_recovery_execution",
                "local_readability_repair", "repair_hint", "repair_input_page_evidence",
                "provider_terminal_responses", "provider_dispatch_stopped"):
        payload.pop(key, None)
    payload["inflight"] = None
    if isinstance(payload.get("source_preservation"), dict):
        payload["source_preservation"]["rendering_verified"] = False
    marker.update(svg_sha256=page["svg_sha256"], input_digest=page["input_digest"])
    marker.update(phase=page.get("phase", "finalized"),
        source_svg_sha256=page.get("source_svg_sha256", page["svg_sha256"]),
        final_render_svg_sha256=page.get("final_render_svg_sha256", page["svg_sha256"]))
    payload["partial_recovery"] = marker
    return payload


async def rebind_episode(session, source, child, recovery, *, key_hash):
    from models.sql.async_presentation_generation_status import AsyncPresentationGenerationTaskModel
    from services.presentation_generation_job_service import rebind_workspace_linked_generation_job_for_retry, request_sha256
    if (digest(recovery.archive) != recovery.manifest["archive_digest"]
            or recovery.archive.get("schema") != ARCHIVE_V2
            or recovery.manifest.get("archive_schema") != ARCHIVE_V2):
        reject("svg_partial_recovery_archive_changed")
    child.request_payload = {**child.request_payload, ARCHIVE_KEY: deepcopy(recovery.archive),
        "svg_partial_recovery_manifest": deepcopy(recovery.manifest),
        "svg_partial_recovery_manifest_hash": recovery.manifest_hash}
    session.add(child)
    await session.flush()
    job = await session.get(PresentationGenerationJob, uuid.UUID(recovery.manifest["job_id"]), with_for_update=True)
    if job is None or digest(_dump(job)) != digest(recovery.archive["job"]):
        reject("svg_partial_recovery_episode_conflict")
    # Install a fresh task before the established helper projects queued state;
    # the historical task is never updated, even within this transaction.
    task = AsyncPresentationGenerationTaskModel(status="pending", owner_id=source.owner_id)
    session.add(task)
    await session.flush()
    job.task_id = task.id
    session.add(job)
    await session.flush()
    if not await rebind_workspace_linked_generation_job_for_retry(session, source.id, child.id,
            expected_job_id=job.id, expected_presentation_id=source.presentation_id):
        reject("svg_partial_recovery_episode_conflict")
    lineage = {"schema": "svg-partial-recovery-lineage/v1",
        "operation_id": str(child.id), "source_operation_id": recovery.manifest["source_operation_id"],
        "action_operation_id": str(source.id), "job_id": str(job.id),
        "presentation_id": str(child.presentation_id), "manifest_hash": recovery.manifest_hash,
        "archive_schema": ARCHIVE_V2,
        **{key: recovery.manifest[key] for key in ("archive_digest", "known_indices", "new_indices",
            "unknown_indices", "frozen_request_digest", "frozen_style_digest")}}
    job.task_id = task.id
    job.request_payload = {**_frozen_payload(deepcopy(recovery.archive["job"]["request_payload"])), LINEAGE_KEY: lineage}
    job.request_sha256 = request_sha256(job.request_payload)
    # Keep the original compatibility key occupied. The child action has its
    # own operation/action key; old async keys must never become purchasable.
    job.completed_slides = 0
    for row in recovery.rows:
        row.svg_checkpoint = cached_page_payload(row, recovery, child.id)
        known = row.slide_index in recovery.manifest["known_indices"]
        row.author_status = "completed" if known else "pending"
        row.illustrate_status = "skipped"
        row.render_status = row.persist_status = "pending"
        for field in ("author_html", "illustrated_html", "render_path", "render_sha256", "artifact_refs",
                "failed_stage", "error", "attempt_token", "failed_at", "author_started_at", "author_completed_at",
                "illustrate_started_at", "illustrate_completed_at", "render_started_at", "render_completed_at",
                "persist_started_at", "persist_completed_at"):
            setattr(row, field, None)
        session.add(row)
    child.request_payload = {**child.request_payload, "job_id": str(job.id), "task_id": task.id,
        "generation_request": deepcopy(job.request_payload), "svg_partial_recovery_lineage": lineage}
    session.add_all((child, job))
    return job


async def verify_episode(session, operation, job):
    from models.sql.async_presentation_generation_status import AsyncPresentationGenerationTaskModel
    from models.sql.source_content import SourceContentManifestRecord
    manifest = operation.request_payload.get("svg_partial_recovery_manifest") or {}
    archive = operation.request_payload.get(ARCHIVE_KEY) or {}
    lineage = job.request_payload.get(LINEAGE_KEY) or {}
    archive_schema = archive.get("schema")
    if archive_schema == ARCHIVE_V1:
        if manifest.get("archive_schema") not in (None, ARCHIVE_V1) or lineage.get("archive_schema") not in (None, ARCHIVE_V1):
            reject("svg_partial_recovery_archive_schema_invalid")
    elif archive_schema == ARCHIVE_V2:
        if (manifest.get("archive_schema") != ARCHIVE_V2 or lineage.get("archive_schema") != ARCHIVE_V2
                or "operations" in archive):
            reject("svg_partial_recovery_archive_schema_invalid")
    else:
        reject("svg_partial_recovery_archive_schema_invalid")
    if (operation.request_payload.get("recovery_revision") != REVISION
            or digest(manifest) != operation.request_payload.get("svg_partial_recovery_manifest_hash")
            or digest(archive) != manifest.get("archive_digest")
            or lineage != operation.request_payload.get("svg_partial_recovery_lineage")
            or lineage.get("schema") != "svg-partial-recovery-lineage/v1"
            or lineage.get("source_operation_id") != manifest.get("source_operation_id")
            or lineage.get("action_operation_id") != manifest.get("operation_id")
            or any(lineage.get(key) != manifest.get(key) for key in ("archive_digest",
                "known_indices", "new_indices", "unknown_indices", "frozen_request_digest", "frozen_style_digest"))
            or lineage.get("manifest_hash") != digest(manifest)
            or lineage.get("operation_id") != str(operation.id)
            or str(operation.retry_of_operation_id) != manifest.get("operation_id")
            or operation.owner_id is None or str(operation.owner_id) != manifest.get("owner_id")
            or operation.request_payload.get("principal_tenant_id") != manifest.get("tenant_id")
            or str(operation.presentation_id) != manifest.get("presentation_id")
            or job.workspace_operation_id != operation.id or job.presentation_id != operation.presentation_id
            or str(job.id) != manifest.get("job_id")
            or digest(_frozen_payload(job.request_payload)) != manifest.get("frozen_request_digest")
            or digest(job.svg_style_snapshot) != manifest.get("frozen_style_digest")
            or _code_digest() != manifest.get("code_digest")):
        reject("svg_partial_recovery_episode_unbound")
    if archive_schema == ARCHIVE_V2:
        await _verify_operation_references(session, operation, manifest, archive.get("operation_refs"))
    operation_snapshots = (("operations", WorkspaceOperation),) if archive_schema == ARCHIVE_V1 else ()
    for key, model in (*operation_snapshots, ("admissions", PublicOperationAdmission),
            ("logical_calls", PublicProviderLogicalCall), ("usage", ProviderUsageReceipt),
            ("resource_leases", PublicResourceLease), ("runtime_budgets", ProviderRuntimeBudget),
            ("runtime_reservations", ProviderRuntimeReservation), ("deck_plans", AuthoredDeckPlanCheckpoint)):
        snapshots = archive.get(key, [])
        if not snapshots:
            continue
        originals = {str(row.id): row for row in await session.scalars(select(model).where(
            model.id.in_([uuid.UUID(item["id"]) for item in snapshots])))}
        for snapshot in snapshots:
            original = originals.get(snapshot["id"])
            if original is None or digest(_dump(original)) != digest(snapshot):
                reject("svg_partial_recovery_history_changed")
    historical_task = await session.get(AsyncPresentationGenerationTaskModel, archive.get("task", {}).get("id"))
    if historical_task is None or digest(_dump(historical_task)) != digest(archive.get("task")):
        reject("svg_partial_recovery_history_changed")
    source_record = archive.get("source_record")
    if source_record is not None:
        original = await session.get(SourceContentManifestRecord, source_record["manifest_id"])
        if original is None or digest(_dump(original)) != digest(source_record):
            reject("svg_partial_recovery_source_changed")
    return manifest


async def runtime_admission(session, *, job_id, operation_id,
                            expected_attempt_token=None, expected_fencing_token=None):
    """Fail closed before spend; retain the actual generation job identity."""
    from models.sql.workspace import get_workspace_owner_scope
    from services.public_operation_admission import _require_phase2_provider_contract
    from utils.owner_context import get_current_owner_id, get_current_tenant_id

    job = await session.get(PresentationGenerationJob, job_id)
    if job is None or not (job.request_payload or {}).get(LINEAGE_KEY):
        return None
    operation = await session.get(WorkspaceOperation, operation_id)
    admission = await session.scalar(select(PublicOperationAdmission).where(
        PublicOperationAdmission.operation_id == operation_id,
        PublicOperationAdmission.owner_id == get_current_owner_id(),
        PublicOperationAdmission.tenant_id == get_current_tenant_id(),
        PublicOperationAdmission.state == "active"))
    if (operation is None or admission is None or operation.kind != "presentation.generate"
            or operation.state != "running" or operation.owner_scope != get_workspace_owner_scope()
            or operation.owner_id != admission.owner_id or job.workspace_operation_id != operation.id
            or operation.cancel_requested_at is not None or job.state not in {"running", "staging"}
            or not expected_attempt_token or expected_fencing_token is None):
        reject("svg_partial_recovery_runtime_unbound")
    await verify_episode(session, operation, job)
    manifest = operation.request_payload["svg_partial_recovery_manifest"]
    decision = operation.request_payload.get("recovery_decision") or {}
    approved = decision.get("additional_budget_microunits")
    if approved is None:
        approved = manifest.get("proven_remaining_budget_microunits")
    if (not isinstance(approved, int) or approved < 1
            or decision.get("manifest_hash") != digest(manifest)
            or decision.get("nonce") != decision_nonce(digest(manifest))
            or decision.get("acknowledged_unknown_indices") != manifest.get("unknown_indices")
            or max(admission.estimated_cost_limit_microunits, admission.actual_cost_limit_microunits) > approved
            or admission.planned_request_limit > manifest["prospective_initial_request_limit"]
            or admission.retry_request_limit > manifest["prospective_retry_request_limit"]):
        reject("svg_partial_recovery_runtime_budget_unbound")
    await _require_phase2_provider_contract(session, admission,
        expected_attempt_token=expected_attempt_token, expected_fencing_token=expected_fencing_token)
    return admission


def verify_cached_page(payload, manifest, index, input_digest):
    page = manifest["pages"][index]
    marker = payload.get("partial_recovery") or {}
    if (marker.get("schema") != "svg-partial-recovery-page/v1"
            or marker.get("manifest_hash") != digest(manifest)
            or marker.get("slide_index") != index or marker.get("source_row_digest") != page["row_digest"]
            or marker.get("category") != page["category"]):
        reject("svg_partial_recovery_page_unbound")
    phase = marker.get("phase", "finalized")
    expected_svg = page.get("svg_sha256")
    if page.get("phase") == "author-only":
        if phase not in {"author-only", "finalized"}:
            reject("svg_partial_recovery_cached_phase_invalid")
        if (marker.get("source_svg_sha256") != page["source_svg_sha256"]
                or marker.get("final_render_svg_sha256") != page["final_render_svg_sha256"]):
            reject("svg_partial_recovery_cached_phase_invalid")
        expected_svg = page["source_svg_sha256"] if phase == "author-only" else page["final_render_svg_sha256"]
        if phase == "author-only" and (payload.get("quality") not in (None, {})
                or payload.get("png_sha256") is not None or payload.get("png_path") is not None):
            reject("svg_partial_recovery_cached_phase_invalid")
    elif phase != "finalized":
        reject("svg_partial_recovery_cached_phase_invalid")
    if page["category"] == "known" and (
            payload.get("input_digest") != input_digest or input_digest != page.get("input_digest")
            or sha256_hex(str(payload.get("svg_content", "")).encode()) != expected_svg
            or author_witness(payload) != page["author_receipt_digest"]):
        reject("svg_partial_recovery_cached_source_changed")
    return page


async def bind_admission(session, operation, recovery):
    admission = await session.scalar(select(PublicOperationAdmission).where(
        PublicOperationAdmission.operation_id == operation.id).with_for_update())
    if (admission is None or admission.state != "queued"
            or admission.initial_request_count or admission.retry_request_count
            or admission.estimated_reserved_microunits or admission.actual_reserved_microunits
            or admission.policy_version != recovery.manifest["prospective_policy_version"]
            or admission.policy_digest != recovery.manifest["prospective_policy_digest"]
            or admission.slide_count != recovery.manifest["expected_page_count"]):
        reject("svg_partial_recovery_admission_conflict")
    # A separate, explicit episode cap never raises the normal policy hard cap.
    admission.planned_request_limit = min(admission.planned_request_limit,
        recovery.manifest["prospective_initial_request_limit"])
    admission.retry_request_limit = min(admission.retry_request_limit,
        recovery.manifest["prospective_retry_request_limit"])
    admission.estimated_cost_limit_microunits = min(admission.estimated_cost_limit_microunits, recovery.approved_budget)
    admission.actual_cost_limit_microunits = min(admission.actual_cost_limit_microunits, recovery.approved_budget)
    session.add(admission)
