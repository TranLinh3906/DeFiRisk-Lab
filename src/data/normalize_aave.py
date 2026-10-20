"""
normalize_aave.py - Xuat bang STAGING cua N2 theo PCCV: borrower_positions.parquet + coverage_report.md.

Chay tu thu muc goc repo (sau fetch_positions.py; reconcile_protocol_totals.py la tuy chon):
    python src/data/normalize_aave.py

Dau vao : positions_long.parquet, hf_reconciliation.parquet, account_reference.parquet,
          borrower_candidates.parquet, reserve_config.parquet, snapshot_metadata.json,
          protocol_reconciliation.parquet (neu co)
Dau ra  : data/processed/aave/borrower_positions.parquet   (staging; KHONG phai bang canonical)
          data/processed/aave/normalization_quality.json
          data/processed/aave/coverage_report.md

KHONG ghi truc tiep vao 6 bang canonical (dim_asset_config, fact_wallet_positions, ...): cac bang do co cot
NOT NULL (asset_class, DEX, lambda, gamma, ...) do N1/N3/N5/N4 cung cap va di qua schema_storage_safe.py.
Cac gia tri chua co duoc de NULL o day, khong dien so gia.
Khong tao co 'bui' (dust): nguong do N1/N4 quyet dinh.
CHUA duoc chay thu o phia nguoi viet (chi kiem tra cu phap).
"""
import json
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path.cwd()
P = ROOT / "data/processed/aave"


def main():
    pos = pd.read_parquet(P / "positions_long.parquet")
    rec = pd.read_parquet(P / "hf_reconciliation.parquet")
    acct = pd.read_parquet(P / "account_reference.parquet")
    cand = pd.read_parquet(P / "borrower_candidates.parquet")
    cfg = pd.read_parquet(P / "reserve_config.parquet")
    meta = json.loads((P / "snapshot_metadata.json").read_text(encoding="utf-8"))
    pr_path = P / "protocol_reconciliation.parquet"
    prot = pd.read_parquet(pr_path) if pr_path.exists() else None

    # ---------- borrower_positions (staging) ----------
    dec = pos["decimals"].astype(float)
    sc = 10.0 ** dec
    bp = pd.DataFrame({
        "snapshot_id": pos.snapshot_id,
        "snapshot_block": pos.snapshot_block,
        "protocol_id": "aave_v3_ethereum",
        "wallet_address": pos.wallet_address,
        "token_address": pos.token_address,
        "token_symbol": pos.symbol,
        "supplied_amount_norm": pos.collateral_amount,                      # aToken balance (da gom lai)
        "collateral_amount": np.where(pos.coll_flag, pos.collateral_amount, 0.0),  # PDF: chi tinh neu bat collateral
        "debt_amount": pos.debt_amount,
        "variable_debt_amount_norm": pos.variable_debt_raw.map(int).astype(float) / sc,
        "stable_debt_amount_norm": pos.stable_debt_raw.map(int).astype(float) / sc,
        "collateral_enabled": pos.coll_flag,
        "emode_category": pos.user_emode.astype(int),
        "price_usd_t0": pos.price_usd,
        "supplied_usd_t0": pos.collateral_usd,
        "collateral_usd_t0": np.where(pos.counted_collateral, pos.collateral_usd, 0.0),
        "debt_usd_t0": pos.debt_usd,
        "lt_std": pos.lt_std / 1e4,
        "effective_lt": pos.lt_eff / 1e4,
        "counted_as_collateral": pos.counted_collateral,
    })
    ceil = cfg.set_index(["snapshot_id", "token_address"])["debt_ceiling_raw_2dec"]
    key = list(zip(bp.snapshot_id, bp.token_address))
    bp["reserve_debt_ceiling_raw_2dec"] = [ceil.get(k) for k in key]
    # Isolation mode: collateral dang bat cua reserve co debt ceiling > 0 (theo huong dan N2 cua nhom)
    bp["is_isolated_collateral"] = bp.counted_as_collateral & (bp.reserve_debt_ceiling_raw_2dec.fillna(0) > 0)
    # Cac cot chua co nguon: de NULL, khong dien gia
    bp["effective_liq_bonus"] = np.nan  # can anh xa bonus E-Mode theo category (N1/N4 chot quy tac)

    dup = int(bp.duplicated(["snapshot_block", "protocol_id", "wallet_address", "token_address"]).sum())
    quality = {
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "rows": len(bp), "duplicate_pk": dup,
        "wallets_by_snapshot": bp.groupby("snapshot_id").wallet_address.nunique().to_dict(),
        "null_price_rows": int(bp.price_usd_t0.isna().sum()),
        "negative_amounts": int(((bp.supplied_amount_norm < 0) | (bp.debt_amount < 0)).sum()),
        "rows_collateral_enabled_but_lt_zero": int((bp.collateral_enabled & (bp.effective_lt <= 0)).sum()),
        "isolated_collateral_rows": int(bp.is_isolated_collateral.sum()),
        "note": "staging; effective_liq_bonus de NULL; khong tao co dust",
    }
    if dup:
        raise SystemExit(f"Trung khoa chinh: {dup} dong. Dung lai.")
    bp.to_parquet(P / "borrower_positions.parquet", index=False)
    (P / "normalization_quality.json").write_text(json.dumps(quality, indent=2), encoding="utf-8")
    print(f"borrower_positions.parquet: {len(bp)} dong | trung khoa: {dup}")

    # ---------- coverage_report.md ----------
    L = ["# Coverage report - Aave V3 Ethereum (N2)", "",
         f"_Tao tu dong: {quality['created_utc']}_", "",
         "## Pham vi (doc truoc)", "",
         "- Day la **tap vi ung vien** lap tu su kien `Borrow` (vi tung phat sinh no), **khong phai danh sach toan bo nguoi vay** "
         "va khong chung minh bao phu 100% Aave.",
         "- Chi cac vi con no (`has_debt`) tai block snapshot duoc lay so du theo token.",
         "- Khong bat duoc no mo bang flash loan co interestRateMode != 0.",
         "- Bang `borrower_positions.parquet` la **staging**, chua phai bang canonical.", ""]
    L += ["- Pham vi giao thuc: **Aave V3 Ethereum Core Market** (Pool `0x87870bca...`). Khong gom cac market Aave khac "
          "(vi du Prime, EtherFi) va cac chain khac."]
    if prot is not None:
        cov = {lb: g.wallet_debt_usd.sum() / g.protocol_debt_usd.sum() for lb, g in prot.groupby("snapshot_id")}
        L += [f"- Do phu **no** so voi totalSupply debt token on-chain cua Pool nay: A = {cov['A']:.4%}, B = {cov['B']:.4%} "
              "(chi do tren NO; vi chi gui tien khong vay nam ngoai pham vi)."]
    L += [""]
    for label in ("A", "B"):
        s = meta["snapshots"][label]
        c = cand[cand[f"in_candidate_{label}"]]
        a = acct[acct.snapshot_id == label]
        r = rec[rec.snapshot_id == label]
        v = r.dropna(subset=["hf_abs_err"])
        lo = v[v.health_factor_reported <= 10]
        L += [f"## Snapshot {label}", "",
              f"- Block `{s['snapshot_block']}` | UTC `{s['snapshot_timestamp_utc']}` | hash `{s['block_hash']}`",
              f"- Vi ung vien (tung co Borrow event den block): **{len(c):,}**",
              f"- Vi goi duoc `getUserAccountData`: {int(a.call_ok.sum()):,}/{len(a):,}",
              f"- Vi con no tai snapshot: **{int(a.has_debt.sum()):,}** | tong no theo Aave: "
              f"{a.loc[a.has_debt, 'total_debt_base_usd'].sum():,.0f} USD",
              f"- Vi HF<1 (Aave bao): {int((r.health_factor_reported < 1).sum())} | tong no cua nhom nay: "
              f"{r.loc[r.health_factor_reported < 1, 'total_debt_base_usd'].sum():,.2f} USD",
              f"- Doi chieu HF tu tinh vs Aave ({len(v):,} vi): sai so tuyet doi trung vi {v.hf_abs_err.median():.6f}; "
              f"vi HF<=10 ({len(lo):,}): p99 {lo.hf_abs_err.quantile(.99):.6f}; sai so tuong doi trung vi {v.hf_rel_err.median():.2e}",
              f"- Vi lech no >=1%: {int((r.debt_rel_err >= 0.01).sum())}; vi lech collateral >=1%: {int((r.coll_rel_err >= 0.01).sum())} "
              "(xem ghi chu: cac vi lech thuong co gia tri rat nho)"]
        if prot is not None:
            g = prot[prot.snapshot_id == label]
            tp, tw = g.protocol_debt_usd.sum(), g.wallet_debt_usd.sum()
            L.append(f"- Tong no tap vi so voi totalSupply debt token cua giao thuc: {tw:,.0f} / {tp:,.0f} USD = **{tw / tp:.4%}**")
        L.append("")
    L += ["## Ghi chu va gioi han", "",
          "- So lech HF/no/collateral >=1% duoc xem la lien quan vi the rat nho (no bui) khi doi chieu tren kenh tong; "
          "can xac nhan lai bang so lieu tong cua nhom neu dua vao bao cao.",
          "- Tieu chi doi chieu 1% voi giao thuc chi ap dung cho **tong no**; collateral cua nguoi vay khong the bang TVL "
          "(aToken gom ca nguoi chi gui tien).",
          "- Chua tai tao HF cho isolation mode/siloed borrowing mot cach rieng; co `is_isolated_collateral` o muc dong.",
          "- Nguong 'no bui' do N1/N4 quyet dinh; du lieu giu nguyen."]
    (P / "coverage_report.md").write_text("\n".join(L) + "\n", encoding="utf-8")
    print(f"Da luu {P / 'borrower_positions.parquet'}, normalization_quality.json, coverage_report.md")


if __name__ == "__main__":
    main()