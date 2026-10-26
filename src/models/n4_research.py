"""Shared gated N4 research cohort and non-overwriting candidate publication."""
import argparse
import hashlib
import json
from pathlib import Path

import pandas as pd
import yaml

from src.models.n5_inputs import prepare_portfolio,read_rows,resolve_account_reference
from src.models.n5_readiness import reconcile_debt,write_manifest,verify_manifest
from src.models.research_tolerances import evaluate_research_gate,load_tolerances
from src.models.health_factor import calculate_health_factor,build_overview

ROOT=Path(__file__).resolve().parents[2]


def load_research_inputs():
    root=ROOT/'data/processed/aave'
    reference=resolve_account_reference(root)
    names=['positions_long','reserve_config','oracle_prices','emode_membership','protocol_reconciliation']
    paths=[root/f'{name}.parquet' for name in names]
    raw={name:read_rows(path) for name,path in zip(names,paths)}
    refs=pd.read_parquet(reference)
    snapshots=json.loads((ROOT/'config/snapshots.json').read_text())['snapshots']
    membership=pd.DataFrame(raw['emode_membership']).rename(columns={'emode_lt_bps':'lt_emode'})
    cfg=pd.concat([pd.DataFrame(raw['reserve_config']),membership],ignore_index=True,sort=False)
    n4risk=calculate_health_factor(pd.DataFrame(raw['positions_long']),cfg,pd.DataFrame(raw['oracle_prices']))
    positions=[];risks=[];gates={};cohorts=[];checks=[]
    for sid,snap in snapshots.items():
        portfolio=prepare_portfolio(raw['positions_long'],raw['reserve_config'],raw['oracle_prices'],
            snapshot_block=snap['block'],emode_membership=raw['emode_membership'],
            depth_usd={r['token_address']:1e6 for r in raw['reserve_config']})
        _,totals=reconcile_debt(portfolio,raw['protocol_reconciliation'])
        selected,cohort,check,gate=evaluate_research_gate(pd.DataFrame(portfolio.positions),refs,totals,
            load_tolerances(),n4_risk=n4risk)
        if gate['status']!='passed':raise ValueError(f'N4 research gate failed: {sid}: {gate}')
        keys=['snapshot_block','protocol_id','wallet_address']
        risk=n4risk.merge(selected[keys].drop_duplicates(),on=keys,validate='one_to_one')
        selected=selected.merge(risk[keys+['health_factor','is_recursive_loop']],on=keys,validate='many_to_one')
        gates[sid]=gate;positions.append(selected);risks.append(risk);cohorts.append(cohort);checks.append(check)
    return (pd.concat(positions,ignore_index=True),pd.concat(risks,ignore_index=True),gates,
        pd.concat(cohorts,ignore_index=True),pd.concat(checks,ignore_index=True),[*paths,reference])


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--output-dir',type=Path,default=ROOT/'outputs/n4_research_AB1B2_20261026')
    a=ap.parse_args();out=a.output_dir
    if out.exists():raise FileExistsError('Choose a new research output directory')
    from src.models.stress_test import run_stress_test,generate_stress_heatmap,_summary_row
    from src.models.cliff_reverse import find_liquidation_cliff,run_reverse_stress
    policy_path=ROOT/'config/n4_research.json'
    policy=json.loads(policy_path.read_text())
    positions,risk,gates,cohort,checks,inputs=load_research_inputs()
    sources=[Path(__file__),*[ROOT/'src/models'/n for n in ['health_factor.py','stress_test.py','cliff_reverse.py',
        'n5_inputs.py','n5_readiness.py','research_tolerances.py']]]
    inputs += [policy_path,ROOT/'config/scenarios.yaml',ROOT/'config/snapshots.json',ROOT/'config/n5_tolerances.json',*sources]
    hashes={p:hashlib.sha256(p.read_bytes()).hexdigest() for p in inputs}
    scenarios=yaml.safe_load((ROOT/'config/scenarios.yaml').read_text())
    frames=[];summaries=[]
    for sid,params in scenarios['scenarios'].items():
        params=dict(params or {},**policy['scenario_overrides'])
        result=run_stress_test(positions,dict(params,scenario_id=sid))
        frames.append(result)
        for _,group in result.groupby('snapshot_block'):
            summaries.append(_summary_row(sid,params,group))
    heat=generate_stress_heatmap(positions,scenarios['heatmap']['eth_steps'],scenarios['heatmap']['depeg_steps'],scenario_params=policy['scenario_overrides'])
    cliff=find_liquidation_cliff(positions,scenario_params=policy['scenario_overrides'])
    reverse=run_reverse_stress(positions,policy['reverse_targets'],scenario_params=policy['scenario_overrides'])
    # Keep deprecated protocol-named API aliases out of the new research marts.
    cliff=cliff.drop(columns=['total_protocol_debt_usd']).rename(columns={'debt_at_risk_pct':'debt_at_risk_pct_of_cohort_after_shock'})
    totals={v['snapshot_block']:v['gates']['protocol_debt']['protocol_debt_usd'] for v in gates.values()}
    cliff['protocol_debt_t0_usd']=cliff.snapshot_block.map(totals)
    revrows=[]
    for block,item in reverse['snapshots'].items():
        for target in item['targets']:
            target={k:v for k,v in target.items() if k!='target_debt_pct_of_protocol'}
            revrows.append(dict(snapshot_block=int(block),baseline_cohort_debt_usd=item['baseline_cohort_debt_usd'],
                protocol_debt_t0_usd=totals[int(block)],**target))
    out.mkdir(parents=True);artifacts={}
    for name,d in [('simulation_positions',positions),('borrower_risk',risk),('wallet_cohort',cohort),('wallet_checks',checks),
        ('stress_positions',pd.concat(frames,ignore_index=True)),('stress_summary',pd.DataFrame(summaries)),
        ('stress_heatmap',heat),('cliff_curve',cliff),('reverse_stress',pd.DataFrame(revrows))]:
        d.to_parquet(out/f'{name}.parquet',index=False);artifacts[f'{name}.parquet']='gated_research_candidate'
    report=dict(status='N4_research_candidates_complete',gates=gates,scenario_policy=policy,
        rows=dict(wallets=len(risk),positions=len(positions),stress_scenarios=len(summaries),heatmap=len(heat),cliff=len(cliff),reverse=len(revrows)),
        limitations='Versioned liquidation close factors and protocol liquidation execution are research assumptions, not certified contract emulation. No official cascade or Monte Carlo acceptance.')
    for name,value in [('summary.json',report),('overview_metrics.json',build_overview(risk)),('reverse_stress.json',reverse)]:
        (out/name).write_text(json.dumps(value,indent=2,allow_nan=False),encoding='utf-8');artifacts[name]='research_candidate_metadata'
    if any(hashlib.sha256(p.read_bytes()).hexdigest()!=h for p,h in hashes.items()):raise ValueError('Input changed')
    write_manifest(out,inputs,artifacts,report)
    if verify_manifest(out):raise ValueError('Manifest mismatch')
    print(json.dumps(dict(status=report['status'],rows=report['rows']),indent=2))


if __name__=='__main__':main()
