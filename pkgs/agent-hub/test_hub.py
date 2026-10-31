"""Routing and lifecycle tests; integration uses an isolated tmux server."""

import json
import os
from pathlib import Path
import shutil
import sqlite3
import shlex
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock, patch
import uuid

import bridge
from server import Hub
from live import SharedReads


def row(**overrides):
    values = {"session": "main", "window": "@1", "window_name": "project", "pane": "%1",
              "pid": "101", "command": "codex", "cwd": "/tmp/project", "role": "agent",
              "primary": "%1", "owner": "@1", "state": "idle", "summary": "ready", "created": "10"}
    values.update(overrides)
    return bridge.SEP.join(values.get(key, "") for key in bridge.FIELDS)


class FreshPreviewTests(unittest.TestCase):
    def setUp(self):
        self.hub = Hub.__new__(Hub)
        self.hub.mesh = None
        self.hub.reads = SharedReads(ttl=60)
        self.connection = dict(id='local', kind='local')
        self.agent = dict(id='local:fixture', pane='%1', state='done', live=True, runtime='codex')
        self.hub.lookup = Mock(return_value=(self.connection, self.agent))

    def test_notification_read_bypasses_both_caches_and_refreshes_pane_metadata(self):
        old = {'observation':{'state':'done', 'completion_confirmed':True}}
        fresh = {'observation':{'state':'running', 'activity_confirmed':True}}
        self.hub.reads.get(('preview', self.agent['id'], True, False), lambda:old)
        self.hub.reads.get(('local-preview', self.agent['id'], True), lambda:old)
        current = {**self.agent, 'state':'running', 'summary':'Current hook'}
        with patch.object(bridge, 'list_agents', return_value=[current]) as panes, \
                patch.object(self.hub, '_local_preview', return_value=fresh) as read:
            self.assertEqual(self.hub.preview(self.agent['id'], history=True), old)
            self.assertEqual(self.hub.preview(self.agent['id'], history=True, fresh=True), fresh)
            panes.assert_called_once_with(self.connection)
            read.assert_called_once_with(self.connection, current, True, fresh=True)

    def test_notification_read_does_not_join_an_old_inflight_preview(self):
        entered, release = threading.Event(), threading.Event()
        old_reads = []
        def load(connection, agent, history=False, fresh=False):
            if not fresh:
                entered.set()
                if not release.wait(2):
                    raise AssertionError('Fresh notification read joined the held preview')
            return {'observation':{'state':'running' if fresh else 'done'}}
        thread = threading.Thread(target=lambda:old_reads.append(self.hub.preview(self.agent['id'], history=True)))
        with patch.object(self.hub, '_local_preview', side_effect=load), \
                patch.object(bridge, 'list_agents', return_value=[self.agent]):
            try:
                thread.start()
                self.assertTrue(entered.wait(1))
                current = self.hub.preview(self.agent['id'], history=True, fresh=True)
                self.assertEqual(current['observation']['state'], 'running')
                self.assertTrue(thread.is_alive())
            finally:
                release.set()
                thread.join(2)
        self.assertEqual(old_reads, [{'observation':{'state':'done'}}])

    def test_notification_read_rejects_a_vanished_pane_even_with_cached_completion(self):
        with patch.object(bridge, 'list_agents', return_value=[]), patch.object(self.hub, '_local_preview') as read:
            with self.assertRaisesRegex(bridge.BridgeError, 'disappeared'):
                self.hub.preview(self.agent['id'], history=True, fresh=True)
            read.assert_not_called()

    def test_notification_observation_requests_uncached_native_status(self):
        self.hub.transcripts = Mock()
        self.hub.transcripts.observe.side_effect = lambda binding, terminal:terminal
        self.hub.native = Mock()
        self.hub.native.available.return_value = True
        self.hub.native.observe.return_value = dict(state='running')
        binding = dict(thread_id='verified-thread', path='/project/log', cwd='/project')
        with patch.object(bridge, 'observation', return_value=dict(state='done')):
            self.hub.agent_observation(self.connection, self.agent, '', '', {'binding':binding}, fresh=True)
        self.hub.native.observe.assert_called_once_with(binding, dict(state='done'), fresh=True)


class RoutingTests(unittest.TestCase):
    def setUp(self):
        self.local = {"id": "local", "label": "Local", "kind": "local"}

    def test_agent_list_follows_window_numbers_across_live_updates(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(Hub, 'save_connection'):
            hub = Hub(directory)
            try:
                agents = []
                for index, window, name, state in [('10', '@1', 'Alpha', 'waiting'),
                                                    ('2', '@30', 'Zulu', 'running'),
                                                    ('1', '@80', 'Middle', 'idle')]:
                    agent = bridge.parse_panes(row(window_index=index, window=window, window_name=name,
                                                   session_id='$1', pane_index='0', state=state), self.local)[0]
                    agent['changed_at'] = time.time()
                    agents.append(agent)
                entry = dict(config=self.local, agents=agents, status='online', error='', last_seen=time.time())
                hub.connections['local'] = entry
                expected = [agents[2]['id'], agents[1]['id'], agents[0]['id']]
                def order(): return [agent['id'] for agent in hub.snapshot()['agents']]
                self.assertEqual(order(), expected)
                for state in ('running', 'done', 'waiting', 'blocked', 'idle'):
                    agents[2].update(state=state, title='Renamed '+state, summary=state)
                    agents[0].update(state='running', title='Another name')
                    self.assertEqual(order(), expected)
                with hub.db() as db:
                    db.execute('INSERT INTO marks VALUES(?,?)',
                               (agents[2]['id'], json.dumps({'ack_state': 'idle:'+str(agents[2]['changed_at'])})))
                self.assertTrue(hub.snapshot()['agents'][0]['acknowledged'])
                self.assertEqual(order(), expected)
                entry['status'] = 'offline'
                self.assertEqual(order(), expected)
                # A real tmux renumbering changes position without changing agent identity.
                agents[2]['window_index'] = '12'
                self.assertEqual(order(), [agents[1]['id'], agents[0]['id'], agents[2]['id']])
                remote = dict(self.local, id='remote', label='A remote')
                remote_agent = dict(agents[0], id='remote:agent', connection_id='remote',
                                    connection_label=remote['label'], window_index='0', state='blocked')
                hub.connections['remote'] = dict(entry, config=remote, agents=[remote_agent], status='online')
                self.assertEqual(order()[-1], remote_agent['id'])
            finally:
                hub.close()

    def test_window_order_has_stable_session_pane_and_legacy_ties(self):
        agents = [dict(id='pane10', window_index='2', session_id='$1', pane_index='10'),
                  dict(id='session2', window_index='2', session_id='$2', pane_index='0'),
                  dict(id='pane2', window_index='2', session_id='$1', pane_index='2'),
                  dict(id='legacy10', window='@10', pane='%1'),
                  dict(id='legacy2', window='@2', pane='%1')]
        self.assertEqual([a['id'] for a in sorted(agents, key=bridge.window_order)],
                         ['pane2', 'pane10', 'session2', 'legacy2', 'legacy10'])
        capture = '\n'.join([row(session='__hidden', session_id='$0', window_index='99'),
                             row(session='main', session_id='$1', window_index='3', pane_index='2')])
        agent = bridge.parse_panes(capture, self.local)[0]
        self.assertEqual((agent['window_index'], agent['session_id'], agent['pane_index']), ('3', '$1', '2'))

    def test_refresh_waits_for_a_new_scan_and_wakes_sleeping_poller(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(Hub, "save_connection"):
            hub = Hub(directory, poll_interval=60)
            hub.connections['local'] = {'config': self.local, 'agents': [], 'status': 'online', 'error': '', 'last_seen': 0}
            scanning, release = threading.Event(), threading.Event()
            scans = []
            def scan(config):
                scans.append(1)
                if len(scans) == 1:
                    scanning.set()
                    release.wait(2)
                    return bridge.parse_panes(row(), config)
                return []  # A window was removed after the first scan started.
            thread = threading.Thread(target=hub.poll_connection, args=('local',), daemon=True)
            results = []
            refresh = threading.Thread(target=lambda: results.append(hub.refresh()), daemon=True)
            try:
                with patch.object(bridge, 'list_agents', side_effect=scan), patch.object(bridge, 'capture', return_value=''):
                    thread.start()
                    self.assertTrue(scanning.wait(1))
                    refresh.start()
                    self.assertTrue(hub.scan_wake['local'].wait(1))
                    release.set()
                    refresh.join(2)
                    self.assertFalse(refresh.is_alive())
                    self.assertEqual(len(scans), 2)
                    self.assertEqual(results[0]['agents'], [])
                    self.assertEqual(results[0]['refresh_errors'], [])
                    hub.refresh_local()  # Wake a poller with a 60-second interval.
                    self.assertEqual(len(scans), 3)
            finally:
                release.set()
                hub.close()
                thread.join(2)
                refresh.join(2)

    def test_refresh_reports_timeout_without_discarding_last_snapshot(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(Hub, "save_connection"):
            hub = Hub(directory)
            hub.connections['local'] = {'config': self.local, 'agents': bridge.parse_panes(row(), self.local),
                                        'status': 'online', 'error': '', 'last_seen': time.time()}
            hub.connections['local']['agents'][0]['changed_at'] = time.time()
            try:
                with self.assertRaisesRegex(bridge.BridgeError, 'did not finish'):
                    hub.refresh_local(timeout=.01)
                with patch.object(hub, 'refresh_local', side_effect=bridge.BridgeError('Scan timed out')):
                    snapshot = hub.refresh()
                self.assertEqual(len(snapshot['agents']), 1)
                self.assertEqual(snapshot['refresh_errors'], ['This host: Scan timed out'])
            finally:
                hub.close()

    def test_two_completed_turns_without_a_polled_running_interval_get_distinct_versions(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(Hub, 'save_connection'):
            hub = Hub(directory)
            hub.connections['local'] = dict(config=self.local, agents=[], status='online', error='', last_seen=0)
            observations = iter([
                dict(state='done', summary='Completed', completion_confirmed=True, turn_status={'turn_id':'one'}),
                dict(state='done', summary='Completed', completion_confirmed=True, turn_status={'turn_id':'two'}),
                dict(state='done', summary='Completed', completion_confirmed=True, turn_status={'turn_id':'two'}),
            ])
            scans = []
            def after_scan(delay):
                agent = hub.connections['local']['agents'][0]
                scans.append((agent['turn_status']['turn_id'], agent['changed_at']))
                if len(scans) == 3:
                    hub.stop.set()
            hub.scan_wake['local'] = Mock(wait=Mock(side_effect=after_scan))
            try:
                with patch.object(bridge, 'list_agents', side_effect=lambda connection:bridge.parse_panes(row(), connection)), \
                        patch.object(bridge, 'capture', return_value=''), \
                        patch.object(hub, 'agent_observation', side_effect=lambda *args:next(observations)):
                    hub._poll_connection('local')
                self.assertEqual([turn for turn, _ in scans], ['one', 'two', 'two'])
                self.assertNotEqual(scans[0][1], scans[1][1])
                self.assertEqual(scans[1][1], scans[2][1])
            finally:
                hub.close()

    def test_rename_uses_window_id_and_disables_automatic_names(self):
        agent = {"id": "agent", "window": "@23"}
        with patch.object(bridge, "verify_agent", return_value=agent), patch.object(bridge, "run") as run:
            bridge.rename(self.local, agent, "RTC: baseline study")
            args = run.call_args.args[1]
            self.assertEqual(args[-5:], ["rename-window", "-t", "@23", "--", "RTC: baseline study"])
            self.assertIn("automatic-rename", args)
            self.assertIn("allow-rename", args)
        for name in ("", "x\nname", "x" * 81, None):
            with self.assertRaises(bridge.BridgeError):
                bridge.rename(self.local, agent, name)

    def test_watch_updates_join_chat_without_copying_or_replaying(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(Hub, "save_connection"):
            hub = Hub(directory)
            try:
                hub.message("user", "Keep me updated")
                for index in range(25):
                    hub.watcher.emit(str(index), "progress", f"Progress {index}", "Observed output", "local:agent")
                first = hub.snapshot()["messages"]
                updates = [item for item in first if item["role"] == "update"]
                self.assertEqual(len(updates), 20)
                self.assertEqual(len(first), 21)
                self.assertEqual(first, hub.snapshot()["messages"])
                self.assertEqual(len({item["id"] for item in updates}), 20)
                self.assertEqual(updates[-1]["agent_id"], "local:agent")
                self.assertEqual([item["created"] for item in first], sorted(item["created"] for item in first))
                self.assertEqual(len(hub.snapshot(include_notifications=False)["messages"]), 1)
                with patch.object(hub.watcher, "eligible", return_value=False):
                    self.assertEqual(len(hub.snapshot()["messages"]), 1)
                with hub.db() as db:
                    self.assertEqual(db.execute("SELECT count(*) FROM messages").fetchone()[0], 1)
                restored = Hub(directory)
                try:
                    self.assertEqual(first, restored.snapshot()["messages"])
                finally:
                    restored.close()
            finally:
                hub.close()

    def test_chat_handoff_keeps_exact_turn_and_reply_identity(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(Hub, 'save_connection'):
            hub = Hub(directory)
            try:
                hub.watcher.emit('short', 'done', 'Claude · gpu-004', 'A reply is ready.', 'local:agent',
                                 target={'handoff_id':'handoff-current', 'version':'done:123'})
                notice = hub.watcher.state()['items'][0]
                sources = [{'message_id':'claude-final', 'digest':'fixture-digest'}]
                hub.assistant.put('assistant_reports', dict(id=notice['id'], created=notice['created'], status='ready',
                                  summary='The measured score is **0.4**.', original_reply=True, sources=sources))
                message = hub.snapshot()['messages'][0]
                self.assertEqual(message['handoff_id'], 'handoff-current')
                self.assertEqual(message['sources'], sources)
                self.assertEqual(message['agent_id'], 'local:agent')
                self.assertTrue(message['original_reply'])
                self.assertEqual(message['text'], 'Claude · gpu-004\nThe measured score is **0.4**.')
            finally:
                hub.close()

    def test_storage_failure_does_not_keep_removed_agents(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(Hub, "save_connection"):
            hub = Hub(directory, poll_interval=0.001)
            hub.connections["local"] = {"config": self.local, "agents": [], "status": "online", "error": "", "last_seen": time.time()}
            hub.mesh = Mock(node_id="local")
            hub.mesh.save_cache.side_effect = sqlite3.OperationalError("database or disk is full")
            polls = []
            def scan(config):
                polls.append(1)
                if len(polls) == 1:
                    return bridge.parse_panes(row(), self.local)
                hub.stop.set()
                return []
            try:
                with patch.object(bridge, "list_agents", side_effect=scan), patch.object(bridge, "capture", return_value=""), patch.object(hub, "event", side_effect=sqlite3.OperationalError("database or disk is full")), self.assertLogs(level="ERROR"):
                    hub.poll_connection("local")
                self.assertEqual(hub.mesh.save_cache.call_count, 2)
                hub.mesh = None
                self.assertEqual(len(polls), 2)
                self.assertEqual(hub.snapshot()["agents"], [])
                self.assertEqual(hub.snapshot()["connections"][0]["status"], "online")
                hub.connections["local"]["last_seen"] = time.time() - 60
                self.assertEqual(hub.snapshot()["connections"][0]["status"], "offline")
            finally:
                hub.close()

    def test_poller_retries_after_unexpected_failure(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(Hub, "save_connection"):
            hub = Hub(directory, poll_interval=0.001)
            hub.connections["local"] = {"config": self.local, "agents": [], "status": "online", "error": "", "last_seen": time.time()}
            try:
                with patch.object(hub, "_poll_connection", side_effect=[RuntimeError("temporary failure"), None]) as poller, self.assertLogs(level="ERROR"):
                    hub.poll_connection("local")
                self.assertEqual(poller.call_count, 2)
                self.assertEqual(hub.snapshot()["connections"][0]["status"], "offline")
            finally:
                hub.close()

    def test_same_pane_ids_on_different_hosts_never_collide(self):
        remote = {**self.local, "id": "remote", "kind": "ssh", "target": "server"}
        a = bridge.parse_panes(row(), self.local)[0]
        b = bridge.parse_panes(row(), remote)[0]
        self.assertNotEqual(a["id"], b["id"])

    def test_reused_pane_id_is_a_new_agent(self):
        a = bridge.parse_panes(row(), self.local)[0]
        b = bridge.parse_panes(row(pid="202", created="11"), self.local)[0]
        self.assertNotEqual(a["id"], b["id"])

    def test_linked_windows_are_deduplicated_and_terminal_mirrors_excluded(self):
        output = "\n".join([row(session="__park"), row(), row(role="terminal", pane="%2", command="zsh")])
        agents = bridge.parse_panes(output, self.local)
        self.assertEqual(len(agents), 1)
        self.assertEqual(agents[0]["session"], "main")

    def test_shell_replacing_agent_cannot_receive_input(self):
        expected = bridge.parse_panes(row(), self.local)[0]
        with patch.object(bridge, "list_agents", return_value=bridge.parse_panes(row(command="zsh"), self.local)), patch.object(bridge, "run") as run:
            with self.assertRaisesRegex(bridge.BridgeError, "no longer running"):
                bridge.send(self.local, expected, "please continue")
            run.assert_not_called()

    def test_replaced_pid_cannot_receive_input(self):
        expected = bridge.parse_panes(row(), self.local)[0]
        with patch.object(bridge, "list_agents", return_value=bridge.parse_panes(row(pid="202"), self.local)):
            with self.assertRaisesRegex(bridge.BridgeError, "no longer running"):
                bridge.interrupt(self.local, expected)

    def test_restarted_agent_in_same_shell_cannot_receive_stale_input(self):
        with patch.object(bridge, "run", return_value=row()), patch.object(bridge, "run_command", return_value="101 301\n"):
            expected = bridge.list_agents(self.local)[0]
        with patch.object(bridge, "run", return_value=row()), patch.object(bridge, "run_command", return_value="101 302\n"):
            with self.assertRaisesRegex(bridge.BridgeError, "no longer running"):
                bridge.send(self.local, expected, "an instruction intended for the previous agent")

    def test_running_and_approval_dialogs_cannot_receive_new_prompts(self):
        expected = bridge.parse_panes(row(), self.local)[0]
        for footer in ("• Working (1m • esc to interrupt)\n› Ask Codex to do anything", "Would you like to run the following command?"):
            with patch.object(bridge, "list_agents", return_value=[expected]), patch.object(bridge, "run", return_value=footer) as run:
                with self.assertRaises(bridge.BridgeError):
                    bridge.send(self.local, expected, "an instruction")
                self.assertEqual(run.call_count, 1)

    def test_choice_is_one_key_and_only_when_on_screen(self):
        expected = bridge.parse_panes(row(), self.local)[0]
        menu = "How long?\n\n❯ 1. Short\n  2. Long\n\nEnter to select · Esc to cancel"
        with patch.object(bridge, "list_agents", return_value=[expected]), patch.object(bridge, "run", side_effect=[menu, ""]) as run:
            bridge.choose(self.local, expected, 2)
            self.assertEqual(run.call_args[0][1][-1], "2")
        with patch.object(bridge, "list_agents", return_value=[expected]), patch.object(bridge, "run", return_value="● Done.\n› Ask Codex to do anything") as run:
            with self.assertRaisesRegex(bridge.BridgeError, "no longer on its screen"):
                bridge.choose(self.local, expected, 2)
            self.assertEqual(run.call_count, 1)
        with self.assertRaises(bridge.BridgeError):
            bridge.choose(self.local, expected, "2; rm")
        with patch.object(bridge, "list_agents", return_value=[expected]), patch.object(bridge, "run", side_effect=["  ❯ 1. Default\n    2. Sonnet\n  ↓ 3. Fable", ""]) as run:
            bridge.choose(self.local, expected, 3)
            self.assertEqual(run.call_args[0][1][-1], "3")

    def test_a_turn_ended_with_shells_still_running_is_progress(self):
        rule = '─' * 40
        report = f"● l15 board: 340 of 360.\n✻ Baked for 1m 13s · done 1:33 AM · 1 shell still running\n{rule}\n❯ \n{rule}\n  -- INSERT -- ⏵⏵ auto mode on · 1 shell · ⧉ index"
        self.assertTrue(bridge.background_work(report))
        self.assertTrue(bridge.background_work(report.replace('1 shell', '2 monitors')))
        finished = f"● All 360 scored.\n✻ Baked for 2m · done 1:50 AM\n{rule}\n❯ \n{rule}\n  -- INSERT -- ⏵⏵ auto mode on · ⧉ index"
        self.assertFalse(bridge.background_work(finished))
        # Words in the conversation about shells don't count.
        self.assertFalse(bridge.background_work("● I opened · 1 shell for you\n" + "\n".join(f"line {i}" for i in range(10))))

    def test_a_claude_menu_is_its_highlighted_choice_not_a_numbered_message(self):
        rule = '─' * 40
        message = f"❯ 1. don't add the needs you. keep the sessions fixed\n\n● Done.\n\n{rule}\n❯ \n{rule}\n  ? for shortcuts"
        self.assertFalse(bridge.claude_menu(message))
        self.assertFalse(bridge.claude_menu(f"● Do you want me to go on?\n\n{rule}\n❯ 1. a draft of mine\n{rule}"))
        self.assertTrue(bridge.claude_menu("Do you want to proceed?\n❯ 1. Yes\n  2. No, and tell Claude what to do differently (esc)"))
        self.assertTrue(bridge.claude_menu(f"{message}\n│ How long?\n\n  1. Short\n❯ 2. Long\n{rule}\n  3. Chat about this\n\nEnter to select · Esc to cancel"))
        claude = dict(bridge.parse_panes(row(), self.local)[0], runtime='claude', live=True)
        self.assertNotEqual(bridge.classify(claude, message)[0], 'waiting')

    def test_a_message_to_claude_while_it_asks_closes_the_menu_first(self):
        expected = dict(bridge.parse_panes(row(), self.local)[0], runtime='claude')
        menu = "Do you want to proceed?\n❯ 1. Yes\n  2. No"
        closed = f"⎿ Interrupted\n{'─' * 40}\n❯ \n{'─' * 40}"
        with patch.object(bridge, 'verify_agent', return_value=expected), patch.object(bridge, 'capture', side_effect=[menu, closed]), \
                patch.object(bridge, 'classify', return_value=('waiting', '')), patch.object(bridge, 'run') as run, patch.object(bridge, 'paste') as paste:
            bridge.send(self.local, expected, 'do it differently')
            self.assertEqual(run.call_args_list[0][0][1][-1], 'Escape')
            paste.assert_called_once()

    def test_claude_takes_a_message_mid_turn_only_into_an_empty_box(self):
        expected = dict(bridge.parse_panes(row(), self.local)[0], runtime='claude')
        with patch.object(bridge, 'verify_agent', return_value=expected), patch.object(bridge, 'capture', return_value='screen'), \
                patch.object(bridge, 'classify', return_value=('running', '')), patch('claude_models.empty_editor', return_value=True), \
                patch.object(bridge, 'paste') as paste:
            self.assertEqual(bridge.queue(self.local, expected, 'also check the tests'), 'queued')
            paste.assert_called_once()
        with patch.object(bridge, 'verify_agent', return_value=expected), patch.object(bridge, 'capture', return_value='screen'), \
                patch.object(bridge, 'classify', return_value=('running', '')), patch('claude_models.empty_editor', return_value=False), \
                patch.object(bridge, 'paste') as paste:
            with self.assertRaisesRegex(bridge.BridgeError, 'draft or open menu'):
                bridge.queue(self.local, expected, 'x')
            paste.assert_not_called()

    def test_an_answer_in_your_own_words_picks_the_free_text_choice_then_types_it(self):
        expected = bridge.parse_panes(row(), self.local)[0]
        screen = "Which colour do you like?\n  1. Red\n  2. Blue\n  3. Type something.\n"
        with patch.object(bridge, "list_agents", return_value=[expected]), patch.object(bridge, "run", side_effect=[screen, "", "", ""]) as run, \
                patch.object(bridge.time, "sleep"):
            bridge.answer(self.local, expected, 3, "green, like a forest")
        calls = [c.args[1] for c in run.call_args_list]
        self.assertEqual(calls[1], ["send-keys", "-t", expected["pane"], "3"])
        self.assertEqual(run.call_args_list[2].kwargs["stdin"], "green, like a forest")
        self.assertEqual(calls[3], ["send-keys", "-t", expected["pane"], "Enter"])
        with self.assertRaises(bridge.BridgeError):
            bridge.answer(self.local, expected, 3, "  ")

    def test_message_is_stdin_not_shell_code(self):
        expected = bridge.parse_panes(row(), self.local)[0]
        message = "literal `touch /tmp/no` and $(do-not-run)\nnext line"
        with patch.object(bridge, "list_agents", return_value=[expected]), patch.object(bridge, "run", side_effect=["› Ask Codex to do anything", "", ""]) as run, \
                patch.object(bridge.time, "sleep"):
            bridge.send(self.local, expected, message)
            args, kwargs = run.call_args_list[-2]
            self.assertNotIn(message, args[1])
            self.assertEqual(kwargs["stdin"], message)
            self.assertEqual(run.call_args.args[1], ["send-keys", "-t", expected["pane"], "Enter"])

    def test_live_status_ignores_blank_padding_and_uses_cli_timer(self):
        agent = bridge.parse_panes(row(state="done"), self.local)[0]
        result = bridge.observation(agent, "• Waiting for background terminal (2m 13s • esc to interrupt)\n› Ask Codex to do anything\n" + "\n" * 40)
        self.assertEqual(result["state"], "running")
        self.assertEqual(result["activity_phase"], "Waiting for terminal")
        self.assertEqual(result["activity_elapsed"], 133)
        self.assertTrue(result["activity_confirmed"])

    def test_every_claude_spinner_frame_is_live_activity(self):
        # Claude Code on Linux cycles · ✢ * ✶ ✻ ✽; the ASCII frame must not
        # read as an unverified turn.
        for glyph in "·✢*✶✻✽":
            with self.subTest(glyph=glyph):
                agent = bridge.parse_panes(row(state="running"), self.local)[0]
                agent["runtime"] = "claude"
                result = bridge.observation(agent, f"● Checking the build.\n\n{glyph} Honking… (1m 15s · ↓ 3.8k tokens)\n\n────\n❯\n────")
                self.assertEqual(result["state"], "running")
                self.assertEqual(result["activity_elapsed"], 75)
                self.assertTrue(result["activity_confirmed"])

    def test_activity_fingerprint_ignores_spinner_timer(self):
        agent = bridge.parse_panes(row(state="running"), self.local)[0]
        first = bridge.observation(agent, "result\n• Working (2s • esc to interrupt)\n›")
        second = bridge.observation(agent, "result\n• Working (3s • esc to interrupt)\n›")
        self.assertEqual(first["activity_fingerprint"], second["activity_fingerprint"])
        idle = bridge.observation(agent, "Done\nWorked for 3s\n›\n" + "\n" * 40)
        self.assertEqual(idle["state"], "done")
        self.assertFalse(idle["activity_confirmed"])

    def test_compaction_is_one_live_phase_and_timer_not_new_output(self):
        for runtime, status in [
            ('codex', '• Compacting context (2m 13s • esc to interrupt)'),
            ('codex', '• Compacting context (2m 13s • esc to interrupt) · 1 background terminal running · /ps to view'),
            ('codex', '• Compacting context… (2m 13s)'),
            ('claude', '✻ Compacting conversation… (2m 13s · ↓ 3.2k tokens)'),
            ('claude', '✢ Auto-compacting… (2m 13s · esc to interrupt)'),
        ]:
            with self.subTest(runtime=runtime, status=status):
                agent = bridge.parse_panes(row(state='done'), self.local)[0]
                agent['runtime'] = runtime
                first = bridge.observation(agent, '• The comparison is saved.\n' + status + '\n›')
                second = bridge.observation(agent, '• The comparison is saved.\n' + status.replace('13s', '14s') + '\n›')
                self.assertEqual(first['state'], 'running')
                self.assertEqual(first['activity_phase'], 'Compacting')
                self.assertEqual(first['activity_elapsed'], 133)
                self.assertTrue(first['activity_confirmed'])
                self.assertEqual(first['activity_fingerprint'], second['activity_fingerprint'])
                resumed = bridge.observation(agent, '• The comparison is saved.\n• Working (2m 15s • esc to interrupt)\n›')
                self.assertEqual(resumed['activity_phase'], 'Working')
                self.assertEqual(first['activity_fingerprint'], resumed['activity_fingerprint'])

    def test_compaction_without_timer_stays_active_and_errors_are_not_status(self):
        agent = bridge.parse_panes(row(state='done'), self.local)[0]
        for status in ['• Compacting context', '✻ Compacting…']:
            result = bridge.observation(agent, status + '\n›')
            self.assertEqual(result['state'], 'running')
            self.assertEqual(result['activity_phase'], 'Compacting')
            self.assertIsNone(result['activity_elapsed'])
        for message in ['• Compacting context failed: access denied.', '• I am compacting the dataset.', '• Context compacted']:
            result = bridge.observation(agent, message + '\n›')
            self.assertFalse(result['activity_confirmed'])
            self.assertEqual(result['activity_fingerprint'], bridge.fingerprint(message))
        old = '• Compacting context (2s)\n' + 'normal output\n' * 30 + '›'
        self.assertFalse(bridge.observation(agent, old)['activity_confirmed'])

    def test_working_text_in_scrollback_does_not_override_visible_idle_prompt(self):
        agent = bridge.parse_panes(row(state="running"), self.local)[0]
        output = "• Working (old • esc to interrupt)\n" + "normal output\n" * 30 + "› Ask Codex to do anything"
        self.assertEqual(bridge.classify(agent, output)[0], "done")

    def test_codex_welcome_screen_is_not_a_completed_turn(self):
        agent = bridge.parse_panes(row(state="running"), self.local)[0]
        welcome = "│ >_ OpenAI Codex (v0.153.0) │\n│ model: gpt-6.1-sol │\nTip: New /fast mode\n› Ask Codex to do anything"
        self.assertEqual(bridge.classify(agent, welcome), ("idle", "Ready for a new task"))
        agent["state"] = "done"
        self.assertEqual(bridge.classify(agent, welcome)[0], "idle")
        self.assertEqual(bridge.classify(agent, welcome + "\n• The test passed.\n›")[0], "done")
        self.assertEqual(bridge.classify(agent, welcome + "\n• Working (2s • esc to interrupt)\n›")[0], "running")

    def test_ssh_target_cannot_inject_options_or_shell(self):
        for target in ("-oProxyCommand=evil", "host;touch /tmp/no", "$(bad)", "host\nother"):
            with self.assertRaises(bridge.BridgeError):
                bridge.validate_connection({"kind": "ssh", "label": "Remote", "target": target})

    def test_codex_jsonl_response(self):
        answer = {"reply": "Two agents need review.", "actions": []}
        output = json.dumps({"type": "item.completed", "item": {"type": "agent_message", "text": json.dumps(answer)}})
        self.assertEqual(Hub.parse_response(output), answer)

    def test_unstructured_error_is_not_a_successful_answer(self):
        with self.assertRaises(bridge.BridgeError):
            Hub.parse_response("API quota exhausted")

    def test_completed_turn_stays_in_attention_until_reviewed(self):
        observed = 0

        def agents(connection):
            nonlocal observed
            observed += 1
            return bridge.parse_panes(row(state="running" if observed == 1 else "idle"), connection)

        def preview(*args):
            return "• Working (1m • esc to interrupt)" if observed == 1 else "• The reply.\nWorked for 1m\n› Ready for an instruction"

        with tempfile.TemporaryDirectory() as directory, patch.object(bridge, "list_agents", side_effect=agents), patch.object(bridge, "capture", side_effect=preview):
            hub = Hub(directory, poll_interval=0.03)
            try:
                for _ in range(50):
                    if observed >= 4:
                        break
                    time.sleep(0.02)
                agent = hub.snapshot()["agents"][0]
                self.assertEqual(agent["state"], "done")
                self.assertTrue(agent["needs_attention"])
                hub.action({"type": "acknowledge", "agent_id": agent["id"]})
                time.sleep(0.08)
                reviewed = hub.snapshot()["agents"][0]
                self.assertEqual(reviewed["changed_at"], agent["changed_at"])
                self.assertTrue(reviewed["acknowledged"])
            finally:
                hub.close()


@unittest.skipUnless(shutil.which("tmux"), "tmux required")
class TmuxIntegrationTests(unittest.TestCase):
    @unittest.skipUnless(sys.platform == "linux", "Linux process names required")
    def test_launch_send_and_pause_on_an_isolated_agent(self):
        with tempfile.TemporaryDirectory(prefix="agent-hub-control-") as directory:
            root = Path(directory)
            binary = root / "codex"
            worker = root / "worker.py"
            args_file, input_file = root / "args.json", root / "input.txt"
            worker.write_text(f'''#!{sys.executable}
import ctypes, json, sys
ctypes.CDLL(None).prctl(15, b"codex", 0, 0, 0)
open({str(args_file)!r}, "w").write(json.dumps(sys.argv[1:]))
print("› Ready for next instruction", flush=True)
while True:
    try:
        line = input()
        open({str(input_file)!r}, "a").write(line + "\\n")
        if line == "work":
            print("• Working (1m • esc to interrupt)", flush=True)
        else:
            print("› Ready for next instruction", flush=True)
    except KeyboardInterrupt:
        print("\\x1b[2J\\x1b[HPaused\\n› Ready for next instruction", flush=True)
''')
            binary.write_text(f'#!{shutil.which("bash")}\nexec -a codex {shlex.quote(sys.executable)} {shlex.quote(str(worker))} "$@"\n')
            binary.chmod(0o700)
            conn = {"id": "control-test", "kind": "local", "label": "Test", "socket": str(root / "tmux.sock")}
            env = {**os.environ, "PATH": str(root) + os.pathsep + os.environ["PATH"]}
            subprocess.run(["tmux", "-f", "/dev/null", "-S", conn["socket"], "new-session", "-d", "-s", "fixture", "sleep 60"], env=env, check=True)
            try:
                bridge.run(conn, ["set-option", "-g", "default-shell", "/bin/sh", ";", "set-environment", "-g", "PATH", env["PATH"],
                                  ";", "set-option", "-g", "remain-on-exit", "on"])
                prompt = "literal $(do-not-execute) `nor-this`"
                with patch.dict(os.environ, {"PATH": env["PATH"]}):
                    launched = bridge.launch(conn, directory, prompt, codex_model="fixture-model")
                for _ in range(30):
                    if args_file.exists():
                        break
                    time.sleep(0.1)
                self.assertTrue(args_file.exists(), bridge.capture(conn, launched["pane"]))
                self.assertEqual(json.loads(args_file.read_text()), ["--model", "fixture-model", "--", prompt])
                for _ in range(30):
                    agents = bridge.list_agents(conn)
                    if len(agents) == 1 and agents[0]["live"]:
                        break
                    time.sleep(0.1)
                self.assertEqual(len(agents), 1)
                agent = agents[0]
                self.assertTrue(agent["live"], agent)
                self.assertTrue(agent["foreground_pid"].isdigit())
                bridge.send(conn, agent, "work")
                time.sleep(0.2)
                self.assertEqual(bridge.classify(agent, bridge.capture(conn, launched["pane"]))[0], "running")
                bridge.interrupt(conn, agent)
                time.sleep(0.2)
                self.assertIn("Paused", bridge.capture(conn, launched["pane"]))
                bridge.send(conn, agent, prompt)
                time.sleep(0.2)
                self.assertIn(prompt, input_file.read_text())
            finally:
                bridge.run(conn, ["kill-server"])

    def test_literal_delivery_on_an_isolated_socket(self):
        with tempfile.TemporaryDirectory(prefix="agent-hub-test-") as directory:
            socket_path = str(Path(directory) / "tmux.sock")
            conn = {"id": "test", "kind": "local", "label": "Test", "socket": socket_path}
            pane = bridge.run(conn, ["new-session", "-d", "-s", "test", "-P", "-F", "#{pane_id}", "cat"]).strip()
            try:
                time.sleep(0.15)
                message = "literal $(should-never-execute) `and-this`"
                buffer = uuid.uuid4().hex
                bridge.run(conn, ["load-buffer", "-b", buffer, "-", ";", "paste-buffer", "-p", "-d", "-b", buffer,
                                  "-t", pane, ";", "send-keys", "-t", pane, "Enter"], stdin=message)
                time.sleep(0.15)
                self.assertIn(message, bridge.capture(conn, pane))
                raw = bridge.run(conn, ["list-panes", "-a", "-F", bridge.FORMAT])
                self.assertEqual(bridge.parse_panes(raw, conn), [])
            finally:
                bridge.run(conn, ["kill-server"])


if __name__ == "__main__":
    unittest.main()
