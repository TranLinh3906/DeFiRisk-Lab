"""Prepare approved numeric assumptions; oracle validation remains blocked on inputs."""
import argparse
import hashlib
import json
from pathlib import Path

import pandas as pd

from src.models.n5_research_protocol import (calibration_returns, calibrated_scenarios,
    peak_event_window, validate_protocol_decisions)
from src.models.n5_snapshots import select_event_windows
from src.models.n5_readiness import write_manifest, verify_manifest


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir',type=Path,required=True)
    parser.add_argument('--protocol-config',type=Path,default=Path('config/n5_research_protocol.json'))
    args=parser.parse_args()
    if args.output_dir.exists():
        raise FileExistsError('Choose a new output directory')
    reference=Path('data/n3_history_2024_2026/final/n3_daily_steth_eth_reference.csv')
    config_path=Path('config/snapshots.json')
    events_path=Path('data/n3_history_2024_2026/final/n3_liquidations_observed.parquet')
    inputs=[reference,config_path,args.protocol_config,events_path,Path(__file__),Path('src/models/n5_research_protocol.py'),Path('src/models/n5_research.py')]
    hashes={p:hashlib.sha256(p.read_bytes()).hexdigest() for p in inputs}
    config=json.loads(config_path.read_text(encoding='utf-8'))
    protocol=json.loads(args.protocol_config.read_text(encoding='utf-8'))
    holdout=validate_protocol_decisions(protocol,config)
    returns=calibration_returns(pd.read_csv(reference),config['snapshots']['A']['block_timestamp_utc'])
    scenarios,estimation=calibrated_scenarios(returns)
    windows,reports=select_event_windows(pd.read_parquet(events_path),config)
    peak={label:peak_event_window(windows[windows.validation_snapshot_id.eq(label)],
        width=holdout['peak_window_blocks']) for label in reports}
    selected_parts=[]
    for label, selection in peak.items():
        selection['role']='primary'
        selection['rule_status']='user_confirmed'
        selection['source_completeness']='pending_independent_verification'
        if selection['start_block'] is not None:
            part=windows[windows.validation_snapshot_id.eq(label) & windows.event_block.between(
                selection['start_block'],selection['end_block'])].copy()
            part['window_role']='primary_peak_300_blocks'
            selected_parts.append(part)
    selected=pd.concat(selected_parts,ignore_index=True) if selected_parts else windows.iloc[:0].copy()
    summary=dict(status='prepared_not_simulated',estimation=estimation,
        calibration_first_return=str(returns.date.min()),calibration_last_return=str(returns.date.max()),
        calibration_source='Daily market reference, asynchronous STETH/USDT quotes; pending N3 confirmation',
        peak_300_block_candidates=peak,
        full_event_windows=reports,
        confirmed_decisions=protocol,
        secondary_window=dict(selection=holdout['secondary_peak_selection'],
            eth_return_threshold=holdout['secondary_eth_return_threshold'],
            status='blocked_missing_chainlink_history'),
        decisions_pending=['Daily close timing/source confirmation by N3; DebtRank node capacities/topology'],
        missing_inputs=['Chainlink AnswerUpdated history and feed/proxy/decimals provenance for holdout',
            'Timestamp/block-aligned stETH/ETH depeg history during holdout',
            'Evidence distinguishing gas/liquidator freezes from prediction failures'],
        rules=dict(min_debt_usd=100,mc_samples=10000,seed=42,df=4,equity_normalization_usd=100000,
            es99_tail_scenarios=100,looping='category1 + enabled LST collateral + WETH debt + leverage>=3',
            collateral_priority='max(liquidation_bonus - DEX_slippage)',
            hit_rate='report wallet count AND t0 debt-weighted; do not remove misses without evidence'),
        output_simulation_results='NOT_GENERATED: scenarios are inputs, not simulated losses or acceptance')
    args.output_dir.mkdir(parents=True)
    returns.to_parquet(args.output_dir/'calibration_returns.parquet',index=False)
    scenarios.to_parquet(args.output_dir/'mc_scenarios_candidate.parquet',index=False)
    selected.to_parquet(args.output_dir/'primary_peak_300_block_events.parquet',index=False)
    (args.output_dir/'readiness.json').write_text(json.dumps(summary,indent=2,ensure_ascii=False,allow_nan=False),encoding='utf-8')
    if any(hashlib.sha256(p.read_bytes()).hexdigest()!=digest for p,digest in hashes.items()):
        raise ValueError('Source changed during preparation')
    artifacts={'calibration_returns.parquet':'calibration_candidate','mc_scenarios_candidate.parquet':'scenario_input_candidate',
        'primary_peak_300_block_events.parquet':'observed_window_selection','readiness.json':'diagnostic'}
    write_manifest(args.output_dir,inputs,artifacts,summary)
    assert not verify_manifest(args.output_dir)
    print(json.dumps(summary,ensure_ascii=False,indent=2))


if __name__=='__main__': main()
