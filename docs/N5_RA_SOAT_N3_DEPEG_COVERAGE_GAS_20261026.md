# N5 — rà soát N3 depeg, coverage log và gas/freeze ngày 26/10/2026

Đã đọc commit `6bba2a7` và kiểm tra mã/dữ liệu thực tế. Tên commit ghi hoàn tất các phần N3; kết quả rà soát dưới đây phân biệt chức năng đã có với đầu vào đủ điều kiện dùng cho holdout B1/B2.

## Bộ kết quả rà soát

`outputs/n5_preparation/n5_N3_completion_review_20261026/`

Giữ nguyên các Parquet/JSON N3 đã bàn giao. Đầu ra kiểm tra/sửa nhãn được ghi trong bundle mới, không sửa dữ liệu gốc hoặc tự loại ví khỏi hit-rate. Không gọi RPC.

| Đầu việc N3 | Đã bổ sung | Điều kiện còn thiếu để N5 dùng chính thức |
|---|---|---|
| Depeg stETH/ETH | Hàm tính `(ratio-1)*100`, 96 dòng metric; N5 tái tính và khớp | Dữ liệu chỉ từ 12–15/10/2026. B1 năm 2024 và B2 năm 2025 đều có **0 dòng** trong holdout |
| Coverage log | Đo tỷ lệ fact-ready/priced trong dữ liệu đầu vào | Không phải kiểm chứng đã lấy hết LiquidationCall trên chuỗi; mã mới còn bị cắt cụt và lỗi cú pháp, đã sửa |
| Gas/profit/freeze | Bảng 42 sự kiện và ước tính profit trừ gas | Chi phí cố định 60 USD là giả định; 42 sự kiện đã thực thi, 24 dòng net estimate âm không chứng minh các vị thế freeze. Không có event key nào trùng bộ holdout B1/B2 |

**Chưa thể đánh dấu cả ba vướng mắc là hoàn tất cho nghiệm thu B1/B2.** Tuy nhiên có thể dùng công thức depeg và bảng giả định gas như nền tảng để bổ sung dữ liệu đúng kỳ.

## 1. Depeg

`data/staging/n3_price_timeseries.parquet` có 1.118 dòng gồm 576 market reference, 288 market ratio, 155 oracle snapshot, 3 Lido conversion và 96 depeg metrics.

N5 đã đối chiếu cả 96 metric với market ratio gốc. Đơn vị là **điểm phần trăm so với parity=1**, có thể âm hoặc bằng 0; không phải giá USD hay lợi suất ngày.

- Thời gian: 2026-10-12 00:00 đến 2026-10-15 23:00 UTC.
- B1: cần sau 2024-08-04 23:59:59, trước 2024-08-06 00:00:00 UTC.
- B2: cần sau 2025-10-09 23:59:59, trước 2025-10-12 00:00:00 UTC.

Các ảnh/chính sách trước đây đã yêu cầu đường depeg ở các holdout. Không dùng số liệu 2026 thay cho 2024/2025. N3 cần bổ sung ratio/giá nguồn theo đúng thời gian, kèm timestamp, nguồn và quy tắc ghép as-of; nếu tỷ giá oracle thì phối hợp N2/N4.

## 2. Coverage

File `src/data/finalize_liquidations.py` tại commit mới chỉ còn 22 dòng là một đoạn thân hàm thụt lề ở top level. Kiểm tra AST trả `IndentationError` tại dòng 2; không có CLI/hàm finalize hoàn chỉnh.

Đã phục hồi implementation đầy đủ từ phiên bản cha và bổ sung báo cáo định giá theo ý định cập nhật:

- `valuation_coverage_pct`: tỷ lệ fact-ready trên **sự kiện đã cung cấp**.
- `valuation_coverage_status`: PASS/WARNING/NO_EVENTS, chỉ cho coverage định giá.
- `event_collection_coverage`: vẫn `NOT_VERIFIED_INDEPENDENTLY` nếu chưa có bằng chứng độc lập.

Đã chạy lại hàm finalize trên file priced hiện có: 42/42 sự kiện định giá được. Không suy từ tỷ lệ này rằng toàn bộ log trên chuỗi đã đủ, và không đồng nhất ngưỡng 85% coverage định giá với 85% hit-rate HF.

N3 cần bàn giao bằng chứng quét log theo block range của B1/B2: range đầu/cuối, các trang/chunk và retry, phân đoạn thành công/thất bại, PK duy nhất, đối chiếu số sự kiện với nguồn log độc lập hoặc truy vấn đã chứng minh hoàn tất. Một tỷ lệ trên danh sách đã tải không phát hiện được sự kiện chưa tải về.

## 3. Gas và freeze

`n3_gas_profit_freeze_proof.parquet` có **42 sự kiện, 38 transaction**, từ 12–15/10/2026. Bảng mới dùng:

```text
400.000 gas × 50 gwei × 3.000 USD/ETH = 60 USD
gross proxy = collateral_seized_usd - debt_repaid_usd
estimated net = gross proxy - 60 USD
```

Kết quả 24 dòng estimated net <=0 là tái tính được theo giả định, nhưng:

1. Các dòng đã có LiquidationCall tức giao dịch đã xảy ra; không thể dùng chính các dòng đó chứng minh đã không thanh lý vì gas.
2. Gas units, gas price và ETH/USD là giá giả định, không phải transaction receipt và giá đúng thời điểm.
3. Giá trị collateral thị trường không phải DEX execution proceeds; thiếu routing, phí, slippage và chi phí thực thi khác.
4. Có nhiều LiquidationCall trong cùng transaction; chi phí toàn transaction cần phân bổ hoặc tổng hợp đúng cấp, không cộng lặp theo sự kiện.
5. Đây không phải dữ liệu kỳ B1/B2: đối chiếu event PK với tập holdout cho kết quả giao nhau bằng 0.

Đã sửa code để ghi `is_unprofitable_under_assumptions`, `analysis_status=assumption_diagnostic_not_freeze_proof`. Cột cũ `is_frozen_due_to_gas` để nullable/unknown; không biến estimate âm thành bằng chứng freeze. Bảng nguồn gốc vẫn giữ nguyên, bản sửa nhãn nằm trong `gas_assumption_review.parquet`.

N3 có thể hoàn thành phần dữ liệu bằng receipts gasUsed/effectiveGasPrice theo tx, giá ETH thời điểm tương ứng và event linkage cho đúng B1/B2. N4/N5 phụ trách ước tính execution profit, kiểm vị thế đủ điều kiện nhưng chưa thanh lý và phân loại freeze theo bằng chứng. Chưa có bằng chứng thì giữ unknown, không bỏ model miss khỏi mẫu số.

## 4. Các sửa chữa để code dùng được

- Finalizer đầy đủ và CLI chạy lại được, giữ các kiểm tra PK, raw token/USD, timestamp, holdout và chống ghi đè của phiên bản hoạt động trước đó.
- Depeg chỉ suy từ `market_ratio`, kiểm giá ratio dương và không dùng timestamp tương lai. QA chấp nhận metric âm/0 nhưng vẫn chặn giá thị trường/oracle âm/0.
- Bổ sung lại guard oracle block/count/timestamp/registry, ghi quality-output như CLI đã khai báo và backup khi người dùng chọn overwrite.
- Gas code từ chối tiền/giá thiếu hoặc NaN, không điền bonus=0 để tự sinh freeze; mặc định từ chối ghi đè các artifact N3 hiện có.

## 5. Kết quả bàn giao và tái lập

Bundle mới có:

- `depeg_metrics_verified.parquet`: 96 metric khớp công thức, phạm vi 2026.
- `prices_rebuilt_candidate.parquet`, `price_integration_qa.json`: chạy lại integration sau sửa, không thay source.
- `gas_assumption_review.parquet`: 24 dòng unprofitable theo giả định, freeze unknown.
- `valuation_fact_candidate.parquet`: 42 sự kiện fact-ready, không chứng nhận chain-log completeness.
- `summary.json`, `manifest.json`: kết luận, scope, hạn chế và hash input/code/output.

```powershell
.\.venv\Scripts\python.exe -B -X utf8 -m scripts.n5_review_n3_completion --output-dir outputs/n5_preparation/n5_N3_completion_NEW
.\.venv\Scripts\python.exe -B -X utf8 -m pytest tests/test_n3_completion_review.py -q -p no:cacheprovider
```

6 regression tests mới đạt: metric âm/0 đúng đơn vị, không dùng giá tương lai/oracle như market ratio, không chứng nhận freeze cho event đã thực thi, không dùng tiền/giá thiếu, coverage định giá không thành coverage toàn log, và giữ sự kiện không giá trong mẫu số.

Kiểm tra rộng hơn: **29 test liên quan đạt**, có 45 cảnh báo deprecation Timedelta, không có test lỗi. Đã đổi fixture thư mục tạm của hai test N3 cũ sang thư mục UUID trong workspace để tránh ACL 0700 trên Windows; không thay đổi assertion. CLI finalizer chạy được. Hash 6 artifacts và mọi input liệt kê trong manifest đều khớp.

Hai việc đã chốt trước vẫn giữ nguyên: không chờ quyết định dust/dung sai nữa; phần oracle token/feed và state B2 thuộc N2/N4 vẫn chưa được commit N3 này giải quyết. Không chạy lại hit-rate rồi công bố cải thiện khi dữ liệu mới không phủ thời gian holdout.
