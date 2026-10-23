#!/usr/bin/env python3
"""N3 historical research completion, additive and non-destructive.

Run in the root of N3_Aave_Day17_Two_Scripts. Existing N3 blockchain
collectors are run in isolated per-period workspaces; original 2026 and
Compound data/checkpoints are never modified.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HISTORY = ROOT / 'data' / 'n3_history_2024_2026'
FINAL = HISTORY / 'final'
LIQ = HISTORY / 'liquidations'

PERIODS = {
    'crash_2024_08': ('2024-08-03', '2024-08-08'),
    'crash_2025_10': ('2025-10-08', '2025-10-13'),
    'reference_2026_10': ('2026-10-12', '2026-10-16'),
}


def utc(s):
    import pandas as pd
    t = pd.Timestamp(s)
    if t.tzinfo is None:
        t = t.tz_localize('UTC')
    return t.tz_convert('UTC')


def check_window(a, b):
    if utc(b) <= utc(a):
        raise ValueError('End must be greater than start (UTC exclusive)')
    if utc(b) > utc(datetime.now(timezone.utc)):
        raise ValueError('End exceeds current UTC time')


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def dump_once(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        raise FileExistsError(f'Already exists: {path}')
    temp = path.with_name(path.name + '.tmp')
    temp.write_text(json.dumps(data, indent=2, ensure_ascii=False, default=str), encoding='utf-8')
    os.replace(temp, path)


def write_parquet_once(path, df):
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        raise FileExistsError(f'Already exists: {path}')
    temp = path.with_name(path.name + '.tmp')
    try:
        df.to_parquet(temp, index=False)
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


def code_dependencies():
    required = ['fetch_liquidations.py', 'price_liquidations_usd.py']
    found = {n: ROOT / 'src' / 'data' / n for n in required}
    for n, p in found.items():
        if not p.is_file():
            raise FileNotFoundError(f'Missing original N3 script: {p}')
    return found


def env_for_run():
    from dotenv import load_dotenv
    load_dotenv(ROOT / '.env', override=False)
    return os.environ.copy()


def run_period(tag, start, end, with_pricing, dry_run=False):
    import re
    if not re.fullmatch('[a-zA-Z0-9_-]{1,65}', tag):
        raise ValueError('Invalid period tag')
    check_window(start, end)
    sources = code_dependencies()
    workspace = LIQ / tag
    source_dir = workspace / 'src' / 'data'
    copied = {n: source_dir / n for n in sources}
    raw = workspace / 'data/staging/n3_liquidations_etherscan.parquet'
    raw_qa = workspace / 'outputs/tables/n3_etherscan_quality.json'
    priced = workspace / 'data/staging/n3_liquidations_priced.parquet'
    priced_qa = workspace / 'outputs/tables/n3_usd_pricing_quality.json'
    manifest = workspace / 'n3_period_manifest.json'
    expected_hashes = {n: sha256(p) for n, p in sources.items()}
    print(f'[PERIOD] {tag}: [{start}, {end}) UTC')
    print('[WORKSPACE]', workspace)
    print('[DATA] Existing 2026 / Compound directories untouched')
    if manifest.exists():
        current = json.loads(manifest.read_text(encoding='utf-8'))
        if (current.get('start_inclusive_utc') != start or
            current.get('end_exclusive_utc') != end or
            current.get('source_sha256') != expected_hashes):
            raise ValueError('Existing manifest differs: do not reuse tag or different collector code')
    for name, target in copied.items():
        if target.exists() and sha256(target) != expected_hashes[name]:
            raise ValueError(f'Existing copied source differs: {target}; use a new tag')
    if dry_run:
        print('[DRY RUN] Collect:', not raw.exists(), 'Price:', with_pricing and not priced.exists())
        return
    source_dir.mkdir(parents=True, exist_ok=True)
    for name, source in sources.items():
        if not copied[name].exists():
            shutil.copy2(source, copied[name])
    if not raw.exists():
        if (workspace / 'data/raw/market/etherscan_liquidations_raw.csv').exists():
            raise RuntimeError('Partial Etherscan outputs: inspect workspace before retrying')
        env = env_for_run()
        if not env.get('ETHERSCAN_API_KEY', '').strip():
            raise RuntimeError('ETHERSCAN_API_KEY missing from local .env (do not share key)')
        print('[COLLECT] on-chain LiquidationCall ...', flush=True)
        subprocess.run([sys.executable, str(copied['fetch_liquidations.py']),
                        '--start', start, '--end', end], cwd=workspace, env=env, check=True)
    elif not raw_qa.exists():
        raise RuntimeError('Raw events exist but Etherscan QA missing; review before proceeding')
    else:
        print('[RESUME] Existing raw events and QA')
    if with_pricing:
        if not priced.exists():
            env = env_for_run()
            print('[PRICE] DefiLlama historical token-as-of USD (no future quotes)', flush=True)
            subprocess.run([sys.executable, str(copied['price_liquidations_usd.py'])],
                           cwd=workspace, env=env, check=True)
        elif not priced_qa.exists():
            raise RuntimeError('Priced events exist but pricing QA missing')
        else:
            print('[RESUME] Existing priced events and QA')
    if not manifest.exists():
        dump_once(manifest, {
            'tag': tag, 'start_inclusive_utc': start, 'end_exclusive_utc': end,
            'protocol': 'aave_v3_ethereum', 'chain_id': 1,
            'source_sha256': expected_hashes,
            'observed_not_simulated': True,
            'price_source': 'DefiLlama historical token price, not Aave oracle',
            'note': 'This captures only the specified UTC window, not the entire 2024-2026 range',
        })
    print('[DONE]', tag, 'raw=', raw.exists(), 'priced=', priced.exists())


def monthly_periods(a, b):
    import pandas as pd
    check_window(a, b)
    start, end = utc(a), utc(b)
    cursor = start
    while cursor < end:
        month_next = (cursor.replace(day=1) + pd.DateOffset(months=1))
        end_part = min(end, month_next)
        tag = f'month_{cursor:%Y%m%d}_{end_part:%Y%m%d}'
        yield tag, cursor.strftime('%Y-%m-%d'), end_part.strftime('%Y-%m-%d')
        cursor = end_part


def active_periods(tags):
    if not LIQ.exists():
        raise FileNotFoundError('No history/liquidations directory. Collect events first.')
    dirs = [LIQ / tag for tag in tags] if tags else sorted(p for p in LIQ.iterdir() if p.is_dir())
    for wd in dirs:
        manifest = wd / 'n3_period_manifest.json'
        if not manifest.exists():
            raise ValueError(f'No period manifest: {wd}')
        yield wd.name, wd, json.loads(manifest.read_text(encoding='utf-8'))


def merge_events(tags, require_full_prices=False):
    import pandas as pd
    frames, windows, qa_parts = [], [], []
    for tag, wd, meta in active_periods(tags):
        f = wd / 'data/staging/n3_liquidations_priced.parquet'
        q = wd / 'outputs/tables/n3_usd_pricing_quality.json'
        if not f.is_file() or not q.is_file():
            raise FileNotFoundError(f'Need priced Parquet and QA for {tag}')
        df = pd.read_parquet(f)
        needed = {'tx_hash', 'log_index', 'event_timestamp', 'wallet_address',
                  'debt_repaid_usd', 'collateral_seized_usd',
                  'collateral_token_address', 'debt_token_address'}
        if needed - set(df.columns):
            raise ValueError(f'{tag} missing {needed-set(df.columns)}')
        df['event_timestamp'] = pd.to_datetime(df['event_timestamp'], utc=True)
        a, b = utc(meta['start_inclusive_utc']), utc(meta['end_exclusive_utc'])
        if ((df['event_timestamp'] < a) | (df['event_timestamp'] >= b)).any():
            raise ValueError(f'{tag}: event(s) outside manifest window')
        df['tx_hash'] = df['tx_hash'].astype(str).str.lower()
        df['log_index'] = pd.to_numeric(df['log_index'], errors='raise').astype('int64')
        if df.duplicated(['tx_hash', 'log_index']).any():
            raise ValueError(f'Duplicate event within {tag}')
        df['period_tag'] = tag
        frames.append(df)
        windows.append((tag, a, b))
        qa_parts.append({'tag': tag, 'events': len(df), 'start': str(a), 'end_exclusive': str(b),
                         'usd_pricing_qa': json.loads(q.read_text(encoding='utf-8'))})
    if not frames:
        raise ValueError('No period selected')
    ordered = sorted(windows, key=lambda x: x[1])
    for previous, following in zip(ordered, ordered[1:]):
        if previous[2] > following[1]:
            raise ValueError('Overlapping windows: analyze event windows OR monthly windows, not both at once')
    joined = pd.concat(frames, ignore_index=True)
    duplicates = joined[joined.duplicated(['tx_hash', 'log_index'], keep=False)]
    if not duplicates.empty:
        for _, grp in duplicates.groupby(['tx_hash', 'log_index']):
            for key in ['event_timestamp', 'wallet_address', 'debt_repaid_usd', 'collateral_seized_usd']:
                if grp[key].astype(str).nunique(dropna=False) > 1:
                    raise ValueError(f'Conflicting duplicates for {key}, inspect event/price source')
    dedup = int(joined.duplicated(['tx_hash', 'log_index']).sum())
    result = joined.sort_values(['event_timestamp', 'period_tag']).drop_duplicates(
        ['tx_hash', 'log_index']).reset_index(drop=True)
    for name in ('debt_repaid_usd', 'collateral_seized_usd'):
        result[name] = pd.to_numeric(result[name], errors='coerce')
        if result[name].lt(0).any():
            raise ValueError(f'Negative {name}')
    result['has_valid_usd'] = (result.debt_repaid_usd.notna()
                               & result.collateral_seized_usd.notna())
    coverage = float(result.has_valid_usd.mean()) if len(result) else 1.0
    if require_full_prices and coverage < 1.0:
        raise ValueError(f'Price coverage {coverage:.2%}; cannot mark complete')
    return result, windows, qa_parts, dedup, coverage


def zero_filled_aggregates(events, windows):
    import pandas as pd
    day_rows, hour_rows = [], []
    for tag, a, b in windows:
        period_events = events[(events.event_timestamp >= a) & (events.event_timestamp < b)].copy()
        for freq, target in [('D', day_rows), ('h', hour_rows)]:
            bins = pd.date_range(a, b, freq=freq, inclusive='left')
            tmp = period_events.copy()
            tmp['bin_utc'] = tmp['event_timestamp'].dt.floor(freq)
            totals = tmp.groupby('bin_utc').agg(
                event_count=('tx_hash','size'),
                usd_priced_events=('has_valid_usd','sum'),
                debt_repaid_usd=('debt_repaid_usd',lambda x:x.sum(min_count=1)),
                collateral_seized_usd=('collateral_seized_usd',lambda x:x.sum(min_count=1)),
            ) if len(tmp) else None
            for bucket in bins:
                if totals is not None and bucket in totals.index:
                    row = totals.loc[bucket]
                    n = int(row['event_count'])
                    target.append({
                        'period_tag':tag, 'bin_utc':bucket, 'event_count':n,
                        'usd_priced_events':int(row['usd_priced_events']),
                        'debt_repaid_usd_priced':float(row['debt_repaid_usd']) if pd.notna(row['debt_repaid_usd']) else None,
                        'collateral_seized_usd_priced':float(row['collateral_seized_usd']) if pd.notna(row['collateral_seized_usd']) else None,
                    })
                else:
                    target.append({'period_tag':tag,'bin_utc':bucket,'event_count':0,
                                   'usd_priced_events':0,'debt_repaid_usd_priced':0.0,
                                   'collateral_seized_usd_priced':0.0})
    return pd.DataFrame(day_rows), pd.DataFrame(hour_rows)


def load_hourly_market():
    import pandas as pd
    market = HISTORY / 'market'
    final_file = FINAL / 'market_crashes_hourly.parquet'
    files = [final_file] if final_file.exists() else sorted(market.glob('*USDT_1h_*.parquet'))
    if not files:
        return pd.DataFrame()
    frames = [pd.read_parquet(p) for p in files]
    df = pd.concat(frames, ignore_index=True)
    needed = {'pair','timestamp_utc','close','volume_quote_usdt'}
    if needed-set(df.columns):
        raise ValueError(f'Hourly market missing {needed-set(df.columns)}')
    df['timestamp_utc'] = pd.to_datetime(df.timestamp_utc, utc=True)
    df['bin_utc'] = df.timestamp_utc
    if df.duplicated(['pair','bin_utc']).any():
        raise ValueError('Duplicate pair/hour, do not silently merge')
    return df


def descriptive_analysis(events, windows):
    import pandas as pd
    daily, hourly = zero_filled_aggregates(events, windows)
    if daily.duplicated(['period_tag','bin_utc']).any():
        raise ValueError('Duplicate descriptive daily bins')
    # Hourly candle values are known only at the END of the hour; this is
    # contemporaneous descriptive comparison, never a causal predictor.
    candles = load_hourly_market()
    if len(candles):
        x = candles.pivot(index='bin_utc',columns='pair',values=['close','volume_quote_usdt'])
        x.columns = [f'{str(col[1]).lower()}_{str(col[0])}' for col in x.columns]
        x = x.reset_index()
        hourly = hourly.merge(x,on='bin_utc',how='left',validate='many_to_one')
    # Use only exact token addresses for breakdown, NOT ticker assumptions.
    breakdown = []
    for col, typ in [('collateral_token_address','collateral'),('debt_token_address','debt')]:
        x = events.groupby(col,dropna=False).agg(event_count=('tx_hash','size'),
            debt_repaid_usd_priced=('debt_repaid_usd',lambda v:v.sum(min_count=1)),
            collateral_seized_usd_priced=('collateral_seized_usd',lambda v:v.sum(min_count=1))).reset_index()
        x = x.rename(columns={col:'token_address'})
        x['token_role']=typ
        breakdown.append(x)
    return daily, hourly, pd.concat(breakdown,ignore_index=True)


def command_analyze(args):
    events, windows, parts, duplicates, coverage = merge_events(args.tags, args.require_full_prices)
    daily, hourly, breakdown = descriptive_analysis(events, windows)
    outputs = {
        'n3_liquidations_observed.parquet': events,
        'n3_liquidations_daily.csv': daily,
        'n3_liquidations_hourly.csv': hourly,
        'n3_liquidation_asset_breakdown.csv': breakdown,
    }
    qa = {
        'type':'N3 observed Aave V3 Ethereum LiquidationCall, never simulated bad debt',
        'source_periods':parts, 'events_unique':int(len(events)),
        'unique_borrowers':int(events.wallet_address.nunique()) if len(events) else 0,
        'deduplicated_overlapping_events':duplicates,
        'usd_priced_events':int(events.has_valid_usd.sum()),
        'usd_price_coverage_pct':round(100*coverage,3),
        'debt_repaid_usd_priced_only':float(events.debt_repaid_usd.sum()),
        'collateral_seized_usd_priced_only':float(events.collateral_seized_usd.sum()),
        'zero_binned_only_within_queried_windows': True,
        'hourly_market':'ETH/BTC Binance Spot candles contemporaneous with event bins (not execution or oracle price)',
        'warnings':['USD totals are partial if price coverage <100%',
                    'No implication that stETH depeg causes liquidation',
                    'No annual liquidation coverage unless all months were collected',
                    'No future market candle may be used to price individual events'],
    }
    planned=[FINAL/name for name in outputs]+[FINAL/'n3_liquidations_quality.json']
    present=[str(p) for p in planned if p.exists()]
    if present:
        raise FileExistsError('Final outputs already exist; review before rerun: '+', '.join(present))
    for name, obj in outputs.items():
        path = FINAL/name
        if name.endswith('.parquet'): write_parquet_once(path,obj)
        else:
            if path.exists(): raise FileExistsError(path)
            obj.to_csv(path,index=False)
        print('[SAVED]',path,'rows',len(obj))
    dump_once(FINAL/'n3_liquidations_quality.json',qa)
    join_observed_market()
    print('[SUMMARY]',qa['events_unique'],'unique events; USD coverage',qa['usd_price_coverage_pct'],'percent')
    print('[NEXT] DEX historical requires N1-approved blocks and N2 same-block Oracle/Lido; see README')



def command_market_final(args):
    """Recalculate log returns/volatility continuously across year boundaries.

    Saved separately from the existing 3 Parquet inputs. No forward fill,
    no implicit USD/USDT equivalence.
    """
    import numpy as np
    import pandas as pd
    base = FINAL
    md = base/'market_daily_2024_2026.parquet'
    tok = base/'token_prices_2024_2026.parquet'
    if not md.exists() or not tok.exists():
        raise FileNotFoundError('Need existing market_daily_2024_2026.parquet and token_prices_2024_2026.parquet in final/')
    prices=pd.read_parquet(md).copy()
    t=pd.read_parquet(tok).copy()
    if {'pair','timestamp_utc','close','volume_quote_usdt'}-set(prices):
        raise ValueError('Market OHLCV columns missing')
    if {'date','asset','price_usd','quote_timestamp_utc'}-set(t):
        raise ValueError('Token as-of columns missing')
    prices['timestamp_utc']=pd.to_datetime(prices.timestamp_utc, utc=True)
    prices=prices.sort_values(['pair','timestamp_utc']).reset_index(drop=True)
    if prices.duplicated(['pair','timestamp_utc']).any():
        raise ValueError('Duplicate pair/timestamp')
    prices['close']=pd.to_numeric(prices.close,errors='coerce')
    if prices.close.isna().any() or prices.close.le(0).any():
        raise ValueError('Invalid price for ETH/BTC')
    log_price=np.log(prices.close)
    prices['log_return_continuous']=log_price.groupby(prices.pair).diff()
    time_gap=prices.groupby('pair').timestamp_utc.diff()
    prices.loc[time_gap.ne(pd.Timedelta(days=1)),'log_return_continuous']=np.nan
    prices['realized_vol_30d_continuous_annualized']=(
        prices.groupby('pair')['log_return_continuous']
        .transform(lambda v:v.rolling(30,min_periods=20).std()*np.sqrt(365))
    )
    t['date']=pd.to_datetime(t.date,utc=True)
    t['quote_timestamp_utc']=pd.to_datetime(t.quote_timestamp_utc,utc=True)
    t=t.sort_values(['asset','date']).reset_index(drop=True)
    if t.duplicated(['asset','date']).any():raise ValueError('Duplicate token/day')
    t['price_usd']=pd.to_numeric(t.price_usd,errors='coerce')
    eod=t.date+pd.Timedelta(days=1)-pd.Timedelta(seconds=1)
    lag=(eod-t.quote_timestamp_utc).dt.total_seconds()/3600
    bad=t.price_usd.le(0)|t.price_usd.isna()|lag.lt(0)|lag.gt(6)
    if bad.any():
        print('[WARN] Invalid or missing token as-of prices:',int(bad.sum()))
    t['valid_asof_quote']=~bad
    t.loc[bad,'price_usd']=np.nan
    t['log_return_continuous']=np.log(t.price_usd).groupby(t.asset).diff()
    tok_gap=t.groupby('asset').date.diff()
    t.loc[tok_gap.ne(pd.Timedelta(days=1)),'log_return_continuous']=np.nan
    t['realized_vol_30d_continuous_annualized']=(
        t.groupby('asset')['log_return_continuous']
        .transform(lambda v:v.rolling(30,min_periods=20).std()*np.sqrt(365))
    )
    # Daily stETH/ETH reference: USD stETH / (ETHUSDT quote * USDT/USD)
    # not an oracle ratio or executable Curve price; retrospective EOD only.
    eth=prices.loc[prices.pair.eq('ETHUSDT'),['timestamp_utc','close']].copy()
    eth=eth.rename(columns={'timestamp_utc':'date','close':'eth_usdt_close'})
    stable=t.loc[t.asset.eq('USDT'),['date','price_usd','quote_timestamp_utc']].rename(
        columns={'price_usd':'usdt_usd','quote_timestamp_utc':'usdt_quote_utc'})
    st=t.loc[t.asset.eq('STETH'),['date','price_usd','quote_timestamp_utc']].rename(
        columns={'price_usd':'steth_usd','quote_timestamp_utc':'steth_quote_utc'})
    rel=eth.merge(stable,on='date',how='left',validate='one_to_one').merge(
        st,on='date',how='left',validate='one_to_one')
    rel['eth_usd_implied']=rel.eth_usdt_close*rel.usdt_usd
    rel['steth_eth_ratio_daily_ref']=rel.steth_usd/rel.eth_usd_implied
    rel['steth_discount_pct_daily_ref']=(1-rel.steth_eth_ratio_daily_ref)*100
    rel['data_scope']='Daily close reference: asynchronous as-of token quotes, NOT same-block Oracle or DEX spot'
    write_parquet_once(FINAL/'market_daily_continuous.parquet',prices)
    write_parquet_once(FINAL/'token_prices_continuous.parquet',t)
    p=FINAL/'n3_daily_steth_eth_reference.csv'
    if p.exists():raise FileExistsError(p)
    rel.to_csv(p,index=False)
    dump_once(FINAL/'n3_market_recalculation_quality.json',{
        'market_daily_rows':int(len(prices)), 'token_rows':int(len(t)),
        'token_missing_or_invalid_asof_prices':int(bad.sum()),
        'daily_depeg_rows_with_both_prices':int(rel.steth_eth_ratio_daily_ref.notna().sum()),
        'method':'Continuous within each token/pair across year boundaries; gap days blank',
        'no_lookahead':True,'not_all_market_volume':True,
        'ratio_warning':'Daily reference; do not use as event-time/stress oracle price',
    })
    print('[MARKET REBUILT] Continuous returns and daily descriptive stETH/ETH reference')


def join_observed_market():
    """Optional same-day retrospective association, never look-ahead for event-level valuation."""
    import pandas as pd
    ref=FINAL/'n3_daily_steth_eth_reference.csv'
    obs=FINAL/'n3_liquidations_daily.csv'
    if not ref.exists() or not obs.exists():
        return
    lhs=pd.read_csv(obs,parse_dates=['bin_utc'])
    rhs=pd.read_csv(ref,parse_dates=['date'])
    lhs['date']=pd.to_datetime(lhs.bin_utc,utc=True)
    rhs['date']=pd.to_datetime(rhs.date,utc=True)
    keep=['date','eth_usdt_close','usdt_usd','steth_usd','steth_eth_ratio_daily_ref','steth_discount_pct_daily_ref']
    joined=lhs.merge(rhs[keep],on='date',how='left',validate='many_to_one')
    path=FINAL/'n3_liquidation_market_daily.csv'
    if path.exists():return
    joined.to_csv(path,index=False)
    print('[MARKET LINK]',path)

def command_concentration(args):
    """M6: Concentration A/B1/B2 from N2 positions and HF."""
    from pathlib import Path
    from datetime import datetime, timezone
    import json
    import math
    import shutil

    import numpy as np
    import pandas as pd
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    root = Path(__file__).resolve().parents[1]
    n2 = root / "data/processed/aave"

    paths = {
        "positions": n2 / "positions_long.parquet",
        "hf": n2 / "hf_reconciliation.parquet",
        "metadata": n2 / "snapshot_metadata.json",
    }

    for name, path in paths.items():
        if not path.is_file():
            raise FileNotFoundError(f"M6 missing {name}: {path}")

    meta = json.loads(
        paths["metadata"].read_text(encoding="utf-8")
    )

    expected = {
        "A": 26179841,
        "B1": 20458637,
        "B2": 23543615,
    }

    snaps = meta.get("snapshots", {})
    if set(snaps) != set(expected):
        raise ValueError(
            f"Metadata snapshots {sorted(snaps)} != A/B1/B2"
        )

    for sid, block in expected.items():
        if int(snaps[sid]["snapshot_block"]) != block:
            raise ValueError(f"Incorrect block for {sid}")
        if snaps[sid].get("block_hash_verified_on_chain") is not True:
            raise ValueError(f"Unverified block: {sid}")

    pos = pd.read_parquet(paths["positions"])
    hf = pd.read_parquet(paths["hf"])

    print("[M6] N2 positions:", len(pos))
    print("[M6] N2 HF:", len(hf))

    def choose(df, candidates, description):
        found = [x for x in candidates if x in df.columns]
        if not found:
            raise ValueError(
                f"Missing {description}. "
                f"Expected one of {candidates}. "
                f"Available: {list(df.columns)}"
            )
        return found[0]

    def prepare_snapshot(df, name):
        df = df.copy()

        if "snapshot_id" in df.columns:
            df["snapshot_id"] = (
                df["snapshot_id"].astype(str).str.strip()
            )
        elif "snapshot_block" in df.columns:
            reverse = {v: k for k, v in expected.items()}
            df["snapshot_id"] = pd.to_numeric(
                df["snapshot_block"], errors="coerce"
            ).map(reverse)
        else:
            raise ValueError(
                f"{name}: no snapshot_id or snapshot_block"
            )

        if set(df["snapshot_id"].dropna()) != set(expected):
            raise ValueError(
                f"{name}: wrong snapshots "
                f"{df['snapshot_id'].value_counts().to_dict()}"
            )

        if df["snapshot_id"].isna().any():
            raise ValueError(f"{name}: unidentified snapshots")

        if "snapshot_block" in df.columns:
            block = pd.to_numeric(
                df["snapshot_block"], errors="coerce"
            )
            correct = df["snapshot_id"].map(expected)
            if not (block == correct).all():
                raise ValueError(
                    f"{name}: snapshot_id/block mismatch"
                )

        df["snapshot_block"] = df["snapshot_id"].map(expected)
        return df

    pos = prepare_snapshot(pos, "positions_long")
    hf = prepare_snapshot(hf, "hf_reconciliation")

    wallet_pos = choose(
        pos, ["wallet_address", "user_address", "wallet"],
        "positions wallet"
    )
    wallet_hf = choose(
        hf, ["wallet_address", "user_address", "wallet"],
        "HF wallet"
    )
    asset_col = choose(
        pos,
        ["token_address", "asset_address",
         "reserve_address", "underlying_asset",
         "asset_symbol", "symbol"],
        "position asset"
    )
    debt_col = choose(
        pos,
        ["debt_usd", "debt_value_usd",
         "borrow_usd", "borrow_value_usd"],
        "asset-level debt USD"
    )
    collateral_col = choose(
        pos,
        ["collateral_usd", "collateral_value_usd",
         "collateral_balance_usd"],
        "collateral USD"
    )
    hf_col = choose(
        hf,
        ["health_factor_reported", "health_factor_aave",
         "hf_aave", "hf_reported",
         "health_factor_onchain", "aave_hf",
         "onchain_hf", "health_factor"],
        "Health Factor from N2"
    )

    pos["wallet"] = (
        pos[wallet_pos].astype(str).str.lower().str.strip()
    )
    hf["wallet"] = (
        hf[wallet_hf].astype(str).str.lower().str.strip()
    )
    pos["asset"] = (
        pos[asset_col].astype(str).str.lower().str.strip()
    )

    for df, name in [(pos, "positions"), (hf, "HF")]:
        if df["wallet"].isin(["", "nan", "none"]).any():
            raise ValueError(f"{name}: missing wallet address")

    pos["debt_usd_m6"] = pd.to_numeric(
        pos[debt_col], errors="coerce"
    )
    pos["collateral_usd_m6"] = pd.to_numeric(
        pos[collateral_col], errors="coerce"
    )

    for col in ["debt_usd_m6", "collateral_usd_m6"]:
        values = pos[col]
        if (values.isna().any()
                or not np.isfinite(values.to_numpy()).all()
                or (values < 0).any()):
            raise ValueError(
                f"{col}: missing, negative or invalid values"
            )

    collateral_flag = None
    for name in [
        "counted_collateral",
        "is_collateral_enabled",
        "usage_as_collateral_enabled",
        "collateral_enabled",
    ]:
        if name in pos.columns:
            collateral_flag = name
            break

    if collateral_flag:
        raw = pos[collateral_flag].astype(str).str.lower()
        mapping = {
            "true": True, "false": False,
            "1": True, "0": False,
        }
        counted = raw.map(mapping)
        if counted.isna().any():
            raise ValueError(
                f"Cannot interpret collateral flag: "
                f"{collateral_flag}"
            )
        pos["counted_usd_m6"] = (
            pos["collateral_usd_m6"] * counted.astype(float)
        )
        collateral_note = (
            f"Counted collateral only; flag={collateral_flag}"
        )
    else:
        pos["counted_usd_m6"] = pos["collateral_usd_m6"]
        collateral_note = (
            "No counted collateral flag; using collateral_usd. "
            "Must verify definition with N2."
        )

    # Aggregate first by wallet; never use repeated wallet-level
    # total debt from each asset row.
    wallets = pos.groupby(
        ["snapshot_id", "snapshot_block", "wallet"],
        as_index=False
    ).agg(
        debt_usd=("debt_usd_m6", "sum"),
        counted_collateral_usd=("counted_usd_m6", "sum"),
        asset_count=("asset", "nunique"),
    )

    # Borrower concentration is based on wallets with debt > 0.
    wallets = wallets.loc[wallets.debt_usd > 0].copy()

    if wallets.empty:
        raise ValueError("No borrowers with positive debt")

    hf["hf_value"] = pd.to_numeric(
        hf[hf_col], errors="coerce"
    )

    # Reconciliation files may contain repeated wallet rows;
    # conflicting finite HF observations must not be silently averaged.
    checks = hf.groupby(
        ["snapshot_id", "wallet"]
    )["hf_value"].agg(["min", "max", "count"])

    mismatch = (
        (checks["count"] > 1)
        & (checks["max"] - checks["min"] > 1e-5)
    )
    if mismatch.any():
        raise ValueError(
            "HF values disagree within same snapshot/wallet"
        )

    hf_unique = hf.sort_values(
        ["snapshot_id", "wallet"]
    ).drop_duplicates(
        ["snapshot_id", "wallet"]
    )[["snapshot_id", "wallet", "hf_value"]]

    if not hf_unique["hf_value"].notna().any():
        raise ValueError("No numerical HF values in N2 file")

    wallets = wallets.merge(
        hf_unique,
        on=["snapshot_id", "wallet"],
        how="left",
        validate="one_to_one",
    )

    def hf_bucket(x):
        if pd.isna(x):
            return "NA"
        if x < 1:
            return "HF < 1"
        if x < 1.1:
            return "1 <= HF < 1.1"
        if x < 1.5:
            return "1.1 <= HF < 1.5"
        return "HF >= 1.5"

    wallets["hf_group"] = wallets["hf_value"].map(hf_bucket)

    wallets["wallet_debt_share"] = (
        wallets["debt_usd"]
        / wallets.groupby("snapshot_id")["debt_usd"]
            .transform("sum")
    )

    wallets["debt_rank"] = wallets.groupby(
        "snapshot_id"
    )["debt_usd"].rank(
        method="first", ascending=False
    ).astype(int)

    assets = pos.groupby(
        ["snapshot_id", "snapshot_block", "asset"],
        as_index=False
    ).agg(
        debt_usd=("debt_usd_m6", "sum"),
        counted_collateral_usd=("counted_usd_m6", "sum"),
        wallet_count=("wallet", "nunique"),
    )

    for metric in ["debt_usd", "counted_collateral_usd"]:
        total = assets.groupby("snapshot_id")[metric].transform("sum")
        assets[metric + "_share"] = np.where(
            total > 0, assets[metric] / total, 0.0
        )

    risk = wallets.groupby(
        ["snapshot_id", "snapshot_block", "hf_group"],
        as_index=False
    ).agg(
        wallet_count=("wallet", "nunique"),
        debt_usd=("debt_usd", "sum"),
    )
    risk["wallet_share_pct"] = (
        risk["wallet_count"]
        / risk.groupby("snapshot_id")["wallet_count"]
              .transform("sum") * 100
    )

    summaries = []
    for sid in expected:
        sub = wallets.loc[
            wallets.snapshot_id.eq(sid)
        ].sort_values("debt_usd", ascending=False)

        if sub.empty:
            raise ValueError(f"No positive-debt wallets in {sid}")

        total = float(sub.debt_usd.sum())
        shares = sub.debt_usd / total

        summaries.append({
            "snapshot_id": sid,
            "snapshot_block": expected[sid],
            "borrower_wallets": int(len(sub)),
            "total_debt_usd": total,
            "total_counted_collateral_usd":
                float(sub.counted_collateral_usd.sum()),
            "top10_wallet_debt_share":
                float(sub.head(10).debt_usd.sum() / total),
            "top50_wallet_debt_share":
                float(sub.head(50).debt_usd.sum() / total),
            "wallet_debt_hhi":
                float((shares ** 2).sum()),
            "hf_coverage_pct":
                float(sub.hf_value.notna().mean() * 100),
            "wallets_hf_below_1":
                int(sub.hf_value.lt(1).sum()),
        })

    summary = pd.DataFrame(summaries)

    quality = {
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "scope": "Aave V3 Ethereum - N2 snapshots A/B1/B2",
        "approved_blocks": expected,
        "n_positions_rows": int(len(pos)),
        "n_hf_rows": int(len(hf)),
        "wallets_by_snapshot": dict(
            zip(summary.snapshot_id, summary.borrower_wallets)
        ),
        "source_columns": {
            "wallet_positions": wallet_pos,
            "wallet_hf": wallet_hf,
            "asset": asset_col,
            "debt_usd": debt_col,
            "collateral_usd": collateral_col,
            "hf": hf_col,
        },
        "collateral_rule": collateral_note,
        "limitations": [
            "HHI is borrower debt concentration, not liquidation probability.",
            "HF < 1 includes dust positions; apply debt thresholds separately.",
            "Snapshot A and historical B1/B2 are distinct time points.",
            "No claim of 100% protocol debt coverage without N2 source QA.",
            "No simulated liquidation or DEX depth included in this module."
        ],
    }

    # Save old outputs separately before replacing any same-name file.
    table_dir = root / "outputs/tables"
    figure_dir = root / "outputs/figures"
    final_dir = (
        root / "data/n3_history_2024_2026/"
        "final/concentration_AB1B2"
    )

    table_dir.mkdir(parents=True, exist_ok=True)
    figure_dir.mkdir(parents=True, exist_ok=True)
    final_dir.mkdir(parents=True, exist_ok=True)

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup = Path.home() / "Downloads" / f"N3_M6_BACKUP_{stamp}"

    def protect(path):
        if path.exists():
            dest = backup / path.relative_to(root)
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, dest)
            print("[BACKUP]", dest)

    outputs = {
        final_dir / "concentration_by_wallet.parquet": wallets,
        final_dir / "concentration_by_asset.parquet": assets,
        final_dir / "hf_risk_distribution.parquet": risk,
    }

    for path, df in outputs.items():
        protect(path)
        df.to_parquet(path, index=False)
        print("[SAVED]", path.relative_to(root))

    csv_outputs = {
        table_dir / "n3_m6_summary_AB1B2.csv": summary,
        table_dir / "n3_m6_top50_wallets_AB1B2.csv":
            wallets.loc[wallets.debt_rank <= 50],
        table_dir / "n3_m6_assets_AB1B2.csv": assets,
        table_dir / "n3_m6_hf_AB1B2.csv": risk,
    }

    for path, df in csv_outputs.items():
        protect(path)
        df.to_csv(path, index=False, encoding="utf-8-sig")
        print("[SAVED]", path.relative_to(root))

    def save_figure(name):
        path = figure_dir / name
        protect(path)
        plt.tight_layout()
        plt.savefig(path, dpi=180, bbox_inches="tight")
        plt.close()
        print("[FIGURE]", path.relative_to(root))

    # Debt concentration by asset
    top_assets = (
        assets.groupby("asset")["debt_usd"]
        .sum().nlargest(12).index
    )
    chart = assets.loc[
        assets.asset.isin(top_assets)
    ].pivot_table(
        index="asset",
        columns="snapshot_id",
        values="debt_usd",
        aggfunc="sum",
        fill_value=0,
    )
    chart = chart.reindex(columns=list(expected), fill_value=0)
    ax = (chart / 1e6).plot.bar(figsize=(12, 6))
    ax.set_title("Debt by asset and snapshot")
    ax.set_ylabel("Debt (million USD)")
    ax.set_xlabel("Asset")
    save_figure("n3_m6_asset_concentration_AB1B2.png")

    # Health Factor distribution
    chart = risk.pivot_table(
        index="hf_group",
        columns="snapshot_id",
        values="wallet_share_pct",
        aggfunc="sum",
        fill_value=0,
    )
    chart = chart.reindex(
        ["HF < 1", "1 <= HF < 1.1",
         "1.1 <= HF < 1.5", "HF >= 1.5", "NA"],
        fill_value=0,
    )
    chart = chart.reindex(columns=list(expected), fill_value=0)
    ax = chart.plot.bar(figsize=(11, 6))
    ax.set_title("Borrower Health Factor distribution")
    ax.set_ylabel("% of borrowing wallets")
    ax.set_xlabel("Health Factor")
    save_figure("n3_m6_hf_distribution_AB1B2.png")

    # Debt concentration Top 10 per snapshot
    for sid in expected:
        top = wallets.loc[
            wallets.snapshot_id.eq(sid)
        ].nlargest(10, "debt_usd")

        labels = [
            f"Wallet {i}" for i in range(1, len(top) + 1)
        ]
        fig, ax = plt.subplots(figsize=(10, 5))
        ax.bar(labels, top.debt_usd.to_numpy() / 1e6)
        ax.set_title(f"Top 10 borrowing wallets - {sid}")
        ax.set_ylabel("Debt (million USD)")
        ax.tick_params(axis="x", rotation=45)
        save_figure(f"n3_m6_top10_wallets_{sid}.png")

    qa_path = table_dir / "n3_m6_quality_AB1B2.json"
    protect(qa_path)
    qa_path.write_text(
        json.dumps(quality, indent=2, ensure_ascii=False),
        encoding="utf-8"
    )

    print("\n====== M6 COMPLETED ======")
    print(summary.to_string(index=False))
    print("[QA]", qa_path.relative_to(root))
    print("[NOTE] Old concentration_2026 remains unchanged.")



def command_status(args):
    print('[PROJECT]',ROOT)
    for tag, start, end in [(x,*y) for x,y in PERIODS.items()]:
        wd = LIQ/tag
        raw=wd/'data/staging/n3_liquidations_etherscan.parquet'
        priced=wd/'data/staging/n3_liquidations_priced.parquet'
        print(tag,'raw=',raw.exists(),'priced=',priced.exists())
    for file in sorted(FINAL.glob('n3_*')):
        print('[FINAL]',file.name)
    for f in ['src/data/fetch_liquidations.py','src/data/price_liquidations_usd.py','src/data/fetch_dex_liquidity.py']:
        print('[SOURCE]',f,(ROOT/f).exists())
    print('[COMPOUND] Outside all writable output paths')


def command_dex_preflight(args):
    import pandas as pd
    if not args.metadata or not args.oracle or not args.lido or not args.registry:
        raise ValueError('Provide --metadata --oracle --lido --registry')
    metadata=json.loads(Path(args.metadata).read_text(encoding='utf-8'))
    if set(metadata.get('snapshots',{})) != {'A','B'}:
        raise ValueError('Existing DEX code supports exactly two labels A and B')
    blocks={int(v['snapshot_block']) for v in metadata['snapshots'].values()}
    approved={int(v.strip()) for v in args.approved_blocks.split(',')}
    if blocks!=approved:
        raise ValueError('Approved blocks do not match metadata')
    if not all(v.get('block_hash_verified_on_chain',False) for v in metadata['snapshots'].values()):
        raise ValueError('Block hash must be verified on-chain')
    for path in [args.oracle,args.lido]:
        df=pd.read_parquet(path)
        if 'snapshot_block' not in df:
            raise ValueError(f'Missing snapshot_block in {path}')
        present=set(pd.to_numeric(df.snapshot_block,errors='raise').astype('int64'))
        if present != blocks:
            raise ValueError(f'{path}: historical oracle/ratio block mismatch {present} vs {blocks}')
    if not Path(args.registry).is_file():
        raise FileNotFoundError(args.registry)
    print('[DEX PREFLIGHT PASS] Approved blocks:',sorted(blocks))
    print('Use existing src/data/fetch_dex_liquidity.py with isolated historical workspace')
    print('IMPORTANT: validate historic pool deployment, reserve/fee tier, and actual archive eth_call')



def command_dex_run(args):
    """Run unchanged N3 2%-depth implementation in a separate historical workspace."""
    import re
    if not re.fullmatch('[a-zA-Z0-9_-]{1,65}',args.tag):
        raise ValueError('Invalid tag')
    command_dex_preflight(args)
    src=ROOT/'src/data/fetch_dex_liquidity.py'
    config=Path(args.pools)
    if not src.is_file() or not config.is_file():
        raise FileNotFoundError('Missing N3 DEX source or pool config')
    work=HISTORY/'dex'/args.tag
    planned={
        'src/data/fetch_dex_liquidity.py':src,
        'config/n3_dex_pools.csv':config,
        'data/processed/aave/snapshot_metadata.json':Path(args.metadata),
        'data/processed/aave/oracle_prices.parquet':Path(args.oracle),
        'data/processed/aave/lido_ratios.parquet':Path(args.lido),
        'data/processed/aave/token_registry.csv':Path(args.registry),
    }
    output=work/'data/staging/n3_dex_liquidity.parquet'
    qa=work/'outputs/tables/n3_dex_liquidity_quality.json'
    if output.exists() or qa.exists():
        raise FileExistsError('Historical DEX output already exists; refusing replacement')
    for relative,source in planned.items():
        dst=work/relative
        if dst.exists() and sha256(dst)!=sha256(source):
            raise ValueError(f'Historical DEX input changed: {dst}; use new tag')
        if not dst.exists():
            dst.parent.mkdir(parents=True,exist_ok=True)
            shutil.copy2(source,dst)
    command=[sys.executable,str(work/'src/data/fetch_dex_liquidity.py')]
    env=env_for_run()
    if not (env.get('ETHEREUM_ARCHIVE_RPC_URL') or env.get('ETH_RPC_URL')):
        if args.execute:raise ValueError('Historical DEX requires Archive RPC URL in original .env')
    if args.execute:
        command+=['--require-complete']
        print('[DEX RPC RUN] Exact approved historical blocks; may be time-consuming')
    else:
        command+=['--dry-run']
        print('[DEX DRY RUN] No RPC calls for depth')
    subprocess.run(command,cwd=work,env=env,check=True)
    if args.execute and (not output.exists() or not qa.exists()):
        raise RuntimeError('DEX execution did not save expected result + QA')
    print('[DEX WORKSPACE]',work)

def main():
    ap=argparse.ArgumentParser(description='N3 add-on: Aave historical liquidations, QA, M5 and 2026 M6')
    sub=ap.add_subparsers(dest='cmd',required=True)
    e=sub.add_parser('events',help='Collect/price the 2024/2025/2026 event windows')
    e.add_argument('--tags',nargs='*',default=list(PERIODS),choices=list(PERIODS))
    e.add_argument('--raw-only',action='store_true')
    e.add_argument('--dry-run',action='store_true')
    m=sub.add_parser('months',help='Optional full monthly history, expensive')
    m.add_argument('--start',required=True);m.add_argument('--end',required=True)
    m.add_argument('--max-periods',type=int,default=1,help='Safety cap per run: default one month')
    m.add_argument('--raw-only',action='store_true');m.add_argument('--dry-run',action='store_true')
    a=sub.add_parser('analyze',help='Consolidate USD priced liquidation logs, M5, and QA')
    a.add_argument('--tags',nargs='*',default=None)
    a.add_argument('--require-full-prices',action='store_true')
    c=sub.add_parser('concentration',help='M6 on supplied Aave positions (2026 only)')
    c.add_argument('--positions',default=str(ROOT/'data/processed/aave/positions_long.parquet'))
    sub.add_parser('market-final',help='Recalculate returns/vol across years, daily stETH/ETH reference')
    sub.add_parser('status')
    d=sub.add_parser('dex-preflight',help='Validate event block provenance; NO DEX queries')
    d.add_argument('--metadata',type=Path);d.add_argument('--oracle',type=Path)
    d.add_argument('--lido',type=Path);d.add_argument('--registry',type=Path)
    d.add_argument('--approved-blocks',required=True)
    dex=sub.add_parser('dex-run',help='Isolated historical Curve/UniV3 measurement; requires approved blocks')
    dex.add_argument('--tag',required=True)
    dex.add_argument('--metadata',type=Path,required=True)
    dex.add_argument('--oracle',type=Path,required=True)
    dex.add_argument('--lido',type=Path,required=True)
    dex.add_argument('--registry',type=Path,required=True)
    dex.add_argument('--pools',type=Path,default=ROOT/'config/n3_dex_pools.csv')
    dex.add_argument('--approved-blocks',required=True)
    dex.add_argument('--execute',action='store_true',help='Actually run archive RPC; absent => dry run')
    args=ap.parse_args()
    if args.cmd=='events':
        for tag in args.tags:
            start,end=PERIODS[tag]
            run_period(tag,start,end,not args.raw_only,args.dry_run)
    elif args.cmd=='months':
        periods=list(monthly_periods(args.start,args.end))
        if args.max_periods<1:ap.error('max-periods must be positive')
        print('[MONTHS]',len(periods),'periods requested; cap',args.max_periods)
        for tag,a,b in periods[:args.max_periods]:
            run_period(tag,a,b,not args.raw_only,args.dry_run)
        if len(periods)>args.max_periods:
            print('[NEXT] Resume next months with later --start date after QA; do not claim full coverage')
    elif args.cmd=='analyze':command_analyze(args)
    elif args.cmd=='concentration':command_concentration(args)
    elif args.cmd=='market-final':command_market_final(args)
    elif args.cmd=='status':command_status(args)
    elif args.cmd=='dex-preflight':command_dex_preflight(args)
    elif args.cmd=='dex-run':command_dex_run(args)

if __name__=='__main__':
    main()
