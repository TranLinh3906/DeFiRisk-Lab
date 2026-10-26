"""Add wallet-level structural diagnostics to an already verified N5 bundle."""
import argparse
import hashlib
import json
from pathlib import Path
import numpy as np
import pandas as pd
from scipy import sparse

from src.models.n5_readiness import verify_manifest
from src.models.n5_network_statistics import token_pair_betweenness, wallet_associations


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--bundle', type=Path, default=Path('outputs/n5_review_corrected_20261026'))
    ap.add_argument('--refresh', action='store_true', help='Recompute structural diagnostics after a code correction')
    args = ap.parse_args(argv)
    root = args.bundle
    if verify_manifest(root):
        raise ValueError('Bundle hash mismatch')
    marker = root / 'q3_wallet_structural_tests.json'
    if marker.exists() and not args.refresh:
        raise FileExistsError('Structural stage already finalized')
    prior = root / ('manifest.before_structural_refresh.json' if args.refresh else 'manifest.before_structural_tests.json')
    if prior.exists():
        raise FileExistsError('Preserved predecessor manifest already exists')
    prior.write_bytes((root / 'manifest.json').read_bytes())
    manifest = json.loads(prior.read_text(encoding='utf-8'))
    stage_sources = [Path(__file__).resolve(), Path('src/models/n5_network_statistics.py').resolve()]
    for source in manifest['inputs']:
        path = Path(source['path'])
        if (hashlib.sha256(path.read_bytes()).hexdigest() != source['sha256']
                and not (args.refresh and path.resolve() in stage_sources)):
            raise ValueError(f'Input changed since base run: {path}')
    positions_path = Path('outputs/n5_completion_20261026/n4_research/simulation_positions.parquet')
    positions = pd.read_parquet(positions_path)
    compound = pd.read_parquet(root / 'compound/exposures_usd.parquet')
    reports = {}
    for sid in ['A', 'B1', 'B2']:
        baseline = pd.read_parquet(root / sid / 'baseline_wallet_kpis.parquet')
        risk = pd.read_parquet(root / sid / 'mc_wallet_risk.parquet')
        # Execution screens eligible wallets for speed. Restore the whole
        # cohort denominator before publishing its per-scenario system BDR.
        execution = pd.read_csv(root / sid / 'mc_execution.csv')
        execution['initial_cohort_debt_usd'] = float(baseline.debt_usd.sum())
        execution['post_execution_bdr_pct'] = 100 * execution.post_execution_bad_debt_usd / execution.initial_cohort_debt_usd
        execution.to_csv(root / sid / 'mc_execution.csv', index=False)
        nodes = pd.read_parquet(root / sid / 'network_wallet_nodes.parquet')
        block = int(baseline.snapshot_block.iloc[0])
        p = positions[positions.snapshot_block.eq(block)]
        comp = compound[compound.snapshot_block.eq(block) & compound.wallet_address.isin(baseline.wallet_address)]
        token_list = sorted(set(p.token_address) | set(comp.token_address))
        wi = {a: i for i, a in enumerate(baseline.wallet_address)}
        ti = {a: i for i, a in enumerate(token_list)}
        p_values = (p.collateral_amount * p.is_collateral_enabled + p.debt_amount) * p.price_usd
        matrix = sparse.csr_matrix((np.r_[p_values.to_numpy(), comp.value_usd.to_numpy()],
            ([wi[a] for a in p.wallet_address] + [wi[a] for a in comp.wallet_address],
             [ti[t] for t in p.token_address] + [ti[t] for t in comp.token_address])),
             shape=(len(baseline), len(token_list)))
        matrix.eliminate_zeros()
        print(f'{sid}: exact weighted token-pair betweenness', flush=True)
        values = token_pair_betweenness(matrix)
        lookup = dict(zip(baseline.wallet_address, values))
        nodes['weighted_token_pair_betweenness'] = nodes.wallet_address.map(lookup)
        nodes.to_parquet(root / sid / 'network_wallet_nodes.parquet', index=False)
        nodes.sort_values('weighted_token_pair_betweenness', ascending=False).head(200).to_csv(
            root / sid / 'top200_wallet_bridges.csv', index=False)
        reports[sid] = wallet_associations(nodes, risk, baseline)
    marker.write_text(json.dumps(reports, indent=2, ensure_ascii=False, allow_nan=False), encoding='utf-8')
    report = root / 'REPORT.md'
    appendix = '''

## Kiểm định cấu trúc bổ sung

`q3_wallet_structural_tests.json` và `A|B1|B2/top200_wallet_bridges.csv` bổ sung betweenness có trọng số giữa các cặp token (tính mọi đường đi ngắn nhất, gồm trường hợp hòa), hồi quy liên hệ BDR với centrality có kiểm soát quy mô vốn và HF, cùng kiểm tra đuôi Pareto bằng bootstrap KS. Những phép này là chẩn đoán cấu trúc/liên hệ; không biến tương quan thành bằng chứng nhân quả hoặc chứng minh power-law. HC3 không loại bỏ phụ thuộc mạng.

Để tái lập bước bổ sung sau runner chính:

```powershell
.\\.venv\\Scripts\\python.exe -B -X utf8 -m scripts.n5_finalize_structural_review --bundle outputs/N5_REVIEW_NEW
```
'''
    if '## Kiểm định cấu trúc bổ sung' not in report.read_text(encoding='utf-8'):
        report.write_text(report.read_text(encoding='utf-8') + appendix, encoding='utf-8')
    def digest(path):
        return hashlib.sha256(path.read_bytes()).hexdigest()
    manifest['inputs'] = [s for s in manifest['inputs'] if Path(s['path']).resolve() not in stage_sources]
    for path in stage_sources:
        manifest['inputs'].append(dict(path=str(path.resolve()), sha256=digest(path)))
    manifest['structural_postprocessing'] = dict(base_manifest='manifest.before_structural_tests.json',
        weighted_betweenness='token_endpoints_inverse_USD_exposure_distance',
        distance_precision='Decimal_dynamic_range_plus_64_digits',
        mc_execution_denominator='whole_snapshot_cohort_initial_debt_including_zero_loss_wallets',
        scientific_acceptance='conditional_diagnostics_only')
    manifest['files'] = [dict(path=p.relative_to(root).as_posix(), bytes=p.stat().st_size, sha256=digest(p))
        for p in root.rglob('*') if p.is_file() and p != root / 'manifest.json']
    (root / 'manifest.json').write_text(json.dumps(manifest, indent=2, ensure_ascii=False, allow_nan=False), encoding='utf-8')
    if verify_manifest(root):
        raise RuntimeError('Finalized manifest mismatch')
    print('Structural diagnostics and manifest finalized', flush=True)


if __name__ == '__main__':
    main()
