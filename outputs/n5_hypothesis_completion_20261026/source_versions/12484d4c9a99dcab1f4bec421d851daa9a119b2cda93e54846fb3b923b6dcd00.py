"""Chronological B1->B2 predictive checks for every approved DEX route."""
import json
from pathlib import Path
import hashlib
import shutil
import numpy as np
import pandas as pd
import statsmodels.api as sm


def main():
    root=Path('outputs/n5_hypothesis_completion_20261026')
    folder=Path('outputs/n5_hypothesis_dex_inputs_20261026')
    spot=pd.read_parquet(folder/'dex_three_route_spots.parquet')
    oracles={sid:pd.read_parquet(f'outputs/n5_completion_20261026/history/{sid}/oracle_all_active_tokens.parquet') for sid in ['B1','B2']}
    reports,all_rows=[],[]
    for (pool,ct,dt), route in spot.groupby(['pool_address','token_in_address','token_out_address']):
        parts=[]
        symbols={}
        for sid,g in route.groupby('snapshot_id'):
            o=oracles[sid]
            native=o[o.token_address.isin([ct,dt])].pivot(index='block',columns='token_address',values='price_usd')
            symbols.update(o[o.token_address.isin([ct,dt])].drop_duplicates('token_address').set_index('token_address').symbol.to_dict())
            q=g.sort_values('block').join(native,on='block',validate='one_to_one')
            if q[[ct,dt]].isna().any().any() or not q.block.diff().dropna().eq(1).all():
                raise ValueError('Unaligned route/oracle blocks')
            q['dex_return']=np.log(q.spot_output_per_input).diff()
            q['oracle_ratio_return']=np.log(q[ct]/q[dt]).diff()
            q['out_oracle_return']=np.log(q[dt]).diff()
            q['dex_lag1']=q.dex_return.shift(1)
            q['dex_lag2']=q.dex_return.shift(2)
            q['oracle_lag1']=q.oracle_ratio_return.shift(1)
            q['target_next_ratio_return']=q.oracle_ratio_return.shift(-1)
            q['target_block']=q.block+1
            q['source_price_resolution_floor']=2e-8*(1/q[ct]+1/q[dt])
            parts.append(q.dropna())
        data=pd.concat(parts,ignore_index=True)
        train,test=data[data.snapshot_id.eq('B1')],data[data.snapshot_id.eq('B2')]
        if train.block_timestamp_utc.max()>=test.block_timestamp_utc.min():raise ValueError('Temporal overlap')
        columns=['dex_return','dex_lag1','dex_lag2','oracle_lag1','out_oracle_return']
        fit=sm.OLS(train.target_next_ratio_return,sm.add_constant(train[columns],has_constant='add')).fit(cov_type='HAC',cov_kwds={'maxlags':10})
        contrast=np.array([0,1,1,1,0,0.])
        beta=float(contrast@fit.params.to_numpy())
        se=float(np.sqrt(max(0.,contrast@fit.cov_params().to_numpy()@contrast)))
        ci=[beta-1.96*se,beta+1.96*se]
        prediction=fit.predict(sm.add_constant(test[columns],has_constant='add'))
        rmse=float(np.sqrt(np.mean((test.target_next_ratio_return-prediction)**2)))
        zero=float(np.sqrt(np.mean(test.target_next_ratio_return**2)))
        variation=float(train.target_next_ratio_return.std())
        floor=float(train.source_price_resolution_floor.max())
        measurable=variation>floor
        supported=measurable and ci[0]>0 and rmse<zero
        point=float(np.clip(beta,0,1)) if supported else 0.
        report=dict(pool_address=pool,token_in_address=ct,token_out_address=dt,
            route=f'{symbols[ct]}->{symbols[dt]}',training_rows=len(train),held_out_rows=len(test),
            train='B1',test='B2',feature_before_target=bool((data.block<data.target_block).all()),
            summed_DEX_coefficient=beta,hac_ci95=ci,held_out_rmse=rmse,zero_change_rmse=zero,
            target_std=variation,assumed_USD_quote_resolution_floor=floor,
            target_variation_above_resolution=bool(measurable),
            positive_predictive_transmission_supported=bool(supported),calibrated_predictive_proxy=point,
            causal_identification='not_established;_relative_pair_predictability_not_randomized_liquidation_impact')
        reports.append(report)
        all_rows.append(data.drop(columns=[ct,dt]))
        print(report['route'],'proxy',point,'measurable',measurable,'heldout improvement',rmse<zero,flush=True)
    (root/'feedback/route_validation.json').write_text(json.dumps(reports,indent=2),encoding='utf-8')
    pd.concat(all_rows,ignore_index=True).to_parquet(root/'feedback/three_route_temporal_observations.parquet',index=False)
    inputs=[p for p in folder.iterdir() if p.is_file()]
    frozen=[dict(path=str(p.resolve()),sha256=hashlib.sha256(p.read_bytes()).hexdigest()) for p in inputs]
    (root/'feedback_input_freeze.json').write_text(json.dumps(frozen,indent=2),encoding='utf-8')
    for path in [Path(__file__),Path('scripts/n5_collect_dex_feedback.py')]:
        sha=hashlib.sha256(path.read_bytes()).hexdigest()
        destination=root/'source_versions'/(sha+'.py')
        if not destination.exists():shutil.copyfile(path,destination)
    (root/'feedback/three_route_method.json').write_text(json.dumps(dict(
        estimator='lagged_relative_pair_log_return_OLS_HAC10',protocol='B1_train_B2_heldout',
        rule='positive_lower_CI_and_better_heldout_RMSE_and_variation_above_quote_resolution',
        note='all three selected routes tested; no claim of causal identification'),indent=2),encoding='utf-8')


if __name__=='__main__':main()
