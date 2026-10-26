"""Read-only data loader for DeFiRisk-Lab (Data Contract v1.0).

This module NEVER creates, overwrites or repairs research data.
Schema specs below are generated from schema_storage_safe.py supplied by the team.
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

CONTRACT = {'dim_asset_config': {'path': 'data/processed/dim_asset_config.parquet', 'primary_keys': ['snapshot_block', 'protocol_id', 'token_address'], 'foreign_keys': [], 'address_cols': ['token_address'], 'fields': [('snapshot_block', 'pa.int64()', False), ('snapshot_timestamp', "pa.timestamp('ns', tz='UTC')", False), ('protocol_id', 'pa.string()', False), ('token_address', 'pa.string()', False), ('token_symbol', 'pa.string()', False), ('asset_class', 'pa.string()', False), ('decimals', 'pa.int32()', False), ('price_usd_t0', 'pa.float64()', False), ('price_eth_t0', 'pa.float64()', False), ('ltv_std', 'pa.float64()', False), ('lt_std', 'pa.float64()', False), ('liq_bonus_std', 'pa.float64()', False), ('lt_emode', 'pa.float64()', False), ('liq_bonus_emode', 'pa.float64()', False), ('dex_depth_2pct_usd', 'pa.float64()', False), ('lambda_impact', 'pa.float64()', False), ('gamma_exponent', 'pa.float64()', False)]}, 'fact_wallet_positions': {'path': 'data/processed/fact_wallet_positions.parquet', 'primary_keys': ['snapshot_block', 'protocol_id', 'wallet_address', 'token_address'], 'foreign_keys': [{'parent_table': 'dim_asset_config', 'parent_path': 'data/processed/dim_asset_config.parquet', 'join_columns': ['snapshot_block', 'protocol_id', 'token_address']}], 'address_cols': ['wallet_address', 'token_address'], 'fields': [('snapshot_block', 'pa.int64()', False), ('protocol_id', 'pa.string()', False), ('wallet_address', 'pa.string()', False), ('token_address', 'pa.string()', False), ('is_collateral_enabled', 'pa.bool_()', False), ('emode_category', 'pa.int32()', False), ('is_isolated_collateral', 'pa.bool_()', False), ('collateral_amount_norm', 'pa.float64()', False), ('collateral_usd_t0', 'pa.float64()', False), ('debt_amount_norm', 'pa.float64()', False), ('debt_usd_t0', 'pa.float64()', False), ('effective_lt', 'pa.float64()', False), ('effective_liq_bonus', 'pa.float64()', False)]}, 'fact_wallet_summary': {'path': 'data/processed/fact_wallet_summary.parquet', 'primary_keys': ['snapshot_block', 'protocol_id', 'wallet_address'], 'foreign_keys': [{'parent_table': 'fact_wallet_positions', 'parent_path': 'data/processed/fact_wallet_positions.parquet', 'join_columns': ['snapshot_block', 'protocol_id', 'wallet_address']}], 'address_cols': ['wallet_address'], 'fields': [('snapshot_block', 'pa.int64()', False), ('protocol_id', 'pa.string()', False), ('wallet_address', 'pa.string()', False), ('emode_category', 'pa.int32()', False), ('total_collateral_usd', 'pa.float64()', False), ('total_debt_usd', 'pa.float64()', False), ('weighted_collateral_lt_usd', 'pa.float64()', False), ('net_equity_usd', 'pa.float64()', False), ('equity_bucket', 'pa.string()', False), ('effective_leverage', 'pa.float64()', False), ('hf_calculated_t0', 'pa.float64()', False), ('hf_reported_t0', 'pa.float64()', True), ('dist_to_liq_pct', 'pa.float64()', False), ('dominant_collateral_symbol', 'pa.string()', False), ('dominant_debt_symbol', 'pa.string()', False), ('is_recursive_loop', 'pa.bool_()', False), ('is_multi_collateral', 'pa.bool_()', False), ('is_cross_protocol', 'pa.bool_()', False)]}, 'graph_network_edges': {'path': 'data/processed/graph_network_edges.parquet', 'primary_keys': ['snapshot_block', 'edge_id'], 'foreign_keys': [], 'address_cols': [], 'fields': [('snapshot_block', 'pa.int64()', False), ('edge_id', 'pa.string()', False), ('source_node_id', 'pa.string()', False), ('source_layer', 'pa.string()', False), ('target_node_id', 'pa.string()', False), ('target_layer', 'pa.string()', False), ('edge_type', 'pa.string()', False), ('protocol_id', 'pa.string()', False), ('weight_usd_t0', 'pa.float64()', False), ('effective_lt', 'pa.float64()', True)]}, 'out_simulation_results': {'path': 'outputs/tables/out_simulation_results.parquet', 'primary_keys': ['run_id', 'question_id', 'scenario_name', 'cf_rule', 'cascade_round', 'protocol_id', 'segment_group'], 'foreign_keys': [], 'address_cols': [], 'fields': [('run_id', 'pa.string()', False), ('snapshot_block', 'pa.int64()', False), ('question_id', 'pa.string()', False), ('scenario_name', 'pa.string()', False), ('cf_rule', 'pa.string()', False), ('cascade_round', 'pa.int32()', False), ('protocol_id', 'pa.string()', False), ('segment_group', 'pa.string()', False), ('round_liq_vol_usd', 'pa.float64()', False), ('cum_liq_vol_usd', 'pa.float64()', False), ('pos_bad_debt_usd', 'pa.float64()', False), ('bad_debt_ratio_pct', 'pa.float64()', False), ('equity_norm_loss_pct', 'pa.float64()', True), ('prob_bad_debt_pct', 'pa.float64()', True), ('tail_loss_es99_usd', 'pa.float64()', True), ('dex_price_impact_pct', 'pa.float64()', False), ('seq_liquidated_count', 'pa.int32()', False), ('cascade_amp_factor', 'pa.float64()', False), ('liq_freeze_vol_usd', 'pa.float64()', False), ('healthy_to_liq_rate_pct', 'pa.float64()', True), ('cross_protocol_contagion_pct', 'pa.float64()', True)]}, 'fact_historical_liquidations': {'path': 'data/processed/fact_historical_liquidations.parquet', 'primary_keys': ['tx_hash', 'log_index'], 'foreign_keys': [{'parent_table': 'dim_asset_config', 'parent_path': 'data/processed/dim_asset_config.parquet', 'join_columns': ['protocol_id', 'collateral_token_address->token_address']}], 'address_cols': ['wallet_address', 'liquidator_address', 'collateral_token_address', 'debt_token_address'], 'fields': [('tx_hash', 'pa.string()', False), ('log_index', 'pa.int32()', False), ('event_block', 'pa.int64()', False), ('event_timestamp', "pa.timestamp('ns', tz='UTC')", False), ('protocol_id', 'pa.string()', False), ('wallet_address', 'pa.string()', False), ('liquidator_address', 'pa.string()', False), ('collateral_token_address', 'pa.string()', False), ('debt_token_address', 'pa.string()', False), ('debt_repaid_usd', 'pa.float64()', False), ('collateral_seized_usd', 'pa.float64()', False)]}}


def _arrow_type(expr: str):
    if expr == 'pa.int64()': return pa.int64()
    if expr == 'pa.int32()': return pa.int32()
    if expr == 'pa.float64()': return pa.float64()
    if expr == 'pa.string()': return pa.string()
    if expr == 'pa.bool_()': return pa.bool_()
    if expr == "pa.timestamp('ns', tz='UTC')": return pa.timestamp('ns', tz='UTC')
    raise ValueError(f'Unsupported contract type: {expr}')


def _safe_path(root: Path, relative: str) -> Path:
    path = (root / relative).resolve()
    if not path.is_relative_to(root.resolve()):
        raise ValueError('Unsafe path outside project root')
    return path


def inspect_table(table_name: str, project_root: str | Path = '.', *, check_rows: bool = True) -> dict:
    """Inspect schema, PK, EVM addresses, and snapshot identifiers; never modify data."""
    if table_name not in CONTRACT:
        raise ValueError(f'Unknown table: {table_name}')
    root = Path(project_root).resolve()
    cfg = CONTRACT[table_name]
    path = _safe_path(root, cfg['path'])
    result = {'table': table_name, 'path': str(path), 'exists': path.is_file(),
              'rows': None, 'snapshots': [], 'errors': [], 'warnings': [], 'status': 'missing'}
    if not path.is_file():
        result['errors'].append('File not found')
        return result
    try:
        schema = pq.read_schema(path)
        expected = cfg['fields']
        actual_names = schema.names
        expected_names = [f[0] for f in expected]
        if actual_names != expected_names:
            result['errors'].append(f'Column names/order mismatch; expected={expected_names}; actual={actual_names}')
        for name, type_expr, nullable in expected:
            if name not in actual_names:
                continue
            field = schema.field(name)
            if field.type != _arrow_type(type_expr) or field.nullable != nullable:
                result['errors'].append(f'{name}: expected {_arrow_type(type_expr)}, nullable={nullable}; actual {field.type}, nullable={field.nullable}')
        metadata = schema.metadata or {}
        expected_meta = {
            b'table_name': table_name.encode(),
            b'primary_keys': json.dumps(cfg['primary_keys']).encode(),
            b'foreign_keys': json.dumps(cfg['foreign_keys']).encode(),
            b'address_columns': json.dumps(cfg['address_cols']).encode(),
        }
        for key, val in expected_meta.items():
            if metadata.get(key) != val:
                result['errors'].append(f'Missing/incorrect Parquet metadata: {key.decode()}')
        result['rows'] = pq.ParquetFile(path).metadata.num_rows
        if check_rows and not result['errors']:
            cols = list(dict.fromkeys(cfg['primary_keys'] + cfg['address_cols'] + (['snapshot_block'] if 'snapshot_block' in actual_names else [])))
            df = pd.read_parquet(path, columns=cols)
            if df.duplicated(subset=cfg['primary_keys']).any():
                result['errors'].append('Duplicate primary keys')
            for col in cfg['primary_keys']:
                if df[col].isna().any():
                    result['errors'].append(f'Null primary key: {col}')
            for col in cfg['address_cols']:
                values = df[col].astype('string')
                if not values.str.fullmatch(r'0x[0-9a-f]{40}').fillna(False).all():
                    result['errors'].append(f'Invalid/non-normalized EVM address: {col}')
            if 'snapshot_block' in df.columns:
                result['snapshots'] = sorted(int(x) for x in df['snapshot_block'].dropna().unique())
        if result['rows'] == 0:
            result['warnings'].append('Empty table: schema exists, but no research observations')
        result['status'] = 'invalid' if result['errors'] else ('empty' if result['rows'] == 0 else 'schema_valid')
    except Exception as exc:
        result['errors'].append(f'{type(exc).__name__}: {exc}')
        result['status'] = 'invalid'
    return result


def inspect_all(project_root: str | Path = '.', *, check_rows: bool = True) -> dict[str, dict]:
    return {name: inspect_table(name, project_root, check_rows=check_rows) for name in CONTRACT}


def load_table(table_name: str, project_root: str | Path = '.', *,
               snapshot_block: int | None = None, require_validated: bool = True,
               approved: bool = False) -> pd.DataFrame:
    """Read a table. `approved=True` must be set ONLY after external research sign-off.

    Schema validation does NOT imply scientific/experimental approval.
    """
    report = inspect_table(table_name, project_root)
    if report['status'] not in ('schema_valid', 'empty'):
        raise ValueError(f'Cannot load {table_name}: {report["errors"]}')
    if require_validated and not approved:
        raise PermissionError('Research approval not supplied; cannot label this data as validated')
    path = _safe_path(Path(project_root).resolve(), CONTRACT[table_name]['path'])
    df = pd.read_parquet(path)
    if snapshot_block is not None:
        if 'snapshot_block' not in df.columns:
            raise ValueError(f'{table_name} has no snapshot_block')
        df = df.loc[df['snapshot_block'] == snapshot_block].copy()
    return df


def inspect_file_manifest(manifest_path: str | Path, project_root: str | Path = '.', *,
                          verify_hashes: bool = True) -> dict:
    """Check the N3 file inventory manifest (files[].file, bytes, sha256).

    This is NOT an experiment-approval/run manifest.
    """
    root = Path(project_root).resolve()
    with open(manifest_path, encoding='utf-8') as f:
        manifest = json.load(f)
    reports = []
    for item in manifest.get('files', []):
        rel = item['file']
        path = _safe_path(root, rel)
        report = {'file': rel, 'exists': path.is_file(), 'errors': []}
        if report['exists']:
            if 'bytes' in item and path.stat().st_size != item['bytes']:
                report['errors'].append('Size mismatch')
            if verify_hashes and item.get('sha256'):
                digest = hashlib.sha256()
                with path.open('rb') as f:
                    for chunk in iter(lambda: f.read(1024 * 1024), b''):
                        digest.update(chunk)
                if digest.hexdigest().lower() != item['sha256'].lower():
                    report['errors'].append('SHA256 mismatch')
        else:
            report['errors'].append('Missing file')
        reports.append(report)
    return {'files': reports, 'missing_optional_or_expected': manifest.get('missing_optional_or_expected', []),
            'ok': all(not x['errors'] for x in reports)}


if __name__ == '__main__':
    for name, report in inspect_all().items():
        print(f'{name}: {report["status"]} | rows={report["rows"]} | {report["errors"]}')
