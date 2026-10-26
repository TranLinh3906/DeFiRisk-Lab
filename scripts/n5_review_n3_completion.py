"""Review N3 depeg/coverage/gas additions; do not certify unsupported freezes."""
import argparse
import ast
import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd

from src.data.n3_price_timeseries import compute_depeg_metrics,integrate
from src.data.n3_gas_profit_freeze import analyze_gas_profit_freeze
from src.data.finalize_liquidations import finalize
from src.models.n5_readiness import write_manifest,verify_manifest


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--output-dir',type=Path,required=True)
    args=ap.parse_args()
    if args.output_dir.exists():raise FileExistsError('Choose a new output directory')
    prices_path=Path('data/staging/n3_price_timeseries.parquet')
    proof_path=Path('outputs/tables/n3_gas_profit_freeze_proof.parquet')
    n2=Path('data/processed/aave')
    events_path=Path('data/n3_history_2024_2026/final/n3_liquidations_observed.parquet')
    priced_path=Path('data/staging/n3_liquidations_priced.parquet')
    sources=[Path(p) for p in ['src/data/n3_price_timeseries.py','src/data/n3_gas_profit_freeze.py',
        'src/data/finalize_liquidations.py','scripts/n3_priority3_price_qa.py',__file__]]
    inputs=[prices_path,proof_path,events_path,priced_path,n2/'oracle_prices.parquet',n2/'lido_ratios.parquet',
        n2/'token_registry.csv',n2/'snapshot_metadata.json',Path('config/snapshots.json'),
        Path('outputs/tables/n3_gas_profit_freeze_summary.json'),*sources,Path('src/models/n5_readiness.py')]
    hashes={p:hashlib.sha256(p.read_bytes()).hexdigest() for p in inputs}
    for source in sources:ast.parse(source.read_text(encoding='utf-8'))
    prices=pd.read_parquet(prices_path)
    ratios=prices[prices.price_type.eq('market_ratio')&prices.asset.eq('STETH')&prices.quote_asset.eq('ETH')]
    expected=compute_depeg_metrics(ratios)
    supplied=prices[prices.price_type.eq('depeg_metric')]
    keys=['timestamp','asset','quote_asset','reference_timestamp']
    if supplied.duplicated(keys).any():raise ValueError('Duplicate depeg metric')
    compare=expected[keys+['price_value']].merge(supplied[keys+['price_value']],on=keys,
        how='outer',validate='one_to_one',suffixes=('_recomputed','_supplied'),indicator=True)
    if not compare['_merge'].eq('both').all() or not np.allclose(compare.price_value_recomputed,compare.price_value_supplied,atol=1e-10):
        raise ValueError('Depeg percentage differs from supplied STETH/ETH ratio')
    config=json.loads(Path('config/snapshots.json').read_text(encoding='utf-8'))
    times=pd.to_datetime(supplied.timestamp,utc=True)
    scope={}
    for label,snap in config['snapshots'].items():
        if not snap.get('window_end_exclusive_utc'):continue
        start=pd.Timestamp(snap['block_timestamp_utc']);end=pd.Timestamp(snap['window_end_exclusive_utc'])
        scope[label]=dict(depeg_rows_in_holdout=int((times.gt(start)&times.lt(end)).sum()),
            status='no_holdout_depeg_rows' if not (times.gt(start)&times.lt(end)).any() else 'observations_present')
    rebuilt,price_report=integrate(prices,pd.read_parquet(n2/'oracle_prices.parquet'),pd.read_parquet(n2/'lido_ratios.parquet'),
        pd.read_csv(n2/'token_registry.csv'),json.loads((n2/'snapshot_metadata.json').read_text(encoding='utf-8')))
    proof=pd.read_parquet(proof_path)
    reviewed,gas_summary=analyze_gas_profit_freeze(proof.drop(columns=[c for c in ['is_frozen_due_to_gas'] if c in proof]))
    original_summary=json.loads(Path('outputs/tables/n3_gas_profit_freeze_summary.json').read_text(encoding='utf-8'))
    assumptions=original_summary['assumptions']
    gas_cost=assumptions['avg_gas_units_per_liquidation']*assumptions['assumed_gas_price_gwei']*1e-9*assumptions['assumed_eth_price_usd']
    if not np.allclose(proof.estimated_gas_cost_usd,gas_cost):raise ValueError('Gas artifact does not match declared assumptions')
    historical=pd.read_parquet(events_path)
    history_keys=set(historical[['tx_hash','log_index']].itertuples(index=False,name=None))
    overlap=set(proof[['tx_hash','log_index']].itertuples(index=False,name=None))&history_keys
    all_events,fact,valuation=finalize(pd.read_parquet(priced_path))
    report=dict(status='partial_completion_verified',depeg=dict(rows=len(supplied),formula_verified=True,
        start=str(times.min()),end=str(times.max()),unit='percentage_points_relative_to_parity_1',holdout_scope=scope),
        gas=dict(original_flagged_frozen_events=int(proof.is_frozen_due_to_gas.sum()),
            original_events=len(proof),start=str(proof.event_timestamp.min()),end=str(proof.event_timestamp.max()),
            assumed_gas_cost_usd=gas_cost,observed_holdout_event_key_overlap=len(overlap),
            reviewed_assumptions=gas_summary,
            acceptance='not_freeze_proof; observed executed events cannot demonstrate nonexecution'),
        valuation_coverage=valuation,
        source_repairs=['Restored complete finalizer after upstream IndentationError; pricing coverage is not chain-log completeness',
            'Allow signed depeg metrics in QA, retain positive-only constraints for actual prices/ratios',
            'Gas estimate marked assumption-only with nullable freeze labels; unknown monetary inputs rejected',
            'Price integration guards restored and quality-output/overwrite backup honored'],
        remaining=['B1/B2-aligned depeg series','Independent on-chain log collection completeness evidence',
            'Actual transaction gas/effective price, ETH quote, execution costs and eligible-but-unexecuted positions',
            'Missing oracle token/feed history and full Q1/Q3 simulation gates'])
    args.output_dir.mkdir(parents=True)
    artifacts={}
    for name,frame in [('depeg_metrics_verified.parquet',supplied),('prices_rebuilt_candidate.parquet',rebuilt),
        ('gas_assumption_review.parquet',reviewed),('valuation_fact_candidate.parquet',fact)]:
        frame.to_parquet(args.output_dir/name,index=False);artifacts[name]='diagnostic_unvalidated'
    for name,value in [('summary.json',report),('price_integration_qa.json',price_report)]:
        (args.output_dir/name).write_text(json.dumps(value,ensure_ascii=False,indent=2,allow_nan=False),encoding='utf-8');artifacts[name]='diagnostic'
    if any(hashlib.sha256(p.read_bytes()).hexdigest()!=h for p,h in hashes.items()):raise ValueError('Source changed')
    write_manifest(args.output_dir,inputs,artifacts,report)
    if verify_manifest(args.output_dir):raise ValueError('Manifest mismatch')
    print(json.dumps(report,ensure_ascii=False,indent=2,allow_nan=False))


if __name__=='__main__':main()
