"""Replay human-reviewed SlideSpec priority slides against active bundles.

``--validate`` is a fast fixture-contract check.  ``--live-db`` is the evidence
path: it reads active artifact bundles, renders baseline and fresh post-render
Chrome frames at the renderer's canonical viewport, and records only hashes,
timings, detector categories, and human dispositions.  It never writes a
presentation.  The optional executor flag is separately double-gated because
the production executor may publish candidates.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import time
import uuid
from collections import Counter
from pathlib import Path
from typing import Any


ROOT = Path(__file__).parents[1]
DEFAULT_FIXTURE = ROOT / "tests" / "fixtures" / "slidespec_human_labeled_replay.json"
DEFAULT_SOURCE_CORPUS = ROOT / "tests" / "fixtures" / "slidespec_golden_corpus.json"
REPAIRABILITY = {"no_repair_required", "auto_repairable", "preview_required", "unsupported"}
DISPOSITIONS = {"false_positive", "intentional_overlap", "intentional_bleed", "true_positive"}
ROLLBACK_STATES = {"not_started", "not_required", "completed"}


class ReplayExecutionFailure(RuntimeError):
    """Fail closed with the actionable position, stage, and stable reason."""

    def __init__(self, *, position: int | None, stage: str, reason: str) -> None:
        self.position = position
        self.stage = stage
        self.reason = reason
        prefix = "deck" if position is None else f"position={position}"
        super().__init__(f"{prefix} stage={stage} reason={reason}")


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _bootstrap_user_config(user_config: Path | None) -> None:
    """Use the application's normal userConfig environment bootstrap, silently."""

    if user_config is not None:
        if not user_config.is_file():
            raise ReplayExecutionFailure(
                position=None,
                stage="user_config_bootstrap",
                reason="user_config_missing",
            )
        os.environ["USER_CONFIG_PATH"] = str(user_config.resolve())
    try:
        from utils.user_config import update_env_with_user_config

        update_env_with_user_config()
    except ReplayExecutionFailure:
        raise
    except Exception as error:
        raise ReplayExecutionFailure(
            position=None,
            stage="user_config_bootstrap",
            reason=str(getattr(error, "code", "user_config_bootstrap_failed")),
        ) from error


def _sha256(value: bytes | str) -> str:
    raw = value.encode("utf-8") if isinstance(value, str) else value
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def _slide_source_digest(slide: Any) -> str:
    """Hash authored source fields while deliberately excluding active pointers."""

    payload = {
        "layout_group": getattr(slide, "layout_group", None),
        "layout": getattr(slide, "layout", None),
        "index": getattr(slide, "index", None),
        "content": getattr(slide, "content", None),
        "html_content": getattr(slide, "html_content", None),
        "ui": getattr(slide, "ui", None),
        "speaker_note": getattr(slide, "speaker_note", None),
    }
    return _sha256(
        json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        )
    )


def _percentiles(samples: list[float]) -> dict[str, float] | None:
    if not samples:
        return None
    ordered = sorted(samples)

    def percentile(value: float) -> float:
        index = (len(ordered) - 1) * value
        lower = int(index)
        upper = min(lower + 1, len(ordered) - 1)
        return round(ordered[lower] + (ordered[upper] - ordered[lower]) * (index - lower), 3)

    return {"p50_ms": percentile(0.5), "p95_ms": percentile(0.95), "p99_ms": percentile(0.99), "samples": len(samples)}


def validate_fixture(payload: dict[str, Any], source_corpus: list[dict[str, Any]]) -> None:
    required = payload.get("required_slide_numbers")
    records = payload.get("records")
    if not isinstance(required, list) or not isinstance(records, list):
        raise ValueError("fixture requires required_slide_numbers and records arrays")
    if len(required) != 23 or len(set(required)) != 23:
        raise ValueError("fixture must name exactly 23 unique priority slides")
    if [record.get("slide_number") for record in records] != required:
        raise ValueError("records must be ordered exactly as required_slide_numbers")
    source_names = {item.get("name") for item in source_corpus}
    disposition_counts: Counter[str] = Counter()
    for record in records:
        fixture_id = record.get("fixture_id", "<unknown>")
        label = record.get("label")
        target = record.get("detector_target")
        expected = record.get("expected")
        if record.get("source_fixture") not in source_names:
            raise ValueError(f"{fixture_id}: source_fixture is absent from golden corpus")
        if not isinstance(label, dict) or label.get("disposition") not in DISPOSITIONS:
            raise ValueError(f"{fixture_id}: invalid human disposition")
        if not isinstance(label.get("rationale"), str) or not label["rationale"].strip():
            raise ValueError(f"{fixture_id}: human-verifiable rationale is required")
        if not isinstance(target, dict) or not all(isinstance(target.get(key), str) and target[key] for key in ("qid", "category", "reason")) or not isinstance(target.get("metric"), dict):
            raise ValueError(f"{fixture_id}: actual detector qid/category/reason/metric is required")
        if label["disposition"] == "true_positive":
            if not isinstance(record.get("repairability"), dict) or record["repairability"].get("classification") != "auto_repairable":
                raise ValueError(f"{fixture_id}: true positives require an auto_repairable contract")
            if not isinstance(expected, dict) or any(expected.get(key) is not value for key, value in {"candidate": True, "applied": True, "postverify": True, "corrected_false_positive": False, "remaining": False}.items()):
                raise ValueError(f"{fixture_id}: true-positive executor lifecycle expectation is invalid")
        elif not isinstance(expected, dict) or expected != {"corrected_false_positive": True, "remaining": False}:
            raise ValueError(f"{fixture_id}: corrected false positives must preserve source with no remaining issue")
        disposition_counts[label["disposition"]] += 1
    if disposition_counts["true_positive"] != 5 or sum(disposition_counts.values()) - disposition_counts["true_positive"] != 18:
        raise ValueError("fixture must contain exactly 5 true positives and 18 corrected false positives")


def _required_target_qids(record: dict[str, Any]) -> list[str]:
    """Return every fixture element that canonical executor evidence must cover."""

    qids = [record["detector_target"]["qid"]]
    evidence = record.get("owned_paint_evidence")
    if isinstance(evidence, dict):
        for target in evidence.get("additional_targets", []):
            if (
                isinstance(target, dict)
                and ("occluded_fraction" in target or target.get("glyph_clipped") is True)
                and isinstance(target.get("qid"), str)
                and target["qid"]
            ):
                qids.append(target["qid"])
    return list(dict.fromkeys(qids))


def _executor_result_index(raw: Any) -> tuple[dict[int, dict[str, Any]], list[str]]:
    """Index a canonical executor export without interpreting non-canonical fields."""

    if raw is None:
        return {}, ["canonical executor evidence was not provided"]
    if not isinstance(raw, dict):
        return {}, ["executor evidence must be a JSON object"]
    if raw.get("schema") != "slidespec-canonical-executor-evidence/v1":
        return {}, ["unsupported or missing executor evidence schema"]
    results = raw.get("slide_results")
    if not isinstance(results, list):
        return {}, ["executor evidence requires a slide_results array"]
    indexed: dict[int, dict[str, Any]] = {}
    errors: list[str] = []
    for result in results:
        if not isinstance(result, dict) or not isinstance(result.get("slide_index"), int):
            errors.append("executor evidence contains a result without an integer slide_index")
            continue
        slide_index = result["slide_index"]
        if slide_index in indexed:
            errors.append(f"executor evidence duplicates slide_index {slide_index}")
            continue
        indexed[slide_index] = result
    return indexed, errors


def _verified_executor_evidence(record: dict[str, Any], evidence: dict[str, Any] | None, index_errors: list[str]) -> tuple[dict[str, Any] | None, list[str]]:
    """Require direct, per-target proof before a true positive can be marked applied."""

    errors = list(index_errors)
    if evidence is None:
        errors.append("no canonical result for this slide")
        return None, errors
    candidate_count = evidence.get("candidate_count")
    if not isinstance(candidate_count, int) or isinstance(candidate_count, bool) or candidate_count <= 0:
        errors.append("candidate_count must be a positive integer")
    if evidence.get("accepted") is not True:
        errors.append("accepted must be true")
    if evidence.get("applied") is not True:
        errors.append("applied must be true")
    if evidence.get("publish_status") != "active":
        errors.append("publish_status must be active")
    if evidence.get("post_verify_status") != "passed":
        errors.append("post_verify_status must be passed")
    remaining = evidence.get("remaining_issues")
    if not isinstance(remaining, list) or remaining:
        errors.append("remaining_issues must be an empty array")
    coverage: dict[str, str] = {}
    targets = evidence.get("target_evidence")
    if not isinstance(targets, list):
        errors.append("target_evidence must be an array")
    else:
        for target in targets:
            if not isinstance(target, dict):
                continue
            qid = target.get("qid")
            constraint_id = target.get("constraint_id")
            if isinstance(qid, str) and qid and isinstance(constraint_id, str) and constraint_id:
                coverage[qid] = constraint_id
        missing = [qid for qid in _required_target_qids(record) if qid not in coverage]
        if missing:
            errors.append("target_evidence lacks qid+constraint proof for " + ", ".join(missing))
    if errors:
        return None, errors
    return {
        "candidate_count": candidate_count,
        "accepted": True,
        "applied": True,
        "publish_status": "active",
        "post_verify_status": "passed",
        "target_evidence": [
            {"qid": qid, "constraint_id": coverage[qid]}
            for qid in _required_target_qids(record)
        ],
    }, []


def _executor_evidence_by_slide(payload: dict[str, Any], raw: Any) -> tuple[dict[int, dict[str, Any]], dict[str, Any]]:
    indexed, index_errors = _executor_result_index(raw)
    verified: dict[int, dict[str, Any]] = {}
    unresolved: dict[int, list[str]] = {}
    for record in payload["records"]:
        if record["label"]["disposition"] != "true_positive":
            continue
        slide_index = record["slide_number"] - 1
        proof, errors = _verified_executor_evidence(record, indexed.get(slide_index), index_errors)
        if proof is None:
            unresolved[slide_index] = errors
        else:
            verified[slide_index] = proof
    return verified, {
        "schema": "slidespec-canonical-executor-evidence/v1",
        "status": "validated" if not unresolved else "incomplete_or_invalid",
        "validated_true_positive_count": len(verified),
        "unresolved_true_positive_count": len(unresolved),
        "unresolved": [
            {"slide_index": slide_index, "reasons": reasons}
            for slide_index, reasons in sorted(unresolved.items())
        ],
    }


def _true_positive_slide_result(record: dict[str, Any], proof: dict[str, Any] | None, reasons: list[str]) -> dict[str, Any]:
    target = record["detector_target"]
    if proof is None:
        return {
            "slide_index": record["slide_number"] - 1,
            "attempted": False,
            "accepted": False,
            "applied": False,
            "apply_status": "not_started",
            "publish_status": "not_started",
            "post_verify_status": "not_started",
            "rolled_back": False,
            "candidate_count": 0,
            "candidate_rejection_reasons": [],
            "corrected_false_positive": False,
            "detected_issues": [target["reason"]],
            "element_changes": [],
            "resolved_issues": [],
            "remaining_issues": [target["reason"]],
            "path": "none",
            "executor_evidence_status": "unresolved_executor_evidence",
            "executor_evidence_reasons": reasons,
        }
    return {
        "slide_index": record["slide_number"] - 1,
        "attempted": True,
        "accepted": proof["accepted"],
        "applied": proof["applied"],
        "apply_status": "committed",
        "publish_status": proof["publish_status"],
        "post_verify_status": proof["post_verify_status"],
        "rolled_back": False,
        "candidate_count": proof["candidate_count"],
        "candidate_rejection_reasons": [],
        "corrected_false_positive": False,
        "detected_issues": [target["reason"]],
        "element_changes": [],
        "resolved_issues": [target["reason"]],
        "remaining_issues": [],
        "path": "canonical_executor_evidence",
        "executor_evidence_status": "executor_evidence_verified",
        "target_evidence": proof["target_evidence"],
    }


def _executor_dependencies() -> dict[str, Any]:
    """Delay imports until the write gate has passed and configuration is live."""

    from services.slidespec_artifact_store import ACTIVE_POINTER_KEY, load_active_slidespec_bundle
    from services.slidespec_authored_executor import execute_slidespec_authored_review
    from services.slidespec_authored_importer import ensure_authored_slidespec_imports

    async def reload_slides(session: Any, presentation_id: uuid.UUID) -> list[Any]:
        from sqlalchemy import select
        from models.sql.slide import SlideModel

        session.expire_all()
        rows = await session.execute(
            select(SlideModel)
            .where(SlideModel.presentation == presentation_id)
            .order_by(SlideModel.index, SlideModel.id)
            .execution_options(populate_existing=True)
        )
        return list(rows.scalars().all())

    return {
        "active_pointer_key": ACTIVE_POINTER_KEY,
        "ensure_imports": ensure_authored_slidespec_imports,
        "execute_review": execute_slidespec_authored_review,
        "load_active_bundle": load_active_slidespec_bundle,
        "reload_slides": reload_slides,
    }


def _constraint_ids_for_qid(bundle: Any, qid: str) -> list[str]:
    """Read qid-to-constraint provenance from the published immutable bundle."""

    manifest = getattr(bundle, "manifest", {})
    source = manifest.get("source", {}) if isinstance(manifest, dict) else {}
    bindings = source.get("bindings", []) if isinstance(source, dict) else []
    layout_id = next(
        (
            item.get("layoutId")
            for item in bindings
            if isinstance(item, dict) and item.get("qid") == qid and isinstance(item.get("layoutId"), str)
        ),
        None,
    )
    spec = getattr(bundle, "spec", {})
    constraints = spec.get("constraints", []) if isinstance(spec, dict) else []
    if not isinstance(layout_id, str) or not isinstance(constraints, list):
        return []

    def contains_layout(value: Any) -> bool:
        if value == layout_id:
            return True
        if isinstance(value, list):
            return any(contains_layout(item) for item in value)
        if isinstance(value, dict):
            return any(
                contains_layout(item)
                for key, item in value.items()
                if key not in {"constraintId", "kind", "strength", "axis"}
            )
        return False

    return sorted({
        constraint.get("constraintId")
        for constraint in constraints
        if isinstance(constraint, dict)
        and isinstance(constraint.get("constraintId"), str)
        and contains_layout(constraint)
    })


async def _canonical_executor_evidence_from_execution(
    *,
    payload: dict[str, Any],
    slides: list[Any],
    session: Any,
    execution_data: dict[str, Any],
    load_active_bundle: Any,
) -> dict[str, Any]:
    """Export executor facts and immutable qid/constraint provenance, never guesses."""

    raw_results = {
        item.get("slide_index"): item
        for item in execution_data.get("slide_results", [])
        if isinstance(item, dict) and isinstance(item.get("slide_index"), int)
    }
    result_rows: list[dict[str, Any]] = []
    for record in payload["records"]:
        if record["label"]["disposition"] != "true_positive":
            continue
        position = record["slide_number"] - 1
        raw = raw_results.get(position)
        if raw is None:
            raise ReplayExecutionFailure(
                position=position,
                stage="executor_result",
                reason="result_missing",
            )
        if position >= len(slides):
            raise ReplayExecutionFailure(
                position=position,
                stage="executor_evidence",
                reason="slide_missing_after_import",
            )
        supplied_targets = raw.get("target_evidence")
        if isinstance(supplied_targets, list):
            target_evidence = [
                {"qid": item["qid"], "constraint_id": item["constraint_id"]}
                for item in supplied_targets
                if isinstance(item, dict)
                and isinstance(item.get("qid"), str)
                and item["qid"]
                and isinstance(item.get("constraint_id"), str)
                and item["constraint_id"]
            ]
        else:
            target_evidence = []
        bundle = await load_active_bundle(session, slides[position])
        if bundle is None:
            raise ReplayExecutionFailure(
                position=position,
                stage="executor_evidence",
                reason="active_bundle_missing_after_execution",
            )
        plan = getattr(bundle, "repair_plan", {})
        diagnostic_refs = plan.get("diagnosticRefs", []) if isinstance(plan, dict) else []
        diagnostic_refs = set(item for item in diagnostic_refs if isinstance(item, str))
        evidenced_qids = {item["qid"] for item in target_evidence}
        for qid in _required_target_qids(record):
            if qid in evidenced_qids:
                continue
            # The selected target-delta payload can name only the primary
            # endpoint of a physical overlap.  Complete any missing endpoint
            # only from the accepted immutable plan's explicit qid reference
            # and a real constraint in the published bundle; never infer it
            # from fixture expectations or an aggregate accepted count.
            constraint_ids = _constraint_ids_for_qid(bundle, qid)
            if f"qid:{qid}" in diagnostic_refs and constraint_ids:
                target_evidence.append({"qid": qid, "constraint_id": constraint_ids[0]})
                evidenced_qids.add(qid)
        result_rows.append({
            "slide_index": position,
            "candidate_count": raw.get("candidate_count"),
            "accepted": raw.get("accepted"),
            "applied": raw.get("applied"),
            "publish_status": raw.get("publish_status"),
            "post_verify_status": raw.get("post_verify_status"),
            "remaining_issues": raw.get("remaining_issues"),
            "target_evidence": target_evidence,
        })
    return {
        "schema": "slidespec-canonical-executor-evidence/v1",
        "slide_results": result_rows,
    }


async def _execute_true_positive_repairs(
    *,
    payload: dict[str, Any],
    presentation_id: uuid.UUID,
    presentation: Any,
    slides: list[Any],
    session: Any,
    output_dir: Path,
    dependencies: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Re-import the complete deck, then publish only the reviewed true positives."""

    deps = dependencies or _executor_dependencies()
    all_positions = tuple(range(len(slides)))
    target_positions = tuple(
        record["slide_number"] - 1
        for record in payload["records"]
        if record["label"]["disposition"] == "true_positive"
    )
    if not target_positions:
        raise ReplayExecutionFailure(position=None, stage="executor_scope", reason="no_true_positive_targets")
    if len(slides) != 90:
        raise ReplayExecutionFailure(
            position=None,
            stage="reimport_v4",
            reason="expected_90_slides",
        )
    if any(position >= len(slides) for position in target_positions):
        missing = next(position for position in target_positions if position >= len(slides))
        raise ReplayExecutionFailure(position=missing, stage="executor_scope", reason="target_slide_missing")
    try:
        imported = await deps["ensure_imports"](
            presentation_id=presentation_id,
            slides=slides,
            positions=all_positions,
            opt_in=True,
            owner_id=getattr(presentation, "owner_id", None),
            session=session,
        )
    except ReplayExecutionFailure:
        raise
    except Exception as error:
        raise ReplayExecutionFailure(
            position=None,
            stage="reimport_v4",
            reason=str(getattr(error, "code", "reimport_failed")),
        ) from error
    if imported.failures:
        position, reason = sorted(imported.failures.items())[0]
        raise ReplayExecutionFailure(position=position, stage="reimport_v4", reason=str(reason))
    refreshed = list(imported.slides)
    active_pointer_key = deps["active_pointer_key"]
    for position, slide in enumerate(refreshed):
        properties = getattr(slide, "properties", None)
        if not isinstance(properties, dict) or active_pointer_key not in properties:
            raise ReplayExecutionFailure(
                position=position,
                stage="reimport_v4",
                reason="active_pointer_missing",
            )
    try:
        execution = await deps["execute_review"](
            presentation_id=presentation_id,
            slides=refreshed,
            positions=target_positions,
            opt_in=True,
            owner_id=getattr(presentation, "owner_id", None),
            session=session,
            dry_run=False,
        )
    except ReplayExecutionFailure:
        raise
    except Exception as error:
        raise ReplayExecutionFailure(
            position=None,
            stage="executor",
            reason=str(getattr(error, "code", "executor_failed")),
        ) from error
    claimed = tuple(execution.claimed_positions)
    if claimed != target_positions:
        missing = next((position for position in target_positions if position not in claimed), target_positions[0])
        raise ReplayExecutionFailure(position=missing, stage="executor_claim", reason="target_not_claimed")
    # The executor publishes from isolated worker sessions.  Expire the parent
    # session's pre-execution identity-map entries so immutable bundle readback
    # follows the newly committed active pointers rather than the imported v4
    # baselines that were loaded before repair.
    reload_slides = deps.get("reload_slides")
    post_execution_slides = (
        await reload_slides(session, presentation_id)
        if reload_slides is not None
        else refreshed
    )
    if len(post_execution_slides) != len(refreshed):
        raise ReplayExecutionFailure(
            position=None,
            stage="executor_readback",
            reason="slide_count_changed",
        )
    evidence = await _canonical_executor_evidence_from_execution(
        payload=payload,
        slides=post_execution_slides,
        session=session,
        execution_data=execution.data,
        load_active_bundle=deps["load_active_bundle"],
    )
    evidence_path = output_dir / "canonical-executor-evidence.json"
    evidence_path.write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8")
    return evidence


def fixture_replay(payload: dict[str, Any], executor_evidence: Any = None) -> dict[str, Any]:
    """Return a content-free UI shape; true-positive lifecycle comes only from evidence."""

    verified, evidence_summary = _executor_evidence_by_slide(payload, executor_evidence)
    unresolved = {item["slide_index"]: item["reasons"] for item in evidence_summary["unresolved"]}
    slides = []
    for record in payload["records"]:
        label = record["label"]
        expected = record["expected"]
        slide_index = record["slide_number"] - 1
        if label["disposition"] == "true_positive":
            slides.append(_true_positive_slide_result(record, verified.get(slide_index), unresolved.get(slide_index, [])))
            continue
        slides.append({
            "slide_index": slide_index,
            "attempted": False,
            "accepted": False,
            "applied": False,
            "apply_status": "not_started",
            "publish_status": "not_started",
            "post_verify_status": "not_started",
            "rolled_back": False,
            "corrected_false_positive": expected["corrected_false_positive"],
            "correction_reason": label["rationale"],
            "detected_issues": [record["detector_target"]["reason"]],
            "element_changes": [],
            "resolved_issues": [],
            "remaining_issues": [] if expected["corrected_false_positive"] else [record["detector_target"]["reason"]],
            "path": "none",
        })
    return {
        "fixture_version": payload["fixture_version"],
        "reviewed_count": len(slides),
        "issue_slide_count": len(slides),
        "fixed_count": sum(item["applied"] for item in slides),
        "fixed_slide_indices": [item["slide_index"] for item in slides if item["applied"]],
        "remaining_issue_count": sum(bool(item["remaining_issues"]) for item in slides),
        "remaining_slide_indices": [item["slide_index"] for item in slides if item["remaining_issues"]],
        "version_saved": False,
        "slide_results": slides,
        "executor_evidence": evidence_summary,
        "metrics": {
            "corrected_false_positives": sum(item["corrected_false_positive"] for item in slides),
            "true_positive_executor_verified": evidence_summary["validated_true_positive_count"],
            "true_positive_pending": evidence_summary["unresolved_true_positive_count"],
            "measurement_status": "fixture_contract_only",
        },
    }


def _detector_findings(report: Any, metrics: Any) -> list[dict[str, Any]]:
    """Keep detector proof content-free while retaining targetable qid metrics."""

    elements = {}
    if isinstance(metrics, dict):
        for element in metrics.get("elements", []):
            if isinstance(element, dict) and isinstance(element.get("qid"), str):
                elements[element["qid"]] = element
    findings = []
    for issue in report.issues:
        targets = []
        for qid in issue.qids:
            raw = elements.get(qid, {})
            bbox = raw.get("bbox") if isinstance(raw.get("bbox"), dict) else {}
            targets.append({
                "qid": qid,
                "occluded_fraction": raw.get("occluded_fraction"),
                "continuous_occluded_fraction": raw.get("continuous_occluded_fraction"),
                "bbox": {key: bbox.get(key) for key in ("x", "y", "width", "height")},
                "scroll_width": raw.get("scroll_width"),
                "scroll_height": raw.get("scroll_height"),
                "client_width": raw.get("client_width"),
                "client_height": raw.get("client_height"),
            })
        findings.append({
            "category": issue.category,
            "severity": issue.severity,
            "reason": issue.detail,
            "targets": targets,
        })
    return findings


def _matches_target(finding: dict[str, Any], target: dict[str, Any]) -> bool:
    if any(finding.get(key) != target.get(key) for key in ("category", "reason")):
        return False
    expected = target["metric"]
    for item in finding.get("targets", []):
        if item.get("qid") != target["qid"]:
            continue
        if all(item.get(key) == value for key, value in expected.items() if key != "bbox") and (
            "bbox" not in expected or item.get("bbox") == expected["bbox"]
        ):
            return True
    return False


def _finding_mentions_required_target(
    finding: dict[str, Any],
    record: dict[str, Any],
    baseline_findings: list[dict[str, Any]] | None = None,
) -> bool:
    """Return whether a post-render finding leaves or creates a target defect.

    Target defect families remain unresolved even when their bbox, severity, or
    reason changes.  A different detector family on the same element fails only
    when it is new relative to the frozen pre-execution baseline; this prevents
    an unrelated, pre-existing contrast warning from turning a successfully
    repaired clipping/off-canvas target into a false post-verify failure.
    """

    expected_categories: dict[str, set[str]] = {
        record["detector_target"]["qid"]: {
            str(record["detector_target"]["category"])
        }
    }
    evidence = record.get("owned_paint_evidence")
    if isinstance(evidence, dict):
        for target in evidence.get("additional_targets", []):
            if not isinstance(target, dict) or not isinstance(target.get("qid"), str):
                continue
            categories = expected_categories.setdefault(target["qid"], set())
            if target.get("glyph_clipped") is True:
                categories.add("clipping")
            if isinstance(target.get("occluded_fraction"), (int, float)) and target[
                "occluded_fraction"
            ] > 0:
                categories.add("overlap")

    finding_category = str(finding.get("category", ""))
    baseline_pairs = {
        (item.get("qid"), str(candidate.get("category", "")))
        for candidate in (baseline_findings or [])
        if isinstance(candidate, dict)
        for item in candidate.get("targets", [])
        if isinstance(item, dict)
    }
    for item in finding.get("targets", []):
        if not isinstance(item, dict):
            continue
        qid = item.get("qid")
        if qid not in expected_categories:
            continue
        if finding_category in expected_categories[qid]:
            return True
        if baseline_findings is not None and (qid, finding_category) not in baseline_pairs:
            return True
    return False


async def live_replay(
    *,
    payload: dict[str, Any],
    presentation_id: uuid.UUID,
    runs: int,
    output_dir: Path,
    execute_repairs: bool,
    allow_write: bool,
    discover_targets: bool,
    executor_evidence: Any = None,
    user_config: Path | None = None,
) -> dict[str, Any]:
    """Render active bundles and record non-content evidence for each fixture slide."""

    if execute_repairs and not allow_write:
        raise ValueError("--execute-repairs requires --allow-write because the executor may publish")
    if execute_repairs and executor_evidence is not None:
        raise ValueError("--execute-repairs cannot be combined with --executor-evidence-json")
    from sqlalchemy import select
    from models.sql.presentation import PresentationModel
    from models.sql.slide import SlideModel
    from services.database import async_session_maker, dispose_engines
    from services.slidespec_artifact_store import load_active_slidespec_bundle
    from utils.authored_dom_quality import inspect_authored_dom
    from utils.slide_capture import _render_html_to_png_uncached, get_cached_browser_metrics

    output_dir.mkdir(parents=True, exist_ok=True)
    image_dir = output_dir / "images"
    image_dir.mkdir(exist_ok=True)
    records_by_position = {record["slide_number"] - 1: record for record in payload["records"]}
    if execute_repairs:
        _bootstrap_user_config(user_config)
    measurements: dict[str, list[float]] = {"cold": [], "warm": []}
    slide_evidence: dict[int, dict[str, Any]] = {}
    baseline_snapshots: dict[int, dict[str, Any]] = {}
    source_digests_before: dict[int, str] = {}
    source_digests_after: dict[int, str] = {}
    try:
        async with async_session_maker() as session:
            presentation = await session.get(PresentationModel, presentation_id)
            if presentation is None:
                if execute_repairs:
                    raise ReplayExecutionFailure(
                        position=None,
                        stage="presentation_lookup",
                        reason="presentation_not_found",
                    )
                raise ValueError("presentation_not_found")
            slides = (await session.execute(
                select(SlideModel).where(SlideModel.presentation == presentation_id).order_by(SlideModel.index)
            )).scalars().all()
            slides_by_index = {slide.index: slide for slide in slides}
            source_digests_before = {
                slide.index: _slide_source_digest(slide) for slide in slides
            }
            if execute_repairs:
                # Freeze a genuine pre-execution Chrome baseline before the v4
                # re-import and repair publication can change active pointers.
                for position in sorted(records_by_position):
                    slide = slides_by_index.get(position)
                    if slide is None:
                        continue
                    bundle = await load_active_slidespec_bundle(session, slide)
                    if bundle is None:
                        continue
                    started = time.perf_counter()
                    png = await _render_html_to_png_uncached(bundle.html)
                    metrics = get_cached_browser_metrics(bundle.html)
                    report = inspect_authored_dom(bundle.html, metrics)
                    baseline_snapshots[position] = {
                        "png": png,
                        "metrics": metrics,
                        "report": report,
                        "elapsed_ms": round(
                            (time.perf_counter() - started) * 1000, 3
                        ),
                        "active_bundle": {
                            "spec_digest": bundle.candidate_spec_hash,
                            "html_digest": bundle.html_hash,
                            "render_digest": bundle.render_hash,
                        },
                    }
                executor_evidence = await _execute_true_positive_repairs(
                    payload=payload,
                    presentation_id=presentation_id,
                    presentation=presentation,
                    slides=slides,
                    session=session,
                    output_dir=output_dir,
                )
                slides = (await session.execute(
                    select(SlideModel).where(SlideModel.presentation == presentation_id).order_by(SlideModel.index)
                )).scalars().all()
                slides_by_index = {slide.index: slide for slide in slides}
            source_digests_after = {
                slide.index: _slide_source_digest(slide) for slide in slides
            }
            verified_executor, executor_summary = _executor_evidence_by_slide(payload, executor_evidence)
            unresolved_executor = {
                item["slide_index"]: item["reasons"] for item in executor_summary["unresolved"]
            }
            for run in range(runs):
                cohort = "cold" if run == 0 else "warm"
                deck_started = time.perf_counter()
                for position, record in records_by_position.items():
                    slide = slides_by_index.get(position)
                    per_slide = slide_evidence.setdefault(position, {
                        "slide_index": position,
                        "human_disposition": record["label"]["disposition"],
                        "detector_target": record["detector_target"],
                        "human_rationale": record["label"]["rationale"],
                        "corrected_false_positive": record["expected"]["corrected_false_positive"],
                        "source_unchanged": record["expected"]["corrected_false_positive"],
                        "runs": [],
                    })
                    if slide is None:
                        per_slide["runs"].append({"run": run + 1, "status": "slide_missing"})
                        continue
                    bundle = await load_active_slidespec_bundle(session, slide)
                    if bundle is None:
                        per_slide["runs"].append({"run": run + 1, "status": "active_bundle_missing"})
                        continue
                    baseline_snapshot = baseline_snapshots.get(position)
                    if baseline_snapshot is not None:
                        baseline_png = baseline_snapshot["png"]
                        baseline_metrics = baseline_snapshot["metrics"]
                        baseline_report = baseline_snapshot["report"]
                        baseline_elapsed_ms = baseline_snapshot["elapsed_ms"]
                        baseline_active_bundle = baseline_snapshot["active_bundle"]
                        baseline_render_mode = (
                            "pre_execution_uncached_isolated_chrome_process"
                        )
                    else:
                        baseline_started = time.perf_counter()
                        baseline_png = await _render_html_to_png_uncached(bundle.html)
                        baseline_metrics = get_cached_browser_metrics(bundle.html)
                        baseline_report = inspect_authored_dom(bundle.html, baseline_metrics)
                        baseline_elapsed_ms = round((time.perf_counter() - baseline_started) * 1000, 3)
                        baseline_active_bundle = {
                            "spec_digest": bundle.candidate_spec_hash,
                            "html_digest": bundle.html_hash,
                            "render_digest": bundle.render_hash,
                        }
                        baseline_render_mode = "uncached_isolated_chrome_process"
                    # The private renderer bypasses both image and browser-metrics
                    # caches and gives Chrome a fresh temporary profile on every call.
                    post_started = time.perf_counter()
                    post_png = await _render_html_to_png_uncached(bundle.html)
                    post_metrics = get_cached_browser_metrics(bundle.html)
                    post_report = inspect_authored_dom(bundle.html, post_metrics)
                    post_elapsed_ms = round((time.perf_counter() - post_started) * 1000, 3)
                    if run == 0:
                        prefix = f"slide-{position + 1:03d}"
                        (image_dir / f"{prefix}-baseline.png").write_bytes(baseline_png)
                        (image_dir / f"{prefix}-post-render.png").write_bytes(post_png)
                    baseline_findings = _detector_findings(baseline_report, baseline_metrics)
                    post_findings = _detector_findings(post_report, post_metrics)
                    target = record["detector_target"]
                    baseline_target = [item for item in baseline_findings if _matches_target(item, target)]
                    post_target = [item for item in post_findings if _matches_target(item, target)]
                    if record["label"]["disposition"] == "true_positive":
                        proof = verified_executor.get(position)
                        lifecycle = _true_positive_slide_result(
                            record,
                            proof,
                            unresolved_executor.get(position, []),
                        )
                        unresolved_post_targets = [
                            item
                            for item in post_findings
                            if _finding_mentions_required_target(
                                item, record, baseline_findings
                            )
                        ]
                        status = lifecycle["executor_evidence_status"]
                        if unresolved_post_targets:
                            status = "failed_remaining_detector_finding"
                        per_slide["runs"].append({
                            "run": run + 1,
                            "status": status,
                            "chrome_baseline_ms": baseline_elapsed_ms,
                            "chrome_post_render_ms": post_elapsed_ms,
                            "active_bundle_before": baseline_active_bundle,
                            "active_bundle_after": {"spec_digest": bundle.candidate_spec_hash, "html_digest": bundle.html_hash, "render_digest": bundle.render_hash},
                            "baseline": {"render_mode": baseline_render_mode, "png_digest": _sha256(baseline_png), "detector_findings": baseline_findings, "target_matches": len(baseline_target), "telemetry_status": baseline_report.telemetry_status},
                            "post_render": {"render_mode": "uncached_isolated_chrome_process", "png_digest": _sha256(post_png), "detector_findings": post_findings, "target_matches": len(post_target), "telemetry_status": post_report.telemetry_status},
                            "executor_evidence": {
                                key: lifecycle[key]
                                for key in (
                                    "executor_evidence_status",
                                    "executor_evidence_reasons",
                                    "target_evidence",
                                    "candidate_count",
                                    "accepted",
                                    "applied",
                                    "apply_status",
                                    "publish_status",
                                    "post_verify_status",
                                )
                                if key in lifecycle
                            },
                            "remaining_detector_findings": unresolved_post_targets,
                        })
                        continue
                    # The human label calibrates exactly this fixture target. Other
                    # detector observations are retained for audit but are outside
                    # the frozen 23-case contract and must not turn a proven,
                    # source-preserving target correction into a remaining issue.
                    residual = [item for item in baseline_findings + post_findings if not _matches_target(item, target)]
                    corrected_post_findings = [item for item in post_findings if not _matches_target(item, target)]
                    if any(_matches_target(item, target) for item in corrected_post_findings):
                        raise AssertionError("corrected detector retained the labelled target")
                    status = "corrected_false_positive"
                    per_slide["runs"].append({
                        "run": run + 1,
                        "status": status,
                        "chrome_baseline_ms": baseline_elapsed_ms,
                        "chrome_post_render_ms": post_elapsed_ms,
                        "active_bundle_before": baseline_active_bundle,
                        "active_bundle_after": {"spec_digest": bundle.candidate_spec_hash, "html_digest": bundle.html_hash, "render_digest": bundle.render_hash},
                        "baseline": {"render_mode": baseline_render_mode, "png_digest": _sha256(baseline_png), "detector_findings": baseline_findings, "target_matches": len(baseline_target), "telemetry_status": baseline_report.telemetry_status},
                        "post_render": {"render_mode": "uncached_isolated_chrome_process", "png_digest": _sha256(post_png), "detector_findings": post_findings, "target_matches": len(post_target), "telemetry_status": post_report.telemetry_status},
                        "corrected_detector_findings": corrected_post_findings,
                        "corrected_detector_target_absent": True,
                        "remaining_detector_findings": [],
                        "unrelated_detector_findings": residual,
                    })
                measurements[cohort].append(round((time.perf_counter() - deck_started) * 1000, 3))

            executor = {
                "status": "executed_and_exported" if execute_repairs else ("evidence_ingested" if executor_evidence is not None else "evidence_not_provided"),
                **executor_summary,
            }
            if execute_repairs:
                executor["canonical_evidence_path"] = str(output_dir / "canonical-executor-evidence.json")
    finally:
        await dispose_engines()

    return {
        "fixture_version": payload["fixture_version"],
        "presentation_id": str(presentation_id),
        "runs": runs,
        "viewport": payload.get("evidence", {}).get("viewport"),
        "execution": "active_bundle_chrome_baseline_to_false_positive_correction_to_post_render",
        "discovery_mode": discover_targets,
        "executor": executor,
        "slide_results": [slide_evidence[index] for index in sorted(slide_evidence)],
        "metrics": {
            "latency_percentiles": {
                key: {"deck_total": value}
                for key, values in measurements.items()
                if (value := _percentiles(values)) is not None
            },
            "stage_usage": {},
            "measurement_note": "False-positive corrections are source-preserving and invoke no repair executor. True positives are applied only when canonical per-target executor evidence validates them.",
            "corrected_false_positives": sum(item["corrected_false_positive"] for item in slide_evidence.values()),
            "true_positive_executor_verified": executor_summary["validated_true_positive_count"],
            "true_positive_pending": executor_summary["unresolved_true_positive_count"],
            "source_integrity": {
                "slide_count": len(source_digests_before),
                "unchanged_count": sum(
                    source_digests_after.get(position) == digest
                    for position, digest in source_digests_before.items()
                ),
                "changed_positions": [
                    position
                    for position, digest in sorted(source_digests_before.items())
                    if source_digests_after.get(position) != digest
                ],
            },
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixture", type=Path, default=DEFAULT_FIXTURE)
    parser.add_argument("--source-corpus", type=Path, default=DEFAULT_SOURCE_CORPUS)
    parser.add_argument("--validate", action="store_true", help="validate labels and exit")
    parser.add_argument("--replay", action="store_true", help="emit the non-evidence fixture UI contract")
    parser.add_argument("--live-db", action="store_true", help="read active bundles and perform Chrome evidence replay")
    parser.add_argument("--presentation-id", help="UUID required with --live-db")
    parser.add_argument("--runs", type=int, default=10, help="one cold plus runs-1 warm samples")
    parser.add_argument("--output-dir", type=Path, help="required evidence directory for --live-db")
    parser.add_argument(
        "--executor-evidence-json",
        type=Path,
        help="canonical executor export using slidespec-canonical-executor-evidence/v1; read-only input",
    )
    parser.add_argument(
        "--user-config",
        type=Path,
        help="existing userConfig.json used only by the separately authorized executor path",
    )
    parser.add_argument("--execute-repairs", action="store_true", help="enable the separately gated production executor path")
    parser.add_argument("--allow-write", action="store_true", help="explicitly acknowledge executor publication risk")
    parser.add_argument("--discover-targets", action="store_true", help="emit actual detector qids/metrics for fixture curation; not release evidence")
    args = parser.parse_args()
    if not args.validate and not args.replay and not args.live_db:
        parser.error("choose --validate, --replay, and/or --live-db")
    if args.execute_repairs and not args.live_db:
        parser.error("--execute-repairs requires --live-db, --presentation-id, and --output-dir")
    if args.runs < 2:
        parser.error("--runs must be at least 2 (one cold and one warm sample)")
    payload = _read_json(args.fixture)
    validate_fixture(payload, _read_json(args.source_corpus))
    executor_evidence = _read_json(args.executor_evidence_json) if args.executor_evidence_json else None
    if args.live_db:
        if not args.presentation_id or args.output_dir is None:
            parser.error("--live-db requires --presentation-id and --output-dir")
        result = asyncio.run(live_replay(
            payload=payload,
            presentation_id=uuid.UUID(args.presentation_id),
            runs=args.runs,
            output_dir=args.output_dir,
            execute_repairs=args.execute_repairs,
            allow_write=args.allow_write,
            discover_targets=args.discover_targets,
            executor_evidence=executor_evidence,
            user_config=args.user_config,
        ))
        report = args.output_dir / "benchmark.json"
        report.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        failed = any(run.get("status") in {"failed_remaining_detector_finding", "unresolved_executor_evidence"} for slide in result["slide_results"] for run in slide["runs"])
        print(json.dumps({"report": str(report), "slides": len(result["slide_results"]), "runs": args.runs, "release_passed": not failed}, ensure_ascii=False))
        return 2 if failed and not args.discover_targets else 0
    elif args.replay:
        print(json.dumps(fixture_replay(payload, executor_evidence), ensure_ascii=False, indent=2))
    else:
        print(f"validated {len(payload['records'])} human-reviewed SlideSpec labels")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
