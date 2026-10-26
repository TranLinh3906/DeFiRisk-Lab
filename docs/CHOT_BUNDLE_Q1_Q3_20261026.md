# Chốt bundle Q1–Q3: P0.1–P0.5 — 26/10/2026

**Nguồn duy nhất dùng viết bài:** `outputs/n5_official_release_20261026/`.

- [Báo cáo kết quả](../outputs/n5_official_release_20261026/results/REPORT.md).
- [Danh sách bảng và hình chính thức, kèm SHA-256](../outputs/n5_official_release_20261026/metadata/publication_artifacts.csv).
- [Kết luận sáu giả thuyết](../outputs/n5_official_release_20261026/results/hypothesis_conclusions.csv).
- [Đối chiếu 18 định nghĩa KPI](../outputs/n5_official_release_20261026/metadata/kpi_release_dictionary.csv).
- [Kiểm tra số liệu và ES99](../outputs/n5_official_release_20261026/metadata/scientific_consistency.json).

## P0.1 — Điều tra 96 đường dẫn

Kiểm toán 40 manifest ghi nhận 2.078 tham chiếu khớp, 239 lượt khác hash và 48 lượt thiếu. Những lượt lỗi lặp lại quy về **96 chuỗi đường dẫn khai báo / 95 tệp sau chuẩn hóa**; một đường dẫn tương đối và tuyệt đối cùng chỉ một tệp.

| Nhóm tệp sau chuẩn hóa | Số tệp | Xử lý |
|---|---:|---|
| Mã/cấu hình khác phiên bản | 41 | Giữ hash lịch sử; chỉ dùng đúng source_versions đã ghi trong lần chạy được chọn |
| Dữ liệu/đầu ra lịch sử khác phiên bản | 28 | Đối chiếu Git/phiên bản hash; chỉ lấy bản khớp manifest của phân tích hiện hành |
| Khác biệt qua hậu xử lý cấu trúc đã ghi nhận | 10 | Phân biệt manifest trước hậu xử lý với các đầu ra sau sửa precision/mẫu số; không dùng manifest trung gian làm master |
| Đường dẫn intermediate/partial không còn tồn tại | 16 | Loại khỏi nguồn phát hành hiện hành; không tạo file giả hoặc điền zero |

Đã tìm được chứng cứ byte chính xác cho **98/141 phiên bản hash lịch sử** từ Git hoặc kho mã lưu theo hash. Phần chưa khôi phục được được ghi rõ; không tuyên bố đã tái tạo đầy đủ mọi bundle cũ. Bốn tệp dữ liệu hiện hành có tham chiếu cũ khác hash vẫn được dùng ở **phiên bản mới khớp manifest đã chọn**, sau đó sao chép vào bundle. Tám đường dẫn mã hiện hành có phiên bản đã được lưu theo hash của giai đoạn chính thức; các đường dẫn live không phải nguồn kiểm hash của bản phát hành.

Chi tiết từng tham chiếu: [manifest_reference_failures.csv](../outputs/n5_official_release_20261026/provenance/manifest_reference_failures.csv). Quyết định từng tệp và chứng cứ: [path_dispositions.csv](../outputs/n5_official_release_20261026/provenance/path_dispositions.csv).

## P0.2 — Chọn một bộ kết quả

Nguồn phân tích được chọn là `n5_hypothesis_completion_20261026`, gồm 120.000 draw MC, 1.458 ô CF, 69 can thiệp nguồn/cạnh và 48 trường hợp Compound. Bản chốt giữ nguyên byte của toàn bộ 196 artifact phân tích và sao chép 53 đầu vào đã kiểm hash. `provenance/analysis_manifest.json` khóa quan hệ giữa bản phát hành và lần chạy gốc.

Mã thực thi từng giai đoạn nằm tại `results/source_versions/`; không tự thay bằng code workspace đang sửa. `metadata/input_index.csv` ánh xạ đường dẫn gốc sang input nội bộ. Không có mô phỏng mới hoặc số liệu được chỉnh để tạo bản chốt.

## P0.3 — Manifest và tính ổn định

Manifest chính thức liệt kê cả `results/` và các input nội bộ. `verify_manifest` kiểm byte toàn bundle; `verify_release` kiểm thêm input được khóa trong inventory, quan hệ với manifest cha và block B1. Đổi hash một kết quả rồi tạo lại inventory vẫn bị chặn bởi hash của kết quả trong manifest cha.

`.gitattributes` tắt chuyển đổi newline cho bản đóng gói và source_versions của phân tích được chọn. Các manifest lịch sử giữ nguyên để truy nguyên; trạng thái lỗi của chúng không được tự đổi thành sạch bằng cách tính lại hash của phiên bản khác.

## P0.4 — Test còn hiệu lực

Test cũ `test_empirical_q1_q3_bundle_manifest_and_kpis` kiểm bundle trung gian 228 lượt CF và 18 mapping, nên không còn là tiêu chí phát hành hiện hành. Đã thay bằng `test_official_q1_q3_release_manifest_and_scientific_scope`, kiểm kết quả đã chọn, input đóng băng, 120.000 draw, 1.458 ô, B1 và định nghĩa KPI.

Giữ các test hành vi mô hình hiện có; bổ sung bốn guard: sửa workspace không làm sai hash input đã đóng băng, input bị sửa phải bị phát hiện, tính lại hash không hợp thức hóa kết quả khác lần chạy cha, và chặn path traversal. Không dùng skip/xfail để che lỗi bundle cũ. Log toàn suite và mã thoát thật được lưu tại `provenance/pytest_full_suite.*` trong bản đã seal.

**Đối chiếu bổ sung:** test lịch sử khôi phục từ Git hiện chạy riêng vẫn PASS; `verify_manifest` của bundle cũ chỉ kiểm `files` và trả sạch, còn audit phát hiện một input mã nguồn khác phiên bản. Thiếu traceback lần thất bại ban đầu nên chưa quy được nguyên nhân lịch sử. Việc thay test là cập nhật phạm vi nghiệm thu, không phải bằng chứng đã sửa được nguyên nhân đó. Xem [bằng chứng tái kiểm](../outputs/n5_citation_pack_20261026/obsolete_test_diagnosis.json) và [danh sách vấn đề](../outputs/n5_citation_pack_20261026/resolved_and_open_issues.csv).

## P0.5 — Định nghĩa, đơn vị và kết luận

- B1 chính thức là **20458637**, đối chiếu cả config và baseline Parquet đã đóng băng.
- Q1 chính dùng ghép equity 1:1 và phân phối trước từng sự kiện. Các phân phối khủng hoảng được gọi đúng là sensitivity theo kỳ hạn.
- ES99 chính là trung bình đúng 100 giá trị lớn nhất trong 10.000 draw, có zero; không dùng lọc tie-inclusive `loss>=VaR` hoặc trung bình ES của từng ví thay cho ES của nhóm.
- M08 chuẩn hóa theo 100.000 USD equity là biến thể khác ratio-of-sums phần trăm. M09 không gắn nhãn triệu USD cho kết quả USD/100.000 equity.
- Số ví mới đủ điều kiện khác số ví lần đầu được thực thi vòng sau. Con số 17/54 của H2a dùng endpoint eligibility và lượng bán vòng đầu.
- H3 chính dùng HF₀≥1,10, vẫn khỏe sau shock, cùng điều kiện collateral khác STETH/WSTETH đã khai báo; không trộn với ngưỡng 1,05 trong dictionary lịch sử.
- M14 freeze nhân quả, M16 CPCI đầy đủ, M17 edge-share thực tế và M18 node-specific DebtRank chưa được công bố như chỉ tiêu gốc ở bộ primary này. Có bảng đối chiếu rõ proxy/biến thể; không gọi đủ 18 mapping là đủ 18 chỉ tiêu khoa học.
- Các trạng thái H1a–H3b giữ nguyên từ bảng kết luận được khóa: không ép H1a hoặc chữ U thành được xác nhận, và không gọi feedback giả định là lây lan nhân quả đã quan sát.

## Kiểm tra

```powershell
.\.venv\Scripts\python.exe -B -X utf8 -m scripts.n5_finalize_release --stage verify
.\.venv\Scripts\python.exe -B -X utf8 -m pytest tests/ -q -p no:cacheprovider --tb=short
```

Bản đóng băng là căn cứ đối chiếu bảng, hình, đơn vị và câu kết luận của bài viết. Việc tăng số lần Monte Carlo hoặc đổi mô hình không thuộc bước chốt kỹ thuật này.
