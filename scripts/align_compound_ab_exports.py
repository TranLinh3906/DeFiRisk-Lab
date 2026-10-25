#!/usr/bin/env python3
"""Reassign OLD Compound snapshot A/B Parquet exports using fixed team wallet_shards.

No RPC calls. Read-only unless --apply. Never edits source Parquet/JSON/checkpoints.
It is a data REPARTITION, not a reconstruction of historical per-wallet RPC checkpoints.
Produces alignment reports, not a fabricated full-coverage n3_compound_quality.json.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import tempfile
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

EXPECTED = {"A": 26179841, "B": 26194188}
N = 6
PARTS = {
    "positions": ("n3_compound_positions_long.parquet", ["snapshot_block", "market_id", "wallet_address", "token_address", "asset_role"]),
    "accounts": ("n3_compound_account_reference.parquet", ["snapshot_block", "market_id", "wallet_address"]),
    "config": ("n3_compound_reserve_config.parquet", ["snapshot_block", "market_id", "token_address", "role"]),
    "overlap": ("n3_compound_overlap_wallets.parquet", ["snapshot_block", "wallet_address"]),
}
OVERLAP_COLUMNS = [
    "snapshot_id", "snapshot_block", "wallet_address", "compound_markets_count",
    "compound_borrow_usd", "compound_collateral_usd", "compound_base_supply_usd",
    "any_compound_liquidatable", "aave_debt_usd",
]
ADDRESS = re.compile(r"0x[a-f0-9]{40}\Z")


def die(message):
    raise SystemExit("ERROR: " + str(message))


def load_json(p):
    with p.open(encoding="utf-8") as f:
        return json.load(f)


def file_sha256(path):
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def check_unique(df, label, keys):
    missing = set(keys) - set(df)
    if missing:
        die(f"{label}: missing key columns: {sorted(missing)}")
    dups = df[df.duplicated(keys, keep=False)]
    if dups.empty:
        return df
    # Deduplicate only IDENTICAL rows. Conflicting rows for the same key are fatal.
    for key, group in dups.groupby(keys, dropna=False):
        if len(group.drop_duplicates()) != 1:
            die(f"{label}: conflicting rows for key {key}. Refusing silent overwrite")
    return df.drop_duplicates(subset=keys, keep="first").reset_index(drop=True)


def validate_frame(df, label):
    if not {"snapshot_id", "snapshot_block"}.issubset(df.columns):
        die(f"{label}: missing snapshot_id / snapshot_block")
    if not df.empty:
        sid = df["snapshot_id"].astype(str)
        blk = pd.to_numeric(df["snapshot_block"], errors="coerce")
        if not sid.isin(EXPECTED).all() or not (blk == sid.map(EXPECTED)).all():
            die(f"{label}: snapshot blocks not exclusively the original A/B: {EXPECTED}")
    if "wallet_address" in df:
        bad = df["wallet_address"].astype(str).str.lower().map(lambda w: not bool(ADDRESS.fullmatch(w)))
        if bad.any():
            die(f"{label}: invalid wallet address")


def aggregate_overlap(accounts):
    if accounts.empty:
        return pd.DataFrame(columns=OVERLAP_COLUMNS)
    cols = (set(OVERLAP_COLUMNS) - {"compound_markets_count", "any_compound_liquidatable"}) | {"market_id", "compound_is_liquidatable"}
    if not cols.issubset(accounts):
        die(f"accounts missing fields needed for overlap: {sorted(cols - set(accounts))}")
    return accounts.groupby(["snapshot_id", "snapshot_block", "wallet_address"], as_index=False).agg(
        compound_markets_count=("market_id", "nunique"),
        compound_borrow_usd=("compound_borrow_usd", "sum"),
        compound_collateral_usd=("compound_collateral_usd", "sum"),
        compound_base_supply_usd=("compound_base_supply_usd", "sum"),
        any_compound_liquidatable=("compound_is_liquidatable", "any"),
        aave_debt_usd=("aave_debt_usd", "max"),
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=Path.cwd())
    parser.add_argument("--apply", action="store_true", help="Write into a NEW output directory")
    parser.add_argument("--allow-partial", action="store_true", help="Stage existing shard exports even if one of six is missing; report remains PARTIAL")
    args = parser.parse_args()
    repo = args.repo.resolve()
    planfile = repo / "config/n3_compound_team_assignment.json"
    if not planfile.is_file():
        die(f"assignment not found: {planfile}")
    plan = load_json(planfile)
    owners = plan.get("wallet_shards", {})
    if plan.get("version") != 1 or plan.get("num_shards") != N or not owners:
        die("assignment must be version=1, num_shards=6, and contain wallet_shards")
    if any(not ADDRESS.fullmatch(w) or type(i) is not int or not 0 <= i < N for w, i in owners.items()):
        die("assignment has invalid wallet address or shard index")
    phash = hashlib.sha256(json.dumps(plan, sort_keys=True).encode("utf-8")).hexdigest()
    hist_qa = {}
    skipped = []
    print(f"[PLAN] {len(owners)} wallets | owners {dict(sorted(Counter(owners.values()).items()))}")
    print(f"[PLAN] selection={plan['selection_hash']} metadata={plan['metadata_fingerprint']}")

    # Search locally where the exported shard directories actually live, including nested folders.
    for qpath in sorted(repo.rglob("n3_compound_quality.json")):
        if ".git" in qpath.parts:
            continue
        try:
            qa = load_json(qpath)
        except Exception as exc:
            skipped.append(f"Unreadable QA {qpath}: {exc}")
            continue
        if qa.get("snapshot_blocks") != EXPECTED:
            continue  # Never mix historical B1/B2 in the A/B reconciliation.
        shard = qa.get("shard", {})
        idx = shard.get("shard_index")
        if type(idx) is not int or not 0 <= idx < N or shard.get("num_shards") != N:
            skipped.append(f"Invalid shard number: {qpath}")
            continue
        if shard.get("selection_hash") != plan["selection_hash"] or qa.get("metadata_fingerprint") != plan["metadata_fingerprint"]:
            die(f"A/B QA hash differs from team plan at {qpath}; do NOT mix data")
        needed = int(qa.get("expected_wallet_market_snapshot_queries", -1))
        if qa.get("complete") is not True or int(qa.get("failed_queries", -1)) != 0 or int(qa.get("successful_queries", -2)) != needed:
            skipped.append(f"Unfinished A/B QA (ignored): {qpath}")
            continue
        folder = qpath.parent
        missing = [name for name, _ in PARTS.values() if not (folder / name).is_file()]
        if missing:
            skipped.append(f"Missing Parquets next to {qpath}: {missing}")
            continue
        hist_qa.setdefault(idx, []).append((folder, qa))

    chosen = {}
    selection_wallet_sets = {}
    sorted_plan_wallets = sorted(owners)
    for idx in range(N):
        candidates = hist_qa.get(idx, [])
        if not candidates:
            print(f"[MISSING] old A/B COMPLETE export shard {idx}")
            continue
        if len(candidates) > 1:
            # Duplicate file trees are safe only if ALL 4 data files are identical.
            sigs = set(tuple(file_sha256(folder / name) for name, _ in PARTS.values()) for folder, _ in candidates)
            if len(sigs) != 1:
                die(f"Multiple DIFFERENT complete A/B exports for shard {idx}: {[str(f) for f, _ in candidates]}. Choose source manually before aligning")
        # Choose shorter path if duplicated identical exports.
        folder, qa = min(candidates, key=lambda x: (len(x[0].parts), str(x[0])))
        method = qa.get("shard", {}).get("assignment", "")
        if method == "sorted_wallet_round_robin_v1":
            old_wallets = set(sorted_plan_wallets[idx::N])
        elif method.startswith("checkpoint_assignment_v1:"):
            old_wallets = {w for w, shard_index in owners.items() if shard_index == idx}
        else:
            die(f"Unknown A/B original assignment method for shard {idx}: {method!r}; inspect QA")
        counted = qa.get("selection", {}).get("selected_unique_wallets")
        if counted is not None and int(counted) != len(old_wallets):
            die(f"Shard {idx} QA reports {counted} wallets but assignment method implies {len(old_wallets)}")
        print(f"[SOURCE {idx}] {folder} | {qa['successful_queries']}/{qa['expected_wallet_market_snapshot_queries']} RPC results | {method}")
        chosen[idx] = (folder, qa)
        selection_wallet_sets[idx] = old_wallets

    missing_indices = sorted(set(range(N)) - set(chosen))
    originally_covered = set().union(*selection_wallet_sets.values()) if selection_wallet_sets else set()
    missing_wallets = set(owners) - originally_covered
    overlapping_wallet_claims = sum(map(len, selection_wallet_sets.values())) - len(originally_covered)
    print(f"[ORIGINAL COVERAGE] {len(originally_covered)}/{len(owners)} wallets, {len(missing_wallets)} not covered, {overlapping_wallet_claims} multiply assigned")
    for s in skipped[:12]:
        print(f"[SKIPPED] {s}")
    if len(skipped) > 12:
        print(f"[SKIPPED] ... plus {len(skipped)-12} other paths")
    if not chosen:
        die("No complete original A/B Parquet shard exports located. Nothing changed")
    if (missing_indices or missing_wallets) and not args.allow_partial:
        die(f"Missing complete A/B shards {missing_indices}, uncovered wallet owners={len(missing_wallets)}. No data changed. To stage PARTIAL outputs, pass --allow-partial")

    loaded = {name: [] for name in PARTS}
    for idx, (folder, qa) in sorted(chosen.items()):
        for label, (filename, keys) in PARTS.items():
            df = pd.read_parquet(folder / filename)
            validate_frame(df, f"shard {idx}/{filename}")
            if df.duplicated(keys).any():
                die(f"Original shard {idx}/{filename} contains duplicate keys; check source QA")
            expected_field = {
                "positions": "positions_rows",
                "accounts": "active_compound_market_accounts",
                "overlap": "unique_overlap_wallet_snapshot_pairs",
            }.get(label)
            if expected_field in qa and int(qa[expected_field]) != len(df):
                die(f"Original shard {idx}/{filename}: QA count differs from Parquet row count")
            if "wallet_address" in df:
                df = df.copy()
                df["wallet_address"] = df["wallet_address"].astype(str).str.lower()
                unknown = set(df.wallet_address) - set(owners)
                if unknown:
                    die(f"Source {idx}/{filename} has wallets outside assignment, examples {list(sorted(unknown))[:3]}")
                outside_source = set(df.wallet_address) - selection_wallet_sets[idx]
                if outside_source:
                    die(f"Source {idx}/{filename} contains {len(outside_source)} wallets outside its declared historical QA shard assignment")
            loaded[label].append(df)

    combined = {}
    deduplications = {}
    for label, (_, keys) in PARTS.items():
        df = pd.concat(loaded[label], ignore_index=True)
        dedup = check_unique(df, label, keys)
        combined[label] = dedup
        deduplications[label] = len(df) - len(dedup)
        print(f"[READ] {label}: {len(df)} rows -> {len(dedup)} unique ({deduplications[label]} duplicate identical rows)")
    # Recompute overlap from the full account union. Individual old shards may split
    # positions of one wallet across markets, so merely concatenating overlap is wrong.
    overlap = aggregate_overlap(combined["accounts"])
    combined["overlap"] = check_unique(overlap, "regenerated_overlap", PARTS["overlap"][1])

    if not combined["positions"].empty:
        p = combined["positions"]
        a = combined["accounts"]
        pkeys = set(zip(p["snapshot_block"], p["market_id"], p["wallet_address"]))
        akeys = set(zip(a["snapshot_block"], a["market_id"], a["wallet_address"]))
        if not pkeys.issubset(akeys):
            die(f"Found {len(pkeys-akeys)} positions with no matching account row")

    per_shard = {}
    for idx in range(N):
        frames = {}
        for label in ("positions", "accounts", "overlap"):
            df = combined[label]
            mask = df["wallet_address"].map(owners) == idx
            frames[label] = df.loc[mask].reset_index(drop=True)
        frames["config"] = combined["config"].copy()
        per_shard[idx] = frames
        print(f"[TARGET {idx}] assigned={sum(o == idx for o in owners.values())} | active accounts={len(frames['accounts'])} | positions={len(frames['positions'])} | overlap={len(frames['overlap'])}")
    for label in ("accounts", "positions", "overlap"):
        if sum(len(per_shard[i][label]) for i in range(N)) != len(combined[label]):
            die(f"Loss during routing: {label}")

    report = {
        "scope": "Legacy Compound A/B only; no B1/B2",
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "source_qa_complete_shards": sorted(chosen),
        "missing_source_shards": missing_indices,
        "source_full_six_shards_present": not missing_indices,
        "previous_assignment_wallets_covered": len(originally_covered),
        "previous_assignment_wallets_uncovered": len(missing_wallets),
        "previous_assignment_uncovered_samples": sorted(missing_wallets)[:10],
        "previous_assignment_overlap_count": overlapping_wallet_claims,
        "aligned_wallet_owner_source": str(planfile.relative_to(repo)),
        "assignment_sha256": phash,
        "selection_hash": plan["selection_hash"],
        "metadata_fingerprint": plan["metadata_fingerprint"],
        "original_snapshot_blocks": EXPECTED,
        "source_export_directories": {str(i): str(folder.relative_to(repo)) for i, (folder, _) in chosen.items()},
        "source_completed_queries_claim": sum(int(qa["successful_queries"]) for _, qa in chosen.values()),
        "deduplicated_identical_input_rows": deduplications,
        "per_target_shard": {str(i): {
            "assigned_unique_wallets": sum(o == i for o in owners.values()),
            "positions_rows": len(per_shard[i]["positions"]),
            "active_account_rows": len(per_shard[i]["accounts"]),
            "overlap_rows": len(per_shard[i]["overlap"]),
        } for i in range(N)},
        "per_wallet_zero_balance_RPC_proof_rebuilt": False,
        "checkpoint_JSONL_migrated": False,
        "complete_for_all_original_A_B_queries_reverified": False,
        "limitations": [
            "Parquet exports hold active positions/accounts, not successful zero-balance RPC calls.",
            "Original QA is retained for provenance; do not substitute this report for completed per-shard RPC QA.",
            "New per-shard RPC checkpoint data has not been reconstructed.",
        ],
    }
    state = "PARTIAL" if (missing_indices or missing_wallets) else "FULL_SOURCE_COVERAGE"
    dest = repo / "outputs" / f"compound_AB_checkpoint_aligned_{phash[:12]}_{state}"
    print(f"[DESTINATION] {dest}")
    if not args.apply:
        print("[DRY RUN] No files written. Add --apply to create new aligned Parquet exports.")
        return
    if dest.exists():
        die(f"Destination already exists, refusing overwrite: {dest}")
    dest.parent.mkdir(parents=True, exist_ok=True)
    # Stage all writes separately and publish the entire new tree in one rename.
    stage = Path(tempfile.mkdtemp(prefix=".compound_ab_align_", dir=dest.parent))
    try:
        for idx, frames in per_shard.items():
            shard_dir = stage / f"shard_{idx:03d}_of_006"
            shard_dir.mkdir(parents=True)
            for label, (name, _) in PARTS.items():
                frames[label].to_parquet(shard_dir / name, index=False)
            (shard_dir / "n3_compound_alignment_report.json").write_text(
                json.dumps(report["per_target_shard"][str(idx)], ensure_ascii=False, indent=2), encoding="utf-8"
            )
        (stage / "n3_compound_alignment_report.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        stage.rename(dest)
    finally:
        if stage.exists():
            shutil.rmtree(stage)
    print(f"[CREATED] {dest}")
    print("[WARNING] Derived Parquet only. No checkpoint moved, no RPC completion claim fabricated.")


if __name__ == "__main__":
    main()
