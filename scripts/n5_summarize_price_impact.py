"""Make readable CSVs from a completed, hash-checked impact review."""
import argparse
import json
from pathlib import Path

import pandas as pd

from src.models.n5_readiness import verify_manifest, write_manifest


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--review-dir', type=Path, required=True)
    ap.add_argument('--output-dir', type=Path, required=True)
    args = ap.parse_args()
    root, out = args.review_dir, args.output_dir
    if out.exists():
        raise FileExistsError('Choose a new directory')
    if verify_manifest(root):
        raise ValueError('Source bundle hash mismatch')
    registry_path = Path('data/processed/aave/reserve_config.parquet')
    registry = pd.read_parquet(registry_path)[['token_address', 'symbol']].drop_duplicates()
    if registry.token_address.duplicated().any():
        raise ValueError('Ambiguous token symbols')
    symbols = registry.set_index('token_address').symbol.to_dict()
    paths = [root/n for n in ['heldout_summary.parquet', 'targeted_quote_requests.parquet',
        'pool_round_sale_demand.parquet', 'paired_execution_comparison.parquet', 'summary.json', 'execution_summary.parquet']]
    cv, requests, flow, comparison = [pd.read_parquet(p) for p in paths[:4]]
    def label(d):
        d = d.copy()
        d['token_in'] = d.token_in_address.map(symbols).fillna(d.token_in_address)
        d['token_out'] = d.token_out_address.map(symbols).fillna(d.token_out_address)
        return d
    cv, requests, flow = label(cv), label(requests), label(flow)
    cv = cv.rename(columns={'mean': 'heldout_mae_percentage_points', 'max': 'heldout_max_error_percentage_points'})
    cv['acceptance'] = 'candidate_only; endpoint and sparse-domain validation remain required'
    best = cv.groupby(['snapshot_id', 'token_in_address', 'token_out_address']).heldout_mae_percentage_points.idxmin()
    candidates = cv.loc[best].copy()
    candidates['choice_scope'] = 'lower MAE on held-out interior sizes only; not approved across full quote domain'
    runs = pd.read_parquet(paths[-1])
    cap = runs[runs.method.ne('no_impact')].groupby(['snapshot_id', 'scenario_id']).hit_round_limit.any()
    comparison = comparison.merge(cap.rename('either_method_hit_round_limit').reset_index(), on=['snapshot_id', 'scenario_id'], validate='one_to_one')
    requests['next_owner'] = requests.reason.map(lambda r: 'N4/N5 approve exact route, then N3 quote' if r == 'missing_exact_route' else 'N5 prioritize by demand, then N3 targeted quotes')
    requests['coordinate_note'] = 'USD at snapshot; convert to input token amount with snapshot input oracle'
    aggregate = flow.groupby(['snapshot_id', 'method', 'token_in', 'token_out'], as_index=False).agg(
        max_pool_round_sale_snapshot_usd=('total_sold_snapshot_usd', 'max'),
        max_individual_sale_snapshot_usd=('max_individual_sale_snapshot_usd', 'max'))
    report = dict(status='readable_review_not_model_acceptance',
        request_rows=len(requests), request_counts=requests.reason.value_counts().to_dict(),
        changed_repayment_pairs=int(comparison.simulated_repaid_usd_delta_linear_minus_power.abs().gt(.01).sum()),
        changed_profit_pairs=int(comparison.liquidator_profit_usd_delta_linear_minus_power.abs().gt(.01).sum()),
        changed_round_pairs=int(comparison.rounds_delta_linear_minus_power.ne(0).sum()),
        pairs_either_method_hit_round_limit=int(comparison.either_method_hit_round_limit.sum()),
        changed_round_pairs_without_round_limit=int((comparison.rounds_delta_linear_minus_power.ne(0) & ~comparison.either_method_hit_round_limit).sum()),
        max_absolute_repayment_difference_usd=float(comparison.simulated_repaid_usd_delta_linear_minus_power.abs().max()),
        max_absolute_profit_difference_usd=float(comparison.liquidator_profit_usd_delta_linear_minus_power.abs().max()),
        caveat='Comparisons use restricted exact routes with fixed shocked oracle, assumed gas/fee and unknown unsupported execution; no official cascade or observed freeze claims')
    out.mkdir(parents=True)
    artifacts = {}
    for name, d in [('heldout_methods.csv', cv), ('interior_method_candidates.csv', candidates), ('targeted_quote_requests.csv', requests),
        ('n3_existing_routes_only.csv', requests[requests.reason.ne('missing_exact_route')]),
        ('routing_decisions_before_new_quotes.csv', requests[requests.reason.eq('missing_exact_route')]),
        ('sale_demand_by_direction.csv', aggregate), ('paired_execution_comparison.csv', comparison)]:
        d.to_csv(out/name, index=False, encoding='utf-8-sig')
        artifacts[name] = 'diagnostic'
    (out/'summary.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    artifacts['summary.json'] = 'diagnostic'
    write_manifest(out, [*paths, root/'manifest.json', registry_path, Path(__file__), Path('src/models/n5_readiness.py')], artifacts, report)
    if verify_manifest(out):
        raise ValueError('Manifest mismatch')
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
