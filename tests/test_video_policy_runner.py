from pathlib import Path
import shutil
import tempfile
import unittest

from run_video_policy_tests import ROOT, check_sources, function_body


class PolicySourceContractTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        for relative in (
            "tests/video_qos_harness.rs", "src/client.rs",
            "libs/scrap/src/common/codec.rs", "libs/scrap/src/common/bitrate_policy.rs",
            "libs/hbb_common/protos/message.proto",
        ):
            target = self.root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(ROOT / relative, target)

    def tearDown(self):
        self.temp.cleanup()

    def replace_harness(self, before, after):
        target = self.root / "tests/video_qos_harness.rs"
        source = target.read_text(encoding="utf-8")
        self.assertIn(before, source)
        target.write_text(source.replace(before, after), encoding="utf-8")

    def test_current_contract_matches(self):
        check_sources(self.root)

    def test_whitespace_and_comments_do_not_change_function_contract(self):
        self.assertEqual(function_body("fn f(x: i32) -> i32 { x . clamp(10,120) // note\n }", "f"),
                         function_body("fn f(x:i32)->i32{x.clamp(10, 120)}", "f"))

    def test_legacy_sixty_fps_normalizer_is_detected(self):
        self.replace_harness("fps.clamp(10, 120)", "fps.clamp(10, 60)")
        with self.assertRaisesRegex(ValueError, "normalize_custom_fps"):
            check_sources(self.root)

    def test_quality_constant_drift_is_detected(self):
        self.replace_harness("BR_BALANCED: f32 = 0.67", "BR_BALANCED: f32 = 1.5")
        with self.assertRaisesRegex(ValueError, "BR_BALANCED"):
            check_sources(self.root)

    def test_copied_bitrate_implementation_is_rejected(self):
        self.replace_harness("pub mod codec {", "pub mod codec {\n pub fn fps_bitrate_scale(_: u32) -> f32 { 1.45 }")
        with self.assertRaisesRegex(ValueError, "reuse production"):
            check_sources(self.root)

    def test_quality_enum_drift_is_detected(self):
        self.replace_harness("Self::Best => 4", "Self::Best => 3")
        with self.assertRaisesRegex(ValueError, "ImageQuality::Best"):
            check_sources(self.root)


if __name__ == "__main__":
    unittest.main()
