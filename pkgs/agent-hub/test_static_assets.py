import hashlib
from http.client import HTTPConnection
from pathlib import Path
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from server import Handler, ThreadingHTTPServer
from static_assets import StaticAssets


class StaticAssetTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        (self.root / 'assets').mkdir()
        (self.root / 'vendor/chunks').mkdir(parents=True)
        (self.root / 'index.html').write_text('<script src="/app.js"></script><link href="/style.css"><a href="/api/state">state</a>')
        (self.root / 'app.js').write_text('window.ready=true;')
        (self.root / 'style.css').write_text('@font-face{src:url("/assets/font.woff2")}')
        (self.root / 'assets/font.woff2').write_bytes(b'font')
        (self.root / 'vendor/chunks/lazy.js').write_text('export const ready=true;')
        self.assets = StaticAssets(self.root)

    def tearDown(self):
        self.temp.cleanup()

    def test_html_and_css_references_are_versioned_but_api_is_not(self):
        content, _, _, policy = self.assets.get('/')
        self.assertIn(self.assets.url('/app.js').encode(), content)
        self.assertIn(b'href="/api/state"', content)
        self.assertEqual(policy, 'no-cache')
        content, _, _, policy = self.assets.get(self.assets.url('/style.css'))
        self.assertIn(self.assets.url('/assets/font.woff2').encode(), content)
        self.assertIn('immutable', policy)
        self.assertIsNotNone(self.assets.get(self.assets.prefix + 'vendor/chunks/lazy.js'))

    def test_new_deployment_changes_urls_and_old_version_never_serves_new_code(self):
        old = self.assets.url('/app.js')
        (self.root / 'app.js').write_text('window.ready="new";')
        updated = StaticAssets(self.root)
        self.assertNotEqual(old, updated.url('/app.js'))
        self.assertIsNone(updated.get(old))
        self.assertEqual(updated.get(updated.url('/app.js'))[0], b'window.ready="new";')

    def test_no_traversal_or_symlink_assets(self):
        (self.root / 'escape').symlink_to('/etc/passwd')
        assets = StaticAssets(self.root)
        for path in ('/escape', '/../app.js', '/assets/../app.js', '/static/wrong/app.js'):
            self.assertIsNone(assets.get(path))

    def test_http_conditional_get_has_no_body_and_versions_remain_cacheable(self):
        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        server.hub = SimpleNamespace(client_allowed=lambda _: True, state_dir=self.root)
        server.allowed_hosts = {'127.0.0.1'}
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with patch('server.assets_for', return_value=self.assets):
                def get(path, headers=None):
                    connection = HTTPConnection('127.0.0.1', server.server_port, timeout=2)
                    try:
                        connection.request('GET', path, headers=headers or {})
                        response = connection.getresponse()
                        return response.status, dict(response.headers), response.read()
                    finally:
                        connection.close()
                status, headers, body = get('/')
                self.assertEqual(status, 200)
                self.assertIn("img-src 'self' blob:", headers['Content-Security-Policy'])
                self.assertIn("script-src 'self';", headers['Content-Security-Policy'])
                self.assertEqual(headers['ETag'], '"'+hashlib.sha256(body).hexdigest()+'"')
                status, _, body = get('/', {'If-None-Match': headers['ETag']})
                self.assertEqual((status, body), (304, b''))
                status, headers, _ = get(self.assets.url('/app.js'))
                self.assertEqual(status, 200)
                self.assertIn('immutable', headers['Cache-Control'])
                (self.root / 'wallpaper.jpg').write_bytes(b'private image')
                _, headers, _ = get('/wallpaper.jpg')
                self.assertEqual(headers['Cache-Control'], 'private, no-store')
                self.assertNotIn('ETag', headers)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(2)
