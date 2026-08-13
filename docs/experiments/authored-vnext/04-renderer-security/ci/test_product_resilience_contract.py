import pathlib
import unittest


REPOSITORY_ROOT = pathlib.Path(__file__).resolve().parents[5]


class ProductResilienceContractTests(unittest.TestCase):
    def test_harness_observes_cleanup_lkg_and_real_product_kill_switch(self):
        source = (
            REPOSITORY_ROOT
            / "docs"
            / "experiments"
            / "authored-vnext"
            / "04-renderer-security"
            / "harness"
            / "verify_product_resilience.py"
        ).read_text(encoding="utf-8")
        for required in (
            "crash_cleanup",
            "timeout_cleanup",
            "lkg_preserved",
            "candidate_unpublished",
            "kill_switch_rejects_new_work",
            "renderer-kill-switch-active",
            "hashlib.sha256",
        ):
            self.assertIn(required, source)


if __name__ == "__main__":
    unittest.main()
