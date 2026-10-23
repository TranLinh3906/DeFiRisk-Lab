"""Find Aave liquidation cliffs and reverse stress thresholds.

The public functions take position-grain data as used by ``stress_test.py``.
Run ``python src/models/cliff_reverse.py`` from any directory to write the
cliff curve mart from the project's processed positions and baseline risk mart.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

try:  # Supports both ``python src/models/cliff_reverse.py`` and package imports.
    from .stress_test import MARTS, PROCESSED, _calculate_wallets, _load_model_inputs, _prepare_positions
except ImportError:  # pragma: no cover - exercised by direct script execution
    from stress_test import MARTS, PROCESSED, _calculate_wallets, _load_model_inputs, _prepare_positions


def _risk_metrics(positions: pd.DataFrame, eth_shock: float, depeg_shock: float) -> pd.DataFrame:
    """Aggregate risk independently for each snapshot block."""
    result = _calculate_wallets(
        positions,
        {"eth_shock": eth_shock, "depeg_shock": depeg_shock},
        "cliff_scan",
    )
    result["_at_risk"] = result["hf_after"] < 1.0
    rows = []
    for block, snap in result.groupby("snapshot_block", sort=True):
        at_risk = snap["_at_risk"]
        rows.append({
            "snapshot_block": int(block),
            "debt_at_risk_usd": float(snap.loc[at_risk, "debt_usd"].sum()),
            "positions_at_risk": int(snap.loc[at_risk, "wallet"].nunique()),
            "total_protocol_debt_usd": float(snap["debt_usd"].sum()),
        })
    return pd.DataFrame(rows)


def _drop_grid(max_drop: float, step: float) -> np.ndarray:
    """Create a stable grid including both zero and max_drop where possible."""
    if not np.isfinite(max_drop) or not 0 <= max_drop <= 1:
        raise ValueError("max_drop must be between 0 and 1")
    if not np.isfinite(step) or step <= 0:
        raise ValueError("step must be greater than zero")
    values = np.arange(0.0, max_drop + step * 0.5, step, dtype=float)
    values = values[values <= max_drop + 1e-12]
    if values.size == 0 or values[0] != 0.0:
        values = np.insert(values, 0, 0.0)
    if not np.isclose(values[-1], max_drop) and max_drop > values[-1]:
        values = np.append(values, max_drop)
    return np.round(values, 10)


def find_liquidation_cliff(
    positions_df: pd.DataFrame, max_drop: float = 0.60, step: float = 0.01
) -> pd.DataFrame:
    """Scan ETH price drops and measure risk levels and marginal debt growth.

    ``eth_shock`` is stored as a negative fraction (for example -0.20); the
    positive ``eth_drop_pct`` column is convenient for chart axes. A cliff flag
    marks a step whose marginal debt increase is at least three times the median
    positive marginal increase on the scan grid.
    """
    positions = _prepare_positions(positions_df)
    drops = _drop_grid(max_drop, step)
    rows: list[dict[str, float | int]] = []
    for drop in drops:
        metrics = _risk_metrics(positions, -float(drop), 0.0)
        for _, metric in metrics.iterrows():
            rows.append({
                "snapshot_block": int(metric["snapshot_block"]),
                "eth_shock": -float(drop), "eth_drop_pct": float(drop * 100.0),
                "debt_at_risk_usd": metric["debt_at_risk_usd"],
                "positions_at_risk": metric["positions_at_risk"],
                "total_protocol_debt_usd": metric["total_protocol_debt_usd"],
            })
    curve = pd.DataFrame(rows)
    curve = curve.sort_values(["snapshot_block", "eth_drop_pct"]).reset_index(drop=True)
    grouped = curve.groupby("snapshot_block", sort=False)
    delta_pct = grouped["eth_drop_pct"].diff()
    curve["marginal_debt_at_risk_usd_per_pct"] = grouped["debt_at_risk_usd"].diff().div(delta_pct.where(delta_pct.ne(0))).clip(lower=0)
    curve["marginal_multiple_vs_median"] = 0.0
    curve["is_cliff"] = False
    for _, indexes in curve.groupby("snapshot_block").groups.items():
        positive_marginal = curve.loc[indexes, "marginal_debt_at_risk_usd_per_pct"].dropna()
        positive_marginal = positive_marginal[positive_marginal.gt(0)]
        median_marginal = float(positive_marginal.median()) if not positive_marginal.empty else 0.0
        if median_marginal > 0:
            curve.loc[indexes, "marginal_multiple_vs_median"] = curve.loc[indexes, "marginal_debt_at_risk_usd_per_pct"] / median_marginal
            curve.loc[indexes, "is_cliff"] = curve.loc[indexes, "marginal_multiple_vs_median"].ge(3.0)
    curve["debt_at_risk_pct"] = np.where(
        curve["total_protocol_debt_usd"] > 0,
        100.0 * curve["debt_at_risk_usd"] / curve["total_protocol_debt_usd"],
        0.0,
    )
    return curve


def _parse_target(target: Any, total_debt_usd: float) -> tuple[str, float]:
    """Resolve a USD target or percentage target into a debt amount."""
    if isinstance(target, dict):
        if "usd" in target:
            amount = float(target["usd"])
            if amount < 0:
                raise ValueError("Target debt must be non-negative")
            return str(target.get("label", f"${amount:,.0f}")), amount
        pct = target.get("pct", target.get("percent"))
        if pct is not None:
            fraction = float(pct)
            if fraction < 0:
                raise ValueError("Target debt must be non-negative")
            if fraction > 1:
                fraction /= 100.0
            return str(target.get("label", f"{fraction:.0%}")), total_debt_usd * fraction
        raise ValueError(f"Unrecognized reverse stress target: {target}")
    if isinstance(target, str) and target.strip().endswith("%"):
        fraction = float(target.strip()[:-1]) / 100.0
        if fraction < 0:
            raise ValueError("Target debt must be non-negative")
        return target.strip(), total_debt_usd * fraction
    amount = float(target)
    if amount < 0:
        raise ValueError("Target debt must be non-negative")
    if not np.isfinite(amount):
        raise ValueError(f"Target debt must be finite: {target}")
    return f"${amount:,.0f}", amount


def _first_reaching_shock(curve: pd.DataFrame, target_usd: float, shock_col: str) -> float | None:
    """Return the least severe sampled shock reaching a debt-risk target."""
    reached = curve.loc[curve["debt_at_risk_usd"] >= target_usd]
    return float(reached.iloc[0][shock_col]) if not reached.empty else None


def run_reverse_stress(positions_df: pd.DataFrame, target_debt_usd_list: list[Any]) -> dict[str, Any]:
    """Find minimum sampled ETH-only or depeg-only shock for each risk target.

    Targets can be dollar amounts (e.g. ``100_000_000``), percentage strings
    (e.g. ``"10%"``), or dictionaries such as ``{"pct": 0.10}``. Percentage
    targets use total baseline protocol debt across the supplied snapshots.
    """
    positions = _prepare_positions(positions_df)
    eth_drops = _drop_grid(0.60, 0.01)
    depeg_drops = _drop_grid(0.20, 0.005)
    blocks = sorted(positions["snapshot_block"].dropna().unique().tolist())
    output: dict[str, Any] = {"snapshots": {}}
    for block in blocks:
      snapshot_positions = positions[positions["snapshot_block"].eq(block)]
      eth_curve = pd.concat([
          pd.DataFrame({"eth_shock": [-float(drop)]}).join(_risk_metrics(snapshot_positions, -float(drop), 0.0).drop(columns="snapshot_block"))
          for drop in eth_drops
      ], ignore_index=True)
      depeg_curve = pd.concat([
          pd.DataFrame({"depeg_shock": [-float(drop)]}).join(_risk_metrics(snapshot_positions, 0.0, -float(drop)).drop(columns="snapshot_block"))
          for drop in depeg_drops
      ], ignore_index=True)
      baseline_debt = float(eth_curve.iloc[0]["total_protocol_debt_usd"])
      results = []
      for target in target_debt_usd_list:
        label, target_usd = _parse_target(target, baseline_debt)
        eth_shock = _first_reaching_shock(eth_curve, target_usd, "eth_shock")
        depeg_shock = _first_reaching_shock(depeg_curve, target_usd, "depeg_shock")
        options = [("eth", eth_shock), ("lst_depeg", depeg_shock)]
        reached_options = [(kind, shock) for kind, shock in options if shock is not None]
        if reached_options:
            minimum_type, minimum_shock = min(reached_options, key=lambda item: abs(item[1]))
        else:
            minimum_type, minimum_shock = None, None
        results.append({
            "target": label,
            "target_debt_usd": float(target_usd),
            "target_debt_pct_of_protocol": float(target_usd / baseline_debt * 100.0) if baseline_debt else 0.0,
            "eth_shock_required": eth_shock,
            "lst_depeg_shock_required": depeg_shock,
            "minimum_shock_type": minimum_type,
            "minimum_shock": minimum_shock,
            "reached_within_search_grid": bool(reached_options),
        })
      output["snapshots"][str(int(block))] = {
          "snapshot_block": int(block), "baseline_protocol_debt_usd": baseline_debt,
          "search_grid": {"eth_min": 0.0, "eth_max_drop": 0.60, "eth_step": 0.01,
                          "depeg_min": 0.0, "depeg_max_drop": 0.20, "depeg_step": 0.005},
          "targets": results,
      }
    return output


def main() -> None:
    """Load project positions and export the ETH liquidation cliff curve."""
    positions, baseline = _load_model_inputs()
    curve = find_liquidation_cliff(positions)
    if "health_factor" in baseline:
        base = curve.iloc[0]
        baseline_at_risk = baseline[baseline["health_factor"] < 1.0]
        # Baseline HF mart may have been generated with identical inputs. Record
        # a diagnostic delta when its debt metric is available for reconciliation.
        if "total_debt_usd" in baseline:
            mart_by_block = baseline_at_risk.groupby("snapshot_block")["total_debt_usd"].sum()
            curve["baseline_mart_debt_at_risk_usd"] = curve["snapshot_block"].map(mart_by_block).fillna(0.0)
            curve["baseline_mart_debt_delta_usd"] = np.nan
            zero_rows = curve[curve["eth_drop_pct"].eq(0)]
            curve.loc[zero_rows.index, "baseline_mart_debt_delta_usd"] = zero_rows["debt_at_risk_usd"].to_numpy() - zero_rows["baseline_mart_debt_at_risk_usd"].to_numpy()
    MARTS.mkdir(parents=True, exist_ok=True)
    output = MARTS / "cliff_curve.parquet"
    curve.to_parquet(output, index=False)
    print(f"Wrote {output} ({len(curve)} ETH shock steps)")
    print(f"Largest sampled marginal debt increase: "
          f"${curve['marginal_debt_at_risk_usd_per_pct'].max():,.2f} per percentage point")
    print("Cliff candidates:", curve.loc[curve["is_cliff"], ["snapshot_block", "eth_drop_pct"]].to_dict("records"))


if __name__ == "__main__":
    main()
