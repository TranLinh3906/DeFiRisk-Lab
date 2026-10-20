"""Aave V3 counterfactual price stress testing engine.

Run from the repository root with ``python src/models/stress_test.py``. The
public functions also accept position-level pandas DataFrames for pipeline use.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import yaml

ROOT = Path(__file__).resolve().parents[2]
PROCESSED = ROOT / "data/processed/aave"
MARTS = ROOT / "data/marts"
PROTOCOL_ID = "aave_v3_ethereum"
POSITION_KEYS = ["snapshot_block", "protocol_id", "wallet_address", "token_address"]
WALLET_KEYS = POSITION_KEYS[:3]
ALIASES = {"snapshot_id": "snapshot_block", "wallet": "wallet_address", "token": "token_address"}
LST_SYMBOLS = {"STETH", "WSTETH", "WEETH", "CBETH"}
ETH_SYMBOLS = {"ETH", "WETH"}
BTC_SYMBOLS = {"BTC", "WBTC", "CBBTC"}
STABLE_SYMBOLS = {"USDC", "USDT"}


def _normalize_keys(frame: pd.DataFrame) -> pd.DataFrame:
    """Rename legacy key aliases and apply project key formats."""
    result = frame.copy()
    for alias, canonical in ALIASES.items():
        if alias in result:
            if canonical not in result:
                result = result.rename(columns={alias: canonical})
            else:
                result = result.drop(columns=alias)
    if "protocol_id" not in result:
        result["protocol_id"] = PROTOCOL_ID
    if "snapshot_block" not in result:
        raise ValueError("Input needs snapshot_block (or a numeric snapshot_id)")
    if not pd.api.types.is_numeric_dtype(result["snapshot_block"]):
        metadata_path = PROCESSED / "snapshot_metadata.json"
        if metadata_path.exists():
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            block_map = {label: entry.get("snapshot_block", entry.get("block"))
                         for label, entry in metadata.get("snapshots", {}).items()}
            mapped = result["snapshot_block"].map(block_map)
            if mapped.notna().all():
                result["snapshot_block"] = mapped
    result["snapshot_block"] = pd.to_numeric(result["snapshot_block"], errors="raise").astype("int64")
    result["protocol_id"] = result["protocol_id"].astype("string").str.strip()
    for col in ("wallet_address", "token_address"):
        if col in result:
            result[col] = result[col].astype("string").str.strip().str.lower()
    return result


def _number(values: pd.Series, default: float = 0.0) -> pd.Series:
    """Convert to finite float values and replace missing data safely."""
    return pd.to_numeric(values, errors="coerce").replace([np.inf, -np.inf], np.nan).fillna(default)


def load_scenarios(config_path: str = "config/scenarios.yaml") -> dict[str, Any]:
    """Load scenario definitions and heatmap axes from a YAML file."""
    path = Path(config_path)
    if not path.is_absolute():
        path = ROOT / path
    with path.open("r", encoding="utf-8") as stream:
        config = yaml.safe_load(stream) or {}
    scenarios = config.get("scenarios", config)
    if not isinstance(scenarios, dict):
        raise ValueError("Scenario YAML must contain a mapping under 'scenarios'")
    return config if "scenarios" in config else {"scenarios": scenarios}


def _prepare_positions(positions_df: pd.DataFrame) -> pd.DataFrame:
    """Map staging and canonical position field names to model inputs."""
    data = _normalize_keys(positions_df)
    aliases = {
        "collateral_amount_norm": "collateral_amount",
        "debt_amount_norm": "debt_amount",
        "token_symbol": "symbol",
        "collateral_enabled": "is_collateral_enabled",
        "price_usd_t0": "price_usd",
    }
    for alias, canonical in aliases.items():
        if canonical not in data and alias in data:
            data = data.rename(columns={alias: canonical})
    required = set(WALLET_KEYS + ["token_address"])
    missing = required - set(data.columns)
    if missing:
        raise ValueError(f"Position data missing contract keys: {sorted(missing)}")
    if "symbol" not in data:
        data["symbol"] = ""
    data["symbol"] = data["symbol"].astype("string").str.strip().str.upper()
    if "is_collateral_enabled" not in data:
        data["is_collateral_enabled"] = data.get("counted_as_collateral", False)
    data["is_collateral_enabled"] = data["is_collateral_enabled"].fillna(False).astype(bool)
    for col in ("collateral_amount", "debt_amount", "price_usd", "effective_lt"):
        if col not in data:
            data[col] = np.nan
        data[col] = _number(data[col], np.nan)

    # Use supplied USD t0 values to infer prices only when a usable price column
    # is absent. A zero or missing amount does not produce an inferred price.
    if data["price_usd"].isna().any():
        for usd_col, amount_col in (("collateral_usd_t0", "collateral_amount"),
                                    ("debt_usd_t0", "debt_amount")):
            if usd_col in data:
                inferred = _number(data[usd_col], np.nan).div(data[amount_col].where(data[amount_col].ne(0)))
                data["price_usd"] = data["price_usd"].fillna(inferred)
    data["price_usd"] = data["price_usd"].fillna(0.0)
    data["collateral_amount"] = data["collateral_amount"].fillna(0.0)
    data["debt_amount"] = data["debt_amount"].fillna(0.0)
    data["effective_lt"] = data["effective_lt"].fillna(0.0).clip(0.0, 1.0)

    # The canonical contract stores amounts and a normalized effective LT; derive
    # baseline USD columns when they were not materialized by the staging export.
    if "collateral_usd_t0" not in data:
        data["collateral_usd_t0"] = np.where(
            data["is_collateral_enabled"], data["collateral_amount"] * data["price_usd"], 0.0
        )
    else:
        data["collateral_usd_t0"] = _number(data["collateral_usd_t0"])
    if "debt_usd_t0" not in data:
        data["debt_usd_t0"] = data["debt_amount"] * data["price_usd"]
    else:
        data["debt_usd_t0"] = _number(data["debt_usd_t0"])
    return data


def _asset_multiplier(symbol: str, params: dict[str, Any]) -> float:
    """Return the scenario price multiplier for one reserve symbol."""
    eth = float(params.get("eth_shock", 0.0))
    btc = float(params.get("btc_shock", 0.0))
    depeg = float(params.get("depeg_shock", params.get("lst_depeg_shock", 0.0)))
    stable_shock = params.get("stablecoin_shocks", params.get("stablecoin_shock", 0.0))
    if isinstance(stable_shock, dict):
        stable_shock = stable_shock.get(symbol, 0.0)
    if symbol in LST_SYMBOLS:
        return max(0.0, (1.0 + eth) * (1.0 + depeg))
    if symbol in ETH_SYMBOLS:
        return max(0.0, 1.0 + eth)
    if symbol in BTC_SYMBOLS:
        return max(0.0, 1.0 + btc)
    if symbol in STABLE_SYMBOLS:
        return max(0.0, 1.0 + float(stable_shock))
    per_asset = params.get("asset_shocks", {})
    return max(0.0, 1.0 + float(per_asset.get(symbol, 0.0)))


def _calculate_wallets(data: pd.DataFrame, params: dict[str, Any], scenario_id: str) -> pd.DataFrame:
    """Revalue positions and aggregate stressed health metrics by wallet."""
    d = data.copy()
    d["_price_multiplier"] = d["symbol"].map(lambda sym: _asset_multiplier(str(sym), params))
    d["_collateral_usd_s"] = np.where(
        d["is_collateral_enabled"], d["collateral_usd_t0"] * d["_price_multiplier"], 0.0
    )
    d["_debt_usd_s"] = d["debt_usd_t0"] * d["_price_multiplier"]
    d["_weighted_collateral_s"] = d["_collateral_usd_s"] * d["effective_lt"]

    group_cols = WALLET_KEYS
    aggregate = d.groupby(group_cols, as_index=False, dropna=False).agg(
        collateral_s=("_collateral_usd_s", "sum"),
        debt_usd=("_debt_usd_s", "sum"),
        weighted_collateral_s=("_weighted_collateral_s", "sum"),
    )
    debt_positive = aggregate["debt_usd"].gt(0)
    aggregate["hf_after"] = np.where(debt_positive,
                                     aggregate["weighted_collateral_s"] / aggregate["debt_usd"], 999.0)
    aggregate["hf_after"] = aggregate["hf_after"].replace([np.inf, -np.inf], 999.0).fillna(999.0)
    if "health_factor" in d:
        baseline = d.groupby(group_cols, as_index=False, dropna=False)["health_factor"].first()
        baseline = baseline.rename(columns={"health_factor": "hf_before"})
        aggregate = aggregate.merge(baseline, on=group_cols, how="left")
    else:
        base = d.assign(_weighted_t0=d["collateral_usd_t0"] * d["effective_lt"])
        base = base.groupby(group_cols, as_index=False, dropna=False).agg(
            _weighted_t0=("_weighted_t0", "sum"), _debt_t0=("debt_usd_t0", "sum")
        )
        base["hf_before"] = np.where(base["_debt_t0"].gt(0), base["_weighted_t0"] / base["_debt_t0"], 999.0)
        aggregate = aggregate.merge(base[group_cols + ["hf_before"]], on=group_cols, how="left")
    if "is_recursive_loop" not in d:
        aggregate["is_recursive_loop"] = False
    else:
        loops = d.groupby(group_cols, as_index=False, dropna=False)["is_recursive_loop"].max()
        aggregate = aggregate.merge(loops, on=group_cols, how="left")
    aggregate["is_recursive_loop"] = aggregate["is_recursive_loop"].fillna(False).astype(bool)
    aggregate["debt_at_risk_usd"] = np.where(aggregate["hf_after"] < 1.0, aggregate["debt_usd"], 0.0)
    aggregate["scenario_id"] = scenario_id
    aggregate = aggregate.rename(columns={"wallet_address": "wallet"})
    return aggregate[["scenario_id", "snapshot_block", "protocol_id", "wallet", "hf_before", "hf_after",
                      "debt_usd", "debt_at_risk_usd", "is_recursive_loop"]]


def run_stress_test(positions_df: pd.DataFrame, scenario_params: dict[str, Any]) -> pd.DataFrame:
    """Return one stressed HF and debt-risk row per wallet and snapshot.

    Input is position-grain data with normalized token amounts, price and LT;
    ``scenario_params`` accepts eth_shock, btc_shock, depeg_shock, and stablecoin
    shock fields. A ``scenario_id`` can be supplied or is generated.
    """
    data = _prepare_positions(positions_df)
    params = dict(scenario_params)
    scenario_id = str(params.pop("scenario_id", "custom_scenario"))
    return _calculate_wallets(data, params, scenario_id)


def generate_stress_heatmap(
    positions_df: pd.DataFrame, eth_steps: list[float], depeg_steps: list[float]
) -> pd.DataFrame:
    """Calculate debt-at-risk and wallet counts for every ETH/depeg pair."""
    prepared = _prepare_positions(positions_df)
    rows: list[dict[str, Any]] = []
    for eth in eth_steps:
        for depeg in depeg_steps:
            result = _calculate_wallets(
                prepared, {"eth_shock": eth, "depeg_shock": depeg},
                f"heatmap_eth_{abs(eth):g}_depeg_{abs(depeg):g}",
            )
            at_risk = result["hf_after"] < 1.0
            rows.append({
                "eth_shock": float(eth),
                "depeg_shock": float(depeg),
                "debt_at_risk_usd": float(result.loc[at_risk, "debt_usd"].sum()),
                "positions_at_risk_count": int(result.loc[at_risk, "wallet"].nunique()),
            })
    return pd.DataFrame(rows, columns=["eth_shock", "depeg_shock", "debt_at_risk_usd", "positions_at_risk_count"])


def _load_model_inputs() -> tuple[pd.DataFrame, pd.DataFrame]:
    """Load underlying positions and attach baseline HF and looping labels."""
    positions_path = PROCESSED / "borrower_positions.parquet"
    if not positions_path.exists():
        positions_path = ROOT / "data/processed/fact_wallet_positions.parquet"
    if not positions_path.exists():
        raise FileNotFoundError("Expected borrower_positions.parquet or fact_wallet_positions.parquet")
    positions = _prepare_positions(pd.read_parquet(positions_path))
    baseline_path = MARTS / "borrower_risk.parquet"
    if not baseline_path.exists():
        raise FileNotFoundError(f"Missing baseline risk mart: {baseline_path}")
    baseline = _normalize_keys(pd.read_parquet(baseline_path))
    baseline_cols = WALLET_KEYS + [c for c in ("health_factor", "is_recursive_loop") if c in baseline]
    baseline = baseline[baseline_cols].drop_duplicates(subset=WALLET_KEYS, keep="first")
    positions = positions.drop(columns=[c for c in ("health_factor", "is_recursive_loop") if c in positions], errors="ignore")
    positions = positions.merge(baseline, on=WALLET_KEYS, how="left")

    # Attach display symbols where canonical fact rows do not carry one.
    if positions["symbol"].fillna("").eq("").any():
        registry_path = PROCESSED / "token_registry.csv"
        if registry_path.exists():
            registry = pd.read_csv(registry_path)
            if {"token_address", "symbol"}.issubset(registry.columns):
                registry["token_address"] = registry["token_address"].astype("string").str.strip().str.lower()
                registry = registry.drop_duplicates(subset=["token_address"], keep="first")
                positions = positions.merge(registry[["token_address", "symbol"]].rename(columns={"symbol": "_registry_symbol"}),
                                            on="token_address", how="left")
                positions["symbol"] = positions["symbol"].where(positions["symbol"].fillna("").ne(""),
                                                                   positions["_registry_symbol"])
                positions = positions.drop(columns="_registry_symbol")
    return positions, baseline


def _summary_row(scenario_id: str, params: dict[str, Any], result: pd.DataFrame) -> dict[str, Any]:
    """Aggregate scenario risk metrics, including the looping segment."""
    at_risk = result["hf_after"] < 1.0
    total_debt = float(result["debt_usd"].sum())
    risk_debt = float(result.loc[at_risk, "debt_usd"].sum())
    loop_at_risk = at_risk & result["is_recursive_loop"]
    return {
        "scenario_id": scenario_id,
        "eth_shock": float(params.get("eth_shock", 0.0)),
        "btc_shock": float(params.get("btc_shock", 0.0)),
        "depeg_shock": float(params.get("depeg_shock", params.get("lst_depeg_shock", 0.0))),
        "positions_at_risk": int(result.loc[at_risk, "wallet"].nunique()),
        "debt_at_risk_usd": risk_debt,
        "debt_at_risk_pct": risk_debt / total_debt * 100.0 if total_debt > 0 else 0.0,
        "looping_debt_at_risk_usd": float(result.loc[loop_at_risk, "debt_usd"].sum()),
    }


def main() -> None:
    """Run configured scenarios and write all three stress-test marts."""
    config = load_scenarios()
    positions, _ = _load_model_inputs()
    summary_rows = []
    position_rows = []
    for scenario_id, params_raw in config["scenarios"].items():
        params = dict(params_raw or {})
        stable_shock = params.pop("stablecoin_shock", None)
        if stable_shock is not None:
            params["stablecoin_shock"] = stable_shock
        result = run_stress_test(positions, {**params, "scenario_id": scenario_id})
        summary_rows.append(_summary_row(scenario_id, params, result))
        position_rows.append(result)

    heat_cfg = config.get("heatmap", {})
    heatmap = generate_stress_heatmap(
        positions,
        list(heat_cfg.get("eth_steps", [0.0, -0.1, -0.2, -0.3, -0.4, -0.5])),
        list(heat_cfg.get("depeg_steps", [0.0, -0.01, -0.03, -0.05, -0.1])),
    )
    summary = pd.DataFrame(summary_rows, columns=[
        "scenario_id", "eth_shock", "btc_shock", "depeg_shock", "positions_at_risk",
        "debt_at_risk_usd", "debt_at_risk_pct", "looping_debt_at_risk_usd",
    ])
    position_output = pd.concat(position_rows, ignore_index=True) if position_rows else pd.DataFrame(
        columns=["scenario_id", "snapshot_block", "protocol_id", "wallet", "hf_before", "hf_after",
                 "debt_usd", "debt_at_risk_usd", "is_recursive_loop"]
    )
    MARTS.mkdir(parents=True, exist_ok=True)
    summary.to_parquet(MARTS / "stress_summary.parquet", index=False)
    position_output.to_parquet(MARTS / "stress_positions.parquet", index=False)
    heatmap.to_parquet(MARTS / "stress_heatmap.parquet", index=False)
    print(f"Wrote {MARTS / 'stress_summary.parquet'} ({len(summary)} scenarios)")
    print(f"Wrote {MARTS / 'stress_positions.parquet'} ({len(position_output):,} wallet-scenarios)")
    print(f"Wrote {MARTS / 'stress_heatmap.parquet'} ({len(heatmap)} shock pairs)")


if __name__ == "__main__":
    main()
