# Nội dung gửi nhóm — Bổ sung Compound III B1/B2

Mọi người tiếp tục đúng **shard đã phụ trách** trước đây (5 bạn còn lại: shard 0, 1, 2, 3, 5). Chỉ chạy **B1 (block 20458637) và B2 (block 23543615)**, mỗi snapshot đủ **2 Comet USDC/WETH**. Không sửa/xóa A/B cũ và không tự đổi phân công ví.

**Quy trình bắt buộc:**
1. Pull code mới nhất trên GitHub sau khi trưởng nhóm merge script kiểm tra; nếu máy có thay đổi Git chưa lưu thì clone mới để tránh conflict.
2. Tạo môi trường Python, cài `requirements.txt`, cấu hình **Ethereum archive RPC riêng** trong `.env` (không push key).
3. Chạy `python scripts/team_compound_b1b2.py check --shard N` rồi `python scripts/team_compound_b1b2.py dry-run --shard N`. Chụp/dán log xác nhận B1/B2 và số truy vấn cho trưởng nhóm.
4. Chỉ khi được duyệt mới chạy `python scripts/team_compound_b1b2.py collect --shard N --batch-size 1 --wallet-chunk 1 --rpc-interval 2`.
5. Nếu lỗi RPC/429, chạy lại **đúng lệnh**, checkpoint tự lưu; **không reset**.
6. Sau khi script báo cập nhật, chạy `python scripts/team_compound_b1b2.py qa --shard N`; phải có `[QA PASS]` mới bàn giao.
7. Chạy `python scripts/team_compound_b1b2.py stage --shard N`; chỉ stage **4 Parquet + 1 JSON QA tại đường dẫn shard cũ**, commit rồi push nhánh shard riêng, mở PR. Không `git add .`.

**Đặc biệt:** Nếu QA A/B cũ còn `sorted_wallet_round_robin_v1`, báo trưởng nhóm để căn chỉnh đúng `wallet_shards` trước; tuyệt đối không bỏ qua bước kiểm tra. Bạn shard 0 lưu ý kiểm tra folder A/B cũ đã được phục hồi trên Git.

Hướng dẫn cài Mac, Windows, xử lý lỗi và cấu trúc thư mục: xem `README_BAT_DAU.md` trong ZIP.
