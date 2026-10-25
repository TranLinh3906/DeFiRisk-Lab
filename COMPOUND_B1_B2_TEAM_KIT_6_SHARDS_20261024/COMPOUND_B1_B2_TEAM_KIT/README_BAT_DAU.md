# BỘ HƯỚNG DẪN NHÓM — Compound III B1/B2 theo shard (6 người)

> Phiên bản: 24/10/2026. Dùng trong dự án `DeFiRisk-Lab`.
>
> **Không chạy theo hướng dẫn này trên dữ liệu A/B cũ đang lỗi `sorted_wallet_round_robin_v1`.**
> Bộ công cụ này là **lớp kiểm tra + điều phối** gọi lại code chuẩn của repo ở `src/data/fetch_compound_overlap.py --extend-existing-shard`; không dùng một thuật toán cào riêng. Trước khi chạy, nhóm phải xác nhận script gốc trên GitHub hiện có chế độ này. Lệnh `check` sẽ từ chối nếu chưa có.

## 0. Mục tiêu và phân công

- 6 người, mỗi người **chỉ 1 shard**. Năm bạn còn lại phụ trách: `0`, `1`, `2`, `3`, `5`. Người đã làm shard `4` tiếp tục shard `4` sau khi A/B cũ của shard 4 đã hoàn tất và đúng checkpoint assignment.
- B1 = Ethereum mainnet **20,458,637**; B2 = Ethereum mainnet **23,543,615**.
- Hai Comet: `compound_v3_ethereum_usdc` và `compound_v3_ethereum_weth`.
- A = **26,179,841** và B cũ = **26,194,188**, **giữ nguyên giá trị và dòng dữ liệu đã cào**. **B cũ không phải B1/B2**.
- Danh sách ví A/B theo `config/n3_compound_team_assignment.json` (`wallet_shards`). Ví chỉ xuất hiện thêm tại B1/B2 được gán chủ sở hữu bằng SHA-256 ổn định theo quy tắc trong code gốc; ví cũ tiếp tục cùng shard cũ.
- **Sau khi đủ B1/B2:** cập nhật **trong cùng thư mục shard cũ** 4 Parquet + 1 JSON QA bằng chính chế độ extension, không sao chép/ghi đè thủ công. Code gốc tự backup 5 file A/B ra `~/Downloads/Compound_shard_NNN_of_006_before_B1B2_...` rồi mới thay sau khi QA đạt.

| Thành viên | Shard index | Thư mục nguồn/đích thông thường |
|---|---:|---|
| Bạn 1 | `0` | `shard_000_of_006/` |
| Bạn 2 | `1` | `shard_001_of_006/` |
| Bạn 3 | `2` | `shard_002_of_006/` |
| Bạn 4 | `3` | `shard_003_of_006/` |
| Bạn 5 | `5` | `shard_005_of_006/` |
| Người phụ trách shard 4 | `4` | `shard_004_of_006/` (chỉ khi A/B cũ đã sửa xong) |

**Dừng nếu thư mục QA cũ của shard có `assignment=sorted_wallet_round_robin_v1`.** Đây là dữ liệu cũ chia sai ví với `wallet_shards`. Cần giải quyết A/B đúng phân công trước khi append B1/B2; tuyệt đối không sửa nhãn JSON cho “qua bài”.

## 1. Chuẩn bị chung của trưởng nhóm (thực hiện một lần)

1. Xác minh `src/data/fetch_compound_overlap.py` trên nhánh `main` đã được review và có `--extend-existing-shard`, `--existing-shards-root`, checkpoint B1/B2.
2. Xác minh `config/n3_compound_markets.csv` chứa đúng Ethereum mainnet USDC và WETH; `config/n3_compound_team_assignment.json` là phân công gốc 28.640 ví.
3. Đưa file `scripts/team_compound_b1b2.py` từ ZIP này vào repo, review rồi **commit/merge riêng phần code** vào `main` trước khi mọi người bắt đầu. Không đưa bản ZIP, `.env`, dữ liệu test lên Git.
4. Đảm bảo `data/processed/aave/snapshot_metadata.json` có **B1, B2 đúng block, hash đã xác nhận**, và dữ liệu Aave `account_reference.parquet` hoặc `positions_long.parquet` có đủ người vay tại B1/B2. Dữ liệu Aave theo phương pháp scaled × index đã chốt là một bộ phận upstream, không phải đầu ra Compound trong bài này.
5. Kiểm tra đủ sáu thư mục shard old A/B trên GitHub hoặc trao đổi bản phục hồi shard0 đã QA. **Shard 0 cũ từng bị xóa nên cần đảm bảo thật sự đã khôi phục và đưa lên nhánh phù hợp trước khi bạn shard 0 clone**.
6. Phân công chính thức mỗi người một shard; duyệt log dry-run trước khi cho lệnh `collect`.

## 2A. Cài trên macOS (MacBook)

Nếu chưa có repo:

```bash
cd ~/Downloads
git clone https://github.com/TranLinh3906/DeFiRisk-Lab.git
cd DeFiRisk-Lab
```

Nếu đã có repo **và `git status --short` sạch**, từ thư mục gốc:

```bash
git switch main
git pull --ff-only origin main
```

Nếu có file `M`, `??`, `D`, **không dùng `git reset --hard`**, không pull đè. Cất giữ và xác nhận phần việc riêng trước, hoặc clone mới sang một thư mục khác. Vì repo nhóm có nhiều file thay đổi, với 5 máy mới nên **clone mới** thường an toàn nhất.

```bash
python3 --version             # đề xuất Python 3.11/3.12
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
python -m pip install pandas pyarrow web3 python-dotenv requests eth-abi
```

Lần sau dùng `source .venv/bin/activate` khi mở Terminal mới.

## 2B. Cài trên Windows 10/11 (VSCode Terminal: PowerShell)

Cài **Git** và **Python 3.11/3.12** trước. Mở VSCode tại thư mục repo:

```powershell
cd "$env:USERPROFILE\Downloads"
git clone https://github.com/TranLinh3906/DeFiRisk-Lab.git
cd DeFiRisk-Lab
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m pip install pandas pyarrow web3 python-dotenv requests eth-abi
```

Không cần chạy `Activate.ps1` nếu Windows chặn script: mọi lệnh dưới đây chỉ cần thay `python` bằng `.\.venv\Scripts\python.exe`.

## 3. Ethereum archive RPC RIÊNG từng thành viên

Ở thư mục gốc có file `.env` **chỉ lưu trên máy** (không commit). Dùng một trong các biến mà code hỗ trợ:

```dotenv
ETH_RPC_URL=https://YOUR_ETHEREUM_ARCHIVE_RPC_ENDPOINT
```

Có thể dùng `ETHEREUM_ARCHIVE_RPC_URL` hoặc `ALCHEMY_ETHEREUM_RPC_URL` thay thế. RPC phải có dữ liệu lưu trữ lịch sử tại hai block 2024/2025; endpoint chỉ hỗ trợ block mới sẽ lỗi dù code đúng. Mỗi người dùng key riêng để tránh dồn quota chung.

Kiểm tra `.env` không được track:

```bash
git ls-files --error-unmatch .env  # nếu báo lỗi: OK, file chưa được track
```

**Nếu dòng trên in tên `.env` thì DỪNG push.** Nếu key bị lộ screenshot/chat/GitHub: tạo lại key. Không đưa RPC URL hoặc `.env` vào screenshot/log chia sẻ.

## 4. Mỗi người tạo nhánh của riêng shard

Sau khi `main` sạch/đã pull đúng phiên bản:

### Mac/Git Bash
```bash
git switch -c data/compound-b1b2-shard-001
```

### Windows PowerShell
```powershell
git switch -c data/compound-b1b2-shard-001
```

Đổi **001** đúng số shard của bạn (`000`, `001`, `002`, `003`, `005`). Đây là ví dụ cho **shard 1**. Không hai người cùng sửa chung một shard.

## 5. CÁC BƯỚC BẮT BUỘC — ví dụ shard 1

Tất cả chạy ở thư mục gốc repo, đã cài dependencies và `.env`.

### (1) Kiểm tra A/B cũ + phân công — KHÔNG gọi RPC
```bash
python scripts/team_compound_b1b2.py check --shard 1
```

- In ra đúng shard 1; QA cũ có `A=26179841`, `B=26194188`.
- Kiểm tra toàn bộ địa chỉ của 4 Parquet thuộc đúng `wallet_shards` shard 1.
- Phải **PASS**. Nếu phát hiện `sorted_wallet_round_robin_v1`, phải chỉnh A/B trước, không chạy tiếp.

Nếu shard nằm ở thư mục khác, bổ sung `--existing-shards-root /path/to/parent` (Windows đường dẫn cha tương ứng).

### (2) Dry-run B1/B2 — KHÔNG gọi RPC
```bash
python scripts/team_compound_b1b2.py dry-run --shard 1
```

GỬI CHO TRƯỞNG NHÓM LOG có đầy đủ:

```text
[EXISTING SHARD] .../shard_001_of_006
[HISTORICAL BLOCKS] {'B1': 20458637, 'B2': 23543615}
[GLOBAL NEW WALLETS] ...
[NEW QUERIES FOR THIS SHARD] ...
[DRY RUN] No RPC, no file changes
```

`[NEW QUERIES FOR THIS SHARD]` là số truy vấn B1/B2 của **shard này**, không phải cả nhóm. Hai Comet được xác nhận qua config và code.

### (3) Thu thập dữ liệu (CHỈ SAU KHI TRƯỞNG NHÓM DUYỆT LOG)
```bash
python scripts/team_compound_b1b2.py collect --shard 1 \
  --batch-size 1 --wallet-chunk 1 --rpc-interval 2
```

Windows PowerShell chạy **trên một dòng**:

```powershell
.\.venv\Scripts\python.exe scripts/team_compound_b1b2.py collect --shard 1 --batch-size 1 --wallet-chunk 1 --rpc-interval 2
```

Wrapper luôn chạy preflight và dry-run thêm lần nữa trước khi gọi RPC. Nên chạy lúc không còn thông báo `429`, có đủ quota. Với `--batch-size 1`, số lượt RPC có thể lớn, tốn nhiều thời gian/quota; theo dõi log/provider. Giãn cách 2s không cam kết ngăn hoàn toàn 429.

Nếu kết quả `[INCOMPLETE]`, `HTTP 429` hoặc timeout, KHÔNG reset và KHÔNG xóa: **chạy lại đúng y lệnh `collect` trên**. Checkpoint B1/B2 nằm riêng trong **thư mục shard hiện có**, tên `n3_overlap_checkpoint_B1_B2.jsonl`.

Không sử dụng `--overwrite`, `--reset-checkpoint`, `--assignment-file`, hoặc `git add .`.

### (4) Chỉ sau khi đủ truy vấn mới thay 5 file của shard cũ

Chế độ extension gốc sẽ tự kiểm định candidate, sau đó tạo bản sao A/B bên ngoài repo và ghi 5 file đã nối A/B/B1/B2 vào *cùng đường dẫn cũ*. Không có thao tác ghi đè thủ công.

Khi hoàn thành, log phải có:

```text
[UPDATED EXISTING SHARD] ...shard_001_of_006
[SNAPSHOTS] A, B, B1, B2
[BACKUP] ...Downloads/Compound_shard_001_of_006_before_B1B2_...
```

### (5) QA độc lập, kiểm tra lại snapshot và backup
```bash
python scripts/team_compound_b1b2.py qa --shard 1
```

QA wrapper sẽ kiểm: 4 Parquet và 1 JSON đầy đủ; snapshot/block map A/B/B1/B2; không trùng primary keys; wallet ownership đúng shard cho cả dữ liệu mới; checkpoint B1/B2 có số lượng, fingerprint nhất quán, đủ hai Comet; **từng dòng A/B phải giống hệt bản backup nguyên gốc**. **Chỉ giao khi có `[QA PASS]`**.

Trên Windows thay `python` bằng `.\.venv\Scripts\python.exe`.

### (6) Stage ĐÚNG 5 FILE CŨ — không thêm file khác
```bash
python scripts/team_compound_b1b2.py stage --shard 1
```

`stage` chạy lại tất cả QA trước, sau đó chỉ `git add` **5 file đã được Git track sẵn**, đúng thư mục shard này:

```text
shard_001_of_006/n3_compound_account_reference.parquet
shard_001_of_006/n3_compound_positions_long.parquet
shard_001_of_006/n3_compound_overlap_wallets.parquet
shard_001_of_006/n3_compound_reserve_config.parquet
shard_001_of_006/n3_compound_quality.json
```

Nếu dữ liệu shard nằm trong `data/processed/compound/...`, `stage` tự tìm đúng đường dẫn thực tế của thư mục cũ. Không stage `.env`, ZIP, code thử, backup, checkpoint JSONL, dữ liệu shard khác.

### (7) Review rồi commit/push NHÁNH RIÊNG
```bash
git diff --cached --name-status
git diff --cached --stat
git commit -m "data(compound): extend shard 001 with B1 B2"
git push -u origin data/compound-b1b2-shard-001
```

Tạo **Pull Request vào main**. Sau khi trưởng nhóm xác nhận QA, merge từng shard. Kết quả **cập nhật 5 file tại đúng đường dẫn cũ trên GitHub**, không tạo thư mục kết quả song song.

> Nếu `git diff --cached` có file lạ: dừng và hỏi nhóm. Không tùy tiện stage dữ liệu cả dự án.

## 6. Cấu trúc bàn giao trên GitHub khi hoàn tất

```text
DeFiRisk-Lab/
├── config/
│   ├── n3_compound_team_assignment.json
│   └── n3_compound_markets.csv
├── data/processed/aave/
│   ├── snapshot_metadata.json
│   ├── account_reference.parquet   # hoặc source B1/B2 positions_long
│   └── positions_long.parquet
├── src/data/
│   └── fetch_compound_overlap.py   # code gốc có --extend-existing-shard
├── scripts/
│   └── team_compound_b1b2.py       # wrapper an toàn (merge 1 lần)
├── shard_000_of_006/
│   ├── n3_compound_account_reference.parquet
│   ├── n3_compound_positions_long.parquet
│   ├── n3_compound_overlap_wallets.parquet
│   ├── n3_compound_reserve_config.parquet
│   ├── n3_compound_quality.json
│   └── n3_overlap_checkpoint_B1_B2.jsonl   # local, thường KHÔNG push
├── shard_001_of_006/
│   └── (cùng bộ 5 file)
├── shard_002_of_006/
│   └── (cùng bộ 5 file)
├── shard_003_of_006/
│   └── (cùng bộ 5 file)
├── shard_004_of_006/
│   └── (sau khi A/B đã khớp assignment + complete)
└── shard_005_of_006/
    └── (cùng bộ 5 file)
```

**Lưu ý:** Đây là cấu trúc ví dụ nếu shard nằm ở root. Một số repo có shard tại `data/processed/compound/shard_XXX_of_006/` hoặc lồng đôi; script sẽ kiểm tra đúng đường dẫn hiện có, không tự tạo bản thứ hai.

## 7. Các lỗi thường gặp

| Lỗi | Nguyên nhân và giải pháp |
|---|---|
| `not checkpoint-assigned` | QA A/B cũ thuộc round-robin; **cần xử lý và hoàn tất A/B**, không bypass |
| `Need exactly one old A/B shard folder` | Chưa pull/khôi phục shard cũ hoặc có bản sao trùng; dùng `--existing-shards-root` trỏ thư mục CHA |
| `Missing B1/B2 reference / positions` | Chưa có dữ liệu N2 tái dựng B1/B2; đồng bộ upstream từ nhóm |
| `snapshot block/hash mismatch` | Metadata N2 khác block yêu cầu; dừng, sửa nguồn upstream, không bỏ kiểm tra |
| `429 / RPC error` | Hết quota hoặc bị throttle; chờ quota, chạy lại lệnh `collect` đúng nguyên dạng, **không reset** |
| `QA did not PASS` | Không stage. Giữ backup và checkpoint. Kiểm tra log, bổ sung dữ liệu/khắc phục trước |
| `Old GitHub file is not tracked` | Bạn đang ở sai nhánh/vị trí hoặc shard bị mất trên main; không force-add bản sao mới |
| `No original A/B backup` | QA buộc có bản A/B trước cập nhật để so khớp chính xác; tìm thư mục `Downloads/Compound_shard...` |
| `can't open file scripts/...` | Đặt `team_compound_b1b2.py` trong `scripts/` đúng đường dẫn, mở Terminal ở root repo |

## 8. Checklist gửi trưởng nhóm trước khi merge

- [ ] Đúng duy nhất shard được phân công, không sai ownership.
- [ ] Dry-run có log `B1=20458637`, `B2=23543615`.
- [ ] Có bản checkpoint B1/B2 và log thu thập hai thị trường USDC/WETH.
- [ ] `[QA PASS]` gồm kiểm tra A/B giống hệt bản sao lưu.
- [ ] `git diff --cached --name-status` chỉ có 5 file của shard đó.
- [ ] Pull Request trên nhánh riêng; gửi trưởng nhóm đường dẫn PR.

**Quan trọng:** Tôi đã kiểm tra luồng wrapper bằng dữ liệu mô phỏng, nhưng chưa thể kiểm chứng trạng thái GitHub `main`, RPC archive, hoặc chạy thực tế trên sáu máy; cần trưởng nhóm xác nhận code collector hiện hành, snapshot N2, và log dry-run từng shard trước khi chạy thật.
