"""Cross-protocol price-spillover sensitivity, distinct from wallet funding behavior."""
import hashlib
import json
from pathlib import Path
import shutil
import pandas as pd
from scripts.n5_complete_hypotheses import setup_snapshots, run_scenario
from src.models.n5_research import compound_price_stress
from src.models.n5_comet_execution import simulate_comet


def build(root):
    paths=[Path('outputs/n5_review_corrected_20261026/compound/exposures_usd.parquet'),
           Path('outputs/compound_consolidated_A_B1_B2_20261025_complete/n3_compound_reserve_config.parquet'),
           Path('outputs/research_review_fix_20261026/comet_state/comet_state.json')]
    records=[dict(path=str(p.resolve()),sha256=hashlib.sha256(p.read_bytes()).hexdigest()) for p in paths]
    freeze=root/'compound_input_freeze.json'
    if freeze.exists() and json.loads(freeze.read_text(encoding='utf-8'))!=records:
        raise ValueError('Compound input changed after freeze')
    freeze.write_text(json.dumps(records,indent=2),encoding='utf-8')
    exposures=pd.read_parquet(paths[0]);config=pd.read_parquet(paths[1]);states=json.loads(paths[2].read_text(encoding='utf-8'))
    rows,ledger=[],[]
    for sid,snap in setup_snapshots().items():
        block=int(snap['baseline'].snapshot_block.iloc[0])
        p=exposures[exposures.snapshot_block.eq(block)]
        for shock in [.04,.05,.07,.1]:
            direct,_=run_scenario(snap,dict(depeg_shock=-shock))
            propagated,_=run_scenario(snap,dict(depeg_shock=-shock),mode='full_sensitivity')
            changes=[]
            for result in [direct,propagated]:
                changes.append({(block,r.token_address):(result.prices[r.token_address]/snap['assets'][r.token_address].price_usd-1
                    if r.token_address in result.prices else (-shock if str(r.token_symbol).upper() in ['STETH','WSTETH'] else 0.))
                    for r in p.itertuples()})
            direct_state=compound_price_stress(p,changes[0]).set_index(['market_id','wallet_address'])
            extra_state=compound_price_stress(p,changes[1]).set_index(['market_id','wallet_address'])
            for state in [s for s in states if s['snapshot_block']==block]:
                market=state['market_id']
                baseline_market=direct_state.loc[market]
                extra_market=extra_state.loc[market]
                triggered=(~baseline_market.liquidatable)&extra_market.liquidatable
                direct_flows={tx['pool_address']:tx['reference_after'] for tx in direct.transactions if tx['pool_address']}
                extra_flows={tx['pool_address']:tx['reference_after'] for tx in propagated.transactions if tx['pool_address']}
                events_d,summary_d=simulate_comet(exposures,config,state,changes[0],snap['routes'],initial_pool_volumes=direct_flows)
                events_e,summary_e=simulate_comet(exposures,config,state,changes[1],snap['routes'],initial_pool_volumes=extra_flows)
                for mode,summary,events in [('predictive_proxy',summary_d,events_d),('full_sensitivity',summary_e,events_e)]:
                    rows.append(dict(snapshot_id=sid,market_id=market,depeg_shock=-shock,feedback_mode=mode,
                        overlap_accounts=len(baseline_market),direct_price_eligible=int(baseline_market.liquidatable.sum()),
                        incremental_price_eligible=int(triggered.sum()) if mode=='full_sensitivity' else 0,
                        absorbed_accounts=summary['absorbed_accounts'],collateral_purchases=summary['collateral_purchases'],
                        initial_reserves_base=summary['initial_reserves_base'],final_reserves_base=summary['final_reserves_base'],
                        base_reserve_currency='WETH' if market.endswith('weth') else 'USDC',
                        scope='overlap_only_shared_market_price_sensitivity_not_observed_Comet_oracle_causality'))
                    ledger.extend([dict(snapshot_id=sid,market_id=market,depeg_shock=-shock,feedback_mode=mode,**e) for e in events])
        print(sid,'Compound direct/induced price sensitivity complete',flush=True)
    pd.DataFrame(rows).to_csv(root/'q3/compound_direct_induced_summary.csv',index=False)
    pd.DataFrame(ledger).to_parquet(root/'q3/compound_conditional_execution_ledger.parquet',index=False)
    versions=[]
    for p in [Path(__file__),Path('src/models/n5_comet_execution.py')]:
        sha=hashlib.sha256(p.read_bytes()).hexdigest();arch=root/'source_versions'/(sha+'.py')
        if not arch.exists():shutil.copyfile(p,arch)
        versions.append(dict(original_path=str(p),archived_path=arch.relative_to(root).as_posix(),sha256=sha))
    (root/'q3/compound_method.json').write_text(json.dumps(dict(code_versions=versions,
        assumption='common_market_price_changes_applied_to_Comet_positions;_Comet_feed_response_not_certified',
        liquidity='Comet_inventory_sales_start_from_Aave_cumulative_pool_flow;_no_pool_reset',
        direction='one_way_Aave_price_spillover_to_Comet;_Comet_resales_not_fed_back_into_Aave',
        funding_channel='not_inferred_from_price_losses_or_base_supply_balances'),indent=2),encoding='utf-8')
    if any(hashlib.sha256(Path(r['path']).read_bytes()).hexdigest()!=r['sha256'] for r in records):
        raise RuntimeError('Compound source changed during sensitivity')
