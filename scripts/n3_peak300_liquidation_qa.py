#!/usr/bin/env python3
"""Verify local Aave LiquidationCall event records inside N1/N4 peak 300-block windows.
No RPC or edits of canonical files. Collection completeness needs Etherscan QA
covering whole interval; a found row count alone never proves no missing events.
"""
from __future__ import annotations
import argparse,json
from pathlib import Path
import pandas as pd

def main():
    p=argparse.ArgumentParser(description='N3 historical liquidation log peak block QA')
    for sid,folder in [('b1','crash_2024_08'),('b2','crash_2025_10')]:
        base=Path(f'data/n3_history_2024_2026/liquidations/{folder}')
        p.add_argument(f'--events-{sid}',type=Path,default=base/'data/staging/n3_liquidations_etherscan.parquet')
        p.add_argument(f'--qa-{sid}',type=Path,default=base/'outputs/tables/n3_etherscan_quality.json')
        p.add_argument(f'--peak-{sid}',type=int,required=True,help='N1/N4-approved peak block, NOT snapshot block')
    p.add_argument('--window-size-blocks',type=int,default=300,help='TOTAL number of blocks, not +/-300')
    p.add_argument('--out',type=Path,default=Path('outputs/n3_peak300_liquidation_QA_20261025'))
    a=p.parse_args()
    if a.window_size_blocks < 1 or a.window_size_blocks>100000:
        raise SystemExit('Invalid window-size-blocks')
    frames=[]; reports=[]
    for sid in ['B1','B2']:
        key=sid.lower(); src=getattr(a,f'events_{key}');qa_p=getattr(a,f'qa_{key}');peak=getattr(a,f'peak_{key}')
        if peak<=0 or not src.is_file() or not qa_p.is_file(): raise SystemExit(f'{sid}: peak, events and Etherscan QA required')
        df=pd.read_parquet(src)
        mandatory={'tx_hash','log_index','event_block','event_timestamp','debt_token_address','collateral_token_address'}
        if mandatory-set(df.columns): raise SystemExit(f'{sid}: missing columns {sorted(mandatory-set(df.columns))}')
        qa=json.loads(qa_p.read_text())
        if qa.get('chain_id')!=1 or qa.get('protocol_id')!='aave_v3_ethereum':
            raise SystemExit(f'{sid}: wrong Etherscan chain/protocol in source QA')
        if int(qa.get('events_decoded',-1)) != len(df):
            raise SystemExit(f'{sid}: source event count differs QA; reconcile before treating as complete')
        df=df.copy()
        df['tx_hash']=df.tx_hash.astype(str).str.lower().str.strip()
        df['log_index']=pd.to_numeric(df.log_index,errors='raise').astype('int64')
        df['event_block']=pd.to_numeric(df.event_block,errors='raise').astype('int64')
        df['event_timestamp']=pd.to_datetime(df.event_timestamp,utc=True,errors='raise')
        if df.duplicated(['tx_hash','log_index']).any():raise SystemExit(f'{sid}: duplicated transaction/log index')
        # Even N window: includes N blocks, with peak in the earlier center position.
        lo=peak-(a.window_size_blocks-1)//2
        hi=lo+a.window_size_blocks-1
        if lo<0: raise SystemExit(f'{sid}: invalid window')
        first=qa.get('first_block');last=qa.get('last_block')
        declared=first is not None and last is not None
        window_covered=declared and int(first)<=lo and int(last)>=hi
        sub=df[df.event_block.between(lo,hi)].copy()
        sub['snapshot_id']=sid
        sub['peak_block']=peak
        sub['window_first_block']=lo
        sub['window_last_block']=hi
        frames.append(sub)
        reports.append({'snapshot_id':sid,'peak_block':peak,'window_start_block':lo,'window_end_block':hi,
                        'window_inclusive_length':hi-lo+1,'event_rows_in_window':len(sub),
                        'unique_tx_count':int(sub.tx_hash.nunique()), 'duplicate_pk':0,
                        'etherscan_qa_blocks':[first,last],
                        'etherscan_source_covers_entire_peak_window':bool(window_covered),
                        'status':'LOCAL_COLLECTION_WINDOW_COVERED' if window_covered else 'NOT_COVERED_BY_LOCAL_ETHERSCAN_QA',
                        'independent_block_log_completeness':'NOT_INDEPENDENTLY_REQUERIED',
                        'note':'Local coverage only, not number of expected external logs. Need re-query peak window to prove exhaustiveness.'})
    if a.out.exists(): raise SystemExit('STOP output exists; choose new --out')
    a.out.mkdir(parents=True,exist_ok=False)
    pd.concat(frames,ignore_index=True).to_parquet(a.out/'n3_liquidations_peak300_events.parquet',index=False)
    (a.out/'n3_peak300_liquidation_quality.json').write_text(json.dumps({'window_definition':'N blocks inclusive centered on supplied peak, not on snapshot', 'snapshots':reports},ensure_ascii=False,indent=2))
    for r in reports: print('[PEAK300]',r)
    print('[SAVED]',a.out)

if __name__=='__main__': main()
