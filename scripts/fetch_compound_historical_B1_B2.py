#!/usr/bin/env python3
"""Fetch Compound B1/B2 into a new output tree, preserving all legacy A/B files."""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import math
import os
import shutil
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
from dotenv import load_dotenv
from web3 import Web3

ROOT = Path(__file__).resolve().parents[1]
FETCH_PATH = ROOT / "src/data/fetch_compound_overlap.py"
spec = importlib.util.spec_from_file_location("fetch_compound_overlap", FETCH_PATH)
fetch = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = fetch
spec.loader.exec_module(fetch)

EXPECTED = {"B1": 20458637, "B2": 23543615}


def digest(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def parse_args():
    p = argparse.ArgumentParser(description="Fetch historical Compound snapshots B1/B2 to a new, isolated output directory")
    p.add_argument("--aave", type=Path, default=fetch.DEFAULT_AAVE)
    p.add_argument("--snapshot-metadata", type=Path, default=fetch.DEFAULT_META)
    p.add_argument("--markets", type=Path, default=fetch.DEFAULT_MARKETS)
    p.add_argument("--assignment-file", type=Path, default=ROOT / "config/n3_compound_team_assignment.json")
    p.add_argument("--output-root", type=Path, required=True, help="New output directory under outputs/; never the legacy shard directory")
    p.add_argument("--num-shards", type=int, default=6)
    p.add_argument("--shard-index", type=int, required=True)
    p.add_argument("--max-wallets", type=int, default=0, help="0 selects all eligible Aave wallets")
    p.add_argument("--include-aave-zero-debt", action="store_true")
    p.add_argument("--batch-size", type=int, default=10)
    p.add_argument("--wallet-chunk", type=int, default=1)
    p.add_argument("--rpc-interval", type=float, default=0.2)
    p.add_argument("--dry-run", action="store_true")
    return p.parse_args()


def main():
    args = parse_args()
    if args.num_shards < 1 or not 0 <= args.shard_index < args.num_shards:
        raise SystemExit("Require num-shards >= 1 and 0 <= shard-index < num-shards")
    if args.batch_size < 1 or args.wallet_chunk < 1 or args.max_wallets < 0:
        raise SystemExit("batch-size/wallet-chunk must be positive and max-wallets nonnegative")
    if not math.isfinite(args.rpc_interval) or args.rpc_interval < 0:
        raise SystemExit("rpc-interval must be finite and nonnegative")
    if args.num_shards != 6:
        raise SystemExit("The shared team assignment is defined for exactly 6 shards")
    out_root = args.output_root.resolve()
    outputs_root = (ROOT / "outputs").resolve()
    if not out_root.is_relative_to(outputs_root) or out_root == outputs_root:
        raise SystemExit(f"--output-root must be a new child directory of {outputs_root}")
    shard_name = f"shard_{args.shard_index:03d}_of_{args.num_shards:03d}"
    final_dir = out_root / shard_name
    checkpoint = out_root / "_checkpoints" / shard_name / "n3_overlap_checkpoint_B1_B2.jsonl"
    if final_dir.exists():
        raise SystemExit(f"Refusing to overwrite existing B1/B2 output: {final_dir}")
    for path in (args.aave, args.snapshot_metadata, args.markets, args.assignment_file):
        if not path.is_file():
            raise SystemExit(f"Missing input file: {path}")

    all_snaps = fetch.load_snapshots(args.snapshot_metadata)
    snaps = {sid: all_snaps[sid] for sid in ("B1", "B2")}
    markets = fetch.load_markets(args.markets)
    all_selected, selection = fetch.pick_wallets(pd.read_parquet(args.aave), all_snaps, args.max_wallets,
                                                 not args.include_aave_zero_debt)
    selected = {sid: all_selected[sid] for sid in ("B1", "B2")}
    if any(not selected[sid] for sid in EXPECTED):
        raise SystemExit("No eligible Aave wallets in B1 or B2; check the N2 input")

    plan = json.loads(args.assignment_file.read_text(encoding="utf-8"))
    if plan.get("version") != 1 or plan.get("num_shards") != args.num_shards:
        raise SystemExit("Assignment file version/num_shards is incompatible")
    owners = plan.get("wallet_shards", {})
    old_owners = {w: int(s) for w, s in owners.items()}
    all_wallets = sorted({w for per_snapshot in selected.values() for w in per_snapshot})
    # Keep every wallet already assigned in the shared plan on its existing owner.
    # New B1/B2-only wallets use a stable hash partition, independent of file order.
    owner_by_wallet = {
        wallet: old_owners.get(wallet, int.from_bytes(hashlib.sha256(wallet.encode()).digest()[:8], "big") % args.num_shards)
        for wallet in all_wallets
    }
    selected_hash = digest(selected)
    plan_hash = digest(plan)
    shard_selected = {
        sid: {w: debt for w, debt in pairs.items() if owner_by_wallet[w] == args.shard_index}
        for sid, pairs in selected.items()
    }
    global_queries = sum(map(len, selected.values())) * len(markets)
    shard_queries = sum(map(len, shard_selected.values())) * len(markets)
    print("[SCOPE] snapshots=B1/B2 only; blocks", EXPECTED)
    print("[SHARD]", args.shard_index, "/", args.num_shards, "wallets", len({w for s in shard_selected.values() for w in s}), "queries", shard_queries)
    print("[GLOBAL] selected wallets", len(all_wallets), "queries", global_queries, "markets", [m["market_id"] for m in markets])
    print("[OUTPUT]", final_dir)
    if args.dry_run:
        print("[DRY-RUN] No RPC calls, checkpoint writes, or output writes were made.")
        return

    out_root.mkdir(parents=True, exist_ok=True)
    checkpoint.parent.mkdir(parents=True, exist_ok=True)
    # Support the repository's common local secret-file locations. Existing
    # process environment variables keep precedence; secret values are never logged.
    for env_path in (ROOT / ".env", ROOT / "config/.env", ROOT / "N3_Compound_Q3_Overlap_VSCode/.env"):
        if env_path.is_file():
            load_dotenv(env_path, override=False)
    rpc = os.getenv("ETHEREUM_ARCHIVE_RPC_URL") or os.getenv("ETH_RPC_URL") or os.getenv("ALCHEMY_ETHEREUM_RPC_URL")
    if not rpc:
        raise SystemExit("Missing ETHEREUM_ARCHIVE_RPC_URL (checked project .env, config/.env, and N3_Compound_Q3_Overlap_VSCode/.env)")
    session = fetch.PacedSession(args.rpc_interval)
    w3 = Web3(Web3.HTTPProvider(rpc, request_kwargs={"timeout": 75}, session=session))
    if int(w3.eth.chain_id) != 1:
        raise SystemExit("RPC is not Ethereum mainnet (chain_id != 1)")
    for sid, snap in snaps.items():
        block = w3.eth.get_block(int(snap["snapshot_block"]))
        if snap.get("block_hash") and Web3.to_hex(block["hash"]).lower() != str(snap["block_hash"]).lower():
            raise RuntimeError("Snapshot block hash mismatch: " + sid)
        if abs(int(block["timestamp"]) - pd.Timestamp(snap["snapshot_timestamp_utc"]).timestamp()) > 300:
            raise RuntimeError("Snapshot time drift > 5min: " + sid)
    metas = {}
    for sid, snap in snaps.items():
        for market in markets:
            meta, _ = fetch.metadata_for_market(w3, snap, market, sid)
            metas[(sid, market["market_id"])] = meta
            print(f"[MARKET] {sid} {market['market_id']} assets={len(meta['assets'])} base={meta['base_token_symbol']}")
    meta_fp = fetch.metadata_fingerprint(metas)
    fp = digest({"metadata_fingerprint": meta_fp, "selection_hash": selected_hash,
                 "assignment_hash": plan_hash, "snapshot_blocks": EXPECTED, "markets": markets,
                 "shard_index": args.shard_index, "num_shards": args.num_shards})
    cache = fetch.checkpoint_load(checkpoint, fp)
    failures = []
    resumed = len(cache)
    started = time.monotonic()
    last_progress = started

    def report_progress():
        elapsed = time.monotonic() - started
        completed = len(cache)
        rate = (completed - resumed) / elapsed if elapsed > 0 else 0
        eta = f"{(shard_queries - completed) / rate / 60:.1f} min" if rate > 0 else "unknown"
        print(f"[PROGRESS] {completed}/{shard_queries} ({100 * completed / shard_queries:.1f}%) "
              f"| {rate * 60:.1f} queries/min | ETA {eta} | failed={len(failures)} "
              f"| elapsed={elapsed / 60:.1f} min", flush=True)

    print(f"[RESUME] cached={resumed}/{shard_queries}; progress every ~15s after an RPC chunk; "
          "queries count wallet-market-snapshot records, each requiring multiple eth_call calls", flush=True)
    with session, checkpoint.open("a", encoding="utf-8") as ck:
        for sid in snaps:
            for market in markets:
                mid = metas[(sid, market["market_id"])]
                wallets = list(shard_selected[sid])
                pending = [w for w in wallets if (sid, market["market_id"], w) not in cache]
                print(f"[FETCH] {sid} {market['market_id']}: selected={len(wallets)} cached={len(wallets)-len(pending)} remaining={len(pending)}", flush=True)
                for begin in range(0, len(pending), args.wallet_chunk):
                    batch = pending[begin:begin + args.wallet_chunk]
                    try:
                        got = fetch.fetch_accounts(session, rpc, mid, batch, args.batch_size)
                    except Exception as exc:
                        failures.extend({"snapshot_id": sid, "market_id": market["market_id"], "wallet": w,
                                         "error": type(exc).__name__} for w in batch)
                        print("[ERROR] RPC batch failed; checkpoint preserved; rerun same command to resume", flush=True)
                        got = []
                    for obj in got:
                        if obj["status"] != "ok":
                            failures.append({"snapshot_id": sid, "market_id": market["market_id"],
                                             "wallet": obj["wallet"], "error": "RPC/ABI call error"})
                            continue
                        rec = {"fingerprint": fp, "snapshot_id": sid, "market_id": market["market_id"],
                               "wallet": obj["wallet"], "status": "ok", "values": obj["values"],
                               "aave_debt_usd": float(shard_selected[sid][obj["wallet"]])}
                        ck.write(json.dumps(rec, ensure_ascii=False) + "\n")
                        cache[(sid, market["market_id"], obj["wallet"])] = rec
                    ck.flush()
                    if time.monotonic() - last_progress >= 15:
                        report_progress()
                        last_progress = time.monotonic()
                report_progress()
    succeeded = sum((sid, market["market_id"], wallet) in cache
                    for sid in snaps for market in markets for wallet in shard_selected[sid])
    if succeeded != shard_queries or failures:
        print(f"[INCOMPLETE] {succeeded}/{shard_queries}; checkpoint preserved; rerun same command")
        raise SystemExit(2)

    records = [cache[(sid, market["market_id"], wallet)]
               for sid in snaps for market in markets for wallet in shard_selected[sid]]
    pos, acc, cfg, overlap = fetch.build_outputs(records, metas)
    qa = {"created_utc": datetime.now(timezone.utc).isoformat(),
          "scope": "B1/B2-only isolated historical Compound III shard; legacy A/B outputs are not read or modified",
          "chain_id": 1, "snapshot_blocks": EXPECTED, "metadata_fingerprint": meta_fp,
          "shard": {"num_shards": args.num_shards, "shard_index": args.shard_index,
                    "selection_hash": selected_hash, "assignment_hash": plan_hash,
                    "assignment": "shared_existing_owner_plus_sha256_new_wallets_v1",
                    "global_expected_queries": global_queries, "expected_queries": shard_queries},
          "input_aave_path": str(args.aave), "markets": markets, "selection": selection,
          "expected_wallet_market_snapshot_queries": shard_queries, "successful_queries": succeeded,
          "failed_queries": 0, "complete": True, "failures_preview": [],
          "method": "historical eth_call via Comet proxy; getPrice at snapshot; all supported collateral assets checked",
          "limitations": ["Aave-targeted wallet sample, not a full Compound census", "B1/B2 output only"]}
    qa.update({"positions_rows": len(pos), "active_compound_market_accounts": len(acc),
               "unique_overlap_wallet_snapshot_pairs": len(overlap),
               "any_borrow_market_accounts": int((acc.compound_borrow_usd > 0).sum()) if not acc.empty else 0,
               "outcome": "pass_complete_for_selected_Aave_wallets"})
    frames = {"n3_compound_positions_long.parquet": pos,
              "n3_compound_account_reference.parquet": acc,
              "n3_compound_reserve_config.parquet": cfg,
              "n3_compound_overlap_wallets.parquet": overlap}
    stage = Path(tempfile.mkdtemp(prefix=f".{shard_name}_stage_", dir=out_root))
    try:
        for name, frame in frames.items():
            frame.to_parquet(stage / name, index=False)
        (stage / "n3_compound_quality.json").write_text(json.dumps(qa, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
        fetch.validate_compound_extended(stage, EXPECTED)
        if final_dir.exists():
            raise FileExistsError(f"Refusing to overwrite existing B1/B2 output: {final_dir}")
        os.replace(stage, final_dir)
    finally:
        if stage.exists():
            shutil.rmtree(stage)
    print("[QA] COMPLETE", succeeded, "/", shard_queries, "| published", final_dir)


if __name__ == "__main__":
    main()
