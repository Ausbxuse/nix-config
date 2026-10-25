"""Bind a local Claude process to its structured transcript, without UI text."""

import json
import os
from pathlib import Path
import uuid


class ClaudeSessions:
    def __init__(self, config_dir, proc_root='/proc'):
        self.root = Path(config_dir).resolve()
        self.proc = Path(proc_root)

    def process(self, pid):
        path = self.proc / str(pid)
        if path.stat().st_uid != os.getuid():
            raise ValueError('Process belongs to a different user')
        raw = (path / 'stat').read_text()
        fields = raw[raw.rindex(')') + 2:].split()
        if raw.split(' ', 1)[0] != str(pid) or fields[0] in ('Z', 'X'):
            raise ValueError('Process is no longer running')
        return fields

    def binding(self, agent, connection):
        """The foreground process, registry, terminal and project must agree.

        Session registry files are version-specific. Missing or changed fields
        fail closed, leaving the existing conservative transcript fallback.
        Only the JSON record is read; peer keys and messaging sockets are unused.
        """
        if connection.get('kind') != 'local' or agent.get('runtime') != 'claude' or not agent.get('live'):
            return None
        try:
            pid, pane_pid = int(agent['foreground_pid']), int(agent['pid'])
            if pid <= 0 or pane_pid <= 0:
                return None
            path = self.root / 'sessions' / f'{pid}.json'
            stat = path.stat()
            if path.is_symlink() or not path.resolve().is_relative_to(self.root) or stat.st_size > 65_536 or stat.st_uid != os.getuid():
                return None
            record = json.loads(path.read_text())
            if not isinstance(record, dict) or record.get('pid') != pid or record.get('kind') != 'interactive':
                return None
            session = str(uuid.UUID(record['sessionId']))
            if record['sessionId'] != session or record.get('cwd') != agent.get('cwd'):
                return None
            if not str(record.get('tmux', '')).endswith(f":{agent['window']}.{agent['pane']}"):
                return None
            process, pane = self.process(pid), self.process(pane_pid)
            if record.get('procStart') != process[19] or int(pane[5]) != pid or int(process[2]) != pid:
                return None
            if process[4] != pane[4] or int(process[4]) == 0:
                return None
            environment = dict(item.split(b'=', 1) for item in (self.proc / str(pid) / 'environ').read_bytes().split(b'\0') if b'=' in item)
            socket = os.fsdecode(environment.get(b'TMUX', b'').split(b',', 1)[0])
            expected_socket = connection.get('socket')
            if not expected_socket or not socket or Path(socket).resolve() != Path(expected_socket).resolve():
                return None
            if os.fsdecode(environment.get(b'TMUX_PANE', b'')) != agent['pane']:
                return None
            # cwd in the registry is a session property, not a most-recent-file
            # guess. Confirm it against the same live foreground process.
            if (self.proc / str(pid) / 'cwd').resolve() != Path(record['cwd']).resolve():
                return None
            project = ''.join(char if char.isascii() and (char.isalnum() or char == '-') else '-' for char in record['cwd'])
            transcript = self.root / 'projects' / project / f'{session}.jsonl'
            if transcript.is_symlink() or not transcript.resolve().is_relative_to(self.root / 'projects') or not transcript.is_file():
                return None
            return {'path': str(transcript), 'session': session, 'pid': pid, 'start': process[19], 'cwd': record['cwd']}
        except (OSError, ValueError, TypeError, KeyError, IndexError, AttributeError):
            return None
