import json, tempfile, sys, importlib.util
from pathlib import Path
import pandas as pd
import numpy as np

base=Path('/mnt/data/N3_UU_TIEN_2_3_FIX/scripts')
spec=importlib.util.spec_from_file_location('p2',base/'n3_priority2_multivolume_quotes.py')
p2=importlib.util.module_from_spec(spec);spec.loader.exec_module(p2)
rows=[]
for sid,block in p2.BLOCKS.items():
 for i in range(4):
  rows.append(dict(snapshot_id=sid,snapshot_block=block,dex='uniswap_v3',pool_address=f'0x{i+1:040x}',token_in='USDC',token_out='ETH', token_in_address=f'0x{i+20:040x}',token_out_address=f'0x{i+30:040x}',input_token_usd_oracle=1.0,spot_output_raw_per_input_raw=0.0003, status='verified_2pct_bracket',depth_lower_bound_usd=5000,depth_upper_bound_usd=5001,impact_below_depth_pct=1.999,observed_impact_pct=2.001,max_search_usd=50000000,snapshot_block_hash='0x'+'1'*64,fee_tier=3000))
source=pd.DataFrame(rows)
anchors,grid=p2.build_grid(source,[1000,5000,10000,50000,100000])
assert len(anchors)==24 and len(grid)==60
assert (anchors.impact_pct<2).sum()==12 and (anchors.impact_pct>=2).sum()==12
assert grid['status'].eq('NOT_QUOTED').all()
print('P2 build_grid: PASS',len(anchors),len(grid))

spec=importlib.util.spec_from_file_location('p3',base/'n3_priority3_price_qa.py')
p3=importlib.util.module_from_spec(spec);spec.loader.exec_module(p3)
df=pd.DataFrame([
  {'timestamp':'2024-08-04T23:00:00Z','reference_timestamp':'2024-08-04T23:00:00Z','asset':'ETH','quote_asset':'USD','token_address':'0x1','price_type':'market_reference','price_value':2200,'source':'market','snapshot_block':np.nan},
  {'timestamp':'2024-08-04T23:30:00Z','reference_timestamp':'2024-08-04T23:30:00Z','asset':'ETH','quote_asset':'USD','token_address':'0x1','price_type':'market_reference','price_value':2210,'source':'market','snapshot_block':np.nan},
  {'timestamp':'2024-08-04T23:59:59Z','reference_timestamp':'2024-08-04T23:59:59Z','asset':'ETH','quote_asset':'USD','token_address':'0x1','price_type':'aave_oracle','price_value':2220,'source':'oracle','snapshot_block':20458637},
  {'timestamp':'2024-08-04T23:10:00Z','reference_timestamp':'2024-08-05T00:00:00Z','asset':'ETH','quote_asset':'USD','token_address':'0x1','price_type':'market_reference','price_value':2222,'source':'market','snapshot_block':np.nan},
  {'timestamp':'2024-08-04T23:35:00Z','reference_timestamp':'2024-08-04T23:35:00Z','asset':'GHO','quote_asset':'USD','token_address':'0x2','price_type':'market_reference','price_value':0.99,'source':'market','snapshot_block':np.nan},
  {'timestamp':'2024-08-04T23:00:00Z','reference_timestamp':'2024-08-04T23:00:00Z','asset':'STETH','quote_asset':'ETH','token_address':'0x3','price_type':'market_ratio','price_value':0.998,'source':'market','snapshot_block':np.nan},
])
p=p3.normalize(df)
assert p.qa_valid.sum()==5
market,conflicts=p3.verified_market(p)
assert len(market)==3
returns=p3.returns_market(market,90)
assert returns.return_valid.sum()==1
assert 'aave_oracle' not in market.price_type.values
snap_meta={'chain_id':1,'snapshots':{'A':{'snapshot_block':26179841,'snapshot_timestamp_utc':'2026-01-01T00:00:00Z'},'B1':{'snapshot_block':20458637,'snapshot_timestamp_utc':'2024-08-04T23:59:59Z'},'B2':{'snapshot_block':23543615,'snapshot_timestamp_utc':'2025-10-09T23:59:59Z'}}}
snaps,st=p3.snapshot_qa(p,market,snap_meta,60)
assert st=='SNAPSHOT_QA_COMPUTED' and snaps[1]['n_distinct_market_assets']==2 and snaps[1]['aave_oracle_rows_at_exact_block']==1
print('P3 pricing/returns/source separation: PASS',len(market),int(returns.return_valid.sum()))

# Smoke test CLI without pyarrow: monkeypatch parquet to/from pickle (identical DataFrame semantics).
old_read=pd.read_parquet; old_write=pd.DataFrame.to_parquet
pd.read_parquet=lambda p,**kw:pd.read_pickle(p)
pd.DataFrame.to_parquet=lambda self,path,**kw:self.to_pickle(path)
with tempfile.TemporaryDirectory() as td:
 td=Path(td)
 source.to_pickle(td/'dex.parquet'); (td/'dex.json').write_text(json.dumps({'n_verified_depth':12,'snapshot_blocks':p2.BLOCKS}))
 sys.argv=['n3_priority2_multivolume_quotes.py','--dex',str(td/'dex.parquet'),'--source-qa',str(td/'dex.json'),'--output-dir',str(td/'p2')]
 p2.main()
 assert (td/'p2/n3_priority2_quality.json').exists()
 assert json.loads((td/'p2/n3_priority2_quality.json').read_text())['real_new_rpc_quotes']==0
 df.to_pickle(td/'prices.parquet');(td/'meta.json').write_text(json.dumps(snap_meta))
 sys.argv=['n3_priority3_price_qa.py','--prices',str(td/'prices.parquet'),'--snapshot-metadata',str(td/'meta.json'),'--output-dir',str(td/'p3')]
 p3.main()
 assert json.loads((td/'p3/n3_priority3_quality.json').read_text())['market_usd_quotes_clean']==3
 assert (td/'p3/n3_market_returns_N5_no_lookahead.parquet').exists()
pd.read_parquet=old_read;pd.DataFrame.to_parquet=old_write
print('CLI dry-run artifacts: PASS')
