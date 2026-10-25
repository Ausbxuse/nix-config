import json
from pathlib import Path
import unittest

from message_content import user_text, visible


class MessageContentTests(unittest.TestCase):
    def test_shared_protocol_fixtures(self):
        for case in json.loads(Path(__file__).with_name('message-content-fixtures.json').read_text()):
            with self.subTest(case['name']):
                self.assertEqual(user_text(case['text']), case['expected'])
                self.assertEqual(visible(dict(role='user', text=case['text'])), bool(case['expected']))
                self.assertTrue(visible(dict(role='assistant', text=case['text'])))
                self.assertTrue(visible(dict(role='tool', text=case['text'])))


if __name__ == '__main__':
    unittest.main()
