# Hoàn thiện kiểm định Q1–Q3 — 26/10/2026

**Bản chốt dùng viết bài:** [báo cáo chính thức](../outputs/n5_official_release_20261026/results/REPORT.md) và [danh sách artifact](../outputs/n5_official_release_20261026/metadata/publication_artifacts.csv). [Biên bản P0.1–P0.5](CHOT_BUNDLE_Q1_Q3_20261026.md) ghi nhận kiểm tra hash/test/định nghĩa. Các liên kết dưới đây giữ nguồn phân tích trước khi đóng gói.

[Báo cáo đầy đủ](../outputs/n5_hypothesis_completion_20261026/REPORT.md) · [Kết luận từng giả thuyết](../outputs/n5_hypothesis_completion_20261026/hypothesis_conclusions.csv) · [Biên bản kiểm tra](../outputs/n5_hypothesis_completion_20261026/validation.json).

## Phần đã thực hiện

| Hạng mục | Đầu ra |
|---|---|
| Hiệu chỉnh trước sự kiện | 366 mức giá trước B1 và B2, cùng nguồn trước A. Quote dùng hiệu chỉnh Q1 không vượt cutoff của snapshot tương ứng |
| Depeg khủng hoảng | 90/92 ngày tháng 5–7/2022; hai ngày thiếu được báo rõ, không nội suy và không nối lợi suất qua khoảng trống |
| Q1 | 120.000 lượt MC, bốn phân phối/kỳ hạn, ghép equity 1:1 không lặp, bootstrap ES99 và kiểm định độ cong trực tiếp |
| Q2 | Chín CF cố định 20%–100%, ba độ sâu, sáu shock, ba snapshot, ba chế độ feedback: 1.458 ô; kiểm tra hai độ dốc biên và CI điểm đảo chiều |
| Hiệu chỉnh feedback | 1.800 quan sát slot0 của ba pool, kiểm hash/time từ raw RPC; B1 ước lượng, B2 kiểm tra khả năng dự báo |
| Q3 | Tách tác động ngoại sinh khỏi phần tăng thêm; lọc HF₀≥1,10, khỏe sau shock và có collateral khác STETH/WSTETH ít nhất 10%/100 USD |
| Cấu trúc mạng | Centrality với loss tăng thêm; ba null ghép degree/exposure; can thiệp top 1% nguồn, 19 nhóm nguồn đối chứng và từng route |
| Compound | Kiểm tra giá/eligibility trực tiếp và tăng thêm trên mẫu overlap; absorb/buyCollateral riêng, giữ đơn vị base và dùng lượng bán Aave cộng dồn vào pool |

## Kết luận phù hợp với bằng chứng

- **H1a:** không được ủng hộ trong thiết kế hiệu chỉnh trước sự kiện ở ba snapshot. So sánh khủng hoảng và kỳ hạn được báo riêng; không suy ra looping luôn an toàn.
- **H1b:** có độ cong quadratic dương ở một số điều kiện, nhưng kiểm tra lồi trên toàn miền chưa được thỏa mãn nhất quán.
- **H2a:** trong 54 cặp full-feedback, 17 cặp đồng thời tăng bán vòng đầu và số ví mới đủ điều kiện khi CF100 thay CF50; kết quả phụ thuộc điều kiện.
- **H2b:** không mặt cắt trung bình nào trong chín trường hợp thỏa đầy đủ chữ U và CI điểm cực tiểu nội miền.
- **H3a:** ba tuyến chưa cải thiện dự báo ngoài mẫu theo quy tắc kiểm tra. Proxy bằng 0 là fallback bảo thủ, không chứng minh tác động thật luôn bằng 0. Tỷ giá oracle wstETH/WETH thiếu biến thiên ở độ phân giải quan sát.
- **H3b:** đã có kiểm định với loss tăng thêm, null và can thiệp nguồn/cạnh; kết quả trong mô hình không đồng nghĩa xác nhận power-law hay nhân quả trên thị trường thật.

Đã hoàn thiện các phép kiểm định trong phạm vi dữ liệu và mô hình đã khai báo. Không đánh dấu cả sáu giả thuyết là đúng chỉ vì chạy đủ thí nghiệm.

## Giới hạn cần giữ trong báo cáo

Chỉ dùng ba route và miền quote đã chốt. Vượt miền là unknown, không tự coi là freeze. Bốn phân phối MC có kỳ hạn và nguồn khác nhau, không gộp thành xác suất khủng hoảng vô điều kiện. Matching và HC3 không loại bỏ mọi nhiễu hoặc phụ thuộc mạng.

Kênh Compound là sensitivity giá thị trường chung một chiều từ Aave, chưa đóng vòng bán Comet quay lại Aave hoặc nhận dạng hành vi rút vốn. Không đồng nhất nợ được absorb với tổn thất ròng của giao thức, và không khẳng định oracle của hai giao thức luôn phản ứng giống nhau.

## Tái lập

Dùng đầu vào đã thu thập và chọn một thư mục mới:

```powershell
.\.venv\Scripts\python.exe -B -X utf8 -m scripts.n5_complete_hypotheses --output-dir outputs/N5_HYPOTHESES_NEW
.\.venv\Scripts\python.exe -B -X utf8 -m pytest tests/ -q -p no:cacheprovider --tb=short
```

Nếu cần thu thập lại dữ liệu nguồn, chạy collector giá công khai và collector DEX chỉ đọc archive RPC trước runner. Giá lịch sử có raw cache; DEX cần RPC Ethereum archive đã cấu hình. Không có thao tác gửi giao dịch.

```powershell
.\.venv\Scripts\python.exe -B -X utf8 -m scripts.n5_collect_pre_event_market
.\.venv\Scripts\python.exe -B -X utf8 -m scripts.n5_collect_dex_feedback
```

`input_freeze.json`, `feedback_input_freeze.json`, `compound_input_freeze.json` giữ hash trước khi dùng từng nguồn; `source_versions/` và marker từng giai đoạn giữ mã đã thực thi; `manifest.json` băm nguồn và toàn bộ đầu ra.
