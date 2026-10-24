"""Regression tests for N4 contracts reported during the N5 audit."""
import unittest
import pandas as pd

from src.models.stress_test import run_stress_test
from src.models.cliff_reverse import _parse_target, find_liquidation_cliff
from src.models.health_factor import calculate_health_factor


def fixture():
    return pd.DataFrame([dict(snapshot_block=100, protocol_id='aave_v3_ethereum',
        wallet_address='w', token_address='t', symbol='WETH', collateral_amount=100.,
        debt_amount=90., price_usd=1., effective_lt=.8, is_collateral_enabled=True)])


class PendingN4Contracts(unittest.TestCase):
    def test_zero_shock_checks_input_arithmetic_instead_of_copying_hf(self):
        rows = fixture().assign(health_factor=1.2)
        try:
            result = run_stress_test(rows, {})
        except ValueError:
            return  # Explicit rejection is a valid strict contract.
        reconstructed = 100. * .8 / 90.
        self.assertAlmostEqual(result.iloc[0].hf_after, reconstructed)
        self.assertAlmostEqual(result.iloc[0].hf_before, reconstructed)

    def test_false_string_never_enables_collateral(self):
        result = run_stress_test(fixture().assign(is_collateral_enabled='false'), {})
        self.assertEqual(result.iloc[0].hf_after, 0.)

    def test_missing_price_rejected(self):
        with self.assertRaises(ValueError):
            run_stress_test(fixture().drop(columns='price_usd'), {})

    def test_negative_dictionary_target_rejected(self):
        with self.assertRaises(ValueError):
            _parse_target({'usd': -1}, 100)

    def test_cliff_multiple_snapshots_rejected_or_separated(self):
        data = pd.concat([fixture(), fixture().assign(snapshot_block=101)], ignore_index=True)
        try:
            out = find_liquidation_cliff(data, max_drop=.01, step=.01)
        except ValueError:
            return
        self.assertIn('snapshot_block', out.columns)
        self.assertEqual(set(out.snapshot_block), {100, 101})

    def test_consistent_baseline_is_continuous_for_tiny_shock(self):
        data = fixture().assign(health_factor=100*.8/90)
        zero = run_stress_test(data, {})
        tiny = run_stress_test(data, {'eth_shock': -1e-12})
        self.assertAlmostEqual(zero.iloc[0].hf_after, tiny.iloc[0].hf_after, places=9)

    def test_inconsistent_baseline_rejected_or_tiny_shock_continuous(self):
        data = fixture().assign(health_factor=1.2)
        try:
            zero = run_stress_test(data, {})
            tiny = run_stress_test(data, {'eth_shock': -1e-12})
        except ValueError:
            return
        self.assertAlmostEqual(zero.iloc[0].hf_after, tiny.iloc[0].hf_after, places=6)

    def test_matching_category_without_collateral_permission_uses_standard_lt(self):
        data = fixture().assign(emode_category_user=2, debt_amount=50.)
        cfg = pd.DataFrame([dict(snapshot_block=100, token_address='t', lt_std=.5,
             emode_category=2, lt_emode=.9, is_collateral=False)])
        px = pd.DataFrame([dict(snapshot_block=100, token_address='t', price_usd=1.)])
        result = calculate_health_factor(data, cfg, px)
        self.assertAlmostEqual(result.iloc[0].health_factor, 1.)

    def test_nan_dictionary_target_rejected(self):
        with self.assertRaises(ValueError):
            _parse_target({'usd': float('nan')}, 100)

    def test_nan_percent_target_rejected(self):
        with self.assertRaises(ValueError):
            _parse_target('NaN%', 100)

    def test_infinite_dict_percent_target_rejected(self):
        with self.assertRaises(ValueError):
            _parse_target({'pct': float('inf')}, 100)


if __name__ == '__main__':
    unittest.main()
