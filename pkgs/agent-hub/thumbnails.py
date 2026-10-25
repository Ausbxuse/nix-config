"""On-demand, bounded previews. Originals are never modified or copied."""
import base64
from collections import OrderedDict
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import threading
import time

from bridge import BridgeError

FORMATS = {'.png': 'png_pipe', '.jpg': 'jpeg_pipe', '.jpeg': 'jpeg_pipe',
           '.webp': 'webp_pipe', '.gif': 'gif', '.mp4': 'mov', '.mov': 'mov',
           '.webm': 'matroska', '.ogv': 'ogg', '.pdf': 'pdf'}
VIDEOS = {'.mp4', '.mov', '.webm', '.ogv'}


def fingerprint(info):
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


class Thumbnails:
    def __init__(self, limit=64, byte_limit=16 * 1024 * 1024):
        self.limit, self.byte_limit = limit, byte_limit
        self.cache, self.lock = OrderedDict(), threading.RLock()
        self.slots = threading.BoundedSemaphore(2)
        self.admission = threading.BoundedSemaphore(6)

    def render(self, agent, name):
        if not isinstance(name, str) or len(name) > 4096:
            raise BridgeError('Invalid preview path.')
        try:
            root = Path(agent['cwd']).resolve(strict=True)
            path = (root / name).resolve(strict=True)
            format = FORMATS.get(path.suffix.lower())
            if root == Path('/') or not path.is_relative_to(root) or not format:
                raise ValueError()
            # An already opened descriptor fixes the file used by both decoders.
            fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
            with os.fdopen(fd, 'rb') as stream:
                info = os.fstat(stream.fileno())
                video = path.suffix.lower() in VIDEOS
                if not stat.S_ISREG(info.st_mode) or not 0 < info.st_size <= (2 * 1024**3 if video else 64 * 1024**2):
                    raise ValueError()
                version = 'pdf-page1-jpeg-1000-v1' if format == 'pdf' else 'webp-640x400-v1'
                key = hashlib.sha256(json.dumps([str(path), fingerprint(info), version]).encode()).hexdigest()
                with self.lock:
                    if key in self.cache:
                        self.cache.move_to_end(key)
                        return self.cache[key]
                if not self.admission.acquire(blocking=False):
                    raise BridgeError('Previews are busy. Open the original file or try again.')
                try:
                    if not self.slots.acquire(timeout=2):
                        raise BridgeError('Previews are busy. Open the original file or try again.')
                    try:
                        with self.lock:
                            if key in self.cache:
                                return self.cache[key]
                        data = self.generate(stream.fileno(), format)
                    finally:
                        self.slots.release()
                finally:
                    self.admission.release()
                if fingerprint(os.fstat(stream.fileno())) != fingerprint(info) or fingerprint(path.stat()) != fingerprint(info):
                    raise BridgeError('The file changed while its preview was being made. Try again.')
                valid = data.startswith(b'\xff\xd8') and data.endswith(b'\xff\xd9') if format == 'pdf' else data.startswith(b'RIFF') and data[8:12] == b'WEBP'
                if not 0 < len(data) <= 1024 * 1024 or not valid:
                    raise ValueError()
                value = {'data': base64.b64encode(data).decode(), 'type': 'image/jpeg' if format == 'pdf' else 'image/webp', 'etag': key,
                         'size': len(data), 'source_size': info.st_size, 'poster': video}
                with self.lock:
                    self.cache[key] = value
                    while len(self.cache) > self.limit or sum(len(v['data']) for v in self.cache.values()) > self.byte_limit:
                        self.cache.popitem(last=False)
                return value
        except (OSError, ValueError, KeyError, TypeError):
            raise BridgeError('Preview unavailable. Open the original file instead.') from None

    def generate(self, fd, format):
        if format == 'pdf':
            return self.pdf(fd)
        ffmpeg = os.environ.get('AGENT_HUB_FFMPEG') or shutil.which('ffmpeg')
        ffprobe = os.environ.get('AGENT_HUB_FFPROBE') or shutil.which('ffprobe')
        prlimit = os.environ.get('AGENT_HUB_PRLIMIT') or shutil.which('prlimit')
        if not all((ffmpeg, ffprobe, prlimit)):
            raise BridgeError('Preview tools are unavailable on this host. Open the original file instead.')
        limits = [prlimit, '--as=805306368', '--cpu=6', '--fsize=1048576', '--']
        inputs = ['-v', 'error', '-protocol_whitelist', 'file,pipe', '-max_alloc', '134217728',
                  '-probesize', '1048576', '-analyzeduration', '1000000', '-threads', '1', '-f', format]
        if format == 'mov':
            inputs += ['-enable_drefs', '0', '-use_absolute_path', '0']
        source = f'/proc/self/fd/{fd}'
        deadline = time.monotonic() + 8
        def run(command):
            try:
                result = subprocess.run(limits + command, pass_fds=(fd,), stdin=subprocess.DEVNULL,
                                        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                        timeout=max(.05, deadline - time.monotonic()), check=True)
                return result.stdout
            except (subprocess.SubprocessError, OSError):
                raise BridgeError('Preview unavailable or too costly to decode. Open the original file instead.') from None
        metadata = json.loads(run([ffprobe, *inputs, '-select_streams', 'v:0', '-show_entries',
                                   'stream=width,height', '-of', 'json', source]))
        streams = metadata.get('streams', [])
        if not streams or not 0 < streams[0].get('width', 0) * streams[0].get('height', 0) <= 32_000_000:
            raise BridgeError('This image is too large for a preview. Open the original file instead.')
        os.lseek(fd, 0, os.SEEK_SET)
        return run([ffmpeg, '-nostdin', *inputs, '-i', source, '-map', '0:v:0', '-an', '-sn', '-dn',
                    '-frames:v', '1', '-filter_threads', '1', '-vf',
                    "scale=w='min(640,iw)':h='min(400,ih)':force_original_aspect_ratio=decrease",
                    '-c:v', 'libwebp', '-threads', '1', '-quality', '78', '-compression_level', '2',
                    '-map_metadata', '-1', '-f', 'image2pipe', 'pipe:1'])

    def pdf(self, fd):
        renderer = os.environ.get('AGENT_HUB_PDFTOPPM') or shutil.which('pdftoppm')
        prlimit = os.environ.get('AGENT_HUB_PRLIMIT') or shutil.which('prlimit')
        if not renderer or not prlimit:
            raise BridgeError('PDF previews are unavailable on this host. Open the original file instead.')
        try:
            result = subprocess.run([prlimit, '--as=805306368', '--cpu=6', '--fsize=1048576', '--', renderer,
                '-f', '1', '-l', '1', '-singlefile', '-scale-to', '1000', '-jpeg', '-jpegopt', 'quality=80',
                f'/proc/self/fd/{fd}'], pass_fds=(fd,), stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=8, check=True)
            return result.stdout
        except (subprocess.SubprocessError, OSError):
            raise BridgeError('PDF preview unavailable. Open the original file instead.') from None
