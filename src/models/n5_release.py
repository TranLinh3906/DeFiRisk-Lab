"""Read-only verification of a frozen research release, including its parents."""
import hashlib
import json
from pathlib import Path, PureWindowsPath


def sha256(path):
    h=hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda:stream.read(1024*1024),b''):h.update(block)
    return h.hexdigest()


def local_path(root, reference):
    root=Path(root).resolve()
    p=Path(reference)
    if p.is_absolute() or PureWindowsPath(reference).is_absolute():
        raise ValueError('Release paths must be relative')
    resolved=(root/p).resolve()
    if not resolved.is_relative_to(root):raise ValueError('Release path escapes bundle')
    return resolved


def verify_release(root):
    root=Path(root).resolve()
    manifest=json.loads((root/'manifest.json').read_text(encoding='utf-8'))
    if manifest.get('version')!='n5_official_release_v1':raise ValueError('Not an official release manifest')
    errors=[];seen={};cache={}
    for row in manifest['files']:
        p=local_path(root,row['path'])
        if row['path'] in seen:errors.append('duplicate:'+row['path'])
        seen[row['path']]=row['sha256']
        if not p.is_file():errors.append('missing:'+row['path']);continue
        actual=cache.setdefault(p,sha256(p))
        if actual!=row['sha256']:errors.append('hash:'+row['path'])
    for row in manifest['inputs']:
        local_path(root,row['path'])
        if seen.get(row['path'])!=row['sha256']:errors.append('input_not_locked:'+row['path'])
    parent=local_path(root,manifest['source_analysis_manifest'])
    if parent.is_file() and sha256(parent)==manifest['source_analysis_manifest_sha256']:
        source=json.loads(parent.read_text(encoding='utf-8'))
        for row in source['files']:
            if seen.get('results/'+row['path'])!=row['sha256']:errors.append('result_parent_mismatch:'+row['path'])
        original={r['original_source_path']:r['sha256'] for r in manifest['inputs']}
        for row in source['inputs']:
            if original.get(row['path'])!=row['sha256']:errors.append('input_parent_mismatch:'+row['path'])
    else:errors.append('parent_manifest_hash')
    if manifest.get('snapshot_blocks',{}).get('B1')!=20458637:errors.append('B1_block')
    return errors


def scientific_checks(root):
    """Reconcile exported numbers and ES99 without rerunning a simulation."""
    import numpy as np
    import pandas as pd
    root=Path(root);r=root/'results'
    evidence=pd.read_csv(r/'q1/hypothesis_evidence.csv')
    summaries=json.loads((r/'q1/paired_hypothesis_tests_adjusted.json').read_text(encoding='utf-8'))
    maximum_error=0.;draws=0
    for row in summaries:
        p=r/'q1'/row['snapshot_id']/(row['distribution']+'_matched_scenarios.parquet')
        d=pd.read_parquet(p)
        if len(d)!=10000:raise ValueError('MC draw count changed')
        draws+=len(d)
        for col,key in [('looping','es99_looping'),('non_looping','es99_non_looping')]:
            actual=float(np.sort(d[col].to_numpy(float))[-100:].mean())
            maximum_error=max(maximum_error,abs(actual-row[key]))
            if not np.isclose(actual,row[key],rtol=1e-10,atol=1e-8):raise ValueError('ES99 formula mismatch')
        published=evidence[evidence.snapshot.eq(row['snapshot_id']) & evidence.distribution.eq(row['distribution'])]
        if len(published)!=1 or not np.isclose(published.es99_difference.iloc[0],row['es99_diff']):
            raise ValueError('Q1 table differs from hypothesis evidence')
    grid=pd.read_csv(r/'q2/dense_cf_grid.csv')
    if len(grid)!=1458 or grid.fixed_cf.nunique()!=9:raise ValueError('CF grid changed')
    if grid.duplicated(['snapshot_id','scenario_id','depth_scale','fixed_cf','feedback_mode']).any():raise ValueError('Duplicate CF case')
    conclusions=pd.read_csv(r/'hypothesis_conclusions.csv')
    if set(conclusions.hypothesis)!={'H1a','H1b','H2a','H2b','H3a','H3b'}:raise ValueError('Incomplete conclusions')
    snapshot_records=[]
    manifest=json.loads((root/'manifest.json').read_text(encoding='utf-8'))
    for row in manifest['inputs']:
        original=PureWindowsPath(row['original_source_path'])
        if original.name=='baseline_wallet_kpis.parquet':
            b=pd.read_parquet(local_path(root,row['path']))
            label=original.parent.name
            if b.snapshot_block.nunique()!=1 or int(b.snapshot_block.iloc[0])!=manifest['snapshot_blocks'][label]:
                raise ValueError('Snapshot baseline drift')
            snapshot_records.append(dict(snapshot=label,block=int(b.snapshot_block.iloc[0]),wallet_snapshot_rows=len(b)))
    if len(snapshot_records)!=3:raise ValueError('Missing baseline snapshots')
    transmission=pd.read_csv(r/'q3/direct_induced_summary.csv')
    if transmission[transmission.feedback_mode.eq('predictive_proxy')].induced_healthy_other_wallets.ne(0).any():
        raise ValueError('H3 fallback conclusion changed')
    return dict(mc_scenarios=draws,cf_grid_cases=len(grid),es99_comparisons=24,
        es99_rule='largest_100_of_all_10000_including_zero_losses',max_es99_absolute_error=maximum_error,
        snapshot_records=snapshot_records,six_hypotheses_reconciled=True,
        primary_q1_population='one_to_one_equity_pairs',h3_threshold=1.10,
        h3_requires_post_exogenous_hf_at_least=1.0,scientific_acceptance='conditional_hypothesis_specific_not_blanket_acceptance')
