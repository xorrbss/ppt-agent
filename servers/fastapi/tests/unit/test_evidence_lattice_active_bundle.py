import asyncio
import json
from pathlib import Path

from models.slidespec_v1 import REPAIR_PLAN_SCHEMA, RepairPlan
from services.slidespec_authored_executor import (
    _confirmed_issue_payload,
    _dom_evidence,
    _incomplete_measurement_payload,
    _merge_dom_evidence,
)
from services.slidespec_repair_pipeline import (
    DomDefect,
    DomEvidence,
    EvidenceAtom,
    PublicationReceipt,
    RenderedCandidate,
    SensorResult,
    SlideSpecInput,
    SlideSpecRepairDependencies,
    SlideSpecRepairPipeline,
    merge_evidence_atoms,
)
from services.slidespec_rollout_policy import SlideSpecRepairRolloutDecision
from utils.authored_dom_quality import (
    STYLE_REFERENCE,
    DomQualityIssue,
    DomQualityReport,
)


FIXTURE = (
    Path(__file__).parents[1]
    / "fixtures"
    / "slidespec_active_bundle_a6470d97.json"
)


def _fixture_contract() -> dict:
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    html = (FIXTURE.parent / payload["html_file"]).read_text(encoding="utf-8")
    for qid in (
        *payload["physical_issue"]["raw_target_qids"],
        payload["unrelated_unknown"]["qid"],
    ):
        assert f'data-qid="{qid}"' in html
    return payload


def _raw_report(*, include_overlap: bool = True) -> DomQualityReport:
    payload = _fixture_contract()
    target_qids = payload["physical_issue"]["raw_target_qids"]
    overlay = "q-div-physical-summary-overlay"
    issues = (
        tuple(
            DomQualityIssue(
                "overlap",
                "high",
                "same summary band occludes owned paint",
                (qid, overlay),
            )
            for qid in target_qids
        )
        if include_overlap
        else ()
    )
    return DomQualityReport(
        STYLE_REFERENCE,
        issues,
        (),
        5,
        telemetry_status="available",
    )


def _metrics(
    *,
    footer_unknown: bool,
    target_unknown: bool = False,
    overlap: bool = True,
) -> dict:
    payload = _fixture_contract()
    tag, text, strong = payload["physical_issue"]["raw_target_qids"]
    footer = payload["unrelated_unknown"]["qid"]
    overlay = "q-div-physical-summary-overlay"

    def item(
        qid: str,
        *,
        parent: str = "",
        x: float = 0,
        width: float = 200,
        fraction: float | None = 1.0,
        occluders: tuple[str, ...] = (overlay,),
    ) -> dict:
        return {
            "qid": qid,
            "parent_qid": parent,
            "bbox": {"x": x, "y": 590, "width": width, "height": 30},
            "occluded_fraction": fraction,
            "continuous_occluded_fraction": fraction,
            "occlusion_occluder_qids": list(occluders),
            "occlusion_unknown_reason": (
                "no_sample_on_owned_paint" if fraction is None else None
            ),
        }

    target_fraction = None if target_unknown else (1.0 if overlap else 0.0)
    return {
        "viewport": {"width": 1280, "height": 720},
        "elements": [
            item(tag, x=80, width=110, fraction=target_fraction),
            item(text, x=210, width=760, fraction=1.0 if overlap else 0.0),
            item(
                strong,
                parent=text,
                x=620,
                width=260,
                fraction=1.0 if overlap else 0.0,
            ),
            item(overlay, x=60, width=1100, fraction=0.0, occluders=()),
            item(
                footer,
                x=1190,
                width=30,
                fraction=None if footer_unknown else 0.0,
                occluders=(),
            ),
        ],
    }


def _qid_map() -> dict[str, str]:
    payload = _fixture_contract()
    tag, text, strong = payload["physical_issue"]["raw_target_qids"]
    footer = payload["unrelated_unknown"]["qid"]
    return {
        tag: "sem:implication-tag",
        text: "sem:implication-text",
        strong: "sem:implication-strong",
        "q-div-physical-summary-overlay": "sem:summary-band",
        footer: "sem:footer-page",
    }


def _evidence(*, footer_unknown: bool = True, target_unknown: bool = False):
    return _dom_evidence(
        _raw_report(),
        _qid_map(),
        browser_metrics=_metrics(
            footer_unknown=footer_unknown,
            target_unknown=target_unknown,
        ),
    )


def _plan() -> RepairPlan:
    target_qid = _fixture_contract()["physical_issue"]["raw_target_qids"][0]
    return RepairPlan.model_validate(
        {
            "$schema": REPAIR_PLAN_SCHEMA,
            "planId": "move-summary-band",
            "slideId": "slide-5",
            "baseSpecRevision": 1,
            "baseSpecHash": "sha256:" + "1" * 64,
            "expectedContentDigest": "sha256:" + "2" * 64,
            "scope": ["sem:implication-tag"],
            "diagnosticRefs": [f"qid:{target_qid}"],
            "strategy": "minimal_change",
            "operations": [
                {
                    "op": "translateWithinParent",
                    "target": "sem:implication-tag",
                    "value": {"x": 0, "y": -0.03},
                }
            ],
            "proposedBy": {"kind": "rule_engine", "policyVersion": "test/1"},
        }
    )


def _rollout() -> SlideSpecRepairRolloutDecision:
    return SlideSpecRepairRolloutDecision(
        "slidespec_active", False, False, True, True, False, 0, None
    )


def _pipeline(candidate: DomEvidence, events: list[str]) -> SlideSpecRepairPipeline:
    async def cascade(spec, dom):
        return (_plan(),)

    async def validate(spec, plan):
        return SensorResult(True)

    async def render(spec, plan):
        return RenderedCandidate("candidate", "render")

    async def inspect(artifact):
        return candidate

    async def ocr(artifact, inventory):
        return SensorResult(True)

    async def vqa(artifact):
        return SensorResult(True, quality_gain=1.0)

    async def publish(spec, plan, artifact):
        events.append("publish")
        return PublicationReceipt(True, True, True)

    return SlideSpecRepairPipeline(
        SlideSpecRepairDependencies(
            deterministic_cascade=cascade,
            validate_plan=validate,
            render_plan=render,
            inspect_dom=inspect,
            run_ocr=ocr,
            run_vqa=vqa,
            publish=publish,
        )
    )


def _spec() -> SlideSpecInput:
    return SlideSpecInput(
        "slide-5:1",
        "sha256:" + "1" * 64,
        {"sem:implication-tag": "운영 원칙"},
    )


def test_unknown_cannot_erase_proven_issue_in_monotonic_join():
    issue = EvidenceAtom(
        "chrome_occlusion", "occlusion", "proven_issue", qid="q-target"
    )
    unknown = EvidenceAtom(
        "chrome_occlusion", "occlusion", "unknown", qid="q-target"
    )

    assert merge_evidence_atoms((issue,), (unknown,))[0].state == "proven_issue"
    assert merge_evidence_atoms((unknown,), (issue,))[0].state == "proven_issue"


def test_incomplete_count_dedupes_one_missing_occlusion_sample_across_consumers():
    chrome = EvidenceAtom(
        "chrome_occlusion",
        "occlusion",
        "unknown",
        qid="q-target",
        semantic_id="sem:target",
        reason_code="dom.occlusion.measurement_unavailable",
    )
    readability = EvidenceAtom(
        "slidespec_constraint",
        "readability",
        "unknown",
        stage="constraint_measurement",
        qid="q-target",
        semantic_id="sem:target",
        constraint_id="con:readability-target",
        reason_code="constraint.evidence.missing",
        diagnostics={
            "missing_measurements": {
                "lay:target": ["occluded_fraction"],
            },
            "target_qids": {"lay:target": "q-target"},
        },
    )

    evidence = DomEvidence(atoms=(chrome, readability))

    assert len(evidence.unknown_atoms) == 2
    assert evidence.incomplete_measurement_count == 1
    key, consumers = evidence.incomplete_measurement_groups[0]
    assert key == ("q-target", "occluded_fraction")
    assert {atom.sensor for atom in consumers} == {
        "chrome_occlusion",
        "slidespec_constraint",
    }
    payload = _incomplete_measurement_payload(evidence)
    assert payload == [
        {
            "scope": "q-target",
            "metric": "occluded_fraction",
            "state": "unknown",
            "sensors": ["chrome_occlusion", "slidespec_constraint"],
            "stages": ["constraint_measurement", "detection"],
            "qids": ["q-target"],
            "semantic_ids": ["sem:target"],
            "constraint_ids": ["con:readability-target"],
            "reason_codes": [
                "constraint.evidence.missing",
                "dom.occlusion.measurement_unavailable",
            ],
            "dependency_refs": [],
            "evidence_atom_count": 2,
        }
    ]


def test_exact_bundle_nested_findings_are_one_physical_issue_with_raw_atoms():
    evidence = _evidence()

    assert evidence.confirmed_issue_count == 1
    assert evidence.incomplete_measurement_count == 1
    assert evidence.clean_slide_count == 0
    assert len(evidence.defects) == 1
    assert len(evidence.defects[0].diagnostics["physical_overlap"]["raw_findings"]) == 3
    assert len(
        [
            atom
            for atom in evidence.atoms
            if atom.sensor == "chrome_dom" and atom.state == "proven_issue"
        ]
    ) == 3


def test_readability_constraints_alias_to_same_physical_overlap():
    overlap = _evidence()
    physical = overlap.defects[0]
    target_semantics = tuple(
        _qid_map()[qid]
        for qid in _fixture_contract()["physical_issue"]["raw_target_qids"]
    )
    constraint = DomEvidence(
        tuple(
            DomDefect(
                "constraint.readability.violated",
                "high",
                semantic_id,
            )
            for semantic_id in target_semantics
        )
    )

    merged = _merge_dom_evidence(overlap, constraint)
    payload = _confirmed_issue_payload(merged)

    assert merged.confirmed_issue_count == 1
    assert len(payload) == 1
    assert len(payload[0]["raw_defects"]) == 4


def test_unrelated_footer_unknown_does_not_block_detection_or_publication():
    baseline = _evidence()
    footer = _fixture_contract()["unrelated_unknown"]["qid"]
    assert {atom.qid for atom in baseline.unknown_atoms} == {footer}
    assert baseline.blocking_unknown_atoms(_plan()) == ()

    candidate = _dom_evidence(
        _raw_report(include_overlap=False),
        _qid_map(),
        browser_metrics=_metrics(footer_unknown=True, overlap=False),
    )
    events: list[str] = []
    result = asyncio.run(
        _pipeline(candidate, events).run(
            spec=_spec(), baseline_dom=baseline, rollout=_rollout()
        )
    )

    assert result.trigger == "geometry"
    assert result.confirmed_issue_count == 1
    assert result.incomplete_measurement_count == 1
    assert result.unresolved_confirmed_issue_count == 0
    assert events == ["publish"]


def test_target_unknown_blocks_publish_but_preserves_confirmed_remaining_issue():
    baseline = _evidence(footer_unknown=False, target_unknown=True)
    blockers = baseline.blocking_unknown_atoms(_plan())
    assert [atom.qid for atom in blockers] == [
        _fixture_contract()["physical_issue"]["raw_target_qids"][0]
    ]

    candidate = _dom_evidence(
        _raw_report(include_overlap=False),
        _qid_map(),
        browser_metrics=_metrics(footer_unknown=False, overlap=False),
    )
    events: list[str] = []
    result = asyncio.run(
        _pipeline(candidate, events).run(
            spec=_spec(), baseline_dom=baseline, rollout=_rollout()
        )
    )

    assert result.trigger == "geometry"
    assert result.confirmed_issue_count == 1
    assert result.unresolved_confirmed_issue_count == 1
    assert result.outcomes[0].reason_codes == (
        "dom.dependency_telemetry_incomplete",
    )
    assert result.outcomes[0].diagnostics["dom_telemetry"][
        "dependency_blockers"
    ][0]["metric"] == "occlusion"
    assert events == []


def test_moving_overlay_removes_same_target_and_intentional_overlap_is_not_inferred():
    baseline = _evidence(footer_unknown=False)
    post_render = _dom_evidence(
        _raw_report(include_overlap=False),
        _qid_map(),
        browser_metrics=_metrics(footer_unknown=False, overlap=False),
    )

    assert baseline.confirmed_issue_count == 1
    assert post_render.confirmed_issue_count == 0
    assert post_render.clean_slide_count == 1
    # No automatic exemption exists merely because authored markup overlaps.
    assert _evidence(footer_unknown=False).confirmed_issue_count == 1
