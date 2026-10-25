"""Supervisor queue requests and Codex terminal input, without real agent commands."""
import json
import tempfile
import unittest
from unittest.mock import Mock, patch

import bridge
from server import Hub
from workflow import action_requested, agent_lookup_requested
from native import NativeError, NativeInputUnavailable


class QueueTests(unittest.TestCase):
    def setUp(self):
        self.connection = {'id': 'local', 'kind': 'local', 'label': 'razy'}
        self.agent = {'id': 'local:codex', 'pane': '%1', 'runtime': 'codex', 'live': True,
                      'state': 'running', 'summary': 'Working', 'cursor_x': '2', 'cursor_y': '1', 'in_mode': '0'}
        self.screen = '• Working (2s • esc to interrupt)\n› Ask Codex to do anything\n'

    def test_queue_intent_is_an_action_but_questions_and_negation_are_not(self):
        for request in ('Queue for Codex: run the comparison', 'Supervisor, can you please queue this: continue',
                        'I want to queue the next comparison', 'Enqueue the follow-up', 'Add this to the Codex queue: test it'):
            self.assertTrue(action_requested(request), request)
            self.assertFalse(agent_lookup_requested(request), request)
        for request in ('What is queued?', 'Do not queue anything', 'Can you explain the queue?',
                        'Tell me which agent has queued work', 'The agent said "queue this"'):
            self.assertFalse(action_requested(request), request)

    def test_queue_uses_tab_and_literal_paste_without_interrupting(self):
        instruction = 'Compare mean and quantile.\nKeep both checkpoints.'
        with patch.object(bridge, 'verify_agent', return_value=self.agent), patch.object(bridge, 'run', return_value=self.screen) as run:
            self.assertEqual(bridge.queue(self.connection, self.agent, instruction), 'queued')
            self.assertEqual(run.call_args.args[1][-4:], ['send-keys', '-t', '%1', 'Tab'])
            self.assertEqual(run.call_args.kwargs['stdin'], instruction)
            self.assertEqual(run.call_count, 2)

    def test_idle_codex_submits_with_the_same_noninterrupting_key(self):
        with patch.object(bridge, 'verify_agent', return_value=self.agent), patch.object(bridge, 'run', return_value='Done\n›\n') as run:
            self.assertEqual(bridge.queue(self.connection, self.agent, 'Continue the study'), 'sent')
            self.assertEqual(run.call_args.args[1][-1], 'Tab')

    def test_queue_keeps_drafts_copy_mode_and_approval_dialogs_intact(self):
        cases = [({**self.agent, 'cursor_x': '9'}, '• Working (2s • esc to interrupt)\n› my draft'),
                 ({**self.agent, 'in_mode': '1'}, self.screen),
                 (self.agent, 'Would you like to run the following command?\n› 1. Yes, proceed'),
                 ({**self.agent, 'runtime': 'claude'}, self.screen)]
        for current, screen in cases:
            with self.subTest(current=current, screen=screen), patch.object(bridge, 'verify_agent', return_value=current), patch.object(bridge, 'run', return_value=screen), patch.object(bridge, 'paste') as paste:
                with self.assertRaises(bridge.BridgeError):
                    bridge.queue(self.connection, self.agent, 'Continue')
                paste.assert_not_called()

    def test_queue_checks_identity_and_rejects_terminal_commands(self):
        with patch.object(bridge, 'verify_agent', side_effect=bridge.BridgeError('Agent changed')), patch.object(bridge, 'paste') as paste:
            with self.assertRaises(bridge.BridgeError):
                bridge.queue(self.connection, self.agent, 'Continue')
            paste.assert_not_called()
        for instruction in ('', None, 'x' * 12001, '/quit', '!whoami', '\x00'):
            with patch.object(bridge, 'verify_agent', return_value=self.agent), patch.object(bridge, 'paste') as paste:
                with self.assertRaises(bridge.BridgeError):
                    bridge.queue(self.connection, self.agent, instruction)
                paste.assert_not_called()

    def test_supervisor_can_queue_exact_user_instruction_but_cannot_invent_work(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(Hub, 'save_connection'):
            hub = Hub(directory)
            agent = {**self.agent, 'title': 'LAP study', 'connection_id': 'local', 'connection_label': 'razy',
                     'cwd': '/tmp/lap', 'summary': 'Working', 'connection_online': True,
                     'needs_attention': False, 'acknowledged': False, 'mark': {}}
            hub.snapshot = Mock(return_value={'agents': [agent], 'connections': [], 'messages': [], 'handoffs': {}})
            hub.preview = Mock(return_value={'messages': [], 'text': 'Working'})
            query = 'Queue for LAP study: Compare the two checkpoints.'
            for index, instruction in enumerate(('Compare the two checkpoints.', 'Run an unrequested experiment.')):
                action = {'type': 'queue', 'agent_id': agent['id'], 'connection_id': '', 'message': instruction, 'cwd': '', 'runtime': '', 'quote': query, 'instruction_source': 'current'}
                hub.jobs[str(index)] = {'request_id': str(index)}
                process = Mock(returncode=0)
                process.communicate.return_value = (json.dumps({'reply': 'I’ll queue that for LAP.', 'request_intent': 'coordinate', 'actions': [action], 'select_agent_id': '', 'agent_choices': []}), '')
                try:
                    with patch('server.shutil.which', return_value='/bin/codex'), patch('server.subprocess.Popen', return_value=process), patch.object(hub, 'action', return_value={'message': 'Queued for LAP study on razy'}) as execute:
                        hub.supervise(str(index), query, agent['id'], True)
                    self.assertEqual(hub.jobs[str(index)]['status'], 'complete')
                    self.assertIn('"actions_enabled": true', process.communicate.call_args.args[0])
                    if index == 0:
                        execute.assert_called_once_with({**action, 'command_id': 'supervisor_0_0'})
                        self.assertIn('queued that for LAP', hub.jobs[str(index)]['reply'])
                    else:
                        execute.assert_not_called()
                        self.assertIn('Instruction not sent', hub.jobs[str(index)]['reply'])
                finally:
                    hub.close()

    def test_native_queue_edit_is_bound_to_the_current_pane_and_deduplicated(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(Hub,'save_connection'):
            hub=Hub(directory)
            binding={'thread_id':'01234567-1234-1234-1234-0123456789ab','path':'/fixture/session.jsonl','cwd':'/fixture'}
            agent={**self.agent,'title':'Fixture','cwd':'/fixture'}
            action={'type':'queue_update','agent_id':agent['id'],'thread_id':binding['thread_id'],
                    'queue_id':'queued-fixture','queue_digest':'a'*64,'message':'Revised fixture instruction','command_id':'native_edit_fixture_1'}
            try:
                with patch.object(hub,'lookup',return_value=(self.connection,agent)), patch.object(bridge,'verify_agent',return_value=agent), \
                        patch.object(bridge,'capture',return_value='Fixture terminal'), patch.object(hub,'session_hints',return_value=[]), \
                        patch.object(hub.transcripts,'match',return_value={'binding':binding}) as match, patch.object(hub.native,'change_queue') as change, \
                        patch.object(hub.native,'queue',return_value={'items':[]}):
                    hub.action(action);hub.action(action)
                    change.assert_called_once_with(binding,'queued-fixture','a'*64,'Revised fixture instruction')
                    self.assertTrue(match.call_args.kwargs['binding'])
                    match.return_value={}
                    with self.assertRaisesRegex(bridge.BridgeError,'pane changed'):
                        hub.action({**action,'command_id':'native_edit_fixture_2'})
                    self.assertEqual(change.call_count,1)
            finally:hub.close()

    def test_web_queue_uses_exact_native_thread_and_only_falls_back_before_writing(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(Hub,'save_connection'):
            hub = Hub(directory)
            self.addCleanup(hub.close)
            agent = {**self.agent, 'cwd':'/fixture', 'title':'Fixture'}
            binding = {'thread_id':'01234567-1234-1234-1234-0123456789ab','cwd':'/fixture','path':'/fixture/session.jsonl'}
            request = dict(type='queue',agent_id=agent['id'],message='Run the next evaluation.',command_id='queue_native_fixture_1')
            with patch.object(hub,'lookup',return_value=(self.connection,agent)), patch.object(bridge,'verify_agent',return_value=agent), \
                 patch.object(bridge,'capture',return_value='Fixture terminal'), patch.object(hub,'session_hints',return_value=[]), \
                 patch.object(hub.transcripts,'match',return_value={'binding':binding}), \
                 patch.object(hub.native,'send_input',return_value='queued') as send, patch.object(bridge,'queue',return_value='queued') as fallback:
                result = hub.action(request)
                self.assertEqual(result['delivery'], 'queued')
                self.assertEqual(hub.action(request), result)
                send.assert_called_once_with(binding,request['message'],[],request['command_id'],queued=True)
                fallback.assert_not_called()
                send.side_effect = NativeError('Write uncertain')
                with self.assertRaisesRegex(NativeError,'uncertain'):hub.action({**request,'command_id':'queue_native_fixture_2'})
                fallback.assert_not_called()
                send.side_effect = NativeInputUnavailable('No verified native connection')
                hub.action({**request,'command_id':'queue_native_fixture_3'})
                fallback.assert_called_once_with(self.connection,agent,request['message'])
                send.reset_mock();send.side_effect=None;fallback.reset_mock()
                with patch('server.terminal_queue.snapshot',return_value={'items':[{'text':'Earlier terminal input'}]}):
                    hub.action({**request,'command_id':'queue_native_fixture_4'})
                send.assert_not_called()
                fallback.assert_called_once_with(self.connection,agent,request['message'])

    def test_terminal_queue_edit_uses_its_own_verified_queue_without_an_api_thread(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(Hub,'save_connection'):
            hub=Hub(directory)
            action={'type':'queue_update','queue_backend':'terminal','agent_id':self.agent['id'],
                    'queue_id':'terminal-item','queue_digest':'a'*64,'message':'Revised instruction',
                    'command_id':'terminal_edit_fixture_1'}
            try:
                with patch.object(hub,'lookup',return_value=(self.connection,self.agent)), patch.object(bridge,'verify_agent',return_value=self.agent), \
                        patch.object(bridge,'capture',return_value=''), \
                        patch('server.terminal_queue.change') as change, patch.object(hub.native,'change_queue') as native:
                    hub.action(action);hub.action(action)
                    change.assert_called_once_with(self.connection,self.agent,'terminal-item','a'*64,'Revised instruction')
                    native.assert_not_called()
            finally:hub.close()
