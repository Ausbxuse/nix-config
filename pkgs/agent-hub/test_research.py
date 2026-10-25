"""Research provenance, durable curation, incremental bounds, and model isolation."""
from contextlib import contextmanager
import copy
import hashlib
import json
from pathlib import Path
import sqlite3
import subprocess
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

from bridge import BridgeError
from research import Research, extract, hashed, MAX_CALLS, paper_links, DeferredSummary


class Sources:
    def __init__(self):
        self.epoch = 'a' * 32
        self.generation = 'b' * 32
        self.session = 'c' * 32
        self.revision = 1
        self.offline = False
        self.calls = []
        self.items = [self.message()]

    def hosts(self):
        return {'gpu': 'GPU lab'}

    def source(self):
        return {'host': 'gpu', 'host_label': 'GPU lab', 'session': self.session,
                'generation': self.generation, 'title': 'Policy comparison', 'cwd': '/work/robot-learning',
                'agent_id': 'gpu:agent', 'runtime': 'codex', 'last_ordinal': len(self.items),
                'revision': self.revision, 'missing': False}

    def message(self, index=1, text=None):
        text = text or ('## Quantile policy comparison\n\n'
                        'The robot policy experiment reported lower tracking error with quantile normalization. '
                        'The baseline still needs a matched-seed evaluation.\n\n'
                        'This suggests that normalization may affect policy stability, because the input ranges differ.\n\n'
                        'For example, the recorded gripper target was clipped at the old threshold.\n\n'
                        '[Related diffusion policy study](https://arxiv.org/abs/2303.04137) is a reference from this discussion.')
        return {'message_id': f'{index:020x}', 'ordinal': index, 'text': text,
                'digest': hashlib.sha256(text.encode()).hexdigest(), 'truncated': False,
                'created': f'2026-10-16T10:{index % 60:02d}:00Z', 'sort_time': 1000 + index,
                'cwd': '/work/robot-learning'}

    def research_sources(self, host, after=0, limit=16):
        self.calls.append(('sources', host, after, limit))
        if self.offline:
            raise BridgeError('Offline')
        return {'epoch': self.epoch, 'items': [self.source()] if self.revision > after else [],
                'next': max(self.revision, after), 'more': False}

    def research_evidence(self, host, session, generation, after=0, limit=16):
        self.calls.append(('evidence', host, after, limit))
        if self.offline:
            raise BridgeError('Offline')
        if generation != self.generation:
            raise BridgeError('Changed')
        items = [item for item in self.items if item['ordinal'] > after][:limit]
        return {'session': session, 'generation': generation, 'items': copy.deepcopy(items),
                'next': items[-1]['ordinal'] if items else after,
                'more': len([item for item in self.items if item['ordinal'] > after]) > limit}


class Hub:
    mesh = None
    provider = 'codex'
    provider_executable = ''
    supervisor_model = 'gpt-6.1-sol'
    supervisor_busy = False

    def __init__(self, root):
        self.state_dir = Path(root)
        self.archives = Sources()
        self.stop = threading.Event()
        self.lock = threading.RLock()
        self.processes = set()
        self.live = Mock()

    @contextmanager
    def db(self):
        db = sqlite3.connect(self.state_dir / 'hub.sqlite')
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()


class ResearchTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.now = 2000
        self.hub = Hub(self.temp.name)
        self.research = Research(self.hub, clock=lambda: self.now)
        self.research.model = Mock(side_effect=self.answer)

    def tearDown(self):
        self.hub.stop.set()
        self.temp.cleanup()

    def answer(self, evidence):
        return {'items': [{'id': item['id'], 'title': 'Quantile normalization needs matched-seed tests',
                           'tldr': 'The experiment reported lower tracking error. A matched-seed comparison is still needed.',
                           'lesson': 'Input normalization may affect policy stability.', 'examples': [],
                           'evidence_quotes': [item['text'].split('\n\n')[1]]} for item in evidence['items']]}

    def populate(self):
        self.research.collect()
        return self.research.snapshot()['items'][0]

    def test_extract_is_auditable_and_does_not_invent_examples_or_papers(self):
        source, message = self.hub.archives.source(), self.hub.archives.message()
        item = extract(source, message)
        self.assertEqual(item['summary_state'], 'excerpt')
        self.assertEqual(item['papers'][0]['title'], 'Related diffusion policy study')
        self.assertEqual(item['papers'][0]['url'], 'https://arxiv.org/abs/2303.04137')
        self.assertIn('gripper', item['examples'][0])
        origin = item['sources'][0]
        self.assertEqual(origin['digest'], message['digest'])
        self.assertIn('archive=' + source['session'], origin['url'])
        self.assertIn('message=' + message['message_id'], origin['url'])
        self.assertIn('generation=' + source['generation'], origin['url'])
        self.assertEqual(extract(source, self.hub.archives.message(text='Deployed. Refresh the hub.')), None)
        text = 'The robot policy evaluation is planned for next week. We have no results yet, so the baseline comparison is still an open research question.'
        tentative = extract(source, self.hub.archives.message(text=text))
        self.assertEqual(tentative['examples'], [])
        self.assertEqual(tentative['papers'], [])
        self.assertIn('no results yet', tentative['tldr'])

    def test_unchanged_history_is_not_refetched_or_resummarized(self):
        item = self.populate()
        self.assertEqual(item['summary_state'], 'ready')
        self.assertNotIn('_evidence', item)
        self.assertEqual(self.research.model.call_count, 1)
        self.research.collect()
        self.assertEqual(len([c for c in self.hub.archives.calls if c[0] == 'evidence']), 1)
        self.assertEqual(self.research.model.call_count, 1)
        # Reads touch neither the network nor the model.
        calls = list(self.hub.archives.calls)
        for _ in range(3):
            self.research.snapshot()
        self.assertEqual(self.hub.archives.calls, calls)

    def test_append_only_reads_new_messages_and_preserves_saved_edits(self):
        item = self.populate()
        self.research.update({'action': 'edit', 'id': item['id'], 'changes': {'title': 'My normalization lesson', 'notes': 'Repeat with fixed seeds.'}})
        self.hub.archives.items.append(self.hub.archives.message(2))
        self.hub.archives.revision += 1
        self.research.collect()
        last = [c for c in self.hub.archives.calls if c[0] == 'evidence'][-1]
        self.assertEqual(last[2], 1)
        restored = Research(self.hub, clock=lambda: self.now).snapshot()['items']
        old = next(card for card in restored if card['id'] == item['id'])
        self.assertEqual(old['title'], 'My normalization lesson')
        self.assertTrue(old['edited'])
        self.assertEqual(len(restored), 2)

    def test_hidden_and_manual_edits_survive_rescan_and_source_rewrite(self):
        item = self.populate()
        self.research.update({'action': 'edit', 'id': item['id'], 'changes': {'tldr': 'My own careful interpretation.'}})
        self.research.update({'action': 'hide', 'id': item['id']})
        self.hub.archives.generation = 'd' * 32
        self.hub.archives.revision += 1
        self.research.collect()
        self.assertEqual(self.research.snapshot()['items'], [])
        hidden = self.research.snapshot(hidden=True)['items'][0]
        self.assertEqual(hidden['tldr'], 'My own careful interpretation.')
        self.assertEqual(hidden['sources'][0]['generation'], 'd' * 32)
        self.assertFalse(hidden['sources'][0]['stale'])
        self.research.update({'action': 'restore', 'id': item['id']})
        self.assertEqual(self.research.snapshot(hidden=True)['items'], [])
        self.assertEqual(self.research.snapshot()['items'][0]['tldr'], 'My own careful interpretation.')

    def test_rewritten_away_messages_keep_labeled_cached_evidence(self):
        old = self.populate()
        self.hub.archives.generation = 'd' * 32
        self.hub.archives.revision += 1
        replacement = self.hub.archives.message(2)
        replacement['ordinal'] = 1
        self.hub.archives.items = [replacement]
        self.research.collect()
        items = self.research.snapshot()['items']
        saved = next(item for item in items if item['id'] == old['id'])
        self.assertTrue(saved['sources'][0]['stale'])
        self.assertEqual(saved['sources'][0]['excerpt'], old['sources'][0]['excerpt'])

    def test_offline_source_does_not_remove_or_overwrite_research(self):
        old = self.populate()
        self.hub.archives.offline = True
        self.research.collect()
        current = Research(self.hub, clock=lambda: self.now).snapshot()
        self.assertEqual(current['items'], [old])
        self.assertEqual(current['status']['unavailable'][0]['host'], 'gpu')
        self.assertFalse(current['items'][0]['sources'][0]['stale'])

    def test_source_epoch_reset_rebuilds_without_duplicating_cards(self):
        old = self.populate()
        self.hub.archives.epoch = 'e' * 32
        self.research.collect()
        self.assertEqual([item['id'] for item in self.research.snapshot()['items']], [old['id']])
        self.assertFalse(self.research.snapshot()['items'][0]['sources'][0]['stale'])

    def test_search_project_and_cursor_pages_are_literal_and_stable(self):
        self.hub.archives.items = [self.hub.archives.message(index) for index in range(1, 71)]
        self.research.summarize = Mock()
        for _ in range(5):
            self.research.collect()
        first = self.research.snapshot(limit=40)
        second = self.research.snapshot(before=first['next'])
        self.assertEqual(len(first['items']), 40)
        self.assertEqual(len(second['items']), 30)
        self.assertFalse({i['id'] for i in first['items']} & {i['id'] for i in second['items']})
        self.assertIsNone(second['next'])
        self.assertEqual(self.research.snapshot(query='normalization')['total'], 70)
        self.assertEqual(self.research.snapshot(query="%' OR 1=1 --")['items'], [])
        self.assertEqual(len(self.research.snapshot(project=first['projects'][0]['id'])['items']), 40)
        self.assertEqual(self.research.snapshot(project='unknown')['items'], [])
        for cursor in ([float('nan'), 'f' * 24], [1, 'bad'], 'bad'):
            with self.assertRaises(BridgeError):
                self.research.snapshot(before=cursor)

    def test_manual_changes_are_validated_and_cannot_rewrite_provenance(self):
        item = self.populate()
        for changes in ({'sources': []}, {'title': ''}, {'tldr': 'x' * 1201}, {'examples': ['x'] * 7}, {'notes': 3}):
            with self.assertRaises(BridgeError):
                self.research.update({'action': 'edit', 'id': item['id'], 'changes': changes})
        self.assertEqual(self.research.snapshot()['items'][0], item)

    def test_invalid_model_quotes_are_rejected_and_extract_stays_available(self):
        self.research.model = Mock(return_value={'items': [{'id': hashed('gpu:' + self.hub.archives.session + ':' + '0' * 19 + '1'),
            'title': 'Invented outcome', 'tldr': 'All robots succeeded.', 'lesson': '', 'examples': [],
            'evidence_quotes': ['This claim was never in the source.']}]})
        item = self.populate()
        self.assertEqual(item['summary_state'], 'excerpt')
        self.assertNotEqual(item['title'], 'Invented outcome')
        # Invalid summaries have a durable backoff, including after a restart.
        new = Research(self.hub, clock=lambda: self.now)
        new.model = Mock()
        new.summarize()
        new.model.assert_not_called()

    def test_edit_while_model_runs_wins_over_generated_summary(self):
        def answer(evidence):
            self.research.update({'action': 'edit', 'id': evidence['items'][0]['id'], 'changes': {'title': 'My title'}})
            return self.answer(evidence)
        self.research.model.side_effect = answer
        self.assertEqual(self.populate()['title'], 'My title')

    def test_busy_supervisor_and_workers_do_no_collection(self):
        self.hub.supervisor_busy = True
        self.research.collect()
        self.assertEqual(self.hub.archives.calls, [])
        self.research.model.assert_not_called()
        self.hub.supervisor_busy = False
        self.hub.mesh = Mock(is_worker=True)
        self.research.collect()
        self.assertFalse(self.research.snapshot()['available'])
        self.assertEqual(self.hub.archives.calls, [])
        with self.assertRaises(BridgeError):
            self.research.update({'action': 'refresh'})

    def test_model_is_tool_disabled_has_durable_budget_and_releases_process(self):
        research = Research(self.hub, clock=lambda: self.now)
        event = {'type': 'item.completed', 'item': {'type': 'agent_message', 'text': '{"items":[]}'}}
        process = Mock(returncode=0)
        process.communicate.return_value = (json.dumps(event), '')
        with patch('research.shutil.which', return_value='/fake/codex'), patch('research.subprocess.Popen', return_value=process) as start:
            for _ in range(MAX_CALLS):
                self.assertEqual(research.model({'items': []}), {'items': []})
            with self.assertRaisesRegex(BridgeError, 'budget'):
                Research(self.hub, clock=lambda: self.now).model({'items': []})
        command = start.call_args.args[0]
        schema = json.loads(Path(command[command.index('--output-schema') + 1]).read_text())
        self.assertEqual(schema['required'], ['items'])
        card_schema = schema['properties']['items']['items']
        self.assertFalse(card_schema['additionalProperties'])
        self.assertEqual(set(card_schema['required']), {'id', 'title', 'tldr', 'lesson', 'examples', 'evidence_quotes'})
        self.assertEqual(set(card_schema['properties']), set(card_schema['required']))
        self.assertIn('agents.enabled=false', command)
        self.assertIn('features.shell_tool=false', command)
        self.assertIn('web_search="disabled"', command)
        self.assertIn('model_reasoning_effort="low"', command)
        self.assertIn('--ignore-user-config', command)
        self.assertIn('--ephemeral', command)
        self.assertNotIn('TMUX', start.call_args.kwargs['env'])
        self.assertEqual(self.hub.processes, set())
        self.assertEqual(len(research.setting('calls')), MAX_CALLS)

    def test_paper_aliases_dedupe_without_replacing_source_urls(self):
        first = 'https://arxiv.org/abs/2303.04137v2#example'
        papers = paper_links('[Study](' + first + ')\n\n'
                             'http://export.arxiv.org/pdf/2303.04137v1.pdf\n'
                             'https://arxiv.org/abs/2303.04137\n'
                             '[Paper](https://doi.org/10.1000/ABC)\n'
                             'http://dx.doi.org/10.1000/abc#section\n'
                             'https://openreview.net/forum?id=AbC\n'
                             'https://openreview.net/pdf?id=AbC')
        self.assertEqual(len(papers), 3)
        self.assertEqual(papers[0]['url'], first)
        self.assertEqual(papers[0]['key'], 'arxiv:2303.04137')
        self.assertEqual(papers[1]['key'], 'doi:10.1000/abc')
        self.assertEqual(papers[2]['key'], 'openreview:AbC')

    def test_nonresearch_classification_is_cached_and_not_shown(self):
        self.research.model.return_value = {'items': []}
        self.research.model.side_effect = None
        self.research.collect()
        self.assertEqual(self.research.snapshot()['items'], [])
        self.assertEqual(self.research.snapshot()['total'], 0)
        self.assertEqual(self.research.snapshot()['projects'], [])
        self.assertEqual(self.research.snapshot()['status']['pending'], 0)
        self.research.collect()
        self.assertEqual(self.research.model.call_count, 1)

    def test_recent_replies_arrive_before_bounded_historical_catch_up(self):
        self.hub.archives.items = [self.hub.archives.message(index) for index in range(1, 201)]
        self.research.summarize = Mock()
        self.research.collect()
        call = [value for value in self.hub.archives.calls if value[0] == 'evidence'][0]
        self.assertEqual(call[2], 152)
        first = self.research.snapshot()['items']
        self.assertEqual(len(first), 16)
        self.assertGreater(min(item['sort_time'] for item in first), 1152)
        for index in range(13):
            self.now += 30
            self.research.collect()
        self.assertEqual(self.research.snapshot()['total'], 200)
        self.assertEqual(self.research.snapshot()['status']['indexing_sources'], 0)

    def test_model_cancels_when_interactive_supervisor_starts(self):
        research = Research(self.hub, clock=lambda: self.now)
        process = Mock(returncode=0)
        def communicate(*args, **kwargs):
            if kwargs:
                self.hub.supervisor_busy = True
                raise subprocess.TimeoutExpired('codex', .5)
            return '', ''
        process.communicate.side_effect = communicate
        with patch('research.shutil.which', return_value='/fake/codex'), patch('research.subprocess.Popen', return_value=process):
            with self.assertRaises(DeferredSummary):
                research.model({'items': []})
        process.kill.assert_called_once()
        self.assertEqual(self.hub.processes, set())

    def test_cancelled_summary_does_not_use_up_card_retry_attempt(self):
        self.research.model.side_effect = DeferredSummary('Interactive priority')
        self.research.collect()
        with self.hub.db() as db:
            row = db.execute('SELECT attempts FROM research_entries').fetchone()
        self.assertEqual(row['attempts'], 0)
        self.assertEqual(self.research.snapshot()['items'][0]['summary_state'], 'excerpt')

    def test_failed_source_does_not_starve_other_pending_sources(self):
        self.populate()
        with self.hub.db() as db:
            for index in range(8):
                source = self.hub.archives.source() | {'session': f'{index:032x}', 'last_ordinal': 100}
                db.execute('INSERT INTO research_sources(host,session,value) VALUES(?,?,?)', ('gpu', source['session'], json.dumps(source)))
        self.research._page = Mock(side_effect=BridgeError('Source changed'))
        self.research.collect()
        self.now += 30
        self.research.collect()
        sessions = {call.args[0]['session'] for call in self.research._page.call_args_list}
        self.assertEqual(len(sessions), 8)
        self.assertTrue(self.research.snapshot()['status']['indexing'])
        self.assertEqual(self.research.snapshot()['status']['backlog'], 8)


if __name__ == '__main__':
    unittest.main()
