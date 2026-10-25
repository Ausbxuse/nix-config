import json
from pathlib import Path
import tempfile
import unittest
import uuid
from unittest.mock import patch

from claude_sessions import ClaudeSessions
from transcripts import Transcripts


class ClaudeSessionTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.config, self.proc, self.cwd = self.root / '.claude', self.root / 'proc', self.root / 'project'
        self.cwd.mkdir()
        self.agent = {'runtime': 'claude', 'live': True, 'foreground_pid': '123', 'pid': '100',
                      'cwd': str(self.cwd), 'window': '@1', 'pane': '%2'}
        self.connection = {'kind': 'local', 'socket': str(self.root / 'tmux-socket')}
        self.registry = self.config / 'sessions' / '123.json'
        self.registry.parent.mkdir(parents=True)
        self.session = '01234567-1234-1234-1234-0123456789ab'
        self.record = {'pid': 123, 'sessionId': self.session, 'procStart': '456', 'cwd': str(self.cwd),
                       'tmux': 'main:@1.%2', 'kind': 'interactive', 'status': 'busy'}
        self.write_registry()
        for pid in [100, 123]:
            path = self.proc / str(pid)
            path.mkdir(parents=True)
            fields = ['S', '100', '123', '100', '99', '123'] + ['0'] * 13 + ['456']
            (path / 'stat').write_text(f'{pid} (claude (test)) ' + ' '.join(fields))
            (path / 'environ').write_bytes(b'TMUX=' + self.connection['socket'].encode() + b',50,0\0TMUX_PANE=%2\0')
            (path / 'cwd').symlink_to(self.cwd, target_is_directory=True)
        project = ''.join(c if c.isascii() and (c.isalnum() or c == '-') else '-' for c in str(self.cwd))
        self.transcript = self.config / 'projects' / project / f'{self.session}.jsonl'
        self.transcript.parent.mkdir(parents=True)
        self.transcript.write_text('')
        self.sessions = ClaudeSessions(self.config, self.proc)
        self.reader = Transcripts(self.root / 'codex', self.config / 'projects')
        self.reader.claude_sessions = self.sessions

    def write_registry(self):
        self.registry.write_text(json.dumps(self.record))

    def record_message(self, role, text, **fields):
        message = {'role': role, 'content': [{'type': 'text', 'text': text}]}
        if 'stop_reason' in fields:
            message['stop_reason'] = fields.pop('stop_reason')
        record = {'type': role, 'uuid': str(uuid.uuid4()), 'cwd': str(self.cwd),
                  'timestamp': '2026-10-13T15:25:00Z', 'message': message, **fields}
        with self.transcript.open('a') as stream:
            stream.write(json.dumps(record) + '\n')
        return record['uuid']

    def test_same_text_in_rapid_turns_has_distinct_native_identity(self):
        terminal = dict(state='done', summary='Completed', activity_confirmed=False, completion_confirmed=True)
        first_user = self.record_message('user', 'Repeat the study.')
        self.record_message('assistant', 'It is ready.', stop_reason='end_turn')
        first = self.reader.observe_claude(self.agent, self.connection, terminal)
        self.assertEqual(first['turn_status']['turn_id'], 'claude:' + first_user)
        self.assertEqual(first['state'], 'done')
        second_user = self.record_message('user', 'Repeat the study.')
        self.record_message('assistant', 'It is ready.', stop_reason='end_turn')
        second = self.reader.observe_claude(self.agent, self.connection, terminal)
        self.assertEqual(second['state'], 'done')
        self.assertEqual(second['turn_status']['turn_id'], 'claude:' + second_user)
        self.assertNotEqual(first['turn_status']['turn_id'], second['turn_status']['turn_id'])
        self.assertEqual(self.reader.observe_claude(self.agent, self.connection, terminal), second)

    def test_new_user_and_native_tool_progress_override_old_completion_without_private_content(self):
        terminal = dict(state='done', summary='Old footer', activity_confirmed=False, completion_confirmed=True)
        self.record_message('user', 'First study.')
        self.record_message('assistant', 'First result.', stop_reason='end_turn')
        self.assertEqual(self.reader.observe_claude(self.agent, self.connection, terminal)['state'], 'done')
        identity = self.record_message('user', 'Next study.')
        def observe():
            current = self.reader.observe_claude(self.agent, self.connection, terminal)
            self.assertEqual(current['turn_status']['turn_id'], 'claude:' + identity)
            self.assertEqual(current['state'], 'running')
            self.assertFalse(current['completion_confirmed'])
            self.assertNotIn('PRIVATE', str(current))
        observe()
        for role, block in [('assistant', dict(type='tool_use', name='Read', input={'file_path':'PRIVATE FILE'})),
                            ('user', dict(type='tool_result', content='PRIVATE RESULT')),
                            ('assistant', dict(type='thinking', thinking='PRIVATE REASONING'))]:
            self.record_message(role, '', message=dict(role=role, content=[block], stop_reason='tool_use' if role == 'assistant' else None))
            observe()
        self.record_message('assistant', 'Next result.', stop_reason='end_turn')
        finished = self.reader.observe_claude(self.agent, self.connection, terminal)
        self.assertEqual(finished['state'], 'done')
        self.assertTrue(finished['completion_confirmed'])
        self.assertEqual(finished['turn_status']['turn_id'], 'claude:' + identity)
        self.assertNotIn('PRIVATE', str(self.reader.match(self.agent, '', connection=self.connection)))

    def test_native_final_never_creates_completion_or_overrides_visible_activity_or_approval(self):
        self.record_message('user', 'A study.')
        self.record_message('assistant', 'A final result.', stop_reason='end_turn')
        for state in ('idle', 'unknown', 'running', 'waiting', 'blocked'):
            terminal = dict(state=state, summary='Current terminal', completion_confirmed=False, activity_confirmed=state == 'running')
            observed = self.reader.observe_claude(self.agent, self.connection, terminal)
            self.assertEqual(observed['state'], state)
            self.assertFalse(observed['completion_confirmed'])
        self.record_message('assistant', 'More text without a native stop reason.')
        completed = self.reader.observe_claude(self.agent, self.connection, dict(state='done', completion_confirmed=True))
        self.assertEqual(completed['state'], 'done')
        self.assertTrue(completed['completion_confirmed'])
        unknown = self.reader.observe_claude(self.agent, self.connection, dict(state='unknown', completion_confirmed=False))
        self.assertEqual(unknown['state'], 'unknown')
        self.assertFalse(unknown['completion_confirmed'])

    def test_meta_commands_and_sidechain_users_do_not_change_current_turn(self):
        identity = self.record_message('user', 'The study.')
        self.record_message('assistant', 'The result.', stop_reason='end_turn')
        for text, fields in [('<local-command-caveat>Internal caveat.</local-command-caveat>', {}),
                             ('<command-name>/status</command-name>', {}),
                             ('<task-notification><summary>A background task finished.</summary></task-notification>', {}),
                             ('Internal instruction.', {'isMeta':True}),
                             ('A sidechain instruction.', {'isSidechain':True})]:
            self.record_message('user', text, **fields)
        observed = self.reader.observe_claude(self.agent, self.connection, dict(state='done', completion_confirmed=True))
        self.assertEqual(observed['state'], 'done')
        self.assertEqual(observed['turn_status']['turn_id'], 'claude:' + identity)

    def test_observation_reuses_the_preview_cache_and_checks_process_after_read(self):
        from server import Hub
        self.record_message('user', 'A study.')
        self.record_message('assistant', 'The result.', stop_reason='end_turn')
        self.reader.match(self.agent, '', connection=self.connection, history=True)
        before = self.reader.metrics.copy()
        hub = Hub.__new__(Hub)
        hub.transcripts = self.reader
        agent = {**self.agent, 'state':'running', 'summary':'Working'}
        capture = '● The result.\n✻ Brewed for 12s\n────────────────────\n❯ \n────────────────────'
        result = hub.agent_observation(self.connection, agent, '', capture, history=True)
        self.assertTrue(result['completion_confirmed'])
        self.assertEqual(result['turn_status']['source'], 'claude')
        self.assertEqual(self.reader.metrics['bytes_read'], before['bytes_read'])
        self.assertGreater(self.reader.metrics['cache_hits'], before['cache_hits'])
        terminal = dict(state='unknown', completion_confirmed=False)
        identity = self.sessions.binding(self.agent, self.connection)
        with patch.object(self.sessions, 'binding', side_effect=[identity, None]):
            self.assertEqual(self.reader.observe_claude(self.agent, self.connection, terminal), terminal)
        self.registry.write_text('{')
        self.assertEqual(self.reader.observe_claude(self.agent, self.connection, terminal), terminal)

    def test_exact_process_binding_does_not_need_terminal_content(self):
        self.record_message('user', 'Compare the traces.')
        self.record_message('assistant', 'Both are saved.')
        result = self.reader.match(self.agent, '', connection=self.connection, history=True, binding=True)
        self.assertTrue(result['session_verified'])
        self.assertEqual([m['text'] for m in result['messages']], ['Compare the traces.', 'Both are saved.'])
        self.assertEqual(result['archive_source'], str(self.transcript))
        self.assertNotIn('binding', result)  # Never supply a Codex control binding.

    def test_original_roles_timestamps_and_new_messages_ignore_terminal_ui(self):
        self.record_message('user', 'Check the plot.')
        self.record_message('assistant', 'It is ready.')
        capture = '❯ Check the plot.  (press up to edit message)\n● TaskOutput(noise)\n✻ Working for 10s'
        first = self.reader.match(self.agent, capture, connection=self.connection, history=True)
        self.record_message('assistant', 'Here is the saved image: ![Plot](plot.png)')
        second = self.reader.match(self.agent, capture, connection=self.connection, history=True)
        self.assertEqual([m['role'] for m in second['messages']], ['user', 'assistant', 'assistant'])
        self.assertEqual(second['messages'][:2], first['messages'])
        self.assertIn('plot.png', second['messages'][-1]['text'])
        self.assertNotIn('press up', str(second)); self.assertNotIn('TaskOutput', str(second))
        self.assertEqual(second['messages'][0]['created'], '2026-10-13T15:25:00Z')

    def test_empty_verified_session_does_not_fall_back_to_shell_history(self):
        result = self.reader.match(self.agent, '❯ old shell command', connection=self.connection)
        self.assertTrue(result['session_verified'])
        self.assertEqual(result['messages'], [])

    def test_stale_or_mismatched_registry_is_rejected(self):
        for key, value in [('pid', 124), ('procStart', 'old'), ('sessionId', '../../other'),
                           ('cwd', '/other'), ('tmux', 'main:@1.%3'), ('kind', 'sdk')]:
            with self.subTest(key=key):
                original = self.record[key]; self.record[key] = value; self.write_registry()
                self.assertIsNone(self.sessions.binding(self.agent, self.connection))
                self.record[key] = original
        self.write_registry()
        self.assertIsNone(self.sessions.binding({**self.agent, 'live': False}, self.connection))
        self.assertIsNone(self.sessions.binding(self.agent, {**self.connection, 'kind': 'ssh'}))
        self.assertIsNone(self.sessions.binding(self.agent, {**self.connection, 'socket': '/another-server'}))

    def test_foreground_process_and_terminal_must_still_belong_to_this_pane(self):
        stat = self.proc / '100/stat'
        original = stat.read_text()
        stat.write_text(original.replace('99 123 ', '99 321 '))
        self.assertIsNone(self.sessions.binding(self.agent, self.connection))
        stat.write_text(original)
        environment = self.proc / '123/environ'
        environment.write_bytes(environment.read_bytes().replace(b'%2', b'%3'))
        self.assertIsNone(self.sessions.binding(self.agent, self.connection))

    def test_symlinks_and_malformed_registry_cannot_select_a_transcript(self):
        original = self.registry.read_text()
        self.registry.write_text('{')
        self.assertIsNone(self.sessions.binding(self.agent, self.connection))
        self.registry.write_text(original)
        self.transcript.unlink(); self.transcript.symlink_to(self.registry)
        self.assertIsNone(self.sessions.binding(self.agent, self.connection))

    def test_session_switch_during_read_does_not_return_the_old_session(self):
        self.record_message('assistant', 'Old session.')
        identity = self.sessions.binding(self.agent, self.connection)
        with patch.object(self.sessions, 'binding', side_effect=[identity, None]):
            self.assertEqual(self.reader.match(self.agent, '', connection=self.connection), {})

    def test_structured_tool_payloads_and_reasoning_stay_out_of_messages(self):
        records = [
            {'type': 'user', 'message': {'role': 'user', 'content': [{'type': 'tool_result', 'content': 'PRIVATE TOOL PAYLOAD'}]}},
            {'type': 'assistant', 'message': {'role': 'assistant', 'content': [{'type': 'thinking', 'thinking': 'PRIVATE REASONING'}]}},
            {'type': 'assistant', 'message': {'role': 'assistant', 'content': [
                {'type': 'text', 'text': 'Checking the file.'}, {'type': 'tool_use', 'name': 'Read', 'input': {'file_path': 'PRIVATE PATH'}}]}},
        ]
        self.transcript.write_text(''.join(json.dumps({**row, 'cwd': str(self.cwd)}) + '\n' for row in records))
        result = self.reader.match(self.agent, '', connection=self.connection)
        self.assertEqual([(m['text'], m['phase']) for m in result['messages']], [('Checking the file.', 'commentary')])
        self.assertNotIn('PRIVATE', str(result))


if __name__ == '__main__':
    unittest.main()
