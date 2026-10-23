"""Unit tests for wallet-level Aave health-factor calculations."""
import numpy as np
import pandas as pd

from src.models.health_factor import _as_bool, calculate_health_factor


def _frames(rows: list[dict]) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    positions = pd.DataFrame(rows)
    config = positions[["snapshot_block", "token_address"]].drop_duplicates().copy()
    config["lt_std"] = 0.80
    config["lt_emode"] = 0.95
    config["emode_category"] = 1
    prices = positions[["snapshot_block", "token_address", "price_usd"]].drop_duplicates().copy()
    return positions, config, prices


def _row(token: str, symbol: str, collateral: float, debt: float, *,
         enabled: bool, user_emode: int = 0, price: float = 1.0) -> dict:
    return {
        "snapshot_block": 100,
        "wallet_address": "0xabc",
        "token_address": token,
        "symbol": symbol,
        "collateral_amount": collateral,
        "debt_amount": debt,
        "is_collateral_enabled": enabled,
        "emode_category_user": user_emode,
        "price_usd": price,
    }


def test_calculates_standard_health_factor_and_two_frame_dashboard_call() -> None:
    positions, config, _ = _frames([
        _row("0xcoll", "DAI", 1_000.0, 0.0, enabled=True, price=1.0),
        _row("0xdebt", "USDC", 0.0, 500.0, enabled=False, price=1.0),
    ])
    result = calculate_health_factor(positions, config)
    assert result.loc[0, "total_collateral_usd"] == 1_000.0
    assert result.loc[0, "total_debt_usd"] == 500.0
    assert result.loc[0, "health_factor"] == 1.6
    assert result.loc[0, "risk_category"] == "Safe"


def test_zero_debt_returns_finite_sentinel_health_factor() -> None:
    positions, config, prices = _frames([
        _row("0xcoll", "DAI", 1_000.0, 0.0, enabled=True),
    ])
    result = calculate_health_factor(positions, config, prices)
    assert result.loc[0, "health_factor"] == 999.0
    assert np.isfinite(result.loc[0, "health_factor"])


def test_no_collateral_with_debt_has_zero_health_factor() -> None:
    positions, config, prices = _frames([
        _row("0xdebt", "USDC", 0.0, 500.0, enabled=False),
    ])
    result = calculate_health_factor(positions, config, prices)
    assert result.loc[0, "total_collateral_usd"] == 0.0
    assert result.loc[0, "total_debt_usd"] == 500.0
    assert result.loc[0, "health_factor"] == 0.0
    assert result.loc[0, "risk_category"] == "High Risk"


def test_e_mode_lst_weth_wallet_is_classified_as_recursive_loop() -> None:
    positions, config, prices = _frames([
        _row("0xlst", "wstETH", 3_000.0, 0.0, enabled=True, user_emode=1),
        _row("0xweth", "WETH", 0.0, 2_000.0, enabled=False, user_emode=1),
    ])
    result = calculate_health_factor(positions, config, prices)
    assert result.loc[0, "emode_category"] == 1
    assert result.loc[0, "is_recursive_loop"]


def test_existing_and_missing_position_prices_merge_without_suffix_key_error() -> None:
    rows = [
        {**_row("0xcoll", "DAI", 100.0, 0.0, enabled=True, price=1.0),
         "wallet_address": "0xexisting"},
        {**_row("0xcoll", "DAI", 100.0, 0.0, enabled=True, price=None),
         "wallet_address": "0xmissing"},
    ]
    positions = pd.DataFrame(rows)
    config = pd.DataFrame([{
        "snapshot_block": 100, "token_address": "0xcoll",
        "lt_std": 0.80,
    }])
    prices = pd.DataFrame([{
        "snapshot_block": 100, "token_address": "0xcoll", "price_usd": 2.0,
    }])
    result = calculate_health_factor(positions, config, prices)
    values = result.set_index("wallet_address")["total_collateral_usd"]
    assert values.loc["0xexisting"] == 200.0
    assert values.loc["0xmissing"] == 200.0


def test_oracle_price_overrides_embedded_position_price() -> None:
    positions, config, _ = _frames([_row("0xcoll", "DAI", 100.0, 0.0, enabled=True, price=1.0)])
    prices = pd.DataFrame([{"snapshot_block": 100, "token_address": "0xcoll", "price_usd": 2.0}])
    assert calculate_health_factor(positions, config, prices).loc[0, "total_collateral_usd"] == 200.0


def test_emode_membership_matches_category_when_token_has_multiple_categories() -> None:
    positions = pd.DataFrame([
        {**_row("0xcoll", "RETH", 100.0, 0.0, enabled=True, user_emode=2), "wallet_address": "0xmulti"},
        {**_row("0xdebt", "WETH", 0.0, 50.0, enabled=False, user_emode=2), "wallet_address": "0xmulti"},
    ])
    config = pd.DataFrame([
        {"snapshot_block": 100, "token_address": "0xcoll", "lt_std": .5, "emode_category": 1, "lt_emode": .6},
        {"snapshot_block": 100, "token_address": "0xcoll", "lt_std": .5, "emode_category": 2, "lt_emode": .9},
        {"snapshot_block": 100, "token_address": "0xdebt", "lt_std": .5, "emode_category": 2, "lt_emode": .9},
    ])
    prices = positions[["snapshot_block", "token_address"]].drop_duplicates().assign(price_usd=1.0)
    result = calculate_health_factor(positions, config, prices)
    assert result.loc[0, "health_factor"] == 1.8


def test_false_string_parses_as_false() -> None:
    assert not _as_bool(pd.Series(["false"])).iloc[0]


def test_matching_non_one_emode_category_uses_emode_lt_and_mismatch_falls_back() -> None:
    rows = [
        {**_row("0xcoll", "DAI", 100.0, 0.0, enabled=True, user_emode=2),
         "wallet_address": "0xmatch"},
        {**_row("0xcoll", "DAI", 0.0, 50.0, enabled=False, user_emode=2),
         "wallet_address": "0xmatch"},
        {**_row("0xcoll", "DAI", 100.0, 0.0, enabled=True, user_emode=3),
         "wallet_address": "0xmismatch"},
        {**_row("0xcoll", "DAI", 0.0, 50.0, enabled=False, user_emode=3),
         "wallet_address": "0xmismatch"},
    ]
    positions = pd.DataFrame(rows)
    # Reserve E-Mode LT is valid for category 2 on this token.
    config = pd.DataFrame([{
        "snapshot_block": 100, "token_address": "0xcoll", "lt_std": 0.50,
        "lt_emode": 0.90, "emode_category": 2,
    }])
    prices = pd.DataFrame([{
        "snapshot_block": 100, "token_address": "0xcoll", "price_usd": 1.0,
    }])
    result = calculate_health_factor(positions, config, prices).set_index("wallet_address")
    assert result.loc["0xmatch", "health_factor"] == 1.8
    assert result.loc["0xmismatch", "health_factor"] == 1.0
