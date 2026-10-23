"""
fetch_borrowers.py - Lap tap vi ung vien (tung co Borrow event) cua Aave V3 Ethereum den Snapshot B.

Chay tu thu muc goc repo:
    python src/data/fetch_borrowers.py
    (chay lai cung lenh de tiep tuc neu bi ngat; them --reset de lam lai tu dau)

Can ETHERSCAN_API_KEY trong .env (cung key voi N3). Khong can RPC.
Dung lai lop Etherscan cua N3 (src/data/fetch_liquidations.py).

Dau ra:
  data/raw/aave/borrow_events_raw.jsonl            (moi dong = 1 Borrow event)
  data/raw/aave/borrow_scan_progress.json          (checkpoint)
  data/processed/aave/borrower_candidates.parquet  (moi dong = 1 vi)

QUAN TRONG: day la TAP UNG VIEN (tung vay), khong phai tap vi dang con no.
Vi co no thuc su tai tung snapshot duoc loc o buoc sau bang getUserAccountData.
Gioi han: khong bat duoc no mo bang flash loan co interestRateMode != 0.
CHUA duoc chay thu o phia nguoi viet.
"""
import argparse
import json
import os
import sys
from pathlib import Path

import pandas as pd
from web3 import Web3

sys.path.insert(0, str(Path(__file__).resolve().parent))
try:
    from dotenv import load_dotenv
    # override=True: gia tri trong .env uu tien hon bien moi truong cu con sot lai
    load_dotenv(Path.cwd() / ".env", override=True)
except ImportError:
    pass
from fetch_liquidations import Etherscan  # noqa: E402  (lop client cua N3)

ROOT = Path.cwd()
POOL = "0x87870bca3f3fd6335c3f4ce8392d69350b4fa4e2"
SIG = "Borrow(address,address,address,uint256,uint8,uint256,uint16)"
TOPIC0 = "0x" + Web3.keccak(text=SIG).hex().removeprefix("0x")
RAW = ROOT / "data/raw/aave/borrow_events_raw.jsonl"
PROGRESS = ROOT / "data/raw/aave/borrow_scan_progress.json"
OUT = ROOT / "data/processed/aave/borrower_candidates.parquet"
STATE = ROOT / "data/raw/aave/state_check.json"
LIQ_RAW = ROOT / "data/raw/market/etherscan_liquidations_raw.csv"


def hx(v):
    return int(v, 16) if str(v).startswith("0x") else int(v)


def addr(word):
    w = word.lower().removeprefix("0x")
    return "0x" + w[-40:]


def logs_range(client, lo, hi):
    """Lay log trong [lo, hi]; neu du 1000 thi chia doi (khong nhan ket qua bi cat)."""
    res = client.get(module="logs", action="getLogs", address=POOL, topic0=TOPIC0,
                     fromBlock=lo, toBlock=hi, page=1, offset=1000)
    if not isinstance(res, list):
        raise RuntimeError("getLogs khong tra ve list")
    if len(res) < 1000:
        return res
    if lo < hi:
        mid = (lo + hi) // 2
        return logs_range(client, lo, mid) + logs_range(client, mid + 1, hi)
    out, page = res[:], 2
    while True:
        more = client.get(module="logs", action="getLogs", address=POOL, topic0=TOPIC0,
                          fromBlock=lo, toBlock=hi, page=page, offset=1000)
        out.extend(more)
        if len(more) < 1000:
            return out
        page += 1


def decode(log):
    t = log["topics"]
    if len(t) != 4 or t[0].lower() != TOPIC0:
        raise ValueError(f"Borrow event khong dung dang: {len(t)} topics")
    data = log["data"].removeprefix("0x")
    if len(data) < 64:
        raise ValueError("Borrow data qua ngan")
    return {
        "tx_hash": log["transactionHash"].lower(), "log_index": hx(log["logIndex"]),
        "event_block": hx(log["blockNumber"]), "timestamp": hx(log["timeStamp"]),
        "reserve": addr(t[1]), "on_behalf_of": addr(t[2]), "caller": addr(data[:64]),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--from-block", type=int, default=16_200_000,
                    help="truoc khi Aave V3 Ethereum ra mat (khoang 12/2022); kiem tra first_borrow_block nho nhat")
    ap.add_argument("--to-block", type=int, default=None, help="mac dinh = block Snapshot B")
    ap.add_argument("--span", type=int, default=200_000)
    ap.add_argument("--reset", action="store_true")
    args = ap.parse_args()

    state = json.loads(STATE.read_text(encoding="utf-8"))
    blocks = {k: v["block"] for k, v in state["snapshots"].items()}
    a_blk, b_blk = blocks.get("A", min(blocks.values())), max(blocks.values())  # b_blk = block lon nhat
    to_block = args.to_block or b_blk
    if to_block > b_blk:
        sys.exit("to-block khong duoc vuot block snapshot lon nhat (tranh dung du lieu tuong lai).")

    key = os.getenv("ETHERSCAN_API_KEY", "").strip().strip('"').strip("'").strip()
    if not key:
        sys.exit("Thieu ETHERSCAN_API_KEY trong .env")
    print(f"Do dai ETHERSCAN_API_KEY dang dung: {len(key)} ky tu (khong in key)")
    client = Etherscan(key)
    print(f"topic0 Borrow = {TOPIC0}")

    # Kiem tra chu ky event con dung khong (neu 0 event nghia la chu ky/pool khac)
    probe = logs_range(client, a_blk - 5000, a_blk)
    if not probe:
        sys.exit("PROBE THAT BAI: 0 Borrow event trong 5000 block truoc Snapshot A. "
                 "Chu ky event co the khac phien ban hien tai; kiem tra ABI Pool tren Etherscan.")
    decode(probe[0])
    print(f"Probe OK: {len(probe)} Borrow event trong 5000 block truoc A.")

    if args.reset:
        RAW.unlink(missing_ok=True)
        PROGRESS.unlink(missing_ok=True)
    RAW.parent.mkdir(parents=True, exist_ok=True)
    start = args.from_block
    if PROGRESS.exists():
        start = json.loads(PROGRESS.read_text())["last_completed_block"] + 1
        print(f"Tiep tuc tu block {start}")

    total_new = 0
    with open(RAW, "a", encoding="utf-8") as f:
        for lo in range(start, to_block + 1, args.span):
            hi = min(to_block, lo + args.span - 1)
            logs = logs_range(client, lo, hi)
            for lg in logs:
                f.write(json.dumps(decode(lg)) + "\n")
            f.flush()
            PROGRESS.write_text(json.dumps({"last_completed_block": hi}))
            total_new += len(logs)
            print(f"[{lo}-{hi}] {len(logs)} event | cong don lan chay nay: {total_new}")

    # Gop thanh bang vi ung vien
    ev = pd.read_json(RAW, lines=True, dtype={"tx_hash": str, "reserve": str,
                                              "on_behalf_of": str, "caller": str})
    n0 = len(ev)
    ev = ev.drop_duplicates(["tx_hash", "log_index"])
    print(f"Event: {n0} | sau khi bo trung (tx_hash, log_index): {len(ev)} (trung {n0 - len(ev)})")
    g = ev.groupby("on_behalf_of").agg(
        first_borrow_block=("event_block", "min"), last_borrow_block=("event_block", "max"),
        n_borrow_events=("event_block", "size")).reset_index().rename(columns={"on_behalf_of": "wallet_address"})
    for lb, bk in blocks.items():
        g[f"in_candidate_{lb}"] = g.first_borrow_block <= bk
    OUT.parent.mkdir(parents=True, exist_ok=True)
    g.to_parquet(OUT, index=False)
    print(f"Vi ung vien: {len(g)} | " + " | ".join(f"{lb}: {int(g[f'in_candidate_{lb}'].sum())}" for lb in blocks))
    print(f"Block Borrow dau tien: {int(g.first_borrow_block.min())} (neu gan --from-block thi ha --from-block xuong va chay --reset)")

    # Kiem tra do phu bang cac vi bi thanh ly that (N3), neu co file
    if LIQ_RAW.exists():
        import ast
        import csv
        with open(LIQ_RAW, newline="", encoding="utf-8") as fh:
            liq = {addr(ast.literal_eval(r["topics"])[3]) for r in csv.DictReader(fh)}
        cand = set(g.wallet_address)
        print(f"Vi bi thanh ly (N3, 12-16/10): {len(liq)} | co trong tap ung vien: {len(liq & cand)}")
        miss = liq - cand
        if miss:
            print("  Khong co Borrow event den Snapshot B (co the vay sau B, hoac no mo bang cach khac):")
            for w in sorted(miss)[:10]:
                print("   ", w)


if __name__ == "__main__":
    main()