"""Synthetic private snapshots only; no live credentials, services or agents."""
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

import controller_backup as backup
from bridge import BridgeError
from federation import Mesh, read_config
from server import Hub


class ControllerBackupTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.hubs = []
        self.patches = [patch.object(Hub, 'save_connection'), patch.object(Mesh, 'start')]
        for item in self.patches:
            item.start()
        self.config = {'node_id': 'trusted', 'role': 'controller', 'supervisor_owner': 'worker',
                       'tmux_socket': '/nonexistent-backup-test',
                       'peers': [{'id': 'worker', 'role': 'worker', 'url': 'http://127.0.0.1:1', 'token': 'synthetic-' * 5}],
                       'tls': {'cert_file': str(self.root / 'certificate.pem'), 'key_file': str(self.root / 'key.pem')}}
        backup.write(self.root / 'certificate.pem', b'fixture certificate')
        backup.write(self.root / 'key.pem', b'fixture private key')
        self.config_path = self.root / 'node.json'
        backup.write(self.config_path, json.dumps(self.config).encode())
        self.state = self.root / 'state'
        self.hub = self.new_hub(self.state, self.config)
        # Match the original bootstrap retained by existing installations.
        self.hub.mesh.ingest([dict(origin='worker', seq=1, clock=1, kind='leadership',
                                  payload={'owner': 'trusted', 'epoch': 2}, created=1)])
        self.hub.message('user', 'Synthetic private conversation')
        self.hub.mesh.append('mark', {'agent_id': 'worker:fixture', 'patch': {'acknowledged': True}})
        backup.write(self.state / 'plans.json', b'{"sources":[]}')
        self.request = dict(type='send', agent_id='worker:fixture', message='fixture instruction', command_id='web-backup-fixture-001')
        self.hub.deliveries.execute_once(self.request['command_id'], self.request, lambda: {'ok': True})
        self.bundle = self.root / 'backup'

    def new_hub(self, state, config):
        hub = Hub(state, node_config=config)
        self.hubs.append(hub)
        return hub

    def tearDown(self):
        for hub in self.hubs:
            hub.close()
        for item in reversed(self.patches):
            item.stop()
        self.temp.cleanup()

    def test_wal_snapshot_and_staged_restore_preserve_private_state_and_receipts(self):
        with self.hub.db() as writer:
            writer.execute("INSERT INTO watch_settings VALUES('fixture','committed in WAL')")
            writer.commit()
            original_config = self.config_path.read_bytes()
            manifest = backup.create(self.config_path, self.state, self.bundle)
            self.assertEqual(self.config_path.read_bytes(), original_config)
            destination = self.root / 'staged'
            backup.restore(self.bundle, destination, 'trusted')
        self.assertEqual(manifest['node_id'], 'trusted')
        config = read_config(destination / 'config/node.json')
        self.assertEqual(config['supervisor_owner'], 'worker', 'keep bootstrap identity; ownership lives in the journal')
        self.assertEqual(Path(config['tls']['key_file']).read_bytes(), b'fixture private key')
        self.assertNotEqual(config['tls']['key_file'], self.config['tls']['key_file'])
        restored = self.new_hub(destination / 'state', config)
        self.assertEqual(restored.mesh.authority(), {'owner': 'trusted', 'epoch': 2})
        self.assertEqual(restored.mesh.views(), self.hub.mesh.views())
        with restored.db() as db:
            self.assertEqual(db.execute("SELECT value FROM watch_settings WHERE key='fixture'").fetchone()[0], 'committed in WAL')
        self.assertEqual(restored.deliveries.read(self.request['command_id'], self.request)['status'], 'complete')
        restored.deliveries.execute_once(self.request['command_id'], self.request, lambda: self.fail('duplicate execution'))
        for root in (self.bundle, destination):
            for path in (root, *root.rglob('*')):
                self.assertEqual(path.stat().st_mode & 0o077, 0, str(path))
        self.assertFalse((destination / 'state/hub.sqlite-wal').exists())

    def test_incomplete_execution_remains_uncertain_after_backup_and_restore(self):
        pending = {**self.request, 'command_id': 'web-backup-pending-001'}
        with self.hub.db() as db:
            db.execute("INSERT INTO command_receipts VALUES(?,?,'started',NULL)",
                       (pending['command_id'], self.hub.deliveries.digest(pending['command_id'], pending)))
        backup.create(self.config_path, self.state, self.bundle)
        backup.restore(self.bundle, self.root / 'staged', 'trusted')
        hub = self.new_hub(self.root / 'staged/state', read_config(self.root / 'staged/config/node.json'))
        self.assertEqual(hub.deliveries.read(pending['command_id'], pending)['status'], 'uncertain')
        with self.assertRaises(BridgeError):
            hub.deliveries.execute_once(pending['command_id'], pending, lambda: self.fail('replayed incomplete command'))

    def test_restore_rejects_wrong_identity_existing_destination_and_tampering(self):
        backup.create(self.config_path, self.state, self.bundle)
        destination = self.root / 'staged'
        with self.assertRaises(ValueError):
            backup.restore(self.bundle, destination, 'worker')
        self.assertFalse(destination.exists())
        with self.assertRaises(FileExistsError):
            backup.restore(self.bundle, self.state, 'trusted')
        (self.bundle / 'plans.json').write_text('changed')
        with self.assertRaises(ValueError):
            backup.restore(self.bundle, destination, 'trusted')
        self.assertFalse(destination.exists())

    def test_manifest_paths_permissions_symlinks_and_worker_promotion_are_rejected(self):
        backup.create(self.config_path, self.state, self.bundle)
        path = self.bundle / 'manifest.json'
        manifest = json.loads(path.read_text())
        path.write_text(json.dumps({**manifest, 'files': {**manifest['files'], '../escape': {'size': 0, 'sha256': ''}}}))
        with self.assertRaises(ValueError):
            backup.verify(self.bundle)
        path.write_text(json.dumps(manifest))
        private = self.bundle / 'tls/key.pem'
        private.chmod(0o644)
        with self.assertRaises(ValueError):
            backup.verify(self.bundle)
        private.chmod(0o600)
        private.unlink()
        private.symlink_to(self.root / 'key.pem')
        with self.assertRaises(ValueError):
            backup.verify(self.bundle)
        self.config['role'] = 'worker'
        self.config['peers'] = [{'id': 'worker', 'role': 'controller', 'token': 'fixture-' * 5}]
        self.config_path.write_text(json.dumps(self.config))
        with self.assertRaises(ValueError):
            backup.create(self.config_path, self.state, self.root / 'wrong-role')
        self.assertFalse((self.root / 'wrong-role').exists())

    def test_mismatched_database_and_partial_bundle_are_not_recoverable(self):
        self.config['supervisor_owner'] = 'trusted'
        self.config_path.write_text(json.dumps(self.config))
        with self.assertRaises(ValueError):
            backup.create(self.config_path, self.state, self.bundle)
        self.assertFalse(self.bundle.exists())
        self.bundle.mkdir(mode=0o700)
        with self.assertRaises(FileNotFoundError):
            backup.verify(self.bundle)


if __name__ == '__main__':
    unittest.main()
