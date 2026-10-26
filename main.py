from pathlib import Path
import base64
from html import escape
import numpy as np
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

st.set_page_config(page_title='DeFiRisk Lab | Dashboard', page_icon='📊', layout='wide')

st.markdown("""<style>
:root{--sidebar-width:21rem}
[data-testid="stSidebar"]{width:var(--sidebar-width)!important;min-width:var(--sidebar-width)!important;background:#0b1931!important;border-right:1px solid #244567!important}
[data-testid="stSidebar"] > div,[data-testid="stSidebar"] [data-testid="stSidebarContent"]{background:linear-gradient(158deg,#0a1830 0%,#142e50 52%,#0b1c35 100%)!important}
[data-testid="stSidebar"] [data-testid="stSidebarUserContent"]{padding-top:0!important}
/* Brand is sticky inside the SAME sidebar scroll flow: it cannot cover Overview. */
[data-testid="stSidebar"] .st-key-brand_fixed{
  position:sticky!important;
  top:0!important;
  left:auto!important;
  width:100%!important;
  height:auto!important;
  z-index:100!important;
  background:linear-gradient(160deg,#0a1830,#142b4c)!important;
  padding:12px 12px 16px!important;
  box-sizing:border-box!important;
  border-bottom:1px solid #2b5272!important;
}
[data-testid="stSidebar"] .st-key-brand_fixed > div{background:transparent!important}
.brand-logo{width:168px;height:168px;object-fit:contain;display:block;margin:0 auto 6px;border-radius:50%;filter:drop-shadow(0 0 22px #37bfff55)}
.brand-label{text-align:center;font-size:1.45rem;font-weight:800;color:#f5faff;margin-top:4px}
.brand-sub{text-align:center;color:#a9c7e9;font-size:.82rem;margin:5px 0 0}
/* No spacer: brand occupies its natural height, menu follows immediately. */
.nav-spacer{height:0!important;display:none!important}
.nav-heading{font-size:.76rem;letter-spacing:.17em;color:#9abce1;font-weight:800;margin:0 0 10px 8px}
[data-testid="stSidebar"] .stButton>button{width:100%;min-height:72px;justify-content:flex-start;text-align:left;border-radius:15px;padding:13px 18px;font-size:1.12rem;font-weight:750;color:#fff!important;border:1px solid #ffffff22;box-shadow:0 6px 16px #050e2166;transition:filter .2s,transform .2s,box-shadow .2s;position:relative;overflow:hidden}
[data-testid="stSidebar"] .stButton>button:before{content:'';position:absolute;inset:0;background:linear-gradient(115deg,#ffffff24,transparent 60%);pointer-events:none}
[data-testid="stSidebar"] .stButton>button p{font-size:1.12rem!important;font-weight:750!important;color:white!important}
[data-testid="stSidebar"] .stButton>button:hover{filter:brightness(1.12);transform:translateX(4px);box-shadow:0 8px 22px #0008}
[data-testid="stSidebar"] .stButton>button[kind="primary"]{outline:2px solid #7be5ff;outline-offset:2px;box-shadow:0 0 0 4px #6fd9ff2a,0 8px 22px #0008}
[data-testid="stSidebar"] .st-key-nav_0 button{background:#1266D6!important}
[data-testid="stSidebar"] .st-key-nav_1 button{background:#008E83!important}
[data-testid="stSidebar"] .st-key-nav_2 button{background:#7048D4!important}
[data-testid="stSidebar"] .st-key-nav_3 button{background:#C47A14!important}
[data-testid="stSidebar"] .st-key-nav_4 button{background:#C3445B!important}
[data-testid="stSidebar"] .st-key-nav_5 button{background:#476682!important}
[data-testid="stSidebar"] hr{border-color:#315071}
[data-testid="stSidebar"] [data-testid="stCaptionContainer"] p{color:#a9c5e5!important}
.status-panel{background:#183555;border:1px solid #355979;border-radius:14px;padding:13px 14px;margin:12px 0 8px;color:#e7f3ff}
.status-title{font-weight:750;font-size:.95rem;margin-bottom:5px}
.status-desc{font-size:.76rem;color:#b5cce4;line-height:1.5}
.status-pill{display:inline-block;background:#124d4a;color:#79f1d1;border-radius:20px;font-size:.66rem;padding:3px 8px;margin-left:5px}
.block-container{padding-top:1.5rem;max-width:1450px}
[data-testid="stMetric"]{background:#151e30;border:1px solid #2a3952;padding:16px;border-radius:12px}
[data-testid="stMetricLabel"] p{color:#b8c8e1!important}
[data-testid="stMetricValue"] div{color:#f8fbff!important}
h1,h2,h3{letter-spacing:-.025em}
.demo{padding:10px 14px;border-radius:9px;background:#493c19;color:#ffe5a2;border:1px solid #796028;margin-bottom:18px}
.page-banner{width:100%;border-radius:14px;padding:21px 25px;margin:4px 0 18px;box-shadow:0 7px 18px #132a441a}
.page-banner-title{color:white!important;font-size:2rem;font-weight:800;line-height:1.2;letter-spacing:-.025em}
.page-banner-sub{color:#fff!important;opacity:.9;font-size:.97rem;margin-top:7px}

/* Aurora Risk Intelligence data explorer */
.risk-shell{background:linear-gradient(150deg,#0b1930,#112843 75%,#0b1b32);border:1px solid #29476b;border-radius:20px;padding:21px;margin:16px 0;box-shadow:0 18px 38px #0a1c3433;color:#eaf5ff}
.risk-head{display:flex;align-items:center;justify-content:space-between;gap:12px;flex-wrap:wrap;margin-bottom:18px}
.risk-title{font-size:1.38rem;font-weight:800;color:#f7fbff}.risk-kicker{font-size:.73rem;color:#90b2d5;letter-spacing:.1em;margin-top:3px}
.risk-tag{background:#204566;border:1px solid #366b8d;border-radius:30px;padding:6px 12px;color:#9fe7fa;font-size:.74rem;font-weight:700}
.risk-kpis{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:12px;margin-bottom:17px}
.risk-kpi{background:#192d48;border:1px solid #2c4562;border-radius:13px;padding:14px;min-width:0}
.risk-number{font-size:1.65rem;font-weight:800;font-variant-numeric:tabular-nums}.risk-label{font-size:.69rem;color:#a0b9d7;letter-spacing:.06em;margin-top:5px}
.risk-composition{background:#152a45;border-radius:13px;padding:14px 16px;margin-bottom:18px;border:1px solid #2c4562}
.risk-comp-title{font-size:.82rem;font-weight:700;margin-bottom:10px;color:#dbeafd}.risk-track{height:11px;display:flex;border-radius:50px;overflow:hidden;background:#334761}
.risk-legend{display:flex;gap:15px;flex-wrap:wrap;margin-top:10px;font-size:.75rem;color:#c0d3e9}
.risk-table-wrap{overflow-x:auto;border-radius:12px;border:1px solid #2d4866;background:#102139}
.risk-table{border-collapse:collapse;width:100%;min-width:800px;color:#e8f3ff;font-size:.82rem}
.risk-table th{background:#1d3654;color:#a5c5e7;letter-spacing:.06em;text-transform:uppercase;font-size:.67rem;text-align:left;padding:14px 12px;white-space:nowrap}
.risk-table td{padding:13px 12px;border-top:1px solid #2a415d;white-space:nowrap;font-variant-numeric:tabular-nums}
.risk-table tbody tr:nth-child(even){background:#152a44}.risk-table tbody tr:hover{background:#204063}
.risk-wallet{color:#70d4ff;font-weight:700}.risk-muted{color:#91aac6;font-size:.72rem;margin-top:4px}
.risk-hf-track{height:5px;background:#354c66;border-radius:20px;overflow:hidden;margin-top:6px;width:88px}
.risk-pill{padding:5px 9px;border-radius:20px;font-weight:800;font-size:.68rem;display:inline-block}
.risk-footer{color:#8da8c9;font-size:.76rem;margin-top:12px}
.risk-shell .risk-empty{padding:25px;text-align:center;color:#b9cbe0}
@media(max-width:760px){.risk-kpis{grid-template-columns:1fr}.risk-number{font-size:1.4rem}.risk-shell{padding:12px}}

/* Reusable Aurora tables across every dashboard page */
.aurora-table-shell{background:linear-gradient(145deg,#0b1930,#142a47);border:1px solid #2a486a;border-radius:19px;padding:20px;margin:16px 0 22px;box-shadow:0 15px 35px #10243a30;color:#edf6ff}
.aurora-table-title{font-size:1.25rem;font-weight:800;color:#f5faff;margin-bottom:5px}
.aurora-table-sub{font-size:.77rem;color:#a2bddc;margin-bottom:17px}
.aurora-scroll{overflow:auto;border:1px solid #36516d;border-radius:12px;max-height:530px}
.aurora-data{border-collapse:collapse;min-width:720px;width:100%;font-size:.84rem;color:#eaf5ff;font-variant-numeric:tabular-nums}
.aurora-data th{position:sticky;top:0;background:#213c5b;color:#b9d5ef;padding:14px 13px;text-align:left;font-size:.72rem;text-transform:uppercase;letter-spacing:.06em;white-space:nowrap}
.aurora-data td{padding:13px;border-top:1px solid #294360;white-space:nowrap}
.aurora-data tbody tr:nth-child(even){background:#152b47}.aurora-data tbody tr:hover{background:#234461}
.aurora-table-footer{font-size:.74rem;color:#a1b8d4;margin-top:12px}
</style>""",unsafe_allow_html=True)

@st.cache_data
def mock_data():
    rng = np.random.default_rng(42)
    n = 360
    wallets = [f'0x{i:040x}' for i in range(1, n + 1)]
    debt = rng.lognormal(10.8, 1.15, n)
    hf = np.clip(rng.normal(1.44, .38, n), .65, 3.1)
    collateral = debt * hf / .81
    borrowers = pd.DataFrame({'wallet': wallets, 'debt_usd': debt, 'collateral_usd': collateral,
                              'health_factor': hf, 'emode': rng.choice(['E-Mode', 'Non E-Mode'], n, p=[.38,.62]),
                              'leverage': rng.choice(['Looping', 'Non-looping'], n, p=[.28,.72]),
                              'main_collateral': rng.choice(['ETH','BTC','stETH','Stablecoins'], n, p=[.48,.18,.22,.12])})
    dates = pd.date_range('2026-07-01', periods=110)
    eth = 3100 * np.exp(np.cumsum(rng.normal(0,.025,len(dates))))
    btc = 87000 * np.exp(np.cumsum(rng.normal(0,.019,len(dates))))
    hist = pd.DataFrame({'date':dates, 'eth_price_usd':eth,'btc_price_usd':btc,
                         'debt_repaid_usd':rng.gamma(2, 45000,len(dates)),
                         'liquidation_count':rng.poisson(16,len(dates)),
                         'steth_eth_ratio':1+rng.normal(0,.002,len(dates))})
    return borrowers, hist

borrowers, history = mock_data()

def dollars(x):
    return f'${x/1e6:,.2f}M' if abs(x)>=1e6 else f'${x:,.0f}'

def stress(eth_shock,btc_shock,stable_shock,steth_depeg):
    factors = {'ETH':1+eth_shock/100,'BTC':1+btc_shock/100,
               'stETH':(1+eth_shock/100)*(1+steth_depeg/100),
               'Stablecoins':1+stable_shock/100}
    result = borrowers.copy()
    result['hf_after'] = result['health_factor']*result['main_collateral'].map(factors)
    result['at_risk'] = result['hf_after']<1
    result['debt_at_risk_usd'] = np.where(result['at_risk'],result['debt_usd'],0.)
    return result

def metric_row(data):
    a,b,c,d=st.columns(4)
    a.metric('Borrower positions',f'{len(data):,}')
    b.metric('Total debt',dollars(data.debt_usd.sum()))
    c.metric('Positions at risk',f'{int(data.at_risk.sum()):,}')
    d.metric('Debt at risk',dollars(data.debt_at_risk_usd.sum()))


# UI-only risk thresholds. Replace these with the team's validated KPI Dictionary.
# HF < 1 is the Aave liquidation eligibility threshold; sub-bands are illustrative.
def risk_level(hf):
    if hf < 0.8:
        return 'Critical', '#ff7794', '#55283c'
    if hf < 1.0:
        return 'High', '#ffbd65', '#57402b'
    if hf < 1.2:
        return 'Watch', '#7dbdff', '#203e61'
    return 'Healthy', '#67dfbd', '#184a47'


def compact_money(value):
    value = float(value)
    if abs(value) >= 1e6:
        return f'${value/1e6:,.2f}M'
    if abs(value) >= 1e3:
        return f'${value/1e3:,.1f}K'
    return f'${value:,.0f}'



def render_aurora_table(df, title, key, accent='#1266D6', subtitle='Synthetic data · UI prototype', formats=None):
    """Reusable styled table for all non-wallet datasets, preserving full data for export."""
    df = df.copy().reset_index(drop=True)
    formats = formats or {}
    if df.empty:
        st.info('No records match this selection.')
        return
    left, right = st.columns([2, 1])
    query = left.text_input('Search table', key=f'{key}_query', placeholder='Search any column...')
    page_size = right.selectbox('Rows per page', [10, 20, 50], index=1, key=f'{key}_size')
    if query.strip():
        mask = df.astype(str).apply(lambda c: c.str.contains(query.strip(), case=False, regex=False, na=False)).any(axis=1)
        filtered = df[mask]
    else:
        filtered = df
    count = len(filtered)
    pages = max(1, (count + page_size - 1) // page_size)
    page = st.number_input('Page', min_value=1, max_value=pages, value=1, key=f'{key}_page')
    shown = filtered.iloc[(page-1)*page_size:page*page_size]
    def format_cell(col, val):
        if pd.isna(val): return '—'
        if col in formats:
            try: return formats[col](val)
            except (ValueError, TypeError): pass
        if isinstance(val, (pd.Timestamp, np.datetime64)):
            return pd.Timestamp(val).strftime('%Y-%m-%d')
        if isinstance(val, (float, np.floating)):
            return f'{val:,.3f}'
        if isinstance(val, (int, np.integer)):
            return f'{val:,}'
        return str(val)
    headers = ''.join(f'<th>{escape(str(col).replace("_", " "))}</th>' for col in shown.columns)
    body = ''
    for _, row in shown.iterrows():
        cells = ''.join(f'<td>{escape(format_cell(col, row[col]))}</td>' for col in shown.columns)
        body += f'<tr>{cells}</tr>'
    if not body: body = f'<tr><td colspan="{len(df.columns)}">No matching records</td></tr>'
    st.markdown(f"""<div class='aurora-table-shell' style='border-top:4px solid {accent}'>
      <div class='aurora-table-title'>✦ {escape(title)}</div>
      <div class='aurora-table-sub'>{escape(subtitle)}</div>
      <div class='aurora-scroll'><table class='aurora-data'><thead><tr>{headers}</tr></thead><tbody>{body}</tbody></table></div>
      <div class='aurora-table-footer'>Showing {len(shown):,} / {count:,} filtered rows · Page {page}/{pages} · MOCK DATA</div>
    </div>""", unsafe_allow_html=True)
    st.download_button('⬇ Export filtered CSV', filtered.to_csv(index=False).encode('utf-8-sig'),
                       file_name=f'defirisk_{key}_demo.csv', mime='text/csv', key=f'{key}_csv')

def render_risk_explorer(data, key='borrower', hf_col='health_factor', title='Borrower positions'):
    # Full-width dark HTML table + native Streamlit controls.
    df = data.copy()
    if hf_col not in df.columns or 'wallet' not in df.columns:
        st.error('Missing wallet / Health Factor columns for Risk Explorer.')
        return
    df['_risk'] = df[hf_col].apply(lambda v: risk_level(float(v))[0])
    st.markdown('#### ✦ Aurora Risk Intelligence Table')
    st.caption('UI prototype · Risk sub-bands are illustrative, not validated research classifications.')
    col1, col2, col3 = st.columns([1.7, 1, 1])
    search = col1.text_input('Search wallet', placeholder='0x... (full or partial)', key=f'{key}_search')
    selected_risk = col2.selectbox('Risk level', ['All', 'Critical', 'High', 'Watch', 'Healthy'], key=f'{key}_risk')
    sort_mode = col3.selectbox('Sort', ['Lowest HF', 'Highest debt', 'Highest HF'], key=f'{key}_sort')
    if search.strip():
        df = df[df['wallet'].str.contains(search.strip(), case=False, regex=False, na=False)]
    if selected_risk != 'All':
        df = df[df['_risk'] == selected_risk]
    if sort_mode == 'Highest debt':
        df = df.sort_values('debt_usd', ascending=False)
    else:
        df = df.sort_values(hf_col, ascending=(sort_mode == 'Lowest HF'))

    total = len(df)
    counts = df['_risk'].value_counts()
    critical = int(counts.get('Critical', 0))
    high = int(counts.get('High', 0))
    watch = int(counts.get('Watch', 0))
    healthy = int(counts.get('Healthy', 0))
    at_risk = critical + high
    debt_at_risk = df.loc[df[hf_col] < 1, 'debt_usd'].sum() if total else 0
    pct = lambda n: 100*n/total if total else 0
    composition = ''.join(
        f'<div style="width:{pct(n):.4f}%;background:{color}"></div>'
        for n, color in [(critical, '#f16b85'), (high, '#f5b451'), (watch, '#6bbaff'), (healthy, '#29bea7')]
    )
    accent = '#7048D4' if key == 'borrower' else '#C47A14'
    html = f'''<div class="risk-shell" style="border-top:4px solid {accent}">
      <div class="risk-head"><div><div class="risk-title">🛡 {escape(title)}</div>
      <div class="risk-kicker">BORROWER ANALYTICS / AAVE V3</div></div><span class="risk-tag">● MOCK DATA</span></div>
      <div class="risk-kpis">
        <div class="risk-kpi"><div class="risk-number" style="color:#70d9ff">{total:,}</div><div class="risk-label">FILTERED POSITIONS</div></div>
        <div class="risk-kpi"><div class="risk-number" style="color:#ff8ca1">{at_risk:,}</div><div class="risk-label">HF BELOW 1</div></div>
        <div class="risk-kpi"><div class="risk-number" style="color:#ffc37a">{compact_money(debt_at_risk)}</div><div class="risk-label">DEBT AT RISK (DEMO)</div></div>
      </div>
      <div class="risk-composition"><div class="risk-comp-title">Portfolio risk composition · Current filter</div>
        <div class="risk-track">{composition}</div>
        <div class="risk-legend"><span style="color:#ff8ba0">● Critical {pct(critical):.0f}%</span>
        <span style="color:#ffc37a">● High {pct(high):.0f}%</span>
        <span style="color:#8dc8ff">● Watch {pct(watch):.0f}%</span>
        <span style="color:#73e3ca">● Healthy {pct(healthy):.0f}%</span></div></div>
      <div class="risk-table-wrap"><table class="risk-table"><thead><tr>
        <th>Wallet</th><th>Debt</th><th>Collateral</th><th>Health factor</th><th>E-Mode</th><th>Strategy</th><th>Risk</th>
      </tr></thead><tbody>'''
    page_size = 15
    pages = max(1, (total + page_size - 1) // page_size)
    page_number = st.number_input('Table page', min_value=1, max_value=pages, value=1, step=1, key=f'{key}_page')
    current = df.iloc[(page_number - 1)*page_size:page_number*page_size]
    for _, row in current.iterrows():
        hf = float(row[hf_col]); label, fg, bg = risk_level(hf)
        wallet = escape(str(row['wallet']))
        short = escape(str(row['wallet'])[:6] + '…' + str(row['wallet'])[-4:])
        emode = escape(str(row.get('emode', '—')))
        leverage = escape(str(row.get('leverage', '—')))
        pct_hf = min(100, max(0, hf / 2 * 100))
        html += (f'<tr><td><span class="risk-wallet" title="{wallet}">{short}</span>'
                 f'<div class="risk-muted">{escape(str(row.get("main_collateral", "")))}</div></td>'
                 f'<td>{compact_money(row["debt_usd"])}</td><td>{compact_money(row["collateral_usd"])}</td>'
                 f'<td><strong style="color:{fg}">{hf:.3f}</strong><div class="risk-hf-track"><div style="height:100%;width:{pct_hf:.1f}%;background:{fg}"></div></div></td>'
                 f'<td>{emode}</td><td>{leverage}</td><td><span class="risk-pill" style="color:{fg};background:{bg}">{label}</span></td></tr>')
    if current.empty:
        html += '<tr><td colspan="7" class="risk-empty">No matching positions</td></tr>'
    html += (f'</tbody></table></div><div class="risk-footer">Showing {len(current)} of {total} filtered positions'
             f' · Page {page_number}/{pages} · Synthetic data only · HF risk bands for UI demonstration</div></div>')
    st.markdown(html, unsafe_allow_html=True)
    if not df.empty:
        csv = df.drop(columns=['_risk']).to_csv(index=False).encode('utf-8-sig')
        st.download_button('⬇ Export filtered rows (CSV)', csv, file_name=f'defirisk_{key}_demo.csv', mime='text/csv', key=f'{key}_export')
        wallet_options = df['wallet'].tolist()
        with st.expander('🔎 Wallet detail · full address & raw values'):
            wallet = st.selectbox('Select wallet', wallet_options, key=f'{key}_wallet')
            row = df.loc[df['wallet'] == wallet].iloc[0]
            st.code(wallet, language=None)
            c1,c2,c3=st.columns(3)
            c1.metric('Debt', compact_money(row['debt_usd']))
            c2.metric('Collateral', compact_money(row['collateral_usd']))
            c3.metric('Health Factor', f'{float(row[hf_col]):.4f}')
            st.write(f'E-Mode: **{row.get("emode", "—")}** · Strategy: **{row.get("leverage", "—")}** · Collateral: **{row.get("main_collateral", "—")}**')

LOGO_BASE64 = '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'

PAGES = ['01 · Overview','02 · Historical','03 · Borrower Risk','04 · Stress Simulator','05 · Cascade','06 · Methodology']
PAGE_ICONS = ['📊','🕒','👥','⚡','🔗','📖']
if 'selected_page' not in st.session_state:
    st.session_state.selected_page = PAGES[0]

with st.sidebar:
    # Embedded logo: no assets folder needed when copying main.py to PyCharm.
    with st.container(key='brand_fixed'):
        st.markdown(f'<img class="brand-logo" src="data:image/webp;base64,{LOGO_BASE64}" alt="DeFiRisk Lab logo">', unsafe_allow_html=True)
        st.markdown('<div class="brand-label">DeFiRisk Lab</div><div class="brand-sub">AAVE V3  /  RISK INTELLIGENCE</div>', unsafe_allow_html=True)
    st.markdown('<div class="nav-heading">WORKSPACE</div>', unsafe_allow_html=True)
    for idx, item in enumerate(PAGES):
        active = st.session_state.selected_page == item
        label = f'{PAGE_ICONS[idx]}   {item}'
        if st.button(label, key=f'nav_{idx}', type='primary' if active else 'secondary', width='stretch'):
            st.session_state.selected_page = item
            st.rerun()
    st.markdown('''<div class="status-panel"><div class="status-title">✦ System status <span class="status-pill">UI READY</span></div><div class="status-desc">Mock data · Awaiting model integration</div></div>''', unsafe_allow_html=True)
    st.divider()
    st.caption('◉  DEMO MODE  ·  MOCK DATA')
    st.caption('Người 6 · Dashboard Developer & Integrator')

page = st.session_state.selected_page

PAGE_COLORS = ['#1266D6','#008E83','#7048D4','#C47A14','#C3445B','#476682']
PAGE_SUBTITLES = [
    'Protocol health & risk summary',
    'Market prices & liquidation events',
    'Health Factor, borrower exposure & leverage',
    'Market shock scenarios & debt at risk',
    'Liquidation cascade & sensitivity analysis',
    'Research assumptions, formulas & limitations',
]
page_index = PAGES.index(page)
st.markdown(f'<div class="page-banner" style="background:{PAGE_COLORS[page_index]}"><div class="page-banner-title">{PAGE_ICONS[page_index]} {page.upper()}</div><div class="page-banner-sub">{PAGE_SUBTITLES[page_index]}</div></div>', unsafe_allow_html=True)
if page.startswith('01'):
    st.markdown('<div class="demo">⚠️ OVERVIEW: snapshot chưa nghiệm thu; không sử dụng mock fallback.</div>', unsafe_allow_html=True)
elif page.startswith('02'):
    st.markdown('<div class="demo">⚠️ HISTORICAL: Parquet chưa nghiệm thu; không sử dụng mock fallback.</div>', unsafe_allow_html=True)
elif page.startswith('03'):
    st.markdown('<div class="demo">⚠️ BORROWER RISK: dữ liệu snapshot nếu có, CHƯA NGHIỆM THU. Không sử dụng mock fallback.</div>', unsafe_allow_html=True)
elif page.startswith('04'):
    st.markdown('<div class="demo">⚠️ STRESS: kết quả Parquet chưa nghiệm thu; không sử dụng mô phỏng mock.</div>', unsafe_allow_html=True)
elif page.startswith('05'):
    st.markdown('<div class="demo">⚠️ CASCADE: kết quả Parquet chưa nghiệm thu; không sử dụng cascade giả lập.</div>', unsafe_allow_html=True)
else:
    st.markdown('<div class="demo">⚠️ METHODOLOGY DRAFT — Quy tắc mô hình và nguồn dữ liệu cần được nhóm xác nhận; không phải kết quả nghiên cứu đã nghiệm thu.</div>', unsafe_allow_html=True)

if page.startswith('01'):
    # Overview chỉ dùng snapshot thật; không fallback sang số liệu mock.
    snapshot_path = Path(__file__).resolve().parent / 'data' / 'processed' / 'fact_wallet_summary.parquet'
    if not snapshot_path.exists():
        # Hỗ trợ cấu trúc PyCharm hiện tại, file nằm cạnh main.py.
        snapshot_path = Path(__file__).resolve().parent / 'fact_wallet_summary.parquet'

    if not snapshot_path.exists():
        st.warning('Chưa tìm thấy fact_wallet_summary.parquet. Overview đang chờ dữ liệu thật từ nhóm.')
    else:
        try:
            overview_df = pd.read_parquet(snapshot_path)
            needed = {'snapshot_block', 'protocol_id', 'wallet_address',
                      'total_collateral_usd', 'total_debt_usd',
                      'hf_calculated_t0', 'dominant_collateral_symbol',
                      'is_recursive_loop', 'emode_category'}
            missing = needed - set(overview_df.columns)
            if missing:
                st.error('Snapshot thiếu các cột cần cho Overview: ' + ', '.join(sorted(missing)))
            elif overview_df.empty:
                st.info('Đã đọc fact_wallet_summary.parquet: 18 cột, 0 dòng. Chưa có dữ liệu ví để tính KPI hoặc vẽ biểu đồ.')
                st.caption('Đây là trạng thái chờ dữ liệu, không phải 0 borrowers hay 0 USD debt thực tế.')
            else:
                # Tránh cộng gộp nhiều snapshot thành một trạng thái thị trường.
                blocks = sorted(overview_df['snapshot_block'].dropna().unique().tolist())
                if not blocks:
                    st.warning('Không có snapshot_block hợp lệ.')
                    st.stop()
                selected_block = st.selectbox('Snapshot block', blocks, index=len(blocks)-1, key='overview_block')
                protocols = sorted(overview_df.loc[overview_df['snapshot_block'] == selected_block, 'protocol_id'].dropna().unique().tolist())
                selected_protocols = st.multiselect('Protocol', protocols, default=protocols, key='overview_protocol')
                view = overview_df[(overview_df['snapshot_block'] == selected_block) &
                                   (overview_df['protocol_id'].isin(selected_protocols))].copy()
                if view.empty:
                    st.info('Không có bản ghi cho bộ lọc đã chọn.')
                else:
                    debt = pd.to_numeric(view['total_debt_usd'], errors='coerce')
                    collateral = pd.to_numeric(view['total_collateral_usd'], errors='coerce')
                    hf = pd.to_numeric(view['hf_calculated_t0'], errors='coerce')
                    a, b, c, d = st.columns(4)
                    a.metric('Wallet-protocol records', f'{len(view):,}')
                    b.metric('Total debt (USD)', dollars(debt.sum()))
                    c.metric('HF < 1 records', f'{int((hf < 1).sum()):,}')
                    d.metric('Debt at HF < 1', dollars(debt[hf < 1].sum()))
                    left, right = st.columns([1.35, 1])
                    with left:
                        st.subheader('Health Factor distribution')
                        valid_hf = hf.replace([np.inf, -np.inf], np.nan).dropna()
                        if valid_hf.empty:
                            st.info('Không có Health Factor hợp lệ để vẽ.')
                        else:
                            fig = px.histogram(x=valid_hf, nbins=35,
                                               labels={'x': 'Health Factor'},
                                               color_discrete_sequence=['#58b5fa'])
                            fig.add_vline(x=1, line_dash='dash', line_color='#ff6b6b')
                            st.plotly_chart(fig, use_container_width=True)
                    with right:
                        st.subheader('Dominant collateral (wallet count)')
                        mix = view['dominant_collateral_symbol'].fillna('Unknown').value_counts().reset_index()
                        mix.columns = ['asset', 'records']
                        st.plotly_chart(px.pie(mix, names='asset', values='records', hole=.6),
                                        use_container_width=True)
                    st.caption('Nguồn: fact_wallet_summary.parquet · Snapshot chưa được xác nhận nghiệm thu. '
                               'Dominant collateral là phân loại theo ví, không phải tổng collateral exposure theo token.')
                    st.subheader('Borrower summary')
                    show_cols = ['protocol_id', 'wallet_address', 'total_collateral_usd',
                                 'total_debt_usd', 'hf_calculated_t0',
                                 'dominant_collateral_symbol', 'is_recursive_loop']
                    st.dataframe(view[show_cols], use_container_width=True, hide_index=True)
        except (OSError, ValueError, ImportError, KeyError) as exc:
            st.error(f'Không đọc được snapshot Overview: {exc}')

elif page.startswith('02'):
    # Historical: chỉ đọc dữ liệu Parquet, tuyệt đối không dùng history mock.
    historical_path = Path(__file__).resolve().parent / 'data' / 'processed' / 'fact_historical_liquidations.parquet'
    if not historical_path.exists():
        historical_path = Path(__file__).resolve().parent / 'fact_historical_liquidations.parquet'

    if not historical_path.exists():
        st.warning('Chưa tìm thấy fact_historical_liquidations.parquet. Historical đang chờ dữ liệu từ nhóm.')
    else:
        try:
            historical_df = pd.read_parquet(historical_path)
        except Exception as exc:
            st.error(f'Không đọc được file Historical: {exc}')
        else:
            st.caption('Nguồn: fact_historical_liquidations.parquet · Dữ liệu chưa được xác nhận nghiệm thu.')
            if historical_df.empty:
                st.info(f'Đã đọc fact_historical_liquidations.parquet: {len(historical_df.columns)} cột, 0 dòng. Chưa có sự kiện thanh lý để hiển thị.')
                st.caption('Bảng rỗng không có nghĩa là thị trường không xảy ra thanh lý.')
            else:
                st.success(f'Đã đọc {len(historical_df):,} bản ghi lịch sử từ Parquet (chưa nghiệm thu).')
                st.metric('Historical records loaded', f'{len(historical_df):,}')
                st.caption('Số bản ghi đọc được không nhất thiết bằng số sự kiện thanh lý duy nhất.')
                st.subheader('Historical liquidation records')
                st.dataframe(historical_df, use_container_width=True, hide_index=True)
                st.download_button('⬇ Export Historical CSV',
                                   historical_df.to_csv(index=False).encode('utf-8-sig'),
                                   file_name='defirisk_historical_unverified.csv',
                                   mime='text/csv', key='historical_export')

elif page.startswith('03'):
    # Borrower Risk sử dụng snapshot summary thực; không dùng borrowers từ mock_data().
    root = Path(__file__).resolve().parent
    borrower_path = root / 'data' / 'processed' / 'fact_wallet_summary.parquet'
    if not borrower_path.exists():
        borrower_path = root / 'fact_wallet_summary.parquet'
    if not borrower_path.exists():
        st.warning('Chưa tìm thấy fact_wallet_summary.parquet. Borrower Risk đang chờ dữ liệu từ nhóm.')
    else:
        try:
            borrower_df = pd.read_parquet(borrower_path)
            required = {'snapshot_block', 'protocol_id', 'wallet_address',
                        'total_collateral_usd', 'total_debt_usd', 'hf_calculated_t0',
                        'emode_category', 'is_recursive_loop', 'dominant_collateral_symbol'}
            missing = required - set(borrower_df.columns)
            if missing:
                st.error('Bảng Borrower Risk thiếu cột: ' + ', '.join(sorted(missing)))
            elif borrower_df.empty:
                st.info(f'Đã đọc fact_wallet_summary.parquet: {len(borrower_df.columns)} cột, 0 dòng. Chưa có dữ liệu ví để phân tích Borrower Risk.')
                st.caption('Bảng rỗng không có nghĩa thị trường không có borrower hoặc không có rủi ro thanh lý.')
            else:
                blocks = sorted(borrower_df['snapshot_block'].dropna().unique().tolist())
                if not blocks:
                    st.warning('Dữ liệu không có snapshot_block hợp lệ.')
                else:
                    block = st.selectbox('Snapshot block', blocks, index=len(blocks)-1, key='risk_block')
                    base = borrower_df.loc[borrower_df['snapshot_block'] == block].copy()
                    protocols = sorted(base['protocol_id'].dropna().astype(str).unique().tolist())
                    selected = st.multiselect('Protocol', protocols, default=protocols, key='risk_protocols')
                    view = base.loc[base['protocol_id'].astype(str).isin(selected)].copy()
                    if view.empty:
                        st.info('Không có bản ghi phù hợp bộ lọc.')
                    else:
                        hf = pd.to_numeric(view['hf_calculated_t0'], errors='coerce')
                        debt = pd.to_numeric(view['total_debt_usd'], errors='coerce')
                        collateral = pd.to_numeric(view['total_collateral_usd'], errors='coerce')
                        c1, c2, c3, c4 = st.columns(4)
                        c1.metric('Wallet-protocol records', f'{len(view):,}')
                        c2.metric('Total debt', dollars(debt.sum()))
                        c3.metric('HF < 1 records', f'{int((hf < 1).sum()):,}')
                        c4.metric('Debt with HF < 1', dollars(debt[hf < 1].sum()))
                        st.caption('HF < 1 là chỉ báo đủ điều kiện thanh lý theo mô hình, không đồng nghĩa sự kiện thanh lý đã xảy ra. Dữ liệu chưa nghiệm thu.')
                        plot_df = pd.DataFrame({'Collateral USD': collateral, 'Debt USD': debt,
                                                'Health Factor': hf, 'Protocol': view['protocol_id'].astype(str),
                                                'Wallet': view['wallet_address'].astype(str)}).replace([np.inf, -np.inf], np.nan)
                        plot_df = plot_df.dropna(subset=['Collateral USD', 'Debt USD'])
                        if not plot_df.empty:
                            st.plotly_chart(px.scatter(plot_df, x='Collateral USD', y='Debt USD', color='Protocol',
                                                       hover_data=['Wallet', 'Health Factor'],
                                                       title='Collateral vs Debt (snapshot)'), width='stretch')
                        else:
                            st.info('Không có số liệu collateral/debt hợp lệ để vẽ biểu đồ.')
                        st.subheader('Borrower snapshot records')
                        cols = ['protocol_id', 'wallet_address', 'total_collateral_usd',
                                'total_debt_usd', 'hf_calculated_t0', 'emode_category',
                                'is_recursive_loop', 'dominant_collateral_symbol']
                        st.dataframe(view[cols], width='stretch', hide_index=True)
                        st.download_button('⬇ Export filtered CSV', view[cols].to_csv(index=False).encode('utf-8-sig'),
                                           file_name='defirisk_borrower_snapshot_unverified.csv', mime='text/csv', key='risk_csv')
        except (OSError, ValueError, ImportError, KeyError) as exc:
            st.error(f'Không đọc được Borrower Risk snapshot: {exc}')

elif page.startswith('04'):
    st.subheader('Stress simulation results · precomputed runs')
    st.caption('Nguồn: out_simulation_results.parquet · Kết quả chưa nghiệm thu. Chưa hỗ trợ chạy model trực tiếp bằng slider.')
    sim_path = Path(__file__).resolve().parent / 'outputs' / 'tables' / 'out_simulation_results.parquet'
    if not sim_path.exists():
        sim_path = Path(__file__).resolve().parent / 'out_simulation_results.parquet'
    if not sim_path.exists():
        st.warning('Chưa tìm thấy out_simulation_results.parquet. Đặt file cạnh main.py hoặc tại outputs/tables/.')
    else:
        try:
            sim = pd.read_parquet(sim_path)
            required = {'run_id', 'snapshot_block', 'question_id', 'scenario_name', 'cf_rule',
                        'cascade_round', 'protocol_id', 'segment_group', 'round_liq_vol_usd',
                        'cum_liq_vol_usd', 'pos_bad_debt_usd'}
            missing = required - set(sim.columns)
            if missing:
                st.error('Thiếu cột theo Data Contract: ' + ', '.join(sorted(missing)))
            elif sim.empty:
                st.info(f'Đã đọc out_simulation_results.parquet: {len(sim.columns)} cột, 0 dòng. Chưa có kết quả mô phỏng để hiển thị.')
                st.caption('Bảng rỗng không có nghĩa là rủi ro bằng 0.')
            else:
                st.warning('Đây là kết quả mô phỏng có sẵn, chưa được nhóm xác nhận nghiệm thu. Không suy diễn thành kết quả stress mới từ slider.')
                filter_cols = [('run_id','Run'), ('snapshot_block','Snapshot block'),
                               ('question_id','Research question'), ('scenario_name','Scenario'),
                               ('cf_rule','Close factor'), ('protocol_id','Protocol'),
                               ('segment_group','Segment')]
                view = sim.copy()
                for col, label in filter_cols:
                    options = sorted(view[col].dropna().unique().tolist(), key=str)
                    if not options:
                        st.info(f'Không có giá trị {label} để lọc.')
                        break
                    selected = st.selectbox(label, options, key='stress_'+col)
                    view = view[view[col] == selected]
                if view.empty:
                    st.info('Không có kết quả phù hợp bộ lọc.')
                else:
                    # Mỗi hàng là một vòng; không cộng cột tích lũy giữa các vòng.
                    view = view.sort_values('cascade_round')
                    st.metric('Records in selection', f'{len(view):,}')
                    fig = px.line(view, x='cascade_round', y='cum_liq_vol_usd', markers=True,
                                  title='Cumulative liquidation volume by round (USD)')
                    st.plotly_chart(fig, use_container_width=True)
                    fig2 = px.bar(view, x='cascade_round', y='round_liq_vol_usd',
                                  title='Liquidation volume per round (USD)')
                    st.plotly_chart(fig2, use_container_width=True)
                    st.subheader('Simulation output rows')
                    st.dataframe(view, use_container_width=True, hide_index=True)
                    st.download_button('⬇ Export selected simulation rows',
                                       view.to_csv(index=False).encode('utf-8-sig'),
                                       file_name='defirisk_stress_unverified.csv', mime='text/csv',
                                       key='stress_export')
        except (OSError, ValueError, ImportError, KeyError) as exc:
            st.error(f'Không đọc được kết quả mô phỏng: {exc}')

elif page.startswith('05'):
    st.subheader('Cascade · precomputed simulation rounds')
    st.caption('Nguồn: out_simulation_results.parquet · Chưa nghiệm thu · Không chạy model trực tiếp từ slider.')
    cascade_path = Path(__file__).resolve().parent / 'outputs' / 'tables' / 'out_simulation_results.parquet'
    if not cascade_path.exists():
        cascade_path = Path(__file__).resolve().parent / 'out_simulation_results.parquet'
    if not cascade_path.exists():
        st.warning('Chưa tìm thấy out_simulation_results.parquet. Đặt file cạnh main.py hoặc trong outputs/tables/.')
    else:
        try:
            cascade = pd.read_parquet(cascade_path)
            required = {'run_id', 'snapshot_block', 'question_id', 'scenario_name', 'cf_rule',
                        'cascade_round', 'protocol_id', 'segment_group', 'round_liq_vol_usd',
                        'cum_liq_vol_usd', 'pos_bad_debt_usd'}
            missing = required - set(cascade.columns)
            if missing:
                st.error('Thiếu cột theo Data Contract: ' + ', '.join(sorted(missing)))
            elif cascade.empty:
                st.info(f'Đã đọc out_simulation_results.parquet: {len(cascade.columns)} cột, 0 dòng. Chưa có kết quả Cascade để hiển thị.')
                st.caption('Bảng rỗng không có nghĩa là không xảy ra cascade hay bad debt.')
            else:
                st.warning('Kết quả mô phỏng có sẵn, chưa được nghiệm thu. Không phải kết quả chạy mới từ giao diện.')
                view = cascade.copy()
                filters = [('run_id', 'Run ID'), ('snapshot_block', 'Snapshot block'),
                           ('question_id', 'Research question'), ('scenario_name', 'Scenario'),
                           ('cf_rule', 'Close factor rule'), ('protocol_id', 'Protocol'),
                           ('segment_group', 'Segment')]
                for col, label in filters:
                    options = sorted(view[col].dropna().unique().tolist(), key=str)
                    if not options:
                        st.info(f'Không có giá trị {label} để lọc.')
                        break
                    chosen = st.selectbox(label, options, key='cascade_filter_' + col)
                    view = view.loc[view[col] == chosen]
                if view.empty:
                    st.info('Không có kết quả phù hợp bộ lọc Cascade.')
                else:
                    view = view.sort_values('cascade_round')
                    rounds = view['cascade_round'].nunique()
                    a, b = st.columns(2)
                    a.metric('Cascade rounds in selection', f'{rounds:,}')
                    b.metric('Output records', f'{len(view):,}')
                    # Group within one selected run/scenario/protocol/segment, not across unrelated runs.
                    by_round = view.groupby('cascade_round', as_index=False).agg(
                        round_liq_vol_usd=('round_liq_vol_usd', 'sum'),
                        cum_liq_vol_usd=('cum_liq_vol_usd', 'max'),
                        pos_bad_debt_usd=('pos_bad_debt_usd', 'sum'))
                    st.plotly_chart(px.bar(by_round, x='cascade_round', y='round_liq_vol_usd',
                                           title='Liquidation volume per cascade round (USD)'),
                                    use_container_width=True)
                    st.plotly_chart(px.line(by_round, x='cascade_round', y='cum_liq_vol_usd',
                                            markers=True, title='Reported cumulative liquidation volume (USD)'),
                                    use_container_width=True)
                    st.plotly_chart(px.bar(by_round, x='cascade_round', y='pos_bad_debt_usd',
                                           title='Reported bad debt per round (USD)'),
                                    use_container_width=True)
                    st.caption('Giá trị tích lũy lấy theo max trong mỗi vòng, không cộng dồn các bản ghi cumulative. Bad debt chỉ hiển thị theo giá trị được lưu, không tự suy diễn.')
                    st.subheader('Cascade output records')
                    st.dataframe(view, use_container_width=True, hide_index=True)
                    st.download_button('⬇ Export selected Cascade rows',
                                       view.to_csv(index=False).encode('utf-8-sig'),
                                       file_name='defirisk_cascade_unverified.csv', mime='text/csv',
                                       key='cascade_export')
        except (OSError, ValueError, ImportError, KeyError) as exc:
            st.error(f'Không đọc được dữ liệu Cascade: {exc}')

else:
    st.markdown(r"""### Research scope
**DeFi Lending Liquidation Stress Test** — nghiên cứu rủi ro thanh lý trong DeFi lending, trọng tâm **Aave V3**. Dashboard phân biệt ba loại dữ liệu: vị thế tại snapshot, sự kiện thanh lý quan sát trên chuỗi và kết quả mô phỏng stress/cascade.

### Research questions
- **Q1 — Borrower vulnerability:** So sánh Looping / Non-looping, E-Mode và cấu trúc đòn bẩy; xem mức độ nhạy cảm của Health Factor theo nhóm vị thế.
- **Q2 — Stress & liquidation thresholds:** Đánh giá tác động của cú sốc giá, ngưỡng HF và các quy tắc Close Factor đến số vị thế / dư nợ có nguy cơ.
- **Q3 — Cascade & contagion:** Phân tích các vòng thanh lý, price feedback, bad debt và lan truyền rủi ro theo tài sản / ví / protocol khi mô hình được xác nhận.

### Core formulas & definitions
**Health Factor (HF)** — ở mức khái niệm:

\[ HF = \frac{\sum_i V_{collateral,i} \times LT_i}{\sum_j V_{debt,j}} \]

Trong đó \(LT_i\) là liquidation threshold áp dụng cho tài sản thế chấp; cách áp dụng **E-Mode, cấu hình reserve, giá oracle và làm tròn** phải khớp phiên bản giao thức tại snapshot. Nếu không có nợ, cách biểu diễn HF cần theo quy ước của pipeline dữ liệu.

- **Positions at Risk:** Số vị thế có \(HF_{post-shock} < 1\) theo quy ước của mô hình.
- **Debt at Risk:** Tổng dư nợ của các vị thế có \(HF_{post-shock} < 1\); **không đồng nghĩa** toàn bộ khoản nợ sẽ bị thanh lý.
- **Simulated Debt Repaid:** Dư nợ được xử lý trong mô hình thanh lý, phụ thuộc Close Factor, thanh khoản và quy tắc thực thi.
- **Bad Debt:** Phần dư nợ không được bảo đảm / thu hồi theo định nghĩa của mô hình; không được suy ra chỉ từ Debt at Risk.
- **Historical Liquidation:** Sự kiện thanh lý thực tế quan sát on-chain, khác với kết quả mô phỏng.

### Stress scenario assumptions — cần xác nhận với N4
- Giá tài sản sau shock có thể được mô tả ở mức kịch bản bằng \(P_i^{shock}=P_i^{base}(1+s_i)\), với \(s_i\) là tỷ lệ shock; đây **không phải xác nhận** rằng mô hình nhóm đang dùng công thức này.
- Cần làm rõ tập tài sản chịu shock, stablecoin depeg, stETH/ETH ratio, cách xử lý nợ và thời điểm định giá.
- **Cliff Finder / Reverse Stress:** Chỉ hiển thị kết quả khi N4 cung cấp thuật toán, tham số và bằng chứng kiểm thử; không tự suy ra từ các slider UI.

### Cascade & liquidation assumptions — cần xác nhận với N5
- Phân biệt **static stress** (định giá lại một lần) với **cascade** (cập nhật trạng thái theo từng vòng).
- Mỗi vòng cần ghi rõ điều kiện chọn vị thế, Close Factor, liquidation bonus, thanh khoản khả dụng, price impact và điều kiện dừng.
- **Price feedback / contagion:** Chỉ kết luận có lan truyền khi mô hình thực sự triển khai liên kết và cơ chế tác động; không đồng nhất số vòng mô phỏng với bằng chứng contagion.
- **Cumulative metrics:** Không cộng chồng các giá trị đã là lũy kế giữa các dòng / nhóm nếu chưa kiểm tra định nghĩa output.

### Data lineage, snapshot & validation
- **Snapshot:** `snapshot_block` phải nhất quán với dữ liệu vị thế, reserve config và oracle phù hợp; ghi nhận protocol, chain, thời điểm, nguồn và phiên bản pipeline.
- **Schema contract:** Kiểm tra cột, dtype, nullability, khóa chính / khóa ngoại và số dòng theo `DATA_CONTRACT.docx` và `schema_storage_safe.py`.
- **Nguồn dữ liệu:** `fact_wallet_summary.parquet` phục vụ Overview / Borrower Risk; `fact_historical_liquidations.parquet` cho Historical; `out_simulation_results.parquet` cho Stress / Cascade.
- **Manifest:** Danh sách file và SHA-256 hỗ trợ kiểm tra tính toàn vẹn, **không tự chứng minh dữ liệu đã nghiệm thu**.
- **Acceptance:** Chỉ gắn nhãn *validated / accepted* sau khi có biên bản hoặc kết quả kiểm thử được nhóm xác nhận. Parquet có schema đúng nhưng **0 dòng** là trạng thái chờ dữ liệu, không phải kết quả rủi ro bằng 0.

### Limitations & interpretation
- Rủi ro mô hình: độ trễ / sai khác oracle, thanh khoản thị trường, slippage, MEV, gas, thay đổi tham số giao thức và giả định hành vi liquidator.
- Các phép đo tại snapshot không thay thế diễn biến theo thời gian; historical on-chain events không tự xác nhận tính đúng của mô phỏng.
- Khi chưa có dữ liệu đã nghiệm thu, dashboard chỉ thể hiện **khả năng tích hợp và cấu trúc giao diện**, không đưa ra kết luận nghiên cứu hay khuyến nghị đầu tư.

### Integration ownership
**N2:** borrower positions / reserve configs → **N3:** price & historical liquidations → **N4:** HF & stress functions → **N5:** cascade / model validation → **N6:** dashboard, hiển thị nguồn và trạng thái dữ liệu.
""")

st.divider()
st.caption('DeFiRisk Lab · UI Prototype · Data / model outputs not yet accepted · Not investment advice')

# Methodology registry reflects actual page wiring; no acceptance claim.
if page.startswith('06'):
    assumptions = pd.DataFrame([
        {'component': 'Overview / Borrower Risk', 'current_source': 'fact_wallet_summary.parquet', 'integration_status': 'Parquet reader connected · acceptance not verified'},
        {'component': 'Historical', 'current_source': 'fact_historical_liquidations.parquet', 'integration_status': 'Parquet reader connected · acceptance not verified'},
        {'component': 'Stress / Cascade', 'current_source': 'out_simulation_results.parquet', 'integration_status': 'Precomputed output reader · not a live model run'},
        {'component': 'HF / E-Mode / Close Factor', 'current_source': 'Team model and snapshot protocol rules', 'integration_status': 'Awaiting methodology and validation confirmation'},
        {'component': 'Data lineage', 'current_source': 'DATA_CONTRACT.docx / schema_storage_safe.py / MANIFEST.json', 'integration_status': 'Schema and checksum evidence do not equal acceptance'},
    ])
    st.dataframe(assumptions, use_container_width=True, hide_index=True)
    st.download_button('⬇ Export integration registry (CSV)', assumptions.to_csv(index=False).encode('utf-8-sig'), file_name='defirisk_methodology_registry.csv', mime='text/csv', key='methodology_registry_export')
