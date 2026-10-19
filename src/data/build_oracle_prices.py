"""
build_oracle_prices.py - Xuat oracle_prices.parquet + lido_ratios.parquet tu state_check.json,
goi them wstETH.stEthPerToken() tai dung block snapshot.

Chay tu thu muc goc repo (sau khi da chay check_state.py):
    pip install pandas pyarrow web3 python-dotenv
    python src/data/build_oracle_prices.py

CHUA duoc chay thu o phia nguoi viet: doc ket qua that. Khong luu RPC URL.
snapshot_id dung "A"/"B": doi cho khop docs/data_contract.md neu nhom dung ten khac.
"""
import json
import os
import sys
from pathlib import Path

import pandas as pd
from web3 import Web3

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

ROOT = Path.cwd()
STATE = ROOT / "data/raw/aave/state_check.json"
OUT_DIR = ROOT / "data/processed/aave"

WSTETH_ABI = [{
    "name": "stEthPerToken", "type": "function", "stateMutability": "view",
    "inputs": [], "outputs": [{"name": "o0", "type": "uint256"}],
}]
MAX_STALE_SECONDS = 24 * 3600  # chi de canh bao, khong loai du lieu
WATCH = ["WETH", "WBTC", "USDC", "USDT", "DAI", "wstETH", "weETH", "cbETH"]


def main():
    if not STATE.exists():
        sys.exit(f"Chua co {STATE}; chay check_state.py truoc.")
    report = json.loads(STATE.read_text(encoding="utf-8"))

    url = os.getenv("ALCHEMY_RPC_URL", "").strip()
    if not url:
        sys.exit("Thieu ALCHEMY_RPC_URL.")
    w3 = Web3(Web3.HTTPProvider(url, request_kwargs={"timeout": 60}))

    price_rows, lido_rows = [], []
    for label, snap in report["snapshots"].items():
        blk = snap["block"]
        blk_ts = snap["block_timestamp_utc"]
        ts_iso = pd.to_datetime(blk_ts, unit="s", utc=True)
        for r in snap["reserves"]:
            upd = r.get("feed_updated_at")
            price_rows.append({
                "snapshot_id": label,
                "snapshot_block": blk,
                "snapshot_timestamp": ts_iso,
                "token_address": r["token_address"],
                "token_symbol": r["symbol"],
                "decimals": r["decimals"],
                "oracle_price_raw": str(r["oracle_price_raw"]),  # int lon -> luu string, tranh mat chinh xac
                "price_usd": r["oracle_price_usd"],
                "base_currency_unit": snap["base_currency_unit"],
                "aave_oracle": snap["aave_oracle"],
                "price_source": r.get("price_source"),
                "feed_description": r.get("description"),
                "aggregator_impl": r.get("aggregator_impl"),
                "feed_updated_at_utc": pd.to_datetime(upd, unit="s", utc=True) if upd else pd.NaT,
                "feed_staleness_sec": (blk_ts - upd) if upd else None,
            })

        # Ty le Lido tai dung block
        wst = next((r for r in snap["reserves"] if r["symbol"] == "wstETH"), None)
        if wst is None:
            print(f"[{label}] CANH BAO: khong thay wstETH trong reserve list")
            continue
        addr = Web3.to_checksum_address(wst["token_address"])
        c = w3.eth.contract(address=addr, abi=WSTETH_ABI)
        ratio = c.functions.stEthPerToken().call(block_identifier=blk) / 1e18
        lido_rows.append({
            "snapshot_id": label, "snapshot_block": blk, "snapshot_timestamp": ts_iso,
            "wsteth_address": addr.lower(), "steth_per_wsteth": ratio,
            "source": "wstETH.stEthPerToken() via eth_call",
        })

    prices = pd.DataFrame(price_rows)
    lido = pd.DataFrame(lido_rows)

    # Kiem tra co ban
    dup = prices.duplicated(["snapshot_block", "token_address"]).sum()
    print(f"Hang gia: {len(prices)} | trung khoa (snapshot_block, token_address): {dup}")
    stale = prices[prices["feed_staleness_sec"].fillna(0) > MAX_STALE_SECONDS]
    print(f"Feed cu hon {MAX_STALE_SECONDS//3600}h: {len(stale)} token "
          f"(stablecoin/feed it cap nhat co the binh thuong; xem tung dong)")
    if len(stale):
        print(stale[["snapshot_id", "token_symbol", "feed_staleness_sec"]].to_string(index=False))

    print("\nGia oracle cac token chinh (USD), de doi chieu voi gia thi truong N3:")
    w = prices[prices.token_symbol.isin(WATCH)].pivot_table(
        index="token_symbol", columns="snapshot_id", values="price_usd")
    print(w.to_string())

    print("\nTy le Lido (stETH tren 1 wstETH):")
    print(lido[["snapshot_id", "snapshot_block", "steth_per_wsteth"]].to_string(index=False))

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    prices.to_parquet(OUT_DIR / "oracle_prices.parquet", index=False)
    lido.to_parquet(OUT_DIR / "lido_ratios.parquet", index=False)
    print(f"\nDa luu {OUT_DIR / 'oracle_prices.parquet'} va lido_ratios.parquet")


if __name__ == "__main__":
    main()