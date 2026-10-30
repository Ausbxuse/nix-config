"""An agent's terminal screen, read for people: what it asks, and the conversation when no log can.

The phone app shows these as they come: a question as a card of choices to tap, and a
conversation read off the screen for an agent whose log the hub can't find. Reading them here, in
one place, keeps the app and the hub from disagreeing about the same screen.
"""
import re

ANSI = re.compile(r'\x1b\](?:[^\x07\x1b]|\x1b(?!\\))*(?:\x07|\x1b\\)|\x1b\[[0-?]*[ -/]*[@-~]')
TOOL = re.compile(r'^(?:Ran |Failed \(exit |Exited \(status \d+\) |Explored\b|Edited |Called |Searched |Read \d+ (?:files?|lines?)\b|Wrote |'
                  r'Updated (?:plan|file)|Bash\(|Read\(|Write\(|Edit\(|Update\(|Grep\(|Glob\(|Web(?:Search|Fetch)\(|Task\(|Agent\(|Tool\b|'
                  r'Waited\b|Searching\b)')
CHROME = re.compile(
    r'^(?:[›❯]\s*(?:Ask Codex to do anything)?\s*|(?:GPT-|gpt-).+ · .+(?:left|context).*|.*← for agents.*|.*\? for shortcuts.*|'
    r'new task\? /clear.*|-- INSERT --|\+ Show details|.*· \d+h .+/rc\s*|.*auto mode on.*|.*accept edits on.*|.*bypass permissions on.*|'
    r'.*plan mode on.*|.*shift\s*\+\s*tab to cycle.*|(?:↳\s*)?shift\s*\+\s*\S*\s*(?:to\s+)?edit last queued message.*|'
    r'\(disable recaps in /config\)|⚠.*limit.*|[─━]{8,})$', re.I)
ACTIVITY = re.compile(r'^[•●✻✽✶✢✳·*]\s+[^()\n]+\((?:\d+[hms]\s*)+[•·][^)\n]*(?:esc to interrupt|tokens\b)[^)\n]*\).*$|'
                      r'^[•●✻✽✶✢✳·*]\s+(?:Auto[- ]?)?Compacting.*$', re.I)
COMPLETION = re.compile(r'^(?:Worked for |[✻✽✶✢✳]\s+\S+\s+for )\d+(?:\.\d+)?(?:ms|[hms])\b.*$', re.I)
MARKER = re.compile(r'^[•●]\s(.+)')
PROMPT = re.compile(r'^[›❯]\s(.+)')
EMPTY_PROMPT = re.compile(r'^[›❯]\s*$')
SHELL_PATH = re.compile(r'^\S?\s*(?:~|/)[\w./~-]*(?:\s+on\s+.*)?$')
BOX = re.compile(r'^\s*[│┃┌┏╭╰└├┤┬┴┼─━╮╯]')
OPTION = re.compile(r'^[›❯>↑↓]?\s*(\d+)\.\s+(.+)$')
HINT = re.compile(r'(?:enter to (?:select|confirm|set)|esc to (?:cancel|interrupt)|q to cancel|tab/arrow keys|↑/↓|to navigate|to adjust)', re.I)
RULE = re.compile(r'^[─━╌╍═▔▁-]{4,}$')
PLAIN = re.compile(
    r'^(?:direnv:.*|[›❯>]\s*(?:Ask Codex to do anything|codex|claude)?\s*|>_ OpenAI Codex.*|(?:GPT-|gpt-).+ · .+(?:left|context).*|'
    r'.*· weekly \d+% left.*|← for agents.*|.*\? for shortcuts|~/\S*|[─━╭╮╰╯│ ]+)$')


def _lines(raw):
    text = re.sub(r'[\x00-\x08\x0b-\x1f\x7f]', '', re.sub(r'\r\n?', '\n', ANSI.sub('', raw)))
    return [line.rstrip() for line in text.split('\n')]


def _tidy(lines):
    """A block's lines as Markdown: drawn tables and boxes become code, so they keep their columns."""
    out, in_box = [], False
    for line in lines:
        box = bool(BOX.match(line))
        if box != in_box:
            out.append('```')
            in_box = box
        if in_box:
            out.append(line)
        else:
            stripped = line.lstrip('⎿ ')
            out.append('    ' + stripped if line.lstrip().startswith('⎿') else line)
    if in_box:
        out.append('```')
    return '\n'.join(out).strip('\n')


def conversation(raw):
    """Your messages (›, ❯), its replies and tool steps (•, ●), without the status bars, spinners,
    input box and shell around them: [{'role': 'user'|'assistant'|'tool', 'text': ...}]."""
    lines = _lines(raw)
    # The input box at the bottom (and everything under it) isn't conversation.
    end = len(lines)
    for i in range(len(lines) - 1, -1, -1):
        if PROMPT.search(lines[i]) or EMPTY_PROMPT.match(lines[i]):
            if not any(MARKER.search(line) for line in lines[i + 1:]):
                end = i
            break
    out, current, fence, prev, recap = [], None, False, '', False
    for line in lines[:end]:
        t = line.strip()
        # Claude Code's "※ recap: …" (a summary of the session so far) isn't something it said.
        if t.startswith('※') or re.search(r'^↳\s+Recap:', t, re.I):
            recap = True
            continue
        if recap:
            if MARKER.search(line) or PROMPT.search(line):
                recap = False
            else:
                continue
        if t.startswith('```') or t.startswith('~~~'):
            fence = not fence
        if fence or t.startswith('```') or t.startswith('~~~'):
            if current is not None:
                current[1].append(line[2:] if line.startswith('  ') else line)
            continue
        if ACTIVITY.match(t) or COMPLETION.match(t) or (t and CHROME.match(t)):
            if t:
                prev = t
            continue
        marker, prompt = MARKER.search(line), PROMPT.search(line)
        if marker:
            text = marker.group(1)
            current = ['tool' if TOOL.search(text) else 'assistant', [text]]
            out.append(current)
        elif prompt:
            # Your message, unless it's a shell prompt (after a path line) or starting the agent.
            text = prompt.group(1).strip()
            starting = text in ('codex', 'claude') or text.startswith(('codex ', 'claude '))
            current = None if SHELL_PATH.match(prev) or starting else ['user', [text]]
            if current:
                out.append(current)
        elif current is not None:
            # Before the first message: the banner and the shell. After: more of the same block.
            current[1].append(line[2:] if line.startswith('  ') else line)
        if t:
            prev = t
    blocks = [{'role': role, 'text': _tidy(ls)} for role, ls in out]
    return [b for b in blocks if b['text'].strip()]


def question(raw):
    """The question an agent is waiting on (Claude Code's menus and permission boxes): what it
    asks and its numbered choices, without box drawing, hint lines, and the input box under it
    (with anything typed there). None when there are no numbered choices."""
    lines = []
    for line in re.sub(r'\r\n?', '\n', ANSI.sub('', raw)).split('\n'):
        line = line.rstrip()
        if line.endswith('│'):
            line = line[:-1].rstrip()
        if line.lstrip().startswith('│'):
            line = line.lstrip()[1:]
        lines.append(line.rstrip())
    # The input box at the bottom: the last prompt row that isn't a choice, and below it.
    end = len(lines)
    for i in range(len(lines) - 1, -1, -1):
        t = lines[i].strip()
        if OPTION.match(t):
            break
        if re.match(r'^[›❯](?:\s|$)', t):
            end = i
            break
    body = lines[:end]
    first = next((i for i, line in enumerate(body) if OPTION.match(line.strip())), None)
    if first is None:
        return None
    options, column = [], 0
    for line in body[first:]:
        t = line.strip()
        m = OPTION.match(t)
        if m:
            # "Default (recommended) ✔   Sonnet 5 · Efficient…": a choice and, after a wide gap,
            # what it means.
            parts = re.split(r'\s{2,}', m.group(2).strip(), maxsplit=1)
            options.append({'number': int(m.group(1)), 'label': parts[0], 'detail': parts[1] if len(parts) > 1 else ''})
            # Where its label starts: a description under it is indented at least that far.
            column = len(line) - len(line.lstrip()) + (line.lstrip().find(m.group(2)) if m.group(2) in line.lstrip() else 0)
        elif not t or RULE.match(t) or BOX.match(line):
            continue
        elif HINT.search(t) or MARKER.match(t) or len(line) - len(line.lstrip()) < column:
            # Past the choices: the menu's own notes and keys.
            if options:
                break
        elif options:
            options[-1]['detail'] = (options[-1]['detail'] + ' ' + t).strip()
    # What it asks: the few lines just above the choices (after its last reply), without tabs,
    # rules and hints.
    # (after its last reply or the line drawn above a menu, whichever is lower).
    start = max((i for i in range(first) if MARKER.search(body[i].strip()) or re.match(r'^[─━═▔▁╭╰┌└]{8,}', body[i].strip())), default=-1) + 1
    prompt = [t for t in (line.strip() for line in body[start:first])
              if t and not re.search(r'^[─━╌╍═▔▁╭╮╰╯┌┐└┘]{2,}', t) and not HINT.search(t)
              and '☐' not in t and '☒' not in t and '✔' not in t and not t.startswith('←')]
    seen, unique = set(), []
    for o in options:
        if o['number'] not in seen:
            seen.add(o['number'])
            unique.append(o)
    # Lines the terminal wrapped (they run nearly its width) join back up.
    joined = []
    for t in prompt[-4:]:
        if joined and len(joined[-1]) >= 60:
            joined[-1] += ' ' + t
        else:
            joined.append(t)
    return {'prompt': '\n'.join(joined), 'options': unique}


def plain(screen):
    """The screen with its decoration (prompts, status bars, shell set-up) taken out; '' when
    nothing is left to read."""
    return '\n'.join(line for line in (l.rstrip() for l in screen.split('\n')) if not PLAIN.match(line.strip())).strip('\n')


def read(raw, with_conversation=False):
    """What the app shows from a screen: its question, and (when the log can't be read) the
    conversation on it."""
    tail = '\n'.join(raw.split('\n')[-80:])
    value = {'question': question(tail), 'plain': plain(tail)}
    if with_conversation:
        value['conversation'] = conversation(raw)
    return value
