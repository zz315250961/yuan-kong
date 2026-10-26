# Device names and bounded lists Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans; preserve the user's chosen inline implementation and one final unified review.

**Goal:** Show reliable login-device names, actual totals/visible counts and collapsed long lists in the existing My workspace.

**Architecture:** Reuse authenticated device heartbeat/currentUser data to enrich only the presenting login session. Reuse one small disclosure widget for login sessions and embedded remote-device records, retaining the existing row actions and popup's independently scrollable layout.

**Tech Stack:** Flutter/Dart; existing Python/SQLite API; no dependencies.

**Spec:** User's2026-10-26 annotated screenshot and explicit name/count/folding request; existing `docs/superpowers/specs/2026-10-25-account-workspace-design.md` remains authority outside this delta. Builder assumption: show5 rows initially, Expand all/Collapse and `共N条，显示M条` (remote records use台); this is a reversible detail, not a new product direction. Pagination/row-count menus add unnecessary controls for this requested small refinement.

## Global Constraints

- No guessed device associations from timestamps, IPs, matching names or other device records. Old metadata-less inactive sessions remain clearly labeled; current and newly reporting sessions show actual names.
- Authenticated metadata updates target only the presenting token, preserve token/opaque ID/owner/created/expiry, never overwrite another installation's nonempty UUID and never erase good name/platform with blank input.
- GET sessions/read-only device entry performs no registration or metadata inference. Existing current-session revoke refusal, password confirmation, readOnly/incoming-only and settings protections remain.
- Native incumbent colors, controls, two main tabs and movable popup are unchanged. Bounded layout checks: light/dark700x600/text1.3 plus normal desktop.
- Existing production1.0.46 and current remote session are not automatically restarted or overwritten. Publishing/installing a new immutable version is a separate delivery gate.

## Review Focus

1. Empty/overlong/mismatched UUID or metadata: do not erase/reattribute sessions (Task1).
2. Concurrent refresh/revoke/account changes: collapse state must not leak into another account; existing handlers still target exact opaque IDs (Task2).
3. Current session appears beyond fifth position: pin it first in the collapsed view (Task2).
4. Counts0/1/5/8 and reduced data after refresh: honest totals, hidden toggle at<=5, no phantom rows (Task2).
5. Long Chinese names at700x600/text1.3: bounded rows, readable count/toggle, no overflow; popup list remains scrollable (Task2).

### Task1: Current authenticated session name

Files: `server/linkremote-api/account_sessions.py`, `app.py`, `tests/test_account_sessions.py`.
Interface: `refresh_session_metadata(conn,user_id,token,body,now)` updates display-only columns for the exact unexpired token/user, with a stable installation UUID guard; heartbeat and existing currentUser use it inside their transaction.
- [ ] Write HTTP RED tests: legacy current-only enrichment/unchanged credentials, rename without blank erasure or UUID reassignment, currentUser compatibility, GET no inference.
- [ ] Run focused tests; implement minimal update; run focused GREEN and backend full suite once after final edits.

### Task2: Disclosure/count UI

Files: new `flutter/lib/common/widgets/device_list_disclosure.dart`; modify `account_workspace.dart`, `my_devices.dart`; new `flutter/test/device_list_disclosure_test.dart`, focused workspace tests.
Interface: `DeviceListDisclosure(count,unit,resetKey,builder(context,visibleCount))`; default limit5, expansion explicit, collapse on changed data scope. Login current row pinned first; original row callbacks stay intact. Embedded remote list uses the same widget; popup keeps bounded scrolling.
- [ ] Write workspace RED tests for8 rows/default5/count/expand/collapse/current-first, and truthful legacy-name fallback.
- [ ] Implement disclosure and both integrations; test0/1/5/8 and scope-reset, actual callback target and small-window layout.
- [ ] Run relevant readonly/navigation tests, targeted analyze, then Flutter full suite once; capture one synthetic production-component batch and at most one necessary correction/confirmation.

### Task3: Unified review and delivery record

- [ ] One fresh unified reviewer; one author fix pass for Important/Critical; document deferred Minor and boundaries.
- [ ] Record actual tests/source status and update deployment ledger with backup, preserving other history. Do not report source changes as already installed or deployed.
