"""Small source excerpts for handoffs. No generated memory or inferred tasks."""
from collections import OrderedDict
import copy
import hashlib
import json
import re
import threading
import time
from message_content import visible as display_visible


def digest(text):
    return hashlib.sha256(text.encode()).hexdigest()[:24]


def visible(message):
    return (isinstance(message, dict) and message.get('role') in ('user', 'assistant') and isinstance(message.get('text'), str)
            and display_visible(message))


def source_version(messages):
    source = [{key: m.get(key) for key in ('id', 'role', 'text', 'created', 'phase', 'kind')} for m in messages
              if visible(m) or (isinstance(m, dict) and m.get('role') == 'tool')]
    return digest(json.dumps(source, ensure_ascii=False, sort_keys=True))


def passages(text):
    """Keep source offsets and complete lines; omit code, tables and quotations."""
    fence = None
    for match in re.finditer(r'[^\n]+', text):
        line = match[0].strip()
        delimiter = re.match(r'(`{3,}|~{3,})', line)
        if delimiter:
            fence = None if fence and delimiter[1][0] == fence[0] and len(delimiter[1]) >= len(fence) else delimiter[1] if not fence else fence
            continue
        if fence or not line or line.startswith(('>', '|', '<', '$$', '\\[', '\\]', '#')) or re.fullmatch(r'[-*_\s]+', line):
            continue
        start = match.start() + len(match[0]) - len(match[0].lstrip())
        # A leading list marker has no bearing on the quoted evidence.
        prefix = re.match(r'(?:[-*+] |\d+[.)] )', line)
        if prefix:
            start += prefix.end()
        quote = text[start:match.end()].rstrip()
        label = re.match(r'(?:\*\*)?(?:Result|Decision|Question|Summary|Next step)(?:\*\*)?:(?:\*\*)?\s+', quote, re.I)
        if label:
            start += label.end()
            quote = text[start:match.end()].rstrip()
        if len(quote) >= 12:
            yield start, quote


def excerpt(message, kind, start, text, limit=240):
    end = len(text)
    if end > limit:
        # Prefer whole sentences; never splice separate claims together.
        sentences = list(re.finditer(r'[.!?](?=\s|$)', text[:limit + 1]))
        end = sentences[-1].end() if sentences and sentences[-1].end() >= 40 else text.rfind(' ', 0, limit)
        if end < 1:
            return None  # An unbroken path/token is not a useful brief.
    quote = text[:end]
    return dict(kind=kind, text=quote, truncated=end < len(text), source={
        'message_id': message['id'], 'role': message['role'], 'digest': digest(message['text']),
        'start': start, 'end': start + end, 'created': message.get('created', '')})


def extract(messages):
    last_role = next((m['role'] for m in reversed(messages) if visible(m) or (isinstance(m, dict) and m.get('role') == 'tool')), '')
    messages = [m for m in messages if visible(m) and isinstance(m.get('id'), str)]
    if not messages:
        return []
    user = next((i for i in range(len(messages) - 1, -1, -1) if messages[i]['role'] == 'user'), -1)
    fields = []
    if user >= 0:
        # Short follow-ups such as "continue" stay literal; do not invent their
        # meaning by turning an older assignment into a current instruction.
        text = messages[user]['text'].strip()
        start = messages[user]['text'].find(text)
        fields.append(excerpt(messages[user], 'request', start, text))
        first = next(m for m in messages if m['role'] == 'user')
        if first['id'] != messages[user]['id'] and len(first['text'].strip()) >= 24:
            fields.append(excerpt(first, 'earlier_request', len(first['text']) - len(first['text'].lstrip()), first['text'].strip()))
    replies = [m for m in messages[user + 1:] if m['role'] == 'assistant']
    if not replies or last_role != 'assistant':
        return [field for field in fields if field]
    reply = replies[-1]
    parts = list(passages(reply['text']))
    questions = [(start, text) for start, text in parts if re.search(
        r'^(?:\*\*)?(?:(?:Question|Decision|Need(?:ed)?|Next step):\s*)?(?:Which\b|Would you\b|Do you\b|Can you\b|Could you\b|Should (?:I|we)\b|Please (?:confirm|choose|approve|provide)\b|I need (?:you|your)\b)', text, re.I)]
    decisions = [(start, text) for start, text in questions if '?' in text or re.search(r'\b(?:confirm|choose|approve|provide|need)\b', text, re.I)]
    changes = [(start, text) for start, text in parts if (start, text) not in questions and
               not re.match(r"(?:I(?:'ll| will)|Next[, :]|Let me|I'll|Going to|To do[: ])", text, re.I)]
    if changes:
        scored = [(2 if re.match(r'(?:Correction|Update|Actually|However|My earlier|The earlier|I was wrong)\b', text, re.I) else
                   int(bool(re.search(r'\b(?:passed|failed|saved|added|removed|fixed|updated|measured|trained|evaluated|completed|finished|deployed|changed|increased|decreased|reduced|restored|implemented|ready)\b', text, re.I))), -index, start, text)
                  for index, (start, text) in enumerate(changes)]
        _, _, start, text = max(scored)
        fields.append(excerpt(reply, 'change', start, text))
    if decisions:
        start, text = decisions[-1]
        fields.append(excerpt(reply, 'decision', start, text))
    return [field for field in fields if field]


def valid_fields(value, messages):
    """Reject missing, edited or mismatched sources before using cached facts."""
    if not isinstance(value, dict):
        return []
    sources = {m.get('id'): m for m in messages if visible(m)}
    fields = value.get('fields', []) if isinstance(value, dict) else []
    if not isinstance(fields, list) or len(fields) > 4 or value.get('source_version') != source_version(messages):
        return []
    for field in fields:
        if not isinstance(field, dict) or field.get('kind') not in ('request', 'change', 'decision', 'earlier_request') or not isinstance(field.get('source'), dict):
            return []
        source = field.get('source', {})
        message = sources.get(source.get('message_id'))
        start, end = source.get('start'), source.get('end')
        if (not message or source.get('role') != message['role'] or source.get('digest') != digest(message['text']) or
                type(start) is not int or type(end) is not int or not 0 <= start < end <= len(message['text']) or
                field.get('text') != message['text'][start:end]):
            return []
    return fields


class TaskFacts:
    def __init__(self, limit=64, byte_limit=256*1024, ttl=60, clock=time.monotonic):
        self.limit, self.byte_limit, self.ttl, self.clock = limit, byte_limit, ttl, clock
        self.cache, self.lock = OrderedDict(), threading.RLock()
        self.hits = self.builds = 0

    def build(self, agent, preview):
        messages = preview.get('messages', [])
        identity = agent.get('id', '')
        if preview.get('source') != 'original' or not messages:
            with self.lock:
                self.cache.pop(identity, None)
            return {}
        source = source_version(messages)
        version = digest(json.dumps([identity, agent.get('cwd'), source], ensure_ascii=False, sort_keys=True))
        now = self.clock()
        with self.lock:
            for key, entry in list(self.cache.items()):
                if now - entry['at'] > self.ttl:
                    del self.cache[key]
            cached = self.cache.get(identity)
            if cached and cached['value']['version'] == version:
                self.cache.move_to_end(identity)
                self.hits += 1
                return copy.deepcopy(cached['value'])
        value = {'version': version, 'source_version': source, 'agent_id': identity, 'fields': extract(messages),
                 'scope': 'Excerpts from matched messages. Agent statements are reports, not independently verified results.'}
        with self.lock:
            self.builds += 1
            self.cache.pop(identity, None)
            size = len(json.dumps(value).encode())
            if size <= self.byte_limit:
                self.cache[identity] = {'at': now, 'value': value, 'size': size}
            while len(self.cache) > self.limit or sum(entry['size'] for entry in self.cache.values()) > self.byte_limit:
                self.cache.popitem(last=False)
        return copy.deepcopy(value)
