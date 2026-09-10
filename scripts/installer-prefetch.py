"""Warm compressed Squashfs pages without unpacking or retaining a RAM copy."""
import argparse
import os
from pathlib import Path
import signal
import stat

CHUNK = 4 * 1024 * 1024
MIN_AVAILABLE = 512 * 1024 * 1024


def available_memory():
    for line in Path('/proc/meminfo').read_text().splitlines():
        if line.startswith('MemAvailable:'):
            return int(line.split()[1]) * 1024
    return 0


def prefetch(image, max_bytes=None, memory=available_memory):
    available = memory()
    if available < MIN_AVAILABLE:
        return 0
    budget = available // 2
    if max_bytes is not None:
        budget = min(budget, max(0, max_bytes))
    total = 0
    with open(image, 'rb', buffering=0) as source:
        if not stat.S_ISREG(os.fstat(source.fileno()).st_mode):
            return 0
        if os.pread(source.fileno(), 4, 0) != b'hsqs':
            return 0
        # Read the actual loop backing file: its compressed pages are reused by
        # Squashfs, and take less cache space than unpacked /nix/store files.
        while total < budget and memory() >= MIN_AVAILABLE:
            data = source.read(min(CHUNK, budget - total))
            if not data:
                break
            total += len(data)
    return total


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--image', default='/iso/nix-store.squashfs')
    parser.add_argument('--max-bytes', type=int)
    args = parser.parse_args()
    if os.environ.get('NIXOS_INSTALLER_PREFETCH', '1') == '0':
        return
    signal.signal(signal.SIGTERM, lambda *_: exit(0))
    try:
        os.nice(10)
        prefetch(args.image, args.max_bytes)
    except (OSError, ValueError):
        # Optional read-ahead must never prevent installation.
        return


if __name__ == '__main__':
    main()
