#!/usr/bin/env python3
"""Guarded B1/B2 extension for DeFiRisk-Lab Compound III team shards.

This wrapper does NOT implement an alternative RPC collector. It validates the
repository and invokes the existing src/data/fetch_compound_overlap.py
--extend-existing-shard for dry-run or collection. It refuses old round-robin
shards to prevent mixing assignments. QA verifies old A/B remained identical
against the backup created by the existing collector, four Parquet files,
checkpoint ownership/counts, and the exact snapshot blocks.

Requires Python 3.10+; preflight/dry-run/collect/qa need pandas+pyarrow.
Only 'collect' launches the archive-RPC-enabled repository collector.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import subprocess
import sys
from collections import Counter
from pathlib import Path

OLD = {"A": 26179841, "B": 26194188}
NEW = {"B1": 20458637, "B2": 23543615}
ALL = {**OLD, **NEW}
SHARDS = 6
MARKETS = {"compound_v3_ethereum_usdc", "compound_v3_ethereum_weth"}
FILES = {
    "accounts": "n3_compound_account_reference.parquet",
    "positions": "n3_compound_positions_long.parquet",
    "overlap": "n3_compound_overlap_wallets.parquet",
    "reserve": "n3_compound_reserve_config.parquet",
}
QA_NAME = "n3_compound_quality.json"
KEYS = {
    "accounts": ("snapshot_block", "market_id", "wallet_address"),
    "positions": ("snapshot_block", "market_id", "wallet_address", "token_address", "asset_role"),
    "overlap": ("snapshot_block", "wallet_address"),
    "reserve": ("snapshot_block", "market_id", "token_address", "role"),
}
ADDR = re.compile(r"0x[a-f0-9]{40}\Z")


def stop(message: str) -> None:
    raise SystemExit("[STOP - NOTHING RESET] " + message)


def read_json(path: Path) -> dict:
    if not path.is_file():
        stop(f"Missing file: {path}")
    try:
        obj = json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, OSError) as exc:
        stop(f"Cannot parse {path}: {type(exc).__name__}: {exc}")
    if not isinstance(obj, dict):
        stop(f"Expected JSON object: {path}")
    return obj


def digest(plan: dict) -> str:
    return hashlib.sha256(json.dumps(plan, sort_keys=True).encode("utf-8")).hexdigest()


def plan_owners(repo: Path, shard: int):
    plan = read_json(repo / "config/n3_compound_team_assignment.json")
    if plan.get("num_shards") != SHARDS or plan.get("version") != 1:
        stop("Team plan must have version 1 / six shards")
    owners = plan.get("wallet_shards")
    if not isinstance(owners, dict) or len(owners) != 28640:
        stop("Team plan missing full 28,640 wallet ownership entries")
    if any(not ADDR.fullmatch(w) or type(owner) is not int or owner not in range(SHARDS)
           for w, owner in owners.items()):
        stop("Invalid wallet or owner in team plan")
    counts = Counter(owners.values())
    if sum(counts.values()) != 28640 or any(counts[i] == 0 for i in range(SHARDS)):
        stop("Unexpected team wallet counts")
    return plan, owners, digest(plan)


def locate(repo: Path, shard: int, source_root: Path | None) -> Path:
    name = f"shard_{shard:03d}_of_006"
    roots = [source_root.resolve()] if source_root else [repo, repo / "data/processed/compound"]
    found = []
    for root in roots:
        for path in (root / name, root / name / name):
            if (path / QA_NAME).is_file() and path.resolve() not in found:
                found.append(path.resolve())
    if len(found) != 1:
        stop(f"Need exactly one old A/B shard folder '{name}', found {len(found)}: {found}. "
             "Supply --existing-shards-root PARENT_FOLDER (the parent of shard_NNN_of_006), "
             "or restore the missing shard without overwriting it.")
    return found[0]


def require_core(repo: Path) -> Path:
    core = repo / "src/data/fetch_compound_overlap.py"
    if not core.is_file():
        stop(f"Missing repo collector: {core}")
    content = core.read_text(encoding="utf-8")
    must = ('--extend-existing-shard', '--existing-shards-root', 'n3_overlap_checkpoint_B1_B2.jsonl')
    missing = [f for f in must if f not in content]
    if missing:
        stop(f"GitHub collector does not support B1/B2 extension, missing {missing}. "
             "Pull the team's reviewed implementation; do NOT fall back to plain scraping.")
    return core


def validate_metadata(repo: Path):
    meta = read_json(repo / "data/processed/aave/snapshot_metadata.json")
    if meta.get("chain_id") != 1:
        stop("Aave metadata must be Ethereum mainnet")
    for sid, block in NEW.items():
        row = meta.get("snapshots", {}).get(sid)
        if not isinstance(row, dict) or row.get("snapshot_block") != block:
            stop(f"Missing or incorrect {sid} snapshot block (expected {block})")
        if row.get("block_hash_verified_on_chain") is not True or not row.get("block_hash"):
            stop(f"N2 {sid} block hash not verified")
    markets_path = repo / "config/n3_compound_markets.csv"
    if not markets_path.is_file():
        stop("Missing config/n3_compound_markets.csv")
    with markets_path.open(newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    got = {r.get("market_id") for r in rows}
    if len(rows) != 2 or got != MARKETS or any(str(r.get("chain_id")) != "1" for r in rows):
        stop(f"Must configure exactly the USDC/WETH Ethereum Comet markets. Got {got}")


def pd_reader():
    try:
        import pandas as pd
        import pyarrow  # noqa: F401
    except ImportError:
        stop("Missing pandas/pyarrow. Run pip install -r requirements.txt (or see setup README)")
    return pd


def get_frames(folder: Path):
    pd = pd_reader()
    frames = {}
    for label, filename in FILES.items():
        path = folder / filename
        if not path.is_file():
            stop(f"Missing shard file: {path}")
        try:
            frame = pd.read_parquet(path)
        except Exception as exc:
            stop(f"Cannot read Parquet {path}: {exc}")
        needed = {"snapshot_id", "snapshot_block"} | set(KEYS[label])
        if not needed.issubset(frame.columns):
            stop(f"{filename}: missing columns {sorted(needed - set(frame.columns))}")
        if frame.duplicated(list(KEYS[label])).any():
            stop(f"Duplicate primary keys in {filename}")
        frames[label] = frame
    return frames


def check_frames(frames: dict, blocks: dict, owners: dict | None = None, shard: int | None = None):
    pd = pd_reader()
    for label, df in frames.items():
        sid = df["snapshot_id"].astype(str)
        actual = pd.to_numeric(df["snapshot_block"], errors="coerce")
        if not sid.isin(blocks).all() or not actual.eq(sid.map(blocks)).all():
            stop(f"{label}: invalid snapshot / block mapping")
        if label != "reserve":
            ws = df["wallet_address"].astype(str).str.lower()
            if ws.map(lambda w: bool(ADDR.fullmatch(w))).eq(False).any():
                stop(f"{label}: invalid wallet address format")
            if owners is not None and shard is not None:
                if not ws.map(lambda w: ownership(w, owners)).eq(shard).all():
                    stop(f"{label}: at least one row belongs to another shard")
    obs = set(zip(frames["reserve"]["snapshot_id"].astype(str),
                  frames["reserve"]["market_id"].astype(str)))
    expected_pairs = {(sid, mid) for sid in blocks for mid in MARKETS}
    if obs != expected_pairs:
        stop(f"Reserve config pairs do not match {expected_pairs}; got {obs}")
    for label, df in frames.items():
        if "market_id" in df.columns and not set(df["market_id"].astype(str)).issubset(MARKETS):
            stop(f"Unexpected market in {label}")


def ownership(w: str, old_owners: dict) -> int:
    if w in old_owners:
        return old_owners[w]
    d = hashlib.sha256(w.encode("utf-8")).digest()
    return int.from_bytes(d[:8], "big") % SHARDS


def preflight(repo: Path, shard: int, source_root: Path | None, *, expect_extended: bool):
    core = require_core(repo)
    plan, owners, plan_sha = plan_owners(repo, shard)
    validate_metadata(repo)
    folder = locate(repo, shard, source_root)
    qa = read_json(folder / QA_NAME)
    if qa.get("chain_id") != 1 or qa.get("shard", {}).get("num_shards") != SHARDS or qa.get("shard", {}).get("shard_index") != shard:
        stop("Wrong chain or shard identity in QA")
    if not expect_extended:
        if qa.get("snapshot_blocks") != OLD:
            stop("Shard old QA must have exactly original A/B; already extended or wrong snapshot")
        if qa["shard"].get("assignment") != f"checkpoint_assignment_v1:{plan_sha}":
            stop("Old A/B QA is not checkpoint-assigned. "
                 "If it says sorted_wallet_round_robin_v1, first rebuild this shard according to the team plan. "
                 "Do not append B1/B2 to wrong legacy ownership.")
        if qa["shard"].get("selection_hash") != plan.get("selection_hash"):
            stop("Old A/B QA selection hash differs from team plan")
        if qa.get("metadata_fingerprint") != plan.get("metadata_fingerprint"):
            stop("Old A/B QA market fingerprint differs from team plan")
        if qa.get("complete") is not True or qa.get("failed_queries") != 0 or qa.get("successful_queries") != qa.get("expected_wallet_market_snapshot_queries"):
            stop("Old A/B dataset not QA-complete; finish/repair it first")
    else:
        if qa.get("snapshot_blocks") != ALL:
            stop("Updated QA must contain original A/B and new B1/B2 blocks exactly")
        if qa.get("shard", {}).get("assignment") != "legacy_owner_plus_sha256_new_wallets_v1":
            stop("Updated QA does not report expected B1/B2 ownership strategy")
        if qa.get("complete") is not True or qa.get("failed_queries") != 0 or qa.get("successful_queries") != qa.get("expected_wallet_market_snapshot_queries"):
            stop("Updated QA not marked fully complete")
        if qa.get("extension", {}).get("added_snapshots") != NEW or qa.get("extension", {}).get("original_AB_kept") is not True:
            stop("Updated QA lacks expected B1/B2 extension declaration")
    frames = get_frames(folder)
    check_frames(frames, ALL if expect_extended else OLD,
                 owners=owners, shard=shard)
    print(f"[PASS] shard={shard} | repo={repo}")
    print(f"[PASS] assignment plan={plan_sha[:12]} | team wallets={sum(i==shard for i in owners.values())}")
    print(f"[PASS] source={folder}")
    print(f"[PASS] QA snapshots={qa['snapshot_blocks']}")
    print("[PASS] 4 Parquet present, mapping + shard ownership + keys checked")
    return core, folder, qa, frames, owners


def run_core(core: Path, folder: Path, shard: int, args, *, dry: bool):
    cmd = [sys.executable, str(core), "--extend-existing-shard",
           "--num-shards", "6", "--shard-index", str(shard),
           "--max-wallets", "0", "--existing-shards-root", str(folder.parent),
           "--batch-size", str(args.batch_size), "--wallet-chunk", str(args.wallet_chunk),
           "--rpc-interval", str(args.rpc_interval)]
    if dry:
        cmd.append("--dry-run")
    print("[RUN]", " ".join(cmd), flush=True)
    rc = subprocess.call(cmd, cwd=core.parents[2])
    if rc != 0:
        stop(f"Collector exit code={rc}. If checkpoint started, rerun exactly the same collect command. "
             "Do not reset; do not stage incomplete files.")


def checkpoint_qa(folder: Path, qa: dict, owners: dict, shard: int):
    expected = int(qa.get("extension", {}).get("historical_queries_added", -1))
    if expected < 0:
        stop("Missing historical_queries_added in QA")
    ck = folder / "n3_overlap_checkpoint_B1_B2.jsonl"
    if not ck.is_file():
        stop(f"No B1/B2 checkpoint found at {ck}")
    found = {}
    fp = set()
    with ck.open(encoding="utf-8") as fh:
        for ln, line in enumerate(fh, 1):
            if not line.strip():
                continue
            try:
                r = json.loads(line)
            except ValueError:
                stop(f"Checkpoint invalid JSON at line {ln}")
            if r.get("status") != "ok":
                stop(f"Checkpoint line {ln} is not successful")
            sid, mid, w = r.get("snapshot_id"), r.get("market_id"), str(r.get("wallet", "")).lower()
            if sid not in NEW or mid not in MARKETS or not ADDR.fullmatch(w):
                stop(f"Invalid snapshot/market/wallet in checkpoint line {ln}")
            if ownership(w, owners) != shard:
                stop(f"Wrong-shard wallet in checkpoint line {ln}")
            if not isinstance(r.get("values"), dict):
                stop(f"Malformed RPC values in checkpoint line {ln}")
            fp.add(r.get("fingerprint"))
            key = (sid, mid, w)
            if key in found and found[key] != r.get("values"):
                stop(f"Conflicting duplicate checkpoint RPC key {key}")
            found[key] = r["values"]
    if len(fp) != 1 or None in fp:
        stop("Checkpoint fingerprint inconsistent")
    if len(found) != expected:
        stop(f"Checkpoint unique successful keys={len(found)}, QA expected B1/B2 queries={expected}")
    counts = Counter((sid, mid) for sid, mid, _ in found)
    for sid in NEW:
        if not counts[(sid, "compound_v3_ethereum_usdc")] or not counts[(sid, "compound_v3_ethereum_weth")]:
            stop(f"No successful queries for {sid} in both Comet markets")
        if counts[(sid, "compound_v3_ethereum_usdc")] != counts[(sid, "compound_v3_ethereum_weth")]:
            stop(f"Unequal USDC/WETH scanned-wallet count for {sid}")
    print("[PASS] B1/B2 RPC proofs:", dict(sorted(counts.items())))
    return found


def backup_qa(folder: Path, old_owners: dict, shard: int, current_frames: dict):
    pd = pd_reader()
    basename = folder.name
    backup_glob = f"Compound_{basename}_before_B1B2_*"
    possibilities = [x for x in (Path.home() / "Downloads").glob(backup_glob) if x.is_dir()]
    valid = []
    for b in possibilities:
        try:
            bqa = read_json(b / QA_NAME)
            if bqa.get("snapshot_blocks") != OLD or bqa.get("shard", {}).get("shard_index") != shard:
                continue
            saved = get_frames(b)
            check_frames(saved, OLD, old_owners, shard)
            valid.append((b, saved))
        except SystemExit:
            continue
    if not valid:
        stop(f"Cannot find a valid ORIGINAL A/B backup in ~/Downloads/{backup_glob}. "
             "The collector creates it automatically before replacement. Do not stage until backed-up A/B can be compared.")
    # The last backup usually belongs to the latest successful extension, but
    # use exact old-data comparison rather than trusting the name/date.
    for backup, saved in sorted(valid, key=lambda x: x[0].name, reverse=True):
        matches = True
        for label, before in saved.items():
            after = current_frames[label]
            kept = after[after["snapshot_id"].isin(OLD)].reset_index(drop=True)
            try:
                pd.testing.assert_frame_equal(kept, before.reset_index(drop=True), check_exact=True)
            except AssertionError:
                matches = False
                break
        if matches:
            print("[PASS] Exact original A/B Parquet rows preserved versus backup:", backup)
            return backup
    stop("A/B rows do not exactly match ANY original backup! Do not push; investigate without resetting")


def final_qa(repo: Path, shard: int, source_root: Path | None):
    _, folder, qa, frames, owners = preflight(repo, shard, source_root, expect_extended=True)
    checkpoint_qa(folder, qa, owners, shard)
    backup = backup_qa(folder, owners, shard, frames)
    print("[QA PASS] shard", shard, "| A/B unchanged + B1/B2 both markets + 5 final files")
    print("[HANDOFF 4 PARQUET + 1 JSON]")
    for name in (*FILES.values(), QA_NAME):
        p = folder / name
        print("  ", p.relative_to(repo) if p.is_relative_to(repo) else p, f"({p.stat().st_size:,} bytes)")
    return folder, qa


def stage_only(repo: Path, folder: Path):
    rel = []
    for name in (*FILES.values(), QA_NAME):
        p = folder / name
        try:
            rel.append(p.relative_to(repo).as_posix())
        except ValueError:
            stop("Shard folder is outside repo; cannot stage tracked files")
    def git(*argv, capture=False):
        return subprocess.run(["git", *argv], cwd=repo, text=True, capture_output=capture)
    br = git("branch", "--show-current", capture=True)
    if br.returncode or not br.stdout.strip() or br.stdout.strip() in ("main", "master"):
        stop("Create or switch to your PERSONAL shard branch before stage: git switch -c data/compound-b1b2-shard-NNN")
    pre = git("diff", "--cached", "--name-only", capture=True)
    if pre.returncode or pre.stdout.strip():
        stop("Other files already staged; review with git diff --cached --name-status, then unstage manually before continuing")
    for p in rel:
        tracked = git("ls-files", "--error-unmatch", "--", p, capture=True)
        if tracked.returncode:
            stop(f"Original GitHub file is not tracked at '{p}'. "
                 "Do not create another parallel copy; check branch/checkout and source path")
        if (repo / p).stat().st_size >= 100 * 1024 * 1024:
            stop(f"File exceeds GitHub 100 MiB hard limit: {p}")
    result = git("add", "--", *rel)
    if result.returncode:
        stop("git add failed; check Git permissions or LFS")
    actual = git("diff", "--cached", "--name-only", capture=True)
    if actual.returncode or not set(actual.stdout.splitlines()).issubset(set(rel)):
        stop("Unexpected staged changes. Stop and review git diff --cached --name-status")
    print("[PASS] Only the team's five tracked handoff files staged. No backup, .env, checkpoint, or scripts.")
    subprocess.run(["git", "diff", "--cached", "--name-status"], cwd=repo, check=True)
    print("[NEXT] git commit -m \"data(compound): extend shard B1 B2\"")
    print("[NEXT] git push -u origin <YOUR-SHARD-BRANCH>")
    print("[NEXT] Open Pull Request into main; do not push directly onto main")


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("command", choices=("check", "dry-run", "collect", "qa", "stage"))
    ap.add_argument("--shard", type=int, required=True, help="Your assigned shard, 0..5")
    ap.add_argument("--repo", type=Path, default=Path.cwd(), help="Project root (default current dir)")
    ap.add_argument("--existing-shards-root", type=Path,
                    help="Parent folder of shard_NNN_of_006 when not in usual repo locations")
    ap.add_argument("--batch-size", type=int, default=1)
    ap.add_argument("--wallet-chunk", type=int, default=1)
    ap.add_argument("--rpc-interval", type=float, default=2.0)
    args = ap.parse_args()
    repo = args.repo.resolve()
    if args.shard not in range(SHARDS):
        stop("Shard must be an integer 0..5")
    if args.batch_size < 1 or args.wallet_chunk < 1 or not 0 <= args.rpc_interval < float("inf"):
        stop("Invalid numeric RPC settings")
    if args.command in ("check", "dry-run", "collect"):
        core, folder, *_ = preflight(repo, args.shard, args.existing_shards_root, expect_extended=False)
        if args.command == "check":
            return
        run_core(core, folder, args.shard, args, dry=True)
        if args.command == "collect":
            print("[GATE PASSED] Starting archive RPC after successful dry-run", flush=True)
            run_core(core, folder, args.shard, args, dry=False)
            print("[COMPLETE PROCESS EXIT] Verify QA before stage; never assume QA PASS from collector exit alone")
    elif args.command == "qa":
        final_qa(repo, args.shard, args.existing_shards_root)
    elif args.command == "stage":
        folder, _ = final_qa(repo, args.shard, args.existing_shards_root)
        stage_only(repo, folder)


if __name__ == "__main__":
    main()
