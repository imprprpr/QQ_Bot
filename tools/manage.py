"""Print local management URL and token. Run manually; never share the token."""
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import nonebot
nonebot.init(log_level="INFO")
from src.plugins.chat_and_draw import store
from src.plugins.chat_and_draw.admin import admin_token

if __name__ == "__main__":
    print("Management URL: http://127.0.0.1:" + os.getenv("PORT", "8080") + "/manage")
    print("Local management token (keep private):")
    print(admin_token(store))
