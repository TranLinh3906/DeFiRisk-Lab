"""Read-only N4 review; regenerated HF writes only to a new N5 review folder."""
import argparse
import contextlib
import io
import json
from pathlib import Path

import pandas as pd

from scripts.n5_audit import finite_json, stable_read
from src.models import health_factor as hf, stress_test as stress, cliff_reverse as cliff
from src.models.n5_inputs import prepare_portfolio
from src.models.n5_readiness import diagnose_n4
from src.models.n5_validation import check_schema


def review(output):
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    # Run the exact N4 HF pipeline but redirect its destinations. No canonical writes.
    original_marts = hf.MARTS
    try:
        hf.MARTS = output / 'n4_hf_regenerated_candidate'
        with contextlib.redirect_stdout(io.StringIO()):
            hf.main()
    finally:
        hf.MARTS = original_marts
    regenerated = pd.read_parquet(output / 'n4_hf_regenerated_candidate/borrower_risk.parquet')
    current = pd.read_parquet('data/marts/borrower_risk.parquet')
    base = Path('data/processed/aave')
    raw = {k: stable_read(base / f'{k}.parquet') for k in
           ['positions_long', 'reserve_config', 'oracle_prices', 'emode_membership']}
    pos = pd.DataFrame(raw['positions_long'])
    metadata = json.loads((base / 'snapshot_metadata.json').read_text(encoding='utf-8'))
    result = {'snapshots': {}, 'scope': 'N4 review; unvalidated; no N4 source/mart changes'}
    for label, snapshot in metadata['snapshots'].items():
        block = snapshot['snapshot_block']
        portfolio = prepare_portfolio(raw['positions_long'], raw['reserve_config'], raw['oracle_prices'],
            snapshot_block=block, emode_membership=raw['emode_membership'],
            depth_usd={r['token_address']: 1_000_000 for r in raw['reserve_config']})
        snapshot_result = {}
        for name, risk in [('current_mart', current), ('regenerated_current_code', regenerated)]:
            diagnostic = diagnose_n4(portfolio, risk, pos)
            diagnostic.to_parquet(output / f'{label}_{name}_comparison.parquet', index=False)
            bad = diagnostic[diagnostic.eligibility_disagreement]
            bad.to_csv(output / f'{label}_{name}_disagreements.csv', index=False, encoding='utf-8-sig')
            snapshot_result[name] = dict(compared_wallets=len(diagnostic),
                eligibility_disagreements=len(bad), accounts_hf_error_ge_005=int(diagnostic.hf_abs_error.ge(.005).sum()),
                disagreement_debt_usd=float(bad.adapter_debt_usd.sum()))
        result['snapshots'][label] = snapshot_result
    membership = pd.DataFrame(raw['emode_membership'])
    categories = membership.groupby(['snapshot_block', 'token_address']).emode_category.nunique()
    result['membership_token_keys_with_multiple_categories'] = int(categories.gt(1).sum())
    positions, _ = stress._load_model_inputs()
    zero = stress.run_stress_test(positions, {'scenario_id': 'n5_zero_shock_review'})
    mismatch = zero[(zero.hf_before - zero.hf_after).abs().ge(.005)]
    mismatch.to_csv(output / 'zero_shock_hf_mismatch.csv', index=False, encoding='utf-8-sig')
    result['zero_shock'] = dict(wallet_snapshots=len(zero), hf_error_ge_005=len(mismatch),
        eligibility_disagreements=int((zero.hf_before.lt(1) != zero.hf_after.lt(1)).sum()))
    tiny = stress.run_stress_test(positions, {'scenario_id': 'n5_tiny_shock_review', 'eth_shock': -1e-12})
    result['tiny_shock'] = dict(hf_error_ge_005=int((tiny.hf_before-tiny.hf_after).abs().ge(.005).sum()),
        eligibility_disagreements=int((tiny.hf_before.lt(1) != tiny.hf_after.lt(1)).sum()))
    # A two-step preview checks snapshot identity without the full scan.
    curve = cliff.find_liquidation_cliff(positions, max_drop=.01, step=.01)
    curve.to_csv(output / 'mixed_snapshot_cliff_probe.csv', index=False, encoding='utf-8-sig')
    result['cliff_probe'] = dict(input_blocks=sorted(positions.snapshot_block.unique().tolist()),
        output_columns=curve.columns.tolist(),
        baseline_rows=curve[curve.eth_drop_pct.eq(0)].to_dict('records'))
    result['n5_schema_compatibility'] = {}
    for name in ['stress_summary', 'stress_heatmap']:
        frame = pd.read_parquet(Path('data/marts') / f'{name}.parquet')
        errors = check_schema(frame.to_dict('records'), name)
        result['n5_schema_compatibility'][name] = dict(rows=len(frame), errors=errors[:4], error_count=len(errors))

    row = dict(snapshot_block=100, protocol_id='aave_v3_ethereum', wallet_address='w',
               token_address='t', symbol='WETH', collateral_amount=100., debt_amount=50.,
               price_usd=1., effective_lt=.8, is_collateral_enabled='false')
    flagged = stress.run_stress_test(pd.DataFrame([row]), {})
    missing = dict(row, is_collateral_enabled=True)
    missing.pop('price_usd')
    def probe(call):
        try:
            return dict(status='accepted', value=call())
        except (ValueError, KeyError, TypeError) as exc:
            return dict(status='rejected', error=str(exc))
    erased = probe(lambda: stress.run_stress_test(pd.DataFrame([missing]), {}).to_dict('records'))
    two = pd.DataFrame([dict(row, is_collateral_enabled=True)])
    cfg = pd.DataFrame([dict(snapshot_block=100, protocol_id='aave_v3_ethereum', token_address='t', lt_std=.8)])
    px = pd.DataFrame([dict(snapshot_block=100, protocol_id='aave_v3_ethereum', token_address='t', price_usd=2.)])
    result['input_probes'] = dict(false_string_hf_after=float(flagged.iloc[0].hf_after),
        missing_price=erased,
        hf_embedded_vs_oracle_collateral_usd=float(hf.calculate_health_factor(two, cfg, px).iloc[0].total_collateral_usd),
        hf_expected_collateral_at_explicit_oracle_usd=200.,
        negative_dict_target=probe(lambda: cliff._parse_target({'usd': -1}, 100)),
        negative_percent_target=probe(lambda: cliff._parse_target('-10%', 100)),
        nan_dict_target=probe(lambda: cliff._parse_target({'usd': float('nan')}, 100)),
        infinite_percent_target=probe(lambda: cliff._parse_target({'pct': float('inf')}, 100)),
        n4_weeth_depeg_multiplier=stress._asset_multiplier('WEETH', {'depeg_shock': -.05}))
    (output / 'review.json').write_text(json.dumps(finite_json(result), ensure_ascii=False,
                                                  indent=2, allow_nan=False), encoding='utf-8')
    print(json.dumps(finite_json(result), ensure_ascii=False, indent=2, allow_nan=False))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir', type=Path, required=True)
    review(parser.parse_args().output_dir)
