# Dữ liệu Compound đã gộp — A, B1, B2

Ngày kiểm tra: 25/10/2026. **A đủ phạm vi truy vấn đã chọn; B1/B2 chưa đủ vì thiếu kết quả shard 001.**

| Snapshot | Block | Truy vấn đã có / cần có | Độ phủ | Dòng positions | Dòng accounts | Dòng overlap |
|---|---:|---:|---:|---:|---:|---:|
| A | 26179841 | 56.986 / 56.986 | 100% | 960 | 686 | 631 |
| B1 | 20458637 | 20.108 / 24.094 | 83,46% | 839 | 483 | 444 |
| B2 | 23543615 | 47.086 / 55.970 | 84,13% | 1.056 | 693 | 636 |

Một truy vấn là một tổ hợp snapshot–market–ví, gồm cả kết quả số dư bằng 0. Phạm vi là các ví đang vay Aave được kiểm tra trên hai market Compound III Ethereum USDC/WETH, không phải toàn bộ người dùng Compound.

## Các file dùng được

- `n3_compound_positions_long.parquet`: 2.855 dòng vị thế tài sản.
- `n3_compound_account_reference.parquet`: 1.862 dòng tài khoản theo market.
- `n3_compound_overlap_wallets.parquet`: 1.711 dòng ví theo snapshot.
- `n3_compound_reserve_config.parquet`: 77 dòng cấu hình tài sản.
- `snapshot_summary.csv`: thống kê riêng từng snapshot.
- `query_coverage.parquet`: toàn bộ 137.050 truy vấn mục tiêu, trạng thái đã có/chưa có và nguồn.
- `missing_queries.csv`: 12.870 truy vấn còn thiếu, toàn bộ thuộc shard 001 (B1: 3.986; B2: 8.884), liên quan 5.408 ví duy nhất.
- `merge_report.json` và `n3_compound_quality.json`: kết quả kiểm tra, SHA-256 từng nguồn, cách đọc file và phạm vi thiếu. `complete=false` cho bộ gộp.

## Kiểm tra đã thực hiện

Đối chiếu block, market, địa chỉ Comet, hash tập ví, hash phân công và số truy vấn từng nguồn. Kiểm tra khóa chính, phép đổi lượng tài sản sang USD, tổng số dư theo tài khoản và tổng hợp ví overlap. Đọc lại các Parquet đầu ra và đối chiếu với bảng trước khi lưu.

Gộp 10 bộ nguồn, gồm bản A-only của shard 004 để bổ sung đúng phân công. Loại các dòng trùng hoàn toàn: positions 160, accounts 110, config 372, overlap 100. Không phát hiện khóa trùng có giá trị xung đột trong dữ liệu đã gộp.

Hai thư mục B1/B2 shard 000 và 004 không đọc được từ working tree do quyền truy cập; dữ liệu được đọc trực tiếp từ Git object tại commit `ce8b0e5`, không sửa file gốc. Các snapshot B cũ không được đưa vào kết quả A/B1/B2.

Nhánh `origin/data/compound-b1b2-shard-001` trong Git cục bộ tại `ba2d654` chỉ thêm script chạy; bộ Parquet shard 001 hiện vẫn chỉ có A/B. Cần bổ sung bộ kết quả B1/B2 shard 001 để hoàn tất độ phủ. Không coi truy vấn chưa có là số dư bằng 0.

Kiểm tra này dùng dữ liệu và bằng chứng QA đã lưu, không gọi lại RPC để xác minh số dư on-chain. Độ phủ truy vấn không đồng nghĩa với độ phủ toàn bộ người dùng Compound.

## Chạy lại

Từ thư mục dự án, dùng một thư mục đầu ra mới:

```powershell
python scripts/consolidate_compound_ab1b2.py --output outputs/compound_consolidated_A_B1_B2_new
```

Script không ghi đè dữ liệu nguồn và từ chối ghi đè thư mục kết quả đã tồn tại. Khi có thêm shard mới, bổ sung đường dẫn vào `SOURCES` trong script trước khi chạy lại.
