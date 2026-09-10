import importlib.util
from pathlib import Path
import tempfile
import unittest

spec = importlib.util.spec_from_file_location('prefetch', Path(__file__).parents[1] / 'scripts/installer-prefetch.py')
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


class PrefetchTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.image = Path(self.tmp.name) / 'image'
        self.image.write_bytes(b'hsqs' + bytes(2 * m.CHUNK))

    def test_explicit_limit(self):
        self.assertEqual(m.prefetch(self.image, 12345, lambda: 2**30), 12345)

    def test_half_available_memory_budget(self):
        # Use small thresholds to exercise the same limit with a small fixture.
        old = m.MIN_AVAILABLE
        m.MIN_AVAILABLE = 4
        self.addCleanup(setattr, m, 'MIN_AVAILABLE', old)
        self.assertEqual(m.prefetch(self.image, memory=lambda: 100), 50)

    def test_low_memory_does_not_open_image(self):
        self.assertEqual(m.prefetch('/does-not-exist', memory=lambda: 0), 0)

    def test_stops_when_memory_pressure_increases(self):
        readings = iter([2**30, 2**30, 0])
        self.assertEqual(m.prefetch(self.image, memory=lambda: next(readings)), m.CHUNK)

    def test_rejects_other_files(self):
        self.image.write_bytes(b'not a squashfs image')
        self.assertEqual(m.prefetch(self.image, memory=lambda: 2**30), 0)

    def test_stops_at_end_of_image(self):
        self.assertEqual(m.prefetch(self.image, memory=lambda: 2**30), self.image.stat().st_size)


if __name__ == '__main__':
    unittest.main()
