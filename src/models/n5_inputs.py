"""Strict, read-only adapters for N2/N4 tables. No RPC, dotenv or path discovery."""
from __future__ import annotations

from dataclasses import dataclass
import math
from pathlib import Path

from src.models.cascade import Asset, Wallet


def read_rows(path: str | Path) -> list[dict]:
    """Read an explicitly selected Parquet file; never write to its directory."""
    import pyarrow.parquet as pq
    return pq.read_table(path).to_pylist()


def resolve_account_reference(directory, required_snapshots=None):
    """Select one complete snapshot-matching source, never merge legacy labels.

    N3's old A/B recovery keeps A/B1/B2 in account_referenceAB1B2.parquet.
    Validate label AND block before falling back; no source renaming or writes.
    """
    import json
    import pyarrow.parquet as pq
    directory=Path(directory)
    if required_snapshots is None:
        metadata=json.loads((directory/'snapshot_metadata.json').read_text(encoding='utf-8'))
        required_snapshots={label:snap['snapshot_block'] for label,snap in metadata['snapshots'].items()}
    errors=[]
    for name in ['account_reference.parquet','account_referenceAB1B2.parquet']:
        path=directory/name
        if not path.is_file():continue
        rows=pq.read_table(path,columns=['snapshot_id','snapshot_block']).to_pylist()
        observed={}
        for row in rows:observed.setdefault(row['snapshot_id'],set()).add(row['snapshot_block'])
        if all(observed.get(label)=={block} for label,block in required_snapshots.items()):
            return path
        errors.append(f'{name}: {observed}')
    raise ValueError('No complete account reference matches configured snapshots: '+ '; '.join(errors))


def number(value, name, low=0.0, high=math.inf) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{name}: boolean is not a number")
    try:
        value = float(value)
    except (ValueError, TypeError):
        raise ValueError(f"{name}: missing or invalid number") from None
    if not math.isfinite(value) or not low <= value <= high:
        raise ValueError(f"{name}: outside [{low}, {high}]")
    return value


def flag(value, name) -> bool:
    if isinstance(value, bool):
        return value
    if value in (0, 1, "0", "1", "true", "false", "True", "False"):
        return value in (1, "1", "true", "True")
    raise ValueError(f"{name}: explicit boolean required")


def pick(row, *names):
    for name in names:
        if name in row and row[name] is not None:
            return row[name]
    raise ValueError(f"Missing column/value: {' / '.join(names)}")


def canonical_rows(rows, block: int, protocol="aave_v3_ethereum"):
    """Select exactly one numeric block; do not guess A/B label mappings."""
    selected = []
    for original in rows:
        raw = pick(original, "snapshot_block")
        numeric = number(raw, "snapshot_block", 1)
        if numeric != int(numeric):
            raise ValueError("snapshot_block must be an integer")
        if int(numeric) != block:
            continue
        row = dict(original)
        if row.get("protocol_id", protocol) != protocol:
            raise ValueError("Mixed or unsupported protocol in selected snapshot")
        row["protocol_id"] = protocol
        for target, aliases in (("wallet_address", ("wallet_address", "wallet")),
                                ("token_address", ("token_address", "token"))):
            if any(a in row for a in aliases):
                value = str(pick(row, *aliases)).strip().lower()
                if not value:
                    raise ValueError(f"Empty {target}")
                row[target] = value
        selected.append(row)
    if not selected:
        raise ValueError(f"No rows at snapshot_block={block}")
    return selected


def unique_index(rows, keys):
    result = {}
    for row in rows:
        key = tuple(pick(row, k) for k in keys)
        if key in result:
            raise ValueError(f"Duplicate key {keys}: {key}")
        result[key] = row
    return result


@dataclass
class PreparedPortfolio:
    wallets: list[Wallet]
    assets: dict[str, Asset]
    positions: list[dict]
    snapshot_block: int
    protocol_id: str
    symbols: dict[str, str]
    assumptions: list[str]


def prepare_portfolio(positions, reserves, prices, *, snapshot_block: int,
                      depth_usd: dict[str, float], emode_membership=None,
                      protocol_id="aave_v3_ethereum") -> PreparedPortfolio:
    """Use normalized token amounts and independently reconstruct effective LT/bonus.

    Depths are explicit assumptions, keyed by token address. E-Mode accounts
    require the membership table (absence of a token there means standard LT).
    This adapter prepares inputs, it does not certify research validation gates.
    """
    if isinstance(snapshot_block, bool) or not isinstance(snapshot_block, int) or snapshot_block < 1:
        raise ValueError("snapshot_block must be a positive integer")
    pos = canonical_rows(positions, snapshot_block, protocol_id)
    cfg = unique_index(canonical_rows(reserves, snapshot_block, protocol_id), ["token_address"])
    px = unique_index(canonical_rows(prices, snapshot_block, protocol_id), ["token_address"])
    unique_index(pos, ["wallet_address", "token_address"])
    em = None
    if emode_membership is not None:
        em = unique_index(canonical_rows(emode_membership, snapshot_block, protocol_id),
                          ["emode_category", "token_address"])
    wallets, assets, symbols, normalized, categories = {}, {}, {}, [], {}
    for row in pos:
        token, wallet = pick(row, "token_address"), pick(row, "wallet_address")
        if (token,) not in cfg or (token,) not in px:
            raise ValueError(f"Missing reserve/price for {token}")
        reserve = cfg[token,]
        if "decimals_matches_state_check" in reserve and not flag(reserve["decimals_matches_state_check"], "decimals check"):
            raise ValueError(f"Reserve decimals not verified: {token}")
        if "decimals" in row and "decimals" in reserve:
            if number(row["decimals"], "position decimals", 0, 255) != number(reserve["decimals"], "reserve decimals", 0, 255):
                raise ValueError(f"Decimals mismatch: {token}")
        price = number(pick(px[token,], "price_usd", "price_usd_t0"), "price_usd", 1e-300)
        if "price_usd" in row and not math.isclose(number(row["price_usd"], "position price", 1e-300), price, rel_tol=1e-9):
            raise ValueError(f"Embedded position price disagrees with oracle: {token}")
        lt = (number(reserve["lt_std"], "lt_std", 0, 1) if "lt_std" in reserve
              else number(pick(reserve, "lt_bps"), "lt_bps", 0, 10000) / 10000)
        if "liq_bonus_std_premium" in reserve:
            bonus = number(reserve["liq_bonus_std_premium"], "bonus premium", 0, 1)
        else:
            raw_bonus = number(pick(reserve, "liq_bonus_raw_bps"), "bonus bps", 0, 20000)
            if 0 < raw_bonus < 10000:
                raise ValueError("Raw bonus must be zero or a multiplier >=10000 bps")
            bonus = raw_bonus / 10000 - 1 if raw_bonus else 0.0
        collateral = number(pick(row, "collateral_amount", "collateral_amount_norm"), "collateral_amount")
        debt = number(pick(row, "debt_amount", "debt_amount_norm"), "debt_amount")
        enabled = flag(pick(row, "collateral_enabled", "is_collateral_enabled", "coll_flag", "collateral_flag"), "collateral flag")
        category = number(pick(row, "user_emode", "emode_category_user", "emode_category"), "emode category")
        if category != int(category):
            raise ValueError("E-Mode category must be integral")
        if wallet in categories and categories[wallet] != category:
            raise ValueError(f"Inconsistent E-Mode for {wallet}")
        categories[wallet] = category
        effective_lt, effective_bonus = lt, bonus
        if category:
            if em is None:
                raise ValueError("E-Mode accounts require explicit emode_membership")
            member = em.get((int(category), token))
            if member and flag(pick(member, "is_collateral"), "emode is_collateral"):
                effective_lt = number(pick(member, "emode_lt_bps"), "emode LT bps", 0, 10000) / 10000
                effective_bonus = number(pick(member, "emode_bonus_premium"), "emode bonus", 0, 1)
        # Existing derived columns are evidence to cross-check, never overrides.
        for name, scale in (("lt_eff", 10000), ("effective_lt", 1)):
            if name in row and not math.isclose(number(row[name], name) / scale, effective_lt, abs_tol=1e-9):
                raise ValueError(f"{name} disagrees with reconstructed LT: {wallet}/{token}")
        enabled = enabled and effective_lt > 0
        if token not in assets:
            depth = number(depth_usd.get(token), f"depth_usd[{token}]", 1e-300)
            assets[token] = Asset(price, lt, bonus, depth)
            symbols[token] = str(pick(reserve, "symbol", "token_symbol"))
        w = wallets.setdefault(wallet, Wallet(wallet, {}, {}))
        w.collateral[token], w.debt[token] = collateral, debt
        w.collateral_enabled[token] = enabled
        w.effective_lt[token], w.effective_bonus[token] = effective_lt, effective_bonus
        normalized.append(dict(snapshot_block=snapshot_block, protocol_id=protocol_id,
                               wallet_address=wallet, token_address=token, collateral_amount=collateral,
                               debt_amount=debt, is_collateral_enabled=enabled, effective_lt=effective_lt,
                               effective_bonus=effective_bonus, price_usd=price, symbol=symbols[token]))
    return PreparedPortfolio(list(wallets.values()), assets, normalized, snapshot_block, protocol_id, symbols,
                             ["Token amounts already include decimals and accrued interest; no rescaling.",
                              "DEX depth is supplied explicitly, not independently calibrated.",
                              "Isolation constraints/protocol execution rules are not fully emulated.",
                              "Inputs are unvalidated until independent validation gates pass."])
