"""Local, bounded image attachments. SQLite stores opaque IDs, never base64."""
import base64
import hashlib
import io
import re
import time
import uuid

from PIL import Image

from .config import IMAGE_CACHE_DIR, IMAGE_CACHE_MAX_BYTES, IMAGE_CACHE_TTL, MAX_IMAGE_BYTES

_ID = re.compile(r"^[0-9a-f]{64}$")


def cleanup_images():
    if not IMAGE_CACHE_DIR.exists():
        return
    entries = []
    for path in IMAGE_CACHE_DIR.glob("*.img"):
        try:
            stat = path.stat()
            if stat.st_mtime < time.time() - IMAGE_CACHE_TTL:
                path.unlink()
            else:
                entries.append((stat.st_mtime, stat.st_size, path))
        except OSError:
            continue
    total = sum(size for _, size, _ in entries)
    for _, size, path in sorted(entries):
        if total <= IMAGE_CACHE_MAX_BYTES:
            break
        try:
            path.unlink()
            total -= size
        except OSError:
            continue


def image_mime(data: bytes) -> str:
    with Image.open(io.BytesIO(data)) as image:
        mime = Image.MIME.get(image.format)
        if mime not in {"image/jpeg", "image/png", "image/webp"}:
            raise ValueError("Only JPEG, PNG and WebP images are supported by this backend")
        image.verify()
        return mime


def save_image(encoded: str) -> str:
    if len(encoded) > ((MAX_IMAGE_BYTES + 2) // 3) * 4:
        raise ValueError("Image exceeds size limit")
    data = base64.b64decode(encoded, validate=True)
    if len(data) > min(MAX_IMAGE_BYTES, IMAGE_CACHE_MAX_BYTES):
        raise ValueError("Image exceeds size limit")
    image_mime(data)
    identity = hashlib.sha256(data).hexdigest()
    IMAGE_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    temporary = IMAGE_CACHE_DIR / f"{uuid.uuid4().hex}.tmp"
    try:
        temporary.write_bytes(data)
        temporary.replace(IMAGE_CACHE_DIR / f"{identity}.img")
    finally:
        temporary.unlink(missing_ok=True)
    cleanup_images()
    return identity


def load_image(identity: str) -> dict | None:
    if not isinstance(identity, str) or not _ID.fullmatch(identity):
        return None
    path = IMAGE_CACHE_DIR / f"{identity}.img"
    try:
        if path.stat().st_mtime < time.time() - IMAGE_CACHE_TTL:
            return None
        with path.open("rb") as file:
            data = file.read(MAX_IMAGE_BYTES + 1)
        if len(data) > MAX_IMAGE_BYTES:
            return None
        return {"mimeType": image_mime(data), "data": base64.b64encode(data).decode("ascii")}
    except (OSError, ValueError):
        return None
