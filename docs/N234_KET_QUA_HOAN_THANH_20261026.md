# N2/N3/N4 — Kết quả hoàn thành trong phạm vi đã chốt

Đã hoàn thành các đầu việc dữ liệu và đồng bộ code phục vụ gói nghiên cứu giới hạn ba route của người dùng. Dữ liệu gốc và các bundle trước được giữ nguyên; nguồn hiện hành được ghi tại `config/n234_handoff_sources.json`.

## N2

- Đã quan sát trực tiếp Aave `getAssetPrice` ở **mọi block** B1/B2 cho toàn bộ token đang có vị thế có giá trị trong mô hình: 33 token ở B1, 55 token ở B2. Tổng **1.023.440 dòng / 21.476 block**.
- Đã truy vấn lại 155 giá và nguồn oracle tại A/B1/B2, khớp N2. Đã thu thập metadata 184 bộ feed–snapshot và log AnswerUpdated, truy vết dependency bằng các getter được hỗ trợ và kiểm tra cập nhật source/proxy. Native ratio và time-dependent caps không được giả định là Chainlink feed; đường giá Aave trực tiếp từng block là nguồn dùng tính HF.
- Cả 6 ví thiếu vị thế trong full B2 đều có **nợ ban đầu bằng 0**, có Borrow sau snapshot. Bốn ví trong primary peak thuộc nhóm này. Không tạo vị thế giả tại t0; khoản vay phát sinh sau snapshot được báo cáo riêng khỏi cohort ban đầu.
- Gate số dư/HF cả ba snapshot đạt theo policy người dùng đã chốt; không thay ngưỡng để đạt hit-rate.

## N3

- Đã truy vấn lại độc lập **111 quote và 24 anchor**, không có sai lệch đáng kể so với dữ liệu nguồn. Đủ **27/27 quote mới**.
- Ba quote WETH→USDC nhỏ có impact âm rất nhỏ do reference integer được giữ nguyên raw. Chỉ khi bật policy định danh rõ, mô hình dùng cận trên theo một raw unit của reference; không đổi dấu rồi coi là quote quan sát, không bỏ ba dòng này để báo thiếu dữ liệu.
- Có depeg Curve đúng B1/B2: 25/49 mốc giờ gồm mốc snapshot cho hai cửa sổ cấu hình, và **600 quan sát tại mọi block của hai peak**. Đây là ước lượng thị trường từ giao dịch rất nhỏ đã điều chỉnh phí, không thay thế oracle Aave.
- Đã quét lại độc lập **2.732 LiquidationCall** trong toàn cửa sổ. Các chunk block liên tục đều có phản hồi; khóa sự kiện khớp dữ liệu Etherscan cũ, không thiếu/thừa.
- Có **1.988 receipt giao dịch thành công** với gasUsed/effectiveGasPrice thực tế. Gas ETH tính một lần theo giao dịch; USD quy đổi theo oracle trước giao dịch. Chia đều chi phí cho các liquidation trong cùng tx chỉ là chẩn đoán, không phải đo PnL của liquidator.
- Đã lấy mới 366 mức giá thị trường ETH/USD và stETH/USD hợp lệ trước snapshot A; có 365 cặp lợi suất và 10.000 **đầu vào kịch bản** Student-t Copula, df=4, seed=42. Chưa gọi đây là 10.000 kết quả tổn thất Monte Carlo.

## N4

- Một cohort chung qua HF, Stress, Heatmap, Cliff, Reverse và loader dùng cho dashboard/API: **50.311 ví**, lần lượt A/B1/B2 = 19.664/9.675/20.972.
- Đã xuất 57 dòng scenario summary, 90 heatmap, 183 cliff và 9 reverse target. Mẫu số nợ cohort trước/sau shock và nợ protocol t0 được tách rõ; các alias protocol cũ chỉ giữ để tương thích API.
- Shock stETH/ETH chỉ áp dụng mặc định cho STETH/WSTETH trong config nghiên cứu chung; LST khác vẫn nhận shock ETH và override riêng. N4/N5 dùng chung policy này; hàm generic cũ giữ hành vi tương thích khi không truyền scope.
- Đã kiểm tra on-chain **155 cấu hình reserve và 307 dòng E-Mode membership** của 77 bộ category–snapshot: LT/LTV/bonus và cờ collateral/borrowable đều khớp. Đã lưu địa chỉ implementation theo snapshot và cách đọc legacy/bitmap.
- Đã xuất 5 tầng vốn tự có. Năm ví vốn tự có không dương được gắn cờ không đủ điều kiện chuẩn hóa loss, không chia cho 0 hoặc gán loss giả.
- CLI nghiên cứu xuất candidate vào thư mục mới có manifest; không trộn cohort đã lọc với marts cũ toàn mẫu. Thư mục dashboard hiện chỉ có khung; API/nguồn dữ liệu đã sẵn sàng để N6 nối giao diện.

## Tích hợp và kiểm định

- Đã chạy lại **570 lần** theo gói đã chốt với đủ 27 quote; không phương án nào chạm giới hạn 100 vòng. Bản chính cộng dồn lượng bán xuyên vòng; reset là sensitivity.
- Sửa lỗi biên khi sự kiện nằm ngay block đầu peak: mang giá của block trước vào cửa sổ, không dùng giá cuối chính block có liquidation để dự báo. Có test chống nhìn trước cho trường hợp này.
- Hit-rate dưới đây dùng ví có nợ tham chiếu t0 >=100 USD, HF dự báo <1,02, danh mục t0 giữ cố định và giá oracle ở block **trước** sự kiện. Đây là kiểm định hồi cứu có điều kiện, không phải bảo đảm dự báo tương lai hoặc precision trên toàn người vay.

| Snapshot | Ví hit / ví quan sát thuộc cohort | Wallet hit-rate | Debt-weighted hit-rate |
|---|---:|---:|---:|
| B1 | 467/467 | 100.00% | 100.00% |
| B2 | 406/430 | 94.42% | 90.62% |

Không nhân thêm depeg thị trường vào giá Aave đã quan sát để tránh áp dụng hai lần. Depeg được bàn giao riêng để phân tích và kiểm tra giả định.

**74 kiểm thử đạt**. Đã kiểm tra hash đầu vào/artifact, đủ quote, luồng bán cộng dồn không vượt miền và đối chiếu block/source. File raw RPC có thể lớn; root manifest dùng SHA-256 đọc theo khối và giữ checkpoint để chạy tiếp, không lưu URL hoặc khóa API.

## Phần không thể kết luận thành kết quả nghiên cứu chính thức

- Receipt của giao dịch đã thực thi không chứng minh cơ hội thanh lý không thực thi là do gas. Dữ liệu gas đã đủ phạm vi thực thi, nhưng kết luận freeze nhân quả cần suy luận riêng.
- Close factor và execution vẫn theo research policy đã chọn; không khẳng định emulator hợp đồng đầy đủ, bao gồm mọi phí protocol hoặc điều kiện phiên bản. 8 quy mô rất lớn và 461 hướng chưa có route không thuộc bản chạy đã chốt.
- Tổn thất Monte Carlo thực nghiệm, ES99, DebtRank và thực thi Comet/Q3 đầy đủ là bước nghiên cứu N5 tiếp theo; không còn là lý do thiếu các đầu vào N2/N3/N4 nêu trên.

## File cần dùng

- [Checklist theo người phụ trách](../outputs/n5_completion_20261026/completion_checklist.csv).
- [Tổng hợp máy đọc và hit-rate](../outputs/n5_completion_20261026/completion_summary.json).
- [Hit-rate theo cửa sổ và phạm vi mẫu](../outputs/n5_completion_20261026/historical_hit_rates.csv).
- [Các ví chưa dự báo trúng trong primary cohort](../outputs/n5_completion_20261026/primary_model_misses.csv) — giữ nguyên để rà soát, không gán là freeze.
- [Kết quả bản chính đủ 27 quote](../outputs/n5_completion_20261026/approved_package_with27/primary_results.csv).
- [Bảng Reverse](../outputs/n5_completion_20261026/n4_research/reverse_stress.parquet).
- [Sáu ví B2 và Borrow sau snapshot](../outputs/n5_completion_20261026/extended_proofs/missing_wallet_state_and_borrows.csv).
- [Nguồn dữ liệu hiện hành](../config/n234_handoff_sources.json).

## Chạy lại

```powershell
python -B -m scripts.n234_collect_history --output-dir outputs/n5_completion_20261026/history --stages oracle --full-oracle
python -B -m src.models.n4_research --output-dir outputs/N4_NEW
python -B -m scripts.n5_run_approved_package --additional-quotes outputs/n3_priority2_quote_grid_v2_with_27/n3_targeted_27_rpc_verified_only.parquet --require-complete-quotes --output-dir outputs/N5_APPROVED_NEW
python -B -m scripts.n234_validate_handoff --output-dir outputs/HISTORICAL_VALIDATION_NEW
```

Thu thập mạng cần RPC archive đã cấu hình; các bước mô hình và kiểm định dùng file đã thu thập. Dùng thư mục mới cho mỗi bundle kiểm định/mô hình.

Code đối chiếu dependency dựa trên [adapter chính thức Aave](https://github.com/aave-dao/aave-price-feeds/blob/main/src/contracts/PriceCapAdapterBase.sol); dữ liệu thị trường lấy qua API lịch sử theo [tài liệu DefiLlama](https://github.com/DefiLlama/api-docs/blob/main/llms-free.txt). Đây là nguồn kỹ thuật; các số liệu trong báo cáo được xác nhận từ artifact và RPC/API thực tế của lần chạy này.
