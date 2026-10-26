import unittest
from pathlib import Path
from contextlib import contextmanager
from uuid import uuid4

import pandas as pd

from src.models.n5_inputs import resolve_account_reference
from src.models.n5_n3_update import calibrate_quote_grid,audit_market_coverage,interpolate_average_impact


@contextmanager
def reference_fixture():
    p=Path('outputs/n5_preparation')/f'reference_test_{uuid4().hex}'
    p.mkdir(parents=True)
    try:yield p
    finally:
        for name in ['account_reference.parquet','account_referenceAB1B2.parquet']:
            f=p/name
            if f.is_file():f.unlink()
        p.rmdir()


class N3UpdateTests(unittest.TestCase):
    def quote_fixture(self):
        keys=dict(snapshot_id='A',snapshot_block=10,pool_address='pool',token_in_address='eth',token_out_address='usd')
        rows=[]
        for size in [1000.,2000.,3000.,4000.,5000.]:
            amount=int(size*1e8);impact=.02*size/10000
            rows.append(dict(keys,status='RPC_VERIFIED',trade_size_usd=size,input_token_usd_oracle=1.,
                spot_output_raw_per_input_raw=1.,snapshot_block_hash='abc',verified_block_hash='0xabc',
                amount_in_raw=str(amount),amount_out_raw=str(round(amount*(1-impact))),input_decimals=8,
                impact_pct=100*impact))
        quotes=pd.DataFrame(rows)
        anchors=pd.DataFrame([dict(keys,trade_size_usd=9900.,impact_pct=1.98),dict(keys,trade_size_usd=10000.,impact_pct=2.)])
        dex=pd.DataFrame([dict(keys,dex_depth_2pct_usd=10000.)])
        return quotes,anchors,dex,{'snapshots':{'A':{'block':10,'block_hash':'0xabc'}}}

    def test_raw_quotes_fit_linear_curve_and_preserve_observations(self):
        q,a,d,c=self.quote_fixture()
        fits,residuals,report=calibrate_quote_grid(q,a,d,c)
        self.assertAlmostEqual(fits.gamma_exponent.item(),1.)
        self.assertAlmostEqual(fits.average_impact_alpha.item(),.02)
        self.assertAlmostEqual(fits.engine_terminal_lambda_if_trapezoid_from_zero.item(),.04)
        self.assertEqual(len(residuals),7)
        self.assertEqual(report['verified_quotes'],5)

    def test_tampered_raw_amount_and_wrong_block_are_rejected(self):
        q,a,d,c=self.quote_fixture();q.loc[0,'amount_out_raw']='1'
        with self.assertRaises(ValueError):calibrate_quote_grid(q,a,d,c)
        q,a,d,c=self.quote_fixture();q.loc[0,'verified_block_hash']='0xbad'
        with self.assertRaises(ValueError):calibrate_quote_grid(q,a,d,c)

    def test_interpolation_preserves_points_labels_derivation_and_refuses_extrapolation(self):
        p=pd.DataFrame(dict(trade_size_usd=[1000.,2000.],impact_pct=[.1,.3]))
        self.assertEqual(interpolate_average_impact(p,1000.)['method'],'observed_point')
        middle=interpolate_average_impact(p,1500.)
        self.assertAlmostEqual(middle['impact_pct'],.2)
        self.assertEqual(middle['method'],'derived_linear_interpolation_not_rpc_verified')
        self.assertEqual(middle['bracket_width_usd'],1000.)
        with self.assertRaises(ValueError):interpolate_average_impact(p,3000.)

    def test_reference_fallback_requires_exact_label_and_block(self):
        with reference_fixture() as p:
            pd.DataFrame(dict(snapshot_id=['A','B'],snapshot_block=[1,2])).to_parquet(p/'account_reference.parquet',index=False)
            pd.DataFrame(dict(snapshot_id=['A','B1','B2'],snapshot_block=[1,3,4])).to_parquet(p/'account_referenceAB1B2.parquet',index=False)
            selected=resolve_account_reference(p,{'A':1,'B1':3,'B2':4})
            self.assertEqual(selected.name,'account_referenceAB1B2.parquet')
            with self.assertRaises(ValueError):resolve_account_reference(p,{'A':1,'B1':99,'B2':4})

    def test_market_quote_coverage_rejects_future_quote_and_is_not_hit_rate(self):
        events=pd.DataFrame([dict(protocol_id='aave_v3_ethereum',wallet_address='w',tx_hash=str(i),log_index=0,
            event_block=11+i,event_timestamp=ts,debt_token_address='usd',collateral_token_address='eth')
            for i,ts in enumerate(['2024-01-01T00:15:00Z','2024-01-01T00:20:00Z'])])
        quotes=pd.DataFrame([dict(token_address=token,price_type='market_reference',quote_asset='USD',price_value=price,
            timestamp=ts,reference_timestamp=ts) for token,price,ts in [('eth',100.,'2024-01-01T00:00:00Z'),('usd',1.,'2024-01-01T00:16:00Z')]])
        config={'snapshots':{'B1':dict(block=10,block_timestamp_utc='2024-01-01T00:00:00Z',window_end_exclusive_utc='2024-01-01T01:00:00Z')}}
        evidence,report=audit_market_coverage(events,quotes,config)
        self.assertEqual(evidence.both_token_market_quotes.tolist(),[False,True])
        self.assertEqual(report['windows']['B1']['full_configured_window']['two_token_quote_coverage'],.5)
        self.assertFalse(report['independent_full_wallet_price_path'])
