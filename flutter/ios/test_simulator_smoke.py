import json
import struct
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
import zlib

from simulator_smoke import _DIAGNOSTICS, choose_devices, main, simctl, smoke_device


def device(name, available=True):
    return {"name": name, "isAvailable": available}


class SimulatorInventoryTests(unittest.TestCase):
    def test_uses_latest_ios_with_both_device_classes(self):
        runtime, selected = choose_devices({"devices": {
            "com.apple.CoreSimulator.SimRuntime.iOS-17-5": [device("iPhone 15"), device("iPad Pro")],
            "com.apple.CoreSimulator.SimRuntime.iOS-18-5": [device("iPhone 16"), device("iPad Air")],
            "com.apple.CoreSimulator.SimRuntime.tvOS-26-0": [device("Apple TV")],
        }})
        self.assertTrue(runtime.endswith("iOS-18-5"))
        self.assertEqual([item["name"] for item in selected], ["iPhone 16", "iPad Air"])

    def test_does_not_select_unavailable_ipad(self):
        with self.assertRaisesRegex(RuntimeError, "iPhone and iPad"):
            choose_devices({"devices": {"com.apple.CoreSimulator.SimRuntime.iOS-18-5": [
                device("iPhone 16"), device("iPad Air", False)]}})

    def test_uses_runtime_for_selected_xcode_sdk(self):
        runtime, _ = choose_devices({"devices": {
            "com.apple.CoreSimulator.SimRuntime.iOS-18-5": [device("iPhone 16"), device("iPad Air")],
            "com.apple.CoreSimulator.SimRuntime.iOS-26-2": [device("iPhone 17"), device("iPad Pro")],
        }}, sdk_version="18.5")
        self.assertTrue(runtime.endswith("iOS-18-5"))


class SimulatorSequenceTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.output = Path(self.directory.name)
        self.events = []

    def run_smoke(self, fail_frame=False, fail_cleanup=False, fail_ready=None, log_text="",
                  verify_images=False, capture_bytes=None, resume_pid=1234,
                  fail_image=None, **options):
        def command(*arguments, **kwargs):
            self.events.append(arguments)
            if fail_ready and arguments[0] == fail_ready:
                raise subprocess.TimeoutExpired("simctl " + fail_ready, kwargs.get("timeout", 120))
            if fail_cleanup and arguments[0] == "shutdown":
                raise subprocess.TimeoutExpired("simctl shutdown", 120)
            if arguments[0] == "launch" and "top.zperme.linkremote" in arguments:
                pid = 1234 if "--terminate-running-process" in arguments else resume_pid
                return f"top.zperme.linkremote: {pid}\n"
            if arguments[:3] == ("spawn", "test-udid", "launchctl"):
                return "1234\t0\ttop.zperme.linkremote\n"
            return ""

        def capture(udid, target):
            self.events.append(("screenshot", target.name))
            if capture_bytes is not None:
                target.write_bytes(capture_bytes)

        def frame(log_path, process):
            if fail_frame:
                raise RuntimeError("first frame failed")
            log_path.write_text(log_text, encoding="utf-8")

        def image_guard(path):
            self.events.append(("image-check", path.name))
            if path.name == fail_image:
                raise RuntimeError("Known home-frame corruption: injected guard failure")
            return {"known_corruption_detected": False, "scope": "mocked image guard"}

        from contextlib import nullcontext
        guard = (nullcontext() if verify_images else
                 patch("simulator_smoke.check_home_screenshot", create=True,
                       side_effect=image_guard))
        with guard, patch("simulator_smoke.simctl", side_effect=command), \
                patch("simulator_smoke.screenshot", side_effect=capture), \
                patch("simulator_smoke.wait_for_frame", side_effect=frame), \
                patch("simulator_smoke.subprocess.Popen", return_value=Mock()), \
                patch("simulator_smoke.time.sleep"), patch("builtins.print"):
            return smoke_device({"name": "iPhone Test", "udid": "test-udid",
                                 "state": "Shutdown"}, self.output / "Runner.app", self.output,
                                **options)

    def test_default_negative_image_gate_runs_on_every_app_checkpoint(self):
        result = self.run_smoke()
        self.assertTrue(result["passed"])
        self.assertEqual([event[1] for event in self.events if event[0] == "image-check"],
                         result["screenshots"])
        self.assertEqual(len(result["image_checks"]), 4)
        self.assertEqual(result["requested_renderer"], "default")
        self.assertFalse(result["renderer_backend_verified"])
        self.assertEqual(result["stress_cycles_completed"], 0)
        self.assertFalse(any("--enable-impeller=false" in event for event in self.events))

    def test_actual_smoke_loop_rejects_known_black_bar_surface_and_still_cleans_up(self):
        def chunk(kind, payload):
            return (struct.pack(">I", len(payload)) + kind + payload
                    + struct.pack(">I", zlib.crc32(kind + payload) & 0xffffffff))
        width, height = 180, 320
        rows = [b"\x00" + b"".join(
            (b"\x00\x00\x00\xff" if y >= 32 and (y // 8) % 2 == 0 and 30 <= x < 150
             else b"\xff\xff\xff\xff") for x in range(width)) for y in range(height)]
        data = (b"\x89PNG\r\n\x1a\n" + chunk(
            b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0))
                + chunk(b"IDAT", zlib.compress(b"".join(rows))) + chunk(b"IEND", b""))
        result = self.run_smoke(verify_images=True, capture_bytes=data)
        self.assertFalse(result["passed"])
        self.assertEqual(result["failed_stage"], "home-light")
        self.assertIn("Known home-frame corruption", result["error"])
        self.assertFalse(any(event[0] == "openurl" for event in self.events))
        self.assertIn(("shutdown", "test-udid"), self.events)

    def test_actual_smoke_loop_rejects_original_damaged_capture_not_just_synthetic_pattern(self):
        original = (Path(__file__).resolve().parents[2].parent / "artifacts" /
                    "v1.0.44-candidate/ios-c0354c-39287264581/terminal-evidence-20261020/"
                    "ui-evidence/ipad-home-light.png")
        if not original.is_file():
            self.skipTest("Original unmodified damaged screenshot is unavailable on this host")
        result = self.run_smoke(verify_images=True, capture_bytes=original.read_bytes())
        self.assertFalse(result["passed"])
        self.assertIn("Known home-frame corruption", result["error"])

    def test_unreadable_capture_cannot_pass_image_gate(self):
        result = self.run_smoke(verify_images=True, capture_bytes=b"not a PNG")
        self.assertFalse(result["passed"])
        self.assertIn("Cannot inspect home screenshot", result["error"])

    def test_no_finding_from_real_reader_stays_explicitly_not_visual_approval(self):
        def chunk(kind, payload):
            return (struct.pack(">I", len(payload)) + kind + payload
                    + struct.pack(">I", zlib.crc32(kind + payload) & 0xffffffff))
        white = (b"\x89PNG\r\n\x1a\n" + chunk(
            b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 6, 0, 0, 0))
                 + chunk(b"IDAT", zlib.compress(b"\x00\xff\xff\xff\xff"))
                 + chunk(b"IEND", b""))
        result = self.run_smoke(verify_images=True, capture_bytes=white)
        self.assertTrue(result["passed"])
        self.assertEqual(len(result["image_checks"]), 4)
        self.assertTrue(all("not visual approval" in item["scope"] for item in result["image_checks"]))

    def test_skia_request_is_applied_to_initial_and_every_resume_without_claiming_backend(self):
        result = self.run_smoke(renderer="skia", stress_cycles=2)
        launches = [event for event in self.events
                    if event[0] == "launch" and "top.zperme.linkremote" in event]
        self.assertEqual(len(launches), 4)
        self.assertTrue(all(event[-1] == "--enable-impeller=false" for event in launches))
        self.assertEqual(result["requested_renderer"], "skia")
        self.assertFalse(result["renderer_backend_verified"])

    def test_stress_cycles_are_bounded_unique_and_all_complete_before_url_prompt(self):
        result = self.run_smoke(stress_cycles=6)
        self.assertTrue(result["passed"])
        self.assertEqual(result["stress_cycles_completed"], 6)
        self.assertEqual(len(result["screenshots"]), 22)
        self.assertEqual(len(set(result["screenshots"])), 22)
        self.assertIn("iphone-stress-06-resumed.png", result["screenshots"])
        self.assertEqual(len(result["image_checks"]), 22)
        open_index = next(index for index, event in enumerate(self.events) if event[0] == "openurl")
        for name in result["screenshots"][:-1]:
            self.assertLess(self.events.index(("screenshot", name)), open_index)

    def test_invalid_stress_or_renderer_fails_before_any_simulator_command(self):
        for options in ({"stress_cycles": -1}, {"stress_cycles": 7}, {"renderer": "metal"}):
            with self.subTest(options=options), self.assertRaises(ValueError):
                self.run_smoke(**options)
        self.assertEqual(self.events, [])

    def test_stress_image_failure_stops_without_url_and_retains_completed_cycle_count(self):
        result = self.run_smoke(stress_cycles=6, fail_image="iphone-stress-03-home-dark.png")
        self.assertFalse(result["passed"])
        self.assertEqual(result["stress_cycles_completed"], 2)
        self.assertEqual(result["failed_stage"], "stress-03-home-dark")
        self.assertFalse(any(event[0] == "openurl" for event in self.events))
        self.assertIn(("shutdown", "test-udid"), self.events)

    def test_diagnostic_extra_image_guard_cannot_be_downgraded_to_optional_capture_error(self):
        collector = Mock()
        token = _DIAGNOSTICS.set(collector)
        try:
            result = self.run_smoke(fail_image="iphone-diagnostic-light-1.png")
        finally:
            _DIAGNOSTICS.reset(token)
        self.assertFalse(result["passed"])
        self.assertIn("Known home-frame corruption", result["error"])
        self.assertFalse(any(event[0] == "openurl" for event in self.events))
        collector.stop_device.assert_called_once_with()

    def test_resume_pid_change_is_not_hidden_by_surviving_original_launchctl_row(self):
        result = self.run_smoke(resume_pid=5678)
        self.assertFalse(result["passed"])
        self.assertEqual(result["failed_stage"], "resumed")
        self.assertIn("restarted", result["error"])

    def test_actual_smoke_loop_finishes_unobscured_ui_before_openurl(self):
        result = self.run_smoke()
        self.assertTrue(result["passed"])
        self.assertEqual(result["screenshots"], [
            "iphone-home-light.png", "iphone-home-dark.png",
            "iphone-resumed.png", "iphone-warm-link.png"])
        open_index = next(index for index, event in enumerate(self.events) if event[0] == "openurl")
        for name in result["screenshots"][:3]:
            self.assertLess(self.events.index(("screenshot", name)), open_index)
        light = self.events.index(("ui", "test-udid", "appearance", "light"),
                                  self.events.index(("screenshot", "iphone-home-dark.png")))
        safari = self.events.index(("launch", "test-udid", "com.apple.mobilesafari"),
                                   self.events.index(("screenshot", "iphone-home-dark.png")))
        self.assertLess(light, safari)
        self.assertFalse(result["url_delivery_verified"])
        self.assertEqual(result["url_delivery_status"], "system_confirmation_requires_manual_verification")

    def test_native_controls_and_browser_cold_start_finish_before_target_app_install(self):
        result = self.run_smoke()
        install = next(index for index, event in enumerate(self.events) if event[0] == "install")
        safari = self.events.index(("launch", "test-udid", "com.apple.mobilesafari"))
        settings = self.events.index(("launch", "test-udid", "com.apple.Preferences"))
        self.assertLess(safari, settings)
        self.assertLess(settings, install)
        self.assertEqual(result["native_control_bundle"], "com.apple.Preferences")
        self.assertEqual(result["native_screenshots"], ["iphone-native-safari-prewarmed.png",
                                                       "iphone-native-settings-light.png",
                                                       "iphone-native-settings-dark.png"])
        for name in result["native_screenshots"]:
            self.assertLess(self.events.index(("screenshot", name)), install)
        self.assertTrue(any(item["stage"] == "prewarm native Safari before target app startup"
                            and item["elapsed_seconds"] >= 0 for item in result["stage_timings"]))

    def test_failure_screenshot_and_cleanup_timeout_preserve_primary_error(self):
        result = self.run_smoke(fail_frame=True, fail_cleanup=True)
        self.assertFalse(result["passed"])
        self.assertEqual(result["error"], "first frame failed")
        self.assertIn(("screenshot", "iphone-failure.png"), self.events)
        self.assertEqual(result["failure_screenshot"], "iphone-failure.png")
        self.assertTrue(any("shutdown" in error for error in result["cleanup_errors"]))

    def test_runtime_boot_timeout_is_not_reported_as_app_failure(self):
        result = self.run_smoke(fail_ready="bootstatus")
        self.assertFalse(result["passed"])
        self.assertEqual(result["failed_stage"], "wait for runtime boot completion")
        self.assertFalse(any(event[0] == "install" for event in self.events))
        self.assertFalse(result.get("first_flutter_frame", False))

    def test_appearance_timeout_identifies_the_ready_command(self):
        result = self.run_smoke(fail_ready="ui")
        self.assertFalse(result["passed"])
        self.assertEqual(result["failed_stage"], "set initial light appearance")
        self.assertFalse(any(event[0] == "install" for event in self.events))

    def test_ui_overflow_and_native_panic_do_not_pass_a_live_process_check(self):
        for message in ("A RenderFlex overflowed by 12 pixels on the right.",
                        "thread 'main' panicked at src/lib.rs:42:5"):
            with self.subTest(message=message):
                result = self.run_smoke(log_text=message)
                self.assertFalse(result["passed"])
                self.assertEqual(result["failed_stage"], "verify original process and application log")

    def test_default_does_not_create_collector_or_extra_images(self):
        with patch("simulator_smoke.SimulatorDiagnostics") as collector:
            result = self.run_smoke()
        collector.assert_not_called()
        self.assertNotIn("diagnostic_screenshots", result)
        self.assertEqual(len(result["screenshots"]), 4)

    def test_opt_in_adds_separate_light_evidence_without_changing_original_sequence(self):
        collector = Mock()
        collector.begin_resume.side_effect = lambda: self.events.append(("diagnostic-resume-start",))
        collector.end_resume.side_effect = lambda: self.events.append(("diagnostic-resume-end",))
        token = _DIAGNOSTICS.set(collector)
        try:
            result = self.run_smoke()
        finally:
            _DIAGNOSTICS.reset(token)
        self.assertTrue(result["passed"])
        self.assertEqual(result["diagnostic_screenshots"],
                         ["iphone-diagnostic-light-1.png", "iphone-diagnostic-light-2.png"])
        self.assertEqual(len(result["screenshots"]), 4)
        collector.begin_resume.assert_called_once_with()
        collector.end_resume.assert_called_once_with()
        collector.stop_device.assert_called_once_with()
        dark = self.events.index(("screenshot", "iphone-home-dark.png"))
        safari = self.events.index(("launch", "test-udid", "com.apple.mobilesafari"), dark)
        resume = self.events.index(("launch", "test-udid", "top.zperme.linkremote"), safari)
        start = self.events.index(("diagnostic-resume-start",))
        end = self.events.index(("diagnostic-resume-end",))
        self.assertGreater(start, safari)
        self.assertEqual(start + 1, resume)
        self.assertEqual(resume + 1, end)
        boot = self.events.index(("bootstatus", "test-udid", "-b"))
        self.assertLess(boot, self.events.index(("install", "test-udid", str(self.output / "Runner.app"))))

    def test_collector_failure_cannot_overwrite_primary_frame_failure(self):
        collector = Mock()
        collector.start_device.side_effect = OSError("diagnostics unavailable")
        collector.failure.side_effect = RuntimeError("sampling unavailable")
        token = _DIAGNOSTICS.set(collector)
        try:
            result = self.run_smoke(fail_frame=True)
        finally:
            _DIAGNOSTICS.reset(token)
        self.assertFalse(result["passed"])
        self.assertEqual(result["error"], "first frame failed")
        self.assertEqual(result["failed_stage"], "launch and wait for first Flutter frame")
        self.assertEqual(collector.error.call_count, 2)
        collector.stop_device.assert_called_once_with()


class SimulatorTimeoutDiagnosticsTests(unittest.TestCase):
    def test_timeout_preserves_the_real_bootstatus_output(self):
        failure = subprocess.TimeoutExpired("xcrun simctl bootstatus", 180,
                                            output=b"Waiting on Data Migration\n")
        with patch("simulator_smoke.subprocess.run", side_effect=failure):
            with self.assertRaisesRegex(RuntimeError, "Waiting on Data Migration"):
                simctl("bootstatus", "test-udid", "-b", timeout=180)

    def test_actual_command_timeout_and_nonzero_exit_are_recorded_not_hidden(self):
        collector = Mock()
        token = _DIAGNOSTICS.set(collector)
        try:
            with patch("simulator_smoke.subprocess.run", side_effect=subprocess.TimeoutExpired("simctl", 120)):
                with self.assertRaisesRegex(RuntimeError, "timed out after 120s"):
                    simctl("launch", "owned-udid", "top.zperme.linkremote")
            self.assertEqual(collector.command_finished.call_args.args[-1], "timeout")
            with patch("simulator_smoke.subprocess.run", return_value=Mock(returncode=7, stdout="failed")):
                with self.assertRaisesRegex(RuntimeError, "failed"):
                    simctl("ui", "owned-udid", "appearance", "light")
            self.assertEqual(collector.command_finished.call_args.args[-1], 7)
        finally:
            _DIAGNOSTICS.reset(token)


class SimulatorCLITests(unittest.TestCase):
    def test_renderer_stress_cli_wires_smoke_and_report_without_claiming_backend(self):
        inventory = {"devices": {"com.apple.CoreSimulator.SimRuntime.iOS-18-5": [
            device("iPhone 16 Pro"), device("iPad Pro 11-inch (M4)")]}}
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "ab-evidence"
            with patch("sys.argv", ["simulator_smoke.py", "--app", "Runner.app", "--output",
                                     str(output), "--family", "ipad", "--renderer", "skia",
                                     "--stress-cycles", "6"]), \
                    patch("simulator_smoke.subprocess.check_output", return_value="18.5\n"), \
                    patch("simulator_smoke.simctl", return_value=json.dumps(inventory)), \
                    patch("simulator_smoke.smoke_device", return_value={"passed": True}) as smoke, \
                    patch("builtins.print"):
                main()
            self.assertEqual(smoke.call_args.kwargs, {"renderer": "skia", "stress_cycles": 6})
            report = json.loads((output / "report.json").read_text())
            self.assertEqual(report["requested_renderer"], "skia")
            self.assertEqual(report["stress_cycles_requested"], 6)
            self.assertFalse(report["renderer_backend_verified"])
            self.assertIn("no finding is not visual approval", report["scope"])

    def test_out_of_range_cli_rejected_before_creating_output_or_querying_simulators(self):
        for arguments in (["--stress-cycles", "-1"], ["--stress-cycles", "7"],
                          ["--renderer", "metal"]):
            with self.subTest(arguments=arguments), tempfile.TemporaryDirectory() as directory:
                output = Path(directory) / "not-created"
                with patch("sys.argv", ["simulator_smoke.py", "--app", "Runner.app", "--output",
                                         str(output), *arguments]), \
                        patch("simulator_smoke.simctl") as command, \
                        patch("sys.stderr"), self.assertRaises(SystemExit) as failure:
                    main()
                self.assertEqual(failure.exception.code, 2)
                command.assert_not_called()
                self.assertFalse(output.exists())

    def test_opt_in_cli_wires_collector_and_reports_errors_separately(self):
        inventory = {"devices": {"com.apple.CoreSimulator.SimRuntime.iOS-18-5": [
            device("iPhone 16 Pro"), device("iPad Pro 11-inch (M4)")]}}
        collector = Mock(errors=[{"action": "probe", "error": "unavailable"}])
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "evidence"
            with patch("sys.argv", ["simulator_smoke.py", "--app", "Runner.app", "--output",
                                     str(output), "--family", "ipad", "--diagnostics"]), \
                    patch("simulator_smoke.SimulatorDiagnostics", return_value=collector), \
                    patch("simulator_smoke.subprocess.check_output", return_value="18.5\n"), \
                    patch("simulator_smoke.simctl", return_value=json.dumps(inventory)), \
                    patch("simulator_smoke.smoke_device", return_value={"passed": True}) as smoke, \
                    patch("builtins.print"):
                main()
            self.assertTrue(smoke.call_args.args[0]["name"].startswith("iPad"))
            report = json.loads((output / "report.json").read_text())
            self.assertEqual(report["diagnostics"]["errors"], collector.errors)
            self.assertTrue(report["devices"][0]["passed"])
        collector.close.assert_called_once_with()
        self.assertIsNone(_DIAGNOSTICS.get())

    def test_diagnostic_setup_failure_does_not_fail_the_smoke(self):
        inventory = {"devices": {"com.apple.CoreSimulator.SimRuntime.iOS-18-5": [
            device("iPhone 16 Pro"), device("iPad Pro 11-inch (M4)")]}}
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "evidence"
            with patch("sys.argv", ["simulator_smoke.py", "--app", "Runner.app", "--output",
                                     str(output), "--diagnostics"]), \
                    patch("simulator_smoke.SimulatorDiagnostics", side_effect=OSError("disk full")), \
                    patch("simulator_smoke.subprocess.check_output", return_value="18.5\n"), \
                    patch("simulator_smoke.simctl", return_value=json.dumps(inventory)), \
                    patch("simulator_smoke.smoke_device", return_value={"passed": True}), \
                    patch("builtins.print"):
                main()
            report = json.loads((output / "report.json").read_text())
            self.assertEqual(report["diagnostics"], {"enabled": False, "errors": ["disk full"]})

    def test_family_option_runs_only_the_requested_device(self):
        inventory = {"devices": {"com.apple.CoreSimulator.SimRuntime.iOS-18-5": [
            device("iPhone 16 Pro"), device("iPad Pro 11-inch (M4)")]}}
        for family, expected in (("iphone", "iPhone"), ("ipad", "iPad"), ("both", "both")):
            with self.subTest(family=family), tempfile.TemporaryDirectory() as directory:
                output = Path(directory) / "fresh-evidence"
                with patch("sys.argv", ["simulator_smoke.py", "--app", "Runner.app",
                                         "--output", str(output), "--family", family]), \
                        patch("simulator_smoke.subprocess.check_output", return_value="18.5\n"), \
                        patch("simulator_smoke.simctl", return_value=json.dumps(inventory)), \
                        patch("simulator_smoke.smoke_device", return_value={"passed": True}) as smoke, \
                        patch("builtins.print"):
                    main()
                names = [call.args[0]["name"] for call in smoke.call_args_list]
                self.assertEqual(len(names), 2 if expected == "both" else 1)
                if expected != "both":
                    self.assertTrue(names[0].startswith(expected))
                report = json.loads((output / "report.json").read_text(encoding="utf-8"))
                self.assertEqual(len(report["devices"]), len(names))

    def run_fresh_cli(self, created="12345678-1234-1234-1234-123456789ABC", fail_delete=False,
                      inventory_udid="existing-iphone", smoke_result=None):
        self.commands = []
        inventory = {"devices": {"com.apple.CoreSimulator.SimRuntime.iOS-18-5": [
            {"name": "iPhone 16 Pro", "isAvailable": True, "udid": inventory_udid,
             "deviceTypeIdentifier": "com.apple.CoreSimulator.SimDeviceType.iPhone-16-Pro"},
            {"name": "iPad Pro 11-inch (M4)", "isAvailable": True, "udid": "existing-ipad"}]}}

        def command(*arguments, **kwargs):
            self.commands.append(arguments)
            if arguments[0] == "list":
                return json.dumps(inventory)
            if arguments[0] == "create":
                return created + "\n"
            if arguments[0] == "delete" and fail_delete:
                raise RuntimeError("owned cleanup failed")
            return ""

        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "fresh-evidence"
            with patch("sys.argv", ["simulator_smoke.py", "--app", "Runner.app", "--output",
                                     str(output), "--family", "iphone", "--fresh-devices"]), \
                    patch("simulator_smoke.subprocess.check_output", return_value="18.5\n"), \
                    patch("simulator_smoke.simctl", side_effect=command), \
                    patch("simulator_smoke.smoke_device",
                          return_value=smoke_result or {"passed": True}) as smoke, \
                    patch("builtins.print"), patch("sys.stderr"):
                try:
                    main()
                except SystemExit as error:
                    self.assertEqual(error.code, 1)
            return json.loads((output / "report.json").read_text(encoding="utf-8")), smoke

    def test_fresh_devices_creates_same_type_and_deletes_only_the_owned_uuid(self):
        report, smoke = self.run_fresh_cli()
        created = "12345678-1234-1234-1234-123456789ABC"
        selected = smoke.call_args.args[0]
        self.assertEqual(selected["udid"], created)
        self.assertEqual(selected["name"], "iPhone 16 Pro")
        self.assertEqual(selected["state"], "Shutdown")
        creation = next(command for command in self.commands if command[0] == "create")
        self.assertEqual(creation[2:], ("com.apple.CoreSimulator.SimDeviceType.iPhone-16-Pro",
                                        "com.apple.CoreSimulator.SimRuntime.iOS-18-5"))
        self.assertEqual([command for command in self.commands if command[0] == "delete"],
                         [("delete", created)])
        self.assertTrue(report["devices"][0]["qa_created_device"])
        self.assertEqual(report["devices"][0]["inventory_udid"], "existing-iphone")

    def test_malformed_creation_response_cannot_delete_an_inventory_device(self):
        report, smoke = self.run_fresh_cli(created="existing-iphone")
        smoke.assert_not_called()
        self.assertFalse(report["devices"][0]["passed"])
        self.assertFalse(any(command[0] == "delete" for command in self.commands))

    def test_existing_uuid_is_rejected_even_when_it_has_valid_uuid_syntax(self):
        existing = "12345678-1234-1234-1234-123456789ABC"
        report, smoke = self.run_fresh_cli(created=existing, inventory_udid=existing)
        smoke.assert_not_called()
        self.assertFalse(report["devices"][0]["passed"])
        self.assertFalse(any(command[0] == "delete" for command in self.commands))

    def test_owned_deletion_failure_cannot_leave_a_passed_result(self):
        report, _ = self.run_fresh_cli(fail_delete=True)
        self.assertFalse(report["devices"][0]["passed"])
        self.assertIn("owned cleanup failed", report["devices"][0]["error"])

    def test_owned_cleanup_cannot_hide_the_primary_boot_failure(self):
        report, _ = self.run_fresh_cli(fail_delete=True, smoke_result={
            "passed": False, "error": "runtime boot failed", "failed_stage": "boot"})
        result = report["devices"][0]
        self.assertEqual(result["error"], "runtime boot failed")
        self.assertEqual(result["failed_stage"], "boot")
        self.assertTrue(any("owned cleanup failed" in error for error in result["cleanup_errors"]))


if __name__ == "__main__":
    unittest.main()
