import importlib.util
import unittest
from decimal import Decimal
from pathlib import Path

path = Path(__file__).resolve().parents[1] / 'src/data/fetch_dex_liquidity.py'
spec = importlib.util.spec_from_file_location('fetch_dex_liquidity_under_test', path)
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

class TestDepth(unittest.TestCase):
    @staticmethod
    def quote(x):
        # Synthetic AMM average execution price with 2% impact at $2m
        # price = 1/(1+x/98,000,000); x / (98m+x) = 2% at 2m.
        return int(Decimal(x) * Decimal(98_000_000) / (Decimal(98_000_000)+Decimal(x)/Decimal(10)**12))

    def test_accurate_crossing(self):
        r = m.binary_depth(self.quote, Decimal(10)**12, Decimal(1), max_usd=500_000_000,
                           iterations=60, tolerance_usd=1)
        self.assertEqual(r['status'], 'verified_2pct_bracket')
        self.assertLessEqual(r['depth_bracket_width_usd'], 1)
        self.assertTrue(1_999_990 < r['depth_usd'] < 2_000_010)
        self.assertLess(r['impact_below_depth_pct'], 2)
        self.assertGreaterEqual(r['impact_at_depth_pct'], 2)

    def test_old_20m_cap_not_enough_and_extended_cap_finds_it(self):
        def deep(x):
            return int(Decimal(x)*Decimal(4_900_000_000)/(Decimal(4_900_000_000)+Decimal(x)/Decimal(10)**12))
        old=m.binary_depth(deep,Decimal(10)**12,Decimal(1),20_000_000)
        self.assertEqual(old['status'],'threshold_not_reached_within_cap')
        self.assertIsNone(old['depth_usd'])
        new=m.binary_depth(deep,Decimal(10)**12,Decimal(1),500_000_000,iterations=55,tolerance_usd=1)
        self.assertEqual(new['status'],'verified_2pct_bracket')
        self.assertTrue(99_999_990 < new['depth_usd'] < 100_000_010)

    def test_never_fake_when_cap_too_low(self):
        def deep(x):return int(Decimal(x)*Decimal(49_000_000_000)/(Decimal(49_000_000_000)+Decimal(x)/Decimal(10)**12))
        r=m.binary_depth(deep,Decimal(10)**12,Decimal(1),500_000_000)
        self.assertEqual(r['status'],'threshold_not_reached_within_cap')
        self.assertIsNone(r['depth_usd'])

    def test_quote_errors_never_fake(self):
        def flaky(x):
            if x > 500:raise RuntimeError('bad quoter')
            return x
        r=m.binary_depth(flaky,Decimal(1),Decimal(1),500000000)
        self.assertEqual(r['status'],'quote_failed_before_threshold')
        self.assertIsNone(r['depth_usd'])

    def test_small_crossing(self):
        def steep(x):return int(Decimal(x)*Decimal(490)/(Decimal(490)+Decimal(x)/Decimal(10)**12))
        r=m.binary_depth(steep,Decimal(10)**12,Decimal(1),1_000,iterations=65,tolerance_usd=.01)
        self.assertEqual(r['status'],'verified_2pct_bracket')
        self.assertLessEqual(r['depth_bracket_width_usd'], .01)

if __name__=='__main__': unittest.main()
