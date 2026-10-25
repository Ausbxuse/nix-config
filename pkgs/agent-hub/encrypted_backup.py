"""Encrypted controller snapshots. Only the controller runs restic or holds its key.

The storage host receives a restic repository over SFTP, never a plaintext bundle.
Restore always stages into a new directory and never starts or promotes a hub.
"""
import argparse
from contextlib import contextmanager
from datetime import datetime
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import shlex
import shutil
import subprocess
import tempfile
import time
import zipfile

import controller_backup as bundle

TAG = 'agent-hub-controller'
SOURCE_FILES = ('encrypted_backup.py', 'controller_backup.py', 'federation.py', 'bridge.py', 'updates.py')


class BackupError(Exception):
    pass


def private_read(path, limit=65536):
    path = Path(path)
    bundle.directory(path.parent)
    info = bundle.regular(path, private=True)
    if info.st_size > limit:
        raise BackupError('Private backup file is too large')
    with os.fdopen(os.open(path, os.O_RDONLY | os.O_NOFOLLOW), 'rb') as source:
        data = source.read(limit + 1)
    if len(data) > limit:
        raise BackupError('Private backup file is too large')
    return data


def atomic_write(path, data):
    path = Path(path)
    bundle.parents(path.parent)
    temporary = path.with_name('.' + path.name + '-' + secrets.token_hex(8))
    try:
        bundle.write(temporary, data)
        os.replace(temporary, path)
        fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    finally:
        temporary.unlink(missing_ok=True)


def save_json(path, value):
    atomic_write(path, (json.dumps(value, indent=2) + '\n').encode())


def load_config(path):
    value = json.loads(private_read(path))
    if not isinstance(value, dict) or value.get('version') != 1:
        raise BackupError('Invalid backup configuration')
    if not re.fullmatch(r'[a-zA-Z0-9_-]{1,64}', value.get('node_id', '')):
        raise BackupError('Invalid controller identity')
    for name in ('node_config', 'state_dir', 'password_file'):
        if not isinstance(value.get(name), str) or not Path(value[name]).is_absolute():
            raise BackupError('Backup paths must be absolute')
    repository = value.get('repository', '')
    if not (repository.startswith('/') or re.fullmatch(r'sftp:[a-zA-Z0-9_.@-]+:/[a-zA-Z0-9_./-]+', repository)):
        raise BackupError('Use a local or SFTP backup repository')
    if repository.startswith('sftp:') and not value.get('ssh_config'):
        raise BackupError('SFTP requires its own SSH configuration')
    if 'repository_id' in value and not re.fullmatch(r'[0-9a-f]{64}', value['repository_id']):
        raise BackupError('Invalid repository identity')
    if not 32 <= len(private_read(value['password_file'], 4096).strip()) <= 512:
        raise BackupError('A separate random recovery key is required')
    return value


def root_for(config):
    root = Path(config['state_dir']) / 'controller-backup'
    bundle.parents(root)
    return root


@contextmanager
def locked(root):
    path = root / 'run.lock'
    fd = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise BackupError('Another backup operation is running')
        yield
    finally:
        os.close(fd)


def restic(config, *args, cwd=None, timeout=600):
    # An inherited RESTIC_PASSWORD/REPOSITORY/COMMAND must not redirect this job.
    environment = {key: value for key, value in os.environ.items()
                   if not key.startswith('RESTIC_') and key != 'SSH_AUTH_SOCK'}
    command = [os.environ.get('AGENT_HUB_RESTIC', 'restic'), '--json',
               '--repo', config['repository'], '--password-file', config['password_file'],
               '--cache-dir', str(root_for(config) / 'cache')]
    if config.get('ssh_config'):
        private_read(config['ssh_config'])
        # restic parses this argument list; no user-supplied shell fragments.
        ssh = shlex.join(['ssh', '-F', config['ssh_config']])
        command += ['-o', 'sftp.command=' + ssh + ' hub-backup-store -s sftp']
    try:
        result = subprocess.run([*command, *args], cwd=cwd, env=environment, stdin=subprocess.DEVNULL,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired):
        raise BackupError('Backup storage is unavailable or timed out') from None
    if result.returncode:
        # Tool output can contain paths or private file names. Do not put it in
        # the UI, journal, or exceptions; a failed upload is never marked saved.
        raise BackupError('Encrypted backup operation failed; previous verified copies are retained')
    return result.stdout


def repository_identity(config):
    identity = json.loads(restic(config, 'cat', 'config')).get('id')
    if not isinstance(identity, str) or not re.fullmatch(r'[0-9a-f]{64}', identity):
        raise BackupError('Invalid repository identity')
    return identity


def check_repository(config):
    if not config.get('repository_id') or repository_identity(config) != config['repository_id']:
        raise BackupError('Backup repository identity changed; refusing to replace or initialize it')


def ensure_space(config, snapshot_bytes):
    minimum = config.get('minimum_free_bytes', 512 * 1024**2)
    if not isinstance(minimum, int) or minimum < 0:
        raise BackupError('Invalid storage reserve')
    repository = config['repository']
    if repository.startswith('sftp:'):
        path = str(Path(repository.split(':', 2)[2]).parent)
        try:
            result = subprocess.run(['sftp', '-q', '-b', '-', '-F', config['ssh_config'], 'hub-backup-store'],
                                    input='df ' + path + '\n', text=True, capture_output=True, timeout=30,
                                    env={key:value for key,value in os.environ.items() if key != 'SSH_AUTH_SOCK'})
        except (OSError, subprocess.TimeoutExpired):
            raise BackupError('Could not check free space on the backup host') from None
        rows = [line.split() for line in result.stdout.splitlines()]
        rows = [row for row in rows if len(row) == 5 and all(value.isdecimal() for value in row[:4])
                and re.fullmatch(r'\d+%', row[4])]
        if result.returncode or len(rows) != 1:
            raise BackupError('Could not check free space on the backup host')
        available = int(rows[0][2]) * 1024  # OpenSSH sftp df uses 1024-byte units.
    else:
        available = shutil.disk_usage(Path(repository).parent).free
    # Reserve space for a worst-case new snapshot and compaction. This check
    # cannot reserve blocks against other workloads writing to the same disk.
    if available < minimum + 2 * snapshot_bytes + 16 * 1024**2:
        raise BackupError('Backup paused: storage is low on free space')


def status_update(config, **values):
    path = root_for(config) / 'status.json'
    try:
        previous = json.loads(private_read(path))
    except FileNotFoundError:
        previous = {}
    previous.update(values, configured=True, target=config.get('target_label', 'Backup storage'),
                    key_id=hashlib.sha256(private_read(config['password_file'])).hexdigest(),
                    repository_id=config.get('repository_id', ''))
    save_json(path, previous)
    return previous


def initialize(config_path):
    config = load_config(config_path)
    with locked(root_for(config)):
        node = bundle.read_config(config['node_config'])
        if node.get('role', 'controller') != 'controller' or node['node_id'] != config['node_id']:
            raise BackupError('Only the configured trusted controller may back itself up')
        if config.get('repository_id'):
            check_repository(config)
        else:
            # Explicit init only; regular backups never recreate an absent repo.
            restic(config, 'init', '--repository-version', '2')
            config['repository_id'] = repository_identity(config)
            save_json(config_path, config)
        status_update(config, phase='ready')
    return config


def restore_snapshot(config, snapshot, destination):
    if snapshot != 'latest' and not re.fullmatch(r'[0-9a-f]{64}', snapshot):
        raise BackupError('Use a full snapshot ID or latest')
    destination = Path(destination).absolute()
    if destination.exists() or destination.is_symlink():
        raise FileExistsError('Restore destination must be new')
    check_repository(config)
    entries = json.loads(restic(config, 'snapshots', '--host', config['node_id'], '--tag', TAG,
                               *([] if snapshot == 'latest' else [snapshot])))
    if snapshot == 'latest' and entries:
        entries = [max(entries, key=lambda item: datetime.fromisoformat(item['time']).timestamp())]
    if len(entries) != 1:
        raise BackupError('Expected exactly one controller snapshot')
    entry = entries[0]
    if (entry.get('hostname') != config['node_id'] or TAG not in entry.get('tags', [])
            or not re.fullmatch(r'[0-9a-f]{64}', entry.get('id', '')) or len(entry.get('paths', [])) != 1):
        raise BackupError('Snapshot does not belong to this controller')
    source_path = Path(entry['paths'][0])
    if not source_path.is_absolute() or '..' in source_path.parts or source_path.name != 'bundle':
        raise BackupError('Unexpected snapshot layout')
    with tempfile.TemporaryDirectory(prefix='verify-', dir=root_for(config)) as temporary:
        target = Path(temporary) / 'download'
        # Restore only the bundle tree, not its original /home/... ancestors.
        # The user service cannot map those ancestors' root ownership inside
        # its private namespace. They are not part of controller recovery.
        # --verify still authenticates all restored files before validation.
        restic(config, 'restore', entry['id'] + ':' + str(source_path), '--target', str(target), '--verify')
        manifest = bundle.restore(target, destination, config['node_id'])
    return {'snapshot': entry['id'], 'files': len(manifest['files']),
            'bytes': sum(item['size'] for item in manifest['files'].values()), 'staged_only': True}


def run_backup(config_path):
    config = load_config(config_path)
    root = root_for(config)
    with locked(root):
        stage = root / 'work'
        bundle.parents(stage)
        source = stage / 'bundle'
        try:
            status_update(config, phase='saving', last_attempt=time.time(), error='')
            check_repository(config)
            if source.exists():
                bundle.directory(source)
                shutil.rmtree(source)
            manifest = bundle.create(config['node_config'], config['state_dir'], source)
            if manifest['node_id'] != config['node_id']:
                raise BackupError('Snapshot controller identity changed')
            ensure_space(config, sum(item['size'] for item in manifest['files'].values()))
            output = restic(config, 'backup', '--host', config['node_id'], '--tag', TAG, str(source), cwd=stage)
            summaries = [value for line in output.splitlines() if isinstance(value := json.loads(line), dict)
                         and value.get('message_type') == 'summary']
            snapshot = summaries[-1].get('snapshot_id', '') if summaries else ''
            if not re.fullmatch(r'[0-9a-f]{64}', snapshot):
                raise BackupError('Storage did not confirm a complete snapshot')
            status_update(config, phase='checking')
            with tempfile.TemporaryDirectory(prefix='restore-test-', dir=root) as temporary:
                restored = restore_snapshot(config, snapshot, Path(temporary) / 'staged')
            now = time.time()
            status = status_update(config, phase='ready', last_success=now, last_verified=now,
                                   snapshot=snapshot, bytes=restored['bytes'], error='')
            # Retention runs only after a downloaded copy passes offline restore.
            # Keep hourly, daily, weekly and monthly recovery points in this repo.
            if now - status.get('last_pruned', 0) >= 86400:
                restic(config, 'forget', '--host', config['node_id'], '--tag', TAG, '--group-by', 'host,tags',
                       '--keep-hourly', '24', '--keep-daily', '14', '--keep-weekly', '8', '--keep-monthly', '6', '--prune')
                status_update(config, last_pruned=time.time())
            return restored
        except Exception as exc:
            status_update(config, phase='error', error=str(exc) if isinstance(exc, BackupError) else
                          'Backup needs attention. The last verified copy is retained.')
            raise
        finally:
            if source.exists() and not source.is_symlink():
                shutil.rmtree(source)


def ssh_config(host, user, key, known_hosts):
    if not re.fullmatch(r'[a-zA-Z0-9_.-]+', host) or not re.fullmatch(r'[a-zA-Z0-9_-]+', user):
        raise BackupError('Invalid storage host')
    # Paths are double quoted for OpenSSH configuration (not shell input).
    def quote(path):
        value = str(Path(path).absolute())
        if any(char in value for char in '\r\n"'):
            raise BackupError('Unsupported SSH file path')
        return '"' + value + '"'
    return ('Host hub-backup-store\n'
            f'  HostName {host}\n  User {user}\n  IdentityFile {quote(key)}\n'
            f'  UserKnownHostsFile {quote(known_hosts)}\n'
            '  GlobalKnownHostsFile /dev/null\n  StrictHostKeyChecking yes\n'
            '  BatchMode yes\n  IdentitiesOnly yes\n  IdentityAgent none\n  ForwardAgent no\n'
            '  PasswordAuthentication no\n  KbdInteractiveAuthentication no\n'
            '  ControlMaster no\n  ControlPath none\n  ConnectTimeout 10\n'
            '  ServerAliveInterval 15\n  ServerAliveCountMax 3\n').encode()


def make_kit(config_path):
    config = load_config(config_path)
    check_repository(config)
    root = root_for(config)
    metadata = {key: config[key] for key in ('version', 'node_id', 'repository', 'repository_id', 'target_label', 'ssh_host', 'ssh_user')}
    instructions = f'''Agent Hub recovery — {config['node_id']}

Keep this ZIP on a device or in a password manager separate from razy.
It contains the backup decryption key and an SFTP-only storage login.
Never send this ZIP to gpu-004 or put it in a source repository.

Storage: {config['repository']}
Repository identity: {config['repository_id']}
Encryption: restic repository format 2; key in restic-password.

On a trusted Linux replacement with Python 3, restic (0.16+) and OpenSSH:
1. Connect a trusted replacement to your tailnet, or retrieve the repository with
   your own storage login. The included SFTP login accepts tailnet addresses only.
   Tailnet enrollment is recovered separately.
2. Extract this ZIP in a private directory: umask 077 before running unzip.
3. Run from that directory:
   python3 encrypted_backup.py restore --kit . --destination /private/new-hub
   To use a downloaded repository, append --repository /path/to/restic-repository
   To select a previous backup, append --snapshot FULL_SNAPSHOT_ID
4. The command verifies the repository and stages config/ and state/ only.
   It never starts a service, changes ownership, or sends agent commands.
5. Stop or fence the old controller before activating the restored identity.
   Check node addresses, TLS paths/certificates, local paths and model login.
   Keep the original node and supervisor bootstrap IDs. Do not replay uncertain
   sends; inspect the agents before manually resending newer instructions.

Includes controller chat, review state, durable delivery receipts, plans settings,
TLS key/certificate, wallpaper and archive index when present. Agent processes,
full source logs, project files, model logins and Tailscale state need separate
backups. Snapshots can predate the most recent instructions. This is manual
recovery, not live takeover. The storage host can delete or withhold backups.
'''
    path = root / 'recovery-kit.zip'
    with tempfile.TemporaryDirectory(prefix='kit-', dir=root) as temporary:
        output = Path(temporary) / path.name
        with zipfile.ZipFile(output, 'x', compression=zipfile.ZIP_DEFLATED) as archive:
            content = {'README.txt': instructions.encode(), 'kit.json': json.dumps(metadata).encode(),
                       'restic-password': private_read(config['password_file']),
                       'sftp-key': private_read(config['ssh_key']),
                       'known_hosts': private_read(config['known_hosts'])}
            for name in SOURCE_FILES:
                content[name] = Path(__file__).with_name(name).read_bytes()
            for name, data in content.items():
                info = zipfile.ZipInfo(name)
                info.external_attr = 0o100600 << 16
                info.compress_type = zipfile.ZIP_DEFLATED
                archive.writestr(info, data)
        output.chmod(0o600)
        atomic_write(path, output.read_bytes())
    status_update(config, kit_ready=True)
    return path


def restore_kit(kit, destination, snapshot='latest', repository=None):
    kit = Path(kit).absolute()
    metadata = json.loads(private_read(kit / 'kit.json'))
    bundle.directory(kit)
    config = dict(metadata, node_config=str(kit / 'unused-node.json'), state_dir=str(kit),
                  password_file=str(kit / 'restic-password'))
    if repository is not None:
        # Offline restore uses a repository copied by a separate trusted login.
        if not Path(repository).is_absolute():
            raise BackupError('Offline repository path must be absolute')
        config['repository'] = repository
    elif config['repository'].startswith('sftp:'):
        private_read(kit / 'sftp-key')
        private_read(kit / 'known_hosts')
        config['ssh_config'] = str(kit / 'recovery-ssh-config')
        atomic_write(config['ssh_config'], ssh_config(metadata['ssh_host'], metadata['ssh_user'], kit / 'sftp-key', kit / 'known_hosts'))
    temporary_config = kit / 'recovery-config.json'
    save_json(temporary_config, config)
    config = load_config(temporary_config)
    return restore_snapshot(config, snapshot, destination)


def main():
    os.umask(0o077)
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    for name in ('init', 'run', 'kit'):
        command = commands.add_parser(name)
        command.add_argument('--config', required=True, type=Path)
    restore = commands.add_parser('restore')
    restore.add_argument('--kit', required=True, type=Path)
    restore.add_argument('--destination', required=True, type=Path)
    restore.add_argument('--snapshot', default='latest')
    restore.add_argument('--repository')
    args = parser.parse_args()
    try:
        if args.command == 'init':
            initialize(args.config)
            result = {'initialized': True}
        elif args.command == 'run':
            result = run_backup(args.config)
        elif args.command == 'kit':
            make_kit(args.config)
            result = {'kit_ready': True}
        else:
            result = restore_kit(args.kit, args.destination, args.snapshot, args.repository)
    except Exception:
        parser.exit(1, 'Backup operation did not complete. Live services were not changed; previous verified copies are retained.\n')
    print(json.dumps(result))


if __name__ == '__main__':
    main()
