"""ADC readiness check; --request additionally makes one small billed model call."""
import argparse
import asyncio
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import nonebot
nonebot.init(log_level="INFO")
from src.plugins.chat_and_draw.vertex import auth, endpoint, generate, VertexError


async def check(make_request):
    try:
        # Do not print the returned headers: they contain the access token.
        _, detected_project = await asyncio.wait_for(asyncio.to_thread(auth.headers), timeout=30)
        project = os.getenv("GOOGLE_CLOUD_PROJECT") or detected_project
        endpoint(project)  # Validate routing without printing credentials.
        print("ADC credentials loaded and refreshed successfully.")
        print("Project:", project)
        print("Location:", os.getenv("GOOGLE_CLOUD_LOCATION", "global"))
        print("Model:", os.getenv("VERTEX_MODEL", "gemini-3.8-flash"))
        if make_request:
            response = await generate("只返回简短的连接确认。",
                                      [{"role": "user", "content": "请回复：连接成功"}])
            print("Model request succeeded:", response)
        else:
            print("No model request made. Use --request to verify model access (billed).")
        return 0
    except Exception as exc:
        print("Check failed:", str(exc) if isinstance(exc, VertexError) else type(exc).__name__)
        print("For local ADC, install Google Cloud CLI and run:")
        print("  gcloud auth application-default login")
        project = os.getenv("GOOGLE_CLOUD_PROJECT", "YOUR_PROJECT_ID")
        print("  gcloud auth application-default set-quota-project " + project)
        print("For HTTP_403, verify the account's project permissions and Vertex AI API access.")
        return 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--request", action="store_true", help="Make one small billed model request")
    args = parser.parse_args()
    raise SystemExit(asyncio.run(check(args.request)))
