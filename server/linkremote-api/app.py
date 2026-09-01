#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
LinkRemote 认证 API 服务（自建，替代 RustDesk 官方账号服务）

功能：
  - 邮箱注册 / 登录（返回 access_token）
  - 会话校验（/api/currentUser）与登出（/api/logout）
  - 邮箱验证码：注册验证 / 忘记密码 / 更换绑定邮箱
  - 地址簿 / 分组 / 设备列表等 RustDesk 客户端接口的空响应桩
  - /api/login-options 返回空数组（客户端不再展示第三方登录按钮）

设计：
  - 仅 Python 标准库（http.server + sqlite3 + hashlib），零第三方依赖
  - 数据存于 data/linkremote.db（SQLite，WAL 模式）
  - 密码 PBKDF2-SHA256 加盐哈希；token 随机 32 字节，180 天有效
  - 登录/注册按 IP 限速（默认每分钟 10 次），防暴力破解
  - 验证码 6 位数字、10 分钟有效、60 秒重发冷却、单码最多校验 5 次
  - SMTP 发信配置放 smtp.json（host/port/user/password/from_email），
    未配置时验证码打印到 stdout（PM2 日志），便于联调

部署（服务器）：
  cd /opt/linkremote-api
  pm2 start app.py --name linkremote-api --interpreter python3
  pm2 save
"""

import datetime
from contextlib import contextmanager
import hashlib
import hmac
import json
import os
import re
import secrets
import smtplib
import sqlite3
import sys
import threading
import time
from email.message import EmailMessage
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
DB_PATH = os.environ.get(
    "LINKREMOTE_DB_PATH", os.path.join(DATA_DIR, "linkremote.db")
)
WEB_DIR = os.path.join(BASE_DIR, "web")
HOST = "0.0.0.0"
PORT = int(os.environ.get("LINKREMOTE_PORT", "21114"))
WEB_COOKIE_NAME = "linkremote_session"
COOKIE_SECURE = os.environ.get("LINKREMOTE_COOKIE_SECURE", "1") != "0"
ALLOWED_ORIGIN = os.environ.get("LINKREMOTE_ALLOWED_ORIGIN", "").rstrip("/")
TOKEN_TTL_DAYS = 180
PBKDF2_ITERATIONS = 120_000
MAX_ATTEMPTS_PER_MINUTE = 10
CODE_TTL_SECONDS = 600
CODE_MAX_ATTEMPTS = 5
CODE_RESEND_COOLDOWN = 60
CODE_LENGTH = 6

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
VALID_PURPOSES = ("register", "reset", "change_email")
STATIC_FILES = {
    "/": ("index.html", "text/html; charset=utf-8", "no-cache"),
    "/assets/app.css": (
        "assets/app.css",
        "text/css; charset=utf-8",
        "public, max-age=3600",
    ),
    "/assets/app.js": (
        "assets/app.js",
        "text/javascript; charset=utf-8",
        "public, max-age=3600",
    ),
}
SECURITY_HEADERS = {
    "Content-Security-Policy": (
        "default-src 'self'; base-uri 'none'; frame-ancestors 'none'; "
        "form-action 'self'; img-src 'self' data:; object-src 'none'; "
        "script-src 'self'; style-src 'self'; connect-src 'self'"
    ),
    "Permissions-Policy": (
        "camera=(), microphone=(), geolocation=(), display-capture=()"
    ),
    "Referrer-Policy": "no-referrer",
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
}


def load_smtp_config():
    """返回 smtp.json 配置；文件不存在或格式错误返回 None（使用日志发码模式）。"""
    path = os.path.join(BASE_DIR, "smtp.json")
    try:
        with open(path, "r", encoding="utf-8") as f:
            cfg = json.load(f)
        if not cfg.get("host") or not cfg.get("from_email"):
            return None
        return cfg
    except (FileNotFoundError, json.JSONDecodeError):
        return None


SMTP_CONFIG = load_smtp_config()


# ---------------------------------------------------------------- database

@contextmanager
def db_connect():
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db():
    db_dir = os.path.dirname(DB_PATH)
    if db_dir:
        os.makedirs(db_dir, exist_ok=True)
    with db_connect() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                email         TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                salt          TEXT NOT NULL,
                display_name  TEXT NOT NULL DEFAULT '',
                avatar        TEXT NOT NULL DEFAULT '',
                is_admin      INTEGER NOT NULL DEFAULT 0,
                status        INTEGER NOT NULL DEFAULT 1,
                created_at    TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS tokens (
                token      TEXT PRIMARY KEY,
                user_id    INTEGER NOT NULL,
                created_at TEXT NOT NULL,
                expires_at TEXT NOT NULL,
                FOREIGN KEY (user_id) REFERENCES users(id)
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS verification_codes (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                email      TEXT NOT NULL,
                code       TEXT NOT NULL,
                purpose    TEXT NOT NULL,
                attempts   INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                expires_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS devices (
                id          TEXT NOT NULL,
                uuid        TEXT NOT NULL,
                user_id     INTEGER NOT NULL,
                os          TEXT NOT NULL DEFAULT '',
                device_name TEXT NOT NULL DEFAULT '',
                last_login  TEXT NOT NULL,
                PRIMARY KEY (user_id, uuid)
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_tokens_user ON tokens(user_id)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_codes_email_purpose "
            "ON verification_codes(email, purpose)"
        )
        conn.commit()


# ---------------------------------------------------------------- helpers

def now_iso():
    return datetime.datetime.now(datetime.timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def expires_iso(days=TOKEN_TTL_DAYS):
    return (
        datetime.datetime.now(datetime.timezone.utc)
        + datetime.timedelta(days=days)
    ).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def expires_in_iso(seconds: int) -> str:
    return (
        datetime.datetime.now(datetime.timezone.utc)
        + datetime.timedelta(seconds=seconds)
    ).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def hash_password(password: str, salt: str) -> str:
    return hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt.encode("utf-8"),
        PBKDF2_ITERATIONS,
    ).hex()


def verify_password(password: str, salt: str, expected: str) -> bool:
    actual = hash_password(password, salt)
    return hmac.compare_digest(actual, expected)


def new_token() -> str:
    return secrets.token_urlsafe(32)


def is_valid_email(email: str) -> bool:
    return bool(EMAIL_RE.fullmatch(email.strip()))


def user_to_payload(row: sqlite3.Row) -> dict:
    email = row["email"]
    return {
        "name": email,
        "display_name": row["display_name"] or email.split("@")[0],
        "avatar": row["avatar"] or "",
        "email": email,
        "note": "",
        "status": row["status"],
        "is_admin": bool(row["is_admin"]),
    }


def create_session(conn: sqlite3.Connection, user_id: int) -> str:
    token = new_token()
    conn.execute(
        "DELETE FROM tokens WHERE expires_at < ?", (now_iso(),)
    )
    conn.execute(
        "INSERT INTO tokens (token, user_id, created_at, expires_at) "
        "VALUES (?, ?, ?, ?)",
        (token, user_id, now_iso(), expires_iso()),
    )
    return token


def auth_response(token: str, row: sqlite3.Row, web_session: bool):
    if not web_session:
        return (
            {
                "access_token": token,
                "type": "access_token",
                "user": user_to_payload(row),
            },
            {},
        )
    cookie = (
        f"{WEB_COOKIE_NAME}={token}; Path=/; HttpOnly; SameSite=Lax"
        + ("; Secure" if COOKIE_SECURE else "")
    )
    return (
        {"ok": True, "user": user_to_payload(row)},
        {"Set-Cookie": cookie},
    )


# ---------------------------------------------------------------- verification codes

def generate_code() -> str:
    return f"{secrets.randbelow(10 ** CODE_LENGTH):0{CODE_LENGTH}d}"


def purpose_label(purpose: str) -> str:
    return {
        "register": "注册账号",
        "reset": "找回密码",
        "change_email": "更换绑定邮箱",
    }.get(purpose, purpose)


def send_verification_email(email: str, code: str, purpose: str) -> None:
    """发送验证码邮件；未配置 SMTP 时打印到 stdout（PM2 日志）供联调。"""
    if not SMTP_CONFIG:
        print(
            f"[VERIFICATION_CODE] purpose={purpose} email={email} "
            f"code={code} (SMTP 未配置，此码已打印到日志)",
            flush=True,
        )
        return
    label = purpose_label(purpose)
    ttl_minutes = CODE_TTL_SECONDS // 60
    msg = EmailMessage()
    # EmailMessage 会自动对非 ASCII 主题做 RFC2047 编码，直接赋字符串即可
    msg["Subject"] = f"【LinkRemote】{label}验证码"
    msg["From"] = SMTP_CONFIG["from_email"]
    msg["To"] = email
    text = (
        f"您正在{label}，验证码为：{code}\n\n"
        f"验证码 {ttl_minutes} 分钟内有效，请勿泄露给他人。\n"
        "如非本人操作，请忽略本邮件。\n\nLinkRemote"
    )
    html = f"""\
<div style="max-width:480px;margin:0 auto;padding:24px;font-family:-apple-system,Segoe UI,Roboto,Microsoft YaHei,sans-serif;background:#0F1923;color:#E8ECEF;border-radius:12px">
  <h2 style="margin:0 0 12px">LinkRemote</h2>
  <p style="margin:0 0 8px">您正在{label}，本次验证码为：</p>
  <div style="font-size:28px;font-weight:700;letter-spacing:6px;color:#4ECDC4;margin:12px 0">{code}</div>
  <p style="margin:0;color:#8A9BA8;font-size:13px">验证码 {ttl_minutes} 分钟内有效，请勿泄露给他人。如非本人操作请忽略此邮件。</p>
</div>"""
    msg.set_content(text)
    msg.add_alternative(html, subtype="html")
    try:
        host = SMTP_CONFIG["host"]
        port = int(SMTP_CONFIG.get("port", 465))
        use_ssl = SMTP_CONFIG.get("use_ssl", port == 465)
        if use_ssl:
            # 隐式 SSL（QQ/163 的 465 端口）
            server = smtplib.SMTP_SSL(host, port, timeout=30)
        else:
            # 显式 STARTTLS（587 端口）
            server = smtplib.SMTP(host, port, timeout=30)
            server.ehlo()
            server.starttls()
            server.ehlo()
        with server:
            user = SMTP_CONFIG.get("user")
            password = SMTP_CONFIG.get("password")
            if user and password:
                server.login(user, password)
            server.send_message(msg)
        print(f"[SMTP] 验证码已发送至 {email}", flush=True)
    except Exception as e:
        print(
            f"[SMTP] 发送失败（改用日志联调）email={email} err={e} code={code}",
            flush=True,
        )


def verify_code(email: str, code: str, purpose: str) -> tuple[bool, str]:
    """校验验证码。成功时删除该码；失败返回 (False, 中文错误)。"""
    if not code:
        return False, "请输入验证码"
    now = now_iso()
    with db_connect() as conn:
        row = conn.execute(
            """
            SELECT * FROM verification_codes
            WHERE email = ? AND purpose = ?
            ORDER BY id DESC LIMIT 1
            """,
            (email, purpose),
        ).fetchone()
        if row is None or row["expires_at"] <= now:
            return False, "验证码错误或已过期"
        if row["attempts"] >= CODE_MAX_ATTEMPTS:
            conn.execute(
                "DELETE FROM verification_codes WHERE id = ?", (row["id"],)
            )
            conn.commit()
            return False, "验证码错误次数过多，请重新获取"
        if hmac.compare_digest(row["code"], code.strip()):
            conn.execute(
                "DELETE FROM verification_codes WHERE id = ?", (row["id"],)
            )
            conn.commit()
            return True, ""
        conn.execute(
            "UPDATE verification_codes SET attempts = attempts + 1 WHERE id = ?",
            (row["id"],),
        )
        conn.commit()
        return False, "验证码错误或已过期"


def upsert_device(
    conn: sqlite3.Connection,
    user_id: int,
    device_id: str,
    uuid: str,
    device_info: dict,
) -> None:
    """登录/注册时记录设备（同一账号同一 uuid 覆盖更新）。"""
    device_id = (device_id or "").strip()
    uuid = (uuid or "").strip()
    if not device_id or not uuid:
        return
    info = device_info if isinstance(device_info, dict) else {}
    os_name = str(info.get("os") or "")
    device_name = str(info.get("name") or "")
    conn.execute(
        """
        INSERT INTO devices (id, uuid, user_id, os, device_name, last_login)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(user_id, uuid) DO UPDATE SET
            id = excluded.id,
            os = excluded.os,
            device_name = excluded.device_name,
            last_login = excluded.last_login
        """,
        (device_id, uuid, user_id, os_name, device_name, now_iso()),
    )


# ---------------------------------------------------------------- rate limit

_rate_lock = threading.Lock()
_rate_hits: dict[str, list[float]] = {}


def rate_limited(key: str) -> bool:
    """Return True if the request should be rejected (over limit)."""
    now = time.time()
    with _rate_lock:
        hits = [t for t in _rate_hits.get(key, []) if now - t < 60]
        if len(hits) >= MAX_ATTEMPTS_PER_MINUTE:
            _rate_hits[key] = hits
            return True
        hits.append(now)
        _rate_hits[key] = hits
        return False


# ---------------------------------------------------------------- handler

class Handler(BaseHTTPRequestHandler):
    server_version = "LinkRemoteAPI/1.0"

    # ---- low level helpers ----

    def log_message(self, fmt, *args):
        sys.stderr.write("[%s] %s %s\n" % (
            self.log_date_time_string(),
            self.address_string(),
            fmt % args,
        ))

    def _origin_allowed(self) -> bool:
        origin = (self.headers.get("Origin") or "").rstrip("/")
        if not origin:
            return False
        if ALLOWED_ORIGIN:
            return hmac.compare_digest(origin, ALLOWED_ORIGIN)
        host = (self.headers.get("Host") or "").strip()
        if not host:
            return False
        return origin in (f"http://{host}", f"https://{host}")

    def _cors_headers(self) -> dict:
        origin = (self.headers.get("Origin") or "").rstrip("/")
        if not origin or not self._origin_allowed():
            return {}
        return {
            "Access-Control-Allow-Origin": origin,
            "Access-Control-Allow-Headers": "Content-Type, Authorization",
            "Access-Control-Allow-Methods": "GET, POST, PUT, DELETE, OPTIONS",
            "Vary": "Origin",
        }

    def _send(
        self,
        body: bytes,
        status=200,
        content_type="application/json; charset=utf-8",
        extra_headers=None,
    ):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        headers = {
            **SECURITY_HEADERS,
            **self._cors_headers(),
            **(extra_headers or {}),
        }
        for name, value in headers.items():
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(body)

    def _send_json(self, obj, status=200, extra_headers=None):
        self._send(
            json.dumps(obj, ensure_ascii=False).encode("utf-8"),
            status,
            extra_headers=extra_headers,
        )

    def _send_raw(self, text: str, status=200):
        self._send(text.encode("utf-8"), status)

    def _error(self, status: int, message: str):
        self._send_json({"error": message}, status)

    def _read_body(self) -> dict:
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except (TypeError, ValueError):
            length = 0
        if length <= 0:
            return {}
        raw = self.rfile.read(length)
        try:
            data = json.loads(raw.decode("utf-8"))
            return data if isinstance(data, dict) else {}
        except (UnicodeDecodeError, json.JSONDecodeError):
            return {}

    def _bearer_token(self) -> str:
        auth = self.headers.get("Authorization") or ""
        if auth.lower().startswith("bearer "):
            return auth[7:].strip()
        return ""

    def _cookie_token(self) -> str:
        raw = self.headers.get("Cookie") or ""
        if not raw:
            return ""
        cookie = SimpleCookie()
        try:
            cookie.load(raw)
        except Exception:
            return ""
        morsel = cookie.get(WEB_COOKIE_NAME)
        return morsel.value.strip() if morsel else ""

    def _session_token(self) -> str:
        return self._bearer_token() or self._cookie_token()

    def _auth_user(self):
        token = self._session_token()
        if not token:
            return None
        with db_connect() as conn:
            row = conn.execute(
                """
                SELECT u.* FROM tokens t
                JOIN users u ON u.id = t.user_id
                WHERE t.token = ? AND t.expires_at > ?
                """,
                (token, now_iso()),
            ).fetchone()
        return row

    def _client_key(self) -> str:
        return self.client_address[0]

    def _requires_origin_check(self, path: str, method: str) -> bool:
        if method not in ("POST", "PUT", "DELETE"):
            return False
        if path.startswith("/api/web/"):
            return True
        return bool(self._cookie_token()) and not bool(self._bearer_token())

    # ---- API handlers ----

    def handle_send_code(self):
        if rate_limited("send-code:" + self._client_key()):
            return self._error(429, "操作过于频繁，请稍后再试")
        body = self._read_body()
        email = (body.get("email") or "").strip().lower()
        purpose = (body.get("purpose") or "").strip()
        if not is_valid_email(email):
            return self._error(400, "请输入正确的邮箱地址")
        if purpose not in VALID_PURPOSES:
            return self._error(400, "无效的验证码用途")

        with db_connect() as conn:
            if purpose == "register":
                exists = conn.execute(
                    "SELECT id FROM users WHERE email = ?", (email,)
                ).fetchone()
                if exists:
                    return self._error(400, "该邮箱已注册，请直接登录")
            elif purpose == "reset":
                exists = conn.execute(
                    "SELECT id FROM users WHERE email = ?", (email,)
                ).fetchone()
                if exists is None:
                    return self._error(400, "该邮箱未注册")

            # 60 秒重发冷却
            last = conn.execute(
                """
                SELECT created_at FROM verification_codes
                WHERE email = ? AND purpose = ?
                ORDER BY id DESC LIMIT 1
                """,
                (email, purpose),
            ).fetchone()
            if last is not None:
                created = last["created_at"]
                try:
                    created_ts = datetime.datetime.strptime(
                        created, "%Y-%m-%dT%H:%M:%S.%fZ"
                    ).replace(tzinfo=datetime.timezone.utc)
                    if (
                        datetime.datetime.now(datetime.timezone.utc)
                        - created_ts
                    ).total_seconds() < CODE_RESEND_COOLDOWN:
                        return self._error(
                            429, f"发送过于频繁，请 {CODE_RESEND_COOLDOWN} 秒后再试"
                        )
                except ValueError:
                    pass

            code = generate_code()
            conn.execute(
                "DELETE FROM verification_codes WHERE email = ? AND purpose = ?",
                (email, purpose),
            )
            conn.execute(
                """
                INSERT INTO verification_codes
                    (email, code, purpose, created_at, expires_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    email,
                    code,
                    purpose,
                    now_iso(),
                    expires_in_iso(CODE_TTL_SECONDS),
                ),
            )
            conn.commit()

        send_verification_email(email, code, purpose)
        self._send_json({"ok": True, "message": "验证码已发送"})

    def handle_register(self, web_session=False):
        if rate_limited("register:" + self._client_key()):
            return self._error(429, "操作过于频繁，请稍后再试")
        body = self._read_body()
        email = (body.get("username") or "").strip().lower()
        password = body.get("password") or ""
        code = (body.get("code") or "").strip()
        if not is_valid_email(email):
            return self._error(400, "请输入正确的邮箱地址")
        if len(password) < 4:
            return self._error(400, "密码至少需要 4 位")
        if len(password) > 64:
            return self._error(400, "密码过长，请控制在 64 位以内")
        ok, msg = verify_code(email, code, "register")
        if not ok:
            return self._error(400, msg)
        with db_connect() as conn:
            exists = conn.execute(
                "SELECT id FROM users WHERE email = ?", (email,)
            ).fetchone()
            if exists:
                return self._error(400, "该邮箱已注册，请直接登录")
            salt = secrets.token_hex(16)
            cur = conn.execute(
                """
                INSERT INTO users (email, password_hash, salt, created_at)
                VALUES (?, ?, ?, ?)
                """,
                (email, hash_password(password, salt), salt, now_iso()),
            )
            user_id = cur.lastrowid
            token = create_session(conn, user_id)
            row = conn.execute(
                "SELECT * FROM users WHERE id = ?", (user_id,)
            ).fetchone()
            upsert_device(
                conn,
                user_id,
                str(body.get("id") or ""),
                str(body.get("uuid") or ""),
                body.get("deviceInfo") if isinstance(body.get("deviceInfo"), dict) else {},
            )
        payload, headers = auth_response(token, row, web_session)
        self._send_json(payload, extra_headers=headers)

    def handle_reset_password(self):
        if rate_limited("reset:" + self._client_key()):
            return self._error(429, "操作过于频繁，请稍后再试")
        body = self._read_body()
        email = (body.get("email") or "").strip().lower()
        code = (body.get("code") or "").strip()
        password = body.get("password") or ""
        if not is_valid_email(email):
            return self._error(400, "请输入正确的邮箱地址")
        if len(password) < 4:
            return self._error(400, "密码至少需要 4 位")
        if len(password) > 64:
            return self._error(400, "密码过长，请控制在 64 位以内")
        ok, msg = verify_code(email, code, "reset")
        if not ok:
            return self._error(400, msg)
        with db_connect() as conn:
            row = conn.execute(
                "SELECT * FROM users WHERE email = ?", (email,)
            ).fetchone()
            if row is None:
                return self._error(400, "该邮箱未注册")
            salt = row["salt"]
            conn.execute(
                "UPDATE users SET password_hash = ? WHERE id = ?",
                (hash_password(password, salt), row["id"]),
            )
            # 密码重置后强制全部设备重新登录
            conn.execute("DELETE FROM tokens WHERE user_id = ?", (row["id"],))
            conn.commit()
        self._send_json({"ok": True, "message": "密码已重置，请使用新密码登录"})

    def handle_change_email(self):
        user = self._auth_user()
        if user is None:
            return self._error(401, "登录已失效，请重新登录")
        body = self._read_body()
        new_email = (body.get("new_email") or "").strip().lower()
        code = (body.get("code") or "").strip()
        password = body.get("password") or ""
        if not is_valid_email(new_email):
            return self._error(400, "请输入正确的邮箱地址")
        if not password:
            return self._error(400, "请输入当前密码")
        if not verify_password(password, user["salt"], user["password_hash"]):
            return self._error(400, "当前密码错误")
        ok, msg = verify_code(new_email, code, "change_email")
        if not ok:
            return self._error(400, msg)
        with db_connect() as conn:
            taken = conn.execute(
                "SELECT id FROM users WHERE email = ? AND id != ?",
                (new_email, user["id"]),
            ).fetchone()
            if taken:
                return self._error(400, "该邮箱已被其他账号使用")
            conn.execute(
                "UPDATE users SET email = ? WHERE id = ?",
                (new_email, user["id"]),
            )
            conn.commit()
            row = conn.execute(
                "SELECT * FROM users WHERE id = ?", (user["id"],)
            ).fetchone()
        self._send_json({"ok": True, "user": user_to_payload(row)})

    def handle_login(self, web_session=False):
        if rate_limited("login:" + self._client_key()):
            return self._error(429, "操作过于频繁，请稍后再试")
        body = self._read_body()
        email = (body.get("username") or "").strip().lower()
        password = body.get("password") or ""
        if not email or not password:
            return self._error(400, "请输入邮箱和密码")
        with db_connect() as conn:
            row = conn.execute(
                "SELECT * FROM users WHERE email = ?", (email,)
            ).fetchone()
            if row is None or not verify_password(password, row["salt"], row["password_hash"]):
                return self._error(401, "邮箱或密码错误")
            if row["status"] == 0:
                return self._error(403, "账号已被禁用")
            token = create_session(conn, row["id"])
            upsert_device(
                conn,
                row["id"],
                str(body.get("id") or ""),
                str(body.get("uuid") or ""),
                body.get("deviceInfo") if isinstance(body.get("deviceInfo"), dict) else {},
            )
        payload, headers = auth_response(token, row, web_session)
        self._send_json(payload, extra_headers=headers)

    def handle_current_user(self):
        row = self._auth_user()
        if row is None:
            return self._error(401, "登录已失效，请重新登录")
        body = self._read_body()
        with db_connect() as conn:
            upsert_device(
                conn,
                row["id"],
                str(body.get("id") or ""),
                str(body.get("uuid") or ""),
                body.get("deviceInfo") if isinstance(body.get("deviceInfo"), dict) else {},
            )
        self._send_json(user_to_payload(row))

    def handle_logout(self, web_session=False):
        token = self._session_token()
        if token:
            with db_connect() as conn:
                conn.execute("DELETE FROM tokens WHERE token = ?", (token,))
                conn.commit()
        headers = {}
        if web_session:
            headers["Set-Cookie"] = (
                f"{WEB_COOKIE_NAME}=; Path=/; HttpOnly; SameSite=Lax; "
                "Max-Age=0"
                + ("; Secure" if COOKIE_SECURE else "")
            )
        self._send_json({"ok": True}, extra_headers=headers)

    def handle_peers(self):
        """登录账号下的设备列表（我的设备）。"""
        user = self._auth_user()
        if user is None:
            return self._error(401, "登录已失效，请重新登录")
        with db_connect() as conn:
            rows = conn.execute(
                "SELECT * FROM devices WHERE user_id = ? ORDER BY last_login DESC",
                (user["id"],),
            ).fetchall()
        data = []
        for r in rows:
            info = {
                "os": r["os"],
                "device_name": r["device_name"],
            }
            data.append(
                {
                    "id": r["id"],
                    "uuid": r["uuid"],
                    "info": info,
                    "status": 1,
                    "last_login": r["last_login"],
                    "user": user["email"],
                    "user_name": user["email"],
                    "device_group_name": None,
                    "note": "",
                }
            )
        self._send_json({"total": len(data), "data": data})

    def handle_delete_device(self, uuid):
        """删除当前账号下的设备记录。"""
        user = self._auth_user()
        if user is None:
            return self._error(401, "登录已失效，请重新登录")
        uuid = (uuid or "").strip()
        if not uuid:
            return self._error(400, "缺少设备标识")
        with db_connect() as conn:
            conn.execute(
                "DELETE FROM devices WHERE user_id = ? AND uuid = ?",
                (user["id"], uuid),
            )
            conn.commit()
        self._send_json({"ok": True})

    def handle_login_options(self):
        self._send_raw("[]")

    def handle_health(self):
        self._send_json({"ok": True, "service": "linkremote-api", "time": now_iso()})

    def handle_static(self, path):
        entry = STATIC_FILES.get(path)
        if entry is None:
            return self._error(404, "Not Found")
        relative, content_type, cache_control = entry
        try:
            with open(os.path.join(WEB_DIR, relative), "rb") as source:
                body = source.read()
        except OSError:
            return self._error(404, "Not Found")
        self._send(
            body,
            content_type=content_type,
            extra_headers={"Cache-Control": cache_control},
        )

    # ---- routing ----

    def _route(self):
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"
        method = self.command.upper()

        if method == "OPTIONS":
            if not self._origin_allowed():
                return self._error(403, "请求来源不受信任")
            return self._send(b"", status=204, content_type="text/plain")

        if self._requires_origin_check(path, method) and not self._origin_allowed():
            return self._error(403, "请求来源不受信任")

        if path == "/api/health":
            return self.handle_health()

        if path == "/api/register" and method == "POST":
            return self.handle_register()
        if path == "/api/web/register" and method == "POST":
            return self.handle_register(web_session=True)
        if path == "/api/login" and method == "POST":
            return self.handle_login()
        if path == "/api/web/login" and method == "POST":
            return self.handle_login(web_session=True)
        if path == "/api/currentUser" and method == "POST":
            return self.handle_current_user()
        if path == "/api/logout" and method == "POST":
            return self.handle_logout()
        if path == "/api/web/logout" and method == "POST":
            return self.handle_logout(web_session=True)
        if path == "/api/send-code" and method == "POST":
            return self.handle_send_code()
        if path == "/api/reset-password" and method == "POST":
            return self.handle_reset_password()
        if path == "/api/change-email" and method == "POST":
            return self.handle_change_email()
        if path == "/api/login-options" and method == "GET":
            return self.handle_login_options()
        if path == "/api/peers" and method == "GET":
            return self.handle_peers()
        if path.startswith("/api/peers/") and method == "DELETE":
            return self.handle_delete_device(path.rsplit("/", 1)[-1])

        # 地址簿（RustDesk 客户端契约）
        if path == "/api/ab" or path.startswith("/api/ab/"):
            if method == "GET":
                # GET /api/ab: 空地址簿返回字符串 null（客户端视为正常空 AB）
                if path == "/api/ab":
                    return self._send_raw("null")
                # 其他 /api/ab/* GET 返回空列表结构
                return self._send_json({"total": 0, "data": []})
            if method in ("POST", "PUT", "DELETE"):
                return self._send_raw("null")

        # 分组 / 用户 / 设备列表（客户端拉取失败仅提示，不影响主功能）
        if method == "GET" and (
            path == "/api/users"
            or path == "/api/device-group/accessible"
        ):
            return self._send_json({"total": 0, "data": []})

        if method == "GET" and not path.startswith("/api/"):
            return self.handle_static(path)

        self._error(404, "Not Found")

    def do_GET(self):
        self._route()

    def do_POST(self):
        self._route()

    def do_PUT(self):
        self._route()

    def do_DELETE(self):
        self._route()

    def do_OPTIONS(self):
        self._route()


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


if __name__ == "__main__":
    main()
