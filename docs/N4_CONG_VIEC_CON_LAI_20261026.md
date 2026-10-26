# N4 — việc còn lại và phụ thuộc, cập nhật 26/10/2026

**Bản rà soát lịch sử.** Các đầu việc dữ liệu và đồng bộ trong phạm vi đã chốt đã được xử lý; xem [kết quả N2/N3/N4 hiện hành](N234_KET_QUA_HOAN_THANH_20261026.md) và catalog `config/n234_handoff_sources.json`. Các mục Monte Carlo loss/ES99/DebtRank/Comet đầy đủ là bước nghiên cứu N5 tiếp theo, không phải tiếp tục chờ dữ liệu N2/N3 theo bảng cũ bên dưới.

Đã đối chiếu `health_factor.py`, `stress_test.py`, `cliff_reverse.py`, hai cấu hình N5 đã chốt, marts hiện có và kết quả kiểm định mới. Không sửa mã mô hình hoặc chạy ghi đè marts trong lần tổng hợp này.

## Phần đã có, không giao làm lại

- Có HF, Stress, Heatmap, Cliff và hàm Reverse Stress.
- Có đủ vị thế/cấu hình/giá snapshot A/B1/B2; Compound shard 001 cũng đã đủ phạm vi truy vấn.
- Đã cấu hình nợ >=100 USD, dung sai số dư <=100 USD/ví, tổng nợ t0 lệch <1%, median HF error <0,005 trong `config/n5_tolerances.json`.
- Input gates N4/N5 đạt ở A/B1/B2. Đã chạy 19 scenario/snapshot sau lọc, tại `outputs/n5_preparation/n5_tolerances_stress_20261025/`.
- Tám cảnh báo tiny-shock đã được giải thích bằng biên biến động số học; không yêu cầu N4 sửa HF chỉ để xóa các cảnh báo này.
- B1 đã chốt block 20458637. Primary là 300 block nhiều LiquidationCall nhất; oracle ETH giảm 5% là secondary. Không cần hỏi lại các quyết định này.

## N4 tự làm ngay được

| Ưu tiên | Việc | Bằng chứng hiện tại / cần chỉnh | Đầu ra cần bàn giao |
|---|---|---|---|
| 1 | Đồng bộ cohort nghiên cứu | `stress_test.main()` đã lọc và gate. `cliff_reverse.main()` vẫn gọi loader lấy đủ ví; public Cliff/Reverse chưa nhận policy như Stress/Heatmap | Một cửa vào nghiên cứu dùng chung policy/cohort cho Stress, Heatmap, Cliff, Reverse và đường gọi dashboard; hàm toán tổng quát vẫn có thể hỗ trợ fixture |
| 2 | Thống nhất phạm vi depeg với N5 | N4 generic `depeg_shock=-.05` hiện làm wstETH/weETH/cbETH/rETH cùng giảm 5%; N5 mặc định chỉ STETH/WSTETH nhận stETH depeg | Quy tắc token-specific rõ ràng: stETH/ETH khác shock chung mọi LST; test ETH × depeg nhân một lần và override từng token |
| 3 | Ghi kết quả Reverse Stress | Có `run_reverse_stress`, nhưng CLI `cliff_reverse.main()` chỉ ghi `cliff_curve.parquet`; hiện không có file reverse trong `data/marts` | Bảng/JSON reverse theo snapshot, target, shock, grid và cờ “không đạt target trong grid”; API bàn giao N6 |
| 4 | Đồng bộ marts và cách công bố phạm vi | `borrower_risk` hiện vẫn đủ 28.493/12.047/27.985 ví A/B1/B2, còn bundle đã lọc là 19.664/9.675/20.972. Không phải mọi bảng bắt buộc bị xóa dust, nhưng phải phân biệt raw/full và simulation cohort | Candidate risk/overview/stress/heatmap/cliff/reverse có cohort/version rõ; kiểm tra rồi thống nhất phát hành cùng N5/N6. Không chép đè file rời làm trộn mẫu |
| 5 | Làm rõ mẫu số | Cliff đang gọi tổng nợ của đầu vào là `total_protocol_debt_usd`; khi đầu vào đã lọc đây là tổng nợ sample, không phải toàn reserve | Tách tổng nợ protocol t0, tổng nợ cohort t0 và tổng nợ sau shock; mọi tỷ lệ/target phần trăm ghi rõ mẫu số |
| 6 | Rà tham số và phạm vi mô hình | Có reserve/membership để rà LT, E-Mode permission, bonus/premium/bps, giá, cờ collateral. Isolation/siloed không được mặc định coi là đã mô phỏng đầy đủ | Bảng đối chiếu theo snapshot và ghi rõ phần là research policy, phần khớp protocol, phần chưa hỗ trợ; ghi lại ngoại lệ cần N2 giải thích |
| 7 | Cập nhật tài liệu và test | Hướng dẫn N4 còn ghi cố định “83 passed”; data contract còn nói A/B. Các test hiện tại không chứng minh cohort giữa mọi entry point nhất quán | Hướng dẫn A/B1/B2, policy, schema/hàm, run ID/model version; test cohort ở API/CLI và depeg giữa N4/N5 |

Không cần đợi dữ liệu mới để viết các chức năng, adapter hoặc test trên. N4 có thể dùng cấu hình và bundle đã có để dựng lại đầu ra candidate ngay.

## Phần nào cần đợi ai?

| Phần chưa thể kết luận chính thức | Chờ ai và đầu vào nào? | N4 vẫn làm trước được gì? |
|---|---|---|
| Holdout oracle đầy đủ | N2 mở rộng giá token active còn thiếu, lịch sử feed/AnswerUpdated/provenance và kiểm tra ví B2 thiếu state. N3 cung cấp depeg và xác nhận coverage LiquidationCall | Bộ đọc/kiểm schema, HF theo giá tại block, kiểm không dùng giá tương lai/cùng block khi chưa biết thứ tự; chạy mẫu hiện có với nhãn partial |
| Quy tắc làm tròn đúng phiên bản | N2 giải thích/đối chiếu hợp đồng nếu N4 cần cam kết tái tạo số nguyên protocol. Báo cáo N2 hiện thử nhóm nợ <10 triệu USD | Dùng gate đã được chốt và giữ bảng sai số; không cần chờ để chạy Stress. Không ép mọi sai số nhỏ về 0 hoặc gọi 100% toàn bộ người vay |
| Hiệu chỉnh thanh khoản và Cascade có cơ sở | N3 bổ sung quote nhiều mức khối lượng/token; N5 fit và tích hợp engine | Rà threshold/bonus/CF theo snapshot, kiểm đơn vị và giao diện; không tự điền thanh khoản giả cho toàn mẫu |
| Monte Carlo/loss/ES99 thực nghiệm | N3 xác nhận chuỗi 365 returns; N5 nối danh mục thật, chạy 10.000 scenario và trả wallet/scenario losses | Kiểm định nhóm Looping, equity buckets, mẫu số BDR và API nhận kết quả; dùng fixture để kiểm công thức |
| Kết luận Q3/Comet và DebtRank thực nghiệm | N5 hoàn thiện truyền dẫn/liquidation Comet; nhóm nghiên cứu/N1 thống nhất hành vi ví và capacity/hướng cạnh DebtRank còn chưa chốt | Rà mapping wallet–asset–protocol và truyền shock; không áp CF Aave cho Comet hoặc coi degree là DebtRank |
| Giải thích freeze do gas | N3 hỗ trợ giao dịch/gas, N5 cung cấp log tính profit/execution | Chuẩn hóa trường eligibility, executed, frozen; không tự bỏ model miss khỏi mẫu số vì nghi ngờ gas |
| Bàn giao dashboard/báo cáo cuối | N5 hoàn thành nghiệm thu liên quan, N6 kiểm tích hợp và N1 dùng kết quả theo đúng trạng thái | Hoàn thiện hàm/schema/tài liệu và bàn giao candidate để N6 phát triển; không phải chờ mọi KPI hoàn tất mới bàn giao API |

Các phần “chờ” là điều kiện hoàn thành/khẳng định kết quả tương ứng, không phải yêu cầu N4 dừng toàn bộ công việc.

## Thứ tự làm đề xuất

1. Dùng chung cohort/gates trên cả Stress, Heatmap, Cliff, Reverse và dashboard.
2. Thống nhất depeg với N5, kiểm token-specific scenarios và mẫu số.
3. Xuất Reverse, dựng lại các bảng candidate nhất quán; giữ dữ liệu nguồn/full riêng.
4. Bổ sung tests/tài liệu, bàn giao API và candidate cho N5/N6.
5. Khi N2/N3/N5 bổ sung đầu vào tương ứng, chạy lại holdout/Monte Carlo/Q3 rồi mới công bố kết luận.

## Kiểm tra của lần tổng hợp này

Chạy chỉ đọc 5 bộ test HF, Stress, Cliff, contracts và tolerance: **47 passed**.

```powershell
.\.venv\Scripts\python.exe -B -X utf8 -m pytest tests/test_health_factor.py tests/test_stress.py tests/test_cliff_reverse.py tests/test_n4_pending_contracts.py tests/test_research_tolerances.py -q -p no:cacheprovider
```

Đã đọc số dòng marts, kiểm `_asset_multiplier` cho các LST và đọc đường CLI Cliff/Reverse. Các đầu việc nêu trên là backlog từ rà soát; lần này không sửa mô hình hoặc phát hành marts mới.
