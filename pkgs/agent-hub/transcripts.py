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

from bridge import terminal_content
from claude_sessions import ClaudeSessions
from message_content import user_text


def normalized(text):
    return re.sub(r"[^\w]", "", text, flags=re.UNICODE).lower()


def visible_variants(text, cwd):
    """Terminal links may show their label, a full path, or a relative path."""
    labels = re.sub(r"!?\[([^\]\n]*)\]\((?:<[^>\n]*>|[^)\n]*)\)", r"\1", text)
    return tuple(dict.fromkeys(normalized(value) for value in (
        text, text.replace(cwd.rstrip("/") + "/", ""), labels)))


def visible_prompts(capture):
    """Explicit prompt records, with clipping kept separate from their contents.

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
    prompts = []
    for index, (marker, _, start) in enumerate(records):
        if marker not in '›❯':
            continue
        end = records[index + 1][1] if index + 1 < len(records) else len(capture)
        text = capture[start:end]
        clipped = re.search(r"(?:…|\.{3})[ \t]*(?:\n|$)", text)
        prompts.append((normalized(text), normalized(text[:clipped.start()]) if clipped else '',
                        len(normalized(capture[:start]))))
    return prompts


def prompt_evidence(body, prompts):
    """Return exact prefix length and position, never a fuzzy text match."""
    evidence = []
    for text, clipped, start in prompts:
        size = len(body) if text == body else len(clipped) if clipped and body.startswith(clipped) else 0
        if size >= 160:
            evidence.append((size, start + size))
    return max(evidence, default=(0, 0))


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
            self.paths = [path for _, path in sorted(paths, reverse=True)[:50]]
            self.scanned = time.monotonic()
        return self.paths

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
            if record.get('cwd', '') != state['cwd']:
                state['cwd'], state['messages'] = record.get('cwd', ''), []
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
            if cwd != identity['cwd'] or self.claude_sessions.binding(agent, connection) != identity:
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
                if (cwd == identity['cwd'] or not cwd and not messages) and self.claude_sessions.binding(agent, connection) == identity:
                    selected = messages[-(200 if history else 8):]
                    while sum(len(item['text']) for item in selected) > (500_000 if history else 150_000) and len(selected) > 1:
                        selected.pop(0)
                    return {'source': 'original', 'messages': selected, 'history': history,
                            'session_verified': True,
                            'history_note': 'Up to 200 messages from the last 16 MB of the active Claude session.' if history else '',
                            **({'archive_source': identity['path']} if binding else {})}
        capture = terminal_content(capture)
        runtime = agent.get("runtime")
        if runtime not in ("codex", "claude") or len(capture) < 160:
            return {}
        visible = normalized(capture)
        prompt_records = visible_prompts(capture)
        matches = []
        with self.lock:
            if runtime == "claude":
                project = re.sub(r"[^a-zA-Z0-9-]", "-", agent.get("cwd", ""))
                paths = sorted((self.claude_root / project).glob("*.jsonl"), key=lambda path: path.stat().st_mtime, reverse=True)[:20]
            else:
                paths = self.candidates()
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
                    cwd, messages = self.read_claude(path) if runtime == "claude" else self.read(path)
                except (OSError, ValueError, TypeError):
                    continue
                if cwd != agent.get("cwd"):
                    continue
                for index in range(len(messages) - 1, -1, -1):
                    role = messages[index].get("role")
                    if role not in ('assistant', 'user'):
                        continue
                    visible_end = None
                    if role == 'user':
                        anchor = normalized(messages[index]['text'])
                        score, visible_end = prompt_evidence(anchor, prompt_records)
                    else:
                        # Multiple substantial windows tolerate terminal-rendered
                        # links, tables and wrapping without fuzzy cross-pane guesses.
                        variants = visible_variants(messages[index]["text"], cwd)
                        score, anchor = max((sum(80 for start in range(0, len(body) - 79, 80)
                                                 if body[start:start + 80] in visible), body) for body in variants)
                    if score >= 160:
                        cached = self.cache.get((path.resolve(), runtime, False), {})
                        identity = {'thread_id': cached.get('view', {}).get('session', ''),
                                    'path': str(path.resolve()), 'cwd': cwd} if runtime == 'codex' else {}
                        matches.append((score, path, index, messages, anchor, identity, visible_end))
                        break
        matches.sort(key=lambda row: row[0], reverse=True)
        if not matches or (len(matches) > 1 and matches[0][0] - matches[1][0] < 160):
            return {}
        _, path, index, messages, anchor, identity, visible_end = matches[0]
        if agent.get('id'):
            with self.lock:
                self.matched_paths[agent['id']] = str(path.resolve())
                self.matched_paths.move_to_end(agent['id'])
                while len(self.matched_paths) > 128:
                    self.matched_paths.popitem(last=False)
        if history:
            matched = messages[index]["id"]
            with self.lock:
                _, expanded = self.read_claude(path, True) if runtime == "claude" else self.read(path, True)
            found = [i for i, item in enumerate(expanded) if item["id"] == matched and item.get("role") == messages[index].get("role")]
            if found:
                messages, index = expanded, found[-1]
        # A terminal user turn is visible before its reply exists. Verify both
        # roles against this pane, including short inputs such as "continue".
        ends = [visible.rfind(anchor[start:start + 80]) + 80 for start in range(0, len(anchor) - 79, 80) if anchor[start:start + 80] in visible]
        anchor_end = visible_end if visible_end is not None else max(ends, default=0)
        prompts = [normalized(capture[match.end():]) for match in re.finditer(r"(?m)^[›❯][ \t]+", capture)
                   if len(normalized(capture[:match.start()])) >= anchor_end]
        for later in range(index + 1, len(messages)):
            body = normalized(messages[later]["text"])
            if messages[later].get("role") == "assistant":
                variants = visible_variants(messages[later]["text"], agent.get("cwd", ""))
                ends = [start + len(value) for value in variants if len(value) >= 12
                        and (start := visible.find(value, anchor_end)) >= 0]
                if ends:
                    index, anchor_end = later, min(ends)
            elif messages[later].get("role") == "user" and body and any(prompt.startswith(body) for prompt in prompts):
                index = later
        # Stop at the actual matched message, not a newer invisible reply.
        selected = messages[max(0, index - (199 if history else 7)):index + 1]
        while sum(len(item["text"]) for item in selected) > (500_000 if history else 150_000) and len(selected) > 1:
            selected.pop(0)
        return {"source": "original", "messages": selected, "history": history,
                "history_note": "Up to 200 messages from the last 16 MB of the matched session." if history else "",
                **({'archive_source': str(path.resolve())} if binding else {}),
                **({'binding': identity} if binding and identity.get('thread_id') else {})}
