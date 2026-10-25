#!/usr/bin/env python3
"""N3 Muc 3 - Tich hop N3 market prices + N2 Aave oracle + Lido snapshots.

Inputs (no API call):
  data/staging/n3_price_timeseries.parquet  (N3: market_reference/market_ratio)
  data/processed/aave/oracle_prices.parquet (N2: real Aave getAssetPrice at snapshot)
  data/processed/aave/lido_ratios.parquet   (N2: real Lido stEthPerToken at snapshot)
  data/processed/aave/snapshot_metadata.json
  data/processed/aave/token_registry.csv

Output: data/staging/n3_price_timeseries.parquet
         outputs/tables/n3_price_timeseries_quality.json

Preserves observed N3 market data, never substitutes market ratios for Lido on-chain
conversion, never fakes Chainlink answers, never extrapolates snapshot oracle to
historical liquidation event timestamps. Safe backup/atomic replacement.
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
SNAPSHOT_TYPES = {"aave_oracle", "lido_contract_conversion"}
ADDR_RE = re.compile(r"0x[a-f0-9]{40}")
LIDO_WSTETH = "0x7f39c581f595b53c5cb19bd0b3f8da6c935e2ca0"


def check_columns(df: pd.DataFrame, required: set[str], name: str) -> None:
    missing = sorted(required - set(df.columns))
    if missing:
        raise ValueError(f"{name}: thieu cot {missing}. Cot hien co: {list(df.columns)}")


def to_utc(series: pd.Series, name: str) -> pd.Series:
    if pd.api.types.is_numeric_dtype(series):
        num = pd.to_numeric(series, errors="coerce")
        median = num.dropna().abs().median()
        unit = "ns" if median >= 1e17 else ("us" if median >= 1e14 else ("ms" if median >= 1e11 else "s"))
        result = pd.to_datetime(num, unit=unit, errors="coerce", utc=True)
    else:
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
    if set(snapshots) != {"A", "B1", "B2"}:
        raise ValueError("Can dung ba snapshot A/B1/B2 trong snapshot_metadata.json")
    for sid, expected in {"A": 26179841, "B1": 20458637, "B2": 23543615}.items():
        if int(snapshots[sid]["snapshot_block"]) != expected:
            raise ValueError(f"{sid}: block {snapshots[sid]['snapshot_block']} khong khop {expected}")
    result = {}
    for sid, info in snapshots.items():
        block = int(info["snapshot_block"])
        ts = pd.to_datetime(info["snapshot_timestamp_utc"], utc=True)
        if not bool(info.get("block_hash_verified_on_chain")):
            raise ValueError(f"Snapshot {sid}: block hash chua duoc verify")
        if block in result:
            raise ValueError("Snapshot A/B1/B2 trung block")
        result[block] = {"snapshot_id": sid, "timestamp": ts,
                         "n_reserves": int(info["n_reserves"]),
                         "aave_oracle": str(info["aave_oracle"]).lower()}
    return result


def market_input(df: pd.DataFrame) -> pd.DataFrame:
    """Existing N3 full price table; or legacy fetch_prices market-only parquet."""
    if set(REQUIRED_COLUMNS).issubset(df.columns):
        out = df[REQUIRED_COLUMNS].copy()
        # Rerunnable: replace snapshot-derived rows only, never double count.
        out = out.loc[~out["price_type"].isin(SNAPSHOT_TYPES)].copy()
        out["timestamp"] = to_utc(out["timestamp"], "market.timestamp")
        out["reference_timestamp"] = pd.to_datetime(out["reference_timestamp"], utc=True, errors="coerce")
        out["price_value"] = pd.to_numeric(out["price_value"], errors="coerce")
        if out["price_value"].isna().any() or (out["price_value"] <= 0).any():
            raise ValueError("N3 market price_value null/<=0")
        return out
    # Legacy: fetch_prices.py writes raw price_usd table into same path.
    check_columns(df, {"timestamp", "asset", "price_usd"}, "N3 market legacy")
    base = df.copy()
    base["timestamp"] = to_utc(base["timestamp"], "legacy.timestamp")
    base["price_usd"] = pd.to_numeric(base["price_usd"], errors="coerce")
    if base["price_usd"].isna().any() or (base["price_usd"] <= 0).any():
        raise ValueError("N3 legacy price_usd null/<=0")
    if base.duplicated(["timestamp", "asset"]).any():
        raise ValueError("N3 legacy trung (timestamp, asset)")
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
    if reg.duplicated("token_address").any():
        raise ValueError("Registry trung token_address")
    registry = reg.set_index("token_address")
    d = oracle.copy()
    d["token_address"] = validate_address(d["token_address"], "oracle.token_address")
    d["snapshot_timestamp"] = to_utc(d["snapshot_timestamp"], "oracle.snapshot_timestamp")
    d["snapshot_block"] = pd.to_numeric(d["snapshot_block"], errors="raise").astype("int64")
    d["price_usd"] = pd.to_numeric(d["price_usd"], errors="raise")
    if set(d["snapshot_block"]) != set(config):
        raise ValueError("Oracle snapshot blocks khong khop 2 block trong N2 metadata")
    if d.duplicated(["snapshot_block", "token_address"]).any():
        raise ValueError("Oracle trung (snapshot_block,token_address)")
    if not d["token_address"].isin(registry.index).all():
        missing = d.loc[~d["token_address"].isin(registry.index), "token_address"].unique().tolist()
        raise ValueError(f"Oracle token khong co trong N2 registry: {missing[:6]}")
    invalid = (~d["price_usd"].map(lambda x: math.isfinite(x) and x > 0))
    if invalid.any():
        raise ValueError(f"Co {int(invalid.sum())} gia oracle khong hop le")
    if (pd.to_numeric(d["base_currency_unit"], errors="coerce") != 100_000_000).any():
        raise ValueError("N2 Oracle BASE_CURRENCY_UNIT khong phai 1e8 USD")
    d["oracle_price_raw"] = pd.to_numeric(d["oracle_price_raw"], errors="coerce")
    if d["oracle_price_raw"].isna().any():
        raise ValueError("N2 oracle_price_raw khong hop le")
    relative_err = ((d["oracle_price_raw"] / 1e8 - d["price_usd"]).abs() / d["price_usd"]).max()
    if relative_err > 1e-8:
        raise ValueError(f"Oracle raw / 1e8 != price_usd, max relative error {relative_err}")
    d["feed_updated_at_utc"] = pd.to_datetime(d["feed_updated_at_utc"], utc=True, errors="coerce")
    d["feed_staleness_sec"] = pd.to_numeric(d["feed_staleness_sec"], errors="coerce")
    d["aave_oracle"] = d["aave_oracle"].astype(str).str.lower()
    for block, conf in config.items():
        s = d[d["snapshot_block"] == block]
        if len(s) != conf["n_reserves"]:
            raise ValueError(f"Snapshot {conf['snapshot_id']} oracle co {len(s)} tokens, metadata {conf['n_reserves']}")
        if not (s["snapshot_id"] == conf["snapshot_id"]).all():
            raise ValueError(f"Snapshot {block} bi nham snapshot_id")
        if not (s["snapshot_timestamp"] == conf["timestamp"]).all():
            raise ValueError(f"Snapshot {block} timestamp khac metadata; khong tu thay")
        if not (s["aave_oracle"] == conf["aave_oracle"]).all():
            raise ValueError(f"Snapshot {block} oracle address khac metadata")
    # If feed observation is provided, its age must be >=0 and match the snapshot time.
    feed_age = (d["snapshot_timestamp"] - d["feed_updated_at_utc"]).dt.total_seconds()
    if ((feed_age < -1) & feed_age.notna()).any():
        raise ValueError("Oracle feed updated_at sau snapshot (look-ahead bias)")
    supplied_age = d["feed_staleness_sec"]
    if ((supplied_age - feed_age).abs() > 2).fillna(False).any():
        raise ValueError("Feed staleness_sec khong khop timestamp trong N2 oracle")
    sym = d["token_address"].map(registry["symbol"]).astype(str).str.strip().str.upper()
    if (sym == "").any() or sym.isna().any():
        raise ValueError("Token symbol trong registry bi thieu")
    descr = d.get("feed_description", pd.Series([None]*len(d), index=d.index))
    source = "AaveOracle.getAssetPrice() at archive block; oracle=" + d["aave_oracle"].astype(str)
    source = source + "; underlying_source=" + d["price_source"].fillna("unknown").astype(str)
    note = "On-chain Aave effective oracle USD/token; not price-at-liquidation-event. Feed: " + descr.fillna("unknown").astype(str)
    missing_feed = d["feed_updated_at_utc"].isna()
    note = note + missing_feed.map({True: "; underlying update time unavailable (adapter/fixed source)", False: ""})
    out = pd.DataFrame({
        "timestamp": d["snapshot_timestamp"], "asset": sym,
        "token_address": d["token_address"], "quote_asset": "USD",
        "price_value": d["price_usd"], "price_type": "aave_oracle",
        "source": source, "snapshot_block": d["snapshot_block"],
        "reference_timestamp": d["feed_updated_at_utc"],
        "staleness_seconds": feed_age,
        "note": note,
    })
    report = {
        "oracle_rows": int(len(out)),
        "oracle_by_snapshot": {conf["snapshot_id"]: int((d["snapshot_block"] == block).sum())
                               for block, conf in config.items()},
        "oracle_feed_timestamp_missing": int(missing_feed.sum()),
        "oracle_feed_gt_24h_count_where_known": int((feed_age > 24 * 3600).sum()),
        "oracle_raw_usd_max_relative_error": float(relative_err),
    }
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
    if set(d["snapshot_block"]) != set(config) or len(d) != len(config):
        raise ValueError("Lido phai co dung 3 hang, ung voi Snapshot A/B1/B2")
    if d.duplicated(["snapshot_block"]).any():
        raise ValueError("Lido trung snapshot_block")
    if (d["wsteth_address"] != LIDO_WSTETH).any():
        raise ValueError("Lido wstETH address khong phai Ethereum mainnet")
    if not d["steth_per_wsteth"].map(lambda v: math.isfinite(v) and v > 0).all():
        raise ValueError("Lido stEthPerToken khong hop le")
    for b, conf in config.items():
        s = d.loc[d["snapshot_block"] == b]
        if not (s["snapshot_id"] == conf["snapshot_id"]).all():
            raise ValueError(f"Lido snapshot_id sai tai block {b}")
        if not (s["snapshot_timestamp"] == conf["timestamp"]).all():
            raise ValueError(f"Lido timestamp sai tai block {b}")
    return pd.DataFrame({
        "timestamp": d["snapshot_timestamp"], "asset": "WSTETH", "token_address": d["wsteth_address"],
        "quote_asset": "STETH", "price_value": d["steth_per_wsteth"],
        "price_type": "lido_contract_conversion", "source": d["source"].astype(str),
        "snapshot_block": d["snapshot_block"], "reference_timestamp": d["snapshot_timestamp"],
        "staleness_seconds": 0.0,
        "note": "1 wstETH = price_value stETH via Lido stEthPerToken() at snapshot block. NOT market depeg ratio",
    })


def validate_market(market: pd.DataFrame, snapshots: dict[int, dict], max_lag_min: int) -> dict:
    """Snapshot readiness: report gaps, never borrow oracle or future market quotes.

    An absent or old quote is a QA finding, not a reason to overwrite observations
    or silently use a Lido contract conversion as stETH/ETH market depeg.
    """
    mkt = market.loc[market["price_type"] == "market_reference"].copy()
    if mkt.empty:
        raise ValueError("Khong co market_reference; chua the QA market data")
    if market["source"].isna().any() or market["source"].astype(str).str.strip().eq("").any():
        raise ValueError("N3 market thieu source")
    ref = pd.to_datetime(market["reference_timestamp"], utc=True, errors="coerce")
    where_ref_available = ref.notna()
    if (ref.loc[where_ref_available] > market.loc[where_ref_available, "timestamp"]).any():
        raise ValueError("N3 market su dung timestamp tu tuong lai")
    if (pd.to_numeric(market["staleness_seconds"], errors="coerce").dropna() < 0).any():
        raise ValueError("N3 market co staleness_seconds am")
    dup = market.duplicated(["timestamp", "asset", "quote_asset", "price_type"]).sum()
    if dup:
        raise ValueError(f"N3 market trung {int(dup)} cap (timestamp,asset,quote,price_type)")
    checks = {}
    for block, conf in snapshots.items():
        snap = conf["timestamp"]
        measures = {}
        for symbol in ("ETH", "STETH", "WSTETH"):
            quotes = mkt.loc[
                (mkt["asset"].astype(str).str.upper() == symbol) &
                (mkt["quote_asset"].astype(str).str.upper() == "USD")
            ].copy()
            effective = pd.to_datetime(quotes["reference_timestamp"], utc=True, errors="coerce")
            quotes["observed_at"] = effective.where(effective.notna(), quotes["timestamp"])
            quotes = quotes.loc[quotes["observed_at"] <= snap].sort_values("observed_at")
            if quotes.empty:
                measures[symbol] = {"status": "MISSING_PRIOR_MARKET_QUOTE", "quote_utc": None,
                                    "price_usd": None, "age_seconds": None}
                continue
            prev = quotes.iloc[-1]
            lag = (snap - prev["observed_at"]).total_seconds()
            status = "OK" if 0 <= lag <= max_lag_min * 60 else "STALE_MARKET_QUOTE"
            measures[symbol] = {"status": status, "quote_utc": prev["observed_at"].isoformat(),
                                "price_usd": float(prev["price_value"]), "age_seconds": lag}
        valid = all(measures[sym]["status"] == "OK" for sym in ("ETH", "STETH", "WSTETH"))
        eth, steth = (measures[sym]["price_usd"] for sym in ("ETH", "STETH"))
        measures["steth_eth_market_ratio_asof"] = (
            steth / eth if eth and steth and
            measures["ETH"]["status"] == measures["STETH"]["status"] == "OK" else None
        )
        measures["market_snapshot_ready"] = valid
        measures["snapshot_utc"] = snap.isoformat()
        checks[conf["snapshot_id"]] = measures
    return checks


def integrate(existing: pd.DataFrame, oracle: pd.DataFrame, lido: pd.DataFrame,
              reg: pd.DataFrame, metadata: dict, *, max_market_lag_min: int = 90) -> tuple[pd.DataFrame, dict]:
    configs = snapshot_config(metadata)
    market = market_input(existing)
    oracle_rows, oracle_info = build_oracle(oracle, reg, configs)
    lido_rows = build_lido(lido, configs)
    market_checks = validate_market(market, configs, max_market_lag_min)
    merged = pd.concat([market, oracle_rows, lido_rows], ignore_index=True)[REQUIRED_COLUMNS]
    merged["timestamp"] = pd.to_datetime(merged["timestamp"], utc=True)
    merged["reference_timestamp"] = pd.to_datetime(merged["reference_timestamp"], utc=True)
    merged["snapshot_block"] = pd.array(merged["snapshot_block"], dtype="Int64")
    merged["price_value"] = pd.to_numeric(merged["price_value"], errors="coerce")
    merged["staleness_seconds"] = pd.to_numeric(merged["staleness_seconds"], errors="coerce")
    merged = merged.sort_values(["timestamp", "price_type", "asset", "quote_asset"], kind="stable").reset_index(drop=True)
    if merged["price_value"].isna().any() or not merged["price_value"].map(lambda v: math.isfinite(v) and v > 0).all():
        raise ValueError("Price output co gia thieu/<=0")
    if (merged.loc[merged["reference_timestamp"].notna(), "reference_timestamp"] > merged.loc[merged["reference_timestamp"].notna(), "timestamp"]).any():
        raise ValueError("Output co timestamp cua tuong lai")
    report = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "protocol_id": metadata["protocol_id"], "chain_id": metadata["chain_id"],
        "snapshot_block_by_id": {v["snapshot_id"]: k for k, v in configs.items()},
        "market_ratio_definitions": {
            "STETH/ETH": "indicative stETH/USD divided by ETH/USD market references; not oracle",
            "WSTETH/STETH": "market reference ratio only; not Lido stEthPerToken",
            "lido_conversion": "stEthPerToken() from Lido at exact snapshot block",
        },
        "rows_by_price_type": {str(k): int(v) for k,v in merged.groupby("price_type").size().items()},
        "market_reference_freshness_by_snapshot": market_checks,
        "all_snapshots_market_ready": all(v["market_snapshot_ready"] for v in market_checks.values()),
        "n2_oracle_audit": oracle_info,
        "missing_feed_timestamp_rule": "NULL reference_timestamp/staleness for oracle adapters without feed updatedAt; never assign age zero",
        "not_present": ["Historical Chainlink feed rounds independently queried", "Oracle prices at each liquidation event_block", "DEX liquidity/tick-level order depth"],
        "research_note": "Snapshot B role must be approved by N1, and historical liquidation validation window must be checked independently.",
        "validation_passed": True,
        "market_readiness_scope": "structural QA separate from market price completeness",
    }
    return merged, report


def safe_write_parquet(df: pd.DataFrame, path: Path, allow_overwrite: bool) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and not allow_overwrite:
        raise FileExistsError(f"{path} da co. Chay --overwrite de sao luu truoc khi ghi")
    # Validate serializability BEFORE backing up/overwriting previous study data.
    import pyarrow as pa
    import pyarrow.parquet as pq
    arr = pa.Table.from_pandas(df, preserve_index=False)
    fd, temp = tempfile.mkstemp(dir=path.parent, prefix=path.name + ".", suffix=".tmp")
    os.close(fd)
    try:
        pq.write_table(arr, temp, compression="snappy")
        if path.exists():
            stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            dest = path.with_name(path.name + ".bak_" + stamp)
            shutil.copy2(path, dest)
            print(f"[BACKUP] {dest}")
        os.replace(temp, path)
    finally:
        Path(temp).unlink(missing_ok=True)


def safe_json(payload: dict, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(dir=path.parent, prefix=path.name + ".", suffix=".tmp")
    os.close(fd)
    tmp = Path(name)
    try:
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        if path.exists():
            stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            backup = path.with_name(path.name + ".bak_" + stamp)
            shutil.copy2(path, backup)
            print(f"[BACKUP] {backup}")
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


def main() -> None:
    ap = argparse.ArgumentParser(description="N3 muc 3: Ghep market + Aave oracle + Lido ty le, khong can goi API")
    ap.add_argument("--market", type=Path, default=ROOT / "data/staging/n3_price_timeseries.parquet")
    ap.add_argument("--oracle", type=Path, default=ROOT / "data/processed/aave/oracle_prices.parquet")
    ap.add_argument("--lido", type=Path, default=ROOT / "data/processed/aave/lido_ratios.parquet")
    ap.add_argument("--registry", type=Path, default=ROOT / "data/processed/aave/token_registry.csv")
    ap.add_argument("--metadata", type=Path, default=ROOT / "data/processed/aave/snapshot_metadata.json")
    ap.add_argument("--output", type=Path, default=ROOT / "data/staging/n3_price_timeseries.parquet")
    ap.add_argument("--quality-output", type=Path, default=ROOT / "outputs/tables/n3_price_timeseries_quality.json")
    ap.add_argument("--max-market-lag-minutes", type=int, default=90)
    ap.add_argument("--dry-run", action="store_true", help="Chi kiem tra va in QA, KHONG ghi file")
    ap.add_argument("--require-market-freshness", action="store_true", help="Fail if any snapshot lacks fresh ETH/STETH/WSTETH market quotes")
    ap.add_argument("--overwrite", action="store_true", help="Backup parquet roi thay the an toan")
    args = ap.parse_args()
    if args.max_market_lag_minutes <= 0:
        raise ValueError("max-market-lag-minutes phai >0")
    for p in (args.market, args.oracle, args.lido, args.registry, args.metadata):
        if not p.is_file():
            raise FileNotFoundError(f"Khong tim thay: {p}")
    metadata = json.loads(args.metadata.read_text(encoding="utf-8"))
    existing = pd.read_parquet(args.market)
    oracle = pd.read_parquet(args.oracle)
    lido = pd.read_parquet(args.lido)
    registry = pd.read_csv(args.registry)
    merged, qa = integrate(existing, oracle, lido, registry, metadata,
                           max_market_lag_min=args.max_market_lag_minutes)
    print("\n=== KET QUA MUC 3 N3 ===")
    print("Snapshot blocks:", qa["snapshot_block_by_id"])
    for kind, n in qa["rows_by_price_type"].items():
        print(f"  {kind}: {n}")
    print("Feed timestamp khong duoc N2 xac minh:", qa["n2_oracle_audit"]["oracle_feed_timestamp_missing"])
    for sid, values in qa["market_reference_freshness_by_snapshot"].items():
        print(f"  Snapshot {sid}: market_ready={values['market_snapshot_ready']}; "
              f"stETH/ETH asof={values['steth_eth_market_ratio_asof']}")
    if args.require_market_freshness and not qa["all_snapshots_market_ready"]:
        raise SystemExit("[STOP] B1/B2 market quotes missing/stale; no data overwritten")
    if args.dry_run:
        print("[DRY RUN] Khong ghi bat ky file nao.")
        return
    if args.output.exists() and not args.overwrite:
        raise FileExistsError(f"{args.output} da ton tai. Chay --overwrite de backup truoc khi cap nhat")
    safe_write_parquet(merged, args.output, allow_overwrite=args.overwrite)
    safe_json(qa, args.quality_output)
    print(f"[SAVED] {args.output}")
    print(f"[QUALITY] {args.quality_output}")
    print("[DONE] Muc 3 N3 - market, market ratios, Aave oracle, Lido conversion; CHUA bao gom Chainlink direct rounds.")


if __name__ == "__main__":
    main()
