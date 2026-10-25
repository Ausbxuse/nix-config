"""Controller-owned commitments and attention policy. Observations never issue commands."""
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import threading
import time
import uuid

from bridge import BridgeError, handoff_state
from task_facts import digest, visible
from spoken_reply import unique_object, READ_ONLY_CONFIG

ROOT = Path(__file__).resolve().parent
MODES = {'normal', 'driving', 'focus', 'quiet'}
TERMINAL = {'response', 'paused', 'closed', 'unconfirmed'}
SHORT_REPLY_WORDS = 300
SUMMARY_POLICY = 1
RECEIPT_QUESTIONS = frozenset({
    'did you send it', 'did you send that', 'did you send my message',
    'did you send my instruction', 'was my message sent', 'was that sent',
    'did you queue it', 'did you queue that', 'is my message queued',
    'did the agent receive my message', 'did the agent get my instruction',
    'did the agent reply', 'has the agent replied', 'did it reply', 'has it replied',
    'what happened to my last instruction', 'what happened to my last message',
})
ACTIVITY_QUESTIONS = frozenset({
    'is it working', 'is it still working', 'is the agent working',
    'is the agent still working', 'is this agent working',
    'is it still running', 'is the agent still running',
})


def question_text(query):
    text = ' '.join(query.casefold().split()).rstrip('.?!').strip()
    for prefix in ('aria, ', 'aria: ', 'aria ', 'supervisor, ', 'supervisor: ', 'supervisor '):
        if text.startswith(prefix):
            return text[len(prefix):]
    return text


def report_response(stdout):
    candidates = []
    for line in stdout.splitlines():
        try:
            event = json.loads(line, object_pairs_hook=unique_object)
            if isinstance(event, dict) and event.get('type') == 'item.completed' and event.get('item', {}).get('type') == 'agent_message':
                candidates.append(event['item']['text'])
        except (ValueError, KeyError, TypeError):
            continue
    for candidate in reversed(candidates):
        try:
            value = json.loads(candidate, object_pairs_hook=unique_object)
            if isinstance(value, dict) and set(value) == {'summary', 'source_ids', 'relevant_ids', 'focus_relevant'}:
                return value
        except (ValueError, TypeError):
            continue
    raise BridgeError('Handoff summary did not include a valid structured answer.')


def requested(text):
    """A conservative fast-path exclusion, not a parser or authorization check."""
    return bool(re.search(r'\b(remember|forget|watch|notify|remind|priority|priorities|focus|quiet|driving|interruptions?|hold|resume updates)\b|let me know|keep (?:an eye|track)|only (?:tell|alert)|stop (?:watching|updates)', text, re.I))


def settled(agent):
    return (agent and agent.get('connection_online') and not agent.get('activity_confirmed')
            and handoff_state(agent) in ('waiting', 'blocked', 'done', 'exited')
            and agent.get('recovery', {}).get('status') not in ('retrying', 'checking', 'waiting'))


def useful_summary(reply, summary):
    """Only add a brief when it cuts the reading by at least half."""
    return (isinstance(summary, str) and bool(summary.strip()) and
            len(reply.split()) > SHORT_REPLY_WORDS and
            len(summary.split()) * 2 <= len(reply.split()) and
            len(summary.strip()) * 2 <= len(reply.strip()))


class Assistant:
    def __init__(self, hub, clock=time.time):
        self.hub, self.clock = hub, clock
        self.lock = threading.RLock()
        self.summary_lock = threading.Lock()
        self.last_track = 0
        self.error = ''
        with hub.db() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS assistant_records(id TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS assistant_settings(id TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS assistant_commitments(id TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS assistant_reports(id TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS assistant_reports_created ON assistant_reports(json_extract(value,'$.created'));
            ''')
            db.execute("INSERT OR IGNORE INTO assistant_settings VALUES('revision','0')")
            db.execute("INSERT OR IGNORE INTO assistant_settings VALUES('since',?)", (str(clock()),))
        self.since = float(self.setting('since', clock()))

    def eligible(self):
        mesh = self.hub.mesh
        return not mesh or (not mesh.is_worker and mesh.authority()['owner'] == mesh.node_id)

    def require_owner(self):
        if not self.eligible():
            raise BridgeError('Open the controller hub to manage your assistant.')

    def setting(self, key, default=None):
        with self.hub.db() as db:
            row = db.execute('SELECT value FROM assistant_settings WHERE id=?', (key,)).fetchone()
        return json.loads(row[0]) if row else default

    def values(self, table):
        assert table in ('assistant_records', 'assistant_commitments', 'assistant_reports')
        with self.hub.db() as db:
            return [json.loads(row[0]) for row in db.execute(f'SELECT value FROM {table}')]

    def put(self, table, value):
        assert table in ('assistant_records', 'assistant_commitments', 'assistant_reports')
        with self.hub.db() as db:
            db.execute(f'INSERT OR REPLACE INTO {table} VALUES(?,?)', (value['id'], json.dumps(value)))
        if hasattr(self.hub, 'live'):
            self.hub.live.wake()

    def state(self):
        if not self.eligible():
            return {'available': False}
        now = self.clock()
        mode = self.setting('attention', {})
        if mode.get('until', 0) <= now:
            mode = {'mode': 'normal', 'text': '', 'until': 0}
        records = [r for r in self.values('assistant_records') if not r.get('until') or r['until'] > now]
        commitments = sorted(self.values('assistant_commitments'), key=lambda r: r['created'], reverse=True)
        public = [{k: r[k] for k in ('id', 'agent_id', 'type', 'text', 'status', 'result', 'source', 'created', 'updated', 'observation', 'response') if k in r} for r in commitments if not r.get('hidden')][:20]
        return {'available': True, 'revision': self.setting('revision', 0), 'attention': mode,
                'records': records[:12], 'commitments': public, 'error': self.error}

    def context(self):
        value = self.state()
        # No growing conversation or tool output. Exact user words remain the authority.
        return {**{k: value[k] for k in ('revision', 'attention') if k in value},
                'records': [{k: r[k] for k in ('id', 'kind', 'text', 'agent_id', 'updated')} for r in value.get('records', [])],
                'commitments': [{k: (r[k][:500] if isinstance(r[k], str) else r[k]) for k in
                    ('id', 'agent_id', 'type', 'text', 'status', 'result', 'observation') if k in r} for r in value.get('commitments', [])[:8]]}

    def receipt_reply(self, query, selected, agents):
        """Answer a narrow receipt question from evidence, without a model or action.

        Full-utterance matching deliberately leaves compound requests, quotations,
        and ambiguous batches on the normal supervisor path.
        """
        if question_text(query) not in RECEIPT_QUESTIONS or not self.eligible():
            return None
        candidates = [r for r in self.values('assistant_commitments')
                      if not r.get('hidden') and r['type'] in ('send', 'queue', 'launch')
                      and (r['agent_id'] == selected if selected else not r.get('source', {}).get('context_agent_id'))]
        if not candidates:
            return None
        candidates.sort(key=lambda r: r['created'], reverse=True)
        record = candidates[0]
        request = record.get('source', {}).get('request_id')
        if any(r['created'] == record['created'] or (request and r.get('source', {}).get('request_id') == request)
               for r in candidates[1:]):
            return None
        self.track(agents, command_ids={record['id']})
        record = next((r for r in self.values('assistant_commitments') if r['id'] == record['id'] and not r.get('hidden')), None)
        if not record:
            return None
        agent = next((a for a in agents if a['id'] == record['agent_id']), None)
        name = (f"{agent.get('display_title') or agent['title']} on {agent['connection_label']}"
                if agent else 'the original agent')
        status = record['status']
        reply = {
            'sending': f"I'm still waiting for delivery confirmation from {name}.",
            'unconfirmed': f"I can't confirm delivery to {name}. Check the conversation before sending it again.",
            'delivered': f"I've sent it to {name}. I haven't confirmed that it accepted the instruction yet.",
            'queued': f"It's queued for {name}. I haven't seen it start that instruction yet.",
            'started': f"{name} accepted that exact instruction. I haven't confirmed its final reply yet.",
            'response': f"{name} replied to that instruction.",
        }.get(status)
        if reply is None:
            return None
        if not agent or not agent.get('connection_online'):
            known = {
                'sending': f"Delivery to {name} is still unconfirmed.",
                'unconfirmed': f"Delivery to {name} is still unconfirmed.",
                'delivered': f"I confirmed delivery to {name}.",
                'queued': f"I confirmed it was queued for {name}.",
                'started': f"I saw {name} accept the instruction.",
                'response': f"I recorded a reply from {name}.",
            }[status]
            reply = known + " It's unavailable now, so I can't check further progress."
        elif status == 'response':
            # A saved receipt survives restarts, but its source may have rotated,
            # been edited, or disappeared. Do not promise a currently valid reply.
            try:
                preview = self.hub.preview(agent['id'], history=True)
            except Exception:
                preview = {}
            source = record.get('response', {})
            found = preview.get('source') == 'original' and any(
                visible(m) and m['role'] == 'assistant' and m.get('id') == source.get('message_id')
                and digest(m['text']) == source.get('digest') for m in preview.get('messages', []))
            if not found:
                reply = f"I recorded a reply from {name}, but can't verify its source now. Open the conversation to check."
        return {'reply': reply, 'command_id': record['id'], 'status': status}

    def activity_reply(self, query, selected, agents):
        """Only an explicit question about this conversation's fresh activity."""
        if question_text(query) not in ACTIVITY_QUESTIONS or not selected or not self.eligible():
            return None
        agent = next((a for a in agents if a['id'] == selected), None)
        if not agent:
            return None
        if not agent.get('connection_online'):
            return "Its host is offline. I can't confirm whether it's still working."
        observed = agent.get('observed_at', 0)
        if not isinstance(observed, (int, float)) or not 0 <= self.clock() - observed <= 10:
            return None
        if agent.get('activity_confirmed'):
            return "It's still working on the current turn."
        recovery = agent.get('recovery') or {}
        if recovery.get('kind') == 'capacity' and recovery.get('status') in ('retrying', 'checking', 'waiting'):
            return "It's waiting for model capacity. Automatic retries are on."
        if agent.get('capacity_error'):
            return "It's waiting for model capacity. Automatic retries are paused." if recovery.get('status') == 'paused' else None
        if agent.get('state') == 'done':
            return "Its latest turn has ended."
        if not agent.get('live', True):
            return "The agent has exited. Its saved conversation is still available."
        # Input requests and unfamiliar failures need the actual question/error,
        # not an inferred answer from a generic state label.
        return None

    def update(self, value):
        self.require_owner()
        action = value.get('action')
        if action == 'close':
            with self.lock:
                record = next((r for r in self.values('assistant_commitments') if r['id'] == value.get('id')), None)
                if not record:
                    raise BridgeError('That instruction is no longer tracked.')
                record.update(hidden=True, status='closed', updated=self.clock())
                self.put('assistant_commitments', record)
            return self.state()
        change = {'type': action, 'id': value.get('id', ''), 'kind': value.get('kind', ''),
                  'text': value.get('text', ''), 'agent_id': value.get('agent_id', ''),
                  'mode': value.get('mode', ''), 'minutes': value.get('minutes', 120)}
        self.apply([change], value.get('revision'), {'kind': 'settings', 'quote': value.get('text', ''), 'created': self.clock()})
        return self.state()

    def apply(self, changes, revision, source, query=None, agents=None):
        """All-or-nothing, bounded edits. Only current user text may create memory."""
        self.require_owner()
        if not isinstance(changes, list) or len(changes) > 4:
            raise BridgeError('Use up to four assistant changes at a time.')
        if not changes:
            return []
        now, receipts = self.clock(), []
        with self.lock, self.hub.db() as db:
            db.execute('BEGIN IMMEDIATE')
            current = json.loads(db.execute("SELECT value FROM assistant_settings WHERE id='revision'").fetchone()[0])
            if type(revision) is not int or revision != current:
                raise BridgeError('Your assistant settings changed. Refresh before editing them.')
            for change in changes:
                if not isinstance(change, dict):
                    raise BridgeError('Invalid assistant change.')
                kind, text = change.get('type'), change.get('text', '')
                if not isinstance(text, str) or len(text) > 320 or any(ord(c) < 32 for c in text):
                    raise BridgeError('Keep saved requests to 320 characters on one line.')
                quote = change.get('quote', '') if query is not None else text
                if query is not None and (not isinstance(quote, str) or not quote.strip() or len(quote) > 1000 or quote not in query or (text and text not in quote)):
                    raise BridgeError('The saved request must use your current words exactly.')
                origin = {**source, 'quote': quote, 'created': now}
                aid = change.get('agent_id', '')
                if not isinstance(aid, str) or (aid and aid not in {a['id'] for a in (agents if agents is not None else self.hub.snapshot(include_notifications=False)['agents'])}):
                    raise BridgeError('Choose a current agent or leave the request unassigned.')
                if kind == 'remember':
                    if change.get('kind') not in ('priority', 'watch') or not text.strip():
                        raise BridgeError('Save a priority or a watch request.')
                    identity = change.get('id') or uuid.uuid4().hex[:16]
                    if not isinstance(identity, str) or len(identity) > 40:
                        raise BridgeError('Invalid saved request.')
                    rows = [json.loads(r[0]) for r in db.execute('SELECT value FROM assistant_records')]
                    if change.get('id') and identity not in {r['id'] for r in rows}:
                        raise BridgeError('That saved request changed. Refresh first.')
                    same = next((r for r in rows if r['text'] == text and r['kind'] == change['kind'] and r['agent_id'] == aid), None)
                    identity = same['id'] if same else identity
                    if len(rows) >= 12 and identity not in {r['id'] for r in rows}:
                        raise BridgeError('Twelve requests are saved. Remove an old one first.')
                    record = dict(id=identity, kind=change['kind'], text=text, agent_id=aid, source=origin, updated=now)
                    db.execute('INSERT OR REPLACE INTO assistant_records VALUES(?,?)', (identity, json.dumps(record)))
                    receipts.append("I’ve saved that priority." if change['kind'] == 'priority' else "I’ll watch for that at handoffs.")
                elif kind == 'forget':
                    deleted = db.execute('DELETE FROM assistant_records WHERE id=?', (change.get('id', ''),)).rowcount
                    if not deleted:
                        raise BridgeError('That saved request changed. Refresh first.')
                    receipts.append('I’ve removed that saved request.')
                elif kind == 'attention':
                    mode, minutes = change.get('mode'), change.get('minutes', 120)
                    if mode not in MODES or type(minutes) is not int or not (1 <= minutes <= 1440 or (mode == 'normal' and minutes == 0)):
                        raise BridgeError('Choose an attention mode for 1 to 1,440 minutes.')
                    if mode == 'focus' and not text.strip():
                        raise BridgeError('Name the work you want to focus on.')
                    attention = dict(mode=mode, text=text, agent_id=aid, until=now + minutes * 60 if mode != 'normal' else 0, source=origin)
                    db.execute("INSERT OR REPLACE INTO assistant_settings VALUES('attention',?)", (json.dumps(attention),))
                    receipts.append({'normal': 'All handoff updates are on.', 'quiet': f'Updates held for {minutes} minutes. They stay in your inbox.',
                                     'driving': f'Driving mode for {minutes} minutes. Brief spoken handoffs while your call is open.',
                                     'focus': f'For {minutes} minutes, only updates about “{text}” will interrupt you.'}[mode])
                else:
                    raise BridgeError('Unsupported assistant change.')
            db.execute("UPDATE assistant_settings SET value=? WHERE id='revision'", (str(current + 1),))
        self.hub.live.wake()
        return receipts

    def begin(self, command, source):
        """Persist before delivery. A crash never grants permission to resend."""
        if not self.eligible() or command.get('type') not in ('send', 'queue', 'interrupt', 'launch'):
            return
        aid = command.get('agent_id', '')
        try:
            preview = self.hub.preview(aid, history=True) if aid else {}
        except Exception:
            preview = {}
        users = [m['id'] for m in preview.get('messages', []) if visible(m) and m['role'] == 'user' and isinstance(m.get('id'), str)]
        record = dict(id=command['command_id'], agent_id=aid, type=command['type'], text=command.get('message', ''),
                      source=source, created=self.clock(), updated=self.clock(), status='sending',
                      anchor=users[-1] if users else '', baseline_verified=preview.get('source') == 'original',
                      baseline_users=users[-100:], matched='', result='Awaiting delivery confirmation.')
        self.put('assistant_commitments', record)

    def delivered(self, command_id, result=None, error=''):
        with self.lock:
            record = next((r for r in self.values('assistant_commitments') if r['id'] == command_id), None)
            if not record or record.get('hidden'):
                return
            status = 'unconfirmed' if error else 'queued' if result.get('delivery') == 'queued' else 'delivered'
            record.update(status=status, updated=self.clock(), result=error or result.get('message', 'Delivery acknowledged.'))
            if result and result.get('launched'):
                record['launched'] = result['launched']
            self.put('assistant_commitments', record)

    def progress(self, record, before):
        # A slow remote read must not undo a dismissal or a newer receipt.
        with self.hub.db() as db:
            db.execute('UPDATE assistant_commitments SET value=? WHERE id=? AND value=?',
                       (json.dumps(record), record['id'], before))
        self.hub.live.wake()

    def track(self, agents, command_ids=None):
        by_id = {a['id']: a for a in agents}
        def trackable(record):
            # A lost acknowledgement is not proof of failed delivery. Reconcile
            # exact transcript evidence for a day, without ever resending input.
            return not record.get('hidden') and (record['status'] not in TERMINAL or (
                record['status'] == 'unconfirmed' and record.get('baseline_verified')
                and self.clock() - record['created'] < 86400))
        active = [r for r in self.values('assistant_commitments') if trackable(r)
                  and (command_ids is None or r['id'] in command_ids)]
        # Bound work without starving older requests. Several instructions can share one read.
        active.sort(key=lambda r: r.get('checked', 0))
        previews = {}
        for saved in active[:12]:
            record = next((r for r in self.values('assistant_commitments') if r['id'] == saved['id']), None)
            if not record or not trackable(record):
                continue
            before = json.dumps(record)
            record['checked'] = self.clock()
            if record['status'] == 'sending':
                # Receipt from a crash can be reconciled; never execute the instruction again.
                with self.hub.db() as db:
                    receipt = db.execute('SELECT status,result FROM command_receipts WHERE id=?', (record['id'],)).fetchone()
                if receipt and receipt['status'] == 'complete':
                    self.delivered(record['id'], json.loads(receipt['result']))
                elif self.clock() - record['created'] > 120:
                    self.delivered(record['id'], error='Delivery is unconfirmed. Check the agent before resending.')
                continue
            if not record['agent_id'] and record.get('launched'):
                matches = [a for a in agents if a['connection_id'] == record['launched']['connection_id'] and a.get('pane') == record['launched']['pane'] and a.get('session') == record['launched']['session']]
                if len(matches) == 1:
                    record['agent_id'] = matches[0]['id']
            agent = by_id.get(record['agent_id'])
            if not agent or not agent.get('connection_online'):
                record['observation'] = 'Host or agent unavailable; outcome is unconfirmed.'
                self.progress(record, before)
                continue
            record.pop('observation', None)
            if record['type'] == 'interrupt':
                if not agent.get('activity_confirmed') and agent.get('state') not in ('running', 'unknown') and agent.get('recovery', {}).get('status') not in ('retrying', 'checking', 'waiting'):
                    record.update(status='paused', result='The agent is no longer working. This does not confirm task completion.')
            else:
                aid = record['agent_id']
                if aid not in previews:
                    try:
                        previews[aid] = self.hub.preview(aid, history=True)
                    except Exception:
                        previews[aid] = {}
                preview = previews[aid]
                messages = [m for m in preview.get('messages', []) if visible(m)] if preview.get('source') == 'original' else []
                ids = [m['id'] for m in messages]
                # A missing pre-delivery anchor is uncertainty, never proof that old text is new.
                after = ids.index(record['anchor']) + 1 if record['anchor'] in ids else 0 if record['baseline_verified'] and not record['anchor'] else None
                if record.get('launched') and not record['matched']:
                    after = 0  # The exact newly-created session is bound above.
                if not record['matched'] and after is not None:
                    matches = [m for m in messages[after:] if m['role'] == 'user' and m['id'] not in record['baseline_users'] and m['text'].strip() == record['text'].strip()]
                    if len(matches) == 1:
                        record['matched'] = matches[0]['id']
                if record['matched'] in ids:
                    tail = messages[ids.index(record['matched']) + 1:]
                    next_user = next((i for i, m in enumerate(tail) if m['role'] == 'user'), len(tail))
                    reply = next((m for m in reversed(tail[:next_user]) if m['role'] == 'assistant' and m.get('phase') not in ('commentary', 'analysis')), None)
                    record.update(status='started', result='The agent accepted this exact instruction.')
                    # A queued/newer turn may start between observations. An
                    # explicit final answer before that next user boundary still
                    # belongs to this instruction, regardless of the current turn.
                    closed_answer = next_user < len(tail) and reply and reply.get('phase') == 'final_answer'
                    if reply and (closed_answer or (settled(agent) and preview.get('observation', {}).get('state') == agent['state'])):
                        record.update(status='response', result='A response is available. Task completion still needs review.',
                                      response={'message_id': reply['id'], 'digest': digest(reply['text'])})
                elif self.clock() - record['created'] > 86400:
                    record.update(status='unconfirmed', result='No matching instruction was observed. Check the conversation.')
            record['updated'] = self.clock()
            self.progress(record, before)

    def reports(self):
        with self.hub.db() as db:
            rows = db.execute("SELECT value FROM assistant_reports ORDER BY json_extract(value,'$.created') DESC LIMIT 200").fetchall()
        return {r['id']: r for r in (json.loads(row[0]) for row in rows)}

    def may_interrupt(self, row, report=None, state=None, *, wait_for_summary=True):
        state = state or self.state()
        mode = state.get('attention', {})
        if mode.get('mode') == 'quiet':
            return False
        if wait_for_summary and not report and row.get('kind') in ('waiting', 'blocked', 'done', 'exited') and row['created'] >= self.since and self.clock() - row['created'] < 75:
            return False
        if wait_for_summary and report and report.get('status') in ('pending', 'thinking') and self.clock() - row['created'] < 75:
            return False
        if report and report.get('status') == 'superseded':
            return False
        if mode.get('mode') == 'focus':
            return bool((mode.get('agent_id') and mode['agent_id'] == row.get('agent_id')) or
                        (report and report.get('focus_relevant') and report.get('revision') == state['revision']))
        return True

    def decorate(self, rows):
        if not self.eligible():
            return rows
        reports, state = self.reports(), self.state()
        for row in rows:
            report = reports.get(row['id'])
            if report and report.get('summary'):
                row.update(body=report['summary'], assistant_summary=True, original_reply=report.get('original_reply', False),
                           sources=report.get('sources', []))
            # The generic visual alert can open the source immediately. Speech
            # and the contextual brief can still wait for a useful summary.
            row['interrupt'] = self.may_interrupt(row, report, state, wait_for_summary=False)
            row['held'] = not row['interrupt']
        return rows

    def enrich_handoffs(self, items):
        if not self.eligible():
            return items
        state = self.state()
        reports = {r.get('handoff_id'): r for r in self.reports().values()}
        for item in items:
            report = reports.get(item['id'])
            item['interrupt'] = self.may_interrupt({**item, 'created': item['created']}, report, state)
            if report and report.get('status') == 'ready':
                item['brief'] = report['summary']
                item['preferred'] |= bool(report.get('relevant_ids'))
        items.sort(key=lambda i: (i['deferred_until'] > self.clock(), not i['preferred'], {'waiting': 0, 'blocked': 1, 'done': 2, 'exited': 3}[i['kind']], i['created'], i['id']))
        return items

    def model(self, evidence):
        executable = shutil.which(self.hub.provider_executable or self.hub.provider)
        if self.hub.provider != 'codex' or not executable:
            raise BridgeError('Handoff summaries require the configured Codex supervisor.')
        with self.lock, self.hub.db() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute("SELECT value FROM assistant_settings WHERE id='model_calls'").fetchone()
            calls = [stamp for stamp in (json.loads(row[0]) if row else []) if stamp > self.clock() - 3600]
            if len(calls) >= 48:
                raise BridgeError('Hourly handoff summary budget reached.')
            db.execute("INSERT OR REPLACE INTO assistant_settings VALUES('model_calls',?)", (json.dumps(calls + [self.clock()]),))
        command = [executable, 'exec', '--model', self.hub.supervisor_model, '--ignore-user-config', '--skip-git-repo-check',
                   '--ephemeral', '--sandbox', 'read-only', '--json', '--output-schema', str(ROOT / 'handoff-schema.json'),
                   '-c', 'model_reasoning_effort="low"', '-c', 'notify=[]', '-c', 'features.apps=false', '-c', 'features.multi_agent=false',
                   '-c', 'features.shell_tool=false', '-c', 'tools.view_image=false', '-c', 'web_search="disabled"',
                   '-c', 'features.apply_patch_freeform=false', '-c', 'features.code_mode=false',
                   '-c', 'skills.max_context_tokens=1', '-c', 'project_doc_max_bytes=0', '-c', 'features.hooks=false',
                   '-c', 'model_instructions_file=' + json.dumps(str(ROOT / 'handoff-instructions.txt')), '-']
        command[2:2] = [part for setting in READ_ONLY_CONFIG for part in ('-c', setting)]
        env = {k: v for k, v in os.environ.items() if k not in ('TMUX', 'TMUX_PANE')}
        process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                   text=True, cwd=self.hub.state_dir, env=env, start_new_session=True)
        with self.hub.lock:
            self.hub.processes.add(process)
        try:
            stdout, _ = process.communicate(json.dumps(evidence, ensure_ascii=False), timeout=45)
            if process.returncode:
                raise BridgeError('Handoff summary unavailable; the source response is still available.')
            return report_response(stdout)
        except subprocess.TimeoutExpired as exc:
            process.kill()
            process.communicate()
            raise BridgeError('Handoff summary timed out.') from exc
        finally:
            with self.hub.lock:
                self.hub.processes.discard(process)

    def summarize(self, notice, agents):
        agent = next((a for a in agents if a['id'] == notice['agent_id']), None)
        report = {**self.reports().get(notice['id'], {}), 'id': notice['id'], 'handoff_id': notice['target'].get('handoff_id'),
                  'created': notice['created'], 'updated': self.clock()}
        if not settled(agent) or notice['target'].get('version') != f"{agent['state']}:{agent.get('changed_at', 0)}":
            report['status'] = 'superseded'
            self.put('assistant_reports', report)
            return
        preview = self.hub.preview(agent['id'], history=True)
        messages = [m for m in preview.get('messages', []) if visible(m)] if preview.get('source') == 'original' else []
        last_user = next((i for i in range(len(messages) - 1, -1, -1) if messages[i]['role'] == 'user'), -1)
        replies = [m for m in messages[last_user + 1:] if m['role'] == 'assistant' and m.get('phase') not in ('analysis', 'commentary')]
        if not replies or preview.get('observation', {}).get('state') != agent['state']:
            report.update(status='unavailable', attempts=2)
            self.put('assistant_reports', report)
            return
        selected = ([messages[last_user]] if last_user >= 0 else []) + [replies[-1]]
        earlier = next((m for m in messages[:max(last_user, 0)] if m['role'] == 'user'), None)
        if earlier:
            selected.insert(0, earlier)
        state = self.state()
        original_reply = len(replies[-1]['text'].split()) <= SHORT_REPLY_WORDS
        # Short answers need no rewrite. Semantic watch/focus matching is still
        # available, but it cannot change the text shown to the user.
        match_context = (any(not r['agent_id'] or r['agent_id'] == agent['id'] for r in state['records']) or
                         (state['attention']['mode'] == 'focus' and not state['attention'].get('agent_id')))
        cached = None
        source_digests = {m['id']: digest(m['text']) for m in selected}
        if (report.get('summary') and report.get('revision') == state['revision'] and report.get('sources') and
            replies[-1]['id'] in {s['message_id'] for s in report['sources']} and
            all(source_digests.get(s['message_id']) == s['digest'] for s in report['sources'])):
            # Upgrade old pending handoffs without generating another summary or
            # spending tokens to reassess unchanged source/context evidence.
            cached = dict(summary=report['summary'], source_ids=[s['message_id'] for s in report['sources']],
                          relevant_ids=report.get('relevant_ids', []), focus_relevant=report.get('focus_relevant', False))
        needs_model = cached is None and (not original_reply or match_context)
        calls = [stamp for stamp in self.setting('model_calls', []) if stamp > self.clock() - 3600]
        if needs_model and len(calls) >= 48:
            report.update(status='unavailable', retry_at=min(calls) + 3600)
            self.put('assistant_reports', report)
            return
        # Quote head and tail without inventing a bridge across omitted content.
        evidence = {'preserve_reply': original_reply,
                    'agent': {k: agent.get(k) for k in ('id', 'title', 'connection_label', 'state')},
                    'records': [{k: r[k] for k in ('id', 'kind', 'text', 'agent_id')} for r in state['records']], 'attention': state['attention'],
                    'messages': [{'id': m['id'], 'role': m['role'], 'text': m['text'][:1200] if m['role'] == 'user' else m['text'] if len(m['text']) <= 4500 else m['text'][:3000] + '\n[Middle omitted]\n' + m['text'][-1500:]} for m in selected]}
        report.update(status='thinking', attempts=report.get('attempts', 0) + 1, fingerprint=agent.get('activity_fingerprint', ''))
        self.put('assistant_reports', report)
        try:
            result = cached or (self.model(evidence) if needs_model else {'source_ids': [replies[-1]['id']], 'relevant_ids': [], 'focus_relevant': False})
            summary = replies[-1]['text'] if original_reply else result.get('summary')
            if isinstance(summary, str) and summary.strip() and not useful_summary(replies[-1]['text'], summary):
                original_reply, summary = True, replies[-1]['text']
            sources = result.get('source_ids')
            if not isinstance(summary, str) or not summary.strip() or (not original_reply and (len(summary) > 600 or len(summary.split()) > 75)) or not isinstance(sources, list) or not sources or any(not isinstance(s, str) for s in sources) or not set(sources) <= {m['id'] for m in selected} or replies[-1]['id'] not in sources:
                raise BridgeError('Invalid handoff summary.')
            fresh = self.hub.preview(agent['id'], history=True)
            fresh_messages = {m['id']: m for m in fresh.get('messages', []) if visible(m)}
            current = next((a for a in self.hub.snapshot(include_notifications=False)['agents'] if a['id'] == agent['id']), None)
            user_changed = last_user >= 0 and [m['id'] for m in fresh_messages.values() if m['role'] == 'user'][-1:] != [messages[last_user]['id']]
            reply_changed = [m['id'] for m in fresh_messages.values() if m['role'] == 'assistant' and m.get('phase') not in ('analysis', 'commentary')][-1:] != [replies[-1]['id']]
            if (not self.eligible() or not settled(current) or current.get('changed_at') != agent.get('changed_at') or
                fresh.get('source') != 'original' or
                fresh.get('observation', {}).get('state') != agent['state'] or
                any(m['id'] not in fresh_messages or digest(fresh_messages[m['id']]['text']) != digest(m['text']) for m in selected) or
                user_changed or reply_changed):
                report['status'] = 'superseded'
            else:
                relevant = result.get('relevant_ids', [])
                if not isinstance(relevant, list):
                    raise BridgeError('Invalid relevance evidence.')
                report.update(status='ready', summary=summary if original_reply else summary.strip(), original_reply=original_reply,
                              short_reply_words=SHORT_REPLY_WORDS, summary_policy=SUMMARY_POLICY, revision=state['revision'],
                              relevant_ids=[r['id'] for r in state['records'] if r['id'] in relevant and (not r['agent_id'] or r['agent_id'] == agent['id'])],
                              focus_relevant=result.get('focus_relevant') is True,
                              sources=[{'message_id': m['id'], 'digest': digest(m['text'])} for m in selected if m['id'] in sources])
                self.error = ''
        except Exception:
            report.update(status='unavailable', retry_at=self.clock() + 120)
            self.error = 'Some handoff summaries are unavailable. Open the source response for details.'
        report['updated'] = self.clock()
        self.put('assistant_reports', report)

    def refresh_cached_reply(self, notice, agents):
        """Apply the short-reply policy to old handoffs by exact source, not by turn recency."""
        report = self.reports().get(notice['id'], {})
        if not self.eligible() or not report.get('summary'):
            return
        agent = next((a for a in agents if a['id'] == notice['agent_id']), None)
        if not agent or not agent.get('connection_online'):
            return
        preview = self.hub.preview(agent['id'], history=True)
        sources = {s['message_id']: s['digest'] for s in report.get('sources', [])}
        replies = [m for m in preview.get('messages', []) if visible(m) and m['role'] == 'assistant'
                   and m['id'] in sources and digest(m['text']) == sources[m['id']]] if preview.get('source') == 'original' else []
        current = self.reports().get(notice['id'], {})
        if not self.eligible() or current.get('sources') != report.get('sources') or current.get('summary') != report['summary']:
            return
        # Keep status, focus relevance, read/handled state and interruption policy.
        # If the exact old source is no longer available, keep its saved report.
        if len(replies) == 1 and not useful_summary(replies[0]['text'], report['summary']):
            current.update(summary=replies[0]['text'], original_reply=True)
        current.update(short_reply_words=SHORT_REPLY_WORDS, summary_policy=SUMMARY_POLICY, updated=self.clock())
        self.put('assistant_reports', current)

    def tick(self, background=False):
        if not self.eligible():
            return
        agents = self.hub.snapshot(include_notifications=False)['agents']
        if self.clock() - self.last_track > 6:
            self.track(agents)
            self.last_track = self.clock()
        if not self.hub.watcher.enabled:
            return
        with self.hub.db() as db:
            rows = [dict(r) for r in db.execute("SELECT * FROM notifications WHERE created>=? AND handled_at IS NULL AND kind IN ('waiting','blocked','done','exited') ORDER BY created DESC LIMIT 40", (max(self.since, self.clock() - 86400),))]
        reports = self.reports()
        by_id = {a['id']: a for a in agents}
        attention = self.state()
        for row in rows:
            row['target'] = json.loads(row['target'])
            agent = by_id.get(row['agent_id'])
            report = reports.get(row['id'], {})
            if report and report.get('status') != 'superseded' and (not settled(agent) or row['target'].get('version') != f"{agent['state']}:{agent.get('changed_at', 0)}" or
                    (report.get('fingerprint') and agent.get('activity_fingerprint') != report['fingerprint'])):
                report.update(status='superseded', updated=self.clock())
                self.put('assistant_reports', report)
            elif row['id'] not in reports:
                self.put('assistant_reports', dict(id=row['id'], handoff_id=row['target'].get('handoff_id'), status='pending', created=row['created'], attempts=0))
            elif report.get('status') == 'ready' and (report.get('short_reply_words') != SHORT_REPLY_WORDS or report.get('summary_policy') != SUMMARY_POLICY or
                    (attention['attention']['mode'] == 'focus' and report.get('revision') != attention['revision'])):
                report.update(status='pending', attempts=0, retry_at=0)
                self.put('assistant_reports', report)
        if self.hub.supervisor_busy:
            return  # Interactive requests always take priority.
        reports = self.reports()
        for row in rows:
            report = reports.get(row['id'], {})
            refresh = (report.get('status') == 'superseded' and report.get('summary') and
                       (report.get('short_reply_words') != SHORT_REPLY_WORDS or report.get('summary_policy') != SUMMARY_POLICY) and by_id.get(row['agent_id'], {}).get('connection_online'))
            if refresh or (report.get('status') not in ('ready', 'superseded') and report.get('attempts', 0) < 2 and report.get('retry_at', 0) <= self.clock()):
                if background:
                    if self.summary_lock.acquire(blocking=False):
                        def summarize(value=row, refresh=refresh):
                            try:
                                (self.refresh_cached_reply if refresh else self.summarize)(value, agents)
                            except Exception:
                                self.error = 'A handoff summary is unavailable. The source response is still saved.'
                            finally:
                                self.summary_lock.release()
                        threading.Thread(target=summarize, daemon=True).start()
                else:
                    (self.refresh_cached_reply if refresh else self.summarize)(row, agents)
                break
        with self.hub.db() as db:
            db.execute("DELETE FROM assistant_reports WHERE json_extract(value,'$.created')<?", (self.clock() - 30 * 86400,))
            db.execute("DELETE FROM assistant_commitments WHERE id IN (SELECT id FROM assistant_commitments WHERE json_extract(value,'$.status') IN ('response','paused','closed','unconfirmed') ORDER BY json_extract(value,'$.created') DESC LIMIT -1 OFFSET 100)")

    def start(self):
        def watch():
            while not self.hub.stop.wait(3):
                try:
                    self.tick(background=True)
                except Exception:
                    self.error = 'Assistant observation interrupted; retrying. Saved requests are kept.'
        threading.Thread(target=watch, daemon=True).start()
