# Bản đồ tích hợp N3 Mục 4–5 theo Người 1

## Research Design v2.0

- Core protocol: Aave V3 on Ethereum; liquidity layer: Curve stETH/ETH, Uniswap V3 wstETH/WETH, WETH/USDC, WBTC/WETH.
- Snapshot A là calibration/counterfactual; Snapshot B là out-of-sample trước biến động lớn. B hiện tại do N2 gửi; phải được N1 duyệt.
- H2/Q2 cần độ sâu DEX để mô phỏng slippage → lãi ròng liquidator → freeze/cascade. N3 bàn giao quan sát liquidity, KHÔNG tạo biến bad debt hay mô phỏng cascade.
- M5 là mô tả tương quan quan sát giữa time-based `LiquidationCall`, lượng debt repaid, ETH/USD, stETH/ETH depeg; không tuyên bố quan hệ nhân quả.

## Data Contract v1.0

- `fact_historical_liquidations`: PK `(tx_hash,log_index)`, USD event-time, khác giá bán sau slippage.
- `dim_asset_config.dex_depth_2pct_usd` là USD có price impact 2% ghi rõ pool/side/block/method trong manifest. N3 staging không thay thế dim_asset_config.
- `dim_asset_config.lambda_impact` và `gamma_exponent` là tham số calibration, không phải dữ liệu thị trường quan sát.
- Không thay `schema_storage_safe.py`, không tạo thêm cột cho sáu bảng chuẩn.

## Tích hợp nhóm

N3 -> N4/N5: Các dòng verified của `n3_dex_liquidity.parquet`, cùng `n3_dex_liquidity_quality.json` để fit hàm impact. N3 -> N1/N6: biểu đồ/table descriptive và quality JSON. N2: oracle tại snapshot và tỷ lệ Lido, không nhất thiết position data. N1: chốt fee-tier/cụ thể route và snapshot B.
