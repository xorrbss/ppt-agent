"""Real-browser import/projection regression over production-shaped documents."""

import asyncio
import json
from pathlib import Path
import uuid

import pytest

from models.slidespec_v1 import REPAIR_PLAN_SCHEMA, RepairPlan
from models.sql.slide import SlideModel
from services.slidespec_authored_importer import build_authored_import_request
from utils.slide_capture import (
    find_chrome,
    get_cached_browser_metrics,
    render_html_to_png_with_metrics,
)
from utils.slidespec_constraints import (
    evaluate_constraints,
    has_soft_constraint_regression,
    imported_projection_roundtrip_errors,
    observations_from_browser_metrics,
)
from utils.slidespec_core import apply_repair_plan, render_slidespec


CORPUS_PATH = Path(__file__).parents[1] / "fixtures" / "slidespec_golden_corpus.json"
CORPUS = json.loads(CORPUS_PATH.read_text(encoding="utf-8"))


def _plan(request, raw_operations: list[dict]) -> RepairPlan:
    assert request.source is not None
    binding_by_qid = {item.qid: item for item in request.source.bindings}
    operations: list[dict] = []
    scope: list[str] = []
    for raw in raw_operations:
        item = dict(raw)
        qid = str(item.pop("qid"))
        target = binding_by_qid[qid].layoutId
        operations.append({**item, "target": target})
        scope.append(target)
    return RepairPlan.model_validate(
        {
            "$schema": REPAIR_PLAN_SCHEMA,
            "planId": "rp-golden-" + request.spec.slideId[-12:],
            "slideId": request.spec.slideId,
            "baseSpecRevision": request.spec.specRevision,
            "baseSpecHash": request.spec.digests.spec,
            "expectedContentDigest": request.spec.digests.content,
            "scope": list(dict.fromkeys(scope)),
            "diagnosticRefs": ["golden.projection.regression"],
            "strategy": "minimal_change",
            "operations": operations,
            "limits": {
                "maxOperations": len(operations),
                "maxAffectedBlocks": len(set(scope)),
            },
            "proposedBy": {
                "kind": "rule_engine",
                "policyVersion": "golden-corpus/1.0",
            },
        }
    )


@pytest.mark.skipif(find_chrome() is None, reason="headless Chrome is required")
@pytest.mark.parametrize("fixture", CORPUS, ids=lambda item: item["name"])
def test_production_shaped_golden_corpus_imports_projects_and_roundtrips(
    fixture: dict,
) -> None:
    assert [item["name"] for item in CORPUS] == [
        "korean-executive-summary-flex",
        "data-dashboard-grid",
        "image-led-case-study",
        "multilingual-quarterly-table",
        "anonymized-chart-scorecard",
        "organization-map-handoff",
        "complex-image-focal-card",
    ]
    index = next(i for i, item in enumerate(CORPUS) if item is fixture)
    slide = SlideModel(
        id=uuid.uuid5(uuid.NAMESPACE_URL, f"slidespec-golden:{fixture['name']}"),
        presentation=uuid.UUID("d3791b98-ecbc-41f9-ae67-40d021e4b45a"),
        layout_group="golden",
        layout=fixture["name"],
        index=index,
        content={},
        html_content=fixture["html"],
        properties={},
    )
    request, imported_png, evidence = asyncio.run(build_authored_import_request(slide))
    baseline = render_slidespec(request)

    assert imported_png.startswith(b"\x89PNG")
    assert request.source is not None
    assert request.source.importerVersion == "authored-html-import/4"
    assert baseline.html == request.source.html
    assert evidence["render_parity"] == "byte_identical_baseline"
    assert evidence["solver"]["feasible"] is True
    assert evidence["solver"]["search_complete"] is True
    baseline_metrics = get_cached_browser_metrics(baseline.html)
    assert baseline_metrics is not None
    assert imported_projection_roundtrip_errors(request, baseline_metrics) == ()
    baseline_report = evaluate_constraints(
        request.spec,
        observations=observations_from_browser_metrics(request, baseline_metrics),
        source=request.source,
    )
    assert baseline_report.hard_failure is False

    repaired = apply_repair_plan(request.spec, _plan(request, fixture["operations"])).spec
    candidate_request = request.model_copy(update={"spec": repaired})
    candidate = render_slidespec(candidate_request)
    candidate_png, repair_metrics = asyncio.run(render_html_to_png_with_metrics(candidate.html))

    assert candidate.html != baseline.html
    assert candidate_png.startswith(b"\x89PNG")
    # The browser import baseline, repaired projection, and a fresh post-render
    # sample each have to preserve source bindings.  Cached metrics alone could
    # otherwise hide a projection that is only valid in the repair renderer.
    assert imported_projection_roundtrip_errors(candidate_request, repair_metrics) == ()
    post_render_png, post_render_metrics = asyncio.run(
        render_html_to_png_with_metrics(candidate.html)
    )
    assert post_render_png.startswith(b"\x89PNG")
    assert imported_projection_roundtrip_errors(
        candidate_request, post_render_metrics
    ) == ()
    observations = observations_from_browser_metrics(candidate_request, post_render_metrics)
    report = evaluate_constraints(
        candidate_request.spec,
        observations=observations,
        source=candidate_request.source,
    )
    qid_by_layout = {
        binding.layoutId: binding.qid for binding in candidate_request.source.bindings
    }
    assert report.hard_failure is False, (
        fixture["name"],
        tuple(
            (
                item.code,
                tuple(qid_by_layout.get(target, target) for target in item.targets),
                item.detail,
            )
            for item in report.violations
        ),
        report.unknown_hard,
    )
    assert not has_soft_constraint_regression(baseline_report, report), fixture["name"]
