"""Shared N4/N5 research gates. Thresholds never suppress failed assertions."""
import json
from pathlib import Path

import numpy as np
import pandas as pd

KEYS=['snapshot_block','protocol_id','wallet_address']
TOLERANCE_PATH=Path(__file__).resolve().parents[2]/'config/n5_tolerances.json'


def load_tolerances(path=TOLERANCE_PATH):
    policy=json.loads(Path(path).read_text(encoding='utf-8'))
    for field in ['min_simulation_debt_usd','wallet_balance_max_absolute_error_usd',
                  'protocol_debt_max_relative_error','hf_max_median_absolute_error']:
        value=policy[field]
        if isinstance(value,bool) or not np.isfinite(value) or value<0:
            raise ValueError(f'Invalid tolerance: {field}')
    if policy['dust_basis']!='aave_reference_initial_debt':
        raise ValueError('Unsupported dust basis')
    return policy


def _keys(frame):
    out=frame.rename(columns={'wallet':'wallet_address'}).copy() if 'wallet_address' not in frame else frame.copy()
    if 'protocol_id' not in out:out['protocol_id']='aave_v3_ethereum'
    if out[KEYS].isna().any().any():raise ValueError('Missing wallet identity')
    out['wallet_address']=out.wallet_address.str.lower()
    return out


def filter_simulation_positions(positions,references,policy=None):
    """Exclude entire wallets using initial Aave debt; never filter token rows.

    Identity includes snapshot and protocol, so a wallet can be retained at one
    snapshot and excluded at another. Unknown references are errors, never dust.
    """
    policy=policy or load_tolerances()
    p,r=_keys(positions),_keys(references)
    if r.duplicated(KEYS).any():raise ValueError('Duplicate account reference')
    debt=pd.to_numeric(r.total_debt_base_usd,errors='coerce')
    if not np.isfinite(debt).all() or debt.lt(0).any():raise ValueError('Invalid reference debt')
    r['initial_reference_debt_usd']=debt
    identities=p[KEYS].drop_duplicates().merge(r[KEYS+['initial_reference_debt_usd']],on=KEYS,
        how='left',validate='one_to_one')
    if identities.initial_reference_debt_usd.isna().any():raise ValueError('Missing account reference; cannot apply dust filter')
    identities['included_in_simulation']=identities.initial_reference_debt_usd.ge(policy['min_simulation_debt_usd'])
    keep=identities.loc[identities.included_in_simulation,KEYS]
    return p.merge(keep,on=KEYS,how='inner',validate='many_to_one'),identities


def evaluate_research_gate(positions,references,protocol_totals,policy=None,n4_risk=None):
    """Protocol debt: full t0 scope. Wallet/HF: retained debt>=100 cohort.

    Median absolute HF error is the research-design statistic, not mean error
    and not an assertion that every individual HF error is below 0.005.
    """
    policy=policy or load_tolerances()
    p,r=_keys(positions),_keys(references)
    if p.duplicated(KEYS+['token_address']).any():raise ValueError('Duplicate position key')
    if p.snapshot_block.nunique()!=1 or p.protocol_id.nunique()!=1:
        raise ValueError('Gate requires exactly one snapshot and protocol')
    for field in ['collateral_amount','debt_amount','price_usd','effective_lt']:
        if not np.isfinite(p[field]).all() or p[field].lt(0).any():raise ValueError(f'Invalid {field}')
    if p.price_usd.le(0).any() or p.effective_lt.gt(1).any():raise ValueError('Invalid price or LT')
    if not p.is_collateral_enabled.map(lambda x:isinstance(x,(bool,np.bool_))).all():
        raise ValueError('Collateral flags must be boolean')
    selected,cohort=filter_simulation_positions(p,r,policy)
    def aggregate(frame):
        d=frame.copy()
        d['debt_usd']=d.debt_amount*d.price_usd
        d['collateral_usd']=d.collateral_amount*d.price_usd*d.is_collateral_enabled
        d['weighted']=d.collateral_usd*d.effective_lt
        return d.groupby(KEYS)[['debt_usd','collateral_usd','weighted']].sum().reset_index()
    full=aggregate(p);balances=aggregate(selected)
    balances=balances.merge(r[KEYS+['total_debt_base_usd','total_collateral_base_usd','health_factor_reported']],
        on=KEYS,how='left',validate='one_to_one')
    for field in ['total_debt_base_usd','total_collateral_base_usd','health_factor_reported']:
        if not np.isfinite(balances[field]).all() or balances[field].lt(0).any():raise ValueError('Invalid account reference')
    if balances.debt_usd.le(0).any():raise ValueError('Retained wallet has no calculated debt')
    for kind in ['debt','collateral']:
        balances[f'{kind}_absolute_error_usd']=(balances[f'{kind}_usd']-balances[f'total_{kind}_base_usd']).abs()
        balances[f'{kind}_passed']=balances[f'{kind}_absolute_error_usd'].le(policy['wallet_balance_max_absolute_error_usd'])
    balances['calculated_hf']=balances.weighted/balances.debt_usd
    balances['hf_absolute_error']=(balances.calculated_hf-balances.health_factor_reported).abs()
    expected=float(protocol_totals['protocol_debt_usd']);actual=float(full.debt_usd.sum())
    block=int(p.snapshot_block.iloc[0]);protocol=p.protocol_id.iloc[0]
    if protocol_totals['snapshot_block']!=block or not np.isfinite(expected) or expected<=0:
        raise ValueError('Missing/nonpositive or wrong-block protocol debt evidence')
    delta=abs(actual-expected)/expected
    median=float(balances.hf_absolute_error.median()) if len(balances) else None
    gates=dict(protocol_debt=dict(status='passed' if delta<policy['protocol_debt_max_relative_error'] else 'failed',
        relative_error=delta,absolute_error_usd=abs(actual-expected),calculated_full_t0_debt_usd=actual,
        protocol_debt_usd=expected,scope='full pre-dust initial borrower universe'),
        wallet_balances=dict(status='passed' if len(balances) and balances.debt_passed.all() and balances.collateral_passed.all() else 'failed',
            failed_debt_wallets=int((~balances.debt_passed).sum()),failed_collateral_wallets=int((~balances.collateral_passed).sum()),
            max_debt_absolute_error_usd=float(balances.debt_absolute_error_usd.max()) if len(balances) else None,
            max_collateral_absolute_error_usd=float(balances.collateral_absolute_error_usd.max()) if len(balances) else None),
        hf_median=dict(status='passed' if median is not None and median<policy['hf_max_median_absolute_error'] else 'failed',
            median_absolute_error=median,individual_wallets_ge_005=int(balances.hf_absolute_error.ge(.005).sum())))
    if n4_risk is not None:
        risk=_keys(n4_risk)
        if risk.duplicated(KEYS).any():raise ValueError('Duplicate N4 risk key')
        balances=balances.merge(risk[KEYS+['health_factor']].rename(columns={'health_factor':'n4_hf'}),
            on=KEYS,how='left',validate='one_to_one')
        valid=balances.n4_hf.map(lambda x:pd.notna(x) and np.isfinite(x) and x>=0)
        balances['n4_hf_absolute_error']=(balances.n4_hf-balances.health_factor_reported).abs()
        n4median=float(balances.n4_hf_absolute_error.median()) if len(balances) else None
        gates['n4_hf_median']=dict(status='passed' if len(balances) and valid.all() and n4median<policy['hf_max_median_absolute_error'] else 'failed',
            median_absolute_error=n4median,invalid_or_missing_wallets=int((~valid).sum()))
    excluded=cohort[~cohort.included_in_simulation]
    report=dict(status='passed' if all(g['status']=='passed' for g in gates.values()) else 'failed',
        snapshot_block=block,protocol_id=protocol,policy=policy,gates=gates,
        included_wallets=len(balances),excluded_dust_wallets=len(excluded),
        excluded_reference_debt_usd=float(excluded.initial_reference_debt_usd.sum()),
        full_research_acceptance='pending_other_liquidity_holdout_and_execution_gates')
    return selected,cohort,balances,report
