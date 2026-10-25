# Căn chỉnh Compound A/B theo checkpoint phân công chung

Phạm vi **chỉ A/B cũ**: A = 26179841, B = 26194188. Không thay đổi B1/B2, không dùng RPC, không reset checkpoint, không sửa dữ liệu nguồn.

## Đưa script vào dự án

Chép `fix_compound_ab_assignment.py` vào thư mục `scripts/` của `DeFiRisk-Lab_FULL`.

## 1) Kiểm tra an toàn (không ghi file)

```bash
cd ~/Downloads/DeFiRisk-Lab_FULL
python scripts/fix_compound_ab_assignment.py --repo . --allow-partial
```

Mặc định script **chỉ đọc**. Lệnh trên tự dò QA/Parquet và `*checkpoint*.jsonl` trong dự án; có thể mất chút thời gian nếu nhiều file. Lệnh sẽ báo `[MISSING] old A/B COMPLETE export shard 0` nếu chưa tìm được shard 0 như lần kiểm tra trước.

Nếu bản shard 0 nằm ở **một thư mục khác**, thêm `--extra-root` với đường dẫn thực tế, ví dụ:

```bash
python scripts/fix_compound_ab_assignment.py --repo . --allow-partial \
  --extra-root "$HOME/Downloads/Compound_shard0_backup"
```

Không cần di chuyển/xóa file. Có thể dùng `--extra-root` nhiều lần.

## 2) Xuất bản căn chỉnh **PARTIAL** ra folder mới (chỉ khi bước 1 không báo xung đột)

```bash
python scripts/fix_compound_ab_assignment.py --repo . --allow-partial --apply
```

Đầu ra mới nằm tại:

`outputs/compound_AB_checkpoint_aligned_<hash>_PARTIAL/`

Gồm các Parquet được phân theo `wallet_shards`, `n3_compound_alignment_report.json` và `verified_old_AB_checkpoint_keys.csv`.

**Lưu ý:** `PARTIAL` là báo cáo trung gian, **không phải** đầu ra Compound A/B đã hoàn thành. Các ví có số dư Compound bằng 0 có thể không xuất hiện trong Parquet. CSV checkpoint chỉ là danh sách các kết quả RPC thành công có bằng chứng thực tế, không phải checkpoint hoàn chỉnh để dùng `--resume`.

## 3) Khi nhận thêm được bản shard 0 hoàn chỉnh

Chạy lại ở bước 1 với `--extra-root` nếu cần, nhưng **không dùng `--apply` vào folder đã tạo**. Script sẽ tạo tên `_FULL_SOURCE_COVERAGE` nếu có đủ 6 nguồn phù hợp và không có ví chưa được phủ. `_FULL_SOURCE_COVERAGE` vẫn không khẳng định đã kiểm chứng mọi RPC zero-balance.

Nếu script báo `Multiple DIFFERENT complete A/B exports`, `conflicting rows`, `Fingerprint mismatch`, `outside its declared historical QA shard assignment`, hoặc lỗi JSONL có kết quả mâu thuẫn: **dừng**, không tự gỡ kiểm tra an toàn hoặc sửa hash.

## 4) Kiểm tra kết quả

Mở `n3_compound_alignment_report.json` và xem:

- `missing_source_shards`, `previous_assignment_wallets_uncovered`
- `deduplicated_identical_input_rows`
- `per_target_shard["4"].assigned_unique_wallets` (với phân công hiện tại phải là **4312**)
- `verified_checkpoint_successful_query_keys`
- `complete_for_all_original_A_B_queries_reverified` (được giữ là **false**, vì chưa có tập truy vấn gốc theo từng snapshot để khẳng định hoàn tất)

Không tự sửa `config/n3_compound_team_assignment.json`, `n3_compound_quality.json` gốc hoặc `fetch_compound_overlap.py` bản A/B1/B2. Không chạy `--reset-checkpoint` hay `git add -A` cho các output PARTIAL mà chưa duyệt.

## Giới hạn

Script không thể tự tạo dữ liệu shard 0 hoặc chứng minh những cuộc gọi RPC đã thành công khi checkpoint gốc không còn. Muốn lập **danh sách RPC còn thiếu chính xác** và chạy bổ sung, cần bản Aave `account_reference.parquet` nguyên gốc ứng với snapshot A/B cũ cùng danh mục Compound market và checkpoint thành công gốc. Không thay bằng bản A/B1/B2 hiện tại.
