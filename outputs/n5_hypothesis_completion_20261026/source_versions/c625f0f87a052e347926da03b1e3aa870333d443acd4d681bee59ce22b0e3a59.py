"""Generate a hypothesis-specific conclusion ledger from completed artifacts."""
import json
import hashlib
import shutil
import sys
from importlib.metadata import version
from pathlib import Path
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt


def add_turning_point_intervals(grid, shapes, n_boot=2000):
    """Conservative refinement: a U needs its turning-point CI inside the CF grid."""
    for result in shapes:
        g = grid[(grid.depth_scale == result['depth_scale']) & grid.feedback_mode.eq(result['feedback_mode'])]
        curves = g.pivot(index=['snapshot_id','scenario_id'], columns='fixed_cf', values='post_execution_bdr_pct')
        x = curves.columns.to_numpy(float)
        design = np.column_stack([np.ones(len(x)), x, x*x])
        coefficients = curves.to_numpy() @ np.linalg.pinv(design).T
        rng = np.random.default_rng(42)
        boot = np.array([coefficients[rng.integers(0,len(coefficients),len(coefficients))].mean(0) for _ in range(n_boot)])
        valid = boot[:,2] > 1e-12
        points = -boot[valid,1] / (2*boot[valid,2])
        if len(points):
            low, high = np.quantile(points,[.025,.975])
            result['turning_point_ci95_positive_curvature_draws'] = [float(low),float(high)]
            result['positive_curvature_bootstrap_fraction'] = float(valid.mean())
            result['turning_point_ci_entirely_inside_grid'] = bool(valid.mean()>=.95 and low>x.min() and high<x.max())
        else:
            result['turning_point_ci95_positive_curvature_draws'] = None
            result['positive_curvature_bootstrap_fraction'] = 0.
            result['turning_point_ci_entirely_inside_grid'] = False
        result['u_shape_supported_with_turning_ci'] = bool(result['u_shape_supported_on_grid'] and result['turning_point_ci_entirely_inside_grid'])
    return shapes


def build_report(root):
    required = ['prepare_completed.json','q1_completed.json','q2_completed.json','q3_completed.json']
    if any(not (root / name).exists() for name in required):
        raise ValueError('All analytical stages must finish before reporting')
    feedback = json.loads((root / 'feedback/validation.json').read_text(encoding='utf-8'))
    q1 = json.loads((root / 'q1/paired_hypothesis_tests.json').read_text(encoding='utf-8'))
    convexity = json.loads((root / 'q1/convexity_tests.json').read_text(encoding='utf-8'))
    shapes = json.loads((root / 'q2/u_shape_tests.json').read_text(encoding='utf-8'))
    grid = pd.read_csv(root / 'q2/dense_cf_grid.csv')
    shapes = add_turning_point_intervals(grid, shapes)
    (root / 'q2/u_shape_tests_with_turning_ci.json').write_text(json.dumps(shapes,indent=2),encoding='utf-8')
    contrasts = pd.read_csv(root / 'q2/h2a_paired_contrasts.csv')
    transmission = pd.read_csv(root / 'q3/direct_induced_summary.csv')
    interventions = pd.read_csv(root / 'q3/node_edge_interventions.csv')
    networks = json.loads((root / 'q3/incremental_loss_network_nulls.json').read_text(encoding='utf-8'))
    calibration = pd.read_parquet(root / 'feedback/temporal_observations.parquet')
    outcomes = [metric for r in q1 for metric in r['matched_outcomes']]
    order = np.argsort([m['paired_wilcoxon_p'] for m in outcomes])
    running = 0.
    for rank, ix in enumerate(order):
        running = max(running, min(1., outcomes[ix]['paired_wilcoxon_p'] * (len(outcomes)-rank)))
        outcomes[ix]['p_holm_all_matched_outcomes'] = running
    (root / 'q1/paired_hypothesis_tests_adjusted.json').write_text(json.dumps(q1, indent=2), encoding='utf-8')
    balance, regressions = [], {}
    from src.models.n5_network_statistics import wallet_associations
    for sid in ['A','B1','B2']:
        baseline = pd.read_parquet(Path('outputs/n5_review_corrected_20261026') / sid / 'baseline_wallet_kpis.parquet')
        nodes = pd.read_parquet(Path('outputs/n5_review_corrected_20261026') / sid / 'network_wallet_nodes.parquet')
        pair = pd.read_csv(root / f'q1/{sid}/equity_pairs.csv')
        ref = baseline.set_index('wallet_address')
        before_a = np.log(baseline.loc[baseline.net_equity_usd.gt(0) & baseline.is_recursive_loop, 'net_equity_usd'])
        before_b = np.log(baseline.loc[baseline.net_equity_usd.gt(0) & ~baseline.is_recursive_loop, 'net_equity_usd'])
        after_a = np.log(ref.loc[pair.looping_wallet, 'net_equity_usd'])
        after_b = np.log(ref.loc[pair.non_looping_wallet, 'net_equity_usd'])
        def smd(a,b):
            return float((np.mean(a)-np.mean(b))/np.sqrt((np.var(a,ddof=1)+np.var(b,ddof=1))/2))
        balance.append(dict(snapshot_id=sid,pairs=len(pair),log_equity_smd_before=smd(before_a,before_b),
                            log_equity_smd_after=smd(after_a,after_b),max_log_equity_gap=float(pair.absolute_log_equity_gap.max())))
        deltas = [pd.read_parquet(p).set_index('wallet_address').positive_incremental_bad_debt_usd
                  for p in (root / f'q3/{sid}').glob('*full_sensitivity.parquet')]
        mean = pd.concat(deltas, axis=1).mean(axis=1)
        if mean.eq(0).all():
            regressions[sid] = dict(status='not_identified_no_incremental_losses')
        else:
            risk = baseline[['wallet_address','net_equity_usd']].copy()
            risk['mean_bad_debt_ratio_t0'] = baseline.wallet_address.map(mean).to_numpy() / baseline.debt_usd.to_numpy()
            if risk.loc[risk.net_equity_usd.gt(0),'mean_bad_debt_ratio_t0'].nunique() < 2:
                regressions[sid] = dict(status='not_identified_no_outcome_variation_in_positive_equity_cohort')
            else:
                regressions[sid] = wallet_associations(nodes,risk,baseline)
                regressions[sid]['outcome'] = 'log1p_mean_positive_incremental_bad_debt_percent_t0_under_pure_depeg_full_feedback_sensitivity'
                regressions[sid]['inference_scope'] = 'conditional_model_association_HC3_not_causal_and_not_network_independence'
    pd.DataFrame(balance).to_csv(root / 'q1/equity_balance_before_after.csv',index=False)
    (root / 'q3/incremental_loss_regressions.json').write_text(json.dumps(regressions,indent=2,allow_nan=False),encoding='utf-8')
    method_path=Path('src/models/n5_network_statistics.py')
    method_hash=hashlib.sha256(method_path.read_bytes()).hexdigest()
    archived=root/'source_versions'/(method_hash+'.py')
    if not archived.exists(): shutil.copyfile(method_path,archived)
    (root/'q3/incremental_regression_method.json').write_text(json.dumps(dict(original_path=str(method_path),
        archived_path=archived.relative_to(root).as_posix(),sha256=method_hash),indent=2),encoding='utf-8')
    h1 = []
    for row in q1:
        both = all(m['ci95_low'] > 0 and m['p_holm_all_matched_outcomes'] < .05 for m in row['matched_outcomes'])
        h1.append(dict(snapshot=row['snapshot_id'], distribution=row['distribution'], pairs=row['pairs'],
            es99_looping=row['es99_looping'], es99_non_looping=row['es99_non_looping'],
            es99_difference=row['es99_diff'], es99_ci_low=row['es99_diff_ci95'][0],
            es99_ci_high=row['es99_diff_ci95'][1], both_PBD_and_BDR_positive=both))
    h1 = pd.DataFrame(h1)
    h1.to_csv(root / 'q1/hypothesis_evidence.csv', index=False)
    main = h1[h1.distribution.eq('pre_event_t4_1d')]
    h1a = ('supported_in_all_primary_snapshot_matched_associations' if main.both_PBD_and_BDR_positive.all()
           else 'supported_in_some_primary_snapshots' if main.both_PBD_and_BDR_positive.any()
           else 'not_supported_in_primary_pre_event_calibration')
    h1b = ('positive_curvature_in_some_conditional_stress_curves' if any(r['positive_quadratic_curvature_supported'] for r in convexity)
           else 'positive_curvature_not_supported_on_tested_grid')
    full = contrasts[contrasts.feedback_mode.eq('full_sensitivity')]
    sale_delta = full.delta_fixed100_minus_fixed50_round_1_sold_collateral_usd
    new_delta = full.delta_fixed100_minus_fixed50_newly_eligible_round_ge_2
    joint = int(((sale_delta > 1e-8) & (new_delta > 0)).sum())
    h2a = 'conditional_support_in_some_cases_not_universal' if joint else 'joint_sales_and_new_eligibility_increase_not_supported'
    u_count = sum(r['u_shape_supported_with_turning_ci'] for r in shapes)
    h2b = 'U_shape_in_selected_grid_averages_only' if u_count else 'U_shape_not_supported_by_boundary_and_turning_point_tests'
    calibrated = transmission[transmission.feedback_mode.eq('predictive_proxy')]
    sensitivity = transmission[transmission.feedback_mode.eq('full_sensitivity')]
    h3a = ('modeled_spillover_under_predictive_proxy_not_causal_confirmation' if calibrated.induced_healthy_other_wallets.gt(0).any()
           else 'no_induced_healthy_other_wallets_under_validated_predictive_rule')
    ledger = pd.DataFrame([
        dict(hypothesis='H1a', tests='matched equity pairs; paired PBD and BDR; pre-event calibration and distribution sensitivity', support=h1a),
        dict(hypothesis='H1b', tests='matched ES99 paired bootstrap; direct quadratic and discrete-convexity tests', support=h1b),
        dict(hypothesis='H2a', tests='actual round-1 collateral sales; CF100-CF50 paired contrasts; newly eligible wallets', support=h2a),
        dict(hypothesis='H2b', tests='nine fixed CF levels; both boundary slopes; interior turning point; block resampling', support=h2b),
        dict(hypothesis='H3a', tests='B1 temporal training B2 held-out validation; pure depeg; HF0>=1.10; post-shock healthy; other assets', support=h3a),
        dict(hypothesis='H3b', tests='incremental loss association; three network-label nulls; top-node/matched-random source and edge interventions',
             support='conditional_structural_diagnostics_not_power_law_or_causal_confirmation')])
    ledger['implementation_status'] = 'completed_on_declared_data_and_scope'
    ledger['causal_acceptance'] = 'not_established_by_observational_or_counterfactual_tests'
    ledger.to_csv(root / 'hypothesis_conclusions.csv', index=False)
    figures = root / 'figures'
    figures.mkdir(exist_ok=True)
    fig, axes = plt.subplots(1,3,figsize=(15,4),constrained_layout=True)
    for sid, ax in zip(['A','B1','B2'], axes):
        p = h1[h1.snapshot.eq(sid)].set_index('distribution')[['es99_looping','es99_non_looping']]
        p.columns=['Looping','Non-looping']
        p.plot.bar(ax=ax, rot=25)
        ax.set_title(sid)
        ax.set_ylabel('Matched ES99 / USD 100,000 initial equity')
        ax.set_xlabel('Declared distribution and horizon')
    fig.savefig(figures / 'q1_distribution_sensitivity.png',dpi=160)
    plt.close(fig)
    fig, axes = plt.subplots(1,3,figsize=(14,4),constrained_layout=True)
    for depth,ax in zip([.5,1.,2.],axes):
        sub=grid[(grid.depth_scale==depth)&grid.feedback_mode.eq('full_sensitivity')]
        for sid,g in sub.groupby('snapshot_id'):
            curve=g.groupby('fixed_cf').post_execution_bdr_pct.mean()
            ax.plot(curve.index,curve.values,marker='o',label=sid)
        ax.set_title(f'DEX depth x{depth}')
        ax.set_xlabel('Fixed close factor')
        ax.set_ylabel('Mean bad debt / initial cohort debt (%)')
        ax.legend()
    fig.suptitle('Finite-grid counterfactual sensitivity; full feedback is not observed behavior')
    fig.savefig(figures / 'q2_dense_cf_response.png',dpi=160)
    plt.close(fig)
    fig,axes=plt.subplots(1,3,figsize=(13,4),constrained_layout=True)
    for sid,ax in zip(['A','B1','B2'],axes):
        for mode,g in transmission[transmission.snapshot_id.eq(sid)].groupby('feedback_mode'):
            g=g.sort_values('depeg_shock')
            ax.plot(-100*g.depeg_shock,100*g.induced_transition_rate,marker='o',label=mode)
        ax.set_title(sid)
        ax.set_xlabel('Pure stETH depeg (%)')
        ax.set_ylabel('Induced transitions among healthy other-asset wallets (%)')
        ax.legend(fontsize=7)
    fig.savefig(figures / 'q3_direct_vs_induced.png',dpi=160)
    plt.close(fig)
    h1a_text = ('Được ủng hộ trong cả ba snapshot của thiết kế chính' if main.both_PBD_and_BDR_positive.all()
               else 'Chỉ được ủng hộ ở một số snapshot' if main.both_PBD_and_BDR_positive.any()
               else 'Không được ủng hộ trong thiết kế hiệu chỉnh trước sự kiện')
    h1b_text = ('Có độ cong quadratic dương ở một số điều kiện, chưa đồng nghĩa lồi trên toàn miền' if any(r['positive_quadratic_curvature_supported'] for r in convexity)
               else 'Chưa có bằng chứng độ cong dương trên lưới đã kiểm tra')
    software = {name:version(name) for name in ['numpy','pandas','scipy','statsmodels','pyarrow','matplotlib']}
    software['python'] = sys.version
    (root/'software_versions.json').write_text(json.dumps(software,indent=2),encoding='utf-8')
    primary_table = '\n'.join(f"| {r.snapshot} | {r.pairs} | {r.es99_looping:.4f} | {r.es99_non_looping:.4f} |" for r in main.itertuples())
    report = f'''# Hoàn thiện kiểm định Q1–Q3 và sáu giả thuyết — 26/10/2026

Gói này hoàn thiện các phép kiểm định được yêu cầu, thay cho việc chỉ suy luận từ mô tả hoặc số lượng test đạt. Kết luận được phân theo từng giả thuyết. Kết quả không ủng hộ giả thuyết được giữ nguyên; không điều chỉnh dữ liệu để ép giả thuyết đúng.

## Những phần đã bổ sung

- Thu thập riêng 366 mức giá trước B1 và trước B2; A dùng 366 mức giá trước A. Mọi quote hiệu chỉnh Q1 đều trước snapshot tương ứng.
- Thu thập giai đoạn khủng hoảng tháng 5–7/2022: 90/92 ngày có đủ hai giá tươi trong giờ cuối ngày. Hai ngày thiếu được báo rõ trong coverage.json, không nội suy và không tính lợi suất bắc qua khoảng trống.
- Mỗi snapshot chạy 10.000 kịch bản ở bốn phân phối: t-copula và Gaussian-copula từ cửa sổ trước sự kiện, cùng bootstrap chung ETH/depeg khủng hoảng ở kỳ hạn 1 và 5 ngày. Tổng **120.000** kết quả kịch bản. Kỳ hạn 5 ngày là shock đầu-cuối, chưa phải mô phỏng đường giá từng ngày với tái cân bằng/lãi suất.
- Ghép equity 1:1 không lặp ví đối chứng, caliper log-equity 0,1; có bảng standardized mean difference trước/sau ghép. Không ghép HF/leverage vì đây có thể là biến trung gian của chiến lược. Các đặc điểm chưa quan sát vẫn có thể gây nhiễu. Hiệu chỉnh Holm trên toàn bộ 24 so sánh PBD/BDR theo snapshot/phân phối.
- Đo trực tiếp độ cong loss-gap trên 31 mức depeg, ở ETH shock 0% và -20%; bootstrap theo cặp ví giữ nguyên trục shock.
- Lưới CF gồm 9 mức cố định 20%–100%, 3 độ sâu, 6 shock, 3 snapshot và 3 chế độ feedback: **{len(grid)} ô**. Chế độ predictive-proxy bằng fixed khi dữ liệu không ủng hộ hệ số truyền dương.
- H3 tách loss trực tiếp và loss tăng thêm do feedback; chỉ đếm đúng nhóm HF ban đầu ≥1,10, còn khỏe sau shock ngoại sinh, có collateral khác STETH/WSTETH ít nhất 10% và 100 USD.
- Kiểm định và hồi quy centrality với **loss tăng thêm**, thêm null có điều kiện degree/exposure và can thiệp chặn nguồn thanh lý tại top 1% ví, 19 tập ví ngẫu nhiên ghép degree/exposure, cùng từng route. Giữ nguyên số dư tài chính; loại ví can thiệp khỏi thước đo người nhận để tránh tạo tác cơ học. Các network-label null là đối chứng ghép tên ví trong cấu trúc cố định, khác với rewiring giữ bậc đã có ở gói trước.

## Kết quả hiệu chỉnh và kiểm định thời gian

B1 dùng để ước lượng dự báo phản hồi DEX→oracle; B2 được giữ riêng để kiểm tra. Có {feedback['training_rows']} dòng train và {feedback['held_out_rows']} dòng test, với feature luôn trước target một block.

- Hệ số DEX cộng các độ trễ: `{feedback['summed_lagged_DEX_coefficient']:.6g}`, CI HAC 95% `{feedback['hac_ci95']}`.
- RMSE ngoài mẫu: `{feedback['held_out_rmse']:.6g}`; baseline không thay đổi: `{feedback['zero_change_baseline_rmse']:.6g}`.
- Có cải thiện ngoài mẫu: **{feedback['predictive_improvement']}**. Quy tắc chọn proxy cho kết quả **{feedback['calibrated_predictive_proxy']}**.
- Biến động tỷ giá oracle wstETH/WETH trong các cửa sổ này rất nhỏ: độ lệch chuẩn target `{calibration.target_next_oracle_return.std():.6g}`. Không diễn giải hệ số gần 0 như bằng chứng mọi oracle đều không phản ứng trong mọi khủng hoảng.

Đây là kiểm định dự báo bằng dữ liệu quan sát, chưa nhận dạng tác động nhân quả. Khối lượng collateral bị tịch thu chỉ là proxy dòng bán, không chứng minh liquidator đã bán ngay trên DEX. Các cửa sổ peak được chọn hồi cứu theo giao thức đã chốt từ trước, không chọn lại theo kết quả fit.

## Bảng kết luận cho sáu giả thuyết

| Giả thuyết | Kết luận từ kiểm định hiện tại |
|---|---|
| H1a | {h1a_text}. Có {int(main.both_PBD_and_BDR_positive.sum())}/3 snapshot thỏa đồng thời CI dương và p Holm <0,05 cho PBD và BDR trong thiết kế chính. Xem thêm các phân phối khủng hoảng trước khi suy rộng. |
| H1b | {h1b_text}. ES99 lớn hơn trung vị không tự chứng minh khuếch đại do looping. Không chia cho trung vị bằng 0. |
| H2a | Trong {len(full)} cặp full-feedback, có {joint} cặp đồng thời tăng lượng bán vòng đầu và số ví mới đủ điều kiện khi CF100 thay CF50. Đây là mức ủng hộ có điều kiện, không phải quy luật tổng quát. |
| H2b | Có {u_count}/{len(shapes)} mặt cắt trung bình thỏa kiểm tra hai độ dốc biên và CI điểm cực tiểu nằm hoàn toàn trong miền CF. Không coi quadratic dương đơn thuần là chữ U. |
| H3a | Proxy được kiểm định tạo tổng {int(calibrated.induced_healthy_other_wallets.sum())} lượt ví khỏe/tài sản khác bị kích hoạt trong 12 tổ hợp; full-feedback sensitivity tạo {int(sensitivity.induced_healthy_other_wallets.sum())}. Đây là lượt theo kịch bản, không phải số ví duy nhất; mô phỏng sensitivity không xác nhận lây lan thực tế. |
| H3b | Đã đo tổn thất lây lan tăng thêm, kiểm định null và can thiệp nguồn/cạnh. Đây là bằng chứng trong mô hình có điều kiện, chưa xác nhận power-law hoặc cơ chế nhân quả ngoài thực tế. |

Số lượng kịch bản chạy, test phần mềm đạt hoặc mapping đủ KPI không thay thế các kết luận riêng trong bảng trên.

## Câu trả lời cho Q1–Q3

**Q1:** Định lượng được chênh lệch giữa các nhóm vị thế đại diện cho looping/non-looping trong từng snapshot, cùng equity gần nhau và cùng kịch bản. Dưới thiết kế chính hiệu chỉnh trước sự kiện, ES99 sau thanh lý trên 100.000 USD equity là:

| Snapshot | Cặp equity | ES99 looping | ES99 non-looping |
|---|---:|---:|---:|
{primary_table}

So sánh thêm 1 và 5 ngày khủng hoảng được lưu riêng; không coi 5 ngày là cùng kỳ hạn với 1 ngày. Các nhóm không được gán ngẫu nhiên chiến lược nên chênh lệch chưa phải tác động nhân quả của việc thực hiện vòng lặp.

**Q2:** Đã có phản thực định lượng trên 9 CF với cùng shock, độ sâu, cohort và giả định feedback; đo được thu hồi nợ, bán vòng đầu, bad debt và ví mới bị kích hoạt. Kết quả phụ thuộc điều kiện, không suy ra một CF tối ưu chung hoặc mặc định tồn tại chữ U.

**Q3:** Đã tách chuyển trạng thái trực tiếp và phần tăng thêm trong mô hình, đo đúng tập ví khỏe/tài sản khác, đồng thời kiểm định dự báo phản hồi bằng B2 giữ riêng. Việc không quan sát được phản hồi dương có khả năng dự báo là kết quả dữ liệu trong cửa sổ kiểm tra; chưa nhận dạng tác động nhân quả cho toàn thị trường. Kết quả full-feedback chỉ là sensitivity, không dùng thay bằng chứng thực nghiệm.

## Miền hiệu lực

Chỉ ba route đã chốt và miền quote được quan sát được dùng để thực thi; vượt miền hoặc thiếu route là unknown, không chứng minh freeze. Phí gas 5 USD và việc giữ nguyên danh mục khi áp shock là giả định nghiên cứu. Bootstrap theo ví/scenario và thay đổi phân phối không loại bỏ mọi bất định về hiệu chỉnh từ mẫu lịch sử hữu hạn. Các CI về độ cong và chữ U mô tả độ bền trên lưới/các block kịch bản đã chọn, không phải bằng chứng nhân quả trên tổng thể thị trường.

## Hình từ artifact

![Q1](figures/q1_distribution_sensitivity.png)

![Q2](figures/q2_dense_cf_response.png)

![Q3](figures/q3_direct_vs_induced.png)

## Nguồn và tái lập

- `preregistered_settings.json`: thiết kế đóng trước khi chạy; `input_freeze.json`: hash dữ liệu quan sát trước khi tính.
- `q1/paired_hypothesis_tests.json`, `q1/convexity_tests.json`: kết quả đầy đủ từng snapshot/phân phối.
- `q2/dense_cf_grid.csv`, `q2/u_shape_tests.json`, `q2/h2a_paired_contrasts.csv`.
- `q2/u_shape_tests_with_turning_ci.json`: CI điểm đảo chiều và tiêu chí chữ U chặt hơn.
- `feedback/validation.json`, `q3/direct_induced_summary.csv`, `q3/incremental_loss_network_nulls.json`, `q3/node_edge_interventions.csv`.
- `q1/equity_balance_before_after.csv`, `q1/paired_hypothesis_tests_adjusted.json`, `q3/incremental_loss_regressions.json`.
- `*_completed.json` và `source_versions/`: mã đúng phiên bản đã thực hiện từng giai đoạn, kể cả khi sửa bước báo cáo sau đó.
- `software_versions.json`: phiên bản phần mềm; thiết kế đóng trong kho trước lượt chạy là giao thức nội bộ, chưa phải đăng ký nghiên cứu tại bên thứ ba.

```powershell
.\\.venv\\Scripts\\python.exe -B -X utf8 -m scripts.n5_collect_pre_event_market
.\\.venv\\Scripts\\python.exe -B -X utf8 -m scripts.n5_complete_hypotheses --output-dir outputs/N5_HYPOTHESES_NEW
```

Giá lịch sử lấy qua [API chính thức DefiLlama](https://api-docs.defillama.com/). Quote thị trường có thể không hoàn toàn đồng thời; không dùng thay oracle Aave. Chênh lệch giữa snapshot và phân phối được báo cáo, không tuyên bố miễn nhiễm hay khuếch đại tổng quát khi bằng chứng không đủ.
'''
    (root / 'REPORT.md').write_text(report, encoding='utf-8')
    validation = dict(expected_mc_scenarios=120000, actual_mc_scenarios=sum(
        len(pd.read_parquet(p)) for p in root.glob('q1/*/*_matched_scenarios.parquet')),
        dense_cf_rows=len(grid), expected_dense_cf_rows=1458,
        fixed_zero_induced=bool(grid[grid.feedback_mode.eq('fixed')].newly_eligible_round_ge_2.eq(0).all()),
        no_round_limits=bool(~grid.stop_reason.eq('round_limit').any()),
        all_six_hypotheses_have_explicit_test_and_conclusion=len(ledger)==6,
        temporal_training_precedes_test=feedback['last_training_timestamp'] < feedback['first_held_out_timestamp'],
        scientific_acceptance='hypothesis_specific_conditional;_causal_feedback_not_identified')
    if validation['actual_mc_scenarios'] != 120000 or len(grid)!=1458 or not validation['fixed_zero_induced'] or not validation['no_round_limits']:
        raise ValueError(f'Incomplete or invalid final artifacts: {validation}')
    (root / 'validation.json').write_text(json.dumps(validation, indent=2), encoding='utf-8')
