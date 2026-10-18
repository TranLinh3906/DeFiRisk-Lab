# N3 — Xác định phạm vi và chạy thử dữ liệu Aave V3

## 1. Phạm vi chạy thử

- Mạng: Ethereum Mainnet (`chain_id=1`).
- Protocol: Aave V3 Ethereum.
- Pool: `0x87870bca3f3fd6335c3f4ce8392d69350b4fa4e2`.
- Sự kiện: `LiquidationCall`, lọc theo topic
  `0xe413a321e8681d831f4dbccbca790d2952b56f977908e45be37335533e005286`.
- Giai đoạn kiểm thử: từ `2026-10-12 00:00:00 UTC` đến trước
  `2026-10-16 00:00:00 UTC` (4 ngày, đầu mút cuối không bao gồm).
- Nguồn log: Etherscan API V2 `getLogs`.
- Giá lịch sử theo sự kiện: DefiLlama token market-price reference theo contract
  address; không phải Aave Oracle.
- Chuỗi giá tham chiếu độc lập: CoinGecko Demo cho ETH, stETH, wstETH, BTC, USDC,
  USDT. Tỷ lệ stETH/ETH và wstETH/ETH được tính từ giá USD cùng giờ UTC; không
  phải `stEthPerToken()` của Lido hoặc giá thực thi tại DEX.

Thay đổi `--start` và `--end` nếu chọn giai đoạn khác. Tất cả ngày dùng UTC,
`--start` bao gồm và `--end` loại trừ.

## 2. Cài đặt và cấu hình

Chạy các lệnh từ thư mục gốc repository:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install pandas requests pyarrow python-dotenv
```

Đặt các key trong `.env` ở thư mục gốc; không chia sẻ file này và không commit
lên Git:

```dotenv
ETHERSCAN_API_KEY=your_etherscan_key
COINGECKO_API_KEY=your_coingecko_demo_key
```

## 3. Thu thập LiquidationCall

```bash
python src/data/fetch_liquidations.py \
  --start 2026-10-12 --end 2026-10-16 --overwrite
```

`--overwrite` tạo bản sao lưu trước khi thay thế kết quả kiểm thử cũ. Script lấy
timestamp-to-block, quét Pool log, giải mã event và truy vấn decimals tại block
event đầu tiên của từng token. Khóa event là `(tx_hash, log_index)`; không loại
bỏ trùng lặp âm thầm.

Đầu ra:

- `data/raw/market/etherscan_liquidations_raw.csv`: log thô từ Etherscan.
- `data/staging/n3_liquidations_etherscan.parquet`: event đã giải mã, gồm raw
  amounts, decimals và địa chỉ token.
- `outputs/tables/n3_etherscan_quality.json`: khoảng thời gian, pool, block,
  số event và kiểm tra chất lượng.

## 4. Lấy giá USD theo từng liquidation

```bash
python src/data/price_liquidations_usd.py --overwrite
```

Script dùng raw integer amount và decimals để định giá theo giá DefiLlama tại
thời điểm lịch sử. Nếu quote đầu tiên nằm sau event, script thử tìm quote ở các
mốc sớm hơn 15, 30 và 45 phút; chỉ chấp nhận quote không sau event và không cũ
hơn `--max-lag-minutes`. Quote tương lai bị từ chối để tránh look-ahead bias;
quote thiếu hoặc quá cũ không được thay bằng 0. Kiểm tra các trạng thái quote và
coverage trước khi dùng tổng USD.

- `data/staging/n3_liquidations_priced.parquet`: tất cả event, cả event thiếu giá.
- `data/staging/n3_liquidations_for_fact.parquet`: event đủ cả hai giá USD, đúng
  11 cột contract.
- `outputs/tables/n3_usd_pricing_quality.json`: coverage, quote status và tổng
  USD chỉ trên event đã định giá đủ.
- `data/raw/market/n3_defillama_price_cache.json`: cache quote theo bucket và
  contract address.

## 5. Lấy chuỗi ETH/USD, stETH/ETH và giá wstETH

```bash
python src/data/fetch_prices.py \
  --start 2026-10-12 --end 2026-10-16 --overwrite
```

Script lấy riêng giá tham chiếu USD của ETH, stETH và wstETH (cùng BTC, USDC,
USDT). `n3_market_ratios.parquet` chứa tỷ lệ stETH/ETH, wstETH/ETH và
wstETH/stETH, chỉ tính khi có quan sát của cả hai token trong cùng giờ UTC,
không nội suy hoặc forward-fill.

- `data/raw/market/prices_raw.csv`: quan sát giá CoinGecko.
- `data/staging/n3_price_timeseries.parquet`: toàn bộ quan sát giá.
- `data/processed/market/price_history.parquet`: giá cuối có sẵn trong mỗi ngày
  UTC cho từng tài sản.
- `data/staging/n3_market_ratios.parquet`: các tỷ lệ market cùng giờ.
- `outputs/tables/n3_price_quality.json`: cửa sổ, số quan sát và số ngày.

Giá CoinGecko là giá thị trường tham chiếu, không phải Aave Oracle. Tỷ lệ giá
USD không thay thế tỷ lệ chuyển đổi on-chain của Lido hoặc giá execution-specific.

## 6. Chuẩn hóa liquidation và tạo chuỗi giá thống nhất

Sau khi đã có file `n3_liquidations_priced.parquet`, chạy:

```bash
python src/data/finalize_liquidations.py --min-coverage-pct 100
```

Kết quả `data/staging/n3_liquidations.parquet` giữ tất cả event và thêm trạng
thái kiểm tra. File `n3_liquidations_for_fact.parquet` chỉ chứa event đạt chuẩn
11 cột. Khi file đầu ra đã tồn tại, chỉ thêm `--overwrite` nếu muốn sao lưu và
thay thế.

Để hợp nhất market data với các nguồn on-chain đã được xác nhận:

```bash
python src/data/build_price_timeseries.py --overwrite
```

Lệnh trên luôn tạo market references/market ratios từ CSV. Nó **không tự tạo**
oracle hoặc Lido conversion nếu chưa có `config/n3_oracle_snapshots.csv` cùng
`ETH_RPC_URL`/`ETHEREUM_ARCHIVE_RPC_URL`, hoặc CSV đã kiểm chứng từ N2. Block
snapshot A/B phải do N1/N2 xác nhận; không dùng probe block làm snapshot nghiên
cứu. Trạng thái các loại giá được ghi trong
`outputs/tables/n3_price_timeseries_quality.json`.

## 7. Dữ liệu thanh khoản DEX và mô tả

Chỉ sau khi nhóm xác nhận pool và snapshot block, tạo
`config/n3_dex_pools.csv` theo header trong
`config/n3_dex_pools.example.csv`, rồi chạy:

```bash
python src/data/fetch_dex_liquidity.py
```

Collector hỗ trợ reserve/balance point-in-time cho Curve và Uniswap V2-compatible
pools. Đầu ra `data/staging/n3_dex_liquidity.parquet` là số dư token trong pool;
nó **không phải** swap quote, executable depth curve hay slippage estimate.
Với Curve, `coin_indices` là các chỉ số coin cần đọc; `curve_index_type` chọn
`uint256` (mặc định) hoặc `int128` theo ABI của pool. Lỗi RPC/contract không bị
bỏ qua để thử một selector khác.
Không có địa chỉ pool đã chọn thì không tạo file dữ liệu giả.

Sau khi có liquidation và price time series:

```bash
python src/analysis/descriptive.py
```

Script sinh bảng ngày/giờ, các SVG trong `outputs/figures/` và báo cáo chất
lượng trong `outputs/tables/n3_descriptive_quality.json`. Tương quan chỉ mang
tính mô tả, không chứng minh quan hệ nhân quả.

## 8. Kiểm tra kết quả

```bash
./.venv/bin/python - <<'PY'
import pandas as pd

events = pd.read_parquet("data/staging/n3_liquidations.parquet")
priced = pd.read_parquet("data/staging/n3_liquidations_priced.parquet")
fact = pd.read_parquet("data/staging/n3_liquidations_for_fact.parquet")
prices = pd.read_parquet("data/staging/n3_price_timeseries.parquet")

print("EVENTS:", events.shape, events.event_timestamp.min(), events.event_timestamp.max())
print("DUPLICATE EVENT KEYS:", events.duplicated(["tx_hash", "log_index"]).sum())
print("PRICED:", priced.shape, "FACT READY:", fact.shape)
print("PRICE TYPES:", prices.price_type.value_counts().to_dict())
PY
```

Chỉ tích hợp batch fact-ready sau khi kiểm tra schema, khóa duy nhất và chất lượng
giá. Không ghi trực tiếp các kết quả staging vào bảng canonical của dự án.

## 9. Giới hạn và diễn giải

- Sự kiện liquidation lịch sử không đồng nghĩa với mô phỏng bad debt.
- DefiLlama và CoinGecko cung cấp giá market reference; để kết luận theo Oracle
  cần dữ liệu Aave/Chainlink theo block.
- stETH/ETH ở đây là tỷ lệ giá thị trường cùng giờ. Cần gọi `stEthPerToken()` tại
  block tương ứng nếu yêu cầu tỷ lệ chuyển đổi của Lido.
- Không dùng giá wstETH/USD để suy diễn tỷ lệ tài sản ở thời điểm thực thi nếu
  không có dữ liệu DEX và độ trượt giá.
- Event thiếu giá vẫn phải giữ trong staging và được báo cáo; không thay bằng 0
  hoặc loại bỏ âm thầm.
- Snapshot A/B, mẫu ví và pool DEX chưa được xem là chính thức nếu N1/N2 chưa xác
  nhận. Mọi coverage hiện tại phải được mô tả đúng là coverage của batch/mẫu.
