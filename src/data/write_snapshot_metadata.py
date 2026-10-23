"""
write_snapshot_metadata.py - Tao data/processed/aave/snapshot_metadata.json cho cac snapshot trong config/snapshots.json
(A, B1, B2) theo cau truc nhom truong yeu cau. status va limitations duoc TINH TU DU LIEU da co, khong nhap tay.

Chay tu thu muc goc repo (sau khi da chay xong chuoi fetch + reconcile_protocol_totals.py):
    python src/data/write_snapshot_metadata.py

Dau vao : config/snapshots.json, data/raw/aave/state_check.json, data/raw/aave/emode_probe.json (neu co),
          data/processed/aave/{oracle_prices,reserve_config,positions_long,hf_reconciliation,
          account_reference,borrower_candidates,protocol_reconciliation}.parquet
Dau ra  : data/processed/aave/snapshot_metadata.json

Giu cac khoa N3 dang doc: chain_id, protocol_id, snapshots.<id>.{snapshot_block, snapshot_timestamp_utc, block_hash,
block_hash_verified_on_chain}, status.archive_eth_call_verified.
Khong chua RPC URL. CHUA duoc chay thu o phia nguoi viet (chi kiem tra cu phap).
"""
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

ROOT = Path.cwd()
P = ROOT / "data/processed/aave"
CFG = ROOT / "config/snapshots.json"
STATE = ROOT / "data/raw/aave/state_check.json"
PROBE = ROOT / "data/raw/aave/emode_probe.json"
OUT = P / "snapshot_metadata.json"

ROLES = {  # theo nhom truong
    "A": "calibration / counterfactual stress test",
    "B1": "out-of-sample validation 1 (Yen Carry Trade)",
    "B2": "out-of-sample validation 2 (10/10 Meltdown)",
}


def pq(name):
    p = P / name
    return pd.read_parquet(p) if p.exists() else None


def has_all(df, labels):
    return df is not None and set(labels) <= set(df.snapshot_id)


def iso_plus(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00")).isoformat()


def main():
    cfg = json.loads(CFG.read_text(encoding="utf-8"))["snapshots"]
    state = json.loads(STATE.read_text(encoding="utf-8"))
    labels = list(cfg)
    for lb in labels:
        if not cfg[lb].get("block"):
            sys.exit(f"config/snapshots.json chua co block cho {lb}; chay find_snapshot_blocks.py.")
        if lb not in state["snapshots"]:
            sys.exit(f"state_check.json chua co {lb}; chay check_state.py.")
        if int(state["snapshots"][lb]["block"]) != int(cfg[lb]["block"]):
            sys.exit(f"{lb}: block trong state_check.json khac config. Chay lai check_state.py.")

    oracle, rescfg = pq("oracle_prices.parquet"), pq("reserve_config.parquet")
    pos, rec = pq("positions_long.parquet"), pq("hf_reconciliation.parquet")
    acct, cand = pq("account_reference.parquet"), pq("borrower_candidates.parquet")
    prot = pq("protocol_reconciliation.parquet")
    probe = json.loads(PROBE.read_text(encoding="utf-8")) if PROBE.exists() else {}

    # ---------- snapshots ----------
    snaps = {}
    for lb in labels:
        c, s = cfg[lb], state["snapshots"][lb]
        ts = c.get("block_timestamp_utc") or datetime.fromtimestamp(
            s["block_timestamp_utc"], tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        snaps[lb] = {
            "snapshot_id": lb,
            "snapshot_block": int(c["block"]),
            "snapshot_timestamp_utc": iso_plus(ts),
            "role": ROLES.get(lb, c.get("role")),
            "target_timestamp_utc": iso_plus(c["target_utc"]),
            "block_hash": c["block_hash"],
            "block_hash_verified_on_chain": s["checks"].get("block_hash_match"),
            "leader_approx_block": c.get("leader_approx_block"),
            "aave_oracle": s["aave_oracle"],
            "pool_data_provider": s["pool_data_provider"],
            "addresses_provider": s["addresses_provider"],
            "n_reserves": s["checks"]["n_reserves"],
        }

    rules = {}
    for lb in labels:
        if cfg[lb].get("window_end_exclusive_utc"):
            rules[lb] = (f"event_block > {int(cfg[lb]['block'])} AND "
                         f"event_timestamp < {cfg[lb]['window_end_exclusive_utc']}")

    # ---------- status (tinh tu du lieu) ----------
    hf_ok = rec is not None and has_all(rec, labels) and all(
        rec[rec.snapshot_id == lb].hf_abs_err.median() < 0.005 for lb in labels)
    prot_ok = False
    if has_all(prot, labels):
        ratios = {lb: prot[prot.snapshot_id == lb].wallet_debt_usd.sum() / prot[prot.snapshot_id == lb].protocol_debt_usd.sum()
                  for lb in labels}
        prot_ok = all(0.99 <= r <= 1.01 for r in ratios.values())
    status = {
        "archive_eth_call_verified": all(bool(cfg[lb].get("archive_state_readable")) for lb in labels),
        "oracle_prices_exported": has_all(oracle, labels),
        "reserve_config_exported": has_all(rescfg, labels),
        "positions_exported": has_all(pos, labels),
        "hf_validated": bool(hf_ok),
        "protocol_debt_reconciled": bool(prot_ok),
    }

    # ---------- limitations (tinh tu du lieu) ----------
    L = [
        "Phạm vi: Aave V3 Ethereum Core Market (Pool 0x87870bca3f3fd6335c3f4ce8392d69350b4fa4e2). Không gồm các market Aave khác "
        "(ví dụ Prime, EtherFi) và các chain khác.",
    ]
    if cand is not None and acct is not None:
        first = int(cand.first_borrow_block.min())
        parts = []
        for lb in labels:
            nc = int(cand[f"in_candidate_{lb}"].sum()) if f"in_candidate_{lb}" in cand.columns else 0
            a = acct[acct.snapshot_id == lb]
            parts.append(f"{lb}: {nc:,} ví ứng viên, {int(a.has_debt.sum()):,} ví còn nợ")
        L.append("Tập ví: lập từ sự kiện Borrow (quét từ block 16,200,000; Borrow đầu tiên ở block "
                 f"{first:,}) đến block snapshot, rồi lọc ví còn nợ bằng getUserAccountData. " + "; ".join(parts) +
                 ". Ví chỉ gửi tiền, không vay, nằm ngoài phạm vi.")
    if prot is not None:
        parts = []
        for lb in labels:
            g = prot[prot.snapshot_id == lb]
            if len(g):
                parts.append(f"{lb}: {g.wallet_debt_usd.sum() / g.protocol_debt_usd.sum():.4%} "
                             f"(tổng nợ giao thức {g.protocol_debt_usd.sum():,.0f} USD)")
        L.append("Độ phủ nợ so với tổng totalSupply debt token on-chain của Pool: " + "; ".join(parts) +
                 ". Chỉ đo cho nợ, tại các snapshot này, trong Core Market; không có tiêu chí tương đương cho collateral/TVL "
                 "vì aToken gồm cả người chỉ gửi tiền.")
    L.append("Phương pháp lập tập ví không bắt trực tiếp nợ mở bằng flash loan (interestRateMode khác 0) hay cơ chế không qua "
             "sự kiện Borrow. Độ phủ đo được ở các snapshot trên không đảm bảo cho block khác.")
    if rec is not None:
        parts, bad_parts, hf1_parts = [], [], []
        for lb in labels:
            r = rec[rec.snapshot_id == lb]
            if r.empty:
                continue
            v = r.dropna(subset=["hf_abs_err"])
            lo = v[v.health_factor_reported <= 10]
            parts.append(f"{lb}: sai số tuyệt đối trung vị {v.hf_abs_err.median():.6f}, p99 (ví HF≤10) "
                         f"{lo.hf_abs_err.quantile(.99):.4f}, sai số tương đối trung vị {v.hf_rel_err.median():.1e}")
            bad = r[r.debt_rel_err >= 0.01]
            flip = r[(r.health_factor_reported < 1) != (r.hf_calc < 1)]
            tot = r.total_debt_base_usd.sum()
            bad_parts.append(f"{lb}: {len(bad)} ví lệch nợ ≥1% (nợ Aave của nhóm {bad.total_debt_base_usd.sum():,.2f} USD = "
                             f"{bad.total_debt_base_usd.sum() / tot:.6%} tổng nợ; {int((bad.total_debt_base_usd >= 100).sum())} ví "
                             f"có nợ ≥100 USD), {int((r.coll_rel_err >= 0.01).sum())} ví lệch collateral ≥1%, "
                             f"{len(flip)} ví lệch ngưỡng HF<1")
            low = r[r.health_factor_reported < 1]
            hf1_parts.append(f"{lb}: {len(low)} ví (tổng nợ {low.total_debt_base_usd.sum():,.2f} USD)")
        L.append("Đối chiếu HF tự tính với HF Aave báo trên toàn bộ ví còn nợ trong tập ứng viên: " + "; ".join(parts) +
                 ". Đây là kiểm tra nội bộ của N2; kiểm định độc lập thuộc N5. "
                 "status.hf_validated = true khi sai số tuyệt đối trung vị < 0.005 ở mọi snapshot (tiêu chí của research_design.md); "
                 "p99 có thể vượt 0.005 do các vị thế rất nhỏ (xem dòng sai lệch ≥1% bên dưới).")
        L.append("Sai lệch từ 1% trở lên: " + "; ".join(bad_parts) + ". Nguyên nhân dự đoán là làm tròn đơn vị USD 8 chữ số "
                 "của Aave ở vị thế rất nhỏ; chưa kiểm tra từng ví.")
        L.append("Ví có HF<1 (Aave báo): " + "; ".join(hf1_parts) + ". Ngưỡng nợ bụi do N1/N4 quyết định; dữ liệu giữ "
                 "nguyên, chưa gắn cờ dust.")
    if pos is not None and "variable_debt_scaled_raw" in pos.columns:
        present = [lb for lb in labels if lb in set(pos.snapshot_id)]
        no_scaled = [lb for lb in present if pos[pos.snapshot_id == lb].variable_debt_scaled_raw.isna().all()]
        with_scaled = [lb for lb in present if lb not in no_scaled]
        txt = ("Cách tính nợ biến đổi: " + (", ".join(with_scaled) if with_scaled else "—") +
               " dùng scaled balance × chỉ số nợ chuẩn hóa (cách Aave dùng khi tính HF)")
        if no_scaled:
            txt += (f"; {', '.join(no_scaled)} dùng balanceOf của debt token (checkpoint cũ không lưu scaled balance), "
                    "cần thống nhất phương pháp nếu so sánh trực tiếp giữa các snapshot")
        txt += (". Lý do: với B1, tính bằng balanceOf làm lệch nợ của ví vay GHO (nợ tự tính thấp hơn nợ Aave) và p99 sai số HF "
                "của ví HF≤10 là 0.0128 trên mẫu 200 ví; sau khi chuyển sang scaled balance p99 giảm còn khoảng 0.0004. "
                "Nguyên nhân dự đoán là chiết khấu lãi suất của GHO, chưa kiểm chứng trực tiếp.")
        L.append(txt)
    cats = {lb: len(probe[lb]["categories"]) for lb in labels if lb in probe}
    L.append("E-Mode: cơ chế đọc khác nhau theo phiên bản Aave tại từng block. "
             + "; ".join(f"{lb}: {n} category" for lb, n in cats.items())
             + ". Snapshot dùng E-Mode kiểu cũ (mỗi reserve thuộc đúng một category, tra bằng getReserveEModeCategory) hoặc bitmap "
             "tùy phiên bản; không dùng chung một bảng E-Mode cho các snapshot. Cột effective_liq_bonus chưa điền, "
             "chờ quy tắc từ N1/N4.")
    L.append("Số lượng reserve theo snapshot: " + "; ".join(f"{lb}: {snaps[lb]['n_reserves']}" for lb in labels) +
             ". PoolDataProvider và phiên bản Aave khác nhau giữa các block, nên so sánh giữa snapshot cần ghi chú.")
    L.append("Isolation mode và siloed borrowing chưa được mô hình hóa riêng; chỉ có cờ is_isolated_collateral suy từ debt "
             "ceiling khác 0.")
    L.append("Giá dùng để tính HF là giá oracle Aave tại block (USD, 8 chữ số thập phân), không phải giá thị trường CoinGecko của N3.")
    approx = {lb: cfg[lb].get("leader_approx_block") for lb in labels if cfg[lb].get("leader_approx_block")}
    if approx:
        L.append("Block snapshot là block cuối cùng có timestamp không muộn hơn mốc mục tiêu. Số block 'khoảng' ban đầu "
                 + ", ".join(f"{lb}: {v}" for lb, v in approx.items()) + " được thay bằng block tìm theo timestamp ("
                 + ", ".join(f"{lb}: {snaps[lb]['snapshot_block']}" for lb in approx)
                 + "); các bước lọc sự kiện thanh lý (event_block >) phải dùng block mới.")
    L.append("Chưa có bằng chứng riêng cho việc Snapshot A là giai đoạn bình thường.")
    L.append("borrower_positions.parquet là bảng staging, chưa phải bảng canonical của nhóm.")

    meta = {
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "chain_id": 1,
        "protocol_id": "aave_v3_ethereum",
        "pool_address": state["pool"].lower(),
        "rpc_provider": "Alchemy (Ethereum mainnet); URL không lưu trong repo",
        "snapshots": snaps,
        "validation_window_rule": rules,
        "status": status,
        "limitations": L,
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(meta, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"Da luu {OUT}")
    print("status:", json.dumps(status))
    for lb in labels:
        s = snaps[lb]
        print(f"  {lb}: block {s['snapshot_block']} | {s['snapshot_timestamp_utc']} | hash verified={s['block_hash_verified_on_chain']}")
    print(f"limitations: {len(L)} muc")


if __name__ == "__main__":
    main()