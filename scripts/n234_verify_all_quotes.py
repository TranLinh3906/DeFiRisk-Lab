"""Replay all 111 grid quotes and the 24 depth anchors at immutable blocks."""
import json
from pathlib import Path

from eth_abi import decode
import numpy as np
import pandas as pd

from scripts.n234_collect_history import CachedRPC,calldata
from scripts.n3_priority2_multivolume_quotes import QUOTER_V2


def main():
    out=Path('outputs/n5_completion_20261026/all_quote_replay');out.mkdir(exist_ok=True)
    rpc=CachedRPC(out)
    base=Path('outputs/n3_priority2_quote_grid_v1')
    quotes=pd.concat([pd.read_parquet(base/'n3_multivolume_rpc_verified_only.parquet'),
        pd.read_parquet('outputs/n3_priority2_quote_grid_v2_with_27/n3_targeted_27_rpc_verified_only.parquet')],ignore_index=True)
    anchors=pd.read_parquet(base/'n3_2pct_observed_anchors.parquet')
    keys=['snapshot_id','snapshot_block','pool_address','token_in_address','token_out_address']
    metadata=quotes[keys+['fee_tier','input_decimals','spot_output_raw_per_input_raw','input_token_usd_oracle']].drop_duplicates(keys)
    anchors=anchors.merge(metadata,on=keys,validate='many_to_one')
    anchors['amount_in_raw']=anchors.apply(lambda r:str(int(r.trade_size_usd/r.input_token_usd_oracle*10**int(r.input_decimals))),axis=1)
    quotes['kind']='grid_raw_output_replay';anchors['kind']='depth_anchor_impact_replay'
    combined=pd.concat([quotes,anchors],ignore_index=True)
    calls=[]
    for row in combined.itertuples():
        if str(row.dex).lower().startswith('curve'):
            target=row.pool_address;data=calldata('get_dy(int128,int128,uint256)',['int128','int128','uint256'],[1,0,int(row.amount_in_raw)])
        else:
            target=QUOTER_V2;data=calldata('quoteExactInputSingle((address,address,uint256,uint24,uint160))',
                ['(address,address,uint256,uint24,uint160)'],[(row.token_in_address,row.token_out_address,int(row.amount_in_raw),int(row.fee_tier),0)])
        calls.append(('eth_call',[{'to':target,'data':data},hex(int(row.snapshot_block))]))
    values=rpc.batch(calls);rows=[]
    for row,value in zip(combined.to_dict('records'),values):
        if str(row['dex']).lower().startswith('curve'):amount=decode(['uint256'],bytes.fromhex(value[2:]))[0]
        else:amount=decode(['uint256','uint160','uint32','uint256'],bytes.fromhex(value[2:]))[0]
        impact=100*(1-(amount/int(row['amount_in_raw']))/row['spot_output_raw_per_input_raw'])
        if row['kind']=='grid_raw_output_replay' and amount!=int(row['amount_out_raw']):raise ValueError('Grid raw output mismatch')
        if abs(impact-row['impact_pct'])>1e-6:raise ValueError('Anchor impact replay mismatch')
        rows.append(dict(**{k:row[k] for k in keys},trade_size_usd=row['trade_size_usd'],kind=row['kind'],
            amount_in_raw=row['amount_in_raw'],replayed_amount_out_raw=str(amount),replayed_impact_pct=impact,
            original_impact_pct=row['impact_pct'],impact_difference_pp=impact-row['impact_pct']))
    pd.DataFrame(rows).to_parquet(out/'all_quote_anchor_replay.parquet',index=False)
    report=dict(grid_quotes=111,depth_anchors=24,mismatches=0,max_absolute_impact_difference_pp=float(max(abs(r['impact_difference_pp']) for r in rows)),
        scope='Independent archive eth_call replay; negative tiny-reference impacts retained raw and modeled only under explicit quantization policy')
    (out/'summary.json').write_text(json.dumps(report,indent=2));print(json.dumps(report,indent=2))


if __name__=='__main__':main()
