"""Durable observation and push outbox, independent of browser lifetime."""
import hashlib
import json
import re
import secrets
import threading
import time
import urllib.error
import urllib.request
from urllib.parse import urlparse

import bridge
from federation import NoRedirect

SEEN_GRACE = 1.25  # Allow the visible-reply receipt (700 ms) to beat phone push.


def meaningful(text):
    lines = []
    for line in bridge.terminal_content(text).splitlines():
        if re.search(r"esc to interrupt|tokens\)|^\s*Worked for |context left|← for agents|^\s*[›❯]\s*$", line):
            continue
        lines.append(line.rstrip())
    return "\n".join(lines).strip()


def excerpt(text):
    # Terminal content is evidence, not instructions. Only visible assistant prose
    # is used in the inbox. Push previews carry names and hosts, never prose.
    text = bridge.terminal_content(text or "")
    matches = list(re.finditer(r"^[•●] (.+)", text, re.M))
    for match in reversed(matches):
        if re.match(r"Ran |Called |Explored|Edited |Searched|Working|Queued", match[1]):
            continue
        body = (text or "")[match.start():].split("\n›", 1)[0]
        return re.sub(r"\s+", " ", body.lstrip("•● "))[:700]
    return ""


def short_update(kind, body):
    """Keep alerts brief; full output remains in the agent reader and database."""
    actions = {
        "waiting": "Open the agent and provide the requested input.",
        "blocked": "Open the agent to check what prevents progress.",
        "done": "The turn ended. Review the response.",
        "exited": "The agent is no longer running or visible. Check its last output.",
        "quiet": "No new output for 3 minutes. The agent may still be working.",
        "review": "Open Agents to review pending decisions and responses.",
    }
    if kind in actions:
        return actions[kind]
    text = re.sub(r"^Agent output:\s*", "", body or "")
    text = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", text)
    text = re.sub(r"[`*#]", "", text)
    text = re.sub(r"\s+", " ", text).strip()
    text = " ".join(re.split(r"(?<=[.!?])\s+", text)[:2])
    words = text.split()
    if len(words) > 22:
        text = " ".join(words[:22]).rstrip(".,;:") + "…"
    if len(text) > 150:
        text = text[:147].rsplit(" ", 1)[0].rstrip(".,;:") + "…"
    return ("Agent reports: " + text) if kind == "progress" and text else text


def push_agent(agent):
    def label(value, limit):
        return value if len(value) <= limit else value[:limit - 1] + '…'
    return {"name": label(agent.get("display_title") or agent.get("title") or "Agent", 96),
            "host": label(agent.get("connection_label") or agent.get("connection_id") or "", 48)}


def observation_identity(agent):
    """Bounded evidence for the exact handoff, without conversation content."""
    evidence = {}
    if agent.get('activity_fingerprint'):
        evidence['activity_fingerprint'] = agent['activity_fingerprint']
    turn = agent.get('turn_status') or {}
    if turn.get('turn_id'):
        evidence['turn_status'] = {key: turn.get(key) for key in ('turn_id', 'state', 'at')}
    return evidence


def same_observation(expected, actual):
    current = observation_identity(actual)
    return all(current.get(key) == value for key, value in expected.items())


class Watcher:
    def __init__(self, hub, config=None, clock=time.time):
        self.hub, self.config, self.clock = hub, config or {}, clock
        self.checked = 0
        self.error = ""
        self.push_error = ""
        self.started = clock()
        self.thread = None
        self.push_thread = None
        self.wake = threading.Event()
        self.push_wake = threading.Event()
        self.push_lock = threading.Lock()
        self.push_status_lock = threading.Lock()
        self.push_status = {"checked_at": 0, "subscribers": None, "last_accepted_at": 0, "error": ""}
        self.push_status_attempt = 0
        self.relay = self.config.get("push_relay", {})
        with hub.db() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS watch_observations(id TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS watch_settings(key TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS handoff_attention(
                  id TEXT PRIMARY KEY, seen_at REAL, heard_at REAL);
                CREATE TABLE IF NOT EXISTS handoff_marks(
                  id TEXT PRIMARY KEY, seen REAL, handled REAL, deferred_until REAL DEFAULT 0);
                CREATE TABLE IF NOT EXISTS push_batches(id TEXT PRIMARY KEY, payload TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS notifications(
                  id TEXT PRIMARY KEY, dedupe TEXT UNIQUE NOT NULL, kind TEXT NOT NULL,
                  title TEXT NOT NULL, body TEXT NOT NULL, agent_id TEXT NOT NULL,
                  created REAL NOT NULL, read_at REAL, push_state TEXT NOT NULL DEFAULT 'pending',
                  push_batch TEXT NOT NULL DEFAULT '',
                  attempts INTEGER NOT NULL DEFAULT 0, next_attempt REAL NOT NULL DEFAULT 0);
            """)
            if "push_batch" not in {row[1] for row in db.execute("PRAGMA table_info(notifications)")}:
                db.execute("ALTER TABLE notifications ADD COLUMN push_batch TEXT NOT NULL DEFAULT ''")
            if "handled_at" not in {row[1] for row in db.execute("PRAGMA table_info(notifications)")}:
                db.execute("ALTER TABLE notifications ADD COLUMN handled_at REAL")
            if "target" not in {row[1] for row in db.execute("PRAGMA table_info(notifications)")}:
                db.execute("ALTER TABLE notifications ADD COLUMN target TEXT NOT NULL DEFAULT '{}'")
            for column in ('push_started_at', 'push_accepted_at'):
                if column not in {row[1] for row in db.execute("PRAGMA table_info(notifications)")}:
                    db.execute(f'ALTER TABLE notifications ADD COLUMN {column} REAL')
            db.execute("CREATE INDEX IF NOT EXISTS notification_handoff ON notifications(agent_id,json_extract(target,'$.handoff_id'))")
            # Do not deliver output-only alerts left in the outbox by older builds.
            # Keep their history and read/handled state intact.
            db.execute("UPDATE notifications SET push_state='suppressed' WHERE kind IN ('progress','quiet') AND push_state='pending'")
            db.execute("UPDATE notifications SET push_state='suppressed' WHERE dedupe='watch-initial' AND push_state='pending'")
        self.enabled = self.setting("enabled", "true") == "true"

    def setting(self, key, default=""):
        with self.hub.db() as db:
            row = db.execute("SELECT value FROM watch_settings WHERE key=?", (key,)).fetchone()
        return row[0] if row else default

    def eligible(self):
        mesh = self.hub.mesh
        return not mesh or (not mesh.is_worker and mesh.authority()["owner"] == mesh.node_id)

    def state(self):
        with self.hub.db() as db:
            rows = [dict(row) for row in db.execute("SELECT * FROM (SELECT id,kind,title,body,agent_id,created,read_at,handled_at,target FROM notifications WHERE handled_at IS NULL ORDER BY created DESC LIMIT 60) UNION ALL SELECT * FROM (SELECT id,kind,title,body,agent_id,created,read_at,handled_at,target FROM notifications WHERE handled_at IS NOT NULL ORDER BY created DESC LIMIT 60) ORDER BY created DESC")]
            unread = db.execute("SELECT count(*) FROM notifications WHERE read_at IS NULL").fetchone()[0]
            unhandled = db.execute("SELECT count(*) FROM notifications WHERE handled_at IS NULL").fetchone()[0]
            handled = db.execute("SELECT count(*) FROM notifications WHERE handled_at IS NOT NULL").fetchone()[0]
            pending = db.execute("SELECT count(*) FROM notifications WHERE push_state='pending'").fetchone()[0]
            latest = db.execute('SELECT created,push_state,read_at,handled_at,push_started_at,push_accepted_at FROM notifications ORDER BY created DESC LIMIT 1').fetchone()
        for row in rows:
            row["body"] = short_update(row["kind"], row["body"])
            row['target'] = json.loads(row['target'])
        if hasattr(self.hub, 'assistant'):
            rows = self.hub.assistant.decorate(rows)
        return {"enabled": self.enabled, "eligible": self.eligible(), "checked_at": self.checked,
                "error": self.error, "push_error": self.push_error, "unread": unread, "unhandled": unhandled, "handled": handled,
                "push_available": bool(self.relay) and self.eligible(), "push_pending": pending,
                "push_status": dict(self.push_status), "push_latest": dict(latest) if latest else None, "items": rows}

    def update(self, value):
        if 'attention' in value:
            self.attended(value['attention'])
        if "enabled" in value:
            if not isinstance(value["enabled"], bool):
                raise bridge.BridgeError("Invalid watching preference")
            self.enabled = value["enabled"]
            with self.hub.db() as db:
                db.execute("INSERT OR REPLACE INTO watch_settings VALUES('enabled',?)", (json.dumps(self.enabled),))
        if "read" in value:
            ids = value["read"]
            if not isinstance(ids, list) or len(ids) > 200 or any(not isinstance(item, str) for item in ids):
                raise bridge.BridgeError("Invalid update IDs")
            with self.hub.db() as db:
                db.executemany("UPDATE notifications SET read_at=?,push_state=CASE WHEN push_state='pending' AND push_batch='' THEN 'observed' ELSE push_state END WHERE id=?", [(self.clock(), key) for key in ids])
        for field in ("handle", "reopen"):
            if field not in value:
                continue
            ids = value[field]
            if not isinstance(ids, list) or len(ids) > 200 or any(not isinstance(item, str) for item in ids):
                raise bridge.BridgeError("Invalid update IDs")
            with self.hub.db() as db:
                if field == "handle":
                    db.executemany("UPDATE notifications SET handled_at=?,read_at=COALESCE(read_at,?),push_state=CASE WHEN push_state='pending' THEN 'handled' ELSE push_state END WHERE id=?",
                                   [(self.clock(), self.clock(), key) for key in ids])
                else:
                    db.executemany("UPDATE notifications SET handled_at=NULL WHERE id=?", [(key,) for key in ids])
        return {"ok": True}

    def verified_preview(self, agent, kind):
        if not hasattr(self.hub, 'preview'):
            return {}
        try:
            # Notification decisions cannot share a read that began before a
            # queued/new turn. The host also bypasses its runtime-status cache.
            preview = self.hub.preview(agent['id'], history=True, fresh=True)
            observation = preview.get('observation')
            if (isinstance(observation, dict) and bridge.handoff_state({**agent, **observation}) == kind and
                    same_observation(observation_identity(agent), observation)):
                return preview
        except (bridge.BridgeError, OSError, ValueError, KeyError, TypeError):
            pass
        return None

    def target(self, agent, preview=None):
        kind = agent['state'] if agent.get('live', True) else 'exited'
        version = f"{agent['state']}:{agent.get('changed_at', 0)}"
        identity = f"{agent['id']}:{kind}:{agent.get('changed_at', 0)}"
        target = {'version': version, 'handoff_id': hashlib.sha256(identity.encode()).hexdigest()[:24],
                  'agents': [push_agent(agent)], 'observation': observation_identity(agent)}
        # Read source identity only at a settled handoff, never on each watch tick.
        # A changed/unsupported preview leaves an exact event link without claiming
        # that an unrelated old message is the source of this handoff.
        if kind == 'done' and hasattr(self.hub, 'preview'):
            try:
                preview = preview if preview is not None else self.hub.preview(agent['id'], history=True)
                if preview.get('source') == 'original' and preview.get('observation', {}).get('state') == kind:
                    messages = preview.get('messages', [])
                    user = next((i for i in range(len(messages) - 1, -1, -1) if messages[i].get('role') == 'user'), -1)
                    message = next((m for m in reversed(messages[user + 1:]) if m.get('role') == 'assistant'), None)
                    if message and message.get('phase') not in ('commentary', 'analysis'):
                        target['message_id'] = str(message['id'])
                        target['message_digest'] = hashlib.sha256(message['text'].encode()).hexdigest()
            except (bridge.BridgeError, OSError, ValueError, KeyError, TypeError):
                pass
        return target

    def notice(self, identity):
        if not isinstance(identity, str) or not re.fullmatch(r'[a-zA-Z0-9_-]{1,100}', identity):
            raise bridge.BridgeError('Invalid update link')
        with self.hub.db() as db:
            row = db.execute('SELECT id,kind,title,body,agent_id,created,read_at,handled_at,target FROM notifications WHERE id=?', (identity,)).fetchone()
        if not row:
            raise bridge.BridgeError('This update is no longer available')
        result = dict(row)
        result['body'], result['target'] = short_update(result['kind'], result['body']), json.loads(result['target'])
        if hasattr(self.hub, 'assistant'):
            result = self.hub.assistant.decorate([result])[0]
        return result

    def attended(self, value):
        if not isinstance(value, dict) or value.get('mode') not in ('seen', 'heard'):
            raise bridge.BridgeError('Invalid attention receipt')
        identity, version, agent_id = value.get('id'), value.get('version'), value.get('agent_id')
        if (not isinstance(identity, str) or not re.fullmatch('[a-f0-9]{24}', identity) or
                not isinstance(version, str) or len(version) > 120 or
                not isinstance(agent_id, str) or len(agent_id) > 240):
            raise bridge.BridgeError('Invalid handoff identity')
        with self.hub.db() as db:
            rows = [dict(row) for row in db.execute("SELECT id,target FROM notifications WHERE agent_id=? AND json_extract(target,'$.handoff_id')=?", (agent_id, identity))]
        ids = [row['id'] for row in rows if json.loads(row['target']).get('handoff_id') == identity
               and json.loads(row['target']).get('version') == version]
        if not ids:
            # Speech can finish before the next watch tick emits this handoff.
            current = self.hub.handoffs.snapshot(self.hub.snapshot(include_notifications=False)['agents'])['items']
            if not any(item['id'] == identity and item['version'] == version and item['agent_id'] == agent_id for item in current):
                raise bridge.BridgeError('This handoff is no longer current')
        now, field = self.clock(), 'seen_at' if value['mode'] == 'seen' else 'heard_at'
        with self.hub.db() as db:
            db.execute('INSERT OR IGNORE INTO handoff_attention(id) VALUES(?)', (identity,))
            db.execute(f'UPDATE handoff_attention SET {field}=COALESCE({field},?) WHERE id=?', (now, identity))
            db.execute('INSERT OR IGNORE INTO handoff_marks(id) VALUES(?)', (identity,))
            db.execute('UPDATE handoff_marks SET seen=COALESCE(seen,?) WHERE id=?', (now, identity))
            db.executemany("UPDATE notifications SET read_at=COALESCE(read_at,?),push_state=CASE WHEN push_state='pending' AND push_batch='' THEN 'observed' ELSE push_state END WHERE id=?", [(now, key) for key in ids])
        if hasattr(self.hub, 'live'):
            self.hub.live.wake()

    def emit(self, dedupe, kind, title, body="", agent_id="", target=None):
        target = target or {}
        now = self.clock()
        with self.hub.db() as db:
            attention = db.execute('SELECT COALESCE(seen_at,heard_at) FROM handoff_attention WHERE id=?', (target.get('handoff_id', ''),)).fetchone()
            read_at = attention[0] if attention else None
            db.execute("INSERT OR IGNORE INTO notifications(id,dedupe,kind,title,body,agent_id,created,target,read_at,push_state,next_attempt) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                       (secrets.token_urlsafe(16), dedupe, kind, title[:180], body[:1400], agent_id, now,
                        json.dumps(target), read_at, 'observed' if read_at is not None else 'pending',
                        now + SEEN_GRACE if target.get('handoff_id') else 0))
        self.push_wake.set()
        if hasattr(self.hub, 'live'):
            self.hub.live.wake()

    def tick(self, snapshot=None):
        if not self.enabled or not self.eligible():
            return
        now = self.clock()
        # Give initial connections time to report instead of calling startup an outage.
        if now - self.started < 9:
            return
        snapshot = snapshot or self.hub.snapshot(include_notifications=False)
        with self.hub.db() as db:
            old = {row["id"]: json.loads(row["value"]) for row in db.execute("SELECT * FROM watch_observations")}
        observed = {}
        for connection in snapshot["connections"]:
            if connection["status"] == "connecting":
                continue
            key = "host:" + connection["id"]
            prior = old.get(key, {})
            status = connection["status"]
            current = dict(prior)
            if current.get("candidate") != status:
                current.update(candidate=status, since=now)
            if current.get("status") != status and now - current["since"] >= 9:
                if prior.get("status") or status == "offline":
                    self.emit(f'{key}:{status}:{current["since"]}', "connection", f'{connection["label"]} {"is offline" if status == "offline" else "reconnected"}',
                              "The hub keeps the last updates. Retry commands after the host connects." if status == "offline" else "The hub can receive agent updates again.")
                current["status"] = status
            observed[key] = current
        online = {c["id"] for c in snapshot["connections"] if c["status"] == "online"}
        for agent in snapshot["agents"]:
            key = "agent:" + agent["id"]
            if agent["connection_id"] not in online:
                if key in old:
                    observed[key] = old[key]
                continue
            prior = old.get(key)
            content = meaningful(agent.get("preview", ""))
            digest = agent.get("activity_fingerprint") or hashlib.sha256(content.encode()).hexdigest()[:20]
            status = bridge.handoff_state(agent)
            current = dict(prior or {})
            version = f"{agent['state']}:{agent.get('changed_at', 0)}"
            if (current.get('handoff_version') and current['handoff_version'] != version and
                    status in ('waiting', 'blocked', 'done')):
                current.update(status='unknown', since=now)
            current['handoff_version'] = version
            if current.get("candidate") != status:
                current.update(candidate=status, since=now)
            if current.get("digest") != digest:
                current.update(digest=digest)
                # A completed turn must stay stable while the handoff settles.
                if status == "done":
                    current["since"] = now
            if not prior:
                # Existing handoffs take the same settled, verified path as
                # new ones. A startup digest had neither an exact destination
                # nor the host recheck and could alert about resumed work.
                initial = 'unknown' if status in ('waiting', 'blocked', 'done') and not agent.get('acknowledged') else status
                current.update(status=initial, sequence=0)
            delay = bridge.handoff_delay(agent) if status in ('waiting', 'blocked', 'done', 'exited') else 0
            if current.get("status") != status and now - current["since"] >= delay:
                preview = self.verified_preview(agent, status) if status in ('waiting', 'blocked', 'done') else {}
                if preview is None:
                    # The host may already be working again. Recheck on the
                    # next tick; a stale controller snapshot is not a handoff.
                    current.update(connection_id=agent['connection_id'], title=agent['title'], host=agent['connection_label'], missing=False)
                    observed[key] = current
                    continue
                current["sequence"] += 1
                if status in ("waiting", "blocked", "done", "exited"):
                    label = {"waiting": "needs your input", "blocked": "is blocked", "done": "has a response to review", "exited": "exited"}[status]
                    self.emit(f'{key}:{current["sequence"]}:{status}', status, f'{agent["title"]} on {agent["connection_label"]} {label}',
                              {"waiting": "Open the agent and provide the requested input.",
                               "blocked": "Open the agent and check what prevents progress.",
                               "done": "The agent ended its turn. Review its response. Task completion is not verified.",
                               "exited": "The agent process stopped. Open the agent to check its last output."}[status]
                              + ("\nAgent output: " + excerpt(agent.get("preview", "")) if excerpt(agent.get("preview", "")) else ""), agent["id"], self.target(agent, preview))
                current["status"] = status
            current.update(connection_id=agent["connection_id"], title=agent["title"], display_title=push_agent(agent)['name'], host=agent["connection_label"], missing=False)
            observed[key] = current
        for key, prior in old.items():
            if key not in observed and key.startswith("agent:"):
                if prior.get("connection_id") in online:
                    if not prior.get("missing"):
                        self.emit(f'{key}:missing:{prior.get("sequence", 0)}', "exited", f'{prior.get("title", "Agent")} on {prior.get("host", "host")} is no longer visible', "The hub cannot find its tmux pane. Previous updates remain in the Updates inbox.", key[6:],
                                  {'agents': [{'name': prior.get('display_title') or prior.get('title', 'Agent'), 'host': prior.get('host', '')}]})
                    prior["missing"] = True
                observed[key] = prior
        with self.hub.db() as db:
            # This runs every second; rewriting unchanged rows was most of the
            # hub's disk writes.
            db.executemany("INSERT OR REPLACE INTO watch_observations VALUES(?,?)",
                           [(key, json.dumps(value)) for key, value in observed.items() if old.get(key) != value])
            if snapshot["agents"]:
                db.execute("INSERT OR IGNORE INTO watch_settings VALUES('initialized','true')")
            # Bounded retention. Pending delivery gets a day; read history gets a month.
            db.execute("UPDATE notifications SET push_state='expired' WHERE push_state='pending' AND created<?", (now - 86400,))
            db.execute("DELETE FROM notifications WHERE handled_at IS NOT NULL AND handled_at<?", (now - 30 * 86400,))
            db.execute('DELETE FROM handoff_attention WHERE COALESCE(heard_at,seen_at)<?', (now - 30 * 86400,))
            db.execute('DELETE FROM push_batches WHERE id NOT IN (SELECT push_batch FROM notifications WHERE push_batch != \'\')')
        self.checked, self.error = now, ""

    def relay_call(self, path, body):
        url = self.relay.get("url", "").rstrip("/")
        if urlparse(url).scheme != "https" or not self.relay.get("token"):
            raise bridge.BridgeError("Push delivery has not been configured on this host")
        request = urllib.request.Request(url + path, data=json.dumps(body).encode(), headers={
            "Content-Type": "application/json", "Authorization": "Bearer " + self.relay["token"]})
        try:
            with urllib.request.build_opener(NoRedirect()).open(request, timeout=12) as response:
                return json.loads(response.read(64_000))
        except (OSError, ValueError) as exc:
            raise bridge.BridgeError("Push service unavailable; updates are kept in the inbox") from exc

    def pairing(self):
        if not self.eligible():
            raise bridge.BridgeError("Enable unified notifications from the controller hub")
        result = self.relay_call("/pair", {})
        return {"url": self.relay["url"].rstrip("/") + "/#" + result["ticket"]}

    def refresh_push_status(self, force=False):
        if not self.relay or not self.eligible():
            return dict(self.push_status)
        with self.push_status_lock:
            # Cache health separately; a public network request never blocks state polling.
            if self.clock() - self.push_status_attempt < (5 if force else 30):
                return dict(self.push_status)
            self.push_status_attempt = self.clock()
            try:
                result = self.relay_call('/status', {})
                subscribers = result.get('subscribers')
                if type(subscribers) is not int or not 0 <= subscribers <= 1000:
                    raise bridge.BridgeError('Push service returned an invalid device count')
                accepted = result.get('last_accepted_at', 0)
                if not isinstance(accepted, (int, float)) or not 0 <= accepted <= self.clock() + 60:
                    raise bridge.BridgeError('Push service returned an invalid delivery time')
                self.push_status = {"checked_at": self.clock(), "subscribers": subscribers,
                                    "last_accepted_at": accepted, "error": ""}
            except bridge.BridgeError as exc:
                self.push_status = {**self.push_status, "error": str(exc)}
        return dict(self.push_status)

    def delivery_candidates(self, rows):
        """Revalidate outside the outbox transaction: previews can write archives."""
        if not hasattr(self.hub, 'snapshot'):
            return {row['id'] for row in rows}, []
        agents = {agent['id']:agent for agent in self.hub.snapshot(include_notifications=False)['agents']}
        allowed, superseded, checked = set(), [], {}
        for row in rows:
            if row['kind'] not in ('done', 'waiting', 'blocked') or not row['agent_id']:
                allowed.add(row['id'])
                continue
            agent = agents.get(row['agent_id'])
            if not agent or not agent.get('connection_online'):
                continue
            target = json.loads(row['target'])
            if target.get('version') != f"{agent['state']}:{agent.get('changed_at', 0)}" or bridge.handoff_state(agent) != row['kind']:
                if agent.get('state') != 'unknown':
                    superseded.append(row['id'])
                continue
            key = (agent['id'], row['kind'])
            if key not in checked:
                checked[key] = self.verified_preview(agent, row['kind'])
            preview = checked[key]
            if preview is None or not same_observation(target.get('observation', {}), preview.get('observation', {})):
                continue
            if target.get('message_id'):
                messages = preview.get('messages', [])
                user = next((i for i in range(len(messages) - 1, -1, -1) if messages[i].get('role') == 'user'), -1)
                final = next((message for message in reversed(messages[user + 1:]) if message.get('role') == 'assistant'), {})
                if (final.get('id') != target['message_id'] or final.get('phase') in ('commentary', 'analysis') or
                        hashlib.sha256(final.get('text', '').encode()).hexdigest() != target.get('message_digest')):
                    continue
            allowed.add(row['id'])
        return allowed, superseded

    def deliver(self):
        if not self.relay or not self.enabled or not self.eligible():
            return
        with self.push_lock:
            with self.hub.db() as db:
                pending = [dict(row) for row in db.execute("SELECT * FROM notifications WHERE push_state='pending' AND next_attempt<=? ORDER BY created LIMIT 200", (self.clock(),))]
            if not pending:
                return
            allowed, superseded = self.delivery_candidates(pending)
            with self.hub.db() as db:
                # Claim the exact batch atomically with respect to seen/heard
                # receipts. An already-attempted batch keeps its dedupe identity.
                db.execute('BEGIN IMMEDIATE')
                db.executemany("UPDATE notifications SET push_state='superseded' WHERE id=? AND push_state='pending' AND push_batch=''", [(key,) for key in superseded])
                candidates = [dict(row) for row in db.execute("SELECT * FROM notifications WHERE push_state='pending' AND next_attempt<=? ORDER BY created LIMIT 200", (self.clock(),))]
                assistant = getattr(self.hub, 'assistant', None)
                reports = assistant.reports() if assistant else {}
                state = assistant.state() if assistant else {}
                # A generic phone alert does not need the prose summary. Quiet/focus
                # policy, settled handoffs and exact seen/handled receipts still apply.
                permitted = lambda row: row['id'] in allowed and (not assistant or assistant.may_interrupt(row, reports.get(row['id']), state, wait_for_summary=False))
                held_batches = set()
                for row in candidates:
                    if row['push_batch'] and row['push_batch'] not in held_batches:
                        batch_rows = [dict(r) for r in db.execute("SELECT * FROM notifications WHERE push_state='pending' AND push_batch=?", (row['push_batch'],))]
                        if not all(permitted(r) for r in batch_rows):
                            held_batches.add(row['push_batch'])
                first = next((row for row in candidates if row['push_batch'] not in held_batches and permitted(row)), None)
                if not first:
                    return
                # A previously attempted batch keeps its exact identity and contents.
                # Hold that whole batch if the user's new attention policy excludes any item.
                batch_rows = [dict(row) for row in db.execute("SELECT * FROM notifications WHERE push_state='pending' AND push_batch=? ORDER BY created", (first['push_batch'],))]
                if first['push_batch'] and not all(permitted(row) for row in batch_rows):
                    return
                # Each agent gets its own destination. A mixed batch previously
                # cleared agent_id and opened the app instead of a conversation.
                rows = batch_rows if first['push_batch'] else [row for row in candidates if not row['push_batch'] and
                                                              row['agent_id'] == first['agent_id'] and permitted(row)][:12]
                if not rows:
                    return
                ids = [row["id"] for row in rows]
                batch = first['push_batch'] or hashlib.sha256(":".join(ids).encode()).hexdigest()
                db.executemany("UPDATE notifications SET push_batch=?,push_started_at=COALESCE(push_started_at,?) WHERE id=?", [(batch, self.clock(), key) for key in ids])
                saved = db.execute('SELECT payload FROM push_batches WHERE id=?', (batch,)).fetchone()
                payload = json.loads(saved[0]) if saved else None
                # Include handled members when reconstructing a legacy retry:
                # relay idempotency requires the original immutable payload.
                payload_rows = [dict(row) for row in db.execute('SELECT * FROM notifications WHERE push_batch=? ORDER BY created', (batch,))]
                if len({row['agent_id'] for row in payload_rows}) > 1:
                    # Already-attempted mixed batches cannot safely be rewritten
                    # or split under a new id; keep their inbox history only.
                    db.executemany("UPDATE notifications SET push_state='superseded' WHERE id=? AND push_state='pending'", [(key,) for key in ids])
                    self.push_wake.set()
                    return
                agents = []
                for row in payload_rows:
                    target = json.loads(row['target'])
                    if 'agents' not in target and row['agent_id']:
                        # Older pending notices get identity from durable observations,
                        # not by parsing titles or fetching a live conversation. Freeze
                        # it now so a retry cannot change names after a window rename.
                        prior = db.execute('SELECT value FROM watch_observations WHERE id=?', ('agent:' + row['agent_id'],)).fetchone()
                        observed = json.loads(prior[0]) if prior else {}
                        name = observed.get('display_title') or observed.get('title')
                        target['agents'] = [{'name': name, 'host': observed.get('host', '')}] if name else []
                        db.execute('UPDATE notifications SET target=? WHERE id=?', (json.dumps(target), row['id']))
                    for source in target.get('agents', []):
                        if source not in agents:
                            agents.append(source)
                if payload is None:
                    labels = [push_agent({'title': source['name'], 'connection_label': source['host']}) for source in agents[:3]]
                    payload = {"id": batch, "count": len(payload_rows), "agent_id": payload_rows[-1]['agent_id'],
                               "notice_id": payload_rows[-1]['id'] if payload_rows[-1]['agent_id'] else '',
                               'agents': labels, 'agent_count': len(agents)}
                    db.execute('INSERT INTO push_batches VALUES(?,?)', (batch, json.dumps(payload)))
                more = any(row['id'] not in ids and row['push_batch'] not in held_batches and permitted(row) for row in candidates)
            try:
                result = self.relay_call("/publish", payload)
                # No enrolled browser is not a delivery failure; history is in the inbox.
                with self.hub.db() as db:
                    db.executemany("UPDATE notifications SET push_state=?,push_accepted_at=? WHERE id=?", [("sent" if result.get("subscribers") else "no_device", self.clock() if result.get('subscribers') else None, key) for key in ids])
                self.push_error = ""
                # Enrollment may have changed since the last health check.
                self.push_status_attempt = 0
            except bridge.BridgeError as exc:
                self.push_error = str(exc)
                with self.hub.db() as db:
                    for row in rows:
                        delay = min(1800, 2 * 2 ** min(row["attempts"], 10))
                        db.execute("UPDATE notifications SET attempts=attempts+1,next_attempt=? WHERE id=?", (self.clock() + delay, row["id"]))
            if more:
                self.push_wake.set()

    def next_delivery(self, since=None):
        now = self.clock()
        with self.hub.db() as db:
            due = db.execute("SELECT min(next_attempt) FROM notifications WHERE push_state='pending' AND next_attempt>?", (now if since is None else since,)).fetchone()[0]
        return min(3, max(0, due - now)) if due is not None else 3

    def start(self):
        def watch():
            while not self.hub.stop.is_set():
                self.wake.clear()
                try:
                    self.tick()
                except Exception:
                    self.error = "Watching interrupted; retrying. Existing updates are saved."
                self.wake.wait(1)
        def push():
            while not self.hub.stop.is_set():
                self.push_wake.clear()
                started, delay = self.clock(), 3
                try:
                    self.deliver()
                    delay = self.next_delivery(started)
                except Exception:
                    self.push_error = "Push delivery interrupted; retrying."
                self.push_wake.wait(delay)
        def health():
            while not self.hub.stop.is_set():
                try:
                    self.refresh_push_status()
                except Exception:
                    self.push_status = {**self.push_status, 'error': 'Push health check interrupted; retrying.'}
                self.hub.stop.wait(30)
        self.thread = threading.Thread(target=watch, daemon=True)
        self.push_thread = threading.Thread(target=push, daemon=True)
        self.thread.start()
        self.push_thread.start()
        threading.Thread(target=health, daemon=True, name='hub-push-health').start()
