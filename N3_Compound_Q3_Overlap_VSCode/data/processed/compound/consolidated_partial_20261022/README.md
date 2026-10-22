# Tổng hợp Compound ngày 22/10/2026 — CHƯA ĐỦ COVERAGE

File duy nhất chứa toàn bộ các bảng đã tổng hợp: `compound_consolidated_partial.sqlite`.
Mở bằng công cụ đọc SQLite (ví dụ DB Browser for SQLite) để xem các bảng:

- `positions`: 1.694 dòng vị thế có số dư dương, sau khi loại trùng.
- `accounts`: 1.218 dòng tài khoản theo market và snapshot.
- `config`: 64 dòng cấu hình tài sản theo market và snapshot.
- `overlap`: 1.121 cặp ví–snapshot có vị thế Compound.
- `query_coverage`: 114.076 tổ hợp dự kiến; cột `covered` phân biệt đã kiểm tra và còn thiếu.
- `merge_report`: báo cáo phạm vi, nguồn và trạng thái chưa hoàn thành.

Các bảng còn có bản Parquet riêng mang hậu tố `_partial` để đọc bằng Python.
`missing_queries.csv` liệt kê chính xác 14.294 tổ hợp còn thiếu (3.594 ví khác nhau).
Ví đã được kiểm tra nhưng không có vị thế không xuất hiện trong `positions`;
không nhầm chúng với các ví chưa được kiểm tra trong bảng coverage.

## Kết quả kiểm tra

Từng phần đều qua QA riêng, cùng metadata market, snapshot và tập ví toàn cục.
Nhưng phần 004 khai báo `sorted_wallet_round_robin_v1`, trong khi năm phần khác khai báo
`checkpoint_assignment_v1:6105d9df30ba...`. Vì vậy chúng không phải cùng bộ phân công.
Kết quả hợp của phạm vi thực tế là 99.782/114.076 tổ hợp, chưa phải bộ dữ liệu đầy đủ.

Đã loại 256 dòng vị thế trùng hoàn toàn, 174 dòng tài khoản trùng, 156 dòng overlap trùng
và 320 dòng cấu hình lặp giữa các phần. Không phát hiện hai dòng khác giá trị cùng khóa.
Đã kiểm tra khóa duy nhất, ghi/đọc lại Parquet và SQLite integrity_check.
Coverage được suy ra từ QA hoàn tất của nguồn và thuật toán phân công được khai báo;
không có checkpoint RPC thô của các thành viên để kiểm chứng độc lập từng truy vấn.

Không gắn `complete=true`, không tự điền số dư bằng 0 cho phần chưa cào, không ghi đè bộ chính thức.
Giữ nguyên tất cả file nguồn.

## Bổ sung phần 004

Người phụ trách phần 004 cần nhận bản code mới nhất và đúng file
`config/n3_compound_team_assignment.json` do trưởng nhóm tạo, rồi chạy tại gốc dự án:

```cmd
.\.venv\Scripts\python.exe N3_Compound_Q3_Overlap_VSCode/src/data/fetch_compound_overlap.py --aave data/processed/aave/account_reference.parquet --snapshot-metadata data/processed/aave/snapshot_metadata.json --max-wallets 0 --num-shards 6 --shard-index 4 --assignment-file N3_Compound_Q3_Overlap_VSCode/config/n3_compound_team_assignment.json --batch-size 1 --wallet-chunk 1 --rpc-interval 0.2 --overwrite
```

Phải có `--assignment-file`; chỉ đổi mã phần mà thiếu tham số này sẽ tiếp tục dùng cách chia cũ.
Nếu nhiều người dùng chung tài khoản RPC, cân nhắc khoảng nghỉ 1 giây thay cho 0,2 giây.
Lệnh trên hoàn thành toàn bộ phần 004 đúng phân công; nếu không có checkpoint phù hợp,
nó sẽ kiểm tra lại cả phần giao nhau đã có trong bộ 004 cũ.
Không cần chạy lại năm phần 000/001/002/003/005.

Kết quả mới cần nằm tại:

```text
N3_Compound_Q3_Overlap_VSCode/data/processed/compound/team_6105d9df30ba/shard_004_of_006/
```

Sau `[QA] COMPLETE`, kiểm tra:

```cmd
.\.venv\Scripts\python.exe N3_Compound_Q3_Overlap_VSCode/scripts/qa_n3_compound.py N3_Compound_Q3_Overlap_VSCode/data/processed/compound/team_6105d9df30ba/shard_004_of_006
```

Gửi lại cả thư mục 004 mới khi QA PASS. Khi đó có thể ghép chính thức bằng công cụ
`merge_n3_compound.py` với đúng sáu phần cùng phân công.

## Tái lập bản tổng hợp tạm này

Từ gốc dự án, dùng tên thư mục output mới để không ghi đè bản hiện tại:

```cmd
.\.venv\Scripts\python.exe scripts/reconcile_compound_exports.py --inputs N3_Compound_Q3_Overlap_VSCode/data/processed/compound/team_6105d9df30ba/shard_000_of_006 shard_001_of_006/shard_001_of_006 shard_002_of_006/shard_002_of_006 shard_003_of_006/shard_003_of_006 shard_004_of_006/shard_004_of_006 shard_005_of_006 --output N3_Compound_Q3_Overlap_VSCode/data/processed/compound/consolidated_partial_NEW
```
