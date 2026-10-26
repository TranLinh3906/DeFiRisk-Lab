"""Read-only archive collection for N2/N3. Resumable raw RPC checkpoint; no secrets printed."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import time

from dotenv import dotenv_values
from eth_abi import encode, decode
from eth_utils import keccak
import pandas as pd
import requests

from src.data.fetch_liquidations import decode_event, EVENT_TOPIC, AAVE_POOL
from src.models.n5_research_protocol import peak_event_window
from src.models.n5_snapshots import select_event_windows

MC = '0xca11bde05977b3631167028862be2a173976ca11'
CURVE = '0xdc24316b9ae028f1497c275eb9192a3ea0f67022'


def calldata(signature, types=(), values=()):
    return '0x'+(keccak(text=signature)[:4]+encode(list(types), list(values))).hex()


class CachedRPC:
    def __init__(self, directory, batch_size=40):
        self.batch_size=batch_size
        values = {}
        for path in [Path('N3_Compound_Q3_Overlap_VSCode/.env'), Path('.env')]:
            if path.exists():
                values.update({k: v for k, v in dotenv_values(path).items() if v})
        self.url = next((os.environ.get(k) or values.get(k) for k in ['ETHEREUM_ARCHIVE_RPC_URL', 'ALCHEMY_RPC_URL', 'ETH_RPC_URL'] if os.environ.get(k) or values.get(k)), None)
        if not self.url:
            raise RuntimeError('No configured archive RPC')
        self.session = requests.Session()
        self.path = directory/'rpc_checkpoint.jsonl'
        self.cache = {}
        if self.path.exists():
            with self.path.open() as stream:
                for line in stream:
                    row = json.loads(line)
                    if row['key'] in self.cache and self.cache[row['key']] != row['result']:
                        raise ValueError('Conflicting immutable RPC checkpoint')
                    self.cache[row['key']] = row['result']
        # Always verify actual connection chain, even on a resumed collection.
        try:
            res = self.session.post(self.url, json={'jsonrpc':'2.0','id':1,'method':'eth_chainId','params':[]}, timeout=30)
            if res.status_code != 200 or int(res.json().get('result','0x0'),16) != 1:
                raise RuntimeError('RPC chain/HTTP verification failed')
        except requests.RequestException as exc:
            raise RuntimeError('RPC_CONNECTION_'+type(exc).__name__) from None

    def batch(self, calls):
        keys = [hashlib.sha256(json.dumps([m,p],sort_keys=True).encode()).hexdigest() for m,p in calls]
        missing = [(k,c) for k,c in dict(zip(keys,calls)).items() if k not in self.cache]
        for offset in range(0,len(missing),self.batch_size):
            group = missing[offset:offset+self.batch_size]
            payload = [dict(jsonrpc='2.0',id=i+1,method=c[0],params=c[1]) for i,(_,c) in enumerate(group)]
            for attempt in range(4):
                try:
                    response = self.session.post(self.url,json=payload,timeout=60)
                    if response.status_code != 200:
                        raise RuntimeError(f'RPC_HTTP_{response.status_code}')
                    body = response.json()
                    if not isinstance(body,list) or len(body)!=len(group):
                        raise RuntimeError('Invalid RPC batch response')
                    results = {r['id']:r for r in body}
                    if any(r.get('error',{}).get('code') in [-32005,429] for r in body):
                        raise RuntimeError('RPC_RATE_LIMIT')
                    break
                except (requests.RequestException, ValueError, RuntimeError) as exc:
                    if attempt==3:
                        reason=str(exc) if isinstance(exc,RuntimeError) and str(exc).startswith(('RPC_HTTP_','RPC_RATE_LIMIT','Invalid RPC batch')) else type(exc).__name__
                        raise RuntimeError('RPC_BATCH_FAILED_'+reason) from None
                    time.sleep(2**attempt)
            with self.path.open('a',encoding='utf-8') as stream:
                for i,(key,call) in enumerate(group):
                    item = results[i+1]
                    # Reverts are retained, never changed to a price of zero.
                    value = item.get('result') if 'result' in item else {'error_code':item.get('error',{}).get('code')}
                    if call[0]=='eth_getBlockByNumber' and isinstance(value,dict) and 'timestamp' in value:
                        # Only header identity/time are needed; do not retain all
                        # unrelated transaction hashes in every cached block.
                        value={k:value[k] for k in ['number','hash','parentHash','timestamp']}
                    self.cache[key] = value
                    stream.write(json.dumps(dict(key=key,method=call[0],params=call[1],result=value,
                        retrieved_utc=datetime.now(timezone.utc).isoformat()))+'\n')
            time.sleep(.15)
        return [self.cache[k] for k in keys]

    def one(self, method, params):
        value = self.batch([(method,params)])[0]
        if isinstance(value,dict) and 'error_code' in value:
            raise RuntimeError(f'RPC_JSON_{value["error_code"]}')
        return value

    def calls(self, block, items):
        packed = [(target,True,bytes.fromhex(data[2:])) for target,data in items]
        data = calldata('aggregate3((address,bool,bytes)[])',['(address,bool,bytes)[]'],[packed])
        return 'eth_call',[{'to':MC,'data':data},hex(int(block))]


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--output-dir',type=Path,required=True)
    ap.add_argument('--stages',default='logs,wallets,depeg,feeds,oracle,receipts')
    ap.add_argument('--full-oracle',action='store_true',help='Observe every block instead of regular-grid + event/peak samples')
    a=ap.parse_args();out=a.output_dir;out.mkdir(parents=True,exist_ok=True)
    stages=set(a.stages.split(','));rpc=CachedRPC(out)
    cfg=json.loads(Path('config/snapshots.json').read_text())
    state=json.loads(Path('data/raw/aave/state_check.json').read_text())['snapshots']
    base=pd.read_parquet('data/processed/aave/oracle_price_path.parquet')
    positions=pd.read_parquet('data/processed/aave/positions_long.parquet')
    previous=pd.read_parquet('data/n3_history_2024_2026/final/n3_liquidations_observed.parquet')
    selected,_=select_event_windows(previous,cfg)
    reports={}
    for sid in ['B1','B2']:
        directory=out/sid;directory.mkdir(exist_ok=True)
        snap=cfg['snapshots'][sid];start=snap['block'];end=int(base[base.snapshot_id.eq(sid)].block.max())
        header=rpc.one('eth_getBlockByNumber',[hex(start),False])
        if header['hash'].lower().removeprefix('0x')!=snap['block_hash'].lower().removeprefix('0x'):
            raise ValueError('Snapshot hash mismatch')
        boundary=rpc.batch([('eth_getBlockByNumber',[hex(n),False]) for n in [end,end+1]])
        cutoff=int(pd.Timestamp(snap['window_end_exclusive_utc']).timestamp())
        if not int(boundary[0]['timestamp'],16)<cutoff<=int(boundary[1]['timestamp'],16):
            raise ValueError('Window end does not match sampled block boundary')
        events_path=directory/'liquidations_rpc.parquet'
        if 'logs' in stages:
            queries=[('eth_getLogs',[{'address':AAVE_POOL,'topics':[EVENT_TOPIC],'fromBlock':hex(lo),'toBlock':hex(min(end,lo+999))}]) for lo in range(start+1,end+1,1000)]
            batches=rpc.batch(queries)
            if any(not isinstance(v,list) for v in batches):
                raise RuntimeError('Incomplete log query; cannot claim zero events')
            logs=[v for batch in batches for v in batch]
            blocks=sorted({int(log['blockNumber'],16) for log in logs})
            headers=rpc.batch([('eth_getBlockByNumber',[hex(n),False]) for n in blocks])
            times={n:int(h['timestamp'],16) for n,h in zip(blocks,headers)}
            events=pd.DataFrame([decode_event(dict(log,timeStamp=hex(times[int(log['blockNumber'],16)]))) for log in logs])
            if events.duplicated(['tx_hash','log_index']).any():raise ValueError('Duplicate RPC event key')
            events['validation_snapshot_id']=sid
            events.to_parquet(events_path,index=False)
            local=selected[selected.validation_snapshot_id.eq(sid)]
            keys=lambda d:set(zip(d.tx_hash.str.lower(),d.log_index))
            proof=dict(first_block=start+1,last_block=end,chunk_count=len(queries),events=len(events),
                source='independent Ethereum archive eth_getLogs rescan vs local Etherscan history',
                missing_local_events=len(keys(events)-keys(local)),missing_rpc_events=len(keys(local)-keys(events)),
                chunks=[dict(first_block=int(q[1][0]['fromBlock'],16),last_block=int(q[1][0]['toBlock'],16),events=len(b)) for q,b in zip(queries,batches)],
                completeness='all_declared_block_chunks_returned; conditional_on_RPC_provider')
            (directory/'log_coverage.json').write_text(json.dumps(proof,indent=2))
            print(sid,'log rescan',len(events),flush=True)
        events=pd.read_parquet(events_path)
        peak=peak_event_window(events,width=300)
        active=positions[positions.snapshot_id.eq(sid)&(positions.debt_amount.gt(0)|(positions.coll_flag&positions.collateral_amount.gt(0)&positions.lt_eff.gt(0)))]
        tokens=active[['token_address','symbol']].drop_duplicates('token_address').sort_values('token_address')
        token_list=tokens.token_address.tolist();oracle=state[sid]['aave_oracle']
        if 'wallets' in stages and sid=='B2':
            evidence=pd.read_parquet('outputs/n5_preparation/n5_N2_update_20261025_v3/B2_oracle_wallet_evidence.parquet')
            wallets=sorted(evidence.loc[evidence.window.eq('primary_peak_300')&~evidence.in_snapshot,'wallet_address'])
            calls=[('eth_call',[{'to':AAVE_POOL,'data':calldata('getUserAccountData(address)',['address'],[w])},hex(start)]) for w in wallets]
            results=rpc.batch(calls);rows=[]
            for w,value in zip(wallets,results):
                if not isinstance(value,str):raise RuntimeError('Missing wallet snapshot state')
                c,d,avail,lt,ltv,hf=decode(['uint256']*6,bytes.fromhex(value[2:]))
                rows.append(dict(snapshot_id=sid,snapshot_block=start,wallet_address=w,call_ok=True,
                    total_debt_base_usd=d/1e8,total_collateral_base_usd=c/1e8,
                    health_factor_reported=hf/1e18 if d else None,
                    classification='no_debt_at_snapshot' if d==0 else 'initial_debt_requires_positions'))
            pd.DataFrame(rows).to_csv(directory/'four_wallet_snapshot_state.csv',index=False)
            borrow_topic='0x'+keccak(text='Borrow(address,address,address,uint256,uint8,uint256,uint16)').hex()
            borrowed=rpc.one('eth_getLogs',[{'address':AAVE_POOL,'topics':[borrow_topic,None,['0x'+w[2:].rjust(64,'0') for w in wallets]],'fromBlock':hex(start+1),'toBlock':hex(end)}])
            (directory/'four_wallet_borrow_logs.json').write_text(json.dumps(borrowed,indent=2))
        if 'depeg' in stages:
            q=base[base.snapshot_id.eq(sid)][['block','block_timestamp_utc']].drop_duplicates().sort_values('block')
            q['block_timestamp_utc']=pd.to_datetime(q.block_timestamp_utc,utc=True)
            grid=pd.date_range(pd.Timestamp(snap['block_timestamp_utc']),pd.Timestamp(snap['window_end_exclusive_utc']),freq='h',inclusive='left')
            matches=pd.merge_asof(pd.DataFrame({'target_utc':grid}),q.sort_values('block_timestamp_utc'),left_on='target_utc',right_on='block_timestamp_utc',direction='backward',tolerance=pd.Timedelta(minutes=10))
            if matches.block.isna().any():raise ValueError('Missing as-of block for Curve depeg')
            calls=[]
            for n in matches.block:
                calls.extend([('eth_call',[{'to':CURVE,'data':calldata('get_dy(int128,int128,uint256)',['int128','int128','uint256'],[1,0,10**14])},hex(int(n))]),
                    ('eth_call',[{'to':CURVE,'data':calldata('fee()')},hex(int(n))])])
            values=rpc.batch(calls);rows=[]
            for i,row in enumerate(matches.to_dict('records')):
                raw=int(values[2*i],16);fee=int(values[2*i+1],16)/1e10
                ratio=raw/1e14/(1-fee)
                rows.append(dict(snapshot_id=sid,block=int(row['block']),timestamp_hour_utc=row['block_timestamp_utc'],target_utc=row['target_utc'],
                    steth_eth_market_ratio=ratio,depeg_pct=(ratio-1)*100,amount_in_raw=str(10**14),amount_out_raw=str(raw),fee_rate=fee,
                    source='Curve archive get_dy small trade; fee-adjusted market ratio estimate; not Lido conversion or Chainlink'))
            pd.DataFrame(rows).to_parquet(directory/'depeg_curve_observed.parquet',index=False)
            print(sid,'historical Curve ratios',len(rows),flush=True)
        if 'feeds' in stages:
            metadata=[dict(snapshot_id=sid,token_address=r['token_address'],symbol=r['symbol'],source_address=r.get('price_source'),aggregator_impl=r.get('aggregator_impl')) for r in state[sid]['reserves'] if r['token_address'] in token_list]
            addresses=sorted({str(v).lower() for r in metadata for v in [r['source_address'],r['aggregator_impl']] if v and int(v,16)!=0})
            topic='0x'+keccak(text='AnswerUpdated(int256,uint256,uint256)').hex()
            queries=[('eth_getLogs',[{'address':addresses,'topics':[topic],'fromBlock':hex(lo),'toBlock':hex(min(end,lo+999))}]) for lo in range(start+1,end+1,1000)]
            batches=rpc.batch(queries)
            if any(not isinstance(v,list) for v in batches):raise RuntimeError('Feed log query failed')
            logs=[v for b in batches for v in b]
            (directory/'answer_updated_raw.json').write_text(json.dumps(logs))
            rows=[]
            for log in logs:
                current=int(log['topics'][1],16);current=current-(1<<256) if current>=(1<<255) else current
                rows.append(dict(feed_address=log['address'].lower(),block=int(log['blockNumber'],16),tx_hash=log['transactionHash'],log_index=int(log['logIndex'],16),
                    answer_raw=str(current),round_id_raw=str(int(log['topics'][2],16)),updated_at=int(log['data'],16)))
            pd.DataFrame(rows).to_parquet(directory/'answer_updated.parquet',index=False)
            for r in metadata:
                relevant={str(v).lower() for v in [r['source_address'],r['aggregator_impl']] if v}
                r['answer_updated_events']=sum(x['feed_address'] in relevant for x in rows)
                r['coverage_status']='queried_declared_snapshot_source_and_implementation; adapter_underlying_and_proxy_upgrades_not_yet_audited'
            pd.DataFrame(metadata).to_csv(directory/'feed_metadata.csv',index=False)
            print(sid,'AnswerUpdated',len(rows),flush=True)
        if 'oracle' in stages:
            # Existing regular grid + every primary-peak block + pre-event blocks.
            blocks=sorted(set(base[base.snapshot_id.eq(sid)].block.astype(int))|set(range(peak['start_block'],peak['end_block']+1))|{int(n)-1 for n in events.event_block if int(n)>start})
            if a.full_oracle:blocks=list(range(start,end+1))
            items=[(oracle,calldata('getAssetPrice(address)',['address'],[t])) for t in token_list]
            rows=[]
            for offset in range(0,len(blocks),100):
                group=blocks[offset:offset+100]
                results=rpc.batch([rpc.calls(n,items) for n in group])
                headers=rpc.batch([('eth_getBlockByNumber',[hex(n),False]) for n in group])
                for n,res,h in zip(group,results,headers):
                    if not isinstance(res,str):raise RuntimeError('Oracle multicall RPC failed')
                    values=decode(['(bool,bytes)[]'],bytes.fromhex(res[2:]))[0]
                    for (token,symbol),(ok,data) in zip(tokens.itertuples(index=False,name=None),values):
                        raw=int.from_bytes(data,'big') if ok and len(data)==32 else None
                        rows.append(dict(snapshot_id=sid,block=n,block_timestamp_utc=pd.Timestamp(int(h['timestamp'],16),unit='s',tz='UTC'),
                            token_address=token,symbol=symbol,price_usd=raw/1e8 if raw is not None else None,
                            oracle_price_raw=str(raw) if raw is not None else None,status='observed' if raw is not None and raw>0 else 'invalid_or_missing_price'))
                print(sid,'oracle blocks',min(offset+100,len(blocks)),'/',len(blocks),flush=True)
            pd.DataFrame(rows).to_parquet(directory/'oracle_all_active_tokens.parquet',index=False)
        if 'receipts' in stages:
            txs=events[['tx_hash','event_block']].drop_duplicates('tx_hash')
            results=rpc.batch([('eth_getTransactionReceipt',[h]) for h in txs.tx_hash]);rows=[]
            for r,(h,n) in zip(results,txs.itertuples(index=False,name=None)):
                if not isinstance(r,dict) or 'error_code' in r or int(r['blockNumber'],16)!=n or int(r['status'],16)!=1:
                    raise RuntimeError('Missing, failed or wrong-block receipt')
                gas=int(r['gasUsed'],16);price=int(r['effectiveGasPrice'],16)
                rows.append(dict(snapshot_id=sid,tx_hash=h,block_number=n,block_hash=r['blockHash'],gas_used=gas,effective_gas_price_wei=str(price),
                    gas_cost_eth=gas*price/1e18,receipt_status=1,liquidation_events=int(events.tx_hash.eq(h).sum()),
                    scope='whole transaction cost, not individual liquidation or net profit'))
            pd.DataFrame(rows).to_parquet(directory/'executed_receipts.parquet',index=False)
            print(sid,'receipts',len(rows),flush=True)
        reports[sid]=dict(start_block=start,end_block=end,events=len(events),active_tokens=len(tokens),peak=peak)
    (out/'collection_summary.json').write_text(json.dumps(reports,indent=2))
    print(json.dumps(reports,indent=2))


if __name__=='__main__':main()
