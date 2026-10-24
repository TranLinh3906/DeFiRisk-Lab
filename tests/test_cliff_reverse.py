"""Focused unit tests for the cliff and reverse-stress functions."""
import pandas as pd
import pytest

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
    assert curve.columns[0] == "snapshot_block"
    assert set(curve["snapshot_block"]) == {1}


def test_reverse_stress_finds_eth_shock_for_usd_and_percentage_targets() -> None:
    result = run_reverse_stress(_toy_positions(), [25_000.0, "50%"])
    snapshot = result["snapshots"]["1"]
    assert snapshot["baseline_protocol_debt_usd"] == 50_000.0
    assert snapshot["targets"][0]["eth_shock_required"] == -0.38
    assert snapshot["targets"][1]["target_debt_usd"] == 25_000.0
    assert snapshot["targets"][1]["reached_within_search_grid"]


def test_cliff_and_reverse_stress_keep_snapshots_independent() -> None:
    second = _toy_positions().assign(snapshot_block=2, debt_amount=[0.0, 100_000.0])
    combined = pd.concat([_toy_positions(), second], ignore_index=True)
    curve = find_liquidation_cliff(combined, max_drop=0.0, step=0.01)
    debt = curve.set_index("snapshot_block")["total_protocol_debt_usd"].to_dict()
    assert debt == {1: 50_000.0, 2: 100_000.0}
    reverse = run_reverse_stress(combined, ["50%"])
    assert reverse["snapshots"]["1"]["baseline_protocol_debt_usd"] == 50_000.0
    assert reverse["snapshots"]["2"]["baseline_protocol_debt_usd"] == 100_000.0


@pytest.mark.parametrize("target", [
    -1, "-2%", {"usd": -1}, {"pct": -0.1}, "NaN%",
    {"usd": float("nan")}, {"pct": float("inf")}, float("-inf"),
])
def test_reverse_stress_rejects_negative_targets(target) -> None:
    with pytest.raises(ValueError, match="^Target debt must be a finite, non-negative number$"):
        run_reverse_stress(_toy_positions(), [target])
