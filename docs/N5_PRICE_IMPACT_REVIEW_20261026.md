# N5 — Lượng bán, kiểm tra quote giữ lại và đối chiếu chi phí thực thi

Ngày 26/10/2026. Đã thực hiện bốn bước: xác định nhu cầu bán từ vị thế thật; so sánh power-law và nội suy; kiểm tra bằng quote giữ lại; đối chiếu lượng nợ thanh lý, lợi nhuận và số vòng. Đây là chẩn đoán phản thực với oracle giữ cố định sau cú shock, chưa phải kết quả cascade nghiên cứu đã nghiệm thu.

## Dữ liệu và phạm vi

- A/B1/B2 tại đúng block trong `config/snapshots.json`.
- Chạy lại gate tổng dư nợ, số dư ví và median sai số HF theo `config/n5_tolerances.json`: cả ba snapshot đạt. Chỉ giữ ví có nợ tham chiếu ban đầu >=100 USD: A 19.664, B1 9.675, B2 20.972 ví.
- Kiểm tra lại quote raw/block/hash bằng `calibrate_quote_grid`: 84 điểm grid, cộng 24 anchor trên 12 hướng-snapshot. Không gọi RPC.
- Chạy 19 kịch bản mỗi snapshot, ba phương án mỗi tổ hợp: no-impact để khảo sát nhu cầu bán, power-law, nội suy tuyến tính. Tổng 171 lần chạy, cùng cohort và tham số giữa hai đường cong.
- Không thay dữ liệu N2/N3, không ghi đè marts hay mô hình cascade dùng chung.

## 1. Lượng bán thực sự cần kiểm tra

`pool_round_sale_demand.parquet` ghi lượng bán cộng dồn từng hướng/pool trong mỗi vòng, không chỉ giao dịch đơn lẻ. `sale_demand_by_direction.csv` tổng hợp mức lớn nhất qua các kịch bản.

Một số mức bán cộng dồn lớn nhất của baseline không trượt giá, triệu USD tại giá snapshot:

| Hướng | A | B1 | B2 |
|---|---:|---:|---:|
| wstETH → WETH | 310,46 | 406,45 | 1.668,84 |
| WETH → USDC | 387,51 | 481,39 | 775,53 |
| WBTC → WETH | 1,53 | 30,76 | 2,26 |

Các mức này là nhu cầu ứng viên dưới chính sách nghiên cứu không trượt giá, không phải khối lượng chắc chắn có thể thực thi, cũng không phải cận trên toán học của mọi hành vi thị trường. Không tự ngoại suy quote lên các quy mô rất lớn này. N5 cần giới hạn phạm vi hoặc bổ sung dữ liệu thanh khoản phù hợp trước khi công bố mô phỏng toàn phần.

Grid quote đo lượng token bán quy đổi theo oracle tại snapshot. Khi shock, runner chuyển lượng USD bán về cùng tọa độ snapshot trước khi áp dụng đường cong. Giả định độ sâu theo lượng token không thay đổi bởi shock được ghi rõ, chưa được kiểm chứng trên trạng thái pool sau shock.

## 2. So sánh đường cong và kiểm tra quote giữ lại

- Mỗi fold bỏ một nhóm quy mô, fit lại power-law và dựng nội suy chỉ từ phần còn lại.
- Hai anchor rất gần nhau quanh ngưỡng 2% được bỏ cùng fold, tránh dùng một anchor gần như trùng để dự báo anchor kia.
- Bỏ điểm thấp nhất hoặc nhóm anchor cao nhất làm quy mô kiểm tra nằm ngoài miền train: ghi `outside_training_domain`, không ngoại suy và không tính thành lỗi bằng 0.
- Có 72 dự báo kiểm tra được cho mỗi phương pháp; 72 dòng khác trên cả hai phương pháp không kiểm tra được do nằm ngoài miền train.
- MAE trên các điểm nội bộ giữ lại: **power-law 0,002990 điểm phần trăm**, **nội suy 0,005078 điểm phần trăm**.
- Trong 12 hướng-snapshot, power-law có MAE thấp hơn ở 9, nội suy thấp hơn ở 3: WETH→USDC tại A/B1, WBTC→WETH tại B2.

Đây chưa phải nghiệm thu toàn đường cong. Power-law vẫn có residual lớn tại anchor cao, ví dụ wstETH→WETH B1 khoảng 1,49 điểm phần trăm. Các điểm rìa chưa có kiểm tra độc lập và khoảng 250.000 USD đến anchor còn thưa. Không chọn một phương pháp toàn cục chỉ vì MAE trung bình thấp hơn.

`interior_method_candidates.csv` ghi lựa chọn ứng viên theo MAE nội bộ, kèm giới hạn phạm vi. Không tự thay cấu hình chính thức.

## 3. Ảnh hưởng lên thanh lý và số vòng

So sánh nội suy với power-law trên 57 tổ hợp:

| Chỉ số | Số tổ hợp thay đổi |
|---|---:|
| Dư nợ được thanh lý, chênh >0,01 USD | 36/57 |
| Lợi nhuận mô hình, chênh >0,01 USD | 47/57 |
| Số vòng | 14/57 |

Ví dụ B2/depeg_7: power-law cho nợ được thanh lý khoảng 136,75 triệu USD, nội suy 65,65 triệu USD; chênh khoảng **71,10 triệu USD**. Lợi nhuận mô hình lần lượt khoảng 552.113 và 352.619 USD. Số vòng 15 và 16. Đây là độ nhạy đáng kể của mô hình bị giới hạn về route/coverage, không phải bằng chứng một phương pháp đúng hơn.

Có 11 tổ hợp mà cả hai phương pháp chạm giới hạn 30 vòng; không kết luận đã hội tụ ở các trường hợp đó. Cả 14 tổ hợp khác số vòng đều không chạm giới hạn này.

### Giả định thực thi

- Chính sách `baseline_aave` nghiên cứu; chưa mô phỏng đầy đủ mọi quy tắc hợp đồng theo phiên bản, phí giao thức, điều kiện dust và hạn mức.
- Khí 5 USD/lần, phí giao dịch bổ sung 0,05% là giả định cố định, không phải gas/receipt quan sát. Cần xác nhận cách tính phí với định nghĩa spot quote N3 trước nghiệm thu.
- Chỉ dùng route trực tiếp đúng token thế chấp → token nợ; không giả định route nhiều chặng. Cặp cùng token không cần quote DEX.
- Giá oracle sau shock giữ cố định; không đưa price impact DEX vào HF. Vì vậy đây là đối chiếu thực thi nhiều vòng; chưa kiểm định lây lan qua phản hồi oracle hoặc Q3 Comet.
- Lượng bán tích lũy trong từng vòng, pool reset giữa các vòng; thứ tự ví theo địa chỉ. Không dùng kết quả này chứng minh phục hồi thanh khoản hay hành vi liquidator thực tế.
- Quote đo trượt giá trung bình. Chi phí tích lũy là `C(v)=v*impact(v)`; giao dịch tiếp theo chịu `(C(v+s)-C(v))/s`. Không nhầm trượt giá trung bình với mức giảm giá cuối cùng.
- Thiếu route, dưới quote nhỏ nhất, vượt quote lớn nhất hoặc chi phí tăng không hợp lệ được ghi unknown; không xem là freeze đã chứng minh. Các đường cong có thể cho phép giao dịch khác nhau nhưng cùng áp dụng miền dữ liệu quan sát.

## 4. Yêu cầu bổ sung đã thu hẹp

File `n3_existing_routes_only.csv` có **35 dòng ứng viên** trên các hướng có dữ liệu: 18 điểm trong khoảng thưa, 9 điểm dưới mức nhỏ nhất, 8 điểm vượt mức lớn nhất. Đây là danh sách để N5 ưu tiên/chốt phạm vi, không phải yêu cầu N3 lập tức chạy cả 35 truy vấn.

Ưu tiên xem các khoảng lớn của wstETH→WETH từ 250.000 USD đến anchor. Điểm giữa theo thang log gợi ý: A khoảng 1,172 triệu; B1 0,819 triệu; B2 2,508 triệu USD. Sau khi có quote có thể tiếp tục chia nhỏ khoảng nếu kết quả thay đổi đáng kể. Giữ nguyên đúng snapshot/block/pool/chiều bán trong CSV.

Quy tắc khoảng thưa (`quy mô trên/quy mô dưới >4`) chỉ là tiêu chí tìm điểm cần xem, không phải ngưỡng nghiệm thu. Đề xuất quy mô vượt miền ở các kịch bản cực đoan cần quyết định phạm vi trước; không yêu cầu quote hàng trăm triệu USD một cách máy móc.

`routing_decisions_before_new_quotes.csv` có **461 hướng-snapshot** chưa có route trực tiếp trong bộ bốn hướng hiện tại. N4/N5 phải xét tính khả thi, route nhiều chặng và mức trọng yếu trước khi nhờ N3 bổ sung; không giao N3 thu thập cả danh sách. Nhu cầu ứng viên này đến từ baseline chọn cặp lợi nhuận cao nhất; không bao quát mọi cặp có thể xuất hiện khi thay đổi hành vi.

## File và cách tái lập

- Bundle máy đọc: `outputs/n5_preparation/n5_price_impact_review_20261026/`.
- CSV dễ mở: `outputs/n5_preparation/n5_price_impact_readable_20261026_final/`.
- Cả hai có manifest SHA-256; dữ liệu nguồn không đổi trong lúc chạy.

```powershell
.\.venv\Scripts\python.exe -B -X utf8 -m scripts.n5_review_price_impact --output-dir outputs/n5_preparation/PRICE_IMPACT_NEW
.\.venv\Scripts\python.exe -B -X utf8 -m scripts.n5_summarize_price_impact --review-dir outputs/n5_preparation/PRICE_IMPACT_NEW --output-dir outputs/n5_preparation/PRICE_IMPACT_READABLE_NEW
```

Kiểm tra: 11 test đạt, gồm bảo toàn chi phí cộng dồn, từ chối ngoại suy, giữ hai anchor gần nhau trong cùng fold, không sửa đầu vào, phân biệt thiếu route với freeze và quy đổi đúng quy mô khi shock.
