# Kiểm thử bộ công cụ B1/B2

Ngày tạo: 24/10/2026.

Đã kiểm tra:
- `python -m py_compile`: hợp lệ.
- Đối chiếu plan thực do nhóm đã gửi: `wallet_shards` = 28.640, SHA-256 = `6105d9df30ba...`.
- Dữ liệu mô phỏng: QA A/B đạt với phân công checkpoint; chặn QA old `sorted_wallet_round_robin_v1`.
- Dữ liệu mô phỏng: 4 Parquet A/B/B1/B2, 1 QA, 4 RPC key B1/B2 trong 2 Comet, so sánh A/B bằng backup: PASS.
- Dữ liệu mô phỏng: nếu thay đổi một giá trị A/B, QA từ chối bàn giao.
- Repo Git mô phỏng: `stage` chỉ stage những file QA cho phép, không stage những file ngoài bộ năm file.

**Chưa kiểm tra:** phiên bản hiện hành ở GitHub (web access không thành công); dữ liệu đầy đủ của sáu shard từ máy thành viên; availability của Ethereum archive RPC và quota; chạy end-to-end với lịch sử blockchain thật. Trưởng nhóm phải duyệt dry-run và nghiệm thu từng shard.
