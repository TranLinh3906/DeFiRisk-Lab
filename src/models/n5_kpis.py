"""Research KPI mapping and independent baseline calculations; no acceptance claims."""
import math
import pandas as pd


def baseline_wallet_kpis(positions, categories):
    keys = ['snapshot_block', 'protocol_id', 'wallet_address']
    p = positions.copy()
    p['collateral_usd'] = p.collateral_amount * p.price_usd * p.is_collateral_enabled
    p['debt_usd'] = p.debt_amount * p.price_usd
    p['weighted_lt_usd'] = p.collateral_usd * p.effective_lt
    p['lst_collateral'] = p.symbol.str.upper().isin(['WSTETH', 'WEETH', 'CBETH']) & p.collateral_usd.gt(0)
    p['weth_debt'] = p.symbol.str.upper().eq('WETH') & p.debt_usd.gt(0)
    out = p.groupby(keys).agg(collateral_usd=('collateral_usd', 'sum'), debt_usd=('debt_usd', 'sum'),
         weighted_lt_usd=('weighted_lt_usd', 'sum'), has_lst_collateral=('lst_collateral', 'any'),
         has_weth_debt=('weth_debt', 'any')).reset_index()
    out['net_equity_usd'] = out.collateral_usd - out.debt_usd
    out['effective_leverage'] = out.collateral_usd / out.net_equity_usd.where(out.net_equity_usd.gt(0))
    out['health_factor'] = out.weighted_lt_usd / out.debt_usd.where(out.debt_usd.gt(0))
    out['dist_to_liq_pct'] = (1 - 1 / out.health_factor.where(out.health_factor.gt(0))) * 100
    out['equity_bucket'] = pd.cut(out.net_equity_usd,
        [-math.inf, 0, 1e4, 1e5, 1e6, 1e7, math.inf],
        labels=['nonpositive', '(0,10k]', '(10k,100k]', '(100k,1m]', '(1m,10m]', '>10m']).astype(str)
    out = out.merge(categories[keys + ['emode_category']], on=keys, validate='one_to_one')
    out['is_recursive_loop'] = out.emode_category.eq(1) & out.has_lst_collateral & out.has_weth_debt & out.effective_leverage.ge(3)
    out['validation_status'] = 'unvalidated'
    return out


def kpi_mapping(dictionary, snapshot_labels=('A', 'B')):
    mappings = {
        'M01': ('baseline_candidate', 'A|B/baseline_wallet_kpis.parquet', 'net_equity_usd', 'Collateral enabled minus debt; borrower scope'),
        'M02': ('baseline_candidate', 'A|B/baseline_wallet_kpis.parquet', 'effective_leverage', 'Null for nonpositive equity; no fabricated epsilon leverage'),
        'M03': ('baseline_candidate', 'A|B/baseline_wallet_kpis.parquet', 'health_factor', 'Independent effective LT reconstruction; N4 mismatch still pending'),
        'M04': ('baseline_candidate', 'A|B/baseline_wallet_kpis.parquet', 'dist_to_liq_pct', 'Null if HF<=0 or no debt; negative distance retained for underwater'),
        'M05': ('not_implemented', '', '', 'Needs Monte Carlo, segmentation and agreed Q1 design'),
        'M06': ('synthetic_only', 'synthetic/cascade_summary.parquet', 'bad_debt_usd', 'System total only; wallet-level research output pending'),
        'M07': ('definition_review', 'synthetic/cascade_summary.parquet', 'bad_debt_ratio_pct', 'Engine denominator is post-shock initial debt; dictionary t0 denominator needs alignment'),
        'M08': ('not_implemented', '', '', 'Needs equity-normalized simulated losses'),
        'M09': ('not_implemented', '', '', 'Needs validated loss distribution and ES99 implementation'),
        'M10': ('synthetic_only', 'synthetic/cascade_rounds.parquet', 'simulated_repaid_debt_usd', 'Actual simulated repay is capped by available token balances'),
        'M11': ('synthetic_only', 'synthetic/cascade_rounds.parquet', 'prices_before_json,prices_after_json', 'Terminal modeled price drop; not calibrated execution slippage'),
        'M12': ('definition_review', 'synthetic/cascade_summary.parquet', 'sequential_liquidatable_wallets', 'Eligible wallets are not necessarily executed liquidations'),
        'M13': ('synthetic_only', 'synthetic/cascade_summary.parquet', 'cascade_amplification', 'Sum repayment / round1; zero denominator gives null'),
        'M14': ('synthetic_only', 'synthetic/cascade_rounds.parquet', 'liquidation_freeze_debt_usd', 'Round freeze, not bad debt; do not sum repeated frozen balances'),
        'M15': ('definition_review', '', '', 'Healthy threshold 1.05 in dictionary vs 1.10 in H3; temporal transition definition pending'),
        'M16': ('tracking_only', 'compound/exposures_partial.parquet', '', 'Explicit flow ledger available; causal cross-protocol liquidation simulation pending'),
        'M17': ('tracking_only', 'A|B/graph_network_edges_candidate.parquet', '', 'Exposure graph is not an induced-liquidation transmission graph'),
        'M18': ('not_implemented', 'A|B/graph_node_metrics_candidate.parquet', '', 'Degree/strength are not DebtRank'),
    }
    out = dictionary.copy()
    values = [mappings[r] for r in out.kpi_id]
    for index, col in enumerate(['implementation_status', 'candidate_artifact', 'candidate_field', 'limitation']):
        out[col] = [v[index] for v in values]
    out['validation_status'] = 'unvalidated'
    template = '|'.join(snapshot_labels) + '/'
    out['candidate_artifact'] = out.candidate_artifact.str.replace('A|B/', template, regex=False)
    return out


def graph_conservation_report(edges, positions):
    """Compare per-token graph exposure to positions, excluding membership weights."""
    keys = ['snapshot_block', 'protocol_id', 'token_address', 'edge_type']
    expected_parts, actual_parts = [], []
    for kind, amount, node in [('collateral_exposure', 'collateral_amount', 'target_node_id'),
                                ('debt_exposure', 'debt_amount', 'source_node_id')]:
        source = positions.copy()
        source['edge_type'] = kind
        source['expected_usd'] = source[amount] * source.price_usd
        if kind == 'collateral_exposure':
            source['expected_usd'] *= source.is_collateral_enabled
        expected_parts.append(source.groupby(keys).expected_usd.sum())
        selected = edges[edges.edge_type.eq(kind)].copy()
        if not selected[node].str.startswith('token:').all():
            raise ValueError('Exposure direction or token node invalid')
        selected['token_address'] = selected[node].str.removeprefix('token:')
        actual_parts.append(selected.groupby(keys).weight_usd_t0.sum().rename('graph_usd'))
    expected = pd.concat(expected_parts)
    actual = pd.concat(actual_parts)
    out = expected.to_frame().join(actual, how='outer').fillna(0).reset_index()
    out['absolute_error_usd'] = (out.expected_usd - out.graph_usd).abs()
    out['passed'] = [math.isclose(a, b, rel_tol=1e-9, abs_tol=1e-7) for a, b in zip(out.expected_usd, out.graph_usd)]
    if edges.duplicated(['snapshot_block', 'edge_id']).any() or edges.weight_usd_t0.lt(0).any():
        out['passed'] = False
    return out


def validate_kpi_artifacts(mapping, directory):
    """Verify declared files/fields without promoting unfinished KPI definitions."""
    from pathlib import Path
    import pyarrow.parquet as pq
    directory = Path(directory)
    if mapping.kpi_id.duplicated().any():
        raise ValueError('Duplicate KPI mapping ID')
    reports = []
    for _, row in mapping.iterrows():
        errors = []
        relative = row.candidate_artifact
        prefix = relative.split('/')[0] if isinstance(relative, str) else ''
        paths = ([relative.replace(prefix + '/', f'{label}/', 1) for label in prefix.split('|')]
                 if '|' in prefix else [relative])
        for path in paths:
            if not isinstance(path, str) or not path:
                continue  # Deliberately unimplemented: no invented file required.
            file = directory / path
            if not file.is_file():
                errors.append(f'Missing {path}'); continue
            fields = row.candidate_field
            if isinstance(fields, str) and fields:
                columns = pq.read_schema(file).names
                errors += [f'{path}: missing {name}' for name in fields.split(',') if name not in columns]
        reports.append(dict(kpi_id=row.kpi_id, implementation_status=row.implementation_status,
            artifact_check='failed' if errors else 'passed_mapping_only', errors=errors,
            research_acceptance='pending'))
    return reports
