"""Private browser checkpoints. These never enter the peer journal or agent context."""
import json
import math
import re
import time

from bridge import BridgeError


def text(value, limit, *, empty=True):
    if not isinstance(value, str) or len(value) > limit or (not empty and not value) or '\x00' in value:
        raise BridgeError('Invalid saved conversation')
    return value


def identity(value):
    if not isinstance(value, str) or not re.fullmatch(r'[a-zA-Z0-9_-]{16,64}', value):
        raise BridgeError('Invalid browser identity')
    return value


def number(value, low, high):
    if type(value) not in (int, float) or not math.isfinite(value) or not low <= value <= high:
        raise BridgeError('Invalid reading position')
    return value


def bookmark(value):
    if value is None:
        return None
    if not isinstance(value, dict) or type(value.get('bottom')) is not bool:
        raise BridgeError('Invalid reading position')
    result = {'bottom': value['bottom'], 'top': number(value.get('top', 0), 0, 100000000)}
    if 'scope' in value:
        if value['scope'] not in ('agent', 'supervisor'):
            raise BridgeError('Invalid reading scope')
        result['scope'] = value['scope']
    if value.get('anchor') is not None:
        anchor = value['anchor']
        if not isinstance(anchor, dict):
            raise BridgeError('Invalid reading anchor')
        result['anchor'] = {'id': text(anchor.get('id'), 512, empty=False),
                            'offset': number(anchor.get('offset'), -100000000, 100000000)}
        if anchor.get('block') is not None:
            block = anchor['block']
            if not isinstance(block, dict) or type(block.get('index')) is not int:
                raise BridgeError('Invalid reading block')
            result['anchor']['block'] = {'index': number(block['index'], 0, 100000),
                'digest': text(block.get('digest'), 32, empty=False),
                'offset': number(block.get('offset'), -100000000, 100000000),
                'fraction': number(block.get('fraction'), 0, 1)}
    if value.get('seen') is not None:
        seen = value['seen']
        if not isinstance(seen, dict):
            raise BridgeError('Invalid reading receipt')
        result['seen'] = {'id': text(seen.get('id'), 512, empty=False), 'digest': text(seen.get('digest'), 32)}
    return result


class Continuity:
    retention = 7 * 86400

    def __init__(self, hub, clock=time.time):
        self.hub, self.clock = hub, clock
        with hub.db() as db:
            db.execute('CREATE TABLE IF NOT EXISTS browser_checkpoints(device TEXT, tab TEXT, sequence INTEGER, updated REAL, value TEXT, PRIMARY KEY(device,tab))')

    def require_controller(self):
        if self.hub.mesh and self.hub.mesh.is_worker:
            raise BridgeError('Continue here is available on your controller')

    def save(self, request):
        self.require_controller()
        device, tab = identity(request.get('device')), identity(request.get('tab'))
        sequence = request.get('sequence')
        if type(sequence) is not int or not 1 <= sequence < 2 ** 53:
            raise BridgeError('Invalid checkpoint sequence')
        label = text(request.get('label'), 64, empty=False)
        if any(ord(char) < 32 for char in label):
            raise BridgeError('Invalid device name')
        agent_id = text(request.get('agent_id'), 256)
        mode = request.get('mode')
        if mode not in ('agent', 'supervisor') or (mode == 'agent' and not agent_id):
            raise BridgeError('Invalid conversation target')
        agent = next((a for a in self.hub.snapshot()['agents'] if a['id'] == agent_id), None) if agent_id else None
        if agent_id and not agent:
            raise BridgeError('This agent is no longer available')
        value = {'agent_id': agent_id, 'mode': mode, 'label': label,
                 'title': agent.get('display_title', agent['title']) if agent else 'Supervisor',
                 'host': agent['connection_label'] if agent else '',
                 'draft': text(request.get('draft'), 12000 if mode == 'agent' else 8000),
                 'bookmark': bookmark(request.get('bookmark'))}
        now = self.clock()
        with self.hub.db() as db:
            db.execute('BEGIN IMMEDIATE')
            db.execute('DELETE FROM browser_checkpoints WHERE updated < ?', (now - self.retention,))
            previous = db.execute('SELECT sequence FROM browser_checkpoints WHERE device=? AND tab=?', (device, tab)).fetchone()
            if previous and previous[0] >= sequence:
                return {'ok': True, 'saved': False}
            db.execute('INSERT OR REPLACE INTO browser_checkpoints VALUES(?,?,?,?,?)',
                       (device, tab, sequence, now, json.dumps(value)))
            db.execute('DELETE FROM browser_checkpoints WHERE device=? AND rowid NOT IN (SELECT rowid FROM browser_checkpoints WHERE device=? ORDER BY updated DESC, rowid DESC LIMIT 4)', (device, device))
            db.execute('DELETE FROM browser_checkpoints WHERE rowid NOT IN (SELECT rowid FROM browser_checkpoints ORDER BY updated DESC, rowid DESC LIMIT 32)')
        return {'ok': True, 'saved': True}

    def list(self, device):
        self.require_controller()
        identity(device)
        agents = {agent['id'] for agent in self.hub.snapshot()['agents']}
        with self.hub.db() as db:
            rows = db.execute('SELECT device,updated,value FROM browser_checkpoints WHERE device!=? AND updated>=? ORDER BY updated DESC,rowid DESC',
                              (device, self.clock() - self.retention)).fetchall()
        seen, items = set(), []
        for row in rows:
            if row['device'] in seen:
                continue
            seen.add(row['device'])
            value = json.loads(row['value'])
            items.append({**value, 'updated': row['updated'], 'available': not value['agent_id'] or value['agent_id'] in agents})
            if len(items) == 8:
                break
        return {'items': items}
