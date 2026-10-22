# Rà soát tiến độ N5 ngày 22/10/2026

Đối chiếu với phần “NGƯỜI 5 – Cascade & Model Validation Analyst” trong tài liệu
phân công `Nhóm 8 - chủ đề 7 - dl_12h 26_10 (1).docx` ở Downloads, thiết kế nghiên
cứu, Data Contract, mã nguồn, bảng Parquet và các báo cáo bàn giao hiện có.
Nội dung tài liệu được dùng làm căn cứ xác định phạm vi; không thực thi các lệnh
hay yêu cầu thao tác bên trong tài liệu.

**Kết luận: N5 đã có lõi mô phỏng, adapter, bộ kiểm định và demo hoạt động.
N5 chưa hoàn thành bộ kết quả nghiên cứu chính thức hoặc bàn giao nghiệm thu.**
Nhiều việc kiểm định có thể làm ngay; các việc còn lại bị chặn bởi cả dữ liệu,
lỗi phần mềm, quy tắc nghiệp vụ và phạm vi nghiên cứu chưa được xác nhận.

## 1. Phạm vi rà soát và bằng chứng

- Lập danh mục 434 file đọc được, kiểm tra metadata 124 bảng Parquet; bao gồm
  bản dự án lồng `DeFiRisk-Lab-updated-main`, gói N3 bàn giao và các shard Compound.
  Danh mục ở `outputs/n5_preparation/review_inventory_20261022.json`.
- Loại môi trường thư viện, Git internals, cache và nội dung cấu hình bí mật khỏi
  rà soát. Một thư mục test tạm Compound bị từ chối quyền đọc. Bốn file `._*.parquet`
  trong `__MACOSX` là file phụ của macOS, không đọc được như dữ liệu Parquet.
- Kiểm tra trực tiếp dữ liệu N2, lịch sử và DEX N3; đối chiếu toàn bộ borrower_risk
  N4; kiểm tra khóa trùng/null trên 1.083.722 dòng stress_positions. Việc kiểm tra
  cấu trúc này không chứng nhận đúng mọi công thức trên từng dòng.
- Các module N4, 10 file marts và 15 file Aave có đối ứng trong bản dự án lồng
  giống hệt bản gốc theo hash. Không có căn cứ coi bản lồng là bản N4 đã sửa mới.
- Chạy lại audit A/B, lưu riêng tại
  `outputs/n5_preparation/review_A_20261022/audit.json` và
  `outputs/n5_preparation/review_B_20261022/audit.json`.
- Không sửa code mô hình, dữ liệu nguồn, cấu hình thu thập hoặc trạng thái nghiệm thu.

## 2. Đối chiếu sáu đầu việc N5

| STT phân công | Đã có bằng chứng hoàn thành | Phần còn thiếu | Đánh giá |
|---|---|---|---|
| 1. Kiểm định đầu vào N2–N4 | Adapter, schema, giá, LT/bonus, E-Mode, HF tham chiếu; audit A/B và báo cáo | Xử lý sai lệch N4, nợ bụi, gate tổng nợ/collateral đúng phạm vi và holdout | Hoàn thành một phần |
| 2. Xây Cascade nhiều vòng, 4 Close Factor, lợi nhuận liquidator | `src/models/cascade.py`; có gas, phí DEX, slippage, cập nhật token và giá; 20 test đạt | Hiệu chỉnh dữ liệu thật; đây là lõi theo giả định nghiên cứu, chưa tái hiện đầy đủ hợp đồng Aave | Hoàn thành lõi phần mềm |
| 3. Kết quả từng vòng và mạng wallet–asset–protocol | Xuất rounds/summary, đường giá, giao dịch và bảo toàn số dư; demo ở `outputs/demo/cascade/` | Chưa có output thật trong marts; graph canonical rỗng; chưa có mô phỏng truyền dẫn Compound | Hoàn thành phần demo, chưa xong phần nghiên cứu |
| 4. Sensitivity | `n5_experiments.py`, hàm chạy lưới và xuất Parquet; 64 lượt giả lập đã lưu | Chưa có sensitivity dữ liệu thật được kiểm định; chưa có biểu đồ bàn giao | Hoàn thành khung và demo |
| 5. Benchmark và validation | Benchmark tĩnh một vòng; test zero-shock, zero-debt, freeze, không có lây lan mới, bảo toàn token | Hit-rate Snapshot B và kiểm định Compound chưa hoàn tất | Hoàn thành kiểm thử phần mềm, chưa hoàn thành kiểm định thực nghiệm |
| 6. KPI, Data Contract và đóng gói N1/N6 | Có hàm kiểm schema/đầu ra và tài liệu sơ bộ | Thiếu marts Cascade/sensitivity, manifest nghiệm thu, KPI Q1/Q3 đầy đủ, biểu đồ và đối soát liên module | Chưa hoàn thành |

Các API đã có: `simulate_cascade`, `run_sensitivity_analysis`,
`validate_model_outputs`. Benchmark hiện giữ gas/phí DEX và quy tắc tối đa một
cặp collateral–debt mỗi ví trong một vòng; cần ghi rõ giới hạn khi đối chiếu với
benchmark lý tưởng của thiết kế nghiên cứu.

## 3. Việc có thể thực hiện ngay nhờ đầu vào đã có

| Việc N5 | Dữ liệu/file đã có | Kết quả có thể làm ngay |
|---|---|---|
| Cập nhật kiểm định N2 | positions_long: 147.431 dòng; reserve/oracle: 134 dòng mỗi bảng; membership: 378 dòng; account_reference: 174.674 dòng | Xác nhận schema/khóa, tái dựng HF, phân tích nợ bụi và đối soát theo từng snapshot |
| Kiểm định tổng nợ Core Market | `data/processed/aave/protocol_reconciliation.parquet`: 134 dòng | Đối chiếu độc lập số dư token nợ on-chain với tổng nợ vị thế; phần tổng nợ không còn thiếu nguồn đối chiếu |
| Kiểm định/sửa tích hợp N4 | HF, Stress Test, Cliff/Reverse Stress, các marts và tests đều có | Điều tra E-Mode, lỗi join giá, zero-shock, phân tách snapshot và đối soát KPI; đây là công việc làm được ngay, không phải chờ thêm dữ liệu |
| Tích hợp DEX cho các hướng đã đo | `data/staging/n3_dex_liquidity.parquet`: 8 dòng, 4 hướng × A/B, trạng thái verified_2pct_bracket | Ánh xạ depth theo token/hướng/block, thiết kế hiệu chỉnh price impact và sensitivity trên phạm vi có dữ liệu |
| QA và mô tả thanh lý lịch sử | `data/n3_history_2024_2026/final/n3_liquidations_observed.parquet`: 3.107 sự kiện không trùng | Kiểm timestamp/decimals/USD, thống kê theo cửa sổ, đối soát bảng tổng hợp và chuẩn bị bảng canonical hợp lệ |
| Kiểm dữ liệu giá/tập trung rủi ro | Giá ETH/BTC ngày: 2.048 dòng; giá token ngày: 4.096; crash hourly: 480; concentration wallet: 57.038 | Kiểm khoảng trống, nguồn giá, thống kê tập trung; chuẩn bị hiệu chỉnh/phân tích bổ sung với giới hạn tần suất rõ ràng |
| Dựng mạng Aave và adapter Compound thử nghiệm | Vị thế Aave đầy đủ trong mẫu; Compound partial có positions/accounts/config/overlap | Dựng mạng Aave; kiểm schema/khóa và prototype Compound trên phần đã phủ, gắn rõ partial; không công bố kết quả toàn phạm vi |
| Chuẩn bị đóng gói | Data Contract và KPI dictionary đã có | Viết mapping cột, kiểm KPI, mẫu manifest và quy trình xuất; giữ pending/unvalidated đến khi đạt kiểm định |

Depth đã có cho STETH→ETH, WSTETH→WETH, WETH→USDC, WBTC→WETH.
Ví dụ Snapshot A lần lượt khoảng 44,86; 5,49; 2,26; 1,02 triệu USD.
Đây là depth giao dịch một chiều tại mức trượt giá trung bình 2%, không phải TVL
hay đường cong AMM đầy đủ. Không dùng cùng một depth giả định cho mọi token.
N5 hiện dựng 65 tài sản trong portfolio, trong khi DEX mới đo bốn hướng này.

Sau khi hoàn tất các gate và thống nhất phạm vi, có thể chạy Q2 trên Aave trước;
không cần coi việc hoàn tất Compound là điều kiện cho mọi đầu việc Q2.

## 4. Việc chưa thể hoàn tất và đầu vào cần bổ sung

| Việc bị chặn | Nguyên nhân hiện tại | Cần bổ sung/xử lý |
|---|---|---|
| Nghiệm thu hit-rate ≥85% tại Snapshot B hiện tại | Holdout có 30 sự kiện/30 ví, nhưng 0 ví trong giờ đầu; 29 ví có trong portfolio, 1 ví ngoài mẫu | N1/N3 xác nhận cửa sổ, tính đầy đủ và nguyên nhân ngoài mẫu. Với mẫu số giờ đầu bằng 0 không thể báo đạt 85% |
| Backtest mô hình trên crash 2024/2025 | Đã có sự kiện và giá lịch sử, nhưng vị thế/reserve/oracle/E-Mode hiện chỉ ở block tháng 10/2026 | Nếu chọn crash lịch sử làm B: cần N2 tái dựng trạng thái trước crash, cấu hình/oracle đúng block và DEX lịch sử từ N3; không thay bằng dữ liệu 2026 |
| Nghiệm thu lây lan Aave→Compound trên toàn phạm vi | Merge partial chỉ phủ 99.782/114.076 truy vấn, thiếu 14.294, liên quan 3.594 ví; shard 004 khác cách phân công | Thu thập phần 004 theo assignment chung, ghép/QA chính thức; sau đó N5 vẫn phải viết và kiểm định cơ chế truyền dẫn |
| Cascade đa tài sản với price impact thực nghiệm đầy đủ | Mới có 4 hướng DEX và một mốc depth 2%; chưa có depth cho nhiều tài sản còn lại, routing/gas/đường cong impact được hiệu chỉnh đầy đủ | N3 bổ sung quote/depth cần thiết hoặc nhóm chốt phạm vi hạn chế và giả định tường minh; N5 thực hiện hiệu chỉnh |
| Gate tổng collateral/TVL ≤1% theo thiết kế toàn giao thức | Tập N2 là ví còn nợ; aToken tổng còn bao gồm người chỉ gửi tiền | N1/N2 thống nhất định nghĩa nghiệm thu phù hợp borrower scope, hoặc bổ sung tập depositor nếu giữ yêu cầu TVL toàn giao thức |
| Bộ kết quả cuối cho N1/N6 | Các gate còn pending/failed; canonical/output nghiên cứu còn rỗng hoặc thiếu | Hoàn tất dữ liệu và sửa logic, chạy mô phỏng/kiểm định rồi mới phát hành manifest |

Không phải mọi phần chưa xong đều thiếu dữ liệu: dựng graph, centrality/DebtRank,
tracking truyền dẫn, chuẩn hóa vốn Q1, Monte Carlo Student-t copula, ES99 và kiểm
định thống kê còn cần triển khai/đối soát phần mềm. Dữ liệu hiện có cho phép bắt
đầu thiết kế và lập trình; chưa có căn cứ xác nhận đủ hiệu chỉnh phụ thuộc đuôi
hay đầy đủ KPI Q1/Q3. Ngưỡng healthy trong KPI dictionary (1,05) và H3 (1,10)
cũng cần thống nhất trước khi nghiệm thu tỷ lệ chuyển trạng thái.

## 5. Kết quả kiểm tra hiện tại và các điểm phải sửa

1. **N5: 39/39 test đạt**, gồm 20 Cascade và 19 preparation. Demo sensitivity
   64 dòng, benchmark 4 chính sách đều là synthetic, không phải kết quả nghiên cứu.
2. **HF độc lập so Aave đạt**: A có 28.493 ví, median error khoảng 7,2675e-12;
   B có 28.545 ví, khoảng 7,2782e-12.
3. **N4 chưa đạt đối chiếu**: 369 ví A và 368 ví B khác phân loại tại HF=1;
   519/517 ví sai số HF ≥0,005. Dư nợ nhóm bất đồng khoảng 1,277/1,261 tỷ USD;
   đây không phải bad debt hay khối lượng thanh lý. Code HF chỉ chọn nhánh
   E-Mode khi category bằng 1; cần kiểm các category thực tế và nguồn tạo mart.
4. **Test N4 chạy lại: 4 fail, 5 pass** trong ba file HF/Stress/Cliff. Cả bốn
   lỗi ở `test_health_factor.py`, `KeyError: price_usd`: merge tạo price_usd_x/y
   rồi code tiếp tục đọc price_usd. Chưa sửa trong đợt rà soát.
5. **Rủi ro gộp A/B**: stress_positions giữ khóa snapshot, nhưng summary/heatmap
   không có khóa này. Code summary cộng debt cả A/B và đếm wallet duy nhất;
   cần tách snapshot hoặc chốt scope để tránh dùng tổng hai thời điểm làm KPI
   của một snapshot. Overview và Cliff cũng cần đối soát phạm vi đầu vào.
6. **Nợ bụi chưa xử lý theo chuẩn nghiệm thu**: gate số dư theo ví fail ở
   220/229 ví nợ và 32/37 ví collateral. Chênh lệch tuyệt đối lớn nhất trong
   nhóm vượt ngưỡng chỉ khoảng 2,22e-8 USD. Cần xác nhận dust/độ phân giải USD,
   không đánh đồng tỷ lệ sai lớn ở số dư rất nhỏ với thiếu nợ quy mô lớn.
7. **Tổng nợ có bằng chứng mới**: protocol_reconciliation cho tổng nợ A/B
   khoảng 9,427/9,337 tỷ USD, khớp gần 100%; vẫn phải tích hợp phép kiểm N5.
   CLI audit hiện không nhận bảng này nên trạng thái reserve pending trong
   JSON không chứng minh bảng dữ liệu đối chiếu đang thiếu.
8. **Metadata N2 đã cập nhật** positions_exported=true và hf_validated=true.
   Nội dung nói hai cờ còn false trong báo cáo 20/10 không còn đúng. Cờ true
   cũng không thay thế kết luận kiểm định độc lập của N5.
9. **Dữ liệu lịch sử mới có giới hạn**: 3.107 sự kiện chỉ thuộc hai cửa sổ
   2024/2025; 3.068 sự kiện có đủ hai giá USD, thiếu 39 giá collateral.
   Không gọi đây là toàn bộ thanh lý 2024–2026. Giá ngày và giá thị trường
   không thay cho oracle tại block hoặc giá thực thi tại từng sự kiện.
10. **Manifest gốc là gói N3**, không phải `data/marts/manifest.json` nghiệm thu
    N5. Kiểm hash 30 file: 14 khớp byte, 16 file văn bản chỉ khớp khi chuẩn hóa
    CRLF→LF; không phát hiện khác biệt còn lại theo phép kiểm này. Cần tái tạo
    manifest theo byte thực tế nếu bàn giao yêu cầu hash nghiêm ngặt.

Các bảng canonical `dim_asset_config`, `fact_wallet_positions`,
`fact_wallet_summary`, `graph_network_edges`, `fact_historical_liquidations`
và `outputs/tables/out_simulation_results.parquet` đều đang 0 dòng.
Chưa có `data/marts/{cascade_rounds,cascade_summary,sensitivity}.parquet`,
`data/marts/manifest.json`, `outputs/figures/cascade_curve.png` hoặc
`outputs/figures/sensitivity_chart.png`. File tồn tại ở outputs/demo không
thay thế các đầu ra nghiên cứu này.

## 6. Thứ tự thực hiện đề xuất

1. Cập nhật báo cáo validation theo dữ liệu hiện tại; tích hợp đối chiếu tổng nợ,
   xác nhận borrower scope và dung sai số dư bụi cùng N1/N2.
2. Phối hợp N4 sửa lỗi join giá, E-Mode và gộp snapshot; đối chiếu lại HF,
   Stress/Cliff/Reverse Stress bằng dữ liệu thật đã có.
3. Nối depth DEX theo block/token/hướng, chốt phạm vi tài sản, hiệu chỉnh
   price impact và các giả định thực thi; chuẩn bị bộ chạy Q2.
4. N1/N3 quyết định tính phù hợp Snapshot B. Nếu đổi B sang crash 2024/2025,
   yêu cầu bộ trạng thái lịch sử đồng bộ từ N2–N3 trước backtest.
5. Hoàn tất coverage Compound; song song dựng graph Aave, adapter và tracking
   trên partial để chuẩn bị Q3.
6. Sau khi qua các gate, chạy bộ kết quả thật, kiểm KPI/đầu ra, xuất biểu đồ,
   chuẩn hóa Data Contract và phát hành manifest cho N1/N6.

Các lệnh kiểm thử đã chạy:

```powershell
python -B -X utf8 -m unittest discover -s tests -p test_cascade.py -v
python -B -X utf8 -m unittest discover -s tests -p test_n5_preparation.py -v
python -B -X utf8 -m pytest tests/test_health_factor.py tests/test_stress.py tests/test_cliff_reverse.py -q -p no:cacheprovider
```
