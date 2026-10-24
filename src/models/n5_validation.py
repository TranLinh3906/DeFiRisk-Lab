"""N5 validation primitives. A passing schema is not research acceptance."""
from __future__ import annotations

import json
import math
from datetime import datetime, timedelta
from statistics import median

from src.models.cascade import collateral_value, debt_value, health_factor
from src.models.n5_inputs import canonical_rows, number, pick, unique_index, flag


# Minimum interchange schemas; extra columns are retained by callers.
SCHEMAS = {
    "borrower_risk": {"snapshot_block": int, "protocol_id": str, "wallet_address": str,
                      "health_factor": float, "total_collateral_usd": float, "total_debt_usd": float,
                      "risk_category": str},
    "stress_positions": {"scenario_id": str, "wallet": str, "hf_before": float,
                         "hf_after": float, "debt_usd": float, "debt_at_risk_usd": float,
                         "snapshot_block": int, "protocol_id": str},
    "stress_summary": {"scenario_id": str, "eth_shock": float, "btc_shock": float,
                       "positions_at_risk": int, "debt_at_risk_usd": float, "debt_at_risk_pct": float},
    "stress_heatmap": {"eth_shock": float, "debt_at_risk_usd": float},
    "cascade_rounds": {"run_id": str, "scenario_id": str, "round": int,
                       "newly_liquidatable_wallets": int, "simulated_repaid_debt_usd": float,
                       "cumulative_repaid_usd": float, "prices_before_json": str, "prices_after_json": str},
    "cascade_summary": {"run_id": str, "scenario_id": str, "total_simulated_repaid_usd": float,
                        "cascade_amplification": float, "total_rounds": int},
    "sensitivity": {"run_id": str, "scenario_id": str, "cf_rule": str,
                    "price_impact_parameter": float, "sell_fraction": float,
                    "eth_shock": float, "depeg_shock": float, "total_simulated_repaid_usd": float},
}
KEYS = {"borrower_risk": ("snapshot_block", "protocol_id", "wallet_address"),
        "stress_positions": ("snapshot_block", "protocol_id", "scenario_id", "wallet"), "stress_summary": ("scenario_id",),
        "cascade_rounds": ("run_id", "round"), "cascade_summary": ("run_id",),
        "sensitivity": ("run_id",)}


def assess_historical_readiness(events, *, snapshot_block, snapshot_timestamp, wallets,
                                protocol_id="aave_v3_ethereum"):
    """Check available events/window, not predictive accuracy or source completeness."""
    start = datetime.fromisoformat(snapshot_timestamp.replace("Z", "+00:00"))
    if start.tzinfo is None:
        raise ValueError("Snapshot timestamp must have timezone")
    keys, errors, observed, first_hour = set(), [], set(), set()
    in_window = 0
    for row in events:
        key = (pick(row, "tx_hash"), pick(row, "log_index"))
        if key in keys:
            errors.append("Duplicate historical event")
        keys.add(key)
        timestamp = pick(row, "event_timestamp")
        if isinstance(timestamp, str):
            timestamp = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
        if timestamp.tzinfo is None:
            raise ValueError("Event timestamp must have timezone")
        if pick(row, "protocol_id") != protocol_id:
            errors.append("Unexpected historical protocol")
            continue
        if number(pick(row, "event_block"), "event_block", 1) <= snapshot_block or not start < timestamp < start + timedelta(hours=24):
            errors.append("Event outside post-snapshot 24h window")
            continue
        number(pick(row, "debt_repaid_usd"), "historical repaid USD")
        number(pick(row, "collateral_seized_usd"), "historical seized USD")
        wallet = str(pick(row, "wallet_address")).lower()
        observed.add(wallet)
        in_window += 1
        if timestamp < start + timedelta(hours=1):
            first_hour.add(wallet)
    return dict(status="failed" if errors else "pending", errors=errors,
                available_events=len(events), in_24h_events=in_window,
                unique_wallets_24h=len(observed), unique_wallets_first_hour=len(first_hour),
                observed_wallets_in_portfolio=len(observed & set(wallets)),
                observed_wallets_outside_portfolio=len(observed - set(wallets)),
                note="Hit-rate not computed; need verified source completeness, event-time prices and holdout predictions")


def check_schema(rows, table: str) -> list[str]:
    """Check mandatory columns, scalar types, nulls, finiteness and primary keys."""
    spec = dict(SCHEMAS[table])
    errors, seen = [], set()
    if not rows:
        return [f"{table}: empty table (not accepted)"]
    # New N4 marts carry snapshot identity; legacy single-snapshot fixtures do not.
    # If supplied anywhere, identity columns are required and typed on EVERY row.
    identity = ()
    if table in ("stress_summary", "stress_heatmap"):
        identity = tuple(k for k in ("snapshot_block", "protocol_id") if any(k in r for r in rows))
        spec.update({k: int if k == "snapshot_block" else str for k in identity})
    heat_axes = tuple(k for k in ("eth_shock", "btc_shock", "depeg_shock")
                      if any(k in r for r in rows)) if table == "stress_heatmap" else ()
    spec.update({k: float for k in heat_axes})
    for i, row in enumerate(rows):
        for name, dtype in spec.items():
            if name not in row:
                errors.append(f"row {i}: missing {name}")
                continue
            value = row[name]
            if value is None and name == "cascade_amplification":
                continue
            valid = (isinstance(value, str) and bool(value.strip()) if dtype is str else
                     isinstance(value, int) and not isinstance(value, bool) if dtype is int else
                     isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value))
            if not valid:
                errors.append(f"row {i}: invalid {name} ({dtype.__name__})")
            elif dtype in (int, float) and value is not None:
                if not name.endswith("_shock") and value < 0:
                    errors.append(f"row {i}: negative {name}")
                if name.endswith("_pct") and not 0 <= value <= 100:
                    errors.append(f"row {i}: percent outside 0..100: {name}")
                if name == "snapshot_block" and value <= 0:
                    errors.append(f"row {i}: snapshot_block must be positive")
        if table == "stress_heatmap":
            if not any(k in row for k in ("btc_shock", "depeg_shock")):
                errors.append(f"row {i}: heatmap requires btc_shock or depeg_shock")
            for dimension in ("btc_shock", "depeg_shock"):
                if dimension in row:
                    try:
                        number(row[dimension], dimension, -1, math.inf)
                    except ValueError:
                        errors.append(f"row {i}: invalid {dimension}")
        keys = KEYS.get(table)
        if table == "stress_summary":
            keys = identity + keys
        if table == "stress_heatmap":
            keys = identity + heat_axes
        if keys and all(k in row for k in keys):
            key = tuple(row[k] for k in keys)
            try:
                if key in seen:
                    errors.append(f"row {i}: duplicate key {key}")
                seen.add(key)
            except TypeError:
                errors.append(f"row {i}: non-scalar key")
    return errors


def validate_inputs(portfolio, references=None, *, reserve_totals=None,
                    full_population=False, historical_evidence=None) -> dict:
    """Check same-block HF and balances; missing independent evidence stays pending.

    reserve_totals must describe the identical full population and block, not
    totals calculated from this sample. Historical evidence is precomputed on
    an untouched holdout: matched_wallets / observed_wallets (unique wallets).
    """
    prices = {t: a.price_usd for t, a in portfolio.assets.items()}
    metrics = {w.wallet: dict(hf=health_factor(w, portfolio.assets, prices),
                             debt=debt_value(w, prices), collateral=collateral_value(w, prices))
               for w in portfolio.wallets}
    report = {"snapshot_block": portfolio.snapshot_block, "protocol_id": portfolio.protocol_id,
              "wallet_count": len(metrics), "token_count": len(prices), "gates": {},
              "assumptions": portfolio.assumptions}
    gates = report["gates"]
    gates["schema_and_adapter"] = {"status": "passed"}
    gates["hf_reference"] = {"status": "pending", "reason": "No reference accounts supplied"}
    if references is not None:
        refs = unique_index(canonical_rows(references, portfolio.snapshot_block, portfolio.protocol_id), ["wallet_address"])
        errors, debt_errors, collateral_errors, missing, balance_details = [], [], [], [], []
        for wallet, values in metrics.items():
            ref = refs.get((wallet,))
            if ref is None or ("call_ok" in ref and not flag(ref["call_ok"], "call_ok")):
                missing.append(wallet)
                continue
            if values["debt"] > 0:
                errors.append(abs(values["hf"] - number(pick(ref, "health_factor_reported"), "reported HF")))
            for key, column, dest in (("debt", "total_debt_base_usd", debt_errors),
                                      ("collateral", "total_collateral_base_usd", collateral_errors)):
                expected = number(pick(ref, column), column)
                dest.append(abs(values[key] - expected) / expected if expected else
                            (0.0 if values[key] == 0 else math.inf))
                if dest[-1] > .01:
                    balance_details.append(dict(wallet=wallet, metric=key, calculated_usd=values[key],
                                                reference_usd=expected, absolute_error_usd=abs(values[key] - expected),
                                                relative_error=dest[-1] if math.isfinite(dest[-1]) else None,
                                                zero_reference=expected == 0))
        unmatched = set(refs) - {(w,) for w in metrics}
        gate = dict(status="passed" if errors and not missing and median(errors) < .005 else "failed",
                    median_absolute_error=median(errors) if errors else None,
                    threshold=.005, compared_borrowers=len(errors), missing_accounts=len(missing),
                    reference_accounts_outside_portfolio=len(unmatched))
        gate["reference_borrowers_outside_portfolio"] = sum(
            number(pick(refs[k], "total_debt_base_usd"), "reference debt") > 0 for k in unmatched
            if "call_ok" not in refs[k] or flag(refs[k]["call_ok"], "call_ok"))
        if not errors and not missing:
            gate.update(status="pending", reason="No indebted accounts to compare")
        gates["hf_reference"] = gate
        gates["sample_account_balances"] = dict(
            status="passed" if not missing and all(x <= .01 for x in debt_errors + collateral_errors) else "failed",
            max_debt_relative_error=max(debt_errors, default=None),
            max_collateral_relative_error=max(collateral_errors, default=None),
            failed_debt_accounts=sum(x > .01 for x in debt_errors),
            failed_collateral_accounts=sum(x > .01 for x in collateral_errors),
            largest_absolute_discrepancies=sorted(balance_details, key=lambda r: r["absolute_error_usd"], reverse=True)[:10],
            note="Per-account check within selected sample; not full-reserve reconciliation")
    gates["reserve_reconciliation"] = {"status": "pending", "reason": "Full-population reserve evidence not supplied"}
    if reserve_totals is not None and full_population:
        if reserve_totals.get("snapshot_block") != portfolio.snapshot_block:
            raise ValueError("Reserve totals use a different snapshot")
        deviations = {}
        for key in ("debt", "collateral"):
            expected = number(pick(reserve_totals, f"{key}_usd"), f"reserve {key}")
            actual = sum(v[key] for v in metrics.values())
            deviations[key] = abs(actual - expected) / expected if expected else (0 if actual == 0 else math.inf)
        gates["reserve_reconciliation"] = dict(status="passed" if max(deviations.values()) <= .01 else "failed",
                                                relative_errors=deviations)
    gates["historical_holdout"] = {"status": "pending", "reason": "Snapshot B/window/coverage and untouched holdout need verification"}
    if historical_evidence is not None:
        e = historical_evidence
        if not e.get("untouched_holdout") or not e.get("window_and_coverage_verified"):
            raise ValueError("Historical evidence must verify holdout, window and coverage")
        observed = number(pick(e, "observed_wallets"), "observed_wallets", 1)
        matched = number(pick(e, "matched_wallets"), "matched_wallets", 0, observed)
        gates["historical_holdout"] = dict(status="passed" if matched / observed >= .85 else "failed",
                                           hit_rate=matched / observed, threshold=.85,
                                           note="Caller-supplied evidence; HF<1.02, unique wallets, first hour")
    statuses = [g["status"] for g in gates.values()]
    report["status"] = "failed" if "failed" in statuses else "pending" if "pending" in statuses else "passed"
    return report


def validate_model_outputs(result, initial_wallets=None) -> dict:
    """Validate schemas, round totals, price continuity and token conservation."""
    errors = check_schema(result.rounds, "cascade_rounds") + check_schema([result.summary], "cascade_summary")
    if errors:
        return {"status": "failed", "errors": errors}
    s, cumulative = result.summary, 0.0
    def equal(a, b, label):
        if not math.isclose(a, b, rel_tol=1e-9, abs_tol=1e-7):
            errors.append(label)
    previous_prices = None
    for i, row in enumerate(result.rounds, 1):
        if row["round"] != i or row["run_id"] != s["run_id"] or row["scenario_id"] != s["scenario_id"]:
            errors.append("Round identity/order mismatch")
        if row["simulated_repaid_debt_usd"] < 0:
            errors.append("Negative repayment")
        cumulative += row["simulated_repaid_debt_usd"]
        equal(cumulative, row["cumulative_repaid_usd"], "Cumulative repayment mismatch")
        equal(sum(t["repaid_debt_usd"] for t in result.transactions if t["round"] == i),
              row["simulated_repaid_debt_usd"], "Transaction/round repayment mismatch")
        try:
            before, after = json.loads(row["prices_before_json"]), json.loads(row["prices_after_json"])
            if not before or before.keys() != after.keys() or any(not math.isfinite(p) or p <= 0 for p in [*before.values(), *after.values()]):
                errors.append("Invalid price path")
            if previous_prices is not None and previous_prices != before:
                errors.append("Price path discontinuity")
            previous_prices = after
        except (ValueError, TypeError, AttributeError):
            errors.append("Malformed price JSON")
    equal(cumulative, s["total_simulated_repaid_usd"], "Summary repayment mismatch")
    if s["total_rounds"] != len(result.rounds):
        errors.append("Summary round count mismatch")
    first = result.rounds[0]["simulated_repaid_debt_usd"]
    if first:
        if s["cascade_amplification"] is None:
            errors.append("Missing amplification")
        else:
            equal(cumulative / first, s["cascade_amplification"], "Amplification mismatch")
    elif s["cascade_amplification"] is not None:
        errors.append("Zero first-round repayment requires null amplification")
    if any(t["round"] not in range(1, len(result.rounds) + 1) for t in result.transactions):
        errors.append("Transaction outside recorded rounds")
    if initial_wallets is not None:
        before = {w.wallet: w for w in initial_wallets}
        after = {w.wallet: w for w in result.wallets}
        if before.keys() != after.keys():
            errors.append("Final wallet population changed")
        for transaction in result.transactions:
            w = before.get(transaction["wallet"])
            if w is None or transaction["debt_token"] not in w.debt or transaction["collateral_token"] not in w.collateral:
                errors.append("Transaction contains unknown wallet/token")
            for name in ("debt_tokens_repaid", "collateral_tokens_seized", "repaid_debt_usd"):
                value = transaction[name]
                if not math.isfinite(value) or value < 0:
                    errors.append(f"Invalid transaction {name}")
        for wallet in before.keys() & after.keys():
            for attr, token_col, quantity_col in (("debt", "debt_token", "debt_tokens_repaid"),
                                                 ("collateral", "collateral_token", "collateral_tokens_seized")):
                start, final = getattr(before[wallet], attr), getattr(after[wallet], attr)
                for token in start.keys() | final.keys():
                    used = sum(t[quantity_col] for t in result.transactions if t["wallet"] == wallet and t[token_col] == token)
                    if final.get(token, 0) < 0 or used < 0:
                        errors.append("Negative token balance/flow")
                    equal(start.get(token, 0) - used, final.get(token, 0), "Token conservation mismatch")
    return {"status": "failed" if errors else "passed", "errors": errors,
            "scope": "Numerical consistency only; not historical or protocol validation"}
