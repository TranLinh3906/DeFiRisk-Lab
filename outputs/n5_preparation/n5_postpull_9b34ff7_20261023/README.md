# N5 offline preparation — chưa nghiệm thu

A/B: dữ liệu thật được chuyển đổi và chẩn đoán độc lập; không chạy Cascade thật.
Config A/B1/B2 được kiểm ở snapshot_readiness.json; B cũ chỉ là legacy, không đổi nhãn.
Zero-shock đơn thuần không thay kiểm HF tái dựng và tiny-shock: xem reconstructed_hf_validation.json.
Graph giữ toàn bộ ví đầu vào, chưa lọc dust; membership là cạnh thông tin, không cộng vào exposure.
Historical: candidate từ hai cửa sổ 2024/2025; không phải holdout Snapshot B 2026.
Compound: chỉ partial; phần chưa truy vấn là unknown, không phải số dư 0.
Synthetic: 8 ví giả lập; Parquet và biểu đồ chỉ phục vụ tích hợp/kiểm thử.
N4 summary tách snapshot vẫn dùng HF cũ của N4, chưa chứng nhận nghiệp vụ.
Manifest unvalidated; không tự đưa bộ này vào data/marts chính thức.
Xem summary.json, A/validation.json, B/validation.json và manifest.json.
