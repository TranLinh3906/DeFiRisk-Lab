"""Figures and Vietnamese report derived only from the corrected bundle."""
import json

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import pandas as pd


def render_report(out, comparison, grid, checks, networks, withdrawals):
    figures = out / 'figures'
    figures.mkdir()
    fig, axes = plt.subplots(1, 3, figsize=(13, 4), constrained_layout=True)
    for ax, sid in zip(axes, ['A', 'B1', 'B2']):
        g = comparison[comparison.snapshot_id.eq(sid)]
        pv = g.pivot(index='loss_stage', columns='is_recursive_loop', values='es99_loss_per_100k')
        pv = pv.rename(columns={False: 'Non-looping', True: 'Looping'})
        pv.plot.bar(ax=ax, rot=15)
        ax.set_title(sid)
        ax.set_ylabel('Conditional ES99 / 100,000 USD equity')
        ax.set_xlabel('Fixed shocked oracle')
    fig.savefig(figures / 'q1_pre_post_es99.png', dpi=160)
    plt.close(fig)
    fig, axes = plt.subplots(1, 3, figsize=(14, 4), constrained_layout=True)
    selected = grid[grid.scenario_id.eq('combined_eth20_depeg5') & grid.oracle_feedback]
    for ax, sid in zip(axes, ['A', 'B1', 'B2']):
        g = selected[selected.snapshot_id.eq(sid)]
        for cf, rows in g.groupby('cf_rule'):
            rows = rows.sort_values('depth_scale')
            ax.plot(rows.depth_scale, rows.post_execution_bad_debt_usd, marker='o', label=cf)
        ax.set_title(sid)
        ax.set_xlabel('Assumed DEX depth multiplier')
        ax.set_ylabel('Post-execution bad debt, USD')
        ax.legend(fontsize=7)
    fig.suptitle('Counterfactual feedback sensitivity; not observed oracle behavior')
    fig.savefig(figures / 'q2_depth_cf_sensitivity.png', dpi=160)
    plt.close(fig)
    fig, axes = plt.subplots(1, 3, figsize=(13, 4), constrained_layout=True)
    for ax, sid in zip(axes, ['A', 'B1', 'B2']):
        null = pd.read_csv(out / sid / 'null_networks.csv')
        ax.hist(null.score, bins=20, alpha=.7, label='Degree-preserving null')
        ax.axvline(networks[sid]['observed_score'], color='red', label='Observed exposures')
        ax.set_title(sid)
        ax.set_xlabel('Conditional token-network DebtRank')
        ax.legend(fontsize=7)
    fig.savefig(figures / 'q3_null_networks.png', dpi=160)
    plt.close(fig)
    endo = grid[grid.oracle_feedback]
    new = int(endo.newly_eligible_round_ge_2.max())
    report = f'''# Kết quả xử lý rà soát nghiên cứu ngày 26/10/2026

Bộ này thay thế các kết luận Q1–Q3 trong báo cáo hoàn thành cũ. Dữ liệu gốc và bundle cũ được giữ để truy nguyên.

## P0 — Dữ liệu và nghiệm thu

- Chuẩn hóa market Compound WETH từ đơn vị WETH sang USD bằng oracle WETH/USD cùng snapshot; giữ native_price/native_value và tỷ giá chuyển đổi.
- Mọi khoản rút vốn có cả lượng token và USD; chọn ví theo snapshot và điều kiện đủ ngưỡng thanh lý Aave trong kịch bản đã ghi rõ.
- Kết quả kiểm tra file/cột được gọi là mapping; kiểm tra số học được ghi riêng ở validation.json. Không dùng mapping 18/18 để tuyên bố nghiệm thu khoa học.
- Trạng thái nghiệm thu: **{checks['research_acceptance']}**.

## Q1

- {checks['mc_scenarios_per_snapshot']:,} vector shock được đánh giá trước và sau thanh lý bằng cùng một bộ giá, cùng cohort, cùng route, cùng phí và cùng quy tắc Close Factor.
- Mô phỏng giữ số dư và LT/bonus từng token, giới hạn miền quote, lượng bán cộng dồn theo pool và thực thi tới khi dừng. Không dùng đường trượt giá trung bình giữa các route.
- ES99 dùng toàn bộ số lần rút mẫu, gồm loss bằng 0. Loại equity không dương khỏi phép chia, vẫn giữ các ví này trong artifact và báo cáo.
- So sánh dùng trọng số bằng nhau giữa các tầng equity chung. Đây là điều chỉnh phân tầng, chưa phải ước lượng nhân quả của looping.
- Mann–Whitney BDR và KS HF được ghi theo tầng; bootstrap ES99 ghép cặp theo cùng scenario, có CI và hiệu chỉnh Holm. Kiểm định wallet dùng chung cú sốc chỉ có ý nghĩa mô tả liên hệ.
- 19 kịch bản stress là kiểm tra tất định; không gán tần suất stress bằng xác suất đời thực hay dùng ES99 trên 19 điểm như ước lượng đuôi thực nghiệm.
- Áp calibration trước A vào B1/B2 là phản thực hồi cứu, chưa phải kiểm định ngoài mẫu không nhìn trước.

![ES99 trước/sau thanh lý](figures/q1_pre_post_es99.png)

## Q2

- Đã chạy **{checks['grid_runs']}** tổ hợp snapshot × shock × độ sâu × CF × phản hồi giá.
- Độ sâu 0,5/1/2 là giả định phản thực trên cùng đường quote, không phải ba thị trường được quan sát độc lập.
- Bản fixed oracle, depth=1, baseline_aave khớp 57 lượt primary cũ: **{checks['approved_primary_regression_matches']}**.
- Đếm riêng giao dịch lặp lại, ví được thực thi lần đầu ở vòng sau và ví mới đủ điều kiện ở vòng sau. Ngoài miền quote là unknown; không coi đó là freeze đã được chứng minh.
- Bảng paired_policy_contrasts so sánh chính sách trong cùng snapshot/scenario/depth; không gán p-value ANOVA iid cho các ô mô phỏng phụ thuộc.

![Độ nhạy CF và độ sâu](figures/q2_depth_cf_sensitivity.png)

## Q3

- Fixed oracle không sinh ví mới đủ điều kiện qua phản hồi giá; kiểm tra zero-control: **{checks['fixed_oracle_newly_eligible_zero']}**.
- Bản phản thực truyền marginal cost suy từ đường DEX vào giá token bán, cập nhật cả collateral và debt. Giá được tính từ mức sau shock ban đầu, không nhân lặp lại impact toàn phần mỗi vòng. Mức truyền 100%, cap 15% là sensitivity chưa hiệu chỉnh từ oracle thực.
- Số ví mới đủ điều kiện lớn nhất trong một lượt phản thực: **{new}**. Chỉ số này là kết quả mô hình có điều kiện, chưa chứng minh H3 trên dữ liệu thực.
- Mạng giữ ví riêng lẻ, token và các lớp phơi nhiễm Aave/Compound. Null dùng double-edge swap trong từng lớp, bảo toàn bậc của từng ví và từng token; giữ tập trọng số cạnh của ví nhưng không giữ tổng trọng số mỗi token.
- Xuất eigenvector, degree, strength theo từng ví và phân phối null. DebtRank đo truyền distress trên phơi nhiễm, chưa đồng nhất với bad debt được thực thi. Chưa kết luận power-law hoặc trung tâm mạng gây lây lan.
- Compound: đã sửa USD; thu thập reserves, target reserves, pause, storefront và collateral inventory tại đủ 6 market–snapshot qua RPC chỉ đọc. Mô hình tách absorb (xóa nợ, nhận collateral, ghi credit base supply của người vay) và buyCollateral (mua từ inventory khi reserves dưới target, dùng discount riêng Comet). Chỉ bán khi route có quote trong miền và liquidator có lãi theo giả định. Base supply không phải collateral. Mẫu chỉ gồm ví overlap; không khẳng định mô phỏng toàn bộ thị trường hoặc làm tròn/lãi suất của hợp đồng. Ledger và summary riêng được bàn giao.

![So sánh mạng null](figures/q3_null_networks.png)

## P2 — Bảng và hình

Các hình trên được tạo trực tiếp từ CSV của bộ này. manifest.json băm các đầu vào, mã nguồn, CSV, Parquet, hình và báo cáo; validation.json ghi riêng kiểm tra hồi quy/số học và mức nghiệm thu.

## Chạy lại

```powershell
.\\.venv\\Scripts\\python.exe -B -X utf8 -m scripts.n5_resolve_research_review --output-dir outputs/N5_REVIEW_NEW
```

Tài liệu cơ chế Compound: https://docs.compound.finance/liquidation/
'''
    (out / 'REPORT.md').write_text(report, encoding='utf-8')
