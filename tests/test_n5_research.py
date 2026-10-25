"""Economic invariants, time leakage and conditional research calculations."""
import unittest
import numpy as np
import pandas as pd

from src.models.n5_diagnostics import (tiny_shock_diagnostics, balance_policy_diagnostics,
    anchor_price_impact, historical_open_price_validation)
from src.models.n5_research import (student_t_copula_shocks, expected_shortfall,
    summarize_q1, compound_price_stress, explicit_base_supply_withdrawal, debt_rank)


class ResearchTests(unittest.TestCase):
    def test_large_hf_response_is_bounded_but_real_discontinuity_is_not(self):
        f=pd.DataFrame([dict(zero_hf=1e15,tiny_jump_abs_error=1000.,baseline_abs_error=0.,
            baseline_eligibility_disagreement=False,passed=False,zero_hf_valid=True),
            dict(zero_hf=1.,tiny_jump_abs_error=.01,baseline_abs_error=0.,
            baseline_eligibility_disagreement=True,passed=False,zero_hf_valid=True)])
        out=tiny_shock_diagnostics(f)
        self.assertEqual(out.analytic_continuity_passed.tolist(),[True,False])
        self.assertFalse(out.passed.any())  # Strict gate is preserved.

    def test_dust_is_a_proposal_and_does_not_drop_wallets(self):
        f=pd.DataFrame(dict(debt_usd=[1e-8,99.],total_debt_base_usd=[2e-8,100.],
            collateral_usd=[0.,200.],total_collateral_base_usd=[1e-8,200.]))
        out,policies=balance_policy_diagnostics(f)
        self.assertEqual(len(out),2)
        self.assertTrue(out.debt_strict_failed.iloc[0])
        self.assertEqual(policies.loc[policies.proposed_min_debt_usd.eq(100),'excluded_wallets'].item(),1)

    def test_quote_anchor_matches_average_not_terminal_impact(self):
        f=pd.DataFrame([dict(status='verified_2pct_bracket',dex_depth_2pct_usd=100.,
            observed_impact_pct=2.,snapshot_id='A',snapshot_block=10,token_in_address='ETH',pool_address='p',token_out='USD')])
        out=anchor_price_impact(f)
        np.testing.assert_allclose(out.lambda_impact,.04)
        np.testing.assert_allclose(out.predicted_average_impact,.02)

    def test_student_copula_is_seeded_bounded_and_validates_inputs(self):
        a=student_t_copula_shocks(100,[-.5,0],[-.2,0],rho=.6)
        pd.testing.assert_frame_equal(a,student_t_copula_shocks(100,[-.5,0],[-.2,0],rho=.6))
        self.assertTrue(a.eth_shock.between(-.5,0).all())
        with self.assertRaises(ValueError): student_t_copula_shocks(10,[-1,0],[-.2,0],rho=.6)

    def test_es_fractional_tail_not_empty_or_wrong_quantile(self):
        self.assertAlmostEqual(expected_shortfall([0,10,20,100],.625),(100+.5*20)/1.5)
        self.assertAlmostEqual(expected_shortfall([1,2,3],.99),3.)
        with self.assertRaises(ValueError): expected_shortfall([float('nan')])

    def test_equity_matching_uses_common_strata_not_group_size(self):
        rows=[]
        for scenario in ['s1','s2']:
            for wallet,loop,bucket,loss in [('a',True,'small',20),('b',False,'small',10),('c',True,'large',999)]:
                rows.append(dict(scenario_id=scenario,wallet_address=wallet,is_recursive_loop=loop,
                    equity_bucket=bucket,initial_equity_usd=100.,initial_debt_usd=200.,
                    bad_debt_usd=loss,bad_debt_ratio_t0=loss/200,loss_per_100k_equity=loss*1000))
        wallet,strata,matched=summarize_q1(pd.DataFrame(rows))
        self.assertEqual(matched.common_strata.tolist(),[1,1])
        self.assertEqual(matched.loc[matched.is_recursive_loop,'mean_loss_per_100k'].item(),20000.)
        with self.assertRaises(ValueError): summarize_q1(pd.DataFrame(rows[:-1]))

    def comet(self):
        base=dict(snapshot_block=10,market_id='comet',wallet_address='w',price_usd=1.,
            token_address='usd',amount_token=100.,value_usd=100.,liquidate_collateral_factor=0.)
        return pd.DataFrame([dict(base,asset_role='base_supply'),dict(base,asset_role='base_borrow'),
            dict(base,token_address='eth',asset_role='collateral',amount_token=200.,value_usd=200.,liquidate_collateral_factor=.8)])

    def test_comet_supply_is_not_collateral_and_stress_uses_comet_factor(self):
        p=self.comet(); out=compound_price_stress(p,{(10,'eth'):-.5})
        self.assertEqual(out.weighted_collateral_usd.item(),80.)
        self.assertTrue(out.liquidatable.item())
        self.assertEqual(out.debt_usd.item(),100.)

    def test_explicit_withdrawal_does_not_spend_collateral_or_repay_aave(self):
        p=self.comet(); out,ledger=explicit_base_supply_withdrawal(p,{'w'},fraction=.25,scenario_id='fixture')
        self.assertEqual(out.loc[out.asset_role.eq('base_supply'),'amount_token'].item(),75.)
        self.assertEqual(out.loc[out.asset_role.eq('collateral'),'amount_token'].item(),200.)
        self.assertEqual(len(ledger),1)
        self.assertEqual(p.loc[p.asset_role.eq('base_supply'),'amount_token'].item(),100.)
        with self.assertRaises(ValueError): explicit_base_supply_withdrawal(p,{'w'},fraction=2,scenario_id='fixture')

    def test_debtrank_has_direction_and_single_activation(self):
        out=debt_rank([[0,50,0],[0,0,50],[0,0,0]],[100]*3,[1,0,0])
        np.testing.assert_allclose(out['final_distress'],[1,.5,.25])
        reverse=debt_rank([[0,50,0],[0,0,50],[0,0,0]],[100]*3,[0,0,1])
        self.assertEqual(reverse['incremental_distress'],[0,0,0])

    def test_historical_prediction_never_uses_future_candle_or_drops_missing_wallet(self):
        snap=dict(block=10,block_timestamp_utc='2024-01-01T00:00:00Z',window_end_exclusive_utc='2024-01-01T02:00:00Z')
        p=pd.DataFrame([dict(snapshot_block=10,wallet_address='w',symbol='WETH',collateral_amount=1.,
            debt_amount=0.,price_usd=100.,is_collateral_enabled=True,effective_lt=.8),
            dict(snapshot_block=10,wallet_address='w',symbol='USDC',collateral_amount=0.,debt_amount=70.,price_usd=1.,is_collateral_enabled=False,effective_lt=0.)])
        rows=[]
        for asset in ['ETH','BTC']:
            for ts,price in [('2024-01-01T00:00:00Z',100.),('2024-01-01T01:00:00Z',1.)]:
                rows.append(dict(asset=asset,timestamp_utc=ts,open=price,low=0.,close=0.))
        events=pd.DataFrame([dict(event_block=11,protocol_id='aave_v3_ethereum',tx_hash=w,log_index=0,
            wallet_address=w,event_timestamp='2024-01-01T00:30:00Z') for w in ['w','missing']])
        predictions,report=historical_open_price_validation(p,events,pd.DataFrame(rows),snap)
        self.assertEqual(report['first_hour']['observed_wallets'],2)
        self.assertEqual(report['first_hour']['matched_wallets'],0)
        self.assertEqual(report['first_hour']['missing_state_wallets'],1)
        self.assertTrue((predictions.prediction_timestamp<=predictions.event_timestamp).all())
        self.assertGreater(predictions.loc[predictions.wallet_address.eq('w'),'min_hf_asof'].item(),1.)


if __name__=='__main__': unittest.main()
