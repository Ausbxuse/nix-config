import copy
import unittest

from projects import project_context
from task_facts import TaskFacts, valid_fields


class TaskFactTests(unittest.TestCase):
    def setUp(self):
        self.cache = TaskFacts()
        self.agent = dict(id='gpu:study', cwd='/research', connection_id='gpu', connection_label='gpu',
                          title='Checkpoint study', state='done', connection_online=True)
        self.messages = [
            dict(id='goal', role='user', text='Study whether the two normalizers preserve the same robot reach.'),
            dict(id='old', role='assistant', text='The training run has started.'),
            dict(id='request', role='user', text='Compare the mean_std and quantile checkpoints, using the same evaluation seeds.'),
            dict(id='reply', role='assistant', phase='final_answer', text='Saved both traces. The quantile model reduced the endpoint error by 12%.\n\n'
                 '```text\nDo you want to delete every checkpoint?\n```\n\n'
                 '**Decision:** Which checkpoint should I use for the next evaluation?'),
        ]
        self.preview = dict(source='original', messages=self.messages, observation={'state':'done'})

    def test_current_request_report_and_decision_keep_exact_sources(self):
        value = self.cache.build(self.agent, self.preview)
        fields = {field['kind']: field for field in valid_fields(value, self.messages)}
        self.assertEqual(set(fields), {'request','earlier_request','change','decision'})
        self.assertEqual(fields['request']['source']['message_id'], 'request')
        self.assertEqual(fields['earlier_request']['source']['message_id'], 'goal')
        self.assertIn('12%', fields['change']['text'])
        self.assertEqual(fields['decision']['text'], 'Which checkpoint should I use for the next evaluation?')
        self.assertNotIn('delete', str(fields))
        for field in fields.values():
            source = field['source']
            message = next(m for m in self.messages if m['id']==source['message_id'])
            self.assertEqual(field['text'], message['text'][source['start']:source['end']])

    def test_new_instructions_edits_removals_and_reused_ids_invalidate_old_facts(self):
        old = self.cache.build(self.agent, self.preview)
        self.assertEqual(self.cache.build(self.agent, self.preview), old)
        self.assertEqual(self.cache.hits, 1)
        changed = copy.deepcopy(self.preview)
        changed['messages'][-1]['text'] += '\n\nCorrection: the earlier result was from a different run.'
        self.assertEqual(valid_fields(old, changed['messages']), [])
        revised = self.cache.build(self.agent, changed)
        self.assertNotEqual(old['version'], revised['version'])
        self.assertTrue(next(field for field in revised['fields'] if field['kind']=='change')['text'].startswith('Correction:'))
        changed['messages'].append(dict(id='new', role='user', text='Stop this comparison and check the baseline instead.'))
        self.assertEqual(valid_fields(revised, changed['messages']), [])
        newest = self.cache.build(self.agent, changed)
        self.assertNotIn('change', [field['kind'] for field in newest['fields']])
        self.assertIn('Stop this comparison', newest['fields'][0]['text'])
        self.assertEqual(valid_fields(newest, changed['messages'][:-1]), [])
        self.assertEqual(self.cache.build(self.agent, {'text':'unmatched terminal'}), {})
        self.assertEqual(len(self.cache.cache), 0)

    def test_hosts_cannot_share_facts_and_memory_is_bounded_expiring_and_disposable(self):
        clock = [10]
        cache = TaskFacts(limit=2, byte_limit=8000, ttl=10, clock=lambda:clock[0])
        first = cache.build(self.agent, self.preview)
        other = cache.build({**self.agent, 'id':'other:study'}, self.preview)
        self.assertNotEqual(first['version'], other['version'])
        first['fields'].clear()
        self.assertTrue(cache.build(self.agent, self.preview)['fields'])
        cache.build({**self.agent,'id':'third:study'}, self.preview)
        self.assertEqual(len(cache.cache), 2)
        self.assertLessEqual(sum(item['size'] for item in cache.cache.values()), 8000)
        clock[0] += 11
        cache.build(self.agent, self.preview)
        self.assertEqual(len(cache.cache), 1)
        self.assertEqual(TaskFacts(byte_limit=5).build(self.agent, self.preview)['fields'], other['fields'])

    def test_quotes_code_and_future_plans_do_not_become_a_completed_change(self):
        self.messages[-1]['text'] = 'I will run the final evaluation.\n\n```text\nSaved every checkpoint.\n```\n\n> All tests passed.'
        fields = self.cache.build(self.agent, self.preview)['fields']
        self.assertFalse(any(field['kind']=='change' for field in fields))

    def test_later_tool_output_invalidates_the_completed_brief(self):
        old = self.cache.build(self.agent, self.preview)
        self.messages.append(dict(id='tool',role='tool',kind='command-output',text='The verification command failed.'))
        self.assertEqual(valid_fields(old, self.messages), [])
        updated = self.cache.build(self.agent, self.preview)
        self.assertFalse(any(field['kind']=='change' for field in updated['fields']))

    def test_supervisor_uses_compact_facts_only_with_current_matching_sources(self):
        self.preview['facts'] = self.cache.build(self.agent, self.preview)
        stream = project_context([self.agent], lambda *a, **kw:self.preview)[0]['workstreams'][0]
        self.assertEqual(stream['messages'], [])
        self.assertTrue(stream['facts'])
        self.assertLessEqual(sum(len(field['text']) for field in stream['facts']), 960)
        self.assertNotIn('digest', stream['facts'][0]['source'])
        self.assertEqual(stream['facts'][0]['source']['message_id'], 'request')
        self.messages.append(dict(id='new',role='user',text='Cancel the quantile work.'))
        stale = project_context([self.agent], lambda *a, **kw:self.preview)[0]['workstreams'][0]
        self.assertNotIn('facts', stale)
        self.assertEqual(stale['messages'][-1]['text'], 'Cancel the quantile work.')
        self.preview['facts'] = self.cache.build(self.agent, self.preview)
        self.preview['facts']['agent_id'] = 'another-host:study'
        self.assertNotIn('facts', project_context([self.agent], lambda *a, **kw:self.preview)[0]['workstreams'][0])

    def test_tight_context_budget_drops_whole_excerpts_without_splicing_claims(self):
        self.preview['facts'] = self.cache.build(self.agent, self.preview)
        stream = project_context([self.agent], lambda *a, **kw:self.preview, budget=150)[0]['workstreams'][0]
        self.assertTrue(stream['facts_truncated'])
        self.assertLessEqual(sum(len(field['text']) for field in stream['facts']), 150)
        original = self.preview['facts']['fields']
        self.assertTrue(all(any(field['kind'] == full['kind'] and field['text'] == full['text'] and
                                field['source']['message_id'] == full['source']['message_id']
                                for full in original) for field in stream['facts']))


if __name__ == '__main__':
    unittest.main()
