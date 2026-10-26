"""Confirm snapshot oracle values/sources and descriptive feed metadata."""
import json
from pathlib import Path

from eth_abi import decode
import pandas as pd

from scripts.n234_collect_history import CachedRPC,calldata


def main():
    out=Path('outputs/n5_completion_20261026/feed_metadata');out.mkdir(exist_ok=True)
    rpc=CachedRPC(out)
    cfg=json.loads(Path('config/snapshots.json').read_text())['snapshots']
    state=json.loads(Path('data/raw/aave/state_check.json').read_text())['snapshots']
    old=pd.read_parquet('data/processed/aave/oracle_prices.parquet')
    snapshot_rows=[];metadata=[]
    for sid,snap in cfg.items():
        reserves=state[sid]['reserves'];oracle=state[sid]['aave_oracle']
        items=[]
        for r in reserves:
            items.extend([(oracle,calldata('getAssetPrice(address)',['address'],[r['token_address']])),
                (oracle,calldata('getSourceOfAsset(address)',['address'],[r['token_address']]))])
        raw=rpc.batch([rpc.calls(snap['block'],items)])[0]
        values=decode(['(bool,bytes)[]'],bytes.fromhex(raw[2:]))[0]
        refs=old[old.snapshot_block.eq(snap['block'])].set_index('token_address')
        for i,r in enumerate(reserves):
            (ok1,v1),(ok2,v2)=values[2*i:2*i+2]
            if not ok1 or not ok2:raise ValueError('Missing snapshot oracle source/price')
            price=int.from_bytes(v1,'big');source=decode(['address'],v2)[0]
            matches=abs(price/1e8-float(refs.loc[r['token_address'],'price_usd']))<1e-7
            snapshot_rows.append(dict(snapshot_id=sid,snapshot_block=snap['block'],token_address=r['token_address'],
                source_address=source,price_raw=str(price),price_usd=price/1e8,matches_N2=matches,
                source_matches_N2=source.lower()==str(r['price_source']).lower()))
        if sid not in ['B1','B2']:continue
        edges=pd.read_csv(f'outputs/n5_completion_20261026/extended_proofs/{sid}_feed_dependencies.csv')
        addresses=sorted(set(edges.parent)|set(edges.child)|{r['price_source'].lower() for r in reserves if r.get('price_source') and int(r['price_source'],16)})
        specs=[(address,method) for address in addresses for method in ['decimals()','description()','latestRoundData()']]
        values=rpc.batch([('eth_call',[{'to':address,'data':calldata(method)},hex(snap['block'])]) for address,method in specs])
        grouped={address:dict(snapshot_id=sid,snapshot_block=snap['block'],feed_address=address) for address in addresses}
        for (address,method),value in zip(specs,values):
            row=grouped[address]
            if not isinstance(value,str) or len(value)<=2:
                row[method[:-2]+'_status']='interface_not_supported';continue
            try:
                if method=='decimals()':row['decimals']=int(decode(['uint8'],bytes.fromhex(value[2:]))[0])
                elif method=='description()':row['description']=decode(['string'],bytes.fromhex(value[2:]))[0]
                else:
                    rd,answer,started,updated,answered=decode(['uint80','int256','uint256','uint256','uint80'],bytes.fromhex(value[2:]))
                    row.update(round_id_raw=str(rd),answer_raw=str(answer),updated_at=updated,answered_in_round_raw=str(answered))
                row[method[:-2]+'_status']='observed'
            except Exception:row[method[:-2]+'_status']='different_interface'
        metadata.extend(grouped.values())
        print(sid,'feed metadata',len(addresses),flush=True)
    if not all(r['matches_N2'] and r['source_matches_N2'] for r in snapshot_rows):raise ValueError('Snapshot source/price changed from N2')
    pd.DataFrame(snapshot_rows).to_parquet(out/'snapshot_oracle_replay.parquet',index=False)
    pd.DataFrame(metadata).to_parquet(out/'historical_feed_metadata.parquet',index=False)
    (out/'summary.json').write_text(json.dumps(dict(snapshot_prices_and_sources_verified=len(snapshot_rows),historical_feed_contracts=len(metadata)),indent=2))


if __name__=='__main__':main()
