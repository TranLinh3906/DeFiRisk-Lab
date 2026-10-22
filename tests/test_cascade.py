"""Run from project root: python -m unittest discover -s tests -p test_cascade.py -v."""
from copy import deepcopy
from contextlib import contextmanager
import importlib.util
import json
from pathlib import Path
import unittest
from uuid import uuid4

from src.models.cascade import (
    Asset, Wallet, CascadeConfig, POLICIES, close_factor, health_factor,
    debt_value, price_impact, simulate_cascade, synthetic_portfolio, export_results,
)


def example(debt=85):
    return [Wallet("alice", {"ETH": 1}, {"USD": debt})], {
        "ETH": Asset(100, .8, .05, 10_000), "USD": Asset(1)}


def frictionless(**kwargs):
    return CascadeConfig(gas_cost_usd=0, dex_fee_rate=0, lambda_impact=0, **kwargs)


@contextmanager
def temporary_export_dir():
    """Keep test exports in the workspace; Windows TEMP may be inaccessible."""
    folder = Path(__file__).resolve().parents[1] / "outputs" / "n5_preparation" / f"core_test_{uuid4().hex}"
    folder.mkdir(parents=True, exist_ok=False)
    try:
        yield folder
    finally:
        for name in ("cascade_rounds.parquet", "cascade_summary.parquet"):
            (folder / name).unlink(missing_ok=True)
        folder.rmdir()


class CascadeTests(unittest.TestCase):
    def test_policy_boundaries(self):
        for policy in POLICIES:
            self.assertEqual(close_factor(1, policy), 0)
            self.assertEqual(close_factor(float("inf"), policy), 0)
        self.assertEqual(close_factor(.95, "baseline_aave"), .5)
        self.assertEqual(close_factor(.9499, "baseline_aave"), 1)
        self.assertEqual(close_factor(.8, "fixed_50"), .5)
        self.assertEqual(close_factor(.99, "fixed_100"), 1)
        self.assertEqual(close_factor(.999, "dynamic_linear"), .2)
        self.assertAlmostEqual(close_factor(.95, "dynamic_linear"), .5)
        self.assertAlmostEqual(close_factor(.90, "dynamic_linear"), 1)
        self.assertEqual(close_factor(.1, "dynamic_linear"), 1)
        with self.assertRaises(ValueError):
            close_factor(.9, "typo")

    def test_zero_shock_healthy_and_zero_debt(self):
        wallets, assets = example(50)
        wallets.append(Wallet("bob", {"ETH": 2}, {}))
        original = deepcopy(wallets)
        r = simulate_cascade(wallets, assets, frictionless())
        self.assertEqual(r.wallets, original)
        self.assertEqual(r.transactions, [])
        self.assertEqual(r.summary["total_simulated_repaid_usd"], 0)
        self.assertIsNone(r.summary["cascade_amplification"])
        self.assertEqual(r.prices["ETH"], 100)
        self.assertTrue(r.summary["converged"])
        self.assertEqual(health_factor(wallets[1], assets, r.prices), float("inf"))

    def test_repayment_and_bonus_conservation(self):
        wallets, assets = example()
        original = deepcopy(wallets)
        r = simulate_cascade(wallets, assets, frictionless(cf_rule="fixed_50", max_rounds=1))
        self.assertEqual(wallets, original)
        self.assertAlmostEqual(r.wallets[0].debt["USD"], 42.5)
        self.assertAlmostEqual(r.wallets[0].collateral["ETH"], 1 - 42.5 * 1.05 / 100)
        self.assertAlmostEqual(r.transactions[0]["liquidator_profit_usd"], 42.5 * .05)
        self.assertAlmostEqual(r.summary["initial_debt_usd"],
                               r.summary["remaining_debt_usd"] + r.summary["total_simulated_repaid_usd"])

    def test_insufficient_collateral_caps_actual_debt_repayment(self):
        wallets, assets = example(120)
        r = simulate_cascade(wallets, assets, frictionless(cf_rule="fixed_100"))
        self.assertAlmostEqual(r.summary["total_simulated_repaid_usd"], 100 / 1.05)
        self.assertAlmostEqual(r.wallets[0].collateral["ETH"], 0)
        self.assertAlmostEqual(r.summary["bad_debt_usd"], 120 - 100 / 1.05)
        self.assertEqual(r.summary["stop_reason"], "no_feasible_liquidations")

    def test_disabled_collateral_excluded(self):
        wallets, assets = example()
        wallets[0].collateral_enabled["ETH"] = False
        r = simulate_cascade(wallets, assets, frictionless())
        self.assertEqual(r.transactions, [])
        self.assertEqual(r.summary["bad_debt_usd"], 85)
        self.assertEqual(r.wallets[0].collateral["ETH"], 1)

    def test_effective_emode_threshold_and_bonus(self):
        wallets, assets = example()
        wallets[0].effective_lt["ETH"] = .9
        self.assertGreater(health_factor(wallets[0], assets, {"ETH": 100, "USD": 1}), 1)
        r = simulate_cascade(wallets, assets, frictionless())
        self.assertEqual(r.transactions, [])
        wallets[0].effective_lt["ETH"] = .8
        wallets[0].effective_bonus["ETH"] = .02
        r = simulate_cascade(wallets, assets, frictionless(cf_rule="fixed_100"))
        self.assertAlmostEqual(r.transactions[0]["collateral_seized_usd"], 85 * 1.02)

    def test_gas_and_dex_fee_can_freeze(self):
        wallets, assets = example()
        for c in [CascadeConfig(gas_cost_usd=1000),
                  CascadeConfig(gas_cost_usd=0, dex_fee_rate=.1, lambda_impact=0)]:
            r = simulate_cascade(wallets, assets, c)
            self.assertEqual(r.transactions, [])
            self.assertEqual(r.rounds[0]["liquidation_freeze_debt_usd"], 85)
            self.assertEqual(r.prices["ETH"], 100)
            self.assertEqual(r.summary["bad_debt_usd"], 0)  # frozen != bad debt

    def test_zero_profit_is_rejected(self):
        wallets, assets = example(80)
        assets["ETH"].liquidation_threshold = .7
        assets["ETH"].liquidation_bonus = 0
        self.assertEqual(simulate_cascade(wallets, assets, frictionless()).transactions, [])

    def test_slippage_can_freeze(self):
        wallets, assets = example()
        assets["ETH"].dex_depth_usd = 1
        c = CascadeConfig(gas_cost_usd=0, dex_fee_rate=0, lambda_impact=1)
        r = simulate_cascade(wallets, assets, c)
        self.assertEqual(r.transactions, [])
        self.assertEqual(r.prices["ETH"], 100)

    def test_shared_collateral_creates_secondary_liquidation(self):
        wallets, assets = example(90)
        wallets.append(Wallet("bob", {"ETH": 1}, {"USD": 79.9}))
        assets["ETH"].dex_depth_usd = 1000
        c = CascadeConfig(cf_rule="fixed_100", gas_cost_usd=0, dex_fee_rate=0, lambda_impact=.1)
        r = simulate_cascade(wallets, assets, c)
        self.assertEqual(r.rounds[0]["newly_liquidatable_wallets"], 1)
        self.assertEqual(r.rounds[1]["newly_liquidatable_wallets"], 1)
        self.assertEqual(r.summary["sequential_liquidatable_wallets"], 1)
        self.assertEqual({t["wallet"] for t in r.transactions}, {"alice", "bob"})
        self.assertGreater(r.summary["cascade_amplification"], 1)
        self.assertLess(r.prices["ETH"], 100)

    def test_no_feedback_means_no_secondary_liquidation(self):
        wallets, assets = example(90)
        wallets.append(Wallet("bob", {"ETH": 1}, {"USD": 79.9}))
        r = simulate_cascade(wallets, assets, frictionless(cf_rule="fixed_100"))
        self.assertEqual(r.summary["sequential_liquidatable_wallets"], 0)
        self.assertEqual(r.summary["cascade_amplification"], 1)

    def test_multitoken_pair_prefers_profit_and_caps_total_cf(self):
        wallets, assets = example(60)
        wallets[0].collateral["BTC"] = 1
        wallets[0].debt["OTHER"] = 60
        assets["BTC"] = Asset(100, .2, .1)
        assets["OTHER"] = Asset(1)
        r = simulate_cascade(wallets, assets, frictionless(cf_rule="fixed_50", max_rounds=1))
        self.assertEqual(r.transactions[0]["collateral_token"], "BTC")
        self.assertAlmostEqual(r.transactions[0]["repaid_debt_usd"], 60)
        self.assertEqual(len(r.transactions), 1)

    def test_repeated_rounds_do_not_count_same_debt_twice(self):
        wallets, assets = example(99)
        r = simulate_cascade(wallets, assets, frictionless(cf_rule="fixed_50"))
        self.assertGreater(len(r.transactions), 1)
        self.assertEqual(sum(x["newly_liquidatable_wallets"] for x in r.rounds), 1)
        self.assertLessEqual(r.summary["total_simulated_repaid_usd"], 99)
        self.assertAlmostEqual(99, r.summary["total_simulated_repaid_usd"] + r.wallets[0].debt["USD"])
        self.assertAlmostEqual(1 - r.wallets[0].collateral["ETH"],
                               sum(t["collateral_tokens_seized"] for t in r.transactions))

    def test_zero_sell_fraction_and_impact_cap(self):
        wallets, assets = example()
        c = CascadeConfig(gas_cost_usd=0, sell_fraction=0)
        r = simulate_cascade(wallets, assets, c)
        self.assertEqual(r.prices["ETH"], 100)
        self.assertEqual(json.loads(r.rounds[0]["sold_usd_json"])["ETH"], 0)
        self.assertEqual(price_impact(1e20, assets["ETH"], c), .95)

    def test_price_drop_revalues_debt_token(self):
        wallets = [Wallet("a", {"USD": 90}, {"ETH": 1})]
        assets = {"ETH": Asset(100), "USD": Asset(1)}
        r = simulate_cascade(wallets, assets, frictionless(), shocks={"ETH": -.5})
        self.assertEqual(r.summary["initial_debt_usd"], 50)
        self.assertEqual(r.transactions, [])

    def test_deterministic_demo_and_input_isolation(self):
        wallets, assets = synthetic_portfolio()
        original_assets = deepcopy(assets)
        self.assertEqual(len(wallets), 8)
        for policy in POLICIES:
            a = simulate_cascade(wallets, assets, CascadeConfig(cf_rule=policy), run_id="test",
                                 shocks={"WETH": -.2, "wstETH": -.24})
            b = simulate_cascade(list(reversed(wallets)), assets, CascadeConfig(cf_rule=policy), run_id="test",
                                 shocks={"WETH": -.2, "wstETH": -.24})
            self.assertEqual(a.rounds, b.rounds)
            self.assertEqual(a.summary, b.summary)
            self.assertTrue(all(v >= 0 for w in a.wallets for d in (w.collateral, w.debt) for v in d.values()))
        self.assertEqual(assets, original_assets)

    def test_max_rounds_is_reported_as_truncated(self):
        wallets, assets = example(99)
        r = simulate_cascade(wallets, assets, frictionless(cf_rule="fixed_50", max_rounds=1))
        self.assertFalse(r.summary["converged"])
        self.assertEqual(r.summary["stop_reason"], "max_rounds")

    def test_invalid_inputs(self):
        for config in [CascadeConfig(sell_fraction=2), CascadeConfig(gas_cost_usd=-1),
                       CascadeConfig(gamma_exponent=0), CascadeConfig(lambda_impact=float("nan")),
                       CascadeConfig(max_rounds=0), CascadeConfig(min_repay_usd=0)]:
            wallets, assets = example()
            with self.assertRaises(ValueError):
                simulate_cascade(wallets, assets, config)
        wallets, assets = example()
        with self.assertRaises(ValueError):
            simulate_cascade(wallets * 2, assets)
        with self.assertRaises(ValueError):
            simulate_cascade(wallets, assets, shocks={"ETH": -1})
        wallets[0].debt["USD"] = -1
        with self.assertRaises(ValueError):
            simulate_cascade(wallets, assets)

    @unittest.skipUnless(importlib.util.find_spec("pyarrow"), "Parquet export requires pyarrow")
    def test_parquet_roundtrip_and_overwrite_protection(self):
        import pyarrow as pa
        import pyarrow.parquet as pq
        wallets, assets = example(50)
        results = [simulate_cascade(wallets, assets, frictionless(cf_rule=p)) for p in POLICIES]
        with temporary_export_dir() as folder:
            rounds, summary = export_results(results, folder)
            rt, st = pq.read_table(rounds), pq.read_table(summary)
            self.assertEqual(rt.num_rows, 4)
            self.assertEqual(st.num_rows, 4)
            self.assertEqual(st.schema.field("cascade_amplification").type, pa.float64())
            self.assertEqual(st["cascade_amplification"].to_pylist(), [None] * 4)
            self.assertEqual(rt.schema.metadata[b"data_kind"], b"synthetic")
            self.assertTrue({"run_id", "scenario_id", "round", "newly_liquidatable_wallets",
                             "simulated_repaid_debt_usd", "cumulative_repaid_usd"} <= set(rt.column_names))
            with self.assertRaises(FileExistsError):
                export_results(results, folder)
            export_results(results, folder, overwrite=True)
            with self.assertRaises(ValueError):
                export_results([results[0], results[0]], folder, overwrite=True)

    @unittest.skipUnless(importlib.util.find_spec("pyarrow"), "Parquet export requires pyarrow")
    def test_nonzero_parquet_totals_match_transaction_ledger(self):
        import pyarrow.parquet as pq
        wallets, assets = example(99)
        r = simulate_cascade(wallets, assets, frictionless(cf_rule="fixed_50"))
        with temporary_export_dir() as folder:
            rounds, summary = export_results(r, folder)
            rows = pq.read_table(rounds).to_pylist()
            final = pq.read_table(summary).to_pylist()[0]
            self.assertAlmostEqual(sum(t["repaid_debt_usd"] for t in r.transactions),
                                   final["total_simulated_repaid_usd"])
            self.assertAlmostEqual(sum(t["simulated_repaid_debt_usd"] for t in rows),
                                   final["total_simulated_repaid_usd"])


if __name__ == "__main__":
    unittest.main()
