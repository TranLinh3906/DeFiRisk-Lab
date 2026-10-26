"""Run empirical N5 Q1 (Monte Carlo & Post-Execution Losses) and Q3 (Transitions, DebtRank, Compound III) on the completed N2/N3/N4 cohort."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

from copy import deepcopy
from src.models.cascade import POLICIES, CascadeResult, Wallet, close_factor, collateral_value, debt_value, health_factor
from src.models.n5_impact_review import KEYS, ExecutionCurve
from src.models.n5_inputs import prepare_portfolio, read_rows
from src.models.n5_integration import adapt_compound_partial
from src.models.n5_kpis import baseline_wallet_kpis, graph_conservation_report, kpi_mapping, validate_kpi_artifacts
from src.models.n5_readiness import build_aave_graph, graph_node_metrics, inspect_compound, reconcile_debt, verify_manifest, write_manifest
from src.models.n5_research import (
    cascade_transitions,
    compound_price_stress,
    debt_rank,
    expected_shortfall,
    explicit_base_supply_withdrawal,
    summarize_q1,
    wallet_loss_rows,
)
from src.models.research_tolerances import evaluate_research_gate, load_tolerances
from src.models.stress_test import _asset_multiplier


def _vectorized_mc_q1(
    positions_df: pd.DataFrame,
    baseline_df: pd.DataFrame,
    mc_shocks: pd.DataFrame,
    depeg_symbols: set[str],
    eth_symbols: set[str],
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Vectorized evaluation of 10,000 Student-t Copula scenarios across all cohort wallets in one snapshot."""
    pos = positions_df.copy()
    sym_upper = pos["symbol"].astype(str).str.upper()
    is_depeg = sym_upper.isin(depeg_symbols)
    is_eth = sym_upper.isin(eth_symbols - depeg_symbols)
    pos["group"] = np.where(is_depeg, "depeg", np.where(is_eth, "eth", "other"))
    pos["coll_usd"] = pos["collateral_amount"] * pos["price_usd"] * pos["is_collateral_enabled"]
    pos["wcoll_usd"] = pos["coll_usd"] * pos["effective_lt"]
    pos["debt_usd"] = pos["debt_amount"] * pos["price_usd"]

    grouped = (
        pos.groupby(["wallet_address", "group"], as_index=False)[["coll_usd", "wcoll_usd", "debt_usd"]]
        .sum()
        .pivot(index="wallet_address", columns="group", values=["coll_usd", "wcoll_usd", "debt_usd"])
        .fillna(0.0)
    )
    wallets = baseline_df["wallet_address"].tolist()
    grouped = grouped.reindex(wallets).fillna(0.0)

    def col_vec(metric: str, grp: str) -> np.ndarray:
        if (metric, grp) in grouped.columns:
            return grouped[(metric, grp)].to_numpy(dtype=np.float64)
        return np.zeros(len(wallets), dtype=np.float64)

    c_dep, c_eth, c_oth = col_vec("coll_usd", "depeg"), col_vec("coll_usd", "eth"), col_vec("coll_usd", "other")
    w_dep, w_eth, w_oth = col_vec("wcoll_usd", "depeg"), col_vec("wcoll_usd", "eth"), col_vec("wcoll_usd", "other")
    d_dep, d_eth, d_oth = col_vec("debt_usd", "depeg"), col_vec("debt_usd", "eth"), col_vec("debt_usd", "other")

    eth_s = mc_shocks["eth_shock"].to_numpy(dtype=np.float64)[None, :]
    dep_s = mc_shocks["depeg_shock"].to_numpy(dtype=np.float64)[None, :]
    m_dep = (1.0 + eth_s) * (1.0 + dep_s)
    m_eth = 1.0 + eth_s

    coll_mat = c_dep[:, None] * m_dep + c_eth[:, None] * m_eth + c_oth[:, None]
    wcoll_mat = w_dep[:, None] * m_dep + w_eth[:, None] * m_eth + w_oth[:, None]
    debt_mat = d_dep[:, None] * m_dep + d_eth[:, None] * m_eth + d_oth[:, None]

    hf_lt_1 = wcoll_mat < debt_mat
    bad_debt_mat = np.maximum(0.0, debt_mat - coll_mat)

    init_debt = baseline_df["debt_usd"].to_numpy(dtype=np.float64)
    init_eq = baseline_df["net_equity_usd"].to_numpy(dtype=np.float64)
    loops = baseline_df["is_recursive_loop"].to_numpy(dtype=bool)
    buckets = baseline_df["equity_bucket"].astype(str).to_numpy()
    snap_block = int(baseline_df["snapshot_block"].iloc[0])
    snap_id = str(baseline_df["snapshot_id"].iloc[0])

    prob_hf_lt_1 = hf_lt_1.mean(axis=1)
    prob_bad_debt = (bad_debt_mat > 0.0).mean(axis=1)
    mean_bad_debt = bad_debt_mat.mean(axis=1)
    mean_bdr_t0 = mean_bad_debt / init_debt

    wallet_rows = []
    for i, w_addr in enumerate(wallets):
        bd_vec = bad_debt_mat[i]
        es99_bd = expected_shortfall(bd_vec, 0.99)
        eq_val = float(init_eq[i])
        if eq_val > 0.0:
            scaled_vec = bd_vec / eq_val * 1e5
            mean_scaled = float(scaled_vec.mean())
            es99_scaled = expected_shortfall(scaled_vec, 0.99)
        else:
            mean_scaled = None
            es99_scaled = None
        wallet_rows.append({
            "snapshot_id": snap_id,
            "snapshot_block": snap_block,
            "wallet_address": w_addr,
            "is_recursive_loop": bool(loops[i]),
            "equity_bucket": str(buckets[i]),
            "initial_equity_usd": eq_val,
            "initial_debt_usd": float(init_debt[i]),
            "scenario_count": int(bad_debt_mat.shape[1]),
            "probability_hf_lt_1": float(prob_hf_lt_1[i]),
            "probability_bad_debt": float(prob_bad_debt[i]),
            "mean_bad_debt_usd": float(mean_bad_debt[i]),
            "es99_bad_debt_usd": float(es99_bd),
            "mean_bad_debt_ratio_t0": float(mean_bdr_t0[i]),
            "mean_loss_per_100k": mean_scaled,
            "es99_loss_per_100k": es99_scaled,
        })
    wallets_out = pd.DataFrame(wallet_rows)

    # Stratum-level scenario distributions for usable positive-equity wallets
    usable_mask = init_eq > 0.0
    usable_df = pd.DataFrame({
        "wallet_idx": np.flatnonzero(usable_mask),
        "equity_bucket": buckets[usable_mask],
        "is_recursive_loop": loops[usable_mask],
    })
    support = usable_df.groupby("equity_bucket")["is_recursive_loop"].nunique()
    common_strata = set(support[support.eq(2)].index)

    strata_rows = []
    stratum_scenario_records = []
    scenario_ids = mc_shocks["scenario_id"].tolist()
    for (bucket, loop_flag), grp in usable_df.groupby(["equity_bucket", "is_recursive_loop"]):
        idxs = grp["wallet_idx"].to_numpy()
        bd_sub = bad_debt_mat[idxs, :]
        eq_sub = init_eq[idxs, None]
        scaled_sub = bd_sub / eq_sub * 1e5
        hf_sub = hf_lt_1[idxs, :]
        mean_loss_by_scen = scaled_sub.mean(axis=0)
        pbd_by_scen = (bd_sub > 0.0).mean(axis=0)
        phf_by_scen = hf_sub.mean(axis=0)
        strata_rows.append({
            "snapshot_id": snap_id,
            "snapshot_block": snap_block,
            "equity_bucket": str(bucket),
            "is_recursive_loop": bool(loop_flag),
            "wallets": int(len(idxs)),
            "scenario_count": int(len(scenario_ids)),
            "mean_loss_per_100k": float(mean_loss_by_scen.mean()),
            "es99_loss_per_100k": float(expected_shortfall(mean_loss_by_scen, 0.99)),
            "probability_bad_debt": float(pbd_by_scen.mean()),
            "probability_hf_lt_1": float(phf_by_scen.mean()),
            "common_support": str(bucket) in common_strata,
        })
        if str(bucket) in common_strata:
            for s_idx, s_mean_loss in enumerate(mean_loss_by_scen):
                stratum_scenario_records.append({
                    "is_recursive_loop": bool(loop_flag),
                    "scenario_idx": s_idx,
                    "loss": float(s_mean_loss),
                    "pbd": float(pbd_by_scen[s_idx]),
                    "phf": float(phf_by_scen[s_idx]),
                })

    strata_out = pd.DataFrame(strata_rows)
    comp_rows = []
    if stratum_scenario_records:
        st_df = pd.DataFrame(stratum_scenario_records)
        matched = st_df.groupby(["is_recursive_loop", "scenario_idx"], as_index=False).agg(
            loss=("loss", "mean"), pbd=("pbd", "mean"), phf=("phf", "mean")
        )
        for loop_flag, g in matched.groupby("is_recursive_loop"):
            comp_rows.append({
                "snapshot_id": snap_id,
                "snapshot_block": snap_block,
                "is_recursive_loop": bool(loop_flag),
                "common_strata": int(len(common_strata)),
                "mean_loss_per_100k": float(g["loss"].mean()),
                "es99_loss_per_100k": float(expected_shortfall(g["loss"].to_numpy(), 0.99)),
                "probability_bad_debt": float(g["pbd"].mean()),
                "probability_hf_lt_1": float(g["phf"].mean()),
                "matching": "equal_common_equity_strata",
                "scenario_source": "mc_10000_calibrated_daily_t_copula",
            })
    return wallets_out, strata_out, pd.DataFrame(comp_rows)


def _simulate_q2_cf_policy(
    wallets: list[Wallet],
    assets: dict,
    multipliers: dict[str, float],
    routes: dict,
    *,
    cf_rule: str,
    method: str = "linear",
    gas: float = 5.0,
    max_rounds: int = 100,
    allowed_routes: set | None = None,
    pool_state: str = "cumulative_across_rounds",
    ordering: str = "lowest_hf",
    pair_rule: str = "bonus_minus_impact",
    initial_cohort_debt_usd: float = 0.0,
) -> tuple[dict, list[dict]]:
    """Run bounded cascade execution under a specified Close Factor policy (baseline_aave, fixed_50, fixed_100, dynamic_linear)."""
    state = deepcopy(wallets)
    prices = {t: a.price_usd * multipliers[t] for t, a in assets.items()}
    active = [w for w in state if debt_value(w, prices) > 0 and health_factor(w, assets, prices) < 1]
    rounds_out, transactions = [], []
    sold: dict[str, float] = {}
    outside_domain_attempts = 0
    for number in range(1, max_rounds + 1):
        if pool_state == "reset_each_round":
            sold = {}
        executed = eligible = unknown = rejected = 0
        repaid = profit_sum = rejected_debt_usd = 0.0
        ordered = sorted(active, key=lambda w: w.wallet)
        if ordering == "lowest_hf":
            ordered.sort(key=lambda w: (health_factor(w, assets, prices), w.wallet))
        for w in ordered:
            hf = health_factor(w, assets, prices)
            w_debt = debt_value(w, prices)
            if hf >= 1 or w_debt <= 0:
                continue
            eligible += 1
            cf_val = close_factor(hf, cf_rule)
            cap = cf_val * w_debt
            best, unavailable, candidates = None, False, []
            for ct, cq in sorted(w.collateral.items()):
                if cq <= 0 or not w.collateral_enabled.get(ct, True):
                    continue
                bonus = w.effective_bonus.get(ct, assets[ct].liquidation_bonus)
                for dt, dq in sorted(w.debt.items()):
                    repay = min(cap, dq * prices[dt], cq * prices[ct] / (1 + bonus))
                    if repay < 0.01:
                        continue
                    sale = repay * (1 + bonus)
                    reference_sale = sale / multipliers[ct]
                    route = routes.get((ct, dt))
                    pool = route["pool_address"] if route else None
                    before = sold.get(pool, 0.0) if pool else 0.0
                    status, slip = "supported", 0.0
                    if method != "no_impact" and ct != dt:
                        if route is None:
                            status = (
                                "outside_selected_scope"
                                if allowed_routes is not None and (ct, dt) not in allowed_routes
                                else "missing_exact_route"
                            )
                        else:
                            try:
                                slip = route["curve"].incremental(before, reference_sale, method)
                            except ValueError as exc:
                                status = str(exc)
                                if status == "outside_quote_domain":
                                    outside_domain_attempts += 1
                    if status != "supported":
                        unavailable = True
                        continue
                    execution_fee = route.get("fee_rate", 0.0) if (route is not None and ct != dt) else 0.0
                    profit = sale * (1 - slip) * (1 - execution_fee) - repay - gas
                    candidate = {
                        "round": number,
                        "wallet_address": w.wallet,
                        "token_in_address": ct,
                        "token_out_address": dt,
                        "pool_address": pool,
                        "sold_usd": sale,
                        "sale_snapshot_usd": reference_sale,
                        "cumulative_after_snapshot_usd": before + reference_sale,
                        "repaid_debt_usd": repay,
                        "liquidator_profit_usd": profit,
                        "slippage_pct": 100.0 * slip,
                        "bonus_minus_impact": bonus - slip,
                    }
                    candidates.append(candidate)
            if candidates:
                feasible = [q for q in candidates if q["liquidator_profit_usd"] > 0]
                if pair_rule == "bonus_minus_impact" and feasible:
                    best = max(feasible, key=lambda q: (q["bonus_minus_impact"], q["liquidator_profit_usd"]))
                else:
                    best = max(candidates, key=lambda q: q["liquidator_profit_usd"])
            unknown += int(unavailable)
            if best is None or best["liquidator_profit_usd"] <= 0:
                if best is not None:
                    rejected += 1
                    rejected_debt_usd += w_debt
                continue
            ct, dt = best["token_in_address"], best["token_out_address"]
            w.debt[dt] = max(0.0, w.debt[dt] - best["repaid_debt_usd"] / prices[dt])
            w.collateral[ct] = max(0.0, w.collateral[ct] - best["sold_usd"] / prices[ct])
            if best["pool_address"]:
                sold[best["pool_address"]] = best["cumulative_after_snapshot_usd"]
            transactions.append(best)
            repaid += best["repaid_debt_usd"]
            profit_sum += best["liquidator_profit_usd"]
            executed += 1
        rounds_out.append({
            "cf_rule": cf_rule,
            "round": number,
            "eligible_wallets": eligible,
            "executed_liquidations": executed,
            "wallets_with_unknown_candidates": unknown,
            "wallets_without_profitable_supported_quote": rejected,
            "unprofitable_supported_debt_usd": rejected_debt_usd,
            "repaid_debt_usd": repaid,
            "liquidator_profit_usd": profit_sum,
        })
        if not executed:
            break

    total_repaid = sum(r["repaid_debt_usd"] for r in rounds_out)
    r1_repaid = rounds_out[0]["repaid_debt_usd"] if rounds_out else 0.0
    post_bad_debt_usd = sum(max(0.0, debt_value(w, prices) - collateral_value(w, prices)) for w in state)
    slips = [tx["slippage_pct"] for tx in transactions]
    summary_row = {
        "cf_rule": cf_rule,
        "rounds": len(rounds_out),
        "initial_eligible_wallets": len(active),
        "executed_liquidations": len(transactions),
        "round_1_liquidations": rounds_out[0]["executed_liquidations"] if rounds_out else 0,
        "round_ge_2_liquidations": sum(r["executed_liquidations"] for r in rounds_out[1:]),
        "round_1_repaid_usd": float(r1_repaid),
        "simulated_repaid_usd": float(total_repaid),
        "cascade_amplification_factor": float(total_repaid / r1_repaid) if r1_repaid > 0 else None,
        "liquidator_profit_usd": float(sum(r["liquidator_profit_usd"] for r in rounds_out)),
        "mean_slippage_pct": float(np.mean(slips)) if slips else 0.0,
        "max_slippage_pct": float(np.max(slips)) if slips else 0.0,
        "r1_unprofitable_supported_wallets": rounds_out[0]["wallets_without_profitable_supported_quote"] if rounds_out else 0,
        "r1_unprofitable_supported_debt_usd": float(rounds_out[0]["unprofitable_supported_debt_usd"]) if rounds_out else 0.0,
        "outside_quote_domain_attempts": int(outside_domain_attempts),
        "post_execution_bad_debt_usd": float(post_bad_debt_usd),
        "post_execution_bad_debt_ratio_pct": float(post_bad_debt_usd / initial_cohort_debt_usd * 100.0)
        if initial_cohort_debt_usd > 0
        else 0.0,
    }
    return summary_row, rounds_out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--output-dir", type=Path, default=Path("outputs/n5_q1_q3_empirical_20261026"))
    ap.add_argument("--overwrite", action="store_true", help="Overwrite existing output directory")
    args = ap.parse_args()
    out = args.output_dir
    if out.exists() and not args.overwrite:
        raise FileExistsError(f"Output directory exists: {out}")

    aave_root = Path("data/processed/aave")
    comp_root = Path("outputs/n5_completion_20261026")
    compound_root = Path("outputs/compound_consolidated_A_B1_B2_20261025_complete")
    config_path = Path("config/snapshots.json")
    scenarios_path = Path("config/scenarios.yaml")
    n4_cfg_path = Path("config/n4_research.json")
    tol_path = Path("config/n5_tolerances.json")
    kpi_dict_path = Path("kpi_dictionary.csv")

    input_paths = [
        config_path,
        scenarios_path,
        n4_cfg_path,
        tol_path,
        kpi_dict_path,
        aave_root / "positions_long.parquet",
        aave_root / "reserve_config.parquet",
        aave_root / "oracle_prices.parquet",
        aave_root / "emode_membership.parquet",
        aave_root / "protocol_reconciliation.parquet",
        comp_root / "history_validation/account_reference_supplemented.parquet",
        comp_root / "emode_replay/wallet_equity_strata.parquet",
        comp_root / "calibration_market/mc_scenario_inputs.parquet",
        comp_root / "approved_package_with27/execution_summary.parquet",
        comp_root / "approved_package_with27/execution_rounds.parquet",
        comp_root / "approved_package_with27/execution_transactions.parquet",
        comp_root / "approved_package_with27/curve_points_with_quantization_audit.parquet",
        Path("outputs/n3_priority2_quote_grid_v1/n3_multivolume_rpc_verified_only.parquet"),
        Path("outputs/n3_priority2_quote_grid_v2_with_27/n3_targeted_27_rpc_verified_only.parquet"),
        compound_root / "merge_report.json",
        compound_root / "n3_compound_positions_long.parquet",
        compound_root / "n3_compound_account_reference.parquet",
        compound_root / "n3_compound_reserve_config.parquet",
        compound_root / "n3_compound_overlap_wallets.parquet",
        Path(__file__),
    ]
    hashes_before = {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in input_paths}

    snapshots_cfg = json.loads(config_path.read_text(encoding="utf-8"))
    scenarios_cfg = yaml.safe_load(scenarios_path.read_text(encoding="utf-8"))["scenarios"]
    n4_cfg = json.loads(n4_cfg_path.read_text(encoding="utf-8"))
    tolerances = load_tolerances(tol_path)
    depeg_symbols = {s.upper() for s in n4_cfg["scenario_overrides"]["depeg_assets"]}
    eth_symbols = {"ETH", "WETH", "STETH", "WSTETH", "WEETH", "CBETH", "RETH"} | depeg_symbols

    raw = {
        name: read_rows(aave_root / f"{name}.parquet")
        for name in ["positions_long", "reserve_config", "oracle_prices", "emode_membership", "protocol_reconciliation"]
    }
    refs = pd.read_parquet(comp_root / "history_validation/account_reference_supplemented.parquet")
    equity_strata_df = pd.read_parquet(comp_root / "emode_replay/wallet_equity_strata.parquet")
    mc_shocks = pd.read_parquet(comp_root / "calibration_market/mc_scenario_inputs.parquet")
    exec_summary = pd.read_parquet(comp_root / "approved_package_with27/execution_summary.parquet")
    exec_rounds = pd.read_parquet(comp_root / "approved_package_with27/execution_rounds.parquet")
    exec_tx = pd.read_parquet(comp_root / "approved_package_with27/execution_transactions.parquet")
    curve_points = pd.read_parquet(comp_root / "approved_package_with27/curve_points_with_quantization_audit.parquet")
    all_quotes = pd.concat(
        [
            pd.read_parquet("outputs/n3_priority2_quote_grid_v1/n3_multivolume_rpc_verified_only.parquet"),
            pd.read_parquet("outputs/n3_priority2_quote_grid_v2_with_27/n3_targeted_27_rpc_verified_only.parquet"),
        ],
        ignore_index=True,
    )
    fees_df = all_quotes.groupby(KEYS)["fee_tier"].agg(["min", "max"]).reset_index()
    fee_lookup = {
        tuple(row[k] for k in KEYS): float(row["min"]) / 1e6
        for row in fees_df.to_dict("records")
        if pd.notna(row["min"])
    }

    out.mkdir(parents=True, exist_ok=args.overwrite)
    artifacts: dict[str, str] = {}

    def save_parquet(rel: str, df: pd.DataFrame, kind: str = "empirical_research_candidate") -> None:
        p = out / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        df.to_parquet(p, index=False)
        artifacts[rel] = kind

    def save_csv(rel: str, df: pd.DataFrame, kind: str = "empirical_research_candidate") -> None:
        p = out / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        df.to_csv(p, index=False, encoding="utf-8-sig")
        artifacts[rel] = kind

    def save_json(rel: str, obj: object, kind: str = "empirical_research_candidate") -> None:
        p = out / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(obj, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
        artifacts[rel] = kind

    mc_wallet_parts, mc_strata_parts, mc_matched_parts = [], [], []
    stress_loss_parts, stress_wallet_parts, stress_strata_parts, stress_matched_parts = [], [], [], []
    q2_cf_summary_rows, q2_cf_round_rows = [], []
    transition_parts, transition_summary_rows = [], []
    debtrank_reports = {}
    debtrank_node_parts = []
    portfolios = {}
    baselines = {}

    for sid, snap in snapshots_cfg["snapshots"].items():
        block = int(snap["block"])
        portfolio = prepare_portfolio(
            raw["positions_long"],
            raw["reserve_config"],
            raw["oracle_prices"],
            snapshot_block=block,
            emode_membership=raw["emode_membership"],
            depth_usd={r["token_address"]: 1e6 for r in raw["reserve_config"]},
        )
        _, totals = reconcile_debt(portfolio, raw["protocol_reconciliation"])
        pos_df = pd.DataFrame(portfolio.positions)
        selected_pos, _, _, gate = evaluate_research_gate(pos_df, refs, totals, tolerances)
        if gate["status"] != "passed":
            raise ValueError(f"{sid}: input gate failed")

        keep_wallets = set(selected_pos["wallet_address"])
        cohort_wallets = [w for w in portfolio.wallets if w.wallet in keep_wallets]
        portfolios[sid] = (portfolio, cohort_wallets, selected_pos)

        # Baseline wallet KPIs aligned with N4 equity strata
        eq_sub = equity_strata_df[equity_strata_df["snapshot_block"].eq(block)].copy()
        eq_sub["snapshot_id"] = sid
        eq_sub = eq_sub.rename(
            columns={
                "total_collateral_usd": "collateral_usd",
                "total_debt_usd": "debt_usd",
                "initial_equity_usd": "net_equity_usd",
                "leverage": "effective_leverage",
            }
        )
        eq_sub["dist_to_liq_pct"] = (
            1.0 - 1.0 / eq_sub["health_factor"].where(eq_sub["health_factor"].gt(0.0))
        ) * 100.0
        baselines[sid] = eq_sub
        save_parquet(f"{sid}/baseline_wallet_kpis.parquet", eq_sub)

        # Graph & conservation for snapshot cohort
        edges = build_aave_graph(portfolio)
        conservation = graph_conservation_report(edges, pos_df)
        if not conservation["passed"].all():
            raise ValueError(f"{sid}: graph conservation failed")
        node_metrics = graph_node_metrics(edges)
        save_parquet(f"{sid}/graph_network_edges_candidate.parquet", edges)
        save_parquet(f"{sid}/graph_node_metrics_candidate.parquet", node_metrics)

        # 1. Empirical Q1 on 10,000 Calibrated Student-t Copula Scenarios
        w_mc, s_mc, m_mc = _vectorized_mc_q1(selected_pos, eq_sub, mc_shocks, depeg_symbols, eth_symbols)
        mc_wallet_parts.append(w_mc)
        mc_strata_parts.append(s_mc)
        mc_matched_parts.append(m_mc)

        # 2. Empirical Q1, Q2 (4 Close Factor Counterfactual Policies) & Q3 on the 19 Primary Scoped Execution Runs
        routes = {}
        for identity, g in curve_points[curve_points["snapshot_id"].eq(sid)].groupby(KEYS):
            _, _, pool, ct, dt = identity
            routes[(ct, dt)] = {
                "pool_address": pool,
                "curve": ExecutionCurve(g),
                "fee_rate": fee_lookup[identity],
            }
        initial_cohort_debt_usd = float(eq_sub["debt_usd"].sum())
        tx_snap = exec_tx[exec_tx["snapshot_id"].eq(sid) & exec_tx["variant"].eq("primary")]
        snap_losses = []
        for scen_id, params in scenarios_cfg.items():
            scen_params = dict(params or {}, depeg_assets=list(depeg_symbols))
            multipliers = {t: _asset_multiplier(portfolio.symbols[t].upper(), scen_params) for t in portfolio.assets}
            shocked_prices = {t: a.price_usd * multipliers[t] for t, a in portfolio.assets.items()}

            # Q2: Run all 4 Close Factor counterfactual rules (baseline_aave, fixed_50, fixed_100, dynamic_linear)
            for cf_rule in POLICIES:
                cf_sum, cf_rnds = _simulate_q2_cf_policy(
                    cohort_wallets,
                    portfolio.assets,
                    multipliers,
                    routes,
                    cf_rule=cf_rule,
                    allowed_routes=set(routes),
                    initial_cohort_debt_usd=initial_cohort_debt_usd,
                )
                if cf_rule == "baseline_aave":
                    ref_row = exec_summary[
                        exec_summary["snapshot_id"].eq(sid)
                        & exec_summary["scenario_id"].eq(scen_id)
                        & exec_summary["variant"].eq("primary")
                    ].iloc[0]
                    if not (
                        cf_sum["executed_liquidations"] == int(ref_row["executed_liquidations"])
                        and np.isclose(cf_sum["simulated_repaid_usd"], float(ref_row["simulated_repaid_usd"]), rtol=1e-9, atol=1e-6)
                    ):
                        raise ValueError(f"Q2 baseline_aave mismatch with approved primary run at {sid} {scen_id}")
                q2_cf_summary_rows.append({
                    "snapshot_id": sid,
                    "snapshot_block": block,
                    "scenario_id": scen_id,
                    **cf_sum,
                })
                for r_row in cf_rnds:
                    q2_cf_round_rows.append({
                        "snapshot_id": sid,
                        "snapshot_block": block,
                        "scenario_id": scen_id,
                        **r_row,
                    })

            tx_scen = tx_snap[tx_snap["scenario_id"].eq(scen_id)]
            repaid_by_wallet_token: dict[tuple[str, str], float] = {}
            seized_by_wallet_token: dict[tuple[str, str], float] = {}
            first_round_by_wallet: dict[str, int] = {}
            tx_records = []
            for row in tx_scen.itertuples():
                w_addr = str(row.wallet_address)
                ct = str(row.token_in_address)
                dt = str(row.token_out_address)
                repaid_tokens = float(row.repaid_debt_usd) / shocked_prices[dt]
                seized_tokens = float(row.sold_usd) / shocked_prices[ct]
                repaid_by_wallet_token[(w_addr, dt)] = repaid_by_wallet_token.get((w_addr, dt), 0.0) + repaid_tokens
                seized_by_wallet_token[(w_addr, ct)] = seized_by_wallet_token.get((w_addr, ct), 0.0) + seized_tokens
                if w_addr not in first_round_by_wallet or int(row.round) < first_round_by_wallet[w_addr]:
                    first_round_by_wallet[w_addr] = int(row.round)
                tx_records.append({"wallet": w_addr, "round": int(row.round)})

            post_wallets = []
            for w in cohort_wallets:
                new_coll = {
                    t: max(0.0, q - seized_by_wallet_token.get((w.wallet, t), 0.0))
                    for t, q in w.collateral.items()
                }
                new_debt = {
                    t: max(0.0, q - repaid_by_wallet_token.get((w.wallet, t), 0.0))
                    for t, q in w.debt.items()
                }
                post_wallets.append(
                    Wallet(
                        wallet=w.wallet,
                        collateral=new_coll,
                        debt=new_debt,
                        collateral_enabled=dict(w.collateral_enabled),
                        effective_lt=dict(w.effective_lt),
                        effective_bonus=dict(w.effective_bonus),
                    )
                )

            pseudo_result = CascadeResult(
                rounds=[],
                summary={"scenario_id": scen_id, "snapshot_id": sid, "snapshot_block": block},
                wallets=post_wallets,
                prices=shocked_prices,
                transactions=tx_records,
            )
            loss_df = wallet_loss_rows(pseudo_result, eq_sub)
            loss_df["snapshot_id"] = sid
            loss_df["snapshot_block"] = block
            snap_losses.append(loss_df)

            # Q3 M15: Cascade transitions (for both H3 threshold 1.10 and KPI dictionary threshold 1.05)
            trans_110 = cascade_transitions(pseudo_result, eq_sub, portfolio.assets, healthy_threshold=1.10)
            trans_105 = cascade_transitions(pseudo_result, eq_sub, portfolio.assets, healthy_threshold=1.05)
            trans_110["first_executed_round"] = pd.to_numeric(trans_110["first_executed_round"], errors="coerce")
            trans_110["snapshot_id"] = sid
            trans_110["snapshot_block"] = block
            trans_110["scenario_id"] = scen_id
            trans_110["initially_healthy_105"] = trans_105["initially_healthy"]
            trans_110["initially_healthy_sequential_execution_105"] = trans_105["initially_healthy_sequential_execution"]
            is_liq_or_exec = trans_110["final_liquidatable"] | trans_110["executed"]
            trans_110["healthy_110_to_liq"] = trans_110["initially_healthy"] & is_liq_or_exec
            trans_110["healthy_105_to_liq"] = trans_110["initially_healthy_105"] & is_liq_or_exec
            transition_parts.append(trans_110)

            n_h110 = int(trans_110["initially_healthy"].sum())
            n_h105 = int(trans_110["initially_healthy_105"].sum())
            n_h110_to_liq = int(trans_110["healthy_110_to_liq"].sum())
            n_h105_to_liq = int(trans_110["healthy_105_to_liq"].sum())
            transition_summary_rows.append({
                "snapshot_id": sid,
                "snapshot_block": block,
                "scenario_id": scen_id,
                "cohort_wallets": int(len(trans_110)),
                "initially_healthy_110_wallets": n_h110,
                "initially_healthy_105_wallets": n_h105,
                "final_liquidatable_wallets": int(trans_110["final_liquidatable"].sum()),
                "executed_wallets": int(trans_110["executed"].sum()),
                "multi_round_executed_wallets": int(
                    tx_scen.loc[tx_scen["round"].ge(2), "wallet_address"].nunique()
                ),
                "round_ge_2_executed_wallets": int(
                    trans_110["first_executed_round"].fillna(0).ge(2).sum()
                ),
                "healthy_110_to_liq_wallets": n_h110_to_liq,
                "healthy_110_to_liq_rate_pct": float(n_h110_to_liq / n_h110 * 100.0) if n_h110 > 0 else 0.0,
                "healthy_105_to_liq_wallets": n_h105_to_liq,
                "healthy_105_to_liq_rate_pct": float(n_h105_to_liq / n_h105 * 100.0) if n_h105 > 0 else 0.0,
                "initially_healthy_110_seq_executed": int(
                    trans_110["initially_healthy_sequential_execution"].sum()
                ),
                "initially_healthy_105_seq_executed": int(
                    trans_110["initially_healthy_sequential_execution_105"].sum()
                ),
                "oracle_feedback_mode": "fixed_shocked_oracle_bounded_scope",
            })

        snap_losses_df = pd.concat(snap_losses, ignore_index=True)
        stress_loss_parts.append(snap_losses_df)
        w_st, s_st, m_st = summarize_q1(snap_losses_df.drop(columns=["snapshot_id", "snapshot_block"]))
        w_st.insert(0, "snapshot_block", block)
        w_st.insert(0, "snapshot_id", sid)
        s_st.insert(0, "snapshot_block", block)
        s_st.insert(0, "snapshot_id", sid)
        m_st.insert(0, "snapshot_block", block)
        m_st.insert(0, "snapshot_id", sid)
        stress_wallet_parts.append(w_st)
        stress_strata_parts.append(s_st)
        stress_matched_parts.append(m_st)

        # 3. Q3 M18: Token-Protocol Bipartite Exposure Network DebtRank
        # Build token -> protocol -> borrower group exposure network for top 10 tokens + Aave protocol node
        token_agg = (
            selected_pos.assign(
                coll_usd=selected_pos["collateral_amount"] * selected_pos["price_usd"] * selected_pos["is_collateral_enabled"],
                debt_usd=selected_pos["debt_amount"] * selected_pos["price_usd"],
            )
            .groupby(["token_address", "symbol"], as_index=False)[["coll_usd", "debt_usd"]]
            .sum()
        )
        token_agg["total_exposure_usd"] = token_agg["coll_usd"] + token_agg["debt_usd"]
        top_tokens = token_agg.sort_values("total_exposure_usd", ascending=False).head(10).reset_index(drop=True)
        node_names = [f"token:{r.symbol}" for r in top_tokens.itertuples()] + [
            "cohort:looping_wallets",
            "cohort:non_looping_wallets",
            "protocol:aave_v3_ethereum",
        ]
        n_nodes = len(node_names)
        w_mat = np.zeros((n_nodes, n_nodes), dtype=np.float64)
        loop_set = set(eq_sub.loc[eq_sub["is_recursive_loop"], "wallet_address"])
        pos_with_loop = selected_pos.assign(
            is_loop=selected_pos["wallet_address"].isin(loop_set),
            coll_usd=selected_pos["collateral_amount"] * selected_pos["price_usd"] * selected_pos["is_collateral_enabled"],
            debt_usd=selected_pos["debt_amount"] * selected_pos["price_usd"],
        )
        idx_loop = n_nodes - 3
        idx_nonloop = n_nodes - 2
        idx_proto = n_nodes - 1
        for t_idx, r in enumerate(top_tokens.itertuples()):
            sub_t = pos_with_loop[pos_with_loop["token_address"].eq(r.token_address)]
            c_loop = float(sub_t.loc[sub_t["is_loop"], "coll_usd"].sum())
            c_non = float(sub_t.loc[~sub_t["is_loop"], "coll_usd"].sum())
            if c_loop > 0:
                w_mat[t_idx, idx_loop] = c_loop
            if c_non > 0:
                w_mat[t_idx, idx_nonloop] = c_non
        d_loop = float(pos_with_loop.loc[pos_with_loop["is_loop"], "debt_usd"].sum())
        d_non = float(pos_with_loop.loc[~pos_with_loop["is_loop"], "debt_usd"].sum())
        w_mat[idx_loop, idx_proto] = d_loop
        w_mat[idx_nonloop, idx_proto] = d_non

        capacities = np.array(
            [max(1.0, float(r.total_exposure_usd)) for r in top_tokens.itertuples()]
            + [
                max(1.0, float(eq_sub.loc[eq_sub["is_recursive_loop"], "collateral_usd"].sum())),
                max(1.0, float(eq_sub.loc[~eq_sub["is_recursive_loop"], "collateral_usd"].sum())),
                max(1.0, float(eq_sub["debt_usd"].sum())),
            ],
            dtype=np.float64,
        )
        init_distress = np.zeros(n_nodes, dtype=np.float64)
        for t_idx, r in enumerate(top_tokens.itertuples()):
            sym_u = str(r.symbol).upper()
            if sym_u in depeg_symbols:
                init_distress[t_idx] = 0.24  # combined_eth20_depeg5: 1 - 0.8 * 0.95 = 0.24
            elif sym_u in eth_symbols:
                init_distress[t_idx] = 0.20  # eth_drop_20
        dr = debt_rank(w_mat, capacities, init_distress)
        debtrank_reports[sid] = {
            "snapshot_id": sid,
            "snapshot_block": block,
            "scenario_reference": "combined_eth20_depeg5",
            "nodes": node_names,
            **dr,
        }
        for node_name, cap, h0, hf_final, h_inc in zip(
            node_names, capacities, init_distress, dr["final_distress"], dr["incremental_distress"]
        ):
            debtrank_node_parts.append({
                "snapshot_id": sid,
                "snapshot_block": block,
                "node_id": node_name,
                "capacity_usd": float(cap),
                "initial_distress": float(h0),
                "final_distress": float(hf_final),
                "incremental_distress": float(h_inc),
            })

    # 4. Q3 M16: Compound III Cross-Protocol Exposure, Price Stress & Explicit Base Supply Withdrawals
    _, comp_tables = inspect_compound(compound_root)
    comp_exposures = adapt_compound_partial(comp_tables["positions"], comp_tables["config"])
    comp_exposures["coverage_status"] = "complete_target_queries"
    save_parquet("compound/exposures_complete.parquet", comp_exposures)

    comp_stress_parts = []
    for scen_id in ["eth_drop_20", "depeg_5", "combined_eth20_depeg5", "combined_extreme"]:
        params = dict(scenarios_cfg[scen_id] or {}, depeg_assets=list(depeg_symbols))
        returns = {}
        for r in comp_exposures[["snapshot_block", "token_address", "token_symbol"]].drop_duplicates().itertuples():
            mult = _asset_multiplier(str(r.token_symbol).upper(), params)
            returns[(int(r.snapshot_block), str(r.token_address))] = mult - 1.0
        st_df = compound_price_stress(comp_exposures, returns)
        st_df.insert(0, "scenario_id", scen_id)
        comp_stress_parts.append(st_df)
    comp_stress_all = pd.concat(comp_stress_parts, ignore_index=True)
    save_parquet("q3_compound_price_stress.parquet", comp_stress_all)

    # Identify Aave-liquidatable overlap wallets under combined_eth20_depeg5 and apply explicit_base_supply_withdrawal
    all_cohort_wallets = set(equity_strata_df["wallet_address"].str.lower())
    overlap_wallets_in_cohort = set(comp_exposures["wallet_address"].str.lower()) & all_cohort_wallets
    withdrawal_ledgers, withdrawal_summaries = [], []
    for frac, label in [(0.25, "withdraw_25pct"), (0.50, "withdraw_50pct"), (1.00, "withdraw_100pct")]:
        changed_bal, ledger = explicit_base_supply_withdrawal(
            comp_exposures, overlap_wallets_in_cohort, fraction=frac, scenario_id=f"overlap_{label}"
        )
        withdrawal_ledgers.append(ledger)
        if not ledger.empty:
            merged_l = ledger.merge(
                comp_exposures[["snapshot_block", "market_id", "wallet_address", "token_address", "asset_role", "price_usd"]],
                on=["snapshot_block", "market_id", "wallet_address", "token_address", "asset_role"],
                how="left",
            )
            merged_l["withdrawn_usd"] = (-merged_l["quantity_delta"]) * merged_l["price_usd"]
            for (sb, mkt), grp in merged_l.groupby(["snapshot_block", "market_id"]):
                withdrawal_summaries.append({
                    "scenario_id": f"overlap_{label}",
                    "withdrawal_fraction": frac,
                    "snapshot_block": int(sb),
                    "market_id": str(mkt),
                    "overlap_wallets_withdrawing": int(grp["wallet_address"].nunique()),
                    "withdrawn_tokens": float((-grp["quantity_delta"]).sum()),
                    "withdrawn_usd": float(grp["withdrawn_usd"].sum()),
                })
    withdrawal_ledger_df = pd.concat(withdrawal_ledgers, ignore_index=True)
    withdrawal_summary_df = pd.DataFrame(withdrawal_summaries)
    save_parquet("q3_explicit_withdrawal_ledger.parquet", withdrawal_ledger_df)
    save_csv("q3_explicit_withdrawal_summary.csv", withdrawal_summary_df)

    # Concatenate and save all Q1 & Q3 empirical tables
    q1_mc_wallets = pd.concat(mc_wallet_parts, ignore_index=True)
    q1_mc_strata = pd.concat(mc_strata_parts, ignore_index=True)
    q1_mc_matched = pd.concat(mc_matched_parts, ignore_index=True)

    q1_stress_losses = pd.concat(stress_loss_parts, ignore_index=True)
    q1_stress_wallets = pd.concat(stress_wallet_parts, ignore_index=True)
    q1_stress_strata = pd.concat(stress_strata_parts, ignore_index=True)
    q1_stress_matched = pd.concat(stress_matched_parts, ignore_index=True)

    q3_transitions_df = pd.concat(transition_parts, ignore_index=True)
    q3_transitions_summary_df = pd.DataFrame(transition_summary_rows)
    q3_debtrank_nodes_df = pd.concat([ pd.DataFrame(debtrank_node_parts) ], ignore_index=True)

    q2_cf_summary_df = pd.DataFrame(q2_cf_summary_rows)
    q2_cf_rounds_df = pd.DataFrame(q2_cf_round_rows)
    q2_cf_policy_agg_df = (
        q2_cf_summary_df.groupby(["snapshot_id", "snapshot_block", "cf_rule"], as_index=False)
        .agg(
            scenarios=("scenario_id", "count"),
            total_executed_liquidations=("executed_liquidations", "sum"),
            total_round_1_liquidations=("round_1_liquidations", "sum"),
            total_round_ge_2_liquidations=("round_ge_2_liquidations", "sum"),
            mean_round_1_repaid_usd=("round_1_repaid_usd", "mean"),
            mean_simulated_repaid_usd=("simulated_repaid_usd", "mean"),
            mean_cascade_amplification=("cascade_amplification_factor", "mean"),
            mean_liquidator_profit_usd=("liquidator_profit_usd", "mean"),
            mean_slippage_pct=("mean_slippage_pct", "mean"),
            max_slippage_pct=("max_slippage_pct", "max"),
            total_outside_quote_domain_attempts=("outside_quote_domain_attempts", "sum"),
            mean_post_execution_bad_debt_usd=("post_execution_bad_debt_usd", "mean"),
            mean_post_execution_bdr_pct=("post_execution_bad_debt_ratio_pct", "mean"),
        )
    )

    save_parquet("q2_cf_counterfactual_summary.parquet", q2_cf_summary_df)
    save_csv("q2_cf_counterfactual_summary.csv", q2_cf_summary_df)
    save_parquet("q2_cf_counterfactual_rounds.parquet", q2_cf_rounds_df)
    save_csv("q2_cf_counterfactual_rounds.csv", q2_cf_rounds_df)
    save_csv("q2_cf_policy_aggregate.csv", q2_cf_policy_agg_df)

    save_parquet("q1_mc10000_wallet_risk.parquet", q1_mc_wallets)
    save_parquet("q1_mc10000_equity_strata.parquet", q1_mc_strata)
    save_parquet("q1_mc10000_matched_comparison.parquet", q1_mc_matched)
    save_csv("q1_mc10000_matched_comparison.csv", q1_mc_matched)
    save_csv("q1_mc10000_equity_strata.csv", q1_mc_strata)

    save_parquet("q1_wallet_losses.parquet", q1_stress_losses)
    save_parquet("q1_wallet_risk.parquet", q1_stress_wallets)
    save_parquet("q1_equity_strata.parquet", q1_stress_strata)
    save_parquet("q1_matched_comparison.parquet", q1_stress_matched)
    save_csv("q1_stress_matched_comparison.csv", q1_stress_matched)

    save_parquet("q3_cascade_transitions.parquet", q3_transitions_df)
    save_csv("q3_cascade_transitions_summary.csv", q3_transitions_summary_df)
    save_json("q3_debtrank.json", debtrank_reports)
    save_parquet("q3_debtrank_nodes.parquet", q3_debtrank_nodes_df)
    save_csv("q3_debtrank_nodes.csv", q3_debtrank_nodes_df)

    # Copy approved execution summary & rounds for M06, M07, M10-M14 mapping verification
    exec_primary_summary = exec_summary[exec_summary["variant"].eq("primary")].copy()
    exec_primary_rounds = exec_rounds[exec_rounds["variant"].eq("primary")].copy()
    save_parquet("execution/cascade_summary.parquet", exec_primary_summary)
    save_parquet("execution/cascade_rounds.parquet", exec_primary_rounds)

    # Update KPI mapping table so M01-M18 point to the empirical candidate artifacts
    mapping = kpi_mapping(pd.read_csv(kpi_dict_path, encoding="utf-8-sig"), ("A", "B1", "B2"))
    empirical_updates = [
        ("M05", "q1_wallet_risk.parquet", "probability_bad_debt", "Empirical cohort (50,311 wallets) across 19 stress executions and 10,000 copula draws"),
        ("M06", "q1_wallet_losses.parquet", "bad_debt_usd", "Per-wallet and system bad debt across post-execution stress and MC scenarios"),
        ("M07", "q1_wallet_losses.parquet", "bad_debt_ratio_t0", "Explicit initial t0 debt denominator per wallet"),
        ("M08", "q1_wallet_losses.parquet", "loss_per_100k_equity", "Scaled per 100k positive initial equity; 5 nonpositive-equity wallets kept null"),
        ("M09", "q1_wallet_risk.parquet", "es99_loss_per_100k", "Empirical 99% Expected Shortfall per wallet and common-support equity stratum"),
        ("M10", "execution/cascade_rounds.parquet", "repaid_debt_usd", "Bounded 3-route execution with all 27 targeted quotes verified"),
        ("M11", "q3_cascade_transitions.parquet", "initial_hf,final_hf", "Fixed shocked oracle in bounded scope; DEX slippage tracked per transaction"),
        ("M12", "execution/cascade_rounds.parquet", "eligible_wallets,executed_liquidations", "Eligible vs executed liquidations per round"),
        ("M13", "execution/cascade_summary.parquet", "simulated_repaid_usd", "Cumulative multi-round repayment under approved package"),
        ("M14", "execution/cascade_rounds.parquet", "wallets_without_profitable_supported_quote", "Unprofitable supported candidates tracked separately from unknown routes"),
        ("M15", "q3_cascade_transitions.parquet", "initially_healthy_sequential_execution", "Evaluated at both HF>=1.10 (H3) and HF>=1.05 (dictionary)"),
        ("M16", "compound/exposures_complete.parquet", "value_usd", "Complete 6-shard Compound III overlap + price stress & explicit withdrawal ledger"),
        ("M17", "A|B1|B2/graph_network_edges_candidate.parquet", "weight_usd_t0", "Per-token exposure conservation verified across A, B1, B2"),
        ("M18", "q3_debtrank_nodes.parquet", "incremental_distress", "Single-activation DebtRank on token-cohort-protocol network"),
    ]
    for kpi_id, art, fld, lim in empirical_updates:
        sel = mapping["kpi_id"].eq(kpi_id)
        mapping.loc[sel, ["implementation_status", "candidate_artifact", "candidate_field", "limitation"]] = [
            "implemented_empirical_candidate",
            art,
            fld,
            lim,
        ]
    save_csv("kpi_mapping.csv", mapping)
    kpi_checks = validate_kpi_artifacts(mapping, out)
    save_json("kpi_artifact_qa.json", kpi_checks)
    if any(r["artifact_check"] == "failed" for r in kpi_checks):
        raise ValueError(f"KPI artifact validation failed: {kpi_checks}")

    summary = {
        "status": "empirical_q1_q3_completed_on_approved_cohort",
        "cohort_wallets": int(len(equity_strata_df)),
        "snapshots": {
            sid: int(len(baselines[sid])) for sid in snapshots_cfg["snapshots"]
        },
        "q1_mc_scenarios": int(len(mc_shocks)),
        "q1_stress_scenarios": int(len(scenarios_cfg)),
        "q1_mc_matched_comparison": q1_mc_matched.to_dict("records"),
        "q1_stress_matched_comparison": q1_stress_matched.to_dict("records"),
        "q2_cf_counterfactual_runs": int(len(q2_cf_summary_df)),
        "q2_cf_policy_aggregate": q2_cf_policy_agg_df.to_dict("records"),
        "q3_compound_complete_exposure_rows": int(len(comp_exposures)),
        "q3_compound_overlap_cohort_wallets": int(len(overlap_wallets_in_cohort)),
        "q3_withdrawal_summary": withdrawal_summary_df.to_dict("records"),
        "q3_debtrank_scores": {
            sid: {
                "unweighted_incremental_score": r["unweighted_incremental_score"],
                "capacity_weighted_incremental_score": r["capacity_weighted_incremental_score"],
            }
            for sid, r in debtrank_reports.items()
        },
        "kpi_checks_passed": int(sum(r["artifact_check"] != "failed" for r in kpi_checks)),
        "kpi_checks_total": int(len(kpi_checks)),
        "limitations": (
            "Q1 MC uses 10,000 Student-t copula scenarios from 365 daily market returns (max 1-day ETH drop -14.34%, depeg -0.56%), "
            "complemented by 19 post-execution tail stress scenarios (-10% to -50% ETH, -1% to -10% depeg). "
            "Q3 bounded execution holds oracle prices fixed after initial shock (no endogenous DEX->oracle feedback). "
            "Compound III Q3 evaluates Comet price-only eligibility and explicit base-supply withdrawals without inventing collateral absorption."
        ),
    }
    save_json("summary.json", summary)

    if any(hashlib.sha256(p.read_bytes()).hexdigest() != h for p, h in hashes_before.items()):
        raise ValueError("Source file modified during empirical Q1/Q3 run")
    write_manifest(out, input_paths, artifacts, summary)
    if verify_manifest(out):
        raise ValueError("Manifest verification failed")
    print(json.dumps({
        "status": summary["status"],
        "cohort_wallets": summary["cohort_wallets"],
        "kpi_checks_passed": f"{summary['kpi_checks_passed']}/{summary['kpi_checks_total']}",
        "q3_debtrank_scores": summary["q3_debtrank_scores"],
    }, indent=2))


if __name__ == "__main__":
    main()
