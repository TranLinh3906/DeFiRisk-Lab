# Cập nhật N5: shard 001 B1/B2 đã đủ

Nguồn `shard_001_of_006/shard_001_of_006/n3_compound_quality.json` hiện ghi đủ A/B/B1/B2, `complete=true`, 30.026 truy vấn thành công, trong đó bổ sung 12.870 truy vấn lịch sử. Đã kiểm tra dữ liệu thực tế, phân công và QA bằng script gộp; không chỉ dựa vào cờ complete.

## Kết quả gộp mới

`outputs/compound_consolidated_A_B1_B2_20261025_complete/`

| Snapshot | Đã có / cần có | Coverage | Thiếu |
|---|---:|---:|---:|
| A | 56.986 / 56.986 | 100% | 0 |
| B1 | 24.094 / 24.094 | 100% | 0 |
| B2 | 55.970 / 55.970 | 100% | 0 |

3.185 dòng positions, 2.083 dòng market accounts, 1.916 dòng overlap theo snapshot. QA từng nguồn và bộ gộp đều đạt; không có khóa trùng có giá trị xung đột. Bộ gộp cũ thiếu 12.870 truy vấn chỉ còn giá trị lịch sử.

## Bộ N5 cập nhật

`outputs/n5_preparation/n5_AB1B2_20261025_compound_complete/`

Runner mặc định đã chuyển sang bộ Compound đủ coverage. Accounts/exposures của cả ba snapshot mang nhãn `complete_target_queries`. Tên một số file đầu ra vẫn giữ chữ `partial` để tương thích; đọc cột coverage và báo cáo hiện tại để xác định trạng thái.

Các bộ N5 cũ được giữ nguyên; manifest cũ mô tả đúng nguồn tại lần chạy cũ, không dùng để kết luận thiếu shard trong hiện trạng mới.

## Điều kiện nào đã gỡ?

**Không còn phải chờ N3/nhóm cào Compound bổ sung shard 001 B1/B2.** Không còn 12.870 truy vấn unknown trong tập truy vấn mục tiêu.

N5 có thể dùng đầy đủ exposure A/B1/B2 trong phạm vi đã chọn. Tuy nhiên, nghiệm thu Q3 vẫn cần mô phỏng phản ứng ví và thực thi Comet, quy tắc truyền dẫn có cơ sở và kiểm định. Coverage đầy đủ không tự chứng minh lây lan, không mở rộng phạm vi thành toàn bộ Compound, và không thay thế các điều kiện dust, liquidity, holdout hoặc calibration Q1.

## Tái lập

```powershell
.\.venv\Scripts\python.exe -B -X utf8 scripts/consolidate_compound_ab1b2.py --output outputs/compound_AB1B2_NEW
.\.venv\Scripts\python.exe -B -X utf8 -m scripts.n5_prepare_handoff --compound-dir outputs/compound_AB1B2_NEW --output-dir outputs/n5_preparation/n5_NEW
```

Kiểm thử liên quan: 7 test `test_n5_readiness.py` đạt. Script gộp kiểm QA tất cả 10 bộ nguồn và bộ gộp, bao gồm đọc lại Parquet đầu ra. Bundle N5 kiểm hash nguồn trước/sau và manifest đầu ra.
