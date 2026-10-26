"""Real temporary SQLite/HTTP session management; no production account actions."""
import json
from pathlib import Path
import sys
import threading
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tests import test_api as existing

app = existing.app


class LoginSessionApiTests(unittest.TestCase):
    request = existing.ApiTestCase.request
    seed_user = existing.ApiTestCase.seed_user
    seed_session = existing.ApiTestCase.seed_session

    def setUp(self):
        existing.ApiTestCase.setUp(self)
        self.smtp = patch.object(app, "SMTP_CONFIG", None)
        self.smtp.start()
        self.addCleanup(self.smtp.stop)
        limiter = patch.object(app, "_session_revoke_attempts", app.account_sessions.RevokeAttemptLimiter())
        limiter.start()
        self.addCleanup(limiter.stop)

    def tearDown(self):
        existing.ApiTestCase.tearDown(self)

    def auth(self, token):
        return {"Authorization": "Bearer " + token}

    def sessions(self, token):
        status, headers, raw = self.request("GET", "/api/sessions", headers=self.auth(token))
        self.assertEqual(status, 200)
        self.assertEqual(headers.get("Cache-Control"), "no-store")
        return json.loads(raw)["sessions"]

    def revoke(self, token, session_id, password="secret1", headers=None):
        return self.request("POST", "/api/sessions/" + session_id + "/revoke",
                            {"password": password}, headers or self.auth(token))

    def login(self, email, name="Laptop", platform="windows", uuid="install-a"):
        self.seed_user(email)
        status, _, raw = self.request("POST", "/api/login", {
            "username": email, "password": "secret1", "id": "123456789",
            "uuid": uuid, "deviceInfo": {"name": name, "os": platform},
        })
        self.assertEqual(status, 200)
        return json.loads(raw)["access_token"]

    def test_list_separates_real_sessions_and_never_exposes_bearer_credentials(self):
        current = self.login("one@example.test", uuid="install-a")
        other = self.login("one@example.test", uuid="install-b")
        outsider = self.login("two@example.test")
        sessions = self.sessions(current)
        self.assertEqual(len(sessions), 2)
        self.assertEqual(sum(s["is_current"] for s in sessions), 1)
        self.assertEqual(len({s["id"] for s in sessions}), 2)
        for entry in sessions:
            self.assertEqual(set(entry), {"id", "device_name", "platform", "created_at", "last_seen_at", "is_current"})
            self.assertEqual(entry["device_name"], "Laptop")
            self.assertEqual(entry["platform"], "windows")
            self.assertTrue(entry["created_at"])
        rendered = json.dumps(sessions)
        for secret in (current, other, outsider, "secret1", "password_hash", "device_uuid"):
            self.assertNotIn(secret, rendered)

    def test_heartbeat_names_only_the_presenting_legacy_session_without_changing_credentials(self):
        current, user_id = self.seed_session("one@example.test")
        other, _ = self.seed_session("one@example.test")
        foreign, _ = self.seed_session("two@example.test")
        with app.db_connect() as conn:
            before = [tuple(r) for r in conn.execute("SELECT token,user_id,session_id,created_at,expires_at FROM tokens ORDER BY token")]
        status, _, _ = self.request("POST", "/api/heartbeat", {
            "id": "123456789", "uuid": "own-installation",
            "deviceInfo": {"name": "  Office\n Laptop ", "os": "windows"},
        }, self.auth(current))
        self.assertEqual(status, 200)
        entries = self.sessions(current)
        own = next(entry for entry in entries if entry["is_current"])
        self.assertEqual((own["device_name"], own["platform"]), ("Office Laptop", "windows"))
        self.assertEqual(next(entry for entry in entries if not entry["is_current"])["device_name"], "")
        self.assertEqual(self.sessions(foreign)[0]["device_name"], "")
        with app.db_connect() as conn:
            after = [tuple(r) for r in conn.execute("SELECT token,user_id,session_id,created_at,expires_at FROM tokens ORDER BY token")]
            self.assertEqual(conn.execute("SELECT device_uuid FROM tokens WHERE token=?", (current,)).fetchone()[0], "own-installation")
        self.assertEqual(before, after)

    def test_current_user_fallback_also_names_the_presenting_session(self):
        current, _ = self.seed_session("one@example.test")
        status, _, _ = self.request("POST", "/api/currentUser", {
            "id": "123456789", "uuid": "own-installation",
            "deviceInfo": {"name": "Existing laptop", "os": "windows"},
        }, self.auth(current))
        self.assertEqual(status, 200)
        self.assertEqual(self.sessions(current)[0]["device_name"], "Existing laptop")

    def test_heartbeat_can_rename_same_installation_but_not_erase_or_reattribute(self):
        token = self.login("one@example.test", name="Before", uuid="installation-a")
        def heartbeat(uuid, info):
            return self.request("POST", "/api/heartbeat", {"id":"123456789", "uuid":uuid,"deviceInfo":info}, self.auth(token))[0]
        self.assertEqual(heartbeat("installation-a", {"name":"Renamed", "os":"windows"}), 200)
        self.assertEqual(self.sessions(token)[0]["device_name"], "Renamed")
        self.assertEqual(heartbeat("installation-a", {"name":"", "os":""}), 200)
        self.assertEqual((self.sessions(token)[0]["device_name"], self.sessions(token)[0]["platform"]), ("Renamed", "windows"))
        self.assertEqual(heartbeat("installation-b", {"name":"Wrong installation", "os":"android"}), 200)
        self.assertEqual((self.sessions(token)[0]["device_name"], self.sessions(token)[0]["platform"]), ("Renamed", "windows"))

    def test_readonly_list_does_not_guess_names_from_registered_peers(self):
        token, owner = self.seed_session("one@example.test")
        with app.db_connect() as conn:
            app.upsert_device(conn, owner, "123456789", "unrelated-installation", {"name":"Do not infer", "os":"windows"})
        entry = self.sessions(token)[0]
        self.assertEqual((entry["device_name"], entry["platform"]), ("", ""))
        with app.db_connect() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM devices").fetchone()[0], 1)

    def test_revoke_only_selected_noncurrent_session_and_keep_other_tokens(self):
        current = self.login("one@example.test", uuid="install-a")
        target = self.login("one@example.test", uuid="install-b")
        outsider, _ = self.seed_session("two@example.test")
        chosen = next(s for s in self.sessions(current) if not s["is_current"])
        status, _, raw = self.revoke(current, chosen["id"])
        self.assertEqual((status, json.loads(raw)), (200, {"revoked": True}))
        for token, expected in ((target, 401), (current, 200), (outsider, 200)):
            actual, _, _ = self.request("GET", "/api/sessions", headers=self.auth(token))
            self.assertEqual(actual, expected)
        self.assertEqual(len(self.sessions(current)), 1)

    def test_current_session_is_not_revocable_even_with_correct_password(self):
        token, _ = self.seed_session("one@example.test")
        current = self.sessions(token)[0]
        self.assertEqual(self.revoke(token, current["id"])[0], 409)
        self.assertEqual(len(self.sessions(token)), 1)

    def test_wrong_password_and_cross_account_do_not_revoke_or_enumerate(self):
        current, _ = self.seed_session("one@example.test")
        other = self.login("one@example.test")
        foreign, _ = self.seed_session("two@example.test")
        target = next(s for s in self.sessions(current) if not s["is_current"])
        foreign_id = self.sessions(foreign)[0]["id"]
        self.assertEqual(self.revoke(current, target["id"], "wrong")[0], 403)
        self.assertEqual(self.revoke(current, foreign_id)[0], 404)
        self.assertEqual(self.revoke(current, "unknown-opaque-session-id")[0], 404)
        self.assertEqual(len(self.sessions(other)), 2)

    def test_password_attempts_lock_for_fifteen_minutes_and_do_not_affect_other_user(self):
        current, _ = self.seed_session("one@example.test")
        other = self.login("one@example.test")
        foreign, _ = self.seed_session("two@example.test")
        target = next(s for s in self.sessions(current) if not s["is_current"])["id"]
        with patch("time.time", return_value=10000):
            for _ in range(5):
                self.assertEqual(self.revoke(current, target, "wrong")[0], 403)
            self.assertEqual(self.revoke(current, target)[0], 429)
            self.assertEqual(len(self.sessions(foreign)), 1)
        with patch("time.time", return_value=10899):
            self.assertEqual(self.revoke(current, target)[0], 429)
        with patch("time.time", return_value=10901):
            self.assertEqual(self.revoke(current, target)[0], 200)
        self.assertEqual(self.request("GET", "/api/sessions", headers=self.auth(other))[0], 401)

    def test_cookie_revoke_requires_same_origin_before_consuming_password(self):
        token, _ = self.seed_session("one@example.test")
        self.login("one@example.test")
        chosen = next(s for s in self.sessions(token) if not s["is_current"])
        cookie = {"Cookie": app.WEB_COOKIE_NAME + "=" + token}
        self.assertEqual(self.revoke(token, chosen["id"], headers=cookie)[0], 403)
        same_origin = {**cookie, "Origin": "http://127.0.0.1:" + str(self.port)}
        self.assertEqual(self.revoke(token, chosen["id"], headers=same_origin)[0], 200)

    def test_expired_and_invalidated_tokens_cannot_list_or_revoke(self):
        token, _ = self.seed_session("one@example.test")
        expired, user_id = self.seed_session("one@example.test")
        with app.db_connect() as conn:
            conn.execute("UPDATE tokens SET expires_at='2000-01-01T00:00:00.000Z' WHERE token=?", (expired,))
        self.assertEqual(len(self.sessions(token)), 1)
        self.assertEqual(self.request("GET", "/api/sessions", headers=self.auth(expired))[0], 401)
        self.assertEqual(self.revoke(expired, "unknown-opaque-session-id")[0], 401)
        self.assertEqual(self.request("GET", "/api/sessions")[0], 401)

    def test_malformed_password_has_no_server_error_or_side_effect(self):
        token, _ = self.seed_session("one@example.test")
        self.login("one@example.test")
        target = next(s for s in self.sessions(token) if not s["is_current"])["id"]
        for password in (None, [], {}, 123, ""):
            self.assertEqual(self.revoke(token, target, password)[0], 400)
        self.assertEqual(len(self.sessions(token)), 2)

    def test_metadata_is_bounded_and_last_seen_is_not_device_heartbeat(self):
        token = self.login("one@example.test", "X" * 200, "w" * 90, "u" * 300)
        with app.db_connect() as conn:
            before = dict(conn.execute("SELECT * FROM tokens WHERE token=?", (token,)).fetchone())
        self.assertEqual((len(before["device_name"]), len(before["platform"]), len(before["device_uuid"])), (80, 32, 128))
        with patch.object(app, "now_iso", return_value="2026-11-01T00:00:00.000Z"):
            entry = self.sessions(token)[0]
        self.assertEqual(entry["last_seen_at"], "2026-11-01T00:00:00.000Z")
        self.assertEqual(entry["created_at"], before["created_at"])
        with app.db_connect() as conn:
            device = conn.execute("SELECT last_login FROM devices LIMIT 1").fetchone()
        self.assertNotEqual(device["last_login"], entry["last_seen_at"])

    def test_oversized_password_body_is_rejected_without_consuming_attempts(self):
        token, _ = self.seed_session("one@example.test")
        self.login("one@example.test")
        target = next(s for s in self.sessions(token) if not s["is_current"])["id"]
        self.assertEqual(self.revoke(token, target, "x" * 5000)[0], 413)
        self.assertEqual(self.revoke(token, target)[0], 200)

    def test_logout_between_authentication_and_transaction_cannot_revoke(self):
        token, _ = self.seed_session("one@example.test")
        other = self.login("one@example.test")
        target = next(s for s in self.sessions(token) if not s["is_current"])["id"]
        original = app.Handler._auth_user

        def authenticate_then_logout(handler):
            user = original(handler)
            with app.db_connect() as conn:
                conn.execute("DELETE FROM tokens WHERE token=?", (token,))
            return user

        with patch.object(app.Handler, "_auth_user", authenticate_then_logout):
            self.assertEqual(self.revoke(token, target)[0], 401)
        self.assertEqual(len(self.sessions(other)), 1)

    def test_concurrent_duplicate_revokes_delete_only_one_target(self):
        token, _ = self.seed_session("one@example.test")
        self.login("one@example.test")
        target = next(s for s in self.sessions(token) if not s["is_current"])["id"]
        barrier = threading.Barrier(2)
        statuses, errors = [], []

        def revoke_once():
            try:
                barrier.wait(timeout=3)
                statuses.append(self.revoke(token, target)[0])
            except Exception as error:
                errors.append(error)

        workers = [threading.Thread(target=revoke_once) for _ in range(2)]
        for worker in workers:
            worker.start()
        for worker in workers:
            worker.join(timeout=5)
        self.assertFalse(errors)
        self.assertEqual(sorted(statuses), [200, 404])
        self.assertEqual(len(self.sessions(token)), 1)

    def test_success_does_not_erase_other_failed_attempts(self):
        limiter = app.account_sessions.RevokeAttemptLimiter()
        failed = limiter.begin((1, "source"))
        limiter.finish((1, "source"), failed, True)
        successful = limiter.begin((1, "source"))
        limiter.finish((1, "source"), successful, False)
        for _ in range(4):
            self.assertIsNotNone(limiter.begin((1, "source")))
        self.assertIsNone(limiter.begin((1, "source")))
        self.assertIsNotNone(limiter.begin((2, "source")))

    def test_concurrent_password_attempt_reservations_are_bounded(self):
        limiter = app.account_sessions.RevokeAttemptLimiter()
        barrier = threading.Barrier(12)
        reservations = []

        def reserve():
            barrier.wait(timeout=3)
            reservations.append(limiter.begin((1, "source")))

        workers = [threading.Thread(target=reserve) for _ in range(12)]
        for worker in workers:
            worker.start()
        for worker in workers:
            worker.join(timeout=5)
        self.assertEqual(sum(item is not None for item in reservations), 5)

    def test_legacy_schema_reinitialization_keeps_token_and_expiry(self):
        token, user_id = self.seed_session("legacy@example.test")
        with app.db_connect() as conn:
            original = dict(conn.execute("SELECT * FROM tokens WHERE token=?", (token,)).fetchone())
            conn.execute("ALTER TABLE tokens RENAME TO old_tokens")
            conn.execute("CREATE TABLE tokens(token TEXT PRIMARY KEY,user_id INTEGER NOT NULL,created_at TEXT NOT NULL,expires_at TEXT NOT NULL)")
            conn.execute("INSERT INTO tokens VALUES (?,?,?,?)", (token, user_id, original["created_at"], original["expires_at"]))
            conn.execute("DROP TABLE old_tokens")
        app.init_db()
        with app.db_connect() as conn:
            first_id = conn.execute("SELECT session_id FROM tokens WHERE token=?", (token,)).fetchone()[0]
        app.init_db()
        with app.db_connect() as conn:
            migrated = dict(conn.execute("SELECT * FROM tokens WHERE token=?", (token,)).fetchone())
        self.assertIn("session_id", migrated)
        self.assertEqual(migrated["token"], token)
        self.assertEqual(migrated["expires_at"], original["expires_at"])
        self.assertEqual(migrated["session_id"], first_id)
        entries = self.sessions(token)
        self.assertEqual(len(entries), 1)
        self.assertTrue(entries[0]["is_current"])
        self.assertEqual(entries[0]["device_name"], "")
        self.assertNotEqual(entries[0]["id"], token)

    def test_old_server_can_create_multiple_tokens_after_code_only_rollback(self):
        user_id = self.seed_user("rollback@example.test")
        with app.db_connect() as conn:
            # This is the INSERT used by the actual pre-migration API, not the
            # new create_session helper. Additive schema must remain writable.
            for token in ("legacy-rollback-one", "legacy-rollback-two"):
                conn.execute("INSERT INTO tokens(token,user_id,created_at,expires_at) VALUES (?,?,?,?)",
                    (token, user_id, app.now_iso(), app.expires_in_iso(86400)))
        app.init_db()
        self.assertEqual(len(self.sessions("legacy-rollback-one")), 2)


if __name__ == "__main__":
    unittest.main()
