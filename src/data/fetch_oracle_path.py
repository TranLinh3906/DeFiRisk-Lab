"""
fetch_oracle_path.py - Duong gia ORACLE AAVE theo block trong cua so kiem dinh cua B1, B2 (tu block snapshot den het
window_end_exclusive_utc trong config/snapshots.json). Dung de doi chieu voi gia thi truong cua N3 va de xac dinh gia thap nhat.

Chay tu thu muc goc repo:
    python src/data/fetch_oracle_path.py                 # B1, B2, buoc 10 block (~2 phut)
    python src/data/fetch_oracle_path.py --step 5 --snapshot B1

Dau ra:
  data/processed/aave/oracle_price_path.parquet   (snapshot_id, block, block_timestamp_utc, token_address, symbol, price_usd, oracle_price_raw)
  data/processed/aave/oracle_price_path_summary.json (gia tai snapshot, gia thap nhat trong cua so, block/thoi diem, muc giam)

Luu y: gia oracle Chainlink cap nhat theo nguong lech/heartbeat nen la duong bac thang; lay mau moi --step block co the
bo lo dinh/day ngan hon khoang do (giam --step de min hon). Gia thap nhat o day la thap nhat TREN CAC MAU.
Khong luu RPC URL. CHUA duoc chay thu o phia nguoi viet (chi kiem tra cu phap).
"""
import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
from eth_abi import decode, encode
from web3 import Web3

try:
    from dotenv import load_dotenv
    load_dotenv(Path.cwd() / ".env", override=True)
except ImportError:
    pass

ROOT = Path.cwd()
P = ROOT / "data/processed/aave"
MULTICALL3 = Web3.to_checksum_address("0xcA11bde05977b3631167028862bE2a173976CA11")
SEL_PRICE = Web3.keccak(text="getAssetPrice(address)")[:4]
KEY_SYMBOLS = ["WETH", "wstETH", "weETH", "cbETH", "rETH", "WBTC", "cbBTC", "USDC", "USDT", "DAI", "GHO"]
MC_ABI = [{"name": "aggregate3", "type": "function", "stateMutability": "payable",
           "inputs": [{"name": "calls", "type": "tuple[]", "components": [
               {"name": "target", "type": "address"}, {"name": "allowFailure", "type": "bool"},
               {"name": "callData", "type": "bytes"}]}],
           "outputs": [{"name": "returnData", "type": "tuple[]", "components": [
               {"name": "success", "type": "bool"}, {"name": "returnData", "type": "bytes"}]}]}]


def last_block_before(w3, target_ts, lo, hi):
    """Block cuoi cung co timestamp < target_ts trong [lo, hi]."""
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if int(w3.eth.get_block(mid)["timestamp"]) < target_ts:
            lo = mid
        else:
            hi = mid - 1
    return lo


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--snapshot", default=None, help="B1 hoac B2 (mac dinh: moi snapshot co window_end_exclusive_utc)")
    ap.add_argument("--step", type=int, default=10, help="lay mau moi N block (12 giay/block)")
    args = ap.parse_args()

    url = os.getenv("ALCHEMY_RPC_URL", "").strip() or os.getenv("ETH_RPC_URL", "").strip()
    if not url:
        sys.exit("Thieu ALCHEMY_RPC_URL (hoac ETH_RPC_URL).")
    w3 = Web3(Web3.HTTPProvider(url, request_kwargs={"timeout": 120}))
    mc = w3.eth.contract(address=MULTICALL3, abi=MC_ABI)
    cfg = json.loads((ROOT / "config/snapshots.json").read_text(encoding="utf-8"))["snapshots"]
    st = json.loads((ROOT / "data/raw/aave/state_check.json").read_text(encoding="utf-8"))["snapshots"]
    labels = [args.snapshot] if args.snapshot else [k for k, v in cfg.items() if v.get("window_end_exclusive_utc")]

    rows, summary = [], {}
    latest = w3.eth.block_number
    for lb in labels:
        c = cfg[lb]
        if not c.get("window_end_exclusive_utc"):
            sys.exit(f"{lb} khong co window_end_exclusive_utc trong config.")
        b0 = int(c["block"])
        end_ts = int(datetime.fromisoformat(c["window_end_exclusive_utc"].replace("Z", "+00:00")).timestamp())
        b1 = last_block_before(w3, end_ts, b0, latest)
        oracle = Web3.to_checksum_address(st[lb]["aave_oracle"])
        toks = [r for r in st[lb]["reserves"] if r["symbol"] in KEY_SYMBOLS]
        blocks = list(range(b0, b1 + 1, args.step))
        if blocks[-1] != b1:
            blocks.append(b1)
        print(f"[{lb}] cua so block {b0} -> {b1} ({b1 - b0} block, {len(blocks)} mau, {len(toks)} token)")
        for n in blocks:
            calls = [(oracle, True, SEL_PRICE + encode(["address"], [Web3.to_checksum_address(r["token_address"])]))
                     for r in toks]
            res = mc.functions.aggregate3(calls).call(block_identifier=n)
            ts = datetime.fromtimestamp(int(w3.eth.get_block(n)["timestamp"]), tz=timezone.utc)
            for r, (ok, d) in zip(toks, res):
                if not ok or len(d) < 32:
                    continue
                raw = int(decode(["uint256"], d)[0])
                rows.append({"snapshot_id": lb, "block": n, "block_timestamp_utc": ts, "token_address": r["token_address"],
                             "symbol": r["symbol"], "price_usd": raw / 1e8, "oracle_price_raw": str(raw)})
        df = pd.DataFrame([x for x in rows if x["snapshot_id"] == lb])
        summary[lb] = {"block_start": b0, "block_end": b1, "step_blocks": args.step, "samples": len(blocks), "tokens": {}}
        for sym, g in df.groupby("symbol"):
            g = g.sort_values("block")
            p0, i = g.iloc[0].price_usd, g.price_usd.idxmin()
            lo = g.loc[i]
            summary[lb]["tokens"][sym] = {
                "price_at_snapshot": p0, "min_price_in_window": lo.price_usd, "min_block": int(lo.block),
                "min_time_utc": lo.block_timestamp_utc.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "drawdown_pct_from_snapshot": round((lo.price_usd / p0 - 1) * 100, 3)}
        print(f"   {'token':8s} {'gia snapshot':>14s} {'gia thap nhat':>14s} {'muc giam %':>11s}  thoi diem thap nhat (UTC)")
        for sym in KEY_SYMBOLS:
            s = summary[lb]["tokens"].get(sym)
            if s:
                print(f"   {sym:8s} {s['price_at_snapshot']:14,.4f} {s['min_price_in_window']:14,.4f} "
                      f"{s['drawdown_pct_from_snapshot']:11.3f}  {s['min_time_utc']}")

    P.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_parquet(P / "oracle_price_path.parquet", index=False)
    (P / "oracle_price_path_summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False, default=str) + "\n",
                                                      encoding="utf-8")
    print(f"Da luu {P / 'oracle_price_path.parquet'} va oracle_price_path_summary.json")


if __name__ == "__main__":
    main()