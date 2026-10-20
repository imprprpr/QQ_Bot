"""Shared settings. Existing .env values and process environment are preserved."""
import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[3]
load_dotenv(ROOT / ".env")


def positive_number(name: str, default: float) -> float:
    value = float(os.getenv(name, str(default)))
    if not 0 < value < float("inf"):
        raise ValueError(f"{name} must be a finite positive number")
    return value


def project_path(name: str, default: str) -> Path:
    path = Path(os.getenv(name, default))
    return path if path.is_absolute() else ROOT / path


DATABASE_PATH = project_path("BOT_DATABASE", "data/sessions.sqlite3")
HISTORY_LIMIT = max(2, int(os.getenv("HISTORY_LIMIT", "30")))
HISTORY_CHAR_LIMIT = max(1000, int(os.getenv("HISTORY_CHAR_LIMIT", "16000")))
LLM_TIMEOUT = positive_number("LLM_TIMEOUT", 45)
DRAW_TIMEOUT = positive_number("DRAW_TIMEOUT", 120)
DRAW_POLL_INTERVAL = positive_number("DRAW_POLL_INTERVAL", 2)
TTS_TIMEOUT = positive_number("TTS_TIMEOUT", 30)
TTS_CACHE_DIR = project_path("TTS_CACHE_DIR", "data/tts_cache")
TTS_CACHE_TTL = positive_number("TTS_CACHE_TTL", 86400)
MAX_IMAGE_BYTES = int(positive_number("MAX_IMAGE_BYTES", 10 * 1024 * 1024))
MAX_IMAGES = max(1, int(os.getenv("MAX_IMAGES", "4")))
IMAGE_CACHE_DIR = project_path("IMAGE_CACHE_DIR", "data/image_cache")
IMAGE_CACHE_TTL = positive_number("IMAGE_CACHE_TTL", 7 * 86400)
IMAGE_CACHE_MAX_BYTES = int(positive_number("IMAGE_CACHE_MAX_BYTES", 256 * 1024 * 1024))
# Limit image history separately from text; base64 expands requests by roughly 4/3.
HISTORY_IMAGES = max(1, int(os.getenv("HISTORY_IMAGES", "4")))
