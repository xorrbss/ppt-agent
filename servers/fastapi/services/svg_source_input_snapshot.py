"""Context-bound verified SVG source-input snapshot (``svg-source-input-snapshot/v1``).

A partial-recovery child deck renders an inherited source manifest that belongs to
another presentation. Export must prove that inheritance is authorized; the legacy
proof (``svg_document_mutations._verified_inherited_slide_source`` ->
``svg_generation_assets._verify_inherited_source_manifest`` ->
``svg_partial_recovery.verify_episode``) re-reads and re-hashes the whole recovery
history on every cold export.

The generating attempt already ran that proof, with current code, at prepare time.
This module lets that attempt's publication persist the result through the existing
fenced artifact authority (``PublicArtifact`` + ``PublicArtifactPublication`` via
``svg_asset_persistence._persist_if_current``), and lets an export consume it with a
handful of small column reads. No statement here selects a payload column
(``request_payload``, ``svg_checkpoint``, ``result``, slide/theme JSON).

Contract rules:
- A present snapshot that is missing, tampered, non-canonical, for another context,
  or from a newer policy is rejected (``SourceInputSnapshotRejected``), never skipped.
- An absent snapshot, or a producer whose terminal accounting is not confirmed,
  returns ``None`` and the caller keeps the unchanged legacy proof.
- Proofs live only in the caller's per-preparation holder; there is no module cache.
- Legacy digest and seal domains are untouched; this digest has its own domain.
"""
from __future__ import annotations

import json
import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select

from utils.hashing import canonical_json_bytes, canonical_json_sha256, sha256_hex

LOGGER = logging.getLogger(__name__)

SNAPSHOT_SCHEMA = "svg-source-input-snapshot/v1"
SNAPSHOT_POLICY_VERSION = 1
VERIFIER_COMPAT = "svg-inherited-source-verifier/v1"
ACCEPTED_VERIFIER_COMPAT = frozenset({VERIFIER_COMPAT})
PARTIAL_RECOVERY_REVISION = "svg-partial-recovery/v1"
# Existing kind and media type: ck_public_artifact_kind needs no migration.
ARTIFACT_KIND = "svg-export-source-lineage"
MEDIA_TYPE = "application/json"
MAX_SNAPSHOT_BYTES = 256 * 1024
PUBLICATION_PREFIX = "svg-source-input/v1/"
ORIGIN_PUBLICATION = "generation-publication"
ORIGIN_BOOTSTRAP = "legacy-bootstrap"
EVIDENCE_ATTRIBUTE = "_svg_source_input_evidence"
_EXPORT_KINDS = frozenset({"export.pptx", "export.pdf"})
_TERMINAL_OPERATION_STATES = frozenset({"succeeded", "completed"})

#: Rollback switch (a code constant, not a user setting). ``False`` stops new writes;
#: readers keep consuming snapshots already emitted, and absent ones keep the legacy path.
WRITE_ENABLED = True

_BODY_KEYS = frozenset({"schema", "policyVersion", "verifierCompat", "origin", "context", "source",
                        "lineage", "pages", "pageCount", "pagesRevision", "digest"})
_CONTEXT_KEYS = frozenset({"presentationId", "ownerId", "ownerScope", "tenantId", "producerOperationId",
                           "producerJobId", "producerAttemptNumber", "producerFencingToken",
                           "producerExpectedRevision", "resultRevision", "producerRequestHash",
                           "producerJobRequestSha256", "producerContractVersion", "admissionId",
                           "admissionPolicyDigest", "recoveryRevision"})
_SOURCE_KEYS = frozenset({"presentationId", "sourceRevision", "manifestDigest", "pointerSha256",
                          "manifestPresentationId", "manifestPresentationRevision", "manifestOwnerId",
                          "manifestTenantId"})
_ORIGIN_KEYS = {ORIGIN_PUBLICATION: frozenset({"kind"}),
                ORIGIN_BOOTSTRAP: frozenset({"kind", "verifiedByOperationId", "verifiedByFencingToken",
                                             "verifiedAtRevision", "legacyVerifier"})}
_PAGE_KEYS = frozenset({"index", "slideSvgSha256", "assetEnvelopeSha256", "inputDigest"})


class SourceInputSnapshotRejected(ValueError):
    retryable = False

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def _reject(reason: str):
    raise SourceInputSnapshotRejected("svg_source_input_snapshot_" + reason)


@dataclass
class SourceInputSnapshotCounters:
    """Per-preparation evidence; ``historical_payload_bytes_read`` stays 0 on the snapshot path
    because no snapshot statement selects a payload column (see ``*_statement``)."""

    lookups: int = 0
    hits: int = 0
    absent: int = 0
    rejected: int = 0
    rechecks: int = 0
    live_rows_read: int = 0
    snapshot_bytes_read: int = 0
    historical_payload_bytes_read: int = 0
    legacy_verify_calls: int = 0
    bootstrap_payload_bytes_read: int = 0

    def as_dict(self) -> dict:
        return dict(vars(self))


@dataclass(frozen=True)
class SourceInputEvidence:
    """This attempt's own successful prepare-time proof; held on the request only."""

    operation_id: str
    job_id: str
    attempt_number: int
    attempt_token: str
    fencing_token: int
    owner_id: str
    tenant_id: str
    presentation_id: str
    source: dict


@dataclass(frozen=True)
class _Consumer:
    presentation_id: str
    revision: int
    owner_id: str
    tenant_id: str
    owner_scope: str


@dataclass(frozen=True)
class LiveView:
    job: dict
    operation: dict
    admission: dict | None


@dataclass(frozen=True)
class SourceInputSnapshotProof:
    body: dict
    publication_id: str
    artifact_id: str
    derived: bool
    source: Any = field(compare=False)
    pointer: Any = field(compare=False)
    owner: str = ""
    tenant: str = ""
    slides: Any = field(default=None, compare=False)


def publication_name(producer_operation_id) -> str:
    return PUBLICATION_PREFIX + uuid.UUID(str(producer_operation_id)).hex  # 52 <= 64


def snapshot_digest(body: dict) -> str:
    return canonical_json_sha256({"domain": SNAPSHOT_SCHEMA,
                                  "body": {key: value for key, value in body.items() if key != "digest"}})


def _text(value):
    return None if value is None else str(value)


def _utc(value):
    if value is None:
        return None
    if isinstance(value, str):
        value = datetime.fromisoformat(value)
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value


def _current_owner_scope() -> str:
    from models.sql.workspace import get_workspace_owner_scope
    return str(get_workspace_owner_scope())


# --- Column-only statements (structurally free of historical payload columns) -----------

def job_statement(presentation_id):
    from models.sql.presentation_generation_job import PresentationGenerationJob as J
    # Same "latest job of the presentation" rule as _verified_inherited_slide_source.
    return (select(J.id, J.presentation_id, J.workspace_operation_id, J.state, J.attempt_number,
                   J.attempt_token, J.lease_expires_at, J.published_at, J.last_started_at, J.request_sha256)
            .where(J.presentation_id == presentation_id).order_by(J.created_at.desc()).limit(1))


def job_by_id_statement(job_id):
    from models.sql.presentation_generation_job import PresentationGenerationJob as J
    return (select(J.id, J.presentation_id, J.workspace_operation_id, J.state, J.attempt_number,
                   J.attempt_token, J.lease_expires_at, J.published_at, J.last_started_at, J.request_sha256)
            .where(J.id == job_id))


def operation_statement(operation_id):
    from models.sql.workspace import WorkspaceOperation as O
    return select(O.id, O.presentation_id, O.owner_id, O.owner_scope, O.kind, O.state, O.attempt_number,
                  O.attempt_token, O.fencing_token, O.expected_revision, O.result_revision, O.request_hash,
                  O.started_at, O.contract_version).where(O.id == operation_id)


def admission_statement(operation_id):
    from models.sql.public_admission import PublicOperationAdmission as A
    return select(A.id, A.operation_id, A.owner_id, A.tenant_id, A.operation_kind, A.state, A.fence,
                  A.policy_digest).where(A.operation_id == operation_id)


def publication_statement(operation_id, name):
    from models.sql.public_artifact import PublicArtifactPublication as P
    return select(P.id, P.operation_id, P.publication_name, P.revision, P.artifact_id, P.quality_digest,
                  P.fencing_token).where(P.operation_id == operation_id, P.publication_name == name)


def derived_publication_statement(presentation_id, owner_id, name, producer_operation_id):
    from models.sql.public_artifact import PublicArtifactPublication as P
    from models.sql.workspace import WorkspaceOperation as O
    return (select(P.id, P.operation_id, P.publication_name, P.revision, P.artifact_id, P.quality_digest,
                   P.fencing_token, O.kind.label("op_kind"), O.state.label("op_state"),
                   O.presentation_id.label("op_presentation_id"), O.owner_id.label("op_owner_id"))
            .join(O, O.id == P.operation_id)
            .where(P.publication_name == name, P.operation_id != producer_operation_id,
                   O.presentation_id == presentation_id, O.owner_id == owner_id,
                   # Only a bootstrap whose export succeeded is promoted; a running or
                   # failed export's pointer stays invisible (absent), never blocks.
                   O.kind.in_(tuple(sorted(_EXPORT_KINDS))), O.state == "succeeded")
            .order_by(P.revision.desc(), P.id.desc())
            .limit(1))


def parent_statement(presentation_id):
    from models.sql.presentation import PresentationModel as M
    return select(M.id, M.owner_id, M.revision).where(M.id == presentation_id)


def snapshot_statements_for_audit(presentation_id, operation_id, owner_id) -> tuple:
    name = publication_name(operation_id)
    return (job_statement(presentation_id), job_by_id_statement(operation_id), operation_statement(operation_id),
            admission_statement(operation_id), publication_statement(operation_id, name),
            derived_publication_statement(presentation_id, owner_id, name, operation_id),
            parent_statement(presentation_id))


async def _first(session, statement, counters):
    row = (await session.execute(statement)).first()
    counters.live_rows_read += 1
    return None if row is None else dict(row._mapping)


# --- Live reads (seams for tests) --------------------------------------------------------

async def _load_live_view(session, presentation_id, counters):
    job = await _first(session, job_statement(presentation_id), counters)
    if job is None or job.get("workspace_operation_id") is None:
        return None
    operation = await _first(session, operation_statement(job["workspace_operation_id"]), counters)
    if operation is None:
        return None
    admission = await _first(session, admission_statement(job["workspace_operation_id"]), counters)
    return LiveView(job=job, operation=operation, admission=admission)


async def _load_producer_view(session, evidence, counters):
    job = await _first(session, job_by_id_statement(uuid.UUID(evidence.job_id)), counters)
    operation = await _first(session, operation_statement(uuid.UUID(evidence.operation_id)), counters)
    admission = await _first(session, admission_statement(uuid.UUID(evidence.operation_id)), counters)
    if job is None or operation is None or admission is None:
        return None
    return LiveView(job=job, operation=operation, admission=admission)


async def _find_publication(session, live, consumer, counters):
    """``(publication, None)`` primary, ``(publication, derived_operation)`` bootstrap, or None."""
    producer = live.operation["id"]
    name = publication_name(producer)
    primary = await _first(session, publication_statement(producer, name), counters)
    if primary is not None:
        return primary, None
    rows = (await session.execute(derived_publication_statement(
        uuid.UUID(consumer.presentation_id), uuid.UUID(consumer.owner_id), name, producer))).all()
    counters.live_rows_read += 1
    if not rows:
        return None
    # The newest succeeded bootstrap; each one is independently verified in full
    # against the live producer, source and pages, so choosing one shares no validity.
    item = dict(rows[0]._mapping)
    derived = {key: item.pop(key) for key in ("op_kind", "op_state", "op_presentation_id", "op_owner_id")}
    return item, derived


async def _load_parent(session, presentation_id, counters):
    try:
        identity = uuid.UUID(str(presentation_id))
    except (ValueError, TypeError, AttributeError):
        _reject("schema_invalid")
    return await _first(session, parent_statement(identity), counters)


async def _read_snapshot_artifact(session, publication, *, owner_id, tenant_id, counters, service=None):
    from services.artifact_store import ArtifactNotFound, configured_public_artifact_service
    service = service or configured_public_artifact_service()
    try:
        reference = await service.resolve_ref(session, artifact_id=publication["artifact_id"],
            owner_id=uuid.UUID(owner_id), tenant_id=tenant_id, expected_digest=publication["quality_digest"],
            check_object=False)
    except ArtifactNotFound:
        _reject("missing")
    if (reference.kind != ARTIFACT_KIND or reference.media_type != MEDIA_TYPE
            or str(reference.created_by_operation_id) != str(publication["operation_id"])
            or not 0 < reference.size_bytes <= MAX_SNAPSHOT_BYTES):
        _reject("artifact_invalid")
    try:
        payload = await service.read_ref_bytes(reference)
    except ArtifactNotFound:
        _reject("missing")
    if len(payload) != reference.size_bytes or sha256_hex(payload) != reference.sha256:
        _reject("tampered")
    counters.snapshot_bytes_read += len(payload)
    return payload


async def _persist_snapshot(session, *, context, name, revision, payload):
    from services.artifact_store import configured_public_artifact_service
    from services.svg_asset_persistence import _persist_if_current
    return await _persist_if_current(session, configured_public_artifact_service(), context=context,
        publication_name=name, revision=revision, kind=ARTIFACT_KIND, payload=payload, media_type=MEDIA_TYPE)


async def _load_partial_manifest(session, operation_id, counters):
    """Bootstrap only: one exact read of the producer's own recovery manifest."""
    from models.sql.workspace import WorkspaceOperation as O
    payload = await session.scalar(select(O.request_payload).where(O.id == operation_id))
    manifest = (payload or {}).get("svg_partial_recovery_manifest")
    counters.bootstrap_payload_bytes_read += len(canonical_json_bytes(payload or {}))
    return manifest if isinstance(manifest, dict) else None


# --- Pure contract ----------------------------------------------------------------------

def page_digests(slides) -> list:
    pages = []
    for position, slide in enumerate(slides):
        content = getattr(slide, "content", None) or {}
        receipt = content.get("svgReceipt") or {}
        svg = getattr(slide, "svg_content", None)
        if getattr(slide, "index", position) != position or not isinstance(svg, str) or not svg:
            return None
        pages.append({"index": position, "slideSvgSha256": sha256_hex(svg.encode("utf-8")),
                      "assetEnvelopeSha256": canonical_json_sha256(content.get("svgAssetManifest")),
                      "inputDigest": receipt.get("input_digest")})
    return pages or None


def promotable_page_digests(slides):
    """UNKNOWN/flagged/unverified pages are never promoted into a snapshot."""
    for slide in slides:
        receipt = ((getattr(slide, "content", None) or {}).get("svgReceipt")) or {}
        quality = receipt.get("quality") or {}
        if (receipt.get("terminal") or receipt.get("flag") or receipt.get("inflight")
                or quality.get("passed") is not True or quality.get("failures")):
            return None
    return page_digests(slides)


def source_binding(*, partial_manifest, inherited, pointer) -> dict:
    return {"presentationId": str(inherited.presentation_id), "sourceRevision": inherited.source_revision,
            "manifestDigest": inherited.digest, "pointerSha256": canonical_json_sha256(pointer),
            "manifestPresentationId": str(partial_manifest["presentation_id"]),
            "manifestPresentationRevision": partial_manifest["presentation_revision"],
            "manifestOwnerId": str(partial_manifest["owner_id"]),
            "manifestTenantId": str(partial_manifest["tenant_id"])}


def snapshot_context(live, *, presentation_id, owner_id, tenant_id, result_revision) -> dict:
    operation, job, admission = live.operation, live.job, live.admission
    return {"presentationId": str(presentation_id), "ownerId": str(owner_id),
            "ownerScope": str(operation["owner_scope"]), "tenantId": str(tenant_id),
            "producerOperationId": str(operation["id"]), "producerJobId": str(job["id"]),
            "producerAttemptNumber": operation["attempt_number"],
            "producerFencingToken": operation["fencing_token"],
            "producerExpectedRevision": operation["expected_revision"], "resultRevision": result_revision,
            "producerRequestHash": operation["request_hash"], "producerJobRequestSha256": job["request_sha256"],
            "producerContractVersion": operation["contract_version"], "admissionId": str(admission["id"]),
            "admissionPolicyDigest": admission["policy_digest"], "recoveryRevision": PARTIAL_RECOVERY_REVISION}


def build_snapshot_body(*, context, source, lineage, pages, pages_revision, origin) -> dict:
    body = {"schema": SNAPSHOT_SCHEMA, "policyVersion": SNAPSHOT_POLICY_VERSION,
            "verifierCompat": VERIFIER_COMPAT, "origin": origin, "context": context, "source": source,
            "lineage": lineage, "pages": pages, "pageCount": len(pages), "pagesRevision": pages_revision}
    body["digest"] = snapshot_digest(body)
    return body


def decode_snapshot(payload: bytes) -> dict:
    try:
        body = json.loads(payload)
    except (ValueError, UnicodeDecodeError):
        _reject("encoding_invalid")
    if not isinstance(body, dict) or canonical_json_bytes(body) != payload:
        _reject("encoding_invalid")
    return body


def producer_eligible(live) -> bool:
    """Only a published producer with confirmed (succeeded) admission accounting."""
    job, operation, admission = live.job, live.operation, live.admission
    return (job.get("state") == "published" and operation.get("state") in _TERMINAL_OPERATION_STATES
            and operation.get("kind") == "presentation.generate"
            and admission is not None and admission.get("state") == "succeeded")


def verify_snapshot(body, *, live, publication, derived, parent, consumer, source, pointer, slides=None):
    """Mirror the terminal branch of the legacy checks with live columns only. Raises on mismatch."""
    if not isinstance(body, dict) or body.get("schema") != SNAPSHOT_SCHEMA:
        _reject("schema_invalid")
    policy = body.get("policyVersion")
    if type(policy) is not int or policy < 1:
        _reject("schema_invalid")
    if policy > SNAPSHOT_POLICY_VERSION:
        _reject("policy_newer")
    if body.get("verifierCompat") not in ACCEPTED_VERIFIER_COMPAT:
        _reject("verifier_incompatible")
    if set(body) != _BODY_KEYS:
        _reject("schema_invalid")
    if body["digest"] != snapshot_digest(body):
        _reject("tampered")
    context, src, origin = body["context"], body["source"], body["origin"]
    if (not isinstance(context, dict) or set(context) != _CONTEXT_KEYS or not isinstance(src, dict)
            or set(src) != _SOURCE_KEYS or not isinstance(origin, dict)
            or set(origin) != _ORIGIN_KEYS.get(origin.get("kind"), frozenset({None}))
            or context["recoveryRevision"] != PARTIAL_RECOVERY_REVISION):
        _reject("schema_invalid")
    job, operation, admission = live.job, live.operation, live.admission
    if admission is None:
        _reject("producer_mismatch")
    # Consumer context: one presentation, principal and scope.
    if (context["presentationId"] != consumer.presentation_id or context["ownerId"] != consumer.owner_id
            or context["tenantId"] != consumer.tenant_id or context["ownerScope"] != consumer.owner_scope
            or src["manifestOwnerId"] != consumer.owner_id or src["manifestTenantId"] != consumer.tenant_id):
        _reject("context_mismatch")
    # Producer: exactly the latest, published, fenced attempt the snapshot names.
    published_at, job_started, operation_started = (_utc(job.get("published_at")),
        _utc(job.get("last_started_at")), _utc(operation.get("started_at")))
    v2 = operation.get("contract_version") == "public-operation/v2"
    if (_text(job["id"]) != context["producerJobId"] or _text(job["presentation_id"]) != consumer.presentation_id
            or _text(job["workspace_operation_id"]) != context["producerOperationId"]
            or job.get("attempt_token") is not None or job.get("lease_expires_at") is not None
            or None in (published_at, job_started, operation_started)
            or published_at < job_started or published_at < operation_started
            or job.get("attempt_number") != operation.get("attempt_number")
            or operation.get("attempt_number") != context["producerAttemptNumber"]
            or job.get("request_sha256") != context["producerJobRequestSha256"]
            or _text(operation["id"]) != context["producerOperationId"]
            or _text(operation["presentation_id"]) != consumer.presentation_id
            or _text(operation["owner_id"]) != consumer.owner_id
            or str(operation.get("owner_scope")) != context["ownerScope"]
            or type(operation.get("fencing_token")) is not int or operation["fencing_token"] < 1
            or operation["fencing_token"] != context["producerFencingToken"]
            or operation.get("expected_revision") != context["producerExpectedRevision"]
            or type(operation.get("result_revision")) is not int
            or operation["result_revision"] != context["resultRevision"]
            or consumer.revision < operation["result_revision"]
            or operation.get("request_hash") != context["producerRequestHash"]
            or operation.get("contract_version") != context["producerContractVersion"]
            or _text(admission["id"]) != context["admissionId"]
            or _text(admission["owner_id"]) != consumer.owner_id or admission["tenant_id"] != consumer.tenant_id
            or admission.get("operation_kind") != "generation"
            or admission.get("policy_digest") != context["admissionPolicyDigest"]
            or admission.get("fence") != (operation["fencing_token"] if v2 else 0)):
        _reject("producer_mismatch")
    # Publication pointer: primary under the producer's own fence, or a derived bootstrap.
    if derived is None:
        if (origin["kind"] != ORIGIN_PUBLICATION
                or _text(publication["operation_id"]) != context["producerOperationId"]
                or publication["revision"] != context["resultRevision"]
                or publication["fencing_token"] != context["producerFencingToken"]):
            _reject("publication_mismatch")
    elif (origin["kind"] != ORIGIN_BOOTSTRAP
            or _text(publication["operation_id"]) != origin["verifiedByOperationId"]
            or publication["operation_id"] == operation["id"]
            or derived.get("op_kind") not in _EXPORT_KINDS or derived.get("op_state") != "succeeded"
            or _text(derived.get("op_presentation_id")) != consumer.presentation_id
            or _text(derived.get("op_owner_id")) != consumer.owner_id
            or publication["fencing_token"] != origin["verifiedByFencingToken"]
            or publication["revision"] != origin["verifiedAtRevision"]
            or consumer.revision < origin["verifiedAtRevision"]):
        _reject("publication_mismatch")
    if publication.get("publication_name") != publication_name(context["producerOperationId"]):
        _reject("publication_mismatch")
    # Source manifest: the exact inherited pointer the legacy binding names.
    if (src["presentationId"] != str(source.presentation_id) or src["sourceRevision"] != source.source_revision
            or src["manifestDigest"] != source.digest or src["pointerSha256"] != canonical_json_sha256(pointer)
            or src["presentationId"] == consumer.presentation_id):
        _reject("source_mismatch")
    # Parent revision rule of _verify_inherited_source_manifest. Its alternative branch
    # (parent == current presentation) is unreachable here: export only asks for an
    # inherited proof when the source presentation differs (svg_export_assets.py:401).
    if (parent is None or _text(parent["id"]) != src["presentationId"]
            or _text(parent["owner_id"]) != consumer.owner_id
            or parent["revision"] != src["manifestPresentationRevision"]):
        _reject("source_parent_changed")
    # Pages: other content at the recorded revision can never share this validity.
    pages = body["pages"]
    if (not isinstance(pages, list) or body["pageCount"] != len(pages) or not pages
            or any(not isinstance(page, dict) or set(page) != _PAGE_KEYS for page in pages)):
        _reject("schema_invalid")
    if slides is not None and consumer.revision == body["pagesRevision"] and page_digests(slides) != pages:
        _reject("pages_mismatch")
    return True


# --- Consumer (export) ------------------------------------------------------------------

def _consumer(presentation, owner, tenant) -> _Consumer:
    return _Consumer(presentation_id=str(presentation.id), revision=presentation.revision,
                     owner_id=str(owner), tenant_id=str(tenant), owner_scope=_current_owner_scope())


async def consume_source_input_snapshot(session, presentation, source, pointer, owner, tenant, *,
                                        slides=None, counters=None, service=None):
    """Return a proof, ``None`` for "use the legacy proof", or raise ``SourceInputSnapshotRejected``."""
    counters = counters if counters is not None else SourceInputSnapshotCounters()
    counters.lookups += 1
    try:
        consumer = _consumer(presentation, owner, tenant)
        live = await _load_live_view(session, presentation.id, counters)
        if live is None or not producer_eligible(live):
            counters.absent += 1
            return None
        found = await _find_publication(session, live, consumer, counters)
        if found is None:
            counters.absent += 1
            return None
        publication, derived = found
        payload = await _read_snapshot_artifact(session, publication, owner_id=consumer.owner_id,
            tenant_id=consumer.tenant_id, counters=counters, service=service)
        body = decode_snapshot(payload)
        parent = await _load_parent(session, (body.get("source") or {}).get("presentationId"), counters)
        verify_snapshot(body, live=live, publication=publication, derived=derived, parent=parent,
                        consumer=consumer, source=source, pointer=pointer, slides=slides)
    except SourceInputSnapshotRejected as error:
        counters.rejected += 1
        LOGGER.warning("svg_source_input_snapshot_rejected presentation=%s code=%s", presentation.id, error.code)
        raise
    counters.hits += 1
    return SourceInputSnapshotProof(body=body, publication_id=str(publication["id"]),
        artifact_id=str(publication["artifact_id"]), derived=derived is not None, source=source,
        pointer=pointer, owner=str(owner), tenant=str(tenant), slides=slides)


async def recheck_source_input_snapshot(session, presentation, proof, *, counters=None):
    """Final-section re-fence: same small live reads, no artifact bytes, no history."""
    counters = counters if counters is not None else SourceInputSnapshotCounters()
    counters.rechecks += 1
    consumer = _consumer(presentation, proof.owner, proof.tenant)
    live = await _load_live_view(session, presentation.id, counters)
    if live is None or not producer_eligible(live):
        _reject("producer_changed")
    found = await _find_publication(session, live, consumer, counters)
    if found is None:
        _reject("publication_changed")
    publication, derived = found
    if (str(publication["id"]) != proof.publication_id or str(publication["artifact_id"]) != proof.artifact_id
            or (derived is not None) != proof.derived):
        _reject("publication_changed")
    parent = await _load_parent(session, proof.body["source"]["presentationId"], counters)
    return verify_snapshot(proof.body, live=live, publication=publication, derived=derived, parent=parent,
                           consumer=consumer, source=proof.source, pointer=proof.pointer, slides=proof.slides)


# --- Producer (generation publication) --------------------------------------------------

def record_source_input_evidence(request, *, job, context, partial_manifest, inherited, pointer):
    """Called right after this attempt's prepare-time inherited-source proof succeeded."""
    try:
        evidence = SourceInputEvidence(operation_id=str(context.operation_id), job_id=str(job.id),
            attempt_number=context.attempt_number, attempt_token=context.attempt_token,
            fencing_token=context.fencing_token, owner_id=str(context.owner_id), tenant_id=str(context.tenant_id),
            presentation_id=str(job.presentation_id),
            source=source_binding(partial_manifest=partial_manifest, inherited=inherited, pointer=pointer))
    except (KeyError, TypeError, ValueError):
        return None
    setattr(request, EVIDENCE_ATTRIBUTE, evidence)
    return evidence


def _producer_current(live, evidence, *, presentation_id, owner_id) -> bool:
    job, operation, admission = live.job, live.operation, live.admission
    return (operation.get("state") == "running" and operation.get("kind") == "presentation.generate"
            and _text(operation["id"]) == evidence.operation_id
            and _text(operation["presentation_id"]) == str(presentation_id)
            and _text(operation["owner_id"]) == str(owner_id)
            and operation.get("attempt_token") == evidence.attempt_token
            and operation.get("attempt_number") == evidence.attempt_number
            and operation.get("fencing_token") == evidence.fencing_token
            and _text(job["id"]) == evidence.job_id and job.get("state") == "staging"
            and _text(job["workspace_operation_id"]) == evidence.operation_id
            and job.get("attempt_number") == evidence.attempt_number
            and admission.get("state") == "active" and admission.get("operation_kind") == "generation"
            and admission.get("tenant_id") == evidence.tenant_id and _text(admission["owner_id"]) == str(owner_id))


def _publication_context(evidence):
    from services.svg_asset_persistence import SvgPublicationContext
    return SvgPublicationContext(operation_id=uuid.UUID(evidence.operation_id), owner_id=uuid.UUID(evidence.owner_id),
        tenant_id=evidence.tenant_id, attempt_number=evidence.attempt_number, attempt_token=evidence.attempt_token,
        fencing_token=evidence.fencing_token, operation_kind="generation")


async def publish_source_input_snapshot(session, *, request, job, presentation_id, owner_id, result_revision,
                                        published_slides):
    """Persist this attempt's fresh proof inside its publication transaction; never fails it."""
    if not WRITE_ENABLED or job is None:
        return None
    evidence = getattr(request, EVIDENCE_ATTRIBUTE, None)
    if (not isinstance(evidence, SourceInputEvidence) or evidence.job_id != str(job.id)
            or evidence.operation_id != str(job.workspace_operation_id)
            or evidence.attempt_token != job.attempt_token
            or evidence.fencing_token != getattr(job, "_workspace_fencing_token", None)
            or evidence.presentation_id != str(presentation_id) or evidence.owner_id != str(owner_id)):
        return None
    pages = promotable_page_digests(published_slides)
    if pages is None:
        return None
    counters = SourceInputSnapshotCounters()
    try:
        async with session.begin_nested():
            live = await _load_producer_view(session, evidence, counters)
            if live is None or not _producer_current(live, evidence, presentation_id=presentation_id,
                                                     owner_id=owner_id):
                return None
            body = build_snapshot_body(
                context=snapshot_context(live, presentation_id=presentation_id, owner_id=owner_id,
                                         tenant_id=evidence.tenant_id, result_revision=result_revision),
                source=dict(evidence.source), lineage={"verifiedAt": "prepare", "verifier": "verify_episode"},
                pages=pages, pages_revision=result_revision, origin={"kind": ORIGIN_PUBLICATION})
            return await _persist_snapshot(session, context=_publication_context(evidence),
                name=publication_name(evidence.operation_id), revision=result_revision,
                payload=canonical_json_bytes(body))
    except Exception:  # noqa: BLE001 - an absent snapshot keeps the legacy export proof
        LOGGER.warning("svg_source_input_snapshot_not_written operation=%s", evidence.operation_id, exc_info=True)
        return None


async def bootstrap_derived_source_input_snapshot(session, *, presentation, source, pointer, context, slides,
                                                  legacy_verify=None, counters=None):
    """Fixture-covered only; no serving caller. One bounded legacy proof, then a NEW derived
    snapshot under the caller's live export fence. Producer rows and history stay untouched."""
    counters = counters if counters is not None else SourceInputSnapshotCounters()
    if not WRITE_ENABLED or context.operation_kind != "export":
        return None
    owner, tenant = str(context.owner_id), str(context.tenant_id)
    consumer = _consumer(presentation, owner, tenant)
    live = await _load_live_view(session, presentation.id, counters)
    if live is None or not producer_eligible(live):
        return None
    if await _find_publication(session, live, consumer, counters) is not None:
        return None  # never replaces an emitted snapshot
    if legacy_verify is None:
        from services.svg_document_mutations import _verified_inherited_slide_source as legacy_verify
    counters.legacy_verify_calls += 1
    if not await legacy_verify(session, presentation, source, pointer, context.owner_id, context.tenant_id):
        return None
    if await _load_live_view(session, presentation.id, counters) != live:
        return None
    manifest = await _load_partial_manifest(session, live.operation["id"], counters)
    pages = promotable_page_digests(slides)
    if manifest is None or pages is None:
        return None
    try:
        binding = manifest["source_asset_binding"]
        src = source_binding(partial_manifest=manifest, inherited=source, pointer=pointer)
        if (binding["digest"] != source.digest or str(binding["presentation_id"]) != str(source.presentation_id)
                or binding["source_revision"] != source.source_revision
                or canonical_json_sha256(binding["pointer"]) != canonical_json_sha256(pointer)):
            return None
    except (KeyError, TypeError):
        return None
    body = build_snapshot_body(
        context=snapshot_context(live, presentation_id=presentation.id, owner_id=owner, tenant_id=tenant,
                                 result_revision=live.operation["result_revision"]),
        source=src, lineage={"verifiedAt": "legacy-bootstrap", "verifier": "verify_episode"},
        pages=pages, pages_revision=presentation.revision,
        origin={"kind": ORIGIN_BOOTSTRAP, "verifiedByOperationId": str(context.operation_id),
                "verifiedByFencingToken": context.fencing_token, "verifiedAtRevision": presentation.revision,
                "legacyVerifier": VERIFIER_COMPAT})
    return await _persist_snapshot(session, context=context, name=publication_name(live.operation["id"]),
                                   revision=presentation.revision, payload=canonical_json_bytes(body))
