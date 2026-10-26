"""Run: python -m unittest discover -s tests -v (from repository root)."""
import importlib.util
import json
from contextlib import contextmanager
from uuid import uuid4
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]


@contextmanager
def workspace_temp():
    # Ordinary mkdir inherits workspace ACLs; Windows sandbox rejects 0700 temp dirs.
    directory = ROOT / 'outputs' / ('n3_test_' + uuid4().hex)
    directory.mkdir()
    try:
        yield str(directory)
    finally:
        for child in directory.iterdir():
            child.unlink()
        directory.rmdir()


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, ROOT / path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


ana = load("n3_muc5", "src/analysis/descriptive.py")
dex = load("n3_muc4", "src/data/fetch_dex_liquidity.py")
WETH = dex.TOKENS["WETH"].lower()
STETH = dex.TOKENS["STETH"].lower()
WSTETH = dex.TOKENS["WSTETH"].lower()


def example():
    hour = pd.date_range("2026-10-12T00:00:00Z", periods=96, freq="h")
    ev = pd.DataFrame({
        "tx_hash": ["0x" + str(x)*64 for x in ("1", "2", "3")],
        "log_index": [0, 1, 2],
        "event_block": [100, 101, 102],
        "event_timestamp": pd.to_datetime(["2026-10-12T01:15:00Z", "2026-10-12T01:55:00Z", "2026-10-14T04:03:00Z"]),
        "protocol_id": ["aave_v3_ethereum"]*3,
        "wallet_address": ["0x" + str(x)*40 for x in ("a", "b", "c")],
        "liquidator_address": ["0x" + "d"*40]*3,
        "collateral_token_address": [WSTETH, WETH, WSTETH],
        "debt_token_address": [WETH, WETH, WETH],
        "debt_repaid_usd": [100, 400, 500],
        "collateral_seized_usd": [107, 428, 535],
    })
    prices = []
    for n, when in enumerate(hour):
        prices.append({"timestamp":when, "asset":"ETH", "quote_asset":"USD", "price_value":3000 + n,
                       "price_type":"market_reference", "reference_timestamp":when})
        prices.append({"timestamp":when, "asset":"STETH", "quote_asset":"ETH", "price_value":0.999 - n*1e-6,
                       "price_type":"market_ratio", "reference_timestamp":when})
    registry = pd.DataFrame({"token_address":[WSTETH,WETH], "symbol":["wstETH","WETH"], "decimals":[18,18]})
    return ev, pd.DataFrame(prices), registry


class DescriptiveTest(unittest.TestCase):
    def test_accept_11_contract_fields_and_zero_hours(self):
        events, prices, reg = example()
        output = ana.build(events, prices, reg, start="2026-10-12", end="2026-10-16")
        self.assertEqual(len(events.columns), 11)
        self.assertEqual(output["quality"]["event_rows"], 3)
        self.assertEqual(len(output["hourly"]), 96)
        self.assertEqual(int(output["hourly"]["liquidation_events"].sum()), 3)
        self.assertEqual(float(output["daily"]["debt_repaid_usd"].sum()), 1000)
        self.assertEqual(output["quality"]["event_steth_eth_ratio_matched"], 3)
        self.assertEqual(int((output["aligned_events"]["eth_usd_quote_age_sec"] < 0).sum()), 0)
        self.assertEqual(len(output["collateral"]), 2)

    def test_prevent_duplicate_events(self):
        ev, pr, reg = example()
        ev.loc[1, "tx_hash"], ev.loc[1, "log_index"] = ev.loc[0, "tx_hash"], ev.loc[0,"log_index"]
        with self.assertRaisesRegex(ValueError, "Trung khoa"):
            ana.build(ev, pr, reg)

    def test_prevent_future_market_price(self):
        ev, pr, reg = example()
        pr.loc[0, "reference_timestamp"] = pr.loc[0, "timestamp"] + pd.Timedelta(hours=1)
        with self.assertRaisesRegex(ValueError, "tuong lai"):
            ana.build(ev, pr, reg)

    def test_missing_market_ratio_keeps_null(self):
        ev, pr, reg = example()
        pr = pr[pr.price_type.eq("market_reference")]
        out = ana.build(ev, pr, reg)
        self.assertEqual(out["quality"]["event_steth_eth_ratio_matched"], 0)
        self.assertTrue(out["timeline"]["steth_discount_pct"].isna().all())

    def test_generated_figures(self):
        ev, pr, reg = example()
        out = ana.build(ev, pr, reg)
        with workspace_temp() as d:
            with patch.object(ana, "OUT_FIGS", Path(d)):
                ana.plot_results(out, overwrite=False)
            pngs = list(Path(d).glob("*.png"))
            self.assertEqual(len(pngs), 4)
            self.assertTrue(all(p.stat().st_size > 1000 for p in pngs))


class DexTest(unittest.TestCase):
    def test_binary_depth_known_curve(self):
        # Simple constant-product style average execution out = in / (1 + in / 10000)
        # gives 2% impact at x = 204.08 USD when 1 raw = 1 USD.
        q = lambda x: int(x*1_000_000 / (1 + x/10000))
        val = dex.binary_depth(q, dex.Decimal(1), dex.Decimal(1_000_000), 1_000_000, iterations=20)
        self.assertEqual(val["status"], "verified_2pct_bracket")
        self.assertLess(abs(val["depth_usd"]-205), 4)

    def test_no_fake_depth_when_not_reached(self):
        q = lambda x: x
        val = dex.binary_depth(q, dex.Decimal(1), dex.Decimal(1), 1000)
        self.assertEqual(val["status"], "threshold_not_reached_within_cap")
        self.assertIsNone(val["depth_usd"])

    def test_no_depth_on_quote_error(self):
        def q(x):
            raise RuntimeError("RPC historical state unavailable")
        val = dex.binary_depth(q, dex.Decimal(1), dex.Decimal(1), 1000)
        self.assertIsNone(val["depth_usd"])
        self.assertEqual(val["status"], "quote_failed_at_small_trade")

    def test_N2_steth_proxy_same_block(self):
        with workspace_temp() as d:
            reg_path = Path(d)/"tokens.csv"
            reg_path.write_text("token_address,symbol,decimals\n" + f"{WSTETH},wstETH,18\n", encoding="utf-8")
            oracle = pd.DataFrame({"snapshot_block":[10], "token_address":[WSTETH], "price_usd":[3000.]})
            lido = pd.DataFrame({"snapshot_block":[10], "steth_per_wsteth":[1.2]})
            (Path(d)/"lido.parquet").touch()
            with patch.object(dex, "REGISTRY", reg_path), patch.object(dex,"LIDO",Path(d)/"lido.parquet"), patch.object(dex,"ORACLE",Path(d)/"oracle.parquet"), patch.object(dex.pd, "read_parquet",side_effect=[oracle,lido]):
                prices = dex.token_metadata()
                self.assertAlmostEqual(prices[(10,STETH)]["usd"], 2500)
                self.assertEqual(prices[(10,STETH)]["decimals"],18)

    def test_snapshot_validation(self):
        with self.assertRaises(ValueError):
            dex.snapshots({"chain_id":42161,"snapshots":{}})


if __name__ == "__main__":
    unittest.main()
