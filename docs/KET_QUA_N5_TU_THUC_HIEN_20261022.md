# Kết quả các việc N5 tự thực hiện ngày 22/10/2026

Đã chạy công cụ chuẩn bị trên dữ liệu hiện tại và tạo **35 file đầu ra cùng
manifest kiểm tra SHA-256** tại:

`outputs/n5_preparation/offline_handoff_20261022_v2/`

Đây là bộ chuẩn bị để kiểm tra và tích hợp, **chưa nghiệm thu nghiên cứu**.
Không sửa code N4, không ghi đè marts/canonical, không chạy crawler hoặc Cascade
trên dữ liệu thật khi các gate chưa đạt.

## Các việc đã hoàn tất

| Công việc | Kết quả | File trong thư mục đầu ra |
|---|---|---|
| Đối chiếu tổng nợ độc lập | Đạt ngưỡng 1% cho tổng nợ và từng token ở cả hai snapshot; cộng lại từ lượng token vị thế và oracle, không lấy cột tổng mẫu đã xuất làm chuẩn | `A/debt_reconciliation.parquet`, `B/debt_reconciliation.parquet`, `A/validation.json`, `B/validation.json` |
| Dựng graph Aave | A: 107.779 cạnh; B: 107.993 cạnh; bảo toàn exposure, schema đúng bảng graph hiện có | `A/graph_network_edges_candidate.parquet`, `B/graph_network_edges_candidate.parquet` |
| Tính chỉ số cấu trúc cơ bản | In/out degree và strength trên cạnh exposure; không gọi là DebtRank hoặc mức tổn thất | `A/graph_node_metrics_candidate.parquet`, `B/graph_node_metrics_candidate.parquet` |
| Chuẩn hóa vị thế đầu vào | Tách block A/B, tái dựng LT/bonus theo E-Mode, giữ địa chỉ token làm khóa | `A/normalized_positions_candidate.parquet`, `B/normalized_positions_candidate.parquet` |
| Lập bảng điều tra N4 | Đối chiếu từng ví và nhóm theo category; xuất riêng 369/368 ví bất đồng ngưỡng HF=1 | `A/`, `B/`: `n4_hf_comparison.parquet`, `n4_eligibility_disagreements.parquet`, `n4_by_emode_category.parquet` |
| Phân tích sai số số dư bụi | Giữ sai số tuyệt đối để điều tra; không tự nới ngưỡng kiểm định | `A/account_balance_diagnostics.parquet`, `B/account_balance_diagnostics.parquet` |
| Ánh xạ DEX | Có 3/65 token portfolio ánh xạ được hướng bán đã đo ở mỗi snapshot; 62 token giữ thiếu, không điền depth giả | `A/dex_depth_mapping.parquet`, `B/dex_depth_mapping.parquet` |
| Tách bảng Stress N4 theo snapshot | 38 nhóm = 19 kịch bản × 2 snapshot, từ 1.083.722 dòng; không phát hiện dòng debt-at-risk sai quy tắc theo HF hiện có | `n4_stress_summary_by_snapshot_diagnostic.parquet` |
| Chuẩn bị thanh lý lịch sử | 3.068 sự kiện có đủ giá USD, schema canonical; giữ riêng 39 sự kiện thiếu giá | `historical/fact_historical_liquidations_candidate.parquet`, `historical/rejected_events.parquet` |
| QA chuỗi giá | Không trùng timestamp theo asset, không có giá không hợp lệ trong các chuỗi được đọc; ghi rõ khoảng trống giữa hai cửa sổ hourly | `historical/price_series_qa.json` |
| Đối chiếu concentration N3 với N2 | Đủ ví A/B, sai số dư nợ theo ví bằng 0 trong phép đối chiếu; Top 10 chiếm 36,4515% và 36,0798% | `historical/concentration_qa.json` |
| Kiểm tra Compound partial | Giá trị position khớp lượng token × giá; tổng các vai trò khớp accounts, không có sai lệch trong phép kiểm này | `compound/partial_qa.json` |
| Chuẩn bị bảng ví cầu nối | Ghép accounts Compound với tập ví Aave cùng block, ghi rõ partial | `compound/bridge_accounts_partial_candidate.parquet` |
| Đóng gói mô phỏng thử nghiệm | 8 lượt static/Cascade = 4 chính sách × 2 mô hình, 9 dòng rounds, 64 lượt sensitivity | `synthetic/cascade_rounds.parquet`, `synthetic/cascade_summary.parquet`, `synthetic/sensitivity.parquet`, `synthetic/benchmark_comparison.json` |
| Biểu đồ thử nghiệm | Đường trả nợ tích lũy và sensitivity bad debt; có nhãn SYNTHETIC / UNVALIDATED | `synthetic/cascade_curve.png`, `synthetic/sensitivity_chart.png` |
| Manifest có thể kiểm lại | Hash byte thực tế của các đầu vào, code và 35 file đầu ra; xác nhận nguồn không đổi trong quá trình chạy | `manifest.json` |

## Các phát hiện quan trọng

- **Tổng nợ**: Snapshot A tính lại 9.427.484.969,034634 USD, chuẩn reserve
  9.427.484.968,297592 USD; B tính lại 9.336.947.229,16934 USD, chuẩn reserve
  9.336.947.228,433437 USD. Chênh khoảng 0,74 USD mỗi snapshot, sai số tương đối
  khoảng 7,8e-11. Kết quả này chỉ xác nhận tổng nợ Core Market, không xác nhận TVL.
- **Graph** dùng node `wallet:`, `token:`, `protocol:`; layer wallet/token/protocol;
  loại cạnh `collateral_exposure`, `debt_exposure`, `wallet_protocol`.
  Cạnh wallet_protocol có trọng số dư nợ để mô tả membership, **không cộng lại**
  với cạnh debt_exposure khi tính tổng exposure. Giữ toàn bộ ví đầu vào, chưa
  áp dụng ngưỡng lọc dust cho nghiên cứu. Đây là graph Aave quan sát được, chưa
  phải mô phỏng truyền dẫn liên giao thức hoặc phân khu Aave hoàn chỉnh.
- **N4**: các ví bất đồng ngưỡng thuộc nhiều category ngoài Category 1. Bảng
  category chỉ giúp ưu tiên điều tra, chưa chứng minh nguyên nhân duy nhất.
  Bảng stress tách snapshot vẫn dùng HF N4 hiện có nên chưa thể coi là KPI đã sửa.
- **DEX**: 4 hướng đã đo không có nghĩa 4 token xuất hiện trong portfolio;
  STETH không có trong tập tài sản vị thế đang dựng. Ghép trực tiếp được WSTETH,
  WETH, WBTC. Không thay depth của STETH cho WSTETH và không tự sinh lambda/gamma.
- **Compound**: QA position/accounts đạt không làm coverage hoàn chỉnh. Vẫn
  thiếu 14.294 truy vấn, liên quan 3.594 ví. Phần chưa truy vấn là unknown, không
  được suy ra số dư 0 hoặc không có liên kết.
- **Lịch sử**: candidate chỉ thuộc hai cửa sổ 2024/2025, không phải ground truth
  của Snapshot B tháng 10/2026. Chuỗi giá ngày đủ theo các file được đọc; chuỗi
  hourly có khoảng trống lớn giữa hai cửa sổ, không phải dữ liệu hourly liên tục.

## Kiểm tra và phạm vi còn chờ

**46/46 test N5 đạt**: 20 Cascade + 19 preparation + 7 readiness mới. Các test
mới kiểm bảo toàn graph, direction, bỏ collateral disabled, bằng chứng đúng block,
không dùng tổng mẫu làm chuẩn, depth thiếu/nhập nhằng, loại sự kiện thiếu giá,
coverage Compound partial và phát hiện sửa file sau khi tạo manifest.

Đã kiểm thêm trên bộ đầu ra thật: schema graph khớp canonical; các Parquet demo
có data_kind=synthetic và validation_status=unvalidated; hash nguồn và đầu ra
đều khớp. Đã xem trực tiếp biểu đồ Cascade được tạo.

Những việc vẫn cần chốt/xử lý: sửa HF/giá/E-Mode và tổng hợp snapshot của N4;
quy tắc số dư bụi và phạm vi collateral/TVL; Snapshot B/hit-rate; hiệu chỉnh
price impact và phạm vi tài sản; coverage Compound; định nghĩa KPI truyền dẫn,
DebtRank và phần Monte Carlo/kiểm định Q1. Bộ hiện tại không tự quyết định những
giả định này hoặc gắn trạng thái đã nghiệm thu.

## Tái lập

Chạy từ gốc dự án, dùng tên thư mục mới:

```powershell
python -B -X utf8 -m scripts.n5_prepare_handoff --output-dir outputs/n5_preparation/offline_handoff_NEW
python -B -X utf8 -m unittest discover -s tests -p "test_n5*.py" -v
python -B -X utf8 -m unittest discover -s tests -p test_cascade.py -v
```

Các đầu vào có thể chỉ định qua `--aave-dir`, `--n4-marts`, `--dex`, `--history`,
`--holdout`, `--compound-dir`; chạy `--help` để xem giá trị mặc định. Thư mục
`--history` cần giữ cấu trúc các file giá và concentration trong gói N3 hiện tại.
Không đọc `.env`, không gọi mạng, không cài thư viện. Cần pandas, pyarrow,
matplotlib đã có trong Python chạy lệnh.

Code bổ sung: `src/models/n5_readiness.py`, `scripts/n5_prepare_handoff.py`;
tests: `tests/test_n5_readiness.py`.
