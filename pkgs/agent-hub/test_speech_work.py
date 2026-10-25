from concurrent.futures import ThreadPoolExecutor
import threading
import unittest

from speech_work import SpeechError, SpeechLane


class SpeechWorkTests(unittest.TestCase):
    def setUp(self):
        self.pool = ThreadPoolExecutor(max_workers=5)
        self.addCleanup(self.pool.shutdown)
        self.lane = SpeechLane('asr')
        self.addCleanup(self.lane.close)

    def work(self, sequence, mode='interim', client='phone-client-one', turn=1):
        return dict(client=client, sequence=sequence, mode=mode, turn=turn)

    def waiting(self, count):
        with self.lane.condition:
            self.assertTrue(self.lane.condition.wait_for(lambda: len(self.lane.pending) >= count, 1))

    def submit(self, work, order):
        def run():
            try:
                with self.lane.request(work):
                    order.append(work['sequence'])
                return 200
            except SpeechError as exc:
                return exc.status
        return self.pool.submit(run)

    def test_final_supersedes_queued_captions_and_runs_before_other_interim_work(self):
        self.lane.acquire()
        order = []
        old = self.submit(self.work(1), order)
        self.waiting(1)
        other = self.submit(self.work(2, client='desktop-client-two'), order)
        self.waiting(2)
        final = self.submit(self.work(3, 'final'), order)
        self.assertEqual(old.result(1), 409)
        self.lane.release()
        self.assertEqual(final.result(1), 200)
        self.assertEqual(other.result(1), 200)
        self.assertEqual(order, [3, 2])
        with self.assertRaises(SpeechError):
            with self.lane.request(self.work(4)):
                self.fail('Late interim after final reached the model')

    def test_running_caption_finishes_but_its_stale_result_is_discarded(self):
        entered, finish = threading.Event(), threading.Event()
        self.addCleanup(finish.set)
        def caption():
            try:
                with self.lane.request(self.work(1)):
                    entered.set()
                    self.assertTrue(finish.wait(2))
                return 'stale'
            except SpeechError as exc:
                return exc.status
        running = self.pool.submit(caption)
        self.assertTrue(entered.wait(1))
        order = []
        final = self.submit(self.work(2, 'final'), order)
        self.waiting(2)
        self.assertFalse(final.done())
        finish.set()
        self.assertEqual(running.result(1), 409)
        self.assertEqual(final.result(1), 200)
        self.assertEqual(order, [2])

    def test_cancel_pending_synthesis_is_scoped_and_late_cancel_cannot_drop_next_reply(self):
        self.lane.kind = 'tts'
        self.lane.acquire()
        order = []
        canceled = self.submit(self.work(1, 'speech'), order)
        self.waiting(1)
        self.lane.cancel(self.work(1, 'speech'))
        self.assertEqual(canceled.result(1), 409)
        next_reply = self.submit(self.work(2, 'speech'), order)
        other = self.submit(self.work(1, 'speech', client='desktop-client-two'), order)
        self.waiting(2)
        self.lane.cancel(self.work(1, 'speech'))
        self.lane.release()
        self.assertEqual(next_reply.result(1), 200)
        self.assertEqual(other.result(1), 200)
        self.assertEqual(sorted(order), [1, 2])
        with self.assertRaises(SpeechError):
            with self.lane.request(self.work(1, 'speech')):
                self.fail('Canceled request arrived after cancellation')

    def test_real_reply_replaces_unused_pending_preload(self):
        self.lane.kind = 'tts'
        self.lane.acquire()
        order = []
        warm = self.submit(self.work(1, 'preload'), order)
        self.waiting(1)
        answer = self.submit(self.work(2, 'speech'), order)
        self.assertEqual(warm.result(1), 409)
        self.lane.release()
        self.assertEqual(answer.result(1), 200)
        self.assertEqual(order, [2])

    def test_ready_audio_bypasses_inference_but_not_cancellation(self):
        self.lane.kind = 'tts'
        self.lane.acquire()
        with self.lane.request(self.work(1, 'speech'), ready=True):
            pass
        self.assertTrue(self.lane.busy)
        self.lane.cancel(self.work(2, 'speech'))
        with self.assertRaises(SpeechError):
            with self.lane.request(self.work(2, 'speech'), ready=True):
                self.fail('Canceled cached audio returned')
        self.lane.release()

    def test_invalid_inputs_and_client_counts_are_bounded(self):
        for invalid in [None, {}, {'client':'short','sequence':1}, self.work(True),
                        self.work(2**53), self.work(1, 'speech'), self.work(1, turn=-1)]:
            if invalid is None:
                continue  # Legacy calls use nonblocking admission without a client.
            with self.assertRaises(SpeechError):
                with self.lane.request(invalid):
                    self.fail('Invalid metadata admitted')
        for i in range(64):
            self.lane.cancel(self.work(1, client='bounded-client-'+str(i)))
        with self.assertRaises(SpeechError) as caught:
            self.lane.cancel(self.work(1, client='one-more-client'))
        self.assertEqual(caught.exception.status, 429)
        self.assertEqual(len(self.lane.clients), 64)


if __name__ == '__main__':
    unittest.main()
