#!/usr/bin/env bash
# RemoteDesk 服务器一键部署（在 Linux 服务器上执行）
set -euo pipefail
cd "$(dirname "$0")"

if ! command -v docker >/dev/null 2>&1; then
  echo "未检测到 Docker，先执行："
  echo '  bash <(wget -qO- https://get.docker.com)'
  exit 1
fi

echo "==> 启动 hbbs + hbbr ..."
sudo docker compose up -d

echo "==> 等待服务器公钥生成 ..."
for _ in $(seq 1 30); do
  [ -f ./data/id_ed25519.pub ] && break
  sleep 1
done

if [ ! -f ./data/id_ed25519.pub ]; then
  echo "公钥文件未生成，请检查容器日志： sudo docker logs hbbs"
  exit 1
fi

echo
echo "部署完成，请记下以下信息："
echo "  服务器地址：你的域名或公网 IP（不带端口）"
echo "  服务器公钥：$(cat ./data/id_ed25519.pub)"
echo
echo "安全组 / 防火墙需放行：21115/tcp、21116/tcp、21116/udp、21117/tcp"
echo "  云厂商控制台放行后，可用  https://ping.eu/port-chk/  或 nc 验证。"
echo "账号与设备管理页需单独按 server/README.md 配置 HTTPS；不要开放 21118/21119。"
