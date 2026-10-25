"""Terminal queue editing must not send, overwrite, or replay unrelated input."""
import unittest
from unittest.mock import patch

import bridge
import terminal_queue as queue


def screen(messages, draft='Ask Codex to do anything', key='←', state='running'):
    rows = ['• Working (2s • esc to interrupt)' if state == 'running' else '• Done.']
    if messages:
        rows += ['', '• Queued follow-up inputs']
        for message in messages:
            rows.extend(('  ↳ ' if i == 0 else '    ') + line for i, line in enumerate(message.split('\n')))
        rows.append(f'    shift+{key} edit last queued message')
    rows += ['', '› ' + draft.replace('\n', '\n  '), '', '  tab to queue message', '']
    return '\n'.join(rows)


class TerminalQueueTests(unittest.TestCase):
    def setUp(self):
        self.connection = {'kind':'local', 'id':'fixture'}
        self.agent = dict(id='fixture:%1:10:@1:20', pane='%1', runtime='codex', live=True,
                          state='running', summary='Working', in_mode='0', cursor_x='2', cursor_y='1')

    def test_plain_latest_item_has_an_edit_control_without_a_transcript(self):
        result = queue.snapshot(self.agent, screen(['First', 'Second\nKeep this line.']))
        self.assertEqual([item['text'] for item in result['items']], ['First', 'Second\nKeep this line.'])
        self.assertEqual([item['editable'] for item in result['items']], [False, True])
        self.assertTrue(all(not item['removable'] for item in result['items']))
        self.assertEqual(result['key'], 'S-Left')
        combined = queue.merge({'status':'available','items':[],'version':'api','thread_id':'thread'}, result)
        self.assertEqual(len(combined['items']), 2)
        self.assertTrue(combined['suppress_terminal'])

    def test_separate_native_queue_is_retained_and_unknown_terminal_is_not_hidden(self):
        native = dict(status='available', items=[dict(id='native')], version='api', thread_id='thread')
        result = queue.merge(native, queue.snapshot(self.agent, screen(['Terminal'])))
        self.assertEqual(result['items'][0]['id'], 'native')
        self.assertEqual(result['items'][1]['text'], 'Terminal')
        self.assertFalse(queue.merge(native, None)['suppress_terminal'])
        self.assertIsNone(queue.merge(None, None))

    def test_hints_recaps_history_and_rich_inputs_are_not_editable_queue_items(self):
        for text in ('• A result\n  ↳ ordinary output\n› ', screen(['Old']) + '\n• Later answer\n› ',
                     screen(['Bad']).replace('    shift+← edit', '    alt+← edit')):
            self.assertIsNone(queue.snapshot(self.agent, text))
        for text in ('[Image #1] Look at this', '[Pasted Content 5000 chars]', '@file.txt Read this', '/model', '!ls', 'A truncated preview\n…'):
            self.assertFalse(queue.snapshot(self.agent, screen([text]))['items'][0]['editable'])
        self.assertEqual(queue.snapshot(self.agent, screen(['Latest'], key='↑'))['key'], 'S-Up')

    def test_all_queue_changes_and_process_changes_invalidate_the_edit(self):
        first = queue.snapshot(self.agent, screen(['Earlier', 'Latest']))
        for agent, text in ((self.agent, screen(['Earlier changed', 'Latest'])),
                            (self.agent, screen(['Latest'])),
                            ({**self.agent, 'id':'replacement'}, screen(['Earlier','Latest']))):
            self.assertNotEqual(first['version'], queue.snapshot(agent, text)['version'])

    def test_capture_padding_does_not_evict_the_identity_anchor(self):
        text = '\n'.join(line.ljust(600) for line in ('Visible response with the original session identity', *('Tool output' for _ in range(80))))
        with patch.object(bridge, 'run', return_value=text):
            result = bridge.capture(self.connection, '%1', 150)
        self.assertTrue(result.startswith('Visible response'))
        self.assertLess(len(result), 2000)

    def test_editor_verification_requires_the_complete_original_draft(self):
        self.assertTrue(queue._editor_matches(screen([], 'First\n\nThird'), 'First\n\nThird'))
        self.assertFalse(queue._editor_matches(screen([], 'First\nNot ours'), 'First'))
        self.assertFalse(queue._editor_matches(screen([], 'Different'), 'First'))

    def exercise(self, *, changed=False, draft=False, pop_wrong=False, ended=False):
        messages = ['Keep earlier input.', 'Original\nSecond line.']
        initial = queue.snapshot(self.agent, screen(messages))['items'][-1]
        phase, typed, keys, pastes = 'initial', '', [], []
        if changed:
            messages[-1] = 'Changed at terminal'
        def capture(*args):
            return screen(messages, typed or ('Someone is typing' if draft else 'Ask Codex to do anything'),
                          state='done' if ended and phase == 'pasted' else 'running')
        def run(connection, args, **kwargs):
            nonlocal phase, typed
            if args[0] == 'capture-pane':
                return capture()
            if args[0] == 'load-buffer':
                typed = kwargs['stdin'];pastes.append(typed);phase='pasted';return ''
            self.assertEqual(args[0], 'send-keys')
            pressed=args[3:];keys.extend(pressed)
            if pressed == ['S-Left']:
                typed = 'Other terminal draft' if pop_wrong else messages.pop();phase='popped'
            elif pressed[0] == 'C-u':
                self.assertEqual(pressed, ['C-u','BSpace','C-u']);typed='';phase='cleared'
            elif pressed == ['Tab']:
                messages.append(typed);typed='';phase='saved'
            return ''
        with patch.object(bridge, 'verify_agent', return_value=self.agent), patch.object(bridge, 'capture', side_effect=capture), \
                patch.object(bridge, 'run', side_effect=run), patch.object(bridge, 'empty_codex_editor', side_effect=lambda *args:not typed and not draft), \
                patch.object(queue.time, 'monotonic', side_effect=range(100)), patch.object(queue.time, 'sleep'):
            error=None
            try:queue.change(self.connection,self.agent,initial['id'],initial['digest'],'Revised\nKeep newline.')
            except bridge.BridgeError as exc:error=str(exc)
        return messages,typed,keys,pastes,error

    def test_edit_replaces_only_latest_input_and_never_uses_enter_or_interrupt(self):
        messages,draft,keys,pastes,error=self.exercise()
        self.assertIsNone(error)
        self.assertEqual(messages,['Keep earlier input.','Revised\nKeep newline.'])
        self.assertEqual(pastes,['Revised\nKeep newline.'])
        self.assertEqual(keys,['S-Left','C-u','BSpace','C-u','Tab'])
        self.assertEqual(draft,'')

    def test_changed_queue_and_existing_draft_send_no_keys(self):
        for option in ('changed','draft'):
            *_,keys,pastes,error=self.exercise(**{option:True})
            self.assertTrue(error)
            self.assertEqual(keys,[])
            self.assertEqual(pastes,[])

    def test_unexpected_pop_is_never_cleared_or_submitted(self):
        _,draft,keys,pastes,error=self.exercise(pop_wrong=True)
        self.assertTrue(error)
        self.assertEqual(keys,['S-Left'])
        self.assertEqual(pastes,[])
        self.assertEqual(draft,'Other terminal draft')

    def test_turn_ending_leaves_the_revision_as_a_draft(self):
        messages,draft,keys,pastes,error=self.exercise(ended=True)
        self.assertIn('turn ended',error.lower())
        self.assertEqual(draft,'Revised\nKeep newline.')
        self.assertNotIn('Tab',keys)
        self.assertEqual(messages,['Keep earlier input.'])


if __name__ == '__main__':
    unittest.main()
