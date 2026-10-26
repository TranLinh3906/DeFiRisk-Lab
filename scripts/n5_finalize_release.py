"""Audit historical manifests and freeze an official bundle; never run models."""
import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
import csv
import json
from pathlib import Path
import shutil
import subprocess

from src.models.n5_release import sha256, local_path, verify_release, scientific_checks

WORKSPACE=Path(__file__).resolve().parents[1]
AUDIT=WORKSPACE/'outputs/n5_release_audit_20261026'
ANALYSIS=WORKSPACE/'outputs/n5_hypothesis_completion_20261026'
RELEASE=WORKSPACE/'outputs/n5_official_release_20261026'


def write_json(path, data):
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(data,ensure_ascii=False,indent=2,allow_nan=False),encoding='utf-8')


def write_csv(path, rows):
    path.parent.mkdir(parents=True,exist_ok=True)
    with path.open('w',encoding='utf-8-sig',newline='') as f:
        w=csv.DictWriter(f,fieldnames=list(rows[0]));w.writeheader();w.writerows(rows)


def git_run(args):
    p=subprocess.run(['git',*args],cwd=WORKSPACE,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL)
    return p.stdout if p.returncode==0 else b''


def audit():
    baseline=json.loads((AUDIT/'baseline_mismatches.json').read_text(encoding='utf-8'))
    inventory=json.loads((AUDIT/'baseline_inventory.json').read_text(encoding='utf-8'))
    primary=json.loads((ANALYSIS/'manifest.json').read_text(encoding='utf-8'))
    official_inputs={str(Path(r['path']).resolve()):r['sha256'] for r in primary['inputs']}
    versions={}
    for marker in ANALYSIS.glob('*completed.json'):
        m=json.loads(marker.read_text(encoding='utf-8'))
        for r in m.get('code_versions',[]):
            original=str((WORKSPACE/r['original_path']).resolve())
            versions.setdefault(original,[]).append(r['sha256'])
    archive={}
    for p in (WORKSPACE/'outputs').glob('*/source_versions/*.py'):
        if len(p.stem)==64 and sha256(p)==p.stem:archive.setdefault(p.stem,str(p.relative_to(WORKSPACE)))
    grouped=defaultdict(list)
    for r in baseline:grouped[str(Path(r['path']).resolve())].append(r)
    dispositions=[];details=[]
    for number,(canonical,refs) in enumerate(sorted(grouped.items()),1):
        path=Path(canonical);relative=path.relative_to(WORKSPACE).as_posix()
        actual=sha256(path) if path.is_file() else None
        expected={r['expected'] for r in refs}
        evidence={e:'archive:'+archive[e] for e in expected if e in archive}
        history=git_run(['log','--all','--format=%H','-12','--',relative]).decode().splitlines()
        for commit in history:
            if set(evidence)==expected:break
            content=git_run(['show',f'{commit}:{relative}'])
            if not content:continue
            import hashlib
            variants=[('git_blob',content)]
            if path.suffix.lower() in ['.py','.json','.csv','.md']:
                variants.append(('git_blob_CRLF',content.replace(b'\r\n',b'\n').replace(b'\n',b'\r\n')))
            for tag,raw in variants:
                candidate=hashlib.sha256(raw).hexdigest()
                if candidate in expected:evidence[candidate]=f'{tag}:{commit}:{relative}'
        if actual is None:
            category='missing_legacy_reference'
            reason='Old manifest references an absent intermediate/partial path; not required by the frozen analysis inputs.'
        elif relative.startswith(('scripts/','src/','config/')):
            category='live_code_or_configuration_version_drift'
            reason='A historical manifest pins earlier code/config bytes; current code has a different version.'
        elif '/n5_review_corrected_20261026/' in '/'+relative and any('manifest.before_' in r['manifest'] for r in refs):
            category='documented_structural_postprocessing_version'
            reason='Before-structural manifests describe outputs predating the documented precision/denominator corrections.'
        else:
            category='historical_data_or_output_version_drift'
            reason='Historical source/output bytes differ from current data; exact earlier versions are identified where recoverable.'
        if canonical in official_inputs:
            role='current_version_used_as_frozen_observed_input'
            if actual!=official_inputs[canonical]:raise ValueError('Selected official input is inconsistent')
        elif actual in versions.get(canonical,[]):role='current_code_version_is_archived_in_official_stage_sources'
        elif canonical in versions:role='official_run_uses_archived_code_versions_not_this_live_path'
        else:role='not_part_of_official_results_or_observed_inputs'
        dispositions.append(dict(canonical_path=relative,declared_path_aliases=len({r['path'] for r in refs}),
            reference_failures=len(refs),category=category,reason=reason,official_role=role,
            current_sha256=actual or '',historical_expected_versions=len(expected),
            exact_historical_versions_recovered=len(evidence),
            evidence=json.dumps(evidence,ensure_ascii=False),
            historical_attribution='exact_bytes_recovered' if len(evidence)==len(expected) else 'some_historical_bytes_unavailable_not_accepted_into_release'))
        for ref in refs:
            details.append(dict(manifest=ref['manifest'],scope=ref['scope'],declared_path=ref['path'],
                canonical_path=relative,expected_sha256=ref['expected'],current_sha256=actual or '',
                failure=ref['reason'],category=category,official_role=role,
                exact_prior_version_evidence=evidence.get(ref['expected'],'not_recovered;historical_reference_excluded')))
        if number%20==0:print('attributed historical paths',number,'/',len(grouped),flush=True)
    write_csv(AUDIT/'path_dispositions.csv',dispositions)
    write_csv(AUDIT/'manifest_reference_failures.csv',details)
    summary=dict(audited_manifests=len(inventory['bundles']),matched_references=inventory['counts']['match'],
        mismatched_references=inventory['counts']['hash_mismatch'],missing_references=inventory['counts']['missing'],
        declared_problem_path_strings=len({r['path'] for r in baseline}),canonical_problem_files=len(grouped),
        canonical_missing_files=sum(not Path(p).is_file() for p in grouped),
        canonical_changed_existing_files=sum(Path(p).is_file() for p in grouped),
        classification=dict(Counter(r['category'] for r in dispositions)),
        selected_analysis_artifact_mismatches=0,selected_analysis_input_mismatches=0,
        exact_recovery_not_claimed_for_unavailable_historical_bytes=True,
        resolution='Preserve historical manifests as evidence; freeze only validated current results and stage source snapshots.')
    from src.models.n5_readiness import verify_manifest
    if verify_manifest(ANALYSIS):raise ValueError('Selected analysis artifacts changed')
    for row in primary['inputs']:
        if sha256(Path(row['path']))!=row['sha256']:raise ValueError('Selected analysis input changed')
    write_json(AUDIT/'audit_summary.json',summary)
    print(json.dumps(summary,ensure_ascii=False),flush=True)


def kpi_metadata(release, manifest):
    source=WORKSPACE/'kpi_dictionary.csv'
    shutil.copyfile(source,release/'metadata/kpi_dictionary_original.csv')
    original=list(csv.DictReader(source.open(encoding='utf-8-sig')))
    baseline=[r['path'] for r in manifest['inputs'] if Path(r['original_source_path']).name=='baseline_wallet_kpis.parquet']
    base=json.dumps(baseline)
    definitions={
      'M01':('net_equity_t0','USD','enabled collateral USD minus debt USD',base,'baseline_exact'),
      'M02':('leverage_t0','times','collateral/equity for equity>0; null otherwise',base,'explicit_null_guard_replaces_epsilon'),
      'M03':('health_factor_t0','ratio','weighted enabled collateral/debt',base,'baseline_exact_with_null_guard'),
      'M04':('distance_to_liquidation_proxy','percent','100*(1-1/HF)',base,'proportional_collateral_price_proxy'),
      'M05':('conditional_probability_bad_debt','fraction_in_storage','mean over all 10000 draws of I(BD>0)','results/q1/*/*_wallet_risk.parquet','multiply_by_100_only_when_displaying_percent'),
      'M06':('post_execution_bad_debt','USD','max(remaining debt USD - remaining enabled collateral USD,0)','results/q1/*/*_wallet_risk.parquet;results/q3/*/depeg_*.parquet','MC_mean_and_ES_exported;deterministic_direct_and_incremental_losses_exported'),
      'M07':('system_BDR_t0','percent','100*post_execution_bad_debt_usd/initial_cohort_debt_usd','results/q2/dense_cf_grid.csv','do_not_replace_ratio_of_sums_with_mean_wallet_BDR'),
      'M08':('matched_mean_loss_per_100k_equity','USD_per_100000_equity','mean over matched wallets of BD_i/E_i0*100000','results/q1/*/*_matched_scenarios.parquet','versioned_variant_not_original_ratio_of_sums_percent'),
      'M09':('ES99_of_matched_group_normalized_scenario_loss','USD_per_100000_equity','mean largest 100 of all 10000 scenario group losses including zeros','results/q1/hypothesis_evidence.csv','fixed_tail_mass;not_tie_inclusive_VaR_filter;not_Million_USD'),
      'M10':('executed_repayment_volume','USD','actual simulated debt repaid; round1 and total separately','results/q2/dense_cf_grid.csv','repayment_is_distinct_from_round1_sold_collateral_USD;full_round_ledger_not_published_for_dense_grid'),
      'M11':('execution_slippage','percent','mean executed trade slippage from bounded observed execution-cost curves','results/q2/dense_cf_grid.csv','execution_average_is_not_terminal_oracle_price_drop_or_original_power_depth_formula'),
      'M12':('sequential_eligibility_and_execution','wallet_count','newly_eligible_round_ge_2 and first_executed_round_ge_2 reported separately','results/q2/dense_cf_grid.csv','H2a_17_of_54_uses_eligibility_endpoint;do_not_label_it_completed_liquidations'),
      'M13':('repayment_cascade_amplification','ratio','total_repayment/round1_repayment;null when denominator is zero','results/q2/dense_cf_grid.csv','cohort_counterfactual'),
      'M14':('causal_freeze_volume','not_published','original freeze USD volume is not established by missing routes/unknown quotes','results/q2/dense_cf_grid.csv','not_established;coverage_attempts_are_not_freeze_USD'),
      'M15':('induced_healthy_other_asset_eligibility_rate','fraction_in_storage','newly eligible round>=2 among HF0>=1.10 AND shocked_HF>=1 AND other collateral>=10pct_and_USD100 divided by that eligible population','results/q3/direct_induced_summary.csv','H3_endpoint_1.10;original_dictionary_1.05_is_not_this_endpoint;eligibility_not_execution'),
      'M16':('Comet_incremental_price_eligibility','market_account_count','new Comet price-eligible accounts under one-way shared-price sensitivity','results/q3/compound_direct_induced_summary.csv','scoped_variant;original_CPCI_induced_liquidation_USD_percentage_not_published'),
      'M17':('source_and_edge_intervention_sensitivity','recipient_incremental_loss_USD','effect of blocking source liquidation or selected execution edge; balances retained','results/q3/node_edge_interventions.csv','not_original_realized_edge_transmission_share_percent'),
      'M18':('original_node_DebtRank','not_primary_publication_endpoint','no node-specific realized DebtRank endpoint claimed in this primary hypothesis bundle','results/q3/incremental_loss_network_nulls.json','null_association_and_betweenness_are_not_DebtRank;original_M18_not_published_here')}
    rows=[]
    for row in original:
        name,unit,formula,paths,status=definitions[row['kpi_id']]
        rows.append(dict(kpi_id=row['kpi_id'],original_name=row['kpi_name_en'],original_unit=row['unit'],
            original_formula=row['formula_code_expr'],official_endpoint=name,official_unit=unit,
            official_definition=formula,official_artifacts=paths,definition_relation=status,
            research_acceptance='conditional_or_not_established;not_18_of_18_scientific_acceptance'))
    write_csv(release/'metadata/kpi_release_dictionary.csv',rows)


def build():
    from src.models.n5_readiness import verify_manifest
    if RELEASE.exists():raise FileExistsError('Release exists; never overwrite a frozen release')
    if verify_manifest(ANALYSIS):raise ValueError('Parent analysis hash mismatch')
    parent=json.loads((ANALYSIS/'manifest.json').read_text(encoding='utf-8'))
    for row in parent['inputs']:
        p=Path(row['path']).resolve()
        if not p.is_relative_to(WORKSPACE) or sha256(p)!=row['sha256']:raise ValueError('Unsafe or changed parent input')
    RELEASE.mkdir()
    for folder in ['results','inputs','metadata','provenance']:(RELEASE/folder).mkdir()
    for row in parent['files']:
        p=local_path(ANALYSIS,row['path']);dest=RELEASE/'results'/row['path']
        dest.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(p,dest)
        if sha256(dest)!=row['sha256']:raise RuntimeError('Scientific result changed during freeze')
    shutil.copyfile(ANALYSIS/'manifest.json',RELEASE/'provenance/analysis_manifest.json')
    frozen=[]
    for row in parent['inputs']:
        source=Path(row['path']);relative='inputs/'+row['sha256']+'/'+source.name
        target=RELEASE/relative;target.parent.mkdir(parents=True,exist_ok=True)
        if not target.exists():shutil.copyfile(source,target)
        frozen.append(dict(path=relative,sha256=row['sha256'],original_source_path=row['path']))
    write_csv(RELEASE/'metadata/input_index.csv',frozen)
    for p in AUDIT.glob('*'):
        if p.is_file():shutil.copyfile(p,RELEASE/'provenance'/p.name)
    snapshots=next(r for r in frozen if Path(r['original_source_path']).name=='snapshots.json')
    cfg=json.loads((RELEASE/snapshots['path']).read_text(encoding='utf-8'))
    manifest=dict(version='n5_official_release_v1',release_state='frozen_candidate',
        created_utc=datetime.now(timezone.utc).isoformat(),
        source_analysis_manifest='provenance/analysis_manifest.json',
        source_analysis_manifest_sha256=sha256(ANALYSIS/'manifest.json'),
        scientific_acceptance=parent['scientific_acceptance'],
        snapshot_blocks={k:v['block'] for k,v in cfg['snapshots'].items()},
        inputs=frozen,files=[])
    kpi_metadata(RELEASE,manifest)
    (RELEASE/'README.md').write_text('''# Bundle Q1–Q3 chính thức — 26/10/2026

Đọc `results/REPORT.md` và `results/hypothesis_conclusions.csv` để lấy kết luận nghiên cứu. `metadata/publication_artifacts.csv` là danh sách bảng/hình chính thức; `metadata/kpi_release_dictionary.csv` đối chiếu định nghĩa gốc với chỉ tiêu thực sự được công bố.

Bundle giữ nguyên byte của các kết quả đã nghiệm thu kỹ thuật, kèm bản sao đầy đủ các đầu vào và source_versions đã dùng. B1 là 20458637. Manifest kiểm cả results và inputs nội bộ; không phụ thuộc hash của mã nguồn workspace được sửa về sau. Không sửa số liệu hoặc tái chạy mô hình để làm manifest sạch.

Các manifest lịch sử được giữ làm chứng cứ. Audit phân biệt 96 chuỗi đường dẫn khai báo với 95 tệp sau chuẩn hóa (79 tệp khác phiên bản và 16 đường dẫn cũ thiếu). Các hash lịch sử chưa khôi phục đủ byte được ghi rõ, không được tự coi là nguồn đã xác thực của lần chạy chính thức.

Q1 chính dùng matched 1:1 trước sự kiện; các phân phối khủng hoảng là sensitivity riêng. ES99 là trung bình đúng 100 loss lớn nhất trong 10.000 draw, có cả loss bằng 0. N_seq đủ điều kiện và N_seq thực thi được giữ riêng; H3 dùng HF0>=1.10 và vẫn khỏe sau shock. Không gọi 18 mapping KPI là 18 chỉ tiêu được nghiệm thu khoa học.

KPI M08/M09 dùng đơn vị chuẩn hóa theo 100.000 USD equity trong kết quả chính, không gắn nhãn triệu USD hoặc ratio-of-sums. M14 freeze nhân quả, M16 CPCI đầy đủ, M17 edge-share thực tế và M18 node-specific DebtRank chưa được công bố như chỉ tiêu gốc; xem đối chiếu định nghĩa thay vì lấy biến proxy để điền vào.

Chứng cứ phản hồi giá là kiểm định dự báo quan sát, chưa nhận dạng nhân quả. Full-feedback là sensitivity; kênh Comet là một chiều, không phải mô phỏng vòng kín toàn giao thức.

Kiểm tra độc lập từ workspace:

```powershell
.\\.venv\\Scripts\\python.exe -B -X utf8 -m scripts.n5_finalize_release --stage verify
```

Để tái lập thí nghiệm, khôi phục đúng input_index và source_versions của từng giai đoạn trong một checkout riêng. Các đường dẫn gốc trong provenance là thông tin truy nguyên; kiểm hash bản phát hành dùng các bản sao nội bộ.
''',encoding='utf-8')
    catalog=[]
    primary=[('Q1','results/q1/hypothesis_evidence.csv','paired normalized ES99; filter pre_event_t4_1d for primary'),
        ('Q1','results/q1/paired_hypothesis_tests_adjusted.json','paired tests with Holm; conditional association'),
        ('Q1','results/q1/equity_balance_before_after.csv','matching balance'),
        ('Q1','results/q1/convexity_tests.json','deterministic stress shape sensitivity'),
        ('Q2','results/q2/dense_cf_grid.csv','1458 finite counterfactual cases'),
        ('Q2','results/q2/h2a_paired_contrasts.csv','CF100-CF50; eligibility endpoint'),
        ('Q2','results/q2/u_shape_tests_with_turning_ci.json','boundary slopes and turning-point intervals'),
        ('Q3','results/feedback/route_validation.json','B1 training B2 predictive validation; not causal'),
        ('Q3','results/q3/direct_induced_summary.csv','strict healthy and post-exogenous-healthy population'),
        ('Q3','results/q3/incremental_loss_network_nulls.json','network-label null association'),
        ('Q3','results/q3/incremental_loss_regressions.json','conditional incremental-loss association'),
        ('Q3','results/q3/node_edge_interventions.csv','69 model source/edge interventions'),
        ('Q3','results/q3/compound_direct_induced_summary.csv','48 one-way shared-price Comet sensitivities'),
        ('ALL','results/hypothesis_conclusions.csv','six hypothesis-specific conclusions'),
        ('ALL','results/REPORT.md','official narrative')]
    for q,path,meaning in primary:
        catalog.append(dict(question=q,path=path,sha256=sha256(RELEASE/path),meaning=meaning))
    for p in sorted((RELEASE/'results/figures').glob('*.png')):
        catalog.append(dict(question='FIGURE',path=p.relative_to(RELEASE).as_posix(),sha256=sha256(p),meaning='generated from locked result tables'))
    write_csv(RELEASE/'metadata/publication_artifacts.csv',catalog)
    def index_files():
        return [dict(path=p.relative_to(RELEASE).as_posix(),sha256=sha256(p),bytes=p.stat().st_size)
            for p in sorted(RELEASE.rglob('*')) if p.is_file() and p!=RELEASE/'manifest.json']
    manifest['files']=index_files();write_json(RELEASE/'manifest.json',manifest)
    checks=scientific_checks(RELEASE)
    write_json(RELEASE/'metadata/scientific_consistency.json',checks)
    manifest['files']=index_files();write_json(RELEASE/'manifest.json',manifest)
    if verify_release(RELEASE) or verify_manifest(RELEASE):raise RuntimeError('Frozen release verification failed')
    print('Frozen official candidate:',RELEASE,'files',len(manifest['files']),'input records',len(frozen),flush=True)


def seal(test_report):
    if verify_release(RELEASE):raise ValueError('Cannot seal an inconsistent candidate')
    content=Path(test_report).read_text(encoding='utf-8')
    result_path=Path(test_report).with_suffix('.json')
    result=json.loads(result_path.read_text(encoding='utf-8'))
    if result['exit_code']!=0 or result['stdout_sha256']!=sha256(test_report):
        raise ValueError('Pytest did not pass or its report changed')
    if 'failed' in content.split('warnings summary')[0].lower() or 'passed' not in content:
        raise ValueError('Passing pytest report required')
    shutil.copyfile(test_report,RELEASE/'provenance/pytest_full_suite.txt')
    shutil.copyfile(result_path,RELEASE/'provenance/pytest_full_suite.json')
    tools=RELEASE/'provenance/release_tools';tools.mkdir(exist_ok=True)
    for source in ['scripts/n5_finalize_release.py','src/models/n5_release.py','tests/test_n5_release.py','tests/test_n234_completion.py',
                   'config/n5_research_artifacts.json','docs/CHOT_BUNDLE_Q1_Q3_20261026.md','.gitattributes']:
        shutil.copyfile(WORKSPACE/source,tools/Path(source).name)
    m=json.loads((RELEASE/'manifest.json').read_text(encoding='utf-8'))
    m['release_state']='sealed';m['sealed_utc']=datetime.now(timezone.utc).isoformat()
    m['software_test_report']='provenance/pytest_full_suite.txt'
    m['files']=[dict(path=p.relative_to(RELEASE).as_posix(),sha256=sha256(p),bytes=p.stat().st_size)
        for p in sorted(RELEASE.rglob('*')) if p.is_file() and p!=RELEASE/'manifest.json']
    write_json(RELEASE/'manifest.json',m)
    if verify_release(RELEASE):raise RuntimeError('Seal verification failed')
    print('SEALED',len(m['files']),'files; frozen inputs verified; source results unchanged',flush=True)


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--stage',choices=['audit','build','seal','verify'],required=True)
    ap.add_argument('--test-report',type=Path)
    args=ap.parse_args()
    if args.stage=='audit':audit()
    elif args.stage=='build':build()
    elif args.stage=='seal':
        if args.test_report is None:raise ValueError('Test report required')
        seal(args.test_report)
    else:
        errors=verify_release(RELEASE)
        if errors:raise RuntimeError(errors)
        print(json.dumps(scientific_checks(RELEASE),indent=2))
        print('verify_manifest/release: clean')


if __name__=='__main__':main()
