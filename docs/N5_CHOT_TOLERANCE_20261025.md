# N4/N5 — áp dụng chính sách tolerance và dust ngày 25/10/2026

Nguồn: yêu cầu người dùng gửi bằng ảnh trong hội thoại. Cấu hình chung tại `config/n5_tolerances.json`.

## Quy tắc được áp dụng

- Tổng nợ t0 toàn phạm vi so với reference reserve: sai lệch tương đối **<1%**.
- Ví có nợ reference Aave t0 **<100 USD**: loại toàn bộ vị thế của ví khỏi tập mô phỏng. Ví đúng 100 USD được giữ.
- Với nhóm giữ lại: sai số tuyệt đối số dư nợ/collateral **<=100 USD/ví**.
- HF: **trung vị sai số tuyệt đối <0,005** so với HF Aave. Kiểm riêng HF adapter N5 và HF mart N4.

Ảnh gọi thống kê HF là “sai số tuyệt đối trung vị (MAE)”; mã dùng **median absolute error** theo lời mô tả và Research Design, không dùng mean absolute error.

**100 USD dung sai số dư/ví** là mức người dùng vừa cung cấp, khác với đề xuất N2 trước đó **0,01 USD**. **100 USD ngưỡng dust** là một quy tắc riêng về lấy mẫu. Không tự thay 100 thành 0,01 hoặc biến phần trăm thành số tiền.

Tổng nợ protocol được đối chiếu trên toàn bộ mẫu t0 **trước khi lọc dust**. Không lấy nợ đã loại dust hoặc nợ sau shock/repay để so trực tiếp với tổng nợ giao thức ban đầu.

## Kết quả thực tế

Bundle: `outputs/n5_preparation/n5_tolerances_stress_20261025/`.

| Snapshot | Ví giữ để mô phỏng | Ví dust loại | Sai lệch nợ protocol USD | Median HF absolute error N5 |
|---|---:|---:|---:|---:|
| A | 19.664 | 8.829 | 0,73704147 | 1,78e-12 |
| B1 | 9.675 | 2.372 | 0,00350952 | 1,80e-12 |
| B2 | 20.972 | 7.013 | 0,02188110 | 1,03e-11 |

Cả ba snapshot đạt các gate protocol debt, wallet balances, median HF N5 và median HF N4. Không có ví giữ lại vượt sai số số dư 100 USD. Sai số nợ thực tế lớn nhất trong nhóm giữ lại là khoảng 0,003647 USD ở B2, thấp hơn nhiều ngưỡng đã chọn.

Đã chạy **19 kịch bản Stress N4 cho mỗi snapshot**, tổng **57 tổ hợp scenario–snapshot**, trên đúng tập đã lọc. Không có ví dust quay lại ở kịch bản nào. Tổng 955.909 dòng kết quả ví–scenario.

Đầu ra mỗi A/B1/B2:

- `simulation_positions.parquet`: toàn bộ token positions của ví được giữ, không cắt lẻ từng token theo 100 USD.
- `wallet_cohort.parquet`: danh sách giữ/loại và nợ reference dùng quyết định.
- `wallet_checks.parquet`, `validation.json`: sai số, kết quả gate.
- `zero_shock_filtered.parquet`: xác minh đường chạy sau lọc.
- `stress_positions_filtered.parquet`, `stress_summary_filtered.parquet`: 19 kịch bản.

Manifest chứa hash 22 artifacts và input/code. Nguồn N2, marts N4 và các bundle cũ được giữ nguyên. Kết quả Stress mới nằm trong bundle riêng, không ghi đè marts để tránh trộn phạm vi mẫu cũ/mới.

## Tích hợp mã

- `src/models/research_tolerances.py`: cấu hình chung, lọc theo khóa snapshot–protocol–wallet, kiểm gate độc lập.
- `src/models/stress_test.py`: API nhận `tolerances` + `account_reference` để áp dụng chính sách. Entry point nghiên cứu `main()` nạp policy, kiểm gate trước rồi mới chạy kịch bản/heatmap. Hàm tính tổng quát không truyền policy vẫn hỗ trợ fixture/dữ liệu tùy chọn.
- `scripts/n5_prepare_handoff.py`: bổ sung gate mới, cohort và positions đã lọc; giữ diagnostics cũ đầy đủ để truy vết.
- `scripts/n5_apply_tolerances.py`: chạy kiểm định và các kịch bản trong thư mục mới, trả lỗi nếu input gate không đạt.

Ví dụ API N4:

```python
from src.models.research_tolerances import load_tolerances
from src.models.stress_test import run_stress_test

result = run_stress_test(
    positions, {"eth_shock": -0.2},
    tolerances=load_tolerances(), account_reference=references,
)
```

Tái lập:

```powershell
.\.venv\Scripts\python.exe -B -X utf8 -m scripts.n5_apply_tolerances --run-stress --output-dir outputs/n5_preparation/n5_tolerances_stress_NEW
```

## Kiểm thử và giới hạn

**131 passed, 12 subtests passed** trong các bộ N4/N5/Cascade liên quan. Có 34 cảnh báo deprecation Timedelta từ các fixture/code calibration trước đây, không có test lỗi. Đã kiểm hash nguồn/đầu ra và số ví của từng kịch bản.

Test mới kiểm mốc 100 USD giữ/loại, không biến reference thiếu thành dust, sai số ví đúng 100 được chấp nhận nhưng >100 bị chặn, sai lệch protocol đúng 1% bị chặn và median HF sai vẫn làm gate thất bại dù số dư đạt.

**Đạt input tolerance gate không đồng nghĩa nghiệm thu toàn bộ nghiên cứu.** Chưa chạy Monte Carlo 10.000 lượt Cascade trên dữ liệu thật; liquidity, oracle/depeg coverage, freeze và cơ chế Q3 vẫn theo các điều kiện đã báo cáo. Các báo cáo holdout trên toàn bộ ví quan sát trước đây vẫn giữ phạm vi cũ và không tự đổi thành kết quả sau lọc dust.
