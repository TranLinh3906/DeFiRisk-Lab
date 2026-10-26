"""Design guards: temporal leakage, matched sampling, shape tests and interventions."""
import numpy as np
import pandas as pd
import pytest
from src.models.n5_hypothesis_methods import past_returns, equity_pairs, convexity_test, u_shape_tests
from src.models.n5_corrected_research import execute, BoundedCurve
from src.models.cascade import Asset, Wallet
from scripts.n5_complete_hypotheses import null_associations
from scripts.n5_hypothesis_report import add_turning_point_intervals


def levels():
    dates = pd.date_range('2020-01-01', periods=8, tz='UTC')
    return pd.DataFrame(dict(date=dates, eth_usd=np.arange(8)+100., steth_eth=np.arange(8)*.001+1,
        eth_quote_utc=dates+pd.Timedelta(hours=23), steth_quote_utc=dates+pd.Timedelta(hours=23)))


def test_calibration_rejects_future_quotes_and_does_not_bridge_calendar_gaps():
    p = levels()
    out = past_returns(p.drop(index=3), '2020-01-09T00:00:00Z')
    assert len(out) == 5
    assert pd.Timestamp('2020-01-05', tz='UTC') not in set(out.date)
    with pytest.raises(ValueError, match='Future'):
        past_returns(p, '2020-01-05T00:00:00Z')


def test_equity_pairs_are_close_unique_and_do_not_invent_controls():
    b = pd.DataFrame(dict(wallet_address=['l1','l2','l3','c1','c2'],
        net_equity_usd=[100.,101.,1e9,100.5,102.], is_recursive_loop=[True,True,True,False,False]))
    matched = equity_pairs(b)
    assert len(matched) == 2
    assert not matched.non_looping_wallet.duplicated().any()
    assert matched.absolute_log_equity_gap.le(.1).all()
    assert 'l3' not in set(matched.looping_wallet)


def test_convexity_detects_direction_instead_of_forcing_amplification():
    x = np.linspace(0, .15, 31)
    convex = convexity_test(x, np.array([x*x, 2*x*x]), n_boot=30)
    concave = convexity_test(x, np.array([-x*x, -2*x*x]), n_boot=30)
    assert convex['positive_quadratic_curvature_supported']
    assert convex['discrete_convex_everywhere']
    assert not concave['positive_quadratic_curvature_supported']


def test_u_shape_requires_two_boundary_slopes_and_internal_turning_point():
    rows = []
    for s in ['A','B1','B2']:
        for scenario in ['x','y']:
            for cf in np.arange(.2, 1.001, .1):
                rows.append(dict(snapshot_id=s, scenario_id=scenario, fixed_cf=round(cf,2), depth_scale=1.,
                    feedback_mode='fixed', post_execution_bdr_pct=100*(cf-.6)**2, outside_quote_domain_attempts=0))
    result = u_shape_tests(pd.DataFrame(rows), n_boot=30)[0]
    assert result['u_shape_supported_on_grid']
    assert result['turning_point'] == pytest.approx(.6)
    enriched = add_turning_point_intervals(pd.DataFrame(rows), [result], n_boot=30)[0]
    assert enriched['turning_point_ci95_positive_curvature_draws'] == pytest.approx([.6,.6])
    assert enriched['u_shape_supported_with_turning_ci']
    for row in rows:
        row['post_execution_bdr_pct'] = row['fixed_cf']**2
    assert not u_shape_tests(pd.DataFrame(rows), n_boot=30)[0]['u_shape_supported_on_grid']


def test_source_intervention_retains_balance_and_fixed_cf_measures_actual_sales():
    w = Wallet('a', {'eth':100.}, {'usd':85.}, {'eth':True}, {'eth':.8}, {'eth':.05})
    assets={'eth':Asset(1.), 'usd':Asset(1.)}
    curve=BoundedCurve(pd.DataFrame(dict(trade_size_usd=[1.,10000.],impact_pct=[.001,.01])))
    routes={('eth','usd'):dict(pool_address='p',curve=curve,fee_rate=0.)}
    result=execute([w],assets,{'eth':1.,'usd':1.},routes,fixed_cf=.2,gas=0.)
    assert result.summary['round_1_sold_collateral_usd'] == pytest.approx(85*.2*1.05)
    blocked=execute([w],assets,{'eth':1.,'usd':1.},routes,blocked_sellers={'a'},feedback=1.)
    assert not blocked.transactions and blocked.wallets[0].debt['usd']==85.
    assert blocked.first_eligible_round['a']==1
    assert not blocked.first_executed_round
    with pytest.raises(ValueError):
        execute([w],assets,{'eth':1.,'usd':1.},routes,fixed_cf=1.1)


def test_constant_spillover_is_not_reported_as_a_significant_network_test():
    nodes=pd.DataFrame(dict(weighted_token_pair_betweenness=[.1,.2],degree=[2,2],weighted_strength_usd=[10.,20.]))
    assert null_associations(nodes,np.zeros(2))['status'].startswith('not_identified')
