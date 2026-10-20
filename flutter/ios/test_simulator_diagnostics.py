"""No Mac or Simulator is modified by these collector contract tests."""

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from simulator_diagnostics import SimulatorDiagnostics


class SimulatorDiagnosticsTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.collector = SimulatorDiagnostics(self.directory.name)
        self.addCleanup(self.collector.close)

    def test_simctl_trace_has_separate_timestamped_start_end_and_exit_code(self):
        identifier = self.collector.command_started(("bootstatus", "owned", "-b"), 180)
        self.collector.command_finished(identifier, 1.25, 0)
        events = [json.loads(line) for line in
                  (self.collector.output / "events.jsonl").read_text().splitlines()]
        self.assertEqual([event["event"] for event in events], ["simctl_start", "simctl_end"])
        self.assertEqual(events[0]["timeout_seconds"], 180)
        self.assertEqual(events[1]["id"], identifier)
        self.assertEqual(events[1]["exit_code"], 0)
        self.assertTrue(all(event["time_utc"] for event in events))

    def test_diagnostic_timeout_kills_only_owned_probe_and_reaps_it(self):
        process = Mock(returncode=-9)
        process.communicate.side_effect = [subprocess.TimeoutExpired("ps", 5), ("partial", None)]
        with patch("simulator_diagnostics.subprocess.Popen", return_value=process):
            self.assertEqual(self.collector._run(["ps", "-axo", "pid,comm"]), "partial")
        process.kill.assert_called_once_with()
        self.assertEqual(process.communicate.call_args_list[0].kwargs["timeout"], 5)
        self.assertEqual(process.communicate.call_args_list[1].kwargs["timeout"], 2)
        self.assertFalse(self.collector._processes)
        self.assertTrue(any("timeout" in item["action"] for item in self.collector.errors))

    def test_second_communicate_timeout_keeps_live_owned_probe_for_cleanup(self):
        process = Mock(pid=42, returncode=None)
        process.poll.return_value = None
        process.communicate.side_effect = subprocess.TimeoutExpired("ps", 5)
        with patch("simulator_diagnostics.subprocess.Popen", return_value=process):
            self.collector._run(["ps", "-axo", "pid,comm"])
        self.assertIn(process, self.collector._processes)
        self.collector.stop_device()
        self.assertIn(process, self.collector._processes)
        self.assertFalse(self.collector._usable)
        self.assertTrue(any("still alive" in item["error"] for item in self.collector.errors))
        process.poll.return_value = 0

    def test_log_streams_have_deadlines_and_owned_cleanup_escalates_boundedly(self):
        process = Mock(pid=42, returncode=-9)
        process.poll.side_effect = [None, 0]
        process.wait.side_effect = [subprocess.TimeoutExpired("collector", 2), 0]
        timer = Mock()
        self.collector.udid, self.collector.family = "owned-udid", "ipad"
        with patch("simulator_diagnostics.subprocess.Popen", return_value=process) as popen, \
                patch("simulator_diagnostics.threading.Timer", return_value=timer) as make_timer:
            self.collector.start_logs()
        self.assertEqual(make_timer.call_args.args[0], 1200)
        self.assertEqual(popen.call_args_list[0].args[0][:4],
                         ["xcrun", "simctl", "spawn", "owned-udid"])
        host_predicate = popen.call_args_list[1].args[0][-1]
        self.assertIn('process == "CoreSimulatorService"', host_predicate)
        self.assertIn('process == "com.apple.CoreSimulator.CoreSimulatorService"', host_predicate)
        self.collector.stop_device()
        process.terminate.assert_called_once_with()
        process.kill.assert_called_once_with()
        self.assertTrue(all(call.kwargs["timeout"] == 2 for call in process.wait.call_args_list))
        self.assertEqual(timer.cancel.call_count, 2)
        self.assertFalse(self.collector._processes)

    def test_resume_probe_waits_25_seconds_and_cancellation_prevents_sampling(self):
        for cancelled in (False, True):
            with self.subTest(cancelled=cancelled):
                event = Mock()
                event.wait.return_value = cancelled
                thread = Mock()
                with patch("simulator_diagnostics.threading.Event", return_value=event), \
                        patch("simulator_diagnostics.threading.Thread", return_value=thread) as make_thread, \
                        patch.object(self.collector, "snapshot") as snapshot:
                    self.collector.begin_resume()
                    make_thread.call_args.kwargs["target"]()
                    event.wait.assert_called_once_with(25)
                    if cancelled:
                        snapshot.assert_not_called()
                    else:
                        snapshot.assert_called_once_with("resume-wait-25s", sample=True)
                    self.collector.end_resume()
                    event.set.assert_called_once_with()

    def test_samples_prioritize_original_app_pid_and_only_target_selected_springboard(self):
        self.collector.family, self.collector.udid = "ipad", "owned-udid"
        self.collector.launched("top.zperme.linkremote: 70458\n")
        commands = []

        def command(arguments, timeout=5):
            commands.append((arguments, timeout))
            if arguments[0] == "ps":
                return "11 1 S 0 100 /host/CoreSimulatorService\n99 1 S 0 30 /private/OtherApp\n"
            if arguments[:3] == ["xcrun", "simctl", "spawn"]:
                return "123\t0\tcom.apple.SpringBoard\n"
            return "evidence"

        with patch.object(self.collector, "_run", side_effect=command):
            self.collector.snapshot("failure", sample=True)
        samples = [arguments for arguments, _ in commands if arguments[0] == "sample"]
        self.assertEqual([arguments[:3] for arguments in samples],
                         [["sample", "70458", "3"], ["sample", "123", "3"], ["sample", "11", "3"]])
        for arguments in samples:
            self.assertEqual(arguments[3], "-file")
            self.assertEqual(Path(arguments[4]).parent, self.collector.output)
            self.assertNotIn("command", Path(arguments[4]).name)
        self.assertTrue(all(timeout <= 5 for _, timeout in commands))
        saved = (self.collector.output / "ipad-failure-processes.txt").read_text()
        self.assertNotIn("OtherApp", saved)
        self.assertTrue(any(arguments[:4] == ["xcrun", "simctl", "spawn", "owned-udid"]
                            for arguments, _ in commands))

    def test_close_cancels_workers_and_cannot_start_late_probe(self):
        cancel, resources, thread = Mock(), Mock(), Mock()
        thread.is_alive.return_value = False
        self.collector._resume_cancel = cancel
        self.collector._resource_stop = resources
        self.collector._threads.append(thread)
        self.collector.close()
        cancel.set.assert_called_once_with()
        resources.set.assert_called_once_with()
        thread.join.assert_called_once_with(timeout=3)
        with patch("simulator_diagnostics.subprocess.Popen") as popen:
            self.assertEqual(self.collector._run(["sample", "123", "3"]), "")
        popen.assert_not_called()

    def test_probe_failure_is_separate_diagnostic_error(self):
        self.collector.family = "ipad"
        with patch.object(self.collector, "_run", side_effect=OSError("sample unavailable")):
            self.collector.snapshot("failure", sample=True)
        self.assertEqual(self.collector.errors[0]["action"], "snapshot failure")

    def test_worker_timeout_keeps_ownership_and_next_device_cannot_revive_it(self):
        thread = Mock()
        thread.is_alive.return_value = True
        self.collector.family, self.collector.udid = "ipad", "original-owned"
        self.collector.app_pid = 1234
        self.collector._threads.append(thread)
        self.collector.stop_device()
        self.assertIn(thread, self.collector._threads)
        self.assertTrue(self.collector._stopped.is_set())
        with patch("simulator_diagnostics.subprocess.Popen") as popen:
            self.collector.start_device("another-owned", "iphone")
            self.collector.start_logs()
            self.collector.begin_resume()
            self.collector.launched("top.zperme.linkremote: 9876")
            self.collector.failure()
        popen.assert_not_called()
        self.assertEqual(self.collector.family, "ipad")
        self.assertEqual(self.collector.udid, "original-owned")
        self.assertEqual(self.collector.app_pid, 1234)
        self.assertFalse(any(self.collector.output.glob("*-failure-*")))
        self.assertTrue(self.collector._stopped.is_set())
        thread.is_alive.return_value = False

    def test_failure_copies_only_fresh_matching_app_or_owned_device_jetsam(self):
        self.collector.family, self.collector.app_pid = "ipad", 70458
        self.collector.udid = "12345678-1234-1234-1234-123456789ABC"
        home = Path(self.directory.name)
        reports = home / "Library/Logs/DiagnosticReports"
        owned = home / "Library/Developer/CoreSimulator/Devices" / self.collector.udid / "data/Library/Logs/CrashReporter"
        reports.mkdir(parents=True)
        owned.mkdir(parents=True)
        old = reports / "Runner-old.ips"
        old.write_text('{"pid":70458,"old":true}')
        os.utime(old, (1, 1))
        (reports / "Runner-other.ips").write_text('{"pid":99}')
        (reports / "Runner-new.ips").write_text('{"pid":70458,"fresh":true}')
        (owned / "JetsamEvent-new.ips").write_text('{"processes":[{"pid":70458,"jetsam":true}]}')
        with patch("simulator_diagnostics.Path.home", return_value=home), \
                patch.object(self.collector, "snapshot"):
            self.collector.failure()
        contents = "\n".join(path.read_text() for path in self.collector.output.glob("ipad-failure-report-*"))
        self.assertIn('"fresh":true', contents)
        self.assertIn('"jetsam":true', contents)
        self.assertNotIn('"old":true', contents)
        self.assertNotIn('"pid":99', contents)


if __name__ == "__main__":
    unittest.main()
