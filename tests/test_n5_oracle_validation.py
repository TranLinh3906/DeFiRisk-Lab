import unittest
import numpy as np
import pandas as pd

from src.models.n5_oracle_validation import audit_oracle_path,validate_sampled_holdout


class OracleValidationTests(unittest.TestCase):
    def fixture(self):
        positions=pd.DataFrame([
            dict(snapshot_block=100,wallet_address='w',token_address='eth',symbol='WETH',collateral_amount=1.,debt_amount=0.,is_collateral_enabled=True,effective_lt=.8,price_usd=100.),
            dict(snapshot_block=100,wallet_address='w',token_address='usd',symbol='USDC',collateral_amount=0.,debt_amount=70.,is_collateral_enabled=False,effective_lt=0.,price_usd=1.)])
        path=pd.DataFrame([dict(snapshot_id='B1',block=b,block_timestamp_utc=pd.Timestamp('2024-01-01T00:00:00Z')+pd.Timedelta(seconds=(b-100)*12),
            token_address=token,symbol=symbol,price_usd=price,oracle_price_raw=str(round(price*1e8)))
            for b,eth in [(100,100.),(110,50.),(120,40.)] for token,symbol,price in [('eth','WETH',eth),('usd','USDC',1.)]])
        events=pd.DataFrame([dict(validation_snapshot_id='B1',wallet_address=w,tx_hash=w,log_index=0,event_block=110,
            event_timestamp=pd.Timestamp('2024-01-01T00:02:00Z')) for w in ['w','missing']])
        snap=dict(label='B1',block=100,block_timestamp_utc='2024-01-01T00:00:00Z',window_end_exclusive_utc='2024-01-01T01:00:00Z')
        return positions,path,events,snap

    def test_same_block_or_future_low_cannot_make_pre_event_hit(self):
        p,q,e,snap=self.fixture()
        wallets,details,r=validate_sampled_holdout(p,q,e,snap,dict(start_block=101,end_block=115))
        row=wallets[(wallets.window=='primary_peak_300')&(wallets.wallet_address=='w')].iloc[0]
        self.assertFalse(row.hit)
        self.assertTrue(row.retrospective_coherent_trough_hit)
        available=details[details.last_sample_block.notna()]
        self.assertTrue(available.last_sample_block.lt(available.event_block).all())
        self.assertFalse(details.loc[details.last_sample_block.isna(),'hit'].any())
        rate=r['windows']['primary_peak_300']['pre_event_sampled_oracle']
        self.assertEqual(rate['observed_wallets'],2)
        self.assertEqual(rate['missing_state_wallets'],1)
        self.assertIsNone(rate['debt_weighted_hit_rate'])
        self.assertEqual(rate['wallet_hit_rate_upper_bound'],.5)

    def test_missing_token_path_cannot_be_used_as_a_hit(self):
        p,q,e,snap=self.fixture()
        q=q[q.token_address.ne('usd')]
        e=e[e.wallet_address.eq('w')].assign(event_block=119)
        _,_,r=validate_sampled_holdout(p,q,e,snap,dict(start_block=101,end_block=120))
        rate=r['windows']['primary_peak_300']['pre_event_sampled_oracle']
        self.assertEqual(rate['hit_wallets'],0)
        self.assertEqual(rate['incomplete_token_price_wallets'],1)
        self.assertEqual(rate['unscorable_wallets'],1)
        self.assertEqual(rate['wallet_hit_rate_upper_bound'],1.)

    def test_event_at_window_start_uses_carried_prior_quote_without_lookahead(self):
        p,q,e,snap=self.fixture()
        wallets,details,_=validate_sampled_holdout(p,q,e,snap,dict(start_block=110,end_block=120))
        row=wallets[(wallets.window=='primary_peak_300')&wallets.wallet_address.eq('w')].iloc[0]
        self.assertAlmostEqual(row.min_hf_before_event,80/70)
        self.assertFalse(row.hit)
        event=details[(details.window=='primary_peak_300')&details.wallet_address.eq('w')].iloc[0]
        self.assertEqual(event.last_sample_block,100)

    def test_path_audit_rejects_duplicate_and_bad_snapshot_price(self):
        p,q,_,snap=self.fixture()
        config={'snapshots':{'B1':snap}}
        summary={'B1':dict(block_start=100,block_end=120,step_blocks=10,samples=3,tokens={
            'WETH':dict(min_block=120,min_price_in_window=40.),'USDC':dict(min_block=100,min_price_in_window=1.)})}
        refs=p[['snapshot_block','token_address','price_usd']]
        _,report=audit_oracle_path(q,summary,config,refs)
        self.assertEqual(report['B1']['samples'],3)
        with self.assertRaises(ValueError):audit_oracle_path(pd.concat([q,q.iloc[[0]]]),summary,config,refs)
        refs=refs.copy();refs.loc[refs.token_address.eq('eth'),'price_usd']=101.
        with self.assertRaises(ValueError):audit_oracle_path(q,summary,config,refs)
