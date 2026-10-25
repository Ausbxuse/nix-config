"""Commitments, source validity, private ownership, and interruption policy."""
import copy
from contextlib import contextmanager
import json
from pathlib import Path
import sqlite3
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

from assistant import Assistant, requested, report_response
from bridge import BridgeError
from delivery import Deliveries
from notifications import Watcher


class FakeHub:
    mesh = None
    supervisor_busy = False
    provider = 'codex'
    provider_executable = ''
    supervisor_model = 'gpt-6.1-sol'
    def __init__(self, directory):
        self.state_dir = Path(directory)
        self.live = Mock()
        self.stop = threading.Event()
        self.lock = threading.RLock()
        self.processes = set()
        self.preview = Mock()
        self.snapshot = Mock()
    @contextmanager
    def db(self):
        db = sqlite3.connect(self.state_dir / 'test.sqlite')
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()


class AssistantTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.now = 1000
        self.hub = FakeHub(self.tmp.name)
        self.hub.deliveries = Deliveries(self.hub)
        self.hub.watcher = Watcher(self.hub, clock=lambda: self.now)
        self.assistant = self.hub.assistant = Assistant(self.hub, clock=lambda: self.now)
        self.agent = dict(id='gpu:%1:99:@2', title='NeMo comparison', connection_id='gpu', connection_label='gpu-004',
                          pane='%1', session='main', state='done', changed_at=1001, live=True, connection_online=True)
        self.old = dict(id='user-old', role='user', text='Compare the stdmean and quantile checkpoints.')
        self.reply = dict(id='reply-old', role='assistant', phase='final_answer', text='The quantile checkpoint scored 0.4. The stdmean baseline scored 0.3.')
        self.preview = dict(source='original', messages=[self.old, self.reply], observation={'state': 'done'})
        self.hub.preview.side_effect = lambda *a, **k: copy.deepcopy(self.preview)
        self.hub.snapshot.side_effect = lambda **k: dict(agents=[self.agent], connections=[])
        self.now = 1010

    def tearDown(self):
        self.tmp.cleanup()

    def change(self, **kwargs):
        return self.assistant.update(dict(revision=self.assistant.state()['revision'], **kwargs))

    def remember(self, text='Focus on NeMo', **kwargs):
        return self.change(action='remember', text=text, kind='priority', **kwargs)['records'][-1]

    def notice(self):
        self.hub.watcher.emit('handoff-1', 'done', 'NeMo comparison · gpu-004', agent_id=self.agent['id'], target=self.hub.watcher.target(self.agent))
        row = self.hub.watcher.state()['items'][0]
        self.hub.watcher.enabled = True
        return row

    def begin(self, kind='send', text='Compare the new checkpoint.', suffix='one'):
        command = dict(command_id='command_testing_'+suffix, agent_id=self.agent['id'], type=kind, message=text)
        self.assistant.begin(command, {'request_id': 'request_1'})
        self.assistant.delivered(command['command_id'], {'delivery': 'queued' if kind == 'queue' else 'sent', 'message': 'Transport acknowledged.'})
        return command

    def commitment(self, identity=None):
        rows = self.assistant.state()['commitments']
        return next(r for r in rows if r['id'] == identity) if identity else rows[0]

    def test_memory_is_explicit_editable_bounded_and_persistent(self):
        record = self.remember()
        self.assertEqual(record['source']['quote'], 'Focus on NeMo')
        self.change(action='remember', id=record['id'], text='Focus on LAP', kind='priority')
        again = Assistant(self.hub, clock=lambda: self.now)
        self.assertEqual(again.state()['records'][0]['text'], 'Focus on LAP')
        for index in range(11):
            self.remember('Watch ' + str(index))
        with self.assertRaisesRegex(BridgeError, 'Twelve'):
            self.remember('One too many')
        self.change(action='forget', id=record['id'])
        self.assertEqual(len(self.assistant.state()['records']), 11)

    def test_model_edits_require_current_exact_user_words(self):
        change = dict(type='remember', kind='watch', text='train a new model', quote='train a new model')
        with self.assertRaisesRegex(BridgeError, 'current words'):
            self.assistant.apply([change], 0, {'kind':'supervisor'}, query='Watch the existing study.', agents=[self.agent])
        change.update(text='existing study', quote='Watch the existing study.')
        self.assistant.apply([change], 0, {'kind':'supervisor','request_id':'request1'}, query='Watch the existing study.', agents=[self.agent])
        record = self.assistant.state()['records'][0]
        self.assertEqual(record['text'], 'existing study')
        self.assertEqual(record['source']['request_id'], 'request1')

    def test_changes_are_atomic_and_stale_device_edit_is_rejected(self):
        self.remember()
        with self.assertRaisesRegex(BridgeError, 'settings changed'):
            self.assistant.update(dict(action='attention', mode='quiet', revision=0))
        before = self.assistant.state()
        with self.assertRaises(BridgeError):
            self.assistant.apply([dict(type='remember', kind='priority', text='LAP'), dict(type='remember', kind='inferred', text='bad')], before['revision'], {'kind':'settings'}, agents=[])
        self.assertEqual(self.assistant.state(), before)

    def test_worker_and_non_owner_cannot_read_or_change_records(self):
        self.remember()
        for worker, owner in [(True, 'controller'), (False, 'other')]:
            self.hub.mesh = Mock(is_worker=worker, node_id='worker')
            self.hub.mesh.authority.return_value = {'owner': owner}
            self.assertEqual(self.assistant.state(), {'available': False})
            self.assertEqual(self.assistant.context(), {'records': [], 'commitments': []})
            with self.assertRaisesRegex(BridgeError, 'controller'):
                self.assistant.update(dict(action='forget', id='x', revision=0))

    def test_attention_expires_and_focus_is_version_bound(self):
        self.change(action='attention', mode='quiet', minutes=1)
        self.assertFalse(self.assistant.may_interrupt(dict(created=self.now, kind='host_offline')))
        self.now += 61
        self.assertEqual(self.assistant.state()['attention']['mode'], 'normal')
        self.change(action='attention', mode='focus', text='NeMo', minutes=120)
        row = dict(created=900, agent_id=self.agent['id'])
        revision = self.assistant.state()['revision']
        self.assertFalse(self.assistant.may_interrupt(row, {'status':'ready', 'focus_relevant': True, 'revision': revision-1}))
        self.assertTrue(self.assistant.may_interrupt(row, {'status':'ready', 'focus_relevant': True, 'revision': revision}))
        self.change(action='attention', mode='focus', text='This agent', agent_id=self.agent['id'])
        self.assertTrue(self.assistant.may_interrupt(row))

    def test_delivery_does_not_mean_started_and_an_old_reply_is_not_a_result(self):
        self.begin()
        self.assistant.track([self.agent])
        self.assertEqual(self.commitment()['status'], 'delivered')
        self.assertNotIn('baseline_users', self.commitment())
        self.preview['messages'].append(dict(id='user-new', role='user', text='Compare the new checkpoint.'))
        self.agent['state'] = 'running'
        self.assistant.track([self.agent])
        self.assertEqual(self.commitment()['status'], 'started')
        self.preview['messages'].append(dict(id='commentary', role='assistant', phase='commentary', text='The score looks better; still testing.'))
        self.agent['state'] = 'done'
        self.assistant.track([self.agent])
        self.assertEqual(self.commitment()['status'], 'started')
        self.preview['messages'].append(dict(id='final', role='assistant', phase='final_answer', text='The new checkpoint scored 0.5.'))
        self.assistant.track([self.agent])
        self.assertEqual(self.commitment()['status'], 'response')
        self.assertEqual(self.commitment()['response']['message_id'], 'final')

    def test_generic_push_does_not_wait_for_summary_but_keeps_attention_policy(self):
        row=dict(created=self.now,kind='done',agent_id=self.agent['id'])
        self.assertFalse(self.assistant.may_interrupt(row))
        self.assertTrue(self.assistant.may_interrupt(row,wait_for_summary=False))
        self.assertTrue(self.assistant.may_interrupt(row,{'status':'thinking'},wait_for_summary=False))
        self.assertFalse(self.assistant.may_interrupt(row,{'status':'superseded'},wait_for_summary=False))
        self.change(action='attention',mode='quiet',minutes=60)
        self.assertFalse(self.assistant.may_interrupt(row,wait_for_summary=False))
        self.change(action='attention',mode='focus',text='Different work',minutes=60)
        self.assertFalse(self.assistant.may_interrupt(row,wait_for_summary=False))
        self.change(action='attention',mode='focus',text='This task',agent_id=self.agent['id'],minutes=60)
        self.assertTrue(self.assistant.may_interrupt(row,wait_for_summary=False))

    def test_visual_notice_does_not_wait_for_summary_and_still_obeys_quiet(self):
        self.notice()
        notice=self.hub.watcher.state()['items'][0]
        self.assertTrue(self.assistant.decorate([dict(notice)])[0]['interrupt'])
        self.change(action='attention',mode='quiet',minutes=60)
        self.assertFalse(self.assistant.decorate([dict(notice)])[0]['interrupt'])

    def test_unseen_handoff_pushes_before_summary_and_seen_reply_still_cancels(self):
        watch=self.hub.watcher
        watch.relay={'url':'https://example.com','token':'a'*40}
        watch.relay_call=Mock(return_value={'subscribers':1})
        watch.emit('new','done','Review',agent_id=self.agent['id'],target=watch.target(self.agent))
        notice=watch.state()['items'][0]
        self.now+=2
        watch.deliver()
        self.assertEqual(watch.relay_call.call_count,1)
        self.assertEqual(watch.relay_call.call_args.args[1]['notice_id'],notice['id'])
        self.now+=1
        watch.emit('seen','done','Already seen',agent_id=self.agent['id'],target=watch.target(self.agent))
        watch.update({'read':[watch.state()['items'][0]['id']]})
        self.now+=2
        watch.deliver()
        self.assertEqual(watch.relay_call.call_count,1)

    def test_queued_request_waits_for_exact_instruction_not_previous_turn(self):
        self.begin(kind='queue')
        self.preview['messages'].append(dict(id='other-final', role='assistant', text='Previous task complete.'))
        self.assistant.track([self.agent])
        self.assertEqual(self.commitment()['status'], 'queued')
        self.preview['messages'].extend([dict(id='different', role='user', text='Another instruction'), dict(id='different-reply', role='assistant', text='Done')])
        self.assistant.track([self.agent])
        self.assertEqual(self.commitment()['status'], 'queued')

    def test_final_reply_is_tracked_after_a_newer_turn_started_between_observations(self):
        self.begin(kind='queue')
        self.preview['messages'].extend([
            dict(id='matched', role='user', text='Compare the new checkpoint.'),
            dict(id='answer', role='assistant', phase='final_answer', text='It scored 0.5.'),
            dict(id='next-user', role='user', text='Check another seed.'),
            dict(id='next-progress', role='assistant', phase='commentary', text='Checking the next seed.'),
        ])
        self.agent.update(state='running', activity_confirmed=True)
        self.preview['observation']['state'] = 'running'
        self.hub.action = Mock(side_effect=AssertionError('Tracking must not send anything'))
        self.assistant.track([self.agent])
        self.assertEqual(self.commitment()['status'], 'response')
        self.assertEqual(self.commitment()['response']['message_id'], 'answer')
        self.assistant = self.hub.assistant = Assistant(self.hub, clock=lambda: self.now)
        self.assistant.track([self.agent])
        self.assertEqual(self.commitment()['response']['message_id'], 'answer')
        self.hub.action.assert_not_called()
        self.assertEqual(self.hub.watcher.state()['items'], [])

    def test_newer_turn_does_not_make_progress_or_an_unknown_phase_a_final_answer(self):
        self.begin()
        self.agent.update(state='running', activity_confirmed=True)
        self.preview['observation']['state'] = 'running'
        base = copy.deepcopy(self.preview['messages'])
        for phase in ('commentary', 'analysis', '', None):
            self.preview['messages'] = base + [
                dict(id='matched', role='user', text='Compare the new checkpoint.'),
                dict(id='progress', role='assistant', phase=phase, text='A provisional result.'),
                dict(id='next-user', role='user', text='Stop. Try a different checkpoint.'),
                dict(id='wrong-answer', role='assistant', phase='final_answer', text='The other checkpoint scored 0.9.'),
            ]
            self.assistant.track([self.agent])
            self.assertEqual(self.commitment()['status'], 'started', phase)
            self.assertNotIn('response', self.commitment())

    def test_receipt_questions_use_fresh_exact_evidence_without_actions_or_a_model(self):
        self.begin(kind='queue')
        self.assistant.model = Mock(side_effect=AssertionError('No model for a receipt'))
        self.hub.action = Mock(side_effect=AssertionError('A receipt question is read-only'))
        receipt = lambda query: self.assistant.receipt_reply(query, self.agent['id'], [self.agent])
        self.assertIn("It's queued", receipt('Supervisor, did you send it?')['reply'])
        self.preview['messages'].append(dict(id='new-user', role='user', text='Compare the new checkpoint.'))
        self.agent.update(state='running', activity_confirmed=True)
        self.assertEqual(receipt('Did you send that?')['status'], 'started')
        self.preview['messages'].append(dict(id='new-answer', role='assistant', phase='final_answer', text='The score is 0.5.'))
        self.agent.update(state='done', activity_confirmed=False)
        self.assertEqual(receipt('Has the agent replied?')['status'], 'response')
        self.assertIn('replied to that instruction', receipt('Has the agent replied?')['reply'])
        self.preview['messages'][-1]['text'] = 'A changed source.'
        self.assertIn("can't verify its source", receipt('Has the agent replied?')['reply'])
        self.agent['connection_online'] = False
        self.assertIn('unavailable now', receipt('Did you send it?')['reply'])
        self.hub.action.assert_not_called(); self.assistant.model.assert_not_called()

    def test_receipt_fast_path_rejects_ambiguity_other_context_and_compound_requests(self):
        command = self.begin()
        for query in ('Do not send it.', 'Did you send it? Also send another message.',
                      'He asked "did you send it?"', 'Did you send it to another agent?', 'Is the task complete?'):
            self.assertIsNone(self.assistant.receipt_reply(query, self.agent['id'], [self.agent]))
        self.assertIsNone(self.assistant.receipt_reply('Did you send it?', 'another-agent', [self.agent]))
        self.begin(suffix='two')  # A batch at one instant is not a unique referent.
        self.assertIsNone(self.assistant.receipt_reply('Did you send it?', self.agent['id'], [self.agent]))
        self.assistant.update(dict(action='close', id=command['command_id']))
        self.assertEqual(self.assistant.receipt_reply('Did you send it?', self.agent['id'], [self.agent])['status'], 'delivered')

    def test_activity_answer_requires_fresh_selected_evidence_and_never_claims_task_success(self):
        answer = lambda query='Is it still working?', selected=None: self.assistant.activity_reply(
            query, self.agent['id'] if selected is None else selected, [self.agent])
        self.agent.update(observed_at=self.now, activity_confirmed=True, state='running')
        self.assertIn('still working', answer())
        self.assertIsNone(answer(selected=''))
        self.assertIsNone(answer('Is it still working? If not, send continue.'))
        self.assertIsNone(answer('Is another agent still working?'))
        self.now += 11
        self.assertIsNone(answer())
        self.agent.update(observed_at=self.now, activity_confirmed=False, state='done')
        self.assertEqual('Its latest turn has ended.', answer())
        self.agent.update(state='blocked', capacity_error=True, recovery={'kind':'capacity', 'status':'retrying'})
        self.assertIn('retries are on', answer())
        self.agent['recovery']['status'] = 'paused'
        self.assertIn('retries are paused', answer())
        self.agent.update(connection_online=False, activity_confirmed=True)
        self.assertIn("can't confirm", answer())

    def test_identical_old_text_missing_anchor_and_unmatched_terminal_do_not_confirm(self):
        self.begin(text=self.old['text'])
        self.assistant.track([self.agent])
        self.assertEqual(self.commitment()['status'], 'delivered')
        self.preview['messages'] = [dict(id='other-session-user', role='user', text=self.old['text']), self.reply]
        self.assistant.track([self.agent])
        self.assertEqual(self.commitment()['status'], 'delivered')
        self.preview['source'] = 'terminal'
        self.assistant.track([self.agent])
        self.assertEqual(self.commitment()['status'], 'delivered')

    def test_host_loss_keeps_commitment_and_never_resends(self):
        self.begin()
        self.agent['connection_online'] = False
        self.assistant.track([self.agent])
        self.assertIn('unconfirmed', self.commitment()['observation'])
        self.agent['connection_online'] = True
        self.assistant.track([self.agent])
        self.assertNotIn('observation', self.commitment())
        self.assertEqual(self.commitment()['status'], 'delivered')

    def test_lost_acknowledgement_reconciles_exact_instruction_after_restart_without_resending(self):
        command = self.begin()
        self.assistant.delivered(command['command_id'], error='Connection closed before acknowledgement.')
        self.assistant = self.hub.assistant = Assistant(self.hub, clock=lambda: self.now)
        self.hub.action = Mock(side_effect=AssertionError('Never retry an uncertain instruction'))
        self.assistant.track([self.agent])
        self.assertEqual(self.commitment()['status'], 'unconfirmed')
        self.preview['messages'] += [dict(id='arrived', role='user', text='Compare the new checkpoint.'),
                                     dict(id='result', role='assistant', phase='final_answer', text='It scored 0.5.')]
        self.assistant.track([self.agent])
        self.assertEqual(self.commitment()['status'], 'response')
        self.assertEqual(self.commitment()['response']['message_id'], 'result')
        self.hub.action.assert_not_called()

    def test_uncertain_receipt_does_not_reconcile_without_baseline_or_after_expiry(self):
        for verified, age in ((False, 1), (True, 86400)):
            with self.subTest(verified=verified, age=age):
                self.preview['source'] = 'original' if verified else 'terminal'
                command = self.begin(suffix=str(age))
                self.assistant.delivered(command['command_id'], error='Connection closed.')
                self.now += age
                self.preview.update(source='original', messages=[self.old, self.reply,
                    dict(id='arrived-'+str(age), role='user', text='Compare the new checkpoint.'),
                    dict(id='result-'+str(age), role='assistant', phase='final_answer', text='It scored 0.5.')])
                self.assistant.track([self.agent])
                self.assertEqual(self.commitment(command['command_id'])['status'], 'unconfirmed')

    def test_slow_history_read_does_not_hold_settings_or_undo_dismissal(self):
        command = self.begin()
        entered, release = threading.Event(), threading.Event()
        def history(*args, **kwargs):
            entered.set()
            release.wait(2)
            return copy.deepcopy(self.preview)
        self.hub.preview.side_effect = history
        thread = threading.Thread(target=self.assistant.track, args=([self.agent],))
        thread.start()
        try:
            self.assertTrue(entered.wait(1))
            self.change(action='attention', mode='quiet', minutes=1)
            self.assistant.update({'action':'close','id':command['command_id']})
        finally:
            release.set();thread.join(2)
        self.assertEqual(self.assistant.state()['commitments'], [])

    def test_crash_recovery_reads_receipt_without_executing(self):
        command = dict(command_id='command_crash_recovery', type='send', agent_id=self.agent['id'], message='Compare the new checkpoint.')
        self.assistant.begin(command, {'request_id':'request'})
        self.hub.deliveries.execute_once(command['command_id'], command, lambda: {'message':'Delivered'})
        self.assistant = self.hub.assistant = Assistant(self.hub, clock=lambda: self.now)
        self.assistant.track([self.agent])
        self.assertEqual(self.commitment()['status'], 'delivered')
        self.hub.action = Mock(side_effect=AssertionError('Observation must not issue commands'))
        self.assistant.track([self.agent])
        self.hub.action.assert_not_called()

    def test_interrupt_and_launch_require_actual_identity_and_state(self):
        self.agent['state'] = 'running'
        self.begin(kind='interrupt')
        self.assistant.track([self.agent])
        self.assertEqual(self.commitment()['status'], 'delivered')
        self.agent['state'] = 'done'
        self.assistant.track([self.agent])
        self.assertEqual(self.commitment()['status'], 'paused')
        command = dict(command_id='command_launch_test', type='launch', agent_id='', message='new task')
        self.assistant.begin(command, {})
        self.assistant.delivered(command['command_id'], {'message':'Launched', 'launched':dict(connection_id='gpu', pane='%1', session='another')})
        self.assistant.track([self.agent])
        self.assertEqual(self.commitment(command['command_id'])['agent_id'], '')

    def model(self, **overrides):
        result = dict(summary='It reports that the quantile checkpoint scored 0.4, compared with 0.3 for stdmean.', source_ids=['user-old','reply-old'], relevant_ids=[], focus_relevant=False)
        return Mock(return_value={**result, **overrides})

    def long_reply(self):
        self.reply['text'] += '\n\n' + 'Both checkpoints used the same observations, seeds, and evaluation settings. ' * 40

    def test_short_reply_keeps_exact_words_formatting_and_source_without_a_model(self):
        self.reply['text'] = 'Agent reports:\n\n**NeMo** scored `0.4`.\n\n- Baseline: `0.3`.\n- [Trace](/tmp/result.svg)\n'
        notice = self.notice()
        self.assistant.model = Mock(side_effect=AssertionError('Short replies need no rewrite'))
        for _ in range(3):
            self.assistant.tick()
            self.now += 3
        report = self.assistant.reports()[notice['id']]
        self.assertEqual(report['status'], 'ready')
        self.assertTrue(report['original_reply'])
        item = self.hub.watcher.notice(notice['id'])
        self.assertEqual(item['body'], self.reply['text'])
        self.assertTrue(item['original_reply'])
        self.assertTrue(item['interrupt'])
        self.assertEqual([s['message_id'] for s in item['sources']], ['reply-old'])
        self.assistant.model.assert_not_called()
        self.assertEqual(self.assistant.setting('model_calls', []), [])

    def test_short_reply_boundary_counts_words_not_characters(self):
        notice = self.notice()
        self.reply['text'] = ' '.join(['checkpoint_comparison'] * 300)
        self.assistant.model = self.model()
        self.assistant.summarize(notice, [self.agent])
        self.assertEqual(self.hub.watcher.notice(notice['id'])['body'], self.reply['text'])
        self.assistant.model.assert_not_called()
        self.reply['text'] += ' complete'
        self.assistant.summarize(notice, [self.agent])
        self.assistant.model.assert_called_once()
        self.assertFalse(self.hub.watcher.notice(notice['id'])['original_reply'])

    def test_short_reply_matching_cannot_rewrite_the_original(self):
        record = self.remember('Watch the NeMo checkpoint comparison')
        self.change(action='attention', mode='focus', text='NeMo checkpoint comparison')
        notice = self.notice()
        self.assistant.model = self.model(summary='', relevant_ids=[record['id']], focus_relevant=True)
        self.assistant.tick()
        self.assertTrue(self.assistant.model.call_args.args[0]['preserve_reply'])
        self.assertEqual(self.hub.watcher.notice(notice['id'])['body'], self.reply['text'])
        self.assertTrue(self.hub.watcher.notice(notice['id'])['interrupt'])
        self.assertEqual(self.assistant.reports()[notice['id']]['relevant_ids'], [record['id']])
        self.change(action='attention', mode='focus', text='An unrelated study')
        self.assistant.model = self.model(summary='Do not use this paraphrase.', focus_relevant=False)
        self.assistant.tick()
        self.assertEqual(self.hub.watcher.notice(notice['id'])['body'], self.reply['text'])
        self.assertFalse(self.hub.watcher.notice(notice['id'])['interrupt'])

    def test_summary_that_does_not_reduce_reading_keeps_original_reply(self):
        self.long_reply()
        notice=self.notice()
        self.assistant.model=self.model(summary=self.reply['text'])
        self.assistant.summarize(notice,[self.agent])
        item=self.hub.watcher.notice(notice['id'])
        self.assertEqual(item['body'],self.reply['text'])
        self.assertTrue(item['original_reply'])
        self.assertEqual(self.assistant.reports()[notice['id']]['status'],'ready')

    def test_cached_near_copy_is_upgraded_without_another_model_call(self):
        self.long_reply()
        notice=self.notice()
        self.assistant.model=self.model()
        self.assistant.summarize(notice,[self.agent])
        report=self.assistant.reports()[notice['id']]
        report.update(summary=self.reply['text']+' Extra context.',original_reply=False)
        report.pop('summary_policy')
        self.assistant.put('assistant_reports',report)
        self.assistant.model.reset_mock()
        self.assistant.tick()
        item=self.hub.watcher.notice(notice['id'])
        self.assertEqual(item['body'],self.reply['text'])
        self.assertTrue(item['original_reply'])
        self.assistant.model.assert_not_called()

    def test_short_reply_works_when_summary_budget_is_exhausted(self):
        with self.hub.db() as db:
            db.execute("INSERT INTO assistant_settings VALUES('model_calls',?)", (json.dumps([self.now] * 48),))
        notice = self.notice()
        self.assistant.model = self.model()
        self.assistant.tick()
        self.assertEqual(self.hub.watcher.notice(notice['id'])['body'], self.reply['text'])
        self.assistant.model.assert_not_called()
        self.long_reply()
        self.assistant.summarize(notice, [self.agent])
        self.assistant.model.assert_not_called()
        self.assertEqual(self.assistant.reports()[notice['id']]['retry_at'], self.now + 3600)

    def test_existing_pending_handoffs_upgrade_without_new_model_calls_or_notices(self):
        notice = self.notice()
        self.assistant.model = self.model()
        for long in (False, True):
            with self.subTest(long=long):
                if long:
                    self.long_reply()
                self.assistant.summarize(notice, [self.agent])
                report = self.assistant.reports()[notice['id']]
                report.pop('original_reply')
                report['short_reply_words'] = 100
                report['summary'] = 'An existing cached summary.'
                self.assistant.put('assistant_reports', report)
                self.assistant.model.reset_mock()
                self.assistant.tick()
                self.assistant.model.assert_not_called()
                item = self.hub.watcher.notice(notice['id'])
                self.assertEqual(item['body'], 'An existing cached summary.' if long else self.reply['text'])
                self.assertEqual(item['original_reply'], not long)
                self.assertEqual(len(self.hub.watcher.state()['items']), 1)

    def test_moderate_claude_reply_is_preserved_without_a_summary(self):
        self.agent['runtime'] = 'claude'
        self.agent['completion_confirmed'] = True
        self.reply['phase'] = ''
        self.reply['text'] += '\n\n' + 'Both checkpoints used the same observations, seeds, and evaluation settings. ' * 23
        self.assertGreater(len(self.reply['text'].split()), 200)
        self.assertLess(len(self.reply['text'].split()), 300)
        notice = self.notice()
        self.assistant.model = self.model()
        self.assistant.tick()
        self.assertEqual(self.hub.watcher.notice(notice['id'])['body'], self.reply['text'])
        self.assistant.model.assert_not_called()

    def test_old_claude_summary_uses_its_own_short_source_not_a_newer_reply(self):
        notice = self.notice()
        self.assistant.summarize(notice, [self.agent])
        report = self.assistant.reports()[notice['id']]
        report.update(summary='An old expanded summary of a short reply.', original_reply=False, short_reply_words=100)
        self.assistant.put('assistant_reports', report)
        self.preview['messages'].extend([dict(id='new-user', role='user', text='Continue.'),
                                         dict(id='new-reply', role='assistant', text='A different answer.')])
        self.agent.update(state='running', activity_confirmed=True)
        self.hub.watcher.update({'read':[notice['id']]})
        self.assistant.model = self.model()
        self.assistant.tick()
        updated = self.hub.watcher.notice(notice['id'])
        self.assertEqual(updated['body'], self.reply['text'])
        self.assertTrue(updated['original_reply'])
        self.assertFalse(updated['interrupt'])
        self.assertTrue(updated['read_at'])
        self.assertIsNone(updated['handled_at'])
        self.assertEqual(self.assistant.reports()[notice['id']]['status'], 'superseded')
        self.assistant.tick()
        self.assistant.model.assert_not_called()

    def test_old_summary_is_not_replaced_by_edited_or_terminal_evidence(self):
        notice = self.notice()
        self.assistant.summarize(notice, [self.agent])
        report = self.assistant.reports()[notice['id']]
        report.update(summary='Saved summary.', original_reply=False, short_reply_words=100, status='superseded')
        original = self.reply['text']
        for missing in ('edited', 'terminal'):
            with self.subTest(missing=missing):
                self.assistant.put('assistant_reports', report)
                self.reply['text'] = original
                self.preview['source'] = 'original'
                if missing == 'edited':
                    self.reply['text'] += ' Changed since the handoff.'
                else:
                    self.preview['source'] = 'terminal'
                self.assistant.refresh_cached_reply(notice, [self.agent])
                updated = self.hub.watcher.notice(notice['id'])
                self.assertEqual(updated['body'], 'Saved summary.')
                self.assertFalse(updated['original_reply'])
                self.assertFalse(updated['interrupt'])

    def test_short_reply_still_rechecks_source_and_current_turn(self):
        notice = self.notice()
        self.assistant.model = self.model()
        for change in ('edited', 'new_user', 'new_reply', 'terminal', 'working'):
            with self.subTest(change=change):
                self.agent.update(state='done', activity_confirmed=False)
                fresh = copy.deepcopy(self.preview)
                if change == 'edited':
                    fresh['messages'][-1]['text'] = 'Correction: the baseline was not measured.'
                elif change == 'new_user':
                    fresh['messages'].append(dict(id='next-user', role='user', text='Check another checkpoint.'))
                elif change == 'new_reply':
                    fresh['messages'].append(dict(id='next-reply', role='assistant', text='Correction: use the other checkpoint.'))
                elif change == 'terminal':
                    fresh['source'] = 'terminal'
                reads = iter([copy.deepcopy(self.preview), fresh])
                def read(*args, **kwargs):
                    result = next(reads)
                    if result is fresh and change == 'working':
                        self.agent.update(state='running', activity_confirmed=True)
                    return result
                self.hub.preview.side_effect = read
                self.assistant.summarize(notice, [copy.deepcopy(self.agent)])
                self.assertEqual(self.assistant.reports()[notice['id']]['status'], 'superseded')
        self.assistant.model.assert_not_called()

    def test_handoff_gets_one_short_source_linked_summary_not_every_poll(self):
        self.long_reply()
        notice = self.notice()
        self.assistant.model = self.model()
        self.assistant.tick()
        first = self.hub.watcher.state()['items'][0]
        self.assertTrue(first['assistant_summary'])
        self.assertIn('0.4', first['body'])
        self.assertTrue(first['interrupt'])
        self.assertEqual(first['sources'][-1]['message_id'], 'reply-old')
        for _ in range(5):
            self.now += 3
            self.assistant.tick()
        self.assistant.model.assert_called_once()
        self.assertEqual(len(self.hub.watcher.state()['items']), 1)
        self.assertEqual(self.hub.watcher.notice(notice['id'])['body'], first['body'])

    def test_working_agent_does_not_get_a_summary_or_alert(self):
        notice = self.notice()
        self.agent.update(state='running', activity_confirmed=True)
        self.assistant.model = self.model()
        self.assistant.tick()
        self.assistant.model.assert_not_called()
        self.assertFalse(self.hub.watcher.notice(notice['id'])['interrupt'])

    def test_new_turn_while_model_is_thinking_discards_result(self):
        self.long_reply()
        notice = self.notice()
        def changed(_):
            self.preview['messages'].append(dict(id='new-user', role='user', text='Do another study.'))
            return self.model()()
        self.assistant.model = changed
        self.assistant.summarize(notice, [self.agent])
        self.assertEqual(self.assistant.reports()[notice['id']]['status'], 'superseded')
        self.assertFalse(self.hub.watcher.notice(notice['id'])['interrupt'])

    def test_edited_evidence_or_invented_sources_cannot_publish(self):
        self.long_reply()
        notice = self.notice()
        self.assistant.model = self.model(source_ids=['made-up'])
        self.assistant.summarize(notice, [self.agent])
        self.assertNotIn('assistant_summary', self.hub.watcher.notice(notice['id']))
        def edited(_):
            self.reply['text'] = 'Correction: earlier scores were wrong.'
            return self.model()()
        self.assistant.model = edited
        self.assistant.summarize(notice, [self.agent])
        self.assertEqual(self.assistant.reports()[notice['id']]['status'], 'superseded')

    def test_additional_final_reply_during_generation_cannot_publish_an_older_summary(self):
        self.long_reply()
        notice = self.notice()
        def appended(_):
            self.preview['messages'].append(dict(id='late-final', role='assistant', text='One more result changes that conclusion.'))
            return self.model()()
        self.assistant.model = appended
        self.assistant.summarize(notice, [self.agent])
        self.assertEqual(self.assistant.reports()[notice['id']]['status'], 'superseded')

    def test_retry_is_bounded_and_ui_falls_back_after_timeout(self):
        self.long_reply()
        notice = self.notice()
        self.assistant.model = Mock(side_effect=BridgeError('model unavailable'))
        for _ in range(5):
            self.assistant.tick()
            self.now += 130
        self.assertEqual(self.assistant.model.call_count, 2)
        item = self.hub.watcher.notice(notice['id'])
        self.assertTrue(item['interrupt'])
        self.assertIn('turn ended', item['body'])

    def test_quiet_holds_push_and_releases_same_notice_without_marking_handled(self):
        notice = self.notice()
        self.assistant.model = self.model()
        self.assistant.tick()
        self.change(action='attention', mode='quiet', minutes=1)
        watch = self.hub.watcher
        watch.relay = {'url':'https://example.invalid','token':'fixture'}
        watch.relay_call = Mock(return_value={'subscribers':1})
        watch.deliver()
        watch.relay_call.assert_not_called()
        self.assertIsNone(watch.notice(notice['id'])['handled_at'])
        self.now += 61
        watch.deliver()
        watch.relay_call.assert_called_once()
        self.assertEqual(watch.relay_call.call_args.args[1]['notice_id'], notice['id'])

    def test_ready_old_event_is_held_if_agent_resumes_during_quiet(self):
        notice = self.notice()
        self.assistant.model = self.model()
        self.assistant.tick()
        self.agent['state'] = 'running'
        self.assistant.tick()
        self.now += 500
        item = self.hub.watcher.notice(notice['id'])
        self.assertTrue(item['assistant_summary'])  # History remains readable.
        self.assertFalse(item['interrupt'])

    def test_held_partial_retry_batch_does_not_starve_new_focus_updates(self):
        watch = self.hub.watcher
        watch.relay = {'url':'https://example.invalid','token':'fixture'}
        watch.relay_call = Mock(return_value={'subscribers':1})
        self.now = 2000
        watch.emit('old-other', 'host_offline', 'Other host', agent_id='other')
        watch.emit('old-focus', 'host_offline', 'Focus host', agent_id=self.agent['id'])
        with self.hub.db() as db:
            db.execute("UPDATE notifications SET push_batch='uncertain-old-batch'")
        self.now += 1
        watch.emit('new-focus', 'host_offline', 'Focus host', agent_id=self.agent['id'])
        self.change(action='attention', mode='focus', text='This agent', agent_id=self.agent['id'])
        watch.deliver()
        watch.relay_call.assert_called_once()
        body = watch.relay_call.call_args.args[1]
        self.assertEqual(body['count'], 1)
        self.assertNotEqual(body['id'], 'uncertain-old-batch')
        with self.hub.db() as db:
            self.assertEqual(db.execute("SELECT count(*) FROM notifications WHERE push_batch='uncertain-old-batch' AND push_state='pending'").fetchone()[0], 2)

    def test_summary_cannot_claim_relevance_to_another_agents_watch(self):
        self.long_reply()
        self.hub.snapshot.side_effect = lambda **k: dict(agents=[self.agent, {**self.agent,'id':'other'}], connections=[])
        record = self.remember('Watch this study', agent_id='other')
        notice = self.notice()
        self.assistant.model = self.model(relevant_ids=[record['id']])
        self.assistant.tick()
        self.assertEqual(self.assistant.reports()[notice['id']]['relevant_ids'], [])

    def test_context_stays_small_and_omits_transcripts_and_internal_delivery_fields(self):
        for i in range(12):
            self.remember(f'{i}: ' + 'x'*310)
        self.begin(text='x'*10000)
        context = self.assistant.context()
        self.assertLess(len(json.dumps(context)), 6500)
        self.assertNotIn('baseline_users', json.dumps(context))
        self.assertNotIn('reply-old', json.dumps(context))

    def test_control_requests_bypass_plain_spoken_answer_path(self):
        for text in ["I'm driving", 'Only tell me about NeMo', 'Remember my priority is LAP', 'Hold other updates', 'Resume updates']:
            self.assertTrue(requested(text), text)
        self.assertFalse(requested('Which agent prepares quantile checkpoints?'))

    def test_summary_parser_accepts_only_structured_agent_answer(self):
        value = self.model()()
        event = lambda text: json.dumps({'type':'item.completed','item':{'type':'agent_message','text':text}})
        self.assertEqual(report_response(event(json.dumps(value))), value)
        for text in [json.dumps(value), event('prose ' + json.dumps(value)), event(json.dumps({**value,'actions':[]})), event('{"summary":"a","summary":"b","source_ids":[],"relevant_ids":[],"focus_relevant":false}')]:
            with self.assertRaises(BridgeError):
                report_response(text)


if __name__ == '__main__':
    unittest.main()
