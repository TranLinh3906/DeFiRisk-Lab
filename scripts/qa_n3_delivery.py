#!/usr/bin/env python3
"""Read-only QA for N3 day 1-5, with optional guarded Git merge-JSON repair.
Run from the project root. This script NEVER touches N1's six contract tables.
"""
from __future__ import annotations
import argparse
import json
import math
import numpy as np
import shutil
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

JSON_FILES = [
    "data/raw/market/n3_defillama_price_cache.json",
    "outputs/tables/n3_etherscan_quality.json",
    "outputs/tables/n3_price_quality.json",
    "outputs/tables/n3_usd_pricing_quality.json",
]
DOC_CONFLICT_FILES = ["docs/HUONG_DAN_CHAY_N3_17_10.md"]
FACT_COLS = ["tx_hash", "log_index", "event_block", "event_timestamp", "protocol_id",
             "wallet_address", "liquidator_address", "collateral_token_address", "debt_token_address",
             "debt_repaid_usd", "collateral_seized_usd"]


def select_merge(text: str, prefer: str):
    lines = text.splitlines(keepends=True)
    result, state, conflicts = [], "normal", 0
    for line in lines:
        if line.startswith("<<<<<<< "):
            if state != "normal":
                raise ValueError("Nested/confusing conflict markers")
            state, conflicts = "ours", conflicts + 1
        elif line.rstrip("\r\n") == "=======" and state == "ours":
            state = "theirs"
        elif line.startswith(">>>>>>> ") and state == "theirs":
            state = "normal"
        elif state == "normal" or state == prefer:
            result.append(line)
    if state != "normal":
        raise ValueError("Incomplete Git conflict markers; refuse to repair")
    return "".join(result), conflicts


def json_check_and_repair(root: Path, apply: bool, prefer: str):
    report = []
    for rel in JSON_FILES:
        path = root / rel
        if not path.exists():
            report.append((rel, "MISSING"))
            continue
        text = path.read_text(encoding="utf-8")
        try:
            json.loads(text)
            report.append((rel, "VALID"))
            continue
        except json.JSONDecodeError:
            pass
        try:
            fixed, count = select_merge(text, prefer)
            if count == 0:
                report.append((rel, "INVALID_NOT_MERGE_MARKERS"))
                continue
            obj = json.loads(fixed)
            if not isinstance(obj, dict):
                raise ValueError("Expected JSON object")
            if not apply:
                report.append((rel, f"REPAIRABLE({count}_CONFLICTS); NO_WRITE"))
                continue
            stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
            backup = path.with_name(path.name + ".bak_mergefix_" + stamp)
            shutil.copy2(path, backup)
            temp = path.with_name(path.name + ".mergefix_tmp")
            try:
                temp.write_text(fixed, encoding="utf-8")
                json.loads(temp.read_text(encoding="utf-8"))
                temp.replace(path)
            finally:
                temp.unlink(missing_ok=True)
            report.append((rel, f"REPAIRED({count}_CONFLICTS); BACKUP={backup.name}"))
        except Exception as ex:
            report.append((rel, f"INVALID_UNREPAIRABLE({type(ex).__name__})"))
    return report


def docs_check_and_repair(root: Path, apply: bool, prefer: str):
    report = []
    for rel in DOC_CONFLICT_FILES:
        path = root / rel
        if not path.exists():
            report.append((rel, "MISSING"))
            continue
        text = path.read_text(encoding="utf-8")
        try:
            fixed, count = select_merge(text, prefer)
            if count == 0:
                report.append((rel, "VALID"))
                continue
            if not apply:
                report.append((rel, f"REPAIRABLE({count}_CONFLICTS); NO_WRITE"))
                continue
            stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
            backup = path.with_name(path.name + ".bak_mergefix_" + stamp)
            shutil.copy2(path, backup)
            path.write_text(fixed, encoding="utf-8")
            report.append((rel, f"REPAIRED({count}_CONFLICTS); BACKUP={backup.name}"))
        except Exception as exc:
            report.append((rel, f"INVALID_UNREPAIRABLE({type(exc).__name__})"))
    return report


def main():
    cli = argparse.ArgumentParser()
    cli.add_argument("--project", type=Path, default=Path.cwd())
    cli.add_argument("--repair-json", "--repair-conflicts", action="store_true",
                     help="Repair 4 merge-damaged JSON plus docs; backup original files first")
    cli.add_argument("--prefer", choices=("ours", "theirs"), default="ours")
    cli.add_argument("--tolerance-usd", type=float, default=1.0)
    args = cli.parse_args()
    root = args.project.resolve()
    passed, failed, warnings = [], [], []
    def check(condition: bool, title: str, detail: str = ""):
        (passed if condition else failed).append(title + (": " + detail if detail else ""))

    jrows = json_check_and_repair(root, args.repair_json, args.prefer)
    drows = docs_check_and_repair(root, args.repair_json, args.prefer)
    for path, status in jrows:
        print("JSON", path, "=>", status)
        check(status in {"VALID"} or status.startswith("REPAIRED"), "JSON: " + path, status)
    for path, status in drows:
        print("DOC", path, "=>", status)
        check(status == "VALID" or status.startswith("REPAIRED"), "DOC: " + path, status)

    def load(rel: str):
        path = root / rel
        if not path.is_file():
            failed.append("File missing: " + rel)
            return None
        try:
            return pd.read_parquet(path)
        except Exception as exc:
            failed.append("Cannot read Parquet: " + rel + " (" + type(exc).__name__ + ")")
            return None

    fact = load("data/staging/n3_liquidations_for_fact.parquet")
    if fact is not None:
        check(set(fact.columns) == set(FACT_COLS), "Muc2 fact contract columns exactly 11")
        check(len(fact) > 0, "Muc2 non-empty events", str(len(fact)))
        check(not fact.duplicated(["tx_hash", "log_index"]).any(), "Muc2 event PK unique")
        check(not fact[FACT_COLS].isna().any().any(), "Muc2 required fields nonnull")

    prices = load("data/staging/n3_price_timeseries.parquet")
    if prices is not None:
        if "price_type" not in prices.columns:
            failed.append("Muc3 prices lack price_type")
        else:
            actual = set(prices.price_type.dropna().unique())
            for t in ("market_reference", "market_ratio", "aave_oracle", "lido_contract_conversion"):
                check(t in actual, f"Muc3 present price type {t}")
            if "snapshot_block" in prices:
                oracles = prices[prices.price_type.eq("aave_oracle")]
                counts = oracles.groupby("snapshot_block").size().to_dict()
                print("MUC3 ORACLE COUNTS:", counts)

    dex = load("data/staging/n3_dex_liquidity_A_B1_B2.parquet")
    if dex is not None:
        needed = {"snapshot_id", "snapshot_block", "dex", "token_in", "token_out",
                  "pool_address", "dex_depth_2pct_usd", "status", "observed_impact_pct",
                  "depth_lower_bound_usd", "depth_upper_bound_usd", "depth_bracket_width_usd",
                  "impact_below_depth_pct", "numerical_tolerance_usd"}
        check(needed <= set(dex.columns), "Muc4 certified 2% depth columns", "missing: "+str(sorted(needed - set(dex.columns))))
        if {"snapshot_id", "dex", "token_in", "token_out"} <= set(dex):
            print("\nMUC4 ALL ROWS:")
            cols = [c for c in ("snapshot_id", "dex", "token_in", "token_out", "status",
                   "dex_depth_2pct_usd", "depth_lower_bound_usd", "depth_bracket_width_usd",
                   "observed_impact_pct") if c in dex.columns]
            print(dex[cols].to_string(index=False))
            check(len(dex) == 12, "Muc4 exactly 12 snapshot/direction combinations", str(len(dex)))
            cfg = root / "config/n3_dex_pools.csv"
            if cfg.exists():
                jobs = pd.read_csv(cfg).fillna("")
                expected_pairs = {(tag, str(r.dex).lower(), str(r.token_in).upper(), str(r.token_out).upper())
                                  for tag in ("A", "B1", "B2") for r in jobs.itertuples(index=False)}
                actual_pairs = set(zip(dex.snapshot_id.astype(str), dex.dex.astype(str),
                                       dex.token_in.astype(str), dex.token_out.astype(str)))
                check(actual_pairs == expected_pairs, "Muc4 exact pool directions from config")
            check(not dex.duplicated(["snapshot_id", "dex", "token_in", "token_out"]).any(),
                  "Muc4 directions and snapshots unique")
            check(set(dex.snapshot_id.astype(str)) == {"A", "B1", "B2"}, "Muc4 all A/B1/B2")
        check(dex.status.eq("verified_2pct_bracket").all(), "Muc4 verified 12/12", str(dex.status.value_counts().to_dict()))
        if needed <= set(dex.columns):
            numeric = ["dex_depth_2pct_usd", "depth_lower_bound_usd", "depth_upper_bound_usd",
                       "depth_bracket_width_usd", "observed_impact_pct", "impact_below_depth_pct"]
            vals = dex[numeric].apply(pd.to_numeric, errors="coerce")
            check(vals.notna().all().all() and np.isfinite(vals.to_numpy(dtype=float)).all(), "Muc4 all numeric values finite")
            bounds = (vals.depth_lower_bound_usd < vals.dex_depth_2pct_usd) & (
                (vals.depth_upper_bound_usd-vals.dex_depth_2pct_usd).abs() < 1e-6)
            check(bounds.all(), "Muc4 USD depth within verified bracket")
            checks = (vals.impact_below_depth_pct < 2.0) & (vals.observed_impact_pct >= 2.0)
            check(checks.all(), "Muc4 threshold crossed with real quotes on both sides")
            check((vals.depth_bracket_width_usd <= args.tolerance_usd + 1e-6).all(),
                  "Muc4 price-impact depth precision <= $" + str(args.tolerance_usd))
            check((vals.dex_depth_2pct_usd > 0).all(), "Muc4 positive USD depths")
            check(dex.pool_address.notna().all(), "Muc4 verified pool contract addresses")

        meta_file = root / "data/processed/aave/snapshot_metadata.json"
        if meta_file.exists() and {"snapshot_id", "snapshot_block"} <= set(dex):
            meta = json.loads(meta_file.read_text())
            snapshots = meta.get("snapshots", {})
            check(set(snapshots) == {"A", "B1", "B2"},
                  "Muc4 metadata exactly A/B1/B2")
            for tag, block in {"B1": 20458637, "B2": 23543615}.items():
                check(int(snapshots.get(tag, {}).get("snapshot_block", 0)) == block,
                      f"Muc4 {tag} approved block", str(block))
            check(all(s.get("block_hash_verified_on_chain") is True
                      for s in snapshots.values()),
                  "Muc4 metadata block hashes verified on chain")
            expect = {k: int(v["snapshot_block"]) for k,v in snapshots.items()}
            check(all(expect.get(str(r.snapshot_id)) == int(r.snapshot_block)
                      for r in dex.itertuples(index=False)), "Muc4 same historical block N2", str(expect))

    for rel in ["src/data/fetch_liquidations.py", "src/data/fetch_prices.py",
                "src/data/price_liquidations_usd.py", "src/analysis/descriptive.py"]:
        check((root / rel).is_file(), "Source script: " + rel)
    for rel in ["outputs/tables/n3_liquidations_daily.csv", "outputs/tables/n3_liquidations_hourly.csv"]:
        check((root / rel).is_file(), "Muc5 result table: " + rel)
    for rel in ["outputs/figures/n3_liquidation_eth_timeline.png",
                "outputs/figures/n3_steth_eth_depeg_timeline.png"]:
        check((root / rel).is_file(), "Muc5 result figure: " + rel)

    print("\n=== PASSED", len(passed), "===")
    for x in passed:print("[PASS]", x)
    print("\n=== FAILED", len(failed), "===")
    for x in failed:print("[FAIL]", x)
    print("\nIMPORTANT: This is file/internal QA, not independent multi-provider source verification.")
    raise SystemExit(0 if not failed else 1)

if __name__ == "__main__":
    main()
