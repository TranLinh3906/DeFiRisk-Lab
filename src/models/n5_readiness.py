"""Offline N5 preparation. Diagnostics never promote inputs to accepted results."""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path

import pandas as pd

from src.models.cascade import health_factor, debt_value, collateral_value
from src.models.n5_inputs import number

PROTOCOL = "aave_v3_ethereum"


def reconcile_debt(portfolio, reserve_rows):
    """Independently sum portfolio debt; never use exported wallet_debt_usd as truth.

    This checks debt only, not depositor coverage, TVL or the combined reserve gate.
    """
    selected = [r for r in reserve_rows if r['snapshot_block'] == portfolio.snapshot_block]
    index = {}
    for row in selected:
        token = row['token_address'].strip().lower()
        if token in index:
            raise ValueError('Duplicate reserve reconciliation token')
        if row.get('protocol_id', portfolio.protocol_id) != portfolio.protocol_id:
            raise ValueError('Unexpected reconciliation protocol')
        index[token] = row
    if not index or set(portfolio.assets) - set(index):
        raise ValueError('Missing same-block reserve debt evidence')
    rows = []
    for token, ref in sorted(index.items()):
        expected = number(ref['protocol_debt_usd'], 'protocol debt')
        price = number(ref['price_usd'], 'reference price', 1e-300)
        units = number(ref['protocol_debt_units'], 'protocol debt units')
        if not math.isclose(expected, units * price, rel_tol=1e-9, abs_tol=1e-7):
            raise ValueError('Reserve debt value does not match units and price')
        if token in portfolio.assets and not math.isclose(
                price, portfolio.assets[token].price_usd, rel_tol=1e-9):
            raise ValueError('Reserve reconciliation price differs from oracle')
        actual = math.fsum(w.debt.get(token, 0) for w in portfolio.wallets) * price
        error = abs(actual - expected)
        relative = error / expected if expected else (0.0 if actual == 0 else None)
        rows.append(dict(snapshot_block=portfolio.snapshot_block, token_address=token,
                         symbol=ref.get('symbol', token), calculated_debt_usd=actual,
                         protocol_debt_usd=expected, absolute_error_usd=error,
                         relative_error=relative,
                         within_one_pct=(relative is not None and relative <= .01)))
    actual = math.fsum(r['calculated_debt_usd'] for r in rows)
    expected = math.fsum(r['protocol_debt_usd'] for r in rows)
    relative = abs(actual - expected) / expected if expected else (0 if actual == 0 else None)
    return rows, dict(status='passed' if relative is not None and relative <= .01 else 'failed',
                      scope='total debt only; Aave Ethereum Core at selected block',
                      snapshot_block=portfolio.snapshot_block, calculated_debt_usd=actual,
                      protocol_debt_usd=expected, relative_error=relative,
                      tokens_outside_one_pct=sum(not r['within_one_pct'] for r in rows),
                      reserve_collateral_gate='pending', research_acceptance='pending')


def build_aave_graph(portfolio):
    """Collateral wallet->token; debt token->wallet; membership wallet->protocol.

    Membership weights equal wallet debt and are informational: do not add them
    to debt edges when summing exposures. No transmission/causal score is inferred.
    """
    edges = []
    def add(source, sl, target, tl, kind, weight, lt=None):
        identity = f'{portfolio.protocol_id}|{kind}|{source}|{target}'
        edges.append(dict(snapshot_block=portfolio.snapshot_block,
                          edge_id=hashlib.sha256(identity.encode()).hexdigest(),
                          source_node_id=source, source_layer=sl, target_node_id=target,
                          target_layer=tl, edge_type=kind, protocol_id=portfolio.protocol_id,
                          weight_usd_t0=weight, effective_lt=lt))
    for row in portfolio.positions:
        wallet = f"wallet:{row['wallet_address']}"
        asset = f"token:{row['token_address']}"
        if row['is_collateral_enabled'] and row['collateral_amount'] > 0:
            add(wallet, 'wallet', asset, 'token', 'collateral_exposure',
                row['collateral_amount'] * row['price_usd'], row['effective_lt'])
        if row['debt_amount'] > 0:
            add(asset, 'token', wallet, 'wallet', 'debt_exposure', row['debt_amount'] * row['price_usd'])
    prices = {t: a.price_usd for t, a in portfolio.assets.items()}
    for w in portfolio.wallets:
        add(f'wallet:{w.wallet}', 'wallet', f'protocol:{portfolio.protocol_id}',
            'protocol', 'wallet_protocol', debt_value(w, prices))
    frame = pd.DataFrame(edges)
    if frame.duplicated(['snapshot_block', 'edge_id']).any():
        raise ValueError('Graph edge key collision')
    for kind, expected in (
        ('debt_exposure', math.fsum(debt_value(w, prices) for w in portfolio.wallets)),
        ('collateral_exposure', math.fsum(collateral_value(w, prices) for w in portfolio.wallets)),
    ):
        if not math.isclose(frame.loc[frame.edge_type.eq(kind), 'weight_usd_t0'].sum(),
                            expected, rel_tol=1e-9, abs_tol=1e-7):
            raise ValueError(f'Graph {kind} does not conserve exposure')
    return frame


def diagnose_n4(portfolio, risk, positions):
    prices = {t: a.price_usd for t, a in portfolio.assets.items()}
    keys = ['snapshot_block', 'protocol_id', 'wallet_address']
    selected = risk[risk.snapshot_block.eq(portfolio.snapshot_block)].copy()
    if selected.duplicated(keys).any() or not selected.protocol_id.eq(portfolio.protocol_id).all():
        raise ValueError('Duplicate/mixed-protocol N4 risk rows')
    categories = positions[positions.snapshot_block.eq(portfolio.snapshot_block)].groupby(
        'wallet_address').user_emode.first()
    ours = pd.DataFrame([dict(snapshot_block=portfolio.snapshot_block,
                              protocol_id=portfolio.protocol_id, wallet_address=w.wallet,
                              adapter_hf=health_factor(w, portfolio.assets, prices),
                              adapter_debt_usd=debt_value(w, prices)) for w in portfolio.wallets])
    joined = ours.merge(selected[keys + ['health_factor']], on=keys, how='outer',
                        validate='one_to_one', indicator=True)
    joined['emode_category'] = joined.wallet_address.map(categories)
    joined['hf_abs_error'] = (joined.adapter_hf - joined.health_factor).abs()
    joined['eligibility_disagreement'] = (
        joined['_merge'].eq('both') & (joined.adapter_hf.lt(1) != joined.health_factor.lt(1)))
    joined['cause_status'] = 'requires_N4_investigation'
    return joined.rename(columns={'_merge': 'match_status'})


def graph_node_metrics(edges):
    """Directed degree/strength of exposure edges only; not DebtRank or loss."""
    exposure = edges[edges.edge_type.isin(['collateral_exposure', 'debt_exposure'])]
    outgoing = exposure.groupby(['snapshot_block', 'source_node_id']).agg(
        out_degree=('target_node_id', 'nunique'), out_strength_usd=('weight_usd_t0', 'sum'))
    outgoing.index.names = ['snapshot_block', 'node_id']
    incoming = exposure.groupby(['snapshot_block', 'target_node_id']).agg(
        in_degree=('source_node_id', 'nunique'), in_strength_usd=('weight_usd_t0', 'sum'))
    incoming.index.names = ['snapshot_block', 'node_id']
    result = outgoing.join(incoming, how='outer').fillna(0).reset_index()
    for col in ['out_degree', 'in_degree']:
        result[col] = result[col].astype('int64')
    result['metric_scope'] = 'exposure_edges_only; not DebtRank'
    return result


def map_dex_depth(portfolio, dex):
    """Map exact block + input address. Missing/ambiguous depth stays null."""
    selected = dex[dex.snapshot_block.eq(portfolio.snapshot_block)]
    rows = []
    for token, asset in portfolio.assets.items():
        matches = selected[selected.token_in_address.str.lower().eq(token)]
        verified = matches[matches.status.eq('verified_2pct_bracket')]
        depth = None
        if len(verified) == 1:
            depth = number(verified.iloc[0].dex_depth_2pct_usd, 'DEX depth', 1e-300)
        rows.append(dict(snapshot_block=portfolio.snapshot_block, token_address=token,
                         symbol=portfolio.symbols[token], dex_depth_2pct_usd=depth,
                         status='mapped' if depth is not None else
                         'ambiguous_direction' if len(verified) > 1 else 'missing_verified_direction',
                         pool_address=verified.iloc[0].pool_address if depth is not None else None,
                         token_out=verified.iloc[0].token_out if depth is not None else None,
                         calibration_status='pending', lambda_impact=None, gamma_exponent=None))
    return pd.DataFrame(rows)


def historical_candidates(events, canonical_columns):
    """Retain valid observed events only; no invented quote or zero filling."""
    keys = ['tx_hash', 'log_index']
    if events.duplicated(keys).any():
        raise ValueError('Duplicate historical event keys')
    valid = events.protocol_id.eq(PROTOCOL)
    for col in ['debt_repaid_usd', 'collateral_seized_usd']:
        values = pd.to_numeric(events[col], errors='coerce')
        valid &= values.map(lambda x: pd.notna(x) and math.isfinite(x) and x >= 0)
    if 'has_valid_usd' in events:
        valid &= events.has_valid_usd.eq(True)
    # Timestamp conversion raises on naive/invalid source values rather than guessing a zone.
    for ts in events.event_timestamp:
        if pd.Timestamp(ts).tzinfo is None:
            raise ValueError('Historical event timestamp needs a timezone')
    return events.loc[valid, canonical_columns].copy(), events.loc[~valid].copy()


def inspect_compound(directory):
    """Read imported exports only; keep coverage status independent of table QA."""
    directory = Path(directory)
    report = json.loads((directory / 'merge_report.json').read_text(encoding='utf-8'))
    tables = {name: pd.read_parquet(directory / f'n3_compound_{suffix}_partial.parquet')
              for name, suffix in [('positions', 'positions_long'), ('accounts', 'account_reference'),
                                   ('config', 'reserve_config'), ('overlap', 'overlap_wallets')]}
    p, a = tables['positions'], tables['accounts']
    keys = ['snapshot_block', 'market_id', 'wallet_address']
    if p.duplicated(keys + ['token_address', 'asset_role']).any() or a.duplicated(keys).any():
        raise ValueError('Duplicate Compound position/account key')
    if not set(p.asset_role) <= {'collateral', 'base_borrow', 'base_supply'}:
        raise ValueError('Unknown Compound asset role')
    for col in ['amount_token', 'price_usd', 'value_usd']:
        if not p[col].map(lambda v: math.isfinite(v) and v >= 0).all():
            raise ValueError(f'Invalid Compound {col}')
    if not ((p.amount_token * p.price_usd - p.value_usd).abs() <=
            (1e-7 + p.value_usd.abs() * 1e-9)).all():
        raise ValueError('Compound token valuation mismatch')
    totals = p.pivot_table(index=keys, columns='asset_role', values='value_usd',
                           aggfunc='sum', fill_value=0).reset_index()
    joined = a.merge(totals, on=keys, how='outer', validate='one_to_one', indicator=True)
    mismatches = int(joined['_merge'].ne('both').sum())
    for role, reference in [('collateral', 'compound_collateral_usd'),
                            ('base_borrow', 'compound_borrow_usd'),
                            ('base_supply', 'compound_base_supply_usd')]:
        actual = joined[role].fillna(0) if role in joined else pd.Series(0, index=joined.index)
        mismatches += int(((actual - joined[reference]).abs() >
                          (1e-7 + joined[reference].abs() * 1e-9)).sum())
    return dict(table_qa_status='passed' if mismatches == 0 else 'failed',
                account_reconciliation_mismatches=mismatches,
                rows={k: len(v) for k, v in tables.items()},
                complete=report['complete'], expected_queries=report['expected_queries'],
                covered_queries=report['covered_queries'], missing_queries=report['missing_queries'],
                missing_unique_wallets=report['missing_unique_wallets'],
                validation_status='unvalidated',
                limitation='Partial coverage; unqueried wallets are unknown, never zero. No bridge simulation.'), tables


def write_manifest(directory, inputs, artifacts, summary):
    """Hash exact bytes; a preparation bundle is always unvalidated."""
    from datetime import datetime, timezone
    directory = Path(directory).resolve()
    def describe(path, kind):
        path = Path(path).resolve()
        raw = path.read_bytes()
        return dict(path=str(path), bytes=len(raw), sha256=hashlib.sha256(raw).hexdigest(),
                    data_kind=kind, validation_status='unvalidated')
    records = []
    for relative, kind in artifacts.items():
        p = (directory / relative).resolve()
        if not p.is_relative_to(directory):
            raise ValueError('Artifact path escapes bundle')
        record = describe(p, kind)
        record['path'] = p.relative_to(directory).as_posix()
        records.append(record)
    manifest = dict(bundle_version='n5_offline_preparation_v1',
                    created_utc=datetime.now(timezone.utc).isoformat(),
                    validation_status='unvalidated', research_acceptance='pending',
                    scope='Offline preparation; observed candidates and synthetic demo kept separate',
                    inputs=[describe(p, 'source') for p in inputs], files=records, summary=summary)
    (directory / 'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False,
                                                       indent=2, allow_nan=False), encoding='utf-8')
    return manifest


def verify_manifest(directory):
    directory = Path(directory).resolve()
    manifest = json.loads((directory / 'manifest.json').read_text(encoding='utf-8'))
    errors = []
    for r in manifest['files']:
        p = (directory / r['path']).resolve()
        if not p.is_relative_to(directory):
            raise ValueError('Manifest path escapes bundle')
        if not p.is_file() or hashlib.sha256(p.read_bytes()).hexdigest() != r['sha256']:
            errors.append(r['path'])
    return errors
