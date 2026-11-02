import unittest

import rewind as R

LIST = '\n'.join([
    '❯ Say the word apple.', '● apple', '▔' * 40, '   Rewind', '   Restore the code and/or conversation to the point before…',
    '     Say the word apple.', '     No code changes', '   ❯ (current)', '   Enter to continue · Esc to cancel'])


class RewindTests(unittest.TestCase):
    def test_the_highlighted_row_is_read_below_the_list_heading(self):
        # (Not your message above the list, marked ❯ too.)
        self.assertEqual(R.selected(LIST), '(current)')
        up = LIST.replace('     Say the word apple.', '   ❯ Say the word apple.').replace('   ❯ (current)', '     (current)')
        self.assertEqual(R.selected(up), 'Say the word apple.')
        self.assertIsNone(R.selected('● Done.'))

    def test_a_row_is_the_message_even_cut_short(self):
        self.assertTrue(R.same('Say the word apple.', ' Say the word  apple. '))
        self.assertTrue(R.same('Write a 3000-word story about an owl, in 30…', 'Write a 3000-word story about an owl, in 30 numbered parts.'))
        self.assertFalse(R.same('900-word story about a fox.', 'Write a 3000-word story about an owl.'))
        self.assertFalse(R.same('', 'anything'))

    def test_nothing_is_typed_without_words(self):
        with self.assertRaises(R.bridge.BridgeError):
            R.edit_last({}, {}, 'was', '  ')


if __name__ == '__main__':
    unittest.main()
