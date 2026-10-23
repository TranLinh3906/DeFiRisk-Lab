from pathlib import Path
from datetime import datetime, timezone
import hashlib
import json
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
N2 = ROOT / "data/processed/aave"

OLD = ROOT / "data/staging/n3_price_timeseries.parquet"
NEW = ROOT / "data/staging/n3_price_timeseries_A_B1_B2.parquet"
QA = ROOT / "outputs/tables/n3_price_timeseries_A_B1_B2_quality.json"

EXPECTED = {
    "A": 26179841,
    "B1": 20458637,
    "B2": 23543615,
}

def require(df, cols, name):
    missing = set(cols) - set(df.columns)
    if missing:
        raise ValueError(f"{name} thieu cot: {sorted(missing)}")

def main():
    if NEW.exists() or QA.exists():
        raise SystemExit(
            "File M3 moi da ton tai. KHONG ghi de. "
            "Hay kiem tra ket qua truoc."
        )

    old_hash = hashlib.sha256(OLD.read_bytes()).hexdigest()
    old = pd.read_parquet(OLD)

    meta = json.loads(
        (N2 / "snapshot_metadata.json").read_text("utf-8")
    )
    oracle = pd.read_parquet(N2 / "oracle_prices.parquet")
    lido = pd.read_parquet(N2 / "lido_ratios.parquet")
    registry = pd.read_csv(N2 / "token_registry.csv")

    require(old, [
        "timestamp", "asset", "token_address",
        "quote_asset", "price_value", "price_type",
        "source", "snapshot_block",
        "reference_timestamp", "staleness_seconds", "note"
    ], "M3 cu")

    require(oracle, [
        "snapshot_block", "token_address", "price_usd"
    ], "N2 Oracle")

    require(lido, [
        "snapshot_block", "steth_per_wsteth"
    ], "N2 Lido")

    require(registry, [
        "token_address", "symbol"
    ], "N2 Registry")

    snaps = meta.get("snapshots", {})

    if set(snaps) != set(EXPECTED):
        raise ValueError(
            f"Metadata dang co {list(snaps)}; "
            "can A, B1, B2"
        )

    times = {}

    for tag, expected_block in EXPECTED.items():
        snap = snaps[tag]
        block = int(snap["snapshot_block"])

        if block != expected_block:
            raise ValueError(
                f"{tag}: block {block} != {expected_block}"
            )

        if snap.get("block_hash_verified_on_chain") is not True:
            raise ValueError(f"{tag}: block chua verified")

        ts = pd.Timestamp(snap["snapshot_timestamp_utc"])

        if ts.tzinfo is None:
            raise ValueError(f"{tag}: timestamp thieu timezone")

        times[block] = ts.tz_convert("UTC")

    # Giu nguyen tat ca market reference / ratio cu.
    market = old.loc[
        ~old["price_type"].isin([
            "aave_oracle", "lido_contract_conversion"
        ])
    ].copy()

    # Chi lay gia Oracle tai 3 block duoc duyet.
    oracle["snapshot_block"] = pd.to_numeric(
        oracle["snapshot_block"], errors="raise"
    ).astype("int64")
    oracle["token_address"] = (
        oracle["token_address"].astype(str).str.lower()
    )
    oracle["price_usd"] = pd.to_numeric(
        oracle["price_usd"], errors="coerce"
    )

    oracle = oracle.loc[
        oracle["snapshot_block"].isin(times)
    ].copy()

    if set(oracle["snapshot_block"]) != set(times):
        raise ValueError("N2 Oracle thieu block A/B1/B2")

    if oracle.duplicated([
        "snapshot_block", "token_address"
    ]).any():
        raise ValueError("N2 Oracle bi trung block/token")

    if (
        oracle["price_usd"].isna().any()
        or (oracle["price_usd"] <= 0).any()
    ):
        raise ValueError("Oracle co gia USD thieu/khong hop le")

    registry["token_address"] = (
        registry["token_address"].astype(str).str.lower()
    )

    if registry.duplicated("token_address").any():
        raise ValueError("N2 Registry trung dia chi token")

    symbols = registry.set_index("token_address")["symbol"]

    missing_tokens = set(
        oracle["token_address"]
    ) - set(symbols.index)

    if missing_tokens:
        raise ValueError(
            "N2 Registry chua co token lich su: "
            + str(sorted(missing_tokens)[:10])
        )

    rows = []

    for r in oracle.itertuples(index=False):
        block = int(r.snapshot_block)
        addr = str(r.token_address).lower()
        ts = times[block]

        rows.append({
            "timestamp": ts,
            "asset": str(symbols[addr]),
            "token_address": addr,
            "quote_asset": "USD",
            "price_value": float(r.price_usd),
            "price_type": "aave_oracle",
            "source": "N2 Aave Oracle same-block",
            "snapshot_block": block,
            "reference_timestamp": ts,
            "staleness_seconds": 0.0,
            "note": "Historical N2 Oracle; not DEX spot"
        })

    # Lay template Lido tu file M3 cu
    old_lido = old.loc[
        old["price_type"].eq("lido_contract_conversion")
    ]

    if old_lido.empty:
        raise ValueError("M3 cu khong co template Lido")

    template = old_lido.iloc[0].to_dict()

    lido["snapshot_block"] = pd.to_numeric(
        lido["snapshot_block"], errors="raise"
    ).astype("int64")

    lido["steth_per_wsteth"] = pd.to_numeric(
        lido["steth_per_wsteth"], errors="coerce"
    )

    lido = lido.loc[
        lido["snapshot_block"].isin(times)
    ].copy()

    if (
        len(lido) != 3
        or set(lido["snapshot_block"]) != set(times)
        or lido["snapshot_block"].duplicated().any()
        or lido["steth_per_wsteth"].isna().any()
        or (lido["steth_per_wsteth"] <= 0).any()
    ):
        raise ValueError("N2 Lido khong du 3 ratio hop le")

    for r in lido.itertuples(index=False):
        block = int(r.snapshot_block)
        ts = times[block]

        row = template.copy()
        row.update({
            "timestamp": ts,
            "snapshot_block": block,
            "price_value": float(r.steth_per_wsteth),
            "source": "N2 Lido stEthPerToken same-block",
            "reference_timestamp": ts,
            "staleness_seconds": 0.0,
            "note": "Historical contract conversion ratio"
        })
        rows.append(row)

    new_snapshot_rows = pd.DataFrame(rows).reindex(
        columns=old.columns
    )

    result = pd.concat([
        market, new_snapshot_rows
    ], ignore_index=True)

    for col in ("timestamp", "reference_timestamp"):
        result[col] = pd.to_datetime(
            result[col], utc=True, errors="coerce"
        )

    result["snapshot_block"] = pd.to_numeric(
        result["snapshot_block"], errors="coerce"
    ).astype("Int64")

    expected_count = len(market) + len(oracle) + len(lido)

    assert len(result) == expected_count
    assert len(result) == len(market) + len(rows)

    # Kiểm tra không để lọt Oracle/Lido block B cũ.
    snapshot_rows = result.loc[
        result["price_type"].isin([
            "aave_oracle", "lido_contract_conversion"
        ])
    ]

    blocks = set(
        snapshot_rows["snapshot_block"].dropna().astype(int)
    )

    assert blocks == set(times), blocks

    qa = {
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "snapshot_blocks": EXPECTED,
        "old_file_sha256": old_hash,
        "old_rows": len(old),
        "new_rows": len(result),
        "market_rows_preserved": len(market),
        "oracle_rows": len(oracle),
        "oracle_by_block": {
            str(k): int(v)
            for k, v in oracle.groupby("snapshot_block").size().items()
        },
        "lido_rows": len(lido),
        "old_snapshot_B_26194188_excluded": True,
        "market_note": (
            "Market rows retained from old M3 only; "
            "these are not automatically historical market "
            "quotes for B1/B2. Use extended 2024-2026 "
            "market datasets for event-period analysis."
        )
    }

    NEW.parent.mkdir(parents=True, exist_ok=True)
    QA.parent.mkdir(parents=True, exist_ok=True)

    result.to_parquet(
        NEW, index=False, compression="snappy"
    )

    QA.write_text(
        json.dumps(qa, indent=2, ensure_ascii=False),
        encoding="utf-8"
    )

    assert hashlib.sha256(
        OLD.read_bytes()
    ).hexdigest() == old_hash

    print("\n===== M3 A/B1/B2 COMPLETED =====")
    print("M3 cu:", len(old), "dong - KHONG DOI")
    print("M3 moi:", len(result), "dong")
    print("Market retained:", len(market))
    print("Oracle:", qa["oracle_by_block"])
    print("Lido:", len(lido))
    print("[SAVED]", NEW)
    print("[SAVED]", QA)

if __name__ == "__main__":
    main()
