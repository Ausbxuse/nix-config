"""Session-only Claude model controls through its verified local picker.

The picker is the authority for model names and effort levels. Never use the
slash command with arguments: that changes defaults for future sessions.
"""
import hashlib
import json
import re
import time

import bridge


LEVELS = {'low':'low', 'medium':'medium', 'high':'high', 'extra high':'xhigh', 'xhigh':'xhigh',
          'max':'max', 'maximum':'max', 'ultracode':'ultracode'}
EFFORT_ORDER = ('low', 'medium', 'high', 'xhigh', 'max', 'ultracode')


def picker(screen):
    headings = list(re.finditer(r'^\s*Select model\s*$', screen, re.M))
    if not headings:
        return None
    body = screen[headings[-1].end():]
    if not re.search(r'Enter to set as default\s*·\s*s to use this session only\s*·\s*Esc to cancel', body):
        return None
    rows = []
    for match in re.finditer(r'^\s*(❯\s*)?(\d+)\.\s+(.+?)(?:\s{2,}(.*))?$', body, re.M):
        label = match[3].replace('✔', '').strip()
        rows.append({'id':label, 'label':label, 'number':int(match[2]), 'selected':bool(match[1]),
                     'current':'✔' in match[3], 'description':match[4] or ''})
    selected = [row for row in rows if row['selected']]
    if len(selected) != 1 or not 1 <= len(rows) <= 20 or len({row['id'] for row in rows}) != len(rows):
        return None
    level = re.search(r'^\s*(?:[^\w\s]+\s*)?([A-Za-z]+(?: [A-Za-z]+)?) effort\b', body, re.I | re.M)
    # Keep an unknown label distinct from an unsupported slider. It must fail
    # discovery, while still allowing us to close the recognized picker.
    effort = LEVELS.get(level[1].lower(), level[1].lower()) if level else None
    return {'rows':rows, 'selected':selected[0]['id'], 'effort':effort}


def empty_editor(agent, screen):
    try:
        lines = screen.splitlines();row = int(agent['cursor_y'])
        return (agent.get('in_mode') == '0' and int(agent['cursor_x']) == 2 and 0 < row < len(lines)-1 and
                re.fullmatch(r'❯\s*', lines[row]) is not None and
                bridge.EDITOR_BORDER.fullmatch(lines[row-1]) is not None and
                bridge.EDITOR_BORDER.fullmatch(lines[row+1]) is not None)
    except (KeyError, ValueError, IndexError):
        return False


def effort_cache_confirmation(screen, effort):
    """Only the cache notice for the effort the user just chose, focused on Yes.

    A PreModelSwitch hook, consent request, different choice, or user moving the
    selection to No must never be accepted by this adapter.
    """
    expected = 'xhigh' if effort == 'ultracode' else effort
    if expected not in EFFORT_ORDER:
        return False
    headings = list(re.finditer(r'^\s*Change effort level\?\s*$', screen, re.M))
    if not headings:
        return False
    body = ' '.join(screen[headings[-1].end():].split())
    level = re.escape(expected)
    return re.fullmatch(
        r'Your next response will be slower and use more tokens '
        r'This conversation is cached for the current effort level\. Switching to ' + level +
        r' means the full history gets re-read on your next message\. '
        r'❯ 1\. Yes, switch to ' + level + r' 2\. No, go back', body, re.I) is not None


class ClaudeModels:
    def __init__(self, sessions):
        self.sessions = sessions
        self.catalog = {}

    @staticmethod
    def screen(connection, agent):
        return bridge.run(connection, ['capture-pane', '-p', '-t', agent['pane'], '-S', '0'])

    def wait_picker(self, connection, agent, previous=None, timeout=.6):
        end = time.monotonic() + timeout
        last = None
        while time.monotonic() < end:
            last = picker(self.screen(connection, agent))
            if last and (previous is None or (last['selected'], last['effort']) != previous):
                return last
            time.sleep(.035)
        if last:
            return last
        raise bridge.BridgeError('Claude’s model picker changed. Check the terminal before trying again.')

    def step(self, connection, agent, key, value):
        # Navigation keys only; never submit a model with Enter.
        if key not in ('Up', 'Down', 'Left', 'Right', 'Home'):
            raise bridge.BridgeError('Unsupported model picker key')
        if not picker(self.screen(connection, agent)):
            raise bridge.BridgeError('The model picker closed. No selection was sent.')
        bridge.run(connection, ['send-keys', '-t', agent['pane'], key])
        return self.wait_picker(connection, agent, (value['selected'], value['effort']), timeout=.3)

    def choose(self, connection, agent, value, identity):
        ids = [row['id'] for row in value['rows']]
        if identity not in ids:
            raise bridge.BridgeError('This model is no longer listed by Claude.')
        for _ in range(len(ids)):
            if value['selected'] == identity:
                return value
            key = 'Down' if ids.index(identity) > ids.index(value['selected']) else 'Up'
            before = value['selected'];value = self.step(connection, agent, key, value)
            if value['selected'] == before:
                break
        raise bridge.BridgeError('Claude did not select that model. No change was applied.')

    def levels(self, connection, agent, value):
        if value['effort'] is None:
            return [], None, value
        default = value['effort']
        if default not in EFFORT_ORDER:
            raise bridge.BridgeError('Claude’s effort choices could not be verified.')
        levels = {default}
        # Current Claude wraps low ← ultracode and ultracode → low. Older
        # pickers stop at their endpoints. Discover either without assuming
        # that an unchanged arrow is the only way to find the last choice.
        for key in ('Right', 'Left'):
            seen = {value['effort']}
            for _ in range(len(EFFORT_ORDER) + 1):
                before = value['effort'];value = self.step(connection, agent, key, value)
                effort = value['effort']
                if effort not in EFFORT_ORDER:
                    raise bridge.BridgeError('Claude’s effort choices could not be verified.')
                if effort in seen:
                    if effort != before or key == 'Left':
                        return [level for level in EFFORT_ORDER if level in levels], default, value
                    break
                seen.add(effort);levels.add(effort)
        raise bridge.BridgeError('Claude’s effort choices could not be verified.')

    def settings(self, connection, expected_agent, change=None):
        agent = bridge.verify_agent(connection, expected_agent)
        binding = self.sessions.binding(agent, connection)
        if not binding:
            raise bridge.BridgeError('This Claude session could not be verified. Refresh and try again.')
        if change and change.get('session') != binding['session']:
            raise bridge.BridgeError('The Claude session changed. Refresh before applying.')
        screen = self.screen(connection, agent)
        # /model is an immediate local command, including during a running
        # turn. The empty editor check still protects drafts and other menus.
        if not empty_editor(agent, screen):
            raise bridge.BridgeError('Finish the terminal draft or close its menu before changing model settings.')
        bridge.paste(connection, agent, '/model')
        try:
            value = self.wait_picker(connection, agent)
            model, effort = value['selected'], value['effort']
            version = hashlib.sha256(json.dumps([binding['session'], model, effort]).encode()).hexdigest()
            key = (binding['pid'], binding['start'], tuple(row['id'] for row in value['rows']))
            models = self.catalog.get(key)
            if change and change.get('version') != version:
                raise bridge.BridgeError('Model settings changed in the terminal. Refresh before applying.')
            if models is None:
                models = []
                for row in value['rows']:
                    if re.search(r'not available|requires usage credits|unavailable', row['description'], re.I):
                        continue
                    value = self.choose(connection, agent, value, row['id'])
                    levels, _, value = self.levels(connection, agent, value)
                    # Probing the previous row leaves its slider at maximum;
                    # that is not this model's recommended default. Preserve
                    # the session's effort when the new model supports it.
                    default = effort if effort in levels else levels[0] if levels else None
                    models.append({'id':row['id'], 'label':row['label'], 'efforts':levels, 'default_effort':default})
                self.catalog = {key:models}
            if change:
                choice = next((row for row in models if row['id'] == change.get('model')), None)
                if not choice or (change.get('effort') not in choice['efforts'] if choice['efforts'] else change.get('effort') is not None):
                    raise bridge.BridgeError('Choose a model and effort listed by this Claude session.')
                value = self.choose(connection, agent, value, choice['id'])
                levels, _, value = self.levels(connection, agent, value)
                if levels != choice['efforts']:
                    self.catalog.clear()
                    raise bridge.BridgeError('Effort choices changed. Refresh before applying.')
                for _ in range(len(levels)):
                    if value['effort'] == change.get('effort'):
                        break
                    key = 'Right' if levels.index(change['effort']) > levels.index(value['effort']) else 'Left'
                    value = self.step(connection, agent, key, value)
                if value['effort'] != change.get('effort'):
                    raise bridge.BridgeError('Claude did not select that effort. No change was applied.')
                current = bridge.verify_agent(connection, agent)
                if self.sessions.binding(current, connection) != binding or picker(self.screen(connection, current)) != value:
                    raise bridge.BridgeError('This terminal changed. No selection was sent.')
                # The advertised session-only key applies both choices without
                # changing user settings, including through a cache notice.
                bridge.run(connection, ['send-keys', '-t', agent['pane'], 's'])
                end = time.monotonic() + 2
                cache_accepted = False
                while time.monotonic() < end:
                    screen = self.screen(connection, agent)
                    current = bridge.verify_agent(connection, agent)
                    if self.sessions.binding(current, connection) != binding:
                        raise bridge.BridgeError('The Claude session changed. Refresh to check its settings.')
                    if not cache_accepted and effort_cache_confirmation(screen, change.get('effort')):
                        # Apply already authorizes this effort change. Accept
                        # only its exact cache notice; Enter in a model picker
                        # would save global defaults and is never sent there.
                        if effort_cache_confirmation(self.screen(connection, current), change.get('effort')):
                            bridge.run(connection, ['send-keys', '-t', agent['pane'], 'Enter'])
                            cache_accepted = True
                    elif empty_editor(current, screen):
                        break
                    time.sleep(.04)
                else:
                    raise bridge.BridgeError('Claude did not confirm the settings. Check its terminal before retrying.')
                # Re-read the picker before claiming success; never infer it
                # from a command echo or an older status line.
                bridge.paste(connection, current, '/model')
                confirmed = self.wait_picker(connection, current)
                if confirmed['selected'] != choice['id'] or confirmed['effort'] != change.get('effort'):
                    raise bridge.BridgeError('Claude did not confirm these settings. Refresh to check them.')
                model, effort = confirmed['selected'], confirmed['effort']
                version = hashlib.sha256(json.dumps([binding['session'], model, effort]).encode()).hexdigest()
            return {'runtime':'claude', 'session':binding['session'], 'model':model, 'effort':effort,
                    'version':version, 'models':models, 'note':'This session only. Applies to Claude’s next request.'}
        finally:
            if picker(self.screen(connection, agent)):
                bridge.run(connection, ['send-keys', '-t', agent['pane'], 'Escape'])
