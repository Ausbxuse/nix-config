"""Codex's TUI queue is separate from thread/queue on the app server.

Only its latest plain-text input has a terminal edit operation. Recheck the
whole queue and editor before using that operation; never replay other inputs.
"""
import hashlib
import json
import re
import time
import uuid

import bridge


HINT = re.compile(r'^\s*shift\s*\+\s*(←|↑|left|up)\s+(?:to\s+)?edit last queued message\s*$', re.I)
RICH = re.compile(r'\[(?:image\b|pasted\b|attachment\b)|(?:^|\s)@[\w/]', re.I)


def snapshot(agent, capture):
    if agent.get('runtime') != 'codex' or not agent.get('live'):
        return None
    lines = [line.rstrip() for line in capture.splitlines()]
    headings = [i for i, line in enumerate(lines) if line == '• Queued follow-up inputs']
    if not headings:
        return None
    start = headings[-1]
    ends = [(i, HINT.fullmatch(line)) for i, line in enumerate(lines[start + 1:], start + 1) if HINT.fullmatch(line)]
    if not ends:
        return None
    end, hint = ends[0]
    # A historical queue above a newer turn is not the live queue.
    prompts = [i for i in range(end + 1, len(lines)) if re.match(r'^›(?: |$)', lines[i])]
    if len(prompts) != 1 or any(re.match(r'^[•●■] ', line) for line in lines[end + 1:]):
        return None
    messages = []
    for line in lines[start + 1:end]:
        if line.startswith('  ↳ '):
            messages.append([line[4:]])
        elif not line.strip() and messages:
            messages[-1].append('')
        elif line.startswith('    ') and messages:
            messages[-1].append(line[4:])
        else:
            return None
    texts = ['\n'.join(parts).strip() for parts in messages]
    if not texts or len(texts) > 128 or any(not text or len(text) > 12000 for text in texts):
        return None
    key = 'S-Left' if hint[1].lower() in ('←', 'left') else 'S-Up'
    version = hashlib.sha256(json.dumps([agent['id'], key, texts], ensure_ascii=False).encode()).hexdigest()
    items = []
    for index, text in enumerate(texts):
        identity = 'terminal-' + hashlib.sha256(json.dumps([index, text]).encode()).hexdigest()[:24]
        editable = (index == len(texts) - 1 and not RICH.search(text) and not text.endswith(('…', '...'))
                    and not text.lstrip().startswith(('/', '!')) and not any(ord(c) < 32 and c != '\n' for c in text))
        items.append(dict(id=identity, text=text, digest=version, editable=editable, removable=False,
                          backend='terminal', has_attachments=False))
    return dict(status='available', thread_id='', items=items, version=version, more=False, key=key)


def merge(native, terminal):
    if terminal:
        result = dict(native or terminal)
        result.pop('key', None)
        result['items'] = [*(native or {}).get('items', []), *terminal['items']]
        result['version'] = (native or {}).get('version', '') + terminal['version']
        result['suppress_terminal'] = True
        return result
    # An empty API queue says nothing about the TUI's independent queue.
    return {**native, 'suppress_terminal':False} if native is not None else None


def _editor_matches(text, expected):
    rows = [row.rstrip() for row in text.splitlines()]
    prompts = [i for i, row in enumerate(rows) if row.startswith('› ')]
    if not prompts:
        return False
    index = prompts[-1]
    rendered = [('› ' if i == 0 else '  ') + row for i, row in enumerate(expected.split('\n'))]
    rendered = [row.rstrip() for row in rendered]
    return rows[index:index + len(rendered)] == rendered and rows[index + len(rendered):index + len(rendered) + 1] == ['']


def change(connection, agent, queue_id, digest, message):
    if not isinstance(message, str) or not message.strip() or len(message) > 12000 or any(ord(c) < 32 and c != '\n' for c in message):
        raise bridge.BridgeError('Use a message between 1 and 12,000 characters.')
    if message.lstrip().startswith(('/', '!')):
        raise bridge.BridgeError('Use the terminal for slash or shell commands.')
    current = bridge.verify_agent(connection, agent)
    screen = bridge.run(connection, ['capture-pane', '-p', '-t', current['pane'], '-S', '0'])
    before = snapshot(current, bridge.capture(connection, current['pane'], 0))
    last = before['items'][-1] if before else {}
    if not last or last['id'] != queue_id or last['digest'] != digest or not last['editable']:
        raise bridge.BridgeError('This queue changed. Refresh before editing its latest message.')
    if bridge.classify(current, screen)[0] != 'running' or not bridge.empty_codex_editor(current, screen):
        raise bridge.BridgeError('The terminal has a draft, a prompt, or stopped working. Your edit was not sent.')
    if message == last['text']:
        return
    bridge.run(connection, ['send-keys', '-t', current['pane'], before['key']])
    deadline = time.monotonic() + 1.5
    while True:
        current = bridge.verify_agent(connection, agent)
        text = bridge.capture(connection, current['pane'], 0)
        remaining = snapshot(current, text)
        expected = [item['text'] for item in before['items'][:-1]]
        if _editor_matches(text, last['text']) and [item['text'] for item in (remaining or {}).get('items', [])] == expected:
            break
        if time.monotonic() >= deadline:
            raise bridge.BridgeError('Could not verify the terminal edit. Check its draft; your revised message was not sent.')
        time.sleep(.04)
    # Codex restores the cursor to the end. Clear each logical line using its
    # own editing keys, then verify the empty editor before pasting anything.
    keys = ['C-u']
    for _ in range(last['text'].count('\n')):
        keys.extend(('BSpace', 'C-u'))
    bridge.run(connection, ['send-keys', '-t', current['pane'], *keys])
    deadline = time.monotonic() + 1.5
    while True:
        current = bridge.verify_agent(connection, agent)
        screen = bridge.run(connection, ['capture-pane', '-p', '-t', current['pane'], '-S', '0'])
        if bridge.empty_codex_editor(current, screen):
            break
        if time.monotonic() >= deadline:
            raise bridge.BridgeError('The terminal draft changed during editing. Your revised message was not sent.')
        time.sleep(.04)
    # Paste without submitting first. If the turn has ended, leave a draft;
    # editing must not start a fresh task or steer the active turn with Enter.
    buffer = 'hub-queue-edit-' + uuid.uuid4().hex[:12]
    bridge.run(connection, ['load-buffer', '-b', buffer, '-', ';', 'paste-buffer', '-p', '-d', '-b', buffer, '-t', current['pane']], stdin=message)
    deadline = time.monotonic() + 1.5
    while True:
        current = bridge.verify_agent(connection, agent)
        text = bridge.capture(connection, current['pane'], 0)
        if _editor_matches(text, message):
            break
        if time.monotonic() >= deadline:
            raise bridge.BridgeError('Your edit is in the terminal draft. Check it there before queuing.')
        time.sleep(.04)
    if bridge.classify(current, text)[0] != 'running':
        raise bridge.BridgeError('The turn ended while you edited. Your revised message is in the terminal draft.')
    bridge.run(connection, ['send-keys', '-t', current['pane'], 'Tab'])
    deadline = time.monotonic() + 1.5
    while True:
        current = bridge.verify_agent(connection, agent)
        after = snapshot(current, bridge.capture(connection, current['pane'], 0))
        if after and [item['text'] for item in after['items']] == [*expected, message.strip()]:
            return
        if time.monotonic() >= deadline:
            raise bridge.BridgeError('Queue update unconfirmed. Check the conversation before trying again.')
        time.sleep(.04)
