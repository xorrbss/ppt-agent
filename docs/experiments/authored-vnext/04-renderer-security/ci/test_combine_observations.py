import unittest

import combine_observations


class CombineObservationTests(unittest.TestCase):
    def test_electron_controls_are_required_and_strengthen_product_controls(self):
        container = {
            "schema_version": "authored-security-observation/v1",
            "run_id": "container",
            "execution": {"observer_errors": []},
            "containment": {"preload_disabled": False, "ipc_disabled": False},
        }
        electron = {
            "schema_version": "authored-electron-security-observation/v1",
            "run_id": "electron",
            "controls": {
                "preload_absent": True,
                "privileged_bridge_absent": True,
                "renderer_pid_observed": True,
            },
            "observer_errors": [],
        }
        combined = combine_observations.combine(container, electron)
        self.assertTrue(combined["containment"]["preload_disabled"])
        self.assertTrue(combined["containment"]["ipc_disabled"])
        self.assertTrue(combined["electron"]["renderer_pid_observed"])

    def test_missing_electron_controls_fail_closed(self):
        with self.assertRaises(combine_observations.ObservationError):
            combine_observations.combine(
                {
                    "schema_version": "authored-security-observation/v1",
                    "execution": {"observer_errors": []},
                    "containment": {},
                },
                {
                    "schema_version": "authored-electron-security-observation/v1",
                    "observer_errors": [],
                },
            )


if __name__ == "__main__":
    unittest.main()
