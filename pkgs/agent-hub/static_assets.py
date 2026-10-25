"""Versioned public application files; private API/media responses never enter this cache."""
from functools import lru_cache
import hashlib
import mimetypes
from pathlib import Path
import re


class StaticAssets:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.files = {}
        digest = hashlib.sha256()
        for file in sorted(self.root.rglob('*')):
            if not file.is_file() or file.is_symlink() or not file.resolve().is_relative_to(self.root):
                continue
            name = file.relative_to(self.root).as_posix()
            content_hash = hashlib.sha256(file.read_bytes()).digest()
            self.files[name] = file
            digest.update(name.encode() + b'\0' + content_hash)
        self.version = digest.hexdigest()[:24]
        self.prefix = '/static/' + self.version + '/'
        self.cache = {}

    def url(self, path):
        return self.prefix + path.lstrip('/') if path.lstrip('/') in self.files else path

    def get(self, path):
        versioned = path.startswith('/static/')
        if versioned and not path.startswith(self.prefix):
            return None
        name = path[len(self.prefix):] if versioned else path.lstrip('/') or 'index.html'
        if name not in self.files:
            return None
        if name not in self.cache:
            content = self.files[name].read_bytes()
            if name == 'index.html':
                content = re.sub(r'\b(src|href)="(/[^"?#]+)"',
                    lambda m: m[1] + '="' + self.url(m[2]) + '"', content.decode()).encode()
            elif name.endswith('.css'):
                content = re.sub(r'url\(([\"\x27]?)(/[^)\"\x27?#]+)\1\)',
                    lambda m: 'url(' + m[1] + self.url(m[2]) + m[1] + ')', content.decode()).encode()
            self.cache[name] = (content, mimetypes.guess_type(name)[0] or 'application/octet-stream',
                                '"' + hashlib.sha256(content).hexdigest() + '"')
        content, kind, etag = self.cache[name]
        policy = 'public, max-age=31536000, immutable' if versioned and name != 'index.html' else 'no-cache'
        return content, kind, etag, policy


@lru_cache(maxsize=4)
def assets_for(root):
    return StaticAssets(root)
