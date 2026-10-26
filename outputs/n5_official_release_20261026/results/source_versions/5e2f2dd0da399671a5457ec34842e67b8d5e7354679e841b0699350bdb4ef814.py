"""Q1/Q3 building blocks. Scenario assumptions are explicit, never fitted silently."""
import math
import numpy as np
import pandas as pd
from scipy.stats import t

from src.models.cascade import debt_value, collateral_value, health_factor
from src.models.n5_integration import track_compound_flows


def student_t_copula_shocks(n, eth_marginal, depeg_marginal, *, rho, df=4, seed=20261025):
    """Seeded t-copula with user-supplied empirical return marginals.

    These are scenario samples, not estimated real-world probabilities unless
    calibration data and parameter estimation have been validated separately.
    """
    if isinstance(n,bool) or not isinstance(n,int) or n<1 or not math.isfinite(df) or df<=2:
        raise ValueError('Need positive integer sample size and df>2')
    if not math.isfinite(rho) or not -1<rho<1:
        raise ValueError('Correlation must be in (-1,1)')
    marginals=[np.asarray(v,dtype=float) for v in (eth_marginal,depeg_marginal)]
    if any(v.ndim!=1 or len(v)<2 or not np.isfinite(v).all() or (v<=-1).any() for v in marginals):
        raise ValueError('Need finite empirical returns >-1')
    rng=np.random.default_rng(seed)
    normal=rng.multivariate_normal([0,0],[[1,rho],[rho,1]],size=n)
    latent=normal/np.sqrt(rng.chisquare(df,size=n)/df)[:,None]
    uniforms=t.cdf(latent,df)
    return pd.DataFrame(dict(scenario_id=[f'mc_{i:05d}' for i in range(n)],
        eth_shock=np.quantile(marginals[0],uniforms[:,0]),
        depeg_shock=np.quantile(marginals[1],uniforms[:,1])))


def expected_shortfall(losses, confidence=.99):
    """Empirical upper-tail mean with fractional boundary mass (no empty tail)."""
    x=np.asarray(losses,dtype=float)
    if x.ndim!=1 or not len(x) or not np.isfinite(x).all() or (x<0).any() or not 0<confidence<1:
        raise ValueError('Invalid losses/confidence')
    tail=(1-confidence)*len(x)
    whole=int(math.floor(tail)); fraction=tail-whole
    ordered=np.sort(x)[::-1]
    return float((ordered[:whole].sum()+(ordered[whole]*fraction if fraction>0 else 0))/tail)


def wallet_loss_rows(result, baseline):
    """Loss scaled to 100k initial equity; BDR uses initial t0 debt explicitly."""
    if baseline.wallet_address.duplicated().any():
        raise ValueError('Baseline must contain one snapshot, one row per wallet')
    refs=baseline.set_index('wallet_address')
    rows=[]
    for w in result.wallets:
        ref=refs.loc[w.wallet]
        loss=max(0.,debt_value(w,result.prices)-collateral_value(w,result.prices))
        equity=float(ref.net_equity_usd); initial_debt=float(ref.debt_usd)
        rows.append(dict(scenario_id=result.summary['scenario_id'],wallet_address=w.wallet,
            is_recursive_loop=bool(ref.is_recursive_loop),equity_bucket=ref.equity_bucket,
            initial_equity_usd=equity,initial_debt_usd=initial_debt,bad_debt_usd=loss,
            bad_debt_ratio_t0=loss/initial_debt if initial_debt>0 else None,
            loss_per_100k_equity=loss/equity*1e5 if equity>0 else None))
    return pd.DataFrame(rows)


def summarize_q1(losses):
    """Per-wallet probabilities / tail loss and common-support equity strata.

    Matching is equal stratum weight over strata containing both groups;
    raw group differences are never presented as a causal estimate.
    """
    keys=['scenario_id','wallet_address']
    if losses.duplicated(keys).any() or losses.empty:
        raise ValueError('Need unique wallet-scenario loss rows')
    if not np.isfinite(losses.bad_debt_usd).all() or losses.bad_debt_usd.lt(0).any():
        raise ValueError('Invalid loss')
    scenario_sets=losses.groupby('wallet_address').scenario_id.agg(frozenset)
    if scenario_sets.nunique()!=1:
        raise ValueError('All wallets must share the same scenarios')
    for name in ['is_recursive_loop','equity_bucket','initial_equity_usd','initial_debt_usd']:
        if losses.groupby('wallet_address')[name].nunique(dropna=False).gt(1).any():
            raise ValueError('Baseline changed across scenarios')
    wallet=[]
    for address,g in losses.groupby('wallet_address'):
        scaled=g.loss_per_100k_equity.dropna()
        wallet.append(dict(wallet_address=address,is_recursive_loop=bool(g.is_recursive_loop.iloc[0]),
            equity_bucket=g.equity_bucket.iloc[0],scenario_count=len(g),
            probability_bad_debt=float(g.bad_debt_usd.gt(0).mean()),
            mean_bad_debt_ratio_t0=float(g.bad_debt_ratio_t0.mean()),
            mean_loss_per_100k=float(scaled.mean()) if len(scaled) else None,
            es99_loss_per_100k=expected_shortfall(scaled) if len(scaled) else None))
    wallets=pd.DataFrame(wallet)
    usable=losses[losses.initial_equity_usd.gt(0)]
    rows=[]
    support=usable.groupby('equity_bucket').is_recursive_loop.nunique()
    common=set(support[support.eq(2)].index)
    for (bucket,group,scenario),g in usable.groupby(['equity_bucket','is_recursive_loop','scenario_id']):
        rows.append(dict(equity_bucket=bucket,is_recursive_loop=bool(group),scenario_id=scenario,
            wallets=g.wallet_address.nunique(),mean_loss_per_100k=float(g.loss_per_100k_equity.mean()),
            probability_bad_debt=float(g.bad_debt_usd.gt(0).mean()),common_support=bucket in common))
    strata=pd.DataFrame(rows)
    comparison=[]
    if common:
        matched=strata[strata.common_support].groupby(['is_recursive_loop','scenario_id']).agg(
            loss=('mean_loss_per_100k','mean'),pbd=('probability_bad_debt','mean')).reset_index()
        for group,g in matched.groupby('is_recursive_loop'):
            comparison.append(dict(is_recursive_loop=bool(group),common_strata=len(common),
                mean_loss_per_100k=float(g.loss.mean()),es99_loss_per_100k=expected_shortfall(g.loss),
                probability_bad_debt=float(g.pbd.mean()),matching='equal_common_equity_strata',
                acceptance='fixture_or_conditional_scenario_only'))
    return wallets,strata,pd.DataFrame(comparison)


def compound_price_stress(exposures, returns):
    """Comet price-only eligibility using its own liquidation collateral factors.

    Does not execute absorb/buyCollateral or infer withdrawals from Aave losses.
    """
    p=exposures.copy()
    multipliers=[]
    for row in p.itertuples():
        key=(row.snapshot_block,row.token_address)
        change=float(returns.get(key,0.))
        if not math.isfinite(change) or change<=-1:
            raise ValueError('Invalid Comet price return')
        multipliers.append(1+change)
    p['stressed_value_usd']=p.value_usd*np.array(multipliers)
    collateral=p.asset_role.eq('collateral')
    if not np.isfinite(p.loc[collateral,'liquidate_collateral_factor']).all():
        raise ValueError('Missing Comet factor')
    p['weighted_collateral_usd']=np.where(collateral,
        p.stressed_value_usd*p.liquidate_collateral_factor,0.)
    p['stressed_debt_usd']=p.stressed_value_usd.where(p.asset_role.eq('base_borrow'),0.)
    out=p.groupby(['snapshot_block','market_id','wallet_address']).agg(
        weighted_collateral_usd=('weighted_collateral_usd','sum'),debt_usd=('stressed_debt_usd','sum')).reset_index()
    out['liquidatable']=out.debt_usd.gt(out.weighted_collateral_usd)
    out['simulation_scope']='Comet_price_only_eligibility_not_executed_liquidation'
    return out


def explicit_base_supply_withdrawal(exposures, wallets, *, fraction, scenario_id):
    """Explicit scenario: withdraw fraction of positive base SUPPLY, not collateral.

    No oracle/solvency rule for withdrawing collateral is invented. Returned
    outflow is not automatically an Aave repayment or liquidator transaction.
    """
    if not scenario_id or not math.isfinite(fraction) or not 0<=fraction<=1:
        raise ValueError('Need named scenario and fraction in [0,1]')
    keys=['snapshot_block','market_id','wallet_address','token_address','asset_role']
    events=[]
    selected=exposures[exposures.wallet_address.isin(wallets) & exposures.asset_role.eq('base_supply')]
    for i,(_,r) in enumerate(selected.iterrows()):
        events.append(dict(**{k:r[k] for k in keys},event_id=f'{scenario_id}_{i}',
            quantity_delta=-float(r.amount_token)*fraction,reason='explicit_base_supply_withdrawal_scenario',source_run_id=scenario_id))
    return track_compound_flows(exposures,events)


def cascade_transitions(result, baseline, assets, *, healthy_threshold=1.10):
    if not math.isfinite(healthy_threshold) or healthy_threshold<1:
        raise ValueError('Invalid healthy threshold')
    initial=baseline.set_index('wallet_address')
    first={}
    for tx in result.transactions:
        first.setdefault(tx['wallet'],tx['round'])
    rows=[]
    for w in result.wallets:
        before=float(initial.loc[w.wallet,'health_factor'])
        after=health_factor(w,assets,result.prices)
        rows.append(dict(wallet_address=w.wallet,initial_hf=before,final_hf=after,
            initially_healthy=before>=healthy_threshold,final_liquidatable=after<1,
            first_executed_round=first.get(w.wallet),executed= w.wallet in first,
            initially_healthy_sequential_execution=before>=healthy_threshold and first.get(w.wallet,0)>=2,
            healthy_threshold=healthy_threshold))
    return pd.DataFrame(rows)


def debt_rank(weights, capacities, initial_distress):
    """Single-activation DebtRank: weights[source,target] transmit distress.

    Capacities and edge orientation must be supplied explicitly. Returns
    propagated distress; no empirical ranking is inferred from exposure degree.
    """
    w=np.asarray(weights,dtype=float); c=np.asarray(capacities,dtype=float); h=np.asarray(initial_distress,dtype=float).copy()
    n=len(c)
    if w.shape!=(n,n) or h.shape!=(n,) or not np.isfinite(w).all() or not np.isfinite(c).all() or not np.isfinite(h).all():
        raise ValueError('Invalid network shapes/values')
    if (w<0).any() or (c<=0).any() or (h<0).any() or (h>1).any():
        raise ValueError('Invalid exposure, capacity or distress')
    impact=np.minimum(1,w/c[None,:]); active=h>0; inactive=np.zeros(n,dtype=bool)
    initial=h.copy()
    for _ in range(n):
        if not active.any(): break
        new=np.minimum(1,h+np.sum(impact[active]*h[active,None],axis=0))
        inactive |= active
        active=(new>0)&~inactive
        h=new
    return dict(final_distress=h.tolist(),incremental_distress=(h-initial).tolist(),
        unweighted_incremental_score=float((h-initial).sum()),capacity_weighted_incremental_score=float(np.dot(h-initial,c)/c.sum()))
