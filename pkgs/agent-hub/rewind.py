"""Editing your last message to a Claude agent: Claude Code's rewind (Esc Esc, then "Restore
conversation"), driven from its screen. The conversation goes back to just before that message
(its answer goes too; the files it changed stay), and the new words are sent in its place.

Each step waits to see the screen it expects and stops otherwise, leaving the menu closed: keys
are never sent into a screen that isn't the one they're meant for.
"""
import re
import time

import bridge
import claude_models

LIST = re.compile(r'^\s*Rewind\s*$\n^\s*Restore the code and/or conversation', re.M)
CONFIRM = re.compile(r'Confirm you want to restore')
SELECTED = re.compile(r'^\s*❯\s+(.*\S)\s*$')


def screen(connection, pane):
    return bridge.run(connection, ['capture-pane', '-p', '-t', pane, '-S', '0'])


def keys(connection, pane, *names):
    bridge.run(connection, ['send-keys', '-t', pane, *names])


def wait(connection, pane, test, seconds):
    """The screen once [test] passes on it, or None after [seconds]."""
    end = time.monotonic() + seconds
    while True:
        s = screen(connection, pane)
        if test(s):
            return s
        if time.monotonic() > end:
            return None
        time.sleep(.08)


def words(text):
    return ' '.join(text.replace('…', ' ').split())


def selected(s):
    """The highlighted row of the rewind list (below its heading)."""
    tail = s[LIST.search(s).end():] if LIST.search(s) else ''
    rows = [m.group(1) for line in tail.split('\n') if (m := SELECTED.match(line))]
    return rows[-1] if rows else None


def same(row, was):
    """Whether the list's row is the message [was] (the row may be cut short with …)."""
    row, was = words(row), words(was)
    return bool(row) and (was.startswith(row) or row.startswith(was[:max(12, len(row))]))


def edit_last(connection, agent, was, text):
    """Sends [text] in place of the agent's last message [was] (stopped first if it's still at it)."""
    if not isinstance(text, str) or not text.strip() or len(text) > 12000 or '\x00' in text:
        raise bridge.BridgeError('Write between 1 and 12,000 characters.')
    if not isinstance(was, str) or not was.strip():
        raise bridge.BridgeError('Which message to edit is missing.')
    current = bridge.verify_agent(connection, agent)
    if current.get('runtime') != 'claude':
        raise bridge.BridgeError("Only a Claude agent's last message can be edited here.")
    pane = current['pane']
    # (Vim mode: typing, it's in insert mode, and the Escapes below leave it; it's put back after.)
    inserting = '-- INSERT --' in screen(connection, pane)
    # Still at it: stopped first (Claude Code takes Ctrl+C as stop while it works).
    if bridge.classify(current, screen(connection, pane))[0] == 'running':
        keys(connection, pane, 'C-c')
        if not wait(connection, pane, lambda s: bridge.interrupted(s) or bridge.classify(current, s)[0] != 'running', 8):
            raise bridge.BridgeError("It didn't stop, so its last message wasn't edited.")
        time.sleep(.3)
    current = bridge.verify_agent(connection, agent)
    if not claude_models.empty_editor(current, bridge.capture(connection, pane, 0), bridge.capture_terminal(connection, pane, 0)):
        raise bridge.BridgeError('Finish the terminal draft or close its menu first.')
    # Esc Esc opens the list (in vim mode the first Escape only leaves insert mode: tried again).
    opened = None
    for _ in range(3):
        keys(connection, pane, 'Escape')
        time.sleep(.12)
        keys(connection, pane, 'Escape')
        opened = wait(connection, pane, lambda s: LIST.search(s) is not None, 1.2)
        if opened:
            break
    if not opened:
        raise bridge.BridgeError("Claude's rewind didn't open; nothing was changed.")
    try:
        # It opens on "(current)"; one up is your last message.
        keys(connection, pane, 'Up')
        s = wait(connection, pane, lambda s: (selected(s) or '(current)') != '(current)', 1.5)
        if not s or not same(selected(s), was):
            raise bridge.BridgeError('Your last message there isn\'t the one shown here; nothing was changed. Refresh and try again.')
        keys(connection, pane, 'Enter')
        s = wait(connection, pane, lambda s: CONFIRM.search(s) is not None, 2)
        choice = s and re.search(r'^\s*(?:❯\s*)?(\d)\.\s+Restore conversation\s*$', s, re.M)
        if not choice:
            raise bridge.BridgeError("Claude didn't offer to restore the conversation; nothing was changed.")
        keys(connection, pane, choice.group(1))
    except Exception:
        # (Whatever went wrong, the menu is closed again.)
        for _ in range(2):
            if LIST.search(screen(connection, pane)) or CONFIRM.search(screen(connection, pane)):
                keys(connection, pane, 'Escape')
                time.sleep(.15)
        raise
    # Restored, the old words are back in its box: cleared (Ctrl+C clears a box with words in
    # it), then the new ones sent.
    if not wait(connection, pane, lambda s: not LIST.search(s) and not CONFIRM.search(s), 3):
        raise bridge.BridgeError('Claude is still restoring; check its terminal.')
    time.sleep(.3)
    keys(connection, pane, 'C-c')
    if not wait(connection, pane, lambda s: claude_models.empty_editor(bridge.verify_agent(connection, agent), bridge.capture(connection, pane, 0), bridge.capture_terminal(connection, pane, 0)), 2):
        raise bridge.BridgeError("The conversation went back, but the old words stayed in its box; send the new ones from its terminal.")
    # (Out of it twice over, a moment apart: never an "i" typed into a box that was only being redrawn.)
    if inserting and '-- INSERT --' not in screen(connection, pane) and not time.sleep(.15) and '-- INSERT --' not in screen(connection, pane):
        keys(connection, pane, 'i')
        wait(connection, pane, lambda s: '-- INSERT --' in s, 1)
    bridge.paste(connection, bridge.verify_agent(connection, agent), text.strip())
