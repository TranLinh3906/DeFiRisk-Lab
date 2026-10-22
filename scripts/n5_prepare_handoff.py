"""Prepare a NEW offline N5 review bundle; never alter source data or N4 marts."""
from __future__ import annotations

import argparse
from collections import Counter
import json
import math
from pathlib import Path

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from scripts.n5_audit import finite_json, stable_read
from src.models.cascade import CascadeConfig, POLICIES, synthetic_portfolio
from src.models.n5_experiments import compare_static_cascade, run_sensitivity_analysis, scenario_shocks
from src.models.n5_inputs import prepare_portfolio
from src.models.n5_validation import validate_inputs, assess_historical_readiness
from src.models.n5_readiness import (
    build_aave_graph, diagnose_n4, historical_candidates, inspect_compound,
    map_dex_depth, reconcile_debt, write_manifest, verify_manifest, graph_node_metrics,
)


def build_bundle(output, *, aave, marts, dex_path, history, holdout, compound):
    output = Path(output)
    if output.exists():
        raise FileExistsError('Choose a new output directory')
    input_paths = [aave / f'{name}.parquet' for name in
                   ['positions_long', 'reserve_config', 'oracle_prices', 'emode_membership',
                    'account_reference', 'protocol_reconciliation']]
    input_paths += [aave / 'snapshot_metadata.json', marts / 'borrower_risk.parquet',
                    marts / 'stress_positions.parquet', dex_path, history, holdout,
                    Path('data/processed/graph_network_edges.parquet'),
                    Path('data/processed/fact_historical_liquidations.parquet'),
                    compound / 'merge_report.json']
    input_paths += [compound / f'n3_compound_{name}_partial.parquet' for name in
                    ['positions_long', 'account_reference', 'reserve_config', 'overlap_wallets']]
    price_files = [history.parent / name for name in
                   ['market_daily_continuous.parquet', 'market_crashes_hourly.parquet',
                    'token_prices_continuous.parquet']]
    concentration_path = history.parent / 'concentration_2026/concentration_by_wallet.parquet'
    input_paths += price_files + [concentration_path]
    input_paths += [Path(p) for p in ['scripts/n5_prepare_handoff.py', 'scripts/n5_audit.py',
                    'src/models/n5_readiness.py', 'src/models/n5_inputs.py',
                    'src/models/n5_validation.py', 'src/models/n5_experiments.py',
                    'src/models/cascade.py', 'research_design.md', 'kpi_dictionary.csv']]
    # Exact hashes before reading allow detecting concurrent source updates.
    import hashlib
    fingerprints = {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in input_paths}
    raw = {name: stable_read(aave / f'{name}.parquet') for name in
           ['positions_long', 'reserve_config', 'oracle_prices', 'emode_membership',
            'account_reference', 'protocol_reconciliation']}
    metadata = json.loads((aave / 'snapshot_metadata.json').read_text(encoding='utf-8'))
    risk = pd.read_parquet(marts / 'borrower_risk.parquet')
    pos = pd.DataFrame(raw['positions_long'])
    dex = pd.read_parquet(dex_path)
    holdout_rows = stable_read(holdout)
    output.mkdir(parents=True, exist_ok=False)
    artifacts = {}
    def write_table(relative, frame, kind='observed_candidate', schema=None):
        path = output / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        table = pa.Table.from_pandas(frame, preserve_index=False, schema=schema)
        table = table.replace_schema_metadata({**(table.schema.metadata or {}),
                    b'data_kind': kind.encode(), b'validation_status': b'unvalidated',
                    b'preparation_version': b'n5_offline_preparation_v1'})
        pq.write_table(table, path, compression='snappy')
        artifacts[relative] = kind
    def write_json(relative, value, kind='diagnostic'):
        path = output / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(finite_json(value), ensure_ascii=False, indent=2,
                                   allow_nan=False), encoding='utf-8')
        artifacts[relative] = kind
    summary = {'snapshots': {}, 'research_acceptance': 'pending'}
    for label, snap in metadata['snapshots'].items():
        block = snap['snapshot_block']
        print(f'Preparing snapshot {label}, block {block}', flush=True)
        # Audit-only construction: this placeholder is NEVER used in a real simulation.
        portfolio = prepare_portfolio(raw['positions_long'], raw['reserve_config'], raw['oracle_prices'],
                    snapshot_block=block, emode_membership=raw['emode_membership'],
                    depth_usd={r['token_address']: 1_000_000 for r in raw['reserve_config']})
        validation = validate_inputs(portfolio, raw['account_reference'])
        debt_rows, debt_report = reconcile_debt(portfolio, raw['protocol_reconciliation'])
        validation['total_debt_only_reconciliation'] = debt_report
        validation['audit_only_depth_note'] = 'Placeholder depth for construction only; no real-data simulation run'
        if label == 'B':
            validation['historical_readiness'] = assess_historical_readiness(
                holdout_rows, snapshot_block=block, snapshot_timestamp=snap['snapshot_timestamp_utc'],
                wallets=[w.wallet for w in portfolio.wallets])
        write_json(f'{label}/validation.json', validation)
        write_table(f'{label}/debt_reconciliation.parquet', pd.DataFrame(debt_rows), 'diagnostic')
        edges = build_aave_graph(portfolio)
        write_table(f'{label}/graph_network_edges_candidate.parquet', edges,
                    schema=pq.read_schema('data/processed/graph_network_edges.parquet'))
        write_table(f'{label}/graph_node_metrics_candidate.parquet', graph_node_metrics(edges))
        write_table(f'{label}/normalized_positions_candidate.parquet', pd.DataFrame(portfolio.positions))
        n4 = diagnose_n4(portfolio, risk, pos)
        write_table(f'{label}/n4_hf_comparison.parquet', n4, 'diagnostic')
        disagreed = n4[n4.eligibility_disagreement]
        write_table(f'{label}/n4_eligibility_disagreements.parquet', disagreed, 'diagnostic')
        category_summary = n4.groupby('emode_category', dropna=False).agg(
            wallets=('wallet_address', 'count'), eligibility_disagreements=('eligibility_disagreement', 'sum'),
            max_hf_abs_error=('hf_abs_error', 'max')).reset_index()
        write_table(f'{label}/n4_by_emode_category.parquet', category_summary, 'diagnostic')
        depth = map_dex_depth(portfolio, dex)
        write_table(f'{label}/dex_depth_mapping.parquet', depth, 'diagnostic')
        # Strict failures remain unchanged. This table exposes absolute USD error without changing tolerance.
        totals = pd.DataFrame(portfolio.positions)
        totals['debt_usd'] = totals.debt_amount * totals.price_usd
        totals['collateral_usd'] = totals.collateral_amount * totals.price_usd * totals.is_collateral_enabled
        totals = totals.groupby('wallet_address')[['debt_usd', 'collateral_usd']].sum().reset_index()
        refs = pd.DataFrame(raw['account_reference'])
        refs = refs[refs.snapshot_block.eq(block)]
        balances = totals.merge(refs[['wallet_address', 'total_debt_base_usd', 'total_collateral_base_usd']],
                                 on='wallet_address', validate='one_to_one')
        for col, ref in [('debt_usd', 'total_debt_base_usd'), ('collateral_usd', 'total_collateral_base_usd')]:
            balances[f'{col}_absolute_error'] = (balances[col] - balances[ref]).abs()
        write_table(f'{label}/account_balance_diagnostics.parquet', balances, 'diagnostic')
        summary['snapshots'][label] = dict(block=block, wallets=len(portfolio.wallets),
            graph_edges=len(edges), graph_edge_types=dict(Counter(edges.edge_type)),
            total_debt_only=debt_report, n4_eligibility_disagreements=len(disagreed),
            dex_mapping_status=dict(Counter(depth.status)), validation_status=validation['status'])
        print(f'{label}: {len(edges)} graph edges; {len(disagreed)} N4 disagreements', flush=True)

    # Preserve snapshot keys in a review table; do not overwrite N4 summary.
    stress = pd.read_parquet(marts / 'stress_positions.parquet')
    keys = ['snapshot_block', 'protocol_id', 'scenario_id']
    if stress.duplicated(keys + ['wallet']).any():
        raise ValueError('Duplicate N4 stress keys')
    if stress[['hf_after', 'debt_usd', 'debt_at_risk_usd']].isna().any().any():
        raise ValueError('Null core N4 stress field')
    stress['at_risk'] = stress.hf_after.lt(1)
    stress['debt_at_risk_recalculated'] = stress.debt_usd.where(stress.at_risk, 0)
    stress['looping_debt_at_risk'] = stress.debt_at_risk_recalculated.where(stress.is_recursive_loop, 0)
    grouped = stress.groupby(keys).agg(wallets=('wallet', 'nunique'),
              positions_at_risk=('at_risk', 'sum'), total_debt_usd=('debt_usd', 'sum'),
              debt_at_risk_usd=('debt_at_risk_recalculated', 'sum'),
              looping_debt_at_risk_usd=('looping_debt_at_risk', 'sum')).reset_index()
    grouped['debt_at_risk_pct'] = grouped.debt_at_risk_usd / grouped.total_debt_usd * 100
    write_table('n4_stress_summary_by_snapshot_diagnostic.parquet', grouped, 'diagnostic')
    summary['n4_stress'] = dict(rows=len(stress),
        inconsistent_debt_at_risk_rows=int(((stress.debt_at_risk_usd - stress.debt_at_risk_recalculated).abs() > 1e-7).sum()),
        limitation='Reaggregated existing N4 outputs only; HF inputs not certified')
    observed = pd.read_parquet(history)
    candidates, rejected = historical_candidates(observed,
                                pq.read_schema('data/processed/fact_historical_liquidations.parquet').names)
    write_table('historical/fact_historical_liquidations_candidate.parquet', candidates,
                schema=pq.read_schema('data/processed/fact_historical_liquidations.parquet'))
    write_table('historical/rejected_events.parquet', rejected, 'diagnostic')
    summary['historical'] = dict(observed_events=len(observed), candidate_events=len(candidates),
        rejected_events=len(rejected), limitation='Only supplied 2024/2025 windows; not Snapshot B ground truth')
    price_qa = []
    for p in price_files:
        frame = pd.read_parquet(p)
        timestamp = 'timestamp_utc'
        price_column = 'price_usd' if 'price_usd' in frame else 'close'
        for asset, group in frame.groupby('asset'):
            times = pd.to_datetime(group[timestamp], utc=True).sort_values()
            values = pd.to_numeric(group[price_column], errors='coerce')
            price_qa.append(dict(source=str(p), asset=asset, rows=len(group),
                duplicate_timestamps=int(group.duplicated([timestamp]).sum()),
                invalid_prices=int((~values.map(lambda v: pd.notna(v) and math.isfinite(v) and v > 0)).sum()),
                start_utc=times.min().isoformat(), end_utc=times.max().isoformat(),
                largest_gap_hours=times.diff().dt.total_seconds().max() / 3600,
                limitation='Source reference prices; not event-time execution or snapshot oracle'))
    write_json('historical/price_series_qa.json', price_qa)
    concentration = pd.read_parquet(concentration_path)
    concentration_qa = []
    for label, snap in metadata['snapshots'].items():
        selected = concentration[concentration.snapshot_id.eq(label)]
        if selected.duplicated(['snapshot_id', 'wallet_address']).any():
            raise ValueError('Duplicate concentration wallet')
        source = pos[pos.snapshot_block.eq(snap['snapshot_block'])].copy()
        source['recalculated_debt_usd'] = source.debt_amount * source.price_usd
        totals = source.groupby('wallet_address').recalculated_debt_usd.sum()
        merged = selected.set_index('wallet_address').join(totals, how='outer')
        differences = (merged.debt_usd - merged.recalculated_debt_usd).abs()
        denominator = float(totals.sum())
        concentration_qa.append(dict(snapshot_block=snap['snapshot_block'], wallets=len(totals),
            missing_wallet_rows=int(merged[['debt_usd', 'recalculated_debt_usd']].isna().any(axis=1).sum()),
            max_debt_abs_error_usd=float(differences.max()),
            total_debt_usd=denominator, top_10_debt_pct=float(totals.nlargest(10).sum() / denominator * 100)))
    write_json('historical/concentration_qa.json', concentration_qa)
    compound_report, tables = inspect_compound(compound)
    write_json('compound/partial_qa.json', compound_report)
    # Normalized observational bridge table. No assumption about withdrawals or loss routing.
    accounts = tables['accounts'].merge(risk[['snapshot_block', 'wallet_address']],
              on=['snapshot_block', 'wallet_address'], how='left', indicator=True, validate='many_to_one')
    accounts['in_aave_portfolio'] = accounts.pop('_merge').eq('both')
    accounts['coverage_status'] = 'partial'
    write_table('compound/bridge_accounts_partial_candidate.parquet', accounts)
    summary['compound'] = compound_report

    print('Preparing synthetic output package and figures', flush=True)
    wallets, assets = synthetic_portfolio()
    symbols = {t: t for t in assets}
    context = {'data_kind': 'synthetic'}
    sensitivity = run_sensitivity_analysis(wallets, assets, symbols=symbols, context=context)
    results, comparisons = [], []
    for policy in POLICIES:
        result = compare_static_cascade(wallets, assets, CascadeConfig(cf_rule=policy),
            shocks=scenario_shocks(symbols, eth_shock=-.2, depeg_shock=-.05),
            scenario_id='demo_eth20_depeg5', context=context)
        results.extend([result['static'], result['cascade']])
        comparisons.append(result['comparison'])
    write_table('synthetic/cascade_rounds.parquet', pd.DataFrame([r for x in results for r in x.rounds]), 'synthetic')
    write_table('synthetic/cascade_summary.parquet', pd.DataFrame([x.summary for x in results]), 'synthetic')
    write_table('synthetic/sensitivity.parquet', pd.DataFrame(sensitivity), 'synthetic')
    write_json('synthetic/benchmark_comparison.json', comparisons, 'synthetic')
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    fig, ax = plt.subplots(figsize=(8, 4.5))
    for result in results:
        if result.summary['experiment_mode'] == 'cascade':
            ax.plot([r['round'] for r in result.rounds], [r['cumulative_repaid_usd'] for r in result.rounds],
                    marker='o', label=result.summary['cf_rule'])
    ax.set(title='SYNTHETIC / UNVALIDATED: cumulative simulated repayment', xlabel='Round', ylabel='USD')
    ax.legend(); fig.tight_layout(); fig.savefig(output / 'synthetic/cascade_curve.png'); plt.close(fig)
    artifacts['synthetic/cascade_curve.png'] = 'synthetic'
    frame = pd.DataFrame(sensitivity)
    selected = frame[frame.eth_shock.eq(-.2) & frame.depeg_shock.eq(-.05) & frame.sell_fraction.eq(1)]
    fig, ax = plt.subplots(figsize=(8, 4.5))
    for policy, group in selected.groupby('cf_rule'):
        group = group.sort_values('price_impact_parameter')
        ax.plot(group.price_impact_parameter, group.bad_debt_usd, marker='o', label=policy)
    ax.set(title='SYNTHETIC / UNVALIDATED: sensitivity (ETH -20%, depeg -5%)',
           xlabel='Assumed price impact parameter', ylabel='Simulated bad debt USD')
    ax.legend(); fig.tight_layout(); fig.savefig(output / 'synthetic/sensitivity_chart.png'); plt.close(fig)
    artifacts['synthetic/sensitivity_chart.png'] = 'synthetic'
    summary['synthetic'] = dict(sensitivity_runs=len(sensitivity), simulation_runs=len(results),
                                software_output_checks='passed', research_acceptance='pending')
    write_json('summary.json', summary)
    (output / 'README.md').write_text(
        '# N5 offline preparation — chưa nghiệm thu\n\n'
        'A/B: dữ liệu thật được chuyển đổi và chẩn đoán độc lập; không chạy Cascade thật.\n'
        'Graph giữ toàn bộ ví đầu vào, chưa lọc dust; membership là cạnh thông tin, không cộng vào exposure.\n'
        'Historical: candidate từ hai cửa sổ 2024/2025; không phải holdout Snapshot B 2026.\n'
        'Compound: chỉ partial; phần chưa truy vấn là unknown, không phải số dư 0.\n'
        'Synthetic: 8 ví giả lập; Parquet và biểu đồ chỉ phục vụ tích hợp/kiểm thử.\n'
        'N4 summary tách snapshot vẫn dùng HF cũ của N4, chưa chứng nhận nghiệp vụ.\n'
        'Manifest unvalidated; không tự đưa bộ này vào data/marts chính thức.\n'
        'Xem summary.json, A/validation.json, B/validation.json và manifest.json.\n', encoding='utf-8')
    artifacts['README.md'] = 'documentation'
    for p, fingerprint in fingerprints.items():
        if hashlib.sha256(p.read_bytes()).hexdigest() != fingerprint:
            raise ValueError(f'Source changed during preparation: {p}')
    write_manifest(output, input_paths, artifacts, summary)
    if verify_manifest(output):
        raise ValueError('Bundle checksum verification failed')
    print(f'Prepared {len(artifacts)} artifacts; manifest verified: {output}', flush=True)
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir', type=Path, required=True)
    parser.add_argument('--aave-dir', type=Path, default=Path('data/processed/aave'))
    parser.add_argument('--n4-marts', type=Path, default=Path('data/marts'))
    parser.add_argument('--dex', type=Path, default=Path('data/staging/n3_dex_liquidity.parquet'))
    parser.add_argument('--history', type=Path, default=Path('data/n3_history_2024_2026/final/n3_liquidations_observed.parquet'))
    parser.add_argument('--holdout', type=Path, default=Path('data/staging/n3_liquidations_holdout_24h.parquet'))
    parser.add_argument('--compound-dir', type=Path,
        default=Path('N3_Compound_Q3_Overlap_VSCode/data/processed/compound/consolidated_partial_20261022'))
    args = parser.parse_args()
    build_bundle(args.output_dir, aave=args.aave_dir, marts=args.n4_marts, dex_path=args.dex,
                 history=args.history, holdout=args.holdout, compound=args.compound_dir)


if __name__ == '__main__':
    main()
