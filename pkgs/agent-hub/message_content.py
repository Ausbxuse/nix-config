"""Separate known Codex context envelopes from user-authored display text.

Only complete, leading protocol blocks are consumed. Prose, quotations, code,
unknown tags, and incomplete examples remain literal. Native logs stay intact.
"""
import re


CONTEXT_OPEN = re.compile(r'<(codex_internal_context|environment_context|permissions instructions|collaboration_mode|turn_aborted|local-command-caveat|system-reminder)(?:\s+[\w:-]+=(?:"[^"]*"|\x27[^\x27]*\x27))*\s*>')
AGENTS_HEADER = re.compile(r'# AGENTS\.md instructions for [^\r\n]+\r?\n\s*<INSTRUCTIONS>')


def user_text(text):
    while True:
        # Four-space indentation is Markdown code, not a protocol envelope.
        candidate = re.sub(r'^(?:[ \t]*\r?\n)+', '', text)
        header = AGENTS_HEADER.match(candidate)
        opening = CONTEXT_OPEN.match(candidate) if not header else None
        if not header and not opening:
            return text
        close = '</INSTRUCTIONS>' if header else '</' + opening[1] + '>'
        end = candidate.find(close, (header or opening).end())
        if end < 0:
            return text
        text = candidate[end + len(close):]
        text = re.sub(r'^(?:[ \t]*\r?\n)+', '', text)
        if not text.strip():
            return ''


def visible(message):
    return not any(message.get(key) in ('analysis', 'reasoning', 'thinking', 'reasoning_summary')
                   for key in ('phase', 'channel', 'type')) and (
        message.get('role') != 'user' or bool(user_text(message.get('text', ''))))
