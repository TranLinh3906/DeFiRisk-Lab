"""Validation against sampled Aave oracle paths; no event-feed completeness claims."""
import math
import numpy as np
import pandas as pd


def audit_oracle_path(path, summary, snapshots, snapshot_prices):
    p=path.copy()
    p['token_address']=p.token_address.str.lower()
    p['block_timestamp_utc']=pd.to_datetime(p.block_timestamp_utc,utc=True)
    if p.duplicated(['snapshot_id','block','token_address']).any():
        raise ValueError('Duplicate oracle sample key')
    if not np.isfinite(p.price_usd).all() or p.price_usd.le(0).any():
        raise ValueError('Nonpositive/nonfinite oracle price')
    raw=p.oracle_price_raw.map(lambda x:int(x)/1e8)
    if not np.allclose(raw,p.price_usd,rtol=1e-12,atol=1e-10):
        raise ValueError('Oracle raw/USD conversion mismatch')
    reports={}
    for label,snap in snapshots['snapshots'].items():
        if not snap.get('window_end_exclusive_utc'):continue
        g=p[p.snapshot_id.eq(label)]
        if g.empty:raise ValueError(f'Missing oracle path {label}')
        start=pd.Timestamp(snap['block_timestamp_utc']); end=pd.Timestamp(snap['window_end_exclusive_utc'])
        if g.block.min()!=snap['block'] or g.block_timestamp_utc.min()!=start:
            raise ValueError('Oracle start differs from snapshot')
        if (g.block_timestamp_utc<start).any() or (g.block_timestamp_utc>=end).any():
            raise ValueError('Oracle sample outside configured window')
        if g.groupby('block').block_timestamp_utc.nunique().gt(1).any():
            raise ValueError('Conflicting timestamps for a block')
        blocks=g[['block','block_timestamp_utc']].drop_duplicates().sort_values('block')
        if not blocks.block_timestamp_utc.is_monotonic_increasing:
            raise ValueError('Oracle timestamps not monotonic')
        evidence=summary[label]
        expected=list(range(evidence['block_start'],evidence['block_end']+1,evidence['step_blocks']))
        if expected[-1]!=evidence['block_end']:expected.append(evidence['block_end'])
        if blocks.block.tolist()!=expected or len(blocks)!=evidence['samples']:
            raise ValueError('Oracle sample grid differs from N2 summary')
        if g.groupby('block').token_address.nunique().nunique()!=1:
            raise ValueError('Missing token sample within oracle grid')
        if g.groupby('token_address').block.nunique().ne(len(blocks)).any():
            raise ValueError('Token grid has holes')
        initial=g[g.block.eq(snap['block'])].set_index('token_address').price_usd
        refs=snapshot_prices[snapshot_prices.snapshot_block.eq(snap['block'])].copy()
        refs['token_address']=refs.token_address.str.lower()
        ref=refs.set_index('token_address').price_usd
        if not set(initial.index)<=set(ref.index) or not np.allclose(initial,ref.reindex(initial.index),rtol=1e-10,atol=1e-8):
            raise ValueError('Path starting prices differ from snapshot oracle')
        minima={}
        for symbol,group in g.groupby('symbol'):
            row=group.loc[group.price_usd.idxmin()]
            reported=evidence['tokens'][symbol]
            if int(row.block)!=reported['min_block'] or not math.isclose(row.price_usd,reported['min_price_in_window'],rel_tol=1e-12):
                raise ValueError('N2 trough summary differs from samples')
            minima[symbol]=dict(min_price_usd=float(row.price_usd),block=int(row.block),timestamp=row.block_timestamp_utc.isoformat())
        reports[label]=dict(status='passed_sample_grid',samples=len(blocks),tokens=len(initial),rows=len(g),
            step_blocks=evidence['step_blocks'],last_block=int(blocks.block.max()),sampled_minima=minima,
            limitation='Sampled getAssetPrice, not complete AnswerUpdated history; extrema between samples unknown')
    return p,reports


def _rates(evidence):
    known=evidence.initial_debt_usd.notna()
    debt=evidence.loc[known,'initial_debt_usd']
    total=float(debt.sum())
    hits=evidence.hit.astype(bool)
    scorable=evidence.in_snapshot.astype(bool)&evidence.fully_priced.astype(bool)&evidence.min_hf_before_event.notna()
    unknown=int((~scorable).sum())
    weighted=float(evidence.loc[known & hits,'initial_debt_usd'].sum()/total) if total else None
    return dict(observed_wallets=len(evidence),hit_wallets=int(hits.sum()),
        wallet_hit_rate=float(hits.mean()) if len(evidence) else None,
        wallet_hit_rate_interpretation='conservative lower bound; unscorable observations retained, not proven model misses',
        unscorable_wallets=unknown,scorable_wallets=int(scorable.sum()),
        scorable_wallet_hit_rate=float(hits[scorable].mean()) if scorable.any() else None,
        wallet_hit_rate_upper_bound=float((hits.sum()+unknown)/len(evidence)) if len(evidence) else None,
        missing_state_wallets=int((~evidence.in_snapshot).sum()),
        incomplete_token_price_wallets=int((evidence.in_snapshot & ~evidence.fully_priced).sum()),
        initial_debt_known_usd=total,
        debt_weighted_hit_rate=weighted if known.all() else None,
        known_state_debt_weighted_hit_rate=weighted,
        unknown_debt_wallets=int((~known).sum()),freeze_status='not_classified_no_gas_execution_evidence')


def validate_sampled_holdout(positions, path, events, snapshot, primary, *, threshold=1.02):
    """Compute pre-event HF and separate retrospective coherent trough checks.

    Sample must be in a STRICTLY EARLIER block than the liquidation: intra-block
    ordering is unknown. Unpriced active tokens are flagged, never assumed safe.
    Each window starts with its preceding sample as the carried oracle state.
    """
    if not math.isfinite(threshold) or threshold<=0:raise ValueError('Invalid HF threshold')
    p=positions[positions.snapshot_block.eq(snapshot['block'])].copy()
    p['token_address']=p.token_address.str.lower()
    if p.duplicated(['wallet_address','token_address']).any():raise ValueError('Duplicate positions')
    q=path[path.snapshot_id.eq(snapshot['label'])].copy()
    prices=q.pivot(index='block',columns='token_address',values='price_usd').sort_index()
    blocks=prices.index.to_numpy()
    observed=events[events.validation_snapshot_id.eq(snapshot['label'])].copy()
    observed['event_timestamp']=pd.to_datetime(observed.event_timestamp,utc=True)
    wallets=pd.Index(sorted(observed.wallet_address.unique()),name='wallet_address')
    p=p[p.wallet_address.isin(wallets)]
    tokens=pd.Index(sorted(p.token_address.unique()),name='token_address')
    p['weighted_quantity']=p.collateral_amount*p.is_collateral_enabled*p.effective_lt
    active=p.weighted_quantity.gt(0)|p.debt_amount.gt(0)
    incomplete=set(p.loc[active & ~p.token_address.isin(prices.columns),'wallet_address'])
    present=set(p.wallet_address)
    refs=p.groupby('token_address').price_usd.first()
    # Frozen-price arithmetic is retained for diagnosis only; incomplete wallets cannot count as hits.
    quote=prices.reindex(columns=tokens)
    for token in tokens:
        if token not in prices:quote[token]=refs.get(token,np.nan)
    c=p.pivot(index='wallet_address',columns='token_address',values='weighted_quantity').reindex(index=wallets,columns=tokens).fillna(0).to_numpy()
    d=p.pivot(index='wallet_address',columns='token_address',values='debt_amount').reindex(index=wallets,columns=tokens).fillna(0).to_numpy()
    cp=quote.to_numpy()@c.T; dp=quote.to_numpy()@d.T
    hf=np.divide(cp,dp,out=np.full_like(cp,np.nan),where=dp>0)
    debt0=(p.debt_amount*p.price_usd).groupby(p.wallet_address).sum()
    block_times=q.groupby('block').block_timestamp_utc.first().reindex(blocks)
    modes={'full':(int(blocks[0]),int(blocks[-1])),
           'primary_peak_300':(primary['start_block'],primary['end_block'])}
    eth=q[q.symbol.str.upper().eq('WETH')].sort_values('block')
    crossed=eth[eth.price_usd.le(float(eth.iloc[0].price_usd)*.95)]
    secondary=dict(status='threshold_not_seen_in_samples')
    if not crossed.empty:
        start=int(crossed.iloc[0].block)
        modes['secondary_sampled_eth_minus5']=(start,start+299)
        secondary=dict(status='first_sample_at_or_below_minus5_not_exact_update',start_block=start,end_block=start+299)
    evidence_parts=[];event_parts=[];reports={}
    for mode,(start,end) in modes.items():
        if start is None:continue
        ev=observed if mode=='full' else observed[observed.event_block.between(start,end)]
        # Carry the STRICTLY prior block into a window whose first event may
        # occur at `start`; a quote at start is only known after that block.
        begin=max(0,int(np.searchsorted(blocks,start,side='left')-1))
        stop=int(np.searchsorted(blocks,end,side='right'))
        # A coherent trough: all token prices come from the SAME sample block.
        trough=np.min(np.where(np.isfinite(hf[begin:stop]),hf[begin:stop],np.inf),axis=0) if stop>begin else np.full(len(wallets),np.nan)
        trough[~np.isfinite(trough)]=np.nan
        rows=[];erows=[]
        for wallet,g in ev.groupby('wallet_address'):
            j=wallets.get_loc(wallet);min_before=np.nan
            full=wallet in present and wallet not in incomplete
            for e in g.itertuples():
                idx=int(np.searchsorted(blocks,e.event_block,side='left')-1)
                while idx>=begin and block_times.iloc[idx]>e.event_timestamp:idx-=1
                value=float(np.nanmin(hf[begin:idx+1,j])) if idx>=begin and np.isfinite(hf[begin:idx+1,j]).any() else np.nan
                if np.isfinite(value):min_before=value if not np.isfinite(min_before) else min(min_before,value)
                erows.append(dict(window=mode,wallet_address=wallet,tx_hash=e.tx_hash,log_index=e.log_index,
                    event_block=e.event_block,last_sample_block=int(blocks[idx]) if idx>=begin else None,
                    min_hf_before_event=value,fully_priced=full,hit=bool(full and value<threshold)))
            rows.append(dict(window=mode,wallet_address=wallet,in_snapshot=wallet in present,fully_priced=full,
                initial_debt_usd=float(debt0[wallet]) if wallet in present else np.nan,
                min_hf_before_event=min_before,hit=bool(full and min_before<threshold),
                retrospective_coherent_trough_hf=float(trough[j]),
                retrospective_coherent_trough_hit=bool(full and trough[j]<threshold)))
        columns=['window','wallet_address','in_snapshot','fully_priced','initial_debt_usd',
            'min_hf_before_event','hit','retrospective_coherent_trough_hf','retrospective_coherent_trough_hit']
        evidence=pd.DataFrame(rows,columns=columns)
        rates=_rates(evidence)
        retrospective=evidence.copy();retrospective['hit']=retrospective.retrospective_coherent_trough_hit
        retrospective['min_hf_before_event']=retrospective.retrospective_coherent_trough_hf
        reports[mode]=dict(start_block=start,end_block=end,events=len(ev),pre_event_sampled_oracle=rates,
            retrospective_coherent_trough=_rates(retrospective),
            debt_ge_100_sensitivity=_rates(evidence[evidence.initial_debt_usd.ge(100)]),
            acceptance='pending_complete_feeds_depeg_coverage_and_validation',
            limitation='Sampled Aave oracle only; no separately supplied depeg; missing tokens excluded from hits but retained as misses')
        evidence_parts.append(evidence);event_parts.append(pd.DataFrame(erows))
    return pd.concat(evidence_parts,ignore_index=True),pd.concat(event_parts,ignore_index=True),dict(
        windows=reports,secondary_window=secondary,oracle_tokens=q.symbol.unique().tolist(),
        missing_active_tokens=sorted(set(p.loc[active,'token_address'])-set(prices.columns)))
