"""Regression guards: new N4 schemas, multiple memberships and partial tracking."""
import math
import unittest
import pandas as pd

from src.models.n5_validation import check_schema
from src.models.n5_inputs import prepare_portfolio
from src.models.n5_integration import (guard_stress_positions, validate_zero_shock,
    parse_reverse_target, adapt_compound_partial, track_compound_flows, attribute_hf_differences)
from src.models.n5_kpis import baseline_wallet_kpis, graph_conservation_report
from src.models.n5_readiness import build_aave_graph


def position():
    return dict(snapshot_block=100, protocol_id='aave_v3_ethereum', wallet_address='w',
        token_address='t', symbol='WETH', collateral_amount=100., debt_amount=50.,
        price_usd=1., effective_lt=.8, is_collateral_enabled=True)


def prepared():
    row = dict(position(), user_emode=2)
    row.pop('effective_lt')  # Adapter reconstructs this from the selected membership.
    cfg = [dict(snapshot_block=100, token_address='t', symbol='WETH', lt_std=.5,
                liq_bonus_std_premium=.05)]
    prices = [dict(snapshot_block=100, token_address='t', price_usd=1.)]
    members = [dict(snapshot_block=100, token_address='t', emode_category=cat,
        is_collateral=enabled, emode_lt_bps=lt, emode_bonus_premium=.02)
        for cat, enabled, lt in [(1, True, 9500), (2, True, 9000), (3, False, 9500)]]
    return row, cfg, prices, members


class SchemaTests(unittest.TestCase):
    def test_summary_snapshot_and_protocol_keys_and_legacy(self):
        r = dict(scenario_id='s', eth_shock=0., btc_shock=0., positions_at_risk=1,
                 debt_at_risk_usd=50., debt_at_risk_pct=50.)
        self.assertEqual(check_schema([r], 'stress_summary'), [])
        rows = [dict(r, snapshot_block=100), dict(r, snapshot_block=101)]
        self.assertEqual(check_schema(rows, 'stress_summary'), [])
        self.assertTrue(check_schema(rows + [rows[0]], 'stress_summary'))
        self.assertTrue(check_schema([rows[0], r], 'stress_summary'))
        self.assertTrue(check_schema([dict(r, snapshot_block='A')], 'stress_summary'))
        self.assertTrue(check_schema([dict(r, snapshot_block=True)], 'stress_summary'))
        self.assertEqual(check_schema([dict(rows[0], protocol_id='a'), dict(rows[0], protocol_id='b')], 'stress_summary'), [])

    def test_heatmap_snapshot_keys_consistent_axes_and_legacy(self):
        r = dict(eth_shock=0., depeg_shock=0., debt_at_risk_usd=2.)
        rows = [dict(r, snapshot_block=100), dict(r, snapshot_block=101)]
        self.assertEqual(check_schema(rows, 'stress_heatmap'), [])
        self.assertTrue(check_schema(rows + [rows[0]], 'stress_heatmap'))
        self.assertTrue(check_schema([rows[0], dict(eth_shock=0., debt_at_risk_usd=2., btc_shock=0.)], 'stress_heatmap'))
        self.assertEqual(check_schema([r], 'stress_heatmap'), [])


class GuardTests(unittest.TestCase):
    def test_boolean_strings_and_missing_price(self):
        out = guard_stress_positions(pd.DataFrame([dict(position(), is_collateral_enabled='false')]))
        self.assertFalse(out.iloc[0].is_collateral_enabled)
        for value in [None, float('nan'), 0., -1.]:
            with self.assertRaises(ValueError):
                guard_stress_positions(pd.DataFrame([dict(position(), price_usd=value)]))
        with self.assertRaises(ValueError):
            guard_stress_positions(pd.DataFrame([dict(position(), is_collateral_enabled='unknown')]))

    def test_oracle_conflict_and_wrong_block_rejected(self):
        px = pd.DataFrame([dict(snapshot_block=100, protocol_id='aave_v3_ethereum', token_address='t', price_usd=2.)])
        with self.assertRaises(ValueError):
            guard_stress_positions(pd.DataFrame([position()]), px)
        px['price_usd'] = 1.
        guard_stress_positions(pd.DataFrame([position()]), px)
        px['snapshot_block'] = 101
        with self.assertRaises(ValueError):
            guard_stress_positions(pd.DataFrame([position()]), px)

    def test_zero_shock_detects_flips_and_separates_snapshots(self):
        r = dict(snapshot_block=100, protocol_id='p', wallet='w', hf_before=1.01, hf_after=1.01)
        self.assertEqual(validate_zero_shock([r, dict(r, snapshot_block=101)])['status'], 'passed')
        result = validate_zero_shock([dict(r, hf_after=.999)])
        self.assertEqual(result['status'], 'failed')
        self.assertEqual(result['eligibility_disagreements'], 1)
        self.assertEqual(validate_zero_shock([r, r])['status'], 'failed')
        self.assertEqual(validate_zero_shock([dict(r, hf_after=math.nan)])['status'], 'failed')

    def test_multiple_memberships_not_collapsed_and_permission_respected(self):
        row, cfg, prices, members = prepared()
        p = prepare_portfolio([row], cfg, prices, snapshot_block=100, depth_usd={'t': 1}, emode_membership=members)
        self.assertEqual(p.positions[0]['effective_lt'], .9)
        row['user_emode'] = 3
        p = prepare_portfolio([row], cfg, prices, snapshot_block=100, depth_usd={'t': 1}, emode_membership=members)
        self.assertEqual(p.positions[0]['effective_lt'], .5)

    def test_invalid_targets_all_branches(self):
        for target in [-1, True, math.nan, math.inf, {'usd': -1}, {'usd': math.nan},
                       {'pct': -1}, {'pct': 101}, '-10%', 'NaN%', '200%', {}]:
            with self.subTest(target=target), self.assertRaises(ValueError):
                parse_reverse_target(target, 100)
        self.assertEqual(parse_reverse_target('10%', 200), 20)
        self.assertEqual(parse_reverse_target({'pct': .1}, 200), 20)
        self.assertEqual(parse_reverse_target({'usd': 10}, 200), 10)

    def test_graph_token_conservation_detects_corruption(self):
        row, cfg, prices, members = prepared()
        p = prepare_portfolio([row], cfg, prices, snapshot_block=100, depth_usd={'t': 1}, emode_membership=members)
        edges = build_aave_graph(p)
        self.assertTrue(graph_conservation_report(edges, pd.DataFrame(p.positions)).passed.all())
        edges.loc[edges.edge_type.eq('debt_exposure'), 'weight_usd_t0'] += 1
        self.assertFalse(graph_conservation_report(edges, pd.DataFrame(p.positions)).passed.all())

    def test_hf_attribution_explains_dropped_membership_without_forcing_residual(self):
        row, cfg, prices, members = prepared()
        cfg[0]['usage_as_collateral'] = True
        p = prepare_portfolio([row], cfg, prices, snapshot_block=100, depth_usd={'t': 1}, emode_membership=members)
        source = pd.DataFrame([row])
        risk = pd.DataFrame([dict(snapshot_block=100, wallet_address='w', health_factor=1.)])
        tokens, wallets = attribute_hf_differences(p, source, pd.DataFrame(cfg), pd.DataFrame(members), risk, model_mode='first_membership')
        self.assertEqual(tokens.iloc[0].candidate_cause, 'membership_category_dropped')
        self.assertTrue(wallets.iloc[0].reconstruction_matches_n4)
        risk['health_factor'] = .7
        _, wallets = attribute_hf_differences(p, source, pd.DataFrame(cfg), pd.DataFrame(members), risk, model_mode='first_membership')
        self.assertEqual(wallets.iloc[0].attribution_status, 'unexplained_residual')

    def test_current_attribution_keeps_category_and_exposes_reserve_flag(self):
        row, cfg, prices, members = prepared()
        cfg[0]['usage_as_collateral'] = False
        p = prepare_portfolio([row], cfg, prices, snapshot_block=100, depth_usd={'t': 1}, emode_membership=members)
        risk = pd.DataFrame([dict(snapshot_block=100, wallet_address='w', health_factor=0.)])
        tokens, wallets = attribute_hf_differences(p,pd.DataFrame([dict(row,coll_flag=True)]),pd.DataFrame(cfg),pd.DataFrame(members),risk)
        self.assertTrue(tokens.iloc[0].wallet_collateral_enabled)
        self.assertFalse(tokens.iloc[0].n4_collateral_enabled)
        self.assertEqual(tokens.iloc[0].reconstructed_n4_lt,.9)
        self.assertTrue(wallets.iloc[0].reconstruction_matches_n4)
        self.assertEqual(tokens.iloc[0].candidate_cause,'collateral_flag_difference')

    def test_wallet_attribution_respects_permission_and_wallet_flag(self):
        row, cfg, prices, members = prepared()
        row['user_emode'] = 3  # Category exists but denies collateral E-Mode LT.
        cfg[0]['usage_as_collateral'] = False
        for enabled, hf in [(True, 1.), (False, 0.)]:
            source = dict(row, is_collateral_enabled=enabled, coll_flag=enabled)
            p = prepare_portfolio([source], cfg, prices, snapshot_block=100,
                depth_usd={'t': 1}, emode_membership=members)
            risk = pd.DataFrame([dict(snapshot_block=100, wallet_address='w', health_factor=hf)])
            tokens, wallets = attribute_hf_differences(p, pd.DataFrame([source]),
                pd.DataFrame(cfg), pd.DataFrame(members), risk, model_mode='wallet_collateral')
            self.assertEqual(tokens.iloc[0].reconstructed_n4_lt, .5)
            self.assertEqual(tokens.iloc[0].n4_collateral_enabled, enabled)
            self.assertTrue(wallets.iloc[0].reconstruction_matches_n4)

    def test_baseline_kpis_nonpositive_equity_has_no_fabricated_leverage(self):
        row = position()
        row['debt_amount'] = 150.
        cats = pd.DataFrame([dict(snapshot_block=100, protocol_id='aave_v3_ethereum', wallet_address='w', emode_category=1)])
        out = baseline_wallet_kpis(pd.DataFrame([row]), cats)
        self.assertEqual(out.iloc[0].net_equity_usd, -50.)
        self.assertTrue(pd.isna(out.iloc[0].effective_leverage))
        self.assertFalse(out.iloc[0].is_recursive_loop)


class CompoundTests(unittest.TestCase):
    def tables(self):
        p = pd.DataFrame([dict(snapshot_block=100, market_id='comet', wallet_address='w',
             token_address='t', asset_role='base_supply', amount_token=10., price_usd=2., value_usd=20.)])
        cfg = pd.DataFrame([dict(snapshot_block=100, market_id='comet', token_address='t',
                                price_usd=2., liquidate_collateral_factor=None)])
        return p, cfg

    def test_base_supply_not_collateral_and_flows_conserve_tokens(self):
        p, cfg = self.tables()
        adapted = adapt_compound_partial(p, cfg)
        self.assertFalse(adapted.iloc[0].is_collateral)
        event = dict(snapshot_block=100, market_id='comet', wallet_address='w', token_address='t',
                     asset_role='base_supply', event_id='e1', quantity_delta=-3., reason='synthetic_fixture')
        final, ledger = track_compound_flows(adapted, [event])
        self.assertEqual(final.iloc[0].amount_token, 7)
        self.assertEqual(ledger.iloc[0].delta_usd_at_fixed_price, -6)
        self.assertEqual(adapted.iloc[0].amount_token, 10)
        for events in [[event, event], [dict(event, quantity_delta=-11)], [dict(event, wallet_address='unknown')]]:
            with self.assertRaises(ValueError):
                track_compound_flows(adapted, events)

    def test_compound_config_block_and_price_must_match(self):
        p, cfg = self.tables()
        for changed in [cfg.assign(snapshot_block=101), cfg.assign(price_usd=3.)]:
            with self.assertRaises(ValueError):
                adapt_compound_partial(p, changed)


if __name__ == '__main__':
    unittest.main()
