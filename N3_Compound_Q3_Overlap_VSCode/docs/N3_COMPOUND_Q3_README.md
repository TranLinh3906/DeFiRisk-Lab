# N3 bổ sung: Truy xuất Compound III cho Q3 (Aave ↔ Compound)

## Phạm vi và giới hạn

Đây là **script bổ trợ của N3**, dùng danh sách ví Aave do N2 cung cấp để kiểm tra vị thế **tại đúng Snapshot A/B** trên hai Compound III Comet Ethereum Mainnet: USDC và WETH. Không phải thay thế phần thu thập/chuẩn hóa Aave của N2. N1/N2 phải xác nhận thị trường, phạm vi ví và các kết quả trước khi ghép vào Data Contract chính thức. **Đừng coi ví hoạt động trên hai giao thức là bằng chứng thực nghiệm đã xảy ra lây lan rủi ro**.

Nguồn kiểm tra hàm và cơ chế: [Compound III Documentation](https://docs.compound.xyz/), [Helper Functions](https://docs.compound.xyz/helper-functions/), [Collateral and Borrowing](https://docs.compound.xyz/collateral-and-borrowing/), [Comet deployments](https://github.com/compound-finance/comet/tree/main/deployments/mainnet).

### Đầu vào có sẵn trong dự án

- `data/processed/aave/account_reference.parquet`: ví Aave tại A/B, có `wallet_address`, `snapshot_id` hoặc `snapshot_block`, `total_debt_base_usd` (N2 hiện tại; hoặc `total_debt_usd`/`debt_usd`).
- `data/processed/aave/snapshot_metadata.json`: **block 26179841 (A)**, **26194188 (B)**. Script đọc block từ file này, không tự lấy block hiện tại.
- `config/n3_compound_markets.csv`: địa chỉ Comet proxy chính thức của **USDC/WETH** Ethereum mainnet, chỉ hai market trong scope. Phải rà soát việc tồn tại ở từng block.
- `.env` cục bộ, chứa `ETHEREUM_ARCHIVE_RPC_URL=https://...` hoặc `ETH_RPC_URL`, không đưa lên GitHub.

### Bước 1 — chép file vào đúng thư mục của repo

Trong VS Code, mở **root repo** `~/Downloads/N3_Aave_Day17_Two_Scripts` (hoặc root Git repo hiện tại). Chép đúng đường dẫn tương ứng từ ZIP (thư mục `src/`, `config/`, `tests/`, `docs/`) vào repo. **Đừng tạo thêm repo lồng nhau.**

```bash
cd ~/Downloads/N3_Aave_Day17_Two_Scripts
source .venv/bin/activate
python3 -m pip install -r requirements_n3_compound.txt
```

### Bước 2 — kiểm tra dữ liệu N2 (KHÔNG gọi RPC)

```bash
python3 src/data/fetch_compound_overlap.py --dry-run --max-wallets 100
```

Nó cho biết Aave wallet count, selection scope, A/B block và USDC/WETH market. Nếu N2 chưa xuất `account_reference.parquet` đúng cột, chương trình sẽ báo tên cột thiếu; không tự điền giá trị.

### Bước 3 — chạy thử RPC và QA trên 100 ví Aave có dư nợ lớn nhất

```bash
python3 src/data/fetch_compound_overlap.py --max-wallets 100 --batch-size 40 --wallet-chunk 5
```

- Truy vấn tất cả asset Comet được hỗ trợ ở từng snapshot, cho từng ví được chọn, bao gồm base borrow, base supply, collateral và `isLiquidatable`.
- Có checkpoint: `data/raw/compound/n3_overlap_checkpoint.jsonl` để chạy tiếp nếu timeout/rate limit; khi chạy lại chỉ truy vấn ví chưa thành công. Nếu có lỗi RPC, QA ghi `n3_compound_failed_queries.json`, **không xuất các bảng hoàn thành giả**.
- Khi dữ liệu đã xuất mà bạn muốn cập nhật từ checkpoint, thêm `--overwrite` (sao lưu file cũ trước khi thay).
- Nếu metadata market đổi hoặc snapshot/giá khác, checkpoint có thể bị từ chối; chỉ sau khi xác minh mới dùng `--reset-checkpoint` (tạo backup raw checkpoint).

### Bước 4 — chạy toàn bộ tập Aave sau khi smoke test đạt

```bash
python3 src/data/fetch_compound_overlap.py --max-wallets 0 --batch-size 40 --wallet-chunk 5 --overwrite
```

⚠️ **Chi phí đáng kể:** Hàng chục nghìn ví × 2 snapshot × 2 Comet markets × (3 + n_assets) lần `eth_call`; có thể hàng triệu lời gọi RPC/tiêu tốn credits. Trước khi chạy FULL cần N1/N2 duyệt phạm vi, ngân sách RPC và ghi rõ coverage. Nên chạy tiếp checkpoint thay vì reset. Nếu chỉ 100 ví, phải ghi *sample of top Aave borrowers*, không đại diện toàn bộ Aave.

### Output khi kiểm tra RPC thành công, không lỗi

```text
data/processed/compound/
    n3_compound_positions_long.parquet
    n3_compound_account_reference.parquet
    n3_compound_reserve_config.parquet
    n3_compound_overlap_wallets.parquet
    n3_compound_quality.json

data/raw/compound/
    n3_overlap_checkpoint.jsonl       # file rất lớn, KHÔNG nên git push
```

Kết quả `n3_compound_positions_long.parquet` biểu diễn **base_borrow / base_supply / collateral**, `isLiquidatable` là hàm gốc của Comet; không được coi là Aave HF. Các số USD lấy **Comet getPrice() tại snapshot**, không dùng giá CoinGecko hiện tại.

### QA sau khi xuất

```bash
python3 scripts/qa_n3_compound.py
python3 -m unittest discover -s tests -p 'test_n3_compound_overlap.py' -v
```

QA cần `complete=true` trong JSON, không có khóa trùng; mỗi ví giao nhau phải có ít nhất một dòng vị thế Compound; tài sản được định giá dương; tổng nợ/collateral theo ví khớp bảng vị thế. `overlap_wallets` là giao **mẫu ví Aave đã chọn** ∩ **ví có vị thế Comet tại snapshot**, không phải tất cả lịch sử hoạt động trên Compound.

### Bàn giao nhóm

N3 → N2/N1: dữ liệu staging Compound được kiểm định, có địa chỉ Comet, block, market, raw amount và log nguồn. N2 chịu trách nhiệm tích hợp Data Contract nếu nhóm đồng ý; N4/N5 sử dụng dữ liệu để mô phỏng Q3. Compound III không có quy tắc thanh lý identical với Aave; N5 phải viết cơ chế riêng.

### GitHub an toàn

Thêm `.gitignore` nếu chưa có:

```gitignore
.env
.venv/
__pycache__/
*.bak*
/data/raw/compound/n3_overlap_checkpoint.jsonl
```

Chỉ `git add` script, CSV config, QA, kết quả Parquet/JSON đã nghiệm thu. Không push API key hoặc checkpoint thô lớn. Trước khi push, đảm bảo nhóm không đồng thời ghi vào các bảng chuẩn N1.
