"""Read original messages from a verified live session or unique visible match.

Tmux session-file hints can point at another pane in a shared app-server. They
are deliberately not an authority. Ambiguous or absent matches use the capture.
Claude's process registry can identify its own live session without UI matching.
Only user/assistant text and supported command envelopes are read, never reasoning.
"""
from collections import OrderedDict
import copy
import hashlib
import json
import os
from pathlib import Path
import re
import threading
import time

from bridge import CODEX_PLACEHOLDERS, TOOL_PREFIX, terminal_content
from claude_sessions import ClaudeSessions
from message_content import user_text


def normalized(text):
    return re.sub(r"[^\w]", "", text, flags=re.UNICODE).lower()


def visible_variants(text, cwd):
    """Terminal links may show their label, a full path, or a relative path."""
    labels = re.sub(r"!?\[([^\]\n]*)\]\((?:<[^>\n]*>|[^)\n]*)\)", r"\1", text)
    return tuple(dict.fromkeys(normalized(value) for value in (
        text, text.replace(cwd.rstrip("/") + "/", ""), labels)))


def visible_dialogue(capture):
    """Terminal message boundaries, excluding markers inside fenced examples.

    A tool redraw can leave a prompt followed by a partial diff. Only a full
    native message or the prefix before a visible clipping marker is evidence;
    matching arbitrary substrings of that diff would attribute it to the user.
    """
    records = []
    fence, fence_indent, offset = None, 0, 0
    for line in capture.splitlines(keepends=True):
        trimmed = line.strip()
        indent = len(line) - len(line.lstrip())
        if fence and trimmed and indent < fence_indent:
            fence = None
        delimiter = re.match(r'(`{3,}|~{3,})', trimmed)
        if fence or delimiter:
            if not fence:
                fence, fence_indent = delimiter[1], indent
            elif delimiter and delimiter[1][0] == fence[0] and len(delimiter[1]) >= len(fence):
                fence = None
        elif record := re.match(r'^[•●›❯■](?:[ \t]+|$)', line.rstrip('\n')):
            records.append((record[0][0], offset, offset + record.end()))
        offset += len(line)
    prefix = capture[:records[0][1]] if records else capture
    dialogue = [('partial', normalized(prefix), '', 0)] if prefix.strip() else []
    for index, (marker, _, start) in enumerate(records):
        end = records[index + 1][1] if index + 1 < len(records) else len(capture)
        text = capture[start:end]
        clipped = re.search(r"(?:…|\.{3})[ \t]*(?:\n|$)", text)
        role = ('user' if marker in '›❯' else
                'assistant' if marker in '•●' and not TOOL_PREFIX.match(text) and
                not text.startswith('Queued follow-up inputs') else 'tool')
        if role == 'user' and index == len(records) - 1 and text.strip() in CODEX_PLACEHOLDERS:
            role = 'tool'  # The empty composer's hint is not a submitted input.
        dialogue.append((role, normalized(text), normalized(text[:clipped.start()]) if clipped else '',
                         len(normalized(capture[:start]))))
    return dialogue


def assistant_evidence(variants, dialogue):
    """Substantial, distinct fragments in order within one assistant record."""
    best = (0, 0, 0)
    for body in variants:
        chunks = tuple(dict.fromkeys(body[start:start + 80] for start in range(0, len(body) - 79, 80)))
        for role, text, _, offset in dialogue:
            if role not in ('assistant', 'partial') or len(text) < 160:
                continue
            positions, cursor = [], 0
            for chunk in chunks:
                start = text.find(chunk, cursor)
                if start >= 0:
                    positions.append(start)
                    cursor = start + len(chunk)
            score = 80 * len(positions)
            if score >= 160:
                evidence = (score, offset + positions[0], offset + cursor)
                if (evidence[2], score) > (best[2], best[0]):
                    best = evidence
    return best


# The input box's own status line, run together with whatever is in the box (normalized).
EDITOR_STATUS = re.compile(r'(?:weekly|daily|5h)\d+left|\d+contextleft|forshortcuts|foragents|shifttabtocycle|automodeon|accepteditson|bypasspermissionson|planmodeon')


def within(path, folder):
    """[path] is inside [folder] (not the folder itself)."""
    return bool(path and folder) and path.startswith(folder.rstrip('/') + '/')


def editor_draft(raw):
    """What's typed in the input box at the bottom of the raw screen (normalized), if anything.

    The box is the last prompt row (› or ❯) with no reply marker below it and its status line
    ("weekly 90% left", "? for shortcuts", "auto mode on") under it.
    """
    lines = raw.splitlines()
    rows = [i for i, line in enumerate(lines) if re.match(r'^[›❯](?:\s|$)', line)]
    if not rows or any(re.match(r'^[•●■](?:\s|$)', line) for line in lines[rows[-1] + 1:]):
        return ''
    # It's the box only with the box's status line under it; a just-sent message has none.
    if not any(EDITOR_STATUS.search(normalized(line)) for line in lines[rows[-1] + 1:]):
        return ''
    text = [lines[rows[-1]][1:].strip()]
    for line in lines[rows[-1] + 1:]:
        if not line.startswith('  ') or not line.strip() or EDITOR_STATUS.search(normalized(line)):
            break
        text.append(line.strip())
    draft = normalized(' '.join(text))
    return '' if draft in {normalized(p) for p in CODEX_PLACEHOLDERS} else draft


def without_draft(dialogue, draft=''):
    """The input box at the bottom is not a message, even with something typed in it.

    While someone types in the terminal, the box reads as a newer user turn; left in, it would
    look like a session the log hasn't caught up with and drop the binding for as long as the
    draft is there.
    """
    if dialogue and dialogue[-1][0] == 'user':
        text = dialogue[-1][1]
        if EDITOR_STATUS.search(text) or (draft and (text.startswith(draft[:40]) or draft.startswith(text[:40]))):
            return dialogue[:-1]
    return dialogue


def last_reply_evidence(messages, dialogue, cwd):
    """An idle pane often shows only its short last answer under a run of tool rows.

    That answer identifies its record when it is the newest visible reply, equals the session's
    last reply word for word (60+ characters), and only tool rows separate it from the prompt
    above it: a quoted copy, or one after an unrelated reply, is not enough.
    """
    # The empty input box (its placeholder, and the status line under it) isn't a prompt.
    empty = tuple(normalized(p) for p in CODEX_PLACEHOLDERS)
    shown = list(dialogue)
    if shown and shown[-1][0] == 'user' and shown[-1][1].startswith(empty):
        shown.pop()
    last = next((i for i in range(len(shown) - 1, -1, -1) if shown[i][0] == 'assistant'), None)
    if last is None or any(role not in ('tool',) for role, *_ in shown[last + 1:]):
        return None
    text, start = shown[last][1], shown[last][3]
    prompt = next((i for i in range(last - 1, -1, -1) if shown[i][0] == 'user'), -1)
    if any(role != 'tool' for role, *_ in shown[prompt + 1:last]):
        return None
    index = next((i for i in range(len(messages) - 1, -1, -1) if messages[i].get('role') == 'assistant'), None)
    if index is None or len(text) < 60 or text not in visible_variants(messages[index]['text'], cwd):
        return None
    return 160, start, start + len(text), index


def short_dialogue_evidence(messages, dialogue, cwd):
    """Combine consecutive, exact short messages in their native order.

    A resumed pane may show several short exchanges and no single 160-character
    reply. Require the same amount of distinct text across explicit dialogue
    records; tool echoes, reordered replies and repeated acknowledgments cannot
    establish identity.
    """
    lookup = {}
    for index, message in enumerate(messages):
        role = message.get('role')
        variants = visible_variants(message['text'], cwd) if role == 'assistant' else (normalized(message['text']),)
        for body in variants:
            if role in ('user', 'assistant') and 12 <= len(body) < 160:
                lookup.setdefault((role, body), []).append(index)
    chains, best = [], None
    for role, body, _, start in dialogue:
        if role == 'tool' or len(body) < 12:
            continue
        next_chains = []
        for index in lookup.get((role, body), []):
            options = [(len(body), start, frozenset((body,)))]
            options.extend((score + (0 if body in seen else len(body)), begin, seen | {body})
                           for previous, score, begin, seen in chains if previous < index)
            score, begin, seen = max(options, key=lambda item: item[0])
            next_chains.append((index, score, begin, seen))
            if score >= 160:
                candidate = (score, begin, start + len(body), index)
                if best is None or (candidate[2], score) > (best[2], best[0]):
                    best = candidate
        chains = next_chains
    return best


def prompt_evidence(body, prompts):
    """Return exact prefix length and position, never a fuzzy text match."""
    evidence = []
    for text, clipped, start in prompts:
        size = len(body) if text == body else len(clipped) if clipped and body.startswith(clipped) else 0
        if size >= 160:
            evidence.append((size, start + size))
    return max(evidence, key=lambda item: (item[1], item[0]), default=(0, 0))


def input_text_parts(content):
    """Hide only native image wrappers next to a structured image payload."""
    skip = set()
    for index in range(len(content) - 2):
        before, image, after = content[index:index+3]
        if (all(isinstance(part, dict) for part in (before, image, after)) and
                before.get('type') == 'input_text' and isinstance(before.get('text'), str) and
                before['text'].startswith('<image name=') and before['text'].endswith('>') and
                image.get('type') == 'input_image' and after.get('type') == 'input_text' and after.get('text') == '</image>'):
            skip.update((index, index + 2))
    return [part['text'] for index, part in enumerate(content) if index not in skip and
            isinstance(part, dict) and part.get('type') in ('input_text', 'output_text') and isinstance(part.get('text'), str)]


def claude_display(record, body):
    """Interpret known CLI envelopes, never arbitrary XML in user prose/code."""
    if record.get("isMeta"):
        return None
    text = body.strip()
    # /compact and auto-compaction: the CLI's own summary, not something the user wrote.
    if record.get("isCompactSummary") or text.startswith("This session is being continued from a previous conversation"):
        return {"role": "tool", "kind": "compaction", "text": "Earlier conversation summarized to free up context"}
    if re.fullmatch(r"\[Request interrupted by user[^\]]*\]", text):
        return {"role": "tool", "kind": "interrupt", "text": "Interrupted"}
    # `!cmd` typed into Claude Code: the command, then what it printed.
    shell = re.fullmatch(r"<bash-input>([\s\S]*?)</bash-input>", text)
    if shell:
        return {"role": "tool", "kind": "command", "text": "! " + shell[1].strip()}
    if text.startswith("<bash-stdout>") or text.startswith("<bash-stderr>"):
        parts = re.findall(r"<bash-(?:stdout|stderr)>([\s\S]*?)</bash-(?:stdout|stderr)>", text)
        out = "\n".join(p.strip() for p in parts if p.strip())
        return {"role": "tool", "kind": "command-output", "text": out} if out else None
    if re.fullmatch(r"<local-command-caveat>[\s\S]*</local-command-caveat>", text):
        return None
    if text.startswith("<command-name>"):
        name = re.search(r"<command-name>([\s\S]*?)</command-name>", text)
        args = re.search(r"<command-args>([\s\S]*?)</command-args>", text)
        if name:
            return {"role": "tool", "kind": "command", "text": (name[1] + (" " + args[1] if args and args[1] else "")).strip()}
    output = re.fullmatch(r"<local-command-(stdout|stderr)>([\s\S]*)</local-command-\1>", text)
    if output:
        if not output[2].strip():
            return None
        return {"role": "tool", "kind": "command-output", "text": output[2].strip()}
    if text.startswith("<task-notification>") and "</task-notification>" in text:
        summary = re.search(r"<summary>([\s\S]*?)</summary>", text)
        if not summary:
            return None
        return {"role": "tool", "kind": "task-update", "text": summary[1].strip()}
    return {"role": record["message"]["role"], "text": body}


def claude_lifecycle(record, payload, body, display, state):
    """Track native identities and block types, never tool/thinking contents."""
    if record.get('isMeta'):
        return
    content = payload.get('content', [])
    kinds = {part['type'] for part in content if isinstance(part, dict) and isinstance(part.get('type'), str)} if isinstance(content, list) else set()
    previous = state.get('lifecycle', {})
    if payload['role'] == 'user':
        identity = record.get('uuid')
        user_input = (display and display.get('role') == 'user') or (not body and bool(kinds & {'image', 'document'}))
        if user_input and isinstance(identity, str) and re.fullmatch(r'[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}', identity):
            turn = 'claude:' + identity.lower()
            if previous.get('turn_id') != turn:
                state['lifecycle'] = dict(source='claude', state='running', turn_id=turn, at=record.get('timestamp', ''))
        elif 'tool_result' in kinds:
            state['lifecycle'] = {**previous, 'source':'claude', 'state':'running'}
        return
    if payload.get('stop_reason') == 'tool_use' or 'tool_use' in kinds or ('thinking' in kinds and payload.get('stop_reason') != 'end_turn'):
        phase = 'running'
    elif payload.get('stop_reason') == 'end_turn' and body.strip():
        phase = 'done'
    else:
        phase = 'unknown'
    state['lifecycle'] = {**previous, 'source':'claude', 'state':phase}


class Transcripts:
    def __init__(self, root=None, claude_root=None):
        self.root = Path(root or Path.home() / ".codex/sessions").resolve()
        self.claude_root = Path(claude_root or Path(os.environ.get('CLAUDE_CONFIG_DIR') or Path.home() / '.claude') / 'projects').resolve()
        self.claude_sessions = ClaudeSessions(self.claude_root.parent)
        self.lock = threading.RLock()
        self.cache = OrderedDict()
        self.cache_bytes = 0
        self.metrics = dict(bytes_read=0, records=0, resets=0, cache_hits=0)
        self.paths = []
        # Each rollout's working directory (its first record never changes), and the rollouts
        # per directory newest first, so an idle agent's session is found however old it is.
        self.cwds = {}
        self.by_cwd = {}
        self.matched_paths = OrderedDict()
        self.scanned = 0

    def candidates(self):
        if time.monotonic() - self.scanned > 30:
            paths = []
            # A rollout stays in its creation-date directory when resumed.
            # Rank by its actual activity, not the date of the parent folder:
            # short supervisor runs must not hide older, still-live sessions.
            for path in self.root.glob("*/*/*/rollout-*.jsonl"):
                try:
                    if path.is_file() and not path.is_symlink():
                        paths.append((path.stat().st_mtime_ns, path))
                except OSError:
                    continue  # A session can rotate during discovery.
            ranked = sorted(paths, reverse=True)
            self.paths = [path for _, path in ranked[:50]]
            # Short supervisor runs fill the recent window quickly; an agent left idle for a
            # few days falls out of it. Its directory still finds it (see for_cwd).
            by_cwd = {}
            for _, path in ranked:
                key = str(path)
                if key not in self.cwds:
                    try:
                        with open(path, 'rb') as f:
                            head = f.read(65536)
                        found = re.search(rb'"cwd":"((?:[^"\\]|\\.)*)"', head)
                        self.cwds[key] = json.loads(b'"' + found.group(1) + b'"') if found else ''
                    except (OSError, ValueError):
                        continue
                if self.cwds[key]:
                    by_cwd.setdefault(self.cwds[key], []).append(path)
            self.by_cwd = by_cwd
            self.scanned = time.monotonic()
        return self.paths

    def for_cwd(self, cwd, limit=8):
        """The newest Codex sessions started in [cwd], however long ago."""
        self.candidates()
        return self.by_cwd.get(cwd, [])[:limit] if cwd else []

    def read(self, path, history=False):
        return self._read_tail(Path(path), 'codex', history)

    def read_claude(self, path, history=False):
        return self._read_tail(Path(path), 'claude', history)

    def _parse_record(self, line, state, runtime, max_text=120_000):
        try:
            record = json.loads(line)
        except (ValueError, UnicodeError):
            return
        if not isinstance(record, dict):
            return
        self.metrics['records'] += 1
        if runtime == 'codex':
            payload = record.get('payload', {})
            if not isinstance(payload, dict):
                return
            if record.get('type') == 'turn_context' or (record.get('type') == 'event_msg' and payload.get('type') == 'task_started'):
                if payload.get('turn_id') and (payload.get('turn_id') != state['turn'] or payload.get('type') == 'task_started'):
                    state['lifecycle'] = dict(state='running', turn_id=payload['turn_id'], at=record.get('timestamp', ''))
                    state.pop('pending_turn', None)
                    state.pop('pending_final_digest', None)
                state['turn'] = payload.get('turn_id', '')
            if record.get('type') == 'event_msg' and payload.get('type') in ('task_complete', 'turn_aborted'):
                turn = payload.get('turn_id')
                pending = state.get('pending_turn')
                reply = payload.get('last_agent_message')
                reply_digest = hashlib.sha256(reply.encode()).hexdigest() if isinstance(reply, str) else ''
                new_final = bool(pending and reply_digest and reply_digest == state.get('pending_final_digest'))
                if turn and turn != pending and ((not pending and (not state['turn'] or turn == state['turn'])) or new_final):
                    completed = payload['type'] == 'task_complete'
                    state['turn'] = turn
                    state.pop('pending_turn', None)
                    state.pop('pending_final_digest', None)
                    state['lifecycle'] = dict(state='done' if completed else 'interrupted', turn_id=turn, at=record.get('timestamp', ''),
                                              has_reply=completed and isinstance(reply, str) and bool(reply.strip()),
                                              reply_digest=reply_digest)
            def resume():
                state.pop('pending_final_digest', None)
                lifecycle = state.get('lifecycle', {})
                if lifecycle.get('state') in ('done', 'interrupted'):
                    # A new input/tool/progress record can precede task_started
                    # in a flushed log. Reject late completion of the old turn.
                    state['pending_turn'] = lifecycle.get('turn_id') or state['turn']
                    state['lifecycle'] = dict(state='running', turn_id='', at=record.get('timestamp', ''))
            if record.get('type') == 'response_item' and payload.get('type') in ('function_call', 'custom_tool_call', 'reasoning'):
                resume()
            if record.get('type') != 'response_item' or payload.get('type') != 'message' or payload.get('role') not in ('user', 'assistant'):
                return
            if payload.get('phase') not in (None, 'commentary', 'final_answer') or payload.get('channel') not in (None, 'commentary', 'final'):
                if payload.get('role') == 'assistant':
                    resume()
                return
            content = payload.get('content', [])
            if not isinstance(content, list):
                return
            parts = [item['text'] for item in content if isinstance(item, dict) and item.get('type') in ('output_text', 'input_text') and isinstance(item.get('text'), str)]
            original = '\n'.join(parts)
            body = user_text('\n'.join(part for value in input_text_parts(content) if (part := user_text(value)))) if payload['role'] == 'user' else original
            if not body or len(body) > max_text:
                return
            if (payload['role'] == 'user' or payload.get('phase') == 'commentary' or payload.get('channel') == 'commentary' or
                    hashlib.sha256(original.encode()).hexdigest() != state.get('lifecycle', {}).get('reply_digest')):
                resume()
            if state.get('pending_turn') and payload['role'] == 'assistant' and payload.get('phase') != 'commentary' and payload.get('channel') != 'commentary':
                state['pending_final_digest'] = hashlib.sha256(original.encode()).hexdigest()
            item = {'id': hashlib.sha256((record.get('timestamp', '') + original).encode()).hexdigest()[:20],
                    'text': body, 'role': payload['role'], 'phase': payload.get('phase') or ('commentary' if payload.get('channel') == 'commentary' else ''), 'created': record.get('timestamp', ''),
                    'fork': {'thread': state['session'], 'turn': state['turn']} if state['session'] and state['turn'] and payload['role'] == 'user' else None}
        else:
            # A message sent while Claude works is taken in mid-turn and logged as a queued
            # command, not as a user record; it is still what the user said.
            queued = record.get('attachment') if record.get('type') == 'attachment' else None
            if (isinstance(queued, dict) and queued.get('type') == 'queued_command' and queued.get('commandMode', 'prompt') == 'prompt'
                    and isinstance(queued.get('prompt'), (str, list))):
                record = {**record, 'type': 'user', 'message': {'role': 'user', 'content': queued['prompt']}}
            if record.get('type') not in ('user', 'assistant') or record.get('isSidechain'):
                return
            payload = record.get('message', {})
            if not isinstance(payload, dict) or payload.get('role') not in ('user', 'assistant'):
                return
            content = payload.get('content', [])
            body = content if isinstance(content, str) else '\n'.join(item['text'] for item in content if isinstance(item, dict) and item.get('type') == 'text' and isinstance(item.get('text'), str)) if isinstance(content, list) else ''
            original = body
            if payload['role'] == 'user':
                body = user_text('\n'.join(text for part in content if isinstance(part, dict) and
                                          part.get('type') == 'text' and isinstance(part.get('text'), str) and
                                          (text := user_text(part['text'])))) if isinstance(content, list) else user_text(body)
            # A session's records follow its shell into subfolders (cd into a run directory and
            # back); only an unrelated folder means another session. The session's folder is the
            # outermost one seen.
            cwd = record.get('cwd', '')
            if cwd != state['cwd'] and not within(cwd, state['cwd']):
                if state['cwd'] and within(state['cwd'], cwd):
                    state['cwd'] = cwd
                else:
                    state['cwd'], state['messages'] = cwd, []
                    state['lifecycle'] = {}
            display = claude_display(record, body) if body and payload['role'] == 'user' else {'role': 'assistant', 'text': body} if body else None
            claude_lifecycle(record, payload, body, display, state)
            if not body or len(body) > max_text or not display:
                return
            # Only structured tool-use metadata identifies progress. Ordinary
            # answers, even tentative prose, remain fully visible.
            progress = payload['role'] == 'assistant' and (payload.get('stop_reason') == 'tool_use' or
                       isinstance(content, list) and any(isinstance(part, dict) and part.get('type') == 'tool_use' for part in content))
            identity = record.get('uuid') or record.get('timestamp') or ''
            identity = identity if isinstance(identity, str) else ''
            item = {'id': hashlib.sha256((identity+'\0'+original).encode()).hexdigest()[:20], 'created': record.get('timestamp', ''),
                    'phase': 'commentary' if progress else '', **display}
            if state['messages'] and state['messages'][-1]['id'] == item['id']:
                return
        state['messages'].append(item)
        if len(state['messages']) > 200:
            del state['messages'][:-200]

    def _read_tail(self, path, runtime, history):
        allowed = self.root if runtime == 'codex' else self.claude_root
        resolved = path.resolve()
        if path.is_symlink() or not resolved.is_relative_to(allowed):
            return '', []
        key = (resolved, runtime, bool(history))
        with self.lock:
            stat = path.stat()
            stamp = (stat.st_dev, stat.st_ino, stat.st_mtime_ns, stat.st_size)
            cached = self.cache.get(key)
            if cached and cached['stamp'] == stamp:
                self.metrics['cache_hits'] += 1
                self.cache.move_to_end(key)
                return cached['view']['cwd'], list(cached['view']['messages'])
            with path.open('rb') as file:
                def read(size):
                    value = file.read(size)
                    self.metrics['bytes_read'] += len(value)
                    return value
                # A small prefix and committed-tail anchor detect replacement,
                # truncation and rewritten tails before trusting append offsets.
                prefix = read(min(256, stat.st_size))
                append = bool(cached and stamp[:2] == cached['stamp'][:2] and stat.st_size > cached['stamp'][3]
                              and stat.st_size - cached['offset'] <= (16_000_000 if history else 2_000_000)
                              and prefix[:len(cached['prefix'])] == cached['prefix'])
                if append:
                    file.seek(max(0, cached['offset'] - 256))
                    append = read(min(256, cached['offset'])) == cached['guard']
                if append:
                    offset, state = cached['offset'], copy.deepcopy(cached['state'])
                else:
                    self.metrics['resets'] += 1
                    state = dict(cwd='', session='', turn='', lifecycle={}, messages=[])
                    if runtime == 'codex':
                        file.seek(0)
                        meta_line = file.readline(512_000)
                        self.metrics['bytes_read'] += len(meta_line)
                        meta = json.loads(meta_line)
                        if not isinstance(meta, dict) or meta.get('type') != 'session_meta' or not isinstance(meta.get('payload'), dict):
                            return '', []
                        state.update(cwd=meta['payload'].get('cwd', ''), session=meta['payload'].get('id', ''))
                        if not state['cwd']:
                            return '', []
                    offset = max(0, stat.st_size - (16_000_000 if history else 2_000_000))
                    if offset:
                        file.seek(offset - 1)
                        if read(1) != b'\n':
                            skipped = file.readline()
                            self.metrics['bytes_read'] += len(skipped)
                            offset = file.tell()
                file.seek(offset)
                data = read(max(0, stat.st_size - offset))
                committed = data.rfind(b'\n') + 1
                for line in data[:committed].splitlines():
                    self._parse_record(line, state, runtime)
                offset += committed
                # A complete JSON record without a newline is visible, but its
                # offset is not committed: the writer may still be finishing it.
                view = copy.deepcopy(state)
                if data[committed:]:
                    self._parse_record(data[committed:], view, runtime)
                file.seek(max(0, offset - 256))
                guard = read(min(256, offset))
            weight = sum(len(item['text'].encode()) for item in state['messages'] + view['messages']) + 1024
            if cached:
                self.cache_bytes -= cached['weight']
                del self.cache[key]
            if weight <= 32 * 1024 * 1024:
                self.cache[key] = dict(stamp=stamp, prefix=prefix, guard=guard, offset=offset, state=state, view=view, weight=weight)
                self.cache_bytes += weight
            while len(self.cache) > 120 or self.cache_bytes > 32 * 1024 * 1024:
                self.cache_bytes -= self.cache.popitem(last=False)[1]['weight']
            return view['cwd'], list(view['messages'])

    def observe(self, binding, terminal):
        """Use structured turn events only after this pane's session is verified."""
        try:
            path = Path(binding['path']).resolve()
            with self.lock:
                cwd, _ = self.read(path)
                cached = self.cache.get((path, 'codex', False), {}).get('view', {})
                if cwd != binding.get('cwd') or cached.get('session') != binding.get('thread_id'):
                    return terminal
                lifecycle = cached.get('lifecycle', {})
        except (OSError, ValueError, KeyError, TypeError):
            return terminal
        if not lifecycle:
            return terminal
        result = {**terminal, 'turn_status':dict(lifecycle)}
        # A current approval, runtime error or live spinner takes precedence.
        if terminal.get('state') in ('waiting', 'blocked') or terminal.get('activity_confirmed'):
            return result
        if lifecycle['state'] == 'running':
            result.update(state='running', summary='Working', completion_confirmed=False)
        elif lifecycle.get('has_reply'):
            result.update(state='done', summary='Turn ended; ready for review', completion_confirmed=True)
        else:
            # Capacity failures and automatic continuations can close an internal
            # turn without delivering a final response to the user.
            result.update(state='idle', summary='Turn interrupted' if lifecycle['state'] == 'interrupted' else 'No final response yet', completion_confirmed=False)
        return result

    def observe_claude(self, agent, connection, terminal, history=False):
        """Scope completion to the verified process's current user UUID."""
        try:
            identity = self.claude_sessions.binding(agent, connection)
            if not identity:
                return terminal
            path = Path(identity['path']).resolve()
            with self.lock:
                cwd, _ = self.read_claude(path, history)
                lifecycle = dict(self.cache.get((path, 'claude', bool(history)), {}).get('view', {}).get('lifecycle', {}))
            if not (cwd == identity['cwd'] or within(cwd, identity['cwd'])) or self.claude_sessions.binding(agent, connection) != identity:
                return terminal
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            return terminal
        if not lifecycle:
            return terminal
        result = {**terminal, 'turn_status':lifecycle}
        # An end_turn message identifies a reply, but only the current terminal
        # completion confirms a handoff. New native input/progress invalidates
        # an older completion footer even before the next spinner is visible.
        if terminal.get('state') == 'done' and lifecycle.get('state') == 'running':
            result.update(state='running', completion_confirmed=False, summary='Working')
        # Some Claude versions leave stop_reason null on a finished text
        # message. That is not proof of progress; retain an affirmative footer.
        return result

    def match(self, agent, capture, hints=(), history=False, binding=False, connection=None):
        if connection is not None:
            identity = self.claude_sessions.binding(agent, connection)
            if identity:
                cwd, messages = self.read_claude(identity['path'], history)
                if (cwd == identity['cwd'] or within(cwd, identity['cwd']) or not cwd and not messages) and self.claude_sessions.binding(agent, connection) == identity:
                    selected = messages[-(200 if history else 8):]
                    while sum(len(item['text']) for item in selected) > (500_000 if history else 150_000) and len(selected) > 1:
                        selected.pop(0)
                    return {'source': 'original', 'messages': selected, 'history': history,
                            'session_verified': True,
                            'history_note': 'Up to 200 messages from the last 16 MB of the active Claude session.' if history else '',
                            **({'archive_source': identity['path']} if binding else {})}
        draft = editor_draft(capture)
        capture = terminal_content(capture)
        runtime = agent.get("runtime")
        if runtime not in ("codex", "claude") or len(capture) < 160:
            return {}
        dialogue = without_draft(visible_dialogue(capture), draft)
        prompt_records = [(body, clipped, start) for role, body, clipped, start in dialogue if role == 'user']
        matches = []
        with self.lock:
            if runtime == "claude":
                project = re.sub(r"[^a-zA-Z0-9-]", "-", agent.get("cwd", ""))
                paths = sorted((self.claude_root / project).glob("*.jsonl"), key=lambda path: path.stat().st_mtime, reverse=True)[:20]
            else:
                paths = self.candidates()
                paths = paths + [path for path in self.for_cwd(agent.get('cwd', '')) if path not in paths]
            # Long-lived sessions may have been created outside the recent date
            # window. Hints only add candidates; identity still requires a match.
            allowed = self.claude_root if runtime == "claude" else self.root
            # Remember candidates for this exact process, but recheck their
            # visible content on every use. New supervisor sessions must not
            # evict the history of an agent that is waiting for its user.
            previous = self.matched_paths.get(agent.get('id'))
            for hint in ([previous] if previous else []) + list(hints):
                try:
                    path = Path(hint)
                    if path.suffix == ".jsonl" and path.resolve().is_relative_to(allowed) and path.is_file():
                        paths = [path, *[item for item in paths if item != path]]
                except OSError:
                    continue
            for path in paths:
                try:
                    cwd, messages = self.read_claude(path, history) if runtime == "claude" else self.read(path, history)
                except (OSError, ValueError, TypeError):
                    continue
                if cwd != agent.get("cwd"):
                    continue
                evidence = short_dialogue_evidence(messages, dialogue, cwd) or last_reply_evidence(messages, dialogue, cwd)
                for index in range(len(messages) - 1, -1, -1):
                    role = messages[index].get("role")
                    if role not in ('assistant', 'user'):
                        continue
                    if role == 'user':
                        body = normalized(messages[index]['text'])
                        score, visible_end = prompt_evidence(body, prompt_records)
                        visible_start = visible_end - score
                    else:
                        # Multiple substantial windows tolerate terminal-rendered
                        # links, tables and wrapping without fuzzy cross-pane guesses.
                        variants = visible_variants(messages[index]["text"], cwd)
                        score, visible_start, visible_end = assistant_evidence(variants, dialogue)
                    if score >= 160:
                        if evidence is None or visible_end > evidence[2]:
                            evidence = (score, visible_start, visible_end, index)
                        break
                if evidence:
                    score, visible_start, visible_end, index = evidence
                    cached = self.cache.get((path.resolve(), runtime, bool(history)), {})
                    identity = {'thread_id': cached.get('view', {}).get('session', ''),
                                'path': str(path.resolve()), 'cwd': cwd} if runtime == 'codex' else {}
                    matches.append((score, path, index, messages, identity, visible_start, visible_end))
        if not matches:
            return {}
        # Restored scrollback can contain a much longer reply from an earlier
        # session. Prefer the newest evidence, retaining the ambiguity margin
        # for candidates that overlap the same visible dialogue.
        latest = max(matches, key=lambda row: row[-1])
        matches = sorted((row for row in matches if row[-1] > latest[-2]), key=lambda row: row[0], reverse=True)
        if len(matches) > 1 and matches[0][0] - matches[1][0] < 160:
            return {}
        _, path, index, messages, identity, _, anchor_end = matches[0]
        # A terminal user turn is visible before its reply exists. Verify both
        # roles against this pane, including short inputs such as "continue".
        for later in range(index + 1, len(messages)):
            body = normalized(messages[later]["text"])
            role = messages[later].get('role')
            variants = visible_variants(messages[later]['text'], agent.get('cwd', '')) if role == 'assistant' else (body,)
            ends = []
            for visible_role, text, clipped, start in dialogue:
                if role == 'assistant' and visible_role == 'partial':
                    # The first record can start above the captured viewport.
                    ends.extend(start + found + len(value) for value in variants if len(value) >= 12
                                and (found := text.find(value, max(0, anchor_end - start))) >= 0)
                elif visible_role == role and start >= anchor_end and body:
                    if text in variants or (role == 'user' and len(clipped) >= 160 and body.startswith(clipped)):
                        ends.append(start + (len(clipped) if text not in variants else len(text)))
            if ends:
                index, anchor_end = later, min(ends)
        # A new session can begin in the same pane before enough of its log is
        # available to identify it. Older scrollback must not supply a binding
        # or hide the new terminal dialogue in that interval.
        if any(role in ('user', 'assistant') and text and start >= anchor_end
               for role, text, _, start in dialogue):
            return {}
        if agent.get('id'):
            with self.lock:
                self.matched_paths[agent['id']] = str(path.resolve())
                self.matched_paths.move_to_end(agent['id'])
                while len(self.matched_paths) > 128:
                    self.matched_paths.popitem(last=False)
        # Stop at the actual matched message, not a newer invisible reply.
        selected = messages[max(0, index - (199 if history else 7)):index + 1]
        while sum(len(item["text"]) for item in selected) > (500_000 if history else 150_000) and len(selected) > 1:
            selected.pop(0)
        return {"source": "original", "messages": selected, "history": history,
                "history_note": "Up to 200 messages from the last 16 MB of the matched session." if history else "",
                **({'archive_source': str(path.resolve())} if binding else {}),
                **({'binding': identity} if binding and identity.get('thread_id') else {})}
