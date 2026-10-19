"""
fetch_reserve_config.py - Lay cau hinh reserve Aave V3 tai Snapshot A/B va probe E-Mode.

Chay tu thu muc goc repo (sau check_state.py):
    pip install web3 pandas pyarrow python-dotenv
    python src/data/fetch_reserve_config.py

Dau ra:
  data/processed/aave/reserve_config.parquet   (LTV/LT/bonus/co/gioi han theo token, theo snapshot)
  data/raw/aave/emode_probe.json               (ket qua thu cac ham E-Mode; KHONG gia dinh phien ban)

Khong luu RPC URL. CHUA duoc chay thu o phia nguoi viet: doc ket qua that.
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
OUT_PQ = ROOT / "data/processed/aave/reserve_config.parquet"
OUT_PROBE = ROOT / "data/raw/aave/emode_probe.json"
MAX_EMODE_ID = 64


def fn(name, ins, outs):
    def comp(t):
        if isinstance(t, tuple):
            return {"type": "tuple", "components": [{"name": n, "type": ty} for n, ty in t[1]]}
        return {"type": t}
    return {
        "name": name, "type": "function", "stateMutability": "view",
        "inputs": [{"name": f"i{k}", **comp(t)} for k, t in enumerate(ins)],
        "outputs": [{"name": f"o{k}", **comp(t)} for k, t in enumerate(outs)],
    }


DP_ABI = [
    fn("getReserveConfigurationData", ["address"],
       ["uint256", "uint256", "uint256", "uint256", "uint256",
        "bool", "bool", "bool", "bool", "bool"]),
    fn("getReserveCaps", ["address"], ["uint256", "uint256"]),
    fn("getDebtCeiling", ["address"], ["uint256"]),
    fn("getPaused", ["address"], ["bool"]),
    fn("getSiloedBorrowing", ["address"], ["bool"]),
]

# Cac bien the ham E-Mode o cac phien ban Pool khac nhau (chi de thu, khong gia dinh)
EM_V30 = ("tuple", [("ltv", "uint16"), ("liquidationThreshold", "uint16"),
                    ("liquidationBonus", "uint16"), ("priceSource", "address"), ("label", "string")])
EM_V32 = ("tuple", [("ltv", "uint16"), ("liquidationThreshold", "uint16"),
                    ("liquidationBonus", "uint16")])
POOL_EM_ABI = [
    fn("getEModeCategoryData", ["uint8"], [EM_V30]),
    fn("getEModeCategoryCollateralConfig", ["uint8"], [EM_V32]),
    fn("getEModeCategoryLabel", ["uint8"], ["string"]),
    fn("getEModeCategoryCollateralBitmap", ["uint8"], ["uint128"]),
    fn("getEModeCategoryBorrowableBitmap", ["uint8"], ["uint128"]),
]


def safe(call):
    try:
        return call(), None
    except Exception as exc:  # noqa: BLE001
        return None, f"{type(exc).__name__}: {str(exc)[:100]}"


def set_bits(x):
    return [i for i in range(128) if (x >> i) & 1] if x else []


def main():
    if not STATE.exists():
        sys.exit("Chua co state_check.json; chay check_state.py truoc.")
    url = os.getenv("ALCHEMY_RPC_URL", "").strip()
    if not url:
        sys.exit("Thieu ALCHEMY_RPC_URL.")
    w3 = Web3(Web3.HTTPProvider(url, request_kwargs={"timeout": 60}))
    state = json.loads(STATE.read_text(encoding="utf-8"))
    pool_addr = Web3.to_checksum_address(state["pool"])

    rows, probe = [], {}
    for label, snap in state["snapshots"].items():
        blk = snap["block"]
        dp = w3.eth.contract(address=Web3.to_checksum_address(snap["pool_data_provider"]), abi=DP_ABI)
        pool = w3.eth.contract(address=pool_addr, abi=POOL_EM_ABI)
        ts = pd.to_datetime(snap["block_timestamp_utc"], unit="s", utc=True)

        for r in snap["reserves"]:
            a = Web3.to_checksum_address(r["token_address"])
            cfg, e = safe(lambda: dp.functions.getReserveConfigurationData(a).call(block_identifier=blk))
            if cfg is None:
                print(f"[{label}] LOI {r['symbol']}: {e}")
                continue
            dec, ltv, lt, bonus, rf, coll_en, borrow_en, stable_en, active, frozen = cfg
            caps, _ = safe(lambda: dp.functions.getReserveCaps(a).call(block_identifier=blk))
            ceil, _ = safe(lambda: dp.functions.getDebtCeiling(a).call(block_identifier=blk))
            paused, _ = safe(lambda: dp.functions.getPaused(a).call(block_identifier=blk))
            siloed, _ = safe(lambda: dp.functions.getSiloedBorrowing(a).call(block_identifier=blk))
            rows.append({
                "snapshot_id": label, "snapshot_block": blk, "snapshot_timestamp": ts,
                "token_address": a.lower(), "symbol": r["symbol"],
                "decimals": int(dec), "decimals_matches_state_check": int(dec) == r["decimals"],
                "ltv_bps": int(ltv), "lt_bps": int(lt), "liq_bonus_raw_bps": int(bonus),
                "ltv_std": ltv / 1e4, "lt_std": lt / 1e4,
                # Aave: bonus 10500 = 105% => premium 0.05; 0 neu token khong lam collateral
                "liq_bonus_std_premium": (bonus / 1e4 - 1) if bonus > 0 else 0.0,
                "reserve_factor": rf / 1e4,
                "usage_as_collateral": bool(coll_en), "borrowing_enabled": bool(borrow_en),
                "is_active": bool(active), "is_frozen": bool(frozen),
                "is_paused": paused, "siloed_borrowing": siloed,
                "borrow_cap": caps[0] if caps else None, "supply_cap": caps[1] if caps else None,
                "debt_ceiling_raw_2dec": ceil,  # >0 => thuong la isolation mode; xac nhan bang docs Aave
            })

        # Probe E-Mode: thu tung ham, ghi lai cai nao chay duoc
        p = {"working_functions": set(), "categories": {}}
        for cid in range(1, MAX_EMODE_ID + 1):
            cat = {}
            v30, _ = safe(lambda: pool.functions.getEModeCategoryData(cid).call(block_identifier=blk))
            if v30 and (v30[0] or v30[1]):
                cat["getEModeCategoryData"] = {"ltv_bps": v30[0], "lt_bps": v30[1],
                                               "bonus_raw_bps": v30[2], "price_source": v30[3], "label": v30[4]}
                p["working_functions"].add("getEModeCategoryData")
            v32, _ = safe(lambda: pool.functions.getEModeCategoryCollateralConfig(cid).call(block_identifier=blk))
            if v32 and (v32[0] or v32[1]):
                lbl, _ = safe(lambda: pool.functions.getEModeCategoryLabel(cid).call(block_identifier=blk))
                cb, _ = safe(lambda: pool.functions.getEModeCategoryCollateralBitmap(cid).call(block_identifier=blk))
                bb, _ = safe(lambda: pool.functions.getEModeCategoryBorrowableBitmap(cid).call(block_identifier=blk))
                cat["getEModeCategoryCollateralConfig"] = {
                    "ltv_bps": v32[0], "lt_bps": v32[1], "bonus_raw_bps": v32[2], "label": lbl,
                    "collateral_bitmap_set_bits": set_bits(cb), "borrowable_bitmap_set_bits": set_bits(bb),
                    "note": "bit i = reserve id; xac nhan reserve id <-> token truoc khi dung",
                }
                p["working_functions"].add("getEModeCategoryCollateralConfig")
            if cat:
                p["categories"][str(cid)] = cat
        p["working_functions"] = sorted(p["working_functions"])
        probe[label] = p
        print(f"[{label}] reserve config: {sum(1 for x in rows if x['snapshot_id'] == label)} dong | "
              f"E-Mode ham chay duoc: {p['working_functions'] or 'KHONG CO'} | "
              f"so category: {len(p['categories'])}")

    df = pd.DataFrame(rows)
    dup = df.duplicated(["snapshot_block", "token_address"]).sum()
    bad_dec = (~df["decimals_matches_state_check"]).sum()
    print(f"Tong hang: {len(df)} | trung khoa: {dup} | decimals lech state_check: {bad_dec}")
    print(df[df.symbol.isin(["WETH", "wstETH", "WBTC", "USDC", "weETH"]) & (df.snapshot_id == "A")]
          [["symbol", "ltv_std", "lt_std", "liq_bonus_std_premium", "usage_as_collateral",
            "borrowing_enabled", "is_frozen", "debt_ceiling_raw_2dec"]].to_string(index=False))

    OUT_PQ.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(OUT_PQ, index=False)
    OUT_PROBE.write_text(json.dumps(probe, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    print(f"Da luu {OUT_PQ} va {OUT_PROBE}")


if __name__ == "__main__":
    main()