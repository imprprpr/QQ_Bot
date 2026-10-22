"""API image backend with an optional trusted, deployment-local adapter."""
import asyncio
import base64
import importlib.util
import io
import os
from pathlib import Path
from urllib.parse import urlsplit

from PIL import Image

from .config import ROOT, DRAW_TIMEOUT
from .http_transport import request_json

_adapters = {}
_semaphore = asyncio.Semaphore(1)
MAX_BYTES = 12 * 1024 * 1024


def validate_image(data):
    if not isinstance(data, bytes) or not data or len(data) > MAX_BYTES:
        raise ValueError("INVALID_GENERATED_IMAGE_SIZE")
    with Image.open(io.BytesIO(data)) as image:
        if image.width * image.height > 20_000_000 or image.format not in {"PNG", "JPEG", "WEBP"}:
            raise ValueError("INVALID_GENERATED_IMAGE_FORMAT")
        image.verify()
    return data


async def generate_image(prompt):
    async with _semaphore:
        filename = os.getenv("DRAW_CLIENT_FACTORY", "").strip()
        if filename:
            path = Path(filename)
            if not path.is_absolute():
                path = ROOT / path
            path = path.resolve()
            if path not in _adapters:
                spec = importlib.util.spec_from_file_location("bot_private_image_adapter", path)
                if spec is None or spec.loader is None:
                    raise ValueError("INVALID_IMAGE_ADAPTER")
                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)
                _adapters[path] = module.generate_image
            data = await _adapters[path](prompt)
        else:
            key = os.getenv("DRAW_API_KEY", "").strip()
            if not key:
                raise ValueError("MISSING_DRAW_API_KEY")
            url = os.getenv("DRAW_API_BASE_URL", "https://api.openai.com/v1").rstrip("/") + "/images/generations"
            async def headers(force=False):
                return {"Authorization": "Bearer " + key}
            proxy = os.getenv("DRAW_PROXY") or None
            if urlsplit(url).hostname in {"localhost", "127.0.0.1", "::1"}:
                proxy = None
            payload = {"model": os.getenv("DRAW_API_MODEL", "dall-e-3"), "prompt": prompt,
                       "n": 1, "size": os.getenv("DRAW_API_SIZE", "1024x1024"), "response_format": "b64_json"}
            # Do not automatically repeat paid image generation after an ambiguous timeout.
            response = await request_json(url, payload, headers, timeout=max(1, DRAW_TIMEOUT - 1),
                attempt_timeout=max(1, DRAW_TIMEOUT - 1), max_attempts=1,
                proxy=proxy, label="image-api", max_bytes=MAX_BYTES * 4 // 3 + 65536)
            items = response.get("data") or []
            if not items or not items[0].get("b64_json"):
                raise ValueError("IMAGE_API_NO_INLINE_IMAGE")
            data = base64.b64decode(items[0]["b64_json"], validate=True)
        return await asyncio.to_thread(validate_image, data)
