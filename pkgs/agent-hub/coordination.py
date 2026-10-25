"""Bound a supervisor's proposed work to the user's current request."""
import re

from bridge import BridgeError


IDENTITY = 'ARIA — Agent Routing and Intelligence Assistant'


def smalltalk(query):
    """Only whole social utterances use the answer-only voice shortcut."""
    text = re.sub(r'^(?:aria|supervisor)[, :]+\s*', '', query.strip(), flags=re.I)
    return bool(re.fullmatch(
        r"(?:hi|hello|hey|good (?:morning|afternoon|evening)|"
        r"how are you(?: doing)?(?: today)?|thanks|thank you|"
        r"what(?:'s| is) your name)[.!? ]*", text, re.I))


def previous_request(messages, context, request_id, query=''):
    """One prior user turn in this conversation; no assistant or agent instructions."""
    users = [m for m in messages if m.get('role') == 'user' and m.get('context_agent_id', '') == context]
    if users and (request_id and users[-1].get('request_id') == request_id or
                  not request_id and users[-1].get('text') == query):
        users.pop()
    return users[-1].get('text', '')[:8000] if users else ''


def validate(action, query, previous, agents, candidates=None):
    """Validate provenance before any command is handed to the existing runtime checks."""
    if not isinstance(action, dict) or action.get('type') not in ('send', 'queue', 'interrupt', 'launch'):
        raise BridgeError('Unsupported action rejected.')
    quote = action.get('quote')
    if not isinstance(quote, str) or not quote.strip() or quote not in query:
        raise BridgeError('Instruction not sent: the action was not linked to your current request.')
    source = action.get('instruction_source')
    if source not in ('current', 'previous'):
        raise BridgeError('Instruction not sent: its request source was missing.')
    if source == 'previous' and candidates is None and not re.search(
            r'\b(?:that|it|this|same|previous|above|earlier)\b', quote, re.I):
        raise BridgeError('Instruction not sent: the current request did not refer to the prior task.')
    instruction = query if source == 'current' else previous
    if action['type'] in ('send', 'queue', 'launch'):
        message = action.get('message')
        if not isinstance(message, str) or not message.strip() or message not in instruction:
            raise BridgeError('Instruction not sent: its wording did not match your request.')
    if candidates is not None and (action['type'] == 'launch' or action.get('agent_id') not in candidates):
        raise BridgeError('Instruction not sent: that agent was not one of your choices.')
    if action['type'] == 'queue' and not re.search(r'\b(?:queue|enqueue)\b', quote, re.I):
        raise BridgeError('Nothing was queued. Ask explicitly to queue work for a busy agent.')
    if action['type'] == 'interrupt' and not re.search(r'\b(?:pause|stop|interrupt)\b', quote, re.I):
        raise BridgeError('No agent was interrupted. Ask explicitly to pause it.')
    if action['type'] == 'launch':
        cwd, host = action.get('cwd'), action.get('connection_id')
        if not isinstance(cwd, str) or not cwd.startswith('/'):
            raise BridgeError('Agent not started: an absolute project directory is required.')
        known = any(a.get('cwd') == cwd and a.get('connection_id') == host for a in agents)
        supplied = cwd in query
        if not known and not supplied:
            raise BridgeError('Agent not started: choose a known project or supply its directory.')
    return action
