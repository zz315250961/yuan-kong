#!/usr/bin/env bash
# 打印服务器公钥（在服务器上执行），输出内容用于客户端构建参数 RS_PUB_KEY
set -euo pipefail
cd "$(dirname "$0")"
[ -f ./data/id_ed25519.pub ] || {
  echo "公钥文件不存在，请先执行 sudo docker compose up -d 并等待 hbbs 生成。" >&2
  exit 1
}
cat ./data/id_ed25519.pub
