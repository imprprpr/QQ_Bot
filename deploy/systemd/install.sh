#!/usr/bin/env bash
# Install AnimaBot as a systemd service for the user who owns the project folder.
#   sudo deploy/systemd/install.sh            install and start
#   sudo deploy/systemd/install.sh --print    only show the generated unit
set -euo pipefail
DIR="$(cd "$(dirname "$0")/../.." && pwd)"
OWNER="${SUDO_USER:-$(stat -c %U "$DIR")}"
UNIT="$(sed -e "s#__USER__#${OWNER}#g" -e "s#__DIR__#${DIR}#g" "$DIR/deploy/systemd/anima-bot.service" | grep -v '^# Template\|^# user and paths\|^# Placeholders')"

if [ "${1:-}" = "--print" ]; then echo "$UNIT"; exit 0; fi
if [ "$(id -u)" -ne 0 ]; then echo "请用 sudo 运行：sudo $0"; exit 1; fi
if [ ! -x "$DIR/.venv/bin/python" ]; then
  echo "还没有运行环境。请先以普通用户运行一次 ./start.sh 完成安装和配置向导。"; exit 1
fi
echo "$UNIT" > /etc/systemd/system/anima-bot.service
systemctl daemon-reload
systemctl enable --now anima-bot
echo "已安装并启动。查看状态：systemctl status anima-bot    查看日志：journalctl -u anima-bot -f"
