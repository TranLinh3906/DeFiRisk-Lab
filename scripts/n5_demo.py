"""Small synthetic N5 exercise. No data reads, network or environment changes."""
import argparse
import json
from pathlib import Path

from src.models.cascade import CascadeConfig, POLICIES, synthetic_portfolio
from src.models.n5_experiments import (
    compare_static_cascade, export_experiment, run_sensitivity_analysis, scenario_shocks,
)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True, help="New directory only")
    args = parser.parse_args()
    if args.output_dir.exists():
        parser.error("Output directory exists; choose another directory")
    wallets, assets = synthetic_portfolio()
    symbols = {token: token for token in assets}
    context = {"data_kind": "synthetic"}
    rows = run_sensitivity_analysis(wallets, assets, symbols=symbols, context=context)
    comparisons = []
    for policy in POLICIES:
        result = compare_static_cascade(wallets, assets, CascadeConfig(cf_rule=policy),
                                        shocks=scenario_shocks(symbols, eth_shock=-.2, depeg_shock=-.05),
                                        scenario_id="demo_eth20_depeg5", context=context)
        comparisons.append(dict(result["comparison"], data_kind="synthetic",
                                static_summary=result["static"].summary,
                                cascade_summary=result["cascade"].summary,
                                static_rounds=result["static"].rounds,
                                cascade_rounds=result["cascade"].rounds))
    export_experiment(rows, args.output_dir)
    (args.output_dir / "benchmark_comparison.json").write_text(
        json.dumps(comparisons, indent=2, allow_nan=False), encoding="utf-8")
    print(f"Synthetic only: {len(rows)} sensitivity runs, {len(comparisons)} benchmark comparisons")
    print(args.output_dir)


if __name__ == "__main__":
    main()
