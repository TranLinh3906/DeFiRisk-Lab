# Bảng kết quả chuẩn để viết báo cáo — 26/10/2026

Nguồn khoa học chính thức duy nhất: `outputs/n5_official_release_20261026`; manifest trạng thái **sealed**, SHA-256 `5434c3ab1f6f4b2b13434b3eeed0b477b81846e8f29af8c23620303e48397717`. Đây là phụ lục trích dẫn được sinh từ bản chốt, không phải lần chạy mô hình mới. Mọi đường dẫn trong CSV tính từ gốc dự án; [evidence_index.csv](evidence_index.csv) ghi SHA-256 của nguồn thực sự đọc. CSV lưu số đầy đủ; bảng dưới làm tròn để trình bày. Không sửa byte bản chốt hoặc đổi kết luận giả thuyết.

## 1. Bảng H1a–H3b

| Giả thuyết | Số liệu then chốt | Tiêu chí kiểm định | Kết luận | Tệp đầu ra |
| --- | --- | --- | --- | --- |
| H1a | 0/3 snapshot đạt đồng thời PBD/BDR. A ΔPBD=-0.0417939 điểm %, pHolm=0.46385; ΔBDR=-0.000843499 điểm %, pHolm=0.46385; B1 ΔPBD=0 điểm %, pHolm=1; ΔBDR=0 điểm %, pHolm=1; B2 ΔPBD=-0.094557 điểm %, pHolm=0.192807; ΔBDR=-0.00357751 điểm %, pHolm=0.192807 | Chênh lệch looping−non-looping dương, CI95% lower>0 và Wilcoxon p Holm<0,05 cho cả PBD và BDR; Holm trên 24 outcome; thiết kế chính t4 1 ngày trước từng snapshot. | Không được ủng hộ trong thiết kế chính; không suy ra looping an toàn trong mọi thị trường. | [paired_hypothesis_tests_adjusted.json](../../outputs/n5_official_release_20261026/results/q1/paired_hypothesis_tests_adjusted.json)<br>[equity_balance_before_after.csv](../../outputs/n5_official_release_20261026/results/q1/equity_balance_before_after.csv) |
| H1b | ES99 looping/non-looping, USD/100.000 equity: A 0.0000/953.9851; B1 0.0000/0.0000; B2 0.0000/2334.9889. Độ cong quadratic dương 6/6 đường; lồi rời rạc toàn miền 2/6 (hai đường B2). | ES99 nhóm trên cùng kịch bản; bootstrap ghép scenario 1.000 lần; CI95% ΔES99 phải hoàn toàn dương để ủng hộ tail loss looping cao hơn. Kiểm độ cong riêng: CI95% hệ số quadratic>0; lồi toàn miền cần mọi thay đổi độ dốc>=−1e−7; bootstrap cặp ví 1.000 lần. | Ủng hộ giới hạn về độ cong trên lưới điều kiện; chưa xác nhận looping khuếch đại tail risk tổng quát hoặc lồi toàn miền mọi snapshot. | [hypothesis_evidence.csv](../../outputs/n5_official_release_20261026/results/q1/hypothesis_evidence.csv)<br>[convexity_tests.json](../../outputs/n5_official_release_20261026/results/q1/convexity_tests.json) |
| H2a | Full-feedback 17/54 cặp tăng đồng thời; fixed/predictive 0/54. Nợ xấu CF100 tăng/giảm/không đổi: full 31/13/10; fixed/proxy 24/12/18 (ngưỡng USD 1e−8). | Trong mỗi cặp CF100−CF50 cùng snapshot/shock/depth/feedback: cả bán collateral vòng 1 và ví mới đủ điều kiện từ vòng 2 đều tăng. Đây là tiêu chí phản thực theo ô, không phải kiểm định nhân quả hay p tổng thể. | Ủng hộ có điều kiện ở full-feedback sensitivity; không được ủng hộ như quy luật phổ quát trong chế độ chính. | [h2a_paired_contrasts.csv](../../outputs/n5_official_release_20261026/results/q2/h2a_paired_contrasts.csv)<br>[dense_cf_grid.csv](../../outputs/n5_official_release_20261026/results/q2/dense_cf_grid.csv) |
| H2b | 0/9 mặt cắt depth×feedback đạt; 9 mức CF × 18 block mỗi mặt cắt. Các ô predictive trùng fixed do proxy=0, không phải lặp độc lập. | Bootstrap 2.000 lần theo 18 block snapshot×shock: CI95% beta2>0, CI độ dốc trái hoàn toàn âm, phải hoàn toàn dương; điểm cực tiểu và CI nằm trong CF [0,2;1,0], với ≥95% bootstrap có curvature dương. CI điểm đảo chiều tính trên draw curvature dương. Không dùng riêng beta2>0. | Không được ủng hộ theo tiêu chí đã dùng; không khẳng định tồn tại CF tối ưu nội miền. | [u_shape_tests_with_turning_ci.json](../../outputs/n5_official_release_20261026/results/q2/u_shape_tests_with_turning_ci.json)<br>[dense_cf_grid.csv](../../outputs/n5_official_release_20261026/results/q2/dense_cf_grid.csv) |
| H3a | Predictive-proxy: 0 lượt/12 tổ hợp; full-feedback 7 lượt/12. Cả 3 route đều không cải thiện RMSE B2; proxy chọn=0; 296 train+296 test/route. Compound: 0 predictive và 9 full trên 48 tổ hợp tổng. | Dự báo B1→B2: feature trước target, CI HAC truyền dương, target vượt độ phân giải, RMSE cải thiện baseline. Tách khỏi mô phỏng: HF0≥1,10, HF sau shock≥1; collateral khác STETH/WSTETH≥10% và ≥100 USD; mới đủ điều kiện vòng≥2. | Chưa xác lập lây lan thực tế. Quy tắc dự báo bảo thủ chọn proxy=0; sensitivity có kích hoạt mới nhưng không thay thế chứng cứ thực nghiệm. | [route_validation.json](../../outputs/n5_official_release_20261026/results/feedback/route_validation.json)<br>[direct_induced_summary.csv](../../outputs/n5_official_release_20261026/results/q3/direct_induced_summary.csv)<br>[compound_direct_induced_summary.csv](../../outputs/n5_official_release_20261026/results/q3/compound_direct_induced_summary.csv) |
| H3b | Spearman A/B1/B2=0,02200/0,02720/−0,00218; p null degree+exposure=0,020/0,055/1,000; p HC3 betweenness=0,4488/0,3653/0,0833; 69 can thiệp (60 nguồn+9 route). | Spearman loss tăng thêm với betweenness; 999 hoán vị mỗi null exact-degree/exposure/degree+exposure; p một phía chưa chỉnh nhiều kiểm định. Hồi quy HC3 có kiểm soát; can thiệp nguồn/cạnh là chẩn đoán mô hình, chưa có tiêu chí chấp nhận nhân quả/power-law. | Chẩn đoán cấu trúc có điều kiện, không nhất quán giữa snapshot/đối chứng/hồi quy; chưa xác nhận centrality gây lây lan hoặc power-law. | [incremental_loss_network_nulls.json](../../outputs/n5_official_release_20261026/results/q3/incremental_loss_network_nulls.json)<br>[incremental_loss_regressions.json](../../outputs/n5_official_release_20261026/results/q3/incremental_loss_regressions.json)<br>[node_edge_interventions.csv](../../outputs/n5_official_release_20261026/results/q3/node_edge_interventions.csv) |

[Bảng CSV chuẩn](official_hypothesis_results.csv); [số liệu Q1 đầy đủ, CI và p](q1_primary_metrics.csv); [chẩn đoán mạng đầy đủ](q3_network_diagnostics.csv). Các kết luận phân biệt **không được ủng hộ**, **ủng hộ có điều kiện**, **chưa xác lập**; không đồng nhất chúng với chứng minh giả thuyết sai.

## 2. Phiên bản và phạm vi trích dẫn

| Question | Version | Class | Selection | Source |
| --- | --- | --- | --- | --- |
| Q1 | official_release_v1 | primary | pre_event_t4_1d; one-to-one equity matching; post liquidation fixed oracle | [paired_hypothesis_tests_adjusted.json](../../outputs/n5_official_release_20261026/results/q1/paired_hypothesis_tests_adjusted.json) |
| Q1 | official_release_v1 | sensitivity | pre_event_gaussian_1d; crisis_joint_1d; crisis_joint_5d; deterministic convexity stress | [convexity_tests.json](../../outputs/n5_official_release_20261026/results/q1/convexity_tests.json) |
| Q2 | official_release_v1 | primary_counterfactual | 1458 cells; reference depth1 predictive_proxy and fixed (identical proxy0); 9 fixed CF; 6 shocks | [dense_cf_grid.csv](../../outputs/n5_official_release_20261026/results/q2/dense_cf_grid.csv) |
| Q2 | official_release_v1 | sensitivity_counterfactual | depth0.5/2 and full_sensitivity, not observed policy outcomes | [h2a_paired_contrasts.csv](../../outputs/n5_official_release_20261026/results/q2/h2a_paired_contrasts.csv) |
| Q3 | official_release_v1 | primary_observational_prediction | B1 training -> B2 held-out; three pair-relative oracle routes | [route_validation.json](../../outputs/n5_official_release_20261026/results/feedback/route_validation.json) |
| Q3 | official_release_v1 | model_output_under_predictive_fallback | 12 pure depeg cases predictive_proxy=0, not observed contagion outcomes | [direct_induced_summary.csv](../../outputs/n5_official_release_20261026/results/q3/direct_induced_summary.csv) |
| Q3 | official_release_v1 | sensitivity_counterfactual | 12 full-feedback cases; network null/regression;69 interventions;48 total Comet cases | [node_edge_interventions.csv](../../outputs/n5_official_release_20261026/results/q3/node_edge_interventions.csv) |
| ALL | n5_hypothesis_completion_20261026 | parent_analysis_run | 196 exact original artifacts, quoted through frozen release only | [analysis_manifest.json](../../outputs/n5_official_release_20261026/provenance/analysis_manifest.json) |
| ALL | n5_review_corrected_20261026 | historical_superseded | intermediate corrected analysis; not current six-hypothesis evidence | [manifest.json](../../outputs/n5_review_corrected_20261026/manifest.json) |
| ALL | n5_q1_q3_empirical_20261026 | historical_exploratory_superseded | 228 CF runs/4 policies;432-cell exploratory grid;earlier shock-only and post-cascade Q1 variants | [manifest.json](../../outputs/n5_q1_q3_empirical_20261026/manifest.json) |

“Kết quả chính” và “phản thực” là hai thuộc tính khác nhau: Q2 chính vẫn là phản thực có điều kiện, không trở thành tác động chính sách quan sát. Q1 chính cũng là mô phỏng dưới shock lấy mẫu từ dữ liệu, không phải kết quả thực nghiệm ngẫu nhiên. Full-feedback và các phân phối khủng hoảng không được thay thế thiết kế chính khi kết quả khác kỳ vọng.

## 3. Giải quyết bất nhất Q1: ES99 có thanh lý hay không?

**Có thanh lý nhiều vòng trong Q1 chính**, nhưng **oracle giữ ở mức sau shock ngoại sinh**, không nhận feedback từ DEX. Mỗi scenario sàng HF<1, gọi `mc_exact→execute`, cập nhật số lượng debt/collateral, bán cộng dồn và điều kiện lợi nhuận; vòng sau có thể tiếp tục xử lý cùng ví. Lặp thực thi không tự chứng minh ví khác bị lây lan qua giá. Code đúng giai đoạn: [mc_exact đã lưu](../../outputs/n5_official_release_20261026/results/source_versions/690ed8ad79941b83cdbee5ecfae13fe5052b0111253ce77d01980ab95bcf1c95.py) và [execute đã lưu](../../outputs/n5_official_release_20261026/results/source_versions/efa7e33f6a7570fdd49015c4a96840aacbd061cbfe88bf393eb41de0acb32840.py); xem [stage_source_versions.csv](stage_source_versions.csv).

Đặt `L_g(m)=mean_i[100000×BD_i,post(m)/E_i,0]` trên các ví thuộc nhóm ghép có equity dương. **ES99_g = trung bình 100 giá trị lớn nhất của 10.000 L_g(m)**, tính cả loss zero. Đây là ES của loss nhóm theo scenario, không phải trung bình ES từng ví, không dùng tie-inclusive `loss≥VaR` khi có nhiều zero. Δ=looping−non-looping. USD/100.000 equity không phải triệu USD hoặc BDR%. BDR kiểm định H1a là trung bình tỷ số bad debt/debt ban đầu theo ví; Q2 BDR là tỷ số tổng trên cohort debt ban đầu.

| Snapshot | Pairs | ES99_looping | ES99_non_looping | Difference | CI95 |
| --- | --- | --- | --- | --- | --- |
| A | 262 | 0.0000 | 953.9851 | -953.9851 | [-1140.4472; -774.9975] |
| B1 | 288 | 0.0000 | 0.0000 | 0.0000 | [0.0000; 0.0000] |
| B2 | 316 | 0.0000 | 2334.9889 | -2334.9889 | [-2506.4608; -2160.0152] |

Các bản shock-only, post-cascade theo strata, 19 stress tất định và MC ghép equity là những estimand khác nhau. Không lấy số từ bảng cũ thay số trên. Zero là kết quả có điều kiện trong mẫu/lưới được chọn, không chứng minh vị thế không có tail risk ngoài miền.

## 4. Giải quyết bất nhất Q2: 228 hay 1.458, và nợ xấu

228 = 3 snapshot ×19 shock ×4 quy tắc CF trong bundle cũ. Chính thức **1.458 = 3×6 shock×3 depth×9 CF×3 mode**. Do predictive_proxy=0, mode predictive trùng fixed: 1.458 là số ô xuất, không phải 1.458 thí nghiệm độc lập; tối đa 972 cấu hình thực thi khác mode trước các trùng khác. Bảng dưới dùng depth=1; CF100−CF50; bad debt USD sau thực thi, bình quân đồng trọng số theo N shock trong từng bản. **N=19 và N=6 khác tập shock, không so chênh lệch hai trung bình như hiệu ứng sửa code.** Bản cũ chỉ dùng giải thích nguồn số đã xuất, không dùng kết luận chính.

| Version | Snapshot | Mode | N | CF50 | CF100 | Delta |
| --- | --- | --- | --- | --- | --- | --- |
| historical_228_not_primary | A | historical_fixed_oracle | 19 | 5575244.161673 | 5575524.449772 | 280.288099 |
| official_1458 | A | fixed | 6 | 2894392.830230 | 2895736.825102 | 1343.994872 |
| official_1458 | A | predictive_proxy | 6 | 2894392.830230 | 2895736.825102 | 1343.994872 |
| official_1458 | A | full_sensitivity | 6 | 3936569.865731 | 3791990.270238 | -144579.595493 |
| historical_228_not_primary | B1 | historical_fixed_oracle | 19 | 18161347.499392 | 18162358.556770 | 1011.057379 |
| official_1458 | B1 | fixed | 6 | 14116121.445053 | 14120299.236593 | 4177.791540 |
| official_1458 | B1 | predictive_proxy | 6 | 14116121.445053 | 14120299.236593 | 4177.791540 |
| official_1458 | B1 | full_sensitivity | 6 | 19351602.731579 | 19512802.599403 | 161199.867823 |
| historical_228_not_primary | B2 | historical_fixed_oracle | 19 | 12910343.454621 | 12917923.831571 | 7580.376950 |
| official_1458 | B2 | fixed | 6 | 8938047.695401 | 8953621.762261 | 15574.066861 |
| official_1458 | B2 | predictive_proxy | 6 | 8938047.695401 | 8953621.762261 | 15574.066861 |
| official_1458 | B2 | full_sensitivity | 6 | 11343536.524021 | 11530855.775948 | 187319.251927 |

[Chi tiết shock và số đầy đủ](q2_bad_debt_version_comparison.csv). Trên toàn 54 cặp snapshot×shock×depth ở mỗi mode:

| feedback_mode | paired_cases | sale_and_newly_eligible_increase | bad_debt_increase | bad_debt_decrease | bad_debt_unchanged |
| --- | --- | --- | --- | --- | --- |
| fixed | 54 | 0 | 24 | 12 | 18 |
| full_sensitivity | 54 | 17 | 31 | 13 | 10 |
| predictive_proxy | 54 | 0 | 24 | 12 | 18 |

17/54 là **đồng thời tăng lượng bán vòng đầu và ví mới đủ điều kiện** trong full-feedback sensitivity; không phải 17 ví, không phải tỷ lệ nợ xấu, không phải số thực thi lần đầu vòng sau. Nợ xấu tăng/giảm dùng ngưỡng USD 1e−8 để tránh sai số float. Không cộng bad debt các shock phản thực thành tổn thất thị trường quan sát. Không trộn bán collateral với repayment hoặc số dư bad debt.

## 5. Giải quyết bất nhất Q3: ngoài mẫu và feedback

B1 train, B2 held-out; 296 dòng mỗi tập mỗi route, lag feature trước target. Tiêu chí dương: CI HAC dương + target đủ biến thiên vượt resolution + cải thiện RMSE so với zero-change. Kết quả route chính thức:

| Route | RMSE | Baseline | CI95 | Above_resolution | Proxy |
| --- | --- | --- | --- | --- | --- |
| wstETH->WETH | 2.43155414e-13 | 2.42687374e-13 | [-7.188661781170463e-13, 5.776333104955115e-12] | False | 0.0 |
| WETH->USDC | 0.00592337488 | 0.00589503819 | [-0.1293636644723959, 0.14690665049964824] | True | 0.0 |
| WBTC->WETH | 0.00610651464 | 0.00603055318 | [-0.31241439477735233, 0.31405112968028426] | True | 0.0 |

`feedback/validation.json` là fit sơ bộ dùng proxy dòng collateral seized; hệ số tổng `4,407007e−11`, RMSE `2,430709e−13` so baseline `2,426874e−13`. `feedback/route_validation.json` là kiểm tra bổ sung theo ba cặp DEX/oracle quan sát và là nguồn trích route ở bảng trên; hai fit khác feature/target nên không trộn hệ số/RMSE thành một ước lượng. Cả hai cùng không đạt tiêu chí cải thiện, proxy chọn 0. wstETH/WETH còn không vượt độ phân giải giả định. Không diễn giải fallback 0 thành bằng chứng tác động thật bằng 0.

Mô phỏng **full_sensitivity** đặt transmission=1, cap giảm giá 15% từ giá đã shock; không phải hệ số đã được kiểm định. So direct vs feedback cùng pure-depeg 4%,5%,7%,10% tại 3 snapshot. [direct_induced_summary.csv](../../outputs/n5_official_release_20261026/results/q3/direct_induced_summary.csv) có 24 dòng: 12 predictive (0 lượt),12 full (7 lượt); mẫu số riêng từng snapshot/shock, không lấy 12 làm số ví. Bảng lưu `induced_transition_rate` đúng từng ô; không gọi tổng lượt theo scenario là ví duy nhất. 48 trường hợp Comet (24 mỗi mode) cho 0/9 lượt mới đủ điều kiện là sensitivity một chiều trên account–market overlap, không xác nhận vòng kín lây lan thực tế. Không đồng nhất absorb với protocol net loss.

## 6. Dữ liệu và cấu hình cuối

| Snapshot | Block | UTC | Wallet_snapshot_rows | Pairs | Q3_role |
| --- | --- | --- | --- | --- | --- |
| A | 26179841 | 2026-10-12T23:59:59Z | 19664 | 262 | counterfactual_snapshot |
| B1 | 20458637 | 2024-08-04T23:59:59Z | 9675 | 288 | training |
| B2 | 23543615 | 2025-10-09T23:59:59Z | 20972 | 316 | held_out |

Tổng **50.311 dòng ví–snapshot**, không phải mặc định 50.311 người/ví duy nhất xuyên thời gian. Hash block và debt mẫu số: [snapshot_census.csv](snapshot_census.csv). Nhãn B1/B2 “out-of-sample” trong config là vai trò sự kiện; Q3 thực tế dùng B1 train và B2 test, không gọi B1 là test của chính fit đó.

- Nguồn on-chain: Aave V3 Ethereum positions/reserve config/oracle/liquidation logs tại snapshot; quote DEX bounded ở 3 route wstETH→WETH, WETH→USDC, WBTC→WETH; Compound Comet exposures đã đổi USD, reserve/pause/storefront trên mẫu overlap. [53 input đóng băng](frozen_inputs.csv) ghi đường dẫn nội bộ+gốc+SHA; code giai đoạn trong [stage_source_versions.csv](stage_source_versions.csv).
- Nguồn hiệu chỉnh giá: quote lịch sử DefiLlama đã lưu raw và timestamp; ETH/USD, STETH/ETH. 366 mức giá trước từng snapshot→365 lợi suất 1 ngày, không quote tương lai. Khủng hoảng 05–07/2022:90/92 ngày, thiếu 19/05 và22/07; không nội suy/không tính returns qua khoảng trống. Hiệu chỉnh khủng hoảng có87 returns 1 ngày; số5 ngày xem census. Chỉ đọc dữ liệu đóng băng trong bước này, không gọi API mới.
- Q1 seed42, t-copula df4 là chính; Gaussian và bootstrap chung ETH/depeg 1/5 ngày là sensitivity. 10.000 draw×4 phân phối×3 snapshot=120.000 **lượt scenario logic**; bootstrap có shock trùng được tái dùng, không phải120.000 cú sốc lịch sử độc lập. [simulation_census.csv](simulation_census.csv) ghi số shock khác nhau. 5 ngày là shock đầu-cuối, không path/rebalance/lãi suất.
- Ghép equity dương1:1, không lặp control, caliper log-equity0,1. Bootstrap1.000 cho matched/ES/curvature; Holm24 PBD/BDR. Convexity6 đường×31 mức depeg0–15% bước0,5 điểm %, ETH0/−20%:186 điểm stress, không phải draws MC. Matching equity không loại hết confounding; không match HF/leverage do có thể là mediator.
- Q2 9 CF cố định20%–100% bước10 điểm %, depth0,5/1/2;6 shock ETH−20/−40%, depeg−4/−5/−10%, kết hợp ETH−20%+depeg−5%. Ba mode fixed/predictive/full; CF là phản thực, không khẳng định protocol thực dùng mọi mức. Bootstrap chữ U2.000 block draws; điểm đảo chiều có CI; không ANOVA coi ô là độc lập.
- Engine: tối đa100 vòng; dừng khi hết thực thi có lợi nhuận được hỗ trợ, không có round-limit ở kết quả chốt; gas5 USD/giao dịch; fee route0,01%/0,05%/0,3%; giữ danh mục khi áp shock; debt/collateral cập nhật sau mỗi thanh lý; flow pool cộng dồn. Không tách lệnh; quote ngoài miền hoặc route thiếu ghi unknown; khoản không thực thi không tự coi là freeze. Float arithmetic, không phải emulator EVM đầy đủ.
- Q3: healthy HF0≥1,10 và sau shock≥1; collateral khác STETH/WSTETH≥10% và≥100 USD. Null999×3 loại×3 snapshot; regressionHC3; nguồn top1% +19 tập ghép ngẫu nhiên mỗi snapshot,3 route bị chặn mỗi snapshot→69 can thiệp, tại depeg10%; không phải69 nút duy nhất. Diagnostics power-law dùng bootstrap300 và không chứng minh power-law.
- Phiên bản phần mềm được đóng băng tại [software_versions.json](../../outputs/n5_official_release_20261026/results/software_versions.json); Python3.12.10, numpy2.5.4, pandas2.3.3, scipy1.18.1. Cần dùng file phiên bản/nguồn đã lưu khi tái lập, không tự thay bằng live workspace.

## 7. Manifest và kiểm thử

Audit cố định **40 manifest**: 2078 tham chiếu khớp; 239 hash khác và 48 thiếu, quy về96 chuỗi/95 tệp; các lượt lỗi lặp qua manifest không được đếm như tệp độc lập. Phân loại không có nghĩa mọi thay đổi nội dung cũ đều vô hại:28 tệp dữ liệu/đầu ra và10 hậu xử lý có thay đổi thật giữa phiên bản. Những bản thiếu byte không thể so/replay hoàn toàn. Chỉ xác nhận bản được chọn và53 input đóng băng sạch; không tuyên bố mọi manifest lịch sử sạch.

Log nghiệm thu bản chốt: [pytest_full_suite.txt](../../outputs/n5_official_release_20261026/provenance/pytest_full_suite.txt) — 198 passed, 12 subtests; 74 warnings (deprecation). Lần kiểm tra bổ sung: 198 passed, 74 warnings, 12 subtests passed in 25.66s. Xem [log](pytest_full_suite.txt).

Test cũ `test_empirical_q1_q3_bundle_manifest_and_kpis` gắn cứng228 và18 mapping. **Khôi phục từ Git và chạy riêng trên bytes hiện tại: PASS**; `verify_manifest(old)` trả 0 lỗi files. Audit lịch sử có 1 lỗi input mã nguồn ở bundle cũ, nhưng hàm `verify_manifest` chỉ kiểm `files`, không kiểm `inputs`; vì thế không dùng input drift để giải thích một assertion files thất bại. [Nguồn test cũ](obsolete_test_source.txt) và [kết quả tái kiểm](obsolete_test_diagnosis.json) ghi chứng cứ. **Không có traceback gốc: chưa quy được nguyên nhân lần thất bại ban đầu**, không kết luận lỗi mô hình hoặc manifest lỗi thời khi chưa đủ bằng chứng. Thay tiêu chí integration sang bản chốt là cập nhật phạm vi nghiệm thu và tăng kiểm input/manifest cha/đơn vị/threshold; không phải chứng minh đã sửa được nguyên nhân lịch sử. Không bỏ test hành vi mô hình hoặc dùng skip/xfail.

Manifest bản chốt đã được tạo sau khi cố định269 tệp/53 input; không tạo lại manifest đó chỉ để thêm tài liệu trích dẫn. **Phụ lục này có manifest riêng**, khóa SHA manifest cha, mọi tệp phụ lục và nguồn đọc. Kiểm tra bằng:

```powershell
.\.venv\Scripts\python.exe -B -X utf8 -m scripts.n5_finalize_release --stage verify
.\.venv\Scripts\python.exe -B -X utf8 -m scripts.n5_build_citation_pack --verify
```

## 8. Vấn đề đã xử lý và giới hạn còn lại

| ID | Status | Evidence | Resolution_or_limit | Source |
| --- | --- | --- | --- | --- |
| I01 | Đã xử lý phạm vi phát hành; lịch sử còn thiếu | 96 chuỗi đường dẫn / 95 tệp: 79 đổi phiên bản, 16 thiếu. 41 code/config, 28 data/output, 10 hậu xử lý. Khôi phục byte 98/141 phiên bản hash. | Không sửa manifest cũ để che thay đổi; chỉ chọn đầu ra và input khớp; các byte chưa tìm được không chứng nhận tái lập lịch sử. | [path_dispositions.csv](../../outputs/n5_official_release_20261026/provenance/path_dispositions.csv) |
| I02 | Đã đổi phạm vi nghiệm thu; lỗi lịch sử chưa quy nguyên nhân | Test cũ trỏ bundle228/18 mapping, hiện chạy riêng PASS; verify_manifest cũ có 0 lỗi FILE. Audit cũ có 1 lỗi INPUT mã nguồn; verify_manifest chỉ kiểm files. | Thay bằng test bản chốt+input+manifest cha+quy mô+KPI. Thiếu traceback lần thất bại ban đầu: không được tự kết luận do stale manifest hoặc lỗi tính toán; ghi không tái hiện được trên bytes hiện tại. | [obsolete_test_diagnosis.json](obsolete_test_diagnosis.json) |
| I03 | Đã làm rõ | Q1 chính có thanh lý nhiều vòng, giữ oracle; không có vòng phản hồi DEX→oracle. | Không trộn pre-shock loss, ES từng ví, ES nhóm, hoặc ES 19 stress với MC ghép cặp. | [hypothesis_evidence.csv](../../outputs/n5_official_release_20261026/results/q1/hypothesis_evidence.csv) |
| I04 | Đã làm rõ | Q2 dùng 1458 ô; 228 dòng và 432 ô là lịch sử. So sánh bad debt có bảng ghép CF, depth, mode, shock. | Trung bình 19 shock và 6 shock khác estimand; không gọi chênh lệch đó là tác động sửa lỗi đơn thuần. | [dense_cf_grid.csv](../../outputs/n5_official_release_20261026/results/q2/dense_cf_grid.csv) |
| I05 | Đã tách; giới hạn khoa học còn mở | 3 route không vượt baseline ngoài mẫu; proxy=0 là fallback. Full-feedback=1, cap15% là sensitivity. | Không nhận dạng nhân quả do biến thiên/độ phân giải, confounding và collateral seized chỉ proxy dòng bán. | [route_validation.json](../../outputs/n5_official_release_20261026/results/feedback/route_validation.json) |
| I06 | Đã đối chiếu định nghĩa; chỉ tiêu gốc còn thiếu | 18 mã mapping không đồng nghĩa 18 KPI nghiệm thu; M08/M09 khác đơn vị/estimand; M12 eligible khác executed; H3 HF0=1,10. | M14 freeze, M16 CPCI đầy đủ, M17 edge-share thực tế, M18 DebtRank gốc chưa được công bố làm primary. | [kpi_release_dictionary.csv](../../outputs/n5_official_release_20261026/metadata/kpi_release_dictionary.csv) |
| I07 | Chưa giải quyết từ dữ liệu hiện có | 3 snapshot; matching chỉ equity; 2 ngày thiếu khủng hoảng; bootstrap có điều kiện, không loại mọi bất định hiệu chỉnh. | Không gán ngẫu nhiên looping; 5 ngày là shock đầu-cuối, không path/rebalance; chưa suy rộng mọi khủng hoảng. | [REPORT.md](../../outputs/n5_official_release_20261026/results/REPORT.md) |
| I08 | Chưa giải quyết từ mô hình hiện có | 3 route DEX; thiếu/vượt quote là unknown. Comet một chiều trên ví overlap; không mô phỏng vòng kín toàn giao thức. | Không tự suy ra freeze, run/rút tiền thực tế, oracle Comet như Aave hoặc absorb bằng protocol net loss. | [compound_direct_induced_summary.csv](../../outputs/n5_official_release_20261026/results/q3/compound_direct_induced_summary.csv) |
| I09 | Chưa xác nhận giả thuyết tổng quát | Null centrality nhỏ/không bền giữa snapshot và regression; p null chưa hiệu chỉnh nhiều kiểm định; HC3 còn phụ thuộc mạng. | Không coi diagnostics là bằng chứng power-law hay tác động nhân quả; không thay kết luận để ủng hộ H3b. | [incremental_loss_regressions.json](../../outputs/n5_official_release_20261026/results/q3/incremental_loss_regressions.json) |

[Danh sách CSV](resolved_and_open_issues.csv). Hướng dẫn trích dẫn: ghi snapshot, phân phối/kỳ hạn, cohort/đơn vị, CF/depth/feedback, tên tệp và SHA từ evidence_index. Bằng chứng dự báo, kết quả mô phỏng và nhận dạng nhân quả là các mức khác nhau. Mọi giả thuyết giữ kết luận đúng bằng chứng; dữ liệu hiện có chưa đủ để nâng lên tuyên bố nhân quả hoặc suy rộng toàn thị trường.
