"""Observe Curve stETH/ETH at every block of the approved peak windows."""
import json
from pathlib import Path

import pandas as pd

from scripts.n234_collect_history import CachedRPC,calldata,CURVE
from src.models.n5_research_protocol import peak_event_window


def main():
    out=Path('outputs/n5_completion_20261026/depeg_peak');out.mkdir(exist_ok=True)
    rpc=CachedRPC(out,batch_size=20);rows=[]
    for sid in ['B1','B2']:
        base=Path('outputs/n5_completion_20261026/history')/sid
        peak=peak_event_window(pd.read_parquet(base/'liquidations_rpc.parquet'),width=300)
        times=pd.read_parquet(base/'oracle_all_active_tokens.parquet',columns=['block','block_timestamp_utc']).drop_duplicates().set_index('block').block_timestamp_utc
        blocks=list(range(peak['start_block'],peak['end_block']+1))
        calls=[]
        for n in blocks:
            calls += [('eth_call',[{'to':CURVE,'data':calldata('get_dy(int128,int128,uint256)',['int128','int128','uint256'],[1,0,10**14])},hex(n)]),
                ('eth_call',[{'to':CURVE,'data':calldata('fee()')},hex(n)])]
        values=rpc.batch(calls)
        for i,n in enumerate(blocks):
            raw=int(values[2*i],16);fee=int(values[2*i+1],16)/1e10;ratio=raw/1e14/(1-fee)
            rows.append(dict(snapshot_id=sid,block=n,block_timestamp_utc=times.loc[n],amount_in_raw=str(10**14),amount_out_raw=str(raw),
                fee_rate=fee,market_ratio=ratio,depeg_pct=(ratio-1)*100,scope='Curve fee-adjusted tiny-trade estimate, not Aave oracle'))
    pd.DataFrame(rows).to_parquet(out/'depeg_every_peak_block.parquet',index=False)
    (out/'summary.json').write_text(json.dumps(dict(rows=len(rows),blocks_per_snapshot=300,scope='Every peak block observed; full windows also have hourly observations'),indent=2))
    print('Peak depeg observations',len(rows))


if __name__=='__main__':main()
