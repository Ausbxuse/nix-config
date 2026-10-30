"""Small tmux adapter. All targets are qualified by a connection and pane PID."""

import hashlib
import re
import shlex
import subprocess
import time
import uuid

SEP = "\x1f"
FIELDS = {
    "session": "session_name", "window": "window_id", "window_name": "window_name",
    "session_id": "session_id", "window_index": "window_index", "pane_index": "pane_index",
    "pane": "pane_id", "pid": "pane_pid", "command": "pane_current_command",
    "cwd": "pane_current_path", "role": "@pane-role", "owner": "@workbench-window",
    "primary": "@agent-pane", "state": "@agent-state", "summary": "@agent-summary",
    "updated": "@agent-updated", "dead": "pane_dead",
    "cursor_x": "cursor_x", "cursor_y": "cursor_y", "in_mode": "pane_in_mode",
}
FORMAT = SEP.join("#{" + value + "}" for value in FIELDS.values())
RUNTIMES = ("codex", "claude", "hermes", "opencode", "aider")
# Match the CLI error marker and reason, independently of the suggested next
# step. Narrow panes can wrap any space in the sentence, including before 'at'.
CAPACITY = re.compile(
    r"^(?:■|⚠\ufe0f?)[ \t]+(?:(?:Selected|The)\s+)?model\s+is\s+(?:currently\s+)?at\s+capacity"
    r"(?:\.|(?=[ \t]*$))", re.M | re.I)
COMPACTION_STATUS = re.compile(
    r"^\s*[•●✻✽✶✢✳·*]\s+(?:Auto[- ]?)?Compacting(?:\s+(?:context|conversation))?\s*(?:…|\.{3})?"
    r"(?:\s*\((?:\d+[hms]\s*)+(?:[•·][^)\n]*)?\))?"
    r"(?:\s*·\s*\d+ background terminals? running(?:\s*·[^\n]*)?)?\s*$", re.I)
CODEX_PLACEHOLDERS = {"Ask Codex to do anything", "Find and fix a bug in @filename",
                      "Improve documentation in @filename", "Write tests for @filename",
                      "Summarize recent commits", "Explain this codebase", "Implement {feature}",
                      "Run /review on my current changes", "Use /skills to list available skills"}
QUOTA_NOTICE = re.compile(
    r"^⚠\ufe0f?\s+Heads up, you have (?:less than )?<?\d+(?:\.\d+)?% of your "
    r"(?:weekly|daily|session|5h|5-hour) limit left\.(?: Run /status for a breakdown\.)?$", re.I)
QUOTA_FOOTER = re.compile(
    r"^⚠\ufe0f?\s+(?:weekly|daily|session|5h|5-hour) limit: only <?\d+(?:\.\d+)?% left"
    r"\s*·\s*resets [^\n]+\s*·\s*/status$", re.I)
ACTIVITY_ROW = re.compile(
    r"^[•●✻✽✶✢✳·*]\s+[^()\n]+\((?:\d+[hms]\s*)+[•·][^)\n]*"
    r"(?:esc to interrupt|tokens\b)[^)\n]*\)(?:\s*[·•].*)?[ \t]*$", re.I)
COMPLETION_ROW = re.compile(
    r"^(?:Worked for |[✻✽✶✢✳]\s+[^\n]+?\s+for )\d+(?:\.\d+)?(?:ms|[hms])\b[^\n]*$", re.I)
COMPLETED_AT = re.compile(
    r"^done\s+(?:(?:[A-Z][a-z]{2,8}\s+\d{1,2}(?:,\s*\d{4})?|today|yesterday)\s+at\s+)?"
    r"\d{1,2}:[0-5]\d(?::[0-5]\d)?(?:\s*[AP]M)?$", re.I)
RECAP_HEADER = re.compile(r"^[─━]+\s*Conversation recap\s*[─━]+\s*$", re.I)
EDITOR_BORDER = re.compile(r"^[─━]{8,}\s*$")
CODEX_STATUS = re.compile(r"^(?:GPT-|gpt-).+ · .+(?:left|context)")
TOOL_PREFIX = re.compile(r'Ran |Failed \(exit |Exited \(status \d+\) |Explored\b|Edited |Called |Searched |Read \d+ |Wrote |Updated (?:plan|file)|(?:Bash|Read|Write|Edit|Grep|Glob|WebSearch|WebFetch|Task)\(')


def terminal_rows(text):
    """Separate terminal UI regions from content before matching or rendering.

    The reader uses the same row kinds; shared fixtures keep the two paths in
    agreement. Never apply this to original structured conversation messages.
    """
    lines = str(text).splitlines()
    editor, codex_footer = None, None
    # Codex's model/status row starts the footer below its final input prompt.
    # Treat that region as UI, including new shortcut hints, not user content.
    for index in range(len(lines) - 1, -1, -1):
        if not re.match(r'^›(?:\s|$)', lines[index]):
            continue
        if not any(re.match(r'^[•●›❯■](?:\s|$)', line) for line in lines[index + 1:]):
            codex_footer = next((i for i in range(index + 1, len(lines))
                                 if CODEX_STATUS.match(lines[i].strip())), None)
        break
    # Claude draws the current editor between two full-width rules. Everything
    # below that closing rule is UI, including arbitrary custom status lines.
    # A later conversation marker means this is an older/quoted editor instead.
    for index in range(len(lines) - 1, 0, -1):
        if not re.match(r'^❯(?:\s|$)', lines[index]) or not EDITOR_BORDER.fullmatch(lines[index - 1]):
            continue
        end = next((i for i in range(index + 1, min(len(lines), index + 34))
                    if EDITOR_BORDER.fullmatch(lines[i])), None)
        if end is not None and not any(re.match(r'^[•●›❯■](?:\s|$)', line) for line in lines[end + 1:]):
            editor = index
            break
    rows, fence, fence_indent, recap_indent, role = [], None, 0, None, ''
    for index, line in enumerate(lines):
        trimmed = line.strip()
        indent = len(line) - len(line.lstrip())
        delimiter = re.match(r'^(`{3,}|~{3,})', trimmed)
        if fence and trimmed and indent < fence_indent:
            fence = None
        if editor is not None and index >= editor - 1:
            if fence:
                # A quoted editor must not hide prose after its closing fence.
                editor = None
            else:
                if index == editor:
                    rows.append(('editor', line))
                continue
        if codex_footer is not None and index >= codex_footer:
            if fence:
                codex_footer = None
            else:
                rows.append(('chrome', line))
                continue
        if fence or delimiter:
            rows.append(('content', line))
            if not fence:
                fence, fence_indent = delimiter[1], indent
            elif delimiter and delimiter[1][0] == fence[0] and len(delimiter[1]) >= len(fence):
                fence = None
            recap_indent = None
            continue
        if recap_indent is not None:
            if not trimmed:
                rows.append(('content', line))
                continue
            if indent >= recap_indent:
                continue
            recap_indent = None
        if ((indent == 0 and QUOTA_NOTICE.fullmatch(trimmed)) or QUOTA_FOOTER.fullmatch(trimmed)):
            continue
        if RECAP_HEADER.fullmatch(line):
            recap_indent = 2
            continue
        recap = re.match(r'^(?:※\s+recap:\s*| {0,2}↳\s+Recap:\s*)', line, re.I)
        if recap and (line.startswith('※') or role not in ('input', 'queued', 'tool')):
            recap_indent = 2 if line.startswith('※') else indent + 2
            continue
        if trimmed == '(disable recaps in /config)':
            continue
        # Indented user examples and tool output retain their literal content.
        protected = indent > 0 and role in ('input', 'queued', 'tool')
        if not protected and (ACTIVITY_ROW.fullmatch(line) or COMPACTION_STATUS.fullmatch(line)):
            rows.append(('activity', line))
            continue
        if not protected and (COMPLETION_ROW.fullmatch(trimmed) or
                              (indent <= 2 and COMPLETED_AT.fullmatch(trimmed))):
            rows.append(('completion', line))
            continue
        marker = re.match(r'^[•●]\s+(.+)', line)
        if marker:
            role = ('queued' if marker[1].startswith('Queued follow-up inputs') else
                    'tool' if TOOL_PREFIX.match(marker[1]) else 'assistant')
        elif re.match(r'^[›❯](?:\s|$)', line):
            role = 'input'
        rows.append(('content', line))
    return rows


def terminal_content(text):
    """Drop recognized CLI notices, not original messages or arbitrary warnings.

    Keep this capture-only filter in sync with reader.js; shared fixtures test
    both paths. The raw terminal remains available unchanged.
    """
    return '\n'.join(line for kind, line in terminal_rows(text) if kind == 'content').rstrip('\n')


class BridgeError(Exception):
    pass


def validate_connection(value):
    kind = value.get("kind", "ssh")
    if kind not in ("local", "ssh"):
        raise BridgeError("Connection must be local or SSH.")
    label = str(value.get("label", "")).strip()[:80]
    target = str(value.get("target", "")).strip()
    if not label:
        raise BridgeError("Give this connection a name.")
    if kind == "ssh" and not re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_.@:\[\]-]{0,200}", target):
        raise BridgeError("Use an SSH host alias or user@hostname from your SSH config.")
    socket = str(value.get("socket", "")).strip()
    if socket and (not socket.startswith("/") or any(c in socket for c in "\n\r\x00")):
        raise BridgeError("A tmux socket must be an absolute path.")
    return {"id": value.get("id") or uuid.uuid4().hex[:12], "kind": kind,
            "label": label, "target": target, "socket": socket}


def run(connection, args, stdin=None, timeout=8):
    command = ["tmux"]
    if connection.get("socket"):
        command += ["-S", connection["socket"]]
    command += args
    return run_command(connection, command, stdin=stdin, timeout=timeout)


def run_command(connection, command, stdin=None, timeout=8):
    if connection["kind"] == "ssh":
        command = ["ssh", "-a", "-o", "ForwardAgent=no", "-o", "BatchMode=yes", "-o", "ConnectTimeout=4",
                   "-o", "StrictHostKeyChecking=yes", "-o", "ServerAliveInterval=3",
                   "-o", "ServerAliveCountMax=1", "--", connection["target"], shlex.join(command)]
    try:
        result = subprocess.run(command, input=stdin, capture_output=True, text=True,
                                timeout=timeout, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise BridgeError(str(exc)) from exc
    if result.returncode:
        raise BridgeError((result.stderr.strip() or "tmux command failed")[:400])
    return result.stdout


def runtime_for(command):
    return next((kind for kind in RUNTIMES if kind in command.lower()), None)


def parse_panes(output, connection):
    found = {}
    for line in output.splitlines():
        values = line.split(SEP)
        if len(values) != len(FIELDS):
            continue
        pane = dict(zip(FIELDS, values))
        if pane["role"] in ("terminal", "editor", "supervisor"):
            continue
        runtime = runtime_for(pane["command"])
        if not runtime and pane["role"] != "agent":
            continue
        if pane["primary"] and pane["primary"] != pane["pane"]:
            continue
        key = f'{connection["id"]}:{pane["pane"]}:{pane["pid"]}:{pane["window"]}'
        pane.update(id=key, connection_id=connection["id"], connection_label=connection["label"],
                    runtime=runtime or "exited", live=bool(runtime) and pane["dead"] != "1")
        pane["state"] = {"needs-input": "waiting", "error": "blocked", "busy": "running"}.get(
            pane["state"], pane["state"] or "unknown")
        if not pane["live"]:
            pane.update(state="idle", summary="Agent exited; shell remains")
        pane["title"] = pane["window_name"] or pane["cwd"].rstrip("/").split("/")[-1] or pane["pane"]
        pane["summary"] = pane["summary"] or "No lifecycle hook yet"
        pane["observed_at"] = time.time()
        # Linked windows appear in multiple sessions; prefer the visible owner.
        if key not in found or found[key]["session"].startswith("__"):
            found[key] = pane
    return list(found.values())


def window_order(agent):
    """Use visible window numbers; states, names and activity never affect order."""
    def number(value):
        match = re.fullmatch(r'[$@%]?(\d+)', str(value))
        return int(match[1]) if match else float('inf')
    return (number(agent.get('window_index')), number(agent.get('session_id')),
            str(agent.get('session', '')), number(agent.get('pane_index')),
            number(agent.get('window')), number(agent.get('pane')), str(agent.get('id', '')))


def capture(connection, pane, lines=100):
    text = run(connection, ["capture-pane", "-p", "-J", "-t", pane, "-S", str(-lines)])
    # Full-width redraws can pad every row with hundreds of spaces. Remove
    # that padding before bounding the capture, or it discards real messages.
    return '\n'.join(line.rstrip() for line in text.splitlines())[-(200000 if lines > 150 else 24000):]


def capture_terminal(connection, pane, lines=150):
    """Read-only styled capture. Never pass its escapes into conversation parsers."""
    while True:
        text = run(connection, ['capture-pane', '-p', '-e', '-J', '-t', pane, '-S', str(-lines)])
        if len(text) <= 2 * 1024 * 1024:
            return text
        # Recapture a shorter history so tmux emits its correct initial style.
        # Cutting the beginning of an ANSI string would lose that state.
        if not lines:
            raise BridgeError('Terminal capture is too large')
        lines //= 2


def terminal_theme(connection):
    try:
        value = run(connection, ['show-environment', '-g', 'TMUX_COLOR_SCHEME']).strip()
        return 'light' if value == 'TMUX_COLOR_SCHEME=light' else 'dark'
    except BridgeError:
        return 'dark'


def capacity_error(pane, output):
    """Only the current Codex UI error, never an old error above a later turn."""
    if pane.get("runtime") != "codex" or not pane.get("live"):
        return False
    errors = list(CAPACITY.finditer(output))
    if not errors:
        return False
    after = output[errors[-1].end():]
    # A later warning can be a different runtime failure. Routine quota
    # footers are informational and do not replace the capacity failure.
    after = '\n'.join(line for line in after.splitlines()
                      if not QUOTA_NOTICE.fullmatch(line.strip()) and not QUOTA_FOOTER.fullmatch(line.strip()))
    if re.search(r"^(?:[•●■] |⚠\ufe0f? |Worked for |Turn interrupted)", after, re.M):
        return False
    # The current editor is allowed; a submitted subsequent input plus a new
    # editor means this error belongs to an earlier turn.
    return len(re.findall(r"^›(?:\s|$)", after, re.M)) <= 1


def empty_codex_editor(pane, output):
    """Check the real cursor and unjoined screen before automatic input."""
    if pane.get("in_mode") != "0":
        return False
    try:
        row, column = int(pane["cursor_y"]), int(pane["cursor_x"])
        lines = output.splitlines()
        line = lines[row] if 0 <= row < len(lines) else ""
    except (KeyError, ValueError, TypeError):
        return False
    prompt = re.fullmatch(r"›(?: (?P<text>.*))?", line)
    text = (prompt["text"] or "").strip() if prompt else ""
    return bool(prompt and column == 2 and (not text or text in CODEX_PLACEHOLDERS))


def terminal_completion(output):
    """A current completion footer, never a missing spinner or an old timer."""
    rows = terminal_rows(output.rstrip())
    prompts = [i for i, (_, line) in enumerate(rows) if re.match(r'^[›❯](?:\s|$)', line)]
    if not prompts:
        return False
    end = prompts[-1]
    completed = [i for i, (kind, line) in enumerate(rows[:end])
                 if kind == 'completion' and COMPLETION_ROW.fullmatch(line.strip())]
    if not completed:
        return False
    # The footer follows the answer. A newer prompt, tool step, commentary or
    # active indicator means that an earlier completed turn is no longer current.
    return all(kind in ('chrome', 'completion') or not line.strip()
               for kind, line in rows[completed[-1] + 1:end])


def handoff_state(agent):
    status = agent.get('state') if agent.get('live', True) else 'exited'
    if agent.get('live', True) and agent.get('activity_confirmed'):
        return 'running'
    if agent.get('recovery', {}).get('status') in ('retrying', 'checking', 'waiting'):
        return 'recovering'
    if status == 'done' and agent.get('runtime') in ('codex', 'claude') and not agent.get('completion_confirmed'):
        return 'unknown'
    return status


def handoff_delay(agent):
    # An explicit completed turn needs only a brief check for resumed/queued
    # work. Inferred terminal states retain the conservative settling window.
    return 1 if handoff_state(agent) == 'done' and agent.get('completion_confirmed') else 6


def classify(pane, output):
    """Visible UI signals override stale hook states; never interpret scrollback."""
    rows = terminal_rows(output.rstrip())[-24:]
    footer = '\n'.join(line for _, line in rows)
    state, summary = pane["state"], pane["summary"]
    if not pane["live"]:
        return state, summary
    if pane["runtime"] == "codex":
        if re.search(r"^\s*(?:› )?(?:approval requested:|needs your approval|do you want to approve|would you like to (?:run|apply))|^\s*.*enter to submit (?:answer|all)", footer, re.I | re.M):
            return "waiting", "Needs a decision in the agent"
        if any(kind == 'activity' for kind, _ in rows):
            return "running", "Working"
        if capacity_error(pane, output):
            return "blocked", "Selected model is at capacity"
        # A newly opened CLI can inherit the window's old lifecycle hook.
        # Its welcome screen is not a completed response from the new process.
        welcome = list(re.finditer(r"^\s*[│|]?\s*>_\s+OpenAI Codex\s*\(v[^\n]+", footer, re.M))
        if welcome and not re.search(r"^\s*•\s|^\s*Worked for ", footer[welcome[-1].end():], re.M):
            return "idle", "Ready for a new task"
        if terminal_completion(output):
            return "done", "Turn ended; ready for review"
        # This prompt is sufficient for manual terminal controls, but is only
        # a candidate state. observation() requires proof before a handoff.
        if re.search(r"^\s*›(?:\s|$)", footer, re.M) and state == "running":
            return "done", "Turn ended; ready for review"
    if pane["runtime"] == "claude":
        if re.search(r"^\s*(?:Do you want|❯\s*1\.)", footer, re.I | re.M):
            return "waiting", "Needs approval in the agent"
        if any(kind == 'activity' for kind, _ in rows):
            return "running", "Working"
        if terminal_completion(output):
            return "done", "Turn ended; ready for review"
        if re.search(r"^\s*❯\s*$", footer, re.M) and state == "running":
            return "done", "Turn ended; ready for review"
    return state, summary


def observation(pane, output):
    state, summary = classify(pane, output)
    completed = state == 'done' and terminal_completion(output)
    if pane.get('runtime') in ('codex', 'claude') and state == 'done' and not completed:
        state, summary = 'unknown', 'Waiting for a verified turn state'
    rows = terminal_rows(output.rstrip())
    active = next((line.strip() for kind, line in reversed(rows[-24:]) if kind == 'activity'), "") if state == "running" else ""
    phase = "Compacting" if COMPACTION_STATUS.fullmatch(active) else "Waiting for terminal" if "waiting for background terminal" in active.lower() else "Working"
    duration = re.search(r"\((?P<time>(?:\d+[hms]\s*)+)(?:[•·]|\))", active)
    elapsed = sum(int(value) * {"h": 3600, "m": 60, "s": 1}[unit] for value, unit in re.findall(r"(\d+)([hms])", duration.group('time'))) if duration else None
    content = "\n".join(line.rstrip() for line in terminal_content(output).splitlines()
                        if line.strip() and "esc to interrupt" not in line.lower()
                        and not COMPACTION_STATUS.fullmatch(line)
                        and not re.match(r"^\s*(?:[›❯]|(?:GPT-|gpt-)|← for agents|Worked for )", line))
    return {"state": state, "summary": summary, "activity_phase": phase if active else "",
            "activity_elapsed": elapsed, "activity_confirmed": bool(active),
            "completion_confirmed": completed,
            "capacity_error": state == "blocked" and capacity_error(pane, output),
            "activity_fingerprint": fingerprint(content), "observed_at": time.time()}


def fingerprint(text):
    return hashlib.sha256(text.encode()).hexdigest()[:20]


def list_agents(connection):
    agents = parse_panes(run(connection, ["list-panes", "-a", "-F", FORMAT]), connection)
    if not agents:
        return agents
    pids = ",".join(sorted({a["pid"] for a in agents if a["pid"].isdigit()}))
    if not pids:
        raise BridgeError("Could not identify the agent processes.")
    output = run_command(connection, ["ps", "-o", "pid=,tpgid=", "-p", pids])
    foregrounds = {}
    for line in output.splitlines():
        values = line.split()
        if len(values) == 2 and values[0].isdigit() and values[1].isdigit():
            foregrounds[values[0]] = values[1]
    for agent in agents:
        foreground = foregrounds.get(agent["pid"])
        agent["foreground_pid"] = foreground or ""
        agent["id"] += ":" + agent["foreground_pid"]
        if not foreground:
            agent.update(live=False, state="unknown", summary="Foreground process unavailable; refresh before controlling")
    return agents


def verify_agent(connection, expected):
    actual = list_agents(connection)
    current = next((a for a in actual if a["id"] == expected["id"]), None)
    if current is None or not current["live"]:
        raise BridgeError("That agent is no longer running. Refresh before sending.")
    return current


def send(connection, agent, message):
    current = verify_agent(connection, agent)
    state, _ = classify(current, capture(connection, current["pane"], 0))
    if state == "running":
        raise BridgeError("This agent is working. Pause it before sending a new instruction.")
    if state == "waiting":
        raise BridgeError("This agent has an interactive decision. Resolve it in tmux first.")
    if not isinstance(message, str) or not message.strip() or len(message) > 12000 or "\x00" in message:
        raise BridgeError("Send a message between 1 and 12,000 characters.")
    paste(connection, current, message)


def queue(connection, agent, message):
    """Use Codex's native deferred-input key; never interrupt the current turn."""
    current = verify_agent(connection, agent)
    if current.get('runtime') != 'codex':
        raise BridgeError('Queuing is available for Codex agents.')
    if not isinstance(message, str) or not message.strip() or len(message) > 12000 or '\x00' in message:
        raise BridgeError('Queue a message between 1 and 12,000 characters.')
    if message.lstrip().startswith(('/', '!')):
        raise BridgeError('Queue a plain-text instruction. Use the terminal for slash or shell commands.')
    screen = run(connection, ['capture-pane', '-p', '-t', current['pane'], '-S', '0'])
    state, _ = classify(current, screen)
    if state == 'waiting':
        raise BridgeError('This agent has an interactive decision. Resolve it in tmux first.')
    if not empty_codex_editor(current, screen):
        raise BridgeError('This agent has a terminal draft or open menu. Finish it before queuing.')
    # Tab queues during a turn and submits when idle. Enter can steer a running
    # Codex turn, so use Tab even if it finishes between the check and paste.
    paste(connection, current, message, key='Tab')
    return 'queued' if state == 'running' else 'sent'


def paste(connection, current, message, key='Enter'):
    buffer = "hub-" + uuid.uuid4().hex[:12]
    run(connection, ["load-buffer", "-b", buffer, "-", ";", "paste-buffer", "-p", "-d",
                     "-b", buffer, "-t", current["pane"], ";", "send-keys", "-t",
                     current["pane"], key], stdin=message)


def retry_capacity(connection, agent):
    current = verify_agent(connection, agent)
    # Keep wrapped screen rows intact so cursor_y addresses the actual editor.
    screen = run(connection, ["capture-pane", "-p", "-t", current["pane"], "-S", "0"])
    if classify(current, screen)[0] != "blocked" or not capacity_error(current, screen):
        raise BridgeError("Capacity error cleared; no retry sent")
    if not empty_codex_editor(current, screen):
        raise BridgeError("Retry waits for an empty terminal input")
    paste(connection, current, "continue")


def rename(connection, agent, name):
    if not isinstance(name, str) or not name.strip() or len(name) > 80 or any(ord(c) < 32 or ord(c) == 127 for c in name):
        raise BridgeError("Use a window name of 1–80 characters without control characters.")
    current = verify_agent(connection, agent)
    # Address the immutable window ID, never a name that can match another window.
    run(connection, ["set-option", "-w", "-t", current["window"], "automatic-rename", "off", ";",
                     "set-option", "-w", "-t", current["window"], "allow-rename", "off", ";",
                     "rename-window", "-t", current["window"], "--", name.strip()])


CHOICE_ROW = r'(?m)^\s*(?:[│┃]\s*)?[❯›>↑↓]?\s*{}\.\s+\S'


def choose(connection, agent, number):
    """Picks numbered choice [number] in the menu the agent is waiting on, with that one key.

    Claude Code's menus take the number key alone; Enter after it would carry on to whatever comes
    next. The key is only sent while that choice is on its screen, so it never lands as typing.
    """
    if not isinstance(number, int) or isinstance(number, bool) or not 1 <= number <= 9:
        raise BridgeError("Choose an option from 1 to 9.")
    current = verify_agent(connection, agent)
    if not re.search(CHOICE_ROW.format(number), capture(connection, current["pane"], 0)):
        raise BridgeError("That choice is no longer on its screen.")
    run(connection, ["send-keys", "-t", current["pane"], str(number)])


def interrupt(connection, agent):
    current = verify_agent(connection, agent)
    state, _ = classify(current, capture(connection, current["pane"], 0))
    if state != "running":
        raise BridgeError("The agent is no longer working; no interrupt was sent.")
    run(connection, ["send-keys", "-t", current["pane"], "C-c"])


def launch(connection, cwd, prompt, runtime="codex", codex_model="", resume_id="", image_paths=None):
    if runtime not in ("codex", "claude"):
        raise BridgeError("Choose Codex or Claude.")
    if not isinstance(cwd, str) or not cwd.startswith("/") or any(c in cwd for c in "\n\r\x00"):
        raise BridgeError("Use an absolute project directory on the selected host.")
    if not isinstance(prompt, str) or not prompt.strip() or len(prompt) > 12000:
        raise BridgeError("Give the new agent a task.")
    command = [runtime]
    if resume_id:
        if not re.fullmatch(r"[0-9a-fA-F-]{36}", resume_id):
            raise BridgeError("Invalid branch session ID")
        command += ["resume", resume_id]
    if runtime == "codex" and codex_model:
        command += ["--model", codex_model]
    if image_paths:
        if runtime != 'codex' or not isinstance(image_paths, list) or len(image_paths) > 4 or any(
                not isinstance(path, str) or not path.startswith('/') or any(c in path for c in '\n\r\x00') for path in image_paths):
            raise BridgeError('Invalid image attachments for this branch.')
        for path in image_paths:
            command += ['--image', path]
    command += ["--", prompt]
    name = "hub-" + uuid.uuid4().hex[:8]
    result = run(connection, ["new-session", "-d", "-s", name, "-n", cwd.rstrip("/").split("/")[-1],
                              "-c", cwd, "-P", "-F", "#{pane_id}", shlex.join(command)])
    pane = result.strip()
    run(connection, ["set-option", "-p", "-t", pane, "@pane-role", "agent", ";",
                     "set-option", "-w", "-t", pane, "@agent-pane", pane, ";",
                     "set-option", "-w", "-t", pane, "@agent-state", "running", ";",
                     "set-option", "-w", "-t", pane, "@agent-summary", prompt[:180]])
    return {"pane": pane, "session": name}
