"""svg-source-input-snapshot/v1: producer, consumer, fail-closed rules, rollback, bootstrap.

Pure fakes for the small live rows and the artifact authority; the statement-shape test
proves no snapshot read selects a historical payload column.
"""
from __future__ import annotations

import asyncio
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

import services.svg_source_input_snapshot as snap
from utils.hashing import canonical_json_bytes, sha256_hex

OWNER, TARGET, SOURCE_DECK = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
OPERATION, JOB, ADMISSION, EXPORT_OP = uuid.uuid4(), uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
TENANT, SCOPE, TOKEN, FENCE, REVISION = "tenant-a", "scope-a", "attempt-token", 7, 4
POINTER = {"ref": str(uuid.uuid4()), "sha256": "a" * 64}
T0 = datetime(2026, 10, 21, tzinfo=timezone.utc)


def _run(coroutine):
    return asyncio.run(coroutine)


def _slides(svg_suffix=""):
    return [SimpleNamespace(index=index, svg_content=f"<svg id='p{index}'{svg_suffix}/>", content={
        "svgAssetManifest": {"sourceManifest": POINTER, "sourcePageIndex": index},
        "svgReceipt": {"input_digest": f"in-{index}", "quality": {"passed": True, "verified": True}}})
        for index in range(3)]


SOURCE = SimpleNamespace(presentation_id=SOURCE_DECK, source_revision=2, digest="d" * 64)
PARTIAL = {"presentation_id": str(SOURCE_DECK), "presentation_revision": 3, "owner_id": str(OWNER),
           "tenant_id": TENANT, "source_asset_binding": {"presentation_id": str(SOURCE_DECK), "source_revision": 2,
                                                         "digest": "d" * 64, "pointer": POINTER}}


def _producer_live():
    return snap.LiveView(
        job={"id": JOB, "presentation_id": TARGET, "workspace_operation_id": OPERATION, "state": "staging",
             "attempt_number": 1, "attempt_token": TOKEN, "lease_expires_at": T0, "published_at": None,
             "last_started_at": T0, "request_sha256": "j" * 64},
        operation={"id": OPERATION, "presentation_id": TARGET, "owner_id": OWNER, "owner_scope": SCOPE,
                   "kind": "presentation.generate", "state": "running", "attempt_number": 1, "attempt_token": TOKEN,
                   "fencing_token": FENCE, "expected_revision": REVISION - 1, "result_revision": None,
                   "request_hash": "r" * 64, "started_at": T0, "contract_version": "public-operation/v2"},
        admission={"id": ADMISSION, "operation_id": OPERATION, "owner_id": OWNER, "tenant_id": TENANT,
                   "operation_kind": "generation", "state": "active", "fence": FENCE, "policy_digest": "p" * 64})


def _published(live):
    return snap.LiveView(
        job={**live.job, "state": "published", "attempt_token": None, "lease_expires_at": None,
             "published_at": T0 + timedelta(minutes=5)},
        operation={**live.operation, "state": "succeeded", "attempt_token": None, "result_revision": REVISION},
        admission={**live.admission, "state": "succeeded"})


class _Session:
    def __init__(self):
        self.writes = []

    @asynccontextmanager
    async def begin_nested(self):
        yield self

    def add(self, row):  # pragma: no cover - must never be called
        self.writes.append(row)


class _Artifacts:
    """Fake authority: owner/tenant/digest-bound rows over immutable bytes."""

    def __init__(self):
        self.rows, self.blobs = {}, {}

    def put(self, payload, *, creator):
        ref = SimpleNamespace(artifact_id=uuid.uuid4(), owner_id=OWNER, tenant_id=TENANT, sha256=sha256_hex(payload),
                              kind=snap.ARTIFACT_KIND, media_type=snap.MEDIA_TYPE, created_by_operation_id=creator,
                              size_bytes=len(payload))
        self.rows[ref.artifact_id], self.blobs[ref.artifact_id] = ref, payload
        return ref

    async def resolve_ref(self, session, *, artifact_id, owner_id, tenant_id, expected_digest, check_object=True):
        from services.artifact_store import ArtifactNotFound
        ref = self.rows.get(artifact_id)
        if ref is None or ref.owner_id != owner_id or ref.tenant_id != tenant_id or ref.sha256 != expected_digest:
            raise ArtifactNotFound("artifact.ref_not_found")
        return ref

    async def read_ref_bytes(self, ref):
        from services.artifact_store import ArtifactNotFound
        if ref.artifact_id not in self.blobs:
            raise ArtifactNotFound("artifact.object_missing")
        return self.blobs[ref.artifact_id]


@pytest.fixture
def world(monkeypatch):
    state = SimpleNamespace(live=_producer_live(), publications=[], artifacts=_Artifacts(), legacy_calls=0,
                            parent={"id": SOURCE_DECK, "owner_id": OWNER, "revision": 3}, derived_op=None)
    monkeypatch.setattr(snap, "_current_owner_scope", lambda: SCOPE)
    monkeypatch.setattr(snap, "WRITE_ENABLED", True)

    async def load_live(session, presentation_id, counters):
        counters.live_rows_read += 3
        return state.live

    async def load_producer(session, evidence, counters):
        counters.live_rows_read += 3
        return state.live

    async def find_publication(session, live, consumer, counters):
        counters.live_rows_read += 1
        primary = [p for p in state.publications if p["operation_id"] == live.operation["id"]]
        if primary:
            return primary[0], None
        # Mirrors derived_publication_statement: only a succeeded export's bootstrap is visible.
        derived = [p for p in state.publications if p["operation_id"] != live.operation["id"]]
        if derived and (state.derived_op or {}).get("op_state") == "succeeded":
            return derived[-1], state.derived_op
        return None

    async def load_parent(session, presentation_id, counters):
        counters.live_rows_read += 1
        return state.parent

    async def persist(session, *, context, name, revision, payload):
        ref = state.artifacts.put(payload, creator=context.operation_id)
        state.publications.append({"id": uuid.uuid4(), "operation_id": context.operation_id, "publication_name": name,
                                   "revision": revision, "artifact_id": ref.artifact_id, "quality_digest": ref.sha256,
                                   "fencing_token": context.fencing_token})
        return {"ref": str(ref.artifact_id), "sha256": ref.sha256}

    async def read_artifact(session, publication, *, owner_id, tenant_id, counters, service=None):
        return await _ORIGINAL_READ(session, publication, owner_id=owner_id, tenant_id=tenant_id,
                                    counters=counters, service=state.artifacts)

    async def legacy_must_not_run(*args, **kwargs):  # the whole-history proof
        state.legacy_calls += 1
        raise AssertionError("legacy verify_episode path must not run on a valid snapshot")

    monkeypatch.setattr(snap, "_load_live_view", load_live)
    monkeypatch.setattr(snap, "_load_producer_view", load_producer)
    monkeypatch.setattr(snap, "_find_publication", find_publication)
    monkeypatch.setattr(snap, "_load_parent", load_parent)
    monkeypatch.setattr(snap, "_persist_snapshot", persist)
    monkeypatch.setattr(snap, "_read_snapshot_artifact", read_artifact)
    monkeypatch.setattr("services.svg_document_mutations._verified_inherited_slide_source", legacy_must_not_run)
    monkeypatch.setattr("services.svg_generation_assets._verify_inherited_source_manifest", legacy_must_not_run)
    return state


_ORIGINAL_READ = snap._read_snapshot_artifact


def _request_with_evidence():
    request = SimpleNamespace()
    context = SimpleNamespace(operation_id=OPERATION, owner_id=OWNER, tenant_id=TENANT, attempt_number=1,
                              attempt_token=TOKEN, fencing_token=FENCE)
    job = SimpleNamespace(id=JOB, presentation_id=TARGET)
    snap.record_source_input_evidence(request, job=job, context=context, partial_manifest=PARTIAL,
                                      inherited=SOURCE, pointer=POINTER)
    return request


def _publish(world, request=None, slides=None):
    job = SimpleNamespace(id=JOB, workspace_operation_id=OPERATION, attempt_token=TOKEN, _workspace_fencing_token=FENCE)
    result = _run(snap.publish_source_input_snapshot(_Session(), request=request or _request_with_evidence(), job=job,
        presentation_id=TARGET, owner_id=OWNER, result_revision=REVISION, published_slides=slides or _slides()))
    world.live = _published(world.live)
    return result


def _deck(revision=REVISION):
    return SimpleNamespace(id=TARGET, revision=revision)


def _consume(slides=None, deck=None, tenant=TENANT, pointer=POINTER, counters=None):
    return _run(snap.consume_source_input_snapshot(_Session(), deck or _deck(), SOURCE, pointer, OWNER, tenant,
                                                   slides=slides or _slides(), counters=counters))


def _rewrite_snapshot(world, mutate, *, redigest=True):
    publication = world.publications[0]
    body = snap.decode_snapshot(world.artifacts.blobs[publication["artifact_id"]])
    mutate(body)
    if redigest:
        body["digest"] = snap.snapshot_digest(body)
    ref = world.artifacts.put(canonical_json_bytes(body), creator=publication["operation_id"])
    publication.update(artifact_id=ref.artifact_id, quality_digest=ref.sha256)


def test_valid_snapshot_is_used_with_zero_historical_payload_bytes(world):
    assert _publish(world) is not None
    counters = snap.SourceInputSnapshotCounters()
    proof = _consume(counters=counters)
    assert proof is not None and not proof.derived and world.legacy_calls == 0
    assert counters.hits == 1 and counters.historical_payload_bytes_read == 0
    assert 0 < counters.snapshot_bytes_read <= snap.MAX_SNAPSHOT_BYTES
    assert _run(snap.recheck_source_input_snapshot(_Session(), _deck(), proof, counters=counters)) is True


def test_snapshot_statements_never_select_historical_payload_columns():
    forbidden = {"request_payload", "svg_checkpoint", "result", "svg_header_design_checkpoint", "content",
                 "svg_content", "theme", "outlines", "authored_outline", "svg_style_snapshot"}
    name = snap.publication_name(OPERATION)
    statements = (
        snap.job_statement(TARGET),
        snap.job_by_id_statement(OPERATION),
        snap.operation_statement(OPERATION),
        snap.admission_statement(OPERATION),
        snap.publication_statement(OPERATION, name),
        snap.derived_publication_statement(TARGET, OWNER, name, OPERATION),
        snap.parent_statement(TARGET),
    )
    for statement in statements:
        names = {column.key for column in statement.selected_columns}
        assert names and not names & forbidden, names


def test_tampered_snapshot_is_rejected(world):
    _publish(world)
    _rewrite_snapshot(world, lambda body: body["context"].update(resultRevision=REVISION + 1), redigest=False)
    with pytest.raises(snap.SourceInputSnapshotRejected, match="tampered"):
        _consume()


def test_missing_snapshot_artifact_is_rejected(world):
    _publish(world)
    world.artifacts.blobs.clear()
    with pytest.raises(snap.SourceInputSnapshotRejected, match="missing"):
        _consume()


@pytest.mark.parametrize("case", ["tenant", "pointer", "source_deck_edited", "other_candidate", "producer_refenced"])
def test_wrong_context_snapshot_is_rejected(world, case):
    _publish(world)
    kwargs = {}
    if case == "tenant":
        kwargs["tenant"] = "tenant-b"
    elif case == "pointer":
        kwargs["pointer"] = {**POINTER, "sha256": "b" * 64}
    elif case == "source_deck_edited":
        world.parent = {**world.parent, "revision": 4}
    elif case == "other_candidate":
        kwargs["slides"] = _slides(svg_suffix=" data-x='1'")
    else:
        world.live = snap.LiveView(job=world.live.job, operation={**world.live.operation, "fencing_token": FENCE + 1},
                                   admission={**world.live.admission, "fence": FENCE + 1})
    with pytest.raises(snap.SourceInputSnapshotRejected):
        _consume(**kwargs)


def test_newer_policy_snapshot_is_rejected(world):
    _publish(world)
    _rewrite_snapshot(world, lambda body: body.update(policyVersion=snap.SNAPSHOT_POLICY_VERSION + 1))
    with pytest.raises(snap.SourceInputSnapshotRejected, match="policy_newer"):
        _consume()


def test_absent_or_unconfirmed_snapshot_keeps_the_legacy_path(world):
    world.live = _published(world.live)
    counters = snap.SourceInputSnapshotCounters()
    assert _consume(counters=counters) is None and counters.absent == 1
    world.publications.clear()
    world.live = _producer_live()
    _publish(world)
    world.live = snap.LiveView(job=world.live.job, operation=world.live.operation,
                               admission={**world.live.admission, "state": "active"})  # UNKNOWN accounting
    assert _consume() is None


def test_unknown_or_flagged_pages_are_never_promoted(world):
    slides = _slides()
    slides[1].content["svgReceipt"]["inflight"] = {"provider": "unknown"}
    assert _publish(world, slides=slides) is None and world.publications == []


def test_rollback_flag_stops_writing_but_reader_reads_emitted_snapshots(world, monkeypatch):
    _publish(world)
    monkeypatch.setattr(snap, "WRITE_ENABLED", False)
    assert _consume() is not None
    world.publications.clear()
    world.live = _producer_live()
    assert _publish(world) is None and world.publications == []
    assert _consume() is None  # absent again: legacy path decides


def test_evidence_from_another_attempt_is_not_published(world):
    request = _request_with_evidence()
    job = SimpleNamespace(id=JOB, workspace_operation_id=OPERATION, attempt_token="next-attempt",
                          _workspace_fencing_token=FENCE + 1)
    assert _run(snap.publish_source_input_snapshot(_Session(), request=request, job=job, presentation_id=TARGET,
        owner_id=OWNER, result_revision=REVISION, published_slides=_slides())) is None
    assert world.publications == []


def test_legacy_bootstrap_writes_a_new_derived_snapshot_with_origin_lineage(world, monkeypatch):
    world.live = _published(world.live)
    calls = []

    async def legacy_once(session, presentation, source, pointer, owner, tenant):
        calls.append((presentation.id, source.digest))
        return True

    async def partial_manifest(session, operation_id, counters):
        counters.bootstrap_payload_bytes_read += 1
        return PARTIAL

    monkeypatch.setattr(snap, "_load_partial_manifest", partial_manifest)
    session = _Session()
    export = SimpleNamespace(operation_id=EXPORT_OP, owner_id=OWNER, tenant_id=TENANT, attempt_number=1,
                             attempt_token="export-token", fencing_token=3, operation_kind="export")
    assert _run(snap.bootstrap_derived_source_input_snapshot(session, presentation=_deck(), source=SOURCE,
        pointer=POINTER, context=export, slides=_slides(), legacy_verify=legacy_once)) is not None
    assert len(calls) == 1 and session.writes == []  # originals untouched
    publication = world.publications[0]
    body = snap.decode_snapshot(world.artifacts.blobs[publication["artifact_id"]])
    assert publication["operation_id"] == EXPORT_OP
    assert body["origin"] == {"kind": snap.ORIGIN_BOOTSTRAP, "verifiedByOperationId": str(EXPORT_OP),
                              "verifiedByFencingToken": 3, "verifiedAtRevision": REVISION,
                              "legacyVerifier": snap.VERIFIER_COMPAT}
    assert body["context"]["producerOperationId"] == str(OPERATION)
    world.derived_op = {"op_kind": "export.pptx", "op_state": "running", "op_presentation_id": TARGET,
                        "op_owner_id": OWNER}
    assert _consume() is None  # not promoted (absent, legacy decides) until its export op succeeded
    world.derived_op = {**world.derived_op, "op_state": "succeeded"}
    proof = _consume()
    assert proof is not None and proof.derived and world.legacy_calls == 0
    assert _run(snap.bootstrap_derived_source_input_snapshot(session, presentation=_deck(), source=SOURCE,
        pointer=POINTER, context=export, slides=_slides(), legacy_verify=legacy_once)) is None
    assert len(calls) == 1  # bounded: never re-run once a snapshot exists


def test_export_bootstrap_hook_reuses_this_exports_legacy_proof_and_never_fails_export(world, monkeypatch):
    """Production wiring: an export that proved its inherited source the legacy way leaves
    one derived snapshot under its own fence, without a second history walk."""
    from services.svg_document_mutations import _PublicationSourceVerification
    from utils.hashing import canonical_json_sha256

    world.live = _published(world.live)

    async def partial_manifest(session, operation_id, counters):
        counters.bootstrap_payload_bytes_read += 1
        return PARTIAL

    monkeypatch.setattr(snap, "_load_partial_manifest", partial_manifest)
    export = SimpleNamespace(operation_id=EXPORT_OP, owner_id=OWNER, tenant_id=TENANT, attempt_number=1,
                             attempt_token="export-token", fencing_token=3, operation_kind="export")
    key = (str(TARGET), REVISION, str(OWNER), TENANT, str(SOURCE_DECK), SOURCE.digest,
           canonical_json_sha256(POINTER))

    def proven_verification(**kwargs):
        verification = _PublicationSourceVerification(_Session(), _deck(), source_snapshot_slides=_slides(), **kwargs)
        verification.sources[key] = (SOURCE, POINTER, OWNER, TENANT, {})  # this export's legacy proof
        return verification

    assert _run(proven_verification().bootstrap_source_snapshots(export)) == 0  # refresh path: off
    assert _run(proven_verification(use_source_snapshot=True).bootstrap_source_snapshots(export)) == 1
    assert world.legacy_calls == 0 and len(world.publications) == 1
    assert world.publications[0]["operation_id"] == EXPORT_OP
    world.derived_op = {"op_kind": "export.pptx", "op_state": "succeeded", "op_presentation_id": TARGET,
                        "op_owner_id": OWNER}
    assert _consume() is not None and world.legacy_calls == 0
    # Once a snapshot is visible no further bootstrap is written.
    assert _run(proven_verification(use_source_snapshot=True).bootstrap_source_snapshots(export)) == 0
    assert len(world.publications) == 1

    async def broken(*args, **kwargs):
        raise RuntimeError("artifact store unavailable")

    world.publications.clear()
    world.derived_op = None
    monkeypatch.setattr(snap, "_persist_snapshot", broken)
    assert _run(proven_verification(use_source_snapshot=True).bootstrap_source_snapshots(export)) == 0