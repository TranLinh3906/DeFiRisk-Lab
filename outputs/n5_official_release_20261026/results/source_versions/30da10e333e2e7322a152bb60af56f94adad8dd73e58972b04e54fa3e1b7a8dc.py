"""Prepare the research decisions supplied on 25 October, without acceptance claims."""
import numpy as np
import pandas as pd
from scipy.stats import rankdata, t

from src.models.n5_research import student_t_copula_shocks


def validate_protocol_decisions(protocol, snapshots):
    """Reject snapshot drift or unsupported interpretation of user decisions."""
    for label, block in protocol['confirmed_snapshot_blocks'].items():
        if snapshots['snapshots'].get(label, {}).get('block') != block:
            raise ValueError(f'Confirmed {label} block differs from snapshot config')
    holdout = protocol['holdout']
    if (holdout['primary_peak_selection'] != 'maximum_liquidation_event_count'
            or holdout['peak_window_blocks'] != 300
            or holdout['tie_break'] != 'earliest_event_start'
            or holdout['secondary_peak_selection'] != 'first_oracle_eth_decline'
            or holdout['secondary_eth_return_threshold'] != -.05):
        raise ValueError('Unsupported or unconfirmed holdout selection rule')
    return holdout


def calibration_returns(reference, snapshot_timestamp, days=365):
    """366 daily reference levels produce 365 paired simple daily returns.

    The daily close reference is asynchronous and requires N3 confirmation.
    Quote times must precede day-end and the actual snapshot timestamp.
    """
    cutoff = pd.Timestamp(snapshot_timestamp)
    if cutoff.tzinfo is None or cutoff.utcoffset().total_seconds() != 0:
        raise ValueError('Snapshot needs explicit UTC')
    data = reference.copy()
    data['date'] = pd.to_datetime(data.date, utc=True)
    if data.date.duplicated().any():
        raise ValueError('Duplicate daily reference')
    last = cutoff.normalize()
    expected = pd.date_range(last-pd.Timedelta(days=days), last, freq='D')
    data = data.set_index('date').reindex(expected)
    for field in ['eth_usd_implied', 'steth_eth_ratio_daily_ref']:
        values = pd.to_numeric(data[field], errors='coerce')
        if not np.isfinite(values).all() or values.le(0).any():
            raise ValueError(f'Missing/nonpositive daily level: {field}')
    day_end = data.index + pd.Timedelta(days=1)-pd.Timedelta(seconds=1)
    if (day_end > cutoff).any():
        raise ValueError('Snapshot occurs before the last daily close is available')
    for field in ['usdt_quote_utc', 'steth_quote_utc']:
        times = pd.to_datetime(data[field], utc=True)
        if times.isna().any() or (times > day_end).any() or (times < data.index).any():
            raise ValueError(f'Missing/stale/future daily quote: {field}')
    out = pd.DataFrame(dict(date=data.index, eth_usd=data.eth_usd_implied,
        steth_eth=data.steth_eth_ratio_daily_ref))
    out['eth_return'] = out.eth_usd.pct_change(fill_method=None)
    out['depeg_return'] = out.steth_eth.pct_change(fill_method=None)
    return out.iloc[1:].reset_index(drop=True)


def calibrated_scenarios(returns, n=10000, seed=42, df=4):
    """Rank-transform moment estimate of t-copula correlation, fixed df.

    Uses simple return empirical marginals; no empirical acceptance or MLE claim.
    """
    values = returns[['eth_return','depeg_return']].to_numpy(dtype=float)
    if len(values)<3 or not np.isfinite(values).all() or (values<=-1).any():
        raise ValueError('Invalid paired calibration returns')
    scores = np.column_stack([t.ppf(rankdata(values[:,i])/(len(values)+1), df) for i in range(2)])
    if np.any(scores.std(axis=0)==0):
        raise ValueError('Cannot estimate correlation from constant returns')
    rho = float(np.clip(np.corrcoef(scores.T)[0,1], -.999999, .999999))
    scenarios = student_t_copula_shocks(n,values[:,0],values[:,1],rho=rho,df=df,seed=seed)
    return scenarios, dict(df=df,seed=seed,scenarios=n,rho=rho,calibration_pairs=len(values),
        method='Pearson correlation of rank-transformed t quantiles; empirical simple-return marginals',
        validation_status='candidate_pending_N3_price_confirmation')


def peak_event_window(events, width=300):
    """Maximize LiquidationCall event COUNT in an inclusive fixed block window.

    Operates on an already selected full holdout. This is retrospective window
    selection and must not be presented as prospective prediction evidence.
    """
    if isinstance(width,bool) or not isinstance(width,int) or width<1:
        raise ValueError('Invalid block width')
    if events.duplicated(['tx_hash','log_index']).any():
        raise ValueError('Duplicate events')
    if events.empty:
        return dict(status='no_events',start_block=None,end_block=None,event_count=0)
    blocks=np.sort(events.event_block.to_numpy(dtype=np.int64))
    best=(0,None,None); right=0
    for left,start in enumerate(blocks):
        while right<len(blocks) and blocks[right]<start+width:
            right+=1
        if right-left>best[0]:
            best=(right-left,int(start),int(start+width-1))
    return dict(status='retrospective_candidate',start_block=best[1],end_block=best[2],
        event_count=best[0],selection='maximum LiquidationCall event count; earliest event-start on ties',
        duration='300 blocks is not assumed to equal exactly one hour')


def dual_hit_rates(evidence):
    """One observation per wallet; USD weights are initial snapshot debt.

    Freeze cases are reported separately but remain in raw denominators.
    Oracle prediction rows must be generated separately with valid event timing.
    """
    if evidence.wallet_address.duplicated().any():
        raise ValueError('Need one observation per wallet')
    debt=pd.to_numeric(evidence.initial_debt_usd,errors='coerce')
    if not np.isfinite(debt).all() or debt.lt(0).any():
        raise ValueError('Missing/invalid initial debt; do not fill unknown wallets with zero')
    for field in ['hit','freeze_confirmed']:
        if not evidence[field].map(lambda v:isinstance(v,(bool,np.bool_))).all():
            raise ValueError('Boolean evidence required')
    total=float(debt.sum())
    return dict(wallet_count=len(evidence),wallet_hit_rate=float(evidence.hit.mean()) if len(evidence) else None,
        initial_debt_usd=total,debt_weighted_hit_rate=float(debt[evidence.hit].sum()/total) if total else None,
        freeze_wallets=int(evidence.freeze_confirmed.sum()),
        policy='Confirmed freezes separate; raw hit-rate denominator unchanged')
