"""Read-only archive spot observations for all three approved DEX routes."""
import json
from pathlib import Path
import pandas as pd
from eth_abi import decode
from scripts.n234_collect_history import CachedRPC, calldata


def main():
    out=Path('outputs/n5_hypothesis_dex_inputs_20261026')
    out.mkdir(exist_ok=True)
    rpc=CachedRPC(out)
    peak=pd.read_parquet('outputs/n5_completion_20261026/depeg_peak/depeg_every_peak_block.parquet')
    routes=json.loads(Path('config/n5_execution_scope_approved.json').read_text(encoding='utf-8'))['routes']
    cfg=pd.read_parquet('data/processed/aave/reserve_config.parquet')
    rows=[]
    for sid,g in peak.groupby('snapshot_id'):
        decimals=cfg[cfg.snapshot_id.eq(sid)].set_index('token_address').decimals.to_dict()
        first=int(g.block.min())
        for route in routes:
            pool,ct,dt=route['pool_address'],route['token_in_address'],route['token_out_address']
            meta=rpc.batch([('eth_call',[{'to':pool,'data':calldata(sig)},hex(first)]) for sig in ['token0()','token1()']])
            t0,t1=[decode(['address'],bytes.fromhex(value[2:]))[0].lower() for value in meta]
            if {t0,t1}!={ct,dt}:
                raise ValueError('Pool route tokens mismatch')
            for offset in range(0,len(g),40):
                chunk=g.iloc[offset:offset+40]
                calls=[]
                for row in chunk.itertuples():
                    calls.extend([('eth_call',[{'to':pool,'data':calldata('slot0()')},hex(int(row.block))]),
                                  ('eth_getBlockByNumber',[hex(int(row.block)),False])])
                answers=rpc.batch(calls)
                for i,row in enumerate(chunk.itertuples()):
                    slot,header=answers[2*i:2*i+2]
                    if not isinstance(slot,str):raise ValueError('Reverted slot0')
                    sqrt=decode(['uint160','int24','uint16','uint16','uint16','uint8','bool'],bytes.fromhex(slot[2:]))[0]
                    ratio=(sqrt/2**96)**2*10**(int(decimals[t0])-int(decimals[t1]))
                    spot=ratio if ct==t0 else 1/ratio
                    rows.append(dict(snapshot_id=sid,block=int(row.block),block_hash=header['hash'],
                        block_timestamp_utc=row.block_timestamp_utc,pool_address=pool,token_in_address=ct,
                        token_out_address=dt,sqrt_price_x96=str(sqrt),spot_output_per_input=spot,
                        source='archive_slot0_marginal_spot_not_execution_average'))
                print(sid,pool,'blocks',offset+len(chunk),'/',len(g),flush=True)
    pd.DataFrame(rows).to_parquet(out/'dex_three_route_spots.parquet',index=False)


if __name__=='__main__':main()
