"""Bounded speech admission and cancellation; no audio or text is stored here."""
from collections import OrderedDict
from contextlib import contextmanager
import re
import threading
import time


class SpeechError(Exception):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


def identity(value):
    if not isinstance(value, dict) or not isinstance(value.get('client'), str) or not re.fullmatch(r'[\w-]{12,64}', value['client'], re.ASCII):
        raise SpeechError('Invalid speech client.')
    if type(value.get('sequence')) is not int or not 0 <= value['sequence'] <= 2**53 - 1:
        raise SpeechError('Invalid speech sequence.')
    return value['client'], value['sequence']


class SpeechLane:
    """One model call at a time. Final requests precede pending interim work.

    Running inference finishes so resident models stay warm. Its result is
    discarded if superseded; canceled pending jobs never reach the model.
    Semaphore methods retain nonblocking warmup and older-client behavior.
    """
    def __init__(self, kind):
        self.kind, self.condition = kind, threading.Condition()
        self.busy, self.closed = False, False
        self.clients, self.pending = OrderedDict(), []

    def _client(self, client):
        now = time.monotonic()
        active = {job['client'] for job in self.pending}
        for key, state in list(self.clients.items()):
            if now - state['seen'] > 600 and key not in active:
                del self.clients[key]
        if client not in self.clients:
            if len(self.clients) >= 64:
                raise SpeechError('Too many speech clients. Try again shortly.', 429)
            self.clients[client] = dict(through=-1, turn=-1, latest=-1, final=False, real=-1, seen=now)
        state = self.clients[client]
        state['seen'] = now
        self.clients.move_to_end(client)
        return state

    def cancel(self, value):
        client, sequence = identity(value)
        with self.condition:
            state = self._client(client)
            state['through'] = max(state['through'], sequence)
            self.condition.notify_all()
        return {'ok': True}

    def _stale(self, job):
        state = self.clients[job['client']]
        return (self.closed or job['sequence'] <= state['through'] or
                (self.kind == 'asr' and (job['turn'] < state['turn'] or
                 (job['mode'] == 'interim' and (state['final'] or job['sequence'] < state['latest'])))) or
                (self.kind == 'tts' and job['mode'] == 'preload' and job['sequence'] < state['real']))

    def acquire(self, blocking=True, timeout=None):
        with self.condition:
            ready = lambda: self.closed or (not self.busy and not self.pending)
            if not ready() and (not blocking or not self.condition.wait_for(ready, timeout)):
                return False
            if self.closed:
                return False
            self.busy = True
            return True

    def release(self):
        with self.condition:
            if not self.busy:
                raise ValueError('Speech lane is not acquired')
            self.busy = False
            self.condition.notify_all()

    def close(self):
        with self.condition:
            self.closed = True
            self.condition.notify_all()

    @contextmanager
    def request(self, work=None, ready=False):
        if work is None:
            if not ready and not self.acquire(blocking=False):
                raise SpeechError('Local voice is busy. Try again in a moment.', 429)
            try:
                yield
            finally:
                if not ready:
                    self.release()
            return
        client, sequence = identity(work)
        mode = work.get('mode')
        if mode not in (('final', 'interim') if self.kind == 'asr' else ('speech', 'preload')):
            raise SpeechError('Invalid speech work type.')
        turn = work.get('turn', 0)
        if type(turn) is not int or not 0 <= turn <= 2**53 - 1:
            raise SpeechError('Invalid speech turn.')
        job = dict(client=client, sequence=sequence, mode=mode, turn=turn, ticket=object(),
                   priority=int(mode in ('interim', 'preload')))
        acquired = False
        with self.condition:
            state = self._client(client)
            if sequence <= state['through'] or self.closed:
                raise SpeechError('Speech request was canceled.', 409)
            if self.kind == 'asr':
                if turn > state['turn']:
                    state.update(turn=turn, latest=-1, final=False)
                if turn == state['turn']:
                    state['latest'] = max(sequence, state['latest'])
                    state['final'] |= mode == 'final'
            elif mode == 'speech':
                state['real'] = max(sequence, state['real'])
            self.condition.notify_all()
            if self._stale(job):
                raise SpeechError('Speech request was superseded.', 409)
            if len(self.pending) >= 16:
                raise SpeechError('Speech requests are busy. Try again shortly.', 429)
            self.pending.append(job)
        try:
            if not ready:
                deadline = time.monotonic() + 20
                with self.condition:
                    while True:
                        if self._stale(job):
                            raise SpeechError('Speech request was superseded.', 409)
                        waiting = [item for item in self.pending if not self._stale(item)]
                        if not self.busy and min(waiting, key=lambda item: item['priority']) is job:
                            self.busy = acquired = True
                            break
                        remaining = deadline - time.monotonic()
                        if remaining <= 0:
                            raise SpeechError('Speech requests are busy. Try again shortly.', 429)
                        self.condition.wait(remaining)
            yield
            with self.condition:
                if self._stale(job):
                    raise SpeechError('Speech request was superseded.', 409)
        finally:
            with self.condition:
                self.pending.remove(job)
                if acquired:
                    self.busy = False
                self.condition.notify_all()
