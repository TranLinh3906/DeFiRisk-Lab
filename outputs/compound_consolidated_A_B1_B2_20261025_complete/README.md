# Compound A/B1/B2 — đủ phạm vi truy vấn mục tiêu

Đã đọc lại shard 001 tại `shard_001_of_006/shard_001_of_006` có dữ liệu B1/B2, gộp cùng các nguồn còn lại và chạy QA. Đây là bản mới thay thế bộ gộp trước còn thiếu 12.870 truy vấn.

| Snapshot | Block | Có / cần truy vấn | Thiếu | Positions | Accounts | Overlap |
|---|---:|---:|---:|---:|---:|---:|
| A | 26179841 | 56.986 / 56.986 | 0 | 960 | 686 | 631 |
| B1 | 20458637 | 24.094 / 24.094 | 0 | 978 | 571 | 525 |
| B2 | 23543615 | 55.970 / 55.970 | 0 | 1.247 | 826 | 760 |

Tổng 137.050 truy vấn mục tiêu, 3.185 positions, 2.083 market accounts và 1.916 overlap wallet–snapshot rows. `complete=true`, `missing_queries=0`.

Kiểm tra block, market, tập ví, phân công shard, khóa trùng/xung đột, lượng token × giá, tổng positions/account và overlap đều đạt. Chi tiết hash nguồn và cách đọc working tree/Git object nằm trong `merge_report.json`. Không gọi lại RPC để xác minh độc lập số dư.

Phạm vi: các ví vay Aave được chọn, truy vấn trên Compound III Ethereum USDC và WETH tại ba block. Đủ truy vấn trong phạm vi này không có nghĩa bao phủ toàn bộ người dùng Compound hoặc chứng minh lây lan liên giao thức.

Tái lập, luôn chọn thư mục đầu ra mới:

```powershell
.\.venv\Scripts\python.exe -B -X utf8 scripts/consolidate_compound_ab1b2.py --output outputs/compound_AB1B2_NEW
```
