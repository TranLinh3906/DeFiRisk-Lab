"""Structural diagnostics; associations do not identify causal contagion."""
import heapq
import numpy as np
from scipy import sparse, stats


def token_pair_betweenness(exposure):
    """Weighted Brandes centrality with token endpoints and wallet bridges.

    Distance is inverse USD exposure. All shortest paths, including ties, are
    counted. Degree-one wallets cannot bridge token pairs and are pruned only
    from the shortest-path calculation, retaining zero in the returned vector.
    """
    full = sparse.csr_matrix(exposure)
    keep = np.flatnonzero(full.getnnz(axis=1) >= 2)
    m = full[keep]
    nw, nt = m.shape
    result = np.zeros(full.shape[0])
    if nw == 0 or nt < 2:
        return result
    graph = sparse.bmat([[None, m], [m.T, None]], format='csr')
    distances = 1 / graph.data
    size = graph.shape[0]
    centrality = np.zeros(nw)
    for source in range(nw, nw + nt):
        dist = np.full(size, np.inf)
        sigma = np.zeros(size)
        dist[source], sigma[source] = 0, 1
        predecessors = [[] for _ in range(size)]
        visited = np.zeros(size, dtype=bool)
        queue, stack = [(0.0, source)], []
        while queue:
            ds, node = heapq.heappop(queue)
            if visited[node]:
                continue
            visited[node] = True
            stack.append(node)
            for ix in range(graph.indptr[node], graph.indptr[node + 1]):
                nxt = graph.indices[ix]
                candidate = ds + distances[ix]
                tolerance = 1e-12 * max(abs(candidate), 1e-20)
                if candidate < dist[nxt] - tolerance:
                    dist[nxt] = candidate
                    sigma[nxt] = sigma[node]
                    predecessors[nxt] = [node]
                    heapq.heappush(queue, (candidate, int(nxt)))
                elif abs(candidate - dist[nxt]) <= tolerance:
                    sigma[nxt] += sigma[node]
                    predecessors[nxt].append(node)
        dependency = np.zeros(size)
        for node in reversed(stack):
            endpoint = 1.0 if node >= nw and node != source else 0.0
            for previous in predecessors[node]:
                dependency[previous] += sigma[previous] / sigma[node] * (endpoint + dependency[node])
            if node < nw:
                centrality[node] += dependency[node]
    result[keep] = centrality / (nt * (nt - 1))
    return result


def power_law_tail_diagnostic(values, *, n_boot=300, seed=42):
    """Fixed-xmin continuous Pareto fit and parametric KS diagnostic.

    This checks tail-model plausibility under an iid sampling assumption; it
    does not prove a power law in dependent network observations.
    """
    x = np.asarray(values, float)
    x = x[np.isfinite(x) & (x > 0)]
    if len(x) < 30:
        return dict(status='insufficient_positive_observations')
    xmin = float(np.quantile(x, .8))
    tail = x[x >= xmin]
    if len(tail) < 20 or np.all(tail == xmin):
        return dict(status='insufficient_nonconstant_tail')
    def fit(v):
        return 1 + len(v) / np.log(v / xmin).sum()
    def ks(v, alpha):
        sorted_v = np.sort(v)
        cdf = 1 - (sorted_v / xmin) ** (1 - alpha)
        upper = np.arange(1, len(v) + 1) / len(v)
        lower = np.arange(len(v)) / len(v)
        return float(max(np.max(upper - cdf), np.max(cdf - lower)))
    alpha = fit(tail)
    observed = ks(tail, alpha)
    rng = np.random.default_rng(seed)
    boot = []
    for _ in range(n_boot):
        sample = xmin * (1 - rng.random(len(tail))) ** (-1 / (alpha - 1))
        boot.append(ks(sample, fit(sample)))
    rate = 1 / np.mean(tail - xmin)
    ll_pareto = len(tail) * np.log((alpha - 1) / xmin) - alpha * np.log(tail / xmin).sum()
    ll_exp = len(tail) * np.log(rate) - rate * (tail - xmin).sum()
    return dict(status='diagnostic_only', xmin=xmin, xmin_policy='positive_sample_80th_percentile',
        n_tail=len(tail), alpha=float(alpha), ks_distance=observed,
        ks_parametric_bootstrap_p=float((1 + np.count_nonzero(np.asarray(boot) >= observed)) / (n_boot + 1)),
        n_boot=n_boot, log_likelihood_pareto_minus_shifted_exponential=float(ll_pareto - ll_exp),
        conclusion='not_a_proof_of_power_law_or_causal_network_transmission')


def wallet_associations(nodes, risk, baseline):
    g = nodes.merge(risk[['wallet_address', 'mean_bad_debt_ratio_t0', 'net_equity_usd']],
                    on='wallet_address', validate='one_to_one')
    g = g.merge(baseline[['wallet_address', 'health_factor']], on='wallet_address', validate='one_to_one')
    g = g[g.net_equity_usd.gt(0)].copy()
    y = np.log1p(g.mean_bad_debt_ratio_t0.to_numpy(float) * 100)
    columns = ['intercept', 'weighted_token_pair_betweenness', 'eigenvector',
               'log_exposure_usd', 'cross_protocol_bridge', 'log_equity', 'initial_hf_capped_10']
    x = np.column_stack([np.ones(len(g)), g.weighted_token_pair_betweenness,
        g.eigenvector_centrality, np.log1p(g.weighted_strength_usd),
        g.is_cross_protocol_bridge.astype(float), np.log1p(g.net_equity_usd),
        np.minimum(g.health_factor.to_numpy(float), 10)])
    if not np.isfinite(x).all() or not np.isfinite(y).all():
        raise ValueError('Nonfinite structural regression inputs')
    beta, _, rank, _ = np.linalg.lstsq(x, y, rcond=None)
    design_pinv = np.linalg.pinv(x)
    residual = y - x @ beta
    leverage = np.sum(x * design_pinv.T, axis=1)
    robust_variance = (residual / np.maximum(1e-10, 1 - leverage)) ** 2
    covariance = (design_pinv * robust_variance) @ design_pinv.T
    se = np.sqrt(np.maximum(0, np.diag(covariance)))
    coefficients = []
    for name, value, error in zip(columns, beta, se):
        t = value / error if error > 0 else None
        coefficients.append(dict(variable=name, coefficient=float(value), hc3_std_error=float(error),
            p_value=float(2 * stats.t.sf(abs(t), len(g) - rank)) if t is not None else None))
    return dict(outcome='log1p_mean_BDR_percent_under_shared_MC_scenarios', observations=len(g),
        design_rank=int(rank), coefficients=coefficients,
        interpretation='descriptive_conditional_association_not_causal;_HC3_does_not_remove_network_dependence',
        power_law_exposure=power_law_tail_diagnostic(g.weighted_strength_usd),
        power_law_betweenness=power_law_tail_diagnostic(g.weighted_token_pair_betweenness))
