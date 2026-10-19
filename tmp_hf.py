import pandas as pd
d = pd.read_parquet("data/processed/aave/account_reference.parquet")
d = d[d.has_debt]
for s, g in d.groupby("snapshot_id"):
    low = g[g.health_factor_reported < 1]
    print(s, "HF<1:", len(low), "| tong no USD:", round(low.total_debt_base_usd.sum()),
          "| no trung vi USD:", round(low.total_debt_base_usd.median()),
          "| ti le tren tong no:", round(low.total_debt_base_usd.sum() / g.total_debt_base_usd.sum(), 4))