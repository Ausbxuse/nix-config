"""Bounded, versioned JSON updates. Missing bases always receive a full snapshot."""
from collections import OrderedDict
import copy
import hashlib
import json
import threading
import time


def encoded(value):
    return json.dumps(value, ensure_ascii=False, separators=(',', ':')).encode()


def apply_update(base, packet):
    """Apply the same bounded wire operations as the browser, including UTF-16 offsets."""
    if not isinstance(packet, dict) or not isinstance(packet.get('version'), str):
        raise ValueError('Invalid update packet')
    if 'value' in packet:
        return copy.deepcopy(packet['value'])
    if not base or packet.get('base') != base.get('version'):
        raise ValueError('Update base expired')
    operations = packet.get('changes')
    if not isinstance(operations, list) or len(operations) > 10000:
        raise ValueError('Invalid update operations')
    value = copy.deepcopy(base['value'])
    try:
        for operation in operations:
            kind, path, *args = operation
            if not isinstance(path, list) or len(path) > 64 or any(type(key) not in (str, int) or key in ('__proto__', 'constructor', 'prototype') for key in path):
                raise ValueError('Invalid update path')
            if not path and kind == 'set' and len(args) == 1:
                value = copy.deepcopy(args[0])
                continue
            parent = value
            for key in path[:-1]:
                parent = parent[key]
            key = path[-1] if path else None
            target = parent[key] if path and (isinstance(parent, list) or key in parent) else value if not path else None
            if kind == 'set' and path and len(args) == 1:
                parent[key] = copy.deepcopy(args[0])
            elif kind == 'delete' and path and not args:
                del parent[key]
            elif kind in ('splice', 'text') and len(args) == 3 and all(type(number) is int and number >= 0 for number in args[:2]):
                start, length, insert = args
                if kind == 'splice' and isinstance(target, list) and isinstance(insert, list) and start <= len(target) and start + length <= len(target):
                    target[start:start + length] = copy.deepcopy(insert)
                elif kind == 'text' and path and isinstance(target, str) and isinstance(insert, str):
                    raw = target.encode('utf-16-le')
                    if (start + length) * 2 > len(raw):
                        raise ValueError('Invalid text range')
                    parent[key] = (raw[:start*2] + insert.encode('utf-16-le') + raw[(start+length)*2:]).decode('utf-16-le')
                else:
                    raise ValueError('Invalid update target')
            else:
                raise ValueError('Unknown update operation')
    except (IndexError, KeyError, TypeError, UnicodeError) as exc:
        raise ValueError('Invalid update operation') from exc
    if len(encoded(value)) > 8 * 1024 * 1024:
        raise ValueError('Update result too large')
    return value


class UpdateBases:
    def __init__(self, limit=32, byte_limit=8*1024*1024):
        self.limit, self.byte_limit = limit, byte_limit
        self.entries, self.lock = OrderedDict(), threading.Lock()

    def get(self, key):
        with self.lock:
            entry = self.entries.get(key)
            if entry:
                self.entries.move_to_end(key)
            return entry[0] if entry else None

    def put(self, key, packet, value):
        size = len(encoded(value))
        with self.lock:
            self.entries.pop(key, None)
            if size <= self.byte_limit:
                self.entries[key] = ({'version': packet['version'], 'value': copy.deepcopy(value)}, size)
            while len(self.entries) > self.limit or sum(entry[1] for entry in self.entries.values()) > self.byte_limit:
                self.entries.popitem(last=False)


def changes(before, after, path=()):
    if before == after:
        return []
    if isinstance(before, dict) and isinstance(after, dict):
        result = [['delete', list(path + (key,))] for key in before.keys() - after.keys()]
        for key, value in after.items():
            result += changes(before[key], value, path + (key,)) if key in before else [['set', list(path + (key,)), value]]
        return result
    if isinstance(before, list) and isinstance(after, list):
        # Stable message/agent identities let existing entries change without
        # downloading the rest of the history. A reordered middle is replaced.
        def ids(items):
            return [json.dumps(item['id']) for item in items] if all(isinstance(item, dict) and 'id' in item for item in items) else None
        old, new = ids(before), ids(after)
        if old is not None and new is not None and len(set(old)) == len(old) and len(set(new)) == len(new):
            start = 0
            while start < min(len(old), len(new)) and old[start] == new[start]:
                start += 1
            end = 0
            while end < min(len(old), len(new)) - start and old[-end-1] == new[-end-1]:
                end += 1
            result = []
            if start + end < max(len(old), len(new)):
                result.append(['splice', list(path), start, len(old) - start - end, after[start:len(new)-end]])
            for index in list(range(start)) + list(range(len(new)-end, len(new))):
                old_index = index if index < start else len(old) - (len(new) - index)
                result += changes(before[old_index], after[index], path + (index,))
            return result
    if isinstance(before, str) and isinstance(after, str) and max(len(before), len(after)) > 256:
        start, end = 0, 0
        while start < min(len(before), len(after)) and before[start] == after[start]:
            start += 1
        while end < min(len(before), len(after)) - start and before[-end-1] == after[-end-1]:
            end += 1
        # Browser string offsets are UTF-16 units, including emoji pairs.
        units = lambda text: len(text.encode('utf-16-le')) // 2
        return [['text', list(path), units(before[:start]), units(before[start:len(before)-end]), after[start:len(after)-end]]]
    return [['set', list(path), after]]


class Updates:
    def __init__(self, limit=48, byte_limit=8*1024*1024, ttl=90, clock=time.monotonic):
        self.limit, self.byte_limit, self.ttl, self.clock = limit, byte_limit, ttl, clock
        self.entries, self.lock = OrderedDict(), threading.Lock()

    def response(self, scope, value, version=''):
        raw, now = encoded(value), self.clock()
        current = hashlib.sha256(scope.encode() + b'\0' + raw).hexdigest()[:24]
        with self.lock:
            for key, entry in list(self.entries.items()):
                if now - entry[1] > self.ttl:
                    del self.entries[key]
            base = self.entries.get(version)
            result = {'version': current, 'value': value}
            if base and base[0] == scope:
                delta = {'version': current, 'base': version, 'changes': changes(base[2], value)}
                if len(encoded(delta)) < len(raw):
                    result = delta
            if len(raw) <= self.byte_limit:
                self.entries[current] = (scope, now, copy.deepcopy(value), len(raw))
                self.entries.move_to_end(current)
                while len(self.entries) > self.limit or sum(entry[3] for entry in self.entries.values()) > self.byte_limit:
                    self.entries.popitem(last=False)
            return result
