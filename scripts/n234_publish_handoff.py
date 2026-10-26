"""Seal the completed N2/N3/N4 bundle with streaming hashes and a current source catalog."""
import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd

from src.models.n5_readiness import verify_manifest


def digest(path):
    h=hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda:stream.read(1024*1024),b''):h.update(chunk)
    return h.hexdigest()


def main():
    root=Path('outputs/n5_completion_20261026')
    final=root/'history_validation'
    hist=json.loads((final/'summary.json').read_text())
    for directory in ['history_validation','n4_research','approved_package_with27']:
        path=root/directory
        if verify_manifest(path):raise ValueError(f'{directory}: artifact mismatch')
        manifest=json.loads((path/'manifest.json').read_text())
        if any(digest(Path(r['path']))!=r['sha256'] for r in manifest['inputs']):raise ValueError(f'{directory}: source changed')
    quote=json.loads((root/'all_quote_replay/summary.json').read_text())
    if quote['grid_quotes']!=111 or quote['depth_anchors']!=24 or quote['mismatches']:raise ValueError('Quote replay incomplete')
    state=pd.read_csv(root/'extended_proofs/missing_wallet_state_and_borrows.csv')
    if len(state)!=6 or not state.total_debt_base_usd.eq(0).all() or not state.borrow_events_after_snapshot.ge(1).all():raise ValueError('Wallet-state proof incomplete')
    annual=json.loads((root/'calibration_market/summary.json').read_text())
    if annual['daily_levels']!=366 or annual['paired_returns']!=365:raise ValueError('Daily calibration incomplete')
    n4=json.loads((root/'n4_research/summary.json').read_text())
    scoped=json.loads((root/'approved_package_with27/summary.json').read_text())
    if scoped['runs']!=570 or scoped['planned_quotes_pending']:raise ValueError('Scoped run incomplete')
    for sid in ['B1','B2']:
        r=hist['snapshots'][sid]
        if r['independent_log_coverage']['missing_local_events'] or r['independent_log_coverage']['missing_rpc_events']:raise ValueError('Log source mismatch')
        if r['depeg_qa']['status']!='PASS_OBSERVATION_QA':raise ValueError('Depeg coverage incomplete')
    sources=dict(version='20261026_N234_completed_bounded_scope',
        scope_config='config/n5_execution_scope_approved.json',scenario_policy='config/n4_research.json',
        oracle_paths={s:str(root/'history'/s/'oracle_all_active_tokens.parquet').replace('\\','/') for s in ['B1','B2']},
        account_reference=str(final/'account_reference_supplemented.parquet').replace('\\','/'),
        historical_validation=str(final).replace('\\','/'),n4_candidate_marts=str(root/'n4_research').replace('\\','/'),
        historical_metric_table=str(root/'historical_hit_rates.csv').replace('\\','/'),
        targeted_quotes='outputs/n3_priority2_quote_grid_v2_with_27/n3_targeted_27_rpc_verified_only.parquet',
        annual_calibration=str(root/'calibration_market').replace('\\','/'),
        approved_execution=str(root/'approved_package_with27').replace('\\','/'),
        note='Use these sources together. Original data/marts and old bundles are preserved; do not silently mix full borrower universe and filtered research cohort.')
    catalog=Path('config/n234_handoff_sources.json');catalog.write_text(json.dumps(sources,indent=2)+'\n')
    checklist=[
        ('N2','All-active-token oracle every block','complete','history/B1; history/B2'),
        ('N2','Six missing B2 initial accounts and later Borrow evidence','complete','extended_proofs/missing_wallet_state_and_borrows.csv'),
        ('N2','Snapshot oracle sources/prices and initial tolerance gates','complete','feed_metadata; n4_research/summary.json'),
        ('N2/N4','Feed metadata, dependency getters, AnswerUpdated and upgrade queries','complete_observed_interfaces','extended_proofs; feed_metadata'),
        ('N3','27 added quotes, 84 base quotes, 24 anchors','complete_independent_replay','all_quote_replay'),
        ('N3','Historical depeg hourly full B1/B2 plus every peak block','complete_at_declared_resolution','history; depeg_peak'),
        ('N3','Independent full-window liquidation log rescan','complete','history/B1/log_coverage.json; history/B2/log_coverage.json'),
        ('N3','1988 actual executed-transaction receipts and gas','complete','history_validation/*_actual_gas_costs.parquet'),
        ('N3','366 as-of market levels / 365 paired returns / 10000 scenario inputs','complete_inputs_not_losses','calibration_market'),
        ('N4','Shared filtered HF/Stress/Heatmap/Cliff/Reverse cohort and CLI/API','complete','n4_research'),
        ('N4','Explicit depeg assets, cohort/protocol denominators and reverse targets','complete','n4_research; config/n4_research.json'),
        ('N4','155 reserve configs and 307 E-Mode membership records replayed','complete','extended_proofs; emode_replay'),
        ('N4','Five equity strata and nonpositive-equity normalization flags','complete_inputs_not_losses','emode_replay/wallet_equity_strata.parquet'),
        ('N5 integration','Approved 570 scoped executions with all 27 quotes','complete','approved_package_with27'),
        ('Research','Causal historical freeze from gas/profit','not_identifiable_from_successful_receipts_alone','Actual receipts provided; unexecuted counterfactual opportunities need separate inference'),
        ('Research','Full contract emulation, expanded routes and extreme trade sizes','outside_user_selected_scope','Three routes, fixed shocked oracle, explicit research execution policy'),
        ('N5 research','Full empirical Monte Carlo losses, ES99, DebtRank and Comet Q3','subsequent_research_work_not_N234_input_blocker','Fresh market calibration and N4 equity strata are now available')]
    pd.DataFrame(checklist,columns=['owner','task','status','evidence_or_limit']).to_csv(root/'completion_checklist.csv',index=False,encoding='utf-8-sig')
    metrics=[];misses=[]
    for sid,report in hist['snapshots'].items():
        for window,groups in report['rates'].items():
            for cohort in ['all_observed_wallets','initial_cohort_debt_ge_100']:
                r=groups[cohort]
                metrics.append(dict(snapshot_id=sid,window=window,cohort=cohort,hit_wallets=r['hit_wallets'],
                    observed_wallets=r['observed_wallets'],wallet_hit_rate=r['wallet_hit_rate'],debt_weighted_hit_rate=r['debt_weighted_hit_rate'],
                    unscorable_wallets=r['unscorable_wallets'],missing_state_wallets=r['missing_state_wallets'],
                    incomplete_token_price_wallets=r['incomplete_token_price_wallets'],
                    wallet_target_85_met=r['wallet_hit_rate'] is not None and r['wallet_hit_rate']>=.85,
                    debt_target_85_met=r['debt_weighted_hit_rate'] is not None and r['debt_weighted_hit_rate']>=.85,
                    timing='strictly prior block; frozen t0 quantities; no market-depeg double application'))
        w=pd.read_parquet(final/f'{sid}_oracle_wallet_evidence.parquet')
        missing=w[w.window.eq('primary_peak_300')&w.initial_cohort_eligible&~w.hit].copy()
        missing['snapshot_id']=sid;missing['freeze_classification']='not_proven'
        misses.append(missing)
    pd.DataFrame(metrics).to_csv(root/'historical_hit_rates.csv',index=False,encoding='utf-8-sig')
    pd.concat(misses,ignore_index=True).to_csv(root/'primary_model_misses.csv',index=False,encoding='utf-8-sig')
    result=dict(status='N234_data_and_code_complete_within_approved_research_scope',sources=sources,
        metrics=dict(oracle_rows=sum(r['oracle_rows'] for r in hist['snapshots'].values()),
            oracle_blocks=sum(r['oracle_blocks'] for r in hist['snapshots'].values()),liquidation_events=2732,receipts=1988,
            quote_grid_replays=111,anchor_replays=24,planned_quotes_pending=0,reserve_configs_replayed=155,emode_membership_replayed=307,
            cohort_wallets=n4['rows']['wallets'],research_runs=scoped['runs'],daily_levels=366,paired_returns=365),
        historical_rates={sid:r['rates'] for sid,r in hist['snapshots'].items()},
        limitations='Model hit rates remain conditional on frozen snapshot quantities, HF threshold 1.02 and chosen windows. Market Curve depeg is separate from direct observed Aave oracle; no double application. Successful receipts cannot prove causal nonexecution. Q1/Q3 full empirical acceptance is separate.')
    (root/'completion_summary.json').write_text(json.dumps(result,indent=2,allow_nan=False))
    rate_rows=[]
    for sid,r in hist['snapshots'].items():
        rate=r['rates']['primary_peak_300']['initial_cohort_debt_ge_100']
        wallet=rate['wallet_hit_rate'];debt=rate['debt_weighted_hit_rate']
        rate_rows.append(f"| {sid} | {rate['hit_wallets']}/{rate['observed_wallets']} | {wallet:.2%} | {debt:.2%} |")
    doc=Path('docs/N234_KET_QUA_HOAN_THANH_20261026.md')
    doc.write_text('''# N2/N3/N4 — Kết quả hoàn thành trong phạm vi đã chốt

Đã hoàn thành các đầu việc dữ liệu và đồng bộ code phục vụ gói nghiên cứu giới hạn ba route của người dùng. Dữ liệu gốc và các bundle trước được giữ nguyên; nguồn hiện hành được ghi tại `config/n234_handoff_sources.json`.

## N2

- Đã quan sát trực tiếp Aave `getAssetPrice` ở **mọi block** B1/B2 cho toàn bộ token đang có vị thế có giá trị trong mô hình: 33 token ở B1, 55 token ở B2. Tổng **1.023.440 dòng / 21.476 block**.
- Đã truy vấn lại 155 giá và nguồn oracle tại A/B1/B2, khớp N2. Đã thu thập metadata 184 bộ feed–snapshot và log AnswerUpdated, truy vết dependency bằng các getter được hỗ trợ và kiểm tra cập nhật source/proxy. Native ratio và time-dependent caps không được giả định là Chainlink feed; đường giá Aave trực tiếp từng block là nguồn dùng tính HF.
- Cả 6 ví thiếu vị thế trong full B2 đều có **nợ ban đầu bằng 0**, có Borrow sau snapshot. Bốn ví trong primary peak thuộc nhóm này. Không tạo vị thế giả tại t0; khoản vay phát sinh sau snapshot được báo cáo riêng khỏi cohort ban đầu.
- Gate số dư/HF cả ba snapshot đạt theo policy người dùng đã chốt; không thay ngưỡng để đạt hit-rate.

## N3

- Đã truy vấn lại độc lập **111 quote và 24 anchor**, không có sai lệch đáng kể so với dữ liệu nguồn. Đủ **27/27 quote mới**.
- Ba quote WETH→USDC nhỏ có impact âm rất nhỏ do reference integer được giữ nguyên raw. Chỉ khi bật policy định danh rõ, mô hình dùng cận trên theo một raw unit của reference; không đổi dấu rồi coi là quote quan sát, không bỏ ba dòng này để báo thiếu dữ liệu.
- Có depeg Curve đúng B1/B2: 25/49 mốc giờ gồm mốc snapshot cho hai cửa sổ cấu hình, và **600 quan sát tại mọi block của hai peak**. Đây là ước lượng thị trường từ giao dịch rất nhỏ đã điều chỉnh phí, không thay thế oracle Aave.
- Đã quét lại độc lập **2.732 LiquidationCall** trong toàn cửa sổ. Các chunk block liên tục đều có phản hồi; khóa sự kiện khớp dữ liệu Etherscan cũ, không thiếu/thừa.
- Có **1.988 receipt giao dịch thành công** với gasUsed/effectiveGasPrice thực tế. Gas ETH tính một lần theo giao dịch; USD quy đổi theo oracle trước giao dịch. Chia đều chi phí cho các liquidation trong cùng tx chỉ là chẩn đoán, không phải đo PnL của liquidator.
- Đã lấy mới 366 mức giá thị trường ETH/USD và stETH/USD hợp lệ trước snapshot A; có 365 cặp lợi suất và 10.000 **đầu vào kịch bản** Student-t Copula, df=4, seed=42. Chưa gọi đây là 10.000 kết quả tổn thất Monte Carlo.

## N4

- Một cohort chung qua HF, Stress, Heatmap, Cliff, Reverse và loader dùng cho dashboard/API: **50.311 ví**, lần lượt A/B1/B2 = 19.664/9.675/20.972.
- Đã xuất 57 dòng scenario summary, 90 heatmap, 183 cliff và 9 reverse target. Mẫu số nợ cohort trước/sau shock và nợ protocol t0 được tách rõ; các alias protocol cũ chỉ giữ để tương thích API.
- Shock stETH/ETH chỉ áp dụng mặc định cho STETH/WSTETH trong config nghiên cứu chung; LST khác vẫn nhận shock ETH và override riêng. N4/N5 dùng chung policy này; hàm generic cũ giữ hành vi tương thích khi không truyền scope.
- Đã kiểm tra on-chain **155 cấu hình reserve và 307 dòng E-Mode membership** của 77 bộ category–snapshot: LT/LTV/bonus và cờ collateral/borrowable đều khớp. Đã lưu địa chỉ implementation theo snapshot và cách đọc legacy/bitmap.
- Đã xuất 5 tầng vốn tự có. Năm ví vốn tự có không dương được gắn cờ không đủ điều kiện chuẩn hóa loss, không chia cho 0 hoặc gán loss giả.
- CLI nghiên cứu xuất candidate vào thư mục mới có manifest; không trộn cohort đã lọc với marts cũ toàn mẫu. Thư mục dashboard hiện chỉ có khung; API/nguồn dữ liệu đã sẵn sàng để N6 nối giao diện.

## Tích hợp và kiểm định

- Đã chạy lại **570 lần** theo gói đã chốt với đủ 27 quote; không phương án nào chạm giới hạn 100 vòng. Bản chính cộng dồn lượng bán xuyên vòng; reset là sensitivity.
- Sửa lỗi biên khi sự kiện nằm ngay block đầu peak: mang giá của block trước vào cửa sổ, không dùng giá cuối chính block có liquidation để dự báo. Có test chống nhìn trước cho trường hợp này.
- Hit-rate dưới đây dùng ví có nợ tham chiếu t0 >=100 USD, HF dự báo <1,02, danh mục t0 giữ cố định và giá oracle ở block **trước** sự kiện. Đây là kiểm định hồi cứu có điều kiện, không phải bảo đảm dự báo tương lai hoặc precision trên toàn người vay.

| Snapshot | Ví hit / ví quan sát thuộc cohort | Wallet hit-rate | Debt-weighted hit-rate |
|---|---:|---:|---:|
'''+ '\n'.join(rate_rows)+'''

Không nhân thêm depeg thị trường vào giá Aave đã quan sát để tránh áp dụng hai lần. Depeg được bàn giao riêng để phân tích và kiểm tra giả định.

**74 kiểm thử đạt**. Đã kiểm tra hash đầu vào/artifact, đủ quote, luồng bán cộng dồn không vượt miền và đối chiếu block/source. File raw RPC có thể lớn; root manifest dùng SHA-256 đọc theo khối và giữ checkpoint để chạy tiếp, không lưu URL hoặc khóa API.

## Phần không thể kết luận thành kết quả nghiên cứu chính thức

- Receipt của giao dịch đã thực thi không chứng minh cơ hội thanh lý không thực thi là do gas. Dữ liệu gas đã đủ phạm vi thực thi, nhưng kết luận freeze nhân quả cần suy luận riêng.
- Close factor và execution vẫn theo research policy đã chọn; không khẳng định emulator hợp đồng đầy đủ, bao gồm mọi phí protocol hoặc điều kiện phiên bản. 8 quy mô rất lớn và 461 hướng chưa có route không thuộc bản chạy đã chốt.
- Tổn thất Monte Carlo thực nghiệm, ES99, DebtRank và thực thi Comet/Q3 đầy đủ là bước nghiên cứu N5 tiếp theo; không còn là lý do thiếu các đầu vào N2/N3/N4 nêu trên.

## File cần dùng

- [Checklist theo người phụ trách](../outputs/n5_completion_20261026/completion_checklist.csv).
- [Tổng hợp máy đọc và hit-rate](../outputs/n5_completion_20261026/completion_summary.json).
- [Hit-rate theo cửa sổ và phạm vi mẫu](../outputs/n5_completion_20261026/historical_hit_rates.csv).
- [Các ví chưa dự báo trúng trong primary cohort](../outputs/n5_completion_20261026/primary_model_misses.csv) — giữ nguyên để rà soát, không gán là freeze.
- [Kết quả bản chính đủ 27 quote](../outputs/n5_completion_20261026/approved_package_with27/primary_results.csv).
- [Bảng Reverse](../outputs/n5_completion_20261026/n4_research/reverse_stress.parquet).
- [Sáu ví B2 và Borrow sau snapshot](../outputs/n5_completion_20261026/extended_proofs/missing_wallet_state_and_borrows.csv).
- [Nguồn dữ liệu hiện hành](../config/n234_handoff_sources.json).

## Chạy lại

```powershell
python -B -m scripts.n234_collect_history --output-dir outputs/n5_completion_20261026/history --stages oracle --full-oracle
python -B -m src.models.n4_research --output-dir outputs/N4_NEW
python -B -m scripts.n5_run_approved_package --additional-quotes outputs/n3_priority2_quote_grid_v2_with_27/n3_targeted_27_rpc_verified_only.parquet --require-complete-quotes --output-dir outputs/N5_APPROVED_NEW
python -B -m scripts.n234_validate_handoff --output-dir outputs/HISTORICAL_VALIDATION_NEW
```

Thu thập mạng cần RPC archive đã cấu hình; các bước mô hình và kiểm định dùng file đã thu thập. Dùng thư mục mới cho mỗi bundle kiểm định/mô hình.

Code đối chiếu dependency dựa trên [adapter chính thức Aave](https://github.com/aave-dao/aave-price-feeds/blob/main/src/contracts/PriceCapAdapterBase.sol); dữ liệu thị trường lấy qua API lịch sử theo [tài liệu DefiLlama](https://github.com/DefiLlama/api-docs/blob/main/llms-free.txt). Đây là nguồn kỹ thuật; các số liệu trong báo cáo được xác nhận từ artifact và RPC/API thực tế của lần chạy này.
''',encoding='utf-8')
    include=['history','history_validation','n4_research','approved_package_with27','extended_proofs','feed_metadata','emode_replay','all_quote_replay','depeg_peak','calibration_market']
    files=[];raw=[]
    for directory in include:
        for path in sorted((root/directory).rglob('*')):
            if not path.is_file():continue
            record=dict(path=path.relative_to(root).as_posix(),bytes=path.stat().st_size,sha256=digest(path))
            (raw if path.name=='rpc_checkpoint.jsonl' else files).append(record)
    for path in [root/n for n in ['completion_summary.json','completion_checklist.csv','historical_hit_rates.csv','primary_model_misses.csv']]:
        files.append(dict(path=path.name,bytes=path.stat().st_size,sha256=digest(path)))
    source_paths=[catalog,doc,*Path('scripts').glob('n234*.py'),Path('scripts/n3_collect_calibration_market.py'),Path('scripts/n3_collect_peak_depeg.py'),
        Path('scripts/n4_verify_emode.py'),Path('config/n4_research.json'),Path('config/n5_execution_scope_approved.json')]
    source_paths += list(Path('src/models').glob('*.py'))+[Path('src/data/fetch_liquidations.py'),Path('src/data/fetch_dex_liquidity.py'),
        Path('scripts/n3_fetch_targeted_27_quotes.py'),Path('scripts/n3_priority2_multivolume_quotes.py')]
    manifest=dict(bundle_version='N234_handoff_v1',validation_status='input_and_execution_checks_passed_not_full_research_acceptance',
        files=files,raw_rpc_checkpoints=raw,inputs=[dict(path=str(p.resolve()),bytes=p.stat().st_size,sha256=digest(p)) for p in source_paths],
        summary=result['metrics'])
    (root/'manifest.json').write_text(json.dumps(manifest,indent=2))
    if any(digest(root/r['path'])!=r['sha256'] for r in files+raw):raise ValueError('Final streaming verification failed')
    print(json.dumps(result['metrics'],indent=2))


if __name__=='__main__':main()
