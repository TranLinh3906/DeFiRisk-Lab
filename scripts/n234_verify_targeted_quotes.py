"""Independently replay all 27 targeted quotes through the configured archive RPC."""
import json
from pathlib import Path

import pandas as pd
from eth_abi import decode
from scripts.n234_collect_history import CachedRPC,calldata
from scripts.n3_priority2_multivolume_quotes import QUOTER_V2,FACTORY_V3
from src.models.n5_n3_update import calibrate_quote_grid
from src.models.n5_readiness import write_manifest,verify_manifest


def main():
    out=Path('outputs/n5_completion_20261026/quote_replay27');out.mkdir(exist_ok=True)
    rpc=CachedRPC(out)
    source=Path('outputs/n3_priority2_quote_grid_v2_with_27/n3_targeted_27_rpc_verified_only.parquet')
    q=pd.read_parquet(source)
    cfg_path=Path('config/snapshots.json');cfg=json.loads(cfg_path.read_text())
    for sid,g in q.groupby('snapshot_id'):
        h=rpc.one('eth_getBlockByNumber',[hex(int(g.snapshot_block.iloc[0])),False])
        if h['hash'].lower().removeprefix('0x')!=cfg['snapshots'][sid]['block_hash'].lower().removeprefix('0x'):raise ValueError('Snapshot mismatch')
    calls=[('eth_call',[{'to':QUOTER_V2,'data':calldata('quoteExactInputSingle((address,address,uint256,uint24,uint160))',
        ['(address,address,uint256,uint24,uint160)'],[(r.token_in_address,r.token_out_address,int(r.amount_in_raw),int(r.fee_tier),0)])},hex(int(r.snapshot_block))]) for r in q.itertuples()]
    values=rpc.batch(calls)
    for row,value in zip(q.itertuples(),values):
        amount=decode(['uint256','uint160','uint32','uint256'],bytes.fromhex(value[2:]))[0]
        if amount!=int(row.amount_out_raw):raise ValueError('Raw quote replay mismatch')
    base=Path('outputs/n3_priority2_quote_grid_v1/n3_multivolume_rpc_verified_only.parquet')
    anchors=Path('outputs/n3_priority2_quote_grid_v1/n3_2pct_observed_anchors.parquet')
    dex=Path('data/staging/n3_dex_liquidity_A_B1_B2.parquet')
    fits,residuals,qa=calibrate_quote_grid(pd.concat([pd.read_parquet(base),q],ignore_index=True),pd.read_parquet(anchors),pd.read_parquet(dex),cfg,quantization_policy='conservative_raw_unit_bound')
    residuals.to_parquet(out/'curve_quantization_audit.parquet',index=False)
    report=dict(raw_quote_replays=27,raw_output_mismatches=0,qa=qa)
    (out/'summary.json').write_text(json.dumps(report,indent=2))
    write_manifest(out,[source,base,anchors,dex,cfg_path,Path(__file__),Path('src/models/n5_n3_update.py'),out/'rpc_checkpoint.jsonl'],
        {'summary.json':'independent_rpc_replay','curve_quantization_audit.parquet':'model_bound_not_observed_for_three_quantized_points'},report)
    assert not verify_manifest(out)
    print(json.dumps(report,indent=2))


if __name__=='__main__':main()
