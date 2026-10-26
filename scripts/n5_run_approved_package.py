"""Run the approved bounded research assumptions, preserving prior runs."""
import argparse
import hashlib
import json
from pathlib import Path

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import numpy as np
import yaml

from src.models.n5_inputs import prepare_portfolio, read_rows, resolve_account_reference
from src.models.n5_n3_update import calibrate_quote_grid
from src.models.n5_impact_review import KEYS, ExecutionCurve, heldout_quotes, simulate_execution
from src.models.n5_execution_scope import scope_points, planned_quote_inventory
from src.models.n5_readiness import reconcile_debt, write_manifest, verify_manifest
from src.models.research_tolerances import evaluate_research_gate, load_tolerances
from src.models.stress_test import _asset_multiplier


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--scope-config', type=Path, default=Path('config/n5_execution_scope_approved.json'))
    ap.add_argument('--output-dir', type=Path, required=True)
    ap.add_argument('--additional-quotes', type=Path)
    ap.add_argument('--require-complete-quotes', action='store_true')
    args = ap.parse_args()
    out = args.output_dir
    if out.exists():
        raise FileExistsError('Choose a new output directory')
    scope = json.loads(args.scope_config.read_text())
    if (scope['pool_state'], scope['ordering'], scope['pair_rule']) != ('cumulative_across_rounds', 'lowest_hf', 'bonus_minus_impact'):
        raise ValueError('Runner requires the approved primary package')
    variants = scope['experiments']
    if len({v['variant'] for v in variants}) != len(variants) or variants[0] != dict(variant='primary', method='linear'):
        raise ValueError('Invalid experiment identities')
    root = Path('data/processed/aave')
    reference = resolve_account_reference(root)
    names = ['positions_long', 'reserve_config', 'oracle_prices', 'emode_membership', 'protocol_reconciliation']
    paths = [root/f'{name}.parquet' for name in names]
    config_path, scenarios_path = Path('config/snapshots.json'), Path('config/scenarios.yaml')
    qr = Path('outputs/n3_priority2_quote_grid_v1')
    quote_path, anchor_path = qr/'n3_multivolume_rpc_verified_only.parquet', qr/'n3_2pct_observed_anchors.parquet'
    depth_path = Path('data/staging/n3_dex_liquidity_A_B1_B2.parquet')
    request_path = Path(scope['pending_quote_requests'])
    if hashlib.sha256(request_path.read_bytes()).hexdigest() != scope['pending_quote_requests_sha256']:
        raise ValueError('Request hash mismatch')
    sources = [Path(__file__), *[Path('src/models')/name for name in ['n5_inputs.py', 'n5_n3_update.py',
        'n5_impact_review.py', 'n5_execution_scope.py', 'n5_readiness.py', 'research_tolerances.py', 'stress_test.py', 'cascade.py']]]
    inputs = [args.scope_config, config_path, scenarios_path, quote_path, anchor_path, depth_path,
        request_path, reference, Path('config/n5_tolerances.json'), Path('src/data/fetch_dex_liquidity.py'),
        Path('scripts/n3_priority2_multivolume_quotes.py'), *paths, *sources]
    scenario_policy=json.loads(Path(scope['scenario_policy_path']).read_text()) if scope.get('scenario_policy_path') else {'scenario_overrides':{}}
    if scope.get('scenario_policy_path'):inputs.append(Path(scope['scenario_policy_path']))
    if args.additional_quotes:
        inputs.append(args.additional_quotes)
    hashes = {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in inputs}
    config = json.loads(config_path.read_text())
    scenarios = yaml.safe_load(scenarios_path.read_text())['scenarios']
    requests, quotes = pd.read_csv(request_path), pd.read_parquet(quote_path)
    if args.additional_quotes:
        added = pd.read_parquet(args.additional_quotes)
        for row in added.to_dict('records'):
            expected = requests
            for key in KEYS:
                expected = expected[expected[key].eq(row[key])]
            if not np.isclose(expected.suggested_size_snapshot_usd, row['trade_size_usd'], rtol=1e-7, atol=1e-8).any():
                raise ValueError('Unplanned additional quote')
        quotes = pd.concat([quotes, added], ignore_index=True)
    _, residuals, qa = calibrate_quote_grid(quotes, pd.read_parquet(anchor_path), pd.read_parquet(depth_path), config,
        quantization_policy='conservative_raw_unit_bound')
    points = scope_points(residuals.rename(columns={'observed_impact_pct': 'impact_pct'})[KEYS+['trade_size_usd', 'impact_pct','raw_observed_impact_pct','point_status']], scope, config)
    fees = quotes.groupby(KEYS).fee_tier.agg(['min', 'max']).reset_index()
    fee_lookup = {}
    for row in fees.to_dict('records'):
        # Ignore Curve directions outside the selected three Uniswap pools.
        if pd.isna(row['min']):
            continue
        if row['min'] != row['max'] or row['min'] != int(row['min']) or not 0 <= row['min'] < 1e6:
            raise ValueError('Conflicting or invalid same-pool fee tiers')
        fee_lookup[tuple(row[k] for k in KEYS)] = row['min']/1e6
    inventory = planned_quote_inventory(requests, quotes)
    pending = int(inventory.receipt_status.eq('pending_collection').sum())
    if args.require_complete_quotes and pending:
        raise ValueError(f'{pending} planned quotes pending; no completed-27 release')
    raw = {name: read_rows(path) for name, path in zip(names, paths)}
    refs = pd.read_parquet(reference)
    out.mkdir(parents=True)
    artifacts, gates = {}, {}
    def save(name, table):
        table.to_parquet(out/name, index=False)
        artifacts[name] = 'bounded_counterfactual_diagnostic'
    inventory.to_csv(out/'planned_quote_inventory.csv', index=False, encoding='utf-8-sig')
    artifacts['planned_quote_inventory.csv'] = 'receipt_inventory'
    save('quote_domain_limits.parquet', points.groupby(KEYS).trade_size_usd.agg(['min', 'max', 'count']).reset_index())
    save('curve_points_with_quantization_audit.parquet',points)
    save('heldout_quote_predictions.parquet', heldout_quotes(points))
    all_summaries, all_rounds, all_tx, all_exclusions = [], [], [], []
    context_strings = ['snapshot_id', 'scenario_id', 'variant', 'method']
    attempt_schema = pa.schema([(k, pa.string()) for k in context_strings+['wallet_address', 'token_in_address', 'token_out_address', 'pool_address', 'status']]
        + [('snapshot_block', pa.int64()), ('round', pa.int64())]
        + [(k, pa.float64()) for k in ['sale_snapshot_usd', 'cumulative_before_snapshot_usd', 'required_after_snapshot_usd']])
    with pq.ParquetWriter(out/'unsupported_attempts.parquet', attempt_schema) as writer:
        for sid, snap in config['snapshots'].items():
            portfolio = prepare_portfolio(raw['positions_long'], raw['reserve_config'], raw['oracle_prices'],
                snapshot_block=snap['block'], emode_membership=raw['emode_membership'],
                depth_usd={r['token_address']: 1e6 for r in raw['reserve_config']})
            _, totals = reconcile_debt(portfolio, raw['protocol_reconciliation'])
            selected, _, _, gate = evaluate_research_gate(pd.DataFrame(portfolio.positions), refs, totals, load_tolerances())
            if gate['status'] != 'passed':
                raise ValueError(f'{sid}: input gate failed')
            gates[sid] = gate
            keep = set(selected.wallet_address)
            wallets = [w for w in portfolio.wallets if w.wallet in keep]
            routes = {}
            for identity, g in points[points.snapshot_id.eq(sid)].groupby(KEYS):
                _, _, pool, ct, dt = identity
                routes[ct, dt] = dict(pool_address=pool, curve=ExecutionCurve(g), fee_rate=fee_lookup[identity])
            for scenario_id, params in scenarios.items():
                params=dict(params or {},**scenario_policy['scenario_overrides'])
                multipliers = {t: _asset_multiplier(portfolio.symbols[t].upper(), params or {}) for t in portfolio.assets}
                for variant in variants:
                    settings = dict(method=variant['method'], gas=variant.get('gas', scope['gas_cost_usd_assumed']),
                        fee=variant.get('fee', 0.), max_rounds=scope['max_rounds'],
                        allowed_routes=set(routes), pool_state=variant.get('pool_state', scope['pool_state']),
                        ordering=variant.get('ordering', scope['ordering']), pair_rule=scope['pair_rule'], seed=variant.get('seed', scope['seed']))
                    settings['use_route_fees'] = variant.get('use_route_fees', True)
                    s, r, tx, missing = simulate_execution(wallets, portfolio.assets, multipliers, routes, **settings)
                    context = dict(snapshot_id=sid, snapshot_block=snap['block'], scenario_id=scenario_id,
                        variant=variant['variant'], method=variant['method'])
                    all_summaries.append(dict(s, **{k: v for k, v in context.items() if k != 'method'}, gas_usd=settings['gas'],
                        fee_policy='verified_pool_tier' if settings['use_route_fees'] else 'zero_fee_sensitivity'))
                    all_rounds.extend(dict(context, **row) for row in r)
                    all_tx.extend(dict(context, **row) for row in tx)
                    if missing:
                        rows = [dict(context, **row) for row in missing]
                        writer.write_table(pa.Table.from_pylist(rows, schema=attempt_schema))
                        d = pd.DataFrame(missing)
                        for reason, part in d.groupby('status'):
                            all_exclusions.append(dict(context, reason=reason, attempts=len(part),
                                wallets_with_unsupported_candidates=int(part.wallet_address.nunique())))
                print(f'{sid} {scenario_id}: {len(variants)} variants complete', flush=True)
    artifacts['unsupported_attempts.parquet'] = 'unknown_execution_candidates_not_freeze'
    summaries = pd.DataFrame(all_summaries)
    save('execution_summary.parquet', summaries)
    save('execution_rounds.parquet', pd.DataFrame(all_rounds))
    save('execution_transactions.parquet', pd.DataFrame(all_tx))
    save('unsupported_candidate_summary.parquet', pd.DataFrame(all_exclusions))
    primary = summaries[summaries.variant.eq('primary')]
    columns = ['simulated_repaid_usd', 'liquidator_profit_usd', 'executed_liquidations', 'rounds', 'hit_round_limit']
    comparison = summaries.merge(primary[['snapshot_id', 'scenario_id']+columns], on=['snapshot_id', 'scenario_id'], suffixes=('', '_primary'), validate='many_to_one')
    for column in columns[:-1]:
        comparison[f'{column}_delta_vs_primary'] = comparison[column]-comparison[f'{column}_primary']
    for name, d in [('primary_results.csv', primary), ('sensitivity_vs_primary.csv', comparison),
        ('unsupported_candidates.csv', pd.DataFrame(all_exclusions))]:
        d.to_csv(out/name, index=False, encoding='utf-8-sig'); artifacts[name] = 'readable_diagnostic'
    report = dict(status='approved_package_run_complete_quotes_pending' if pending else 'approved_package_run_complete_with_planned_quotes',
        scope=scope, runs=len(summaries), primary_runs=len(primary), input_gates=gates,
        planned_quotes_pending=pending, planned_quotes_received=27-pending, quote_qa=qa,
        round_limits_by_variant=summaries.groupby('variant').hit_round_limit.sum().astype(int).to_dict(),
        max_rounds_by_variant=summaries.groupby('variant').rounds.max().astype(int).to_dict(),
        pair_rule_definition='Among supported positive-USD-profit candidates maximize bonus premium minus incremental average execution slippage, both fractions; ties by USD profit then sorted token iteration',
        random_order_definition='Same seed and full initial-wallet permutation schedule per round across paired runs',
        fee_status=scope['fee_status'], oracle_feedback=False, freeze_proven=False,
        interpretation='Bounded execution under fixed shocked oracle and zero DEX/oracle basis assumption. Unknown alternatives can coexist with executed pairs. Reset is sensitivity only; no observed gas or causal freeze claims. A completed planned-quote run requires all 27 observations.')
    (out/'summary.json').write_text(json.dumps(report, indent=2, allow_nan=False), encoding='utf-8')
    artifacts['summary.json'] = 'run_status'
    if any(hashlib.sha256(p.read_bytes()).hexdigest() != h for p, h in hashes.items()):
        raise ValueError('Source changed during run')
    write_manifest(out, inputs, artifacts, report)
    if verify_manifest(out):
        raise ValueError('Manifest mismatch')
    print(json.dumps({k: report[k] for k in ['status', 'runs', 'primary_runs', 'planned_quotes_pending', 'round_limits_by_variant']}, indent=2))


if __name__ == '__main__':
    main()
