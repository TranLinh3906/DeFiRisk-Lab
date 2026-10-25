#!/usr/bin/env python3
"""Retry ONLY uncovered Compound queries in an existing shard."""
import argparse
import csv
import hashlib
import importlib.util
import json
import os
import time
from collections import Counter
from pathlib import Path

import requests
from dotenv import load_dotenv
from web3 import Web3

ROOT = Path(__file__).resolve().parents[1]
FETCH = ROOT / "src/data/fetch_compound_overlap.py"
SHARD = ROOT / "shard_004_of_006/shard_004_of_006"

EXPECTED = {"A": 26179841, "B": 26194188}

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--csv", type=Path,
        default=Path.home() / "Downloads/missing_queries.csv"
    )
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--max-new", type=int, default=0)
    parser.add_argument("--interval", type=float, default=0.4)
    args = parser.parse_args()

    if not FETCH.is_file() or not SHARD.is_dir():
        raise SystemExit("THIEU CODE HOAC SHARD 004")

    spec = importlib.util.spec_from_file_location("compound_fetch", FETCH)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)

    qa_path = SHARD / "n3_compound_quality.json"
    qa = json.loads(qa_path.read_text(encoding="utf-8"))
    if qa["snapshot_blocks"] != EXPECTED:
        raise SystemExit(
            "QA KHONG CON LA A/B CU. DUNG LAI DE TRANH TRUNG."
        )

    with args.csv.open(encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        required = {
            "snapshot_id", "snapshot_block", "market_id",
            "wallet_address", "assigned_shard", "covered"
        }
        if not required.issubset(reader.fieldnames or []):
            raise SystemExit("CSV SAI COT")
        rows = list(reader)

    markets = m.load_markets(
        ROOT / "config/n3_compound_markets.csv"
    )
    market_map = {x["market_id"]: x for x in markets}
    qa_markets = {
        x["market_id"]: x["comet_address"]
        for x in qa["markets"]
    }
    if qa_markets != {
        x["market_id"]: x["comet_address"] for x in markets
    }:
        raise SystemExit("THI TRUONG KHONG KHOP QA CU")

    needed = {}
    for row in rows:
        sid = row["snapshot_id"].strip()
        mid = row["market_id"].strip()
        wallet = m.valid_address(row["wallet_address"])

        if (
            sid not in EXPECTED
            or int(row["snapshot_block"]) != EXPECTED[sid]
            or row["assigned_shard"].strip() != "4"
            or row["covered"].strip().lower() not in ("false", "0")
            or mid not in market_map
        ):
            raise SystemExit("CSV CO DONG KHONG THUOC MUC TIEU")

        key = (sid, mid, wallet)
        if key in needed:
            raise SystemExit("CSV CO KHOA TRUNG")
        needed[key] = row

    counts = Counter((k[0], k[1]) for k in needed)
    print("===== CAC LUOT THIEU =====")
    for key, count in sorted(counts.items()):
        print(key, count)
    print("TONG:", len(needed))

    if args.max_new < 0 or args.interval < 0:
        raise SystemExit("THAM SO KHONG HOP LE")

    if not args.execute:
        print("[DRY RUN] Khong goi RPC, khong ghi file")
        return

    load_dotenv(ROOT / ".env")
    rpc = (
        os.getenv("ETHEREUM_ARCHIVE_RPC_URL")
        or os.getenv("ETH_RPC_URL")
        or os.getenv("ALCHEMY_ETHEREUM_RPC_URL")
    )
    if not rpc:
        raise SystemExit("THIEU ETHEREUM ARCHIVE RPC")

    # Original collector sends JSON arrays even with batch-size=1.
    # This adapter sends one JSON-RPC object per request.
    def rpc_single(session, url, calls, batch_size, retries=3):
        output = {}
        for call in calls:
            for attempt in range(retries):
                try:
                    response = session.post(
                        url, json=call, timeout=60
                    )
                    response.raise_for_status()
                    obj = response.json()
                    if isinstance(obj, list) and len(obj) == 1:
                        obj = obj[0]
                    if not isinstance(obj, dict):
                        raise RuntimeError("Invalid RPC response")
                    if obj.get("id") != call["id"]:
                        raise RuntimeError("RPC ID mismatch")
                    if "error" in obj:
                        code = obj["error"].get("code")
                        raise RuntimeError(f"RPC error code {code}")
                    if not isinstance(obj.get("result"), str):
                        raise RuntimeError("RPC result missing")
                    output[call["id"]] = obj
                    break
                except Exception as ex:
                    if attempt == retries - 1:
                        raise RuntimeError(
                            "Single RPC failed: "
                            + type(ex).__name__
                        ) from None
                    time.sleep(2 ** attempt)
        return output

    m.rpc_batch = rpc_single

    csv_hash = hashlib.sha256(args.csv.read_bytes()).hexdigest()

    with m.PacedSession(args.interval) as session:
        w3 = Web3(Web3.HTTPProvider(
            rpc,
            request_kwargs={"timeout": 60},
            session=session
        ))
        if int(w3.eth.chain_id) != 1:
            raise SystemExit("RPC KHONG PHAI ETHEREUM MAINNET")

        metas = {}
        try:
            for sid, block in EXPECTED.items():
                header = w3.eth.get_block(block)
                if int(header.number) != block:
                    raise RuntimeError("Invalid historical block")

                for mid in market_map:
                    if (sid, mid) not in counts:
                        continue
                    info, _ = m.metadata_for_market(
                        w3, {"snapshot_block": block},
                        market_map[mid], sid
                    )
                    metas[(sid, mid)] = info
        except Exception as ex:
            raise SystemExit(
                "[STOP] Archive RPC metadata failed: "
                + type(ex).__name__
            )

        fingerprint = hashlib.sha256(
            json.dumps({
                "csv_sha256": csv_hash,
                "snapshot_blocks": EXPECTED,
                "market_fingerprint":
                    m.metadata_fingerprint(metas),
            }, sort_keys=True).encode()
        ).hexdigest()

        checkpoint = (
            SHARD / "n3_overlap_missing_AB_checkpoint.jsonl"
        )
        done = m.checkpoint_load(checkpoint, fingerprint)

        saved = 0
        consecutive_errors = 0

        with checkpoint.open("a", encoding="utf-8") as f:
            for sid, mid, wallet in sorted(needed):
                key = (sid, mid, wallet)
                if key in done:
                    continue

                if args.max_new and saved >= args.max_new:
                    break

                try:
                    result = m.fetch_accounts(
                        session, rpc, metas[(sid, mid)],
                        [wallet], batch_size=1
                    )[0]

                    if result["status"] != "ok":
                        raise RuntimeError("Account RPC error")

                except Exception as ex:
                    consecutive_errors += 1
                    print(
                        "[ERROR]", sid, mid,
                        type(ex).__name__,
                        "consecutive:", consecutive_errors
                    )
                    if consecutive_errors >= 3:
                        print("[STOP] 3 failures in a row")
                        break
                    continue

                record = {
                    "fingerprint": fingerprint,
                    "snapshot_id": sid,
                    "market_id": mid,
                    "wallet": wallet,
                    "status": "ok",
                    "values": result["values"],
                }
                f.write(json.dumps(record) + "\n")
                f.flush()

                done[key] = record
                saved += 1
                consecutive_errors = 0

                if saved % 25 == 0:
                    print(
                        "[PROGRESS]",
                        len(done), "/", len(needed),
                        "saved this run:", saved,
                        flush=True
                    )

    print("\n===== KET QUA =====")
    print("Checkpoint:", checkpoint)
    print("Da luu:", len(set(done) & set(needed)))
    print("Can co:", len(needed))
    print("Con thieu:", len(set(needed) - set(done)))
    if set(needed) <= set(done):
        print("[COMPLETE] Du 100% luot truy van A/B con thieu")
        print("CHO QA VA HOP NHAT; CHUA SUA PARQUET CU")
    else:
        print("[PARTIAL] Chay lai cung lenh de tiep tuc")

if __name__ == "__main__":
    main()
