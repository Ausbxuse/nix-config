"""Abrupt process exit around durable send receipts, using a file as the effect."""
from contextlib import contextmanager
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest

from bridge import BridgeError
from delivery import Deliveries


class Store:
    def __init__(self, root):
        self.path = Path(root) / 'receipts.sqlite'

    @contextmanager
    def db(self):
        db = sqlite3.connect(self.path)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()


class CrashReceiptTests(unittest.TestCase):
    def test_exit_after_side_effect_and_after_commit_do_not_repeat_execution(self):
        code = """
import json,os,sys
from pathlib import Path
from delivery import Deliveries
from test_crash_receipts import Store
root,phase,request=sys.argv[1],sys.argv[2],json.loads(sys.argv[3])
def effect():
    with (Path(root)/'effect').open('xb') as output:
        output.write(b'executed once');output.flush();os.fsync(output.fileno())
    if phase=='effect':os._exit(73)
    return {'ok':True}
Deliveries(Store(root)).execute_once(request['command_id'],request,effect)
os._exit(74)
"""
        for phase in ('effect', 'commit'):
            with self.subTest(phase=phase), tempfile.TemporaryDirectory() as root:
                request = dict(type='send', agent_id='synthetic:1', message='test', command_id='web-crash-fixture-1')
                result = subprocess.run([sys.executable, '-c', code, root, phase, json.dumps(request)],
                                        cwd=Path(__file__).parent, timeout=10, capture_output=True)
                self.assertEqual(result.returncode, 73 if phase == 'effect' else 74)
                self.assertEqual((Path(root) / 'effect').read_bytes(), b'executed once')
                store = Deliveries(Store(root))
                self.assertEqual(store.read(request['command_id'], request)['status'], 'uncertain' if phase == 'effect' else 'complete')
                if phase == 'effect':
                    with self.assertRaises(BridgeError):
                        store.execute_once(request['command_id'], request, lambda: self.fail('duplicate effect'))
                else:
                    self.assertEqual(store.execute_once(request['command_id'], request, lambda: self.fail('duplicate effect')), {'ok': True})


if __name__ == '__main__':
    unittest.main()
