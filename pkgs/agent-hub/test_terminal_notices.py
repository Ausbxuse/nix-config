import json
from pathlib import Path
import unittest

import bridge
from notifications import excerpt, meaningful
from projects import project_context, search_agents


class TerminalNoticeTests(unittest.TestCase):
    def test_missing_spinner_or_completion_prose_cannot_confirm_a_codex_handoff(self):
        agent = dict(runtime='codex', state='running', summary='Working', live=True)
        for capture in ('• The runs are complete. I am checking the viewer.\n› ',
                        '• Context compacted · 2m 20s\n› ', '• Ran a check\n  └ done\n› ',
                        '• A result.\n› ', '• Working (1m • esc to interrupt)\n'+'tool output\n'*30+'› '):
            observation = bridge.observation(agent, capture)
            self.assertFalse(observation['completion_confirmed'])
            self.assertNotEqual(observation['state'], 'done')

    def test_completion_must_be_current_and_outside_examples_or_tool_output(self):
        good = '• Saved the traces.\n  Worked for 46m 13s · done 2:22 AM\n\n› '
        agent = dict(runtime='codex', state='running', summary='Working', live=True)
        self.assertTrue(bridge.observation(agent, good)['completion_confirmed'])
        for capture in (good+'Compare again\n• Checking another trace.\n› ',
                        good+'\n• Working (2s • esc to interrupt)\n› ',
                        '• Example:\n```text\nWorked for 5m\n```\n› ',
                        '• Ran cat a log\n  Worked for 5m\n› '):
            self.assertFalse(bridge.observation(agent,capture)['completion_confirmed'])

    def test_claude_activity_survives_dynamic_status_without_changing_conversation(self):
        agent = dict(runtime='claude', state='running', summary='Working', live=True)
        fingerprints = set()
        for seconds in (40, 41, 42):
            capture = ('● Checking the trace.\n'
                       f'· Crafting… (6m {seconds}s · ↓ 31.2k tokens · thought for 1s)           \n'
                       '────────────────────────────────\n❯ \n────────────────────────────────\n'
                       f'  study · Opus 5.5 M · 5h {seconds}% · arbitrary changing footer {seconds}')
            observation = bridge.observation(agent, capture)
            self.assertEqual(observation['state'], 'running')
            self.assertTrue(observation['activity_confirmed'])
            self.assertEqual(observation['activity_elapsed'], 360 + seconds)
            fingerprints.add(observation['activity_fingerprint'])
            self.assertEqual(meaningful(capture), '● Checking the trace.')
            self.assertEqual(excerpt(capture), 'Checking the trace.')
        self.assertEqual(len(fingerprints), 1)
        completed = capture.replace('· Crafting… (6m 42s · ↓ 31.2k tokens · thought for 1s)',
                                    '✻ Brewed for 6m 42s · done Oct 10 at 6:05 PM')
        observation = bridge.observation(agent, completed)
        self.assertEqual(observation['state'], 'done')
        self.assertFalse(observation['activity_confirmed'])

    def test_capture_fixtures_match_the_reader_and_preserve_content(self):
        cases = json.loads(Path(__file__).with_name('terminal-notices-fixtures.json').read_text())
        for case in cases:
            with self.subTest(case=case['name']):
                self.assertEqual(bridge.terminal_content(case['capture']), case['expected'])
                self.assertEqual(bridge.terminal_content(case['expected']), case['expected'])

    def test_repeated_notices_do_not_change_activity_or_notification_evidence(self):
        agent = dict(runtime='codex', state='done', summary='Turn ended', live=True)
        plain = '• The trace is ready.\n› '
        for percent in (25, 10, 5):
            capture = ('• The trace is ready.\n'
                       f'⚠ Heads up, you have less than {percent}% of your weekly limit left. Run /status for a breakdown.\n'
                       '  ↳ Recap: The trace is ready.\n           Duplicate text.\n› ')
            self.assertEqual(bridge.observation(agent, capture)['activity_fingerprint'],
                             bridge.observation(agent, plain)['activity_fingerprint'])
            self.assertEqual(meaningful(capture), meaningful(plain))
            self.assertEqual(excerpt(capture), excerpt(plain))
        # A recap that happens to mention CLI controls is not a live spinner.
        result = bridge.observation(agent, '• Done.\nWorked for 3s\n※ recap: Earlier we used esc to interrupt.\n› ')
        self.assertEqual(result['state'], 'done')
        self.assertFalse(result['activity_confirmed'])

    def test_recaps_cannot_create_supervisor_search_hits_or_project_facts(self):
        agent = dict(id='agent', title='Study', cwd='/study', connection_id='local', connection_label='local',
                     state='done', connection_online=True)
        capture = ('• The real reply.\n\n  ↳ Recap: An unrelated quantile task.\n           Duplicate context.\n\n'
                   '⚠ Heads up, you have less than 5% of your weekly limit left. Run /status for a breakdown.\n› ')
        def preview(*args, **kwargs): return {'text': capture}
        self.assertEqual(search_agents([agent], preview, 'quantile')['matches'], [])
        self.assertEqual(search_agents([agent], preview, 'weekly')['matches'], [])
        evidence = str(project_context([agent], preview))
        self.assertIn('The real reply.', evidence)
        self.assertNotIn('quantile', evidence)
        self.assertNotIn('weekly', evidence)

    def test_codex_footer_shortcuts_do_not_change_activity_or_supervisor_evidence(self):
        agent = dict(runtime='codex', state='done', summary='Turn ended', live=True)
        plain = '• The trace is ready.\n› '
        for hint in ('tab to queue message', 'shift+↑ edit last queued message', 'Future shortcut help'):
            capture = plain + '\n  GPT-6.1-Sol low · /study · weekly 62% left\n  ' + hint
            self.assertEqual(bridge.observation(agent, capture)['activity_fingerprint'],
                             bridge.observation(agent, plain)['activity_fingerprint'])
            self.assertEqual(meaningful(capture), meaningful(plain))
            self.assertEqual(excerpt(capture), excerpt(plain))


if __name__ == '__main__':
    unittest.main()
