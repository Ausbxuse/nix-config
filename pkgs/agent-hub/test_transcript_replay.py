"""Conversation attribution replays using synthetic logs, never user sessions."""
import hashlib
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import tempfile
import textwrap
import threading
import time
import unittest
from unittest.mock import patch
from urllib.parse import urlencode
from urllib.request import urlopen

import bridge
from server import Handler, Hub, ThreadingHTTPServer
from transcripts import Transcripts


class ReplayFixture:
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='hub-transcript-replay-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.logs = self.root / 'sessions/2026/10/25'
        self.logs.mkdir(parents=True)
        self.reader = Transcripts(self.root / 'sessions', self.root / 'claude/projects')
        self.agent = dict(id='fixture:%1:101:@1:201', runtime='codex', cwd='/fixture/project')
        self.anchor = ('The checkpoint comparison has completed with the same controller settings in every trial. '
                       'The measurements include the commanded motion, observed displacement, and elapsed time. '
                       'Each saved trace can be compared against the matching evaluation run without guessing its source.')
        self.exchange = [('user', 'The supervisor is showing the previous conversation even though this terminal has started a different task.'),
                         ('assistant', 'I will check the transcript selected for this pane and compare its latest messages with the current terminal output.')]

    def seed(self, name, messages, cwd=None):
        path = self.logs / ('rollout-' + name + '.jsonl')
        records = [dict(type='session_meta', payload=dict(id=name, cwd=cwd or self.agent['cwd'])),
                   dict(type='response_item', payload=dict(type='reasoning', text='PRIVATE REASONING SENTINEL'))]
        records.extend(self.record(index, role, text) for index, (role, text) in enumerate(messages))
        path.write_text(''.join(json.dumps(record) + '\n' for record in records))
        self.reader.scanned = 0
        return path

    @staticmethod
    def record(index, role, text):
        return dict(type='response_item', timestamp=f'2026-10-25T12:00:{index:02d}Z', payload=dict(
            type='message', role=role, content=[dict(type='input_text' if role == 'user' else 'output_text', text=text)]))

    @staticmethod
    def render(messages, width=80):
        blocks = []
        for role, text in messages:
            lines = textwrap.wrap(text, width=width - 2, break_long_words=False, break_on_hyphens=False)
            blocks.append(('› ' if role == 'user' else '• ') + '\n  '.join(lines))
        return '\n\n'.join(blocks) + '\n\n• Working (3s • esc to interrupt)\n› '

    def match(self, capture, **kwargs):
        result = self.reader.match(self.agent, capture, binding=True, **kwargs)
        self.assertNotIn('PRIVATE REASONING SENTINEL', json.dumps(result))
        return result


class TranscriptReplayTests(ReplayFixture, unittest.TestCase):
    def test_restored_session_replay_across_width_history_mtime_and_hint_order(self):
        old = self.seed('old', [('assistant', self.anchor * 6)])
        current = self.seed('current', self.exchange)
        self.seed('other-project', self.exchange, cwd='/another/project')
        # Discovery order and shared-daemon hints must not decide identity.
        for latest in (old, current):
            os.utime(latest, (2_000_000_000, 2_000_000_000))
            self.reader.scanned = 0
            for width in (24, 47, 100):
                for history in (False, True):
                    for hints in ([str(old), str(current)], [str(current), str(old)]):
                        with self.subTest(latest=latest.name, width=width, history=history, hints=hints):
                            self.match(self.render([('assistant', self.anchor * 6)], width), hints=hints)
                            capture = self.render([('assistant', self.anchor * 6), *self.exchange], width)
                            result = self.match(capture, history=history, hints=hints)
                            self.assertEqual(result['binding']['thread_id'], 'current')
                            self.assertEqual([m['text'] for m in result['messages']], [text for _, text in self.exchange])
                            self.assertEqual(result['archive_source'], str(current))
            os.utime(latest, (100, 100))

    def test_unseen_reply_cannot_be_added_by_a_tool_user_or_quoted_example(self):
        reply = 'The current configuration has been rebuilt successfully.'
        self.seed('current', [('assistant', self.anchor), ('assistant', reply)])
        for suffix in ('• Ran cat saved-output\n  ' + reply,
                       '• Exited (status 1) check\n  ' + reply,
                       '› ' + reply,
                       '• Example:\n  ```text\n  ' + reply + '\n  ```'):
            for history in (False, True):
                with self.subTest(suffix=suffix, history=history):
                    result = self.match('• ' + self.anchor + '\n' + suffix, history=history)
                    if suffix.startswith(('• Ran ', '• Exited ')):
                        self.assertEqual([m['text'] for m in result['messages']], [self.anchor])
                    else:
                        self.assertEqual(result, {}, 'New unmatched dialogue must use the live capture')
        result = self.match('• ' + self.anchor + '\n• ' + reply)
        self.assertEqual(result['messages'][-1]['text'], reply)

    def test_new_user_message_requires_the_whole_prompt_outside_quotes(self):
        prompt = 'Continue the comparison'
        self.seed('current', [('assistant', self.anchor), ('user', prompt)])
        for suffix in ('› ' + prompt + ' and change the selected checkpoint.',
                       '• Example:\n```text\n› ' + prompt + '\n```',
                       '• Ran cat request\n  › ' + prompt):
            with self.subTest(suffix=suffix):
                result = self.match('• ' + self.anchor + '\n' + suffix)
                if suffix.startswith('• Ran '):
                    self.assertEqual([m['text'] for m in result['messages']], [self.anchor])
                else:
                    self.assertEqual(result, {}, 'A different prompt or quoted example cannot reuse the old binding')
        result = self.match('• ' + self.anchor + '\n› ' + prompt + '\n• Working (2s • esc to interrupt)\n› ')
        self.assertEqual(result['messages'][-1]['text'], prompt)

    def test_followups_cannot_be_matched_in_reverse_terminal_order(self):
        prompt, reply = 'Run the next comparison.', 'The next comparison is complete.'
        self.seed('current', [('assistant', self.anchor), ('user', prompt), ('assistant', reply)])
        result = self.match('• ' + self.anchor + '\n• ' + reply + '\n› ' + prompt)
        self.assertEqual(result['messages'][-1]['text'], prompt)
        self.assertNotIn(reply, [m['text'] for m in result['messages']])

    def test_matching_fragments_cannot_be_joined_across_different_dialogue_records(self):
        first = 'checkpoint_record_0123456789abcdef' * 3
        second = 'separate_result_9876543210fedcba' * 3
        # Neither displayed fragment alone is substantial enough to identify a
        # session. Together they are a different, reordered conversation.
        first, second = first[:80], second[:80]
        self.seed('fragments', [('assistant', first + second)])
        capture = '• ' + second + '\n› An unrelated request between the fragments.\n• ' + first
        self.assertEqual(self.match(capture), {})

    def test_one_repeated_fragment_is_not_multiple_independent_matches(self):
        fragment = ('measurement_record_0123456789abcdef' * 3)[:80]
        self.seed('repetition', [('assistant', fragment * 8)])
        capture = '• ' + fragment + '\n• Ran status\n  ' + 'unrelated output ' * 20
        self.assertEqual(self.match(capture), {})

    def test_exited_command_output_is_not_a_reply_for_session_identification(self):
        self.seed('tool-echo', [('assistant', self.anchor)])
        for status in (0, 1, 137):
            self.assertEqual(self.match(f'• Exited (status {status}) check\n  ' + self.anchor), {})

    def test_log_append_and_replacement_do_not_reuse_old_identity_or_unflushed_text(self):
        path = self.seed('first', [('assistant', self.anchor)])
        capture = '• ' + self.anchor
        self.assertEqual(self.match(capture)['binding']['thread_id'], 'first')
        reply = 'The next result is available in the saved trace.'
        record = json.dumps(self.record(1, 'assistant', reply)).encode()
        with path.open('ab') as file:
            file.write(record[:len(record)//2])
        self.assertEqual(self.match(capture + '\n• ' + reply), {})
        with path.open('ab') as file:
            file.write(record[len(record)//2:] + b'\n')
        self.assertEqual(self.match(capture + '\n• ' + reply)['messages'][-1]['text'], reply)
        replacement = self.seed('replacement', self.exchange)
        os.replace(replacement, path)
        self.assertEqual(self.match(capture), {})
        self.assertEqual(self.match(self.render(self.exchange))['binding']['thread_id'], 'replacement')

    def test_new_unmatched_dialogue_does_not_reuse_an_old_session_binding(self):
        old = self.seed('old', [('assistant', self.anchor)])
        self.match('• ' + self.anchor)
        # The new request may be too short to identify its session, or its
        # rollout may not have flushed. Neither permits binding the old chat.
        capture = '• ' + self.anchor + '\n› Check the other task\n• I will check it.'
        self.assertEqual(self.match(capture, hints=[str(old)]), {})

    def test_composer_placeholder_does_not_invalidate_a_verified_reply(self):
        self.seed('current', [('assistant', self.anchor)])
        for placeholder in ('Ask Codex to do anything', 'Write tests for @filename'):
            capture = '• ' + self.anchor + '\n› ' + placeholder + '\n  GPT-6.1-Sol low · /fixture/project · weekly 62% left'
            self.assertEqual(self.match(capture)['binding']['thread_id'], 'current')

    def test_later_clipped_prompt_remains_ambiguous_even_after_an_older_full_match(self):
        prefix = self.anchor[:220]
        self.seed('first', [('user', self.anchor)])
        self.seed('second', [('user', prefix + ' A different continuation with another task and a separate expected result.')])
        capture = '› ' + self.anchor + '\n• Previous task finished.\n› ' + prefix + '…\n• Ran check\n  └ waiting'
        self.assertEqual(self.match(capture), {})

    def test_history_recovers_visible_dialogue_after_a_large_tool_record_and_restart(self):
        path = self.seed('busy', [('assistant', self.anchor)])
        with path.open('a') as file:
            file.write(json.dumps(dict(type='response_item', payload=dict(
                type='function_call_output', call_id='fixture', output='PRIVATE TOOL SENTINEL ' + 'x' * 2_100_000))) + '\n')
        capture = '• ' + self.anchor + '\n• Ran verbose-build\n  └ Output omitted\n› '
        result = self.match(capture, history=True)
        self.assertEqual(result['binding']['thread_id'], 'busy')
        self.assertEqual([m['text'] for m in result['messages']], [self.anchor])
        self.assertNotIn('PRIVATE TOOL SENTINEL', json.dumps(result))
        before = self.reader.metrics['bytes_read']
        self.assertEqual(self.match(capture, history=True), result)
        self.assertEqual(self.reader.metrics['bytes_read'], before, 'Unchanged history must use its append cache')


@unittest.skipUnless(shutil.which('tmux'), 'tmux is required for the preview replay')
class TranscriptPreviewIntegrationTests(ReplayFixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.socket = str(self.root / 'tmux.sock')
        self.env = {key: value for key, value in os.environ.items() if key in ('PATH', 'HOME', 'LANG', 'LC_ALL')}
        self.frame, self.ack = self.root / 'frame.json', self.root / 'ack'
        self.frame.write_text(json.dumps(dict(step=0, text='')))
        self.step = 0
        script = self.root / 'terminal.py'
        script.write_text('''import json, pathlib, sys, time
frame, ack = map(pathlib.Path, sys.argv[1:])
last = None
while True:
    value = frame.read_text()
    if value != last:
        current = json.loads(value)
        sys.stdout.write(current['text'])
        sys.stdout.flush()
        ack.write_text(str(current['step']))
        last = value
    time.sleep(.01)
''')
        subprocess.run(['tmux', '-S', self.socket, '-f', '/dev/null', 'new-session', '-d', '-x', '42', '-y', '24',
                        shlex.join([sys.executable, str(script), str(self.frame), str(self.ack)])],
                       env=self.env, check=True, capture_output=True)
        self.addCleanup(lambda: subprocess.run(['tmux', '-S', self.socket, 'kill-server'], env=self.env, capture_output=True))
        self.connection = dict(id='fixture', label='Fixture', kind='local', socket=self.socket)
        pane = bridge.run(self.connection, ['list-panes', '-F', '#{pane_id}']).strip()
        self.agent.update(pane=pane, live=True, state='running', summary='Working', title='Fixture')
        with patch.object(Hub, 'save_connection'):
            self.hub = Hub(self.root / 'hub')
        self.addCleanup(self.hub.close)
        self.hub.transcripts = self.reader
        self.hub.archive.roots = [('codex', self.root / 'sessions')]
        # Only process discovery is stubbed: capture, transcript parsing,
        # observation, archive binding, cache, and HTTP serialization are real.
        for mocker in (patch.object(self.hub, 'lookup', return_value=(self.connection, self.agent)),
                       patch.object(bridge, 'list_agents', return_value=[self.agent]),
                       patch.object(self.hub.native, 'available', return_value=False)):
            mocker.start();self.addCleanup(mocker.stop)
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.server.hub = self.hub
        self.server.allowed_hosts = {'127.0.0.1'}
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.close_server)

    def close_server(self):
        self.server.shutdown();self.server.server_close();self.thread.join(2)

    def emit(self, text):
        self.step += 1
        temporary = self.frame.with_suffix('.tmp')
        temporary.write_text(json.dumps(dict(step=self.step, text=text + '\n')))
        os.replace(temporary, self.frame)
        deadline = time.monotonic() + 3
        while not self.ack.exists() or self.ack.read_text() != str(self.step):
            self.assertLess(time.monotonic(), deadline, 'Isolated terminal did not display the replay frame')
            time.sleep(.01)

    def preview(self, history=True):
        query = urlencode(dict(agent_id=self.agent['id'], history=int(history), fresh=1))
        with urlopen(f'http://127.0.0.1:{self.server.server_port}/api/preview?{query}', timeout=5) as response:
            self.assertEqual(response.status, 200)
            return json.load(response)

    def test_restored_scrollback_switches_to_live_chat_and_tracks_later_appends_over_http(self):
        old = self.seed('old', [('assistant', self.anchor * 4)])
        with old.open('a') as file:
            file.write(json.dumps(dict(type='event_msg', payload=dict(type='task_complete', turn_id='old-turn', last_agent_message=self.anchor * 4))) + '\n')
        self.emit('• ' + self.anchor * 4)
        before = self.preview()
        self.assertEqual(before['conversation_id'], hashlib.sha256(str(old).encode()).hexdigest()[:32])
        current = self.seed('current', self.exchange)
        with current.open('a') as file:
            file.write(json.dumps(dict(type='event_msg', payload=dict(type='task_started', turn_id='current-turn'))) + '\n')
        self.emit(self.render(self.exchange, width=1000))
        identity = hashlib.sha256(str(current).encode()).hexdigest()[:32]
        for history in (False, True):
            result = self.preview(history)
            self.assertEqual(result['conversation_id'], identity)
            self.assertEqual([m['text'] for m in result['messages']], [text for _, text in self.exchange])
            self.assertEqual(result['observation']['turn_status']['turn_id'], 'current-turn')
            self.assertNotIn('binding', result)
            self.assertNotIn('PRIVATE REASONING SENTINEL', json.dumps(result))
        reply = 'The preview now follows the active conversation correctly.'
        with current.open('a') as file:
            file.write(json.dumps(self.record(2, 'assistant', reply)) + '\n')
        # A tool quoting the next reply must not advance the public history.
        self.emit('• Ran cat saved-result\n  ' + reply)
        self.assertEqual(self.preview()['messages'][-1]['text'], self.exchange[-1][1])
        self.emit('• ' + reply)
        after = self.preview()
        self.assertEqual(after['conversation_id'], identity)
        self.assertEqual(after['messages'][-1]['text'], reply)
