"""Bounded execution-cost diagnostics. Oracle prices never follow DEX flow."""
from copy import deepcopy
import math

import numpy as np
import pandas as pd

from src.models.cascade import close_factor, debt_value, health_factor
from src.models.n5_n3_update import interpolate_average_impact

KEYS = ['snapshot_id', 'snapshot_block', 'pool_address', 'token_in_address', 'token_out_address']


def power_fit(points):
    p = points[points.impact_pct.gt(0)]
    slope, intercept = np.polyfit(np.log(p.trade_size_usd), np.log(p.impact_pct / 100), 1)
    if not np.isfinite([slope, intercept]).all() or slope <= 0:
        raise ValueError('Invalid power fit')
    return float(slope), float(intercept)


class ExecutionCurve:
    def __init__(self, points):
        self.points = points[['trade_size_usd', 'impact_pct']].drop_duplicates().sort_values('trade_size_usd')
        # Reuse the input validator, including monotonicity and conflicting sizes.
        interpolate_average_impact(self.points, float(self.points.trade_size_usd.min()))
        self.low, self.high = self.points.trade_size_usd.min(), self.points.trade_size_usd.max()
        self.slope, self.intercept = power_fit(self.points)

    def average(self, size, method):
        if size == 0:
            return 0.
        if not self.low <= size <= self.high:
            raise ValueError('outside_quote_domain')
        if method == 'linear':
            value = interpolate_average_impact(self.points, size)['impact_pct'] / 100
        elif method == 'power':
            value = math.exp(self.intercept) * size ** self.slope
        else:
            raise ValueError('Unknown curve method')
        if not 0 <= value < 1:
            raise ValueError('invalid_impact')
        return value

    def incremental(self, before, sale, method):
        """Average quote describes TOTAL execution cost, not terminal price drop.

        In snapshot USD coordinates C(v)=v*average(v). Sequential execution
        over [before,before+sale] uses (C(after)-C(before))/sale.
        """
        if before < 0 or sale <= 0 or not np.isfinite([before, sale]).all():
            raise ValueError('Invalid flow')
        after = before + sale
        value = (after * self.average(after, method) - before * self.average(before, method)) / sale
        if not 0 <= value < 1:
            raise ValueError('invalid_incremental_impact')
        return value


def heldout_quotes(points):
    """Group nearly identical anchors into one held-out fold, preventing leakage.

    End folds are reported as outside the training domain, never extrapolated.
    """
    rows = []
    for identity, g in points.groupby(KEYS):
        g = g[['trade_size_usd', 'impact_pct']].drop_duplicates().sort_values('trade_size_usd').copy()
        # Quotes around the same 2%-impact binary-search boundary are one group.
        sizes = g.trade_size_usd.to_numpy()
        groups = np.cumsum(np.r_[True, np.diff(sizes) > 1e-5 * sizes[:-1]])
        for fold in np.unique(groups):
            test, train = g[groups == fold], g[groups != fold]
            curve = ExecutionCurve(train)
            for row in test.itertuples():
                for method in ['power', 'linear']:
                    result = dict(zip(KEYS, identity), heldout_size_usd=float(row.trade_size_usd),
                                  observed_impact_pct=float(row.impact_pct), method=method, fold=int(fold))
                    try:
                        prediction = 100 * curve.average(row.trade_size_usd, method)
                        lower = train[train.trade_size_usd.lt(row.trade_size_usd)].trade_size_usd.max()
                        upper = train[train.trade_size_usd.gt(row.trade_size_usd)].trade_size_usd.min()
                        result.update(status='scored', predicted_impact_pct=prediction,
                            error_percentage_points=prediction-row.impact_pct,
                            lower_train_size_usd=float(lower), upper_train_size_usd=float(upper))
                    except ValueError:
                        result.update(status='outside_training_domain', predicted_impact_pct=None,
                                      error_percentage_points=None)
                    rows.append(result)
    return pd.DataFrame(rows)


def simulate_execution(wallets, assets, multipliers, routes, *, method, gas=5., fee=.0005, max_rounds=30,
                       allowed_routes=None, pool_state='reset_each_round', ordering='address',
                       pair_rule='total_profit', seed=42, use_route_fees=False):
    """Research liquidation prototype with fixed shocked oracle and bounded pools.

    Only exact collateral->debt routes are modeled; no implicit multi-hop. The
    no_impact method discovers candidate demand across ALL pairs, not an upper
    bound on every possible market response. Unsupported execution is UNKNOWN.
    The caller selects cumulative pools or hypothetical reset between rounds.
    """
    if method not in ['no_impact', 'power', 'linear'] or not math.isfinite(gas) or gas < 0 or not math.isfinite(fee) or not 0 <= fee < 1:
        raise ValueError('Invalid execution settings')
    if pool_state not in ['reset_each_round', 'cumulative_across_rounds'] or ordering not in ['address', 'lowest_hf', 'random'] or pair_rule not in ['total_profit', 'bonus_minus_impact']:
        raise ValueError('Invalid execution policy')
    state = deepcopy(wallets)
    prices = {t: a.price_usd * multipliers[t] for t, a in assets.items()}
    if any(not math.isfinite(p) or p <= 0 for p in prices.values()):
        raise ValueError('Invalid shocked price')
    # Fixed oracle means initially healthy wallets cannot become newly eligible
    # through a DEX/oracle feedback assumption. Retain all eligible initial ones.
    active = [w for w in state if debt_value(w, prices) > 0 and health_factor(w, assets, prices) < 1]
    rounds, transactions, attempts = [], [], []
    rng = np.random.default_rng(seed)
    sold = {}
    for number in range(1, max_rounds+1):
        if pool_state == 'reset_each_round':
            sold = {}
        executed, eligible, unknown, rejected = 0, 0, 0, 0
        repaid, profit_sum = 0., 0.
        ordered = sorted(active, key=lambda w: w.wallet)
        if ordering == 'lowest_hf':
            ordered.sort(key=lambda w: (health_factor(w, assets, prices), w.wallet))
        elif ordering == 'random':
            # Permute the same full initial set each round for paired runs;
            # differing executions do not consume a different random schedule.
            ordered = [ordered[i] for i in rng.permutation(len(ordered))]
        for w in ordered:
            hf = health_factor(w, assets, prices)
            if hf >= 1 or debt_value(w, prices) <= 0:
                continue
            eligible += 1
            cap = close_factor(hf, 'baseline_aave') * debt_value(w, prices)
            best, unavailable, candidates = None, False, []
            for ct, cq in sorted(w.collateral.items()):
                if cq <= 0 or not w.collateral_enabled.get(ct, True):
                    continue
                bonus = w.effective_bonus.get(ct, assets[ct].liquidation_bonus)
                for dt, dq in sorted(w.debt.items()):
                    repay = min(cap, dq*prices[dt], cq*prices[ct]/(1+bonus))
                    if repay < .01:
                        continue
                    sale = repay*(1+bonus)
                    # Restore snapshot notional: quote grid priced input at t0.
                    reference_sale = sale / multipliers[ct]
                    route = routes.get((ct, dt))
                    pool = route['pool_address'] if route else None
                    before = sold.get(pool, 0.) if pool else 0.
                    status, slip = 'supported', 0.
                    if method != 'no_impact' and ct != dt:
                        if route is None:
                            status = 'outside_selected_scope' if allowed_routes is not None and (ct, dt) not in allowed_routes else 'missing_exact_route'
                        else:
                            try:
                                slip = route['curve'].incremental(before, reference_sale, method)
                            except ValueError as exc:
                                status = str(exc)
                    if status != 'supported':
                        unavailable = True
                        attempts.append(dict(round=number, wallet_address=w.wallet, token_in_address=ct,
                            token_out_address=dt, pool_address=pool, sale_snapshot_usd=reference_sale,
                            cumulative_before_snapshot_usd=before, required_after_snapshot_usd=before+reference_sale,
                            status=status))
                        continue
                    execution_fee = fee if ct != dt else 0.
                    if use_route_fees and ct != dt:
                        if route is None:
                            raise ValueError('Route fee requires an exact route')
                        execution_fee = route.get('fee_rate')
                        if execution_fee is None or not math.isfinite(execution_fee) or not 0 <= execution_fee < 1:
                            raise ValueError('Invalid verified route fee')
                    profit = sale*(1-slip)*(1-execution_fee)-repay-gas
                    candidate = dict(round=number, wallet_address=w.wallet, token_in_address=ct,
                        token_out_address=dt, pool_address=pool, sold_usd=sale,
                        sale_snapshot_usd=reference_sale, cumulative_after_snapshot_usd=before+reference_sale,
                        cumulative_before_snapshot_usd=before,
                        repaid_debt_usd=repay, liquidator_profit_usd=profit, slippage_pct=100*slip,
                        execution_fee_rate=execution_fee, bonus_premium=bonus, bonus_minus_impact=bonus-slip)
                    candidates.append(candidate)
            if candidates:
                feasible = [q for q in candidates if q['liquidator_profit_usd'] > 0]
                if pair_rule == 'bonus_minus_impact' and feasible:
                    best = max(feasible, key=lambda q: (q['bonus_minus_impact'], q['liquidator_profit_usd']))
                else:
                    best = max(candidates, key=lambda q: q['liquidator_profit_usd'])
            unknown += int(unavailable)
            if best is None or best['liquidator_profit_usd'] <= 0:
                rejected += int(best is not None)
                continue
            ct, dt = best['token_in_address'], best['token_out_address']
            w.debt[dt] = max(0., w.debt[dt]-best['repaid_debt_usd']/prices[dt])
            w.collateral[ct] = max(0., w.collateral[ct]-best['sold_usd']/prices[ct])
            if best['pool_address']:
                sold[best['pool_address']] = best['cumulative_after_snapshot_usd']
            transactions.append(best)
            repaid += best['repaid_debt_usd']; profit_sum += best['liquidator_profit_usd']; executed += 1
        rounds.append(dict(round=number, eligible_wallets=eligible, executed_liquidations=executed,
            wallets_with_unknown_candidates=unknown, wallets_without_profitable_supported_quote=rejected,
            repaid_debt_usd=repaid, liquidator_profit_usd=profit_sum))
        if not executed:
            break
    summary = dict(method=method, rounds=len(rounds), executed_liquidations=len(transactions),
        simulated_repaid_usd=sum(r['repaid_debt_usd'] for r in rounds),
        liquidator_profit_usd=sum(r['liquidator_profit_usd'] for r in rounds),
        initial_eligible_wallets=len(active), unknown_candidate_attempts=len(attempts),
        hit_round_limit=bool(rounds[-1]['executed_liquidations'] and len(rounds)==max_rounds),
        pool_state=pool_state, ordering=ordering, pair_rule=pair_rule, seed=seed,
        oracle_feedback=False, freeze_proven=False)
    return summary, rounds, transactions, attempts
