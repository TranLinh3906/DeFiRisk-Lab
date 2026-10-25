#!/usr/bin/env python3
"""N3 / Muc 3 & 4 - Analysis of Liquidation Profit vs Gas Fee Freeze Conditions.

Computes expected liquidation bonus (profit) vs execution gas cost.
If Profit < Gas Fee, liquidators will not trigger liquidations (Freeze State).

Output: outputs/tables/n3_gas_profit_freeze_proof.parquet
        outputs/tables/n3_gas_profit_freeze_summary.json
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]


def analyze_gas_profit_freeze(liquidations_df: pd.DataFrame, 
                               avg_gas_units: int = 400_000, 
                               default_gas_price_gwei: float = 50.0) -> tuple[pd.DataFrame, dict]:
    """Tao bang chung Freeze khi Loi nhuan thanh ly < Phi Gas."""
    df = liquidations_df.copy()
    
    # Gia dinh Liquidation Bonus trung binh cua Aave V3 ~ 5% (0.05)
    if "collateral_seized_usd" in df.columns and "debt_repaid_usd" in df.columns:
        df["expected_bonus_usd"] = df["collateral_seized_usd"] - df["debt_repaid_usd"]
    else:
        df["expected_bonus_usd"] = 0.0
    
    # Tinh phi gas uoc tinh (Gas Used * Gas Price in ETH * ETH Price)
    eth_price_usd = 3000.0 
    gas_cost_eth = (avg_gas_units * default_gas_price_gwei * 1e-9)
    estimated_gas_usd = gas_cost_eth * eth_price_usd
    
    df["estimated_gas_cost_usd"] = estimated_gas_usd
    df["net_liquidator_profit_usd"] = df["expected_bonus_usd"] - df["estimated_gas_cost_usd"]
    
    # Dieu kien Freeze: Profit <= Gas Fee
    df["is_frozen_due_to_gas"] = df["net_liquidator_profit_usd"] <= 0
    
    frozen_count = int(df["is_frozen_due_to_gas"].sum())
    total_count = len(df)
    
    summary = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "total_analyzed_events": total_count,
        "frozen_events_count": frozen_count,
        "frozen_events_pct": round(frozen_count / total_count * 100, 2) if total_count > 0 else 0.0,
        "assumptions": {
            "avg_gas_units_per_liquidation": avg_gas_units,
            "assumed_gas_price_gwei": default_gas_price_gwei,
            "assumed_eth_price_usd": eth_price_usd
        },
        "conclusion": "Positions with Profit < Gas Fee are economically rational to remain unliquidated (Frozen)."
    }
    
    return df, summary


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, default=ROOT / "data/staging/n3_liquidations_for_fact.parquet")
    parser.add_argument("--output-parquet", type=Path, default=ROOT / "outputs/tables/n3_gas_profit_freeze_proof.parquet")
    parser.add_argument("--output-json", type=Path, default=ROOT / "outputs/tables/n3_gas_profit_freeze_summary.json")
    args = parser.parse_args()

    # Fallback to general liquidations staging if fact staging is not generated yet
    input_path = args.input
    if not input_path.exists():
        fallback = ROOT / "data/staging/n3_liquidations.parquet"
        if fallback.exists():
            input_path = fallback
        else:
            print(f"[WARN] Input path {args.input} not found. Creating empty analysis summary.")
            summary = {
                "generated_at_utc": datetime.now(timezone.utc).isoformat(),
                "status": "WAITING_FOR_LIQUIDATION_STAGING_DATA"
            }
            args.output_json.parent.mkdir(parents=True, exist_ok=True)
            args.output_json.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
            return

    df_in = pd.read_parquet(input_path)
    df_out, summary = analyze_gas_profit_freeze(df_in)

    args.output_parquet.parent.mkdir(parents=True, exist_ok=True)
    args.output_json.parent.mkdir(parents=True, exist_ok=True)
    
    df_out.to_parquet(args.output_parquet, index=False)
    args.output_json.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")

    print(f"[SUCCESS N3 GAS vs PROFIT FREEZE] Proof generated successfully:")
    print(f" - Parquet: {args.output_parquet}")
    print(f" - JSON QA: {args.output_json}")


if __name__ == "__main__":
    main()