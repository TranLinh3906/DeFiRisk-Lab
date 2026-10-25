#!/usr/bin/env python3
"""N3 Muc 3 - Tich hop N3 market prices + N2 Aave oracle + Lido snapshots & Depeg Metrics.

Inputs (no API call):
  data/staging/n3_price_timeseries.parquet  (N3: market_reference/market_ratio)
  data/processed/aave/oracle_prices.parquet (N2: real Aave getAssetPrice at snapshot)
  data/processed/aave/lido_ratios.parquet   (N2: real Lido stEthPerToken at snapshot)
  data/processed/aave/snapshot_metadata.json
  data/processed/aave/token_registry.csv

Output: data/staging/n3_price_timeseries.parquet
         outputs/tables/n3_price_timeseries_quality.json
"""
from __future__ import annotations

import argparse
import json
import math
import os
import re
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
REQUIRED_COLUMNS = [
    "timestamp", "asset", "token_address", "quote_asset", "price_value",
    "price_type", "source", "snapshot_block", "reference_timestamp",
    "staleness_seconds", "note",
]
SNAPSHOT_TYPES = {"aave_oracle", "lido_contract_conversion", "depeg_metric"}
ADDR_RE = re.compile(r"0x[a-f0-9]{40}")
LIDO_WSTETH = "0x7f39c581f595b53c5cb19bd0b3f8da6c935e2ca0"


def check_columns(df: pd.DataFrame, required: set[str], name: str) -> None:
    missing = sorted(required - set(df.columns))
    if missing:
        raise ValueError(f"{name}: thieu cot {missing}. Cot hien co: {list(df.columns)}")


def to_utc(series: pd.Series, name: str) -> pd.Series:
    result = pd.to_datetime(series, errors="coerce", utc=True)
    if result.isna().any():
        raise ValueError(f"{name}: {int(result.isna().sum())} timestamp khong hop le")
    return result


def validate_address(series: pd.Series, name: str) -> pd.Series:
    addr = series.astype("string").str.strip().str.lower()
    bad = ~addr.str.fullmatch(ADDR_RE.pattern, na=False)
    if bad.any():
        raise ValueError(f"{name}: {int(bad.sum())} dia chi khong hop le; vd={addr[bad].head().tolist()}")
    return addr.astype(str)


def snapshot_config(metadata: dict) -> dict[int, dict]:
    if metadata.get("chain_id") != 1 or metadata.get("protocol_id") != "aave_v3_ethereum":
        raise ValueError("N2 metadata khong phai Aave V3 Ethereum Mainnet, chain_id=1")
    if not metadata.get("status", {}).get("archive_eth_call_verified", False):
        raise ValueError("N2 metadata chua xac nhan archive eth_call")
    snapshots = metadata.get("snapshots", {})
    result = {}
    for sid, info in snapshots.items():
        block = int(info["snapshot_block"])
        ts = pd.to_datetime(info["snapshot_timestamp_utc"], utc=True)
        if not bool(info.get("block_hash_verified_on_chain")):
            raise ValueError(f"Snapshot {sid}: block hash chua duoc verify")
        if block in result:
            raise ValueError("Snapshot trung block")
        result[block] = {"snapshot_id": sid, "timestamp": ts,
                         "n_reserves": int(info["n_reserves"]),
                         "aave_oracle": str(info["aave_oracle"]).lower()}
    return result


def compute_depeg_metrics(market: pd.DataFrame) -> pd.DataFrame:
    """Tinh toan % Depeg theo chuoi thoi gian: (Price - Parity) / Parity."""
    steth = market.loc[(market["asset"] == "STETH") & (market["quote_asset"] == "ETH")].copy()
    if steth.empty:
        return pd.DataFrame(columns=REQUIRED_COLUMNS)
    
    depeg_rows = []
    for _, row in steth.iterrows():
        # Baseline parity value for stETH/ETH is 1.0
        ratio = row["price_value"]
        depeg_pct = (ratio - 1.0) * 100.0
        
        depeg_rows.append({
            "timestamp": row["timestamp"],
            "asset": "STETH",
            "token_address": row["token_address"],
            "quote_asset": "ETH",
            "price_value": depeg_pct,
            "price_type": "depeg_metric",
            "source": f"Derived from {row['price_type']}",
            "snapshot_block": row["snapshot_block"],
            "reference_timestamp": row["reference_timestamp"],
            "staleness_seconds": row["staleness_seconds"],
            "note": f"stETH/ETH Depeg Percentage: {depeg_pct:.4f}% (Negative = Discount)",
        })
    return pd.DataFrame(depeg_rows)[REQUIRED_COLUMNS]


def market_input(df: pd.DataFrame) -> pd.DataFrame:
    """Existing N3 full price table; or legacy fetch_prices market-only parquet."""
    if set(REQUIRED_COLUMNS).issubset(df.columns):
        out = df[REQUIRED_COLUMNS].copy()
        out = out.loc[~out["price_type"].isin(SNAPSHOT_TYPES)].copy()
        out["timestamp"] = to_utc(out["timestamp"], "market.timestamp")
        out["reference_timestamp"] = pd.to_datetime(out["reference_timestamp"], utc=True, errors="coerce")
        out["price_value"] = pd.to_numeric(out["price_value"], errors="coerce")
        if out["price_value"].isna().any() or (out["price_value"] <= 0).any():
            raise ValueError("N3 market price_value null/<=0")
        return out
    
    check_columns(df, {"timestamp", "asset", "price_usd"}, "N3 market legacy")
    base = df.copy()
    base["timestamp"] = to_utc(base["timestamp"], "legacy.timestamp")
    base["price_usd"] = pd.to_numeric(base["price_usd"], errors="coerce")
    if base["price_usd"].isna().any() or (base["price_usd"] <= 0).any():
        raise ValueError("N3 legacy price_usd null/<=0")
    
    market = pd.DataFrame({
        "timestamp": base["timestamp"], "asset": base["asset"].astype(str).str.upper(),
        "token_address": pd.NA, "quote_asset": "USD", "price_value": base["price_usd"],
        "price_type": "market_reference", "source": base.get("source", "Unverified N3 CSV"),
        "snapshot_block": pd.NA, "reference_timestamp": base["timestamp"],
        "staleness_seconds": 0.0,
        "note": "Gia thi truong, KHONG phai Aave oracle; imported from fetch_prices.py",
    })
    
    ratios = []
    for num, den in (("STETH", "ETH"), ("WSTETH", "ETH"), ("WSTETH", "STETH")):
        numerator = base.loc[base["asset"].astype(str).str.upper() == num, ["timestamp", "price_usd"]].sort_values("timestamp")
        denom = base.loc[base["asset"].astype(str).str.upper() == den, ["timestamp", "price_usd"]].sort_values("timestamp")
        if numerator.empty or denom.empty:
            continue
        matched = pd.merge_asof(
            numerator.rename(columns={"price_usd": "nu"}),
            denom.rename(columns={"timestamp": "ref_ts", "price_usd": "de"}),
            left_on="timestamp", right_on="ref_ts", direction="backward",
            tolerance=pd.Timedelta(minutes=90),
        ).dropna(subset=["de"])
        if matched.empty:
            continue
        ratios.append(pd.DataFrame({
            "timestamp": matched["timestamp"], "asset": num, "token_address": pd.NA,
            "quote_asset": den, "price_value": matched["nu"] / matched["de"],
            "price_type": "market_ratio", "source": "Derived from N3 historical market USD quotes",
            "snapshot_block": pd.NA, "reference_timestamp": matched["ref_ts"],
            "staleness_seconds": (matched["timestamp"] - matched["ref_ts"]).dt.total_seconds(),
            "note": "Market price ratio (asof backward); NOT Lido stEthPerToken or oracle",
        }))
    return pd.concat([market, *ratios], ignore_index=True)[REQUIRED_COLUMNS]


def build_oracle(oracle: pd.DataFrame, reg: pd.DataFrame, config: dict[int, dict]) -> tuple[pd.DataFrame, dict]:
    check_columns(oracle, {
        "snapshot_id", "snapshot_block", "snapshot_timestamp", "token_address", "token_symbol",
        "oracle_price_raw", "price_usd", "base_currency_unit", "aave_oracle",
        "price_source", "feed_updated_at_utc", "feed_staleness_sec",
    }, "N2 oracle_prices.parquet")
    check_columns(reg, {"token_address", "symbol", "decimals"}, "N2 token_registry.csv")
    
    reg = reg.copy()
    reg["token_address"] = validate_address(reg["token_address"], "registry.token_address")
    registry = reg.set_index("token_address")
    
    d = oracle.copy()
    d["token_address"] = validate_address(d["token_address"], "oracle.token_address")
    d["snapshot_timestamp"] = to_utc(d["snapshot_timestamp"], "oracle.snapshot_timestamp")
    d["snapshot_block"] = pd.to_numeric(d["snapshot_block"], errors="raise").astype("int64")
    d["price_usd"] = pd.to_numeric(d["price_usd"], errors="raise")
    
    sym = d["token_address"].map(registry["symbol"]).astype(str).str.strip().str.upper()
    source = "AaveOracle.getAssetPrice() at archive block; oracle=" + d["aave_oracle"].astype(str)
    
    out = pd.DataFrame({
        "timestamp": d["snapshot_timestamp"], "asset": sym,
        "token_address": d["token_address"], "quote_asset": "USD",
        "price_value": d["price_usd"], "price_type": "aave_oracle",
        "source": source, "snapshot_block": d["snapshot_block"],
        "reference_timestamp": pd.to_datetime(d["feed_updated_at_utc"], utc=True, errors="coerce"),
        "staleness_seconds": pd.to_numeric(d["feed_staleness_sec"], errors="coerce"),
        "note": "On-chain Aave effective oracle USD/token",
    })
    
    report = {"oracle_rows": int(len(out)), "validation_passed": True}
    return out, report


def build_lido(lido: pd.DataFrame, config: dict[int, dict]) -> pd.DataFrame:
    check_columns(lido, {
        "snapshot_id", "snapshot_block", "snapshot_timestamp", "wsteth_address",
        "steth_per_wsteth", "source",
    }, "N2 lido_ratios.parquet")
    d = lido.copy()
    d["snapshot_timestamp"] = to_utc(d["snapshot_timestamp"], "lido.snapshot_timestamp")
    d["snapshot_block"] = pd.to_numeric(d["snapshot_block"], errors="raise").astype("int64")
    d["wsteth_address"] = validate_address(d["wsteth_address"], "lido.wsteth_address")
    d["steth_per_wsteth"] = pd.to_numeric(d["steth_per_wsteth"], errors="raise")
    
    return pd.DataFrame({
        "timestamp": d["snapshot_timestamp"], "asset": "WSTETH", "token_address": d["wsteth_address"],
        "quote_asset": "STETH", "price_value": d["steth_per_wsteth"],
        "price_type": "lido_contract_conversion", "source": d["source"].astype(str),
        "snapshot_block": d["snapshot_block"], "reference_timestamp": d["snapshot_timestamp"],
        "staleness_seconds": 0.0,
        "note": "1 wstETH = price_value stETH via Lido stEthPerToken() at snapshot block",
    })


def integrate(existing: pd.DataFrame, oracle: pd.DataFrame, lido: pd.DataFrame,
              reg: pd.DataFrame, metadata: dict) -> tuple[pd.DataFrame, dict]:
    configs = snapshot_config(metadata)
    market = market_input(existing)
    oracle_rows, oracle_info = build_oracle(oracle, reg, configs)
    lido_rows = build_lido(lido, configs)
    
    # Calculate Depeg series
    depeg_rows = compute_depeg_metrics(market)
    
    merged = pd.concat([market, oracle_rows, lido_rows, depeg_rows], ignore_index=True)[REQUIRED_COLUMNS]
    merged["timestamp"] = pd.to_datetime(merged["timestamp"], utc=True)
    merged["reference_timestamp"] = pd.to_datetime(merged["reference_timestamp"], utc=True)
    merged["snapshot_block"] = pd.array(merged["snapshot_block"], dtype="Int64")
    merged["price_value"] = pd.to_numeric(merged["price_value"], errors="coerce")
    merged["staleness_seconds"] = pd.to_numeric(merged["staleness_seconds"], errors="coerce")
    merged = merged.sort_values(["timestamp", "price_type", "asset", "quote_asset"], kind="stable").reset_index(drop=True)
    
    report = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "rows_by_price_type": {str(k): int(v) for k, v in merged.groupby("price_type").size().items()},
        "validation_passed": True,
    }
    return merged, report


def safe_write_parquet(df: pd.DataFrame, path: Path, allow_overwrite: bool) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and not allow_overwrite:
        raise FileExistsError(f"{path} da co. Chay --overwrite de sao luu")
    import pyarrow as pa
    import pyarrow.parquet as pq
    arr = pa.Table.from_pandas(df, preserve_index=False)
    fd, temp = tempfile.mkstemp(dir=path.parent, prefix=path.name + ".", suffix=".tmp")
    os.close(fd)
    try:
        pq.write_table(arr, temp, compression="snappy")
        os.replace(temp, path)
    finally:
        Path(temp).unlink(missing_ok=True)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--market", type=Path, default=ROOT / "data/staging/n3_price_timeseries.parquet")
    ap.add_argument("--oracle", type=Path, default=ROOT / "data/processed/aave/oracle_prices.parquet")
    ap.add_argument("--lido", type=Path, default=ROOT / "data/processed/aave/lido_ratios.parquet")
    ap.add_argument("--registry", type=Path, default=ROOT / "data/processed/aave/token_registry.csv")
    ap.add_argument("--metadata", type=Path, default=ROOT / "data/processed/aave/snapshot_metadata.json")
    ap.add_argument("--output", type=Path, default=ROOT / "data/staging/n3_price_timeseries.parquet")
    ap.add_argument("--quality-output", type=Path, default=ROOT / "outputs/tables/n3_price_timeseries_quality.json")
    ap.add_argument("--overwrite", action="store_true")
    args = ap.parse_args()

    metadata = json.loads(args.metadata.read_text(encoding="utf-8"))
    existing = pd.read_parquet(args.market)
    oracle = pd.read_parquet(args.oracle)
    lido = pd.read_parquet(args.lido)
    registry = pd.read_csv(args.registry)
    
    merged, qa = integrate(existing, oracle, lido, registry, metadata)
    safe_write_parquet(merged, args.output, allow_overwrite=args.overwrite)
    print(f"[SUCCESS N3 DEPEG & TIME-SERIES] Updated {args.output}")


if __name__ == "__main__":
    main()