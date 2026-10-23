"""
fetch_account_data.py - Lay tai khoan Aave (HF do Aave bao, tong collateral/no, E-Mode) cho
cac vi ung vien tai Snapshot A va B, qua Multicall3. Co checkpoint de chay tiep.

Chay tu thu muc goc repo (sau fetch_borrowers.py):
    python src/data/fetch_account_data.py            # ca A va B
    python src/data/fetch_account_data.py --snapshot A
    python src/data/fetch_account_data.py --limit 300   # chay thu tren 300 vi dau

Dau ra:
  data/raw/aave/account_data_raw_{A|B}.jsonl        (checkpoint, moi dong = 1 vi)
  data/processed/aave/account_reference.parquet     (HF Aave bao + tong USD; CHI vi co no > 0 la 'has_debt')

Don vi: *_base_usd la USD 8 chu so thap phan (da chia 1e8). health_factor_reported la so thuc (da chia 1e18);
null neu khong co no (Aave tra uint256 max).
Chi dung vi co event_block <= snapshot_block (khong ro ri tuong lai).
CHUA duoc chay thu o phia nguoi viet: chay --limit 300 truoc va doc ket qua.
"""
import argparse
import json
import os
import sys
import time
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
STATE = ROOT / "data/raw/aave/state_check.json"
CAND = ROOT / "data/processed/aave/borrower_candidates.parquet"
OUT = ROOT / "data/processed/aave/account_reference.parquet"
MULTICALL3 = Web3.to_checksum_address("0xcA11bde05977b3631167028862bE2a173976CA11")
POOL = Web3.to_checksum_address("0x87870bca3f3fd6335c3f4ce8392d69350b4fa4e2")
MAX_UINT = 2**256 - 1

SEL_ACCT = Web3.keccak(text="getUserAccountData(address)")[:4]
SEL_EMODE = Web3.keccak(text="getUserEMode(address)")[:4]
MC_ABI = [{
    "name": "aggregate3", "type": "function", "stateMutability": "payable",
    "inputs": [{"name": "calls", "type": "tuple[]", "components": [
        {"name": "target", "type": "address"}, {"name": "allowFailure", "type": "bool"},
        {"name": "callData", "type": "bytes"}]}],
    "outputs": [{"name": "returnData", "type": "tuple[]", "components": [
        {"name": "success", "type": "bool"}, {"name": "returnData", "type": "bytes"}]}],
}]


def run_batch(mc, wallets, blk):
    calls = []
    for w in wallets:
        arg = encode(["address"], [Web3.to_checksum_address(w)])
        calls.append((POOL, True, SEL_ACCT + arg))
        calls.append((POOL, True, SEL_EMODE + arg))
    for attempt in range(4):
        try:
            res = mc.functions.aggregate3(calls).call(block_identifier=blk)
            break
        except Exception as exc:  # noqa: BLE001
            if attempt == 3:
                raise
            print(f"   thu lai ({type(exc).__name__}: {str(exc)[:80]})")
            time.sleep(2 * (attempt + 1))
    rows = []
    for i, w in enumerate(wallets):
        ok1, d1 = res[2 * i]
        ok2, d2 = res[2 * i + 1]
        row = {"wallet_address": w, "call_ok": bool(ok1)}
        if ok1:
            coll, debt, avail, lt, ltv, hf = decode(["uint256"] * 6, d1)
            row.update({
                "total_collateral_base_usd": coll / 1e8, "total_debt_base_usd": debt / 1e8,
                "available_borrows_base_usd": avail / 1e8,
                "weighted_lt_bps": int(lt), "weighted_ltv_bps": int(ltv),
                "health_factor_reported": None if hf == MAX_UINT else hf / 1e18,
            })
        row["emode_category_user"] = int(decode(["uint256"], d2)[0]) if ok2 else None
        rows.append(row)
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--snapshot", default=None, help="nhan snapshot (A, B1, B2...); mac dinh: tat ca trong state_check.json")
    ap.add_argument("--batch", type=int, default=150, help="so vi moi yeu cau Multicall3")
    ap.add_argument("--limit", type=int, default=None, help="chi chay N vi dau (de thu)")
    args = ap.parse_args()

    url = os.getenv("ALCHEMY_RPC_URL", "").strip() or os.getenv("ETH_RPC_URL", "").strip()
    if not url:
        sys.exit("Thieu ALCHEMY_RPC_URL (hoac ETH_RPC_URL).")
    w3 = Web3(Web3.HTTPProvider(url, request_kwargs={"timeout": 120}))
    mc = w3.eth.contract(address=MULTICALL3, abi=MC_ABI)
    state = json.loads(STATE.read_text(encoding="utf-8"))
    cand = pd.read_parquet(CAND)

    labels = [args.snapshot] if args.snapshot else list(state["snapshots"])
    for lb in labels:
        if lb not in state["snapshots"]:
            sys.exit(f"Snapshot {lb} khong co trong state_check.json (chay check_state.py).")
        if f"in_candidate_{lb}" not in cand.columns:
            sys.exit(f"borrower_candidates.parquet thieu cot in_candidate_{lb}; chay build_candidates.py.")

    frames = []
    for label in labels:
        snap = state["snapshots"][label]
        blk = snap["block"]
        flag = f"in_candidate_{label}"
        wallets = sorted(cand.loc[cand[flag], "wallet_address"])
        if args.limit:
            wallets = wallets[: args.limit]
        raw = ROOT / f"data/raw/aave/account_data_raw_{label}.jsonl"
        raw.parent.mkdir(parents=True, exist_ok=True)
        guard = raw.with_name(raw.name + ".block")  # chan dung nham checkpoint cua block khac
        if guard.exists() and guard.read_text().strip() != str(blk):
            sys.exit(f"[{label}] checkpoint {raw.name} thuoc block khac ({guard.read_text().strip()} != {blk}). Xoa/doi ten roi chay lai.")
        guard.write_text(str(blk))
        done = set()
        if raw.exists():
            with open(raw, encoding="utf-8") as f:
                done = {json.loads(line)["wallet_address"] for line in f}
        todo = [w for w in wallets if w not in done]
        print(f"[{label}] block {blk}: {len(wallets)} vi, da co {len(done)}, con {len(todo)}")

        with open(raw, "a", encoding="utf-8") as f:
            for i in range(0, len(todo), args.batch):
                chunk = todo[i:i + args.batch]
                for row in run_batch(mc, chunk, blk):
                    f.write(json.dumps(row) + "\n")
                f.flush()
                if (i // args.batch) % 20 == 0:
                    print(f"   {min(i + args.batch, len(todo))}/{len(todo)}")

        df = pd.read_json(raw, lines=True, dtype={"wallet_address": str})
        df = df[df.wallet_address.isin(set(wallets))].drop_duplicates("wallet_address")
        df.insert(0, "snapshot_id", label)
        df.insert(1, "snapshot_block", blk)
        df["has_debt"] = df["total_debt_base_usd"].fillna(0) > 0
        frames.append(df)

        d = df[df.has_debt]
        print(f"[{label}] vi goi duoc: {int(df.call_ok.sum())}/{len(df)} | "
              f"co no: {len(d)} | HF<1: {int((d.health_factor_reported < 1).sum())} | "
              f"tong no USD: {d.total_debt_base_usd.sum():,.0f}")
        if len(d):
            print("   HF (vi co no) phan vi 1/5/50%:",
                  d.health_factor_reported.quantile([0.01, 0.05, 0.5]).round(3).tolist())

    if not args.limit:
        new = pd.concat(frames, ignore_index=True)
        if OUT.exists():  # giu snapshot khac con hop le, bo snapshot khong con trong config (vd B cu)
            old = pd.read_parquet(OUT)
            old = old[old.snapshot_id.isin(list(state["snapshots"])) & ~old.snapshot_id.isin(set(new.snapshot_id))]
            new = pd.concat([old, new], ignore_index=True)
        new.to_parquet(OUT, index=False)
        print(f"Da luu {OUT}")
    else:
        print("Che do --limit: KHONG ghi account_reference.parquet (chi de thu).")


if __name__ == "__main__":
    main()