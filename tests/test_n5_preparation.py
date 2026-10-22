"""N5 integration safeguards; uses fixtures, never production inputs or RPC."""
from copy import deepcopy
import importlib.util
from pathlib import Path
import unittest
from uuid import uuid4

from src.models.cascade import Asset, Wallet, CascadeConfig, health_factor, simulate_cascade
from src.models.n5_inputs import prepare_portfolio
from src.models.n5_validation import check_schema, validate_inputs, validate_model_outputs, assess_historical_readiness
from src.models.n5_experiments import (
    scenario_shocks, run_static_benchmark, compare_static_cascade,
    run_sensitivity_analysis, export_experiment,
)


def tables():
    keys = dict(snapshot_block=100, protocol_id="aave_v3_ethereum")
    pos = [dict(keys, wallet_address="Alice", token_address="eth", collateral_amount=1.,
                debt_amount=0., coll_flag=True, user_emode=0, lt_eff=8000),
           dict(keys, wallet_address="Alice", token_address="usd", collateral_amount=0.,
                debt_amount=85., coll_flag=False, user_emode=0, lt_eff=8000)]
    cfg = [dict(keys, token_address=t, symbol=s, lt_std=.8, liq_bonus_std_premium=.05)
           for t, s in (("eth", "WETH"), ("usd", "USDC"))]
    prices = [dict(keys, token_address=t, price_usd=p) for t, p in (("eth", 100.), ("usd", 1.))]
    return pos, cfg, prices


def prepared(pos=None, **kwargs):
    p, c, x = tables()
    return prepare_portfolio(p if pos is None else pos, c, x, snapshot_block=100,
                             depth_usd={"eth": 1000, "usd": 1000}, **kwargs)


def refs():
    return [dict(snapshot_block=100, wallet_address="alice", call_ok=True,
                 health_factor_reported=80 / 85, total_debt_base_usd=85., total_collateral_base_usd=100.)]


class InputTests(unittest.TestCase):
    def test_adapter_normalizes_without_mutating_or_rescaling(self):
        p, c, x = tables()
        original = deepcopy((p, c, x))
        out = prepare_portfolio(p, c, x, snapshot_block=100, depth_usd={"eth": 1000, "usd": 1000})
        self.assertEqual((p, c, x), original)
        self.assertEqual(out.wallets[0].wallet, "alice")
        self.assertEqual(out.wallets[0].collateral["eth"], 1)
        self.assertAlmostEqual(health_factor(out.wallets[0], out.assets, {"eth": 100, "usd": 1}), 80 / 85)

    def test_no_cross_snapshot_price_fallback(self):
        p, c, x = tables()
        x[0]["snapshot_block"] = 101
        with self.assertRaisesRegex(ValueError, "Missing reserve/price"):
            prepare_portfolio(p, c, x, snapshot_block=100, depth_usd={"eth": 1000, "usd": 1000})

    def test_duplicate_and_protocol_keys_rejected(self):
        p, _, _ = tables()
        with self.assertRaisesRegex(ValueError, "Duplicate"):
            prepared(p + [p[0]])
        p[0]["protocol_id"] = "compound"
        with self.assertRaisesRegex(ValueError, "protocol"):
            prepared(p)

    def test_missing_flags_and_invalid_amounts_fail(self):
        p, _, _ = tables()
        del p[0]["coll_flag"]
        with self.assertRaises(ValueError):
            prepared(p)
        for bad in (None, float("nan"), -1, True):
            p, _, _ = tables()
            p[0]["collateral_amount"] = bad
            with self.assertRaises(ValueError):
                prepared(p)

    def test_false_string_is_not_true(self):
        p, _, _ = tables()
        p[0]["coll_flag"] = "false"
        self.assertFalse(prepared(p).wallets[0].collateral_enabled["eth"])

    def test_emode_membership_required_and_applied(self):
        p, _, _ = tables()
        for row in p:
            row["user_emode"] = 1
        p[0]["lt_eff"] = 9300
        with self.assertRaisesRegex(ValueError, "emode_membership"):
            prepared(p)
        member = [dict(snapshot_block=100, token_address="eth", emode_category=1,
                       is_collateral=True, emode_lt_bps=9300, emode_bonus_premium=.02)]
        out = prepared(p, emode_membership=member)
        self.assertEqual(out.wallets[0].effective_lt["eth"], .93)
        self.assertEqual(out.wallets[0].effective_bonus["eth"], .02)
        self.assertEqual(out.wallets[0].effective_lt["usd"], .8)

    def test_disagreeing_derived_lt_rejected(self):
        p, _, _ = tables()
        p[0]["lt_eff"] = 9000
        with self.assertRaisesRegex(ValueError, "disagrees"):
            prepared(p)

    def test_missing_depth_never_implicitly_calibrated(self):
        p, c, x = tables()
        with self.assertRaises(ValueError):
            prepare_portfolio(p, c, x, snapshot_block=100, depth_usd={})


class ValidationTests(unittest.TestCase):
    def test_partial_evidence_cannot_pass_acceptance(self):
        report = validate_inputs(prepared(), refs())
        self.assertEqual(report["gates"]["hf_reference"]["status"], "passed")
        self.assertEqual(report["gates"]["sample_account_balances"]["status"], "passed")
        self.assertEqual(report["status"], "pending")

    def test_hf_failure_and_missing_accounts(self):
        r = refs()
        r[0]["health_factor_reported"] = 1.5
        self.assertEqual(validate_inputs(prepared(), r)["status"], "failed")
        r[0]["wallet_address"] = "bob"
        report = validate_inputs(prepared(), r)
        self.assertEqual(report["gates"]["hf_reference"]["missing_accounts"], 1)

    def test_same_block_reserve_and_holdout_gates(self):
        totals = dict(snapshot_block=100, debt_usd=85, collateral_usd=100)
        hist = dict(untouched_holdout=True, window_and_coverage_verified=True,
                    observed_wallets=20, matched_wallets=17)
        self.assertEqual(validate_inputs(prepared(), refs(), reserve_totals=totals,
                                        full_population=True, historical_evidence=hist)["status"], "passed")
        totals["snapshot_block"] = 101
        with self.assertRaises(ValueError):
            validate_inputs(prepared(), refs(), reserve_totals=totals, full_population=True)
        hist["untouched_holdout"] = False
        with self.assertRaises(ValueError):
            validate_inputs(prepared(), refs(), historical_evidence=hist)

    def test_schema_types_nulls_keys_and_heatmap_variant(self):
        self.assertTrue(check_schema([], "cascade_summary"))
        row = dict(run_id="r", scenario_id="s", total_simulated_repaid_usd=0.,
                   cascade_amplification=None, total_rounds=1)
        self.assertEqual(check_schema([row], "cascade_summary"), [])
        self.assertTrue(check_schema([row, row], "cascade_summary"))
        row["total_rounds"] = True
        self.assertTrue(check_schema([row], "cascade_summary"))
        self.assertEqual(check_schema([dict(eth_shock=-.2, depeg_shock=-.05, debt_at_risk_usd=0)], "stress_heatmap"), [])

    def test_historical_window_uniqueness_and_sample_coverage(self):
        event = dict(tx_hash="tx", log_index=1, event_block=101, event_timestamp="2026-10-15T00:30:00Z",
                     protocol_id="aave_v3_ethereum", wallet_address="alice", debt_repaid_usd=10., collateral_seized_usd=10.5)
        options = dict(snapshot_block=100, snapshot_timestamp="2026-10-15T00:00:00Z", wallets=["alice"])
        result = assess_historical_readiness([event], **options)
        self.assertEqual(result["status"], "pending")
        self.assertEqual(result["unique_wallets_first_hour"], 1)
        self.assertEqual(result["observed_wallets_in_portfolio"], 1)
        self.assertEqual(assess_historical_readiness([event, event], **options)["status"], "failed")
        event["event_timestamp"] = "2026-10-16T00:00:00Z"
        self.assertEqual(assess_historical_readiness([event], **options)["in_24h_events"], 0)

    def test_ledger_and_price_corruption_are_detected(self):
        p = prepared()
        cfg = CascadeConfig(gas_cost_usd=0, dex_fee_rate=0, cf_rule="fixed_50", lambda_impact=0)
        r = simulate_cascade(p.wallets, p.assets, cfg)
        self.assertEqual(validate_model_outputs(r, p.wallets)["status"], "passed")
        bad = deepcopy(r)
        bad.wallets[0].debt["usd"] += 1
        self.assertEqual(validate_model_outputs(bad, p.wallets)["status"], "failed")
        bad = deepcopy(r)
        bad.rounds[0]["prices_after_json"] = '{"eth": -1}'
        self.assertEqual(validate_model_outputs(bad)["status"], "failed")
        bad = deepcopy(r)
        bad.rounds[0]["cumulative_repaid_usd"] += 1
        self.assertEqual(validate_model_outputs(bad)["status"], "failed")


class ExperimentTests(unittest.TestCase):
    def test_shocks_are_multiplicative_and_targeted(self):
        shock = scenario_shocks({"e": "WETH", "s": "wstETH", "o": "weETH", "u": "USDC"},
                                eth_shock=-.2, depeg_shock=-.05)
        self.assertAlmostEqual(shock["s"], -.24)
        self.assertEqual(shock["e"], -.2)
        self.assertNotIn("u", shock)
        self.assertEqual(shock["o"], -.2)  # ETH exposure, without stETH depeg
        with self.assertRaises(ValueError):
            scenario_shocks({"e": "WETH"}, depeg_shock=-.05)

    def test_benchmark_one_round_fixed_price_not_multi_round_lambda_zero(self):
        p = prepared()
        p.wallets[0].debt["usd"] = 99
        cfg = CascadeConfig(cf_rule="fixed_50", gas_cost_usd=0, dex_fee_rate=0, lambda_impact=.1)
        r = run_static_benchmark(p.wallets, p.assets, cfg)
        multi = simulate_cascade(p.wallets, p.assets, CascadeConfig(cf_rule="fixed_50", gas_cost_usd=0, lambda_impact=0))
        self.assertEqual(len(r.rounds), 1)
        self.assertEqual(r.prices["eth"], 100)
        self.assertGreater(multi.summary["total_simulated_repaid_usd"], r.summary["total_simulated_repaid_usd"])
        self.assertFalse(r.summary["converged"])
        self.assertEqual(cfg.lambda_impact, .1)

    def test_secondary_liquidation_and_benchmark_comparison(self):
        wallets = [Wallet("a", {"e": 1}, {"u": 90}), Wallet("b", {"e": 1}, {"u": 79.9})]
        assets = {"e": Asset(100, .8, .05, 1000), "u": Asset(1)}
        cfg = CascadeConfig(cf_rule="fixed_100", gas_cost_usd=0, dex_fee_rate=0, lambda_impact=.1)
        result = compare_static_cascade(wallets, assets, cfg)
        self.assertEqual(result["static"].summary["sequential_liquidatable_wallets"], 0)
        self.assertEqual(result["cascade"].summary["sequential_liquidatable_wallets"], 1)
        self.assertGreater(result["comparison"]["cascade_to_static_ratio"], 1)

    def test_bounded_sensitivity_and_input_isolation(self):
        p = prepared()
        original = deepcopy(p)
        rows = run_sensitivity_analysis(p.wallets, p.assets, symbols=p.symbols,
                                        depeg_shocks=(0,), depth_multipliers=(.5, 2))
        self.assertEqual(len(rows), 64)
        self.assertEqual(len({r["run_id"] for r in rows}), 64)
        self.assertEqual(p, original)
        self.assertTrue(all(r["validation_status"] == "unvalidated" for r in rows))
        with self.assertRaises(ValueError):
            run_sensitivity_analysis(p.wallets, p.assets, symbols=p.symbols, max_runs=1)
        with self.assertRaises(ValueError):
            run_static_benchmark(p.wallets, p.assets, context={"validation_status": "passed"})

    @unittest.skipUnless(importlib.util.find_spec("pyarrow"), "Optional Parquet dependency")
    def test_export_new_directory_only(self):
        import pyarrow.parquet as pq
        p = prepared()
        rows = run_sensitivity_analysis(p.wallets, p.assets, symbols=p.symbols, depeg_shocks=(0,), eth_shocks=(0,))
        dest = Path(__file__).resolve().parents[1] / "outputs" / "n5_preparation" / f"test_{uuid4().hex}"
        try:
            path = export_experiment(rows, dest)
            self.assertEqual(pq.read_table(path).num_rows, len(rows))
            with self.assertRaises(FileExistsError):
                export_experiment(rows, dest)
        finally:
            if dest.exists():
                for filename in ("sensitivity.parquet", "README.txt"):
                    (dest / filename).unlink(missing_ok=True)
                dest.rmdir()


if __name__ == "__main__":
    unittest.main()
