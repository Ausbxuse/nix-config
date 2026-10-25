import unittest
from branching import fork_message
from bridge import BridgeError


class BranchTests(unittest.TestCase):
    def client(self, turns):
        self.calls = []
        owner = self
        class Fake:
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def call(self, method, params):
                owner.calls.append((method, params))
                if method == 'thread/read': return {'thread': {'turns': turns}}
                if method == 'thread/fork': return {'thread': {'id': 'new', 'turns': turns[:next(i for i, t in enumerate(turns) if t['id'] == params['lastTurnId']) + 1]}}
                return {}
        return Fake

    def turn(self, id, text='original', status='completed'):
        return {'id': id, 'status': status, 'items': [{'type': 'userMessage', 'content': [{'type': 'text', 'text': text}]}]}

    def message(self, turn):
        return {'text': 'original', 'fork': {'thread': 'parent', 'turn': turn}}

    def test_older_message_excludes_selected_and_future_turns(self):
        result = fork_message(self.message('second'), self.client([self.turn('first'), self.turn('second'), self.turn('future')]))
        self.assertEqual(result, 'new')
        self.assertEqual(self.calls[-1], ('thread/fork', {'threadId': 'parent', 'lastTurnId': 'first'}))

    def test_first_message_rolls_back_only_new_branch(self):
        fork_message(self.message('first'), self.client([self.turn('first'), self.turn('future')]))
        self.assertEqual(self.calls[-1], ('thread/rollback', {'threadId': 'new', 'numTurns': 1}))

    def test_changed_message_rejected_before_fork(self):
        with self.assertRaises(BridgeError):
            fork_message(self.message('first'), self.client([self.turn('first', 'different')]))
        self.assertFalse(any(method == 'thread/fork' for method, _ in self.calls))

    def test_active_first_turn_rejected(self):
        with self.assertRaises(BridgeError):
            fork_message(self.message('first'), self.client([self.turn('first', status='inProgress')]))

    def test_mid_turn_steering_rejected(self):
        turn = self.turn('first')
        turn['items'].append(turn['items'][0])
        with self.assertRaises(BridgeError):
            fork_message(self.message('first'), self.client([turn]))

if __name__ == '__main__': unittest.main()
