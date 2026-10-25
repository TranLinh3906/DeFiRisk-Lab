#!/usr/bin/env python3
"""N3 supporting data for N4/N5: executed liquidation gas; never claims freeze.

With --execute-rpc, fetch historical Ethereum receipts for observed SUCCESSFUL
liquidation transaction hashes; checkpoint append-only. This CANNOT measure
unexecuted/unprofitable liquidations or causal 'freeze'. ETH->USD only via
independently observed backward as-of market ETH price <=60 minutes old.
"""
from __future__ import annotations
import argparse, json, os, time, shutil, tempfile
from datetime import datetime, timezone
from pathlib import Path
import pandas as pd

def main():
    p=argparse.ArgumentParser(description='N3 observed gas expenditure for N4/N5')
    p.add_argument('--events',type=Path,default=Path('outputs/n3_peak300_liquidation_QA_20261025/n3_liquidations_peak300_events.parquet'))
    p.add_argument('--prices',type=Path,default=Path('data/staging/n3_price_timeseries_A_B1_B2.parquet'))
    p.add_argument('--out',type=Path,default=Path('outputs/n3_gas_inputs_20261025'))
    p.add_argument('--execute-rpc',action='store_true',help='Fetch transaction receipts. Otherwise dry-run checkpoint only')
    p.add_argument('--max-new-receipts',type=int,default=50)
    p.add_argument('--rpc-interval',type=float,default=1.0)
    a=p.parse_args()
    if a.max_new_receipts<0 or a.rpc_interval<0:raise SystemExit('Invalid limits')
    if not a.events.is_file():raise SystemExit('Missing peak300 events. Run n3_peak300_liquidation_qa.py first')
    events=pd.read_parquet(a.events)
    required={'tx_hash','event_timestamp','event_block'}
    if required-set(events.columns):raise SystemExit(f'Missing event fields {required-set(events.columns)}')
    e=events.copy()
    e['tx_hash']=e.tx_hash.astype(str).str.lower()
    e['event_timestamp']=pd.to_datetime(e.event_timestamp,utc=True,errors='coerce')
    if e.event_timestamp.isna().any() or (~e.tx_hash.str.fullmatch(r'0x[0-9a-f]{64}')).any():
        raise SystemExit('Invalid hash or UTC time')
    tx=e.sort_values('event_timestamp').drop_duplicates('tx_hash').copy()
    a.out.mkdir(parents=True,exist_ok=True)
    checkpoint=a.out/'n3_gas_receipts_checkpoint.jsonl'
    saved={}
    if checkpoint.exists():
        for line in checkpoint.read_text().splitlines():
            if not line.strip():continue
            o=json.loads(line)
            if o['tx_hash'] in saved and saved[o['tx_hash']]!=o:
                raise SystemExit('Conflicting checkpoint transaction')
            saved[o['tx_hash']]=o
    new_count=0
    if a.execute_rpc:
        from dotenv import load_dotenv
        from web3 import Web3
        load_dotenv('.env')
        url=os.environ.get('ETHEREUM_ARCHIVE_RPC_URL') or os.environ.get('ETH_RPC_URL')
        if not url:raise SystemExit('Missing RPC env; keys never printed')
        w3=Web3(Web3.HTTPProvider(url,request_kwargs={'timeout':45}))
        if int(w3.eth.chain_id)!=1:raise SystemExit('STOP non-mainnet RPC')
        with checkpoint.open('a',encoding='utf-8') as f:
            for h,b in zip(tx.tx_hash,tx.event_block):
                if h in saved:continue
                if new_count>=a.max_new_receipts:break
                try:
                    receipt=w3.eth.get_transaction_receipt(h)
                    if int(receipt['blockNumber'])!=int(b):
                        raise ValueError('Receipt block mismatch')
                    effective=receipt.get('effectiveGasPrice')
                    if effective is None:raise ValueError('Receipt has no effectiveGasPrice')
                    row={'tx_hash':h,'block_number':int(receipt['blockNumber']),
                         'gas_used':int(receipt['gasUsed']),
                         'effective_gas_price_wei':int(effective),
                         'tx_receipt_status':int(receipt['status'])}
                except Exception as exc:
                    print(f'[STOP] RPC receipt failure: {type(exc).__name__}; checkpoint preserved; retry same command')
                    break
                f.write(json.dumps(row,ensure_ascii=False)+'\n');f.flush()
                saved[h]=row;new_count+=1
                if new_count%10==0:print(f'[CHECKPOINT] new={new_count} total={len(saved)}/{len(tx)}')
                time.sleep(a.rpc_interval)
    receipts=pd.DataFrame(saved.values())
    if receipts.empty:
        for c in ['tx_hash','block_number','gas_used','effective_gas_price_wei','tx_receipt_status']:
            receipts[c]=pd.Series(dtype=object)
    result=tx.merge(receipts,on='tx_hash',how='left',validate='one_to_one')
    result['gas_cost_eth']=(pd.to_numeric(result['gas_used'],errors='coerce')*pd.to_numeric(result['effective_gas_price_wei'],errors='coerce')/1e18)
    result['eth_usd_quote_time']=pd.NaT
    result['eth_usd_price']=float('nan')
    if a.prices.is_file() and len(result):
        prices=pd.read_parquet(a.prices)
        if {'timestamp','asset','quote_asset','price_type','price_value'}.issubset(prices.columns):
            m=prices[(prices.asset.astype(str).str.upper()=='ETH') &
                     (prices.quote_asset.astype(str).str.upper()=='USD') &
                     (prices.price_type=='market_reference')].copy()
            if not m.empty:
                obs=(pd.to_datetime(m['reference_timestamp'],utc=True,errors='coerce').fillna(pd.to_datetime(m.timestamp,utc=True,errors='coerce')) if 'reference_timestamp' in m else pd.to_datetime(m.timestamp,utc=True,errors='coerce'))
                m=m.assign(observed_utc=obs).dropna(subset=['observed_utc']).sort_values('observed_utc')
                m=m.drop_duplicates('observed_utc',keep='last')
                aleft=result.reset_index().rename(columns={'index':'_row_id'}).sort_values('event_timestamp')
                matched=pd.merge_asof(aleft,m[['observed_utc','price_value']],left_on='event_timestamp',right_on='observed_utc',direction='backward',tolerance=pd.Timedelta(minutes=60))
                matched=matched.sort_values('_row_id')
                result['eth_usd_quote_time']=matched.observed_utc.to_numpy()
                result['eth_usd_price']=pd.to_numeric(matched.price_value,errors='coerce').to_numpy()
    result['gas_cost_usd_market']=result.gas_cost_eth*result.eth_usd_price
    result['gas_evidence_status']=result.gas_cost_eth.notna().map({True:'EXECUTED_RECEIPT_VERIFIED',False:'RECEIPT_NOT_COLLECTED'})
    result['gas_usd_status']=result.gas_cost_usd_market.notna().map({True:'MARKET_PRICE_MATCHED',False:'MISSING_FRESH_INDEPENDENT_ETH_USD'})
    qa={'executed_liquidation_txs':len(tx),'verified_receipts':len(saved), 'new_receipts':new_count,
        'gas_usd_quote_matched':int(result.gas_cost_usd_market.notna().sum()),
        'gas_proof_scope':'Observed successful liquidation executions ONLY',
        'freeze_due_to_gas_or_profit_confirmed':False,
        'limitations':'Cannot infer missed liquidation opportunities, gas-profit freeze, liquidator PnL, MEV or incentives from successful receipt costs alone. N4/N5 must model counterfactual unexecuted opportunities.',
        'source_events':str(a.events)}
    target=a.out/'n3_gas_executed_liquidations.csv';report=a.out/'n3_gas_evidence_quality.json'
    # Reports are regenerated from the append-only checkpoint each time. Back up
    # previous reports to avoid losing audit history when resuming RPC collection.
    for dest in (target, report):
        if dest.exists():
            stamp=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
            shutil.copy2(dest,dest.with_name(dest.name+'.bak_'+stamp))
    result.to_csv(target,index=False)
    report.write_text(json.dumps(qa,ensure_ascii=False,indent=2))
    print('[GAS]',qa);print('[SAVED]',target,report)

if __name__=='__main__': main()
