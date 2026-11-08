import unittest

import bridge


CLAUDE_BASH = ("/run/current-system/sw/bin/zsh -c source /home/u/.claude/shell-snapshots/snapshot-zsh-1-x.sh 2>/dev/null || true "
               "&& { \\builtin unalias -- 'unsetenv'; } >/dev/null 2>&1 || true && eval 'python mix.py --robot '\"'\"'pt'\"'\"'' "
               "\\< /dev/null && pwd -P >| /tmp/claude-a130-cwd")


class ShellWaitTest(unittest.TestCase):
    """What an agent waits on: a command its tool runs under it, from the host's process table."""

    def table(self, *rows):
        return {pid: (parent, seconds, args) for pid, parent, seconds, args in rows}

    def test_claude_waits_on_its_bash_command(self):
        table = self.table(("100", "1", 9000, "claude"), ("200", "100", 964, CLAUDE_BASH))
        self.assertEqual(bridge.waiting_on(table, "100", "claude", 10_000), {"command": "python mix.py --robot 'pt'", "since": 9036})

    def test_a_short_command_hooks_and_servers_are_not_waits(self):
        table = self.table(("100", "1", 9000, "claude"), ("200", "100", 3, CLAUDE_BASH),
                           ("300", "100", 9000, "bash /home/u/.local/bin/tmux/claude-notify.sh"),
                           ("400", "100", 9000, "node /nix/store/x-mcp/server.js"))
        self.assertIsNone(bridge.waiting_on(table, "100", "claude", 10_000))

    def test_claude_under_a_shell_that_stays_in_front(self):
        table = self.table(("50", "1", 9000, "zsh -lc claude; exec zsh"), ("100", "50", 9000, "claude"), ("200", "100", 60, CLAUDE_BASH))
        self.assertEqual(bridge.waiting_on(table, "50", "claude", 10_000)["command"], "python mix.py --robot 'pt'")

    def test_codex_waits_on_its_exec(self):
        table = self.table(("100", "1", 9000, "codex"), ("200", "100", 30, "bash -lc cargo test --release"))
        self.assertEqual(bridge.waiting_on(table, "100", "codex", 10_000), {"command": "cargo test --release", "since": 9970})

    def test_the_longest_running_is_the_one(self):
        quick = CLAUDE_BASH.replace("python mix.py --robot '\"'\"'pt'\"'\"'", "ls")
        table = self.table(("100", "1", 9000, "claude"), ("200", "100", 20, quick), ("300", "100", 600, CLAUDE_BASH))
        self.assertEqual(bridge.waiting_on(table, "100", "claude", 10_000)["command"], "python mix.py --robot 'pt'")


if __name__ == "__main__":
    unittest.main()
