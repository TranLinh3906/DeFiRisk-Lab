"""Only synthetic smoke tests; does not access Ethereum nor historical real Parquets."""
import importlib.util,sys,tempfile,json
from pathlib import Path
import pandas as pd
HERE=Path(__file__).resolve().parents[1]
def load(path,name):
 spec=importlib.util.spec_from_file_location(name,path)
 mod=importlib.util.module_from_spec(spec);spec.loader.exec_module(mod)
 return mod
price=load(HERE/'src/data/n3_price_timeseries.py','n3_pricepatched')
depeg=load(HERE/'scripts/n3_depeg_b1_b2_qa.py','n3_depeg')
meta={'chain_id':1,'protocol_id':'aave_v3_ethereum','status':{'archive_eth_call_verified':True},
 'snapshots':{sid:{'snapshot_block':block,'snapshot_timestamp_utc':ts,'n_reserves':1,
     'aave_oracle':'0x'+'1'*40,'block_hash_verified_on_chain':True} for sid,block,ts in [
 ('A',26179841,'2026-10-15T00:00:00Z'),('B1',20458637,'2024-08-04T23:59:59Z'),('B2',23543615,'2025-10-09T23:59:59Z')]}}
assert len(price.snapshot_config(meta))==3
snap=price.snapshot_config(meta)
market=pd.DataFrame([{'timestamp':pd.Timestamp('2026-10-14T23:59:50Z'),
 'reference_timestamp':pd.Timestamp('2026-10-14T23:59:50Z'), 'asset':asset,
 'quote_asset':'USD','price_type':'market_reference','price_value':100.0,
 'source':'Independent historical market', 'staleness_seconds':0} for asset in ['ETH','STETH','WSTETH']])
checks=price.validate_market(market,snap,60)
assert checks['A']['market_snapshot_ready'] and not checks['B1']['market_snapshot_ready'] and not checks['B2']['market_snapshot_ready']
assert checks['B1']['steth_eth_market_ratio_asof'] is None
lido=pd.DataFrame([{'snapshot_id':sid,'snapshot_block':block,'snapshot_timestamp':ts,
 'wsteth_address':price.LIDO_WSTETH,'steth_per_wsteth':1.2,'source':'Lido exact block'}
 for sid,block,ts in [('A',26179841,'2026-10-15T00:00:00Z'),('B1',20458637,'2024-08-04T23:59:59Z'),('B2',23543615,'2025-10-09T23:59:59Z')]])
assert len(price.build_lido(lido,snap))==3
ratios=pd.DataFrame({'timestamp_hour_utc':['2024-08-04T23:59:50Z','2024-08-05T00:59:50Z','2025-10-09T23:59:50Z'],
 'steth_eth_market_ratio':[0.997,0.992,0.999], 'source':['Curve historical market']*3})
ratios['timestamp_utc']=pd.to_datetime(ratios.timestamp_hour_utc,utc=True)
ratios['market_ratio']=ratios.steth_eth_market_ratio
ratios['depeg_pct']=(ratios.market_ratio-1)*100
ratios['provenance_check']='MARKET_SOURCE_CANDIDATE'
grid,summary=depeg.audit(ratios,meta,max_lag_min=60,window_hours=2)
assert len(summary)==2 and summary[0]['market_ratio_asof_snapshot_available']
assert len(grid)>0 and not grid.market_quote_valid.all()  # missing after 2nd hour
assert summary[0]['status']=='INCOMPLETE_MARKET_SERIES'
print('PASS synthetic: A/B1/B2 metadata, no-lookahead market QA, 3 Lido snapshots, market depeg incomplete flags')
# CLI smoke: peak-range audit and gas audit without RPC using Parquet IO mocked
# as pickle only in the test's temporary directory (not in delivery scripts).
import runpy
with tempfile.TemporaryDirectory() as dirname:
 root=Path(dirname)
 old_read=pd.read_parquet; old_write=pd.DataFrame.to_parquet
 pd.read_parquet=lambda p,*args,**kw: pd.read_pickle(p)
 pd.DataFrame.to_parquet=lambda self,p,*args,**kw: self.to_pickle(p)
 try:
  args=[]
  for sid,block,st in [('b1',20458640,'2024-08-05T00:01:00Z'),('b2',23543618,'2025-10-10T00:02:00Z')]:
   src=root/f'{sid}_events.parquet';qa=root/f'{sid}_qa.json'
   h='0x'+('1' if sid=='b1' else '2')*64
   pd.DataFrame([{'tx_hash':h,'log_index':1,'event_block':block,'event_timestamp':st,
                  'debt_token_address':'0x'+'1'*40,'collateral_token_address':'0x'+'2'*40}]).to_pickle(src)
   qa.write_text(json.dumps({'chain_id':1,'protocol_id':'aave_v3_ethereum','events_decoded':1,
                              'first_block':block-500,'last_block':block+500}))
   args.extend([f'--events-{sid}',str(src),f'--qa-{sid}',str(qa),f'--peak-{sid}',str(block)])
  peakout=root/'peak';oldargv=sys.argv[:]
  sys.argv=['n3_peak300_liquidation_qa.py',*args,'--out',str(peakout)]
  try: runpy.run_path(str(HERE/'scripts/n3_peak300_liquidation_qa.py'),run_name='__main__')
  finally: sys.argv=oldargv
  report=json.loads((peakout/'n3_peak300_liquidation_quality.json').read_text())
  assert all(x['etherscan_source_covers_entire_peak_window'] for x in report['snapshots'])
  gasout=root/'gas';oldargv=sys.argv[:]
  sys.argv=['n3_gas_freeze_evidence_inputs.py','--events',str(peakout/'n3_liquidations_peak300_events.parquet'),
            '--prices',str(root/'not_found_prices.parquet'),'--out',str(gasout)]
  try: runpy.run_path(str(HERE/'scripts/n3_gas_freeze_evidence_inputs.py'),run_name='__main__')
  finally: sys.argv=oldargv
  gasq=json.loads((gasout/'n3_gas_evidence_quality.json').read_text())
  assert gasq['verified_receipts']==0 and gasq['freeze_due_to_gas_or_profit_confirmed'] is False
  print('PASS synthetic: peak 300 block window audit & no-RPC freeze gap report')
 finally:
  pd.read_parquet=old_read;pd.DataFrame.to_parquet=old_write
