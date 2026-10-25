# N5 — Kiểm định đầu vào, adapter và khung thí nghiệm

**Cập nhật 25/10/2026:** dùng [kết quả A/B1/B2](KET_QUA_N5_AB1B2_20261025.md)
và bundle `outputs/n5_preparation/n5_AB1B2_20261025_final/`.
Runner mặc định đọc DEX A/B1/B2 và bộ Compound gộp 25/10, hỗ trợ coverage Parquet.
Đã thêm điều tra tiny-shock/dust, baseline/graph ba snapshot, historical market proxy,
price-impact anchors và fixture Q1/Q3. Những phần bên dưới mô tả các phiên bản trước.

**Sau pull 9b34ff7:** xem [kết quả mới nhất](N5_SAU_PULL_9b34ff7.md) và
[bảng bàn giao N4](BAN_GIAO_N4_SAU_PULL_9b34ff7.md). Bundle:
`outputs/n5_preparation/n5_postpull_9b34ff7_20261023/`.
Có QA config A/B1/B2, HF tái dựng/tiny-shock, attribution theo code mới và
regression contracts đã rà soát marker. Các báo cáo ngày 23/10 trước pull là lịch sử.

**Cập nhật 23/10/2026:** validator đã hỗ trợ schema N4 nhiều snapshot, thêm
regression guards, attribution HF, mapping KPI và adapter/tracking Compound partial.
Xem [kết quả mới](CAP_NHAT_N5_20261023.md). Bundle mới:
`outputs/n5_preparation/offline_handoff_20261023_v2/` (unvalidated).

**Cập nhật 22/10/2026:** đã có bộ chuẩn bị offline gồm graph Aave, đối chiếu tổng
nợ, chẩn đoán N4, mapping DEX, QA N3/Compound partial và đóng gói demo/biểu đồ.
Xem [kết quả và cách chạy](KET_QUA_N5_TU_THUC_HIEN_20261022.md).
Đầu ra hiện tại: `outputs/n5_preparation/offline_handoff_20261022_v2/`.
Toàn bộ bộ chuẩn bị giữ trạng thái chưa nghiệm thu nghiên cứu.

Các module này chạy từ gốc dự án hiện tại, nhận đường dẫn tường minh. Chưa cần
di chuyển code N4 trong `DeFiRisk-Lab-updated-main/`. Không import/chạy crawler,
không đọc `.env`, không gọi RPC, không cài thư viện. Lõi `cascade.py` giữ nguyên.

## Thành phần

| File | Chức năng |
|---|---|
| `src/models/n5_inputs.py` | Đọc Parquet theo đường dẫn chỉ định; chuyển vị thế thành Wallet/Asset và bảng chuẩn hóa cho N4 |
| `src/models/n5_validation.py` | Kiểm schema, đối chiếu HF/số dư, các gate nghiệm thu, kiểm cửa sổ lịch sử và tính nhất quán đầu ra |
| `src/models/n5_experiments.py` | Benchmark một vòng, so sánh Cascade, sensitivity và xuất Parquet thử nghiệm |
| `scripts/n5_audit.py` | Audit chỉ đọc, xuất báo cáo JSON vào thư mục mới |
| `scripts/n5_demo.py` | Demo nhỏ bằng 8 ví giả lập, không đọc dữ liệu nghiên cứu |
| `tests/validation_report.md` | Kết quả kiểm tra và các mục đang chờ dữ liệu/đối soát |

## Chạy kiểm thử và demo

Dùng Python có sẵn `pyarrow` cho thao tác Parquet. Các kiểm thử tính toán dùng
thư viện chuẩn; phần Parquet của unittest bỏ qua nếu chưa có `pyarrow`. Không
cần cài hay nâng cấp thư viện trong `.venv` đang phục vụ Compound. Đợt triển khai
này đã kiểm tra bằng Python hệ thống 3.12, không dùng `.venv` của crawler.

```powershell
python -B -X utf8 -m unittest discover -s tests -p test_cascade.py -v
python -B -X utf8 -m unittest discover -s tests -p test_n5_preparation.py -v
python -B -X utf8 -m scripts.n5_demo --output-dir outputs/n5_preparation/my_new_demo
```

Chọn tên thư mục mới mỗi lần. Demo tạo `sensitivity.parquet` (64 lượt),
`benchmark_comparison.json` (4 chính sách, có đường giá từng vòng) và README.
Mọi kết quả đều là thử nghiệm, `validation_status=unvalidated`;
demo ghi rõ `data_kind=synthetic`. Không đưa vào marts chính thức.

Các test xuất Parquet dùng thư mục UUID riêng trong `outputs/n5_preparation/`,
tự xóa đúng các file thử của chúng. Điều này tránh lỗi quyền thư mục Windows
TEMP; không thay đổi phép kiểm tra trong bộ test Cascade cũ.

## Audit dữ liệu thật, không chạy mô phỏng

Ví dụ Snapshot B; cho Snapshot A đổi block sang `26179841` và bỏ
`--liquidations` vì bảng đang chọn là holdout B.

```powershell
python -B -X utf8 -m scripts.n5_audit `
  --positions data/processed/aave/positions_long.parquet `
  --reserves data/processed/aave/reserve_config.parquet `
  --prices data/processed/aave/oracle_prices.parquet `
  --emode data/processed/aave/emode_membership.parquet `
  --references data/processed/aave/account_reference.parquet `
  --metadata data/processed/aave/snapshot_metadata.json `
  --liquidations data/staging/n3_liquidations_holdout_24h.parquet `
  --snapshot-block 26194188 `
  --n4-marts DeFiRisk-Lab-updated-main/data/marts `
  --output-dir outputs/n5_preparation/my_new_audit_B
```

Audit đọc toàn bộ bảng đầu vào được chỉ định, không quét thư mục Compound.
Các file Parquet được kiểm tra size/mtime trước và sau khi đọc; thay đổi trong
lúc đọc thì từ chối dùng. Với marts N4, chỉ kiểm schema của tối đa 2.000 dòng
đầu mỗi bảng để giới hạn tài nguyên; đối chiếu HF đọc đầy đủ `borrower_risk`.
Chưa chứng nhận toàn bộ dòng stress hoặc KPI. Có thể bỏ `--n4-marts` để chỉ
kiểm định N2. Exit code 1 kèm `audit.json` nghĩa là kiểm định có mục không đạt,
không phải crawler bị lỗi.

Gate HF dùng median absolute error <0,005. Kiểm tra số dư theo tài khoản dùng
ngưỡng tương đối 1% nghiêm ngặt, có lưu sai số USD tuyệt đối: khoản nợ bụi có
thể vượt 1% dù chênh lệch rất nhỏ. Chưa tự nới ngưỡng. Gate toàn reserve 1%
và hit-rate lịch sử 85% chỉ có thể đánh giá khi có bằng chứng độc lập đủ phạm vi.
Kiểm tra mẫu không thay thế kiểm tra toàn giao thức. Trạng thái `passed` của
schema hoặc HF không có nghĩa toàn nghiên cứu đã được nghiệm thu.

## API nối dữ liệu

```python
from src.models.n5_inputs import read_rows, prepare_portfolio
from src.models.n5_validation import validate_inputs

portfolio = prepare_portfolio(
    read_rows("data/processed/aave/positions_long.parquet"),
    read_rows("data/processed/aave/reserve_config.parquet"),
    read_rows("data/processed/aave/oracle_prices.parquet"),
    snapshot_block=26179841,
    emode_membership=read_rows("data/processed/aave/emode_membership.parquet"),
    depth_usd=depth_by_token_address,  # phải cung cấp rõ nguồn/giả định
)
report = validate_inputs(portfolio, read_rows("data/processed/aave/account_reference.parquet"))
```

- Khóa nội bộ là địa chỉ token, tránh trùng symbol. Chọn đúng một numeric block
  và protocol; adapter không đoán ánh xạ A/B, không trộn Aave với Compound.
- Số dư đã chuẩn hóa decimals/lãi; không nhân index hay chia decimals lần nữa.
- Cờ collateral, E-Mode user, giá, LT/bonus bắt buộc rõ ràng; không điền thiếu
  bằng 0. Phát hiện khóa trùng, giá/số dư không hữu hạn hoặc âm, decimals sai,
  giá nhúng khác oracle và LT đã tính khác LT tái dựng.
- LT tiêu chuẩn đọc `lt_std` tỷ lệ hoặc `lt_bps`; bonus đọc premium hoặc raw bps
  với `10500 -> 0.05`. E-Mode cần bảng membership; tài sản thuộc category và
  được bật collateral dùng LT/bonus hiệu dụng.
- `portfolio.positions` chứa các cột `snapshot_block`, `protocol_id`,
  `wallet_address`, `token_address`, `collateral_amount`, `debt_amount`,
  `is_collateral_enabled`, `effective_lt`, `effective_bonus`, `price_usd`,
  `symbol`: có thể truyền cho `run_stress_test` của N4 sau khi thống nhất import.
- `depth_usd` bắt buộc tường minh. Audit cấp giá trị placeholder chỉ để dựng
  đối tượng, không dùng nó để mô phỏng giá hay công bố kết quả thanh khoản.

## Sensitivity và benchmark

```python
from src.models.n5_experiments import run_sensitivity_analysis, compare_static_cascade
from src.models.cascade import synthetic_portfolio

wallets, assets = synthetic_portfolio()
symbols = {token: token for token in assets}
rows = run_sensitivity_analysis(
    wallets, assets, symbols=symbols,
    policies=("baseline_aave", "fixed_50", "fixed_100", "dynamic_linear"),
    impact_parameters=(0.0, 0.02), sell_fractions=(0.5, 1.0),
    eth_shocks=(0.0, -0.1, -0.2, -0.3),
    depeg_shocks=(-0.01, -0.02, -0.03, -0.04, -0.05, -0.07, -0.1),
    depth_multipliers=(0.5, 1.0, 2.0),  # 1.344 lượt, chỉ chạy khi sẵn sàng
    context={"data_kind": "synthetic"},
)
comparison = compare_static_cascade(wallets, assets, context={"data_kind": "synthetic"})
```

Sensitivity giới hạn mặc định 5.000 lượt và chỉ giữ summary. Giá LST chịu
ETH shock và depeg theo tích `(1+eth)*(1+depeg)-1`; ví dụ −20% và −5% thành
−24%. Mặc định chỉ stETH/wstETH nhận stETH depeg; weETH/cbETH/rETH vẫn chịu ETH
shock, không tự nhận thêm stETH depeg. Có thể truyền rõ `eth_symbols` và
`depeg_symbols` khi cần mở rộng ánh xạ tài sản.
Giữ nguyên tỷ lệ quy đổi wstETH trong một lượt. Người gọi phải truyền vị thế/giá
ban đầu chưa stress để tránh shock hai lần. Chưa ánh xạ price impact giữa các
token qua AMM hay oracle lag.

Benchmark chạy đúng một vòng, lambda=0, giá sau shock cố định; vẫn giữ gas,
phí DEX và quy tắc một cặp collateral–debt mỗi ví của lõi hiện tại. Đây là
benchmark cùng giả định thực thi với lõi, chưa phải bộ thanh lý đầy đủ mọi cặp
trong một vòng. `converged=False` khi còn ví đủ điều kiện sau vòng benchmark là
có chủ ý. `cascade_to_static_ratio` khác với `cascade_amplification` (tổng repay
Cascade / repay vòng 1 của chính Cascade). Mẫu số bằng 0 trả null.

`validate_model_outputs` kiểm schema, tổng vòng/summary/giao dịch, đường giá,
amplification và bảo toàn lượng token nợ/collateral. Không suy ra nợ cuối bằng
nợ USD ban đầu trừ tổng repay USD khi token nợ cũng đổi giá. Hàm này không
thay thế kiểm định lịch sử hoặc kiểm chứng đầy đủ hợp đồng Aave.

Không tự xuất marts chính thức hoặc manifest nghiệm thu. Việc di chuyển code
N4, thống nhất cấu trúc import, hiệu chỉnh DEX depth/price impact và tích hợp
Compound vẫn để giai đoạn sau theo yêu cầu.
