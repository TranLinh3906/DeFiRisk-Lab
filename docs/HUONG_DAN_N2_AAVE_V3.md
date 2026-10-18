# N2 – Hướng dẫn chạy bộ thu thập & chuẩn hóa Aave V3 Ethereum

**Dự án**: *Multi-Layer Network Contagion in Decentralized Finance: Quantifying Systemic Risk from Overlapping Collateral and Cross-Protocol Wallets* (Research Design v2.0).

**Phạm vi của bộ này**: 3 nhiệm vụ N2 được phân công: (1) kiểm tra RPC, Aave market, xác định snapshot/tập ví/giới hạn, (2) lấy tài sản thế chấp, nợ theo token, trạng thái tài khoản, (3) lưu raw JSONL + reserves và xuất `borrower_positions.parquet`. Chưa bao gồm Compound V3, mô phỏng Q1–Q3, phân loại recursive looping, hoặc hoàn thiện 6 bảng canonical.

## 1. Phải hiểu rõ trước khi chạy

- **Aave V3 Ethereum mainnet**, chain ID `1`, Pool `0x87870bca3f3fd6335c3f4ce8392d69350b4fa4e2` (khớp phần thu thập sự kiện thanh lý của N3). RPC phải hỗ trợ gọi `eth_call` ở **block lịch sử**. RPC miễn phí thông thường có thể không hỗ trợ archive/pruned history.
- **Snapshot A / Snapshot B**: các mốc tạm chọn theo phạm vi N3 theo yêu cầu người dùng, ghi trong `data/processed/aave/snapshot_selection.json`; archive RPC xác nhận block/time và Pool. Đây chưa phải phê duyệt độc lập của N1. Block `20458000` chỉ là probe cũ, không phải A/B. Mọi lần đọc khóa vào block được chỉ định; không dùng `latest` để lấy số dư.
- **Tập ví**: Pool không có hàm “liệt kê toàn bộ ví đang vay”. `discover` quét `Borrow` và lấy *onBehalfOf* — ví từng phát sinh nợ. Danh sách chỉ là **candidate frame**, có thể chứa ví đã tất toán; cần lọc số dư nợ ở snapshot và kiểm tra độ phủ. **Không dùng sự kiện thanh lý sau snapshot B để chọn mẫu B (look-ahead bias).**
- **Số dư**: lấy `aToken.balanceOf(user)` và `variableDebtToken.balanceOf(user)` / `stableDebtToken.balanceOf(user)` tại block, là số dư đã ghi nhận lãi tích lũy; lưu thêm `scaledBalanceOf` và liquidity/variable debt index để kiểm tra, **không nhân index lần 2**. Đây là quy tắc trong Data Contract.
- Các block A/B tạm chọn theo phạm vi N3 được ghi trong `data/processed/aave/snapshot_selection.json`. Block/time đã xác minh qua archive RPC; lựa chọn này không thay thế phê duyệt thiết kế độc lập của N1. Candidate frame (`config/aave_borrower_candidates_A.csv`, `_B.csv`) không phải sample file để fetch.
- **USD**: lấy giá từ **Aave Oracle ở đúng block**, và chỉ quy đổi USD khi `BASE_CURRENCY` bằng địa chỉ zero `0x000...0000` (USD theo Aave) và `BASE_CURRENCY_UNIT=10**8`. Giá này không phải CoinGecko hay DefiLlama của N3.
- **E-Mode**: lưu `emode_category` thực tế và reserve config; khi E-Mode khác 0, không áp dụng đại trà `lt_std` cho HF. Cần đọc E-Mode category/priceSource/khả năng sử dụng collateral theo phiên bản Aave tại block, rồi N2/N4 tái dựng HF và kiểm định với số Aave báo cáo.
- **Giới hạn:** Quét Borrow logs, sau đó đọc mỗi ví × các reserve qua RPC có thể mất lâu và tốn quota. Mặc định giới hạn 20 ví để thử. Với hàng nghìn ví cần lập lịch batch/chunk/checkpoint, archive RPC và nghiệm thu độ phủ.

## 2. Chuẩn bị trong VS Code (Mac/Windows)

Đưa ZIP đã giải nén vào dự án của nhóm theo cấu trúc:

```text
TEN_DU_AN/
├─ src/
│  └─ data/
│     ├─ fetch_aave.py                 <-- code N2
│     └─ normalize_aave.py             <-- code N2
├─ config/
│  ├─ aave_wallets.csv                 <-- có header, chưa có ví (không bịa ví)
│  └─ env_n2.example                   <-- chỉ để tham khảo
├─ data/                               <-- script sẽ tự tạo thư mục lúc lưu
├─ requirements_n2.txt
└─ .env                                <-- tự tạo trên máy, KHÔNG commit
```

Trong Terminal tại thư mục gốc:

```bash
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install -r requirements_n2.txt
```

Windows PowerShell dùng `.venv\\Scripts\\Activate.ps1` thay `source ...`.

Tạo `.env` tại thư mục gốc (lấy URL từ nhà cung cấp RPC thực sự hỗ trợ archive state):

```dotenv
ETH_RPC_URL=https://YOUR_ARCHIVE_ETHEREUM_RPC_ENDPOINT
# Tùy chọn: nếu có endpoint Aave subgraph đã được xác thực
# AAVE_SUBGRAPH_URL=https://YOUR_AAVE_SUBGRAPH_ENDPOINT
```

**Không gửi API key / RPC URL chứa secret lên GitHub, ảnh chụp, hoặc vào chat.** Trong `.gitignore` nên có `.env`, `.venv/`, `data/raw/`, và các thư mục dữ liệu lớn tùy chính sách repo; dùng metadata/báo cáo QA để tái lập thay vì đưa key.

## 3. Bước 1 – Kiểm tra Chain, Aave Pool, RPC lịch sử, Oracle

**Thay `BLOCK_A` bằng số block N1 đã chốt; không gõ nguyên chữ BLOCK_A.** Ví dụ các lệnh này minh họa trường hợp `20458000`:

```bash
python3 src/data/fetch_aave.py probe --snapshot-block 20458000 --tag A --save
```

Được kiểm tra: chain ID 1, block tồn tại, Aave Pool/getReservesList, Aave AddressesProvider, Oracle trả giá với USD base, metadata/reserve dữ liệu tại block, contract bytecode của aToken/debt token; ngoài ra thử `_meta` Aave Subgraph nếu đã cung cấp URL. Nếu quá khứ không truy vấn được, chương trình **dừng và báo lỗi**.

Dữ liệu **probe** thử ghi vào: `data/processed/aave/snapshot_probe_A.json` (không ghi đè `snapshot_metadata_A.json` chính thức của lần fetch). Khi chạy bước fetch, file metadata sẽ xuất ở đúng `data/processed/aave/snapshot_metadata_A.json`, hoặc `data/processed/aave/snapshot_metadata.json` nếu không dùng `--tag`. Metadata này không khẳng định 100% người vay đã được thu thập.

## 4. Bước 2 – Tạo tập ví ứng viên

### Phương án A: Có danh sách từ Aave Subgraph/N2

Điền các địa chỉ ví thật, có nguồn kiểm chứng, vào `config/aave_wallets.csv` theo mẫu:

```csv
wallet_address
0x...DIA_CHI_VI_THAT_HOP_LE...
```

Lưu ý: dòng `0x...` ở trên chỉ là **minh họa dạng dữ liệu**, không phải địa chỉ có thể chạy. Không tự thay bằng 1 ví nổi tiếng rồi kết luận cho toàn thị trường.

### Phương án B: Quét `Borrow` logs của Aave Pool

Các mốc hiện chọn và deployment block đã xác minh được ghi tại
`data/processed/aave/snapshot_selection.json`. RPC archive ở gói hiện tại
giới hạn `eth_getLogs` tối đa 10 block mỗi yêu cầu; vì vậy candidate frame được
quét qua Etherscan API V2 bằng script chuyên dụng, một lần cho cả A/B:

```bash
python3 src/data/build_aave_sampling_frame.py
```

Lệnh lọc chữ ký sự kiện Borrow và lấy `onBehalfOf`; range được chia đôi nếu API
trả đủ 1000 logs để không chấp nhận kết quả có thể bị cắt. Kết quả:

- `config/aave_borrower_candidates_selected_window_A.csv` và `_B.csv`: địa chỉ cùng block vay đầu/cuối và số Borrow logs đến từng snapshot.
- `data/processed/aave/wallet_discovery_selected_window_A.json` và `_B.json`: block range, nguồn frame, số lượng ứng viên và giới hạn.

File `config/aave_wallets_A.csv` hiện có là kết quả quét probe cũ chỉ trong
block range `20450000–20458000`; không dùng làm sample hoặc candidate frame đầy đủ.
Candidate frames mới có khoảng 87 nghìn địa chỉ mỗi snapshot; trước khi fetch
cần chọn sample size/strata/seed, ghi vào metadata, rồi lưu mẫu riêng thành
`config/aave_wallets_A.csv` và `_B.csv`. Mỗi address trong frame chưa chắc còn
có dư nợ tại snapshot.

Đây là candidate frame lịch sử, không chứng minh ví còn nợ tại snapshot và không phải mẫu ngẫu nhiên. Trước khi fetch, rút mẫu ngẫu nhiên có seed cố định từ frame, lưu riêng vào `config/aave_wallets_A.csv` / `_B.csv`, rồi xác nhận số dư nợ tại snapshot. Không lấy 20 ví đầu vì sẽ tạo selection bias.

Nếu `discover` tiêu tốn quá nhiều API hoặc lỗi ở block quá khứ, sử dụng phương án A.

## 5. Bước 3 – Thu thập dữ liệu thô từng ví

Cho Snapshot A (dùng wallet sample đã rút riêng, không dùng thẳng candidate frame):

```bash
python3 src/data/fetch_aave.py fetch --snapshot-block 20458000 --tag A --wallets config/aave_wallets_A.csv --max-wallets 20
```

Nếu muốn đọc 100 ví và đã kiểm tra quota RPC, có thể đổi `--max-wallets 100`. Nếu cần ghi lại output cũ, thêm `--overwrite`: script tự sao lưu bản trước bằng suffix `.bak_...`.

Đầu ra:

```text
data/raw/aave/A/accounts_raw.jsonl
           A/reserves_raw.json

data/processed/aave/snapshot_metadata_A.json
```

Nếu không có `--tag` thì đúng đường dẫn gốc bảng phân công:

```text
data/raw/aave/accounts_raw.jsonl
data/raw/aave/reserves_raw.json
data/processed/aave/snapshot_metadata.json
```

Mỗi dòng `accounts_raw.jsonl` là một ví với `getUserAccountData`, `emode_category`, cấu hình bitmap, vị thế từng token và số dư `balanceOf/scaledBalanceOf`. `reserves_raw.json` ghi oracle, `getReserveData`, risk bitmap, index, decimals, giá USD; **không lưu khóa API**.

Để làm Snapshot B, chạy riêng với `--tag B --snapshot-block <BLOCK_B> --wallets ...`. Đừng ghi đè A bằng B; không trộn hai snapshots.

## 6. Bước 4 – Chuẩn hóa wallet–token và kiểm tra

```bash
python3 src/data/normalize_aave.py --tag A
```

Kết quả:

```text
data/processed/aave/borrower_positions_A.parquet
                    borrower_accounts_A.parquet
                    account_reference_A.parquet
                    reserve_config_A.parquet
                    oracle_prices_A.parquet
                    normalization_quality_A.json
                    coverage_report_A.md
```

Nếu không dùng `--tag`, file tên tương ứng không có hậu tố `_A`.
`account_reference` giữ HF do Pool báo cáo cho mọi ví đã truy vấn, kể cả ví không
có vị thế vay; `reserve_config` và `oracle_prices` được xuất từ cùng snapshot.

Để đọc nhanh:

```bash
python3 - <<'PY'
import pandas as pd
p = pd.read_parquet('data/processed/aave/borrower_positions_A.parquet')
a = pd.read_parquet('data/processed/aave/borrower_accounts_A.parquet')
print('Số ví đang vay:', a.wallet_address.nunique())
print('Số dòng wallet-token:', len(p))
print('Tổng nợ sample (USD):', round(a.total_debt_usd_from_positions.sum(),2))
print(p[['wallet_address','token_symbol','collateral_amount_norm','debt_amount_norm','collateral_usd_t0','debt_usd_t0','emode_category']].head(10).to_string(index=False))
PY
```

Các nguyên tắc ở `normalize_aave.py`:

1. Một dòng = `(snapshot_block, protocol_id, wallet_address, token_address)`; không trùng PK.
2. Quy đổi số dư bằng `balanceOf / 10**decimals`, không lấy dư nợ raw chưa tính lãi.
3. Chỉ xem supply là **collateral đủ điều kiện** nếu cờ `usage_as_collateral_enabled` được bật; vẫn lưu `supplied_amount_norm` riêng.
4. Tách `variable_debt` và `stable_debt`, tổng nợ = cả hai (khi stable debt còn tồn tại trong snapshot lịch sử).
5. Dùng **Aave Oracle tại đúng block** để định giá.
6. So sánh tổng nợ và tài sản thế chấp theo từng ví với `getUserAccountData` của Aave.
7. HF tính thử theo `lt_std` **chỉ áp dụng với ví không có E-Mode**; các ví E-Mode cần N2/N4 bổ sung `effective_lt` và category price source phù hợp. Trường thiếu để null trong **bảng N2 staging**, không đẩy bảng này trực tiếp vào contract canonical (NOT NULL).
8. E-Mode category ID và tham số LTV/liquidation threshold/bonus/price source được lưu nếu RPC trả cấu hình category; cần kiểm chứng reserve eligibility trước khi gán effective LT/bonus cho từng token.
9. Isolation mode account-level được đánh dấu từ collateral-enabled reserve có debt ceiling khác 0; reserve-level flags và debt ceiling được giữ để kiểm tra.
10. File `normalization_quality_A.json` và `coverage_report_A.md` ghi cổng kiểm tra mẫu, không được xem là nghiệm thu TVL toàn thị trường.

## 7. Phù hợp Data Contract và ba câu hỏi Q1–Q3 thế nào?

| Mục | Đã có | Chưa có / chủ thể bổ sung |
|---|---|---|
| Q1 Recursive Looping | Cặp token có supply/debt, user E-Mode, định giá thời điểm snapshot | N4 phân loại loop dựa trên LST/WETH, effective leverage, phân tầng equity; N2/N4 kiểm chứng mode và HF |
| Q2 Close Factor | Dư nợ, tài sản thế chấp, LT chuẩn, bonus chuẩn, giá oracle t0 | N4/N5 mô phỏng các CF, oracle động, DEX impact, thanh lý thực tế từ N3 |
| Q3 Overlapping Collateral | Các token theo ví, tổng dư nợ, ví có nhiều tài sản | N2 mở rộng Compound V3 cho ví cầu nối; N3 cung cấp depeg và DEX; N5 dựng mạng |
| `fact_wallet_positions` (canonical) | Đã có phần lớn trường nguyên liệu và PK, đơn vị/tỷ lệ phù hợp | Cần xác minh effective LT/bonus E-Mode, `dim_asset_config` đầy đủ DEX depth, lambda, gamma, và chạy qua `schema_storage_safe.py`; **không viết thẳng canonical** |
| Validation Gate N1 | Đối chiếu từng ví Aave, HF chuẩn thường | Chưa có đối chiếu tổng TVL và Total Debt 1% với toàn Pool, chưa có HF median error cho E-Mode, chưa có historical hit-rate |

**Ba điều cần ghi rõ khi trình bày:** (i) có chọn mẫu hay toàn bộ ví, (ii) snapshot block đã cố định, (iii) E-Mode và giá oracle có tái dựng đúng phiên bản hợp đồng lịch sử hay chưa. Không được gán giá market từ CoinGecko cho `getAssetPrice` của oracle.

## 8. Các lỗi thường gặp

- `Missing ETH_RPC_URL`: đặt `.env` ở **thư mục gốc**, không phải `src/data`.
- `missing trie node`, `state unavailable`, `archive not supported`: RPC không cung cấp state lịch sử tại block đã chọn. Cần nhà cung cấp RPC archive hoặc node phù hợp, **không đổi `latest`**.
- `wrong chain_id`: URL đang trỏ chain khác; nghiên cứu yêu cầu Ethereum mainnet chain=1.
- `getReserveData layout`/aToken code invalid: ABI hoặc bản Pool tại block không tương thích, cần kiểm tra phiên bản; **không tự điền địa chỉ token**.
- `CSV has N wallets; --max-wallets=20`: giới hạn an toàn; muốn nâng hãy tính quota trước.
- `borrower_positions` rỗng: các ví trong mẫu có thể chưa vay tại block; phải chọn lại danh sách ví trước snapshot chứ không được bịa nợ.
- `emode_needs_category_level_oracle_and_LT`: tính HF theo E-Mode cần cấu hình danh mục và price source hiệu lực tại block; chuyển N2/N4 xác minh.
- `pyarrow`: `python3 -m pip install -r requirements_n2.txt` trong đúng môi trường `.venv`.
- `--overwrite`: chỉ áp dụng **output N2**, có backup, không ghi đè bảng canonical của nhóm.

## 9. Tài liệu nguồn và giới hạn trích dẫn

**Từ chính tài liệu nhóm đã gửi:** `research_design.md` (bản v2.0 – Core-and-Bridge, snapshots A/B, Q1–Q3, reconstruction/HF, 3 validation gates), `DATA_CONTRACT.docx` (6 bảng/90 trường, PK/FK, units, E-Mode fallback, safe storage) và `literature_review.xlsx` (nhóm tham khảo Aave V3 Technical Paper, liquidation/contagion và E-Mode). Literature Review hỗ trợ lập luận nghiên cứu, **không thay thế chứng cứ ABI và cấu hình theo block**.

**Tài liệu kỹ thuật chính thức để kiểm chứng method/event:**

- Aave, [Pool](https://aave.com/docs/aave-v3/smart-contracts/pool) – `getReservesList`, `getUserAccountData`, `getReserveData`, `getUserConfiguration`, `getUserEMode`.
- Aave, [View Contracts](https://www.aave.com/docs/aave-v3/smart-contracts/view-contracts) – `getUserReservesData`, `scaledATokenBalance` / `scaledVariableDebt`.
- Aave, [Pool interface – Borrow event](https://github.com/aave/aave-v3-core/blob/master/contracts/interfaces/IPool.sol) – sự kiện `Borrow` và `onBehalfOf` là người chịu dư nợ.
- Aave DAO, [ReserveData source](https://github.com/aave-dao/aave-v3-origin/blob/main/src/contracts/protocol/libraries/types/DataTypes.sol) – cách bố trí reserve bitmap/index/token addresses; cần xét phiên bản hợp đồng tại block thực tế.

**Giới hạn kiểm thử bộ code:** Đã chạy kiểm tra cú pháp và kiểm thử chuẩn hóa trên dữ liệu tổng hợp tại môi trường tạo code. Chưa chạy RPC mainnet/đọc ví thực hoặc ghi Parquet thực tế ở môi trường tạo code (không có archive RPC và `pyarrow` tại đó). Chỉ có kết quả thực tế trên máy bạn mới cho phép kiểm định tiếp.
