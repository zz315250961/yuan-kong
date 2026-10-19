"""Distribution metadata checks; no client executable or installer is run."""

from pathlib import Path
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
            args = [str(script), "--app-name", "LinkRemote", "--version", "1.0.43",
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
