"""Bounded sensitivity scaffold and a one-round, fixed-price benchmark."""
from __future__ import annotations

from dataclasses import replace
from itertools import product
import math
from pathlib import Path

from src.models.cascade import CascadeConfig, POLICIES, simulate_cascade
from src.models.n5_inputs import number
from src.models.n5_validation import check_schema, validate_model_outputs


def scenario_shocks(symbols: dict[str, str], *, eth_shock=0.0, depeg_shock=0.0,
                    depeg_symbols=("STETH", "WSTETH"),
                    eth_symbols=("ETH", "WETH", "STETH", "WSTETH", "WEETH", "CBETH", "RETH")) -> dict[str, float]:
    """Combine ETH return and stETH/ETH return multiplicatively, once.

    wstETH conversion ratio is held fixed during a run. Other LSTs do not
    inherit stETH depeg automatically; explicitly extend depeg_symbols if needed.
    Keys can be addresses; symbols are descriptive only.
    """
    eth = number(eth_shock, "eth_shock", -.999999, 0)
    depeg = number(depeg_shock, "depeg_shock", -.999999, 0)
    targets = {s.upper() for s in depeg_symbols}
    eth_targets = {s.upper() for s in eth_symbols} | targets
    if depeg and not any(s.upper() in targets for s in symbols.values()):
        raise ValueError("Depeg scenario has no matching collateral/debt asset")
    if eth and not any(s.upper() in eth_targets for s in symbols.values()):
        raise ValueError("ETH scenario has no matching asset")
    return {token: ((1 + eth) * (1 + depeg) - 1 if symbol.upper() in targets else eth)
            for token, symbol in symbols.items() if symbol.upper() in eth_targets}


def _tag(result, mode, context):
    context = dict(context or {})
    allowed = {"snapshot_block", "protocol_id", "data_kind", "validation_status"}
    if context.keys() - allowed:
        raise ValueError("Unknown context keys")
    if context.get("validation_status", "unvalidated") != "unvalidated":
        raise ValueError("Scaffold outputs cannot be marked accepted")
    for row in [*result.rounds, result.summary]:
        row["engine_model_version"] = row["model_version"]
        row["model_version"] = "n5_preparation_v0.1"
        row["data_kind"] = "unspecified_input_simulation"
        row.update(context)
        row.update(experiment_mode=mode, validation_status="unvalidated")
    return result


def run_static_benchmark(wallets, assets, config=None, *, shocks=None,
                         scenario_id="benchmark", context=None):
    """One round, lambda=0, no endogenous price feedback.

    Shares the engine's one-pair-per-wallet rule and gas/DEX fee assumptions.
    Remaining eligible debt is intentional; convergence is not claimed.
    """
    cfg = replace(config or CascadeConfig(), lambda_impact=0.0, max_rounds=1)
    result = simulate_cascade(wallets, assets, cfg, shocks=shocks, scenario_id=scenario_id)
    return _tag(result, "static_one_round", context)


def compare_static_cascade(wallets, assets, config=None, *, shocks=None,
                           scenario_id="comparison", context=None):
    cfg = config or CascadeConfig()
    static = run_static_benchmark(wallets, assets, cfg, shocks=shocks, scenario_id=scenario_id, context=context)
    cascade = _tag(simulate_cascade(wallets, assets, cfg, shocks=shocks, scenario_id=scenario_id),
                   "cascade", context)
    for result in (static, cascade):
        check = validate_model_outputs(result, wallets)
        if check["status"] != "passed":
            raise ValueError(check["errors"])
    first = static.summary["total_simulated_repaid_usd"]
    return dict(static=static, cascade=cascade,
                comparison=dict(scenario_id=scenario_id, cf_rule=cfg.cf_rule,
                                static_repaid_usd=first,
                                cascade_repaid_usd=cascade.summary["total_simulated_repaid_usd"],
                                cascade_to_static_ratio=cascade.summary["total_simulated_repaid_usd"] / first if first else None,
                                static_bad_debt_usd=static.summary["bad_debt_usd"],
                                cascade_bad_debt_usd=cascade.summary["bad_debt_usd"],
                                validation_status="unvalidated"))


def run_sensitivity_analysis(wallets, assets, *, symbols,
                             policies=POLICIES, impact_parameters=(0.0, .02),
                             sell_fractions=(.5, 1.0), eth_shocks=(0.0, -.2),
                             depeg_shocks=(0.0, -.05), depth_multipliers=(1.0,),
                             base_config=None, max_runs=5000, context=None) -> list[dict]:
    """Return small summary rows; never write files or retain all transaction logs.

    Set depth_multipliers=(.5,1,2), ETH=(0,-.1,-.2,-.3) and depeg=
    (-.01,-.02,-.03,-.04,-.05,-.07,-.1) for the research grid when ready.
    Lambda and depth multiplier are independent dimensions, not interchangeable.
    """
    if set(symbols) != set(assets):
        raise ValueError("symbols must cover exactly the portfolio asset keys")
    axes = [tuple(v) for v in (policies, impact_parameters, sell_fractions, eth_shocks, depeg_shocks, depth_multipliers)]
    count = math.prod(len(v) for v in axes)
    if count == 0 or count > max_runs:
        raise ValueError(f"Grid has {count} runs; allowed 1..{max_runs}")
    rows = []
    for i, (policy, impact, sell, eth, depeg, depth) in enumerate(product(*axes), 1):
        factor = number(depth, "depth multiplier", 1e-300)
        run_assets = {t: replace(a, dex_depth_usd=a.dex_depth_usd * factor) for t, a in assets.items()}
        cfg = replace(base_config or CascadeConfig(), cf_rule=policy, lambda_impact=impact, sell_fraction=sell)
        shocks = scenario_shocks(symbols, eth_shock=eth, depeg_shock=depeg)
        result = _tag(simulate_cascade(wallets, run_assets, cfg, shocks=shocks,
                                       scenario_id=f"eth_{eth:g}_depeg_{depeg:g}"), "cascade", context)
        check = validate_model_outputs(result, wallets)
        if check["status"] != "passed":
            raise ValueError(f"Grid run {i}: {check['errors']}")
        rows.append(dict(result.summary, price_impact_parameter=float(impact), sell_fraction=float(sell),
                         eth_shock=float(eth), depeg_shock=float(depeg), depth_multiplier=factor))
    errors = check_schema(rows, "sensitivity")
    if errors:
        raise ValueError(errors)
    return rows


def export_experiment(rows: list[dict], output_dir: str | Path):
    """Export sensitivity to a NEW directory only; never replace existing files."""
    import pyarrow as pa
    import pyarrow.parquet as pq
    errors = check_schema(rows, "sensitivity")
    if errors:
        raise ValueError(errors)
    table = pa.Table.from_pylist(rows)
    # Preserve numeric type even when every amplification is null.
    col = table.schema.get_field_index("cascade_amplification")
    if col >= 0:
        table = table.set_column(col, "cascade_amplification", pa.array(
            [r["cascade_amplification"] for r in rows], type=pa.float64()))
    table = table.replace_schema_metadata({b"validation_status": b"unvalidated"})
    dest = Path(output_dir)
    dest.mkdir(parents=True, exist_ok=False)
    pq.write_table(table, dest / "sensitivity.parquet")
    (dest / "README.txt").write_text(
        "N5 experimental sensitivity scaffold. Not accepted research results.\n"
        "See parameters_json, shocks_json, data_kind and validation_status per run.\n", encoding="utf-8")
    return dest / "sensitivity.parquet"
