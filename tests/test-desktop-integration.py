#!/usr/bin/env python3
"""Exercise agent state and clipboard/environment behavior without a desktop."""

import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import tempfile
import time
import unittest


REPO = Path(__file__).resolve().parents[1]
WORKBENCH = REPO / "modules/home/tmux/workbench.sh"


class DesktopIntegrationTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="tmux-desktop-test-")
        self.root = Path(self.temp.name)
        self.home = self.root / "home"
        self.home.mkdir()
        self.screen = self.root / "screen"
        self.screen.write_text("› Ask Codex to do anything\n")
        self.tmux_cmd = [shutil.which("tmux"), "-S", str(self.root / "socket")]
        self.env = dict(os.environ, HOME=str(self.home),
                        XDG_STATE_HOME=str(self.root / "state"),
                        TMUX_WORKBENCH_SESSION="test", WORKBENCH_SYNC_INTERVAL_MS="1")
        self.env.pop("TMUX", None)
        self.env.pop("TMUX_PANE", None)
        self.tmux("-f", "/dev/null", "new-session", "-d", "-s", "test", "sleep 3600")
        self.pane = self.tmux("display", "-p", "-t", "test", "#{pane_id}")
        self.bin = self.root / "bin"
        self.bin.mkdir()
        wrapper = self.bin / "tmux"
        wrapper.write_text(
            f"#!{shutil.which('bash')}\n"
            'if [[ "$1" == capture-pane ]]; then\n'
            f"  cat {shlex.quote(str(self.screen))}\n"
            "else\n"
            f"  exec {shlex.join(self.tmux_cmd)} \"$@\"\n"
            "fi\n"
        )
        wrapper.chmod(0o755)
        self.env.update(TMUX_BIN=str(wrapper), TMUX_WORKBENCH_TARGET_PANE=self.pane,
                        PATH=str(self.bin) + os.pathsep + os.environ["PATH"])
        self.option("@agent-pane", self.pane)
        self.option("@agent-state", "running")
        self.option("@agent-summary", "working")
        self.option("@agent-updated", "1")

    def tearDown(self):
        subprocess.run(self.tmux_cmd + ["kill-server"], env=self.env,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.temp.cleanup()

    def tmux(self, *args):
        return subprocess.check_output(self.tmux_cmd + list(args), env=self.env,
                                       text=True).strip()

    def option(self, name, value=None):
        if value is None:
            return self.tmux("show-options", "-wqv", "-t", self.pane, name)
        self.tmux("set-option", "-w", "-t", self.pane, name, value)

    def transcript(self, event, unchanged=False):
        file = self.home / ".codex/sessions/cached.jsonl"
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text(json.dumps({"type": "event_msg", "payload": {"type": event}},
                                   separators=(",", ":")) + "\n")
        self.option("@agent-session-file", str(file))
        self.option("@agent-session-scan", "0")
        if unchanged:
            self.option("@agent-session-sig", f"{int(file.stat().st_mtime)}:{file.stat().st_size}")

    def sync(self):
        subprocess.run(["bash", str(WORKBENCH), "sync", "test"], env=self.env,
                       check=True, capture_output=True, text=True, timeout=10)

    def test_cached_completion_survives_missing_process_fd_and_empty_summary(self):
        self.transcript("task_complete")
        self.tmux("set-option", "-wu", "-t", self.pane, "@agent-summary")
        self.sync()
        self.assertEqual(self.option("@agent-state"), "done")
        self.assertEqual(self.option("@agent-summary"), "needs review")
        self.assertGreaterEqual(int(self.option("@agent-session-scan")), int(time.time()) - 5)

    def test_quoted_working_and_approval_text_does_not_override_completion(self):
        self.transcript("task_complete")
        self.screen.write_text(
            '    markers: ["working (", "esc to interrupt", "would you like to run"]\n'
            "› Ask Codex to do anything\n"
        )
        self.sync()
        self.assertEqual(self.option("@agent-state"), "done")

    def test_interrupt_clears_running_with_unchanged_transcript(self):
        self.transcript("task_started", unchanged=True)
        self.sync()
        self.assertEqual(self.option("@agent-state"), "idle")

    def test_current_working_footer_overrides_previous_completion(self):
        self.transcript("task_complete")
        self.screen.write_text("• Working (5s • esc to interrupt)\n› Ask Codex to do anything\n")
        self.sync()
        self.assertEqual(self.option("@agent-state"), "running")

    def test_current_approval_overrides_transcript(self):
        self.transcript("task_started")
        self.screen.write_text("Would you like to run the following command?\n1. Yes\n2. No\n")
        self.sync()
        self.assertEqual(self.option("@agent-state"), "waiting")

    def test_done_remains_visible_when_transcript_is_unavailable(self):
        self.option("@agent-state", "done")
        self.sync()
        self.assertEqual(self.option("@agent-state"), "done")

    def test_agent_colors_take_precedence_over_old_bells(self):
        for theme in ("dark", "light"):
            self.tmux("source-file", str(REPO / f"modules/home/tmux/theme-{theme}.conf"))
            for name in ("@workbench-agent-window-number-style",
                         "@workbench-agent-window-current-number-style"):
                style = self.tmux("show-options", "-gv", name)
                # Force the bell condition true without sending a terminal bell.
                self.tmux("set-option", "-g", name,
                          style.replace("window_bell_flag", "#{==:1,1}"))
                for state, color in (("running", "#5fb3c4"), ("done", "#9ece6a"),
                                     ("waiting", "#d6a65d"), ("blocked", "#ff5c57")):
                    with self.subTest(theme=theme, state=state, format=name):
                        self.option("@agent-state", state)
                        self.assertEqual(self.tmux("display", "-p", "-t", self.pane,
                                                   "#{E:" + name + "}"), "fg=" + color)

    def test_restored_shell_imports_desktop_environment_for_child_programs(self):
        source = (REPO / "modules/home/zsh/zshrc").read_text()
        function = source.split("  __tmux_import_desktop_environment() {", 1)[1]
        function = "__tmux_import_desktop_environment() {" + function.split(
            "\n  __tmux_import_desktop_environment\n", 1)[0]
        helper = self.root / "desktop.zsh"
        helper.write_text(function)
        for key, value in (("DISPLAY", ":99"), ("WAYLAND_DISPLAY", "wayland-test"),
                           ("XDG_CURRENT_DESKTOP", "GNOME")):
            self.tmux("set-environment", "-t", "test", key, value)
        env = dict(self.env, TMUX_PANE=self.pane)
        for key in ("DISPLAY", "WAYLAND_DISPLAY", "XDG_CURRENT_DESKTOP", "SSH_CONNECTION"):
            env.pop(key, None)
        script = 'source "$1"; __tmux_import_desktop_environment; bash -c \'printf "%s\\n" "$DISPLAY" "$WAYLAND_DISPLAY" "$XDG_CURRENT_DESKTOP"\''
        result = subprocess.check_output(["zsh", "-f", "-c", script, "test", str(helper)],
                                         env=env, text=True)
        self.assertEqual(result.splitlines(), [":99", "wayland-test", "GNOME"])
        env.update(SSH_CONNECTION="remote", DISPLAY="localhost:10.0")
        result = subprocess.check_output(["zsh", "-f", "-c", script, "test", str(helper)],
                                         env=env, text=True)
        self.assertEqual(result.splitlines(), ["localhost:10.0", "", ""])

    def test_yank_and_paste_never_use_wl_clipboard_after_late_desktop_attach(self):
        script = self.root / "clipboard.lua"
        script.write_text("""vim.opt.rtp:prepend(""" + json.dumps(str(REPO / "modules/home/nvim/nvim")) + """)
vim.env.TMUX = 'test'
vim.env.TMUX_PANE = '%1'
vim.env.SSH_CONNECTION = nil
vim.env.DISPLAY = nil
vim.env.WAYLAND_DISPLAY = nil
vim.env.XDG_CURRENT_DESKTOP = nil
local desktop_ready = false
local helper_available = true
local copied = ''
local external_text
local desktop_reads = 0
local executable = vim.fn.executable
vim.fn.executable = function(name)
  if name == 'nvim-gnome-clipboard' then return helper_available and 1 or 0 end
  if name == 'tmux' or name == 'wl-paste' or name == 'wl-copy' then return 1 end
  return executable(name)
end
package.loaded['vim.ui.clipboard.osc52'] = {
  copy = function()
    return function(lines) copied = table.concat(lines, '\\n') end
  end,
}
vim.system = function(command)
  assert(command[1] ~= 'wl-paste' and command[1] ~= 'wl-copy', 'wl-clipboard was invoked')
  local stdout
  if command[1] == 'tmux' then
    stdout = desktop_ready and 'DISPLAY=:99\\nWAYLAND_DISPLAY=wayland-test\\nXDG_CURRENT_DESKTOP=GNOME\\n' or ''
  else
    assert(command[1] == 'nvim-gnome-clipboard' and command[#command] == 'paste')
    desktop_reads = desktop_reads + 1
    stdout = external_text or copied
  end
  return { wait = function() return { code = 0, stdout = stdout } end }
end
require 'config.options'
desktop_ready = true
vim.cmd 'unlet! g:loaded_clipboard_provider'
vim.cmd 'runtime autoload/provider/clipboard.vim'
vim.api.nvim_buf_set_lines(0, 0, -1, false, { 'first', 'second' })
vim.cmd 'normal! yy'
assert(copied == 'first\\n', 'linewise yank lost its newline: ' .. vim.inspect(copied))
vim.cmd 'normal! p'
assert(vim.deep_equal(vim.api.nvim_buf_get_lines(0, 0, -1, false), { 'first', 'first', 'second' }))
assert(desktop_reads > 0, 'the desktop clipboard was not read')
external_text = 'external\\n'
vim.cmd 'normal! p'
assert(vim.api.nvim_buf_get_lines(0, 2, 3, false)[1] == 'external', 'external clipboard paste failed')
helper_available = false
external_text = nil
vim.cmd 'normal! yy'
vim.cmd 'normal! p'
assert(vim.api.nvim_buf_get_lines(0, 3, 4, false)[1] == 'external', 'GNOME cache fallback failed')
vim.cmd 'qa!'
""")
        result = subprocess.run(["nvim", "--headless", "-u", "NONE", "-i", "NONE", "-l", str(script)],
                                env=self.env, capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
