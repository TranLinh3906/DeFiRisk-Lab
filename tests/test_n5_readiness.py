"""Exposure conservation, missing evidence, partial coverage and package integrity."""
import json
from pathlib import Path
import unittest
from contextlib import contextmanager
from uuid import uuid4

import pandas as pd

from src.models.cascade import Asset, Wallet
from src.models.n5_inputs import PreparedPortfolio
from src.models.n5_readiness import (
    build_aave_graph, historical_candidates, map_dex_depth, reconcile_debt,
    verify_manifest, write_manifest, inspect_compound, graph_node_metrics,
)


def portfolio():
    return PreparedPortfolio(
        [Wallet('w', {'t': 10}, {'t': 3}, {'t': True}),
         Wallet('disabled', {'t': 100}, {'t': 1}, {'t': False})],
        {'t': Asset(2, .8, .05, 100)},
        [dict(wallet_address='w', token_address='t', collateral_amount=10,
              debt_amount=3, price_usd=2, effective_lt=.8, is_collateral_enabled=True),
         dict(wallet_address='disabled', token_address='t', collateral_amount=100,
              debt_amount=1, price_usd=2, effective_lt=.8, is_collateral_enabled=False)],
        100, 'aave_v3_ethereum', {'t': 'TEST'}, [])


@contextmanager
def workspace_fixture():
    root = Path('outputs/n5_preparation') / f'readiness_test_{uuid4().hex}'
    root.mkdir(parents=True)
    try:
        yield root
    finally:
        # Only flat files created by this fixture; no recursive deletion.
        for p in root.iterdir():
            p.unlink()
        root.rmdir()


class ReadinessTests(unittest.TestCase):
    def test_graph_directions_conservation_disabled_collateral_and_stable_ids(self):
        a = build_aave_graph(portfolio())
        pd.testing.assert_frame_equal(a, build_aave_graph(portfolio()))
        self.assertEqual(a.loc[a.edge_type.eq('collateral_exposure'), 'weight_usd_t0'].sum(), 20)
        self.assertEqual(a.loc[a.edge_type.eq('debt_exposure'), 'weight_usd_t0'].sum(), 8)
        self.assertEqual(set(a.source_layer) | set(a.target_layer), {'wallet', 'token', 'protocol'})
        self.assertTrue(a.loc[a.edge_type.eq('debt_exposure'), 'source_node_id'].eq('token:t').all())
        self.assertEqual(len(a[a.edge_type.eq('wallet_protocol')]), 2)
        metrics = graph_node_metrics(a).set_index('node_id')
        self.assertEqual(metrics.loc['wallet:w', 'out_strength_usd'], 20)
        self.assertEqual(metrics.loc['token:t', 'out_strength_usd'], 8)
        self.assertFalse(metrics.index.str.startswith('protocol:').any())

    def test_debt_evidence_same_block_and_independent_sum(self):
        row = dict(snapshot_block=100, token_address='t', price_usd=2,
                   protocol_debt_units=4, protocol_debt_usd=8, wallet_debt_usd=9999)
        rows, report = reconcile_debt(portfolio(), [row])
        self.assertEqual(report['status'], 'passed')
        self.assertEqual(rows[0]['calculated_debt_usd'], 8)
        self.assertEqual(report['reserve_collateral_gate'], 'pending')
        with self.assertRaises(ValueError):
            reconcile_debt(portfolio(), [dict(row, snapshot_block=101)])
        with self.assertRaises(ValueError):
            reconcile_debt(portfolio(), [row, row])
        with self.assertRaises(ValueError):
            reconcile_debt(portfolio(), [dict(row, price_usd=3, protocol_debt_usd=12)])

    def test_zero_reference_positive_actual_does_not_pass(self):
        _, report = reconcile_debt(portfolio(), [dict(snapshot_block=100, token_address='t',
              price_usd=2, protocol_debt_units=0, protocol_debt_usd=0)])
        self.assertEqual(report['status'], 'failed')
        self.assertIsNone(report['relative_error'])

    def test_depth_no_other_block_fallback_and_ambiguity(self):
        row = dict(snapshot_block=101, token_in_address='t', status='verified_2pct_bracket',
                   dex_depth_2pct_usd=123, pool_address='pool', token_out='OUT')
        a = map_dex_depth(portfolio(), pd.DataFrame([row]))
        self.assertTrue(pd.isna(a.iloc[0].dex_depth_2pct_usd))
        row['snapshot_block'] = 100
        a = map_dex_depth(portfolio(), pd.DataFrame([row]))
        self.assertEqual(a.iloc[0].dex_depth_2pct_usd, 123)
        a = map_dex_depth(portfolio(), pd.DataFrame([row, row]))
        self.assertEqual(a.iloc[0].status, 'ambiguous_direction')

    def test_historical_missing_quote_rejected_not_zero_filled(self):
        row = dict(tx_hash='tx', log_index=1, protocol_id='aave_v3_ethereum',
                   event_timestamp=pd.Timestamp('2024-01-01', tz='UTC'),
                   debt_repaid_usd=1, collateral_seized_usd=2, has_valid_usd=True)
        f = pd.DataFrame([row, dict(row, log_index=2, collateral_seized_usd=None)])
        accepted, rejected = historical_candidates(f, ['tx_hash', 'log_index'])
        self.assertEqual(accepted.log_index.tolist(), [1])
        self.assertEqual(rejected.log_index.tolist(), [2])
        with self.assertRaises(ValueError):
            historical_candidates(pd.DataFrame([row, row]), ['tx_hash'])

    def test_manifest_exact_hash_tampering_and_path_escape(self):
        # Workspace-local temp folder avoids restricted Windows system TEMP.
        with workspace_fixture() as tmp:
            root = Path(tmp)
            source = root / 'source.txt'; source.write_text('input', encoding='utf-8')
            artifact = root / 'result.txt'; artifact.write_text('output', encoding='utf-8')
            m = write_manifest(root, [source], {'result.txt': 'synthetic'}, {})
            self.assertEqual(m['validation_status'], 'unvalidated')
            self.assertEqual(verify_manifest(root), [])
            artifact.write_text('modified', encoding='utf-8')
            self.assertEqual(verify_manifest(root), ['result.txt'])
            with self.assertRaises(ValueError):
                write_manifest(root, [], {'../escape.txt': 'synthetic'}, {})

    def test_compound_partial_qa_does_not_imply_complete_coverage(self):
        with workspace_fixture() as tmp:
            root = Path(tmp)
            report = dict(complete=False, expected_queries=10, covered_queries=8,
                          missing_queries=2, missing_unique_wallets=1)
            (root / 'merge_report.json').write_text(json.dumps(report), encoding='utf-8')
            keys = dict(snapshot_block=100, market_id='comet', wallet_address='w')
            pd.DataFrame([dict(keys, token_address='t', asset_role='base_borrow',
                               amount_token=4., price_usd=2., value_usd=8.)]).to_parquet(
                                   root / 'n3_compound_positions_long_partial.parquet')
            pd.DataFrame([dict(keys, compound_borrow_usd=8., compound_collateral_usd=0.,
                               compound_base_supply_usd=0.)]).to_parquet(
                                   root / 'n3_compound_account_reference_partial.parquet')
            for name in ['reserve_config', 'overlap_wallets']:
                pd.DataFrame([keys]).to_parquet(root / f'n3_compound_{name}_partial.parquet')
            qa, _ = inspect_compound(root)
            self.assertEqual(qa['table_qa_status'], 'passed')
            self.assertFalse(qa['complete'])
            self.assertEqual(qa['validation_status'], 'unvalidated')


if __name__ == '__main__':
    unittest.main()
