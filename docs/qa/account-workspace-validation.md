# Account workspace validation — 2026-10-26

Scope: approved combination A account summary + B full-width lists + C clear login/remote-device distinction, top Remote/My main pages, settings inside My, movable desktop device shortcut. Source changes are not evidence of an installed native upgrade.

## Verified locally

- Backend initial9 RED for missing schema/routes; Task1 41 passed on2026-10-25. On2026-10-26 a real old-API INSERT regression failed against the full unique index, then passed after a nonempty-only unique index. This protects code-only rollback. Existing token values and expiry are preserved.
- Client DTO/controller and account-switch/API-change/dispose guard tests34 passed on2026-10-25. The actual UserModel adapters are used by the new workspace; no credential is a view-model field.
- New movable-dialog tests and existing home/read-only tests25 passed on2026-10-26. Only this desktop dialog changes containers; original Android CustomAlertDialog and immutable readOnly/no-registration boundary retained.
- Native workspace/navigation/confirmation tests4 passed; layout tests8 passed across700x600/text1.3,1000x700,1280x800, light/dark. Captures are **synthetic Flutter widget renderings**, not an installed application or hardware acceptance. First batch and one consolidated correction/confirmation retained at `.impeccable/review/native-round1|native-round2`. The shadow debug-variable harness defect was corrected; final full suite below is green.
- Flutter full suite196 passed on2026-10-26. Targeted analysis: one existing desktop_tab_page.dart deprecated `colorScheme.background` info, no new warning/error in the nine targeted files. Prior release's21 analyzer diagnostics remain historical; this is not a clean whole-repo analyzer claim.
- Backend47 passed with temporary SQLite/loopback including5 disposable deployment-helper tests. Root-level CI invocation also47 passed. Helper default is dry-run, checks exact file inventory/hash, private backup, additive migration, token preservation, rollback code without restoring live DB. Windows test fixture explicitly closes SQLite handles.
- Real temporary-server browser smoke passed: session list/current refusal, wrong-password clear, selected-token revoke/other-token retention/peer retention; existing email/register/reset and late-account200/401 isolation,320–1440 overflow. Browser captures are synthetic test accounts. No production account logout/delete/reset/mail was performed.
- Release scope16 passed; packaging13 tests with1 local skip (Linux zip required); Android signature-parser36 passed. A future exact-source official build must resolve that Linux packaging gate.

## Network and continuity

2026-10-25 ordinary default access recovered before any configuration mutation. Two ordinary HTTPS/SSH rounds approximately70seconds apart passed; original Merge/TUN hashes and process identities unchanged. Logs do not explain the spontaneous recovery.2026-10-26 preflight again passed ordinary SSH strict host-key and HTTPS health200/strict TLS. No interface PATCH, proxy bypass, firewall/route change, profile reload or connection clear. **Permanent timeout cause/fix is not proven.** Original private Merge backup and rollback helper remain preserved, unapplied.

## Server update and delivery gates

User explicitly authorized server update2026-10-26. Current preflight found account API PM2 fork process, `/opt/linkremote-api/app.py` SHA6ff380d87184006929bd95c218917ef2bb87e87cca15874576370d90663fb0c7; release manifest still the previous1.0.45 value. New API/web cutover is pending independent final review, private server code/SQLite backup and dry-run. Only account API may restart; rendezvous/relay and current Windows client must not be restarted. SMTP and release manifest are untouched by this helper. Any client download change requires separately verified immutable candidate artifacts; keep old files and rollback.

The completed1.0.45 publication/email dated2026-10-25 is historical; it is not re-executed or re-emailed. New1.0.46+78 source/candidate must not be described as installed or accepted before the corresponding gates. No Windows upgrade/reboot or unsigned third-party iOS tool execution is authorized by this server update. Windows unsigned, iOS signing/install and previous real-device black-stripe/duplex-voice/right-click/120FPS exclusions remain honest.

Independent final review, actual deployment backup/result, public verification and any new completion-mail acceptance must be appended with their real outcomes before a completion claim.
