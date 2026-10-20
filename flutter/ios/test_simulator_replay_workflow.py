"""Replay wiring contracts, using only Python's standard library on clean Macs."""

import unittest
from pathlib import Path

class SimulatorReplayWorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = (Path(__file__).resolve().parents[2] / ".github/workflows/linkremote-ios-verify.yml").read_text(encoding="utf-8")
        cls.replay = cls.text.split("  ios-simulator-ui-replay:", 1)[1].split("  ios-unsigned:", 1)[0]
        cls.build = cls.text.split("  ios-unsigned:", 1)[1]

    def test_defaults_keep_both_families_and_diagnostics_off(self):
        inputs = self.text.split("permissions:", 1)[0]
        family = inputs.split("      replay_family:", 1)[1].split("      simulator_diagnostics:", 1)[0]
        self.assertIn("options: [both, iphone, ipad]", family)
        self.assertIn("default: both", family)
        self.assertIn("type: boolean\n        default: false", inputs.split("      simulator_diagnostics:", 1)[1])
        self.assertIn("inputs.replay_family == 'ipad'", self.replay)
        self.assertIn("'[\"ipad\"]'", self.replay)
        self.assertIn("'[\"iphone\",\"ipad\"]'", self.replay)

    def test_opt_in_is_replay_only_and_never_changes_app_arguments(self):
        self.assertIn("DIAGNOSTICS: ${{ inputs.simulator_diagnostics }}", self.replay)
        self.assertIn('if [[ "$DIAGNOSTICS" == true ]]', self.replay)
        self.assertIn("fresh_options+=(--diagnostics)", self.replay)
        self.assertIn('--app simulator-replay-app/Runner.app', self.replay)
        self.assertNotIn("--diagnostics", self.build)
        for name in ("test_simulator_diagnostics.py", "test_simulator_replay_workflow.py"):
            self.assertIn("-p '" + name + "' -v", self.replay)

    def test_original_archive_hash_source_and_extraction_gates_stay_mandatory(self):
        for option in ("--source-run", "--source-commit", "--archive-sha256", "--version", "--build"):
            self.assertIn(option, self.replay)
        self.assertIn("ditto -x -k", self.replay)
        self.assertIn("runs-on: macos-15-intel", self.replay)
        self.assertNotIn("continue-on-error", self.replay)

    def test_renderer_ab_is_replay_only_and_keeps_dispatch_within_ten_inputs(self):
        inputs = self.text.split("permissions:", 1)[0]
        import re
        self.assertEqual(len(re.findall(r"^      [a-z_0-9]+:$", inputs, re.MULTILINE)), 10)
        self.assertIn("simulator-renderer-ab", inputs)
        self.assertIn("inputs.build_target == 'simulator-renderer-ab'", self.replay)
        self.assertIn("renderer:", self.replay)
        self.assertIn("'[\"default\",\"skia\"]'", self.replay)
        self.assertIn("RENDERER: ${{ matrix.renderer }}", self.replay)
        self.assertIn('fresh_options+=(--renderer "$RENDERER" --stress-cycles 6 --fresh-devices)', self.replay)
        self.assertIn("inputs.build_target != 'simulator-renderer-ab'", self.build)
        self.assertIn("matrix.renderer", self.replay.split("      REPLAY_EVIDENCE:", 1)[1].split("    steps:", 1)[0])
        self.assertIn("test_verify_simulator_image.py", self.replay)
        self.assertNotIn("FLTEnableImpeller", self.replay)


if __name__ == "__main__":
    unittest.main()
