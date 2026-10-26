"""Readable outputs for a bounded scope run; no research acceptance promotion."""
import argparse
import hashlib
import json
from pathlib import Path

import pandas as pd

from src.models.n5_readiness import verify_manifest, write_manifest


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--run-dir', type=Path, required=True)
    ap.add_argument('--output-dir', type=Path, required=True)
    args = ap.parse_args()
    if args.output_dir.exists():
        raise FileExistsError('Choose a new output directory')
    if verify_manifest(args.run_dir):
        raise ValueError('Artifact hash mismatch')
    report_path = args.run_dir/'summary.json'
    report = json.loads(report_path.read_text(encoding='utf-8'))
    if report.get('scope') is None:
        raise ValueError('Explicit scope required')
    manifest = json.loads((args.run_dir/'manifest.json').read_text(encoding='utf-8'))
    if any(hashlib.sha256(Path(r['path']).read_bytes()).hexdigest() != r['sha256'] for r in manifest['inputs']):
        raise ValueError('Run source changed since execution')
    primary = report['primary_method']
    names = ['execution_summary', 'planned_quote_inventory', 'quote_domain_limits', 'unscorable_execution_attempts']
    paths = [args.run_dir/f'{name}.parquet' for name in names]
    runs, inventory, limits, attempts = [pd.read_parquet(p) for p in paths]
    selected = runs[runs.method.eq(primary)]
    attempts = attempts[attempts.method.eq(primary)]
    exclusions = attempts.groupby(['snapshot_id', 'scenario_id', 'status'], as_index=False).agg(
        candidate_attempts=('wallet_address', 'size'), wallets_with_unsupported_candidates=('wallet_address', 'nunique'))
    # Wallets can also have supported candidates and can appear under several
    # reasons/rounds. Do not label these counts as mutually exclusive coverage.
    exclusions['interpretation'] = 'overlapping candidate diagnostics; wallet may also execute another supported pair'
    result = dict(status=report['bounded_run_status'], primary_method=primary,
        primary_scenario_snapshot_runs=len(selected), planned_quotes_received=report['planned_quotes_received'],
        planned_quotes_pending=report['planned_quotes_pending'],
        primary_runs_hit_round_limit=int(selected.hit_round_limit.sum()),
        execution_round_limit=report['execution_assumptions']['max_rounds'],
        research_acceptance='not_official_cascade; fixed oracle and assumed gas/fees/pool reset',
        next_step='Collect pending quotes then rerun with --additional-quotes and --require-complete-quotes' if report['planned_quotes_pending'] else 'Review bounded-run outputs and remaining model assumptions')
    out = args.output_dir
    out.mkdir(parents=True)
    artifacts = {}
    for name, table in [('primary_execution_summary.csv', selected), ('planned_quote_inventory.csv', inventory),
        ('quote_domain_limits.csv', limits), ('unsupported_candidates.csv', exclusions)]:
        table.to_csv(out/name, index=False, encoding='utf-8-sig')
        artifacts[name] = 'bounded_run_diagnostic'
    (out/'summary.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
    artifacts['summary.json'] = 'bounded_run_status'
    write_manifest(out, [*paths, report_path, args.run_dir/'manifest.json', Path(__file__), Path('src/models/n5_readiness.py')], artifacts, result)
    if verify_manifest(out):
        raise ValueError('Output hash mismatch')
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
