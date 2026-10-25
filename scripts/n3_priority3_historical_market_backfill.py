#!/usr/bin/env python3
"""N3 P3: inspect and conditionally import real historical market USD quotes.

Does not call network; NEVER converts Aave oracles, Lido ratios, priced event
rows, or unnamed sources into independent market reference quotes.
Hourly close rows use the END of the bar (start + 1h) as effective timestamp.
Outputs only to a new directory; does not edit sources.
"""
from __future__ import annotations
import argparse
import json
import math
from pathlib import Path
from datetime import datetime, timezone

import pandas as pd

DEFAULT_ARCHIVES = (
    'data/n3_history_2024_2026/final/market_crashes_hourly.parquet',
    'data/n3_history_2024_2026/final/market_daily_continuous.parquet',
    'data/processed/market/price_history.parquet',
)
TS = ('effective_timestamp_utc', 'timestamp_utc','observation_timestamp_utc','timestamp',
      'hour_end_utc','hour_utc','hour_start_utc','datetime_utc','datetime',
      'time_utc','time','date_utc','date')
SYMBOL = ('asset','symbol','token_symbol','token','coin','asset_symbol')
VALUE = ('price_value','market_price_usd','price_usd','close_usd','usd_price',
         'close_price_usd','close_price','close','price')
SOURCES = ('source','market_source','provider','data_source','price_source','exchange')
BAD_SOURCE_WORDS = ('aave', 'oracle', 'liquidation_priced', 'liquidations', 'contract_conversion',
                    'lido_ratio','implied', 'estimated')


def first(cols, aliases):
    lookup={str(c).lower():c for c in cols}
    for name in aliases:
        if name in lookup:return lookup[name]
    return None


def select_market_history(src: Path):
    df=pd.read_parquet(src)
    info={'file':str(src),'input_rows':len(df),'columns':list(df.columns),
          'decision':'NOT_SUPPORTED','candidate_rows':0,'source_provenance':'UNKNOWN'}
    if any(x in src.name.lower() for x in ('liquidation','oracle','ratio')):
        info['decision']='REJECTED_NOT_INDEPENDENT_MARKET_FILE'
        return pd.DataFrame(),info
    tc=first(df.columns,TS)
    sc=first(df.columns,SYMBOL)
    pc=first(df.columns,VALUE)
    yc=first(df.columns,SOURCES)
    qc=first(df.columns,('quote_asset','quote_currency','currency'))
    pc_type=first(df.columns,('price_type','type','quote_type'))
    if not all((tc,sc,pc)):
        info['decision']='NO_STANDARD_TIMESTAMP_ASSET_PRICE_COLUMNS'
        return pd.DataFrame(),info
    t=df[tc]
    if pd.api.types.is_integer_dtype(t) or pd.api.types.is_float_dtype(t):
        # Epoch numeric timestamps must be unambiguous seconds or milliseconds.
        nonnull=pd.to_numeric(t,errors='coerce').dropna()
        if nonnull.empty:
            info['decision']='INVALID_TIME';return pd.DataFrame(),info
        median=nonnull.abs().median()
        if 1e9<median<1e11: times=pd.to_datetime(t,utc=True,unit='s',errors='coerce')
        elif 1e12<median<1e14: times=pd.to_datetime(t,utc=True,unit='ms',errors='coerce')
        else: info['decision']='AMBIGUOUS_EPOCH_UNIT';return pd.DataFrame(),info
    else:
        times=pd.to_datetime(t,utc=True,errors='coerce')
    stamp_name=str(tc).lower()
    price_name=str(pc).lower()
    bar_close= ('hour_utc' in stamp_name or 'hour_start_utc' in stamp_name or
                stamp_name in ('date','date_utc')) and ('close' in price_name)
    if bar_close:
        # Hourly/daily open timestamp cannot be used as a close quote until bar ends.
        interval=pd.Timedelta(days=1) if stamp_name in ('date','date_utc') else pd.Timedelta(hours=1)
        times=times+interval
    values=pd.to_numeric(df[pc],errors='coerce')
    symbols=df[sc].astype(str).str.strip().str.upper()
    quote=(df[qc].astype(str).str.strip().str.upper() if qc is not None
           else pd.Series('USD',index=df.index) if ('usd' in price_name)
           else pd.Series('UNKNOWN',index=df.index))
    if pc_type is not None:
        kinds=df[pc_type].astype(str).str.lower().str.strip()
        permitted=kinds.isin(('market_reference','market','market_price','spot','exchange_spot',
                               'historical_market_reference','market_close'))
    else: permitted=pd.Series(True,index=df.index)
    if yc is not None:
        source=df[yc].astype(str).str.strip()
        allowed=source.str.len().gt(0) & ~source.str.lower().apply(
            lambda val:any(word in val for word in BAD_SOURCE_WORDS))
        provenance='SOURCE_FIELD_PRESENT_REVIEW_ORIGIN'
    else:
        source=pd.Series('UNVERIFIED_SOURCE',index=df.index)
        allowed=pd.Series(False,index=df.index)
        provenance='MISSING_SOURCE_FIELD'
    token_col=first(df.columns,('token_address','asset_address'))
    addr=df[token_col].astype(str).str.lower().str.strip() if token_col is not None else pd.Series('',index=df.index)
    keep=(permitted & quote.eq('USD') & times.notna() & values.gt(0) &
          values.apply(lambda v:bool(math.isfinite(v)) if pd.notna(v) else False))
    cand=pd.DataFrame({'timestamp':times[keep], 'reference_timestamp':times[keep],
                       'asset':symbols[keep], 'quote_asset':'USD','price_value':values[keep],
                       'price_type':'market_reference','source':source[keep],
                       'token_address':addr[keep],
                       'approved_source_for_import':allowed[keep].astype(bool),
                       'archive_path':str(src),
                       'bar_time_interpretation':('BAR_END' if bar_close else 'OBSERVATION_TIME')})
    cand=cand.drop_duplicates(['asset','timestamp','source','price_value'])
    info.update(decision=('SOURCE_FIELD_PRESENT' if yc is not None else 'REVIEW_PROVENANCE'),
                candidate_rows=len(cand),approved_candidate_rows=int(cand.approved_source_for_import.sum()),
                source_provenance=provenance, timestamp_column=str(tc),
                price_column=str(pc),quote_column=str(qc),price_type_column=str(pc_type),
                time_semantics=('BAR_END' if bar_close else 'OBSERVATION_TIME'))
    return cand,info


def main():
    p=argparse.ArgumentParser()
    p.add_argument('--prices',type=Path,default=Path('data/staging/n3_price_timeseries_A_B1_B2.parquet'))
    p.add_argument('--snapshot-metadata',type=Path,default=Path('data/processed/aave/snapshot_metadata.json'))
    p.add_argument('--archive',type=Path,action='append',help='Historical market Parquet; can repeat')
    p.add_argument('--out',type=Path,default=Path('outputs/n3_priority3_market_backfill_v2'))
    p.add_argument('--max-lag-min',type=int,default=60)
    args=p.parse_args()
    if args.max_lag_min <=0: p.error('max lag must be positive')
    original=pd.read_parquet(args.prices)
    required={'timestamp','reference_timestamp','asset','quote_asset','price_value','price_type','source'}
    if required-set(original.columns): raise ValueError('Original N3 input lacks '+str(sorted(required-set(original.columns))))
    meta=json.loads(args.snapshot_metadata.read_text(encoding='utf-8'))
    if int(meta.get('chain_id',0)) !=1: raise ValueError('Wrong chain_id')
    sources=args.archive or [Path(x) for x in DEFAULT_ARCHIVES]
    summaries=[]; candidates=[]
    for path in sources:
        if not path.exists():
            summaries.append({'file':str(path),'decision':'MISSING_FILE'});continue
        cand,qa=select_market_history(path);summaries.append(qa)
        if len(cand):candidates.append(cand)
    args.out.mkdir(parents=True,exist_ok=True)
    report={'input_prices':str(args.prices),'archives':summaries,'snapshots':{},
            'generated_utc':datetime.now(timezone.utc).isoformat()}
    merged=pd.concat(candidates,ignore_index=True) if candidates else pd.DataFrame()
    if len(merged):
        merged.to_csv(args.out/'n3_historical_market_candidates_REVIEW.csv',index=False)
        accepted=merged.loc[merged.approved_source_for_import].copy()
    else:accepted=pd.DataFrame()
    if len(accepted):
        accepted=accepted.drop_duplicates(['asset','timestamp','price_value','source'])
        # Check conflicting independent USD prices for same asset/time, fail closed.
        grouped=accepted.groupby(['asset','timestamp'])['price_value']
        conflicts=grouped.agg(['min','max'])
        conflicting=conflicts[(conflicts['max']-conflicts['min'])>1e-6*conflicts['min'].clip(lower=1)].index
        if len(conflicting):
            badidx=pd.MultiIndex.from_frame(accepted[['asset','timestamp']]).isin(conflicting)
            accepted=accepted.loc[~badidx].copy()
            report['conflicting_asset_time_groups_excluded']=len(conflicting)
    # Do not claim a historical market reference if no sourced input.
    if len(accepted):
        need=list(original.columns)
        to_add=accepted.reindex(columns=need)
        supplemented=pd.concat([original,to_add],ignore_index=True)
    else: supplemented=original.copy()
    for sid,block in [('A',26179841),('B1',20458637),('B2',23543615)]:
        snap=meta['snapshots'].get(sid)
        if not snap or int(snap['snapshot_block']) !=block:raise ValueError('Snapshot block mismatch '+sid)
        t=pd.Timestamp(snap['snapshot_timestamp_utc'])
        if t.tzinfo is None:raise ValueError('Snapshot timestamp missing tz '+sid)
        t=t.tz_convert('UTC')
        if len(accepted):
            at=accepted.loc[(accepted.timestamp<=t) &
                (accepted.timestamp>=t-pd.Timedelta(minutes=args.max_lag_min))]
            assetlist=sorted(at.asset.unique().tolist())
        else:assetlist=[]
        report['snapshots'][sid]={'block':block,'timestamp_utc':t.isoformat(),
            'new_source_labeled_market_assets_near_snapshot':assetlist,
            'count':len(assetlist)}
    outpath=args.out/'n3_price_timeseries_WITH_INDEPENDENT_HISTORY_CANDIDATES.parquet'
    if outpath.exists(): raise SystemExit('STOP: output exists; use new output directory; no overwrite')
    supplemented.to_parquet(outpath,index=False)
    (args.out/'n3_p3_archive_inspection_quality.json').write_text(json.dumps(report,indent=2,ensure_ascii=False))
    print('[ARCHIVE INSPECT] '+json.dumps([{'file':x['file'],'decision':x['decision'],'source_ok':x.get('approved_candidate_rows',0)} for x in summaries],ensure_ascii=False))
    for sid,rec in report['snapshots'].items(): print(f'[MARKET {sid}] historical sourced assets within {args.max_lag_min}m = {rec["count"]} : {rec["new_source_labeled_market_assets_near_snapshot"]}')
    print('[OUTPUT]',outpath)
    if not len(accepted):print('[WARNING] NO independently source-labeled market observations imported. Inspect CSV and document sources; DO NOT relabel oracle.')

if __name__=='__main__': main()
