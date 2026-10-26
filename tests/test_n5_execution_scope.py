import json
from pathlib import Path

import pandas as pd
import pytest

from src.models.n5_execution_scope import scope_points, planned_quote_inventory
from src.models.n5_impact_review import simulate_execution
from src.models.cascade import Asset, Wallet


def test_selected_scope_has_all_nine_directions_and_preserves_actual_bounds():
    scope = json.loads(Path('config/n5_execution_scope.json').read_text())
    rows = []
    for sid, block in scope['snapshots'].items():
        for route in scope['routes']:
            for amount in [1000., 5000.]:
                rows.append(dict(route, snapshot_id=sid, snapshot_block=block, trade_size_usd=amount, impact_pct=.1))
    points = pd.DataFrame(rows)
    snaps = {'snapshots': {sid: {'block': block} for sid, block in scope['snapshots'].items()}}
    selected = scope_points(points, scope, snaps)
    assert len(selected) == 18 and selected.trade_size_usd.min() == 1000
    snaps['snapshots']['B1']['block'] += 1
    with pytest.raises(ValueError, match='identity'):
        scope_points(points, scope, snaps)


def test_planned_amounts_do_not_extend_observed_domain_or_mark_received():
    requests = pd.read_csv('outputs/n5_preparation/n3_quote_handoff_20261026/n3_quote_first_batch_27.csv')
    columns = ['snapshot_id', 'snapshot_block', 'pool_address', 'token_in_address', 'token_out_address']
    quotes = requests[columns].copy()
    quotes['trade_size_usd'] = 1000.
    quotes['status'] = 'RPC_VERIFIED'
    inventory = planned_quote_inventory(requests, quotes)
    assert inventory.receipt_status.eq('pending_collection').all()
    quotes = requests[columns].copy()
    quotes['trade_size_usd'] = requests.suggested_size_snapshot_usd
    quotes['status'] = 'RPC_VERIFIED'
    assert planned_quote_inventory(requests, quotes).receipt_status.eq('received_validated').all()
    quotes.loc[0, 'snapshot_block'] += 1
    assert planned_quote_inventory(requests, quotes).receipt_status.eq('pending_collection').sum() == 1


def test_outside_scope_is_reported_without_executing_or_claiming_freeze():
    assets = {'c': Asset(1, .8, .1), 'd': Asset(1)}
    wallets = [Wallet('one', {'c': 600}, {'d': 550})]
    s, _, txs, attempts = simulate_execution(wallets, assets, {'c': 1, 'd': 1}, {}, method='linear', allowed_routes=set())
    assert not txs and attempts[0]['status'] == 'outside_selected_scope'
    assert not s['freeze_proven']
