# Cascade demo — N5

Đây là bộ khung dùng **8 ví giả lập**, phục vụ phát triển và kiểm thử trước khi
nhận dữ liệu N2–N4 đã nghiệm thu. Không phải kết quả thực nghiệm hay mô phỏng đã
được kiểm định. Cơ sở: `research_design.md` mục 3.2, 4.2, 4.3 và
`DeFi_Network_Contagion_Research_and_Data_Specification.docx`.

## Chạy

Từ thư mục gốc, dùng Python 3.10+:

```powershell
python -m pip install -r src/requirements_cascade.txt
python -m unittest discover -s tests -p test_cascade.py -v
python -m src.models.cascade
```

Mặc định xuất `outputs/demo/cascade/cascade_rounds.parquet` và
`outputs/demo/cascade/cascade_summary.parquet`, gồm cả bốn chính sách, mỗi lượt
chạy có `run_id` riêng. Dùng `--output-dir` để đổi thư mục; chỉ dùng `--overwrite`
khi muốn ghi đè. Không tự đưa demo vào `data/marts/` chính thức.

## API và dữ liệu đầu vào

```python
from src.models.cascade import (
    CascadeConfig, synthetic_portfolio, simulate_cascade, export_results,
)

wallets, assets = synthetic_portfolio()
result = simulate_cascade(
    wallets, assets,
    CascadeConfig(cf_rule="dynamic_linear", lambda_impact=0.02),
    shocks={"WETH": -0.20, "wstETH": -0.24, "WBTC": -0.10},
    scenario_id="demo_combined",
)
export_results(result, "outputs/demo/custom_cascade")
```

- `Asset`: giá USD, LT, bonus dạng premium (`0.05` = 5%), độ sâu USD giả định.
- `Wallet`: ID duy nhất; collateral/debt là số lượng token đã chuẩn hóa decimals
  và lãi tích lũy; `collateral_enabled` mặc định True. Có thể truyền LT và bonus
  hiệu dụng theo từng tài sản qua `effective_lt`, `effective_bonus`.
- `shocks`: suất sinh lợi áp dụng một lần trước vòng 1 (`-0.20` = giảm 20%).
  Các token được shock độc lập; người gọi phải tự ánh xạ ETH/depeg sang LST.
- `CascadeResult`: các dòng vòng lặp, summary, số dư cuối, giá cuối và danh sách
  giao dịch `transactions` để kiểm tra số lượng token thực sự đã thay đổi.
- Hàm không sửa đối tượng đầu vào. Lõi mô phỏng chỉ dùng thư viện chuẩn Python;
  xuất Parquet cần `pyarrow`.

## Close Factor theo research

| Chính sách | Khi HF < 1 |
|---|---|
| `baseline_aave` | 50% nếu HF ≥ 0.95; 100% nếu HF < 0.95 |
| `fixed_50` | 50% |
| `fixed_100` | 100% |
| `dynamic_linear` | clip((1 − HF)/0.10, 0.20, 1.00) |

HF ≥ 1 hoặc không có nợ: không thanh lý. Baseline ở đây là quy tắc trong research,
không khẳng định tái hiện đầy đủ phiên bản hợp đồng Aave tại một block cụ thể.

## Quy tắc vòng lặp

1. Tính HF = tổng collateral enabled × giá × LT hiệu dụng / tổng nợ × giá.
   Không có nợ thì HF = vô cực. Chốt tập ví HF < 1 tại đầu vòng.
2. Xử lý theo ID ví tăng dần, tối đa một cặp collateral–debt mỗi ví mỗi vòng.
   Chọn cặp có lợi nhuận USD ròng cao nhất. Đây là giả định thực thi xác định;
   thứ tự liquidator thực tế và tối ưu chia nhỏ giao dịch chưa được mô hình hóa.
3. Repay USD = min(CF × dư nợ hiện tại của ví, dư nợ token được chọn,
   collateral USD khả dụng / (1 + bonus)). Không hoàn trả khoản nợ đã xử lý.
   Seize token = repay USD × (1 + bonus) / giá collateral đầu vòng.
4. Dòng bán = seize USD × `sell_fraction`. Tác động giá cuối =
   min(`max_price_impact`, lambda × (dòng bán USD / depth USD)^gamma).
   Depth và dòng bán đều tính theo giá đầu vòng. Dòng bán cộng dồn theo token
   trong vòng; vòng sau dùng lại cùng độ sâu USD giả định.
5. Slippage giao dịch = trung bình tác động trước/sau dòng bán của giao dịch
   (xấp xỉ hình thang, kể cả khi gamma khác 1). Lợi nhuận = seize USD ×
   (1 − slippage) × (1 − phí DEX) − repay USD − gas USD.
   Chỉ thực thi nếu lợi nhuận > 0. Khi `sell_fraction < 1`, collateral chưa bán
   được định giá theo cùng giá thực thi; lợi nhuận là giá trị kinh tế giả định,
   không phải toàn bộ tiền mặt đã thực thu. Chưa mô hình hóa vốn/flash loan.
6. Trừ chính xác lượng token nợ và collateral của các giao dịch thực thi.
   Giá dùng cho HF cố định trong vòng; cuối vòng, giá mới = giá đầu vòng ×
   (1 − tác động tổng dòng bán). Giá mới cũng định giá lại token nợ.
7. Tính lại HF cho vòng sau. Dừng khi không còn ví dưới ngưỡng, không còn
   giao dịch khả thi, hoặc đạt `max_rounds`. Ngưỡng `min_repay_usd` tránh giao
   dịch bụi. `converged=False` nếu phải dừng ở giới hạn vòng.

Bad debt cuối = tổng theo ví max(nợ USD − collateral enabled USD, 0).
Collateral chưa enabled không được tự kích hoạt để trả nợ. Freeze là dư nợ của
ví HF < 1 không thực thi được giao dịch trong vòng; không đồng nghĩa bad debt.
Chưa có lãi tích lũy theo thời gian, phí giao thức, Isolation Mode đầy đủ,
oracle lag, AMM chính xác, arbitrage hay cầu nối Compound.

## Schema đầu ra

Hai bảng có `run_id`, `scenario_id`, `cf_rule`, `model_version`,
`data_kind="synthetic"`. Arrow metadata cũng đánh dấu dữ liệu giả lập.

| Bảng | Các trường chính |
|---|---|
| `cascade_rounds` | `round` (bắt đầu 1), `newly_liquidatable_wallets`, `eligible_wallets`, `executed_liquidations`, `simulated_repaid_debt_usd`, `cumulative_repaid_usd`, `collateral_seized_usd`, `liquidator_profit_usd`, `liquidation_freeze_debt_usd`, `bad_debt_usd`, `remaining_debt_usd` |
| `cascade_summary` | `total_simulated_repaid_usd`, `cascade_amplification`, `total_rounds`, `initial_debt_usd`, `remaining_debt_usd`, `bad_debt_usd`, `bad_debt_ratio_pct`, `unique_liquidatable_wallets`, `sequential_liquidatable_wallets`, `total_liquidator_profit_usd`, `stop_reason`, `converged` |

Mỗi vòng lưu `prices_before_json`, `prices_after_json`, `sold_usd_json` và
`max_price_impact_pct` (0–100) để vẽ đường giá. Summary lưu `parameters_json` và
`shocks_json` để tái lập cấu hình.

`newly_liquidatable_wallets` đếm ví lần đầu HF < 1 trong lượt chạy, kể cả ví bị
freeze; `executed_liquidations` đếm giao dịch thực thi. Amplification = tổng
repay / repay vòng 1, bằng null nếu vòng 1 không repay. Một vòng không có giao
dịch vẫn được ghi để thể hiện trạng thái dừng. Giá trị repay USD dùng giá nợ
tại từng vòng, nên khi giá token nợ thay đổi, không lấy nợ USD ban đầu trừ
tổng repay USD để suy ra nợ USD cuối; cần đối chiếu sổ token `transactions`.

Đây là các marts thử nghiệm riêng, chưa ánh xạ vào `out_simulation_results`
trong `schema_storage_safe.py`. Trước khi tích hợp dữ liệu thật cần adapter
N2–N4 và các validation gates mục 3.3 của research.
