# Bàn giao kiểm định cho N4 — sau pull 9b34ff7

Kết quả từ dữ liệu hiện tại, chưa sửa code N4. Thư mục:
`outputs/n5_preparation/n5_postpull_9b34ff7_20261023/`.

## 1. Sửa cờ collateral trong hàm HF

59 ví A và 57 ví B đổi phân loại do khác cờ collateral ví so với fallback reserve.
Hàm HF chưa nhận alias `collateral_enabled` trong borrower_positions của N2,
có thể dùng usage_as_collateral cấp reserve thay cho cờ ví.

Kiểm cột wallet_collateral_enabled/n4_collateral_enabled/adapter_collateral_enabled,
lượng collateral, weighted contribution trong:

- [Token của 60 ví A](../outputs/n5_preparation/n5_postpull_9b34ff7_20261023/A/hf_disagreement_tokens.csv)
- [Token của 58 ví B](../outputs/n5_preparation/n5_postpull_9b34ff7_20261023/B/hf_disagreement_tokens.csv)

Ưu tiên cờ theo ví, không fallback sang reserve chỉ vì alias chưa được nhận diện.

## 2. Kiểm permission E-Mode khi chọn LT

1 ví mỗi snapshot đổi phân loại do membership không được phép collateral nhưng
N4 vẫn áp E-Mode LT. Cần giữ is_collateral qua main và join category-specific,
rồi chỉ áp LT E-Mode khi category/token/cờ collateral đáp ứng quy tắc.

Lọc candidate_cause=membership_collateral_permission_ignored trong các file token.

## 3. Kiểm zero-shock và shock rất nhỏ bằng cùng phép tính đầu vào

Không chỉ gán hf_after=hf_before khi shock=0 để test đạt. Cần tái dựng baseline
từ cùng số dư/giá/LT/cờ với Stress và kiểm độ khớp trước mô phỏng. Phát hiện
hiện tại: 436 dòng lệch HF tái dựng ≥0,005; 446 dòng có bước nhảy ≥0,005 giữa
zero/tiny-shock; 118 dòng khác phân loại baseline với phép tái dựng.

File: [HF tái dựng không khớp](../outputs/n5_preparation/n5_postpull_9b34ff7_20261023/reconstructed_hf_failures.csv).

## 4. Chặn target NaN/vô cực

Thống nhất kiểm finite/nonnegative/range ở nhánh số, dictionary và phần trăm.
Các test cụ thể đã có trong `tests/test_n4_pending_contracts.py`.

## Tiêu chí kiểm lại

- Chạy tests N4 và regression contracts; bỏ expectedFailure sau khi xác nhận sửa đúng.
- Đối chiếu HF với adapter/Aave trên cùng block; xem từng ví đổi phân loại.
- Zero-shock phải khớp phép tái dựng; tiny-shock không có bước nhảy do hai nhánh
  tính HF khác nhau. Không sao chép HF để thay kiểm định.
- Tái tạo mart đúng snapshot sau sửa, rồi bàn giao N5 kiểm lại. Không dùng tổng
  dư nợ nhóm bất đồng làm bad debt hoặc lượng thanh lý.

Các nhãn nguyên nhân đã được kiểm bằng phép tái dựng số học khớp mart N4,
không phải kết luận nhân quả thị trường. Dữ liệu B ở đây là B cũ 2026, không phải B1/B2.
