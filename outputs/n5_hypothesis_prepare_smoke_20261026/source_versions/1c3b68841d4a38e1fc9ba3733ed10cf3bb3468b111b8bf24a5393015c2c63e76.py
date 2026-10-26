"""Pre-event calibration, paired designs, shape tests and temporal validation."""
from bisect import bisect_left
import math
import numpy as np
import pandas as pd
from scipy import stats
from src.models.n5_research import expected_shortfall


def past_returns(levels, cutoff, horizon=1):
    p = levels.sort_values('date').copy()
    p['date'] = pd.to_datetime(p.date, utc=True)
    cutoff = pd.Timestamp(cutoff)
    if cutoff.tzinfo is None or horizon < 1 or p.date.duplicated().any():
        raise ValueError('Invalid date/cutoff/horizon')
    for name in ['eth_quote_utc', 'steth_quote_utc']:
        times = pd.to_datetime(p[name], utc=True)
        if (times > cutoff).any() or (times > p.date + pd.Timedelta(days=1) - pd.Timedelta(seconds=1)).any():
            raise ValueError('Future calibration quote')
    values = p[['eth_usd', 'steth_eth']].to_numpy(float)
    if not np.isfinite(values).all() or (values <= 0).any():
        raise ValueError('Invalid market levels')
    # Every horizon must consist of consecutive calendar observations.
    complete = p.date.diff(horizon).eq(pd.Timedelta(days=horizon))
    ret = p[['date']].copy()
    ret['eth_return'] = p.eth_usd.pct_change(horizon, fill_method=None)
    ret['depeg_return'] = p.steth_eth.pct_change(horizon, fill_method=None)
    return ret[complete].reset_index(drop=True)


def scenarios_from_returns(returns, n=10000, *, mode='t4', seed=42):
    from src.models.n5_research_protocol import calibrated_scenarios
    if mode == 't4':
        return calibrated_scenarios(returns, n=n, seed=seed, df=4)[0]
    values = returns[['eth_return', 'depeg_return']].to_numpy(float)
    if len(values) < 20 or not np.isfinite(values).all():
        raise ValueError('Insufficient calibration sample')
    rng = np.random.default_rng(seed)
    if mode == 'paired_bootstrap':
        samples = values[rng.integers(0, len(values), n)]
    elif mode == 'gaussian':
        ranks = np.column_stack([stats.rankdata(values[:, i]) for i in range(2)])
        scores = stats.norm.ppf((ranks - .5) / len(values))
        rho = float(np.clip(np.corrcoef(scores.T)[0, 1], -.999, .999))
        uniforms = stats.norm.cdf(rng.multivariate_normal([0, 0], [[1, rho], [rho, 1]], n))
        samples = np.column_stack([np.quantile(values[:, i], uniforms[:, i]) for i in range(2)])
    else:
        raise ValueError('Unknown scenario distribution')
    return pd.DataFrame(dict(scenario_id=[f'mc_{i:05}' for i in range(n)],
                              eth_shock=samples[:, 0], depeg_shock=samples[:, 1]))


def equity_pairs(baseline, caliper=.1):
    """1:1 equity matching without replacement; does not condition on leverage/HF mediators."""
    usable = baseline[baseline.net_equity_usd.gt(0)].copy()
    usable['log_equity'] = np.log(usable.net_equity_usd)
    controls = sorted(zip(usable.loc[~usable.is_recursive_loop, 'log_equity'],
                          usable.loc[~usable.is_recursive_loop, 'wallet_address']))
    treated = usable[usable.is_recursive_loop].sort_values(['log_equity', 'wallet_address'])
    rows = []
    for r in treated.itertuples():
        if not controls:
            break
        k = bisect_left(controls, (r.log_equity, ''))
        options = [i for i in [k - 1, k] if 0 <= i < len(controls)]
        choice = min(options, key=lambda i: (abs(controls[i][0] - r.log_equity), controls[i][1]))
        log_c, address = controls[choice]
        if abs(log_c - r.log_equity) <= caliper:
            controls.pop(choice)
            rows.append(dict(looping_wallet=r.wallet_address, non_looping_wallet=address,
                log_equity_gap=r.log_equity - log_c, absolute_log_equity_gap=abs(r.log_equity - log_c)))
    out = pd.DataFrame(rows)
    if out.empty:
        raise ValueError('No equity pairs within declared caliper')
    return out


def paired_summary(risk, pairs, scenario_losses, *, n_boot=1000):
    indexed = risk.set_index('wallet_address')
    results = []
    rng = np.random.default_rng(42)
    for metric in ['probability_bad_debt', 'mean_bad_debt_ratio_t0']:
        x = indexed.loc[pairs.looping_wallet, metric].to_numpy(float)
        y = indexed.loc[pairs.non_looping_wallet, metric].to_numpy(float)
        diff = x - y
        boots = [float(diff[rng.integers(0, len(diff), len(diff))].mean()) for _ in range(n_boot)]
        lo, hi = np.quantile(boots, [.025, .975])
        p = float(stats.wilcoxon(diff).pvalue) if np.any(diff != 0) else 1.0
        results.append(dict(metric=metric, mean_loop_minus_nonloop=float(diff.mean()),
            ci95_low=float(lo), ci95_high=float(hi), paired_wilcoxon_p=p,
            inference='descriptive_matched_wallet_association_under_shared_shocks'))
    x, y = scenario_losses.looping.to_numpy(float), scenario_losses.non_looping.to_numpy(float)
    observed = expected_shortfall(x) - expected_shortfall(y)
    diffs = []
    for _ in range(n_boot):
        ix = rng.integers(0, len(x), len(x))
        diffs.append(expected_shortfall(x[ix]) - expected_shortfall(y[ix]))
    lo, hi = np.quantile(diffs, [.025, .975])
    return dict(pairs=len(pairs), max_absolute_log_equity_gap=float(pairs.absolute_log_equity_gap.max()),
        matched_outcomes=results, es99_looping=expected_shortfall(x), es99_non_looping=expected_shortfall(y),
        es99_diff=observed, es99_diff_ci95=[float(lo), float(hi)],
        bootstrap_unit='paired_shared_scenario', n_boot=n_boot,
        median_looping=float(np.median(x)), median_non_looping=float(np.median(y)),
        tail_to_median_ratio_status='undefined_if_median_is_zero_no_epsilon_substitution')


def convexity_test(shocks, paired_loss_gap, *, n_boot=1000):
    """Fit the loss gap curve; resample wallet PAIRS with the shock grid fixed."""
    x = np.asarray(shocks, float)
    losses = np.asarray(paired_loss_gap, float)
    if losses.ndim != 2 or losses.shape[1] != len(x) or len(x) < 5 or not np.all(np.diff(x) > 0):
        raise ValueError('Need ordered shock grid and pair-by-shock loss differences')
    design = np.column_stack([np.ones(len(x)), x, x*x])
    inverse = np.linalg.pinv(design)
    pair_curvature = losses @ inverse[2]
    rng = np.random.default_rng(42)
    boots = [float(pair_curvature[rng.integers(0, len(losses), len(losses))].mean()) for _ in range(n_boot)]
    lo, hi = np.quantile(boots, [.025, .975])
    mean = losses.mean(0)
    slopes = np.diff(mean) / np.diff(x)
    curvature = np.diff(slopes)
    return dict(quadratic_curvature_coefficient=float(pair_curvature.mean()),
        ci95_low=float(lo), ci95_high=float(hi),
        positive_quadratic_curvature_supported=bool(lo > 0),
        discrete_convex_everywhere=bool(np.all(curvature >= -1e-7)),
        negative_slope_change_intervals=int(np.count_nonzero(curvature < -1e-7)),
        interpretation='wallet_pair_resampling_on_fixed_deterministic_shock_grid_not_tail_probability_estimation')


def u_shape_tests(grid, *, n_boot=2000):
    """Boundary-slope/turning-point tests with snapshot-scenario blocks."""
    rows = []
    for (depth, mode), group in grid.groupby(['depth_scale', 'feedback_mode']):
        curves = group.pivot(index=['snapshot_id', 'scenario_id'], columns='fixed_cf', values='post_execution_bdr_pct')
        if curves.isna().any().any():
            raise ValueError('Incomplete CF response surface')
        x = curves.columns.to_numpy(float)
        design = np.column_stack([np.ones(len(x)), x, x*x])
        coefficients = curves.to_numpy() @ np.linalg.pinv(design).T
        rng = np.random.default_rng(42)
        boot = np.array([coefficients[rng.integers(0, len(coefficients), len(coefficients))].mean(0) for _ in range(n_boot)])
        beta = coefficients.mean(0)
        left = boot[:, 1] + 2 * x.min() * boot[:, 2]
        right = boot[:, 1] + 2 * x.max() * boot[:, 2]
        c_lo, c_hi = np.quantile(boot[:, 2], [.025, .975])
        l_lo, l_hi = np.quantile(left, [.025, .975])
        r_lo, r_hi = np.quantile(right, [.025, .975])
        turning = -beta[1] / (2*beta[2]) if abs(beta[2]) > 1e-12 else None
        supported = c_lo > 0 and l_hi < 0 and r_lo > 0 and turning is not None and x.min() < turning < x.max()
        rows.append(dict(depth_scale=float(depth), feedback_mode=mode, blocks=len(curves), cf_levels=len(x),
            beta2=float(beta[2]), beta2_ci95=[float(c_lo), float(c_hi)],
            left_boundary_slope_ci95=[float(l_lo), float(l_hi)],
            right_boundary_slope_ci95=[float(r_lo), float(r_hi)],
            turning_point=float(turning) if turning is not None else None,
            u_shape_supported_on_grid=bool(supported),
            unknown_domain_attempts=int(group.outside_quote_domain_attempts.sum()),
            inference='paired_snapshot_scenario_block_stability_on_selected_grid_not_iid_population_ANOVA'))
    return rows


def temporal_feedback_validation(peak, oracle_frames, liquidation_frames, reserve_config):
    """B1 estimates; B2 held out. Lagged observables predict next-block oracle returns.

    Seized collateral is a flow proxy, not observed DEX sale. Predictive
    association does not identify a liquidation-to-oracle causal coefficient.
    """
    import statsmodels.api as sm
    frames = []
    for sid in ['B1', 'B2']:
        q = peak[peak.snapshot_id.eq(sid)].sort_values('block').copy()
        o = oracle_frames[sid]
        px = o[o.symbol.str.upper().isin(['WETH', 'WSTETH'])].pivot(index='block', columns='symbol', values='price_usd')
        px.columns = px.columns.str.upper()
        q = q.join(px, on='block', validate='one_to_one')
        if q[['WETH', 'WSTETH']].isna().any().any() or not q.block.diff().dropna().eq(1).all():
            raise ValueError('Incomplete aligned DEX/oracle blocks')
        q['oracle_ratio'] = q.WSTETH / q.WETH
        q['dex_return'] = np.log(q.market_ratio).diff()
        q['oracle_return'] = np.log(q.oracle_ratio).diff()
        q['eth_return'] = np.log(q.WETH).diff()
        ev = liquidation_frames[sid]
        ev = ev[ev.event_block.between(int(q.block.min()), int(q.block.max())) & ~ev.receive_atoken]
        decimals = reserve_config[reserve_config.snapshot_id.eq(sid)].set_index('token_address').decimals.to_dict()
        price = o.set_index(['block', 'token_address']).price_usd.to_dict()
        volumes = {}
        for e in ev.itertuples():
            key = (e.event_block - 1, e.collateral_token_address)
            if key not in price or e.collateral_token_address not in decimals:
                raise ValueError('Missing preceding-block liquidation valuation')
            value = int(e.collateral_raw_amount) / 10**int(decimals[e.collateral_token_address]) * price[key]
            volumes[e.event_block] = volumes.get(e.event_block, 0) + value
        q['seized_proxy_usd'] = q.block.map(volumes).fillna(0.)
        q['flow_feature'] = np.log1p(q.seized_proxy_usd / 1e6)
        q['dex_return_lag1'] = q.dex_return.shift(1)
        q['dex_return_lag2'] = q.dex_return.shift(2)
        q['oracle_return_lag1'] = q.oracle_return.shift(1)
        q['target_next_oracle_return'] = q.oracle_return.shift(-1)
        q['feature_block'] = q.block
        q['target_block'] = q.block + 1
        frames.append(q.dropna(subset=['dex_return', 'dex_return_lag1', 'dex_return_lag2',
            'oracle_return_lag1', 'eth_return', 'target_next_oracle_return']))
    data = pd.concat(frames, ignore_index=True)
    columns = ['dex_return', 'dex_return_lag1', 'dex_return_lag2', 'oracle_return_lag1', 'eth_return', 'flow_feature']
    train, test = data[data.snapshot_id.eq('B1')], data[data.snapshot_id.eq('B2')]
    if train.block_timestamp_utc.max() >= test.block_timestamp_utc.min():
        raise ValueError('Temporal holdout overlap')
    fit = sm.OLS(train.target_next_oracle_return, sm.add_constant(train[columns], has_constant='add')).fit(cov_type='HAC', cov_kwds={'maxlags': 10})
    prediction = fit.predict(sm.add_constant(test[columns], has_constant='add'))
    contrast = np.array([0., 1., 1., 1., 0., 0., 0.])
    beta = float(contrast @ fit.params.to_numpy())
    variance = max(0., float(contrast @ fit.cov_params().to_numpy() @ contrast))
    low, high = beta - 1.96 * math.sqrt(variance), beta + 1.96 * math.sqrt(variance)
    rmse = float(np.sqrt(np.mean((test.target_next_oracle_return - prediction)**2)))
    baseline_rmse = float(np.sqrt(np.mean(test.target_next_oracle_return**2)))
    improves = rmse < baseline_rmse
    usable = low > 0 and improves
    # Do not turn a failed predictive association into a positive causal input.
    point = float(np.clip(beta, 0, 1)) if usable else 0.0
    data['role'] = np.where(data.snapshot_id.eq('B1'), 'training', 'held_out_test')
    report = dict(train_snapshot='B1', test_snapshot='B2', training_rows=len(train), held_out_rows=len(test),
        first_held_out_timestamp=str(test.block_timestamp_utc.min()), last_training_timestamp=str(train.block_timestamp_utc.max()),
        summed_lagged_DEX_coefficient=beta, hac_ci95=[low, high],
        held_out_rmse=rmse, zero_change_baseline_rmse=baseline_rmse,
        predictive_improvement=bool(improves), positive_predictive_transmission_supported=bool(usable),
        calibrated_predictive_proxy=point, positive_ci_upper_sensitivity=float(np.clip(max(0., high), 0, 1)),
        causal_identification='not_established_observational_lagged_proxy',
        selection='retrospective_max_event_peak_fixed_by_prior_protocol_not_tuned_on_fit')
    return data, report
