from pathlib import Path
import shutil
import tempfile
import unittest

from run_ios_voice_tests import ROOT, check_sources


class VoiceSourceContractTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        for relative in (
            "tests/ios_voice_packetizer_harness.rs", "src/client/voice_packetizer.rs",
            "src/client/ios_voice.rs",
        ):
            target = self.root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(ROOT / relative, target)

    def tearDown(self):
        self.temp.cleanup()

    def replace(self, relative, before, after):
        path = self.root / relative
        source = path.read_text(encoding="utf-8")
        self.assertIn(before, source)
        path.write_text(source.replace(before, after), encoding="utf-8")

    def test_current_contract_matches(self):
        check_sources(self.root)

    def test_harness_cannot_test_a_copied_packetizer(self):
        self.replace("tests/ios_voice_packetizer_harness.rs", "mod voice_packetizer;", "mod voice_packetizer;\nstruct VoicePacketizer;")
        with self.assertRaisesRegex(ValueError, "must not copy"):
            check_sources(self.root)

    def test_harness_cannot_include_a_different_implementation(self):
        self.replace("tests/ios_voice_packetizer_harness.rs", "../src/client/voice_packetizer.rs", "elsewhere.rs")
        with self.assertRaisesRegex(ValueError, "production packetizer"):
            check_sources(self.root)

    def test_realtime_callback_cannot_encode_or_send_over_network(self):
        self.replace("src/client/ios_voice.rs", "for chunk in data.chunks", "encoder.encode_float(data);\n                for chunk in data.chunks")
        with self.assertRaisesRegex(ValueError, "callback must only copy"):
            check_sources(self.root)

    def test_start_cannot_block_tokio_while_waiting_for_microphone(self):
        self.replace("src/client/ios_voice.rs", "tokio::time::timeout(STARTUP_TIMEOUT, ready_rx).await", "ready_rx.recv_timeout(STARTUP_TIMEOUT)")
        with self.assertRaisesRegex(ValueError, "blocking async startup"):
            check_sources(self.root)

    def test_no_server_audio_service_can_be_added_to_ios(self):
        self.replace("src/client/ios_voice.rs", "let host = cpal::default_host();", "let host = cpal::default_host();\n    audio_service::new();")
        with self.assertRaisesRegex(ValueError, "must not depend on a server"):
            check_sources(self.root)

    def test_teardown_cannot_omit_stream_drop(self):
        self.replace("src/client/ios_voice.rs", "drop(stream);", "// stream retained")
        with self.assertRaisesRegex(ValueError, "teardown"):
            check_sources(self.root)

    def test_readiness_cannot_precede_capture_and_encoding(self):
        self.replace("src/client/ios_voice.rs", "let mut output = [0u8; OPUS_PACKET_BYTES];", "let _ = ready.send(Ok(()));\n    let mut output = [0u8; OPUS_PACKET_BYTES];")
        with self.assertRaisesRegex(ValueError, "Ready requires real encoded PCM"):
            check_sources(self.root)

    def test_late_old_worker_cannot_emit_unscoped_messages(self):
        self.replace("src/client/ios_voice.rs", "Data::IosVoiceMessage(request_timestamp, message, counter.clone())", "Data::Message(message)")
        with self.assertRaisesRegex(ValueError, "scoped to their voice request token"):
            check_sources(self.root)

    def test_late_old_worker_cannot_close_a_new_call(self):
        self.replace("src/client/ios_voice.rs", "Data::IosVoiceFailed(request_timestamp, reason.clone())", "Data::CloseVoiceCall")
        with self.assertRaisesRegex(ValueError, "scoped to their voice request token"):
            check_sources(self.root)

    def test_harness_must_run_production_cancellation_controls(self):
        self.replace("tests/ios_voice_packetizer_harness.rs", "mod ios_voice;", "mod other_control;")
        with self.assertRaisesRegex(ValueError, "production cancellation controls"):
            check_sources(self.root)

    def test_scoped_worker_cannot_replace_its_original_request_token(self):
        self.replace("src/client/ios_voice.rs", "Data::IosVoiceMessage(request_timestamp, message, counter.clone())", "Data::IosVoiceMessage(0, message, counter.clone())")
        with self.assertRaisesRegex(ValueError, "original voice request token unchanged"):
            check_sources(self.root)

    def test_cancelled_worker_cannot_publish_late_ready_after_encoding(self):
        self.replace(
            "src/client/ios_voice.rs",
            "if stopped(stop, cancelled) || stream_failed.load(Ordering::Acquire) {",
            "if stream_failed.load(Ordering::Acquire) {",
        )
        with self.assertRaisesRegex(ValueError, "checked again after encoding"):
            check_sources(self.root)

    def test_encoded_network_backlog_cannot_become_unbounded(self):
        self.replace("src/client/ios_voice.rs", "const MAX_PENDING_VOICE_MESSAGES: usize = 12;", "const MAX_PENDING_VOICE_MESSAGES: usize = 1000;")
        with self.assertRaisesRegex(ValueError, "bounded to twelve"):
            check_sources(self.root)

    def test_send_failure_cannot_leak_pending_message_credit(self):
        self.replace("src/client/ios_voice.rs", "counter.fetch_sub(1, Ordering::Release);", "// missing credit rollback")
        with self.assertRaisesRegex(ValueError, "send failures must return"):
            check_sources(self.root)


if __name__ == "__main__":
    unittest.main()
