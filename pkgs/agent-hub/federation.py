"""Authenticated peer sync, local journals, and explicit supervisor ownership.

Peers replicate immutable events, never database files. Each host alone observes
and controls its tmux server. There is deliberately no timeout-based election.
"""

import copy
import ipaddress
import json
import logging
from pathlib import Path
import re
import secrets
import threading
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, build_opener, HTTPRedirectHandler, ProxyHandler

from bridge import BridgeError
from updates import UpdateBases, apply_update


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        raise BridgeError("Peer redirects are not allowed")


def read_config(path):
    path = Path(path)
    if path.stat().st_mode & 0o077:
        raise ValueError("Node config contains credentials; use chmod 600")
    value = json.loads(path.read_text())
    role = value.get('role', 'controller')
    if role not in ('controller', 'worker'):
        raise ValueError('Node role must be controller or worker')
    if 'allowed_clients' in value:
        if not isinstance(value['allowed_clients'], list):
            raise ValueError('allowed_clients must be a list of IP networks')
        for network in value['allowed_clients']:
            ipaddress.ip_network(network, strict=False)
    ids = [value.get("node_id"), *(peer.get("id") for peer in value.get("peers", []))]
    if len(set(ids)) != len(ids) or any(not isinstance(key, str) or not re.fullmatch(r"[a-zA-Z0-9_-]{1,64}", key) for key in ids):
        raise ValueError("Each node needs a unique stable ID")
    if value.get("supervisor_owner") not in ids:
        raise ValueError("Supervisor owner must be a configured node")
    if value.get("tailnet_bind"):
        address = ipaddress.ip_address(value['tailnet_bind'])
        if address not in ipaddress.ip_network('100.64.0.0/10'):
            raise ValueError('The additional listener must bind a Tailscale IPv4 address')
    for peer in value.get("peers", []):
        peer_role = peer.get('role', 'peer')
        if peer_role not in (('controller',) if role == 'worker' else ('peer', 'worker')):
            raise ValueError('Workers only accept controllers; controllers connect to peers or workers')
        url = urlparse(peer.get("url", ""))
        if peer_role != 'controller' and (url.scheme not in ("http", "https") or not url.hostname or url.username or url.password or url.query or url.fragment or url.path not in ("", "/")):
            raise ValueError("Peer URL must be an HTTP(S) origin")
        if peer.get('public_url'):
            browser_url = urlparse(peer['public_url'])
            if browser_url.scheme != 'https' or not browser_url.hostname or browser_url.username or browser_url.password or browser_url.query or browser_url.fragment or browser_url.path not in ('', '/'):
                raise ValueError('Peer browser URL must be an HTTPS origin')
        if len(peer.get("token", "")) < 32:
            raise ValueError("Each peer needs a private token of at least 32 characters")
    return value


class Mesh:
    def __init__(self, hub, config):
        self.hub = hub
        self.config = config
        self.node_id = config["node_id"]
        self.is_worker = config.get('role') == 'worker'
        self.peers = {peer["id"]: peer for peer in config.get("peers", [])}
        self.known = {self.node_id, *self.peers}
        self.lock = threading.RLock()
        self.sync_locks = {key: threading.Lock() for key in self.peers}
        self.cache = {}
        self.read_bases = UpdateBases()
        self.peer_errors = {}
        self.last_seen = {}
        self.opener = build_opener(ProxyHandler({}), NoRedirect())
        with hub.db() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS journal(
                    origin TEXT NOT NULL, seq INTEGER NOT NULL, clock INTEGER NOT NULL,
                    kind TEXT NOT NULL, payload TEXT NOT NULL, created REAL NOT NULL,
                    PRIMARY KEY(origin,seq));
                CREATE TABLE IF NOT EXISTS node_cache(node TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS command_receipts(
                    id TEXT PRIMARY KEY, digest TEXT NOT NULL, status TEXT NOT NULL, result TEXT);
                CREATE TABLE IF NOT EXISTS mesh_meta(key TEXT PRIMARY KEY, value TEXT NOT NULL);
            """)
            bound = db.execute("SELECT value FROM mesh_meta WHERE key='node_id'").fetchone()
            if bound and bound[0] != self.node_id:
                raise ValueError("This state directory belongs to a different node")
            db.execute("INSERT OR IGNORE INTO mesh_meta VALUES('node_id',?)", (self.node_id,))
            boot = db.execute("SELECT value FROM mesh_meta WHERE key='initial_owner'").fetchone()
            if boot and boot[0] != config['supervisor_owner']:
                raise ValueError("Use supervisor handoff to change ownership; retain the original bootstrap owner")
            db.execute("INSERT OR IGNORE INTO mesh_meta VALUES('initial_owner',?)", (config['supervisor_owner'],))
            for row in db.execute("SELECT node,value FROM node_cache"):
                self.cache[row['node']] = json.loads(row['value'])
        self.migrate_history()

    def append(self, kind, payload, created=None):
        with self.hub.db() as db:
            db.execute("BEGIN IMMEDIATE")
            seq = db.execute("SELECT COALESCE(MAX(seq),0)+1 FROM journal WHERE origin=?", (self.node_id,)).fetchone()[0]
            clock = db.execute("SELECT COALESCE(MAX(clock),0)+1 FROM journal").fetchone()[0]
            db.execute("INSERT INTO journal VALUES(?,?,?,?,?,?)", (self.node_id, seq, clock, kind, json.dumps(payload), created if created is not None else time.time()))

    def migrate_history(self):
        with self.hub.db() as db:
            db.execute('BEGIN IMMEDIATE')
            if db.execute("SELECT 1 FROM mesh_meta WHERE key='migrated'").fetchone():
                return
            messages = [dict(row) for row in db.execute("SELECT * FROM messages ORDER BY id")]
            events = [dict(row) for row in db.execute("SELECT * FROM events ORDER BY id")]
            marks = [dict(row) for row in db.execute("SELECT * FROM marks")]
            aliases = {'local': self.node_id, **self.config.get('legacy_connection_ids', {})}
            def agent_id(value):
                prefix, sep, tail = value.partition(':')
                return aliases.get(prefix, prefix) + sep + tail
            seq = db.execute('SELECT COALESCE(MAX(seq),0) FROM journal WHERE origin=?', (self.node_id,)).fetchone()[0]
            clock = db.execute('SELECT COALESCE(MAX(clock),0) FROM journal').fetchone()[0]
            def insert(kind, payload, created):
                nonlocal seq, clock
                seq += 1
                clock += 1
                db.execute('INSERT INTO journal VALUES(?,?,?,?,?,?)', (self.node_id, seq, clock, kind, json.dumps(payload), created))
            for kind, item in sorted([('message', x) for x in messages] + [('event', x) for x in events], key=lambda item: item[1]['created']):
                payload = {k: v for k, v in item.items() if k not in ('id', 'created')}
                if 'agent_id' in payload:
                    payload['agent_id'] = agent_id(payload['agent_id'])
                insert(kind, payload, item['created'])
            for row in marks:
                insert('mark', {'agent_id': agent_id(row['agent_id']), 'patch': json.loads(row['value'])}, time.time())
            db.execute("INSERT OR REPLACE INTO mesh_meta VALUES('migrated','yes')")

    def cursors(self):
        with self.hub.db() as db:
            return {row[0]: row[1] for row in db.execute("SELECT origin,MAX(seq) FROM journal GROUP BY origin")}

    def export(self, have):
        if not isinstance(have, dict) or any(key not in self.known or type(seq) is not int or seq < 0 for key, seq in have.items()):
            raise BridgeError('Invalid journal cursor')
        with self.hub.db() as db:
            rows = db.execute("SELECT * FROM journal ORDER BY clock,origin,seq")
            result = []
            for row in rows:
                if row['seq'] > have.get(row['origin'], 0):
                    result.append({**dict(row), 'payload': json.loads(row['payload'])})
                    if len(result) == 200:
                        break
            return result

    def ingest(self, events):
        if not isinstance(events, list) or len(events) > 200:
            raise BridgeError('Invalid journal batch')
        with self.hub.action_lock, self.hub.db() as db:
            db.execute('BEGIN IMMEDIATE')
            for event in events:
                origin, seq = event.get('origin'), event.get('seq')
                if origin not in self.known or type(seq) is not int or seq < 1 or type(event.get('clock')) is not int or event['clock'] < 1:
                    raise BridgeError('Invalid journal identity')
                if event.get('kind') not in ('message', 'event', 'mark', 'leadership') or not isinstance(event.get('payload'), dict):
                    raise BridgeError('Invalid journal event')
                payload = json.dumps(event['payload'])
                existing = db.execute('SELECT * FROM journal WHERE origin=? AND seq=?', (origin, seq)).fetchone()
                if existing:
                    if existing['kind'] != event['kind'] or json.loads(existing['payload']) != event['payload'] or existing['clock'] != event['clock'] or existing['created'] != event['created']:
                        raise BridgeError('Conflicting immutable event')
                    continue
                if origin == self.node_id:
                    raise BridgeError('A peer cannot create events for this node')
                last = db.execute('SELECT COALESCE(MAX(seq),0) FROM journal WHERE origin=?', (origin,)).fetchone()[0]
                if seq != last + 1:
                    raise BridgeError('Journal gap; retry from the last saved cursor')
                db.execute('INSERT INTO journal VALUES(?,?,?,?,?,?)', (origin, seq, event['clock'], event['kind'], payload, event['created']))

    def views(self):
        messages, events, marks = [], [], {}
        with self.hub.db() as db:
            query = 'SELECT * FROM journal' + (' WHERE origin=?' if self.is_worker else '') + ' ORDER BY clock,origin,seq'
            for row in db.execute(query, (self.node_id,) if self.is_worker else ()):
                payload = json.loads(row['payload'])
                item = {**payload, 'id': f"{row['origin']}:{row['seq']}", 'created': row['created']}
                if row['kind'] == 'message':
                    messages.append(item)
                elif row['kind'] == 'event':
                    events.append(item)
                elif row['kind'] == 'mark':
                    marks.setdefault(payload['agent_id'], {}).update(payload['patch'])
        return messages[-60:], list(reversed(events[-35:])), marks

    def authority(self):
        if self.is_worker:
            return {'owner': self.node_id, 'epoch': 1}
        authority = {'owner': self.config['supervisor_owner'], 'epoch': 1}
        with self.hub.db() as db:
            rows = [dict(row) for row in db.execute("SELECT * FROM journal WHERE kind='leadership'")]
        rows.sort(key=lambda row: (json.loads(row['payload']).get('epoch', 0), row['origin'], row['seq']))
        for row in rows:
            candidate = json.loads(row['payload'])
            if row['origin'] == authority['owner'] and candidate.get('epoch') == authority['epoch'] + 1 and candidate.get('owner') in self.known:
                authority = {'owner': candidate['owner'], 'epoch': candidate['epoch']}
        return authority

    def request(self, peer_id, method, value=None):
        if self.is_worker:
            raise BridgeError('Worker nodes cannot contact other hubs')
        peer = self.peers.get(peer_id)
        if not peer:
            raise BridgeError('Unknown peer')
        request = Request(peer['url'].rstrip('/') + '/peer/' + method,
                          data=json.dumps(value or {}).encode(), headers={
                              'Content-Type': 'application/json', 'X-Hub-Peer': self.node_id,
                              'Authorization': 'Bearer ' + peer['token']})
        action = (value or {}).get("action", value or {})
        timeout = 110 if action.get("type") == "fork" and method in ("action", "execute", "worker/execute") else 20 if method.endswith('image/upload') else 12 if method in ("action", "execute", "worker/execute", "supervisor", "handoff") else 5
        try:
            with self.opener.open(request, timeout=timeout) as response:
                limit = 4_300_000 if method.endswith('image/read') else 4_000_000
                data = response.read(limit + 1)
                if len(data) > limit:
                    raise BridgeError('Peer response too large')
                return json.loads(data)
        except HTTPError as exc:
            try:
                error = json.loads(exc.read(4000)).get('error', str(exc.code))
            except ValueError:
                error = str(exc.code)
            finally:
                exc.close()
            raise BridgeError(f"{peer_id}: {error}") from exc
        except (URLError, OSError, ValueError) as exc:
            raise BridgeError(f'{peer_id} is unavailable; local monitoring continues') from exc

    def authenticate(self, headers):
        peer_id = headers.get('X-Hub-Peer', '')
        peer = self.peers.get(peer_id)
        if not peer or peer.get('role') == 'worker' or headers.get('Origin') or not secrets.compare_digest(headers.get('Authorization', ''), 'Bearer ' + peer['token']):
            raise BridgeError('Peer authentication failed')
        return peer_id

    def read_request(self, peer_id, method, value=None):
        """Delta negotiation is read-only and initiated solely by the controller."""
        if method not in ('sync', 'worker/snapshot', 'preview', 'worker/preview'):
            raise BridgeError('This operation does not support compact reads')
        value = value or {}
        key = (peer_id, method, value.get('agent_id', ''), bool(value.get('history')), bool(value.get('terminal')))
        base = self.read_bases.get(key)
        field = 'snapshot_update' if method in ('sync', 'worker/snapshot') else 'update'
        for attempt in range(2):
            response = self.request(peer_id, method, {**value, 'updates': True, 'since': base['version'] if base else ''})
            if field not in response:
                return response  # Rolling upgrades retain the original JSON API.
            try:
                result = apply_update(base, response[field])
                self.read_bases.put(key, response[field], result)
                return {**response, 'snapshot': result} if field == 'snapshot_update' else result
            except ValueError as exc:
                if attempt:
                    raise BridgeError('Peer update could not be applied') from exc
                base = None

    def save_cache(self, node, value):
        with self.lock:
            if self.cache.get(node) == value:
                return  # Polled every second; most polls change nothing.
            self.cache[node] = copy.deepcopy(value)
        with self.hub.db() as db:
            db.execute('INSERT OR REPLACE INTO node_cache VALUES(?,?)', (node, json.dumps(value)))

    def local_export(self):
        snapshot = self.hub.snapshot(include_peers=False)
        for agent in snapshot['agents']:
            agent.pop('preview', None)
        return {key: snapshot[key] for key in ('connections', 'agents', 'supervisor')}

    def sync(self, peer_id, refresh=False):
        if self.is_worker:
            raise BridgeError('Worker nodes cannot contact other hubs')
        with self.sync_locks[peer_id]:
            try:
                if self.peers[peer_id].get('role') == 'worker':
                    value = self.read_request(peer_id, 'worker/snapshot', {'refresh': True} if refresh else None)
                    if value.get('node_id') != peer_id:
                        raise BridgeError('Worker identity mismatch')
                    self.validate_worker_snapshot(peer_id, value.get('snapshot'))
                    self.record_worker_changes(peer_id, value['snapshot'])
                else:
                    for page in range(20):
                        value = self.read_request(peer_id, 'sync', {'have': self.cursors(), **({'refresh': True} if refresh and page == 0 else {})})
                        if value.get('node_id') != peer_id:
                            raise BridgeError('Peer identity mismatch')
                        self.ingest(value['events'])
                        if len(value['events']) < 200:
                            break
                self.save_cache(peer_id, value['snapshot'])
                with self.lock:
                    self.last_seen[peer_id] = time.time()
                    self.peer_errors.pop(peer_id, None)
                self.hub.live.wake()
                if hasattr(self.hub, 'watcher'):
                    self.hub.watcher.wake.set()
            except BridgeError as exc:
                with self.lock:
                    self.peer_errors[peer_id] = str(exc)
                self.hub.live.wake()
                raise

    def start(self):
        if self.is_worker:
            return
        for peer_id in self.peers:
            threading.Thread(target=self.poll, args=(peer_id,), daemon=True).start()

    def poll(self, peer_id):
        while not self.hub.stop.is_set():
            try:
                self.sync(peer_id)
            except BridgeError:
                pass
            except Exception as exc:
                with self.lock:
                    self.peer_errors[peer_id] = str(exc)[:300]
                logging.exception("Peer polling failed; retrying %s", peer_id)
            self.hub.stop.wait(self.hub.poll_interval)

    def entries(self):
        if self.is_worker:
            return []
        entries = []
        with self.lock:
            for node, peer in self.peers.items():
                cached = self.cache.get(node, {})
                online = node not in self.peer_errors and time.time() - self.last_seen.get(node, 0) < 15
                for conn in cached.get('connections', [{'id': node, 'label': peer.get('label', node), 'socket': ''}]):
                    # Only the peer's own observations are exported, never recursively cached nodes.
                    config = {**conn, 'kind': 'peer', 'peer_id': node, 'target': peer.get('ssh_target', ''), 'url': peer['url'],
                              'peer_role': peer.get('role', 'peer')}
                    entries.append({'config': config, 'status': 'online' if online and conn.get('status') == 'online' else 'offline',
                                    'error': self.peer_errors.get(node, '') or conn.get('error', ''), 'last_seen': conn.get('last_seen', 0),
                                    'agents': copy.deepcopy([a for a in cached.get('agents', []) if a['connection_id'] == conn['id']])})
        return entries

    def peer_for(self, agent_id=None, connection_id=None):
        for entry in self.entries():
            if (connection_id and entry['config']['id'] == connection_id) or any(a['id'] == agent_id for a in entry['agents']):
                if entry['status'] != 'online':
                    raise BridgeError('This host is offline; commands are not queued')
                return entry['config']['peer_id']
        return None

    def supervisor_status(self):
        authority = self.authority()
        owner = authority['owner']
        if owner == self.node_id:
            status = self.hub.local_supervisor_status()
        else:
            with self.lock:
                status = dict(self.cache.get(owner, {}).get('supervisor', {}))
                status['available'] = bool(status.get('available')) and owner not in self.peer_errors and time.time() - self.last_seen.get(owner, 0) < 15
        return {**self.hub.local_supervisor_status(), **status, **authority, 'node_id': self.node_id}

    def handoff(self, target):
        if self.is_worker or self.peers.get(target, {}).get('role') in ('worker', 'controller'):
            raise BridgeError('Only trusted controller peers can own the unified supervisor')
        # Revocation is durable before the successor can learn that it owns the role.
        # A lost acknowledgement leaves the predecessor revoked; it never rolls back.
        authority = self.authority()
        if authority['owner'] != self.node_id:
            return self.request(authority['owner'], 'handoff', {'target': target})
        with self.hub.action_lock:
            authority = self.authority()
            if authority['owner'] != self.node_id:
                raise BridgeError('Supervisor changed; refresh first')
            if target not in self.peers:
                raise BridgeError('Choose another configured node')
            if self.hub.supervisor_busy:
                raise BridgeError('Wait for the supervisor to finish before handing it off')
            self.sync(target)
            target_state = self.cache[target]['supervisor']
            if not target_state.get('local_available'):
                raise BridgeError('The target node has no supervisor CLI available')
            self.append('leadership', {'owner': target, 'epoch': authority['epoch'] + 1})
        try:
            self.request(target, 'refresh', {})
        except BridgeError:
            return {'ok': True, 'message': f'Supervisor released to {target}; waiting for it to reconnect. This node is now inactive.'}
        return {'ok': True, 'message': f'Supervisor moved to {target}'}

    def validate_worker_snapshot(self, peer_id, snapshot):
        if not isinstance(snapshot, dict) or not isinstance(snapshot.get('agents'), list) or not isinstance(snapshot.get('connections'), list):
            raise BridgeError('Invalid worker snapshot')
        connections = snapshot['connections']
        if len(connections) != 1 or connections[0].get('id') != peer_id or connections[0].get('kind') != 'local':
            raise BridgeError('Worker can only report its own local connection')
        ids = set()
        for agent in snapshot['agents']:
            required = ('id', 'connection_id', 'connection_label', 'title', 'summary', 'state', 'runtime', 'pane', 'cwd')
            if not isinstance(agent, dict) or any(not isinstance(agent.get(key), str) for key in required):
                raise BridgeError('Invalid worker agent observation')
            agent_id = agent.get('id', '')
            if agent.get('connection_id') != peer_id or not agent_id.startswith(peer_id + ':') or agent_id in ids:
                raise BridgeError('Worker cannot report agents from another host')
            ids.add(agent_id)
        # These are observations, never replicated chat, marks, or authority.
        snapshot.pop('messages', None)
        snapshot.pop('events', None)
        snapshot.pop('marks', None)

    def record_worker_changes(self, peer_id, snapshot):
        with self.lock:
            previous = {agent['id']: agent for agent in self.cache.get(peer_id, {}).get('agents', [])}
        for agent in snapshot['agents']:
            prior = previous.get(agent['id'])
            if not prior or prior.get('state') != agent.get('state'):
                self.hub.event('state' if prior else 'discovered',
                               f"{agent['title']} on {peer_id}: {agent['summary']}", agent['id'])

    def execute_once(self, command_id, value, operation):
        return self.hub.deliveries.execute_once(command_id, value, operation)
