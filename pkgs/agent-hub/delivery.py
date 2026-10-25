"""Durable execution receipts. Looking up an outcome never executes a command."""
import hashlib
import json
import re
import threading

from bridge import BridgeError


class Deliveries:
    def __init__(self, hub):
        self.hub = hub
        self.active = set()
        self.lock = threading.RLock()
        with hub.db() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS command_receipts(
                id TEXT PRIMARY KEY, digest TEXT NOT NULL, status TEXT NOT NULL, result TEXT)""")

    @staticmethod
    def digest(command_id, value):
        if not isinstance(command_id, str) or not re.fullmatch(r'[a-zA-Z0-9_-]{12,100}', command_id):
            raise BridgeError('A valid command ID is required')
        return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()

    def read(self, command_id, value):
        digest = self.digest(command_id, value)
        with self.hub.db() as db:
            row = db.execute('SELECT * FROM command_receipts WHERE id=?', (command_id,)).fetchone()
        if not row:
            return {'status': 'unknown'}
        if row['digest'] != digest:
            raise BridgeError('Command ID was reused with different contents')
        if row['status'] == 'complete':
            return {'status': 'complete', 'result': json.loads(row['result'])}
        with self.lock:
            active = command_id in self.active
        return {'status': 'sending' if active else 'uncertain'}

    def execute_once(self, command_id, value, operation):
        digest = self.digest(command_id, value)
        with self.lock, self.hub.db() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT * FROM command_receipts WHERE id=?', (command_id,)).fetchone()
            if row:
                if row['digest'] != digest:
                    raise BridgeError('Command ID was reused with different contents')
                if row['status'] == 'complete':
                    return json.loads(row['result'])
                raise BridgeError('Command outcome is uncertain or failed; inspect the agent before issuing a new command')
            db.execute("INSERT INTO command_receipts VALUES(?,?,'started',NULL)", (command_id, digest))
            self.active.add(command_id)
        try:
            result = operation()
            with self.hub.db() as db:
                db.execute("UPDATE command_receipts SET status='complete',result=? WHERE id=?", (json.dumps(result), command_id))
            return result
        except Exception:
            with self.hub.db() as db:
                db.execute("UPDATE command_receipts SET status='failed' WHERE id=?", (command_id,))
            raise
        finally:
            with self.lock:
                self.active.discard(command_id)


def supervisor_request(query, selected='', allow_actions=False, request_id='', clarification_id='', voice=False, attachments=None):
    return dict(message=query, selected_agent_id=selected, allow_actions=bool(allow_actions),
                request_id=request_id, clarification_id=clarification_id, voice=bool(voice),
                **({'attachments':attachments} if attachments else {}))
