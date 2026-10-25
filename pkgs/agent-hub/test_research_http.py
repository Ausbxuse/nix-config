"""Research reads and edits over real HTTP with synthetic, isolated host archives."""
import json
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from unittest.mock import patch

import bridge
from test_archive_http import ArchiveNodes


class ResearchHTTPTests(unittest.TestCase):
    def setUp(self):
        self.nodes = ArchiveNodes()
        self.hub = self.nodes.hubs['controller']
        self.worker = self.nodes.hubs['worker']
        self.nodes.seed('controller', ['Robot learning experiment: the fixed-seed baseline reduced variance. '
                                     'This means the next comparison must use the same seeds and reset conditions.'])
        self.nodes.seed('worker', ['The robot learning ablation compared two normalization methods with identical seeds. '
                                 'The result remains preliminary because only one environment has completed. '
                                 'For example, a seed-matched pair avoids confounding the normalization choice.'])

    def tearDown(self):
        self.nodes.close()

    def post(self, host, value, token=True):
        headers = {'Content-Type': 'application/json'}
        if token:
            headers['X-Hub-Token'] = self.nodes.hubs[host].csrf
        request = Request(self.nodes.urls[host] + '/api/research', json.dumps(value).encode(), headers)
        with urlopen(request, timeout=5) as response:
            return json.load(response)

    def test_controller_collects_local_and_worker_without_reverse_access_or_agent_commands(self):
        with patch.object(self.worker.mesh, 'request', side_effect=AssertionError('Reverse access')), \
             patch.object(bridge, 'send', side_effect=AssertionError('Agent command')), \
             patch.object(self.hub.research, 'summarize'):
            self.hub.research.collect()
            result = self.nodes.get('controller', '/api/research')
            self.assertEqual(len(result['items']), 2)
            self.assertEqual({item['sources'][0]['host'] for item in result['items']}, {'controller', 'worker'})
            self.assertTrue(all('_evidence' not in item for item in result['items']))
            self.assertFalse(self.nodes.get('worker', '/api/research')['available'])
            for item in result['items']:
                source = item['sources'][0]
                page = self.nodes.get('controller', '/api/archive/conversation', host=source['host'], session=source['session'],
                                      generation=source['generation'], message=source['message_id'])
                self.assertTrue(any(message['id'] == source['message_id'] for message in page['messages']))

    def test_edits_hide_restore_and_csrf_are_persistent_and_local(self):
        with patch.object(self.hub.research, 'summarize'):
            self.hub.research.collect()
        item = self.nodes.get('controller', '/api/research')['items'][0]
        with self.assertRaises(HTTPError) as error:
            self.post('controller', {'action': 'hide', 'id': item['id']}, token=False)
        self.assertEqual(error.exception.code, 403)
        error.exception.close()
        changed = self.post('controller', {'action': 'edit', 'id': item['id'], 'changes': {'title': 'A matched comparison', 'notes': 'Repeat across environments.'}})
        self.assertEqual(changed['item']['sources'], item['sources'])
        self.post('controller', {'action': 'hide', 'id': item['id']})
        self.assertFalse(any(row['id'] == item['id'] for row in self.nodes.get('controller', '/api/research')['items']))
        hidden = self.nodes.get('controller', '/api/research', hidden=1)['items']
        self.assertEqual([row['id'] for row in hidden], [item['id']])
        self.assertEqual(hidden[0]['title'], 'A matched comparison')
        self.post('controller', {'action': 'restore', 'id': item['id']})
        self.assertEqual(len(self.nodes.get('controller', '/api/research')['items']), 2)
        with self.assertRaises(HTTPError) as error:
            self.post('worker', {'action': 'hide', 'id': item['id']})
        error.exception.close()
        with self.worker.db() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM research_entries').fetchone()[0], 0)


if __name__ == '__main__':
    unittest.main()
