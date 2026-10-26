# N4/N5 — Gói giả định người dùng đã chốt

Ngày 26/10/2026. Cấu hình hiện hành cho gói mới: `config/n5_execution_scope_approved.json`. Các cấu hình và bundle trước được giữ làm lịch sử, không đổi nhãn thành kết quả của gói này.

## Kết quả đã thực hiện

- Bundle hoàn chỉnh: `outputs/n5_preparation/n5_approved_package_current_20261026_final/`.
- **570/570 lần chạy hoàn tất**, 57 tổ hợp cho mỗi phương án. Gate đầu vào A/B1/B2 đạt. Không phương án nào chạm giới hạn 100 vòng.
- Bản chính dừng sau tối đa **5 vòng**; phương án reset pool cần tối đa **90 vòng**. Reset làm thay đổi dư nợ được thanh lý ở **43/57 tổ hợp**, cho thấy giả định này ảnh hưởng đáng kể đến kết quả. Đây là thay đổi mô hình, không phải bằng chứng thị trường phục hồi thanh khoản.
- Có 48.001 dòng giao dịch mô hình trên tất cả các phương án/kịch bản; không coi đây là số giao dịch lịch sử hoặc cộng chúng thành một lần chạy thực tế. Các trường hợp ngoài phạm vi được lưu riêng, có thể lặp cùng ví qua nhiều vòng/phương án.
- Nhận **0/27 quote mới**, còn chờ 27. Trạng thái `approved_package_run_complete_quotes_pending`. Bản có đủ 27 quote chưa hoàn tất; runner đã sẵn sàng và kiểm tra bắt buộc sẽ dừng nếu còn thiếu.
- **20 kiểm thử đạt**. Đối chiếu sau chạy xác nhận: định danh 570 lần chạy không trùng; lượng bán của bản chính và các phương án tích lũy không tự reset xuyên vòng; mọi giao dịch có pool nằm trong miền quote; phí theo đúng từng pool hoặc 0 ở phương án kiểm tra; số dòng chưa hỗ trợ khớp audit; SHA-256 toàn bộ đầu vào/artifact khớp.
- Thư mục không có hậu tố `_final` là lần chạy chưa hoàn tất, đã đánh dấu `INCOMPLETE.md`; không sử dụng làm đầu ra của gói này.

Các bảng để mở trực tiếp:

- [Bản chính](../outputs/n5_preparation/n5_approved_package_current_20261026_final/primary_results.csv).
- [Độ nhạy so với bản chính](../outputs/n5_preparation/n5_approved_package_current_20261026_final/sensitivity_vs_primary.csv).
- [Trường hợp ngoài phạm vi](../outputs/n5_preparation/n5_approved_package_current_20261026_final/unsupported_candidates.csv).
- [Theo dõi 27 quote](../outputs/n5_preparation/n5_approved_package_current_20261026_final/planned_quote_inventory.csv).

## Bản chính

- Giữ A/B1/B2, ví có nợ tham chiếu >=100 USD và ba route/pool đã chọn: wstETH→WETH, WETH→USDC, WBTC→WETH. Không đưa thêm route hoặc quy mô vào miền chỉ vì xuất hiện trong danh sách yêu cầu.
- **Cộng dồn lượng bán xuyên suốt các vòng**, theo pool, trong cùng một lần chạy. Dừng đánh giá giao dịch khi vượt miền quote; không reset, không tự chia nhỏ để né giới hạn. Mỗi phương án/kịch bản bắt đầu từ trạng thái riêng.
- **Ưu tiên HF thấp nhất** tại đầu mỗi vòng; hòa HF thì theo địa chỉ ví. Oracle giữ cố định sau shock; không mô hình hóa lây lan từ DEX sang oracle.
- **Chọn cặp theo `bonus premium - execution slippage`**, cả hai tính theo tỷ lệ, trong tập cặp đã có route/quote và lợi nhuận USD sau gas/phí >0. Hòa biên thì chọn lợi nhuận USD cao hơn, sau đó dùng thứ tự token ổn định. `execution slippage` là chi phí bình quân tăng thêm trên đoạn lượng bán cộng dồn, không phải mức giảm giá cuối cùng.
- Nội suy tuyến tính là đường cong chính. Giới hạn vẫn lấy từ dữ liệu quan sát đã kiểm tra của đúng block/pool/chiều bán.
- Gas cơ sở **5 USD/lần** là giả định. Chính sách close factor vẫn là prototype nghiên cứu `baseline_aave`, chưa phải emulator hợp đồng đầy đủ.
- Tối đa 100 vòng; xuất cờ chạm giới hạn. Dừng vì không còn giao dịch hỗ trợ có lợi nhuận không chứng minh mọi khoản nợ đã thanh lý.

## Phí DEX đã đối chiếu trong dự án

N3 `fetch_dex_liquidity.py` dựng spot reference bằng quote nhỏ đã bao gồm phí. Multivolume đo impact tương đối với reference này: `1 - (amount_out / amount_in) / spot_reference`. Vì vậy impact tương đối không tự bao gồm phần chênh lệch của reference so với giá trước phí.

Metadata `fee_tier` của ba pool nhất quán qua các quote:

| Pool/hướng | fee_tier, phần triệu | Phí áp dụng |
|---|---:|---:|
| wstETH→WETH | 100 | 0,01% |
| WETH→USDC | 500 | 0,05% |
| WBTC→WETH | 3000 | 0,30% |

Gói mới dùng phí từng pool, không cộng thêm mức 0,05% chung của bản cũ. Giao dịch cùng token không có phí DEX. Đây là đối chiếu metadata/code N3 trong dự án, chưa truy vấn lại hợp đồng. Giá DEX trước phí được giả định ngang tỷ giá oracle sau shock; chênh lệch DEX/oracle vẫn chưa được hiệu chỉnh. Phương án phí bằng 0 là kiểm tra độ nhạy, không phải khẳng định giao dịch thực tế miễn phí.

## Các phương án đối chiếu

Mỗi snapshot/kịch bản chạy 10 phương án, chỉ đổi tham số được ghi trong bảng, còn lại giữ bản chính:

| Variant | Thay đổi so với bản chính |
|---|---|
| `primary` | Không đổi |
| `curve_power` | Power-law thay nội suy |
| `pool_reset` | Reset pool giữa vòng, chỉ là giả định kiểm tra |
| `gas_0` | Gas giả định 0 USD/lần |
| `gas_20` | Gas giả định 20 USD/lần |
| `gas_60` | Gas giả định 60 USD/lần |
| `random_42` | Thứ tự ví ngẫu nhiên, seed 42 |
| `random_43` | Thứ tự ví ngẫu nhiên, seed 43 |
| `random_44` | Thứ tự ví ngẫu nhiên, seed 44 |
| `fee_zero` | Phí DEX bằng 0 để kiểm tra độ nhạy |

Lịch hoán vị dựa trên cùng tập ví ban đầu, seed và số vòng; không tiêu thụ số ngẫu nhiên khác nhau do phương án này thực thi được nhiều ví hơn phương án kia. Không coi ba seed là phân phối xác suất đầy đủ hoặc khoảng tin cậy.

Tổng 57 tổ hợp snapshot/kịch bản × 10 phương án = 570 lần chạy. Không cộng các kết quả giữa kịch bản/phương án thành tổn thất thực tế.

## Phân loại và dữ liệu chờ

- `outside_selected_scope`: ngoài route đang chọn.
- `outside_quote_domain`: thiếu miền quote phù hợp, bao gồm tổng lượng bán xuyên vòng.
- Ví không có cặp hỗ trợ với lợi nhuận dương: chi phí không khả thi theo giả định hiện tại. Đây là thống kê riêng trong bảng rounds, không phải freeze lịch sử đã chứng minh.
- Một ví có thể có cặp thực thi được và cặp chưa hỗ trợ; số ví ở các nhóm có thể chồng lấp, không cộng thành coverage toàn mẫu.
- 27 quote mới vẫn chờ N3 thu thập. Yêu cầu dự kiến không được biến thành quan sát. Bản chạy với đủ 27 quote phải dùng kiểm tra bắt buộc `--require-complete-quotes`.

## Chạy và bàn giao

Runner gói đã chốt (thay cho runner cũ khi muốn áp dụng gói này):

```powershell
.\.venv\Scripts\python.exe -B -X utf8 -m scripts.n5_run_approved_package --output-dir outputs/n5_preparation/APPROVED_PACKAGE_NEW
```

Khi N3 có quote mới:

```powershell
.\.venv\Scripts\python.exe -B -X utf8 -m scripts.n5_run_approved_package --additional-quotes outputs/N3_NEW_QUOTES/quotes_27_verified.parquet --require-complete-quotes --output-dir outputs/n5_preparation/APPROVED_WITH27_NEW
```

Thay đường dẫn quote bằng file thực tế N3 cung cấp. Chọn thư mục đầu ra mới mỗi lần.

Các đầu ra: `primary_results.csv`, `sensitivity_vs_primary.csv`, `execution_rounds.parquet`, `execution_transactions.parquet`, `unsupported_attempts.parquet`, `unsupported_candidates.csv`, `planned_quote_inventory.csv`, `quote_domain_limits.parquet`, QA quote giữ lại, summary và manifest SHA-256.

Chưa công nhận đây là cascade toàn hệ thống hay bằng chứng freeze thực nghiệm: oracle cố định, phạm vi route hữu hạn, gas giả định và điều kiện thực thi hợp đồng chưa đầy đủ vẫn được ghi trong báo cáo.
