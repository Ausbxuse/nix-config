"""Private controller snapshots and offline restore staging. Never starts a hub.

Backups contain credentials and conversations. Keep them on a trusted device;
this format has integrity checks, not encryption or authenticity signatures.
"""
import argparse
from contextlib import closing, contextmanager
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import stat
import time

from federation import read_config


FILES = {'node.json', 'hub.sqlite', 'plans.json', 'wallpaper.jpg',
         'archive/archive-v1.sqlite', 'tls/fullchain.pem', 'tls/key.pem'}
REQUIRED = {'node.json', 'hub.sqlite'}
LIMIT = 4 * 1024**3


def regular(path, private=False):
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid():
        raise ValueError('Expected an owned regular file; links are not accepted')
    if private and info.st_mode & 0o077:
        raise ValueError('Private files require mode 0600')
    if info.st_size > LIMIT:
        raise ValueError('Backup file exceeds the size limit')
    return info


def directory(path):
    info = path.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise ValueError('Backup directories must be owned, private, and not links')


def parents(path):
    missing = []
    while not path.exists() and not path.is_symlink():
        missing.append(path)
        path = path.parent
    directory(path)
    for path in reversed(missing):
        path.mkdir(mode=0o700)


def write(path, data):
    parents(path.parent)
    with path.open('xb') as output:
        os.chmod(path, 0o600)
        output.write(data)
        output.flush()
        os.fsync(output.fileno())


def copy(source, target):
    regular(source)
    parents(target.parent)
    with source.open('rb') as incoming, target.open('xb') as outgoing:
        os.chmod(target, 0o600)
        size = 0
        while chunk := incoming.read(1024 * 1024):
            size += len(chunk)
            if size > LIMIT:
                raise ValueError('Backup file exceeds the size limit')
            outgoing.write(chunk)
        outgoing.flush()
        os.fsync(outgoing.fileno())


def digest(path):
    with path.open('rb') as source:
        return hashlib.file_digest(source, 'sha256').hexdigest()


@contextmanager
def database(path):
    regular(path)
    with closing(sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True, timeout=5)) as db:
        yield db


def check_database(path, config=None):
    with database(path) as db:
        if db.execute('PRAGMA quick_check').fetchone() != ('ok',):
            raise ValueError('Backup database integrity check failed')
        if config:
            binding = dict(db.execute("SELECT key,value FROM mesh_meta WHERE key IN ('node_id','initial_owner')"))
            if binding != {'node_id': config['node_id'], 'initial_owner': config['supervisor_owner']}:
                raise ValueError('Database and node configuration identities do not match')


def snapshot(source, target):
    regular(source)
    parents(target.parent)
    # SQLite's backup API includes committed WAL data while the hub keeps running.
    write(target, b'')
    deadline = time.monotonic() + 30
    def progress(*_):
        if time.monotonic() > deadline:
            raise TimeoutError('Database snapshot exceeded 30 seconds')
    with database(source) as incoming, closing(sqlite3.connect(target)) as outgoing:
        incoming.backup(outgoing, pages=256, progress=progress, sleep=.02)
    # Keep each snapshot self-contained, with no WAL dependency.
    with closing(sqlite3.connect(target)) as db:
        db.execute('PRAGMA journal_mode=DELETE')
    with target.open('rb') as source_file:
        os.fsync(source_file.fileno())
    check_database(target)


def create(config_path, state_dir, destination):
    config_path, state_dir, destination = map(Path, (config_path, state_dir, destination))
    regular(config_path, private=True)
    config = read_config(config_path)
    if config.get('role', 'controller') != 'controller':
        raise ValueError('This command backs up trusted controllers only')
    check_database(state_dir / 'hub.sqlite', config)
    destination.mkdir(mode=0o700)  # Never overwrite an existing or incomplete backup.
    files = {'node.json', 'hub.sqlite'}
    write(destination / 'node.json', (json.dumps(config, indent=2) + '\n').encode())
    snapshot(state_dir / 'hub.sqlite', destination / 'hub.sqlite')
    for name in ('plans.json', 'wallpaper.jpg', 'archive/archive-v1.sqlite'):
        source = state_dir / name
        if source.exists() or source.is_symlink():
            (snapshot if name.endswith('.sqlite') else copy)(source, destination / name)
            files.add(name)
    if config.get('tls'):
        for setting, name in [('cert_file', 'tls/fullchain.pem'), ('key_file', 'tls/key.pem')]:
            source = Path(config['tls'][setting])
            regular(source, private=setting == 'key_file')
            copy(source, destination / name)
            files.add(name)
    manifest = {'version': 1, 'node_id': config['node_id'], 'role': 'controller', 'created': time.time(),
                'files': {name: {'size': (destination / name).stat().st_size, 'sha256': digest(destination / name)} for name in sorted(files)}}
    # Written last: a failed/interrupted creation cannot appear complete.
    write(destination / 'manifest.json', (json.dumps(manifest, indent=2) + '\n').encode())
    descriptor = os.open(destination, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    verify(destination)
    return manifest


def verify(bundle):
    bundle = Path(bundle)
    directory(bundle)
    info = regular(bundle / 'manifest.json', private=True)
    if info.st_size > 16000:
        raise ValueError('Backup manifest exceeds the size limit')
    manifest = json.loads((bundle / 'manifest.json').read_text())
    if not isinstance(manifest, dict):
        raise ValueError('Invalid controller backup manifest')
    files = manifest.get('files')
    if manifest.get('version') != 1 or manifest.get('role') != 'controller' or not isinstance(files, dict) or not REQUIRED <= files.keys() <= FILES:
        raise ValueError('Invalid controller backup manifest')
    for entry in bundle.rglob('*'):
        if entry.is_symlink():
            raise ValueError('Backup links are not accepted')
        if entry.is_dir():
            directory(entry)
        elif entry.relative_to(bundle).as_posix() not in {*files, 'manifest.json'}:
            raise ValueError('Backup contains an unlisted file')
    for name, expected in files.items():
        for parent in (bundle / name).parents:
            if parent == bundle:
                break
            directory(parent)
        info = regular(bundle / name, private=True)
        if not isinstance(expected, dict) or info.st_size != expected.get('size') or digest(bundle / name) != expected.get('sha256'):
            raise ValueError('Backup content does not match its manifest')
    config = read_config(bundle / 'node.json')
    if config.get('role', 'controller') != 'controller' or config['node_id'] != manifest.get('node_id'):
        raise ValueError('Backup node identity does not match its manifest')
    tls_files = {'tls/fullchain.pem', 'tls/key.pem'}
    if files.keys() & tls_files != (tls_files if config.get('tls') else set()):
        raise ValueError('Backup TLS files are incomplete')
    check_database(bundle / 'hub.sqlite', config)
    if 'archive/archive-v1.sqlite' in files:
        check_database(bundle / 'archive/archive-v1.sqlite')
    return manifest


def restore(bundle, destination, expected_node):
    """Stage into a new directory; do not edit live config, services, or ownership."""
    bundle, destination = Path(bundle), Path(destination).absolute()
    manifest = verify(bundle)
    if expected_node != manifest['node_id']:
        raise ValueError('Restore must retain the explicitly named node identity')
    destination.mkdir(mode=0o700)
    for name in manifest['files']:
        if name != 'node.json':
            copy(bundle / name, destination / ('config/' + name if name.startswith('tls/') else 'state/' + name))
    config = read_config(bundle / 'node.json')
    if config.get('tls'):
        for setting, name in [('cert_file', 'fullchain.pem'), ('key_file', 'key.pem')]:
            config['tls'][setting] = str(destination / 'config/tls' / name)
    write(destination / 'config/node.json', (json.dumps(config, indent=2) + '\n').encode())
    check_database(destination / 'state/hub.sqlite', config)
    return manifest


def main():
    os.umask(0o077)
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    save = commands.add_parser('create')
    save.add_argument('--node-config', type=Path, required=True)
    save.add_argument('--state-dir', type=Path, required=True)
    save.add_argument('destination', type=Path)
    check = commands.add_parser('verify')
    check.add_argument('bundle', type=Path)
    stage = commands.add_parser('restore')
    stage.add_argument('bundle', type=Path)
    stage.add_argument('destination', type=Path)
    stage.add_argument('--expected-node', required=True)
    args = parser.parse_args()
    try:
        if args.command == 'create':
            result = create(args.node_config, args.state_dir, args.destination)
        elif args.command == 'verify':
            result = verify(args.bundle)
        else:
            result = restore(args.bundle, args.destination, args.expected_node)
    except (OSError, ValueError, KeyError, sqlite3.Error) as exc:
        parser.exit(1, f'{type(exc).__name__}: backup operation did not complete. Live services were not changed.\n')
    print(json.dumps({'node_id': result['node_id'], 'files': len(result['files']),
                      'bytes': sum(item['size'] for item in result['files'].values()),
                      'verified': True, 'staged_only': args.command == 'restore'}))


if __name__ == '__main__':
    main()
