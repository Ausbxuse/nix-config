#!/usr/bin/env python3
"""Read-only lookup profiling; model calls are opt-in and use an isolated chat.

python3 pkgs/agent-hub/profile_lookup.py --query 'Which agent runs the ablation?'
Add --model-runs 3 to measure model usage and identical-answer reuse. Add
--fresh-model to measure independent model calls instead of answer-cache hits.
"""
import argparse
import json
from pathlib import Path
import tempfile
import time
from unittest.mock import patch
from urllib.parse import urlencode
from urllib.request import urlopen

from lookup import LookupCache
from projects import search_agents
from server import Hub


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--query', required=True)
    parser.add_argument('--url', default='http://127.0.0.1:8765')
    parser.add_argument('--fixture', type=Path, help='Private JSON with snapshot and previews, for reproducible runs')
    parser.add_argument('--model-runs', type=int, choices=range(0, 6), default=0)
    parser.add_argument('--fresh-model', action='store_true')
    parser.add_argument('--node-config', type=Path, default=Path.home() / '.config/agent-hub/node.json')
    args = parser.parse_args()

    def fetch(path):
        with urlopen(args.url.rstrip('/') + path, timeout=45) as response:
            return json.load(response)

    fixture = json.loads(args.fixture.read_text()) if args.fixture else None
    snapshot = fixture['snapshot'] if fixture else fetch('/api/state')
    previews = fixture['previews'] if fixture else {}

    def read(agent_id, **kwargs):
        if fixture:
            return previews[agent_id]
        value = fetch('/api/preview?' + urlencode({'agent_id': agent_id, 'history': 1}))
        previews[agent_id] = value
        return value

    # Keep one snapshot for comparable retrieval runs. Cache verification still
    # rejects any history whose capture differs from that observation.
    agents = snapshot['agents']
    by_id = {a['id']: a for a in agents}
    cache = LookupCache()
    for mode in ('direct', 'cache'):
        for run in range(3):
            before = cache.stats()
            cache.prune(agents)
            preview = read if mode == 'direct' else lambda id, **kw: cache.preview(by_id[id], read)
            start = time.perf_counter()
            result = search_agents(agents, preview, args.query, budget=6000)
            print(json.dumps({'stage': 'retrieval', 'mode': mode, 'run': run + 1,
                              'ms': round((time.perf_counter() - start) * 1000, 2),
                              'agents': len(agents), 'searched': result['searched_histories'],
                              'unavailable': len(result['unavailable']),
                              'evidence_characters': len(json.dumps(result, ensure_ascii=False)),
                              **({k: v - before[k] for k, v in cache.stats().items()} if mode == 'cache' else {})}), flush=True)

    if not args.model_runs:
        return
    config = json.loads(args.node_config.read_text())
    with tempfile.TemporaryDirectory(prefix='agent-hub-profile-') as directory, patch.object(Hub, 'save_connection'):
        hub = Hub(directory)
        hub.provider_executable = config['codex_binary']
        # Live runs re-check observations on each request, exactly as the hub
        # does. A fixture explicitly freezes both snapshot and source evidence.
        hub.snapshot = (lambda: snapshot) if fixture else (lambda: fetch('/api/state'))
        hub.preview = read
        try:
            for run in range(args.model_runs):
                if args.fresh_model:
                    hub.lookup_cache.answers.clear()
                job_id = str(run)
                hub.jobs[job_id] = {'status': 'running', 'phase': 'gathering', 'started_at': time.time()}
                start = time.perf_counter()
                hub.supervise(job_id, args.query, '', False)
                job = hub.jobs[job_id]
                print(json.dumps({'stage': 'supervisor', 'run': run + 1, 'status': job['status'],
                                  'ms': round((time.perf_counter() - start) * 1000, 2),
                                  **job.get('lookup_metrics', {}),
                                  'reply': job.get('reply'), 'error': job.get('error')}), flush=True)
        finally:
            hub.close()


if __name__ == '__main__':
    main()
