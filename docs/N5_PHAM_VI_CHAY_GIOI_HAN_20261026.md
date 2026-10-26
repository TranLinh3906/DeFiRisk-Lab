# Phạm vi chạy giới hạn N4/N5 — 26/10/2026

**Bản lịch sử trước khi chốt gói giả định mới.** Phạm vi ba route vẫn giữ, nhưng bản chính mới cộng dồn xuyên vòng, ưu tiên HF thấp và dùng phí từng pool. Xem [gói đã chốt và runner hiện hành](N5_GOI_GIA_DINH_DA_CHOT_20261026.md); không dùng kết quả reset trong tài liệu này làm kết quả bản chính mới.

Theo lựa chọn của người dùng: hoàn thành bản chạy trong phạm vi dữ liệu hiện có và bổ sung 27 quote đã lên danh sách khi chúng được thu thập. Chưa mở rộng lên 8 quy mô cực lớn hoặc 461 hướng-snapshot ngoài bộ route đang chọn.

## Kết quả đã chạy

- Bundle: `outputs/n5_preparation/n5_bounded_scope_current_20261026/`.
- CSV: `outputs/n5_preparation/n5_bounded_scope_current_readable_20261026/`.
- Gate đầu vào cả A/B1/B2 đạt; 57 tổ hợp phương án chính, 57 kiểm tra độ nhạy và 57 baseline nhu cầu không trượt giá, tổng 171 lần chạy.
- Không có lần chạy chính hoặc sensitivity chạm giới hạn 100 vòng; số vòng lớn nhất là 90. Dừng theo điều kiện mô hình không đồng nghĩa đã thực thi mọi khoản nợ: các route ngoài phạm vi và quy mô thiếu quote vẫn được ghi riêng.
- Đã nhận 0/27 quote mới, còn chờ 27. Trạng thái `completed_current_data_planned_quotes_pending`. Giới hạn quan sát hiện tại vẫn bắt đầu từ 1.000 USD; không tự hạ theo quy mô dự kiến trong CSV yêu cầu.
- CSV `primary_execution_summary.csv` là bảng chính; `quote_domain_limits.csv` ghi miền quan sát từng pool/snapshot; `unsupported_candidates.csv` tách các lý do ngoài phạm vi; `planned_quote_inventory.csv` theo dõi đủ 27 yêu cầu.
- 14 kiểm thử đạt. Đã kiểm tra thực tế chế độ yêu cầu đủ quote: dừng trước khi tạo bundle vì còn thiếu 27 quote. Chưa gọi RPC hoặc chi tiêu quota để thu thập chúng.
- Các file đầu vào và artifact đã kiểm tra SHA-256. Manifest cũ của các lần rà soát trước vẫn lưu nguồn tại thời điểm trước; không sửa lịch sử để khớp code mới.

## Phạm vi đã cấu hình

Nguồn cấu hình: `config/n5_execution_scope.json`.

- Snapshot A/B1/B2: 26179841 / 20458637 / 23543615.
- Ba hướng và đúng pool hiện có: wstETH→WETH, WETH→USDC, WBTC→WETH. Cặp cùng token được xử lý như identity, không cần quote DEX. Collateral và debt ngoài ba route vẫn giữ trong tính HF; không xóa vị thế để làm thay đổi rủi ro ví.
- Chỉ giữ ví có nợ tham chiếu ban đầu >=100 USD sau khi gate đầu vào đạt.
- 19 kịch bản mỗi snapshot, cùng bộ quy tắc shock của N4. Phương án chính là nội suy tuyến tính giữa các quan sát; power-law là kiểm tra độ nhạy. Đây là lựa chọn có giới hạn theo dữ liệu, không khẳng định nội suy luôn dự báo chính xác hơn.
- Giới hạn quy mô của mỗi pool/snapshot được tính từ quote đã kiểm tra. Kiểm tra lượng bán đơn lẻ và lượng bán cộng dồn trong vòng. Không ngoại suy hoặc chia nhỏ để áp lại quote ban đầu.
- Quote chỉ nằm trên danh sách dự kiến không được tính vào miền quan sát. Danh sách 27 yêu cầu được cố định bằng SHA-256.
- Ngoài phạm vi route: `outside_selected_scope`. Trong route nhưng ngoài miền quote: `outside_quote_domain`. Các trạng thái này là chưa đánh giá được, không phải freeze đã chứng minh.
- Mô hình giữ giá oracle cố định sau shock, giả định gas 5 USD/lần và phí bổ sung 0,05%. Pool cộng dồn bán trong vòng và reset giữa các vòng. Chưa nghiệm thu giả định thanh khoản hồi phục, truyền dẫn sang oracle, gas thực tế hoặc toàn bộ quy tắc hợp đồng.
- Tăng giới hạn từ 30 lên 100 vòng để xem thêm các trường hợp trước đây bị dừng sớm; kết quả vẫn phải ghi rõ trường hợp chạm 100 vòng, không tự coi là hội tụ. Không đồng nhất một vòng với một block hoặc khoảng thời gian thực.

## Nhận 27 quote

File giao N3: `outputs/n5_preparation/n3_quote_handoff_20261026/n3_quote_first_batch_27.csv`.

N3 cung cấp Parquet có cùng schema raw với `outputs/n3_priority2_quote_grid_v1/n3_multivolume_rpc_verified_only.parquet`. Giữ quy mô yêu cầu chính xác trong CSV, định danh pool/chiều bán/block, raw amount vào/ra, decimals, giá oracle dùng quy đổi, spot reference, trạng thái `RPC_VERIFIED`, block hash và verified block hash. Quote lỗi giữ riêng, không điền kết quả giả hoặc gắn verified cho yêu cầu chưa chạy.

Runner kiểm tra dữ liệu raw/block/hash như bộ cũ; chỉ nhận quote bổ sung thuộc đúng 27 yêu cầu. Nhãn verified và đối chiếu nội bộ không thay thế bằng chứng truy vấn RPC của N3.

## Chạy bản hiện có

```powershell
.\.venv\Scripts\python.exe -B -X utf8 -m scripts.n5_review_price_impact --scope-config config/n5_execution_scope.json --output-dir outputs/n5_preparation/N5_BOUNDED_CURRENT_NEW
.\.venv\Scripts\python.exe -B -X utf8 -m scripts.n5_report_bounded_scope --run-dir outputs/n5_preparation/N5_BOUNDED_CURRENT_NEW --output-dir outputs/n5_preparation/N5_BOUNDED_CURRENT_READABLE_NEW
```

Thiếu quote mới vẫn chạy được trên dữ liệu cũ và ghi số quote đang chờ. Không ghi đè bundle cũ hoặc marts.

## Chạy bản có đủ 27 quote

```powershell
.\.venv\Scripts\python.exe -B -X utf8 -m scripts.n5_review_price_impact --scope-config config/n5_execution_scope.json --additional-quotes outputs/N3_NEW_QUOTES/quotes_27_verified.parquet --require-complete-quotes --output-dir outputs/n5_preparation/N5_BOUNDED_WITH27_NEW
.\.venv\Scripts\python.exe -B -X utf8 -m scripts.n5_report_bounded_scope --run-dir outputs/n5_preparation/N5_BOUNDED_WITH27_NEW --output-dir outputs/n5_preparation/N5_BOUNDED_WITH27_READABLE_NEW
```

Đường dẫn quote bổ sung ở ví dụ cần thay bằng file N3 bàn giao thực tế. Nếu thiếu một trong 27 quote, chế độ `--require-complete-quotes` dừng trước khi tạo bundle. Không tự tăng giới hạn quy mô theo tên file hoặc danh sách yêu cầu.

## Điều kiện hoàn thành bản giới hạn

1. Gate đầu vào đạt cho ba snapshot.
2. Nhận và kiểm tra đủ 27 quote nếu gắn nhãn bản có 27 quote; ghi rõ quote pending ở bản chỉ có dữ liệu hiện tại.
3. Chạy đủ 57 tổ hợp cho phương án chính và kiểm tra độ nhạy, giữ nguyên cohort.
4. Xuất miền quote và các trường hợp ngoài phạm vi. Ví có nhiều cặp có thể vừa thực thi cặp hỗ trợ vừa có cặp chưa hỗ trợ; không cộng các thống kê chồng lấp thành coverage toàn mẫu.
5. Ghi trạng thái dừng/hội tụ, tham số và manifest. Đây là hoàn thành bản thực thi trong phạm vi đã chọn, chưa phải nghiệm thu cascade toàn hệ thống hay Q3.
