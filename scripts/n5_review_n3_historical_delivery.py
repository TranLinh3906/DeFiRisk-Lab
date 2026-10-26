"""Offline review of N3 historical delivery; source files remain unchanged."""
import argparse
import ast
import hashlib
import json
from pathlib import Path

import pandas as pd

from scripts.n3_depeg_b1_b2_qa import load_market_ratios, audit
from src.models.n5_research_protocol import peak_event_window
from src.models.n5_snapshots import select_event_windows
from src.models.n5_readiness import write_manifest, verify_manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir', type=Path, required=True)
    out = parser.parse_args().output_dir
    if out.exists():
        raise FileExistsError('Choose a new output directory')
    config_path = Path('config/snapshots.json')
    meta_path = Path('data/processed/aave/snapshot_metadata.json')
    ratio_path = Path('data/staging/n3_market_ratios.parquet')
    history_path = Path('data/n3_history_2024_2026/final/n3_liquidations_observed.parquet')
    delivered_peak = Path('outputs/n3_peak300_liquidation_QA_FINAL/n3_peak300_liquidation_quality.json')
    gas_path = Path('outputs/n3_gas_inputs_FINAL/n3_gas_executed_liquidations.csv')
    gas_qa = Path('outputs/n3_gas_inputs_FINAL/n3_gas_evidence_quality.json')
    sources = [Path(p) for p in [__file__, 'scripts/n3_depeg_b1_b2_qa.py',
        'scripts/n3_peak300_liquidation_qa.py', 'scripts/n3_gas_freeze_evidence_inputs.py',
        'src/data/fetch_liquidations.py', 'src/models/n5_research_protocol.py',
        'src/models/n5_snapshots.py', 'src/models/n5_readiness.py']]
    inputs = [config_path, meta_path, ratio_path, history_path, delivered_peak, gas_path, gas_qa, *sources]
    for source in sources:
        ast.parse(source.read_text(encoding='utf-8-sig'))
    config = json.loads(config_path.read_text(encoding='utf-8'))
    metadata = json.loads(meta_path.read_text(encoding='utf-8'))
    ratios = load_market_ratios(ratio_path)
    observed, _ = select_event_windows(pd.read_parquet(history_path), config)
    delivered = json.loads(delivered_peak.read_text())
    grids, parts, report = [], [], {}
    for sid, folder, hours in [('B1', 'crash_2024_08', 24), ('B2', 'crash_2025_10', 48)]:
        snap = config['snapshots'][sid]
        if metadata['snapshots'][sid]['snapshot_block'] != snap['block']:
            raise ValueError('Snapshot identity mismatch')
        grid, depeg_reports = audit(ratios, metadata, window_hours=hours)
        grids.append(grid[grid.snapshot_id.eq(sid)])
        depeg = next(r for r in depeg_reports if r['snapshot_id'] == sid)
        peak = peak_event_window(observed[observed.validation_snapshot_id.eq(sid)], width=300)
        base = Path('data/n3_history_2024_2026/liquidations') / folder
        event_path = base / 'data/staging/n3_liquidations_etherscan.parquet'
        qa_path = base / 'outputs/tables/n3_etherscan_quality.json'
        inputs.extend([event_path, qa_path])
        events = pd.read_parquet(event_path)
        qa = json.loads(qa_path.read_text())
        if len(events) != qa['events_decoded'] or events.duplicated(['tx_hash', 'log_index']).any():
            raise ValueError('Source count or event identity mismatch')
        chosen = events[events.event_block.between(peak['start_block'], peak['end_block'])].copy()
        chosen['snapshot_id'] = sid
        parts.append(chosen)
        expected = observed[observed.validation_snapshot_id.eq(sid) & observed.event_block.between(peak['start_block'], peak['end_block'])]
        keys = lambda d: set(zip(d.tx_hash.str.lower(), d.log_index))
        if keys(chosen) != keys(expected):
            raise ValueError('Local peak events differ from historical validation input')
        old = next(r for r in delivered['snapshots'] if r['snapshot_id'] == sid)
        start, end = pd.Timestamp(snap['block_timestamp_utc']), pd.Timestamp(snap['window_end_exclusive_utc'])
        local = ratios[ratios.timestamp_utc.ge(start) & ratios.timestamp_utc.lt(end)]
        report[sid] = dict(depeg=depeg, ratio_values=sorted(local.market_ratio.dropna().unique().tolist()),
            last_ratio_utc=local.timestamp_utc.max().isoformat() if len(local) else None,
            approved_peak=peak, local_peak_event_rows=len(chosen), local_peak_wallets=int(chosen.wallet_address.nunique()),
            delivered_peak_matches=old['window_start_block'] == peak['start_block'] and old['window_end_block'] == peak['end_block'],
            independent_log_completeness='not_proven_by_local_count_agreement')
    gas = pd.read_csv(gas_path)
    receipts = gas.gas_used.notna() & gas.effective_gas_price_wei.notna()
    summary = dict(status='diagnostic_pending_acceptance', snapshots=report,
        ratios_total=len(ratios), ratios_unique=int(ratios.market_ratio.nunique()),
        provenance='Source label alone does not verify raw historical quote provenance; constant values require reconciliation',
        gas=dict(delivered_txs=len(gas), rows_with_receipt_fields=int(receipts.sum()),
                 rows_with_usd_gas=int(gas.gas_cost_usd_market.notna().sum()), freeze_proven=False),
        limitation='No RPC or external verification. Corrected peak rows are local candidates, not proof of exhaustive logs.')
    out.mkdir(parents=True)
    pd.concat(grids, ignore_index=True).to_csv(out / 'depeg_full_window_qa.csv', index=False)
    pd.concat(parts, ignore_index=True).to_parquet(out / 'approved_peak300_events.parquet', index=False)
    (out / 'summary.json').write_text(json.dumps(summary, indent=2, allow_nan=False), encoding='utf-8')
    artifacts = {'depeg_full_window_qa.csv': 'diagnostic', 'approved_peak300_events.parquet': 'observed_local_candidate', 'summary.json': 'diagnostic'}
    write_manifest(out, inputs, artifacts, summary)
    if verify_manifest(out):
        raise ValueError('Manifest mismatch')
    print(json.dumps(summary, indent=2, allow_nan=False))


if __name__ == '__main__':
    main()
