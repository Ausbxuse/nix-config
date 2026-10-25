"""Exercise the packaged TUI in a real PTY without starting a model turn.

Run with Python + pyte, passing the built package's bin/codex. Uses the
caller's trusted working directory and never submits the draft to the model.
"""

import argparse
import codecs
import fcntl
import json
import os
import re
import select
import signal
import struct
import subprocess
import termios
import time

import pyte


PALETTES = {
    "dark": ((212, 210, 226), (30, 34, 51)),
    "light": ((42, 51, 41), (238, 240, 228)),
}
QUERY = re.compile(rb"\x1b\](10|11);\?(?:\x1b\\|\x07)|\x1b\[6n|\x1b\[\?u|\x1b\[c")


class Terminal:
    def __init__(self, binary, answer_colors=True):
        self.master, slave = os.openpty()
        fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", 36, 120, 0, 0))
        env = dict(os.environ, TERM="xterm-256color", COLORTERM="truecolor")
        for key in ("TMUX", "TMUX_PANE", "FORCE_COLOR", "NO_COLOR"):
            env.pop(key, None)

        def controlling_terminal():
            os.setsid()
            fcntl.ioctl(0, termios.TIOCSCTTY, 0)

        # An unknown syntax name deliberately exercises the adaptive fallback,
        # regardless of a caller's explicit theme. Overrides are never saved.
        command = [
            binary, "--no-daemon", "--no-alt-screen", "--cd", os.getcwd(),
            "--model", "palette-test",
            "-c", 'tui.theme=""', "-c", "tui.notifications=false",
            "-c", 'model_provider="palette_test"',
            "-c", 'model_providers.palette_test={name="Palette test",base_url="http://127.0.0.1:9/v1",wire_api="responses",requires_openai_auth=false}',
        ]
        self.process = subprocess.Popen(
            command, stdin=slave, stdout=slave, stderr=slave, env=env,
            preexec_fn=controlling_terminal, close_fds=True,
        )
        os.close(slave)
        self.screen = pyte.Screen(120, 36)
        self.stream = pyte.Stream(self.screen)
        self.decoder = codecs.getincrementaldecoder("utf-8")("replace")
        self.mode = "dark"
        self.queries = b""
        self.answer_colors = answer_colors
        self.inject_on_reply = b""
        self.query_count = 0
        self.transcript = bytearray()

    def write(self, data):
        os.write(self.master, data)

    def pump(self, seconds=0.1):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            if not select.select([self.master], [], [], max(0, min(0.05, deadline - time.monotonic())))[0]:
                continue
            try:
                data = os.read(self.master, 65536)
            except OSError:
                data = b""
            if not data:
                raise AssertionError(f"TUI exited early: {self.process.poll()}\n{self.text()}")
            self.transcript.extend(data)
            self.stream.feed(self.decoder.decode(data))
            self.queries += data
            consumed = 0
            for match in QUERY.finditer(self.queries):
                consumed = match.end()
                query = match.group()
                if match.group(1):
                    self.query_count += 1
                    if self.answer_colors:
                        slot = int(match.group(1))
                        rgb = PALETTES[self.mode][slot - 10]
                        if slot == 10 and self.inject_on_reply:
                            self.write(self.inject_on_reply)
                            self.inject_on_reply = b""
                        reply = f"\x1b]{slot};rgb:{rgb[0]:02x}/{rgb[1]:02x}/{rgb[2]:02x}\x1b\\".encode()
                        # Exercise fragmented terminal replies through the real parser.
                        self.write(reply[:6])
                        time.sleep(0.005)
                        self.write(reply[6:])
                elif query == b"\x1b[6n":
                    self.write(b"\x1b[1;1R")
                elif query == b"\x1b[?u":
                    self.write(b"\x1b[?1u")
                else:
                    self.write(b"\x1b[?1;2c")
            self.queries = self.queries[consumed:][-256:]

    def text(self):
        return "\n".join(self.screen.display)

    def backgrounds(self):
        return {cell.bg for row in self.screen.buffer.values() for cell in row.values()}

    def wait_for(self, condition, timeout=8):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if condition():
                return
            self.pump()
        raise AssertionError(f"Condition timed out; palette={self.mode}; backgrounds={self.backgrounds()}\n{self.text()}")

    def close(self):
        if self.process.poll() is None:
            self.write(b"\x15\x04")
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                os.killpg(self.process.pid, signal.SIGTERM)
                try:
                    self.process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    os.killpg(self.process.pid, signal.SIGKILL)
                    self.process.wait()
        os.close(self.master)


def check_late_palette(binary):
    terminal = Terminal(binary, answer_colors=False)
    try:
        terminal.wait_for(lambda: "Ask Codex to do anything" in terminal.text(), timeout=30)
        terminal.write(b"\x1b[200~typed without color replies\x1b[201~")
        terminal.pump(1.2)
        assert "typed without color replies" in terminal.text()
        terminal.mode = "light"
        terminal.answer_colors = True
        terminal.wait_for(
            lambda: "e4e6da" in terminal.backgrounds()
            and "typed without color replies" in terminal.text(), timeout=4,
        )
        return True
    finally:
        terminal.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("codex")
    parser.add_argument("--transcript")
    args = parser.parse_args()
    terminal = Terminal(os.path.abspath(args.codex))
    result = {"pid": terminal.process.pid, "transitions": []}
    try:
        terminal.wait_for(lambda: "Ask Codex to do anything" in terminal.text(), timeout=30)
        terminal.pump(0.5)
        draft = "palette draft: keep every key "
        terminal.write(b"\x1b[200~" + draft.encode() + b"\x1b[201~")
        terminal.wait_for(lambda: draft.strip() in terminal.text())
        # These fills are computed from the real Ghostty backgrounds, not ANSI slots.
        fills = {"light": "e4e6da", "dark": "393c4b"}
        for index, mode in enumerate(["light", "dark", "light", "dark"]):
            terminal.mode = mode
            key = str(index)
            terminal.inject_on_reply = key.encode()
            draft += key
            start = time.monotonic()
            terminal.wait_for(lambda: fills[mode] in terminal.backgrounds() and draft in terminal.text(), timeout=4)
            assert terminal.process.poll() is None
            result["transitions"].append({"mode": mode, "seconds": round(time.monotonic() - start, 3), "background": fills[mode], "draft_preserved": True})
        # A terminal that stops answering must retain the last valid palette and
        # keep accepting input, rather than block or reset to a guessed theme.
        terminal.answer_colors = False
        draft += " still typing"
        terminal.write(b"\x1b[200~ still typing\x1b[201~")
        terminal.pump(2.2)
        assert draft in terminal.text()
        assert fills["dark"] in terminal.backgrounds()
        terminal.answer_colors = True
        terminal.mode = "light"
        terminal.write(b"\x1b[O\x1b[I")
        terminal.wait_for(lambda: fills["light"] in terminal.backgrounds() and draft in terminal.text(), timeout=4)
        result.update({"query_count": terminal.query_count, "missing_replies_keep_input_and_palette": True, "focus_refresh": True, "same_process": terminal.process.poll() is None})
        result["late_palette_after_unsupported_startup"] = check_late_palette(os.path.abspath(args.codex))
        print(json.dumps(result, indent=2))
    finally:
        if args.transcript:
            with open(args.transcript, "wb") as output:
                output.write(terminal.transcript)
        terminal.close()


if __name__ == "__main__":
    main()
