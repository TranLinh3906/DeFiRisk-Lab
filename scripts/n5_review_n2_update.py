"""Audit N2 update and run sampled-oracle diagnostics into a NEW bundle, offline."""
import argparse
import hashlib
import json
from pathlib import Path

import pandas as pd

from scripts.n5_audit import finite_json
from src.models.n5_inputs import prepare_portfolio,read_rows,resolve_account_reference
from src.models.n5_snapshots import select_event_windows
from src.models.n5_research_protocol import validate_protocol_decisions,peak_event_window
from src.models.n5_oracle_validation import audit_oracle_path,validate_sampled_holdout
from src.models.n5_readiness import write_manifest,verify_manifest


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--output-dir',type=Path,required=True)
    args=ap.parse_args()
    if args.output_dir.exists():raise FileExistsError('Choose a new output directory')
    aave=Path('data/processed/aave')
    reference_path=resolve_account_reference(aave)
    names=['positions_long','reserve_config','oracle_prices','emode_membership','account_reference']
    inputs=[reference_path if n=='account_reference' else aave/f'{n}.parquet' for n in names]
    inputs += [aave/'oracle_price_path.parquet',aave/'oracle_price_path_summary.json',aave/'dust_diagnosis.json',
        Path('config/snapshots.json'),Path('config/n5_research_protocol.json'),
        Path('data/n3_history_2024_2026/final/n3_liquidations_observed.parquet'),Path(__file__),
        Path('src/models/n5_oracle_validation.py'),Path('src/models/n5_inputs.py'),
        Path('src/models/n5_snapshots.py'),Path('src/models/n5_research_protocol.py'),
        Path('src/data/fetch_oracle_path.py'),Path('src/data/diagnose_dust_mismatch.py')]
    hashes={p:hashlib.sha256(p.read_bytes()).hexdigest() for p in inputs}
    config=json.loads(Path('config/snapshots.json').read_text(encoding='utf-8'))
    protocol=json.loads(Path('config/n5_research_protocol.json').read_text(encoding='utf-8'))
    holdout=validate_protocol_decisions(protocol,config)
    raw={n:read_rows(reference_path if n=='account_reference' else aave/f'{n}.parquet') for n in names}
    path,path_qa=audit_oracle_path(pd.read_parquet(aave/'oracle_price_path.parquet'),
        json.loads((aave/'oracle_price_path_summary.json').read_text(encoding='utf-8')),config,pd.DataFrame(raw['oracle_prices']))
    diagnosis=json.loads((aave/'dust_diagnosis.json').read_text(encoding='utf-8'))
    # Use an explicit path rather than relying on input-list ordering.
    events,window_qa=select_event_windows(pd.read_parquet('data/n3_history_2024_2026/final/n3_liquidations_observed.parquet'),config)
    args.output_dir.mkdir(parents=True)
    artifacts={};summary=dict(status='diagnostic_unvalidated',oracle_path_qa=path_qa,account_reference_source=str(reference_path),
        dust_policy_status='N2_proposal_not_automatically_approved',dust={},holdout={},
        remaining=['AnswerUpdated feed completeness/provenance','separate aligned depeg history',
            'independent liquidation-event coverage','gas/profit evidence for freeze classification'])
    def write_json(name,value):
        (args.output_dir/name).write_text(json.dumps(finite_json(value),ensure_ascii=False,indent=2,allow_nan=False),encoding='utf-8')
        artifacts[name]='diagnostic'
    def write_table(name,value):
        value.to_parquet(args.output_dir/name,index=False);artifacts[name]='diagnostic_unvalidated'
    for label,snap in config['snapshots'].items():
        block=snap['block']
        portfolio=prepare_portfolio(raw['positions_long'],raw['reserve_config'],raw['oracle_prices'],
            snapshot_block=block,emode_membership=raw['emode_membership'],
            depth_usd={r['token_address']:1e6 for r in raw['reserve_config']})
        p=pd.DataFrame(portfolio.positions)
        p['debt_usd']=p.debt_amount*p.price_usd
        p['collateral_usd']=p.collateral_amount*p.price_usd*p.is_collateral_enabled
        balances=p.groupby('wallet_address')[['debt_usd','collateral_usd']].sum().reset_index()
        refs=pd.DataFrame(raw['account_reference']);refs=refs[refs.snapshot_block.eq(block)]
        balances=balances.merge(refs[['wallet_address','total_debt_base_usd','total_collateral_base_usd']],on='wallet_address',validate='one_to_one')
        combined={}
        for kind in ['debt','collateral']:
            delta=(balances[f'{kind}_usd']-balances[f'total_{kind}_base_usd']).abs()
            ref=balances[f'total_{kind}_base_usd'].abs()
            balances[f'{kind}_abs_error_usd']=delta
            balances[f'{kind}_strict_failed']=delta.gt(.01*ref)
            balances[f'{kind}_proposed_failed']=delta.ge(.01*ref)&delta.gt(.01)
            combined[kind]=dict(strict_failed=int(balances[f'{kind}_strict_failed'].sum()),
                proposed_failed=int(balances[f'{kind}_proposed_failed'].sum()),
                proposed_rule='relative error <1% OR absolute error <=0.01 USD')
        tested=diagnosis[label]['part2'];variant=tested['best_variant']
        combined['N2_integer_reconstruction']=dict(wallets_tested=tested['wallets_tested'],
            variant=variant,reported_exact_matches=tested[variant]['exact_match'],
            scope='N2 code limits reference debt <10 million USD and raw scaled availability; not all borrowers',
            independent_raw_integer_replay=False)
        summary['dust'][label]=combined
        write_table(f'{label}_balance_policy_diagnostics.parquet',balances)
        if snap.get('window_end_exclusive_utc'):
            selected=events[events.validation_snapshot_id.eq(label)]
            peak=peak_event_window(selected,width=holdout['peak_window_blocks'])
            wallets,details,result=validate_sampled_holdout(p,path,events,dict(snap,label=label),peak)
            summary['holdout'][label]=result
            write_table(f'{label}_oracle_wallet_evidence.parquet',wallets)
            write_table(f'{label}_oracle_event_evidence.parquet',details)
            write_json(f'{label}_sampled_oracle_validation.json',result)
    write_json('oracle_path_qa.json',path_qa)
    write_json('summary.json',summary)
    for p,digest in hashes.items():
        if hashlib.sha256(p.read_bytes()).hexdigest()!=digest:raise ValueError(f'Source changed: {p}')
    write_manifest(args.output_dir,inputs,artifacts,summary)
    if verify_manifest(args.output_dir):raise ValueError('Manifest verification failed')
    print(json.dumps(finite_json(summary),ensure_ascii=False,indent=2,allow_nan=False))


if __name__=='__main__':main()
