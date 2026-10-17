# RESEARCH DESIGN SPECIFICATION

**Project Title:** Multi-Layer Network Contagion in Decentralized Finance: Quantifying Systemic Risk from Overlapping Collateral and Cross-Protocol Wallets  
**Vietnamese Title:** Lây lan mạng lưới đa tầng trong Tài chính Phi tập trung: Định lượng rủi ro hệ thống từ tài sản thế chấp chồng chéo và các ví liên giao thức  
**Version:** 2.0 (Optimized for Research Blueprint 07)

---

## 1. PHẠM VI NGHIÊN CỨU (RESEARCH SCOPE)

### 1.1. Phạm vi Không gian & Giao thức (Protocol & Market Scope)

Nghiên cứu áp dụng chiến lược **"Core-and-Bridge"** để đảm bảo độ sâu tái tạo bảng cân đối (Balance-sheet Reconstruction) trên Aave V3 mà vẫn bắt trọn kênh lây lan mạng lưới:

- **Giao thức lõi (Core Protocol - Tái tạo toàn phần 100%):** **Aave V3 trên mạng lưới Ethereum Mainnet**. Bao gồm đầy đủ 3 chế độ cấu trúc rủi ro:
  1. _High Efficiency Mode (E-Mode Category 1 - ETH Correlated):_ Trọng tâm phân tích đòn bẩy lặp (`wstETH`, `weETH`, `cbETH` thế chấp vay `WETH`).
  2. _General Cross-Margin Pool:_ Các vị thế thế chấp đa tài sản (`WBTC`, `WETH`, `wstETH`, `LINK`, `AAVE`) vay stablecoin (`USDC`, `USDT`, `DAI`).
  3. _Isolation Mode:_ Các tài sản thế chấp bị giới hạn trần nợ riêng biệt.
- **Giao thức mở rộng (Bridge Scope - Truy xuất có mục tiêu cho Q3):** **Compound V3 (Comet - thị trường USDC & WETH trên Ethereum)**. Chỉ trích xuất bảng cân đối của các địa chỉ ví có mặt đồng thời trên Aave V3 (`is_cross_protocol == True`) nhằm định lượng kênh truyền dẫn liên giao thức mà không gây nhiễu dữ liệu.
- **Không gian Thanh khoản DEX (Execution Liquidity Layer):** Các bể thanh khoản quyết định độ trượt giá nội sinh khi thanh lý, bao gồm: Curve Finance (`stETH/ETH` pool) và Uniswap V3 (`wstETH/WETH`, `WETH/USDC`, `WBTC/WETH`).

### 1.2. Phạm vi Thời gian & Thiết kế Mẫu (Point-in-Time Snapshots)

Để triệt tiêu hoàn toàn _Look-ahead bias_ và _Data leakage_, nghiên cứu sử dụng 2 mốc chụp trạng thái trên chuỗi (Block Snapshots) tách biệt:

- **Snapshot A (In-Sample Calibration & Counterfactual Stress Testing):** Một block tại trạng thái thị trường bình thường/đòn bẩy tích tụ cao (dùng để phân tích cấu trúc vị thế và chạy mô phỏng kịch bản cho Q1, Q2, Q3).
- **Snapshot B (Untouched Out-of-Sample Validation):** Block ngay trước một đợt sụt giảm mạnh lịch sử trên Ethereum (ví dụ: sự kiện ngày 05/08/2024, `block_number = 20458000` trước khi ETH giảm >20%). Toàn bộ sự kiện `LiquidationCall` diễn ra trong 24 giờ sau block này được dùng làm tập kiểm định thực nghiệm (Ground Truth).

---

## 2. CÂU HỎI NGHIÊN CỨU & GIẢ THUYẾT KIỂM ĐỊNH (RQ & HYPOTHESES H1–H3)

### Câu hỏi nghiên cứu 1 (Q1 - Rủi ro vi mô & Đòn bẩy nội sinh)

> _Recursive Looping có khuếch đại rủi ro tổn thất tín dụng như thế nào? (Trong cùng các điều kiện vốn ban đầu và kịch bản cú sốc giá, chiến lược Recursive Looping trong E-Mode của Aave V3 làm thay đổi xác suất phát sinh bad debt, tỷ lệ bad debt trên tổng dư nợ và tổn thất ở đuôi phân phối như thế nào so với các vị thế vay không sử dụng đòn bẩy lặp?)_

- **Giả thuyết H1 (Amplification & Tail-Risk Hypothesis):**
  - **H1a:** Ở cùng một quy mô vốn tự có ban đầu ($E_{i,0}$) và chịu cùng một kịch bản cú sốc giá ngoại sinh, các vị thế sử dụng chiến lược _Recursive Looping_ trong E-Mode có xác suất phát sinh nợ xấu ($P_{BD}$) và tỷ lệ nợ xấu trên tổng dư nợ ($BDR$) cao hơn có ý nghĩa thống kê so với các vị thế vay tiêu chuẩn (_Non-looping_).
  - **H1b (Convex Tail Loss):** Sự chênh lệch về tổn thất tín dụng giữa nhóm _Looping_ và _Non-looping_ mang tính phi tuyến tính lồi: ở vùng đuôi phân phối cực đoan ($\text{Expected Shortfall } ES_{99\%}$), hệ số khuếch đại tổn thất lớn hơn gấp nhiều lần so với vùng tổn thất trung vị, do hiệu ứng mất chốt giá đồng thời (co-crash depeg) làm xóa sạch vùng đệm thanh lý mỏng ($1 - LT_{\text{emode}} \approx 5\%$).
- **Kiểm định thống kê:** Kiểm định phi tham số Mann-Whitney U cho trung vị $BDR$, kiểm định hai mẫu Kolmogorov-Smirnov (K-S test) cho phân phối $HF$, và kiểm định Bootstrap cho chênh lệch $ES_{99\%}$.

---

### Câu hỏi nghiên cứu 2 (Q2 - Thiết kế cơ chế & Sự đánh đổi phản thực)

> _Quy tắc Close Factor ảnh hưởng đến sự đánh đổi giữa thu hồi nợ và lây lan tổn thất ra sao? (So với cơ chế Close Factor hiện hành, việc áp dụng các quy tắc thanh lý phản thực khác nhau sẽ làm thay đổi khối lượng thanh lý, tác động giá, số vị thế bị thanh lý nối tiếp và bad debt như thế nào dưới cùng một kịch bản stress?)_

- **Giả thuyết H2 (Liquidation Mechanism Trade-off Hypothesis):**
  - **H2a (Liquidity-Induced Contagion):** Dưới điều kiện thanh khoản DEX hữu hạn ($\lambda > 0$), việc áp dụng quy tắc thanh lý quyết liệt ($\text{Close Factor} = 100\%$) làm tăng đáng kể tổng khối lượng xả tài sản thế chấp ở Vòng 1 ($LV_1$), từ đó đẩy độ trượt giá nội sinh ($\Delta P^{\text{DEX}}$) lên cao và kích hoạt nhiều vị thế bị thanh lý nối tiếp ($N_{\text{seq}}$) ở các vòng sau hơn so với quy tắc bảo thủ ($\text{Close Factor} = 50\%$) hoặc quy tắc tuyến tính động (_Dynamic Linear CF_).
  - **H2b (U-shaped Bad Debt Curve):** Mối quan hệ giữa độ quyết liệt của Close Factor và tổng Nợ xấu hệ thống ($Bad Debt$) có dạng hình chữ U phụ thuộc vào độ sâu thanh khoản:
    - Khi thanh khoản DEX dồi dào ($\lambda$ nhỏ), $\text{CF} = 100\%$ giúp giảm nợ xấu nhờ thu hồi nợ dứt điểm trước khi giá rơi sâu hơn.
    - Khi thanh khoản DEX mỏng ($\lambda$ lớn), $\text{CF} = 100\%$ gây trượt giá vượt quá phần thưởng thanh lý ($\Delta P^{\text{DEX}} > LB_j$), làm tê liệt động lực của Liquidator (_Liquidation Freeze_) và làm tổng nợ xấu tăng vọt.
- **Kiểm định thống kê:** Phân tích độ nhạy hai chiều (Two-way Sensitivity ANOVA / Surface Response) giữa quy tắc $\text{CF} \in \{\text{Fixed 50\%, Baseline Aave, Dynamic Linear, Fixed 100\%}\}$ và tham số trượt giá $\lambda$.

---

### Câu hỏi nghiên cứu 3 (Q3 - Cấu trúc mạng lưới đa tầng & Kênh lây lan)

> _Cú sốc depeg truyền dẫn qua mạng tài sản và mạng ví với quy mô bao nhiêu? (Khi tỷ giá tương đối stETH/ETH suy giảm, tỷ lệ các vị thế ban đầu còn khỏe mạnh chuyển sang trạng thái đủ điều kiện thanh lý hoặc phát sinh bad debt ở những nhóm tài sản và giao thức khác nhau thay đổi như thế nào, và các đường liên kết nào đóng vai trò truyền dẫn quan trọng nhất?)_

- **Giả thuyết H3 (Multi-Layer Network Contagion Hypothesis):**
  - **H3a (Secondary Asset Spillover):** Một cú sốc mất chốt giá cục bộ vào nút tài sản `wstETH/ETH` ($\ge 4\%$) sẽ truyền dẫn sang các vị thế vay ban đầu hoàn toàn khỏe mạnh ($HF_{i,0} \ge 1.10$) ở nhóm tài sản khác (`WBTC`, `WETH`, `USDC`) thông qua cơ chế tịch thu tài sản thế chấp chồng chéo (_Overlapping Collateral Seizure_), khiến tỷ lệ chuyển trạng thái sang thanh lý ($TR_{\text{liq}}$) ở các vòng $r \ge 2$ chiếm tỷ trọng đáng kể trong tổng thanh lý toàn hệ thống.
  - **H3b (Centrality-Driven Transmission):** Sự lây lan tổn thất sang các bể tài sản khác và giao thức khác (Compound V3) không phân bổ đồng đều mà tập trung theo luật lũy thừa (Power-law) tại một số ít đường liên kết có **Độ trung tâm trung gian trọng số (Weighted Betweenness / DebtRank Centrality)** cao nhất — đặc biệt là các ví cá voi liên giao thức và các ví kết hợp `wstETH` với tài sản thế chấp chéo.
- **Kiểm định thống kê:** Hồi quy mức độ tổn thất lan tỏa theo các chỉ số cấu trúc mạng lưới (Degree, Eigenvector Centrality, DebtRank) và so sánh với mạng lưới ngẫu nhiên giữ nguyên bậc (Degree-preserving Randomized Null Network).

---

## 3. DỮ LIỆU NGHIÊN CỨU & QUY TRÌNH KIỂM ĐỊNH (DATA & VALIDATION)

### 3.1. Nguồn dữ liệu (Data Sources)

1. **On-chain Lending Events & State:** Truy vấn từ Aave V3 Subgraph và Compound V3 Subgraph (The Graph Network) kết hợp RPC Archive Node (`eth_call` tại `block_number` cố định).
2. **Oracle Price Feeds:** Lịch sử giá từ hợp đồng Chainlink Aggregator (`ETH/USD`, `stETH/ETH`, `BTC/USD`, `USDC/USD`) tại đúng block snapshot.
3. **DEX Liquidity Profiles:** Dữ liệu dự trữ và độ sâu sổ lệnh/AMM từ Curve Finance (`stETH/ETH` pool) và Uniswap V3 Subgraph để ước lượng hàm trượt giá thực nghiệm.

### 3.2. Quy trình Tái tạo Bảng cân đối (Point-in-Time Reconstruction)

Tại block $t_0$, số dư thực tế của ví $i$ đối với tài sản $j$ bắt buộc được tính qua chỉ số lãi cộng dồn của giao thức (không cộng trừ số gốc thô):
$$C_{ij,t_0} = \text{ScaledATokenBalance}_{ij,t_0} \times \frac{\text{LiquidityIndex}_{j,t_0}}{10^{27}}$$
$$D_{ik,t_0} = \text{ScaledVariableDebt}_{ik,t_0} \times \frac{\text{VariableBorrowIndex}_{k,t_0}}{10^{27}}$$

Hệ số sức khỏe ($HF_{i,t_0}$) được tái tạo theo chế độ của ví:
$$HF_{i,t_0} = \frac{\sum_{j \in \text{Enabled}} C_{ij,t_0} \cdot P_{j,t_0} \cdot LT_{i,j}}{\sum_k D_{ik,t_0} \cdot P_{k,t_0}}$$
Trong đó $LT_{i,j} = LT_{j}^{\text{emode}}$ nếu ví $i$ kích hoạt E-Mode và tài sản $j$ thuộc danh mục E-Mode; ngược lại $LT_{i,j} = LT_{j}^{\text{std}}$.

### 3.3. Tiêu chuẩn Kiểm định Dữ liệu (Data Validation Gate)

Mô phỏng chỉ được phép chạy sau khi vượt qua 3 bài kiểm tra:

1. **Cross-check HF:** Sai số tuyệt đối trung vị (Median Absolute Error) giữa $HF_{i,t_0}^{\text{calc}}$ và $HF_{i,t_0}^{\text{reported}}$ nhỏ hơn $0.005$ ($0.5\%$).
2. **TVL & Total Debt Reconciliation:** Tổng giá trị thế chấp và tổng nợ toàn bảng lệch không quá $1\%$ so với số liệu công bố trên Aave V3 Reserve Contract tại cùng block.
3. **Historical Liquidation Hit-Rate:** Trên tập Out-of-Sample, ít nhất $85\%$ các ví bị thanh lý thực tế trên chuỗi trong vòng 1 giờ đầu tiên của đợt sập giá phải nằm trong nhóm dự báo có $HF_i^{\text{sim}} < 1.02$.

---

## 4. PHƯƠNG PHÁP LUẬN ĐỊNH LƯỢNG (QUANTITATIVE METHODOLOGY)

### 4.1. Mô-đun 1 (Giải quyết Q1): Phân tách Looping & Mô phỏng Rủi ro Đuôi

1. **Quy tắc nhận diện vị thế Looping (`is_recursive_loop = True`):**
   - Ví bật E-Mode (`emode_category == 1`), thế chấp bằng LST (`wstETH`, `weETH`, `cbETH`), vay `WETH`, và có đòn bẩy hiệu dụng $L_{i,0} = \frac{C_{i,0}}{C_{i,0} - D_{i,0}} \ge 3.0\times$.
2. **Chuẩn hóa vốn ban đầu (Equity Matching):**
   - Phân tầng các ví thành 5 nhóm quy mô vốn tự có ($E_{i,0} = C_{i,0} - D_{i,0}$): `< $10k`, `$10k–$100k`, `$100k–$1M`, `$1M–$10M`, `> $10M`.
   - Trong mỗi phân tầng, chuẩn hóa tổn thất về cùng $100,000 USD vốn tự có ban đầu để loại bỏ sai lệch do quy mô tài sản.
3. **Mô phỏng Monte Carlo có cấu trúc phụ thuộc đuôi (Student-t Copula):**
   - Sinh $M = 10,000$ vector cú sốc $(\Delta P_{\text{ETH}}^{(m)}, \Delta P_{\text{stETH/ETH}}^{(m)})$ từ phân phối đồng thời Student-t Copula (bậc tự do $\nu = 4$, hệ số tương quan đuôi dương trong kịch bản khủng hoảng).
   - Với mỗi kịch bản $m$, chạy cơ chế thanh lý và tính toán xác suất nợ xấu $P_{BD}$, tỷ lệ nợ xấu $BDR$, và tổn thất kỳ vọng vùng đuôi $ES_{99\%}$.

### 4.2. Mô-đun 2 (Giải quyết Q2): Động cơ Vòng lặp Cascade & Ràng buộc Liquidator

1. **Thiết lập 4 quy tắc Close Factor ($\text{CF}$) phản thực:**
   - `baseline_aave`: $\text{CF} = 0.5$ nếu $0.95 \le HF < 1.0$; $\text{CF} = 1.0$ nếu $HF < 0.95$.
   - `fixed_50`: $\text{CF} = 0.5$ cho mọi $HF < 1.0$.
   - `fixed_100`: $\text{CF} = 1.0$ cho mọi $HF < 1.0$.
   - `dynamic_linear`: $\text{CF}(HF) = \text{clip}\left(\frac{1.0 - HF}{1.0 - 0.90}, 0.20, 1.00\right)$.
2. **Cơ chế thực thi của Liquidator và hình thành Bad Debt tại vòng lặp $r$:**
   - Với mỗi ví $i$ có $HF_{i,r} < 1.0$, lượng nợ cần trả tối đa là $D_{i,r}^{\text{repay}} = \text{CF}(HF_{i,r}) \cdot D_{i,r}$.
   - Lượng tài sản thế chấp yêu cầu tịch thu: $C_{i,j,r}^{\text{req}} = \frac{D_{i,r}^{\text{repay}} \cdot (1 + LB_{i,j})}{P_{j,r}}$.
   - **Điều kiện khả thi kinh tế của Liquidator (Rational Liquidator Condition):** Liquidator chỉ thực hiện thanh lý nếu lợi nhuận ròng sau trượt giá DEX ($\Delta P_{j,r}^{\text{DEX}}$) và phí Gas ($G_r$) dương:
     $$\Pi_{i,r}^{\text{liq}} = \min\left(C_{i,j,r}, C_{i,j,r}^{\text{req}}\right) \cdot P_{j,r} \cdot (1 - \Delta P_{j,r}^{\text{DEX}}) - D_{i,r}^{\text{repay}} - G_r > 0$$
   - Nếu $\Pi_{i,r}^{\text{liq}} \le 0$, vị thế bị **đóng băng thanh lý (Unliquidated Underwater Position)** trong vòng $r$.
   - Khi kết thúc chuỗi vòng lặp, mọi phần dư nợ vượt quá giá trị tài sản thế chấp khả thu hồi ($\sum_k D_{ik}P_k - \sum_j C_{ij}P_j^{\text{final}} > 0$) được ghi nhận chính thức là **Bad Debt ($BD_i$)**.

### 4.3. Mô-đun 3 (Giải quyết Q3): Mạng lưới Đa tầng & Thuật toán Truyền dẫn Depeg

1. **Xây dựng đồ thị 3 tầng $G = (V_A \cup V_W \cup V_P, E)$:**
   - **Tầng Tài sản ($V_A$):** Các nút token (`wstETH`, `WETH`, `WBTC`, `USDC`, `USDT`).
   - **Tầng Ví ($V_W$):** Các nút người vay. Cạnh $(w, a) \in E_{\text{supply}}$ mang trọng số giá trị thế chấp; cạnh $(a, w) \in E_{\text{borrow}}$ mang trọng số giá trị nợ.
   - **Tầng Giao thức ($V_P$):** Các phân khu thị trường (`Aave_EMode`, `Aave_Core`, `Compound_V3`). Các ví có vị thế ở nhiều phân khu/giao thức đóng vai trò là **Cầu nối lây lan (Bridge Nodes)**.
2. **Quy tắc chọn tài sản tịch thu trong ví đa thế chấp (Collateral Seizure Priority):**
   - Khi một ví thế chấp cả `wstETH` và `WBTC` bị $HF < 1$ do `wstETH` depeg, Liquidator ưu tiên tịch thu tài sản mang lại biên lợi nhuận ròng cao nhất ($\max [LB_j - \Delta P_j^{\text{DEX}}]$). Do đó, `WBTC` hoặc `WETH` trong cùng ví đó sẽ bị tịch thu và bán tháo ra DEX, tạo ra kênh truyền dẫn trực tiếp từ cú sốc `wstETH` sang giá `WBTC/WETH`.
3. **Quy tắc lan truyền qua Ví liên giao thức (Cross-Protocol Rebalancing):**
   - Khi một ví cầu nối (`is_cross_protocol == True`) bị thanh lý trên Aave V3 làm giảm vốn chủ sở hữu một lượng $\Delta E_{i,\text{Aave}}$, ví đó rút một tỷ lệ tương ứng tài sản thế chấp đang rảnh rỗi trên Compound V3 để bù đắp thanh khoản, hoặc bị thanh lý dây chuyền khi giá tài sản chung sụt giảm.

---

## 5. BENCHMARK, PHÂN TÍCH ĐỘ NHẠY & ĐẦU RA (EVALUATION & DELIVERABLES)

- **Benchmark Model:** Mô hình Stress Test Tĩnh truyền thống (giả định thanh khoản DEX vô hạn $\lambda = 0$, thanh lý tức thời trong 1 vòng duy nhất, không có lây lan mạng lưới).
- **Sensitivity Matrix:** Kiểm thử độ nhạy qua lưới tham số:
  - Mức Depeg `stETH/ETH`: $\{-1\%, -2\%, -3\%, -4\%, -5\%, -7\%, -10\%\}$.
  - Cú sốc giá `ETH/USD` đồng thời: $\{0\%, -10\%, -20\%, -30\%\}$.
  - Hệ số độ sâu thanh khoản DEX: $\{0.5\times, 1.0\times, 2.0\times\}$ so với thanh khoản thực tế.
