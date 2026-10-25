"""Shared reads and a resumable, bounded stream of public hub observations."""
from collections import Counter, OrderedDict, deque
from concurrent.futures import Future, ThreadPoolExecutor
from contextlib import contextmanager
import copy
import hashlib
import json
import secrets
import threading
import time

from updates import Updates, encoded


class SharedReads:
    def __init__(self, ttl=.2, limit=32, byte_limit=8*1024*1024):
        self.ttl, self.limit, self.byte_limit = ttl, limit, byte_limit
        self.lock = threading.Lock()
        self.cache, self.pending = OrderedDict(), {}
        self.generation = 0
        self.loads, self.hits = 0, 0

    def invalidate(self):
        with self.lock:
            self.generation += 1
            self.cache.clear()
            self.pending.clear()

    def get(self, key, loader):
        with self.lock:
            now, generation = time.monotonic(), self.generation
            entry = self.cache.get(key)
            if entry and now - entry[0] < self.ttl:
                self.hits += 1
                self.cache.move_to_end(key)
                return copy.deepcopy(entry[1])
            pending = self.pending.get(key)
            owner = pending is None
            if owner:
                pending = self.pending[key] = Future()
                self.loads += 1
            else:
                self.hits += 1
        if owner:
            try:
                value = loader()
                size = len(encoded(value))
                with self.lock:
                    if self.generation == generation and size <= self.byte_limit:
                        self.cache[key] = (time.monotonic(), copy.deepcopy(value), size)
                        self.cache.move_to_end(key)
                        while len(self.cache) > self.limit or sum(entry[2] for entry in self.cache.values()) > self.byte_limit:
                            self.cache.popitem(last=False)
                pending.set_result(value)
            except BaseException as exc:
                pending.set_exception(exc)
            finally:
                with self.lock:
                    if self.pending.get(key) is pending:
                        del self.pending[key]
        return copy.deepcopy(pending.result())


def meaningful(value):
    """Observation timestamps are refreshed periodically without a frame every tick."""
    if isinstance(value, dict):
        return {key: meaningful(item) for key, item in value.items() if key not in ('server_time', 'last_seen', 'observed_at')}
    if isinstance(value, list):
        return [meaningful(item) for item in value]
    return value


class LiveFeed:
    def __init__(self, snapshot, preview, stop, heartbeat=5, replay_bytes=4*1024*1024):
        self.snapshot, self.preview, self.stop = snapshot, preview, stop
        self.heartbeat, self.replay_bytes = heartbeat, replay_bytes
        self.condition, self.dirty = threading.Condition(), threading.Event()
        self.refs, self.latest, self.pending = Counter(), {}, {}
        self.events, self.bytes, self.sequence = deque(), 0, 0
        self.epoch, self.thread = secrets.token_hex(8), None
        self.updates = Updates(limit=96, byte_limit=16*1024*1024)

    def wake(self):
        self.dirty.set()

    def close(self):
        self.wake()
        with self.condition:
            self.condition.notify_all()

    def refresh(self, topic):
        try:
            value = self.snapshot() if topic == ('state',) else self.preview(*topic[1:])
            self.publish(topic, value)
        except Exception as exc:
            self.publish(topic, {'error': str(exc)[:300]}, error=True)

    def publish(self, topic, value, error=False):
        now = time.monotonic()
        fingerprint = hashlib.sha256(encoded(meaningful(value))).digest()
        with self.condition:
            old = self.latest.get(topic)
            if old and old['fingerprint'] == fingerprint and old['error'] == error and now - old['at'] < self.heartbeat:
                return
            scope = json.dumps(topic)
            packet = self.updates.response(scope, value, old['version'] if old and old['error'] == error else '')
            frame = {'topic': list(topic), 'packet': packet, 'error': error}
            self.latest[topic] = dict(value=copy.deepcopy(value), version=packet['version'], fingerprint=fingerprint, at=now, error=error)
            self.sequence += 1
            event = {'id': self.epoch + ':' + str(self.sequence), 'event': 'update', 'data': frame}
            size = len(encoded(event))
            self.events.append((self.sequence, topic, event, size))
            self.bytes += size
            while len(self.events) > 256 or self.bytes > self.replay_bytes:
                self.bytes -= self.events.popleft()[3]
            self.condition.notify_all()

    def run(self):
        with ThreadPoolExecutor(max_workers=4, thread_name_prefix='hub-live') as pool:
            while not self.stop.is_set():
                self.dirty.wait(1)
                self.dirty.clear()
                with self.condition:
                    for topic in list(self.latest):
                        if not self.refs[topic]:
                            self.latest.pop(topic, None)
                    for topic in list(self.refs):
                        if self.refs[topic] and (topic not in self.pending or self.pending[topic].done()):
                            self.pending[topic] = pool.submit(self.refresh, topic)
                        elif not self.refs[topic] and (topic not in self.pending or self.pending[topic].done()):
                            self.pending.pop(topic, None)
                            del self.refs[topic]

    @contextmanager
    def subscribe(self, agent_id='', history=True, previews=None):
        previews = previews if previews is not None else ([(agent_id, history)] if agent_id else [])
        topics = {('state',)} | {('preview', item[0], bool(item[1])) + ((True,) if len(item) > 2 and item[2] else ()) for item in previews}
        with self.condition:
            self.refs.update(topics)
            if self.thread is None:
                self.thread = threading.Thread(target=self.run, daemon=True, name='hub-live')
                self.thread.start()
        self.wake()
        try:
            yield topics
        finally:
            with self.condition:
                self.refs.subtract(topics)
            self.wake()

    def stream(self, topics, cursor=''):
        try:
            epoch, number = cursor.split(':')
            sequence = int(number) if epoch == self.epoch else -1
        except (ValueError, AttributeError):
            sequence = -1
        while not self.stop.is_set():
            with self.condition:
                self.condition.wait_for(lambda: self.stop.is_set() or (
                    bool(self.latest) if sequence < 0 else self.sequence != sequence), timeout=10)
                if self.stop.is_set():
                    return
                oldest = self.events[0][0] if self.events else self.sequence + 1
                reset = sequence < 0 or sequence > self.sequence or sequence < oldest - 1
                if reset:
                    frames = [{'topic': list(topic), 'packet': {'version': item['version'], 'value': copy.deepcopy(item['value'])}, 'error': item['error']}
                              for topic in topics if (item := self.latest.get(topic))]
                    if frames:
                        result = [{'id': self.epoch + ':' + str(self.sequence), 'event': 'reset', 'data': frames}]
                    else:
                        result = []
                else:
                    result = [event for seq, topic, event, _ in self.events if seq > sequence and topic in topics]
                sequence = self.sequence
            if result:
                yield from result
            else:
                yield {'event': 'ping', 'data': {}}
