from pathlib import Path
import re
import unittest


ROOT = Path(__file__).resolve().parents[2]
WORKFLOWS = ROOT / ".github" / "workflows"


def text(name):
    return (WORKFLOWS / name).read_text(encoding="utf-8")


def on_block(source):
    match = re.search(r"(?ms)^on:\s*\n(?P<body>(?:^[ \t]+.*\n?)*)", source)
    return match.group("body") if match else ""


class ReleaseScopeTest(unittest.TestCase):
    def test_upstream_release_workflows_are_manual_only(self):
        for name in (
            "flutter-tag.yml",
            "flutter-nightly.yml",
            "fdroid.yml",
            "flutter-ci.yml",
        ):
            block = on_block(text(name))
            self.assertIn("workflow_dispatch:", block, name)
            self.assertNotIn("push:", block, name)
            self.assertNotIn("pull_request:", block, name)
            self.assertNotIn("schedule:", block, name)

    def test_only_product_release_responds_to_version_tags(self):
        block = on_block(text("remote-desk-build.yml"))
        self.assertIn("push:", block)
        self.assertIn("v[0-9]+.[0-9]+.[0-9]+", block)

    def test_product_release_contains_only_supported_architectures(self):
        workflow = text("remote-desk-build.yml")
        self.assertIn("x86_64-pc-windows-msvc", workflow)
        self.assertIn("aarch64-linux-android", workflow)
        for unsupported in (
            "i686-pc-windows-msvc",
            "aarch64-pc-windows-msvc",
            "armv7-linux-androideabi",
            "x86_64-linux-android",
            "i686-linux-android",
        ):
            self.assertNotIn(unsupported, workflow)

    def test_product_release_packages_web_management(self):
        workflow = text("remote-desk-build.yml")
        self.assertIn("web-management:", workflow)
        self.assertIn(
            "python -m unittest discover -s server/linkremote-api/tests -v",
            workflow,
        )
        self.assertIn("LinkRemote-${VERSION}-web-management.zip", workflow)

    def test_product_ci_checks_scope_and_web_without_release_uploads(self):
        workflow = text("linkremote-ci.yml")
        self.assertIn("python .github/tests/test_release_scope.py -v", workflow)
        self.assertIn(
            "python -m unittest discover -s server/linkremote-api/tests -v",
            workflow,
        )
        self.assertNotIn("action-gh-release", workflow)
        self.assertNotIn("flutter-build.yml", workflow)


if __name__ == "__main__":
    unittest.main()
