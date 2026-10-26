"""Apply the user-selected N4/N5 input policy, preserving sources and old gates."""
import argparse
import hashlib
import json
from pathlib import Path

import pandas as pd

from src.models.n5_inputs import read_rows,prepare_portfolio,resolve_account_reference
from src.models.n5_readiness import reconcile_debt,write_manifest,verify_manifest
from src.models.research_tolerances import load_tolerances,evaluate_research_gate
from src.models.stress_test import run_stress_test,load_scenarios


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--output-dir',type=Path,required=True)
    ap.add_argument('--run-stress',action='store_true',help='Run configured N4 scenarios after the input gate passes')
    args=ap.parse_args()
    if args.output_dir.exists():raise FileExistsError('Choose a new output directory')
    root=Path('data/processed/aave')
    reference_path=resolve_account_reference(root)
    names=['positions_long','reserve_config','oracle_prices','emode_membership','account_reference','protocol_reconciliation']
    inputs=[reference_path if n=='account_reference' else root/f'{n}.parquet' for n in names]+[root/'snapshot_metadata.json',
        Path('data/marts/borrower_risk.parquet'),Path('config/n5_tolerances.json'),Path(__file__),
        *[Path('src/models')/n for n in ['research_tolerances.py','stress_test.py','n5_inputs.py','n5_readiness.py','cascade.py']]]
    if args.run_stress:inputs.append(Path('config/scenarios.yaml'))
    hashes={p:hashlib.sha256(p.read_bytes()).hexdigest() for p in inputs}
    raw={n:read_rows(reference_path if n=='account_reference' else root/f'{n}.parquet') for n in names}
    metadata=json.loads((root/'snapshot_metadata.json').read_text(encoding='utf-8'))
    risk=pd.read_parquet('data/marts/borrower_risk.parquet');policy=load_tolerances()
    args.output_dir.mkdir(parents=True)
    artifacts={};summary=dict(tolerance_policy=policy,snapshots={},research_acceptance='pending_other_gates',account_reference_source=str(reference_path))
    scenarios=load_scenarios()['scenarios'] if args.run_stress else {}
    for label,snap in metadata['snapshots'].items():
        portfolio=prepare_portfolio(raw['positions_long'],raw['reserve_config'],raw['oracle_prices'],
            snapshot_block=snap['snapshot_block'],emode_membership=raw['emode_membership'],
            depth_usd={r['token_address']:1e6 for r in raw['reserve_config']})
        _,totals=reconcile_debt(portfolio,raw['protocol_reconciliation'])
        p=pd.DataFrame(portfolio.positions)
        keys=['snapshot_block','protocol_id','wallet_address']
        p=p.merge(risk[keys+['is_recursive_loop']],on=keys,how='left',validate='many_to_one')
        if p.is_recursive_loop.isna().any():raise ValueError('Missing N4 looping classification')
        selected,cohort,checks,gate=evaluate_research_gate(p,pd.DataFrame(raw['account_reference']),totals,policy,n4_risk=risk)
        summary['snapshots'][label]=gate
        directory=args.output_dir/label;directory.mkdir()
        for name,frame,kind in [('simulation_positions.parquet',selected,'simulation_input_candidate'),
            ('wallet_cohort.parquet',cohort,'diagnostic'),('wallet_checks.parquet',checks,'diagnostic')]:
            frame.to_parquet(directory/name,index=False);artifacts[f'{label}/{name}']=kind
        if gate['status']=='passed':
            # Zero shock verifies the filtered N4 input path, not liquidity/execution.
            zero=run_stress_test(p,{'scenario_id':'research_zero'},tolerances=policy,
                account_reference=pd.DataFrame(raw['account_reference']))
            assert len(zero)==gate['included_wallets']
            zero.to_parquet(directory/'zero_shock_filtered.parquet',index=False)
            artifacts[f'{label}/zero_shock_filtered.parquet']='diagnostic'
            if args.run_stress:
                result_parts=[];scenario_rows=[]
                for scenario_id,params in scenarios.items():
                    result=run_stress_test(selected,dict(params or {},scenario_id=scenario_id))
                    if len(result)!=gate['included_wallets']:raise ValueError('Scenario changed wallet cohort')
                    result_parts.append(result)
                    scenario_rows.append(dict(snapshot_block=snap['snapshot_block'],scenario_id=scenario_id,
                        wallets=len(result),positions_at_risk=int(result.hf_after.lt(1).sum()),
                        debt_at_risk_usd=float(result.debt_at_risk_usd.sum()),
                        total_debt_usd=float(result.debt_usd.sum()),validation_status='unvalidated_research_input_gate_passed'))
                pd.concat(result_parts,ignore_index=True).to_parquet(directory/'stress_positions_filtered.parquet',index=False)
                pd.DataFrame(scenario_rows).to_parquet(directory/'stress_summary_filtered.parquet',index=False)
                artifacts[f'{label}/stress_positions_filtered.parquet']='observed_input_counterfactual_candidate'
                artifacts[f'{label}/stress_summary_filtered.parquet']='observed_input_counterfactual_candidate'
                summary['snapshots'][label]['stress_scenarios_run']=len(scenarios)
        (directory/'validation.json').write_text(json.dumps(gate,indent=2,ensure_ascii=False,allow_nan=False),encoding='utf-8')
        artifacts[f'{label}/validation.json']='diagnostic'
    summary['input_gate_status']='passed' if all(v['status']=='passed' for v in summary['snapshots'].values()) else 'failed'
    (args.output_dir/'summary.json').write_text(json.dumps(summary,indent=2,ensure_ascii=False,allow_nan=False),encoding='utf-8')
    artifacts['summary.json']='diagnostic'
    if any(hashlib.sha256(p.read_bytes()).hexdigest()!=h for p,h in hashes.items()):raise ValueError('Source changed')
    write_manifest(args.output_dir,inputs,artifacts,summary)
    if verify_manifest(args.output_dir):raise ValueError('Manifest mismatch')
    print(json.dumps(summary,indent=2,ensure_ascii=False,allow_nan=False))
    return 0 if summary['input_gate_status']=='passed' else 1


if __name__=='__main__':raise SystemExit(main())
