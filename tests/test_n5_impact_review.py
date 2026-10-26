import numpy as np
import pandas as pd
import pytest

from src.models.cascade import Asset, Wallet
from src.models.n5_impact_review import ExecutionCurve, heldout_quotes, simulate_execution


def points():
    x = np.array([100., 200., 400., 800., 1600., 1600.001])
    return pd.DataFrame(dict(snapshot_id='A', snapshot_block=1, pool_address='pool',
        token_in_address='c', token_out_address='d', trade_size_usd=x, impact_pct=.001*x))


def test_incremental_cost_telescopes_instead_of_reapplying_average():
    curve = ExecutionCurve(points())
    a = curve.incremental(0, 200, 'linear')
    b = curve.incremental(200, 200, 'linear')
    assert a == pytest.approx(.002)
    assert b == pytest.approx(.006)
    assert 200*a+200*b == pytest.approx(400*curve.average(400, 'linear'))


def test_cumulative_flow_refuses_extrapolation_even_when_individual_sales_fit():
    curve = ExecutionCurve(points())
    with pytest.raises(ValueError, match='outside_quote_domain'):
        curve.incremental(1500, 200, 'linear')
    with pytest.raises(ValueError, match='outside_quote_domain'):
        curve.incremental(10, 200, 'power')


def test_near_identical_anchors_held_out_together_and_edges_unscorable():
    evidence = heldout_quotes(points())
    anchors = evidence[evidence.heldout_size_usd.ge(1600)]
    assert anchors.fold.nunique() == 1
    assert anchors.status.eq('outside_training_domain').all()
    interior = evidence[evidence.status.eq('scored')]
    assert len(interior) == 6
    assert interior.error_percentage_points.abs().max() < 1e-8


def fixture():
    assets = {'c': Asset(1, .8, .1), 'd': Asset(1)}
    wallets = [Wallet('one', {'c': 600}, {'d': 550})]
    routes = {('c', 'd'): dict(pool_address='pool', curve=ExecutionCurve(points()))}
    return assets, wallets, routes


def test_real_execution_updates_balances_and_preserves_inputs_and_oracle():
    assets, wallets, routes = fixture()
    summary, rounds, tx, misses = simulate_execution(wallets, assets, {'c': 1, 'd': 1}, routes, method='linear')
    assert tx and summary['simulated_repaid_usd'] <= 550
    assert wallets[0].debt['d'] == 550
    assert assets['c'].price_usd == 1
    assert not summary['oracle_feedback'] and not summary['freeze_proven']
    assert tx[0]['slippage_pct'] == pytest.approx(.6)


def test_missing_route_is_unknown_and_demand_still_discoverable():
    assets, wallets, _ = fixture()
    s, _, tx, misses = simulate_execution(wallets, assets, {'c': 1, 'd': 1}, {}, method='linear')
    assert not tx and misses[0]['status'] == 'missing_exact_route'
    assert s['unknown_candidate_attempts'] > 0 and not s['freeze_proven']
    s, _, tx, _ = simulate_execution(wallets, assets, {'c': 1, 'd': 1}, {}, method='no_impact')
    assert tx and s['simulated_repaid_usd'] > 0


def test_shocked_sale_uses_snapshot_quantity_coordinate():
    assets, wallets, routes = fixture()
    _, _, tx, _ = simulate_execution(wallets, assets, {'c': .5, 'd': 1}, routes, method='linear')
    assert tx[0]['sold_usd'] == pytest.approx(300)
    assert tx[0]['sale_snapshot_usd'] == pytest.approx(600)
    assert tx[0]['slippage_pct'] == pytest.approx(.6)
