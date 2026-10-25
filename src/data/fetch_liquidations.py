"""N3: Collect Aave V3 Ethereum LiquidationCall logs using Etherscan API V2.

Install: pip install requests pandas pyarrow python-dotenv
Run from project root:
    python src/data/fetch_liquidations.py --start 2026-10-12 --end 2026-10-16

Date start inclusive, end exclusive, both UTC.
No Dune API, no Dune SQL and no attempt to fabricate historical USD prices.
This script writes only N3 staging files; it does NOT overwrite six project contract tables.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import tempfile
import time
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pandas as pd
import requests
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[2]
API_URL = "https://api.etherscan.io/v2/api"
CHAIN_ID = "1"  # Ethereum mainnet (not Arbitrum or Base)
PROTOCOL_ID = "aave_v3_ethereum"
AAVE_POOL = "0x87870bca3f3fd6335c3f4ce8392d69350b4fa4e2"
EVENT_TOPIC = "0xe413a321e8681d831f4dbccbca790d2952b56f977908e45be37335533e005286"

COLUMNS = [
    "tx_hash", "log_index", "event_block", "event_timestamp", "protocol_id",
    "wallet_address", "liquidator_address", "collateral_token_address", "debt_token_address",
    "debt_raw_amount", "collateral_raw_amount", "receive_atoken",
    "debt_decimals", "collateral_decimals", "debt_amount_token", "collateral_amount_token",
]


def parse_args():
    p = argparse.ArgumentParser(description="Etherscan V2 Aave V3 liquidation collector")
    p.add_argument("--start", default="2026-10-12", help="UTC inclusive YYYY-MM-DD")
    p.add_argument("--end", default="2026-10-16", help="UTC exclusive YYYY-MM-DD")
    p.add_argument("--block-span", type=int, default=2000, help="Etherscan block window size")
    p.add_argument("--start-block", type=int, help="Explicit inclusive block for peak QA (requires --end-block)")
    p.add_argument("--end-block", type=int, help="Explicit inclusive block for peak QA (requires --start-block)")
    p.add_argument("--output-root", type=Path, help="Separate root for peak log exports; never overwrite existing raw N3 data")
    p.add_argument("--no-decimals", action="store_true", help="Skip eth_call for token decimals")
    p.add_argument("--overwrite", action="store_true", help="Back up and replace existing N3 outputs")
    return p.parse_args()


class Etherscan:
    def __init__(self, key: str):
        self.key = key
        self.session = requests.Session()
        self.last_call = 0.0
        self.call_count = 0

    def get(self, **params):
        # Free tier friendly pacing, 2.5 requests/s maximum before retries.
        for attempt in range(6):
            elapsed = time.monotonic() - self.last_call
            if elapsed < 0.42:
                time.sleep(0.42 - elapsed)
            self.last_call = time.monotonic()
            self.call_count += 1
            request_params = {"chainid": CHAIN_ID, **params, "apikey": self.key}
            try:
                response = self.session.get(API_URL, params=request_params, timeout=35)
                if response.status_code in (429, 500, 502, 503, 504):
                    raise RuntimeError("Rate limited or temporary HTTP error")
                if response.status_code >= 400:
                    raise ValueError(f"HTTP {response.status_code}; check Etherscan subscription/access")
                result = response.json()
            except (requests.RequestException, ValueError, RuntimeError) as exc:
                # Avoid printing request URL because it contains the API key.
                if attempt == 5:
                    raise RuntimeError("Etherscan request failed. Check connectivity, key and rate limits") from None
                time.sleep(min(2 ** attempt, 16))
                continue
            if "jsonrpc" in result:
                if result.get("error"):
                    raise RuntimeError(f"Etherscan RPC error: {str(result['error'])[:160]}")
                return result.get("result")
            if str(result.get("status")) == "1":
                return result.get("result")
            message = str(result.get("message", "")) + " " + str(result.get("result", ""))
            if "No records found" in message or "No transactions found" in message:
                return []
            if "rate limit" in message.lower() or "busy" in message.lower():
                if attempt == 5:
                    raise RuntimeError("Etherscan API rate limit not cleared")
                time.sleep(min(2 ** attempt, 16))
                continue
            # An explicit error is not equivalent to zero liquidation events.
            raise RuntimeError(f"Etherscan returned an error (not zero events): {message[:180]}")
        raise RuntimeError("Etherscan failed after retries")

    def block_at(self, timestamp: int, closest: str) -> int:
        result = self.get(module="block", action="getblocknobytime", timestamp=timestamp, closest=closest)
        if not isinstance(result, (str, int)):
            raise RuntimeError(f"Cannot resolve block from timestamp: {result}")
        return int(result)

    def logs(self, first: int, last: int):
        result = self.get(
            module="logs", action="getLogs", address=AAVE_POOL, topic0=EVENT_TOPIC,
            fromBlock=first, toBlock=last, page=1, offset=1000,
        )
        if not isinstance(result, list):
            raise RuntimeError("Etherscan getLogs result is not a list")
        return result

    def token_decimals(self, address: str, at_block: int):
        try:
            data = self.get(
                module="proxy", action="eth_call", to=address,
                data="0x313ce567", tag=hex(at_block),
            )
            if not isinstance(data, str) or not data.startswith("0x") or len(data) < 3:
                return None
            decimals = int(data, 16)
            return decimals if 0 <= decimals <= 36 else None
        except RuntimeError:
            return None


def find_logs(client: Etherscan, first: int, last: int, span: int) -> list[dict]:
    all_logs = []
    for lo in range(first, last + 1, span):
        hi = min(last, lo + span - 1)
        all_logs.extend(fetch_safe_block_range(client, lo, hi))
        print(f"[LOGS] blocks {lo}–{hi}: total {len(all_logs)} events")
    return all_logs


def fetch_safe_block_range(client: Etherscan, first: int, last: int) -> list[dict]:
    batch = client.logs(first, last)
    if len(batch) < 1000:
        return batch
    # 1000 may be truncated on free plan; subdivide instead of silently accepting.
    if first < last:
        mid = (first + last) // 2
        return (fetch_safe_block_range(client, first, mid) +
                fetch_safe_block_range(client, mid + 1, last))
    # Rare edge case: >999 logs in the same block; use pagination.
    results = batch[:]
    page = 2
    while True:
        more = client.get(module="logs", action="getLogs", address=AAVE_POOL, topic0=EVENT_TOPIC,
                          fromBlock=first, toBlock=last, page=page, offset=1000)
        if not isinstance(more, list):
            raise RuntimeError("Invalid paginated result")
        results.extend(more)
        if len(more) < 1000:
            break
        page += 1
    return results


def decode_address(topic_or_word: str) -> str:
    value = topic_or_word.lower().removeprefix("0x")
    if len(value) != 64 or any(c not in "0123456789abcdef" for c in value):
        raise ValueError("Invalid 32-byte address topic/data word")
    return "0x" + value[-40:]


def as_int(hexval: str) -> int:
    return int(hexval, 16) if str(hexval).startswith("0x") else int(hexval)


def decode_event(log: dict) -> dict:
    topics = log.get("topics", [])
    if len(topics) != 4 or str(topics[0]).lower() != EVENT_TOPIC:
        raise ValueError("Unexpected event signature/topics")
    raw_data = log.get("data", "")
    if not isinstance(raw_data, str) or not raw_data.startswith("0x"):
        raise ValueError("Missing event data")
    data = raw_data[2:]
    if len(data) != 4 * 64:
        raise ValueError(f"LiquidationCall expects four 32-byte data words, got {len(data)} hex chars")
    words = [data[n:n+64] for n in range(0, len(data), 64)]
    timestamp = pd.to_datetime(as_int(log["timeStamp"]), unit="s", utc=True)
    return {
        "tx_hash": log["transactionHash"].lower(),
        "log_index": as_int(log["logIndex"]),
        "event_block": as_int(log["blockNumber"]),
        "event_timestamp": timestamp,
        "protocol_id": PROTOCOL_ID,
        "wallet_address": decode_address(topics[3]),
        "liquidator_address": decode_address(words[2]),
        "collateral_token_address": decode_address(topics[1]),
        "debt_token_address": decode_address(topics[2]),
        "debt_raw_amount": str(int(words[0], 16)),
        "collateral_raw_amount": str(int(words[1], 16)),
        "receive_atoken": bool(int(words[3], 16)),
    }


def scaled_amount(raw: str, decimals: int | None):
    if decimals is None:
        return None
    return float(Decimal(raw) / (Decimal(10) ** decimals))


def atomic_save(df: pd.DataFrame, dest: Path, fmt: str, overwrite: bool):
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists() and not overwrite:
        raise FileExistsError(f"Existing output {dest}, use --overwrite to back up and replace")
    if dest.exists():
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        shutil.copy2(dest, dest.with_name(dest.name + f".bak_{stamp}"))
    fd, name = tempfile.mkstemp(prefix="n3_", suffix=".tmp", dir=dest.parent)
    os.close(fd)
    tmp = Path(name)
    try:
        if fmt == "csv":
            df.to_csv(tmp, index=False)
        else:
            df.to_parquet(tmp, index=False)
        os.replace(tmp, dest)
    finally:
        tmp.unlink(missing_ok=True)


def main():
    args = parse_args()
    if args.block_span < 1:
        raise SystemExit("--block-span must be positive")
    use_blocks = args.start_block is not None or args.end_block is not None
    if use_blocks and (args.start_block is None or args.end_block is None):
        raise SystemExit('Must give BOTH --start-block and --end-block')
    if use_blocks and (args.start_block < 1 or args.end_block < args.start_block):
        raise SystemExit('Invalid explicit block range')
    start = pd.Timestamp(args.start, tz="UTC") if not use_blocks else None
    end = pd.Timestamp(args.end, tz="UTC") if not use_blocks else None
    if not use_blocks and end <= start:
        raise SystemExit("End date must be after start date")
    if not use_blocks and end.to_pydatetime() > datetime.now(timezone.utc):
        raise SystemExit("End date must not be in the future")
    out_root = args.output_root.resolve() if args.output_root else ROOT
    raw_path = out_root / "data/raw/market/etherscan_liquidations_raw.csv"
    staging_path = out_root / "data/staging/n3_liquidations_etherscan.parquet"
    qa_path = out_root / "outputs/tables/n3_etherscan_quality.json"
    if not args.overwrite and any(p.exists() for p in (raw_path, staging_path, qa_path)):
        raise SystemExit("Some output files exist; add --overwrite to back up and replace them")
    load_dotenv(ROOT / ".env")
    key = os.getenv("ETHERSCAN_API_KEY", "").strip()
    if not key:
        raise SystemExit("Add ETHERSCAN_API_KEY=... to .env in project root; never paste keys in chats")
    client = Etherscan(key)
    if use_blocks:
        first, last = args.start_block, args.end_block
    else:
        first = client.block_at(int(start.timestamp()), "after")
        last = client.block_at(int(end.timestamp()) - 1, "before")
    if last < first:
        raise SystemExit("No blocks in requested date range")
    print(f"[RANGE] blocks {first}-{last} (inclusive); mode={'EXPLICIT_BLOCKS' if use_blocks else 'UTC_DATES'}")
    raw_logs = find_logs(client, first, last, args.block_span)
    decoded = [decode_event(record) for record in raw_logs]
    df = pd.DataFrame(decoded)
    if df.empty:
        df = pd.DataFrame(columns=COLUMNS)
    else:
        if not use_blocks:
            df = df[(df.event_timestamp >= start) & (df.event_timestamp < end)].copy()
        if df.duplicated(["tx_hash", "log_index"]).any():
            raise RuntimeError("Duplicate (tx_hash, log_index) found; do not silently drop")
        df = df.sort_values(["event_block", "log_index"])
        metadata = {}
        if not args.no_decimals:
            addresses = sorted(set(df.debt_token_address) | set(df.collateral_token_address))
            for address in addresses:
                first_seen = int(df.loc[
                    (df.debt_token_address == address) | (df.collateral_token_address == address),
                    "event_block"].min())
                metadata[address] = client.token_decimals(address, first_seen)
                print(f"[TOKEN] {address} decimals={metadata[address]}")
        df["debt_decimals"] = df.debt_token_address.map(metadata)
        df["collateral_decimals"] = df.collateral_token_address.map(metadata)
        df["debt_amount_token"] = [scaled_amount(a, b) for a, b in
                                  zip(df.debt_raw_amount, df.debt_decimals)]
        df["collateral_amount_token"] = [scaled_amount(a, b) for a, b in
                                        zip(df.collateral_raw_amount, df.collateral_decimals)]
        df = df.reindex(columns=COLUMNS)
    raw_df = pd.DataFrame(raw_logs)
    atomic_save(raw_df, raw_path, "csv", args.overwrite)
    atomic_save(df, staging_path, "parquet", args.overwrite)
    report = {
        "data_source": "Etherscan API V2 getLogs",
        "chain_id": 1, "protocol_id": PROTOCOL_ID, "pool_address": AAVE_POOL,
        "topic0": EVENT_TOPIC, "start_inclusive_utc": str(start) if start is not None else None,
        "end_exclusive_utc": str(end) if end is not None else None,
        "query_mode": "explicit_inclusive_block_range" if use_blocks else "utc_date_range",
        "first_block": first, "last_block": last, "events_decoded": len(df),
        "unique_transactions": df.tx_hash.nunique(),
        "missing_debt_decimals": int(df.debt_decimals.isna().sum()),
        "missing_collateral_decimals": int(df.collateral_decimals.isna().sum()),
        "requires_usd_valuation": True,
        "price_usd_source": None,
        "warning": "Do not write these rows into fact_historical_liquidations until event-time prices and USD columns are validated",
        "created_utc": datetime.now(timezone.utc).isoformat(), "api_requests": client.call_count,
    }
    qa_path.parent.mkdir(parents=True, exist_ok=True)
    if qa_path.exists() and args.overwrite:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        shutil.copy2(qa_path, qa_path.with_name(qa_path.name + f".bak_{stamp}"))
    qa_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"[SUCCESS] {len(df)} events saved; {df.tx_hash.nunique()} transactions; {client.call_count} API requests")
    print(f"[OUTPUT] {raw_path}")
    print(f"[OUTPUT] {staging_path}")
    print(f"[OUTPUT] {qa_path}")
    print("[NEXT] Run src/data/price_liquidations_usd.py to add historical token USD prices.")


if __name__ == "__main__":
    main()
