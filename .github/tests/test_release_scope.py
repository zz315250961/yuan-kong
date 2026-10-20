from pathlib import Path
import json
import re
import unittest
from urllib.parse import urlparse


ROOT = Path(__file__).resolve().parents[2]
WORKFLOWS = ROOT / ".github" / "workflows"


def text(name):
    return (WORKFLOWS / name).read_text(encoding="utf-8")


def on_block(source):
    match = re.search(r"(?ms)^on:\s*\n(?P<body>(?:^[ \t]+.*\n?)*)", source)
    return match.group("body") if match else ""


def job_block(source, name):
    match = re.search(rf"(?ms)^  {re.escape(name)}:\n(.*?)(?=^  [a-z][a-z0-9-]*:\n|\Z)", source)
    if not match:
        raise ValueError(f"Missing release job: {name}")
    return match[1]


def stable_release_version(homepage, manifest):
    """The public site's stable version is independent of a candidate build."""
    site = re.search(r'title:\s*["\']LinkRemote\b[^"\']*["\']\s*,\s*sub:\s*["\']v([0-9]+\.[0-9]+\.[0-9]+)\s*·', homepage)
    if not site:
        raise ValueError("Homepage is missing the LinkRemote stable version")
    version = site[1]
    for platform, suffix in (("windows_x64", "x86_64.exe"), ("android_arm64", "android-signed.apk")):
        url = manifest.get(platform)
        if not isinstance(url, str):
            raise ValueError(f"Stable manifest is missing {platform}")
        parsed = urlparse(url)
        expected = f"/download/LinkRemote-{version}-{suffix}"
        if parsed.scheme != "https" or parsed.netloc != "zperme.top" or parsed.path != expected or parsed.query or parsed.fragment:
            raise ValueError(f"Stable {platform} download does not match homepage v{version}")
    return version


class ReleaseScopeTest(unittest.TestCase):
    def test_upstream_release_workflows_are_removed(self):
        for name in (
            "flutter-tag.yml",
            "flutter-nightly.yml",
            "fdroid.yml",
            "flutter-ci.yml",
            "remote-desk-build.yml",
        ):
            self.assertFalse((WORKFLOWS / name).exists(), name)

    def test_only_product_release_responds_to_version_tags(self):
        block = on_block(text("linkremote-release.yml"))
        self.assertIn("push:", block)
        self.assertIn("v[0-9]+.[0-9]+.[0-9]+", block)

    def test_manual_candidate_is_source_pinned_and_cannot_publish_by_default(self):
        workflow = text("linkremote-release.yml")
        inputs = on_block(workflow)
        self.assertRegex(inputs, r"(?s)source_sha:\n\s+description:[^\n]+\n\s+required: true\n\s+type: string")
        self.assertRegex(inputs, r"(?s)candidate_only:\n\s+description:[^\n]+\n\s+required: true\n\s+type: boolean\n\s+default: true")
        self.assertIn("EXPECTED_SOURCE_SHA: \"${{ github.event_name == 'push' && github.sha || inputs.source_sha }}\"", workflow)
        self.assertIn("inputs.candidate_only == false", workflow)
        checkout_count = len(re.findall(r"uses: actions/checkout@", workflow))
        self.assertEqual(checkout_count, 5)
        self.assertEqual(workflow.count("ref: ${{ github.sha }}"), checkout_count)
        self.assertEqual(workflow.count("fetch-depth: 0"), checkout_count)
        self.assertIn("python .github/release_candidate.py --check-source", workflow)
        self.assertIn("Release already exists; existing assets must not be overwritten", workflow)

    def test_candidate_builds_are_gated_and_final_packages_have_run_artifacts(self):
        workflow = text("linkremote-release.yml")
        for job in ("generate-bridge", "build-privacy-window", "web-management"):
            self.assertRegex(workflow, rf"(?m)^  {job}:\n    needs: \[prepare-release\]$")
        self.assertIn("needs: [build-privacy-window, generate-bridge]", workflow)
        self.assertIn("needs: [generate-bridge]", workflow)
        for platform in ("Windows", "Web"):
            self.assertIn(f"Record {platform} candidate provenance", workflow)
        self.assertIn("Verify signed Android candidate and record provenance", workflow)
        self.assertIn("Upload Windows candidate packages", workflow)
        self.assertIn("${{ github.run_id }}-${{ github.run_attempt }}", workflow)
        self.assertIn("Require configured Android signing identity", workflow)
        self.assertIn('python .github/verify_android_signature.py android-signature.txt --expected-sha256 "$EXPECTED_ANDROID_CERT_SHA256"', workflow)
        self.assertIn("c99e74633706c5ac413bf3c4e4782ceabd7b156eae6b288664986927d1aa1b01", workflow)
        self.assertNotIn("Publish unsigned apk package", workflow)

    def test_android_only_is_typed_opt_in_and_only_skips_non_android_jobs(self):
        workflow = text("linkremote-release.yml")
        self.assertRegex(on_block(workflow), r"(?s)android_only:\n\s+description:[^\n]+\n\s+required: true\n\s+type: boolean\n\s+default: false")
        gate = "if: github.event_name != 'workflow_dispatch' || inputs.android_only != true"
        self.assertEqual(workflow.count(gate), 3)
        for job in ("build-privacy-window", "build-for-windows-flutter", "web-management"):
            self.assertIn(gate, job_block(workflow, job))
        for job in ("prepare-release", "generate-bridge", "build-rustdesk-android"):
            self.assertNotIn("inputs.android_only", job_block(workflow, job))
        # The exact condition retains tag builds and default full-platform dispatch.
        for event, android_only, expected_full in (
            ("push", False, True), ("push", True, True),
            ("workflow_dispatch", False, True), ("workflow_dispatch", True, False),
        ):
            self.assertEqual(event != "workflow_dispatch" or android_only is not True, expected_full)

    def test_android_only_publication_guard_is_wired_into_existing_source_validation(self):
        workflow = text("linkremote-release.yml")
        self.assertIn('ANDROID_ONLY: "${{ github.event_name == \'workflow_dispatch\' && inputs.android_only == true }}"', workflow)
        helper = (ROOT / ".github/release_candidate.py").read_text(encoding="utf-8")
        self.assertIn('android_only = os.environ.get("ANDROID_ONLY") == "true"', helper)
        self.assertIn('validate_source(source_sha, os.environ.get("EXPECTED_SOURCE_SHA", ""), version, tag, publish, tag_sha, android_only)', helper)
        self.assertIn("if android_only and publish:", helper)
        self.assertIn("publication is forbidden", helper)
        self.assertIn("python .github/release_candidate.py --check-source", job_block(workflow, "prepare-release"))

    def test_android_signature_parser_keeps_real_verifier_and_alignment_gates(self):
        workflow = text("linkremote-release.yml")
        android = job_block(workflow, "build-rustdesk-android")
        verification = android.split("- name: Verify signed Android candidate and record provenance", 1)[1].split("- name: Upload Artifacts", 1)[0]
        self.assertIn("set -euo pipefail", verification)
        commands = (
            'apksigner" verify --verbose --print-certs "$SIGNED_APK" | tee android-signature.txt',
            'python .github/verify_android_signature.py android-signature.txt --expected-sha256 "$EXPECTED_ANDROID_CERT_SHA256"',
            'zipalign" -c -P 16 -v 4 "$SIGNED_APK"',
            'python tests/verify_android_artifact.py "$SIGNED_APK"',
            'python .github/release_candidate.py --platform android',
        )
        positions = [verification.index(command) for command in commands]
        self.assertEqual(positions, sorted(positions))
        self.assertNotIn("grep -Fx", verification)
        self.assertIn("python .github/tests/test_verify_android_signature.py -v", job_block(workflow, "prepare-release"))

    def test_all_release_uploads_require_explicit_publication(self):
        workflow = text("linkremote-release.yml")
        steps = re.split(r"(?m)^      - ", workflow)
        publishers = [step for step in steps if "uses: softprops/action-gh-release@" in step]
        self.assertEqual(len(publishers), 3)
        for step in publishers:
            self.assertRegex(step, r"(?m)^        if: .*env.PUBLISH_RELEASE == 'true'")
            self.assertIn("target_commitish: ${{ github.sha }}", step)
            self.assertIn("draft: true", step)
            self.assertIn("prerelease: true", step)

    def test_product_release_contains_only_supported_architectures(self):
        workflow = text("linkremote-release.yml")
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
        workflow = text("linkremote-release.yml")
        self.assertIn("--class-name Rustdesk", workflow)
        self.assertIn("web-management:", workflow)
        self.assertIn(
            "python -m unittest discover -s server/linkremote-api/tests -v",
            workflow,
        )
        self.assertIn("LinkRemote-${VERSION}-web-management.zip", workflow)

    def test_candidate_app_versions_are_consistent(self):
        cargo_toml = (ROOT / "Cargo.toml").read_text(encoding="utf-8")
        cargo_lock = (ROOT / "Cargo.lock").read_text(encoding="utf-8")
        flutter = (ROOT / "flutter" / "pubspec.yaml").read_text(encoding="utf-8")
        version = re.search(r'(?m)^version = "([0-9]+\.[0-9]+\.[0-9]+)"$', cargo_toml)
        self.assertIsNotNone(version)
        version = version.group(1)
        self.assertRegex(cargo_lock, rf'(?m)^name = "linkremote"\nversion = "{re.escape(version)}"$')
        self.assertRegex(flutter, rf'(?m)^version: {re.escape(version)}\+[0-9]+$')
        self.assertIn(f'VERSION: "{version}"', text("linkremote-release.yml"))
        portable = (ROOT / "libs" / "portable" / "Cargo.toml").read_text(encoding="utf-8")
        portable_lock = (ROOT / "libs" / "portable" / "Cargo.lock").read_text(encoding="utf-8")
        self.assertIn(f'version = "{version}"', portable)
        self.assertRegex(cargo_lock, rf'(?m)^name = "linkremote-portable-packer"\nversion = "{re.escape(version)}"$')
        self.assertRegex(portable_lock, rf'(?m)^name = "linkremote-portable-packer"\nversion = "{re.escape(version)}"$')

    def test_stable_homepage_and_manifest_are_consistent(self):
        homepage = (ROOT / "server" / "site-root" / "index.html").read_text(encoding="utf-8")
        manifest = json.loads((ROOT / "server" / "linkremote-api" / "release.json").read_text(encoding="utf-8"))
        stable = stable_release_version(homepage, manifest)
        cargo = (ROOT / "Cargo.toml").read_text(encoding="utf-8")
        candidate = re.search(r'(?m)^version = "([0-9]+\.[0-9]+\.[0-9]+)"$', cargo)[1]
        self.assertLessEqual(tuple(map(int, stable.split("."))), tuple(map(int, candidate.split("."))))

    def test_stable_version_can_lag_candidate_but_not_mismatch_downloads(self):
        homepage = 'title: "LinkRemote 远控", sub: "v1.0.42 · 2026-09"'
        manifest = {
            "windows_x64": "https://zperme.top/download/LinkRemote-1.0.42-x86_64.exe",
            "android_arm64": "https://zperme.top/download/LinkRemote-1.0.42-android-signed.apk",
        }
        self.assertEqual(stable_release_version(homepage, manifest), "1.0.42")
        manifest["android_arm64"] = manifest["android_arm64"].replace("1.0.42", "1.0.43")
        with self.assertRaisesRegex(ValueError, "android_arm64"):
            stable_release_version(homepage, manifest)
        with self.assertRaisesRegex(ValueError, "windows_x64"):
            stable_release_version(homepage.replace("1.0.42", "1.0.43"), manifest)

    def test_windows_portable_wrapper_uses_own_brand(self):
        portable = (ROOT / "libs" / "portable" / "Cargo.toml").read_text(encoding="utf-8")
        self.assertIn('ProductName = "LinkRemote"', portable)
        self.assertIn('FileDescription = "LinkRemote Remote Access"', portable)
        self.assertNotIn('RustDesk Remote Desktop', portable)
        self.assertIn('linkremote-portable-packer.exe', text("linkremote-release.yml"))

    def test_product_ci_checks_scope_and_web_without_release_uploads(self):
        workflow = text("linkremote-ci.yml")
        self.assertIn("python .github/tests/test_release_scope.py -v", workflow)
        self.assertIn(
            "python -m unittest discover -s server/linkremote-api/tests -v",
            workflow,
        )
        self.assertNotIn("action-gh-release", workflow)
        self.assertNotIn("flutter-build.yml", workflow)

    def test_product_ci_runs_real_policy_tests_on_both_host_platforms(self):
        workflow = text("linkremote-ci.yml")
        self.assertIn("os: [ubuntu-24.04, windows-latest]", workflow)
        self.assertIn("python tests/run_video_policy_tests.py", workflow)
        self.assertIn("python -m unittest discover -s tests -p test_video_policy_runner.py -v", workflow)


if __name__ == "__main__":
    unittest.main()
