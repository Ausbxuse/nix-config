import os
import shutil
import tempfile
import time
import unittest
from pathlib import Path

import bridge


class AgentWindowsTest(unittest.TestCase):
    """On a tmux server of its own, with a stand-in for claude on its PATH (a process named claude,
    at work until its stop file appears)."""

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="agent-hub-test-")
        bin_dir = Path(self.directory.name) / "bin"
        bin_dir.mkdir()
        self.stop = Path(self.directory.name) / "stop"
        (bin_dir / "claude").write_text(f"#!/bin/sh\nwhile [ ! -e {self.stop} ]; do sleep 0.1; done\n")
        (bin_dir / "claude").chmod(0o755)
        self.path = os.environ["PATH"]
        os.environ["PATH"] = f"{bin_dir}:{self.path}"
        self.conn = {"id": "test", "kind": "local", "label": "Test", "socket": str(Path(self.directory.name) / "tmux.sock")}

    def tearDown(self):
        try:
            bridge.run(self.conn, ["kill-server"])
        except bridge.BridgeError:
            pass
        os.environ["PATH"] = self.path
        self.directory.cleanup()

    def agent(self, until):
        for _ in range(30):
            time.sleep(0.1)
            found = bridge.parse_panes(bridge.run(self.conn, ["list-panes", "-a", "-F", bridge.FORMAT]), self.conn)
            if found and until(found[0]):
                return found[0]
        return found[0] if found else None

    def test_new_agents_are_windows_of_one_session(self):
        first = bridge.launch(self.conn, self.directory.name, "one", "claude")
        second = bridge.launch(self.conn, "/tmp", "two", "claude")
        self.assertEqual(first["session"], bridge.AGENT_SESSION)
        self.assertEqual(second["session"], bridge.AGENT_SESSION)
        sessions = bridge.run(self.conn, ["list-sessions", "-F", "#{session_name}"]).split()
        self.assertEqual(sessions, [bridge.AGENT_SESSION])
        windows = bridge.run(self.conn, ["list-windows", "-t", "=" + bridge.AGENT_SESSION, "-F", "#{window_name} #{pane_id}"]).split("\n")
        self.assertIn(f"tmp {second['pane']}", windows)
        self.assertEqual(len([w for w in windows if w]), 2)

    @unittest.skipUnless(shutil.which("zsh"), "needs zsh")
    def test_an_agent_behind_a_shell_is_live_until_it_ends(self):
        # As an agent handed over from razy was started: its shell stays in front of it, and stays on after it.
        pane = bridge.run(self.conn, ["new-session", "-d", "-s", "work", "-P", "-F", "#{pane_id}", "zsh -fc 'claude \"task\"; exec zsh -f'"]).strip()
        bridge.run(self.conn, ["set-option", "-p", "-t", pane, "@pane-role", "agent"])
        agent = self.agent(lambda a: a["live"])
        self.assertEqual(agent["command"], "zsh")
        self.assertEqual((agent["runtime"], agent["live"]), ("claude", True))
        self.stop.touch()
        agent = self.agent(lambda a: not a["live"])
        self.assertEqual((agent["command"], agent["live"]), ("zsh", False))

    def test_a_plain_shell_is_no_agent(self):
        bridge.run(self.conn, ["new-session", "-d", "-s", "work", "sh"])
        time.sleep(0.3)
        self.assertEqual(bridge.parse_panes(bridge.run(self.conn, ["list-panes", "-a", "-F", bridge.FORMAT]), self.conn), [])


if __name__ == "__main__":
    unittest.main()
