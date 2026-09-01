# LinkRemote Release Platform Scope Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make Windows x64, Android arm64-v8a, and the Web management package the only automatic release outputs.

**Architecture:** Keep upstream workflow files for future comparison but make them manual-only. Turn `remote-desk-build.yml` into the sole tag release workflow, delete unreachable architecture branches from that workflow, and enforce the product scope with dependency-free Python contract tests.

**Tech Stack:** GitHub Actions YAML, Python 3 `unittest`, existing Rust/Flutter build toolchains.

**Spec:** `docs/superpowers/specs/2026-09-01-linkremote-performance-platform-web-management-design.md`

## Global Constraints

- Automatic tag releases produce Windows x64, Android arm64-v8a, and the Web account/device management package only.
- macOS, Linux, iOS, F-Droid, Android 32-bit, and emulator packages are not automatically built or released.
- Upstream source and reusable workflows remain in the repository; only triggers and product matrices are restricted.
- `remote-desk-build.yml` remains the sole tag-triggered release workflow.
- Do not pin the server image until the running production image version or digest has been read under separate deployment authorization.
- No release tag, upload, or deployment is performed by this plan.

---

### Task 1: Restrict upstream workflows to manual execution

**Files:**
- Create: `.github/tests/__init__.py`
- Create: `.github/tests/test_release_scope.py`
- Modify: `.github/workflows/flutter-tag.yml:3-11`
- Modify: `.github/workflows/flutter-nightly.yml:3-8`
- Modify: `.github/workflows/fdroid.yml:3-11`
- Modify: `.github/workflows/flutter-ci.yml:3-20`

**Interfaces:**
- Consumes: workflow text under `.github/workflows/`.
- Produces: trigger tests without a YAML dependency and manual-only upstream workflows.

- [ ] **Step 1: Write the workflow text helpers and failing tests**

```python
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
        for name in ("flutter-tag.yml", "flutter-nightly.yml", "fdroid.yml", "flutter-ci.yml"):
            block = on_block(text(name))
            self.assertIn("workflow_dispatch:", block, name)
            self.assertNotIn("push:", block, name)
            self.assertNotIn("pull_request:", block, name)
            self.assertNotIn("schedule:", block, name)

    def test_only_product_release_responds_to_version_tags(self):
        block = on_block(text("remote-desk-build.yml"))
        self.assertIn("push:", block)
        self.assertIn("v[0-9]+.[0-9]+.[0-9]+", block)

if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run and verify RED**

Run: `python .github/tests/test_release_scope.py -v`

Expected: the manual-only test fails because the current workflows still have push, pull-request, or schedule triggers; the product-release tag test passes.

- [ ] **Step 3: Replace each upstream trigger block**

Use exactly:

```yaml
on:
  workflow_dispatch:
```

Keep the existing jobs unchanged so maintainers can still run an upstream comparison manually.

- [ ] **Step 4: Run and verify GREEN**

Run: `python .github/tests/test_release_scope.py -v`

Expected: 2 tests pass.

- [ ] **Step 5: Commit**

```bash
git add .github/tests .github/workflows/flutter-tag.yml .github/workflows/flutter-nightly.yml .github/workflows/fdroid.yml .github/workflows/flutter-ci.yml
git commit -m "ci: make upstream platform workflows manual"
```

### Task 2: Remove dead architecture branches from the product release

**Files:**
- Modify: `.github/workflows/remote-desk-build.yml`
- Test: `.github/tests/test_release_scope.py`

**Interfaces:**
- Consumes: current x64 Windows and aarch64 Android jobs.
- Produces: one bridge artifact, one Windows target, one Android target, and no unsupported target strings.

- [ ] **Step 1: Add a failing supported-architecture test**

```python
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
```

- [ ] **Step 2: Run and verify RED**

Run: `python .github/tests/test_release_scope.py ReleaseScopeTest.test_product_release_contains_only_supported_architectures -v`

Expected: the unsupported target strings still present in dead workflow branches make the test fail.

- [ ] **Step 3: Simplify bridge generation to one toolchain**

Replace the bridge matrix with a single job configuration:

```yaml
matrix:
  job:
    - target: x86_64-unknown-linux-gnu
      os: ubuntu-22.04
      flutter-version: "3.22.3"
      artifact-name: bridge-artifact
```

Remove `FLUTTER_WINDOWS_ARM_VERSION`, the Flutter 3.44 bridge artifact, 3.44 source-patch conditionals, and Windows arm64 cache/SDK branches. Keep the Windows x64 Flutter 3.24.5 path unchanged.

- [ ] **Step 4: Reduce Windows job to x64-only literals**

Keep only:

```yaml
target: x86_64-pc-windows-msvc
arch: x86_64
flutter-arch: x64
vcpkg-triplet: x64-windows-static
build-args: "--vram"
```

Replace matrix expressions that choose arm64 artifacts/platforms with x64 literals. Remove ARM64-only Dart, vcpkg, SODIUM, topmost-window, and MSI branches. The final files remain `LinkRemote-${VERSION}-x86_64.exe` and `.msi`.

- [ ] **Step 5: Reduce Android shell branches to arm64-only commands**

Replace architecture `case` blocks with:

```bash
ANDROID_TARGET=arm64-v8a
./flutter/build_android_deps.sh "${ANDROID_TARGET}"
./flutter/ndk_arm64.sh
mkdir -p ./flutter/android/app/src/main/jniLibs/arm64-v8a
cp ./target/aarch64-linux-android/release/liblibrustdesk.so ./flutter/android/app/src/main/jniLibs/arm64-v8a/librustdesk.so
```

Build Flutter with `--target-platform android-arm64 --split-per-abi` and keep the release filename `LinkRemote-${VERSION}-android.apk`.

- [ ] **Step 6: Run the architecture contract**

Run: `python .github/tests/test_release_scope.py ReleaseScopeTest.test_product_release_contains_only_supported_architectures -v`

Expected: 1 test passes and unsupported target strings are absent from the product workflow.

- [ ] **Step 7: Review the resulting job dependency graph**

Run: `rg -n '^  [A-Za-z0-9_-]+:|needs:' .github/workflows/remote-desk-build.yml`

Expected: bridge, Windows helper, Windows x64, and Android arm64 jobs reference only existing job names.

- [ ] **Step 8: Commit**

```bash
git add .github/workflows/remote-desk-build.yml .github/tests/test_release_scope.py
git commit -m "ci: remove unsupported release architectures"
```

### Task 3: Test and package the Web management server in the release workflow

**Files:**
- Modify: `.github/workflows/remote-desk-build.yml`
- Test: `.github/tests/test_release_scope.py`

**Interfaces:**
- Consumes: completed source from `server/linkremote-api/` and its `unittest` suite.
- Produces: `web-management` job and `LinkRemote-${VERSION}-web-management.zip` release asset.

- [ ] **Step 1: Add a failing Web package contract**

```python
def test_product_release_packages_web_management(self):
    workflow = text("remote-desk-build.yml")
    self.assertIn("web-management", workflow)
    self.assertIn("python -m unittest discover -s server/linkremote-api/tests -v", workflow)
```

- [ ] **Step 2: Run and verify RED**

Run: `python .github/tests/test_release_scope.py ReleaseScopeTest.test_product_release_packages_web_management -v`

Expected: the test fails because no Web management job exists.

- [ ] **Step 3: Add the Web management job**

```yaml
web-management:
  runs-on: ubuntu-24.04
  steps:
    - name: Checkout source code
      uses: actions/checkout@34e114876b0b11c390a56381ad16ebd13914f8d5
      with:
        submodules: recursive
    - name: Test Web management API
      run: python -m unittest discover -s server/linkremote-api/tests -v
    - name: Compile Python sources
      run: python -m py_compile server/linkremote-api/app.py server/linkremote-api/reset_password.py
    - name: Package Web management server
      run: zip -r "LinkRemote-${VERSION}-web-management.zip" server/linkremote-api server/nginx server/README.md -x '*/data/*' '*/smtp.json' '*/__pycache__/*'
    - name: Upload Web management artifact
      uses: actions/upload-artifact@043fb46d1a93c77aae656e7c1c64a875d1fc6a0a
      with:
        name: web-management
        path: LinkRemote-${{ env.VERSION }}-web-management.zip
    - name: Publish Web management release asset
      uses: softprops/action-gh-release@de2c0eb89ae2a093876385947365aca7b0e5f844
      with:
        prerelease: true
        tag_name: ${{ env.TAG_NAME }}
        files: LinkRemote-${{ env.VERSION }}-web-management.zip
```

- [ ] **Step 4: Run the Web packaging contract**

Run: `python .github/tests/test_release_scope.py ReleaseScopeTest.test_product_release_packages_web_management -v`

Expected: 1 test passes.

- [ ] **Step 5: Create the archive locally and inspect it**

Run on a shell with `zip`:

```bash
VERSION=1.0.35
zip -r "LinkRemote-${VERSION}-web-management.zip" server/linkremote-api server/nginx server/README.md -x '*/data/*' '*/smtp.json' '*/__pycache__/*'
unzip -l "LinkRemote-${VERSION}-web-management.zip"
```

Expected: source, static assets, nginx example, and README are present; database, live SMTP config, and bytecode are absent. Remove only this generated local archive after inspection.

- [ ] **Step 6: Commit**

```bash
git add .github/workflows/remote-desk-build.yml .github/tests/test_release_scope.py
git commit -m "ci: package web management release"
```

### Task 4: Add lightweight product CI without publishing other platforms

**Files:**
- Create: `.github/workflows/linkremote-ci.yml`
- Modify: `.github/tests/test_release_scope.py`

**Interfaces:**
- Consumes: release-scope tests and Web API tests.
- Produces: push/PR checks that do not build or upload macOS, Linux, iOS, F-Droid, or unsupported Android ABIs.

- [ ] **Step 1: Extend the contract with CI job assertions**

```python
def test_product_ci_checks_scope_and_web_without_release_uploads(self):
    workflow = text("linkremote-ci.yml")
    self.assertIn("python .github/tests/test_release_scope.py -v", workflow)
    self.assertIn("python -m unittest discover -s server/linkremote-api/tests -v", workflow)
    self.assertNotIn("action-gh-release", workflow)
    self.assertNotIn("flutter-build.yml", workflow)
```

- [ ] **Step 2: Run and verify RED**

Run: `python .github/tests/test_release_scope.py ReleaseScopeTest.test_product_ci_checks_scope_and_web_without_release_uploads -v`

Expected: error because `linkremote-ci.yml` does not exist.

- [ ] **Step 3: Create the product CI workflow**

```yaml
name: LinkRemote Product CI

on:
  workflow_dispatch:
  pull_request:
    paths:
      - '.github/**'
      - 'server/**'
      - 'src/server/video_policy.rs'
      - 'src/server/video_qos.rs'
      - 'libs/scrap/src/common/**'
  push:
    branches: [master]
    paths:
      - '.github/**'
      - 'server/**'
      - 'src/server/video_policy.rs'
      - 'src/server/video_qos.rs'
      - 'libs/scrap/src/common/**'

jobs:
  scope-and-web:
    runs-on: ubuntu-24.04
    steps:
      - uses: actions/checkout@34e114876b0b11c390a56381ad16ebd13914f8d5
        with:
          submodules: recursive
      - run: python .github/tests/test_release_scope.py -v
      - run: python -m unittest discover -s server/linkremote-api/tests -v
      - run: python -m py_compile server/linkremote-api/app.py server/linkremote-api/reset_password.py
```

This workflow validates policy and Web contracts only; full Windows/Android compilation remains in the release workflow where their native dependencies are already configured.

- [ ] **Step 4: Run and verify GREEN**

Run: `python .github/tests/test_release_scope.py -v`

Expected: every release-scope test passes.

- [ ] **Step 5: Commit**

```bash
git add .github/workflows/linkremote-ci.yml .github/tests/test_release_scope.py
git commit -m "ci: add focused product checks"
```

### Task 5: Run final static verification without releasing

**Files:**
- Verify only; modify files only to fix an observed failure.

**Interfaces:**
- Consumes: all release-plan tasks.
- Produces: fresh evidence that workflow scope and Web packaging contracts hold.

- [ ] **Step 1: Run all dependency-free checks**

```bash
python .github/tests/test_release_scope.py -v
python -m unittest discover -s server/linkremote-api/tests -v
python -m py_compile server/linkremote-api/app.py server/linkremote-api/reset_password.py
git diff --check
```

Expected: zero failed tests, Python compilation exits 0, and diff check exits 0.

- [ ] **Step 2: Inspect automatic trigger ownership**

Run:

```bash
rg -n '^on:|^  push:|^  pull_request:|^  schedule:|^  workflow_dispatch:' .github/workflows/flutter-tag.yml .github/workflows/flutter-nightly.yml .github/workflows/fdroid.yml .github/workflows/flutter-ci.yml .github/workflows/remote-desk-build.yml .github/workflows/linkremote-ci.yml
```

Expected: only `remote-desk-build.yml` has the version-tag release trigger; `linkremote-ci.yml` has branch/PR checks; upstream workflows are manual-only.

- [ ] **Step 3: Inspect the final working tree**

Run: `git status --short`

Expected: no generated archive, database, SMTP secret, Python bytecode, release tag, or unrelated file is present.
