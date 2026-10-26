# Báo cáo N5 Q1–Q3 sau rà soát ngày 26/10/2026

**Cập nhật kết quả hiện hành:** [gói hoàn thiện kiểm định sáu giả thuyết](../outputs/n5_hypothesis_completion_20261026/REPORT.md). Nội dung dưới đây ghi nhận bản rà soát trước; các kiểm định bổ sung và kết luận mới nằm trong gói cập nhật.

Bản này thay thế các tuyên bố “hoàn thành 100%” và “18/18 KPI đã được nghiệm thu” trong báo cáo trước. Bản trước được giữ tại `outputs/research_review_fix_20261026/source_before/BAO_CAO_HOAN_THANH_N5_Q1_Q3_20261026.md` để truy nguyên.

**Gói hiện hành:** [Báo cáo sinh từ artifact đã sửa](../outputs/n5_review_corrected_20261026/REPORT.md).

## Những lỗi đã sửa

| Hạng mục | Cách xử lý | Giới hạn kết luận |
|---|---|---|
| P0 — Compound WETH gắn nhãn USD | Chuyển native WETH sang USD bằng oracle cùng snapshot; giữ số native và tỷ giá | Raw cũ được giữ nguyên; dùng exposures_usd của gói mới |
| P0 — Mapping bị gọi là nghiệm thu | Tách kiểm file/cột, kiểm số học/hồi quy và nghiệm thu nghiên cứu | Mapping đủ 18 mã không có nghĩa đã nghiệm thu khoa học |
| Q1 — Hai giai đoạn loss khác nhau | MC xuất trước và sau thanh lý bằng cùng engine, route và số dư token | Xác suất có điều kiện trên phân phối hiệu chỉnh |
| Q1 — ES99 trên 19 stress | Stress tất định được tách khỏi phân phối MC; ES99 chính gồm cả loss bằng 0 | ES99 trên 19 điểm đồng trọng số bằng điểm lớn nhất |
| Q1 — Kiểm định | Phân tầng equity; KS, Mann–Whitney, bootstrap ES99 ghép cặp và Holm | Chưa phải ước lượng nhân quả của looping |
| Q2 — Thiếu lưới độ nhạy | 3 snapshot × 19 shock × 3 độ sâu × 4 CF × 2 chế độ phản hồi | Độ sâu và feedback là các giả định phản thực có tên |
| Q3 — Nhầm lặp lại với lây lan | Đếm riêng giao dịch, ví thực thi lần đầu và ví mới đủ điều kiện vòng sau | Fixed oracle không có kênh truyền giá nội sinh |
| Q3 — Feedback | Dùng marginal cost suy trên đường DEX để thay đổi giá token bán; cập nhật cả collateral và debt | Độ truyền vào oracle chưa được hiệu chỉnh từ dữ liệu thực |
| Q3 — Compound | Tách absorb và buyCollateral; dùng reserve/pause/storefront tại 6 market–snapshot | Mẫu overlap, số học float; chưa phải emulator hợp đồng toàn thị trường |
| Q3 — Mạng null | Double-edge swap từng lớp, kiểm giữ bậc từng ví và từng token | Không giữ tổng trọng số token; chưa kết luận power-law hoặc nhân quả centrality |
| P2 — Hình/bảng | Sinh từ CSV/Parquet mới và khóa SHA-256 | Hình không nâng mức nghiệm thu |

## Nguồn kết quả hiện hành

- [Giả định nghiên cứu](../outputs/n5_review_corrected_20261026/research_settings.json).
- [Kiểm số học và hồi quy](../outputs/n5_review_corrected_20261026/validation.json).
- [Trạng thái từng KPI](../outputs/n5_review_corrected_20261026/kpi_acceptance.csv).
- [Q1: trước/sau thanh lý](../outputs/n5_review_corrected_20261026/q1_mc_comparison.csv) và [kiểm định](../outputs/n5_review_corrected_20261026/q1_hypothesis_tests.json).
- [Q2: lưới độ nhạy](../outputs/n5_review_corrected_20261026/q2_shock_depth_cf_feedback_grid.csv).
- [Q3: ví mới bị kích hoạt](../outputs/n5_review_corrected_20261026/q3_transitions.csv), [ledger Compound](../outputs/n5_review_corrected_20261026/q3_comet_absorb_buy_ledger.parquet) và [mạng null](../outputs/n5_review_corrected_20261026/q3_network_null_tests.json).
- [Q3: kiểm định cấu trúc ví](../outputs/n5_review_corrected_20261026/q3_wallet_structural_tests.json).
- [Manifest](../outputs/n5_review_corrected_20261026/manifest.json).

`validation.json` xác nhận kiểm số học/hồi quy. `kpi_acceptance.csv` ghi riêng trạng thái triển khai và giới hạn từng chỉ tiêu. `research_acceptance` vẫn phản ánh phần cần kiểm định ngoài mô hình; không đổi sang “đã nghiệm thu” chỉ vì chương trình chạy xong.

Không coi thiếu route/vượt miền quote là bằng chứng freeze. Rút base supply giả định không phải hành vi quan sát hay khoản trả nợ tự động cho Aave. Calibration trước A áp vào B1/B2 là phản thực hồi cứu, chưa phải kiểm định ngoài mẫu không nhìn trước.

## Tái lập

Chạy từ thư mục dự án và chọn output directory mới:

```powershell
.\.venv\Scripts\python.exe -B -X utf8 -m scripts.n5_resolve_research_review --output-dir outputs/N5_REVIEW_NEW
.\.venv\Scripts\python.exe -B -X utf8 -m scripts.n5_finalize_structural_review --bundle outputs/N5_REVIEW_NEW
.\.venv\Scripts\python.exe -B -X utf8 -m pytest tests/ -q -p no:cacheprovider --tb=short
```

Trạng thái Comet đã thu thập được giữ cùng raw checkpoint tại `outputs/research_review_fix_20261026/comet_state/`. Thu thập lại chỉ đọc blockchain:

```powershell
.\.venv\Scripts\python.exe -B -X utf8 -m scripts.n5_collect_comet_state
```

Cơ chế Comet được đối chiếu với [tài liệu chính thức Compound III](https://docs.compound.finance/liquidation/).
