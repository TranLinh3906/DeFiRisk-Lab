"""Synthetic cascade prototype following research_design.md sections 4.2–4.3.

Token quantities are normalized (interest already accrued); all prices are USD.
No live Aave contract rules, historical observations, or Compound bridge logic.
The baseline policy implements the research specification, not a protocol emulator.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
from dataclasses import asdict, dataclass, field
import json
import math
from pathlib import Path
from uuid import uuid4

POLICIES = ("baseline_aave", "fixed_50", "fixed_100", "dynamic_linear")
MODEL_VERSION = "synthetic_cascade_v0.1"


@dataclass
class Asset:
    price_usd: float
    liquidation_threshold: float = 0.8
    liquidation_bonus: float = 0.05  # premium: 0.05 means 5%, not 1.05
    dex_depth_usd: float = 1_000_000.0


@dataclass
class Wallet:
    wallet: str
    collateral: dict[str, float]
    debt: dict[str, float]
    collateral_enabled: dict[str, bool] = field(default_factory=dict)
    effective_lt: dict[str, float] = field(default_factory=dict)
    effective_bonus: dict[str, float] = field(default_factory=dict)


@dataclass
class CascadeConfig:
    cf_rule: str = "baseline_aave"
    max_rounds: int = 30
    gas_cost_usd: float = 5.0
    dex_fee_rate: float = 0.0005
    lambda_impact: float = 0.02
    gamma_exponent: float = 1.0
    sell_fraction: float = 1.0
    max_price_impact: float = 0.95
    min_repay_usd: float = 0.01


@dataclass
class CascadeResult:
    rounds: list[dict]
    summary: dict
    wallets: list[Wallet]
    prices: dict[str, float]
    transactions: list[dict]


def close_factor(hf: float, policy: str) -> float:
    """Research policies: Dynamic = clip((1-HF)/0.10, 0.20, 1)."""
    if policy not in POLICIES:
        raise ValueError(f"Unknown Close Factor policy: {policy}")
    if math.isnan(hf) or hf < 0:
        raise ValueError("HF must be nonnegative")
    if hf >= 1:
        return 0.0
    if policy == "baseline_aave":
        return 0.5 if hf >= 0.95 else 1.0
    if policy == "fixed_50":
        return 0.5
    if policy == "fixed_100":
        return 1.0
    return min(1.0, max(0.2, (1.0 - hf) / 0.1))


def _enabled(w: Wallet, token: str) -> bool:
    return w.collateral_enabled.get(token, True)


def debt_value(w: Wallet, prices: dict[str, float]) -> float:
    return sum(q * prices[t] for t, q in w.debt.items())


def collateral_value(w: Wallet, prices: dict[str, float]) -> float:
    return sum(q * prices[t] for t, q in w.collateral.items() if _enabled(w, t))


def health_factor(w: Wallet, assets: dict[str, Asset], prices: dict[str, float]) -> float:
    debt = debt_value(w, prices)
    if debt == 0:
        return math.inf
    return sum(q * prices[t] * w.effective_lt.get(t, assets[t].liquidation_threshold)
               for t, q in w.collateral.items() if _enabled(w, t)) / debt


def price_impact(sold_usd: float, asset: Asset, config: CascadeConfig) -> float:
    """Assumed terminal drop: min(cap, lambda * (sold USD / depth USD)**gamma)."""
    if sold_usd <= 0 or config.lambda_impact == 0:
        return 0.0
    return min(config.max_price_impact,
               config.lambda_impact * (sold_usd / asset.dex_depth_usd) ** config.gamma_exponent)


def _validate(wallets: list[Wallet], assets: dict[str, Asset], c: CascadeConfig,
              shocks: dict[str, float]) -> None:
    def finite(value, name, low=0, high=math.inf, strict=False):
        if not math.isfinite(value) or value < low or value > high or (strict and value == low):
            raise ValueError(f"Invalid {name}: {value}")
    close_factor(1, c.cf_rule)
    if not isinstance(c.max_rounds, int) or isinstance(c.max_rounds, bool) or c.max_rounds < 1:
        raise ValueError("max_rounds must be a positive integer")
    for name in ("gas_cost_usd", "lambda_impact"):
        finite(getattr(c, name), name)
    finite(c.gamma_exponent, "gamma_exponent", strict=True)
    finite(c.min_repay_usd, "min_repay_usd", strict=True)
    finite(c.dex_fee_rate, "dex_fee_rate", high=1)
    finite(c.sell_fraction, "sell_fraction", high=1)
    finite(c.max_price_impact, "max_price_impact", high=0.99)
    for t, a in assets.items():
        finite(a.price_usd, f"{t}.price", strict=True)
        finite(a.dex_depth_usd, f"{t}.depth", strict=True)
        finite(a.liquidation_threshold, f"{t}.LT", high=1)
        finite(a.liquidation_bonus, f"{t}.bonus", high=1)
    if len({w.wallet for w in wallets}) != len(wallets):
        raise ValueError("Duplicate wallet IDs")
    for w in wallets:
        if not w.wallet:
            raise ValueError("Wallet ID cannot be empty")
        for values in (w.collateral, w.debt):
            for t, q in values.items():
                if t not in assets:
                    raise ValueError(f"Unknown asset: {t}")
                finite(q, f"{w.wallet}.{t}.quantity")
        for overrides in (w.effective_lt, w.effective_bonus):
            for t, v in overrides.items():
                if t not in w.collateral:
                    raise ValueError(f"Override without collateral: {t}")
                finite(v, f"{w.wallet}.{t}.override", high=1)
        if any(t not in w.collateral or not isinstance(v, bool)
               for t, v in w.collateral_enabled.items()):
            raise ValueError("collateral_enabled requires collateral tokens and bool values")
    for t, shock in shocks.items():
        if t not in assets or not math.isfinite(shock) or shock <= -1:
            raise ValueError(f"Invalid shock: {t}={shock}")


def _quote(w: Wallet, assets: dict[str, Asset], prices: dict[str, float],
           sold: dict[str, float], hf: float, c: CascadeConfig) -> dict | None:
    """One transaction per wallet per round; choose pair with highest net profit.

    Repayment is capped by CURRENT wallet debt, selected debt-token balance and
    available collateral including bonus. Other pairs may execute next round.
    Execution slippage is the trapezoid average of impact before/after this sale.
    """
    best = None
    cap = close_factor(hf, c.cf_rule) * debt_value(w, prices)
    for ct in sorted(w.collateral):
        if not _enabled(w, ct) or w.collateral[ct] <= 0:
            continue
        bonus = w.effective_bonus.get(ct, assets[ct].liquidation_bonus)
        for dt in sorted(w.debt):
            repay = min(cap, w.debt[dt] * prices[dt],
                        w.collateral[ct] * prices[ct] / (1 + bonus))
            if repay < c.min_repay_usd:
                continue
            seized = repay * (1 + bonus)
            sale = seized * c.sell_fraction
            before = price_impact(sold[ct], assets[ct], c)
            after = price_impact(sold[ct] + sale, assets[ct], c)
            slippage = (before + after) / 2
            # Unsold collateral is marked at the same modeled execution quote.
            proceeds = seized * (1 - slippage) * (1 - c.dex_fee_rate)
            profit = proceeds - repay - c.gas_cost_usd
            quote = dict(collateral_token=ct, debt_token=dt, repaid_debt_usd=repay,
                         collateral_seized_usd=seized, sold_usd=sale,
                         debt_tokens_repaid=repay / prices[dt],
                         collateral_tokens_seized=seized / prices[ct],
                         execution_slippage=slippage, liquidator_profit_usd=profit,
                         hf_before=hf, close_factor=close_factor(hf, c.cf_rule))
            if best is None or profit > best["liquidator_profit_usd"]:
                best = quote
    return best


def simulate_cascade(wallets: list[Wallet], assets: dict[str, Asset],
                     config: CascadeConfig | None = None, *,
                     shocks: dict[str, float] | None = None,
                     run_id: str | None = None, scenario_id: str = "synthetic_custom") -> CascadeResult:
    """Return independent final balances, transaction audit, round and summary rows.

    Shock values are returns (-0.20 = -20%) applied once before round 1.
    HF/oracle prices stay fixed inside a round; DEX flow is sequential in sorted
    wallet order. Accepted sales update all token prices at the end of the round.
    """
    c = deepcopy(config) if config is not None else CascadeConfig()
    shocks = dict(shocks or {})
    _validate(wallets, assets, c, shocks)
    state = deepcopy(wallets)
    prices = {t: a.price_usd * (1 + shocks.get(t, 0)) for t, a in assets.items()}
    if any(not math.isfinite(p) or p <= 0 for p in prices.values()):
        raise ValueError("Shocked prices must be finite and positive")
    initial_debt = sum(debt_value(w, prices) for w in state)
    context = dict(run_id=run_id or str(uuid4()), scenario_id=scenario_id,
                   cf_rule=c.cf_rule, model_version=MODEL_VERSION, data_kind="synthetic")
    rounds, transactions, seen = [], [], set()
    cumulative = 0.0
    stop_reason = "max_rounds"
    for number in range(1, c.max_rounds + 1):
        start_prices = prices.copy()
        eligible = {w.wallet for w in state if debt_value(w, prices) > 0
                    and health_factor(w, assets, prices) < 1}
        newly = eligible - seen
        seen.update(eligible)
        sold = {t: 0.0 for t in assets}
        repaid = seized = profit = frozen = 0.0
        executed = 0
        for w in sorted(state, key=lambda w: w.wallet):
            if w.wallet not in eligible:
                continue
            hf = health_factor(w, assets, prices)
            quote = _quote(w, assets, prices, sold, hf, c)
            if quote is None or quote["liquidator_profit_usd"] <= 0:
                frozen += debt_value(w, prices)
                continue
            ct, dt = quote["collateral_token"], quote["debt_token"]
            w.debt[dt] = max(0.0, w.debt[dt] - quote["debt_tokens_repaid"])
            w.collateral[ct] = max(0.0, w.collateral[ct] - quote["collateral_tokens_seized"])
            sold[ct] += quote["sold_usd"]
            repaid += quote["repaid_debt_usd"]
            seized += quote["collateral_seized_usd"]
            profit += quote["liquidator_profit_usd"]
            executed += 1
            transactions.append(dict(round=number, wallet=w.wallet, **quote))
        impacts = {t: price_impact(sold[t], assets[t], c) for t in assets}
        prices = {t: prices[t] * (1 - impacts[t]) for t in assets}
        cumulative += repaid
        bad_debt = sum(max(0.0, debt_value(w, prices) - collateral_value(w, prices)) for w in state)
        remaining = sum(debt_value(w, prices) for w in state)
        rounds.append(dict(**context, round=number,
                           newly_liquidatable_wallets=len(newly),
                           eligible_wallets=len(eligible), executed_liquidations=executed,
                           simulated_repaid_debt_usd=repaid, cumulative_repaid_usd=cumulative,
                           collateral_seized_usd=seized, liquidator_profit_usd=profit,
                           liquidation_freeze_debt_usd=frozen, bad_debt_usd=bad_debt,
                           remaining_debt_usd=remaining,
                           prices_before_json=json.dumps(start_prices, sort_keys=True),
                           prices_after_json=json.dumps(prices, sort_keys=True),
                           sold_usd_json=json.dumps(sold, sort_keys=True),
                           max_price_impact_pct=100 * max(impacts.values(), default=0.0)))
        if not eligible:
            stop_reason = "no_liquidatable_wallets"
            break
        if not executed:
            stop_reason = "no_feasible_liquidations"
            break
        if not any(debt_value(w, prices) > 0 and health_factor(w, assets, prices) < 1 for w in state):
            stop_reason = "no_liquidatable_wallets"
            break
    first = rounds[0]["simulated_repaid_debt_usd"]
    summary = dict(**context, total_simulated_repaid_usd=cumulative,
                   cascade_amplification=cumulative / first if first else None,
                   total_rounds=len(rounds), initial_debt_usd=initial_debt,
                   remaining_debt_usd=rounds[-1]["remaining_debt_usd"],
                   bad_debt_usd=rounds[-1]["bad_debt_usd"],
                   bad_debt_ratio_pct=100 * bad_debt / initial_debt if initial_debt else 0.0,
                   unique_liquidatable_wallets=len(seen),
                   sequential_liquidatable_wallets=sum(r["newly_liquidatable_wallets"] for r in rounds[1:]),
                   total_liquidator_profit_usd=sum(r["liquidator_profit_usd"] for r in rounds),
                   stop_reason=stop_reason, converged=stop_reason != "max_rounds",
                   parameters_json=json.dumps(asdict(c), sort_keys=True),
                   shocks_json=json.dumps(shocks, sort_keys=True))
    return CascadeResult(rounds, summary, state, prices, transactions)


def export_results(results: CascadeResult | list[CascadeResult], output_dir: str | Path,
                   *, overwrite: bool = False) -> tuple[Path, Path]:
    """Write prototype marts with explicit Arrow types, preserving null amplification.

    These marts are separate from the six canonical Data Contract tables.
    """
    import pyarrow as pa
    import pyarrow.parquet as pq

    results = [results] if isinstance(results, CascadeResult) else results
    if not results:
        raise ValueError("At least one simulation result is required")
    ids = [r.summary["run_id"] for r in results]
    if len(set(ids)) != len(ids):
        raise ValueError("run_id must be unique per simulation")
    output_dir = Path(output_dir)
    paths = (output_dir / "cascade_rounds.parquet", output_dir / "cascade_summary.parquet")
    if not overwrite and any(p.exists() for p in paths):
        raise FileExistsError("Cascade outputs exist; choose another directory or pass overwrite=True")
    text_fields = {"run_id", "scenario_id", "cf_rule", "model_version", "data_kind", "stop_reason"}
    int_fields = {"round", "newly_liquidatable_wallets", "eligible_wallets", "executed_liquidations",
                  "total_rounds", "unique_liquidatable_wallets", "sequential_liquidatable_wallets"}
    tables = []
    for rows in ([row for r in results for row in r.rounds], [r.summary for r in results]):
        fields = []
        for key in rows[0]:
            dtype = (pa.string() if key in text_fields or key.endswith("_json") else
                     pa.int64() if key in int_fields else
                     pa.bool_() if key == "converged" else pa.float64())
            fields.append(pa.field(key, dtype, nullable=key == "cascade_amplification"))
        schema = pa.schema(fields, metadata={b"data_kind": b"synthetic", b"model_version": MODEL_VERSION.encode()})
        tables.append(pa.Table.from_pylist(rows, schema=schema))
    output_dir.mkdir(parents=True, exist_ok=True)
    for table, path in zip(tables, paths):
        pq.write_table(table, path, compression="snappy")
    return paths


def synthetic_portfolio() -> tuple[list[Wallet], dict[str, Asset]]:
    """Eight fictitious wallets with shared collateral and token-denominated debts."""
    assets = {"WETH": Asset(2500, .8, .05, 500_000),
              "wstETH": Asset(3000, .8, .06, 300_000),
              "WBTC": Asset(80_000, .75, .07, 700_000),
              "USDC": Asset(1, .8, .04, 2_000_000)}
    wallets = [
        Wallet("demo_01", {"WETH": 10}, {"USDC": 18_000}),
        Wallet("demo_02", {"WETH": 20}, {"USDC": 33_000}),
        Wallet("demo_03", {"wstETH": 12}, {"WETH": 12},
               effective_lt={"wstETH": .93}, effective_bonus={"wstETH": .02}),
        Wallet("demo_04", {"wstETH": 5, "WBTC": .2}, {"USDC": 21_000}),
        Wallet("demo_05", {"WBTC": .5}, {"USDC": 24_000}),
        Wallet("demo_06", {"WETH": 8}, {}),
        Wallet("demo_07", {"WETH": 4, "USDC": 50_000}, {"USDC": 7000},
               collateral_enabled={"USDC": False}),
        Wallet("demo_08", {"WETH": 5, "WBTC": .1}, {"USDC": 11_000}),
    ]
    return wallets, assets


def main() -> None:
    parser = argparse.ArgumentParser(description="Synthetic cascade demo (8 wallets, 4 CF rules)")
    parser.add_argument("--output-dir", type=Path, default=Path("outputs/demo/cascade"))
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args()
    wallets, assets = synthetic_portfolio()
    results = [simulate_cascade(wallets, assets, CascadeConfig(cf_rule=rule),
                shocks={"WETH": -.2, "wstETH": -.24, "WBTC": -.1},
                scenario_id="synthetic_combined_shock") for rule in POLICIES]
    for r in results:
        s = r.summary
        print(f"{s['cf_rule']}: rounds={s['total_rounds']}, repaid=${s['total_simulated_repaid_usd']:,.2f}, "
              f"bad_debt=${s['bad_debt_usd']:,.2f}, stop={s['stop_reason']}")
    for path in export_results(results, args.output_dir, overwrite=args.overwrite):
        print(path)


if __name__ == "__main__":
    main()
