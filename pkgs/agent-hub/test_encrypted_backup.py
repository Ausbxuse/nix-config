"""Actual restic encryption, independent kit recovery and browser isolation."""
import json
import os
from pathlib import Path
import secrets
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen
import zipfile

import controller_backup as bundle
import encrypted_backup as encrypted
from backup_status import BackupStatus
from bridge import BridgeError
from server import Handler, ThreadingHTTPServer
import test_controller_backup as fixtures


@unittest.skipUnless(shutil.which(os.environ.get('AGENT_HUB_RESTIC', 'restic')), 'restic is required')
class EncryptedBackupTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.ControllerBackupTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.tearDown)
        self.root = self.fixture.root
        self.password = self.root / 'restic-password'
        bundle.write(self.password, (secrets.token_urlsafe(48) + '\n').encode())
        self.config = dict(version=1, node_id='trusted', node_config=str(self.fixture.config_path),
                           state_dir=str(self.fixture.state), repository=str(self.root / 'remote-repository'),
                           password_file=str(self.password), target_label='Synthetic storage',
                           ssh_host='127.0.0.1', ssh_user='fixture', ssh_key=str(self.root / 'storage-key'),
                           known_hosts=str(self.root / 'known_hosts'))
        bundle.write(self.root / 'storage-key', b'fixture SFTP key, not a real login')
        bundle.write(self.root / 'known_hosts', b'fixture host key, not a real host')
        self.config_path = self.root / 'backup.json'
        encrypted.save_json(self.config_path, self.config)

    def initialize(self):
        self.config = encrypted.initialize(self.config_path)

    def test_encrypted_upload_independent_kit_restore_and_retained_receipts(self):
        self.initialize()
        first = encrypted.run_backup(self.config_path)
        self.assertTrue(first['staged_only'])
        self.assertFalse((encrypted.root_for(self.config) / 'work/bundle').exists())
        for path in Path(self.config['repository']).rglob('*'):
            if path.is_file():
                self.assertNotIn(b'Synthetic private conversation', path.read_bytes())
                self.assertNotIn(self.password.read_bytes().strip(), path.read_bytes())
        self.fixture.hub.message('user', 'Second synthetic turn')
        second = encrypted.run_backup(self.config_path)
        self.assertNotEqual(first['snapshot'], second['snapshot'])
        kit_path = encrypted.make_kit(self.config_path)
        kit = self.root / 'separate-trusted-device'
        kit.mkdir(mode=0o700)
        with zipfile.ZipFile(kit_path) as archive:
            self.assertNotIn('node.json', archive.namelist())
            self.assertNotIn('hub.sqlite', archive.namelist())
            archive.extractall(kit)
        for path in kit.iterdir():
            path.chmod(0o600)
        self.password.unlink()  # Recovery must work without the original key file.
        destination = self.root / 'recovered'
        result = encrypted.restore_kit(kit, destination)
        self.assertEqual(result['snapshot'], second['snapshot'])
        hub = self.fixture.new_hub(destination / 'state', bundle.read_config(destination / 'config/node.json'))
        self.assertEqual(hub.deliveries.read(self.fixture.request['command_id'], self.fixture.request)['status'], 'complete')
        with self.assertRaises(FileExistsError):
            encrypted.restore_kit(kit, destination)
        previous = self.root / 'previous'
        encrypted.restore_kit(kit, previous, first['snapshot'])
        self.assertNotEqual((previous / 'state/hub.sqlite').read_bytes(), (destination / 'state/hub.sqlite').read_bytes())

    def test_failed_upload_or_verification_cannot_claim_success_or_prune(self):
        self.initialize()
        encrypted.run_backup(self.config_path)
        status_path = encrypted.root_for(self.config) / 'status.json'
        previous = json.loads(status_path.read_text())
        real = encrypted.restic
        for failure in ('backup', 'restore'):
            def fail(config, *args, **kwargs):
                if args[0] == 'forget':
                    self.fail('Pruned before a verified restore')
                if args[0] == failure:
                    raise encrypted.BackupError('Synthetic interrupted transfer')
                return real(config, *args, **kwargs)
            with patch.object(encrypted, 'restic', side_effect=fail), self.assertRaises(encrypted.BackupError):
                encrypted.run_backup(self.config_path)
            current = json.loads(status_path.read_text())
            self.assertEqual(current['snapshot'], previous['snapshot'])
            self.assertEqual(current['last_verified'], previous['last_verified'])
            self.assertEqual(current['phase'], 'error')
            self.assertFalse((encrypted.root_for(self.config) / 'work/bundle').exists())

    def test_restore_in_user_namespace_needs_no_ancestor_ownership(self):
        # PrivateTmp in a systemd user service uses a user namespace. Its own
        # UID is mapped, but the original /home or /tmp root ownership is not.
        unshare = os.environ.get('AGENT_HUB_TEST_UNSHARE') or shutil.which('unshare')
        if not unshare:
            self.skipTest('unshare is required for the service namespace regression')
        probe = subprocess.run([unshare, '--user', '--map-current-user', sys.executable, '-c', 'pass'],
                               capture_output=True, timeout=5)
        if probe.returncode:
            self.skipTest('User namespaces are unavailable')
        self.initialize()
        saved = encrypted.run_backup(self.config_path)
        target = self.root / 'namespace-restore'
        program = ('import sys,json; from pathlib import Path; '
                   'import encrypted_backup as b; '
                   'c=b.load_config(Path(sys.argv[1])); '
                   'print(json.dumps(b.restore_snapshot(c,"latest",Path(sys.argv[2]))))')
        restored = subprocess.run([unshare, '--user', '--map-current-user', sys.executable, '-c',
                                   program, str(self.config_path), str(target)],
                                  cwd=Path(encrypted.__file__).parent, capture_output=True, timeout=30)
        self.assertEqual(restored.returncode, 0, restored.stderr.decode())
        self.assertEqual(json.loads(restored.stdout)['snapshot'], saved['snapshot'])
        self.assertTrue((target / 'state/hub.sqlite').is_file())
        self.assertTrue((target / 'config/node.json').is_file())

    def test_wrong_key_replaced_repository_and_worker_cannot_initialize(self):
        self.initialize()
        result = encrypted.run_backup(self.config_path)
        original = self.password.read_bytes()
        self.password.write_bytes(b'wrong-password-' * 6)
        with self.assertRaises(encrypted.BackupError):
            encrypted.restore_snapshot(self.config, result['snapshot'], self.root / 'wrong-key')
        self.password.write_bytes(original)
        replaced = {**self.config, 'repository_id': '0' * 64}
        with self.assertRaises(encrypted.BackupError):
            encrypted.restore_snapshot(replaced, 'latest', self.root / 'wrong-repo')
        self.fixture.config['role'] = 'worker'
        self.fixture.config['peers'] = [{'id':'worker', 'role':'controller', 'token':'fixture-' * 5}]
        self.fixture.config_path.write_text(json.dumps(self.fixture.config))
        with self.assertRaises(encrypted.BackupError):
            encrypted.initialize(self.config_path)
        self.assertFalse((self.root / 'wrong-key').exists())
        self.assertFalse((self.root / 'wrong-repo').exists())

    def test_uninitialized_job_never_creates_a_repository_and_lock_prevents_overlap(self):
        with self.assertRaises(encrypted.BackupError):
            encrypted.run_backup(self.config_path)
        self.assertFalse(Path(self.config['repository']).exists())
        with encrypted.locked(encrypted.root_for(self.config)), self.assertRaises(encrypted.BackupError):
            encrypted.run_backup(self.config_path)
        self.password.chmod(0o644)
        with self.assertRaises(ValueError):
            encrypted.load_config(self.config_path)

    def test_low_space_aborts_before_any_upload(self):
        self.initialize()
        with patch.object(encrypted.shutil, 'disk_usage', return_value=shutil._ntuple_diskusage(100, 99, 1)), \
                self.assertRaisesRegex(encrypted.BackupError, 'storage is low'):
            encrypted.run_backup(self.config_path)
        self.assertEqual(json.loads(encrypted.restic(self.config, 'snapshots')), [])
        status = json.loads((encrypted.root_for(self.config) / 'status.json').read_text())
        self.assertNotIn('last_verified', status)


class BackupHTTPTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.ControllerBackupTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.tearDown)
        self.hub = self.fixture.hub
        self.root = self.hub.state_dir / 'controller-backup'
        self.root.mkdir(mode=0o700)
        encrypted.save_json(self.root / 'status.json', dict(configured=True, kit_ready=True, key_id='fixture-key-id',
                            last_verified=100, secret='must never be returned', repository_id='private repository identity'))
        bundle.write(self.root / 'recovery-kit.zip', b'fixture private recovery download')
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.server.hub = self.hub
        self.server.allowed_hosts = {'127.0.0.1'}
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.url = f'http://127.0.0.1:{self.server.server_port}'

    def request(self, path, body=None, **headers):
        return urlopen(Request(self.url + path, None if body is None else json.dumps(body).encode(), headers), timeout=3)

    def test_key_requires_explicit_csrf_post_and_never_enters_snapshots(self):
        with self.request('/api/backup') as response:
            body = json.load(response)
            self.assertNotIn('secret', body)
            self.assertNotIn('repository_id', body)
            self.assertFalse(body['key_saved'])
        for path in ('/api/backup/key', '/api/backup/key-saved'):
            with self.assertRaises(HTTPError) as error:
                self.request(path, {})
            self.assertEqual(error.exception.code, 403)
            error.exception.close()
        with self.assertRaises(HTTPError) as error:
            self.request('/api/backup/key')
        self.assertEqual(error.exception.code, 404)
        error.exception.close()
        with self.request('/api/backup/key', {}, **{'X-Hub-Token':self.hub.csrf}) as response:
            self.assertEqual(response.read(), b'fixture private recovery download')
            self.assertIn('no-store', response.headers['Cache-Control'])
            self.assertIn('attachment', response.headers['Content-Disposition'])
        self.assertFalse(self.hub.backups.read()['key_saved'], 'A download is not proof of independent key storage')
        with self.request('/api/backup/key-saved', {'key_id':'fixture-key-id','saved':True}, **{'X-Hub-Token':self.hub.csrf}) as response:
            self.assertTrue(json.load(response)['key_saved'])
        self.assertNotIn('fixture private recovery', json.dumps(self.hub.snapshot()))
        self.assertNotIn('fixture private recovery', json.dumps(self.hub.mesh.export({})))

    def test_worker_peer_origin_host_and_client_boundaries(self):
        for headers in ({'X-Hub-Peer':'worker'}, {'Authorization':'Bearer peer-token'}, {'Origin':'https://evil.invalid'}, {'Host':'evil.invalid'}):
            with self.assertRaises(HTTPError) as error:
                self.request('/api/backup/key', {}, **{'X-Hub-Token':self.hub.csrf, **headers})
            self.assertEqual(error.exception.code, 403)
            error.exception.close()
        with patch.object(self.hub, 'client_allowed', return_value=False), self.assertRaises(HTTPError) as error:
            self.request('/api/backup/key', {}, **{'X-Hub-Token':self.hub.csrf})
        self.assertEqual(error.exception.code, 403)
        error.exception.close()
        with patch.object(self.hub.mesh, 'is_worker', True):
            for path, body in [('/api/backup', None), ('/api/backup/key', {})]:
                with self.assertRaises(HTTPError) as error:
                    self.request(path, body, **{'X-Hub-Token':self.hub.csrf})
                self.assertEqual(error.exception.code, 409)
                error.exception.close()
        with self.assertRaises(BridgeError):
            self.hub.backups.confirm({'key_id':'old-key','saved':True})
        (self.root / 'recovery-kit.zip').unlink()
        (self.root / 'recovery-kit.zip').symlink_to(self.fixture.config_path)
        with self.assertRaises(BridgeError):
            self.hub.backups.kit()


if __name__ == '__main__':
    unittest.main()
