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
LST_SYMBOLS = {"STETH", "WSTETH", "WEETH", "CBETH", "RETH", "ANKRETH", "SETH2"}


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
    positions_df: pd.DataFrame,
    reserve_config: pd.DataFrame,
    prices_df: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """Calculate wallet risk metrics using normalized Data Contract keys.

    LTs can be fractions (``lt_std``/``lt_emode``) or basis points
    (``lt_bps``/``emode_lt_bps``). Missing amounts/prices/LTs are safely zeroed.
    """
    pos = normalize_input(positions_df)
    cfg = normalize_input(reserve_config)
    if prices_df is None:
        # Dashboard callers may pass two frames when prices are already carried
        # by the position mart or reserve config.
        if "price_usd" in pos:
            px = pos.copy()
        elif "price_usd_t0" in pos:
            px = pos.rename(columns={"price_usd_t0": "price_usd"}).copy()
        elif "price_usd" in cfg:
            px = cfg.copy()
        else:
            raise ValueError("prices_df is required unless positions/config contain price_usd")
    else:
        px = normalize_input(prices_df)
    pos = pos.rename(columns={"wallet_address": "wallet_address"})
    if "wallet_address" not in pos:
        raise ValueError("positions_df requires wallet_address (or wallet alias)")
    if "collateral_flag" not in pos:
        for alias in ("collateral_enabled", "is_collateral_enabled", "coll_flag", "usage_as_collateral"):
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

    # Preserve the wallet's E-Mode category before joining the reserve category.
    user_mode_col = next(
        (name for name in ("emode_category_user", "user_emode", "emode_category") if name in pos),
        None,
    )
    pos["_user_emode_category"] = _num(pos[user_mode_col]) if user_mode_col else 0
    if "emode_category" in cfg and "emode_category" in pos:
        cfg = cfg.rename(columns={"emode_category": "emode_category_config"})

    # Keep official composite keys on every join; deduplicate dimensions first.
    token_keys = POSITION_KEYS[:2] + ["token_address"]
    cfg_cols = token_keys + [c for c in (
        "lt_std", "lt_bps", "symbol",
        "usage_as_collateral", "is_collateral_enabled",
    ) if c in cfg.columns]
    cfg = cfg[cfg_cols].drop_duplicates(subset=token_keys, keep="first")
    pos = pos.merge(cfg, on=token_keys, how="left", suffixes=("", "_config"))
    px_dim = px[token_keys + ["price_usd"]].drop_duplicates(subset=token_keys, keep="first")
    # Oracle is authoritative whenever supplied. Do not let embedded/stale t0
    # prices silently override it; missing Oracle observations are errors for
    # rows with an active collateral or debt balance.
    pos = pos.drop(columns=["price_usd"], errors="ignore").merge(px_dim, on=token_keys, how="left")

    flag_col = next((c for c in ("collateral_flag", "is_collateral_enabled", "coll_flag", "usage_as_collateral") if c in pos), None)
    collateral_enabled = _as_bool(pos[flag_col]) if flag_col else pd.Series(False, index=pos.index)
    # The user-level on-chain flag in positions is authoritative when present.
    for c in ("collateral_flag", "coll_flag"):
        if c in pos:
            collateral_enabled = _as_bool(pos[c])
            break
    pos["collateral_amount"] = _num(pos["collateral_amount"])
    pos["debt_amount"] = _num(pos["debt_amount"])
    raw_price = pd.to_numeric(pos["price_usd"], errors="coerce")
    exposed = (pos["collateral_amount"].gt(0) & collateral_enabled) | pos["debt_amount"].gt(0)
    invalid_price = exposed & (raw_price.isna() | ~np.isfinite(raw_price) | raw_price.le(0))
    if invalid_price.any():
        raise ValueError("Oracle price is missing or non-positive for an active position")
    pos["price_usd"] = _num(raw_price)
    pos["collateral_usd"] = np.where(collateral_enabled, pos.collateral_amount * pos.price_usd, 0.0)
    pos["debt_usd"] = pos.debt_amount * pos.price_usd

    def lt_fraction(frame: pd.DataFrame, fraction_col: str, bps_col: str) -> pd.Series:
        """Read LT as a fraction or basis points and invalidate out-of-range values."""
        if fraction_col in frame:
            raw = _num(frame[fraction_col], np.nan)
        else:
            raw = pd.Series(np.nan, index=frame.index, dtype="float64")
        raw = raw.where(raw <= 1.0, raw / 10_000.0)
        raw = raw.where(raw.between(0.0, 1.0))
        if bps_col in frame:
            fallback = _num(frame[bps_col], np.nan) / 10_000.0
            fallback = fallback.where(fallback.between(0.0, 1.0))
            raw = raw.fillna(fallback)
        return raw

    std = lt_fraction(pos, "lt_std", "lt_bps").fillna(0.0)
    user_category = _num(pos["_user_emode_category"])
    # E-Mode membership is category-specific. Preserve all categories for a
    # token and join on the wallet's category rather than selecting one row.
    cfg_source = normalize_input(reserve_config)
    membership_flag = next((c for c in ("is_collateral", "collateral_enabled") if c in cfg_source), None)
    membership_source = cfg_source
    if membership_flag:
        membership_source = cfg_source.loc[_as_bool(cfg_source[membership_flag])].copy()
    membership = membership_source[[c for c in token_keys + ["emode_category", "emode_category_config", "lt_emode", "emode_lt_bps"] if c in membership_source]].copy()
    if "emode_category_config" not in membership and "emode_category" in membership:
        membership = membership.rename(columns={"emode_category": "emode_category_config"})
    if "emode_category_config" in membership:
        if "lt_emode" not in membership and "emode_lt_bps" in membership:
            membership["lt_emode"] = pd.to_numeric(membership["emode_lt_bps"], errors="coerce") / 10_000.0
        membership = membership.drop_duplicates(subset=token_keys + ["emode_category_config"], keep="first")
        pos = pos.drop(columns=[c for c in ("lt_emode", "emode_lt_bps", "emode_category_config") if c in pos], errors="ignore")
        pos = pos.merge(membership, left_on=token_keys + ["_user_emode_category"],
                        right_on=token_keys + ["emode_category_config"], how="left", suffixes=("", "_membership"))
    else:
        pos["emode_category_config"] = np.nan
        pos["lt_emode"] = np.nan
    em = lt_fraction(pos, "lt_emode", "emode_lt_bps")
    member_category = _num(pos.get("emode_category_config", pd.Series(0, index=pos.index)))
    emode_lt_valid = em.notna() & em.gt(0.0)
    use_emode_lt = user_category.gt(0) & user_category.eq(member_category) & emode_lt_valid
    effective = np.where(use_emode_lt, em.fillna(0.0), std)
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
    """Summarize wallets and risk exposure separately for each snapshot block."""
    snapshots: dict[str, Any] = {}
    for block, snap in risk_df.groupby("snapshot_block", sort=True):
        at_risk = snap["health_factor"].lt(1.0)
        snapshots[str(int(block))] = {
            "snapshot_block": int(block),
            "total_wallets": int(snap["wallet_address"].nunique()),
            "total_collateral_usd": float(snap["total_collateral_usd"].sum()),
            "total_debt_usd": float(snap["total_debt_usd"].sum()),
            "total_positions_at_risk_count": int(snap.loc[at_risk, "wallet_address"].nunique()),
            "total_debt_at_risk_usd": float(snap.loc[at_risk, "total_debt_usd"].sum()),
            "looping_wallets_count": int(snap.loc[snap["is_recursive_loop"], "wallet_address"].nunique()),
        }
    return {"snapshots": snapshots}


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
    # Keep every membership category; only standard reserve config is reduced
    # to one row per token by calculate_health_factor.
    membership_path = PROCESSED / "emode_membership.parquet"
    if membership_path.exists():
        membership = normalize_input(pd.read_parquet(membership_path))
        lt_col = next((c for c in ("lt_emode", "emode_lt", "emode_lt_bps") if c in membership), None)
        if lt_col:
            em_cols = [c for c in ("snapshot_block", "protocol_id", "token_address", "emode_category", lt_col, "is_collateral", "collateral_enabled") if c in membership]
            membership_dim = membership[em_cols].rename(columns={lt_col: "lt_emode"})
            # Concatenate to make category membership available to the function
            # without collapsing categories for a repeated token.
            config = pd.concat([config, membership_dim], ignore_index=True, sort=False)

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
