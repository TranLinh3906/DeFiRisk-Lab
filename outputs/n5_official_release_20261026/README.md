# Bundle Q1–Q3 chính thức — 26/10/2026

Đọc `results/REPORT.md` và `results/hypothesis_conclusions.csv` để lấy kết luận nghiên cứu. `metadata/publication_artifacts.csv` là danh sách bảng/hình chính thức; `metadata/kpi_release_dictionary.csv` đối chiếu định nghĩa gốc với chỉ tiêu thực sự được công bố.

Bundle giữ nguyên byte của các kết quả đã nghiệm thu kỹ thuật, kèm bản sao đầy đủ các đầu vào và source_versions đã dùng. B1 là 20458637. Manifest kiểm cả results và inputs nội bộ; không phụ thuộc hash của mã nguồn workspace được sửa về sau. Không sửa số liệu hoặc tái chạy mô hình để làm manifest sạch.

Các manifest lịch sử được giữ làm chứng cứ. Audit phân biệt 96 chuỗi đường dẫn khai báo với 95 tệp sau chuẩn hóa (79 tệp khác phiên bản và 16 đường dẫn cũ thiếu). Các hash lịch sử chưa khôi phục đủ byte được ghi rõ, không được tự coi là nguồn đã xác thực của lần chạy chính thức.

Q1 chính dùng matched 1:1 trước sự kiện; các phân phối khủng hoảng là sensitivity riêng. ES99 là trung bình đúng 100 loss lớn nhất trong 10.000 draw, có cả loss bằng 0. N_seq đủ điều kiện và N_seq thực thi được giữ riêng; H3 dùng HF0>=1.10 và vẫn khỏe sau shock. Không gọi 18 mapping KPI là 18 chỉ tiêu được nghiệm thu khoa học.

KPI M08/M09 dùng đơn vị chuẩn hóa theo 100.000 USD equity trong kết quả chính, không gắn nhãn triệu USD hoặc ratio-of-sums. M14 freeze nhân quả, M16 CPCI đầy đủ, M17 edge-share thực tế và M18 node-specific DebtRank chưa được công bố như chỉ tiêu gốc; xem đối chiếu định nghĩa thay vì lấy biến proxy để điền vào.

Chứng cứ phản hồi giá là kiểm định dự báo quan sát, chưa nhận dạng nhân quả. Full-feedback là sensitivity; kênh Comet là một chiều, không phải mô phỏng vòng kín toàn giao thức.

Kiểm tra độc lập từ workspace:

```powershell
.\.venv\Scripts\python.exe -B -X utf8 -m scripts.n5_finalize_release --stage verify
```

Để tái lập thí nghiệm, khôi phục đúng input_index và source_versions của từng giai đoạn trong một checkout riêng. Các đường dẫn gốc trong provenance là thông tin truy nguyên; kiểm hash bản phát hành dùng các bản sao nội bộ.
