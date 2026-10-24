"""Unit tests for stress scenario pricing and heatmap outputs."""
import pandas as pd
import pytest

from src.models.stress_test import _asset_multiplier, _summary_row, generate_stress_heatmap, run_stress_test


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


def test_zero_shock_preserves_oracle_baseline_hf_exactly() -> None:
    positions = _positions()
    positions["health_factor"] = 9_999.0
    positions["price_usd"] = positions["price_usd_t0"]
    result = run_stress_test(positions, {
        "scenario_id": "oracle_zero", "eth_shock": 0.0, "btc_shock": 0.0, "depeg_shock": 0.0,
    })
    assert result.loc[0, "hf_after"] == result.loc[0, "hf_before"]
    assert result.loc[0, "hf_after"] == 1.6


def test_tiny_eth_shock_changes_hf_smoothly_without_jump() -> None:
    result = run_stress_test(_positions(), {"scenario_id": "tiny", "eth_shock": -1e-12})
    assert result.loc[0, "hf_after"] < result.loc[0, "hf_before"]
    assert result.loc[0, "hf_before"] - result.loc[0, "hf_after"] < 1e-10


def test_tiny_shock_has_no_material_jump_or_spurious_threshold_flip() -> None:
    positions = pd.DataFrame([
        {"snapshot_block": 100, "protocol_id": "aave_v3_ethereum", "wallet_address": "at-one",
         "token_address": "weth-c", "symbol": "WETH", "collateral_amount": 100., "debt_amount": 0.,
         "price_usd_t0": 1., "is_collateral_enabled": True, "effective_lt": .8},
        {"snapshot_block": 100, "protocol_id": "aave_v3_ethereum", "wallet_address": "at-one",
         "token_address": "weth-d", "symbol": "WETH", "collateral_amount": 0., "debt_amount": 80.,
         "price_usd_t0": 1., "is_collateral_enabled": False, "effective_lt": 0.},
        {"snapshot_block": 100, "protocol_id": "aave_v3_ethereum", "wallet_address": "near-one",
         "token_address": "weth-c2", "symbol": "WETH", "collateral_amount": 100., "debt_amount": 0.,
         "price_usd_t0": 1., "is_collateral_enabled": True, "effective_lt": .8},
        {"snapshot_block": 100, "protocol_id": "aave_v3_ethereum", "wallet_address": "near-one",
         "token_address": "usdc-d", "symbol": "USDC", "collateral_amount": 0., "debt_amount": 79.9,
         "price_usd_t0": 1., "is_collateral_enabled": False, "effective_lt": 0.},
    ])
    result = run_stress_test(positions, {"scenario_id": "tiny", "eth_shock": -1e-12}).set_index("wallet")
    delta = (result["hf_after"] - result["hf_before"]).abs()
    assert delta.max() < .005
    # At HF=1, collateral and debt use the same shocked WETH price, so the
    # tiny shock must not create a rounding-only classification flip.
    assert result.loc["at-one", "hf_before"] == 1.0
    assert result.loc["at-one", "hf_after"] == 1.0
    assert (result["hf_before"] < 1.0).equals(result["hf_after"] < 1.0)


def test_lst_specific_depeg_overrides_general_depeg() -> None:
    assert _asset_multiplier("STETH", {"depeg_shock": -0.02, "wsteth_depeg": -0.07}) == pytest.approx(0.98)
    assert _asset_multiplier("WSTETH", {"depeg_shock": -0.02, "wsteth_depeg": -0.07}) == pytest.approx(0.93)
    assert _asset_multiplier("STETH", {"depeg_shock": -0.02, "asset_shocks": {"STETH": -0.05}}) == pytest.approx(0.95)


def test_missing_price_for_active_balance_is_rejected() -> None:
    positions = _positions()
    positions.loc[0, "price_usd_t0"] = None
    positions.loc[0, "collateral_usd_t0"] = None
    with pytest.raises(ValueError, match="Price is missing or non-positive"):
        run_stress_test(positions, {"scenario_id": "missing_price"})


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
    second_snapshot = _positions().assign(snapshot_block=200)
    positions = pd.concat([_positions(), second_snapshot], ignore_index=True)
    result = generate_stress_heatmap(positions, [0.0, -0.2], [0.0, -0.05, -0.1])
    required = {
        "snapshot_block", "eth_shock", "depeg_shock", "debt_at_risk_usd", "positions_at_risk_count"
    }
    assert required.issubset(result.columns)
    assert len(result) == 12
    assert set(result["snapshot_block"]) == {100, 200}


def test_scenario_summary_keeps_snapshot_debt_separate() -> None:
    second_snapshot = _positions().assign(snapshot_block=200)
    result = run_stress_test(
        pd.concat([_positions(), second_snapshot], ignore_index=True),
        {"scenario_id": "eth_drop_40", "eth_shock": -0.40},
    )
    summaries = [
        _summary_row("eth_drop_40", {"eth_shock": -0.40}, snapshot)
        for _, snapshot in result.groupby("snapshot_block", sort=True)
    ]
    assert [row["snapshot_block"] for row in summaries] == [100, 200]
    assert [row["debt_at_risk_usd"] for row in summaries] == [50_000.0, 50_000.0]
