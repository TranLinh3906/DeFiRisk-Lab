"""
map_emode_assets.py - Anh xa bitmap E-Mode (bit i = reserve id i) sang token.

Chay tu thu muc goc repo (sau check_state.py va fetch_reserve_config.py):
    python src/data/map_emode_assets.py

Dau vao : data/raw/aave/state_check.json, data/raw/aave/emode_probe.json
Dau ra  : data/processed/aave/emode_membership.parquet

CHUA duoc chay thu o phia nguoi viet. Script co kiem tra: id phai duy nhat, aToken khac 0,
moi bit trong bitmap phai tim duoc token. Neu mot kiem tra hong, DUNG LAI va bao lai,
khong dung bang ket qua.
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
PROBE = ROOT / "data/raw/aave/emode_probe.json"
OUT = ROOT / "data/processed/aave/emode_membership.parquet"

# Gia dinh: getReserveData tra ve struct 15 truong (layout ReserveDataLegacy cua Aave V3).
# Neu gia dinh sai, cac kiem tra ben duoi se bao loi (id trung / aToken = 0).
FIELDS = [
    ("configuration", "uint256"), ("liquidityIndex", "uint128"), ("currentLiquidityRate", "uint128"),
    ("variableBorrowIndex", "uint128"), ("currentVariableBorrowRate", "uint128"),
    ("currentStableBorrowRate", "uint128"), ("lastUpdateTimestamp", "uint40"), ("id", "uint16"),
    ("aTokenAddress", "address"), ("stableDebtTokenAddress", "address"),
    ("variableDebtTokenAddress", "address"), ("interestRateStrategyAddress", "address"),
    ("accruedToTreasury", "uint128"), ("unbacked", "uint128"), ("isolationModeTotalDebt", "uint128"),
]
POOL_ABI = [{
    "name": "getReserveData", "type": "function", "stateMutability": "view",
    "inputs": [{"name": "asset", "type": "address"}],
    "outputs": [{"name": "d", "type": "tuple",
                 "components": [{"name": n, "type": t} for n, t in FIELDS]}],
}]
ZERO = "0x0000000000000000000000000000000000000000"


def main():
    for p in (STATE, PROBE):
        if not p.exists():
            sys.exit(f"Thieu {p}")
    url = os.getenv("ALCHEMY_RPC_URL", "").strip()
    if not url:
        sys.exit("Thieu ALCHEMY_RPC_URL.")
    w3 = Web3(Web3.HTTPProvider(url, request_kwargs={"timeout": 60}))
    state = json.loads(STATE.read_text(encoding="utf-8"))
    probe = json.loads(PROBE.read_text(encoding="utf-8"))
    pool = w3.eth.contract(address=Web3.to_checksum_address(state["pool"]), abi=POOL_ABI)

    rows, problems = [], []
    for label, snap in state["snapshots"].items():
        blk = snap["block"]
        ts = pd.to_datetime(snap["block_timestamp_utc"], unit="s", utc=True)
        id_to_tok = {}
        for r in snap["reserves"]:
            a = Web3.to_checksum_address(r["token_address"])
            d = pool.functions.getReserveData(a).call(block_identifier=blk)
            rid, atoken = int(d[7]), d[8]
            if atoken == ZERO:
                problems.append(f"[{label}] {r['symbol']}: aToken = 0 (giai ma struct co the sai)")
            if rid in id_to_tok:
                problems.append(f"[{label}] id {rid} trung: {id_to_tok[rid]['symbol']} va {r['symbol']}")
            id_to_tok[rid] = r
        print(f"[{label}] {len(id_to_tok)} reserve id duy nhat / {len(snap['reserves'])} reserve; "
              f"id min={min(id_to_tok)} max={max(id_to_tok)}")

        for cid, cat in probe[label]["categories"].items():
            cfg = cat.get("getEModeCategoryCollateralConfig")
            if not cfg:
                problems.append(f"[{label}] category {cid}: khong co bitmap (chi co ham kieu cu)")
                continue
            col = set(cfg["collateral_bitmap_set_bits"])
            bor = set(cfg["borrowable_bitmap_set_bits"])
            for bit in sorted(col | bor):
                tok = id_to_tok.get(bit)
                if tok is None:
                    problems.append(f"[{label}] category {cid}: bit {bit} khong khop reserve nao")
                    continue
                rows.append({
                    "snapshot_id": label, "snapshot_block": blk, "snapshot_timestamp": ts,
                    "emode_category": int(cid), "emode_label": cfg["label"],
                    "reserve_id": bit, "token_address": tok["token_address"], "symbol": tok["symbol"],
                    "is_collateral": bit in col, "is_borrowable": bit in bor,
                    "emode_ltv_bps": cfg["ltv_bps"], "emode_lt_bps": cfg["lt_bps"],
                    "emode_bonus_raw_bps": cfg["bonus_raw_bps"],
                    "emode_bonus_premium": cfg["bonus_raw_bps"] / 1e4 - 1,
                })

    df = pd.DataFrame(rows)
    print(f"\nTong hang: {len(df)} | trung khoa: "
          f"{df.duplicated(['snapshot_id', 'emode_category', 'token_address']).sum()}")
    if problems:
        print("\nVAN DE - CAN XEM TRUOC KHI DUNG BANG NAY:")
        for p in problems[:30]:
            print("  ", p)
        print(f"   ({len(problems)} van de)")
    else:
        print("Khong co van de o cac kiem tra tu dong.")

    for cid in (1, 3, 22, 26):
        sub = df[(df.snapshot_id == "A") & (df.emode_category == cid)]
        print(f"\nCategory {cid} ({sub.emode_label.iloc[0] if len(sub) else '?'}):")
        print("  collateral:", ", ".join(sub[sub.is_collateral].symbol))
        print("  borrowable:", ", ".join(sub[sub.is_borrowable].symbol))

    OUT.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(OUT, index=False)
    print(f"\nDa luu {OUT}")


if __name__ == "__main__":
    main()