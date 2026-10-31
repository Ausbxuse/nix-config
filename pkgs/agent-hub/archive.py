"""Incremental, private visible-message index. No model calls or agent controls."""
from contextlib import contextmanager
from concurrent.futures import ThreadPoolExecutor
import datetime
import hashlib
import json
import logging
import math
import os
from pathlib import Path
import re
import sqlite3
import socket
import stat
import threading
import time
from urllib.parse import unquote
import uuid

from bridge import BridgeError
from media import TYPES, TEXT_NAMES
from transcripts import Transcripts
from message_content import user_text
from projects import ProjectIdentity

MAX_RECORD = 16 * 1024 * 1024
CHUNK = 4 * 1024 * 1024
PAGE_CHARS = 180000
TOKEN = re.compile(r'[^\W_]+', re.UNICODE)
REFERENCE = re.compile(r'(?<![\w@./:+~%\-])(?:\.{0,2}/|/)?[\w@+.\-]+(?:/[\w@+.\-]+)*(?::\d+(?::\d+)?)?(?:#L\d+(?:-L?\d+)?)?')
CODEX_HEADER = re.compile(rb'^\s*\{\s*"timestamp"\s*:\s*"(?:[^"\\]|\\.)*"\s*,\s*(?:"[a-zA-Z_][a-zA-Z0-9_]*"\s*:\s*\d+\s*,\s*)?"type"\s*:\s*"(response_item|event_msg)"\s*,\s*"payload"\s*:\s*\{\s*"type"\s*:\s*"([a-z_]+)"\s*[,}]')
LINK_PART = r'(?:\\[^\w\s]|[^\s\\()])'
for _ in range(4):
    LINK_PART = r'(?:' + LINK_PART + r'|\(' + LINK_PART + r'*\))'
LINK_TARGET = re.compile(r'\]\((?:<([^>\n]+)>|(' + LINK_PART + r'+))')


def terms(query):
    if not isinstance(query, str) or len(query) > 240:
        raise BridgeError('Search with up to 240 characters')
    return list(dict.fromkeys(TOKEN.findall(query.lower())))[:16]


def local_path(value):
    try:
        value = re.sub(r':\d+(?::\d+)?$', '', unquote(re.split(r'[?#]', value)[0], errors='strict'))
    except (UnicodeError, ValueError, TypeError):
        return None
    if not value or len(value) > 4096 or re.search(r'[\x00-\x1f\x7f]', value) or re.match(r'[a-z][a-z0-9+.-]*:', value, re.I) or value.startswith(('//', '~/')):
        return None
    if '.' not in value.rsplit('/', 1)[-1] and value.rsplit('/', 1)[-1] not in TEXT_NAMES:
        return None
    return value if Path(value).suffix.lower() in TYPES or Path(value).name in TEXT_NAMES else None


def artifacts(body):
    # Explicit Markdown destinations support spaces; automatic references match
    # the conservative browser grammar. These are references, not execution proof.
    paths = {}
    for match in LINK_TARGET.finditer(body):
        target = match[1] or re.sub(r'\\([^\w\s])', r'\1', match[2])
        if path := local_path(target):
            paths[path] = None
    # Stream matches. A long prose reply must not allocate one Python string
    # for every ordinary word before discarding non-file references.
    for match in REFERENCE.finditer(body):
        if path := local_path(match[0].rstrip('.')):
            paths[path] = None
    return list(paths)


def stamp(value, fallback):
    try:
        return datetime.datetime.fromisoformat(value.replace('Z', '+00:00')).timestamp()
    except (ValueError, TypeError, AttributeError, OverflowError):
        return fallback


def identifier(value):
    if not isinstance(value, str) or not re.fullmatch(r'[0-9a-f]{32}', value):
        raise BridgeError('Invalid archived conversation')
    return value


class Archive:
    def __init__(self, directory, stop=None, roots=None):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.directory.chmod(0o700)
        self.path = self.directory / 'archive-v1.sqlite'
        home = Path.home()
        self.roots = roots if roots is not None else [('codex', home / '.codex/sessions'),
                    ('codex', home / '.codex/archived_sessions'), ('claude', home / '.claude/projects')]
        self.roots = [(runtime, Path(path).resolve()) for runtime, path in self.roots]
        self.stop = stop or threading.Event()
        self.wake = threading.Event()
        self.lock = threading.Lock()
        self.thread = None
        self.parser = Transcripts()
        self.research_projects = ProjectIdentity(ttl=300)
        self.paths = []
        self.seen = {}
        self.bindings = {}
        self.scanned = 0
        self.metrics = dict(bytes_read=0, records=0, resets=0)
        with self.db() as db:
            db.execute('PRAGMA journal_mode=WAL')
            db.executescript('''
                CREATE TABLE IF NOT EXISTS archive_sessions(
                  id TEXT PRIMARY KEY, runtime TEXT, native_id TEXT, path TEXT, cwd TEXT, title TEXT,
                  stamp TEXT, size INTEGER, offset INTEGER, prefix BLOB, guard BLOB,
                  generation TEXT, parser TEXT, updated REAL, skipped INTEGER DEFAULT 0, missing INTEGER DEFAULT 0);
                CREATE TABLE IF NOT EXISTS archive_messages(
                  rowid INTEGER PRIMARY KEY, session TEXT, ordinal INTEGER, id TEXT,
                  role TEXT, phase TEXT, text TEXT, created TEXT, sort_time REAL, cwd TEXT, paths TEXT,
                  UNIQUE(session,id));
                CREATE INDEX IF NOT EXISTS archive_session_path ON archive_sessions(path);
                CREATE INDEX IF NOT EXISTS archive_message_order ON archive_messages(session,ordinal);
                CREATE VIRTUAL TABLE IF NOT EXISTS archive_fts USING fts5(text, paths, content='archive_messages', content_rowid='rowid');
                CREATE TRIGGER IF NOT EXISTS archive_insert AFTER INSERT ON archive_messages BEGIN
                  INSERT INTO archive_fts(rowid,text,paths) VALUES(new.rowid,new.text,new.paths); END;
                CREATE TRIGGER IF NOT EXISTS archive_delete AFTER DELETE ON archive_messages BEGIN
                  INSERT INTO archive_fts(archive_fts,rowid,text,paths) VALUES('delete',old.rowid,old.text,old.paths); END;
                CREATE TABLE IF NOT EXISTS archive_bindings(path TEXT PRIMARY KEY, agent TEXT, title TEXT);
                CREATE TABLE IF NOT EXISTS archive_research_meta(key TEXT PRIMARY KEY,value TEXT NOT NULL);
                INSERT OR IGNORE INTO archive_research_meta VALUES('revision','0');
                CREATE TABLE IF NOT EXISTS archive_research_sources(session TEXT PRIMARY KEY,revision INTEGER NOT NULL);
                CREATE INDEX IF NOT EXISTS archive_research_revision ON archive_research_sources(revision);
                CREATE TRIGGER IF NOT EXISTS archive_research_insert AFTER INSERT ON archive_sessions BEGIN
                  UPDATE archive_research_meta SET value=CAST(value AS INTEGER)+1 WHERE key='revision';
                  INSERT OR REPLACE INTO archive_research_sources VALUES(new.id,(SELECT CAST(value AS INTEGER) FROM archive_research_meta WHERE key='revision'));
                END;
                CREATE TRIGGER IF NOT EXISTS archive_research_update AFTER UPDATE ON archive_sessions BEGIN
                  UPDATE archive_research_meta SET value=CAST(value AS INTEGER)+1 WHERE key='revision';
                  INSERT OR REPLACE INTO archive_research_sources VALUES(new.id,(SELECT CAST(value AS INTEGER) FROM archive_research_meta WHERE key='revision'));
                END;
            ''')
            if db.execute('PRAGMA user_version').fetchone()[0] < 2:
                self._clean_context(db)
                db.execute('PRAGMA user_version=2')
            db.execute("INSERT OR IGNORE INTO archive_research_meta VALUES('epoch',?)", (uuid.uuid4().hex,))
            # Populate only the derived manifest. Existing logs need no reread.
            for row in db.execute('SELECT id FROM archive_sessions WHERE id NOT IN (SELECT session FROM archive_research_sources) ORDER BY updated DESC').fetchall():
                db.execute("UPDATE archive_research_meta SET value=CAST(value AS INTEGER)+1 WHERE key='revision'")
                db.execute("INSERT INTO archive_research_sources VALUES(?,(SELECT CAST(value AS INTEGER) FROM archive_research_meta WHERE key='revision'))", (row['id'],))
        # Keeps the WAL open between writes (see Hub.wal_anchor).
        self.wal_anchor = sqlite3.connect(self.path, check_same_thread=False)

    @staticmethod
    def _clean_context(db):
        """Repair the derived index once; keep original logs and message IDs."""
        rows = db.execute("SELECT m.* FROM archive_messages m "
                          "WHERE m.role='user' AND "
                          "(ltrim(m.text, char(9)||char(10)||char(13)||' ') LIKE '<%' OR ltrim(m.text, char(9)||char(10)||char(13)||' ') LIKE '# AGENTS.md instructions for %')")
        while batch := rows.fetchmany(128):
            for row in batch:
                body = user_text(row['text'])
                if body == row['text']:
                    continue
                # Delete/insert invokes both FTS triggers. Retain ordinals,
                # identities and generations so existing reply links still work.
                db.execute('DELETE FROM archive_messages WHERE rowid=?', (row['rowid'],))
                if body:
                    item = dict(row, text=body, paths=json.dumps(artifacts(body)))
                    db.execute('INSERT INTO archive_messages(rowid,session,ordinal,id,role,phase,text,created,sort_time,cwd,paths) VALUES(?,?,?,?,?,?,?,?,?,?,?)', tuple(item.values()))

    @contextmanager
    def db(self):
        db = sqlite3.connect(self.path, timeout=5)
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA synchronous=NORMAL')
        try:
            with db:
                yield db
        finally:
            db.close()

    def start(self):
        with self.lock:
            if self.thread and self.thread.is_alive():
                return
            self.thread = threading.Thread(target=self.run, name='hub-archive', daemon=True)
            self.thread.start()

    def close(self):
        self.stop.set(); self.wake.set()
        if self.thread:
            self.thread.join(5)
        self.wal_anchor.close()

    def run(self):
        while not self.stop.is_set():
            try:
                pending = self.refresh()
            except (OSError, ValueError, sqlite3.Error):
                logging.exception('Archive indexing will retry')
                pending = False
            self.wake.wait(.05 if pending else 5)
            self.wake.clear()

    def discover(self):
        found = []
        for runtime, root in self.roots:
            pattern = '**/rollout-*.jsonl' if runtime == 'codex' else '*/*.jsonl'
            for path in root.glob(pattern):
                try:
                    info = path.lstat()
                    if stat.S_ISREG(info.st_mode) and path.resolve().is_relative_to(root):
                        found.append((info.st_mtime_ns, runtime, path))
                except OSError:
                    continue
        self.paths = [(runtime, path) for _, runtime, path in sorted(found, reverse=True)]
        self.scanned = time.monotonic()
        existing = {str(path) for _, path in self.paths}
        with self.db() as db:
            for row in db.execute('SELECT id,path,missing FROM archive_sessions').fetchall():
                missing = int(row['path'] not in existing)
                if row['missing'] != missing:
                    db.execute('UPDATE archive_sessions SET missing=? WHERE id=?', (missing, row['id']))

    def refresh(self, discover=False):
        self.flush_bindings()
        if discover or not self.scanned or time.monotonic() - self.scanned > 30:
            self.discover()
        pending = False
        # One bounded chunk per file per pass prevents a long session from
        # starving older, short conversations. Work resumes from durable offsets.
        for runtime, path in self.paths:
            if self.stop.is_set():
                break
            try:
                before = self.metrics['bytes_read']
                pending |= self.index(path, runtime)
            except (OSError, ValueError, TypeError, KeyError, UnicodeError, BridgeError):
                continue  # Replaced/part-written sources are rechecked next pass.
            if self.metrics['bytes_read'] != before and self.stop.wait(.005):
                break
        return pending

    def _open(self, path):
        resolved = path.resolve()
        if not any(resolved.is_relative_to(root) for _, root in self.roots) or path.is_symlink():
            raise BridgeError('Session source is outside the archive roots')
        fd = os.open(resolved, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        stream = os.fdopen(fd, 'rb')
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            stream.close(); raise BridgeError('Invalid session source')
        return stream

    def index(self, path, runtime):
        path = Path(path)
        info = path.lstat()
        observed = (info.st_dev, info.st_ino, info.st_mtime_ns, info.st_size)
        if not stat.S_ISREG(info.st_mode):
            raise BridgeError('Invalid session source')
        if self.seen.get(str(path)) == observed:
            return False
        with self._open(path) as stream, self.db() as db:
            info = os.fstat(stream.fileno())
            fingerprint = json.dumps([info.st_dev, info.st_ino, info.st_mtime_ns, info.st_size])
            old = db.execute('SELECT * FROM archive_sessions WHERE path=?', (str(path),)).fetchone()
            # Resuming Codex can produce several rollout logs for one native
            # thread. Each log is a source; the native UUID is not a unique key.
            source = path.name if runtime == 'codex' else path.parent.name + '/' + path.name
            key = old['id'] if old else hashlib.sha256((runtime + ':' + source).encode()).hexdigest()[:32]
            if not old:
                old = db.execute('SELECT * FROM archive_sessions WHERE id=?', (key,)).fetchone()
                if old and old['path'] != str(path) and Path(old['path']).exists():
                    # Preserve both copies even if an unusual directory layout
                    # reuses the same filename. Never silently merge evidence.
                    key = hashlib.sha256((runtime + ':' + str(path.resolve())).encode()).hexdigest()[:32]
                    old = db.execute('SELECT * FROM archive_sessions WHERE id=?', (key,)).fetchone()
                if old and old['path'] != str(path):
                    # A move into archived_sessions retains its identity and
                    # offsets, including existing exact-message links.
                    db.execute('UPDATE archive_sessions SET path=?,missing=0 WHERE id=?', (str(path), key))
            if old and old['stamp'] == fingerprint and json.loads(old['parser']).get('at_eof'):
                self.seen[str(path)] = tuple(json.loads(fingerprint))
                return False
            def read(size):
                value = stream.read(size); self.metrics['bytes_read'] += len(value); return value
            prefix = read(min(256, info.st_size))
            append = bool(old and info.st_size >= old['size'] and json.loads(old['stamp'])[:2] == [info.st_dev, info.st_ino]
                          and prefix[:len(old['prefix'])] == old['prefix'])
            if append:
                stream.seek(max(0, old['offset'] - 256))
                append = read(min(256, old['offset'])) == old['guard']
                # A same-length rewrite must never retain stale indexed text.
                if old['stamp'] != fingerprint and info.st_size == old['size']:
                    append = False
            if append:
                session = dict(old); state = json.loads(session['parser']); offset = session['offset']
            else:
                stream.seek(0); first = stream.readline(MAX_RECORD + 1); self.metrics['bytes_read'] += len(first)
                if len(first) > MAX_RECORD:
                    return False
                record = json.loads(first)
                meta = record.get('payload', {}) if runtime == 'codex' else record
                native = str(uuid.UUID(meta.get('id' if runtime == 'codex' else 'sessionId', path.stem)))
                if runtime == 'codex' and record.get('type') != 'session_meta':
                    return False
                cwd = meta.get('cwd', '')
                session = dict(id=key, runtime=runtime, native_id=native, path=str(path), cwd=cwd, title='',
                    generation=uuid.uuid4().hex, skipped=0)
                state = dict(cwd=cwd, session=native, turn='', messages=[], discarding=False)
                offset = 0
                db.execute('DELETE FROM archive_messages WHERE session=?', (key,))
                if old and old['id'] != key:
                    db.execute('DELETE FROM archive_messages WHERE session=?', (old['id'],))
                    db.execute('DELETE FROM archive_sessions WHERE id=?', (old['id'],))
                self.metrics['resets'] += 1
            stream.seek(offset)
            start = offset
            ordinal = db.execute('SELECT COALESCE(MAX(ordinal),0) FROM archive_messages WHERE session=?', (session['id'],)).fetchone()[0]
            while stream.tell() - start < CHUNK and not self.stop.is_set():
                at = stream.tell(); line = stream.readline(MAX_RECORD + 1)
                self.metrics['bytes_read'] += len(line)
                if not line:
                    break
                if state.get('discarding'):
                    state['discarding'] = not line.endswith(b'\n'); offset = stream.tell(); continue
                if len(line) > MAX_RECORD:
                    header = CODEX_HEADER.match(line[:2048]) if runtime == 'codex' else None
                    # Large tool/image payloads are outside the visible-message
                    # index. Only count omitted potentially visible records.
                    ignored = header and (header[1] == b'event_msg' or header[2] in (b'function_call_output', b'custom_tool_call_output', b'function_call', b'custom_tool_call', b'reasoning'))
                    if not ignored:
                        session['skipped'] += 1
                    state['discarding'] = not line.endswith(b'\n'); offset = stream.tell(); continue
                if not line.endswith(b'\n'):
                    # Commit complete lines only. Never index a half-written turn.
                    stream.seek(at); break
                offset = stream.tell()
                if runtime == 'codex' and b'"turn_context"' in line:
                    try:
                        context = json.loads(line)
                        if context.get('type') == 'turn_context':
                            if 'cwd' in context.get('payload', {}):
                                state['cwd'] = context['payload']['cwd']
                            continue
                    except (ValueError, TypeError, AttributeError):
                        session['skipped'] += 1; continue
                if runtime == 'codex' and (b'"response_item"' not in line or b'"message"' not in line):
                    continue
                before = state['messages'][-1]['id'] if state['messages'] else None
                try:
                    self.parser._parse_record(line, state, runtime, max_text=MAX_RECORD)
                except (ValueError, TypeError, AttributeError, KeyError):
                    session['skipped'] += 1; continue
                if not state['messages'] or state['messages'][-1]['id'] == before:
                    continue
                item = state['messages'][-1]
                state['messages'] = [item]  # The parser needs only the previous identity for deduplication.
                cwd = state['cwd']
                if not isinstance(cwd, str) or not Path(cwd).is_absolute() or len(cwd) > 4096:
                    cwd = ''  # Text remains readable; unknown project roots grant no file access.
                if item['role'] not in ('user', 'assistant'):
                    continue
                ordinal += 1
                refs = artifacts(item['text'])
                db.execute('INSERT OR IGNORE INTO archive_messages(session,ordinal,id,role,phase,text,created,sort_time,cwd,paths) VALUES(?,?,?,?,?,?,?,?,?,?)',
                    (session['id'], ordinal, item['id'], item['role'], item.get('phase', ''), item['text'], item['created'], stamp(item['created'], info.st_mtime), cwd, json.dumps(refs)))
                self.metrics['records'] += 1
                session['cwd'] = cwd
                if not session['title'] and item['role'] == 'user' and not item['text'].lstrip().startswith(('# AGENTS', '<', '# Context')):
                    session['title'] = re.sub(r'\s+', ' ', item['text']).strip()[:140]
            at_eof = stream.tell() >= info.st_size or offset == start
            state['at_eof'] = at_eof
            state['messages'] = [{'id': state['messages'][-1]['id']}] if state['messages'] else []
            stream.seek(max(0, offset - 256)); guard = read(min(256, offset))
            db.execute('INSERT OR REPLACE INTO archive_sessions VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                (session['id'], runtime, session['native_id'], str(path), session['cwd'], session['title'], fingerprint, info.st_size,
                 offset, prefix, guard, session['generation'], json.dumps(state), info.st_mtime, session['skipped'], 0))
            if at_eof:
                self.seen[str(path)] = tuple(json.loads(fingerprint))
        return not at_eof

    def bind(self, path, agent):
        if not path or not any(Path(path).resolve().is_relative_to(root) for _, root in self.roots):
            return
        with self.lock:
            self.bindings[agent['id']] = (path, agent.get('display_title') or agent['title'])
        self.wake.set()

    def flush_bindings(self):
        # Preview delivery must never wait on a background index write lock.
        with self.lock:
            pending, self.bindings = self.bindings, {}
        if not pending:
            return
        try:
            with self.db() as db:
                for agent, (path, title) in pending.items():
                    db.execute('DELETE FROM archive_bindings WHERE agent=? AND path!=?', (agent, path))
                    db.execute('INSERT INTO archive_bindings VALUES(?,?,?) ON CONFLICT(path) DO UPDATE SET agent=excluded.agent,title=excluded.title WHERE agent!=excluded.agent OR title!=excluded.title', (path, agent, title))
        except sqlite3.Error:
            with self.lock:
                for agent, value in pending.items():
                    self.bindings.setdefault(agent, value)
            raise

    def status(self):
        with self.db() as db:
            row = db.execute('SELECT COUNT(*) AS sessions,COALESCE(SUM(size),0) AS bytes,COALESCE(SUM(offset),0) AS indexed_bytes,COALESCE(SUM(skipped),0) AS skipped FROM archive_sessions').fetchone()
            result = dict(row)
            result['messages'] = db.execute('SELECT COUNT(*) FROM archive_messages').fetchone()[0]
        return {**result, 'discovering': not self.scanned, 'indexing': result['indexed_bytes'] < result['bytes'], 'known_files': len(self.paths)}

    def research_sources(self, after=0, limit=16):
        """A bounded change manifest; revisions follow indexing, not file mtimes."""
        if type(after) is not int or after < 0 or type(limit) is not int or not 1 <= limit <= 32:
            raise BridgeError('Invalid research source cursor')
        with self.db() as db:
            epoch = db.execute("SELECT value FROM archive_research_meta WHERE key='epoch'").fetchone()[0]
            rows = db.execute('SELECT r.revision,s.id AS session,s.generation,s.runtime,s.cwd,s.title,s.missing,s.updated,b.title AS named,b.agent,'
                              '(SELECT COALESCE(MAX(ordinal),0) FROM archive_messages WHERE session=s.id) AS last_ordinal '
                              'FROM archive_research_sources r JOIN archive_sessions s ON s.id=r.session LEFT JOIN archive_bindings b ON b.path=s.path '
                              'WHERE r.revision>? ORDER BY r.revision LIMIT ?', (after, limit + 1)).fetchall()
        items = [{key: row[key] for key in ('session', 'generation', 'runtime', 'cwd', 'revision', 'last_ordinal', 'updated')} |
                 {'title': (row['named'] or row['title'] or Path(row['cwd']).name or row['runtime'])[:1000],
                  'agent_id': row['agent'], 'missing': bool(row['missing'])} for row in rows[:limit]]
        return {'items': items, 'epoch': epoch, 'next': items[-1]['revision'] if items else after, 'more': len(rows) > limit}

    def research_evidence(self, session, generation, after=0, limit=16):
        """Visible final responses only, with exact identities and content digests.

        Pages are incremental and have a fixed text budget. A source rewrite
        invalidates the generation instead of silently attaching stale quotes.
        """
        identifier(session); identifier(generation)
        if type(after) is not int or after < 0 or type(limit) is not int or not 1 <= limit <= 16:
            raise BridgeError('Invalid research evidence cursor')
        with self.db() as db:
            current = db.execute('SELECT generation FROM archive_sessions WHERE id=?', (session,)).fetchone()
            if not current or current['generation'] != generation:
                raise BridgeError('This research source changed. Refresh its manifest.')
            rows = db.execute("SELECT id,ordinal,text,created,sort_time,cwd FROM archive_messages WHERE session=? AND ordinal>? AND role='assistant' AND phase NOT IN ('analysis','commentary','partial_answer') ORDER BY ordinal LIMIT ?", (session, after, limit + 1))
            items = []
            more = False
            for row in rows:
                if len(items) == limit:
                    more = True; break
                body = row['text']
                items.append({'message_id': row['id'], 'ordinal': row['ordinal'], 'text': body[:6000],
                              'digest': hashlib.sha256(body.encode()).hexdigest(), 'truncated': len(body) > 6000,
                              'created': row['created'][:120], 'sort_time': row['sort_time'], 'cwd': row['cwd'],
                              'project_id': self.research_projects.key(row['cwd'], socket.gethostname())})
            last = db.execute('SELECT COALESCE(MAX(ordinal),0) FROM archive_messages WHERE session=?', (session,)).fetchone()[0]
        return {'session': session, 'generation': generation, 'items': items,
                'next': items[-1]['ordinal'] if more else last, 'more': more}

    def history_source(self, path, messages):
        """Bridge a verified live tail to older indexed messages without rereading logs."""
        ids = [item['id'] for item in messages if item.get('role') in ('user', 'assistant') and item.get('id')][:200]
        if not ids:
            return None
        with self.db() as db:
            session = db.execute('SELECT id,generation FROM archive_sessions WHERE path=? AND missing=0', (str(path),)).fetchone()
            if not session:
                return None
            placeholders = ','.join('?' for _ in ids)
            first = db.execute('SELECT id,ordinal FROM archive_messages WHERE session=? AND id IN ('+placeholders+') ORDER BY ordinal LIMIT 1', (session['id'], *ids)).fetchone()
            if not first:
                return None  # The background index has not reached this live tail yet.
            earlier = bool(db.execute('SELECT 1 FROM archive_messages WHERE session=? AND ordinal<? LIMIT 1', (session['id'], first['ordinal'])).fetchone())
        return {'session':session['id'], 'generation':session['generation'], 'anchor':first['id'], 'earlier':earlier}

    def search(self, query, before=None, files=False, limit=21):
        words = terms(query)
        if not words:
            return {'items': [], 'status': self.status()}
        if before is not None and (not isinstance(before, list) or len(before) != 2 or type(before[0]) not in (int, float) or not math.isfinite(before[0]) or not isinstance(before[1], str) or len(before[1]) > 256):
            raise BridgeError('Invalid search cursor')
        match = ' AND '.join('"' + word + '"' for word in words)
        clauses = ['archive_fts MATCH ?']; args = [match]
        if files:
            clauses.append("m.paths!='[]'")
        if before:
            clauses.append("(m.sort_time<? OR (m.sort_time=? AND m.session||':'||m.id<?))"); args += [before[0], before[0], before[1]]
        with self.db() as db:
            rows = db.execute('SELECT m.*,s.runtime,s.title,s.path,s.missing,s.generation,b.title AS named,b.agent FROM archive_fts JOIN archive_messages m ON m.rowid=archive_fts.rowid JOIN archive_sessions s ON s.id=m.session LEFT JOIN archive_bindings b ON b.path=s.path WHERE '+ ' AND '.join(clauses) + " ORDER BY m.sort_time DESC,m.session||':'||m.id DESC LIMIT ?", (*args, limit)).fetchall()
        items = []
        for row in rows:
            text = row['text']; positions = [match.start() for word in words if (match := re.search(re.escape(word), text, re.I))]
            position = min(positions, default=0); start = max(0, position - 80); end = min(len(text), start + 300)
            items.append({'session': row['session'], 'message_id': row['id'], 'role': row['role'], 'phase': row['phase'],
                'title': row['named'] or row['title'] or Path(row['cwd']).name or row['runtime'], 'cwd': row['cwd'], 'runtime': row['runtime'],
                'created': row['created'], 'sort_time': row['sort_time'], 'key': row['session'] + ':' + row['id'],
                'excerpt': text[start:end], 'offset': position, 'paths': json.loads(row['paths'])[:4],
                'generation': row['generation'], 'missing': bool(row['missing']), 'agent_id': row['agent']})
        return {'items': items, 'status': self.status()}

    def conversation(self, key, anchor='', direction='', generation='', offset=0):
        identifier(key)
        if direction not in ('', 'before', 'after') or not isinstance(anchor, str) or len(anchor) > 80 or type(offset) is not int or not 0 <= offset <= MAX_RECORD:
            raise BridgeError('Invalid archive page')
        with self.db() as db:
            session = db.execute('SELECT s.*,b.title AS named,b.agent FROM archive_sessions s LEFT JOIN archive_bindings b ON b.path=s.path WHERE s.id=?', (key,)).fetchone()
            if not session:
                raise BridgeError('Archived conversation unavailable')
            if generation and generation != session['generation']:
                raise BridgeError('This source changed. Search again for its current messages.')
            found = db.execute('SELECT ordinal FROM archive_messages WHERE session=? AND id=?', (key, anchor)).fetchone() if anchor else None
            if anchor and not found:
                raise BridgeError('This message changed or is no longer in the source')
            ordinal = found[0] if found else 0
            where = 'ordinal<?' if direction == 'before' else 'ordinal>?' if direction == 'after' else 'ordinal>=?'
            start = ordinal if direction else max(0, ordinal - 3)
            rows = db.execute('SELECT * FROM archive_messages WHERE session=? AND '+where+' ORDER BY ordinal '+('DESC' if direction == 'before' or not anchor else 'ASC')+' LIMIT 24', (key, start)).fetchall()
            if direction == 'before' or not anchor:
                rows = list(reversed(rows))
                while len(rows) > 1 and sum(min(PAGE_CHARS, len(row['text'])) for row in rows) > PAGE_CHARS:
                    rows.pop(0)
            if not direction and anchor:
                # Always include the exact match, even when surrounding replies
                # are unusually long. Context is added within the display budget.
                center = next(i for i, row in enumerate(rows) if row['id'] == anchor)
                while center and sum(min(PAGE_CHARS, len(row['text'])) for row in rows[:center+1]) > PAGE_CHARS:
                    rows.pop(0); center -= 1
            messages, size = [], 0
            for row in rows:
                body = row['text']; part = min(offset, max(0, len(body)-1)) if row['id'] == anchor and not direction else 0
                if len(body) > PAGE_CHARS:
                    part = max(0, min(part - (80 if offset else 0), len(body)-1))
                else:
                    part = 0
                shown = body[part:part+PAGE_CHARS]
                if messages and size + len(shown) > PAGE_CHARS:
                    break
                messages.append({'id': row['id'], 'role': row['role'], 'phase': row['phase'], 'text': shown, 'created': row['created'],
                    'cwd': row['cwd'], 'ordinal': row['ordinal'], 'part': {'start': part, 'end': part+len(shown), 'total': len(body)}})
                size += len(shown)
            first, last = (messages[0]['ordinal'], messages[-1]['ordinal']) if messages else (0, 0)
            earlier = bool(db.execute('SELECT 1 FROM archive_messages WHERE session=? AND ordinal<? LIMIT 1', (key, first)).fetchone())
            later = bool(db.execute('SELECT 1 FROM archive_messages WHERE session=? AND ordinal>? LIMIT 1', (key, last)).fetchone())
        return {'session': key, 'title': session['named'] or session['title'] or Path(session['cwd']).name,
                'runtime': session['runtime'], 'cwd': session['cwd'], 'generation': session['generation'],
                'missing': bool(session['missing']), 'skipped': session['skipped'], 'agent_id': session['agent'],
                'messages': messages, 'earlier': earlier, 'later': later}

    def asset_agent(self, key, message, name):
        identifier(key)
        if not isinstance(message, str) or not re.fullmatch(r'[0-9a-f]{20}', message):
            raise BridgeError('Invalid archived message')
        with self.db() as db:
            row = db.execute('SELECT cwd,paths FROM archive_messages WHERE session=? AND id=?', (key, message)).fetchone()
            referenced = row and name in json.loads(row['paths'])
            if row and not referenced and Path(name).suffix.lower() in ('.tsv', '.jsonl', '.ndjson'):
                # Older indexes predate these file types. Recheck the saved visible
                # message on demand; keep exact references and project confinement.
                saved = db.execute('SELECT text FROM archive_messages WHERE session=? AND id=?', (key, message)).fetchone()
                referenced = name in artifacts(saved['text'])
        if not referenced:
            raise BridgeError('This file is not referenced in the archived message')
        if not row['cwd'] or not Path(row['cwd']).is_absolute():
            raise BridgeError('This message has no verified project directory for files')
        return {'cwd': row['cwd']}


class ArchiveQueries:
    """Small controller-initiated queries. Workers return only their own archive."""
    def __init__(self, hub):
        self.hub = hub

    @property
    def node(self):
        return self.hub.mesh.node_id if self.hub.mesh else 'local'

    def hosts(self):
        result = {self.node: self.hub.mesh.config.get('label', self.node) if self.hub.mesh else 'This host'}
        if self.hub.mesh and not self.hub.mesh.is_worker:
            result.update({key: value.get('label', key) for key, value in self.hub.mesh.peers.items()})
        return result

    def remote(self, host, method, value):
        if host not in self.hosts() or host == self.node:
            raise BridgeError('Archive host unavailable')
        prefix = 'worker/' if self.hub.mesh.peers[host].get('role') == 'worker' else ''
        return self.hub.mesh.request(host, prefix + method, value)

    def research_sources(self, host, after=0, limit=16):
        hosts = self.hosts()
        if host not in hosts:
            raise BridgeError('Unknown archive host')
        value = {'after': after, 'limit': limit}
        if host == self.node:
            self.hub.archive.start()
            result = self.hub.archive.research_sources(**value)
        else:
            result = self.remote(host, 'archive/research_sources', value)
        identifier(result['epoch'])
        if not isinstance(result['items'], list) or len(result['items']) > limit or type(result['next']) is not int or result['next'] < after or type(result['more']) is not bool:
            raise BridgeError('Invalid research source manifest')
        previous = after
        items = []
        for item in result['items']:
            identifier(item['session']); identifier(item['generation'])
            if type(item['revision']) is not int or item['revision'] <= previous or type(item['last_ordinal']) is not int or item['last_ordinal'] < 0:
                raise BridgeError('Invalid research source order')
            if any(not isinstance(item[key], str) or len(item[key]) > maximum for key, maximum in (('title', 1000), ('cwd', 4096), ('runtime', 40))):
                raise BridgeError('Invalid research source metadata')
            previous = item['revision']
            agent = item.get('agent_id')
            if type(item.get('updated')) not in (int, float) or not math.isfinite(item['updated']):
                raise BridgeError('Invalid research source time')
            items.append({key: item[key] for key in ('session', 'generation', 'runtime', 'cwd', 'revision', 'last_ordinal', 'title', 'updated')} |
                         {'agent_id': agent if isinstance(agent, str) and len(agent) <= 240 and agent.startswith(host + ':') else None,
                          'missing': item.get('missing') is True, 'host': host, 'host_label': hosts[host]})
        if items and result['next'] != previous:
            raise BridgeError('Invalid research source cursor')
        return {**result, 'items': items}

    def research_evidence(self, host, session, generation, after=0, limit=16):
        if host not in self.hosts():
            raise BridgeError('Unknown archive host')
        value = {'session': session, 'generation': generation, 'after': after, 'limit': limit}
        result = self.hub.archive.research_evidence(**value) if host == self.node else self.remote(host, 'archive/research_evidence', value)
        if result.get('session') != session or result.get('generation') != generation or not isinstance(result.get('items'), list) or len(result['items']) > limit:
            raise BridgeError('Invalid research evidence page')
        previous = after
        for item in result['items']:
            if not isinstance(item.get('message_id'), str) or not re.fullmatch(r'[0-9a-f]{20}', item['message_id']) or not isinstance(item.get('digest'), str) or not re.fullmatch(r'[0-9a-f]{64}', item['digest']):
                raise BridgeError('Invalid research evidence identity')
            if type(item['ordinal']) is not int or item['ordinal'] <= previous or type(item['sort_time']) not in (int, float) or not math.isfinite(item['sort_time']):
                raise BridgeError('Invalid research evidence order')
            if any(not isinstance(item[key], str) or len(item[key]) > maximum for key, maximum in (('text', 6000), ('cwd', 4096), ('created', 120))):
                raise BridgeError('Invalid research evidence text')
            if item.get('truncated') is not True and hashlib.sha256(item['text'].encode()).hexdigest() != item['digest']:
                raise BridgeError('Research evidence digest does not match')
            if not isinstance(item.get('project_id'), str) or (item['project_id'] and not re.fullmatch(r'[0-9a-f]{24}', item['project_id'])):
                raise BridgeError('Invalid research project identity')
            previous = item['ordinal']
        if type(result['next']) is not int or result['next'] < previous or type(result['more']) is not bool or (result['more'] and not result['items']):
            raise BridgeError('Invalid research evidence cursor')
        return result

    def search(self, value):
        query, selected = value.get('query', ''), value.get('host', '')
        terms(query)
        hosts = self.hosts()
        if selected and selected not in hosts:
            raise BridgeError('Unknown archive host')
        if selected:
            hosts = {selected: hosts[selected]}
        cursor = value.get('before')
        if cursor is not None and (not isinstance(cursor, list) or len(cursor) != 2 or type(cursor[0]) not in (int, float) or not math.isfinite(cursor[0]) or not isinstance(cursor[1], str) or len(cursor[1]) > 256):
            raise BridgeError('Invalid search cursor')
        def search(host):
            # The global key begins with the host. Preserve that ordering when
            # translating a cursor for each local index.
            before = None
            if cursor:
                owner, _, key = cursor[1].partition(':')
                before = [cursor[0], key if owner == host else '\uffff' if host < owner else '']
            request = {'query': query, 'before': before, 'files': value.get('files') is True}
            if host == self.node:
                self.hub.archive.start()
                return self.hub.archive.search(**request)
            return self.remote(host, 'archive/search', request)
        items, statuses, errors = [], [], []
        with ThreadPoolExecutor(max_workers=min(8, len(hosts))) as pool:
            requests = [(host, pool.submit(search, host)) for host in hosts]
            for host, future in requests:
                try:
                    result = future.result()
                    if not isinstance(result['items'], list) or len(result['items']) > 21:
                        raise ValueError('Invalid archive result page')
                    for item in result['items']:
                        identifier(item['session'])
                        if not re.fullmatch(r'[0-9a-f]{20}', item['message_id']) or item['role'] not in ('assistant', 'user') or not math.isfinite(item['sort_time']):
                            raise ValueError('Invalid archive result')
                        if any(not isinstance(item[key], str) or len(item[key]) > limit for key, limit in (('title', 1000), ('excerpt', 300), ('cwd', 4096), ('created', 120))):
                            raise ValueError('Invalid archive result text')
                        if not isinstance(item['paths'], list) or len(item['paths']) > 4 or any(not isinstance(path, str) or len(path) > 4096 for path in item['paths']):
                            raise ValueError('Invalid archived file references')
                        identifier(item['generation'])
                        items.append({**item, 'host': host, 'host_label': hosts[host], 'key': host + ':' + item['session'] + ':' + item['message_id']})
                    status = {key: result['status'][key] for key in ('sessions', 'bytes', 'indexed_bytes', 'skipped', 'messages', 'known_files', 'indexing', 'discovering')}
                    if any(type(status[key]) is not int or status[key] < 0 for key in ('sessions', 'bytes', 'indexed_bytes', 'skipped', 'messages', 'known_files')):
                        raise ValueError('Invalid index status')
                    statuses.append({**status, 'host': host, 'label': hosts[host]})
                except (BridgeError, ValueError, TypeError, KeyError, sqlite3.Error, OSError) as exc:
                    errors.append({'host': host, 'label': hosts[host], 'error': str(exc)})
        items.sort(key=lambda item: (item['sort_time'], item['key']), reverse=True)
        more = len(items) > 20
        items = items[:20]
        return {'items': items, 'hosts': statuses, 'unavailable': errors,
                'next': [items[-1]['sort_time'], items[-1]['key']] if more else None}

    def conversation(self, host, value):
        if host not in self.hosts():
            raise BridgeError('Unknown archive host')
        try:
            return self._conversation(host, value)
        except (ValueError, TypeError, KeyError, AttributeError, sqlite3.Error, OSError) as exc:
            raise BridgeError('History is unavailable. Refresh and try again.') from exc

    def _conversation(self, host, value):
        if host == self.node:
            self.hub.archive.start(); result = self.hub.archive.conversation(**value)
            if hasattr(self.hub, 'archive_images'):
                result = self.hub.archive_images(result)
        else:
            result = self.remote(host, 'archive/conversation', value)
        identifier(result['session'])
        if result['session'] != value.get('key') or len(result['messages']) > 24:
            raise BridgeError('Archive source does not match the request')
        messages = []
        for message in result['messages']:
            if not re.fullmatch(r'[0-9a-f]{20}', message['id']) or message['role'] not in ('user', 'assistant') or not isinstance(message['text'], str):
                raise BridgeError('Invalid archive message')
            part = message['part']
            if any(type(part[key]) is not int for key in ('start', 'end', 'total')) or not 0 <= part['start'] <= part['end'] <= part['total'] <= MAX_RECORD or part['end'] - part['start'] != len(message['text']):
                raise BridgeError('Invalid archived message range')
            messages.append({key: message[key] for key in ('id', 'role', 'phase', 'text', 'created', 'part')})
            if message.get('attachments'):
                messages[-1]['attachments'] = message['attachments']
            messages[-1]['asset_agent_id'] = host + ':archive:' + result['session'] + ':' + message['id']
        if sum(len(message['text']) for message in messages) > PAGE_CHARS:
            raise BridgeError('Archive page exceeds the display limit')
        agent = result.get('agent_id')
        if not isinstance(agent, str) or not agent.startswith(host + ':'):
            agent = None
        return {key: result[key] for key in ('session', 'title', 'runtime', 'cwd', 'generation', 'missing', 'skipped', 'earlier', 'later')} | {
            'host': host, 'host_label': self.hosts()[host], 'messages': messages, 'agent_id': agent}

    def asset(self, agent_id, name):
        if not isinstance(agent_id, str) or ':archive:' not in agent_id:
            return None
        parts = agent_id.split(':')
        if len(parts) != 4 or parts[0] != self.node or parts[1] != 'archive':
            raise BridgeError('Archived files belong to their source host')
        return self.hub.archive.asset_agent(parts[2], parts[3], name)

    def peer(self, agent_id):
        if not isinstance(agent_id, str) or ':archive:' not in agent_id:
            return None
        parts = agent_id.split(':')
        if len(parts) != 4 or parts[1] != 'archive' or parts[0] not in self.hosts():
            raise BridgeError('Unknown archive host')
        identifier(parts[2])
        if not re.fullmatch(r'[0-9a-f]{20}', parts[3]):
            raise BridgeError('Invalid archived message')
        return parts[0] if parts[0] != self.node else None
