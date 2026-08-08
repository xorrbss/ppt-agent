from __future__ import annotations

import importlib.util
from pathlib import Path
from types import SimpleNamespace
import unittest

from services.slidespec_repair_pipeline import DomDefect, DomEvidence, EvidenceAtom


FASTAPI_ROOT = Path(__file__).parents[2]
SCRIPT_PATH = FASTAPI_ROOT / "scripts" / "slidespec_active_bundle_replayer.py"
FIXTURE_PATH = FASTAPI_ROOT / "tests" / "fixtures" / "slidespec_active_bundle_a6470d97.json"

spec = importlib.util.spec_from_file_location("slidespec_active_bundle_replayer", SCRIPT_PATH)
assert spec and spec.loader
replayer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(replayer)


class ActiveBundleFixtureReplayerTest(unittest.TestCase):
    def test_fixture_pins_exact_active_html_and_all_scoped_evidence_cases(self) -> None:
        fixture = replayer.load_fixture(FIXTURE_PATH)
        replay = replayer.verify_fixture(fixture, base_directory=FIXTURE_PATH.parent)

        self.assertEqual(replay["presentation_id"], "50907f60-ea13-404c-90da-57f821404d74")
        self.assertEqual(replay["slide_index"], 4)
        self.assertEqual(replay["expected_deduplicated_issue_count"], 1)
        self.assertEqual(replay["raw_target_qids"], [
            "q-div-63a4d3c2e6c7",
            "q-div-655134fc7990",
            "q-strong-f213b54d7db8",
        ])
        self.assertEqual(replay["unrelated_unknown_qid"], "q-div-c6f2820b2605")
        self.assertEqual(replay["fresh_v4_footer_state"], "proven_clean")
        self.assertEqual(len(replay["scenarios"]), 5)

    def test_scoped_evidence_contract_preserves_detection_and_blocks_only_dependency_unknowns(self) -> None:
        target_qids = (
            "q-div-63a4d3c2e6c7",
            "q-div-655134fc7990",
            "q-strong-f213b54d7db8",
        )
        physical_issue = "bottom-implication-band-occludes-content"
        evidence = DomEvidence(
            defects=tuple(
                DomDefect(
                    "dom.unintended_overlap",
                    "high",
                    f"semantic:{qid}",
                    qid=qid,
                    physical_issue_id=physical_issue,
                    diagnostics={"intentional": True},
                )
                for qid in target_qids
            ),
            atoms=(
                EvidenceAtom(
                    sensor="chrome_occlusion",
                    metric="occluded_fraction",
                    stage="chrome_occlusion",
                    state="unknown",
                    qid="q-div-c6f2820b2605",
                    reason_code="no_sample_on_owned_paint",
                ),
            ),
        )

        # Three nested raw reports are one physical issue; an unrelated footer
        # gap is incomplete evidence but cannot erase the confirmed overlap.
        self.assertEqual(evidence.confirmed_issue_count, 1)
        self.assertEqual(evidence.incomplete_measurement_count, 1)
        self.assertEqual(evidence.clean_slide_count, 0)
        self.assertEqual(evidence.blocking_unknown_atoms(SimpleNamespace(
            scope=(), diagnostic_refs=("qid:q-div-655134fc7990",), operations=(),
        )), ())

        target_unknown = EvidenceAtom(
            sensor="chrome_occlusion",
            metric="occluded_fraction",
            stage="publish_safety",
            state="unknown",
            qid="q-div-655134fc7990",
            dependency_refs=("constraint:implication-band",),
            reason_code="target_dependency_unknown",
        )
        blocked = DomEvidence(defects=evidence.defects, atoms=(*evidence.atoms, target_unknown))
        plan = SimpleNamespace(
            scope=(),
            diagnostic_refs=("qid:q-div-655134fc7990", "constraint:implication-band"),
            operations=(),
        )
        self.assertEqual(blocked.confirmed_issue_count, 1)
        self.assertEqual([atom.reason_code for atom in blocked.blocking_unknown_atoms(plan)], [
            "target_dependency_unknown",
        ])

        # Removing/moving the overlay is only clean after post-render evidence
        # has no defect; an "intentional" diagnostic does not auto-exempt it.
        self.assertEqual(DomEvidence().confirmed_issue_count, 0)
        self.assertEqual(evidence.confirmed_issue_count, 1)
