# LinkRemote Web Account and Device Management Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deliver a responsive same-origin Web page for personal account and device management without enabling browser remote control.

**Architecture:** Version the existing standard-library Python account API inside the client repository, add a separate cookie-based Web session path while retaining native Bearer authentication, and serve a dependency-free HTML/CSS/JavaScript application from a fixed file allowlist.

**Tech Stack:** Python 3 standard library (`http.server`, `sqlite3`, `unittest`), HTML5, CSS, browser JavaScript modules.

**Spec:** `docs/superpowers/specs/2026-09-01-linkremote-performance-platform-web-management-design.md`

## Global Constraints

- Web supports personal registration, login, password reset, email change, logout, device list, ID copy, refresh, and removal from the account list.
- Web does not initiate remote control, file transfer, terminal, camera access, or administrator actions.
- Native clients retain the existing Bearer Token API contract.
- Web tokens never enter URLs, JavaScript storage, snapshots, or logs.
- Production cookies are `HttpOnly; Secure; SameSite=Lax; Path=/`.
- No third-party Python or JavaScript runtime dependency is added.
- Do not deploy or upload; produce versioned source, tests, package, and instructions only.

---

### Task 1: Bring server source under version control without deleting the deployed copy

**Files:**
- Create: `server/docker-compose.yml`
- Create: `server/deploy.sh`
- Create: `server/get-public-key.sh`
- Create: `server/linkremote-api/app.py`
- Create: `server/linkremote-api/reset_password.py`
- Create: `server/linkremote-api/smtp.json.example`
- Modify: `.gitignore`

**Interfaces:**
- Consumes: current source in `F:/Desktop/codex/远控/server/`.
- Produces: repository-owned `server/` source used by all following tasks.

- [ ] **Step 1: Record source hashes before the mechanical copy**

Run:

```powershell
Get-FileHash ..\server\docker-compose.yml, ..\server\deploy.sh, ..\server\get-public-key.sh, ..\server\linkremote-api\app.py, ..\server\linkremote-api\reset_password.py, ..\server\linkremote-api\smtp.json.example -Algorithm SHA256
```

Expected: six SHA-256 hashes are printed. Do not include `data/`, `smtp.json`, or `__pycache__/`.

- [ ] **Step 2: Copy only the six source/configuration files into `client/server`**

Use a mechanical file copy, preserving the sibling `../server` directory unchanged. Add these ignore rules:

```gitignore
/server/linkremote-api/data/
/server/linkremote-api/smtp.json
/server/linkremote-api/__pycache__/
```

- [ ] **Step 3: Verify the copy is byte-identical**

Run:

```powershell
$pairs = @(
  @('..\server\docker-compose.yml', 'server\docker-compose.yml'),
  @('..\server\deploy.sh', 'server\deploy.sh'),
  @('..\server\get-public-key.sh', 'server\get-public-key.sh'),
  @('..\server\linkremote-api\app.py', 'server\linkremote-api\app.py'),
  @('..\server\linkremote-api\reset_password.py', 'server\linkremote-api\reset_password.py'),
  @('..\server\linkremote-api\smtp.json.example', 'server\linkremote-api\smtp.json.example')
)
$pairs | ForEach-Object {
  if ((Get-FileHash $_[0]).Hash -ne (Get-FileHash $_[1]).Hash) { throw "mismatch: $($_[0])" }
}
```

Expected: exit code 0 with no mismatch.

- [ ] **Step 4: Compile the imported Python source**

Run: `python -m py_compile server/linkremote-api/app.py server/linkremote-api/reset_password.py`

Expected: exit code 0.

- [ ] **Step 5: Commit**

```bash
git add .gitignore server
git commit -m "chore: version self-hosted account server"
```

### Task 2: Build an isolated HTTP test harness

**Files:**
- Create: `server/linkremote-api/tests/__init__.py`
- Create: `server/linkremote-api/tests/test_api.py`
- Modify: `server/linkremote-api/app.py`

**Interfaces:**
- Consumes: `Handler`, `init_db()`, `hash_password()`, and `ThreadingHTTPServer` from `app.py`.
- Produces: `make_server(host="127.0.0.1", port=0)` and a real HTTP `ApiTestCase` using a temporary SQLite database.

- [ ] **Step 1: Write a failing health and isolation test**

```python
import http.client
import json
import os
import tempfile
import threading
import unittest

import app


class ApiTestCase(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        app.DB_PATH = os.path.join(self.temp_dir.name, "test.db")
        app.init_db()
        self.server = app.make_server()
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
                "INSERT OR IGNORE INTO users (email, password_hash, salt, created_at) VALUES (?, ?, ?, ?)",
                (email, app.hash_password(password, salt), salt, app.now_iso()),
            )
            row = conn.execute("SELECT id FROM users WHERE email = ?", (email,)).fetchone()
        return row["id"]

    def seed_session(self, email):
        user_id = self.seed_user(email)
        with app.db_connect() as conn:
            token = app.create_session(conn, user_id)
        return token, user_id

    def seed_verification_code(self, email, code, purpose):
        with app.db_connect() as conn:
            conn.execute(
                "INSERT INTO verification_codes (email, code, purpose, created_at, expires_at) VALUES (?, ?, ?, ?, ?)",
                (email, code, purpose, app.now_iso(), app.expires_in_iso(600)),
            )

    def seed_device(self, user_id, remote_id, uuid, os_name, device_name):
        with app.db_connect() as conn:
            conn.execute(
                "INSERT INTO devices (id, uuid, user_id, os, device_name, last_login) VALUES (?, ?, ?, ?, ?, ?)",
                (remote_id, uuid, user_id, os_name, device_name, "2026-09-01T00:00:00.000Z"),
            )

    def web_login(self, email, password="secret1"):
        self.seed_user(email, password)
        status, headers, _ = self.request(
            "POST", "/api/web/login",
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


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run and verify RED**

Run from `server/linkremote-api`: `python -m unittest -v tests.test_api.ApiTestCase.test_health_uses_an_ephemeral_test_database`

Expected: error because `app.make_server` does not exist.

- [ ] **Step 3: Extract a reusable server factory**

```python
def make_server(host=HOST, port=PORT):
    return ThreadingHTTPServer((host, port), Handler)


def main():
    init_db()
    server = make_server()
    print(f"LinkRemote API listening on {HOST}:{PORT}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
```

Also change the database constant so local acceptance and tests never touch production data:

```python
DB_PATH = os.environ.get(
    "LINKREMOTE_DB_PATH", os.path.join(DATA_DIR, "linkremote.db")
)
```

- [ ] **Step 4: Run and verify GREEN**

Run: `python -m unittest -v tests.test_api.ApiTestCase.test_health_uses_an_ephemeral_test_database`

Expected: 1 test passes.

- [ ] **Step 5: Commit**

```bash
git add server/linkremote-api/app.py server/linkremote-api/tests
git commit -m "test: add isolated account api harness"
```

### Task 3: Add secure Web cookie sessions while preserving Bearer clients

**Files:**
- Modify: `server/linkremote-api/app.py`
- Modify: `server/linkremote-api/tests/test_api.py`

**Interfaces:**
- Consumes: existing user/password verification and token table.
- Produces: `POST /api/web/login`, `POST /api/web/register`, `POST /api/web/logout`, Cookie-or-Bearer `_auth_user`, same-origin checks, and restricted CORS.

- [ ] **Step 1: Add failing authentication and origin tests**

Add a `seed_user(email, password)` helper that inserts a user with `app.hash_password`, then add:

```python
def test_web_login_sets_http_only_cookie_without_returning_token(self):
    self.seed_user("owner@example.test", "secret1")
    status, headers, raw = self.request(
        "POST", "/api/web/login",
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

def test_native_bearer_login_contract_remains_available(self):
    self.seed_user("native@example.test", "secret1")
    status, _, raw = self.request(
        "POST", "/api/login",
        {"username": "native@example.test", "password": "secret1"},
    )
    self.assertEqual(status, 200)
    self.assertTrue(json.loads(raw)["access_token"])

def test_web_register_sets_cookie_without_exposing_token(self):
    self.seed_verification_code("new@example.test", "123456", "register")
    status, headers, raw = self.request(
        "POST", "/api/web/register",
        {"username": "new@example.test", "password": "secret1", "code": "123456"},
        {"Origin": f"http://127.0.0.1:{self.port}"},
    )
    self.assertEqual(status, 200)
    self.assertIn("linkremote_session=", headers.get("Set-Cookie", ""))
    self.assertNotIn("access_token", json.loads(raw))

def test_cross_origin_cookie_mutation_is_rejected(self):
    cookie = self.web_login("owner@example.test", "secret1")
    status, _, _ = self.request(
        "POST", "/api/logout", {},
        {"Cookie": cookie, "Origin": "https://evil.example"},
    )
    self.assertEqual(status, 403)
```

- [ ] **Step 2: Run and verify RED**

Run: `python -m unittest -v tests.test_api`

Expected: `/api/web/login` returns 404 and the origin guard test fails.

- [ ] **Step 3: Implement explicit Web login/logout routes**

Add configuration:

```python
WEB_COOKIE_NAME = "linkremote_session"
COOKIE_SECURE = os.environ.get("LINKREMOTE_COOKIE_SECURE", "1") != "0"
ALLOWED_ORIGIN = os.environ.get("LINKREMOTE_ALLOWED_ORIGIN", "").rstrip("/")
```

Extend `_send`/`_send_json` with an `extra_headers` dictionary. Extract `auth_response(token, row, web_session)` and use it from both `handle_login` and `handle_register`. Native routes keep the existing response; Web routes send only `{"ok": True, "user": user_to_payload(row)}` plus:

```python
def auth_response(token, row, web_session):
    if not web_session:
        return ({
            "access_token": token,
            "type": "access_token",
            "user": user_to_payload(row),
        }, {})
    cookie = (
        f"{WEB_COOKIE_NAME}={token}; Path=/; HttpOnly; SameSite=Lax"
        + ("; Secure" if COOKIE_SECURE else "")
    )
    return (
        {"ok": True, "user": user_to_payload(row)},
        {"Set-Cookie": cookie},
    )

payload, headers = auth_response(token, row, web_session)
self._send_json(payload, extra_headers=headers)
```

Parse cookies with `http.cookies.SimpleCookie`. `_bearer_token` remains first priority and `_session_token` falls back to the cookie. Route `POST /api/web/login` to `handle_login(web_session=True)`, `POST /api/web/register` to `handle_register(web_session=True)`, and `POST /api/web/logout` to logout plus an expired cookie.

- [ ] **Step 4: Implement origin and CORS policy**

The allowed origin is `LINKREMOTE_ALLOWED_ORIGIN` when configured; otherwise compare `Origin` with `http(s)://Host`. For authenticated Cookie requests and every `/api/web/*` POST/PUT/DELETE, reject a non-matching origin with 403. Bearer-authenticated native requests bypass browser origin enforcement.

Only emit `Access-Control-Allow-Origin` when the request origin is allowed. Add `DELETE` to `Access-Control-Allow-Methods` and `Vary: Origin` when a CORS origin is emitted.

- [ ] **Step 5: Run and verify GREEN**

Run: `python -m unittest -v tests.test_api`

Expected: all authentication, origin, and health tests pass.

- [ ] **Step 6: Commit**

```bash
git add server/linkremote-api/app.py server/linkremote-api/tests/test_api.py
git commit -m "feat: add secure web account sessions"
```

### Task 4: Make device management truthful and user-isolated

**Files:**
- Modify: `server/linkremote-api/app.py`
- Modify: `server/linkremote-api/tests/test_api.py`

**Interfaces:**
- Consumes: Cookie/Bearer authentication from Task 3.
- Produces: `last_login` in `/api/peers`, idempotent user-scoped deletion, and no fabricated online status in the Web contract.

- [ ] **Step 1: Write failing device contract tests**

```python
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
        "DELETE", "/api/peers/private-uuid",
        headers={"Authorization": f"Bearer {other_token}"},
    )
    self.assertEqual(status, 200)
    status, _, raw = self.request(
        "GET", "/api/peers", headers={"Authorization": f"Bearer {owner_token}"}
    )
    self.assertEqual(len(json.loads(raw)["data"]), 1)
```

- [ ] **Step 2: Run and verify RED**

Run: `python -m unittest -v tests.test_api`

Expected: the `last_login` assertion fails because the field is absent; ownership test documents and protects the existing `user_id + uuid` behavior.

- [ ] **Step 3: Add the real timestamp without breaking native fields**

```python
data.append({
    "id": r["id"],
    "uuid": r["uuid"],
    "info": info,
    "status": 1,
    "last_login": r["last_login"],
    "user": user["email"],
    "user_name": user["email"],
    "device_group_name": None,
    "note": "",
})
```

The Web UI must ignore `status: 1`; it is retained only for native-client compatibility.

- [ ] **Step 4: Run and verify GREEN**

Run: `python -m unittest -v tests.test_api`

Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
git add server/linkremote-api/app.py server/linkremote-api/tests/test_api.py
git commit -m "fix: expose truthful device activity"
```

### Task 5: Serve static files from an explicit allowlist

**Files:**
- Create: `server/linkremote-api/web/index.html`
- Create: `server/linkremote-api/web/assets/app.css`
- Create: `server/linkremote-api/web/assets/app.js`
- Modify: `server/linkremote-api/app.py`
- Modify: `server/linkremote-api/tests/test_api.py`

**Interfaces:**
- Consumes: the same-origin HTTP server.
- Produces: `/`, `/assets/app.css`, and `/assets/app.js`; every other non-API path returns 404.

- [ ] **Step 1: Write failing static allowlist tests**

```python
def test_management_page_and_assets_are_served(self):
    for path, content_type in (
        ("/", "text/html"),
        ("/assets/app.css", "text/css"),
        ("/assets/app.js", "text/javascript"),
    ):
        status, headers, raw = self.request("GET", path)
        self.assertEqual(status, 200, path)
        self.assertIn(content_type, headers["Content-Type"])
        self.assertTrue(raw)

def test_unknown_and_traversal_paths_are_not_served(self):
    for path in ("/smtp.json", "/assets/../app.py", "/data/linkremote.db"):
        status, _, _ = self.request("GET", path)
        self.assertEqual(status, 404, path)
```

- [ ] **Step 2: Run and verify RED**

Run: `python -m unittest -v tests.test_api`

Expected: `/` and the asset paths return 404.

- [ ] **Step 3: Add the fixed static mapping**

```python
WEB_DIR = os.path.join(BASE_DIR, "web")
STATIC_FILES = {
    "/": ("index.html", "text/html; charset=utf-8", "no-cache"),
    "/assets/app.css": ("assets/app.css", "text/css; charset=utf-8", "public, max-age=3600"),
    "/assets/app.js": ("assets/app.js", "text/javascript; charset=utf-8", "public, max-age=3600"),
}

def handle_static(self, path):
    entry = STATIC_FILES.get(path)
    if entry is None:
        return self._error(404, "Not Found")
    relative, content_type, cache_control = entry
    with open(os.path.join(WEB_DIR, relative), "rb") as source:
        body = source.read()
    self._send(body, content_type=content_type,
               extra_headers={"Cache-Control": cache_control})
```

Call `handle_static` only for GET requests after all `/api/` routes have been considered.

- [ ] **Step 4: Add the semantic HTML shell**

`index.html` must contain `login-view`, `register-view`, `reset-view`, and `dashboard-view` sections; forms named `login-form`, `register-form`, `reset-form`, and `change-email-form`; a `device-list`; one `aria-live="polite"` status region; and no inline scripts or inline event handlers. Load `/assets/app.css` and `/assets/app.js` with `type="module"`.

- [ ] **Step 5: Run and verify GREEN**

Run: `python -m unittest -v tests.test_api`

Expected: static allowlist and traversal tests pass.

- [ ] **Step 6: Commit**

```bash
git add server/linkremote-api/app.py server/linkremote-api/web server/linkremote-api/tests/test_api.py
git commit -m "feat: serve web management shell"
```

### Task 6: Implement account and device interactions in the Web page

**Files:**
- Modify: `server/linkremote-api/web/assets/app.js`
- Modify: `server/linkremote-api/web/assets/app.css`
- Modify: `server/linkremote-api/web/index.html`
- Modify: `server/linkremote-api/tests/test_api.py`

**Interfaces:**
- Consumes: `/api/web/login`, `/api/web/register`, `/api/web/logout`, `/api/send-code`, `/api/reset-password`, `/api/currentUser`, `/api/change-email`, `/api/peers`, and `DELETE /api/peers/:uuid`.
- Produces: a responsive personal dashboard with a single `request(path, options)` error boundary.

- [ ] **Step 1: Add a failing HTML/JavaScript contract test**

```python
def test_web_bundle_contains_required_account_and_device_contracts(self):
    _, _, html = self.request("GET", "/")
    _, _, js = self.request("GET", "/assets/app.js")
    html = html.decode("utf-8")
    js = js.decode("utf-8")
    for marker in ("login-form", "register-form", "reset-form", "change-email-form", "device-list"):
        self.assertIn(marker, html)
    for endpoint in ("/api/web/login", "/api/web/register", "/api/web/logout", "/api/peers", "/api/change-email"):
        self.assertIn(endpoint, js)
    self.assertNotIn("localStorage", js)
    self.assertNotIn("sessionStorage", js)
```

- [ ] **Step 2: Run and verify RED**

Run: `python -m unittest -v tests.test_api`

Expected: the bundle contract fails until every form and endpoint is wired.

- [ ] **Step 3: Implement one fetch boundary and view state**

```javascript
async function request(path, options = {}) {
  const response = await fetch(path, {
    credentials: 'same-origin',
    headers: {'Content-Type': 'application/json', ...(options.headers || {})},
    ...options,
  });
  const body = await response.json().catch(() => ({}));
  if (response.status === 401) {
    showView('login');
    throw new Error('登录已失效，请重新登录');
  }
  if (!response.ok || body.error) {
    throw new Error(body.error || `请求失败 (${response.status})`);
  }
  return body;
}
```

Keep password inputs out of module-level state. `showView(name)` hides all four views, reveals the requested one, clears the live status, and moves focus to that view's first heading.

- [ ] **Step 4: Implement account forms**

Login posts to `/api/web/login`; registration sends a `register` code then posts the current client-compatible fields to `/api/web/register`; reset sends a `reset` code and posts the new password, then returns to login because password reset revokes every session; email change sends a `change_email` code and posts current password plus new email; logout posts to `/api/web/logout`. After successful login, registration, or email change call `loadDashboard()`.

Use the same submit wrapper for every form:

```javascript
async function submitJson(form, path, onSuccess) {
  const submit = form.querySelector('[type="submit"]');
  submit.disabled = true;
  try {
    const body = Object.fromEntries(new FormData(form).entries());
    const result = await request(path, {method: 'POST', body: JSON.stringify(body)});
    await onSuccess(result);
  } catch (error) {
    announce(error.message, true);
  } finally {
    submit.disabled = false;
  }
}

document.querySelector('#login-form').addEventListener('submit', event => {
  event.preventDefault();
  return submitJson(event.currentTarget, '/api/web/login', loadDashboard);
});
document.querySelector('#register-form').addEventListener('submit', event => {
  event.preventDefault();
  return submitJson(event.currentTarget, '/api/web/register', loadDashboard);
});
```

- [ ] **Step 5: Implement truthful device cards**

Render device name, ID, OS, and localized `last_login`. Provide Copy and Remove buttons. Copy uses `navigator.clipboard.writeText(id)`. Remove requires `window.confirm('仅从账号列表移除此设备？')`, sends `DELETE /api/peers/${encodeURIComponent(uuid)}`, then reloads the list. Do not render an online badge and do not provide a Connect button.

```javascript
function button(label, action) {
  const element = document.createElement('button');
  element.type = 'button';
  element.textContent = label;
  element.addEventListener('click', action);
  return element;
}

function formatDate(value) {
  if (!value) return '从未登录';
  const date = new Date(value);
  return Number.isNaN(date.valueOf()) ? value : date.toLocaleString();
}

function renderDevice(device) {
  const card = document.createElement('article');
  card.className = 'device-card';
  const info = device.info || {};
  const title = document.createElement('h3');
  title.textContent = info.device_name || device.id;
  const detail = document.createElement('p');
  detail.textContent = `${device.id} · ${info.os || 'Unknown'} · ${formatDate(device.last_login)}`;
  const copy = button('复制 ID', () => navigator.clipboard.writeText(device.id));
  const remove = button('移除', async () => {
    if (!window.confirm('仅从账号列表移除此设备？')) return;
    await request(`/api/peers/${encodeURIComponent(device.uuid)}`, {method: 'DELETE'});
    await loadDevices();
  });
  card.append(title, detail, copy, remove);
  return card;
}
```

- [ ] **Step 6: Implement responsive and accessible CSS**

Use a centered `max-width: 960px` shell, a two-column dashboard above 760 px, one column below 760 px, minimum 44 px interactive height, visible `:focus-visible`, system fonts, and `prefers-reduced-motion`. Device cards must wrap long IDs without horizontal page scrolling at 360 px width.

```css
.app-shell { width: min(100% - 32px, 960px); margin-inline: auto; }
.dashboard-grid { display: grid; grid-template-columns: minmax(0, 1fr) minmax(0, 2fr); gap: 24px; }
button, input { min-height: 44px; }
button:focus-visible, input:focus-visible { outline: 3px solid #2563eb; outline-offset: 2px; }
.device-card { overflow-wrap: anywhere; }
@media (max-width: 760px) { .dashboard-grid { grid-template-columns: 1fr; } }
@media (prefers-reduced-motion: reduce) { *, *::before, *::after { scroll-behavior: auto; transition: none !important; } }
```

- [ ] **Step 7: Run the full server suite**

Run:

```bash
cd server/linkremote-api
python -m unittest -v tests.test_api
python -m py_compile app.py reset_password.py
```

Expected: all tests pass and Python compilation exits 0.

- [ ] **Step 8: Commit**

```bash
git add server/linkremote-api/web server/linkremote-api/tests/test_api.py
git commit -m "feat: add account and device web management"
```

### Task 7: Add deployment guidance and browser acceptance

**Files:**
- Create: `server/nginx/linkremote-web.conf.example`
- Create: `server/README.md`
- Modify: `server/deploy.sh`

**Interfaces:**
- Consumes: the same-origin Web/API service on port 21114.
- Produces: an HTTPS reverse-proxy example, health check, and a repeatable browser checklist without opening 21118/21119.

- [ ] **Step 1: Add exact reverse-proxy configuration**

The example must proxy `/` to `http://127.0.0.1:21114`, set `Host`, `X-Forwarded-Proto`, and `X-Real-IP`, enforce a request-body limit, and redirect HTTP to HTTPS. It must not proxy or expose WebSocket ports 21118/21119.

- [ ] **Step 2: Document environment and health checks**

Document:

```text
LINKREMOTE_PORT=21114
LINKREMOTE_ALLOWED_ORIGIN=https://remote.example.com
LINKREMOTE_COOKIE_SECURE=1
```

Add `curl --fail https://remote.example.com/api/health` and backup/restore instructions for `data/linkremote.db`. Use placeholder domains only in the example; do not insert production credentials.

- [ ] **Step 3: Run local browser acceptance**

Start with a temporary DB and insecure localhost cookie:

```powershell
$env:LINKREMOTE_COOKIE_SECURE='0'
$env:LINKREMOTE_DB_PATH=(Join-Path $env:TEMP 'linkremote-web-acceptance.db')
python server/linkremote-api/app.py
```

In a browser verify: registration/login, refresh persistence through the HttpOnly cookie, device empty state, password reset, email change, logout, 401 return to login, 360 px layout, keyboard focus, and no browser remote-control entry.

- [ ] **Step 4: Run final Web verification and commit**

Run:

```bash
python -m unittest discover -s server/linkremote-api/tests -v
python -m py_compile server/linkremote-api/app.py server/linkremote-api/reset_password.py
git diff --check
```

Expected: all tests pass, compilation and diff check exit 0.

```bash
git add server/nginx/linkremote-web.conf.example server/README.md server/deploy.sh
git commit -m "docs: add secure web management deployment"
```
