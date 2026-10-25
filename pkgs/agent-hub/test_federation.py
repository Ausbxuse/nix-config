"""Two real HTTP nodes; terminal operations are stubbed, never user agents."""

import json
import copy
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import bridge
from federation import Mesh, read_config
from server import Hub, Handler, ThreadingHTTPServer
from test_hub import row


class FederationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.servers = {key: ThreadingHTTPServer(('127.0.0.1', 0), Handler) for key in ('a', 'b')}
        self.urls = {key: f'http://127.0.0.1:{server.server_port}' for key, server in self.servers.items()}
        self.hubs = {}
        self.patches = [patch.object(Mesh, 'start'), patch.object(bridge, 'list_agents', side_effect=lambda conn: bridge.parse_panes(row(), conn)),
                        patch.object(bridge, 'capture', return_value='› Ready for an instruction')]
        for item in self.patches:
            item.start()
        for key in ('a', 'b'):
            other = 'b' if key == 'a' else 'a'
            config = {'node_id': key, 'label': key, 'tmux_socket': '/tmp/unused-test.sock',
                      'public_url': self.urls[key], 'supervisor_owner': 'b',
                      'peers': [{'id': other, 'url': self.urls[other], 'token': 'test-secret-' * 5, 'ssh_target': 'fixture@' + other}]}
            hub = Hub(self.root / key, poll_interval=.05, node_config=config)
            server = self.servers[key]
            server.hub, server.allowed_hosts = hub, {'127.0.0.1'}
            self.hubs[key] = hub
            threading.Thread(target=server.serve_forever, daemon=True).start()
        self.a, self.b = self.hubs['a'], self.hubs['b']
        self.wait(lambda: all(hub.snapshot()['agents'] for hub in self.hubs.values()))
        self.sync()

    def tearDown(self):
        for hub in self.hubs.values():
            hub.close()
        # Let the observer exit before removing its database directory.
        for hub in self.hubs.values():
            for thread in hub.workers.values():
                thread.join(timeout=2)
        for server in self.servers.values():
            server.shutdown()
            server.server_close()
        for item in reversed(self.patches):
            item.stop()
        self.temp.cleanup()

    def wait(self, predicate):
        for _ in range(100):
            if predicate():
                return
            time.sleep(.02)
        self.fail('Condition did not become true')

    def sync(self):
        self.a.mesh.sync('b')
        self.b.mesh.sync('a')
        self.a.mesh.sync('b')

    def test_shared_agents_history_marks_and_duplicate_sync(self):
        self.assertEqual({a['connection_id'] for a in self.a.snapshot()['agents']}, {'a', 'b'})
        self.a.message('user', 'first message')
        self.b.message('assistant', 'second message')
        target = self.a.snapshot()['agents'][0]
        self.a.action({'type': 'focus', 'agent_id': target['id']})
        self.sync()
        before = self.a.mesh.cursors()
        self.sync()
        self.assertEqual(before, self.a.mesh.cursors())
        self.assertEqual(self.a.snapshot()['messages'], self.b.snapshot()['messages'])
        self.assertTrue(next(a for a in self.b.snapshot()['agents'] if a['id'] == target['id'])['mark']['focused'])

    def test_peer_auth_is_separate_from_browser_csrf(self):
        for headers in ({'X-Hub-Token': self.a.csrf}, {'X-Hub-Peer': 'b', 'Authorization': 'Bearer wrong'},
                        {'X-Hub-Peer': 'b', 'Authorization': 'Bearer ' + 'test-secret-' * 5, 'Origin': self.urls['a']}):
            with self.assertRaises(HTTPError) as error:
                urlopen(Request(self.urls['a'] + '/peer/sync', b'{}', headers), timeout=2)
            self.assertEqual(error.exception.code, 403)
            error.exception.close()

    def test_follower_routes_to_owner_then_back_to_target_once(self):
        target = next(a for a in self.a.snapshot()['agents'] if a['connection_id'] == 'a')
        value = {'type': 'send', 'agent_id': target['id'], 'message': 'fixture task', 'command_id': 'repeatable_command_1'}
        with patch.object(bridge, 'send') as send:
            first = self.a.action(value)
            second = self.a.action(value)
            self.assertEqual(first, second)
            self.assertEqual(send.call_count, 1)
            with self.assertRaisesRegex(bridge.BridgeError, 'different contents'):
                self.a.action({**value, 'message': 'changed task'})
        with patch.object(bridge, 'send') as send:
            with self.assertRaisesRegex(bridge.BridgeError, 'Stale supervisor'):
                self.a.peer_request('b', 'execute', {'authority': {'owner': 'b', 'epoch': 0}, 'action': value})
            send.assert_not_called()

    def test_uncertain_command_is_never_automatically_repeated(self):
        value = {'type': 'launch', 'connection_id': 'a'}
        calls = []
        def interrupted():
            calls.append(True)
            raise RuntimeError('reply lost after possible side effect')
        with self.assertRaises(RuntimeError):
            self.a.mesh.execute_once('uncertain_command_1', value, interrupted)
        with self.assertRaisesRegex(bridge.BridgeError, 'uncertain or failed'):
            self.a.mesh.execute_once('uncertain_command_1', value, interrupted)
        self.assertEqual(len(calls), 1)

    def test_supervisor_forwarding_and_job_history(self):
        started = threading.Event()
        finish = threading.Event()
        def answer(job_id, query, selected, allow_actions, clarification=None):
            started.set()
            finish.wait(3)
            self.b.message('assistant', 'Both hosts are visible.')
            with self.b.lock:
                self.b.jobs[job_id] = {'status': 'complete', 'reply': 'Both hosts are visible.'}
                self.b.supervisor_busy = False
        with patch.object(self.b, 'supervise', side_effect=answer):
            job = self.a.start_supervisor('What needs attention?')['job_id']
            self.assertTrue(job.startswith('b/'))
            self.assertTrue(started.wait(1))
            with self.assertRaisesRegex(bridge.BridgeError, 'finishing'):
                self.a.start_supervisor('Another request')
            with self.assertRaisesRegex(bridge.BridgeError, 'finish'):
                self.b.mesh.handoff('a')
            finish.set()
            self.wait(lambda: self.a.job(job)['status'] == 'complete')
            self.assertEqual(self.a.snapshot()['messages'][-1]['text'], 'Both hosts are visible.')

    def test_clarification_follows_the_active_supervisor(self):
        candidates = [dict(agent_id='a:lap', title='LAP', host='a', reason='Evaluation'),
                      dict(agent_id='b:lap', title='LAP', host='b', reason='Checkpoints')]
        offered = self.b.offer_choices(candidates, 'Switch to LAP', 'a:task')
        with patch.object(self.b, 'supervise') as supervise:
            result = self.a.start_supervisor('the second one', 'a:task', clarification_id=offered['clarification_id'])
            self.wait(lambda: supervise.called)
        self.assertTrue(result['job_id'].startswith('b/'))
        self.assertEqual(supervise.call_args.args[-1]['candidates'], candidates)
        self.assertNotIn(offered['clarification_id'], self.b.clarifications)

    def test_handoff_revokes_old_owner_and_survives_lost_acknowledgement(self):
        with patch.object(self.a, 'local_supervisor_status', return_value={'local_available': True, 'available': True, 'busy': False}):
            original = self.b.mesh.request
            def lose_ack(peer, method, value=None):
                if method == 'refresh':
                    raise bridge.BridgeError('Connection lost')
                return original(peer, method, value)
            with patch.object(self.b.mesh, 'request', side_effect=lose_ack):
                result = self.b.mesh.handoff('a')
            self.assertTrue(result['ok'])
            self.assertEqual(self.b.mesh.authority(), {'owner': 'a', 'epoch': 2})
            self.assertEqual(self.a.mesh.authority(), {'owner': 'b', 'epoch': 1})
            self.a.mesh.sync('b')
            self.assertEqual(self.a.mesh.authority(), {'owner': 'a', 'epoch': 2})
            with self.assertRaisesRegex(bridge.BridgeError, 'Stale supervisor'):
                self.a.peer_request('b', 'execute', {'authority': {'owner': 'b', 'epoch': 1}, 'action': {}})

    def test_offline_peer_retains_state_and_recovers_missing_events(self):
        with patch.object(self.a.mesh, 'request', side_effect=bridge.BridgeError('fixture offline')):
            with self.assertRaises(bridge.BridgeError):
                self.a.mesh.sync('b')
            snapshot = self.a.snapshot()
            remote = next(c for c in snapshot['connections'] if c['id'] == 'b')
            self.assertEqual(remote['status'], 'offline')
            self.assertGreater(remote['agent_count'], 0)
            self.assertFalse(snapshot['supervisor']['available'])
            self.a.message('user', 'Recorded while disconnected')
        self.b.message('assistant', 'Remote progress while disconnected')
        self.sync()
        self.assertEqual(self.a.snapshot()['messages'], self.b.snapshot()['messages'])
        self.assertTrue(all(c['status'] == 'online' for c in self.a.snapshot()['connections']))

    def test_journal_gaps_and_forgery_are_rejected_atomically(self):
        self.a.message('user', 'new event')
        events = self.a.mesh.export(self.b.mesh.cursors())
        self.assertTrue(events)
        forged = dict(events[0], origin='b', seq=999)
        before = self.b.mesh.cursors()
        with self.assertRaises(bridge.BridgeError):
            self.b.mesh.ingest([events[0], forged])
        self.assertEqual(before, self.b.mesh.cursors())
        with self.assertRaisesRegex(bridge.BridgeError, 'gap'):
            self.b.mesh.ingest([dict(events[0], seq=999)])

    def test_private_config_permissions_and_peer_identity(self):
        p = self.root / 'node.json'
        p.write_text(json.dumps(self.a.mesh.config))
        p.chmod(0o644)
        with self.assertRaisesRegex(ValueError, 'chmod 600'):
            read_config(p)
        p.chmod(0o600)
        self.assertEqual(read_config(p)['node_id'], 'a')
        config = self.a.mesh.config.copy()
        config['peers'] = [{**config['peers'][0], 'url': 'http://user:password@host/'}]
        p.write_text(json.dumps(config))
        with self.assertRaises(ValueError):
            read_config(p)
        config = {**self.a.mesh.config, 'tailnet_bind': '0.0.0.0'}
        p.write_text(json.dumps(config))
        with self.assertRaisesRegex(ValueError, 'Tailscale'):
            read_config(p)

    def make_directional(self):
        self.b.mesh.append('leadership', {'owner': 'a', 'epoch': 2})
        self.a.mesh.sync('b')
        self.a.mesh.peers['b']['role'] = 'worker'
        self.b.mesh.is_worker = True
        self.b.mesh.config['role'] = 'worker'
        self.b.mesh.peers['a']['role'] = 'controller'
        self.b.mesh.peers['a'].pop('url')

    def test_worker_is_polled_without_receiving_controller_history(self):
        self.make_directional()
        self.a.message('user', 'Private controller conversation')
        self.b.message('user', 'Local worker conversation')
        before = self.b.mesh.cursors()
        self.a.mesh.sync('b')
        self.assertEqual(self.b.mesh.cursors(), before)
        self.assertEqual({a['connection_id'] for a in self.a.snapshot()['agents']}, {'a', 'b'})
        self.assertEqual({a['connection_id'] for a in self.b.snapshot()['agents']}, {'b'})
        self.assertEqual([n['id'] for n in self.b.snapshot()['nodes']], ['b'])
        self.assertNotIn('Private controller conversation', json.dumps(self.b.snapshot()))
        self.assertNotIn('Local worker conversation', json.dumps(self.a.snapshot()))
        self.assertFalse(next(n for n in self.a.snapshot()['nodes'] if n['id'] == 'b')['can_supervise'])
        self.assertEqual(self.b.snapshot()['supervisor']['scope'], 'local')

    def test_worker_video_ranges_and_history_stay_directional(self):
        self.make_directional()
        project = self.root / "video-project"
        project.mkdir()
        (project / "clip.mp4").write_bytes(b"0123456789")
        target = self.b.snapshot()["agents"][0]
        local = {"kind": "local"}
        with patch.object(self.b, "lookup", return_value=(local, {**target, "cwd": str(project)})):
            video = self.a.mesh.request("b", "worker/media", {"agent_id": target["id"], "path": "clip.mp4", "start": 2, "end": 5})
            import base64
            self.assertEqual(base64.b64decode(video["data"]), b"2345")
            with self.assertRaises(bridge.BridgeError):
                self.a.mesh.request("b", "worker/media", {"agent_id": target["id"], "path": "../outside.mp4"})
            with patch.object(self.b, "local_preview", return_value={"messages": []}) as preview:
                self.a.preview(target["id"], history=True)
                self.assertTrue(preview.call_args.args[2])
        with self.assertRaises(bridge.BridgeError):
            self.b.mesh.request("a", "media", {"agent_id": target["id"], "path": "clip.mp4"})

    def test_worker_generates_its_own_thumbnail_without_reverse_access(self):
        self.make_directional()
        self.a.mesh.sync('b')
        target = self.b.snapshot()['agents'][0]
        expected = {'data':'fixture-small-image','etag':'fixture-version'}
        with patch.object(self.b.thumbnails, 'render', return_value=expected) as render, patch.object(self.b.mesh, 'request') as outbound:
            self.assertEqual(self.a.thumbnail(target['id'], 'plots/trace.png'), expected)
            self.assertEqual(render.call_args.args[0]['id'], target['id'])
            self.assertEqual(render.call_args.args[1], 'plots/trace.png')
            outbound.assert_not_called()
        with self.assertRaises(bridge.BridgeError):
            self.b.mesh.request('a', 'thumbnail', {'agent_id':target['id'], 'path':'trace.png'})

    def test_notification_preview_bypasses_controller_and_worker_caches_without_reverse_access(self):
        self.make_directional()
        target = self.b.snapshot()['agents'][0]
        old = {'observation':{'state':'done', 'completion_confirmed':True}}
        current = {'observation':{'state':'running', 'activity_confirmed':True}}
        for hub in (self.a, self.b):
            hub.reads.ttl = 60
        self.a.reads.get(('preview', target['id'], True, False), lambda:old)
        self.b.reads.get(('local-preview', target['id'], True), lambda:old)
        with patch.object(self.b, '_local_preview', return_value=current) as read, \
                patch.object(self.a.mesh, 'read_request', side_effect=AssertionError('Notification must not join a delta read')), \
                patch.object(self.b.mesh, 'request') as outbound:
            self.assertEqual(self.a.preview(target['id'], history=True), old)
            self.assertEqual(self.a.preview(target['id'], history=True, fresh=True), current)
            self.assertTrue(read.call_args.kwargs['fresh'])
            self.assertEqual(read.call_args.args[1]['id'], target['id'])
            outbound.assert_not_called()
        with self.assertRaises(bridge.BridgeError):
            self.a.mesh.request('b', 'worker/thumbnail', {'agent_id':'a:private','path':'trace.png'})

    def test_worker_compact_snapshots_and_previews_recover_after_cache_reset(self):
        self.make_directional()
        self.a.message('user', 'Controller-private text')
        first = self.a.mesh.request('b', 'worker/snapshot', {'updates': True})
        second = self.a.mesh.request('b', 'worker/snapshot', {'updates': True, 'since': first['snapshot_update']['version']})
        self.assertIn('changes', second['snapshot_update'])
        self.assertNotIn('Controller-private text', json.dumps(second))
        target = self.b.snapshot()['agents'][0]
        before = {'messages': [{'id':'one','text':'🎉' + 'x'*20000}], 'text':'Preview'}
        with patch.object(self.b, 'local_preview', return_value=before):
            result = self.a.mesh.read_request('b', 'worker/preview', {'agent_id':target['id'], 'history':True})
        self.assertEqual(result, before)
        after = {**before, 'messages': [before['messages'][0], {'id':'two', 'text':'New result'}]}
        with patch.object(self.b, 'local_preview', return_value=after), patch.object(self.a.mesh, 'request', wraps=self.a.mesh.request) as request:
            result = self.a.mesh.read_request('b', 'worker/preview', {'agent_id':target['id'], 'history':True})
            self.assertEqual(result, after)
            self.assertTrue(request.call_args.args[2]['since'])
            self.b.peer_updates.entries.clear()
            self.assertEqual(self.a.mesh.read_request('b', 'worker/preview', {'agent_id':target['id'], 'history':True}), after)
        with self.assertRaises(bridge.BridgeError):
            self.b.mesh.read_request('a', 'worker/snapshot')

    def test_compact_read_accepts_legacy_peer_without_retrying_actions(self):
        legacy = {'messages': [{'id':'one', 'text':'Old peer'}]}
        with patch.object(self.a.mesh, 'request', return_value=legacy) as request:
            self.assertEqual(self.a.mesh.read_request('b', 'preview', {'agent_id':'example'}), legacy)
            self.assertEqual(request.call_count, 1)
            with self.assertRaises(bridge.BridgeError):
                self.a.mesh.read_request('b', 'worker/execute', {'action': {'type':'send'}})
            self.assertEqual(request.call_count, 1)

    def test_worker_terminal_colours_are_opt_in_and_have_separate_delta_bases(self):
        self.make_directional()
        target = self.b.snapshot()['agents'][0]
        def preview(connection, agent, history=False, terminal=False):
            return {'text': 'same', **({'terminal': {'ansi': '\x1b[31msame', 'theme': 'dark'}} if terminal else {})}
        with patch.object(self.b, 'local_preview', side_effect=preview), patch.object(self.b.mesh, 'request') as outbound:
            value = {'agent_id': target['id'], 'history': True}
            plain = self.a.mesh.read_request('b', 'worker/preview', value)
            raw = self.a.mesh.read_request('b', 'worker/preview', {**value, 'terminal': True})
            self.assertNotIn('terminal', plain)
            self.assertEqual(raw['terminal']['ansi'], '\x1b[31msame')
            self.assertEqual(self.a.mesh.read_request('b', 'worker/preview', value), plain)
            self.assertEqual(self.a.mesh.read_request('b', 'worker/preview', {**value, 'terminal': True}), raw)
            outbound.assert_not_called()

    def test_usage_flows_from_worker_to_controller_only(self):
        self.make_directional()
        for hub, percent in ((self.a, 61), (self.b, 73)):
            hub.usage.value = {'provider': 'codex', 'status': 'available', 'observed_at': time.time(),
                               'checked_at': time.time(), 'stale': False,
                               'weekly': [{'id': 'codex:primary', 'label': 'Codex',
                                           'used_percent': percent, 'resets_at': time.time() + 86400}]}
            hub.claude_usage.value = {'provider': 'claude', 'status': 'available', 'observed_at': time.time(),
                                      'checked_at': time.time(), 'stale': False,
                                      'limits': [{'id': 'seven_day', 'label': 'Claude', 'window_minutes': 10080,
                                                  'used_percent': percent - 10, 'resets_at': time.time() + 86400}]}
        with patch.object(self.b.mesh, 'request') as outbound:
            self.a.mesh.sync('b')
            reports = {c['id']: c['usage']['weekly'][0]['used_percent'] for c in self.a.snapshot()['connections']}
            self.assertEqual(reports, {'a': 61, 'b': 73})
            claude_reports = {c['id']: c['claude_usage']['limits'][0]['used_percent'] for c in self.a.snapshot()['connections']}
            self.assertEqual(claude_reports, {'a': 51, 'b': 63})
            local = self.b.snapshot()['connections']
            self.assertEqual(len(local), 1)
            self.assertEqual(local[0]['usage']['weekly'][0]['used_percent'], 73)
            self.assertEqual(local[0]['claude_usage']['limits'][0]['used_percent'], 63)
            outbound.assert_not_called()

    def test_worker_has_no_outbound_hub_requests(self):
        self.make_directional()
        with patch.object(self.b.mesh.opener, 'open') as network:
            for method in ('sync', 'preview', 'supervisor', 'action', 'handoff'):
                with self.assertRaisesRegex(bridge.BridgeError, 'cannot contact'):
                    self.b.mesh.request('a', method)
            with self.assertRaisesRegex(bridge.BridgeError, 'cannot contact'):
                self.b.mesh.sync('a')
            network.assert_not_called()
        with patch('federation.threading.Thread') as thread:
            self.b.mesh.start()
            thread.assert_not_called()

    def test_worker_credential_cannot_access_any_controller_peer_endpoint(self):
        self.make_directional()
        headers = {'X-Hub-Peer': 'b', 'Authorization': 'Bearer ' + self.a.mesh.peers['b']['token']}
        for method in ('sync', 'preview', 'supervisor', 'action', 'execute', 'handoff', 'receipt', 'thumbnail', 'continuity', 'switch-info', 'worker/switch-info'):
            with self.assertRaises(HTTPError) as error:
                urlopen(Request(self.urls['a'] + '/peer/' + method, b'{}', headers), timeout=2)
            self.assertEqual(error.exception.code, 403)
            error.exception.close()

    def test_browser_checkpoints_are_private_to_the_controller(self):
        self.make_directional()
        value = {'device': 'browser-phone-0001', 'tab': 'phone-session-0001', 'sequence': 1,
                 'label': 'Firefox Phone', 'agent_id': '', 'mode': 'supervisor', 'draft': 'private unsent draft', 'bookmark': None}
        self.a.continuity.save(value)
        self.a.mesh.sync('b')
        self.assertNotIn('private unsent draft', json.dumps(self.a.mesh.local_export()))
        self.assertNotIn('private unsent draft', json.dumps(self.a.mesh.export({})))
        self.assertNotIn('private unsent draft', json.dumps(self.b.snapshot()))
        with self.assertRaisesRegex(bridge.BridgeError, 'controller'):
            self.b.continuity.save(value)
        with self.assertRaisesRegex(bridge.BridgeError, 'controller'):
            self.b.continuity.list('browser-desktop-0001')

    def test_refresh_scans_worker_without_reverse_access_and_requires_csrf(self):
        self.make_directional()
        self.a.mesh.sync('b')
        requested = time.monotonic()
        # HTTP refresh uses the existing browser authorization boundary.
        with self.assertRaises(HTTPError) as error:
            urlopen(Request(self.urls['a'] + '/api/refresh', b'{}'), timeout=5)
        self.assertEqual(error.exception.code, 403)
        error.exception.close()
        headers = {'Content-Type': 'application/json', 'X-Hub-Token': self.a.csrf}
        with patch.object(self.b.mesh, 'request', side_effect=AssertionError('Worker attempted reverse access')):
            with urlopen(Request(self.urls['a'] + '/api/refresh', b'{}', headers), timeout=5) as response:
                snapshot = json.load(response)
            self.assertEqual(snapshot['refresh_errors'], [])
            self.assertRegex(snapshot['app_version'], r'^[a-f0-9]{24}$')
            self.assertEqual({agent['connection_id'] for agent in snapshot['agents']}, {'a', 'b'})
            self.assertGreaterEqual(self.a.scanned['a'], requested)
            self.assertGreaterEqual(self.b.scanned['b'], requested)
            worker = self.b.refresh()
            self.assertEqual(worker['refresh_errors'], [])
            self.assertEqual({agent['connection_id'] for agent in worker['agents']}, {'b'})

    def test_manual_preview_refresh_bypasses_controller_and_worker_caches(self):
        self.make_directional()
        self.a.mesh.sync('b')
        target = self.b.snapshot()['agents'][0]['id']
        old = {'text': 'Old capture', 'messages': [], 'observation': {'state': 'idle'}}
        self.b.reads.get(('local-preview', target, True), lambda: old)
        self.b.reads.get(('terminal', target, True), lambda: {'ansi': 'Old terminal'})
        with patch.object(self.b, '_local_preview', return_value={
                'text': 'Fresh capture', 'messages': [], 'observation': {'state': 'running'}}) as read, \
                patch.object(bridge, 'capture_terminal', return_value='Fresh terminal'), \
                patch.object(bridge, 'terminal_theme', return_value={}), \
                patch.object(self.b.mesh, 'request', side_effect=AssertionError('Worker attempted reverse access')):
            path = self.urls['a'] + '/api/preview?agent_id=' + target + '&history=1&terminal=1'
            with urlopen(path, timeout=5) as response:
                self.assertEqual(json.load(response)['text'], 'Old capture')
            read.assert_not_called()
            with urlopen(path + '&fresh=1', timeout=5) as response:
                current = json.load(response)
            self.assertEqual(current['text'], 'Fresh capture')
            self.assertEqual(current['terminal']['ansi'], 'Fresh terminal')
            self.assertEqual(read.call_args.kwargs, {'fresh': True})

    def test_controller_controls_worker_once_without_a_callback(self):
        self.make_directional()
        self.a.mesh.sync('b')
        target = self.b.snapshot()['agents'][0]
        command = {'type': 'send', 'agent_id': target['id'], 'message': 'Local fixture task', 'command_id': 'worker_command_123'}
        with patch.object(bridge, 'send') as send, patch.object(self.b.mesh, 'request') as callback:
            first = self.a.action(command)
            second = self.a.action(command)
            self.assertEqual(first, second)
            send.assert_called_once()
            callback.assert_not_called()
        self.assertIn('Ready', self.a.preview(target['id'])['text'])
        self.assertTrue(any(e['kind'] == 'action' for e in self.a.snapshot()['events']))

    def test_controller_queues_on_worker_once_without_reverse_access(self):
        self.make_directional()
        self.a.mesh.sync('b')
        target = self.b.snapshot()['agents'][0]
        command = {'type': 'queue', 'agent_id': target['id'], 'message': 'Compare the saved checkpoints.', 'command_id': 'queued_fixture_123'}
        with patch.object(bridge, 'queue', return_value='queued') as queued, patch.object(self.b.mesh, 'request') as callback:
            first = self.a.action(command)
            second = self.a.action(command)
            self.assertEqual(first, second)
            self.assertIn('Queued for', first['message'])
            queued.assert_called_once()
            self.assertEqual(queued.call_args.args[-1], command['message'])
            callback.assert_not_called()

    def test_switch_controls_are_host_scoped_and_never_request_controller_context(self):
        self.make_directional();self.a.mesh.sync('b')
        target = self.b.snapshot()['agents'][0]
        account = dict(available=True,account={'type':'chatgpt','label':'research@example.test'},version='fixture',login=None)
        with patch.object(self.b.codex_account,'status',return_value=account), \
             patch.object(self.b.codex_account,'start',return_value=account) as login, \
             patch.object(self.b.mesh,'request') as callback:
            result = self.a.switch_info(target['id'], 'account')
            self.assertEqual(result['account'], account['account'])
            login.assert_not_called()
            command = dict(type='account_login',agent_id=target['id'],version='fixture',command_id='worker_account_fixture_123')
            self.assertEqual(self.a.action(command), self.a.action(command))
            login.assert_called_once_with('fixture')
            callback.assert_not_called()
        with patch.object(self.b,'local_preview',return_value={'conversation_id':'research-only',
                'observation':{'state':'done'}, 'messages':[{'role':'user','text':'Continue the experiment.'}]}), \
             patch('agent_switch.shutil.which',return_value='/fixture/claude'), \
             patch('agent_switch.bridge.launch',return_value={'session':'new-research','pane':'%999'}) as launch, \
             patch.object(self.b.mesh,'request') as callback:
            info = self.a.switch_info(target['id'], 'handoff')
            self.assertEqual(info['context'], 'You:\nContinue the experiment.')
            launch.assert_not_called()
            command = dict(type='handoff_start',agent_id=target['id'],version=info['version'],runtime='claude',
                           context=info['context'],command_id='worker_handoff_fixture_123')
            self.assertEqual(self.a.action(command), self.a.action(command))
            launch.assert_called_once()
            callback.assert_not_called()

    def test_native_queue_controls_route_to_the_worker_once_and_preserve_isolation(self):
        self.make_directional();self.a.mesh.sync('b')
        target=self.b.snapshot()['agents'][0]
        binding={'thread_id':'01234567-1234-1234-1234-0123456789ab','path':'/fixture/session.jsonl','cwd':target['cwd']}
        with patch.object(self.b.native,'available',return_value=False), patch.object(self.b.transcripts,'match',return_value={'binding':binding}), \
                patch.object(self.b,'session_hints',return_value=[]), patch.object(self.b.native,'change_queue') as change, \
                patch.object(self.b.mesh,'request') as callback:
            command={'type':'queue_delete','agent_id':target['id'],'thread_id':binding['thread_id'],
                     'queue_id':'fixture-queue','queue_digest':'b'*64,'command_id':'native_delete_fixture_1'}
            self.assertEqual(self.a.action(command),self.a.action(command))
            change.assert_called_once_with(binding,'fixture-queue','b'*64,None)
            callback.assert_not_called()

    def test_model_controls_reach_only_the_bound_worker_session_once(self):
        self.make_directional();self.a.mesh.sync('b')
        target=self.b.snapshot()['agents'][0]
        binding={'thread_id':'01234567-1234-1234-1234-0123456789ab','path':'/fixture/session.jsonl','cwd':target['cwd']}
        settings={'session':binding['thread_id'],'model':'sol','effort':'low','version':'fixture-version','models':[]}
        with patch.object(self.b.transcripts,'match',return_value={'binding':binding}), \
                patch.object(self.b,'session_hints',return_value=[]), \
                patch.object(self.b.native,'model_settings',return_value=settings) as read, \
                patch.object(self.b.native,'change_model',return_value={**settings,'effort':'high'}) as change, \
                patch.object(self.b.mesh,'request',side_effect=AssertionError('Worker attempted reverse access')):
            query={'type':'model_options','agent_id':target['id'],'command_id':'model-read-fixture-1'}
            self.assertEqual(self.a.action(query)['settings'],settings)
            read.assert_called_once_with(binding)
            change.assert_not_called()
            command={'type':'model_set','agent_id':target['id'],'command_id':'model-set-fixture-1',
                     'session':binding['thread_id'],'version':'fixture-version','model':'sol','effort':'high'}
            self.assertEqual(self.a.action(command),self.a.action(command))
            change.assert_called_once_with(binding,'fixture-version','sol','high')
            with self.assertRaisesRegex(bridge.BridgeError,'session'):
                self.a.action({**command,'session':'wrong','command_id':'model-set-fixture-2'})
            self.assertEqual(change.call_count,1)

    def test_worker_rejects_relay_endpoints_remote_targets_and_connections(self):
        self.make_directional()
        for method in ('sync', 'refresh', 'action', 'supervisor', 'handoff', 'execute', 'job'):
            with self.assertRaisesRegex(bridge.BridgeError, 'local observations'):
                self.a.mesh.request('b', method, {})
        target = next(a for a in self.a.snapshot()['agents'] if a['connection_id'] == 'a')
        with patch.object(bridge, 'send') as send:
            with self.assertRaisesRegex(bridge.BridgeError, 'disappeared'):
                self.a.mesh.request('b', 'worker/execute', {'action': {'type': 'send', 'agent_id': target['id'],
                    'message': 'Must not escape worker', 'command_id': 'remote_target_123'}})
            send.assert_not_called()
        with self.assertRaisesRegex(bridge.BridgeError, 'configured local'):
            self.b.save_connection({'id': 'remote', 'kind': 'ssh', 'label': 'Forbidden', 'target': 'fixture@a'})

    def test_exact_approval_is_sent_to_worker_once_without_reverse_access(self):
        self.make_directional();self.a.mesh.sync('b')
        target=self.b.snapshot()['agents'][0]
        binding={'thread_id':'01234567-1234-1234-1234-0123456789ab','path':'/fixture/session.jsonl','cwd':target['cwd']}
        with patch.object(self.b.transcripts,'match',return_value={'binding':binding}), \
                patch.object(self.b,'session_hints',return_value=[]), patch.object(self.b.approvals,'answer') as answer, \
                patch.object(self.b.mesh,'request') as callback:
            command={'type':'approval_respond','agent_id':target['id'],'thread_id':binding['thread_id'],
                     'approval_id':'a'*32,'approval_digest':'b'*64,'decision':'decline','command_id':'approval_worker_fixture_1'}
            self.assertEqual(self.a.action(command),self.a.action(command))
            answer.assert_called_once_with(binding,'a'*32,'b'*64,'decline')
            callback.assert_not_called()

    def test_worker_cannot_report_another_hosts_agents_or_become_supervisor(self):
        self.make_directional()
        snapshot = copy.deepcopy(self.b.mesh.local_export())
        snapshot['agents'][0]['id'] = 'a:forged'
        with patch.object(self.b.mesh, 'local_export', return_value=snapshot):
            with self.assertRaisesRegex(bridge.BridgeError, 'another host'):
                self.a.mesh.sync('b')
        with self.assertRaisesRegex(bridge.BridgeError, 'trusted controller'):
            self.a.mesh.handoff('b')
        self.assertEqual(self.a.mesh.authority()['owner'], 'a')

    def test_source_allowlist_covers_ipv4_ipv6_and_both_http_methods(self):
        import ipaddress
        self.a.client_networks = [ipaddress.ip_network(x) for x in ('100.64.0.1/32', '100.64.0.10/32', 'fd7a:115c:a1e0::a/128')]
        for address in ('127.0.0.1', '::1', '100.64.0.1', '100.64.0.10', 'fd7a:115c:a1e0::a'):
            self.assertTrue(self.a.client_allowed(address))
        for address in ('100.64.0.8', 'fd7a:115c:a1e0::8', '::ffff:100.64.0.8', '192.0.2.1'):
            self.assertFalse(self.a.client_allowed(address))
        with patch.object(self.a, 'client_allowed', return_value=False):
            for data in (None, b'{}'):
                with self.assertRaises(HTTPError) as error:
                    urlopen(Request(self.urls['a'] + '/api/state', data, headers={'X-Forwarded-For': '100.64.0.1'}), timeout=2)
                self.assertEqual(error.exception.code, 403)
                error.exception.close()

    def test_directional_config_validation(self):
        self.make_directional()
        p = self.root / 'worker.json'
        p.write_text(json.dumps(self.b.mesh.config))
        p.chmod(0o600)
        self.assertEqual(read_config(p)['role'], 'worker')
        bad = copy.deepcopy(self.b.mesh.config)
        bad['peers'][0]['role'] = 'peer'
        p.write_text(json.dumps(bad))
        with self.assertRaisesRegex(ValueError, 'Workers only'):
            read_config(p)

    def test_lost_worker_ack_recovers_receipt_without_resending_or_reverse_access(self):
        self.make_directional()
        self.a.mesh.sync('b')
        target = next(agent for agent in self.b.snapshot()['agents'] if agent['connection_id'] == 'b')
        request = dict(type='queue', agent_id=target['id'], message='Compare both traces', command_id='web-fixture-lost-ack')
        original = self.a.mesh.request
        def lose_ack(peer, method, value):
            result = original(peer, method, value)
            if method == 'worker/execute':
                raise bridge.BridgeError('Acknowledgment lost')
            return result
        with patch.object(bridge, 'queue', return_value='queued') as execute, patch.object(self.b.mesh, 'request', side_effect=AssertionError('Reverse access')):
            with patch.object(self.a.mesh, 'request', side_effect=lose_ack):
                with self.assertRaisesRegex(bridge.BridgeError, 'Acknowledgment lost'):
                    self.a.action(request)
            with patch.object(self.b, 'lookup', side_effect=AssertionError('Closed panes need no live lookup')):
                for _ in range(2):
                    result = self.a.delivery_receipt(dict(kind='queue', request=request))
                    self.assertEqual(result['status'], 'complete')
                    self.assertEqual(result['result']['delivery'], 'queued')
            execute.assert_called_once()
            with self.assertRaisesRegex(bridge.BridgeError, 'own agent receipts'):
                self.b.peer_request('a', 'worker/receipt', dict(kind='supervisor', request={}))
            with self.assertRaisesRegex(bridge.BridgeError, 'cannot read'):
                self.b.delivery_receipt(dict(kind='supervisor', owner='a', request={'request_id':'web-fixture-private'}))

    def stop_fixture_node(self, key):
        hub = self.hubs[key]
        config = copy.deepcopy(hub.mesh.config)
        hub.close()
        for thread in hub.workers.values():
            thread.join(timeout=2)
        self.servers[key].shutdown()
        self.servers[key].server_close()
        return config

    def resume_fixture_node(self, key, config):
        port = int(self.urls[key].rsplit(':', 1)[1])
        server = self.servers[key] = ThreadingHTTPServer(('127.0.0.1', port), Handler)
        hub = self.hubs[key] = Hub(self.root / key, poll_interval=.05, node_config=config)
        setattr(self, key, hub)
        server.hub, server.allowed_hosts = hub, {'127.0.0.1'}
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.wait(lambda: bool(hub.snapshot()['agents']))

    def test_worker_outage_and_restart_preserve_receipts_without_offline_queueing(self):
        self.make_directional()
        self.a.mesh.sync('b')
        target = self.b.snapshot()['agents'][0]['id']
        request = dict(type='send', agent_id=target, message='Synthetic outage check', command_id='web-outage-fixture-1')
        with patch.object(bridge, 'send') as send:
            self.a.action(request)
            config = self.stop_fixture_node('b')
            with self.assertRaises(bridge.BridgeError):
                self.a.mesh.sync('b')
            snapshot = self.a.snapshot()
            self.assertTrue(any(a['connection_id'] == 'a' and a['connection_online'] for a in snapshot['agents']))
            self.assertTrue(all(not a['connection_online'] for a in snapshot['agents'] if a['connection_id'] == 'b'))
            with self.assertRaisesRegex(bridge.BridgeError, 'offline'):
                self.a.action({**request, 'command_id': 'web-outage-offline-1'})
            self.assertEqual(send.call_count, 1)
            self.resume_fixture_node('b', config)
            with patch.object(self.b.mesh, 'request', side_effect=AssertionError('Worker callback')):
                self.a.mesh.sync('b')
                for _ in range(2):
                    self.assertEqual(self.a.delivery_receipt(dict(kind='agent', request=request))['status'], 'complete')
                    self.a.action(request)
                self.assertEqual(self.b.deliveries.read('web-outage-offline-1', {**request, 'command_id': 'web-outage-offline-1'})['status'], 'unknown')
            self.assertEqual(send.call_count, 1, 'restart and repeated receipt checks cannot resend')
            self.assertEqual({a['connection_id'] for a in self.b.snapshot()['agents']}, {'b'})

    def test_controller_restart_after_lost_worker_ack_recovers_durable_outcome(self):
        self.make_directional()
        self.a.mesh.sync('b')
        request = dict(type='send', agent_id=self.b.snapshot()['agents'][0]['id'],
                       message='Synthetic lost acknowledgement', command_id='web-controller-crash-1')
        original = self.a.mesh.request
        def lose_ack(peer, method, value):
            result = original(peer, method, value)
            if method == 'worker/execute':
                raise bridge.BridgeError('Synthetic dropped acknowledgement')
            return result
        with patch.object(bridge, 'send') as send:
            with patch.object(self.a.mesh, 'request', side_effect=lose_ack):
                with self.assertRaises(bridge.BridgeError):
                    self.a.action(request)
            config = self.stop_fixture_node('a')
            self.assertTrue(self.b.snapshot()['agents'], 'worker observation survives controller loss')
            self.resume_fixture_node('a', config)
            with patch.object(self.b.mesh, 'request', side_effect=AssertionError('Worker callback')):
                self.a.mesh.sync('b')
                self.assertEqual(self.a.mesh.authority()['owner'], 'a')
                for _ in range(3):
                    self.assertEqual(self.a.delivery_receipt(dict(kind='agent', request=request))['status'], 'complete')
                with self.assertRaisesRegex(bridge.BridgeError, 'uncertain or failed'):
                    self.a.action(request)
            self.assertEqual(send.call_count, 1)


if __name__ == '__main__':
    unittest.main()
