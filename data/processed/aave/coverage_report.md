# Coverage report - Aave V3 Ethereum (N2)

_Tạo tự động ngày 2026-10-20T05:28:28Z bởi `normalize_aave.py`, sau đó chỉnh tay phần phạm vi và ghi chú. Chạy lại `normalize_aave.py` sẽ ghi đè các chỉnh sửa này._

## Phạm vi (đọc trước)

- **Giao thức:** Aave V3 Ethereum **Core Market** (Pool `0x87870bca3f3fd6335c3f4ce8392d69350b4fa4e2`). Không gồm các market Aave khác (ví dụ Prime, EtherFi) và các chain khác.
- **Cách lập tập ví:** tập ví ứng viên được lập từ sự kiện `Borrow` đến block snapshot (quét từ block 16,200,000; sự kiện `Borrow` đầu tiên ở block 16,496,939). Sau đó lọc ra các ví còn nợ (`has_debt`) bằng `getUserAccountData` và chỉ các ví này được lấy số dư theo token.
- **Độ phủ nợ:** tổng nợ của tập ví so với `totalSupply` của debt token on-chain là **100.0000%** ở cả Snapshot A và B (lệch khoảng 1 USD trên khoảng 9.4 tỷ USD). Kết luận chỉ áp dụng cho **nợ**, tại **hai snapshot này**, trong **Core Market**.
- **Ngoài phạm vi:** ví chỉ gửi tiền mà không vay. Không có tiêu chí 1% tương đương cho collateral/TVL vì `aToken` gồm cả người chỉ gửi tiền.
- **Giới hạn phương pháp:** cách lập tập ví không bắt trực tiếp nợ mở bằng flash loan (`interestRateMode` khác 0) hay cơ chế không qua sự kiện `Borrow`. Độ phủ đo được là khoảng 100% ở hai snapshot, nhưng không đảm bảo cho block khác.
- Bảng `borrower_positions.parquet` là **staging**, chưa phải bảng canonical của nhóm.

## Snapshot A

- Block `26179841` | UTC `2026-10-12T23:59:59+00:00` | hash `0x40c731f68bcd8152ad12433f4be57783e731fcf1546575ab51aa894054244ac3`
- Ví ứng viên (từng có sự kiện `Borrow` đến block): **87,286**
- Ví gọi được `getUserAccountData`: 87,286/87,286
- Ví còn nợ tại snapshot: **28,493** | tổng nợ theo Aave: 9,427,484,969 USD
- Ví HF<1 (Aave báo): 449 | tổng nợ của nhóm này: 52.97 USD
- Đối chiếu HF tự tính với Aave (28,493 ví): sai số tuyệt đối trung vị 0.000000; ví HF≤10 (26,069 ví): p99 0.004349; sai số tương đối trung vị 3.16e-12
- Ví lệch nợ ≥1%: 220; ví lệch collateral ≥1%: 30; ví lệch ngưỡng HF<1: 1 (xem ghi chú)
- Tổng nợ tập ví so với `totalSupply` debt token của giao thức: 9,427,484,969 / 9,427,484,968 USD = **100.0000%**

## Snapshot B

- Block `26194188` | UTC `2026-10-14T23:59:59+00:00` | hash `0xf12bcb7db5631cc69f0aaa392df0855f2a2ff3c18a9b9bcbb3a25981cac9daed`
- Ví ứng viên (từng có sự kiện `Borrow` đến block): **87,388**
- Ví gọi được `getUserAccountData`: 87,388/87,388
- Ví còn nợ tại snapshot: **28,545** | tổng nợ theo Aave: 9,336,947,229 USD
- Ví HF<1 (Aave báo): 460 | tổng nợ của nhóm này: 52.91 USD
- Đối chiếu HF tự tính với Aave (28,545 ví): sai số tuyệt đối trung vị 0.000000; ví HF≤10 (26,131 ví): p99 0.004304; sai số tương đối trung vị 3.19e-12
- Ví lệch nợ ≥1%: 229; ví lệch collateral ≥1%: 35; ví lệch ngưỡng HF<1: 2 (xem ghi chú)
- Tổng nợ tập ví so với `totalSupply` debt token của giao thức: 9,336,947,229 / 9,336,947,228 USD = **100.0000%**

## Ghi chú và giới hạn

- **Sai lệch từ 1% trở lên đều ở vị thế rất nhỏ.** 220 (A) và 229 (B) ví lệch nợ ≥1% có nợ Aave trung vị làm tròn bằng 0.0 USD; 30 (A) và 35 (B) ví lệch collateral ≥1% cũng vậy. Tỷ lệ của các nhóm này trên tổng nợ/tổng collateral làm tròn 8 chữ số vẫn bằng 0. Các ví lệch ngưỡng HF<1 (1 ví ở A, 2 ví ở B) có nợ tối đa khoảng 2.3e-5 USD và HF rất gần 1. Nguyên nhân dự đoán là làm tròn đơn vị USD 8 chữ số thập phân của Aave; **chưa kiểm tra từng ví**.
- **Ví HF<1 là vị thế bụi.** 449 (A) và 460 (B) ví có HF<1 nhưng tổng nợ chỉ khoảng 53 USD. Ngưỡng "nợ bụi" do N1/N4 quyết định; dữ liệu giữ nguyên, chưa gắn cờ dust.
- **Đối chiếu HF là kiểm tra nội bộ của N2** (HF tự tính từ số dư, giá oracle và LT, so với HF Aave báo trên ví còn nợ trong tập ứng viên). Kiểm định độc lập thuộc N5. Công thức tính LT hiệu lực theo E-Mode là cách hiểu của N2 về Aave V3, được đối chiếu với HF Aave báo và khớp trên 2,626 (A) và 2,651 (B) ví E-Mode.
- **Tiêu chí 1% với giao thức chỉ áp dụng cho tổng nợ.** Collateral của người vay không thể bằng TVL.
- **E-Mode:** category 47 và 48 đổi liquidation bonus giữa A và B (10420 thành 10414; 10120 thành 10114). Cấu hình theo token không đổi giữa A và B. Cột `effective_liq_bonus` chưa điền, chờ quy tắc từ N1/N4.
- **Isolation mode và siloed borrowing** chưa được mô hình hóa riêng; chỉ có cờ `is_isolated_collateral` ở mức dòng, suy từ debt ceiling khác 0.
- **Giá:** giá tính HF là giá oracle Aave tại block (USD, 8 chữ số thập phân), không phải giá thị trường CoinGecko của N3.
- **Snapshot:** timestamp là của block cuối cùng có thời điểm không muộn hơn mốc mục tiêu (sớm hơn khoảng 1 giây). Snapshot B chờ N1 duyệt. ETH giảm khoảng 1.4% từ A đến B; trong 24 giờ sau B có lúc thấp hơn khoảng 3.3% so với lúc chụp rồi hồi lại; N3 ghi nhận 30 sự kiện thanh lý trong cửa sổ đó, nên kiểm định ngoài mẫu có độ mạnh thống kê thấp. Chưa có bằng chứng riêng cho việc Snapshot A là giai đoạn bình thường.