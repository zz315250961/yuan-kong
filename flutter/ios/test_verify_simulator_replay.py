"""The replay gate must reject a different source or binary before Simulator install."""

import hashlib
import plistlib
import struct
import tempfile
import unittest
import zipfile
from pathlib import Path

from verify_simulator_replay import check_archive, check_source


class ReplayProvenanceTests(unittest.TestCase):
    def setUp(self):
        self.source = {"id": 123, "head_sha": "a" * 40}
        self.artifacts = [{"id": 456, "name": "simulator-app-1", "expired": False,
                           "workflow_run": {"id": 123, "head_sha": "a" * 40}}]

    def inspect(self):
        return check_source(self.source, self.artifacts, "123", "a" * 40, "simulator-app-1")

    def test_exact_source_and_artifact_pass(self):
        self.assertEqual(self.inspect()["artifact_id"], 456)

    def test_different_source_commit_fails(self):
        self.source["head_sha"] = "b" * 40
        with self.assertRaisesRegex(ValueError, "source commit"):
            self.inspect()

    def test_different_run_or_artifact_fails(self):
        self.artifacts[0]["workflow_run"]["id"] = 999
        with self.assertRaisesRegex(ValueError, "artifact source"):
            self.inspect()

    def test_expired_or_ambiguous_artifact_fails(self):
        self.artifacts[0]["expired"] = True
        with self.assertRaisesRegex(ValueError, "expired"):
            self.inspect()
        self.artifacts[0]["expired"] = False
        self.artifacts.append(self.artifacts[0].copy())
        with self.assertRaisesRegex(ValueError, "exactly one"):
            self.inspect()


class ReplayArchiveTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.archive = Path(self.directory.name) / "simulator.zip"
        self.info = {"CFBundleIdentifier": "top.zperme.linkremote",
                     "CFBundleExecutable": "Runner",
                     "CFBundleShortVersionString": "1.0.43", "CFBundleVersion": "75",
                     "CFBundleSupportedPlatforms": ["iPhoneSimulator"]}

    def make_archive(self, cpu=0x01000007, executable=True, extra=None):
        with zipfile.ZipFile(self.archive, "w") as archive:
            archive.writestr("Runner.app/Info.plist", plistlib.dumps(self.info))
            runner = zipfile.ZipInfo("Runner.app/Runner")
            runner.create_system = 3
            runner.external_attr = (0o100755 if executable else 0o100644) << 16
            archive.writestr(runner, struct.pack("<II", 0xFEEDFACF, cpu))
            archive.writestr("Runner.app/Assets.car", b"assets")
            if extra:
                archive.writestr(extra, b"unexpected")
        return hashlib.sha256(self.archive.read_bytes()).hexdigest()

    def inspect(self, digest):
        return check_archive(self.archive, digest, "1.0.43", "75")

    def test_exact_intel_simulator_archive_passes_without_current_pubspec(self):
        result = self.inspect(self.make_archive())
        self.assertEqual(result["version"], "1.0.43")
        self.assertEqual(result["build"], "75")

    def test_different_archive_hash_fails(self):
        self.make_archive()
        with self.assertRaisesRegex(ValueError, "SHA-256"):
            self.inspect("0" * 64)

    def test_device_archive_cannot_be_replayed(self):
        self.info["CFBundleSupportedPlatforms"] = ["iPhoneOS"]
        with self.assertRaisesRegex(ValueError, "Simulator"):
            self.inspect(self.make_archive(cpu=0x0100000C))

    def test_arm64_cannot_be_mislabeled_as_intel_simulator(self):
        with self.assertRaisesRegex(ValueError, "x86_64"):
            self.inspect(self.make_archive(cpu=0x0100000C))

    def test_stale_version_and_missing_executable_permission_fail(self):
        self.info["CFBundleVersion"] = "76"
        with self.assertRaisesRegex(ValueError, "version"):
            self.inspect(self.make_archive())
        self.info["CFBundleVersion"] = "75"
        with self.assertRaisesRegex(ValueError, "executable permission"):
            self.inspect(self.make_archive(executable=False))

    def test_archive_cannot_escape_app_directory(self):
        with self.assertRaisesRegex(ValueError, "archive path"):
            self.inspect(self.make_archive(extra="Runner.app/../../outside"))


if __name__ == "__main__":
    unittest.main()
