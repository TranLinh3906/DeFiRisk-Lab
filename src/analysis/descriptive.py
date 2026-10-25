#!/usr/bin/env python3
"""N3 MUC 5: descriptive Aave liquidation, ETH, and stETH/ETH depeg.

Input (N3 staging): fact-ready liquidation events (11 Data Contract fields)
                 + market reference and market_ratio from N3 Muc 3.
Output: UTC time-series CSV, event/market alignment, token breakdowns,
        quality JSON and PNG figures. NO position-level causal claims.

Never reads snapshot A/B oracle as event-time market quotes. All event-aligned
quotes use backward as-of with a strict maximum quote age.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_EVENTS = ROOT / "data/staging/n3_liquidations.parquet"
DEFAULT_PRICES = ROOT / "data/staging/n3_price_timeseries.parquet"
DEFAULT_REGISTRY = ROOT / "data/processed/aave/token_registry.csv"
OUT_TABLES = ROOT / "outputs/tables"
OUT_FIGS = ROOT / "outputs/figures"
EVENT_KEYS = ["tx_hash", "log_index"]
NEEDED_EVENTS = {
    "tx_hash", "log_index", "event_block", "event_timestamp", "protocol_id",
    "collateral_token_address", "debt_token_address", "wallet_address",
    "debt_repaid_usd", "collateral_seized_usd",
}
NEEDED_PRICES = {"timestamp", "asset", "quote_asset", "price_value", "price_type"}


def utc(s: pd.Series) -> pd.Series:
    return pd.to_datetime(s, utc=True, errors="coerce")


def safe_write(path: Path, method, overwrite: bool) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        if not overwrite:
            raise FileExistsError(f"Da co {path}; truyen --overwrite neu muon cap nhat (co backup)")
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        shutil.copy2(path, path.with_name(f"{path.name}.bak_{stamp}"))
    fd, p = tempfile.mkstemp(prefix=".n3_", suffix=".tmp", dir=path.parent)
    os.close(fd)
    try:
        method(Path(p))
        os.replace(p, path)
    finally:
        Path(p).unlink(missing_ok=True)


def load_events(df: pd.DataFrame) -> pd.DataFrame:
    miss = sorted(NEEDED_EVENTS - set(df.columns))
    if miss:
        raise ValueError(f"Muc 5: liquidation thieu cot: {miss}")
    if df.empty:
        raise ValueError("File liquidation khong co su kien")
    data = df.copy()
    if data.duplicated(EVENT_KEYS).any():
        raise ValueError("Trung khoa tx_hash,log_index")
    if "n3_validation_status" in data.columns:
        data = data.loc[data["n3_validation_status"].eq("ok")].copy()
    data["event_timestamp"] = utc(data["event_timestamp"])
    for name in ("debt_repaid_usd", "collateral_seized_usd"):
        data[name] = pd.to_numeric(data[name], errors="coerce")
        if data[name].isna().any() or (~data[name].map(lambda x: math.isfinite(x) and x >= 0)).any():
            raise ValueError(f"Co USD khong hop le (null, am, vo cung): {name}")
    if data.empty or data["event_timestamp"].isna().any():
        raise ValueError("Su kien bi rong hoac timestamp khong hop le")
    for c in ("collateral_token_address", "debt_token_address", "wallet_address"):
        data[c] = data[c].astype(str).str.strip().str.lower()
    data = data.sort_values("event_timestamp").reset_index(drop=True)
    return data


def load_prices(frame: pd.DataFrame) -> pd.DataFrame:
    miss = sorted(NEEDED_PRICES - set(frame.columns))
    if miss:
        raise ValueError(f"Muc 5: price_timeseries thieu cot: {miss}")
    p = frame.loc[frame["price_type"].isin(["market_reference", "market_ratio"])].copy()
    p["timestamp"] = utc(p["timestamp"])
    p["asset"] = p["asset"].astype(str).str.strip().str.upper()
    p["quote_asset"] = p["quote_asset"].astype(str).str.strip().str.upper()
    p["price_value"] = pd.to_numeric(p["price_value"], errors="coerce")
    if p.empty or p.timestamp.isna().any() or p.price_value.isna().any():
        raise ValueError("Gia market_ref/market_ratio rong hoac khong hop le")
    if (p["price_value"] <= 0).any() or (~p["price_value"].map(math.isfinite)).any():
        raise ValueError("Gia market_ref/market_ratio <=0/inf")
    if "reference_timestamp" in p:
        ref = utc(p["reference_timestamp"])
        bad = ref.notna() & (ref > p["timestamp"])
        if bad.any():
            raise ValueError("Phat hien market_reference dung gia tuong lai (reference_timestamp > timestamp)")
    return p.sort_values("timestamp")


def series(p: pd.DataFrame, ptype: str, asset: str, quote: str) -> pd.DataFrame:
    filtered = p.loc[(p.price_type == ptype) & (p.asset == asset) & (p.quote_asset == quote)].copy()
    # Reference timestamp, when available, is the actual market observation
    # time. Using the resampled nominal timestamp would overstate freshness.
    if "reference_timestamp" in filtered.columns:
        ref = utc(filtered["reference_timestamp"])
        filtered["timestamp"] = ref.where(ref.notna(), filtered["timestamp"])
    d = filtered[["timestamp", "price_value"]].copy()
    d = d.drop_duplicates(["timestamp"], keep="last").sort_values("timestamp")
    return d


def backward_align(left: pd.DataFrame, prices: pd.DataFrame, ts_col: str,
                   prefix: str, tolerance_min: int) -> pd.DataFrame:
    left = left.sort_values(ts_col).copy()
    if prices.empty:
        left[f"{prefix}_price"] = math.nan
        left[f"{prefix}_quote_time_utc"] = pd.NaT
        left[f"{prefix}_quote_age_sec"] = math.nan
        return left
    rhs = prices.rename(columns={"timestamp": f"{prefix}_quote_time_utc", "price_value": f"{prefix}_price"})
    aligned = pd.merge_asof(
        left, rhs, left_on=ts_col, right_on=f"{prefix}_quote_time_utc",
        direction="backward", tolerance=pd.Timedelta(minutes=tolerance_min),
    )
    aligned[f"{prefix}_quote_age_sec"] = (
        aligned[ts_col] - aligned[f"{prefix}_quote_time_utc"]
    ).dt.total_seconds()
    if aligned[f"{prefix}_quote_age_sec"].dropna().lt(0).any():
        raise AssertionError("Look-ahead detected in backward market merge")
    return aligned


def token_breakdown(events: pd.DataFrame, registry: pd.DataFrame | None,
                    token_col: str) -> pd.DataFrame:
    col = token_col
    result = events.groupby(col, as_index=False).agg(
        liquidation_events=("tx_hash", "size"),
        unique_transactions=("tx_hash", "nunique"),
        debt_repaid_usd=("debt_repaid_usd", "sum"),
        collateral_seized_usd=("collateral_seized_usd", "sum"),
    )
    if registry is not None and {"token_address", "symbol"}.issubset(registry):
        names = registry[["token_address", "symbol"]].copy()
        names["token_address"] = names["token_address"].astype(str).str.lower().str.strip()
        names = names.drop_duplicates("token_address")
        result = result.merge(names, left_on=col, right_on="token_address", how="left")
        if col != "token_address":
            result = result.drop(columns=["token_address"], errors="ignore")
        result = result.rename(columns={"symbol": "token_symbol"})
    else:
        result["token_symbol"] = pd.NA
    return result.sort_values("debt_repaid_usd", ascending=False).reset_index(drop=True)


def corr(frame: pd.DataFrame, a: str, b: str) -> dict:
    if a not in frame.columns or b not in frame.columns:
        return {"n": 0, "pearson": None, "spearman": None}
    pairs = frame[[a, b]].dropna()
    if len(pairs) < 10 or pairs[a].nunique() < 2 or pairs[b].nunique() < 2:
        return {"n": int(len(pairs)), "pearson": None, "spearman": None}
    return {"n": int(len(pairs)),
            "pearson": float(pairs[a].corr(pairs[b])),
            "spearman": float(pairs[a].corr(pairs[b], method="spearman"))}


def build(events: pd.DataFrame, prices: pd.DataFrame, registry: pd.DataFrame | None,
          max_age_min: int = 90, start: str | None = None, end: str | None = None) -> dict:
    e = load_events(events)
    p = load_prices(prices)
    t0 = pd.Timestamp(start).tz_localize("UTC") if start and pd.Timestamp(start).tzinfo is None else (pd.Timestamp(start).tz_convert("UTC") if start else min(e.event_timestamp.min(), p.timestamp.min()).floor("D"))
    t1 = pd.Timestamp(end).tz_localize("UTC") if end and pd.Timestamp(end).tzinfo is None else (pd.Timestamp(end).tz_convert("UTC") if end else max(e.event_timestamp.max(), p.timestamp.max()).floor("D") + pd.Timedelta(days=1))
    if t0 >= t1:
        raise ValueError("--start phai truoc --end")
    e = e[(e.event_timestamp >= t0) & (e.event_timestamp < t1)].copy()
    p = p[(p.timestamp >= t0 - pd.Timedelta(minutes=max_age_min)) & (p.timestamp < t1)].copy()
    if e.empty:
        raise ValueError("Khong co liquidation trong khung thoi gian da chon")
    e["event_hour_utc"] = e.event_timestamp.dt.floor("h")
    e["event_date_utc"] = e.event_timestamp.dt.floor("D")
    # Full UTC hour grid explicitly includes zero-event hours.
    grid = pd.DataFrame({"event_hour_utc": pd.date_range(t0.floor("h"), t1.ceil("h"), freq="h", inclusive="left")})
    h = e.groupby("event_hour_utc", as_index=False).agg(
        liquidation_events=("tx_hash", "size"),
        unique_transactions=("tx_hash", "nunique"),
        debt_repaid_usd=("debt_repaid_usd", "sum"),
        collateral_seized_usd=("collateral_seized_usd", "sum"),
    )
    hourly = grid.merge(h, on="event_hour_utc", how="left")
    hourly[["liquidation_events", "unique_transactions"]] = hourly[["liquidation_events", "unique_transactions"]].fillna(0).astype(int)
    hourly[["debt_repaid_usd", "collateral_seized_usd"]] = hourly[["debt_repaid_usd", "collateral_seized_usd"]].fillna(0.0)
    eth = series(p, "market_reference", "ETH", "USD")
    ratio = series(p, "market_ratio", "STETH", "ETH")
    # Event-aligned quotes must not be fetched from later timestamps.
    aligned_events = backward_align(e, eth, "event_timestamp", "eth_usd", max_age_min)
    aligned_events = backward_align(aligned_events, ratio, "event_timestamp", "steth_eth", max_age_min)
    aligned_events["steth_discount_pct"] = (1 - aligned_events["steth_eth_price"]) * 100
    # Hourly market references as of the END of each calendar hour (ex-post descriptive, not event feature).
    timeline = hourly.rename(columns={"event_hour_utc": "hour_utc"}).copy()
    timeline["hour_end_utc"] = timeline.hour_utc + pd.Timedelta(hours=1) - pd.Timedelta(nanoseconds=1)
    timeline = backward_align(timeline, eth, "hour_end_utc", "eth_usd", max_age_min)
    timeline = backward_align(timeline, ratio, "hour_end_utc", "steth_eth", max_age_min)
    timeline["eth_return_1h_pct"] = timeline["eth_usd_price"].pct_change(fill_method=None) * 100
    age0 = timeline["eth_usd_quote_time_utc"] - timeline["eth_usd_quote_time_utc"].shift(1)
    timeline.loc[age0 > pd.Timedelta(minutes=90), "eth_return_1h_pct"] = float("nan")
    timeline["steth_discount_pct"] = (1 - timeline["steth_eth_price"]) * 100
    daily = timeline.assign(date_utc=timeline.hour_utc.dt.floor("D")).groupby("date_utc", as_index=False).agg(
        liquidation_events=("liquidation_events", "sum"),
        debt_repaid_usd=("debt_repaid_usd", "sum"),
        collateral_seized_usd=("collateral_seized_usd", "sum"),
        eth_usd_close=("eth_usd_price", "last"),
        steth_eth_close=("steth_eth_price", "last"),
        steth_discount_pct_close=("steth_discount_pct", "last"),
    )
    # Empty market columns must stay NaN; never fill missing depeg with zero.
    col_break = token_breakdown(e, registry, "collateral_token_address")
    debt_break = token_breakdown(e, registry, "debt_token_address")
    correlations = {
        "hourly_debt_vs_eth_return": corr(timeline, "debt_repaid_usd", "eth_return_1h_pct"),
        "hourly_debt_vs_steth_discount": corr(timeline, "debt_repaid_usd", "steth_discount_pct"),
        "hourly_count_vs_steth_discount": corr(timeline, "liquidation_events", "steth_discount_pct"),
    }
    quality = {
        "method": "Observed descriptive association; same-hour contemporaneous correlations not predictive or causal",
        "event_rows": int(len(e)),
        "unique_transactions": int(e.tx_hash.nunique()),
        "unique_borrowers": int(e.wallet_address.nunique()),
        "debt_repaid_usd_total": float(e.debt_repaid_usd.sum()),
        "collateral_seized_usd_total": float(e.collateral_seized_usd.sum()),
        "hourly_bins_including_zero": int(len(timeline)),
        "zero_event_hours": int((timeline.liquidation_events == 0).sum()),
        "event_eth_price_quote_matched": int(aligned_events.eth_usd_price.notna().sum()),
        "event_steth_eth_ratio_matched": int(aligned_events.steth_eth_price.notna().sum()),
        "max_market_quote_age_min": int(max_age_min),
        "window": {"start_inclusive_utc": t0.isoformat(), "end_exclusive_utc": t1.isoformat()},
        "correlations": correlations,
        "caveats": [
            "CoinGecko/market observations are reference quotes, not on-chain execution prices or Chainlink state",
            "Hourly comparison is descriptive, cannot demonstrate depeg causes liquidations",
            "Token USD amounts value repaid debt vs seized collateral, not realized execution proceeds",
            "B1/B2 historical holdout windows must match verified N1/N2 snapshot metadata and event source QA",
        ],
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
    }
    return {"daily": daily, "hourly": hourly, "timeline": timeline,
            "aligned_events": aligned_events, "collateral": col_break,
            "debt": debt_break, "quality": quality}



def _utc_timestamp(value: object, label: str) -> pd.Timestamp:
    """Require an explicit timezone for snapshot metadata; never guess local time."""
    t = pd.Timestamp(value)
    if pd.isna(t) or t.tzinfo is None:
        raise ValueError(f"{label}: must be timezone-aware UTC")
    return t.tz_convert("UTC")


def audited_snapshots(metadata: dict) -> dict:
    """Use verified Research Design snapshot metadata, never hardcode timestamps."""
    if int(metadata.get("chain_id", 0)) != 1:
        raise ValueError("Invalid snapshot metadata: chain_id must be Ethereum mainnet 1")
    snaps = metadata.get("snapshots", {})
    approved = {"B1": 20458637, "B2": 23543615}
    result = {}
    for name, block in approved.items():
        row = snaps.get(name)
        if not row:
            raise ValueError(f"Missing verified snapshot {name} metadata")
        if int(row.get("snapshot_block", -1)) != block:
            raise ValueError(f"{name} snapshot block mismatch (expected {block})")
        if row.get("block_hash_verified_on_chain") is not True:
            raise ValueError(f"{name} block hash has not been verified on-chain")
        if not row.get("block_hash"):
            raise ValueError(f"{name} verified block hash is missing")
        result[name] = (block, _utc_timestamp(row["snapshot_timestamp_utc"], name))
    return result


def _event_side_quote_audit(events: pd.DataFrame, prices: pd.DataFrame,
                            registry: pd.DataFrame | None,
                            token_field: str, max_age_min: int) -> pd.DataFrame:
    """Audit exact event-time USD market reference for each event token.

    Currency price matching uses the *effective source time* when provided,
    not a synthetic resampling timestamp. An absent/ambiguous token mapping
    remains an explicit gap (we never borrow another token's price).
    """
    if registry is None or not {"token_address", "symbol"}.issubset(registry.columns):
        lookup = {}
        ambiguous = set()
        registry_available = False
    else:
        r = registry[["token_address", "symbol"]].dropna().copy()
        r["token_address"] = r.token_address.astype(str).str.lower().str.strip()
        r["symbol"] = r.symbol.astype(str).str.upper().str.strip()
        options = r.groupby("token_address")["symbol"].agg(lambda x: set(x))
        ambiguous = set(options[options.map(len).gt(1)].index)
        symbol_to_addresses = r.groupby("symbol")["token_address"].nunique()
        ambiguous_symbols = set(symbol_to_addresses[symbol_to_addresses.gt(1)].index)
        ambiguous |= set(r.loc[r.symbol.isin(ambiguous_symbols), "token_address"])
        lookup = {token: next(iter(symbols)) for token, symbols in options.items()
                  if len(symbols) == 1 and token not in ambiguous}
        registry_available = True

    out = events[["tx_hash", "log_index", "event_timestamp", token_field]].copy()
    out["token_address"] = out[token_field].astype(str).str.lower().str.strip()
    out["token_symbol"] = out.token_address.map(lookup)
    out["market_price_usd"] = float("nan")
    out["market_quote_time_utc"] = pd.Series(pd.NaT, index=out.index, dtype="datetime64[ns, UTC]")
    out["market_quote_age_sec"] = float("nan")
    out["status"] = "no_token_registry" if not registry_available else "token_unmapped"
    out.loc[out.token_address.isin(ambiguous), "status"] = "ambiguous_token_mapping"

    market = prices[(prices.price_type == "market_reference") & (prices.quote_asset == "USD")].copy()
    if "reference_timestamp" in market.columns:
        ref = utc(market["reference_timestamp"])
        market["effective_quote_timestamp"] = ref.where(ref.notna(), market.timestamp)
    else:
        market["effective_quote_timestamp"] = market.timestamp
    market = market.sort_values("effective_quote_timestamp")

    for sym in out.token_symbol.dropna().unique():
        selector = out.token_symbol.eq(sym) & out.status.eq("token_unmapped")
        # A mapped symbol is not ambiguous but can still lack a USD market series.
        if not selector.any():
            selector = out.token_symbol.eq(sym) & ~out.token_address.isin(ambiguous)
        mp = market.loc[market.asset.eq(sym), ["effective_quote_timestamp", "price_value"]]
        idx = out.index[selector]
        if mp.empty:
            out.loc[idx, "status"] = "no_usd_market_series"
            continue
        lef = out.loc[idx, ["event_timestamp"]].copy().sort_values("event_timestamp")
        rhs = mp.drop_duplicates("effective_quote_timestamp", keep="last").sort_values("effective_quote_timestamp")
        merged = pd.merge_asof(lef, rhs, left_on="event_timestamp", right_on="effective_quote_timestamp", direction="backward")
        merged.index = lef.index
        ages = (merged.event_timestamp - merged.effective_quote_timestamp).dt.total_seconds()
        ok = merged.price_value.notna() & ages.ge(0) & ages.le(max_age_min * 60)
        out.loc[merged.index, "market_quote_age_sec"] = ages
        out.loc[merged.index, "market_quote_time_utc"] = merged.effective_quote_timestamp
        out.loc[merged.index, "market_price_usd"] = merged.price_value.where(ok)
        out.loc[merged.index, "status"] = "stale_market_quote"
        out.loc[merged.index[merged.effective_quote_timestamp.isna()], "status"] = "no_prior_market_quote"
        out.loc[merged.index[ok], "status"] = "matched"
    return out[["token_address", "token_symbol", "status", "market_price_usd",
                "market_quote_time_utc", "market_quote_age_sec"]].rename(
                    columns={"token_address": f"{token_field}_audit_address",
                             "token_symbol": f"{token_field}_symbol",
                             "status": f"{token_field}_price_status",
                             "market_price_usd": f"{token_field}_market_usd",
                             "market_quote_time_utc": f"{token_field}_quote_utc",
                             "market_quote_age_sec": f"{token_field}_quote_age_sec"})


def audit_holdout_price_coverage(raw_events: pd.DataFrame, raw_prices: pd.DataFrame,
                                 registry: pd.DataFrame | None, metadata: dict,
                                 max_age_min: int = 90, target_pct: float = 85.) -> tuple[pd.DataFrame, pd.DataFrame, dict]:
    """Independent 1h/24h market USD price coverage; never excludes unpriced logs.

    Two-sided coverage is the proportion of eligible liquidation logs for
    which BOTH debt and collateral tokens have prior nonstale USD market quotes.
    This definition may differ from any prior 49.72% report; no retroactive
    reinterpretation of that figure is allowed.
    """
    needed = {"tx_hash", "log_index", "event_timestamp", "event_block",
              "debt_token_address", "collateral_token_address"}
    if needed - set(raw_events.columns):
        raise ValueError(f"Holdout audit events missing columns: {sorted(needed - set(raw_events.columns))}")
    if not 0 <= target_pct <= 100 or max_age_min <= 0:
        raise ValueError("Invalid quote age or coverage target")
    e = raw_events.copy()
    e["tx_hash"] = e.tx_hash.astype(str).str.lower().str.strip()
    e["log_index"] = pd.to_numeric(e.log_index, errors="raise").astype("int64")
    e["event_block"] = pd.to_numeric(e.event_block, errors="raise").astype("int64")
    e["event_timestamp"] = utc(e.event_timestamp)
    if e.event_timestamp.isna().any() or e.duplicated(["tx_hash", "log_index"]).any():
        raise ValueError("Holdout audit requires valid UTC times and unique event keys")
    p = load_prices(raw_prices)
    specs = audited_snapshots(metadata)
    slices = []
    for tag, (block, ts) in specs.items():
        mask = e.event_block.gt(block) & e.event_timestamp.ge(ts) & e.event_timestamp.lt(ts + pd.Timedelta(hours=24))
        sub = e.loc[mask].copy()
        sub["snapshot_id"] = tag
        sub["snapshot_block"] = block
        sub["snapshot_utc"] = ts
        sub["window_first_hour"] = sub.event_timestamp.lt(ts + pd.Timedelta(hours=1))
        slices.append(sub)
    audit = pd.concat(slices, ignore_index=True)
    if not audit.empty:
        for fld in ("debt_token_address", "collateral_token_address"):
            side = _event_side_quote_audit(audit, p, registry, fld, max_age_min)
            audit = pd.concat([audit.reset_index(drop=True), side.reset_index(drop=True)], axis=1)
        # Separately audit ETH/USD and stETH/ETH. These MUST NOT be
        # conflated with the two-token USD coverage statistic.
        def _reference_series(price_type, asset, quote):
            subset = p.loc[
                p.price_type.eq(price_type) & p.asset.eq(asset) & p.quote_asset.eq(quote)
            ].copy()
            if "reference_timestamp" in subset.columns:
                observed = utc(subset.reference_timestamp)
                subset["timestamp"] = observed.where(observed.notna(), subset.timestamp)
            return series(subset, price_type, asset, quote)

        event_times = audit[["event_timestamp"]].copy()
        event_times["_audit_row_id"] = range(len(audit))
        aligned_eth = backward_align(
            event_times,
            _reference_series("market_reference", "ETH", "USD"),
            "event_timestamp", "eth_audit", max_age_min
        )
        aligned_ratio = backward_align(
            event_times,
            _reference_series("market_ratio", "STETH", "ETH"),
            "event_timestamp", "steth_ratio_audit", max_age_min
        )
        aligned_eth = aligned_eth.sort_values("_audit_row_id")
        aligned_ratio = aligned_ratio.sort_values("_audit_row_id")
        audit["eth_usd_market_reference_matched"] = aligned_eth.eth_audit_price.notna().to_numpy()
        audit["steth_eth_market_ratio_matched"] = aligned_ratio.steth_ratio_audit_price.notna().to_numpy()
        audit["eth_usd_quote_age_sec"] = aligned_eth.eth_audit_quote_age_sec.to_numpy()
        audit["steth_eth_ratio_age_sec"] = aligned_ratio.steth_ratio_audit_quote_age_sec.to_numpy()
        audit["both_token_usd_quotes_matched"] = (
            audit.debt_token_address_price_status.eq("matched") &
            audit.collateral_token_address_price_status.eq("matched")
        )
        audit["either_token_usd_quote_matched"] = (
            audit.debt_token_address_price_status.eq("matched") |
            audit.collateral_token_address_price_status.eq("matched")
        )
        if "n3_validation_status" in audit.columns:
            audit["fact_ready_usd"] = audit.n3_validation_status.eq("ok")
        else:
            audit["fact_ready_usd"] = pd.NA
    else:
        audit["both_token_usd_quotes_matched"] = pd.Series(dtype=bool)
        audit["either_token_usd_quote_matched"] = pd.Series(dtype=bool)
        audit["fact_ready_usd"] = pd.Series(dtype=bool)
        audit["eth_usd_market_reference_matched"] = pd.Series(dtype=bool)
        audit["steth_eth_market_ratio_matched"] = pd.Series(dtype=bool)
    gaps = []
    summary = {
        "method": "Event-level, no lookahead, address-to-symbol mapping, USD market_reference for BOTH event tokens",
        "price_age_limit_minutes": max_age_min,
        "coverage_target_pct": target_pct,
        "denominator": "ALL observed unique liquidation events after verified snapshot block within [snapshot_utc,snapshot_utc+window)",
        "time_windows": {"first_hour": "[t0,t0+1h)", "full_day": "[t0,t0+24h)"},
        "warning": "Do not equate this two-token event-time market coverage to preexisting 49.72% without verifying identical numerator, denominator and time window",
        "oracle_comparison": "NOT_ASSESSED: snapshot oracle prices cannot substitute for event-time oracle history",
        "event_collection_completeness": "NOT_VERIFIED_INDEPENDENTLY: check Etherscan window QA and chain event logs",
        "snapshots": {},
    }
    for tag, (block, ts) in specs.items():
        one = audit[audit.snapshot_id.eq(tag)]
        snap_report = {"block": block, "snapshot_utc": ts.isoformat(), "first_hour": {}, "full_day": {}}
        for win, sub in (("first_hour", one[one.window_first_hour]), ("full_day", one)):
            count = int(len(sub))
            n_two = int(sub.both_token_usd_quotes_matched.sum())
            n_either = int(sub.either_token_usd_quote_matched.sum())
            pct = round(100 * n_two / count, 4) if count else None
            snap_report[win] = {
                "events_total": count,
                "events_with_both_usd_market_quotes": n_two,
                "events_with_at_least_one_usd_market_quote": n_either,
                "eth_usd_market_reference_matched": int(sub.eth_usd_market_reference_matched.sum()),
                "steth_eth_market_ratio_matched": int(sub.steth_eth_market_ratio_matched.sum()),
                "eth_usd_market_reference_coverage_pct": round(100 * int(sub.eth_usd_market_reference_matched.sum()) / count, 4) if count else None,
                "steth_eth_market_ratio_coverage_pct": round(100 * int(sub.steth_eth_market_ratio_matched.sum()) / count, 4) if count else None,
                "two_token_price_coverage_pct": pct,
                "target_pct": target_pct,
                "status": "NO_EVENTS" if count == 0 else ("PASS" if pct >= target_pct else "BELOW_TARGET"),
                "debt_token_reason_counts": {str(k): int(v) for k,v in sub.get("debt_token_address_price_status", pd.Series(dtype=str)).value_counts().items()},
                "collateral_token_reason_counts": {str(k): int(v) for k,v in sub.get("collateral_token_address_price_status", pd.Series(dtype=str)).value_counts().items()},
            }
        summary["snapshots"][tag] = snap_report
        print(f"[MARKET FIRST HOUR {tag}] market_usd_both={snap_report['first_hour']['events_with_both_usd_market_quotes']}/{snap_report['first_hour']['events_total']} coverage={snap_report['first_hour']['two_token_price_coverage_pct']}% target={target_pct}% status={snap_report['first_hour']['status']}")
        if not one.empty:
            missing = one.loc[one.window_first_hour & ~one.both_token_usd_quotes_matched]
            for fld in ("debt_token_address", "collateral_token_address"):
                bad = missing[missing[f"{fld}_price_status"].ne("matched")].copy()
                if bad.empty:
                    continue
                gaps.append(pd.DataFrame({
                    "snapshot_id": tag,
                    "snapshot_block": block,
                    "tx_hash": bad.tx_hash,
                    "log_index": bad.log_index,
                    "event_timestamp": bad.event_timestamp,
                    "side": "debt" if fld.startswith("debt") else "collateral",
                    "token_address": bad[fld],
                    "token_symbol": bad[f"{fld}_symbol"],
                    "missing_reason": bad[f"{fld}_price_status"],
                    "quote_age_seconds": bad[f"{fld}_quote_age_sec"],
                }))
    gap_columns = ["snapshot_id", "snapshot_block", "tx_hash", "log_index", "event_timestamp", "side",
                   "token_address", "token_symbol", "missing_reason", "quote_age_seconds"]
    gap_df = pd.concat(gaps, ignore_index=True) if gaps else pd.DataFrame(columns=gap_columns)
    return audit, gap_df, summary

def export_m5_deliverables(result: dict, overwrite: bool) -> None:
    """Create the three required M5 delivery files."""
    daily = result["daily"].sort_values("date_utc").copy()
    hourly = result["hourly"].copy()
    q = result["quality"]

    if daily.empty or daily["date_utc"].duplicated().any():
        raise ValueError("M5 QA: empty or duplicated daily data")

    for label, df in [("daily", daily), ("hourly", hourly)]:
        if int(df["liquidation_events"].sum()) != int(q["event_rows"]):
            raise ValueError(f"M5 QA: event count mismatch in {label}")

        for col, key in [
            ("debt_repaid_usd", "debt_repaid_usd_total"),
            ("collateral_seized_usd", "collateral_seized_usd_total"),
        ]:
            if not math.isclose(
                float(df[col].sum()),
                float(q[key]),
                rel_tol=1e-9,
                abs_tol=0.01,
            ):
                raise ValueError(f"M5 QA: {col} mismatch in {label}")

    peak = daily.loc[daily["debt_repaid_usd"].idxmax()]
    peak_date = pd.Timestamp(peak["date_utc"]).strftime("%Y-%m-%d")
    debt_total = float(q["debt_repaid_usd_total"])
    peak_debt = float(peak["debt_repaid_usd"])

    summary = dict(q)
    summary.update({
        "schema_version": "m5_descriptive_v1",
        "daily_observations": int(len(daily)),
        "peak_liquidation_date_utc": peak_date,
        "peak_daily_debt_repaid_usd": peak_debt,
        "peak_day_share_pct": (
            100 * peak_debt / debt_total if debt_total > 0 else None
        ),
    })

    rows = [
        ("liquidation_events", q["event_rows"], "events"),
        ("unique_transactions", q["unique_transactions"], "transactions"),
        ("unique_borrowers", q["unique_borrowers"], "wallets"),
        ("debt_repaid_usd_total", debt_total, "USD"),
        ("collateral_seized_usd_total",
         q["collateral_seized_usd_total"], "USD"),
        ("daily_observations", len(daily), "days"),
        ("hourly_observations", len(hourly), "hours"),
        ("zero_event_hours", q["zero_event_hours"], "hours"),
        ("average_daily_debt_usd",
         float(daily["debt_repaid_usd"].mean()), "USD"),
        ("median_daily_debt_usd",
         float(daily["debt_repaid_usd"].median()), "USD"),
        ("peak_liquidation_date_utc", peak_date, "UTC date"),
        ("peak_daily_debt_usd", peak_debt, "USD"),
        ("peak_day_share_pct", summary["peak_day_share_pct"], "percent"),
    ]

    for name, values in q["correlations"].items():
        for field in ("n", "pearson", "spearman"):
            rows.append(
                (f"{name}_{field}", values.get(field), field)
            )

    statistics = pd.DataFrame(
        rows, columns=["metric", "value", "unit"]
    )

    marts = ROOT / "data" / "marts"

    safe_write(
        marts / "historical_daily.parquet",
        lambda path: daily.to_parquet(path, index=False),
        overwrite,
    )
    safe_write(
        marts / "descriptive_summary.json",
        lambda path: path.write_text(
            json.dumps(
                summary, ensure_ascii=False,
                indent=2, allow_nan=False
            ),
            encoding="utf-8",
        ),
        overwrite,
    )
    safe_write(
        OUT_TABLES / "descriptive_statistics.csv",
        lambda path: statistics.to_csv(path, index=False),
        overwrite,
    )

    print("[M5] CREATED historical_daily.parquet")
    print("[M5] CREATED descriptive_summary.json")
    print("[M5] CREATED descriptive_statistics.csv")
    print("[M5 QA] Daily and hourly totals MATCH")


def plot_results(data: dict, overwrite: bool) -> None:
    daily, timeline = data["daily"], data["timeline"]
    plt.rcParams.update({"font.size": 10})

    fig, ax = plt.subplots(figsize=(11, 4.6))
    ax.bar(daily.date_utc.dt.strftime("%Y-%m-%d"), daily.debt_repaid_usd)
    ax.set_yscale("symlog", linthresh=1000)
    ax.set_ylabel("Debt repaid (USD, symlog scale)")
    ax.set(title="Aave V3 Ethereum — Debt repaid by day (USD)", ylabel="USD (historical market reference)", xlabel="UTC day")
    ax.tick_params(axis="x", rotation=30)
    fig.tight_layout()
    safe_write(OUT_FIGS / "n3_debt_repaid_daily.png", lambda path: fig.savefig(path, format="png", dpi=160), overwrite)
    plt.close(fig)

    fig, ax = plt.subplots(figsize=(11, 4.6))
    ax.bar(timeline.hour_utc, timeline.liquidation_events, width=0.03, label="Liquidation logs")
    ax.set(ylabel="Events/hour", xlabel="UTC", title="Liquidation events and ETH market reference")
    ay = ax.twinx()
    ay.plot(timeline.hour_utc, timeline.eth_usd_price, label="ETH/USD", linewidth=1.4)
    ay.set(ylabel="ETH/USD reference")
    fig.tight_layout()
    safe_write(OUT_FIGS / "n3_liquidation_eth_timeline.png", lambda path: fig.savefig(path, format="png", dpi=160), overwrite)
    plt.close(fig)

    if timeline.steth_eth_price.notna().any():
        fig, ax = plt.subplots(figsize=(11, 4.6))
        ax.plot(timeline.hour_utc, timeline.steth_eth_price, label="stETH/ETH ratio")
        ax.axhline(1.0, linewidth=1, linestyle="--", label="1 stETH = 1 ETH reference")
        ax.set(title="stETH/ETH market ratio (not contract conversion)", ylabel="Market price ratio", xlabel="UTC")
        ax.legend()
        fig.tight_layout()
        safe_write(OUT_FIGS / "n3_steth_eth_depeg_timeline.png", lambda path: fig.savefig(path, format="png", dpi=160), overwrite)
        plt.close(fig)

    breakdown = (
    data["collateral"]
    .sort_values("collateral_seized_usd", ascending=False)
    .head(8)
    .iloc[::-1]
)
    fig, ax = plt.subplots(figsize=(10, 4.5))
    names = breakdown["token_symbol"].fillna(breakdown["collateral_token_address"].str.slice(0, 10))
    ax.barh(names.astype(str), breakdown.collateral_seized_usd)
    ax.set(title="Top seized collateral assets (USD market reference)", xlabel="Collateral seized USD")
    fig.tight_layout()
    safe_write(OUT_FIGS / "n3_collateral_by_token.png", lambda path: fig.savefig(path, format="png", dpi=160), overwrite)
    plt.close(fig)


def main() -> None:
    cli = argparse.ArgumentParser(description="N3 Muc 5 — liquidation & depeg descriptive statistics")
    cli.add_argument("--events", type=Path, default=DEFAULT_EVENTS)
    cli.add_argument("--prices", type=Path, default=DEFAULT_PRICES)
    cli.add_argument("--registry", type=Path, default=DEFAULT_REGISTRY)
    cli.add_argument("--start", default=None, help="UTC inclusive YYYY-MM-DD")
    cli.add_argument("--end", default=None, help="UTC exclusive YYYY-MM-DD")
    cli.add_argument("--max-price-lag-min", type=int, default=90)
    cli.add_argument("--overwrite", action="store_true")
    cli.add_argument("--dry-run", action="store_true")
    cli.add_argument("--audit-holdouts", action="store_true", help="Audit B1/B2 first-hour market price coverage")
    cli.add_argument("--audit-only", action="store_true", help="Only run B1/B2 coverage audit; do not overwrite M5 deliverables")
    cli.add_argument("--snapshot-metadata", type=Path, default=ROOT / "data/processed/aave/snapshot_metadata.json")
    cli.add_argument("--coverage-target-pct", type=float, default=85.0)
    cli.add_argument("--audit-output-root", type=Path, default=ROOT, help="Separate root for new QA CSV/JSON files")
    args = cli.parse_args()
    if args.max_price_lag_min <= 0:
        cli.error("--max-price-lag-min must be >0")
    if args.audit_only and not args.audit_holdouts:
        cli.error("--audit-only requires --audit-holdouts")
    if not 0 <= args.coverage_target_pct <= 100:
        cli.error("--coverage-target-pct must be between 0 and 100")
    if not args.events.exists() or not args.prices.exists():
        raise SystemExit("Thieu events/prices. Chay N3 Muc 1-3 truoc; khong dung oracle snapshot lam market quote.")
    registry = pd.read_csv(args.registry) if args.registry.exists() else None
    if args.audit_holdouts:
        if not args.snapshot_metadata.is_file():
            raise SystemExit(f"Missing verified B1/B2 metadata: {args.snapshot_metadata}")
        snapshot_metadata = json.loads(args.snapshot_metadata.read_text(encoding="utf-8"))
        audit, gaps, report = audit_holdout_price_coverage(
            pd.read_parquet(args.events), pd.read_parquet(args.prices), registry,
            snapshot_metadata, max_age_min=args.max_price_lag_min,
            target_pct=args.coverage_target_pct,
        )
        if args.dry_run:
            print("[DRY RUN] Holdout QA not written")
        else:
            table_dir = args.audit_output_root.resolve() / "outputs/tables"
            paths = {
                "n3_holdout_price_coverage_B1_B2_events.csv":
                    (audit, table_dir / "n3_holdout_price_coverage_B1_B2_events.csv"),
                "n3_holdout_price_missing_first_hour.csv":
                    (gaps, table_dir / "n3_holdout_price_missing_first_hour.csv"),
            }
            target = table_dir / "n3_holdout_market_price_coverage_quality.json"
            if not args.overwrite and (target.exists() or any(path.exists() for _, path in paths.values())):
                raise SystemExit("Holdout audit exists; choose --audit-output-root or --overwrite (backup)")
            for label, (frame, path) in paths.items():
                safe_write(path, lambda dest, f=frame: f.to_csv(dest, index=False), args.overwrite)
                print("[SAVED]", path)
            safe_write(target, lambda dest: dest.write_text(
                json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8"), args.overwrite)
            print("[SAVED]", target)
        if args.audit_only:
            print("[AUDIT ONLY] M5 charts and descriptive marts untouched")
            return
    result = build(pd.read_parquet(args.events), pd.read_parquet(args.prices), registry,
                   max_age_min=args.max_price_lag_min, start=args.start, end=args.end)
    q = result["quality"]
    print(f"[MUC 5] events={q['event_rows']}, debt USD={q['debt_repaid_usd_total']:,.2f}, "
          f"ratio matched={q['event_steth_eth_ratio_matched']}/{q['event_rows']}")
    if args.dry_run:
        print("[DRY RUN] KHONG ghi file")
        return
    files = {
        "n3_liquidations_daily.csv": result["daily"],
        "n3_liquidations_hourly.csv": result["hourly"],
        "n3_liquidation_market_hourly.csv": result["timeline"],
        "n3_liquidation_event_market_alignment.csv": result["aligned_events"],
        "n3_collateral_breakdown.csv": result["collateral"],
        "n3_debt_breakdown.csv": result["debt"],
    }
    for filename, frame in files.items():
        safe_write(OUT_TABLES / filename, lambda path, x=frame: x.to_csv(path, index=False), args.overwrite)
    safe_write(OUT_TABLES / "n3_descriptive_quality.json",
               lambda path: path.write_text(json.dumps(q, ensure_ascii=False, indent=2), encoding="utf-8"),
               args.overwrite)
    export_m5_deliverables(result, args.overwrite)
    plot_results(result, args.overwrite)
    print("[SAVED] outputs/tables/n3_* and outputs/figures/n3_*png")
    print("[NOTICE] Correlation is descriptive, NOT evidence of causality.")


if __name__ == "__main__":
    main()
