#!/usr/bin/env python3
"""N3 priority 3: clean source-specific price history for N5 (no lookahead).

Separates real market quotes from snapshot Aave oracle, contract conversion, and
market ratios. Computes market returns only from adjacent, fresh historical quotes.
Does NOT invent missing prices or assert B1 85% event-time coverage.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import math
from datetime import datetime, timezone
from pathlib import Path
import numpy as np
import pandas as pd

BLOCKS={'A':26179841,'B1':20458637,'B2':23543615}
NEEDED={'timestamp','asset','quote_asset','price_value','price_type','source','reference_timestamp'}
STABLECOINS={'USDC','USDT','DAI','GHO','LUSD','PYUSD','USDE','FRAX','USDS'}


def normalize(df):
    absent=NEEDED-set(df.columns)
    if absent: raise ValueError('Price input missing columns: '+str(sorted(absent)))
    p=df.copy()
    p['timestamp']=pd.to_datetime(p.timestamp,utc=True,errors='coerce')
    p['reference_timestamp']=pd.to_datetime(p.reference_timestamp,utc=True,errors='coerce')
    p['asset']=p.asset.astype(str).str.strip().str.upper()
    p['quote_asset']=p.quote_asset.astype(str).str.strip().str.upper()
    p['price_type']=p.price_type.astype(str).str.strip().str.lower()
    p['source']=p.source.astype(str).str.strip()
    p['price_value']=pd.to_numeric(p.price_value,errors='coerce')
    p['effective_timestamp_utc']=p.reference_timestamp.fillna(p.timestamp)
    p['quote_age_sec_at_observation']=(p.timestamp-p.effective_timestamp_utc).dt.total_seconds()
    bad=pd.Series(False,index=p.index)
    for term in (p.timestamp.isna(),p.effective_timestamp_utc.isna(),p.price_value.isna(),
                 ~np.isfinite(p.price_value),p.price_value<=0,
                 p.quote_age_sec_at_observation.lt(0),p.asset.eq(''),p.quote_asset.eq('')):
        bad|=term
    p['qa_valid']=~bad
    p['qa_invalid_reason']='OK'
    conditions=[(p.timestamp.isna(),'invalid_timestamp'),
                (p.effective_timestamp_utc.isna(),'invalid_reference_timestamp'),
                (p.price_value.isna() | ~np.isfinite(p.price_value),'invalid_numeric_price'),
                (p.price_value<=0,'nonpositive_price'),
                (p.quote_age_sec_at_observation<0,'future_reference_timestamp')]
    for cond,label in conditions:
        p.loc[cond,'qa_invalid_reason']=label
    return p


def verified_market(p):
    # Don't alias wrapped token prices silently; don't use oracle snapshot as market quote.
    market=p.loc[p.qa_valid & p.price_type.eq('market_reference')].copy()
    market=market.loc[market.quote_asset.eq('USD')].copy()
    market['token_address']=market['token_address'].astype('string').str.lower().str.strip() if 'token_address' in market.columns else pd.Series('',index=market.index)
    keys=['asset','quote_asset','token_address','effective_timestamp_utc']
    # When exact duplicate sources conflict, retain NONE of the conflicting keys.
    duplicate_conflicts=[]
    for key, group in market.groupby(keys,dropna=False):
        if len(group)>1 and (group.price_value.max()-group.price_value.min())>max(1e-6, 1e-6*float(group.price_value.median())):
            duplicate_conflicts.extend(group.index.tolist())
    conflicts=market.loc[duplicate_conflicts].copy()
    market=market.drop(index=duplicate_conflicts).sort_values(['asset','token_address','effective_timestamp_utc','source'])
    market=market.drop_duplicates(keys,keep='first').reset_index(drop=True)
    market['stablecoin_market_depeg_pct']=np.where(
        market.asset.isin(STABLECOINS),(market.price_value-1.0)*100,np.nan)
    return market,conflicts


def returns_market(market,max_gap_min):
    market=market.copy().sort_values(['asset','token_address','effective_timestamp_utc'])
    groups=market.groupby(['asset','token_address'],dropna=False,sort=False)
    prevprice=groups['price_value'].shift(1)
    prevts=groups['effective_timestamp_utc'].shift(1)
    market['previous_quote_utc']=prevts
    market['return_gap_minutes']=(market.effective_timestamp_utc-prevts).dt.total_seconds()/60
    in_window=market.return_gap_minutes.between(0,max_gap_min,inclusive='both') & market.return_gap_minutes.gt(0)
    market['log_return']=np.where(in_window,np.log(market.price_value/prevprice),np.nan)
    market['return_valid']=market['log_return'].notna()
    market['return_window_end_utc']=market.effective_timestamp_utc
    # NB: N5 should only use returns with return_window_end_utc <= its prediction timestamp.
    return market


def snapshot_qa(p,market,meta,max_quote_lag_min):
    if not meta or 'snapshots' not in meta:
        return [],'NO_SNAPSHOT_METADATA; provide --snapshot-metadata for A/B1/B2 audit'
    if int(meta.get('chain_id',0))!=1:
        raise ValueError('Snapshot metadata must be Ethereum mainnet')
    report=[]
    for sid,block in BLOCKS.items():
        rec=meta['snapshots'].get(sid)
        if not rec or int(rec.get('snapshot_block',-1))!=block:
            raise ValueError(f'Metadata mismatch for {sid} block={block}')
        t=pd.Timestamp(rec['snapshot_timestamp_utc'])
        if t.tzinfo is None: raise ValueError('snapshot_timestamp_utc requires timezone')
        t=t.tz_convert('UTC')
        # Snapshot oracle is an exact-block state, NOT a market quote.
        b=pd.to_numeric(p.get('snapshot_block',pd.Series(index=p.index,dtype='float64')),errors='coerce')
        oracle=p.loc[p.qa_valid & p.price_type.eq('aave_oracle') & b.eq(block)]
        # market as-of <= snapshot, age <= configured threshold
        m=market[(market.effective_timestamp_utc<=t)&
                 (market.effective_timestamp_utc>=t-pd.Timedelta(minutes=max_quote_lag_min))]
        n_prices=m.groupby('asset')['price_value'].count().to_dict()
        report.append({'snapshot_id':sid,'snapshot_block':block,'snapshot_utc':t.isoformat(),
                       'market_quote_rows_within_lag':len(m),
                       'market_assets_with_valid_quote':sorted(n_prices),
                       'n_distinct_market_assets':len(n_prices),
                       'aave_oracle_rows_at_exact_block':len(oracle),
                       'warning':'Oracle and contract ratios are NOT substituted for missing market quotes'})
    return report,'SNAPSHOT_QA_COMPUTED'


def main():
    pa=argparse.ArgumentParser(description='N3 P3: nonleaking, source-separated prices for N5')
    pa.add_argument('--prices',type=Path,required=True)
    pa.add_argument('--output-dir',type=Path,default=Path('outputs/n3_priority3_price_qc_v1'))
    pa.add_argument('--snapshot-metadata',type=Path,default=Path('data/processed/aave/snapshot_metadata.json'))
    pa.add_argument('--max-market-lag-min',type=int,default=60)
    pa.add_argument('--max-return-gap-min',type=int,default=90)
    a=pa.parse_args()
    if a.max_market_lag_min<=0 or a.max_return_gap_min<=0: pa.error('lag limits must be positive')
    src=pd.read_parquet(a.prices)
    p=normalize(src)
    market,conflicts=verified_market(p)
    returns=returns_market(market,a.max_return_gap_min)
    meta=json.loads(a.snapshot_metadata.read_text(encoding='utf-8')) if a.snapshot_metadata.is_file() else None
    snap_status,snap_message=snapshot_qa(p,market,meta,a.max_market_lag_min)
    qa={
        'source_rows':int(len(p)),
        'qa_valid_all_types':int(p.qa_valid.sum()),
        'invalid_rows':int((~p.qa_valid).sum()),
        'invalid_reasons':{str(k):int(v) for k,v in p.loc[~p.qa_valid,'qa_invalid_reason'].value_counts().items()},
        'price_type_counts':{str(k):int(v) for k,v in p.price_type.value_counts().items()},
        'market_usd_quotes_clean':int(len(market)),
        'removed_market_price_conflicts':int(len(conflicts)),
        'market_log_returns_with_fresh_previous_quote':int(returns.return_valid.sum()),
        'snapshot_coverage':snap_status,
        'snapshot_qa_status':snap_message,
        'event_time_B1_85pct_coverage_VERIFIED':False,
        'warning':'Data readiness does not establish event-time two-sided B1 coverage; keep the independent event/quote test.',
        'source_sha256':hashlib.sha256(a.prices.read_bytes()).hexdigest(),
        'generated_utc':datetime.now(timezone.utc).isoformat()
    }
    a.output_dir.mkdir(parents=True,exist_ok=True)
    market.to_parquet(a.output_dir/'n3_market_usd_prices_N5_clean.parquet',index=False)
    returns.to_parquet(a.output_dir/'n3_market_returns_N5_no_lookahead.parquet',index=False)
    p.loc[p.qa_valid & p.price_type.eq('aave_oracle')].to_parquet(a.output_dir/'n3_aave_oracle_snapshot_only.parquet',index=False)
    p.loc[p.qa_valid & p.price_type.isin(['market_ratio','lido_contract_conversion'])].to_parquet(a.output_dir/'n3_ratios_conversions_separate.parquet',index=False)
    p.loc[~p.qa_valid].to_csv(a.output_dir/'n3_prices_invalid_rows.csv',index=False)
    conflicts.to_csv(a.output_dir/'n3_market_conflicting_quotes.csv',index=False)
    pd.DataFrame(snap_status).to_csv(a.output_dir/'n3_snapshot_price_readiness_A_B1_B2.csv',index=False)
    (a.output_dir/'n3_priority3_quality.json').write_text(json.dumps(qa,indent=2,ensure_ascii=False,allow_nan=False),encoding='utf-8')
    print('[P3]',json.dumps({k:qa[k] for k in ('source_rows','qa_valid_all_types','market_usd_quotes_clean','market_log_returns_with_fresh_previous_quote','snapshot_qa_status')}))
    for r in snap_status:
        print(f'[SNAPSHOT {r["snapshot_id"]}] market USD assets={r["n_distinct_market_assets"]} oracle exact block rows={r["aave_oracle_rows_at_exact_block"]} (not interchangeable)')
    print('[SAVED]',a.output_dir)

if __name__=='__main__':
    main()
