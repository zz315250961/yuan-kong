#!/usr/bin/env python3
"""
RustDesk / RemoteDesk 定制配置注入脚本

在 GitHub Actions 构建前运行，根据环境变量把自建服务器地址、服务器公钥
和品牌名写入源码。环境变量留空时保持官方默认值，方便本地直接编译。

支持的环境变量：
  RS_SERVER      自建服务器地址，例如 rd.example.com（不要带端口，自动补 21116）
  RS_PUB_KEY     服务器公钥，例如 docker exec hbbs cat /root/id_ed25519.pub 的输出
  RS_APP_NAME    应用显示名，例如 RemoteDesk（默认不修改）
"""

import os
import re
import sys


def root_dir() -> str:
    """返回本文件所在的 rustdesk 仓库根目录。"""
    return os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def patch_file(path: str, replacements: list) -> bool:
    """对 path 依次执行 (regex, replacement) 替换，返回是否发生过修改。"""
    with open(path, "r", encoding="utf-8") as f:
        content = f.read()

    changed = False
    for pattern, value in replacements:
        new_content, count = re.subn(pattern, value, content)
        if count > 0:
            changed = True
            content = new_content
            print(f"[patched] {os.path.relpath(path, root_dir())}: {pattern!r} -> {value!r}")
        else:
            print(f"[skip]    {os.path.relpath(path, root_dir())}: pattern not found: {pattern!r}", file=sys.stderr)

    if changed:
        with open(path, "w", encoding="utf-8", newline="") as f:
            f.write(content)
    return changed


def main() -> int:
    repo = root_dir()
    server = os.environ.get("RS_SERVER", "").strip()
    pub_key = os.environ.get("RS_PUB_KEY", "").strip()
    app_name = os.environ.get("RS_APP_NAME", "").strip()

    if not (server or pub_key or app_name):
        print("No RS_SERVER / RS_PUB_KEY / RS_APP_NAME provided; keeping upstream defaults.")
        return 0

    config_rs = os.path.join(repo, "libs", "hbb_common", "src", "config.rs")
    if not os.path.exists(config_rs):
        print(f"config.rs not found: {config_rs}", file=sys.stderr)
        return 1

    replacements = []
    if server:
        replacements.append(
            (
                r'pub const RENDEZVOUS_SERVERS: &\[&str\] = &\[[^\]]*\];',
                f'pub const RENDEZVOUS_SERVERS: &[&str] = &["{server}"];',
            )
        )
    if pub_key:
        replacements.append(
            (
                r'pub const RS_PUB_KEY: &str = "[^"]*";',
                f'pub const RS_PUB_KEY: &str = "{pub_key}";',
            )
        )
    if app_name:
        replacements.append(
            (
                r'APP_NAME: RwLock<String> = RwLock::new\("[^"]*"\.to_owned\(\)\);',
                f'APP_NAME: RwLock<String> = RwLock::new("{app_name}".to_owned());',
            )
        )

    patch_file(config_rs, replacements)

    if app_name:
        manifest = os.path.join(repo, "flutter", "android", "app", "src", "main", "AndroidManifest.xml")
        if os.path.exists(manifest):
            patch_file(
                manifest,
                [
                    (r'android:label="RustDesk Input"', f'android:label="{app_name} Input"'),
                    (r'android:label="RustDesk"', f'android:label="{app_name}"'),
                ],
            )
        else:
            print(f"AndroidManifest.xml not found: {manifest}", file=sys.stderr)

    print("Custom configuration applied.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
