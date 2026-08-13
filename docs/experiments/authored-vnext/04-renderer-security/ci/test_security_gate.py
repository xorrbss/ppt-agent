import json
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

import baseline_adapter
import security_gate


HERE = Path(__file__).resolve().parent
FIXTURES = HERE / "fixtures"


class SecurityGateTests(unittest.TestCase):
    def evaluate(self, name: str):
        document = json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))
        return security_gate.evaluate_document(document, "fixture-digest")

    def test_pass_fixture_allows_promotion(self):
        result = self.evaluate("pass")
        self.assertEqual(result["status"], "PASS")
        self.assertTrue(result["promotion_allowed"])
        self.assertFalse(result["kill_switch"]["triggered"])
        self.assertEqual(security_gate.exit_code_for_status(result["status"]), 0)

    def test_fail_fixture_blocks_promotion_and_triggers_kill_switch(self):
        result = self.evaluate("fail")
        self.assertEqual(result["status"], "FAIL")
        self.assertFalse(result["promotion_allowed"])
        self.assertTrue(result["kill_switch"]["triggered"])
        self.assertEqual(security_gate.exit_code_for_status(result["status"]), 1)
        failed_ids = {check["id"] for check in result["checks"] if check["status"] == "FAIL"}
        self.assertEqual(failed_ids, {"boundary.loopback_egress"})

    def test_inconclusive_fixture_is_fail_closed(self):
        result = self.evaluate("inconclusive")
        self.assertEqual(result["status"], "INCONCLUSIVE")
        self.assertFalse(result["promotion_allowed"])
        self.assertTrue(result["kill_switch"]["triggered"])
        self.assertEqual(security_gate.exit_code_for_status(result["status"]), 2)
        inconclusive_ids = {
            check["id"] for check in result["checks"] if check["status"] == "INCONCLUSIVE"
        }
        self.assertEqual(inconclusive_ids, {"boundary.metadata_egress"})

    def test_machine_readable_outputs_are_parseable_and_do_not_copy_evidence(self):
        result = self.evaluate("pass")
        with tempfile.TemporaryDirectory(dir=HERE) as temporary:
            output_dir = Path(temporary)
            json_path, junit_path, sarif_path = security_gate.write_outputs(result, output_dir)
            parsed_json = json.loads(json_path.read_text(encoding="utf-8"))
            ET.parse(junit_path)
            parsed_sarif = json.loads(sarif_path.read_text(encoding="utf-8"))
            self.assertEqual(parsed_json["status"], "PASS")
            self.assertEqual(parsed_sarif["version"], "2.1.0")
            combined = json_path.read_text(encoding="utf-8") + sarif_path.read_text(encoding="utf-8")
            self.assertNotIn("deny-log:host-read", combined)

    def test_missing_evidence_is_inconclusive(self):
        document = json.loads((FIXTURES / "pass.json").read_text(encoding="utf-8"))
        document["boundaries"]["host_read"]["evidence"] = []
        result = security_gate.evaluate_document(document)
        self.assertEqual(result["status"], "INCONCLUSIVE")
        host_read = next(check for check in result["checks"] if check["id"] == "boundary.host_read")
        self.assertEqual(host_read["status"], "INCONCLUSIVE")

    def test_missing_electron_runtime_observation_is_inconclusive(self):
        document = json.loads((FIXTURES / "pass.json").read_text(encoding="utf-8"))
        del document["electron"]
        result = security_gate.evaluate_document(document)
        self.assertEqual(result["status"], "INCONCLUSIVE")
        electron_checks = [
            check for check in result["checks"] if check["category"] == "electron"
        ]
        self.assertTrue(electron_checks)
        self.assertTrue(all(check["status"] == "INCONCLUSIVE" for check in electron_checks))

    def test_missing_packaged_policy_provenance_is_inconclusive(self):
        document = json.loads((FIXTURES / "pass.json").read_text(encoding="utf-8"))
        del document["electron"]["compiled_policy_matches_build_provenance"]
        result = security_gate.evaluate_document(document)
        provenance = next(
            check
            for check in result["checks"]
            if check["id"] == "electron.compiled_policy_matches_build_provenance"
        )
        self.assertEqual(result["status"], "INCONCLUSIVE")
        self.assertEqual(provenance["status"], "INCONCLUSIVE")

    def test_current_baseline_adapter_preserves_failures_and_unknowns(self):
        fastapi = {
            "schema_version": "authored-renderer-baseline-v1",
            "observations": {
                "host_file_read": {"attempts": 1, "successful_reads": 1},
                "loopback_egress": {"attempts": 1, "successful_responses": 1},
            },
            "errors": [],
        }
        nextjs = {
            "schema_version": "authored-renderer-baseline-v1",
            "observations": {
                "host_file_read": {"attempts": 1, "successful_reads": 1},
                "loopback_egress": {"attempts": 0, "successful_responses": 0},
            },
        }
        observation = baseline_adapter.adapt(fastapi, nextjs)
        result = security_gate.evaluate_document(observation)
        failed_ids = {check["id"] for check in result["checks"] if check["status"] == "FAIL"}
        self.assertEqual(result["status"], "FAIL")
        self.assertEqual(failed_ids, {"boundary.host_read", "boundary.loopback_egress"})
        self.assertGreater(result["summary"]["inconclusive"], 0)


if __name__ == "__main__":
    unittest.main()
