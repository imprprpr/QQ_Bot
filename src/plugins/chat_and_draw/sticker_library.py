"""Curated local reactions with role/scenario tags and bounded metadata."""
import io
import json
from pathlib import Path
import random
import re
import threading
import time
import uuid

from PIL import Image

from .config import project_path

_lock = threading.RLock()
MAX_BYTES = 5 * 1024 * 1024


class StickerLibrary:
    def __init__(self, root=None):
        self.root = Path(root) if root is not None else project_path("LOCAL_STICKER_DIR", "data/sticker_library")

    def _read(self):
        path = self.root / "library.json"
        if not path.exists():
            return []
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, list) or any(not isinstance(entry, dict) for entry in data):
            raise ValueError("Invalid local sticker index")
        return data

    def _write(self, entries):
        self.root.mkdir(parents=True, exist_ok=True)
        temporary = self.root / (uuid.uuid4().hex + ".tmp")
        try:
            temporary.write_text(json.dumps(entries, ensure_ascii=False, indent=2), encoding="utf-8")
            temporary.replace(self.root / "library.json")
        finally:
            temporary.unlink(missing_ok=True)

    @staticmethod
    def _strings(values):
        if isinstance(values, str):
            values = values.split(",")
        if not isinstance(values, list):
            raise ValueError("Tags must be a list")
        return list(dict.fromkeys(str(v).strip()[:80] for v in values if str(v).strip()))[:20]

    def list(self):
        with _lock:
            return self._read()

    def asset(self, identity):
        if not re.fullmatch(r"[0-9a-f]{32}", identity):
            raise ValueError("Invalid sticker ID")
        entry = next((e for e in self._read() if e["id"] == identity), None)
        if entry is None:
            raise ValueError("Sticker not found")
        path = (self.root / entry["file"]).resolve()
        if not path.is_relative_to(self.root.resolve()) or not path.is_file():
            raise ValueError("Invalid sticker path")
        return path

    def add(self, data, metadata):
        from .stickers import normalize_category
        if not data or len(data) > MAX_BYTES:
            raise ValueError("Image must be at most 5 MiB")
        with Image.open(io.BytesIO(data)) as image:
            suffix = {"GIF": "gif", "PNG": "png", "JPEG": "jpg", "WEBP": "webp"}.get(image.format)
            if suffix is None or image.width * image.height > 16_000_000:
                raise ValueError("Unsupported image format or dimensions")
            image.verify()
        emotions = self._strings(metadata.get("emotions", []))
        if not emotions or any(normalize_category(v) is None for v in emotions):
            raise ValueError("At least one supported emotion is required")
        identity = uuid.uuid4().hex
        entry = {"id": identity, "file": f"{identity}.{suffix}",
                 "name": str(metadata.get("name", "未命名表情"))[:80],
                 "emotions": list(dict.fromkeys(normalize_category(v) for v in emotions)),
                 "situations": self._strings(metadata.get("situations", [])),
                 "avoid": self._strings(metadata.get("avoid", [])),
                 "characters": self._strings(metadata.get("characters", [])),
                 "selected_count": 0, "last_selected": 0}
        with _lock:
            entries = self._read()
            if len(entries) >= 500:
                raise ValueError("Local library is limited to 500 stickers")
            self.root.mkdir(parents=True, exist_ok=True)
            path = self.root / entry["file"]
            path.write_bytes(data)
            try:
                self._write(entries + [entry])
            except BaseException:
                path.unlink(missing_ok=True)
                raise
        return entry

    def delete(self, identity):
        with _lock:
            path = self.asset(identity)
            self._write([e for e in self._read() if e["id"] != identity])
            path.unlink(missing_ok=True)

    def select(self, category, character=None, context=""):
        with _lock:
            entries = self._read()
            matches = []
            for entry in entries:
                if category not in entry.get("emotions", []):
                    continue
                if entry.get("characters") and character not in entry["characters"]:
                    continue
                if any(tag.lower() in context.lower() for tag in entry.get("avoid", [])):
                    continue
                score = sum(tag.lower() in context.lower() for tag in entry.get("situations", []))
                score += 2 if entry.get("characters") else 0
                matches.append((score, entry))
            if not matches:
                return None
            best = max(score for score, _ in matches)
            candidates = [e for score, e in matches if score == best]
            # Avoid the last choice when alternatives exist, then balance selection counts.
            if len(candidates) > 1:
                latest = max(candidates, key=lambda e: e.get("last_selected", 0))
                candidates = [e for e in candidates if e["id"] != latest["id"]]
            entry = random.choices(candidates, weights=[1 / (1 + e.get("selected_count", 0)) for e in candidates])[0]
            path = self.asset(entry["id"])
            if path.stat().st_size > MAX_BYTES:
                raise ValueError("Local sticker too large")
            data = path.read_bytes()
            entry["selected_count"] = entry.get("selected_count", 0) + 1
            entry["last_selected"] = time.time()
            self._write(entries)
            return data


library = StickerLibrary()
