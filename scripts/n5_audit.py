"""Explicit-path, read-only N5 audit. Run from repository root with -m scripts.n5_audit."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import math
from pathlib import Path
from statistics import median

from src.models.cascade import health_factor, debt_value
from src.models.n5_inputs import prepare_portfolio, read_rows, canonical_rows, unique_index
from src.models.n5_validation import check_schema, validate_inputs, assess_historical_readiness


def stable_read(path):
    path = Path(path)
    before = path.stat()
    rows = read_rows(path)
    after = path.stat()
    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
        raise ValueError(f"Input changed during read; retry later: {path}")
    return rows


def finite_json(value):
    if isinstance(value, dict):
        return {k: finite_json(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [finite_json(v) for v in value]
    return None if isinstance(value, float) and not math.isfinite(value) else value


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--positions", required=True, type=Path)
    ap.add_argument("--reserves", required=True, type=Path)
    ap.add_argument("--prices", required=True, type=Path)
    ap.add_argument("--emode", required=True, type=Path)
    ap.add_argument("--references", required=True, type=Path)
    ap.add_argument("--metadata", type=Path)
    ap.add_argument("--liquidations", type=Path, help="Explicit N3 holdout table, read only")
    ap.add_argument("--snapshot-block", required=True, type=int)
    ap.add_argument("--n4-marts", type=Path)
    ap.add_argument("--output-dir", required=True, type=Path, help="Must not exist")
    args = ap.parse_args()
    if args.output_dir.exists():
        ap.error("Output directory already exists; choose a new directory")
    paths = {name: getattr(args, name) for name in ("positions", "reserves", "prices", "emode", "references")}
    report = {"created_utc": datetime.now(timezone.utc).isoformat(),
              "snapshot_block": args.snapshot_block, "status": "pending",
              "inputs": {k: str(v.resolve()) for k, v in paths.items()},
              "scope": "Read-only input audit; no simulation or Compound access", "n4": {}}
    try:
        tables = {name: stable_read(path) for name, path in paths.items()}
        depths = {r["token_address"].lower(): 1_000_000.0 for r in tables["reserves"]}
        portfolio = prepare_portfolio(tables["positions"], tables["reserves"], tables["prices"],
                                      snapshot_block=args.snapshot_block, depth_usd=depths,
                                      emode_membership=tables["emode"])
        report["input_validation"] = validate_inputs(portfolio, tables["references"])
        report["status"] = report["input_validation"]["status"]
        report["audit_only_depth_note"] = "Placeholder USD depth=1m; not used for price impact or simulation in this audit"
        if args.metadata:
            metadata = json.loads(args.metadata.read_text(encoding="utf-8"))
            snapshots = metadata.get("snapshots", {})
            matching = [s for s in snapshots.values() if s.get("snapshot_block") == args.snapshot_block]
            report["snapshot_metadata"] = dict(path=str(args.metadata), matching_snapshots=matching,
                                                declared_status=metadata.get("status"), limitations=metadata.get("limitations"))
            if len(matching) != 1 or metadata.get("protocol_id") != portfolio.protocol_id:
                raise ValueError("Snapshot metadata does not uniquely match block/protocol")
            if args.liquidations:
                snap = matching[0]
                report["historical_readiness"] = assess_historical_readiness(
                    stable_read(args.liquidations), snapshot_block=args.snapshot_block,
                    snapshot_timestamp=snap["snapshot_timestamp_utc"],
                    wallets=[w.wallet for w in portfolio.wallets])
    except (ValueError, OSError, KeyError) as exc:
        report.update(status="failed", input_error=str(exc))
        portfolio = None
    if args.n4_marts:
        import pyarrow.parquet as pq
        for name in ("borrower_risk", "stress_positions", "stress_summary", "stress_heatmap"):
            path = args.n4_marts / f"{name}.parquet"
            try:
                file = pq.ParquetFile(path)
                # Bound resource use while the independent crawler is active.
                batch = next(file.iter_batches(batch_size=2000)).to_pylist()
                errors = check_schema(batch, name)
                report["n4"][name] = dict(path=str(path), total_rows=file.metadata.num_rows,
                                          checked_rows=len(batch), scope="First 2000 rows only",
                                          status="failed" if errors else "sample_schema_passed",
                                          errors=errors[:20], columns=file.schema_arrow.names)
            except (OSError, ValueError, StopIteration) as exc:
                report["n4"][name] = dict(status="failed", reason=str(exc))
        if portfolio is not None:
            try:
                n4 = canonical_rows(stable_read(args.n4_marts / "borrower_risk.parquet"), args.snapshot_block)
                unique_index(n4, ["wallet_address"])
                prices = {t: a.price_usd for t, a in portfolio.assets.items()}
                ours = {w.wallet: health_factor(w, portfolio.assets, prices) for w in portfolio.wallets if any(w.debt.values())}
                debts = {w.wallet: debt_value(w, prices) for w in portfolio.wallets}
                differences = [abs(ours[r["wallet_address"]] - r["health_factor"]) for r in n4 if r["wallet_address"] in ours]
                largest = sorted((dict(wallet=r["wallet_address"], adapter_hf=ours[r["wallet_address"]],
                                       n4_hf=r["health_factor"], absolute_error=abs(ours[r["wallet_address"]] - r["health_factor"]))
                                  for r in n4 if r["wallet_address"] in ours), key=lambda r: r["absolute_error"], reverse=True)[:5]
                flips = [dict(wallet=r["wallet_address"], adapter_hf=ours[r["wallet_address"]],
                              n4_hf=r["health_factor"], adapter_debt_usd=debts[r["wallet_address"]])
                         for r in n4 if r["wallet_address"] in ours and
                         (ours[r["wallet_address"]] < 1) != (r["health_factor"] < 1)]
                report["n4"]["hf_comparison"] = dict(
                    status="failed" if flips else "comparison_completed",
                    compared_borrowers=len(differences), portfolio_borrowers=len(ours),
                    median_absolute_error=median(differences) if differences else None,
                    max_absolute_error=max(differences, default=None),
                    accounts_above_005=sum(d >= .005 for d in differences), largest_discrepancies=largest,
                    eligibility_disagreements=len(flips),
                    disagreement_debt_usd=sum(r["adapter_debt_usd"] for r in flips),
                    largest_eligibility_disagreements=sorted(flips, key=lambda r: r["adapter_debt_usd"], reverse=True)[:5],
                    note="N4 vs independent adapter; does not replace Aave reference check")
            except (ValueError, OSError, KeyError) as exc:
                report["n4"]["hf_comparison"] = dict(status="failed", reason=str(exc))
    if any(item.get("status") == "failed" for item in report["n4"].values()):
        report["status"] = "failed"
    if report.get("historical_readiness", {}).get("status") == "failed":
        report["status"] = "failed"
    report = finite_json(report)
    args.output_dir.mkdir(parents=True, exist_ok=False)
    target = args.output_dir / "audit.json"
    target.write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    print(f"Audit status: {report['status']}; report: {target}")
    if "input_error" in report:
        print(report["input_error"])
    return 1 if report["status"] == "failed" else 0


if __name__ == "__main__":
    raise SystemExit(main())
