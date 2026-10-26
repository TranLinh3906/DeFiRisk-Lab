"""Integrate completed N2/N3 historical observations and export a reviewable handoff."""
import argparse
import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd

from src.models.n5_inputs import prepare_portfolio,read_rows,resolve_account_reference
from src.models.n5_oracle_validation import validate_sampled_holdout,_rates
from src.models.n5_research_protocol import peak_event_window
from src.models.n5_readiness import write_manifest,verify_manifest
from scripts.n3_depeg_b1_b2_qa import audit as audit_depeg


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--output-dir',type=Path,required=True)
    ap.add_argument('--snapshots',default='B1,B2',help='Comma-separated historical snapshots')
    args=ap.parse_args();out=args.output_dir
    labels=args.snapshots.split(',')
    if not labels or any(s not in ['B1','B2'] for s in labels):raise ValueError('Invalid historical snapshot selection')
    if out.exists():raise FileExistsError('Choose a new handoff directory')
    base=Path('outputs/n5_completion_20261026')
    root=Path('data/processed/aave');reference=resolve_account_reference(root)
    cfg_path=Path('config/snapshots.json');cfg=json.loads(cfg_path.read_text())
    names=['positions_long','reserve_config','oracle_prices','emode_membership']
    paths=[root/f'{name}.parquet' for name in names]
    raw={name:read_rows(path) for name,path in zip(names,paths)}
    supplements=base/'extended_proofs/missing_wallet_state_and_borrows.csv'
    account=pd.read_parquet(reference)
    extra=pd.read_csv(supplements)
    # Do not fill unknown snapshots from a future-state record. The six queried
    # B2 accounts are independently observed at the configured snapshot block.
    for row in extra.to_dict('records'):
        mask=account.snapshot_block.eq(row['snapshot_block'])&account.wallet_address.eq(row['wallet_address'])
        if mask.any():
            if not np.allclose(account.loc[mask,'total_debt_base_usd'],row['total_debt_base_usd'],atol=1e-8):raise ValueError('Supplement disagrees with existing account')
            account=account[~mask]
    account=pd.concat([account,extra],ignore_index=True)
    inputs=[cfg_path,reference,*paths,supplements,root/'snapshot_metadata.json',Path(__file__),
        Path('src/models/n5_oracle_validation.py'),Path('src/models/n5_inputs.py'),Path('src/models/n5_research_protocol.py'),Path('src/models/n5_readiness.py'),Path('scripts/n3_depeg_b1_b2_qa.py')]
    for sid in labels:
        inputs.extend([base/'history'/sid/name for name in ['oracle_all_active_tokens.parquet','liquidations_rpc.parquet','executed_receipts.parquet','depeg_curve_observed.parquet','log_coverage.json']])
    hashes={p:hashlib.sha256(p.read_bytes()).hexdigest() for p in inputs}
    out.mkdir(parents=True);artifacts={};report={'snapshots':{},'status':'historical_handoff_diagnostic_not_acceptance','hf_prediction_threshold':1.02}
    def save(name,d):
        d.to_parquet(out/name,index=False);artifacts[name]='observed_input_diagnostic'
    save('account_reference_supplemented.parquet',account)
    for sid in labels:
        directory=base/'history'/sid;snap=cfg['snapshots'][sid]
        p=prepare_portfolio(raw['positions_long'],raw['reserve_config'],raw['oracle_prices'],snapshot_block=snap['block'],
            emode_membership=raw['emode_membership'],depth_usd={r['token_address']:1e6 for r in raw['reserve_config']})
        positions=pd.DataFrame(p.positions)
        oracle=pd.read_parquet(directory/'oracle_all_active_tokens.parquet')
        events=pd.read_parquet(directory/'liquidations_rpc.parquet')
        if not oracle.status.eq('observed').all():raise ValueError('Invalid active token oracle prices')
        blocks=oracle.block.drop_duplicates().sort_values()
        if blocks.iloc[0]!=snap['block'] or not np.array_equal(blocks.to_numpy(),np.arange(blocks.iloc[0],blocks.iloc[-1]+1)):
            raise ValueError('Full every-block oracle path required for this handoff')
        if oracle.groupby('token_address').block.nunique().ne(len(blocks)).any():raise ValueError('Token grid has holes')
        if oracle.duplicated(['block','token_address']).any():raise ValueError('Duplicate oracle key')
        initial=oracle[oracle.block.eq(snap['block'])].set_index('token_address').price_usd
        snapshot_prices=pd.DataFrame(raw['oracle_prices'])
        snapshot_prices=snapshot_prices[snapshot_prices.snapshot_block.eq(snap['block'])].set_index('token_address').price_usd
        if not np.allclose(initial,snapshot_prices.reindex(initial.index),rtol=1e-10,atol=1e-8):raise ValueError('Oracle path start disagrees with snapshot prices')
        peak=peak_event_window(events,width=300)
        wallets,detail,validation=validate_sampled_holdout(positions,oracle,events,dict(snap,label=sid),peak)
        refs=account[account.snapshot_block.eq(snap['block'])].set_index('wallet_address')
        wallets['initial_state_known']=wallets.wallet_address.isin(refs.index)
        wallets['initial_reference_debt_usd']=wallets.wallet_address.map(refs.total_debt_base_usd)
        wallets['initial_debt_usd']=wallets.initial_reference_debt_usd
        wallets['initial_cohort_eligible']=wallets.initial_reference_debt_usd.ge(100)
        wallets['classification']=np.where(wallets.initial_reference_debt_usd.eq(0),'no_initial_debt_position_opened_after_snapshot',
            np.where(wallets.initial_cohort_eligible,'initial_debt_ge_100','dust_or_unknown_state'))
        rates={}
        for window,g in wallets.groupby('window'):
            raw_rates=_rates(g)
            raw_rates['missing_state_wallets']=int((~g.initial_state_known).sum())
            raw_rates['no_initial_debt_wallets']=int(g.initial_reference_debt_usd.eq(0).sum())
            eligible=g[g.initial_cohort_eligible]
            initial_rates=_rates(eligible)
            retrospective=eligible.copy();retrospective['hit']=retrospective.retrospective_coherent_trough_hit
            retrospective['min_hf_before_event']=retrospective.retrospective_coherent_trough_hf
            rates[window]=dict(all_observed_wallets=raw_rates,initial_cohort_debt_ge_100=initial_rates,
                retrospective_coherent_trough_initial_cohort=_rates(retrospective),
                scope='Raw observed events retained; verified zero-initial-debt and dust wallets are outside initial research cohort, not silently dropped')
            for key in ['all_observed_wallets','initial_cohort_debt_ge_100','retrospective_coherent_trough_initial_cohort']:
                rates[window][key]['freeze_status']='not_classified_executed_receipts_do_not_prove_counterfactual_freeze'
            rates[window]['initial_cohort_wallet_target_85_met']=initial_rates['wallet_hit_rate'] is not None and initial_rates['wallet_hit_rate']>=.85
            rates[window]['initial_cohort_debt_weighted_target_85_met']=initial_rates['debt_weighted_hit_rate'] is not None and initial_rates['debt_weighted_hit_rate']>=.85
        save(f'{sid}_oracle_wallet_evidence.parquet',wallets)
        save(f'{sid}_oracle_event_evidence.parquet',detail)
        receipts=pd.read_parquet(directory/'executed_receipts.parquet')
        receipts=receipts.merge(events[['tx_hash','event_timestamp']].drop_duplicates('tx_hash'),on='tx_hash',validate='one_to_one')
        # Convert whole-transaction gas once, using the strictly prior observed
        # oracle ETH quote. Do not charge the whole gas cost to every event.
        eth=oracle[oracle.symbol.str.upper().eq('WETH')][['block','price_usd','block_timestamp_utc']].sort_values('block')
        receipts=pd.merge_asof(receipts.sort_values('block_number'),eth,left_on='block_number',right_on='block',direction='backward',allow_exact_matches=False)
        if receipts.price_usd.isna().any():raise ValueError('Missing pre-tx ETH quote')
        receipts['gas_cost_usd_oracle']=receipts.gas_cost_eth*receipts.price_usd
        receipts['equal_allocated_gas_usd_per_liquidation']=receipts.gas_cost_usd_oracle/receipts.liquidation_events
        receipts['valuation_scope']='whole tx gas; prior Aave oracle USD; equal event allocation is a diagnostic, not measured liquidator PnL'
        save(f'{sid}_actual_gas_costs.parquet',receipts)
        ratios=pd.read_parquet(directory/'depeg_curve_observed.parquet').rename(columns={'timestamp_hour_utc':'timestamp_utc','steth_eth_market_ratio':'market_ratio'})
        ratios['source']='Curve archive fee-adjusted tiny-trade market estimate'
        ratios['provenance_check']='MARKET_SOURCE_CANDIDATE';ratios['depeg_pct']=(ratios.market_ratio-1)*100
        meta={'snapshots':{label:{'snapshot_block':s['block'],'snapshot_timestamp_utc':s['block_timestamp_utc']} for label,s in cfg['snapshots'].items()}}
        grid,depeg_reports=audit_depeg(ratios,meta,window_hours=24 if sid=='B1' else 48)
        grid=grid[grid.snapshot_id.eq(sid)]
        save(f'{sid}_depeg_hourly_qa.parquet',grid)
        depeg=next(r for r in depeg_reports if r['snapshot_id']==sid)
        coverage=json.loads((directory/'log_coverage.json').read_text())
        report['snapshots'][sid]=dict(oracle_blocks=len(blocks),oracle_tokens=oracle.token_address.nunique(),oracle_rows=len(oracle),
            independent_log_coverage=coverage,peak=peak,rates=rates,depeg_qa=depeg,
            actual_receipts=len(receipts),gas_usd_oracle_total=float(receipts.gas_cost_usd_oracle.sum()),
            oracle_scope='Direct Aave getAssetPrice observed every block, not reconstructed from market prices',
            execution_limits='Strictly earlier block prediction; same-block update ordering not inferred; frozen t0 quantities ignore borrower changes; market depeg is separate, not multiplied onto already observed oracle prices',
            freeze_classification='not_proven_from_executed_receipts')
    (out/'summary.json').write_text(json.dumps(report,indent=2,allow_nan=False))
    artifacts['summary.json']='historical_validation_status'
    if any(hashlib.sha256(p.read_bytes()).hexdigest()!=h for p,h in hashes.items()):raise ValueError('Input changed during audit')
    write_manifest(out,inputs,artifacts,report)
    if verify_manifest(out):raise ValueError('Manifest mismatch')
    print(json.dumps({sid:r['rates']['primary_peak_300']['initial_cohort_debt_ge_100'] for sid,r in report['snapshots'].items()},indent=2))


if __name__=='__main__':main()
