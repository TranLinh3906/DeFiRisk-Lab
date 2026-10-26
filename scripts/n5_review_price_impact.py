"""Run real-position demand, grouped quote holdout and execution comparisons offline."""
import argparse
import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

from src.models.n5_inputs import prepare_portfolio, read_rows, resolve_account_reference
from src.models.n5_n3_update import calibrate_quote_grid
from src.models.n5_readiness import reconcile_debt, write_manifest, verify_manifest
from src.models.n5_impact_review import KEYS, ExecutionCurve, heldout_quotes, simulate_execution
from src.models.research_tolerances import evaluate_research_gate, load_tolerances
from src.models.stress_test import _asset_multiplier
from src.models.n5_execution_scope import scope_points, planned_quote_inventory


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--output-dir', type=Path, required=True)
    ap.add_argument('--scope-config', type=Path, help='Explicit bounded scope; omit for legacy diagnostic')
    ap.add_argument('--additional-quotes', type=Path, help='Verified raw-schema Parquet for the planned 27 requests')
    ap.add_argument('--require-complete-quotes', action='store_true', help='Fail preflight until all planned quotes are validated')
    args = ap.parse_args()
    out = args.output_dir
    if out.exists():
        raise FileExistsError('Choose a new output directory')
    root = Path('data/processed/aave')
    quote_root = Path('outputs/n3_priority2_quote_grid_v1')
    reference = resolve_account_reference(root)
    paths = {n: root/f'{n}.parquet' for n in ['positions_long', 'reserve_config', 'oracle_prices', 'emode_membership', 'protocol_reconciliation']}
    quote_path, anchor_path = quote_root/'n3_multivolume_rpc_verified_only.parquet', quote_root/'n3_2pct_observed_anchors.parquet'
    depth_path = Path('data/staging/n3_dex_liquidity_A_B1_B2.parquet')
    sources = [Path(p) for p in [__file__, 'src/models/n5_impact_review.py', 'src/models/n5_n3_update.py',
        'src/models/n5_inputs.py', 'src/models/n5_readiness.py', 'src/models/cascade.py',
        'src/models/research_tolerances.py', 'src/models/stress_test.py', 'src/models/n5_execution_scope.py']]
    inputs = [*paths.values(), reference, quote_path, anchor_path, depth_path,
        Path('config/snapshots.json'), Path('config/scenarios.yaml'), Path('config/n5_tolerances.json'), *sources]
    scope = json.loads(args.scope_config.read_text(encoding='utf-8')) if args.scope_config else None
    if (args.additional_quotes or args.require_complete_quotes) and not scope:
        raise ValueError('Additional/required quotes need an explicit scope')
    if scope:
        request_path = Path(scope['pending_quote_requests'])
        if hashlib.sha256(request_path.read_bytes()).hexdigest() != scope['pending_quote_requests_sha256']:
            raise ValueError('Planned quote request hash changed')
        inputs.extend([args.scope_config, request_path])
    if args.additional_quotes:
        inputs.append(args.additional_quotes)
    before = {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in inputs}
    config = json.loads(Path('config/snapshots.json').read_text(encoding='utf-8'))
    scenarios = yaml.safe_load(Path('config/scenarios.yaml').read_text(encoding='utf-8'))['scenarios']
    raw = {n: read_rows(p) for n, p in paths.items()}
    refs = pd.read_parquet(reference)
    quotes = pd.read_parquet(quote_path)
    if args.additional_quotes:
        additional = pd.read_parquet(args.additional_quotes)
        requests = pd.read_csv(request_path)
        for row in additional.to_dict('records'):
            match = requests
            for key in KEYS:
                match = match[match[key].eq(row[key])]
            if not np.isclose(match.suggested_size_snapshot_usd, row['trade_size_usd'], rtol=1e-7, atol=1e-8).any():
                raise ValueError('Additional quote is outside the planned request list')
        quotes = pd.concat([quotes, additional], ignore_index=True)
    fits, residuals, qa = calibrate_quote_grid(quotes, pd.read_parquet(anchor_path), pd.read_parquet(depth_path), config)
    points = residuals.rename(columns={'observed_impact_pct': 'impact_pct'})[KEYS+['trade_size_usd', 'impact_pct']]
    inventory = None
    max_rounds, gas, fee = 30, 5., .0005
    if scope:
        points = scope_points(points, scope, config)
        inventory = planned_quote_inventory(pd.read_csv(request_path), quotes)
        if args.require_complete_quotes and not inventory.receipt_status.eq('received_validated').all():
            raise ValueError('Planned quotes remain pending; no completed-27 run can be released')
        max_rounds, gas, fee = scope['max_rounds'], scope['gas_cost_usd_assumed'], scope['extra_fee_rate_assumed']
        if isinstance(max_rounds, bool) or not isinstance(max_rounds, int) or not 1 <= max_rounds <= 1000:
            raise ValueError('Invalid scoped round limit')
    holdout = heldout_quotes(points)
    scored = holdout[holdout.status.eq('scored')].copy()
    scored['absolute_error_pp'] = scored.error_percentage_points.abs()
    cv_summary = scored.groupby(KEYS+['method']).absolute_error_pp.agg(['count', 'mean', 'max']).reset_index()
    out.mkdir(parents=True)
    artifacts = {}
    def save(name, table, kind='diagnostic'):
        table.to_parquet(out/name, index=False)
        artifacts[name] = kind
    save('heldout_quote_predictions.parquet', holdout)
    save('heldout_summary.parquet', cv_summary)
    save('quote_points.parquet', points, 'observed_source_points')
    save('power_fit_residuals.parquet', residuals)
    save('quote_domain_limits.parquet', points.groupby(KEYS).trade_size_usd.agg(['min', 'max', 'count']).reset_index())
    if inventory is not None:
        save('planned_quote_inventory.parquet', inventory)
    summary, rounds, txs, misses = [], [], [], []
    gates = {}
    for sid, snap in config['snapshots'].items():
        portfolio = prepare_portfolio(raw['positions_long'], raw['reserve_config'], raw['oracle_prices'],
            snapshot_block=snap['block'], emode_membership=raw['emode_membership'],
            depth_usd={r['token_address']: 1e6 for r in raw['reserve_config']})
        _, totals = reconcile_debt(portfolio, raw['protocol_reconciliation'])
        selected, _, _, gate = evaluate_research_gate(pd.DataFrame(portfolio.positions), refs, totals, load_tolerances())
        if gate['status'] != 'passed':
            raise ValueError(f'{sid}: input gate failed')
        gates[sid] = gate
        included = set(selected.wallet_address)
        wallets = [w for w in portfolio.wallets if w.wallet in included]
        routes = {}
        for identity, g in points[points.snapshot_id.eq(sid)].groupby(KEYS):
            _, _, pool, ct, dt = identity
            if (ct, dt) in routes:
                raise ValueError('Ambiguous exact execution route')
            routes[ct, dt] = dict(pool_address=pool, curve=ExecutionCurve(g))
        for scenario_id, params in scenarios.items():
            multipliers = {t: _asset_multiplier(portfolio.symbols[t].upper(), params or {}) for t in portfolio.assets}
            for method in ['no_impact', 'power', 'linear']:
                s, r, t, m = simulate_execution(wallets, portfolio.assets, multipliers, routes, method=method,
                    gas=gas, fee=fee, max_rounds=max_rounds,
                    allowed_routes=set(routes) if scope else None)
                context = dict(snapshot_id=sid, snapshot_block=snap['block'], scenario_id=scenario_id, method=method)
                summary.append(dict(context, **{k: v for k, v in s.items() if k != 'method'}))
                rounds.extend(dict(context, **row) for row in r)
                txs.extend(dict(context, **row) for row in t)
                misses.extend(dict(context, **row) for row in m)
            print(f'{sid} {scenario_id}: compared', flush=True)
    summaries, transactions = pd.DataFrame(summary), pd.DataFrame(txs)
    save('execution_summary.parquet', summaries, 'real_position_counterfactual_diagnostic')
    save('execution_rounds.parquet', pd.DataFrame(rounds), 'real_position_counterfactual_diagnostic')
    save('execution_transactions.parquet', transactions, 'real_position_counterfactual_diagnostic')
    save('unscorable_execution_attempts.parquet', pd.DataFrame(misses))
    group = ['snapshot_id', 'scenario_id', 'method', 'round', 'token_in_address', 'token_out_address']
    flow = transactions.groupby(group, dropna=False).agg(total_sold_usd=('sold_usd', 'sum'),
        total_sold_snapshot_usd=('sale_snapshot_usd', 'sum'), transactions=('wallet_address', 'size'),
        max_individual_sale_snapshot_usd=('sale_snapshot_usd', 'max')).reset_index()
    # Currently one pool per exact direction; explicitly attach pool identity.
    route_keys = points[['snapshot_id', 'token_in_address', 'token_out_address', 'pool_address']].drop_duplicates()
    flow = flow.merge(route_keys, on=['snapshot_id', 'token_in_address', 'token_out_address'], how='left', validate='many_to_one')
    save('pool_round_sale_demand.parquet', flow)
    comparison_keys = ['snapshot_id', 'scenario_id']
    cols = ['simulated_repaid_usd', 'liquidator_profit_usd', 'executed_liquidations', 'rounds', 'unknown_candidate_attempts']
    comparison = summaries[summaries.method.eq('power')][comparison_keys+cols].merge(
        summaries[summaries.method.eq('linear')][comparison_keys+cols], on=comparison_keys,
        suffixes=('_power', '_linear'), validate='one_to_one')
    for col in cols:
        comparison[f'{col}_delta_linear_minus_power'] = comparison[f'{col}_linear']-comparison[f'{col}_power']
    save('paired_execution_comparison.parquet', comparison)
    requests = []
    for identity, demand in flow[flow.method.eq('no_impact')].groupby(['snapshot_id', 'token_in_address', 'token_out_address']):
        sid, ct, dt = identity
        if ct == dt:
            continue  # Same-token repayment has no DEX quote requirement.
        g = points[points.snapshot_id.eq(sid) & points.token_in_address.eq(ct) & points.token_out_address.eq(dt)].sort_values('trade_size_usd')
        max_flow = float(demand.total_sold_snapshot_usd.max())
        min_sale = float(transactions.loc[transactions.method.eq('no_impact') & transactions.snapshot_id.eq(sid) &
            transactions.token_in_address.eq(ct) & transactions.token_out_address.eq(dt), 'sale_snapshot_usd'].min())
        common = dict(snapshot_id=sid, snapshot_block=config['snapshots'][sid]['block'], token_in_address=ct,
            token_out_address=dt, max_round_sale_snapshot_usd=max_flow, min_individual_sale_snapshot_usd=min_sale,
            priority_status='targeted_candidate_request_not_automatic_recollection')
        if g.empty:
            requests.append(dict(common, reason='missing_exact_route', pool_address=None, suggested_size_snapshot_usd=max_flow))
            continue
        pool = g.pool_address.iloc[0]
        if min_sale < g.trade_size_usd.min():
            requests.append(dict(common, reason='below_minimum_observed_quote', pool_address=pool, suggested_size_snapshot_usd=min_sale))
        if max_flow > g.trade_size_usd.max():
            requests.append(dict(common, reason='above_maximum_observed_quote', pool_address=pool, suggested_size_snapshot_usd=max_flow))
        sizes = g.trade_size_usd.to_numpy()
        for lo, hi in zip(sizes[:-1], sizes[1:]):
            if hi/lo > 4 and lo < max_flow and hi > min_sale:
                requests.append(dict(common, reason='sparse_interval_overlapping_candidate_demand', pool_address=pool,
                    lower_observed_snapshot_usd=float(lo), upper_observed_snapshot_usd=float(hi),
                    suggested_size_snapshot_usd=float(np.sqrt(lo*hi))))
    save('targeted_quote_requests.parquet', pd.DataFrame(requests))
    report = dict(status='diagnostic_not_official_cascade', input_gates=gates, quote_qa=qa,
        scenario_snapshot_pairs=len(summaries)//3, execution_runs=len(summaries),
        heldout_scored_rows=len(scored), heldout_unscorable_rows=int(holdout.status.ne('scored').sum()),
        heldout_mae_pp=scored.groupby('method').absolute_error_pp.mean().to_dict(),
        scope=scope, primary_method=scope['primary_method'] if scope else None,
        planned_quotes_received=int(inventory.receipt_status.eq('received_validated').sum()) if inventory is not None else None,
        planned_quotes_pending=int(inventory.receipt_status.eq('pending_collection').sum()) if inventory is not None else None,
        bounded_run_status=('completed_with_validated_planned_quotes' if inventory.receipt_status.eq('received_validated').all() else 'completed_current_data_planned_quotes_pending') if inventory is not None else 'legacy_diagnostic',
        execution_assumptions=dict(max_rounds=max_rounds, gas_usd_per_liquidation=gas, extra_fee_rate=fee, oracle_feedback=False,
            curve_coordinate='input token quantity valued at snapshot oracle USD',
            pool_state='cumulative sales within each round; reset between rounds',
            routing='exact collateral to debt token only; same-token identity needs no DEX',
            ordering='sorted wallet address', policy='research baseline_aave, not complete versioned protocol emulator',
            depeg_scope='N4 scenario mapper, including all listed LSTs',
            quote_curve_shock_assumption='pool token depth unchanged; quoted impact invariant to external shock'),
        coverage='Missing routes/out-of-domain candidates remain unknown, not observed freeze; results are restricted execution diagnostics',
        demand_interpretation='No-impact full-pair research demand is a candidate sizing baseline, not a guaranteed upper bound',
        quote_request_rule='Adjacent-size ratio >4 is exploratory prioritization, not acceptance tolerance',
        remaining='Validate new quotes, shock-state liquidity, routing, gas and oracle feedback before official cascade acceptance')
    (out/'summary.json').write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False), encoding='utf-8')
    artifacts['summary.json'] = 'diagnostic'
    if any(hashlib.sha256(p.read_bytes()).hexdigest() != h for p, h in before.items()):
        raise ValueError('Source changed during review')
    write_manifest(out, inputs, artifacts, report)
    if verify_manifest(out):
        raise ValueError('Manifest mismatch')
    print(json.dumps({k: report[k] for k in ['status', 'execution_runs', 'heldout_mae_pp']}, indent=2))


if __name__ == '__main__':
    main()
