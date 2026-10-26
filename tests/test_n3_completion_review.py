import unittest
import pandas as pd

from src.data.n3_price_timeseries import compute_depeg_metrics
from src.data.n3_gas_profit_freeze import analyze_gas_profit_freeze
from src.data.finalize_liquidations import finalize
from scripts.n3_priority3_price_qa import normalize


class CompletionReviewTests(unittest.TestCase):
    def ratios(self):
        return pd.DataFrame([dict(timestamp='2026-10-12T00:00:00Z',reference_timestamp='2026-10-12T00:00:00Z',
            asset='STETH',quote_asset='ETH',price_value=v,price_type='market_ratio',source='test',
            token_address=None,snapshot_block=None,staleness_seconds=0.,note='fixture') for v in [.99,1.]])

    def test_discount_metric_can_be_negative_and_zero_but_prices_cannot(self):
        ratios=self.ratios()
        metrics=compute_depeg_metrics(ratios)
        self.assertAlmostEqual(metrics.price_value.iloc[0],-1.)
        self.assertEqual(metrics.price_value.iloc[1],0.)
        self.assertTrue(normalize(metrics).qa_valid.all())
        invalid=metrics.copy();invalid['price_type']='market_reference';invalid['quote_asset']='USD'
        self.assertFalse(normalize(invalid).qa_valid.any())

    def test_depeg_never_uses_future_or_oracle_ratio(self):
        ratios=self.ratios()
        ratios.loc[0,'reference_timestamp']='2026-10-13T00:00:00Z'
        with self.assertRaises(ValueError):compute_depeg_metrics(ratios)
        ratios=self.ratios();ratios['price_type']='aave_oracle'
        self.assertTrue(compute_depeg_metrics(ratios).empty)

    def test_executed_event_with_negative_estimated_profit_is_not_proven_frozen(self):
        events=pd.DataFrame([dict(tx_hash='tx',log_index=0,debt_repaid_usd=100.,collateral_seized_usd=105.)])
        out,report=analyze_gas_profit_freeze(events)
        self.assertEqual(out.estimated_gas_cost_usd.item(),60.)
        self.assertTrue(out.is_unprofitable_under_assumptions.item())
        self.assertTrue(out.is_frozen_due_to_gas.isna().all())
        self.assertIsNone(report['frozen_events_count'])
        self.assertEqual(report['analysis_status'],'assumption_diagnostic_not_freeze_proof')

    def test_missing_gas_inputs_and_invalid_assumptions_are_rejected(self):
        with self.assertRaises(ValueError):analyze_gas_profit_freeze(pd.DataFrame([dict(debt_repaid_usd=1.)]))
        events=pd.DataFrame([dict(debt_repaid_usd=100.,collateral_seized_usd=105.)])
        with self.assertRaises(ValueError):analyze_gas_profit_freeze(events,default_gas_price_gwei=float('nan'))
        events['collateral_seized_usd']=float('nan')
        with self.assertRaises(ValueError):analyze_gas_profit_freeze(events)

    def event(self):
        return dict(tx_hash='0x'+'1'*64,log_index=0,event_block=100,event_timestamp='2024-08-05T00:10:00Z',
            protocol_id='aave_v3_ethereum',wallet_address='0x'+'a'*40,liquidator_address='0x'+'b'*40,
            collateral_token_address='0x'+'c'*40,debt_token_address='0x'+'d'*40,
            debt_repaid_usd=100.,collateral_seized_usd=105.,debt_raw_amount='100000000',collateral_raw_amount='105000000',
            debt_decimals=6,collateral_decimals=6,debt_price_usd=1.,collateral_price_usd=1.,
            debt_price_timestamp='2024-08-05T00:09:00Z',collateral_price_timestamp='2024-08-05T00:09:00Z')

    def test_finalizer_runs_but_pricing_coverage_does_not_certify_collection(self):
        all_events,fact,report=finalize(pd.DataFrame([self.event()]))
        self.assertEqual(len(fact),1)
        self.assertEqual(report['valuation_coverage_pct'],100.)
        self.assertEqual(report['event_collection_coverage'],'NOT_VERIFIED_INDEPENDENTLY')
        self.assertEqual(report['valuation_coverage_status'],'PASS')

    def test_finalizer_keeps_unpriced_events_in_denominator_and_rejects_duplicates(self):
        event=self.event();event['debt_price_usd']=float('nan')
        _,fact,report=finalize(pd.DataFrame([event]))
        self.assertEqual(len(fact),0)
        self.assertEqual(report['total_events_in_input'],1)
        self.assertEqual(report['valuation_coverage_pct'],0.)
        with self.assertRaises(ValueError):finalize(pd.DataFrame([self.event(),self.event()]))
