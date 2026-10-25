"""Read-only diagnostics; strict gates and proposed policies remain separate."""
import math
import numpy as np
import pandas as pd


def tiny_shock_diagnostics(frame, max_relative_price_change=1e-12):
    """HF=C/D implies HF'/HF in [(1-h)/(1+h), (1+h)/(1-h)].

    This bounds any token price return in [-h,h]; it does not change the
    absolute HF gate or excuse baseline differences / eligibility flips.
    """
    h = float(max_relative_price_change)
    if not math.isfinite(h) or not 0 < h < 1:
        raise ValueError('Price change bound must be in (0,1)')
    out = frame.copy()
    out['tiny_relative_change'] = out.tiny_jump_abs_error / out.zero_hf.abs().replace(0, np.nan)
    out['tiny_analytic_bound'] = out.zero_hf.abs() * (2*h/(1-h) + 32*np.finfo(float).eps)
    out['tiny_within_price_bound'] = out.tiny_jump_abs_error.le(out.tiny_analytic_bound)
    valid = out[[c for c in out if c.endswith('_valid')]].all(axis=1)
    out['analytic_continuity_passed'] = (valid & out.tiny_within_price_bound &
        out.baseline_abs_error.lt(.005) & ~out.baseline_eligibility_disagreement)
    out['diagnosis'] = np.where(out.passed, 'strict_pass', np.where(
        out.analytic_continuity_passed, 'large_hf_expected_price_response', 'needs_investigation'))
    return out


def balance_policy_diagnostics(balances, debt_thresholds=(0., 1., 100.)):
    """Keep all wallets and quantify proposed dust policies without approving one."""
    out = balances.copy()
    for kind in ['debt', 'collateral']:
        actual, ref = out[f'{kind}_usd'], out[f'total_{kind}_base_usd']
        error = (actual-ref).abs()
        out[f'{kind}_relative_error'] = error / ref.abs().replace(0, np.nan)
        out[f'{kind}_strict_failed'] = error.gt(.01 * ref.abs())
        # A candidate numerical budget, not a replacement acceptance rule.
        out[f'{kind}_within_1e_6_usd'] = error.le(1e-6)
    rows = []
    for threshold in debt_thresholds:
        if not math.isfinite(threshold) or threshold < 0:
            raise ValueError('Invalid debt threshold')
        selected = out.total_debt_base_usd.ge(threshold)
        rows.append(dict(proposed_min_debt_usd=threshold, wallets=int(selected.sum()),
            excluded_wallets=int((~selected).sum()),
            excluded_reference_debt_usd=float(out.loc[~selected, 'total_debt_base_usd'].sum()),
            failed_debt_accounts=int((selected & out.debt_strict_failed).sum()),
            failed_collateral_accounts=int((selected & out.collateral_strict_failed).sum()),
            policy_status='proposal_requires_N1_N2_N4_review'))
    return out, pd.DataFrame(rows)


def anchor_price_impact(dex):
    """Match the engine's *average execution* impact to the observed 2% quote.

    Engine uses trapezoid average of before/after terminal impact. At empty
    flow and q=depth: average=lambda/2, hence lambda=.04 for gamma=1.
    One quote identifies one scale conditional on gamma, never the curve shape.
    """
    rows = []
    for r in dex.itertuples():
        if r.status != 'verified_2pct_bracket':
            continue
        depth, impact = float(r.dex_depth_2pct_usd), float(r.observed_impact_pct)/100
        if not math.isfinite(depth) or depth <= 0 or not 0 < impact < 1:
            raise ValueError('Invalid quoted depth/impact')
        for gamma in (.5, 1., 2.):
            rows.append(dict(snapshot_id=r.snapshot_id, snapshot_block=r.snapshot_block,
                token_address=r.token_in_address.lower(), pool_address=r.pool_address,
                token_out=r.token_out, dex_depth_usd=depth, gamma_exponent=gamma,
                lambda_impact=2*impact, quoted_average_impact=impact,
                predicted_average_impact=impact,
                calibration_status='single_quote_anchor_conditional_on_gamma',
                limitation='Shape assumed; no multi-hop, pool depletion or oracle lag fit'))
    return pd.DataFrame(rows)


def historical_open_price_validation(positions, events, hourly, snapshot, protocol='aave_v3_ethereum'):
    """Conditional market-price diagnostic, not an oracle forecast or certified OOS.

    Only hourly OPENs available at/before each event are used. Reference is
    the latest OPEN at/before snapshot. No candle low/close or future quote.
    ETH-linked LSTs inherit ETH only (fixed depeg); unsupported tokens are frozen
    and their collateral/debt exposure is reported explicitly.
    """
    start = pd.Timestamp(snapshot['block_timestamp_utc'])
    end = pd.Timestamp(snapshot['window_end_exclusive_utc'])
    block = snapshot['block']
    ev = events[events.event_block.gt(block) & events.protocol_id.eq(protocol)].copy()
    ev['event_timestamp'] = pd.to_datetime(ev.event_timestamp, utc=True)
    ev = ev[ev.event_timestamp.gt(start) & ev.event_timestamp.lt(end)]
    if ev.duplicated(['tx_hash', 'log_index']).any():
        raise ValueError('Duplicate observed event')
    p = positions[positions.snapshot_block.eq(block)].copy()
    p['c'] = p.collateral_amount*p.price_usd*p.is_collateral_enabled*p.effective_lt
    p['d'] = p.debt_amount*p.price_usd
    eth = {'ETH','WETH','WSTETH','STETH','WEETH','CBETH','RETH'}
    btc = {'BTC','WBTC','CBBTC'}
    p['driver'] = p.symbol.str.upper().map(lambda s: 'ETH' if s in eth else 'BTC' if s in btc else 'FIXED')
    weights = {driver: p[p.driver.eq(driver)].groupby('wallet_address')[['c','d']].sum()
               for driver in ['ETH','BTC','FIXED']}
    wallets = pd.Index(p.wallet_address.unique(), name='wallet_address')
    market = hourly.copy()
    market['timestamp_utc'] = pd.to_datetime(market.timestamp_utc, utc=True)
    if market.duplicated(['asset','timestamp_utc']).any():
        raise ValueError('Duplicate market timestamp')
    series, refs = {}, {}
    for asset in ['ETH','BTC']:
        s = market[market.asset.eq(asset)].set_index('timestamp_utc').open.sort_index()
        if s.empty or not np.isfinite(s).all() or s.le(0).any():
            raise ValueError('Missing/invalid market driver')
        past = s.loc[:start]
        if past.empty or start.value-past.index[-1].value > 3600_000_000_000:
            raise ValueError('Missing fresh pre-snapshot market quote')
        series[asset], refs[asset] = s, float(past.iloc[-1])
    times = sorted({start, *[t for s in series.values() for t in s.index if start<t<end]})
    observations=[]
    minimum = pd.Series(np.inf, index=wallets)
    for time in times:
        c,d = pd.Series(0.,index=wallets), pd.Series(0.,index=wallets)
        fresh=True
        for driver, w in weights.items():
            ratio=1.
            if driver!='FIXED':
                s=series[driver].loc[:time]
                fresh &= time.value-s.index[-1].value <= 3600_000_000_000
                ratio=float(s.iloc[-1])/refs[driver]
            c=c.add(w.c.reindex(wallets,fill_value=0)*ratio,fill_value=0)
            d=d.add(w.d.reindex(wallets,fill_value=0)*ratio,fill_value=0)
        hf=c/d.where(d.gt(0))
        minimum=pd.concat([minimum,hf],axis=1).min(axis=1)
        # Store only observed wallets; retain all model wallets in computations.
        for wallet in ev.wallet_address.unique():
            observations.append(dict(wallet_address=wallet,prediction_timestamp=time,
                min_hf_asof=float(minimum.get(wallet,np.nan)), driver_quotes_fresh=fresh))
    pred=pd.DataFrame(observations)
    if ev.empty:
        return pd.DataFrame(), dict(status='no_observations',hit_rate=None)
    joined=pd.merge_asof(ev.sort_values('event_timestamp'),pred.sort_values('prediction_timestamp'),
        left_on='event_timestamp',right_on='prediction_timestamp',by='wallet_address',direction='backward')
    joined['in_snapshot']=joined.wallet_address.isin(wallets)
    joined['hit']=joined.min_hf_asof.lt(1.02) & joined.in_snapshot & joined.driver_quotes_fresh.eq(True)
    first=joined[joined.event_timestamp.lt(pd.Timestamp(start.value+3600_000_000_000,tz='UTC'))]
    def score(frame):
        w=frame.groupby('wallet_address').agg(hit=('hit','any'),in_snapshot=('in_snapshot','any'))
        return dict(observed_wallets=len(w), matched_wallets=int(w.hit.sum()),
            missing_state_wallets=int((~w.in_snapshot).sum()),hit_rate=float(w.hit.mean()) if len(w) else None)
    return joined,dict(status='conditional_market_proxy_only',full_window=score(joined),first_hour=score(first),
        reference_open_prices=refs, frozen_weighted_collateral_usd=float(p.loc[p.driver.eq('FIXED'),'c'].sum()),
        frozen_debt_usd=float(p.loc[p.driver.eq('FIXED'),'d'].sum()),threshold=1.02,
        acceptance='pending', limitation='Observed supplied events only; fixed depeg/other assets; no oracle path or independent source completeness')
