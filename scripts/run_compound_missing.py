"""Run the existing crawler on a frozen missing-only recovery package.

No RPC with --dry-run. Actual crawling requires the existing Compound environment.
Outputs/checkpoints are isolated from original shard exports.
"""
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import sys


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--bundle',type=Path,required=True)
    parser.add_argument('--part',type=int,help='0..5; omit to run all missing pairs in one process')
    parser.add_argument('--dry-run',action='store_true')
    parser.add_argument('--overwrite',action='store_true')
    parser.add_argument('--rpc-interval',type=float,default=1.)
    args=parser.parse_args()
    bundle=args.bundle.resolve()
    manifest=json.loads((bundle/'manifest.json').read_text(encoding='utf-8'))
    for relative,digest in manifest['files'].items():
        p=(bundle/relative).resolve()
        if not p.is_relative_to(bundle) or hashlib.sha256(p.read_bytes()).hexdigest()!=digest:
            raise ValueError('Recovery input hash mismatch: '+relative)
    summary=json.loads((bundle/'summary.json').read_text(encoding='utf-8'))
    if args.part is not None and args.part not in {r['part'] for r in summary['parts']}:
        parser.error('Invalid recovery part')
    inputs=bundle if args.part is None else bundle/f'part_{args.part:02d}'
    # Imports require the crawler's Python dependencies, but do not read dotenv.
    source=Path(__file__).resolve().parents[1]/'N3_Compound_Q3_Overlap_VSCode/src/data/fetch_compound_overlap.py'
    spec=importlib.util.spec_from_file_location('compound_missing_fetch',source)
    fetch=importlib.util.module_from_spec(spec);spec.loader.exec_module(fetch)
    fetch.OUTPUT=bundle/'runs'/('all' if args.part is None else f'part_{args.part:02d}')
    fetch.CACHE=fetch.OUTPUT/'checkpoint.jsonl'
    sys.argv=[str(source),'--aave',str(inputs/'aave_missing_pairs.parquet'),
        '--snapshot-metadata',str(bundle/'snapshot_metadata_AB_frozen.json'),
        '--markets',str(bundle/'markets.csv'),'--max-wallets','0',
        '--batch-size','1','--wallet-chunk','1','--rpc-interval',str(args.rpc_interval)]
    if args.dry_run:sys.argv.append('--dry-run')
    if args.overwrite:sys.argv.append('--overwrite')
    fetch.main()


if __name__=='__main__':main()
