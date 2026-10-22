#!/usr/bin/env python3
"""Fail-closed QA for N3 Compound III overlap research exports (no RPC)."""
from pathlib import Path
import json
import math
import sys
import pandas as pd

ROOT=Path(__file__).resolve().parents[1]
BASE=ROOT/"data/processed/compound"
FILES={
    "positions":BASE/"n3_compound_positions_long.parquet",
    "accounts":BASE/"n3_compound_account_reference.parquet",
    "config":BASE/"n3_compound_reserve_config.parquet",
    "overlap":BASE/"n3_compound_overlap_wallets.parquet",
    "quality":BASE/"n3_compound_quality.json",
}


def check(base: Path = BASE) -> None:
    files = {label: base / path.name for label, path in FILES.items()}
    _check(files)


def _check(FILES: dict) -> None:
    for label,loc in FILES.items():
        if not loc.exists():
            raise RuntimeError(f"MISSING {label}: {loc}")
    q=json.loads(FILES["quality"].read_text(encoding="utf8"))
    if not q.get("complete") or q.get("successful_queries")!=q.get("expected_wallet_market_snapshot_queries") or q.get("failed_queries")!=0:
        raise AssertionError("Compound RPC coverage NOT COMPLETE for declared sample")
    pos=pd.read_parquet(FILES["positions"])
    acc=pd.read_parquet(FILES["accounts"])
    cfg=pd.read_parquet(FILES["config"])
    ov=pd.read_parquet(FILES["overlap"])
    keys_acc=["snapshot_block","comet_address","wallet_address"]
    keys_pos=keys_acc+["token_address","asset_role"]
    keys_ov=["snapshot_block","wallet_address"]
    for label,df,keys in (("positions",pos,keys_pos),("accounts",acc,keys_acc),("overlap",ov,keys_ov),
                          ("reserve_config",cfg,["snapshot_block","comet_address","token_address"])):
        miss=set(keys)-set(df.columns)
        if miss:raise AssertionError(f"{label}: missing PK {miss}")
        if df.duplicated(keys).any():raise AssertionError(f"{label}: duplicate PK")
    if len(pos)!=q.get("positions_rows") or len(acc)!=q.get("active_compound_market_accounts") or len(ov)!=q.get("unique_overlap_wallet_snapshot_pairs"):
        raise AssertionError("Row counts mismatch QA JSON")
    if not pos.empty:
        for c in ("amount_token","price_usd","value_usd"):
            num=pd.to_numeric(pos[c],errors="coerce")
            if num.isna().any() or not num.map(math.isfinite).all() or (num<=0).any():
                raise AssertionError("Invalid value in positions "+c)
        discrepancy=(pos["amount_token"]*pos["price_usd"]-pos["value_usd"]).abs()
        if (discrepancy>0.02).any():
            raise AssertionError("USD conversion discrepancy > 0.02")
    if len(acc):
        roles={"base_borrow":"compound_borrow_usd","collateral":"compound_collateral_usd","base_supply":"compound_base_supply_usd"}
        for role,column in roles.items():
            sub=pos.loc[pos.asset_role.eq(role)].groupby(keys_acc)["value_usd"].sum()
            a=acc.set_index(keys_acc)
            actual=sub.reindex(a.index,fill_value=0)
            delta=(actual-a[column]).abs()
            if (delta>0.02).any():
                raise AssertionError(f"{column}: mismatch from positions")
        expected=acc.groupby(keys_ov)["market_id"].nunique()
        got=ov.set_index(keys_ov)["compound_markets_count"]
        if not expected.sort_index().equals(got.sort_index()):
            raise AssertionError("Overlap market counts mismatch")
        if not (acc["is_cross_protocol"]==True).all():
            raise AssertionError("Cross-protocol flag invalid")
    if len(cfg)==0:
        raise AssertionError("Missing Compound market config")
    print(f"[PASS] Compound Q3 overlap QA | positions={len(pos)} accounts={len(acc)} overlap={len(ov)}")
    if q.get("selection",{}).get("limited_sample"):
        print("[WARNING] TOP-DEBT SUBSAMPLE, NOT REPRESENTATIVE AAVE/COMPOUND COVERAGE")
    print("[NOTICE] Compound overlap is an exposure bridge; contagion unproven")


if __name__=="__main__":
    try:check(Path(sys.argv[1]) if len(sys.argv) > 1 else BASE)
    except Exception as e:
        print("[FAIL]",type(e).__name__,str(e),file=sys.stderr)
        raise SystemExit(1)
