"""
reconcile_protocol_totals.py - Doi chieu TONG NO cua tap vi voi tong no on-chain cua Aave V3 (Validation Gate 2).

Chay tu thu muc goc repo (sau fetch_positions.py):
    python src/data/reconcile_protocol_totals.py

Voi moi reserve tai block snapshot, lay totalSupply cua variable debt token + stable debt token
(tong no cua TOAN giao thuc) va so voi tong no cua cac vi trong positions_long.parquet.

Dau ra: data/processed/aave/protocol_reconciliation.parquet (theo snapshot x token)

LUU Y:
 - Chi doi chieu NO la co y nghia (tong no cua moi nguoi vay = totalSupply debt token). Tap vi ung vien
   duoc lap tu Borrow event nen ky vong phu ~100% no; phan thieu co the do no mo bang flash loan, ...
 - aToken.totalSupply gom ca nguoi chi gui tien (khong vay), nen collateral cua nguoi vay KHONG the bang
   TVL. Cot supply_share_held_by_debt_wallets chi de mo ta, khong phai tieu chi 1%.
 - Gia dinh cau truc getReserveData (aToken o vi tri 8, stable debt 9, variable debt 10) giong map_emode_assets.py.
CHUA duoc chay thu o phia nguoi viet. Khong luu RPC URL.
"""
import json
import os
import sys
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
STATE = ROOT / "data/raw/aave/state_check.json"
POOL = Web3.to_checksum_address("0x87870bca3f3fd6335c3f4ce8392d69350b4fa4e2")
MULTICALL3 = Web3.to_checksum_address("0xcA11bde05977b3631167028862bE2a173976CA11")
ZERO = "0x0000000000000000000000000000000000000000"
SEL_TS = Web3.keccak(text="totalSupply()")[:4]

FIELDS = [
    ("configuration", "uint256"), ("liquidityIndex", "uint128"), ("currentLiquidityRate", "uint128"),
    ("variableBorrowIndex", "uint128"), ("currentVariableBorrowRate", "uint128"),
    ("currentStableBorrowRate", "uint128"), ("lastUpdateTimestamp", "uint40"), ("id", "uint16"),
    ("aTokenAddress", "address"), ("stableDebtTokenAddress", "address"),
    ("variableDebtTokenAddress", "address"), ("interestRateStrategyAddress", "address"),
    ("accruedToTreasury", "uint128"), ("unbacked", "uint128"), ("isolationModeTotalDebt", "uint128"),
]
POOL_ABI = [{"name": "getReserveData", "type": "function", "stateMutability": "view",
             "inputs": [{"name": "asset", "type": "address"}],
             "outputs": [{"name": "d", "type": "tuple",
                          "components": [{"name": n, "type": t} for n, t in FIELDS]}]}]
MC_ABI = [{"name": "aggregate3", "type": "function", "stateMutability": "payable",
           "inputs": [{"name": "calls", "type": "tuple[]", "components": [
               {"name": "target", "type": "address"}, {"name": "allowFailure", "type": "bool"},
               {"name": "callData", "type": "bytes"}]}],
           "outputs": [{"name": "returnData", "type": "tuple[]", "components": [
               {"name": "success", "type": "bool"}, {"name": "returnData", "type": "bytes"}]}]}]


def supply(data_ok):
    ok, d = data_ok
    if not ok or len(d) < 32:
        return None
    return int(decode(["uint256"], d)[0])


def main():
    url = os.getenv("ALCHEMY_RPC_URL", "").strip() or os.getenv("ETH_RPC_URL", "").strip()
    if not url:
        sys.exit("Thieu ALCHEMY_RPC_URL (hoac ETH_RPC_URL).")
    w3 = Web3(Web3.HTTPProvider(url, request_kwargs={"timeout": 120}))
    pool = w3.eth.contract(address=POOL, abi=POOL_ABI)
    mc = w3.eth.contract(address=MULTICALL3, abi=MC_ABI)
    state = json.loads(STATE.read_text(encoding="utf-8"))
    pos = pd.read_parquet(P / "positions_long.parquet")
    px = pd.read_parquet(P / "oracle_prices.parquet")

    out = []
    for label, snap in state["snapshots"].items():
        blk = snap["block"]
        prices = px[px.snapshot_id == label].set_index("token_address")["price_usd"]
        res_rows = []
        for r in snap["reserves"]:
            a = Web3.to_checksum_address(r["token_address"])
            d = pool.functions.getReserveData(a).call(block_identifier=blk)
            res_rows.append((r, d[8], d[9], d[10]))
        calls = []
        for _, at, sd, vd in res_rows:
            for tok in (at, sd, vd):
                calls.append((tok, True, SEL_TS))
        res = mc.functions.aggregate3(calls).call(block_identifier=blk)
        p = pos[pos.snapshot_id == label].groupby("token_address").agg(
            wallet_debt_units=("debt_amount", "sum"), wallet_supplied_units=("collateral_amount", "sum"))
        for i, (r, at, sd, vd) in enumerate(res_rows):
            dec = int(r["decimals"])
            sc = 10.0 ** dec
            a_s, s_s, v_s = (supply(res[3 * i + k]) for k in range(3))
            if sd == ZERO:
                s_s = 0
            tok = r["token_address"]
            pu = (v_s or 0) / sc + (s_s or 0) / sc
            wu = float(p.wallet_debt_units.get(tok, 0.0))
            pr = float(prices.get(tok, float("nan")))
            out.append({
                "snapshot_id": label, "snapshot_block": blk, "token_address": tok, "symbol": r["symbol"],
                "decimals": dec, "price_usd": pr,
                "protocol_debt_units": pu, "wallet_debt_units": wu,
                "debt_coverage": (wu / pu) if pu > 0 else None,
                "protocol_debt_usd": pu * pr, "wallet_debt_usd": wu * pr,
                "atoken_supply_units": (a_s or 0) / sc,
                "wallet_supplied_units": float(p.wallet_supplied_units.get(tok, 0.0)),
                "supply_share_held_by_debt_wallets": (float(p.wallet_supplied_units.get(tok, 0.0)) / ((a_s or 0) / sc))
                if a_s else None,
                "supply_call_failed": any(x is None for x in (a_s, v_s)) or (sd != ZERO and s_s is None),
            })

    df = pd.DataFrame(out)
    for label, g in df.groupby("snapshot_id"):
        tp, tw = g.protocol_debt_usd.sum(), g.wallet_debt_usd.sum()
        print(f"[{label}] tong no giao thuc: {tp:,.0f} USD | tong no cua tap vi: {tw:,.0f} USD | "
              f"phu: {tw / tp:.4%} | lech: {tp - tw:,.0f} USD")
        print(f"   token loi goi totalSupply: {int(g.supply_call_failed.sum())}")
        big = g[g.protocol_debt_usd > 1e6].copy()
        big["gap_usd"] = big.protocol_debt_usd - big.wallet_debt_usd
        bad = big[(big.debt_coverage < 0.99) | (big.debt_coverage > 1.01)].sort_values("gap_usd", key=abs, ascending=False)
        print(f"   token no > 1 trieu USD: {len(big)} | co do phu ngoai [99%, 101%]: {len(bad)}")
        if len(bad):
            print(bad[["symbol", "protocol_debt_usd", "wallet_debt_usd", "debt_coverage"]].head(8).to_string(index=False))
    P.mkdir(parents=True, exist_ok=True)
    df.to_parquet(P / "protocol_reconciliation.parquet", index=False)
    print(f"Da luu {P / 'protocol_reconciliation.parquet'}")


if __name__ == "__main__":
    main()