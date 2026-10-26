"""Audit N3 additions, fit price-impact candidates, preserve all original files."""
import argparse
import hashlib
import json
from pathlib import Path

import pandas as pd

from src.models.n5_inputs import resolve_account_reference
from src.models.n5_n3_update import calibrate_quote_grid,audit_market_coverage
from src.models.n5_readiness import write_manifest,verify_manifest


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--output-dir',type=Path,required=True)
    args=ap.parse_args()
    if args.output_dir.exists():raise FileExistsError('Choose a new output directory')
    root=Path('outputs/n3_priority2_quote_grid_v1')
    quote_path=root/'n3_multivolume_rpc_verified_only.parquet'
    anchor_path=root/'n3_2pct_observed_anchors.parquet'
    depth_path=Path('data/staging/n3_dex_liquidity_A_B1_B2.parquet')
    config_path=Path('config/snapshots.json')
    recovered=Path('outputs/n3_b1_recovered_priced_quotes_20261025/market_reference_from_historical_priced.parquet')
    events_path=Path('data/n3_history_2024_2026/final/n3_liquidations_observed.parquet')
    returns_path=Path('outputs/n3_priority3_price_qc_v2/n3_market_returns_N5_no_lookahead.parquet')
    reference=resolve_account_reference('data/processed/aave')
    coverage_path=Path('outputs/compound_consolidated_A_B1_B2_20261025_complete/query_coverage.parquet')
    inputs=[quote_path,anchor_path,depth_path,config_path,recovered,events_path,returns_path,reference,coverage_path,
        root/'n3_priority2_quality.json',Path('data/processed/aave/snapshot_metadata.json'),Path(__file__),
        Path('src/models/n5_n3_update.py'),Path('src/models/n5_inputs.py'),Path('src/models/n5_snapshots.py'),
        Path('src/models/n5_research_protocol.py')]
    hashes={p:hashlib.sha256(p.read_bytes()).hexdigest() for p in inputs}
    config=json.loads(config_path.read_text(encoding='utf-8'))
    fits,residuals,quote_qa=calibrate_quote_grid(pd.read_parquet(quote_path),pd.read_parquet(anchor_path),pd.read_parquet(depth_path),config)
    declared=json.loads((root/'n3_priority2_quality.json').read_text(encoding='utf-8'))
    if declared['real_new_rpc_quotes']!=quote_qa['verified_quotes'] or declared['directions_total']!=quote_qa['directions']:
        raise ValueError('N3 quote quality counts differ from actual rows')
    if declared['input_dex_sha256']!=hashes[depth_path]:raise ValueError('N3 depth source hash differs')
    quote_qa['checkpoint_present']=(root/'n3_priority2_verified_quotes_checkpoint.jsonl').is_file()
    quote_qa['max_power_fit_residual_percentage_points']=float(fits.max_abs_residual_percentage_points.max())
    quote_qa['fit_acceptance']='candidate_only; power-law error can be material, inspect residuals'
    curve=residuals.rename(columns={'observed_impact_pct':'impact_pct'}).drop(columns=['fitted_impact_pct','residual_percentage_points'])
    curve['point_status']='source_observation_not_interpolated'
    evidence,coverage_qa=audit_market_coverage(pd.read_parquet(events_path),pd.read_parquet(recovered),config)
    quotes=pd.read_parquet(returns_path)
    quotes['return_window_end_utc']=pd.to_datetime(quotes.return_window_end_utc,utc=True)
    calibration_end=pd.Timestamp(config['snapshots']['A']['block_timestamp_utc'])
    calibration_start=calibration_end-pd.Timedelta(days=365)
    eligible=quotes[quotes.return_valid.eq(True)&quotes.return_window_end_utc.between(calibration_start,calibration_end)].copy()
    return_report=dict(rows=len(quotes),eligible_before_A=len(eligible),
        timestamp_min=str(quotes.return_window_end_utc.min()),timestamp_max=str(quotes.return_window_end_utc.max()),
        rows_by_asset=eligible.groupby('asset').size().to_dict(),
        depeg_daily_365_confirmation='not_supplied_in_new_short_price_return_table',
        limitation='Fresh adjacent returns are not evidence of a complete 365-day paired ETH/USD and stETH/ETH calibration')
    refs=pd.read_parquet(reference)
    cov=pd.read_parquet(coverage_path)
    actual=set(cov[['snapshot_id','snapshot_block','wallet_address']].itertuples(index=False,name=None))
    target=refs[refs.total_debt_base_usd.gt(0)]
    expected=set(target[['snapshot_id','snapshot_block','wallet_address']].itertuples(index=False,name=None))
    if actual!=expected or not cov.covered.all() or len(cov)!=2*len(expected):
        raise ValueError('Complete Compound bundle no longer matches A/B1/B2 borrower selection')
    summary=dict(status='intake_completed_not_research_accepted',
        account_reference_selection=dict(path=str(reference),
            reason='Default account_reference.parquet now holds legacy A/B; selected matching A/B1/B2 file after label/block checks'),
        compound_selection_check=dict(status='passed',queries=len(cov),missing_queries=int((~cov.covered).sum())),
        multivolume_quotes=quote_qa,market_event_price_coverage=coverage_qa,
        new_return_table=return_report,
        remaining=['Missing oracle token paths/AnswerUpdated/depeg history for full-wallet holdout',
            'Independent event-log completeness', '365-day calibration source confirmation',
            'Apply calibrated execution curves with finite supported trade domain and explicit oracle feedback assumptions',
            'Aave/Comet execution and behavioral Q3 assumptions', 'N4 cohort/depeg/Reverse consistency'])
    args.output_dir.mkdir(parents=True)
    artifacts={}
    for name,frame,kind in [('price_impact_fit_candidates.parquet',fits,'calibration_candidate'),
        ('price_impact_fit_residuals.parquet',residuals,'diagnostic'),
        ('execution_curve_observed_points.parquet',curve,'observed_quote_points'),
        ('event_market_quote_evidence.parquet',evidence,'observed_quote_coverage'),
        ('new_market_returns_before_A.parquet',eligible,'calibration_input_candidate')]:
        frame.to_parquet(args.output_dir/name,index=False);artifacts[name]=kind
    (args.output_dir/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2,allow_nan=False),encoding='utf-8')
    artifacts['summary.json']='diagnostic'
    if any(hashlib.sha256(p.read_bytes()).hexdigest()!=h for p,h in hashes.items()):raise ValueError('Source changed')
    write_manifest(args.output_dir,inputs,artifacts,summary)
    if verify_manifest(args.output_dir):raise ValueError('Manifest mismatch')
    print(json.dumps(summary,ensure_ascii=False,indent=2,allow_nan=False))


if __name__=='__main__':main()
