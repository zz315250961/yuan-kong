#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
管理员兜底：直接重置某个账号的密码（忘记密码自助流程不可用时的后备通道）。

用法（服务器上，SSH 登录后执行）：
    cd /opt/linkremote-api
    python3 reset_password.py <邮箱> <新密码>

说明：
    - 新密码至少 4 位
    - 重置成功后该账号全部设备的登录态（token）都会被清除，需重新登录
    - 密码使用与 app.py 相同的 PBKDF2-SHA256 加盐哈希
"""

import sys
import sqlite3
import hashlib
import hmac
import os

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "data", "linkremote.db")
PBKDF2_ITERATIONS = 120_000


def hash_password(password: str, salt: str) -> str:
    return hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt.encode("utf-8"),
        PBKDF2_ITERATIONS,
    ).hex()


def main():
    if len(sys.argv) != 3:
        print("用法: python3 reset_password.py <邮箱> <新密码>")
        sys.exit(1)
    email = sys.argv[1].strip().lower()
    password = sys.argv[2]
    if len(password) < 4:
        print("错误: 新密码至少需要 4 位")
        sys.exit(1)
    if not os.path.exists(DB_PATH):
        print(f"错误: 数据库不存在 {DB_PATH}")
        sys.exit(1)
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(
            "SELECT * FROM users WHERE email = ?", (email,)
        ).fetchone()
        if row is None:
            print(f"错误: 邮箱 {email} 未注册")
            sys.exit(1)
        new_hash = hash_password(password, row["salt"])
        if hmac.compare_digest(new_hash, row["password_hash"]):
            print("提示: 新密码与原密码相同，未做修改")
        conn.execute(
            "UPDATE users SET password_hash = ? WHERE id = ?",
            (new_hash, row["id"]),
        )
        deleted = conn.execute(
            "DELETE FROM tokens WHERE user_id = ?", (row["id"],)
        ).rowcount
        conn.commit()
        print(f"已重置 {email} 的密码，并清除其全部 {deleted} 个设备的登录态")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
