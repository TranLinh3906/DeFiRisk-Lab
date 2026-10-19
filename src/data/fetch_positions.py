"""
fetch_positions.py - So du theo vi x token cho cac vi con no tai Snapshot A/B, va doi chieu HF.

Chay tu thu muc goc repo (sau fetch_account_data.py):
    python src/data/fetch_positions.py --limit 200    # chay thu truoc
    python src/data/fetch_positions.py                # chay day du (co checkpoint)
    python src/data/fetch_positions.py --snapshot A

Dau vao : account_reference.parquet, reserve_config.parquet, oracle_prices.parquet,
          emode_membership.parquet, state_check.json
Dau ra  : data/raw/aave/user_config_raw_{A|B}.jsonl, positions_raw_{A|B}.jsonl   (checkpoint)
          data/processed/aave/positions_long.parquet      (vi x token, don vi token + USD)
          data/processed/aave/hf_reconciliation.parquet   (HF tu tinh vs HF Aave bao, theo vi)

LUU Y:
 - Day la bang trung gian, KHONG phai schema canonical cua nhom. Buoc normalize_aave.py se anh xa
   sang fact_wallet_positions / fact_wallet_summary theo schema_storage_safe.py.
 - So du lon luu dang chuoi (a_balance_raw...) de khong mat chinh xac.
 - Quy tac tinh HF o day (E-Mode: neu vi o category != 0 va token thuoc bitmap collateral cua
   category thi dung LT cua category, nguoc lai LT chuan) la CACH HIEU cua nguoi viet ve Aave V3;
   ket qua duoc doi chieu voi HF Aave bao de kiem tra, khong coi la dung truoc khi doi chieu.
 - Tinh nang bo qua: isolation mode, siloed borrowing.
CHUA duoc chay thu o phia nguoi viet: chay --limit 200 truoc va doc ket qua.
"""
import argparse
import json
import os
import sys
import time
from pathlib import Path

import numpy as np
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
MULTICALL3 = Web3.to_checksum_address("0xcA11bde05977b3631167028862bE2a173976CA11")
POOL = Web3.to_checksum_address("0x87870bca3f3fd6335c3f4ce8392d69350b4fa4e2")
SEL_UCFG = Web3.keccak(text="getUserConfiguration(address)")[:4]
SEL_URD = Web3.keccak(text="getUserReserveData(address,address)")[:4]
MC_ABI = [{
    "name": "aggregate3", "type": "function", "stateMutability": "payable",
    "inputs": [{"name": "calls", "type": "tuple[]", "components": [
        {"name": "target", "type": "address"}, {"name": "allowFailure", "type": "bool"},
        {"name": "callData", "type": "bytes"}]}],
    "outputs": [{"name": "returnData", "type": "tuple[]", "components": [
        {"name": "success", "type": "bool"}, {"name": "returnData", "type": "bytes"}]}],
}]


def mc_call(mc, calls, blk):
    payload = [(t, True, d) for t, d in calls]
    for attempt in range(4):
        try:
            return mc.functions.aggregate3(payload).call(block_identifier=blk)
        except Exception as exc:  # noqa: BLE001
            if attempt == 3:
                raise
            print(f"   thu lai ({type(exc).__name__}: {str(exc)[:80]})")
            time.sleep(2 * (attempt + 1))


def read_jsonl(path):
    if not path.exists():
        return []
    with open(path, encoding="utf-8") as f:
        return [json.loads(line) for line in f]


def stage1_user_config(mc, wallets, blk, path, batch=500):
    done = {r["wallet"] for r in read_jsonl(path)}
    todo = [w for w in wallets if w not in done]
    print(f"   stage1 getUserConfiguration: {len(todo)} vi can lay")
    with open(path, "a", encoding="utf-8") as f:
        for i in range(0, len(todo), batch):
            chunk = todo[i:i + batch]
            calls = [(POOL, SEL_UCFG + encode(["address"], [Web3.to_checksum_address(w)])) for w in chunk]
            for w, (ok, d) in zip(chunk, mc_call(mc, calls, blk)):
                bm = int(decode(["uint256"], d)[0]) if ok else None
                f.write(json.dumps({"wallet": w, "bitmap": None if bm is None else str(bm)}) + "\n")
            f.flush()


def stage2_reserve_data(mc, wallets, blk, dp_addr, reserves, cfg_path, pos_path, per_call=400):
    cfg = {r["wallet"]: r["bitmap"] for r in read_jsonl(cfg_path)}
    done = {r["wallet"] for r in read_jsonl(pos_path)}
    todo = [w for w in wallets if w not in done and cfg.get(w) is not None]
    n = len(reserves)
    print(f"   stage2 getUserReserveData: {len(todo)} vi can lay")
    anomalies = 0
    batch_w, batch_calls = [], []

    def flush(f):
        nonlocal batch_w, batch_calls
        if not batch_calls:
            return
        res = mc_call(mc, [(dp_addr, c) for _, _, c in batch_calls], blk)
        got = {w: [] for w in batch_w}
        for (w, rid, _), (ok, d) in zip(batch_calls, res):
            row = {"wallet": w, "rid": rid, "token": reserves[rid]["token_address"], "ok": bool(ok)}
            if ok:
                a, sd, vd, _, _, _, _, _, cflag = decode(["uint256"] * 7 + ["uint40", "bool"], d)
                row.update({"a_bal": str(a), "stable": str(sd), "var": str(vd), "coll_flag": bool(cflag)})
            got[w].append(row)
        for w in batch_w:
            # vi khong co reserve nao duoc danh dau van duoc ghi 1 dong "rong" de checkpoint
            f.write(json.dumps({"wallet": w, "rows": got[w]}) + "\n")
        f.flush()
        batch_w, batch_calls = [], []

    with open(pos_path, "a", encoding="utf-8") as f:
        for k, w in enumerate(todo):
            bm = int(cfg[w])
            rids = [i for i in range(n) if (bm >> (2 * i)) & 3]
            if bm >> (2 * n):
                anomalies += 1
            arg_user = Web3.to_checksum_address(w)
            for rid in rids:
                data = SEL_URD + encode(["address", "address"],
                                        [Web3.to_checksum_address(reserves[rid]["token_address"]), arg_user])
                batch_calls.append((w, rid, data))
            batch_w.append(w)
            if len(batch_calls) >= per_call:
                flush(f)
            if k % 5000 == 0 and k:
                print(f"   {k}/{len(todo)}")
        flush(f)
    if anomalies:
        print(f"   CANH BAO: {anomalies} vi co bit ngoai danh sach reserve da biet")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--snapshot", choices=["A", "B"], default=None)
    ap.add_argument("--limit", type=int, default=None)
    args = ap.parse_args()

    url = os.getenv("ALCHEMY_RPC_URL", "").strip() or os.getenv("ETH_RPC_URL", "").strip()
    if not url:
        sys.exit("Thieu ALCHEMY_RPC_URL (hoac ETH_RPC_URL).")
    w3 = Web3(Web3.HTTPProvider(url, request_kwargs={"timeout": 120}))
    mc = w3.eth.contract(address=MULTICALL3, abi=MC_ABI)

    state = json.loads(STATE.read_text(encoding="utf-8"))
    acct = pd.read_parquet(P / "account_reference.parquet")
    cfg_tbl = pd.read_parquet(P / "reserve_config.parquet")
    px = pd.read_parquet(P / "oracle_prices.parquet")
    emem = pd.read_parquet(P / "emode_membership.parquet")

    all_pos, all_rec = [], []
    for label in ([args.snapshot] if args.snapshot else ["A", "B"]):
        snap = state["snapshots"][label]
        blk = snap["block"]
        reserves = snap["reserves"]  # thu tu getReservesList; id = vi tri (da kiem tra: id 0..66 duy nhat)
        # Kiem tra: reserve_id trong emode_membership phai khop thu tu danh sach
        em = emem[emem.snapshot_id == label]
        bad = [(r.reserve_id, r.token_address) for r in em.itertuples()
               if reserves[r.reserve_id]["token_address"] != r.token_address]
        if bad:
            sys.exit(f"[{label}] reserve id khong khop vi tri danh sach: {bad[:3]}. Dung lai.")

        a = acct[(acct.snapshot_id == label) & acct.has_debt & acct.call_ok]
        wallets = sorted(a.wallet_address)
        if args.limit:
            wallets = wallets[: args.limit]
        a = a[a.wallet_address.isin(set(wallets))]  # doi chieu chi tren vi da chay
        print(f"[{label}] block {blk}: {len(wallets)} vi co no")
        cfg_path = ROOT / f"data/raw/aave/user_config_raw_{label}.jsonl"
        pos_path = ROOT / f"data/raw/aave/positions_raw_{label}.jsonl"
        stage1_user_config(mc, wallets, blk, cfg_path)
        stage2_reserve_data(mc, wallets, blk, Web3.to_checksum_address(snap["pool_data_provider"]),
                            reserves, cfg_path, pos_path)

        # ---- Dung bang vi x token ----
        wset = set(wallets)
        rows = [r for rec in read_jsonl(pos_path) if rec["wallet"] in wset for r in rec["rows"]]
        fail = sum(1 for r in rows if not r["ok"])
        rows = [r for r in rows if r["ok"]]
        df = pd.DataFrame(rows).drop_duplicates(["wallet", "token"])
        c = cfg_tbl[cfg_tbl.snapshot_id == label].set_index("token_address")
        pr = px[px.snapshot_id == label].set_index("token_address")["price_usd"]
        df["symbol"] = df.token.map(c["symbol"])
        df["decimals"] = df.token.map(c["decimals"]).astype(int)
        df["lt_std"] = df.token.map(c["lt_bps"]).astype(int)
        df["price_usd"] = df.token.map(pr)
        sc = 10.0 ** df["decimals"]
        df["collateral_amount"] = df.a_bal.map(int).astype(float) / sc
        df["debt_amount"] = (df.stable.map(int) + df["var"].map(int)).astype(float) / sc
        df["collateral_usd"] = df.collateral_amount * df.price_usd
        df["debt_usd"] = df.debt_amount * df.price_usd

        # LT hieu luc theo E-Mode cua vi
        ucat = a.set_index("wallet_address")["emode_category_user"]
        df["user_emode"] = df.wallet.map(ucat).fillna(0).astype(int)
        lt_em = {(r.emode_category, r.token_address): r.emode_lt_bps
                 for r in em[em.is_collateral].itertuples()}
        df["lt_eff"] = [lt_em.get((u, t), s) if u != 0 else s
                        for u, t, s in zip(df.user_emode, df.token, df.lt_std)]
        df["counted_collateral"] = df.coll_flag & (df.lt_eff > 0)
        df.insert(0, "snapshot_id", label)
        df.insert(1, "snapshot_block", blk)
        all_pos.append(df)

        # ---- Doi chieu HF theo vi ----
        df["coll_counted_usd"] = np.where(df.counted_collateral, df.collateral_usd, 0.0)
        df["coll_x_lt"] = df.coll_counted_usd * df.lt_eff / 1e4
        g = df.groupby("wallet").agg(collateral_usd_calc=("coll_counted_usd", "sum"),
                                     debt_usd_calc=("debt_usd", "sum"),
                                     coll_x_lt=("coll_x_lt", "sum")).reset_index()
        g = g.merge(a[["wallet_address", "total_collateral_base_usd", "total_debt_base_usd",
                       "health_factor_reported", "emode_category_user"]].rename(columns={"wallet_address": "wallet"}), on="wallet", how="right")
        g["hf_calc"] = np.where(g.debt_usd_calc > 0, g.coll_x_lt / g.debt_usd_calc, np.nan)
        g["hf_abs_err"] = (g.hf_calc - g.health_factor_reported).abs()
        g["hf_rel_err"] = g.hf_abs_err / g.health_factor_reported
        g["debt_rel_err"] = (g.debt_usd_calc - g.total_debt_base_usd).abs() / g.total_debt_base_usd
        g["coll_rel_err"] = ((g.collateral_usd_calc - g.total_collateral_base_usd).abs()
                             / g.total_collateral_base_usd.replace(0, np.nan))
        g.insert(0, "snapshot_id", label)
        g.insert(1, "snapshot_block", blk)
        all_rec.append(g)

        v = g.dropna(subset=["hf_abs_err"])
        print(f"[{label}] dong vi x token: {len(df)} | loi goi: {fail} | vi doi chieu duoc: {len(v)}/{len(g)}")
        print(f"   |HF tinh - HF Aave| trung vi: {v.hf_abs_err.median():.6f} | p95: {v.hf_abs_err.quantile(.95):.6f} "
              f"| p99: {v.hf_abs_err.quantile(.99):.6f}")
        print(f"   vi co sai so no <1%: {(g.debt_rel_err < 0.01).mean():.4f} | sai so collateral <1%: "
              f"{(g.coll_rel_err.dropna() < 0.01).mean():.4f}")
        lo = v[v.health_factor_reported <= 10]
        print(f"   sai so tuyet doi HF chi vi HF<=10 ({len(lo)} vi): trung vi {lo.hf_abs_err.median():.6f} "
              f"| p99 {lo.hf_abs_err.quantile(.99):.6f}")
        print(f"   sai so tuong doi HF: trung vi {v.hf_rel_err.median():.2e} | p99 {v.hf_rel_err.quantile(.99):.2e}")
        em_v = v[v.emode_category_user.fillna(0) > 0]
        print(f"   vi o E-Mode: {len(em_v)} | sai so tuong doi HF trung vi: "
              f"{em_v.hf_rel_err.median() if len(em_v) else float('nan'):.2e}")
        print(f"   HF<1 theo Aave: {int((g.health_factor_reported < 1).sum())} | HF<1 theo tinh: {int((g.hf_calc < 1).sum())}")
        worst = v.sort_values("hf_rel_err", ascending=False).head(3)
        for r in worst.itertuples():
            print(f"   vi sai nhieu (theo sai so tuong doi): {r.wallet[:10]}... HF Aave={r.health_factor_reported:.4f} "
                  f"HF tinh={r.hf_calc:.4f} | no tinh USD={r.debt_usd_calc:.4f} | no Aave USD={r.total_debt_base_usd:.4f}")

    if not args.limit:
        pd.concat(all_pos, ignore_index=True)[[
            "snapshot_id", "snapshot_block", "wallet", "token", "symbol", "decimals", "a_bal", "stable", "var",
            "coll_flag", "collateral_amount", "debt_amount", "price_usd", "collateral_usd", "debt_usd",
            "user_emode", "lt_std", "lt_eff", "counted_collateral"]].rename(columns={
                "wallet": "wallet_address", "token": "token_address", "a_bal": "a_balance_raw",
                "stable": "stable_debt_raw", "var": "variable_debt_raw"}).to_parquet(P / "positions_long.parquet", index=False)
        pd.concat(all_rec, ignore_index=True).to_parquet(P / "hf_reconciliation.parquet", index=False)
        print(f"Da luu {P / 'positions_long.parquet'} va hf_reconciliation.parquet")
    else:
        print("Che do --limit: KHONG ghi file dau ra cuoi (chi de thu).")


if __name__ == "__main__":
    main()