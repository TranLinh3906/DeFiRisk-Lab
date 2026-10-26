#!/usr/bin/env python3
"""Fetch the 27 targeted DEX quotes from n3_quote_first_batch_27.csv via Archive RPC.

Preserves all raw integer amounts, block hashes, and append-only JSONL checkpoint proofs.
Outputs to a new directory (default: outputs/n3_priority2_quote_grid_v2_with_27) without
modifying outputs/n3_priority2_quote_grid_v1.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import pandas as pd

from scripts.n3_priority2_multivolume_quotes import BLOCKS, Rpc, quote_one, request_id


def build_targeted_requests(dex_df: pd.DataFrame, batch_df: pd.DataFrame) -> pd.DataFrame:
    keys = ["snapshot_id", "snapshot_block", "pool_address", "token_in_address", "token_out_address"]
    merged = batch_df.merge(dex_df, on=keys, how="left", suffixes=("", "_dex"), validate="many_to_one")
    if merged["dex"].isna().any():
        raise ValueError("Some targeted requests did not match certified DEX pool metadata")
    rows = []
    for r in merged.to_dict("records"):
        rows.append({
            "snapshot_id": str(r["snapshot_id"]),
            "dex": str(r["dex"]),
            "pool_address": str(r["pool_address"]).lower(),
            "token_in_address": str(r["token_in_address"]).lower(),
            "token_out_address": str(r["token_out_address"]).lower(),
            "snapshot_block": int(r["snapshot_block"]),
            "snapshot_block_hash": str(r["snapshot_block_hash"]),
            "token_in": str(r.get("token_in_dex", r["token_in"])),
            "token_out": str(r.get("token_out_dex", r["token_out"])),
            "fee_tier": r.get("fee_tier"),
            "input_token_usd_oracle": float(r["input_token_usd_oracle"]),
            "spot_output_raw_per_input_raw": float(r["spot_output_raw_per_input_raw"]),
            "trade_size_usd": float(r["suggested_size_snapshot_usd"]),
            "reason": str(r["reason"]),
            "priority": int(r["priority"]),
            "status": "NOT_QUOTED",
            "amount_out_raw": None,
            "impact_pct": None,
            "error_code": None,
            "amount_in_raw": None,
            "input_decimals": None,
            "output_decimals": None,
            "verified_block_hash": None,
            "quote_method": None,
        })
    return pd.DataFrame(rows)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dex", type=Path, default=Path("data/staging/n3_dex_liquidity_A_B1_B2.parquet"))
    ap.add_argument("--batch-csv", type=Path, default=Path("outputs/n5_preparation/n3_quote_handoff_20261026/n3_quote_first_batch_27.csv"))
    ap.add_argument("--base-grid-dir", type=Path, default=Path("outputs/n3_priority2_quote_grid_v1"))
    ap.add_argument("--output-dir", type=Path, default=Path("outputs/n3_priority2_quote_grid_v2_with_27"))
    ap.add_argument("--env-file", type=Path, default=Path("N3_Compound_Q3_Overlap_VSCode/.env"))
    ap.add_argument("--execute", action="store_true", help="Execute Archive RPC calls")
    ap.add_argument("--rpc-interval", type=float, default=0.35)
    args = ap.parse_args()

    dex_df = pd.read_parquet(args.dex)
    batch_df = pd.read_csv(args.batch_csv)
    grid = build_targeted_requests(dex_df, batch_df)

    args.output_dir.mkdir(parents=True, exist_ok=True)
    checkpoint = args.output_dir / "n3_targeted_27_verified_quotes_checkpoint.jsonl"
    saved = {}
    if checkpoint.exists():
        for ln in checkpoint.read_text(encoding="utf-8").splitlines():
            if ln.strip():
                d = json.loads(ln)
                if d.get("status") == "RPC_VERIFIED":
                    k = d["request_id"]
                    if k in saved and saved[k] != d:
                        raise SystemExit("Conflicting RPC checkpoint records")
                    saved[k] = d

    if args.execute:
        from dotenv import load_dotenv
        if args.env_file.exists():
            load_dotenv(args.env_file)
        load_dotenv(".env")
        url = os.getenv("ETHEREUM_ARCHIVE_RPC_URL") or os.getenv("ETH_RPC_URL")
        if not url:
            raise SystemExit("Missing ETHEREUM_ARCHIVE_RPC_URL / ETH_RPC_URL")
        import requests
        requests.post(url, json={"jsonrpc": "2.0", "id": 0, "method": "eth_chainId", "params": []}, timeout=5).raise_for_status()
        rpc = Rpc(url, args.rpc_interval)
        with checkpoint.open("a", encoding="utf-8") as f:
            for i, r in grid.iterrows():
                key = request_id(r)
                if key in saved:
                    continue
                try:
                    result = quote_one(r, rpc)
                    item = {
                        "request_id": key,
                        "snapshot_id": r["snapshot_id"],
                        "snapshot_block": int(r["snapshot_block"]),
                        "dex": r["dex"],
                        "pool_address": r["pool_address"],
                        "token_in_address": r["token_in_address"],
                        "token_out_address": r["token_out_address"],
                        "trade_size_usd": float(r["trade_size_usd"]),
                        **result,
                        "verified_utc": datetime.now(timezone.utc).isoformat(),
                    }
                    f.write(json.dumps(item, ensure_ascii=False) + "\n")
                    f.flush()
                    os.fsync(f.fileno())
                    saved[key] = item
                    print(
                        f"[RPC VERIFIED] {i+1}/{len(grid)} {r.snapshot_id} "
                        f"{r.token_in}->{r.token_out} USD={r.trade_size_usd:.2f} "
                        f"impact={result['impact_pct']:.6f}%",
                        flush=True,
                    )
                except RuntimeError as e:
                    print(f"[STOP] {type(e).__name__} {str(e)}; checkpoint retained", flush=True)
                    break

    completed = []
    for r in grid.to_dict("records"):
        item = saved.get(request_id(r))
        if item:
            for k in (
                "status",
                "amount_out_raw",
                "amount_in_raw",
                "input_decimals",
                "output_decimals",
                "impact_pct",
                "verified_block_hash",
                "quote_method",
            ):
                r[k] = item.get(k)
        completed.append(r)

    # Combine with base 84 verified quotes and 24 anchors
    base_verified = pd.read_parquet(args.base_grid_dir / "n3_multivolume_rpc_verified_only.parquet")
    anchors = pd.read_parquet(args.base_grid_dir / "n3_2pct_observed_anchors.parquet")
    common_cols = list(base_verified.columns)

    batch_result = pd.DataFrame(completed)
    batch_verified = batch_result.loc[batch_result.status.eq("RPC_VERIFIED"), common_cols].copy()
    batch_positive = batch_verified.loc[batch_verified["impact_pct"] > 0, common_cols].copy()
    batch_result.to_csv(args.output_dir / "n3_targeted_27_requests_and_status.csv", index=False)
    batch_verified.to_parquet(args.output_dir / "n3_targeted_27_rpc_verified_only.parquet", index=False)
    batch_positive.to_parquet(args.output_dir / "n3_targeted_24_positive_rpc_verified.parquet", index=False)

    combined_verified = pd.concat(
        [base_verified, batch_verified[common_cols]], ignore_index=True
    ).drop_duplicates(["snapshot_id", "pool_address", "token_in_address", "token_out_address", "trade_size_usd"])
    combined_verified = combined_verified.sort_values(
        ["snapshot_id", "pool_address", "token_in_address", "token_out_address", "trade_size_usd"]
    ).reset_index(drop=True)

    anchors.to_parquet(args.output_dir / "n3_2pct_observed_anchors.parquet", index=False)
    combined_verified.to_parquet(args.output_dir / "n3_multivolume_rpc_verified_all_111.parquet", index=False)
    combined_verified.to_csv(args.output_dir / "n3_multivolume_rpc_verified_all_111.csv", index=False)

    # Filter out 3 sub-$160 WETH->USDC quotes where 6-decimal USDC integer floor in the
    # original ~0.001 WETH reference quote causes tiny negative (-0.00005% to -0.00028%) relative impact.
    positive_verified = combined_verified.loc[combined_verified["impact_pct"] > 0].reset_index(drop=True)
    positive_verified.to_parquet(args.output_dir / "n3_multivolume_rpc_verified_only.parquet", index=False)
    positive_verified.to_csv(args.output_dir / "n3_multivolume_rpc_verified_only.csv", index=False)

    status = {
        "base_verified_quotes": int(len(base_verified)),
        "targeted_batch_requested": int(len(grid)),
        "targeted_batch_verified": int(len(batch_verified)),
        "targeted_batch_not_quoted": int(len(grid) - len(batch_verified)),
        "combined_verified_quotes_all": int(len(combined_verified)),
        "combined_verified_quotes_positive_calibration": int(len(positive_verified)),
        "usdc_6dec_quantization_sub160_quotes": int(len(combined_verified) - len(positive_verified)),
        "bracket_anchor_points": int(len(anchors)),
        "checkpoint_file": str(checkpoint),
        "input_dex_sha256": hashlib.sha256(args.dex.read_bytes()).hexdigest(),
        "input_batch_csv_sha256": hashlib.sha256(args.batch_csv.read_bytes()).hexdigest(),
        "created_utc": datetime.now(timezone.utc).isoformat(),
    }
    (args.output_dir / "n3_priority2_quality.json").write_text(
        json.dumps(status, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print("[TARGETED_27_SUMMARY]", json.dumps(status, indent=2))


if __name__ == "__main__":
    main()
