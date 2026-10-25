import unittest
from updates import Updates, changes, encoded, apply_update


class UpdateTests(unittest.TestCase):
    def test_snapshot_then_small_changes_and_history_append(self):
        updates = Updates()
        before = {'agents': [{'id': 'a', 'state': 'running', 'preview': 'x'*20000}], 'messages': [{'id': str(i), 'text': 'History '+str(i)} for i in range(100)]}
        first = updates.response('state', before)
        after = {**before, 'agents': [{**before['agents'][0], 'state': 'done'}], 'messages': before['messages']+[{'id': 'new', 'text': 'Finished.'}]}
        delta = updates.response('state', after, first['version'])
        self.assertIn('changes', delta)
        self.assertLess(len(encoded(delta)), len(encoded(after))/30)
        self.assertEqual(delta['base'], first['version'])
        self.assertEqual(before['agents'][0]['state'], 'running')

    def test_expired_evicted_wrong_scope_and_unknown_bases_reset(self):
        now = [1]
        updates = Updates(limit=2, ttl=10, clock=lambda: now[0])
        first = updates.response('one', {'data': 'x'*2000})
        self.assertIn('value', updates.response('two', {'data': 'x'*2000}, first['version']))
        self.assertIn('value', updates.response('one', {'data': 'x'*2000}, 'not-a-version'))
        now[0] = 12
        self.assertIn('value', updates.response('one', {'data': 'x'*2000}, first['version']))
        updates.response('two', {'data': 'second'})
        updates.response('three', {'data': 'third'})
        self.assertLessEqual(len(updates.entries), 2)
        bounded = Updates(byte_limit=100)
        bounded.response('large', {'data': 'x'*200})
        self.assertFalse(bounded.entries)

    def test_identical_data_has_no_operations(self):
        self.assertEqual(changes({'value': 'same'}, {'value': 'same'}), [])

    def test_python_apply_roundtrips_nested_array_and_unicode_changes(self):
        updates = Updates()
        before = {'text': '🎉' + 'x' * 1000, 'remove': True, 'items': [{'id': 'a', 'n': 1}, {'id': 'b', 'n': 2}]}
        first = updates.response('peer', before)
        after = {'text': '🎉' + 'x'*500 + ' café 🐈 ' + 'x'*500, 'items': [{'id':'a', 'n': 3}, {'id':'c', 'n':4}, {'id':'b','n':2}], 'added': {'name': 'yes'}}
        packet = updates.response('peer', after, first['version'])
        self.assertIn('changes', packet)
        self.assertEqual(apply_update(first, packet), after)
        self.assertEqual(first['value'], before)
        for packet in ({'version':'new', 'base':'missing', 'changes':[]},
                       {'version':'new', 'base':first['version'], 'changes':[['set',['__proto__'], {}]]},
                       {'version':'new', 'base':first['version'], 'changes':[['text',['text'],999999,2,'x']]}):
            with self.assertRaises(ValueError):
                apply_update(first, packet)
