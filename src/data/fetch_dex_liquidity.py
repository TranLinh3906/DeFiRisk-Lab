#!/usr/bin/env python3
"""N3 MUC 4: point-in-time, executable DEX depth for Curve stETH/ETH
and Uniswap V3 specified pairs on Ethereum Mainnet.

Reconstruction: archive eth_call at exact snapshot blocks, not current TVL.
Curve: get_dy(int128,int128,uint256) from the historical stETH/ETH pool.
Uniswap V3: factory.getPool + pool.slot0 + QuoterV2.quoteExactInputSingle
at the historical block (tick-crossing engine in the deployed QuoterV2).

Depth definition: input USD notional at 2% *average execution* price
impact, relative to the fee-inclusive tiny-trade marginal reference.
Swap fees are included in both quoted amounts; their approximately constant effect
is normalized out when comparing the tiny quote to larger sizes.
A missing/failing archive quote is NA, NEVER a manufactured zero or TVL.

Assumptions: single pool/single swap, no multi-hop routing, gas, MEV,
liquidator incentive, or future liquidity. This is indicative executable depth.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import shutil
import tempfile
from datetime import datetime, timezone
from decimal import Decimal, getcontext
from pathlib import Path

import pandas as pd
from dotenv import load_dotenv

getcontext().prec = 70

ROOT = Path(__file__).resolve().parents[2]
CONFIG = ROOT / "config/n3_dex_pools.csv"
METADATA = ROOT / "data/processed/aave/snapshot_metadata.json"
ORACLE = ROOT / "data/processed/aave/oracle_prices.parquet"
REGISTRY = ROOT / "data/processed/aave/token_registry.csv"
LIDO = ROOT / "data/processed/aave/lido_ratios.parquet"
OUTPUT = ROOT / "data/staging/n3_dex_liquidity.parquet"
QUALITY = ROOT / "outputs/tables/n3_dex_liquidity_quality.json"
FACTORY = "0x1F98431c8aD98523631AE4a59f267346ea31F984"
QUOTER_V2 = "0x61fFE014bA17989E743c5F6cB21bF9697530B21e"
ETH_SENTINEL = "0xeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee"
TOKENS = {
    "ETH": "0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2",  # ETH priced as WETH
    "WETH": "0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2",
    "STETH": "0xae7ab96520DE3A18E5e111B5EaAb095312D7fE84",
    "WSTETH": "0x7f39C581F595B53c5cb19Bd0b3f8dA6c935E2Ca0",
    "WBTC": "0x2260FAC5E5542a773Aa44fBCfeDf7C193bc2C599",
    "USDC": "0xA0b86991c6218b36c1d19D4a2e9eb0cE3606eB48",
}

ERC20_ABI = [
    {"name": "decimals", "type": "function", "stateMutability": "view", "inputs": [], "outputs": [{"name": "", "type": "uint8"}]},
]
FACTORY_ABI = [{"name": "getPool", "type": "function", "stateMutability": "view", "inputs": [
    {"name": "tokenA", "type": "address"}, {"name": "tokenB", "type": "address"}, {"name": "fee", "type": "uint24"}],
    "outputs": [{"name": "pool", "type": "address"}]}]
POOL_ABI = [
    {"name": "token0", "type": "function", "stateMutability": "view", "inputs": [], "outputs": [{"name": "", "type": "address"}]},
    {"name": "token1", "type": "function", "stateMutability": "view", "inputs": [], "outputs": [{"name": "", "type": "address"}]},
    {"name": "fee", "type": "function", "stateMutability": "view", "inputs": [], "outputs": [{"name": "", "type": "uint24"}]},
    {"name": "liquidity", "type": "function", "stateMutability": "view", "inputs": [], "outputs": [{"name": "", "type": "uint128"}]},
    {"name": "slot0", "type": "function", "stateMutability": "view", "inputs": [], "outputs": [
        {"name": "sqrtPriceX96", "type": "uint160"}, {"name": "tick", "type": "int24"},
        {"name": "observationIndex", "type": "uint16"}, {"name": "observationCardinality", "type": "uint16"},
        {"name": "observationCardinalityNext", "type": "uint16"}, {"name": "feeProtocol", "type": "uint8"},
        {"name": "unlocked", "type": "bool"}]},
]
QUOTER_ABI = [{"name": "quoteExactInputSingle", "type": "function", "stateMutability": "nonpayable",
    "inputs": [{"name": "params", "type": "tuple", "components": [
        {"name": "tokenIn", "type": "address"}, {"name": "tokenOut", "type": "address"},
        {"name": "amountIn", "type": "uint256"}, {"name": "fee", "type": "uint24"},
        {"name": "sqrtPriceLimitX96", "type": "uint160"}]}],
    "outputs": [{"name": "amountOut", "type": "uint256"}, {"name": "sqrtPriceX96After", "type": "uint160"},
                {"name": "initializedTicksCrossed", "type": "uint32"}, {"name": "gasEstimate", "type": "uint256"}]}]
CURVE_ABI = [
    {"name": "coins", "type": "function", "stateMutability": "view", "inputs": [{"name": "i", "type": "uint256"}], "outputs": [{"name": "", "type": "address"}]},
    {"name": "balances", "type": "function", "stateMutability": "view", "inputs": [{"name": "i", "type": "uint256"}], "outputs": [{"name": "", "type": "uint256"}]},
    {"name": "get_dy", "type": "function", "stateMutability": "view", "inputs": [
        {"name": "i", "type": "int128"}, {"name": "j", "type": "int128"},
        {"name": "dx", "type": "uint256"}], "outputs": [{"name": "", "type": "uint256"}]},
]


def snapshots(metadata: dict) -> dict[str, dict]:
    if int(metadata.get("chain_id", 0)) != 1 or metadata.get("protocol_id") != "aave_v3_ethereum":
        raise ValueError("Only Aave V3 Ethereum Mainnet snapshots are supported")
    raw = metadata.get("snapshots", {})
    if set(raw) != {"A", "B"}:
        raise ValueError("Need both N2 snapshot A and B")
    for tag, snap in raw.items():
        if not bool(snap.get("block_hash_verified_on_chain")):
            raise ValueError(f"Snapshot {tag}: block hash is not verified in N2 metadata")
        if int(snap.get("snapshot_block", 0)) <= 0:
            raise ValueError("Missing positive snapshot_block")
    return raw


def save(path: Path, write_cb, overwrite: bool) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        if not overwrite:
            raise FileExistsError(f"{path} exists; use --overwrite (creates backup)")
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        shutil.copy2(path, path.with_name(f"{path.name}.bak_{stamp}"))
    fd, p = tempfile.mkstemp(prefix=".n3_", suffix=".tmp", dir=path.parent)
    os.close(fd)
    try:
        write_cb(Path(p))
        os.replace(p, path)
    finally:
        Path(p).unlink(missing_ok=True)


def token_metadata() -> dict[str, dict]:
    registry = pd.read_csv(REGISTRY)
    oracle = pd.read_parquet(ORACLE)
    expected = {"token_address", "symbol", "decimals"}
    if expected - set(registry):
        raise ValueError("N2 token_registry.csv lacks address/symbol/decimals")
    if {"snapshot_block", "token_address", "price_usd"} - set(oracle):
        raise ValueError("N2 oracle_prices.parquet lacks snapshot_block/token_address/price_usd")
    reg = registry.copy()
    reg["token_address"] = reg["token_address"].astype(str).str.lower()
    if reg.duplicated("token_address").any():
        raise ValueError("N2 registry contains duplicate token addresses")
    if oracle.duplicated(["snapshot_block", "token_address"]).any():
        raise ValueError("N2 oracle contains duplicate snapshot/address")
    reg_map = reg.set_index("token_address").to_dict("index")
    out = {}
    for r in oracle.itertuples(index=False):
        key = (int(r.snapshot_block), str(r.token_address).lower())
        info = reg_map.get(key[1])
        if not info:
            raise ValueError(f"Oracle token not in N2 registry: {key[1]}")
        raw_price = float(r.price_usd)
        if not math.isfinite(raw_price) or raw_price <= 0:
            raise ValueError(f"Invalid oracle USD price for block {key[0]}: {key[1]}")
        out[key] = {"symbol": str(info["symbol"]), "decimals": int(info["decimals"]), "usd": raw_price, "price_method": "N2 Aave oracle getAssetPrice"}
    # stETH is not an Aave reserve in the supplied 67-token N2 registry.
    # Convert N2 wstETH oracle price using N2's same-block contract ratio;
    # this is only an implied USD input-notional reference, NOT Curve spot.
    if not LIDO.exists():
        raise FileNotFoundError("Missing N2 Lido same-block ratio file")
    lido = pd.read_parquet(LIDO)
    if {"snapshot_block", "steth_per_wsteth"} - set(lido):
        raise ValueError("N2 Lido ratio file missing snapshot_block/steth_per_wsteth")
    if lido.duplicated("snapshot_block").any():
        raise ValueError("Duplicate Lido ratio per snapshot")
    for item in lido.itertuples(index=False):
        block = int(item.snapshot_block)
        ratio = float(item.steth_per_wsteth)
        weth_stake = out.get((block, TOKENS["WSTETH"].lower()))
        if not weth_stake or not math.isfinite(ratio) or ratio <= 0:
            raise ValueError(f"Cannot derive stETH USD from N2 Lido ratio at block {block}")
        # No N2 stETH reserve: add separately (symbol never joins asset config).
        out[(block, TOKENS["STETH"].lower())] = {
            "symbol": "STETH", "decimals": 18,
            "usd": float(weth_stake["usd"] / ratio),
            "price_method": "implied: N2 wstETH Aave oracle USD / Lido same-block stEthPerToken",
        }
    return out


def pair_info(block: int, symbol: str, token_map: dict) -> dict:
    sym = symbol.upper().strip()
    if sym not in TOKENS:
        raise ValueError(f"Unknown token {sym}; add approved token config before running")
    addr = TOKENS[sym].lower()
    data = token_map.get((block, addr))
    if data is None:
        raise ValueError(f"No N2 historical Aave USD price at block={block} asset={sym}")
    return {"symbol": sym, "address": addr, **data}


def impact(amount_raw: int, output_raw: int, spot_raw_ratio: Decimal) -> float:
    if amount_raw <= 0 or output_raw <= 0 or spot_raw_ratio <= 0:
        raise ValueError("Invalid amount or spot quote")
    achieved = Decimal(output_raw) / Decimal(amount_raw)
    slippage = Decimal(1) - achieved / spot_raw_ratio
    return float(slippage)


def binary_depth(quote, raw_per_usd: Decimal, spot_raw_ratio: Decimal, max_usd: float,
                 threshold: float = 0.02, iterations: int = 48,
                 tolerance_usd: float = 1.0) -> dict:
    """Bracket the first 2% average-impact crossing; return a USD upper bound.

    A value is VERIFIED only if impact(low)<2%<=impact(high) and the
    high-low notional interval is <= tolerance_usd. We never extrapolate.
    """
    if (not math.isfinite(max_usd) or max_usd <= 100
            or not math.isfinite(tolerance_usd) or tolerance_usd <= 0
            or raw_per_usd <= 0 or spot_raw_ratio <= 0):
        raise ValueError("Bad search limits or price inputs")
    cap_raw = int(raw_per_usd * Decimal(str(max_usd)))
    calls = 0
    cached: dict[int, float] = {}
    def evaluate(x: int) -> float:
        nonlocal calls
        if x in cached:
            return cached[x]
        calls += 1
        y = int(quote(int(x)))
        v = impact(x, y, spot_raw_ratio)
        if not math.isfinite(v):
            raise ValueError("Non-finite slippage")
        cached[x] = v
        return v
    def pack(status: str, lo=None, hi=None, detail=None):
        def usd(x):
            return None if x is None else float(Decimal(x) / raw_per_usd)
        return {"status": status, "depth_usd": usd(hi) if status == "verified_2pct_bracket" else None,
                "depth_lower_bound_usd": usd(lo),
                "depth_upper_bound_usd": usd(hi),
                "depth_bracket_width_usd": usd(hi-lo) if hi is not None and lo is not None else None,
                "impact_at_depth_pct": 100 * cached[hi] if hi in cached else None,
                "impact_below_depth_pct": 100 * cached[lo] if lo in cached else None,
                "impact_at_cap_pct": 100 * cached[cap_raw] if cap_raw in cached else None,
                "quotes": calls, "numerical_tolerance_usd": tolerance_usd,
                "failure_detail": detail}
    if cap_raw < 2:
        return pack("invalid_cap")
    start = min(cap_raw, max(1, int(raw_per_usd * Decimal(100))))
    try:
        imp = evaluate(start)
    except Exception as exc:
        return pack("quote_failed_at_small_trade", detail=type(exc).__name__)
    if imp < threshold:
        low, high = start, None
        current = start
        while current < cap_raw:
            nxt = min(cap_raw, current * 2)
            if nxt == current:
                break
            try:
                imp = evaluate(nxt)
            except Exception as exc:
                return pack("quote_failed_before_threshold", low, detail=type(exc).__name__)
            if imp >= threshold:
                high = nxt
                break
            low, current = nxt, nxt
        if high is None:
            return pack("threshold_not_reached_within_cap", low,
                        detail="Raise --max-usd; this value is a LOWER bound, not a measured crossing")
    else:
        high, low = start, None
        current = start
        # Avoid probing 1 wei directly: rounding commonly makes a 1-wei quote zero.
        while current > 1:
            nxt = max(1, current // 2)
            if nxt == current:
                break
            try:
                imp = evaluate(nxt)
            except Exception:
                break
            if imp < threshold:
                low = nxt
                break
            high, current = nxt, nxt
        if low is None:
            return pack("threshold_below_smallest_valid_quote", None, high,
                        detail="Cannot verify a quote BELOW 2%; depth is an upper bound only")
    # Both bracket ends are actually quoted, with strict sign conditions.
    if not (cached[low] < threshold <= cached[high]):
        return pack("non_monotonic_or_invalid_bracket", low, high)
    for _ in range(iterations):
        if high - low <= 1 or (Decimal(high-low) / raw_per_usd <= Decimal(str(tolerance_usd))):
            break
        mid = (low + high) // 2
        if mid in (low, high):
            break
        try:
            got = evaluate(mid)
        except Exception as exc:
            return pack("quote_failed_during_bisection", low, high, detail=type(exc).__name__)
        if got >= threshold:
            high = mid
        else:
            low = mid
    width = Decimal(high-low) / raw_per_usd
    if width > Decimal(str(tolerance_usd)) and high-low > 1:
        return pack("insufficient_bisection_precision", low, high,
                    detail="Increase --iterations; do not report the upper bound as exact depth")
    if not (cached[low] < threshold <= cached[high]):
        return pack("non_monotonic_or_invalid_bracket", low, high)
    return pack("verified_2pct_bracket", low, high)


def get_web3(url: str):
    try:
        from web3 import Web3
    except ImportError as exc:
        raise RuntimeError("Install dependencies: pip install -r requirements_n3_muc4_muc5.txt") from exc
    w3 = Web3(Web3.HTTPProvider(url, request_kwargs={"timeout": 50}))
    if not w3.is_connected():
        raise RuntimeError("Cannot connect to Ethereum RPC; check .env (URL not printed)")
    if int(w3.eth.chain_id) != 1:
        raise RuntimeError("RPC is not Ethereum Mainnet chain_id=1")
    return w3, Web3


def check_time(block: int, snap: dict, w3) -> dict:
    r = w3.eth.get_block(block)
    epoch = int(r["timestamp"])
    t = pd.Timestamp(epoch, unit="s", tz="UTC")
    expected = pd.to_datetime(snap["snapshot_timestamp_utc"], utc=True)
    if t != expected:
        raise ValueError(f"Block timestamp mismatch: block {block}, metadata={expected}, RPC={t}")
    observed_hash = str(r["hash"].hex()).lower().removeprefix("0x")
    approved_hash = str(snap.get("block_hash", "")).lower().removeprefix("0x")
    if approved_hash != observed_hash:
        raise ValueError("Historical RPC block hash differs from N2 metadata (reorg/wrong chain)")
    return {"block_hash": r["hash"].hex(), "timestamp": t}


def evaluate_one(row: dict, snap_tag: str, snap: dict, w3, Web3,
                 token_map: dict, factory, quoter, max_usd: float, iterations: int, tolerance_usd: float) -> dict:
    block = int(snap["snapshot_block"])
    out = {
        "snapshot_id": snap_tag, "snapshot_block": block,
        "snapshot_timestamp_utc": pd.to_datetime(snap["snapshot_timestamp_utc"], utc=True),
        "chain_id": 1, "protocol_id": "aave_v3_ethereum",
        "dex": row["dex"], "pool_address": None,
        "token_in": row["token_in"], "token_out": row["token_out"],
        "token_in_address": TOKENS[row["token_in"]].lower(),
        "token_out_address": TOKENS[row["token_out"]].lower(),
        "fee_tier": None, "snapshot_block_hash": None,
        "spot_output_raw_per_input_raw": None, "reference_quote_method": "tiny fee-inclusive historical quote",
        "input_token_usd_oracle": None, "usd_conversion_method": None,
        "dex_depth_2pct_usd": math.nan, "observed_impact_pct": math.nan,
        "max_search_usd": float(max_usd), "quote_calls": 0,
        "depth_lower_bound_usd": math.nan, "depth_upper_bound_usd": math.nan,
        "depth_bracket_width_usd": math.nan, "impact_below_depth_pct": math.nan,
        "impact_at_cap_pct": math.nan, "numerical_tolerance_usd": tolerance_usd,
        "status": "not_started", "reconstruction_method": None,
        "data_source": "Ethereum archive RPC; Uniswap V3 Factory/Pool/QuoterV2 or Curve stETH pool",
        "limitations": "One preselected pool and trade direction; no routing, MEV, gas or execution guarantee",
        "error_type": None,
    }
    try:
        blockinfo = check_time(block, snap, w3)
        out["snapshot_block_hash"] = blockinfo["block_hash"]
        inp = pair_info(block, row["token_in"], token_map)
        output_meta = pair_info(block, row["token_out"], token_map)
        input_decimals = inp["decimals"]
        out_decimals = output_meta["decimals"]
        price_usd = inp["usd"]
        out["input_token_usd_oracle"] = price_usd
        out["usd_conversion_method"] = inp.get("price_method", "unknown")
        # Check ERC20 decimals at historical block; ETH is native (18 decimals).
        if row["token_in"] != "ETH":
            observed = w3.eth.contract(address=Web3.to_checksum_address(inp["address"]), abi=ERC20_ABI).functions.decimals().call(block_identifier=block)
            if int(observed) != int(input_decimals):
                raise ValueError("Token decimals disagree with N2 registry / Lido convention")
        raw_per_usd = (Decimal(10) ** input_decimals) / Decimal(str(price_usd))
        if row["dex"] == "curve":
            addr = Web3.to_checksum_address(row["pool_address"])
            pool = w3.eth.contract(address=addr, abi=CURVE_ABI)
            i, j = int(row["curve_index_in"]), int(row["curve_index_out"])
            actual_i = str(pool.functions.coins(i).call(block_identifier=block)).lower()
            actual_j = str(pool.functions.coins(j).call(block_identifier=block)).lower()
            expected_i = ETH_SENTINEL if row["token_in"] == "ETH" else inp["address"]
            expected_j = ETH_SENTINEL if row["token_out"] == "ETH" else output_meta["address"]
            if (actual_i, actual_j) != (expected_i.lower(), expected_j.lower()):
                raise ValueError("Curve historical coin ordering differs from config")
            def quote(x):
                return pool.functions.get_dy(i, j, x).call(block_identifier=block)
            # Marginal benchmark includes the Curve fee; uses tiny but non-dust real quote.
            tiny = max(1, 10 ** max(input_decimals - 4, 0))  # 0.0001 token
            tiny_out = int(quote(tiny))
            if tiny_out <= 0:
                raise ValueError("Curve marginal quote returned zero")
            spot = Decimal(tiny_out) / Decimal(tiny)
            out["pool_address"] = addr.lower()
            out["reconstruction_method"] = "Curve historical pool.get_dy; fee-adjusted tiny-trade marginal reference; bracket 2% average slippage"
        elif row["dex"] == "uniswap_v3":
            fee = int(row["fee"])
            a, b = Web3.to_checksum_address(inp["address"]), Web3.to_checksum_address(output_meta["address"])
            addr = factory.functions.getPool(a, b, fee).call(block_identifier=block)
            if int(addr, 16) == 0:
                raise ValueError("Uniswap V3 Factory.getPool returns zero at historical block; fee tier may not exist")
            addr = Web3.to_checksum_address(addr)
            pool = w3.eth.contract(address=addr, abi=POOL_ABI)
            token0 = pool.functions.token0().call(block_identifier=block).lower()
            token1 = pool.functions.token1().call(block_identifier=block).lower()
            if {token0, token1} != {inp["address"], output_meta["address"]}:
                raise ValueError("V3 factory pool token0/token1 mismatch")
            actual_fee = int(pool.functions.fee().call(block_identifier=block))
            if fee != actual_fee:
                raise ValueError("V3 fee mismatch")
            if int(pool.functions.liquidity().call(block_identifier=block)) <= 0:
                raise ValueError("V3 pool has zero active liquidity at historical block")
            sqrt = int(pool.functions.slot0().call(block_identifier=block)[0])
            if sqrt <= 0:
                raise ValueError("V3 pool is not initialized at historical block")
            price_1per0_raw = Decimal(sqrt) ** 2 / Decimal(2) ** 192
            spot_no_fee = price_1per0_raw if token0 == inp["address"] else 1 / price_1per0_raw
            if spot_no_fee <= 0:
                raise ValueError("Invalid Uniswap historical marginal price")
            def quote(x):
                res = quoter.functions.quoteExactInputSingle((a, b, int(x), fee, 0)).call(
                    block_identifier=block)
                return res[0]
            tiny = max(1, 10 ** max(input_decimals - 4, 0))
            tiny_out = int(quote(tiny))
            if tiny_out <= 0:
                raise ValueError("V3 fee-inclusive tiny quote returned zero")
            spot = Decimal(tiny_out) / Decimal(tiny)
            out["pool_address"] = addr.lower()
            out["fee_tier"] = fee
            out["reconstruction_method"] = "Uniswap V3 historical factory.getPool + slot0 verification + QuoterV2 tick-crossing; fee-inclusive tiny trade reference"
        else:
            raise ValueError("Unknown DEX name")
        out["spot_output_raw_per_input_raw"] = float(spot)
        result = binary_depth(quote, raw_per_usd, spot, max_usd, iterations=iterations, tolerance_usd=tolerance_usd)
        out["status"] = result["status"]
        out["quote_calls"] = result["quotes"]
        for name in ("depth_lower_bound_usd", "depth_upper_bound_usd", "depth_bracket_width_usd",
                     "impact_below_depth_pct", "impact_at_cap_pct", "numerical_tolerance_usd"):
            if result.get(name) is not None:
                out[name] = result[name]
        if result["depth_usd"] is not None:
            out["dex_depth_2pct_usd"] = result["depth_usd"]
            out["observed_impact_pct"] = result["impact_at_depth_pct"]
        if out["status"] != "verified_2pct_bracket":
            out["limitations"] += "; depth not verified: " + out["status"]
    except Exception as exc:
        out["status"] = "unverified_error"
        # Do not print RPC errors that can include sensitive URLs/API keys.
        out["error_type"] = type(exc).__name__
        out["limitations"] += "; historical quote/pool/oracle unavailable; no depth inferred"
    return out


def main() -> None:
    cli = argparse.ArgumentParser(description="N3 Muc 4 — historical Curve/UniV3 executable depth at 2% impact")
    cli.add_argument("--config", type=Path, default=CONFIG)
    cli.add_argument("--metadata", type=Path, default=METADATA)
    cli.add_argument("--max-usd", type=float, default=500_000_000,
                     help="Maximum USD to test; can raise to 1e9 or more if needed")
    cli.add_argument("--iterations", type=int, default=48)
    cli.add_argument("--tolerance-usd", type=float, default=1.0,
                     help="Maximum certified bracket width in USD (numeric precision)")
    cli.add_argument("--dry-run", action="store_true")
    cli.add_argument("--overwrite", action="store_true")
    cli.add_argument("--require-complete", action="store_true", help="Exit if any selected pool snapshot unavailable")
    args = cli.parse_args()
    if (args.max_usd <= 100 or not math.isfinite(args.max_usd)
            or not 1 <= args.iterations <= 70 or args.tolerance_usd <= 0
            or not math.isfinite(args.tolerance_usd)):
        cli.error("--max-usd >100, 1<=--iterations<=70, --tolerance-usd >0 required")
    raw = json.loads(args.metadata.read_text(encoding="utf-8"))
    snaps = snapshots(raw)
    config = pd.read_csv(args.config).fillna("")
    required = {"dex", "token_in", "token_out", "fee", "curve_index_in", "curve_index_out", "pool_address"}
    if config.empty or required - set(config):
        raise ValueError(f"config/n3_dex_pools.csv must contain {sorted(required)}")
    jobs = config.to_dict("records")
    for row in jobs:
        for k in ("dex", "token_in", "token_out"):
            row[k] = str(row[k]).strip().lower() if k == "dex" else str(row[k]).strip().upper()
        if row["dex"] not in {"curve", "uniswap_v3"} or row["token_in"] not in TOKENS or row["token_out"] not in TOKENS:
            raise ValueError(f"Invalid DEX/token config row: {row['dex']} {row['token_in']} {row['token_out']}")
        if row["dex"] == "curve" and (not row["pool_address"] or row["curve_index_in"] == "" or row["curve_index_out"] == ""):
            raise ValueError("Curve pool address and coin indices are required")
        if row["dex"] == "uniswap_v3" and row["fee"] == "":
            raise ValueError("Uniswap V3 fee tier is required")
    print(f"[MUC 4] {len(jobs)} pool directions, {len(snaps)} snapshots => {len(jobs)*len(snaps)} rows")
    if args.dry_run:
        print("[DRY RUN] Approved pair config parsed; no RPC queries, no output written")
        print("[SCOPE] " + ", ".join(f"{x['dex']} {x['token_in']}->{x['token_out']}" for x in jobs))
        return
    load_dotenv(ROOT / ".env")
    url = os.environ.get("ETHEREUM_ARCHIVE_RPC_URL", "").strip() or os.environ.get("ETH_RPC_URL", "").strip()
    if not url.startswith(("https://", "http://")):
        raise SystemExit("Need ETHEREUM_ARCHIVE_RPC_URL or ETH_RPC_URL in .env (DO NOT print your API keys)")
    tok = token_metadata()
    w3, Web3 = get_web3(url)
    factory = w3.eth.contract(address=Web3.to_checksum_address(FACTORY), abi=FACTORY_ABI)
    quoter = w3.eth.contract(address=Web3.to_checksum_address(QUOTER_V2), abi=QUOTER_ABI)
    records = []
    for tag, snap in snaps.items():
        for row in jobs:
            d = evaluate_one(row, tag, snap, w3, Web3, tok, factory, quoter, args.max_usd, args.iterations, args.tolerance_usd)
            records.append(d)
            print(f"[{tag}] {row['dex']} {row['token_in']}->{row['token_out']}: "
                  f"{d['status']} depth_usd={d['dex_depth_2pct_usd']} "
                  f"lower_bound_usd={d['depth_lower_bound_usd']} "
                  f"impact_at_cap_pct={d['impact_at_cap_pct']}")
    frame = pd.DataFrame(records)
    valid = frame["status"].eq("verified_2pct_bracket")
    summary = {
        "chain_id": 1, "n_snapshot_pool_directions": int(len(frame)),
        "n_verified_depth": int(valid.sum()), "n_unverified": int((~valid).sum()),
        "approved_pools_source": str(args.config),
        "oracle": "N2 Aave oracle USD/token; ETH=WETH; stETH USD implied from same-block wstETH oracle / Lido ratio (NOT Curve market spot)",
        "reconstruction": "archive eth_call Curve get_dy / UniV3 QuoterV2 at exact block, NOT TVL",
        "depth_definition": "input USD where average quoted execution deteriorates 2% vs same-block fee-inclusive tiny-trade quote; equal normalization Curve/V3",
        "depth_certification": "smallest QUOTED upper bracket in USD within numerical_tolerance_usd of a QUOTED lower bracket; lower impact <2%, upper impact >=2%. Not exchange-wide liquidity.",
        "max_trade_search_usd": args.max_usd,
        "depth_numeric_tolerance_usd": args.tolerance_usd,
        "depth_is_numeric_bracket_upper_bound": True,
        "unverified_rows": frame.loc[~valid, ["snapshot_id", "dex", "token_in", "token_out", "status", "depth_lower_bound_usd", "impact_at_cap_pct"]].where(pd.notna(frame.loc[~valid, ["snapshot_id", "dex", "token_in", "token_out", "status", "depth_lower_bound_usd", "impact_at_cap_pct"]]), None).to_dict(orient="records"),
        "assumptions": ["Single selected pool per pair; alternate fee tiers may be deeper",
                        "No multi-hop routes, aggregator, gas, MEV, or liquidation incentive",
                        "Both Curve and V3 references estimated via fee-inclusive tiny swap; V3 slot0 separately verified",
                        "Real trading may differ; simulations should disclose selected direction"],
        "snapshot_status": "N2 supplied snapshot blocks, N1 approval of crisis window still needed",
        "status_counts": {str(k): int(v) for k, v in frame["status"].value_counts().items()},
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
    }
    if args.require_complete and not bool(valid.all()):
        raise SystemExit("Selected DEX routes have missing historical depth; outputs NOT written (--require-complete)")
    save(OUTPUT, lambda p: frame.to_parquet(p, index=False, compression="snappy"), args.overwrite)
    save(QUALITY, lambda p: p.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8"), args.overwrite)
    print(f"[SAVED] {OUTPUT.relative_to(ROOT)}; verified={int(valid.sum())}/{len(frame)}")
    print("[NOTICE] Unverified rows have NaN depth; never substitute TVL or zero.")


if __name__ == "__main__":
    main()
