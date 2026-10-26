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


def test_empirical_q1_q3_bundle_manifest_and_kpis():
    from pathlib import Path
    import json
    from src.models.n5_readiness import verify_manifest
    from src.models.n5_kpis import validate_kpi_artifacts

    out_dir = Path("outputs/n5_q1_q3_empirical_20261026")
    assert out_dir.exists()
    assert verify_manifest(out_dir) == []
    summary = json.loads((out_dir / "summary.json").read_text(encoding="utf-8"))
    assert summary["status"] == "empirical_q1_q3_completed_on_approved_cohort"
    assert summary["cohort_wallets"] == 50311
    assert summary["q1_mc_scenarios"] == 10000
    assert summary["q1_stress_scenarios"] == 19
    assert summary["q2_cf_counterfactual_runs"] == 228
    assert len(summary["q2_cf_policy_aggregate"]) == 12
    assert summary["kpi_checks_passed"] == 18
    assert summary["kpi_checks_total"] == 18
    mapping = pd.read_csv(out_dir / "kpi_mapping.csv", encoding="utf-8-sig")
    checks = validate_kpi_artifacts(mapping, out_dir)
    assert all(r["artifact_check"] == "passed_mapping_only" and not r["errors"] for r in checks)

