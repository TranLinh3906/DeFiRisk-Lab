"""Collect wallet, feed-dependency and snapshot-configuration proofs, read-only."""
import json
from pathlib import Path

from eth_abi import decode
from eth_utils import keccak
import pandas as pd

from scripts.n234_collect_history import CachedRPC,calldata,AAVE_POOL


def main():
    out=Path('outputs/n5_completion_20261026/extended_proofs');out.mkdir(exist_ok=True)
    rpc=CachedRPC(out)
    cfg=json.loads(Path('config/snapshots.json').read_text())['snapshots']
    state=json.loads(Path('data/raw/aave/state_check.json').read_text())['snapshots']
    evidence=pd.read_parquet('outputs/n5_preparation/n5_N2_update_20261025_v3/B2_oracle_wallet_evidence.parquet')
    wallets=sorted(evidence.loc[evidence.window.eq('full')&~evidence.in_snapshot,'wallet_address'])
    block=cfg['B2']['block'];end=int(pd.read_parquet('outputs/n5_completion_20261026/history/B2/liquidations_rpc.parquet').event_block.max())
    values=rpc.batch([('eth_call',[{'to':AAVE_POOL,'data':calldata('getUserAccountData(address)',['address'],[w])},hex(block)]) for w in wallets])
    rows=[]
    for w,value in zip(wallets,values):
        c,d,available,lt,ltv,hf=decode(['uint256']*6,bytes.fromhex(value[2:]))
        rows.append(dict(snapshot_id='B2',snapshot_block=block,wallet_address=w,call_ok=True,total_collateral_base_usd=c/1e8,
            total_debt_base_usd=d/1e8,health_factor_reported=hf/1e18 if d else None,classification='no_debt_at_snapshot' if not d else 'initial_debt_needs_position_recovery'))
    borrow_topic='0x'+keccak(text='Borrow(address,address,address,uint256,uint8,uint256,uint16)').hex()
    logs=rpc.one('eth_getLogs',[{'address':AAVE_POOL,'topics':[borrow_topic,None,['0x'+w[2:].rjust(64,'0') for w in wallets]],'fromBlock':hex(block+1),'toBlock':hex(end)}])
    for row in rows:
        matches=[x for x in logs if '0x'+x['topics'][2][-40:]==row['wallet_address']]
        row['borrow_events_after_snapshot']=len(matches)
        row['first_borrow_block_after_snapshot']=min([int(x['blockNumber'],16) for x in matches],default=None)
    pd.DataFrame(rows).to_csv(out/'missing_wallet_state_and_borrows.csv',index=False)
    (out/'borrow_logs.json').write_text(json.dumps(logs))
    reserves=pd.read_parquet('data/processed/aave/reserve_config.parquet')
    configrows=[];implrows=[]
    implslot='0x360894a13ba1a3210667c828492db98dca3e2076cc3735a920a3ca505d382bbc'
    for sid,snap in cfg.items():
        sub=reserves[reserves.snapshot_id.eq(sid)]
        calls=[('eth_call',[{'to':AAVE_POOL,'data':calldata('getConfiguration(address)',['address'],[t])},hex(snap['block'])]) for t in sub.token_address]
        values=rpc.batch(calls)
        for row,value in zip(sub.to_dict('records'),values):
            raw=int(value,16)
            observed={'ltv_bps':raw&65535,'lt_bps':(raw>>16)&65535,'liq_bonus_raw_bps':(raw>>32)&65535,'decimals':(raw>>48)&255}
            configrows.append(dict(snapshot_id=sid,snapshot_block=snap['block'],token_address=row['token_address'],
                symbol=row['symbol'],raw_configuration=str(raw),**observed,
                matches_N2=all(int(row[k])==v for k,v in observed.items()),liquidation_protocol_fee_bps=(raw>>152)&65535))
        value=rpc.one('eth_getStorageAt',[AAVE_POOL,implslot,hex(snap['block'])])
        implrows.append(dict(snapshot_id=sid,snapshot_block=snap['block'],implementation_address='0x'+value[-40:],
            close_factor_status='research baseline policy; not claimed identical to every versioned contract rule'))
    pd.DataFrame(configrows).to_csv(out/'reserve_parameter_replay.csv',index=False)
    pd.DataFrame(implrows).to_csv(out/'pool_implementations.csv',index=False)
    if not all(r['matches_N2'] for r in configrows):raise ValueError('Snapshot reserve config mismatch')
    getters=['aggregator()','BASE_TO_USD_AGGREGATOR()','ASSET_TO_USD_AGGREGATOR()','RATIO_PROVIDER()']
    answer_topic='0x'+keccak(text='AnswerUpdated(int256,uint256,uint256)').hex()
    confirmed_topic='0x'+keccak(text='AggregatorConfirmed(address,address)').hex()
    source_topic='0x'+keccak(text='AssetSourceUpdated(address,address)').hex()
    report={}
    for sid in ['B1','B2']:
        start=cfg[sid]['block'];end=int(pd.read_parquet(f'outputs/n5_completion_20261026/history/{sid}/oracle_all_active_tokens.parquet').block.max())
        roots={r['price_source'].lower() for r in state[sid]['reserves'] if r.get('price_source') and int(r['price_source'],16)}
        source_logs=rpc.one('eth_getLogs',[{'address':state[sid]['aave_oracle'],'topics':[source_topic],'fromBlock':hex(start+1),'toBlock':hex(end)}])
        roots|={'0x'+log['topics'][2][-40:] for log in source_logs}
        visited=set();frontier=roots;edges=[];all_logs=[]
        for depth in range(6):
            addresses=sorted(frontier-visited)
            if not addresses:break
            visited.update(addresses)
            specs=[(address,getter,n) for address in addresses for getter in getters for n in [start,end]]
            values=rpc.batch([('eth_call',[{'to':address,'data':calldata(getter)},hex(n)]) for address,getter,n in specs])
            children=set()
            for (address,getter,n),value in zip(specs,values):
                if isinstance(value,str) and len(value)==66 and 0<int(value,16)<(1<<160):
                    child='0x'+value[-40:]
                    edges.append(dict(snapshot_id=sid,parent=address,getter=getter,block=n,child=child));children.add(child)
            queries=[('eth_getLogs',[{'address':addresses,'topics':[[answer_topic,confirmed_topic]],'fromBlock':hex(lo),'toBlock':hex(min(end,lo+999))}]) for lo in range(start+1,end+1,1000)]
            batches=rpc.batch(queries)
            if any(not isinstance(b,list) for b in batches):raise RuntimeError('Incomplete dependency log query')
            logs=[log for batch in batches for log in batch];all_logs.extend(logs)
            for log in logs:
                if log['topics'][0].lower()==confirmed_topic and len(log['topics'])>=3:
                    children.add('0x'+log['topics'][2][-40:])
            frontier=children-visited
            print(sid,'feed graph depth',depth,'addresses',len(visited),flush=True)
        if frontier:raise RuntimeError('Feed graph exceeded bounded traversal depth; do not claim closure')
        (out/f'{sid}_feed_logs_raw.json').write_text(json.dumps(all_logs))
        pd.DataFrame(edges).to_csv(out/f'{sid}_feed_dependencies.csv',index=False)
        (out/f'{sid}_source_upgrade_logs.json').write_text(json.dumps(source_logs))
        answers=[log for log in all_logs if log['topics'][0].lower()==answer_topic]
        answerrows=[]
        for log in answers:
            value=int(log['topics'][1],16);value=value-(1<<256) if value>=(1<<255) else value
            answerrows.append(dict(feed_address=log['address'],block=int(log['blockNumber'],16),log_index=int(log['logIndex'],16),
                tx_hash=log['transactionHash'],answer_raw=str(value),round_id_raw=str(int(log['topics'][2],16)),updated_at=int(log['data'],16)))
        pd.DataFrame(answerrows).drop_duplicates(['tx_hash','log_index']).to_parquet(out/f'{sid}_answer_updated_dependencies.parquet',index=False)
        report[sid]=dict(feed_addresses=len(visited),dependency_edges=len(edges),answer_updated_events=len(answerrows),
            oracle_source_changes=len(source_logs),graph_closed_for_supported_getters=not frontier,
            limits='Native ratios, time-dependent caps and unrecognized adapter interfaces are not reconstructed from AnswerUpdated alone; direct all-token Aave oracle samples and exact peak-block samples are separate observations')
    (out/'summary.json').write_text(json.dumps(dict(wallets=rows,reserve_configs_replayed=len(configrows),feeds=report),indent=2))
    print(json.dumps(report,indent=2))


if __name__=='__main__':main()
