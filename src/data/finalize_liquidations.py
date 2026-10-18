"""N3 / Muc 2 - Finalize Etherscan Aave V3 liquidation staging.

Aligned to Research Design v2.0 (Snapshot B historical ground truth) and
DATA_CONTRACT v1.0 (11 fields, exact event PK).

Uses existing output of price_liquidations_usd.py, DOES NOT call the API and
DOES NOT overwrite canonical data/processed/fact_historical_liquidations.parquet.

Run from repository root:
    python src/data/finalize_liquidations.py
    python src/data/finalize_liquidations.py --overwrite

Output:
    data/staging/n3_liquidations.parquet        (ALL events, even without USD)
    data/staging/n3_liquidations_for_fact.parquet (11 official fields; priced only)
    outputs/tables/n3_liquidations_final_quality.json
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import tempfile
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
FACT_COLUMNS = [
    'tx_hash', 'log_index', 'event_block', 'event_timestamp', 'protocol_id',
    'wallet_address', 'liquidator_address', 'collateral_token_address',
    'debt_token_address', 'debt_repaid_usd', 'collateral_seized_usd'
]
ADDRESS_COLUMNS = [
    'wallet_address', 'liquidator_address',
    'collateral_token_address', 'debt_token_address'
]


def safe_write_df(df: pd.DataFrame, path: Path, overwrite: bool):
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and not overwrite:
        raise FileExistsError(f'{path} exists; pass --overwrite to make backup and replace')
    if path.exists():
        ts = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
        shutil.copy2(path, path.with_name(path.name + '.bak_' + ts))
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix='.n3_', suffix='.tmp')
    os.close(fd)
    try:
        df.to_parquet(tmp, index=False, compression='snappy')
        os.replace(tmp, path)
    finally:
        Path(tmp).unlink(missing_ok=True)


def safe_write_json(obj: dict, path: Path, overwrite: bool):
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and not overwrite:
        raise FileExistsError(f'{path} exists; pass --overwrite')
    if path.exists():
        ts = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
        shutil.copy2(path, path.with_name(path.name + '.bak_' + ts))
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix='.n3_', suffix='.tmp')
    os.close(fd)
    try:
        Path(tmp).write_text(json.dumps(obj, indent=2, ensure_ascii=False), encoding='utf-8')
        os.replace(tmp, path)
    finally:
        Path(tmp).unlink(missing_ok=True)


def usd_from_raw(amount, decimals, price) -> float | None:
    """Recompute USD from raw integer token units, avoiding float-wei rounding."""
    if any(pd.isna(x) for x in (amount, decimals, price)):
        return None
    try:
        d = Decimal(str(decimals)); a = Decimal(str(amount)); p = Decimal(str(price))
        if not d.is_finite() or d != d.to_integral_value() or not 0 <= d <= 36:
            return None
        if not a.is_finite() or a != a.to_integral_value() or a < 0:
            return None
        if not p.is_finite() or p <= 0:
            return None
        return float(a / Decimal(10) ** int(d) * p)
    except (InvalidOperation, OverflowError, ValueError, TypeError):
        return None


def finalize(priced: pd.DataFrame, *, max_lag_minutes: int = 60) -> tuple[pd.DataFrame, pd.DataFrame, dict]:
    required = set(FACT_COLUMNS) | {
        'debt_raw_amount', 'collateral_raw_amount', 'debt_decimals',
        'collateral_decimals', 'debt_price_usd', 'collateral_price_usd',
        'debt_price_timestamp', 'collateral_price_timestamp'
    }
    missing = sorted(required - set(priced.columns))
    if missing:
        raise ValueError(f'Missing input columns: {missing}. Run price_liquidations_usd.py first.')
    df = priced.copy()
    df['event_timestamp'] = pd.to_datetime(df['event_timestamp'], utc=True, errors='coerce')
    if df['event_timestamp'].isna().any():
        raise ValueError('event_timestamp contains invalid timestamps')
    for col in ('debt_price_timestamp', 'collateral_price_timestamp'):
        df[col] = pd.to_datetime(df[col], utc=True, errors='coerce')
    for col in ADDRESS_COLUMNS:
        df[col] = df[col].astype(str).str.lower().str.strip()
        bad = ~df[col].str.fullmatch(r'0x[0-9a-f]{40}')
        if bad.any():
            raise ValueError(f'Invalid {col}: {df.loc[bad,col].iloc[0]}')
    df['tx_hash'] = df['tx_hash'].astype(str).str.lower().str.strip()
    if (~df.tx_hash.str.fullmatch(r'0x[0-9a-f]{64}')).any():
        raise ValueError('Invalid tx_hash (required: 0x + 64 hex)')
    if (df['protocol_id'] != 'aave_v3_ethereum').any():
        raise ValueError('Wrong protocol_id: expected aave_v3_ethereum')
    for col in ('log_index', 'event_block'):
        numeric = pd.to_numeric(df[col], errors='raise')
        if numeric.isna().any() or (numeric < 0).any() or (numeric % 1 != 0).any():
            raise ValueError(f'Invalid {col}')
        df[col] = numeric.astype('int32' if col == 'log_index' else 'int64')
    if df.duplicated(['tx_hash', 'log_index']).any():
        raise ValueError('Duplicate tx_hash + log_index; investigate source before continuing')

    # Validate existing USD amounts independently against raw token amounts/decimals/prices.
    reasons = []
    for row in df.itertuples(index=False):
        issues = []
        for side, val_col, raw_col, dec_col, price_col, ts_col in (
            ('debt', 'debt_repaid_usd', 'debt_raw_amount', 'debt_decimals', 'debt_price_usd', 'debt_price_timestamp'),
            ('collateral', 'collateral_seized_usd', 'collateral_raw_amount', 'collateral_decimals', 'collateral_price_usd', 'collateral_price_timestamp')
        ):
            val = getattr(row, val_col)
            expected = usd_from_raw(getattr(row, raw_col), getattr(row, dec_col), getattr(row, price_col))
            qt = getattr(row, ts_col)
            if expected is None or pd.isna(val):
                issues.append(f'{side}_price_or_decimals_missing')
                continue
            actual = float(val)
            if not (-float('inf') < actual < float('inf')) or actual < 0:
                raise ValueError(f'Invalid USD amount in {val_col}')
            tolerance = max(0.02, 1e-7 * abs(expected))
            if abs(actual - expected) > tolerance:
                raise ValueError(f'USD mismatch for {row.tx_hash} {side}: saved={actual}, expected={expected}')
            if pd.isna(qt):
                issues.append(f'{side}_quote_timestamp_missing')
                continue
            lag_s = (row.event_timestamp - qt).total_seconds()
            if lag_s < 0:
                issues.append(f'{side}_future_quote')
            elif lag_s > max_lag_minutes * 60:
                issues.append(f'{side}_stale_quote')
        reasons.append(';'.join(issues) if issues else 'ok')
    df['n3_validation_status'] = reasons
    df = df.sort_values(['event_timestamp', 'tx_hash', 'log_index']).reset_index(drop=True)
    valid_usd = df['n3_validation_status'].eq('ok')
    fact = df.loc[valid_usd, FACT_COLUMNS].copy().reset_index(drop=True)
    fact['debt_repaid_usd'] = fact['debt_repaid_usd'].astype(float)
    fact['collateral_seized_usd'] = fact['collateral_seized_usd'].astype(float)
    if fact[FACT_COLUMNS].isna().any().any():
        raise ValueError('Fact-ready dataset contains null; investigate')
    quality = {
        'status': 'fact-ready subset; full event coverage NOT guaranteed',
        'created_utc': datetime.now(timezone.utc).isoformat(),
        'source_price_type': 'DefiLlama historical market reference; NOT Aave oracle',
        'study_scope': 'Aave V3 Ethereum; Snapshot B 24h holdout requires N1/N2 block and window',
        'research_design_alignment': {'Q1': 'background market liquidation evidence, not looping identification', 'Q2': 'realized debt repaid and collateral seized; NOT counterfactual CF/bad debt', 'Q3': 'realized token liquidation events for depeg validation; needs price and N2 position joins'},
        'total_events_in_input': int(len(df)),
        'fact_ready_events': int(len(fact)),
        'fact_ready_pct': round(100 * len(fact) / len(df), 2) if len(df) else None,
        'events_excluded': int(len(df) - len(fact)),
        'reasons': {str(k): int(v) for k, v in df['n3_validation_status'].value_counts().items()},
        'unique_event_pk': not bool(df.duplicated(['tx_hash', 'log_index']).any()),
        'min_time_utc': str(df['event_timestamp'].min()) if len(df) else None,
        'max_time_utc': str(df['event_timestamp'].max()) if len(df) else None,
        'fact_fields': FACT_COLUMNS,
        'total_debt_repaid_usd_priced_subset': float(fact['debt_repaid_usd'].sum()),
        'total_collateral_seized_usd_priced_subset': float(fact['collateral_seized_usd'].sum()),
        'max_quote_lag_minutes': max_lag_minutes,
        'caveat': 'Event-time USD uses market reference. Do not treat collateral_seized_usd as execution proceeds or claim system totals without independent block-window coverage and N2/N1 snapshot checks.',
        'event_collection_coverage': 'NOT_VERIFIED_INDEPENDENTLY',
        'canonical_contract_written': False
    }
    return df, fact, quality


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--input', type=Path, default=ROOT / 'data/staging/n3_liquidations_priced.parquet')
    p.add_argument('--raw-events', type=Path, default=ROOT / 'data/staging/n3_liquidations_etherscan.parquet', help='Independent local pre-price event source for PK reconciliation')
    p.add_argument('--no-raw-check', action='store_true', help='Use only if original Etherscan staging is unavailable')
    p.add_argument('--n2-asset-config', type=Path, help='Optional N2 dim_asset_config parquet for snapshot-independent token address precheck')
    p.add_argument('--max-lag-minutes', type=int, default=60)
    p.add_argument('--snapshot-b-block', type=int, help='N1/N2 confirmed on-chain Snapshot B block; used only for optional 24h OOS extraction')
    p.add_argument('--snapshot-b-utc', help='UTC timestamp for Snapshot B block; N1/N2 must verify exact block time')
    p.add_argument('--min-coverage-pct', type=float, default=0)
    p.add_argument('--overwrite', action='store_true')
    a = p.parse_args()
    if not a.input.exists():
        raise SystemExit(f'Missing {a.input}; run price_liquidations_usd.py first.')
    if a.max_lag_minutes < 0 or not 0 <= a.min_coverage_pct <= 100:
        raise SystemExit('Invalid limits')
    if (a.snapshot_b_block is None) != (a.snapshot_b_utc is None):
        raise SystemExit('Provide BOTH --snapshot-b-block and --snapshot-b-utc, or neither')
    if a.snapshot_b_block is not None and a.snapshot_b_block < 0:
        raise SystemExit('Invalid --snapshot-b-block')
    df, fact, quality = finalize(pd.read_parquet(a.input), max_lag_minutes=a.max_lag_minutes)
    if not a.no_raw_check:
        if not a.raw_events.is_file():
            raise SystemExit(f'Missing {a.raw_events}; use --no-raw-check only when input provenance has been documented')
        raw = pd.read_parquet(a.raw_events)
        needed = {'tx_hash', 'log_index', 'event_block', 'event_timestamp', 'debt_raw_amount', 'collateral_raw_amount'}
        if needed - set(raw.columns):
            raise ValueError(f'Raw event file missing {needed-set(raw.columns)}')
        raw['tx_hash'] = raw['tx_hash'].astype(str).str.lower().str.strip()
        raw['log_index'] = pd.to_numeric(raw.log_index, errors='raise').astype('int64')
        raw['event_timestamp'] = pd.to_datetime(raw.event_timestamp, utc=True, errors='raise')
        if raw.duplicated(['tx_hash', 'log_index']).any():
            raise ValueError('Raw Etherscan event keys duplicated')
        raw_key = set(zip(raw.tx_hash, raw.log_index))
        val_key = set(zip(df.tx_hash, df.log_index))
        if raw_key != val_key:
            raise ValueError(f'Inconsistent event IDs: priced missing={len(raw_key-val_key)}, priced extra={len(val_key-raw_key)}')
        joined = df.merge(raw[list(needed)], on=['tx_hash','log_index'], suffixes=('', '_raw'), validate='one_to_one')
        for c in ('event_block','event_timestamp','debt_raw_amount','collateral_raw_amount'):
            if (joined[c].astype(str) != joined[c+'_raw'].astype(str)).any():
                raise ValueError(f'Raw Etherscan versus priced values mismatch in {c}')
        quality['local_raw_event_reconciliation'] = 'PASS exact PK, timestamp, raw token amounts, block'
        quality['raw_events_seen'] = len(raw)
    else:
        quality['local_raw_event_reconciliation'] = 'SKIPPED; unverified'
    if a.n2_asset_config:
        assets = pd.read_parquet(a.n2_asset_config)
        required = {'token_address', 'protocol_id'}
        if required-set(assets.columns):
            raise ValueError('N2 asset config missing token_address/protocol_id')
        known = set(assets.loc[assets.protocol_id == 'aave_v3_ethereum', 'token_address'].astype(str).str.lower())
        seen = set(df.collateral_token_address) | set(df.debt_token_address)
        unknown = sorted(seen-known)
        quality['n2_unknown_token_addresses'] = unknown
        quality['n2_token_check'] = 'PASS' if not unknown else 'REVIEW REQUIRED (snapshots may not include all historical assets)'
    else:
        quality['n2_token_check'] = 'PENDING (not provided)'
    eth_qa_path = ROOT / 'outputs/tables/n3_etherscan_quality.json'
    if eth_qa_path.exists():
        eth_qa = json.loads(eth_qa_path.read_text())
        quality['etherscan_query_window_utc'] = {
            'start': eth_qa.get('start_inclusive_utc'), 'end': eth_qa.get('end_exclusive_utc'),
            'first_block': eth_qa.get('first_block'), 'last_block': eth_qa.get('last_block')
        }
        if eth_qa.get('events_decoded') is not None and int(eth_qa['events_decoded']) != len(df):
            raise ValueError('Etherscan quality report event count does not match priced rows')
        if 'start_inclusive_utc' in eth_qa and 'end_exclusive_utc' in eth_qa:
            start = pd.Timestamp(eth_qa['start_inclusive_utc']); end = pd.Timestamp(eth_qa['end_exclusive_utc'])
            if len(df) and not df.event_timestamp.between(start, end, inclusive='left').all():
                raise ValueError('Event timestamps outside documented Etherscan window')
    holdout = None
    if a.snapshot_b_block is not None:
        snap_time = pd.Timestamp(a.snapshot_b_utc)
        if snap_time.tzinfo is None:
            raise SystemExit('--snapshot-b-utc must have explicit UTC/Z timezone offset')
        snap_time = snap_time.tz_convert('UTC')
        if not eth_qa_path.exists():
            raise ValueError('Snapshot-B extraction requires n3_etherscan_quality.json to prove the requested 24h window was queried')
        holdout_qa = json.loads(eth_qa_path.read_text())
        if not holdout_qa.get('start_inclusive_utc') or not holdout_qa.get('end_exclusive_utc'):
            raise ValueError('Etherscan quality JSON lacks query start/end; cannot claim full 24h query')
        query_start = pd.Timestamp(holdout_qa['start_inclusive_utc'])
        query_end = pd.Timestamp(holdout_qa['end_exclusive_utc'])
        if query_start > snap_time or query_end < snap_time + pd.Timedelta(hours=24):
            raise ValueError('Collected Etherscan time window does not cover the full 24h after Snapshot B')
        after_block = df.event_block > a.snapshot_b_block
        in_24h = (df.event_timestamp >= snap_time) & (df.event_timestamp < snap_time + pd.Timedelta(hours=24))
        holdout_all = df.loc[after_block & in_24h].copy()
        holdout = holdout_all.loc[holdout_all.n3_validation_status=='ok', FACT_COLUMNS].copy()
        quality['snapshotB_24h_holdout'] = {
            'snapshot_block_user_supplied': a.snapshot_b_block,
            'snapshot_utc_user_supplied': snap_time.isoformat(),
            'end_exclusive_utc': (snap_time + pd.Timedelta(hours=24)).isoformat(),
            'all_events_after_snapshot': int(len(holdout_all)),
            'priced_events': int(len(holdout)),
            'snapshot_block_timestamp_independently_verified': False,
            'oos_scope': 'only the events present in the N3 input; block coverage external verification still required'
        }
    print(f"[AUDIT] priced={len(fact)}/{len(df)} ({quality['fact_ready_pct']}%), reasons={quality['reasons']}")
    if quality['fact_ready_pct'] is not None and quality['fact_ready_pct'] < a.min_coverage_pct:
        raise SystemExit('Coverage lower than minimum, NO files written.')
    out = ROOT / 'data/staging/n3_liquidations.parquet'
    fact_path = ROOT / 'data/staging/n3_liquidations_for_fact.parquet'
    qa = ROOT / 'outputs/tables/n3_liquidations_final_quality.json'
    targets = [out, fact_path, qa]
    holdout_path = ROOT / 'data/staging/n3_liquidations_holdout_24h.parquet'
    if holdout is not None:
        targets.append(holdout_path)
    if not a.overwrite and any(x.exists() for x in targets):
        raise SystemExit('Output exists; use --overwrite to back up previous results.')
    safe_write_df(df, out, a.overwrite)
    safe_write_df(fact, fact_path, a.overwrite)
    if holdout is not None:
        safe_write_df(holdout, holdout_path, a.overwrite)
    safe_write_json(quality, qa, a.overwrite)
    for x in targets:
        print('[SAVED]', x.relative_to(ROOT))
    print('[NOTICE] Canonical data/processed/fact_historical_liquidations.parquet was NOT modified.')


if __name__ == '__main__':
    main()
