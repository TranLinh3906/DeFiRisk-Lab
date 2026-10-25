#!/usr/bin/env python3
"""Resume ONLY Compound legacy A/B, shard 4, following the fixed team wallet_shards.

Safe defaults: dry-run; new checkpoint/output directories; no modifications to
original shards, A/B1/B2, team assignment or aligned partial exports.

Strict legacy scope: supply --aave-ab with historical A/B Aave account_reference;
its selection_hash MUST match the team assignment. If unavailable, deliberately
opt in to --expanded-panel (all assigned wallets at BOTH A and B, broader scope).
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import math
import os
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
from dotenv import load_dotenv
from web3 import Web3

A_BLOCK = 26179841
B_BLOCK = 26194188  # legacy B, NOT B1 / B2
SHARD = 4
MARKET_KEY = "market_id"
ADDR = re.compile(r"^0x[a-f0-9]{40}$")


def fail(msg):
    raise SystemExit("[STOP - NO SOURCE CHANGES] " + str(msg))


def json_read(path):
    with path.open(encoding="utf-8") as f:
        return json.load(f)


def plan_hash(plan):
    return hashlib.sha256(json.dumps(plan, sort_keys=True).encode()).hexdigest()


def load_core(repo):
    path = repo / "src/data/fetch_compound_overlap.py"
    if not path.is_file():
        fail("Cannot find " + str(path))
    spec = importlib.util.spec_from_file_location("n3_compound_core_ab_resume", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    required = ("metadata_for_market", "metadata_fingerprint", "load_markets", "fetch_accounts", "PacedSession", "build_outputs", "pick_wallets")
    if any(not hasattr(module, attr) for attr in required):
        fail("Compound fetch script lacks required functions")
    return module


def canonical_values(record):
    return json.dumps(record["values"], sort_keys=True, separators=(",", ":"))


def checkpoint_iter(paths, fingerprint, wanted, markets):
    found = {}
    original_files = []
    for path in paths:
        if not path.is_file():
            continue
        count = 0
        with path.open(encoding="utf-8") as f:
            for ln, raw in enumerate(f, 1):
                if not raw.strip():
                    continue
                try:
                    rec = json.loads(raw)
                except Exception:
                    fail(f"Invalid JSON in {path}:{ln}. Preserve file for examination")
                if not isinstance(rec, dict) or rec.get("status") != "ok":
                    continue
                if rec.get("fingerprint") != fingerprint:
                    continue
                key = (rec.get("snapshot_id"), rec.get("market_id"), str(rec.get("wallet", "")).lower())
                if key not in wanted:
                    continue
                if key[1] not in markets or not isinstance(rec.get("values"), dict):
                    fail(f"Malformed positive cache record {path}:{ln}")
                if key in found and canonical_values(rec) != canonical_values(found[key]):
                    fail(f"Conflicting historical RPC records for {key}")
                rec = {**rec, "wallet": key[2]}
                found[key] = rec
                count += 1
        if count:
            original_files.append((str(path), count))
    return found, original_files


def frames_from_aligned(repo, explicit, digest, owners):
    if explicit:
        base = explicit.expanduser().resolve()
    else:
        candidates = sorted((repo / "outputs").glob(f"compound_AB_checkpoint_aligned_{digest[:12]}_*"))
        if len(candidates) != 1:
            fail("Expected exactly one aligned export. Supply --aligned-root PATH. Found: " + str(candidates))
        base = candidates[0]
    reportpath = base / "n3_compound_alignment_report.json"
    if not reportpath.is_file():
        fail("Missing aligned export report: " + str(reportpath))
    report = json_read(reportpath)
    if report.get("assignment_sha256") != digest:
        fail("Aligned report does not match current team assignment")
    if report.get("original_snapshot_blocks") != {"A": A_BLOCK, "B": B_BLOCK}:
        fail("Aligned report is not old A/B data")
    folder = base / "shard_004_of_006"
    spec = {
        "positions": ("n3_compound_positions_long.parquet", ["snapshot_id", "market_id", "wallet_address", "token_address", "asset_role"]),
        "accounts": ("n3_compound_account_reference.parquet", ["snapshot_id", "market_id", "wallet_address"]),
        "overlap": ("n3_compound_overlap_wallets.parquet", ["snapshot_id", "wallet_address"]),
    }
    result = {}
    for label, (name, keys) in spec.items():
        p = folder / name
        if not p.is_file():
            fail("Missing aligned Parquet " + str(p))
        frame = pd.read_parquet(p)
        if not set(keys + ["snapshot_block"]).issubset(frame):
            fail("Bad aligned frame fields: " + str(p))
        if frame.duplicated(keys).any():
            fail("Duplicate unique keys in " + str(p))
        if not frame.empty:
            frame = frame.copy()
            frame["wallet_address"] = frame["wallet_address"].astype(str).str.lower()
            if any(owners.get(w) != SHARD for w in frame.wallet_address):
                fail("Aligned shard4 contains wallets assigned elsewhere")
            if not (pd.to_numeric(frame.snapshot_block) == frame.snapshot_id.map({"A": A_BLOCK, "B": B_BLOCK})).all():
                fail("Aligned shard4 snapshot/block mapping mismatched")
        result[label] = frame
    return base, result


def discover_checkpoints(repo, extra_paths, own_checkpoint):
    # A/B1/B2 history must never be imported as old A/B checkpoint.
    candidates = set()
    for root in [repo / "data/raw/compound", repo / "shard_004_of_006", repo / "scripts", *extra_paths]:
        if root.is_file() and root.suffix == ".jsonl":
            candidates.add(root.resolve())
        elif root.is_dir():
            candidates.update(p.resolve() for p in root.rglob("*checkpoint*.jsonl") if ".git" not in p.parts)
    candidates.add(own_checkpoint.resolve())
    return [p for p in sorted(candidates) if "b1b2" not in p.name.lower().replace("_", "") and "b1_b2" not in p.name.lower()]


def safe_concat(a, b, keys, label):
    both = pd.concat([a, b], ignore_index=True)
    dup = both[both.duplicated(keys, keep=False)]
    if not dup.empty:
        for _, group in dup.groupby(keys, dropna=False):
            if len(group.drop_duplicates()) != 1:
                fail(f"Conflicting {label} row for an existing key")
        both = both.drop_duplicates(keys).reset_index(drop=True)
    return both


def make_output(core, old_frames, metas, fetched, need, evidence, expected_count, fp, digest, scope, output_root, aave_known):
    # Existing active accounts are verified from previous fully QA-passed A/B exports.
    active_keys = set(zip(old_frames["accounts"].snapshot_id, old_frames["accounts"].market_id, old_frames["accounts"].wallet_address))
    raw = []
    unknown_debt = set()
    for key, rec in fetched.items():
        if key in active_keys:
            continue
        clone = dict(rec)
        val = clone.get("aave_debt_usd")
        if not isinstance(val, (int, float)) or not math.isfinite(float(val)) or float(val) < 0:
            clone["aave_debt_usd"] = 0.0  # only for core schema; masked to NaN immediately below
            unknown_debt.add(key)
        raw.append(clone)
    p_new, a_new, config, _ = core.build_outputs(raw, metas)
    if unknown_debt and not a_new.empty:
        mask = pd.Series(list(zip(a_new.snapshot_id, a_new.market_id, a_new.wallet_address))).isin(unknown_debt).to_numpy()
        a_new.loc[mask, "aave_debt_usd"] = float("nan")
    pos = safe_concat(old_frames["positions"], p_new, ["snapshot_block", "market_id", "wallet_address", "token_address", "asset_role"], "positions")
    acc = safe_concat(old_frames["accounts"], a_new, ["snapshot_block", "market_id", "wallet_address"], "accounts")
    if acc.empty:
        overlap = old_frames["overlap"].iloc[0:0].copy()
    else:
        overlap = acc.groupby(["snapshot_id", "snapshot_block", "wallet_address"], as_index=False).agg(
            compound_markets_count=("market_id", "nunique"),
            compound_borrow_usd=("compound_borrow_usd", "sum"),
            compound_collateral_usd=("compound_collateral_usd", "sum"),
            compound_base_supply_usd=("compound_base_supply_usd", "sum"),
            any_compound_liquidatable=("compound_is_liquidatable", "any"),
            aave_debt_usd=("aave_debt_usd", "max"),
        )
    if len(set(zip(pos.snapshot_id, pos.market_id, pos.wallet_address)) - set(zip(acc.snapshot_id, acc.market_id, acc.wallet_address))):
        fail("Positions without corresponding account")
    # QA declares success for the requested scope, not legacy Aave-scoped QA if expanded panel is chosen.
    qa = {
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "chain_id": 1,
        "snapshot_blocks": {"A": A_BLOCK, "B": B_BLOCK},
        "shard": {"num_shards": 6, "shard_index": SHARD,
                  "assignment": "checkpoint_assignment_v1:" + digest,
                  "assigned_unique_wallets": len({w for _, _, w in need}),
                  "selection_hash": scope["selection_hash"]},
        "metadata_fingerprint": fp,
        "scope": scope["description"],
        "matches_original_A_B_Aave_snapshot_membership": bool(aave_known),
        "aave_debt_usd_missing_for_newly_fetched_queries": int(acc.aave_debt_usd.isna().sum()) if not acc.empty else 0,
        "expected_wallet_market_snapshot_queries": expected_count,
        "successful_queries": expected_count,
        "failed_queries": 0,
        "complete": True,
        "source_aligned_existing_active_query_keys": len(evidence),
        "checkpoint_successful_RPC_query_keys_including_old": len(fetched),
        "positions_rows": len(pos),
        "active_compound_market_accounts": len(acc),
        "unique_overlap_wallet_snapshot_pairs": len(overlap),
        "all_assigned_4312_wallets_tested_in_both_snapshots": scope["mode"] == "expanded_AB_crossproduct",
        "limitations": ["Derived aligned Parquet reused only for active Compound accounts",
                        "Original complete QA may include zero-balance RPC results not preserved in Parquet",
                        "No overwrite to legacy A/B or new A/B1/B2 datasets"],
    }
    output_root.parent.mkdir(parents=True, exist_ok=True)
    if output_root.exists():
        fail("Output already exists: " + str(output_root))
    import tempfile
    with tempfile.TemporaryDirectory(prefix="compound_shard4_AB_", dir=output_root.parent) as td:
        stage = Path(td)
        for df, name in ((pos,"n3_compound_positions_long.parquet"), (acc,"n3_compound_account_reference.parquet"), (config,"n3_compound_reserve_config.parquet"), (overlap,"n3_compound_overlap_wallets.parquet")):
            df.to_parquet(stage / name, index=False)
        (stage / "n3_compound_quality.json").write_text(json.dumps(qa, indent=2, ensure_ascii=False), encoding="utf8")
        stage.rename(output_root)
    return qa


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--repo", type=Path, default=Path.cwd())
    p.add_argument("--aligned-root", type=Path, help="Existing outputs/compound_AB_checkpoint_aligned_*_PARTIAL directory")
    p.add_argument("--extra-checkpoint-root", action="append", default=[], type=Path)
    p.add_argument("--aave-ab", type=Path, help="Historical original A/B Aave account_reference.parquet; SHA must match team plan")
    p.add_argument("--expanded-panel", action="store_true", help="Explicitly query all shard4 wallets at both A and B; not identical to old Aave-snapshot membership")
    p.add_argument("--max-new-queries", type=int, default=500, help="RPC query budget per execution; 0=all pending")
    p.add_argument("--batch-size", type=int, default=20)
    p.add_argument("--wallet-chunk", type=int, default=5)
    p.add_argument("--rpc-interval", type=float, default=0.25)
    p.add_argument("--execute", action="store_true", help="Connect to Ethereum archive RPC and append to NEW shard4 checkpoint")
    a = p.parse_args()
    repo = a.repo.expanduser().resolve()
    if a.max_new_queries < 0 or a.wallet_chunk < 1 or a.batch_size < 1 or not math.isfinite(a.rpc_interval) or a.rpc_interval < 0:
        fail("Invalid numeric limits")
    if bool(a.aave_ab) == bool(a.expanded_panel):
        fail("Choose exactly one: --aave-ab original_AB.parquet (strict) OR --expanded-panel (broader scope)")
    plan = json_read(repo / "config/n3_compound_team_assignment.json")
    if plan.get("version") != 1 or plan.get("num_shards") != 6:
        fail("Invalid team plan")
    owners = plan["wallet_shards"]
    if any(not ADDR.fullmatch(w) or type(i) is not int or i not in range(6) for w, i in owners.items()):
        fail("Invalid wallet_shards plan")
    assigned = sorted(w for w, owner in owners.items() if owner == SHARD)
    if not assigned:
        fail("No shard4 wallets assigned")
    digest = plan_hash(plan)
    core = load_core(repo)
    base, prior = frames_from_aligned(repo, a.aligned_root, digest, owners)
    markets = core.load_markets(repo / "config/n3_compound_markets.csv")
    market_ids = {m[MARKET_KEY] for m in markets}
    current_meta = json_read(repo / "data/processed/aave/snapshot_metadata.json")
    A = current_meta.get("snapshots", {}).get("A")
    if (not A or int(A["snapshot_block"]) != A_BLOCK or
            not A.get("block_hash") or not A.get("block_hash_verified_on_chain")):
        fail("Current N2 snapshot A is missing verified block hash, do not continue")
    snaps = {"A": A, "B": {"snapshot_block": B_BLOCK}}
    aave_known = a.aave_ab is not None
    if aave_known:
        path = a.aave_ab.expanduser().resolve()
        if not path.is_file():
            fail("Missing original A/B Aave reference " + str(path))
        selected, _ = core.pick_wallets(pd.read_parquet(path), snaps, 0, True)
        shash = hashlib.sha256(json.dumps(selected, sort_keys=True).encode("utf-8")).hexdigest()
        if shash != plan["selection_hash"]:
            fail("Original A/B Aave reference selection_hash does NOT match team plan")
        scoped = {sid: {w: debt for w, debt in selected[sid].items() if owners.get(w) == SHARD} for sid in ("A", "B")}
        if set(scoped["A"]) | set(scoped["B"]) != set(assigned):
            fail("A/B membership does not cover exactly the shard4 assigned wallets")
        scope = {"description": "original Aave-positive-debt A/B membership; checkpoint plan preserved",
                 "mode": "original_Aave_AB", "selection_hash": plan["selection_hash"]}
    else:
        scoped = {sid: {w: None for w in assigned} for sid in ("A", "B")}
        scope = {"description": "EXPANDED all-assigned-wallets X both A/B snapshots; NOT the original Aave-positive-debt snapshot filter",
                 "mode": "expanded_AB_crossproduct", "selection_hash": None}
    expected = [(sid, m["market_id"], w) for sid in ("A", "B") for m in markets for w in sorted(scoped[sid])]
    wanted = set(expected)
    old_active = set(zip(prior["accounts"].snapshot_id, prior["accounts"].market_id, prior["accounts"].wallet_address))
    if old_active - wanted:
        fail(f"{len(old_active - wanted)} previous active account records outside requested A/B scope")
    if any(key[1] not in market_ids for key in old_active):
        fail("Existing aligned Parquet includes markets not in config")
    raw_folder = repo / "data/raw/compound" / f"team_{digest[:12]}" / "shard_004_of_006"
    own_ck = raw_folder / "n3_overlap_checkpoint_AB_resume_v2.jsonl"
    checkpoint_paths = discover_checkpoints(repo, [r.expanduser().resolve() for r in a.extra_checkpoint_root], own_ck)
    found, evidence = checkpoint_iter(checkpoint_paths, plan["metadata_fingerprint"], wanted, market_ids)
    if aave_known:
        # Re-attach the verified Aave A/B debt for a previously collected RPC proof,
        # without changing or rewriting the source checkpoint file.
        found = {key: {**rec, "aave_debt_usd": float(scoped[key[0]][key[2]])}
                 for key, rec in found.items()}
    available = old_active | set(found)
    pending = [key for key in expected if key not in available]
    print(f"[TEAM PLAN] fixed shard4={len(assigned)} wallets; plan_sha256={digest[:12]}")
    print(f"[SCOPE] {scope['mode']} | snapshots A={A_BLOCK}, B={B_BLOCK} | markets={len(markets)}")
    print(f"[SOURCE ALIGNED] {base} | active successful query keys={len(old_active)}")
    print(f"[CHECKPOINT] {len(found)} verified complete RPC keys; source files with relevant records={len(evidence)}")
    print(f"[PROGRESS] {len(available)}/{len(expected)} queries proven | pending={len(pending)}")
    print(f"[CHECKPOINT NEW] {own_ck}")
    if not a.execute:
        print("[DRY RUN] No RPC called or file changed. Add --execute to collect missing A/B queries.")
        return
    load_dotenv(repo / ".env")
    rpc = os.getenv("ETHEREUM_ARCHIVE_RPC_URL") or os.getenv("ETH_RPC_URL") or os.getenv("ALCHEMY_ETHEREUM_RPC_URL")
    if not rpc:
        fail("Missing archive-capable Ethereum RPC in .env")
    rpc_session = core.PacedSession(a.rpc_interval)
    w3 = Web3(Web3.HTTPProvider(rpc, request_kwargs={"timeout": 75}, session=rpc_session))
    if int(w3.eth.chain_id) != 1:
        fail("Wrong Ethereum chain ID")
    for sid, blk in (("A", A_BLOCK), ("B", B_BLOCK)):
        block = w3.eth.get_block(blk)
        if sid == "A" and Web3.to_hex(block["hash"]).lower() != str(A["block_hash"]).lower():
            fail("Snapshot A hash mismatch")
        snaps[sid] = {
            "snapshot_block": blk,
            "block_hash": Web3.to_hex(block["hash"]),
            "snapshot_timestamp_utc": datetime.fromtimestamp(int(block["timestamp"]), timezone.utc).isoformat(),
        }
    metas = {}
    for sid in ("A", "B"):
        for m in markets:
            row, _ = core.metadata_for_market(w3, snaps[sid], m, sid)
            metas[(sid, m["market_id"])] = row
    fp = core.metadata_fingerprint(metas)
    if fp != plan["metadata_fingerprint"]:
        fail("Live A/B market fingerprint does not match ORIGINAL team plan. No writes made")
    if not pending:
        print("[NO RPC NEEDED] all expected keys already proven")
    budget = len(pending) if a.max_new_queries == 0 else min(a.max_new_queries, len(pending))
    # Group by (snapshot, market), bounded by wallet-chunk, and checkpoint after each wallet group.
    to_fetch = pending[:budget]
    raw_folder.mkdir(parents=True, exist_ok=True)
    failed = 0
    completed_this_run = 0
    with rpc_session as session, own_ck.open("a", encoding="utf-8") as sink:
        for sid in ("A", "B"):
            for market in markets:
                wallets = [w for s, mid, w in to_fetch if s == sid and mid == market["market_id"]]
                for ofs in range(0, len(wallets), a.wallet_chunk):
                    chunk = wallets[ofs:ofs + a.wallet_chunk]
                    try:
                        batch_result = core.fetch_accounts(session, rpc, metas[(sid, market["market_id"])], chunk, a.batch_size)
                    except Exception as ex:
                        print(f"[RETRY LATER] {sid}/{market['market_id']} batch={len(chunk)} error={type(ex).__name__}")
                        failed += len(chunk)
                        continue
                    for rec in batch_result:
                        if rec.get("status") != "ok":
                            failed += 1
                            continue
                        wallet = rec["wallet"].lower()
                        key = (sid, market["market_id"], wallet)
                        debt = scoped[sid][wallet]
                        event = {"fingerprint": fp, "snapshot_id": sid, "market_id": market["market_id"],
                                 "wallet": wallet, "status": "ok", "values": rec["values"],
                                 "aave_debt_usd": float(debt) if debt is not None else None,
                                 "scope": scope["mode"]}
                        sink.write(json.dumps(event, ensure_ascii=False) + "\n")
                        found[key] = event
                        completed_this_run += 1
                    sink.flush()
                    if completed_this_run and (completed_this_run % 50 < a.wallet_chunk or completed_this_run == budget):
                        print(f"[CHECKPOINT SAVED] new={completed_this_run}/{budget}; failed_batches={failed}", flush=True)
    covered = old_active | set(found)
    remaining = len(wanted - covered)
    print(f"[AFTER RUN] proven={len(covered)}/{len(wanted)}, remaining={remaining}, failed_this_run={failed}")
    if remaining:
        print("[RESUMABLE] Checkpoint preserved. Run SAME command again; existing RPC proofs will be reused.")
        return
    out = repo / "outputs" / f"compound_AB_shard004_{digest[:12]}_{scope['mode']}_COMPLETE"
    qa = make_output(core, prior, metas, found, wanted, old_active, len(wanted), fp, digest, scope, out, aave_known)
    print(f"[COMPLETE] {qa['successful_queries']}/{qa['expected_wallet_market_snapshot_queries']} | {out}")
    if not aave_known:
        print("[SCOPE NOTE] Full A/B panel is broader than the original Aave-positive-debt membership. Do not silently merge with original other-shard QA.")


if __name__ == "__main__":
    main()
