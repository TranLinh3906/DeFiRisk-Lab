"""
write_snapshot_metadata.py - Tao data/processed/aave/snapshot_metadata.json.

Chay tu thu muc goc repo (sau khi da co data/raw/aave/state_check.json):
    python src/data/write_snapshot_metadata.py

File nay khong goi RPC va khong chua RPC URL/API key.
CHUA duoc chay thu o phia nguoi viet.
"""
import json
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path.cwd()
STATE = ROOT / "data/raw/aave/state_check.json"
OUT = ROOT / "data/processed/aave/snapshot_metadata.json"

# Thong tin da chot o buoc 1 (muc tieu UTC chi de kiem toan; timestamp thuc la cua block)
CHOSEN = {
    "A": {
        "target_utc": "2026-10-13T00:00:00+00:00",
        "block_hash": "0x40c731f68bcd8152ad12433f4be57783e731fcf1546575ab51aa894054244ac3",
        "role": "calibration / counterfactual stress test",
    },
    "B": {
        "target_utc": "2026-10-15T00:00:00+00:00",
        "block_hash": "0xf12bcb7db5631cc69f0aaa392df0855f2a2ff3c18a9b9bcbb3a25981cac9daed",
        "role": "out-of-sample validation",
    },
}


def main():
    state = json.loads(STATE.read_text(encoding="utf-8"))
    snapshots = {}
    for label, info in CHOSEN.items():
        s = state["snapshots"][label]
        ts = datetime.fromtimestamp(s["block_timestamp_utc"], tz=timezone.utc)
        snapshots[label] = {
            "snapshot_id": label,
            "snapshot_block": s["block"],
            "snapshot_timestamp_utc": ts.isoformat(),
            "target_timestamp_utc": info["target_utc"],
            "block_hash": info["block_hash"],
            "block_hash_verified_on_chain": s["checks"].get("block_hash_match"),
            "role": info["role"],
            "aave_oracle": s["aave_oracle"],
            "pool_data_provider": s["pool_data_provider"],
            "addresses_provider": s["addresses_provider"],
            "n_reserves": s["checks"]["n_reserves"],
        }

    meta = {
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "chain_id": 1,
        "protocol_id": "aave_v3_ethereum",  # doi neu data_contract.md dung ten khac
        "pool_address": state["pool"].lower(),
        "rpc_provider": "Alchemy (Ethereum mainnet); URL khong luu trong repo",
        "snapshots": snapshots,
        "validation_window_rule": (
            "Snapshot B: chi dung LiquidationCall co event_block > snapshot_block B "
            "va event_timestamp < 2026-10-16T00:00:00Z."
        ),
        "status": {
            "archive_eth_call_verified": True,
            "oracle_prices_exported": False,
            "reserve_config_exported": False,
            "positions_exported": False,
            "hf_validated": False,
        },
        "limitations": [
            "Cap nhat cac muc 'status' va 'limitations' khi hoan thanh tung buoc.",
            "Chua kiem dinh HF toan mau; khong ghi la toan bo Aave neu chi lay mot phan nguoi vay.",
        ],
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Da luu {OUT}")


if __name__ == "__main__":
    main()