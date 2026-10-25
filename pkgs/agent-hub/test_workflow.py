"""Durable handoffs and bounded supervisor context; no real agent commands."""
import copy
import json
import tempfile
import unittest
from unittest.mock import Mock, patch

from bridge import BridgeError
from server import Hub
from workflow import Handoffs, action_requested, request_scope, display_title, mark_patch


class HandoffTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        with patch.object(Hub, 'save_connection'):
            self.hub = Hub(self.temp.name)
        self.now = 1000
        self.queue = Handoffs(self.hub, clock=lambda: self.now)
        self.agent = dict(id='host-a:agent', state='done', title='RTC baseline', cwd='/research/rtc',
                          connection_label='host-a', connection_online=True, live=True,
                          changed_at=900, acknowledged=False, activity_confirmed=False, mark={})

    def tearDown(self):
        self.hub.close()
        self.temp.cleanup()

    def item(self):
        return self.queue.snapshot([self.agent])['items'][0]

    def test_only_settled_handoffs_are_eligible(self):
        for change in [dict(state='running'), dict(state='idle'), dict(activity_confirmed=True),
                       dict(connection_online=False), dict(acknowledged=True), dict(changed_at=997),
                       dict(state='blocked',recovery={'kind':'capacity','status':'retrying'})]:
            with self.subTest(change=change):
                self.assertEqual(self.queue.snapshot([{**self.agent, **change}])['items'], [])
        self.assertEqual(self.item()['kind'], 'done')
        self.assertEqual(self.queue.snapshot([{**self.agent,'runtime':'codex','completion_confirmed':False}])['items'], [])
        self.assertEqual(self.queue.snapshot([{**self.agent,'runtime':'codex','completion_confirmed':True}])['items'][0]['kind'], 'done')

    def test_later_survives_restart_and_new_turn_is_distinct(self):
        first = self.item()
        self.queue.update(dict(action='later', id=first['id'], until=1100), [self.agent])
        restored = Handoffs(self.hub, clock=lambda: self.now)
        self.assertEqual(restored.snapshot([self.agent])['pending'], 0)
        self.assertEqual(restored.snapshot([self.agent])['items'][0]['deferred_until'], 1100)
        self.now = 1101
        self.assertEqual(restored.snapshot([self.agent])['pending'], 1)
        self.agent['changed_at'] = 1090
        newer = self.item()
        self.assertNotEqual(first['id'], newer['id'])
        self.assertEqual(newer['deferred_until'], 0)
        with self.assertRaises(BridgeError):
            self.queue.update(dict(action='handled', id=first['id']), [self.agent])

    def test_seen_does_not_handle_and_restore_does_not_send(self):
        key = self.item()['id']
        with patch.object(self.hub, 'action') as action:
            self.queue.update(dict(action='seen', id=key), [self.agent])
            self.queue.update(dict(action='later', id=key), [self.agent])
            self.queue.update(dict(action='restore', id=key), [self.agent])
            self.assertEqual(self.item()['seen'], self.now)
            self.assertEqual(self.item()['deferred_until'], 0)
            action.assert_not_called()

    def test_done_waits_for_watch_output_to_settle(self):
        self.agent['activity_fingerprint'] = 'current-output'
        def observe(digest, since):
            with self.hub.db() as db:
                db.execute('INSERT OR REPLACE INTO watch_observations VALUES(?,?)',
                           ('agent:' + self.agent['id'], json.dumps(dict(candidate='done', since=since, digest=digest))))
        observe('old-output', 900)
        self.assertEqual(self.queue.snapshot([self.agent])['pending'], 0)
        observe('current-output', 998)
        self.assertEqual(self.queue.snapshot([self.agent])['pending'], 0)
        self.now = 1005
        self.assertEqual(self.queue.snapshot([self.agent])['pending'], 1)

    def test_proven_completion_can_be_seen_before_phone_push(self):
        self.agent.update(runtime='codex',completion_confirmed=True,changed_at=999,activity_fingerprint='final-output')
        with self.hub.db() as db:
            db.execute('INSERT OR REPLACE INTO watch_observations VALUES(?,?)',
                       ('agent:'+self.agent['id'],json.dumps(dict(candidate='done',since=999.5,digest='final-output'))))
        self.assertEqual(self.queue.snapshot([self.agent])['pending'],0)
        self.now=1000.5
        self.assertEqual(self.queue.snapshot([self.agent])['pending'],1)
        self.agent['activity_fingerprint']='new-output'
        self.assertEqual(self.queue.snapshot([self.agent])['pending'],0)

    def test_review_is_correlated_and_failure_preserves_pending(self):
        item = self.item()
        with patch.object(self.hub, 'action', side_effect=BridgeError('State changed')):
            with self.assertRaises(BridgeError):
                self.queue.update(dict(action='handled', id=item['id']), [self.agent])
        self.assertEqual(self.queue.snapshot([self.agent])['pending'], 1)
        with patch.object(self.hub, 'action') as action:
            self.queue.update(dict(action='handled', id=item['id']), [self.agent])
            action.assert_called_once_with(dict(type='acknowledge', agent_id=self.agent['id'], expected_state='done:900'))
        self.assertEqual(self.queue.snapshot([self.agent])['pending'], 0)

    def test_reply_next_handles_only_the_captured_turn_after_delivery(self):
        item = self.item()
        value = dict(id=item['id'], request=dict(type='send', agent_id=self.agent['id'], message='Run the next comparison',
            command_id='web-fixture-reply-next', expected_state=item['version']))
        def delivered(request):
            self.assertEqual(request, value['request'])
            self.agent['changed_at'] = 950
            return {'ok': True, 'message': 'Delivered'}
        with patch.object(self.hub, 'snapshot', side_effect=lambda: {'agents': [self.agent]}), patch.object(self.hub, 'action', side_effect=delivered) as action:
            result = self.queue.reply(value)
            self.assertEqual(result['handled_handoff'], item['id'])
            self.assertEqual(self.queue.reply(value), result)
            action.assert_called_once()
        # The newer completed turn remains in the queue, and no acknowledge
        # command could accidentally mark its current state reviewed.
        self.assertEqual(self.queue.snapshot([self.agent])['pending'], 1)
        self.assertNotEqual(self.item()['id'], item['id'])

    def test_reply_next_failure_or_stale_turn_keeps_review_pending(self):
        item = self.item()
        value = dict(id=item['id'], request=dict(type='send', agent_id=self.agent['id'], message='Run another comparison',
            command_id='web-fixture-reply-fail', expected_state=item['version']))
        with patch.object(self.hub, 'snapshot', return_value={'agents': [self.agent]}), patch.object(self.hub, 'action', side_effect=BridgeError('Offline')) as action:
            with self.assertRaisesRegex(BridgeError, 'Offline'):
                self.queue.reply(value)
            self.assertEqual(self.item()['id'], item['id'])
            with self.assertRaisesRegex(BridgeError, 'uncertain or failed'):
                self.queue.reply(value)
            action.assert_called_once()
        self.agent['changed_at'] = 950
        value['request']['command_id'] = 'web-fixture-stale-reply'
        with patch.object(self.hub, 'snapshot', return_value={'agents': [self.agent]}), patch.object(self.hub, 'action') as action:
            with self.assertRaisesRegex(BridgeError, 'handoff changed'):
                self.queue.reply(value)
            action.assert_not_called()

    def test_priority_is_explicit_and_expires(self):
        other = {**self.agent, 'id': 'host-b:agent', 'state': 'waiting', 'title': 'Deployment', 'cwd': '/app'}
        self.assertEqual(self.queue.snapshot([self.agent, other])['items'][0]['agent_id'], other['id'])
        self.queue.update(dict(action='priority', text='RTC', until=1100), [])
        self.assertEqual(self.queue.snapshot([self.agent, other])['items'][0]['agent_id'], self.agent['id'])
        self.now = 1101
        self.assertEqual(self.queue.priority(), {})

    def test_undo_later_is_durable_once_only_and_does_not_touch_newer_work(self):
        result = self.queue.update(dict(action='later', id=self.item()['id'], until=1100), [self.agent])
        restored = Handoffs(self.hub, clock=lambda: self.now)
        with patch.object(self.hub, 'action') as action:
            restored.update(dict(action='undo', token=result['undo']), [self.agent])
            action.assert_not_called()
        self.assertEqual(self.item()['deferred_until'], 0)
        with self.assertRaises(BridgeError):
            restored.update(dict(action='undo', token=result['undo']), [self.agent])
        result = self.queue.update(dict(action='later', id=self.item()['id'], until=1100), [self.agent])
        self.agent['changed_at'] = 950
        with self.assertRaises(BridgeError):
            self.queue.update(dict(action='undo', token=result['undo']), [self.agent])
        self.assertEqual(self.item()['deferred_until'], 0)

    def test_undo_review_restores_only_the_same_turn_and_expires(self):
        with patch.object(self.hub, 'action') as action:
            result = self.queue.update(dict(action='handled', id=self.item()['id']), [self.agent])
            self.assertEqual(self.queue.snapshot([self.agent])['pending'], 0)
            self.queue.update(dict(action='undo', token=result['undo']), [self.agent])
            action.assert_called_with(dict(type='unacknowledge', agent_id=self.agent['id'], expected_state='done:900'))
            self.assertEqual(self.queue.snapshot([self.agent])['pending'], 1)
            result = self.queue.update(dict(action='handled', id=self.item()['id']), [self.agent])
            self.now += 61
            with self.assertRaises(BridgeError):
                self.queue.update(dict(action='undo', token=result['undo']), [self.agent])

    def test_task_labels_are_explicit_metadata_and_searchable(self):
        agent = {**self.agent, 'title': 'we-teleop-rewrite-0728-compact-2'}
        self.assertEqual(display_title(agent), 'Teleop rewrite · 2')
        patch = mark_patch(agent, dict(type='label', label='Teleop · IK tuning'), {})
        named = {**agent, 'mark': patch, 'display_title': 'Teleop · IK tuning'}
        self.assertEqual(display_title(named), 'Teleop · IK tuning')
        self.assertEqual(request_scope('Teleop · IK tuning result?', [named], ''), ([named], False))
        for label in ['x'*49, 'two\nlines', None]:
            with self.assertRaises(BridgeError):
                mark_patch(agent, dict(type='label', label=label), {})
        with self.assertRaises(BridgeError):
            mark_patch(agent, dict(type='unacknowledge'), {'ack_state': 'done:800'})

    def test_invalid_defer_values_are_rejected(self):
        for until in [float('nan'), float('inf'), True, 900, 4000000, 'tomorrow']:
            with self.subTest(until=until), self.assertRaises(BridgeError):
                self.queue.update(dict(action='later', id=self.item()['id'], until=until), [self.agent])

    def test_question_does_not_authorize_work(self):
        for query in ['What should I do next?', 'Should I pause RTC?', 'Brief me across all hosts', 'Tell me what changed']:
            self.assertFalse(action_requested(query))
        for query in ['Pause RTC', 'Tell RTC to run the baseline', 'Please send this to RTC: compare the traces', 'Can you pause this agent?']:
            self.assertTrue(action_requested(query))

    def test_history_is_scoped_until_user_requests_broader_context(self):
        agents = [self.agent, {**self.agent, 'id': 'second', 'title': 'Policy evaluation'}]
        self.assertEqual(request_scope('What changed?', agents, self.agent['id']), ([self.agent], False))
        self.assertEqual(request_scope('Brief me across all hosts', agents, self.agent['id']), (agents, True))
        self.assertEqual(request_scope('Policy evaluation result?', agents, ''), ([agents[1]], False))
        self.assertEqual(request_scope('Hello', agents, ''), ([], False))
        self.assertEqual(request_scope('Which agent is preparing the stdmean vs quantile ckpts by nemo?', agents, self.agent['id']), (agents, False))
        self.assertEqual(request_scope('Find the agent testing quantile', agents, ''), (agents, False))
        self.assertEqual(request_scope('Who is working on Nemo?', agents, self.agent['id']), (agents, False))
        self.assertEqual(request_scope('Tell Policy evaluation: which agent is using quantile?', agents, self.agent['id']), ([agents[1]], False))

    def test_overview_retains_attention_beyond_the_first_twelve_windows(self):
        agents = [dict(self.agent, id='host:window'+str(i), needs_attention=False) for i in range(14)]
        agents[-1].update(needs_attention=True, acknowledged=False)
        agents[0].update(needs_attention=True, acknowledged=True)
        selected, broad = request_scope('Brief me across all hosts', agents, '')
        self.assertTrue(broad)
        self.assertEqual(len(selected), 12)
        self.assertEqual(selected[0]['id'], 'host:window13')
        self.assertEqual([a['id'] for a in agents], ['host:window'+str(i) for i in range(14)])

    def test_messages_preserve_their_task_context(self):
        self.hub.message('user', 'Check this result', 'request-a', self.agent['id'])
        self.hub.message('assistant', 'The result is ready', 'request-a', self.agent['id'])
        messages = self.hub.snapshot()['messages']
        self.assertTrue(all(m['context_agent_id'] == self.agent['id'] for m in messages))
