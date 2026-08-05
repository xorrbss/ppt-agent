"""Contract tests for the read-only human-label replay adapter."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
import asyncio
import tempfile
import unittest


FASTAPI_ROOT = Path(__file__).parents[2]
SCRIPT_PATH = FASTAPI_ROOT / "scripts" / "slidespec_human_labeled_replayer.py"
FIXTURE_PATH = FASTAPI_ROOT / "tests" / "fixtures" / "slidespec_human_labeled_replay.json"
SOURCE_CORPUS_PATH = FASTAPI_ROOT / "tests" / "fixtures" / "slidespec_golden_corpus.json"

spec = importlib.util.spec_from_file_location("slidespec_human_labeled_replayer", SCRIPT_PATH)
assert spec and spec.loader
replayer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(replayer)


def _fixture() -> dict:
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


def _canonical_evidence(payload: dict) -> dict:
    results = []
    for record in payload["records"]:
        if record["label"]["disposition"] != "true_positive":
            continue
        target_evidence = [
            {"qid": qid, "constraint_id": f"dom.target:{qid}"}
            for qid in replayer._required_target_qids(record)
        ]
        results.append({
            "slide_index": record["slide_number"] - 1,
            "candidate_count": 1,
            "accepted": True,
            "applied": True,
            "publish_status": "active",
            "post_verify_status": "passed",
            "remaining_issues": [],
            "target_evidence": target_evidence,
        })
    return {
        "schema": "slidespec-canonical-executor-evidence/v1",
        "slide_results": results,
    }


class HumanLabeledReplayerContractTest(unittest.TestCase):
    def test_source_digest_excludes_active_pointer_properties(self) -> None:
        baseline = SimpleNamespace(
            layout_group="authored",
            layout="html",
            index=8,
            content={"stable": True},
            html_content="<main>source</main>",
            ui=None,
            speaker_note="note",
            properties={"slidespec_active": {"artifact": "before"}},
        )
        published = SimpleNamespace(
            **{
                **baseline.__dict__,
                "properties": {"slidespec_active": {"artifact": "after"}},
            }
        )
        changed_source = SimpleNamespace(
            **{
                **baseline.__dict__,
                "html_content": "<main>changed</main>",
            }
        )

        self.assertEqual(
            replayer._slide_source_digest(baseline),
            replayer._slide_source_digest(published),
        )
        self.assertNotEqual(
            replayer._slide_source_digest(baseline),
            replayer._slide_source_digest(changed_source),
        )

    def test_postverify_tracks_new_defect_family_on_required_qid(self) -> None:
        record = next(
            item
            for item in _fixture()["records"]
            if item["slide_number"] == 9
        )
        finding = {
            "category": "clipping",
            "severity": "medium",
            "reason": "changed after a partial repair",
            "targets": [{"qid": record["detector_target"]["qid"], "bbox": {}}],
        }

        self.assertTrue(
            replayer._finding_mentions_required_target(finding, record, [])
        )

    def test_postverify_ignores_preexisting_unrelated_target_warning(self) -> None:
        record = next(
            item for item in _fixture()["records"] if item["slide_number"] == 19
        )
        contrast = {
            "category": "contrast",
            "severity": "medium",
            "reason": "pre-existing authored footer contrast",
            "targets": [{"qid": record["detector_target"]["qid"], "bbox": {}}],
        }

        self.assertFalse(
            replayer._finding_mentions_required_target(
                contrast, record, [contrast]
            )
        )

    def test_fixture_requires_canonical_executor_evidence_for_each_true_positive(self) -> None:
        payload = _fixture()
        replayer.validate_fixture(payload, json.loads(SOURCE_CORPUS_PATH.read_text(encoding="utf-8")))

        replay = replayer.fixture_replay(payload)

        self.assertEqual(replay["metrics"], {
            "corrected_false_positives": 18,
            "true_positive_executor_verified": 0,
            "true_positive_pending": 5,
            "measurement_status": "fixture_contract_only",
        })
        self.assertEqual(replay["fixed_count"], 0)
        self.assertEqual(replay["remaining_issue_count"], 5)
        self.assertTrue(all(
            slide["executor_evidence_status"] == "unresolved_executor_evidence"
            for slide in replay["slide_results"]
            if not slide["corrected_false_positive"]
        ))

    def test_fixture_replay_accepts_only_complete_per_target_executor_evidence(self) -> None:
        payload = _fixture()

        replay = replayer.fixture_replay(payload, _canonical_evidence(payload))

        self.assertEqual(replay["executor_evidence"]["status"], "validated")
        self.assertEqual(replay["metrics"]["true_positive_executor_verified"], 5)
        self.assertEqual(replay["metrics"]["true_positive_pending"], 0)
        self.assertEqual(replay["fixed_count"], 5)
        self.assertEqual(replay["remaining_issue_count"], 0)
        self.assertTrue(all(
            slide["applied"] and slide["post_verify_status"] == "passed"
            for slide in replay["slide_results"]
            if not slide["corrected_false_positive"]
        ))

    def test_authorized_execution_reimports_full_deck_and_executes_only_true_positives(self) -> None:
        payload = _fixture()
        calls: dict[str, object] = {}
        slides = [SimpleNamespace(index=index, properties={"slidespec_active": {}}) for index in range(90)]

        async def ensure_imports(**kwargs):
            calls["import_positions"] = kwargs["positions"]
            return SimpleNamespace(slides=slides, failures={})

        async def execute_review(**kwargs):
            calls["execution_positions"] = kwargs["positions"]
            calls["dry_run"] = kwargs["dry_run"]
            results = []
            for record in payload["records"]:
                if record["label"]["disposition"] != "true_positive":
                    continue
                qids = replayer._required_target_qids(record)
                results.append({
                    "slide_index": record["slide_number"] - 1,
                    "candidate_count": 1,
                    "accepted": True,
                    "applied": True,
                    "publish_status": "active",
                    "post_verify_status": "passed",
                    "remaining_issues": [],
                    "target_evidence": [
                        {"qid": qid, "constraint_id": f"constraint:{qid}"}
                        for qid in qids
                    ],
                })
            return SimpleNamespace(
                claimed_positions=tuple(item["slide_index"] for item in results),
                data={"slide_results": results},
            )

        async def load_active_bundle(_session, _slide):
            return SimpleNamespace(repair_plan={}, manifest={}, spec={})

        with tempfile.TemporaryDirectory() as directory:
            evidence = asyncio.run(replayer._execute_true_positive_repairs(
                payload=payload,
                presentation_id=__import__("uuid").uuid4(),
                presentation=SimpleNamespace(owner_id="owner"),
                slides=slides,
                session=object(),
                output_dir=Path(directory),
                dependencies={
                    "active_pointer_key": "slidespec_active",
                    "ensure_imports": ensure_imports,
                    "execute_review": execute_review,
                    "load_active_bundle": load_active_bundle,
                },
            ))
            persisted = json.loads((Path(directory) / "canonical-executor-evidence.json").read_text(encoding="utf-8"))

        self.assertEqual(calls["import_positions"], tuple(range(90)))
        self.assertEqual(calls["execution_positions"], (8, 18, 25, 46, 71))
        self.assertFalse(calls["dry_run"])
        self.assertEqual(persisted, evidence)
        self.assertEqual(replayer.fixture_replay(payload, evidence)["fixed_count"], 5)

    def test_executor_evidence_completes_missing_overlap_endpoint_from_published_plan(self) -> None:
        record = next(
            item for item in _fixture()["records"] if item["slide_number"] == 47
        )
        required_qids = replayer._required_target_qids(record)
        self.assertEqual(len(required_qids), 2)
        primary_qid, peer_qid = required_qids
        primary_layout = "lay:primary"
        peer_layout = "lay:peer"
        bundle = SimpleNamespace(
            repair_plan={
                "diagnosticRefs": [f"qid:{primary_qid}", f"qid:{peer_qid}"]
            },
            manifest={
                "source": {
                    "bindings": [
                        {"qid": primary_qid, "layoutId": primary_layout},
                        {"qid": peer_qid, "layoutId": peer_layout},
                    ]
                }
            },
            spec={
                "constraints": [
                    {
                        "constraintId": "con:physical-overlap",
                        "kind": "nonOverlap",
                        "first": primary_layout,
                        "second": peer_layout,
                    }
                ]
            },
        )

        async def load_active_bundle(_session, _slide):
            return bundle

        execution_data = {
            "slide_results": [
                {
                    "slide_index": 46,
                    "candidate_count": 1,
                    "accepted": True,
                    "applied": True,
                    "publish_status": "active",
                    "post_verify_status": "passed",
                    "remaining_issues": [],
                    "target_evidence": [
                        {
                            "qid": primary_qid,
                            "constraint_id": "con:physical-overlap",
                        }
                    ],
                }
            ]
        }
        evidence = asyncio.run(
            replayer._canonical_executor_evidence_from_execution(
                payload={"records": [record]},
                slides=[SimpleNamespace() for _ in range(47)],
                session=object(),
                execution_data=execution_data,
                load_active_bundle=load_active_bundle,
            )
        )

        self.assertEqual(
            evidence["slide_results"][0]["target_evidence"],
            [
                {"qid": primary_qid, "constraint_id": "con:physical-overlap"},
                {"qid": peer_qid, "constraint_id": "con:physical-overlap"},
            ],
        )

    def test_authorized_execution_fails_with_position_stage_and_reason(self) -> None:
        payload = _fixture()
        slides = [SimpleNamespace(index=index, properties={"slidespec_active": {}}) for index in range(90)]
        execute_called = False

        async def ensure_imports(**_kwargs):
            return SimpleNamespace(slides=slides, failures={25: "import.publish.failed"})

        async def execute_review(**_kwargs):
            nonlocal execute_called
            execute_called = True
            raise AssertionError("must not execute after a reimport failure")

        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(replayer.ReplayExecutionFailure) as caught:
                asyncio.run(replayer._execute_true_positive_repairs(
                    payload=payload,
                    presentation_id=__import__("uuid").uuid4(),
                    presentation=SimpleNamespace(owner_id="owner"),
                    slides=slides,
                    session=object(),
                    output_dir=Path(directory),
                    dependencies={
                        "active_pointer_key": "slidespec_active",
                        "ensure_imports": ensure_imports,
                        "execute_review": execute_review,
                        "load_active_bundle": None,
                    },
                ))

        self.assertEqual(caught.exception.position, 25)
        self.assertEqual(caught.exception.stage, "reimport_v4")
        self.assertEqual(caught.exception.reason, "import.publish.failed")
        self.assertFalse(execute_called)
