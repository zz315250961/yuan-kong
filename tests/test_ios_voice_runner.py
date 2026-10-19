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
            "src/ui_session_interface.rs",
            "src/client/io_loop.rs",
            "src/flutter.rs",
            "flutter/lib/models/model.dart", "flutter/lib/models/chat_model.dart",
            "flutter/lib/utils/ios_voice_call.dart",
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

    def test_session_voice_stop_cannot_wait_for_network_close_queue(self):
        self.replace(
            "src/ui_session_interface.rs",
            "crate::client::ios_voice::cancel_owner(Arc::as_ptr(&self.sender) as usize);",
            "// microphone only stopped after queued Close",
        )
        with self.assertRaisesRegex(ValueError, "synchronously before queued"):
            check_sources(self.root)

    def test_ui_cannot_send_a_voice_request_without_arming_owner_epoch(self):
        self.replace("src/ui_session_interface.rs", "Data::IosNewVoiceCall(epoch)", "Data::NewVoiceCall")
        with self.assertRaisesRegex(ValueError, "arm an owner epoch"):
            check_sources(self.root)

    def test_old_worker_cannot_remove_new_owner_registration(self):
        self.replace("src/client/ios_voice.rs", "state.epoch == self.epoch && Weak::ptr_eq", "Weak::ptr_eq")
        with self.assertRaisesRegex(ValueError, "own owner epoch"):
            check_sources(self.root)

    def test_cancelled_owner_epoch_cannot_later_start_recording(self):
        self.replace("src/client/ios_voice.rs", "state.epoch != epoch", "false")
        with self.assertRaisesRegex(ValueError, "invalidate late remote accepts"):
            check_sources(self.root)

    def test_owner_registration_cannot_be_delayed_until_future_poll(self):
        self.replace(
            "src/client/ios_voice.rs",
            "let registration = register_owner(owner, epoch, &cancelled);",
            "let registration = None; // too late to cancel before first poll",
        )
        with self.assertRaisesRegex(ValueError, "register eagerly before future polling"):
            check_sources(self.root)

    def test_io_events_cannot_match_only_timestamp_after_synchronous_cancel(self):
        self.replace("src/client/io_loop.rs", "super::ios_voice::is_owner_current(owner, epoch)", "true")
        with self.assertRaisesRegex(ValueError, "current owner epoch"):
            check_sources(self.root)

    def test_ready_success_cannot_attach_after_owner_was_cancelled(self):
        self.replace("src/client/io_loop.rs", "if !self.ios_voice_event_is_current(timestamp) {", "if false {")
        with self.assertRaisesRegex(ValueError, "cancelled Ready success"):
            check_sources(self.root)

    def test_ready_error_cleanup_cannot_depend_on_worker_still_being_armed(self):
        self.replace("src/client/io_loop.rs", "Err(reason) => {\n                        self.stop_voice_call();", "Err(reason) => {\n                        if !self.ios_voice_event_is_current(timestamp) { return true; }\n                        self.stop_voice_call();")
        with self.assertRaisesRegex(ValueError, "Ready errors must clear"):
            check_sources(self.root)

    def test_worker_error_cleanup_cannot_depend_on_worker_still_being_armed(self):
        self.replace("src/client/io_loop.rs", "if self.ios_voice_timestamp == Some(timestamp) {", "if self.ios_voice_event_is_current(timestamp) {")
        with self.assertRaisesRegex(ValueError, "Worker failure cleanup"):
            check_sources(self.root)

    def test_cancelled_message_cannot_use_timestamp_alone(self):
        self.replace("src/client/io_loop.rs", "if self.ios_voice_event_is_current(timestamp) {\n                    allow_err!", "if self.ios_voice_timestamp == Some(timestamp) {\n                    allow_err!")
        with self.assertRaisesRegex(ValueError, "always return their pending credit"):
            check_sources(self.root)

    def test_ignored_message_cannot_skip_its_credit_refund(self):
        self.replace("src/client/io_loop.rs", "pending.fetch_sub(1, Ordering::Release);", "// no credit refund")
        with self.assertRaisesRegex(ValueError, "always return their pending credit"):
            check_sources(self.root)

    def test_stopping_cannot_leave_old_owner_epoch_attached(self):
        self.replace("src/client/io_loop.rs", "self.ios_voice_epoch = None;", "// stale epoch retained")
        with self.assertRaisesRegex(ValueError, "clear pending, starting and active"):
            check_sources(self.root)

    def test_queued_close_cannot_send_a_second_idle_closed_event(self):
        self.replace("src/client/io_loop.rs", "if had_voice_call {", "if true {")
        with self.assertRaisesRegex(ValueError, "idle queued close"):
            check_sources(self.root)

    def test_remote_close_cannot_send_a_second_idle_closed_event(self):
        self.replace("src/client/io_loop.rs", "if self.stop_voice_call() {", "if true {")
        with self.assertRaisesRegex(ValueError, "idle repeated remote close"):
            check_sources(self.root)

    def test_refused_request_cannot_leave_stale_voice_state(self):
        self.replace("src/client/io_loop.rs", "// The peer refused the voice call.\n                                self.stop_voice_call();", "// The peer refused the voice call.")
        with self.assertRaisesRegex(ValueError, "Refused requests must clear"):
            check_sources(self.root)

    def test_close_ack_cannot_be_missing_for_an_idle_close(self):
        self.replace("src/client/io_loop.rs", "self.handler.on_voice_call_close_ack();", "if had_voice_call { self.handler.on_voice_call_close_ack(); }")
        with self.assertRaisesRegex(ValueError, "always follow local stop"):
            check_sources(self.root)

    def test_close_ack_cannot_wait_for_a_blocked_network_write(self):
        self.replace("src/client/io_loop.rs", "self.handler.on_voice_call_close_ack();\n                allow_err!(peer.send(&msg).await);", "allow_err!(peer.send(&msg).await);\n                self.handler.on_voice_call_close_ack();")
        with self.assertRaisesRegex(ValueError, "precede network await"):
            check_sources(self.root)

    def test_close_ack_must_not_reuse_the_closed_event_name(self):
        self.replace("src/flutter.rs", 'self.push_event::<&str>("on_voice_call_close_ack", &[], &[]);', 'self.push_event::<&str>("on_voice_call_closed", &[], &[]);')
        with self.assertRaisesRegex(ValueError, "same Flutter session stream"):
            check_sources(self.root)

    def test_model_must_dispatch_the_close_ack_to_chat_barrier(self):
        self.replace("flutter/lib/models/model.dart", "parent.target?.chatModel.onVoiceCallCloseAck();", "parent.target?.chatModel.onVoiceCallClosed('');")
        with self.assertRaisesRegex(ValueError, "dispatch must synchronously"):
            check_sources(self.root)

    def test_chat_close_cannot_finish_after_enqueue_without_real_ack(self):
        self.replace("flutter/lib/models/chat_model.dart", "if (pending != null && !_voiceDisposed) await pending.future;", "// FFI enqueue only")
        with self.assertRaisesRegex(ValueError, "await the real ack"):
            check_sources(self.root)

    def test_chat_cannot_register_ack_after_close_was_enqueued(self):
        self.replace("flutter/lib/models/chat_model.dart", "_iosVoiceCloseAck = pending;", "// barrier registered too late")
        with self.assertRaisesRegex(ValueError, "register its close barrier before FFI"):
            check_sources(self.root)

    def test_coordinator_cannot_wait_for_ack_before_native_audio_end(self):
        self.replace("flutter/lib/utils/ios_voice_call.dart", "if (key != null) await _endNative(key);", "if (closing != null) await closing;\n      if (key != null) await _endNative(key);")
        with self.assertRaisesRegex(ValueError, "Native audio end must not wait"):
            check_sources(self.root)

    def test_coordinator_cannot_allow_redial_while_close_ack_is_pending(self):
        self.replace("flutter/lib/utils/ios_voice_call.dart", "_disposed || _requesting || _active || _stopping", "_disposed || _requesting || _active")
        with self.assertRaisesRegex(ValueError, "redial must remain fenced"):
            check_sources(self.root)


if __name__ == "__main__":
    unittest.main()
