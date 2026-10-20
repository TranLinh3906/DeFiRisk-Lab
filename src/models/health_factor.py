"""Build Aave borrower health factors using the project's storage-safe keys."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
PROCESSED = ROOT / "data/processed/aave"
MARTS = ROOT / "data/marts"
PROTOCOL_ID = "aave_v3_ethereum"
POSITION_KEYS = ["snapshot_block", "protocol_id", "wallet_address", "token_address"]
SUMMARY_KEYS = POSITION_KEYS[:-1]
RENAME_ALIASES = {"wallet": "wallet_address", "snapshot_id": "snapshot_block", "token": "token_address"}
LST_SYMBOLS = {"WSTETH", "WEETH", "CBETH"}


def normalize_input(frame: pd.DataFrame) -> pd.DataFrame:
    """Apply Data Contract key names and add the known Aave protocol identifier.

    When both snapshot_id and snapshot_block exist, the numeric block column is
    retained. This preserves the canonical block key in current staging files.
    """
    result = frame.copy()
    for alias, canonical in RENAME_ALIASES.items():
        if alias in result.columns:
            if canonical not in result.columns:
                result = result.rename(columns={alias: canonical})
            else:
                result = result.drop(columns=[alias])
    if "protocol_id" not in result.columns:
        result["protocol_id"] = PROTOCOL_ID
    if "snapshot_block" not in result.columns:
        raise ValueError("Input requires snapshot_block or snapshot_id")
    if "token_address" in result:
        result["token_address"] = result["token_address"].astype("string").str.strip().str.lower()
    if "wallet_address" in result:
        result["wallet_address"] = result["wallet_address"].astype("string").str.strip().str.lower()
    result["protocol_id"] = result["protocol_id"].astype("string").str.strip()
    # The official contract defines snapshot_block as int64. Legacy A/B labels
    # are translated through the project's snapshot metadata when available.
    if not pd.api.types.is_numeric_dtype(result["snapshot_block"]):
        metadata_path = PROCESSED / "snapshot_metadata.json"
        if metadata_path.exists():
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            snapshots = metadata.get("snapshots", {})
            block_by_label = {label: info.get("snapshot_block", info.get("block"))
                              for label, info in snapshots.items()}
            mapped = result["snapshot_block"].map(block_by_label)
            if mapped.notna().all():
                result["snapshot_block"] = mapped
    result["snapshot_block"] = pd.to_numeric(result["snapshot_block"], errors="raise").astype("int64")
    return result


def _num(series: pd.Series, default: float = 0.0) -> pd.Series:
    """Return finite numeric values with a safe missing-value default."""
    return pd.to_numeric(series, errors="coerce").replace([np.inf, -np.inf], np.nan).fillna(default)


def _as_bool(series: pd.Series) -> pd.Series:
    """Parse true/false flags robustly, including nullable string columns."""
    if pd.api.types.is_bool_dtype(series):
        return series.fillna(False)
    return series.astype("string").str.strip().str.lower().isin({"true", "1", "yes", "y"})


def calculate_health_factor(
    positions_df: pd.DataFrame, config_df: pd.DataFrame, prices_df: pd.DataFrame
) -> pd.DataFrame:
    """Calculate wallet risk metrics using normalized Data Contract keys.

    LTs can be fractions (``lt_std``/``lt_emode``) or basis points
    (``lt_bps``/``emode_lt_bps``). Missing amounts/prices/LTs are safely zeroed.
    """
    pos, cfg, px = (normalize_input(frame) for frame in (positions_df, config_df, prices_df))
    pos = pos.rename(columns={"wallet_address": "wallet_address"})
    if "wallet_address" not in pos:
        raise ValueError("positions_df requires wallet_address (or wallet alias)")
    if "collateral_flag" not in pos:
        for alias in ("is_collateral_enabled", "coll_flag", "usage_as_collateral"):
            if alias in pos:
                pos = pos.rename(columns={alias: "collateral_flag"})
                break
    required = set(POSITION_KEYS + ["collateral_amount", "debt_amount"])
    missing = required - set(pos.columns)
    if missing:
        raise ValueError(f"positions_df missing required columns: {sorted(missing)}")
    if not set(POSITION_KEYS[:2] + ["token_address"]).issubset(cfg.columns):
        raise ValueError("config_df requires snapshot_block, protocol_id and token_address")
    if not set(POSITION_KEYS[:2] + ["token_address", "price_usd"]).issubset(px.columns):
        raise ValueError("prices_df requires snapshot_block, protocol_id, token_address and price_usd")

    # Keep official composite keys on every join; deduplicate dimensions first.
    token_keys = POSITION_KEYS[:2] + ["token_address"]
    cfg_cols = token_keys + [c for c in (
        "lt_std", "lt_bps", "lt_emode", "emode_lt_bps", "emode_category", "symbol",
        "usage_as_collateral", "is_collateral_enabled",
    ) if c in cfg.columns]
    cfg = cfg[cfg_cols].drop_duplicates(subset=token_keys, keep="first")
    pos = pos.merge(cfg, on=token_keys, how="left", suffixes=("", "_config"))
    px_dim = px[token_keys + ["price_usd"]].drop_duplicates(subset=token_keys, keep="first")
    pos = pos.merge(px_dim, on=token_keys, how="left")

    flag_col = next((c for c in ("collateral_flag", "is_collateral_enabled", "coll_flag", "usage_as_collateral") if c in pos), None)
    collateral_enabled = _as_bool(pos[flag_col]) if flag_col else pd.Series(False, index=pos.index)
    # The user-level on-chain flag in positions is authoritative when present.
    for c in ("collateral_flag", "coll_flag"):
        if c in pos:
            collateral_enabled = _as_bool(pos[c])
            break
    pos["collateral_amount"] = _num(pos["collateral_amount"])
    pos["debt_amount"] = _num(pos["debt_amount"])
    pos["price_usd"] = _num(pos["price_usd"])
    pos["collateral_usd"] = np.where(collateral_enabled, pos.collateral_amount * pos.price_usd, 0.0)
    pos["debt_usd"] = pos.debt_amount * pos.price_usd

    std = _num(pos.get("lt_std", pd.Series(np.nan, index=pos.index)), np.nan)
    if "lt_std" not in pos or std.isna().all():
        std = _num(pos.get("lt_bps", pd.Series(0.0, index=pos.index))) / 10_000
    else:
        std = std.where(std <= 1, std / 10_000)
    em = _num(pos.get("lt_emode", pd.Series(np.nan, index=pos.index)), np.nan)
    if "lt_emode" not in pos or em.isna().all():
        em = _num(pos.get("emode_lt_bps", pd.Series(np.nan, index=pos.index)), np.nan) / 10_000
    else:
        em = em.where(em <= 1, em / 10_000)

    # A per-position user category may be present in positions; membership
    # category is attached from config under the suffixed name after the join.
    if "emode_category_user" in pos:
        user_category = _num(pos["emode_category_user"])
    elif "user_emode" in pos:
        user_category = _num(pos["user_emode"])
    elif "emode_category" in pos:
        user_category = _num(pos["emode_category"])
    else:
        user_category = pd.Series(0.0, index=pos.index)
    member_category = _num(pos.get("emode_category_config", pos.get("emode_category", pd.Series(0, index=pos.index))))
    effective = np.where(user_category.eq(1) & member_category.eq(1) & em.notna(), em.fillna(0), std.fillna(0))
    pos["weighted_collateral_lt_usd"] = pos["collateral_usd"] * np.clip(effective, 0, 1)

    symbols = pos.get("symbol", pos.get("token_symbol", pd.Series("", index=pos.index)))
    symbols = symbols.astype("string").str.strip().str.upper()
    pos["_lst"] = symbols.isin(LST_SYMBOLS) & collateral_enabled & pos.collateral_amount.gt(0)
    pos["_weth_debt"] = symbols.eq("WETH") & pos.debt_amount.gt(0)
    pos["_user_emode"] = user_category
    risk = pos.groupby(SUMMARY_KEYS, as_index=False, dropna=False).agg(
        total_collateral_usd=("collateral_usd", "sum"),
        total_debt_usd=("debt_usd", "sum"),
        weighted_collateral_lt_usd=("weighted_collateral_lt_usd", "sum"),
        emode_category=("_user_emode", "max"),
        has_lst_collateral=("_lst", "any"),
        has_weth_debt=("_weth_debt", "any"),
    )
    risk["health_factor"] = np.where(risk.total_debt_usd.gt(0),
                                     risk.weighted_collateral_lt_usd / risk.total_debt_usd, 999.0)
    risk["health_factor"] = risk.health_factor.replace([np.inf, -np.inf], 999.0).fillna(999.0)
    risk["risk_category"] = np.select(
        [risk.health_factor.ge(1.10), risk.health_factor.ge(1.0)], ["Safe", "Warning"], default="High Risk"
    )
    denominator = risk.total_collateral_usd - risk.total_debt_usd
    leverage = risk.total_collateral_usd.div(denominator.where(denominator.ne(0)))
    risk["is_recursive_loop"] = (risk.emode_category.eq(1) & risk.has_lst_collateral &
                                  risk.has_weth_debt & leverage.ge(3.0)).fillna(False)
    risk["emode_category"] = risk.emode_category.fillna(0).astype("int64")
    return risk[SUMMARY_KEYS + ["total_collateral_usd", "total_debt_usd", "weighted_collateral_lt_usd",
                                "health_factor", "risk_category", "is_recursive_loop", "emode_category"]]


def build_overview(risk_df: pd.DataFrame) -> dict[str, Any]:
    """Summarize unique wallets and risk exposure across all snapshot blocks."""
    at_risk = risk_df.health_factor.lt(1.0)
    return {
        "total_wallets": int(risk_df.wallet_address.nunique()),
        "total_collateral_usd": float(risk_df.total_collateral_usd.sum()),
        "total_debt_usd": float(risk_df.total_debt_usd.sum()),
        "total_positions_at_risk_count": int(risk_df.loc[at_risk, "wallet_address"].nunique()),
        "total_debt_at_risk_usd": float(risk_df.loc[at_risk, "total_debt_usd"].sum()),
        "looping_wallets_count": int(risk_df.loc[risk_df.is_recursive_loop, "wallet_address"].nunique()),
    }


def main() -> None:
    """Load processed Aave inputs, normalize contract keys, and write the mart."""
    position_path = PROCESSED / "borrower_positions.parquet"
    if not position_path.exists():
        position_path = PROCESSED / "positions_long.parquet"
    positions = pd.read_parquet(position_path)
    config = pd.read_parquet(PROCESSED / "reserve_config.parquet")
    prices = pd.read_parquet(PROCESSED / "oracle_prices.parquet")

    # Current extractor includes block numbers but also legacy A/B IDs. Map IDs
    # to blocks only if the official block column is absent.
    positions, config, prices = (normalize_input(d) for d in (positions, config, prices))
    # Attach E-Mode user category from account reference on the official wallet keys.
    account_path = PROCESSED / "account_reference.parquet"
    if account_path.exists():
        account = normalize_input(pd.read_parquet(account_path))
        account = account.rename(columns={"wallet_address": "wallet_address"})
        if "emode_category_user" in account:
            emode_users = account[SUMMARY_KEYS + ["emode_category_user"]].drop_duplicates(
                subset=SUMMARY_KEYS, keep="first"
            )
            positions = positions.merge(emode_users, on=SUMMARY_KEYS, how="left")
    # Membership provides LT for category 1; standard config supplies lt_std.
    membership_path = PROCESSED / "emode_membership.parquet"
    if membership_path.exists():
        membership = normalize_input(pd.read_parquet(membership_path))
        lt_col = next((c for c in ("lt_emode", "emode_lt", "emode_lt_bps") if c in membership), None)
        if lt_col:
            em_cols = [c for c in ("snapshot_block", "protocol_id", "token_address", "emode_category", lt_col) if c in membership]
            membership_keys = ["snapshot_block", "protocol_id", "token_address"]
            membership_dim = membership[em_cols].drop_duplicates(subset=membership_keys, keep="first")
            config = config.drop_duplicates(subset=membership_keys, keep="first")
            config = config.merge(membership_dim.rename(columns={lt_col: "lt_emode"}),
                                  on=membership_keys, how="left", suffixes=("", "_membership"))
            if "emode_category_membership" in config:
                config["emode_category"] = config["emode_category_membership"]

    risk = calculate_health_factor(positions, config, prices)
    MARTS.mkdir(parents=True, exist_ok=True)
    output_columns = SUMMARY_KEYS + ["total_collateral_usd", "total_debt_usd", "health_factor",
                                     "risk_category", "is_recursive_loop", "emode_category"]
    risk[output_columns].to_parquet(MARTS / "borrower_risk.parquet", index=False)
    (MARTS / "overview_metrics.json").write_text(
        json.dumps(build_overview(risk), indent=2, ensure_ascii=False, allow_nan=False), encoding="utf-8"
    )
    print(f"Wrote {MARTS / 'borrower_risk.parquet'} ({len(risk):,} wallet snapshots)")
    print(f"Wrote {MARTS / 'overview_metrics.json'}")


if __name__ == "__main__":
    main()
