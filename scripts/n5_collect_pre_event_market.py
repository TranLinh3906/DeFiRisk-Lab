"""Read-only public market collection: separate pre-event windows and 2022 crisis."""
import json
from pathlib import Path
import time
import pandas as pd
import requests


def main():
    out = Path('outputs/n5_hypothesis_inputs_20261026')
    out.mkdir(exist_ok=True)
    cfg = json.loads(Path('config/snapshots.json').read_text(encoding='utf-8'))['snapshots']
    windows = {s: pd.date_range(pd.Timestamp(cfg[s]['block_timestamp_utc']).normalize() - pd.Timedelta(days=365),
                                pd.Timestamp(cfg[s]['block_timestamp_utc']).normalize(), freq='D') for s in ['B1', 'B2']}
    windows['crisis_2022'] = pd.date_range('2022-05-01', '2022-07-31', tz='UTC', freq='D')
    coins = {'ETH': 'coingecko:ethereum', 'STETH': 'ethereum:0xae7ab96520de3a18e5e111b5eaab095312d7fe84'}
    for label, days in windows.items():
        folder = out / label
        folder.mkdir(exist_ok=True)
        observations = []
        for start in range(0, len(days), 40):
            path = folder / f'raw_{start:03}.json'
            if path.exists():
                payload = json.loads(path.read_text(encoding='utf-8'))
            else:
                timestamps = [int((d + pd.Timedelta(hours=23, minutes=45)).timestamp()) for d in days[start:start+40]]
                payload = None
                for host in ['https://coins.llama.fi', 'https://api.llama.fi']:
                    for attempt in range(3):
                        try:
                            response = requests.get(host + '/batchHistorical', params={
                                'coins': json.dumps({coin: timestamps for coin in coins.values()}), 'searchWidth': '30m'}, timeout=30)
                            if response.status_code == 200 and isinstance(response.json().get('coins'), dict):
                                payload = response.json()
                                break
                        except requests.RequestException:
                            pass
                        time.sleep(1 + attempt)
                    if payload is not None:
                        break
                if payload is None:
                    raise RuntimeError(f'Public history unavailable: {label} batch {start}')
                path.write_text(json.dumps(payload), encoding='utf-8')
            for asset, coin in coins.items():
                for row in payload['coins'].get(coin, {}).get('prices', []):
                    observations.append(dict(asset=asset, price_usd=float(row['price']),
                        quote_utc=pd.Timestamp(row['timestamp'], unit='s', tz='UTC'), source='DefiLlama_batchHistorical'))
            print(label, 'batch', start, flush=True)
            time.sleep(.2)
        quotes = pd.DataFrame(observations).drop_duplicates()
        if quotes.duplicated(['asset', 'quote_utc']).any():
            raise ValueError('Conflicting quote')
        rows, missing = [], []
        for day in days:
            end = day + pd.Timedelta(days=1) - pd.Timedelta(seconds=1)
            row = dict(date=day)
            for asset in coins:
                g = quotes[quotes.asset.eq(asset) & quotes.quote_utc.le(end) & quotes.quote_utc.ge(end - pd.Timedelta(hours=1))]
                if g.empty:
                    if label != 'crisis_2022':
                        raise ValueError(f'Missing fresh past quote: {label} {day} {asset}')
                    missing.append(dict(date=str(day), asset=asset, reason='no_past_quote_within_one_hour'))
                    continue
                value = g.sort_values('quote_utc').iloc[-1]
                row[asset.lower() + '_usd'] = float(value.price_usd)
                row[asset.lower() + '_quote_utc'] = value.quote_utc
            if 'steth_usd' not in row or 'eth_usd' not in row:
                continue  # Recorded gaps; downstream must not bridge missing dates.
            row['steth_eth'] = row['steth_usd'] / row['eth_usd']
            rows.append(row)
        levels = pd.DataFrame(rows)
        if (levels[['eth_usd', 'steth_usd', 'steth_eth']] <= 0).any().any():
            raise ValueError('Nonpositive market price')
        quotes.to_parquet(folder / 'observed_quotes.parquet', index=False)
        levels.to_parquet(folder / 'levels.parquet', index=False)
        (folder / 'coverage.json').write_text(json.dumps(dict(expected_days=len(days), observed_days=len(levels),
            missing=missing, policy='no_imputation_no_returns_across_calendar_gaps'), indent=2), encoding='utf-8')
        print(label, 'levels', len(levels), flush=True)


if __name__ == '__main__':
    main()
