"""Independent intake checks for N3 quote grids and price-coverage evidence."""
import math
import numpy as np
import pandas as pd

from src.models.n5_snapshots import select_event_windows
from src.models.n5_research_protocol import peak_event_window


def interpolate_average_impact(points,trade_size_usd):
    """Derived interpolation of average slippage, never a new RPC observation.

    Refuse extrapolation or conflicting sizes. The caller must retain the
    quoted interval width: sparse intervals do not certify the true pool curve.
    """
    if not math.isfinite(trade_size_usd) or trade_size_usd<0:raise ValueError('Invalid trade size')
    if trade_size_usd==0:return dict(impact_pct=0.,method='zero_trade_identity',bracket_width_usd=0.)
    p=points[['trade_size_usd','impact_pct']].drop_duplicates().sort_values('trade_size_usd')
    if len(p)<2 or p.trade_size_usd.duplicated().any() or not np.isfinite(p.to_numpy()).all():
        raise ValueError('Need unambiguous finite quote points for one direction')
    if p.trade_size_usd.le(0).any() or p.impact_pct.lt(0).any() or (np.diff(p.impact_pct)<-1e-7).any():
        raise ValueError('Nonpositive sizes or nonmonotonic impacts')
    x=p.trade_size_usd.to_numpy();y=p.impact_pct.to_numpy()
    if not x[0]<=trade_size_usd<=x[-1]:raise ValueError('Trade outside quoted domain; no extrapolation')
    exact=np.flatnonzero(x==trade_size_usd)
    if len(exact):return dict(impact_pct=float(y[exact[0]]),method='observed_point',bracket_width_usd=0.)
    right=int(np.searchsorted(x,trade_size_usd));left=right-1
    return dict(impact_pct=float(np.interp(trade_size_usd,x,y)),method='derived_linear_interpolation_not_rpc_verified',
        bracket_width_usd=float(x[right]-x[left]))


def calibrate_quote_grid(quotes, anchors, dex, snapshots, *, quantization_policy=None):
    keys=['snapshot_id','snapshot_block','pool_address','token_in_address','token_out_address']
    q=quotes.copy()
    if not q.status.eq('RPC_VERIFIED').all():raise ValueError('Unverified quote in verified-only input')
    if q.duplicated(keys+['trade_size_usd']).any():raise ValueError('Duplicate quote request')
    expected=dex[keys+['dex_depth_2pct_usd']]
    if expected.duplicated(keys).any():raise ValueError('Ambiguous depth direction')
    q=q.merge(expected,on=keys,how='left',validate='many_to_one')
    if q.dex_depth_2pct_usd.isna().any():raise ValueError('Quote direction has no same-block depth')
    for field in ['trade_size_usd','input_token_usd_oracle','spot_output_raw_per_input_raw']:
        if not np.isfinite(q[field]).all() or q[field].le(0).any():raise ValueError(f'Invalid {field}')
    errors=[]
    for row in q.itertuples():
        config=snapshots['snapshots'][row.snapshot_id]
        expected_hash=config['block_hash'].lower().removeprefix('0x')
        if row.snapshot_block!=config['block'] or any(str(h).lower().removeprefix('0x')!=expected_hash for h in [row.snapshot_block_hash,row.verified_block_hash]):
            raise ValueError('Quote block/hash differs from config')
        amount,out=int(row.amount_in_raw),int(row.amount_out_raw)
        if amount<=0 or out<=0:raise ValueError('Nonpositive raw quote')
        impact=100*(1-(out/amount)/row.spot_output_raw_per_input_raw)
        if not math.isclose(impact,row.impact_pct,rel_tol=1e-7,abs_tol=1e-8):
            raise ValueError('Reported impact differs from raw amounts/reference quote')
        actual_usd=amount/(10**int(row.input_decimals))*row.input_token_usd_oracle
        quantum=row.input_token_usd_oracle/(10**int(row.input_decimals))
        if abs(actual_usd-row.trade_size_usd)>quantum+1e-7*row.trade_size_usd:
            raise ValueError('Raw input amount differs from requested USD trade')
        errors.append(abs(impact-row.impact_pct))
    if not set(map(tuple,anchors[keys].drop_duplicates().to_numpy()))<=set(map(tuple,expected[keys].to_numpy())):
        raise ValueError('Anchor direction outside expected set')
    adjustments=[]
    q['raw_impact_pct']=q.impact_pct
    q['point_status']='observed_quote'
    if quantization_policy not in [None,'conservative_raw_unit_bound']:
        raise ValueError('Unknown quantization policy')
    for index,row in q[q.impact_pct.lt(0)].iterrows():
        if quantization_policy is None:
            raise ValueError('Negative observed impact requires explicit quantization review')
        # N3 fetch_dex_liquidity uses a 10^-4 input-token reference quote.
        # A floored output integer means its true reference ratio lies within
        # one output raw unit. Retain raw observation; use the conservative
        # nonnegative upper impact bound only under this named policy.
        tiny=max(1,10**max(int(row.input_decimals)-4,0))
        reference=row.spot_output_raw_per_input_raw
        if not math.isclose(reference*tiny,round(reference*tiny),abs_tol=1e-6,rel_tol=0):
            raise ValueError('Cannot certify integer reference quantization')
        upper=100*(1-(int(row.amount_out_raw)/int(row.amount_in_raw))/(reference+1/tiny))
        if upper<0 or not math.isfinite(upper):
            raise ValueError('Negative impact exceeds reference raw-unit bound')
        q.loc[index,'impact_pct']=upper
        q.loc[index,'point_status']='conservative_reference_quantization_upper_bound_not_observed'
        adjustments.append(dict(snapshot_id=row.snapshot_id,pool_address=row.pool_address,
            trade_size_usd=float(row.trade_size_usd),raw_impact_pct=float(row.raw_impact_pct),
            model_upper_impact_pct=float(upper),reference_input_raw=str(tiny)))
    fits=[];residuals=[]
    for direction,group in q.groupby(keys):
        g=group.sort_values('trade_size_usd')
        if g.trade_size_usd.nunique()<5:raise ValueError('Need at least five quoted sizes per direction')
        if g.impact_pct.lt(0).any() or np.diff(g.impact_pct).min() < -1e-7:
            raise ValueError('Negative/nonmonotonic observed impact')
        a=anchors
        for name,value in zip(keys,direction):a=a[a[name].eq(value)]
        points=pd.concat([g[['trade_size_usd','impact_pct']],a[['trade_size_usd','impact_pct']]],ignore_index=True)
        points=points.drop_duplicates().sort_values('trade_size_usd')
        if points.duplicated('trade_size_usd').any():raise ValueError('Conflicting quoted impacts at same size')
        positive=points.impact_pct.gt(0)
        depth=float(g.dex_depth_2pct_usd.iloc[0])
        x=np.log(points.loc[positive,'trade_size_usd'].to_numpy()/depth)
        y=np.log(points.loc[positive,'impact_pct'].to_numpy()/100)
        gamma,intercept=np.polyfit(x,y,1)
        alpha=float(np.exp(intercept))
        if not math.isfinite(gamma) or gamma<=0 or not 0<alpha<1:raise ValueError('Unphysical power fit')
        predicted=100*alpha*(points.trade_size_usd.to_numpy()/depth)**gamma
        metadata=dict(zip(keys,direction))
        fits.append(dict(metadata,quoted_sizes=int(g.trade_size_usd.nunique()),anchor_points=len(a),
            dex_depth_usd=depth,average_impact_alpha=alpha,gamma_exponent=float(gamma),
            engine_terminal_lambda_if_trapezoid_from_zero=2*alpha,
            min_calibrated_trade_usd=float(points.trade_size_usd.min()),max_calibrated_trade_usd=float(points.trade_size_usd.max()),
            rmse_impact_percentage_points=float(np.sqrt(np.mean((predicted-points.impact_pct.to_numpy())**2))),
            max_abs_residual_percentage_points=float(np.max(abs(predicted-points.impact_pct.to_numpy()))),
            status='fit_candidate_review_residuals',
            limitation='Average execution slippage fit; not calibrated endogenous oracle feedback. No extrapolation approval.'))
        for (_,row),value in zip(points.iterrows(),predicted):
            raw_row=g[g.trade_size_usd.eq(row.trade_size_usd)]
            residuals.append(dict(metadata,trade_size_usd=row.trade_size_usd,observed_impact_pct=row.impact_pct,
                raw_observed_impact_pct=float(raw_row.raw_impact_pct.iloc[0]) if len(raw_row) else row.impact_pct,
                point_status=raw_row.point_status.iloc[0] if len(raw_row) else 'observed_anchor',
                fitted_impact_pct=float(value),residual_percentage_points=float(value-row.impact_pct)))
    return pd.DataFrame(fits),pd.DataFrame(residuals),dict(status='passed_internal_quote_checks',
        verified_quotes=len(q),directions=len(fits),max_raw_recalculation_error_pct=max(errors),
        rpc_requeried=False,checkpoint_present=False,quantization_policy=quantization_policy,quantization_adjustments=adjustments,
        limitation='Verifies supplied raw quote fields; no independent RPC replay or provenance beyond supplied files')


def audit_market_coverage(events,quotes,snapshots,max_age_seconds=3600):
    """Quote availability at actual event token addresses, NOT model hit-rate.

    Recovered event-pricing quotes can verify valuation availability but cannot
    establish independent market-path coverage of every position in the wallet.
    """
    e,windows=select_event_windows(events,snapshots)
    if e.duplicated(['tx_hash','log_index','validation_snapshot_id']).any():raise ValueError('Duplicate event')
    q=quotes.copy()
    if not q.price_type.eq('market_reference').all() or not q.quote_asset.eq('USD').all():
        raise ValueError('Market USD reference quotes required')
    q['token_address']=q.token_address.str.lower()
    q['quote_time']=pd.to_datetime(q.reference_timestamp,utc=True)
    q['observation_time']=pd.to_datetime(q.timestamp,utc=True)
    if (q.quote_time>q.observation_time).any():raise ValueError('Future-dated source quote')
    if not np.isfinite(q.price_value).all() or q.price_value.le(0).any():raise ValueError('Invalid quote value')
    if q.groupby(['token_address','quote_time']).price_value.nunique().gt(1).any():raise ValueError('Conflicting market quotes')
    q=q.drop_duplicates(['token_address','quote_time'])
    e=e.copy();e['event_timestamp']=pd.to_datetime(e.event_timestamp,utc=True)
    for side in ['debt','collateral']:
        prices=pd.Series(np.nan,index=e.index); ages=pd.Series(np.nan,index=e.index)
        for address,indexes in e.groupby(f'{side}_token_address').groups.items():
            source=q[q.token_address.eq(address.lower())].sort_values('quote_time')
            if source.empty:continue
            left=e.loc[indexes,['event_timestamp']].copy();left['row_index']=indexes
            merged=pd.merge_asof(left.sort_values('event_timestamp'),source[['quote_time','price_value']].sort_values('quote_time'),
                left_on='event_timestamp',right_on='quote_time',direction='backward',tolerance=pd.Timedelta(seconds=max_age_seconds))
            ages.loc[merged.row_index]=(merged.event_timestamp-merged.quote_time).dt.total_seconds().to_numpy()
            prices.loc[merged.row_index]=merged.price_value.to_numpy()
        e[f'{side}_market_price_usd']=prices;e[f'{side}_quote_age_seconds']=ages
        e[f'{side}_quote_matched']=prices.notna()&ages.between(0,max_age_seconds)
    e['both_token_market_quotes']=e.debt_quote_matched&e.collateral_quote_matched
    reports={}
    for label,group in e.groupby('validation_snapshot_id'):
        reports[label]={}
        peak=peak_event_window(group)
        primary=group[group.event_block.between(peak['start_block'],peak['end_block'])]
        for name,g in [('full_configured_window',group),('first_hour_after_snapshot',group[group.first_hour]),('primary_peak_300',primary)]:
            reports[label][name]=dict(events=len(g),events_with_both_quotes=int(g.both_token_market_quotes.sum()),
                two_token_quote_coverage=float(g.both_token_market_quotes.mean()) if len(g) else None,
                interpretation='Valuation quote availability, NOT predictive HF hit-rate; NOT independent event completeness')
    return e,dict(windows=reports,configured_windows=windows,source_origin='historical_liquidation_priced',
        independent_full_wallet_price_path=False,event_collection_completeness='unverified')
