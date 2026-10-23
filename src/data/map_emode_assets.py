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
# E-Mode kieu cu (Aave V3.0/3.1): moi reserve thuoc dung 1 category, tra bang DataProvider
DP_ABI = [{"name": "getReserveEModeCategory", "type": "function", "stateMutability": "view",
           "inputs": [{"name": "asset", "type": "address"}], "outputs": [{"name": "o", "type": "uint256"}]}]


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

        cats = probe[label]["categories"]
        needs_old = any((not c.get("getEModeCategoryCollateralConfig")) and c.get("getEModeCategoryData")
                        for c in cats.values())
        old_members = {}
        if needs_old:
            dp = w3.eth.contract(address=Web3.to_checksum_address(snap["pool_data_provider"]), abi=DP_ABI)
            for rid, tk in id_to_tok.items():
                try:
                    old_members[rid] = int(dp.functions.getReserveEModeCategory(
                        Web3.to_checksum_address(tk["token_address"])).call(block_identifier=blk))
                except Exception as exc:  # noqa: BLE001
                    problems.append(f"[{label}] {tk['symbol']}: getReserveEModeCategory loi ({type(exc).__name__})")
            print(f"[{label}] E-Mode KIEU CU: reserve thuoc category != 0: "
                  f"{sum(1 for c in old_members.values() if c)}")

        for cid, cat in cats.items():
            cfg = cat.get("getEModeCategoryCollateralConfig")
            if not cfg:
                old = cat.get("getEModeCategoryData")
                if not old:
                    problems.append(f"[{label}] category {cid}: khong doc duoc cau hinh E-Mode")
                    continue
                if old.get("price_source") and old["price_source"] != ZERO:
                    problems.append(f"[{label}] category {cid}: priceSource != 0 ({old['price_source']}); "
                                    "Aave dung oracle rieng cho vi o E-Mode, HF tu tinh co the lech")
                for rid, c in old_members.items():
                    if c != int(cid):
                        continue
                    tk = id_to_tok[rid]
                    rows.append({
                        "snapshot_id": label, "snapshot_block": blk, "snapshot_timestamp": ts,
                        "emode_category": int(cid), "emode_label": old["label"],
                        "reserve_id": rid, "token_address": tk["token_address"], "symbol": tk["symbol"],
                        "is_collateral": True, "is_borrowable": True,
                        "emode_ltv_bps": old["ltv_bps"], "emode_lt_bps": old["lt_bps"],
                        "emode_bonus_raw_bps": old["bonus_raw_bps"],
                        "emode_bonus_premium": old["bonus_raw_bps"] / 1e4 - 1,
                        "emode_membership_method": "getReserveEModeCategory",
                        "emode_price_source": old.get("price_source"),
                    })
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
                    "emode_membership_method": "bitmap", "emode_price_source": None,
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

    for lb in state["snapshots"]:
        for cid in (1, 3, 22, 26):
            sub = df[(df.snapshot_id == lb) & (df.emode_category == cid)]
            if not len(sub):
                continue
            print(f"\n[{lb}] Category {cid} ({sub.emode_label.iloc[0]}):")
            print("  collateral:", ", ".join(sub[sub.is_collateral].symbol))
            print("  borrowable:", ", ".join(sub[sub.is_borrowable].symbol))

    OUT.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(OUT, index=False)
    print(f"\nDa luu {OUT}")


if __name__ == "__main__":
    main()