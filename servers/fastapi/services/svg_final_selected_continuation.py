"""Bounded consumption of immutable, previously selected SVG review evidence."""
from __future__ import annotations

from copy import deepcopy
from hashlib import sha256
from typing import Any, Mapping, Sequence

from services.generation_core.quality.advisory_record import content_digest

SCHEMA = "svg-final-selected-continuation/v1"
CONTROL_SCHEMA = "svg-final-selected-review-control/v1"
NAMESPACE = "final-selected/v1"
INITIAL_NAMESPACE = "initial-continuation/v1"


class FinalSelectedEvidenceError(ValueError):
    def __init__(self, code="svg_final_selected_evidence_unproven"):
        if code not in {"svg_final_selected_evidence_unproven", "svg_final_selected_context_unproven",
                        "svg_final_selected_runtime_unproven"}:
            code = "svg_final_selected_evidence_unproven"
        self.code = code
        super().__init__(code)


def _require(condition: bool) -> None:
    if not condition:
        raise FinalSelectedEvidenceError()


def _sha(value: Any) -> str:
    _require(isinstance(value, str) and bool(value.strip()))
    result = value.removeprefix("sha256:")
    _require(len(result) == 64 and all(c in "0123456789abcdef" for c in result))
    return result


def _svg_sha(payload: Mapping[str, Any]) -> str:
    svg = payload.get("svg_content")
    _require(isinstance(svg, str) and bool(svg.strip()))
    return sha256(svg.encode("utf-8")).hexdigest()


def _gate_value(values: Any, index: int) -> str:
    if isinstance(values, Mapping):
        value = values.get(str(index), values.get(index))
    else:
        _require(isinstance(values, list) and index < len(values))
        value = values[index]
    return _sha(value)


def final_review_control(page_count: int, *, physical_offset: int) -> dict[str, Any]:
    _require(type(page_count) is int and page_count > 0)
    _require(type(physical_offset) is int and physical_offset >= 0)
    groups = (page_count + 4) // 5
    value = dict(schema=CONTROL_SCHEMA, namespace=NAMESPACE,
                 page_count=page_count, local_group_size=5,
                 local_group_limit=groups, global_format_retry_limit=1,
                 review_call_limit=groups + 2, physical_offset=physical_offset,
                 run_limit=1)
    return value | {"digest": content_digest(value)}


def verify_review_control(value: Any, page_count: int, physical_offset: int) -> dict:
    _require(isinstance(value, Mapping))
    expected = final_review_control(page_count, physical_offset=physical_offset)
    _require(dict(value) == expected)
    return deepcopy(expected)


def selected_source_vector(payloads: Sequence[Mapping[str, Any]]) -> list[dict]:
    from services.svg_cached_author_render_recovery import author_witness
    result = []
    for index, payload in enumerate(payloads):
        quality = payload.get("quality")
        _require(isinstance(quality, Mapping) and quality.get("measurement_complete") is True)
        svg = _svg_sha(payload)
        png = _sha(payload.get("png_sha256"))
        _require(_sha(payload.get("source_digest")) == svg)
        _require(_sha(quality.get("source_digest")) == svg)
        _require(_sha(quality.get("png_digest")) == png)
        _require(isinstance(payload.get("png_path"), str) and bool(payload["png_path"].strip()))
        result.append(dict(index=index, svg_digest=svg, png_digest=png,
                           input_digest=payload.get("input_digest"),
                           author_receipt_digest=author_witness(payload),
                           quality_digest=content_digest({k: v for k, v in quality.items()
                                                          if k != "compositionObservation"}),
                           native_digest=content_digest(payload.get("nativeDeclarations")),
                           asset_digest=content_digest(payload.get("svgAssetManifest")),
                           preservation_digest=content_digest(payload.get("source_preservation"))))
    return result


def build_selected_proof(*, operation_id: str, job_id: str, owner_id: str,
                         tenant_id: str, archive_digest: str,
                         parent_manifest_hash: str, payloads: Sequence[Mapping[str, Any]],
                         execution: Mapping[str, Any], source_snapshot: Mapping[str, Any]) -> dict | None:
    selections = [{"index": i, "status": p.get("quality_recovery", {}).get("status"),
                   "selection": deepcopy(p.get("quality_recovery", {}).get("selection"))}
                  for i, p in enumerate(payloads) if p.get("quality_recovery", {}).get("selection")]
    if not any(s["status"] == "selected" for s in selections) or not any(s["status"] == "retained" for s in selections):
        return None
    from services.generation_core.drivers.svg.quality import _gate_intact
    rounds = execution.get("rounds")
    _require(isinstance(rounds, list) and len(rounds) == 2)
    _require(all(r.get("status") == "completed" for r in rounds))
    gates = [_gate_intact(r.get("advisory")) for r in rounds]
    _require(all(isinstance(g, Mapping) for g in gates))
    _require(gates[0].get("binding") == gates[1].get("binding"))
    for key in ("snapshotDigest", "runtimeBinding", "policy", "styleDigest", "exampleDigests"):
        _require(key in gates[0] and key in gates[1] and gates[0][key] == gates[1][key])
    _require(gates[0]["snapshotDigest"] == content_digest(source_snapshot))
    _require(execution.get("binding") is not None)
    execution_binding = _sha(execution["binding"])
    _require(all(r.get("binding", execution["binding"]) == execution["binding"] for r in rounds))
    binding = gates[0]["binding"]
    _require(str(binding.get("operationId")) == operation_id)
    _require(str(binding.get("jobId")) == job_id)
    _require(str(binding.get("ownerId")) == owner_id)
    _require(binding.get("tenantId") == tenant_id)
    vector = selected_source_vector(payloads)
    selection_by_index = {s["index"]: s for s in selections}
    for item in vector:
        selection = selection_by_index.get(item["index"])
        source_round = 0
        if selection is not None:
            _require(isinstance(selection["selection"], Mapping))
            if selection["status"] == "selected":
                _require(selection["selection"].get("selected") == "candidate")
                source_round = 1
            else:
                _require(selection["status"] == "retained")
                _require(selection["selection"].get("selected") == "prior")
        _require(item["svg_digest"] == _gate_value(gates[source_round]["pageDigests"], item["index"]))
        _require(item["png_digest"] == _gate_value(gates[source_round]["pngDigests"], item["index"]))
    affected = []
    for start in range(0, len(vector), 5):
        indices = list(range(start, min(start + 5, len(vector))))
        if any(v["svg_digest"] != _gate_value(gates[0]["pageDigests"], v["index"])
               or v["png_digest"] != _gate_value(gates[0]["pngDigests"], v["index"])
               for v in vector[start:start + 5]):
            affected.extend(indices)
    value = dict(schema=SCHEMA, namespace=NAMESPACE,
                 source_operation_id=operation_id, source_job_id=job_id,
                 owner_id=owner_id, tenant_id=tenant_id,
                 archive_digest=archive_digest, parent_manifest_hash=parent_manifest_hash,
                 selection_digest=content_digest(selections), selections=selections,
                 execution_binding=execution_binding,
                 source_snapshot_digest=gates[0]["snapshotDigest"],
                 source_runtime_binding=deepcopy(gates[0]["runtimeBinding"]),
                 source_policy=gates[0]["policy"],
                 source_pages=vector, fresh_local_indices=affected,
                 original_gate_digests=[g["digest"] for g in gates],
                 original_round_digests=[content_digest(r) for r in rounds])
    return value | {"digest": content_digest(value)}


def verify_selected_proof(proof: Any, *, operation_id: str, job_id: str,
                          owner_id: str, tenant_id: str, archive_digest: str,
                          parent_manifest_hash: str,
                          payloads: Sequence[Mapping[str, Any]],
                          execution: Mapping[str, Any], source_snapshot: Mapping[str, Any]) -> dict:
    expected = build_selected_proof(operation_id=operation_id, job_id=job_id,
                                   owner_id=owner_id, tenant_id=tenant_id,
                                   archive_digest=archive_digest,
                                   parent_manifest_hash=parent_manifest_hash,
                                   payloads=payloads, execution=execution,
                                   source_snapshot=source_snapshot)
    _require(expected is not None and isinstance(proof, Mapping) and dict(proof) == expected)
    return deepcopy(expected)


def render_evidence_marker(proof: Mapping[str, Any], index: int, *, consumer_operation_id: str,
                           consumer_manifest_hash: str) -> dict:
    _require(proof.get("schema") == SCHEMA and proof.get("namespace") == NAMESPACE)
    _require(type(index) is int and 0 <= index < len(proof["source_pages"]))
    page = proof["source_pages"][index]
    _require(page["index"] == index)
    return dict(schema="svg-final-selected-render-consumer/v1", namespace=NAMESPACE,
                producer_operation_id=proof["source_operation_id"],
                producer_job_id=proof["source_job_id"], owner_id=proof["owner_id"],
                tenant_id=proof["tenant_id"], continuation_digest=proof["digest"],
                archive_digest=proof["archive_digest"], source_row_index=index,
                consumer_operation_id=consumer_operation_id,
                consumer_manifest_hash=consumer_manifest_hash,
                source=deepcopy(page), physical_capture_reused=True)


def verify_cached_render(payload: Mapping[str, Any], manifest: Mapping[str, Any], index: int) -> bytes:
    from services.svg_cached_author_render_recovery import author_witness, digest
    from services.svg_presentation_checkpoint import _read_png
    proof = manifest.get("final_selected_continuation")
    marker = payload.get("partial_recovery")
    evidence = payload.get("final_selected_render_evidence")
    _require(isinstance(proof, Mapping) and isinstance(marker, Mapping))
    _require(isinstance(evidence, Mapping))
    _require(evidence == render_evidence_marker(proof, index,
                   consumer_operation_id=str(marker.get("operation_id")),
                   consumer_manifest_hash=digest(manifest)))
    page = manifest["pages"][index]
    source = proof["source_pages"][index]
    _require(source["author_receipt_digest"] == author_witness(payload) == page["author_receipt_digest"])
    _require(source["svg_digest"] == _svg_sha(payload) == _sha(page["svg_sha256"]))
    _require(payload.get("input_digest") == source["input_digest"])
    quality = payload.get("quality")
    _require(isinstance(quality, Mapping) and quality.get("measurement_complete") is True)
    _require(content_digest({k: v for k, v in quality.items()
                            if k != "compositionObservation"}) == source["quality_digest"])
    _require(_sha(payload.get("png_sha256")) == source["png_digest"])
    _require(_sha(quality.get("source_digest")) == source["svg_digest"])
    _require(_sha(quality.get("png_digest")) == source["png_digest"])
    _require(_sha(payload.get("source_digest")) == source["svg_digest"])
    _require(content_digest(payload.get("nativeDeclarations")) == source["native_digest"])
    _require(content_digest(payload.get("svgAssetManifest")) == source["asset_digest"])
    _require(content_digest(payload.get("source_preservation")) == source["preservation_digest"])
    png = _read_png(payload)
    _require(sha256(png).hexdigest() == source["png_digest"])
    return png


def archived_producer_payloads(archive: Mapping[str, Any], proof: Mapping[str, Any]) -> tuple[dict, list[dict]]:
    from services.svg_cached_author_render_recovery import digest
    _require(digest(archive) == proof["archive_digest"])
    job = archive.get("job")
    rows = archive.get("checkpoints")
    _require(isinstance(job, Mapping) and isinstance(rows, list))
    _require(str(job.get("id")) == proof["source_job_id"])
    _require(str(job.get("workspace_operation_id")) == proof["source_operation_id"])
    _require(len(rows) == len(proof["source_pages"]))
    by_index = {r.get("slide_index"): r for r in rows}
    _require(set(by_index) == set(range(len(rows))))
    payloads = [deepcopy(by_index[i]["svg_checkpoint"]) for i in range(len(rows))]
    _require(selected_source_vector(payloads) == proof["source_pages"])
    verify_selected_proof(proof, operation_id=proof["source_operation_id"],
                          job_id=proof["source_job_id"], owner_id=proof["owner_id"],
                          tenant_id=proof["tenant_id"], archive_digest=proof["archive_digest"],
                          parent_manifest_hash=proof["parent_manifest_hash"], payloads=payloads,
                          execution=payloads[0]["quality_recovery_execution"],
                          source_snapshot=job["svg_style_snapshot"])
    return deepcopy(dict(job)), payloads


def reusable_local_groups(context: Mapping[str, Any], pages: Sequence, renders: Sequence[bytes],
                          evidence: Mapping[str, Any]) -> tuple[dict[int, dict], list[dict]]:
    from services.generation_core.drivers.svg.quality import (
        _gate_intact, _group_review_input_digest, _local_group_context,
    )
    reused, consumers = {}, []
    sources = [evidence, *evidence.get("additional_producers", [])]
    for source in sources:
        _consume_local_producer(context, pages, renders, source, reused, consumers,
                                evidence.get("consumer_namespace", NAMESPACE))
    proof = evidence["proof"]
    if not reused and len(proof["fresh_local_indices"]) < len(proof["source_pages"]):
        raise FinalSelectedEvidenceError("svg_final_selected_context_unproven")
    return reused, consumers


def _consume_local_producer(context, pages, renders, evidence, reused, consumers, namespace):
    from services.generation_core.drivers.svg.quality import (
        _gate_intact, _group_review_input_digest, _local_group_context,
    )
    _require(namespace in {NAMESPACE, INITIAL_NAMESPACE})
    proof = evidence["proof"]
    original = evidence["execution"]["rounds"][0]
    gate = _gate_intact(original["advisory"])
    _require(gate is not None)
    _require(context["binding"].get("ownerId") == proof["owner_id"])
    _require(context["binding"].get("tenantId") == proof["tenant_id"])
    if (evidence.get("consumer_binding") is not None
            or context["binding"].get("presentationId") != gate["binding"].get("presentationId")):
        _require(evidence.get("consumer_binding") == context["binding"])
        _require(gate["binding"].get("presentationId") == evidence.get("producer_presentation_id"))
        _require(gate["binding"].get("jobId") == proof["source_job_id"])
        _require(gate["binding"].get("operationId") == proof["source_operation_id"])
    if evidence.get("consumer_runtime_equivalent") is not True:
        raise FinalSelectedEvidenceError("svg_final_selected_runtime_unproven")
    groups = original.get("groups")
    _require(isinstance(groups, Mapping))
    projected_context = deepcopy(dict(context))
    projected_context["binding"] = deepcopy(gate["binding"])
    projected_context["runtimeBinding"] = deepcopy(gate["runtimeBinding"])
    projected_context["priorCandidates"] = {}
    if "reviewPrinciplesVersion" in gate:
        projected_context["reviewPrinciplesVersion"] = gate["reviewPrinciplesVersion"]
    else:
        projected_context.pop("reviewPrinciplesVersion", None)
    for ordinal, start in enumerate(range(0, len(pages), 5)):
        if ordinal in reused:
            continue
        indices = list(range(start, min(start + 5, len(pages))))
        group_id = f"phase-0/group-{ordinal}"
        marker = groups.get(group_id)
        if marker is None:
            marker = next((m for m in groups.values() if m.get("group_id") == group_id), None)
        if not isinstance(marker, Mapping) or marker.get("status") != "completed":
            continue
        if marker.get("review_scope") != "local" or marker.get("indices") != indices:
            continue
        if any(_svg_sha(pages[i][0]) != _gate_value(gate["pageDigests"], i)
               or sha256(renders[i]).hexdigest() != _gate_value(gate["pngDigests"], i)
               for i in indices):
            continue
        group_context = _local_group_context(projected_context, indices, False)
        expected_input = _group_review_input_digest(group_context, [renders[i] for i in indices],
                                                    0, ordinal, indices)
        if marker.get("input_digest") != expected_input:
            continue
        record = marker.get("advisory")
        if not isinstance(record, Mapping) or record.get("vision", {}).get("skipped"):
            continue
        if (record.get("inputDigest") != expected_input
            or record.get("globalPageIndices") != indices
            or record.get("reviewScope") != "local"
            or record.get("groupId") != group_id
            or record.get("digest") != content_digest({k: v for k, v in record.items() if k != "digest"})):
            continue
        # The original verdict is copied unchanged, including failed axes.
        reused[ordinal] = deepcopy(dict(record))
        consumers.append(dict(schema="svg-local-review-consumer/v1", namespace=namespace,
                              producer_operation_id=proof["source_operation_id"],
                              producer_job_id=proof["source_job_id"],
                              producer_group_id=group_id,
                              producer_record_digest=content_digest(marker),
                              producer_input_digest=expected_input,
                              consumer_binding=deepcopy(context["binding"]),
                              indices=indices, provider_response_reused=False,
                              original_verdict_preserved=True))
