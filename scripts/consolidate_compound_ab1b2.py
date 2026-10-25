"""Audit and consolidate mixed legacy/historical Compound shards without RPC.

Coverage is reconstructed from deterministic assignments and completed source QA,
including zero-balance queries. Missing queries are never treated as zero balances.
Unreadable tracked files can be read from the local Git object database; originals
are never changed. Run from any directory with --output pointing to a new folder.
"""
import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import io
import json
from pathlib import Path
import subprocess
from uuid import uuid4

import pandas as pd
from qa_n3_compound import _check

ROOT = Path(__file__).resolve().parents[1]
TABLES = {
    "positions": "n3_compound_positions_long.parquet",
    "accounts": "n3_compound_account_reference.parquet",
    "config": "n3_compound_reserve_config.parquet",
    "overlap": "n3_compound_overlap_wallets.parquet",
}
KEYS = {
    "positions": ["snapshot_block", "comet_address", "wallet_address", "token_address", "asset_role"],
    "accounts": ["snapshot_block", "comet_address", "wallet_address"],
    "config": ["snapshot_block", "comet_address", "token_address"],
    "overlap": ["snapshot_block", "wallet_address"],
}
SOURCES = [
    "N3_Compound_Q3_Overlap_VSCode/data/processed/compound/team_6105d9df30ba/shard_000_of_006",
    "shard_001_of_006/shard_001_of_006",
    "shard_002_of_006/shard_002_of_006",
    "shard_003_of_006/shard_003_of_006",
    "shard_004_of_006/shard_004_of_006",
    "shard_005_of_006",
    "processed/processed/compound/team_6105d9df30ba/shard_004_of_006_A_ONLY",
    *[f"outputs/compound_historical_B1_B2_20261024/shard_{i:03d}_of_006" for i in (0, 2, 4)],
]


def require(condition, message):
    if not condition:
        raise ValueError(message)


def digest(value, compact=False):
    options = {"separators": (",", ":")} if compact else {}
    return hashlib.sha256(json.dumps(value, sort_keys=True, **options).encode()).hexdigest()


def git_bytes(ref, path):
    return subprocess.check_output(["git", "show", f"{ref}:{path}"], cwd=ROOT)


def selection(frame):
    frame = frame.loc[frame.total_debt_base_usd.gt(0)].copy()
    frame.wallet_address = frame.wallet_address.str.lower()
    grouped = frame.groupby(["snapshot_id", "wallet_address"]).total_debt_base_usd.max()
    return {sid: grouped.loc[sid].to_dict() for sid in sorted(frame.snapshot_id.unique())}


@contextmanager
def validation_directory():
    # Ordinary mkdir inherits workspace ACLs on Windows (mkdtemp uses mode 0700).
    path = ROOT / "outputs" / ("compound_validation_" + uuid4().hex)
    path.mkdir()
    try:
        yield path
    finally:
        for child in path.iterdir():
            child.unlink()
        path.rmdir()


def consolidate(output, legacy_ref):
    require(not output.exists(), f"Use a new output folder: {output}")
    head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    plan = json.loads((ROOT / "config/n3_compound_team_assignment.json").read_text(encoding="utf-8"))
    old_raw = git_bytes(legacy_ref, "data/processed/aave/account_reference.parquet")
    old = selection(pd.read_parquet(io.BytesIO(old_raw)))
    require(digest(old) == plan["selection_hash"], "Legacy Aave selection does not match team plan")
    current_path = ROOT / "data/processed/aave/account_reference.parquet"
    current = selection(pd.read_parquet(current_path))
    historical = {sid: current[sid] for sid in ("B1", "B2")}
    meta = json.loads((ROOT / "data/processed/aave/snapshot_metadata.json").read_text(encoding="utf-8"))
    blocks = {sid: int(meta["snapshots"][sid]["snapshot_block"]) for sid in current}
    markets = pd.read_csv(ROOT / "config/n3_compound_markets.csv").to_dict("records")
    markets = [{"market_id": m["market_id"], "comet_address": m["comet_address"].lower()} for m in markets]
    mids = [m["market_id"] for m in markets]
    comets = {m["market_id"]: m["comet_address"] for m in markets}
    owners = plan["wallet_shards"]
    count = plan["num_shards"]
    old_round_robin = {w: i % count for i, w in enumerate(sorted(owners))}

    def owner(wallet):
        return owners.get(wallet, int.from_bytes(hashlib.sha256(wallet.encode()).digest()[:8], "big") % count)

    expected = {(sid, mid, w) for sid, ws in current.items() for mid in mids for w in ws}
    covered = {}
    parts = {label: [] for label in TABLES}
    source_reports = []
    for source in SOURCES:
        provenance = {}

        def read(name):
            path = ROOT / source / name
            try:
                raw = path.read_bytes()
                origin = "working_tree"
            except OSError:
                raw = git_bytes(head, f"{source}/{name}")
                origin = f"git:{head}"
            provenance[name] = {"read_from": origin, "sha256": hashlib.sha256(raw).hexdigest()}
            return raw

        q = json.loads(read("n3_compound_quality.json"))
        require(q.get("complete") is True and q.get("failed_queries") == 0, f"Incomplete source: {source}")
        source_markets = [{"market_id": m["market_id"], "comet_address": m["comet_address"]} for m in q["markets"]]
        require(q.get("chain_id") == 1 and source_markets == markets, f"Wrong chain/markets: {source}")
        shard = q["shard"]
        index = shard["shard_index"]
        require(shard["num_shards"] == count and index in range(count), "Invalid shard identity")
        assignment = shard["assignment"]
        if assignment == "shared_existing_owner_plus_sha256_new_wallets_v1":
            require(shard["selection_hash"] == digest(historical, True), "Historical selection hash mismatch")
            require(shard["assignment_hash"] == digest(plan, True), "Assignment hash mismatch")
        elif assignment == "legacy_owner_plus_sha256_new_wallets_v1":
            composite = hashlib.sha256((plan["selection_hash"] + digest(historical)).encode()).hexdigest()
            require(shard["selection_hash"] == composite, "Extended selection hash mismatch")
        else:
            require(shard["selection_hash"] == digest(old), "Legacy selection hash mismatch")
            require(assignment in ("sorted_wallet_round_robin_v1", "checkpoint_assignment_v1:" + digest(plan)), "Unknown assignment")
        query_sets = {}
        for sid, block in q["snapshot_blocks"].items():
            require(block == ({**blocks, "B": 26194188})[sid], "Snapshot block mismatch")
            population = old[sid] if sid in ("A", "B") else historical[sid]
            own = old_round_robin if assignment == "sorted_wallet_round_robin_v1" else None
            query_sets[sid] = {w for w in population if (own[w] if own is not None else owner(w)) == index}
        source_queries = {(sid, mid, w) for sid, ws in query_sets.items() for mid in mids for w in ws}
        require(len(source_queries) == q["successful_queries"] == q["expected_wallet_market_snapshot_queries"], f"Reconstructed query count mismatch: {source}")
        for key in source_queries & expected:
            covered.setdefault(key, []).append(source)
        loaded = {}
        for label, filename in TABLES.items():
            frame = pd.read_parquet(io.BytesIO(read(filename)))
            require(not frame.duplicated(KEYS[label]).any(), f"Duplicate source keys: {source}/{filename}")
            require(frame[KEYS[label]].notna().all().all(), f"Null source keys: {source}/{filename}")
            require(frame.snapshot_id.map(q["snapshot_blocks"]).eq(frame.snapshot_block).all(), "Invalid snapshot/block mapping")
            if label != "overlap":
                require(frame.market_id.map(comets).eq(frame.comet_address).all(), "Invalid market/comet mapping")
            if label in ("positions", "accounts"):
                observed = set(frame[["snapshot_id", "market_id", "wallet_address"]].itertuples(index=False, name=None))
                require(observed <= source_queries, f"Rows outside assignment: {source}/{filename}")
            if label == "overlap":
                valid = {(sid, w) for sid, ws in query_sets.items() for w in ws}
                require(set(frame[["snapshot_id", "wallet_address"]].itertuples(index=False, name=None)) <= valid, "Overlap outside assignment")
            loaded[label] = frame
            parts[label].append(frame.loc[frame.snapshot_id.isin(blocks)].copy())
        # Existing QA checks price conversion, position/account sums and overlap counts.
        with validation_directory() as temp:
            for label, frame in loaded.items():
                frame.to_parquet(temp / TABLES[label], index=False)
            (temp / "quality.json").write_text(json.dumps(q), encoding="utf-8")
            _check({**{label: temp / name for label, name in TABLES.items()}, "quality": temp / "quality.json"})
        source_reports.append({"directory": source, "shard_index": index, "snapshot_blocks": q["snapshot_blocks"],
                               "assignment": assignment, "successful_queries": len(source_queries), "files": provenance})
    frames = {}
    duplicates = {}
    for label, values in parts.items():
        combined = pd.concat(values, ignore_index=True)
        unique = combined.drop_duplicates().reset_index(drop=True)
        require(not unique.duplicated(KEYS[label]).any(), f"Conflicting values for the same key in {label}")
        duplicates[label] = len(combined) - len(unique)
        frames[label] = unique.sort_values(KEYS[label]).reset_index(drop=True)
    coverage = pd.DataFrame([
        {"snapshot_id": sid, "snapshot_block": blocks[sid], "market_id": mid, "wallet_address": w,
         "assigned_shard": owner(w), "covered": (sid, mid, w) in covered,
         "source_shards": ";".join(covered.get((sid, mid, w), []))}
        for sid, mid, w in sorted(expected)
    ])
    missing = coverage.loc[~coverage.covered]
    summary = []
    for sid in blocks:
        c = coverage.loc[coverage.snapshot_id.eq(sid)]
        summary.append({"snapshot_id": sid, "snapshot_block": blocks[sid], "target_wallets": len(current[sid]),
                        "expected_queries": len(c), "covered_queries": int(c.covered.sum()),
                        "missing_queries": int((~c.covered).sum()), "coverage_pct": 100 * c.covered.mean(),
                        **{label + "_rows": int(f.snapshot_id.eq(sid).sum()) for label, f in frames.items()}})
    quality = {"created_utc": datetime.now(timezone.utc).isoformat(), "chain_id": 1, "snapshot_blocks": blocks,
               "markets": markets, "complete": missing.empty, "failed_queries": 0,
               "expected_wallet_market_snapshot_queries": len(expected), "successful_queries": len(covered),
               "positions_rows": len(frames["positions"]), "active_compound_market_accounts": len(frames["accounts"]),
               "unique_overlap_wallet_snapshot_pairs": len(frames["overlap"]),
               "selection": {"limited_sample": False}, "missing_queries": len(missing),
               "validation_status": "coverage_complete" if missing.empty else "incomplete_coverage",
               "scope": "Aave borrower-targeted Compound III USDC/WETH; snapshots A/B1/B2 only",
               "coverage_evidence": "Reconstructed from completed source QA, matching input selection hashes and deterministic assignments; not an independent RPC recheck.",
               "limitations": ["Missing queries are unknown, not zero balances", "Not a census of all Compound users", "Legacy snapshot B is excluded"],
               "git_head": head, "legacy_aave_ref": legacy_ref, "legacy_aave_sha256": hashlib.sha256(old_raw).hexdigest(),
               "current_aave_sha256": hashlib.sha256(current_path.read_bytes()).hexdigest(),
               "snapshots": summary, "exact_duplicate_rows_removed": duplicates, "sources": source_reports}
    # Validate combined table arithmetic on the observed scope, even when coverage is partial.
    with validation_directory() as temp:
        for label, frame in frames.items():
            frame.to_parquet(temp / TABLES[label], index=False)
        check_quality = {**quality, "complete": True, "expected_wallet_market_snapshot_queries": len(covered)}
        (temp / "quality.json").write_text(json.dumps(check_quality), encoding="utf-8")
        _check({**{label: temp / name for label, name in TABLES.items()}, "quality": temp / "quality.json"})
    output.mkdir(parents=True)
    for label, frame in frames.items():
        frame.to_parquet(output / TABLES[label], index=False)
        pd.testing.assert_frame_equal(frame, pd.read_parquet(output / TABLES[label]))
    coverage.to_parquet(output / "query_coverage.parquet", index=False)
    missing.to_csv(output / "missing_queries.csv", index=False)
    pd.DataFrame(summary).to_csv(output / "snapshot_summary.csv", index=False)
    for name in ("n3_compound_quality.json", "merge_report.json"):
        (output / name).write_text(json.dumps(quality, indent=2, ensure_ascii=False, allow_nan=False), encoding="utf-8")
    print(json.dumps({"output": str(output), "complete": missing.empty, "snapshots": summary}, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--legacy-aave-ref", default="aca4cdd")
    args = parser.parse_args()
    consolidate(args.output.resolve(), args.legacy_aave_ref)
