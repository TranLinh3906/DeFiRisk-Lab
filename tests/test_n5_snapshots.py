import copy
import unittest
import pandas as pd
from src.models.n5_snapshots import audit_snapshots, select_event_windows
from src.models.n5_integration import check_reconstructed_hf


def inputs():
    snap = dict(block=100, block_hash='0xabc', block_timestamp_utc='2024-08-05T00:00:00Z',
                window_end_exclusive_utc='2024-08-06T00:00:00Z')
    config = {'snapshots': {'B1': snap}}
    meta = {'snapshots': {'B1': dict(snapshot_block=100, block_hash='0xabc',
                                    snapshot_timestamp_utc='2024-08-05T00:00:00Z')}}
    tables = {'positions': pd.DataFrame([dict(snapshot_id='B1', snapshot_block=100)])}
    return config, meta, tables


class SnapshotTests(unittest.TestCase):
    def test_matching_identity_only_passes_structure(self):
        c, m, t = inputs()
        result = audit_snapshots(c, m, t)
        self.assertEqual(result['status'], 'passed_structure')
        self.assertEqual(result['snapshots']['B1']['research_acceptance'], 'pending')

    def test_missing_new_labels_not_replaced_by_old_b(self):
        c, m, t = inputs()
        t['positions']['snapshot_id'] = 'B'
        m['snapshots'] = {'B': m['snapshots']['B1']}
        r = audit_snapshots(c, m, t)
        self.assertEqual(r['snapshots']['B1']['status'], 'missing_data')
        self.assertEqual(r['stale_labels']['positions'], ['B'])
        self.assertEqual(t['positions'].iloc[0].snapshot_id, 'B')

    def test_wrong_block_hash_and_timezone_detected(self):
        for error in ['block', 'hash', 'timezone']:
            c, m, t = inputs()
            if error == 'block': t['positions']['snapshot_block'] = 101
            if error == 'hash': m['snapshots']['B1']['block_hash'] = '0xwrong'
            if error == 'timezone': c['snapshots']['B1']['block_timestamp_utc'] = '2024-08-05T00:00:00'
            self.assertEqual(audit_snapshots(c, m, t)['snapshots']['B1']['status'], 'failed')

    def test_event_selection_excludes_boundaries_wrong_block_and_protocol(self):
        c, _, _ = inputs()
        row = dict(tx_hash='t', log_index=1, event_block=101, event_timestamp='2024-08-05T00:30:00Z',
                   protocol_id='aave_v3_ethereum', wallet_address='w')
        events = pd.DataFrame([row, dict(row,log_index=2,event_timestamp='2024-08-05T00:00:00Z'),
            dict(row,log_index=3,event_timestamp='2024-08-06T00:00:00Z'),
            dict(row,log_index=4,event_block=100), dict(row,log_index=5,protocol_id='other')])
        selected, report = select_event_windows(events,c)
        self.assertEqual(selected.log_index.tolist(),[1])
        self.assertEqual(report['B1']['first_hour_wallets'],1)
        self.assertIsNone(report['B1']['hit_rate'])
        self.assertNotIn('validation_snapshot_id',events.columns)
        with self.assertRaises(ValueError): select_event_windows(pd.concat([events,events]),c)

    def test_reconstructed_guard_detects_copied_zero_shock_hf(self):
        keys = dict(snapshot_block=100,protocol_id='p',wallet_address='w')
        p = pd.DataFrame([dict(keys,token_address='t',collateral_amount=100.,debt_amount=90.,
                              price_usd=1.,effective_lt=.8,is_collateral_enabled=True)])
        risk = pd.DataFrame([dict(keys,health_factor=1.2)])
        zero = pd.DataFrame([dict(keys,hf_after=1.2)])
        tiny = pd.DataFrame([dict(keys,hf_after=80/90)])
        detail, result = check_reconstructed_hf(p,risk,zero,tiny)
        self.assertEqual(result['status'],'failed')
        self.assertEqual(result['baseline_eligibility_disagreements'],1)
        self.assertEqual(result['tiny_jump_ge_tolerance'],1)
        risk['health_factor']=80/90;zero['hf_after']=80/90
        self.assertEqual(check_reconstructed_hf(p,risk,zero,tiny)[1]['status'],'passed')


if __name__ == '__main__': unittest.main()
