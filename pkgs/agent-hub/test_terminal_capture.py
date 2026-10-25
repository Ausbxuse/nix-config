"""Native terminal colours; all terminal writes use an isolated tmux server."""
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

import bridge
from server import Hub
from test_hub import row


class TerminalCaptureTests(unittest.TestCase):
    def test_large_capture_is_recaptured_instead_of_cutting_off_its_style(self):
        with patch.object(bridge, 'run', side_effect=['x' * (2 * 1024 * 1024 + 1), '\x1b[31mred']) as run:
            self.assertEqual(bridge.capture_terminal({'kind': 'local'}, '%1', 2000), '\x1b[31mred')
        self.assertEqual([call.args[1][-1] for call in run.call_args_list], ['-2000', '-1000'])
        self.assertTrue(all('-e' in call.args[1] for call in run.call_args_list))

    def test_terminal_only_adds_styles_when_requested_and_does_not_mutate_reader_cache(self):
        connection = {'id': 'fixture', 'kind': 'local', 'label': 'Fixture'}
        agent = bridge.parse_panes(row(), connection)[0]
        with tempfile.TemporaryDirectory() as directory, patch.object(Hub, 'save_connection'):
            hub = Hub(directory)
            try:
                with patch.object(bridge, 'capture', return_value='plain'), \
                        patch.object(hub.transcripts, 'match', return_value={}), \
                        patch.object(hub, 'agent_observation', return_value={}), \
                        patch.object(hub, 'session_hints', return_value=[]), \
                        patch.object(bridge, 'capture_terminal', return_value='\x1b[31mplain') as raw, \
                        patch.object(bridge, 'terminal_theme', return_value='light') as theme:
                    self.assertNotIn('terminal', hub.local_preview(connection, agent, True))
                    raw.assert_not_called()
                    value = hub.local_preview(connection, agent, True, True)
                    self.assertEqual(value['text'], 'plain')
                    self.assertEqual(value['terminal'], {'ansi': '\x1b[31mplain', 'theme': 'light'})
                    self.assertNotIn('terminal', hub.local_preview(connection, agent, True))
                    hub.local_preview(connection, agent, False, True)
                    theme.assert_called_once_with(connection)
            finally:
                hub.close()

    @unittest.skipUnless(shutil.which('tmux'), 'tmux is required')
    def test_isolated_tmux_preserves_truecolour_backgrounds_and_text_attributes(self):
        with tempfile.TemporaryDirectory() as directory:
            socket = str(Path(directory) / 'tmux.sock')
            script = Path(directory) / 'colours.py'
            script.write_text("import time\nprint('\\x1b[1;31mred bold\\x1b[0m normal', flush=True)\n"
                              "print('\\x1b[38;2;137;180;250;48;2;51;53;62mRGB background\\x1b[0m', flush=True)\n"
                              "print('\\x1b[2mdim\\x1b[0m done', flush=True)\ntime.sleep(30)\n")
            connection = {'kind': 'local', 'socket': socket}
            env = {k: v for k, v in os.environ.items() if k not in ('TMUX', 'TMUX_PANE')}
            command = ['tmux', '-S', socket, '-f', '/dev/null', 'new-session', '-d', '-x', '90', '-y', '24',
                       shlex.join([sys.executable, str(script)])]
            try:
                subprocess.run(command, env=env, check=True, capture_output=True)
                pane = bridge.run(connection, ['list-panes', '-F', '#{pane_id}']).strip()
                deadline = time.monotonic() + 3
                while True:
                    text = bridge.capture_terminal(connection, pane)
                    if 'done' in text:
                        break
                    self.assertLess(time.monotonic(), deadline)
                    time.sleep(.02)
                self.assertIn('\x1b[1m', text)
                self.assertIn('\x1b[31m', text)
                self.assertIn('38;2;137;180;250', text)
                self.assertIn('48;2;51;53;62', text)
                self.assertIn('\x1b[2m', text)
                self.assertNotIn('\x1b', bridge.capture(connection, pane))
                bridge.run(connection, ['set-environment', '-g', 'TMUX_COLOR_SCHEME', 'light'])
                self.assertEqual(bridge.terminal_theme(connection), 'light')
            finally:
                subprocess.run(['tmux', '-S', socket, 'kill-server'], env=env, capture_output=True)


if __name__ == '__main__':
    unittest.main()
