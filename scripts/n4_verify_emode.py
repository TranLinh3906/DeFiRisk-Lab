"""Replay version-appropriate E-Mode parameters and membership, plus equity strata."""
import json
from pathlib import Path

from eth_abi import decode
import numpy as np
import pandas as pd

from scripts.n234_collect_history import CachedRPC,calldata,AAVE_POOL


def main():
    out=Path('outputs/n5_completion_20261026/emode_replay');out.mkdir(exist_ok=True)
    rpc=CachedRPC(out)
    membership=pd.read_parquet('data/processed/aave/emode_membership.parquet')
    reserve=pd.read_csv('outputs/n5_completion_20261026/extended_proofs/reserve_parameter_replay.csv')
    methods=['getEModeCategoryData(uint8)','getEModeCategoryCollateralConfig(uint8)',
        'getEModeCategoryCollateralBitmap(uint8)','getEModeCategoryBorrowableBitmap(uint8)']
    specs=[(sid,int(block),int(category),method) for (sid,block,category),_ in membership.groupby(['snapshot_id','snapshot_block','emode_category']) for method in methods]
    values=rpc.batch([('eth_call',[{'to':AAVE_POOL,'data':calldata(method,['uint8'],[category])},hex(block)]) for sid,block,category,method in specs])
    observed={}
    for (sid,block,category,method),value in zip(specs,values):observed.setdefault((sid,block,category),{})[method]=value
    rows=[]
    for (sid,block,category),g in membership.groupby(['snapshot_id','snapshot_block','emode_category']):
        answer=observed[sid,int(block),int(category)]
        modern=answer[methods[1]]
        if isinstance(modern,str) and len(modern)>=194:
            ltv,lt,bonus=decode(['(uint16,uint16,uint16)'],bytes.fromhex(modern[2:]))[0]
            cb=int(answer[methods[2]],16);bb=int(answer[methods[3]],16);mode='modern_bitmaps'
        else:
            value=decode(['(uint16,uint16,uint16,address,string)'],bytes.fromhex(answer[methods[0]][2:]))[0]
            ltv,lt,bonus=value[:3];mode='legacy_reserve_category'
        for row in g.to_dict('records'):
            if mode=='modern_bitmaps':
                coll=bool((cb>>int(row['reserve_id']))&1);borrow=bool((bb>>int(row['reserve_id']))&1)
            else:
                r=reserve[reserve.snapshot_id.eq(sid)&reserve.token_address.eq(row['token_address'])].iloc[0]
                coll=borrow=((int(r.raw_configuration)>>168)&255)==int(category)
            matches=(int(row['emode_ltv_bps'])==ltv and int(row['emode_lt_bps'])==lt and int(row['emode_bonus_raw_bps'])==bonus
                and bool(row['is_collateral'])==coll and bool(row['is_borrowable'])==borrow)
            rows.append(dict(snapshot_id=sid,snapshot_block=int(block),emode_category=int(category),token_address=row['token_address'],
                ltv_bps=ltv,lt_bps=lt,bonus_raw_bps=bonus,is_collateral=coll,is_borrowable=borrow,version_method=mode,matches_N2=matches))
    if not all(r['matches_N2'] for r in rows):raise ValueError('E-Mode membership/parameter mismatch')
    pd.DataFrame(rows).to_parquet(out/'emode_parameter_membership_replay.parquet',index=False)
    risk=pd.read_parquet('outputs/n5_completion_20261026/n4_research/borrower_risk.parquet')
    risk['initial_equity_usd']=risk.total_collateral_usd-risk.total_debt_usd
    risk['positive_equity']=risk.initial_equity_usd.gt(0)
    risk['leverage']=risk.total_collateral_usd/risk.initial_equity_usd.where(risk.positive_equity)
    risk['equity_bucket']=pd.cut(risk.initial_equity_usd,[-np.inf,1e4,1e5,1e6,1e7,np.inf],right=False,
        labels=['<10k','10k-100k','100k-1m','1m-10m','>=10m']).astype(str)
    risk['loss_normalization_eligible']=risk.positive_equity
    risk.to_parquet(out/'wallet_equity_strata.parquet',index=False)
    (out/'summary.json').write_text(json.dumps(dict(emode_rows_replayed=len(rows),parameter_membership_mismatches=0,
        categories=len(observed),equity_wallets=len(risk),nonpositive_equity_wallets=int((~risk.positive_equity).sum()),
        scope='Initial cohort equity strata only. Normalized loss/ES require actual scenario losses; negative or zero equity is not normalized.'),indent=2))
    print((out/'summary.json').read_text())


if __name__=='__main__':main()
