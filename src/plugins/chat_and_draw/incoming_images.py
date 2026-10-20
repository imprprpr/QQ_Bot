"""Bounded image normalization and OneBot image/market-sticker resolution."""
import asyncio
import io
import re

from PIL import Image

from .config import MAX_IMAGE_BYTES
from .runtime import record

MAX_PIXELS = 16_000_000


def normalize_image(data):
    """Keep static API-friendly images; animated inputs become a PNG frame sheet."""
    if not data or len(data) > MAX_IMAGE_BYTES:
        raise ValueError("Image exceeds limit")
    with Image.open(io.BytesIO(data)) as image:
        if image.width * image.height > MAX_PIXELS:
            raise ValueError("Image dimensions exceed limit")
        if image.format not in {"JPEG", "PNG", "WEBP", "GIF"}:
            raise ValueError("Unsupported image format")
        animated = getattr(image, "is_animated", False)
        if image.format != "GIF" and not animated:
            image.verify()
            return data
        count = min(getattr(image, "n_frames", 1), 120)
        positions = sorted({0, (count - 1) // 2, count - 1})
        frames = []
        for index in positions:
            image.seek(index)
            if image.width * image.height > MAX_PIXELS:
                raise ValueError("Frame dimensions exceed limit")
            frame = image.convert("RGBA")
            frame.thumbnail((768, 768), Image.Resampling.LANCZOS)
            canvas = Image.new("RGB", frame.size, "white")
            canvas.paste(frame, mask=frame.getchannel("A"))
            frames.append(canvas)
        width, height = max(f.width for f in frames), max(f.height for f in frames)
        columns = 1 if len(frames) == 1 else 2
        rows = (len(frames) + columns - 1) // columns
        sheet = Image.new("RGB", (columns * width, rows * height), "white")
        for index, frame in enumerate(frames):
            sheet.paste(frame, ((index % columns) * width, (index // columns) * height))
        buffer = io.BytesIO()
        sheet.save(buffer, "PNG", optimize=True)
        result = buffer.getvalue()
        if len(result) > MAX_IMAGE_BYTES:
            raise ValueError("Normalized image exceeds limit")
        return result


def sticker_url(data):
    identity = data.get("emoji_id", "")
    if isinstance(identity, str) and re.fullmatch(r"[0-9a-fA-F]{32}", identity):
        # NapCat uses this QQ market-sticker CDN layout for incoming market faces.
        return f"https://gxh.vip.qq.com/club/item/parcel/item/{identity[:2]}/{identity}/raw300.gif"
    return None


async def read_image(bot, data, downloader):
    async def resolve():
        visited = set()
        async def attempt(value):
            if not isinstance(value, str) or not value or value in visited:
                return None
            visited.add(value)
            return await downloader(value)
        for value in (data.get("url"), data.get("file"), sticker_url(data)):
            blob = await attempt(value)
            if blob:
                await record("image-fetch", "completed", source="message-segment")
                return blob
        # The OneBot side may know a cache path/URL while the event has only a file ID.
        get_image = getattr(bot, "get_image", None)
        if get_image and data.get("file"):
            try:
                info = await get_image(file=data["file"])
                if isinstance(info, dict):
                    for field in ("url", "file", "file_path"):
                        blob = await attempt(info.get(field))
                        if blob:
                            await record("image-fetch", "completed", source="onebot-get-image")
                            return blob
                    encoded = info.get("base64")
                    if isinstance(encoded, str):
                        blob = await attempt(encoded if encoded.startswith("base64://") else "base64://" + encoded)
                        if blob:
                            await record("image-fetch", "completed", source="onebot-get-image")
                            return blob
            except Exception as exc:
                await record("image-resolve", "failed", error=type(exc).__name__)
        return None
    try:
        result = await asyncio.wait_for(resolve(), timeout=15)
        if not result:
            await record("image-fetch", "failed", error="NO_READABLE_IMAGE_SOURCE")
        return result
    except asyncio.TimeoutError:
        await record("image-fetch", "failed", error="IMAGE_FETCH_TIMEOUT")
        return None
    except Exception as exc:
        await record("image-fetch", "failed", error=type(exc).__name__)
        return None
