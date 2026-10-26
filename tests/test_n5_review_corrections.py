"""Economic regression tests for the research review corrections."""
import numpy as np
import pandas as pd
import pytest
from scipy import sparse

from src.models.cascade import Asset, Wallet
from src.models.n5_corrected_research import BoundedCurve, execute, degree_preserving_swap, normalize_compound_usd
from scripts.n5_resolve_research_review import mc_exact, load_snapshot
from src.models.n5_comet_execution import simulate_comet
from src.models.n5_network_statistics import token_pair_betweenness, power_law_tail_diagnostic


def routes():
    return {('eth', 'usd'): dict(pool_address='pool', fee_rate=0,
        curve=BoundedCurve(pd.DataFrame(dict(trade_size_usd=[1., 1000.], impact_pct=[.1, 20.]))))}


def wallet(name, collateral, debt):
    return Wallet(name, {'eth': collateral}, {'usd': debt}, {'eth': True}, {'eth': .8}, {'eth': .05})


def test_feedback_activates_new_wallet_and_zero_control_does_not():
    assets = {'eth': Asset(1), 'usd': Asset(1)}
    wallets = [wallet('a', 100, 85), wallet('b', 100, 79.5)]
    fixed = execute(wallets, assets, {'eth': 1, 'usd': 1}, routes(), gas=0)
    endo = execute(wallets, assets, {'eth': 1, 'usd': 1}, routes(), gas=0, feedback=1)
    assert fixed.summary['newly_eligible_round_ge_2'] == 0
    assert endo.summary['newly_eligible_round_ge_2'] == 1
    assert endo.summary['first_executed_round_ge_2'] == 1
    assert wallets[0].debt['usd'] == 85  # Caller state is immutable.
    assert endo.prices['eth'] < 1


def test_missing_quote_remains_unknown_and_no_feedback_without_sales():
    assets = {'eth': Asset(1), 'usd': Asset(1)}
    result = execute([wallet('a', 100, 85)], assets, {'eth': 1, 'usd': 1}, {}, feedback=1)
    assert result.summary['unsupported_route_attempts'] > 0
    assert not result.transactions
    assert result.prices['eth'] == 1


def test_reference_volume_tracks_token_quantity_when_oracle_moves():
    curve = routes()[('eth', 'usd')]['curve']
    assert curve.incremental(0, 100) == pytest.approx(curve.cost(100) / 100)
    assert curve.incremental(100, 100) == pytest.approx((curve.cost(200) - curve.cost(100)) / 100)
    with pytest.raises(ValueError):
        curve.incremental(950, 100)
    with pytest.raises(ValueError):
        curve.incremental(0, .5)


def test_bipartite_null_preserves_each_wallet_and_token_degree_and_wallet_strength():
    matrix = sparse.csr_matrix([[1., 2., 0., 0.], [0., 3., 4., 0.], [0., 0., 5., 6.], [7., 0., 0., 8.]])
    result, meta = degree_preserving_swap(matrix, seed=42)
    assert meta['accepted_swaps'] > 0
    np.testing.assert_array_equal(matrix.getnnz(axis=0), result.getnnz(axis=0))
    np.testing.assert_array_equal(matrix.getnnz(axis=1), result.getnnz(axis=1))
    np.testing.assert_allclose(matrix.sum(1), result.sum(1))
    assert (matrix != result).nnz > 0


def test_compound_weth_numeraire_requires_exact_snapshot_and_preserves_native():
    weth = '0xc02aaa39b223fe8d0a0e5c4f27ead9083c756cc2'
    exposure = pd.DataFrame([dict(snapshot_block=10, market_id='compound_v3_ethereum_weth',
        token_address=weth, asset_role='base_supply', amount_token=2., price_usd=1., value_usd=2.)])
    oracle = pd.DataFrame([dict(snapshot_block=10, token_address=weth, price_usd=2500.)])
    result = normalize_compound_usd(exposure, oracle)
    assert result.value_usd.item() == 5000
    assert result.native_value.item() == 2
    pd.testing.assert_frame_equal(normalize_compound_usd(result, oracle), result)
    with pytest.raises(ValueError):
        normalize_compound_usd(exposure, oracle.assign(snapshot_block=11))
    assert exposure.value_usd.item() == 2


def test_mc_executes_exact_pairs_and_includes_zero_scenarios_in_es():
    rows = []
    for name in ['a', 'b']:
        rows.extend([dict(snapshot_block=10, wallet_address=name, token_address='eth', symbol='WETH',
            collateral_amount=300., debt_amount=0., is_collateral_enabled=True,
            effective_lt=.8, effective_bonus=.05, price_usd=1.),
            dict(snapshot_block=10, wallet_address=name, token_address='usd', symbol='USDC',
            collateral_amount=0., debt_amount=250., is_collateral_enabled=False,
            effective_lt=.8, effective_bonus=.05, price_usd=1.)])
    baseline = pd.DataFrame([dict(snapshot_block=10, wallet_address=name, debt_usd=250., collateral_usd=300.,
        net_equity_usd=50., is_recursive_loop=i == 0, equity_bucket='small') for i, name in enumerate(['a', 'b'])])
    snap = load_snapshot(pd.DataFrame(rows), baseline)
    curve = BoundedCurve(pd.DataFrame(dict(trade_size_usd=[1., 1e6], impact_pct=[.001, .01])))
    scenarios = pd.DataFrame(dict(scenario_id=[f's{i}' for i in range(100)], eth_shock=[-.3] + [0.] * 99, depeg_shock=0.))
    risk, _, _, execution = mc_exact(snap, scenarios, {('eth', 'usd'): dict(curve=curve, pool_address='p', fee_rate=0)})
    np.testing.assert_allclose(risk.probability_bad_debt, .01)
    np.testing.assert_allclose(risk.es99_bad_debt_usd, 50)
    np.testing.assert_allclose(risk.mean_bad_debt_usd, .5)
    np.testing.assert_allclose(risk.mean_pre_execution_bad_debt_usd, .4)
    assert execution.iloc[0].executed_liquidations == 2


def test_comet_absorb_credits_supply_and_resells_from_protocol_inventory():
    p = pd.DataFrame([
        dict(snapshot_block=10, market_id='comet', wallet_address='a', token_address='usd',
             asset_role='base_borrow', amount_token=85., price_usd=1., value_usd=85., liquidate_collateral_factor=0.),
        dict(snapshot_block=10, market_id='comet', wallet_address='a', token_address='eth',
             asset_role='collateral', amount_token=100., price_usd=1., value_usd=100., liquidate_collateral_factor=.8)])
    cfg = pd.DataFrame([dict(snapshot_block=10, market_id='comet', token_address='eth',
                            price_usd=1., scale=1., liquidation_factor=.9)])
    state = dict(snapshot_block=10, market_id='comet', baseToken='usd', baseScale='1',
        getReserves='1000', targetReserves='2000', storeFrontPriceFactor=str(5 * 10**17),
        isAbsorbPaused=False, isBuyPaused=False, collateral_reserves_raw={'eth': '0'})
    events, summary = simulate_comet(p, cfg, state, {}, routes(), gas_usd=0)
    absorbed = next(e for e in events if e['action'] == 'absorb')
    bought = next(e for e in events if e['action'] == 'buy_collateral')
    assert absorbed['debt_cleared_usd'] == 85
    assert absorbed['borrower_base_supply_credit_usd'] == 5
    assert absorbed['reserves_delta_base'] == -90
    assert bought['base_paid'] == pytest.approx(95)
    assert summary['final_reserves_base'] == pytest.approx(1005)
    assert summary['residual_inventory_tokens']['eth'] == 0
    blocked, s = simulate_comet(p, cfg, dict(state, isAbsorbPaused=True), {}, routes(), gas_usd=0)
    assert blocked[0]['action'] == 'absorb_blocked_paused'
    assert s['final_reserves_base'] == 1000


def test_weighted_token_pair_betweenness_counts_tied_paths_and_retains_leaf_wallet():
    # Two equal bridges between the same two tokens split the shortest paths.
    # The degree-one wallet is retained in the answer with zero centrality.
    m = sparse.csr_matrix([[1., 1.], [1., 1.], [50., 0.]])
    np.testing.assert_allclose(token_pair_betweenness(m), [.5, .5, 0.])
    # Larger exposure means shorter inverse-exposure distance.
    m = sparse.csr_matrix([[2., 2.], [1., 1.]])
    np.testing.assert_allclose(token_pair_betweenness(m), [1., 0.])


def test_tail_diagnostic_handles_degenerate_data_and_is_reproducible():
    assert power_law_tail_diagnostic(np.ones(100))['status'] == 'insufficient_nonconstant_tail'
    x = np.linspace(1, 100, 200)
    a = power_law_tail_diagnostic(x, n_boot=10)
    assert a == power_law_tail_diagnostic(x, n_boot=10)
    assert 0 < a['ks_parametric_bootstrap_p'] <= 1


def test_token_pair_paths_keep_positive_edges_after_extreme_distance_prefix():
    m = sparse.csr_matrix([[1e-30, 1e12, 0.], [0., 1e12, 1e12], [0., 1e12, 1e12]])
    np.testing.assert_allclose(token_pair_betweenness(m), [2/3, 1/3, 1/3])
