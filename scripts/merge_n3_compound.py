"""Merge complete team exports locally, with coverage and compatibility checks (no RPC)."""
import argparse
import importlib.util
import json
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
from qa_n3_compound import check

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("compound_fetch", ROOT / "src/data/fetch_compound_overlap.py")
fetch = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fetch)

TABLES = {
    "positions": "n3_compound_positions_long.parquet",
    "accounts": "n3_compound_account_reference.parquet",
    "config": "n3_compound_reserve_config.parquet",
    "overlap": "n3_compound_overlap_wallets.parquet",
}


def merge(inputs: list[Path], output: Path, overwrite: bool = False) -> None:
    if not inputs:
        raise ValueError("No input shards")
    if any(output.resolve() == path.resolve() for path in inputs):
        raise ValueError("Output must differ from input directories")
    reports = []
    for path in inputs:
        check(path)
        reports.append(json.loads((path / "n3_compound_quality.json").read_text(encoding="utf-8")))
    first = reports[0]
    shard = first["shard"]
    count = shard["num_shards"]
    ids = [report["shard"]["shard_index"] for report in reports]
    if len(ids) != count or set(ids) != set(range(count)):
        raise ValueError("Need exactly one complete export for every shard index")
    for report in reports:
        for key in ("num_shards", "selection_hash", "global_selection", "global_expected_queries", "assignment"):
            if report["shard"][key] != shard[key]:
                raise ValueError("Incompatible shard selection: " + key)
        for key in ("metadata_fingerprint", "snapshot_blocks", "markets", "chain_id"):
            if report[key] != first[key]:
                raise ValueError("Incompatible snapshot/market metadata: " + key)
    expected = shard["global_expected_queries"]
    if sum(report["successful_queries"] for report in reports) != expected:
        raise ValueError("Shard coverage does not match global expected queries")
    if sum(report["selection"]["selected_unique_wallets"] for report in reports) != shard["global_selection"]["selected_unique_wallets"]:
        raise ValueError("Shard wallet counts do not match global selection")
    frames = {}
    for label, filename in TABLES.items():
        parts = [pd.read_parquet(path / filename) for path in inputs]
        if label == "config":
            keys = ["snapshot_block", "comet_address", "token_address"]
            baseline = parts[0].sort_values(keys).reset_index(drop=True)
            for part in parts[1:]:
                pd.testing.assert_frame_equal(baseline, part.sort_values(keys).reset_index(drop=True))
            frames[label] = baseline
        else:
            frames[label] = pd.concat(parts, ignore_index=True)
    keys = {
        "positions": ["snapshot_block", "comet_address", "wallet_address", "token_address", "asset_role"],
        "accounts": ["snapshot_block", "comet_address", "wallet_address"],
        "overlap": ["snapshot_block", "wallet_address"],
    }
    for label, columns in keys.items():
        if frames[label].duplicated(columns).any():
            raise ValueError("Overlapping shard rows in " + label)
    report = {**first,
              "created_utc": datetime.now(timezone.utc).isoformat(),
              "selection": shard["global_selection"],
              "shard": {**shard, "shard_index": None, "merged_indices": sorted(ids)},
              "expected_wallet_market_snapshot_queries": expected,
              "successful_queries": expected, "failed_queries": 0, "complete": True,
              "positions_rows": len(frames["positions"]),
              "active_compound_market_accounts": len(frames["accounts"]),
              "unique_overlap_wallet_snapshot_pairs": len(frames["overlap"]),
              "any_borrow_market_accounts": int((frames["accounts"].compound_borrow_usd > 0).sum()),
              "merged_from": [str(path) for path in inputs]}
    # Validate the combined export in a temporary directory before touching destinations.
    import tempfile
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="compound_merge_", dir=output.parent) as temp:
        staging = Path(temp)
        for label, filename in TABLES.items():
            frames[label].to_parquet(staging / filename, index=False)
        (staging / "n3_compound_quality.json").write_text(json.dumps(report), encoding="utf-8")
        check(staging)
    for filename in [*TABLES.values(), "n3_compound_quality.json"]:
        if (output / filename).exists() and not overwrite:
            raise FileExistsError(f"{output / filename} exists; use --overwrite to back up and replace")
    for label, filename in TABLES.items():
        fetch.save_parquet(frames[label], output / filename, overwrite)
    fetch.save_json(report, output / "n3_compound_quality.json", overwrite)
    print("[MERGED]", output)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--inputs", nargs="+", type=Path, help="Completed shard directories")
    source.add_argument("--team-dir", type=Path, help="Directory containing all completed shard_* directories")
    parser.add_argument("--output", type=Path, default=ROOT / "data/processed/compound/merged")
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args()
    inputs = sorted(path for path in args.team_dir.glob("shard_*") if path.is_dir()) if args.team_dir else args.inputs
    merge(inputs, args.output, args.overwrite)
