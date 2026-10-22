# N3 — Compound III Aave-wallet overlap, Q3

- **Hướng dẫn đầy đủ cho nhóm hiện tại (giữ khoảng 7.000 ví đã cào, RPC → chạy → gửi file → ghép):** [docs/HUONG_DAN_CHIA_TU_CHECKPOINT.md](docs/HUONG_DAN_CHIA_TU_CHECKPOINT.md)

- **Chia nhóm cào song song và ghép kết quả:** `docs/HUONG_DAN_CHIA_NHOM.md`

- **Bắt đầu:** `docs/N3_COMPOUND_Q3_README.md`
- Code: `src/data/fetch_compound_overlap.py`
- Thị trường: `config/n3_compound_markets.csv` (Ethereum USDC/WETH)
- Kiểm định: `scripts/qa_n3_compound.py`, `tests/test_n3_compound_overlap.py`
- Đây là mã hỗ trợ mở rộng Q3; **không khẳng định đã thu thập dữ liệu live**. Bắt buộc chạy bằng Archive RPC và tự kiểm tra QA trên máy của nhóm.
- Không chứa `.env`, API key hoặc dữ liệu người dùng trong ZIP.
