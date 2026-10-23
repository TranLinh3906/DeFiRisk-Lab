#!/usr/bin/env python3
"""N3: historical Compound III (Comet) balances for wallets already observed on Aave V3.

Point-in-time, account-scoped bridge-data exploration for Q3. NOT a contagion model
and NOT a census of all Compound users. Comet liquidation differs from Aave HF.

Requires: pip install web3 pandas pyarrow python-dotenv requests
Run from project root; see docs/N3_COMPOUND_Q3_README.md.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import shutil
import tempfile
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd
import requests
from dotenv import load_dotenv
from eth_abi import decode, encode
from web3 import Web3

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_AAVE = ROOT / "data/processed/aave/account_reference.parquet"
DEFAULT_META = ROOT / "data/processed/aave/snapshot_metadata.json"
DEFAULT_MARKETS = ROOT / "config/n3_compound_markets.csv"
OUTPUT = ROOT / "data/processed/compound"
CACHE = ROOT / "data/raw/compound/n3_overlap_checkpoint.jsonl"
ADDR = re.compile(r"^0x[a-fA-F0-9]{40}$")
PRICE_SCALE = 10**8  # Comet getPrice() USD quote scale; docs Compound III
FACTOR_SCALE = 10**18

COMET_ABI = [
    {"type":"function","name":"baseToken","stateMutability":"view","inputs":[],"outputs":[{"type":"address","name":""}]},
    {"type":"function","name":"baseTokenPriceFeed","stateMutability":"view","inputs":[],"outputs":[{"type":"address","name":""}]},
    {"type":"function","name":"numAssets","stateMutability":"view","inputs":[],"outputs":[{"type":"uint8","name":""}]},
    {"type":"function","name":"getPrice","stateMutability":"view","inputs":[{"type":"address","name":"priceFeed"}],"outputs":[{"type":"uint128","name":""}]},
    {"type":"function","name":"getAssetInfo","stateMutability":"view","inputs":[{"type":"uint8","name":"i"}],"outputs":[{"type":"tuple","name":"","components":[
        {"type":"uint8","name":"offset"},{"type":"address","name":"asset"},{"type":"address","name":"priceFeed"},
        {"type":"uint64","name":"scale"},{"type":"uint64","name":"borrowCollateralFactor"},
        {"type":"uint64","name":"liquidateCollateralFactor"},{"type":"uint64","name":"liquidationFactor"},
        {"type":"uint128","name":"supplyCap"}]}]},
]
ERC20_ABI = [
    {"type":"function","name":"decimals","stateMutability":"view","inputs":[],"outputs":[{"type":"uint8","name":""}]},
    {"type":"function","name":"symbol","stateMutability":"view","inputs":[],"outputs":[{"type":"string","name":""}]},
]
SIGS = {
    "borrow_raw": ("borrowBalanceOf(address)", ["address"], ["uint256"]),
    "supply_raw": ("balanceOf(address)", ["address"], ["uint256"]),
    "is_liquidatable": ("isLiquidatable(address)", ["address"], ["bool"]),
    "collateral_raw": ("collateralBalanceOf(address,address)", ["address","address"], ["uint128"]),
}


def valid_address(s: Any) -> str:
    v = str(s).strip().lower()
    if not ADDR.fullmatch(v):
        raise ValueError("Invalid EVM address in input: " + repr(s)[:85])
    return v


def account_columns(df: pd.DataFrame) -> tuple[str, str, str | None]:
    wc = next((k for k in ("wallet_address", "wallet", "user_address", "account") if k in df), None)
    sc = next((k for k in ("snapshot_id", "snapshot", "snapshot_block") if k in df), None)
    dc = next((k for k in ("total_debt_base_usd", "total_debt_usd", "debt_usd", "borrow_usd", "totalDebtUsd") if k in df), None)
    if not wc or not sc:
        raise ValueError(f"Aave account_reference requires wallet and snapshot columns; got {list(df.columns)}")
    return wc, sc, dc


def pick_wallets(aave: pd.DataFrame, snapshots: dict, limit: int, debt_only: bool = True) -> tuple[dict, dict]:
    wc, sc, dc = account_columns(aave)
    a = aave.copy()
    a[wc] = a[wc].map(valid_address)
    if sc == "snapshot_block":
        blocks = {int(x["snapshot_block"]): key for key, x in snapshots.items()}
        a["_snapshot"] = pd.to_numeric(a[sc], errors="raise").astype(int).map(blocks)
    else:
        a["_snapshot"] = a[sc].astype(str).str.strip().str.upper()
    unknown = set(a["_snapshot"].dropna().unique()) - set(snapshots)
    if unknown:
        raise ValueError("Unrecognized snapshot IDs " + str(unknown))
    if debt_only:
        if dc is None:
            raise ValueError("Aave file lacks debt USD column; pass --include-aave-zero-debt to query all Aave accounts")
        a["_debt_usd"] = pd.to_numeric(a[dc], errors="coerce")
        if a["_debt_usd"].isna().any() or (a["_debt_usd"] < 0).any():
            raise ValueError("Invalid or missing Aave debt USD")
        a = a[a["_debt_usd"] > 0].copy()
    else:
        a["_debt_usd"] = pd.to_numeric(a[dc], errors="coerce").fillna(0) if dc else 0.0
    a = a[a["_snapshot"].isin(snapshots)].copy()
    if a.empty:
        raise ValueError("No eligible Aave wallets for A/B snapshots")
    # Duplicate Aave rows for the same wallet/snapshot must not inflate debt.
    by_pair = a.groupby(["_snapshot", wc], as_index=False)["_debt_usd"].max()
    debt_rank = by_pair.groupby(wc)["_debt_usd"].max().sort_values(ascending=False)
    selected = list(debt_rank.index[:limit] if limit > 0 else debt_rank.index)
    result = {}
    selected_set = set(selected)
    for sid in snapshots:
        subset = by_pair[(by_pair._snapshot == sid) & by_pair[wc].isin(selected_set)]
        result[sid] = dict(zip(subset[wc], subset["_debt_usd"]))
    return result, {"eligible_unique_wallets": len(debt_rank), "selected_unique_wallets": len(selected),
                    "input_aave_rows_after_filter": len(by_pair), "selection": "ranked_by_max_aave_debt_usd" if limit else "all_eligible_wallets",
                    "limited_sample": limit > 0 and len(debt_rank) > limit, "aave_debt_column": dc}


def load_snapshots(path: Path) -> dict:
    meta = json.loads(path.read_text(encoding="utf-8"))
    if int(meta.get("chain_id", 0)) != 1:
        raise ValueError("Aave snapshot metadata must be Ethereum mainnet chain_id=1")
    snaps = meta.get("snapshots", {})
    if not all(k in snaps for k in ("A", "B")):
        raise ValueError("Aave metadata needs snapshots A and B")
    for s, info in snaps.items():
        if "snapshot_block" not in info or not info.get("snapshot_timestamp_utc"):
            raise ValueError("Missing point-in-time snapshot metadata for " + s)
    return {s: snaps[s] for s in ("A", "B")}


def load_markets(path: Path) -> list[dict]:
    df = pd.read_csv(path)
    if not {"market_id", "comet_address", "chain_id"}.issubset(df):
        raise ValueError("Missing columns market_id, comet_address, chain_id")
    markets = []
    for row in df.to_dict("records"):
        if int(row["chain_id"]) != 1:
            raise ValueError("Compound market must be Ethereum mainnet")
        markets.append({"market_id": str(row["market_id"]), "comet_address": valid_address(row["comet_address"])})
    if len(markets) == 0 or len({r["comet_address"] for r in markets}) != len(markets):
        raise ValueError("Duplicate Comet market address / missing markets")
    return markets


def metadata_for_market(w3: Web3, info: dict, market: dict, sid: str) -> tuple[dict, list[dict]]:
    block = int(info["snapshot_block"])
    comet_addr = Web3.to_checksum_address(market["comet_address"])
    if not w3.eth.get_code(comet_addr, block_identifier=block):
        raise RuntimeError(f"No Comet code at block {block} for {market['market_id']}")
    comet = w3.eth.contract(address=comet_addr, abi=COMET_ABI)
    base_addr = comet.functions.baseToken().call(block_identifier=block)
    feed = comet.functions.baseTokenPriceFeed().call(block_identifier=block)
    base_price = int(comet.functions.getPrice(feed).call(block_identifier=block)) / PRICE_SCALE
    erc20 = w3.eth.contract(address=base_addr, abi=ERC20_ABI)
    base_decimals = int(erc20.functions.decimals().call(block_identifier=block))
    try:
        base_symbol = erc20.functions.symbol().call(block_identifier=block)
    except Exception:
        base_symbol = base_addr[:10]
    if base_price <= 0 or base_decimals > 36:
        raise ValueError("Invalid base price or decimals at snapshot")
    assets = []
    for i in range(int(comet.functions.numAssets().call(block_identifier=block))):
        offset, addr, price_feed, scale, bcf, lcf, lf, cap = comet.functions.getAssetInfo(i).call(block_identifier=block)
        if int(scale) not in {10 ** i for i in range(0, 37)}:
            raise ValueError("Invalid collateral scale")
        px = int(comet.functions.getPrice(price_feed).call(block_identifier=block)) / PRICE_SCALE
        if px <= 0:
            raise ValueError("Invalid collateral USD price at snapshot")
        token = w3.eth.contract(address=addr, abi=ERC20_ABI)
        try:
            sym = token.functions.symbol().call(block_identifier=block)
        except Exception:
            sym = addr[:10]
        assets.append({"token_address":valid_address(addr),"token_symbol":str(sym),"scale":int(scale),
                       "price_usd":px,"borrow_collateral_factor":int(bcf)/FACTOR_SCALE,
                       "liquidate_collateral_factor":int(lcf)/FACTOR_SCALE,
                       "liquidation_factor":int(lf)/FACTOR_SCALE,
                       "supply_cap_raw":str(int(cap)),"price_feed":valid_address(price_feed),
                       "asset_offset":int(offset)})
    metadata = {"snapshot_id":sid,"snapshot_block":block,"market_id":market["market_id"],
                "comet_address":valid_address(comet_addr),"base_token_address":valid_address(base_addr),
                "base_token_symbol":str(base_symbol),"base_decimals":base_decimals,
                "base_price_usd":base_price,"base_price_feed":valid_address(feed),
                "assets":assets}
    return metadata, assets


def encode_call(sig: str, args: list) -> str:
    _, types, _ = SIGS[sig]
    return "0x" + (Web3.keccak(text=SIGS[sig][0])[:4] + encode(types, args)).hex()


class PacedSession(requests.Session):
    """Space HTTP request starts, including metadata, batches and retries, per process."""

    def __init__(self, interval: float = 0.0):
        super().__init__()
        if not math.isfinite(interval) or interval < 0:
            raise ValueError("RPC interval must be finite and nonnegative")
        self.interval = interval
        self._last_send = None

    def send(self, request, **kwargs):
        if self.interval > 0:
            if self._last_send is not None:
                remaining = self.interval - (time.monotonic() - self._last_send)
                if remaining > 0:
                    time.sleep(remaining)
            self._last_send = time.monotonic()
        return super().send(request, **kwargs)


def rpc_batch(session: requests.Session, rpc: str, calls: list[dict], batch_size: int, retries: int = 4) -> dict[int, dict]:
    """RPC read-only batches; no silent partial results. Error messages never expose RPC URL."""
    found = {}
    for offset in range(0, len(calls), batch_size):
        chunk = calls[offset:offset+batch_size]
        for attempt in range(retries):
            try:
                resp = session.post(rpc, json=chunk, timeout=75)
                resp.raise_for_status()
                body = resp.json()
                if not isinstance(body, list):
                    raise RuntimeError("Provider does not support JSON-RPC batch: expected list response")
                replies = {int(x["id"]):x for x in body if isinstance(x,dict) and "id" in x}
                if set(replies) != {int(x["id"]) for x in chunk}:
                    raise RuntimeError("Incomplete/duplicated RPC batch replies")
                # Providers may return HTTP 200 with per-call RPC errors (e.g.
                # throttling). Preserve successes and retry only failed calls.
                failed_ids = {id_ for id_, reply in replies.items()
                              if "error" in reply or not isinstance(reply.get("result"), str)}
                found.update({id_: reply for id_, reply in replies.items() if id_ not in failed_ids})
                if not failed_ids or attempt == retries-1:
                    found.update(replies)
                    break
                chunk = [call for call in chunk if int(call["id"]) in failed_ids]
                time.sleep(min(2 ** attempt, 8))
            except (requests.RequestException, ValueError, RuntimeError) as exc:
                if attempt == retries-1:
                    raise RuntimeError(f"RPC batch failed after {retries} attempts ({type(exc).__name__}); see RPC provider logs") from None
                time.sleep(min(2 ** attempt, 8))
    return found


def fetch_accounts(session: requests.Session, rpc: str, mid: dict, wallets: list[str], batch_size: int) -> list[dict]:
    calls = []
    tasks = {}
    block = int(mid["snapshot_block"])
    for wallet in wallets:
        chk = Web3.to_checksum_address(wallet)
        specs = [("borrow_raw", [chk], None), ("supply_raw", [chk], None), ("is_liquidatable", [chk], None)]
        specs.extend(("collateral_raw", [chk, Web3.to_checksum_address(a["token_address"])], a["token_address"]) for a in mid["assets"])
        for field, args, addr in specs:
            id_ = len(calls) + 1
            calls.append({"jsonrpc":"2.0","id":id_,"method":"eth_call","params":[
                {"to":Web3.to_checksum_address(mid["comet_address"]),"data":encode_call(field,args)}, hex(block)]})
            tasks[id_] = (wallet,field,addr)
    replies = rpc_batch(session, rpc, calls, batch_size)
    values = defaultdict(dict)
    errors = defaultdict(list)
    for id_, (wallet,field,addr) in tasks.items():
        reply = replies[id_]
        if "error" in reply or not isinstance(reply.get("result"),str):
            errors[wallet].append(field + ": RPC contract error")
            continue
        try:
            v = decode(SIGS[field][2], bytes.fromhex(reply["result"][2:]))[0]
            if field == "collateral_raw":
                values[wallet].setdefault("collaterals", {})[addr] = int(v)
            else:
                values[wallet][field] = bool(v) if field == "is_liquidatable" else int(v)
        except Exception:
            errors[wallet].append(field + ": invalid ABI return")
    answer = []
    for wallet in wallets:
        if errors[wallet]:
            answer.append({"wallet":wallet,"status":"rpc_error","errors":errors[wallet]})
        else:
            answer.append({"wallet":wallet,"status":"ok","values":values[wallet]})
    return answer


def checkpoint_load(path: Path, fingerprint: str) -> dict:
    result = {}
    if not path.exists():
        return result
    with path.open(encoding="utf-8") as f:
        for line_no, line in enumerate(f,1):
            if not line.strip():
                continue
            obj = json.loads(line)
            if obj.get("fingerprint") != fingerprint:
                raise RuntimeError(f"Checkpoint incompatible at line {line_no}. Use --reset-checkpoint after archiving prior output.")
            key = (obj["snapshot_id"],obj["market_id"],obj["wallet"])
            if obj.get("status") == "ok":
                result[key] = obj
    return result


def save_parquet(df: pd.DataFrame, dest: Path, overwrite: bool) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists():
        if not overwrite:
            raise FileExistsError(f"Exists {dest}; pass --overwrite to update (backup created)")
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        shutil.copy2(dest, dest.with_name(dest.name+f".bak_{stamp}"))
    fd, tmp = tempfile.mkstemp(prefix=".n3_compound_",suffix=".parquet",dir=dest.parent)
    os.close(fd)
    try:
        df.to_parquet(tmp,index=False)
        os.replace(tmp,dest)
    finally:
        Path(tmp).unlink(missing_ok=True)


def save_json(obj: dict, dest: Path, overwrite: bool) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists():
        if not overwrite:
            raise FileExistsError(f"Exists {dest}; pass --overwrite")
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        shutil.copy2(dest, dest.with_name(dest.name+f".bak_{stamp}"))
    fd,tmp=tempfile.mkstemp(prefix=".n3_compound_",suffix=".json",dir=dest.parent)
    os.close(fd)
    try:
        Path(tmp).write_text(json.dumps(obj,ensure_ascii=False,indent=2,allow_nan=False),encoding="utf8")
        os.replace(tmp,dest)
    finally:
        Path(tmp).unlink(missing_ok=True)


def build_outputs(records: list[dict], metas: dict) -> tuple[pd.DataFrame,pd.DataFrame,pd.DataFrame,pd.DataFrame]:
    positions = []; accounts=[]; configs=[]
    for mid in metas.values():
        common = {k:mid[k] for k in ("snapshot_id","snapshot_block","market_id","comet_address")}
        configs.append({**common,"token_address":mid["base_token_address"],"token_symbol":mid["base_token_symbol"],
                        "role":"base_asset","price_usd":mid["base_price_usd"],"scale":10**mid["base_decimals"]})
        for asset in mid["assets"]:
            configs.append({**common,**asset,"role":"collateral_asset"})
    for item in records:
        if item["status"] != "ok":
            continue
        mid=metas[(item["snapshot_id"],item["market_id"])]
        wallet=item["wallet"]
        v=item["values"]
        common={k:mid[k] for k in ("snapshot_id","snapshot_block","market_id","comet_address")}
        rows=[]
        for role,raw_field in (("base_borrow","borrow_raw"),("base_supply","supply_raw")):
            amount=int(v[raw_field])/10**mid["base_decimals"]
            if amount>0:
                rows.append({**common,"wallet_address":wallet,"asset_role":role,
                             "token_address":mid["base_token_address"],"token_symbol":mid["base_token_symbol"],
                             "amount_token":amount,"price_usd":mid["base_price_usd"],
                             "value_usd":amount*mid["base_price_usd"],"raw_amount":str(v[raw_field])})
        for asset in mid["assets"]:
            raw=int(v["collaterals"][asset["token_address"]])
            if raw>0:
                amount=raw/asset["scale"]
                rows.append({**common,"wallet_address":wallet,"asset_role":"collateral",
                             "token_address":asset["token_address"],"token_symbol":asset["token_symbol"],
                             "amount_token":amount,"price_usd":asset["price_usd"],
                             "value_usd":amount*asset["price_usd"],"raw_amount":str(raw)})
        if not rows:
            continue
        positions.extend(rows)
        debt_usd=sum(r["value_usd"] for r in rows if r["asset_role"] == "base_borrow")
        collateral_usd=sum(r["value_usd"] for r in rows if r["asset_role"] == "collateral")
        supplied_usd=sum(r["value_usd"] for r in rows if r["asset_role"] == "base_supply")
        accounts.append({**common,"wallet_address":wallet,
                         "compound_borrow_usd":debt_usd,"compound_collateral_usd":collateral_usd,
                         "compound_base_supply_usd":supplied_usd,
                         "compound_is_liquidatable":bool(v["is_liquidatable"]),
                         "is_cross_protocol":True,"aave_debt_usd":float(item.get("aave_debt_usd",0.0))})
    pcols=["snapshot_id","snapshot_block","market_id","comet_address","wallet_address","asset_role","token_address","token_symbol","amount_token","price_usd","value_usd","raw_amount"]
    acols=["snapshot_id","snapshot_block","market_id","comet_address","wallet_address","compound_borrow_usd","compound_collateral_usd","compound_base_supply_usd","compound_is_liquidatable","is_cross_protocol","aave_debt_usd"]
    pos=pd.DataFrame(positions,columns=pcols)
    acc=pd.DataFrame(accounts,columns=acols)
    config=pd.DataFrame(configs)
    if pos.duplicated(["snapshot_block","comet_address","wallet_address","token_address","asset_role"]).any():
        raise ValueError("Duplicate Compound position keys")
    if acc.duplicated(["snapshot_block","comet_address","wallet_address"]).any():
        raise ValueError("Duplicate Compound account keys")
    if not pos.empty and ((pos["amount_token"]<=0)|(pos["price_usd"]<=0)|(pos["value_usd"]<=0)).any():
        raise ValueError("Nonpositive amount/price/value")
    if acc.empty:
        overlap=pd.DataFrame(columns=["snapshot_id","snapshot_block","wallet_address","compound_markets_count","compound_borrow_usd","compound_collateral_usd","compound_base_supply_usd","any_compound_liquidatable","aave_debt_usd"])
    else:
        overlap=acc.groupby(["snapshot_id","snapshot_block","wallet_address"],as_index=False).agg(
            compound_markets_count=("market_id","nunique"),compound_borrow_usd=("compound_borrow_usd","sum"),
            compound_collateral_usd=("compound_collateral_usd","sum"),compound_base_supply_usd=("compound_base_supply_usd","sum"),
            any_compound_liquidatable=("compound_is_liquidatable","any"),aave_debt_usd=("aave_debt_usd","max"))
        if not all(overlap.aave_debt_usd>=0):raise ValueError("Negative Aave debt in overlap")
    return pos,acc,config,overlap


def metadata_fingerprint(metas: dict) -> str:
    # JSON object keys cannot be (snapshot_id, market_id) tuples.
    payload = [[sid, market_id, metas[(sid, market_id)]]
               for sid, market_id in sorted(metas)]
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()


def partition_wallets(selected: dict, count: int, index: int) -> dict:
    """Assign the same wallet to one shard across all snapshots, deterministically."""
    if count < 1 or not 0 <= index < count:
        raise ValueError("Require num-shards >= 1 and 0 <= shard-index < num-shards")
    wallets = sorted({wallet for pairs in selected.values() for wallet in pairs})
    assigned = set(wallets[index::count])
    return {sid: {wallet: debt for wallet, debt in pairs.items() if wallet in assigned}
            for sid, pairs in selected.items()}


def assignment_partition(plan: dict, selected: dict, count: int, index: int) -> tuple[dict, str]:
    selection_hash = hashlib.sha256(json.dumps(selected, sort_keys=True).encode("utf-8")).hexdigest()
    if plan.get("version") != 1 or plan.get("num_shards") != count or plan.get("selection_hash") != selection_hash:
        raise ValueError("Assignment does not match selected Aave inputs or num-shards")
    owners = plan.get("wallet_shards", {})
    wallets = {w for pairs in selected.values() for w in pairs}
    if set(owners) != wallets or any(type(v) is not int or not 0 <= v < count for v in owners.values()):
        raise ValueError("Assignment must cover every selected wallet exactly once with a valid shard index")
    if not plan.get("metadata_fingerprint"):
        raise ValueError("Assignment must declare checkpoint metadata fingerprint")
    digest = hashlib.sha256(json.dumps(plan, sort_keys=True).encode("utf-8")).hexdigest()
    return ({sid: {w: debt for w, debt in pairs.items() if owners[w] == index}
             for sid, pairs in selected.items()}, digest)



def extend_existing_shard(args):
    """Add historical B1/B2 to existing six-shard Compound exports."""
    import importlib.util
    import tempfile

    repo = ROOT
    expected_new = {"B1": 20458637, "B2": 23543615}
    expected_old = {"A": 26179841, "B": 26194188}

    if args.num_shards != 6 or args.max_wallets != 0:
        raise ValueError("Require --num-shards 6 --max-wallets 0")
    if args.assignment_file or args.reset_checkpoint:
        raise ValueError(
            "Do not use --assignment-file or --reset-checkpoint "
            "in historical extension mode"
        )

    name = f"shard_{args.shard_index:03d}_of_006"

    # Locate actual exported shard, including double-nested folders.
    roots = [repo, OUTPUT]
    if args.existing_shards_root:
        roots = [args.existing_shards_root.resolve()]

    candidates = []
    for base in roots:
        for dest in (base / name, base / name / name):
            if (dest / "n3_compound_quality.json").is_file():
                if dest.resolve() not in [
                    x.resolve() for x in candidates
                ]:
                    candidates.append(dest)

    if len(candidates) != 1:
        raise ValueError(
            f"Need exactly one existing {name} export. "
            f"Found: {[str(x) for x in candidates]}. "
            "Specify --existing-shards-root if needed."
        )

    output = candidates[0]

    spec = importlib.util.spec_from_file_location(
        "compound_quality_module",
        ROOT / "scripts/qa_n3_compound.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    # Validate old A/B data before doing anything.
    module.check(output)

    old_qa = json.loads(
        (output / "n3_compound_quality.json").read_text("utf-8")
    )
    if old_qa.get("snapshot_blocks") != expected_old:
        raise ValueError(
            "Existing shard is not the original A/B dataset. "
            "Refusing to overwrite."
        )
    if old_qa["shard"]["shard_index"] != args.shard_index:
        raise ValueError("Shard index does not match old QA")
    if old_qa["shard"]["num_shards"] != 6:
        raise ValueError("Old shard count is not six")

    plan_path = ROOT / "config/n3_compound_team_assignment.json"
    plan = json.loads(plan_path.read_text("utf-8"))
    owners_old = {
        valid_address(w): int(i)
        for w, i in plan["wallet_shards"].items()
    }
    if plan["num_shards"] != 6:
        raise ValueError("Old wallet assignment must have six shards")
    if len(owners_old) != old_qa["shard"][
        "global_selection"
    ]["selected_unique_wallets"]:
        raise ValueError("Old QA does not match wallet assignment")

    # Historical snapshot metadata comes from updated N2 repository.
    metadata_path = repo / "data/processed/aave/snapshot_metadata.json"
    metadata = json.loads(metadata_path.read_text("utf-8"))
    if int(metadata.get("chain_id", 0)) != 1:
        raise ValueError("Wrong Ethereum chain")

    all_snaps = metadata.get("snapshots", {})
    snaps = {}
    for sid, required_block in expected_new.items():
        info = all_snaps.get(sid)
        if not info:
            raise ValueError(f"N2 metadata is missing {sid}")
        if int(info["snapshot_block"]) != required_block:
            raise ValueError(f"Incorrect {sid} block")
        if info.get("block_hash_verified_on_chain") is not True:
            raise ValueError(f"{sid} block is not verified")
        if not info.get("block_hash"):
            raise ValueError(f"Missing {sid} block hash")
        snaps[sid] = info

    # Prefer new N2 account_reference. If it still lacks B1/B2,
    # derive borrower addresses from asset-level positions_long.
    ref_path = repo / "data/processed/aave/account_reference.parquet"
    positions_path = repo / "data/processed/aave/positions_long.parquet"

    def historical_rows(df):
        wc, sc, dc = account_columns(df)
        if sc == "snapshot_block":
            mask = pd.to_numeric(
                df[sc], errors="coerce"
            ).isin(expected_new.values())
        else:
            mask = df[sc].astype(str).str.upper().isin(expected_new)
        return df.loc[mask].copy()

    aave = pd.read_parquet(ref_path) if ref_path.is_file() else None
    selected_source = (
        historical_rows(aave) if aave is not None else None
    )

    def contains_both(df):
        if df is None or df.empty:
            return False
        _, sc, _ = account_columns(df)
        values = set(df[sc].astype(str).str.upper())
        return (
            values == set(expected_new)
            if sc != "snapshot_block"
            else set(pd.to_numeric(df[sc])) == set(
                expected_new.values()
            )
        )

    if not contains_both(selected_source):
        if not positions_path.is_file():
            raise ValueError(
                "Need new B1/B2 account_reference or positions_long"
            )
        positions = pd.read_parquet(positions_path)
        if not {
            "wallet_address", "debt_usd", "snapshot_id"
        }.issubset(positions):
            raise ValueError(
                "N2 positions_long needs wallet_address, "
                "debt_usd and snapshot_id"
            )
        sub = positions[
            positions.snapshot_id.astype(str).isin(expected_new)
        ].copy()

        sub["debt_usd"] = pd.to_numeric(
            sub["debt_usd"], errors="coerce"
        )
        if (
            sub.empty
            or sub.debt_usd.isna().any()
            or (sub.debt_usd < 0).any()
        ):
            raise ValueError("Invalid historical N2 debt data")

        selected_source = sub.groupby(
            ["snapshot_id", "wallet_address"], as_index=False
        )["debt_usd"].sum()

        print(
            "[N2] Borrowers derived from historical positions_long"
        )

    selected_all, global_selection = pick_wallets(
        selected_source, snaps, 0, debt_only=True
    )
    if any(not selected_all[sid] for sid in expected_new):
        raise ValueError("B1 or B2 has no eligible Aave borrowers")

    markets = load_markets(ROOT / "config/n3_compound_markets.csv")
    if old_qa.get("markets") != markets:
        raise ValueError("Compound market list changed")

    # Keep each old wallet assigned to its existing shard.
    # Only previously unseen historical wallets get hashed ownership.
    all_new_wallets = sorted({
        w for pairs in selected_all.values() for w in pairs
    })
    owners = dict(owners_old)
    for wallet in all_new_wallets:
        if wallet not in owners:
            digest = hashlib.sha256(wallet.encode()).digest()
            owners[wallet] = int.from_bytes(digest[:8], "big") % 6

    selected = {
        sid: {
            w: debt for w, debt in pairs.items()
            if owners[w] == args.shard_index
        }
        for sid, pairs in selected_all.items()
    }

    count_new = sum(len(x) for x in selected.values()) * len(markets)

    print("[EXISTING SHARD]", output)
    print("[HISTORICAL BLOCKS]", expected_new)
    print("[GLOBAL NEW WALLETS]", len(all_new_wallets))
    print("[NEW QUERIES FOR THIS SHARD]", count_new)

    if args.dry_run:
        print("[DRY RUN] No RPC, no file changes")
        return

    load_dotenv(repo / ".env")
    load_dotenv(ROOT / ".env", override=False)

    rpc = (
        os.getenv("ETHEREUM_ARCHIVE_RPC_URL")
        or os.getenv("ETH_RPC_URL")
        or os.getenv("ALCHEMY_ETHEREUM_RPC_URL")
    )
    if not rpc:
        raise ValueError("Missing Ethereum archive RPC in .env")

    session = PacedSession(args.rpc_interval)
    w3 = Web3(Web3.HTTPProvider(
        rpc, request_kwargs={"timeout": 75}, session=session
    ))
    if int(w3.eth.chain_id) != 1:
        raise ValueError("RPC is not Ethereum mainnet")

    metas = {}
    for sid, info in snaps.items():
        block = w3.eth.get_block(int(info["snapshot_block"]))
        if (
            Web3.to_hex(block["hash"]).lower()
            != str(info["block_hash"]).lower()
        ):
            raise ValueError(f"On-chain block hash mismatch: {sid}")
        target = pd.Timestamp(info["snapshot_timestamp_utc"])
        if target.tzinfo is None:
            raise ValueError(f"{sid}: timestamp must have timezone")
        if abs(int(block["timestamp"]) - target.timestamp()) > 300:
            raise ValueError(f"Block timestamp mismatch: {sid}")

        for market in markets:
            m, _ = metadata_for_market(w3, info, market, sid)
            metas[(sid, market["market_id"])] = m

    market_fp = metadata_fingerprint(metas)
    selection_hash = hashlib.sha256(
        json.dumps(selected_all, sort_keys=True).encode()
    ).hexdigest()
    checkpoint_fp = hashlib.sha256(
        (market_fp + selection_hash).encode()
    ).hexdigest()

    # A different checkpoint file in the SAME old shard checkpoint folder.
    checkpoint = (
        ROOT / "data/raw/compound" / name
        / "n3_overlap_checkpoint_B1_B2.jsonl"
    )
    cache = checkpoint_load(checkpoint, checkpoint_fp)
    checkpoint.parent.mkdir(parents=True, exist_ok=True)

    failures = []
    with session as rpc_session, checkpoint.open(
        "a", encoding="utf-8"
    ) as ck:
        for sid in ("B1", "B2"):
            for market in markets:
                mid = metas[(sid, market["market_id"])]
                wallets = sorted(selected[sid])
                pending = [
                    w for w in wallets
                    if (sid, market["market_id"], w) not in cache
                ]
                print(
                    f"[FETCH] {name} {sid} {market['market_id']} "
                    f"pending={len(pending)}"
                )
                for offset in range(
                    0, len(pending), args.wallet_chunk
                ):
                    batch = pending[
                        offset:offset + args.wallet_chunk
                    ]
                    try:
                        results = fetch_accounts(
                            rpc_session, rpc, mid,
                            batch, args.batch_size
                        )
                    except Exception as exc:
                        failures.extend(batch)
                        print(
                            "[RETRY LATER]",
                            type(exc).__name__,
                            len(batch)
                        )
                        continue

                    for item in results:
                        wallet = item["wallet"]
                        if item["status"] != "ok":
                            failures.append(wallet)
                            continue

                        rec = {
                            "fingerprint": checkpoint_fp,
                            "snapshot_id": sid,
                            "market_id": market["market_id"],
                            "wallet": wallet,
                            "status": "ok",
                            "values": item["values"],
                            "aave_debt_usd":
                                float(selected[sid][wallet]),
                        }
                        ck.write(json.dumps(rec) + "\n")
                        cache[(sid, market["market_id"], wallet)] = rec

                    ck.flush()

    records = []
    for sid in ("B1", "B2"):
        for market in markets:
            for wallet in sorted(selected[sid]):
                key = (sid, market["market_id"], wallet)
                if key in cache:
                    records.append(cache[key])

    if len(records) != count_new:
        print(
            f"[INCOMPLETE] {len(records)}/{count_new}. "
            "Re-run the SAME command to resume checkpoint."
        )
        raise SystemExit(2)

    new_pos, new_acc, new_cfg, new_overlap = build_outputs(
        records, metas
    )

    filenames = {
        "positions": "n3_compound_positions_long.parquet",
        "accounts": "n3_compound_account_reference.parquet",
        "config": "n3_compound_reserve_config.parquet",
        "overlap": "n3_compound_overlap_wallets.parquet",
    }
    new_frames = {
        "positions": new_pos,
        "accounts": new_acc,
        "config": new_cfg,
        "overlap": new_overlap,
    }

    combined = {}
    for label, filename in filenames.items():
        previous = pd.read_parquet(output / filename)
        combined[label] = pd.concat(
            [previous, new_frames[label]], ignore_index=True
        )

    all_wallets = set(owners)
    assigned = {
        w for w in all_wallets
        if owners[w] == args.shard_index
    }
    old_shard = old_qa["shard"]
    old_global = old_shard["global_selection"]

    global_scope = {
        **old_global,
        "selected_unique_wallets": len(all_wallets),
        "input_aave_rows_after_filter": (
            int(old_global.get(
                "input_aave_rows_after_filter", 0
            ))
            + sum(len(x) for x in selected_all.values())
        ),
        "selection": "legacy A/B plus historical B1/B2",
        "limited_sample": False,
    }

    composite_fp = hashlib.sha256(
        (
            old_qa["metadata_fingerprint"]
            + market_fp + selection_hash
        ).encode()
    ).hexdigest()

    composite_selection = hashlib.sha256(
        (
            old_shard["selection_hash"]
            + selection_hash
        ).encode()
    ).hexdigest()

    total_new_global = (
        sum(len(x) for x in selected_all.values())
        * len(markets)
    )

    quality = {
        **old_qa,
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "snapshot_blocks": {
            **expected_old, **expected_new
        },
        "metadata_fingerprint": composite_fp,
        "selection": {
            **old_qa["selection"],
            "selected_unique_wallets": len(assigned),
            "limited_sample": False,
        },
        "shard": {
            **old_shard,
            "selection_hash": composite_selection,
            "assignment":
                "legacy_owner_plus_sha256_new_wallets_v1",
            "global_selection": global_scope,
            "global_expected_queries": (
                old_shard["global_expected_queries"]
                + total_new_global
            ),
        },
        "expected_wallet_market_snapshot_queries":
            old_qa["expected_wallet_market_snapshot_queries"]
            + count_new,
        "successful_queries":
            old_qa["successful_queries"] + count_new,
        "failed_queries": 0,
        "complete": True,
        "positions_rows": len(combined["positions"]),
        "active_compound_market_accounts":
            len(combined["accounts"]),
        "unique_overlap_wallet_snapshot_pairs":
            len(combined["overlap"]),
        "any_borrow_market_accounts": int(
            (
                combined["accounts"]["compound_borrow_usd"] > 0
            ).sum()
        ),
        "extension": {
            "added_snapshots": expected_new,
            "historical_queries_added": count_new,
            "original_AB_kept": True,
            "new_checkpoint": str(checkpoint),
            "scope": "Aave-targeted, not full Compound census",
        },
    }

    # Validate ALL FIVE candidate files before replacing originals.
    names = list(filenames.values()) + [
        "n3_compound_quality.json"
    ]

    with tempfile.TemporaryDirectory(
        prefix="compound_validate_", dir=output.parent
    ) as tmp:
        stage = Path(tmp)
        for label, filename in filenames.items():
            combined[label].to_parquet(
                stage / filename, index=False
            )
        (stage / "n3_compound_quality.json").write_text(
            json.dumps(quality, ensure_ascii=False, indent=2),
            encoding="utf-8"
        )

        module.check(stage)

        # Back up originals OUTSIDE the repository.
        stamp = datetime.now(timezone.utc).strftime(
            "%Y%m%dT%H%M%SZ"
        )
        backup = (
            Path.home() / "Downloads"
            / f"Compound_{name}_before_B1B2_{stamp}"
        )
        backup.mkdir(parents=True, exist_ok=False)

        for filename in names:
            shutil.copy2(
                output / filename, backup / filename
            )

        try:
            for filename in names:
                os.replace(
                    stage / filename, output / filename
                )
        except Exception:
            for filename in names:
                shutil.copy2(
                    backup / filename,
                    output / filename
                )
            raise

    module.check(output)
    print("[UPDATED EXISTING SHARD]", output)
    print("[SNAPSHOTS] A, B, B1, B2")
    print("[BACKUP]", backup)


def main() -> None:
    cli=argparse.ArgumentParser(description="N3 experimental targeted Compound III overlap @ Aave Snapshot A/B")
    cli.add_argument("--aave",type=Path,default=DEFAULT_AAVE)
    cli.add_argument("--snapshot-metadata",type=Path,default=DEFAULT_META)
    cli.add_argument("--markets",type=Path,default=DEFAULT_MARKETS)
    cli.add_argument("--max-wallets",type=int,default=100,help="Limit distinct Aave wallets, 0 means all; selects biggest Aave debt first")
    cli.add_argument("--include-aave-zero-debt",action="store_true")
    cli.add_argument("--batch-size",type=int,default=50,help="JSON-RPC eth_call batch size")
    cli.add_argument("--wallet-chunk",type=int,default=10,help="Progress + checkpoint after N wallets")
    cli.add_argument("--rpc-interval",type=float,default=0.0,help="Minimum seconds between HTTP RPC request starts, including metadata and retries; use batch-size 1 to limit individual calls")
    cli.add_argument("--num-shards",type=int,default=1,help="Number of team partitions")
    cli.add_argument("--shard-index",type=int,default=0,help="Partition number, starting at 0")
    cli.add_argument("--assignment-file",type=Path,help="Shared wallet assignment prepared from an existing checkpoint")
    cli.add_argument("--dry-run",action="store_true",help="Inspect Aave and config without RPC or file writes")
    cli.add_argument("--overwrite",action="store_true",help="Allow backed-up output updates")
    cli.add_argument("--reset-checkpoint",action="store_true",help="Back up checkpoint and start over (only if needed)")
    cli.add_argument("--extend-existing-shard",
                     action="store_true")
    cli.add_argument("--existing-shards-root", type=Path)
    args=cli.parse_args()
    if args.extend_existing_shard:
        return extend_existing_shard(args)
    if args.max_wallets<0 or args.batch_size<1 or args.wallet_chunk<1:cli.error("invalid limits")
    if not math.isfinite(args.rpc_interval) or args.rpc_interval < 0:
        cli.error("rpc-interval must be finite and nonnegative")
    if args.num_shards < 1 or not 0 <= args.shard_index < args.num_shards:
        cli.error("Require num-shards >= 1 and 0 <= shard-index < num-shards")
    output = OUTPUT
    cache_path = CACHE
    if args.num_shards > 1:
        shard_name = f"shard_{args.shard_index:03d}_of_{args.num_shards:03d}"
        output = OUTPUT / shard_name
        cache_path = CACHE.parent / shard_name / CACHE.name
    if not all(p.exists() for p in (args.aave,args.snapshot_metadata,args.markets)):
        cli.error("Missing input file (Aave account_reference, snapshot metadata, or Compound markets config)")
    snaps=load_snapshots(args.snapshot_metadata)
    markets=load_markets(args.markets)
    selected,selection=pick_wallets(pd.read_parquet(args.aave),snaps,args.max_wallets,not args.include_aave_zero_debt)
    selection_hash = hashlib.sha256(json.dumps(selected, sort_keys=True).encode("utf-8")).hexdigest()
    global_selection = selection.copy()
    global_expected_queries = sum(map(len, selected.values())) * len(markets)
    plan = None
    assignment = "sorted_wallet_round_robin_v1"
    if args.assignment_file:
        plan = json.loads(args.assignment_file.read_text(encoding="utf-8"))
        selected, plan_hash = assignment_partition(plan, selected, args.num_shards, args.shard_index)
        assignment = "checkpoint_assignment_v1:" + plan_hash
        plan_folder = "team_" + plan_hash[:12]
        output = output.parent / plan_folder / output.name if args.num_shards > 1 else OUTPUT / plan_folder
        cache_path = CACHE.parent / plan_folder / f"shard_{args.shard_index:03d}_of_{args.num_shards:03d}" / CACHE.name
    else:
        selected = partition_wallets(selected, args.num_shards, args.shard_index)
    shard = {"num_shards": args.num_shards, "shard_index": args.shard_index,
             "selection_hash": selection_hash, "global_selection": global_selection,
             "global_expected_queries": global_expected_queries,
             "assignment": assignment}
    if args.num_shards > 1:
        selection = {**selection, "selected_unique_wallets": len({w for pairs in selected.values() for w in pairs}),
                     "input_aave_rows_after_filter": sum(map(len, selected.values()))}
        print("[SHARD]", args.shard_index, "/", args.num_shards, "| output", output)
    print("[SCOPE]",selection, "| markets",[x["market_id"] for x in markets])
    print("[BLOCKS]",{sid:info["snapshot_block"] for sid,info in snaps.items()})
    if args.dry_run:
        print("[DRY-RUN] RPC not accessed, nothing written. This does not confirm Compound market state.")
        return
    load_dotenv(ROOT / ".env")
    rpc=os.getenv("ETHEREUM_ARCHIVE_RPC_URL") or os.getenv("ETH_RPC_URL") or os.getenv("ALCHEMY_ETHEREUM_RPC_URL")
    if not rpc:
        raise SystemExit("Missing ETHEREUM_ARCHIVE_RPC_URL in project .env; use archive-capable Ethereum Mainnet RPC")
    rpc_session = PacedSession(args.rpc_interval)
    if args.rpc_interval:
        print("[RPC PACING] minimum HTTP interval", args.rpc_interval, "seconds | batch-size", args.batch_size)
    w3=Web3(Web3.HTTPProvider(rpc,request_kwargs={"timeout":75},session=rpc_session))
    if int(w3.eth.chain_id)!=1:
        raise SystemExit("RPC is not Ethereum mainnet (chain_id != 1)")
    for sid,snap in snaps.items():
        block=w3.eth.get_block(int(snap["snapshot_block"]))
        if snap.get("block_hash") and Web3.to_hex(block["hash"]).lower() != str(snap["block_hash"]).lower():
            raise RuntimeError("Snapshot block hash mismatch: "+sid)
        expected=pd.Timestamp(snap["snapshot_timestamp_utc"]).timestamp()
        if abs(int(block["timestamp"])-expected)>300:
            raise RuntimeError("Snapshot time drift > 5min: "+sid)
    metas={}
    for sid,snap in snaps.items():
        for market in markets:
            meta,_=metadata_for_market(w3,snap,market,sid)
            metas[(sid,market["market_id"])]=meta
            print(f"[MARKET] {sid} {market['market_id']} assets={len(meta['assets'])} base={meta['base_token_symbol']}")
    fp=metadata_fingerprint(metas)
    if plan is not None and plan["metadata_fingerprint"] != fp:
        raise RuntimeError("Assignment checkpoint metadata differs from current RPC metadata; preserve checkpoint and review inputs")
    if args.reset_checkpoint and cache_path.exists():
        stamp=datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        cache_path.parent.mkdir(parents=True,exist_ok=True)
        shutil.move(cache_path,cache_path.with_name(cache_path.name+f".bak_{stamp}"))
    # Reuse work from a prior unpartitioned run on this machine, without changing it.
    cache = checkpoint_load(CACHE, fp) if args.num_shards > 1 and CACHE.exists() and not args.reset_checkpoint else {}
    cache.update(checkpoint_load(cache_path,fp))
    cache_path.parent.mkdir(parents=True,exist_ok=True)
    failures=[]
    with rpc_session as session,cache_path.open("a",encoding="utf8") as ck:
        for sid in ("A","B"):
            for market in markets:
                key=(sid,market["market_id"])
                mid=metas[key]
                wallets=list(selected[sid])
                pending=[w for w in wallets if (sid,market["market_id"],w) not in cache]
                print(f"[FETCH] {sid} {market['market_id']}: selected={len(wallets)} cached={len(wallets)-len(pending)} remaining={len(pending)}")
                for begin in range(0,len(pending),args.wallet_chunk):
                    batch=pending[begin:begin+args.wallet_chunk]
                    try:
                        got=fetch_accounts(session,rpc,mid,batch,args.batch_size)
                    except Exception as exc:
                        failures.extend({"snapshot_id":sid,"market_id":market["market_id"],"wallet":w,"error":type(exc).__name__} for w in batch)
                        print("[ERROR] RPC batch failed; checkpoint preserved; review your RPC quota")
                        continue
                    for obj in got:
                        if obj["status"]!="ok":
                            failures.append({"snapshot_id":sid,"market_id":market["market_id"],"wallet":obj["wallet"],"error":"RPC/ABI call error"})
                            continue
                        rec={"fingerprint":fp,"snapshot_id":sid,"market_id":market["market_id"],"wallet":obj["wallet"],
                             "status":"ok","values":obj["values"],"aave_debt_usd":float(selected[sid][obj["wallet"]])}
                        ck.write(json.dumps(rec,ensure_ascii=False)+"\n")
                        cache[(sid,market["market_id"],obj["wallet"])]=rec
                    ck.flush()
                    print(f"  {sid}/{market['market_id']} {min(begin+len(batch),len(pending))}/{len(pending)} new wallet queries")
    needed=sum(len(selected[sid])*len(markets) for sid in snaps)
    succeeded=sum(1 for sid in snaps for m in markets for w in selected[sid] if (sid,m["market_id"],w) in cache)
    if succeeded+len(failures)<needed:
        raise RuntimeError("Unaccounted RPC calls; aborting outputs")
    complete=succeeded==needed and len(failures)==0
    qa={"created_utc":datetime.now(timezone.utc).isoformat(),"scope":"Aave-wallet-targeted Compound III on Ethereum; not full Compound census",
        "chain_id":1,"snapshot_blocks":{k:int(v["snapshot_block"]) for k,v in snaps.items()},
        "metadata_fingerprint":fp,"shard":shard,
        "input_aave_path":str(args.aave.relative_to(ROOT)) if args.aave.is_relative_to(ROOT) else str(args.aave),
        "markets":markets,"selection":selection,"expected_wallet_market_snapshot_queries":needed,
        "successful_queries":succeeded,"failed_queries":len(failures),"complete":complete,"failures_preview":failures[:30],
        "method":"historical eth_call via Comet proxy; getPrice at snapshot; all supported collateral assets checked",
        "limitations":["Aave-targeted wallet sample (not all Compound users)",
                       "Uses Comet isLiquidatable, NOT Aave HF", "Cross-protocol participation is an exposure link, NOT evidence of contagion",
                       "No historical lending-event path proof from snapshots alone", "Sampling by Aave debt causes selection bias if max-wallets > 0"]}
    if not complete:
        print(f"[INCOMPLETE] {succeeded}/{needed} wallet-market-snapshot results. Checkpoints saved; rerun to resume.")
        err_dest=output/"n3_compound_failed_queries.json"
        save_json(qa,err_dest,overwrite=True)
        raise SystemExit(2)
    records=[cache[(sid,m["market_id"],w)] for sid in snaps for m in markets for w in selected[sid]]
    pos,acc,cfg,overlap=build_outputs(records,metas)
    qa.update({"positions_rows":len(pos),"active_compound_market_accounts":len(acc),
               "unique_overlap_wallet_snapshot_pairs":len(overlap),"any_borrow_market_accounts":int((acc.compound_borrow_usd>0).sum()) if not acc.empty else 0,
               "outcome":"pass_complete_for_selected_Aave_wallets"})
    destinations={"n3_compound_positions_long.parquet":pos,"n3_compound_account_reference.parquet":acc,
                  "n3_compound_reserve_config.parquet":cfg,"n3_compound_overlap_wallets.parquet":overlap}
    for n in [*destinations, "n3_compound_quality.json"]:
        if (output/n).exists() and not args.overwrite:
            raise FileExistsError(f"Output {n} exists; rerun --overwrite only when ready")
    for filename,frame in destinations.items():
        save_parquet(frame,output/filename,args.overwrite)
        print(f"[SAVED] {filename} rows={len(frame)}")
    save_json(qa,output/"n3_compound_quality.json",args.overwrite)
    print("[QA] COMPLETE",succeeded,"/",needed,"queries | overlap wallet-snapshot pairs",len(overlap))


if __name__=="__main__":
    main()
