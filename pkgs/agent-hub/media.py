"""Bounded artifact reads restricted to an agent's project directory."""
import base64
import os
from pathlib import Path
import stat
from bridge import BridgeError

TYPES = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp", ".gif": "image/gif", ".pdf": "application/pdf", ".txt": "text/plain; charset=utf-8", ".md": "text/plain; charset=utf-8", ".json": "text/plain; charset=utf-8", ".csv": "text/plain; charset=utf-8", ".mp4": "video/mp4", ".webm": "video/webm", ".ogv": "video/ogg", ".mov": "video/quicktime"}
TEXT_TYPE = "text/plain; charset=utf-8"
TYPES.update(dict.fromkeys(("." + suffix for suffix in "nix py js jsx ts tsx mjs cjs sh bash zsh fish yaml yml toml ini cfg conf lock log c h cc hh cpp hpp rs go java rb lua sql css scss html xml svg tex rst patch diff ipynb jsonl ndjson tsv".split()), TEXT_TYPE))
TEXT_NAMES = {"Makefile", "Dockerfile", "Justfile", "CMakeLists.txt", ".gitignore", ".gitattributes", ".editorconfig"}

def read_video(agent, name, start=0, end=None):
    try:
        root = Path(agent["cwd"]).resolve(strict=True)
        path = (root / name).resolve(strict=True)
        content_type = TEXT_TYPE if path.name in TEXT_NAMES else TYPES.get(path.suffix.lower())
        if root == Path("/") or not path.is_relative_to(root) or not content_type:
            raise ValueError()
        start = int(start)
        with path.open("rb") as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or start < 0 or (start >= info.st_size and not start == info.st_size == 0):
                raise ValueError()
            if info.st_size == 0:
                return {"data":"", "start":0, "end":-1, "size":0, "type":content_type}
            stop = min(info.st_size - 1, start + 512 * 1024 - 1, int(end) if end is not None else info.st_size - 1)
            if stop < start:
                raise ValueError()
            stream.seek(start)
            data = stream.read(stop - start + 1)
        return {"data": base64.b64encode(data).decode(), "start": start,
                "end": start + len(data) - 1, "size": info.st_size, "type": content_type}
    except (OSError, ValueError, TypeError, KeyError):
        raise BridgeError("File unavailable. Use a supported file inside this agent's project directory.") from None
