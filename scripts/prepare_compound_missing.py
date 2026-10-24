"""Export exact unqueried Compound tuples and non-overlapping recovery inputs. No RPC."""
import argparse
import hashlib
import json
from pathlib import Path
import sqlite3

import pandas as pd


def prepare(source, aave, metadata, markets, output, parts=6):
    if output.exists():
        raise FileExistsError('Use a new recovery directory')
    report = json.loads((source/'merge_report.json').read_text(encoding='utf-8'))
    with sqlite3.connect((source/'compound_consolidated_partial.sqlite').resolve().as_uri()+'?mode=ro', uri=True) as con:
        coverage = pd.read_sql_query('SELECT * FROM query_coverage', con)
    keys = ['snapshot_id','snapshot_block','market_id','wallet_address']
    if coverage.duplicated(keys).any() or not set(coverage.covered) <= {0,1}:
        raise ValueError('Invalid coverage table')
    missing = coverage[coverage.covered.eq(0)].sort_values(keys).reset_index(drop=True)
    if len(missing) != report['missing_queries'] or len(coverage) != report['expected_queries']:
        raise ValueError('Coverage does not match merge report')
    # Verify sources have not changed since this coverage was reconstructed.
    for record in report['sources']:
        path = Path(record['directory'])/'n3_compound_quality.json'
        if hashlib.sha256(path.read_bytes()).hexdigest() != record['quality_sha256']:
            raise ValueError('Shard QA changed; rebuild coverage before exporting missing queries')
    meta = json.loads(metadata.read_text(encoding='utf-8'))
    if {k:v['snapshot_block'] for k,v in meta['snapshots'].items()} != report['snapshot_blocks']:
        raise ValueError('Metadata differs from the legacy A/B coverage')
    market_rows = pd.read_csv(markets)
    available_markets = set(market_rows.market_id)
    groups = missing.groupby(['snapshot_id','snapshot_block','wallet_address']).market_id.agg(set)
    if any(group != available_markets for group in groups):
        raise ValueError('Some wallet/snapshot needs only one market: use an exact-tuple crawler instead')
    accounts = pd.read_parquet(aave)
    pair_keys = ['snapshot_id','snapshot_block','wallet_address']
    accounts = accounts.merge(missing[pair_keys].drop_duplicates(), on=pair_keys, validate='many_to_one')
    if accounts.duplicated(pair_keys).any() or len(accounts) != len(groups):
        raise ValueError('Missing/duplicate Aave account evidence')
    if not accounts.total_debt_base_usd.gt(0).all():
        raise ValueError('Recovery Aave rows must be eligible borrowers')
    unique = sorted(missing.wallet_address.unique())
    owner = {wallet:i % parts for i,wallet in enumerate(unique)}
    missing['recovery_part'] = missing.wallet_address.map(owner)
    wallets = missing.groupby('wallet_address').agg(
        missing_queries=('market_id','size'), snapshots=('snapshot_id',lambda v: '|'.join(sorted(set(v))))).reset_index()
    wallets['recovery_part'] = wallets.wallet_address.map(owner)
    output.mkdir(parents=True)
    missing.to_csv(output/'missing_queries.csv', index=False, encoding='utf-8-sig')
    wallets.to_csv(output/'missing_wallets_unique.csv', index=False, encoding='utf-8-sig')
    (output/'missing_wallets_unique.txt').write_text('\n'.join(unique)+'\n', encoding='utf-8')
    accounts.to_parquet(output/'aave_missing_pairs.parquet', index=False)
    (output/'snapshot_metadata_AB_frozen.json').write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding='utf-8')
    market_rows.to_csv(output/'markets.csv', index=False)
    rows=[]
    for part in range(parts):
        folder=output/f'part_{part:02d}';folder.mkdir()
        todo=missing[missing.recovery_part.eq(part)]
        selected=accounts[accounts.wallet_address.map(owner).eq(part)]
        todo.to_csv(folder/'missing_queries.csv', index=False, encoding='utf-8-sig')
        selected.to_parquet(folder/'aave_missing_pairs.parquet', index=False)
        rows.append(dict(part=part, unique_wallets=todo.wallet_address.nunique(),
                         wallet_snapshot_pairs=len(selected), queries=len(todo)))
    for (sid, market), subset in missing.groupby(['snapshot_id','market_id']):
        subset.to_csv(output/f'missing_{sid}_{market}.csv', index=False, encoding='utf-8-sig')
    pd.DataFrame(rows).to_csv(output/'parts_summary.csv', index=False, encoding='utf-8-sig')
    summary=dict(scope='Legacy A/B only; not historical B1/B2',
        snapshot_blocks=report['snapshot_blocks'], missing_queries=len(missing),
        unique_wallets=len(unique), wallet_snapshot_pairs=len(accounts),
        overlap_with_covered_queries=0, parts=rows,
        coverage_evidence=report['coverage_evidence'],
        by_snapshot_market=missing.groupby(['snapshot_id','market_id']).size().reset_index(name='queries').to_dict('records'))
    (output/'summary.json').write_text(json.dumps(summary,indent=2,ensure_ascii=False),encoding='utf-8')
    files={p.relative_to(output).as_posix():hashlib.sha256(p.read_bytes()).hexdigest()
           for p in output.rglob('*') if p.is_file()}
    (output/'manifest.json').write_text(json.dumps({'files':files,'status':'recovery_inputs_only'},indent=2),encoding='utf-8')
    print(json.dumps(summary,indent=2))


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir',type=Path,required=True)
    args=parser.parse_args()
    prepare(Path('N3_Compound_Q3_Overlap_VSCode/data/processed/compound/consolidated_partial_20261022'),
        Path('data/processed/aave/account_reference.parquet'),Path('data/processed/aave/snapshot_metadata.json'),
        Path('N3_Compound_Q3_Overlap_VSCode/config/n3_compound_markets.csv'),args.output_dir)
