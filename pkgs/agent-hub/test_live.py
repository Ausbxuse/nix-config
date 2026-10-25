from concurrent.futures import ThreadPoolExecutor
from http.client import HTTPConnection
import json
from pathlib import Path
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest

from live import LiveFeed, SharedReads
from server import Handler, ThreadingHTTPServer
from updates import apply_update


class SharedReadTests(unittest.TestCase):
    def test_simultaneous_clients_share_one_load_without_sharing_mutable_results(self):
        reads = SharedReads()
        started, release = threading.Event(), threading.Event()
        def load():
            started.set()
            release.wait(2)
            return {'messages': [{'text': 'Result'}]}
        with ThreadPoolExecutor(max_workers=6) as pool:
            pending = [pool.submit(reads.get, 'a', load) for _ in range(6)]
            self.assertTrue(started.wait(1))
            release.set()
            values = [result.result(2) for result in pending]
        self.assertEqual(reads.loads, 1)
        values[0]['messages'][0]['text'] = 'UI mutation'
        self.assertEqual(values[1]['messages'][0]['text'], 'Result')
        self.assertEqual(reads.get('a', load)['messages'][0]['text'], 'Result')

    def test_refresh_invalidates_inflight_cache_and_failures_are_retryable(self):
        reads = SharedReads()
        started, release = threading.Event(), threading.Event()
        def slow():
            started.set(); release.wait(2); return {'value':'old'}
        with ThreadPoolExecutor(max_workers=1) as pool:
            first = pool.submit(reads.get, 'a', slow)
            self.assertTrue(started.wait(1))
            reads.invalidate()
            self.assertEqual(reads.get('a', lambda: {'value':'new'}), {'value':'new'})
            release.set();first.result(2)
        self.assertEqual(reads.get('a', slow), {'value':'new'})
        def fail(): raise ValueError('temporary')
        with self.assertRaises(ValueError): reads.get('failed', fail)
        self.assertEqual(reads.get('failed', lambda: {'ok':True}), {'ok':True})


class LiveFeedTests(unittest.TestCase):
    def setUp(self):
        self.stop = threading.Event()
        self.feed = LiveFeed(lambda: {'agents': []}, lambda *_: {'messages': []}, self.stop)

    def tearDown(self):
        self.stop.set();self.feed.close()
        if self.feed.thread:self.feed.thread.join(2)

    def test_reconnect_replays_deltas_and_expired_cursor_gets_complete_current_state(self):
        topic = ('state',)
        first = {'agents': [{'id':'a','preview':'x'*20000,'state':'running'}], 'server_time':1}
        self.feed.publish(topic, first)
        event = next(self.feed.stream({topic}))
        self.assertEqual(event['event'], 'reset')
        base = event['data'][0]['packet']
        second = {'agents': [{**first['agents'][0], 'state':'done'}], 'server_time':2}
        self.feed.publish(topic, second)
        update = next(self.feed.stream({topic}, event['id']))
        self.assertEqual(update['event'], 'update')
        self.assertEqual(apply_update(base, update['data']['packet']), second)
        self.assertEqual(next(self.feed.stream({topic}, 'old-process:1'))['event'], 'reset')
        self.feed.events.clear()
        reset = next(self.feed.stream({topic}, event['id']))
        self.assertEqual(reset['event'], 'reset')
        self.assertEqual(reset['data'][0]['packet']['value'], second)

    def test_scopes_do_not_replay_other_agent_content(self):
        a, b = ('preview','a',True), ('preview','b',True)
        self.feed.publish(a, {'text':'Agent A'})
        self.feed.publish(b, {'text':'Agent B'})
        reset = next(self.feed.stream({a}))
        self.assertEqual([item['topic'] for item in reset['data']], [list(a)])
        self.assertNotIn('Agent B', json.dumps(reset))

    def test_terminal_subscription_retains_its_mode_and_independent_deltas(self):
        calls = []
        self.feed.preview = lambda *args: calls.append(args) or {'text': 'same', **({'terminal': {'ansi': '\x1b[31msame'}} if len(args) == 3 and args[2] else {})}
        with self.feed.subscribe(previews=[['a', True], ['a', True, True]]) as topics:
            self.assertEqual(topics, {('state',), ('preview', 'a', True), ('preview', 'a', True, True)})
            for topic in topics:
                self.feed.refresh(topic)
            self.assertIn(('a', True, True), calls)
            raw = next(self.feed.stream({('preview', 'a', True, True)}))['data']
            reader = next(self.feed.stream({('preview', 'a', True)}))['data']
            self.assertIn('terminal', raw[0]['packet']['value'])
            self.assertNotIn('terminal', reader[0]['packet']['value'])

    def test_timestamp_ticks_are_coalesced_but_heartbeat_refreshes_freshness(self):
        self.feed.heartbeat = .01
        self.feed.publish(('state',), {'agents':[], 'server_time':1})
        sequence = self.feed.sequence
        self.feed.publish(('state',), {'agents':[], 'server_time':2})
        self.assertEqual(self.feed.sequence, sequence)
        time.sleep(.02)
        self.feed.publish(('state',), {'agents':[], 'server_time':3})
        self.assertEqual(self.feed.sequence, sequence+1)

    def test_http_stream_initial_and_reconnect_deliver_real_events(self):
        self.feed.publish(('state',), {'agents': [{'id':'a','state':'running','preview':'x'*2000}]})
        with tempfile.TemporaryDirectory() as directory:
            server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
            server.hub = SimpleNamespace(client_allowed=lambda _:True, state_dir=Path(directory), live=self.feed)
            server.allowed_hosts = {'127.0.0.1'}
            thread = threading.Thread(target=server.serve_forever, daemon=True);thread.start()
            connection = HTTPConnection('127.0.0.1', server.server_port, timeout=3)
            try:
                connection.request('GET', '/api/live')
                response = connection.getresponse()
                self.assertEqual(response.status, 200)
                self.assertEqual(response.headers['Content-Type'], 'text/event-stream')
                def frame():
                    result = {}
                    while True:
                        line = response.fp.readline().decode().strip()
                        if not line:
                            if 'event' in result:return result
                            continue
                        key, value = line.split(':',1);result[key]=value.strip()
                first = frame()
                self.assertEqual(first['event'], 'reset')
                self.assertIn('state', first['data'])
                self.feed.publish(('state',), {'agents': [{'id':'a','state':'done','preview':'x'*2000}]})
                changed = frame()
                self.assertEqual(changed['event'], 'update')
                self.assertNotEqual(changed['id'], first['id'])
            finally:
                self.stop.set();self.feed.close()
                connection.close();server.shutdown();server.server_close();thread.join(2)
