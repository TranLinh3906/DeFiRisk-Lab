"""Focused unit tests for the cliff and reverse-stress functions."""
import pandas as pd

from src.models.cliff_reverse import find_liquidation_cliff, run_reverse_stress


def _toy_positions() -> pd.DataFrame:
    """One ETH-collateral wallet with a stablecoin debt."""
    return pd.DataFrame([
        {
            "snapshot_block": 1,
            "protocol_id": "aave_v3_ethereum",
            "wallet_address": "0xabc",
            "token_address": "0xeth",
            "token_symbol": "WETH",
            "is_collateral_enabled": True,
            "collateral_amount": 100.0,
            "debt_amount": 0.0,
            "price_usd_t0": 1_000.0,
            "effective_lt": 0.8,
        },
        {
            "snapshot_block": 1,
            "protocol_id": "aave_v3_ethereum",
            "wallet_address": "0xabc",
            "token_address": "0xusd",
            "token_symbol": "USDC",
            "is_collateral_enabled": False,
            "collateral_amount": 0.0,
            "debt_amount": 50_000.0,
            "price_usd_t0": 1.0,
            "effective_lt": 0.0,
        },
    ])


def test_cliff_curve_starts_at_baseline_and_marks_liquidation_risk() -> None:
    curve = find_liquidation_cliff(_toy_positions(), max_drop=0.60, step=0.01)
    assert curve.iloc[0]["debt_at_risk_usd"] == 0.0
    assert curve.iloc[0]["positions_at_risk"] == 0
    assert curve.iloc[-1]["debt_at_risk_usd"] == 50_000.0
    assert curve.iloc[-1]["positions_at_risk"] == 1
    assert curve["marginal_debt_at_risk_usd_per_pct"].notna().any()


def test_reverse_stress_finds_eth_shock_for_usd_and_percentage_targets() -> None:
    result = run_reverse_stress(_toy_positions(), [25_000.0, "50%"])
    assert result["baseline_protocol_debt_usd"] == 50_000.0
    assert result["targets"][0]["eth_shock_required"] == -0.38
    assert result["targets"][1]["target_debt_usd"] == 25_000.0
    assert result["targets"][1]["reached_within_search_grid"]
