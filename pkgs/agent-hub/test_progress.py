"""Request correlation, job stages and schema upgrade without real agent work."""
import json
import sqlite3
import subprocess
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import bridge
from server import Hub


class ProgressTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.agents = patch.object(bridge, "list_agents", return_value=[])
        self.agents.start()
        self.hub = Hub(self.directory.name)

    def tearDown(self):
        self.hub.close()
        self.agents.stop()
        self.directory.cleanup()

    def begin(self, request_id="browser-request"):
        with patch("server.threading.Thread"):
            return self.hub.start_supervisor("Check the agents", allow_actions=True, request_id=request_id)["job_id"]

    def test_stages_and_messages_share_the_same_request(self):
        job_id = self.begin()
        job = self.hub.local_supervisor_status()["job"]
        self.assertEqual(job["phase"], "gathering")
        self.assertEqual(job["request_id"], "browser-request")
        self.assertEqual(self.hub.snapshot()["messages"][-1]["request_id"], "browser-request")

        answer = {"reply": "The task is running.", "request_intent": "coordinate", "actions": [{"type": "interrupt", "agent_id": "fake", "quote": "Pause the selected agent", "instruction_source": "current"}]}
        process = Mock(returncode=0)

        def communicate(*args, **kwargs):
            self.assertEqual(self.hub.job(job_id)["phase"], "thinking")
            return json.dumps(answer), ""

        def action(value):
            self.assertEqual(self.hub.job(job_id)["phase"], "acting")
            return {"message": "Mock pause requested"}

        process.communicate.side_effect = communicate
        with patch("server.shutil.which", return_value="/mock/codex"), patch("server.subprocess.Popen", return_value=process), patch.object(self.hub, "action", side_effect=action) as execute:
            self.hub.supervise(job_id, "Pause the selected agent", "", True)
        execute.assert_called_once()
        job = self.hub.job(job_id)
        self.assertEqual(job["status"], "complete")
        self.assertGreaterEqual(job["updated_at"], job["started_at"])
        self.assertFalse(self.hub.local_supervisor_status()["busy"])
        self.assertEqual(self.hub.snapshot()["messages"][-1]["request_id"], "browser-request")

    def test_waiting_job_wakes_on_completion_and_shutdown_without_losing_a_reply(self):
        job_id = self.begin()
        waiting, finished = threading.Event(), threading.Event()
        results = []
        original_wait = self.hub.job_changed.wait
        def wait(timeout=None):
            waiting.set()
            return original_wait(timeout)
        def read():
            results.append(self.hub.job(job_id, wait=True))
            finished.set()
        with patch.object(self.hub.job_changed, 'wait', side_effect=wait):
            thread = threading.Thread(target=read, daemon=True)
            thread.start()
            self.assertTrue(waiting.wait(1))
            self.hub.job_progress(job_id, 'thinking')
            self.assertFalse(finished.wait(.02), 'an intermediate phase is not a completed reply')
            self.hub.job_progress(job_id, 'complete', status='complete', reply='Ready.')
            self.assertTrue(finished.wait(1))
            thread.join(1)
            self.assertEqual(results[0]['reply'], 'Ready.')
            self.assertEqual(self.hub.job(job_id, wait=True)['reply'], 'Ready.')
            self.assertEqual(self.hub.job('missing', wait=True)['status'], 'failed')
            self.hub.jobs[job_id]['status'] = 'running'
            waiting.clear(); finished.clear()
            thread = threading.Thread(target=read, daemon=True)
            thread.start()
            self.assertTrue(waiting.wait(1))
            self.hub.close()
            self.assertTrue(finished.wait(1))
            thread.join(1)

    def test_saved_plans_reach_supervisor_but_not_public_snapshot(self):
        job_id = self.begin("plans-request")
        plans = {"sources": [{"label": "Research", "documents": [{"text": "PRIVATE PLAN"}]}]}
        process = Mock(returncode=0)
        process.communicate.return_value = (json.dumps({"reply": "Research is your saved goal.", "actions": []}), "")
        with patch("server.load_plans", return_value=plans) as read, patch("server.shutil.which", return_value="/mock/codex"), patch("server.subprocess.Popen", return_value=process):
            self.hub.supervise(job_id, "What is my focus?", "", False)
        read.assert_called_once_with(self.hub.state_dir, worker=False)
        self.assertIn('"plans":', process.communicate.call_args.args[0])
        self.assertIn("PRIVATE PLAN", process.communicate.call_args.args[0])
        self.assertNotIn("PRIVATE PLAN", json.dumps(self.hub.snapshot()))

    def test_timeout_ends_progress_and_preserves_failed_request_identity(self):
        job_id = self.begin("timeout-request")
        process = Mock()
        process.communicate.side_effect = [subprocess.TimeoutExpired("codex", 180), ("", "")]
        with patch("server.shutil.which", return_value="/mock/codex"), patch("server.subprocess.Popen", return_value=process):
            self.hub.supervise(job_id, "Check the agents", "", False)
        self.assertEqual(self.hub.job(job_id)["status"], "failed")
        self.assertFalse(self.hub.local_supervisor_status()["busy"])
        self.assertIn("timed out", self.hub.job(job_id)["error"])
        self.assertEqual(self.hub.snapshot()["messages"][-1]["request_id"], "timeout-request")
        process.kill.assert_called_once()

    def test_existing_chat_survives_request_id_schema_upgrade(self):
        with tempfile.TemporaryDirectory() as directory:
            with sqlite3.connect(Path(directory) / "hub.sqlite") as db:
                db.execute("CREATE TABLE messages(id INTEGER PRIMARY KEY, role TEXT, text TEXT, created REAL)")
                db.execute("INSERT INTO messages VALUES(1, 'user', 'Earlier conversation', 1)")
            hub = Hub(directory)
            try:
                self.assertEqual(hub.snapshot()["messages"][0]["text"], "Earlier conversation")
                hub.message("user", "New message", "new-request")
                self.assertEqual(hub.snapshot()["messages"][-1]["request_id"], "new-request")
            finally:
                hub.close()

    def test_invalid_request_id_is_rejected_before_creating_a_message(self):
        with self.assertRaises(bridge.BridgeError):
            self.hub.start_supervisor("Hello", request_id="<script>")
        self.assertEqual(self.hub.snapshot()["messages"], [])
        self.assertFalse(self.hub.supervisor_busy)

    def test_informational_request_cannot_execute_suggested_actions(self):
        job_id = self.begin('read-only-request')
        process = Mock(returncode=0)
        process.communicate.return_value = (json.dumps({'reply': 'A review is pending.', 'actions': [{'type': 'interrupt', 'agent_id': 'fake'}]}), '')
        with patch('server.shutil.which', return_value='/mock/codex'), patch('server.subprocess.Popen', return_value=process), patch.object(self.hub, 'action') as execute, patch('server.load_plans') as plans:
            self.hub.supervise(job_id, 'Tell me what changed', '', True)
        execute.assert_not_called()
        plans.assert_not_called()

    def test_agent_instructions_must_come_from_current_request(self):
        job_id = self.begin('instruction-request')
        process = Mock(returncode=0)
        process.communicate.return_value = (json.dumps({'reply': 'I will route your instruction.', 'request_intent': 'coordinate', 'actions': [{'type': 'send', 'agent_id': 'fake', 'message': 'Run another experiment', 'quote': 'Tell RTC to compare the traces', 'instruction_source': 'current'}]}), '')
        with patch('server.shutil.which', return_value='/mock/codex'), patch('server.subprocess.Popen', return_value=process), patch.object(self.hub, 'action') as execute:
            self.hub.supervise(job_id, 'Tell RTC to compare the traces', '', True)
        execute.assert_not_called()
        self.assertIn('Instruction not sent', self.hub.job(job_id)['reply'])

    def test_valid_explicit_instruction_is_delivered_without_paraphrase(self):
        job_id = self.begin('exact-request')
        action = {'type': 'send', 'agent_id': 'fake', 'message': 'compare the traces', 'quote': 'Tell RTC to compare the traces', 'instruction_source': 'current'}
        process = Mock(returncode=0)
        process.communicate.return_value = (json.dumps({'reply': 'I will send that to RTC.', 'request_intent': 'coordinate', 'actions': [action]}), '')
        with patch('server.shutil.which', return_value='/mock/codex'), patch('server.subprocess.Popen', return_value=process), patch.object(self.hub, 'action', return_value={'message': 'Instruction delivered'}) as execute:
            self.hub.supervise(job_id, 'Tell RTC to compare the traces', '', True)
        execute.assert_called_once_with({**action, 'command_id': f'supervisor_{job_id}_0'})
        tracked = self.hub.assistant.state()['commitments'][0]
        self.assertEqual(tracked['status'], 'delivered')
        self.assertEqual(tracked['text'], 'compare the traces')
        self.assertIn('Instruction delivered', self.hub.job(job_id)['reply'])

    def test_task_context_does_not_include_other_conversations(self):
        self.hub.message('user', 'Private other task discussion', 'other', 'agent-b')
        self.hub.message('assistant', 'Prior selected task reply', 'selected', 'agent-a')
        job_id = self.begin('scoped-request')
        process = Mock(returncode=0)
        process.communicate.return_value = (json.dumps({'reply': 'The selected task is ready.', 'actions': []}), '')
        with patch('server.shutil.which', return_value='/mock/codex'), patch('server.subprocess.Popen', return_value=process):
            self.hub.supervise(job_id, 'Explain this result', 'agent-a', False)
        prompt = process.communicate.call_args.args[0]
        self.assertIn('Prior selected task reply', prompt)
        self.assertNotIn('Private other task discussion', prompt)

    def test_agent_lookup_reads_other_tasks_without_loading_plans_or_running_actions(self):
        snapshot = self.hub.snapshot()
        common = dict(runtime='codex', connection_id='gpu', connection_label='gpu-004', pane='%1', cwd='/research',
                      state='running', summary='Working', connection_online=True, needs_attention=False, acknowledged=False, mark={})
        snapshot['agents'] = [dict(common, id='selected', title='Hub UI'), dict(common, id='lap', title='lap')]
        query = 'Which agent is preparing stdmean vs quantile ckpts by Nemo?'
        process = Mock(returncode=0)
        process.communicate.return_value = (json.dumps({'reply':'lap on gpu-004 is testing both normalizers.', 'actions':[]}), '')
        def preview(agent_id, **kwargs):
            self.assertTrue(kwargs['history'])
            return {'messages':[{'role':'assistant','text':'Nemo task-meanstd and task-quantile fits are running.'}]} if agent_id == 'lap' else {'messages':[]}
        job_id = self.begin('lookup-request')
        with patch.object(self.hub, 'snapshot', return_value=snapshot), patch.object(self.hub, 'preview', side_effect=preview) as read, patch('server.shutil.which', return_value='/mock/codex'), patch('server.subprocess.Popen', return_value=process), patch('server.load_plans') as plans, patch.object(self.hub, 'action') as execute:
            self.hub.supervise(job_id, query, 'selected', True)
        payload = json.loads(process.communicate.call_args.args[0].rsplit('\n', 1)[-1])
        self.assertEqual(payload['agent_search']['matches'][0]['title'], 'lap')
        self.assertEqual(set(payload), {'request', 'agent_search', 'select_requested'})
        self.assertFalse(payload['select_requested'])
        self.assertEqual(read.call_count, 2)
        plans.assert_not_called()
        execute.assert_not_called()

    def test_unchanged_lookup_reuses_answer_but_fresh_output_runs_model(self):
        snapshot = self.hub.snapshot()
        agent = dict(id='lap', title='lap', runtime='codex', connection_id='gpu', connection_label='gpu-004',
                     pane='%1', cwd='/research', state='running', summary='Working', connection_online=True,
                     needs_attention=False, acknowledged=False, mark={}, activity_fingerprint='v1')
        snapshot['agents'] = [agent]
        query = 'Which agent runs Nemo?'
        process = Mock(returncode=0)
        process.communicate.return_value = (json.dumps({'reply': 'lap on gpu-004 runs Nemo.', 'actions': []}), '')
        def preview(*args, **kwargs):
            return {'messages': [{'role': 'assistant', 'text': 'Nemo fits are running.'}],
                    'observation': {'activity_fingerprint': agent['activity_fingerprint'], 'state': agent['state']}}
        with patch.object(self.hub, 'snapshot', return_value=snapshot), patch.object(self.hub, 'preview', side_effect=preview) as read, patch('server.shutil.which', return_value='/mock/codex'), patch('server.subprocess.Popen', return_value=process) as spawn:
            for i in range(3):
                if i == 2: agent['activity_fingerprint'] = 'v2'
                job_id = self.begin('lookup-' + str(i))
                self.hub.supervise(job_id, query, '', False)
                self.assertEqual(self.hub.job(job_id)['lookup_metrics']['answer_cache_hit'], i == 1)
            self.assertEqual(spawn.call_count, 2)
            self.assertEqual(read.call_count, 2)
            self.assertIn('--ignore-user-config', spawn.call_args.args[0])
            self.assertIn('features.shell_tool=false', spawn.call_args.args[0])
        with self.hub.db() as db:
            messages = [dict(row) for row in db.execute("SELECT request_id FROM messages WHERE role='assistant'")]
        self.assertEqual([m['request_id'] for m in messages], ['lookup-0', 'lookup-1', 'lookup-2'])
