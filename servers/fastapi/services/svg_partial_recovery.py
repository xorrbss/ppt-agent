"""Owner-bound, explicit recovery episodes for an existing SVG presentation.

The compatibility job is unique per presentation. New episodes fork a staging
presentation and job, preserving producer rows and their identities. Historical
in-place episodes remain verifiable, but are never created again.
"""
from __future__ import annotations

from copy import deepcopy
from collections import OrderedDict
from dataclasses import dataclass, field
from pathlib import Path, PurePath
from threading import RLock
from types import SimpleNamespace
from typing import Any, get_args
from weakref import ReferenceType, ref
import hashlib
import time
import uuid

from sqlalchemy import JSON, Text, case, cast, column, func, literal, or_, select, text as sql_text, true
from sqlalchemy.orm import load_only

from models.sql.authored_slide_checkpoint import AuthoredSlideCheckpoint
from models.sql.presentation_generation_job import PresentationGenerationJob
from models.sql.public_admission import AuthoredDeckPlanCheckpoint, PublicOperationAdmission, PublicProviderLogicalCall, PublicResourceLease
from models.sql.provider_runtime_reservation import ProviderRuntimeBudget, ProviderRuntimeReservation
from models.sql.provider_usage_receipt import ProviderUsageReceipt
from models.sql.workspace import WorkspaceOperation
from services.svg_recovery_witness import author_witness, digest, unknown_provider as _unknown_provider
from services.svg_timeout_repurchase import _live
from utils.datetime_utils import get_current_utc_datetime
from utils.code_file_digest import code_file_digest
from utils.hashing import sha256_hex

REVISION = "svg-partial-recovery/v1"
LINEAGE_KEY = "_svg_partial_recovery"
ARCHIVE_KEY = "_svg_partial_recovery_archive"
ARCHIVE_V1 = "svg-partial-recovery-archive/v1"
ARCHIVE_V2 = "svg-partial-recovery-archive/v2"
ARCHIVE_V3 = "svg-partial-recovery-archive/v3"
CHECKPOINT_REFERENCE = "svg-partial-recovery-checkpoint-ref/v1"
OPERATION_REFERENCE = "svg-partial-recovery-operation-ref/v1"
PAYLOAD_HASH_ALGORITHM = "sha256/postgresql-json-text-utf8/v1"

# A successful preparation proves historical producers once for this worker
# attempt. Provider calls check the live consumer, not its entire ancestry.
_PREPARED_EPISODES = OrderedDict()
_PREPARED_EPISODES_LOCK = RLock()
_RUNTIME_OPERATION_FIELDS = (
    "id", "kind", "owner_id", "owner_scope", "presentation_id",
    "retry_of_operation_id", "contract_version", "execution_profile_version",
    "attempt_token", "fencing_token", "deadline_at",
)
_RUNTIME_PAYLOAD_FIELDS = (
    "principal_tenant_id", "recovery_revision", "svg_partial_recovery_manifest",
    "svg_partial_recovery_manifest_hash", "svg_partial_recovery_lineage",
    "recovery_decision", "job_id",
)


def _runtime_episode_key(operation, job):
    from utils.owner_context import get_current_owner_id, get_current_tenant_id
    return (str(get_current_owner_id()), str(get_current_tenant_id()),
            str(operation.id), str(job.id), operation.attempt_token, operation.fencing_token)


def _runtime_episode_digest(operation, job):
    # These projections contain no archive, historical checkpoint, or SDK body.
    return digest({
        "operation": {key: str(getattr(operation, key, None)) for key in _RUNTIME_OPERATION_FIELDS},
        "payload": {key: operation.request_payload.get(key) for key in _RUNTIME_PAYLOAD_FIELDS},
        "job": {"id": str(job.id), "operation_id": str(job.workspace_operation_id),
                "presentation_id": str(job.presentation_id), "attempt_token": job.attempt_token,
                "total_slides": job.total_slides, "request": job.request_payload,
                "style": job.svg_style_snapshot, "outline": job.authored_outline},
        "code": _code_digest(),
    })


def _remember_prepared_episode(operation, job):
    if (operation.state != "running" or job.state not in {"running", "staging"}
            or not (job.request_payload or {}).get(LINEAGE_KEY)
            or not operation.attempt_token or job.attempt_token != operation.attempt_token):
        return
    key, fingerprint = _runtime_episode_key(operation, job), _runtime_episode_digest(operation, job)
    with _PREPARED_EPISODES_LOCK:
        _PREPARED_EPISODES[key] = fingerprint
        _PREPARED_EPISODES.move_to_end(key)
        while len(_PREPARED_EPISODES) > 128:
            _PREPARED_EPISODES.popitem(last=False)


def _require_prepared_episode(operation, job):
    key = _runtime_episode_key(operation, job)
    with _PREPARED_EPISODES_LOCK:
        expected = _PREPARED_EPISODES.get(key)
    if expected is None:
        reject("svg_partial_recovery_runtime_not_prepared")
    if expected != _runtime_episode_digest(operation, job):
        reject("svg_partial_recovery_request_snapshot_changed")


async def _runtime_episode_operation(session, operation_id):
    # Extract only current authority from JSON in PostgreSQL. Loading the ORM
    # request_payload here would decode the complete historical archive again.
    statement = select(
        *(getattr(WorkspaceOperation, key) for key in _RUNTIME_OPERATION_FIELDS),
        WorkspaceOperation.state, WorkspaceOperation.cancel_requested_at,
        *(WorkspaceOperation.request_payload[key].label("payload_" + key)
          for key in _RUNTIME_PAYLOAD_FIELDS),
    ).where(WorkspaceOperation.id == operation_id)
    row = (await session.execute(statement)).mappings().one_or_none()
    if row is None:
        return None
    return SimpleNamespace(
        **{key: row[key] for key in (*_RUNTIME_OPERATION_FIELDS, "state", "cancel_requested_at")},
        request_payload={key: row["payload_" + key] for key in _RUNTIME_PAYLOAD_FIELDS},
    )


def _recovery_review_allocation(request, snapshot, slide_count):
    """Keep regular offsets separate from an already admitted v2 final budget."""
    from services.svg_authoring_profile import (
        global_format_retry_control, read_quality_recovery_execution_plan,
        read_quality_recovery_plan, read_quality_recovery_policy,
        svg_quality_review_admission_budget,
    )
    from services.svg_final_selected_continuation import final_review_control

    policy = read_quality_recovery_policy(snapshot)
    format_control = global_format_retry_control(snapshot)
    if policy and policy["version"] == 2:
        execution = read_quality_recovery_execution_plan(snapshot)
        return (execution["review_call_limit"], format_control,
                deepcopy(execution.get("final_selected_review")))
    review_calls = svg_quality_review_admission_budget(request, slide_count)
    if format_control is not None:
        review_calls = format_control["review_call_limit"]
    final_control = (final_review_control(slide_count, physical_offset=review_calls)
                     if read_quality_recovery_plan(snapshot) is not None else None)
    return review_calls, format_control, final_control


_POST_ANCHOR_CODE_FILES = (
    "services/generation_core/assets/vector_offer.py",
    "services/generation_core/assets/vector_illustration.py",
    "services/svg_templates/generation.py",
    "services/svg_templates/body_composition.py",
    "services/svg_templates/picture_policy.py",
    "services/generation_core/assets/infographic_plan.py",
    "services/generation_core/assets/infographic_assets.py",
    "utils/llm_calls/draw_vector_illustrations.py",
    "services/generation_core/drivers/svg/native_chart_evidence.py",
    "services/generation_core/drivers/svg/native_chart.py",
    "services/generation_core/drivers/svg/native_requirements.py",
    "services/generation_core/drivers/svg/prose_chart_literal.py",
    "services/generation_core/drivers/svg/native_geometry.py",
    "utils/svg_uniform_transform.py",
    "services/generation_core/drivers/svg/chart_content_cartesian.py",
    "services/generation_core/drivers/svg/chart_content_polar.py",
    "services/generation_core/drivers/svg/chart_content_paint.py",
    "services/generation_core/drivers/svg/native_chart_visual.py",
    "services/generation_core/drivers/svg/native_paint.py",
    "services/generation_core/drivers/svg/native_publish.py",
    "services/generation_core/drivers/svg/native_table.py",
    "services/generation_core/drivers/svg/export_chart_style.py",
    "services/generation_core/drivers/svg/fixed_axis_migration.py",
    "services/generation_core/drivers/svg/fallback.py",
    "services/generation_core/drivers/svg/source_paint_occlusion.py",
    "services/generation_core/drivers/svg/source_classification_association.py",
    "services/generation_core/native_objects/__init__.py",
    "services/generation_core/native_objects/model.py",
    "services/generation_core/native_objects/payload.py",
    "utils/slide_capture_chart_adapter.py",
    "utils/slide_capture_chart_paint.py",
    "utils/slide_capture_chart_text.py",
    "utils/slide_capture_chart_support.py",
    "utils/slide_capture_worker_client.py",
    "utils/renderer_isolation.py",
    "utils/authored_renderer_worker.py",
    "utils/authored_renderer_session.py",
    "utils/renderer_worker_policy.py",
    "utils/slide_capture_pipe_render.py",
    "services/svg_source_preset_shapes.py",
    "services/source_content/pptx_custom_geometry.py",
    "vendor/slide_master/pptx_shapes/__init__.py",
    "vendor/slide_master/pptx_shapes/errors.py",
    "vendor/slide_master/pptx_shapes/formula.py",
    "vendor/slide_master/pptx_shapes/loader.py",
    "vendor/slide_master/pptx_shapes/models.py",
    "vendor/slide_master/pptx_shapes/registry.py",
    "vendor/slide_master/pptx_shapes/semantic_hash.py",
    "vendor/slide_master/pptx_shapes/xml_safety.py",
    "vendor/slide_master/pptx_shapes/data/presetShapeDefinitions.xml",
    "vendor/slide_master/pptx_shapes/data/shape_type_values.txt",
    "utils/slide_capture_glyph_probe.py",
    "utils/slide_capture_local_fit.py",
    "utils/authored_dom_quality.py",
    "utils/authored_fit_edits.py",
    "utils/authored_qid_namespace.py",
    "utils/authored_dom_tags.py",
    "utils/slide_capture_chart_timing.py",
)

CODE_FILES = (
    "services/svg_partial_recovery.py",
    "services/svg_source_local_review_continuation.py",
    "services/generation_core/drivers/svg/collect.py",
    "services/generation_core/drivers/svg/author.py",
    "services/generation_core/drivers/svg/attempt_cause.py",
    "services/generation_core/drivers/svg/render.py",
    "services/generation_core/drivers/svg/quality.py",
    "services/generation_core/drivers/svg/quality_recovery.py",
    "services/generation_core/quality/vision_advisory.py",
    "services/generation_core/quality/vision_advisory_prompt.py",
    "services/generation_core/quality/review_coverage.py",
    "services/svg_source_layout.py",
    "services/svg_source_native_tables.py",
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
    "services/svg_common_header.py",
    "services/svg_common_header_generation.py",
    "services/svg_common_header_design_checkpoint.py",
    "services/svg_common_header_titles.py",
    "services/svg_common_header_title_budget.py",
    "services/svg_generation_inputs.py",
    "services/svg_presentation_checkpoint.py",
    "services/svg_generation_assets.py",
    "services/svg_default_fonts.py",
    "services/svg_contract.py",
    "utils/svg_validation.py",
    "services/svg_renderer.py",
    "utils/svg_template_fonts.py",
    "services/authored_vnext/semantic/typography.py",
    "services/svg_text_advance.py",
    "services/svg_text_overlap.py",
    "services/svg_master_furniture.py",
    "services/generation_core/drivers/svg/native_declarations.py",
    "services/generation_core/drivers/svg/asset_slots.py",
    "services/generation_core/drivers/svg/embed_assets.py",
    "services/generation_core/drivers/svg/icon_flatten.py",
    "services/generation_core/drivers/svg/icon_geometry.py",
    "services/generation_core/assets/offer.py",
    *_POST_ANCHOR_CODE_FILES,
    "utils/authored_image_slots.py",
    "utils/codex_stream_adapter.py",
    "utils/llm_calls/prepare_svg_common_header_titles.py",
    "utils/ppt_writing_prompt.py",
    "services/ppt_writing_policy.py",
    "services/ppt_writing_visible.py",
    "services/svg_recovery_source_evidence.py",
    "services/generation_core/authoring/author_slide_svg.py",
    "services/svg_author_output_view.py",
    "services/generation_core/authoring/source_id_alias.py",
    "services/svg_text_defaults.py",
    "services/generation_core/authoring/author_source_svg_layout.py",
    "models/presentation_outline_model.py",
    "utils/slide_capture_result.py",
    "utils/slide_capture.py",
    "utils/slide_capture_text_ink.py",
)


class PartialRecoveryRejected(ValueError):
    retryable = False

    def __init__(self, code):
        super().__init__(code)
        self.code = code


def reject(code="svg_partial_recovery_unproven"):
    if code == "svg_partial_recovery_request_snapshot_changed":
        import logging
        import sys

        caller = sys._getframe(1)
        logging.getLogger(__name__).warning(
            "SVG partial recovery snapshot guard rejected: function=%s line=%s",
            caller.f_code.co_name, caller.f_lineno)
    raise PartialRecoveryRejected(code)


def _dump(row):
    return row.model_dump(mode="json")


def _fresh_producer_checkpoint_digest(row):
    """Only for finish()'s freshly decoded, unexposed CP Core-select rows."""
    if type(row) is AuthoredSlideCheckpoint and type(row.svg_checkpoint) is dict:
        envelope = row.model_dump(mode="json", exclude={"svg_checkpoint"})
        return digest(envelope | {"svg_checkpoint": row.svg_checkpoint})
    return digest(_dump(row))


def _operation_control(row):
    return row.model_dump(mode="json", exclude={"request_payload"})


def _operation_projection(row):
    """Control plus the live payload in _dump() key order; digest() sorts keys.

    finish() compares this against a fresh _dump(row): matching key order lets
    its marshal hint succeed instead of walking the whole payload in Python.
    """
    control = _operation_control(row)
    fields = getattr(type(row), "model_fields", None) or ()
    projection = {key: row.request_payload if key == "request_payload" else control[key]
                  for key in fields if key == "request_payload" or key in control}
    if projection.keys() != control.keys() | {"request_payload"}:
        return {**control, "request_payload": row.request_payload}
    return projection


def _sha256_hex_valid(value):
    return isinstance(value, str) and len(value) == 64 and all(char in "0123456789abcdef" for char in value)


def _same_json_value(left, right):
    """Compare finite JSON exactly; unsupported graphs take the original digest path."""
    import math
    pending = [(left, right, False)]
    left_active, right_active = set(), set()
    while pending:
        expected, actual, leaving = pending.pop()
        kind = type(expected)
        if leaving:
            left_active.remove(id(expected))
            right_active.remove(id(actual))
            continue
        if kind is not type(actual):
            return False
        if kind in {dict, list}:
            if id(expected) in left_active or id(actual) in right_active:
                return None
            if kind is dict:
                if (any(type(key) is not str for key in expected)
                        or any(type(key) is not str for key in actual)):
                    return None
                if expected.keys() != actual.keys():
                    return False
                pairs = [(value, actual[key], False) for key, value in expected.items()]
            else:
                if len(expected) != len(actual):
                    return False
                pairs = [(a, b, False) for a, b in zip(expected, actual, strict=False)]
            left_active.add(id(expected))
            right_active.add(id(actual))
            pending.append((expected, actual, True))
            pending.extend(pairs)
        elif kind is float:
            if not math.isfinite(expected) or not math.isfinite(actual):
                return None
            if expected.hex() != actual.hex():
                return False
        elif kind not in {str, int, bool, type(None)}:
            return None
        elif expected != actual:
            return False
    return True


def _marshal_json_bytes(value):
    """A CPython-only encoding hint, never a JSON eligibility verdict."""
    import sys
    if sys.implementation.name != "cpython" or sys.version_info[:2] != (3, 11):
        return None
    import marshal
    try:
        return marshal.dumps(value, 2)
    except Exception:
        return None


def _exact_json_snapshot(value):
    """(independent copy, marshal hint) only for provably finite, exact JSON.

    json.loads only yields exact dict/list/str/int/float/bool/None, and our
    allow_nan=False dump never emits NaN, so a marshal-identical round trip
    is the verdict _same_json_value(value, value) gives, without the Python
    walk. Anything else returns None and keeps the original walking path.
    """
    encoded = _marshal_json_bytes(value)
    if encoded is None:
        return None
    try:
        import json
        candidate = json.loads(json.dumps(
            value, allow_nan=False, ensure_ascii=True, sort_keys=False,
        ))
    except Exception:
        return None
    # Differs for tuples, non-str keys, subclasses, joined surrogate pairs.
    if _marshal_json_bytes(candidate) != encoded:
        return None
    return candidate, encoded


def _same_json_snapshot(snapshot, actual, encoded):
    """Only a previously proven finite, exact JSON snapshot may supply encoded."""
    if encoded is not None and _marshal_json_bytes(actual) == encoded:
        return True
    return _same_json_value(snapshot, actual)


def _json_fingerprint_snapshot(value, fingerprint):
    """Keep an independent exact-value snapshot; unsupported copies stay legacy."""
    copied = False
    snapshot = None
    try:
        if _same_json_value(value, value) is True:
            import json
            candidate = json.loads(json.dumps(
                value, allow_nan=False, ensure_ascii=True, sort_keys=False,
            ))
            # JSON can combine adjacent surrogate code points without raising.
            if _same_json_value(value, candidate) is True:
                snapshot = candidate
                copied = True
    except Exception:
        pass
    if not copied:
        snapshot = deepcopy(value)
    return snapshot, fingerprint, _marshal_json_bytes(snapshot)


def _proven_json_snapshot(value, fingerprint):
    """A fingerprint memo entry for finite exact JSON, else None."""
    prepared = _exact_json_snapshot(value)
    if prepared is not None:
        return prepared[0], fingerprint, prepared[1]
    if _same_json_value(value, value) is True:
        return _json_fingerprint_snapshot(value, fingerprint)
    return None


def _payload_reused_snapshot(projection, fingerprint, payload_snapshot):
    """A row memo entry around an already proven private payload copy, else None.

    payload_snapshot is (live payload, private exact copy); the caller proved
    them equal with no intervening code. Only the small control fields are
    copied here, so the payload is not serialized and decoded a second time.
    """
    live, private = payload_snapshot
    if type(projection) is not dict or projection.get("request_payload") is not live:
        return None
    prepared = _exact_json_snapshot(
        {key: value for key, value in projection.items() if key != "request_payload"})
    if prepared is None:
        return None
    snapshot = {key: private if key == "request_payload" else prepared[0][key] for key in projection}
    return snapshot, fingerprint, _marshal_json_bytes(snapshot)


@dataclass
class _HistoricalOperationRead:
    verification: object
    operation: object
    control_digest: str
    storage_digest: str
    consumed: bool = False


_UNLOADED = object()


@dataclass(eq=False)
class _HistoricalNodeProof:
    """One proven historical (operation, job) node, bound to its live ORM objects.

    Its content was proven exactly once against private exact snapshots held by
    the verification's row/job fingerprint memo. Later edge and memo visits
    reuse it only while the very same objects (and the very same payload object)
    are presented; finish() rechecks their in-memory content once per node.
    """
    operation: object
    payload: object
    fingerprint_key: tuple
    operation_digest: str
    job: object
    job_digest: str


def _elapsed(verification, key, started):
    _count(verification, key, time.perf_counter() - started)


_CHECKPOINT_ARCHIVE_MEMO_MAX_BYTES = 512 * 1024 * 1024
_CHECKPOINT_ARCHIVE_MEMO_MAX_ENTRIES = 8
_CHECKPOINT_ARCHIVE_MEMO_GUARD_DOMAIN = b"svg-checkpoint-hydration-memo/cpython311-marshal2/sha256/v1\x00"


class _EpisodeVerification:
    """One call's historical proofs, fenced by fresh dependency reads at exit."""

    def __init__(self, session):
        from utils.owner_context import get_current_owner_id, get_current_tenant_id
        self.session = session
        self.principal = (get_current_owner_id(), get_current_tenant_id())
        self.transaction = session.sync_session.get_transaction()
        self.episodes = {}
        self.operation_fingerprints = {}
        self.job_fingerprints = {}
        self.references = set()
        self.active = set()
        self.rows = {}
        self.row_fingerprints = {}
        self.controls = {}
        self.ledgers = {}
        self.producers = {}
        # {job_id: {row_id: (storage sha256, finish digest)}}: both values come from the one
        # first-pass SELECT that decoded the row. In memory only; finish() always re-reads.
        self.producer_storage = {}
        # Per-verification counters and visited (operation, job) episodes; evidence only.
        self.stats = {"rows_decoded_python": 0, "rows_redecoded_finish": 0, "episode_visits": 0,
                      "episode_memo_hits": 0, "reference_edges": 0,
                      "expect_digests_reused": 0, "payload_snapshots_reused": 0,
                      # Whole-payload work: these scale with distinct nodes, not edge visits.
                      "operation_digests": 0, "job_digests": 0, "payload_marshal_compares": 0,
                      "node_identity_reuses": 0, "node_finish_checks": 0, "edge_job_id_reads": 0,
                      # Finish storage fast path (PostgreSQL only).
                      "rows_storage_hits": 0, "rows_storage_changed": 0, "rows_storage_recorded": 0,
                      "storage_sha_rows": 0, "ledger_storage_hits": 0, "ledger_storage_changed": 0,
                      "ledger_union_rows_decoded": 0,
                      "t_marshal_s": 0.0, "t_digest_s": 0.0, "t_db_fetch_s": 0.0,
                      "t_storage_sha_s": 0.0, "t_final_decode_s": 0.0}
        # {operation id: _HistoricalNodeProof}; finish() rechecks each node's live content once.
        self.node_proofs = {}
        # {(model, identity, value): (signature, storage sha256, verified digest)}. Each entry
        # comes from one finish() SELECT that decoded the row AND matched its expectation.
        self.row_storage = {}
        # {(operation ids, job id): (signature, {group: {row id: storage sha256}})}, ditto.
        self.ledger_storage = {}
        self.storage_modes = {}
        self.visited_episodes = set()
        self.checkpoint_archives = {}
        self.checkpoint_archive_bytes = 0
        self.live_operation = None
        self.live_job = None
        self.live_admission_id = None
        self.live_admission = None
        self.live_leases = {}
        self.live_lease_ids = None
        self.live_budget = None
        self.seal_memo = {"session": session, "transaction": None, "principal": None,
                          "seals": {}, "verification": ref(self)}

    def bind_live(self, operation, job):
        """Separate this attempt's heartbeat from its immutable authority."""
        if operation.state != "running":
            return
        if (operation.owner_id != self.principal[0] or not operation.attempt_token
                or operation.contract_version != "public-operation/v2"
                or job.workspace_operation_id != operation.id
                or job.state not in {"running", "staging"}
                or job.attempt_token != operation.attempt_token):
            reject("svg_partial_recovery_request_snapshot_changed")
        self.live_operation = _operation_control(operation)
        snapshot = _dump(job)
        self.live_job = {key: snapshot.get(key) for key in (
            "id", "heartbeat_at", "updated_at", "completed_slides", "total_slides", "current_stage")}
        self._heartbeat_snapshot(self.live_operation, self.live_operation)

    def _nonnegative_counters(self, snapshot, baseline, fields, *, monotonic=False):
        for key in fields:
            value = snapshot.get(key, 0)
            if (type(value) is not int or value < 0
                    or monotonic and value < baseline.get(key, 0)):
                reject("svg_partial_recovery_request_snapshot_changed")
            baseline[key] = value

    def _operating_times(self, snapshot, baseline, fields):
        from datetime import datetime
        from utils.datetime_utils import as_utc

        for key in fields:
            value, previous = snapshot.get(key), baseline.get(key)
            if value is None or previous is None:
                reject("svg_partial_recovery_request_snapshot_changed")
            value_time = datetime.fromisoformat(value) if isinstance(value, str) else value
            old_time = datetime.fromisoformat(previous) if isinstance(previous, str) else previous
            if as_utc(value_time) < as_utc(old_time):
                reject("svg_partial_recovery_request_snapshot_changed")
            baseline[key] = value

    def _heartbeat_snapshot(self, snapshot, baseline, *, resource=False, require_live=True):
        from datetime import datetime
        from utils.datetime_utils import as_utc

        def instant(value):
            if value is None:
                reject("svg_partial_recovery_request_snapshot_changed")
            return as_utc(datetime.fromisoformat(value) if isinstance(value, str) else value)

        deadline = instant(self.live_operation["deadline_at"])
        expiry, heartbeat = instant(snapshot.get("lease_expires_at")), instant(snapshot.get("heartbeat_at"))
        if (expiry < instant(baseline.get("lease_expires_at"))
                or heartbeat < instant(baseline.get("heartbeat_at"))
                or (require_live and expiry <= as_utc(get_current_utc_datetime()))
                or expiry > deadline or heartbeat > expiry):
            reject("svg_partial_recovery_request_snapshot_changed")
        fields = {"heartbeat_at", "lease_expires_at"}
        if resource:
            if snapshot.get("version", -1) < baseline.get("version", 0):
                reject("svg_partial_recovery_request_snapshot_changed")
            fields.add("version")
        else:
            if instant(snapshot.get("updated_at")) < instant(baseline.get("updated_at")):
                reject("svg_partial_recovery_request_snapshot_changed")
            fields.add("updated_at")
        stable = {key: value for key, value in snapshot.items() if key not in fields}
        for key in fields:
            baseline[key] = snapshot[key]
        return stable

    def row_snapshot(self, model, snapshot, *, require_live=True):
        if (model is WorkspaceOperation and self.live_operation is not None
                and str(snapshot["id"]) == str(self.live_operation["id"])):
            stable = self._heartbeat_snapshot(snapshot, self.live_operation, require_live=require_live)
            self._nonnegative_counters(snapshot, self.live_operation, ("version", "next_event_sequence"), monotonic=True)
            progress = snapshot.get("progress")
            if progress is not None:
                if not isinstance(progress, dict):
                    reject("svg_partial_recovery_request_snapshot_changed")
                for key in ("percent", "current", "total"):
                    value = progress.get(key)
                    if value is not None and (type(value) is not int or value < 0 or key == "percent" and value > 100):
                        reject("svg_partial_recovery_request_snapshot_changed")
                if (progress.get("current") is not None and progress.get("total") is not None
                        and progress["current"] > progress["total"]):
                    reject("svg_partial_recovery_request_snapshot_changed")
            return {key: value for key, value in stable.items() if key not in {"version", "next_event_sequence", "progress"}}
        if (model is PresentationGenerationJob and self.live_job is not None
                and str(snapshot["id"]) == str(self.live_job["id"])):
            self._operating_times(snapshot, self.live_job, ("heartbeat_at", "updated_at"))
            self._nonnegative_counters(snapshot, self.live_job, ("completed_slides",), monotonic=True)
            if (snapshot.get("total_slides") != self.live_job["total_slides"]
                    or snapshot.get("completed_slides", 0) > self.live_job["total_slides"]
                    or snapshot.get("current_stage") is not None and (
                        not isinstance(snapshot["current_stage"], str) or len(snapshot["current_stage"]) > 128)):
                reject("svg_partial_recovery_request_snapshot_changed")
            return {key: value for key, value in snapshot.items() if key not in {
                "heartbeat_at", "updated_at", "current_stage", "completed_slides"}}
        return snapshot

    def lease_snapshots(self, admissions, leases):
        if self.live_operation is None:
            return [_dump(row) for row in leases]
        operation = self.live_operation
        active = [row for row in admissions if str(row.operation_id) == str(operation["id"])]
        if active:
            if (len(active) != 1 or active[0].state != "active"
                    or str(active[0].owner_id) != str(operation["owner_id"])
                    or active[0].tenant_id != self.principal[1]
                    or active[0].fence != operation["fencing_token"]
                    or _dump(active[0])["deadline_at"] != operation["deadline_at"]
                    or self.live_admission_id not in (None, str(active[0].id))):
                reject("svg_partial_recovery_request_snapshot_changed")
            self.live_admission_id = str(active[0].id)
            related = [row for row in leases if str(row.operation_id) == str(operation["id"])
                or str(row.admission_id) == self.live_admission_id]
            own = {str(row.id) for row in related}
            if (len(related) != 3 or len(own) != 3 or self.live_lease_ids not in (None, own)
                    or any(str(row.operation_id) != str(operation["id"])
                        or str(row.admission_id) != self.live_admission_id for row in related)):
                reject("svg_partial_recovery_request_snapshot_changed")
            self.live_lease_ids = own
        result = []
        for row in leases:
            snapshot = _dump(row)
            if str(row.operation_id) == str(operation["id"]):
                if (str(row.admission_id) != self.live_admission_id or row.state != "leased"
                        or row.attempt != operation["attempt_number"] or row.fence != operation["fencing_token"]
                        or row.lease_owner_hash != sha256_hex(operation["attempt_token"].encode())
                        or snapshot["deadline_at"] != operation["deadline_at"]):
                    reject("svg_partial_recovery_request_snapshot_changed")
                baseline = self.live_leases.setdefault(str(row.id), deepcopy(snapshot))
                snapshot = self._heartbeat_snapshot(snapshot, baseline, resource=True)
            result.append(snapshot)
        return result

    def ledger_snapshots(self, admissions, calls, leases, usage, budgets, reservations):
        projected_leases = self.lease_snapshots(admissions, leases)
        live = self.live_operation
        if live is None:
            return {name: [_dump(row) for row in rows] for name, rows in (
                ("admissions", admissions), ("calls", calls), ("leases", leases),
                ("usage", usage), ("budgets", budgets), ("reservations", reservations))}
        active = [row for row in admissions if str(row.operation_id) == str(live["id"])]
        projected_admissions = []
        for row in admissions:
            snapshot = _dump(row)
            if active and row is active[0]:
                baseline = self.live_admission or deepcopy(snapshot)
                self._nonnegative_counters(snapshot, baseline, ("initial_request_count", "retry_request_count",
                    "estimated_consumed_microunits"), monotonic=True)
                # A late receipt replaces an estimated actual charge and can
                # reduce both actual consumption and the unavailable count.
                self._nonnegative_counters(snapshot, baseline, ("estimated_reserved_microunits",
                    "actual_reserved_microunits", "actual_consumed_microunits", "actual_cost_unavailable_count"))
                self._operating_times(snapshot, baseline, ("updated_at",))
                if (snapshot.get("initial_request_count", 0) > snapshot["planned_request_limit"]
                        or snapshot.get("retry_request_count", 0) > snapshot["retry_request_limit"]):
                    reject("svg_partial_recovery_request_snapshot_changed")
                self.live_admission = baseline
                snapshot = {key: value for key, value in snapshot.items() if key not in {
                    "initial_request_count", "retry_request_count", "estimated_consumed_microunits",
                    "actual_consumed_microunits", "actual_cost_unavailable_count", "estimated_reserved_microunits",
                    "actual_reserved_microunits", "updated_at"}}
            projected_admissions.append(snapshot)
        own_budgets = [row for row in budgets if str(row.workspace_operation_id) == str(live["id"])
            or self.live_job is not None and str(row.job_id) == str(self.live_job["id"])]
        if len(own_budgets) > 1:
            reject("svg_partial_recovery_request_snapshot_changed")
        if own_budgets:
            row = own_budgets[0]
            snapshot = _dump(row)
            if (not active or str(row.workspace_operation_id) != str(live["id"])
                    or row.job_id is not None or str(row.owner_id) != str(live["owner_id"])
                    or row.tenant_id != self.principal[1] or row.state != "active" or row.cancellation_reason is not None
                    or type(row.estimated_limit) is not int or not 0 <= row.estimated_limit <= active[0].estimated_cost_limit_microunits
                    or type(row.actual_limit) is not int or not 0 <= row.actual_limit <= active[0].actual_cost_limit_microunits):
                reject("svg_partial_recovery_request_snapshot_changed")
            baseline = self.live_budget or deepcopy(snapshot)
            self._nonnegative_counters(snapshot, baseline, ("estimated_consumed", "actual_consumed", "request_count", "version"), monotonic=True)
            self._nonnegative_counters(snapshot, baseline, ("estimated_reserved",))
            self._operating_times(snapshot, baseline, ("updated_at",))
            operating = {"estimated_consumed", "actual_consumed", "request_count", "version", "estimated_reserved", "updated_at"}
            if {key: value for key, value in snapshot.items() if key not in operating} != {
                    key: value for key, value in baseline.items() if key not in operating}:
                reject("svg_partial_recovery_request_snapshot_changed")
            self.live_budget = baseline
        elif active and self.live_budget is not None:
            reject("svg_partial_recovery_request_snapshot_changed")
        budget_ids = {row.id for row in own_budgets}
        return {"admissions": projected_admissions, "leases": projected_leases,
            "calls": [_dump(row) for row in calls if str(row.admission_id) != self.live_admission_id],
            "usage": [_dump(row) for row in usage if self.live_job is None or str(row.job_id) != str(self.live_job["id"])],
            "budgets": [_dump(row) for row in budgets if row not in own_budgets],
            "reservations": [_dump(row) for row in reservations if row.budget_id not in budget_ids]}

    def check(self, session):
        from utils.owner_context import get_current_owner_id, get_current_tenant_id
        transaction = session.sync_session.get_transaction()
        if (session is not self.session
                or (get_current_owner_id(), get_current_tenant_id()) != self.principal
                or self.transaction is not None and transaction is not self.transaction):
            reject("svg_partial_recovery_request_snapshot_changed")
        if self.transaction is None:
            self.transaction = transaction

    def _payload_matches(self, cached, payload):
        """Whole live payload vs a private exact snapshot (marshal hint, else exact walk)."""
        started = time.perf_counter()
        try:
            return _same_json_snapshot(cached[0], payload, cached[2])
        finally:
            _count(self, "payload_marshal_compares")
            _elapsed(self, "t_marshal_s", started)

    def _operation_digest(self, projection):
        """The operation fingerprint: digest of exactly the row projection expect() binds.

        Same information as [control, payload] (control never carries the payload),
        so the in-memory episode memo stays injective, and the row expectation can
        reuse this digest instead of hashing the whole payload a second time.
        """
        started = time.perf_counter()
        try:
            return digest(projection)
        finally:
            _count(self, "operation_digests")
            _elapsed(self, "t_digest_s", started)

    def node(self, operation, job=None):
        """The node proof for these exact live objects, else None (never content-based)."""
        if self.transaction is None:
            return None
        proof = self.node_proofs.get(str(getattr(operation, "id", None)))
        if (proof is None or proof.operation is not operation
                or vars(operation).get("request_payload", _UNLOADED) is not proof.payload
                or job is not None and proof.job is not job):
            return None
        return proof

    def remember_node(self, operation, job, fingerprint_key, operation_digest, job_digest):
        """Bind a just-proven historical node; the caller compared both live values with no await since."""
        identity = str(operation.id)
        if self.transaction is None or identity in self.node_proofs:
            return
        row = self.row_fingerprints.get((WorkspaceOperation, "id", identity))
        job_row = self.job_fingerprints.get(str(job.id))
        payload = vars(operation).get("request_payload", _UNLOADED)
        cached = self.operation_fingerprints.get(fingerprint_key)
        if (row is None or job_row is None or cached is None or payload is _UNLOADED
                or row[1] != operation_digest or job_row[1] != job_digest or cached[1] != operation_digest
                or self.rows.get((WorkspaceOperation, "id", identity)) != operation_digest
                or self.rows.get((PresentationGenerationJob, "id", str(job.id))) != job_digest):
            return
        self.node_proofs[identity] = _HistoricalNodeProof(
            operation, payload, fingerprint_key, operation_digest, job, job_digest)

    def _check_node_proofs(self):
        """Same-process, possibly unflushed in-place edits of a proven node fail closed.

        One whole-content comparison per unique node per finish(), against the
        private exact snapshots, independent of any database storage hash.
        """
        from sqlalchemy import inspect as inspect_state
        for identity, proof in self.node_proofs.items():
            started = time.perf_counter()
            _count(self, "node_finish_checks")
            # Expired/detached objects would lazy-load (or have no live state): fail closed as a
            # recovery rejection instead of escaping as an async lazy-load error.
            for live in (proof.operation, proof.job):
                state = inspect_state(live)
                if state.expired_attributes or state.detached:
                    reject("svg_partial_recovery_request_snapshot_changed")
            row = self.row_fingerprints.get((WorkspaceOperation, "id", identity))
            job_row = self.job_fingerprints.get(str(proof.job.id))
            if row is None or job_row is None or type(row[0]) is not dict:
                reject("svg_partial_recovery_request_snapshot_changed")
            control = {key: value for key, value in row[0].items() if key != "request_payload"}
            if "request_payload" in vars(proof.operation):
                same = _same_json_snapshot(row[0], _operation_projection(proof.operation), row[2])
            else:
                # Not loaded: no in-memory payload exists to drift; the fresh row read binds storage.
                same = _same_json_value(control, _operation_control(proof.operation))
            if same is not True or _same_json_snapshot(job_row[0], _dump(proof.job), job_row[2]) is not True:
                reject("svg_partial_recovery_request_snapshot_changed")
            _elapsed(self, "t_marshal_s", started)

    def operation_fingerprint(self, operation, read, *, require_current_code, node=None):
        self.check(self.session)
        if read is None:
            control = _operation_control(operation)
            identity = str(operation.id)
            if self.controls.get(identity) == control:
                control_digest = digest(control)
                proof = self.node(operation)
                if proof is not None and proof.fingerprint_key[1] == control_digest:
                    cached = self.operation_fingerprints.get(proof.fingerprint_key)
                    if cached is not None and cached[1] == proof.operation_digest:
                        # Same live objects as the proven node; finish() rechecks content.
                        _count(self, "node_identity_reuses")
                        return cached[1], None
                for (cached_id, cached_control, _), cached in self.operation_fingerprints.items():
                    if (cached_id == identity and cached_control == control_digest
                            and self._payload_matches(cached, operation.request_payload) is True):
                        return cached[1], None
            # No new storage-bound proof is minted on this direct-call path.
            return self._operation_digest(_operation_projection(operation)), None
        if (require_current_code or not isinstance(read, _HistoricalOperationRead)
                or read.verification is not self or read.operation is not operation or read.consumed):
            reject("svg_partial_recovery_request_snapshot_changed")
        read.consumed = True
        control = _operation_control(operation)
        if (digest(control) != read.control_digest
                or self.controls.get(str(operation.id)) != control):
            reject("svg_partial_recovery_request_snapshot_changed")
        key = (str(operation.id), read.control_digest, read.storage_digest)
        cached = self.operation_fingerprints.get(key)
        if cached is not None:
            if (node is not None and node is self.node(operation) and node.fingerprint_key == key
                    and node.operation_digest == cached[1]):
                # The proven node's own objects: no whole-payload marshal per visit.
                _count(self, "node_identity_reuses")
                return cached[1], None
            same = self._payload_matches(cached, operation.request_payload)
            if same is None:
                return self._operation_digest(_operation_projection(operation)), None
            if not same:
                reject("svg_partial_recovery_request_snapshot_changed")
            return cached[1], None
        projection = _operation_projection(operation)
        fingerprint = self._operation_digest(projection)
        started = time.perf_counter()
        try:
            prepared = _exact_json_snapshot(operation.request_payload)
            if prepared is not None:
                return fingerprint, (key, prepared[0], prepared[1], projection)
            if _same_json_value(operation.request_payload, operation.request_payload) is not True:
                return fingerprint, None
            snapshot = deepcopy(operation.request_payload)
            return fingerprint, (key, snapshot, _marshal_json_bytes(snapshot), projection)
        finally:
            _elapsed(self, "t_marshal_s", started)

    def job_fingerprint(self, job, *, node=None):
        self.check(self.session)
        if node is not None and node is self.node(node.operation, job):
            # The proven node's own job object; finish() rechecks its content once.
            _count(self, "node_identity_reuses")
            return node.job_digest
        projection = _dump(job)
        key = str(job.id)
        cached = self.job_fingerprints.get(key) if self.transaction is not None else None
        if cached is not None:
            started = time.perf_counter()
            same = _same_json_snapshot(cached[0], projection, cached[2])
            _elapsed(self, "t_marshal_s", started)
            if same is True:
                return cached[1]
        started = time.perf_counter()
        fingerprint = digest(projection)
        _count(self, "job_digests")
        _elapsed(self, "t_digest_s", started)
        if self.transaction is not None and key not in self.job_fingerprints:
            prepared = _proven_json_snapshot(projection, fingerprint)
            if prepared is not None:
                self.job_fingerprints[key] = prepared
        return fingerprint

    def expect(self, model, snapshot, *, identity="id", _digested=None, _payload_snapshot=None):
        """_digested=(snapshot, digest(snapshot)) and _payload_snapshot=(live
        payload, proven private copy) are only valid for values the caller
        produced from these exact objects with no intervening code."""
        self.check(self.session)
        key = (model, identity, str(snapshot[identity]))
        # This may be an earlier ORM read from before a slow historical proof.
        # finish() checks the freshly read lease against the current time.
        projection = self.row_snapshot(model, snapshot, require_live=False)
        cached = self.row_fingerprints.get(key) if self.transaction is not None else None
        job_cached = (self.job_fingerprints.get(str(snapshot[identity]))
            if model is PresentationGenerationJob
            and identity == "id" and self.transaction is not None
            and (self.live_job is None or str(snapshot["id"]) != str(self.live_job["id"])) else None)
        if (_digested is not None and _digested[0] is snapshot and projection is snapshot
                and _sha256_hex_valid(_digested[1])):
            # The caller just hashed this unprojected object; digest it once.
            job_cached = None
            expected = _digested[1]
            _count(self, "expect_digests_reused")
        elif (job_cached is not None
                and _same_json_snapshot(job_cached[0], projection, job_cached[2]) is True):
            # The job projection is the one already fingerprinted for this episode key.
            expected = job_cached[1]
        else:
            job_cached = None
            expected = (cached[1] if cached is not None
                and _same_json_snapshot(cached[0], projection, cached[2]) is True else digest(projection))
        if key in self.rows and self.rows[key] != expected:
            reject("svg_partial_recovery_request_snapshot_changed")
        self.rows[key] = expected
        if self.transaction is not None and key not in self.row_fingerprints:
            # A job_cached match already proved projection equals an exact snapshot.
            prepared = job_cached
            if prepared is None and _payload_snapshot is not None:
                prepared = _payload_reused_snapshot(projection, expected, _payload_snapshot)
                if prepared is not None:
                    _count(self, "payload_snapshots_reused")
            if prepared is None:
                prepared = _proven_json_snapshot(projection, expected)
            if prepared is not None:
                self.row_fingerprints[key] = prepared

    async def ledger(self, operation_ids, job_id, *, archive=None):
        ids = tuple(sorted({str(value) for value in operation_ids}))
        key = (ids, str(job_id))
        if key not in self.ledgers:
            self.ledgers[key] = await _fresh_recovery_ledger_snapshot(self.session,
                [uuid.UUID(value) for value in ids], uuid.UUID(str(job_id)), verification=self)
        if archive is not None:
            # A generation job also receives later, independently fenced page
            # preview accounting. Only operation-owned membership is frozen by
            # the archive; every archived row remains checked individually.
            budgets = [row for row in archive.get("runtime_budgets", [])
                       if str(row.get("workspace_operation_id")) in ids]
            budget_ids = {row["id"] for row in budgets}
            expected = digest({name: sorted(rows, key=lambda item: str(item["id"]))
                for name, rows in (("admissions", archive.get("admissions", [])),
                    ("calls", archive.get("logical_calls", [])),
                    ("leases", archive.get("resource_leases", [])), ("usage", []),
                    ("budgets", budgets), ("reservations", [row for row in
                        archive.get("runtime_reservations", []) if row["budget_id"] in budget_ids]))})
            operation_owned = await _fresh_recovery_ledger_snapshot(self.session,
                [uuid.UUID(value) for value in ids], uuid.UUID(str(job_id)), operation_bound_only=True)
            if operation_owned != expected:
                reject("svg_partial_recovery_history_changed")

    def record_producer_storage(self, identity, storage):
        """Bind registered producer digests to the storage hashes of the SELECT that decoded them."""
        expected = self.producers.get(identity)
        if (not isinstance(storage, dict) or expected is None or set(storage) != set(expected)
                or any(not _sha256_hex_valid(sha) for sha, _ in storage.values())):
            return
        self.producer_storage.setdefault(identity, storage)

    async def _fresh_producer_digests(self):
        """Fresh {job: {row: finish digest}}; Python decodes only rows whose stored bytes changed."""
        jobs = list(self.producers)
        fresh = None
        if self.producer_storage and _checkpoint_storage_supported(self.session):
            fresh = await _fresh_checkpoint_storage_digests(self.session, jobs)
        if fresh is None:
            rows = await _fresh_recovery_model_rows(self.session, AuthoredSlideCheckpoint,
                AuthoredSlideCheckpoint.job_id.in_([uuid.UUID(value) for value in jobs]))
            self.stats["rows_redecoded_finish"] += len(rows)
            actual = {value: {} for value in jobs}
            for row, value in zip(rows, await _producer_digests_off_loop(rows), strict=True):
                actual[str(row.job_id)][str(row.id)] = value
            return actual, "legacy"
        # The exact fresh row set of every producer job must equal the registered one.
        if ({job: set(rows) for job, rows in fresh.items()}
                != {job: set(rows) for job, rows in self.producers.items()}):
            reject("svg_partial_recovery_request_snapshot_changed")
        actual, changed = {value: {} for value in jobs}, {}
        for job, rows in fresh.items():
            bound = self.producer_storage.get(job) or {}
            for row_id, sha in rows.items():
                record = bound.get(row_id)
                if record is not None and _sha256_hex_valid(sha) and record[0] == sha:
                    # Byte-identical stored tuple: a fresh decode yields this same digest.
                    actual[job][row_id] = record[1]
                else:
                    changed[row_id] = job
        if changed:
            rows = await _fresh_recovery_model_rows(self.session, AuthoredSlideCheckpoint,
                AuthoredSlideCheckpoint.id.in_([uuid.UUID(value) for value in changed]))
            self.stats["rows_redecoded_finish"] += len(rows)
            for row, value in zip(rows, await _producer_digests_off_loop(rows), strict=True):
                row_id = str(row.id)
                if changed.pop(row_id, None) != str(row.job_id):
                    reject("svg_partial_recovery_request_snapshot_changed")
                actual[str(row.job_id)][row_id] = value
            if changed:
                reject("svg_partial_recovery_request_snapshot_changed")
        return actual, "storage"

    async def finish(self):
        # Never consume the memo here: identity-map rows and previously sealed
        # JSON are insufficient evidence that dependencies stayed unchanged.
        self.check(self.session)
        groups = {}
        for model, identity, value in self.rows:
            groups.setdefault((model, identity), []).append(value)
        producer_ids = {value for expected in self.producers.values() for value in expected}
        deferred_checkpoints = {}
        # Storage plans (and their catalog guards) are decided afresh for every finish().
        plans, row_modes = {}, []
        for (model, identity), values in groups.items():
            if (model is AuthoredSlideCheckpoint and identity == "id"
                    and set(values).issubset(producer_ids)):
                # Keep both checks at the final producer fresh-read boundary.
                deferred_checkpoints = {value: self.rows[(model, identity, value)] for value in values}
                continue
            row_modes.append(await self._finish_rows(model, identity, values, plans))
        if self.controls:
            fresh = await _fresh_recovery_operation_controls(self.session, self.controls)
            if fresh != self.controls:
                reject("svg_partial_recovery_request_snapshot_changed")
        ledger_mode = await self._finish_ledgers(plans)
        producer_mode = "none"
        if self.producers:
            actual, producer_mode = await self._fresh_producer_digests()
            if actual != self.producers:
                reject("svg_partial_recovery_request_snapshot_changed")
            checked_rows = {value: fingerprint for expected in actual.values()
                            for value, fingerprint in expected.items() if value in deferred_checkpoints}
            if checked_rows != deferred_checkpoints:
                reject("svg_partial_recovery_request_snapshot_changed")
        seals = self.seal_memo["seals"]
        if seals:
            fresh = await _payload_storage_seals(self.session, [uuid.UUID(value) for value in seals])
            if fresh != seals:
                reject("svg_partial_recovery_request_snapshot_changed")
        for working, guard in self.checkpoint_archives.values():
            encoded = _marshal_json_bytes(working)
            if encoded is None:
                reject("svg_partial_recovery_request_snapshot_changed")
            current_guard = hashlib.sha256(_CHECKPOINT_ARCHIVE_MEMO_GUARD_DOMAIN)
            current_guard.update(encoded)
            del encoded
            if current_guard.digest() != guard:
                reject("svg_partial_recovery_request_snapshot_changed")
        # Last, after every await: in-place edits of proven nodes' live ORM objects.
        self._check_node_proofs()
        self.check(self.session)
        import logging
        stats = self.stats
        self.storage_modes = {"rows": ",".join(sorted(set(row_modes))) or "none",
                              "ledgers": ledger_mode, "producers": producer_mode}
        logging.getLogger(__name__).info(
            "SVG partial recovery verification finished: distinct_jobs=%s distinct_episodes=%s "
            "episode_visits=%s episode_memo_hits=%s reference_edges=%s rows_decoded_python=%s "
            "rows_redecoded_finish=%s expect_digests_reused=%s payload_snapshots_reused=%s producer_mode=%s "
            "distinct_nodes=%s row_mode=%s ledger_mode=%s work=%s",
            len(self.producers), len(self.visited_episodes), stats["episode_visits"],
            stats["episode_memo_hits"], stats["reference_edges"], stats["rows_decoded_python"],
            stats["rows_redecoded_finish"], stats["expect_digests_reused"],
            stats["payload_snapshots_reused"], producer_mode, len(self.node_proofs),
            self.storage_modes["rows"], ledger_mode,
            " ".join(f"{key}={round(value, 3) if type(value) is float else value}"
                     for key, value in stats.items() if key.startswith(("t_", "operation_digests",
                         "job_digests", "payload_marshal", "node_", "edge_job", "rows_storage",
                         "storage_sha", "ledger_"))))

    def _live_row_values(self, model, identity):
        """Rows whose heartbeat/lease/progress projection is live authority: never storage-reused."""
        live = set()
        if identity == "id" and model is WorkspaceOperation and self.live_operation is not None:
            live.add(str(self.live_operation["id"]))
        if identity == "id" and model is PresentationGenerationJob and self.live_job is not None:
            live.add(str(self.live_job["id"]))
        return live

    async def _finish_rows(self, model, identity, values, plans):
        """Fresh check of one expected row group; identical stored rows skip Python decode."""
        expected = {value: self.rows[(model, identity, value)] for value in values}
        plan, reason = await _row_storage_plan(self.session, model, identity, plans)
        live = self._live_row_values(model, identity)
        actual = {}
        if plan is not None:
            recorded = {}
            for value in values:
                record = self.row_storage.get((model, identity, value))
                if value not in live and record is not None and record[0] == plan.signature:
                    recorded[value] = record
            if recorded:
                fresh = await _fresh_row_storage_shas(self.session, model, identity, plan,
                    list(recorded), verification=self)
                for value, record in recorded.items():
                    sha = fresh.get(value)
                    if _sha256_hex_valid(sha) and sha == record[1]:
                        # Byte-identical stored row: a fresh decode yields this verified digest.
                        actual[value] = record[2]
                        _count(self, "rows_storage_hits")
                    else:
                        _count(self, "rows_storage_changed")
        decode = [value for value in values if value not in actual]
        storage = {}
        if decode:
            field = getattr(model, identity)
            if plan is not None:
                typed = [uuid.UUID(value) if plan.is_uuid else value for value in decode]
                rows, shas = await _fresh_model_rows_with_storage(self.session, model,
                    field.in_(typed), plan, verification=self)
            else:
                annotation = model.model_fields[identity].annotation
                is_uuid = annotation is uuid.UUID or uuid.UUID in get_args(annotation)
                typed = [uuid.UUID(value) if is_uuid else value for value in decode]
                started = time.perf_counter()
                rows = await _fresh_recovery_model_rows(self.session, model, field.in_(typed))
                _elapsed(self, "t_db_fetch_s", started)
                shas = [None] * len(rows)
            started = time.perf_counter()
            for row, sha in zip(rows, shas, strict=True):
                value = str(getattr(row, identity))
                projection = self.row_snapshot(model, _dump(row))
                key = (model, identity, value)
                cached = self.row_fingerprints.get(key) if self.transaction is not None else None
                actual[value] = (cached[1] if cached is not None and
                    _same_json_snapshot(cached[0], projection, cached[2]) is True
                    else digest(projection))
                storage[value] = sha
            _elapsed(self, "t_final_decode_s", started)
        if actual != expected:
            reject("svg_partial_recovery_request_snapshot_changed")
        if plan is None:
            return "exact:" + reason
        for value, sha in storage.items():
            # Bind only a digest this very SELECT's decode just matched against its expectation.
            if value not in live and _sha256_hex_valid(sha) and actual.get(value) == expected.get(value):
                self.row_storage[(model, identity, value)] = (plan.signature, sha, actual[value])
                _count(self, "rows_storage_recorded")
        return "storage"

    async def _finish_ledgers(self, plans):
        """Fresh ledger fence. With no live authority bound, each group row is read once.

        Live-bound verifications keep the exact per-key path: their projections
        carry heartbeat/lease/budget monotonic checks that are never reused.
        """
        if not self.ledgers:
            return "none"
        reason = None
        if self.live_operation is not None or self.live_job is not None:
            reason = "live_bound"
        ledger_plans = {}
        if reason is None:
            for name, model, _ in _LEDGER_GROUPS:
                plan, why = await _row_storage_plan(self.session, model, "id", plans)
                if plan is None:
                    reason = name + ":" + why
                    break
                ledger_plans[name] = plan
        if reason is not None:
            await self._finish_ledgers_exact(list(self.ledgers))
            return "exact:" + reason
        signature = tuple(ledger_plans[name].signature for name, _, _ in _LEDGER_GROUPS)
        recorded = [key for key in self.ledgers
                    if key in self.ledger_storage and self.ledger_storage[key][0] == signature]
        if recorded:
            fresh = await _fresh_ledger_union(self.session, recorded, ledger_plans,
                decode=False, verification=self)
            exact = [key for key in self.ledgers if key not in recorded]
            if fresh is None:
                exact = list(self.ledgers)
            else:
                for key in recorded:
                    members = _ledger_members(key, fresh)
                    current = {name: {row_id: fresh[name][row_id][0] for row_id in ids}
                               for name, ids in members.items()}
                    if current == self.ledger_storage[key][1]:
                        _count(self, "ledger_storage_hits")
                    else:
                        _count(self, "ledger_storage_changed")
                        exact.append(key)
            if exact:
                await self._finish_ledgers_exact([key for key in self.ledgers if key in set(exact)])
            return "storage"
        fresh = await _fresh_ledger_union(self.session, list(self.ledgers), ledger_plans,
            decode=True, verification=self)
        if fresh is None:
            await self._finish_ledgers_exact(list(self.ledgers))
            return "exact:inconsistent_union"
        for key, expected in self.ledgers.items():
            members = _ledger_members(key, fresh)
            started = time.perf_counter()
            value = digest({name: [fresh[name][row_id][2] for row_id in sorted(ids)]
                            for name, ids in members.items()})
            _elapsed(self, "t_digest_s", started)
            if value != expected:
                reject("svg_partial_recovery_request_snapshot_changed")
            self.ledger_storage[key] = (signature, {name: {row_id: fresh[name][row_id][0] for row_id in ids}
                                                    for name, ids in members.items()})
        return "storage_bound"

    async def _finish_ledgers_exact(self, keys):
        for key in keys:
            operation_ids, job_id = key
            started = time.perf_counter()
            fresh = await _fresh_recovery_ledger_snapshot(self.session,
                [uuid.UUID(value) for value in operation_ids], uuid.UUID(job_id), verification=self)
            _elapsed(self, "t_db_fetch_s", started)
            if fresh != self.ledgers[key]:
                reject("svg_partial_recovery_request_snapshot_changed")


def _count(verification, key, amount=1):
    # Counters are evidence only; substituted verifier doubles may not carry them.
    stats = getattr(verification, "stats", None)
    if type(stats) is dict:
        stats[key] = stats.get(key, 0) + amount


async def _producer_digests_off_loop(rows):
    """Digest private, freshly decoded rows without blocking the event loop (no session use)."""
    if not rows:
        return []
    import asyncio
    rows = list(rows)
    return await asyncio.to_thread(lambda: [_fresh_producer_checkpoint_digest(row) for row in rows])


async def _fresh_recovery_model_rows(session, model, predicate, *, skip_request_payload=False):
    from sqlalchemy import inspect as inspect_model
    names = [item.key for item in inspect_model(model).column_attrs if not (skip_request_payload and item.key == "request_payload")]
    statement = select(*(getattr(model, name).label(name) for name in names)).where(predicate)
    records = (await session.execute(statement)).mappings().all()
    return [model.model_validate(dict(record) | ({"request_payload": {}} if skip_request_payload and "request_payload" in model.model_fields else {})) for record in records]


def _pg_sha256_text(value):
    return func.encode(func.sha256(func.convert_to(cast(value, Text), "UTF8")), "hex")


def _checkpoint_storage_supported(session):
    """PostgreSQL >= 11 (sha256) only; anything else keeps the full legacy decode."""
    try:
        dialect = session.get_bind().dialect
        version = dialect.server_version_info
    except Exception:
        # Not knowing the bind is never a reason to skip a decode: take the legacy path.
        return False
    return (dialect.name == "postgresql" and isinstance(version, tuple)
            and bool(version) and type(version[0]) is int and version[0] >= 11)


def _checkpoint_storage_expression(overrides=None):
    """sha256 over the JSON array text of every stored checkpoint column, in model order.

    Every column type here (uuid, integer, varchar, text, json, timestamptz) has an exact,
    injective JSON rendering, so equal hashes in one transaction mean an identical stored
    tuple. ``overrides`` ({column name: expression}) exists only for the read-only PG check.
    """
    from sqlalchemy import inspect as inspect_model
    names = [item.key for item in inspect_model(AuthoredSlideCheckpoint).column_attrs]
    if not names or len(names) >= 100:
        return None
    overrides = overrides or {}
    return _pg_sha256_text(func.json_build_array(
        *(overrides.get(name, getattr(AuthoredSlideCheckpoint, name)) for name in names)))


async def _fresh_checkpoint_rows_sealed(session, predicate):
    """Rows plus {row_id: storage sha256} from the same SELECT; storage None means legacy read."""
    expression = _checkpoint_storage_expression() if _checkpoint_storage_supported(session) else None
    if expression is None:
        return await _fresh_recovery_model_rows(session, AuthoredSlideCheckpoint, predicate), None
    from sqlalchemy import inspect as inspect_model
    names = [item.key for item in inspect_model(AuthoredSlideCheckpoint).column_attrs]
    statement = select(*(getattr(AuthoredSlideCheckpoint, name).label(name) for name in names),
                       expression.label("_storage_sha256")).where(predicate)
    rows, storage = [], {}
    for record in (await session.execute(statement)).mappings().all():
        values = dict(record)
        sha = values.pop("_storage_sha256")
        row = AuthoredSlideCheckpoint.model_validate(values)
        rows.append(row)
        storage[str(row.id)] = sha
    if len(storage) != len(rows):
        return rows, None
    return rows, storage


async def _fresh_checkpoint_storage_digests(session, job_ids):
    """{job_id: {row_id: storage sha256}} for every requested job; no payload column is returned."""
    expression = _checkpoint_storage_expression()
    if expression is None:
        return None
    result = await session.execute(select(
        AuthoredSlideCheckpoint.id, AuthoredSlideCheckpoint.job_id, expression.label("storage_sha256"),
    ).where(AuthoredSlideCheckpoint.job_id.in_([uuid.UUID(str(value)) for value in job_ids])))
    fresh = {str(value): {} for value in job_ids}
    for row_id, job_id, sha in result.all():
        rows = fresh.get(str(job_id))
        if rows is None or str(row_id) in rows:
            reject("svg_partial_recovery_request_snapshot_changed")
        rows[str(row_id)] = sha
    return fresh


# Row storage hashing for finish(): stored PostgreSQL types whose json_build_array
# rendering is injective for a fixed session (TimeZone only affects timestamptz text,
# and a changed setting can only turn an equal row into "changed", never the reverse).
# Floating point, arrays, domains, bytea, intervals and everything else stay exact.
_ROW_STORAGE_SCHEMA = "svg-partial-recovery-row-storage/v1"
_ROW_STORAGE_PG_TYPES = frozenset({
    "uuid", "int2", "int4", "int8", "bool", "varchar", "text", "json", "jsonb",
    "timestamptz", "timestamp", "date", "numeric"})
_ROW_STORAGE_BATCH = 500
_LEDGER_GROUPS = (
    ("admissions", PublicOperationAdmission, ("operation_id",)),
    ("calls", PublicProviderLogicalCall, ("admission_id",)),
    ("leases", PublicResourceLease, ("admission_id",)),
    ("usage", ProviderUsageReceipt, ("job_id",)),
    ("budgets", ProviderRuntimeBudget, ("workspace_operation_id", "job_id")),
    ("reservations", ProviderRuntimeReservation, ("budget_id",)),
)


@dataclass(frozen=True)
class _RowStoragePlan:
    signature: tuple
    expression: object
    is_uuid: bool


def _relation_name(table):
    return ".".join('"' + part.replace('"', '""') + '"' for part in (table.schema, table.name) if part)


async def _row_storage_plan(session, model, identity, cache):
    """(plan, None) when every mapped column of a single-PK model hashes injectively, else (None, reason).

    The catalog guard runs once per model per finish(): the stored types, not the
    Python annotations, decide the rendering. The plan signature (schema tag, table,
    column names and stored types) must match for a recorded hash to be reused.
    """
    cache_key = (model, identity)
    if cache_key not in cache:
        cache[cache_key] = await _build_row_storage_plan(session, model, identity)
    return cache[cache_key]


async def _build_row_storage_plan(session, model, identity):
    from sqlalchemy import Column
    from sqlalchemy import inspect as inspect_model
    if not _checkpoint_storage_supported(session):
        return None, "unsupported_dialect"
    table = getattr(model, "__table__", None)
    attributes = list(inspect_model(model).column_attrs)
    if table is None or not attributes or len(attributes) >= 100:
        return None, "column_count"
    columns = []
    for attribute in attributes:
        if (len(attribute.columns) != 1 or not isinstance(attribute.columns[0], Column)
                or attribute.columns[0].table is not table):
            return None, "unmapped_column"
        columns.append((attribute.key, attribute.columns[0]))
    key_column = dict(columns).get(identity)
    primary = list(table.primary_key.columns)
    if key_column is None or len(primary) != 1 or primary[0] is not key_column:
        return None, "identity_not_primary_key"
    # A failing catalog read aborts the PostgreSQL transaction, so it is not a fallback: let it raise.
    catalog = (await session.execute(sql_text(
        # typtype is the one-byte "char" type (bytes under asyncpg): compare text only.
        "SELECT a.attname::text, t.typname::text, t.typtype::text, n.nspname::text "
        "FROM pg_catalog.pg_attribute a "
        "JOIN pg_catalog.pg_type t ON t.oid = a.atttypid "
        "JOIN pg_catalog.pg_namespace n ON n.oid = t.typnamespace "
        "WHERE a.attrelid = to_regclass(:relation) AND a.attnum > 0 AND NOT a.attisdropped"),
        {"relation": _relation_name(table)})).all()
    stored = {name: (type_name, type_kind, namespace) for name, type_name, type_kind, namespace in catalog}
    signature = []
    for key, column_object in columns:
        found = stored.get(column_object.name)
        if found is None:
            return None, "schema_mismatch"
        type_name, type_kind, namespace = found
        if type_kind == "e":
            signature.append((key, column_object.name, "enum:" + namespace + "." + type_name))
        elif namespace == "pg_catalog" and type_kind == "b" and type_name in _ROW_STORAGE_PG_TYPES:
            signature.append((key, column_object.name, type_name))
        else:
            return None, "unsupported_type:" + type_name
    # [[is_null, value], ...] in model order: typed, null-safe (SQL NULL vs JSON null) and
    # unambiguous; nesting keeps json_build_array below its 100-argument limit.
    expression = _pg_sha256_text(func.json_build_array(*(
        func.json_build_array(getattr(model, key).is_(None), getattr(model, key)) for key, _ in columns)))
    annotation = model.model_fields[identity].annotation
    is_uuid = annotation is uuid.UUID or uuid.UUID in get_args(annotation)
    return _RowStoragePlan((_ROW_STORAGE_SCHEMA, table.fullname, tuple(signature)), expression, is_uuid), None


def _batches(values):
    values = list(values)
    for start in range(0, len(values), _ROW_STORAGE_BATCH):
        yield values[start:start + _ROW_STORAGE_BATCH]


async def _fresh_model_rows_with_storage(session, model, predicate, plan, *, verification=None):
    """Exactly _fresh_recovery_model_rows plus each row's storage sha256 from the same SELECT."""
    from sqlalchemy import inspect as inspect_model
    names = [item.key for item in inspect_model(model).column_attrs]
    started = time.perf_counter()
    records = (await session.execute(select(
        *(getattr(model, name).label(name) for name in names),
        plan.expression.label("_svg_storage_sha256")).where(predicate))).mappings().all()
    _elapsed(verification, "t_db_fetch_s", started)
    started = time.perf_counter()
    rows, shas = [], []
    for record in records:
        values = dict(record)
        shas.append(values.pop("_svg_storage_sha256"))
        rows.append(model.model_validate(values))
    _elapsed(verification, "t_final_decode_s", started)
    _count(verification, "storage_sha_rows", len(rows))
    return rows, shas


async def _fresh_row_storage_shas(session, model, identity, plan, values, *, verification=None):
    """{identity: storage sha256} in bounded batches; no data column is transferred."""
    attribute = getattr(model, identity)
    fresh = {}
    started = time.perf_counter()
    for chunk in _batches(values):
        typed = [uuid.UUID(value) if plan.is_uuid else value for value in chunk]
        for value, sha in (await session.execute(select(attribute, plan.expression)
                                                 .where(attribute.in_(typed)))).all():
            if str(value) in fresh:
                reject("svg_partial_recovery_request_snapshot_changed")
            fresh[str(value)] = sha
    _elapsed(verification, "t_storage_sha_s", started)
    _count(verification, "storage_sha_rows", len(fresh))
    return fresh


async def _fresh_ledger_union(session, keys, plans, *, decode, verification=None):
    """Every ledger group row any key can select, once: {group: {id: (sha, links, dump)}}.

    Each group is read with the union of the per-key predicates; membership is
    then evaluated per key on the link columns of the very SELECT that hashed
    (and, with decode, decoded) the row. None means an inconsistent re-read.
    """
    from sqlalchemy import inspect as inspect_model
    operations = sorted({str(uuid.UUID(value)) for key in keys for value in key[0]})
    jobs = sorted({str(uuid.UUID(key[1])) for key in keys})
    fresh = {}

    async def read(name, model, links, predicates):
        plan = plans[name]
        rows = {}
        names = [item.key for item in inspect_model(model).column_attrs] if decode else ["id", *links]
        for attribute, values in predicates:
            for chunk in _batches(values):
                started = time.perf_counter()
                records = (await session.execute(select(
                    *(getattr(model, column_name).label(column_name) for column_name in names),
                    plan.expression.label("_svg_storage_sha256"))
                    .where(attribute.in_([uuid.UUID(value) for value in chunk])))).mappings().all()
                _elapsed(verification, "t_db_fetch_s" if decode else "t_storage_sha_s", started)
                started = time.perf_counter()
                for record in records:
                    values_by_name = dict(record)
                    sha = values_by_name.pop("_svg_storage_sha256")
                    if not _sha256_hex_valid(sha):
                        return None
                    row_links = tuple(None if values_by_name[link] is None else str(values_by_name[link])
                                      for link in links)
                    row_id = str(values_by_name["id"])
                    dump = None
                    if decode and row_id not in rows:
                        dump = _dump(model.model_validate(values_by_name))
                        _count(verification, "ledger_union_rows_decoded")
                    previous = rows.get(row_id)
                    if previous is not None:
                        if previous[:2] != (sha, row_links):
                            return None
                        continue
                    rows[row_id] = (sha, row_links, dump)
                _count(verification, "storage_sha_rows", len(records))
                if decode:
                    _elapsed(verification, "t_final_decode_s", started)
        fresh[name] = rows
        return rows

    groups = {name: (model, links) for name, model, links in _LEDGER_GROUPS}

    def spec(name, *predicates):
        model, links = groups[name]
        return name, model, links, [(attribute, values) for attribute, values in predicates if values]

    if await read(*spec("admissions", (PublicOperationAdmission.operation_id, operations))) is None:
        return None
    admissions = sorted(fresh["admissions"])
    for name, attribute in (("calls", PublicProviderLogicalCall.admission_id),
                            ("leases", PublicResourceLease.admission_id)):
        if await read(*spec(name, (attribute, admissions))) is None:
            return None
    if await read(*spec("usage", (ProviderUsageReceipt.job_id, jobs))) is None:
        return None
    if await read(*spec("budgets", (ProviderRuntimeBudget.workspace_operation_id, operations),
                        (ProviderRuntimeBudget.job_id, jobs))) is None:
        return None
    if await read(*spec("reservations", (ProviderRuntimeReservation.budget_id, sorted(fresh["budgets"])))) is None:
        return None
    return fresh


def _ledger_members(key, fresh):
    """The exact per-key membership _fresh_recovery_ledger_snapshot selects (no live operation)."""
    operations = {str(uuid.UUID(value)) for value in key[0]}
    job = str(uuid.UUID(key[1]))
    admissions = {row_id for row_id, row in fresh["admissions"].items() if row[1][0] in operations}
    budgets = {row_id for row_id, row in fresh["budgets"].items()
               if row[1][0] in operations or row[1][1] == job}
    return {
        "admissions": admissions,
        "calls": {row_id for row_id, row in fresh["calls"].items() if row[1][0] in admissions},
        "leases": {row_id for row_id, row in fresh["leases"].items() if row[1][0] in admissions},
        "usage": {row_id for row_id, row in fresh["usage"].items() if row[1][0] == job},
        "budgets": budgets,
        "reservations": {row_id for row_id, row in fresh["reservations"].items() if row[1][0] in budgets},
    }


async def _fresh_recovery_operation_controls(session, operation_ids):
    from uuid import UUID
    ids = [UUID(str(value)) for value in operation_ids]
    rows = await _fresh_recovery_model_rows(session, WorkspaceOperation, WorkspaceOperation.id.in_(ids), skip_request_payload=True)
    return {str(row.id): _operation_control(row) for row in rows}


def _recovery_ledger_snapshot(admissions, calls, leases, usage, budgets, reservations, *, verification=None):
    groups = [sorted(rows, key=lambda item: str(item.id)) for rows in (admissions, calls, leases, usage, budgets, reservations)]
    if verification is not None:
        return digest(verification.ledger_snapshots(*groups))
    return digest({name: [_dump(row) for row in rows] for name, rows in zip(
        ("admissions", "calls", "leases", "usage", "budgets", "reservations"), groups, strict=False)})


async def _fresh_recovery_ledger_snapshot(session, operation_ids, job_id, *, operation_bound_only=False, verification=None):
    admissions = await _fresh_recovery_model_rows(session, PublicOperationAdmission, PublicOperationAdmission.operation_id.in_(operation_ids))
    admission_ids = [row.id for row in admissions]
    calls = await _fresh_recovery_model_rows(session, PublicProviderLogicalCall, PublicProviderLogicalCall.admission_id.in_(admission_ids)) if admission_ids else []
    lease_predicate = PublicResourceLease.admission_id.in_(admission_ids)
    live_id = (verification.live_operation or {}).get("id") if verification is not None else None
    include_live = live_id is not None and str(live_id) in {str(value) for value in operation_ids}
    if include_live:
        lease_predicate = or_(lease_predicate, PublicResourceLease.operation_id == uuid.UUID(str(live_id)))
    leases = await _fresh_recovery_model_rows(session, PublicResourceLease, lease_predicate) if admission_ids or include_live else []
    usage = ([] if operation_bound_only else await _fresh_recovery_model_rows(
        session, ProviderUsageReceipt, ProviderUsageReceipt.job_id == job_id))
    budget_predicate = ProviderRuntimeBudget.workspace_operation_id.in_(operation_ids)
    if not operation_bound_only:
        budget_predicate = or_(budget_predicate, ProviderRuntimeBudget.job_id == job_id)
    budgets = await _fresh_recovery_model_rows(session, ProviderRuntimeBudget, budget_predicate)
    budget_ids = [row.id for row in budgets]
    reservations = await _fresh_recovery_model_rows(session, ProviderRuntimeReservation, ProviderRuntimeReservation.budget_id.in_(budget_ids)) if budget_ids else []
    return _recovery_ledger_snapshot(admissions, calls, leases, usage, budgets, reservations, verification=verification)


async def _payload_storage_seals(session, operation_ids, *, seal_memo=None):
    if seal_memo is None:
        return await _fresh_payload_storage_seals(session, operation_ids)
    from utils.owner_context import get_current_owner_id, get_current_tenant_id
    transaction = session.sync_session.get_transaction()
    principal = (get_current_owner_id(), get_current_tenant_id())
    if seal_memo.get("session") is not session or transaction is None:
        reject("svg_partial_recovery_request_snapshot_changed")
    if seal_memo.get("transaction") is None:
        seal_memo["transaction"] = transaction
        seal_memo["principal"] = principal
    if seal_memo["transaction"] is not transaction or seal_memo["principal"] != principal:
        reject("svg_partial_recovery_request_snapshot_changed")
    ids = tuple(dict.fromkeys(str(value) for value in operation_ids))
    missing = [value for value in ids if value not in seal_memo["seals"]]
    if missing:
        from uuid import UUID
        first = await _fresh_payload_storage_seals(session, [UUID(value) for value in missing])
        seal_memo["seals"].update(deepcopy(first))
    return {value: deepcopy(seal_memo["seals"][value]) for value in ids if value in seal_memo["seals"]}


async def _fresh_payload_storage_seals(session, operation_ids):
    """Hash original PostgreSQL JSON bytes with one shared field extraction."""
    if session.get_bind().dialect.name != "postgresql":
        reject("svg_partial_recovery_archive_v2_postgresql_required")

    def sha_text(value):
        return func.encode(func.sha256(func.convert_to(cast(value, Text), "UTF8")), "hex")

    payload = WorkspaceOperation.request_payload
    # Preserve the old scalar/array/null extraction result without asking
    # json_to_record to populate a record from a non-object JSON value.
    record_source = case((func.json_typeof(payload) == "object", payload),
                         else_=cast(literal("{}"), JSON))
    fields = func.json_to_record(record_source).table_valued(
        column(ARCHIVE_KEY, JSON),
        column("svg_partial_recovery_manifest", JSON),
        column("principal_tenant_id", Text),
    ).render_derived(with_types=True).lateral("recovery_payload_fields")
    # json_to_record maps JSON literal null to SQL NULL. Recover the original
    # extraction only on this boundary, preserving its TEXT bytes and hash.
    archive = func.coalesce(fields.c[ARCHIVE_KEY], payload[ARCHIVE_KEY], type_=JSON)
    result = await session.execute(select(
        WorkspaceOperation.id,
        sha_text(payload).label("payload_sha256"),
        sha_text(archive).label("archive_storage_sha256"),
        archive["schema"].as_string().label("archive_schema"),
        fields.c.svg_partial_recovery_manifest["archive_digest"].as_string().label("archive_canonical_sha256"),
        fields.c.principal_tenant_id.label("tenant_id"),
    ).select_from(WorkspaceOperation.__table__.join(fields, true()))
      .where(WorkspaceOperation.id.in_(operation_ids)))
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
        if not isinstance(prior_archive, dict) or prior_archive.get("schema") not in {ARCHIVE_V1, ARCHIVE_V2, ARCHIVE_V3}:
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


def _seal_memo_verification(seal_memo, *, verification=None):
    if seal_memo is None or "verification" not in seal_memo:
        return None
    reference = seal_memo["verification"]
    if type(reference) is not ReferenceType:
        reject("svg_partial_recovery_request_snapshot_changed")
    current = reference()
    if (type(current) is not _EpisodeVerification or current.seal_memo is not seal_memo
            or verification is not None and current is not verification):
        reject("svg_partial_recovery_request_snapshot_changed")
    return current

async def _verify_operation_references(session, operation, manifest, references, *, seal_memo=None,
                                       verification=None, reuse_historical_seals=False,
                                       operation_digest=None):
    memo_verification = _seal_memo_verification(seal_memo, verification=verification)
    verification = verification or memo_verification
    owns_verification = verification is None
    verification = verification or _EpisodeVerification(session)
    verification.check(session)
    # The manifest and references are bound to the payload by their stored hashes.
    if operation_digest is None:
        operation_digest, _ = verification.operation_fingerprint(operation, None, require_current_code=False)
    key = (str(operation.id), operation_digest)
    if key not in verification.references:
        await _verify_operation_references_once(session, operation, manifest, references,
            seal_memo=seal_memo or verification.seal_memo, verification=verification,
            reuse_historical_seals=reuse_historical_seals)
        verification.check(session)
        verification.references.add(key)
    if owns_verification:
        await verification.finish()


async def _verify_operation_references_once(session, operation, manifest, references, *, seal_memo,
                                            verification, reuse_historical_seals=False):
    if not isinstance(references, list) or not references or not isinstance(references[0], dict):
        reject("svg_partial_recovery_archive_reference_unbound")
    ids = []
    expected = str(operation.retry_of_operation_id)
    source_presentation = (manifest["presentation_id"] if manifest.get("append_only_recovery") is True
                           else str(operation.presentation_id))
    source_conversation = ((references[0].get("control") or {}).get("conversation_id")
                           if manifest.get("append_only_recovery") is True
                           else str(operation.conversation_id))
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
                or (not ids and (control.get("presentation_id") != source_presentation
                    or control.get("conversation_id") != source_conversation))
                or control.get("kind") != "presentation.generate"
                or control.get("state") not in {"failed", "cancelled", "dead_letter", "deadline_exceeded"}
                or expected in ids):
            reject("svg_partial_recovery_archive_reference_unbound")
        if (ref.get("archive_schema") is not None and (
                ref["archive_schema"] not in {ARCHIVE_V1, ARCHIVE_V2, ARCHIVE_V3}
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
    seals = await _payload_storage_seals(session, [uuid.UUID(value) for value in ids], seal_memo=seal_memo)
    fresh_controls = await _fresh_recovery_operation_controls(session, ids)
    if set(fresh_controls) != set(originals) or any(_operation_control(row) != fresh_controls[key] for key, row in originals.items()):
        reject("svg_partial_recovery_request_snapshot_changed")
    for key, control in fresh_controls.items():
        if key in verification.controls and verification.controls[key] != control:
            reject("svg_partial_recovery_request_snapshot_changed")
        verification.controls[key] = control
    for entry in references:
        original, seal = originals.get(entry["id"]), seals.get(entry["id"])
        ref = entry["payload_ref"]
        if (original is None or seal is None
                or digest(_operation_control(original)) != entry["control_digest"]
                or seal.get("tenant_id") != ref["tenant_id"]
                or any(seal.get(key) != ref.get(key) for key in (
                    "payload_sha256", "archive_storage_sha256", "archive_schema", "archive_canonical_sha256"))):
            reject("svg_partial_recovery_history_changed")
    for newer, older in zip(ids, ids[1:], strict=False):
        child, parent = originals[newer], originals[older]
        if (child.presentation_id, child.conversation_id) != (parent.presentation_id, parent.conversation_id):
            verification.check(session)
            _count(verification, "reference_edges")
            fresh_seals, missing, newer_same = {}, [], None
            for identity in (newer, older):
                expected_seal = seals.get(identity)
                candidate = originals[identity]
                eligible = (reuse_historical_seals and verification.transaction is not None
                    and seal_memo is verification.seal_memo
                    and candidate.state in {"failed", "cancelled", "dead_letter", "deadline_exceeded"}
                    and verification.controls.get(identity) == fresh_controls[identity]
                    and isinstance(expected_seal, dict))
                snapshot_same = None
                if eligible:
                    storage = dict(expected_seal)
                    storage_id = storage.get("id", identity)
                    eligible = ((isinstance(storage_id, uuid.UUID) or type(storage_id) is str)
                                and str(storage_id) == identity)
                    if eligible:
                        if "id" in storage:
                            storage["id"] = identity
                        memo_key = (identity, digest(fresh_controls[identity]), digest(storage))
                        cached = verification.operation_fingerprints.get(memo_key)
                        eligible = (cached is not None
                            and any(key[0] == identity and key[1] == cached[1]
                                    for key in verification.episodes)
                            and "request_payload" in vars(candidate))
                        if eligible:
                            node = verification.node(candidate)
                            if (node is not None and node.fingerprint_key == memo_key
                                    and node.operation_digest == cached[1]):
                                # The proven node's own live objects; its whole content
                                # is rechecked once at finish(), not on every edge.
                                _count(verification, "node_identity_reuses")
                                snapshot_same = True
                            else:
                                snapshot_same = verification._payload_matches(
                                    cached, vars(candidate)["request_payload"])
                            eligible = snapshot_same is True
                        if eligible and identity == newer:
                            newer_same = snapshot_same
                if eligible:
                    fresh_seals[identity] = expected_seal
                else:
                    missing.append(uuid.UUID(identity))
            if missing:
                fresh_seals.update(await _fresh_payload_storage_seals(session, missing))
            verification.check(session)
            if fresh_seals != {value: seals.get(value) for value in (newer, older)}:
                reject("svg_partial_recovery_history_changed")
            storage_fingerprint = dict(fresh_seals[newer])
            if "id" in storage_fingerprint:
                storage_id = storage_fingerprint["id"]
                if (not (isinstance(storage_id, uuid.UUID) or type(storage_id) is str)
                        or str(storage_id) != newer):
                    reject("svg_partial_recovery_history_changed")
                storage_fingerprint["id"] = newer
            control_digest = digest(fresh_controls[newer])
            storage_digest = digest(storage_fingerprint)
            cached = verification.operation_fingerprints.get((newer, control_digest, storage_digest))
            node = verification.node(child) if cached is not None else None
            if (node is None or node.fingerprint_key != (newer, control_digest, storage_digest)
                    or node.operation_digest != cached[1]):
                node = None
            if newer_same is True and not missing:
                # The same cached snapshot and payload were compared above with no await since.
                same = True
            elif node is not None:
                # The proven node's own live objects; finish() rechecks the content once.
                _count(verification, "node_identity_reuses")
                same = True
            else:
                same = (verification._payload_matches(cached, vars(child)["request_payload"])
                        if cached is not None and "request_payload" in vars(child) else None)
            if same is False:
                reject("svg_partial_recovery_request_snapshot_changed")
            if same is not True:
                await session.refresh(child, attribute_names=["request_payload"])
            read = _HistoricalOperationRead(verification, child,
                control_digest, storage_digest)
            # Graph relationship reads come from the node's private proof-bound copy.
            await _verify_append_only_edge(session, child, parent,
                verification=verification, _operation_read=read,
                _payload=cached[0] if node is not None else None)


async def _verify_append_only_edge(session, child, parent, *, verification=None, _operation_read=None,
                                   _payload=None):
    """A cross-document ancestor must be a verified append-only consumer.

    _payload is only the verification's private exact copy of child's proven
    payload (finish() rechecks the live one); it is never handed out.
    """
    payload = child.request_payload if _payload is None else _payload
    lineage = payload.get("svg_partial_recovery_lineage") or {}
    if (lineage.get("schema") != "svg-partial-recovery-lineage/v2"
            or lineage.get("action_operation_id") != str(parent.id)
            or lineage.get("source_presentation_id") != str(parent.presentation_id)
            or child.retry_of_operation_id != parent.id):
        reject("svg_partial_recovery_lineage_conflict")
    # The same binding predicate as before, evaluated in the database; only the
    # identity is transferred, and the session's identity map supplies the row
    # (loading it exactly as the former full-row SELECT did when it is absent).
    job_id = await session.scalar(select(PresentationGenerationJob.id).where(
        PresentationGenerationJob.workspace_operation_id == child.id,
        PresentationGenerationJob.presentation_id == child.presentation_id))
    _count(verification, "edge_job_id_reads")
    job = await session.get(PresentationGenerationJob, job_id) if job_id is not None else None
    if job is None:
        reject("svg_partial_recovery_lineage_conflict")
    await verify_episode(session, child, job, require_current_code=False, verification=verification,
        _operation_read=_operation_read)


def _base_code_digest(files=None):
    # Source bytes are fixed for the life of the process; each file is hashed once.
    files = CODE_FILES if files is None else files
    return _base_identity({name: digests.raw for name, digests in _code_file_digests(files).items()}, files)


def _base_identity(hashes, files):
    # Only these newly inventoried dependencies are absent from the historical
    # preimage. Current source states still read every file before reaching here.
    return digest([{ "path": name, "sha256": hashes[name]} for name in files
                   if name in hashes or name not in _POST_ANCHOR_CODE_FILES])


def _code_file_digests(files):
    root = Path(__file__).resolve().parents[1]
    return {name: code_file_digest(root / name) for name in files}


# Authority remains pinned by _code_digest(). Measured page equivalence excludes
# only recovery/operation/provider authority, retaining every other CODE_FILES
# dependency and the final-selected continuation outside that original list.
_PAGE_RENDER_AUTHORITY_FILES = frozenset({
    "services/generation_core/quality/review_coverage.py",
    "services/svg_partial_recovery.py",
    "services/svg_source_local_review_continuation.py",
    "services/workspace_service.py",
    "services/provider_durable_admission.py",
    "services/provider_usage_instrumentation.py",
    "services/provider_runtime_job_registration.py",
    "services/generation_core/quality/vision_advisory.py",
    "services/generation_core/quality/vision_advisory_prompt.py",
    "services/generation_core/drivers/svg/quality.py",
})


PAGE_RENDER_CODE_FILES = (tuple(name for name in CODE_FILES if name not in _PAGE_RENDER_AUTHORITY_FILES)
                          + ("services/svg_final_selected_continuation.py",))
# _code_digest() also binds these sources, named relative to services/ with the
# platform's path text (str(Path)), as it always has.
_FINAL_SELECTED_SOURCES = (
    "svg_final_selected_continuation.py", "svg_authoring_profile.py",
    "generation_core/drivers/svg/quality.py", "generation_core/drivers/svg/quality_recovery.py",
    "generation_core/drivers/svg/collect.py",
)
#: Every file either code identity reads.
CODE_IDENTITY_FILES = tuple(dict.fromkeys(
    (*CODE_FILES, *("services/" + name for name in _FINAL_SELECTED_SOURCES))))


def _native_path_text(name):
    return str(PurePath(name))


def code_identity(hashes, *, path_text=None):
    """Identity formula of ``_code_digest`` over ``{CODE_IDENTITY_FILES name: SHA-256}``."""
    path_text = path_text or _native_path_text
    return digest({"base": _base_identity(hashes, CODE_FILES),
                   "final_selected_sources": {path_text(name): hashes["services/" + name]
                                              for name in _FINAL_SELECTED_SOURCES}})


def _reviewed_code_identity(hashes):
    """``code_identity`` with POSIX path text, so a reviewed key is the same on every OS."""
    return code_identity(hashes, path_text=str)


def page_render_identity(hashes):
    """Identity formula of ``_page_render_code_digest`` over ``{file: SHA-256}``."""
    return _base_identity(hashes, PAGE_RENDER_CODE_FILES)


def code_source_state():
    from services.verifier_revisions import PARTIAL_RECOVERY_CODE_LEDGER

    return PARTIAL_RECOVERY_CODE_LEDGER.state(_code_file_digests(CODE_IDENTITY_FILES), code_identity,
                                              reviewed_formula=_reviewed_code_identity)


def page_render_source_state():
    from services.verifier_revisions import PAGE_RENDER_CODE_LEDGER

    # Both identities share one anchor table; the formula reads only its own files.
    return PAGE_RENDER_CODE_LEDGER.state(_code_file_digests(CODE_IDENTITY_FILES), page_render_identity)


def _page_render_code_digest():
    """The measured page-equivalence code identity in the checkpoint reuse key.

    It is the reviewed revision's identity (``services/verifier_revisions``) while
    the running source is a reviewed state of that revision, and the raw
    ``page_render_source_state().raw`` otherwise.
    """
    from services.verifier_revisions import PAGE_RENDER_CODE_LEDGER

    return PAGE_RENDER_CODE_LEDGER.resolve(page_render_source_state())


def _frozen_payload(payload):
    return {key: value for key, value in payload.items() if key != LINEAGE_KEY}


def _source_request_routing(payload):
    return {"presentation_id_present": "presentation_id" in payload,
            "presentation_id": deepcopy(payload.get("presentation_id"))}


def frozen_episode_payload(payload, manifest):
    """Project only a verified fork's routing identity back to its producer."""
    return _frozen_episode_projection(payload, manifest, copy=True)


def _frozen_episode_projection(payload, manifest, *, copy, manifest_digest=None):
    """copy=False shares nested caller values; only for an immediate digest.

    Only top-level keys of the new dict are rebound below, so the caller's
    payload is never mutated and nothing of the result is retained.
    """
    frozen = deepcopy(_frozen_payload(payload)) if copy else _frozen_payload(payload)
    if manifest.get("append_only_recovery") is not True:
        return frozen
    lineage = payload.get(LINEAGE_KEY) or {}
    routing = manifest.get("source_request_routing")
    if (not isinstance(routing, dict)
            or set(routing) != {"presentation_id_present", "presentation_id"}
            or type(routing.get("presentation_id_present")) is not bool
            or (routing["presentation_id_present"] is False and routing["presentation_id"] is not None)
            or lineage.get("schema") != "svg-partial-recovery-lineage/v2"
            or lineage.get("source_job_id") != manifest.get("job_id")
            or lineage.get("source_presentation_id") != manifest.get("presentation_id")
            or lineage.get("manifest_hash") != (digest(manifest) if manifest_digest is None else manifest_digest)
            or frozen.get("presentation_id") != lineage.get("presentation_id")
            or lineage.get("presentation_id") == manifest.get("presentation_id")):
        reject("svg_partial_recovery_episode_unbound")
    if routing["presentation_id_present"]:
        frozen["presentation_id"] = (deepcopy(routing["presentation_id"]) if copy
                                     else routing["presentation_id"])
    else:
        frozen.pop("presentation_id", None)
    return frozen


def _undispatched_episode_page(payload, job, operation, owner, tenant, *, page_calls=None,
                               attempt_token=None):
    """Only a new, server-sealed episode can prove that SDK I/O never began.

    Outside a recovery lineage (a first operation) the sealed stops are accepted
    only when they account for every logical call of the page one to one: each
    call settled as a failed pre-SDK reservation (no actual cost), each stop
    bound to the row's attempt, and no other provider outcome on the page.
    """
    lineage = bool(job.request_payload.get(LINEAGE_KEY))
    if ((not lineage and page_calls is None) or payload.get("attempts")
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
    if lineage:
        return True
    ordinals = [record.get("ordinal") for record in records.values()]
    if (any(type(value) is not int or value < 1 for value in ordinals) or len(set(ordinals)) != len(ordinals)
            or not isinstance(attempt_token, str) or not attempt_token
            or any(record.get("attempt_token") != attempt_token
                   or type(record.get("fencing_token")) is not int for record in records.values())):
        return False
    # Every stop has its own diagnostics entry and no other provider outcome is on the page.
    diagnostics = payload.get("provider_diagnostics") or []
    if (payload.get("provider_failures") or not isinstance(diagnostics, list)
            or any(not isinstance(item, dict) or item.get("code") != "public_provider_dispatch_stopped"
                   for item in diagnostics)
            or any(type(item.get("ordinal")) is not int or item["ordinal"] < 1 for item in diagnostics)
            or sorted(item["ordinal"] for item in diagnostics) != sorted(ordinals)):
        return False
    # One failed, cost-less pre-SDK reservation per stop; any other call stays unmatched.
    author_stages = {"author", "repair", "repair_remake", "repair_patch"}
    return len(page_calls) == len(ordinals) and all(
        call.stage in author_stages and call.outcome == "failed" and call.state == "unavailable"
        and getattr(call, "actual_cost_microunits", None) is None for call in page_calls)


def _receipt_terminal_ordinal(receipt, payload):
    """The sealed terminal ordinal a provider attempt's receipt answers for, or None.

    ``attempt_ordinal`` is the attempt index the author sent to admission. Each
    earlier sealed ``dispatch.stopped`` record of the page occupied one terminal
    ordinal without a purchase, so the author keys the call past them and
    writes that key as ``terminal_ordinal``. Receipts without the field (old
    ones, and every page without stops) answer for ``attempt_ordinal``. A
    present field must equal the attempt index plus exactly the stops sealed
    before it; anything else proves nothing.
    """
    ordinal = receipt.get("attempt_ordinal") if isinstance(receipt, dict) else None
    if type(ordinal) is not int or ordinal < 1:
        return None
    if "terminal_ordinal" not in receipt:
        return ordinal
    terminal = receipt["terminal_ordinal"]
    records = payload.get("provider_terminal_responses")
    if type(terminal) is not int or terminal <= ordinal or not isinstance(records, dict):
        return None
    stops = sum(isinstance(record, dict) and record.get("terminal_event") == "dispatch.stopped"
                and type(record.get("ordinal")) is int and record["ordinal"] < terminal
                and key == str(record["ordinal"]) for key, record in records.items())
    return terminal if ordinal + stops == terminal else None


def _completed_response(payload, job, operation, owner, tenant):
    records = payload.get("provider_terminal_responses") or {}
    if not isinstance(records, dict):
        return None
    # A completion from an earlier author is not the result of an unresolved
    # later repair. Keep that older response as history, never current output.
    inflight = payload.get("inflight")
    ordinals = [record["ordinal"] for record in records.values()
                if isinstance(record, dict) and type(record.get("ordinal")) is int
                and record["ordinal"] > 0]
    current_ordinal = (inflight if type(inflight) is int and inflight > 0
                       else max(ordinals, default=None))
    if current_ordinal is not None:
        record = records.get(str(current_ordinal))
        if not isinstance(record, dict) or record.get("ordinal") != current_ordinal:
            return None
        records = {str(current_ordinal): record}
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


def _author_style_matches(payload, receipt, job, index, *, header_producer=None):
    """Reprove the selected representative's pre-selection author context."""
    snapshot = job.svg_style_snapshot or {}
    if receipt.get("style_digest") == snapshot.get("digest"):
        return True
    from services.svg_common_header_design_checkpoint import _candidate_key, _envelope
    from utils.hashing import canonical_json_digest
    selection, header = snapshot.get("common_header_selection"), snapshot.get("common_header")
    if not isinstance(selection, dict) or not isinstance(header, dict) or header.get("version") != "svg-common-header/v2":
        return False
    try:
        page_id = job.authored_outline["slides"][index]["page_id"]
        identity = dict(candidate_revision=str(header["revision"]),candidate_id=header["candidate_id"],page_id=page_id)
        body_style = {key:value for key,value in snapshot.items() if key not in {"digest","common_header_selection"}}
        body_digest = canonical_json_digest(body_style)
        producer = header_producer if header_producer is not None else job
        if producer.svg_style_snapshot != snapshot or producer.authored_outline != job.authored_outline:
            return False
        envelope = _envelope(producer,producer.svg_header_design_checkpoint)
        document = envelope["document"]
        candidate = envelope["candidate_pages"].get(_candidate_key(index,identity))
        return (selection.get("representative_page_id") == page_id
            and selection.get("selected_candidate_id") == header["candidate_id"]
            and selection.get("selected_revision") == header["revision"]
            and payload.get("common_header_candidate_lineage") == identity
            and payload.get("common_header_candidate_style_digest") == body_digest == receipt.get("style_digest")
            and document.get("status") == "published" and document.get("selection") == selection
            and envelope.get("published_inputs") == dict(snapshot_digest=snapshot.get("digest"),
                outline_digest=canonical_json_digest(job.authored_outline))
            and isinstance(candidate,dict) and candidate.get("header_author_attempt_identity") == identity
            and author_witness(candidate) == author_witness(payload))
    except (KeyError,TypeError,ValueError,IndexError):
        return False


async def verified_header_producer(session, operation, job, *, verification=None):
    """Resolve preserved header evidence through verified consumer episodes."""
    owns_verification = verification is None
    verification = verification or _EpisodeVerification(session)
    verification.check(session)
    producer, current, seen = job, operation, set()
    while (producer.request_payload.get(LINEAGE_KEY) or {}).get("schema") == "svg-partial-recovery-lineage/v2":
        if producer.id in seen or current is None:
            reject("svg_partial_recovery_header_binding_invalid")
        seen.add(producer.id)
        manifest = await verify_episode(session,current,producer,
            require_current_code=not _historical_episode(current, producer), verification=verification)
        producer = await session.get(PresentationGenerationJob,uuid.UUID(manifest["job_id"]))
        if (producer is None or producer.svg_style_snapshot != job.svg_style_snapshot
                or producer.authored_outline != job.authored_outline):
            reject("svg_partial_recovery_header_binding_invalid")
        current = await session.get(WorkspaceOperation,producer.workspace_operation_id)
    if owns_verification:
        await verification.finish()
    return producer


def _frozen_vector_offer(seed_row, job, operation, owner, tenant):
    """Read only the DB-frozen catalogue and its content-bound library files."""
    from services.generation_core.assets.offer import AssetOffer
    from services.generation_core.drivers.svg.asset_slots import SEED_KEY, _ink, validate_offer_projection
    from services.generation_core.drivers.svg.embed_assets import flatten_icon_svg, IconFlattenBudgetExceeded
    from utils.authored_image_slots import source_slot_id_for_asset
    from utils.get_env import get_app_data_directory_env

    seed = (seed_row.svg_checkpoint or {}).get(SEED_KEY)
    if seed is None:
        return [{} for _ in range(job.total_slides)], None
    if seed_row.job_id != job.id or seed_row.slide_index != 0 or job.workspace_operation_id != operation.id:
        reject("svg_partial_recovery_vector_seed_unbound")
    offer = AssetOffer.from_json(seed, root=get_app_data_directory_env())
    revision = validate_offer_projection(offer, job.svg_style_snapshot)
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
            icon = flatten_icon_svg(text, color=_ink(text, slot), projection_revision=revision)
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


def _expected_page_input_digest(request, job, index, page_assets):
    from services.svg_generation_inputs import svg_page_input_digest
    _verify_frozen_header_outline(job)
    slide = job.authored_outline["slides"][index]
    return svg_page_input_digest(content=slide["content"], style=job.svg_style_snapshot,
        index=index, count=job.total_slides, instructions=request.instructions,
        language=(request.language or "").strip() or None,
        preset=request.codex_generation_preset, assets=page_assets,
        writing_policy=getattr(request, "writing_policy", None),
        header_page=slide.get("common_header"))


def _verify_frozen_header_outline(job):
    """Use the same frozen title and source binding for review and replay."""
    from services.svg_common_header import common_header_source_digest, validate_common_header_outline
    from services.svg_common_header_title_budget import (
        INTERNAL_HEADER_TITLE_PLAN_KEY, validate_header_title_plan,
    )
    from utils.hashing import canonical_json_digest

    style = job.svg_style_snapshot or {}
    outline = job.authored_outline or {}
    slides = outline.get("slides") if isinstance(outline, dict) else None
    if (not isinstance(slides, list)
            or (job.request_payload.get("common_header_mode") == "uniform"
                and "common_header" not in style)):
        reject("svg_partial_recovery_header_binding_invalid")
    try:
        validate_common_header_outline(style, outline)
        plan = validate_header_title_plan(
            job.request_payload.get(INTERNAL_HEADER_TITLE_PLAN_KEY))
    except (TypeError, ValueError, KeyError) as error:
        raise PartialRecoveryRejected("svg_partial_recovery_header_binding_invalid") from error
    if plan is not None:
        try:
            sources = [{"page_index": index,
                        "source_digest": common_header_source_digest(slide["content"])}
                       for index, slide in enumerate(slides)]
        except (TypeError, KeyError, ValueError) as error:
            raise PartialRecoveryRejected("svg_partial_recovery_header_binding_invalid") from error
        if (job.request_payload.get("common_header_mode") != "uniform"
                or len(slides) != plan["page_count"]
                or canonical_json_digest(sources) != plan["source_digest"]
                or any(not slides[index].get("title_source_digest")
                       for index in plan["missing_indices"])):
            reject("svg_partial_recovery_header_binding_invalid")
    for slide in slides:
        if not isinstance(slide, dict):
            reject("svg_partial_recovery_header_binding_invalid")
        provenance = slide.get("title_source_digest")
        if provenance is not None and (not isinstance(provenance, str) or not provenance
                or (slide.get("common_header") or {}).get("title_source_digest") != provenance):
            reject("svg_partial_recovery_header_binding_invalid")


def _recovery_source_pages(source, job, validation=None):
    from services.svg_source_layout import source_pages
    chart = job.svg_style_snapshot.get("source_chart_contract")
    if (validation is not None and validation[0] is source
            and validation[1] == chart and validation[2] is source.units):
        return validation[2]
    return source_pages(source, chart_contract=chart)


def _replay_output_view(job, source, index, validation=None, *,
                        code="svg_partial_recovery_source_transform_unproven"):
    """(view, facts) to re-derive a stored raw author response of this job's page.

    The view is the one the job pinned at admission, so a replay restores a compact response
    exactly as its producer did; ``(None, None)`` for jobs without a pin (today's parse). A pin
    this code cannot interpret rejects with its own code: a replay never guesses.
    """
    from services.svg_author_output_view import output_view_plan, snapshot_output_view
    try:
        view = snapshot_output_view(getattr(job, "svg_style_snapshot", None))
    except ValueError as error:
        raise PartialRecoveryRejected(str(error)) from error
    if view is None:
        return None, None
    from services.generation_core.authoring.author_source_svg_layout import whole_page_view_facts
    try:
        facts = whole_page_view_facts(_recovery_source_pages(source, job, validation)[index])
        output_view_plan(view, facts)
    except PartialRecoveryRejected:
        raise
    except (ValueError, TypeError, KeyError, IndexError, AttributeError) as error:
        raise PartialRecoveryRejected(code) from error
    return view, facts


def _source_author_render_witness(svg, payload, raw, *, job, row, completed):
    """Keep source-only replay unrendered until the existing real render path."""
    value = {'author_svg_sha256': sha256_hex(svg.encode()),
             'render_svg_sha256': sha256_hex(svg.encode()), 'transform': 'source-preserved'}
    if row is None or row.render_status != 'pending':
        return value
    from utils.owner_context import get_current_owner_id, get_current_tenant_id
    terminal = completed or {}
    unsigned = {key: item for key, item in terminal.items() if key != 'receipt_digest'}
    if (row.author_status != 'completed' or row.persist_status != 'pending'
            or row.render_path is not None or row.render_sha256 is not None
            or payload.get('quality') not in (None, {}) or payload.get('initial_quality') not in (None, {})
            or payload.get('png_sha256') is not None or payload.get('png_path') is not None
            or payload.get('source_digest') not in (None, value['author_svg_sha256'], 'sha256:' + value['author_svg_sha256'])
            or payload.get('last_valid_svg') not in (None, svg)
            or payload.get('local_readability_repair') not in (None, {})
            or terminal.get('schema') != 'svg-provider-terminal/v1'
            or terminal.get('terminal_event') != 'response.completed'
            or terminal.get('receipt_digest') != digest(unsigned)
            or terminal.get('raw_response') != raw or terminal.get('output_digest') != sha256_hex(raw.encode())
            or terminal.get('job_id') != str(job.id) or terminal.get('operation_id') != str(job.workspace_operation_id)
            or terminal.get('owner_id') != str(get_current_owner_id()) or terminal.get('tenant_id') != get_current_tenant_id()
            or not terminal.get('input_digest') or terminal.get('input_digest') != payload.get('input_digest')):
        reject('svg_partial_recovery_completed_author_unproven')
    return {**value, 'phase': 'author-only', 'source_svg_sha256': value['author_svg_sha256'],
            'first_render_svg_sha256': value['render_svg_sha256'], 'completed_receipt_digest': terminal['receipt_digest']}


def _author_render_chain(payload, raw, receipt, *, request, job, asset, index,
                         row=None, completed=None, vectors=None, vector_seed_witness=None, source=None, _source_validation=None):
    """Replay the existing local transforms; hashes alone never authorize bytes."""
    import json
    import xml.etree.ElementTree as ET
    from services.authored_canvas import request_canvas_contract
    from services.svg_contract import finalize_svg
    from services.svg_default_fonts import normalize_model_font_families
    from services.generation_core.drivers.svg.native_declarations import (
        extract_native_declarations, declaration_receipt,
    )

    canvas = request_canvas_contract(request)
    offered = next((page.slot_ids for page in asset.offered_pages if page.page_index == index), ())
    slots = {slot.slot_id: slot for slot in asset.slots if slot.slot_id in offered}
    page_assets = {key: slot.model_dump(mode="json", by_alias=True) for key, slot in slots.items()}
    expected_input = _expected_page_input_digest(request, job, index, page_assets)
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
        restoration = (receipt.get("source_preservation") or {}).get("source_lf_restoration")
        stored_restoration = (payload.get("source_preservation") or {}).get("source_lf_restoration")
        if restoration is None and stored_restoration is None:
            return _source_author_render_witness(author_svg, payload, raw, job=job, row=row, completed=completed)
        if (source is None or not isinstance(restoration, dict)
                or restoration != stored_restoration
                or str(receipt.get("raw_digest", "")).removeprefix("sha256:") != sha256_hex(raw.encode())):
            reject("svg_partial_recovery_source_transform_unproven")
    vectors = vectors or {}
    vector_offer = (payload.get("assetOffer") or {}).get("vectorIcons") or {}
    recorded_used = receipt.get("vector_icons_used") or []
    if (vectors or recorded_used or vector_offer.get("offered")) and vector_seed_witness is None:
        reject("svg_partial_recovery_vector_transform_unproven")
    from services.svg_author_output_view import parse_author_response
    view, view_facts = _replay_output_view(job, source, index, _source_validation)
    try:
        root, _ = parse_author_response(raw, view=view, source_facts=view_facts)
    except ET.ParseError as error:
        raise PartialRecoveryRejected("svg_partial_recovery_author_transform_unproven") from error
    declarations = extract_native_declarations(root)
    if declaration_receipt(declarations).get("native_declarations") != receipt.get("native_declarations"):
        reject("svg_partial_recovery_author_declarations_changed")
    from services.generation_core.drivers.svg.embed_assets import replace_vector_icon_images, vector_icon_id_prefix

    used = replace_vector_icon_images(root, vectors, id_prefix=vector_icon_id_prefix(job.svg_style_snapshot)) if vectors else set()
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
    if request.source_mode == "source_content_restyle":
        from services.svg_source_layout import bind_whole_page_source_svg
        from services.generation_core.authoring.author_source_svg_layout import _whole_page_facts
        from services.document_canvas_generation import source_restyle_font_floor, svg_quality_font_floor
        try:
            pages = _recovery_source_pages(source, job, _source_validation)
            image_slots = {}
            for block in pages[index].blocks:
                if block.kind != "image":
                    continue
                try:
                    occurrence = uuid.UUID(block.source_block_id)
                except ValueError:
                    occurrence = uuid.uuid5(asset.presentation_id, f"source-occurrence:{block.source_block_id}")
                matches = [slot.slot_id for slot in slots.values() if slot.occurrence_id == occurrence]
                if len(matches) != 1:
                    reject("svg_partial_recovery_source_transform_unproven")
                image_slots[block.source_block_id] = matches[0]
            floor = source_restyle_font_floor(request.instructions, getattr(request, "_document_canvas", None))
            if job.svg_style_snapshot.get("readability_policy") is not None:
                floor = max(floor, svg_quality_font_floor(job.svg_style_snapshot["readability_policy"]))
            rebound, preservation = bind_whole_page_source_svg(author_svg, source, index,
                facts=_whole_page_facts(pages[index], image_slots), width=canvas.width_px,
                height=canvas.height_px, style_snapshot=job.svg_style_snapshot, minimum_font_px=floor,
                _validated_pages=pages, native_declarations=declarations,
                restore_source_lf=True, raw_output_digest="sha256:" + sha256_hex(raw.encode()))
        except PartialRecoveryRejected:
            raise
        except (ValueError, TypeError, KeyError, IndexError) as error:
            raise PartialRecoveryRejected("svg_partial_recovery_source_transform_unproven") from error
        if rebound != payload["svg_content"] or preservation.get("source_lf_restoration") != restoration:
            reject("svg_partial_recovery_source_transform_unproven")
        return _source_author_render_witness(rebound, payload, raw, job=job, row=row, completed=completed)
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
        source_manifest=contract,
        header_page=job.authored_outline["slides"][index].get("common_header"))
    stored = payload["svg_content"]
    from utils.owner_context import get_current_owner_id, get_current_tenant_id

    historical_initial_bound = False
    if (stored == normalized and row is not None and row.render_status == "pending"
            and payload.get("initial_quality") not in (None, {})):
        attempts = payload.get("attempts") or []
        previous = attempts[-2] if len(attempts) == 2 else None
        previous_quality = previous.get("quality") if isinstance(previous, dict) else None
        initial = payload.get("initial_quality")
        prior_svg = payload.get("last_valid_svg")
        previous_raw = previous.get("raw_response") if isinstance(previous, dict) else None
        previous_receipt = previous.get("receipt") if isinstance(previous, dict) else None
        records = payload.get("provider_terminal_responses") or {}
        ordinal = _receipt_terminal_ordinal(previous_receipt, payload)
        terminal = records.get(str(ordinal)) if isinstance(records, dict) and type(ordinal) is int else None
        if (previous is not None and previous.get("kind") == "provider"
                and isinstance(previous_quality, dict) and isinstance(initial, dict)
                and initial == {**previous_quality, "stage": "first_response"}
                and isinstance(prior_svg, str) and prior_svg != stored
                and isinstance(previous_raw, str) and previous_raw.strip()
                and isinstance(previous_receipt, dict) and isinstance(terminal, dict)
                and terminal.get("schema") == "svg-provider-terminal/v1"
                and terminal.get("terminal_event") == "response.completed"
                and terminal.get("ordinal") == ordinal
                and terminal.get("raw_response") == previous_raw
                and terminal.get("output_digest") == sha256_hex(previous_raw.encode())
                and str(previous_receipt.get("raw_digest", "")).removeprefix("sha256:")
                    == terminal["output_digest"]
                and terminal.get("job_id") == str(job.id)
                and terminal.get("operation_id") == str(job.workspace_operation_id)
                and terminal.get("owner_id") == str(get_current_owner_id())
                and terminal.get("tenant_id") == get_current_tenant_id()
                and terminal.get("input_digest") == payload.get("input_digest")
                and terminal.get("receipt_digest") == digest({
                    key: value for key, value in terminal.items() if key != "receipt_digest"})):
            try:
                prior_root, _ = parse_author_response(previous_raw, view=view, source_facts=view_facts)
                if vectors:
                    from services.generation_core.drivers.svg.embed_assets import replace_vector_icon_images, vector_icon_id_prefix
                    replace_vector_icon_images(prior_root, vectors, id_prefix=vector_icon_id_prefix(job.svg_style_snapshot))
                prior_author = ET.tostring(prior_root, encoding="unicode")
                _, prior_advisories = normalize_model_font_families(prior_author)
                historical = {**payload, "svg_content": prior_svg,
                    "source_digest": sha256_hex(prior_svg.encode()),
                    "quality": previous_quality, "fontAdvisories": prior_advisories,
                    "png_sha256": None}
                prior_chain = _author_render_chain(historical, previous_raw, previous_receipt,
                    request=request, job=job, asset=asset, index=index, vectors=vectors,
                    vector_seed_witness=vector_seed_witness)
                historical_initial_bound = (prior_chain["render_svg_sha256"]
                    == sha256_hex(prior_svg.encode())
                    == str(initial.get("source_digest", "")).removeprefix("sha256:"))
            except (ValueError, TypeError, KeyError, ET.ParseError):
                historical_initial_bound = False
    if stored == normalized and row is not None and row.render_status == "pending":
        terminal = completed or {}
        unsigned = {key: value for key, value in terminal.items() if key != "receipt_digest"}
        if (row.author_status == "completed" and row.persist_status == "pending"
                and row.render_path is None and row.render_sha256 is None
                and payload.get("quality") in (None, {})
                and (payload.get("initial_quality") in (None, {}) or historical_initial_bound)
                and payload.get("png_sha256") is None and payload.get("png_path") is None
                and payload.get("source_digest") in (None, sha256_hex(stored.encode()), "sha256:" + sha256_hex(stored.encode()))
                and (payload.get("last_valid_svg") in (None, stored) or historical_initial_bound)
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
            source_manifest=contract,
            header_page=job.authored_outline["slides"][index].get("common_header"),
            allow_existing_common_header=True)
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
        from services.svg_presentation_checkpoint import quality_browser_metrics
        try:
            metrics = quality_browser_metrics(quality)
        except ValueError:
            reject("svg_partial_recovery_render_metrics_unbound")
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
    baseline_payloads: dict = field(default_factory=dict)
    # One transaction only; never serialized into the episode or archive.
    verification: _EpisodeVerification | None = field(default=None, repr=False, compare=False)


async def _pre_dispatch_repair_baseline(payload, row, *, job, operation, seed,
                                        episode_calls):
    """Project an approved prior drawing without changing the failed source row."""
    marker = payload.get("quality_recovery") or {}
    if (not isinstance(marker, dict) or marker.get("status") != "repair_pending"
            or payload.get("svg_content") is not None
            or row.author_status not in {"running", "failed"}
            or row.render_status != "completed" or row.persist_status != "pending"):
        return None
    if (payload.get("inflight") is not None or _unknown_provider(payload)
            or payload.get("provider_terminal_responses")
            or any(call.stage in {"author", "repair", "repair_remake", "repair_patch"}
                   and call.slide_index in {None, row.slide_index} for call in episode_calls)):
        return None
    from types import SimpleNamespace
    from services.generation_core.drivers.svg.partial_quality_recovery import current_quality_repair_authorized
    from services.generation_core.drivers.svg.quality_recovery import _restore_candidate
    from services.generation_core.drivers.svg.quality import _gate_intact
    from services.svg_presentation_checkpoint import _read_png

    prior_manifest = operation.request_payload.get("svg_partial_recovery_manifest")
    execution = seed.get("quality_recovery_execution") or {}
    if not isinstance(prior_manifest, dict) or not isinstance(execution, dict):
        reject("svg_partial_recovery_repair_baseline_unproven")
    ctx = SimpleNamespace(partial_recovery_manifest=prior_manifest,
        snapshot=job.svg_style_snapshot, count=job.total_slides,
        quality_recovery_runtime_binding=execution.get("binding"))

    async def read_checkpoint(read_job, index, **changes):
        if read_job.id != job.id or index != 0 or changes:
            reject("svg_partial_recovery_repair_baseline_unproven")
        return deepcopy(seed)

    try:
        if not await current_quality_repair_authorized(ctx, payload, row.slide_index,
                                                      job, read_checkpoint):
            reject("svg_partial_recovery_repair_baseline_unproven")
        restored = _restore_candidate(marker["prior_candidate"], payload, job)
        prior_page = prior_manifest["pages"][row.slide_index]
        gate = _gate_intact(execution["rounds"][0]["advisory"])
        png = _read_png(restored)
        svg = restored.get("svg_content")
        if (gate is None or not isinstance(svg, str) or not svg
                or payload.get("last_valid_svg") != svg
                or payload.get("input_digest") != restored.get("input_digest")
                or restored.get("input_digest") != prior_page.get("input_digest")
                or author_witness(restored) != prior_page.get("author_receipt_digest")
                or sha256_hex(svg.encode()) != prior_page.get("svg_sha256")
                or png is None or sha256_hex(png) != restored.get("png_sha256")
                or restored.get("png_sha256") != gate["pngDigests"][row.slide_index]
                or (restored.get("quality") or {}).get("passed") is not True
                or (restored.get("quality") or {}).get("measurement_complete") is not True):
            reject("svg_partial_recovery_repair_baseline_unproven")
    except PartialRecoveryRejected:
        raise
    except (OSError, ValueError, TypeError, KeyError, IndexError) as error:
        raise PartialRecoveryRejected("svg_partial_recovery_repair_baseline_unproven") from error
    proof = {"schema": "svg-pre-dispatch-repair-baseline/v1",
        "source_operation_id": str(operation.id),
        "source_payload_digest": digest(payload), "prior_candidate_digest": digest(marker["prior_candidate"]),
        "source_manifest_hash": digest(prior_manifest), "approved_gate_digest": gate["digest"],
        "author_receipt_digest": author_witness(restored), "svg_sha256": sha256_hex(svg.encode()),
        "png_sha256": restored["png_sha256"], "input_digest": restored["input_digest"],
        "current_episode_calls_digest": digest([_dump(call) for call in episode_calls]),
        "current_author_repair_calls": 0}
    return restored, proof


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


def _completed_repair_guard_reject(condition, index):
    error = PartialRecoveryRejected("svg_partial_recovery_completed_repair_unproven")
    error.guard_condition = condition
    error.guard_slide_index = index
    raise error


def _replay_sealed_author_transform(payload, raw, *, request, job, asset, index, row=None, completed=None, vectors=None, vector_seed_witness=None, source=None, _source_validation=None, source_projection=None):
    from services.svg_contract import normalize_svg_response
    raw_svg = normalize_svg_response(raw)
    if not isinstance(raw_svg, str) or not raw_svg.strip() or "<!" in raw_svg or "<?" in raw_svg:
        reject("svg_partial_recovery_completed_raw_invalid")
    receipt = {}
    'Replay the existing local transforms; hashes alone never authorize bytes.'
    import xml.etree.ElementTree as ET
    from services.authored_canvas import request_canvas_contract
    from services.svg_contract import normalize_svg_response, finalize_svg
    from services.svg_default_fonts import normalize_model_font_families
    from services.generation_core.drivers.svg.native_declarations import extract_native_declarations
    canvas = request_canvas_contract(request)
    offered = next((page.slot_ids for page in asset.offered_pages if page.page_index == index), ())
    slots = {slot.slot_id: slot for slot in asset.slots if slot.slot_id in offered}
    page_assets = {key: slot.model_dump(mode='json', by_alias=True) for key, slot in slots.items()}
    expected_input = _expected_page_input_digest(request, job, index, page_assets)
    if request.source_mode is None and payload.get('input_digest') != expected_input:
        reject('svg_partial_recovery_page_input_changed')
    if request.source_mode == 'source_content_restyle' and (source is None or source_projection is None):
        reject('svg_partial_recovery_source_transform_unproven')
    vectors = vectors or {}
    vector_offer = (payload.get('assetOffer') or {}).get('vectorIcons') or {}
    recorded_used = receipt.get('vector_icons_used') or []
    if (vectors or recorded_used or vector_offer.get('offered')) and vector_seed_witness is None:
        reject('svg_partial_recovery_vector_transform_unproven')
    from services.svg_author_output_view import parse_author_response
    view, view_facts = _replay_output_view(job, source, index, _source_validation)
    try:
        root, view_receipt = parse_author_response(raw, view=view, source_facts=view_facts)
    except ET.ParseError as error:
        raise PartialRecoveryRejected('svg_partial_recovery_author_transform_unproven') from error
    declarations = extract_native_declarations(root)
    from services.generation_core.drivers.svg.embed_assets import replace_vector_icon_images, vector_icon_id_prefix
    used = replace_vector_icon_images(root, vectors, id_prefix=vector_icon_id_prefix(job.svg_style_snapshot)) if vectors else set()
    for node in root.iter():
        if node.tag.rsplit('}', 1)[-1] == 'image' and node.get('href') not in slots:
            reject('svg_partial_recovery_author_asset_unproven')
    author_svg = ET.tostring(root, encoding='unicode')
    if request.source_mode == 'source_content_restyle':
        from services.svg_source_layout import bind_whole_page_source_svg
        from services.generation_core.authoring.author_source_svg_layout import _whole_page_facts
        from services.document_canvas_generation import source_restyle_font_floor, svg_quality_font_floor
        try:
            pages = _recovery_source_pages(source, job, _source_validation)
            image_slots = {}
            for block in pages[index].blocks:
                if block.kind != 'image':
                    continue
                try:
                    occurrence = uuid.UUID(block.source_block_id)
                except ValueError:
                    occurrence = uuid.uuid5(asset.presentation_id, f'source-occurrence:{block.source_block_id}')
                matches = [slot.slot_id for slot in slots.values() if slot.occurrence_id == occurrence]
                if len(matches) != 1:
                    reject('svg_partial_recovery_source_transform_unproven')
                image_slots[block.source_block_id] = matches[0]
            floor = source_restyle_font_floor(request.instructions, getattr(request, '_document_canvas', None))
            if job.svg_style_snapshot.get('readability_policy') is not None:
                floor = max(floor, svg_quality_font_floor(job.svg_style_snapshot['readability_policy']))
            rebound, preservation = bind_whole_page_source_svg(author_svg, source, index,
                facts=_whole_page_facts(pages[index], image_slots), width=canvas.width_px,
                height=canvas.height_px, style_snapshot=job.svg_style_snapshot, minimum_font_px=floor,
                _validated_pages=pages, native_declarations=declarations,
                restore_source_lf=True, raw_output_digest='sha256:' + sha256_hex(raw.encode()))
        except PartialRecoveryRejected:
            raise
        except (ValueError, TypeError, KeyError, IndexError) as error:
            raise PartialRecoveryRejected('svg_partial_recovery_source_transform_unproven') from error
        source_projection['source_preservation'] = deepcopy(preservation)
        if view_receipt is not None:
            # The producer's receipt entry, recomputed from the same raw and pinned view.
            source_projection['output_view'] = view_receipt
        return rebound, rebound, sha256_hex(rebound.encode()), declarations, used, []
    author_digest = sha256_hex(author_svg.encode())
    normalized, advisories = normalize_model_font_families(author_svg)
    contract = {key: {'required': slot.policy.required, 'allowOmit': slot.policy.allow_omit} for key, slot in slots.items()}
    first = finalize_svg(normalized, index=index, n=job.total_slides, width=canvas.width_px, height=canvas.height_px, style_snapshot=job.svg_style_snapshot, source_manifest=contract,
        header_page=job.authored_outline["slides"][index].get("common_header"))
    return (normalized, first, author_digest, declarations, used, advisories)


async def _completed_repair_author_projection(payload, row, *, job, operation, seed, episode_calls, completed,
                                              source=None, _source_validation=None):
    """Separate a sealed current repair author output from its approved prior render."""
    marker = payload.get('quality_recovery') or {}
    if not isinstance(marker, dict) or marker.get('status') != 'repair_pending' or row.author_status != 'completed' or (row.render_status != 'completed') or (row.persist_status != 'pending') or (not isinstance(payload.get('svg_content'), str)) or (not payload['svg_content'].strip()) or (not isinstance(completed, dict)):
        return None
    attempts = payload.get('attempts')
    if not isinstance(attempts, list) or not attempts or (not isinstance(attempts[-1], dict)) or (attempts[-1].get('kind') != 'provider') or (attempts[-1].get('raw_response') != completed.get('raw_response')) or (not isinstance(attempts[-1].get('receipt'), dict)) or (str(attempts[-1]['receipt'].get('raw_digest') or '').removeprefix('sha256:') != completed.get('output_digest')):
        return None
    current_svg_digest = sha256_hex(payload["svg_content"].encode())
    if payload.get("source_digest") in (current_svg_digest, "sha256:" + current_svg_digest):
        return None  # Keep the existing known-finalized validator authoritative.
    from types import SimpleNamespace
    from services.generation_core.drivers.svg.partial_quality_recovery import current_quality_repair_authorized, _history_prefix
    from services.generation_core.drivers.svg.quality_recovery import _restore_candidate
    from services.generation_core.drivers.svg.quality import _gate_intact
    from services.svg_presentation_checkpoint import _read_png
    prior_manifest = operation.request_payload.get('svg_partial_recovery_manifest')
    execution = seed.get('quality_recovery_execution') or {}
    if not isinstance(prior_manifest, dict):
        _completed_repair_guard_reject(1, row.slide_index)
    if not isinstance(execution, dict):
        _completed_repair_guard_reject(2, row.slide_index)
    ctx = SimpleNamespace(partial_recovery_manifest=prior_manifest, snapshot=job.svg_style_snapshot, count=job.total_slides, quality_recovery_runtime_binding=execution.get('binding'))

    async def read_checkpoint(read_job, index, **changes):
        if read_job.id != job.id:
            _completed_repair_guard_reject(3, row.slide_index)
        if index != 0:
            _completed_repair_guard_reject(4, row.slide_index)
        if changes:
            _completed_repair_guard_reject(5, row.slide_index)
        return deepcopy(seed)
    try:
        if not await current_quality_repair_authorized(ctx, payload, row.slide_index, job, read_checkpoint):
            _completed_repair_guard_reject(6, row.slide_index)
        restored = _restore_candidate(marker['prior_candidate'], payload, job)
        restored['attempts'] = deepcopy(payload.get('attempts', [])[:_history_prefix(ctx, payload, row.slide_index)])
        prior_page = prior_manifest['pages'][row.slide_index]
        gate = _gate_intact(execution['rounds'][0]['advisory'])
        png = _read_png(restored)
        svg = restored.get('svg_content')
        if gate is None:
            _completed_repair_guard_reject(7, row.slide_index)
        if not isinstance(svg, str):
            _completed_repair_guard_reject(8, row.slide_index)
        if not svg:
            _completed_repair_guard_reject(9, row.slide_index)
        if payload.get('last_valid_svg') not in (svg, payload['svg_content']):
            _completed_repair_guard_reject(10, row.slide_index)
        if payload.get('input_digest') != restored.get('input_digest'):
            _completed_repair_guard_reject(11, row.slide_index)
        if restored.get('input_digest') != prior_page.get('input_digest'):
            _completed_repair_guard_reject(12, row.slide_index)
        if author_witness(restored) != prior_page.get('author_receipt_digest'):
            _completed_repair_guard_reject(13, row.slide_index)
        if sha256_hex(svg.encode()) != prior_page.get('svg_sha256'):
            _completed_repair_guard_reject(14, row.slide_index)
        if png is None:
            _completed_repair_guard_reject(15, row.slide_index)
        if sha256_hex(png) != restored.get('png_sha256'):
            _completed_repair_guard_reject(16, row.slide_index)
        if restored.get('png_sha256') != gate['pngDigests'][row.slide_index]:
            _completed_repair_guard_reject(17, row.slide_index)
        if (restored.get('quality') or {}).get('passed') is not True:
            _completed_repair_guard_reject(18, row.slide_index)
        if (restored.get('quality') or {}).get('measurement_complete') is not True:
            _completed_repair_guard_reject(19, row.slide_index)
    except PartialRecoveryRejected:
        raise
    except (OSError, ValueError, TypeError, KeyError, IndexError) as error:
        raise PartialRecoveryRejected('svg_partial_recovery_completed_repair_unproven') from error
    from services.generation_core.drivers.svg.native_declarations import bound_declarations, extract_native_declarations
    from services.svg_author_output_view import parse_author_response
    view, view_facts = _replay_output_view(job, source, row.slide_index, _source_validation)
    native = extract_native_declarations(parse_author_response(
        completed['raw_response'], view=view, source_facts=view_facts)[0])
    bound, binding_valid = bound_declarations(payload, payload['svg_content'])
    if (native is None) != (bound is None) or (native is not None and (not binding_valid or not isinstance(bound, dict) or native.get('objects') != bound.get('objects') or (native.get('dropped') != bound.get('dropped')))):
        reject('svg_partial_recovery_completed_native_unproven')
    if payload.get('source_digest') not in (sha256_hex(svg.encode()), 'sha256:' + sha256_hex(svg.encode())):
        _completed_repair_guard_reject(20, row.slide_index)
    if payload.get('png_sha256') != restored.get('png_sha256'):
        _completed_repair_guard_reject(21, row.slide_index)
    proof = {'schema': 'svg-completed-repair-author-projection/v1', 'source_operation_id': str(operation.id), 'source_payload_digest': digest(payload), 'prior_candidate_digest': digest(marker['prior_candidate']), 'source_manifest_hash': digest(prior_manifest), 'approved_gate_digest': gate['digest'], 'author_receipt_digest': author_witness(restored), 'svg_sha256': sha256_hex(svg.encode()), 'png_sha256': restored['png_sha256'], 'input_digest': restored['input_digest'], 'current_episode_calls_digest': digest([_dump(call) for call in episode_calls]), 'current_author_repair_calls': sum((call.stage in ('author', 'repair', 'repair_remake', 'repair_patch') and call.slide_index in (None, row.slide_index) for call in episode_calls)), 'completed_receipt_digest': completed['receipt_digest'], 'completed_output_digest': completed['output_digest'], 'current_author_receipt_digest': author_witness(payload), 'current_author_svg_digest': sha256_hex(payload['svg_content'].encode())}
    projected = deepcopy(payload)
    for key in ('quality', 'initial_quality', 'png_path', 'png_sha256', 'browser_metrics', 'local_readability_repair'):
        projected.pop(key, None)
    projected['source_digest'] = None
    projected['last_valid_svg'] = projected['svg_content']
    return (projected, proof)


async def _terminal_repair_author_projection(payload, row, *, job, operation, seed, episode_calls, completed, session, request, binding, vectors, vector_seed_witness, source=None, _source_validation=None):
    """Recover only sealed current terminal output using a separate local replay witness."""
    source_projection = await _completed_source_author_projection(payload, row, job=job,
        operation=operation, episode_calls=episode_calls, completed=completed, session=session,
        request=request, binding=binding, vectors=vectors, vector_seed_witness=vector_seed_witness,
        source=source, _source_validation=_source_validation)
    if source_projection is not None:
        return source_projection
    from utils.owner_context import get_current_owner_id, get_current_tenant_id
    owner, tenant = (get_current_owner_id(), get_current_tenant_id())
    if not owner or not tenant:
        reject('svg_partial_recovery_completed_owner_unproven')
    from services.svg_asset_persistence import load_source_manifest_pointer
    from services.artifact_store import configured_public_artifact_service
    from services.svg_presentation_checkpoint import _read_png
    from services.svg_asset_manifest import ArtifactPointer
    marker = payload.get('quality_recovery') or {}
    if not isinstance(marker, dict) or marker.get('status') != 'repair_pending' or row.author_status not in ('running', 'failed') or (row.render_status != 'completed') or (row.persist_status != 'pending') or (payload.get('svg_content') is not None) or (not isinstance(completed, dict)):
        return None
    if not completed["raw_response"].strip():
        return None
    from types import SimpleNamespace
    from services.generation_core.drivers.svg.partial_quality_recovery import current_quality_repair_authorized, _history_prefix
    from services.generation_core.drivers.svg.quality_recovery import _restore_candidate
    from services.generation_core.drivers.svg.quality import _gate_intact
    prior_manifest = operation.request_payload.get('svg_partial_recovery_manifest')
    execution = seed.get('quality_recovery_execution') or {}
    if not isinstance(prior_manifest, dict):
        _completed_repair_guard_reject(1, row.slide_index)
    if not isinstance(execution, dict):
        _completed_repair_guard_reject(2, row.slide_index)
    ctx = SimpleNamespace(partial_recovery_manifest=prior_manifest, snapshot=job.svg_style_snapshot, count=job.total_slides, quality_recovery_runtime_binding=execution.get('binding'))

    async def read_checkpoint(read_job, index, **changes):
        if read_job.id != job.id:
            _completed_repair_guard_reject(3, row.slide_index)
        if index != 0:
            _completed_repair_guard_reject(4, row.slide_index)
        if changes:
            _completed_repair_guard_reject(5, row.slide_index)
        return deepcopy(seed)
    try:
        if not await current_quality_repair_authorized(ctx, payload, row.slide_index, job, read_checkpoint):
            _completed_repair_guard_reject(6, row.slide_index)
        restored = _restore_candidate(marker['prior_candidate'], payload, job)
        restored['attempts'] = deepcopy(payload.get('attempts', [])[:_history_prefix(ctx, payload, row.slide_index)])
        prior_page = prior_manifest['pages'][row.slide_index]
        gate = _gate_intact(execution['rounds'][0]['advisory'])
        png = _read_png(restored)
        svg = restored.get('svg_content')
        if gate is None:
            _completed_repair_guard_reject(7, row.slide_index)
        if not isinstance(svg, str):
            _completed_repair_guard_reject(8, row.slide_index)
        if not svg:
            _completed_repair_guard_reject(9, row.slide_index)
        if payload.get('last_valid_svg') != svg:
            _completed_repair_guard_reject(10, row.slide_index)
        if payload.get('input_digest') != restored.get('input_digest'):
            _completed_repair_guard_reject(11, row.slide_index)
        if restored.get('input_digest') != prior_page.get('input_digest'):
            _completed_repair_guard_reject(12, row.slide_index)
        if author_witness(restored) != prior_page.get('author_receipt_digest'):
            _completed_repair_guard_reject(13, row.slide_index)
        if sha256_hex(svg.encode()) != prior_page.get('svg_sha256'):
            _completed_repair_guard_reject(14, row.slide_index)
        if png is None:
            _completed_repair_guard_reject(15, row.slide_index)
        if sha256_hex(png) != restored.get('png_sha256'):
            _completed_repair_guard_reject(16, row.slide_index)
        if restored.get('png_sha256') != gate['pngDigests'][row.slide_index]:
            _completed_repair_guard_reject(17, row.slide_index)
        if (restored.get('quality') or {}).get('passed') is not True:
            _completed_repair_guard_reject(18, row.slide_index)
        if (restored.get('quality') or {}).get('measurement_complete') is not True:
            _completed_repair_guard_reject(19, row.slide_index)
    except PartialRecoveryRejected:
        raise
    except (OSError, ValueError, TypeError, KeyError, IndexError) as error:
        raise PartialRecoveryRejected('svg_partial_recovery_completed_repair_unproven') from error
    service = configured_public_artifact_service()
    pointer = ArtifactPointer.model_validate(payload.get('source_manifest_ref'))
    reference = await service.resolve_ref(session, artifact_id=pointer.ref, owner_id=owner, tenant_id=tenant, expected_digest=pointer.sha256)
    asset = await load_source_manifest_pointer(session, service, pointer=payload.get('source_manifest_ref'), owner_id=owner, tenant_id=tenant)
    current_binding = {'pointer': deepcopy(payload.get('source_manifest_ref')), 'digest': asset.digest, 'presentation_id': str(asset.presentation_id), 'source_revision': asset.source_revision, 'origin_operation_id': str(reference.created_by_operation_id)}
    if asset.digest != payload.get('source_manifest_digest') or binding not in (None, current_binding):
        reject('svg_partial_recovery_asset_binding_changed')
    binding = current_binding
    from services.svg_contract import SVG_CONTRACT_VERSION
    from services.generation_core.drivers.svg.native_declarations import bind_native_declarations, bound_declarations, declaration_receipt
    current_calls = [call for call in episode_calls if call.stage in ('author', 'repair', 'repair_remake', 'repair_patch') and call.slide_index == row.slide_index]
    models = {(call.provider_id, call.model_id) for call in current_calls}
    if not current_calls or len(models) != 1 or (not all((provider and model for provider, model in models))):
        reject('svg_partial_recovery_completed_model_unproven')
    provider, model = next(iter(models))
    source_projection = {}
    normalized, first, author_digest, declarations, used, advisories = _replay_sealed_author_transform(payload, completed['raw_response'], request=request, job=job, asset=asset, index=row.slide_index, row=row, completed=completed, vectors=vectors, vector_seed_witness=vector_seed_witness, source=source, _source_validation=_source_validation, source_projection=source_projection)
    projected = deepcopy(payload)
    projected['svg_content'] = normalized
    projected['fontAdvisories'] = advisories
    if isinstance(projected.get('assetOffer'), dict) and isinstance(projected['assetOffer'].get('vectorIcons'), dict):
        projected['assetOffer']['vectorIcons']['used'] = sorted(used)
    bind_native_declarations(projected, normalized, declarations)
    bound, native_valid = bound_declarations(projected, normalized)
    if declaration_receipt(bound) != declaration_receipt(declarations) or (declarations is not None and (not native_valid)):
        reject('svg_partial_recovery_completed_native_unproven')
    witness = {'schema': 'svg-author-terminal-local-replay/v1', 'completed_receipt_digest': completed['receipt_digest'], 'completed_output_digest': completed['output_digest'], 'source_operation_id': str(operation.id), 'source_job_id': str(job.id), 'input_digest': payload['input_digest'], 'current_calls_digest': digest([_dump(call) for call in current_calls]), 'provider': provider, 'requested_model': model, 'first_render_svg_digest': sha256_hex(first.encode())}
    receipt = {'contract_version': SVG_CONTRACT_VERSION, 'provider': provider, 'requested_model': model, 'style_digest': job.svg_style_snapshot['digest'], 'authoring_profile': job.svg_style_snapshot.get('authoring_profile'), 'raw_digest': 'sha256:' + completed['output_digest'], 'source_digest': 'sha256:' + author_digest, 'native_declarations': declaration_receipt(declarations).get('native_declarations'), 'vector_icons_used': sorted(used), 'terminal_recovery_witness': witness}
    if source_projection:
        projected['source_preservation'] = deepcopy(source_projection['source_preservation'])
        receipt.update(source_preservation=deepcopy(source_projection['source_preservation']),
            source_authoring_contract=deepcopy(job.svg_style_snapshot.get('source_authoring_contract')))
        if source_projection.get('output_view') is not None:
            receipt['output_view'] = deepcopy(source_projection['output_view'])
    projected.setdefault('attempts', []).append({'kind': 'sealed_terminal_replay', 'raw_response': completed['raw_response'], 'receipt': receipt})
    for key in ('quality', 'initial_quality', 'png_path', 'png_sha256', 'browser_metrics', 'local_readability_repair'):
        projected.pop(key, None)
    projected['source_digest'] = None
    projected['last_valid_svg'] = normalized
    proof = {'schema': 'svg-terminal-repair-author-projection/v1', 'source_operation_id': str(operation.id), 'source_payload_digest': digest(payload), 'prior_candidate_digest': digest(marker['prior_candidate']), 'approved_gate_digest': digest(gate), 'completed_receipt_digest': completed['receipt_digest'], 'completed_output_digest': completed['output_digest'], 'current_author_witness_digest': author_witness(projected), 'current_author_svg_digest': sha256_hex(normalized.encode()), 'prior_svg_digest': sha256_hex(svg.encode()), 'prior_png_digest': restored['png_sha256'], 'input_digest': payload['input_digest'], 'terminal_recovery_witness': witness}
    return (projected, proof)


async def _completed_source_author_projection(payload, row, *, job, operation, episode_calls,
        completed, session, request, binding, vectors, vector_seed_witness,
        _selected_attempt_index=None, source=None, _source_validation=None):
    """Project a valid v2 author-only response without changing historical receipts."""
    from services.svg_authoring_profile import read_quality_recovery_policy
    from utils.owner_context import get_current_owner_id, get_current_tenant_id
    policy = read_quality_recovery_policy(job.svg_style_snapshot)
    if ((policy or {}).get("version") != 2 or job.request_payload.get(LINEAGE_KEY)
            or getattr(operation, "error_code", None) != "svg_source_quality_failed"
            or row.author_status not in {"running", "failed"} or row.render_status != "pending"
            or row.persist_status != "pending" or payload.get("svg_content") is not None
            or completed is None):
        return None
    owner, tenant = get_current_owner_id(), get_current_tenant_id()
    attempts = payload.get("attempts")
    if (not owner or not tenant or operation.owner_id != owner
            or job.workspace_operation_id != operation.id
            or operation.request_payload.get("generation_request") != job.request_payload
            or payload.get("inflight") is not None or _unknown_provider(payload)
            or not isinstance(attempts, list) or not attempts
            or not isinstance(attempts[-1], dict) or attempts[-1].get("kind") != "provider"):
        reject("svg_partial_recovery_completed_author_unproven")
    selected_key = (str(completed["ordinal"]) if type(completed.get("ordinal")) is int
                    and completed["ordinal"] > 0 else "selected")
    terminal_payload = payload if _selected_attempt_index is None else {**payload,
        "provider_terminal_responses": {selected_key: completed}}
    if _completed_response(terminal_payload, job, operation, owner, tenant) != completed:
        reject("svg_partial_recovery_completed_author_unproven")
    attempt = attempts[-1] if _selected_attempt_index is None else attempts[_selected_attempt_index]
    receipt = attempt.get("receipt")
    raw = attempt.get("raw_response")
    if (not isinstance(receipt, dict) or receipt.get("validation_error") is not None
            or not isinstance(raw, str) or not raw.strip() or raw != completed.get("raw_response")
            or str(receipt.get("raw_digest", "")).removeprefix("sha256:") != completed["output_digest"]
            or receipt.get("contract_version") not in {"svg-static/v1", "svg-static/v2"}
            or not receipt.get("provider") or not receipt.get("requested_model")
            or receipt.get("style_digest") != job.svg_style_snapshot.get("digest")
            or receipt.get("authoring_profile") != job.svg_style_snapshot.get("authoring_profile")):
        reject("svg_partial_recovery_completed_author_unproven")
    current_calls = [call for call in episode_calls if call.slide_index == row.slide_index
                     and call.stage in {"author", "repair", "repair_remake", "repair_patch"}]
    if (not current_calls or any(call.outcome != "succeeded" or call.reconciled_at is None
            or call.state not in {"reported", "unavailable"} for call in current_calls)
            or {(call.provider_id, call.model_id) for call in current_calls}
               != {(receipt["provider"], receipt["requested_model"])}):
        reject("svg_partial_recovery_completed_model_unproven")
    from services.artifact_store import configured_public_artifact_service
    from services.svg_asset_manifest import ArtifactPointer
    from services.svg_asset_persistence import load_source_manifest_pointer
    service = configured_public_artifact_service()
    pointer = ArtifactPointer.model_validate(payload.get("source_manifest_ref"))
    reference = await service.resolve_ref(session, artifact_id=pointer.ref,
        owner_id=owner, tenant_id=tenant, expected_digest=pointer.sha256)
    asset = await load_source_manifest_pointer(session, service,
        pointer=payload.get("source_manifest_ref"), owner_id=owner, tenant_id=tenant)
    current_binding = dict(pointer=deepcopy(payload["source_manifest_ref"]), digest=asset.digest,
        presentation_id=str(asset.presentation_id), source_revision=asset.source_revision,
        origin_operation_id=str(reference.created_by_operation_id))
    if asset.digest != payload.get("source_manifest_digest") or binding not in (None, current_binding):
        reject("svg_partial_recovery_asset_binding_changed")
    source_projection = {}
    try:
        normalized, first, author_digest, declarations, used, advisories = _replay_sealed_author_transform(
            payload, raw, request=request, job=job, asset=asset, index=row.slide_index,
            vectors=vectors, vector_seed_witness=vector_seed_witness, source=source,
            _source_validation=_source_validation, source_projection=source_projection)
    except ValueError as error:
        if isinstance(error, PartialRecoveryRejected):
            raise
        if _selected_attempt_index is None and str(error).startswith("svg_contract_"):
            # Inspect at most two earlier known responses; never repair their bytes.
            for index in range(len(attempts) - 2, max(-1, len(attempts) - 4), -1):
                prior = attempts[index]
                if not isinstance(prior, dict) or prior.get("kind") != "provider":
                    continue
                records = {key: value for key, value in (payload.get("provider_terminal_responses") or {}).items()
                           if isinstance(value, dict) and value.get("raw_response") == prior.get("raw_response")}
                terminal = _completed_response({**payload, "provider_terminal_responses": records},
                                              job, operation, owner, tenant)
                if terminal is None:
                    continue
                try:
                    return await _completed_source_author_projection(payload, row, job=job,
                        operation=operation, episode_calls=episode_calls, completed=terminal,
                        session=session, request=request, binding=binding, vectors=vectors,
                        vector_seed_witness=vector_seed_witness, _selected_attempt_index=index,
                        source=source, _source_validation=_source_validation)
                except PartialRecoveryRejected:
                    continue
        raise PartialRecoveryRejected("svg_partial_recovery_local_output_requires_repair") from error
    if (str(receipt.get("source_digest", "")).removeprefix("sha256:") != author_digest
            or _selected_attempt_index is None and payload.get("source_digest") not in
                (None, author_digest, "sha256:" + author_digest,
                 sha256_hex(normalized.encode()), "sha256:" + sha256_hex(normalized.encode()))):
        reject("svg_partial_recovery_completed_author_unproven")
    if _selected_attempt_index is not None:
        quality = attempt.get("quality") or {}
        initial = payload.get("initial_quality") or {}
        previous = payload.get("last_valid_svg")
        from services.svg_presentation_checkpoint import quality_browser_metrics
        try:
            selected_metrics = quality_browser_metrics(quality)
        except ValueError:
            reject("svg_partial_recovery_completed_author_unproven")
        if (not isinstance(previous, str) or sha256_hex(previous.encode()) != sha256_hex(first.encode())
                or quality.get("measurement_complete") is not True
                or str(quality.get("source_digest", "")).removeprefix("sha256:") != sha256_hex(first.encode())
                or str(initial.get("source_digest", "")).removeprefix("sha256:") != sha256_hex(first.encode())
                or not isinstance(selected_metrics, dict)
                or not quality.get("metrics_digest") or not quality.get("png_digest") or not quality.get("wrapper_digest")
                or type(receipt.get("attempt_ordinal")) is not int):
            reject("svg_partial_recovery_completed_author_unproven")
        historical = {**payload, "svg_content": previous, "source_digest": quality["source_digest"],
            "last_valid_svg": previous, "quality": quality, "initial_quality": initial,
            "fontAdvisories": advisories, "png_sha256": str(quality["png_digest"]).removeprefix("sha256:")}
        from types import SimpleNamespace
        try:
            historical_chain = _author_render_chain(historical, raw, receipt, request=request,
                job=job, asset=asset, index=row.slide_index, row=SimpleNamespace(
                    author_status="completed", render_status="completed", persist_status="pending"),
                completed=completed, vectors=vectors, vector_seed_witness=vector_seed_witness)
        except (ValueError, TypeError, KeyError) as error:
            raise PartialRecoveryRejected("svg_partial_recovery_local_output_requires_repair") from error
        if historical_chain["render_svg_sha256"] != sha256_hex(previous.encode()):
            reject("svg_partial_recovery_completed_author_unproven")
    projected = deepcopy(payload)
    projected.update(svg_content=normalized, last_valid_svg=normalized, source_digest=None, fontAdvisories=advisories)
    if source_projection:
        if (receipt.get('source_preservation') != source_projection['source_preservation']
                or receipt.get('output_view') != source_projection.get('output_view')):
            reject('svg_partial_recovery_source_transform_unproven')
        projected['source_preservation'] = deepcopy(source_projection['source_preservation'])
    for key in ("quality", "initial_quality", "png_path", "png_sha256", "browser_metrics", "local_readability_repair"):
        projected.pop(key, None)
    from services.generation_core.drivers.svg.native_declarations import (
        bind_native_declarations, bound_declarations, declaration_receipt,
    )
    bind_native_declarations(projected, normalized, declarations)
    bound, native_valid = bound_declarations(projected, normalized)
    if declaration_receipt(bound) != declaration_receipt(declarations) or declarations is not None and not native_valid:
        reject("svg_partial_recovery_completed_native_unproven")
    from types import SimpleNamespace
    witness = _author_render_chain(projected, raw, receipt, request=request, job=job, asset=asset,
        index=row.slide_index, row=SimpleNamespace(author_status="completed", render_status="pending",
            persist_status="pending", render_path=None, render_sha256=None),
        completed=completed, vectors=vectors, vector_seed_witness=vector_seed_witness,
        source=source, _source_validation=_source_validation)
    if witness.get("phase") != "author-only":
        reject("svg_partial_recovery_completed_author_unproven")
    if _selected_attempt_index is not None:
        terminal_witness = dict(schema="svg-author-terminal-local-replay/v1",
            completed_receipt_digest=completed["receipt_digest"], completed_output_digest=completed["output_digest"],
            source_operation_id=str(operation.id), source_job_id=str(job.id), input_digest=payload["input_digest"],
            current_calls_digest=digest([_dump(call) for call in current_calls]),
            provider=receipt["provider"], requested_model=receipt["requested_model"],
            first_render_svg_digest=sha256_hex(first.encode()), attempt_ordinal=receipt["attempt_ordinal"])
        projected["attempts"].append(dict(kind="sealed_terminal_replay", raw_response=raw,
            receipt={**deepcopy(receipt), "terminal_recovery_witness": terminal_witness}))
    return projected, witness




async def _verified_retained_source_author(payload, row, *, manifest, operation, session, owner, tenant, seen=frozenset(), seal_memo=None):
    from types import SimpleNamespace
    from uuid import UUID
    pages = manifest.get("pages", [])
    page = pages[row.slide_index] if isinstance(pages, list) and 0 <= row.slide_index < len(pages) else None
    proof = page.get("retained_source_selection") if isinstance(page, dict) else None
    if proof is None:
        return None
    code = "svg_partial_recovery_retained_source_unproven"
    if not isinstance(proof, dict) or proof.get("schema") != "svg-retained-quality-source/v1":
        reject(code)
    pending_cache = (
        row.author_status == "completed" and row.render_status == "pending" and row.persist_status == "pending"
        and payload.get("png_path") is None and payload.get("png_sha256") is None
        and payload.get("quality") is None and payload.get("initial_quality") is None
        and payload.get("local_readability_repair") is None
    )
    if pending_cache:
        verify_cached_page(payload, manifest, row.slide_index, payload.get("input_digest"))
    attempts = payload.get("attempts")
    prefix = proof.get("author_history_prefix")
    if (not isinstance(attempts, list) or type(prefix) is not int or not 0 < prefix <= len(attempts)
            or author_witness(payload) != proof.get("author_receipt_digest")
            or author_witness({"attempts": attempts[:prefix]}) != proof.get("prior_author_receipt_digest")
            or sha256_hex(payload.get("svg_content", "").encode()) != proof.get("svg_sha256")
            or (not pending_cache and payload.get("png_sha256", "").removeprefix("sha256:") != proof.get("png_sha256", "").removeprefix("sha256:"))):
        reject(code)
    producer_id = proof.get("selection_operation_id")
    if not isinstance(producer_id, str) or producer_id in seen:
        reject(code)
    seen = seen | {producer_id}
    carrier = operation
    carrier_manifest = manifest
    carrier_archive = carrier.request_payload.get(ARCHIVE_KEY)
    visited = set()
    original_payload = original_row = original_job = producer = original_seed = None
    while True:
        if str(carrier.id) in visited:
            reject(code)
        visited.add(str(carrier.id))
        if (not isinstance(carrier_archive, dict)
                or digest(carrier_manifest) != carrier.request_payload.get("svg_partial_recovery_manifest_hash")
                or digest(carrier_archive) != carrier_manifest.get("archive_digest")
                or carrier_archive.get("schema") != carrier_manifest.get("archive_schema")):
            reject(code)
        references = carrier_archive.get("operation_refs")
        if not isinstance(references, list):
            reject(code)
        await _verify_operation_references(session, carrier, carrier_manifest, references, seal_memo=seal_memo)
        source_id = carrier_manifest.get("operation_id")
        if (source_id is None or str(carrier.retry_of_operation_id) != source_id
                or not any(item.get("id") == source_id for item in references)):
            reject(code)
        working_archive = await _hydrate_checkpoint_archive(session, carrier_manifest, carrier_archive,
            verification=_seal_memo_verification(seal_memo))
        snapshot = working_archive.get("job")
        checkpoints = working_archive.get("checkpoints")
        if not isinstance(snapshot, dict) or not isinstance(checkpoints, list):
            reject(code)
        if source_id == producer_id:
            producer = await session.get(WorkspaceOperation, UUID(source_id))
            original_row = next((item for item in checkpoints if item.get("slide_index") == row.slide_index), None)
            seed_row = next((item for item in checkpoints if item.get("slide_index") == 0), None)
            if (producer is None or not isinstance(original_row, dict) or not isinstance(seed_row, dict)
                    or str(snapshot.get("workspace_operation_id")) != producer_id
                    or str(snapshot.get("presentation_id")) != str(row.presentation_id if hasattr(row, "presentation_id") else operation.presentation_id)):
                reject(code)
            original_payload = original_row.get("svg_checkpoint")
            original_seed = seed_row.get("svg_checkpoint")
            if (not isinstance(original_payload, dict) or not isinstance(original_seed, dict)
                    or digest(original_payload) != proof.get("source_payload_digest")
                    or digest(producer.request_payload.get("svg_partial_recovery_manifest")) != proof.get("source_manifest_hash")):
                reject(code)
            original_job = SimpleNamespace(**snapshot)
            break
        carrier = await session.get(WorkspaceOperation, UUID(source_id))
        if carrier is None:
            reject(code)
        carrier_manifest = carrier.request_payload.get("svg_partial_recovery_manifest")
        carrier_archive = carrier.request_payload.get(ARCHIVE_KEY)
        if not isinstance(carrier_manifest, dict):
            reject(code)
    completed = _completed_response(original_payload, original_job, producer, owner, tenant)
    replay = await _retained_quality_source_author(original_payload, SimpleNamespace(**original_row), job=original_job,
        operation=producer, seed=original_seed, completed=completed, session=session, owner=owner, tenant=tenant, retained_seen=seen, seal_memo=seal_memo)
    if replay is None or digest(replay[2]) != digest(proof):
        reject(code)
    source_attempt = attempts[prefix - 1]
    if (not isinstance(source_attempt, dict) or source_attempt.get("raw_response") != replay[0]
            or source_attempt.get("receipt") != replay[1]):
        reject(code)
    return replay


def _v2_retained_comparison_matches(selection, decision, prior_gate, candidate_gate, index, input_digest):
    from services.generation_core.quality.advisory_record import content_digest
    from services.generation_core.quality.vision_advisory_prompt import DESIGN_AXES

    if any((gate.get("policy") or {}).get("version") != 2 for gate in (prior_gate, candidate_gate)):
        return False
    if any(gate.get("digest") != content_digest({k: v for k, v in gate.items() if k != "digest"}) for gate in (prior_gate, candidate_gate)):
        return False
    if any(prior_gate.get(key) != candidate_gate.get(key) for key in ("binding", "snapshotDigest", "runtimeBinding", "styleDigest", "exampleDigests")):
        return False
    old_page = next((item for item in prior_gate.get("pages", []) if item.get("index") == index), {})
    new_page = next((item for item in candidate_gate.get("pages", []) if item.get("index") == index), {})
    if not old_page.get("evaluated") or not new_page.get("evaluated") or new_page.get("mandatoryEvidenceValid") is not True:
        return False
    comparisons = [item for item in candidate_gate.get("candidateComparisons", []) if item.get("index") == index]
    if len(comparisons) != 1:
        return False
    paired = comparisons[0]
    targets = decision.get("target_axes") or []
    goals = decision.get("design_targets") or []
    if not targets or not goals or any(name not in DESIGN_AXES for name in targets):
        return False
    if (paired.get("evaluated") is not True
            or paired.get("digest") != content_digest({k: v for k, v in paired.items() if k != "digest"})
            or paired.get("targetAxes") != targets or paired.get("targetGoals") != goals
            or paired.get("priorInputDigest") != input_digest
            or paired.get("priorMandatoryEvidenceValid") is not True
            or paired.get("comparisonMode") != "joint-targets-current-source-context/v1"
            or not paired.get("comparisonCohortDigest")
            or paired.get("deckContextDigest") != candidate_gate.get("deckContextDigest")):
        return False
    for digest_field, gate, key in (
        ("priorPageDigest", prior_gate, "pageDigests"),
        ("priorPngDigest", prior_gate, "pngDigests"),
        ("candidatePageDigest", candidate_gate, "pageDigests"),
        ("candidatePngDigest", candidate_gate, "pngDigests"),
    ):
        values = gate.get(key) or []
        if not 0 <= index < len(values) or paired.get(digest_field) != values[index]:
            return False
    presentation = paired.get("blindedPresentation") or {}
    changes = paired.get("axisChanges") or {}
    prior_axes = paired.get("priorAxes")
    if (not isinstance(prior_axes, dict) or set(prior_axes) != set(DESIGN_AXES)
            or any(not isinstance(axis, dict) for axis in prior_axes.values())
            or set(changes) != set(DESIGN_AXES)
            or presentation.get("candidateLabel") not in {"A", "B"}
            or presentation.get("priorLabel") not in {"A", "B"}
            or presentation.get("candidateLabel") == presentation.get("priorLabel")
            or presentation.get("displayOrder") != ["A", "B"]
            or presentation.get("chronologyExposed") is not False or presentation.get("versionExposed") is not False
            or any(not isinstance(change, dict) or change.get("change") not in {"improved", "equivalent", "worsened", "unjudged"} for change in changes.values())):
        return False
    reason = "comparison_improvement_unjudged" if any(change.get("change") == "unjudged" for change in changes.values()) else (
        "target_goal_not_improved" if any(changes[name].get("change") != "improved" for name in targets) else None)
    return selection == {"selected": "prior", "reason": reason} and reason is not None


async def _retained_quality_source_author(payload: dict[str, Any], row: Any, *, job: Any, operation: Any, seed: dict[str, Any], completed: dict[str, Any] | None, session, owner, tenant, retained_seen=frozenset(), seal_memo=None):
    """Verify the retained source separately from the discarded candidate history."""
    marker = payload.get("quality_recovery")
    if not isinstance(marker, dict):
        return await _verified_retained_source_author(payload, row, manifest=operation.request_payload.get("svg_partial_recovery_manifest") or {}, operation=operation, session=session, owner=owner, tenant=tenant, seen=retained_seen, seal_memo=seal_memo)
    if marker.get("status") != "retained":
        return None
    selection = marker.get("selection")
    allowed_reasons = {
        "target_defect_not_resolved", "candidate_major_regression",
        "candidate_mandatory_evidence_invalid", "comparison_evidence_stale",
        "comparison_runtime_or_style_changed", "comparison_review_incomplete",
        "candidate_design_evidence_invalid", "comparison_source_stale",
        "comparison_render_stale", "no_targeted_defect",
        "baseline_deck_context_review_required", "comparison_binding_invalid",
    }
    v2_reasons = {"comparison_improvement_unjudged", "target_goal_not_improved"}
    allowed_reasons |= v2_reasons
    if (not isinstance(selection, dict) or set(selection) != {"selected", "reason"}
            or selection.get("selected") != "prior" or not isinstance(selection.get("reason"), str)
            or selection.get("reason") not in allowed_reasons
            or row.author_status != "completed" or row.render_status != "completed"
            or row.persist_status != "pending" or not isinstance(payload.get("svg_content"), str)):
        reject("svg_partial_recovery_repair_baseline_unproven")
    from services.generation_core.drivers.svg.partial_quality_recovery import _history_prefix
    from types import SimpleNamespace
    from services.generation_core.drivers.svg.partial_quality_recovery import current_quality_repair_authorized
    from services.generation_core.drivers.svg.quality_recovery import _restore_candidate
    from services.generation_core.drivers.svg.quality import _gate_intact
    from services.svg_presentation_checkpoint import _read_png

    prior_manifest = operation.request_payload.get("svg_partial_recovery_manifest")
    execution = seed.get("quality_recovery_execution") or {}
    if not isinstance(prior_manifest, dict) or not isinstance(execution, dict):
        reject("svg_partial_recovery_repair_baseline_unproven")
    ctx = SimpleNamespace(partial_recovery_manifest=prior_manifest,
        snapshot=job.svg_style_snapshot, count=job.total_slides,
        quality_recovery_runtime_binding=execution.get("binding"))

    async def read_checkpoint(read_job, index, **changes):
        if read_job.id != job.id or index != 0 or changes:
            reject("svg_partial_recovery_repair_baseline_unproven")
        return deepcopy(seed)

    try:
        if not await current_quality_repair_authorized(ctx, payload, row.slide_index,
                                                      job, read_checkpoint):
            reject("svg_partial_recovery_repair_baseline_unproven")
        restored = _restore_candidate(marker["prior_candidate"], payload, job)
        prefix = _history_prefix(ctx, payload, row.slide_index)
        restored["attempts"] = deepcopy(payload.get("attempts", [])[:prefix])
        prior_page = prior_manifest["pages"][row.slide_index]
        gate = _gate_intact(execution["rounds"][0]["advisory"])
        png = _read_png(restored)
        svg = restored.get("svg_content")
        if (gate is None or not isinstance(svg, str) or not svg
                or payload.get("last_valid_svg") != svg
                or payload.get("input_digest") != restored.get("input_digest")
                or restored.get("input_digest") != prior_page.get("input_digest")
                or author_witness(restored) != prior_page.get("author_receipt_digest")
                or sha256_hex(svg.encode()) != prior_page.get("svg_sha256")
                or png is None or sha256_hex(png) != restored.get("png_sha256")
                or restored.get("png_sha256") != gate["pngDigests"][row.slide_index]
                or (restored.get("quality") or {}).get("passed") is not True
                or (restored.get("quality") or {}).get("measurement_complete") is not True):
            reject("svg_partial_recovery_repair_baseline_unproven")
    except PartialRecoveryRejected:
        raise
    except (OSError, ValueError, TypeError, KeyError, IndexError) as error:
        raise PartialRecoveryRejected("svg_partial_recovery_repair_baseline_unproven") from error

    rounds = execution.get("rounds")
    if (not isinstance(rounds, list) or len(rounds) != 2
            or any(not isinstance(item, dict) or item.get("status") != "completed" for item in rounds)):
        reject("svg_partial_recovery_repair_baseline_unproven")
    candidate_gate = _gate_intact(rounds[1].get("advisory"))
    if (candidate_gate is None or any(gate.get(key) != candidate_gate.get(key)
            for key in ("binding", "policy", "snapshotDigest", "runtimeBinding", "styleDigest", "exampleDigests"))
            or len(candidate_gate.get("pageDigests", [])) != job.total_slides
            or len(candidate_gate.get("pngDigests", [])) != job.total_slides):
        reject("svg_partial_recovery_repair_baseline_unproven")
    if selection.get("reason") in v2_reasons and not _v2_retained_comparison_matches(
        selection, marker.get("decision") or {}, gate, candidate_gate, row.slide_index, restored.get("input_digest")
    ):
        reject("svg_partial_recovery_repair_baseline_unproven")
    attempts = payload.get("attempts")
    prior_attempts = restored.get("attempts")
    if (not isinstance(attempts, list) or not isinstance(prior_attempts, list)
            or not prior_attempts or prefix >= len(attempts) or completed is None
            or not isinstance(attempts[-1], dict) or not isinstance(attempts[-1].get("receipt"), dict)
            or not isinstance(attempts[-1].get("raw_response"), str)
            or completed.get("output_digest") != sha256_hex(attempts[-1]["raw_response"].encode())
            or attempts[-1]["receipt"].get("raw_digest") not in {
                completed["output_digest"], "sha256:" + completed["output_digest"]}):
        reject("svg_partial_recovery_repair_baseline_unproven")
    candidate_quality = attempts[-1].get("quality")
    candidate_digest = candidate_gate["pageDigests"][row.slide_index].removeprefix("sha256:")
    candidate_png_digest = candidate_gate["pngDigests"][row.slide_index].removeprefix("sha256:")
    if (not isinstance(candidate_quality, dict)
            or candidate_quality.get("source_digest") not in {candidate_digest, "sha256:" + candidate_digest}
            or candidate_quality.get("png_digest") not in {candidate_png_digest, "sha256:" + candidate_png_digest}
            or payload.get("svg_content") != restored.get("svg_content")
            or payload.get("png_sha256") != restored.get("png_sha256")):
        reject("svg_partial_recovery_repair_baseline_unproven")
    inherited = await _verified_retained_source_author(restored, row, manifest=prior_manifest, operation=operation,
        session=session, owner=owner, tenant=tenant, seen=retained_seen, seal_memo=seal_memo)
    if inherited is None:
        source_attempt = prior_attempts[-1]
        source_prior_digest = author_witness(restored)
    else:
        source_attempt = {"raw_response": inherited[0], "receipt": inherited[1]}
        prefix = inherited[2]["author_history_prefix"]
        source_prior_digest = inherited[2]["prior_author_receipt_digest"]
    if (not isinstance(source_attempt, dict) or not isinstance(source_attempt.get("raw_response"), str)
            or not isinstance(source_attempt.get("receipt"), dict)):
        reject("svg_partial_recovery_repair_baseline_unproven")
    proof = {
        "schema": "svg-retained-quality-source/v1",
        "selection_operation_id": str(operation.id),
        "source_manifest_hash": digest(prior_manifest),
        "source_payload_digest": digest(payload),
        "input_digest": payload.get("input_digest"),
        "prior_candidate_digest": digest(marker["prior_candidate"]),
        "prior_author_receipt_digest": source_prior_digest,
        "author_receipt_digest": author_witness(payload),
        "author_history_prefix": prefix,
        "selection_digest": digest(selection),
        "prior_gate_digest": gate["digest"],
        "candidate_gate_digest": candidate_gate["digest"],
        "svg_sha256": sha256_hex(svg.encode()),
        "png_sha256": restored["png_sha256"],
        "candidate_svg_sha256": candidate_digest,
        "candidate_png_sha256": candidate_gate["pngDigests"][row.slide_index],
    }
    return source_attempt["raw_response"], source_attempt["receipt"], proof


async def _inherited_terminal_author_verified(payload, row, *, manifest, manifest_archive, receipt, session, owner, tenant, operation, seal_memo=None):
    from services.svg_recovery_witness import author_witness
    witness = receipt.get("terminal_recovery_witness")
    marker = payload.get("partial_recovery")
    if not isinstance(manifest, dict) or not isinstance(witness, dict) or not isinstance(marker, dict):
        _completed_repair_guard_reject(40, row.slide_index)
    page = next((p for p in manifest.get("pages", []) if p.get("slide_index") == row.slide_index), None)
    if not isinstance(page, dict) or page.get("category") != "known" or witness.get("schema") != "svg-author-terminal-local-replay/v1" or witness.get("source_job_id") != manifest.get("job_id") or marker.get("manifest_hash") != digest(manifest) or marker.get("slide_index") != row.slide_index or marker.get("source_row_digest") != page.get("row_digest") or payload.get("input_digest") != page.get("input_digest") or author_witness(payload) != page.get("author_receipt_digest"):
        _completed_repair_guard_reject(41, row.slide_index)
    from types import SimpleNamespace
    archive = manifest_archive
    if not isinstance(archive, dict) or digest(archive) != manifest.get("archive_digest") or archive.get("schema") != manifest.get("archive_schema"):
        _completed_repair_guard_reject(42, row.slide_index)
    references = archive.get("operation_refs")
    await _verify_operation_references(session, operation, manifest, references, seal_memo=seal_memo)
    source_id = witness.get("source_operation_id")
    allowed_ids = {r.get("id") for r in references if isinstance(r, dict)}
    if source_id not in allowed_ids:
        _completed_repair_guard_reject(45, row.slide_index)
    seen = set()
    hop_limit = len(references)
    while manifest.get("operation_id") != source_id:
        carrier_id = manifest.get("operation_id")
        if carrier_id in seen or len(seen) >= hop_limit or carrier_id not in allowed_ids:
            _completed_repair_guard_reject(46, row.slide_index)
        seen.add(carrier_id)
        carrier = await session.get(WorkspaceOperation, uuid.UUID(carrier_id))
        if carrier is None or carrier.owner_id != owner:
            _completed_repair_guard_reject(47, row.slide_index)
        manifest = carrier.request_payload.get("svg_partial_recovery_manifest")
        archive = carrier.request_payload.get(ARCHIVE_KEY)
        if not isinstance(manifest, dict) or not isinstance(archive, dict) or digest(manifest) != carrier.request_payload.get("svg_partial_recovery_manifest_hash") or digest(archive) != manifest.get("archive_digest") or archive.get("schema") != manifest.get("archive_schema") or str(carrier.retry_of_operation_id) != manifest.get("operation_id") or source_id not in {r.get("id") for r in archive.get("operation_refs", []) if isinstance(r, dict)}:
            _completed_repair_guard_reject(48, row.slide_index)
        await _verify_operation_references(session, carrier, manifest, archive.get("operation_refs"), seal_memo=seal_memo)
    archive = await _hydrate_checkpoint_archive(session, manifest, archive,
        verification=_seal_memo_verification(seal_memo))
    job_snapshot = archive.get("job")
    row_snapshot = next((r for r in archive.get("checkpoints", []) if r.get("slide_index") == row.slide_index), None)
    if not isinstance(job_snapshot, dict) or not isinstance(row_snapshot, dict) or str(job_snapshot.get("id")) != witness["source_job_id"] or str(job_snapshot.get("workspace_operation_id")) != witness["source_operation_id"]:
        _completed_repair_guard_reject(43, row.slide_index)
    prior_operation = await session.get(WorkspaceOperation, uuid.UUID(witness["source_operation_id"]))
    if prior_operation is None or prior_operation.owner_id != owner or str(prior_operation.presentation_id) != str(job_snapshot.get("presentation_id")) or not any(r.get("id") == str(prior_operation.id) for r in archive.get("operation_refs", [])):
        _completed_repair_guard_reject(44, row.slide_index)
    prior_job = SimpleNamespace(**job_snapshot)
    completed = _completed_response(row_snapshot.get("svg_checkpoint") or {}, prior_job, prior_operation, owner, tenant)
    raw = (payload.get("attempts") or [{}])[-1].get("raw_response")
    return bool(completed and completed.get("raw_response") == raw and completed.get("receipt_digest") == witness.get("completed_receipt_digest") and completed.get("output_digest") == witness.get("completed_output_digest") and witness.get("input_digest") == payload.get("input_digest"))


def _historical_uniform_grid_rejection(svg, preservation, page, *, operation, job):
    """Prove only the old equal-cell assumption rejected a strict authored grid."""
    carrier = getattr(operation, "request_payload", None) or {}
    manifest = carrier.get("svg_partial_recovery_manifest") or {}
    lineage = (job.request_payload or {}).get(LINEAGE_KEY) or {}
    old_code = manifest.get("code_digest")
    current_code = _code_digest()
    if (not _sha256_hex_valid(old_code) or old_code == current_code
            or digest(manifest) != carrier.get("svg_partial_recovery_manifest_hash")
            or lineage.get("schema") != "svg-partial-recovery-lineage/v2"
            or lineage != carrier.get("svg_partial_recovery_lineage")
            or lineage.get("manifest_hash") != digest(manifest)
            or lineage.get("operation_id") != str(operation.id)
            or lineage.get("job_id") != str(job.id)
            or str(getattr(operation, "retry_of_operation_id", None)) != manifest.get("operation_id")
            or carrier.get("generation_request") != job.request_payload):
        return None
    import xml.etree.ElementTree as ET
    from services.svg_source_native_tables import _evidence

    tables = []
    for record, _group, rows, cells, rects, _owned, xs, ys in _evidence(ET.fromstring(svg), preservation, page):
        frame = record["frame"]
        column_width = frame["width"] / len(rows[0])
        row_height = frame["height"] / len(rows)
        widths = [right - left for left, right in zip(xs, xs[1:], strict=False)]
        heights = [bottom - top for top, bottom in zip(ys, ys[1:], strict=False)]
        if not (any(abs(value - column_width) > .000001 for value in widths)
                or any(abs(value - row_height) > .000001 for value in heights)):
            continue
        # Reproduce only the former formula; the current module above proves
        # every shared edge, source span, cell identity and text membership.
        mismatch = None
        for (row, column_index, rs, cs, _cell), rect in zip(cells, rects, strict=False):
            actual = [float(rect.get(key)) for key in ("x", "y", "width", "height")]
            expected = [frame["x"] + column_index * column_width, frame["y"] + row * row_height,
                cs * column_width, rs * row_height]
            if any(abs(value - target) > .000001 for value, target in zip(actual, expected, strict=True)):
                mismatch = {"row": row, "column": column_index, "row_span": rs, "column_span": cs,
                    "actual": actual, "uniform_expected": expected}
                break
        if mismatch is not None:
            tables.append({"block_id": record["block_id"], "element_id": record["element_id"],
                "rows": len(rows), "columns": len(rows[0]), "cell_count": len(cells),
                "column_widths": widths, "row_heights": heights, "first_uniform_mismatch": mismatch})
    if not tables:
        return None
    return {"reason": "historical_uniform_grid_rejected", "historical_code_digest": old_code,
        "current_code_digest": current_code, "current_source_svg_sha256": sha256_hex(svg.encode()),
        "current_source_receipt_digest": digest(preservation), "tables": tables}


def _unresolved_provider_call(call):
    return (call.outcome == "unknown" or call.state in {"reserved", "uncertain"}
        or getattr(call, "actual_cost_microunits", None) is None and call.outcome != "succeeded")


def _require_known_source_page_accounting(*, episode_calls, usage, job, index, receipt, code):
    author_stages = {"author", "repair", "repair_remake", "repair_patch"}
    if (not episode_calls or not usage or any(
            not isinstance(call.stage, str) or not call.stage
            or not isinstance(call.provider_id, str) or not call.provider_id
            or not isinstance(call.model_id, str) or not call.model_id
            or call.stage in author_stages and (type(call.slide_index) is not int
                or not 0 <= call.slide_index < job.total_slides) for call in episode_calls)):
        reject(code)
    # Match the invalid-source contract: only another mapped author's UNKNOWN
    # is outside this page's settled-output proof. Preserve it in the ledger.
    unresolved_other_calls = [call for call in episode_calls if call.stage in author_stages
        and call.slide_index != index and _unresolved_provider_call(call)]
    known_calls = [call for call in episode_calls if not (
        call.stage in author_stages and call.slide_index != index
        and _unresolved_provider_call(call))]
    if any(call.state != "reported" or call.outcome != "succeeded" or call.reconciled_at is None
            or call.actual_cost_microunits is None for call in known_calls):
        reject(code)
    page_calls = [call for call in episode_calls if call.slide_index == index and call.stage in author_stages]
    if (not page_calls or {(call.provider_id, call.model_id) for call in page_calls}
            != {(receipt.get("provider"), receipt.get("requested_model"))}):
        reject(code)
    known_groups, unknown_groups = {}, {}
    for calls, groups in ((known_calls, known_groups), (unresolved_other_calls, unknown_groups)):
        for call in calls:
            if call.provider_id != "local":
                key = (call.stage, call.provider_id, call.model_id)
                groups[key] = groups.get(key, 0) + 1
    settled_groups, unsettled_groups = {}, {}
    for item in usage:
        if (item.job_id != job.id or type(item.request_count) is not int or item.request_count < 1
                or not isinstance(item.stage, str) or not item.stage
                or not isinstance(item.provider, str) or not item.provider
                or not isinstance(item.model, str) or not item.model
                or item.usage_state not in {"reported", "estimated", "unavailable"}
                or item.outcome not in {"succeeded", "failed", "cancelled"}):
            reject(code)
        settled = item.usage_state == "reported" and item.outcome == "succeeded"
        if item.provider == "local":
            if not settled:
                reject(code)
            continue
        key = (item.stage, item.provider, item.model)
        groups = settled_groups if settled else unsettled_groups
        groups[key] = groups.get(key, 0) + item.request_count
    # Exact job/stage/provider/model matching never credits an unresolved call
    # with a settled receipt, or an unrelated usage row with another UNKNOWN.
    if (settled_groups != known_groups or any(count > unknown_groups.get(key, 0)
            for key, count in unsettled_groups.items())):
        reject(code)


async def _completed_invalid_source_output(payload, row, *, completed, job, operation,
        request, source, seed, episode_calls, usage, session, owner, tenant, binding,
        vectors, header_producer, _source_validation=None):
    """Prove a rejected source response for a separately approved child budget."""
    attempts = payload.get("attempts")
    attempt = attempts[-1] if isinstance(attempts, list) and attempts else None
    receipt = attempt.get("receipt") if isinstance(attempt, dict) else None
    error = receipt.get("validation_error") if isinstance(receipt, dict) else None
    native_structure_errors = {"svg_source_native_table_group_invalid",
        "svg_source_native_table_membership_mismatch"}
    allowed = {"svg_source_receipt_binding_mismatch", "svg_source_native_table_grid_mismatch",
        "svg_source_receipt_text_mismatch", *native_structure_errors}
    if error not in allowed or request.source_mode != "source_content_restyle":
        return None
    code = "svg_partial_recovery_invalid_source_unproven"
    stages = {"author", "repair", "repair_remake", "repair_patch"}
    if any(call.stage in stages and (type(call.slide_index) is not int
            or not 0 <= call.slide_index < job.total_slides
            or not isinstance(call.provider_id, str) or not call.provider_id
            or not isinstance(call.model_id, str) or not call.model_id) for call in episode_calls):
        reject(code)
    # An earlier rejected answer is history while the current call is unresolved.
    # Leave it to the manifest's UNKNOWN branch; it cannot prove a new purchase.
    if completed is None and (payload.get("inflight") is not None or _unknown_provider(payload)
            or any(call.slide_index == row.slide_index and _unresolved_provider_call(call)
                for call in episode_calls)):
        return None
    raw = attempt.get("raw_response")
    # A design-repair candidate rejected by source validation: the approved prior
    # render legitimately exists; the page is re-bought as a fresh author in the child.
    quality_recovery = payload.get("quality_recovery")
    repair_stage_rejection = (row.render_status == "completed"
        and isinstance(quality_recovery, dict) and quality_recovery.get("status") == "repair_pending"
        and payload.get("svg_content") is None and row.author_status in {"running", "failed"})
    if (source is None or completed is None or not isinstance(raw, str) or not raw.strip()
            or attempt.get("kind") != "provider" or payload.get("inflight") is not None
            or _unknown_provider(payload) or operation.owner_id != owner
            or job.workspace_operation_id != operation.id
            or row.author_status not in {"completed", "failed", "running"}
            or (row.render_status != "pending" and not repair_stage_rejection)
            or row.persist_status != "pending"
            or job.svg_style_snapshot.get("source_authoring_contract") != {"version": "svg-source-whole-page/v1"}
            or receipt.get("source_authoring_contract") != job.svg_style_snapshot["source_authoring_contract"]
            or receipt.get("contract_version") not in {"svg-static/v1", "svg-static/v2"}
            or not receipt.get("provider") or not receipt.get("requested_model")
            or not _author_style_matches(payload, receipt, job, row.slide_index, header_producer=header_producer)
            or receipt.get("authoring_profile") != job.svg_style_snapshot.get("authoring_profile")
            or completed != _completed_response(payload, job, operation, owner, tenant)
            or raw != completed["raw_response"]
            or str(receipt.get("raw_digest", "")).removeprefix("sha256:") != completed["output_digest"]
            or type(receipt.get("attempt_ordinal")) is not int
            or _receipt_terminal_ordinal(receipt, payload) is None
            or _receipt_terminal_ordinal(receipt, payload) != completed.get("ordinal")):
        reject(code)
    unresolved_other_calls = [call for call in episode_calls if call.stage in stages
        and call.slide_index != row.slide_index and _unresolved_provider_call(call)]
    current_calls = [call for call in episode_calls if call.stage in stages
        and not (call.slide_index != row.slide_index and _unresolved_provider_call(call))]
    page_calls = [call for call in episode_calls if call.slide_index == row.slide_index]
    author_calls = [call for call in page_calls if call.stage in stages]
    if (not author_calls or any(call.state != "reported" or call.outcome != "succeeded"
            or call.reconciled_at is None for call in [*current_calls, *page_calls])
            or {(call.provider_id, call.model_id) for call in author_calls}
                != {(receipt["provider"], receipt["requested_model"])}):
        reject(code)
    # An unavailable receipt may be preserved only within the same job/stage/
    # provider/model UNKNOWN group, bounded by that group's durable call count.
    unsettled_groups = {}
    for item in usage:
        if item.job_id != job.id:
            reject(code)
        if item.stage not in stages or item.provider == "local" or (
                item.usage_state == "reported" and item.outcome == "succeeded"):
            continue
        if (type(item.request_count) is not int or item.request_count < 1
                or item.usage_state not in {"reported", "estimated", "unavailable"}
                or item.outcome not in {"succeeded", "failed", "cancelled"}):
            reject(code)
        key = (item.stage, item.provider, item.model)
        unsettled_groups[key] = unsettled_groups.get(key, 0) + item.request_count
    for key, count in unsettled_groups.items():
        matching = [call for call in unresolved_other_calls
            if (call.stage, call.provider_id, call.model_id) == key]
        if count > len(matching):
            reject(code)
    # Usage is job/model aggregate evidence. Count every settled success and
    # preserve explicitly bounded unresolved receipts in the immutable ledger.
    for provider, model in {(call.provider_id, call.model_id) for call in author_calls}:
        related_calls = [call for call in current_calls
            if (call.provider_id, call.model_id) == (provider, model)]
        unresolved = [call for call in unresolved_other_calls
            if (call.provider_id, call.model_id) == (provider, model)]
        related_usage = [item for item in usage if item.job_id == job.id and item.stage in stages
            and (item.provider, item.model) == (provider, model)]
        settled_usage = [item for item in related_usage
            if item.usage_state == "reported" and item.outcome == "succeeded"]
        if (not settled_usage or any(type(item.request_count) is not int or item.request_count < 1
                or item.usage_state not in {"reported", "estimated", "unavailable"}
                or item.outcome not in {"succeeded", "failed", "cancelled"} for item in related_usage)
                or len(settled_usage) != len(related_usage) and not unresolved
                or sum(item.request_count for item in settled_usage) < len(related_calls)):
            reject(code)
    if error in native_structure_errors:
        _require_known_source_page_accounting(episode_calls=episode_calls, usage=usage, job=job,
            index=row.slide_index, receipt=receipt, code=code)
    from services.artifact_store import configured_public_artifact_service
    from services.svg_asset_manifest import ArtifactPointer
    from services.svg_asset_persistence import load_source_manifest_pointer
    service = configured_public_artifact_service()
    pointer = ArtifactPointer.model_validate(payload.get("source_manifest_ref"))
    reference = await service.resolve_ref(session, artifact_id=pointer.ref,
        owner_id=owner, tenant_id=tenant, expected_digest=pointer.sha256)
    asset = await load_source_manifest_pointer(session, service,
        pointer=payload.get("source_manifest_ref"), owner_id=owner, tenant_id=tenant)
    current_binding = dict(pointer=deepcopy(payload["source_manifest_ref"]), digest=asset.digest,
        presentation_id=str(asset.presentation_id), source_revision=asset.source_revision,
        origin_operation_id=str(reference.created_by_operation_id))
    if asset.digest != payload.get("source_manifest_digest") or binding not in (None, current_binding):
        reject("svg_partial_recovery_asset_binding_changed")
    from services.svg_source_layout import bind_whole_page_source_svg
    from services.generation_core.authoring.author_source_svg_layout import _whole_page_facts
    from services.generation_core.drivers.svg.native_declarations import extract_native_declarations, declaration_receipt
    from services.svg_contract import normalize_svg_response
    from utils.svg_template_fonts import source_font_profile
    from services.authored_canvas import request_canvas_contract
    from services.document_canvas_generation import source_restyle_font_floor, svg_quality_font_floor
    import xml.etree.ElementTree as ET
    pages = _recovery_source_pages(source, job, _source_validation)
    offered = next((page.slot_ids for page in asset.offered_pages if page.page_index == row.slide_index), ())
    slots = {slot.slot_id: slot for slot in asset.slots if slot.slot_id in offered}
    page_assets = {key: slot.model_dump(mode="json", by_alias=True) for key, slot in slots.items()}
    profile = source_font_profile(job.svg_style_snapshot)
    expected = digest({"page": _expected_page_input_digest(request, job, row.slide_index, page_assets),
        "source_manifest": source.manifest_digest, "font_profile": profile,
        "source_page": pages[row.slide_index].model_dump(mode="json"), "source_layout_version": "svg-source-layout/v2"})
    if seed.get("source_font_profile") != profile or payload.get("input_digest") != expected:
        reject("svg_partial_recovery_page_input_changed")
    image_slots = {}
    for block in pages[row.slide_index].blocks:
        if block.kind != "image":
            continue
        try:
            occurrence = uuid.UUID(block.source_block_id)
        except ValueError:
            occurrence = uuid.uuid5(asset.presentation_id, f"source-occurrence:{block.source_block_id}")
        matches = [slot.slot_id for slot in slots.values() if slot.occurrence_id == occurrence]
        if len(matches) != 1:
            reject(code)
        image_slots[block.source_block_id] = matches[0]
    from services.svg_author_output_view import parse_author_response
    view, view_facts = _replay_output_view(job, source, row.slide_index, _source_validation, code=code)
    svg = normalize_svg_response(raw)
    if "<!" in svg or "<?" in svg:
        reject(code)
    historical_grid = None
    try:
        root, _ = parse_author_response(raw, view=view, source_facts=view_facts)
        declarations = extract_native_declarations(root)
        from services.generation_core.drivers.svg.embed_assets import replace_vector_icon_images, vector_icon_id_prefix
        used = replace_vector_icon_images(root, vectors, id_prefix=vector_icon_id_prefix(job.svg_style_snapshot)) if vectors else set()
        if sorted(used) != sorted(receipt.get("vector_icons_used") or []):
            reject(code)
        if declaration_receipt(declarations).get("native_declarations") != receipt.get("native_declarations"):
            reject(code)
        for node in root.iter():
            if node.tag == "{http://www.w3.org/2000/svg}image":
                if node.get("href") not in slots:
                    reject(code)
                used.add(node.get("href"))
        if any((slot.policy.required or not slot.policy.allow_omit) and key not in used for key, slot in slots.items()):
            reject(code)
        svg = ET.tostring(root, encoding="unicode")
        if (str(receipt.get("source_digest", "")).removeprefix("sha256:") != sha256_hex(svg.encode())
                or payload.get("svg_content") not in (None, svg)):
            reject(code)
        canvas = request_canvas_contract(request)
        floor = source_restyle_font_floor(request.instructions, getattr(request, "_document_canvas", None))
        if job.svg_style_snapshot.get("readability_policy") is not None:
            floor = max(floor, svg_quality_font_floor(job.svg_style_snapshot["readability_policy"]))
        bound_svg, preservation = bind_whole_page_source_svg(svg, source, row.slide_index,
            facts=_whole_page_facts(pages[row.slide_index], image_slots), width=canvas.width_px,
            height=canvas.height_px, style_snapshot=job.svg_style_snapshot, minimum_font_px=floor,
            _validated_pages=pages, native_declarations=declarations)
    except PartialRecoveryRejected:
        raise
    except (ValueError, TypeError, KeyError, ET.ParseError) as failure:
        if str(failure) != error:
            raise PartialRecoveryRejected(code) from failure
    else:
        if error == "svg_source_native_table_grid_mismatch":
            historical_grid = _historical_uniform_grid_rejection(bound_svg, preservation,
                pages[row.slide_index], operation=operation, job=job)
        if historical_grid is None:
            reject(code)
    return {"reason": "completed_source_invalid", "validation_error": error,
        "completed_receipt_digest": completed["receipt_digest"], "output_digest": completed["output_digest"],
        "author_receipt_digest": author_witness(payload), "input_digest": expected,
        "source_asset_binding": current_binding,
        **({"historical_uniform_grid_rejection": historical_grid} if historical_grid is not None else {})}


def _completed_failed_source_quality(payload, row, *, completed, job, operation,
        request, source, seed, asset, episode_calls, usage, owner, tenant, header_producer=None, _source_validation=None):
    """Seal a measured overlap failure for a separately approved fresh author."""
    quality = payload.get("quality") or {}
    failures = quality.get("failures")
    if (request.source_mode != "source_content_restyle" or quality.get("passed") is not False
            or not isinstance(failures, list) or not failures
            or any(not isinstance(item, dict) or item.get("kind") != "text_overlap" for item in failures)):
        return None
    code = "svg_partial_recovery_failed_source_quality_unproven"
    attempts = payload.get("attempts") or []
    attempt = attempts[-1] if attempts and isinstance(attempts[-1], dict) else {}
    receipt = attempt.get("receipt") or {}
    svg = payload.get("svg_content")
    if (source is None or completed is None or operation.owner_id != owner
            or operation.error_code != "svg_source_quality_failed"
            or job.workspace_operation_id != operation.id
            or row.author_status != "completed" or row.render_status != "failed"
            or row.persist_status != "pending" or payload.get("inflight") is not None
            or _unknown_provider(payload) or attempt.get("kind") != "provider"
            or receipt.get("validation_error") is not None
            or not _author_style_matches(payload, receipt, job, row.slide_index, header_producer=header_producer)
            or receipt.get("authoring_profile") != job.svg_style_snapshot.get("authoring_profile")
            or receipt.get("source_authoring_contract") != {"version": "svg-source-whole-page/v1"}
            or job.svg_style_snapshot.get("source_authoring_contract") != receipt.get("source_authoring_contract")
            or completed != _completed_response(payload, job, operation, owner, tenant)
            or attempt.get("raw_response") != completed["raw_response"]
            or not completed["raw_response"].strip()
            or _receipt_terminal_ordinal(receipt, payload) is None
            or _receipt_terminal_ordinal(receipt, payload) != completed.get("ordinal")
            or str(receipt.get("raw_digest", "")).removeprefix("sha256:") != completed["output_digest"]
            or not isinstance(svg, str) or not svg.strip()
            or quality.get("measurement_complete") is not True
            or quality.get("geometry_passed") is not False
            or (quality.get("physical_readability") or {}).get("passed") is not True
            or quality.get("contract_version") not in {"svg-static/v1", "svg-static/v2"}
            or quality.get("renderer_version") != "svg-chromium/v1"):
        reject(code)
    _require_known_source_page_accounting(episode_calls=episode_calls, usage=usage, job=job,
        index=row.slide_index, receipt=receipt, code=code)
    from services.svg_source_layout import verify_source_receipt
    from utils.svg_template_fonts import source_font_profile
    pages = _recovery_source_pages(source, job, _source_validation)
    offered = next((page.slot_ids for page in asset.offered_pages if page.page_index == row.slide_index), ())
    page_assets = {slot.slot_id: slot.model_dump(mode="json", by_alias=True)
        for slot in asset.slots if slot.slot_id in offered}
    profile = source_font_profile(job.svg_style_snapshot)
    expected = digest({"page": _expected_page_input_digest(request, job, row.slide_index, page_assets),
        "source_manifest": source.manifest_digest, "font_profile": profile,
        "source_page": pages[row.slide_index].model_dump(mode="json"), "source_layout_version": "svg-source-layout/v2"})
    source_digest = sha256_hex(svg.encode())
    if (seed.get("source_font_profile") != profile or payload.get("input_digest") != expected
            or payload.get("source_manifest_digest") != asset.digest
            or str(payload.get("source_digest", "")).removeprefix("sha256:") != source_digest
            or str(quality.get("source_digest", "")).removeprefix("sha256:") != source_digest):
        reject(code)
    try:
        verify_source_receipt(svg, payload.get("source_preservation"), source, row.slide_index,
            _validated_pages=pages)
    except (ValueError, TypeError, KeyError) as error:
        raise PartialRecoveryRejected(code) from error
    images = quality.get("sourceImages") or {}
    if (images.get("omitted") != [] or set(images.get("offered") or []) != set(offered)
            or set(images.get("used") or []) != set(offered)):
        reject(code)
    import json
    from services.svg_presentation_checkpoint import quality_browser_metrics
    try:
        metrics = quality_browser_metrics(quality)
    except ValueError:
        reject(code)
    if (not isinstance(metrics, dict) or metrics.get("probe_error") or not metrics.get("fonts_ready")
            or not _sha256_hex_valid(str(quality.get("wrapper_digest", "")).removeprefix("sha256:"))):
        reject(code)
    metrics_digest = sha256_hex(json.dumps(metrics, sort_keys=True, separators=(",", ":"),
        ensure_ascii=False, default=str).encode())
    recorded = str(quality.get("metrics_digest", "")).removeprefix("sha256:")
    if metrics_digest != recorded:
        recovered = json.loads(json.dumps(metrics).replace("\\u241f", "\\u0000"))
        if (sha256_hex(json.dumps(recovered, sort_keys=True, separators=(",", ":"),
                ensure_ascii=False, default=str).encode()) != recorded
                or json.loads(json.dumps(recovered).replace("\\u0000", "\\u241f")) != metrics):
            reject(code)
    from services.svg_text_overlap import text_overlaps
    pairs, basis = text_overlaps([item for item in metrics.get("elements", []) if item.get("has_owned_text")])
    if not pairs or failures != [{"kind": "text_overlap", "pairs": pairs}] or quality.get("overlap_basis") != basis:
        reject(code)
    from services.svg_presentation_checkpoint import _read_png
    png = _read_png(payload)
    png_digest = payload.get("png_sha256")
    if (png is None or sha256_hex(png) != png_digest
            or str(quality.get("png_digest", "")).removeprefix("sha256:") != png_digest):
        reject(code)
    from services.generation_core.drivers.svg.render import native_table_line_pitch_hint
    table_pitch = native_table_line_pitch_hint(quality, svg, payload.get("source_preservation"))
    diagnostic_hint = None
    if table_pitch is not None:
        diagnostic_hint = json.dumps({"kind": "svg_readability_repair", "native_table_line_pitch": table_pitch},
            ensure_ascii=False, separators=(",", ":"))
        if len(diagnostic_hint) > 3000:
            diagnostic_hint = None
    return {"reason": "completed_source_quality_failed", "failure_kind": "text_overlap",
        **({"diagnostic_hint": diagnostic_hint} if diagnostic_hint else {}),
        "completed_receipt_digest": completed["receipt_digest"], "output_digest": completed["output_digest"],
        "author_receipt_digest": author_witness(payload), "input_digest": expected,
        "source_svg_sha256": source_digest, "png_sha256": png_digest,
        "metrics_digest": recorded, "quality_digest": digest(quality)}


class _SourceBundleRequestReuse:
    """One build call/transaction only; never stores authority across requests."""
    def __init__(self, session, service, *, owner, tenant, context):
        self.session, self.service = session, service
        self.owner, self.tenant, self.context = owner, tenant, context
        self.transaction = session.sync_session.get_transaction()
        self.entries, self.closed = {}, False
        self._guard(session, owner, tenant, context)

    def _guard(self, session, owner, tenant, context):
        from utils.owner_context import get_current_owner_id, get_current_tenant_id
        if (self.closed or self.transaction is None or session is not self.session
                or session.sync_session.get_transaction() is not self.transaction
                or (owner, tenant, context) != (self.owner, self.tenant, self.context)
                or (get_current_owner_id(), get_current_tenant_id()) != (owner, tenant)):
            reject('svg_partial_recovery_request_snapshot_changed')

    async def load(self, session, *, pointer, reference, owner, tenant, context):
        from services.svg_asset_manifest import ArtifactPointer
        from services.svg_asset_persistence import load_source_manifest_pointer
        self._guard(session, owner, tenant, context)
        pointer = ArtifactPointer.model_validate(pointer)
        key = (owner, tenant, context, digest(pointer.model_dump(mode='json', by_alias=True)))
        if reference.kind != 'source-manifest' or reference.media_type != 'application/json':
            reject('svg_partial_recovery_asset_binding_changed')
        entry = self.entries.get(key)
        if entry is not None:
            if reference != entry['reference']:
                reject('svg_partial_recovery_asset_binding_changed')
            return entry['asset']
        asset = await load_source_manifest_pointer(session, self.service, pointer=pointer,
            owner_id=owner, tenant_id=tenant)
        self._guard(session, owner, tenant, context)
        self.entries[key] = dict(pointer=pointer, reference=reference, asset=asset, seal=digest(_dump(asset)))
        return asset

    async def finish(self):
        from services.svg_asset_persistence import load_source_manifest_pointer
        from models.sql.public_artifact import PublicArtifact
        self._guard(self.session, self.owner, self.tenant, self.context)
        for entry in self.entries.values():
            pointer = entry['pointer']
            # Refresh the same scoped row before the official resolver; an
            # ORM identity-map hit is not a final metadata readback.
            refreshed = await self.session.scalar(select(PublicArtifact).where(
                PublicArtifact.id == pointer.ref, PublicArtifact.owner_id == self.owner,
                PublicArtifact.tenant_id == self.tenant, PublicArtifact.sha256 == pointer.sha256,
                PublicArtifact.lifecycle_state == 'committed').execution_options(populate_existing=True))
            if refreshed is None:
                reject('svg_partial_recovery_asset_binding_changed')
            reference = await self.service.resolve_ref(self.session, artifact_id=pointer.ref,
                owner_id=self.owner, tenant_id=self.tenant, expected_digest=pointer.sha256)
            fresh = await load_source_manifest_pointer(self.session, self.service, pointer=pointer,
                owner_id=self.owner, tenant_id=self.tenant)
            self._guard(self.session, self.owner, self.tenant, self.context)
            if (reference != entry['reference'] or digest(_dump(entry['asset'])) != entry['seal']
                    or digest(_dump(fresh)) != entry['seal']):
                reject('svg_partial_recovery_asset_binding_changed')
        self.closed = True
        self.entries.clear()


async def build_manifest(session, operation, *, presentation_revision):
    """Read-only review surface; locks are released by the caller transaction."""
    from models.sql.async_presentation_generation_status import AsyncPresentationGenerationTaskModel
    from models.sql.presentation import PresentationModel
    from models.sql.source_content import SourceContentManifestRecord
    from services.presentation_generation_staging import _request_from_durable_payload
    from services.source_content.manifest_store import admit_source_content_manifest_pin
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
            or operation.state not in {"failed", "cancelled", "dead_letter", "deadline_exceeded"}
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
    _verify_frozen_header_outline(job)
    if (job.request_payload or {}).get("_internal_svg_header_title_plan") is not None:
        from models.presentation_outline_model import PresentationOutlineModel
        from services.svg_common_header_title_budget import validate_header_title_plan
        from services.svg_presentation_prepare import _verify_frozen_title_result

        plan = validate_header_title_plan(job.request_payload["_internal_svg_header_title_plan"])
        try:
            await _verify_frozen_title_result(session, job,
                PresentationOutlineModel.model_validate(job.authored_outline), plan)
        except (TypeError, ValueError, KeyError) as error:
            raise PartialRecoveryRejected("svg_partial_recovery_header_binding_invalid") from error
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
    source_validation = None
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
    if source is not None:
        from services.source_content.contracts import SourceContentManifest
        if isinstance(source, SourceContentManifest):
            from services.svg_source_layout import source_pages
            chart = deepcopy(job.svg_style_snapshot.get("source_chart_contract"))
            source_validation = (source, chart, source_pages(source, chart_contract=chart))
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
    verification = _EpisodeVerification(session)
    seal_memo = verification.seal_memo
    ancestors, admissions, calls, leases = [], [], [], []
    current, previous, seen = operation, None, set()
    while current is not None:
        if (current.id in seen or current.owner_id != owner
                or current.owner_scope != operation.owner_scope
                or current.kind != "presentation.generate"
                or current.state not in {"failed", "cancelled", "dead_letter", "deadline_exceeded"}
                or _live(current.lease_expires_at, now)):
            reject("svg_partial_recovery_lineage_conflict")
        if previous is not None and (current.presentation_id, current.conversation_id) != (
                previous.presentation_id, previous.conversation_id):
            await _verify_append_only_edge(session, previous, current, verification=verification)
        seen.add(current.id)
        ancestors.append(current)
        admission = await session.scalar(select(PublicOperationAdmission).where(
            PublicOperationAdmission.operation_id == current.id).with_for_update())
        if (admission is None or admission.owner_id != owner or admission.tenant_id != tenant
                or admission.state not in {"failed", "cancelled", "budget_exhausted", "deadline_exceeded"}
                or admission.terminal_at is None):
            reject("svg_partial_recovery_admission_unresolved")
        admissions.append(admission)
        admission_calls = list(await session.scalars(select(PublicProviderLogicalCall).where(
            PublicProviderLogicalCall.admission_id == admission.id).order_by(
            PublicProviderLogicalCall.id).with_for_update()))
        if any(call.admission_id != admission.id for call in admission_calls):
            reject("svg_partial_recovery_invalid_source_unproven")
        calls.extend(admission_calls)
        leases.extend(await session.scalars(select(PublicResourceLease).where(
            PublicResourceLease.admission_id == admission.id).order_by(
            PublicResourceLease.id).with_for_update()))
        previous = current
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
    pages, binding, baseline_payloads = [], None, {}
    admission_ids = {item.id for item in admissions}
    author_stages = {"author", "repair", "repair_remake", "repair_patch"}
    if any(call.admission_id not in admission_ids or (call.stage in author_stages and (
            type(call.slide_index) is not int or not 0 <= call.slide_index < job.total_slides
            or not isinstance(call.provider_id, str) or not call.provider_id
            or not isinstance(call.model_id, str) or not call.model_id)) for call in calls):
        reject("svg_partial_recovery_invalid_source_unproven")
    episode_calls = [call for call in calls if call.admission_id == admissions[0].id]
    seed = rows[0].svg_checkpoint or {}
    header_producer = await verified_header_producer(session,operation,job, verification=verification)
    initial_controls = {str(item.id): _operation_control(item) for item in ancestors}
    initial_job_control = _operation_control(job)
    initial_presentation_control = _operation_control(presentation)
    initial_ledger = _recovery_ledger_snapshot(admissions, calls, leases, usage, budgets, reservations)
    bundle_context = (str(operation.id), str(job.id), str(job.presentation_id), digest(job.svg_style_snapshot),
        request.source_mode, str(pin.manifest_id) if pin is not None else None, str(pin.digest) if pin is not None else None)
    bundle_service = configured_public_artifact_service()
    source_bundle_reuse = _SourceBundleRequestReuse(session, bundle_service,
        owner=owner, tenant=tenant, context=bundle_context)
    for row in rows:
        payload = row.svg_checkpoint or {}
        completed = _completed_response(payload, job, operation, owner, tenant)
        prior_lineage = job.request_payload.get(LINEAGE_KEY) or {}
        episode_admissions = ({admissions[0].id} if prior_lineage else admission_ids)
        page_calls = [call for call in calls if call.slide_index == row.slide_index
                      and call.admission_id in episode_admissions]
        undispatched = _undispatched_episode_page(payload, job, operation, owner, tenant,
            page_calls=page_calls, attempt_token=getattr(row, "attempt_token", None))
        unknown_calls = [call for call in page_calls if _unresolved_provider_call(call)]
        is_unknown = not undispatched and (bool(unknown_calls) or completed is None and (
            _unknown_provider(payload) or payload.get("inflight") is not None))
        if is_unknown:
            pages.append({"slide_index": row.slide_index, "category": "unknown",
                "row_digest": digest(_dump(row))})
            continue
        baseline = await _pre_dispatch_repair_baseline(payload, row, job=job, operation=operation, seed=seed, episode_calls=episode_calls) if completed is None else None
        if baseline is not None:
            payload, baseline_proof = baseline
            baseline_payloads[row.slide_index] = payload
        svg = payload.get("svg_content")
        completed_output_failure = "completed_empty" if completed is not None and not completed["raw_response"].strip() else None
        invalid_source = await _completed_invalid_source_output(payload, row, completed=completed,
            job=job, operation=operation, request=request, source=source, seed=seed,
            episode_calls=episode_calls, usage=usage, session=session, owner=owner, tenant=tenant,
            binding=binding, vectors=vector_maps[row.slide_index], header_producer=header_producer, _source_validation=source_validation)
        if invalid_source is not None:
            completed_output_failure = invalid_source["reason"]
        completed_projection = None if invalid_source else await _completed_repair_author_projection(
            payload, row, completed=completed, job=job, operation=operation, seed=seed,
            episode_calls=episode_calls, source=source, _source_validation=source_validation)
        if completed_projection is None and invalid_source is None:
            completed_projection = await _terminal_repair_author_projection(payload, row, completed=completed, job=job, operation=operation, seed=seed, episode_calls=episode_calls, session=session, request=request, binding=binding, vectors=vector_maps[row.slide_index], vector_seed_witness=vector_seed_witness, source=source, _source_validation=source_validation)
        if completed_projection:
            payload, baseline_proof = completed_projection
            if completed is not None and baseline_proof.get("completed_receipt_digest") not in (None, completed["receipt_digest"]):
                records = {key: value for key, value in (payload.get("provider_terminal_responses") or {}).items()
                    if isinstance(value, dict) and value.get("receipt_digest") == baseline_proof["completed_receipt_digest"]}
                completed = _completed_response({**payload, "provider_terminal_responses": records}, job, operation, owner, tenant)
                if completed is None:
                    reject("svg_partial_recovery_completed_author_unproven")
            baseline = completed_projection
            baseline_payloads[row.slide_index] = payload
            svg = payload.get("svg_content")
        if completed and completed_output_failure is None and not (isinstance(svg, str) and svg and payload.get("attempts")):
            reject("svg_partial_recovery_local_output_requires_repair")
        if completed_output_failure is not None:
            category = "known_failed_output"
        elif (baseline is not None or row.author_status == "completed") and isinstance(svg, str) and svg:
            category = "known"
        elif undispatched or (not payload.get("attempts") and not svg and not payload.get("provider_terminal_responses")
              and not any(call.slide_index == row.slide_index and call.stage == "author"
                          for call in (episode_calls if prior_lineage else calls))
              and row.render_status == "pending" and row.persist_status == "pending"):
            category = "unstarted"
        else:
            # A response.completed raw-only output is a known local failure,
            # never evidence that another provider call may be purchased.
            reject("svg_partial_recovery_local_output_requires_repair")
        witness = {"slide_index": row.slide_index, "category": category,
                   "row_digest": digest(_dump(row))}
        if completed_output_failure is not None:
            witness["completed_output_failure"] = invalid_source or {"reason": completed_output_failure, "completed_receipt_digest": completed["receipt_digest"], "output_digest": completed["output_digest"]}
        if baseline is not None:
            witness["repair_baseline_projection"] = baseline_proof
        if category == 'known':
            attempts = payload.get("attempts") or []
            receipt = attempts[-1].get("receipt") or {} if attempts else {}
            raw = attempts[-1].get("raw_response") if attempts else None
            retained_source = await _retained_quality_source_author(payload, row, job=job, operation=operation, seed=seed, completed=completed, session=session, owner=owner, tenant=tenant, seal_memo=seal_memo)
            if retained_source is not None:
                raw, receipt, retained_source_proof = retained_source
                witness["retained_source_selection"] = retained_source_proof
            good_calls = [call for call in calls if call.slide_index == row.slide_index
                and call.stage == "author" and call.state in {"reported", "unavailable"}
                and call.outcome == "succeeded" and call.reconciled_at is not None]
            quality = payload.get("quality") or {}
            png_digest = payload.get("png_sha256")
            png = _read_png(payload)
            inherited_terminal_author = attempts[-1].get("kind") == "sealed_terminal_replay" and completed_projection is None and await _inherited_terminal_author_verified(payload, row, manifest=operation.request_payload.get("svg_partial_recovery_manifest"), manifest_archive=operation.request_payload.get(ARCHIVE_KEY), receipt=receipt, session=session, owner=owner, tenant=tenant, operation=operation, seal_memo=seal_memo)
            if not good_calls and completed is None and not inherited_terminal_author or not attempts or (not isinstance(raw, str)) or ((attempts[-1].get('kind') != 'provider' and (not ((completed_projection and attempts[-1].get('kind') == 'sealed_terminal_replay' and (receipt.get('terminal_recovery_witness', {}).get('schema') == 'svg-author-terminal-local-replay/v1') and (receipt.get('terminal_recovery_witness', {}).get('completed_receipt_digest') == completed['receipt_digest'])) and (not inherited_terminal_author)))) and (not inherited_terminal_author)) or (receipt.get('contract_version') not in {'svg-static/v1', 'svg-static/v2'}) or (not receipt.get('provider')) or (not receipt.get('requested_model')) or not _author_style_matches(payload,receipt,job,row.slide_index,header_producer=header_producer) or (receipt.get('authoring_profile') != job.svg_style_snapshot.get('authoring_profile')) or (receipt.get('validation_error') is not None) or (str(receipt.get('raw_digest', '')).removeprefix('sha256:') != sha256_hex(raw.encode())) or (completed is not None and (retained_source is None and completed.get('raw_response') != raw)) or (not payload.get('input_digest')):
                reject('svg_partial_recovery_local_output_requires_repair' if completed is not None else 'svg_partial_recovery_known_output_unproven')
            if good_calls and not any(call.provider_id == receipt["provider"]
                    and call.model_id == receipt["requested_model"] for call in good_calls):
                reject("svg_partial_recovery_author_model_unbound")
            service = bundle_service
            pointer = ArtifactPointer.model_validate(payload.get("source_manifest_ref"))
            reference = await service.resolve_ref(session, artifact_id=pointer.ref,
                owner_id=owner, tenant_id=tenant, expected_digest=pointer.sha256)
            asset = await source_bundle_reuse.load(session, pointer=pointer, reference=reference,
                owner=owner, tenant=tenant, context=bundle_context)
            current_binding = {"pointer": deepcopy(payload.get("source_manifest_ref")),
                "digest": asset.digest, "presentation_id": str(asset.presentation_id),
                "source_revision": asset.source_revision,
                "origin_operation_id": str(reference.created_by_operation_id)}
            if asset.digest != payload.get("source_manifest_digest") or binding not in (None, current_binding):
                reject("svg_partial_recovery_asset_binding_changed")
            binding = current_binding
            try:
                transform_witness = _author_render_chain(payload, raw, receipt, request=request, job=job, asset=asset, index=row.slide_index, row=__import__('types').SimpleNamespace(author_status='completed', render_status='pending', persist_status='pending', render_path=None, render_sha256=None) if completed_projection else row, completed=(None if retained_source is not None else completed), vectors=vector_maps[row.slide_index], vector_seed_witness=vector_seed_witness, source=source, _source_validation=source_validation)
            except PartialRecoveryRejected:
                raise
            except (ValueError, TypeError, KeyError) as error:
                raise PartialRecoveryRejected("svg_partial_recovery_render_transform_unproven") from error
            if source is not None:
                preservation = payload.get("source_preservation") or {}
                validated_pages = (_recovery_source_pages(source, job, source_validation)
                    if source_validation is not None and preservation.get("source_chart_contract") == source_validation[1]
                    else None)
                verify_source_receipt(svg, preservation, source, row.slide_index,
                    _validated_pages=validated_pages)
            witness.update(svg_sha256=transform_witness["render_svg_sha256"],
                source_svg_sha256=sha256_hex(svg.encode()),
                final_render_svg_sha256=transform_witness["render_svg_sha256"],
                phase=transform_witness.get("phase", "finalized"), png_sha256=png_digest,
                historical_png_verified=png is not None and sha256_hex(png) == png_digest,
                historical_geometry_passed=quality.get("passed") is True and quality.get("measurement_complete") is True,
                author_render_chain=transform_witness,
                input_digest=payload["input_digest"], author_receipt_digest=author_witness(payload),
                source_font_digest=digest((payload.get("source_preservation") or {}).get("font_profile")))
            failed_quality = _completed_failed_source_quality(payload, row, completed=completed,
                job=job, operation=operation, request=request, source=source, seed=seed, asset=asset,
                episode_calls=episode_calls, usage=usage, owner=owner, tenant=tenant, header_producer=header_producer, _source_validation=source_validation)
            if failed_quality is not None:
                witness.update(category="known_failed_output", completed_output_failure=failed_quality)
            elif witness.get("phase") != "author-only":
                local_render = _verified_page_render_evidence(payload)
                if local_render is not None:
                    witness["page_render_evidence_digest"] = digest(local_render[0])
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
    from services.svg_authoring_profile import read_quality_recovery_plan

    review_calls, format_control, final_control = _recovery_review_allocation(
        request, job.svg_style_snapshot, job.total_slides,
    )
    author_calls = sum(page["category"] != "known" for page in pages)
    quality_plan = read_quality_recovery_plan(job.svg_style_snapshot)
    repair_calls = (job.total_slides if quality_plan and quality_plan["review_round_limit"] == 2
                    else author_calls)
    # Resolve inherited leaf rows before the existing final freshness fence.
    # This is the same single job dump used by the immutable archive below.
    source_job_snapshot = _dump(job)
    # A recovery source job that never reached review has no own review trace, so
    # the inherited parent source-local proof has nothing to continue from; drop it.
    no_own_review_trace = ('quality_recovery_execution' not in seed and not any(
        'quality_recovery' in (row.svg_checkpoint or {}) or 'quality_recovery_execution' in (row.svg_checkpoint or {})
        for row in rows))
    from services.svg_source_local_review_continuation import prepare_source_local_leaf, FinalSelectedEvidenceError
    source_local_leaf = None
    try:
        if not no_own_review_trace:
            source_local_leaf = await prepare_source_local_leaf(session, source_operation=operation,
                verification=verification, source_job=source_job_snapshot,
                execution=seed.get("quality_recovery_execution", {}), owner_id=str(owner), tenant_id=tenant,
                operation_id=str(operation.id), parent_manifest_hash=(operation.request_payload or {}).get("svg_partial_recovery_manifest_hash", ""))
    except FinalSelectedEvidenceError as error:
        raise PartialRecoveryRejected(error.code) from error
    await source_bundle_reuse.finish()
    await verification.finish()
    storage_seals = await _payload_storage_seals(session, [row.id for row in ancestors])
    if any(storage_seals.get(key) != value for key, value in seal_memo["seals"].items()):
        reject("svg_partial_recovery_request_snapshot_changed")
    if seal_memo["transaction"] is not None:
        from utils.owner_context import get_current_owner_id, get_current_tenant_id
        if (session.sync_session.get_transaction() is not seal_memo["transaction"]
                or (get_current_owner_id(), get_current_tenant_id()) != seal_memo["principal"]):
            reject("svg_partial_recovery_request_snapshot_changed")
    final_controls = await _fresh_recovery_operation_controls(session, [item.id for item in ancestors])
    if final_controls != initial_controls:
        reject("svg_partial_recovery_request_snapshot_changed")
    fresh_jobs = await _fresh_recovery_model_rows(session, PresentationGenerationJob, PresentationGenerationJob.id == job.id, skip_request_payload=True)
    fresh_presentations = await _fresh_recovery_model_rows(session, type(presentation), type(presentation).id == presentation.id, skip_request_payload=True)
    if (len(fresh_jobs) != 1 or _operation_control(fresh_jobs[0]) != initial_job_control
            or len(fresh_presentations) != 1 or _operation_control(fresh_presentations[0]) != initial_presentation_control
            or await _fresh_recovery_ledger_snapshot(session, [item.id for item in ancestors], job.id) != initial_ledger):
        reject("svg_partial_recovery_request_snapshot_changed")
    archive = {"schema": ARCHIVE_V3, "operation_refs": [
        _operation_reference(row, storage_seals.get(str(row.id)), tenant=tenant) for row in ancestors],
        "job": source_job_snapshot, "task": _dump(task), "checkpoint_refs": [
            _checkpoint_reference(_dump(row), owner_id=str(owner), tenant_id=tenant) for row in rows],
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
        "archive_schema": ARCHIVE_V3, "append_only_recovery": True,
        "source_request_routing": _source_request_routing(job.request_payload),
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
        "new_indices": [page["slide_index"] for page in pages if page["category"] in {"unstarted", "known_failed_output"}],
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
    if format_control is not None:
        manifest["global_format_retry_control"] = format_control
    source_local_proof = None
    inherited_local = (operation.request_payload.get("svg_partial_recovery_manifest") or {}).get("source_local_continuation")
    if no_own_review_trace:
        inherited_local = None
    local_categories = {"known", "unknown", "unstarted", "known_failed_output"}
    if inherited_local is not None and any(page.get("category") not in local_categories for page in pages):
        reject("svg_final_selected_evidence_unproven")
    if inherited_local is not None or len(manifest["known_indices"]) == len(pages):
        from services.svg_source_local_review_continuation import build_source_local_proof, FinalSelectedEvidenceError
        try:
            source_local_proof = build_source_local_proof(flattened_leaf=source_local_leaf,
                operation_id=str(operation.id), job_id=str(job.id), owner_id=str(owner), tenant_id=tenant,
                archive_digest=manifest["archive_digest"],
                parent_manifest_hash=(operation.request_payload or {}).get("svg_partial_recovery_manifest_hash", ""),
                payloads=[baseline_payloads.get(row.slide_index, row.svg_checkpoint or {})
                    if pages[row.slide_index]["category"] == "known" else row.svg_checkpoint or {} for row in rows],
                raw_payloads=[row.svg_checkpoint or {} for row in rows],
                fresh_page_witnesses={str(page["slide_index"]): dict(category=page["category"],
                    row_digest=page["row_digest"], source_payload_digest=digest(rows[page["slide_index"]].svg_checkpoint or {}))
                    for page in pages if page["category"] in {"unknown", "unstarted", "known_failed_output"}},
                baseline_projections={str(index): {"witness": deepcopy(pages[index]["repair_baseline_projection"]),
                    "projected_payload_digest": digest(payload)} for index, payload in baseline_payloads.items()
                    if pages[index].get("category") == "known" and pages[index].get("repair_baseline_projection") is not None},
                execution=seed.get("quality_recovery_execution", {}), source_job=archive["job"])
        except FinalSelectedEvidenceError as error:
            raise PartialRecoveryRejected(error.code) from error
        if source_local_proof is not None:
            manifest["source_local_continuation"] = source_local_proof
            # Only the completed first-phase local calls are no longer purchased.
            # The existing repair pool and later review phases stay admitted.
            manifest["prospective_initial_request_limit"] -= len(source_local_proof["source_local_group_digests"])
    if final_control is not None:
        from services.svg_final_selected_continuation import build_selected_proof
        manifest["final_selected_review_control"] = final_control
        manifest["prospective_initial_request_limit"] += final_control["review_call_limit"]
        final_proof = build_selected_proof(
            operation_id=str(operation.id), job_id=str(job.id), owner_id=str(owner),
            tenant_id=tenant, archive_digest=manifest["archive_digest"],
            parent_manifest_hash=(operation.request_payload or {}).get("svg_partial_recovery_manifest_hash", ""),
            payloads=[r.svg_checkpoint or {} for r in rows],
            execution=seed.get("quality_recovery_execution", {}),
            source_snapshot=job.svg_style_snapshot,
        )
        if final_proof is not None:
            if len(manifest["known_indices"]) != len(pages):
                reject("svg_final_selected_evidence_unproven")
            # The external final-selected consumer hashes the raw archive and
            # reads inline checkpoints. Preserve its v2 contract unchanged.
            archive = {key: value for key, value in archive.items() if key != "checkpoint_refs"}
            archive.update(schema=ARCHIVE_V2, checkpoints=[_dump(row) for row in rows])
            manifest.update(archive_schema=ARCHIVE_V2, archive_digest=digest(archive))
            final_proof = build_selected_proof(
                operation_id=str(operation.id), job_id=str(job.id), owner_id=str(owner),
                tenant_id=tenant, archive_digest=manifest["archive_digest"],
                parent_manifest_hash=(operation.request_payload or {}).get("svg_partial_recovery_manifest_hash", ""),
                payloads=[r.svg_checkpoint or {} for r in rows],
                execution=seed.get("quality_recovery_execution", {}), source_snapshot=job.svg_style_snapshot)
            if final_proof is None:
                reject("svg_final_selected_evidence_unproven")
            manifest["final_selected_continuation"] = final_proof
    return PartialRecovery(manifest, archive, rows, digest(manifest),
        baseline_payloads=baseline_payloads, verification=verification)


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


def _page_render_result_digest(payload):
    quality = payload.get("quality") or {}
    return digest({
        "svg": sha256_hex(str(payload.get("svg_content", "")).encode()),
        "author": author_witness(payload),
        "quality": {key: value for key, value in quality.items()
                    if key not in {"classification", "verified", "compositionObservation"}},
        "page": {key: payload.get(key) for key in (
            "input_digest", "source_digest", "png_sha256", "png_path", "source_manifest_ref",
            "source_manifest_digest", "source_preservation", "svgAssetManifest",
            "nativeDeclarations", "readability_policy", "source_font_profile",
            "system_furniture", "raster_approval_digest", "visual_intent",
            "source_format", "schema_version", "terminal", "flag")},
    })


def _verified_page_render_evidence(payload):
    """Read a measured page; consumer/producer ownership stays in the manifest."""
    from services.svg_presentation_checkpoint import _read_png

    evidence = payload.get("page_render_evidence")
    quality = payload.get("quality") or {}
    if (not isinstance(evidence, dict) or set(evidence) != {"environment", "result_digest"}
            or not isinstance(evidence["environment"], dict)
            or evidence["result_digest"] != _page_render_result_digest(payload)
            or payload.get("inflight") is not None or _unknown_provider(payload)
            or payload.get("terminal") or payload.get("flag")
            or any(quality.get(key) is not True
                   for key in ("passed", "geometry_passed", "measurement_complete"))):
        return None
    svg_digest = sha256_hex(str(payload.get("svg_content", "")).encode())
    png = _read_png(payload)
    if (png is None or sha256_hex(png) != payload.get("png_sha256")
            or str(quality.get("png_digest", "")).removeprefix("sha256:") != payload["png_sha256"]
            or str(quality.get("source_digest", "")).removeprefix("sha256:") != svg_digest
            or str(payload.get("source_digest", "")).removeprefix("sha256:") != svg_digest):
        return None
    return evidence, png

def cached_page_payload(row, recovery, operation_id):
    page = recovery.manifest["pages"][row.slide_index]
    marker = {"schema": "svg-partial-recovery-page/v1", "operation_id": str(operation_id),
        "manifest_hash": recovery.manifest_hash, "slide_index": row.slide_index,
        "source_row_digest": page["row_digest"], "category": page["category"]}
    if page["category"] != "known":
        result = {"partial_recovery": marker}
        failure = page.get("completed_output_failure") or {}
        if (page["category"] == "known_failed_output"
                and failure.get("reason") == "completed_source_quality_failed"
                and isinstance(failure.get("diagnostic_hint"), str)):
            result["repair_hint"] = failure["diagnostic_hint"]
        return result
    payload = deepcopy(getattr(recovery, "baseline_payloads", {}).get(row.slide_index, row.svg_checkpoint))
    local_render = (_verified_page_render_evidence(payload)
                    if page.get("page_render_evidence_digest") else None)
    render_copy = ({key: deepcopy(payload.get(key)) for key in (
        "quality", "png_path", "png_sha256", "source_digest", "source_preservation", "page_render_evidence")}
        if local_render is not None
        and digest(local_render[0]) == page["page_render_evidence_digest"] else None)
    for key in ("quality", "initial_quality", "browser_metrics", "png_sha256", "png_path",
                "terminal", "quality_recovery_local_evidence", "quality_receipt", "qualityReceipt",
                "cache_only_remeasurement", "append_only_cache", "cached_author_render",
                "cached_author_render_archive", "classification_remeasurement_archive",
                "last_valid_svg", "quality_recovery", "quality_recovery_fallback",
                "quality_recovery_execution",
                "local_readability_repair", "repair_hint", "repair_input_page_evidence",
                "provider_terminal_responses", "provider_dispatch_stopped", "page_render_evidence"):
        payload.pop(key, None)
    payload["inflight"] = None
    if isinstance(payload.get("source_preservation"), dict):
        payload["source_preservation"]["rendering_verified"] = False
    marker.update(svg_sha256=page["svg_sha256"], input_digest=page["input_digest"])
    marker.update(phase=page.get("phase", "finalized"),
        source_svg_sha256=page.get("source_svg_sha256", page["svg_sha256"]),
        final_render_svg_sha256=page.get("final_render_svg_sha256", page["svg_sha256"]))
    payload["partial_recovery"] = marker
    if render_copy is not None:
        payload.update(render_copy)
    proof = recovery.manifest.get("final_selected_continuation")
    if not proof and payload.get("final_selected_render_evidence"):
        from services.svg_authoring_profile import read_quality_recovery_policy
        source_snapshot = ((getattr(recovery, "archive", {}) or {}).get("job") or {}).get("svg_style_snapshot")
        source_policy = read_quality_recovery_policy(source_snapshot)
        if source_policy is not None and source_policy["version"] == 2:
            payload.pop("final_selected_render_evidence")
    if proof is not None:
        from services.svg_final_selected_continuation import render_evidence_marker, verify_cached_render
        original = row.svg_checkpoint
        for field in ("quality", "png_path", "png_sha256", "source_digest", "source_preservation"):
            payload[field] = deepcopy(original.get(field))
        payload["final_selected_render_evidence"] = render_evidence_marker(
            proof, row.slide_index, consumer_operation_id=str(operation_id),
            consumer_manifest_hash=recovery.manifest_hash,
        )
        verify_cached_render(payload, recovery.manifest, row.slide_index)
    return payload


async def rebind_episode(session, source, child, recovery, *, key_hash):
    """Create an append-only consumer; never rebind the compatibility producer."""
    from models.sql.async_presentation_generation_status import AsyncPresentationGenerationTaskModel
    from services.presentation_generation_job_service import request_sha256
    if (digest(recovery.archive) != recovery.manifest["archive_digest"]
            or recovery.archive.get("schema") not in {ARCHIVE_V2, ARCHIVE_V3}
            or recovery.manifest.get("archive_schema") != recovery.archive.get("schema")
            or recovery.manifest.get("append_only_recovery") is not True
            or child.presentation_id == source.presentation_id
            or str(source.presentation_id) != recovery.manifest.get("presentation_id")
            or str(source.id) != recovery.manifest.get("operation_id")
            or child.retry_of_operation_id != source.id
            or child.owner_id != source.owner_id or child.owner_scope != source.owner_scope):
        reject("svg_partial_recovery_archive_changed")
    child.request_payload = {**child.request_payload, ARCHIVE_KEY: deepcopy(recovery.archive),
        "svg_partial_recovery_manifest": deepcopy(recovery.manifest),
        "svg_partial_recovery_manifest_hash": recovery.manifest_hash}
    session.add(child)
    await session.flush()
    parent = await session.get(PresentationGenerationJob, uuid.UUID(recovery.manifest["job_id"]), with_for_update=True)
    if parent is None or digest(_dump(parent)) != digest(recovery.archive["job"]):
        reject("svg_partial_recovery_episode_conflict")
    await _verify_producer_rows(session, recovery.manifest, recovery.archive,
        verification=recovery.verification)
    task = AsyncPresentationGenerationTaskModel(status="pending", owner_id=source.owner_id)
    session.add(task)
    await session.flush()
    payload = _frozen_payload(deepcopy(parent.request_payload))
    payload["presentation_id"] = str(child.presentation_id)
    job = PresentationGenerationJob(
        task_id=task.id, presentation_id=child.presentation_id,
        workspace_operation_id=child.id, idempotency_key_hash=key_hash,
        request_sha256=request_sha256(payload), request_payload=payload,
        svg_style_snapshot=deepcopy(parent.svg_style_snapshot),
        authored_outline=deepcopy(parent.authored_outline), total_slides=parent.total_slides,
        state="queued", export_cookie_required=False,
    )
    lineage = {"schema": "svg-partial-recovery-lineage/v2",
        "operation_id": str(child.id), "source_operation_id": recovery.manifest["source_operation_id"],
        "action_operation_id": str(source.id), "job_id": str(job.id),
        "source_job_id": str(parent.id), "source_presentation_id": str(parent.presentation_id),
        "presentation_id": str(child.presentation_id), "manifest_hash": recovery.manifest_hash,
        "archive_schema": recovery.archive["schema"],
        **{key: recovery.manifest[key] for key in ("archive_digest", "known_indices", "new_indices",
            "unknown_indices", "frozen_request_digest", "frozen_style_digest")}}
    if recovery.manifest.get("final_selected_review_control") is not None:
        lineage["final_selected_review_control"] = deepcopy(recovery.manifest["final_selected_review_control"])
    if recovery.manifest.get("final_selected_continuation") is not None:
        lineage["final_selected_continuation_digest"] = recovery.manifest["final_selected_continuation"]["digest"]
    if recovery.manifest.get("source_local_continuation") is not None:
        lineage["source_local_continuation_digest"] = recovery.manifest["source_local_continuation"]["digest"]
    job.request_payload = {**payload, LINEAGE_KEY: lineage}
    job.request_sha256 = request_sha256(job.request_payload)
    session.add(job)
    await session.flush()
    for original in recovery.rows:
        known = original.slide_index in recovery.manifest["known_indices"]
        session.add(AuthoredSlideCheckpoint(
            job_id=job.id, slide_index=original.slide_index, role=original.role,
            svg_checkpoint=cached_page_payload(original, recovery, child.id),
            author_status="completed" if known else "pending", illustrate_status="skipped",
        ))
    child.request_payload = {**child.request_payload, "job_id": str(job.id), "task_id": task.id,
        "generation_request": deepcopy(job.request_payload), "svg_partial_recovery_lineage": lineage}
    session.add_all((child, job))
    return job


def _checkpoint_reference(snapshot, *, owner_id, tenant_id):
    return {"schema": CHECKPOINT_REFERENCE, "id": snapshot["id"], "job_id": snapshot["job_id"],
        "slide_index": snapshot["slide_index"], "owner_id": owner_id, "tenant_id": tenant_id,
        "row_digest": digest(snapshot)}


async def _hydrate_checkpoint_archive(session, manifest, archive, *, verification=None, _fresh_producer_proof=None):
    """Resolve v3 into a separate working view; raw archive/storage hashes stay raw."""
    if archive.get("schema") != ARCHIVE_V3:
        return archive
    from models.sql.workspace import get_workspace_owner_scope
    from utils.owner_context import get_current_owner_id, get_current_tenant_id

    if verification is not None:
        verification.check(session)
    owner, tenant = get_current_owner_id(), get_current_tenant_id()
    refs = archive.get("checkpoint_refs")
    job_snapshot = archive.get("job")
    count = manifest.get("expected_page_count")
    if (manifest.get("archive_schema") != ARCHIVE_V3 or digest(archive) != manifest.get("archive_digest")
            or "checkpoints" in archive or not isinstance(refs, list) or not isinstance(job_snapshot, dict)
            or owner is None or manifest.get("owner_id") != str(owner) or not tenant
            or manifest.get("tenant_id") != tenant or type(count) is not int or count < 1 or len(refs) != count
            or any(type(manifest.get(key)) is not str for key in ("operation_id", "job_id", "presentation_id"))
            or job_snapshot.get("id") != manifest.get("job_id")
            or job_snapshot.get("workspace_operation_id") != manifest.get("operation_id")
            or job_snapshot.get("presentation_id") != manifest.get("presentation_id")
            or job_snapshot.get("total_slides") != count):
        reject("svg_partial_recovery_checkpoint_reference_unbound")
    expected_keys = {"schema", "id", "job_id", "slide_index", "owner_id", "tenant_id", "row_digest"}
    ids, pages = set(), set()
    for reference in refs:
        if (not isinstance(reference, dict) or set(reference) != expected_keys
                or reference.get("schema") != CHECKPOINT_REFERENCE or type(reference.get("id")) is not str
                or reference.get("job_id") != manifest["job_id"] or reference.get("owner_id") != str(owner)
                or reference.get("tenant_id") != tenant or type(reference.get("slide_index")) is not int
                or not 0 <= reference["slide_index"] < count or reference["id"] in ids
                or reference["slide_index"] in pages
                or not _sha256_hex_valid(reference.get("row_digest"))):
            reject("svg_partial_recovery_checkpoint_reference_unbound")
        try:
            if str(uuid.UUID(reference["id"])) != reference["id"]:
                raise ValueError()
        except (ValueError, TypeError, AttributeError):
            reject("svg_partial_recovery_checkpoint_reference_unbound")
        ids.add(reference["id"])
        pages.add(reference["slide_index"])
    try:
        parent_id, job_id = uuid.UUID(manifest["operation_id"]), uuid.UUID(manifest["job_id"])
    except (ValueError, TypeError, AttributeError, KeyError):
        reject("svg_partial_recovery_checkpoint_reference_unbound")
    parents = await _fresh_recovery_model_rows(session, WorkspaceOperation,
        WorkspaceOperation.id == parent_id, skip_request_payload=True)
    jobs = await _fresh_recovery_model_rows(session, PresentationGenerationJob, PresentationGenerationJob.id == job_id)
    admissions = await _fresh_recovery_model_rows(session, PublicOperationAdmission,
        PublicOperationAdmission.operation_id == parent_id)
    if (len(parents) != 1 or len(jobs) != 1 or len(admissions) != 1
            or parents[0].owner_id != owner or parents[0].owner_scope != get_workspace_owner_scope()
            or parents[0].kind != "presentation.generate" or parents[0].state not in {"failed", "cancelled", "dead_letter", "deadline_exceeded"}
            or str(parents[0].presentation_id) != manifest["presentation_id"]
            or jobs[0].workspace_operation_id != parent_id or jobs[0].presentation_id != parents[0].presentation_id
            or digest(_dump(jobs[0])) != digest(job_snapshot)
            or admissions[0].owner_id != owner or admissions[0].tenant_id != tenant):
        reject("svg_partial_recovery_checkpoint_reference_unbound")
    cache_key = (str(parent_id), str(job_id), digest(manifest))
    if verification is not None and verification.transaction is not None:
        cached = verification.checkpoint_archives.get(cache_key)
        if cached is not None:
            if verification.controls.get(str(parent_id)) != _operation_control(parents[0]):
                reject("svg_partial_recovery_request_snapshot_changed")
            verification.expect(PresentationGenerationJob, job_snapshot)
            encoded = _marshal_json_bytes(cached[0])
            if encoded is None:
                reject("svg_partial_recovery_request_snapshot_changed")
            current_guard = hashlib.sha256(_CHECKPOINT_ARCHIVE_MEMO_GUARD_DOMAIN)
            current_guard.update(encoded)
            del encoded
            if current_guard.digest() != cached[1]:
                reject("svg_partial_recovery_request_snapshot_changed")
            verification.check(session)
            return cached[0]
    rows, row_storage = await _fresh_checkpoint_rows_sealed(session, AuthoredSlideCheckpoint.job_id == job_id)
    _count(verification, "rows_decoded_python", len(rows))
    if (_fresh_producer_proof is not None and verification is not None
            and verification.transaction is not None):
        # This existing producer-only proof returns no working archive. Preserve
        # JSON identity serialization, but digest the private fresh payload once.
        projections = [row.model_dump(mode="json", include={"id", "job_id", "slide_index"}) for row in rows]
        fingerprints = {projection["id"]: value
            for projection, value in zip(projections, await _producer_digests_off_loop(rows), strict=True)}
        if (len(rows) != count or len(fingerprints) != count or set(fingerprints) != ids
                or sorted(row.slide_index for row in rows) != list(range(count))):
            reject("svg_partial_recovery_checkpoint_reference_changed")
        metadata = {projection["id"]: projection for projection in projections}
        for ref in refs:
            projection = metadata[ref["id"]]
            if (projection.get("job_id") != ref["job_id"]
                    or projection.get("slide_index") != ref["slide_index"]
                    or fingerprints[ref["id"]] != ref["row_digest"]):
                reject("svg_partial_recovery_checkpoint_reference_changed")
        control = _operation_control(parents[0])
        identity = str(parent_id)
        if identity in verification.controls and verification.controls[identity] != control:
            reject("svg_partial_recovery_request_snapshot_changed")
        verification.controls[identity] = control
        verification.expect(PresentationGenerationJob, job_snapshot)
        verification.check(session)
        for ref in refs:
            key = (AuthoredSlideCheckpoint, "id", str(ref["id"]))
            fingerprint = fingerprints[ref["id"]]
            if key in verification.rows and verification.rows[key] != fingerprint:
                reject("svg_partial_recovery_request_snapshot_changed")
            verification.rows[key] = fingerprint
        identity = str(job_id)
        if identity in verification.producers and verification.producers[identity] != fingerprints:
            reject("svg_partial_recovery_request_snapshot_changed")
        verification.producers[identity] = fingerprints
        if row_storage is not None:
            # Same SELECT, same decoded rows, and the digest finish() itself computes.
            verification.record_producer_storage(identity, {row_id: (sha, fingerprints.get(row_id))
                for row_id, sha in row_storage.items()})
        verification.check(session)
        _fresh_producer_proof.append((verification, session, verification.transaction, jobs[0]))
        return None
    snapshots = {}
    for row in rows:
        snapshot = _dump(row)
        snapshots[snapshot["id"]] = snapshot
    if (len(rows) != count or len(snapshots) != count or set(snapshots) != ids
            or sorted(row.slide_index for row in rows) != list(range(count))):
        reject("svg_partial_recovery_checkpoint_reference_changed")
    ordered = []
    validated_fingerprints = {}
    for ref in refs:
        snapshot = snapshots[ref["id"]]
        fingerprint = digest(snapshot)
        if (snapshot.get("job_id") != ref["job_id"] or snapshot.get("slide_index") != ref["slide_index"]
                or fingerprint != ref["row_digest"]):
            reject("svg_partial_recovery_checkpoint_reference_changed")
        validated_fingerprints[snapshot["id"]] = fingerprint
        ordered.append(snapshot)
    ordered.sort(key=lambda snapshot: snapshot["slide_index"])
    if verification is not None:
        control = _operation_control(parents[0])
        identity = str(parent_id)
        if identity in verification.controls and verification.controls[identity] != control:
            reject("svg_partial_recovery_request_snapshot_changed")
        verification.controls[identity] = control
        verification.expect(PresentationGenerationJob, job_snapshot)
        if _fresh_producer_proof is not None and verification.transaction is not None:
            # These exact fresh rows already matched every bound ref digest. Final
            # complete-producer reads check both row and set fingerprints directly.
            verification.check(session)
            for snapshot in ordered:
                key = (AuthoredSlideCheckpoint, "id", str(snapshot["id"]))
                fingerprint = validated_fingerprints[snapshot["id"]]
                if key in verification.rows and verification.rows[key] != fingerprint:
                    reject("svg_partial_recovery_request_snapshot_changed")
                verification.rows[key] = fingerprint
            expected = validated_fingerprints
        else:
            for snapshot in ordered:
                verification.expect(AuthoredSlideCheckpoint, snapshot)
            expected = {snapshot["id"]: digest(snapshot) for snapshot in ordered}
        identity = str(job_id)
        if identity in verification.producers and verification.producers[identity] != expected:
            reject("svg_partial_recovery_request_snapshot_changed")
        # No storage binding here: finish() decodes these rows (its digest differs in kind).
        verification.producers[identity] = expected
        verification.check(session)
    if (_fresh_producer_proof is not None and verification is not None
            and verification.transaction is not None):
        # Only the immediate producer verifier consumes this validated fresh job;
        # actual page/seed consumers still require the full working archive below.
        verification.check(session)
        _fresh_producer_proof.append((verification, session, verification.transaction, jobs[0]))
        return None
    working = deepcopy(archive)
    working["checkpoints"] = ordered
    if (verification is not None and verification.transaction is not None
            and len(verification.checkpoint_archives) < _CHECKPOINT_ARCHIVE_MEMO_MAX_ENTRIES):
        import math
        import sys

        remaining = _CHECKPOINT_ARCHIVE_MEMO_MAX_BYTES - verification.checkpoint_archive_bytes
        # Include the dictionary slot, entry tuple, key and 32-byte guard.
        # Count repeated scalar references again conservatively. Shared
        # containers are safely left on the fresh path. Encoding stays temporary.
        retained = (512 + sys.getsizeof(cache_key)
            + sum(sys.getsizeof(value) for value in cache_key) + sys.getsizeof(bytes(32)))
        pending = [iter((working,))]
        seen = set()
        eligible = retained <= remaining
        while eligible and pending:
            try:
                value = next(pending[-1])
            except StopIteration:
                pending.pop()
                continue
            kind = type(value)
            if kind not in {dict, list, str, int, float, bool, type(None)}:
                eligible = False
                break
            retained += sys.getsizeof(value)
            if retained > remaining:
                eligible = False
                break
            if kind in {dict, list}:
                if id(value) in seen:
                    eligible = False
                    break
                seen.add(id(value))
                if kind is dict:
                    if any(type(key) is not str for key in value):
                        eligible = False
                        break
                    pending.append(iter(part for pair in value.items() for part in pair))
                else:
                    pending.append(iter(value))
            elif kind is float and not math.isfinite(value):
                eligible = False
        if eligible:
            encoded = _marshal_json_bytes(working)
            if encoded is not None:
                current_guard = hashlib.sha256(_CHECKPOINT_ARCHIVE_MEMO_GUARD_DOMAIN)
                current_guard.update(encoded)
                del encoded
                verification.checkpoint_archives[cache_key] = (working, current_guard.digest())
                verification.checkpoint_archive_bytes += retained
    return working


async def _verify_producer_rows(session, manifest, archive, *, verification=None):
    """Check real immutable producer rows, not only their archived copies."""
    fresh_producer_proof = []
    archive = await _hydrate_checkpoint_archive(session, manifest, archive, verification=verification,
        _fresh_producer_proof=fresh_producer_proof)
    if fresh_producer_proof:
        proof_verification, proof_session, proof_transaction, producer = fresh_producer_proof.pop()
        if (proof_verification is not verification or proof_session is not session
                or proof_transaction is not session.sync_session.get_transaction()):
            reject("svg_partial_recovery_request_snapshot_changed")
        verification.check(session)
        return producer
    jobs = await _fresh_recovery_model_rows(session, PresentationGenerationJob,
        PresentationGenerationJob.id == uuid.UUID(manifest["job_id"]))
    if (len(jobs) != 1 or digest(_dump(jobs[0])) != digest(archive.get("job"))
            or str(jobs[0].presentation_id) != manifest["presentation_id"]
            or str(jobs[0].workspace_operation_id) != manifest["operation_id"]):
        reject("svg_partial_recovery_history_changed")
    rows, row_storage = await _fresh_checkpoint_rows_sealed(session,
        AuthoredSlideCheckpoint.job_id == jobs[0].id)
    _count(verification, "rows_decoded_python", len(rows))
    current = {str(row.id): _dump(row) for row in rows}
    archived = {row["id"]: row for row in archive.get("checkpoints", [])}
    if (len(rows) != manifest["expected_page_count"] or current != archived
            or sorted(row.slide_index for row in rows) != list(range(jobs[0].total_slides))):
        reject("svg_partial_recovery_history_changed")
    if verification is not None:
        verification.expect(PresentationGenerationJob, archive["job"])
        expected = {key: digest(snapshot) for key, snapshot in archived.items()}
        job_id = str(jobs[0].id)
        if job_id in verification.producers and verification.producers[job_id] != expected:
            reject("svg_partial_recovery_request_snapshot_changed")
        verification.producers[job_id] = expected
        if row_storage is not None:
            # Bind the digest finish() computes, from these same private rows; finish then
            # compares it with ``expected`` exactly as a fresh decode would.
            verification.record_producer_storage(job_id, {str(row.id): (row_storage.get(str(row.id)), value)
                for row, value in zip(rows, await _producer_digests_off_loop(rows), strict=True)})
    return jobs[0]


def _historical_episode(operation, job):
    return (operation.state in {"failed", "cancelled", "dead_letter", "deadline_exceeded", "completed", "succeeded"}
            and job.state in {"failed", "cancelled", "published", "completed", "succeeded"})


async def verify_episode(session, operation, job, *, require_current_code=True, verification=None,
                         _operation_read=None, finish_verification=False, _retained_proofs=None):
    # A caller-owned holder ({operation id: proof}) may carry this owned,
    # finished historical proof to a later recheck in the same transaction.
    retained = (type(_retained_proofs) is dict and verification is None
                and _operation_read is None and not finish_verification
                and require_current_code is False)
    if retained:
        proof = _retained_proofs.pop(str(operation.id), None)
        manifest = await _recheck_retained_episode(session, operation, job, proof)
        if manifest is not None:
            _retained_proofs[str(operation.id)] = proof
            return manifest
    owns_verification = verification is None
    verification = verification or _EpisodeVerification(session)
    if owns_verification:
        verification.bind_live(operation, job)
    verification.check(session)
    identity = str(operation.id)
    if identity in verification.active:
        reject("svg_partial_recovery_lineage_conflict")
    # A changed historical job is always re-fingerprinted, including nested payloads,
    # unless these are the proven node's own live objects (rechecked once at finish()).
    node = (verification.node(operation, job)
            if not require_current_code and _operation_read is not None
            and type(getattr(verification, "node_proofs", None)) is dict else None)
    if node is None:
        operation_digest, operation_key = verification.operation_fingerprint(operation,
            _operation_read, require_current_code=require_current_code)
        job_digest = verification.job_fingerprint(job)
    else:
        operation_digest, operation_key = verification.operation_fingerprint(operation,
            _operation_read, require_current_code=require_current_code, node=node)
        job_digest = verification.job_fingerprint(job, node=node)
    key = (identity, operation_digest, job_digest)
    _count(verification, "episode_visits")
    visited = getattr(verification, "visited_episodes", None)
    if type(visited) is set:
        visited.add((identity, str(getattr(job, "id", None))))
    if require_current_code or key not in verification.episodes:
        verification.active.add(identity)
        try:
            manifest = await _verify_episode_once(session, operation, job,
                require_current_code=require_current_code, verification=verification,
                operation_digest=operation_digest)
            verification.check(session)
            payload_snapshot = digested = None
            if not require_current_code and operation_key is not None:
                fingerprint_key, snapshot, *prepared = operation_key
                # snapshot is exact JSON by construction (round-trip proof or a
                # deepcopy of a walk-proven payload, no await between), so it may
                # supply the marshal hint; a mismatch still takes the exact walk.
                # Its encoding was taken while it was private; it is never exposed.
                encoded = prepared[0] if prepared else _marshal_json_bytes(snapshot)
                started = time.perf_counter()
                same = _same_json_snapshot(snapshot, operation.request_payload, encoded)
                _count(verification, "payload_marshal_compares")
                _elapsed(verification, "t_marshal_s", started)
                if same is not True:
                    reject("svg_partial_recovery_request_snapshot_changed")
                # Proven equal just above with no await until the transfer below,
                # so the row memo may share this private copy instead of its own.
                payload_snapshot = (operation.request_payload, snapshot)
                projection = prepared[1] if len(prepared) > 1 else None
                if (type(projection) is dict
                        and projection.get("request_payload") is operation.request_payload
                        and _same_json_value(
                            {name: value for name, value in projection.items() if name != "request_payload"},
                            _operation_control(operation)) is True):
                    # operation_digest hashed exactly this projection before the
                    # await; its control still matches and its payload object is the
                    # one just proven equal to the private snapshot. Hash it once.
                    digested = (projection, operation_digest)
            if digested is not None:
                verification.expect(WorkspaceOperation, digested[0], _digested=digested,
                    _payload_snapshot=payload_snapshot)
            elif payload_snapshot is None:
                verification.expect(WorkspaceOperation, _operation_projection(operation))
            else:
                verification.expect(WorkspaceOperation, _operation_projection(operation),
                    _payload_snapshot=payload_snapshot)
            verification.expect(PresentationGenerationJob, _dump(job))
            if not require_current_code:
                if operation_key is not None:
                    # The initial exact-JSON copy stayed private in this
                    # frame while verification awaited. The comparison above
                    # proves it remains finite and exact; transfer ownership.
                    verification.operation_fingerprints[fingerprint_key] = (
                        snapshot, operation_digest, encoded)
                verification.episodes[key] = manifest
                if operation_key is not None and type(getattr(verification, "node_proofs", None)) is dict:
                    # Both live values were compared above with no await since.
                    verification.remember_node(operation, job, fingerprint_key, operation_digest, job_digest)
        finally:
            verification.active.remove(identity)
    else:
        _count(verification, "episode_memo_hits")
        manifest = verification.episodes[key]
    if owns_verification or finish_verification:
        # A supplied context still needs the fresh exit fence before admission
        # writes this child's ledger. Historical memo never replaces this read.
        await verification.finish()
        if require_current_code:
            _remember_prepared_episode(operation, job)
    if retained:
        # Retain only a finished success. finish() never reads the payload
        # memo copies, so they are released instead of held until recheck.
        # A copy shared with a row fingerprint stays held once, by that row.
        verification.operation_fingerprints.clear()
        _retained_proofs[identity] = (verification, str(job.id))
    return manifest


async def verify_prepared_episode(session, operation, job):
    """Reuse a completed preparation only for unchanged consumer authority."""
    key = _runtime_episode_key(operation, job)
    with _PREPARED_EPISODES_LOCK:
        prepared = key in _PREPARED_EPISODES
    if prepared and operation.state == "running":
        _require_prepared_episode(operation, job)
        return operation.request_payload["svg_partial_recovery_manifest"]
    return await verify_episode(session, operation, job)


def _retained_rows_pending(session, verification):
    """Unflushed edits of any model a proof read force the full cold verification."""
    tracked = {model for model, _, _ in verification.rows} | {
        WorkspaceOperation, PresentationGenerationJob, AuthoredSlideCheckpoint,
        PublicOperationAdmission, PublicProviderLogicalCall, PublicResourceLease,
        ProviderUsageReceipt, ProviderRuntimeBudget, ProviderRuntimeReservation}
    sync = session.sync_session
    return any(type(row) in tracked for pending in (sync.new, sync.dirty, sync.deleted)
               for row in pending)


async def _recheck_retained_episode(session, operation, job, proof):
    """Re-fence one finished owned historical proof instead of re-deriving it.

    finish() re-reads every row, control, ledger, producer and payload seal the
    proof consumed and compares them with its first-pass expectations; the
    caller still re-runs every binding outside verify_episode. Any mismatch
    returns None, so the unchanged cold verification decides.
    """
    import logging

    if proof is None:
        return None
    verification, job_id = proof
    reason = None
    if (type(verification) is not _EpisodeVerification or job_id != str(job.id)
            or verification.session is not session or verification.transaction is None
            or verification.live_operation is not None or verification.live_job is not None
            or verification.active
            or (WorkspaceOperation, "id", str(operation.id)) not in verification.rows
            or (PresentationGenerationJob, "id", job_id) not in verification.rows):
        reason = "unbound"
    elif _retained_rows_pending(session, verification):
        reason = "pending_session_changes"
    else:
        try:
            verification.check(session)
            # The objects consumed now must be the ones the proof fingerprinted.
            verification.expect(WorkspaceOperation, _operation_projection(operation))
            verification.expect(PresentationGenerationJob, _dump(job))
            await verification.finish()
        except PartialRecoveryRejected as error:
            reason = error.code
    if reason is not None:
        logging.getLogger(__name__).info(
            "SVG partial recovery retained proof not reused; verifying cold: operation=%s reason=%s",
            operation.id, reason)
        return None
    logging.getLogger(__name__).info(
        "SVG partial recovery retained proof rechecked: operation=%s", operation.id)
    return operation.request_payload.get("svg_partial_recovery_manifest") or {}


async def _verify_episode_once(session, operation, job, *, require_current_code, verification,
                                 operation_digest=None):
    from models.sql.async_presentation_generation_status import AsyncPresentationGenerationTaskModel
    from models.sql.source_content import SourceContentManifestRecord
    if not require_current_code and not _historical_episode(operation, job):
        reject("svg_partial_recovery_episode_unbound")
    manifest = operation.request_payload.get("svg_partial_recovery_manifest") or {}
    archive = operation.request_payload.get(ARCHIVE_KEY) or {}
    lineage = job.request_payload.get(LINEAGE_KEY) or {}
    append_only = manifest.get("append_only_recovery") is True
    archive_schema = archive.get("schema")
    if archive_schema == ARCHIVE_V1:
        if manifest.get("archive_schema") not in (None, ARCHIVE_V1) or lineage.get("archive_schema") not in (None, ARCHIVE_V1):
            reject("svg_partial_recovery_archive_schema_invalid")
    elif archive_schema in {ARCHIVE_V2, ARCHIVE_V3}:
        if (manifest.get("archive_schema") != archive_schema or lineage.get("archive_schema") != archive_schema
                or "operations" in archive):
            reject("svg_partial_recovery_archive_schema_invalid")
    else:
        reject("svg_partial_recovery_archive_schema_invalid")
    manifest_digest = digest(manifest)
    if (operation.request_payload.get("recovery_revision") != REVISION
            or manifest_digest != operation.request_payload.get("svg_partial_recovery_manifest_hash")
            or digest(archive) != manifest.get("archive_digest")
            or lineage != operation.request_payload.get("svg_partial_recovery_lineage")
            or lineage.get("schema") != ("svg-partial-recovery-lineage/v2" if append_only else "svg-partial-recovery-lineage/v1")
            or lineage.get("source_operation_id") != manifest.get("source_operation_id")
            or lineage.get("action_operation_id") != manifest.get("operation_id")
            or any(lineage.get(key) != manifest.get(key) for key in ("archive_digest",
                "known_indices", "new_indices", "unknown_indices", "frozen_request_digest", "frozen_style_digest"))
            or lineage.get("manifest_hash") != manifest_digest
            or lineage.get("operation_id") != str(operation.id)
            or str(operation.retry_of_operation_id) != manifest.get("operation_id")
            or operation.owner_id is None or str(operation.owner_id) != manifest.get("owner_id")
            or operation.request_payload.get("principal_tenant_id") != manifest.get("tenant_id")
            or (not append_only and str(operation.presentation_id) != manifest.get("presentation_id"))
            or job.workspace_operation_id != operation.id or job.presentation_id != operation.presentation_id
            or str(job.id) != (lineage.get("job_id") if append_only else manifest.get("job_id"))
            or (append_only and (lineage.get("presentation_id") != str(job.presentation_id)
                or lineage.get("source_job_id") != manifest.get("job_id")
                or lineage.get("source_presentation_id") != manifest.get("presentation_id")
                or manifest.get("source_request_routing") != _source_request_routing(
                    (archive.get("job") or {}).get("request_payload") or {})))
            or digest(_frozen_episode_projection(job.request_payload, manifest, copy=False,
                manifest_digest=manifest_digest)) != manifest.get("frozen_request_digest")
            or digest(job.svg_style_snapshot) != manifest.get("frozen_style_digest")
            or job.authored_outline != (archive.get("job") or {}).get("authored_outline")
            or (require_current_code and _code_digest() != manifest.get("code_digest"))):
        reject("svg_partial_recovery_episode_unbound")
    source_local_proof = manifest.get("source_local_continuation")
    if source_local_proof is not None and not isinstance(source_local_proof, dict):
        reject("svg_partial_recovery_episode_unbound")
    if source_local_proof is not None and (
            lineage.get("source_local_continuation_digest") != source_local_proof.get("digest")
            or source_local_proof.get("source_operation_id") != manifest.get("operation_id")
            or source_local_proof.get("source_job_id") != manifest.get("job_id")
            or source_local_proof.get("owner_id") != manifest.get("owner_id")
            or source_local_proof.get("tenant_id") != manifest.get("tenant_id")
            or source_local_proof.get("archive_digest") != manifest.get("archive_digest")):
        reject("svg_partial_recovery_episode_unbound")
    _verify_frozen_header_outline(job)
    if append_only:
        await _verify_producer_rows(session, manifest, archive, verification=verification)
    if archive_schema in {ARCHIVE_V2, ARCHIVE_V3}:
        await _verify_operation_references(session, operation, manifest, archive.get("operation_refs"),
            verification=verification, reuse_historical_seals=not require_current_code,
            operation_digest=operation_digest)
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
            if original is None:
                reject("svg_partial_recovery_history_changed")
            original_digest = digest(_dump(original))
            snapshot_digest = digest(snapshot)
            if original_digest != snapshot_digest:
                reject("svg_partial_recovery_history_changed")
            verification.expect(model, snapshot, _digested=(snapshot, snapshot_digest))
    historical_task = await session.get(AsyncPresentationGenerationTaskModel, archive.get("task", {}).get("id"))
    if historical_task is None:
        reject("svg_partial_recovery_history_changed")
    task_snapshot = archive.get("task")
    original_digest = digest(_dump(historical_task))
    task_digest = digest(task_snapshot)
    if original_digest != task_digest:
        reject("svg_partial_recovery_history_changed")
    verification.expect(AsyncPresentationGenerationTaskModel, archive["task"],
        _digested=(task_snapshot, task_digest))
    source_record = archive.get("source_record")
    if source_record is not None:
        original = await session.get(SourceContentManifestRecord, source_record["manifest_id"])
        if original is None:
            reject("svg_partial_recovery_source_changed")
        original_digest = digest(_dump(original))
        source_digest = digest(source_record)
        if original_digest != source_digest:
            reject("svg_partial_recovery_source_changed")
        verification.expect(SourceContentManifestRecord, source_record, identity="manifest_id",
            _digested=(source_record, source_digest))
    operation_ids = {item["id"] for item in archive.get(
        "operation_refs" if archive_schema in {ARCHIVE_V2, ARCHIVE_V3} else "operations", [])}
    await verification.ledger(operation_ids, manifest["job_id"], archive=archive)
    await verification.ledger({str(operation.id), *operation_ids}, job.id)
    return manifest


async def runtime_admission(session, *, job_id, operation_id,
                            expected_attempt_token=None, expected_fencing_token=None,
                            slide_index=None, stage=None, is_retry=False,
                            quality_repair_context=None):
    """Fail closed before spend; retain the actual generation job identity."""
    from models.sql.workspace import get_workspace_owner_scope
    from services.public_operation_admission import _require_phase2_provider_contract
    from utils.owner_context import get_current_owner_id, get_current_tenant_id

    job = await session.get(PresentationGenerationJob, job_id)
    if job is None or not (job.request_payload or {}).get(LINEAGE_KEY):
        return None
    operation = await _runtime_episode_operation(session, operation_id)
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
    _require_prepared_episode(operation, job)
    manifest = operation.request_payload["svg_partial_recovery_manifest"]
    if stage in {"author", "repair", "repair_remake", "repair_patch"}:
        eligible = {*manifest.get("new_indices", []), *manifest.get("unknown_indices", [])}
        if type(slide_index) is not int:
            reject("svg_partial_recovery_cached_provider_forbidden")
        if slide_index not in eligible:
            ctx = quality_repair_context
            if (slide_index not in manifest.get("known_indices", [])
                    or stage not in {"repair", "repair_remake", "repair_patch"}
                    or is_retry is not True or ctx is None or admission.retry_request_limit < 1
                    or getattr(ctx, "snapshot", None) != job.svg_style_snapshot
                    or getattr(ctx, "count", None) != job.total_slides
                    or digest(getattr(ctx, "partial_recovery_manifest", None)) != digest(manifest)
                    or not getattr(ctx, "quality_recovery_runtime_binding", None)
                    or ctx.quality_recovery_runtime_binding != getattr(ctx, "quality_recovery_input_binding", None)):
                reject("svg_partial_recovery_cached_provider_forbidden")
            from services.generation_core.drivers.svg.partial_quality_recovery import (
                current_episode_attempt_count, current_quality_repair_authorized,
            )
            from services.svg_authoring_profile import read_quality_recovery_plan
            plan = read_quality_recovery_plan(job.svg_style_snapshot)
            fields = ("partial_recovery", "input_digest", "quality_recovery", "correction_mode", "attempts",
                      "provider_failures", "inflight", "provider_terminal_responses",
                      "provider_dispatch_stopped", "provider_diagnostics")
            record = (await session.execute(select(*[
                AuthoredSlideCheckpoint.svg_checkpoint[key].label(key) for key in fields
            ]).where(AuthoredSlideCheckpoint.job_id == job.id,
                     AuthoredSlideCheckpoint.slide_index == slide_index))).mappings().one_or_none()
            payload = dict(record) if record is not None else {}
            if (plan is None or (payload.get("quality_recovery") or {}).get("status") != "repair_pending"
                    or payload.get("inflight") is not None or _unknown_provider(payload)
                    or current_episode_attempt_count(ctx, payload, slide_index) >= min(2, plan["page_attempt_limit"])):
                reject("svg_partial_recovery_cached_provider_forbidden")

            async def current_checkpoint(active_job, index):
                if active_job is not job or index != 0:
                    reject("svg_partial_recovery_page_unbound")
                execution = await session.scalar(select(
                    AuthoredSlideCheckpoint.svg_checkpoint["quality_recovery_execution"]
                ).where(AuthoredSlideCheckpoint.job_id == job.id,
                        AuthoredSlideCheckpoint.slide_index == 0))
                return {"quality_recovery_execution": execution}

            if not await current_quality_repair_authorized(ctx, payload, slide_index, job, current_checkpoint):
                reject("svg_partial_recovery_cached_provider_forbidden")
        checkpoint_id = await session.scalar(select(AuthoredSlideCheckpoint.id).where(
            AuthoredSlideCheckpoint.job_id == job.id,
            AuthoredSlideCheckpoint.slide_index == slide_index))
        if checkpoint_id is None:
            reject("svg_partial_recovery_page_unbound")
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
    if payload.get("final_selected_render_evidence") is not None:
        from services.svg_final_selected_continuation import verify_cached_render
        if input_digest != page.get("input_digest") or payload.get("input_digest") != page.get("input_digest"):
            reject("svg_partial_recovery_page_unbound")
        verify_cached_render(payload, manifest, index)
        return page
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


def _code_digest():
    """The partial recovery code identity that approval manifests and runtime digests carry.

    It is the reviewed revision's identity (``services/verifier_revisions``) while
    the running source is a reviewed state of that revision, and the raw
    ``code_source_state().raw`` otherwise.
    """
    from services.verifier_revisions import PARTIAL_RECOVERY_CODE_LEDGER

    return PARTIAL_RECOVERY_CODE_LEDGER.resolve(code_source_state())
