"""Browser handoff persistence and HTTP boundaries; no agent instructions."""
import json
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from bridge import BridgeError
from continuity import Continuity
from server import Hub, Handler, ThreadingHTTPServer


class ContinuityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        with patch.object(Hub, 'save_connection'):
            self.hub = Hub(self.temp.name)
        self.agent = {'id': 'fixture:1', 'title': 'Research', 'connection_label': 'Fixture'}
        self.snapshot = patch.object(self.hub, 'snapshot', return_value={'agents': [self.agent]})
        self.snapshot.start()
        self.now = 1000000
        self.store = self.hub.continuity = Continuity(self.hub, clock=lambda: self.now)
        self.value = {'device': 'browser-phone-0001', 'tab': 'phone-session-0001', 'label': 'Firefox · Phone',
            'sequence': 1, 'agent_id': self.agent['id'], 'mode': 'agent', 'draft': 'Compare the two checkpoints.',
            'bookmark': {'scope': 'agent', 'bottom': False, 'top': 2000, 'anchor': {'id': 'assistant:answer-1', 'offset': -100,
                'block': {'index': 3, 'digest': 'hash01', 'offset': -35, 'fraction': .25}}, 'seen': {'id': 'assistant:answer-1', 'digest': 'hash02'}}}

    def tearDown(self):
        self.snapshot.stop()
        self.hub.close()
        self.temp.cleanup()

    def test_checkpoint_survives_restart_without_command_chat_or_peer_history(self):
        with patch.object(self.hub, 'action') as action, patch.object(self.hub, 'message') as message:
            self.store.save({**self.value, 'unexpected': 'ignored', 'bookmark': {**self.value['bookmark'], 'transcript': 'not stored'}})
            restored = Continuity(self.hub, clock=lambda: self.now)
            item = restored.list('browser-desktop-0001')['items'][0]
            self.assertEqual(item['draft'], self.value['draft'])
            self.assertEqual(item['bookmark'], self.value['bookmark'])
            self.assertEqual(item['title'], 'Research')
            self.assertEqual(restored.list(self.value['device'])['items'], [])
            action.assert_not_called(); message.assert_not_called()
            self.assertEqual(self.hub.snapshot(), {'agents': [self.agent]})

    def test_old_and_duplicate_saves_cannot_replace_a_newer_or_cleared_draft(self):
        self.store.save(self.value)
        self.now += 1
        self.store.save({**self.value, 'sequence': 3, 'draft': ''})
        for sequence in (1, 2, 3):
            self.assertFalse(self.store.save({**self.value, 'sequence': sequence})['saved'])
        self.assertEqual(self.store.list('browser-desktop-0001')['items'][0]['draft'], '')

    def test_distinct_tabs_collapse_to_latest_device_without_changing_other_rows(self):
        self.store.save(self.value)
        self.now += 1
        self.store.save({**self.value, 'tab': 'phone-session-0002', 'draft': 'Latest phone tab'})
        self.now += 1
        self.store.save({**self.value, 'device': 'browser-tablet-0001', 'draft': 'Tablet draft'})
        items = self.store.list('browser-desktop-0001')['items']
        self.assertEqual([item['draft'] for item in items], ['Tablet draft', 'Latest phone tab'])
        self.assertEqual(len(self.store.list('browser-tablet-0001')['items']), 1)

    def test_retention_limits_and_closed_agents_preserve_readable_drafts(self):
        for index in range(40):
            self.now += 1
            self.store.save({**self.value, 'device': 'browser-device-%04d' % index})
        with self.hub.db() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM browser_checkpoints').fetchone()[0], 32)
        self.assertEqual(len(self.store.list('browser-desktop-0001')['items']), 8)
        self.hub.snapshot.return_value = {'agents': []}
        self.assertFalse(self.store.list('browser-desktop-0001')['items'][0]['available'])
        with self.assertRaises(BridgeError):
            self.store.save(self.value)
        self.now += self.store.retention + 1
        self.assertEqual(self.store.list('browser-desktop-0001')['items'], [])

    def test_validation_excludes_unknown_targets_invalid_positions_and_oversized_text(self):
        invalid = [{'device': 'bad'}, {'sequence': True}, {'sequence': 0}, {'sequence': float('inf')},
            {'label': 'two\nlines'}, {'agent_id': 'wrong-host:1'}, {'mode': 'raw'}, {'agent_id': ''},
            {'draft': 'x' * 12001}, {'draft': None}, {'bookmark': {'bottom': True, 'top': float('nan')}},
            {'bookmark': {'scope': 'raw', 'bottom': True, 'top': 0}},
            {'bookmark': {**self.value['bookmark'], 'anchor': {'id': 'message', 'offset': float('inf')}}}]
        for change in invalid:
            with self.subTest(change=change), self.assertRaises(BridgeError):
                self.store.save({**self.value, **change})
        self.store.save({**self.value, 'mode': 'supervisor', 'agent_id': '', 'draft': 'Global supervisor draft'})
        self.assertEqual(self.store.list('browser-desktop-0001')['items'][0]['title'], 'Supervisor')

    def test_http_host_origin_csrf_and_no_store(self):
        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        server.hub, server.allowed_hosts = self.hub, {'127.0.0.1'}
        thread = threading.Thread(target=server.serve_forever, daemon=True);thread.start()
        url = f'http://127.0.0.1:{server.server_port}/api/continuity'
        headers = {'Content-Type': 'application/json', 'X-Hub-Token': self.hub.csrf}
        try:
            for extra in ({'X-Hub-Token': 'wrong'}, {'Origin': 'https://untrusted.invalid'}, {'Host': 'wrong.invalid'}):
                with self.assertRaises(HTTPError) as error:
                    urlopen(Request(url, json.dumps(self.value).encode(), {**headers, **extra}), timeout=2)
                self.assertEqual(error.exception.code, 403);error.exception.close()
            # A maximum-length non-ASCII draft fits the endpoint's bounded upload.
            value = {**self.value, 'draft': '文' * 12000}
            with urlopen(Request(url, json.dumps(value, ensure_ascii=False).encode(), headers), timeout=2) as response:
                self.assertTrue(json.load(response)['saved'])
            with urlopen(url + '?device=browser-desktop-0001', timeout=2) as response:
                self.assertEqual(response.headers['Cache-Control'], 'no-store')
                self.assertEqual(json.load(response)['items'][0]['draft'], value['draft'])
        finally:
            server.shutdown();server.server_close();thread.join(2)


if __name__ == '__main__':
    unittest.main()
