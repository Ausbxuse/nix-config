"""Real TLS handshakes and renewal verification; no user agents are contacted."""

import json
import os
from pathlib import Path
import socket
import ssl
import subprocess
import tempfile
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from server import Handler
from tls import TLSHTTPServer, context_for, install_certificate, validate_config


class TLSTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.root = Path(cls.temp.name)
        cls.openssl('req', '-x509', '-newkey', 'ec', '-pkeyopt', 'ec_paramgen_curve:P-256',
                    '-nodes', '-keyout', 'ca.key', '-out', 'ca.pem', '-days', '3',
                    '-subj', '/CN=Agent Hub test CA', '-addext', 'basicConstraints=critical,CA:TRUE',
                    '-addext', 'keyUsage=critical,keyCertSign,cRLSign')
        for name, hostname, days in [('first', 'localhost', '2'), ('second', 'localhost', '3'),
                                     ('wrong', 'other.invalid', '3')]:
            if name == 'first':
                cls.openssl('genpkey', '-algorithm', 'EC', '-pkeyopt', 'ec_paramgen_curve:P-256', '-out', 'key.pem')
                (cls.root / 'key.pem').chmod(0o600)
            cls.openssl('req', '-new', '-key', 'key.pem', '-out', name + '.csr', '-subj', '/CN=' + hostname)
            (cls.root / 'extensions').write_text('subjectAltName=DNS:' + hostname + '\nextendedKeyUsage=serverAuth\nbasicConstraints=CA:FALSE\n')
            cls.openssl('x509', '-req', '-in', name + '.csr', '-CA', 'ca.pem', '-CAkey', 'ca.key',
                        '-CAcreateserial', '-out', name + '.pem', '-days', days, '-extfile', 'extensions')
        cls.openssl('genpkey', '-algorithm', 'EC', '-pkeyopt', 'ec_paramgen_curve:P-256', '-out', 'other.key')
        (cls.root / 'other.key').chmod(0o600)

    @classmethod
    def openssl(cls, *args):
        subprocess.run(['openssl', *args], cwd=cls.root, capture_output=True, check=True)

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def setUp(self):
        self.cert = self.root / 'live.pem'
        self.cert.write_bytes((self.root / 'first.pem').read_bytes())
        self.settings = {'server_name': 'localhost', 'cert_file': str(self.cert),
                         'key_file': str(self.root / 'key.pem')}
        self.client_context = ssl.create_default_context(cafile=str(self.root / 'ca.pem'))
        self.env = patch.dict(os.environ, {'SSL_CERT_FILE': str(self.root / 'ca.pem')})
        self.env.start()
        self.addCleanup(self.env.stop)

    def server(self, allowed=True):
        server = TLSHTTPServer(('127.0.0.1', 0), Handler, self.settings)
        self.sources = []
        def check(address):
            self.sources.append(address)
            return allowed
        server.hub = SimpleNamespace(client_allowed=check, snapshot=lambda: {'ok': True})
        server.allowed_hosts = {'localhost'}
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        return server, f'https://localhost:{server.server_port}'

    def test_tls_source_checks_ignore_forwarded_headers(self):
        server, url = self.server(allowed=False)
        with self.assertRaises(HTTPError) as error:
            urlopen(Request(url + '/api/state', headers={'X-Forwarded-For': '100.64.0.1'}),
                    context=self.client_context, timeout=2)
        self.assertEqual(error.exception.code, 403)
        error.exception.close()
        self.assertEqual(self.sources, ['127.0.0.1'])

    def test_stalled_handshake_does_not_block_other_clients(self):
        server, url = self.server()
        with socket.create_connection(server.server_address, timeout=2):
            with urlopen(url + '/api/state', context=self.client_context, timeout=2) as response:
                self.assertEqual(json.load(response), {'ok': True})

    def test_renewal_is_verified_and_reloaded_without_restart(self):
        server, url = self.server()
        def peer_cert():
            with socket.create_connection(server.server_address, timeout=2) as raw:
                with self.client_context.wrap_socket(raw, server_hostname='localhost') as secured:
                    return secured.getpeercert(binary_form=True)
        before = peer_cert()
        content = (self.root / 'second.pem').read_bytes()
        self.assertTrue(install_certificate(self.settings, content))
        self.assertFalse(install_certificate(self.settings, content))
        self.assertNotEqual(before, peer_cert())
        self.assertEqual(self.cert.stat().st_mode & 0o777, 0o600)
        with self.assertRaisesRegex(ValueError, 'older'):
            install_certificate(self.settings, (self.root / 'first.pem').read_bytes())

    def test_reject_wrong_name_key_and_untrusted_issuer(self):
        before = self.cert.read_bytes()
        with self.assertRaisesRegex(ValueError, 'verification'):
            install_certificate(self.settings, (self.root / 'wrong.pem').read_bytes())
        with self.assertRaises(ssl.SSLError):
            install_certificate({**self.settings, 'key_file': str(self.root / 'other.key')},
                                (self.root / 'second.pem').read_bytes())
        with patch.dict(os.environ, {'SSL_CERT_FILE': '/dev/null', 'SSL_CERT_DIR': '/nonexistent'}):
            with self.assertRaisesRegex(ValueError, 'verification'):
                install_certificate(self.settings, (self.root / 'second.pem').read_bytes())
        self.assertEqual(self.cert.read_bytes(), before)

    def test_bad_replacement_keeps_current_tls_context(self):
        server, url = self.server()
        self.cert.write_text('partial or invalid replacement')
        with urlopen(url + '/api/state', context=self.client_context, timeout=2) as response:
            self.assertEqual(json.load(response), {'ok': True})

    def test_require_private_key_permissions_and_private_listener(self):
        config = {'tailnet_bind': '100.64.0.1', 'tls': self.settings}
        validate_config(config)
        for patch_value in ({'port': 443}, {'key_file': '/nix/store/exposed/key.pem'},
                            {'cert_file': 'relative.pem'}, {'renewal_url': 'http://example.com/cert.pem'}):
            with self.assertRaises(ValueError):
                validate_config({**config, 'tls': {**self.settings, **patch_value}})
        with self.assertRaises(ValueError):
            validate_config({'tls': self.settings})
        key = Path(self.settings['key_file'])
        try:
            key.chmod(0o644)
            with self.assertRaisesRegex(ValueError, 'mode 600'):
                context_for(self.settings)
        finally:
            key.chmod(0o600)


if __name__ == '__main__':
    unittest.main()
