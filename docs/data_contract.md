# Data Contract: Rủi ro người vay, kiểm thử sức chịu đựng và kiểm thử ngược

Tài liệu này quy định các bảng dữ liệu rủi ro được lưu trữ (risk marts) và giao diện hàm Python dành cho Dashboard và pipeline. Các giá trị USD là giá trị danh nghĩa theo giá tại snapshot hoặc theo kịch bản phản thực. Địa chỉ ví và token được loại bỏ khoảng trắng thừa và chuyển thành chữ thường.

## Khóa chung và quy ước

| Trường | Kiểu dữ liệu | Ý nghĩa |
|---|---|---|
| `snapshot_block` | `int64` | Block Ethereum xác định thời điểm snapshot của vị thế. |
| `protocol_id` | `string` | Định danh giao thức/thị trường; Aave V3 Ethereum Core dùng `aave_v3_ethereum`. |
| `wallet_address` | `string` | Địa chỉ ví, viết thường. |
| `token_address` | `string` | Địa chỉ hợp đồng reserve, viết thường. |

Dữ liệu đầu vào vị thế gồm các dòng cấp token, có khóa `(snapshot_block,
protocol_id, wallet_address, token_address)`. Snapshot A và B là hai quan sát
độc lập, không gộp chúng khi phân tích ở cấp ví. Cú sốc giá được biểu diễn bằng
tỷ suất dạng thập phân: `-0.20` nghĩa là giá giảm 20%. Health Factor (`HF`) bằng
giá trị tài sản thế chấp đủ điều kiện có trọng số chia cho tổng dư nợ. Ví không
có dư nợ được gán `HF = 999.0`. Vị thế được xem là có nguy cơ thanh lý khi và
chỉ khi `HF < 1.0`.

---

## 1. Các Data Mart tĩnh

### `data/marts/borrower_risk.parquet`

Mỗi dòng ứng với một bộ khóa `(snapshot_block, protocol_id, wallet_address)`.

| Cột | Kiểu dữ liệu | Mô tả |
|---|---|---|
| `snapshot_block` | `int64` | Block snapshot. |
| `protocol_id` | `string` | Định danh giao thức/thị trường. |
| `wallet_address` | `string` | Địa chỉ người vay, viết thường. |
| `total_collateral_usd` | `float64` | Tổng giá trị tài sản thế chấp đủ điều kiện theo giá snapshot. |
| `total_debt_usd` | `float64` | Tổng giá trị dư nợ theo giá snapshot. |
| `health_factor` | `float64` | Giá trị thanh lý của tài sản thế chấp có trọng số chia cho dư nợ; bằng `999.0` khi dư nợ bằng 0. |
| `risk_category` | `string` | Nhóm rủi ro: `Safe` (`HF >= 1.10`), `Warning` (`1.00 <= HF < 1.10`) hoặc `High Risk` (`HF < 1.00`). |
| `is_recursive_loop` | `bool` | Cờ đòn bẩy lặp theo quy tắc E-Mode/LST/WETH và đòn bẩy hiệu dụng của mô hình. |
| `emode_category` | `int64` | Danh mục E-Mode của ví; bằng 0 nếu ví không dùng E-Mode. |

Hàm tính toán còn tạo `weighted_collateral_lt_usd`, nhưng trình ghi mart hiện
không đưa cột này vào file Parquet.

### `data/marts/stress_summary.parquet`

Mỗi dòng ứng với một bộ `(scenario_id, snapshot_block)`. Chỉ số của Snapshot A
và Snapshot B được tính riêng, không cộng dồn vào cùng một KPI.

| Cột | Kiểu dữ liệu | Mô tả |
|---|---|---|
| `scenario_id` | `string` | Định danh kịch bản từ `config/scenarios.yaml`. |
| `snapshot_block` | `int64` | Block snapshot mà các chỉ số rủi ro trong dòng này đại diện. |
| `eth_shock` | `float64` | Cú sốc biến động giá ETH/WETH, dạng thập phân. |
| `btc_shock` | `float64` | Cú sốc biến động giá BTC/WBTC, dạng thập phân. |
| `depeg_shock` | `float64` | Mức mất neo bổ sung của LST so với ETH, dạng thập phân. |
| `positions_at_risk` | `int64` | Số địa chỉ ví duy nhất có `HF < 1.0` sau cú sốc. |
| `debt_at_risk_usd` | `float64` | Tổng dư nợ USD của các ví có `HF < 1.0`. |
| `debt_at_risk_pct` | `float64` | Dư nợ rủi ro chia tổng dư nợ sau cú sốc, biểu diễn từ 0 đến 100. |
| `looping_debt_at_risk_usd` | `float64` | Dư nợ rủi ro thuộc các ví được gắn cờ đòn bẩy lặp. |

Các cú sốc stablecoin được khai báo trong cấu hình và nhận diện qua
`scenario_id`; schema hiện tại chưa có cột riêng cho mức cú sốc stablecoin.

### `data/marts/stress_positions.parquet`

Mỗi dòng ứng với bộ khóa `(scenario_id, snapshot_block, protocol_id, wallet)`.

| Cột | Kiểu dữ liệu | Mô tả |
|---|---|---|
| `scenario_id` | `string` | Định danh kịch bản được áp dụng. |
| `snapshot_block` | `int64` | Block snapshot nguồn, dùng để phân biệt Snapshot A và B. |
| `protocol_id` | `string` | Giao thức/thị trường nguồn. |
| `wallet` | `string` | Địa chỉ ví, viết thường. |
| `hf_before` | `float64` | HF cơ sở lấy từ mart rủi ro nếu có; nếu không thì tính lại từ vị thế. |
| `hf_after` | `float64` | HF sau khi áp dụng cú sốc giá. |
| `debt_usd` | `float64` | Tổng dư nợ của ví sau khi định giá lại theo kịch bản. |
| `debt_at_risk_usd` | `float64` | Bằng `debt_usd` nếu `HF < 1.0`; ngược lại bằng 0. |
| `is_recursive_loop` | `bool` | Phân loại đòn bẩy lặp tại thời điểm cơ sở. |

### `data/marts/stress_heatmap.parquet`

Mỗi dòng ứng với một bộ `(snapshot_block, eth_shock, depeg_shock)`.

| Cột | Kiểu dữ liệu | Mô tả |
|---|---|---|
| `eth_shock` | `float64` | Cú sốc giá ETH, dạng thập phân. |
| `snapshot_block` | `int64` | Block snapshot được tính riêng trong ma trận. |
| `depeg_shock` | `float64` | Cú sốc mất neo LST/ETH, dạng thập phân. |
| `debt_at_risk_usd` | `float64` | Tổng dư nợ USD của các vị thế có `HF < 1.0`. |
| `positions_at_risk_count` | `int64` | Số địa chỉ ví duy nhất rơi vào vùng rủi ro. |

### `data/marts/overview_metrics.json`

Các chỉ số được lồng dưới khóa `snapshots`, với một đối tượng cho mỗi
`snapshot_block` (khóa JSON là chuỗi biểu diễn số block). Không có tổng hợp
chung giữa các snapshot. Mỗi đối tượng chứa `snapshot_block`, `total_wallets`,
`total_collateral_usd`, `total_debt_usd`, `total_positions_at_risk_count`,
`total_debt_at_risk_usd` và `looping_wallets_count`.

### `data/marts/cliff_curve.parquet`

Mỗi dòng ứng với một bước giảm giá ETH. Mặc định quét từ 0% đến 60%, mỗi bước
1 điểm phần trăm.

| Cột | Kiểu dữ liệu | Mô tả |
|---|---|---|
| `eth_shock` | `float64` | Biến động giá ETH dạng thập phân âm, từ 0.00 đến -0.60. |
| `eth_drop_pct` | `float64` | Mức giảm giá ETH tính theo phần trăm dương, từ 0 đến 60. |
| `debt_at_risk_usd` | `float64` | Tổng dư nợ USD của các ví có `HF < 1.0`. |
| `positions_at_risk` | `int64` | Số ví duy nhất có nguy cơ thanh lý. |
| `total_protocol_debt_usd` | `float64` | Tổng dư nợ toàn giao thức sau khi định giá lại. |
| `marginal_debt_at_risk_usd_per_pct` | `float64` | Mức tăng dư nợ rủi ro biên trên mỗi 1 điểm phần trăm giảm giá ETH; dòng đầu tiên là null. |
| `marginal_multiple_vs_median` | `float64` | Mức tăng biên chia cho trung vị của các mức tăng biên dương trong lần quét. |
| `is_cliff` | `bool` | Bằng True khi bội số mức tăng biên đạt ít nhất 3.0. |
| `debt_at_risk_pct` | `float64` | Dư nợ rủi ro chia tổng dư nợ toàn giao thức, biểu diễn từ 0 đến 100. |
| `baseline_mart_debt_at_risk_usd` | `float64` | Dư nợ rủi ro từ mart cơ sở, được thêm trong quy trình dòng lệnh khi có dữ liệu. |
| `baseline_mart_debt_delta_usd` | `float64` | Dư nợ rủi ro ở mức cú sốc 0 trừ giá trị từ mart cơ sở; chỉ có giá trị ở dòng cú sốc 0. |

Hai cột đối chiếu cuối được quy trình `__main__` bổ sung khi mart cơ sở có
`total_debt_usd`. Gọi trực tiếp `find_liquidation_cliff` chỉ trả các cột đường
cong cốt lõi, không có hai cột đối chiếu này.

---

## 2. Đặc tả API cho Dashboard tính toán động

Các hàm nhận DataFrame pandas và trả kết quả trong bộ nhớ, không tự ghi file.
Các DataFrame đầu vào không bị chỉnh sửa.

### `calculate_health_factor(positions_df, reserve_config, prices_df=None) -> pd.DataFrame`

Dashboard có thể gọi hàm với hai DataFrame nếu giá đã có trong `positions_df`
(`price_usd` hoặc `price_usd_t0`) hoặc trong `reserve_config` (`price_usd`). Nếu
không, cần truyền riêng `prices_df`. Trong code, tên tham số cấu hình reserve là
`reserve_config`.

| Tham số | Kiểu dữ liệu | Mặc định | Mô tả |
|---|---|---:|---|
| `positions_df` | `pd.DataFrame` | Bắt buộc | Dữ liệu vị thế cấp token. Bắt buộc có `snapshot_block`, `protocol_id`, `wallet_address`, `token_address`, `collateral_amount`, `debt_amount` và cờ `is_collateral_enabled` hoặc alias được hỗ trợ. Có thể có `emode_category_user`, `symbol` và giá. |
| `reserve_config` | `pd.DataFrame` | Bắt buộc | Cấu hình reserve theo `snapshot_block`, `protocol_id`, `token_address`; có `lt_std` hoặc `lt_bps`, tùy chọn LT/danh mục E-Mode và symbol. |
| `prices_df` | `pd.DataFrame \| None` | `None` | Giá Oracle theo snapshot/protocol/token, gồm cột `price_usd`. Bắt buộc nếu giá không có trong hai DataFrame còn lại. |

Đầu ra có cấp độ `(snapshot_block, protocol_id, wallet_address)` và các cột:
`total_collateral_usd: float64`, `total_debt_usd: float64`,
`weighted_collateral_lt_usd: float64`, `health_factor: float64`,
`risk_category: string`, `is_recursive_loop: bool`, `emode_category: int64`.

### `run_stress_test(positions_df, scenario_params) -> pd.DataFrame`

| Tham số | Kiểu dữ liệu | Mặc định | Mô tả |
|---|---|---:|---|
| `positions_df` | `pd.DataFrame` | Bắt buộc | Dữ liệu vị thế cấp token gồm các khóa, symbol, lượng collateral/nợ, giá cơ sở hoặc giá trị USD cơ sở, effective LT và cờ collateral. Alias staging được hỗ trợ gồm `token_symbol`, `collateral_enabled`, `price_usd_t0`, `collateral_amount_norm`, `debt_amount_norm`. |
| `scenario_params` | `dict` | Bắt buộc | Có thể gồm `scenario_id`; các cú sốc thập phân `eth_shock`, `btc_shock`, `depeg_shock` (hoặc `lst_depeg_shock`), `stablecoin_shock`/`stablecoin_shocks` và `asset_shocks` theo symbol. Cú sốc không khai báo mặc định bằng 0. |

Đầu ra có một dòng cho mỗi ví-snapshot, gồm: `scenario_id: string`,
`snapshot_block: int64`, `protocol_id: string`, `wallet: string`,
`hf_before: float64`, `hf_after: float64`, `debt_usd: float64`,
`debt_at_risk_usd: float64`, `is_recursive_loop: bool`.

### `generate_stress_heatmap(positions_df, eth_steps, depeg_steps) -> pd.DataFrame`

| Tham số | Kiểu dữ liệu | Mặc định | Mô tả |
|---|---|---:|---|
| `positions_df` | `pd.DataFrame` | Bắt buộc | Dữ liệu đầu vào vị thế như mô tả ở `run_stress_test`. |
| `eth_steps` | `list[float]` | Bắt buộc | Các cú sốc ETH dạng thập phân, ví dụ `[0.0, -0.1, -0.2]`. |
| `depeg_steps` | `list[float]` | Bắt buộc | Các mức mất neo LST/ETH dạng thập phân, ví dụ `[0.0, -0.03, -0.05]`. |

Đầu ra có một dòng cho mỗi cặp trong tích Descartes của hai danh sách, gồm:
`eth_shock: float64`, `depeg_shock: float64`, `debt_at_risk_usd: float64` và
`positions_at_risk_count: int64`.

### `find_liquidation_cliff(positions_df, max_drop=0.60, step=0.01) -> pd.DataFrame`

| Tham số | Kiểu dữ liệu | Mặc định | Mô tả |
|---|---|---:|---|
| `positions_df` | `pd.DataFrame` | Bắt buộc | Dữ liệu đầu vào vị thế như mô tả ở `run_stress_test`. |
| `max_drop` | `float` | `0.60` | Mức giảm giá ETH tối đa dưới dạng tỷ lệ, trong khoảng 0–1. |
| `step` | `float` | `0.01` | Bước giảm giá ETH dương, biểu diễn dạng tỷ lệ; mặc định 1% là `0.01`. |

Đầu ra là các trường đường cong vách rủi ro đã mô tả ở phần Data Mart
`cliff_curve.parquet`. Khi gọi trực tiếp hàm, không có hai cột đối chiếu mart cơ sở.

### `run_reverse_stress(positions_df, target_debt_usd_list) -> dict`

| Tham số | Kiểu dữ liệu | Mặc định | Mô tả |
|---|---|---:|---|
| `positions_df` | `pd.DataFrame` | Bắt buộc | Dữ liệu đầu vào vị thế như mô tả ở `run_stress_test`. |
| `target_debt_usd_list` | `list` | Bắt buộc | Các ngưỡng dư nợ mục tiêu: số USD (`100_000_000`), chuỗi phần trăm (`"10%"`) hoặc dictionary (`{"pct": 0.10}` / `{"usd": 100_000_000}`). |

Dictionary kết quả gồm `baseline_protocol_debt_usd: float64`, `search_grid`
(các mức tối đa và bước tìm kiếm ETH/depeg) và danh sách `targets`. Mỗi mục tiêu
có các trường: `target: string`, `target_debt_usd: float64`,
`target_debt_pct_of_protocol: float64`, `eth_shock_required: float64 | None`,
`lst_depeg_shock_required: float64 | None`, `minimum_shock_type: string | None`,
`minimum_shock: float64 | None`, `reached_within_search_grid: bool`. Lưới mặc định
quét cú sốc ETH độc lập từ 0% đến -60%, bước 1%, và depeg độc lập từ 0% đến
-20%, bước 0.5%. Kết quả là cú sốc ít nghiêm trọng nhất trong các điểm mẫu đạt
hoặc vượt ngưỡng dư nợ rủi ro.

---

## Chạy pipeline và kiểm thử

Chạy các lệnh sau từ thư mục gốc repository:

```powershell
python src/models/health_factor.py
python src/models/stress_test.py
python src/models/cliff_reverse.py
python -m pytest tests/
```
