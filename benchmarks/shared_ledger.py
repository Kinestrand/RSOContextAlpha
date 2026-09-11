"""Offline multi-client benchmark. Run from any directory with Python 3.11+."""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import os
from pathlib import Path
import statistics
import subprocess
import sys
import tempfile
import threading
import time

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--agents', type=int, default=3)
    parser.add_argument('--rounds', type=int, default=5)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.agents < 2 or args.rounds < 1:
        parser.error('Use at least two agents and one round.')
    args.output.parent.mkdir(parents=True, exist_ok=True)
    # Exclusive creation protects earlier reports.
    with args.output.open('x', encoding='utf-8') as report_file:
        calls, checks = [], []
        lock = threading.Lock()
        with tempfile.TemporaryDirectory(prefix='rso-benchmark-') as scratch:
            base = Path(scratch)
            env = dict(os.environ, PYTHONPATH=str(ROOT / 'src'),
                       RSO_CONTEXT_HOME=str(base / 'home'), PYTHONUTF8='1')
            env.pop('RSO_CONTEXT_ROOTS', None)
            db = base / 'ledger.sqlite3'

            def call(agent, project, *command):
                argv = [sys.executable, '-B', '-m', 'rso_context', '--db', str(db),
                        *command, '--agent', agent, '--path', str(project)]
                started = time.perf_counter()
                result = subprocess.run(argv, cwd=ROOT, env=env, capture_output=True,
                                        text=True, encoding='utf-8', timeout=60)
                record = {'agent': agent, 'command': list(command),
                          'elapsed_ms': round((time.perf_counter()-started)*1000, 2),
                          'exit_code': result.returncode, 'stderr': result.stderr}
                try:
                    record['result'] = json.loads(result.stdout)
                except ValueError:
                    record['stdout'] = result.stdout
                with lock:
                    calls.append(record)
                if result.returncode or 'result' not in record:
                    raise RuntimeError(f'CLI failed: {record}')
                return record['result']

            def check(name, passed):
                checks.append({'name': name, 'passed': bool(passed)})

            def evidence(packet):
                return '\n'.join(e.get('text', '') for e in packet.get('evidence', [])
                                 if not e.get('stale', False))

            def concurrent(project, *command):
                barrier = threading.Barrier(args.agents)
                def worker(index):
                    barrier.wait(timeout=30)
                    return call(f'benchmark-client-{index}', project, *command)
                with ThreadPoolExecutor(max_workers=args.agents) as pool:
                    return list(pool.map(worker, range(args.agents)))

            try:
                alpha, beta = base / 'alpha', base / 'beta'
                for project, color in ((alpha, 'amber'), (beta, 'violet')):
                    project.mkdir()
                    (project / 'AGENTS.md').write_text(
                        f'# {project.name}\nDecision: delivery beacon must be {color}.\n',
                        encoding='utf-8')
                # Warm initialization is explicit: this does not test first-open races.
                call('benchmark-coordinator', alpha, 'use')
                call('benchmark-coordinator', beta, 'use')
                initial = concurrent(alpha, 'query', 'delivery beacon')
                check('all clients retrieve alpha, never beta', all(
                    'amber' in evidence(p) and 'violet' not in evidence(p) for p in initial))
                check('source claims remain observed', all(
                    p.get('claims') and all(c['trust_state'] == 'observed' for c in p['claims'])
                    and not p.get('validations') for p in initial))

                (alpha / 'AGENTS.md').write_text(
                    '# alpha\nDecision: delivery beacon must be turquoise.\n', encoding='utf-8')
                before = concurrent(alpha, 'query', 'delivery beacon')
                check('un-ingested edit is not current evidence', all(
                    'amber' not in evidence(p) and 'turquoise' not in evidence(p) for p in before))
                call('benchmark-client-0', alpha, 'use')
                refreshed = concurrent(alpha, 'query', 'delivery beacon')
                check('one client refresh reaches every client', all(
                    'turquoise' in evidence(p) and 'amber' not in evidence(p) for p in refreshed))
                cold = call('benchmark-new-reader', alpha, 'query', 'delivery beacon')
                check('fresh identity retrieves current source', 'turquoise' in evidence(cold))

                concurrent(alpha, 'propose', 'Delivery beacon should be silver.')
                pending = call('benchmark-coordinator', alpha, 'pending')
                check('proposals remain proposed', 'proposed' in json.dumps(pending)
                      and 'silver' in json.dumps(pending))
                after = call('benchmark-reviewer', alpha, 'query', 'delivery beacon')
                check('agreement does not create validation', not after.get('validations')
                      and all(c['trust_state'] != 'verified' for c in after.get('claims', [])))

                # Per-agent run budget and simultaneous reads/writes, with no retries.
                for index in range(args.agents):
                    call(f'benchmark-client-{index}', alpha, 'run-budget', '--set', str(args.rounds + 10))
                for round_index in range(args.rounds):
                    concurrent(alpha, 'propose', f'Round {round_index} delivery beacon candidate.')
                    packets = concurrent(alpha, 'query', 'delivery beacon')
                    check(f'round {round_index} current evidence', all(
                        'turquoise' in evidence(p) and 'violet' not in evidence(p) for p in packets))
                budgets = concurrent(alpha, 'run-budget')
                check('agent budgets are independent', all(p['remaining'] == 10 for p in budgets))
                resume = call('benchmark-new-reader', alpha, 'resume')
                check('historical versions retained', resume['takes']['older_versions'] > 0)
            except Exception as exc:
                checks.append({'name': 'execution completed', 'passed': False, 'error': str(exc)})

            durations = sorted(c['elapsed_ms'] for c in calls)
            source_hashes = {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest()
                             for p in sorted((ROOT / 'src' / 'rso_context').glob('*.py'))}
            report = {'schema': 'rso-multi-client-benchmark/v1',
                      'mode': 'scripted CLI clients, not LLM agents or MCP hosts',
                      'agents': args.agents, 'rounds': args.rounds,
                      'python': sys.version, 'source_sha256': source_hashes,
                      'passed': bool(checks) and all(c['passed'] for c in checks),
                      'checks': checks, 'call_count': len(calls),
                      'latency_ms': {'median': statistics.median(durations) if durations else None,
                                     'max': max(durations) if durations else None},
                      'calls': calls}
            json.dump(report, report_file, indent=2)
        print(json.dumps({k: report[k] for k in ('passed', 'checks', 'call_count', 'latency_ms')}, indent=2))
        return 0 if report['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
