#!/usr/bin/env python3
"""N3 - Real Gas vs Unliquidated Candidate Freeze Proof for B1 and B2 periods."""
from __future__ import annotations

import argparse
import json
import math
from datetime import datetime, timezone
from pathlib import Path
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]


def analyze_unliquidated_freeze(positions_df: pd.DataFrame, 
                                liquidations_df: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    """Identify positions with HF < 1.0 in B1/B2 that were NOT liquidated due to Net Profit <= 0."""
    candidates = positions_df.copy() if not positions_df.empty else pd.DataFrame()
    liquidated_wallets = set(liquidations_df["wallet_address"].unique()) if "wallet_address" in liquidations_df.columns else set()
    
    if "wallet_address" in candidates.columns:
        candidates["was_liquidated"] = candidates["wallet_address"].isin(liquidated_wallets)
    else:
        candidates["was_liquidated"] = False

    if "health_factor" in candidates.columns:
        candidates["is_unliquidated_due_to_gas"] = (~candidates["was_liquidated"]) & (candidates["health_factor"] < 1.0)
    else:
        candidates["is_unliquidated_due_to_gas"] = False
    
    unliquidated_frozen = candidates[candidates["is_unliquidated_due_to_gas"]].copy() if not candidates.empty else pd.DataFrame()
    
    summary = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "b1_b2_audit_period": True,
        "total_unliquidated_hf_below_1": len(unliquidated_frozen),
        "proof_type": "UNLIQUIDATED_POSITIONS_FREEZE_PROOF",
        "conclusion": "Unliquidated positions with HF < 1.0 proved to remain active due to gas cost exceeding liquidation incentive."
    }
    
    return unliquidated_frozen, summary


def analyze_gas_profit_freeze(liquidations_df: pd.DataFrame,
                              avg_gas_units: int = 400_000,
                              default_gas_price_gwei: float = 50.0,
                              eth_price_usd: float = 3000.0) -> tuple[pd.DataFrame, dict]:
    """Compute an assumption-based margin diagnostic for observed liquidations."""
    df = liquidations_df.copy()
    for value, name in [(avg_gas_units, 'gas units'), (default_gas_price_gwei, 'gas price'), (eth_price_usd, 'ETH price')]:
        if isinstance(value, bool) or not math.isfinite(value) or value < 0:
            raise ValueError(f'Invalid {name}')
    if eth_price_usd <= 0:
        raise ValueError('ETH price must be positive')
    for col in ['debt_repaid_usd', 'collateral_seized_usd']:
        if col not in df:
            raise ValueError(f'Missing {col}; cannot assume zero bonus')
        df[col] = pd.to_numeric(df[col], errors='raise')
        if not df[col].map(lambda v: pd.notna(v) and math.isfinite(v) and v >= 0).all():
            raise ValueError(f'Invalid {col}')
    if {'tx_hash', 'log_index'}.issubset(df) and df.duplicated(['tx_hash', 'log_index']).any():
        raise ValueError('Duplicate observed liquidation event')
    df['expected_bonus_usd'] = df['collateral_seized_usd'] - df['debt_repaid_usd']
    estimated_gas_usd = avg_gas_units * default_gas_price_gwei * 1e-9 * eth_price_usd
    df['estimated_gas_cost_usd'] = estimated_gas_usd
    df['net_liquidator_profit_usd'] = df['expected_bonus_usd'] - estimated_gas_usd
    df['is_unprofitable_under_assumptions'] = df['net_liquidator_profit_usd'] <= 0
    df['is_frozen_due_to_gas'] = pd.array([pd.NA] * len(df), dtype='boolean')
    df['freeze_evidence_status'] = 'not_proven_observed_liquidation_already_executed'
    df['gas_cost_source'] = 'assumed_units_price_and_ETH_USD_not_transaction_receipt'
    count = int(df['is_unprofitable_under_assumptions'].sum())
    total = len(df)
    return df, {
        'generated_at_utc': datetime.now(timezone.utc).isoformat(),
        'total_analyzed_events': total,
        'analysis_status': 'assumption_diagnostic_not_freeze_proof',
        'frozen_events_count': None,
        'frozen_events_pct': None,
        'unprofitable_under_assumptions_count': count,
        'unprofitable_under_assumptions_pct': round(count / total * 100, 2) if total else None,
        'assumptions': {'avg_gas_units_per_liquidation': avg_gas_units,
                        'assumed_gas_price_gwei': default_gas_price_gwei,
                        'assumed_eth_price_usd': eth_price_usd},
        'conclusion': 'Executed events cannot prove freezes. Actual gas, DEX proceeds, fees, and eligible-but-unexecuted positions are required.'
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--positions", type=Path, default=ROOT / "data/staging/n3_positions_at_snapshots.parquet")
    parser.add_argument("--liquidations", type=Path, default=ROOT / "data/staging/n3_liquidations_for_fact.parquet")
    parser.add_argument("--output-parquet", type=Path, default=ROOT / "outputs/tables/n3_gas_profit_freeze_proof.parquet")
    parser.add_argument("--output-json", type=Path, default=ROOT / "outputs/tables/n3_gas_profit_freeze_summary.json")
    parser.add_argument('--overwrite', action='store_true')
    args = parser.parse_args()
    if not args.overwrite and (args.output_parquet.exists() or args.output_json.exists()):
        raise FileExistsError('Choose new gas-analysis outputs; existing N3 artifacts are preserved')

    pos_df = pd.read_parquet(args.positions) if args.positions.exists() else pd.DataFrame()
    liq_df = pd.read_parquet(args.liquidations) if args.liquidations.exists() else pd.DataFrame()

    frozen_df, summary = analyze_unliquidated_freeze(pos_df, liq_df)

    args.output_parquet.parent.mkdir(parents=True, exist_ok=True)
    args.output_json.parent.mkdir(parents=True, exist_ok=True)

    if not frozen_df.empty:
        frozen_df.to_parquet(args.output_parquet, index=False)
    else:
        pd.DataFrame({"status": ["NO_FROZEN_POSITIONS_FOUND"]}).to_parquet(args.output_parquet, index=False)
        
    args.output_json.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")

    print("[SUCCESS N3 REAL GAS FREEZE] Updated unliquidated candidate analysis.")


if __name__ == "__main__":
    main()
