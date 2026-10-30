import unittest
from types import SimpleNamespace
from unittest.mock import patch

import bridge
from claude_models import ClaudeModels, empty_editor, effort_cache_confirmation, picker


CACHE_NOTICE = '''Change effort level?
Your next response will be slower and use more tokens

This conversation is cached for the current effort level. Switching to high means the full history gets re-read on
your next message.

❯ 1. Yes, switch to high
  2. No, go back
'''


class Terminal:
    """Inert picker; Enter is allowed only in its exact cache confirmation."""
    ids = ['Opus', 'Sonnet']
    efforts = ['low', 'medium', 'high']

    def __init__(self):
        self.current, self.effort = 'Opus', 'medium'
        self.selected, self.slider = self.current, self.effort
        self.open = False
        self.keys = []
        self.commands = []
        self.draft = ''
        self.wrap = False
        self.needs_cache_confirmation = False
        self.confirming = False
        self.agent = {'pane':'%1', 'cursor_x':'2', 'cursor_y':'2', 'in_mode':'0'}
        self.binding = {'session':'fixture-session', 'pid':10, 'start':20}

    def screen(self):
        if self.confirming:
            return CACHE_NOTICE.replace('high',self.slider)
        if not self.open:
            return 'Set model to '+self.current+'\n────────────\n❯ '+self.draft+'\n────────────'
        rows = ['Select model']
        for i, name in enumerate(self.ids):
            rows.append(('❯ ' if name == self.selected else '  ')+str(i+1)+'. '+name+(' ✔' if name == self.current else '')+'  Model description')
        rows += [self.slider.title()+' effort', 'Enter to set as default · s to use this session only · Esc to cancel']
        return '\n'.join(rows)

    def paste(self, connection, agent, text):
        assert not self.draft and not self.open and text == '/model'
        self.commands.append(text)
        self.open = True
        self.selected, self.slider = self.current, self.effort

    def run(self, connection, args):
        if args[0] == 'capture-pane':
            return self.screen()
        assert args[:3] == ['send-keys', '-t', '%1']
        key = args[3];self.keys.append(key)
        if key == 'Enter':
            assert self.confirming, 'Enter must never save global defaults in the picker'
            self.current, self.effort = self.selected, self.slider
            self.confirming = False
            return
        assert self.open
        if key in ('Down', 'Up'):
            self.selected = self.ids[max(0, min(len(self.ids)-1, self.ids.index(self.selected)+(1 if key=='Down' else -1)))]
        elif key in ('Left', 'Right'):
            index = self.efforts.index(self.slider)+(1 if key=='Right' else -1)
            self.slider = self.efforts[index % len(self.efforts) if self.wrap else max(0, min(len(self.efforts)-1, index))]
        elif key == 's':
            if self.needs_cache_confirmation:
                self.confirming = True
            else:
                self.current, self.effort = self.selected, self.slider
            self.open = False
        elif key == 'Escape':
            self.open = False
        else:
            raise AssertionError('Unexpected or global-default key: '+key)


class ClaudeModelTests(unittest.TestCase):
    def setUp(self):
        self.term = Terminal()
        self.adapter = ClaudeModels(SimpleNamespace(binding=lambda *args:self.term.binding.copy()))
        for name, side_effect in [('verify_agent', lambda *args:self.term.agent.copy()),
                                  ('observation', lambda *args:{}), ('paste', self.term.paste), ('run', self.term.run)]:
            patched = patch.object(bridge, name, side_effect=side_effect);patched.start();self.addCleanup(patched.stop)
        # Terminal rendering is immediate in this fixture.
        patched = patch.object(self.adapter, 'wait_picker', side_effect=lambda *args, **kwargs:picker(self.term.screen()))
        patched.start();self.addCleanup(patched.stop)

    def test_read_cancels_and_apply_uses_only_session_key_then_verifies(self):
        before = self.adapter.settings({}, self.term.agent)
        self.assertEqual((before['model'],before['effort']), ('Opus','medium'))
        self.assertEqual([row['default_effort'] for row in before['models']], ['medium','medium'])
        self.assertFalse(self.term.open)
        self.assertNotIn('s', self.term.keys)
        change = {**before,'model':'Sonnet','effort':'high'}
        after = self.adapter.settings({}, self.term.agent, change)
        self.assertEqual((after['model'],after['effort']), ('Sonnet','high'))
        self.assertNotEqual(after['version'],before['version'])
        self.assertEqual(self.term.keys.count('s'),1)
        self.assertEqual(self.term.commands,['/model','/model','/model'])
        self.assertFalse(self.term.open)

    def test_stale_settings_session_and_invalid_choices_never_apply(self):
        before = self.adapter.settings({}, self.term.agent)
        for change in ({'session':'other'}, {'version':'stale'}, {'model':'unknown'}, {'effort':'ultra'}):
            with self.subTest(change=change), self.assertRaises(bridge.BridgeError):
                self.adapter.settings({}, self.term.agent, {**before, **change})
            self.assertFalse(self.term.open)
            self.assertNotIn('s',self.term.keys)

    def test_terminal_draft_and_open_menu_are_preserved(self):
        self.term.draft = 'Unsent user text'
        with self.assertRaisesRegex(bridge.BridgeError,'draft'):
            self.adapter.settings({}, self.term.agent)
        self.assertEqual(self.term.draft,'Unsent user text')
        self.term.draft = '';self.term.open = True
        with self.assertRaisesRegex(bridge.BridgeError,'menu'):
            self.adapter.settings({}, self.term.agent)
        self.assertTrue(self.term.open)
        self.assertEqual(self.term.commands,[])
        self.assertEqual(self.term.keys,[])

    def test_working_session_can_change_effort_without_interrupting(self):
        with patch.object(bridge,'observation',return_value={'activity_confirmed':True}):
            before = self.adapter.settings({}, self.term.agent)
            after = self.adapter.settings({}, self.term.agent, {**before,'effort':'high'})
        self.assertEqual((after['model'],after['effort']),('Opus','high'))
        self.assertFalse(self.term.open)
        self.assertNotIn('C-c',self.term.keys)
        self.assertNotIn('Enter',self.term.keys)

    def test_current_wrapping_slider_discovers_all_efforts_and_preserves_defaults(self):
        self.term.wrap = True
        self.term.efforts = ['low','medium','high','xhigh','max','ultracode']
        for current in self.term.efforts:
            with self.subTest(current=current):
                self.term.effort = current
                self.adapter.catalog.clear()
                before = self.adapter.settings({}, self.term.agent)
                self.assertEqual(before['effort'],current)
                self.assertEqual(self.term.effort,current)
                self.assertEqual(before['models'][0]['efforts'],self.term.efforts)
                after = self.adapter.settings({}, self.term.agent, {**before,'effort':'max'})
                self.assertEqual(after['effort'],'max')
                self.assertFalse(self.term.open)

    def test_unknown_effort_cancels_picker_without_applying(self):
        self.term.effort = 'future'
        with self.assertRaisesRegex(bridge.BridgeError,'could not be verified'):
            self.adapter.settings({}, self.term.agent)
        self.assertNotIn('s',self.term.keys)
        self.assertFalse(self.term.open)

    def test_apply_handles_only_the_requested_efforts_cache_notice(self):
        before = self.adapter.settings({}, self.term.agent)
        self.term.needs_cache_confirmation = True
        after = self.adapter.settings({}, self.term.agent, {**before,'effort':'high'})
        self.assertEqual((after['model'],after['effort']),('Opus','high'))
        self.assertEqual(self.term.keys.count('s'),1)
        self.assertEqual(self.term.keys.count('Enter'),1)
        self.assertFalse(self.term.confirming)
        self.assertFalse(self.term.open)

    def test_cache_notice_match_requires_exact_kind_effort_and_selection(self):
        self.assertTrue(effort_cache_confirmation(CACHE_NOTICE,'high'))
        self.assertTrue(effort_cache_confirmation(CACHE_NOTICE.replace('high','xHigh'),'ultracode'))
        self.assertFalse(effort_cache_confirmation(CACHE_NOTICE,'low'))
        for unsafe in (
                CACHE_NOTICE.replace('Your next response will be slower and use more tokens','A PreModelSwitch hook asked you to confirm'),
                CACHE_NOTICE.replace('❯ 1.','1.').replace('2. No','❯ 2. No'),
                CACHE_NOTICE.replace('Change effort level?','Switch model?'),
                CACHE_NOTICE.replace('1. Yes','1. Allow all'),
                CACHE_NOTICE+'Another confirmation',
        ):
            with self.subTest(screen=unsafe):
                self.assertFalse(effort_cache_confirmation(unsafe,'high'))

    def test_picker_requires_the_session_only_contract(self):
        self.term.open = True;screen = self.term.screen()
        self.assertEqual(picker(screen)['selected'],'Opus')
        self.assertIsNone(picker(screen.replace('s to use this session only','Enter to continue')))
        self.assertIsNone(picker(screen.replace('Select model','Model output')))
        self.assertIsNone(picker(screen.replace('❯ ', '  ')))
        self.term.open = False
        self.assertTrue(empty_editor(self.term.agent,self.term.screen()))
        self.assertFalse(empty_editor({**self.term.agent,'cursor_x':5},self.term.screen()))
        self.assertFalse(empty_editor({**self.term.agent,'in_mode':'1'},self.term.screen()))

    def test_a_grey_suggestion_in_the_box_is_not_a_draft(self):
        screen = self.term.screen()
        row = screen.splitlines()[2]
        suggested = screen.replace(row, '❯\xa0Try "refactor AgentHub.kt"')
        styled = lambda rest: '\n'.join(l if i != 2 else '\x1b[39m❯\xa0' + rest for i, l in enumerate(suggested.splitlines()))
        self.assertTrue(empty_editor(self.term.agent, suggested, styled('\x1b[2mTry "refactor AgentHub.kt"\x1b[0m')))
        self.assertFalse(empty_editor(self.term.agent, suggested), 'without the styling it may be typed')
        self.assertFalse(empty_editor(self.term.agent, suggested, styled('Try "refactor AgentHub.kt"')), 'typed text')
        self.assertFalse(empty_editor(self.term.agent, suggested, styled('\x1b[2mTry\x1b[22m "refactor"')), 'partly typed')


if __name__ == '__main__':
    unittest.main()
