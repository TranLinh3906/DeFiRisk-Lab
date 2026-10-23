"""
build_candidates.py - Lap borrower_candidates.parquet cho MOI snapshot trong config/snapshots.json
tu file borrow_events_raw.jsonl da quet (KHONG goi Etherscan).

Chay tu thu muc goc repo:
    python src/data/build_candidates.py

Quy tac: vi ung vien cua snapshot = vi co it nhat 1 Borrow event voi event_block <= block snapshot.
Yeu cau: raw da quet den block >= block lon nhat trong config (kiem tra bang borrow_scan_progress.json).
Dau ra : data/processed/aave/borrower_candidates.parquet (cot in_candidate_<label> cho moi snapshot)

Day la tap UNG VIEN (tung vay), khong phai tap vi dang con no. CHUA duoc chay thu o phia nguoi viet.
"""
import json
import sys
from pathlib import Path

import pandas as pd

ROOT = Path.cwd()
RAW = ROOT / "data/raw/aave/borrow_events_raw.jsonl"
PROGRESS = ROOT / "data/raw/aave/borrow_scan_progress.json"
CFG = ROOT / "config/snapshots.json"
OUT = ROOT / "data/processed/aave/borrower_candidates.parquet"


def main():
    for p in (RAW, PROGRESS, CFG):
        if not p.exists():
            sys.exit(f"Thieu {p}")
    snaps = json.loads(CFG.read_text(encoding="utf-8"))["snapshots"]
    blocks = {k: int(v["block"]) for k, v in snaps.items() if v.get("block")}
    missing = [k for k, v in snaps.items() if not v.get("block")]
    if missing:
        sys.exit(f"config/snapshots.json chua co block cho {missing}; chay find_snapshot_blocks.py.")

    scanned_to = int(json.loads(PROGRESS.read_text())["last_completed_block"])
    if max(blocks.values()) > scanned_to:
        sys.exit(f"Raw event moi quet den block {scanned_to} < block snapshot lon nhat {max(blocks.values())}. "
                 "Chay fetch_borrowers.py de quet them.")
    print(f"Raw event da quet den block {scanned_to} | snapshot: {blocks}")

    ev = pd.read_json(RAW, lines=True, dtype={"tx_hash": str, "reserve": str, "on_behalf_of": str, "caller": str})
    n0 = len(ev)
    ev = ev.drop_duplicates(["tx_hash", "log_index"])
    print(f"Event: {n0} | sau khi bo trung (tx_hash, log_index): {len(ev)}")

    g = ev.groupby("on_behalf_of").agg(
        first_borrow_block=("event_block", "min"), last_borrow_block=("event_block", "max"),
        n_borrow_events=("event_block", "size")).reset_index().rename(columns={"on_behalf_of": "wallet_address"})
    for lb, bk in blocks.items():
        g[f"in_candidate_{lb}"] = g.first_borrow_block <= bk

    # So voi file cu (neu co): so vi A phai khong doi
    if OUT.exists():
        old = pd.read_parquet(OUT)
        if "in_candidate_A" in old.columns and "A" in blocks:
            print(f"Vi ung vien A: truoc {int(old.in_candidate_A.sum()):,} | sau {int(g.in_candidate_A.sum()):,}"
                  " (phai bang nhau vi block A khong doi)")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    g.to_parquet(OUT, index=False)
    print(f"Tong vi: {len(g):,} | " + " | ".join(f"{lb}: {int(g[f'in_candidate_{lb}'].sum()):,}" for lb in blocks))
    print(f"Block Borrow dau tien: {int(g.first_borrow_block.min())}")
    print(f"Da luu {OUT}")


if __name__ == "__main__":
    main()