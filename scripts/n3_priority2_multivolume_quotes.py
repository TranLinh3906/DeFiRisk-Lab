#!/usr/bin/env python3
"""N3 priority 2: preserve certified 2% depth; request REAL archival swap quotes at multiple sizes.

DRY RUN IS DEFAULT. No interpolation; never manufacture verified price-impact observations.
Requires pandas+pyarrow; --execute also requires requests+eth-abi+eth-utils+python-dotenv.
"""
from __future__ import annotations
import argparse
import json
import os
import time
import hashlib
import math
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

BLOCKS = {"A": 26179841, "B1": 20458637, "B2": 23543615}
DEFAULT_SIZES = (1000, 5000, 10000, 25000, 50000, 100000, 250000)
QUOTER_V2 = '0x61fFE014bA17989E743c5F6cB21bF9697530B21e'
FACTORY_V3 = '0x1F98431c8aD98523631AE4a59f267346ea31F984'
CURVE_STETH = '0xDC24316b9AE028F1497c275EB9192a3Ea0f67022'
ZERO = '0x0000000000000000000000000000000000000000'


def canonical_block_hash(value):
    """Normalize 0x-prefixed or bare Ethereum block hashes without relaxing equality."""
    if isinstance(value, (bytes, bytearray)):
        value = value.decode('ascii')
    s = str(value).strip().lower()
    if s.startswith('0x'):
        s = s[2:]
    if len(s) != 64 or any(c not in '0123456789abcdef' for c in s):
        raise RuntimeError('INVALID_SNAPSHOT_BLOCK_HASH_FORMAT')
    return '0x' + s


def _valid_num(v):
    try:
        return math.isfinite(float(v))
    except (ValueError, TypeError):
        return False


def build_grid(df, sizes):
    needed = {'snapshot_id', 'snapshot_block', 'dex', 'pool_address', 'token_in',
              'token_out', 'token_in_address', 'token_out_address', 'input_token_usd_oracle',
              'spot_output_raw_per_input_raw', 'status', 'depth_lower_bound_usd',
              'depth_upper_bound_usd', 'impact_below_depth_pct', 'observed_impact_pct',
              'max_search_usd', 'snapshot_block_hash'}
    absent = needed-set(df.columns)
    if absent:
        raise ValueError(f'DEX input missing columns: {sorted(absent)}')
    if len(df) != 12:
        raise ValueError(f'Expected 12 verified rows (3 snapshots x 4 directions), got {len(df)}')
    df = df.copy()
    if any(int(r.snapshot_block) != BLOCKS.get(str(r.snapshot_id)) for r in df.itertuples()):
        raise ValueError('Snapshot block mapping mismatch: refuse to quote')
    if not df.status.eq('verified_2pct_bracket').all():
        raise ValueError('Not all original depths are certified; manual QA required')
    keycols = ['snapshot_id','dex','pool_address','token_in_address','token_out_address']
    if df.duplicated(keycols).any():
        raise ValueError('Duplicate pool-direction-snapshot rows')
    anchors, requests = [], []
    for d in df.to_dict('records'):
        for name in ('input_token_usd_oracle','spot_output_raw_per_input_raw'):
            if not _valid_num(d[name]) or float(d[name]) <= 0:
                raise ValueError(f'Invalid {name}: {d["snapshot_id"]}/{d["token_in"]}')
        lower, upper = float(d['depth_lower_bound_usd']), float(d['depth_upper_bound_usd'])
        if not (0 < lower <= upper <= float(d['max_search_usd'])):
            raise ValueError('Invalid certified 2pct bracket limits')
        for label,size,impact in [('lower_2pct_bracket', lower, d['impact_below_depth_pct']),
                                   ('upper_2pct_bracket', upper, d['observed_impact_pct'])]:
            if not _valid_num(impact):
                raise ValueError(f'Missing certified {label} impact')
            anchors.append({**{k:d.get(k) for k in keycols}, 'snapshot_block':int(d['snapshot_block']),
                            'trade_size_usd':size,'impact_pct':float(impact), 'point_label':label,
                            'verification':'VERIFIED_FROM_ORIGINAL_DEPTH_BRACKET',
                            'pricing_basis':'same-block on-chain quote; input USD normalized by Aave oracle'})
        for s in sizes:
            requests.append({**{k:d.get(k) for k in keycols},
                             'snapshot_block':int(d['snapshot_block']),
                             'snapshot_block_hash':str(d['snapshot_block_hash']),
                             'token_in':d['token_in'],'token_out':d['token_out'],
                             'fee_tier':d.get('fee_tier'),
                             'input_token_usd_oracle':float(d['input_token_usd_oracle']),
                             'spot_output_raw_per_input_raw':float(d['spot_output_raw_per_input_raw']),
                             'trade_size_usd':float(s), 'status':'NOT_QUOTED',
                             'amount_out_raw':None,'impact_pct':None,'error_code':None})
    return pd.DataFrame(anchors),pd.DataFrame(requests)


def request_id(rec):
    return hashlib.sha256(json.dumps([
       rec['snapshot_id'],int(rec['snapshot_block']),str(rec['dex']).lower(),
       str(rec['pool_address']).lower(),str(rec['token_in_address']).lower(),
       str(rec['token_out_address']).lower(),float(rec['trade_size_usd'])
    ],separators=(',',':')).encode()).hexdigest()


class Rpc:
    def __init__(self,url,interval):
        import requests
        self.url=url; self.interval=interval; self.session=requests.Session(); self.next_id=1
    def call(self,method,params):
        import requests
        time.sleep(self.interval)
        payload={'jsonrpc':'2.0','id':self.next_id,'method':method,'params':params}
        self.next_id+=1
        for retry in range(4):
            try:
                res=self.session.post(self.url,json=payload,timeout=60)
                if res.status_code in (429,500,502,503,504):
                    if retry==3: raise RuntimeError(f'RPC_HTTP_{res.status_code}')
                    time.sleep(min(45, 3 * (2**retry))); continue
                if res.status_code != 200: raise RuntimeError(f'RPC_HTTP_{res.status_code}')
                msg=res.json()
                if 'error' in msg:
                    code=msg['error'].get('code','unknown')
                    raise RuntimeError(f'RPC_JSON_{code}')
                return msg['result']
            except requests.RequestException as e:
                if retry==3: raise RuntimeError('RPC_CONNECTION_'+type(e).__name__) from None
                time.sleep(min(45,3*2**retry))
        raise RuntimeError('RPC_RETRIES_EXHAUSTED')


def quote_one(r,rpc):
    from eth_utils import keccak
    from eth_abi import encode, decode
    block=hex(int(r['snapshot_block']))
    chain=rpc.call('eth_chainId',[])
    if int(chain,16)!=1: raise RuntimeError('RPC_WRONG_CHAIN')
    header=rpc.call('eth_getBlockByNumber',[block,False])
    if not header: raise RuntimeError('RPC_MISSING_ARCHIVE_BLOCK')
    actual_hash = canonical_block_hash(header['hash'])
    expected_hash = canonical_block_hash(r['snapshot_block_hash'])
    if actual_hash != expected_hash:
        raise RuntimeError(
            f'SNAPSHOT_BLOCK_HASH_MISMATCH snapshot={r["snapshot_id"]} '
            f'block={r["snapshot_block"]} actual={actual_hash} stored={expected_hash}'
        )

    def eth_call(to,signature,types,values):
        selector=keccak(text=signature)[:4]
        data='0x'+(selector+encode(types,values)).hex()
        return bytes.fromhex(rpc.call('eth_call',[{'to':to,'data':data},block])[2:])

    token_in=str(r['token_in_address'])
    token_out=str(r['token_out_address'])
    is_curve=str(r['dex']).lower().startswith('curve')
    if is_curve:
        if str(r['pool_address']).lower()!=CURVE_STETH.lower():
            raise RuntimeError('CURVE_POOL_NOT_SUPPORTED')
        tin=str(r['token_in']).upper().replace('WETH','ETH')
        tout=str(r['token_out']).upper().replace('WETH','ETH')
        if {tin,tout}!={'ETH','STETH'}:
            raise RuntimeError('CURVE_UNSUPPORTED_DIRECTION')
        dec_in=18
    else:
        fee=r.get('fee_tier')
        if not _valid_num(fee): raise RuntimeError('MISSING_UNISWAP_FEE')
        fee=int(fee)
        found=eth_call(FACTORY_V3,'getPool(address,address,uint24)',
                       ['address','address','uint24'],[token_in,token_out,fee])
        factory_pool=decode(['address'],found)[0]
        if factory_pool.lower()!=str(r['pool_address']).lower():
            raise RuntimeError('UNISWAP_POOL_MISMATCH')
        dec_in=int(decode(['uint8'],eth_call(token_in,'decimals()',[],[]))[0])
    dec_out=18 if is_curve else int(decode(['uint8'],eth_call(token_out,'decimals()',[],[]))[0])
    if not 0 <= dec_in <= 36 or not 0 <= dec_out <= 36: raise RuntimeError('INVALID_TOKEN_DECIMALS')
    amount=int(float(r['trade_size_usd'])/float(r['input_token_usd_oracle'])*10**dec_in)
    if amount<=0: raise RuntimeError('TRADE_ROUNDS_TO_ZERO')
    if is_curve:
        idx={'ETH':0,'STETH':1}
        out=int(decode(['uint256'],eth_call(r['pool_address'],'get_dy(int128,int128,uint256)',
            ['int128','int128','uint256'],[idx[tin],idx[tout],amount]))[0])
    else:
        data=eth_call(QUOTER_V2,'quoteExactInputSingle((address,address,uint256,uint24,uint160))',
            ['(address,address,uint256,uint24,uint160)'],[(token_in,token_out,amount,fee,0)])
        out=int(decode(['uint256','uint160','uint32','uint256'],data)[0])
    if out<=0: raise RuntimeError('QUOTE_ZERO_OUTPUT')
    # Reference in original data is RAW output units / RAW input units.
    ref=float(r['spot_output_raw_per_input_raw'])
    impact=100*(1-(out/amount)/ref)
    if not math.isfinite(impact): raise RuntimeError('INVALID_IMPACT')
    return {'status':'RPC_VERIFIED','amount_out_raw':str(out),'amount_in_raw':str(amount),
            'input_decimals':dec_in,'output_decimals':dec_out,'impact_pct':float(impact),
            'verified_block_hash':actual_hash,'quote_method':'eth_call_archive_exact_block'}


def main():
    p=argparse.ArgumentParser(description='N3 P2: observed bracket anchors + multi-quote request grid')
    p.add_argument('--dex',type=Path,required=True)
    p.add_argument('--source-qa',type=Path,required=True)
    p.add_argument('--output-dir',type=Path,default=Path('outputs/n3_priority2_quote_grid_v1'))
    p.add_argument('--sizes',default=','.join(map(str,DEFAULT_SIZES)))
    p.add_argument('--execute',action='store_true',help='Use YOUR archive RPC; actual on-chain quotes only')
    p.add_argument('--rpc-interval',type=float,default=1.5)
    a=p.parse_args()
    try:
        sizes=sorted({float(x) for x in a.sizes.split(',')})
    except ValueError:
        p.error('Invalid sizes')
    if not sizes or min(sizes)<=0 or not all(map(math.isfinite,sizes)) or a.rpc_interval<0:
        p.error('Invalid --sizes/--rpc-interval')
    qa=json.loads(a.source_qa.read_text(encoding='utf-8'))
    if qa.get('n_verified_depth')!=12 or qa.get('snapshot_blocks')!=BLOCKS:
        raise SystemExit('STOP: original depth QA inconsistent with 12 certified brackets')
    df=pd.read_parquet(a.dex)
    anchors,grid=build_grid(df,sizes)
    a.output_dir.mkdir(parents=True,exist_ok=True)
    # Never modify the original DEX parquet. A separate, append-only RPC proof log.
    checkpoint=a.output_dir/'n3_priority2_verified_quotes_checkpoint.jsonl'
    saved={}
    if checkpoint.exists():
        for ln in checkpoint.read_text(encoding='utf-8').splitlines():
            if ln.strip():
                d=json.loads(ln)
                if d.get('status')=='RPC_VERIFIED':
                    k=d['request_id']
                    if k in saved and saved[k]!=d: raise SystemExit('Conflicting RPC checkpoint records')
                    saved[k]=d
    if a.execute:
        from dotenv import load_dotenv
        load_dotenv()
        url=os.getenv('ETHEREUM_ARCHIVE_RPC_URL') or os.getenv('ETH_RPC_URL')
        if not url: raise SystemExit('Missing ETHEREUM_ARCHIVE_RPC_URL / ETH_RPC_URL in .env')
        rpc=Rpc(url,a.rpc_interval)
        with checkpoint.open('a',encoding='utf-8') as f:
            for i,r in grid.iterrows():
                key=request_id(r)
                if key in saved:continue
                try:
                    result=quote_one(r,rpc)
                    item={'request_id':key,**result,'verified_utc':datetime.now(timezone.utc).isoformat()}
                    f.write(json.dumps(item,ensure_ascii=False)+'\n');f.flush();os.fsync(f.fileno())
                    saved[key]=item
                    print(f'[RPC VERIFIED] {i+1}/{len(grid)} {r.snapshot_id} {r.token_in}->{r.token_out} USD={r.trade_size_usd:g} impact={result["impact_pct"]:.4f}%')
                except RuntimeError as e:
                    print(f'[STOP] {type(e).__name__} {str(e)}; checkpoint retained')
                    break
    completed=[]
    for r in grid.to_dict('records'):
        item=saved.get(request_id(r))
        if item:
            for k in ('status','amount_out_raw','amount_in_raw','input_decimals','output_decimals','impact_pct','verified_block_hash','quote_method'):
                r[k]=item.get(k)
        completed.append(r)
    grid=pd.DataFrame(completed)
    verified=grid.loc[grid.status.eq('RPC_VERIFIED')].copy()
    # The original bracket rows are observed anchors, not synthetic fixed-size quotes.
    anchors.to_parquet(a.output_dir/'n3_2pct_observed_anchors.parquet',index=False)
    grid.to_csv(a.output_dir/'n3_multivolume_quote_requests_and_status.csv',index=False)
    verified.to_parquet(a.output_dir/'n3_multivolume_rpc_verified_only.parquet',index=False)
    grouped=(verified.groupby(['snapshot_id','pool_address','token_in_address','token_out_address'])['trade_size_usd'].nunique()
             if len(verified) else pd.Series(dtype=int))
    monotonic_violations=[]
    for keys, group in verified.groupby(['snapshot_id','pool_address','token_in_address','token_out_address']):
        group=group.sort_values('trade_size_usd')
        impacts=pd.to_numeric(group['impact_pct'],errors='coerce').dropna().tolist()
        if any(b < x-0.15 for x,b in zip(impacts,impacts[1:])):
            monotonic_violations.append(list(map(str,keys)))
    status={'source_2pct_brackets_verified':len(df),
            'bracket_anchor_points':len(anchors),
            'target_grid_queries':len(grid),'real_new_rpc_quotes':len(verified),
            'not_quoted':int(len(grid)-len(verified)),
            'number_of_directions_with_at_least_5_real_sizes':int((grouped>=5).sum()),
            'directions_total':12,
            'calibration_ready':len(grouped)==12 and bool((grouped>=5).all()) and not monotonic_violations,
            'impact_monotonicity_violations':monotonic_violations,
            'executed_rpc':bool(a.execute),
            'warning':'Original 2% brackets stay verified. Unquoted sizes are NOT verified or interpolated. See checkpoint for actual RPC proofs.',
            'input_dex_sha256':hashlib.sha256(a.dex.read_bytes()).hexdigest(),
            'input_qa_sha256':hashlib.sha256(a.source_qa.read_bytes()).hexdigest(),
            'created_utc':datetime.now(timezone.utc).isoformat()}
    (a.output_dir/'n3_priority2_quality.json').write_text(json.dumps(status,ensure_ascii=False,indent=2))
    print('[P2]',json.dumps({k:status[k] for k in ('bracket_anchor_points','target_grid_queries','real_new_rpc_quotes','not_quoted','calibration_ready')}))
    print('[SAVED]',a.output_dir)

if __name__=='__main__':
    main()
