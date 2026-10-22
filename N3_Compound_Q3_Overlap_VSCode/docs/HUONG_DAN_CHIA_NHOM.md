# Chia nhóm cào Compound III

Code hỗ trợ `--num-shards N --shard-index K`, với K bắt đầu từ 0.
Ví được sắp xếp theo địa chỉ rồi chia luân phiên, không trùng giữa các phần.
Cùng một ví luôn do một người xử lý ở cả snapshot A/B và hai market.

**Nếu đã cào một phần và muốn giữ khoảng 7.000 ví trên máy trưởng nhóm:** dùng
[hướng dẫn chia theo checkpoint](HUONG_DAN_CHIA_TU_CHECKPOINT.md). Cách đó bảo toàn phân công
cho các ví đã xử lý; không dùng lệnh chia đều bên dưới cho cùng đợt phân công.

## Chuẩn bị trên từng máy

- Dùng cùng bản code mới, cùng `account_reference.parquet`, `snapshot_metadata.json`
  và `config/n3_compound_markets.csv`. Không chỉnh dữ liệu đầu vào giữa các lần chạy.
- Mở terminal ở gốc repo, nơi có `data/processed/aave/`.
- Tạo môi trường Python và cài thư viện nếu chưa có:

```cmd
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r N3_Compound_Q3_Overlap_VSCode/requirements_n3_compound.txt
```

- Mỗi người cấu hình RPC trong `N3_Compound_Q3_Overlap_VSCode/.env`.
  Nên dùng tài khoản RPC riêng nếu cần phân bổ hạn mức; nhiều máy dùng chung tài khoản
  có thể vẫn chịu cùng giới hạn. Không chia sẻ file `.env` hoặc API key.
- Nếu đang chạy bản cào toàn bộ trên máy mình, dừng bằng Ctrl+C và chờ chương trình thoát
  trước khi chuyển sang chia nhóm. Không xóa checkpoint cũ.

## Phân công nhóm 6 người

| Thành viên | `--shard-index` | Số ví với bộ đầu vào hiện tại |
|---|---|---|
| Bạn | 0 | 4.774 |
| Người 2 | 1 | 4.774 |
| Người 3 | 2 | 4.773 |
| Người 4 | 3 | 4.773 |
| Người 5 | 4 | 4.773 |
| Người 6 | 5 | 4.773 |

Tổng cộng 28.640 ví. Tất cả dùng `--num-shards 6`; mỗi người dùng một mã phần khác nhau từ 0 đến 5.

Mỗi người dán một dòng sau trong CMD, thay giá trị sau `--shard-index` bằng phần được giao:

```cmd
.\.venv\Scripts\python.exe N3_Compound_Q3_Overlap_VSCode/src/data/fetch_compound_overlap.py --aave data/processed/aave/account_reference.parquet --snapshot-metadata data/processed/aave/snapshot_metadata.json --max-wallets 0 --batch-size 10 --wallet-chunk 1 --num-shards 6 --shard-index 0 --overwrite
```

Thêm `--dry-run` để kiểm tra cách chia mà không gọi RPC. Chạy lại đúng lệnh để tiếp tục
khi gián đoạn. Không đổi số phần hoặc mã phần khi resume.

Kết quả và checkpoint riêng cho phần 0:

```text
N3_Compound_Q3_Overlap_VSCode/data/processed/compound/shard_000_of_006/
N3_Compound_Q3_Overlap_VSCode/data/raw/compound/shard_000_of_006/n3_overlap_checkpoint.jsonl
```

Các phần còn lại là `shard_001_of_006`, `shard_002_of_006`, `shard_003_of_006`,
`shard_004_of_006`, `shard_005_of_006`.
Trên máy đã cào trước đó, script cũng đọc checkpoint gốc để tái sử dụng kết quả phù hợp,
không sửa checkpoint gốc. Giữ cả checkpoint gốc lẫn checkpoint của phần khi resume.
Checkpoint chỉ được dùng lại khi metadata phù hợp; không reset nếu chưa xác minh nguyên nhân.

## Kiểm tra và bàn giao

Chờ `[QA] COMPLETE` cho phần của mình rồi chạy (đổi tên thư mục theo phần):

```cmd
.\.venv\Scripts\python.exe N3_Compound_Q3_Overlap_VSCode/scripts/qa_n3_compound.py N3_Compound_Q3_Overlap_VSCode/data/processed/compound/shard_000_of_006
```

Sau khi QA PASS, mỗi người gửi nguyên thư mục kết quả của phần mình (4 Parquet và JSON quality)
cho người tổng hợp. Không cần gửi `.env` hoặc checkpoint thô để ghép bảng.

## Ghép trên máy tổng hợp

Đặt đủ 6 thư mục `shard_000_of_006` đến `shard_005_of_006` vào
`N3_Compound_Q3_Overlap_VSCode/data/processed/compound/`, rồi chạy:

```cmd
.\.venv\Scripts\python.exe N3_Compound_Q3_Overlap_VSCode/scripts/merge_n3_compound.py --inputs N3_Compound_Q3_Overlap_VSCode/data/processed/compound/shard_000_of_006 N3_Compound_Q3_Overlap_VSCode/data/processed/compound/shard_001_of_006 N3_Compound_Q3_Overlap_VSCode/data/processed/compound/shard_002_of_006 N3_Compound_Q3_Overlap_VSCode/data/processed/compound/shard_003_of_006 N3_Compound_Q3_Overlap_VSCode/data/processed/compound/shard_004_of_006 N3_Compound_Q3_Overlap_VSCode/data/processed/compound/shard_005_of_006
```

Script kiểm tra QA của từng phần, đủ mã phần, cùng tập ví được chọn, cùng metadata market,
snapshot, tổng coverage và không trùng khóa dữ liệu. QA được chạy cả trên bộ ghép trước khi xuất.
Kết quả nằm tại `N3_Compound_Q3_Overlap_VSCode/data/processed/compound/merged/`.
Nếu cần thay kết quả ghép cũ, thêm `--overwrite` để sao lưu và cập nhật.

Chia máy không giảm tổng số truy vấn cần thiết và không bảo đảm tăng tốc nếu cùng bị giới hạn RPC.
Phạm vi vẫn là ví Aave có dư nợ được chọn trên hai market Compound III USDC/WETH tại A/B,
không phải toàn bộ người dùng Compound.
