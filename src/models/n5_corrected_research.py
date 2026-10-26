"""Conditional research execution with exact token balances and bounded quotes.

DEX execution costs are observations. Passing a DEX marginal cost into the
oracle is an explicit counterfactual, never a claim about Chainlink updates.
"""
from copy import deepcopy
from dataclasses import dataclass, field
import math

import numpy as np
import pandas as pd
from scipy import sparse

from src.models.cascade import close_factor, collateral_value, debt_value, health_factor


class BoundedCurve:
    def __init__(self, points):
        from src.models.n5_impact_review import ExecutionCurve
        checked = ExecutionCurve(points)
        self.x = checked.points.trade_size_usd.to_numpy(float)
        self.y = checked.points.impact_pct.to_numpy(float) / 100
        self.low, self.high = float(self.x[0]), float(self.x[-1])

    def cost(self, volume):
        if volume == 0:
            return 0.0
        if not self.low <= volume <= self.high:
            raise ValueError('outside_quote_domain')
        return float(volume * np.interp(volume, self.x, self.y))

    def incremental(self, before, size):
        if before < 0 or size <= 0:
            raise ValueError('Invalid volume')
        impact = (self.cost(before + size) - self.cost(before)) / size
        if not 0 <= impact < 1:
            raise ValueError('invalid_incremental_impact')
        return impact

    def marginal_cost(self, volume):
        """Derivative of the fitted total cost, not an observed terminal price."""
        if volume == 0:
            return 0.0
        self.cost(volume)  # Domain check, including nonzero lower boundary.
        k = min(len(self.x) - 2, max(0, int(np.searchsorted(self.x, volume, side='right')) - 1))
        slope = (self.y[k + 1] - self.y[k]) / (self.x[k + 1] - self.x[k])
        return float(np.interp(volume, self.x, self.y) + volume * slope)


@dataclass
class ExecutionResult:
    wallets: list
    prices: dict
    transactions: list
    rounds: list
    summary: dict
    first_eligible_round: dict = field(default_factory=dict)
    first_executed_round: dict = field(default_factory=dict)


def execute(wallets, assets, multipliers, routes, *, cf_rule='baseline_aave',
            depth_scale=1.0, feedback=0.0, feedback_cap=0.15, gas=5.0,
            max_rounds=100, initial_eligible=None, fixed_cf=None,
            feedback_by_token=None, blocked_sellers=frozenset(), disabled_pairs=frozenset()):
    """Exact collateral/debt pairs, cumulative pool flow, no order splitting.

    feedback=0 reproduces the approved fixed-oracle policy. Positive feedback
    transfers the fitted marginal DEX cost to the sold token's oracle, relative
    to its initial shocked price. Debt tokens receive that same price update.
    """
    if not math.isfinite(depth_scale) or depth_scale <= 0 or not 0 <= feedback <= 1:
        raise ValueError('Invalid depth/feedback')
    if not 0 <= feedback_cap < 1 or gas < 0 or not math.isfinite(gas) or max_rounds < 1:
        raise ValueError('Invalid execution settings')
    if fixed_cf is not None and (not math.isfinite(fixed_cf) or not 0 < fixed_cf <= 1):
        raise ValueError('Fixed CF must be in (0,1]')
    if feedback_by_token is not None and any(not math.isfinite(v) or not 0 <= v <= 1 for v in feedback_by_token.values()):
        raise ValueError('Token feedback coefficients must be in [0,1]')
    shocked = {t: a.price_usd * multipliers[t] for t, a in assets.items()}
    if any(not math.isfinite(p) or p <= 0 for p in shocked.values()):
        raise ValueError('Invalid prices')
    prices = shocked.copy()
    initial = (set(initial_eligible) if initial_eligible is not None else
               {w.wallet for w in wallets if health_factor(w, assets, prices) < 1})
    # Only falling prices are allowed. This bound safely retains every wallet
    # that can cross HF=1 under the configured maximum collateral price drop.
    candidates = [w for w in wallets if (health_factor(w, assets, prices) < 1 / (1 - feedback_cap)
                  if feedback else w.wallet in initial)]
    state = deepcopy(candidates)
    changed = {w.wallet: w for w in state}
    sold, token_pools, first_eligible, first_executed = {}, {}, {}, {}
    txs, rounds = [], []
    for w in state:
        if w.wallet in initial:
            first_eligible[w.wallet] = 1
    outside = unsupported = 0
    stop_reason = 'round_limit'
    for number in range(1, max_rounds + 1):
        active = [w for w in state if debt_value(w, prices) > 0 and health_factor(w, assets, prices) < 1]
        active.sort(key=lambda w: (health_factor(w, assets, prices), w.wallet))
        for w in active:
            first_eligible.setdefault(w.wallet, number)
        executed = 0
        repaid = 0.0
        unprofitable = 0
        for w in active:
            if w.wallet in blocked_sellers:
                continue
            hf = health_factor(w, assets, prices)
            cap = (fixed_cf if fixed_cf is not None else close_factor(hf, cf_rule)) * debt_value(w, prices)
            choices = []
            for ct, cq in sorted(w.collateral.items()):
                if cq <= 0 or not w.collateral_enabled.get(ct, True):
                    continue
                bonus = w.effective_bonus.get(ct, assets[ct].liquidation_bonus)
                for dt, dq in sorted(w.debt.items()):
                    if (ct, dt) in disabled_pairs:
                        continue
                    repay = min(cap, dq * prices[dt], cq * prices[ct] / (1 + bonus))
                    if repay < 0.01:
                        continue
                    sale = repay * (1 + bonus)
                    route = routes.get((ct, dt)) if ct != dt else None
                    pool, before, ref_sale, slip = None, 0.0, 0.0, 0.0
                    if ct != dt:
                        if route is None:
                            unsupported += 1
                            continue
                        pool = route['pool_address']
                        before = sold.get(pool, 0.0)
                        # Quantity * original USD price; oracle feedback does
                        # not make the historical quote domain artificially larger.
                        ref_sale = sale / prices[ct] * assets[ct].price_usd / depth_scale
                        try:
                            slip = route['curve'].incremental(before, ref_sale)
                        except ValueError:
                            outside += 1
                            continue
                    fee = route.get('fee_rate', 0.0) if route else 0.0
                    profit = sale * (1 - slip) * (1 - fee) - repay - gas
                    choices.append(dict(round=number, wallet_address=w.wallet,
                        token_in_address=ct, token_out_address=dt, pool_address=pool,
                        sold_usd=sale, repaid_debt_usd=repay, slippage_pct=100 * slip,
                        liquidator_profit_usd=profit, bonus_minus_impact=bonus - slip,
                        reference_after=before + ref_sale))
            feasible = [c for c in choices if c['liquidator_profit_usd'] > 0]
            if not feasible:
                unprofitable += int(bool(choices))
                continue
            best = max(feasible, key=lambda c: (c['bonus_minus_impact'], c['liquidator_profit_usd']))
            ct, dt = best['token_in_address'], best['token_out_address']
            w.debt[dt] = max(0.0, w.debt[dt] - best['repaid_debt_usd'] / prices[dt])
            w.collateral[ct] = max(0.0, w.collateral[ct] - best['sold_usd'] / prices[ct])
            if best['pool_address']:
                sold[best['pool_address']] = best['reference_after']
                token_pools.setdefault(ct, {})[best['pool_address']] = routes[(ct, dt)]['curve']
            first_executed.setdefault(w.wallet, number)
            txs.append(best)
            repaid += best['repaid_debt_usd']
            executed += 1
        rounds.append(dict(round=number, eligible_wallets=len(active), executed_liquidations=executed,
                           repaid_debt_usd=repaid, supported_unprofitable_wallets=unprofitable))
        price_changed = False
        if feedback:
            for token, pools in token_pools.items():
                # Shared token price: largest modeled dislocation among the
                # selected pools; this aggregation is a declared sensitivity.
                transmission = feedback if feedback_by_token is None else feedback_by_token.get(token, 0.0)
                drop = min(feedback_cap, transmission * max(c.marginal_cost(sold[p]) for p, c in pools.items()))
                target = shocked[token] * (1 - drop)
                if target < prices[token] - 1e-12:
                    prices[token] = target
                    price_changed = True
        if executed == 0 and not price_changed:
            stop_reason = 'no_profitable_supported_execution'
            break
    full_state = [changed.get(w.wallet, w) for w in wallets]
    debt0 = sum(debt_value(w, {t: a.price_usd for t, a in assets.items()}) for w in wallets)
    loss = sum(max(0.0, debt_value(w, prices) - collateral_value(w, prices)) for w in full_state)
    nseq = sum(r >= 2 for r in first_eligible.values())
    r1 = rounds[0]['repaid_debt_usd'] if rounds else 0.0
    total = sum(t['repaid_debt_usd'] for t in txs)
    summary = dict(cf_rule=cf_rule, depth_scale=depth_scale, oracle_feedback=bool(feedback),
        fixed_cf=fixed_cf,
        feedback_passthrough=feedback, feedback_cap=feedback_cap, rounds=len(rounds), stop_reason=stop_reason,
        initial_eligible_wallets=len(initial), newly_eligible_round_ge_2=nseq,
        first_executed_round_ge_2=sum(r >= 2 for r in first_executed.values()),
        executed_liquidations=len(txs), executed_wallets=len(first_executed),
        round_1_repaid_usd=r1, simulated_repaid_usd=total,
        round_1_sold_collateral_usd=sum(t['sold_usd'] for t in txs if t['round'] == 1),
        cascade_amplification_factor=total / r1 if r1 else None,
        outside_quote_domain_attempts=outside, unsupported_route_attempts=unsupported,
        post_execution_bad_debt_usd=loss, post_execution_bdr_pct=100 * loss / debt0 if debt0 else None,
        liquidator_profit_usd=sum(t['liquidator_profit_usd'] for t in txs),
        mean_slippage_pct=float(np.mean([t['slippage_pct'] for t in txs])) if txs else 0.0,
        research_acceptance='conditional_model_not_empirical_causality')
    return ExecutionResult(full_state, prices, txs, rounds, summary, first_eligible, first_executed)


def degree_preserving_swap(matrix, *, seed, swaps_per_edge=3):
    """Bipartite double-edge swaps preserve every wallet AND token degree.

    Each source wallet retains its edge-weight multiset; token strengths may
    change. This null preserves degrees, not both weighted margins.
    """
    m = sparse.csr_matrix(matrix)
    rows, cols = m.nonzero()
    values = np.asarray(m[rows, cols]).ravel()
    edges = set(zip(rows.tolist(), cols.tolist()))
    rng = np.random.default_rng(seed)
    accepted = 0
    target = swaps_per_edge * len(rows)
    for _ in range(max(1, target * 20)):
        if accepted >= target or len(rows) < 2:
            break
        i, j = rng.integers(0, len(rows), 2)
        a, b, x, y = rows[i], rows[j], cols[i], cols[j]
        if a == b or x == y or (a, y) in edges or (b, x) in edges:
            continue
        edges.remove((a, x)); edges.remove((b, y))
        edges.add((a, y)); edges.add((b, x))
        cols[i], cols[j] = y, x
        accepted += 1
    out = sparse.csr_matrix((values, (rows, cols)), shape=m.shape)
    if not (np.array_equal(m.getnnz(axis=0), out.getnnz(axis=0)) and
            np.array_equal(m.getnnz(axis=1), out.getnnz(axis=1))):
        raise AssertionError('Null degree conservation failed')
    return out, dict(accepted_swaps=accepted, target_swaps=target,
                    degenerate=accepted == 0, wallet_and_token_degrees_preserved=True)


def normalize_compound_usd(exposures, oracle_prices):
    """Convert the known ETH-numeraire Comet market using same-block WETH/USD.

    Original columns remain available for reproducing Comet eligibility in its
    native numeraire. Already normalized inputs are returned unchanged.
    """
    p = exposures.copy()
    if 'usd_normalization' in p and p.usd_normalization.eq('same_snapshot_weth_oracle').any():
        weth_market = p.market_id.eq('compound_v3_ethereum_weth')
        if not p.loc[weth_market, 'usd_normalization'].eq('same_snapshot_weth_oracle').all():
            raise ValueError('Mixed normalized and native WETH rows')
        return p
    p['native_price'] = p.price_usd
    p['native_value'] = p.value_usd
    p['native_quote_currency'] = 'USD'
    p['usd_conversion_rate'] = 1.0
    p['usd_normalization'] = 'native_usd'
    weth = '0xc02aaa39b223fe8d0a0e5c4f27ead9083c756cc2'
    o = oracle_prices[oracle_prices.token_address.str.lower().eq(weth)]
    if o.snapshot_block.duplicated().any():
        raise ValueError('Ambiguous WETH oracle snapshot')
    for block, g in p[p.market_id.eq('compound_v3_ethereum_weth')].groupby('snapshot_block'):
        base = g[g.token_address.str.lower().eq(weth) & g.asset_role.isin(['base_supply', 'base_borrow'])]
        if base.empty or not np.allclose(base.price_usd, 1, rtol=0, atol=1e-8):
            raise ValueError('Unrecognized WETH market numeraire; do not guess')
        match = o[o.snapshot_block.eq(block)]
        if len(match) != 1 or not math.isfinite(float(match.price_usd.iloc[0])) or match.price_usd.iloc[0] <= 0:
            raise ValueError('Missing positive same-block WETH/USD oracle')
        rate = float(match.price_usd.iloc[0])
        p.loc[g.index, 'native_quote_currency'] = 'WETH'
        p.loc[g.index, 'usd_conversion_rate'] = rate
        p.loc[g.index, 'price_usd'] *= rate
        p.loc[g.index, 'value_usd'] *= rate
        p.loc[g.index, 'usd_normalization'] = 'same_snapshot_weth_oracle'
    if not np.allclose(p.amount_token * p.price_usd, p.value_usd, rtol=1e-9, atol=1e-7):
        raise ValueError('Normalized Compound balances do not reconcile')
    return p
