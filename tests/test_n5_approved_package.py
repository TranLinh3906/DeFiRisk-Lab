import pytest

from src.models.cascade import Asset, Wallet
from src.models.n5_impact_review import simulate_execution
from test_n5_impact_review import points
from src.models.n5_impact_review import ExecutionCurve


def test_cumulative_pool_does_not_reset_to_admit_unsupported_sales_next_round():
    assets = {'c': Asset(1, .8, .1), 'd': Asset(1)}
    wallets = [Wallet(str(i), {'c': 600}, {'d': 550}) for i in range(3)]
    routes = {('c', 'd'): dict(pool_address='pool', curve=ExecutionCurve(points()))}
    cumulative, _, tx, _ = simulate_execution(wallets, assets, {'c': 1, 'd': 1}, routes,
        method='linear', pool_state='cumulative_across_rounds')
    reset, _, reset_tx, _ = simulate_execution(wallets, assets, {'c': 1, 'd': 1}, routes,
        method='linear', pool_state='reset_each_round')
    assert sum(t['sale_snapshot_usd'] for t in tx) <= 1600.001
    assert reset['simulated_repaid_usd'] > cumulative['simulated_repaid_usd']
    assert sum(t['sale_snapshot_usd'] for t in reset_tx) > 1600.001


def test_lowest_hf_order_overrides_address_order():
    assets = {'c': Asset(1, .8, .1), 'd': Asset(1)}
    wallets = [Wallet('a_high', {'c': 600}, {'d': 550}), Wallet('z_low', {'c': 600}, {'d': 600})]
    _, _, tx, _ = simulate_execution(wallets, assets, {'c': 1, 'd': 1}, {}, method='no_impact', ordering='lowest_hf')
    assert tx[0]['wallet_address'] == 'z_low'


def test_collateral_margin_rule_differs_from_total_usd_profit():
    assets = {'small': Asset(1, .8, .1), 'big': Asset(1, .8, .05), 'd': Asset(1)}
    wallets = [Wallet('one', {'small': 100, 'big': 1000}, {'d': 1100})]
    args = wallets, assets, {t: 1 for t in assets}, {}
    _, _, by_profit, _ = simulate_execution(*args, method='no_impact', pair_rule='total_profit')
    _, _, by_margin, _ = simulate_execution(*args, method='no_impact', pair_rule='bonus_minus_impact')
    assert by_profit[0]['token_in_address'] == 'big'
    assert by_margin[0]['token_in_address'] == 'small'
    assert by_margin[0]['liquidator_profit_usd'] > 0


def test_random_order_is_reproducible_and_gas_can_prevent_execution():
    assets = {'c': Asset(1, .8, .1), 'd': Asset(1)}
    wallets = [Wallet(str(i), {'c': 600}, {'d': 550}) for i in range(8)]
    args = wallets, assets, {'c': 1, 'd': 1}, {}
    a = simulate_execution(*args, method='no_impact', ordering='random', seed=42)
    b = simulate_execution(*args, method='no_impact', ordering='random', seed=42)
    assert a[2] == b[2]
    c = simulate_execution(*args, method='no_impact', gas=60)
    assert c[0]['executed_liquidations'] == 0 and not c[0]['freeze_proven']


def test_same_token_identity_does_not_pay_dex_fee():
    assets = {'c': Asset(1, .5, .1)}
    wallets = [Wallet('one', {'c': 150}, {'c': 100})]
    _, _, tx, _ = simulate_execution(wallets, assets, {'c': 1}, {}, method='linear', fee=.99)
    assert tx and tx[0]['execution_fee_rate'] == 0
    assert tx[0]['liquidator_profit_usd'] == pytest.approx(5)


def test_pool_fee_replaces_uniform_fee_instead_of_adding_it_twice():
    assets = {'c': Asset(1, .8, .1), 'd': Asset(1)}
    wallets = [Wallet('one', {'c': 600}, {'d': 550})]
    routes = {('c', 'd'): dict(pool_address='pool', curve=ExecutionCurve(points()), fee_rate=.003)}
    _, _, tx, _ = simulate_execution(wallets, assets, {'c': 1, 'd': 1}, routes,
        method='linear', use_route_fees=True, fee=.99)
    assert tx[0]['execution_fee_rate'] == .003
    assert tx[0]['liquidator_profit_usd'] == pytest.approx(600*(1-.006)*(1-.003)-600/1.1-5)
