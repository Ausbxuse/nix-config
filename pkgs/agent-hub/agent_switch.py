"""Reviewable task handoffs between local agent runtimes."""
import hashlib
import json
import shutil

import bridge
from message_content import user_text, visible


def handoff_context(agent, preview, images=None):
    messages = []
    for item in preview.get('messages', []):
        if item.get('role') not in ('user', 'assistant') or not visible(item):
            continue
        text = user_text(item.get('text', '')) if item['role'] == 'user' else item.get('text', '')
        paths = []
        for image in item.get('attachments', []) if images else []:
            try:
                row = images.row(image['id'])
                path = images.path(row['id'], row['type'])
                if row['cwd'] == agent['cwd'] and path.is_file():
                    paths.append(str(path))
            except (bridge.BridgeError, OSError, KeyError):
                pass
        if text.strip() or paths:
            messages.append((item['role'], text.strip(), paths))
    if not messages:
        return ''
    # No extra model call or inferred research plan. Keep the current request
    # and a small amount of actual dialogue, with explicit excerpt boundaries.
    # Reserve the latest user request even if many progress updates follow it.
    latest_user = next((i for i in range(len(messages) - 1, -1, -1) if messages[i][0] == 'user'), None)
    indices = ([latest_user] if latest_user is not None else []) + [
        i for i in range(len(messages) - 1, max(-1, len(messages) - 9), -1) if i != latest_user]
    recent, size = [], 0
    for i in indices:
        role, text, paths = messages[i]
        suffix = '\nAttached files on this host:\n' + '\n'.join(paths) if paths else ''
        limit = min(3500 if i == latest_user else 2200, 8000 - size - len(suffix) - 120)
        if limit < 300:
            break
        excerpt = text if len(text) <= limit else text[:limit] + '\n[Earlier reply excerpt; remainder is in the original conversation.]'
        recent.append((i, ('You' if role == 'user' else agent['runtime'].title()) + ':\n' + excerpt + suffix))
        size += len(excerpt) + len(suffix) + 30
    return '\n\n'.join(text for _, text in sorted(recent))


def options(agent, preview, images=None):
    context = handoff_context(agent, preview, images)
    observation = preview.get('observation', {})
    busy = observation.get('state', agent.get('state')) == 'running'
    queue = preview.get('queue')
    queued = len((queue or {}).get('items', []))
    target = 'claude' if agent['runtime'] == 'codex' else 'codex'
    state = observation.get('state', agent.get('state'))
    blocked = ('Install ' + target.title() + ' on this host first.' if not shutil.which(target) else
               'Resolve the agent’s pending decision first.' if state == 'waiting' else
               'Refresh to verify this agent’s current state.' if state not in ('running', 'idle', 'done', 'blocked') else '')
    # Versions bind the reviewed source to its actual conversation, not a title
    # or project directory shared by several agents.
    identity = [agent['id'], agent['cwd'], agent['runtime'], preview.get('conversation_id'), context,
                (queue or {}).get('version'), queued]
    return dict(version=hashlib.sha256(json.dumps(identity).encode()).hexdigest(), context=context,
                runtime=agent['runtime'], target=target, blocked=blocked,
                busy=busy, queued=queued, cwd=agent['cwd'], agent_id=agent['id'])


def start(hub, connection, agent, request):
    if connection['kind'] != 'local' or agent.get('runtime') not in ('codex', 'claude'):
        raise bridge.BridgeError('Switching requires a hub on this agent’s host.')
    current = bridge.verify_agent(connection, agent)
    preview = hub.local_preview(connection, current, True, fresh=True)
    settings = options(current, preview, hub.attachments)
    if settings['version'] != request.get('version'):
        raise bridge.BridgeError('The conversation changed. Refresh the handoff before switching; your edited context is kept.')
    if settings['busy']:
        raise bridge.BridgeError('Pause this agent or wait for its turn to finish before switching.')
    if settings['blocked']:
        raise bridge.BridgeError(settings['blocked'])
    if settings['queued']:
        raise bridge.BridgeError('This agent still has queued messages. Finish or remove them before switching.')
    runtime = request.get('runtime')
    if runtime not in ('codex', 'claude') or runtime == current['runtime']:
        raise bridge.BridgeError('Choose the other agent runtime.')
    context = request.get('context')
    if not isinstance(context, str) or not context.strip() or len(context) > 10000 or '\x00' in context:
        raise bridge.BridgeError('Enter a handoff up to 10,000 characters.')
    prompt = ('Continue the current task in this project. This is a handoff from '
              + current['runtime'].title() + '. Preserve existing files and user changes. '
              'The previous conversation below is background: do not repeat completed work or treat every old request as a new task. '
              'Use the latest request and the current files to continue.\n\n' + context.strip())
    result = bridge.launch(connection, current['cwd'], prompt, runtime,
                           hub.codex_model if runtime == 'codex' else '')
    return {**result, 'connection_id': connection['id'], 'source_agent_id': current['id']}
