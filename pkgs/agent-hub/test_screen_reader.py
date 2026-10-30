import unittest

import screen_reader as R


class ConversationTests(unittest.TestCase):
    def test_claude_screen(self):
        screen = '\n'.join([
            ' ~/src/app', '❯ claude', '', '❯ fix the build please', '', "● I'll look at the error first.", '',
            '● Bash(gradle build)', '  ⎿  BUILD SUCCESSFUL', '', '● Fixed: a missing import.', '  It builds now.', '',
            '✻ Churned for 16s · done 3:24 PM', '', '※ recap: We fixed the build. Next, run the tests.',
            '  (disable recaps in /config)', '', '────────────────────────', '❯ ', '────────────────────────',
            '  ⏵⏵ auto mode on (shift+tab to cycle) · ← for agents'])
        b = R.conversation(screen)
        self.assertEqual([x['role'] for x in b], ['user', 'assistant', 'tool', 'assistant'])
        self.assertEqual(b[0]['text'], 'fix the build please')
        self.assertEqual(b[3]['text'], 'Fixed: a missing import.\nIt builds now.')

    def test_codex_screen(self):
        screen = '\n'.join(['>_ OpenAI Codex (v0.164.0)', '', '› what changed?', '', '• Explored', '  └ Read app.py', '',
                            '• Two files changed: app.py and README.md.', '', '› Ask Codex to do anything', '',
                            '  GPT-6.1-Sol medium · ~/src · weekly 91% left'])
        self.assertEqual([x['role'] for x in R.conversation(screen)], ['user', 'tool', 'assistant'])

    def test_draft_in_the_box_is_not_a_message(self):
        screen = '\n'.join(['● Done.', '', '────────', '❯ something I am still typing', '────────', '  ⏵⏵ auto mode on'])
        self.assertEqual([x['role'] for x in R.conversation(screen)], ['assistant'])


class QuestionTests(unittest.TestCase):
    def test_menu_with_tabs_hints_and_draft(self):
        screen = '\n'.join([
            "● I've laid out the plan. Let me know your preferences.",
            ' ←  ☐ Horizon rule  ☐ Scaling axes  ☐ CLI command  ✔ Submit  →', '',
            "How should each task's horizon (max episode length) be set?", '',
            '❯ 1. Max success time + 10% (Recommended)', '     Per task: longest first-success time, plus 10%.',
            '  2. 95th-pct success time × 1.2', '     Robust to one freakishly slow success.', '  3. Type something.',
            '────────────────────────────────', '  4. Chat about this', '',
            'Enter to select · Tab/Arrow keys to navigate · Esc to cancel'])
        q = R.question(screen)
        self.assertEqual(q['prompt'], "How should each task's horizon (max episode length) be set?")
        self.assertEqual([o['number'] for o in q['options']], [1, 2, 3, 4])
        self.assertEqual(q['options'][0]['label'], 'Max success time + 10% (Recommended)')
        self.assertEqual(q['options'][0]['detail'], 'Per task: longest first-success time, plus 10%.')
        self.assertEqual(q['options'][3]['detail'], '')

    def test_permission_box_with_draft_below(self):
        screen = '\n'.join([
            '╭──────────────────────────────────────╮', '│ Bash command                         │',
            '│   rm -rf build                       │', '│ Do you want to proceed?              │',
            '│ ❯ 1. Yes                             │', '│   2. No, and tell Claude what to do  │',
            '╰──────────────────────────────────────╯', '────────────────────────────────',
            "❯ something I'm typing in tmux", '────────────────────────────────',
            '  ⏵⏵ auto mode on (shift+tab to cycle)'])
        q = R.question(screen)
        self.assertEqual(q['prompt'], 'Bash command\nrm -rf build\nDo you want to proceed?')
        self.assertEqual([o['label'] for o in q['options']], ['Yes', 'No, and tell Claude what to do'])

    def test_scrolling_menu(self):
        screen = '\n'.join([
            '▝▝ ▝▝    ~/src/public/valkrie', '▔' * 80, '   Select model', '   Switch between Claude models.',
            '   ❯ 1. Default (recommended) ✔  Sonnet 5 · Efficient for routine tasks',
            '     2. Sonnet                   Sonnet 5 · Efficient for routine tasks',
            '   ↓ 3. Fable                    Fable 5.1 · Most capable for your hardest',
            '                                 and longest-running tasks', '      … +3 models',
            '   Enter to set as default · s to use this session only · q to cancel'])
        q = R.question(screen)
        self.assertEqual([o['number'] for o in q['options']], [1, 2, 3])
        self.assertEqual(q['prompt'], 'Select model\nSwitch between Claude models.')
        self.assertEqual(q['options'][0]['label'], 'Default (recommended) ✔')
        self.assertEqual(q['options'][0]['detail'], 'Sonnet 5 · Efficient for routine tasks')
        # Its description runs on under it; the menu's notes after the choices aren't part of it.
        self.assertEqual(q['options'][2]['detail'], 'Fable 5.1 · Most capable for your hardest and longest-running tasks')

    def test_wrapped_question_joins_up(self):
        screen = '\n'.join([
            '   Switch between Claude models. Your pick becomes the default for new',
            '   sessions. For other names, specify with --model.', '', '   ❯ 1. Default', '     2. Sonnet',
            '   ● High effort (default) ←/→ to adjust'])
        q = R.question(screen)
        self.assertEqual(q['prompt'], 'Switch between Claude models. Your pick becomes the default for new sessions. For other names, specify with --model.')
        self.assertEqual([o['detail'] for o in q['options']], ['', ''])

    def test_no_choices(self):
        self.assertIsNone(R.question('● Done.\n\n› Ask Codex to do anything\n\n  ? for shortcuts'))


class PlainTests(unittest.TestCase):
    def test_fresh_codex_screen_has_nothing_to_read(self):
        screen = '\n'.join(['direnv: loading ~/src/public/nix-config/.envrc', 'direnv: using flake', ' ~/src/public/nix-config',
                            '❯ codex', '', '  >_ OpenAI Codex (v0.164.0)', '     ~/src/public/nix-config', '',
                            '› Ask Codex to do anything', '',
                            '  GPT-6.1-Sol medium fast · ~/src/public/nix-config · weekly 91% left · Main [default]',
                            '  ← for agents · ? for shortcuts'])
        self.assertEqual(R.plain(screen).strip(), '')

    def test_keeps_what_the_agent_says(self):
        self.assertEqual(R.plain('› \nAllow it to run `rm -rf build`?\n← for agents').strip(), 'Allow it to run `rm -rf build`?')


if __name__ == '__main__':
    unittest.main()
