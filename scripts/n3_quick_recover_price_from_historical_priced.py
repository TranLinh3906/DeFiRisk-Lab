from pathlib import Path
import json, math
import pandas as pd

ROOT = Path.cwd()
BASE = ROOT / 'data/n3_history_2024_2026/liquidations'
DST = ROOT / 'outputs/n3_b1_recovered_priced_quotes_20261025'
PRICE_OUT = DST / 'market_reference_from_historical_priced.parquet'
EVENT_OUT = ROOT / 'outputs/n3_b1_diagnostic_v3/input_liquidations_B1_B2_etherscan.parquet'
REGISTRY = ROOT / 'data/processed/aave/token_registry.csv'

if PRICE_OUT.exists():
    raise SystemExit(f'[STOP] Output already exists: {PRICE_OUT}')
if not EVENT_OUT.exists():
    raise SystemExit(f'[STOP] Missing joined B1/B2 event file: {EVENT_OUT}')
if not REGISTRY.exists():
    raise SystemExit(f'[STOP] Missing asset registry: {REGISTRY}')

events = pd.read_parquet(EVENT_OUT)
events['tx_hash'] = events['tx_hash'].astype(str).str.lower()
events['log_index'] = pd.to_numeric(events['log_index'],errors='raise').astype('int64')
events['event_timestamp'] = pd.to_datetime(events.event_timestamp,utc=True,errors='raise')
if events.duplicated(['tx_hash','log_index']).any():
    raise SystemExit('[STOP] Duplicated Etherscan event keys')
events = events.set_index(['tx_hash','log_index'])
registry = pd.read_csv(REGISTRY)
if not {'token_address','symbol'} <= set(registry.columns):
    raise SystemExit('[STOP] Registry needs token_address and symbol')
registry['token_address'] = registry.token_address.astype(str).str.lower().str.strip()
registry['symbol'] = registry.symbol.astype(str).str.upper().str.strip()
registry = registry.loc[registry.token_address.str.fullmatch(r'0x[0-9a-f]{40}')]
ambiguous_addr = set(registry.groupby('token_address').symbol.nunique().loc[lambda x:x>1].index)
reg = registry[~registry.token_address.isin(ambiguous_addr)].drop_duplicates('token_address').set_index('token_address').symbol.to_dict()

quotes=[]
counts={}
for sid, folder in [('B1','crash_2024_08'),('B2','crash_2025_10')]:
    priced_path = BASE / folder / 'data/staging/n3_liquidations_priced.parquet'
    if not priced_path.exists():
        raise SystemExit(f'[STOP] Missing priced events: {priced_path}')
    df=pd.read_parquet(priced_path)
    req=['tx_hash','log_index','event_timestamp',
         'debt_token_address','collateral_token_address',
         'debt_price_usd','collateral_price_usd',
         'debt_price_timestamp','collateral_price_timestamp']
    missing=set(req)-set(df.columns)
    if missing:
        raise SystemExit(f'[STOP] Missing fields in {priced_path}: {sorted(missing)}')
    df['tx_hash']=df.tx_hash.astype(str).str.lower()
    df['log_index']=pd.to_numeric(df.log_index,errors='raise').astype('int64')
    if df.duplicated(['tx_hash','log_index']).any():
        raise SystemExit(f'[STOP] Duplicate priced keys: {sid}')
    df['event_timestamp']=pd.to_datetime(df.event_timestamp,utc=True,errors='raise')
    df=df.set_index(['tx_hash','log_index'])
    extra=df.index.difference(events.index)
    if len(extra):
        raise SystemExit(f'[STOP] {sid}: {len(extra)} priced event keys absent from raw combined')
    src=events.loc[df.index]
    if (df.event_timestamp!=src.event_timestamp).any():
        raise SystemExit(f'[STOP] {sid}: priced/raw event times disagree')
    for side in ('debt','collateral'):
        c=f'{side}_token_address'
        if (df[c].astype(str).str.lower().values != src[c].astype(str).str.lower().values).any():
            raise SystemExit(f'[STOP] {sid}: priced/raw token address disagree on {side}')
    valid_side={}
    for side in ('debt','collateral'):
        for _,r in df.reset_index().iterrows():
            obs=pd.to_datetime(r[f'{side}_price_timestamp'],utc=True,errors='coerce')
            px=pd.to_numeric(r[f'{side}_price_usd'],errors='coerce')
            ev=r['event_timestamp']
            addr=str(r[f'{side}_token_address']).lower()
            symbol=reg.get(addr)
            if not symbol or pd.isna(obs) or pd.isna(px) or not math.isfinite(float(px)) or float(px)<=0:
                continue
            lag=(ev-obs).total_seconds()
            if not (0<=lag<=3600):
                continue
            # Never import a price explicitly labelled as oracle/snapshot.
            origin=' '.join(str(r.get(n,'')) for n in (f'{side}_price_source','price_source','source'))
            if 'oracle' in origin.lower() or 'snapshot' in origin.lower():
                continue
            quotes.append({'timestamp':obs,'reference_timestamp':obs,'asset':symbol,
                           'quote_asset':'USD','price_value':float(px),
                           'price_type':'market_reference',
                           'origin':'historical_liquidation_priced',
                           'origin_snapshot':sid,
                           'token_address':addr})
    counts[sid]={'priced_event_rows':len(df)}

if not quotes:
    print('[NO VALID QUOTES] Historical priced files have no timestamped prior market observations within 60 minutes. Do not claim 85%.')
    raise SystemExit(2)
q=pd.DataFrame(quotes)
# Drop duplicates; reject conflicting prices for same address/instant rather than averaging silently.
groups=q.groupby(['token_address','timestamp'],dropna=False).price_value
conflict=groups.transform(lambda x: (x.max()-x.min())/x.median() > .001)
rejected=int(conflict.sum())
q=q.loc[~conflict].drop_duplicates(['token_address','timestamp']).copy()
# A symbol used by multiple token addresses cannot be safely merged by symbol into descriptive.py.
asset_counts=q.groupby('asset').token_address.nunique()
unsafe=set(asset_counts[asset_counts.gt(1)].index)
if unsafe:
    print('[REVIEW] Multiple addresses per symbol, omitted:', sorted(unsafe))
    q=q.loc[~q.asset.isin(unsafe)]
if q.empty:
    raise SystemExit('[STOP] All quotes ambiguous/conflicting')
DST.mkdir(parents=True,exist_ok=True)
q.to_parquet(PRICE_OUT,index=False)
print('[CREATED]',PRICE_OUT)
print('[VALID HISTORICAL QUOTES]',len(q),'[CONFLICT REJECTED]',rejected)
print('[BY TOKEN]',q.groupby('asset').size().to_dict())
print('[NOTE] Only timestamped prices <= event time, max 60min, not snapshot/oracle; provider precision still requires review')
print('NEXT: python src/analysis/descriptive.py --events outputs/n3_b1_diagnostic_v3/input_liquidations_B1_B2_etherscan.parquet --prices outputs/n3_b1_recovered_priced_quotes_20261025/market_reference_from_historical_priced.parquet --registry data/processed/aave/token_registry.csv --snapshot-metadata data/processed/aave/snapshot_metadata.json --audit-holdouts --audit-only --max-price-lag-min 60 --coverage-target-pct 85 --audit-output-root outputs/n3_b1_diagnostic_v4')
