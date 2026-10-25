# Rà soát N5 theo dữ liệu hiện có — 25/10/2026

**Sau triển khai:** xem [kết quả mới](KET_QUA_N5_AB1B2_20261025.md). Nội dung dưới đây
là đánh giá ban đầu trước khi bổ sung runner, diagnostics và Q1/Q3 fixture.

Đối chiếu phân công trong tài liệu `Nhóm 8 - chủ đề 7 - dl_12h 26_10.docx` với mã nguồn, bảng dữ liệu và kết quả kiểm tra tại working tree hiện tại. Các yêu cầu trong tài liệu được dùng làm tiêu chí đánh giá, không phải lệnh thực thi. Không sửa mô hình, dữ liệu nguồn hoặc gọi RPC.

## Kết luận

N5 đã có lõi Cascade, khung benchmark/sensitivity, adapter, kiểm định và các bundle chuẩn bị. Chưa hoàn thành bộ kết quả nghiên cứu chính thức. Có thể bắt đầu kiểm định và tích hợp A/B1/B2 ngay; không còn phải chờ N2 cung cấp B1/B2 như báo cáo ngày 23/10. Compound A đã đủ phạm vi truy vấn đã chọn; chỉ B1/B2 còn thiếu shard 001.

## Kiểm tra thực hiện lại

- Bộ test N5, Cascade, HF, Stress, Cliff và hợp đồng N4: **106 passed, 12 subtests passed**, không có xfail trong bộ chạy. Đây không phải toàn bộ test repository.
- `audit_snapshots`: cả A/B1/B2 passed_structure trên đủ 8 bảng Aave; không có nhãn B cũ trong các bảng này. Kiểm tra tính nhất quán với config/metadata, không xác minh lại blockchain.
- Adapter N5 được dựng trên dữ liệu thật của cả ba snapshot. Depth 1 triệu USD chỉ dùng để dựng đối tượng phục vụ audit HF/số dư; không chạy mô phỏng hoặc hiệu chỉnh price impact bằng giá này.

| Phép kiểm | A | B1 | B2 |
|---|---:|---:|---:|
| Block | 26179841 | 20458637 | 23543615 |
| Ví còn nợ | 28.493 | 12.047 | 27.985 |
| Trung vị sai số HF adapter–Aave | 7,27e-12 | 1,05e-11 | 5,55e-11 |
| Khác phía HF=1 giữa N4–adapter | 0 | 0 | 0 |
| N4–adapter lệch HF ≥0,005 | 1 | 0 | 0 |
| Ví lệch số dư nợ >1% theo validator N5 | 220 | 17 | 537 |
| Ví lệch số dư collateral >1% theo validator N5 | 32 | 5 | 13 |
| Tổng nợ so reference reserve | passed | passed | passed |
| Ví có bước nhảy HF ≥0,005 khi shock −1e-12 | 5 | 0 | 3 |

Gate HF trung vị đạt ở cả ba snapshot. Gate số dư từng ví hiện failed; phải điều tra sai số tuyệt đối, nợ bụi, làm tròn và phạm vi collateral trước khi nghiệm thu. Không tự coi mọi sai lệch là dust. Số liệu validator N5 có thể khác thống kê N2 vì khác cách tính/phạm vi collateral.

Zero-shock đạt trên **68.525** dòng ví–snapshot. Kiểm tra HF tái dựng không có dòng sai baseline ≥0,005, không có dòng đổi phía HF=1, nhưng tiny-shock còn **8** dòng không đạt. Vì vậy các lỗi 60/58 ví và 6 expected failures nêu trong báo cáo cũ không mô tả hiện trạng nữa; vẫn chưa đủ căn cứ nói mô hình hoàn toàn đạt.

## Đối chiếu sáu đầu việc được giao cho N5

| Đầu việc | Đã có | Phần còn lại |
|---|---|---|
| 1. Kiểm định N2–N4 | Adapter, kiểm schema/snapshot, HF, số dư, đối chiếu tổng nợ, cửa sổ lịch sử; có báo cáo cũ | Cập nhật báo cáo A/B1/B2, xử lý các gate chưa đạt, chốt phạm vi collateral/TVL và holdout |
| 2. Xây Cascade | `src/models/cascade.py`: nhiều vòng, 4 Close Factor, chọn cặp collateral/debt theo lợi nhuận, gas/slippage, cập nhật số dư/giá | Hiệu chỉnh price impact và xác nhận quy tắc thực thi theo phiên bản giao thức; chưa phải mô phỏng đầy đủ mọi ràng buộc Aave |
| 3. Kết quả theo vòng, Q3 | Repay, seized, cumulative, bad debt, đường giá, giao dịch audit; graph exposure và Compound adapter/ledger | Chưa có động cơ phản ứng ví liên giao thức, mô phỏng Comet và attribution truyền dẫn Q3 hoàn chỉnh |
| 4. Sensitivity | Hàm chạy CF × impact × sell fraction × ETH × depeg × depth; có demo | Chưa có bộ sensitivity nghiên cứu thật được nghiệm thu |
| 5. Benchmark và kiểm định | Benchmark tĩnh một vòng, test phần mềm, phân cửa sổ sự kiện | Chưa có kết quả hit-rate ngoài mẫu đã kiểm chứng, chưa nghiệm thu kênh Compound |
| 6. KPI và đóng gói | Mapping 18 KPI, validator, manifest hash cho bundle thử | Chưa đủ KPI; thiếu bộ Cascade/sensitivity/manifest chính thức trong `data/marts` |

Demo và các bundle trong `outputs/n5_preparation` là bằng chứng triển khai, không phải kết quả nghiên cứu chính thức. Mapping file/cột đạt không đồng nghĩa KPI đã được triển khai hoặc nghiệm thu.

## Những việc N5 làm ngay được

1. Lập lại báo cáo validation trên A/B1/B2 hiện tại; dùng các số ở trên thay cho kết luận thiếu B1/B2 trong báo cáo cũ.
2. Tách 8 ví tiny-shock và các ví sai số số dư để điều tra cùng N4/N2. Chốt chính sách dust với N1, giữ dữ liệu gốc và báo cáo cả trước/sau lọc.
3. Dựng lại baseline KPI, nhóm Looping/Non-looping và graph A/B1/B2 trong thư mục candidate, kiểm bảo toàn exposure theo token/block.
4. Chuẩn bị kiểm định lịch sử B1: đã có state trước sự kiện, sự kiện quan sát và dữ liệu giá. Ghép ví, block, thời gian, chạy dự báo theo đường giá đã chốt rồi tính hit-rate. Việc triển khai kiểm định không cần đợi Compound hoàn tất.
5. Tích hợp Compound A và phần đã có của B1/B2 để phân tích exposure/bridge. Giữ unknown cho truy vấn thiếu; không dùng coverage partial làm kết luận toàn mẫu.
6. Hiệu chỉnh hàm price impact từ depth có sẵn, chốt giả định/phạm vi token. Sau khi qua các gate mới chạy bộ mô phỏng nghiên cứu; có thể chuẩn bị cấu hình và runner ngay.
7. Viết phần còn thiếu của Q1/Q3 và chuẩn hóa giao diện cho N6; có thể phát triển bằng fixture trong khi chờ quyết định nghiên cứu.

## Điều kiện còn thiếu và giới hạn thực tế

- **Gate dữ liệu/mô hình:** số dư từng ví chưa đạt validator; tiny-shock còn 8 dòng; đối chiếu tổng nợ đạt không thay thế gate collateral/TVL. Mẫu chỉ gồm người vay nên không so tổng collateral của mẫu trực tiếp với TVL toàn reserve gồm người chỉ gửi tiền.
- **Thanh khoản:** `data/staging/n3_dex_liquidity_A_B1_B2.parquet` có 12 dòng pool–hướng tại đúng block theo QA N3. Mapper hiện nối được WETH, WBTC, wstETH ở mỗi snapshot; stETH không có trong danh sách asset của portfolio hiện tại. Còn 62/30/52 asset A/B1/B2 không có depth theo mapper. Đây là thống kê asset, không phải tỷ trọng exposure thiếu. Chưa có lambda/gamma hiệu chỉnh; không được điền depth giả cho toàn bộ token.
- **Holdout:** nguồn lịch sử hiện cho B1 1.830 sự kiện, 1.152 ví, 177 ví trong giờ đầu sau snapshot; B2 902 sự kiện, 623 ví, 0 ví trong giờ đầu sau snapshot. Độ đầy đủ nguồn chưa được xác minh độc lập. B2 dài khoảng 48 giờ; cần phân biệt giờ sau snapshot với giờ đầu của đợt sập. Không tính hit-rate trên mẫu số 0 hoặc tự đổi cửa sổ.
- **Compound:** bundle mới `outputs/compound_consolidated_A_B1_B2_20261025` có 2.855 positions, 1.862 accounts và 1.711 overlap rows. A: 56.986/56.986 truy vấn; B1: 20.108/24.094; B2: 47.086/55.970. Thiếu **12.870** truy vấn thuộc shard 001 (3.986 B1, 8.884 B2). Coverage chỉ áp dụng tập ví vay Aave trên hai market Comet USDC/WETH.
- **Q3:** có dữ liệu exposure và ledger delta tường minh, chưa có quy tắc phản ứng rút/bù collateral đã chốt hay động cơ Comet causal. Hoàn tất crawler không tự hoàn thành mô phỏng lây lan.
- **KPI:** mapping hiện phân 4 baseline_candidate, 5 synthetic_only, 3 definition_review, 2 tracking_only, 4 not_implemented. Còn thiếu Monte Carlo/xác suất bad debt, tổn thất chuẩn hóa equity, ES99 và DebtRank hoàn chỉnh. Cần thống nhất mẫu số BDR, eligible/executed, ngưỡng healthy 1,05/1,10 và cách xác định chuyển trạng thái.
- **Đường chạy đóng gói:** `scripts/n5_prepare_handoff.py` mặc định đọc DEX cũ, Compound partial cũ, tên file `_partial.parquet`, SQLite và concentration_2026. Không chỉ đổi `--compound-dir` sang bundle mới vì định dạng/tên file khác; cần sửa adapter/runner trước. Mapping KPI còn đường dẫn A|B cần cập nhật A/B1/B2.
- **Canonical:** năm bảng `dim_asset_config`, `fact_wallet_positions`, `fact_wallet_summary`, `fact_historical_liquidations`, `graph_network_edges` trong `data/processed` hiện **0 dòng**. `data/marts` chưa có `cascade_rounds.parquet`, `cascade_summary.parquet`, `sensitivity.parquet`, `manifest.json`. Dữ liệu staging và candidate vẫn dùng được để tiếp tục làm việc.

## Thứ tự ưu tiên

Ưu tiên cập nhật validation và xử lý tiny-shock/dust; đồng thời sửa đường tích hợp A/B1/B2 và bộ Compound mới. Tiếp theo chốt liquidity/holdout, chạy kiểm định B1 và benchmark/sensitivity trên dữ liệu thật sau khi đạt các gate. Q1/Q3 còn cần bổ sung mã và giả định. Chỉ phát hành manifest nghiệm thu và bàn giao kết quả chính thức cho N1/N6 khi các điều kiện tương ứng được đáp ứng.

## Lệnh kiểm thử đã chạy

```powershell
.\.venv\Scripts\python.exe -B -X utf8 -m pytest tests/test_n5_integration.py tests/test_n5_preparation.py tests/test_n5_readiness.py tests/test_n5_snapshots.py tests/test_cascade.py tests/test_n4_pending_contracts.py tests/test_health_factor.py tests/test_stress.py tests/test_cliff_reverse.py -q -p no:cacheprovider
```

Các đối chiếu dữ liệu được chạy chỉ đọc bằng các hàm `audit_snapshots`, `prepare_portfolio`, `validate_inputs`, `diagnose_n4`, `reconcile_debt`, `map_dex_depth`, `select_event_windows`, `run_stress_test`, `validate_zero_shock`, `check_reconstructed_hf`. Không chạy lại crawler hoặc toàn bộ sensitivity nghiên cứu.
