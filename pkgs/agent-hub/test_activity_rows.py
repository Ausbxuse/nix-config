import re
import unittest

import bridge
import screen_reader

# Claude Code's spinner through a turn, as it draws it (2.1.310), and lines that aren't it.
WORKING = [
    "✽ Deciphering… (running UserPromptSubmit hook · 0s)",
    "* Deciphering…",
    "✶ Deciphering… (1s · thinking)",
    "✢ Deciphering… (7s · ↓ 817 tokens · thought for 6s)",
    "· Deciphering… (9s · ↓ 1.2k tokens)",
    "✶ Deciphering… (running Stop hook · 21s · ↓ 1.7k tokens)",
    "· Generating… (9m 33s · ↓ 11.9k tokens · thought for 2s)",
    "• Working (12s • esc to interrupt)",
]
NOT_WORKING = [
    "● I'll look into it… after lunch.",
    "✻ Baked for 1m 13s",
    "● Hmm… let me think about that.",
    "  ⎿  Tip: Use /btw to ask a quick side question",
    "❯ ",
]


class ActivityRows(unittest.TestCase):
    def test_the_spinner_is_working_at_every_step_of_a_turn(self):
        for line in WORKING:
            self.assertTrue(bridge.ACTIVITY_ROW.match(line), line)
            self.assertTrue(screen_reader.ACTIVITY.match(line), line)
        for line in NOT_WORKING:
            self.assertFalse(bridge.ACTIVITY_ROW.match(line), line)
            self.assertFalse(screen_reader.ACTIVITY.match(line), line)

    def test_a_thinking_agent_is_running(self):
        pane = {"state": "idle", "summary": "", "live": True, "runtime": "claude"}
        screen = "● Sure.\n\n✶ Deciphering… (41s · thinking)\n\n" + "─" * 40 + "\n❯ \n" + "─" * 40 + "\n  status\n"
        self.assertEqual(bridge.classify(pane, screen)[0], "running")


class Interrupted(unittest.TestCase):
    RULE = "─" * 40

    def test_an_interrupted_turn_after_your_message(self):
        screen = ("❯ bake round 21\n\n● Render 1 is running now.\n\n  Ran 1 shell command\n  ⎿  Interrupted · What should Claude do instead?\n"
                  + self.RULE + "\n❯ \n" + self.RULE + "\n  status\n")
        self.assertTrue(bridge.interrupted(screen))

    def test_a_reply_being_written_isnt_interrupted(self):
        screen = ("❯ go on\n\n● Packing a moving truck: the order that works\n\n  The guiding rule is last in, first out.\n"
                  + self.RULE + "\n❯ \n" + self.RULE + "\n  status\n")
        self.assertFalse(bridge.interrupted(screen))
        # An interruption before your latest message is over.
        self.assertFalse(bridge.interrupted("  ⎿  Interrupted · What should Claude do instead?\n\n❯ try again\n\n● Sure.\n"
                                            + self.RULE + "\n❯ \n" + self.RULE + "\n"))


if __name__ == "__main__":
    unittest.main()
