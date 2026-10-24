"""Strict N5 guards, numerical attribution and observational Compound adapters."""
from __future__ import annotations

import math
import pandas as pd

from src.models.n5_inputs import flag, number


def guard_stress_positions(frame, oracle=None):
    """Normalize explicit booleans, reject missing financial fields before N4 call."""
    data = frame.copy()
    keys = ['snapshot_block', 'protocol_id', 'wallet_address', 'token_address']
    for name in keys + ['collateral_amount', 'debt_amount', 'price_usd',
                        'effective_lt', 'is_collateral_enabled']:
        if name not in data:
            raise ValueError(f'Missing {name}')
    if data.duplicated(keys).any():
        raise ValueError('Duplicate position key')
    data['is_collateral_enabled'] = [flag(v, 'collateral flag') for v in data.is_collateral_enabled]
    for name in ['collateral_amount', 'debt_amount', 'price_usd', 'effective_lt']:
        data[name] = [number(v, name, 1e-300 if name == 'price_usd' else 0,
                             1 if name == 'effective_lt' else math.inf) for v in data[name]]
    if oracle is not None:
        px_keys = ['snapshot_block', 'protocol_id', 'token_address']
        if oracle.duplicated(px_keys).any():
            raise ValueError('Duplicate oracle key')
        prices = oracle[px_keys + ['price_usd']].rename(columns={'price_usd': 'oracle_price'})
        joined = data.merge(prices, on=px_keys, how='left', validate='many_to_one')
        for price, expected in zip(joined.price_usd, joined.oracle_price):
            expected = number(expected, 'oracle price', 1e-300)
            if not math.isclose(price, expected, rel_tol=1e-9):
                raise ValueError('Position price conflicts with same-block oracle')
    return data


def validate_zero_shock(rows, *, tolerance=.005):
    errors, flips, large = [], 0, 0
    seen = set()
    if not rows:
        errors.append('Empty zero-shock evidence')
    for row in rows:
        key = tuple(row[k] for k in ['snapshot_block', 'protocol_id', 'wallet'])
        if key in seen:
            errors.append('Duplicate wallet-snapshot')
        seen.add(key)
        try:
            before = number(row['hf_before'], 'hf_before')
            after = number(row['hf_after'], 'hf_after')
        except ValueError:
            errors.append('Invalid HF'); continue
        flips += (before < 1) != (after < 1)
        large += abs(before - after) >= tolerance
    return dict(status='failed' if errors or flips or large else 'passed',
                errors=errors, rows=len(rows), eligibility_disagreements=int(flips),
                hf_error_ge_tolerance=int(large), tolerance=tolerance,
                scope='Numerical zero-shock consistency; not research acceptance')


def parse_reverse_target(target, baseline_debt_usd):
    """N5 input guard matching documented N4 target units, with range checks."""
    baseline = number(baseline_debt_usd, 'baseline debt')
    if isinstance(target, dict):
        if 'usd' in target:
            return number(target['usd'], 'USD target')
        raw = target.get('pct', target.get('percent'))
        fraction = number(raw, 'percentage target', 0, 100)
        fraction = fraction / 100 if fraction > 1 else fraction
    elif isinstance(target, str) and target.strip().endswith('%'):
        fraction = number(target.strip()[:-1], 'percentage target', 0, 100) / 100
    else:
        return number(target, 'USD target')
    return baseline * fraction


def attribute_hf_differences(portfolio, positions, reserves, membership, risk, *,
                             model_mode='category_membership'):
    """Reconstruct the selected N4 pipeline version; attribute numerator deltas.

    The reconstruction is tested against the stored N4 HF. Unexplained residuals
    remain visible instead of assigning all disagreements to E-Mode by fiat.
    """
    block = portfolio.snapshot_block
    if model_mode not in ('category_membership', 'first_membership'):
        raise ValueError('Unknown N4 attribution mode')
    selected = membership[membership.snapshot_block.eq(block)]
    first = selected.drop_duplicates('token_address', keep='first')
    first = first.set_index('token_address').to_dict('index')
    if selected.duplicated(['emode_category', 'token_address']).any():
        raise ValueError('Duplicate E-Mode membership')
    by_category = selected.set_index(['emode_category', 'token_address']).to_dict('index')
    cfg = reserves[reserves.snapshot_block.eq(block)].set_index('token_address').to_dict('index')
    categories = positions[positions.snapshot_block.eq(block)].groupby('wallet_address').user_emode.first().to_dict()
    source_flags = positions[positions.snapshot_block.eq(block)].set_index(
        ['wallet_address', 'token_address']).coll_flag.to_dict() if 'coll_flag' in positions else {}
    per_token = []
    for row in portfolio.positions:
        token, wallet = row['token_address'], row['wallet_address']
        category = int(categories[wallet])
        chosen = first.get(token) if model_mode == 'first_membership' else by_category.get((category, token))
        chosen_category = (int(chosen['emode_category']) if model_mode == 'first_membership'
                           else category) if chosen is not None else 0
        standard = float(cfg[token]['lt_std'])
        n4_lt = float(chosen['emode_lt_bps']) / 10000 if (
            category > 0 and category == chosen_category and chosen['emode_lt_bps'] > 0) else standard
        # N4 main uses borrower_positions. The collateral quantity there already
        # has disabled rows zeroed; reserve usage_as_collateral is the fallback flag.
        n4_enabled = bool(cfg[token]['usage_as_collateral'])
        amount, price = row['collateral_amount'], row['price_usd']
        wallet_enabled = flag(source_flags.get((wallet, token), row['is_collateral_enabled']), 'wallet collateral')
        # borrower_positions zeros disabled collateral before the HF pipeline.
        n4_amount = amount * wallet_enabled if model_mode == 'category_membership' else amount
        n4_weighted = n4_amount * price * n4_lt * n4_enabled
        expected = amount * price * row['effective_lt'] * row['is_collateral_enabled']
        delta = n4_weighted - expected
        reason = 'no_weighted_difference'
        if not math.isclose(delta, 0, abs_tol=1e-7):
            if n4_enabled != wallet_enabled and amount > 0:
                reason = 'collateral_flag_difference'
            elif category != chosen_category and row['effective_lt'] != standard:
                reason = 'membership_category_dropped'
            elif chosen is not None and category == chosen_category and not bool(chosen['is_collateral']):
                reason = 'membership_collateral_permission_ignored'
            else:
                reason = 'lt_or_input_difference_requires_review'
        per_token.append(dict(snapshot_block=block, wallet_address=wallet, token_address=token,
            symbol=row['symbol'], user_category=category, selected_first_category=chosen_category,
            expected_effective_lt=row['effective_lt'], reconstructed_n4_lt=n4_lt,
            wallet_collateral_enabled=wallet_enabled, adapter_collateral_enabled=row['is_collateral_enabled'], n4_collateral_enabled=n4_enabled,
            membership_is_collateral=bool(chosen['is_collateral']) if chosen is not None else None,
            n4_collateral_amount=n4_amount, attribution_model=model_mode,
            collateral_amount=amount, price_usd=price, n4_weighted_collateral_usd=n4_weighted,
            adapter_weighted_collateral_usd=expected, weighted_delta_usd=delta, candidate_cause=reason))
    tokens = pd.DataFrame(per_token)
    aggregate = tokens.groupby('wallet_address').agg(
        reconstructed_n4_weighted_usd=('n4_weighted_collateral_usd', 'sum'),
        adapter_weighted_usd=('adapter_weighted_collateral_usd', 'sum')).reset_index()
    debt = pd.DataFrame(portfolio.positions)
    debt['debt_usd'] = debt.debt_amount * debt.price_usd
    debt = debt.groupby('wallet_address').debt_usd.sum().reset_index()
    aggregate = aggregate.merge(debt, on='wallet_address', validate='one_to_one')
    aggregate = aggregate.merge(risk[risk.snapshot_block.eq(block)][['wallet_address', 'health_factor']],
                                on='wallet_address', how='left', validate='one_to_one')
    aggregate['reconstructed_n4_hf'] = aggregate.reconstructed_n4_weighted_usd / aggregate.debt_usd
    aggregate['adapter_hf'] = aggregate.adapter_weighted_usd / aggregate.debt_usd
    aggregate['reconstruction_residual'] = aggregate.reconstructed_n4_hf - aggregate.health_factor
    aggregate['reconstruction_matches_n4'] = [math.isclose(a, b, rel_tol=1e-9, abs_tol=1e-7)
        for a, b in zip(aggregate.reconstructed_n4_hf, aggregate.health_factor)]
    causes = tokens[tokens.candidate_cause.ne('no_weighted_difference')].groupby('wallet_address').candidate_cause.agg(
        lambda values: '|'.join(sorted(set(values))))
    aggregate['candidate_causes'] = aggregate.wallet_address.map(causes).fillna('none')
    aggregate['eligibility_disagreement'] = aggregate.adapter_hf.lt(1) != aggregate.health_factor.lt(1)
    aggregate['snapshot_block'] = block
    aggregate['attribution_status'] = aggregate.reconstruction_matches_n4.map(
        {True: 'numerically_reconstructed', False: 'unexplained_residual'})
    tokens = tokens.merge(debt, on='wallet_address', validate='many_to_one')
    tokens['n4_hf_contribution'] = tokens.n4_weighted_collateral_usd / tokens.debt_usd
    tokens['adapter_hf_contribution'] = tokens.adapter_weighted_collateral_usd / tokens.debt_usd
    return tokens, aggregate


def check_reconstructed_hf(positions, risk, zero, tiny, *, tolerance=.005):
    """Check baseline against input arithmetic and detect a copied zero-shock HF."""
    keys = ['snapshot_block', 'protocol_id', 'wallet_address']
    p = guard_stress_positions(positions)
    p['weighted'] = p.collateral_amount * p.price_usd * p.effective_lt * p.is_collateral_enabled
    p['debt'] = p.debt_amount * p.price_usd
    out = p.groupby(keys).agg(weighted=('weighted', 'sum'), debt=('debt', 'sum')).reset_index()
    out['reconstructed_hf'] = out.weighted / out.debt.where(out.debt.gt(0))
    out.loc[out.debt.eq(0), 'reconstructed_hf'] = 999.  # N4 no-debt sentinel.
    for data, source, dest in [(risk, 'health_factor', 'baseline_hf'),
                                (zero, 'hf_after', 'zero_hf'), (tiny, 'hf_after', 'tiny_hf')]:
        data = data.rename(columns={'wallet': 'wallet_address'})
        if data.duplicated(keys).any():
            raise ValueError('Duplicate HF evidence keys')
        out = out.merge(data[keys + [source]].rename(columns={source: dest}),
                        on=keys, how='outer', validate='one_to_one')
    for col in ['reconstructed_hf', 'baseline_hf', 'zero_hf', 'tiny_hf']:
        out[f'{col}_valid'] = out[col].map(lambda v: pd.notna(v) and math.isfinite(v) and v >= 0)
    out['baseline_abs_error'] = (out.baseline_hf - out.reconstructed_hf).abs()
    out['tiny_jump_abs_error'] = (out.zero_hf - out.tiny_hf).abs()
    out['baseline_eligibility_disagreement'] = out.baseline_hf.lt(1) != out.reconstructed_hf.lt(1)
    valid = out[[f'{col}_valid' for col in ['reconstructed_hf','baseline_hf','zero_hf','tiny_hf']]].all(axis=1)
    out['passed'] = valid & out.baseline_abs_error.lt(tolerance) & out.tiny_jump_abs_error.lt(tolerance) & ~out.baseline_eligibility_disagreement
    return out, dict(status='passed' if out.passed.all() else 'failed', rows=len(out),
        invalid_evidence_rows=int((~valid).sum()),
        baseline_error_ge_tolerance=int(out.baseline_abs_error.ge(tolerance).sum()),
        tiny_jump_ge_tolerance=int(out.tiny_jump_abs_error.ge(tolerance).sum()),
        baseline_eligibility_disagreements=int(out.baseline_eligibility_disagreement.sum()))


def adapt_compound_partial(positions, config):
    """Keep Comet market/role identity; base supply is NOT collateral.

    No Aave bonus/close-factor or absorb execution is imposed on Comet balances.
    """
    keys = ['snapshot_block', 'market_id', 'token_address']
    pkeys = keys + ['wallet_address', 'asset_role']
    if positions.duplicated(pkeys).any() or config.duplicated(keys).any():
        raise ValueError('Duplicate Compound keys')
    cfg = config[keys + ['price_usd', 'liquidate_collateral_factor']].rename(columns={'price_usd': 'config_price'})
    out = positions.merge(cfg, on=keys, how='left', validate='many_to_one', indicator=True)
    if not out['_merge'].eq('both').all():
        raise ValueError('Missing Compound configuration')
    if not set(out.asset_role) <= {'collateral', 'base_borrow', 'base_supply'}:
        raise ValueError('Unsupported Compound role')
    for _, row in out.iterrows():
        for col in ['amount_token', 'value_usd']:
            number(row[col], col)
        price = number(row.price_usd, 'position price', 1e-300)
        expected = number(row.config_price, 'config price', 1e-300)
        if not math.isclose(price, expected, rel_tol=1e-9):
            raise ValueError('Compound position/config price mismatch')
        if not math.isclose(row.amount_token * price, row.value_usd, rel_tol=1e-9, abs_tol=1e-7):
            raise ValueError('Compound value mismatch')
        if row.asset_role == 'collateral':
            number(row.liquidate_collateral_factor, 'Comet liquidation collateral factor', 0, 1)
    out['is_collateral'] = out.asset_role.eq('collateral')
    out['is_debt'] = out.asset_role.eq('base_borrow')
    out['coverage_status'] = 'partial'
    out['validation_status'] = 'unvalidated'
    return out.drop(columns=['_merge'])


def track_compound_flows(exposures, events):
    """Ledger for EXPLICIT external/scenario token deltas; no inferred rebalancing.

    No real on-chain action. Prices held fixed; this is token bookkeeping only.
    """
    keys = ['snapshot_block', 'market_id', 'wallet_address', 'token_address', 'asset_role']
    if exposures.duplicated(keys).any():
        raise ValueError('Duplicate opening balance')
    out = exposures.copy()
    for name, low in [('amount_token', 0), ('price_usd', 1e-300)]:
        out[name] = [number(v, name, low) for v in out[name]]
    balances = {tuple(row[k] for k in keys): float(row['amount_token']) for _, row in out.iterrows()}
    prices = {tuple(row[k] for k in keys): float(row['price_usd']) for _, row in out.iterrows()}
    ledger, seen = [], set()
    for event in events:
        if not event.get('event_id') or event['event_id'] in seen or not event.get('reason'):
            raise ValueError('Event needs unique ID and explicit reason')
        seen.add(event['event_id'])
        key = tuple(event[k] for k in keys)
        if key not in balances:
            raise ValueError('Unknown Compound balance; never assume missing position is zero')
        delta = number(event['quantity_delta'], 'quantity delta', -math.inf)
        before = balances[key]
        after = before + delta
        if not math.isfinite(after) or after < 0:
            raise ValueError('Flow exceeds available token balance')
        balances[key] = after
        ledger.append(dict(event, before_token=before, after_token=after,
                           delta_usd_at_fixed_price=delta * prices[key],
                           validation_status='unvalidated', input_kind='explicit_scenario_or_external_event'))
    out['amount_token'] = [balances[tuple(row[k] for k in keys)] for _, row in out.iterrows()]
    out['value_usd'] = out.amount_token * out.price_usd
    return out, pd.DataFrame(ledger)
