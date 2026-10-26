"""Explicit bounded execution scope and receipt inventory for planned quotes."""
import numpy as np
import pandas as pd

from src.models.n5_impact_review import KEYS


def scope_points(points, scope, snapshots):
    if {k: v['block'] for k, v in snapshots['snapshots'].items()} != scope['snapshots']:
        raise ValueError('Scope snapshot identity differs from config')
    if scope['domain_policy'] != 'validated_observation_bounds_only' or scope['outside_domain_policy'] != 'unknown_do_not_split_or_extrapolate' or scope['oracle_feedback'] is not False:
        raise ValueError('Unsupported scope policy')
    if scope['primary_method'] != 'linear' or scope['sensitivity_method'] != 'power' or scope['pool_state'] not in ['cumulative_within_round_reset_between_rounds_assumed', 'cumulative_across_rounds'] or scope['same_token_repayment'] != 'identity_no_dex_required':
        raise ValueError('Unsupported execution assumption')
    routes = pd.DataFrame(scope['routes'])
    keys = ['token_in_address', 'token_out_address', 'pool_address']
    if routes.duplicated(keys[:2]).any():
        raise ValueError('Ambiguous scope route')
    selected = points.merge(routes[keys], on=keys, how='inner', validate='many_to_one')
    if len(selected[KEYS].drop_duplicates()) != len(routes)*len(scope['snapshots']):
        raise ValueError('Missing observed scope direction')
    return selected


def planned_quote_inventory(requests, quotes):
    """A request is fulfilled only by same-direction/same-block verified quote.

    Call after calibrate_quote_grid validates raw quote arithmetic and hashes.
    Never use suggested request values as observed quote values.
    """
    if len(requests) != 27 or requests.duplicated(KEYS+['suggested_size_snapshot_usd']).any():
        raise ValueError('Expected 27 unambiguous planned quotes')
    rows = []
    for row in requests.to_dict('records'):
        match = quotes
        for key in KEYS:
            match = match[match[key].eq(row[key])]
        amount = float(row['suggested_size_snapshot_usd'])
        match = match[np.isclose(match.trade_size_usd, amount, rtol=1e-7, atol=1e-8) & match.status.eq('RPC_VERIFIED')]
        rows.append(dict(row, receipt_status='received_validated' if len(match) else 'pending_collection',
                         matching_observations=len(match)))
    return pd.DataFrame(rows)
