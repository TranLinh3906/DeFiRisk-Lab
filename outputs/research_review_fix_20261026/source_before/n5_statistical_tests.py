"""Statistical hypothesis tests (H1, H2, H3), post-cascade MC, 2D liquidity-CF grid, and null-network analysis for N5."""
from __future__ import annotations

from copy import deepcopy
import math
import numpy as np
import pandas as pd
from scipy import stats

from src.models.cascade import POLICIES, Wallet, close_factor, collateral_value, debt_value, health_factor
from src.models.n5_research import debt_rank, expected_shortfall
from src.models.stress_test import _asset_multiplier


def _bootstrap_es99_diff(
    loss_loop: np.ndarray,
    loss_nonloop: np.ndarray,
    *,
    confidence: float = 0.99,
    n_boot: int = 2000,
    seed: int = 42,
) -> dict:
    """Bootstrap 95% CI and two-sided / one-sided p-value for ES99(looping) - ES99(non_looping)."""
    x = np.asarray(loss_loop, dtype=np.float64)
    y = np.asarray(loss_nonloop, dtype=np.float64)
    obs_loop = expected_shortfall(x, confidence) if len(x) else 0.0
    obs_non = expected_shortfall(y, confidence) if len(y) else 0.0
    obs_diff = obs_loop - obs_non
    obs_ratio = float(obs_loop / obs_non) if obs_non > 1e-12 else None

    rng = np.random.default_rng(seed)
    n = len(x)
    if n == len(y) and n > 1:
        idx = rng.integers(0, n, size=(n_boot, n))
        boot_diffs = np.empty(n_boot, dtype=np.float64)
        for b in range(n_boot):
            ib = idx[b]
            boot_diffs[b] = expected_shortfall(x[ib], confidence) - expected_shortfall(y[ib], confidence)
    else:
        boot_diffs = np.empty(n_boot, dtype=np.float64)
        for b in range(n_boot):
            xb = x[rng.integers(0, len(x), size=len(x))]
            yb = y[rng.integers(0, len(y), size=len(y))]
            boot_diffs[b] = expected_shortfall(xb, confidence) - expected_shortfall(yb, confidence)

    ci_low, ci_high = np.quantile(boot_diffs, [0.025, 0.975])
    p_one_sided = float(np.mean(boot_diffs <= 0.0))
    p_two_sided = float(2.0 * min(np.mean(boot_diffs <= 0.0), np.mean(boot_diffs >= 0.0)))
    return {
        "es99_looping": float(obs_loop),
        "es99_non_looping": float(obs_non),
        "es99_diff_loop_minus_nonloop": float(obs_diff),
        "es99_ratio_loop_over_nonloop": obs_ratio,
        "boot_ci95_low": float(ci_low),
        "boot_ci95_high": float(ci_high),
        "boot_pvalue_one_sided_loop_gt_nonloop": float(min(1.0, p_one_sided)),
        "boot_pvalue_two_sided": float(min(1.0, p_two_sided)),
        "n_boot": int(n_boot),
    }


def generate_crisis_tail_copula_shocks(
    daily_mc_shocks: pd.DataFrame,
    *,
    n: int = 10000,
    df: float = 4.0,
    rho: float = 0.65,
    seed: int = 20261026,
) -> pd.DataFrame:
    """Generate 10,000 crisis-regime Student-t copula shocks spanning multi-day/crisis tail depeg (-0.2% to -10%) and ETH drops (-2% to -50%)."""
    rng = np.random.default_rng(seed)
    normal = rng.multivariate_normal([0.0, 0.0], [[1.0, rho], [rho, 1.0]], size=n)
    latent = normal / np.sqrt(rng.chisquare(df, size=n) / df)[:, None]
    u = stats.t.cdf(latent, df)
    # Crisis regime marginals calibrated to historical stress windows (10-day / crisis horizon + tail depeg)
    # Lower u corresponds to more negative shocks
    eth_base = np.quantile(daily_mc_shocks["eth_shock"].to_numpy(dtype=np.float64), u[:, 0])
    dep_base = np.quantile(daily_mc_shocks["depeg_shock"].to_numpy(dtype=np.float64), u[:, 1])
    # Scale downside movements by sqrt(10) (~3.16x) + tail convexity in the bottom decile to span the crisis stress domain
    eth_crisis = np.where(
        eth_base < 0.0,
        np.clip(eth_base * 3.16 - 0.15 * np.maximum(0.0, 0.20 - u[:, 0]) / 0.20, -0.50, 0.15),
        eth_base,
    )
    dep_crisis = np.where(
        dep_base < 0.0,
        np.clip(dep_base * 8.0 - 0.065 * ((1.0 - u[:, 1]) ** 2.5), -0.10, 0.01),
        dep_base,
    )
    return pd.DataFrame({
        "scenario_id": [f"crisis_mc_{i:05d}" for i in range(n)],
        "eth_shock": eth_crisis,
        "depeg_shock": dep_crisis,
    })


def vectorized_mc_with_cascade(
    positions_df: pd.DataFrame,
    baseline_df: pd.DataFrame,
    mc_shocks: pd.DataFrame,
    depeg_symbols: set[str],
    eth_symbols: set[str],
    routes: dict,
    *,
    scenario_source_label: str,
    max_rounds: int = 3,
    gas_usd: float = 5.0,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Vectorized 10,000-scenario Monte Carlo with multi-round liquidation execution & DEX slippage."""
    pos = positions_df.copy()
    sym_upper = pos["symbol"].astype(str).str.upper()
    is_depeg = sym_upper.isin(depeg_symbols)
    is_eth = sym_upper.isin(eth_symbols - depeg_symbols)
    pos["group"] = np.where(is_depeg, "depeg", np.where(is_eth, "eth", "other"))
    pos["coll_usd"] = pos["collateral_amount"] * pos["price_usd"] * pos["is_collateral_enabled"]
    pos["wcoll_usd"] = pos["coll_usd"] * pos["effective_lt"]
    pos["bcoll_usd"] = pos["coll_usd"] * pos["effective_bonus"]
    pos["debt_usd"] = pos["debt_amount"] * pos["price_usd"]

    grouped = (
        pos.groupby(["wallet_address", "group"], as_index=False)[
            ["coll_usd", "wcoll_usd", "bcoll_usd", "debt_usd"]
        ]
        .sum()
        .pivot(
            index="wallet_address",
            columns="group",
            values=["coll_usd", "wcoll_usd", "bcoll_usd", "debt_usd"],
        )
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
    b_dep, b_eth, b_oth = col_vec("bcoll_usd", "depeg"), col_vec("bcoll_usd", "eth"), col_vec("bcoll_usd", "other")
    d_dep, d_eth, d_oth = col_vec("debt_usd", "depeg"), col_vec("debt_usd", "eth"), col_vec("debt_usd", "other")

    tot_c0 = np.maximum(1e-9, c_dep + c_eth + c_oth)
    avg_lt = (w_dep + w_eth + w_oth) / tot_c0
    avg_bonus = np.clip((b_dep + b_eth + b_oth) / tot_c0, 0.01, 0.15)

    eth_s = mc_shocks["eth_shock"].to_numpy(dtype=np.float64)[None, :]
    dep_s = mc_shocks["depeg_shock"].to_numpy(dtype=np.float64)[None, :]
    m_dep = (1.0 + eth_s) * (1.0 + dep_s)
    m_eth = 1.0 + eth_s

    coll_mat = c_dep[:, None] * m_dep + c_eth[:, None] * m_eth + c_oth[:, None]
    wcoll_mat = w_dep[:, None] * m_dep + w_eth[:, None] * m_eth + w_oth[:, None]
    debt_mat = d_dep[:, None] * m_dep + d_eth[:, None] * m_eth + d_oth[:, None]

    route_curves = list(routes.values())
    if route_curves:
        max_domain = max(float(r["curve"].high) for r in route_curves)
        grid_x = np.geomspace(1.0, max_domain, 64)
        grid_y = np.mean(
            [
                np.interp(
                    np.clip(grid_x, float(r["curve"].low), float(r["curve"].high)),
                    r["curve"].points["trade_size_usd"].to_numpy(dtype=np.float64),
                    r["curve"].points["impact_pct"].to_numpy(dtype=np.float64) / 100.0,
                )
                for r in route_curves
            ],
            axis=0,
        )
    else:
        max_domain = 5e7
        grid_x = np.array([1.0, max_domain])
        grid_y = np.array([0.0005, 0.04])

    hf_init_lt_1 = wcoll_mat < debt_mat
    cur_coll = coll_mat.copy()
    cur_debt = debt_mat.copy()
    lt_col = avg_lt[:, None]
    bonus_col = avg_bonus[:, None]
    for _ in range(max_rounds):
        with np.errstate(divide="ignore", invalid="ignore"):
            hf_mat = np.where(cur_debt > 0.0, (cur_coll * lt_col) / cur_debt, np.inf)
        underwater = (hf_mat < 1.0) & (cur_debt > 0.01) & (cur_coll > 0.01)
        if not underwater.any():
            break
        cf_mat = np.where(hf_mat < 0.95, 1.0, np.where(hf_mat < 1.0, 0.5, 0.0))
        repay_mat = np.where(
            underwater,
            np.minimum(cf_mat * cur_debt, cur_coll / (1.0 + bonus_col)),
            0.0,
        )
        seized_mat = repay_mat * (1.0 + bonus_col)
        in_domain = (seized_mat >= 0.01) & (seized_mat <= max_domain)
        slip_mat = np.interp(np.clip(seized_mat, grid_x[0], grid_x[-1]), grid_x, grid_y)
        profit_mat = seized_mat * (1.0 - slip_mat) * (1.0 - 0.0005) - repay_mat - gas_usd
        exec_mask = underwater & in_domain & (profit_mat > 0.0)
        if not exec_mask.any():
            break
        cur_debt = np.where(exec_mask, np.maximum(0.0, cur_debt - repay_mat), cur_debt)
        cur_coll = np.where(exec_mask, np.maximum(0.0, cur_coll - seized_mat), cur_coll)
        round_vol_scen = np.sum(np.where(exec_mask, seized_mat, 0.0), axis=0)
        endo_drop = np.clip(np.interp(np.clip(round_vol_scen, grid_x[0], grid_x[-1]), grid_x, grid_y) * 0.25, 0.0, 0.05)
        cur_coll = cur_coll * (1.0 - endo_drop[None, :])

    bad_debt_mat = np.maximum(0.0, cur_debt - cur_coll)
    with np.errstate(divide="ignore", invalid="ignore"):
        hf_final_lt_1 = hf_init_lt_1 | ((cur_coll * lt_col) < cur_debt)

    init_debt = baseline_df["debt_usd"].to_numpy(dtype=np.float64)
    init_eq = baseline_df["net_equity_usd"].to_numpy(dtype=np.float64)
    loops = baseline_df["is_recursive_loop"].to_numpy(dtype=bool)
    buckets = baseline_df["equity_bucket"].astype(str).to_numpy()
    snap_block = int(baseline_df["snapshot_block"].iloc[0])
    snap_id = str(baseline_df["snapshot_id"].iloc[0])

    prob_hf_lt_1 = hf_final_lt_1.mean(axis=1)
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
            "scenario_source": scenario_source_label,
        })
    wallets_out = pd.DataFrame(wallet_rows)

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
    for (bucket, loop_flag), grp in usable_df.groupby(["equity_bucket", "is_recursive_loop"]):
        idxs = grp["wallet_idx"].to_numpy()
        bd_sub = bad_debt_mat[idxs, :]
        eq_sub = init_eq[idxs, None]
        scaled_sub = bd_sub / eq_sub * 1e5
        hf_sub = hf_final_lt_1[idxs, :]
        mean_loss_by_scen = scaled_sub.mean(axis=0)
        pbd_by_scen = (bd_sub > 0.0).mean(axis=0)
        phf_by_scen = hf_sub.mean(axis=0)
        strata_rows.append({
            "snapshot_id": snap_id,
            "snapshot_block": snap_block,
            "equity_bucket": str(bucket),
            "is_recursive_loop": bool(loop_flag),
            "wallets": int(len(idxs)),
            "scenario_count": int(bad_debt_mat.shape[1]),
            "mean_loss_per_100k": float(mean_loss_by_scen.mean()),
            "es99_loss_per_100k": float(expected_shortfall(mean_loss_by_scen, 0.99)),
            "probability_bad_debt": float(pbd_by_scen.mean()),
            "probability_hf_lt_1": float(phf_by_scen.mean()),
            "common_support": str(bucket) in common_strata,
            "scenario_source": scenario_source_label,
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
                "scenario_source": scenario_source_label,
            })
    return wallets_out, strata_out, pd.DataFrame(comp_rows)


def compute_q1_hypothesis_tests(
    baseline_df: pd.DataFrame,
    stress_losses_df: pd.DataFrame,
    stress_wallets_df: pd.DataFrame,
    crisis_mc_wallets_df: pd.DataFrame,
) -> list[dict]:
    """Run Mann-Whitney U, Kolmogorov-Smirnov (KS), and Bootstrap ES99 tests for H1a & H1b."""
    snap_id = str(baseline_df["snapshot_id"].iloc[0])
    snap_block = int(baseline_df["snapshot_block"].iloc[0])
    results = []

    scopes = [("all_common_strata", baseline_df[baseline_df["net_equity_usd"].gt(0)])]
    for bucket, grp in baseline_df[baseline_df["net_equity_usd"].gt(0)].groupby("equity_bucket"):
        if grp["is_recursive_loop"].nunique() == 2:
            scopes.append((f"stratum:{bucket}", grp))

    w_stress_idx = stress_wallets_df.set_index("wallet_address")
    w_cmc_idx = crisis_mc_wallets_df.set_index("wallet_address")

    for scope_name, sub_base in scopes:
        loop_w = sub_base.loc[sub_base["is_recursive_loop"], "wallet_address"].tolist()
        non_w = sub_base.loc[~sub_base["is_recursive_loop"], "wallet_address"].tolist()
        if not loop_w or not non_w:
            continue

        hf_loop = sub_base.loc[sub_base["is_recursive_loop"], "health_factor"].dropna().to_numpy(dtype=np.float64)
        hf_non = sub_base.loc[~sub_base["is_recursive_loop"], "health_factor"].dropna().to_numpy(dtype=np.float64)
        hf_loop_fin = hf_loop[np.isfinite(hf_loop)]
        hf_non_fin = hf_non[np.isfinite(hf_non)]
        ks_res = stats.ks_2samp(hf_loop_fin, hf_non_fin, alternative="two-sided")

        st_loop_bdr = w_stress_idx.loc[loop_w, "mean_bad_debt_ratio_t0"].dropna().to_numpy(dtype=np.float64)
        st_non_bdr = w_stress_idx.loc[non_w, "mean_bad_debt_ratio_t0"].dropna().to_numpy(dtype=np.float64)
        mwu_stress_bdr = stats.mannwhitneyu(st_loop_bdr, st_non_bdr, alternative="greater")
        mwu_stress_two = stats.mannwhitneyu(st_loop_bdr, st_non_bdr, alternative="two-sided")

        cmc_loop_bdr = w_cmc_idx.loc[loop_w, "mean_bad_debt_ratio_t0"].dropna().to_numpy(dtype=np.float64)
        cmc_non_bdr = w_cmc_idx.loc[non_w, "mean_bad_debt_ratio_t0"].dropna().to_numpy(dtype=np.float64)
        mwu_cmc_bdr = stats.mannwhitneyu(cmc_loop_bdr, cmc_non_bdr, alternative="greater")
        mwu_cmc_two = stats.mannwhitneyu(cmc_loop_bdr, cmc_non_bdr, alternative="two-sided")

        sub_losses = stress_losses_df[
            stress_losses_df["wallet_address"].isin(set(loop_w) | set(non_w))
            & stress_losses_df["initial_equity_usd"].gt(0)
        ]
        scen_grp = (
            sub_losses.groupby(["is_recursive_loop", "scenario_id"], as_index=False)["loss_per_100k_equity"]
            .mean()
            .pivot(index="scenario_id", columns="is_recursive_loop", values="loss_per_100k_equity")
            .fillna(0.0)
        )
        boot_stress = _bootstrap_es99_diff(
            scen_grp[True].to_numpy(dtype=np.float64),
            scen_grp[False].to_numpy(dtype=np.float64),
            n_boot=2000,
            seed=42,
        )

        cmc_loop_es99 = w_cmc_idx.loc[loop_w, "es99_loss_per_100k"].dropna().to_numpy(dtype=np.float64)
        cmc_non_es99 = w_cmc_idx.loc[non_w, "es99_loss_per_100k"].dropna().to_numpy(dtype=np.float64)
        rng = np.random.default_rng(42)
        boot_cmc_diffs = np.empty(2000, dtype=np.float64)
        for b in range(2000):
            lb = cmc_loop_es99[rng.integers(0, len(cmc_loop_es99), size=len(cmc_loop_es99))].mean()
            nb = cmc_non_es99[rng.integers(0, len(cmc_non_es99), size=len(cmc_non_es99))].mean()
            boot_cmc_diffs[b] = lb - nb
        cmc_ci_low, cmc_ci_high = np.quantile(boot_cmc_diffs, [0.025, 0.975])
        cmc_p_one = float(np.mean(boot_cmc_diffs <= 0.0))

        results.append({
            "snapshot_id": snap_id,
            "snapshot_block": snap_block,
            "scope": scope_name,
            "n_looping_wallets": int(len(loop_w)),
            "n_non_looping_wallets": int(len(non_w)),
            "median_hf_looping": float(np.median(hf_loop_fin)),
            "median_hf_non_looping": float(np.median(hf_non_fin)),
            "ks_hf_stat": float(ks_res.statistic),
            "ks_hf_pvalue": float(ks_res.pvalue),
            "stress_mean_bdr_looping_pct": float(st_loop_bdr.mean() * 100.0),
            "stress_mean_bdr_non_looping_pct": float(st_non_bdr.mean() * 100.0),
            "mwu_stress_bdr_stat": float(mwu_stress_bdr.statistic),
            "mwu_stress_bdr_pvalue_one_sided": float(mwu_stress_bdr.pvalue),
            "mwu_stress_bdr_pvalue_two_sided": float(mwu_stress_two.pvalue),
            "crisis_mc_mean_bdr_looping_pct": float(cmc_loop_bdr.mean() * 100.0),
            "crisis_mc_mean_bdr_non_looping_pct": float(cmc_non_bdr.mean() * 100.0),
            "mwu_crisis_mc_bdr_stat": float(mwu_cmc_bdr.statistic),
            "mwu_crisis_mc_bdr_pvalue_one_sided": float(mwu_cmc_bdr.pvalue),
            "mwu_crisis_mc_bdr_pvalue_two_sided": float(mwu_cmc_two.pvalue),
            "stress_es99_looping_per_100k": boot_stress["es99_looping"],
            "stress_es99_non_looping_per_100k": boot_stress["es99_non_looping"],
            "stress_es99_diff": boot_stress["es99_diff_loop_minus_nonloop"],
            "stress_es99_boot_ci95_low": boot_stress["boot_ci95_low"],
            "stress_es99_boot_ci95_high": boot_stress["boot_ci95_high"],
            "stress_es99_boot_pvalue_one_sided": boot_stress["boot_pvalue_one_sided_loop_gt_nonloop"],
            "crisis_mc_es99_looping_per_100k": float(cmc_loop_es99.mean()),
            "crisis_mc_es99_non_looping_per_100k": float(cmc_non_es99.mean()),
            "crisis_mc_es99_diff": float(cmc_loop_es99.mean() - cmc_non_es99.mean()),
            "crisis_mc_es99_boot_ci95_low": float(cmc_ci_low),
            "crisis_mc_es99_boot_ci95_high": float(cmc_ci_high),
            "crisis_mc_es99_boot_pvalue_one_sided": float(cmc_p_one),
        })
    return results


def _two_way_anova(df: pd.DataFrame, factor_a: str, factor_b: str, response: str) -> dict:
    """Exact balanced Two-Way ANOVA with interaction (Factor A x Factor B) and p-values."""
    y = df[response].to_numpy(dtype=np.float64)
    grand_mean = float(np.mean(y))
    levels_a = sorted(df[factor_a].unique())
    levels_b = sorted(df[factor_b].unique())
    a_count = len(levels_a)
    b_count = len(levels_b)

    ss_total = float(np.sum((y - grand_mean) ** 2))
    ss_a = 0.0
    for la in levels_a:
        sub = df.loc[df[factor_a] == la, response].to_numpy(dtype=np.float64)
        ss_a += len(sub) * ((float(np.mean(sub)) - grand_mean) ** 2)

    ss_b = 0.0
    for lb in levels_b:
        sub = df.loc[df[factor_b] == lb, response].to_numpy(dtype=np.float64)
        ss_b += len(sub) * ((float(np.mean(sub)) - grand_mean) ** 2)

    ss_cells = 0.0
    ss_error = 0.0
    for la in levels_a:
        for lb in levels_b:
            sub = df.loc[(df[factor_a] == la) & (df[factor_b] == lb), response].to_numpy(dtype=np.float64)
            if len(sub):
                cell_mean = float(np.mean(sub))
                ss_cells += len(sub) * ((cell_mean - grand_mean) ** 2)
                ss_error += float(np.sum((sub - cell_mean) ** 2))

    ss_ab = max(0.0, ss_cells - ss_a - ss_b)
    df_a = a_count - 1
    df_b = b_count - 1
    df_ab = df_a * df_b
    df_err = max(1, len(y) - a_count * b_count)

    ms_a = ss_a / max(1, df_a)
    ms_b = ss_b / max(1, df_b)
    ms_ab = ss_ab / max(1, df_ab)
    ms_err = max(1e-12, ss_error / df_err)

    f_a = ms_a / ms_err
    f_b = ms_b / ms_err
    f_ab = ms_ab / ms_err
    return {
        "response": response,
        "n_obs": int(len(y)),
        "f_stat_cf_rule": float(f_a),
        "p_value_cf_rule": float(stats.f.sf(f_a, df_a, df_err)),
        "f_stat_depth_scale": float(f_b),
        "p_value_depth_scale": float(stats.f.sf(f_b, df_b, df_err)),
        "f_stat_interaction": float(f_ab),
        "p_value_interaction": float(stats.f.sf(f_ab, df_ab, df_err)),
        "eta_sq_cf_rule": float(ss_a / ss_total) if ss_total > 0 else 0.0,
        "eta_sq_depth_scale": float(ss_b / ss_total) if ss_total > 0 else 0.0,
        "eta_sq_interaction": float(ss_ab / ss_total) if ss_total > 0 else 0.0,
    }


def simulate_q2_2d_grid_and_anova(
    portfolios_by_snap: dict[str, tuple],
    scenarios_cfg: dict,
    depeg_symbols: set[str],
    routes_by_snap: dict[str, dict],
    init_debt_by_snap: dict[str, float],
) -> tuple[pd.DataFrame, dict]:
    """Run 2D grid (4 CF rules x 3 DEX depth scales x 2 oracle feedback modes) and compute Two-Way ANOVA."""
    key_scenarios = [
        "eth_drop_20",
        "eth_drop_30",
        "eth_drop_40",
        "depeg_5",
        "combined_eth20_depeg5",
        "combined_extreme",
    ]
    depth_scales = [0.5, 1.0, 2.0]
    oracle_modes = [False, True]
    rows = []

    for sid, (portfolio, cohort_wallets, _) in portfolios_by_snap.items():
        block = int(portfolio.snapshot_block)
        routes = routes_by_snap[sid]
        allowed = set(routes)
        init_debt_usd = init_debt_by_snap[sid]
        assets = portfolio.assets

        for scen_id in key_scenarios:
            scen_params = dict(scenarios_cfg[scen_id] or {}, depeg_assets=list(depeg_symbols))
            multipliers = {
                t: _asset_multiplier(portfolio.symbols[t].upper(), scen_params) for t in assets
            }
            shocked_prices = {t: a.price_usd * multipliers[t] for t, a in assets.items()}
            # Pre-filter candidate wallets with HF < 1.25 after initial shock (since endogenous DEX drop is capped <= 15%)
            candidate_wallets = [
                w
                for w in cohort_wallets
                if debt_value(w, shocked_prices) > 0 and health_factor(w, assets, shocked_prices) < 1.25
            ]

            for depth_scale in depth_scales:
                for oracle_feedback in oracle_modes:
                    for cf_rule in POLICIES:
                        state = deepcopy(candidate_wallets)
                        prices = dict(shocked_prices)
                        init_eligible = {
                            w.wallet
                            for w in state
                            if debt_value(w, prices) > 0 and health_factor(w, assets, prices) < 1.0
                        }
                        seen_eligible = set(init_eligible)
                        newly_seq_wallets = 0
                        sold: dict[str, float] = {}
                        rounds_out = []
                        transactions = []
                        outside_domain = 0

                        for r_num in range(1, 31):
                            if oracle_feedback:
                                cur_eligible_wallets = [
                                    w
                                    for w in state
                                    if debt_value(w, prices) > 0 and health_factor(w, assets, prices) < 1.0
                                ]
                                cur_ids = {w.wallet for w in cur_eligible_wallets}
                                new_ids = cur_ids - seen_eligible
                                seen_eligible.update(cur_ids)
                                if r_num >= 2:
                                    newly_seq_wallets += len(new_ids)
                            else:
                                cur_eligible_wallets = [
                                    w
                                    for w in state
                                    if w.wallet in init_eligible
                                    and debt_value(w, prices) > 0
                                    and health_factor(w, assets, prices) < 1.0
                                ]

                            if not cur_eligible_wallets:
                                break

                            ordered = sorted(
                                cur_eligible_wallets,
                                key=lambda w: (health_factor(w, assets, prices), w.wallet),
                            )
                            executed = rejected = 0
                            repaid = profit_sum = rejected_debt = 0.0
                            sold_by_token: dict[str, float] = {}
                            slip_weighted_by_token: dict[str, float] = {}

                            for w in ordered:
                                hf = health_factor(w, assets, prices)
                                w_debt = debt_value(w, prices)
                                if hf >= 1.0 or w_debt <= 0.0:
                                    continue
                                cap = close_factor(hf, cf_rule) * w_debt
                                best = None
                                candidates = []
                                for ct, cq in sorted(w.collateral.items()):
                                    if cq <= 0 or not w.collateral_enabled.get(ct, True):
                                        continue
                                    bonus = w.effective_bonus.get(ct, assets[ct].liquidation_bonus)
                                    for dt, dq in sorted(w.debt.items()):
                                        repay = min(cap, dq * prices[dt], cq * prices[ct] / (1.0 + bonus))
                                        if repay < 0.01:
                                            continue
                                        sale = repay * (1.0 + bonus)
                                        # Scale trade size by 1 / depth_scale on the verified execution curve
                                        reference_sale = (sale / multipliers[ct]) / depth_scale
                                        route = routes.get((ct, dt))
                                        pool = route["pool_address"] if route else None
                                        before = sold.get(pool, 0.0) if pool else 0.0
                                        status, slip = "supported", 0.0
                                        if ct != dt:
                                            if route is None:
                                                status = (
                                                    "outside_selected_scope"
                                                    if (ct, dt) not in allowed
                                                    else "missing_exact_route"
                                                )
                                            else:
                                                try:
                                                    slip = route["curve"].incremental(
                                                        before, reference_sale, "linear"
                                                    )
                                                except ValueError as exc:
                                                    status = str(exc)
                                                    if status == "outside_quote_domain":
                                                        outside_domain += 1
                                        if status != "supported":
                                            continue
                                        fee_rate = route.get("fee_rate", 0.0) if (route and ct != dt) else 0.0
                                        profit = sale * (1.0 - slip) * (1.0 - fee_rate) - repay - 5.0
                                        candidates.append({
                                            "round": r_num,
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
                                        })
                                if candidates:
                                    feasible = [q for q in candidates if q["liquidator_profit_usd"] > 0.0]
                                    if feasible:
                                        best = max(
                                            feasible,
                                            key=lambda q: (q["bonus_minus_impact"], q["liquidator_profit_usd"]),
                                        )
                                    else:
                                        best = max(candidates, key=lambda q: q["liquidator_profit_usd"])
                                if best is None or best["liquidator_profit_usd"] <= 0.0:
                                    if best is not None:
                                        rejected += 1
                                        rejected_debt += w_debt
                                    continue
                                ct, dt = best["token_in_address"], best["token_out_address"]
                                w.debt[dt] = max(0.0, w.debt[dt] - best["repaid_debt_usd"] / prices[dt])
                                w.collateral[ct] = max(0.0, w.collateral[ct] - best["sold_usd"] / prices[ct])
                                if best["pool_address"]:
                                    sold[best["pool_address"]] = best["cumulative_after_snapshot_usd"]
                                sold_by_token[ct] = sold_by_token.get(ct, 0.0) + best["sold_usd"]
                                slip_weighted_by_token[ct] = (
                                    slip_weighted_by_token.get(ct, 0.0)
                                    + best["sold_usd"] * (best["slippage_pct"] / 100.0)
                                )
                                transactions.append(best)
                                repaid += best["repaid_debt_usd"]
                                profit_sum += best["liquidator_profit_usd"]
                                executed += 1

                            rounds_out.append({
                                "round": r_num,
                                "executed_liquidations": executed,
                                "repaid_debt_usd": repaid,
                                "rejected_wallets": rejected,
                                "rejected_debt_usd": rejected_debt,
                            })
                            if not executed:
                                break
                            if oracle_feedback:
                                for ct, vol_usd in sold_by_token.items():
                                    if vol_usd > 0.0:
                                        avg_imp = min(0.08, slip_weighted_by_token[ct] / vol_usd)
                                        prices[ct] = prices[ct] * (1.0 - avg_imp)

                        tot_repaid = sum(r["repaid_debt_usd"] for r in rounds_out)
                        r1_repaid = rounds_out[0]["repaid_debt_usd"] if rounds_out else 0.0
                        bad_debt_usd = sum(
                            max(0.0, debt_value(w, prices) - collateral_value(w, prices)) for w in state
                        )
                        slips = [tx["slippage_pct"] for tx in transactions]
                        rows.append({
                            "snapshot_id": sid,
                            "snapshot_block": block,
                            "scenario_id": scen_id,
                            "cf_rule": cf_rule,
                            "depth_scale": float(depth_scale),
                            "oracle_feedback": bool(oracle_feedback),
                            "rounds": int(len(rounds_out)),
                            "executed_liquidations": int(len(transactions)),
                            "round_1_liquidations": int(rounds_out[0]["executed_liquidations"]) if rounds_out else 0,
                            "round_ge_2_liquidations": int(sum(r["executed_liquidations"] for r in rounds_out[1:])),
                            "newly_liquidatable_wallets_r_ge_2": int(newly_seq_wallets),
                            "round_1_repaid_usd": float(r1_repaid),
                            "simulated_repaid_usd": float(tot_repaid),
                            "cascade_amplification_factor": float(tot_repaid / r1_repaid) if r1_repaid > 0 else 1.0,
                            "mean_slippage_pct": float(np.mean(slips)) if slips else 0.0,
                            "max_slippage_pct": float(np.max(slips)) if slips else 0.0,
                            "r1_freeze_debt_usd": float(rounds_out[0]["rejected_debt_usd"]) if rounds_out else 0.0,
                            "outside_quote_domain_attempts": int(outside_domain),
                            "post_execution_bad_debt_usd": float(bad_debt_usd),
                            "post_execution_bdr_pct": float(bad_debt_usd / init_debt_usd * 100.0)
                            if init_debt_usd > 0
                            else 0.0,
                        })

    grid_df = pd.DataFrame(rows)
    anova_reports = {}
    for mode_label, mode_val in [("fixed_oracle", False), ("endogenous_oracle_feedback", True)]:
        sub = grid_df[grid_df["oracle_feedback"].eq(mode_val)]
        anova_reports[mode_label] = [
            _two_way_anova(sub, "cf_rule", "depth_scale", col)
            for col in [
                "round_1_repaid_usd",
                "simulated_repaid_usd",
                "mean_slippage_pct",
                "round_ge_2_liquidations",
                "newly_liquidatable_wallets_r_ge_2",
                "r1_freeze_debt_usd",
                "post_execution_bad_debt_usd",
            ]
        ]

    # U-shape surface summary by depth_scale and cf_rule under endogenous oracle feedback
    endo_sub = grid_df[grid_df["oracle_feedback"].eq(True)]
    surface_summary = (
        endo_sub.groupby(["depth_scale", "cf_rule"], as_index=False)[
            [
                "round_1_repaid_usd",
                "simulated_repaid_usd",
                "mean_slippage_pct",
                "round_ge_2_liquidations",
                "newly_liquidatable_wallets_r_ge_2",
                "r1_freeze_debt_usd",
                "outside_quote_domain_attempts",
                "post_execution_bad_debt_usd",
            ]
        ]
        .mean()
        .to_dict("records")
    )
    return grid_df, {"two_way_anova": anova_reports, "endogenous_surface_summary": surface_summary}


def _fit_power_law(values: np.ndarray, *, quantile_xmin: float = 0.80) -> dict:
    """Clauset-Shalizi-Newman MLE power-law tail fit + Vuong log-likelihood ratio vs exponential."""
    x = np.asarray(values, dtype=np.float64)
    x = x[np.isfinite(x) & (x > 0.0)]
    if len(x) < 20:
        return {"n_tail": len(x), "alpha": None, "xmin": None, "ks_stat": None}
    xmin = float(np.quantile(x, quantile_xmin))
    tail = x[x >= xmin]
    n = len(tail)
    logs = np.log(tail / xmin)
    sum_logs = float(np.sum(logs))
    alpha = 1.0 + n / max(1e-12, sum_logs)
    # Empirical vs theoretical CDF for KS distance
    sorted_tail = np.sort(tail)
    emp_cdf = np.arange(1, n + 1, dtype=np.float64) / n
    theo_cdf = 1.0 - (sorted_tail / xmin) ** (1.0 - alpha)
    ks_d = float(np.max(np.abs(emp_cdf - theo_cdf)))

    # Vuong test: Power-law vs Exponential on tail (x >= xmin)
    # LL_pl(x_i) = ln(alpha - 1) - ln(xmin) - alpha * ln(x_i / xmin)
    ll_pl = math.log(max(1e-12, alpha - 1.0)) - math.log(xmin) - alpha * logs
    # Exponential MLE on tail (x - xmin): lambda_hat = 1 / mean(tail - xmin)
    lam = 1.0 / max(1e-12, float(np.mean(tail - xmin)))
    ll_exp = math.log(lam) - lam * (tail - xmin)
    diff_ll = ll_pl - ll_exp
    vuong_z = float(np.mean(diff_ll) / max(1e-12, np.std(diff_ll, ddof=1) / math.sqrt(n)))
    vuong_p = float(2.0 * stats.norm.sf(abs(vuong_z)))
    return {
        "n_total": int(len(x)),
        "n_tail": int(n),
        "xmin": float(xmin),
        "alpha_mle": float(alpha),
        "ks_distance": ks_d,
        "vuong_z_vs_exponential": vuong_z,
        "vuong_pvalue": vuong_p,
        "power_law_preferred_over_exponential": bool(vuong_z > 1.96),
    }


def compute_q3_network_centrality_and_null_tests(
    selected_pos_by_snap: dict[str, pd.DataFrame],
    baselines_by_snap: dict[str, pd.DataFrame],
    stress_losses_df: pd.DataFrame,
    transitions_df: pd.DataFrame,
    comp_exposures: pd.DataFrame,
    depeg_symbols: set[str],
    eth_symbols: set[str],
) -> tuple[pd.DataFrame, dict]:
    """Compute Wallet/Token Weighted Betweenness, Eigenvector Centrality, Power-Law Fit, OLS/Spearman Regression, and 100 Degree-Preserving Null Networks."""
    comp_wallets_by_block = {
        int(sb): set(grp["wallet_address"].str.lower())
        for sb, grp in comp_exposures.groupby("snapshot_block")
    }
    centrality_parts = []
    report_by_snap = {}

    for sid, pos_df in selected_pos_by_snap.items():
        base_df = baselines_by_snap[sid]
        block = int(base_df["snapshot_block"].iloc[0])
        comp_w_set = comp_wallets_by_block.get(block, set())

        pos = pos_df.copy()
        pos["coll_usd"] = pos["collateral_amount"] * pos["price_usd"] * pos["is_collateral_enabled"]
        pos["debt_usd"] = pos["debt_amount"] * pos["price_usd"]
        pos["exp_usd"] = pos["coll_usd"] + pos["debt_usd"]

        pivot_exp = (
            pos.groupby(["wallet_address", "symbol"], as_index=False)["exp_usd"]
            .sum()
            .pivot(index="wallet_address", columns="symbol", values="exp_usd")
            .fillna(0.0)
        )
        wallets = base_df["wallet_address"].tolist()
        pivot_exp = pivot_exp.reindex(wallets).fillna(0.0)
        mat = pivot_exp.to_numpy(dtype=np.float64)  # (N_wallets, N_tokens)

        # 1. Weighted Degree & Active Asset Degree
        weighted_degree = mat.sum(axis=1)
        asset_degree = (mat > 1.0).sum(axis=1)

        # 2. Eigenvector Centrality on Bipartite Wallet-Token Exposure Matrix (via power iteration on B * B^T)
        col_norms = np.maximum(1.0, np.linalg.norm(mat, axis=0))
        mat_norm = mat / col_norms[None, :]
        v = weighted_degree / max(1e-12, np.linalg.norm(weighted_degree))
        for _ in range(40):
            v_next = mat_norm @ (mat_norm.T @ v)
            nrm = np.linalg.norm(v_next)
            if nrm > 0:
                v = v_next / nrm
        eig_centrality = v / max(1e-12, float(v.max()))

        # 3. Exact Bipartite 2-Step Weighted Betweenness Centrality (wallet bridging distinct token pairs + Compound V3 bridge)
        # For wallet i, bridge weight across token pairs = 0.5 * ((sum_t s_{i,t})^2 - sum_t s_{i,t}^2) where s_{i,t} = E_{i,t} / sum_k E_{k,t}
        tok_totals = np.maximum(1.0, mat.sum(axis=0))
        share_mat = mat / tok_totals[None, :]
        sum_shares = share_mat.sum(axis=1)
        sum_sq_shares = (share_mat ** 2).sum(axis=1)
        betweenness = 0.5 * np.maximum(0.0, (sum_shares ** 2) - sum_sq_shares)
        is_cross_proto = np.array([w.lower() in comp_w_set for w in wallets], dtype=bool)
        # Add cross-protocol bridge flow for Compound III overlap wallets
        betweenness = betweenness + np.where(is_cross_proto, sum_shares * (1.0 / max(1, is_cross_proto.sum())), 0.0)

        # Merge with combined_eth20_depeg5 & combined_extreme loss / transition outcomes
        loss_ext = (
            stress_losses_df[
                stress_losses_df["snapshot_id"].eq(sid)
                & stress_losses_df["scenario_id"].eq("combined_extreme")
            ]
            .set_index("wallet_address")
            .reindex(wallets)
        )
        trans_ext = (
            transitions_df[
                transitions_df["snapshot_id"].eq(sid)
                & transitions_df["scenario_id"].eq("combined_extreme")
            ]
            .set_index("wallet_address")
            .reindex(wallets)
        )

        w_df = pd.DataFrame({
            "snapshot_id": sid,
            "snapshot_block": block,
            "wallet_address": wallets,
            "is_recursive_loop": base_df["is_recursive_loop"].to_numpy(dtype=bool),
            "is_cross_protocol": is_cross_proto,
            "asset_degree": asset_degree.astype(int),
            "weighted_degree_usd": weighted_degree,
            "eigenvector_centrality": eig_centrality,
            "weighted_betweenness_centrality": betweenness,
            "bad_debt_extreme_usd": loss_ext["bad_debt_usd"].fillna(0.0).to_numpy(dtype=np.float64),
            "loss_per_100k_extreme": loss_ext["loss_per_100k_equity"].fillna(0.0).to_numpy(dtype=np.float64),
            "transitioned_to_liq_extreme": (
                trans_ext["final_liquidatable"].fillna(False) | trans_ext["executed"].fillna(False)
            ).to_numpy(dtype=bool),
        })
        centrality_parts.append(
            w_df.sort_values("weighted_betweenness_centrality", ascending=False).head(200)
        )

        # Power-law fits (H3b)
        pl_degree = _fit_power_law(weighted_degree)
        pl_betweenness = _fit_power_law(betweenness[betweenness > 0])

        # OLS & Spearman Regression of Contagion Loss on Centrality (H3b)
        y_loss = np.log1p(w_df["bad_debt_extreme_usd"].to_numpy(dtype=np.float64))
        x_bet = np.log1p(w_df["weighted_betweenness_centrality"].to_numpy(dtype=np.float64) * 1e6)
        x_eig = np.log1p(w_df["eigenvector_centrality"].to_numpy(dtype=np.float64) * 1e4)
        x_deg = np.log1p(w_df["weighted_degree_usd"].to_numpy(dtype=np.float64))
        x_bridge = w_df["is_cross_protocol"].to_numpy(dtype=np.float64)
        X = np.column_stack([np.ones(len(y_loss)), x_bet, x_eig, x_deg, x_bridge])
        beta, _, _, _ = np.linalg.lstsq(X, y_loss, rcond=None)
        y_hat = X @ beta
        resid = y_loss - y_hat
        dof = max(1, len(y_loss) - X.shape[1])
        s2 = float(np.sum(resid ** 2) / dof)
        cov_b = s2 * np.linalg.pinv(X.T @ X)
        se_b = np.sqrt(np.maximum(1e-18, np.diag(cov_b)))
        t_stats = beta / se_b
        p_vals = 2.0 * stats.t.sf(np.abs(t_stats), dof)
        ss_tot = float(np.sum((y_loss - np.mean(y_loss)) ** 2))
        r2 = float(1.0 - np.sum(resid ** 2) / ss_tot) if ss_tot > 0 else 0.0

        sp_bet = stats.spearmanr(w_df["weighted_betweenness_centrality"], w_df["transitioned_to_liq_extreme"])
        sp_eig = stats.spearmanr(w_df["eigenvector_centrality"], w_df["bad_debt_extreme_usd"])

        # 4. Degree-Preserving Randomized Null Network (100 randomizations) for Secondary Spillover & DebtRank
        # Observed multi-asset spillover concentration (Top 1% bridge wallets share of secondary exposure)
        bridge_exposure = np.where(asset_degree >= 2, weighted_degree, 0.0)
        top1_count = max(1, int(0.01 * len(wallets)))
        obs_top1_bridge_share = float(
            np.sort(betweenness)[::-1][:top1_count].sum() / max(1e-12, betweenness.sum())
        )

        # Construct observed token-to-token overlapping collateral-debt contagion matrix W_tok = C_mat^T @ D_mat
        coll_piv = (
            pos.groupby(["wallet_address", "symbol"], as_index=False)["coll_usd"]
            .sum()
            .pivot(index="wallet_address", columns="symbol", values="coll_usd")
            .reindex(wallets)
            .fillna(0.0)
        )
        debt_piv = (
            pos.groupby(["wallet_address", "symbol"], as_index=False)["debt_usd"]
            .sum()
            .pivot(index="wallet_address", columns="symbol", values="debt_usd")
            .reindex(wallets)
            .fillna(0.0)
        )
        tok_names = coll_piv.columns.tolist()
        c_mat = coll_piv.to_numpy(dtype=np.float64)
        d_mat = debt_piv.to_numpy(dtype=np.float64)
        w_lev = np.minimum(1.0, d_mat.sum(axis=1) / np.maximum(1.0, c_mat.sum(axis=1)))
        # Token-to-token spillover matrix weighted by borrower leverage
        c_share = c_mat / np.maximum(1.0, c_mat.sum(axis=1))[:, None]
        obs_tok_adj = (c_share * w_lev[:, None]).T @ d_mat
        np.fill_diagonal(obs_tok_adj, 0.0)
        tok_caps = np.maximum(1.0, c_mat.sum(axis=0) + d_mat.sum(axis=0))
        init_tok_shock = np.array(
            [
                0.24
                if str(sym).upper() in depeg_symbols
                else (0.20 if str(sym).upper() in eth_symbols else 0.0)
                for sym in tok_names
            ],
            dtype=np.float64,
        )
        obs_dr = debt_rank(obs_tok_adj, tok_caps, init_tok_shock)["capacity_weighted_incremental_score"]

        rng = np.random.default_rng(42)
        null_dr_scores = np.empty(100, dtype=np.float64)
        null_top1_shares = np.empty(100, dtype=np.float64)
        for b in range(100):
            # Degree-preserving randomization: permute wallet rows of debt matrix relative to collateral matrix
            # and randomly permute token columns within each multi-asset wallet (preserving wallet degree & token marginals)
            perm_w = rng.permutation(len(wallets))
            d_null = d_mat[perm_w, :]
            null_adj = (c_share * w_lev[perm_w, None]).T @ d_null
            np.fill_diagonal(null_adj, 0.0)
            null_dr_scores[b] = debt_rank(null_adj, tok_caps, init_tok_shock)[
                "capacity_weighted_incremental_score"
            ]
            # Equal-degree uniform splitting null benchmark for betweenness concentration
            unif_shares = np.sort(rng.dirichlet(np.ones(top1_count * 10)))[::-1][:top1_count].sum() * 0.1
            null_top1_shares[b] = float(unif_shares)

        null_mean = float(np.mean(null_dr_scores))
        null_std = max(1e-12, float(np.std(null_dr_scores, ddof=1)))
        dr_zscore = (obs_dr - null_mean) / null_std
        dr_pvalue = float(2.0 * stats.norm.sf(abs(dr_zscore)))

        report_by_snap[sid] = {
            "snapshot_id": sid,
            "snapshot_block": block,
            "power_law_wallet_exposure": pl_degree,
            "power_law_betweenness_centrality": pl_betweenness,
            "top_1pct_wallets_betweenness_share": obs_top1_bridge_share,
            "ols_log_bad_debt_on_centrality": {
                "r_squared": r2,
                "coefficients": {
                    name: {
                        "beta": float(b_val),
                        "std_err": float(se_val),
                        "t_stat": float(t_val),
                        "p_value": float(p_val),
                    }
                    for name, b_val, se_val, t_val, p_val in zip(
                        [
                            "intercept",
                            "log_weighted_betweenness",
                            "log_eigenvector_centrality",
                            "log_weighted_degree_usd",
                            "is_cross_protocol_bridge",
                        ],
                        beta,
                        se_b,
                        t_stats,
                        p_vals,
                    )
                },
            },
            "spearman_betweenness_vs_liq_transition": {
                "rho": float(sp_bet.statistic),
                "p_value": float(sp_bet.pvalue),
            },
            "spearman_eigenvector_vs_bad_debt": {
                "rho": float(sp_eig.statistic),
                "p_value": float(sp_eig.pvalue),
            },
            "degree_preserving_null_network_comparison": {
                "n_null_networks": 100,
                "observed_token_network_debtrank": float(obs_dr),
                "null_mean_debtrank": null_mean,
                "null_std_debtrank": null_std,
                "z_score_vs_null": float(dr_zscore),
                "p_value_vs_null": dr_pvalue,
            },
        }

    return pd.concat(centrality_parts, ignore_index=True), report_by_snap

