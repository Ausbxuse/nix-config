"""Controller-local recovery status and explicit browser key export.

No paths, keys or backup configuration enter snapshots, peer journals or models.
"""
import json
import time

from bridge import BridgeError
from encrypted_backup import BackupError, private_read, save_json


class BackupStatus:
    def __init__(self, hub):
        self.hub = hub
        self.root = hub.state_dir / 'controller-backup'

    def controller(self):
        if not self.hub.mesh or self.hub.mesh.is_worker:
            raise BridgeError('Controller recovery is not available on this host')

    def read(self):
        self.controller()
        try:
            value = json.loads(private_read(self.root / 'status.json'))
        except FileNotFoundError:
            return {'configured': False}
        except (OSError, ValueError, BackupError):
            raise BridgeError('Backup status is unavailable') from None
        allowed = ('configured', 'phase', 'target', 'last_attempt', 'last_success', 'last_verified', 'snapshot', 'error', 'kit_ready', 'key_id')
        status = {key: value[key] for key in allowed if key in value}
        if status.get('phase') in ('saving', 'checking') and time.time() - status.get('last_attempt', 0) > 1800:
            status.update(phase='error', error='The last backup was interrupted. Check the backup service.')
        try:
            saved = json.loads(private_read(self.root / 'key-saved.json'))
            status['key_saved'] = saved.get('key_id') == value.get('key_id') and bool(saved.get('saved'))
        except (OSError, ValueError, BackupError):
            status['key_saved'] = False
        return status

    def kit(self):
        self.controller()
        status = self.read()
        if not status.get('kit_ready'):
            raise BridgeError('Recovery download is not ready')
        try:
            return private_read(self.root / 'recovery-kit.zip', 1024 * 1024)
        except (OSError, ValueError, BackupError):
            raise BridgeError('Recovery download is unavailable') from None

    def confirm(self, value):
        status = self.read()
        if not status.get('kit_ready') or value.get('key_id') != status.get('key_id') or value.get('saved') is not True:
            raise BridgeError('Download the current recovery key and save it off this host first')
        save_json(self.root / 'key-saved.json', {'key_id': status['key_id'], 'saved': time.time()})
        return self.read()
