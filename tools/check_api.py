"""Validate API settings; --request explicitly makes one small billed request."""
import argparse
import asyncio
import os
from pathlib import Path
import sys
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import nonebot
nonebot.init(log_level="INFO")
from src.plugins.chat_and_draw.llm import close_clients, get_client, response_content
from src.plugins.chat_and_draw.config import LLM_TIMEOUT


async def check(make_request):
    try:
        missing = [name for name in ("LLM_API_KEY", "LLM_BASE_URL", "LLM_MODEL") if not os.getenv(name, "").strip()]
        if missing:
            print("Missing settings: " + ", ".join(missing))
            return 1
        url = urlsplit(os.environ["LLM_BASE_URL"])
        if url.scheme not in {"https", "http"} or not url.hostname or url.username or url.password:
            print("LLM_BASE_URL must be an HTTP(S) URL without embedded credentials.")
            return 1
        print("API key, base URL and model ID are configured. Credentials are not displayed.")
        if make_request:
            response = await asyncio.wait_for(get_client().chat.completions.create(
                model=os.environ["LLM_MODEL"], messages=[{"role": "user", "content": "Reply OK."}],
                max_tokens=32), timeout=LLM_TIMEOUT)
            if not response_content(response):
                raise ValueError("Empty response")
            print("Model request succeeded.")
        else:
            print("No network request made. Use --request to test access (billed).")
        return 0
    except Exception as exc:
        print("API check failed: " + type(exc).__name__)
        return 1
    finally:
        await close_clients()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--request", action="store_true", help="Make one small billed request")
    raise SystemExit(asyncio.run(check(parser.parse_args().request)))
