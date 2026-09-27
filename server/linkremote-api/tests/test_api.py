import http.client
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import unittest

API_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(API_DIR))

import app


class ApiTestCase(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        app.DB_PATH = os.path.join(self.temp_dir.name, "test.db")
        app.RELEASE_MANIFEST_PATH = os.path.join(self.temp_dir.name, "release.json")
        app._rate_hits.clear()
        app.init_db()
        self.server = app.make_server(port=0)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.port = self.server.server_address[1]

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        self.temp_dir.cleanup()

    def request(self, method, path, body=None, headers=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=3)
        payload = None if body is None else json.dumps(body)
        merged = {"Content-Type": "application/json", **(headers or {})}
        conn.request(method, path, body=payload, headers=merged)
        response = conn.getresponse()
        raw = response.read()
        result = response.status, dict(response.getheaders()), raw
        conn.close()
        return result

    def seed_user(self, email, password="secret1"):
        salt = "test-salt"
        with app.db_connect() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO users "
                "(email, password_hash, salt, created_at) VALUES (?, ?, ?, ?)",
                (email, app.hash_password(password, salt), salt, app.now_iso()),
            )
            row = conn.execute(
                "SELECT id FROM users WHERE email = ?", (email,)
            ).fetchone()
        return row["id"]

    def seed_session(self, email):
        user_id = self.seed_user(email)
        with app.db_connect() as conn:
            token = app.create_session(conn, user_id)
        return token, user_id

    def seed_verification_code(self, email, code, purpose):
        with app.db_connect() as conn:
            conn.execute(
                "INSERT INTO verification_codes "
                "(email, code, purpose, created_at, expires_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (email, code, purpose, app.now_iso(), app.expires_in_iso(600)),
            )

    def seed_device(self, user_id, remote_id, uuid, os_name, device_name):
        with app.db_connect() as conn:
            conn.execute(
                "INSERT INTO devices "
                "(id, uuid, user_id, os, device_name, last_login) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (
                    remote_id,
                    uuid,
                    user_id,
                    os_name,
                    device_name,
                    "2026-09-01T00:00:00.000Z",
                ),
            )

    def web_login(self, email, password="secret1"):
        self.seed_user(email, password)
        status, headers, _ = self.request(
            "POST",
            "/api/web/login",
            {"username": email, "password": password},
            {"Origin": f"http://127.0.0.1:{self.port}"},
        )
        self.assertEqual(status, 200)
        return headers["Set-Cookie"].split(";", 1)[0]

    def test_health_uses_an_ephemeral_test_database(self):
        status, _, raw = self.request("GET", "/api/health")
        self.assertEqual(status, 200)
        self.assertTrue(json.loads(raw)["ok"])
        self.assertTrue(os.path.exists(app.DB_PATH))

    def test_update_manifest_selects_supported_platform_without_cross_app_api(self):
        Path(app.RELEASE_MANIFEST_PATH).write_text(json.dumps({
            "windows_x64": "https://zperme.top/download/LinkRemote-1.0.36-x86_64.exe",
            "android_arm64": "https://zperme.top/download/LinkRemote-1.0.36-android-signed.apk",
        }), encoding="utf-8")
        cases = {
            "/api/version/latest?platform=windows": ".exe",
            "/api/version/latest?platform=android": ".apk",
            "/api/version/latest": ".apk",  # older clients did not send a platform
        }
        for path, extension in cases.items():
            status, _, raw = self.request("GET", path)
            self.assertEqual(status, 200)
            self.assertTrue(json.loads(raw)["url"].endswith(extension))
        status, _, raw = self.request("GET", "/api/version/latest?platform=ios")
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(raw), {"url": ""})

    def test_web_login_sets_http_only_cookie_without_returning_token(self):
        self.seed_user("owner@example.test", "secret1")
        status, headers, raw = self.request(
            "POST",
            "/api/web/login",
            {"username": "owner@example.test", "password": "secret1"},
            {"Origin": f"http://127.0.0.1:{self.port}"},
        )
        body = json.loads(raw)
        cookie = headers.get("Set-Cookie", "")
        self.assertEqual(status, 200)
        self.assertNotIn("access_token", body)
        self.assertIn("linkremote_session=", cookie)
        self.assertIn("HttpOnly", cookie)
        self.assertIn("SameSite=Lax", cookie)
        self.assertIn("Path=/remote", cookie)

    def test_website_prefix_keeps_native_api_routes_available(self):
        for path in ("/remote", "/remote/", "/remote/manage", "/remote/assets/app.js"):
            status, _, _ = self.request("GET", path)
            self.assertEqual(status, 200, path)
        status, _, raw = self.request("GET", "/remote/api/health")
        self.assertEqual(status, 200)
        self.assertTrue(json.loads(raw)["ok"])
        status, _, raw = self.request("GET", "/api/health")
        self.assertEqual(status, 200)
        self.assertTrue(json.loads(raw)["ok"])

    def test_prefixed_web_login_is_cookie_scoped_and_origin_checked(self):
        self.seed_user("prefixed@example.test", "secret1")
        status, headers, raw = self.request(
            "POST",
            "/remote/api/web/login",
            {"username": "prefixed@example.test", "password": "secret1"},
            {"Origin": f"http://127.0.0.1:{self.port}"},
        )
        self.assertEqual(status, 200)
        self.assertIn("Path=/remote", headers["Set-Cookie"])
        self.assertNotIn("access_token", json.loads(raw))
        cookie = headers["Set-Cookie"].split(";", 1)[0]
        status, _, _ = self.request(
            "POST", "/remote/api/web/logout", {},
            {"Cookie": cookie, "Origin": "https://evil.example"},
        )
        self.assertEqual(status, 403)

    def test_native_bearer_login_contract_remains_available(self):
        self.seed_user("native@example.test", "secret1")
        status, _, raw = self.request(
            "POST",
            "/api/login",
            {"username": "native@example.test", "password": "secret1"},
        )
        self.assertEqual(status, 200)
        self.assertTrue(json.loads(raw)["access_token"])

    def test_web_register_sets_cookie_without_exposing_token(self):
        self.seed_verification_code("new@example.test", "123456", "register")
        status, headers, raw = self.request(
            "POST",
            "/api/web/register",
            {
                "username": "new@example.test",
                "password": "secret1",
                "code": "123456",
            },
            {"Origin": f"http://127.0.0.1:{self.port}"},
        )
        self.assertEqual(status, 200)
        self.assertIn("linkremote_session=", headers.get("Set-Cookie", ""))
        self.assertNotIn("access_token", json.loads(raw))

    def test_cross_origin_cookie_mutation_is_rejected(self):
        cookie = self.web_login("owner@example.test", "secret1")
        status, _, _ = self.request(
            "POST",
            "/api/logout",
            {},
            {"Cookie": cookie, "Origin": "https://evil.example"},
        )
        self.assertEqual(status, 403)

    def test_cookie_can_read_current_user_and_devices(self):
        cookie = self.web_login("owner@example.test", "secret1")
        status, _, raw = self.request(
            "POST",
            "/api/currentUser",
            {},
            {
                "Cookie": cookie,
                "Origin": f"http://127.0.0.1:{self.port}",
            },
        )
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(raw)["email"], "owner@example.test")

    def test_peer_list_includes_real_last_login(self):
        token, user_id = self.seed_session("owner@example.test")
        self.seed_device(user_id, "123456789", "owner-device", "Windows", "Office PC")
        status, _, raw = self.request(
            "GET", "/api/peers", headers={"Authorization": f"Bearer {token}"}
        )
        row = json.loads(raw)["data"][0]
        self.assertEqual(status, 200)
        self.assertEqual(row["last_login"], "2026-09-01T00:00:00.000Z")

    def test_user_cannot_delete_another_users_device(self):
        owner_token, owner_id = self.seed_session("owner@example.test")
        other_token, _ = self.seed_session("other@example.test")
        self.seed_device(owner_id, "111", "private-uuid", "Android", "Phone")
        status, _, _ = self.request(
            "DELETE",
            "/api/peers/private-uuid",
            headers={"Authorization": f"Bearer {other_token}"},
        )
        self.assertEqual(status, 200)
        status, _, raw = self.request(
            "GET",
            "/api/peers",
            headers={"Authorization": f"Bearer {owner_token}"},
        )
        self.assertEqual(len(json.loads(raw)["data"]), 1)

    def test_public_management_pages_and_assets_are_served(self):
        for path, content_type in (
            ("/", "text/html"),
            ("/manage", "text/html"),
            ("/assets/site.css", "text/css"),
            ("/assets/app.css", "text/css"),
            ("/assets/app.js", "text/javascript"),
            ("/assets/app-icon.png", "image/png"),
        ):
            status, headers, raw = self.request("GET", path)
            self.assertEqual(status, 200, path)
            self.assertIn(content_type, headers["Content-Type"])
            self.assertTrue(raw)

    def test_management_page_uses_strict_browser_security_headers(self):
        status, headers, _ = self.request("GET", "/")
        self.assertEqual(status, 200)
        self.assertEqual(headers["X-Content-Type-Options"], "nosniff")
        self.assertEqual(headers["X-Frame-Options"], "DENY")
        self.assertIn("default-src 'self'", headers["Content-Security-Policy"])
        self.assertIn("display-capture=()", headers["Permissions-Policy"])

    def test_unknown_and_traversal_paths_are_not_served(self):
        for path in ("/smtp.json", "/assets/../app.py", "/data/linkremote.db"):
            status, _, _ = self.request("GET", path)
            self.assertEqual(status, 404, path)

    def test_web_bundle_contains_required_account_and_device_contracts(self):
        _, _, html = self.request("GET", "/manage")
        _, _, js = self.request("GET", "/assets/app.js")
        html = html.decode("utf-8")
        js = js.decode("utf-8")
        for marker in (
            "login-form",
            "register-form",
            "reset-form",
            "change-email-form",
            "device-list",
        ):
            self.assertIn(marker, html)
        for endpoint in (
            "/api/web/login",
            "/api/web/register",
            "/api/web/logout",
            "/api/peers",
            "/api/change-email",
        ):
            self.assertIn(endpoint, js)
        self.assertNotIn("localStorage", js)
        self.assertNotIn("sessionStorage", js)


if __name__ == "__main__":
    unittest.main()
