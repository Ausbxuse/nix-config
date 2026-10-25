"""Bounded, attributed project evidence for the unified supervisor."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path, PurePosixPath
from collections import OrderedDict
import configparser
import hashlib
import re
import threading
import time
from urllib.parse import urlsplit

from bridge import terminal_content
from task_facts import valid_fields


class ProjectIdentity:
    """Read repository identity locally without spawning git or evaluating config includes."""
    def __init__(self, ttl=30):
        self.ttl, self.cache, self.lock = ttl, OrderedDict(), threading.Lock()

    def key(self, directory, host=''):
        location = str(directory or '').rstrip('/')
        if not location or location in ('/tmp', '/var/tmp') or re.fullmatch(r'/(?:home|Users)/[^/]+', location):
            return ''
        cache_key = (host, location)
        with self.lock:
            cached = self.cache.get(cache_key)
            if cached and time.monotonic() - cached[0] < self.ttl:
                self.cache.move_to_end(cache_key)
                return cached[1]
        identity = 'directory:' + host + ':' + location
        try:
            current = Path(location).resolve()
            for root in [current, *list(current.parents)[:32]]:
                marker = root / '.git'
                if marker.is_dir():
                    gitdir = marker.resolve()
                elif marker.is_file():
                    with marker.open() as file:
                        value = file.read(4096).strip()
                    if not value.startswith('gitdir: '):
                        continue
                    gitdir = (root / value[8:]).resolve()
                else:
                    continue
                common = gitdir
                if (gitdir / 'commondir').is_file():
                    with (gitdir / 'commondir').open() as file:
                        common = (gitdir / file.read(4096).strip()).resolve()
                identity = 'repository:' + host + ':' + str(common)
                config = configparser.RawConfigParser(strict=False)
                if (common / 'config').is_file():
                    with (common / 'config').open() as file:
                        config.read_string(file.read(65536))
                    origin = config.get('remote "origin"', 'url', fallback='').strip().strip('"')
                    scp = re.fullmatch(r'(?:[^/@:]+@)?([^/:]+):(.+)', origin) if '://' not in origin else None
                    url = urlsplit(origin if not scp else 'ssh://' + scp[1] + '/' + scp[2])
                    if url.hostname and url.path and url.scheme in ('ssh','https','http','git'):
                        remote = url.hostname.casefold() + ((':' + str(url.port)) if url.port else '') + '/' + url.path.strip('/').removesuffix('.git')
                        identity = 'origin:' + remote
                break
        except (OSError, ValueError, configparser.Error):
            pass
        result = hashlib.sha256(identity.encode()).hexdigest()[:24]
        with self.lock:
            self.cache[cache_key] = (time.monotonic(), result)
            self.cache.move_to_end(cache_key)
            while len(self.cache) > 128:
                self.cache.popitem(last=False)
        return result


_QUERY_NOISE = set('a an and are as at be been being by can could did do does find for from has have i identify in is it locate look me my of on or please prepare prepared preparing search session sessions some task tasks tell than that the their them there these they this to vs was were what which who will with work working you your agent agents window windows'.split())
_ALIASES = {'stdmean': 'meanstd', 'ckpt': 'checkpoint', 'ckpts': 'checkpoint', 'checkpoints': 'checkpoint'}
_QUERY_NOISE.update('aria supervisor let talk speak chat switch take connect open show conversation works would like want need'.split())


def _tokens(text):
    text = re.sub(r'\b(?:std[\s_-]*mean|mean[\s_-]*std)\b', 'meanstd', text.casefold())
    return {_ALIASES.get(word, word) for word in re.findall(r'[a-z0-9]+', text)}


def _search_excerpt(text, terms, limit):
    if len(text) <= limit:
        return text
    size = max(0, limit - 2)
    if not size:
        return text[:limit]
    positions = {0, max(0, len(text) - size)}
    for word in terms:
        pattern = {'meanstd': r'(?:std[\s_-]*mean|mean[\s_-]*std)',
                   'checkpoint': r'(?:checkpoints?|ckpts?)'}.get(word, re.escape(word))
        hits = list(re.finditer(pattern, text, re.I))
        for hit in hits[:2] + hits[-2:]:
            positions.add(max(0, min(len(text) - size, hit.start() - size // 3)))
    start = max(positions, key=lambda pos: (len(_tokens(text[pos:pos + size]) & terms), pos))
    return ('…' if start else '') + text[start:start + size] + ('…' if start + size < len(text) else '')


def search_agents(agents, preview, query, budget=12000, limit=64):
    """Search matched histories; send the model only bounded, attributed hits."""
    terms = set(sorted(_tokens(query) - _QUERY_NOISE)[:18])
    result = {'terms': sorted(terms), 'total_agents': len(agents), 'searched_histories': 0,
              'unavailable': [], 'matches': [], 'scope': 'Available matched history and terminal captures, not every past session.'}
    if not terms:
        result['note'] = 'No task keywords were supplied. Ask which task to find.'
        return result

    def metadata(agent):
        return ' '.join(str(agent.get(k, '')) for k in ('title', 'display_title', 'connection_label', 'cwd', 'summary'))

    ordered = sorted(agents, key=lambda a: len(_tokens(metadata(a)) & terms), reverse=True)

    def collect(agent):
        hit = {'agent_id': agent['id'], 'title': agent['title'], 'task_name': agent.get('display_title', agent['title']), 'host': agent['connection_label'],
               'state': agent['state'], 'connection_online': agent.get('connection_online', False),
               'live': agent.get('live', True), 'messages': []}
        available = False
        matching = []
        terminal = ''
        if not agent.get('connection_online'):
            hit['unavailable'] = 'Host offline; only last known metadata was checked.'
        else:
            try:
                history = preview(agent['id'], history=True)
                available = True
                messages = [m for m in history.get('messages', []) if m.get('role') in ('user', 'assistant') and
                            not m.get('text', '').startswith(('# AGENTS.md', '<environment_context>', '<permissions instructions>'))]
                matching = [(len(_tokens(m.get('text', '')) & terms), index, m) for index, m in enumerate(messages)]
                matching = [m for m in matching if m[0]]
                chosen = sorted(sorted(matching, key=lambda m: (m[0], m[1]), reverse=True)[:3], key=lambda m: m[1])
                # Keep current instructions even when they omit the search term:
                # "Stop that study; now evaluate the baseline" changes ownership.
                indices = {index for _, index, _ in chosen}
                latest = {role: next((i for i in range(len(messages) - 1, -1, -1) if messages[i]['role'] == role), None)
                          for role in ('user', 'assistant')}
                indices.update(index for index in latest.values() if index is not None)
                for index in sorted(indices):
                    message = messages[index]
                    entry = {k: message.get(k, '') for k in ('role', 'created', 'text')}
                    if index == latest[message['role']]:
                        entry['context'] = 'Latest available instruction' if message['role'] == 'user' else 'Latest available reply'
                    hit['messages'].append(entry)
                hit['history_scope'] = history.get('history_note') or result['scope']
                terminal = terminal_content(history.get('text', ''))
            except Exception:
                hit['unavailable'] = 'History could not be read.'
        native_terms = set().union(*(_tokens(m[2].get('text', '')) & terms for m in matching)) if matching else set()
        terminal_terms = _tokens(terminal) & terms
        metadata_terms = _tokens(metadata(agent)) & terms
        matched = native_terms | terminal_terms | metadata_terms
        if not matching and terminal_terms:
            hit['terminal_excerpt'] = terminal
        hit['matched_terms'] = sorted(matched)
        score = 2 * len(native_terms) + len(terminal_terms - native_terms) + .25 * len(metadata_terms)
        return hit, score, available

    fetched = []
    with ThreadPoolExecutor(max_workers=4) as pool:
        for hit, score, available in pool.map(collect, ordered[:limit]):
            result['searched_histories'] += int(available)
            if hit.get('unavailable'):
                result['unavailable'].append({k: hit[k] for k in ('agent_id', 'title', 'host', 'unavailable')})
            if hit['matched_terms']:
                fetched.append((score, hit))
    if len(ordered) > limit:
        result['unsearched_agents'] = len(ordered) - limit
    matches = [hit for _, hit in sorted(fetched, key=lambda entry: entry[0], reverse=True)[:6]]
    allowance = budget // max(1, len(matches))
    for hit in matches:
        per_message = allowance // max(1, len(hit['messages']))
        for message in hit['messages']:
            text = message['text']
            message['text'] = _search_excerpt(text, terms, per_message)
            message['truncated'] = len(text) > per_message
        if 'terminal_excerpt' in hit:
            hit['terminal_excerpt'] = _search_excerpt(hit['terminal_excerpt'], terms, allowance)
            hit['history_scope'] = 'Terminal capture; a mention alone does not prove this task is assigned here.'
    result['matches'] = matches
    return result


def project_context(agents, preview, selected="", budget=64000):
    groups = {}
    for agent in agents:
        key = (agent['connection_id'], agent['cwd'])
        groups.setdefault(key, []).append(agent)
    # Interleave projects so a large project cannot consume every context slot.
    ordered = []
    remaining = [sorted(group, key=lambda a: a['id'] != selected) for group in groups.values()]
    while any(remaining):
        for group in remaining:
            if group:
                ordered.append(group.pop(0))

    def collect(agent):
        evidence = {'agent_id': agent['id'], 'title': agent['title'], 'task_name': agent.get('display_title', agent['title']), 'state': agent['state'],
                    'reported_status': agent.get('summary', ''), 'messages': []}
        if not agent.get('connection_online'):
            evidence['unavailable'] = 'Host offline; status is last known.'
            return evidence
        try:
            result = preview(agent['id'], history=True)
            messages = result.get('messages', [])
            facts = valid_fields(result.get('facts'), messages)
            if (facts and result.get('facts', {}).get('agent_id') == agent['id'] and
                    agent['state'] in ('done', 'waiting', 'blocked') and not agent.get('activity_confirmed') and
                    result.get('observation', {}).get('state', agent['state']) == agent['state'] and
                    any(field['kind'] == 'change' for field in facts)):
                # Digests/ranges were checked above. Keep the exact quotation and
                # message locator in the prompt, rather than repeating validation
                # metadata the language model cannot use to verify anything.
                evidence['facts'] = [{
                    'kind': field['kind'], 'text': field['text'], 'truncated': field.get('truncated', False),
                    'source': {key: field['source'].get(key, '') for key in ('message_id', 'role', 'created')}
                } for field in sorted(facts, key=lambda f: {'request':0, 'change':1, 'decision':2, 'earlier_request':3}[f['kind']])]
                evidence['source_version'] = result['facts']['version']
                evidence['history_scope'] = result['facts']['scope']
                return evidence
            # Preserve an earlier goal and recent instructions/results. This is
            # an excerpt, never a claim that the full research history was read.
            goals = [m for m in messages if m.get('role') == 'user' and
                     not m.get('text', '').startswith(('# AGENTS.md', '<environment_context>', '<permissions instructions>'))]
            candidates = ([goals[0]] if goals else []) + messages[-6:]
            seen = set()
            for message in candidates:
                key = (message.get('id'), message.get('created'), message.get('text'))
                if key in seen or message.get('role') not in ('user', 'assistant'):
                    continue
                seen.add(key)
                text = message.get('text', '')
                evidence['messages'].append({'id': message.get('id', ''), 'role': message['role'], 'created': message.get('created', ''),
                                             'text': text, 'truncated': False})
            evidence['history_scope'] = result.get('history_note') or 'Available matched messages only.'
            if not evidence['messages']:
                evidence['terminal_excerpt'] = terminal_content(result.get('text', ''))[-2200:]
                evidence['history_scope'] = 'Terminal capture only; project goal may be missing.'
        except Exception:
            evidence['unavailable'] = 'Recent history unavailable.'
            evidence['terminal_excerpt'] = terminal_content(agent.get('preview', ''))[-1600:]
        return evidence

    # Bound retrieval as well as prompt size. All agents still appear in groups.
    fetched = {}
    with ThreadPoolExecutor(max_workers=4) as pool:
        for agent, evidence in zip(ordered[:32], pool.map(collect, ordered[:32])):
            fetched[agent['id']] = evidence
    allowance = min(8000, budget // max(1, len(fetched)))
    for evidence in fetched.values():
        if 'facts' in evidence:
            remaining = allowance
            bounded = []
            for fact in evidence['facts']:
                if len(fact['text']) <= remaining:
                    bounded.append(fact)
                    remaining -= len(fact['text'])
            evidence['facts_truncated'] = len(bounded) != len(evidence['facts'])
            evidence['facts'] = bounded
        messages = evidence['messages']
        per_message = allowance // max(1, len(messages))
        for message in messages:
            text = message['text']
            message['text'] = text if len(text) <= per_message else text[:per_message // 2] + '\n[… excerpt …]\n' + text[-max(0, per_message // 2 - 18):] if per_message > 40 else text[:per_message]
            message['truncated'] = len(text) > per_message
        if 'terminal_excerpt' in evidence:
            evidence['terminal_excerpt'] = evidence['terminal_excerpt'][:allowance]
    return [{'host': group[0]['connection_label'], 'directory': cwd,
             'project_name': PurePosixPath(cwd).name, 'workstreams': [fetched.get(a['id'], {
                 'agent_id': a['id'], 'title': a['title'], 'state': a['state'],
                 'unavailable': 'History not read: context limit.'}) for a in group]}
            for (_, cwd), group in groups.items()]
