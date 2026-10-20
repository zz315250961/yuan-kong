from pathlib import Path
import shutil
import tempfile
import unittest

from run_ios_duplex_tests import ROOT, MODULE, HARNESS, check_sources, manifest


class DuplexRunnerContractTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        for relative in (MODULE, HARNESS):
            target = self.root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(ROOT / relative, target)

    def tearDown(self):
        self.temporary.cleanup()

    def replace(self, relative, before, after):
        target = self.root / relative
        source = target.read_text(encoding="utf-8")
        self.assertIn(before, source)
        target.write_text(source.replace(before, after), encoding="utf-8")

    def test_current_source_contract(self):
        check_sources(self.root)

    def test_harness_cannot_copy_the_production_queue(self):
        self.replace(HARNESS, "mod ios_duplex;", "mod ios_duplex;\nstruct Playback;")
        with self.assertRaisesRegex(ValueError, "must not copy or mock"):
            check_sources(self.root)

    def test_harness_cannot_include_a_different_module(self):
        self.replace(HARNESS, "../src/client/ios_duplex.rs", "not-production.rs")
        with self.assertRaisesRegex(ValueError, "production module directly"):
            check_sources(self.root)

    def test_unprocessed_fallback_is_rejected(self):
        self.replace(MODULE, "sys::kAudioUnitSubType_VoiceProcessingIO,", "RemoteIO,")
        with self.assertRaisesRegex(ValueError, "must not fall back"):
            check_sources(self.root)

    def test_independent_capture_audio_unit_is_rejected(self):
        self.replace(MODULE, "let description =", "let _ = sys::AudioComponentInstanceNew(ptr::null_mut(), ptr::null_mut());\nlet description =")
        with self.assertRaisesRegex(ValueError, "share one AudioUnit"):
            check_sources(self.root)

    def test_bypassing_voice_processing_is_rejected(self):
        self.replace(MODULE, "sys::kAUVoiceIOProperty_BypassVoiceProcessing, sys::kAudioUnitScope_Global, 0, &0u32", "sys::kAUVoiceIOProperty_BypassVoiceProcessing, sys::kAudioUnitScope_Global, 0, &1u32")
        with self.assertRaisesRegex(ValueError, "processed duplex"):
            check_sources(self.root)

    def test_capture_callback_cannot_allocate(self):
        self.replace(MODULE, "let result = sys::AudioUnitRender", "let _scratch = Vec::new();\nlet result = sys::AudioUnitRender")
        with self.assertRaisesRegex(ValueError, "must not allocate"):
            check_sources(self.root)

    def test_render_callback_cannot_lock_or_sleep(self):
        self.replace(MODULE, "playback.render(output);", "Mutex::new(()); std::thread::sleep(duration);\nplayback.render(output);")
        with self.assertRaisesRegex(ValueError, "must not allocate"):
            check_sources(self.root)

    def test_processing_readback_cannot_be_removed(self):
        self.replace(MODULE, "native.verify_processing()?;", "// missing readback")
        with self.assertRaisesRegex(ValueError, "read back"):
            check_sources(self.root)

    def test_capture_error_must_clear_scratch(self):
        self.replace(MODULE, "context.scratch[..frames as usize].fill(0.0);", "// captured PCM retained")
        with self.assertRaisesRegex(ValueError, "discard temporary"):
            check_sources(self.root)

    def test_end_before_bind_requires_tombstone(self):
        self.replace(MODULE, "self.latest = self.latest.max(epoch);", "// lost tombstone")
        with self.assertRaisesRegex(ValueError, "epoch tombstone"):
            check_sources(self.root)

    def test_manifest_points_at_real_harness_and_locked_queue_version(self):
        source = manifest(self.root)
        self.assertIn((self.root / HARNESS).resolve().as_posix(), source)
        self.assertIn('crossbeam-queue = "=0.3.11"', source)
        self.assertIn('version = "=0.11.3"', source)
        self.assertNotIn("cpal", source)


if __name__ == "__main__":
    unittest.main()
