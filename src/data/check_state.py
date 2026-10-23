"""
check_state.py - Kiem tra eth_call o block lich su cho Aave V3 Ethereum (Snapshot A/B).

Chay tu thu muc goc repo:
    pip install web3 python-dotenv
    $env:ALCHEMY_RPC_URL = "https://..."     # PowerShell, hoac dat trong .env
    python src/data/check_state.py

Khong in/luu RPC URL. Ket qua luu vao data/raw/aave/state_check.json.
CHUA duoc chay thu o phia nguoi viet: hay doc ket qua PASS/FAIL that.
"""
import ast
import csv
import json
import os
import sys
from pathlib import Path

from web3 import Web3

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

ROOT = Path(__file__).resolve().parents[2] if Path(__file__).parent.name == "data" else Path.cwd()
POOL = Web3.to_checksum_address("0x87870bca3f3fd6335c3f4ce8392d69350b4fa4e2")

def load_snapshots():
    """Doc nhan snapshot, block, hash tu config/snapshots.json (nguon duy nhat)."""
    cfg = json.loads((ROOT / "config/snapshots.json").read_text(encoding="utf-8"))["snapshots"]
    bad = [k for k, v in cfg.items() if not v.get("block") or not v.get("block_hash")]
    if bad:
        sys.exit(f"config/snapshots.json thieu block/hash cho {bad}; chay find_snapshot_blocks.py truoc.")
    return {k: (int(v["block"]), v["block_hash"]) for k, v in cfg.items()}


SNAPSHOTS = load_snapshots()


def abi(*items):
    out = []
    for name, ins, outs in items:
        out.append({
            "name": name, "type": "function", "stateMutability": "view",
            "inputs": [{"name": f"i{k}", "type": t} for k, t in enumerate(ins)],
            "outputs": [{"name": f"o{k}", "type": t} for k, t in enumerate(outs)],
        })
    return out


POOL_ABI = abi(
    ("ADDRESSES_PROVIDER", [], ["address"]),
    ("getReservesList", [], ["address[]"]),
    ("getReserveNormalizedIncome", ["address"], ["uint256"]),
    ("getReserveNormalizedVariableDebt", ["address"], ["uint256"]),
    ("getUserAccountData", ["address"],
     ["uint256", "uint256", "uint256", "uint256", "uint256", "uint256"]),
)
PROVIDER_ABI = abi(
    ("getPriceOracle", [], ["address"]),
    ("getPoolDataProvider", [], ["address"]),
)
ORACLE_ABI = abi(
    ("BASE_CURRENCY_UNIT", [], ["uint256"]),
    ("getAssetPrice", ["address"], ["uint256"]),
    ("getSourceOfAsset", ["address"], ["address"]),
)
ERC20_ABI = abi(("symbol", [], ["string"]), ("decimals", [], ["uint8"]))
FEED_ABI = abi(
    ("description", [], ["string"]),
    ("decimals", [], ["uint8"]),
    ("aggregator", [], ["address"]),
    ("latestRoundData", [], ["uint80", "int256", "uint256", "uint256", "uint80"]),
)


def safe(fn):
    """Chay fn(); tra ve (gia_tri, None) hoac (None, loi ngan)."""
    try:
        return fn(), None
    except Exception as exc:  # noqa: BLE001
        return None, f"{type(exc).__name__}: {str(exc)[:120]}"


def main():
    url = os.getenv("ALCHEMY_RPC_URL", "").strip()
    if not url:
        sys.exit("Thieu ALCHEMY_RPC_URL (dat bien moi truong hoac .env, khong commit).")
    w3 = Web3(Web3.HTTPProvider(url, request_kwargs={"timeout": 60}))
    assert w3.eth.chain_id == 1, "Khong phai Ethereum mainnet"

    pool = w3.eth.contract(address=POOL, abi=POOL_ABI)
    report = {"pool": POOL, "snapshots": {}}

    for label, (blk, expected_hash) in SNAPSHOTS.items():
        res = {"block": blk, "checks": {}}
        b = w3.eth.get_block(blk)
        res["block_timestamp_utc"] = int(b["timestamp"])
        res["checks"]["block_hash_match"] = b["hash"].hex().lower().removeprefix("0x") == \
            expected_hash.lower().removeprefix("0x")

        provider_addr, err = safe(lambda: pool.functions.ADDRESSES_PROVIDER().call(block_identifier=blk))
        res["checks"]["eth_call_pool"] = err is None
        if err:
            res["error"] = err
            report["snapshots"][label] = res
            print(f"[{label}] FAIL eth_call Pool: {err}")
            continue

        provider = w3.eth.contract(address=provider_addr, abi=PROVIDER_ABI)
        oracle_addr = provider.functions.getPriceOracle().call(block_identifier=blk)
        data_provider = provider.functions.getPoolDataProvider().call(block_identifier=blk)
        res["addresses_provider"] = provider_addr
        res["aave_oracle"] = oracle_addr
        res["pool_data_provider"] = data_provider
        oracle = w3.eth.contract(address=oracle_addr, abi=ORACLE_ABI)
        base_unit = oracle.functions.BASE_CURRENCY_UNIT().call(block_identifier=blk)
        res["base_currency_unit"] = int(base_unit)

        reserves = pool.functions.getReservesList().call(block_identifier=blk)
        rows, n_price_ok, n_idx_ok = [], 0, 0
        for asset in reserves:
            tok = w3.eth.contract(address=asset, abi=ERC20_ABI)
            sym, e1 = safe(lambda: tok.functions.symbol().call(block_identifier=blk))
            dec, e2 = safe(lambda: tok.functions.decimals().call(block_identifier=blk))
            price, e3 = safe(lambda: oracle.functions.getAssetPrice(asset).call(block_identifier=blk))
            src, _ = safe(lambda: oracle.functions.getSourceOfAsset(asset).call(block_identifier=blk))
            inc, e4 = safe(lambda: pool.functions.getReserveNormalizedIncome(asset).call(block_identifier=blk))
            dbt, e5 = safe(lambda: pool.functions.getReserveNormalizedVariableDebt(asset).call(block_identifier=blk))

            feed_info = {}
            if src:
                feed = w3.eth.contract(address=src, abi=FEED_ABI)
                d, _ = safe(lambda: feed.functions.description().call(block_identifier=blk))
                agg, _ = safe(lambda: feed.functions.aggregator().call(block_identifier=blk))
                rd, _ = safe(lambda: feed.functions.latestRoundData().call(block_identifier=blk))
                feed_info = {"description": d, "aggregator_impl": agg,
                             "feed_updated_at": int(rd[3]) if rd else None}

            if price is not None and price > 0:
                n_price_ok += 1
            if inc and dbt and inc >= 10**27 and dbt >= 10**27:
                n_idx_ok += 1
            rows.append({
                "token_address": asset.lower(), "symbol": sym or f"ERR({e1})", "decimals": dec,
                "oracle_price_raw": price, "oracle_price_usd": (price / base_unit) if price else None,
                "price_source": src, **feed_info,
                "liquidity_index_normalized": inc, "variable_borrow_index_normalized": dbt,
                "errors": [e for e in (e2, e3, e4, e5) if e],
            })
        res["reserves"] = rows
        res["checks"]["n_reserves"] = len(reserves)
        res["checks"]["all_prices_ok"] = n_price_ok == len(reserves)
        res["checks"]["all_indices_ok"] = n_idx_ok == len(reserves)
        report["snapshots"][label] = res
        print(f"[{label}] block {blk}: {len(reserves)} reserves | "
              f"price OK {n_price_ok}/{len(reserves)} | index OK {n_idx_ok}/{len(reserves)}")
        print(f"      oracle={oracle_addr} | data_provider={data_provider}")

    # Kiem tra getUserAccountData bang 1 vi bi thanh ly that (lay tu du lieu N3)
    raw = ROOT / "data/raw/market/etherscan_liquidations_raw.csv"
    if raw.exists():
        with open(raw, newline="", encoding="utf-8") as f:
            ev = next(csv.DictReader(f))
        topics = ast.literal_eval(ev["topics"])
        user = Web3.to_checksum_address("0x" + topics[3][-40:])
        evblk = int(ev["blockNumber"], 16)
        acct, err = safe(lambda: pool.functions.getUserAccountData(user).call(block_identifier=evblk - 1))
        report["user_account_probe"] = {
            "user": user.lower(), "block": evblk - 1,
            "health_factor": (acct[5] / 1e18) if acct else None, "error": err,
            "note": "Chi de xem getUserAccountData goi duoc o block lich su; HF ky vong gan/duoi 1 truoc luc bi thanh ly.",
        }
        print(f"[probe] user {user[:10]}... block {evblk-1}: HF="
              f"{report['user_account_probe']['health_factor']} err={err}")

    out = ROOT / "data/raw/aave/state_check.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    print(f"Da luu {out}")


if __name__ == "__main__":
    main()