"""Consume sealed source-local reviews without changing their producer records."""
from __future__ import annotations

from copy import deepcopy
from hashlib import sha256
from typing import Mapping

from services.generation_core.quality.advisory_record import content_digest
from services.svg_final_selected_continuation import (
    INITIAL_NAMESPACE, NAMESPACE, FinalSelectedEvidenceError, _require, _sha, _svg_sha,
)

SOURCE_LOCAL_SCHEMA = "svg-source-local-review-continuation/v1"

def _source_local_vector(payloads):
    from services.svg_cached_author_render_recovery import author_witness
    result = []
    for index, payload in enumerate(payloads):
        quality = payload.get("quality")
        _require(isinstance(quality, Mapping) and all(quality.get(key) is True
                 for key in ("passed", "geometry_passed", "measurement_complete", "verified")))
        svg, png = _svg_sha(payload), _sha(payload.get("png_sha256"))
        _require(_sha(payload.get("source_digest")) == svg
                 and _sha(quality.get("source_digest")) == svg and _sha(quality.get("png_digest")) == png)
        _require(isinstance(payload.get("input_digest"), str) and bool(payload["input_digest"]))
        result.append(dict(index=index, svg_digest=svg, png_digest=png, input_digest=payload["input_digest"],
            author_receipt_digest=author_witness(payload), native_digest=content_digest(payload.get("nativeDeclarations")),
            asset_digest=content_digest(payload.get("svgAssetManifest")),
            preservation_digest=content_digest(payload.get("source_preservation"))))
    return result


def _source_local_records(execution, payloads, *, sealed_vector=None):
    from services.generation_core.quality.vision_advisory_prompt import (
        DESIGN_LOCAL_AXES, DESIGN_PRINCIPLES_VERSION, DESIGN_REVIEW_PROMPT_VERSION,
    )
    from services.svg_presentation_checkpoint import _digest
    _require(execution.get("schema") == "svg-quality-recovery-execution/v1")
    _require(execution.get("reviewPrinciplesVersion") == DESIGN_PRINCIPLES_VERSION)
    _sha(execution.get("binding"))
    rounds = execution.get("rounds")
    _require(isinstance(rounds, list) and len(rounds) == 1)
    original = rounds[0]
    _require(isinstance(original, Mapping))
    _require(original.get("status") in {"review_unknown", "completed"})
    _require(original.get("binding", execution["binding"]) == execution["binding"])
    if sealed_vector is None:
        _require(original.get("page_digests") == [
            _digest({"svg": page.get("svg_content"), "png": page.get("png_sha256")}) for page in payloads])
    else:
        # The original proof/execution seal was checked against the actual leaf
        # archive at admission. Never compare its old review to repaired bytes.
        _require(len(sealed_vector) == len(payloads) == len(original.get("page_digests", [])))
        for value in original["page_digests"]:
            _sha(value)
    groups = original.get("groups")
    _require(isinstance(groups, Mapping))
    count = len(payloads)
    group_count = (count + 4) // 5
    records = []
    for ordinal, start in enumerate(range(0, count, 5)):
        indices = list(range(start, min(start + 5, count)))
        marker = groups.get(str(ordinal))
        group_id = f"phase-0/group-{ordinal}"
        _require(isinstance(marker, Mapping) and marker.get("status") == "completed")
        _require(marker.get("group_id") == group_id and marker.get("review_scope") == "local"
                 and marker.get("indices") == indices)
        record = marker.get("advisory")
        _require(isinstance(record, Mapping) and record.get("groupId") == group_id
                 and record.get("globalPageIndices") == indices and record.get("reviewScope") == "local")
        _require(record.get("inputDigest") == marker.get("input_digest"))
        _sha(record.get("inputDigest"))
        _require(record.get("digest") == content_digest({k: v for k, v in record.items() if k != "digest"}))
        vision = record.get("vision")
        _require(isinstance(vision, Mapping) and not vision.get("skipped")
                 and vision.get("promptVersion") == DESIGN_REVIEW_PROMPT_VERSION)
        _require(vision.get("reviewedPageIndices") == list(range(len(indices)))
                 and vision.get("reviewedPngDigests") == [
                     _sha(sealed_vector[i]["png_digest"] if sealed_vector is not None else payloads[i].get("png_sha256"))
                     for i in indices])
        reviewed = (vision.get("designReview") or {}).get("pages")
        _require(isinstance(reviewed, list) and len(reviewed) == len(indices)
                 and all(isinstance(page, Mapping) for page in reviewed)
                 and {page.get("index") for page in reviewed} == set(range(len(indices))))
        for page in reviewed:
            _require(page.get("evaluated") is True and set(page.get("axes", {})) == set(DESIGN_LOCAL_AXES))
            for axis in page["axes"].values():
                _require(isinstance(axis, Mapping) and isinstance(axis.get("visibleEvidence"), str)
                         and bool(axis["visibleEvidence"].strip()))
                _require((axis.get("verdict"), axis.get("severity"), axis.get("repairScope")) in {
                    ("passed", "none", "none"), ("advisory", "minor", "none"),
                    ("failed", "major", "local_repair"), ("failed", "critical", "local_repair"),
                    ("failed", "major", "design_rewrite"), ("failed", "critical", "design_rewrite")})
            _require(isinstance(page.get("issues"), list) and isinstance(page.get("designTargets"), list))
            _require(all(isinstance(target, Mapping) and target.get("axis") in DESIGN_LOCAL_AXES
                         for target in page["designTargets"]))
        records.append(marker)
    unknown = groups.get(str(group_count))
    _require(set(groups) == {str(index) for index in range(group_count + 1)})
    if original.get("status") == "completed":
        from services.generation_core.drivers.svg.quality import _gate_intact
        vector = sealed_vector if sealed_vector is not None else _source_local_vector(payloads)
        gate = _gate_intact(original.get("advisory"))
        initial = execution.get("initial_continuation_review") or {}
        prior = initial.get("producer_proof") or {}
        _require(initial.get("namespace") == INITIAL_NAMESPACE
                 and initial.get("initial_continuation") is True
                 and initial.get("status") == "completed"
                 and initial.get("advisory") == original.get("advisory")
                 and prior.get("schema") == "svg-final-selected-continuation/v1"
                 and prior.get("local_review_mode") == "fresh-all"
                 and prior.get("digest") == content_digest({k: v for k, v in prior.items() if k != "digest"})
                 and initial.get("source_proof_digest") == prior.get("digest")
                 and original.get("namespace") == INITIAL_NAMESPACE
                 and original.get("physical_purpose") == "initial-continuation")
        _require(gate is not None and gate.get("status") == "unreviewed"
                 and gate.get("runtimeBinding") == execution["binding"]
                 and gate.get("pageDigests") == [item["svg_digest"] for item in vector]
                 and gate.get("pngDigests") == [item["png_digest"] for item in vector]
                 and len(gate.get("pages", [])) == count
                 and all(page.get("mandatoryEvidenceValid") is True for page in gate["pages"]))
        for ordinal, marker in enumerate(records):
            _checked_current_local_group(marker, ordinal, vector)
        _require(isinstance(unknown, Mapping) and unknown.get("status") == "completed"
                 and unknown.get("group_id") == f"phase-0/group-{group_count}"
                 and unknown.get("indices") == list(range(count))
                 and unknown.get("review_scope") == "global"
                 and unknown.get("namespace") == INITIAL_NAMESPACE
                 and unknown.get("physical_purpose") == "initial-continuation"
                 and unknown.get("physical_ordinal") == group_count)
        global_record = unknown.get("advisory") or {}
        _require(global_record.get("groupId") == unknown["group_id"]
                 and global_record.get("globalPageIndices") == list(range(count))
                 and global_record.get("reviewScope") == "global"
                 and global_record.get("inputDigest") == unknown.get("input_digest")
                 and global_record.get("digest") == content_digest({k: v for k, v in global_record.items() if k != "digest"})
                 and (global_record.get("vision") or {}).get("skipped") == "design_review_schema_or_coverage_invalid")
        _sha(unknown.get("input_digest"))
        vision = original["advisory"]["vision"]
        _require(vision.get("reviewGroups") == [marker["advisory"] for marker in records] + [global_record]
                 and not vision.get("localEvidenceConsumers"))
    else:
        _require(isinstance(unknown, Mapping) and unknown.get("status") == "review_unknown"
                 and isinstance(unknown.get("failureDiagnostics"), Mapping))
    return records, unknown


def _inherited_local_required(execution, source_job):
    request = source_job.get("request_payload") or {}
    manifest = request.get("svg_partial_recovery_manifest") or {}
    initial = execution.get("initial_continuation_review") if isinstance(execution, Mapping) else None
    return (manifest.get("source_local_continuation") is not None
            or isinstance(initial, Mapping) and initial.get("producer_proof", {}).get("schema") == SOURCE_LOCAL_SCHEMA)


async def prepare_source_local_leaf(session, *, source_operation=None, verification=None, **kwargs):
    """Resolve at most one immutable leaf archive, never a recursive history."""
    operation_request = getattr(source_operation, "request_payload", None) or {}
    prior = operation_request.get("svg_partial_recovery_manifest") or {}
    if (not _inherited_local_required(kwargs["execution"], kwargs["source_job"])
            and prior.get("source_local_continuation") is None):
        return None
    from services.svg_partial_recovery import (
        ARCHIVE_KEY, _hydrate_checkpoint_archive, _frozen_payload, frozen_episode_payload,
    )
    from services.svg_cached_author_render_recovery import digest
    request = kwargs["source_job"].get("request_payload") or {}
    manifest, archive = operation_request.get("svg_partial_recovery_manifest"), operation_request.get(ARCHIVE_KEY)
    _require(isinstance(manifest, Mapping) and isinstance(archive, Mapping)
             and digest(manifest) == kwargs["parent_manifest_hash"]
             and operation_request.get("svg_partial_recovery_manifest_hash") == kwargs["parent_manifest_hash"]
             and manifest.get("owner_id") == kwargs["owner_id"] and manifest.get("tenant_id") == kwargs["tenant_id"])
    leaf = manifest.get("source_local_continuation")
    _require(isinstance(leaf, Mapping)
             and leaf.get("schema") == SOURCE_LOCAL_SCHEMA
             and leaf.get("archive_digest") == digest(archive) == manifest.get("archive_digest")
             and str(source_operation.id) == kwargs["operation_id"]
             and str(source_operation.presentation_id) == str(kwargs["source_job"]["presentation_id"])
             and str(source_operation.retry_of_operation_id) == leaf.get("source_operation_id")
             and all(leaf.get(key) == manifest.get(target) for key, target in (
                 ("source_operation_id", "operation_id"), ("source_job_id", "job_id"),
                 ("source_presentation_id", "presentation_id"), ("owner_id", "owner_id"), ("tenant_id", "tenant_id"))))
    working = await _hydrate_checkpoint_archive(session, manifest, archive, verification=verification)
    leaf_job, leaf_pages = archived_source_local_payloads(archive, working, leaf, manifest_pages=manifest.get("pages"))
    _require(content_digest(frozen_episode_payload(request, manifest)) == content_digest(_frozen_payload(leaf_job["request_payload"]))
             and kwargs["source_job"]["svg_style_snapshot"] == leaf_job["svg_style_snapshot"])
    rounds = kwargs["execution"].get("rounds")
    if (isinstance(rounds, list) and len(rounds) == 2
            and all(isinstance(r, Mapping) and r.get("status") == "completed" for r in rounds)):
        # The parent archive above remains fully verified. A completed repair
        # round is outside the phase-0 source-local reuse protocol, not broken
        # authority. Its selected-vector proof below must require fresh QA.
        initial = kwargs["execution"].get("initial_continuation_review") or {}
        _require(initial.get("producer_proof") == leaf
                 and initial.get("source_proof_digest") == leaf.get("digest")
                 and initial.get("namespace") == INITIAL_NAMESPACE
                 and initial.get("initial_continuation") is True
                 and initial.get("status") == "completed"
                 and initial.get("advisory") == rounds[0].get("advisory"))
        return None
    if "flattened_local" in leaf:
        # Exactly the existing original UNKNOWN leaf plus this source's direct
        # local groups; never recursively hydrate or relabel ancestor records.
        original = leaf["flattened_local"]
        _require(isinstance(original, Mapping) and set(original) == {"proof", "execution", "job"}
                 and "flattened_local" not in original["proof"] and not leaf.get("source_local_group_origins"))
        return deepcopy(dict(original))
    # Keep the original sealed execution, not a fabricated completed/UNKNOWN gate.
    material = dict(proof=deepcopy(dict(leaf)), execution=deepcopy(leaf_pages[0]["quality_recovery_execution"]),
        job={key: deepcopy(leaf_job[key]) for key in ("id", "workspace_operation_id", "presentation_id", "svg_style_snapshot")})
    return material


def _checked_current_local_group(marker, ordinal, vector):
    """Same local-only evidence contract, independent of the global verdict."""
    from services.generation_core.quality.vision_advisory_prompt import DESIGN_LOCAL_AXES, DESIGN_REVIEW_PROMPT_VERSION
    indices = list(range(ordinal * 5, min(ordinal * 5 + 5, len(vector))))
    group_id = f"phase-0/group-{ordinal}"
    _require(isinstance(marker, Mapping) and marker.get("status") == "completed"
             and marker.get("group_id") == group_id and marker.get("indices") == indices
             and marker.get("review_scope") == "local" and marker.get("namespace") == INITIAL_NAMESPACE
             and marker.get("physical_purpose") == "initial-continuation"
             and marker.get("physical_ordinal") == ordinal)
    record = marker.get("advisory")
    _require(isinstance(record, Mapping) and record.get("groupId") == group_id
             and record.get("globalPageIndices") == indices and record.get("reviewScope") == "local"
             and record.get("inputDigest") == marker.get("input_digest")
             and record.get("digest") == content_digest({k:v for k,v in record.items() if k != "digest"}))
    _sha(marker.get("input_digest"))
    vision = record.get("vision") or {}
    _require(not vision.get("skipped") and vision.get("promptVersion") == DESIGN_REVIEW_PROMPT_VERSION
             and vision.get("reviewedPageIndices") == list(range(len(indices)))
             and vision.get("reviewedPngDigests") == [vector[i]["png_digest"] for i in indices])
    pages = (vision.get("designReview") or {}).get("pages")
    _require(isinstance(pages, list) and len(pages) == len(indices)
             and all(isinstance(page, Mapping) for page in pages)
             and {page.get("index") for page in pages} == set(range(len(indices))))
    for page in pages:
        _require(page.get("evaluated") is True and set(page.get("axes", {})) == set(DESIGN_LOCAL_AXES)
                 and isinstance(page.get("issues"), list) and isinstance(page.get("designTargets"), list))
        for axis in page["axes"].values():
            _require(isinstance(axis, Mapping) and isinstance(axis.get("visibleEvidence"), str)
                     and bool(axis["visibleEvidence"].strip())
                     and (axis.get("verdict"),axis.get("severity"),axis.get("repairScope")) in {
                         ("passed","none","none"),("advisory","minor","none"),
                         ("failed","major","local_repair"),("failed","critical","local_repair"),
                         ("failed","major","design_rewrite"),("failed","critical","design_rewrite")})
        _require(all(isinstance(target,Mapping) and target.get("axis") in DESIGN_LOCAL_AXES for target in page["designTargets"]))
    return marker


def _build_mixed_local_proof(*, operation_id, job_id, owner_id, tenant_id, archive_digest,
                             parent_manifest_hash, payloads, execution, source_job, flattened_leaf,
                             raw_payloads=None):
    from services.generation_core.drivers.svg.quality import _gate_intact
    from services.svg_presentation_checkpoint import _digest
    material = flattened_leaf
    _require(isinstance(material,Mapping) and set(material)=={"proof","execution","job"})
    leaf, leaf_execution, leaf_job = material["proof"],material["execution"],material["job"]
    _require(isinstance(leaf,Mapping) and "flattened_local" not in leaf and leaf.get("schema")==SOURCE_LOCAL_SCHEMA
             and leaf.get("owner_id")==owner_id and leaf.get("tenant_id")==tenant_id
             and leaf.get("digest")==content_digest({k:v for k,v in leaf.items() if k!="digest"})
             and leaf.get("source_execution_digest")==content_digest(leaf_execution)
             and leaf_execution.get("binding")==leaf.get("source_runtime_binding")
             and leaf_job.get("id")==leaf.get("source_job_id")
             and leaf_job.get("workspace_operation_id")==leaf.get("source_operation_id")
             and leaf_job.get("presentation_id")==leaf.get("source_presentation_id")
             and content_digest(leaf_job.get("svg_style_snapshot"))==leaf.get("source_snapshot_digest"))
    _require(raw_payloads is None or list(raw_payloads)==list(payloads))
    vector = _source_local_vector(payloads)
    _require(len(vector)==len(leaf["source_pages"]) and len(vector)>5
             and str(source_job.get("id"))==job_id and str(source_job.get("workspace_operation_id"))==operation_id
             and source_job.get("svg_style_snapshot")==leaf_job.get("svg_style_snapshot")
             and payloads[0].get("quality_recovery_execution")==execution
             and execution.get("schema")=="svg-quality-recovery-execution/v1"
             and execution.get("reviewPrinciplesVersion")==leaf.get("review_principles_version")
             and len(execution.get("rounds",[]))==1
             and not any(page.get("quality_recovery",{}).get("selection") for page in payloads))
    _sha(execution.get("binding"))
    original = execution["rounds"][0]
    initial = execution.get("initial_continuation_review") or {}
    prior = initial.get("producer_proof") or {}
    _require(prior.get("schema")==SOURCE_LOCAL_SCHEMA and prior.get("flattened_local")==material
             and not prior.get("source_local_group_origins")
             and prior.get("owner_id")==owner_id and prior.get("tenant_id")==tenant_id
             and prior.get("digest")==content_digest({k:v for k,v in prior.items() if k!="digest"})
             and initial.get("source_proof_digest")==prior["digest"]
             and initial.get("namespace")==INITIAL_NAMESPACE and initial.get("initial_continuation") is True
             and initial.get("status")==original.get("status")=="completed"
             and initial.get("advisory")==original.get("advisory")
             and original.get("page_digests")==[_digest({"svg":page.get("svg_content"),"png":page.get("png_sha256")}) for page in payloads])
    binding = dict(operationId=operation_id,jobId=job_id,presentationId=str(source_job["presentation_id"]),ownerId=owner_id,tenantId=tenant_id)
    gate = _gate_intact(original["advisory"])
    _require(gate is not None and gate.get("binding")==binding and gate.get("runtimeBinding")==execution["binding"]
             and gate.get("snapshotDigest")==leaf["source_snapshot_digest"]
             and gate.get("pageDigests")==[item["svg_digest"] for item in vector]
             and gate.get("pngDigests")==[item["png_digest"] for item in vector]
             and len(gate.get("pages",[]))==len(vector)
             and all(page.get("mandatoryEvidenceValid") is True for page in gate["pages"]))
    # Global coverage/status is intentionally preserved, never used as local
    # coverage. Only twenty fully evaluated four-axis local records suffice.
    leaf_records,_ = _source_local_records(leaf_execution,payloads,sealed_vector=leaf["source_pages"])
    _require([content_digest(marker) for marker in leaf_records]==leaf["source_local_group_digests"])
    fresh = prior.get("fresh_local_indices") or []
    fresh_groups = {index//5 for index in fresh}
    _require(set(fresh)=={i for group in fresh_groups for i in range(group*5,min(group*5+5,len(vector)))})
    _require(prior.get("source_local_group_digests")==[content_digest(marker) for i,marker in enumerate(leaf_records) if i not in fresh_groups])
    groups = original.get("groups") or {}
    _require(set(groups)=={str(i) for i in fresh_groups}|{str(len(leaf_records))})
    vision = original["advisory"]["vision"]
    returned,consumers = vision.get("reviewGroups"),vision.get("localEvidenceConsumers")
    _require(isinstance(returned,list) and len(returned)==len(leaf_records)+1
             and isinstance(consumers,list) and len(consumers)==len(leaf_records)-len(fresh_groups))
    by_group = {item.get("producer_group_id"):item for item in consumers if isinstance(item,Mapping)}
    _require(len(by_group)==len(consumers))
    records,origins = [],[]
    for ordinal,leaf_marker in enumerate(leaf_records):
        if ordinal in fresh_groups:
            marker = _checked_current_local_group(groups[str(ordinal)],ordinal,vector)
            _require(marker["group_id"] not in by_group)
            origins.append("source")
        else:
            marker = leaf_marker
            consumer = by_group.get(marker["group_id"]) or {}
            _require(all(vector[i]==leaf["source_pages"][i] for i in marker["indices"])
                     and consumer.get("schema")=="svg-local-review-consumer/v1" and consumer.get("namespace")==INITIAL_NAMESPACE
                     and consumer.get("producer_operation_id")==leaf["source_operation_id"]
                     and consumer.get("producer_job_id")==leaf["source_job_id"]
                     and consumer.get("producer_record_digest")==content_digest(marker)
                     and consumer.get("producer_input_digest")==marker["input_digest"]
                     and consumer.get("producer_source_local_digest")==leaf["digest"]
                     and consumer.get("consumer_source_local_digest")==prior["digest"]
                     and consumer.get("consumer_binding")==binding and consumer.get("indices")==marker["indices"]
                     and consumer.get("provider_response_reused") is False and consumer.get("original_verdict_preserved") is True)
            origins.append("leaf")
        _require(returned[ordinal]==marker["advisory"])
        records.append(marker)
    global_group = groups[str(len(records))]
    _require(global_group.get("status")=="completed" and global_group.get("advisory")==returned[-1]
             and returned[-1].get("reviewScope")=="global"
             and returned[-1].get("digest")==content_digest({k:v for k,v in returned[-1].items() if k!="digest"}))
    value = dict(schema=SOURCE_LOCAL_SCHEMA,namespace=INITIAL_NAMESPACE,source_operation_id=operation_id,
        source_job_id=job_id,source_presentation_id=str(source_job["presentation_id"]),owner_id=owner_id,tenant_id=tenant_id,
        archive_digest=archive_digest,parent_manifest_hash=parent_manifest_hash,source_pages=vector,fresh_local_indices=[],
        source_snapshot_digest=leaf["source_snapshot_digest"],source_runtime_binding=execution["binding"],
        review_principles_version=leaf["review_principles_version"],source_execution_digest=content_digest(execution),
        source_local_group_digests=[content_digest(marker) for marker in records],source_local_group_origins=origins,
        **{key: leaf[key] for key in ("preserved_unknown_global_digest", "preserved_rejected_global_digest") if key in leaf},source_consumer_digest=content_digest(consumers),
        completed_global_digest=content_digest(global_group),completed_gate_digest=gate["digest"],flattened_local=deepcopy(dict(material)))
    return value|{"digest":content_digest(value)}


def _build_flattened_local_proof(*, operation_id, job_id, owner_id, tenant_id, archive_digest,
                               parent_manifest_hash, payloads, execution, source_job, flattened_leaf,
                               raw_payloads=None, baseline_projections=None, fresh_page_witnesses=None):
    from services.generation_core.drivers.svg.quality import _gate_intact
    from services.svg_presentation_checkpoint import _digest
    prior = (execution.get("initial_continuation_review") or {}).get("producer_proof")
    if isinstance(prior, Mapping) and prior.get("flattened_local") is not None:
        _require(not baseline_projections and not fresh_page_witnesses)
        return _build_mixed_local_proof(operation_id=operation_id, job_id=job_id, owner_id=owner_id,
            tenant_id=tenant_id, archive_digest=archive_digest, parent_manifest_hash=parent_manifest_hash,
            payloads=payloads, execution=execution, source_job=source_job, flattened_leaf=flattened_leaf,
            raw_payloads=raw_payloads)
    material = flattened_leaf
    _require(isinstance(material, Mapping) and set(material) == {"proof", "execution", "job"})
    leaf, leaf_execution, leaf_job = material["proof"], material["execution"], material["job"]
    _require(isinstance(leaf, Mapping) and "flattened_local" not in leaf
             and leaf.get("schema") == SOURCE_LOCAL_SCHEMA
             and leaf.get("owner_id") == owner_id and leaf.get("tenant_id") == tenant_id)
    _require(leaf.get("digest") == content_digest({k: v for k, v in leaf.items() if k != "digest"})
             and leaf.get("source_execution_digest") == content_digest(leaf_execution)
             and leaf_job.get("id") == leaf.get("source_job_id")
             and leaf_job.get("workspace_operation_id") == leaf.get("source_operation_id")
             and leaf_job.get("presentation_id") == leaf.get("source_presentation_id")
             and content_digest(leaf_job.get("svg_style_snapshot")) == leaf.get("source_snapshot_digest")
             and leaf_execution.get("binding") == leaf.get("source_runtime_binding")
             and leaf_execution.get("reviewPrinciplesVersion") == leaf.get("review_principles_version"))
    # Author-only projections authorize a fresh render, never old measured
    # evidence. Their immutable raw row and official witness delimit a slot
    # which cannot contribute a reusable local group.
    projections = baseline_projections or {}
    raw_payloads = raw_payloads if raw_payloads is not None else payloads
    _require(isinstance(projections, Mapping) and len(raw_payloads) == len(payloads))
    _require(set(projections) <= {str(i) for i in range(len(payloads))})
    fresh_pages = fresh_page_witnesses or {}
    _require(isinstance(fresh_pages, Mapping)
             and set(fresh_pages) <= {str(i) for i in range(len(payloads))}
             and not set(fresh_pages).intersection(projections))
    vector = []
    for index, payload in enumerate(payloads):
        classification = fresh_pages.get(str(index))
        if classification is not None:
            raw = raw_payloads[index]
            _require(isinstance(classification, Mapping)
                     and set(classification) == {"category", "row_digest", "source_payload_digest"}
                     and classification.get("category") in {"unknown", "unstarted", "known_failed_output"}
                     and classification.get("source_payload_digest") == _digest(raw) and payload == raw)
            _sha(classification.get("row_digest"))
            vector.append(dict(index=index, phase="fresh-author-required",
                source_category=classification["category"], raw_payload_digest=_digest(raw),
                classification_digest=content_digest(classification)))
            continue
        projection = projections.get(str(index)) or {}
        witness = projection.get("witness") or {}
        if witness.get("schema") in {"svg-completed-repair-author-projection/v1",
                                     "svg-terminal-repair-author-projection/v1"}:
            raw = raw_payloads[index]
            marker = raw.get("quality_recovery") or {}
            _require(witness.get("source_operation_id") == operation_id
                     and witness.get("source_payload_digest") == _digest(raw)
                     and witness.get("prior_candidate_digest") == _digest(marker.get("prior_candidate"))
                     and marker.get("status") == "repair_pending"
                     and witness.get("input_digest") == raw.get("input_digest"))
            for key in ("completed_receipt_digest", "completed_output_digest", "current_author_svg_digest"):
                _sha(witness.get(key))
            _sha(projection.get("projected_payload_digest"))
            if raw != payload:
                from services.svg_cached_author_render_recovery import author_witness
                _require(_digest(payload) == projection["projected_payload_digest"]
                         and _svg_sha(payload) == witness["current_author_svg_digest"]
                         and author_witness(payload) == witness.get("current_author_receipt_digest",
                                                                   witness.get("current_author_witness_digest"))
                         and payload.get("source_digest") is None
                         and all(payload.get(key) is None for key in ("quality", "initial_quality", "png_path", "png_sha256")))
            # On archive replay the raw row stays raw. This sentinel grants no
            # author/render/review authority; the prepared manifest owns that.
            vector.append(dict(index=index, phase="fresh-author-only",
                raw_payload_digest=_digest(raw), projection_digest=content_digest(projection)))
        else:
            item = _source_local_vector([payload])[0]
            vector.append({**item, "index": index})
    _require(len(vector) == len(leaf["source_pages"])
             and str(source_job.get("id")) == job_id and str(source_job.get("workspace_operation_id")) == operation_id
             and source_job.get("svg_style_snapshot") == leaf_job.get("svg_style_snapshot")
             and payloads[0].get("quality_recovery_execution") == execution
             and execution.get("schema") == "svg-quality-recovery-execution/v1"
             and execution.get("reviewPrinciplesVersion") == leaf["review_principles_version"]
             and len(execution.get("rounds", [])) == 1
             and not any(page.get("quality_recovery", {}).get("selection") for page in payloads))
    round_zero, initial = execution["rounds"][0], execution.get("initial_continuation_review")
    _sha(execution.get("binding"))
    _require(isinstance(initial, Mapping) and initial.get("status") == "completed"
             and initial.get("namespace") == INITIAL_NAMESPACE and initial.get("initial_continuation") is True
             and initial.get("producer_proof") == leaf and initial.get("source_proof_digest") == leaf["digest"]
             and round_zero.get("status") == "completed" and initial.get("advisory") == round_zero.get("advisory")
             and round_zero.get("page_digests") == leaf_execution["rounds"][0].get("page_digests"))
    advisory = round_zero["advisory"]
    gate = _gate_intact(advisory)
    binding = dict(operationId=operation_id, jobId=job_id, presentationId=str(source_job["presentation_id"]),
                   ownerId=owner_id, tenantId=tenant_id)
    _require(gate is not None and gate.get("binding") == binding
             and gate.get("runtimeBinding") == execution.get("binding")
             and gate.get("snapshotDigest") == leaf["source_snapshot_digest"]
             and gate.get("pageCoverageComplete") is True
             and gate.get("pageDigests") == [p["svg_digest"] for p in leaf["source_pages"]]
             and gate.get("pngDigests") == [p["png_digest"] for p in leaf["source_pages"]])
    records, _ = _source_local_records(leaf_execution, payloads, sealed_vector=leaf["source_pages"])
    _require([content_digest(marker) for marker in records] == leaf.get("source_local_group_digests"))
    vision = advisory["vision"]
    returned, consumers = vision.get("reviewGroups"), vision.get("localEvidenceConsumers")
    _require(isinstance(returned, list) and len(returned) == len(records) + 1
             and isinstance(consumers, list) and len(consumers) == len(records))
    by_group = {item.get("producer_group_id"): item for item in consumers if isinstance(item, Mapping)}
    _require(len(by_group) == len(records))
    for index, marker in enumerate(records):
        consumer = by_group.get(marker["group_id"])
        _require(returned[index] == marker["advisory"] and isinstance(consumer, Mapping)
                 and consumer.get("schema") == "svg-local-review-consumer/v1" and consumer.get("namespace") == INITIAL_NAMESPACE
                 and consumer.get("producer_operation_id") == leaf["source_operation_id"]
                 and consumer.get("producer_job_id") == leaf["source_job_id"]
                 and consumer.get("producer_record_digest") == content_digest(marker)
                 and consumer.get("producer_input_digest") == marker["input_digest"]
                 and consumer.get("producer_source_local_digest") == leaf["digest"]
                 and consumer.get("consumer_binding") == binding and consumer.get("indices") == marker["indices"]
                 and consumer.get("provider_response_reused") is False and consumer.get("original_verdict_preserved") is True)
    groups = round_zero.get("groups")
    _require(isinstance(groups, Mapping) and set(groups) == {str(len(records))})
    global_group = groups[str(len(records))]
    _require(global_group.get("status") == "completed" and global_group.get("advisory") == returned[-1]
             and returned[-1].get("reviewScope") == "global"
             and returned[-1].get("digest") == content_digest({k: v for k, v in returned[-1].items() if k != "digest"}))
    for index, (raw, projected) in enumerate(zip(raw_payloads, payloads, strict=True)):
        projection = projections.get(str(index))
        if projection is None:
            _require(raw == projected)
            continue
        witness = projection.get("witness") or {}
        marker = raw.get("quality_recovery") or {}
        if vector[index].get("phase") == "fresh-author-only":
            if witness["schema"] == "svg-completed-repair-author-projection/v1":
                _require(witness.get("source_manifest_hash") == parent_manifest_hash
                         and witness.get("approved_gate_digest") == gate["digest"])
            else:
                _require(witness.get("approved_gate_digest") == _digest(gate))
                replay = witness.get("terminal_recovery_witness") or {}
                _require(replay.get("schema") == "svg-author-terminal-local-replay/v1"
                         and replay.get("source_operation_id") == operation_id
                         and replay.get("source_job_id") == job_id
                         and replay.get("input_digest") == raw.get("input_digest")
                         and replay.get("completed_receipt_digest") == witness["completed_receipt_digest"]
                         and replay.get("completed_output_digest") == witness["completed_output_digest"])
            continue
        _require(witness.get("schema") == "svg-pre-dispatch-repair-baseline/v1"
                 and witness.get("source_operation_id") == operation_id
                 and witness.get("source_payload_digest") == _digest(raw)
                 and witness.get("prior_candidate_digest") == _digest(marker.get("prior_candidate"))
                 and witness.get("source_manifest_hash") == parent_manifest_hash
                 and witness.get("approved_gate_digest") == gate["digest"]
                 and witness.get("current_author_repair_calls") == 0
                 and projection.get("projected_payload_digest") == _digest(projected)
                 and vector[index]["svg_digest"] == witness.get("svg_sha256")
                 and vector[index]["png_digest"] == _sha(witness.get("png_sha256"))
                 and vector[index]["input_digest"] == witness.get("input_digest")
                 and vector[index]["author_receipt_digest"] == witness.get("author_receipt_digest"))
    fresh = []
    reused_digests = []
    for ordinal, start in enumerate(range(0, len(vector), 5)):
        indices = list(range(start, min(start + 5, len(vector))))
        if any(vector[i] != leaf["source_pages"][i] for i in indices):
            fresh.extend(indices)
        else:
            reused_digests.append(content_digest(records[ordinal]))
    value = dict(schema=SOURCE_LOCAL_SCHEMA, namespace=INITIAL_NAMESPACE,
        source_operation_id=operation_id, source_job_id=job_id, source_presentation_id=str(source_job["presentation_id"]),
        owner_id=owner_id, tenant_id=tenant_id, archive_digest=archive_digest, parent_manifest_hash=parent_manifest_hash,
        source_pages=vector, fresh_local_indices=fresh,
        source_snapshot_digest=leaf["source_snapshot_digest"], source_runtime_binding=execution["binding"],
        review_principles_version=leaf["review_principles_version"], source_execution_digest=content_digest(execution),
        source_local_group_digests=reused_digests,
        **{key: leaf[key] for key in ("preserved_unknown_global_digest", "preserved_rejected_global_digest") if key in leaf},
        source_consumer_digest=content_digest(consumers), completed_global_digest=content_digest(global_group),
        completed_gate_digest=gate["digest"], flattened_local=deepcopy(dict(material)),
        **({"fresh_source_pages": deepcopy(dict(fresh_pages))} if fresh_pages else {}),
        **({"baseline_projections": deepcopy(dict(projections))} if projections else {}))
    return value | {"digest": content_digest(value)}


def build_source_local_proof(*, operation_id, job_id, owner_id, tenant_id, archive_digest,
                             parent_manifest_hash, payloads, execution, source_job, flattened_leaf=None,
                             raw_payloads=None, baseline_projections=None, fresh_page_witnesses=None):
    rounds = execution.get("rounds") if isinstance(execution, Mapping) else None
    selections = [p.get("quality_recovery", {}) for p in payloads
                  if p.get("quality_recovery", {}).get("selection")]
    if (isinstance(rounds, list) and len(rounds) == 2 and selections
            and all(isinstance(r, Mapping) and r.get("status") == "completed" for r in rounds)
            and all(m.get("status") == "selected" and isinstance(m.get("selection"), Mapping)
                    and m["selection"].get("selected") == "candidate" for m in selections)):
        _require(flattened_leaf is None and not baseline_projections and not fresh_page_witnesses
                 and (raw_payloads is None or list(raw_payloads) == list(payloads)))
        # No local QA authority is minted. build_selected_proof independently
        # checks both immutable gates/vectors and seals fresh-all QA mode.
        return None
    if flattened_leaf is not None or _inherited_local_required(execution, source_job):
        return _build_flattened_local_proof(operation_id=operation_id, job_id=job_id, owner_id=owner_id,
            tenant_id=tenant_id, archive_digest=archive_digest, parent_manifest_hash=parent_manifest_hash,
            payloads=payloads, execution=execution, source_job=source_job, flattened_leaf=flattened_leaf,
            raw_payloads=raw_payloads, baseline_projections=baseline_projections,
            fresh_page_witnesses=fresh_page_witnesses)
    _require(not fresh_page_witnesses)
    snapshot = source_job.get("svg_style_snapshot") or {}
    rounds = execution.get("rounds") if isinstance(execution, Mapping) else None
    if (len(payloads) <= 5 or (snapshot.get("quality_recovery_policy") or {}).get("version") != 2
            or not isinstance(rounds, list) or len(rounds) != 1
            or not isinstance(rounds[0], Mapping) or rounds[0].get("status") not in {"review_unknown", "completed"}
            or any(page.get("quality_recovery", {}).get("selection") for page in payloads)):
        return None
    groups = rounds[0].get("groups")
    group_count = (len(payloads) + 4) // 5
    if (not isinstance(groups, Mapping)
            or set(groups) != {str(index) for index in range(group_count + 1)}
            or any(not isinstance(groups.get(str(index)), Mapping)
                   or groups[str(index)].get("status") != "completed" for index in range(group_count))
            or not isinstance(groups.get(str(group_count)), Mapping)
            or groups[str(group_count)].get("status") != (
                "completed" if rounds[0]["status"] == "completed" else "review_unknown")):
        return None
    _require(str(source_job.get("id")) == job_id
             and str(source_job.get("workspace_operation_id")) == operation_id)
    vector = _source_local_vector(payloads)
    if rounds[0]["status"] == "completed":
        initial = execution.get("initial_continuation_review") or {}
        prior = initial.get("producer_proof") or {}
        global_vision = (groups[str(group_count)].get("advisory") or {}).get("vision") or {}
        if (prior.get("schema") != "svg-final-selected-continuation/v1"
                or prior.get("local_review_mode") != "fresh-all"
                or global_vision.get("skipped") != "design_review_schema_or_coverage_invalid"):
            return None
        _require(not baseline_projections and not fresh_page_witnesses
                 and (raw_payloads is None or list(raw_payloads) == list(payloads)))
    records, unknown = _source_local_records(execution, payloads)
    if rounds[0]["status"] == "completed":
        from services.generation_core.drivers.svg.quality import _gate_intact
        gate = _gate_intact(rounds[0]["advisory"])
        initial = execution["initial_continuation_review"]
        lineage = (source_job.get("request_payload") or {}).get("_svg_partial_recovery") or {}
        _require(initial["source_proof_digest"] == lineage.get("final_selected_continuation_digest")
                 and lineage.get("manifest_hash") == parent_manifest_hash
                 and lineage.get("operation_id") == operation_id and lineage.get("job_id") == job_id
                 and gate.get("binding") == dict(operationId=operation_id, jobId=job_id,
                    presentationId=str(source_job["presentation_id"]), ownerId=owner_id, tenantId=tenant_id)
                 and gate.get("snapshotDigest") == content_digest(snapshot))
    value = dict(schema=SOURCE_LOCAL_SCHEMA, namespace=INITIAL_NAMESPACE,
        source_operation_id=operation_id, source_job_id=job_id,
        source_presentation_id=str(source_job["presentation_id"]), owner_id=owner_id, tenant_id=tenant_id,
        archive_digest=archive_digest, parent_manifest_hash=parent_manifest_hash,
        source_pages=vector, fresh_local_indices=[],
        source_snapshot_digest=content_digest(snapshot), source_runtime_binding=execution["binding"],
        review_principles_version=execution["reviewPrinciplesVersion"],
        source_execution_digest=content_digest(execution),
        source_local_group_digests=[content_digest(marker) for marker in records],
        **({"preserved_rejected_global_digest": content_digest(unknown),
            "completed_gate_digest": gate["digest"]} if rounds[0]["status"] == "completed"
           else {"preserved_unknown_global_digest": content_digest(unknown)}))
    return value | {"digest": content_digest(value)}


def archived_source_local_payloads(archive, working, proof, *, manifest_pages=None):
    from services.svg_cached_author_render_recovery import digest
    _require(proof.get("schema") == SOURCE_LOCAL_SCHEMA and digest(archive) == proof.get("archive_digest"))
    job = archive.get("job")
    rows = working.get("checkpoints")
    _require(isinstance(job, Mapping) and isinstance(rows, list) and len(rows) == len(proof["source_pages"]))
    by_index = {row.get("slide_index"): row for row in rows}
    _require(set(by_index) == set(range(len(rows))))
    raw_payloads = [deepcopy(by_index[index]["svg_checkpoint"]) for index in range(len(rows))]
    payloads = deepcopy(raw_payloads)
    fresh_pages = proof.get("fresh_source_pages") or {}
    _require(isinstance(fresh_pages, Mapping))
    if fresh_pages or manifest_pages is not None:
        _require(isinstance(manifest_pages, list) and len(manifest_pages) == len(rows)
                 and set(fresh_pages) == {str(i) for i, page in enumerate(manifest_pages)
                     if page.get("category") in {"unknown", "unstarted", "known_failed_output"}})
    for key, classification in fresh_pages.items():
        _require(isinstance(key, str) and key.isdigit() and str(int(key)) == key and int(key) < len(rows)
                 and isinstance(classification, Mapping)
                 and classification.get("row_digest") == digest(by_index[int(key)])
                 and classification.get("category") == manifest_pages[int(key)].get("category")
                 and classification.get("row_digest") == manifest_pages[int(key)].get("row_digest"))
    projections = proof.get("baseline_projections") or {}
    if projections:
        from types import SimpleNamespace
        from services.generation_core.drivers.svg.quality_recovery import _restore_candidate
        for key, projection in projections.items():
            _require(isinstance(key, str) and key.isdigit() and str(int(key)) == key and int(key) < len(payloads))
            index = int(key)
            witness = projection.get("witness") or {}
            raw = raw_payloads[index]
            _require(witness.get("source_payload_digest") == digest(raw))
            if witness.get("schema") in {"svg-completed-repair-author-projection/v1",
                                         "svg-terminal-repair-author-projection/v1"}:
                # Fresh-only source slots intentionally retain raw archive data.
                # build_source_local_proof rechecks witness and group closure.
                continue
            _require(witness.get("schema") == "svg-pre-dispatch-repair-baseline/v1")
            payloads[index] = _restore_candidate((raw.get("quality_recovery") or {}).get("prior_candidate"), raw,
                SimpleNamespace(workspace_operation_id=proof["source_operation_id"]))
            _require(digest(payloads[index]) == projection.get("projected_payload_digest"))
    expected = build_source_local_proof(operation_id=proof["source_operation_id"], job_id=proof["source_job_id"],
        owner_id=proof["owner_id"], tenant_id=proof["tenant_id"], archive_digest=proof["archive_digest"],
        parent_manifest_hash=proof["parent_manifest_hash"], payloads=payloads,
        execution=payloads[0].get("quality_recovery_execution", {}), source_job=job,
        flattened_leaf=proof.get("flattened_local"), raw_payloads=raw_payloads, baseline_projections=projections,
        fresh_page_witnesses=fresh_pages)
    _require(expected is not None and dict(proof) == expected)
    return deepcopy(dict(job)), payloads


def _consume_source_local_producer(context, pages, renders, evidence, reused, consumers, namespace):
    from services.generation_core.drivers.svg.quality import _group_review_input_digest, _local_group_context
    proof = evidence["proof"]
    _require(namespace in {INITIAL_NAMESPACE, NAMESPACE} and proof.get("namespace") == INITIAL_NAMESPACE)
    _require(evidence.get("consumer_binding") == context["binding"])
    _require(context["binding"].get("ownerId") == proof["owner_id"]
             and context["binding"].get("tenantId") == proof["tenant_id"])
    _require(evidence.get("consumer_runtime_equivalent") is True)
    _require(context.get("snapshotDigest") == proof["source_snapshot_digest"]
             and context.get("reviewPrinciplesVersion") == proof["review_principles_version"])
    _require(len(context.get("mandatoryEvidence", [])) == len(pages) and all(context["mandatoryEvidence"]))
    producer_job, payloads = evidence["producer_job"], evidence["producer_payloads"]
    _require(bool(payloads) and evidence["execution"] == payloads[0].get("quality_recovery_execution"))
    expected = build_source_local_proof(operation_id=proof["source_operation_id"], job_id=proof["source_job_id"],
        owner_id=proof["owner_id"], tenant_id=proof["tenant_id"], archive_digest=proof["archive_digest"],
        parent_manifest_hash=proof["parent_manifest_hash"], payloads=payloads,
        execution=evidence["execution"], source_job=producer_job, flattened_leaf=proof.get("flattened_local"),
        raw_payloads=evidence.get("producer_raw_payloads"), baseline_projections=proof.get("baseline_projections"),
        fresh_page_witnesses=proof.get("fresh_source_pages"))
    _require(expected is not None and expected == proof)
    current_vector = _source_local_vector([payload for payload, _ in pages])
    _require(len(current_vector) == len(proof["source_pages"]))
    fresh = proof.get("fresh_local_indices") or []
    _require(isinstance(fresh, list) and len(set(fresh)) == len(fresh)
             and all(type(index) is int and 0 <= index < len(pages) for index in fresh))
    for start in range(0, len(pages), 5):
        group_indices = set(range(start, min(start + 5, len(pages))))
        _require(not group_indices.intersection(fresh) or group_indices <= set(fresh))
    if namespace == INITIAL_NAMESPACE:
        _require(all(current_vector[i] == proof["source_pages"][i] for i in range(len(pages)) if i not in fresh))
    leaf = proof.get("flattened_local")
    record_proof = leaf["proof"] if leaf else proof
    record_execution = leaf["execution"] if leaf else evidence["execution"]
    records, _ = _source_local_records(record_execution, payloads,
        sealed_vector=record_proof["source_pages"] if leaf else None)
    origins = proof.get("source_local_group_origins")
    producer_proofs = [record_proof] * len(records)
    if origins is not None:
        _require(leaf is not None and isinstance(origins,list) and len(origins)==len(records)
                 and set(origins)<={"leaf","source"})
        for ordinal, origin in enumerate(origins):
            if origin == "source":
                records[ordinal] = _checked_current_local_group(evidence["execution"]["rounds"][0]["groups"][str(ordinal)],
                    ordinal,proof["source_pages"])
                producer_proofs[ordinal] = proof
        _require([content_digest(marker) for marker in records]==proof["source_local_group_digests"])
    projected_contexts = {}
    pending = []
    for ordinal, marker in enumerate(records):
        record_proof = producer_proofs[ordinal]
        if namespace == NAMESPACE and ordinal in reused:
            continue
        indices = marker["indices"]
        if any(index in fresh for index in indices):
            continue
        if namespace == NAMESPACE and any(current_vector[index] != proof["source_pages"][index] for index in indices):
            continue
        _require(all(sha256(renders[index]).hexdigest() == proof["source_pages"][index]["png_digest"]
                     for index in indices))
        key = record_proof["source_operation_id"]
        if key not in projected_contexts:
            projected = deepcopy(dict(context))
            projected["binding"] = dict(jobId=record_proof["source_job_id"],operationId=key,
                presentationId=record_proof["source_presentation_id"],ownerId=proof["owner_id"],tenantId=proof["tenant_id"])
            projected["runtimeBinding"] = record_proof["source_runtime_binding"]
            projected["priorCandidates"] = {}
            if "blindVariantLabel" in projected:
                vector_digest = content_digest([item["png_digest"] for item in record_proof["source_pages"]])
                projected["blindVariantLabel"] = "A" if int(vector_digest[-1],16)%2 else "B"
            projected_contexts[key] = projected
        projected = projected_contexts[key]
        group = _local_group_context(projected, indices, False)
        expected_input = _group_review_input_digest(group, [renders[index] for index in indices], 0, ordinal, indices)
        if expected_input != marker["input_digest"]:
            if namespace == NAMESPACE:
                continue
            raise FinalSelectedEvidenceError("svg_final_selected_context_unproven")
        pending.append((ordinal, marker, expected_input, record_proof))
    # No partial reuse or new local request is allowed when any group disagrees.
    for ordinal, marker, expected_input, record_proof in pending:
        _require(ordinal not in reused)
        reused[ordinal] = deepcopy(dict(marker["advisory"]))
        consumers.append(dict(schema="svg-local-review-consumer/v1", namespace=namespace,
            producer_operation_id=record_proof["source_operation_id"], producer_job_id=record_proof["source_job_id"],
            producer_group_id=marker["group_id"], producer_record_digest=content_digest(marker),
            producer_input_digest=expected_input, consumer_binding=deepcopy(context["binding"]),
            indices=deepcopy(marker["indices"]), provider_response_reused=False,
            original_verdict_preserved=True, producer_source_local_digest=record_proof["digest"],
            **({"consumer_source_local_digest": proof["digest"]} if leaf else {})))


def reusable_source_local_groups(context, pages, renders, evidence):
    reused, consumers = {}, []
    _consume_source_local_producer(context, pages, renders, evidence, reused, consumers, INITIAL_NAMESPACE)
    fresh_groups = {index // 5 for index in evidence["proof"].get("fresh_local_indices", [])}
    _require(len(reused) == (len(pages) + 4) // 5 - len(fresh_groups) and len(consumers) == len(reused))
    return reused, consumers


def reusable_continuation_groups(context, pages, renders, evidence):
    if evidence.get("proof", {}).get("schema") == SOURCE_LOCAL_SCHEMA:
        return reusable_source_local_groups(context, pages, renders, evidence)
    from services.svg_final_selected_continuation import _consume_local_producer, _fresh_local_review_required
    if _fresh_local_review_required(context, pages, renders, evidence):
        return {}, []
    reused, consumers = {}, []
    namespace = evidence.get("consumer_namespace", NAMESPACE)
    for source in [evidence, *evidence.get("additional_producers", [])]:
        if source.get("proof", {}).get("schema") == SOURCE_LOCAL_SCHEMA:
            _require(namespace == NAMESPACE)
            _consume_source_local_producer(context, pages, renders, source, reused, consumers, namespace)
        else:
            _consume_local_producer(context, pages, renders, source, reused, consumers, namespace)
    proof = evidence["proof"]
    if not reused and len(proof["fresh_local_indices"]) < len(proof["source_pages"]):
        raise FinalSelectedEvidenceError("svg_final_selected_context_unproven")
    return reused, consumers


async def prepare_source_local_source(ctx, job, hooks):
    from types import SimpleNamespace
    from models.sql.workspace import WorkspaceOperation
    from services.svg_partial_recovery import (
        ARCHIVE_KEY, verify_prepared_episode, _hydrate_checkpoint_archive, _frozen_payload, frozen_episode_payload,
    )
    from services.generation_core.drivers.svg.quality_recovery import ensure_recovery_authority, recovery_binding
    from services.svg_presentation_checkpoint import _digest

    manifest = ctx.partial_recovery_manifest
    await ensure_recovery_authority(job, hooks)
    async with hooks.async_session_maker() as session:
        operation = await session.get(WorkspaceOperation, job.workspace_operation_id)
        current = await verify_prepared_episode(session, operation, job)
        _require(_digest(current) == _digest(manifest))
        proof = current.get("source_local_continuation")
        _require(isinstance(proof, Mapping) and proof.get("schema") == SOURCE_LOCAL_SCHEMA)
        _require(proof.get("owner_id") == current.get("owner_id")
                 and proof.get("tenant_id") == current.get("tenant_id")
                 and proof.get("source_operation_id") == current.get("operation_id")
                 and proof.get("source_job_id") == current.get("job_id")
                 and proof.get("source_presentation_id") == current.get("presentation_id"))
        archive = operation.request_payload[ARCHIVE_KEY]
        working = await _hydrate_checkpoint_archive(session, current, archive)
        producer_job, payloads = archived_source_local_payloads(archive, working, proof, manifest_pages=current.get("pages"))
        await session.rollback()
    equivalent = (
        bool(getattr(ctx, "quality_recovery_checkpoint_reuse_eligible", False))
        and _digest(frozen_episode_payload(job.request_payload, current)) == _digest(_frozen_payload(producer_job["request_payload"]))
        and _digest(job.svg_style_snapshot) == _digest(producer_job["svg_style_snapshot"]))
    request = SimpleNamespace(instructions=job.request_payload.get("instructions"))
    source_runtime = recovery_binding(ctx, request,
        SimpleNamespace(workspace_operation_id=proof["source_operation_id"]))
    if not equivalent or source_runtime != proof["source_runtime_binding"]:
        raise FinalSelectedEvidenceError("svg_final_selected_runtime_unproven")
    evidence = dict(proof=proof, consumer_namespace=INITIAL_NAMESPACE,
        execution=payloads[0]["quality_recovery_execution"], producer_job=producer_job,
        producer_payloads=payloads, consumer_runtime_equivalent=True,
        producer_raw_payloads=[deepcopy(row["svg_checkpoint"]) for row in sorted(working["checkpoints"], key=lambda row: row["slide_index"])],
        consumer_binding=dict(jobId=str(job.id), operationId=str(job.workspace_operation_id),
            presentationId=str(job.presentation_id), ownerId=current["owner_id"], tenantId=current["tenant_id"]),
        producer_presentation_id=producer_job["presentation_id"])
    ctx.quality_final_selected_source = evidence
    return evidence


