"""N3 – Historical USD valuation of Aave V3 LiquidationCall events.

Input (created by src/data/fetch_liquidations.py):
    data/staging/n3_liquidations_etherscan.parquet

Outputs (independent N3 staging; NEVER writes project's 6 canonical tables):
    data/staging/n3_liquidations_priced.parquet
    data/staging/n3_liquidations_for_fact.parquet
    outputs/tables/n3_usd_pricing_quality.json
    data/raw/market/n3_defillama_price_cache.json

Run from the repository root:
    python src/data/price_liquidations_usd.py

Optional:
    python src/data/price_liquidations_usd.py --bucket-minutes 5 --max-lag-minutes 60
    python src/data/price_liquidations_usd.py --overwrite

Price: DefiLlama historical USD *market reference*, not Aave oracle price.
The script uses contract addresses, not ticker symbols; missing prices remain missing.
Quotes after an event are retried at earlier lookup times and still rejected if
they are future-dated or exceed the configured maximum lag.
No Coinbase/CoinGecko/Etherscan API key needed for this particular price lookup.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import shutil
import tempfile
import time
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path

import pandas as pd
import requests

ROOT = Path(__file__).resolve().parents[2]
STAGING_IN = ROOT / "data/staging/n3_liquidations_etherscan.parquet"
PRICED_OUT = ROOT / "data/staging/n3_liquidations_priced.parquet"
FACT_OUT = ROOT / "data/staging/n3_liquidations_for_fact.parquet"
QA_OUT = ROOT / "outputs/tables/n3_usd_pricing_quality.json"
CACHE_OUT = ROOT / "data/raw/market/n3_defillama_price_cache.json"
PRICE_BASE = "https://coins.llama.fi"
SOURCE = "DefiLlama historical token market-price reference (not Aave oracle)"
CONTRACT_COLS = [
    "tx_hash", "log_index", "event_block", "event_timestamp", "protocol_id",
    "wallet_address", "liquidator_address", "collateral_token_address",
    "debt_token_address", "debt_repaid_usd", "collateral_seized_usd",
]
INPUT_COLS = [
    "tx_hash", "log_index", "event_block", "event_timestamp", "protocol_id",
    "wallet_address", "liquidator_address", "collateral_token_address",
    "debt_token_address", "debt_raw_amount", "collateral_raw_amount",
    "debt_decimals", "collateral_decimals",
]
ADDR_COLS = [
    "wallet_address", "liquidator_address", "collateral_token_address",
    "debt_token_address",
]


def cli() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Value Aave V3 liquidation events in historical USD")
    parser.add_argument("--input", type=Path, default=STAGING_IN)
    parser.add_argument("--bucket-minutes", type=int, default=5,
                        help="Group events in preceding UTC time buckets (default 5 minutes)")
    parser.add_argument("--max-lag-minutes", type=int, default=60,
                        help="Maximum age of returned price versus the event")
    parser.add_argument("--search-width", default="1h",
                        help="DefiLlama historical price searchWidth. Result timestamp still validated.")
    parser.add_argument("--allow-forward-minutes", type=int, default=0,
                        help="Default 0 rejects future price quotes to avoid look-ahead bias")
    parser.add_argument("--overwrite", action="store_true", help="Backup and replace output files")
    parser.add_argument("--refresh-cache", action="store_true", help="Ignore old DefiLlama cache entries")
    return parser.parse_args()


def parse_decimal_amount(raw: object, decimals: object) -> Decimal | None:
    """Use *raw blockchain integer* to avoid previous float-token rounding."""
    try:
        if pd.isna(raw) or pd.isna(decimals):
            return None
        d = Decimal(str(decimals))
        if d != d.to_integral_value() or d < 0 or d > 36:
            return None
        n = Decimal(str(raw))
        if not n.is_finite() or n < 0 or n != n.to_integral_value():
            return None
        return n / (Decimal(10) ** int(d))
    except (InvalidOperation, TypeError, ValueError):
        return None


def standardize_events(frame: pd.DataFrame) -> pd.DataFrame:
    missing = [c for c in INPUT_COLS if c not in frame.columns]
    if missing:
        raise ValueError(f"Input missing columns {missing}. Run the updated Etherscan collector first.")
    df = frame.copy()
    df["event_timestamp"] = pd.to_datetime(df["event_timestamp"], utc=True, errors="coerce")
    if df.event_timestamp.isna().any():
        raise ValueError("event_timestamp has invalid/missing values")
    for col in ADDR_COLS:
        df[col] = df[col].astype(str).str.strip().str.lower()
        invalid = ~df[col].str.fullmatch(r"0x[0-9a-f]{40}")
        if invalid.any():
            raise ValueError(f"Invalid address in {col}: {df.loc[invalid, col].iloc[0]}")
    df["tx_hash"] = df["tx_hash"].astype(str).str.strip().str.lower()
    if (~df.tx_hash.str.fullmatch(r"0x[0-9a-f]{64}")).any():
        raise ValueError("tx_hash must be a full 0x + 64-hex transaction hash")
    if (df["protocol_id"] != "aave_v3_ethereum").any():
        raise ValueError("Only aave_v3_ethereum is supported")
    for col in ("event_block", "log_index"):
        df[col] = pd.to_numeric(df[col], errors="raise").astype("int64" if col == "event_block" else "int32")
    if df.duplicated(["tx_hash", "log_index"]).any():
        raise ValueError("Duplicate tx_hash + log_index; investigate Etherscan data first")
    return df.sort_values(["event_timestamp", "tx_hash", "log_index"]).reset_index(drop=True)


def load_cache() -> dict:
    if not CACHE_OUT.exists():
        return {}
    cache = json.loads(CACHE_OUT.read_text(encoding="utf-8"))
    if not isinstance(cache, dict):
        raise ValueError("Price cache must be a JSON object")
    return cache


def save_atomic_bytes(data: bytes, path: Path, backup: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if backup and path.exists():
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        shutil.copy2(path, path.with_name(f"{path.name}.bak_{stamp}"))
    fd, name = tempfile.mkstemp(dir=path.parent, prefix="n3_", suffix=".tmp")
    os.close(fd)
    temp = Path(name)
    try:
        temp.write_bytes(data)
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


def save_json_atomic(payload: dict, path: Path, backup: bool = False) -> None:
    save_atomic_bytes(json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8"), path, backup)


def fetch_price_batch(session: requests.Session, bucket_ts: int,
                      addrs: list[str], search_width: str) -> dict[str, dict | None]:
    """One request at a time; request all tokens for same historical time.

    Response format (DefiLlama): {"coins": {"ethereum:0x...": {price,timestamp,confidence}}}
    Missing coins do not get inferred as $0 / $1 / $ETH.
    """
    pairs = [f"ethereum:{a.lower()}" for a in addrs]
    url = f"{PRICE_BASE}/prices/historical/{bucket_ts}/{','.join(pairs)}"
    for attempt in range(5):
        try:
            response = session.get(url, params={"searchWidth": search_width}, timeout=40)
            if response.status_code in (429, 500, 502, 503, 504):
                raise requests.HTTPError(f"Temporary HTTP {response.status_code}")
            response.raise_for_status()
            payload = response.json()
            if not isinstance(payload, dict) or not isinstance(payload.get("coins"), dict):
                raise RuntimeError("Unexpected DefiLlama historical-price response")
            coins = {str(k).lower(): v for k, v in payload["coins"].items()}
            out = {}
            for addr in addrs:
                item = coins.get(f"ethereum:{addr.lower()}")
                if not isinstance(item, dict):
                    out[addr] = None
                    continue
                price, ts = item.get("price"), item.get("timestamp")
                try:
                    price = float(price)
                    ts = int(ts)
                    if not math.isfinite(price) or price <= 0:
                        out[addr] = None
                        continue
                except (TypeError, ValueError, OverflowError):
                    out[addr] = None
                    continue
                out[addr] = {"price": price, "price_timestamp": ts,
                             "confidence": item.get("confidence"),
                             "source": SOURCE}
            return out
        except (requests.RequestException, RuntimeError, ValueError):
            if attempt == 4:
                raise RuntimeError("DefiLlama historical price request failed. Check connectivity/API status") from None
            time.sleep(min(2 ** attempt, 12))
    raise AssertionError("unreachable")


def get_prices(df: pd.DataFrame, bucket_minutes: int, search_width: str,
               refresh_cache: bool, max_lag_minutes: int) -> tuple[dict, int]:
    bucket_seconds = bucket_minutes * 60
    # Floor timestamps to a time <= each original event; no future bucket.
    df["price_bucket_ts"] = df.event_timestamp.map(
        lambda value: int(value.timestamp()) // bucket_seconds * bucket_seconds
    ).astype("int64")
    tasks: dict[int, dict[str, int]] = {}
    for row in df.itertuples(index=False):
        bucket = int(row.price_bucket_ts)
        event_ts = int(row.event_timestamp.timestamp())
        bucket_tasks = tasks.setdefault(bucket, {})
        for addr in (row.debt_token_address, row.collateral_token_address):
            bucket_tasks[addr] = min(bucket_tasks.get(addr, event_ts), event_ts)
    cache = {} if refresh_cache else load_cache()
    session = requests.Session()
    requests_made = 0
    for n, (bucket, event_times) in enumerate(sorted(tasks.items()), 1):
        addresses = set(event_times)
        pending = [a for a in sorted(addresses) if f"{bucket}|{a}" not in cache]
        if pending:
            # Prevent too-long URL; a basket of <=20 contracts per call.
            for start in range(0, len(pending), 20):
                group = pending[start:start + 20]
                got = fetch_price_batch(session, bucket, group, search_width)
                for addr in group:
                    cache[f"{bucket}|{addr}"] = got.get(addr)
                requests_made += 1
                time.sleep(0.25)
            # Save incrementally so reruns do not refetch every past price.
            save_json_atomic(cache, CACHE_OUT)
        needs_earlier_quote = {
            addr for addr in addresses
            if cache.get(f"{bucket}|{addr}") is None
            or int(cache[f"{bucket}|{addr}"]["price_timestamp"]) > event_times[addr]
        }
        for lookback_minutes in (15, 30, 45):
            if not needs_earlier_quote:
                break
            remaining = sorted(needs_earlier_quote)
            for start in range(0, len(remaining), 20):
                group = remaining[start:start + 20]
                earlier = fetch_price_batch(
                    session, bucket - lookback_minutes * 60, group, search_width
                )
                requests_made += 1
                for addr in group:
                    candidate = earlier.get(addr)
                    if candidate is None:
                        continue
                    quote_ts = int(candidate["price_timestamp"])
                    event_ts = event_times[addr]
                    if event_ts - max_lag_minutes * 60 <= quote_ts <= event_ts:
                        cache[f"{bucket}|{addr}"] = candidate
                        needs_earlier_quote.remove(addr)
                time.sleep(0.25)
            if not needs_earlier_quote:
                save_json_atomic(cache, CACHE_OUT)
        if n % 10 == 0 or n == len(tasks):
            print(f"[PRICES] {n}/{len(tasks)} time buckets processed, new API requests={requests_made}")
    return cache, requests_made


def quote(cache: dict, bucket: int, addr: str, event_ts: int,
          max_lag_minutes: int, forward_minutes: int) -> tuple[float | None, int | None, float | None, str]:
    item = cache.get(f"{bucket}|{addr}")
    if item is None:
        return None, None, None, "missing_price"
    ts = int(item["price_timestamp"])
    gap_s = event_ts - ts  # Positive: quote is older than event
    if gap_s > max_lag_minutes * 60:
        return None, ts, item.get("confidence"), "too_old"
    if gap_s < -(forward_minutes * 60):
        return None, ts, item.get("confidence"), "future_price_rejected"
    return float(item["price"]), ts, item.get("confidence"), "ok"


def calculate(df: pd.DataFrame, cache: dict,
              max_lag_minutes: int, forward_minutes: int) -> tuple[pd.DataFrame, pd.DataFrame, dict]:
    df = df.copy()
    if df.empty:
        for k in ("debt_price_usd", "collateral_price_usd", "debt_repaid_usd", "collateral_seized_usd"):
            df[k] = pd.Series(dtype="float64")
        for k in ("debt_quote_status", "collateral_quote_status"):
            df[k] = pd.Series(dtype="object")
        ready = df.reindex(columns=CONTRACT_COLS).copy()
        return df, ready, {"events_total": 0, "events_fact_ready": 0, "usd_coverage_pct": None}

    results = []
    for row in df.itertuples(index=False):
        event_ts = int(row.event_timestamp.timestamp())
        b = int(row.price_bucket_ts)
        dp, dts, dconf, ds = quote(cache, b, row.debt_token_address, event_ts,
                                   max_lag_minutes, forward_minutes)
        cp, cts, cconf, cs = quote(cache, b, row.collateral_token_address, event_ts,
                                   max_lag_minutes, forward_minutes)
        debt_amt = parse_decimal_amount(row.debt_raw_amount, row.debt_decimals)
        coll_amt = parse_decimal_amount(row.collateral_raw_amount, row.collateral_decimals)
        dr = float(debt_amt * Decimal(str(dp))) if debt_amt is not None and dp is not None else None
        cr = float(coll_amt * Decimal(str(cp))) if coll_amt is not None and cp is not None else None
        if debt_amt is None:
            ds = "missing_debt_decimals_or_amount"
        if coll_amt is None:
            cs = "missing_collateral_decimals_or_amount"
        results.append({
            "debt_price_usd": dp, "collateral_price_usd": cp,
            "debt_price_timestamp": pd.to_datetime(dts, unit="s", utc=True) if dts is not None else pd.NaT,
            "collateral_price_timestamp": pd.to_datetime(cts, unit="s", utc=True) if cts is not None else pd.NaT,
            "debt_price_confidence": dconf, "collateral_price_confidence": cconf,
            "debt_quote_status": ds, "collateral_quote_status": cs,
            "debt_repaid_usd": dr, "collateral_seized_usd": cr,
        })
    valued = pd.concat([df.reset_index(drop=True), pd.DataFrame(results)], axis=1)
    valid = valued["debt_repaid_usd"].notna() & valued["collateral_seized_usd"].notna()
    ready = valued.loc[valid, CONTRACT_COLS].copy().reset_index(drop=True)
    ready["event_block"] = ready["event_block"].astype("int64")
    ready["log_index"] = ready["log_index"].astype("int32")
    ready["event_timestamp"] = pd.to_datetime(ready["event_timestamp"], utc=True)
    if ready.duplicated(["tx_hash", "log_index"]).any():
        raise RuntimeError("The fact-ready output unexpectedly has duplicate keys")
    quality = {
        "events_total": len(valued),
        "events_fact_ready": int(valid.sum()),
        "usd_coverage_pct": round(float(valid.mean() * 100), 2),
        "missing_debt_usd": int(valued.debt_repaid_usd.isna().sum()),
        "missing_collateral_usd": int(valued.collateral_seized_usd.isna().sum()),
        "debt_quote_status_counts": valued.debt_quote_status.value_counts(dropna=False).to_dict(),
        "collateral_quote_status_counts": valued.collateral_quote_status.value_counts(dropna=False).to_dict(),
        "total_repaid_usd_on_priced_events_only": float(ready.debt_repaid_usd.sum()),
        "total_seized_usd_on_priced_events_only": float(ready.collateral_seized_usd.sum()),
    }
    return valued, ready, quality


def parquet_bytes(frame: pd.DataFrame) -> bytes:
    # Writing to a temp file ensures a single atomic destination replace.
    import io
    buffer = io.BytesIO()
    frame.to_parquet(buffer, index=False, engine="pyarrow", compression="snappy")
    return buffer.getvalue()


def main() -> None:
    args = cli()
    if args.bucket_minutes < 1 or args.max_lag_minutes < 0 or args.allow_forward_minutes < 0:
        raise SystemExit("Invalid bucket/lag/forward-minutes setting")
    if not args.input.is_file():
        raise SystemExit(f"No input {args.input}. Run fetch_liquidations.py first.")
    for path in (PRICED_OUT, FACT_OUT, QA_OUT):
        if path.exists() and not args.overwrite:
            raise SystemExit(f"{path} already exists. Use --overwrite (backup) before replacing outputs.")
    df = standardize_events(pd.read_parquet(args.input))
    if df.empty:
        print("[WARN] 0 LiquidationCall events in input. No fabricated events or values.")
        cache, request_count = {}, 0
    else:
        cache, request_count = get_prices(
            df, args.bucket_minutes, args.search_width, args.refresh_cache, args.max_lag_minutes
        )
    valued, ready, quality = calculate(df, cache, args.max_lag_minutes, args.allow_forward_minutes)
    quality.update({
        "source_event_logs": str(args.input),
        "source_price_usd": SOURCE,
        "price_lookup": f"{PRICE_BASE}/prices/historical/{{timestamp}}/{{ethereum:token_addresses}}",
        "bucket_minutes": args.bucket_minutes,
        "max_lag_minutes": args.max_lag_minutes,
        "allow_forward_minutes": args.allow_forward_minutes,
        "price_search_width": args.search_width,
        "earlier_quote_fallback_minutes": [15, 30, 45],
        "new_api_requests": request_count,
        "data_contract_primary_key": ["tx_hash", "log_index"],
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "caveat": "USD totals only cover events with two valid token quotes; reference market prices, NOT Aave oracle.",
    })
    save_atomic_bytes(parquet_bytes(valued), PRICED_OUT, backup=args.overwrite)
    save_atomic_bytes(parquet_bytes(ready), FACT_OUT, backup=args.overwrite)
    save_json_atomic(quality, QA_OUT, backup=args.overwrite)
    print(f"[FINISHED] events={quality['events_total']}; fact-ready={quality['events_fact_ready']} "
          f"({quality['usd_coverage_pct']}% price coverage); requests={request_count}")
    print(f"[STAGING] {PRICED_OUT.relative_to(ROOT)} (all events incl. missing USD)")
    print(f"[FACT-READY] {FACT_OUT.relative_to(ROOT)} (exactly 11 contract fields)")
    print(f"[QUALITY] {QA_OUT.relative_to(ROOT)}")
    print("[IMPORTANT] This script does NOT write data/processed/fact_historical_liquidations.parquet")
    if quality["events_fact_ready"] != quality["events_total"]:
        print("[WARNING] Incomplete event-time USD coverage. Check quality JSON before claiming total USD.")


if __name__ == "__main__":
    main()
