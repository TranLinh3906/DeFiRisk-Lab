"""Build citation tables from the sealed release; never rerun a model or edit it."""
from __future__ import annotations

import ast
import hashlib
import json
import re
import subprocess
import traceback
from datetime import datetime, timezone
from pathlib import Path, PureWindowsPath

import numpy as np
import pandas as pd

from src.models.n5_readiness import verify_manifest
from src.models.n5_release import scientific_checks, sha256, verify_release

ROOT = Path(__file__).resolve().parents[1]
RELEASE = ROOT / 'outputs/n5_official_release_20261026'
RESULTS = RELEASE / 'results'
OUT = ROOT / 'outputs/n5_citation_pack_20261026'


def read(path):
    return json.loads(path.read_text(encoding='utf-8'))


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False), encoding='utf-8')


def write_csv(name, rows):
    pd.DataFrame(rows).to_csv(OUT / name, index=False, encoding='utf-8-sig', float_format='%.17g')


def table(rows, columns):
    def cell(value):
        return str(value).replace('|', '\\|').replace('\n', '<br>')
    return '\n'.join(['| ' + ' | '.join(columns) + ' |',
                      '| ' + ' | '.join(['---'] * len(columns)) + ' |'] +
                     ['| ' + ' | '.join(cell(r[c]) for c in columns) + ' |' for r in rows])


def build():
    assert verify_release(RELEASE) == []
    assert verify_manifest(RELEASE) == []
    manifest = read(RELEASE / 'manifest.json')
    assert manifest['release_state'] == 'sealed'
    original_digest = sha256(RELEASE / 'manifest.json')
    checks = scientific_checks(RELEASE)
    OUT.mkdir(exist_ok=True)
    evidence = {}

    def ref(path):
        path = path.resolve()
        relative = path.relative_to(ROOT).as_posix()
        evidence[relative] = dict(path=relative, sha256=sha256(path), bytes=path.stat().st_size,
            role='official' if path.is_relative_to(RELEASE) else 'historical_or_audit_only')
        return relative

    def source(name):
        return ref(RESULTS / name)

    def link(path, label=None):
        return '[' + (label or Path(path).name) + '](../../' + path.replace(' ', '%20') + ')'

    tests = read(RESULTS / 'q1/paired_hypothesis_tests_adjusted.json')
    primary = [r for r in tests if r['distribution'] == 'pre_event_t4_1d']
    q1rows = []
    for row in primary:
        pairs = pd.read_csv(RESULTS / f"q1/{row['snapshot_id']}/equity_pairs.csv")
        risk = pd.read_parquet(RESULTS / f"q1/{row['snapshot_id']}/pre_event_t4_1d_wallet_risk.parquet").set_index('wallet_address')
        for metric in row['matched_outcomes']:
            key = metric['metric']
            q1rows.append(dict(snapshot=row['snapshot_id'], pairs=row['pairs'], metric=key,
                mean_looping=float(risk.loc[pairs.looping_wallet, key].mean()),
                mean_non_looping=float(risk.loc[pairs.non_looping_wallet, key].mean()),
                difference=metric['mean_loop_minus_nonloop'], ci95_low=metric['ci95_low'], ci95_high=metric['ci95_high'],
                unit='fraction; multiply by 100 for percentage points',
                p_wilcoxon=metric['paired_wilcoxon_p'], p_holm_24=metric['p_holm_all_matched_outcomes'],
                es99_looping=row['es99_looping'], es99_non_looping=row['es99_non_looping'],
                es99_difference=row['es99_diff'], es99_ci95_low=row['es99_diff_ci95'][0], es99_ci95_high=row['es99_diff_ci95'][1],
                es99_unit='USD per 100000 USD initial positive equity; ES of matched group scenario mean',
                artifact=source('q1/paired_hypothesis_tests_adjusted.json')))
        source(f"q1/{row['snapshot_id']}/equity_pairs.csv")
        source(f"q1/{row['snapshot_id']}/pre_event_t4_1d_wallet_risk.parquet")
    write_csv('q1_primary_metrics.csv', q1rows)

    curves = read(RESULTS / 'q1/convexity_tests.json')
    shapes = read(RESULTS / 'q2/u_shape_tests_with_turning_ci.json')
    grid = pd.read_csv(RESULTS / 'q2/dense_cf_grid.csv')
    contrasts = pd.read_csv(RESULTS / 'q2/h2a_paired_contrasts.csv')
    sale = 'delta_fixed100_minus_fixed50_round_1_sold_collateral_usd'
    newly = 'delta_fixed100_minus_fixed50_newly_eligible_round_ge_2'
    debt = 'delta_fixed100_minus_fixed50_post_execution_bad_debt_usd'
    contrast_rows = []
    for mode, d in contrasts.groupby('feedback_mode'):
        contrast_rows.append(dict(feedback_mode=mode, paired_cases=len(d),
            sale_and_newly_eligible_increase=int(((d[sale] > 0) & (d[newly] > 0)).sum()),
            bad_debt_increase=int((d[debt] > 1e-8).sum()), bad_debt_decrease=int((d[debt] < -1e-8).sum()),
            bad_debt_unchanged=int((d[debt].abs() <= 1e-8).sum()),
            comparison='CF100 minus CF50; paired snapshot/scenario/depth; USD tolerance 1e-8',
            minimum_delta_bad_debt_usd=float(d[debt].min()), maximum_delta_bad_debt_usd=float(d[debt].max()),
            artifact=source('q2/h2a_paired_contrasts.csv')))
    write_csv('q2_paired_bad_debt_counts.csv', contrast_rows)
    oldpath = ROOT / 'outputs/n5_q1_q3_empirical_20261026/q2_cf_counterfactual_summary.csv'
    old = pd.read_csv(oldpath)
    assert len(old) == 228
    comparison_rows = []
    for sid in ['A', 'B1', 'B2']:
        groups = [('historical_228_not_primary', 'historical_fixed_oracle', old[old.snapshot_id.eq(sid)], 'cf_rule', oldpath)]
        groups += [('official_1458', mode, grid[grid.snapshot_id.eq(sid) & grid.depth_scale.eq(1) & grid.feedback_mode.eq(mode)],
                    'fixed_cf', RESULTS / 'q2/dense_cf_grid.csv') for mode in ['fixed', 'predictive_proxy', 'full_sensitivity']]
        for version, mode, d, key, path in groups:
            low = d[d[key].eq('fixed_50' if key == 'cf_rule' else .5)]
            high = d[d[key].eq('fixed_100' if key == 'cf_rule' else 1.)]
            assert set(low.scenario_id) == set(high.scenario_id)
            a, b = float(low.post_execution_bad_debt_usd.mean()), float(high.post_execution_bad_debt_usd.mean())
            comparison_rows.append(dict(version=version, snapshot=sid, feedback_mode=mode, depth_scale=1.,
                scenarios_per_policy=len(low), weighting='equal weight across listed deterministic scenarios, not probabilities',
                cf50_mean_bad_debt_usd=a, cf100_mean_bad_debt_usd=b, cf100_minus_cf50_usd=b-a,
                scenario_ids=';'.join(sorted(low.scenario_id)), artifact=ref(path)))
    write_csv('q2_bad_debt_version_comparison.csv', comparison_rows)

    h3 = pd.read_csv(RESULTS / 'q3/direct_induced_summary.csv')
    compound = pd.read_csv(RESULTS / 'q3/compound_direct_induced_summary.csv')
    network = read(RESULTS / 'q3/incremental_loss_network_nulls.json')
    regressions = read(RESULTS / 'q3/incremental_loss_regressions.json')
    routes = read(RESULTS / 'feedback/route_validation.json')
    interventions = pd.read_csv(RESULTS / 'q3/node_edge_interventions.csv')
    assert len(interventions) == 69
    network_rows = []
    for sid in ['A', 'B1', 'B2']:
        bet = next(r for r in regressions[sid]['coefficients'] if r['variable'] == 'weighted_token_pair_betweenness')
        for item in network[sid]['tests']:
            network_rows.append(dict(snapshot=sid, null=item['null'], draws=item['draws'],
                spearman=item['observed_spearman'], p_one_sided_unadjusted=item['positive_association_randomization_p'],
                hc3_betweenness_coefficient=bet['coefficient'], hc3_betweenness_p_unadjusted=bet['p_value'],
                scope='full_feedback sensitivity; conditional association; HC3 does not remove network dependence',
                artifact=source('q3/incremental_loss_network_nulls.json'), regression_artifact=source('q3/incremental_loss_regressions.json')))
    write_csv('q3_network_diagnostics.csv', network_rows)

    criteria = {
        'H1a': 'Chênh lệch looping−non-looping dương, CI95% lower>0 và Wilcoxon p Holm<0,05 cho cả PBD và BDR; Holm trên 24 outcome; thiết kế chính t4 1 ngày trước từng snapshot.',
        'H1b': 'ES99 nhóm trên cùng kịch bản; bootstrap ghép scenario 1.000 lần; CI95% ΔES99 phải hoàn toàn dương để ủng hộ tail loss looping cao hơn. Kiểm độ cong riêng: CI95% hệ số quadratic>0; lồi toàn miền cần mọi thay đổi độ dốc>=−1e−7; bootstrap cặp ví 1.000 lần.',
        'H2a': 'Trong mỗi cặp CF100−CF50 cùng snapshot/shock/depth/feedback: cả bán collateral vòng 1 và ví mới đủ điều kiện từ vòng 2 đều tăng. Đây là tiêu chí phản thực theo ô, không phải kiểm định nhân quả hay p tổng thể.',
        'H2b': 'Bootstrap 2.000 lần theo 18 block snapshot×shock: CI95% beta2>0, CI độ dốc trái hoàn toàn âm, phải hoàn toàn dương; điểm cực tiểu và CI nằm trong CF [0,2;1,0], với ≥95% bootstrap có curvature dương. CI điểm đảo chiều tính trên draw curvature dương. Không dùng riêng beta2>0.',
        'H3a': 'Dự báo B1→B2: feature trước target, CI HAC truyền dương, target vượt độ phân giải, RMSE cải thiện baseline. Tách khỏi mô phỏng: HF0≥1,10, HF sau shock≥1; collateral khác STETH/WSTETH≥10% và ≥100 USD; mới đủ điều kiện vòng≥2.',
        'H3b': 'Spearman loss tăng thêm với betweenness; 999 hoán vị mỗi null exact-degree/exposure/degree+exposure; p một phía chưa chỉnh nhiều kiểm định. Hồi quy HC3 có kiểm soát; can thiệp nguồn/cạnh là chẩn đoán mô hình, chưa có tiêu chí chấp nhận nhân quả/power-law.'}
    positive_h1a = sum(all(m['ci95_low'] > 0 and m['p_holm_all_matched_outcomes'] < .05 for m in row['matched_outcomes']) for row in primary)
    full = next(r for r in contrast_rows if r['feedback_mode'] == 'full_sensitivity')
    fixed = next(r for r in contrast_rows if r['feedback_mode'] == 'fixed')
    values = {
        'H1a': f'{positive_h1a}/3 snapshot đạt đồng thời PBD/BDR. ' + '; '.join(
            f"{r['snapshot_id']} ΔPBD={100*r['matched_outcomes'][0]['mean_loop_minus_nonloop']:.6g} điểm %, pHolm={r['matched_outcomes'][0]['p_holm_all_matched_outcomes']:.6g}; ΔBDR={100*r['matched_outcomes'][1]['mean_loop_minus_nonloop']:.6g} điểm %, pHolm={r['matched_outcomes'][1]['p_holm_all_matched_outcomes']:.6g}" for r in primary),
        'H1b': 'ES99 looping/non-looping, USD/100.000 equity: ' + '; '.join(f"{r['snapshot_id']} {r['es99_looping']:.4f}/{r['es99_non_looping']:.4f}" for r in primary) + f". Độ cong quadratic dương {sum(r['positive_quadratic_curvature_supported'] for r in curves)}/6 đường; lồi rời rạc toàn miền {sum(r['discrete_convex_everywhere'] for r in curves)}/6 (hai đường B2).",
        'H2a': f"Full-feedback {full['sale_and_newly_eligible_increase']}/54 cặp tăng đồng thời; fixed/predictive 0/54. Nợ xấu CF100 tăng/giảm/không đổi: full {full['bad_debt_increase']}/{full['bad_debt_decrease']}/{full['bad_debt_unchanged']}; fixed/proxy {fixed['bad_debt_increase']}/{fixed['bad_debt_decrease']}/{fixed['bad_debt_unchanged']} (ngưỡng USD 1e−8).",
        'H2b': f"{sum(r['u_shape_supported_with_turning_ci'] for r in shapes)}/9 mặt cắt depth×feedback đạt; 9 mức CF × 18 block mỗi mặt cắt. Các ô predictive trùng fixed do proxy=0, không phải lặp độc lập.",
        'H3a': f"Predictive-proxy: {int(h3[h3.feedback_mode.eq('predictive_proxy')].induced_healthy_other_wallets.sum())} lượt/12 tổ hợp; full-feedback {int(h3[h3.feedback_mode.eq('full_sensitivity')].induced_healthy_other_wallets.sum())} lượt/12. Cả 3 route đều không cải thiện RMSE B2; proxy chọn=0; 296 train+296 test/route. Compound: 0 predictive và 9 full trên 48 tổ hợp tổng.",
        'H3b': 'Spearman A/B1/B2=0,02200/0,02720/−0,00218; p null degree+exposure=0,020/0,055/1,000; p HC3 betweenness=0,4488/0,3653/0,0833; 69 can thiệp (60 nguồn+9 route).'}
    decisions = {
        'H1a': 'Không được ủng hộ trong thiết kế chính; không suy ra looping an toàn trong mọi thị trường.',
        'H1b': 'Ủng hộ giới hạn về độ cong trên lưới điều kiện; chưa xác nhận looping khuếch đại tail risk tổng quát hoặc lồi toàn miền mọi snapshot.',
        'H2a': 'Ủng hộ có điều kiện ở full-feedback sensitivity; không được ủng hộ như quy luật phổ quát trong chế độ chính.',
        'H2b': 'Không được ủng hộ theo tiêu chí đã dùng; không khẳng định tồn tại CF tối ưu nội miền.',
        'H3a': 'Chưa xác lập lây lan thực tế. Quy tắc dự báo bảo thủ chọn proxy=0; sensitivity có kích hoạt mới nhưng không thay thế chứng cứ thực nghiệm.',
        'H3b': 'Chẩn đoán cấu trúc có điều kiện, không nhất quán giữa snapshot/đối chứng/hồi quy; chưa xác nhận centrality gây lây lan hoặc power-law.'}
    artifacts = {
        'H1a': ['q1/paired_hypothesis_tests_adjusted.json', 'q1/equity_balance_before_after.csv'],
        'H1b': ['q1/hypothesis_evidence.csv', 'q1/convexity_tests.json'],
        'H2a': ['q2/h2a_paired_contrasts.csv', 'q2/dense_cf_grid.csv'],
        'H2b': ['q2/u_shape_tests_with_turning_ci.json', 'q2/dense_cf_grid.csv'],
        'H3a': ['feedback/route_validation.json', 'q3/direct_induced_summary.csv', 'q3/compound_direct_induced_summary.csv'],
        'H3b': ['q3/incremental_loss_network_nulls.json', 'q3/incremental_loss_regressions.json', 'q3/node_edge_interventions.csv']}
    conclusions = pd.read_csv(RESULTS / 'hypothesis_conclusions.csv').set_index('hypothesis')
    rows = [dict(hypothesis=h, key_numbers=values[h], test_criterion=criteria[h], conclusion=decisions[h],
                 frozen_support_code=conclusions.loc[h, 'support'], causal_acceptance='not_established',
                 artifact_paths=';'.join(source(p) for p in artifacts[h])) for h in criteria]
    write_csv('official_hypothesis_results.csv', rows)

    index = pd.read_csv(RELEASE / 'metadata/input_index.csv')
    def frozen_input(suffix):
        found = index[index.original_source_path.str.replace('\\', '/', regex=False).str.endswith(suffix)]
        assert len(found) == 1, suffix
        return RELEASE / found.path.iloc[0]
    snapshots = read(frozen_input('config/snapshots.json'))['snapshots']
    census = []
    for sid in ['A', 'B1', 'B2']:
        baseline = pd.read_parquet(frozen_input(f'{sid}/baseline_wallet_kpis.parquet'))
        snap = snapshots[sid]
        census.append(dict(snapshot=sid, block=snap['block'], block_hash=snap['block_hash'],
            timestamp_utc=snap['block_timestamp_utc'], wallet_snapshot_rows=len(baseline),
            unique_wallets_within_snapshot=baseline.wallet_address.nunique(),
            initial_cohort_debt_usd=float(baseline.debt_usd.sum()), matched_equity_pairs=next(r['pairs'] for r in primary if r['snapshot_id']==sid),
            q1_role='separate pre-snapshot calibration, not random assignment',
            q3_role='training' if sid=='B1' else 'held_out' if sid=='B2' else 'counterfactual_snapshot',
            artifact=ref(frozen_input(f'{sid}/baseline_wallet_kpis.parquet'))))
    write_csv('snapshot_census.csv', census)
    simulation_rows = []
    for row in tests:
        path = RESULTS / f"q1/{row['snapshot_id']}/{row['distribution']}_shocks.parquet"
        shocks = pd.read_parquet(path)
        simulation_rows.append(dict(snapshot=row['snapshot_id'], distribution=row['distribution'],
            classification='primary' if row['distribution']=='pre_event_t4_1d' else 'sensitivity',
            cutoff_utc=row['cutoff'], horizon_days=row['horizon_days'], calibration_returns=row['calibration_pairs'],
            logical_draws=len(shocks), distinct_shock_pairs=len(shocks.drop_duplicates(['eth_shock','depeg_shock'])),
            seed=42, liquidation='post_execution, fixed oracle, baseline_aave CF, depth1', artifact=ref(path)))
    write_csv('simulation_census.csv', simulation_rows)
    write_csv('frozen_inputs.csv', [dict(**r, artifact=ref(RELEASE/r['path'])) for r in index.to_dict('records')])
    stage_sources = []
    for p in sorted(RESULTS.glob('*completed.json')):
        for r in read(p)['code_versions']:
            stage_sources.append(dict(stage=p.stem, original_path=r['original_path'], sha256=r['sha256'],
                archived_path=source(r['archived_path']), stage_record=ref(p)))
    write_csv('stage_source_versions.csv', stage_sources)

    legacy_errors = verify_manifest(oldpath.parent)
    # The original integration test reads results only; preserve and reproduce it.
    old_test = subprocess.run(['git','show','HEAD:tests/test_n234_completion.py'],cwd=ROOT,check=True,stdout=subprocess.PIPE).stdout
    old_text = old_test.decode('utf-8')
    marker = 'def test_empirical_q1_q3_bundle_manifest_and_kpis():'
    assert marker in old_text
    original_test = old_text[old_text.index(marker):]
    (OUT/'obsolete_test_source.txt').write_text(original_test,encoding='utf-8')
    node=next(n for n in ast.parse(original_test).body if isinstance(n,ast.FunctionDef))
    namespace={'pd':pd}
    old_test_error=None
    try:
        exec(compile(ast.Module(body=[node],type_ignores=[]),'obsolete_test_source.txt','exec'),namespace)
        namespace[node.name]()
    except Exception:
        old_test_error=traceback.format_exc()
    baseline_failures=read(ROOT/'outputs/n5_release_audit_20261026/baseline_mismatches.json')
    old_baseline=[r for r in baseline_failures if 'n5_q1_q3_empirical_20261026' in r['manifest']]
    ref(ROOT/'outputs/n5_release_audit_20261026/baseline_mismatches.json')
    write_json(OUT/'obsolete_test_diagnosis.json', dict(original_test_source_sha256=hashlib.sha256(old_test).hexdigest(),
        git_head=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),
        reproduced_check='verify_manifest(outputs/n5_q1_q3_empirical_20261026) == []',
        reproduced_assertion_would_fail=bool(legacy_errors), current_legacy_manifest_errors=legacy_errors,
        original_test_reproduced_status='passed' if old_test_error is None else 'failed',
        reproduction_traceback=old_test_error, baseline_audit_input_failures=old_baseline,
        interpretation='historical_test_currently_passes; initial_failure_cause_not_reconstructable_without_original_traceback; file_verifier_does_not_check_inputs',
        resolution='replace obsolete integration target with official frozen results and stronger input/parent checks; preserve model tests',
        original_run_traceback_available=False))
    audit = read(RELEASE/'provenance/audit_summary.json')
    dispositions = pd.read_csv(RELEASE/'provenance/path_dispositions.csv')
    recovery = dict(exact_versions=int(dispositions.exact_historical_versions_recovered.sum()),
                    historical_versions=int(dispositions.historical_expected_versions.sum()))
    issues = [
        ('I01','Đã xử lý phạm vi phát hành; lịch sử còn thiếu','96 chuỗi đường dẫn / 95 tệp: 79 đổi phiên bản, 16 thiếu. 41 code/config, 28 data/output, 10 hậu xử lý. Khôi phục byte '+str(recovery['exact_versions'])+'/'+str(recovery['historical_versions'])+' phiên bản hash.', 'Không sửa manifest cũ để che thay đổi; chỉ chọn đầu ra và input khớp; các byte chưa tìm được không chứng nhận tái lập lịch sử.', ref(RELEASE/'provenance/path_dispositions.csv')),
        ('I02','Đã đổi phạm vi nghiệm thu; lỗi lịch sử chưa quy nguyên nhân','Test cũ trỏ bundle228/18 mapping, hiện chạy riêng '+('PASS' if old_test_error is None else 'FAIL')+'; verify_manifest cũ có '+str(len(legacy_errors))+' lỗi FILE. Audit cũ có '+str(len(old_baseline))+' lỗi INPUT mã nguồn; verify_manifest chỉ kiểm files.', 'Thay bằng test bản chốt+input+manifest cha+quy mô+KPI. Thiếu traceback lần thất bại ban đầu: không được tự kết luận do stale manifest hoặc lỗi tính toán; ghi không tái hiện được trên bytes hiện tại.', 'obsolete_test_diagnosis.json'),
        ('I03','Đã làm rõ','Q1 chính có thanh lý nhiều vòng, giữ oracle; không có vòng phản hồi DEX→oracle.', 'Không trộn pre-shock loss, ES từng ví, ES nhóm, hoặc ES 19 stress với MC ghép cặp.', source('q1/hypothesis_evidence.csv')),
        ('I04','Đã làm rõ','Q2 dùng 1458 ô; 228 dòng và 432 ô là lịch sử. So sánh bad debt có bảng ghép CF, depth, mode, shock.', 'Trung bình 19 shock và 6 shock khác estimand; không gọi chênh lệch đó là tác động sửa lỗi đơn thuần.', source('q2/dense_cf_grid.csv')),
        ('I05','Đã tách; giới hạn khoa học còn mở','3 route không vượt baseline ngoài mẫu; proxy=0 là fallback. Full-feedback=1, cap15% là sensitivity.', 'Không nhận dạng nhân quả do biến thiên/độ phân giải, confounding và collateral seized chỉ proxy dòng bán.', source('feedback/route_validation.json')),
        ('I06','Đã đối chiếu định nghĩa; chỉ tiêu gốc còn thiếu','18 mã mapping không đồng nghĩa 18 KPI nghiệm thu; M08/M09 khác đơn vị/estimand; M12 eligible khác executed; H3 HF0=1,10.', 'M14 freeze, M16 CPCI đầy đủ, M17 edge-share thực tế, M18 DebtRank gốc chưa được công bố làm primary.', ref(RELEASE/'metadata/kpi_release_dictionary.csv')),
        ('I07','Chưa giải quyết từ dữ liệu hiện có','3 snapshot; matching chỉ equity; 2 ngày thiếu khủng hoảng; bootstrap có điều kiện, không loại mọi bất định hiệu chỉnh.', 'Không gán ngẫu nhiên looping; 5 ngày là shock đầu-cuối, không path/rebalance; chưa suy rộng mọi khủng hoảng.', source('REPORT.md')),
        ('I08','Chưa giải quyết từ mô hình hiện có','3 route DEX; thiếu/vượt quote là unknown. Comet một chiều trên ví overlap; không mô phỏng vòng kín toàn giao thức.', 'Không tự suy ra freeze, run/rút tiền thực tế, oracle Comet như Aave hoặc absorb bằng protocol net loss.', source('q3/compound_direct_induced_summary.csv')),
        ('I09','Chưa xác nhận giả thuyết tổng quát','Null centrality nhỏ/không bền giữa snapshot và regression; p null chưa hiệu chỉnh nhiều kiểm định; HC3 còn phụ thuộc mạng.', 'Không coi diagnostics là bằng chứng power-law hay tác động nhân quả; không thay kết luận để ủng hộ H3b.', source('q3/incremental_loss_regressions.json'))]
    issue_rows=[dict(id=i,status=s,evidence=e,resolution_or_remaining_limit=l,artifact=p) for i,s,e,l,p in issues]
    write_csv('resolved_and_open_issues.csv',issue_rows)

    classifications = [
        dict(question='Q1',version='official_release_v1',classification='primary',selection='pre_event_t4_1d; one-to-one equity matching; post liquidation fixed oracle',artifact=source('q1/paired_hypothesis_tests_adjusted.json')),
        dict(question='Q1',version='official_release_v1',classification='sensitivity',selection='pre_event_gaussian_1d; crisis_joint_1d; crisis_joint_5d; deterministic convexity stress',artifact=source('q1/convexity_tests.json')),
        dict(question='Q2',version='official_release_v1',classification='primary_counterfactual',selection='1458 cells; reference depth1 predictive_proxy and fixed (identical proxy0); 9 fixed CF; 6 shocks',artifact=source('q2/dense_cf_grid.csv')),
        dict(question='Q2',version='official_release_v1',classification='sensitivity_counterfactual',selection='depth0.5/2 and full_sensitivity, not observed policy outcomes',artifact=source('q2/h2a_paired_contrasts.csv')),
        dict(question='Q3',version='official_release_v1',classification='primary_observational_prediction',selection='B1 training -> B2 held-out; three pair-relative oracle routes',artifact=source('feedback/route_validation.json')),
        dict(question='Q3',version='official_release_v1',classification='model_output_under_predictive_fallback',selection='12 pure depeg cases predictive_proxy=0, not observed contagion outcomes',artifact=source('q3/direct_induced_summary.csv')),
        dict(question='Q3',version='official_release_v1',classification='sensitivity_counterfactual',selection='12 full-feedback cases; network null/regression;69 interventions;48 total Comet cases',artifact=source('q3/node_edge_interventions.csv')),
        dict(question='ALL',version='n5_hypothesis_completion_20261026',classification='parent_analysis_run',selection='196 exact original artifacts, quoted through frozen release only',artifact=ref(RELEASE/'provenance/analysis_manifest.json')),
        dict(question='ALL',version='n5_review_corrected_20261026',classification='historical_superseded',selection='intermediate corrected analysis; not current six-hypothesis evidence',artifact='outputs/n5_review_corrected_20261026/manifest.json'),
        dict(question='ALL',version='n5_q1_q3_empirical_20261026',classification='historical_exploratory_superseded',selection='228 CF runs/4 policies;432-cell exploratory grid;earlier shock-only and post-cascade Q1 variants',artifact=ref(oldpath.parent/'manifest.json'))]
    write_csv('result_version_registry.csv',classifications)

    q1lines=[]
    for row in primary:
        q1lines.append(dict(Snapshot=row['snapshot_id'],Pairs=row['pairs'],ES99_looping=f"{row['es99_looping']:.4f}",
            ES99_non_looping=f"{row['es99_non_looping']:.4f}",Difference=f"{row['es99_diff']:.4f}",CI95=f"[{row['es99_diff_ci95'][0]:.4f}; {row['es99_diff_ci95'][1]:.4f}]"))
    headline = [dict(zip(['Giả thuyết','Số liệu then chốt','Tiêu chí kiểm định','Kết luận','Tệp đầu ra'],[r['hypothesis'],r['key_numbers'],r['test_criterion'],r['conclusion'],'<br>'.join(link(p) for p in r['artifact_paths'].split(';'))])) for r in rows]
    bdtable=[dict(Version=r['version'],Snapshot=r['snapshot'],Mode=r['feedback_mode'],N=r['scenarios_per_policy'],CF50=f"{r['cf50_mean_bad_debt_usd']:.6f}",CF100=f"{r['cf100_mean_bad_debt_usd']:.6f}",Delta=f"{r['cf100_minus_cf50_usd']:.6f}") for r in comparison_rows]
    stage_mc = read(RESULTS/'q1_completed.json')
    archived_mc = next(r for r in stage_mc['code_versions'] if PureWindowsPath(r['original_path']).name=='n5_resolve_research_review.py')
    archived_engine = next(r for r in stage_mc['code_versions'] if PureWindowsPath(r['original_path']).name=='n5_corrected_research.py')
    source(archived_mc['archived_path']);source(archived_engine['archived_path'])
    route_table=[dict(Route=r['route'],RMSE=f"{r['held_out_rmse']:.9g}",Baseline=f"{r['zero_change_rmse']:.9g}",CI95=str(r['hac_ci95']),Above_resolution=r['target_variation_above_resolution'],Proxy=r['calibrated_predictive_proxy']) for r in routes]
    trial = OUT/'pytest_full_suite.txt'
    trial_json = OUT/'pytest_full_suite.json'
    test_status='Log nghiệm thu bản chốt: '+link(ref(RELEASE/'provenance/pytest_full_suite.txt'))+' — 198 passed, 12 subtests; 74 warnings (deprecation).'
    if trial.exists() and trial_json.exists():
        result=read(trial_json)
        assert result['exit_code']==0 and result['stdout_sha256']==sha256(trial)
        test_status+=' Lần kiểm tra bổ sung: '+re.findall(r'\d+ passed[^\n]*',trial.read_text(encoding='utf-8'))[-1]+'. Xem [log](pytest_full_suite.txt).'
    report=f'''# Bảng kết quả chuẩn để viết báo cáo — 26/10/2026

Nguồn khoa học chính thức duy nhất: `{RELEASE.relative_to(ROOT).as_posix()}`; manifest trạng thái **sealed**, SHA-256 `{original_digest}`. Đây là phụ lục trích dẫn được sinh từ bản chốt, không phải lần chạy mô hình mới. Mọi đường dẫn trong CSV tính từ gốc dự án; [evidence_index.csv](evidence_index.csv) ghi SHA-256 của nguồn thực sự đọc. CSV lưu số đầy đủ; bảng dưới làm tròn để trình bày. Không sửa byte bản chốt hoặc đổi kết luận giả thuyết.

## 1. Bảng H1a–H3b

{table(headline,['Giả thuyết','Số liệu then chốt','Tiêu chí kiểm định','Kết luận','Tệp đầu ra'])}

[Bảng CSV chuẩn](official_hypothesis_results.csv); [số liệu Q1 đầy đủ, CI và p](q1_primary_metrics.csv); [chẩn đoán mạng đầy đủ](q3_network_diagnostics.csv). Các kết luận phân biệt **không được ủng hộ**, **ủng hộ có điều kiện**, **chưa xác lập**; không đồng nhất chúng với chứng minh giả thuyết sai.

## 2. Phiên bản và phạm vi trích dẫn

{table([dict(Question=r['question'],Version=r['version'],Class=r['classification'],Selection=r['selection'],Source=link(r['artifact']) if (ROOT/r['artifact']).is_file() else r['artifact']) for r in classifications],['Question','Version','Class','Selection','Source'])}

“Kết quả chính” và “phản thực” là hai thuộc tính khác nhau: Q2 chính vẫn là phản thực có điều kiện, không trở thành tác động chính sách quan sát. Q1 chính cũng là mô phỏng dưới shock lấy mẫu từ dữ liệu, không phải kết quả thực nghiệm ngẫu nhiên. Full-feedback và các phân phối khủng hoảng không được thay thế thiết kế chính khi kết quả khác kỳ vọng.

## 3. Giải quyết bất nhất Q1: ES99 có thanh lý hay không?

**Có thanh lý nhiều vòng trong Q1 chính**, nhưng **oracle giữ ở mức sau shock ngoại sinh**, không nhận feedback từ DEX. Mỗi scenario sàng HF<1, gọi `mc_exact→execute`, cập nhật số lượng debt/collateral, bán cộng dồn và điều kiện lợi nhuận; vòng sau có thể tiếp tục xử lý cùng ví. Lặp thực thi không tự chứng minh ví khác bị lây lan qua giá. Code đúng giai đoạn: {link(source(archived_mc['archived_path']),'mc_exact đã lưu')} và {link(source(archived_engine['archived_path']),'execute đã lưu')}; xem [stage_source_versions.csv](stage_source_versions.csv).

Đặt `L_g(m)=mean_i[100000×BD_i,post(m)/E_i,0]` trên các ví thuộc nhóm ghép có equity dương. **ES99_g = trung bình 100 giá trị lớn nhất của 10.000 L_g(m)**, tính cả loss zero. Đây là ES của loss nhóm theo scenario, không phải trung bình ES từng ví, không dùng tie-inclusive `loss≥VaR` khi có nhiều zero. Δ=looping−non-looping. USD/100.000 equity không phải triệu USD hoặc BDR%. BDR kiểm định H1a là trung bình tỷ số bad debt/debt ban đầu theo ví; Q2 BDR là tỷ số tổng trên cohort debt ban đầu.

{table(q1lines,['Snapshot','Pairs','ES99_looping','ES99_non_looping','Difference','CI95'])}

Các bản shock-only, post-cascade theo strata, 19 stress tất định và MC ghép equity là những estimand khác nhau. Không lấy số từ bảng cũ thay số trên. Zero là kết quả có điều kiện trong mẫu/lưới được chọn, không chứng minh vị thế không có tail risk ngoài miền.

## 4. Giải quyết bất nhất Q2: 228 hay 1.458, và nợ xấu

228 = 3 snapshot ×19 shock ×4 quy tắc CF trong bundle cũ. Chính thức **1.458 = 3×6 shock×3 depth×9 CF×3 mode**. Do predictive_proxy=0, mode predictive trùng fixed: 1.458 là số ô xuất, không phải 1.458 thí nghiệm độc lập; tối đa 972 cấu hình thực thi khác mode trước các trùng khác. Bảng dưới dùng depth=1; CF100−CF50; bad debt USD sau thực thi, bình quân đồng trọng số theo N shock trong từng bản. **N=19 và N=6 khác tập shock, không so chênh lệch hai trung bình như hiệu ứng sửa code.** Bản cũ chỉ dùng giải thích nguồn số đã xuất, không dùng kết luận chính.

{table(bdtable,['Version','Snapshot','Mode','N','CF50','CF100','Delta'])}

[Chi tiết shock và số đầy đủ](q2_bad_debt_version_comparison.csv). Trên toàn 54 cặp snapshot×shock×depth ở mỗi mode:

{table(contrast_rows,['feedback_mode','paired_cases','sale_and_newly_eligible_increase','bad_debt_increase','bad_debt_decrease','bad_debt_unchanged'])}

17/54 là **đồng thời tăng lượng bán vòng đầu và ví mới đủ điều kiện** trong full-feedback sensitivity; không phải 17 ví, không phải tỷ lệ nợ xấu, không phải số thực thi lần đầu vòng sau. Nợ xấu tăng/giảm dùng ngưỡng USD 1e−8 để tránh sai số float. Không cộng bad debt các shock phản thực thành tổn thất thị trường quan sát. Không trộn bán collateral với repayment hoặc số dư bad debt.

## 5. Giải quyết bất nhất Q3: ngoài mẫu và feedback

B1 train, B2 held-out; 296 dòng mỗi tập mỗi route, lag feature trước target. Tiêu chí dương: CI HAC dương + target đủ biến thiên vượt resolution + cải thiện RMSE so với zero-change. Kết quả route chính thức:

{table(route_table,['Route','RMSE','Baseline','CI95','Above_resolution','Proxy'])}

`feedback/validation.json` là fit sơ bộ dùng proxy dòng collateral seized; hệ số tổng `4,407007e−11`, RMSE `2,430709e−13` so baseline `2,426874e−13`. `feedback/route_validation.json` là kiểm tra bổ sung theo ba cặp DEX/oracle quan sát và là nguồn trích route ở bảng trên; hai fit khác feature/target nên không trộn hệ số/RMSE thành một ước lượng. Cả hai cùng không đạt tiêu chí cải thiện, proxy chọn 0. wstETH/WETH còn không vượt độ phân giải giả định. Không diễn giải fallback 0 thành bằng chứng tác động thật bằng 0.

Mô phỏng **full_sensitivity** đặt transmission=1, cap giảm giá 15% từ giá đã shock; không phải hệ số đã được kiểm định. So direct vs feedback cùng pure-depeg 4%,5%,7%,10% tại 3 snapshot. [direct_induced_summary.csv]({('../../'+source('q3/direct_induced_summary.csv')).replace(' ','%20')}) có 24 dòng: 12 predictive (0 lượt),12 full (7 lượt); mẫu số riêng từng snapshot/shock, không lấy 12 làm số ví. Bảng lưu `induced_transition_rate` đúng từng ô; không gọi tổng lượt theo scenario là ví duy nhất. 48 trường hợp Comet (24 mỗi mode) cho 0/9 lượt mới đủ điều kiện là sensitivity một chiều trên account–market overlap, không xác nhận vòng kín lây lan thực tế. Không đồng nhất absorb với protocol net loss.

## 6. Dữ liệu và cấu hình cuối

{table([dict(Snapshot=r['snapshot'],Block=r['block'],UTC=r['timestamp_utc'],Wallet_snapshot_rows=r['wallet_snapshot_rows'],Pairs=r['matched_equity_pairs'],Q3_role=r['q3_role']) for r in census],['Snapshot','Block','UTC','Wallet_snapshot_rows','Pairs','Q3_role'])}

Tổng **50.311 dòng ví–snapshot**, không phải mặc định 50.311 người/ví duy nhất xuyên thời gian. Hash block và debt mẫu số: [snapshot_census.csv](snapshot_census.csv). Nhãn B1/B2 “out-of-sample” trong config là vai trò sự kiện; Q3 thực tế dùng B1 train và B2 test, không gọi B1 là test của chính fit đó.

- Nguồn on-chain: Aave V3 Ethereum positions/reserve config/oracle/liquidation logs tại snapshot; quote DEX bounded ở 3 route wstETH→WETH, WETH→USDC, WBTC→WETH; Compound Comet exposures đã đổi USD, reserve/pause/storefront trên mẫu overlap. [53 input đóng băng](frozen_inputs.csv) ghi đường dẫn nội bộ+gốc+SHA; code giai đoạn trong [stage_source_versions.csv](stage_source_versions.csv).
- Nguồn hiệu chỉnh giá: quote lịch sử DefiLlama đã lưu raw và timestamp; ETH/USD, STETH/ETH. 366 mức giá trước từng snapshot→365 lợi suất 1 ngày, không quote tương lai. Khủng hoảng 05–07/2022:90/92 ngày, thiếu 19/05 và22/07; không nội suy/không tính returns qua khoảng trống. Hiệu chỉnh khủng hoảng có87 returns 1 ngày; số5 ngày xem census. Chỉ đọc dữ liệu đóng băng trong bước này, không gọi API mới.
- Q1 seed42, t-copula df4 là chính; Gaussian và bootstrap chung ETH/depeg 1/5 ngày là sensitivity. 10.000 draw×4 phân phối×3 snapshot=120.000 **lượt scenario logic**; bootstrap có shock trùng được tái dùng, không phải120.000 cú sốc lịch sử độc lập. [simulation_census.csv](simulation_census.csv) ghi số shock khác nhau. 5 ngày là shock đầu-cuối, không path/rebalance/lãi suất.
- Ghép equity dương1:1, không lặp control, caliper log-equity0,1. Bootstrap1.000 cho matched/ES/curvature; Holm24 PBD/BDR. Convexity6 đường×31 mức depeg0–15% bước0,5 điểm %, ETH0/−20%:186 điểm stress, không phải draws MC. Matching equity không loại hết confounding; không match HF/leverage do có thể là mediator.
- Q2 9 CF cố định20%–100% bước10 điểm %, depth0,5/1/2;6 shock ETH−20/−40%, depeg−4/−5/−10%, kết hợp ETH−20%+depeg−5%. Ba mode fixed/predictive/full; CF là phản thực, không khẳng định protocol thực dùng mọi mức. Bootstrap chữ U2.000 block draws; điểm đảo chiều có CI; không ANOVA coi ô là độc lập.
- Engine: tối đa100 vòng; dừng khi hết thực thi có lợi nhuận được hỗ trợ, không có round-limit ở kết quả chốt; gas5 USD/giao dịch; fee route0,01%/0,05%/0,3%; giữ danh mục khi áp shock; debt/collateral cập nhật sau mỗi thanh lý; flow pool cộng dồn. Không tách lệnh; quote ngoài miền hoặc route thiếu ghi unknown; khoản không thực thi không tự coi là freeze. Float arithmetic, không phải emulator EVM đầy đủ.
- Q3: healthy HF0≥1,10 và sau shock≥1; collateral khác STETH/WSTETH≥10% và≥100 USD. Null999×3 loại×3 snapshot; regressionHC3; nguồn top1% +19 tập ghép ngẫu nhiên mỗi snapshot,3 route bị chặn mỗi snapshot→69 can thiệp, tại depeg10%; không phải69 nút duy nhất. Diagnostics power-law dùng bootstrap300 và không chứng minh power-law.
- Phiên bản phần mềm được đóng băng tại {link(source('software_versions.json'))}; Python3.12.10, numpy2.5.4, pandas2.3.3, scipy1.18.1. Cần dùng file phiên bản/nguồn đã lưu khi tái lập, không tự thay bằng live workspace.

## 7. Manifest và kiểm thử

Audit cố định **{audit['audited_manifests']} manifest**: {audit['matched_references']} tham chiếu khớp; {audit['mismatched_references']} hash khác và {audit['missing_references']} thiếu, quy về96 chuỗi/95 tệp; các lượt lỗi lặp qua manifest không được đếm như tệp độc lập. Phân loại không có nghĩa mọi thay đổi nội dung cũ đều vô hại:28 tệp dữ liệu/đầu ra và10 hậu xử lý có thay đổi thật giữa phiên bản. Những bản thiếu byte không thể so/replay hoàn toàn. Chỉ xác nhận bản được chọn và53 input đóng băng sạch; không tuyên bố mọi manifest lịch sử sạch.

{test_status}

Test cũ `test_empirical_q1_q3_bundle_manifest_and_kpis` gắn cứng228 và18 mapping. **Khôi phục từ Git và chạy riêng trên bytes hiện tại: {'PASS' if old_test_error is None else 'FAIL'}**; `verify_manifest(old)` trả {len(legacy_errors)} lỗi files. Audit lịch sử có {len(old_baseline)} lỗi input mã nguồn ở bundle cũ, nhưng hàm `verify_manifest` chỉ kiểm `files`, không kiểm `inputs`; vì thế không dùng input drift để giải thích một assertion files thất bại. [Nguồn test cũ](obsolete_test_source.txt) và [kết quả tái kiểm](obsolete_test_diagnosis.json) ghi chứng cứ. **Không có traceback gốc: chưa quy được nguyên nhân lần thất bại ban đầu**, không kết luận lỗi mô hình hoặc manifest lỗi thời khi chưa đủ bằng chứng. Thay tiêu chí integration sang bản chốt là cập nhật phạm vi nghiệm thu và tăng kiểm input/manifest cha/đơn vị/threshold; không phải chứng minh đã sửa được nguyên nhân lịch sử. Không bỏ test hành vi mô hình hoặc dùng skip/xfail.

Manifest bản chốt đã được tạo sau khi cố định269 tệp/53 input; không tạo lại manifest đó chỉ để thêm tài liệu trích dẫn. **Phụ lục này có manifest riêng**, khóa SHA manifest cha, mọi tệp phụ lục và nguồn đọc. Kiểm tra bằng:

```powershell
.\\.venv\\Scripts\\python.exe -B -X utf8 -m scripts.n5_finalize_release --stage verify
.\\.venv\\Scripts\\python.exe -B -X utf8 -m scripts.n5_build_citation_pack --verify
```

## 8. Vấn đề đã xử lý và giới hạn còn lại

{table([dict(ID=r['id'],Status=r['status'],Evidence=r['evidence'],Resolution_or_limit=r['resolution_or_remaining_limit'],Source=link(r['artifact']) if r['artifact'].startswith('outputs/') else '['+r['artifact']+']('+r['artifact']+')') for r in issue_rows],['ID','Status','Evidence','Resolution_or_limit','Source'])}

[Danh sách CSV](resolved_and_open_issues.csv). Hướng dẫn trích dẫn: ghi snapshot, phân phối/kỳ hạn, cohort/đơn vị, CF/depth/feedback, tên tệp và SHA từ evidence_index. Bằng chứng dự báo, kết quả mô phỏng và nhận dạng nhân quả là các mức khác nhau. Mọi giả thuyết giữ kết luận đúng bằng chứng; dữ liệu hiện có chưa đủ để nâng lên tuyên bố nhân quả hoặc suy rộng toàn thị trường.
'''
    (OUT/'REPORT.md').write_text(report,encoding='utf-8')
    (OUT/'build_citation_pack.py').write_bytes(Path(__file__).read_bytes())
    source('preregistered_settings.json');ref(RELEASE/'metadata/scientific_consistency.json')
    ref(RELEASE/'metadata/publication_artifacts.csv');ref(RELEASE/'provenance/audit_summary.json')
    ref(RELEASE/'provenance/manifest_reference_failures.csv');ref(RELEASE/'provenance/pytest_full_suite.json')
    write_csv('evidence_index.csv',list(evidence.values()))
    write_json(OUT/'verification.json',dict(official_manifest_errors=[],official_release_errors=[],scientific_checks=checks,
        official_files=len(manifest['files']),frozen_input_records=len(manifest['inputs']),audit_recovery=recovery,
        immutable_parent_manifest_sha256=original_digest))
    assert sha256(RELEASE/'manifest.json')==original_digest and verify_release(RELEASE)==[]
    package=dict(version='n5_citation_pack_v1',created_utc=datetime.now(timezone.utc).isoformat(),
        role='citation_companion_no_new_model_run',official_release=RELEASE.relative_to(ROOT).as_posix(),
        official_manifest_sha256=original_digest,script_sha256=sha256(Path(__file__)),
        files=[dict(path=p.relative_to(OUT).as_posix(),sha256=sha256(p),bytes=p.stat().st_size)
               for p in sorted(OUT.iterdir()) if p.is_file() and p.name!='manifest.json'],
        evidence=list(evidence.values()))
    write_json(OUT/'manifest.json',package)
    verify()
    print('Citation pack built:',OUT)


def verify():
    m=read(OUT/'manifest.json')
    assert sha256(RELEASE/'manifest.json')==m['official_manifest_sha256']
    assert verify_manifest(RELEASE)==[] and verify_release(RELEASE)==[]
    for row in m['files']:
        p=(OUT/row['path']).resolve()
        assert p.is_relative_to(OUT.resolve()) and sha256(p)==row['sha256'],row['path']
    for row in m['evidence']:
        p=(ROOT/row['path']).resolve()
        assert p.is_relative_to(ROOT) and sha256(p)==row['sha256'],row['path']
    print('Official parent and citation manifest: clean;',len(m['files']),'companion files;',len(m['evidence']),'source references')


if __name__=='__main__':
    import argparse
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--verify',action='store_true')
    args=parser.parse_args()
    verify() if args.verify else build()
