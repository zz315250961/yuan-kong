"""Account login-session summaries and bounded password-attempt bookkeeping.

Device metadata is display-only. A session's opaque ID is never its bearer token
or a derivation of that credential; existing tokens remain valid on migration.
"""
import re
import secrets
import threading
import time


SESSION_ID = re.compile(r"[A-Za-z0-9_-]{22,64}\Z")
_COLUMNS = {
    "session_id": "TEXT NOT NULL DEFAULT ''",
    "device_name": "TEXT NOT NULL DEFAULT ''",
    "platform": "TEXT NOT NULL DEFAULT ''",
    "device_uuid": "TEXT NOT NULL DEFAULT ''",
    "last_seen_at": "TEXT NOT NULL DEFAULT ''",
}


def init_session_schema(conn):
    columns = {row[1] for row in conn.execute("PRAGMA table_info(tokens)")}
    for name, definition in _COLUMNS.items():
        if name not in columns:
            conn.execute(f"ALTER TABLE tokens ADD COLUMN {name} {definition}")
    for row in conn.execute("SELECT token FROM tokens WHERE session_id = ''").fetchall():
        conn.execute("UPDATE tokens SET session_id=? WHERE token=?",
                     (secrets.token_urlsafe(18), row[0]))
    conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_tokens_session_id ON tokens(session_id)")


def _display_text(value, limit):
    if not isinstance(value, str):
        return ""
    return " ".join(value.split())[:limit]


def session_metadata(body, web_session=False):
    info = body.get("deviceInfo")
    info = info if isinstance(info, dict) else {}
    return {
        "device_name": _display_text(info.get("name", info.get("device_name", "")), 80),
        "platform": "web" if web_session else _display_text(info.get("os", ""), 32),
        "device_uuid": _display_text(body.get("uuid", ""), 128),
    }


def insert_session(conn, token, user_id, created_at, expires_at, metadata=None):
    metadata = metadata or {}
    conn.execute(
        "INSERT INTO tokens(token,user_id,created_at,expires_at,session_id,device_name,platform,device_uuid,last_seen_at) "
        "VALUES (?,?,?,?,?,?,?,?,?)",
        (token, user_id, created_at, expires_at, secrets.token_urlsafe(18),
         metadata.get("device_name", ""), metadata.get("platform", ""),
         metadata.get("device_uuid", ""), created_at),
    )


def list_sessions(conn, user_id, current_token, now):
    rows = conn.execute(
        "SELECT session_id,device_name,platform,created_at,last_seen_at,token "
        "FROM tokens WHERE user_id=? AND expires_at>? "
        "ORDER BY last_seen_at DESC,created_at DESC,session_id",
        (user_id, now),
    ).fetchall()
    return [{"id": row["session_id"], "device_name": row["device_name"],
             "platform": row["platform"], "created_at": row["created_at"],
             "last_seen_at": row["last_seen_at"], "is_current": row["token"] == current_token}
            for row in rows]


class RevokeAttemptLimiter:
    """Five failed/in-flight attempts per account/source in a 15-minute window.

    Reserve before checking a password, so concurrent requests cannot all pass an
    initially empty counter. Success removes only its own reservation.
    """
    def __init__(self):
        self._lock = threading.Lock()
        self._buckets = {}

    def begin(self, key):
        now = time.time()
        with self._lock:
            for old_key in list(self._buckets):
                kept = [entry for entry in self._buckets[old_key] if now - entry[0] < 900]
                if kept:
                    self._buckets[old_key] = kept
                else:
                    del self._buckets[old_key]
            bucket = self._buckets.get(key, [])
            if len(bucket) >= 5 or (key not in self._buckets and len(self._buckets) >= 4096):
                return None
            marker = object()
            self._buckets.setdefault(key, []).append((now, marker))
            return marker

    def finish(self, key, marker, failed):
        if failed:
            return
        with self._lock:
            remaining = [entry for entry in self._buckets.get(key, []) if entry[1] is not marker]
            if remaining:
                self._buckets[key] = remaining
            else:
                self._buckets.pop(key, None)
