import unittest
import pandas as pd

from src.models.research_tolerances import load_tolerances,filter_simulation_positions,evaluate_research_gate
from src.models.stress_test import run_stress_test


class ToleranceTests(unittest.TestCase):
    def fixture(self):
        p=pd.DataFrame([dict(snapshot_block=1,protocol_id='aave_v3_ethereum',wallet_address=w,token_address='t',symbol='USDC',
            collateral_amount=200.,debt_amount=debt,price_usd=1.,effective_lt=.8,is_collateral_enabled=True)
            for w,debt in [('dust',99.),('keep',100.)]])
        r=pd.DataFrame([dict(snapshot_block=1,wallet_address=w,total_debt_base_usd=debt,
            total_collateral_base_usd=200.,health_factor_reported=160/debt) for w,debt in [('dust',99.),('keep',100.)]])
        return p,r

    def test_boundary_keeps_100_and_protocol_uses_full_unfiltered_debt(self):
        p,r=self.fixture()
        selected,cohort,_,report=evaluate_research_gate(p,r,dict(snapshot_block=1,protocol_debt_usd=199.))
        self.assertEqual(selected.wallet_address.tolist(),['keep'])
        self.assertEqual(report['status'],'passed')
        self.assertEqual(report['gates']['protocol_debt']['calculated_full_t0_debt_usd'],199.)
        out=run_stress_test(p,{},tolerances=load_tolerances(),account_reference=r)
        self.assertEqual(out.wallet.tolist(),['keep'])

    def test_missing_reference_is_not_treated_as_dust(self):
        p,r=self.fixture()
        with self.assertRaises(ValueError):filter_simulation_positions(p,r[r.wallet_address.eq('keep')])

    def test_wallet_error_exactly_100_passes_but_above_100_fails(self):
        p,r=self.fixture()
        p=p[p.wallet_address.eq('keep')]
        r=r[r.wallet_address.eq('keep')].copy()
        r['total_collateral_base_usd']=300.
        _,_,_,report=evaluate_research_gate(p,r,dict(snapshot_block=1,protocol_debt_usd=100.))
        self.assertEqual(report['gates']['wallet_balances']['status'],'passed')
        r['total_collateral_base_usd']=300.01
        _,_,_,report=evaluate_research_gate(p,r,dict(snapshot_block=1,protocol_debt_usd=100.))
        self.assertEqual(report['gates']['wallet_balances']['status'],'failed')

    def test_protocol_error_exactly_one_percent_fails(self):
        p,r=self.fixture();p=p[p.wallet_address.eq('dust')].copy();p['debt_amount']=99.
        _,_,_,report=evaluate_research_gate(p,r,dict(snapshot_block=1,protocol_debt_usd=100.))
        self.assertEqual(report['gates']['protocol_debt']['status'],'failed')

    def test_bad_median_hf_is_not_bypassed_by_wallet_tolerance(self):
        p,r=self.fixture();r.loc[r.wallet_address.eq('keep'),'health_factor_reported']=2.
        _,_,_,report=evaluate_research_gate(p,r,dict(snapshot_block=1,protocol_debt_usd=199.))
        self.assertEqual(report['gates']['wallet_balances']['status'],'passed')
        self.assertEqual(report['gates']['hf_median']['status'],'failed')
