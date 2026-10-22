"""Offline integration tests for complete and incompatible team exports."""
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import merge_n3_compound as merger
import prepare_n3_compound_team as prepare


class TestTeamMerge(unittest.TestCase):
    def test_assignment_preserves_cached_wallets_and_covers_every_wallet(self):
        wallets = ["0x" + f"{i:040x}" for i in range(20)]
        selected = {"A": dict.fromkeys(wallets, 10), "B": dict.fromkeys(wallets[1:], 20)}
        with tempfile.TemporaryDirectory(dir=ROOT / "tests") as temp:
            checkpoint = Path(temp) / "checkpoint.jsonl"
            records = [{"wallet": w, "status": "ok", "fingerprint": "same"} for w in wallets[-3:]]
            checkpoint.write_text("\n".join(json.dumps(r) for r in records), encoding="utf-8")
            plan = prepare.make_plan(selected, checkpoint, 6, 5)
            owners = plan["wallet_shards"]
            self.assertEqual(set(owners), set(wallets))
            self.assertEqual(sum(owner == 0 for owner in owners.values()), 5)
            for wallet in wallets[-3:]:
                self.assertEqual(owners[wallet], 0)
            counts = [sum(owner == i for owner in owners.values()) for i in range(1, 6)]
            self.assertEqual(counts, [3] * 5)
            combined = {sid: {} for sid in selected}
            hashes = set()
            for i in range(6):
                part, digest = merger.fetch.assignment_partition(plan, selected, 6, i)
                hashes.add(digest)
                for sid, pairs in part.items():
                    self.assertFalse(set(combined[sid]) & set(pairs))
                    combined[sid].update(pairs)
            self.assertEqual(combined, selected)
            self.assertEqual(len(hashes), 1)
            # Never discard prior work to enforce a smaller requested leader size.
            smaller = prepare.make_plan(selected, checkpoint, 6, 1)
            self.assertEqual(sum(v == 0 for v in smaller["wallet_shards"].values()), 3)
            with self.assertRaises(ValueError):
                merger.fetch.assignment_partition(plan, selected, 5, 0)
            with self.assertRaises(ValueError):
                merger.fetch.assignment_partition(plan, {"A": selected["A"]}, 6, 0)
            plan["wallet_shards"].pop(wallets[0])
            with self.assertRaises(ValueError):
                merger.fetch.assignment_partition(plan, selected, 6, 0)

    def test_complete_merge_and_reject_missing_duplicate_or_mismatched_shards(self):
        with tempfile.TemporaryDirectory(dir=ROOT / "tests") as temp:
            base = Path(temp)
            mid = {"snapshot_id": "A", "snapshot_block": 123, "market_id": "usdc",
                   "comet_address": "0x" + "c" * 40, "base_token_address": "0x" + "d" * 40,
                   "base_token_symbol": "USDC", "base_decimals": 6, "base_price_usd": 1.0, "assets": []}
            metas = {("A", "usdc"): mid}
            paths = []
            for index in range(2):
                path = base / f"shard{index}"
                path.mkdir()
                record = {"snapshot_id": "A", "market_id": "usdc", "wallet": "0x" + str(index + 1) * 40,
                          "status": "ok", "aave_debt_usd": 100,
                          "values": {"borrow_raw": 1000000, "supply_raw": 0,
                                     "is_liquidatable": False, "collaterals": {}}}
                tables = merger.fetch.build_outputs([record], metas)
                for frame, filename in zip(tables, merger.TABLES.values()):
                    frame.to_parquet(path / filename, index=False)
                report = {"complete": True, "successful_queries": 1, "failed_queries": 0,
                          "expected_wallet_market_snapshot_queries": 1, "positions_rows": 1,
                          "active_compound_market_accounts": 1, "unique_overlap_wallet_snapshot_pairs": 1,
                          "selection": {"selected_unique_wallets": 1},
                          "chain_id": 1, "snapshot_blocks": {"A": 123}, "markets": ["usdc"],
                          "metadata_fingerprint": merger.fetch.metadata_fingerprint(metas),
                          "shard": {"num_shards": 2, "shard_index": index, "selection_hash": "same",
                                    "global_selection": {"selected_unique_wallets": 2},
                                    "global_expected_queries": 2, "assignment": "sorted_wallet_round_robin_v1"}}
                (path / "n3_compound_quality.json").write_text(json.dumps(report), encoding="utf-8")
                paths.append(path)
            output = base / "merged"
            merger.merge(paths, output)
            merger.check(output)
            quality = json.loads((output / "n3_compound_quality.json").read_text())
            self.assertEqual(quality["successful_queries"], 2)
            self.assertEqual(quality["positions_rows"], 2)
            with self.assertRaises(ValueError):
                merger.merge(paths[:1], base / "missing")
            with self.assertRaises(ValueError):
                merger.merge([paths[0], paths[0]], base / "duplicate")
            quality_path = paths[1] / "n3_compound_quality.json"
            quality = json.loads(quality_path.read_text())
            quality["metadata_fingerprint"] = "different"
            quality_path.write_text(json.dumps(quality), encoding="utf-8")
            with self.assertRaises(ValueError):
                merger.merge(paths, base / "mismatched")


if __name__ == "__main__":
    unittest.main()
