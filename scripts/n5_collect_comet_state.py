"""Read-only historical Comet reserves/pause state. Never submits transactions."""
import argparse
import json
from pathlib import Path
from eth_abi import decode
import pandas as pd

from scripts.n234_collect_history import CachedRPC, calldata


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--output-dir', type=Path, default=Path('outputs/research_review_fix_20261026/comet_state'))
    args = ap.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    try:
        rpc = CachedRPC(args.output_dir)
    except Exception as exc:
        print('COMET_STATE_UNAVAILABLE_' + type(exc).__name__, flush=True)
        raise SystemExit(1) from None
    config = pd.read_parquet('outputs/compound_consolidated_A_B1_B2_20261025_complete/n3_compound_reserve_config.parquet')
    rows = []
    for (sid, block, market, address), group in config.groupby(['snapshot_id', 'snapshot_block', 'market_id', 'comet_address']):
        specs = [('baseToken()', 'address'), ('baseScale()', 'uint256'), ('getReserves()', 'int256'),
                 ('targetReserves()', 'uint256'), ('storeFrontPriceFactor()', 'uint256'),
                 ('isAbsorbPaused()', 'bool'), ('isBuyPaused()', 'bool')]
        calls = [('eth_call', [{'to': address, 'data': calldata(sig)}, hex(int(block))]) for sig, _ in specs]
        responses = rpc.batch(calls)
        header = rpc.one('eth_getBlockByNumber', [hex(int(block)), False])
        row = dict(snapshot_id=sid, snapshot_block=int(block), market_id=market,
                   comet_address=address, block_hash=header['hash'])
        for (sig, dtype), raw in zip(specs, responses):
            if not isinstance(raw, str):
                raise RuntimeError('Comet state getter reverted: ' + sig)
            row[sig[:-2]] = decode([dtype], bytes.fromhex(raw[2:]))[0]
        row['collateral_reserves_raw'] = {}
        for t in group.loc[group.role.eq('collateral_asset'), 'token_address']:
            raw = rpc.one('eth_call', [{'to': address,
                'data': calldata('getCollateralReserves(address)', ['address'], [t])}, hex(int(block))])
            row['collateral_reserves_raw'][t] = str(decode(['uint256'], bytes.fromhex(raw[2:]))[0])
        # Retain integer values without JSON floating-point precision loss.
        for key in ['baseScale', 'getReserves', 'targetReserves', 'storeFrontPriceFactor']:
            row[key] = str(row[key])
        rows.append(row)
        print(f'{sid} {market}: state observed at {block}', flush=True)
    (args.output_dir / 'comet_state.json').write_text(json.dumps(rows, indent=2), encoding='utf-8')


if __name__ == '__main__':
    main()
