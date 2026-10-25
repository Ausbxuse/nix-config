"""Small, durable learning cards derived from visible archived responses.

The archive is evidence, never instructions. Reading this view does no model or
network work. Collection and optional summaries run in a bounded background job;
manual edits, hidden entries, and offline-host evidence remain in the local DB.
"""
import hashlib
import json
import logging
import math
import os
from pathlib import Path
import re
import shutil
import sqlite3
import subprocess
import threading
import time
from urllib.parse import urlencode, urlsplit, unquote

from bridge import BridgeError
from spoken_reply import unique_object, READ_ONLY_CONFIG

ROOT = Path(__file__).resolve().parent
MAX_CALLS = 6
MODEL_BATCH = 6
PAPER_HOSTS = {'arxiv.org', 'export.arxiv.org', 'openreview.net', 'proceedings.mlr.press',
               'proceedings.neurips.cc', 'papers.nips.cc', 'doi.org', 'dx.doi.org', 'dl.acm.org',
               'ieeexplore.ieee.org', 'pubmed.ncbi.nlm.nih.gov', 'nature.com', 'science.org'}
TOPIC = re.compile(r'\b(?:robot(?:ics|ic|s)?|reinforcement learning|imitation learning|diffusion polic(?:y|ies)|'
                   r'behavior(?:al)? cloning|behaviour(?:al)? cloning|world models?|vision.language.action|'
                   r'offline (?:RL|reinforcement)|policy (?:learning|distillation|gradient)|sim.to.real|'
                   r'ablation|quantile|stdmean|meanstd|NeMo|VLA|LAP|dexterous|manipulation)\b', re.I)
FINDING = re.compile(r'\b(?:result|found|show|suggest|learn|because|means|trade.off|improv|reduc|outperform|'
                     r'comparison|baseline|experiment|limitation|evidence|success|accuracy|error|hypothesis|evaluation)', re.I)
LESSON = re.compile(r'\b(?:because|means|suggest|lesson|learn|trade.off|limitation|pitfall|caution|depends on|in contrast)\b', re.I)
URL = re.compile(r'https?://[^\s<>\[\]"`]+')
MARKDOWN_LINK = re.compile(r'\[([^\]\n]{1,200})\]\((https?://[^\s)]+)\)')


def hashed(value, length=24):
    return hashlib.sha256(value.encode()).hexdigest()[:length]


def plain(text):
    text = MARKDOWN_LINK.sub(lambda match: match[1], text)
    return re.sub(r'\s+', ' ', re.sub(r'^[\s#>*+\-\d.)]+|[`*_]', '', text)).strip()


def clipped(text, limit):
    text = text.strip()
    if len(text) <= limit:
        return text
    end = text.rfind(' ', 0, limit - 1)
    return text[:end if end > limit // 2 else limit - 1].rstrip() + '…'


def paper_identity(url):
    parsed = urlsplit(url)
    host = (parsed.hostname or '').removeprefix('www.').lower()
    path = unquote(parsed.path).strip('/')
    if host in ('arxiv.org', 'export.arxiv.org'):
        path = re.sub(r'^(?:abs|pdf)/', '', path)
        return 'arxiv:' + re.sub(r'v\d+$', '', path.removesuffix('.pdf')).lower()
    if host in ('doi.org', 'dx.doi.org'):
        return 'doi:' + path.lower()
    if host == 'openreview.net':
        from urllib.parse import parse_qs
        identity = parse_qs(parsed.query).get('id', [''])[0]
        if identity:
            return 'openreview:' + identity
    return host + '/' + path + ('?' + parsed.query if parsed.query else '')


def paper_links(text):
    labels = {match[2].rstrip('.,;'): plain(match[1]) for match in MARKDOWN_LINK.finditer(text)}
    result, seen = [], set()
    for match in URL.finditer(text):
        url = match[0].rstrip(').,;:')
        try:
            parsed = urlsplit(url)
        except ValueError:
            continue
        host = (parsed.hostname or '').removeprefix('www.')
        if host not in PAPER_HOSTS or parsed.username or parsed.password or len(url) > 2000:
            continue
        identity = paper_identity(url)
        if identity in seen:
            continue
        seen.add(identity)
        # This is only a reference present in the response, not a verified paper.
        start = text.rfind('\n\n', 0, match.start())
        start = start + 2 if start >= 0 else 0
        end = text.find('\n\n', match.end())
        paragraph = text[max(0, start):end if end >= 0 else len(text)]
        title = labels.get(url) or clipped(parsed.path.strip('/') or host, 140)
        result.append({'title': title, 'url': url, 'tldr': clipped(plain(paragraph), 260), 'key': identity})
        if len(result) == 8:
            break
    return result


def extract(source, message):
    """Conservative, zero-token first pass; all displayed claims are quotations."""
    text = message['text']
    papers = paper_links(text)
    if len(text.strip()) < 100 or not (papers or TOPIC.search(text)):
        return None
    paragraphs = [plain(part) for part in re.split(r'\n\s*\n', re.sub(r'```[^\n]*\n[\s\S]*?```', '', text))]
    paragraphs = [part for part in paragraphs if len(part) >= 40 and not part.startswith(('http://', 'https://'))]
    if not paragraphs:
        return None
    if not papers and not any(TOPIC.search(part) and FINDING.search(part) for part in paragraphs):
        return None  # Merely naming a research agent is not a research finding.
    ranked = sorted(enumerate(paragraphs), key=lambda item: (-(2 * bool(TOPIC.search(item[1])) + bool(FINDING.search(item[1]))), item[0]))
    summary = clipped(ranked[0][1], 420)
    headings = [plain(match[1]) for match in re.finditer(r'^#{1,4}\s+(.+)$', text, re.M)]
    meaningful = next((heading for heading in headings if TOPIC.search(heading) or FINDING.search(heading)), '')
    title = clipped(meaningful or re.split(r'(?<=[.!?])\s+', summary)[0], 110)
    lesson = next((clipped(part, 650) for part in paragraphs if LESSON.search(part) and part != ranked[0][1]), '')
    examples = [clipped(part, 900) for part in paragraphs if re.search(r'\b(?:for example|e\.g\.|example:|suppose|for instance)\b', part, re.I)][:2]
    host, session, mid = source['host'], source['session'], message['message_id']
    cwd = message.get('cwd') or source.get('cwd', '')
    source_url = '/?' + urlencode({'archive': session, 'host': host, 'message': mid,
                                  'generation': source['generation'], 'offset': 0})
    return {'id': hashed(host + ':' + session + ':' + mid), 'title': title, 'tldr': summary,
            'lesson': lesson, 'examples': examples, 'papers': papers, 'notes': '',
            'project': {'id': message.get('project_id') or hashed(host + ':' + cwd), 'label': Path(cwd).name or source['title'] or 'Research'},
            'created': message['created'], 'sort_time': message['sort_time'], 'summary_state': 'excerpt',
            'agent_id': source.get('agent_id'),
            'sources': [{'host': host, 'host_label': source['host_label'], 'session': session,
                         'generation': source['generation'], 'message_id': mid, 'digest': message['digest'],
                         'excerpt': text[:1800], 'created': message['created'], 'title': source['title'],
                         'offset': 0, 'url': source_url, 'missing': source.get('missing', False),
                         'stale': False, 'truncated': message.get('truncated', False)}]}


class DeferredSummary(BridgeError):
    pass


class Research:
    def __init__(self, hub, clock=time.time):
        self.hub, self.clock = hub, clock
        self.lock = threading.Lock()
        self.wake = threading.Event()
        self.thread = None
        self.collecting = False
        self.last_tick = 0
        self.host_cursor = 0
        with hub.db() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS research_entries(
                  id TEXT PRIMARY KEY,host TEXT,session TEXT,message_id TEXT,digest TEXT,
                  sort_time REAL,value TEXT NOT NULL,edits TEXT NOT NULL DEFAULT '{}',
                  hidden INTEGER NOT NULL DEFAULT 0,stale INTEGER NOT NULL DEFAULT 0,
                  attempts INTEGER NOT NULL DEFAULT 0,retry_at REAL NOT NULL DEFAULT 0);
                CREATE INDEX IF NOT EXISTS research_entry_source ON research_entries(host,session);
                CREATE INDEX IF NOT EXISTS research_entry_order ON research_entries(hidden,sort_time DESC,id DESC);
                CREATE TABLE IF NOT EXISTS research_sources(host TEXT,session TEXT,value TEXT NOT NULL,
                  position INTEGER NOT NULL DEFAULT 0,recent_position INTEGER NOT NULL DEFAULT 0,
                  floor INTEGER NOT NULL DEFAULT 0,scanned REAL NOT NULL DEFAULT 0,PRIMARY KEY(host,session));
                CREATE TABLE IF NOT EXISTS research_hosts(host TEXT PRIMARY KEY,epoch TEXT,cursor INTEGER,
                  checked REAL,error TEXT NOT NULL DEFAULT '',more INTEGER NOT NULL DEFAULT 0);
                CREATE TABLE IF NOT EXISTS research_settings(key TEXT PRIMARY KEY,value TEXT NOT NULL);
            ''')
            columns = {row[1] for row in db.execute('PRAGMA table_info(research_sources)')}
            for name, kind in (('recent_position', 'INTEGER'), ('floor', 'INTEGER'), ('scanned', 'REAL')):
                if name not in columns:
                    db.execute('ALTER TABLE research_sources ADD COLUMN ' + name + ' ' + kind + ' NOT NULL DEFAULT 0')
            if 'more' not in {row[1] for row in db.execute('PRAGMA table_info(research_hosts)')}:
                db.execute('ALTER TABLE research_hosts ADD COLUMN more INTEGER NOT NULL DEFAULT 0')

    def eligible(self):
        mesh = getattr(self.hub, 'mesh', None)
        return not mesh or (not mesh.is_worker and mesh.authority()['owner'] == mesh.node_id)

    def setting(self, key, default=None):
        with self.hub.db() as db:
            row = db.execute('SELECT value FROM research_settings WHERE key=?', (key,)).fetchone()
        return json.loads(row[0]) if row else default

    def _visible(self, row):
        item = json.loads(row['value'])
        item.pop('_evidence', None)
        edits = json.loads(row['edits'])
        item.update(edits)
        item.update(hidden=bool(row['hidden']), edited=bool(edits))
        for source in item['sources']:
            source['stale'] = bool(row['stale'])
        return item

    def snapshot(self, query='', hidden=False, project='', before=None, limit=40):
        if not isinstance(query, str) or len(query) > 240 or not isinstance(project, str) or len(project) > 100:
            raise BridgeError('Research filters are too long')
        if type(hidden) is not bool or type(limit) is not int or not 1 <= limit <= 50:
            raise BridgeError('Invalid research page')
        if before is not None and (not isinstance(before, list) or len(before) != 2 or type(before[0]) not in (int, float) or not math.isfinite(before[0]) or not isinstance(before[1], str) or not re.fullmatch(r'[0-9a-f]{24}', before[1])):
            raise BridgeError('Invalid research page cursor')
        if not self.eligible():
            return {'available': False, 'items': [], 'projects': [], 'next': None,
                    'status': {'collecting': False, 'pending': 0, 'indexing': False, 'backlog': 0, 'last_checked': 0, 'unavailable': [], 'model_calls_last_hour': 0}}
        visible = "(json_extract(value,'$.summary_state')!='skipped' OR edits!='{}')"
        clauses, args = ['hidden=?', visible], [int(hidden)]
        if project:
            clauses.append("json_extract(value,'$.project.id')=?"); args.append(project)
        if query.strip():
            for word in query.lower().split()[:12]:
                clauses.append("lower(value||edits) LIKE ? ESCAPE '\\'")
                args.append('%' + word.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_') + '%')
        if before:
            clauses.append('(sort_time<? OR (sort_time=? AND id<?))'); args += [before[0], before[0], before[1]]
        with self.hub.db() as db:
            rows = db.execute('SELECT * FROM research_entries WHERE ' + ' AND '.join(clauses) + ' ORDER BY sort_time DESC,id DESC LIMIT ?', (*args, limit + 1)).fetchall()
            projects = [dict(row) for row in db.execute("SELECT json_extract(value,'$.project.id') AS id,json_extract(value,'$.project.label') AS label,json_extract(value,'$.sources[0].host_label') AS host_label,COUNT(*) AS count FROM research_entries WHERE hidden=? AND " + visible + " GROUP BY json_extract(value,'$.project.id') ORDER BY lower(label),id LIMIT 100", (int(hidden),))]
            hosts = [dict(row) for row in db.execute('SELECT * FROM research_hosts')]
            pending = db.execute("SELECT COUNT(*) FROM research_entries WHERE hidden=0 AND stale=0 AND edits='{}' AND json_extract(value,'$.summary_state')='excerpt' AND attempts<2").fetchone()[0]
            total = db.execute('SELECT COUNT(*) FROM research_entries WHERE hidden=? AND ' + visible, (int(hidden),)).fetchone()[0]
            indexing = db.execute("SELECT COUNT(*) FROM research_sources WHERE position<floor OR recent_position<json_extract(value,'$.last_ordinal')").fetchone()[0]
        duplicate_labels = {project['label'] for project in projects if sum(other['label'] == project['label'] for other in projects) > 1}
        for project in projects:
            if project['label'] in duplicate_labels:
                project['label'] += ' · ' + project['host_label']
        items = [self._visible(row) for row in rows[:limit]]
        labels = self.hub.archives.hosts() if hasattr(self.hub, 'archives') else {}
        return {'available': True, 'items': items, 'projects': projects, 'total': total,
                'next': [items[-1]['sort_time'], items[-1]['id']] if len(rows) > limit else None,
                'status': {'collecting': self.collecting, 'last_checked': max((row['checked'] for row in hosts), default=0),
                           'unavailable': [{'host': row['host'], 'label': labels.get(row['host'], row['host']), 'error': row['error']} for row in hosts if row['error']],
                           'pending': pending, 'indexing_sources': indexing,
                           'indexing': bool(indexing or any(row['more'] for row in hosts)),
                           'backlog': indexing + sum(bool(row['more']) for row in hosts),
                           'model_calls_last_hour': len([stamp for stamp in self.setting('calls', []) if stamp > self.clock() - 3600])}}

    def update(self, value):
        if not self.eligible():
            raise BridgeError('Open the controller hub to manage research notes.')
        if not isinstance(value, dict):
            raise BridgeError('Invalid research change')
        action = value.get('action')
        if action == 'refresh':
            self.last_tick = 0
            self.wake.set()
            self.tick()
            return {'ok': True, 'status': 'collecting'}
        identity = value.get('id')
        if not isinstance(identity, str) or not re.fullmatch(r'[0-9a-f]{24}', identity):
            raise BridgeError('Invalid research entry')
        with self.hub.db() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT * FROM research_entries WHERE id=?', (identity,)).fetchone()
            if not row:
                raise BridgeError('This research entry is no longer available')
            if action == 'edit':
                changes = value.get('changes')
                lengths = {'title': 160, 'tldr': 1200, 'lesson': 1200, 'notes': 4000}
                if not isinstance(changes, dict) or not changes or set(changes) - (set(lengths) | {'examples'}):
                    raise BridgeError('Edit the title, summary, lesson, examples, or notes')
                for key, text in changes.items():
                    if key == 'examples':
                        if not isinstance(text, list) or len(text) > 6 or any(not isinstance(item, str) or len(item) > 1500 for item in text):
                            raise BridgeError('Keep at most six short examples')
                    elif not isinstance(text, str) or len(text) > lengths[key] or (key in ('title', 'tldr') and not text.strip()):
                        raise BridgeError('Keep research edits short and give the entry a title and summary')
                edits = json.loads(row['edits']) | changes
                db.execute("UPDATE research_entries SET edits=?,value=json_set(value,'$.updated',?) WHERE id=?", (json.dumps(edits), self.clock(), identity))
            elif action in ('hide', 'restore'):
                db.execute('UPDATE research_entries SET hidden=? WHERE id=?', (int(action == 'hide'), identity))
            else:
                raise BridgeError('Unknown research action')
            current = db.execute('SELECT * FROM research_entries WHERE id=?', (identity,)).fetchone()
        self._wake_clients()
        return {'ok': True, 'item': self._visible(current)}

    def _wake_clients(self):
        if hasattr(self.hub, 'live'):
            self.hub.live.wake()

    def _host(self, host):
        with self.hub.db() as db:
            saved = db.execute('SELECT * FROM research_hosts WHERE host=?', (host,)).fetchone()
        after = saved['cursor'] if saved else 0
        result = self.hub.archives.research_sources(host, after=after)
        if saved and result['epoch'] != saved['epoch']:
            result = self.hub.archives.research_sources(host, after=0)
            with self.hub.db() as db:
                db.execute('DELETE FROM research_sources WHERE host=?', (host,))
                db.execute('UPDATE research_entries SET stale=1 WHERE host=?', (host,))
        with self.hub.db() as db:
            for source in result['items']:
                old = db.execute('SELECT * FROM research_sources WHERE host=? AND session=?', (host, source['session'])).fetchone()
                previous = json.loads(old['value']) if old else None
                same = previous and previous['generation'] == source['generation']
                if previous and not same:
                    db.execute('UPDATE research_entries SET stale=1 WHERE host=? AND session=?', (host, source['session']))
                floor = max(0, source['last_ordinal'] - 48)
                db.execute('INSERT OR REPLACE INTO research_sources(host,session,value,position,recent_position,floor,scanned) VALUES(?,?,?,?,?,?,?)',
                           (host, source['session'], json.dumps(source), old['position'] if same else 0,
                            old['recent_position'] if same else floor, old['floor'] if same else floor,
                            old['scanned'] if old else 0))
                # Saved evidence remains readable when its original log is gone.
                db.execute("UPDATE research_entries SET value=json_set(value,'$.sources[0].missing',json(?)) WHERE host=? AND session=?", (json.dumps(source.get('missing') is True), host, source['session']))
            db.execute('INSERT OR REPLACE INTO research_hosts VALUES(?,?,?,?,?,?)', (host, result['epoch'], result['next'], self.clock(), '', int(result['more'])))

    def _page(self, row):
        source = json.loads(row['value'])
        recent = row['recent_position'] < source['last_ordinal']
        position = row['recent_position'] if recent else row['position']
        page = self.hub.archives.research_evidence(source['host'], source['session'], source['generation'], after=position)
        with self.hub.db() as db:
            for message in page['items']:
                card = extract(source, message)
                if not card:
                    continue
                old = db.execute('SELECT digest,value FROM research_entries WHERE id=?', (card['id'],)).fetchone()
                # Keep the source body bounded and private for later summaries.
                card['_evidence'] = message['text']
                card['updated'] = self.clock()
                if old and old['digest'] == message['digest']:
                    cached = json.loads(old['value'])
                    card = cached | {'sources': card['sources'], 'project': card['project'], 'agent_id': card['agent_id']}
                db.execute('INSERT INTO research_entries(id,host,session,message_id,digest,sort_time,value) VALUES(?,?,?,?,?,?,?) '
                           'ON CONFLICT(id) DO UPDATE SET digest=excluded.digest,value=excluded.value,sort_time=excluded.sort_time,stale=0,'
                           'attempts=CASE WHEN research_entries.digest=excluded.digest THEN research_entries.attempts ELSE 0 END',
                           (card['id'], source['host'], source['session'], message['message_id'], message['digest'], message['sort_time'], json.dumps(card)))
            field = 'recent_position' if recent else 'position'
            position = page['next'] if recent else min(row['floor'], page['next'])
            db.execute('UPDATE research_sources SET ' + field + '=?,scanned=? WHERE host=? AND session=?', (position, self.clock(), source['host'], source['session']))
        self._wake_clients()

    def collect(self):
        """One bounded cycle. Tests can call this synchronously; UI calls tick."""
        if not self.eligible() or getattr(self.hub, 'supervisor_busy', False):
            return
        hosts = self.hub.archives.hosts()
        ordered = list(hosts)
        selected = (ordered[self.host_cursor:] + ordered[:self.host_cursor])[:16]
        self.host_cursor = (self.host_cursor + len(selected)) % max(1, len(ordered))
        with self.hub.db() as db:
            for row in db.execute('SELECT host FROM research_hosts').fetchall():
                if row['host'] not in hosts:
                    db.execute("UPDATE research_hosts SET error='Source host is no longer configured. Saved research is still available.' WHERE host=?", (row['host'],))
        for host in selected:
            if self.hub.stop.is_set() or self.hub.supervisor_busy:
                return
            try:
                self._host(host)
            except (BridgeError, OSError, ValueError, TypeError, KeyError, sqlite3.Error):
                with self.hub.db() as db:
                    db.execute("INSERT INTO research_hosts(host,epoch,cursor,checked,error) VALUES(?,'',0,?,?) ON CONFLICT(host) DO UPDATE SET checked=excluded.checked,error=excluded.error",
                               (host, self.clock(), 'Host unavailable. Saved research is still available.'))
        with self.hub.db() as db:
            pending = db.execute("SELECT s.* FROM research_sources s JOIN research_hosts h ON h.host=s.host WHERE h.error='' AND (position<floor OR recent_position<json_extract(value,'$.last_ordinal')) ORDER BY scanned,json_extract(value,'$.updated') DESC LIMIT 6").fetchall()
        for row in pending:
            if self.hub.stop.is_set() or self.hub.supervisor_busy:
                return
            try:
                self._page(row)
            except (BridgeError, OSError, ValueError, TypeError, KeyError, sqlite3.Error):
                with self.hub.db() as db:
                    db.execute('UPDATE research_sources SET scanned=? WHERE host=? AND session=?', (self.clock(), row['host'], row['session']))
                continue  # A changed generation is picked up by the next manifest.
        if not self.hub.supervisor_busy and not self.hub.stop.is_set():
            self.summarize()

    def model(self, evidence):
        if self.hub.supervisor_busy or self.hub.stop.is_set():
            raise DeferredSummary('Interactive supervisor has priority.')
        executable = shutil.which(getattr(self.hub, 'provider_executable', '') or getattr(self.hub, 'provider', ''))
        if getattr(self.hub, 'provider', None) != 'codex' or not executable:
            raise BridgeError('Research summaries require the configured supervisor.')
        with self.hub.db() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute("SELECT value FROM research_settings WHERE key='calls'").fetchone()
            calls = [stamp for stamp in (json.loads(row[0]) if row else []) if stamp > self.clock() - 3600]
            if len(calls) >= MAX_CALLS:
                raise BridgeError('Research summary budget reached.')
            db.execute("INSERT OR REPLACE INTO research_settings VALUES('calls',?)", (json.dumps(calls + [self.clock()]),))
        command = [executable, 'exec', '--model', self.hub.supervisor_model, '--ignore-user-config', '--skip-git-repo-check',
                   '--ephemeral', '--sandbox', 'read-only', '--json', '--output-schema', str(ROOT / 'research-schema.json'),
                   '-c', 'model_reasoning_effort="low"', '-c', 'notify=[]', '-c', 'features.apps=false', '-c', 'features.multi_agent=false',
                   '-c', 'features.shell_tool=false', '-c', 'tools.view_image=false', '-c', 'web_search="disabled"',
                   '-c', 'features.apply_patch_freeform=false', '-c', 'features.code_mode=false', '-c', 'skills.max_context_tokens=1',
                   '-c', 'project_doc_max_bytes=0', '-c', 'features.hooks=false',
                   '-c', 'model_instructions_file=' + json.dumps(str(ROOT / 'research-instructions.txt')), '-']
        command[2:2] = [part for setting in READ_ONLY_CONFIG for part in ('-c', setting)]
        env = {key: value for key, value in os.environ.items() if key not in ('TMUX', 'TMUX_PANE')}
        process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                   text=True, cwd=self.hub.state_dir, env=env, start_new_session=True)
        with self.hub.lock:
            self.hub.processes.add(process)
        try:
            deadline = time.monotonic() + 45
            pending = json.dumps(evidence, ensure_ascii=False)
            while True:
                if self.hub.supervisor_busy or self.hub.stop.is_set():
                    process.kill(); process.communicate()
                    raise DeferredSummary('Interactive supervisor has priority.')
                try:
                    stdout, _ = process.communicate(pending, timeout=.5)
                    break
                except subprocess.TimeoutExpired:
                    pending = None
                    if time.monotonic() >= deadline:
                        raise
            if process.returncode or len(stdout) > 200000:
                raise BridgeError('Research summary unavailable.')
            for line in reversed(stdout.splitlines()):
                try:
                    event = json.loads(line, object_pairs_hook=unique_object)
                    if event.get('type') == 'item.completed' and event.get('item', {}).get('type') == 'agent_message':
                        value = json.loads(event['item']['text'], object_pairs_hook=unique_object)
                        if isinstance(value, dict) and set(value) == {'items'}:
                            return value
                except (ValueError, TypeError, AttributeError, KeyError):
                    continue
            raise BridgeError('Research summary did not return valid cards.')
        except subprocess.TimeoutExpired as exc:
            process.kill(); process.communicate()
            raise BridgeError('Research summary timed out.') from exc
        finally:
            with self.hub.lock:
                self.hub.processes.discard(process)

    def summarize(self):
        if len([stamp for stamp in self.setting('calls', []) if stamp > self.clock() - 3600]) >= MAX_CALLS:
            return
        with self.hub.db() as db:
            rows = db.execute("SELECT * FROM research_entries WHERE hidden=0 AND stale=0 AND edits='{}' AND attempts<2 AND retry_at<=? AND json_extract(value,'$.summary_state')='excerpt' ORDER BY (json_array_length(value,'$.papers')>0) DESC,sort_time DESC LIMIT ?", (self.clock(), MODEL_BATCH)).fetchall()
        if not rows:
            return
        cards = {row['id']: json.loads(row['value']) for row in rows}
        evidence = {'items': [{'id': card['id'], 'project': card['project']['label'], 'text': card['_evidence'][:4000]}
                              for card in cards.values()]}
        try:
            result = self.model(evidence)
            items = result.get('items')
            if not isinstance(items, list) or len(items) > len(cards):
                raise BridgeError('Invalid research summary batch')
            accepted = {}
            for item in items:
                if not isinstance(item, dict) or item.get('id') not in cards or item['id'] in accepted:
                    raise BridgeError('Invalid research summary source')
                lengths = {'title': 160, 'tldr': 900, 'lesson': 900}
                if any(not isinstance(item.get(key), str) or len(item[key]) > maximum for key, maximum in lengths.items()) or not item['title'].strip() or not item['tldr'].strip():
                    raise BridgeError('Research summary exceeds its display limit')
                if not isinstance(item.get('examples'), list) or len(item['examples']) > 3 or any(not isinstance(text, str) or len(text) > 1000 for text in item['examples']):
                    raise BridgeError('Invalid research examples')
                # Quotes make the proposed lesson auditable and must occur in
                # the exact, bounded source supplied to this model call.
                quotes = item.get('evidence_quotes')
                body = cards[item['id']]['_evidence'][:4000]
                if not isinstance(quotes, list) or not 1 <= len(quotes) <= 4 or any(not isinstance(quote, str) or not 12 <= len(quote) <= 500 or quote not in body for quote in quotes):
                    raise BridgeError('Research summary has no matching source evidence')
                accepted[item['id']] = item
            with self.hub.db() as db:
                for row in rows:
                    fresh = db.execute('SELECT * FROM research_entries WHERE id=?', (row['id'],)).fetchone()
                    if not fresh or fresh['digest'] != row['digest'] or fresh['hidden'] or fresh['stale'] or fresh['edits'] != '{}':
                        continue
                    item = accepted.get(row['id'])
                    card = json.loads(fresh['value'])
                    if item:
                        card.update({key: item[key] for key in ('title', 'tldr', 'lesson', 'examples')})
                        card.update(summary_state='ready', updated=self.clock(), evidence_quotes=item['evidence_quotes'])
                    else:
                        card.update(summary_state='skipped', updated=self.clock())
                    db.execute('UPDATE research_entries SET value=?,attempts=attempts+1,retry_at=? WHERE id=?',
                               (json.dumps(card), self.clock() + 600, row['id']))
            self._wake_clients()
        except DeferredSummary:
            return
        except (BridgeError, OSError, ValueError, TypeError, KeyError):
            with self.hub.db() as db:
                for row in rows:
                    db.execute('UPDATE research_entries SET attempts=attempts+1,retry_at=? WHERE id=? AND digest=?', (self.clock() + 600, row['id'], row['digest']))

    def tick(self, background=True):
        if not self.eligible() or self.clock() - self.last_tick < 20 or not self.lock.acquire(blocking=False):
            return
        self.last_tick, self.collecting = self.clock(), True
        def work():
            try:
                self.collect()
            except Exception:
                logging.exception('Research collection will retry')
            finally:
                self.collecting = False
                self.lock.release()
        if background:
            threading.Thread(target=work, name='hub-research-collect', daemon=True).start()
        else:
            work()

    def start(self):
        if self.thread and self.thread.is_alive():
            return
        def watch():
            while not self.hub.stop.is_set():
                self.tick()
                self.wake.wait(30)
                self.wake.clear()
        self.thread = threading.Thread(target=watch, name='hub-research', daemon=True)
        self.thread.start()
