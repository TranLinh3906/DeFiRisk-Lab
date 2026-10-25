# N5 offline preparation — chưa nghiệm thu

A/B1/B2: dữ liệu thật được chuyển đổi và chẩn đoán độc lập; không chạy Cascade thật.
Config A/B1/B2 được kiểm ở snapshot_readiness.json; B cũ chỉ là legacy, không đổi nhãn.
Zero-shock đơn thuần không thay kiểm HF tái dựng và tiny-shock: xem reconstructed_hf_validation.json.
Graph giữ toàn bộ ví đầu vào, chưa lọc dust; membership là cạnh thông tin, không cộng vào exposure.
Historical: proxy dùng hourly open có sẵn trước sự kiện, không phải dự báo oracle đã nghiệm thu.
Compound: A đủ truy vấn mục tiêu; B1/B2 partial, phần chưa truy vấn là unknown.
Synthetic: 8 ví giả lập; Parquet và biểu đồ chỉ phục vụ tích hợp/kiểm thử.
N4 summary tách snapshot dùng marts hiện tại; mọi gate chưa đạt vẫn giữ nguyên.
Manifest unvalidated; không tự đưa bộ này vào data/marts chính thức.
Fixtures Q1/Q3 là giả lập, không phải kết quả nghiên cứu; dust chỉ là phương án chờ duyệt.
Xem summary.json, A/B1/B2/validation.json và manifest.json.
