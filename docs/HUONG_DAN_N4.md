# 1. Chạy Unit Tests tự động (Yêu cầu: 83 passed)
.\.venv\Scripts\python.exe -m pytest tests/

# 2. Khởi chạy lại Pipeline tạo Data Marts
.\.venv\Scripts\python.exe src/models/health_factor.py
.\.venv\Scripts\python.exe src/models/stress_test.py
.\.venv\Scripts\python.exe src/models/cliff_reverse.py

# 3. Lệnh Self-QA đối soát độc lập 3 Snapshots A/B1/B2 và Zero-shock
.\.venv\Scripts\python.exe -c "
import pandas as pd
risk = pd.read_parquet('data/marts/borrower_risk.parquet')
print('Snapshots trong Marts:', risk['snapshot_block'].unique().tolist())
cliff = pd.read_parquet('data/marts/cliff_curve.parquet')
print('Cột đầu của Cliff:', cliff.columns[0])
"