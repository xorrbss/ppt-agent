"""The registered timeout-repurchase incident is data behind one digest-checked verifier.

The scripted ledger below reproduces the incident's whole approval chain
(interrupted batch -> zero-dispatch replay -> bounded overload -> completion ->
continuation -> quality continuation). Its manifest digests were captured from
the implementation that still held the incident literals inline, so they pin
that every stored approval keeps building byte-identically.
"""
from __future__ import annotations

import json
import re
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from uuid import NAMESPACE_URL, uuid5

import pytest

from services import svg_presentation_checkpoint as checkpoint
from services import svg_timeout_repurchase as general
from services import svg_timeout_repurchase_incident as incident
from utils.hashing import canonical_json_sha256, sha256_hex

V3 = "svg-provider-timeout-repurchase/v3"
SERVICES = Path(__file__).resolve().parents[2] / "services"

# Manifest digests of the scripted chain, captured from the inline-literal implementation.
GOLDEN_CHAIN_DIGESTS = {
    "interrupted": "6943a9e222dd90f6177672589d40f91de2179b841014eec906edca1c2fe37b43",
    "zero_dispatch": "43be3b7fe95dc0e175347147ae2a3522379439918b28b072a914562e094c31d8",
    "overload": "4ac0ba3019fa132aa90770039b91d3acf36f7dd354727964014bf60b3dc09648",
    "completion": "8f748c1dbb29196c3707fe67dc7fbc4e07797d01eff740011fe497ce4e2c758b",
    "continuation": "15e3e59e3b1aa26c987af39518a3d76613b50bcd74f00d2343a3e925ed0592fc",
    "quality": "3874de40b0aea6b987b3270358ca621b1ce5521086a382b41358a33ed262d5c4",
}


@pytest.fixture(autouse=True)
def _fresh_registry():
    incident._verified_registry_text.cache_clear()
    yield
    incident._verified_registry_text.cache_clear()


class Obj:
    def __init__(self, **values):
        self.__dict__.update(values)

    def model_dump(self, mode="json"):
        return {key: deepcopy(value) for key, value in self.__dict__.items()}


class Listing(list):
    def all(self):
        return list(self)


class Script:
    """Answers the builders' queries in the order they are issued."""

    def __init__(self, items):
        self.items, self.position = items, 0

    def _next(self):
        item = self.items[self.position]
        self.position += 1
        return item

    async def scalar(self, statement):
        return self._next()

    async def scalars(self, statement):
        return Listing(self._next())

    async def get(self, model, key):
        return self._next()


def drive(coroutine):
    try:
        coroutine.send(None)
    except StopIteration as stop:
        return stop.value
    raise AssertionError("the scripted session never suspends")


def _uuid(label):
    return str(uuid5(NAMESPACE_URL, "svg-timeout-repurchase-incident-test/" + label))


def build_chain(module, setattr_):
    """Run every incident stage of ``module`` over one coherent scripted ledger.

    ``setattr_`` installs the test doubles (``monkeypatch.setattr``). Pinned
    approval hashes and the returned page's source digest are stored evidence
    whose preimages are not in the repository, so the digest doubles map the
    scripted stand-ins onto those pinned values.
    """
    import utils.llm_calls.author_prompt_profile as author_profile
    import utils.llm_provider as llm_provider

    registry = incident.incident_registry()
    proof = registry["stages"]["bounded_completion"]["terms"]["returned_cache_proof"]
    returned_svg = "<svg>returned page</svg>"
    request = {"strategy": "svg", "prompt": "frozen"}
    style = {"authoring_profile": dict(registry["authoring_profile"]), "style": "navy"}
    pins = [(request, registry["frozen_request_digest"]), (style, registry["frozen_style_digest"])]
    real_digest = general._digest

    def digest(value):
        return next((pinned for original, pinned in pins if value == original), None) or real_digest(value)

    def sha(data):
        return proof["candidate_digest"] if data == returned_svg.encode() else sha256_hex(data)

    setattr_(module, "_digest", digest)
    setattr_(module, "sha256_hex", sha)
    setattr_(checkpoint, "_digest", digest)
    setattr_(checkpoint, "_read_png", lambda payload: b"png" if payload.get("png_sha256") else None)
    setattr_(llm_provider, "get_llm_provider", lambda: SimpleNamespace(value=registry["author_provider"]))
    setattr_(llm_provider, "get_model", lambda: registry["author_model"])
    setattr_(author_profile, "authoring_model", lambda model: model)

    ops = {"A": "167736b7-c871-4896-85f7-4118834ce236", "B": "6739a423-d5ad-4c76-b9ac-c903482bb3f3",
           "C": "9608ec0a-01ba-4b8b-bb5e-8bdbea1e51cc", "D": "25fed2cc-0c24-41e5-8387-d26f339ef1b9",
           "E": "9f04879a-d9ee-4e1b-a2b3-c01c2a4f6166", "F": "b4536446-ceeb-42de-b98c-87c43cc560a1",
           "G": "d96f4086-3b43-4c53-9ab6-329cc577a492"}
    admissions = {key: _uuid("admission-" + key) for key in ops}
    serial = iter(range(10_000))

    def operation(key, *, error, retry_of=None, manifest=None, manifest_hash=None, request_revision=V3):
        payload = {"job_id": registry["job_id"], "recovery_revision": request_revision}
        if manifest is not None:
            payload["svg_timeout_repurchase_manifest"] = deepcopy(manifest)
            payload["svg_timeout_repurchase_manifest_hash"] = manifest_hash or real_digest(manifest)
        return Obj(id=ops[key], kind="presentation.generate", state="failed", error_code=error,
                   error_detail={"code": error}, lease_expires_at=None, presentation_id=registry["presentation_id"],
                   owner_id="owner-1", retry_of_operation_id=retry_of, request_payload=payload, version=2,
                   fencing_token=5, attempt_number=1, attempt_token="token-" + key, max_attempts=1)

    def job(key, completed):
        return Obj(id=registry["job_id"], state="failed", request_payload=request, svg_style_snapshot=style,
                   total_slides=registry["total_slides"], completed_slides=completed, lease_expires_at=None,
                   attempt_number=1, attempt_token="job-token", request_sha256="request-sha",
                   workspace_operation_id=ops[key])

    def admission(key, initial, retry=0):
        return Obj(id=admissions[key], operation_id=ops[key], state="failed", terminal_at=1, owner_id="owner-1",
                   tenant_id="tenant-1", estimated_reserved_microunits=0, actual_reserved_microunits=0,
                   initial_request_count=initial, retry_request_count=retry)

    def call(key, index, stage, outcome, state, ordinal=1):
        return Obj(id=_uuid(f"call-{next(serial)}"), admission_id=admissions[key], slide_index=index, stage=stage,
                   outcome=outcome, state=state, reconciled_at=1, provider_id=registry["author_provider"],
                   model_id=registry["author_model"], logical_call_ordinal=ordinal)

    def receipt(outcome):
        return Obj(id=_uuid(f"receipt-{next(serial)}"), request_count=1, provider=registry["author_provider"],
                   model=registry["author_model"], outcome=outcome)

    def row(index, payload, author="pending", render="pending"):
        return Obj(id=_uuid(f"row-{index}"), slide_index=index, svg_checkpoint=payload, author_status=author,
                   render_status=render)

    def attempt(svg, ordinal):
        raw = f"raw-{svg}-{ordinal}"
        return {"kind": "provider", "raw_response": raw, "receipt": {
            "validation_error": None, "attempt_ordinal": ordinal, "raw_digest": "sha256:" + sha256_hex(raw.encode()),
            "source_digest": "sha256:" + sha256_hex(svg.encode()), "requested_model": registry["author_model"]}}

    def build(builder, op, script, **kwargs):
        return drive(getattr(module, builder)(Script(script), op, presentation_revision=3, **kwargs))

    lease, budget = Obj(id="lease", state="released"), Obj(id="budget", estimated_reserved=0, actual_consumed=0)
    reservation = Obj(id="reservation", state="released")
    chain = {}

    # Interrupted batch: one cached page, one returned page, two in-flight pages.
    calls_a = [call("A", 0, "author", "succeeded", "reported"), call("A", 1, "author", "succeeded", "reported"),
               call("A", 2, "author", "cancelled", "unavailable"), call("A", 3, "author", "cancelled", "unavailable")]
    svg0, svg1 = "<svg>page 0</svg>", "<svg>page 1</svg>"
    rows_a = [row(0, {"input_digest": "in0", "svg_content": svg0, "model_attempt_count": 1,
                      "attempts": [attempt(svg0, 1)], "quality": {"passed": True}, "png_sha256": "png0"},
                  "completed", "completed"),
              row(1, {"input_digest": "in1", "svg_content": svg1, "model_attempt_count": 1,
                      "attempts": [attempt(svg1, 1)]}, "completed"),
              row(2, {"input_digest": "in2", "inflight": 1}), row(3, {"input_digest": "in3", "inflight": 1})]
    rows_a += [row(index, {"input_digest": f"in{index}"}) for index in range(4, 12)]
    admission_a = admission("A", 4)
    op_a = operation("A", error="svg_provider_attempt_uncertain", request_revision=None)
    script_a = [job("A", 0), admission_a, [lease], calls_a, [budget], [reservation], rows_a]
    chain["interrupted"] = (op_a, script_a, {"recovery_revision": V3},
                            build("_build_interrupted_batch_manifest", op_a, script_a, recovery_revision=V3))
    manifest_a = chain["interrupted"][3].manifest

    # Zero-dispatch replay after the approved retry failed before dispatching.
    previous = {item["slide_index"]: item for name in ("completed_checkpoints", "returned_checkpoints",
                                                       "unknown_checkpoints") for item in manifest_a[name]}
    rows_c = deepcopy(rows_a)
    for item in rows_c[2:4]:
        item.svg_checkpoint = {
            "input_digest": f"in{item.slide_index}", "recovery_revision": V3, "recovery_operation_id": ops["C"],
            "previous_checkpoint_row": rows_a[item.slide_index].model_dump(),
            "retained_provider_attempt_fence": {
                "consumed_attempt_count": 1, "previous_ordinal": 1, "approval_manifest_hash": real_digest(manifest_a),
                "logical_call_ids": previous[item.slide_index]["logical_call_ids"]}}
    op_c = operation("C", error="http_error_500", retry_of=ops["A"], manifest=manifest_a)
    usage_a = [receipt("succeeded"), receipt("succeeded"), receipt("cancelled"), receipt("cancelled")]
    admission_c = admission("C", 0)
    script_c = [job("C", 0), admission_c, [lease], [], [budget], [reservation], rows_c, admission_a, calls_a, usage_a]
    chain["zero_dispatch"] = (op_c, script_c, {"recovery_revision": V3},
                              build("_build_zero_dispatch_batch_replay", op_c, script_c, recovery_revision=V3))
    manifest_c = chain["zero_dispatch"][3].manifest

    # Bounded overload: the provider refused the repaired and the fresh page.
    calls_d = [call("D", 2, "repair", "failed", "unavailable"), call("D", 3, "repair", "failed", "unavailable"),
               call("D", 4, "author", "failed", "unavailable")]
    rows_d = deepcopy(rows_a)
    rows_d[1].svg_checkpoint.update(quality={"passed": True}, png_sha256="png1")
    rows_d[1].svg_checkpoint["attempts"][0]["quality"] = {"passed": True}
    rows_d[1].render_status = "completed"
    for item in rows_d[2:4]:
        replay = rows_c[item.slide_index].svg_checkpoint
        item.svg_checkpoint = {
            "input_digest": f"in{item.slide_index}", "recovery_revision": V3, "recovery_operation_id": ops["D"],
            "provider_failures": [{"ordinal": 2, "code": "public_provider_overload"}],
            "previous_checkpoint_row": replay["previous_checkpoint_row"],
            "retained_provider_attempt_fence": {**replay["retained_provider_attempt_fence"],
                                                "approval_manifest_hash": real_digest(manifest_c)}}
    rows_d[4].svg_checkpoint = {"input_digest": "in4",
                                "provider_failures": [{"ordinal": 1, "code": "public_provider_overload"}]}
    op_d = operation("D", error="svg_repair_budget_exhausted", retry_of=ops["C"], manifest=manifest_c)
    admission_d = admission("D", 1, 2)
    usage_d = usage_a + [receipt("failed"), receipt("failed"), receipt("failed")]
    script_d = [job("D", 2), admission_d, [lease], [budget], [reservation], calls_d, admission_a, calls_a, usage_d,
                rows_d]
    chain["overload"] = (op_d, script_d, {}, build("_build_bounded_overload_manifest", op_d, script_d))
    manifest_d = chain["overload"][3].manifest

    # Bounded completion: two unknown remote outcomes and one returned repair.
    calls_e = [call("E", 2, "repair", "cancelled", "unavailable"), call("E", 3, "repair", "cancelled", "unavailable"),
               call("E", 4, "repair", "succeeded", "reported")]
    targets = {item["slide_index"]: item for item in manifest_d["unknown_checkpoints"]}
    rows_e = deepcopy(rows_d)
    for item in rows_e[2:4]:
        item.svg_checkpoint = {
            "input_digest": f"in{item.slide_index}", "inflight": 3, "recovery_revision": V3,
            "recovery_operation_id": ops["E"], "previous_checkpoint_row": rows_d[item.slide_index].model_dump(),
            "retained_provider_attempt_fence": {
                "consumed_attempt_count": 2, "previous_ordinal": 2, "approval_manifest_hash": real_digest(manifest_d),
                "logical_call_ids": targets[item.slide_index]["logical_call_ids"]}}
    rows_e[4].svg_checkpoint = {"input_digest": proof["input_digest"], "svg_content": returned_svg,
                                "attempts": [attempt(returned_svg, 2)]}
    rows_e[4].author_status = "completed"
    op_e = operation("E", error="svg_provider_attempt_uncertain", retry_of=ops["D"], manifest=manifest_d)
    admission_e = admission("E", 3)
    history_e = [admission_a, admission("B", 0), admission_c, admission_d, admission_e]
    calls_history_e = calls_a + calls_d + calls_e
    usage_e = usage_d + [receipt("succeeded")]
    script_e = [job("E", 2), admission_e, [Obj(id=ops[key]) for key in "ABCDE"], history_e, calls_history_e,
                [lease], [budget], [reservation], usage_e, rows_e]
    chain["completion"] = (op_e, script_e, {}, build("_build_bounded_completion_manifest", op_e, script_e))
    manifest_e = chain["completion"][3].manifest
    continuation = registry["stages"]["bounded_continuation"]
    pins.append((manifest_e, continuation["approval_source_hash"]))

    # Bounded continuation: one page left with an unknown remote outcome.
    calls_f = [call("F", 2, "repair", "succeeded", "reported"), call("F", 3, "repair", "succeeded", "reported")]
    calls_f += [call("F", index, "author", "succeeded", "reported") for index in (5, 6, 7, 8)]
    calls_f += [call("F", 8, "repair", "succeeded", "reported", 2), call("F", 9, "author", "failed", "unavailable")]
    rows_f = deepcopy(rows_e)
    for item in rows_f[:9]:
        if item.slide_index not in (0, 1, 4):
            svg = f"<svg>page {item.slide_index} done</svg>"
            item.svg_checkpoint = {"input_digest": f"in{item.slide_index}", "svg_content": svg,
                                   "quality": {"passed": True}, "png_sha256": f"png{item.slide_index}",
                                   "attempts": [attempt(svg, 1)]}
        item.author_status = item.render_status = "completed"
    rows_f[4].svg_checkpoint.update(quality={"passed": True}, png_sha256="png4")
    rows_f[9].svg_checkpoint = {"input_digest": "in9", "inflight": 1, "provider_diagnostics": [{"code": "timeout"}]}
    rows_f[9].author_status = "running"
    op_f = operation("F", error=continuation["source_error_code"], retry_of=ops["E"], manifest=manifest_e,
                     manifest_hash=continuation["approval_source_hash"])
    admission_f = admission("F", 8)
    operations_f = [Obj(id=ops[key]) for key in "ABCDEF"]
    calls_history_f = calls_history_e + calls_f
    usage_f = usage_e + [receipt("succeeded") for _ in range(8)]
    script_f = [job("F", 9), admission_f, operations_f, history_e + [admission_f], calls_history_f,
                [lease], [budget], [reservation], usage_f, rows_f]
    chain["continuation"] = (op_f, script_f, {}, build("_build_bounded_continuation_manifest", op_f, script_f))
    manifest_f = chain["continuation"][3].manifest
    quality = registry["stages"]["bounded_quality_continuation"]
    pins.append((manifest_f, quality["approval_source_hash"]))

    # Bounded quality continuation: the last unknown page returned and repaired.
    rows_g = deepcopy(rows_f)
    svg9 = "<svg>page 9 done</svg>"
    rows_g[9].svg_checkpoint = {"input_digest": "in9", "svg_content": svg9, "png_sha256": "png9",
                                "quality": {"passed": True, "source_digest": "sha256:" + sha256_hex(svg9.encode())},
                                "attempts": [attempt(svg9, 1), attempt(svg9, 2)]}
    rows_g[9].author_status = rows_g[9].render_status = "completed"
    op_g = operation("G", error=quality["source_error_code"], retry_of=ops["F"], manifest=manifest_f,
                     manifest_hash=quality["approval_source_hash"])
    admission_g = admission("G", 1)
    script_g = [job("G", 10), admission_g, operations_f + [Obj(id=ops["G"])], history_e + [admission_f, admission_g],
                calls_history_f + [call("G", 9, "repair", "succeeded", "reported", 2)], [lease], [budget],
                [reservation], usage_f + [receipt("succeeded")], rows_g]
    chain["quality"] = (op_g, script_g, {"quality_continuation": True},
                        build("_build_bounded_continuation_manifest", op_g, script_g, quality_continuation=True))
    return chain, digest


def test_incident_chain_builds_the_pinned_manifests(monkeypatch):
    chain, _ = build_chain(incident, monkeypatch.setattr)
    digests = {name: general._digest(entry[3].manifest) for name, entry in chain.items()}
    assert digests == GOLDEN_CHAIN_DIGESTS
    for name, (op, script, _kwargs, expected) in chain.items():
        routed = drive(general.build_timeout_repurchase_manifest(
            Script(script), op, presentation_revision=3, recovery_revision=V3))
        assert general._digest(routed.manifest) == GOLDEN_CHAIN_DIGESTS[name]
        assert [row.id for row in routed.rows] == [row.id for row in expected.rows]


def test_each_bounded_stage_enforces_every_registered_term(monkeypatch):
    chain, _ = build_chain(incident, monkeypatch.setattr)
    registry = incident.incident_registry()
    stages = {"overload": ("bounded_overload", incident._bounded_overload_shape),
              "completion": ("bounded_completion", incident.bounded_completion_shape),
              "continuation": ("bounded_continuation", incident.bounded_completion_shape),
              "quality": ("bounded_quality_continuation", incident.bounded_completion_shape)}
    for name, (stage, shape) in stages.items():
        manifest = chain[name][3].manifest
        assert shape(manifest) is True
        terms = {**registry["stages"][stage]["terms"], **registry["stages"][stage]["inherited_terms"]}
        for key, value in terms.items():
            changed = {**manifest, key: None if not isinstance(value, int) else value + 1}
            assert shape(changed) is False, (name, key)
        for key in ("operation_id", "presentation_id", "job_id", "new_author_model", "new_author_provider"):
            assert shape({**manifest, key: "other"}) is False, (name, key)


def test_incident_attempt_allowances_stay_bound_to_the_registered_rows(monkeypatch):
    chain, digest = build_chain(incident, monkeypatch.setattr)
    completion = chain["completion"][3].manifest
    job = SimpleNamespace(id=completion["job_id"], workspace_operation_id="child-operation")
    returned = completion["returned_checkpoints"][0]
    cached = {"recovery_revision": V3, "timeout_repurchase_approval_manifest": completion,
              "completion_cache_only": True, "recovery_operation_id": "child-operation",
              "input_digest": returned["input_digest"], "svg_content": "<svg>returned page</svg>",
              "attempts": chain["completion"][1][-1][4].svg_checkpoint["attempts"],
              "retained_provider_attempt_fence": {"approval_manifest_hash": digest(completion), "slide_index": 4,
                                                  "consumed_attempt_count": 1, "previous_ordinal": 1}}
    assert checkpoint._provider_attempt_limit(cached, index=4, job=job) == 2
    with pytest.raises(ValueError, match="svg_timeout_repurchase_attempt_fence_invalid"):
        checkpoint._provider_attempt_limit(cached, index=5, job=job)
    for index in (2, 3):
        target = next(item for item in completion["unknown_checkpoints"] if item["slide_index"] == index)
        payload = {"recovery_revision": V3, "timeout_repurchase_approval_manifest": completion,
                   "recovery_operation_id": "child-operation", "input_digest": target["input_digest"], "attempts": [],
                   "retained_provider_attempt_fence": {"approval_manifest_hash": digest(completion),
                                                       "slide_index": index, "max_additional_author_calls": 1,
                                                       "consumed_attempt_count": 3, "previous_ordinal": 3}}
        assert checkpoint._provider_attempt_limit(payload, index=index, job=job) == 4


def test_registry_is_digest_pinned_and_fails_closed(monkeypatch, tmp_path):
    data = json.loads(incident.REGISTRY_PATH.read_text(encoding="utf-8"))
    assert canonical_json_sha256(data) == incident.REGISTRY_DIGEST
    data["stages"]["bounded_completion"]["terms"]["remaining_author_call_limit"] += 1
    tampered = tmp_path / "registry.json"
    tampered.write_text(json.dumps(data), encoding="utf-8")
    monkeypatch.setattr(incident, "REGISTRY_PATH", tampered)
    with pytest.raises(general.TimeoutRepurchaseRejected, match="svg_timeout_repurchase_incident_registry_invalid"):
        incident.incident_registry()
    monkeypatch.setattr(incident, "REGISTRY_PATH", tmp_path / "missing.json")
    with pytest.raises(general.TimeoutRepurchaseRejected, match="svg_timeout_repurchase_incident_registry_invalid"):
        incident.incident_registry()


def test_ordinary_paths_never_read_the_incident_registry(monkeypatch, tmp_path):
    monkeypatch.setattr(incident, "REGISTRY_PATH", tmp_path / "missing.json")
    assert checkpoint._provider_attempt_limit({"attempts": []}, index=0, job=None) == 2
    for manifest in ({}, {"append_only_recovery": True}, {"schema": "svg-partial-recovery/v2"},
                     {"bounded_completion_recovery": False}, None):
        assert general._bounded_completion_shape(manifest) is False
    recovery = general.TimeoutRepurchaseRecovery([], {"recovery_revision": "svg-provider-timeout-repurchase/v1",
                                                      "unknown_checkpoints": []})
    general.reset_timeout_repurchase_checkpoints(None, recovery, operation_id="operation", now=None)


def test_general_modules_hold_no_incident_literals():
    registry = incident.incident_registry()
    text = json.dumps(registry, ensure_ascii=False)
    literals = set(re.findall(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}|[0-9a-f]{64}", text))
    literals |= {registry["author_model"], "외부 연계·저장소"}
    for name in ("svg_timeout_repurchase.py", "svg_presentation_checkpoint.py"):
        source = (SERVICES / name).read_text(encoding="utf-8")
        assert not {literal for literal in literals if literal in source}, name
        assert "art-direction-v1" not in source, name
