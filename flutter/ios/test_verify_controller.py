"""Regression checks for the artifact validation gate; no Xcode is needed."""

import contextlib
import io
import plistlib
import re
import struct
import tempfile
import unittest
from pathlib import Path

import verify_controller as verify


class DeviceArtifactTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.app = Path(self.temporary.name) / "Runner.app"
        self.app.mkdir()
        self.info = verify.read_plist(verify.IOS / "Runner/Info.plist")
        version = re.search(r"^version:\s*([^+\s]+)\+(\d+)\s*$",
                            (verify.IOS.parent / "pubspec.yaml").read_text(encoding="utf-8"),
                            re.MULTILINE)
        self.info.update({
            "CFBundleIdentifier": "top.zperme.linkremote",
            "CFBundleExecutable": "Runner",
            "CFBundleShortVersionString": version[1],
            "CFBundleVersion": version[2],
            "CFBundleSupportedPlatforms": ["iPhoneOS"],
            "CFBundleIcons": {"CFBundlePrimaryIcon": {"CFBundleIconName": "AppIcon"}},
        })
        (self.app / "Assets.car").touch()
        (self.app / "Runner").write_bytes(struct.pack("<II", 0xFEEDFACF, 0x0100000C))

    def inspect(self):
        (self.app / "Info.plist").write_bytes(plistlib.dumps(self.info))
        with contextlib.redirect_stdout(io.StringIO()):
            verify.check_app(self.app)

    def test_current_arm64_device_artifact_passes(self):
        self.inspect()

    def test_old_or_wrong_bundle_fails(self):
        self.info["CFBundleIdentifier"] = "com.carriez.flutter_hbb"
        with self.assertRaisesRegex(ValueError, "bundle ID"):
            self.inspect()

    def test_stale_version_fails(self):
        self.info["CFBundleShortVersionString"] = "0.0.0"
        with self.assertRaisesRegex(ValueError, "version differs"):
            self.inspect()

    def test_simulator_artifact_fails(self):
        self.info["CFBundleSupportedPlatforms"] = ["iPhoneSimulator"]
        with self.assertRaisesRegex(ValueError, "device artifact"):
            self.inspect()

    def test_intel_simulator_requires_its_own_mode(self):
        self.info["CFBundleSupportedPlatforms"] = ["iPhoneSimulator"]
        (self.app / "Runner").write_bytes(struct.pack("<II", 0xFEEDFACF, 0x01000007))
        (self.app / "Info.plist").write_bytes(plistlib.dumps(self.info))
        with contextlib.redirect_stdout(io.StringIO()):
            verify.check_app(self.app, simulator=True)

    def test_device_cpu_cannot_be_mislabeled_as_simulator(self):
        self.info["CFBundleSupportedPlatforms"] = ["iPhoneSimulator"]
        (self.app / "Info.plist").write_bytes(plistlib.dumps(self.info))
        with self.assertRaisesRegex(ValueError, "x86_64"):
            verify.check_app(self.app, simulator=True)

    def test_wrong_cpu_fails(self):
        (self.app / "Runner").write_bytes(struct.pack("<II", 0xFEEDFACF, 0x01000007))
        with self.assertRaisesRegex(ValueError, "arm64"):
            self.inspect()

    def test_missing_assets_fail(self):
        (self.app / "Assets.car").unlink()
        with self.assertRaisesRegex(ValueError, "asset catalog"):
            self.inspect()

    def test_unconfigured_deep_link_owner_fails(self):
        self.info.pop("FlutterDeepLinkingEnabled")
        with self.assertRaisesRegex(ValueError, "uni_links"):
            self.inspect()


if __name__ == "__main__":
    unittest.main()
