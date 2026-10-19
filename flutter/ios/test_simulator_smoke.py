import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from simulator_smoke import choose_devices, main, simctl, smoke_device


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

    def run_smoke(self, fail_frame=False, fail_cleanup=False, fail_ready=None, log_text=""):
        def command(*arguments, **kwargs):
            self.events.append(arguments)
            if fail_ready and arguments[0] == fail_ready:
                raise subprocess.TimeoutExpired("simctl " + fail_ready, kwargs.get("timeout", 120))
            if fail_cleanup and arguments[0] == "shutdown":
                raise subprocess.TimeoutExpired("simctl shutdown", 120)
            if arguments[0] == "launch" and arguments[-1] == "top.zperme.linkremote":
                return "top.zperme.linkremote: 1234\n"
            if arguments[:3] == ("spawn", "test-udid", "launchctl"):
                return "1234\t0\ttop.zperme.linkremote\n"
            return ""

        def capture(udid, target):
            self.events.append(("screenshot", target.name))

        def frame(log_path, process):
            if fail_frame:
                raise RuntimeError("first frame failed")
            log_path.write_text(log_text, encoding="utf-8")

        with patch("simulator_smoke.simctl", side_effect=command), \
                patch("simulator_smoke.screenshot", side_effect=capture), \
                patch("simulator_smoke.wait_for_frame", side_effect=frame), \
                patch("simulator_smoke.subprocess.Popen", return_value=Mock()), \
                patch("simulator_smoke.time.sleep"), patch("builtins.print"):
            return smoke_device({"name": "iPhone Test", "udid": "test-udid",
                                 "state": "Shutdown"}, self.output / "Runner.app", self.output)

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


class SimulatorTimeoutDiagnosticsTests(unittest.TestCase):
    def test_timeout_preserves_the_real_bootstatus_output(self):
        failure = subprocess.TimeoutExpired("xcrun simctl bootstatus", 180,
                                            output=b"Waiting on Data Migration\n")
        with patch("simulator_smoke.subprocess.run", side_effect=failure):
            with self.assertRaisesRegex(RuntimeError, "Waiting on Data Migration"):
                simctl("bootstatus", "test-udid", "-b", timeout=180)


class SimulatorCLITests(unittest.TestCase):
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
