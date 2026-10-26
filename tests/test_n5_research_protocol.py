import unittest
import numpy as np
import pandas as pd

from src.models.n5_research_protocol import (calibration_returns,calibrated_scenarios,
    peak_event_window,dual_hit_rates,validate_protocol_decisions)


class ProtocolTests(unittest.TestCase):
    def test_confirmed_protocol_rejects_snapshot_drift_and_primary_rule_change(self):
        import json
        from pathlib import Path
        protocol=json.loads(Path('config/n5_research_protocol.json').read_text(encoding='utf-8'))
        snapshots={'snapshots':{'B1':{'block':20458637}}}
        self.assertEqual(validate_protocol_decisions(protocol,snapshots)['peak_window_blocks'],300)
        with self.assertRaises(ValueError):
            validate_protocol_decisions(protocol,{'snapshots':{'B1':{'block':20458692}}})
        protocol['holdout']['primary_peak_selection']='first_oracle_eth_decline'
        with self.assertRaises(ValueError):validate_protocol_decisions(protocol,snapshots)

    def reference(self):
        dates=pd.date_range('2025-10-12','2026-10-12',tz='UTC')
        return pd.DataFrame(dict(date=dates,eth_usd_implied=np.linspace(100,200,len(dates)),
            steth_eth_ratio_daily_ref=np.linspace(.97,1,len(dates)),
            usdt_quote_utc=dates+pd.Timedelta(hours=23),steth_quote_utc=dates+pd.Timedelta(hours=23)))

    def test_exact_365_returns_and_no_future_quote(self):
        ref=self.reference()
        out=calibration_returns(ref,'2026-10-12T23:59:59Z')
        self.assertEqual(len(out),365)
        ref.loc[0,'steth_quote_utc']=pd.Timestamp('2025-10-13T01:00:00Z')
        with self.assertRaises(ValueError):calibration_returns(ref,'2026-10-12T23:59:59Z')

    def test_missing_day_rejected(self):
        with self.assertRaises(ValueError):calibration_returns(self.reference().iloc[1:],'2026-10-12T23:59:59Z')

    def test_10000_seed42_scenarios_reproducible(self):
        returns=calibration_returns(self.reference(),'2026-10-12T23:59:59Z')
        a,meta=calibrated_scenarios(returns)
        b,_=calibrated_scenarios(returns)
        pd.testing.assert_frame_equal(a,b)
        self.assertEqual(len(a),10000)
        self.assertEqual(meta['seed'],42)

    def test_peak_exact_300_blocks_not_301(self):
        events=pd.DataFrame(dict(event_block=[1,299,300,301,302],tx_hash=list('abcde'),log_index=0))
        out=peak_event_window(events)
        self.assertEqual((out['start_block'],out['end_block'],out['event_count']),(299,598,4))

    def test_dual_rates_and_freeze_do_not_hide_misses(self):
        evidence=pd.DataFrame(dict(wallet_address=['a','b'],initial_debt_usd=[100.,900.],
            hit=[True,False],freeze_confirmed=[False,True]))
        out=dual_hit_rates(evidence)
        self.assertEqual(out['wallet_hit_rate'],.5)
        self.assertEqual(out['debt_weighted_hit_rate'],.1)
        self.assertEqual(out['freeze_wallets'],1)
        evidence.loc[0,'initial_debt_usd']=np.nan
        with self.assertRaises(ValueError):dual_hit_rates(evidence)
