"""Reconcile completed Compound exports, preserving coverage when assignment schemes differ.

No RPC. A successful per-shard QA does not imply global coverage. This tool reconstructs
the declared query scope and labels incomplete unions explicitly.
"""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import sqlite3
import sys

import pandas as pd

REPO = Path(__file__).resolve().parents[1]
PACKAGE = REPO / "N3_Compound_Q3_Overlap_VSCode"
sys.path.insert(0, str(PACKAGE / "scripts"))
import merge_n3_compound as merger


def reconcile(inputs, output):
    if output.exists():
        raise FileExistsError(f"Use a new output directory: {output}")
    plan = json.loads((PACKAGE / "config/n3_compound_team_assignment.json").read_text(encoding="utf-8"))
    snapshots = merger.fetch.load_snapshots(REPO / "data/processed/aave/snapshot_metadata.json")
    selected, selection = merger.fetch.pick_wallets(
        pd.read_parquet(REPO / "data/processed/aave/account_reference.parquet"), snapshots, 0)
    markets = merger.fetch.load_markets(PACKAGE / "config/n3_compound_markets.csv")
    selection_hash = hashlib.sha256(json.dumps(selected, sort_keys=True).encode()).hexdigest()
    frames = {label: [] for label in merger.TABLES}
    covered = {}
    sources = []
    identities = set()
    key_columns = {
        "positions": ["snapshot_block", "comet_address", "wallet_address", "token_address", "asset_role"],
        "accounts": ["snapshot_block", "comet_address", "wallet_address"],
        "config": ["snapshot_block", "comet_address", "token_address"],
        "overlap": ["snapshot_block", "wallet_address"],
    }
    for path in inputs:
        merger.check(path)
        quality_path = path / "n3_compound_quality.json"
        quality = json.loads(quality_path.read_text(encoding="utf-8"))
        shard = quality["shard"]
        index, count = shard["shard_index"], shard["num_shards"]
        assert count == plan["num_shards"] and type(index) is int and 0 <= index < count
        assert index not in identities, "Duplicate source shard index"
        identities.add(index)
        assert quality["chain_id"] == 1
        assert quality["snapshot_blocks"] == {sid: int(info["snapshot_block"]) for sid, info in snapshots.items()}
        assert quality["markets"] == markets
        assert quality["metadata_fingerprint"] == plan["metadata_fingerprint"]
        assert shard["selection_hash"] == selection_hash
        assert shard["global_selection"] == selection
        if shard["assignment"] == "sorted_wallet_round_robin_v1":
            part = merger.fetch.partition_wallets(selected, count, index)
        else:
            part, digest = merger.fetch.assignment_partition(plan, selected, count, index)
            assert shard["assignment"] == "checkpoint_assignment_v1:" + digest
        pairs = {(sid, market["market_id"], wallet)
                 for sid, wallets in part.items() for wallet in wallets for market in markets}
        assert len(pairs) == quality["successful_queries"]
        assert shard["global_expected_queries"] == sum(map(len, selected.values())) * len(markets)
        assert quality["selection"]["selected_unique_wallets"] == len({w for ws in part.values() for w in ws})
        valid_rows = {(int(snapshots[sid]["snapshot_block"]), market["comet_address"], wallet)
                      for sid, ws in part.items() for wallet in ws for market in markets}
        valid_overlap = {(block, wallet) for block, comet, wallet in valid_rows}
        for label, name in merger.TABLES.items():
            frame = pd.read_parquet(path / name)
            if label in ("positions", "accounts"):
                assert set(frame[["snapshot_block", "comet_address", "wallet_address"]].itertuples(index=False, name=None)) <= valid_rows
            elif label == "overlap":
                assert set(frame[["snapshot_block", "wallet_address"]].itertuples(index=False, name=None)) <= valid_overlap
            frames[label].append(frame)
        for pair in pairs:
            covered.setdefault(pair, []).append(index)
        sources.append({"shard_index": index, "directory": str(path.resolve()),
                        "assignment": shard["assignment"], "successful_queries": len(pairs),
                        "quality_sha256": hashlib.sha256(quality_path.read_bytes()).hexdigest()})
    assert identities == set(range(plan["num_shards"])), "Need all six input exports"
    duplicate_counts = {}
    for label, parts in frames.items():
        combined = pd.concat(parts, ignore_index=True)
        unique = combined.drop_duplicates().reset_index(drop=True)
        if unique.duplicated(key_columns[label]).any():
            raise ValueError("Conflicting rows for the same key: " + label)
        duplicate_counts[label] = len(combined) - len(unique)
        frames[label] = unique
    expected = {(sid, market["market_id"], wallet)
                for sid, wallets in selected.items() for wallet in wallets for market in markets}
    assert set(covered) <= expected
    missing = expected - set(covered)
    counts = Counter((sid, mid) for sid, mid, wallet in missing)
    report = {
        "complete": not missing, "validation_status": "incomplete_coverage" if missing else "coverage_complete",
        "scope": "Aave-targeted wallets; not a census of all Compound users",
        "expected_queries": len(expected), "covered_queries": len(covered), "missing_queries": len(missing),
        "missing_unique_wallets": len({wallet for sid, mid, wallet in missing}),
        "target_unique_wallets": selection["selected_unique_wallets"],
        "metadata_fingerprint": plan["metadata_fingerprint"],
        "snapshot_blocks": {sid: int(info["snapshot_block"]) for sid, info in snapshots.items()},
        "missing_by_snapshot_market": [{"snapshot_id": sid, "market_id": mid, "queries": n}
                                       for (sid, mid), n in sorted(counts.items())],
        "table_rows": {label: len(frame) for label, frame in frames.items()},
        "exact_duplicate_rows_removed": duplicate_counts, "sources": sources,
        "coverage_evidence": "Reconstructed from completed source QA and the declared deterministic assignment; no raw RPC checkpoints supplied for imported shards.",
    }
    coverage = pd.DataFrame([
        {"snapshot_id": sid, "snapshot_block": int(snapshots[sid]["snapshot_block"]),
         "market_id": mid, "wallet_address": wallet,
         "assigned_shard": plan["wallet_shards"][wallet], "covered": (sid, mid, wallet) in covered,
         "source_shards": ",".join(map(str, covered.get((sid, mid, wallet), [])))}
        for sid, mid, wallet in sorted(expected)])
    output.mkdir(parents=True)
    suffix = "_partial" if missing else ""
    database = output / ("compound_consolidated" + suffix + ".sqlite")
    with sqlite3.connect(database) as connection:
        for label, frame in frames.items():
            frame.to_sql(label, connection, index=False)
            columns = ",".join(key_columns[label])
            connection.execute(f"CREATE UNIQUE INDEX pk_{label} ON {label} ({columns})")
        coverage.to_sql("query_coverage", connection, index=False)
        connection.execute("CREATE UNIQUE INDEX pk_coverage ON query_coverage(snapshot_id,market_id,wallet_address)")
        pd.DataFrame([{"key": key, "value_json": json.dumps(value, ensure_ascii=False)}
                      for key, value in report.items()]).to_sql("merge_report", connection, index=False)
        connection.commit()
        assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        for label, frame in frames.items():
            assert connection.execute(f"SELECT COUNT(*) FROM {label}").fetchone()[0] == len(frame)
    for label, frame in frames.items():
        filename = merger.TABLES[label].replace(".parquet", suffix + ".parquet")
        frame.to_parquet(output / filename, index=False)
        pd.testing.assert_frame_equal(frame, pd.read_parquet(output / filename))
    coverage.loc[~coverage.covered].to_csv(output / "missing_queries.csv", index=False, encoding="utf-8-sig")
    (output / "merge_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    print("[DATABASE]", database)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inputs", nargs="+", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    reconcile(args.inputs, args.output)
