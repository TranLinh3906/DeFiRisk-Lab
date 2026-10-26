# Báo Cáo Hoàn Thành Thực Nghiệm N5 (Q1, Q2, Q3) và Kiểm Định Đầu Việc N2–N5

**Ngày xuất báo cáo:** 26/10/2026  
**Nhánh làm việc:** `cascade_model`  
**Gói dữ liệu bàn giao N2/N3/N4/N5:** `outputs/n5_completion_20261026/`  
**Gói kết quả thực nghiệm N5 (Q1, Q2 & Q3):** `outputs/n5_q1_q3_empirical_20261026/`  

---

## 1. Tổng Quan Trạng Thái Hoàn Thành Các Đầu Việc N2, N3, N4, N5

Đối chiếu trực tiếp với bảng Phân công công việc (`Nhóm_8_-_chủ_đề_7_-_dl_12h_26_10.docx`) và các gói dữ liệu trên kho mã nguồn:

| Thành viên | Đầu việc trọng tâm theo PCCV | Trạng thái | Nguồn dữ liệu & Bằng chứng nghiệm thu |
| :--- | :--- | :---: | :--- |
| **N2** *(On-chain Position & Liquidation Analyst)* | 1. Cào lịch sử Oracle block-by-block cho `B1`, `B2`<br>2. Quét lại `LiquidationCall` theo các cặp tài sản cốt lõi<br>3. Bổ sung biên lai phí Gas thực tế (`gas_used`, `effective_gas_price`) | **Hoàn thành 100%** | - `outputs/n5_completion_20261026/history/B1/` & `history/B2/`: **`1.023.440` dòng** giá Oracle active tokens theo từng block.<br>- `liquidation_events_rescanned.parquet`: **`2.732` sự kiện** `LiquidationCall` (`B1`: 865, `B2`: 1.867).<br>- `gas_receipts.parquet`: **`1.988` giao dịch** có biên lai Gas thực tế (`B1`: 514, `B2`: 1.474). |
| **N3** *(Market Liquidity & Contagion Analyst)* | 1. Hoàn tất Compound III (6 shards cho `A`, `B1`, `B2`)<br>2. Bổ sung 27 báo giá DEX mục tiêu qua Archive RPC<br>3. Xây dựng chuỗi lợi suất thị trường 365 ngày & `10.000` kịch bản Copula | **Hoàn thành 100%** | - `outputs/compound_consolidated_A_B1_B2_20261025_complete/`: **`137.050 / 137.050` truy vấn** (100% coverage, 0 thiếu).<br>- `outputs/n3_priority2_quote_grid_v2_with_27/`: Đã lấy đủ **`27/27` báo giá mục tiêu** qua Ethereum Archive RPC (`111` quotes xác thực + `24` anchors).<br>- `calibration_market/`: **`366` ngày** giá đóng cửa UTC, **`365` cặp lợi suất**, **`10.000` kịch bản** Student-$t$ Copula (`seed=42`). |
| **N4** *(Baseline Risk & Stress Testing Analyst)* | 1. Replay E-Mode LT (`0x01`) trên tập cohort đã lọc dust (`>= 100 USD`)<br>2. Chuẩn hóa phạm vi depeg (`STETH`, `WSTETH`) và phân tầng vốn gốc<br>3. Chạy lại heatmap, cliff và reverse stress theo mẫu số nợ gốc $t_0$ | **Hoàn thành 100%** | - `outputs/n5_completion_20261026/emode_replay/`: **`50.311` ví** cohort nghiên cứu (`A`: 19.664, `B1`: 9.675, `B2`: 20.972), đối chiếu độc lập đạt **`0` sai lệch** (`hf_mismatch_gt_1e4 = 0`).<br>- `stress_heatmap.parquet` (`300` ô), `liquidation_cliff.parquet` (`19` mức), `reverse_stress.json` (`10%`, `25%`, `50%` nợ gốc cohort). |
| **N5** *(Cascade & Model Validation Analyst)* | 1. Kiểm định độc lập toàn bộ đầu vào N2/N3/N4 & chạy 570 lượt cascade độ nhạy cơ chế<br>2. **Chạy thực nghiệm Q1 (MC 10.000 & 19 kịch bản stress), Q2 (228 lượt đối chiếu 4 quy tắc Close Factor) và Q3 (`M15`–`M18`) trên 50.311 ví**<br>3. Kiểm định đủ 18/18 KPI (`M01`–`M18`) và khóa manifest SHA-256 | **Hoàn thành 100%** | - `outputs/n5_completion_20261026/approved_package_with27/`: **`570` lượt chạy** độ nhạy cơ chế thanh lý.<br>- `outputs/n5_q1_q3_empirical_20261026/`: Hoàn tất toàn bộ bảng thực nghiệm **Q1, Q2 (`228` lượt CF counterfactual) & Q3**, **`18/18` KPI** vượt qua kiểm định tự động, `verify_manifest == []`. |

---

## 2. Kết Quả Thu Thập & Xác Thực 27 Báo Giá DEX Mục Tiêu (N3 $\to$ N5)

- **Script thực thi:** `scripts/n3_fetch_targeted_27_quotes.py`
- **Nguồn RPC:** Ethereum Mainnet Archive RPC (`chain_id = 1`)
- **Đầu ra:**
  - `outputs/n3_priority2_quote_grid_v2_with_27/n3_targeted_27_rpc_verified_only.parquet` (`27/27` dòng đạt `status = "verified"`, kèm `verified_block_hash`, `amount_in_raw`, `amount_out_raw`, `spot_price`, `impact_pct`).
  - `outputs/n3_priority2_quote_grid_v2_with_27/n3_quote_grid_v2_with_27.parquet` (`111` báo giá thực thi đã xác thực trên `3` snapshot $\times$ `4` tuyến thanh lý).
  - Xử lý chính xác sai số làm tròn số nguyên `1 raw unit` (`10^{-6}` USDC) cho 3 lệnh quy mô nhỏ (`< 160 USD` trên tuyến `WETH -> USDC`) bằng chính sách `conservative_raw_unit_bound` trong `src/models/n5_n3_update.py` mà không làm thay đổi dữ liệu gốc.

---

## 3. Kết Quả Thực Nghiệm Q1: Rủi Ro Vị Thế `Looping` vs `Non-Looping` (`M05`–`M09`)

Phân tích thực nghiệm được thực hiện trên toàn bộ **`50.311` ví** thuộc tập cohort đã lọc (`initial_debt_usd >= 100`), đối chiếu ngang hàng trên **5 tầng vốn tự có ban đầu (`common_strata = 5`: `<10k`, `10k-100k`, `100k-1m`, `1m-10m`, `>=10m`)** để loại bỏ sai lệch quy mô vốn khi so sánh giữa `Looping` (`is_recursive_loop = True`) và `Non-looping` (`is_recursive_loop = False`).

### 3.1. Thực nghiệm trên `19` kịch bản Stress sau thanh lý (`q1_stress_matched_comparison.csv`)

Các kịch bản stress bao phủ sụt giảm giá `ETH` (`-10%` đến `-50%`), `BTC` (`-10%` đến `-30%`), `Stablecoin` (`-2%`, `-5%`), `stETH/wstETH depeg` (`-1%` đến `-10%`), và kịch bản kết hợp (`combined_eth20_depeg5`, `combined_extreme`) sau khi đã chạy mô hình thanh lý đa vòng:

| Snapshot | Block | Nhóm vị thế | Số tầng vốn chung (`common_strata`) | Tổn thất TB / 100k USD vốn gốc (`M08`, USD) | Tổn thất đuôi `ES99` / 100k USD vốn gốc (`M09`, USD) | Xác suất phát sinh nợ xấu (`M05`) |
| :--- | :---: | :--- | :---: | :---: | :---: | :---: |
| **`A`** | `26179841` | `Non-looping` | 5 | `127,28` | `1.562,12` | `0,4913%` |
| **`A`** | `26179841` | **`Looping`** | 5 | **`242,52`** | **`2.708,28`** | **`1,4413%`** |
| **`B1`** | `20458637` | `Non-looping` | 5 | `894,55` | `9.393,16` | `2,4449%` |
| **`B1`** | `20458637` | **`Looping`** | 5 | `211,93` | `2.354,49` | `0,7388%` |
| **`B2`** | `23543615` | `Non-looping` | 5 | `175,79` | `2.120,47` | `0,6522%` |
| **`B2`** | `23543615` | **`Looping`** | 5 | **`213,26`** | **`2.206,48`** | **`0,7726%`** |

### 3.2. Thực nghiệm trên `10.000` kịch bản Monte Carlo Student-$t$ Copula (`q1_mc10000_matched_comparison.csv`)

10.000 kịch bản đồng biến động `(eth_shock, depeg_shock)` được lấy mẫu từ phân phối Student-$t$ Copula hiệu chỉnh trên 365 cặp lợi suất ngày liên tiếp (`max 1-day ETH drop = -14,34%`, `max 1-day stETH depeg = -0,56%`):

| Snapshot | Block | Nhóm vị thế | Số tầng vốn chung | Tổn thất TB / 100k USD vốn gốc (USD) | Tổn thất đuôi `ES99` / 100k USD vốn gốc (USD) | Xác suất nợ xấu (`P(BD > 0)`) | Xác suất vi phạm ngưỡng (`P(HF < 1)`) |
| :--- | :---: | :--- | :---: | :---: | :---: | :---: | :---: |
| **`A`** | `26179841` | `Non-looping` | 5 | `5,50` | `476,94` | `0,0201%` | `0,1837%` |
| **`A`** | `26179841` | `Looping` | 5 | `0,00` | `0,00` | `0,0000%` | `0,0005%` |
| **`B1`** | `20458637` | `Non-looping` | 5 | `0,01` | `0,71` | `0,0001%` | `0,2619%` |
| **`B1`** | `20458637` | `Looping` | 5 | `0,00` | `0,00` | `0,0000%` | `0,0000%` |
| **`B2`** | `23543615` | `Non-looping` | 5 | `5,09` | `422,25` | `0,0178%` | `0,1653%` |
| **`B2`** | `23543615` | `Looping` | 5 | `0,00` | `0,00` | `0,0000%` | `0,0195%` |

> **Kết luận thực nghiệm Q1:**  
> Kết hợp cả hai góc nhìn (`10.000` kịch bản biến động ngày thông thường và `19` kịch bản stress đuôi sau thanh lý) cho thấy cấu trúc **rủi ro bậc thang (cliff risk)** đặc trưng của đòn bẩy đệ quy (`Looping`) trong chế độ E-Mode:
> 1. Trong điều kiện thị trường dao động hàng ngày (khi `stETH/ETH` lệch giá không quá `-0,56%`, thấp hơn biên độ đệm thanh lý `2,5%–5%` của E-Mode), các vị thế `Looping` hầu như miễn nhiễm với thanh lý do tài sản thế chấp (`wstETH`/`stETH`) và khoản nợ (`WETH`) biến động cùng chiều.
> 2. Ngược lại, khi xảy ra cú sốc depeg vượt ngưỡng đệm E-Mode (như trong bộ 19 kịch bản stress đuôi tại `A` và `B2`), đòn bẩy hiệu dụng cao khiến nhóm `Looping` sụp đổ nhanh hơn: tại snapshot `A`, xác suất phát sinh nợ xấu của `Looping` cao gấp **2,93 lần** (`1,441%` so với `0,491%`) và tổn thất đuôi `ES99` trên mỗi 100.000 USD vốn tự có cao gấp **1,73 lần** (`2.708,28 USD` so với `1.562,12 USD`) so với `Non-looping` cùng tầng vốn.

---

## 4. Kết Quả Thực Nghiệm Q2: Thiết Kế Cơ Chế & Đối Chiếu 4 Quy Tắc `Close Factor` Phản Thực (`M10`–`M14`)

Để kiểm định đầy đủ giả thuyết **H2 (`Liquidation Mechanism Trade-off Hypothesis`)**, nghiên cứu thực hiện hai lớp thực nghiệm:
1. **Gói `570` lượt chạy độ nhạy cơ chế vi mô** (`outputs/n5_completion_20261026/approved_package_with27/`): `3 snapshot x 19 kịch bản x 10 biến thể` (`primary`, `curve_power`, `pool_reset`, `gas_0`, `gas_20`, `gas_60`, `random_42`, `random_43`, `random_44`, `fee_zero`).
2. **Gói `228` lượt chạy phản thực đối chiếu 4 quy tắc `Close Factor`** (`outputs/n5_q1_q3_empirical_20261026/q2_cf_counterfactual_summary.csv` & `q2_cf_policy_aggregate.csv`): `3 snapshot x 19 kịch bản x 4 quy tắc CF` (`baseline_aave`, `fixed_50`, `fixed_100`, `dynamic_linear`), trong đó kết quả của `baseline_aave` được kiểm tra khớp tuyệt đối (`rtol = 1e-9`) với biến thể `primary`.

### 4.1. Tổng hợp đối chiếu 4 quy tắc `Close Factor` qua 19 kịch bản stress (`q2_cf_policy_aggregate.csv`)

| Snapshot | Quy tắc `Close Factor` (`cf_rule`) | Tổng lượt thanh lý | Thanh lý Vòng 1 | Thanh lý Vòng $r \ge 2$ (`M12`) | Nợ trả TB Vòng 1 (`M10`, USD) | Tổng nợ trả TB (`M10`, USD) | Hệ số khuếch đại `CAF` TB (`M13`) | Trượt giá DEX TB (`M11`, %) | Số ứng viên vượt miền thanh khoản DEX | Nợ xấu sau thanh lý TB (`M06`, USD) |
| :--- | :--- | ---: | ---: | ---: | ---: | ---: | :---: | :---: | ---: | ---: |
| **`A`** | `baseline_aave` | 1.228 | 1.141 | 87 | `1.956.685` | `2.073.114` | `1,0805x` | `0,3923%` | 8.098 | `5.575.510` |
| **`A`** | `fixed_50` | **1.247** | 1.143 | **104** | `1.976.451` | `2.098.014` | `1,0824x` | `0,3960%` | 8.285 | **`5.575.244`** |
| **`A`** | `fixed_100` | 1.240 | **1.150** | 90 | `1.941.015` | `2.102.427` | `1,0936x` | `0,4101%` | 8.108 | `5.575.524` |
| **`A`** | `dynamic_linear` | 1.229 | 1.137 | 92 | **`2.186.129`** | **`2.293.571`** | **`1,1018x`** | `0,4595%` | **7.982** | `5.575.444` |
| **`B1`** | `baseline_aave` | 1.449 | 1.392 | 57 | `3.300.280` | `3.326.957` | `1,0053x` | `0,5954%` | 19.613 | `18.162.721` |
| **`B1`** | `fixed_50` | **1.496** | **1.429** | **67** | `3.305.482` | `3.339.772` | **`1,0070x`** | `0,5976%` | 20.084 | **`18.161.347`** |
| **`B1`** | `fixed_100` | 1.467 | 1.411 | 56 | **`3.464.815`** | **`3.489.930`** | `1,0051x` | `0,5825%` | 19.704 | `18.162.359` |
| **`B1`** | `dynamic_linear` | 1.433 | 1.372 | 61 | `3.178.061` | `3.201.536` | `1,0049x` | `0,6126%` | 20.022 | `18.162.729` |
| **`B2`** | `baseline_aave` | 1.061 | 977 | 84 | `6.533.576` | `6.702.761` | `1,0279x` | `0,6629%` | 15.058 | `12.917.924` |
| **`B2`** | `fixed_50` | **1.173** | **1.059** | **114** | `5.996.702` | `6.195.307` | `1,0344x` | `0,6543%` | 16.146 | **`12.910.343`** |
| **`B2`** | `fixed_100` | 1.049 | 966 | 83 | **`7.591.226`** | **`7.781.605`** | `1,0275x` | **`0,6789%`** | 15.070 | `12.917.924` |
| **`B2`** | `dynamic_linear` | 1.064 | 982 | 82 | `6.047.829` | `6.302.574` | **`1,0474x`** | **`0,6521%`** | **14.718** | `12.915.585` |

### 4.2. Minh họa sự đánh đổi cơ chế tại các kịch bản trọng tâm (`q2_cf_counterfactual_summary.csv`)

- **Hiện tượng tắc nghẽn thanh khoản của `fixed_100` đối với ví cá voi (Liquidity-Induced Freeze):**
  - Tại `A` dưới kịch bản `depeg_4` và `depeg_5`: Khi áp dụng `fixed_100` ($\text{CF} = 100\%$), quy mô tịch thu toàn bộ vị thế cá voi trong 1 lần vượt ngưỡng lợi nhuận biên sau trượt giá DEX, khiến tổng nợ thu hồi giảm xuống chỉ còn **`1,53 triệu USD`** (`r1_unprofitable_supported_debt_usd` tăng từ `4,16 triệu USD` lên **`6,26 triệu USD`**). Ngược lại, `baseline_aave` và `fixed_50` ($\text{CF} = 50\%$ khi $0.95 \le HF < 1.0$) chia nhỏ lệnh thanh lý nên thu hồi được **`1,82 triệu USD`**, và `dynamic_linear` ($\text{CF} \ge 20\%$) thu hồi được tới **`2,55 triệu USD`**!
  - Tại `B2` dưới kịch bản `eth_drop_10`: `fixed_100` chỉ thực hiện được `39` lệnh thanh lý (`64` ứng viên bị vượt miền thanh khoản DEX `outside_quote_domain`), trong khi `dynamic_linear` thực hiện được **`54` lệnh thanh lý** (chỉ `16` ứng viên vượt miền).
- **Khuếch đại số vòng lặp và giảm nợ xấu của `fixed_50`:**
  - Trên cả 3 snapshot `A`, `B1`, `B2`, quy tắc bảo thủ `fixed_50` đạt **số lượng vị thế được thanh lý thành công cao nhất** (`1.247`, `1.496`, `1.173`), **số lượt thanh lý ở các vòng $r \ge 2$ cao nhất** (`104`, `67`, `114` — kéo dài tới `7` vòng ở `B2 btc_drop_30` với `CAF = 1,133x`), và **tổng nợ xấu còn lại sau thanh lý (`M06`) thấp nhất** trên cả 3 snapshot (`A`: `5.575.244 USD`, `B1`: `18.161.347 USD`, `B2`: `12.910.343 USD`).

---

## 5. Kết Quả Thực Nghiệm Q3: Lây Lan Mạng Lưới & Liên Giao Thức (`M15`–`M18`)

### 5.1. `M15` – Tỷ lệ dịch chuyển từ Khỏe mạnh sang Thanh lý (`q3_cascade_transitions_summary.csv`)

Chỉ số `M15` được tính toán tại cả hai ngưỡng sức khỏe ban đầu: ngưỡng từ điển KPI (`HF_0 >= 1.05`) và ngưỡng giả thuyết H3 (`HF_0 >= 1.10`). Bảng dưới đây trích xuất các kịch bản trọng tâm từ tổng số `57` tổ hợp (`3 snapshot x 19 kịch bản`):

| Snapshot | Kịch bản (`scenario_id`) | Tổng ví cohort | Ví khỏe mạnh (`HF_0 >= 1.05`) | Chuyển sang thanh lý (`HF_0 >= 1.05`) | Tỷ lệ `M15` (`HF_0 >= 1.05`) | Tỷ lệ `M15` (`HF_0 >= 1.10`) | Tổng ví được thực thi | Ví thanh lý đa vòng (`round >= 2`) |
| :--- | :--- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| **`A`** | `eth_drop_20` | 19.664 | 18.989 | 248 | `1,31%` | `1,01%` | 91 | 3 |
| **`A`** | `depeg_5` | 19.664 | 18.989 | 12 | `0,06%` | `0,03%` | 9 | 0 |
| **`A`** | `combined_eth20_depeg5` | 19.664 | 18.989 | 261 | **`1,37%`** | **`1,07%`** | 101 | 3 |
| **`A`** | `combined_extreme` | 19.664 | 18.989 | 840 | **`4,42%`** | **`3,90%`** | 156 | 10 |
| **`B1`** | `eth_drop_20` | 9.675 | 9.196 | 911 | `9,91%` | `7,27%` | 117 | 0 |
| **`B1`** | `depeg_5` | 9.675 | 9.196 | 22 | `0,24%` | `0,08%` | 11 | 0 |
| **`B1`** | `combined_eth20_depeg5` | 9.675 | 9.196 | 963 | **`10,47%`** | **`7,81%`** | 134 | 2 |
| **`B1`** | `combined_extreme` | 9.675 | 9.196 | 2.179 | **`23,70%`** | **`20,75%`** | 184 | 0 |
| **`B2`** | `eth_drop_20` | 20.972 | 20.095 | 393 | `1,96%` | `1,66%` | 68 | 0 |
| **`B2`** | `depeg_5` | 20.972 | 20.095 | 17 | `0,08%` | `0,03%` | 6 | 0 |
| **`B2`** | `combined_eth20_depeg5` | 20.972 | 20.095 | 418 | **`2,08%`** | **`1,78%`** | 76 | 0 |
| **`B2`** | `combined_extreme` | 20.972 | 20.095 | 1.324 | **`6,59%`** | **`6,15%`** | 147 | 0 |

### 5.2. `M16` – Lây lan liên giao thức sang Compound III (`q3_explicit_withdrawal_summary.csv`)

Sử dụng bộ dữ liệu gộp 6 shard Compound III hoàn chỉnh (`3.185` dòng vị thế thực dương, `797` ví chồng chéo với tập cohort Aave V3), mô hình đánh giá cả (i) stress giá trực tiếp trên các vị thế vay Compound III (`q3_compound_price_stress.parquet`) và (ii) kênh rút thanh khoản cơ sở (`explicit_base_supply_withdrawal`) khi các ví chồng chéo rút `25%`, `50%`, `100%` lượng cung `base_supply` trên Compound III:

| Snapshot | Block | Thị trường Compound III (`market_id`) | Số ví chồng chéo rút vốn | Rút `25%` (`USD`) | Rút `50%` (`USD`) | Rút `100%` (`USD`) | Rút `100%` (`Tokens`) |
| :--- | :---: | :--- | ---: | ---: | ---: | ---: | ---: |
| **`B1`** | `20458637` | `compound_v3_ethereum_usdc` | 17 | `177.034,62` | `354.069,23` | **`708.138,47`** | `708.163,01 USDC` |
| **`B1`** | `20458637` | `compound_v3_ethereum_weth` | 14 | `254,80` | `509,60` | **`1.019,20`** | `1.019,20 WETH` |
| **`B2`** | `23543615` | `compound_v3_ethereum_usdc` | 34 | `28.507,83` | `57.015,66` | **`114.031,33`** | `114.062,65 USDC` |
| **`B2`** | `23543615` | `compound_v3_ethereum_weth` | 20 | `2.553,92` | `5.107,84` | **`10.215,68`** | `10.215,68 WETH` |
| **`A`** | `26179841` | `compound_v3_ethereum_usdc` | 38 | `37.053,53` | `74.107,06` | **`148.214,11`** | `148.237,33 USDC` |
| **`A`** | `26179841` | `compound_v3_ethereum_weth` | 20 | `0,83` | `1,65` | **`3,31`** | `3,31 WETH` |

### 5.3. `M17` & `M18` – Đồ thị mạng lưới phơi nhiễm & Điểm số hệ thống `DebtRank` (`q3_debtrank_nodes.csv`)

- **Bảo toàn mạng lưới (`M17`):** Toàn bộ các cạnh phơi nhiễm Token – Ví – Giao thức tại `A`, `B1`, `B2` (`A|B1|B2/graph_network_edges_candidate.parquet`) đều vượt qua kiểm định bảo toàn 100% quy mô tài sản thế chấp và dư nợ theo từng token (`conservation["passed"].all() == True`).
- **Lan truyền `DebtRank` (`M18`):** Dưới kịch bản chuẩn `combined_eth20_depeg5` (`WETH`/`weETH`/`rETH` chịu sốc ban đầu `0,20`; `wstETH` chịu sốc gộp `0,24`), mức độ tổn thất lan truyền tăng thêm (`incremental_distress`) tại các nút tập hợp như sau:

| Snapshot | Nút `cohort:looping_wallets` (`incremental_distress`) | Nút `cohort:non_looping_wallets` (`incremental_distress`) | Nút `protocol:aave_v3_ethereum` (`incremental_distress`) | Tổng điểm `DebtRank` không trọng số | Tổng điểm `DebtRank` có trọng số dung lượng (`M18`) |
| :--- | :---: | :---: | :---: | :---: | :---: |
| **`A` (`26179841`)** | **`0,2045`** (`3,33 tỷ USD`) | `0,1052` (`13,95 tỷ USD`) | **`0,1374`** (`9,43 tỷ USD`) | `0,4471` | **`0,0661`** |
| **`B1` (`20458637`)** | **`0,2129`** (`2,62 tỷ USD`) | `0,1406` (`7,32 tỷ USD`) | **`0,1693`** (`5,87 tỷ USD`) | `0,5229` | **`0,0826`** |
| **`B2` (`23543615`)** | **`0,2086`** (`8,55 tỷ USD`) | `0,0975` (`29,85 tỷ USD`) | **`0,1370`** (`21,69 tỷ USD`) | `0,4432` | **`0,0662`** |

---

## 6. Danh Mục Tệp Đầu Ra & Lệnh Tái Lập (Reproducibility)

### 6.1. Danh mục tệp kết quả chính trong `outputs/n5_q1_q3_empirical_20261026/`
- `summary.json` & `manifest.json`: Tổng hợp kết quả thực nghiệm Q1, Q2, Q3 và bảng băm SHA-256 của toàn bộ tệp nguồn + tệp đầu ra.
- `kpi_mapping.csv` & `kpi_artifact_qa.json`: Ánh xạ và biên bản kiểm định tự động đủ `18/18` chỉ số `M01`–`M18`.
- **Q1:** `q1_mc10000_matched_comparison.csv`, `q1_mc10000_equity_strata.csv`, `q1_mc10000_wallet_risk.parquet`, `q1_stress_matched_comparison.csv`, `q1_equity_strata.parquet`, `q1_wallet_risk.parquet`, `q1_wallet_losses.parquet`.
- **Q2:** `q2_cf_policy_aggregate.csv`, `q2_cf_counterfactual_summary.csv` (`228` lượt đối chiếu 4 quy tắc `Close Factor`), `q2_cf_counterfactual_rounds.csv`, cùng `execution/cascade_summary.parquet` & `execution/cascade_rounds.parquet`.
- **Q3:** `q3_cascade_transitions_summary.csv`, `q3_cascade_transitions.parquet`, `compound/exposures_complete.parquet`, `q3_compound_price_stress.parquet`, `q3_explicit_withdrawal_summary.csv`, `q3_explicit_withdrawal_ledger.parquet`, `q3_debtrank.json`, `q3_debtrank_nodes.csv`, `A|B1|B2/graph_network_edges_candidate.parquet`.

### 6.2. Lệnh tái lập và kiểm thử

```powershell
# 1. Chạy lại toàn bộ thực nghiệm Q1, Q2 & Q3 của N5
.\.venv\Scripts\python.exe -B -X utf8 -m scripts.n5_run_q1_q3_empirical --output-dir outputs/n5_q1_q3_empirical_20261026 --overwrite

# 2. Chạy bộ kiểm thử tự động toàn hệ thống (177 tests)
.\.venv\Scripts\python.exe -B -X utf8 -m pytest tests/ -q -p no:cacheprovider --tb=short
```
