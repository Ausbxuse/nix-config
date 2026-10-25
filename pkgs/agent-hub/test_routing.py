"""Conversation selection must not send commands or move another client's view."""
import json
import tempfile
import time
import unittest
from unittest.mock import Mock, patch

import bridge
from server import Hub
from workflow import agent_lookup_requested, agent_navigation_requested, explicit_agent_choice, exact_navigation_target, request_scope


class RoutingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        with patch.object(Hub, 'save_connection'):
            self.hub = Hub(self.temp.name)
        self.agent = dict(id='gpu:lap', title='LAP evaluation', runtime='codex', connection_id='gpu',
                          connection_label='gpu-004', pane='%2', cwd='/research/lap', state='done',
                          summary='LAP evaluation complete.', connection_online=True, live=True,
                          needs_attention=True, acknowledged=False, mark={}, activity_fingerprint='v1')
        self.snapshot = dict(agents=[self.agent], connections=[], messages=[], handoffs={})
        self.hub.snapshot = Mock(return_value=self.snapshot)
        self.hub.preview = Mock(side_effect=lambda *a, **k: dict(
            messages=[dict(role='user', text='Evaluate LAP.')],
            observation=dict(state='done', activity_fingerprint='v1')))
        self.hub.action = Mock()

    def tearDown(self):
        self.hub.close()
        self.temp.cleanup()

    def run_request(self, query, target='gpu:lap', choices=None, clarification=None):
        key = 'job-' + str(len(self.hub.jobs))
        self.hub.jobs[key] = dict(request_id=key, context_agent_id='razy:nix')
        process = Mock(returncode=0)
        process.communicate.return_value = (json.dumps(dict(reply='LAP evaluation on gpu-004.', actions=[], select_agent_id=target, agent_choices=choices or [])), '')
        with patch('server.shutil.which', return_value='/bin/codex'), patch('server.subprocess.Popen', return_value=process) as run:
            self.hub.supervise(key, query, 'razy:nix', False, clarification)
        self.hub.action.assert_not_called()
        return self.hub.jobs[key], run, process

    def test_navigation_searches_all_agents_without_loading_plans(self):
        for query in ['Supervisor, please let me talk to the agent that works on lap',
                      'Switch me to LAP', 'Can you connect me to the LAP agent?',
                      'I want to talk to the LAP agent', 'Open the agent working on LAP']:
            with self.subTest(query=query):
                self.assertTrue(agent_navigation_requested(query))
                self.assertTrue(agent_lookup_requested(query))
                self.assertEqual(request_scope(query, [self.agent], 'elsewhere'), ([self.agent], False))
        for query in ['Which agent works on LAP?', 'Tell LAP: switch to the new dataset',
                      'Do not switch to LAP', 'The agent said "let me talk to LAP"']:
            self.assertFalse(agent_navigation_requested(query))

    def test_switch_uses_bounded_lookup_and_needs_no_command_permission(self):
        job, _, process = self.run_request('Please let me talk to the agent that works on lap')
        self.assertEqual(job['status'], 'complete')
        self.assertEqual(job['select_agent_id'], self.agent['id'])
        prompt = json.loads(process.communicate.call_args.args[0])
        self.assertTrue(prompt['select_requested'])
        self.assertEqual(prompt['agent_search']['terms'], ['lap'])
        self.assertNotIn('plans', prompt)

    def test_exact_task_destination_needs_no_model_or_history_reads(self):
        for query in ['Switch to LAP evaluation', 'Supervisor, please let me talk to the agent LAP evaluation on gpu-004',
                      'Open the conversation "LAP evaluation"', 'Can you connect me to LAP evaluation?']:
            with self.subTest(query=query):
                job, model, _ = self.run_request(query)
                model.assert_not_called()
                self.hub.preview.assert_not_called()
                self.assertEqual(job['select_agent_id'], self.agent['id'])
                self.assertEqual(job['lookup_metrics']['input_tokens'], 0)

    def test_exact_names_preserve_ambiguity_and_unavailable_destinations(self):
        other = {**self.agent, 'id':'razy:lap', 'connection_id':'razy', 'connection_label':'razy'}
        self.assertEqual(exact_navigation_target('Switch to LAP evaluation', [self.agent,other]), '')
        self.assertEqual(exact_navigation_target('Switch to LAP evaluation on razy', [self.agent,other]), other['id'])
        for query in ['Switch to the agent that works on LAP evaluation', 'Switch to LAP', 'Tell LAP evaluation: continue', 'Which agent is LAP evaluation?']:
            self.assertEqual(exact_navigation_target(query, [self.agent]), '')
        self.snapshot['agents']=[{**self.agent, 'connection_online':False}]
        job, model, _ = self.run_request('Switch to LAP evaluation')
        model.assert_not_called()
        self.assertEqual(job['select_agent_id'], '')
        self.assertIn('unavailable', job['reply'])

    def test_ordinary_lookup_and_ambiguous_answers_do_not_switch(self):
        job, _, _ = self.run_request('Which agent works on lap?')
        self.assertEqual(job['select_agent_id'], '')
        job, _, _ = self.run_request('Switch to LAP', target='')
        self.assertEqual(job['select_agent_id'], '')

    def test_readonly_voice_publishes_sentences_but_actions_and_switches_wait(self):
        reply = 'LAP evaluation is on gpu-004. The comparison is ready.'
        def stream(*args):
            args[6](reply.split('. ')[0] + '.')
            self.assertEqual(self.hub.jobs['voice']['phase'], 'responding')
            self.assertNotIn('reply', self.hub.jobs['voice'])
            return dict(reply=reply, actions=[], select_agent_id='', agent_choices=[]), {'model_ms': 10}
        self.hub.jobs['voice'] = dict(request_id='voice', voice=True, status='running')
        with patch('server.shutil.which', return_value='/bin/codex'), patch('server.spoken_answer', side_effect=stream) as speech, patch('server.subprocess.Popen') as model:
            self.hub.supervise('voice', 'Which agent works on lap?', '', True)
        speech.assert_called_once(); model.assert_not_called(); self.hub.action.assert_not_called()
        self.assertEqual(self.hub.jobs['voice']['reply'], reply)
        self.assertEqual(self.hub.jobs['voice']['status'], 'complete')
        self.assertTrue(self.hub.jobs['voice']['speech'])
        for query in ('Queue for LAP: compare the checkpoints', 'Switch to the agent working on LAP'):
            self.hub.jobs['guard'] = dict(request_id='guard', voice=True, status='running')
            process = Mock(returncode=0)
            actions = [dict(type='queue', agent_id='gpu:lap', message='compare the checkpoints', quote=query, instruction_source='current')] if query.startswith('Queue') else []
            process.communicate.return_value = (json.dumps(dict(reply='The proposed action.', actions=actions, request_intent='coordinate' if actions else 'answer',
                select_agent_id='gpu:lap' if not actions else '', agent_choices=[])), '')
            def act(action):
                self.assertNotIn('speech', self.hub.jobs['guard'])
                self.assertNotIn('reply', self.hub.jobs['guard'])
                return {'message': 'Queued for LAP on gpu-004.'}
            self.hub.action.side_effect = act
            with patch('server.shutil.which', return_value='/bin/codex'), patch('server.spoken_answer') as speech, patch('server.subprocess.Popen', return_value=process):
                self.hub.supervise('guard', query, '', True)
            speech.assert_not_called()
            self.assertEqual(self.hub.jobs['guard']['status'], 'complete')
            if actions:self.assertEqual(self.hub.jobs['guard']['reply'], "I've queued that for LAP on gpu-004.")

    def test_receipt_question_finishes_without_model_history_or_new_actions(self):
        self.hub.assistant.begin(dict(command_id='receipt-fixture', type='queue', agent_id=self.agent['id'], message='Evaluate another seed.'), {'request_id': 'prior'})
        self.hub.assistant.delivered('receipt-fixture', {'delivery': 'queued', 'message': 'Queued.'})
        for voice in (False, True):
            key = 'receipt-' + str(voice)
            self.hub.jobs[key] = dict(request_id=key, voice=voice)
            with patch('server.spoken_answer') as speech, patch('server.subprocess.Popen') as process, patch('server.project_context') as projects, patch('server.load_plans') as plans:
                self.hub.supervise(key, 'Did you send it?', self.agent['id'], True)
            job = self.hub.jobs[key]
            self.assertEqual(job['status'], 'complete')
            self.assertIn("It's queued for LAP evaluation on gpu-004", job['reply'])
            self.assertEqual(job['receipt_metrics']['model_ms'], 0)
            self.assertEqual(job['select_agent_id'], '')
            speech.assert_not_called(); process.assert_not_called(); projects.assert_not_called(); plans.assert_not_called()
        self.hub.action.assert_not_called()

    def test_invalid_offline_and_exited_targets_do_not_switch(self):
        job, _, _ = self.run_request('Switch to LAP', target='invented')
        self.assertEqual(job['select_agent_id'], '')
        for change in [dict(connection_online=False), dict(live=False)]:
            self.snapshot['agents'] = [{**self.agent, **change}]
            job, _, _ = self.run_request('Switch to LAP')
            self.assertEqual(job['select_agent_id'], '')

    def test_repeat_switch_reuses_validated_lookup_and_preserves_target(self):
        query = 'Switch to LAP'
        first, _, _ = self.run_request(query)
        second, run, _ = self.run_request(query)
        self.assertEqual(second['select_agent_id'], first['select_agent_id'])
        self.assertTrue(second['lookup_metrics']['answer_cache_hit'])
        run.assert_not_called()
        self.assertEqual(self.hub.preview.call_count, 1)

    def similar_agents(self):
        # Identical title, host and directory must not collapse separate panes.
        self.snapshot['agents'].append({**self.agent, 'id': 'gpu:checkpoints', 'pane': '%3'})
        return [dict(agent_id='gpu:lap', reason='Evaluate the LAP baseline'),
                dict(agent_id='gpu:checkpoints', reason='Prepare NeMo quantile checkpoints')]

    def test_ambiguity_overrides_a_suggested_selection_and_has_short_lived_choices(self):
        choices = self.similar_agents()
        job, _, _ = self.run_request('Switch to LAP', choices=choices)
        self.assertEqual(job['select_agent_id'], '')
        self.assertEqual([c['pane'] for c in job['agent_choices']], ['%2', '%3'])
        pending = self.hub.clarifications[job['clarification_id']]
        self.assertEqual(pending['request'], 'Switch to LAP')
        self.assertEqual(pending['context'], 'razy:nix')
        self.assertGreater(pending['expires_at'], time.time())
        self.assertLessEqual(pending['expires_at'], time.time() + 300)
        repeated, run, _ = self.run_request('Switch to LAP')
        self.assertEqual(repeated['agent_choices'], job['agent_choices'])
        self.assertNotEqual(repeated['clarification_id'], job['clarification_id'])
        run.assert_not_called()

    def test_followup_uses_only_candidates_and_latest_evidence_without_plans(self):
        choices = self.similar_agents()
        job, _, _ = self.run_request('Switch to LAP', choices=choices)
        pending = self.hub.clarifications[job['clarification_id']]
        self.snapshot['agents'].append({**self.agent, 'id': 'razy:other'})
        with patch('server.load_plans') as plans:
            result, _, process = self.run_request('the checkpoint one', target='gpu:checkpoints', clarification=pending)
        self.assertEqual(result['select_agent_id'], 'gpu:checkpoints')
        plans.assert_not_called()
        prompt = json.loads(process.communicate.call_args.args[0])
        self.assertEqual(prompt['clarification']['request'], 'Switch to LAP')
        self.assertEqual([c['agent_id'] for c in prompt['clarification']['candidates']], ['gpu:lap', 'gpu:checkpoints'])
        self.assertEqual(prompt['agent_search']['total_agents'], 2)
        self.assertEqual(result['lookup_metrics']['evidence_cache_hits'], 2)
        self.assertNotIn('history', prompt)
        # A live agent outside this choice cannot be selected by the model.
        result, _, _ = self.run_request('the other one', target='razy:other', clarification=pending)
        self.assertEqual(result['select_agent_id'], '')

    def test_candidate_order_is_part_of_the_cache_key(self):
        choices = self.similar_agents()
        job, _, _ = self.run_request('Switch to LAP', choices=choices)
        pending = self.hub.clarifications[job['clarification_id']]
        first, _, _ = self.run_request('the latter one', target='gpu:checkpoints', clarification=pending)
        reversed_order = {**pending, 'candidates': pending['candidates'][::-1]}
        second, run, _ = self.run_request('the latter one', target='gpu:lap', clarification=reversed_order)
        self.assertEqual(first['select_agent_id'], 'gpu:checkpoints')
        self.assertEqual(second['select_agent_id'], 'gpu:lap')
        run.assert_called_once()

    def test_missing_duplicate_and_offline_candidates_cannot_cause_a_switch(self):
        choices = self.similar_agents()
        self.snapshot['agents'][1]['connection_online'] = False
        job, _, _ = self.run_request('Switch to LAP', choices=choices)
        self.assertEqual(job['select_agent_id'], '')
        self.assertEqual(len(job['agent_choices']), 2)
        pending = self.hub.clarifications[job['clarification_id']]
        job, _, _ = self.run_request('the second one', target='gpu:checkpoints', clarification=pending)
        self.assertEqual(job['select_agent_id'], '')
        invalid = [choices[0], choices[0], dict(agent_id='invented', reason='Match')]
        job, _, _ = self.run_request('Open the agent for LAP', choices=invalid)
        self.assertEqual(job['select_agent_id'], '')
        self.assertEqual(job['agent_choices'], [])

    def test_expired_or_wrong_context_is_rejected_and_new_requests_drop_old_choices(self):
        choices = self.similar_agents()
        job, _, _ = self.run_request('Switch to LAP', choices=choices)
        token = job['clarification_id']
        with patch.object(self.hub, 'message') as message:
            for selected, choice_id in [('another-context', token), ('razy:nix', 'missing')]:
                with self.assertRaises(bridge.BridgeError):
                    self.hub.start_supervisor('the second one', selected, clarification_id=choice_id)
            self.hub.clarifications[token]['expires_at'] = time.time() - 1
            with self.assertRaises(bridge.BridgeError):
                self.hub.start_supervisor('the second one', 'razy:nix', clarification_id=token)
            message.assert_not_called()
        # A new explicit navigation request works even after the old choice expires.
        with patch('server.threading.Thread') as thread:
            self.hub.start_supervisor('Switch to RTC', 'razy:nix', clarification_id=token)
        self.assertIsNone(thread.call_args.kwargs['args'][-1])

    def test_short_answer_passes_the_pending_choice_but_never_action_permission(self):
        choices = self.similar_agents()
        job, _, _ = self.run_request('Switch to LAP', choices=choices)
        token = job['clarification_id']
        with patch('server.threading.Thread') as thread:
            self.hub.start_supervisor('gpu-004', 'razy:nix', allow_actions=True, clarification_id=token)
        args = thread.call_args.kwargs['args']
        self.assertEqual(args[-1]['request'], 'Switch to LAP')
        self.assertNotIn(token, self.hub.clarifications)
        process = Mock(returncode=0)
        process.communicate.return_value = (json.dumps(dict(reply='No instruction.', actions=[dict(type='interrupt', agent_id='gpu:lap')], select_agent_id='', agent_choices=[])), '')
        with patch('server.shutil.which', return_value='/bin/codex'), patch('server.subprocess.Popen', return_value=process):
            self.hub.supervise(*args)
        self.hub.action.assert_not_called()

    def test_explicit_choice_is_instant_and_still_rechecks_live_identity(self):
        choices = self.similar_agents()
        job, _, _ = self.run_request('Switch to LAP', choices=choices)
        pending = self.hub.clarifications[job['clarification_id']]
        self.hub.preview.reset_mock()
        result, run, _ = self.run_request('the second one', clarification=pending)
        self.assertEqual(result['select_agent_id'], 'gpu:checkpoints')
        self.assertEqual(result['lookup_metrics']['input_tokens'], 0)
        run.assert_not_called()
        self.hub.preview.assert_not_called()
        self.snapshot['agents'][1]['connection_online'] = False
        result, run, _ = self.run_request('option 2', clarification=pending)
        self.assertEqual(result['select_agent_id'], '')
        run.assert_not_called()
        self.assertEqual(explicit_agent_choice('the one on gpu-004', pending['candidates']), '')
        self.assertEqual(explicit_agent_choice('the checkpoint one', pending['candidates']), '')
        distinct_hosts = [{**pending['candidates'][0], 'host': 'razy'}, pending['candidates'][1]]
        self.assertEqual(explicit_agent_choice('the one on gpu-004', distinct_hosts), 'gpu:checkpoints')
        self.assertEqual(explicit_agent_choice('the third one', distinct_hosts), '')
        self.assertEqual(explicit_agent_choice('not the second one', distinct_hosts), '')


if __name__ == '__main__':
    unittest.main()
