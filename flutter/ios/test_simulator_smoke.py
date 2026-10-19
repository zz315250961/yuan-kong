import unittest

from simulator_smoke import choose_devices


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


if __name__ == "__main__":
    unittest.main()
