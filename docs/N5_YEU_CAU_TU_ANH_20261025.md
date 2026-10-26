# N5 — đối chiếu yêu cầu mới trong ảnh ngày 25/10/2026

**Sau cập nhật N2:** [đã có đường oracle lấy mẫu và báo cáo dust](N5_TIEP_NHAN_N2_20261025.md).
Những chỗ “chưa có đường oracle” bên dưới mô tả lần kiểm tra trước khi nhận dữ liệu.
Lịch sử AnswerUpdated đầy đủ và depeg độc lập vẫn chưa có; không đồng nhất oracle lấy mẫu với feed history đầy đủ.

## Những tham số đã được cung cấp

- Monte Carlo: chuỗi 365 lợi suất ngày trước A, Student-t copula df=4, 10.000 kịch bản, seed=42.
- Phân tích Q1: nợ tối thiểu 100 USD, Looping = E-Mode category 1 + collateral LST + vay WETH + leverage >=3.
- Equity: 5 tầng theo các mốc 10k/100k/1m/10m USD; chuẩn hóa loss về 100.000 USD equity. Equity <=0 cần báo riêng, không chia cho 0.
- ES99: trung bình 100 loss lớn nhất khi có đúng 10.000 kịch bản hợp lệ; không giảm mẫu âm thầm.
- DebtRank: mỗi node chỉ truyền distress một lần.
- Chọn collateral theo max(bonus - DEX impact), khác với việc chọn giao dịch có tổng lợi nhuận USD lớn nhất.
- Holdout: dùng Chainlink Oracle và depeg, báo cáo cả peak 300 block và toàn cửa sổ, cả hit-rate theo ví và theo nợ USD t0.
- Các trường hợp freeze cần bằng chứng và báo cáo riêng; không tự bỏ ví dự báo sai khỏi mẫu số.

## Đã chuẩn bị từ dữ liệu hiện có

Chạy:

```powershell
.\.venv\Scripts\python.exe -B -X utf8 -m scripts.n5_prepare_research_protocol --output-dir outputs/n5_preparation/n5_research_protocol_NEW
```

Đầu ra hiện tại: `outputs/n5_preparation/n5_research_protocol_20261025_confirmed/`.
Quyết định đã lưu ở `config/n5_research_protocol.json` và được đưa vào manifest.
Bundle không có hậu tố `_confirmed` là lần chuẩn bị trước khi người dùng xác nhận.

- `calibration_returns.parquet`: 365 cặp lợi suất đơn ETH/USD và stETH/ETH, tính từ 366 mức giá ngày 12/10/2025–12/10/2026. Kiểm thiếu ngày, giá không dương và quote vượt cutoff.
- `mc_scenarios_candidate.parquet`: 10.000 vector shock, df=4, seed=42; rho ước lượng bằng tương quan của t-quantile từ thứ hạng hai chuỗi lợi suất (không tuyên bố MLE).
- `readiness.json`: cấu hình, cửa sổ ứng viên và dữ liệu còn thiếu.
- `primary_peak_300_block_events.parquet`: sự kiện trong cửa sổ peak chính của B1/B2, tách biệt với “giờ đầu sau snapshot”.
- `manifest.json`: SHA-256 nguồn và đầu ra.

Nguồn `n3_daily_steth_eth_reference.csv` dùng daily close reference với quote STETH/USDT không hoàn toàn đồng thời. N3 cần xác nhận nguồn/tần suất này dùng được cho calibration. Đây là dữ liệu thị trường cho hiệu chỉnh Monte Carlo, không dùng thay lịch sử oracle cho holdout.

Chưa xuất `out_simulation_results.parquet` từ bộ mới: vector shock là đầu vào, chưa phải loss mô phỏng. Chưa chạy 10.000 lượt Cascade trên danh mục thật hoặc công nhận Q1/Q3 hoàn thành.

## Cửa sổ peak ứng viên

Theo số lượng LiquidationCall nhiều nhất trong 300 block liên tiếp, trên tập sự kiện đã bàn giao và full-window theo config hiện tại:

| Snapshot | Block đầu | Block cuối, tính cả hai đầu | Số sự kiện |
|---|---:|---:|---:|
| B1 | 20459042 | 20459341 | 644 |
| B2 | 23549924 | 23550223 | 648 |

Tiêu chí cực đại số sự kiện đã được người dùng chọn làm primary. Đây vẫn là lựa chọn hồi cứu trên nguồn hiện có, chưa xác nhận đầy đủ log. 300 block không được coi chính xác là 60 phút. Không gán hit-rate của “giờ sau snapshot” trước đây cho các cửa sổ peak này.

## Hai quyết định đã được người dùng xác nhận

1. **Block B1: giữ 20458637**, khớp vị thế Aave/Compound và `config/snapshots.json`. Không dùng block ước lượng cũ 20458692 trong ảnh.
2. **Primary: 300 block có nhiều LiquidationCall nhất**; nếu hòa dùng cửa sổ bắt đầu tại sự kiện sớm hơn. **Secondary: 300 block từ lần đầu oracle ETH giảm >=5%** so với snapshot. Secondary chưa chạy vì thiếu lịch sử oracle. Không chọn tiêu chí dựa trên hit-rate nào đẹp hơn.

Hai điểm này không còn nằm trong danh sách chờ quyết định. Nguồn giá calibration, dữ liệu oracle/depeg, coverage và cấu trúc/capacity DebtRank vẫn cần xác nhận/bổ sung như dưới đây.

## Đầu vào còn thiếu cho holdout oracle

Chưa tìm thấy lịch sử `AnswerUpdated` trong dự án; `data/processed/aave/oracle_prices.parquet` là giá tại snapshot.

N2/N4 cần lịch sử giá có feed/proxy, decimals, round ID, block, timestamp, tx/log index, quote currency và provenance. Cần đọc được giá hiệu lực tại đầu cửa sổ và các lần cập nhật bên trong, xác định feed chuyển phiên bản nếu có. N3/N2/N4 bổ sung chuỗi tỷ giá depeg cùng block/thời gian và bằng chứng coverage.

Giá thấp nhất từng feed có thể xảy ra ở các thời điểm khác nhau. Phải báo rõ nếu dùng tổ hợp minima như kịch bản cực trị; không mô tả đó là bảng giá đã đồng thời tồn tại trên chuỗi. Khi tính hit-rate trước mỗi sự kiện phải tránh dùng update xảy ra sau sự kiện; extrema toàn cửa sổ chỉ là kiểm tra hồi cứu.

Đã bổ sung hàm `dual_hit_rates` tính số ví và trọng số nợ t0. Ví thiếu state/nợ không được tự gán nợ bằng 0. Freeze được đánh dấu riêng theo bằng chứng và vẫn giữ trong kết quả raw.

Ảnh gọi Liquidation Freeze là M15, trong mapping trước đây M14 là Freeze, M15 là transition. Giữ nguyên mapping hiện tại đến khi nhóm thống nhất dictionary; dùng tên chỉ tiêu đầy đủ khi bàn giao để tránh đổi nhầm mã.

## Mã và kiểm thử

- `src/models/n5_research_protocol.py`: kiểm tra chuỗi hiệu chỉnh, sinh scenario theo seed42/df4, chọn peak300 và dual hit-rate.
- `tests/test_n5_research_protocol.py`: 6 test đạt; cùng 10 test research cũ là 16 test đạt. Có cảnh báo deprecation Timedelta từ môi trường thư viện, không có test lỗi. Đã kiểm SHA-256 nguồn/đầu ra và số sự kiện trong hai cửa sổ peak của bộ `_confirmed`.
- Quy tắc ưu tiên collateral theo margin và ranh giới equity mới còn cần nối vào runner thực nghiệm; lõi Cascade cũ đang chọn theo tổng profit USD. Không coi việc ghi tham số trong readiness là đã áp dụng vào mọi mô phỏng.
