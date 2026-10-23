# Coverage report - Aave V3 Ethereum (N2)

_Tạo tự động ngày 2026-10-23T17:45:54Z bởi `normalize_aave.py` từ `snapshot_metadata.json` và các bảng trong `data/processed/aave/`._

## Phạm vi (đọc trước)

- **Giao thức:** Aave V3 Ethereum **Core Market** (Pool `0x87870bca3f3fd6335c3f4ce8392d69350b4fa4e2`). Không gồm các market Aave khác và các chain khác.
- **Cách lập tập ví:** tập ví ứng viên từ sự kiện `Borrow` đến block snapshot; chỉ các ví còn nợ (`has_debt`) tại block được lấy số dư theo token.
- **Độ phủ nợ** so với `totalSupply` debt token on-chain của Pool: xem từng snapshot. Kết luận chỉ áp dụng cho **nợ**, tại các snapshot đã chạy, trong Core Market.
- Bảng `borrower_positions.parquet` là **staging**, chưa phải bảng canonical của nhóm.

## Snapshot A (calibration / counterfactual stress test)

- Block `26179841` | UTC `2026-10-12T23:59:59+00:00` | hash `0x40c731f68bcd8152ad12433f4be57783e731fcf1546575ab51aa894054244ac3`
- Ví ứng viên (từng có `Borrow` đến block): **87,286**
- Ví gọi được `getUserAccountData`: 87,286/87,286
- Ví còn nợ tại snapshot: **28,493** | tổng nợ theo Aave: 9,427,484,969 USD
- Ví HF<1 (Aave báo): 449 | tổng nợ của nhóm này: 52.97 USD
- Đối chiếu HF tự tính với Aave (28,493 ví): sai số tuyệt đối trung vị 0.000000; ví HF≤10 (26,069): p99 0.004349; sai số tương đối trung vị 3.16e-12
- Ví lệch nợ ≥1%: 220; ví lệch collateral ≥1%: 30
- Tổng nợ tập ví so với `totalSupply` debt token của giao thức: 9,427,484,969 / 9,427,484,968 USD = **100.0000%**

## Snapshot B1 (out-of-sample validation 1 (Yen Carry Trade))

- Block `20458637` | UTC `2024-08-04T23:59:59+00:00` | hash `0x95ed667a5a234f47766e9f89a744e0c6d550de01ccaffe02c1dd56ea23ed5b92`
- Ví ứng viên (từng có `Borrow` đến block): **21,262**
- Ví gọi được `getUserAccountData`: 21,262/21,262
- Ví còn nợ tại snapshot: **12,047** | tổng nợ theo Aave: 5,868,705,952 USD
- Ví HF<1 (Aave báo): 346 | tổng nợ của nhóm này: 27,299.22 USD
- Đối chiếu HF tự tính với Aave (12,047 ví): sai số tuyệt đối trung vị 0.000000; ví HF≤10 (11,481): p99 0.000448; sai số tương đối trung vị 5.38e-12
- Ví lệch nợ ≥1%: 17; ví lệch collateral ≥1%: 2
- Tổng nợ tập ví so với `totalSupply` debt token của giao thức: 5,868,705,952 / 5,868,705,952 USD = **100.0000%**

## Snapshot B2 (out-of-sample validation 2 (10/10 Meltdown))

- Block `23543615` | UTC `2025-10-09T23:59:59+00:00` | hash `0xacaf6dc7e29b333e24d5142d4863f867d51f20a86cdb10ab89c9bb4241a01496`
- Ví ứng viên (từng có `Borrow` đến block): **62,622**
- Ví gọi được `getUserAccountData`: 62,622/62,622
- Ví còn nợ tại snapshot: **27,985** | tổng nợ theo Aave: 21,688,604,176 USD
- Ví HF<1 (Aave báo): 236 | tổng nợ của nhóm này: 330.15 USD
- Đối chiếu HF tự tính với Aave (27,985 ví): sai số tuyệt đối trung vị 0.000000; ví HF≤10 (25,901): p99 0.077859; sai số tương đối trung vị 2.34e-11
- Ví lệch nợ ≥1%: 537; ví lệch collateral ≥1%: 10
- Tổng nợ tập ví so với `totalSupply` debt token của giao thức: 21,688,604,175 / 21,688,604,175 USD = **100.0000%**

## Ghi chú và giới hạn

_Các mục dưới đây lấy nguyên từ `limitations` trong `snapshot_metadata.json`._

- Phạm vi: Aave V3 Ethereum Core Market (Pool 0x87870bca3f3fd6335c3f4ce8392d69350b4fa4e2). Không gồm các market Aave khác (ví dụ Prime, EtherFi) và các chain khác.
- Tập ví: lập từ sự kiện Borrow (quét từ block 16,200,000; Borrow đầu tiên ở block 16,496,939) đến block snapshot, rồi lọc ví còn nợ bằng getUserAccountData. A: 87,286 ví ứng viên, 28,493 ví còn nợ; B1: 21,262 ví ứng viên, 12,047 ví còn nợ; B2: 62,622 ví ứng viên, 27,985 ví còn nợ. Ví chỉ gửi tiền, không vay, nằm ngoài phạm vi.
- Độ phủ nợ so với tổng totalSupply debt token on-chain của Pool: A: 100.0000% (tổng nợ giao thức 9,427,484,968 USD); B1: 100.0000% (tổng nợ giao thức 5,868,705,952 USD); B2: 100.0000% (tổng nợ giao thức 21,688,604,175 USD). Chỉ đo cho nợ, tại các snapshot này, trong Core Market; không có tiêu chí tương đương cho collateral/TVL vì aToken gồm cả người chỉ gửi tiền.
- Phương pháp lập tập ví không bắt trực tiếp nợ mở bằng flash loan (interestRateMode khác 0) hay cơ chế không qua sự kiện Borrow. Độ phủ đo được ở các snapshot trên không đảm bảo cho block khác.
- Đối chiếu HF tự tính với HF Aave báo trên toàn bộ ví còn nợ trong tập ứng viên: A: sai số tuyệt đối trung vị 0.000000, p99 (ví HF≤10) 0.0043, sai số tương đối trung vị 3.2e-12; B1: sai số tuyệt đối trung vị 0.000000, p99 (ví HF≤10) 0.0004, sai số tương đối trung vị 5.4e-12; B2: sai số tuyệt đối trung vị 0.000000, p99 (ví HF≤10) 0.0779, sai số tương đối trung vị 2.3e-11. Đây là kiểm tra nội bộ của N2; kiểm định độc lập thuộc N5. status.hf_validated = true khi sai số tuyệt đối trung vị < 0.005 ở mọi snapshot (tiêu chí của research_design.md); p99 có thể vượt 0.005 do các vị thế rất nhỏ (xem dòng sai lệch ≥1% bên dưới).
- Sai lệch từ 1% trở lên: A: 220 ví lệch nợ ≥1% (nợ Aave của nhóm 0.00 USD = 0.000000% tổng nợ; 0 ví có nợ ≥100 USD), 30 ví lệch collateral ≥1%, 1 ví lệch ngưỡng HF<1; B1: 17 ví lệch nợ ≥1% (nợ Aave của nhóm 0.00 USD = 0.000000% tổng nợ; 0 ví có nợ ≥100 USD), 2 ví lệch collateral ≥1%, 0 ví lệch ngưỡng HF<1; B2: 537 ví lệch nợ ≥1% (nợ Aave của nhóm 2.28 USD = 0.000000% tổng nợ; 0 ví có nợ ≥100 USD), 10 ví lệch collateral ≥1%, 30 ví lệch ngưỡng HF<1. Nguyên nhân dự đoán là làm tròn đơn vị USD 8 chữ số của Aave ở vị thế rất nhỏ; chưa kiểm tra từng ví.
- Ví có HF<1 (Aave báo): A: 449 ví (tổng nợ 52.97 USD); B1: 346 ví (tổng nợ 27,299.22 USD); B2: 236 ví (tổng nợ 330.15 USD). Ngưỡng nợ bụi do N1/N4 quyết định; dữ liệu giữ nguyên, chưa gắn cờ dust.
- Cách tính nợ biến đổi: B1, B2 dùng scaled balance × chỉ số nợ chuẩn hóa (cách Aave dùng khi tính HF); A dùng balanceOf của debt token (checkpoint cũ không lưu scaled balance), cần thống nhất phương pháp nếu so sánh trực tiếp giữa các snapshot. Lý do: với B1, tính bằng balanceOf làm lệch nợ của ví vay GHO (nợ tự tính thấp hơn nợ Aave) và p99 sai số HF của ví HF≤10 là 0.0128 trên mẫu 200 ví; sau khi chuyển sang scaled balance p99 giảm còn khoảng 0.0004. Nguyên nhân dự đoán là chiết khấu lãi suất của GHO, chưa kiểm chứng trực tiếp.
- E-Mode: cơ chế đọc khác nhau theo phiên bản Aave tại từng block. A: 48 category; B1: 1 category; B2: 28 category. Snapshot dùng E-Mode kiểu cũ (mỗi reserve thuộc đúng một category, tra bằng getReserveEModeCategory) hoặc bitmap tùy phiên bản; không dùng chung một bảng E-Mode cho các snapshot. Cột effective_liq_bonus chưa điền, chờ quy tắc từ N1/N4.
- Số lượng reserve theo snapshot: A: 67; B1: 33; B2: 55. PoolDataProvider và phiên bản Aave khác nhau giữa các block, nên so sánh giữa snapshot cần ghi chú.
- Isolation mode và siloed borrowing chưa được mô hình hóa riêng; chỉ có cờ is_isolated_collateral suy từ debt ceiling khác 0.
- Giá dùng để tính HF là giá oracle Aave tại block (USD, 8 chữ số thập phân), không phải giá thị trường CoinGecko của N3.
- Block snapshot là block cuối cùng có timestamp không muộn hơn mốc mục tiêu. Số block 'khoảng' ban đầu B1: 20458692, B2: 23530241 được thay bằng block tìm theo timestamp (B1: 20458637, B2: 23543615); các bước lọc sự kiện thanh lý (event_block >) phải dùng block mới.
- Chưa có bằng chứng riêng cho việc Snapshot A là giai đoạn bình thường.
- borrower_positions.parquet là bảng staging, chưa phải bảng canonical của nhóm.
