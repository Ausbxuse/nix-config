"""Regressions for repeated handoffs and noisy but healthy runtime changes."""
import copy
import json
import unittest
from unittest.mock import Mock

from notifications import Watcher, COMPLETION_ALERT_INTERVAL, SEEN_GRACE
import test_notifications as fixtures
from workflow import Handoffs


class NotificationNoiseTests(unittest.TestCase):
    tearDown = fixtures.WatchTests.tearDown
    tick, items = fixtures.WatchTests.tick, fixtures.WatchTests.items

    def setUp(self):
        fixtures.WatchTests.setUp(self)
        with self.hub.db() as db:
            db.execute('DELETE FROM watch_observations')

    def completed(self, turn='turn-a', message='answer-a', agent_id=None):
        agent = self.snapshot['agents'][0]
        agent.update(state='done', changed_at=self.now, runtime='claude', completion_confirmed=True,
                     activity_confirmed=False, connection_online=True, mark={},
                     turn_status={'turn_id': turn, 'state': 'done', 'at': '2026-10-26T12:00:00Z'},
                     activity_fingerprint='terminal-' + message)
        if agent_id:
            agent['id'] = agent_id
        self.hub.preview = Mock(side_effect=lambda *args, **kwargs: {
            'source': 'original', 'observation': copy.deepcopy(agent),
            'messages': [{'id': message, 'role': 'assistant', 'text': 'Same short response.'}]})
        return agent

    def test_same_source_survives_state_churn_restart_and_cli_relaunch(self):
        agent = self.completed(agent_id='gpu:%3:123:@3:456')
        self.tick(1); self.tick(1)
        first = self.items()[0]
        self.watch.update({'read': [first['id']]})
        agent.update(state='unknown', completion_confirmed=False)
        self.tick(1)
        self.completed(agent_id='gpu:%3:123:@3:789')
        self.tick(1); self.tick(1)
        self.watch = Watcher(self.hub, clock=lambda: self.now)
        agent['changed_at'] = self.now
        self.tick(10); self.tick(1); self.tick(60)
        self.assertEqual(len(self.items()), 1)
        self.assertEqual(self.items()[0]['id'], first['id'])
        self.assertIsNotNone(self.items()[0]['read_at'])
        self.assertIsNone(self.items()[0]['handled_at'])
        self.assertEqual(self.items()[0]['agent_id'], agent['id'])
        self.assertEqual(self.watch.state()['push_pending'], 0)

    def test_message_identity_bridges_missing_then_available_turn_id(self):
        agent = self.completed(turn='')
        self.tick(1); self.tick(1)
        agent['changed_at'] = self.now
        agent['turn_status']['turn_id'] = 'now-verified'
        agent['activity_fingerprint'] = 'different-terminal-chrome'
        self.tick(1); self.tick(1)
        self.assertEqual(len(self.items()), 1)
        # A different actual turn with identical words is still a new response.
        self.completed(turn='different-turn', message='different-message')
        self.tick(1); self.tick(1)
        self.assertEqual(len(self.items()), 2)

    def test_other_panes_and_hosts_are_not_deduplicated(self):
        for agent_id in ('gpu:%3:123:@3:456', 'gpu:%4:123:@4:456', 'laptop:%3:123:@3:456'):
            agent = self.completed(agent_id=agent_id)
            self.watch.emit(agent_id, 'done', 'Reply', agent_id=agent_id, target=self.watch.target(agent))
        self.assertEqual(len(self.items()), 3)
        self.assertTrue(all(not row['target'].get('quiet') for row in self.items()))

    def test_rapid_completions_stay_in_app_but_decisions_and_other_agents_alert(self):
        self.watch.relay = {'url': 'https://example.com', 'token': 'x' * 40}
        self.watch.relay_call = Mock(return_value={'subscribers': 1})
        for index, kind in enumerate(('done', 'done', 'waiting', 'blocked')):
            agent = self.completed(turn=str(index), message=str(index))
            target = self.watch.target(agent)
            self.watch.emit(str(index), kind, 'Update', agent_id=agent['id'], target=target)
            self.now += SEEN_GRACE
            self.watch.deliver()
        self.assertEqual(self.watch.relay_call.call_count, 3)
        quiet = [row for row in self.items() if row['target'].get('quiet')]
        self.assertEqual(len(quiet), 1)
        self.assertIsNone(quiet[0]['read_at'])
        self.assertIsNone(quiet[0]['handled_at'])
        self.now += COMPLETION_ALERT_INTERVAL
        agent = self.completed(turn='later', message='later')
        self.watch.emit('later', 'done', 'Reply', agent_id=agent['id'], target=self.watch.target(agent))
        self.now += SEEN_GRACE
        self.watch.deliver()
        self.assertEqual(self.watch.relay_call.call_count, 4)
        self.assertEqual(self.watch.relay_call.call_args.args[1]['kind'], 'done')

    def test_seen_handoff_stays_seen_when_only_timestamp_changes(self):
        agent = self.completed()
        self.tick(1); self.tick(1)
        handoffs = Handoffs(self.hub, clock=lambda: self.now)
        item = handoffs.snapshot([agent])['items'][0]
        self.watch.attended({**item, 'mode': 'seen'})
        agent['changed_at'] = self.now
        self.tick(1); self.tick(1)
        new = handoffs.snapshot([agent])['items'][0]
        self.assertEqual(item['id'], new['id'])
        self.assertTrue(new['seen'])
        self.assertNotEqual(item['version'], new['version'])

    def test_unsent_or_seen_previous_reply_does_not_silence_a_new_handoff(self):
        agent = self.completed()
        first = self.watch.emit('first', 'done', 'Reply', agent_id=agent['id'], target=self.watch.target(agent))
        agent = self.completed(turn='second', message='second')
        second = self.watch.emit('second', 'done', 'Reply', agent_id=agent['id'], target=self.watch.target(agent))
        self.assertEqual(second['push_state'], 'pending')
        with self.hub.db() as db:
            db.execute("UPDATE notifications SET push_state='sent',push_accepted_at=? WHERE id=?", (self.now, first['id']))
        self.watch.update({'read': [first['id']]})
        agent = self.completed(turn='third', message='third')
        third = self.watch.emit('third', 'done', 'Reply', agent_id=agent['id'], target=self.watch.target(agent))
        self.assertEqual(third['push_state'], 'pending')

    def test_existing_duplicates_fold_without_erasing_history_or_reviews(self):
        agent = self.completed()
        target = self.watch.target(agent)
        first = self.watch.emit('one', 'done', 'Reply', agent_id=agent['id'], target=target)
        with self.hub.db() as db:
            db.execute("DELETE FROM watch_settings WHERE key='event_identity_version'")
            db.execute('DELETE FROM notification_events')
            db.execute("INSERT INTO notifications(id,dedupe,kind,title,body,agent_id,created,read_at,handled_at,target) VALUES('old-duplicate','two','done','Reply','',?,?,?, ?,?)",
                       (agent['id'], self.now + 1, self.now + 1, self.now + 1, json.dumps(target)))
        self.watch = Watcher(self.hub, clock=lambda: self.now)
        self.assertEqual(len(self.items()), 1)
        self.assertEqual(self.watch.state()['push_pending'], 0)
        self.assertIsNotNone(self.items()[0]['handled_at'])
        self.assertEqual(self.watch.notice('old-duplicate')['id'], first['id'])
        with self.hub.db() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM notifications').fetchone()[0], 2)

    def test_short_outage_and_reconnect_do_not_interrupt(self):
        self.tick(10)
        self.snapshot['connections'][0]['status'] = 'offline'
        self.tick(1); self.tick(30)
        self.snapshot['connections'][0]['status'] = 'online'
        self.tick(1); self.tick(10)
        self.assertEqual(self.items(), [])
        self.snapshot['connections'][0]['status'] = 'offline'
        self.tick(1); self.tick(60)
        self.assertEqual(len(self.items()), 1)
        self.snapshot['connections'][0]['status'] = 'online'
        self.tick(1); self.tick(10)
        self.assertEqual(len(self.items()), 1)
        self.hub.snapshot = Mock(return_value=self.snapshot)
        with self.hub.db() as db:
            rows = [dict(row) for row in db.execute('SELECT * FROM notifications')]
        allowed, obsolete = self.watch.delivery_candidates(rows)
        self.assertEqual(allowed, set())
        self.assertEqual(obsolete, [rows[0]['id']])

    def test_idle_close_and_active_cli_restart_are_not_crashes(self):
        agent = self.snapshot['agents'][0]
        agent.update(state='idle', id='gpu:%1:100:@1:200')
        self.tick(1)
        self.snapshot['agents'] = []
        self.tick(1); self.tick(60)
        self.assertEqual(self.items(), [])
        agent.update(state='running', id='gpu:%1:100:@1:201')
        self.snapshot['agents'] = [agent]
        self.tick(1)
        self.snapshot['agents'] = []
        self.tick(1)
        agent['id'] = 'gpu:%1:100:@1:202'
        self.snapshot['agents'] = [agent]
        self.tick(1); self.tick(60)
        self.assertEqual(self.items(), [])
