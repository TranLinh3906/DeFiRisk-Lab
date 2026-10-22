"""Offline unit tests; no Ethereum RPC or real provider credentials needed."""
import sys
import unittest
from unittest.mock import Mock, patch
from pathlib import Path
import importlib.util
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("n3_compound_overlap", ROOT / "src/data/fetch_compound_overlap.py")
mod = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = mod
spec.loader.exec_module(mod)


class TestN3Compound(unittest.TestCase):
    def setUp(self):
        self.a = "0x" + "a" * 40
        self.b = "0x" + "b" * 40
        self.base = "0x" + "c" * 40
        self.token = "0x" + "d" * 40
        self.comet = "0x" + "e" * 40
        self.snaps = {"A":{"snapshot_block":26179841},"B":{"snapshot_block":26194188}}

    def test_pick_eligible_borrowers_two_snapshots(self):
        df = pd.DataFrame([
            {"snapshot_id":"A","wallet_address":self.a,"total_debt_usd":10},
            {"snapshot_id":"B","wallet_address":self.a,"total_debt_usd":0},
            {"snapshot_id":"A","wallet_address":self.b,"total_debt_usd":50},
            {"snapshot_id":"B","wallet_address":self.b,"total_debt_usd":20},
        ])
        got,q = mod.pick_wallets(df,self.snaps,limit=1)
        self.assertEqual(q["selected_unique_wallets"],1)
        self.assertEqual(set(got["A"]),{self.b})
        self.assertEqual(set(got["B"]),{self.b})

    def test_partition_wallets_disjoint_complete_and_same_across_snapshots(self):
        wallets = ["0x" + f"{i:040x}" for i in range(11)]
        selected = {"A": dict.fromkeys(wallets, 10), "B": dict.fromkeys(wallets[2:], 20)}
        shards = [mod.partition_wallets(selected, 4, i) for i in range(4)]
        seen = set()
        for shard in shards:
            self.assertFalse(seen & set(shard["A"]))
            seen.update(shard["A"])
            self.assertTrue(set(shard["B"]) <= set(shard["A"]))
        self.assertEqual(seen, set(wallets))
        for sid in selected:
            combined = {w: d for shard in shards for w, d in shard[sid].items()}
            self.assertEqual(combined, selected[sid])
        reordered = {sid: dict(reversed(list(pairs.items()))) for sid, pairs in selected.items()}
        self.assertEqual(mod.partition_wallets(selected, 4, 0), mod.partition_wallets(reordered, 4, 0))
        self.assertEqual(mod.partition_wallets(selected, 1, 0), selected)
        with self.assertRaises(ValueError):
            mod.partition_wallets(selected, 4, 4)

    def test_actual_n2_account_reference_debt_column(self):
        df = pd.DataFrame([
            {"snapshot_id":"A", "wallet_address":self.a, "total_debt_base_usd":2200.0},
            {"snapshot_id":"B", "wallet_address":self.a, "total_debt_base_usd":2100.0},
        ])
        selected, qa = mod.pick_wallets(df, self.snaps, limit=0)
        self.assertEqual(qa["aave_debt_column"], "total_debt_base_usd")
        self.assertIn(self.a, selected["A"])
        self.assertIn(self.a, selected["B"])

    def test_empty_after_no_debt_rejected(self):
        df = pd.DataFrame([{"snapshot_id":"A","wallet_address":self.a,"debt_usd":0}])
        with self.assertRaises(ValueError):
            mod.pick_wallets(df,self.snaps,limit=1)

    def test_bad_address_rejected(self):
        with self.assertRaises(ValueError):mod.valid_address("not_an_address")

    def test_position_sums_and_overlap(self):
        m = {"snapshot_id":"A","snapshot_block":26179841,"market_id":"usdc", "comet_address":self.comet,
             "base_token_address":self.base,"base_token_symbol":"USDC","base_decimals":6,"base_price_usd":1.0,
             "assets":[{"token_address":self.token,"token_symbol":"WETH","scale":10**18,"price_usd":3000.0,
                         "borrow_collateral_factor":0.8,"liquidate_collateral_factor":0.85,
                         "liquidation_factor":0.9,"asset_offset":0,"supply_cap_raw":"100","price_feed":self.base}]}
        rec={"snapshot_id":"A","market_id":"usdc","wallet":self.a,"status":"ok", "aave_debt_usd":1000.0,
             "values":{"borrow_raw":5000000,"supply_raw":0,"is_liquidatable":False,
                       "collaterals":{self.token:2*10**18}}}
        pos,acc,config,overlap=mod.build_outputs([rec],{("A","usdc"):m})
        self.assertEqual(len(pos),2)
        self.assertEqual(len(config),2)
        self.assertEqual(len(acc),1)
        self.assertAlmostEqual(float(acc.iloc[0].compound_borrow_usd),5.0)
        self.assertAlmostEqual(float(acc.iloc[0].compound_collateral_usd),6000.0)
        self.assertAlmostEqual(float(overlap.iloc[0].aave_debt_usd),1000.0)

    def test_metadata_fingerprint_supports_tuple_keys_and_stable_order(self):
        metas = {("B", "weth"): {"price": 3000, "assets": []},
                 ("A", "usdc"): {"assets": [], "price": 1}}
        fingerprint = mod.metadata_fingerprint(metas)
        reordered = {("A", "usdc"): {"price": 1, "assets": []},
                     ("B", "weth"): {"assets": [], "price": 3000}}
        self.assertEqual(len(fingerprint), 64)
        self.assertEqual(fingerprint, mod.metadata_fingerprint(reordered))
        reordered[("B", "weth")]["price"] = 3100
        self.assertNotEqual(fingerprint, mod.metadata_fingerprint(reordered))
        self.assertNotEqual(fingerprint, mod.metadata_fingerprint({
            ("A", "weth"): metas[("B", "weth")],
            ("B", "usdc"): metas[("A", "usdc")]}))

    def test_paced_session_spaces_requests_and_web3_metadata(self):
        response = mod.requests.Response()
        response.status_code = 200
        response._content = b'{"jsonrpc":"2.0","id":0,"result":"0x1"}'
        with mod.PacedSession(0.2) as session:
            with patch.object(mod.requests.Session, "send", return_value=response) as send, \
                    patch.object(mod.time, "monotonic", return_value=100.0), \
                    patch.object(mod.time, "sleep") as sleep:
                w3 = mod.Web3(mod.Web3.HTTPProvider("https://example.invalid", session=session))
                self.assertEqual(w3.eth.chain_id, 1)
                session.post("https://example.invalid", json=[])
                self.assertEqual(send.call_count, 2)
                sleep.assert_called_once_with(0.2)

    def test_paced_session_does_not_wait_when_request_already_took_long_enough(self):
        with mod.PacedSession(0.2) as session:
            with patch.object(mod.requests.Session, "send"), \
                    patch.object(mod.time, "monotonic", side_effect=[100.0, 101.0, 101.0]), \
                    patch.object(mod.time, "sleep") as sleep:
                session.post("https://example.invalid", json=[])
                session.post("https://example.invalid", json=[])
                sleep.assert_not_called()
        for interval in (-1, float("inf"), float("nan")):
            with self.assertRaises(ValueError):
                mod.PacedSession(interval)

    def test_rpc_batch_retries_per_call_errors_and_preserves_successes(self):
        session = Mock()
        first = Mock()
        first.json.return_value = [
            {"id": 1, "result": "0x01"},
            {"id": 2, "error": {"code": 429, "message": "rate limited"}}]
        second = Mock()
        second.json.return_value = [{"id": 2, "result": "0x02"}]
        session.post.side_effect = [first, second]
        calls = [{"id": 1}, {"id": 2}]
        with patch.object(mod.time, "sleep"):
            replies = mod.rpc_batch(session, "https://example.invalid", calls, 40)
        self.assertEqual(replies[1]["result"], "0x01")
        self.assertEqual(replies[2]["result"], "0x02")
        self.assertEqual(session.post.call_args_list[1].kwargs["json"], [{"id": 2}])

    def test_rpc_batch_keeps_persistent_errors_visible(self):
        session = Mock()
        session.post.return_value.json.return_value = [
            {"id": 1, "error": {"code": -32000, "message": "execution reverted"}}]
        with patch.object(mod.time, "sleep"):
            replies = mod.rpc_batch(session, "https://example.invalid", [{"id": 1}], 10, retries=2)
        self.assertIn("error", replies[1])
        self.assertEqual(session.post.call_count, 2)

    def test_no_cross_protocol_activity(self):
        m={"snapshot_id":"A","snapshot_block":26179841,"market_id":"usdc","comet_address":self.comet,
           "base_token_address":self.base,"base_token_symbol":"USDC","base_decimals":6,"base_price_usd":1.0,"assets":[]}
        rec={"snapshot_id":"A","market_id":"usdc","wallet":self.a,"status":"ok","values":{
             "borrow_raw":0,"supply_raw":0,"is_liquidatable":False,"collaterals":{}}}
        pos,acc,_,ov=mod.build_outputs([rec],{("A","usdc"):m})
        self.assertTrue(pos.empty and acc.empty and ov.empty)


if __name__ == "__main__":unittest.main()
