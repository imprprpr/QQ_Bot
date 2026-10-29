#!/usr/bin/env bash
# AnimaBot launcher for Linux / macOS: creates .venv, installs dependencies,
# runs the setup wizard on first use, then starts the bot.
set -euo pipefail
cd "$(dirname "$0")"

PY=""
for candidate in python3 python; do
  if command -v "$candidate" >/dev/null 2>&1 && "$candidate" -c 'import sys; sys.exit(sys.version_info < (3, 10))'; then
    PY="$candidate"; break
  fi
done
if [ -z "$PY" ]; then
  echo "[!] 需要 Python 3.10 或更高版本。Ubuntu/Debian: sudo apt install python3 python3-venv；macOS: brew install python"
  exit 1
fi

if [ ! -x .venv/bin/python ]; then
  echo "正在创建运行环境，只需一次..."
  "$PY" -m venv .venv || { echo "[!] 创建失败。Ubuntu/Debian 请先运行：sudo apt install python3-venv"; exit 1; }
fi

if ! cmp -s requirements.txt .venv/requirements.installed; then
  echo "正在安装依赖，第一次大约需要 1 到 3 分钟..."
  .venv/bin/python -m pip install --disable-pip-version-check -q -r requirements.txt \
    || { echo "默认下载源失败，改用国内镜像重试..."; .venv/bin/python -m pip install --disable-pip-version-check -q -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple; }
  cp requirements.txt .venv/requirements.installed
fi

.venv/bin/python tools/setup_wizard.py --check || .venv/bin/python tools/setup_wizard.py

[ -n "${ANIMABOT_NO_START:-}" ] && { echo "环境检查完成，已按 ANIMABOT_NO_START 跳过启动。"; exit 0; }
echo "AnimaBot 正在运行，按 Ctrl+C 停止。管理页面令牌：.venv/bin/python tools/manage.py"
exec .venv/bin/python bot.py
