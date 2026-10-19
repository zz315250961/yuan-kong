"""Distribution metadata checks; no client executable or installer is run."""

from pathlib import Path
import hashlib
import importlib.util
import re
import runpy
import shlex
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
import zipfile


ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("release_candidate", ROOT / ".github/release_candidate.py")
CANDIDATE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(CANDIDATE)


def web_package_command():
    workflow = (ROOT / ".github" / "workflows" / "linkremote-release.yml").read_text(
        encoding="utf-8"
    )
    command = re.search(
        r"(?m)^      - name: Package Web management server\n        run: (.+)$", workflow
    )
    if command is None:
        raise AssertionError("Missing Web management packaging command")
    return shlex.split(command[1])


class MsiLicenseTest(unittest.TestCase):
    def test_preprocessing_preserves_reviewed_license_and_upstream_attribution(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            script = root / "res" / "msi" / "preprocess.py"
            shutil.copytree(ROOT / "res" / "msi", script.parent)
            shutil.copy2(ROOT / "res" / "icon.ico", script.parent.parent / "icon.ico")
            dist = root / "dist"
            dist.mkdir()
            # Packaging input only: never execute this placeholder.
            (dist / "LinkRemote.exe").write_bytes(b"not an executable")
            license_file = script.parent / "Package" / "License.rtf"
            reviewed_license = license_file.read_bytes()
            self.assertIn(b"derived from the RustDesk community project", reviewed_license)
            self.assertIn(b"https://github.com/zz315250961/yuan-kong", reviewed_license)

            process = mock.Mock()
            process.communicate.return_value = (b"2026-10-19 10:00", None)
            args = [str(script), "--app-name", "LinkRemote", "--version", "1.0.44",
                    "--revision-version", "123", "--manufacturer", "LinkRemote contributors",
                    "-d", "../../dist"]
            # The preprocessor normally asks the built client for its build date.
            # Stub that OS process boundary so tests cannot start any client.
            with mock.patch.object(sys, "argv", args), mock.patch(
                "subprocess.Popen", return_value=process
            ):
                runpy.run_path(str(script), run_name="__main__")

            packaged_license = license_file.read_bytes()
            self.assertIn(b"derived from the RustDesk community project", packaged_license)
            self.assertEqual(packaged_license, reviewed_license)


class CandidateSourceTest(unittest.TestCase):
    SHA = "a" * 40

    def test_candidate_does_not_require_or_create_a_tag(self):
        CANDIDATE.validate_source(self.SHA, self.SHA, "1.0.44", "branch-name", False)

    def test_source_pin_rejects_blank_short_moving_and_different_refs(self):
        for value in ("", "a" * 7, "branch-name", "b" * 40):
            with self.subTest(value=value), self.assertRaises(ValueError):
                CANDIDATE.validate_source(self.SHA, value, "1.0.44", "", False)

    def test_publication_requires_matching_existing_immutable_version_tag(self):
        CANDIDATE.validate_source(self.SHA, self.SHA, "1.0.44", "v1.0.44", True, self.SHA)
        for tag, tag_sha in (("v1.0.43", self.SHA), ("v1.0.44", None), ("v1.0.44", "b" * 40)):
            with self.subTest(tag=tag, tag_sha=tag_sha), self.assertRaises(ValueError):
                CANDIDATE.validate_source(self.SHA, self.SHA, "1.0.44", tag, True, tag_sha)

    def test_current_candidate_versions_are_consistent(self):
        workflow = (ROOT / ".github/workflows/linkremote-release.yml").read_text(encoding="utf-8")
        version = re.search(r'(?m)^  VERSION: "([0-9.]+)"$', workflow)[1]
        metadata = CANDIDATE.candidate_versions(ROOT, version)
        self.assertEqual(metadata["version"], version)
        self.assertGreater(metadata["flutter_build"], 0)

    def test_version_validation_does_not_accept_old_source_under_new_name(self):
        with self.assertRaisesRegex(ValueError, "Cargo version"):
            CANDIDATE.candidate_versions(ROOT, "0.0.0")

    def test_provenance_records_real_file_hash_and_source_without_runtime_claims(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "LinkRemote-1.0.44-android-signed.apk"
            # Hash input only: never install or execute this placeholder.
            content = b"candidate-test-input"
            path.write_bytes(content)
            manifest = CANDIDATE.candidate_manifest(
                ROOT, "android", [path], self.SHA,
                {"version": "1.0.44", "flutter_build": 76},
                {"GITHUB_RUN_ID": "123", "GITHUB_RUN_ATTEMPT": "2", "PUBLISH_RELEASE": "false"},
            )
            self.assertEqual(manifest["source_commit"], self.SHA)
            self.assertEqual(manifest["run_id"], "123")
            self.assertEqual(manifest["run_attempt"], "2")
            self.assertTrue(manifest["candidate_only"])
            self.assertEqual(manifest["artifacts"], [{
                "name": path.name, "bytes": len(content), "sha256": hashlib.sha256(content).hexdigest()
            }])
            self.assertEqual({record["name"] for record in manifest["notices"]}, {"LICENCE", "NOTICE.md"})
            self.assertIn("not installation, runtime, signature identity, or release approval", manifest["scope"])

    def test_provenance_rejects_wrong_version_unsigned_or_duplicate_names(self):
        metadata = {"version": "1.0.44", "flutter_build": 76}
        for names in (
            ["LinkRemote-1.0.43-android-signed.apk"],
            ["LinkRemote-1.0.44-android.apk"],
            ["LinkRemote-1.0.44-android-signed.apk"] * 2,
        ):
            with self.subTest(names=names), self.assertRaisesRegex(ValueError, "artifact names"):
                CANDIDATE.candidate_manifest(ROOT, "android", names, self.SHA, metadata, {})

    def test_provenance_rejects_missing_and_empty_files(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "LinkRemote-1.0.44-web-management.zip"
            with self.assertRaisesRegex(ValueError, "Missing"):
                CANDIDATE.artifact_record(path)
            path.touch()
            with self.assertRaisesRegex(ValueError, "Empty"):
                CANDIDATE.artifact_record(path)


class WebPackageTest(unittest.TestCase):
    def test_release_command_includes_root_licenses_without_relocating_server(self):
        command = web_package_command()
        self.assertEqual(command[:3], ["zip", "-r", "LinkRemote-${VERSION}-web-management.zip"])
        exclude_index = command.index("-x")
        inputs = command[3:exclude_index]
        self.assertIn("LICENCE", inputs)
        self.assertIn("NOTICE.md", inputs)
        self.assertEqual(set(inputs), {
            "server/linkremote-api", "server/nginx", "server/README.md", "LICENCE", "NOTICE.md"
        })
        self.assertEqual(set(command[exclude_index + 1:]), {
            "*/data/*", "*/smtp.json", "*/__pycache__/*"
        })

    @unittest.skipUnless(shutil.which("zip"), "Requires zip, available on the Linux release runner")
    def test_archive_preserves_deployment_paths_licenses_and_secret_exclusions(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fixtures = {
                "server/linkremote-api/app.py": "# test application",
                "server/linkremote-api/data/linkremote.db": "must not ship",
                "server/linkremote-api/smtp.json": "must not ship",
                "server/linkremote-api/__pycache__/app.pyc": "must not ship",
                "server/nginx/linkremote-web.conf.example": "# test config",
                "server/README.md": "test deployment instructions",
            }
            for name, content in fixtures.items():
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(content, encoding="utf-8")
            for name in ("LICENCE", "NOTICE.md"):
                shutil.copy2(ROOT / name, root / name)
            command = web_package_command()
            command[0] = shutil.which("zip")
            command[2] = command[2].replace("${VERSION}", "1.2.3")
            subprocess.run(command, cwd=root, check=True, capture_output=True, text=True)
            with zipfile.ZipFile(root / command[2]) as archive:
                names = set(archive.namelist())
                for name in ("LICENCE", "NOTICE.md"):
                    self.assertIn(name, names)
                    self.assertEqual(archive.read(name), (ROOT / name).read_bytes())
                self.assertIn("server/linkremote-api/app.py", names)
                self.assertIn("server/nginx/linkremote-web.conf.example", names)
                self.assertIn("server/README.md", names)
                for name in names:
                    self.assertNotIn("/data/", name)
                    self.assertNotIn("/__pycache__/", name)
                    self.assertFalse(name.endswith("/smtp.json"), name)


if __name__ == "__main__":
    unittest.main()
