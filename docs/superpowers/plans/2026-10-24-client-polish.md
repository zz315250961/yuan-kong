# Client polish and release Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 整理客户端界面，验证并安装新版本，安全更新公开下载入口，同时准备免费的 iOS 本机签名依赖。

**Architecture:** 保留现有 Flutter 业务组件和回调，只调整呈现布局。Apple 依赖安装独立于客户端修改；发行沿用 source-pinned candidate CI 及可回滚的下载入口切换。

**Tech Stack:** Flutter 3.24.5 / Dart 3.5.4、PowerShell、GitHub Actions、现有 SSH 部署。

**Spec:** `docs/superpowers/plans/2026-10-24-client-polish-spec.md`

## Global Constraints

- 只整理 Windows 首页及共享“我的设备”面板；不改远控协议、账户 API、网页管理界面或全局主题。
- 保留 incoming-only/outgoing-only、自动完成、连接及设备操作的现有行为。
- 新版本为 1.0.45+77；保留 1.0.44 文件、用户配置和线上回滚路径。
- 未签名工具等待风险确认；不输入 Apple 凭据，不自动重启。
- 无 iPhone 真机时，不宣称通过真机签名、远控、双向语音或 120FPS 验收。

## Review Focus

- 700×600 与 1.3 倍文字：主操作可见且无 RenderFlex 溢出。
- 深色主题：内容、边框及主次按钮仍可区分。
- incoming-only/outgoing-only：仅保留模式允许的内容。
- 窄屏与超长设备名：设备操作不被挤出，触控目标至少 48 像素。
- 连接输入与已有设备：保留回调及原有状态，不因美化改变安全/连接流程。

### Task 1: Existing client presentation

**Files:** Modify `flutter/lib/desktop/pages/desktop_home_page.dart`, `flutter/lib/desktop/pages/connection_page.dart`, `flutter/lib/common/widgets/my_devices.dart`; create a focused presentation layout file only if needed, and `flutter/test/linkremote_home_layout_test.dart` / `flutter/test/linkremote_my_devices_layout_test.dart` as appropriate.

**Interfaces:** Consumes current widget slots and callbacks. Produces production-used layout components without FFI logic changes; tests and synthetic screenshots consume the same components.

- [ ] Write failing behavioral layout tests: viewport matrix, dark, text scaling, compact devices and reachable actions; run focused tests and record RED.
- [ ] Implement grouped left panel, responsive connection card, explicit My devices entry and 44px primary action; preserve all business callbacks. Refine shared device hierarchy with 48px actions.
- [ ] Run focused tests GREEN and full `flutter test --no-pub`; run targeted analyze and capture one batch of representative synthetic screenshots.
- [ ] Self-review, commit only task files, write report with exact test evidence and capture paths.

### Task 2: Signed Apple dependency preparation

**Files:** Create helpers and logs only under `../artifacts/ios-tools-20261024/`.

**Interfaces:** Consumes official downloaded EXEs and known SHA256 values; produces installer exit codes, Apple installed package/service inventory and any required human action.

- [ ] Recheck exact SHA256 and valid Apple Authenticode; install iTunes then iCloud with verified `/quiet /norestart /log` switches and human UAC.
- [ ] Inspect exit codes, installed dependency versions and Apple Mobile Device Service. Report reboot/UAC requirements accurately; never execute unsigned tools while consent pending.

### Task 3: Version, packaging and local upgrade

**Files:** Existing release version files and `docs/qa/1.0.45-validation.md`; generated artifacts under `../artifacts/v1.0.45-candidate/`.

**Interfaces:** Consumes reviewed Task 1 commit; produces source-pinned candidate run, verified artifacts and local installed version/service evidence.

- [ ] Update consistent release metadata to 1.0.45+77 and run `.github/tests/test_release_scope.py` plus release metadata tests.
- [ ] Commit source, push only the existing user fork branch and trigger candidate-only workflow; verify all required jobs and download artifacts with immutable run/source evidence.
- [ ] Verify hashes/platform identity; back up existing local app/configuration, install the new package without deleting configuration, validate installed version and Auto/Running service.

### Task 4: Download cutover and handoff

**Files:** Existing release manifest/site source and validation record; production download files, aliases, root website and release manifest only.

**Interfaces:** Consumes verified Task 3 artifacts; produces versioned download URLs, consistent public metadata and rollback backup.

- [ ] Back up exact current production pointers and metadata, stage new immutable packages and verify hashes before switching.
- [ ] Atomically update download aliases and metadata; do not modify API code, database or accounts.
- [ ] Download and hash-check the five public versioned/stable Windows/Android URLs; verify website/version API/health, retain prior packages, record actual limitations.
