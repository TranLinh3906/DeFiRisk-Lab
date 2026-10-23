"""
find_snapshot_blocks.py - Tim block chinh xac cho Snapshot theo timestamp UTC va dien vao config/snapshots.json.

Chay tu thu muc goc repo:
    python src/data/find_snapshot_blocks.py

Quy tac: block = block CUOI CUNG co timestamp <= target_utc (timestamp thuc cua block se la timestamp snapshot).
Snapshot da co block (vd A) chi duoc XAC MINH (hash, thu tu thoi gian), khong bi ghi de.
Voi moi snapshot cung kiem tra: Pool co bytecode va goi duoc eth_call (ADDRESSES_PROVIDER) o block do
-> chung minh RPC doc duoc state o thoi diem do (can cho B1 nam o nam 2024).

Khong luu RPC URL. CHUA duoc chay thu o phia nguoi viet (chi kiem tra cu phap).
"""
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from web3 import Web3

try:
    from dotenv import load_dotenv
    load_dotenv(Path.cwd() / ".env", override=True)
except ImportError:
    pass

ROOT = Path.cwd()
CFG = ROOT / "config/snapshots.json"
POOL = Web3.to_checksum_address("0x87870bca3f3fd6335c3f4ce8392d69350b4fa4e2")
SEL_PROVIDER = Web3.keccak(text="ADDRESSES_PROVIDER()")[:4]


def ts_of(w3, n):
    return int(w3.eth.get_block(n)["timestamp"])


def last_block_at_or_before(w3, target_ts):
    lo, hi = 1, w3.eth.block_number
    if ts_of(w3, hi) <= target_ts:
        raise ValueError("Muc tieu o tuong lai so voi block moi nhat")
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if ts_of(w3, mid) <= target_ts:
            lo = mid
        else:
            hi = mid - 1
    return lo


def iso(ts):
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def main():
    url = os.getenv("ALCHEMY_RPC_URL", "").strip() or os.getenv("ETH_RPC_URL", "").strip()
    if not url:
        sys.exit("Thieu ALCHEMY_RPC_URL (hoac ETH_RPC_URL).")
    w3 = Web3(Web3.HTTPProvider(url, request_kwargs={"timeout": 60}))
    if w3.eth.chain_id != 1:
        sys.exit("Khong phai Ethereum mainnet")

    cfg = json.loads(CFG.read_text(encoding="utf-8"))
    changed = False
    for label, s in cfg["snapshots"].items():
        target = int(datetime.fromisoformat(s["target_utc"].replace("Z", "+00:00")).timestamp())
        if s.get("block"):
            n = int(s["block"])
            b = w3.eth.get_block(n)
            ok_hash = b["hash"].hex().lower().removeprefix("0x") == str(s["block_hash"]).lower().removeprefix("0x")
            nxt = ts_of(w3, n + 1)
            ok_order = int(b["timestamp"]) <= target < nxt
            print(f"[{label}] da co block {n}: hash khop={ok_hash} | thu tu thoi gian dung={ok_order}")
            if not (ok_hash and ok_order):
                sys.exit(f"[{label}] Block da ghi KHONG khop. Dung lai de kiem tra.")
        else:
            n = last_block_at_or_before(w3, target)
            b = w3.eth.get_block(n)
            nxt = ts_of(w3, n + 1)
            assert int(b["timestamp"]) <= target < nxt, "Tim block sai"
            s["block"] = n
            s["block_hash"] = "0x" + b["hash"].hex().removeprefix("0x")
            changed = True
            print(f"[{label}] block moi: {n} | timestamp thuc {iso(int(b['timestamp']))} | muc tieu {s['target_utc']} "
                  f"| cach muc tieu {target - int(b['timestamp'])} giay")
            ap = s.get("leader_approx_block")
            if ap:
                print(f"   so voi block 'khoang' cua nhom truong ({ap}): lech {n - int(ap):+d} block")
        s["block_timestamp_utc"] = iso(int(b["timestamp"]))

        # Kiem tra archive state tai block nay
        code = w3.eth.get_code(POOL, block_identifier=n)
        try:
            w3.eth.call({"to": POOL, "data": "0x" + SEL_PROVIDER.hex().removeprefix("0x")}, block_identifier=n)
            call_ok = True
        except Exception as exc:  # noqa: BLE001
            call_ok = False
            print(f"   LOI eth_call tai block {n}: {type(exc).__name__}: {str(exc)[:100]}")
        print(f"   Pool co bytecode: {len(code) > 0} | eth_call doc duoc state: {call_ok}")
        s["archive_state_readable"] = bool(len(code) > 0 and call_ok)

    if changed:
        CFG.write_text(json.dumps(cfg, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        print(f"Da cap nhat {CFG}")
    else:
        print("Khong co thay doi; config da day du.")


if __name__ == "__main__":
    main()