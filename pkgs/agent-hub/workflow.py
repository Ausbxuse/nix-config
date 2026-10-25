"""A durable attention queue. Selection never creates work for an agent."""
import hashlib
import json
import math
import re
import time
import threading
import uuid

from bridge import BridgeError, handoff_state, handoff_delay


def display_title(agent):
    label = agent.get('mark', {}).get('task_label')
    if label:
        return label
    title = agent.get('title', 'Agent')
    serial = re.search(r'[-_]compact[-_](\d+)$', title, re.I)
    short = re.sub(r'[-_]compact(?:[-_]\d+)?$', '', title, flags=re.I)
    short = re.sub(r'[-_]\d{4,8}$', '', short)
    short = re.sub(r'^(?:we|codex|claude)[-_]', '', short, flags=re.I)
    short = re.sub(r'[-_]+', ' ', short).strip()
    if not short:
        return title
    short = short[0].upper() + short[1:]
    return short + (' · ' + serial[1] if serial else '')


def mark_patch(agent, value, mark):
    kind, version = value['type'], f"{agent['state']}:{agent['changed_at']}"
    if value.get('expected_state') and value['expected_state'] != version:
        raise BridgeError('This handoff changed. Refresh before taking action.')
    if kind == 'label':
        label = value.get('label', '')
        if not isinstance(label, str) or len(label) > 48 or re.search(r'[\x00-\x1f\x7f]', label):
            raise BridgeError('Use a task name of up to 48 characters on one line.')
        return {'task_label': label.strip()}
    if kind == 'unacknowledge':
        if mark.get('ack_state') != version:
            raise BridgeError('This review changed. Refresh before undoing it.')
        return {'ack_state': None}
    return {'ack_state': version} if kind == 'acknowledge' else {'focused': bool(value.get('focused', True))}


class Handoffs:
    def __init__(self, hub, clock=time.time):
        self.hub, self.clock = hub, clock
        self.lock = threading.RLock()
        with hub.db() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS handoff_marks(
                  id TEXT PRIMARY KEY, seen REAL, handled REAL, deferred_until REAL DEFAULT 0);
                CREATE TABLE IF NOT EXISTS workflow_settings(key TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS handoff_undo(token TEXT PRIMARY KEY, expires REAL, value TEXT NOT NULL);
            """)

    def priority(self):
        with self.hub.db() as db:
            row = db.execute("SELECT value FROM workflow_settings WHERE key='priority'").fetchone()
        value = json.loads(row[0]) if row else {}
        return value if value.get('until', 0) > self.clock() else {}

    def snapshot(self, agents):
        now, priority = self.clock(), self.priority()
        with self.hub.db() as db:
            marks = {row['id']: dict(row) for row in db.execute('SELECT * FROM handoff_marks')}
            observations = {row['id']: json.loads(row['value']) for row in db.execute('SELECT * FROM watch_observations')}
        items = []
        for agent in agents:
            kind = handoff_state(agent)
            delay = handoff_delay(agent)
            if (kind not in ('waiting', 'blocked', 'done', 'exited') or
                not agent.get('connection_online') or agent.get('activity_confirmed') or
                agent.get('recovery', {}).get('status') in ('retrying', 'checking', 'waiting') or
                agent.get('acknowledged') or now - agent.get('changed_at', now) < delay):
                continue
            observed = observations.get('agent:' + agent['id'])
            if kind == 'done' and observed and (
                observed.get('candidate') != 'done' or now - observed.get('since', now) < delay or
                (agent.get('activity_fingerprint') and observed.get('digest') != agent['activity_fingerprint'])
            ):
                continue
            identity = f"{agent['id']}:{kind}:{agent.get('changed_at', 0)}"
            key = hashlib.sha256(identity.encode()).hexdigest()[:24]
            mark = marks.get(key, {})
            if mark.get('handled'):
                continue
            corpus = ' '.join(str(agent.get(k, '')) for k in ('title', 'display_title', 'cwd', 'connection_label')).casefold()
            words = [w for w in re.findall(r'[\w-]+', priority.get('text', '').casefold()) if w not in {'the', 'on', 'my', 'for', 'and', 'today', 'project', 'task'}]
            preferred = bool(words and any(re.search(r'(?<!\w)' + re.escape(w) + r'(?!\w)', corpus) for w in words))
            focused = bool(agent.get('mark', {}).get('focused'))
            why = ('Your current priority.' if preferred else 'You pinned this task.' if focused else
                   'A decision is waiting.' if kind == 'waiting' else 'The agent needs help.' if kind == 'blocked' else
                   'A response is ready to review.' if kind == 'done' else 'The agent stopped.')
            items.append(dict(id=key, agent_id=agent['id'], kind=kind, title=agent.get('display_title', agent['title']),
                              host=agent['connection_label'], created=agent.get('changed_at', now),
                              version=f"{agent['state']}:{agent.get('changed_at', 0)}",
                              reason=why, seen=mark.get('seen'), deferred_until=mark.get('deferred_until', 0),
                              preferred=preferred or focused))
        items.sort(key=lambda i: (i['deferred_until'] > now, not i['preferred'],
                                 {'waiting': 0, 'blocked': 1, 'done': 2, 'exited': 3}[i['kind']], i['created'], i['id']))
        if hasattr(self.hub, 'assistant'):
            items = self.hub.assistant.enrich_handoffs(items)
        return {'items': items, 'priority': priority, 'pending': sum(i['deferred_until'] <= now for i in items)}

    def update(self, value, agents):
        with self.lock:
            return self._update(value, agents)

    def reply(self, value):
        request = value.get('request', {})
        if not isinstance(request, dict) or request.get('type') != 'send':
            raise BridgeError('Reply and next requires an immediate instruction')
        command_id = request.get('command_id', '')
        if not isinstance(command_id, str) or len(command_id) > 94:
            raise BridgeError('Invalid reply request')
        with self.lock:
            return self.hub.deliveries.execute_once('reply_' + command_id, value, lambda: self._reply(value))

    def _reply(self, value):
        request = value['request']
        item = next((i for i in self.snapshot(self.hub.snapshot()['agents'])['items'] if i['id'] == value.get('id')), None)
        if not item or item['agent_id'] != request.get('agent_id') or item['version'] != request.get('expected_state'):
            raise BridgeError('This handoff changed. Your instruction was not sent.')
        now = self.clock()
        with self.hub.db() as db:
            notices = [row[0] for row in db.execute('SELECT id FROM notifications WHERE agent_id=? AND created<=? AND handled_at IS NULL', (item['agent_id'], now))]
        result = self.hub.action(request)
        # The instruction may already have started a newer turn. Record only the
        # reviewed handoff and captured notice IDs, without acknowledging its new state.
        with self.hub.db() as db:
            db.execute('INSERT OR IGNORE INTO handoff_marks(id) VALUES(?)', (item['id'],))
            db.execute('UPDATE handoff_marks SET handled=?,seen=COALESCE(seen,?) WHERE id=?', (now, now, item['id']))
            for identity in notices:
                db.execute("UPDATE notifications SET handled_at=?,read_at=COALESCE(read_at,?),push_state=CASE WHEN push_state='pending' THEN 'handled' ELSE push_state END WHERE id=? AND handled_at IS NULL", (now, now, identity))
        return {**result, 'handled_handoff': item['id']}

    def _update(self, value, agents):
        action, now = value.get('action'), self.clock()
        if action == 'undo':
            return self.undo(value.get('token'), agents)
        if action == 'priority':
            text = value.get('text', '')
            until = value.get('until', now + 12 * 3600)
            if not isinstance(text, str) or len(text) > 160 or not self.valid_until(until, now):
                raise BridgeError('Use a short priority and a time within the next 30 days.')
            with self.hub.db() as db:
                db.execute("INSERT OR REPLACE INTO workflow_settings VALUES('priority',?)",
                           (json.dumps({'text': text.strip(), 'until': until}) if text.strip() else '{}',))
            return {'ok': True}
        item = next((i for i in self.snapshot(agents)['items'] if i['id'] == value.get('id')), None)
        if not item:
            raise BridgeError('This handoff changed. Refresh before taking action.')
        if action not in ('seen', 'handled', 'later', 'restore'):
            raise BridgeError('Unknown handoff action.')
        until = value.get('until', now + 1800)
        if action == 'later' and not self.valid_until(until, now):
            raise BridgeError('Choose a time within the next 30 days.')
        with self.hub.db() as db:
            previous = db.execute('SELECT * FROM handoff_marks WHERE id=?', (item['id'],)).fetchone()
            notices = [row['id'] for row in db.execute('SELECT id FROM notifications WHERE agent_id=? AND created<=? AND handled_at IS NULL', (item['agent_id'], now))] if action == 'handled' else []
        receipt = {'item': item, 'action': action, 'at': now, 'until': until,
                   'previous': dict(previous) if previous else {}, 'notifications': notices}
        if action == 'handled':
            # Mark only the observed turn. A concurrent newer turn stays pending.
            self.hub.action({'type': 'acknowledge', 'agent_id': item['agent_id'],
                             'expected_state': item['version']})
        with self.hub.db() as db:
            db.execute('INSERT OR IGNORE INTO handoff_marks(id) VALUES(?)', (item['id'],))
            field, value = {'seen': ('seen', now), 'handled': ('handled', now),
                            'later': ('deferred_until', until), 'restore': ('deferred_until', 0)}[action]
            db.execute(f'UPDATE handoff_marks SET {field}=? WHERE id=?', (value, item['id']))
            if action == 'seen':
                db.execute('UPDATE notifications SET read_at=COALESCE(read_at,?) WHERE agent_id=? AND created<=?', (now, item['agent_id'], now))
            if action == 'handled':
                db.execute("UPDATE notifications SET handled_at=?,read_at=COALESCE(read_at,?),push_state=CASE WHEN push_state='pending' THEN 'handled' ELSE push_state END WHERE agent_id=? AND created<=? AND handled_at IS NULL",
                           (now, now, item['agent_id'], now))
            db.execute('DELETE FROM handoff_undo WHERE expires<?', (now,))
            token = uuid.uuid4().hex if action in ('handled', 'later') else ''
            if token:
                db.execute('INSERT INTO handoff_undo VALUES(?,?,?)', (token, now + 60, json.dumps(receipt)))
        return {'ok': True, **({'undo': token} if token else {})}

    def undo(self, token, agents):
        now = self.clock()
        with self.hub.db() as db:
            row = db.execute('SELECT * FROM handoff_undo WHERE token=? AND expires>=?', (token, now)).fetchone()
            if not row:
                raise BridgeError('Undo expired. The current handoff has not changed.')
            receipt = json.loads(row['value'])
            item, action = receipt['item'], receipt['action']
            mark = db.execute('SELECT * FROM handoff_marks WHERE id=?', (item['id'],)).fetchone()
        agent = next((a for a in agents if a['id'] == item['agent_id']), None)
        field, expected = ('handled', receipt['at']) if action == 'handled' else ('deferred_until', receipt['until'])
        if not agent or f"{agent['state']}:{agent.get('changed_at', 0)}" != item['version'] or not mark or mark[field] != expected:
            raise BridgeError('This task changed. Its new work has not been changed.')
        if action == 'handled':
            self.hub.action({'type': 'unacknowledge', 'agent_id': item['agent_id'], 'expected_state': item['version']})
        with self.hub.db() as db:
            db.execute(f'UPDATE handoff_marks SET {field}=? WHERE id=?', (receipt['previous'].get(field, 0 if action == 'later' else None), item['id']))
            # Keep notices read and never re-send their old push notification.
            for identity in receipt['notifications']:
                db.execute('UPDATE notifications SET handled_at=NULL WHERE id=? AND handled_at=?', (identity, receipt['at']))
            db.execute('DELETE FROM handoff_undo WHERE token=?', (token,))
        return {'ok': True, 'agent_id': item['agent_id']}

    @staticmethod
    def valid_until(value, now):
        return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and now < value <= now + 30 * 86400


def action_requested(query):
    """Recognize control-shaped requests for shortcuts, not action authorization."""
    text = query.strip().casefold()
    text = re.sub(r"^(?:(?:aria|supervisor)[, :]\s*|please[, ]*\s+|(?:can|could|would) you\s+|i (?:want|need|would like) (?:you )?to\s+|i'd like (?:you )?to\s+|let's\s+)+", '', text)
    if re.match(r'(?:tell|send|show|give) me\b|(?:repeat|continue) (?:your |the )?(?:summary|answer|explanation|report)\b', text):
        return False
    return bool(re.match(r'(?:tell|send|relay|give|ask|instruct|have|launch|start|run|repeat|pause|stop|interrupt|resume|continue|assign|queue|enqueue)\b', text)
                or re.match(r'(?:add|put)\b.{0,120}\b(?:in|on|to)\b.{0,60}\bqueue\b', text))


def agent_lookup_requested(query):
    if action_requested(query):
        return False
    if agent_navigation_requested(query):
        return True
    # Finding someone to do new work must reach the general coordinator,
    # not the answer-only lookup shortcut.
    if re.search(r'\b(?:find|locate|choose|pick)\b.{0,80}\bagent\b\s+to\b', query, re.I):
        return False
    return bool(re.search(
        r"\b(?:which|what)\s+(?:agent|session|window)\b|\bwho(?:'s|\s+is)\b|"
        r"\b(?:find|locate|identify|look for|search for)\b.{0,80}\b(?:agent|session|window)\b",
        query, re.I))


def explicit_agent_choice(query, candidates):
    """Resolve only exact displayed choices; task descriptions still need evidence."""
    text = query.strip().casefold().rstrip('.!?').strip()
    ordinal = re.fullmatch(r'(?:the |option |number |choice )?(first|second|third|fourth|fifth|sixth|[1-6])(?: one| agent| option)?', text)
    if ordinal:
        word = ordinal[1]
        index = int(word) - 1 if word.isdigit() else ['first', 'second', 'third', 'fourth', 'fifth', 'sixth'].index(word)
        return candidates[index]['agent_id'] if index < len(candidates) else ''
    locator = re.sub(r'^(?:(?:the )?(?:agent|one) (?:on|from) |on |from )', '', text)
    matches = [c['agent_id'] for c in candidates if locator in (c['title'].casefold(), c['host'].casefold())]
    return matches[0] if len(matches) == 1 else ''


def agent_navigation_requested(query):
    """An explicit conversation switch, never an instruction to the agent."""
    text = query.strip().casefold()
    text = re.sub(r"^(?:(?:aria|supervisor)[, :]\s*|please[, ]*\s+|(?:can|could|would) you\s+|i (?:want|need|would like) (?:you )?to\s+|i'd like (?:you )?to\s+)+", '', text)
    return bool(re.match(
        r"(?:(?:let me )?(?:talk|speak|chat) (?:to|with)|switch(?: me)? to|"
        r"(?:take|connect) me to|(?:open|show)(?: me)? (?:the )?(?:agent|session|conversation|window))\s+\S", text))


def exact_navigation_target(query, agents):
    """Only whole, explicitly named destinations. Descriptions never become aliases."""
    if not agent_navigation_requested(query) or action_requested(query):
        return ''
    text = ' '.join(query.strip().casefold().split())
    text = re.sub(r"^(?:(?:aria|supervisor)[, :]\s*|please[, ]*\s+|(?:can|could|would) you\s+|i (?:want|need|would like) (?:you )?to\s+|i'd like (?:you )?to\s+)+", '', text)
    match = re.fullmatch(r"(?:(?:let me )?(?:talk|speak|chat) (?:to|with)|switch(?: me)? to|(?:take|connect) me to|(?:open|show)(?: me)? (?:the )?(?:agent|session|conversation|window))\s+(.+)", text)
    if not match:
        return ''
    destination = match[1].rstrip('.!?').strip()
    destinations = {destination, re.sub(r'^(?:the )?(?:agent|session|conversation|window)(?: named| called)?\s+', '', destination)}
    destinations |= {value.strip('"\x27`') for value in destinations}
    matches = set()
    for agent in agents:
        aliases = {' '.join(value.casefold().split()) for value in (agent.get('id', ''), agent.get('title', ''), agent.get('display_title') or display_title(agent)) if value}
        hosts = {str(agent.get(key, '')).casefold() for key in ('connection_label', 'connection_id')} - {''}
        qualified = aliases | {f'{alias} on {host}' for alias in aliases for host in hosts} | {f'{alias} ({host})' for alias in aliases for host in hosts}
        if destinations & qualified:
            matches.add(agent['id'])
    return next(iter(matches)) if len(matches) == 1 else ''


def request_scope(query, agents, selected):
    """Read only the selected task unless the user explicitly asks more broadly."""
    if agent_lookup_requested(query):
        # Finding a task crosses conversations, without loading unrelated plans.
        return agents, False
    broad = bool(re.search(r"\b(all (?:hosts|agents|projects)|across|brief me|my (?:plans|focus|projects|research|studies)|priorities|overview|catch me up|what(?:'s| is) (?:going on|happening)|what am i (?:working|focusing) on)\b", query, re.I))
    if broad:
        # Context selection must not depend on the drawer's fixed window order.
        ordered = sorted(agents, key=lambda a: not (a.get('needs_attention') and not a.get('acknowledged')))
        return ordered[:12], True
    matches = [a for a in agents if any(name and name.casefold() in query.casefold() for name in (a['title'], a.get('display_title')))]
    if not matches:
        matches = [a for a in agents if a['id'] == selected]
    return matches[:3], False
