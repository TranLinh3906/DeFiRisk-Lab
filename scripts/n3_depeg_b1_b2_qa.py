#!/usr/bin/env python3
"""N3: Independently audit observed MARKET stETH/ETH depeg over B1/B2.

Input n3_market_ratios.parquet: timestamp_hour_utc, steth_eth_market_ratio,
 wsteth_eth_market_ratio, wsteth_steth_market_ratio, source.
Never convert Lido stEthPerToken into a market depeg proxy.
No RPC, no edits to source; unresolved time windows stay INCOMPLETE.
"""
from __future__ import annotations
import argparse, json, math
from pathlib import Path
import pandas as pd


def utc_series(v):
    if pd.api.types.is_numeric_dtype(v):
        n = pd.to_numeric(v, errors='coerce')
        med = n.dropna().abs().median()
        unit = 'ns' if med >= 1e17 else ('us' if med >= 1e14 else ('ms' if med >= 1e11 else 's'))
        return pd.to_datetime(n, unit=unit, utc=True, errors='coerce')
    return pd.to_datetime(v, utc=True, errors='coerce')


def load_market_ratios(path: Path):
    d = pd.read_parquet(path)
    required = {'timestamp_hour_utc','steth_eth_market_ratio','source'}
    if not required.issubset(d):
        raise ValueError(f'Market ratio schema incorrect: missing {sorted(required-set(d))}')
    d = d.copy()
    d['timestamp_utc'] = utc_series(d['timestamp_hour_utc'])
    d['market_ratio'] = pd.to_numeric(d['steth_eth_market_ratio'], errors='coerce')
    if d['timestamp_utc'].isna().any():
        raise ValueError('Invalid market ratio observation timestamp')
    if d['source'].isna().any() or d['source'].astype(str).str.strip().eq('').any():
        raise ValueError('Source missing: stETH/ETH cannot be certified as MARKET')
    d['provenance_check'] = d['source'].astype(str).str.contains('lido|oracle|contract', case=False, regex=True).map({True:'REVIEW_NON_MARKET_SOURCE',False:'MARKET_SOURCE_CANDIDATE'})
    invalid = d['market_ratio'].notna() & (~d['market_ratio'].map(lambda n: math.isfinite(float(n)) and n>0 if pd.notna(n) else False))
    if invalid.any():
        raise ValueError('Non-positive or nonfinite ratio')
    if d['timestamp_utc'].duplicated().any():
        raise ValueError('Duplicate market ratio observation timestamps: do not resolve arbitrarily')
    d['depeg_pct'] = (d['market_ratio'] - 1)*100
    return d.sort_values('timestamp_utc').reset_index(drop=True)


def audit(ratios, metadata, max_lag_min=90, window_hours=24):
    result=[]; samples=[]
    for sid in ('B1','B2'):
        snap=metadata['snapshots'][sid]
        start=pd.Timestamp(snap['snapshot_timestamp_utc']).tz_convert('UTC')
        end=start+pd.Timedelta(hours=window_hours)
        window=ratios[(ratios.timestamp_utc>=start-pd.Timedelta(minutes=max_lag_min)) & (ratios.timestamp_utc<end)].copy()
        grid=pd.DataFrame({'hour_start_utc':pd.date_range(start.floor('h'), end.ceil('h'), freq='h', tz='UTC', inclusive='left')})
        # evaluate just-before the END of each hour, no future borrowing
        grid['available_by_utc']=grid.hour_start_utc+pd.Timedelta(hours=1)-pd.Timedelta(nanoseconds=1)
        grid['snapshot_id']=sid
        prior=window[window.provenance_check.eq('MARKET_SOURCE_CANDIDATE') & window.market_ratio.notna()].copy()
        if len(prior):
            rhs=prior[['timestamp_utc','market_ratio','depeg_pct','source']].sort_values('timestamp_utc')
            grid=pd.merge_asof(grid.sort_values('available_by_utc'),rhs,left_on='available_by_utc',right_on='timestamp_utc',direction='backward',tolerance=pd.Timedelta(minutes=max_lag_min))
        else:
            for c in ['timestamp_utc','market_ratio','depeg_pct','source']:
                grid[c] = pd.Series(pd.NaT, index=grid.index, dtype='datetime64[ns, UTC]') if c=='timestamp_utc' else None
        grid['age_min']=(grid.available_by_utc-grid.timestamp_utc).dt.total_seconds()/60
        grid['market_quote_valid']=grid.market_ratio.notna() & grid.age_min.ge(0) & grid.age_min.le(max_lag_min)
        grid['status']=grid.market_quote_valid.map({True:'MARKET_QUOTE_VALID',False:'MISSING_OR_STALE_MARKET_RATIO'})
        samples.append(grid)
        asof=ratios[(ratios.timestamp_utc<=start) & ratios.provenance_check.eq('MARKET_SOURCE_CANDIDATE') & ratios.market_ratio.notna()].tail(1)
        has_snap=False
        if len(asof):
            has_snap=bool((start-asof.iloc[-1].timestamp_utc)<=pd.Timedelta(minutes=max_lag_min))
        result.append({'snapshot_id':sid,'snapshot_block':int(snap['snapshot_block']),
                       'snapshot_utc':start.isoformat(),'window_hours':window_hours,
                       'market_obs_near_window':int(len(window)),
                       'suspected_non_market_obs':int(window.provenance_check.ne('MARKET_SOURCE_CANDIDATE').sum()),
                       'expected_hour_bins':int(len(grid)), 'covered_hour_bins':int(grid.market_quote_valid.sum()),
                       'coverage_pct':round(100*grid.market_quote_valid.mean(),3) if len(grid) else None,
                       'market_ratio_asof_snapshot_available':has_snap,
                       'status':'PASS_OBSERVATION_QA' if bool(grid.market_quote_valid.all()) and has_snap else 'INCOMPLETE_MARKET_SERIES'})
    return pd.concat(samples,ignore_index=True),result


def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('--ratios',type=Path,default=Path('data/staging/n3_market_ratios.parquet'))
    ap.add_argument('--metadata',type=Path,default=Path('data/processed/aave/snapshot_metadata.json'))
    ap.add_argument('--lido',type=Path,default=Path('data/processed/aave/lido_ratios.parquet'))
    ap.add_argument('--out',type=Path,default=Path('outputs/n3_depeg_B1_B2_QA_20261025'))
    ap.add_argument('--max-lag-min',type=int,default=90)
    ap.add_argument('--window-hours',type=int,default=24)
    a=ap.parse_args()
    if a.max_lag_min<=0 or a.window_hours<=0: raise SystemExit('Invalid time parameters')
    for path in [a.ratios,a.metadata]:
        if not path.is_file(): raise SystemExit(f'Missing: {path}')
    metadata=json.loads(a.metadata.read_text())
    if int(metadata.get('chain_id',0))!=1: raise SystemExit('STOP wrong chain')
    for sid,b in [('B1',20458637),('B2',23543615)]:
        if int(metadata['snapshots'][sid]['snapshot_block']) != b: raise SystemExit('STOP bad snapshot block')
    ratios=load_market_ratios(a.ratios)
    grid, rows=audit(ratios,metadata,a.max_lag_min,a.window_hours)
    qa={'status':'QA_ONLY', 'source_path':str(a.ratios),'rows_input':len(ratios),
        'source_types':ratios[['source','provenance_check']].value_counts(dropna=False).rename('count').reset_index().to_dict('records'),
        'snapshots':rows,'lido_contract_ratios_are_not_market_depeg':True,
        'limitations':'Aave oracle or stEthPerToken do NOT substitute market stETH/ETH. Hourly observed depeg is NOT Chainlink AnswerUpdated or causal liquidation evidence.'}
    if a.lido.is_file():
        lido=pd.read_parquet(a.lido)
        qa['lido_input_rows_for_reference_only']=len(lido)
    a.out.mkdir(parents=True,exist_ok=True)
    dest=a.out/'n3_depeg_steth_eth_hourly_QA.csv'
    report=a.out/'n3_depeg_B1_B2_quality.json'
    if dest.exists() or report.exists(): raise SystemExit('STOP output exists, choose new --out')
    grid.to_csv(dest,index=False)
    report.write_text(json.dumps(qa,indent=2,ensure_ascii=False,default=str))
    for row in rows: print('[DEPEG]',row)
    print('[SAVED]',dest,report)

if __name__=='__main__': main()
