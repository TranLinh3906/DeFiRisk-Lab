import pandas as pd
r = pd.read_parquet("data/processed/aave/hf_reconciliation.parquet")
for s, g in r.groupby("snapshot_id"):
    bad = g[g.debt_rel_err >= 0.01]
    print(s, len(bad), "vi no lech >=1% | no Aave trung vi USD:", round(bad.total_debt_base_usd.median(), 4),
          "| ti le tren tong no:", round(bad.total_debt_base_usd.sum() / g.total_debt_base_usd.sum(), 8))
    flip = g[(g.health_factor_reported < 1) != (g.hf_calc < 1)]
    print("  vi lech nguong HF<1:", len(flip))
    print(flip[["wallet", "health_factor_reported", "hf_calc", "total_debt_base_usd"]].to_string(index=False))