# Tinh toan Coverage Log % cho N3
    total_input = len(df)
    fact_ready_count = len(fact)
    coverage_pct = round((fact_ready_count / total_input * 100.0), 2) if total_input > 0 else 0.0

    quality = {
        'status': 'fact-ready subset; verified event coverage log',
        'created_utc': datetime.now(timezone.utc).isoformat(),
        'source_price_type': 'DefiLlama historical market reference; NOT Aave oracle',
        'event_coverage_pct': coverage_pct,
        'event_coverage_log_status': 'PASS' if coverage_pct >= 85.0 else 'WARNING_LOW_COVERAGE',
        'total_events_in_input': total_input,
        'fact_ready_events': fact_ready_count,
        'fact_ready_pct': coverage_pct,
        'events_excluded': total_input - fact_ready_count,
        'reasons': {str(k): int(v) for k, v in df['n3_validation_status'].value_counts().items()},
        'unique_event_pk': not bool(df.duplicated(['tx_hash', 'log_index']).any()),
        'min_time_utc': str(df['event_timestamp'].min()) if len(df) else None,
        'max_time_utc': str(df['event_timestamp'].max()) if len(df) else None,
        'total_debt_repaid_usd_priced_subset': float(fact['debt_repaid_usd'].sum()),
        'total_collateral_seized_usd_priced_subset': float(fact['collateral_seized_usd'].sum()),
    }