"""Unit tests for stress scenario pricing and heatmap outputs."""
import pandas as pd

from src.models.stress_test import generate_stress_heatmap, run_stress_test


def _positions() -> pd.DataFrame:
    return pd.DataFrame([
        {
            "snapshot_block": 100,
            "protocol_id": "aave_v3_ethereum",
            "wallet_address": "0xabc",
            "token_address": "0xlst",
            "token_symbol": "wstETH",
            "collateral_amount": 100.0,
            "debt_amount": 0.0,
            "collateral_enabled": True,
            "price_usd_t0": 1_000.0,
            "effective_lt": 0.80,
        },
        {
            "snapshot_block": 100,
            "protocol_id": "aave_v3_ethereum",
            "wallet_address": "0xabc",
            "token_address": "0xusdc",
            "token_symbol": "USDC",
            "collateral_amount": 0.0,
            "debt_amount": 50_000.0,
            "collateral_enabled": False,
            "price_usd_t0": 1.0,
            "effective_lt": 0.0,
        },
    ])


def test_zero_shock_preserves_health_factor() -> None:
    result = run_stress_test(_positions(), {
        "scenario_id": "zero", "eth_shock": 0.0, "btc_shock": 0.0, "depeg_shock": 0.0,
    })
    assert result.loc[0, "hf_after"] == result.loc[0, "hf_before"]
    assert result.loc[0, "hf_before"] == 1.6


def test_eth_and_lst_depeg_shocks_reduce_hf_and_increase_debt_at_risk() -> None:
    positions = _positions()
    baseline = run_stress_test(positions, {"scenario_id": "base"})
    stressed = run_stress_test(positions, {
        "scenario_id": "eth_down_lst_depeg", "eth_shock": -0.40, "depeg_shock": -0.10,
    })
    assert stressed.loc[0, "hf_after"] < baseline.loc[0, "hf_after"]
    assert stressed.loc[0, "debt_at_risk_usd"] > baseline.loc[0, "debt_at_risk_usd"]
    assert stressed.loc[0, "debt_at_risk_usd"] == 50_000.0


def test_heatmap_has_required_schema_and_grid_size() -> None:
    result = generate_stress_heatmap(_positions(), [0.0, -0.2], [0.0, -0.05, -0.1])
    required = {"eth_shock", "depeg_shock", "debt_at_risk_usd", "positions_at_risk_count"}
    assert required.issubset(result.columns)
    assert len(result) == 6
