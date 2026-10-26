"""Collect 366 daily as-of ETH/USD and stETH/USD levels from public historical API."""
import json
from pathlib import Path
import time

import pandas as pd
import requests

from src.models.n5_research_protocol import calibrated_scenarios


def main():
    out=Path('outputs/n5_completion_20261026/calibration_market');out.mkdir(exist_ok=True)
    cfg=json.loads(Path('config/snapshots.json').read_text())
    cutoff=pd.Timestamp(cfg['snapshots']['A']['block_timestamp_utc'])
    days=pd.date_range(cutoff.normalize()-pd.Timedelta(days=365),cutoff.normalize(),freq='D')
    coins={'ETH':'coingecko:ethereum','STETH':'ethereum:0xae7ab96520de3a18e5e111b5eaab095312d7fe84'}
    observations=[];failures=[]
    for offset in range(0,len(days),40):
        chunk=days[offset:offset+40]
        times=[int((d+pd.Timedelta(hours=23,minutes=44,seconds=59)).timestamp()) for d in chunk]
        path=out/f'raw_batch_{offset:03}.json'
        if path.exists():payload=json.loads(path.read_text())
        else:
            payload=None
            for base in ['https://api.llama.fi','https://coins.llama.fi']:
                for retry in range(3):
                    try:
                        response=requests.get(base+'/batchHistorical',params={'coins':json.dumps({v:times for v in coins.values()}),'searchWidth':'30m'},timeout=35)
                        if response.status_code==200 and isinstance(response.json().get('coins'),dict):
                            payload=response.json();break
                        failures.append(dict(batch=offset,base=base,http_status=response.status_code))
                        if response.status_code not in [429,500,502,503,504]:break
                    except requests.RequestException as exc:failures.append(dict(batch=offset,base=base,failure_type=type(exc).__name__))
                    time.sleep(2**retry)
                if payload is not None:break
            if payload is None:
                (out/'collection_failure.json').write_text(json.dumps(failures,indent=2))
                print(json.dumps(dict(status='public_market_history_unavailable',failures=failures),indent=2));return
            path.write_text(json.dumps(payload))
        for asset,coin in coins.items():
            for row in payload['coins'].get(coin,{}).get('prices',[]):
                observations.append(dict(asset=asset,price_usd=row['price'],quote_utc=pd.Timestamp(row['timestamp'],unit='s',tz='UTC'),source='DefiLlama public historical market reference'))
        print('daily market batch',offset,'complete',flush=True);time.sleep(.3)
    q=pd.DataFrame(observations).drop_duplicates(['asset','quote_utc','price_usd'])
    if q.empty:raise ValueError('Empty market history')
    if q.duplicated(['asset','quote_utc']).any():raise ValueError('Conflicting quote timestamp')
    q.to_parquet(out/'observed_market_quotes.parquet',index=False)
    rows=[]
    for day in days:
        deadline=day+pd.Timedelta(days=1)-pd.Timedelta(seconds=1)
        row=dict(date=day)
        for asset in coins:
            eligible=q[q.asset.eq(asset)&q.quote_utc.le(deadline)&q.quote_utc.ge(deadline-pd.Timedelta(hours=1))].sort_values('quote_utc')
            if eligible.empty:raise ValueError(f'Missing fresh backward daily quote: {day} {asset}')
            value=eligible.iloc[-1]
            row[asset.lower()+'_usd']=float(value.price_usd);row[asset.lower()+'_quote_utc']=value.quote_utc
        row['steth_eth']=row['steth_usd']/row['eth_usd'];rows.append(row)
    levels=pd.DataFrame(rows)
    if levels[['eth_usd','steth_eth']].le(0).any().any():raise ValueError('Invalid daily market level')
    returns=levels.assign(eth_return=levels.eth_usd.pct_change(fill_method=None),depeg_return=levels.steth_eth.pct_change(fill_method=None)).iloc[1:].reset_index(drop=True)
    scenarios,estimation=calibrated_scenarios(returns,n=10000,seed=42,df=4)
    estimation['validation_status']='observed_asof_market_inputs_checked; scenario_estimation_not_loss_model_acceptance'
    for name,d in [('calibration_levels',levels),('calibration_returns',returns),('mc_scenario_inputs',scenarios)]:d.to_parquet(out/f'{name}.parquet',index=False)
    (out/'summary.json').write_text(json.dumps(dict(status='observed_asof_daily_calibration',daily_levels=len(levels),paired_returns=len(returns),estimation=estimation,
        scope='Market reference quotes within each daily close, potentially asynchronous; not oracle, not realized Monte Carlo loss results'),indent=2))
    print('collected',len(levels),'daily levels')


if __name__=='__main__':main()
