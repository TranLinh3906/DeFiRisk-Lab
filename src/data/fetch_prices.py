"""N3 | Market prices for DeFi Liquidation Stress Test.

Source: CoinGecko Demo historical market-chart/range.
Outputs are MARKET reference prices, NOT Aave oracle prices.

Examples (from repo root):
    python src/data/fetch_prices.py --start 2026-09-15 --end 2026-10-16
    python src/data/fetch_prices.py --start 2026-09-15 --end 2026-10-16 --overwrite

Date --end is EXCLUSIVE, in UTC.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import requests
from dotenv import load_dotenv

API_URL = "https://api.coingecko.com/api/v3"
ASSETS = {
    "ETH": "ethereum",
    "STETH": "staked-ether",
    "WSTETH": "wrapped-steth",
    "BTC": "bitcoin",
    "USDC": "usd-coin",
    "USDT": "tether",
}
PROJECT_ROOT = Path(__file__).resolve().parents[2]


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Download CoinGecko historical market prices (UTC)")
    parser.add_argument("--start", default="2026-09-15", help="UTC start date, inclusive: YYYY-MM-DD")
    parser.add_argument("--end", default="2026-10-16", help="UTC end date, EXCLUSIVE: YYYY-MM-DD")
    parser.add_argument("--assets", default="ETH,STETH,WSTETH,BTC,USDC,USDT",
                        help="Comma-separated asset symbols")
    parser.add_argument("--overwrite", action="store_true", help="Back up then replace outputs")
    parser.add_argument("--wait", type=float, default=3.0, help="Seconds between API calls")
    return parser.parse_args()


def atomic_save(df: pd.DataFrame, target: Path, output_type: str, overwrite: bool) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists() and not overwrite:
        raise FileExistsError(f"{target} already exists. Re-run with --overwrite to back up and replace.")
    if target.exists():
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        shutil.copy2(target, target.with_name(target.name + f".bak_{stamp}"))
    fd, name = tempfile.mkstemp(dir=target.parent, suffix=".tmp")
    os.close(fd)
    temp_path = Path(name)
    try:
        if output_type == "csv":
            df.to_csv(temp_path, index=False)
        elif output_type == "parquet":
            df.to_parquet(temp_path, index=False, engine="pyarrow")
        else:
            raise ValueError(output_type)
        os.replace(temp_path, target)
    finally:
        temp_path.unlink(missing_ok=True)


def atomic_json(data: dict, path: Path, overwrite: bool) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and not overwrite:
        raise FileExistsError(f"{path} already exists. Re-run with --overwrite.")
    if path.exists():
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        shutil.copy2(path, path.with_name(path.name + f".bak_{stamp}"))
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(temp, path)


def api_prices(symbol: str, key: str, start_unix: int, end_unix: int) -> pd.DataFrame:
    url = f"{API_URL}/coins/{ASSETS[symbol]}/market_chart/range"
    headers = {"x-cg-demo-api-key": key, "Accept": "application/json"}
    params = {"vs_currency": "usd", "from": start_unix, "to": end_unix}
    error = None
    for attempt in range(4):
        try:
            response = requests.get(url, headers=headers, params=params, timeout=50)
            if response.status_code in (429, 500, 502, 503, 504):
                wait = min(60, 8 * (attempt + 1))
                print(f"[RETRY] {symbol} status={response.status_code}, wait {wait}s")
                time.sleep(wait)
                continue
            if response.status_code >= 400:
                raise RuntimeError(f"CoinGecko HTTP {response.status_code} for {symbol}: {response.text[:400]}")
            payload = response.json()
            pairs = payload.get("prices")
            if not isinstance(pairs, list) or not pairs:
                raise RuntimeError(f"Empty/invalid 'prices' in {symbol} response")
            frame = pd.DataFrame(pairs, columns=["timestamp_ms", "price_usd"])
            frame["timestamp"] = pd.to_datetime(frame.pop("timestamp_ms"), unit="ms", utc=True)
            frame["price_usd"] = pd.to_numeric(frame["price_usd"], errors="coerce")
            frame["asset"] = symbol
            frame["coingecko_id"] = ASSETS[symbol]
            frame["price_type"] = "market_reference"
            frame["source"] = "CoinGecko Demo"
            return frame[["timestamp", "asset", "coingecko_id", "price_usd", "price_type", "source"]]
        except requests.RequestException as exc:
            error = exc
            time.sleep(min(30, 5 * (attempt + 1)))
    raise RuntimeError(f"Failed CoinGecko request for {symbol}; last error={error}")


def prepare_outputs(df: pd.DataFrame, start: pd.Timestamp, end: pd.Timestamp) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    df = df.copy()
    df = df[(df["timestamp"] >= start) & (df["timestamp"] < end)].copy()
    df = df.drop_duplicates(["asset", "timestamp"]).sort_values(["asset", "timestamp"])
    if df.empty or df["price_usd"].isna().any() or (df["price_usd"] <= 0).any():
        raise ValueError("Missing or non-positive market prices. Stop and inspect raw data.")
    # Store actual quote timestamps. Do not fabricate OHLC or an Aave oracle series.
    df["date"] = df["timestamp"].dt.floor("D")
    daily = df.groupby(["asset", "date"], sort=False, as_index=False).tail(1).copy()
    daily = daily[["date", "timestamp", "asset", "price_usd", "price_type", "source"]]
    daily = daily.sort_values(["date", "asset"]).reset_index(drop=True)
    # Ratios: price observations are within the same UTC HOUR (no interpolation or forward filling).
    hourly = df.copy()
    hourly["hour"] = hourly["timestamp"].dt.floor("h")
    hourly = hourly.groupby(["asset", "hour"], sort=False, as_index=False).tail(1)
    wide = hourly.pivot(index="hour", columns="asset", values="price_usd")
    ratios = pd.DataFrame(index=wide.index)
    for col, numerator, denominator in (
        ("steth_eth_market_ratio", "STETH", "ETH"),
        ("wsteth_eth_market_ratio", "WSTETH", "ETH"),
        ("wsteth_steth_market_ratio", "WSTETH", "STETH"),
    ):
        if numerator in wide.columns and denominator in wide.columns:
            ratios[col] = wide[numerator] / wide[denominator]
        else:
            ratios[col] = float("nan")
    ratios = ratios.reset_index().rename(columns={"hour": "timestamp_hour_utc"})
    ratios["source"] = "CoinGecko indicative MARKET ratio (hour-aligned USD prices)"
    ratios = ratios.dropna(subset=["steth_eth_market_ratio", "wsteth_eth_market_ratio", "wsteth_steth_market_ratio"], how="all")
    return df, daily, ratios


def main() -> None:
    args = parse_arguments()
    load_dotenv(PROJECT_ROOT / ".env")
    key = os.getenv("COINGECKO_API_KEY", "").strip()
    if not key:
        raise SystemExit("Missing COINGECKO_API_KEY in repo-root .env")
    start = pd.Timestamp(args.start, tz="UTC")
    end = pd.Timestamp(args.end, tz="UTC")
    if end <= start:
        raise SystemExit("--end must be later than --start (end exclusive)")
    # API Demo normally only offers data within the last 365 days.
    if start < pd.Timestamp.now(tz="UTC") - pd.Timedelta(days=365):
        print("[WARNING] Requested data may be older than CoinGecko Demo 365-day limit.")
    requested = [s.strip().upper() for s in args.assets.split(",") if s.strip()]
    if not requested or len(set(requested)) != len(requested):
        raise SystemExit("--assets must contain unique symbols")
    unknown = [s for s in requested if s not in ASSETS]
    if unknown:
        raise SystemExit(f"Unsupported symbols: {unknown}; allowed={list(ASSETS)}")
    raw_path = PROJECT_ROOT / "data/raw/market/prices_raw.csv"
    staging_path = PROJECT_ROOT / "data/staging/n3_price_timeseries.parquet"
    daily_path = PROJECT_ROOT / "data/processed/market/price_history.parquet"
    ratios_path = PROJECT_ROOT / "data/staging/n3_market_ratios.parquet"
    quality_path = PROJECT_ROOT / "outputs/tables/n3_price_quality.json"
    targets = [raw_path, staging_path, daily_path, ratios_path, quality_path]
    if not args.overwrite and any(path.exists() for path in targets):
        raise SystemExit("Output file already exists. Use --overwrite (creates backups) or move files aside first.")
    frames = []
    for i, symbol in enumerate(requested):
        print(f"[GET] {symbol} / {ASSETS[symbol]} ...")
        frames.append(api_prices(symbol, key, int(start.timestamp()), int(end.timestamp())))
        if i < len(requested) - 1:
            time.sleep(max(0, args.wait))
    all_quotes, daily, ratios = prepare_outputs(pd.concat(frames, ignore_index=True), start, end)
    if any(daily[daily["asset"] == s].empty for s in requested):
        raise RuntimeError("One or more requested assets returned no observations in date range")
    quality = {
        "source": "CoinGecko Demo market chart / range",
        "price_type": "market_reference_NOT_Aave_oracle",
        "window_utc": {"start_inclusive": str(start), "end_exclusive": str(end)},
        "requested_assets": requested,
        "raw_quote_counts": {k: int(v) for k, v in all_quotes.groupby("asset").size().items()},
        "daily_counts": {k: int(v) for k, v in daily.groupby("asset").size().items()},
        "ratio_rows_nonempty": len(ratios),
        "ratio_rule": "prices within same UTC hour; otherwise NA; not oracle and not Lido contract conversion rate",
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
    }
    atomic_save(all_quotes, raw_path, "csv", args.overwrite)
    atomic_save(all_quotes, staging_path, "parquet", args.overwrite)
    atomic_save(daily, daily_path, "parquet", args.overwrite)
    atomic_save(ratios, ratios_path, "parquet", args.overwrite)
    atomic_json(quality, quality_path, args.overwrite)
    print(f"[OK] raw={len(all_quotes)} rows; daily={len(daily)} rows; ratio={len(ratios)} hours")
    for path in targets:
        print("  ", path.relative_to(PROJECT_ROOT))
    print("[NOTICE] CoinGecko market prices are NOT Aave oracle price history.")


if __name__ == "__main__":
    main()
