import unittest
from lookup import LookupCache, token_usage


class LookupCacheTests(unittest.TestCase):
    def setUp(self):
        self.now = 100
        self.cache = LookupCache(clock=lambda: self.now)
        self.agent = dict(id='gpu:%1:pid1', connection_id='gpu', connection_label='gpu-004', title='lap',
                          cwd='/research', runtime='codex', state='running', activity_fingerprint='v1', connection_online=True)
        self.calls = []

    def read(self, agent_id, **kwargs):
        self.assertTrue(kwargs['history'])
        self.calls.append(agent_id)
        return {'messages': [{'role': 'assistant', 'text': 'Nemo checkpoint fits are running.'}],
                'observation': {'activity_fingerprint': self.agent['activity_fingerprint'], 'state': self.agent['state']}}

    def test_evidence_reused_until_output_changes_or_ttl_expires(self):
        first = self.cache.preview(self.agent, self.read)
        self.assertEqual(self.cache.preview(self.agent, self.read), first)
        self.assertEqual(len(self.calls), 1)
        self.agent['activity_fingerprint'] = 'v2'
        self.cache.prune([self.agent])
        self.cache.preview(self.agent, self.read)
        self.assertEqual(len(self.calls), 2)
        self.now += 30
        self.cache.preview(self.agent, self.read)
        self.assertEqual(len(self.calls), 3)

    def test_renamed_removed_offline_and_replaced_agents_invalidate_answers(self):
        for change in ({'title': 'ablation'}, {'state': 'done'}, {'connection_online': False},
                       {'id': 'gpu:%1:pid2'}, {'connection_id': 'different-host'}):
            with self.subTest(change=change):
                self.cache.preview(self.agent, self.read)
                key = self.cache.answer_key('Which agent runs Nemo?', [self.agent])
                self.cache.remember_answer(key, 'lap on gpu-004.', {'matches': [1]})
                self.assertEqual(self.cache.answer(key), 'lap on gpu-004.')
                changed = {**self.agent, **change}
                self.cache.prune([changed])
                self.assertIsNone(self.cache.answer(self.cache.answer_key('Which agent runs Nemo?', [changed])))
                self.assertEqual(len(self.cache.sources), 0)
        self.cache.preview(self.agent, self.read)
        self.cache.prune([])
        self.assertEqual(self.cache.bytes, 0)

    def test_new_agent_and_different_question_do_not_reuse_answer(self):
        self.cache.preview(self.agent, self.read)
        key = self.cache.answer_key('Which agent runs Nemo?', [self.agent])
        self.cache.remember_answer(key, 'lap.', {'matches': [1]})
        self.assertIsNone(self.cache.answer(self.cache.answer_key('Which agent does not run Nemo?', [self.agent])))
        self.assertIsNone(self.cache.answer(self.cache.answer_key(key[0], [self.agent, {**self.agent, 'id': 'new'}])))

    def test_answer_reuse_does_not_extend_old_source_lifetime(self):
        self.cache.preview(self.agent, self.read)
        self.now += 28
        key = self.cache.answer_key('Which agent?', [self.agent])
        self.cache.remember_answer(key, 'lap.', {'matches': [1]})
        self.now += 1
        self.assertEqual(self.cache.answer(key), 'lap.')
        self.now += 1
        self.assertIsNone(self.cache.answer(key))

    def test_answer_expires_after_fifteen_seconds_without_any_output_change(self):
        self.cache.preview(self.agent, self.read)
        key = self.cache.answer_key('Which agent?', [self.agent])
        self.cache.remember_answer(key, 'lap.', {'matches': [1]})
        self.now += 15
        self.assertIsNone(self.cache.answer(key))
        self.cache.preview(self.agent, self.read)
        self.assertEqual(len(self.calls), 1)

    def test_missing_identity_and_racing_observation_are_not_cached(self):
        unknown = {**self.agent, 'activity_fingerprint': ''}
        self.cache.preview(unknown, self.read)
        self.assertIsNone(self.cache.answer_key('Which agent?', [unknown]))
        self.cache.preview(self.agent, lambda *a, **kw: {'observation': {'activity_fingerprint': 'newer', 'state': 'running'}})
        self.assertEqual(len(self.cache.sources), 0)
        self.assertIsNone(self.cache.answer_key('Which agent?', [{**self.agent, 'connection_online': False}]))

    def test_failed_reads_retry_and_incomplete_searches_do_not_cache_answers(self):
        def fail(*a, **kw): raise OSError('host unavailable')
        with self.assertRaises(OSError): self.cache.preview(self.agent, fail)
        self.cache.preview(self.agent, self.read)
        self.assertEqual(len(self.calls), 1)
        key = self.cache.answer_key('Which agent?', [self.agent])
        for evidence in ({'matches': []}, {'matches': [1], 'unavailable': [1]}, {'matches': [1], 'unsearched_agents': 1}):
            self.cache.remember_answer(key, 'lap.', evidence)
            self.assertIsNone(self.cache.answer(key))

    def test_source_cache_is_bounded(self):
        self.cache.max_entries = 1
        self.cache.preview(self.agent, self.read)
        self.cache.preview({**self.agent, 'id': 'new'}, self.read)
        self.assertEqual(list(self.cache.sources), ['new'])
        self.assertLessEqual(self.cache.bytes, self.cache.max_bytes)
        self.cache.max_bytes = 10
        self.cache.prune([])
        self.cache.preview(self.agent, self.read)
        self.assertEqual(len(self.cache.sources), 0)

    def test_usage_ignores_content_and_invalid_values(self):
        self.assertEqual(token_usage('not json\n[]\n{"type":"turn.completed","usage":{"input_tokens":100,"output_tokens":12,"cached_input_tokens":-1,"private":"text"}}'),
                         {'input_tokens': 100, 'output_tokens': 12})


if __name__ == '__main__': unittest.main()
