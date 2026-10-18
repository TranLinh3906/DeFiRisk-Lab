"""N3 / Muc 3: Market prices + market ratios + optional exact-block Aave/Lido data.

DEFAULT: reads existing data/raw/market/prices_raw.csv from fetch_prices.py and
writes data/staging/n3_price_timeseries.parquet, WITHOUT fabricating oracle prices.

Option A (from N2): --oracle-csv /path/oracle_prices.csv --lido-csv /path/lido_rates.csv
Option B (archive RPC): --oracle-snapshots config/n3_oracle_snapshots.csv
                        --rpc-url via .env ETHEREUM_ARCHIVE_RPC_URL

Market ratios are *not* Aave oracle prices. For 2024 data, CoinGecko Demo
may not offer adequate historical range/granularity.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import requests
from dotenv import load_dotenv
from finalize_liquidations import safe_write_df, safe_write_json

ROOT = Path(__file__).resolve().parents[2]
WSTETH_MAINNET = '0x7f39c581f595b53c5cb19bd0b3f8da6c935e2ca0'
EVM_ADDRESS = re.compile(r'^0x[0-9a-f]{40}$')

COLUMNS = [
    'timestamp', 'asset', 'token_address', 'quote_asset', 'price_value',
    'price_type', 'source', 'snapshot_block', 'reference_timestamp',
    'staleness_seconds', 'note'
]


def addr(value):
    value = str(value).strip().lower()
    if not EVM_ADDRESS.fullmatch(value):
        raise ValueError(f'Invalid Ethereum address: {value}')
    return value


def market_rows(raw: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    needed = {'timestamp', 'asset', 'price_usd'}
    if needed - set(raw.columns):
        raise ValueError(f'prices_raw.csv missing: {needed - set(raw.columns)}')
    data = raw.copy()
    # Keep the original provider identity: no source is relabeled as an Aave oracle.
    if 'source' not in data.columns:
        data['source'] = 'Unspecified market CSV provider (audit required)'
    if 'token_address' not in data.columns:
        data['token_address'] = None
    data['timestamp'] = pd.to_datetime(data['timestamp'], utc=True, errors='coerce')
    data['asset'] = data['asset'].astype(str).str.upper().str.strip()
    data['price_usd'] = pd.to_numeric(data['price_usd'], errors='coerce')
    if data['timestamp'].isna().any() or data['price_usd'].isna().any() or (data['price_usd'] <= 0).any():
        raise ValueError('Invalid or missing timestamp/price in market data')
    if data.duplicated(['asset', 'timestamp']).any():
        raise ValueError('Duplicate asset+timestamp in market input; inspect fetch_prices.py output')
    data = data.sort_values(['asset', 'timestamp']).reset_index(drop=True)
    base = pd.DataFrame({
        'timestamp': data.timestamp,
        'asset': data.asset,
        'token_address': data.token_address,  # CoinGecko rows remain NULL; DefiLlama uses contract addresses
        'quote_asset': 'USD',
        'price_value': data.price_usd,
        'price_type': 'market_reference',
        'source': data.source,
        'snapshot_block': pd.NA,
        'reference_timestamp': data.timestamp,
        'staleness_seconds': 0.0,
        'note': 'Historical market reference, not Aave oracle or on-chain execution price',
    })
    return base, {
        'market_rows': len(base),
        'assets': sorted(data.asset.unique().tolist()),
        'market_min_utc': str(data.timestamp.min()),
        'market_max_utc': str(data.timestamp.max()),
    }


def ratio_rows(raw: pd.DataFrame, lag_minutes: int = 90) -> pd.DataFrame:
    """Compute ratio only when numerator has an earlier/same reasonably fresh USD quote.

    Each output timestamp is numerator quote time; denominator price timestamp
    and age are explicit. This does NOT derive a historical contract exchange rate.
    """
    price = raw[['timestamp', 'asset', 'price_usd']].copy()
    price['timestamp'] = pd.to_datetime(price['timestamp'], utc=True, errors='raise')
    price['asset'] = price['asset'].astype(str).str.strip().str.upper()
    price['price_usd'] = pd.to_numeric(price['price_usd'], errors='raise')
    pieces = []
    for numerator, denominator in [('STETH', 'ETH'), ('WSTETH', 'ETH'), ('WSTETH', 'STETH')]:
        nu = price.loc[price.asset == numerator, ['timestamp', 'price_usd']].rename(columns={'price_usd':'nu_price'}).sort_values('timestamp')
        de = price.loc[price.asset == denominator, ['timestamp', 'price_usd']].rename(columns={'timestamp':'ref_ts', 'price_usd':'de_price'}).sort_values('ref_ts')
        if nu.empty or de.empty:
            continue
        joined = pd.merge_asof(nu, de, left_on='timestamp', right_on='ref_ts',
                               direction='backward', tolerance=pd.Timedelta(minutes=lag_minutes))
        joined = joined.dropna(subset=['de_price'])
        joined = joined[(joined.nu_price > 0) & (joined.de_price > 0)]
        if joined.empty:
            continue
        result = pd.DataFrame({
            'timestamp': joined.timestamp,
            'asset': numerator,
            'token_address': None,
            'quote_asset': denominator,
            'price_value': joined.nu_price / joined.de_price,
            'price_type': 'market_ratio',
            'source': 'Relative ratio from paired market USD references in same input provider',
            'snapshot_block': pd.NA,
            'reference_timestamp': joined.ref_ts,
            'staleness_seconds': (joined.timestamp - joined.ref_ts).dt.total_seconds(),
            'note': 'Relative market USD quotes joined backward, NOT Lido conversion or Aave oracle; underlying chart sample time may need provider verification',
        })
        pieces.append(result)
    return pd.concat(pieces, ignore_index=True) if pieces else pd.DataFrame(columns=COLUMNS)


def manual_oracle_rows(path: Path) -> pd.DataFrame:
    """Accepted only if N2 has provided observed oracle USD/block evidence."""
    df = pd.read_csv(path)
    required = {'snapshot_block','snapshot_timestamp','asset','token_address','oracle_price_usd','source'}
    if required - set(df.columns):
        raise ValueError(f'Oracle CSV missing {required - set(df.columns)}')
    df['snapshot_timestamp'] = pd.to_datetime(df.snapshot_timestamp, utc=True, errors='raise')
    df['token_address'] = df.token_address.map(addr)
    df['snapshot_block'] = pd.to_numeric(df.snapshot_block, errors='raise').astype('int64')
    df['oracle_price_usd'] = pd.to_numeric(df.oracle_price_usd, errors='raise')
    if (df.oracle_price_usd <= 0).any() or df.source.isna().any():
        raise ValueError('Oracle data need positive on-chain prices and nonempty source')
    if df.duplicated(['snapshot_block','token_address']).any():
        raise ValueError('Duplicate snapshot+token in oracle CSV')
    return pd.DataFrame({
        'timestamp': df.snapshot_timestamp, 'asset': df.asset.astype(str).str.upper(),
        'token_address': df.token_address, 'quote_asset': 'USD',
        'price_value': df.oracle_price_usd, 'price_type': 'aave_oracle',
        'source': df.source.astype(str), 'snapshot_block': df.snapshot_block,
        'reference_timestamp': df.snapshot_timestamp, 'staleness_seconds': 0.0,
        'note': 'Provided by N2 CSV; archive block evidence and source provenance require independent review'
    })


def manual_lido_rows(path: Path) -> pd.DataFrame:
    df = pd.read_csv(path)
    required = {'snapshot_block','snapshot_timestamp','steth_per_wsteth','source'}
    if required - set(df.columns):
        raise ValueError(f'Lido CSV missing {required - set(df.columns)}')
    df['snapshot_timestamp'] = pd.to_datetime(df.snapshot_timestamp, utc=True, errors='raise')
    df['snapshot_block'] = pd.to_numeric(df.snapshot_block, errors='raise').astype('int64')
    df['steth_per_wsteth'] = pd.to_numeric(df.steth_per_wsteth, errors='raise')
    if (df.steth_per_wsteth <= 0).any() or df.source.isna().any():
        raise ValueError('Invalid Lido conversion values/source')
    if df.duplicated(['snapshot_block']).any():
        raise ValueError('Duplicate block in Lido conversion CSV')
    return pd.DataFrame({
        'timestamp': df.snapshot_timestamp, 'asset': 'WSTETH',
        'token_address': WSTETH_MAINNET, 'quote_asset': 'STETH',
        'price_value': df.steth_per_wsteth, 'price_type': 'lido_contract_conversion',
        'source': df.source.astype(str), 'snapshot_block': df.snapshot_block,
        'reference_timestamp': df.snapshot_timestamp, 'staleness_seconds': 0.0,
        'note': 'Reported as Lido stEthPerToken() by N2 CSV; requires independent archive evidence',
    })


def selector(signature: str) -> str:
    """Ethereum uses Keccak-256 (not hashlib.sha3_256) for function selectors."""
    try:
        from web3 import Web3
    except ImportError as e:
        raise RuntimeError('RPC mode requires web3; install src/data/requirements_n2.txt') from e
    return Web3.keccak(text=signature).hex()[:10]


def rpc_call(url: str, method: str, params: list):
    # Do not expose RPC URL or auth token in error text.
    payload = {'jsonrpc':'2.0', 'id':1, 'method': method, 'params': params}
    try:
        r = requests.post(url, json=payload, timeout=45)
        r.raise_for_status()
        result = r.json()
        if result.get('error'):
            raise ValueError('RPC returned error (historical block not available or bad call)')
        if 'result' not in result or result['result'] is None:
            raise ValueError('Missing RPC result')
        return result['result']
    except (requests.RequestException, ValueError, KeyError) as exc:
        raise RuntimeError(f'RPC {method} failed. Check your archive RPC access/block/contract; key/URL not printed. ({type(exc).__name__})') from None


def rpc_eth_call(url: str, contract_address: str, calldata: str, block: int) -> int:
    value = rpc_call(url, 'eth_call', [{'to': addr(contract_address), 'data': calldata}, hex(int(block))])
    if not isinstance(value, str) or not value.startswith('0x') or value == '0x':
        raise RuntimeError('RPC eth_call returned invalid data')
    return int(value, 16)


def rpc_block_timestamp(url: str, block: int) -> pd.Timestamp:
    value = rpc_call(url, 'eth_getBlockByNumber', [hex(int(block)), False])
    if not isinstance(value, dict) or 'timestamp' not in value:
        raise RuntimeError(f'RPC does not serve historical block {block}')
    return pd.to_datetime(int(value['timestamp'], 16), unit='s', utc=True)


def rpc_snapshot_rows(snapshots_path: Path, rpc_url: str, max_feed_age_hours: float = 24) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Real Aave getAssetPrice at each block + Lido stEthPerToken at each block.

    snapshot CSV: snapshot_block,asset,token_address,aave_oracle_address
    Optional: snapshot_timestamp (used only to cross-check actual block timestamp).
    Ethereum Mainnet Aave V3 USD base is 1e8; reject any other base to avoid wrong USD.
    """
    inp = pd.read_csv(snapshots_path)
    required = {'snapshot_block', 'asset', 'token_address', 'aave_oracle_address'}
    if required - set(inp.columns):
        raise ValueError(f'Snapshot list missing {required - set(inp.columns)}')
    if inp.empty:
        raise ValueError('Snapshot list has no rows; ask N2 for actual block and oracle address')
    inp['snapshot_block'] = pd.to_numeric(inp.snapshot_block, errors='raise').astype('int64')
    inp['token_address'] = inp.token_address.map(addr)
    inp['aave_oracle_address'] = inp.aave_oracle_address.map(addr)
    if inp.duplicated(['snapshot_block','token_address']).any():
        raise ValueError('Duplicate block+token in snapshot list')
    select_price = selector('getAssetPrice(address)')
    select_unit = selector('BASE_CURRENCY_UNIT()')
    select_base = selector('BASE_CURRENCY()')
    select_ratio = selector('stEthPerToken()')
    oracle_records, lido_records, chainlink_records = [], [], []
    for block, group in inp.groupby('snapshot_block', sort=True):
        ts = rpc_block_timestamp(rpc_url, int(block))
        if 'snapshot_timestamp' in group.columns:
            provided = group.snapshot_timestamp.notna() & group.snapshot_timestamp.astype(str).str.strip().ne('')
            if provided.any():
                declared = pd.to_datetime(group.loc[provided, 'snapshot_timestamp'], utc=True, errors='coerce')
                if declared.isna().any() or any(abs((t - ts).total_seconds()) > 60 for t in declared):
                    raise ValueError(f'Block timestamp mismatch at block {block}; check N2 snapshot information')
        for row in group.itertuples(index=False):
            base_currency = rpc_eth_call(rpc_url, row.aave_oracle_address, select_base, int(block))
            if base_currency != 0:
                raise RuntimeError(
                    f'Block {block} oracle BASE_CURRENCY={base_currency:#x}, not Aave USD zero-address base.'
                )
            unit = rpc_eth_call(rpc_url, row.aave_oracle_address, select_unit, int(block))
            if unit != 100_000_000:
                raise RuntimeError(f'Block {block} oracle BASE_CURRENCY_UNIT={unit}, not USD 1e8. Do not label as USD.')
            raw = rpc_eth_call(rpc_url, row.aave_oracle_address,
                               select_price + row.token_address[2:].rjust(64, '0'), int(block))
            if raw <= 0:
                raise RuntimeError(f'Oracle price zero at block {block} for {row.asset}: cannot claim valid USD')
            oracle_records.append({
                'timestamp': ts, 'asset': str(row.asset).upper(), 'token_address': row.token_address,
                'quote_asset': 'USD', 'price_value': raw / unit, 'price_type': 'aave_oracle',
                'source': f'AaveOracle.getAssetPrice at Ethereum block {block}',
                'snapshot_block': int(block), 'reference_timestamp': ts,
                'staleness_seconds': 0.0, 'note': 'Aave oracle actual at block; source update age not independently checked'
            })
        # Optional direct Chainlink observation: feed and quote token MUST be
        # supplied by N2; Aave's selected source may differ from this feed.
        if 'chainlink_feed_address' in group.columns:
            for row in group.itertuples(index=False):
                feed = getattr(row, 'chainlink_feed_address', None)
                if pd.isna(feed) or not str(feed).strip():
                    continue
                feed = addr(feed)
                quote_ccy = getattr(row, 'chainlink_quote_asset', None)
                if pd.isna(quote_ccy) or str(quote_ccy).strip().upper() not in ('USD','ETH'):
                    raise ValueError('Chainlink feed quote_asset must be supplied as USD or ETH; do not guess')
                unit_decimals = rpc_eth_call(rpc_url, feed, selector('decimals()'), int(block))
                if not 0 <= unit_decimals <= 36:
                    raise ValueError(f'Chainlink feed bad decimals at block {block}')
                rd_hex = rpc_call(rpc_url, 'eth_call', [{'to': feed, 'data': selector('latestRoundData()')}, hex(int(block))])
                if not isinstance(rd_hex, str) or not rd_hex.startswith('0x') or len(rd_hex[2:]) < 320:
                    raise ValueError('Chainlink latestRoundData invalid encoded result')
                words = [int(rd_hex[2+i*64:2+(i+1)*64],16) for i in range(5)]
                round_id, unsigned_answer, started_at, updated_at, answered_in_round = words
                signed_answer = unsigned_answer if unsigned_answer < 2**255 else unsigned_answer - 2**256
                if signed_answer <= 0 or updated_at <= 0 or updated_at > int(ts.timestamp()):
                    raise ValueError('Chainlink invalid price or updatedAt > snapshot block timestamp')
                age = int(ts.timestamp()) - updated_at
                if age > max_feed_age_hours * 3600:
                    raise ValueError(f'Chainlink stale at block={block}, asset={row.asset}, age={age}s; not accepted')
                if answered_in_round < round_id:
                    raise ValueError(f'Chainlink answeredInRound < roundId for {row.asset}')
                chainlink_records.append({
                    'timestamp': ts, 'asset': str(row.asset).upper(), 'token_address': row.token_address,
                    'quote_asset': str(quote_ccy).strip().upper(),
                    'price_value': signed_answer / (10 ** unit_decimals),
                    'price_type': 'chainlink_feed',
                    'source': f'Chainlink latestRoundData at Ethereum block {block}, feed {feed}',
                    'snapshot_block': int(block),
                    'reference_timestamp': pd.to_datetime(updated_at,unit='s',utc=True),
                    'staleness_seconds': float(age),
                    'note': 'Independent Chainlink feed answer (at block); not necessarily Aave effective oracle source'
                })
        lido_raw = rpc_eth_call(rpc_url, WSTETH_MAINNET, select_ratio, int(block))
        if lido_raw <= 0:
            raise RuntimeError(f'Lido wstETH conversion invalid at block {block}')
        lido_records.append({
            'timestamp': ts, 'asset': 'WSTETH', 'token_address': WSTETH_MAINNET,
            'quote_asset': 'STETH', 'price_value': lido_raw / 1e18,
            'price_type': 'lido_contract_conversion',
            'source': f'Lido.wstETH.stEthPerToken at Ethereum block {block}',
            'snapshot_block': int(block), 'reference_timestamp': ts,
            'staleness_seconds': 0.0, 'note': 'One wstETH = this many stETH at block; NOT its USD oracle price',
        })
        print(f'[RPC] block {block}: {len(group)} Aave oracle prices + Lido conversion')
    return (pd.DataFrame(oracle_records, columns=COLUMNS),
            pd.DataFrame(lido_records, columns=COLUMNS),
            pd.DataFrame(chainlink_records, columns=COLUMNS))


def build(raw_market: pd.DataFrame, *, lag_minutes: int = 90,
          oracle: pd.DataFrame | None = None, lido: pd.DataFrame | None = None,
          chainlink: pd.DataFrame | None = None) -> tuple[pd.DataFrame, dict]:
    market, stats = market_rows(raw_market)
    ratios = ratio_rows(raw_market, lag_minutes)
    parts = [market, ratios]
    if oracle is not None and not oracle.empty:
        parts.append(oracle)
    if lido is not None and not lido.empty:
        parts.append(lido)
    if chainlink is not None and not chainlink.empty:
        parts.append(chainlink)
    result = pd.concat(parts, ignore_index=True)[COLUMNS].copy()
    result['timestamp'] = pd.to_datetime(result['timestamp'], utc=True)
    result['reference_timestamp'] = pd.to_datetime(result['reference_timestamp'], utc=True)
    result['snapshot_block'] = pd.array(result['snapshot_block'], dtype='Int64')
    if result['price_value'].isna().any() or (result['price_value'] <= 0).any():
        raise ValueError('Unexpected missing/negative price in generated records')
    if result.duplicated(['timestamp', 'asset', 'quote_asset', 'price_type', 'snapshot_block']).any():
        raise ValueError('Duplicate price records; check sources and snapshot CSV')
    result = result.sort_values(['timestamp','price_type','asset','quote_asset']).reset_index(drop=True)
    stats.update({
        'market_ratios': int(len(ratios)),
        'aave_oracle_snapshot_records': int((result.price_type=='aave_oracle').sum()),
        'lido_contract_ratio_records': int((result.price_type=='lido_contract_conversion').sum()),
        'chainlink_feed_records': int((result.price_type=='chainlink_feed').sum()),
        'oracle_status': 'AVAILABLE at supplied snapshots' if (result.price_type=='aave_oracle').any() else 'MISSING: ask N2 for RPC archive/snapshot oracle or validated CSV',
        'lido_status': 'AVAILABLE at supplied snapshots' if (result.price_type=='lido_contract_conversion').any() else 'MISSING: needs contract rate at exact snapshot',
        'market_ratio_rule': f'backward asof quotes, max {lag_minutes} minutes; no future denominator price',
        'chainlink_status': 'AVAILABLE at supplied snapshots' if (result.price_type=='chainlink_feed').any() else 'PENDING: N2 needs to confirm feeds, assets, quotes, block',
        'source_difference': 'market/chart rows are indicative. RPC aave_oracle = actual block state; CSV aave_oracle is N2-supplied and pending independent validation.',
        'generated_at_utc': datetime.now(timezone.utc).isoformat(),
    })
    return result, stats


# Source of historical ETH/STETH/wstETH data across both Snapshot A and B.
# DefiLlama chart endpoint can be used outside the CoinGecko Demo rolling limit.
# These are reference token addresses for market chart only, NOT oracle mappings.
MARKET_COINS = {
    'ETH': ('coingecko:ethereum', None),
    'WETH': ('ethereum:0xc02aaa39b223fe8d0a0e5c4f27ead9083c756cc2', '0xc02aaa39b223fe8d0a0e5c4f27ead9083c756cc2'),
    'STETH': ('ethereum:0xae7ab96520de3a18e5e111b5eaab095312d7fe84', '0xae7ab96520de3a18e5e111b5eaab095312d7fe84'),
    'WSTETH': ('ethereum:0x7f39c581f595b53c5cb19bd0b3f8da6c935e2ca0', WSTETH_MAINNET),
    'WBTC': ('ethereum:0x2260fac5e5542a773aa44fbcfedf7c193bc2c599', '0x2260fac5e5542a773aa44fbcfedf7c193bc2c599'),
    'USDC': ('ethereum:0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48', '0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48'),
    'USDT': ('ethereum:0xdac17f958d2ee523a2206206994597c13d831ec7', '0xdac17f958d2ee523a2206206994597c13d831ec7'),
    'DAI': ('ethereum:0x6b175474e89094c44da98b954eedeac495271d0f', '0x6b175474e89094c44da98b954eedeac495271d0f'),
    'LINK': ('ethereum:0x514910771af9ca656af840dff83e8264ecf986ca', '0x514910771af9ca656af840dff83e8264ecf986ca'),
    'AAVE': ('ethereum:0x7fc66500c84a76ad7e9c93437bfc5ac33e2ddae9', '0x7fc66500c84a76ad7e9c93437bfc5ac33e2ddae9'),
}


def chart_payload_prices(payload: dict, coin: str) -> list[dict]:
    coins = payload.get('coins', payload)
    if not isinstance(coins, dict):
        raise ValueError('DefiLlama chart unexpected response: no coins mapping')
    match = {str(k).lower(): v for k,v in coins.items()}.get(coin.lower())
    if match is None:
        return []
    if not isinstance(match, dict) or not isinstance(match.get('prices'), list):
        raise ValueError('DefiLlama chart unexpected price series')
    return match['prices']


def historical_market_defillama(start: pd.Timestamp, end: pd.Timestamp, assets: list[str],
                                period: str, chunk_span: int = 72,
                                coin_map: dict | None = None) -> tuple[pd.DataFrame, dict]:
    period_s = {'1h':3600, '4h':14400, '1d':86400}[period]
    if end <= start:
        raise ValueError('Historical window end must exceed start')
    if not 1 <= chunk_span <= 720:
        raise ValueError('chunk-span must be in [1,720]')
    session = requests.Session()
    coin_map = MARKET_COINS if coin_map is None else coin_map
    frames, failures, req_count = [], {}, 0
    for asset in assets:
        coin, token_addr = coin_map[asset]
        rows = []
        for start_ts in range(int(start.timestamp()), int(end.timestamp()), period_s * chunk_span):
            remaining = int(math.ceil((end.timestamp()-start_ts)/period_s))
            span = min(chunk_span, remaining)
            error = None
            for attempt in range(5):
                try:
                    r = session.get(f'https://coins.llama.fi/chart/{coin}', params={
                        'start': start_ts, 'span': span, 'period': period, 'searchWidth':'3h'
                    }, timeout=45)
                    if r.status_code in (429, 500, 502, 503, 504):
                        raise requests.HTTPError('Retryable DefiLlama HTTP error')
                    r.raise_for_status()
                    points = chart_payload_prices(r.json(), coin)
                    req_count += 1
                    rows.extend(points)
                    break
                except (requests.RequestException, ValueError) as exc:
                    error = type(exc).__name__
                    if attempt == 4:
                        raise RuntimeError(f'DefiLlama chart request failed for {asset} window={start_ts} ({error})') from None
                    time.sleep(min(3 * (attempt + 1), 15))
            time.sleep(0.15)
        parsed = []
        for q in rows:
            try:
                ts = pd.to_datetime(int(q['timestamp']),unit='s',utc=True)
                val = float(q['price'])
                if start <= ts < end and math.isfinite(val) and val > 0:
                    parsed.append({'timestamp':ts,'asset':asset,'token_address':token_addr,
                                   'price_usd':val,'source':f'DefiLlama chart {coin} {period}',
                                   'price_type':'market_reference'})
            except (TypeError, ValueError, KeyError, OverflowError):
                continue
        if parsed:
            frames.append(pd.DataFrame(parsed))
            print(f'[HISTORY] {asset}: {len(parsed)} quotes')
        else:
            failures[asset] = 'No historical quotes; NOT imputed'
            print(f'[MISSING] {asset}: no prices (check token availability)')
    if not frames:
        raise ValueError('DefiLlama returned zero valid historical quotations for all requested assets')
    data = pd.concat(frames,ignore_index=True).drop_duplicates(['asset','timestamp']).sort_values(['asset','timestamp'])
    return data, {'requests':req_count, 'missing_assets':failures, 'requested_start':str(start), 'requested_end':str(end)}


def main():
    ap = argparse.ArgumentParser(description='Build N3 point-in-time market/oracle/Chainlink/Lido prices')
    ap.add_argument('--input', type=Path, default=ROOT / 'data/raw/market/prices_raw.csv', help='CoinGecko CSV from your current fetch_prices.py')
    ap.add_argument('--market-source', choices=['csv','defillama'], default='csv')
    ap.add_argument('--start', help='UTC inclusive YYYY-MM-DD for DefiLlama historical market')
    ap.add_argument('--end', help='UTC EXCLUSIVE YYYY-MM-DD for DefiLlama historical market')
    ap.add_argument('--assets', default='ETH,WETH,STETH,WSTETH,WBTC,USDC,USDT,DAI', help='Default addresses apply only to DefiLlama market mode')
    ap.add_argument('--period', choices=['1h','4h','1d'], default='1h')
    ap.add_argument('--token-map-csv', type=Path, help='Optional N2 token_map CSV: asset,coin_id,token_address, for WEETH/CBETH or other assets')
    ap.add_argument('--chunk-span', type=int, default=72)
    ap.add_argument('--oracle-csv', type=Path, help='N2 verified snapshot oracle CSV')
    ap.add_argument('--lido-csv', type=Path, help='N2 verified snapshot wstETH/stETH CSV')
    ap.add_argument('--oracle-snapshots', type=Path, help='N2 snapshot CSV with token/oracle/optional Chainlink feeds, requires archive RPC')
    ap.add_argument('--max-feed-age-hours', type=float, default=24, help='Hard-fail on stale Chainlink feed at snapshot')
    ap.add_argument('--lag-minutes', type=int, default=90)
    ap.add_argument('--overwrite', action='store_true')
    args = ap.parse_args()
    if args.lag_minutes <= 0 or args.max_feed_age_hours <= 0:
        raise SystemExit('lag-minutes and max-feed-age-hours must be positive')
    if args.oracle_snapshots and (args.oracle_csv or args.lido_csv):
        raise SystemExit('Choose --oracle-snapshots RPC mode OR provided oracle/lido CSV mode')
    if args.market_source == 'csv':
        if not args.input.is_file():
            raise SystemExit(f'Missing {args.input}; first run fetch_prices.py or use --market-source defillama')
        raw = pd.read_csv(args.input)
        market_meta = {'provider':'existing CoinGecko/CSV market prices','input':str(args.input)}
    else:
        if not args.start or not args.end:
            raise SystemExit('DefiLlama mode requires --start YYYY-MM-DD --end YYYY-MM-DD (exclusive)')
        start = pd.Timestamp(args.start, tz='UTC'); end = pd.Timestamp(args.end, tz='UTC')
        coin_map = dict(MARKET_COINS)
        if args.token_map_csv:
            maps = pd.read_csv(args.token_map_csv)
            required = {'asset','coin_id','token_address'}
            if required - set(maps.columns):
                raise SystemExit(f'--token-map-csv missing {required - set(maps.columns)}')
            if maps['asset'].astype(str).str.upper().duplicated().any():
                raise SystemExit('Duplicate assets in --token-map-csv')
            for row in maps.itertuples(index=False):
                name = str(row.asset).strip().upper()
                address = addr(row.token_address)
                coin_id = str(row.coin_id).strip().lower()
                if coin_id != f'ethereum:{address}':
                    raise SystemExit(f'Bad coin ID mapping for {name}; expected ethereum:{{token_address}}')
                coin_map[name] = (coin_id, address)
        assets = [x.strip().upper() for x in args.assets.split(',') if x.strip()]
        if not assets or len(set(assets)) != len(assets) or any(x not in coin_map for x in assets):
            raise SystemExit(f'Invalid assets; supplied map is missing {[x for x in assets if x not in coin_map]}')
        raw, market_meta = historical_market_defillama(start,end,assets,args.period,args.chunk_span,coin_map)
        market_meta['token_mapping_assets'] = list(coin_map)

        market_meta['provider']='DefiLlama historical chart, on-chain contract ID if available'
    oracle = lido = chainlink = None
    if args.oracle_snapshots:
        load_dotenv(ROOT / '.env')
        rpc_url = (
            os.getenv('ETHEREUM_ARCHIVE_RPC_URL', '').strip()
            or os.getenv('ETH_RPC_URL', '').strip()
        )
        if not rpc_url.startswith(('https://','http://')):
            raise SystemExit('Missing ETHEREUM_ARCHIVE_RPC_URL or ETH_RPC_URL in .env (must support historical archive state)')
        oracle, lido, chainlink = rpc_snapshot_rows(args.oracle_snapshots, rpc_url, args.max_feed_age_hours)
    else:
        if args.oracle_csv:
            oracle = manual_oracle_rows(args.oracle_csv)
        if args.lido_csv:
            lido = manual_lido_rows(args.lido_csv)
    prices, report = build(raw,lag_minutes=args.lag_minutes,oracle=oracle,lido=lido,chainlink=chainlink)
    report.update({'market':market_meta,'chain_id':1,'protocol_id':'aave_v3_ethereum',
                   'study_scope':'Research Design v2.0 core Aave V3 Mainnet, bridge Compound is N2/N5 scope',
                   'oracle_warning':'Aave oracle rows are block state; their underlying Chainlink answer age is NOT automatically verified unless explicit Chainlink feed row provided.',
                   'snapshot_warning':'Snapshot A/B blocks must be provided or confirmed by N1/N2; example 2024 block is not treated as final.',
                   'market_not_oracle':True,'no_future_join_market_ratio':True,
                   'market_underlying_sample_time_independently_verified':False,
                   'point_in_time_HF_requires_onchain_oracle':True})
    output = ROOT / 'data/staging/n3_price_timeseries.parquet'
    qa = ROOT / 'outputs/tables/n3_price_timeseries_quality.json'
    if not args.overwrite and (output.exists() or qa.exists()):
        raise SystemExit('Output already exists (your fetch_prices.py may have created it). Use --overwrite for timestamped backups.')
    safe_write_df(prices, output, args.overwrite)
    safe_write_json(report, qa, args.overwrite)
    print('[SAVED]',output.relative_to(ROOT),len(prices),'rows')
    print('[SAVED]',qa.relative_to(ROOT))
    print('[COUNTS]',prices.price_type.value_counts().to_dict())
    print('[STATUS]',report['oracle_status'])
    print('[STATUS]',report['chainlink_status'])
    print('[STATUS]',report['lido_status'])


if __name__ == '__main__':
    main()
