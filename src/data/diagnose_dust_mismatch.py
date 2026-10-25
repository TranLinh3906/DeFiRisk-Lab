"""
diagnose_dust_mismatch.py - Ra nguyen nhan sai lech % o cac khoan no rat nho (khong goi RPC).

Chay tu thu muc goc repo (sau fetch_positions.py, reconcile, normalize...):
    python src/data/diagnose_dust_mismatch.py

Phan 1: phan bo sai so TUYET DOI (USD) cua cac vi bi coi la "lech >=1%", de de xuat tieu chi chap nhan sai so.
Phan 2: tai tao tong no Aave bang SO NGUYEN (don vi co so 1e-8 USD, nhu getUserAccountData) voi cac quy tac lam tron
        khac nhau (balanceOf, scaled x chi so lam tron nua/len/xuong). Quy tac nao cho ti le khop so nguyen cao nhat
        la ung vien cho cach Aave thuc su tinh. Day la phep thu, KHONG phai ket luan.
Dau ra: data/processed/aave/dust_diagnosis.json
Phan 2 chi xet vi co tong no < 10 trieu USD (de so nguyen khong bi mat chinh xac khi so sanh voi so thuc).
CHUA duoc chay thu o phia nguoi viet (chi kiem tra cu phap).
"""
import json
from pathlib import Path

import pandas as pd

ROOT = Path.cwd()
P = ROOT / "data/processed/aave"
RAY = 10 ** 27
BINS = [-1, 1e-6, 1e-4, 1e-2, 1, float("inf")]
LABELS = ["<=1e-6", "<=1e-4", "<=0.01", "<=1", ">1"]


def part1(r, out):
    print("=== PHAN 1: sai so tuyet doi cua cac vi lech >=1% ===")
    for lb, g in r.groupby("snapshot_id"):
        g = g.copy()
        g["debt_abs"] = (g.debt_usd_calc - g.total_debt_base_usd).abs()
        g["coll_abs"] = (g.collateral_usd_calc - g.total_collateral_base_usd).abs()
        res = {}
        for name, rel, ab in (("no", "debt_rel_err", "debt_abs"), ("collateral", "coll_rel_err", "coll_abs")):
            bad = g[g[rel] >= 0.01]
            dist = pd.cut(bad[ab], BINS, labels=LABELS).value_counts().reindex(LABELS).fillna(0).astype(int)
            fail = int((bad[ab] > 0.01).sum())
            res[name] = {"wallets_rel_ge_1pct": int(len(bad)), "abs_err_bins_usd": dist.to_dict(),
                         "fail_if_rel_ge_1pct_AND_abs_gt_0.01usd": fail,
                         "max_abs_err_usd": round(float(bad[ab].max()) if len(bad) else 0.0, 6)}
            print(f"[{lb}] {name}: {len(bad)} vi lech >=1% | phan bo sai so tuyet doi (USD): {dist.to_dict()} | "
                  f"van lech neu them dieu kien abs>0.01 USD: {fail} | sai so tuyet doi lon nhat: {res[name]['max_abs_err_usd']}")
        flip = g[(g.health_factor_reported < 1) != (g.hf_calc < 1)]
        res["hf_lt1_flips"] = {"wallets": int(len(flip)), "max_debt_abs_err_usd": round(float(flip.debt_abs.max()) if len(flip) else 0.0, 6),
                               "total_aave_debt_usd": round(float(flip.total_debt_base_usd.sum()), 4)}
        print(f"[{lb}] ve HF<1: {len(flip)} vi lech nguong | sai so no tuyet doi lon nhat {res['hf_lt1_flips']['max_debt_abs_err_usd']} USD")
        out.setdefault(lb, {})["part1"] = res


def part2(cfg_labels, out):
    print("\n=== PHAN 2: tai tao tong no Aave bang so nguyen (don vi 1e-8 USD) ===")
    st = json.loads((ROOT / "data/raw/aave/state_check.json").read_text(encoding="utf-8"))["snapshots"]
    px = pd.read_parquet(P / "oracle_prices.parquet")
    ar = pd.read_parquet(P / "account_reference.parquet")
    for lb in cfg_labels:
        raw = ROOT / f"data/raw/aave/positions_raw_{lb}.jsonl"
        if lb not in st or not raw.exists():
            print(f"[{lb}] bo qua (thieu du lieu)")
            continue
        res = {r["token_address"]: r for r in st[lb]["reserves"]}
        price = px[px.snapshot_id == lb].set_index("token_address").oracle_price_raw.map(int).to_dict()
        a = ar[(ar.snapshot_id == lb) & ar.has_debt & (ar.total_debt_base_usd < 1e7)].set_index("wallet_address")
        aave_int = (a.total_debt_base_usd * 1e8).round().astype("int64").to_dict()
        raw_rules = ["balanceOf", "scaled_half_up", "scaled_ceil", "scaled_floor"]
        variants = [f"{v}|{b}" for v in raw_rules for b in ("base_floor", "base_ceil")]  # b = lam tron buoc doi sang USD
        tot = {v: {} for v in variants}
        debt_tokens = {}
        n_missing = 0
        for line in open(raw, encoding="utf-8"):
            rec = json.loads(line)
            w = rec["wallet"]
            if w not in aave_int:
                continue
            for r in rec["rows"]:
                if not r["ok"]:
                    continue
                if "scaled_var" not in r:
                    n_missing += 1
                    continue
                t = r["token"]
                idx = int(res[t]["variable_borrow_index_normalized"])
                unit = 10 ** int(res[t]["decimals"])
                s, stable = int(r["scaled_var"]), int(r["stable"])
                raws = {"balanceOf": int(r["var"]) + stable,
                        "scaled_half_up": (s * idx + RAY // 2) // RAY + stable,
                        "scaled_ceil": (s * idx + RAY - 1) // RAY + stable,
                        "scaled_floor": (s * idx) // RAY + stable}
                if raws["balanceOf"] > 0:
                    debt_tokens.setdefault(w, set()).add(res[t]["symbol"])
                for v in raw_rules:
                    num = raws[v] * price[t]
                    tot[f"{v}|base_floor"][w] = tot[f"{v}|base_floor"].get(w, 0) + num // unit
                    tot[f"{v}|base_ceil"][w] = tot[f"{v}|base_ceil"].get(w, 0) + (-(-num // unit))
        if n_missing:
            print(f"[{lb}] CANH BAO: {n_missing} dong khong co scaled_var (checkpoint cu) bi bo qua")
        wallets = list(aave_int)
        res_lb = {"wallets_tested": len(wallets)}
        for v in variants:
            diff = {w: tot[v].get(w, 0) - aave_int[w] for w in wallets}
            exact = sum(1 for d in diff.values() if d == 0)
            off1 = sum(1 for d in diff.values() if abs(d) == 1)
            lower = sum(1 for d in diff.values() if d < 0)
            res_lb[v] = {"exact_match": exact, "off_by_1_unit": off1, "other": len(wallets) - exact - off1,
                         "ours_lower_than_aave": lower, "exact_pct": round(100 * exact / max(len(wallets), 1), 4)}
            print(f"[{lb}] {v:28s}: khop so nguyen {exact:,}/{len(wallets):,} ({res_lb[v]['exact_pct']}%) | "
                  f"lech dung 1 don vi: {off1:,} | lech khac: {len(wallets) - exact - off1:,} | thap hon Aave: {lower:,}")
        best = max(variants, key=lambda v: res_lb[v]["exact_match"])
        miss = [w for w in wallets if tot[best].get(w, 0) != aave_int[w]]
        cnt = {}
        for w in miss:
            for s_ in debt_tokens.get(w, ()):
                cnt[s_] = cnt.get(s_, 0) + 1
        top = sorted(cnt.items(), key=lambda kv: -kv[1])[:5]
        res_lb["best_variant"] = best
        res_lb["top_tokens_among_mismatched_wallets"] = top
        print(f"[{lb}] quy tac khop tot nhat: {best} | token no hay gap o cac vi con lech: {top}")
        out.setdefault(lb, {})["part2"] = res_lb


def main():
    cfg = list(json.loads((ROOT / "config/snapshots.json").read_text(encoding="utf-8"))["snapshots"])
    r = pd.read_parquet(P / "hf_reconciliation.parquet")
    out = {}
    part1(r, out)
    part2(cfg, out)
    (P / "dust_diagnosis.json").write_text(json.dumps(out, indent=2, ensure_ascii=False, default=str) + "\n", encoding="utf-8")
    print(f"\nDa luu {P / 'dust_diagnosis.json'}")


if __name__ == "__main__":
    main()