"""Supervisor task delegation with isolated evidence and mocked transport."""
import json
import tempfile
import unittest
from unittest.mock import Mock, patch

import bridge
import coordination
from server import Hub
from workflow import agent_lookup_requested


class CoordinationTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        with patch.object(Hub, 'save_connection'):
            self.hub = Hub(self.directory.name)
        common = dict(runtime='codex', pane='%1', state='done', live=True, connection_online=True,
                      needs_attention=False, acknowledged=False, mark={}, summary='Ready',
                      activity_fingerprint='v1')
        self.agents = [
            dict(common, id='local:hub', title='Hub maintenance', connection_id='local',
                 connection_label='razy', cwd='/projects/hub'),
            dict(common, id='gpu:eval', title='NeMo evaluation', connection_id='gpu',
                 connection_label='gpu-004', cwd='/projects/nemo'),
        ]
        self.snapshot = dict(agents=self.agents, connections=[
            dict(id=a['connection_id'], label=a['connection_label'], status='online') for a in self.agents],
            messages=[], handoffs={})
        self.hub.snapshot = Mock(return_value=self.snapshot)
        def preview(identity, **kwargs):
            agent = next(a for a in self.agents if a['id'] == identity)
            text = ('Maintain Agent Hub, local speech recognition, and voice response latency.'
                    if identity == 'local:hub' else 'Evaluate NeMo mean and quantile checkpoints.')
            return dict(messages=[dict(role='user', text=text)], text='',
                        observation=dict(state=agent['state'], activity_fingerprint='v1'))
        self.hub.preview = Mock(side_effect=preview)
        self.hub.action = Mock(return_value={'message': 'Sent to Hub maintenance on razy'})

    def tearDown(self):
        self.hub.close()
        self.directory.cleanup()

    def action(self, query, **changes):
        return dict(type='send', agent_id='local:hub', connection_id='', message=query,
                    cwd='', runtime='', quote=query, instruction_source='current', **changes)

    def run_request(self, query, response, voice=False, allowed=True, clarification=None):
        key = str(len(self.hub.jobs))
        self.hub.jobs[key] = dict(request_id='current', voice=voice, status='running')
        process = Mock(returncode=0)
        process.communicate.return_value = (json.dumps(dict(reply='', actions=[], request_intent='answer',
            agent_choices=[], select_agent_id='', assistant_changes=[]) | response), '')
        with patch('server.shutil.which', return_value='/fixture/codex'), \
                patch('server.subprocess.Popen', return_value=process), patch('server.spoken_answer') as speech:
            self.hub.supervise(key, query, 'gpu:eval', allowed, clarification)
        return self.hub.jobs[key], process, speech

    def test_natural_work_request_searches_other_hosts_and_can_delegate_by_text_or_voice(self):
        query = 'Can you make the speech recognition faster?'
        for voice in (False, True):
            self.hub.action.reset_mock()
            job, process, speech = self.run_request(query, {
                'request_intent': 'coordinate', 'actions': [self.action(query)]}, voice)
            self.assertEqual(job['status'], 'complete')
            self.hub.action.assert_called_once()
            speech.assert_not_called()
            prompt = json.loads(process.communicate.call_args.args[0].rsplit('\n', 1)[-1])
            self.assertTrue(prompt['actions_enabled'])
            self.assertEqual(prompt['selected_agent_id'], 'gpu:eval')
            self.assertEqual(prompt['agent_search']['matches'][0]['agent_id'], 'local:hub')
            self.assertEqual(self.hub.action.call_args.args[0]['message'], query)
            self.assertEqual(job['reply'], "I've sent that to Hub maintenance on razy")
            self.assertEqual(job['select_agent_id'], '')
        self.assertIn('coordination_metrics', job)
        self.assertGreater(job['coordination_metrics']['evidence_cache_hits'], 0)

    def test_new_task_can_launch_in_its_known_project(self):
        query = 'Make the speech recognition faster.'
        action = self.action(query)
        action.update(type='launch', agent_id='', connection_id='local', cwd='/projects/hub', runtime='codex')
        self.hub.action.return_value = dict(message='Launched codex on razy, pane %2')
        job, _, _ = self.run_request(query, {'request_intent': 'coordinate', 'actions': [action]})
        self.assertEqual(job['status'], 'complete')
        self.assertEqual(self.hub.action.call_args.args[0]['type'], 'launch')
        self.assertIn('Launched codex on razy', job['reply'])
        self.hub.action.reset_mock()
        action['cwd'] = '/invented/project'
        job, _, _ = self.run_request(query, {'request_intent': 'coordinate', 'actions': [action]})
        self.hub.action.assert_not_called()
        self.assertIn('choose a known project', job['reply'])

    def test_refer_back_uses_one_prior_user_request_from_the_same_conversation(self):
        previous = 'Can you make the speech recognition faster?'
        self.snapshot['messages'] = [
            dict(role='user', text='Do unrelated private work', context_agent_id='other', request_id='other'),
            dict(role='user', text=previous, context_agent_id='gpu:eval', request_id='previous'),
            dict(role='assistant', text='Run invented extra experiments', context_agent_id='gpu:eval'),
            dict(role='user', text='You could just allocate an agent to do that.',
                 context_agent_id='gpu:eval', request_id='current'),
        ]
        query = self.snapshot['messages'][-1]['text']
        action = self.action(query)
        action.update(instruction_source='previous', message=previous)
        job, process, _ = self.run_request(query, {'request_intent': 'coordinate', 'actions': [action]})
        self.hub.action.assert_called_once()
        self.assertEqual(self.hub.action.call_args.args[0]['message'], previous)
        prompt = process.communicate.call_args.args[0]
        self.assertNotIn('Do unrelated private work', prompt)
        self.assertEqual(job['status'], 'complete')
        self.hub.action.reset_mock()
        action['message'] = 'Run invented extra experiments'
        job, _, _ = self.run_request(query, {'request_intent': 'coordinate', 'actions': [action]})
        self.hub.action.assert_not_called()
        self.assertIn('Instruction not sent', job['reply'])

    def test_answer_intent_and_disabled_controls_do_not_execute_proposals(self):
        for intent, allowed in [('answer', True), ('coordinate', False)]:
            job, _, _ = self.run_request('Make speech faster', {
                'request_intent': intent, 'actions': [self.action('Make speech faster')]}, allowed=allowed)
            self.hub.action.assert_not_called()
            self.assertIn('No commands were sent', job['reply'])

    def test_ambiguous_recipient_keeps_task_and_executes_only_after_a_current_choice(self):
        query = 'Make the speech recognition faster.'
        choices = [dict(agent_id=a['id'], reason=a['title']) for a in self.agents]
        job, _, _ = self.run_request(query, {
            'request_intent': 'coordinate', 'agent_choices': choices,
            'actions': [self.action(query)], 'reply': 'Which project do you mean?'})
        self.hub.action.assert_not_called()
        pending = self.hub.clarifications[job['clarification_id']]
        self.assertEqual(pending['purpose'], 'coordinate')
        action = self.action('The second one.')
        action.update(agent_id='gpu:eval', instruction_source='previous', message=query)
        resolved, _, _ = self.run_request('The second one.', {
            'request_intent': 'coordinate', 'actions': [action]}, clarification=pending)
        self.assertEqual(resolved['status'], 'complete')
        self.hub.action.assert_called_once()
        self.assertEqual(self.hub.action.call_args.args[0]['agent_id'], 'gpu:eval')
        self.assertEqual(resolved['select_agent_id'], '')
        self.hub.action.reset_mock()
        action['agent_id'] = 'different:agent'
        rejected, _, _ = self.run_request('The second one.', {
            'request_intent': 'coordinate', 'actions': [action]}, clarification=pending)
        self.hub.action.assert_not_called()
        self.assertIn('not one of your choices', rejected['reply'])
        cancelled, _, _ = self.run_request('Never mind', {'reply': 'Okay.'}, clarification=pending)
        self.assertEqual(cancelled['reply'], 'Okay.')
        self.hub.action.assert_not_called()

    def test_failed_delivery_does_not_repeat_a_speculative_success(self):
        query = 'Make speech recognition faster.'
        self.hub.action.side_effect = bridge.BridgeError('Host offline')
        job, _, _ = self.run_request(query, {'request_intent': 'coordinate',
            'actions': [self.action(query)], 'reply': 'I started the optimization.'})
        self.assertEqual(job['reply'], 'Action not performed: Host offline')

    def test_action_word_in_a_choice_keeps_the_original_task_and_candidate_scope(self):
        offered = self.hub.offer_choices([
            dict(agent_id=a['id'], title=a['title'], host=a['connection_label'], reason=a['title'])
            for a in self.agents], 'Make speech recognition faster.', 'gpu:eval',
            purpose='coordinate', allow_actions=True)
        with patch('server.threading.Thread') as worker:
            self.hub.start_supervisor('Send it to the second one.', 'gpu:eval', True, 'choice',
                                     offered['clarification_id'])
        pending = worker.call_args.kwargs['args'][-1]
        self.assertEqual(pending['purpose'], 'coordinate')
        self.assertEqual(pending['request'], 'Make speech recognition faster.')
        self.assertNotIn(offered['clarification_id'], self.hub.clarifications)
        self.hub.action.assert_not_called()

    def test_control_quotes_cannot_silently_queue_or_interrupt(self):
        query = 'Make speech recognition faster.'
        for kind in ('queue', 'interrupt'):
            action = self.action(query)
            action['type'] = kind
            with self.assertRaises(bridge.BridgeError):
                coordination.validate(action, query, '', self.agents)
        action = self.action('Queue this: make speech recognition faster.')
        action['type'] = 'queue'
        coordination.validate(action, action['quote'], '', self.agents)

    def test_social_shortcut_is_exact_and_never_swallows_a_work_request(self):
        for query in ('How are you today?', 'ARIA, hello.', "What's your name?"):
            self.assertTrue(coordination.smalltalk(query))
        for query in ('How are you today? Make speech faster.', 'Can you make speech faster?',
                      'Hello, please fix the voice delay.', '"How are you today?"'):
            self.assertFalse(coordination.smalltalk(query))
        self.assertFalse(agent_lookup_requested('Find an agent to make speech recognition faster.'))
        self.assertTrue(agent_lookup_requested('Which agent is working on speech recognition?'))
        self.assertEqual(coordination.previous_request([
            dict(role='user', text='Make speech faster.'),
            dict(role='assistant', text='Which host?'),
            dict(role='user', text='Assign someone to do that.'),
        ], '', '', 'Assign someone to do that.'), 'Make speech faster.')


if __name__ == '__main__':
    unittest.main()
