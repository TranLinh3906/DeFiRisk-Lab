# N5 đối chiếu cập nhật N3 `901e740` — 26/10/2026

N3 đã bổ sung code kiểm tra depeg B1/B2, chọn cửa sổ log và thu thập receipt gas. Chưa đủ căn cứ nghiệm thu dữ liệu thực nghiệm. Đối chiếu offline, không gọi RPC và không thay dữ liệu nguồn.

## Depeg

- Có 98 dòng đúng các giai đoạn lịch sử; cả 98 giá trị stETH/ETH đều bằng 0,995. Chỉ có nhãn nguồn CoinGecko, chưa đối chiếu được với báo giá gốc trong lần rà soát này. Giá trị hằng không tự chứng minh dữ liệu giả, nhưng cần nguồn gốc và kiểm tra tái lập trước hiệu chỉnh mô hình.
- Báo cáo N3 chạy `window_hours=24` cho cả hai snapshot. B2 phải dùng 48 giờ theo quyết định đã chốt.
- Chạy lại code QA N3 với B1 24 giờ và B2 48 giờ: B1 đạt kiểm tra độ phủ cục bộ; B2 chỉ có 26/49 ô giờ hợp lệ (53,061%), trạng thái `INCOMPLETE_MARKET_SERIES`.
- 49 ô là cách code N3 tạo lưới từ giờ chứa snapshot 23:59:59; đây là độ phủ theo lưới của script, không phải 49 giờ holdout và không phải chứng nhận nguồn giá.
- N3 cần đối chiếu chuỗi với báo giá gốc, giữ thời điểm quan sát thực của ETH và stETH, bổ sung phần B2 còn thiếu. Không dùng thời điểm làm tròn đầu giờ để coi báo giá cuối giờ là đã biết trước sự kiện.

## Cửa sổ peak và log

- Kết quả `n3_peak300_liquidation_QA_FINAL` truyền block snapshot làm peak, trong khi code đặt peak ở giữa cửa sổ. Do đó B1 dùng 20458488–20458787 (25 sự kiện), B2 dùng 23543466–23543765 (0 sự kiện); có cả block trước snapshot.
- Theo quy tắc đã chốt (300 block có nhiều LiquidationCall nhất trong holdout), N5 dựng lại từ nguồn lịch sử:

| Snapshot | Cửa sổ đúng | Sự kiện | Ví |
|---|---|---:|---:|
| B1 | 20459042–20459341 | 644 | 467 |
| B2 | 23549924–23550223 | 648 | 434 |

- Khóa `(tx_hash, log_index)` khớp giữa nguồn Etherscan cục bộ và đầu vào kiểm định lịch sử. Đây là đối chiếu dữ liệu trong dự án, chưa phải truy vấn độc lập chứng minh đủ log.
- Code fetch mới đã hỗ trợ block đầu/cuối rõ ràng và output riêng. N3 cần chạy thu thập/đối chiếu đúng hai khoảng trên, cung cấp bằng chứng phân đoạn, trang truy vấn, retry và count; code có khả năng chạy không đồng nghĩa đã có kết quả.

## Gas và freeze

- Báo cáo `n3_gas_inputs_FINAL` gồm 16 giao dịch từ cửa sổ B1 đang chọn sai; không có receipt nào, không có USD gas nào. Trạng thái từng dòng là `RECEIPT_NOT_COLLECTED`.
- Code mới có tùy chọn thu thập `gasUsed`, `effectiveGasPrice`, kiểm tra block và checkpoint; mặc định không gọi RPC. N3 cần chạy thu thập receipt trên bộ peak đúng và ghép giá ETH lịch sử phù hợp.
- Receipt của giao dịch đã thực thi chỉ chứng minh chi phí thực thi. N4/N5 vẫn phải tính cơ hội thanh lý không thực thi và kiểm tra lợi nhuận; không suy ra freeze chỉ từ chi phí của giao dịch thành công.

## Kết quả tái lập

Runner: `python -B -m scripts.n5_review_n3_historical_delivery --output-dir <thư_mục_mới>`.

Bundle: `outputs/n5_preparation/n5_N3_historical_delivery_20261026/` gồm QA depeg toàn cửa sổ, 1.292 sự kiện peak đúng, summary và manifest SHA-256. Bộ này là dữ liệu đối chiếu cục bộ, chưa phải kết quả nghiên cứu đã nghiệm thu.
