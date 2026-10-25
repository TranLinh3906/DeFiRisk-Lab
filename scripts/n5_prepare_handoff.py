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
from src.models.n5_validation import validate_inputs, assess_historical_readiness, check_schema, flag
from src.models.n5_integration import (attribute_hf_differences, adapt_compound_partial,
                                       track_compound_flows, validate_zero_shock, check_reconstructed_hf)
from src.models.n5_snapshots import audit_snapshots, select_event_windows
from src.models.n5_kpis import baseline_wallet_kpis, kpi_mapping, graph_conservation_report, validate_kpi_artifacts
from src.models.n5_diagnostics import (tiny_shock_diagnostics, balance_policy_diagnostics,
    anchor_price_impact, historical_open_price_validation)
from src.models.n5_research import (compound_price_stress, student_t_copula_shocks,
    wallet_loss_rows, summarize_q1, cascade_transitions, debt_rank, explicit_base_supply_withdrawal)
from src.models.n5_readiness import (
    build_aave_graph, diagnose_n4, historical_candidates, inspect_compound,
    map_dex_depth, reconcile_debt, write_manifest, verify_manifest, graph_node_metrics,
)


def build_bundle(output, *, aave, marts, dex_path, history, holdout, compound,
                 snapshot_config=Path('config/snapshots.json')):
    output = Path(output)
    if output.exists():
        raise FileExistsError('Choose a new output directory')
    input_paths = [aave / f'{name}.parquet' for name in
                   ['positions_long', 'reserve_config', 'oracle_prices', 'emode_membership',
                    'account_reference', 'protocol_reconciliation']]
    input_paths += [aave / 'snapshot_metadata.json', marts / 'borrower_risk.parquet',
                    aave / 'borrower_positions.parquet',
                    marts / 'stress_positions.parquet', marts / 'stress_summary.parquet',
                    marts / 'stress_heatmap.parquet', dex_path, history, holdout,
                    Path('data/processed/graph_network_edges.parquet'),
                    Path('data/processed/fact_historical_liquidations.parquet'),
                    compound / 'merge_report.json']
    input_paths += [snapshot_config, aave / 'hf_reconciliation.parquet', Path('src/models/n5_snapshots.py')]
    modern_compound = (compound / 'query_coverage.parquet').is_file()
    input_paths += [compound / ('query_coverage.parquet' if modern_compound else 'compound_consolidated_partial.sqlite')]
    ending = '' if modern_compound else '_partial'
    input_paths += [compound / f'n3_compound_{name}{ending}.parquet' for name in
                    ['positions_long', 'account_reference', 'reserve_config', 'overlap_wallets']]
    price_files = [history.parent / name for name in
                   ['market_daily_continuous.parquet', 'market_crashes_hourly.parquet',
                    'token_prices_continuous.parquet']]
    concentration_path = history.parent / 'concentration_AB1B2/concentration_by_wallet.parquet'
    if not concentration_path.is_file():
        concentration_path = history.parent / 'concentration_2026/concentration_by_wallet.parquet'
    input_paths += price_files + [concentration_path]
    input_paths += [Path(p) for p in ['scripts/n5_prepare_handoff.py', 'scripts/n5_audit.py',
                    'src/models/n5_readiness.py', 'src/models/n5_inputs.py',
                    'src/models/n5_validation.py', 'src/models/n5_experiments.py',
                    'src/models/n5_integration.py', 'src/models/n5_kpis.py',
                    'src/models/health_factor.py', 'src/models/stress_test.py', 'src/models/cliff_reverse.py',
                    'src/models/cascade.py', 'src/models/n5_diagnostics.py', 'src/models/n5_research.py',
                    'research_design.md', 'kpi_dictionary.csv']]
    # Exact hashes before reading allow detecting concurrent source updates.
    import hashlib
    fingerprints = {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in input_paths}
    raw = {name: stable_read(aave / f'{name}.parquet') for name in
           ['positions_long', 'reserve_config', 'oracle_prices', 'emode_membership',
            'account_reference', 'protocol_reconciliation']}
    metadata = json.loads((aave / 'snapshot_metadata.json').read_text(encoding='utf-8'))
    config = json.loads(snapshot_config.read_text(encoding='utf-8'))
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
    def write_csv(relative, frame, kind='diagnostic'):
        path = output / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        frame.to_csv(path, index=False, encoding='utf-8-sig')
        artifacts[relative] = kind
    summary = {'snapshots': {}, 'research_acceptance': 'pending'}
    audit_tables = {name: pd.DataFrame(rows) for name, rows in raw.items()}
    audit_tables['borrower_positions'] = pd.read_parquet(aave / 'borrower_positions.parquet')
    audit_tables['account_reference'] = pd.DataFrame(raw['account_reference'])
    audit_tables['hf_reconciliation'] = pd.read_parquet(aave / 'hf_reconciliation.parquet')
    summary['configured_snapshot_readiness'] = audit_snapshots(config, metadata, audit_tables)
    write_json('snapshot_readiness.json', summary['configured_snapshot_readiness'])
    mapping = kpi_mapping(pd.read_csv('kpi_dictionary.csv', encoding='utf-8-sig'), tuple(metadata['snapshots']))
    for kpi, artifact, field in [
        ('M05','fixtures/q1_wallet_risk.parquet','probability_bad_debt'),
        ('M08','fixtures/q1_wallet_losses.parquet','loss_per_100k_equity'),
        ('M09','fixtures/q1_wallet_risk.parquet','es99_loss_per_100k'),
        ('M15','fixtures/q3_cascade_transitions.parquet','initially_healthy_sequential_execution'),
        ('M18','fixtures/q3_debtrank.json','')]:
        selected = mapping.kpi_id.eq(kpi)
        mapping.loc[selected, ['implementation_status','candidate_artifact','candidate_field','limitation']] = [
            'implemented_fixture_only',artifact,field,'Implemented and exercised on fixtures; empirical calibration/definitions and acceptance pending']
    write_csv('kpi_mapping.csv', mapping)
    write_table('calibration/price_impact_anchors.parquet', anchor_price_impact(dex), 'conditional_calibration')
    normalized_snapshots = []
    summary['n4_schema_checks'] = {}
    for name in ['stress_summary', 'stress_heatmap']:
        errors = check_schema(pd.read_parquet(marts / f'{name}.parquet').to_dict('records'), name)
        summary['n4_schema_checks'][name] = dict(status='failed' if errors else 'passed', errors=errors)
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
        normalized = pd.DataFrame(portfolio.positions)
        normalized_snapshots.append(normalized)
        graph_checks = graph_conservation_report(edges, normalized)
        write_table(f'{label}/graph_conservation.parquet', graph_checks, 'diagnostic')
        write_csv(f'{label}/graph_conservation.csv', graph_checks)
        if not graph_checks.passed.all():
            raise ValueError('Per-token graph exposure conservation failed')
        cats = pos[pos.snapshot_block.eq(block)].groupby(['snapshot_block', 'wallet_address']).user_emode.first().reset_index()
        cats = cats.rename(columns={'user_emode': 'emode_category'})
        cats['protocol_id'] = portfolio.protocol_id
        baseline = baseline_wallet_kpis(normalized, cats)
        write_table(f'{label}/baseline_wallet_kpis.parquet', baseline)
        group_summary = baseline.groupby(['equity_bucket','is_recursive_loop'], observed=True).agg(
            wallets=('wallet_address','size'),debt_usd=('debt_usd','sum'),
            net_equity_usd=('net_equity_usd','sum')).reset_index()
        write_csv(f'{label}/looping_equity_group_summary.csv',group_summary)
        n4 = diagnose_n4(portfolio, risk, pos)
        write_table(f'{label}/n4_hf_comparison.parquet', n4, 'diagnostic')
        disagreed = n4[n4.eligibility_disagreement]
        write_table(f'{label}/n4_eligibility_disagreements.parquet', disagreed, 'diagnostic')
        write_csv(f'{label}/n4_eligibility_disagreements.csv', disagreed)
        token_causes, wallet_causes = attribute_hf_differences(portfolio, pos,
            pd.DataFrame(raw['reserve_config']), pd.DataFrame(raw['emode_membership']), risk,
            model_mode='wallet_collateral')
        write_table(f'{label}/hf_attribution_by_token.parquet', token_causes, 'diagnostic')
        write_table(f'{label}/hf_attribution_by_wallet.parquet', wallet_causes, 'diagnostic')
        implicated = wallet_causes[wallet_causes.eligibility_disagreement]
        affected_tokens = token_causes[token_causes.wallet_address.isin(implicated.wallet_address)]
        write_csv(f'{label}/hf_disagreement_tokens.csv', affected_tokens)
        unresolved = wallet_causes[~wallet_causes.reconstruction_matches_n4]
        write_csv(f'{label}/hf_unexplained_residuals.csv', unresolved)
        write_csv(f'{label}/hf_attribution_disagreements.csv', implicated)
        cause_summary = implicated.groupby(['candidate_causes', 'attribution_status']).agg(
            wallets=('wallet_address', 'size'), debt_usd=('debt_usd', 'sum')).reset_index()
        write_csv(f'{label}/hf_cause_summary.csv', cause_summary)
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
        balances, policies = balance_policy_diagnostics(balances)
        write_table(f'{label}/account_balance_diagnostics.parquet', balances, 'diagnostic')
        write_csv(f'{label}/account_balance_failures.csv', balances[balances.debt_strict_failed | balances.collateral_strict_failed])
        write_csv(f'{label}/proposed_dust_policy_comparison.csv', policies)
        summary['snapshots'][label] = dict(block=block, wallets=len(portfolio.wallets),
            graph_edges=len(edges), graph_edge_types=dict(Counter(edges.edge_type)),
            total_debt_only=debt_report, n4_eligibility_disagreements=len(disagreed),
            dex_mapping_status=dict(Counter(depth.status)), validation_status=validation['status'],
            graph_conservation_passed=bool(graph_checks.passed.all()),
            snapshot_scope='configured' if label in config['snapshots'] else 'legacy_metadata_only',
            hf_reconstruction_unexplained_wallets=len(unresolved),
            hf_attribution_summary=cause_summary.to_dict('records'))
        summary['snapshots'][label]['proposed_dust_policies'] = policies.to_dict('records')
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
    from src.models.stress_test import _prepare_positions, run_stress_test
    loaded = _prepare_positions(pd.read_parquet(aave / 'borrower_positions.parquet'))
    wallet_keys = ['snapshot_block', 'protocol_id', 'wallet_address']
    loaded = loaded.drop(columns=['health_factor', 'is_recursive_loop'], errors='ignore').merge(
        risk[wallet_keys + ['health_factor', 'is_recursive_loop']], on=wallet_keys, validate='many_to_one')
    zero = run_stress_test(loaded, {'scenario_id': 'n5_zero_shock'})
    summary['zero_shock'] = validate_zero_shock(zero.to_dict('records'))
    write_json('zero_shock_validation.json', summary['zero_shock'])
    write_csv('zero_shock_mismatches.csv', zero[(zero.hf_before-zero.hf_after).abs().ge(.005)
                                               | (zero.hf_before.lt(1) != zero.hf_after.lt(1))])
    tiny = run_stress_test(loaded, {'scenario_id': 'n5_tiny_shock', 'eth_shock': -1e-12})
    reconstructed, checked = check_reconstructed_hf(loaded, risk, zero, tiny)
    summary['reconstructed_hf_and_tiny_shock'] = checked
    write_json('reconstructed_hf_validation.json', checked)
    write_table('reconstructed_hf_diagnostics.parquet', reconstructed, 'diagnostic')
    write_csv('reconstructed_hf_failures.csv', reconstructed[~reconstructed.passed])
    continuity = tiny_shock_diagnostics(reconstructed)
    write_csv('tiny_shock_investigation.csv', continuity[~continuity.passed])
    summary['tiny_shock_investigation'] = dict(strict_failed=int((~continuity.passed).sum()),
        analytic_continuity_failed=int((~continuity.analytic_continuity_passed).sum()),
        diagnoses=dict(Counter(continuity.diagnosis)), strict_gate_unchanged=True)
    observed = pd.read_parquet(history)
    window_events, window_reports = select_event_windows(observed, config)
    write_table('historical/configured_window_events.parquet', window_events)
    write_json('historical/configured_window_qa.json', window_reports)
    summary['configured_event_windows'] = window_reports
    hourly = pd.read_parquet(history.parent / 'market_crashes_hourly.parquet')
    for label, snap in config['snapshots'].items():
        if not snap.get('window_end_exclusive_utc'):
            continue
        predictions, validation = historical_open_price_validation(pd.concat(normalized_snapshots), observed, hourly, snap)
        write_table(f'historical/{label}_market_proxy_predictions.parquet', predictions, 'conditional_market_proxy')
        write_json(f'historical/{label}_market_proxy_validation.json', validation)
        summary.setdefault('historical_market_proxy', {})[label] = validation
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
    concentration = concentration.rename(columns={'wallet': 'wallet_address'})
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
    complete_blocks = {r['snapshot_block'] for r in json.loads((compound / 'merge_report.json').read_text(encoding='utf-8')).get('snapshots', []) if not r['missing_queries']}
    accounts['coverage_status'] = accounts.snapshot_block.map(lambda b: 'complete_target_queries' if b in complete_blocks else 'partial')
    write_table('compound/bridge_accounts_partial_candidate.parquet', accounts)
    summary['compound'] = compound_report
    exposures = adapt_compound_partial(tables['positions'], tables['config'])
    exposures['coverage_status'] = exposures.snapshot_block.map(lambda b: 'complete_target_queries' if b in complete_blocks else 'partial')
    write_table('compound/exposures_partial.parquet', exposures)
    unchanged, _ = track_compound_flows(exposures, [])
    if not unchanged.amount_token.equals(exposures.amount_token):
        raise ValueError('Empty event ledger altered Compound balances')
    event_columns = ['event_id', 'snapshot_block', 'market_id', 'wallet_address',
                     'token_address', 'asset_role', 'quantity_delta', 'reason', 'source_run_id']
    write_csv('compound/flow_events_template.csv', pd.DataFrame(columns=event_columns), 'template')
    if modern_compound:
        coverage = pd.read_parquet(compound / 'query_coverage.parquet')
    else:
        import sqlite3
        with sqlite3.connect((compound / 'compound_consolidated_partial.sqlite').resolve().as_uri() + '?mode=ro', uri=True) as connection:
            coverage = pd.read_sql_query('SELECT * FROM query_coverage', connection)
    coverage['covered'] = [flag(v, 'covered') for v in coverage.covered]
    if len(coverage) != compound_report['expected_queries'] or int(coverage.covered.sum()) != compound_report['covered_queries']:
        raise ValueError('Compound coverage differs from merge report')
    coverage['coverage_status'] = coverage.covered.map({True: 'queried_source_claim', False: 'unknown_not_queried'})
    write_table('compound/query_coverage_tracking.parquet', coverage)
    coverage_summary = coverage.groupby(['snapshot_block','market_id']).covered.agg(['size','sum']).reset_index()
    coverage_summary['missing_queries'] = coverage_summary['size']-coverage_summary['sum']
    write_csv('compound/coverage_by_snapshot_market.csv', coverage_summary)
    exposure_summary = exposures.groupby(['snapshot_block','market_id','asset_role']).agg(
        wallets=('wallet_address','nunique'),value_usd=('value_usd','sum')).reset_index()
    write_csv('compound/exposure_by_snapshot_market_role.csv', exposure_summary)
    # Named price scenario, NOT estimated spillover caused by an Aave loss.
    comet_returns = {(r.snapshot_block,r.token_address): -.2 for r in exposures.itertuples()
                     if str(r.token_symbol if hasattr(r,'token_symbol') else getattr(r,'symbol','')).upper() in ['WETH','ETH','WSTETH','CBETH','WEETH']}
    write_table('compound/conditional_eth20_eligibility.parquet', compound_price_stress(exposures,comet_returns), 'conditional_scenario')
    write_json('compound/tracking_status.json', dict(exposure_rows=len(exposures), events_applied=0,
        status='prepared_not_simulated', token_conservation_empty_ledger='passed',
        unqueried_positions='unknown', coverage_evidence='reconstructed assignment/source QA; not raw RPC evidence',
        limitation='No assumed withdrawals; no Aave close factor imposed on Comet'))

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
    # Q1/Q3 functions exercised on fixtures only, pending research assumptions.
    from src.models.cascade import Asset, Wallet, simulate_cascade, collateral_value, debt_value, health_factor
    qassets = {'WETH': Asset(2000,.8,.05,1e6), 'wstETH': Asset(2000,.93,.02,1e6), 'USDC': Asset(1,.8,.05,1e6)}
    qwallets = [Wallet('loop_small',{'wstETH':5},{'WETH':4}),
                Wallet('standard_small',{'wstETH':1.5},{'USDC':1000}),
                Wallet('loop_medium',{'wstETH':50},{'WETH':40}),
                Wallet('standard_medium',{'wstETH':15},{'USDC':10000})]
    base=[]
    prices={t:a.price_usd for t,a in qassets.items()}
    for w in qwallets:
        c,d=collateral_value(w,prices),debt_value(w,prices)
        base.append(dict(wallet_address=w.wallet,net_equity_usd=c-d,debt_usd=d,
            health_factor=health_factor(w,qassets,prices),is_recursive_loop=w.wallet.startswith('loop'),
            equity_bucket='(0,10k]' if c-d<=1e4 else '(10k,100k]'))
    qbase=pd.DataFrame(base)
    shocks=student_t_copula_shocks(256,[-.5,-.3,-.2,-.1,0],[-.20,-.1,-.05,-.02,0],rho=.6)
    write_table('fixtures/q1_copula_scenarios.parquet',shocks,'synthetic')
    losses=[]
    for s in shocks.itertuples():
        result=simulate_cascade(qwallets,qassets,CascadeConfig(lambda_impact=.04),
            shocks=scenario_shocks({t:t for t in qassets},eth_shock=s.eth_shock,depeg_shock=s.depeg_shock),scenario_id=s.scenario_id)
        losses.append(wallet_loss_rows(result,qbase))
    wallet_metrics,strata,matched=summarize_q1(pd.concat(losses,ignore_index=True))
    write_table('fixtures/q1_wallet_losses.parquet',pd.concat(losses,ignore_index=True),'synthetic')
    write_table('fixtures/q1_wallet_risk.parquet',wallet_metrics,'synthetic')
    write_table('fixtures/q1_equity_strata.parquet',strata,'synthetic')
    write_table('fixtures/q1_matched_comparison.parquet',matched,'synthetic')
    write_table('fixtures/q3_cascade_transitions.parquet',cascade_transitions(result,qbase,qassets),'synthetic')
    write_json('fixtures/q3_debtrank.json',debt_rank([[0,50,0],[0,0,50],[0,0,0]],[100,100,100],[1,0,0]),'synthetic')
    # Small explicit Comet ledger fixture uses real schema but invented balances.
    cp=exposures.head(0).copy()
    if not exposures.empty:
        cp=exposures.iloc[[0]].copy()
        cp['asset_role']='base_supply'; cp['amount_token']=10.; cp['value_usd']=10.*cp.price_usd
        changed,ledger=explicit_base_supply_withdrawal(cp,set(cp.wallet_address),fraction=.25,scenario_id='fixture_withdraw_25pct')
        write_table('fixtures/q3_explicit_withdrawal_ledger.parquet',ledger,'synthetic')
        write_table('fixtures/q3_explicit_withdrawal_balances.parquet',changed,'synthetic')
    summary['q1_q3_scaffold']=dict(status='implemented_fixture_verified',mc_fixture_scenarios=256,
        marginal_source='invented stress grid',rho=.6,df=4,seed=20261025,
        limitation='No fitted probability, no empirical Q1/Q3 causal conclusion; no assumed collateral withdrawals')
    # Mapping now includes fixture-only KPI artifacts, so check after generating them.
    summary['kpi_artifact_checks'] = validate_kpi_artifacts(mapping, output)
    write_json('kpi_artifact_qa.json', summary['kpi_artifact_checks'])
    if any(row['artifact_check'] == 'failed' for row in summary['kpi_artifact_checks']):
        raise ValueError('KPI mapping refers to missing artifact/field')
    write_json('summary.json', summary)
    (output / 'README.md').write_text(
        '# N5 offline preparation — chưa nghiệm thu\n\n'
        'A/B1/B2: dữ liệu thật được chuyển đổi và chẩn đoán độc lập; không chạy Cascade thật.\n'
        'Config A/B1/B2 được kiểm ở snapshot_readiness.json; B cũ chỉ là legacy, không đổi nhãn.\n'
        'Zero-shock đơn thuần không thay kiểm HF tái dựng và tiny-shock: xem reconstructed_hf_validation.json.\n'
        'Graph giữ toàn bộ ví đầu vào, chưa lọc dust; membership là cạnh thông tin, không cộng vào exposure.\n'
        'Historical: proxy dùng hourly open có sẵn trước sự kiện, không phải dự báo oracle đã nghiệm thu.\n'
        'Compound: A đủ truy vấn mục tiêu; B1/B2 partial, phần chưa truy vấn là unknown.\n'
        'Synthetic: 8 ví giả lập; Parquet và biểu đồ chỉ phục vụ tích hợp/kiểm thử.\n'
        'N4 summary tách snapshot dùng marts hiện tại; mọi gate chưa đạt vẫn giữ nguyên.\n'
        'Manifest unvalidated; không tự đưa bộ này vào data/marts chính thức.\n'
        'Fixtures Q1/Q3 là giả lập, không phải kết quả nghiên cứu; dust chỉ là phương án chờ duyệt.\n'
        'Xem summary.json, A/B1/B2/validation.json và manifest.json.\n', encoding='utf-8')
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
    parser.add_argument('--dex', type=Path, default=Path('data/staging/n3_dex_liquidity_A_B1_B2.parquet'))
    parser.add_argument('--history', type=Path, default=Path('data/n3_history_2024_2026/final/n3_liquidations_observed.parquet'))
    parser.add_argument('--holdout', type=Path, default=Path('data/staging/n3_liquidations_holdout_24h.parquet'))
    parser.add_argument('--compound-dir', type=Path,
        default=Path('outputs/compound_consolidated_A_B1_B2_20261025'))
    parser.add_argument('--snapshot-config', type=Path, default=Path('config/snapshots.json'))
    args = parser.parse_args()
    build_bundle(args.output_dir, aave=args.aave_dir, marts=args.n4_marts, dex_path=args.dex,
                 history=args.history, holdout=args.holdout, compound=args.compound_dir,
                 snapshot_config=args.snapshot_config)


if __name__ == '__main__':
    main()
