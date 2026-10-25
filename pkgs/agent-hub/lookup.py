"""Small, disposable lookup caches. Source histories remain the authority."""
from collections import OrderedDict
import json
import threading
import time


class LookupCache:
    def __init__(self, clock=time.monotonic, source_ttl=30, answer_ttl=15, max_bytes=8_000_000, max_entries=64):
        self.clock, self.source_ttl, self.answer_ttl = clock, source_ttl, answer_ttl
        self.max_bytes, self.max_entries = max_bytes, max_entries
        self.sources, self.answers = OrderedDict(), OrderedDict()
        self.lock = threading.RLock()
        self.bytes = self.hits = self.reads = self.transferred = 0

    @staticmethod
    def version(agent):
        # Never reuse evidence whose observation identity cannot be checked.
        if not agent.get('connection_online') or not agent.get('activity_fingerprint'):
            return None
        return tuple(agent.get(k, '') for k in ('id', 'connection_id', 'connection_label', 'title', 'display_title',
                     'cwd', 'runtime', 'state', 'live', 'activity_fingerprint'))

    def prune(self, agents):
        current = {a['id']: self.version(a) for a in agents}
        now = self.clock()
        with self.lock:
            for key, (version, expires, size, _) in list(self.sources.items()):
                if expires <= now or current.get(key) != version:
                    self.bytes -= size
                    del self.sources[key]
            for key, (expires, _) in list(self.answers.items()):
                if expires <= now or key[1] != tuple(sorted(v for v in current.values() if v)):
                    del self.answers[key]

    def preview(self, agent, read):
        version, now = self.version(agent), self.clock()
        with self.lock:
            entry = self.sources.get(agent['id'])
            if version and entry and entry[0] == version and entry[1] > now:
                self.hits += 1
                self.sources.move_to_end(agent['id'])
                return entry[3]
            self.reads += 1
        value = read(agent['id'], history=True)
        size = len(json.dumps(value, ensure_ascii=False).encode())
        observation = value.get('observation', {})
        verified = (version and observation.get('activity_fingerprint') == agent['activity_fingerprint']
                    and observation.get('state') == agent['state'])
        with self.lock:
            self.transferred += size
            old = self.sources.pop(agent['id'], None)
            if old:
                self.bytes -= old[2]
            if verified and size <= self.max_bytes:
                self.sources[agent['id']] = (version, now + self.source_ttl, size, value)
                self.bytes += size
                while len(self.sources) > self.max_entries or self.bytes > self.max_bytes:
                    _, entry = self.sources.popitem(last=False)
                    self.bytes -= entry[2]
        return value

    def answer_key(self, query, agents):
        versions = [self.version(agent) for agent in agents]
        if not versions or any(v is None for v in versions):
            return None
        # Exact requests only: changing negation or task wording must re-evaluate.
        return query.strip(), tuple(sorted(versions))

    def answer(self, key):
        with self.lock:
            entry = self.answers.get(key)
            if entry and entry[0] > self.clock():
                self.answers.move_to_end(key)
                return entry[1]
        return None

    def remember_answer(self, key, reply, evidence):
        if key is None or not evidence.get('matches') or evidence.get('unavailable') or evidence.get('unsearched_agents'):
            return
        with self.lock:
            expires = self.clock() + self.answer_ttl
            for version in key[1]:
                source = self.sources.get(version[0])
                if not source or source[0] != version:
                    return
                # Reuse never extends the age of its underlying source evidence.
                expires = min(expires, source[1])
            if expires <= self.clock():
                return
            self.answers[key] = (expires, reply)
            self.answers.move_to_end(key)
            while len(self.answers) > 16:
                self.answers.popitem(last=False)

    def stats(self):
        with self.lock:
            return {'evidence_cache_hits': self.hits, 'evidence_reads': self.reads,
                    'evidence_bytes': self.transferred}


def token_usage(stdout):
    """Expose numeric usage only, never model events or private reasoning."""
    result = {}
    for line in stdout.splitlines():
        try:
            event = json.loads(line)
            if event.get('type') == 'turn.completed':
                for key in ('input_tokens', 'cached_input_tokens', 'output_tokens'):
                    value = event.get('usage', {}).get(key)
                    if type(value) is int and value >= 0:
                        result[key] = value
        except (ValueError, AttributeError):
            continue
    return result
