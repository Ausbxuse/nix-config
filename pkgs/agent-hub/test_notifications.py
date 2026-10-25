import copy
import hashlib
import json
from contextlib import contextmanager
import sqlite3
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock

from notifications import Watcher, short_update, SEEN_GRACE


class FakeHub:
    mesh = None
    def __init__(self, path): self.path = path
    @contextmanager
    def db(self):
        db = sqlite3.connect(self.path)
        db.row_factory = sqlite3.Row
        try:
            with db: yield db
        finally: db.close()


class WatchTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.hub = FakeHub(self.directory.name + '/db')
        self.now = 1000
        self.watch = Watcher(self.hub, clock=lambda: self.now)
        self.snapshot = {'connections': [{'id':'gpu', 'label':'gpu004','status':'online'}], 'agents': [
            {'id':'gpu:a', 'title':'Task','connection_id':'gpu','connection_label':'gpu004','state':'running','live':True,'preview':'• Starting a task.','summary':'Working'}]}
        self.now += 10
        self.tick()

    def tearDown(self): self.directory.cleanup()
    def tick(self, seconds=0):
        self.now += seconds
        self.watch.tick(copy.deepcopy(self.snapshot))
    def items(self): return self.watch.state()['items']

    def test_debounce_persistent_dedupe_and_read(self):
        self.snapshot['agents'][0]['state'] = 'done'
        self.tick(1)
        self.tick(3)
        self.assertEqual(self.items(), [])
        self.tick(4)
        self.assertEqual(len(self.items()), 1)
        self.watch.update({'read':[self.items()[0]['id']]})
        self.assertEqual(self.watch.state()['unread'], 0)
        self.watch = Watcher(self.hub, clock=lambda: self.now)
        self.tick(10)
        self.assertEqual(len(self.items()), 1)
        self.snapshot['agents'][0]['state'] = 'running'
        self.tick(1); self.tick(7)
        self.snapshot['agents'][0]['state'] = 'done'
        self.tick(1); self.tick(7)
        self.assertEqual(len(self.items()), 2)

    def test_proven_completion_settles_quickly_and_short_next_turn_is_not_lost(self):
        agent=self.snapshot['agents'][0]
        agent.update(runtime='codex',state='done',completion_confirmed=True)
        self.tick(1);self.tick(.5)
        self.assertEqual(self.items(),[])
        self.tick(.5)
        self.assertEqual(len(self.items()),1)
        # A turn shorter than the old six-second debounce must reset the
        # baseline, otherwise its final reply would never generate a notice.
        agent.update(state='running',activity_confirmed=True)
        self.tick(.1)
        agent.update(runtime='claude',state='done',activity_confirmed=False,completion_confirmed=True)
        self.tick(.2);self.tick(1)
        self.assertEqual(len(self.items()),2)

    def test_proven_completion_still_requires_stable_output_and_no_activity(self):
        agent=self.snapshot['agents'][0]
        agent.update(runtime='codex',state='done',completion_confirmed=True)
        self.tick(1)
        agent['preview']='• Updated final reply.'
        self.tick(.8);self.tick(.8)
        self.assertEqual(self.items(),[])
        agent['activity_confirmed']=True
        self.tick(.3);self.tick(10)
        self.assertEqual(self.items(),[])

    def test_seen_grace_cancels_visible_reply_before_push(self):
        agent={**self.snapshot['agents'][0],'state':'done','changed_at':self.now}
        target=self.watch.target(agent)
        self.watch.relay={'url':'https://example.com','token':'a'*40}
        self.watch.relay_call=Mock(return_value={'subscribers':1})
        self.watch.emit('visible','done','Reply',agent_id=agent['id'],target=target)
        self.assertEqual(self.watch.next_delivery(),SEEN_GRACE)
        self.now+=.7
        self.watch.deliver()
        self.watch.relay_call.assert_not_called()
        self.watch.update({'attention':{'id':target['handoff_id'],'agent_id':agent['id'],'version':target['version'],'mode':'seen'}})
        self.now+=SEEN_GRACE
        self.watch.deliver()
        self.watch.relay_call.assert_not_called()
        self.assertEqual(self.watch.state()['push_pending'],0)

    def test_unseen_deadline_retry_and_delivery_timing(self):
        from bridge import BridgeError
        agent={**self.snapshot['agents'][0],'state':'done','changed_at':self.now}
        self.watch.relay={'url':'https://example.com','token':'a'*40}
        self.watch.relay_call=Mock(side_effect=[BridgeError('offline'),{'subscribers':1}])
        created=self.now
        self.watch.emit('unseen','done','Reply',agent_id=agent['id'],target=self.watch.target(agent))
        self.now+=SEEN_GRACE-.01
        self.watch.deliver()
        self.watch.relay_call.assert_not_called()
        self.now=created+SEEN_GRACE
        self.watch.deliver()
        self.assertEqual(self.watch.relay_call.call_count,1)
        self.assertEqual(self.watch.next_delivery(),2)
        started=self.watch.state()['push_latest']['push_started_at']
        self.assertEqual(started,self.now)
        self.assertIsNone(self.watch.state()['push_latest']['push_accepted_at'])
        self.now+=2
        self.watch.deliver()
        latest=self.watch.state()['push_latest']
        self.assertEqual(latest['push_state'],'sent')
        self.assertEqual(latest['push_started_at'],started)
        self.assertEqual(latest['push_accepted_at'],self.now)
        self.assertEqual(self.watch.relay_call.call_args_list[0],self.watch.relay_call.call_args_list[1])

    def test_delivery_wakes_for_a_new_notice_while_health_is_slow(self):
        self.hub.stop=threading.Event()
        health_started,release_health,health_finished,delivered=(threading.Event() for _ in range(4))
        self.watch.clock=time.time
        self.hub.snapshot=Mock(return_value={'agents':[],'connections':[]})
        self.watch.relay={'url':'https://example.com','token':'a'*40}
        def relay(path,body):
            if path=='/status':
                health_started.set()
                release_health.wait(5)
                health_finished.set()
            else:
                delivered.set()
            return {'subscribers':1}
        self.watch.relay_call=relay
        self.watch.start()
        try:
            self.assertTrue(health_started.wait(1))
            # No handoff grace for a host outage; it is already settled.
            self.watch.emit('host-offline','connection','Host offline')
            self.assertTrue(delivered.wait(1),'Delivery waited for the health check or old polling interval')
            self.assertFalse(health_finished.is_set())
        finally:
            self.hub.stop.set()
            release_health.set()
            self.watch.wake.set();self.watch.push_wake.set()
            self.watch.thread.join(2);self.watch.push_thread.join(2)
            health_finished.wait(2)

    def test_automatic_capacity_retries_do_not_create_handoffs_or_push_noise(self):
        agent = self.snapshot['agents'][0]
        agent.update(state='blocked', recovery={'kind':'capacity','status':'retrying'})
        self.tick(1); self.tick(7); self.tick(30)
        self.assertEqual(self.items(), [])
        agent.update(state='running',activity_confirmed=True); agent.pop('recovery')
        self.tick(1); self.tick(7)
        self.assertEqual(self.items(), [])
        agent.update(state='done',activity_confirmed=False)
        self.tick(1); self.tick(7)
        self.assertEqual(len(self.items()), 1)
        self.assertEqual(self.items()[0]['kind'],'done')

    def test_seen_is_not_handled_and_reopen_survives_restart(self):
        self.watch.emit("one", "done", "Review this", agent_id="gpu:a")
        notice = self.items()[0]
        self.watch.update({"read": [notice["id"]]})
        self.assertEqual(self.watch.state()["unhandled"], 1)
        self.assertIsNone(self.items()[0]["handled_at"])
        self.watch.update({"handle": [notice["id"]]})
        self.assertEqual(self.watch.state()["unhandled"], 0)
        self.assertEqual(self.watch.state()["handled"], 1)
        self.assertEqual(self.watch.state()["push_pending"], 0)
        self.watch = Watcher(self.hub, clock=lambda: self.now)
        self.assertIsNotNone(self.items()[0]["handled_at"])
        self.watch.emit("two", "waiting", "New decision", agent_id="gpu:a")
        self.assertEqual(self.watch.state()["unhandled"], 1)
        self.watch.update({"reopen": [notice["id"]]})
        self.assertEqual(self.watch.state()["unhandled"], 2)
        self.assertIsNotNone(next(item for item in self.items() if item["id"] == notice["id"])["read_at"])

    def test_saved_long_alerts_are_short_without_changing_handled_state(self):
        body = "Agent output: " + "A long report with many details " * 100
        self.watch.emit("long", "progress", "Progress", body)
        notice = self.items()[0]
        self.watch.update({"handle": [notice["id"]]})
        short = self.items()[0]
        self.assertLessEqual(len(short["body"]), 165)
        self.assertNotIn("Agent output:", short["body"])
        self.assertTrue(short["handled_at"])
        with self.hub.db() as db:
            self.assertEqual(db.execute("SELECT body FROM notifications WHERE id=?", (notice["id"],)).fetchone()[0], body[:1400])
        self.assertEqual(short_update("done", body), "The turn ended. Review the response.")

    def test_working_output_and_silence_never_emit_updates(self):
        for preview in [
            '• Working (42s • esc to interrupt)',
            '• Deployed. Refresh the browser.',
            '• Tests passed. Next I will run the evaluation.',
        ]:
            self.snapshot['agents'][0]['preview'] = preview
            self.tick(301)
            self.tick(400)
        self.assertEqual(self.items(), [])
        self.assertEqual(self.watch.state()['push_pending'], 0)

    def test_confirmed_activity_overrides_inferred_handoff(self):
        agent = self.snapshot['agents'][0]
        agent.update(state='done', activity_confirmed=True)
        self.tick(1); self.tick(20)
        self.assertEqual(self.items(), [])
        agent['activity_confirmed'] = False
        self.tick(1); self.tick(7)
        self.assertEqual([item['kind'] for item in self.items()], ['done'])

    def test_codex_and_claude_require_completion_proof_even_if_false_done_persists(self):
        agent = self.snapshot['agents'][0]
        for runtime in ('codex', 'claude'):
            agent.update(runtime=runtime,state='done',activity_confirmed=False,completion_confirmed=False)
            self.tick(1);self.tick(30);self.tick(180)
            self.assertEqual(self.items(),[])
        agent['completion_confirmed']=True
        self.tick(1);self.tick(7)
        self.assertEqual([item['kind'] for item in self.items()],['done'])

    def test_claude_empty_editor_during_work_does_not_claim_completion(self):
        import bridge
        agent = dict(runtime='claude', state='running', summary='Working', live=True)
        editor = '\n────────────────────────\n❯ \n────────────────────────\n  custom footer'
        for capture in ('● I will check the next comparison.' + editor,
                        '● Saved an intermediate plot.\n' + editor):
            observation = bridge.observation(agent, capture)
            self.assertFalse(observation['completion_confirmed'])
            self.assertNotEqual(bridge.handoff_state({**agent, **observation}), 'done')
        finished = bridge.observation(agent, '● Saved the comparison.\n✻ Brewed for 12s' + editor)
        self.assertTrue(finished['completion_confirmed'])
        self.assertEqual(finished['state'], 'done')

    def test_handoff_rechecks_the_host_before_creating_a_notice(self):
        agent = self.snapshot['agents'][0]
        agent.update(state='done', changed_at=self.now)
        self.hub.preview=Mock(return_value={'observation':{'state':'running','activity_confirmed':True}})
        self.tick(1);self.tick(7);self.tick(30)
        self.assertEqual(self.items(),[])
        self.hub.preview.return_value={'observation':{'state':'done','activity_confirmed':False}}
        self.tick(3)
        self.assertEqual(len(self.items()),1)
        self.hub.preview.assert_called_with(agent['id'], history=True, fresh=True)

    def test_old_snapshot_cannot_announce_a_different_completed_turn(self):
        agent = self.snapshot['agents'][0]
        agent.update(state='done', changed_at=self.now, activity_fingerprint='old-answer',
                     turn_status={'turn_id':'old', 'state':'done', 'at':'old-time'})
        observation = {'state':'done', 'activity_fingerprint':'new-answer',
                       'turn_status':{'turn_id':'new', 'state':'done', 'at':'new-time'}}
        self.hub.preview = Mock(return_value={'observation':observation})
        self.tick(1); self.tick(7)
        self.assertEqual(self.items(), [])
        agent.update(observation, changed_at=self.now)
        self.tick(1); self.tick(7)
        self.assertEqual(len(self.items()), 1)
        self.assertEqual(self.items()[0]['target']['observation']['turn_status']['turn_id'], 'new')

    def test_new_completed_turn_without_a_polled_running_interval_still_notifies(self):
        agent = self.snapshot['agents'][0]
        agent.update(state='done', changed_at=self.now, completion_confirmed=True)
        self.tick(1); self.tick(1)
        self.assertEqual(len(self.items()), 1)
        agent.update(changed_at=self.now, activity_fingerprint='next-answer')
        self.tick(1); self.tick(1)
        self.assertEqual(len(self.items()), 2)
        self.assertEqual(len({item['target']['handoff_id'] for item in self.items()}), 2)

    def test_push_rechecks_host_and_never_delivers_a_superseded_handoff(self):
        agent=self.snapshot['agents'][0]
        agent.update(state='done',changed_at=self.now,connection_online=True)
        self.hub.snapshot=Mock(side_effect=lambda **kw:copy.deepcopy(self.snapshot))
        self.hub.preview=Mock(return_value={'observation':{'state':'done'}})
        self.watch.relay={'url':'https://example.com','token':'a'*40}
        self.watch.relay_call=Mock(return_value={'subscribers':1})
        self.tick(1);self.tick(7)
        self.hub.preview.return_value={'observation':{'state':'running','activity_confirmed':True}}
        self.now += SEEN_GRACE
        self.watch.deliver()
        self.watch.relay_call.assert_not_called()
        agent.update(state='running',changed_at=self.now)
        self.watch.deliver()
        self.watch.relay_call.assert_not_called()
        with self.hub.db() as db:
            self.assertEqual(db.execute('SELECT push_state FROM notifications').fetchone()[0],'superseded')
        self.tick(1);self.tick(7)
        agent.update(state='done',changed_at=self.now)
        self.hub.preview.return_value={'observation':{'state':'done','activity_confirmed':False}}
        self.tick(1);self.tick(7)
        self.now += SEEN_GRACE
        self.watch.deliver()
        self.watch.relay_call.assert_called_once()

    def test_retrying_push_batch_is_not_replayed_after_work_resumes(self):
        from bridge import BridgeError
        agent=self.snapshot['agents'][0]
        agent.update(state='done',changed_at=self.now,connection_online=True)
        self.hub.snapshot=Mock(side_effect=lambda **kw:copy.deepcopy(self.snapshot))
        self.hub.preview=Mock(return_value={'observation':{'state':'done'}})
        self.watch.relay={'url':'https://example.com','token':'a'*40}
        self.watch.relay_call=Mock(side_effect=BridgeError('network timeout'))
        self.tick(1);self.tick(7)
        self.now += SEEN_GRACE
        self.watch.deliver()
        self.assertEqual(self.watch.relay_call.call_count,1)
        agent.update(state='running',changed_at=self.now)
        self.now+=30
        self.watch.deliver()
        self.assertEqual(self.watch.relay_call.call_count,1)

    def test_push_requires_the_same_final_reply_even_if_snapshot_version_is_unchanged(self):
        agent = self.snapshot['agents'][0]
        agent.update(state='done', changed_at=self.now, connection_online=True)
        message = dict(id='one', role='assistant', phase='final_answer', text='The first result.')
        preview = {'source':'original', 'observation':{'state':'done'}, 'messages':[message]}
        self.hub.snapshot = Mock(side_effect=lambda **kw:copy.deepcopy(self.snapshot))
        self.hub.preview = Mock(side_effect=lambda *args, **kw:copy.deepcopy(preview))
        self.watch.relay = {'url':'https://example.com','token':'a'*40}
        self.watch.relay_call = Mock(return_value={'subscribers':1})
        self.tick(1); self.tick(7)
        self.now += SEEN_GRACE
        for messages in ([{**message, 'text':'The changed result.'}],
                         [message, dict(id='user-two', role='user', text='Continue the study.')],
                         [message, dict(id='two', role='assistant', phase='commentary', text='Checking again.')],
                         [dict(id='two', role='assistant', phase='final_answer', text='The next result.')]):
            preview['messages'] = messages
            self.watch.deliver()
            self.watch.relay_call.assert_not_called()
        preview['messages'] = [message]
        self.watch.deliver()
        self.watch.relay_call.assert_called_once()

    def test_transient_done_and_changing_output_do_not_handoff(self):
        agent = self.snapshot['agents'][0]
        agent['state'] = 'done'
        self.tick(1)
        agent['preview'] = '• One more check.'
        self.tick(5); self.tick(2)
        self.assertEqual(self.items(), [])
        agent['state'] = 'running'
        self.tick(1); self.tick(7)
        self.assertEqual(self.items(), [])
        agent['state'] = 'done'
        self.tick(1); self.tick(7); self.tick(400)
        self.assertEqual([item['kind'] for item in self.items()], ['done'])

    def test_waiting_and_blocked_handoffs_still_notify_once(self):
        for status in ['waiting', 'blocked']:
            self.snapshot['agents'][0]['state'] = status
            self.tick(1); self.tick(7); self.tick(400)
        self.assertEqual(sorted(item['kind'] for item in self.items()), ['blocked', 'waiting'])

    def test_cli_nags_do_not_restart_handoff_settling_or_generate_more_updates(self):
        agent = self.snapshot['agents'][0]
        agent.update(state='done', preview='• The plot is ready.')
        self.tick(1)
        for percent in (25, 10, 5):
            agent['preview'] = ('• The plot is ready.\n'
                                f'⚠ Heads up, you have less than {percent}% of your weekly limit left. Run /status for a breakdown.\n'
                                '  ↳ Recap: The plot is ready.\n           Duplicate text.')
            self.tick(3)
        self.assertEqual([item['kind'] for item in self.items()], ['done'])
        with self.hub.db() as db:
            saved = db.execute('SELECT body FROM notifications').fetchone()[0]
        self.assertNotIn('Heads up', saved)
        self.assertNotIn('Recap', saved)
        self.assertIn('The plot is ready.', saved)

    def test_restart_cancels_old_output_only_push_without_erasing_history(self):
        self.watch.emit('old-progress', 'progress', 'Old output')
        self.watch.emit('old-quiet', 'quiet', 'Old silence')
        self.watch.emit('handoff', 'done', 'Review response')
        self.watch = Watcher(self.hub, clock=lambda: self.now)
        self.assertEqual(self.watch.state()['push_pending'], 1)
        self.assertEqual(len(self.items()), 3)
        self.assertTrue(all(item['handled_at'] is None for item in self.items()))

    def test_offline_host_does_not_create_disappeared_agent(self):
        self.tick(10)
        self.snapshot['connections'][0]['status'] = 'offline'
        self.snapshot['agents'] = []
        self.tick(1); self.tick(10)
        self.assertEqual([item['kind'] for item in self.items()], ['connection'])
        self.snapshot['connections'][0]['status'] = 'online'
        self.tick(1); self.tick(10)
        self.assertIn('exited', [item['kind'] for item in self.items()])

    def test_worker_cannot_watch_or_publish_controller_updates(self):
        self.hub.mesh = Mock(is_worker=True)
        self.watch.emit('x','done','Private update')
        self.watch.relay = {'url':'https://example.com','token':'a'*40}
        self.watch.relay_call = Mock()
        self.tick(181)
        self.watch.deliver()
        self.watch.relay_call.assert_not_called()
        self.assertFalse(self.watch.state()['push_available'])

    def test_backoff_keeps_batch_identity_when_new_updates_arrive(self):
        self.watch.relay = {'url':'https://example.com','token':'a'*40}
        self.watch.emit('one','done','PRIVATE TITLE','PRIVATE CONTENT')
        from bridge import BridgeError
        self.watch.relay_call = Mock(side_effect=BridgeError('offline'))
        self.watch.deliver()
        first = self.watch.relay_call.call_args[0][1]
        self.assertNotIn('PRIVATE', str(first))
        self.watch.emit('two','done','Another')
        self.now += 20
        self.watch.relay_call = Mock(return_value={'subscribers':1})
        self.watch.deliver()
        self.assertEqual(first, self.watch.relay_call.call_args[0][1])
        self.assertEqual(self.watch.state()['push_pending'], 1)
        self.watch.deliver()
        self.assertEqual(self.watch.state()['push_pending'], 0)

    def test_push_identifies_displayed_agent_and_host_without_conversation_content(self):
        self.watch.relay = {'url':'https://example.com','token':'a'*40}
        self.watch.relay_call = Mock(return_value={'subscribers':1})
        agent = self.snapshot['agents'][0]
        agent.update(display_title='NeMo checkpoints',state='done',preview='• PRIVATE experiment result.')
        self.tick(1); self.tick(7)
        self.now += SEEN_GRACE
        self.watch.deliver()
        payload = self.watch.relay_call.call_args[0][1]
        self.assertEqual(payload['agents'],[{'name':'NeMo checkpoints','host':'gpu004'}])
        self.assertEqual(payload['agent_count'],1)
        self.assertEqual(payload['agent_id'],agent['id'])
        self.assertEqual(payload['notice_id'],self.items()[0]['id'])
        self.assertNotIn('PRIVATE',json.dumps(payload))
        self.assertNotIn('body',payload)

    def test_old_notice_gets_durable_name_and_keeps_it_through_retry_and_rename(self):
        from bridge import BridgeError
        self.watch.relay = {'url':'https://example.com','token':'a'*40}
        self.watch.emit('old','done','Do not parse this title','PRIVATE content',agent_id='gpu:a')
        self.watch.relay_call = Mock(side_effect=BridgeError('offline'))
        self.watch.deliver()
        first = self.watch.relay_call.call_args[0][1]
        self.assertEqual(first['agents'],[{'name':'Task','host':'gpu004'}])
        self.snapshot['agents'][0].update(display_title='Renamed task',title='new-window-name')
        self.tick(20)
        self.watch.relay_call = Mock(return_value={'subscribers':1})
        self.watch.deliver()
        self.assertEqual(first,self.watch.relay_call.call_args[0][1])
        self.assertNotIn('PRIVATE',json.dumps(first))

    def test_closed_agent_push_retains_its_last_name_and_host(self):
        self.snapshot['agents'][0]['display_title'] = 'Motion trace'
        self.tick(1)
        self.snapshot['agents'] = []
        self.tick(1)
        self.watch.relay = {'url':'https://example.com','token':'a'*40}
        self.watch.relay_call = Mock(return_value={'subscribers':1})
        self.watch.deliver()
        payload = self.watch.relay_call.call_args[0][1]
        self.assertEqual(payload['agents'],[{'name':'Motion trace','host':'gpu004'}])
        self.assertEqual(self.items()[0]['kind'],'exited')

    def test_simultaneous_agents_receive_separate_direct_push_destinations(self):
        self.watch.relay = {'url':'https://example.com','token':'a'*40}
        self.watch.relay_call = Mock(return_value={'subscribers':1})
        agents = [{**self.snapshot['agents'][0], 'id':f'host-{i}:a','display_title':'🧪'*120, 'connection_label':f'gpu-{i}-'+'🧪'*100} for i in range(5)]
        for i, agent in enumerate(agents):
            self.watch.emit(str(i),'done','Private title',agent_id=agent['id'],target=self.watch.target(agent))
        self.watch.emit('duplicate','done','Another turn',agent_id=agents[0]['id'],target=self.watch.target(agents[0]))
        self.now += SEEN_GRACE
        for _ in agents:
            self.watch.deliver()
        payloads = [call.args[1] for call in self.watch.relay_call.call_args_list]
        self.assertEqual(len(payloads), 5)
        self.assertEqual(sum(payload['count'] for payload in payloads), 6)
        self.assertEqual({payload['agent_id'] for payload in payloads}, {agent['id'] for agent in agents})
        for payload in payloads:
            self.assertEqual(payload['agent_count'], 1)
            self.assertEqual(len(payload['agents']), 1)
            self.assertTrue(all(len(item['name'])<=96 and len(item['host'])<=48 for item in payload['agents']))
            self.assertEqual(self.watch.notice(payload['notice_id'])['agent_id'], payload['agent_id'])
            self.assertLess(len(json.dumps(payload).encode()), 10000)

    def test_multiple_updates_for_one_agent_keep_a_conversation_destination(self):
        self.watch.relay = {'url':'https://example.com','token':'a'*40}
        self.watch.relay_call = Mock(return_value={'subscribers':1})
        agent = self.snapshot['agents'][0]
        self.watch.emit('first-turn','done','First reply',agent_id=agent['id'],target=self.watch.target(agent))
        self.now += 1
        self.watch.emit('second-turn','done','Second reply',agent_id=agent['id'],target=self.watch.target(agent))
        latest = self.items()[0]['id']
        self.now += SEEN_GRACE
        self.watch.deliver()
        payload = self.watch.relay_call.call_args[0][1]
        self.assertEqual(payload['count'],2)
        self.assertEqual(payload['agent_id'],agent['id'])
        self.assertEqual(payload['notice_id'],latest)

    def test_initial_handoff_settles_and_gets_a_verified_direct_destination(self):
        hub = FakeHub(self.directory.name + '/initial')
        watcher = Watcher(hub,clock=lambda:self.now)
        self.now += 10
        snapshot = copy.deepcopy(self.snapshot)
        snapshot['agents'][0].update(state='waiting',display_title='Pending study')
        watcher.tick(snapshot)
        self.assertEqual(watcher.state()['items'], [])
        self.now += 6
        watcher.tick(snapshot)
        self.now += SEEN_GRACE
        watcher.relay = {'url':'https://example.com','token':'a'*40}
        watcher.relay_call = Mock(return_value={'subscribers':1})
        watcher.deliver()
        payload = watcher.relay_call.call_args[0][1]
        self.assertEqual(payload['agents'],[{'name':'Pending study','host':'gpu004'}])
        self.assertEqual(payload['agent_id'],snapshot['agents'][0]['id'])
        self.assertEqual(watcher.notice(payload['notice_id'])['kind'], 'waiting')

    def test_seen_and_heard_suppress_only_the_exact_unclaimed_handoff(self):
        agent={**self.snapshot['agents'][0], 'state':'done','changed_at':1001}
        target=self.watch.target(agent)
        self.watch.emit('one','done','Review this',agent_id=agent['id'],target=target)
        self.watch.emit('next','done','A later reply',agent_id=agent['id'],target=self.watch.target({**agent,'changed_at':1002}))
        self.watch.update({'attention':{'id':target['handoff_id'],'agent_id':agent['id'],'version':target['version'],'mode':'seen'}})
        rows=self.items()
        self.assertEqual(self.watch.state()['push_pending'],1)
        self.assertEqual(self.watch.state()['unhandled'],2)
        self.assertEqual(sum(row['read_at'] is not None for row in rows),1)
        self.watch=Watcher(self.hub,clock=lambda:self.now)
        self.assertEqual(self.watch.state()['push_pending'],1)
        with self.hub.db() as db:
            marks = [dict(row) for row in db.execute('SELECT * FROM handoff_marks')]
        self.assertEqual(len(marks), 1)
        self.assertEqual(marks[0]['id'], target['handoff_id'])
        self.assertEqual(marks[0]['seen'], self.now)
        self.assertIsNone(marks[0]['handled'])
        self.watch.update({'attention':{'id':target['handoff_id'],'agent_id':agent['id'],'version':target['version'],'mode':'heard'}})
        self.assertEqual(self.watch.state()['unhandled'],2)

    def test_completed_speech_before_emit_is_remembered_for_that_turn_only(self):
        agent={**self.snapshot['agents'][0],'state':'done','changed_at':1001}
        target=self.watch.target(agent)
        item={'id':target['handoff_id'],'version':target['version'],'agent_id':agent['id']}
        self.hub.snapshot=Mock(return_value={'agents':[agent]})
        self.hub.handoffs=Mock()
        self.hub.handoffs.snapshot.return_value={'items':[item]}
        self.watch.update({'attention':{**item,'mode':'heard'}})
        self.watch.emit('after-speech','done','Review',agent_id=agent['id'],target=target)
        self.assertEqual(self.watch.state()['push_pending'],0)
        self.assertEqual(self.watch.state()['unhandled'],1)
        self.assertIsNotNone(self.items()[0]['read_at'])
        from bridge import BridgeError
        with self.assertRaises(BridgeError):
            self.watch.update({'attention':{**item,'version':'done:99999','mode':'seen'}})

    def test_visible_reply_anchor_is_exact_and_old_notices_remain_addressable(self):
        agent={**self.snapshot['agents'][0],'state':'done','changed_at':1001}
        message={'id':'reply-id','role':'assistant','text':'🧪 Saved both traces.'}
        self.hub.preview=Mock(return_value={'source':'original','observation':{'state':'done'},'messages':[message]})
        target=self.watch.target(agent)
        self.assertEqual(target['message_id'],message['id'])
        self.assertEqual(target['message_digest'],hashlib.sha256(message['text'].encode()).hexdigest())
        self.assertNotIn('text',target)
        self.watch.emit('old','done','Original update',agent_id=agent['id'],target=target)
        original=self.items()[0]['id']
        for number in range(65):
            self.now+=1
            self.watch.emit(str(number),'done','Later update')
        self.assertNotIn(original,[item['id'] for item in self.items()])
        self.assertEqual(self.watch.notice(original)['target'],target)
        self.hub.preview.return_value['observation']['state']='running'
        self.assertNotIn('message_id',self.watch.target(agent))

    def test_handoff_anchor_never_points_to_progress_or_a_previous_turn(self):
        agent={**self.snapshot['agents'][0],'state':'done','changed_at':1001}
        messages=[{'id':'old','role':'assistant','text':'An earlier result.'},
                  {'id':'new','role':'user','text':'Run the next comparison.'}]
        preview={'source':'original','observation':{'state':'done'},'messages':messages}
        self.hub.preview=Mock(return_value=preview)
        self.assertNotIn('message_id',self.watch.target(agent,preview))
        messages.append({'id':'progress','role':'assistant','phase':'commentary','text':'Still checking the result.'})
        self.assertNotIn('message_id',self.watch.target(agent,preview))
        messages.append({'id':'result','role':'assistant','phase':'final_answer','text':'The comparison is complete.'})
        self.assertEqual(self.watch.target(agent,preview)['message_id'],'result')
        self.hub.preview.assert_not_called()

    def test_attention_during_uncertain_delivery_preserves_dedupe_identity(self):
        from bridge import BridgeError
        self.watch.relay={'url':'https://example.com','token':'a'*40}
        agent={**self.snapshot['agents'][0],'state':'done','changed_at':1001}
        target=self.watch.target(agent)
        self.watch.emit('one','done','Review',agent_id=agent['id'],target=target)
        self.watch.relay_call=Mock(side_effect=BridgeError('Acknowledgment lost'))
        self.now += SEEN_GRACE
        self.watch.deliver()
        first=self.watch.relay_call.call_args[0][1]
        self.assertEqual(first['notice_id'],self.items()[0]['id'])
        self.watch.update({'attention':{'id':target['handoff_id'],'agent_id':agent['id'],'version':target['version'],'mode':'seen'}})
        self.assertEqual(self.watch.state()['push_pending'],1)
        self.now+=20
        self.watch.relay_call=Mock(return_value={'subscribers':1})
        self.watch.deliver()
        self.assertEqual(first,self.watch.relay_call.call_args[0][1])

    def test_handling_one_notice_in_an_attempted_batch_does_not_make_a_new_push(self):
        from bridge import BridgeError
        self.watch.relay={'url':'https://example.com','token':'a'*40}
        self.watch.emit('one','done','First')
        self.watch.emit('two','done','Second')
        self.watch.relay_call=Mock(side_effect=BridgeError('Acknowledgment lost'))
        self.watch.deliver()
        payload=self.watch.relay_call.call_args[0][1]
        self.watch.update({'handle':[self.items()[0]['id']]})
        self.now+=20
        self.watch.relay_call=Mock(return_value={'subscribers':1})
        self.watch.deliver()
        self.assertEqual(payload,self.watch.relay_call.call_args[0][1])

    def test_pause_survives_restart(self):
        self.watch.update({'enabled':False})
        self.watch = Watcher(self.hub, clock=lambda: self.now)
        self.assertFalse(self.watch.enabled)
        self.snapshot['agents'][0]['state'] = 'waiting'
        self.tick(400)
        self.assertEqual(self.items(), [])

    def test_push_health_is_cached_and_polling_does_not_call_public_network(self):
        self.watch.relay={'url':'https://example.com','token':'secret'}
        self.watch.relay_call=Mock(return_value={'subscribers':1,'last_accepted_at':1000})
        self.watch.state()
        self.watch.relay_call.assert_not_called()
        self.assertEqual(self.watch.refresh_push_status()['subscribers'],1)
        self.watch.refresh_push_status();self.watch.state()
        self.assertEqual(self.watch.relay_call.call_count,1)
        self.now+=6
        self.watch.refresh_push_status(force=True)
        self.assertEqual(self.watch.relay_call.call_count,2)
        from bridge import BridgeError
        self.watch.relay_call.side_effect=BridgeError('Push unavailable')
        self.now+=31
        value=self.watch.refresh_push_status()
        self.assertEqual(value['subscribers'],1)
        self.assertEqual(value['error'],'Push unavailable')
        self.assertLess(value['checked_at'],self.now)
        self.hub.mesh=Mock(is_worker=True)
        self.watch.relay_call.reset_mock()
        self.watch.refresh_push_status(force=True)
        self.watch.relay_call.assert_not_called()

    def test_status_explains_seen_suppression_without_exposing_message_content(self):
        self.watch.emit('one','done','PRIVATE TITLE','PRIVATE CONTENT')
        identity=self.items()[0]['id']
        self.watch.update({'read':[identity]})
        latest=self.watch.state()['push_latest']
        self.assertEqual(latest['push_state'],'observed')
        self.assertIsNotNone(latest['read_at'])
        self.assertNotIn('PRIVATE',str(latest))
