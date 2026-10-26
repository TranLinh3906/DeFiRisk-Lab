"""Complete the predeclared Q1/Q2/Q3 tests, preserving prior research bundles."""
from __future__ import annotations
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil

import numpy as np
import pandas as pd
from scipy import stats

from scripts.n5_resolve_research_review import load_snapshot, mc_exact
from src.models.n5_corrected_research import BoundedCurve, execute
from src.models.cascade import collateral_value, debt_value
from src.models.n5_hypothesis_methods import (past_returns, scenarios_from_returns, equity_pairs,
    paired_summary, convexity_test, u_shape_tests, temporal_feedback_validation)
from src.models.stress_test import _asset_multiplier

OLD = Path('outputs/n5_review_corrected_20261026')
INPUTS = Path('outputs/n5_hypothesis_inputs_20261026')
COMPLETION = Path('outputs/n5_completion_20261026')
KEY_SCENARIOS = dict(eth_drop_20={'eth_shock': -.2}, eth_drop_40={'eth_shock': -.4},
    depeg_4={'depeg_shock': -.04}, depeg_5={'depeg_shock': -.05}, depeg_10={'depeg_shock': -.1},
    combined_eth20_depeg5={'eth_shock': -.2, 'depeg_shock': -.05})


def save(root, name, value):
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    if name.endswith('.parquet'):
        value.to_parquet(path, index=False)
    elif name.endswith('.csv'):
        value.to_csv(path, index=False)
    else:
        path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False), encoding='utf-8')


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def research_inputs(include_compound=False):
    paths = [COMPLETION / 'n4_research/simulation_positions.parquet',
        COMPLETION / 'approved_package_with27/curve_points_with_quantization_audit.parquet',
        COMPLETION / 'calibration_market/calibration_levels.parquet',
        COMPLETION / 'depeg_peak/depeg_every_peak_block.parquet', Path('config/snapshots.json'),
        Path('data/processed/aave/reserve_config.parquet')]
    paths += list(INPUTS.rglob('*parquet')) + list(INPUTS.rglob('raw*.json')) + list(INPUTS.rglob('coverage.json'))
    route_folder=Path('outputs/n5_hypothesis_dex_inputs_20261026')
    if route_folder.exists(): paths += [p for p in route_folder.iterdir() if p.is_file()]
    for sid in ['A','B1','B2']:
        paths += [OLD / sid / 'baseline_wallet_kpis.parquet', OLD / sid / 'network_wallet_nodes.parquet']
    for sid in ['B1','B2']:
        paths += [COMPLETION / f'history/{sid}/oracle_all_active_tokens.parquet', COMPLETION / f'history/{sid}/liquidations_rpc.parquet']
    if include_compound:
        paths += [OLD/'compound/exposures_usd.parquet',
            Path('outputs/compound_consolidated_A_B1_B2_20261025_complete/n3_compound_reserve_config.parquet'),
            Path('outputs/research_review_fix_20261026/comet_state/comet_state.json')]
    return paths


def setup_snapshots():
    positions = pd.read_parquet(COMPLETION / 'n4_research/simulation_positions.parquet')
    curves = pd.read_parquet(COMPLETION / 'approved_package_with27/curve_points_with_quantization_audit.parquet')
    fees = {'0x109830a1aaad605bbf02a9dfa7b0b92ec2fb7daa': .0001,
            '0x88e6a0c2ddd26feeb64f039a2c41296fcb3f5640': .0005,
            '0xcbcdf9626bc03e24f779434178a73a0b4bad62ed': .003}
    snapshots = {}
    for sid in ['A', 'B1', 'B2']:
        base = pd.read_parquet(OLD / sid / 'baseline_wallet_kpis.parquet')
        snap = load_snapshot(positions, base)
        snap['routes'] = {(ct, dt): dict(pool_address=pool, curve=BoundedCurve(g), fee_rate=fees[pool])
            for (pool, ct, dt), g in curves[curves.snapshot_id.eq(sid)].groupby(['pool_address', 'token_in_address', 'token_out_address'])}
        snap['nodes'] = pd.read_parquet(OLD / sid / 'network_wallet_nodes.parquet').set_index('wallet_address').loc[base.wallet_address].reset_index()
        snapshots[sid] = snap
    return snapshots


def run_scenario(snap, params, *, cf=None, depth=1., mode='fixed', proxy=0., blocked=frozenset(), disabled=frozenset()):
    params = dict(params, depeg_assets=['STETH', 'WSTETH'])
    multipliers = {t: _asset_multiplier(symbol.upper(), params) for t, symbol in snap['symbols'].items()}
    vector = np.array([multipliers[t] for t in snap['token_ids']])
    d, wc = snap['d'] @ vector, snap['wc'] @ vector
    shock_hf = np.divide(wc, d, out=np.full(len(d), np.inf), where=d > 0)
    active_feedback = mode == 'full_sensitivity' or (mode == 'predictive_proxy' and proxy > 0)
    ix = np.flatnonzero(shock_hf < (1/.85 if active_feedback else 1))
    token_feedback = ({t: proxy for t, s in snap['symbols'].items() if s.upper() in ['WSTETH', 'STETH']}
                      if mode == 'predictive_proxy' else None)
    result = execute([snap['wallets'][i] for i in ix], snap['assets'], multipliers, snap['routes'],
        fixed_cf=cf, depth_scale=depth, feedback=float(active_feedback), feedback_by_token=token_feedback,
        blocked_sellers=blocked, disabled_pairs=disabled)
    if result.summary['stop_reason'] == 'round_limit':
        raise RuntimeError('Execution reached round limit')
    result.summary['post_execution_bdr_pct'] = 100 * result.summary['post_execution_bad_debt_usd'] / float(snap['baseline'].debt_usd.sum())
    result.summary['initial_cohort_debt_usd'] = float(snap['baseline'].debt_usd.sum())
    return result, shock_hf


def losses_for(snap, result):
    state = {w.wallet: w for w in result.wallets}
    return np.array([max(0., debt_value(state.get(w.wallet, w), result.prices) - collateral_value(state.get(w.wallet, w), result.prices))
                     for w in snap['wallets']])


def prepare(root):
    peak = pd.read_parquet(COMPLETION / 'depeg_peak/depeg_every_peak_block.parquet')
    oracles = {sid: pd.read_parquet(COMPLETION / f'history/{sid}/oracle_all_active_tokens.parquet') for sid in ['B1', 'B2']}
    events = {sid: pd.read_parquet(COMPLETION / f'history/{sid}/liquidations_rpc.parquet') for sid in ['B1', 'B2']}
    data, feedback = temporal_feedback_validation(peak, oracles, events, pd.read_parquet('data/processed/aave/reserve_config.parquet'))
    save(root, 'feedback/temporal_observations.parquet', data)
    save(root, 'feedback/validation.json', feedback)
    if Path('outputs/n5_hypothesis_dex_inputs_20261026/dex_three_route_spots.parquet').exists():
        from scripts.n5_validate_route_feedback import main as validate_routes
        validate_routes(['--bundle',str(root)])
    print('B1 -> B2 feedback proxy:', feedback['calibrated_predictive_proxy'], 'causal identification remains observational', flush=True)


def q1(root, snapshots, config):
    comparisons, inference, convexity = [], [], []
    crisis = pd.read_parquet(INPUTS / 'crisis_2022/levels.parquet')
    for sid, snap in snapshots.items():
        b = snap['baseline']
        cutoff = config['snapshots'][sid]['block_timestamp_utc']
        recent = pd.read_parquet(COMPLETION / 'calibration_market/calibration_levels.parquet' if sid == 'A' else INPUTS / sid / 'levels.parquet')
        recent_r = past_returns(recent, cutoff)
        pairs = equity_pairs(b, caliper=.1)
        save(root, f'q1/{sid}/equity_pairs.csv', pairs)
        wi = {a: i for i, a in enumerate(b.wallet_address)}
        loop_ix = np.array([wi[a] for a in pairs.looping_wallet])
        non_ix = np.array([wi[a] for a in pairs.non_looping_wallet])
        equity = b.net_equity_usd.to_numpy(float)
        modes = [('pre_event_t4_1d', recent_r, 't4'), ('pre_event_gaussian_1d', recent_r, 'gaussian'),
                 ('crisis_joint_1d', past_returns(crisis, cutoff), 'paired_bootstrap'),
                 ('crisis_joint_5d', past_returns(crisis, cutoff, horizon=5), 'paired_bootstrap')]
        for name, returns, method in modes:
            summary_file = root / f'q1/{sid}/{name}_paired_summary.json'
            if summary_file.exists():
                inference.append(json.loads(summary_file.read_text(encoding='utf-8')))
                comparisons.append(pd.read_csv(root / f'q1/{sid}/{name}_comparison.csv'))
                print(sid, name, 'reuse completed distribution', flush=True)
                continue
            print(sid, name, '10000 exact MC draws; calibration pairs', len(returns), flush=True)
            shocks = scenarios_from_returns(returns, mode=method)
            save(root, f'q1/{sid}/{name}_calibration.parquet', returns)
            save(root, f'q1/{sid}/{name}_shocks.parquet', shocks)
            values = []
            def sink(scenario_id, losses, pre):
                values.append(dict(scenario_id=scenario_id,
                    looping=float(np.mean(losses[loop_ix] / equity[loop_ix] * 1e5)),
                    non_looping=float(np.mean(losses[non_ix] / equity[non_ix] * 1e5))))
            def progress(done, total):
                if done % 2048 == 0 or done == total:
                    print(sid, name, done, '/', total, flush=True)
            risk, _, comp, execution = mc_exact(snap, shocks, snap['routes'], progress=progress,
                scenario_sink=sink, reuse_identical_shocks=method == 'paired_bootstrap')
            paired = pd.DataFrame(values)
            summary = paired_summary(risk, pairs, paired)
            test_record = dict(snapshot_id=sid, distribution=name, calibration_pairs=len(returns),
                cutoff=cutoff, horizon_days=5 if name.endswith('5d') else 1,
                observed_min_depeg_return=float(returns.depeg_return.min()), **summary)
            inference.append(test_record)
            comp['snapshot_id'], comp['distribution'] = sid, name
            comparisons.append(comp)
            save(root, f'q1/{sid}/{name}_wallet_risk.parquet', risk)
            save(root, f'q1/{sid}/{name}_matched_scenarios.parquet', paired)
            save(root, f'q1/{sid}/{name}_execution.csv', execution)
            save(root, f'q1/{sid}/{name}_comparison.csv', comp)
            save(root, f'q1/{sid}/{name}_paired_summary.json', test_record)
        gaps = np.arange(0, .150001, .005)
        for eth in [0., -.2]:
            curve = []
            for depeg in gaps:
                result, _ = run_scenario(snap, dict(eth_shock=eth, depeg_shock=-float(depeg)))
                loss = losses_for(snap, result)
                curve.append(loss[loop_ix] / equity[loop_ix] * 1e5 - loss[non_ix] / equity[non_ix] * 1e5)
            paired_gaps = np.column_stack(curve)
            convexity.append(dict(snapshot_id=sid, eth_shock=eth, **convexity_test(gaps, paired_gaps)))
            save(root, f'q1/{sid}/convexity_eth_{abs(eth):.1f}.csv', pd.DataFrame(dict(depeg_drop=gaps, paired_mean_loss_gap=paired_gaps.mean(0))))
            print(sid, 'convexity curve', eth, 'complete', flush=True)
        save(root, f'q1/{sid}/complete.json', dict(status='completed', distributions=4, scenarios_each=10000, equity_pairs=len(pairs)))
    save(root, 'q1/comparison.csv', pd.concat(comparisons, ignore_index=True))
    save(root, 'q1/paired_hypothesis_tests.json', inference)
    save(root, 'q1/convexity_tests.json', convexity)


def q2(root, snapshots, proxy):
    records = []
    for sid, snap in snapshots.items():
        checkpoint = root / f'q2/{sid}_checkpoint.csv'
        if checkpoint.exists():
            prior = pd.read_csv(checkpoint)
            if len(prior) != 486:
                raise ValueError('Incomplete dense CF checkpoint')
            records.extend(prior.to_dict('records'))
            print(sid, 'reuse dense CF checkpoint', flush=True)
            continue
        for scenario, params in KEY_SCENARIOS.items():
            cache = {}
            for depth in [.5, 1., 2.]:
                for cf in np.round(np.arange(.2, 1.001, .1), 2):
                    for mode in ['fixed', 'predictive_proxy', 'full_sensitivity']:
                        equivalent = 'fixed' if mode == 'predictive_proxy' and proxy == 0 else mode
                        key = (depth, cf, equivalent)
                        if key not in cache:
                            cache[key] = run_scenario(snap, params, cf=float(cf), depth=depth, mode=equivalent, proxy=proxy)[0]
                        result = cache[key]
                        records.append(dict(snapshot_id=sid, scenario_id=scenario, feedback_mode=mode, **result.summary))
            print(sid, 'dense CF grid', scenario, 'complete', flush=True)
        save(root, f'q2/{sid}_checkpoint.csv', pd.DataFrame([r for r in records if r['snapshot_id'] == sid]))
    grid = pd.DataFrame(records)
    save(root, 'q2/dense_cf_grid.csv', grid)
    save(root, 'q2/u_shape_tests.json', u_shape_tests(grid))
    paired = grid.pivot(index=['snapshot_id', 'scenario_id', 'depth_scale', 'feedback_mode'], columns='fixed_cf',
        values=['round_1_sold_collateral_usd', 'newly_eligible_round_ge_2', 'post_execution_bad_debt_usd'])
    contrast = paired.xs(1., axis=1, level='fixed_cf') - paired.xs(.5, axis=1, level='fixed_cf')
    contrast.columns = ['delta_fixed100_minus_fixed50_' + name for name in contrast.columns]
    save(root, 'q2/h2a_paired_contrasts.csv', contrast.reset_index())


def null_associations(nodes, outcome, n_null=999):
    """Network-label nulls preserve token strengths and per-wallet degree.

    Row relabeling within exact-degree and/or exposure-decile strata is an
    alternative to rewiring. It tests association with fixed spillover outcomes.
    """
    centrality = nodes.weighted_token_pair_betweenness.to_numpy(float)
    if np.all(outcome == outcome[0]) or np.all(centrality == centrality[0]):
        return dict(status='not_identified_constant_outcome_or_centrality')
    x, y = stats.rankdata(centrality), stats.rankdata(outcome)
    x = x - x.mean(); y = y - y.mean()
    norm = np.linalg.norm(x) * np.linalg.norm(y)
    observed = float(x @ y / norm)
    deciles = pd.qcut(nodes.weighted_strength_usd.rank(method='first'), 10, labels=False)
    tests = []
    for name in ['exact_degree', 'exposure_decile', 'degree_and_exposure_decile']:
        labels = (nodes.degree.astype(str) if name == 'exact_degree' else deciles.astype(str) if name == 'exposure_decile'
                  else nodes.degree.astype(str) + ':' + deciles.astype(str))
        groups = list(pd.Series(np.arange(len(nodes))).groupby(labels.to_numpy()).apply(list))
        rng = np.random.default_rng(42)
        values = []
        for _ in range(n_null):
            perm = np.arange(len(nodes))
            for ix in groups:
                perm[ix] = rng.permutation(ix)
            values.append(float(x[perm] @ y / norm))
        tests.append(dict(null=name, draws=n_null, observed_spearman=observed,
            positive_association_randomization_p=(1 + int(np.count_nonzero(np.asarray(values) >= observed))) / (n_null + 1),
            permutable_wallets=sum(len(ix) for ix in groups if len(ix) > 1),
            preserves='network_topology_and_token_strengths;_degree_exact_only_when_degree_stratified',
            inference='conditional_network_label_association_not_causal_effect'))
    return dict(status='tested', tests=tests)


def q3(root, snapshots, proxy):
    summaries, associations, interventions = [], {}, []
    for sid, snap in snapshots.items():
        b, nodes = snap['baseline'], snap['nodes']
        addresses = b.wallet_address.to_numpy()
        is_other = np.array([snap['symbols'][t].upper() not in ['WSTETH', 'STETH'] for t in snap['token_ids']])
        other_value = snap['c'][:, is_other].sum(1)
        other = (other_value >= 100) & (other_value >= .1 * snap['c'].sum(1))
        healthy = b.health_factor.to_numpy(float) >= 1.10
        outcomes = []
        for depeg in [.04, .05, .07, .1]:
            direct, initial_hf = run_scenario(snap, dict(depeg_shock=-depeg))
            direct_loss = losses_for(snap, direct)
            eligible_recipient = healthy & other & (initial_hf >= 1)
            for mode in ['predictive_proxy', 'full_sensitivity']:
                result, _ = run_scenario(snap, dict(depeg_shock=-depeg), mode=mode, proxy=proxy)
                delta = losses_for(snap, result) - direct_loss
                newly = np.array([result.first_eligible_round.get(a, 0) >= 2 for a in addresses])
                induced = newly & eligible_recipient
                frame = pd.DataFrame(dict(wallet_address=addresses, initial_hf=b.health_factor,
                    shocked_hf=initial_hf, healthy_other_asset_recipient=eligible_recipient,
                    newly_eligible_round_ge_2=newly, induced_healthy_other=induced,
                    direct_bad_debt_usd=direct_loss, incremental_bad_debt_usd=delta,
                    positive_incremental_bad_debt_usd=np.maximum(0, delta)))
                save(root, f'q3/{sid}/depeg_{depeg:.2f}_{mode}.parquet', frame)
                summaries.append(dict(snapshot_id=sid, depeg_shock=-depeg, feedback_mode=mode,
                    healthy_other_eligible_denominator=int(eligible_recipient.sum()),
                    induced_healthy_other_wallets=int(induced.sum()),
                    induced_transition_rate=float(induced.sum()/eligible_recipient.sum()) if eligible_recipient.any() else None,
                    all_newly_eligible=int(newly.sum()), incremental_bad_debt_usd=float(delta.sum()),
                    positive_incremental_bad_debt_usd=float(np.maximum(0, delta).sum()),
                    research_scope='pure_depeg;_HF0>=1.10;_post_exogenous_HF>=1;_other_collateral_share>=10pct_and_USD100'))
                if mode == 'full_sensitivity':
                    outcomes.append(np.maximum(0, delta))
            print(sid, 'direct versus induced pure depeg', depeg, flush=True)
        associations[sid] = null_associations(nodes, np.mean(outcomes, axis=0))
        # Source interventions retain all financial balances; evaluate losses
        # only on recipients outside the intervened source set.
        params = dict(depeg_shock=-.1)
        direct, _ = run_scenario(snap, params)
        direct_loss = losses_for(snap, direct)
        full, _ = run_scenario(snap, params, mode='full_sensitivity')
        full_loss = losses_for(snap, full)
        count = max(1, int(np.ceil(.01 * len(b))))
        ranked = nodes.sort_values(['weighted_token_pair_betweenness', 'wallet_address'], ascending=[False, True])
        top = set(ranked.head(count).wallet_address)
        deciles = pd.qcut(nodes.weighted_strength_usd.rank(method='first'), 10, labels=False).to_numpy()
        groups = {}
        for i, (degree, decile) in enumerate(zip(nodes.degree, deciles)):
            groups.setdefault((int(degree), int(decile)), []).append(addresses[i])
        address_group = {a: key for key, values in groups.items() for a in values}
        rng = np.random.default_rng(42)
        sets = [('top_1pct_betweenness', top)]
        for draw in range(19):
            sampled = set()
            for a in sorted(top):
                candidates = [v for v in groups[address_group[a]] if v not in sampled]
                sampled.add(candidates[int(rng.integers(len(candidates)))])
            sets.append((f'degree_strength_matched_random_{draw:02}', sampled))
        for name, blocked in sets:
            result, _ = run_scenario(snap, params, mode='full_sensitivity', blocked=blocked)
            keep = ~b.wallet_address.isin(blocked).to_numpy()
            after = losses_for(snap, result)
            interventions.append(dict(snapshot_id=sid, intervention=name, blocked_sources=len(blocked),
                recipient_wallets=int(keep.sum()), recipient_debt_t0_usd=float(b.debt_usd.to_numpy()[keep].sum()),
                baseline_positive_incremental_loss_usd=float(np.maximum(0, full_loss[keep]-direct_loss[keep]).sum()),
                intervention_positive_incremental_loss_usd=float(np.maximum(0, after[keep]-direct_loss[keep]).sum()),
                top_source_overlap=len(top & blocked),
                semantics='block_liquidation_transmission_keep_balances;_exclude_intervened_sources_from_recipient_metric'))
        for ct, dt in snap['routes']:
            result, _ = run_scenario(snap, params, mode='full_sensitivity', disabled={(ct, dt)})
            after = losses_for(snap, result)
            interventions.append(dict(snapshot_id=sid, intervention=f'disable_pair_{snap["symbols"][ct]}_{snap["symbols"][dt]}',
                blocked_sources=0, recipient_wallets=len(b), recipient_debt_t0_usd=float(b.debt_usd.sum()),
                baseline_positive_incremental_loss_usd=float(np.maximum(0, full_loss-direct_loss).sum()),
                intervention_positive_incremental_loss_usd=float(np.maximum(0, after-direct_loss).sum()),
                semantics='disable_selected_execution_edge_keep_all_balances'))
        save(root, f'q3/{sid}/complete.json', dict(status='completed', source_interventions=20, edge_interventions=len(snap['routes'])))
        print(sid, 'node/edge interventions complete', flush=True)
    save(root, 'q3/direct_induced_summary.csv', pd.DataFrame(summaries))
    save(root, 'q3/incremental_loss_network_nulls.json', associations)
    save(root, 'q3/node_edge_interventions.csv', pd.DataFrame(interventions))


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--output-dir', type=Path, default=Path('outputs/n5_hypothesis_completion_20261026'))
    ap.add_argument('--stages', default='prepare,q1,q2,q3,report')
    ap.add_argument('--refresh-report', action='store_true', help='Regenerate reporting with preserved analytical artifacts')
    args = ap.parse_args()
    root = args.output_dir
    root.mkdir(exist_ok=True, parents=True)
    stages = args.stages.split(',')
    settings = dict(version='n5_hypothesis_completion_v1', seed=42, mc_scenarios=10000,
        mc_distributions=['pre_event_t4_1d', 'pre_event_gaussian_1d', 'crisis_joint_1d', 'crisis_joint_5d'],
        fixed_cf_grid=[.2,.3,.4,.5,.6,.7,.8,.9,1.], depth_scales=[.5,1.,2.],
        key_scenarios=KEY_SCENARIOS, equity_log_caliper=.1, matching='one_to_one_without_replacement_equity_only',
        convexity_depeg_grid=[float(v) for v in np.arange(0, .150001, .005)],
        pure_depeg_tests=[.04,.05,.07,.1], healthy_threshold=1.10, other_asset_min_share=.1,
        feedback_training='B1', feedback_held_out='B2', source_ablation_fraction=.01, matched_random_ablations=19,
        scientific_rule='do_not_force_hypotheses_true;_observational_feedback_is_not_causal_identification')
    settings_file = root / 'preregistered_settings.json'
    if settings_file.exists() and json.loads(settings_file.read_text(encoding='utf-8')) != settings:
        raise ValueError('Research settings changed; choose a new output directory')
    save(root, 'preregistered_settings.json', settings)
    external = research_inputs(include_compound=(root/'compound_input_freeze.json').exists())
    input_records = [dict(path=str(p.resolve()), sha256=digest(p)) for p in external]
    freeze_path = root / 'input_freeze.json'
    if freeze_path.exists():
        previous = json.loads(freeze_path.read_text(encoding='utf-8'))
        supplemental=root/'feedback_input_freeze.json'
        if supplemental.exists():
            previous += json.loads(supplemental.read_text(encoding='utf-8'))
        supplemental=root/'compound_input_freeze.json'
        if supplemental.exists(): previous += json.loads(supplemental.read_text(encoding='utf-8'))
        if {v['path']:v['sha256'] for v in previous} != {v['path']:v['sha256'] for v in input_records}:
            raise ValueError('Observed inputs changed after freeze; choose a new bundle')
    else:
        save(root, 'input_freeze.json', input_records)
    config = json.loads(Path('config/snapshots.json').read_text(encoding='utf-8'))
    snapshots = None
    for stage in stages:
        marker = root / f'{stage}_completed.json'
        if stage=='report' and args.refresh_report and marker.exists():
            archived=root/('report_previous_'+digest(marker)[:16]+'.json')
            marker.replace(archived)
        if marker.exists():
            print('already completed', stage, flush=True)
            continue
        print('STAGE', stage, flush=True)
        sources = [Path(__file__), Path('src/models/n5_hypothesis_methods.py'), Path('src/models/n5_corrected_research.py'),
                   Path('scripts/n5_resolve_research_review.py'), Path('src/models/cascade.py'), Path('src/models/stress_test.py'),
                   Path('src/models/n5_research.py'), Path('src/models/n5_research_protocol.py'),
                   Path('src/models/n5_impact_review.py'), Path('src/models/n5_n3_update.py')]
        if Path('scripts/n5_hypothesis_report.py').exists():
            sources.append(Path('scripts/n5_hypothesis_report.py'))
        records = []
        for source in sources:
            sha = digest(source)
            dest = root / 'source_versions' / (sha + '.py')
            dest.parent.mkdir(exist_ok=True)
            if not dest.exists():
                shutil.copyfile(source, dest)
            records.append(dict(original_path=str(source), archived_path=dest.relative_to(root).as_posix(), sha256=sha))
        if stage == 'prepare':
            prepare(root)
        elif stage in ['q1', 'q2', 'q3']:
            if snapshots is None:
                snapshots = setup_snapshots()
            proxy = json.loads((root / 'feedback/validation.json').read_text(encoding='utf-8'))['calibrated_predictive_proxy']
            if stage == 'q1': q1(root, snapshots, config)
            elif stage == 'q2': q2(root, snapshots, proxy)
            else: q3(root, snapshots, proxy)
        elif stage == 'report':
            from scripts.n5_hypothesis_report import build_report
            build_report(root)
        else:
            raise ValueError('Unknown stage')
        save(root, f'{stage}_completed.json', dict(stage=stage, completed_utc=datetime.now(timezone.utc).isoformat(), code_versions=records))
    # Capture files AND the observed external inputs. Archived source versions
    # preserve earlier completed stages when a later reporting stage is fixed.
    compound_freeze=root/'compound_input_freeze.json'
    if compound_freeze.exists():
        known={r['path'] for r in input_records}
        input_records += [r for r in json.loads(compound_freeze.read_text(encoding='utf-8')) if r['path'] not in known]
    if any(digest(Path(row['path'])) != row['sha256'] for row in input_records):
        raise RuntimeError('Observed input changed during computation')
    manifest = dict(version=settings['version'], scientific_acceptance='hypothesis_specific_not_blanket_acceptance',
        inputs=input_records,
        files=[dict(path=p.relative_to(root).as_posix(), sha256=digest(p), bytes=p.stat().st_size)
               for p in root.rglob('*') if p.is_file() and p.name != 'manifest.json'])
    save(root, 'manifest.json', manifest)
    print('Completed stages:', args.stages, flush=True)


if __name__ == '__main__':
    main()
