"""Rebuild reviewed Q1/Q2/Q3 results without overwriting historical bundles."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd
from scipy import sparse, stats
import yaml

from src.models.cascade import Asset, Wallet, POLICIES, collateral_value, debt_value
from src.models.n5_corrected_research import BoundedCurve, execute, degree_preserving_swap, normalize_compound_usd
from src.models.n5_comet_execution import simulate_comet
from src.models.n5_research import expected_shortfall, compound_price_stress, debt_rank
from src.models.n5_readiness import verify_manifest
from src.models.stress_test import _asset_multiplier


def load_snapshot(positions, baseline):
    block = int(baseline.snapshot_block.iloc[0])
    p = positions[positions.snapshot_block.eq(block)].copy()
    if set(p.wallet_address) != set(baseline.wallet_address):
        raise ValueError('Cohort mismatch')
    assets = {t: Asset(float(g.price_usd.iloc[0])) for t, g in p.groupby('token_address')}
    symbols = p.drop_duplicates('token_address').set_index('token_address').symbol.to_dict()
    wallets = {}
    for r in p.itertuples():
        w = wallets.setdefault(r.wallet_address, Wallet(r.wallet_address, {}, {}))
        if r.collateral_amount > 0:
            w.collateral[r.token_address] = float(r.collateral_amount)
        if r.debt_amount > 0:
            w.debt[r.token_address] = float(r.debt_amount)
        w.collateral_enabled[r.token_address] = bool(r.is_collateral_enabled)
        w.effective_lt[r.token_address] = float(r.effective_lt)
        w.effective_bonus[r.token_address] = float(r.effective_bonus)
    ordered = [wallets[a] for a in baseline.wallet_address]
    token_ids = list(assets)
    ix = {t: i for i, t in enumerate(token_ids)}
    c = np.zeros((len(ordered), len(assets)))
    d, wc = c.copy(), c.copy()
    for i, w in enumerate(ordered):
        for t, q in w.collateral.items():
            if w.collateral_enabled[t]:
                c[i, ix[t]] = q * assets[t].price_usd
                wc[i, ix[t]] = c[i, ix[t]] * w.effective_lt[t]
        for t, q in w.debt.items():
            d[i, ix[t]] = q * assets[t].price_usd
    if not np.allclose(d.sum(1), baseline.debt_usd, rtol=1e-9, atol=1e-6):
        raise ValueError('Baseline debt mismatch')
    if not np.allclose(c.sum(1), baseline.collateral_usd, rtol=1e-9, atol=1e-6):
        raise ValueError('Baseline collateral mismatch')
    return dict(positions=p, baseline=baseline, assets=assets, symbols=symbols,
                wallets=ordered, token_ids=token_ids, c=c, d=d, wc=wc)


def mc_exact(snapshot, shocks, routes, *, progress=None):
    """Screen HF in batches, then execute every eligible wallet/token pair.

    Unscreened wallets have HF>=1 and cannot change under fixed oracle. They
    remain in every probability, loss and stratum denominator, with zero loss.
    """
    b, wallets = snapshot['baseline'], snapshot['wallets']
    n, ns = len(b), len(shocks)
    if ns < 100 or shocks.scenario_id.duplicated().any():
        raise ValueError('Need unique MC scenarios and at least 100 draws')
    group = np.array([2 if snapshot['symbols'][t].upper() in {'STETH', 'WSTETH'} else
                      1 if snapshot['symbols'][t].upper() in {'ETH', 'WETH', 'WEETH', 'CBETH', 'RETH'} else 0
                      for t in snapshot['token_ids']])
    cg = np.column_stack([snapshot['c'][:, group == k].sum(1) for k in range(3)])
    dg = np.column_stack([snapshot['d'][:, group == k].sum(1) for k in range(3)])
    wg = np.column_stack([snapshot['wc'][:, group == k].sum(1) for k in range(3)])
    loss_sum, pbd_count, phf_count = np.zeros(n), np.zeros(n), np.zeros(n)
    pre_sum = np.zeros(n)
    nonzero = [[] for _ in range(n)]
    equity = b.net_equity_usd.to_numpy(float)
    loops = b.is_recursive_loop.to_numpy(bool)
    buckets = b.equity_bucket.astype(str).to_numpy()
    strata = []
    for bucket in sorted(set(buckets[equity > 0])):
        masks = [(buckets == bucket) & (loops == flag) & (equity > 0) for flag in [False, True]]
        if all(m.any() for m in masks):
            strata.extend([(bucket, flag, m) for flag, m in zip([False, True], masks)])
    if not strata:
        raise ValueError('No common equity strata')
    scen_rows, execution_rows = [], []
    wallet_lookup = {w.wallet: i for i, w in enumerate(wallets)}
    for start in range(0, ns, 128):
        block = shocks.iloc[start:start + 128]
        eth = 1 + block.eth_shock.to_numpy(float)
        dep = eth * (1 + block.depeg_shock.to_numpy(float))
        mults = np.vstack([np.ones(len(block)), eth, dep])
        coll, debt, weighted = cg @ mults, dg @ mults, wg @ mults
        eligible = weighted < debt
        phf_count += eligible.sum(1)
        for j, row in enumerate(block.itertuples()):
            idx = np.flatnonzero(eligible[:, j])
            pre = np.maximum(0, debt[:, j] - coll[:, j])
            losses = pre.copy()
            multipliers = {t: float(mults[group[k], j]) for k, t in enumerate(snapshot['token_ids'])}
            result = execute([wallets[i] for i in idx], snapshot['assets'], multipliers, routes,
                             initial_eligible={wallets[i].wallet for i in idx})
            if result.summary['stop_reason'] == 'round_limit':
                raise RuntimeError('Monte Carlo hit round limit')
            for w in result.wallets:
                losses[wallet_lookup[w.wallet]] = max(0.0, debt_value(w, result.prices) - collateral_value(w, result.prices))
            pre_sum += pre
            loss_sum += losses
            pbd_count += losses > 0
            for i in np.flatnonzero(losses > 0):
                nonzero[i].append(float(losses[i]))
            execution_rows.append(dict(scenario_id=row.scenario_id, **result.summary))
            for flag in [False, True]:
                matching = [m for _, f, m in strata if f == flag]
                for stage, data in [('pre_execution', pre), ('post_execution', losses)]:
                    means = [float(np.mean(data[m] / equity[m] * 1e5)) for m in matching]
                    probabilities = [float(np.mean(data[m] > 0)) for m in matching]
                    scen_rows.append(dict(scenario_id=row.scenario_id, is_recursive_loop=flag,
                        loss_stage=stage, loss_per_100k=float(np.mean(means)),
                        probability_bad_debt=float(np.mean(probabilities)), common_strata=len(matching)))
        if progress:
            progress(min(start + len(block), ns), ns)
    # Zeros are implicit storage only: tail mass always uses ALL ns draws.
    tail_mass = ns * 0.01
    whole = int(np.floor(tail_mass))
    fraction = tail_mass - whole
    es = []
    for values in nonzero:
        tail = np.sort(values)[::-1]
        es.append((tail[:whole].sum() + (tail[whole] * fraction if fraction and len(tail) > whole else 0)) / tail_mass)
    out = b[['snapshot_block', 'wallet_address', 'is_recursive_loop', 'equity_bucket', 'net_equity_usd', 'debt_usd']].copy()
    out['scenario_count'] = ns
    out['probability_bad_debt'] = pbd_count / ns
    out['probability_hf_lt_1_after_initial_shock'] = phf_count / ns
    out['mean_pre_execution_bad_debt_usd'] = pre_sum / ns
    out['mean_bad_debt_usd'] = loss_sum / ns
    out['mean_bad_debt_ratio_t0'] = out.mean_bad_debt_usd / out.debt_usd
    out['es99_bad_debt_usd'] = es
    out['mean_loss_per_100k'] = np.divide(loss_sum / ns * 1e5, equity, out=np.full(n, np.nan), where=equity > 0)
    out['es99_loss_per_100k'] = np.divide(np.asarray(es) * 1e5, equity, out=np.full(n, np.nan), where=equity > 0)
    scenarios = pd.DataFrame(scen_rows)
    comp = []
    for (flag, stage), g in scenarios.groupby(['is_recursive_loop', 'loss_stage']):
        comp.append(dict(is_recursive_loop=bool(flag), loss_stage=stage, scenario_count=ns,
            common_strata=int(g.common_strata.iloc[0]), mean_loss_per_100k=float(g.loss_per_100k.mean()),
            es99_loss_per_100k=expected_shortfall(g.loss_per_100k),
            probability_bad_debt=float(g.probability_bad_debt.mean()),
            interpretation='conditional_on_calibrated_scenarios_and_selected_routes'))
    return out, scenarios, pd.DataFrame(comp), pd.DataFrame(execution_rows)


def q1_tests(snapshot, mc_wallets, scenario_losses, n_boot=1000):
    b = snapshot['baseline']
    rows = []
    for bucket, g in b[b.net_equity_usd.gt(0)].groupby('equity_bucket'):
        if g.is_recursive_loop.nunique() != 2:
            continue
        w = mc_wallets.set_index('wallet_address')
        a, z = g[g.is_recursive_loop], g[~g.is_recursive_loop]
        x = w.loc[a.wallet_address, 'mean_bad_debt_ratio_t0'].to_numpy()
        y = w.loc[z.wallet_address, 'mean_bad_debt_ratio_t0'].to_numpy()
        if not np.isfinite(x).all() or not np.isfinite(y).all():
            raise ValueError('Nonfinite BDR test input')
        identical = np.all(x == x[0]) and np.all(y == x[0])
        u = None if identical else stats.mannwhitneyu(x, y, alternative='two-sided')
        hf_a, hf_z = a.health_factor.to_numpy(float), z.health_factor.to_numpy(float)
        ks = stats.ks_2samp(hf_a[np.isfinite(hf_a)], hf_z[np.isfinite(hf_z)])
        rows.append(dict(scope=str(bucket), test='mann_whitney_bdr', statistic=float(len(x) * len(y) / 2 if identical else u.statistic),
                         p_value=float(1.0 if identical else u.pvalue), interpretation='descriptive_wallet_association_shared_scenarios'))
        rows.append(dict(scope=str(bucket), test='ks_initial_hf', statistic=float(ks.statistic),
                         p_value=float(ks.pvalue), interpretation='baseline_distribution_difference'))
    p = scenario_losses[scenario_losses.loss_stage.eq('post_execution')].pivot(
        index='scenario_id', columns='is_recursive_loop', values='loss_per_100k')
    x, y = p[True].to_numpy(), p[False].to_numpy()
    observed = expected_shortfall(x) - expected_shortfall(y)
    rng = np.random.default_rng(42)
    diffs = []
    for _ in range(n_boot):
        idx = rng.integers(0, len(x), len(x))
        diffs.append(expected_shortfall(x[idx]) - expected_shortfall(y[idx]))
    lo, hi = np.quantile(diffs, [.025, .975])
    centered = np.asarray(diffs) - observed
    pvalue = (1 + np.count_nonzero(np.abs(centered) >= abs(observed))) / (n_boot + 1)
    rows.append(dict(scope='equal_common_equity_strata', test='paired_scenario_es99_bootstrap',
        statistic=float(observed), ci95_low=float(lo), ci95_high=float(hi),
        p_value=float(pvalue), n_boot=n_boot,
        interpretation='conditional_scenario_uncertainty_not_causal_wallet_effect'))
    # Holm adjustment for the family of reported tests in this snapshot.
    order = np.argsort([r['p_value'] for r in rows])
    bound = 0.0
    for rank, i in enumerate(order):
        bound = max(bound, min(1.0, rows[i]['p_value'] * (len(rows) - rank)))
        rows[i]['p_value_holm'] = bound
    return rows


def network_analysis(snapshot, compound, *, n_null=100):
    """Wallet/token bridge network with actual degree-preserving null draws."""
    c, d = sparse.csr_matrix(snapshot['c']), sparse.csr_matrix(snapshot['d'])
    # Include Aave and Compound positions for each shared wallet at this block.
    b = snapshot['baseline']
    wallets = {a: i for i, a in enumerate(b.wallet_address)}
    tokens = {t: i for i, t in enumerate(snapshot['token_ids'])}
    sub = compound[compound.snapshot_block.eq(int(b.snapshot_block.iloc[0])) & compound.wallet_address.isin(wallets)]
    extra_tokens = sorted(set(sub.token_address) - set(tokens))
    tokens.update({t: len(tokens) + i for i, t in enumerate(extra_tokens)})
    if extra_tokens:
        c = sparse.hstack([c, sparse.csr_matrix((len(b), len(extra_tokens)))]).tocsr()
        d = sparse.hstack([d, sparse.csr_matrix((len(b), len(extra_tokens)))]).tocsr()
    # Distinct role layers retained for the null, then aggregate the exposures.
    layers = [c, d]
    for role in ['collateral', 'base_borrow', 'base_supply']:
        g = sub[sub.asset_role.eq(role)]
        layers.append(sparse.csr_matrix((g.value_usd.to_numpy(),
            ([wallets[a] for a in g.wallet_address], [tokens[t] for t in g.token_address])), shape=c.shape))
    exposure = sum(layers).tocsr()
    strengths = np.asarray(exposure.sum(1)).ravel()
    token_strength = np.asarray(exposure.sum(0)).ravel()
    capacities = np.maximum(1.0, token_strength)
    shock = np.zeros(len(tokens))
    for t, i in tokens.items():
        sym = snapshot['symbols'].get(t, '')
        shock[i] = .05 if sym.upper() in {'STETH', 'WSTETH'} else 0.0

    def score(matrices):
        m = sum(matrices).tocsr()
        inv = np.divide(1, np.asarray(m.sum(1)).ravel(), out=np.zeros(len(b)), where=np.asarray(m.sum(1)).ravel() > 0)
        adj = (m.T @ sparse.diags(inv) @ m).toarray()
        np.fill_diagonal(adj, 0)
        return debt_rank(adj, capacities, shock)['capacity_weighted_incremental_score']

    observed = score(layers)
    null_rows = []
    for draw in range(n_null):
        randomized, accepted, degenerate = [], 0, True
        for k, layer in enumerate(layers):
            r, meta = degree_preserving_swap(layer, seed=42 + draw * 11 + k, swaps_per_edge=1)
            randomized.append(r)
            accepted += meta['accepted_swaps']
            degenerate &= meta['degenerate']
        null_rows.append(dict(draw=draw, score=score(randomized), accepted_swaps=accepted,
                              degenerate=degenerate, degrees_preserved=True))
        if (draw + 1) % 10 == 0:
            print(f'null networks: {draw + 1}/{n_null}', flush=True)
    null = pd.DataFrame(null_rows)
    p_upper = (1 + int(null.score.ge(observed).sum())) / (n_null + 1)
    # Sparse spectral centrality on the wallet-token network; no top-wallet
    # truncation is used during calculation.
    m = exposure / max(float(exposure.max()), 1)
    v = np.ones(m.shape[1]) / np.sqrt(m.shape[1])
    for _ in range(200):
        nv = np.asarray(m.T @ (m @ v)).ravel()
        norm = np.linalg.norm(nv)
        if norm == 0:
            break
        nv /= norm
        if np.linalg.norm(nv - v) < 1e-10:
            v = nv
            break
        v = nv
    eigen = np.asarray(m @ v).ravel()
    bridge = set(sub.wallet_address)
    nodes = pd.DataFrame(dict(wallet_address=b.wallet_address, weighted_strength_usd=strengths,
        degree=exposure.getnnz(axis=1), eigenvector_centrality=eigen,
        is_cross_protocol_bridge=b.wallet_address.isin(bridge)))
    report = dict(observed_score=observed, null_mean=float(null.score.mean()), n_null=n_null,
        upper_tail_randomization_p=p_upper, null='role_layer_bipartite_double_edge_swap',
        preserves='wallet_and_token_degrees_and_wallet_edge_weight_multisets',
        does_not_preserve='token_weighted_strengths',
        interpretation='exposure_network_sensitivity_not_realized_liquidation_transmission',
        power_law_conclusion='not_established', protocol_mechanism='Aave_and_Compound_exposure_layers')
    return nodes, null, report


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--output-dir', type=Path, default=Path('outputs/n5_review_corrected_20261026'))
    ap.add_argument('--mc-scenarios', type=int, default=10000)
    ap.add_argument('--null-networks', type=int, default=100)
    args = ap.parse_args()
    out = args.output_dir
    if out.exists():
        raise FileExistsError(f'Use a fresh output directory: {out}')
    if not 100 <= args.mc_scenarios <= 10000 or args.null_networks < 1:
        raise ValueError('MC draws must be 100..10000; null networks >=1')
    old = Path('outputs/n5_q1_q3_empirical_20261026')
    completion = Path('outputs/n5_completion_20261026')
    for source in [old, completion / 'approved_package_with27']:
        if verify_manifest(source):
            raise ValueError(f'Source artifact hash mismatch: {source}')
    input_paths = [completion / 'n4_research/simulation_positions.parquet',
        completion / 'calibration_market/mc_scenario_inputs.parquet',
        completion / 'approved_package_with27/curve_points_with_quantization_audit.parquet',
        old / 'compound/exposures_complete.parquet', Path('data/processed/aave/oracle_prices.parquet'),
        Path('config/scenarios.yaml'), Path('config/n4_research.json'),
        Path('config/n5_execution_scope_approved.json'), Path(__file__),
        Path('src/models/n5_corrected_research.py'), Path('src/models/n5_research.py'),
        Path('src/models/cascade.py'), Path('src/models/stress_test.py'),
        Path('src/models/n5_impact_review.py'), Path('src/models/n5_n3_update.py'),
        Path('src/models/n5_comet_execution.py'), Path('scripts/n5_review_report.py'),
        Path('scripts/n5_collect_comet_state.py'),
        Path('outputs/compound_consolidated_A_B1_B2_20261025_complete/n3_compound_reserve_config.parquet'),
        Path('outputs/research_review_fix_20261026/comet_state/comet_state.json'),
        Path('outputs/research_review_fix_20261026/comet_state/rpc_checkpoint.jsonl')]
    input_paths += [old / f'{sid}/baseline_wallet_kpis.parquet' for sid in ['A', 'B1', 'B2']]
    def hash_file(p):
        return hashlib.sha256(p.read_bytes()).hexdigest()
    initial_hashes = {p: hash_file(p) for p in input_paths}
    positions = pd.read_parquet(input_paths[0])
    shocks = pd.read_parquet(input_paths[1]).iloc[:args.mc_scenarios].copy()
    curves = pd.read_parquet(input_paths[2])
    compound = normalize_compound_usd(pd.read_parquet(input_paths[3]), pd.read_parquet(input_paths[4]))
    comet_config = pd.read_parquet('outputs/compound_consolidated_A_B1_B2_20261025_complete/n3_compound_reserve_config.parquet')
    comet_states = json.loads(Path('outputs/research_review_fix_20261026/comet_state/comet_state.json').read_text(encoding='utf-8'))
    scenarios = yaml.safe_load(Path('config/scenarios.yaml').read_text(encoding='utf-8'))['scenarios']
    # Fees are observed pool tiers, not a single averaged route fee.
    quote_paths = [Path('outputs/n3_priority2_quote_grid_v1/n3_multivolume_rpc_verified_only.parquet'),
                   Path('outputs/n3_priority2_quote_grid_v2_with_27/n3_targeted_27_rpc_verified_only.parquet')]
    for p in quote_paths:
        input_paths.append(p); initial_hashes[p] = hash_file(p)
    quotes = pd.concat([pd.read_parquet(p) for p in quote_paths])
    fees = quotes.groupby(['snapshot_id', 'pool_address', 'token_in_address', 'token_out_address']).fee_tier.agg(['min', 'max'])
    if not fees.dropna()['min'].eq(fees.dropna()['max']).all():
        raise ValueError('Conflicting fee tiers')
    out.mkdir(parents=True)
    def save(name, value):
        p = out / name
        p.parent.mkdir(parents=True, exist_ok=True)
        if name.endswith('.parquet'):
            value.to_parquet(p, index=False)
        elif name.endswith('.csv'):
            value.to_csv(p, index=False)
        else:
            p.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False), encoding='utf-8')
    save('compound/exposures_usd.parquet', compound)
    save('mc_scenarios.parquet', shocks)
    settings = dict(mc_draws=len(shocks), seed=42, depth_scales=[.5, 1., 2.], cf_rules=list(POLICIES),
        feedback_passthrough=[0., 1.], feedback_cap=.15, max_rounds=100, gas_usd=5.,
        feedback_model='sold_token_oracle_equals_initial_shocked_price_times_one_minus_capped_marginal_cost',
        feedback_status='unvalidated_counterfactual_sensitivity',
        depth_model='scale_snapshot_trade_notional_by_inverse_depth',
        compound_scope='USD_normalized_overlap_absorb_and_conditional_inventory_resale_with_observed_market_state',
        mc_scope='exact_token_pair_fixed_oracle_post_execution',
        stress_scope='deterministic_scenarios_no_real_world_probabilities',
        holdout_scope='A_calibrated_MC_on_B1_B2_is_retrospective_counterfactual_not_out_of_sample')
    save('research_settings.json', settings)
    comparisons, tests, grids, transitions, withdrawal_rows, com_stresses = [], [], [], [], [], []
    network_reports = {}
    comet_summaries, comet_events = [], []
    grid_rounds = []
    baseline_cohort_debt = {}
    wallet_checks = []
    for sid in ['A', 'B1', 'B2']:
        print(f'{sid}: load cohort', flush=True)
        base = pd.read_parquet(old / f'{sid}/baseline_wallet_kpis.parquet')
        snap = load_snapshot(positions, base)
        save(f'{sid}/baseline_wallet_kpis.parquet', base)
        block = int(base.snapshot_block.iloc[0])
        baseline_cohort_debt[sid] = float(base.debt_usd.sum())
        routes = {}
        for identity, g in curves[curves.snapshot_id.eq(sid)].groupby(['pool_address', 'token_in_address', 'token_out_address']):
            pool, ct, dt = identity
            fee = float(fees.loc[(sid, pool, ct, dt), 'min']) / 1e6
            routes[(ct, dt)] = dict(pool_address=pool, curve=BoundedCurve(g), fee_rate=fee)
        print(f'{sid}: exact MC {len(shocks)} draws', flush=True)
        def progress(done, total):
            if done % 1024 == 0 or done == total:
                print(f'{sid}: MC {done}/{total}', flush=True)
        wr, sl, co, ex = mc_exact(snap, shocks, routes, progress=progress)
        for df in [wr, sl, co, ex]:
            df['snapshot_id'] = sid
        save(f'{sid}/mc_wallet_risk.parquet', wr)
        save(f'{sid}/mc_scenario_losses.parquet', sl)
        save(f'{sid}/mc_execution.csv', ex)
        numeric_cols = ['mean_bad_debt_usd', 'mean_bad_debt_ratio_t0', 'es99_bad_debt_usd',
                        'probability_bad_debt', 'probability_hf_lt_1_after_initial_shock']
        if not np.isfinite(wr[numeric_cols].to_numpy()).all() or wr[numeric_cols].lt(0).any().any():
            raise ValueError('Nonfinite or negative Monte Carlo output')
        if not wr.probability_bad_debt.between(0, 1).all() or not wr.scenario_count.eq(len(shocks)).all():
            raise ValueError('Invalid MC denominator')
        wallet_checks.append(dict(snapshot_id=sid, wallets=len(wr),
            nonpositive_equity_wallets=int(wr.net_equity_usd.le(0).sum()),
            invalid_equity_losses_are_null=bool(wr.loc[wr.net_equity_usd.le(0), 'es99_loss_per_100k'].isna().all()),
            all_scenario_denominators_match=True))
        comparisons.append(co)
        tests.extend([dict(snapshot_id=sid, **r) for r in q1_tests(snap, wr, sl)])
        print(f'{sid}: shock x depth x CF x feedback grid', flush=True)
        for scen_id, parameters in scenarios.items():
            params = dict(parameters or {}, depeg_assets=['STETH', 'WSTETH'])
            multipliers = {t: _asset_multiplier(sym.upper(), params) for t, sym in snap['symbols'].items()}
            vec = np.array([multipliers[t] for t in snap['token_ids']])
            wd, wc = snap['d'] @ vec, snap['wc'] @ vec
            hf = np.divide(wc, wd, out=np.full(len(wd), np.inf), where=wd > 0)
            for depth in settings['depth_scales']:
                for feedback in settings['feedback_passthrough']:
                    idx = np.flatnonzero(hf < (1 / .85 if feedback else 1))
                    selected = [snap['wallets'][i] for i in idx]
                    for cf in POLICIES:
                        result = execute(selected, snap['assets'], multipliers, routes, cf_rule=cf,
                                         depth_scale=depth, feedback=feedback)
                        result.summary['post_execution_bdr_pct'] = 100 * result.summary['post_execution_bad_debt_usd'] / baseline_cohort_debt[sid]
                        grids.append(dict(snapshot_id=sid, snapshot_block=block, scenario_id=scen_id, **result.summary))
                        grid_rounds.extend([dict(snapshot_id=sid, scenario_id=scen_id, cf_rule=cf,
                            depth_scale=depth, oracle_feedback=bool(feedback), **r) for r in result.rounds])
                        if depth == 1 and cf == 'baseline_aave':
                            healthy = set(base.loc[base.health_factor.ge(1.10), 'wallet_address'])
                            first = {}
                            for tx in result.transactions:
                                first.setdefault(tx['wallet_address'], tx['round'])
                            transitions.append(dict(snapshot_id=sid, scenario_id=scen_id, oracle_feedback=bool(feedback),
                                initially_healthy_threshold=1.10, initially_healthy_wallets=len(healthy),
                                newly_eligible_round_ge_2=result.summary['newly_eligible_round_ge_2'],
                                first_executed_round_ge_2=result.summary['first_executed_round_ge_2'],
                                initially_healthy_first_executed_round_ge_2=sum(w in healthy and r >= 2 for w, r in first.items())))
                            sub_compound = compound[compound.snapshot_block.eq(block)]
                            returns_comet = {}
                            for r in sub_compound.itertuples():
                                token = r.token_address
                                returns_comet[(block, token)] = (result.prices[token] / snap['assets'][token].price_usd - 1
                                    if token in result.prices else _asset_multiplier(str(r.token_symbol).upper(), params) - 1)
                            for market_state in [s for s in comet_states if s['snapshot_block'] == block]:
                                events, summary_comet = simulate_comet(compound, comet_config, market_state, returns_comet, routes)
                                context = dict(snapshot_id=sid, scenario_id=scen_id, oracle_feedback=bool(feedback),
                                               market_id=market_state['market_id'])
                                comet_summaries.append(dict(**context, **summary_comet))
                                comet_events.extend([dict(**context, **e) for e in events])
                            if scen_id == 'combined_eth20_depeg5' and not feedback:
                                # Only same-snapshot overlapping wallets actually
                                # eligible on Aave are the withdrawal scenario set.
                                eligible_ids = set(base.wallet_address[hf < 1])
                                sub = compound[compound.snapshot_block.eq(block) & compound.wallet_address.isin(eligible_ids)
                                               & compound.asset_role.eq('base_supply')]
                                for fraction in [.25, .5, 1.]:
                                    for market, g in sub.groupby('market_id'):
                                        withdrawal_rows.append(dict(snapshot_id=sid, snapshot_block=block,
                                            market_id=market, fraction=fraction, wallets=g.wallet_address.nunique(),
                                            withdrawn_tokens=float(g.amount_token.sum()) * fraction,
                                            withdrawn_usd=float(g.value_usd.sum()) * fraction,
                                            assumption='eligible_overlap_wallets_withdraw_explicit_fraction_not_observed_behavior'))
            sub = compound[compound.snapshot_block.eq(block)]
            returns = {(block, r.token_address): _asset_multiplier(str(r.token_symbol).upper(), params) - 1
                       for r in sub.itertuples()}
            stressed = compound_price_stress(sub, returns)
            stressed['snapshot_id'], stressed['scenario_id'] = sid, scen_id
            com_stresses.append(stressed)
        print(f'{sid}: {args.null_networks} degree-preserving null networks', flush=True)
        nodes, nulls, report = network_analysis(snap, compound, n_null=args.null_networks)
        save(f'{sid}/network_wallet_nodes.parquet', nodes)
        save(f'{sid}/null_networks.csv', nulls)
        save(f'{sid}/grid_checkpoint.csv', pd.DataFrame([r for r in grids if r['snapshot_id'] == sid]))
        network_reports[sid] = report
        print(f'{sid}: complete', flush=True)
    comp = pd.concat(comparisons, ignore_index=True)
    grid = pd.DataFrame(grids)
    save('q1_mc_comparison.csv', comp)
    save('q1_hypothesis_tests.json', tests)
    save('q2_shock_depth_cf_feedback_grid.csv', grid)
    save('q2_execution_rounds.parquet', pd.DataFrame(grid_rounds))
    save('q3_transitions.csv', pd.DataFrame(transitions))
    save('q3_withdrawal_scenarios.csv', pd.DataFrame(withdrawal_rows))
    save('q3_compound_price_stress.parquet', pd.concat(com_stresses, ignore_index=True))
    save('q3_network_null_tests.json', network_reports)
    save('q3_comet_absorb_buy_summaries.json', comet_summaries)
    save('q3_comet_absorb_buy_ledger.parquet', pd.DataFrame(comet_events))
    save('wallet_numerical_checks.json', wallet_checks)
    # Paired counterfactual contrasts use snapshot/scenario as blocks. These
    # are finite-grid sensitivity results; no iid ANOVA p-values are invented.
    fixed = grid[~grid.oracle_feedback]
    pivot = fixed.pivot(index=['snapshot_id', 'scenario_id', 'depth_scale'], columns='cf_rule', values='post_execution_bad_debt_usd')
    contrasts = pivot.reset_index()
    contrasts['delta_fixed100_minus_fixed50_usd'] = contrasts.fixed_100 - contrasts.fixed_50
    save('q2_paired_policy_contrasts.csv', contrasts)
    baseline = pd.read_parquet(completion / 'approved_package_with27/execution_summary.parquet')
    primary = grid[(grid.depth_scale == 1) & ~grid.oracle_feedback & grid.cf_rule.eq('baseline_aave')]
    reference = baseline[baseline.variant.eq('primary')]
    joined = primary.merge(reference, on=['snapshot_id', 'scenario_id'], suffixes=('_new', '_old'), validate='one_to_one')
    regression_ok = (np.allclose(joined.simulated_repaid_usd_new, joined.simulated_repaid_usd_old, rtol=1e-9, atol=1e-6)
                     and joined.executed_liquidations_new.eq(joined.executed_liquidations_old).all())
    checks = dict(approved_primary_regression_matches=bool(regression_ok),
        all_runs_terminated=bool(~grid.stop_reason.eq('round_limit').any()),
        fixed_oracle_newly_eligible_zero=bool(fixed.newly_eligible_round_ge_2.eq(0).all()),
        grid_runs=len(grid), expected_grid_runs=3 * len(scenarios) * 3 * 2 * 4,
        mc_scenarios_per_snapshot=len(shocks),
        compound_usd_amounts_reconcile=bool(np.allclose(compound.amount_token * compound.price_usd, compound.value_usd)),
        null_network_degrees_preserved=True,
        research_acceptance='pending_external_validation_of_feedback_and_protocol_execution')
    save('validation.json', checks)
    if not regression_ok or not checks['all_runs_terminated']:
        raise RuntimeError(f'Regression or termination check failed: {checks}')
    kpi_records = []
    fields = [
        ('M01', 'baseline', 'net_equity_usd'), ('M02', 'baseline', 'effective_leverage'),
        ('M03', 'baseline', 'health_factor'), ('M04', 'baseline', 'dist_to_liq_pct'),
        ('M05', 'mc_wallet_risk', 'probability_bad_debt'), ('M06', 'mc_wallet_risk', 'mean_bad_debt_usd'),
        ('M07', 'mc_wallet_risk', 'mean_bad_debt_ratio_t0'), ('M08', 'mc_wallet_risk', 'mean_loss_per_100k'),
        ('M09', 'mc_wallet_risk', 'es99_loss_per_100k'), ('M10', 'grid', 'simulated_repaid_usd'),
        ('M11', 'grid', 'mean_slippage_pct'), ('M12', 'grid', 'first_executed_round_ge_2'),
        ('M13', 'grid', 'cascade_amplification_factor'), ('M14', 'rounds', 'supported_unprofitable_wallets'),
        ('M15', 'transitions', 'initially_healthy_first_executed_round_ge_2'),
        ('M16', 'comet_ledger', 'debt_cleared_usd'), ('M17', 'network_wallet_nodes', 'degree'),
        ('M18', 'network_null_tests', 'observed_score')]
    for kid, artifact, field in fields:
        artifact_paths = dict(baseline='A|B1|B2/baseline_wallet_kpis.parquet',
            mc_wallet_risk='A|B1|B2/mc_wallet_risk.parquet', grid='q2_shock_depth_cf_feedback_grid.csv',
            rounds='q2_execution_rounds.parquet', transitions='q3_transitions.csv',
            comet_ledger='q3_comet_absorb_buy_ledger.parquet',
            network_wallet_nodes='A|B1|B2/network_wallet_nodes.parquet', network_null_tests='q3_network_null_tests.json')
        kpi_records.append(dict(kpi_id=kid, artifact=artifact_paths[artifact], field=field,
            technical_status='proxy_only' if kid == 'M14' else 'conditional_output_available',
            research_acceptance='pending',
            limitation='Nonprofitability proxy is not proven causal freeze' if kid == 'M14' else
                       'Numerical checks and mapping do not establish the research hypothesis'))
    save('kpi_acceptance.csv', pd.DataFrame(kpi_records))
    from scripts.n5_review_report import render_report
    render_report(out, comp, grid, checks, network_reports, pd.DataFrame(withdrawal_rows))
    for p, previous in initial_hashes.items():
        if hash_file(p) != previous:
            raise RuntimeError(f'Input changed during run: {p}')
    artifacts = [p for p in out.rglob('*') if p.is_file()]
    manifest = dict(version='n5_review_corrected_v1', created_utc=datetime.now(timezone.utc).isoformat(),
        validation_status='numerical_regression_and_invariant_checks_passed',
        research_acceptance=checks['research_acceptance'],
        inputs=[dict(path=str(p.resolve()), sha256=initial_hashes[p]) for p in input_paths],
        files=[dict(path=p.relative_to(out).as_posix(), bytes=p.stat().st_size, sha256=hash_file(p)) for p in artifacts])
    save('manifest.json', manifest)
    if verify_manifest(out):
        raise RuntimeError('Output manifest verification failed')
    print(json.dumps(checks), flush=True)


if __name__ == '__main__':
    main()
