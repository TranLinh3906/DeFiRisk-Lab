"""Offline snapshot identity and event-window QA; missing data is never relabeled."""
from datetime import datetime, timedelta
import math
import pandas as pd


def utc_timestamp(value):
    ts = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if ts.tzinfo is None or ts.utcoffset() != timedelta(0):
        raise ValueError('Snapshot timestamp must explicitly be UTC')
    return ts


def audit_snapshots(config, metadata, tables):
    reports = {}
    configured = config['snapshots']
    for label, snap in configured.items():
        block = snap.get('block')
        errors, missing = [], []
        if isinstance(block, bool) or not isinstance(block, int) or block <= 0:
            errors.append('Invalid configured block')
        try:
            start = utc_timestamp(snap['block_timestamp_utc'])
            if snap.get('window_end_exclusive_utc'):
                end = utc_timestamp(snap['window_end_exclusive_utc'])
                if end <= start:
                    errors.append('Event window must end after snapshot')
        except (ValueError, KeyError, TypeError):
            errors.append('Invalid/missing UTC window')
        info = metadata.get('snapshots', {}).get(label)
        if info is None:
            missing.append('snapshot_metadata')
        else:
            if info.get('snapshot_block') != block:
                errors.append('Metadata block differs from config')
            if str(info.get('block_hash', '')).lower() != str(snap.get('block_hash', '')).lower():
                errors.append('Metadata hash differs from config')
            try:
                if utc_timestamp(info['snapshot_timestamp_utc']) != utc_timestamp(snap['block_timestamp_utc']):
                    errors.append('Metadata timestamp differs from config')
            except (ValueError, KeyError, TypeError):
                errors.append('Invalid metadata timestamp')
        counts = {}
        for name, frame in tables.items():
            if frame is None or frame.empty:
                missing.append(name); counts[name] = 0; continue
            if not {'snapshot_id', 'snapshot_block'}.issubset(frame.columns):
                errors.append(f'{name}: missing label/block columns'); continue
            selected = frame[frame.snapshot_id.eq(label)]
            counts[name] = len(selected)
            if selected.empty:
                missing.append(name)
            elif not selected.snapshot_block.eq(block).all():
                errors.append(f'{name}: label is attached to wrong block')
        reports[label] = dict(snapshot_block=block, status='failed' if errors else 'missing_data' if missing else 'passed_structure',
            missing=missing, errors=errors, rows=counts, research_acceptance='pending')
    stale = {name: sorted(set(frame.snapshot_id.dropna()) - set(configured))
             for name, frame in tables.items() if frame is not None and 'snapshot_id' in frame}
    return dict(status='passed_structure' if all(r['status'] == 'passed_structure' for r in reports.values()) else 'blocked',
                snapshots=reports, stale_labels=stale,
                limitation='Config identity only; no independent chain/hash verification or data relabeling')


def select_event_windows(events, config):
    keys = ['tx_hash', 'log_index']
    if events.duplicated(keys).any():
        raise ValueError('Duplicate source liquidation events')
    timestamps = pd.to_datetime(events.event_timestamp)
    if timestamps.dt.tz is None:
        raise ValueError('Event timestamps need explicit timezone')
    selected, reports = [], {}
    for label, snap in config['snapshots'].items():
        if not snap.get('window_end_exclusive_utc'):
            continue
        start = utc_timestamp(snap['block_timestamp_utc'])
        end = utc_timestamp(snap['window_end_exclusive_utc'])
        if end <= start:
            raise ValueError('Invalid event window')
        mask = (events.event_block.gt(snap['block']) & timestamps.gt(start) & timestamps.lt(end)
                & events.protocol_id.eq('aave_v3_ethereum'))
        part = events.loc[mask].copy()
        part['validation_snapshot_id'] = label  # Relation to window, not a rewritten source identity.
        part['validation_snapshot_block'] = snap['block']
        part['first_hour'] = timestamps.loc[mask].lt(start + timedelta(hours=1))
        reports[label] = dict(events=len(part), unique_wallets=part.wallet_address.nunique(),
            first_hour_wallets=part.loc[part.first_hour, 'wallet_address'].nunique(),
            start_exclusive_utc=start.isoformat(), end_exclusive_utc=end.isoformat(),
            duration_hours=(end-start).total_seconds()/3600, hit_rate=None,
            status='observations_only', source_completeness='unverified')
        selected.append(part)
    return (pd.concat(selected, ignore_index=True) if selected else events.iloc[:0].copy()), reports
