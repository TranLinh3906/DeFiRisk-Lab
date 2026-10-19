import json
p = json.load(open("data/raw/aave/emode_probe.json", encoding="utf-8"))
for snap in ("A", "B"):
    print("==", snap, "ham chay duoc:", p[snap]["working_functions"])
    for cid, c in p[snap]["categories"].items():
        k = c.get("getEModeCategoryCollateralConfig") or c.get("getEModeCategoryData")
        print(cid, k.get("label"), "LT_bps=", k["lt_bps"], "bonus_raw=", k["bonus_raw_bps"])
print("A == B:", p["A"]["categories"] == p["B"]["categories"])

import pandas as pd
d = pd.read_parquet("data/processed/aave/reserve_config.parquet")
cols = ["ltv_bps", "lt_bps", "liq_bonus_raw_bps", "usage_as_collateral", "borrowing_enabled", "is_frozen", "is_paused"]
a = d[d.snapshot_id == "A"].set_index("token_address")[cols]
b = d[d.snapshot_id == "B"].set_index("token_address")[cols]
print("Token doi cau hinh giua A va B:", (a != b).any(axis=1).sum())