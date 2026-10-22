"""Prepare a fixed team assignment AFTER stopping the original collector. No RPC."""
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("compound_fetch", ROOT / "src/data/fetch_compound_overlap.py")
fetch = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fetch)


def make_plan(selected: dict, checkpoint: Path, count: int, leader_wallets: int) -> dict:
    if count < 2 or leader_wallets < 0:
        raise ValueError("Require num-shards >= 2 and leader-wallets >= 0")
    wallets = {w for pairs in selected.values() for w in pairs}
    cached = set()
    fingerprints = set()
    with checkpoint.open(encoding="utf-8") as source:
        for line_number, line in enumerate(source, 1):
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except ValueError:
                raise ValueError(f"Invalid checkpoint JSON at line {line_number}; preserve the original file") from None
            if record.get("status") == "ok":
                fingerprints.add(record["fingerprint"])
                if record["wallet"] in wallets:
                    cached.add(record["wallet"])
    if len(fingerprints) != 1 or not cached:
        raise ValueError("Need a nonempty checkpoint with one metadata fingerprint")
    # Keep ALL prior work on the leader, including cached wallets at other markets/snapshots.
    target = min(len(wallets), max(leader_wallets, len(cached)))
    leader = set(cached)
    for wallet in sorted(wallets):
        if len(leader) >= target:
            break
        leader.add(wallet)
    owners = {wallet: 0 for wallet in sorted(leader)}
    for offset, wallet in enumerate(sorted(wallets - leader)):
        owners[wallet] = 1 + offset % (count - 1)
    return {"version": 1, "num_shards": count,
            "selection_hash": hashlib.sha256(json.dumps(selected, sort_keys=True).encode("utf-8")).hexdigest(),
            "metadata_fingerprint": next(iter(fingerprints)), "wallet_shards": owners}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--aave", type=Path, required=True)
    parser.add_argument("--snapshot-metadata", type=Path, required=True)
    parser.add_argument("--checkpoint", type=Path, default=fetch.CACHE)
    parser.add_argument("--num-shards", type=int, default=6)
    parser.add_argument("--leader-wallets", type=int, default=8000,
                        help="Minimum leader allocation; retain more if checkpoint already touches more wallets")
    parser.add_argument("--output", type=Path, default=ROOT / "config/n3_compound_team_assignment.json")
    args = parser.parse_args()
    snapshots = fetch.load_snapshots(args.snapshot_metadata)
    selected, _ = fetch.pick_wallets(pd.read_parquet(args.aave), snapshots, 0)
    plan = make_plan(selected, args.checkpoint, args.num_shards, args.leader_wallets)
    digest = fetch.assignment_partition(plan, selected, args.num_shards, 0)[1]
    # Once distributed, an assignment must stay fixed. Never silently replace it.
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x", encoding="utf-8") as dest:
        json.dump(plan, dest, indent=2, ensure_ascii=False)
    print("[ASSIGNMENT]", args.output)
    print("[TEAM FOLDER]", "team_" + digest[:12])
    for index in range(args.num_shards):
        shard, _ = fetch.assignment_partition(plan, selected, args.num_shards, index)
        unique = {w for pairs in shard.values() for w in pairs}
        print(f"[SHARD {index}] {len(unique)} wallets")
    print("Share this assignment JSON with all members; keep the original checkpoint on the leader's machine.")
