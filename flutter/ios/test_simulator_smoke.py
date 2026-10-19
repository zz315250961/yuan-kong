import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from simulator_smoke import choose_devices, smoke_device


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

    def run_smoke(self, fail_frame=False, fail_cleanup=False, fail_ready=None):
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

        frame_error = RuntimeError("first frame failed") if fail_frame else None
        with patch("simulator_smoke.simctl", side_effect=command), \
                patch("simulator_smoke.screenshot", side_effect=capture), \
                patch("simulator_smoke.wait_for_frame", side_effect=frame_error), \
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
        safari = self.events.index(("launch", "test-udid", "com.apple.mobilesafari"))
        self.assertLess(light, safari)
        self.assertFalse(result["url_delivery_verified"])
        self.assertEqual(result["url_delivery_status"], "system_confirmation_requires_manual_verification")

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


if __name__ == "__main__":
    unittest.main()
