import pandas as pd
import pytest

from test_cliff_reverse import _toy_positions
import test_n5_n3_update as quote_fixtures
from src.models.n5_n3_update import calibrate_quote_grid
from src.models.stress_test import _asset_multiplier,run_stress_test,generate_stress_heatmap
from src.models.cliff_reverse import find_liquidation_cliff,run_reverse_stress


def test_quantization_requires_explicit_policy_preserves_raw_and_rejects_large_negative():
    q,a,d,c=quote_fixtures.N3UpdateTests().quote_fixture()
    amount=int(q.loc[0,'amount_in_raw'])
    q.loc[0,'amount_out_raw']=str(amount+1)
    q.loc[0,'impact_pct']=100*(1-(amount+1)/amount)
    original=q.copy(deep=True)
    with pytest.raises(ValueError):calibrate_quote_grid(q,a,d,c)
    _,res,qa=calibrate_quote_grid(q,a,d,c,quantization_policy='conservative_raw_unit_bound')
    assert len(qa['quantization_adjustments'])==1
    bound=res[res.point_status.str.startswith('conservative')].iloc[0]
    assert bound.raw_observed_impact_pct<0<bound.observed_impact_pct
    pd.testing.assert_frame_equal(q,original)
    q.loc[0,'amount_out_raw']=str(2*amount);q.loc[0,'impact_pct']=-100.
    with pytest.raises(ValueError,match='exceeds'):
        calibrate_quote_grid(q,a,d,c,quantization_policy='conservative_raw_unit_bound')


def test_explicit_depeg_scope_keeps_eth_exposure_and_token_override():
    params=dict(eth_shock=-.2,depeg_shock=-.1,depeg_assets=['STETH','WSTETH'])
    assert _asset_multiplier('WSTETH',params)==pytest.approx(.72)
    assert _asset_multiplier('WEETH',params)==pytest.approx(.8)
    assert _asset_multiplier('WEETH',dict(params,asset_shocks={'WEETH':-.2}))==pytest.approx(.64)


def test_all_public_research_apis_share_dust_cohort_and_explicit_denominator():
    base=_toy_positions()
    dust=base.assign(wallet_address='dust',debt_amount=[0.,50.])
    p=pd.concat([base,dust],ignore_index=True)
    refs=pd.DataFrame([dict(snapshot_block=1,protocol_id='aave_v3_ethereum',wallet_address='0xabc',total_debt_base_usd=50000.),
        dict(snapshot_block=1,protocol_id='aave_v3_ethereum',wallet_address='dust',total_debt_base_usd=99.9)])
    policy=dict(min_simulation_debt_usd=100,dust_basis='aave_reference_initial_debt')
    kwargs=dict(tolerances=policy,account_reference=refs)
    stress=run_stress_test(p,dict(eth_shock=-.6),**kwargs)
    assert set(stress.wallet)=={'0xabc'}
    heat=generate_stress_heatmap(p,[-.6],[0.],**kwargs)
    assert heat.positions_at_risk_count.item()==1
    cliff=find_liquidation_cliff(p,max_drop=0.,**kwargs)
    assert cliff.baseline_cohort_debt_usd.item()==50000.
    reverse=run_reverse_stress(p,['10%'],**kwargs)['snapshots']['1']
    assert reverse['baseline_cohort_debt_usd']==50000.
    assert reverse['targets'][0]['target_debt_usd']==5000.


def test_official_q1_q3_release_manifest_and_scientific_scope():
    from pathlib import Path
    import json
    from src.models.n5_readiness import verify_manifest
    from src.models.n5_release import verify_release, scientific_checks

    # The old bundle/18 mapping assertions describe an intermediate 228-run
    # experiment. Publication now verifies the selected frozen results AND
    # their local inputs; software success does not accept all hypotheses.
    pointer=json.loads(Path('config/n5_research_artifacts.json').read_text(encoding='utf-8'))
    out_dir=Path(pointer['current_bundle'])
    assert out_dir.exists()
    assert verify_manifest(out_dir) == []
    assert verify_release(out_dir)==[]
    checks=scientific_checks(out_dir)
    assert checks['mc_scenarios']==120000
    assert checks['cf_grid_cases']==1458
    assert sum(s['wallet_snapshot_rows'] for s in checks['snapshot_records'])==50311
    assert next(s['block'] for s in checks['snapshot_records'] if s['snapshot']=='B1')==20458637
    assert checks['h3_threshold']==1.10
    mapping=pd.read_csv(out_dir/'metadata/kpi_release_dictionary.csv')
    assert set(mapping.kpi_id)=={f'M{i:02}' for i in range(1,19)}
    assert 'not_original' in mapping.set_index('kpi_id').loc['M17','definition_relation']
    assert 'not_18_of_18' in mapping.research_acceptance.iloc[0]

