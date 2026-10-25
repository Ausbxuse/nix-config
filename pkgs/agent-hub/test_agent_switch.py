import copy
import tempfile
import unittest
from unittest.mock import Mock, patch

import agent_switch
import bridge
from server import Hub
from test_attachments import png


class SwitchTests(unittest.TestCase):
    def setUp(self):
        self.directory = self.enterContext(tempfile.TemporaryDirectory())
        self.enterContext(patch.object(Hub, 'save_connection'))
        self.hub = Hub(self.directory)
        self.addCleanup(self.hub.close)
        self.connection = {'kind':'local', 'id':'fixture', 'label':'Fixture'}
        self.agent = {'id':'fixture:agent', 'runtime':'codex', 'cwd':'/project', 'state':'done', 'pane':'%1'}
        self.preview = {'conversation_id':'original-thread', 'observation':{'state':'done'},
                        'messages':[{'role':'user', 'text':'Compare the two checkpoints.'},
                                    {'role':'assistant', 'text':'The first evaluation passed.'}]}
        self.enterContext(patch('agent_switch.shutil.which', return_value='/bin/runtime'))
        self.enterContext(patch('agent_switch.bridge.verify_agent', return_value=self.agent))
        self.enterContext(patch.object(self.hub, 'lookup', return_value=(self.connection, self.agent)))
        self.enterContext(patch.object(self.hub, 'local_preview', side_effect=lambda *a, **kw:copy.deepcopy(self.preview)))
        self.launch = self.enterContext(patch('agent_switch.bridge.launch', return_value={'pane':'%2','session':'fixture-new'}))

    def request(self):
        info = self.hub.switch_info(self.agent['id'], 'handoff')
        return dict(type='handoff_start', agent_id=self.agent['id'], version=info['version'],
                    runtime=info['target'], context=info['context'], command_id='handoff_fixture_123')

    def test_review_is_read_only_then_handoff_starts_once_in_same_project(self):
        request = self.request()
        self.launch.assert_not_called()
        request['context'] += '\nYou:\nRun the second evaluation only.'
        result = self.hub.action(request)
        self.assertEqual(self.hub.action(request), result)
        self.launch.assert_called_once()
        args = self.launch.call_args.args
        self.assertEqual((args[0], args[1], args[3]), (self.connection, '/project', 'claude'))
        self.assertIn('Run the second evaluation only.', args[2])
        self.assertEqual(result['launched']['pane'], '%2')
        record = self.hub.recovery.records[self.agent['id']]
        self.assertTrue(record['paused'])
        self.assertEqual(record['connection_id'], self.connection['id'])

    def test_stale_busy_queued_missing_runtime_or_pending_decision_never_launch(self):
        for change in ({'conversation_id':'different-thread'}, {'observation':{'state':'running'}},
                       {'observation':{'state':'waiting'}}, {'observation':{'state':'unknown'}},
                       {'queue':{'items':[{'id':'queued'}]}}):
            with self.subTest(change=change):
                before = copy.deepcopy(self.preview)
                request = self.request()
                self.preview.update(change)
                with self.assertRaises(bridge.BridgeError):agent_switch.start(self.hub, self.connection, self.agent, request)
                self.preview = before
        request = self.request()
        with patch('agent_switch.shutil.which', return_value=None), self.assertRaises(bridge.BridgeError):
            agent_switch.start(self.hub, self.connection, self.agent, request)
        self.launch.assert_not_called()

    def test_context_keeps_current_request_and_images_without_internal_thoughts(self):
        image = self.hub.attachments.upload(self.agent['id'], '/project', 'plot.png', png())
        self.preview['messages'] = [
            {'role':'user', 'text':'<environment_context>internal</environment_context>\nInspect the plotted result.', 'attachments':[image]},
            {'role':'assistant', 'phase':'analysis', 'text':'SECRET_THOUGHT'},
            *[{'role':'assistant', 'text':f'Progress {i}. '+('measured result '*300)} for i in range(12)]]
        context = self.hub.switch_info(self.agent['id'], 'handoff')['context']
        self.assertIn('Inspect the plotted result.', context)
        self.assertIn(str(self.hub.attachments.path(image['id'], image['type'])), context)
        self.assertNotIn('SECRET_THOUGHT', context)
        self.assertNotIn('<environment_context>', context)
        self.assertLessEqual(len(context), 8500)

    def test_reverse_switch_uses_codex_default_and_does_not_resume_original(self):
        self.agent['runtime'] = 'claude'
        result = agent_switch.start(self.hub, self.connection, self.agent, self.request())
        self.assertEqual(self.launch.call_args.args[3:], ('codex', self.hub.codex_model))
        self.assertNotIn('resume_id', self.launch.call_args.kwargs)
        self.assertEqual(result['source_agent_id'], self.agent['id'])


if __name__ == '__main__':unittest.main()
