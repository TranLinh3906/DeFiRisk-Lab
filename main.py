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
/* Ice Blue pastel: main content only; original navy sidebar and cards untouched. */
html, body, [data-testid="stApp"], [data-testid="stAppViewContainer"],
[data-testid="stMain"], [data-testid="stMainBlockContainer"] {
  background-color:#EAF5FF !important;
}
[data-testid="stMain"] {
  background:linear-gradient(145deg,#F5FAFF 0%,#EAF5FF 48%,#E1F0FD 100%) !important;
}
[data-testid="stHeader"] {background:rgba(234,245,255,.94) !important;}
/* Keep the dark navigation and its scroll region exactly as designed. */
[data-testid="stSidebar"], [data-testid="stSidebar"] > div,
[data-testid="stSidebar"] [data-testid="stSidebarContent"] {
  background-color:#0b1931 !important;
}

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

/* Bloomberg x Aurora | TABLE-ONLY OVERRIDES: original navigation, logo and charts untouched */
.aurora-table-shell,.risk-shell{background:#203A55!important;border:1px solid #6486A5!important;border-top-width:3px!important;border-radius:13px!important;box-shadow:0 12px 26px #07152a24!important;padding:17px!important}
.aurora-table-title,.risk-title{font-family:Inter,Arial,sans-serif!important;font-size:1.13rem!important;letter-spacing:.005em!important;color:#f3f7ff!important}
.aurora-table-title:before{content:'▮';color:#f3ae4d;margin-right:9px}
.aurora-table-sub,.risk-kicker{font-size:.71rem!important;color:#92aac5!important;letter-spacing:.045em!important}
.aurora-scroll,.risk-table-wrap{border:1px solid #29435c!important;border-radius:7px!important;background:#264560!important;scrollbar-color:#8EABC4 #264560}
.aurora-data,.risk-table{font-family:'Cascadia Code','Consolas','SFMono-Regular',monospace!important;font-size:.78rem!important;color:#e8f0fa!important;font-variant-numeric:tabular-nums!important}
.aurora-data th,.risk-table th{background:#345B79!important;color:#E7F5FF!important;padding:11px 13px!important;font-size:.67rem!important;letter-spacing:.055em!important;position:sticky!important;top:0!important;z-index:2!important;border-bottom:1px solid #7194B2!important}
.aurora-data td,.risk-table td{padding:10px 13px!important;border-top:1px solid #496985!important;line-height:1.45!important}
.aurora-data tbody tr:nth-child(even),.risk-table tbody tr:nth-child(even){background:#2B4C68!important}
.aurora-data tbody tr:hover,.risk-table tbody tr:hover{background:#3B6684!important}
.aurora-data td.terminal-num{text-align:right;color:#f1f5fd}
.aurora-data th.terminal-num{text-align:right}
.aurora-data td.terminal-identity{color:#78c9f9;font-weight:650}
.aurora-data td.terminal-alert{color:#ff899a;font-weight:700}
.aurora-data td.terminal-safe{color:#75dfb4;font-weight:650}
.aurora-data td.terminal-muted{color:#a8bdd5}
.aurora-table-footer,.risk-footer{color:#91abc6!important;font-family:'Cascadia Code',Consolas,monospace;font-size:.69rem!important}
.risk-kpis{gap:8px!important}.risk-kpi{background:#315471!important;border:1px solid #6485A1!important;border-radius:7px!important;padding:11px!important}
.risk-number{font-family:'Cascadia Code',Consolas,monospace;font-size:1.35rem!important}
.risk-composition{background:#315471!important;border-radius:7px!important;border-color:#2a425b!important}
.risk-pill{border-radius:4px!important;padding:4px 7px!important;letter-spacing:.03em!important}
.risk-tag{border-radius:4px!important;background:#163c34!important;border-color:#276a55!important;color:#78e2b6!important}
.risk-wallet{font-family:'Cascadia Code',Consolas,monospace!important}
/* Native widgets outside terminal tables retain the existing design. */

/* CONCEPT 01 · CLASSIC CLEAN / all six pages. Sidebar, data and logic unchanged. */
[data-testid="stMain"]{
 background:radial-gradient(ellipse at 90% 0%,#D9EEFF 0%,transparent 46%),
 linear-gradient(160deg,#F4FAFF 0%,#EAF5FF 60%,#E7F2FC 100%)!important;
}
.block-container{max-width:1450px;padding-top:1.55rem}
.page-banner{background-image:linear-gradient(110deg,rgba(255,255,255,.13),transparent 70%)!important;
 border-radius:18px!important;box-shadow:0 10px 24px #4D87B72B!important;padding:23px 28px!important}
.page-banner-title{font-size:1.83rem!important;letter-spacing:-.025em!important}
.page-banner-sub{font-size:.96rem!important}
[data-testid="stMain"] h1,[data-testid="stMain"] h2,[data-testid="stMain"] h3{
 color:#173454!important;letter-spacing:-.03em!important;font-weight:800!important}
[data-testid="stMain"] h2{font-size:1.55rem!important}
[data-testid="stMain"] h3{font-size:1.25rem!important}
[data-testid="stMetric"]{
 background:linear-gradient(155deg,#FFFFFF,#EDF7FF)!important;
 border:1px solid #C9E0F2!important;border-radius:15px!important;
 box-shadow:0 7px 20px #5085AC14!important;padding:19px 21px!important;
 min-height:112px!important}
[data-testid="stMetricLabel"] p{color:#54718D!important;font-size:.85rem!important;font-weight:650!important}
[data-testid="stMetricValue"] div{color:#153A65!important;font-weight:800!important;font-size:2rem!important}
[data-testid="stMetricDelta"]{color:#098C82!important}
[data-testid="stMain"] [data-testid="stRadio"]>label,
[data-testid="stMain"] [data-testid="stSelectbox"]>label,
[data-testid="stMain"] [data-testid="stMultiSelect"]>label{
 color:#365675!important;font-weight:650!important}
[data-testid="stMain"] [data-baseweb="select"]>div,
[data-testid="stMain"] [data-testid="stNumberInput"] input{
 background:#FFFFFF!important;border:1px solid #C8DFEF!important;border-radius:10px!important}
[data-testid="stMain"] .stButton>button:not([kind="primary"]){
 border-radius:10px!important;border-color:#BBD7EB!important;
 background:#F7FBFF!important;color:#285B89!important}
.aurora-table-shell,.risk-shell{
 background:linear-gradient(160deg,#274F71,#315C7D)!important;
 border:1px solid #7BA4C1!important;border-radius:17px!important;
 box-shadow:0 12px 26px #487DA32B!important}
.aurora-table-title,.risk-title{color:#FFFFFF!important;font-size:1.17rem!important}
.aurora-scroll,.risk-table-wrap{background:#284E6D!important;border-color:#7A9FBA!important}
.aurora-data th,.risk-table th{background:#3B6788!important;color:#F1F9FF!important}
.aurora-data tbody tr:nth-child(even),.risk-table tbody tr:nth-child(even){background:#355D7A!important}
.aurora-data tbody tr:hover,.risk-table tbody tr:hover{background:#477794!important}
.aurora-data td,.risk-table td{border-top-color:#547993!important}
.risk-kpi,.risk-composition{background:#365F7D!important;border-color:#7397B1!important}
.aurora-table-footer,.risk-footer{color:#D5E8F8!important}
@media(max-width:780px){.page-banner-title{font-size:1.5rem!important}[data-testid="stMetricValue"] div{font-size:1.5rem!important}}


/* MICRO PATCH: section titles + filter labels ONLY (no layout/data changes). */
[data-testid="stMain"] [data-testid="stHeadingWithActionElements"] h3 {
  display:inline-block!important;
  background:linear-gradient(90deg,#D9EEFF,#F1F8FF)!important;
  border-left:5px solid #2686C9!important;
  border-bottom:1px solid #C0DBEF!important;
  border-radius:0 12px 12px 0!important;
  padding:11px 18px!important;
  color:#123D68!important;
  font-size:1.52rem!important;
  font-weight:850!important;
  box-shadow:0 4px 14px #538AB51B!important;
}
[data-testid="stMain"] [data-testid="stSelectbox"] > label p,
[data-testid="stMain"] [data-testid="stMultiSelect"] > label p,
[data-testid="stMain"] [data-testid="stRadio"] > label p {
  color:#185684!important;
  font-weight:800!important;
  font-size:.94rem!important;
  letter-spacing:.012em!important;
}
</style>""",unsafe_allow_html=True)

def dollars(x):
    return f'${x/1e6:,.2f}M' if abs(x)>=1e6 else f'${x:,.0f}'

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



def _set_table_page(page_key, target):
    st.session_state[page_key] = int(target)


def _sync_table_page(page_key, selector_key):
    st.session_state[page_key] = int(st.session_state[selector_key])


def render_aurora_table(df, title, key, accent='#1266D6', subtitle='', formats=None):
    """Reusable styled table for all non-wallet datasets, preserving full data for export."""
    df = df.copy().reset_index(drop=True)
    formats = formats or {}
    if df.empty:
        return
    # Bộ lọc chọn sẵn: người xem không cần đoán từ khóa để tìm kiếm.
    show_controls = len(df) > 20
    filtered = df
    page_size = max(1, len(df))
    if show_controls:
        # Chỉ đưa các trường có danh sách giá trị đủ ngắn vào bộ lọc.
        preferred = ('snapshot_id', 'snapshot_block', 'scenario_id', 'variant',
                     'method', 'date_utc', 'date', 'pool_state', 'ordering',
                     'pair_rule', 'seed', 'oracle_feedback', 'fee_policy',
                     'main_collateral', 'emode', 'leverage', 'status', 'risk')
        choices = []
        for col in df.columns:
            unique_count = df[col].nunique(dropna=True)
            if 1 < unique_count <= 120:
                choices.append(col)
        choices.sort(key=lambda c: (preferred.index(c) if c in preferred else 999, str(c)))
        if choices:
            col_filter, col_values, col_rows = st.columns([1.15, 2.2, 1])
            chosen_col = col_filter.selectbox(
                'Chọn tiêu chí', ['Tất cả'] + choices,
                format_func=lambda v: 'Tất cả dữ liệu' if v == 'Tất cả' else str(v).replace('_', ' ').title(),
                key=f'{key}_filter_column'
            )
            if chosen_col != 'Tất cả':
                values = sorted(df[chosen_col].dropna().unique().tolist(), key=lambda v: str(v))
                chosen_values = col_values.multiselect(
                    'Chọn giá trị', values,
                    format_func=lambda v: str(v),
                    placeholder='Chọn một hoặc nhiều giá trị...',
                    key=f'{key}_filter_values_{chosen_col}'
                )
                if chosen_values:
                    filtered = df[df[chosen_col].isin(chosen_values)]
            page_size = col_rows.selectbox('Số dòng', [20, 50, 100], key=f'{key}_size')
        else:
            page_size = st.selectbox('Số dòng hiển thị', [20, 50, 100], key=f'{key}_size')
    count = len(filtered)
    if count == 0:
        return
    pages = max(1, (count + page_size - 1) // page_size)
    page_key = f'{key}_page_index'
    if page_key not in st.session_state:
        st.session_state[page_key] = 1
    page = min(max(1, int(st.session_state[page_key])), pages)
    st.session_state[page_key] = page
    shown = filtered.iloc[(page - 1) * page_size:page * page_size]
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
    numeric_columns = {c for c in shown.columns if pd.api.types.is_numeric_dtype(shown[c]) and not pd.api.types.is_bool_dtype(shown[c])}
    def cell_class(col, val):
        if col in numeric_columns:
            if col in ('health_factor', 'hf', 'hf_after') and pd.notna(val):
                return 'terminal-num terminal-alert' if float(val) < 1 else 'terminal-num terminal-safe'
            return 'terminal-num'
        if col in ('wallet', 'wallet_address', 'scenario_id', 'snapshot_id'):
            return 'terminal-identity'
        return 'terminal-muted' if pd.isna(val) else ''
    headers = ''.join(f'<th class={"terminal-num" if col in numeric_columns else "terminal-text"}>{escape(str(col).replace("_", " "))}</th>' for col in shown.columns)
    body = ''
    for _, row in shown.iterrows():
        cells = ''.join(f'<td class="{cell_class(col, row[col])}">{escape(format_cell(col, row[col]))}</td>' for col in shown.columns)
        body += f'<tr>{cells}</tr>'
    if not body: body = f'<tr><td colspan="{len(df.columns)}">No matching records</td></tr>'
    table_html = (
        f"<div class='aurora-table-shell' style='border-top:4px solid {accent}'>"
        f"<div class='aurora-table-title'>✦ {escape(title)}</div>"
        + (f"<div class='aurora-table-sub'>{escape(subtitle)}</div>" if subtitle else "")
        + f"<div class='aurora-scroll'><table class='aurora-data'><thead><tr>{headers}</tr></thead><tbody>{body}</tbody></table></div>"
        + f"<div class='aurora-table-footer'>Showing {len(shown):,} / {count:,} filtered rows · Page {page}/{pages}</div>"
        + "</div>"
    )
    st.markdown(table_html, unsafe_allow_html=True)
    if pages > 1:
        prev_col, page_col, next_col = st.columns([1, 2, 1])
        selector_key = f'{key}_bottom_page'
        # Đồng bộ selector trước khi Streamlit tạo widget, tránh giá trị cũ ghi đè trang mới.
        if st.session_state.get(selector_key) != page:
            st.session_state[selector_key] = page
        with prev_col:
            st.button('← Trước', key=f'{key}_prev', disabled=page <= 1,
                      use_container_width=True, on_click=_set_table_page, args=(page_key, page - 1))
        with page_col:
            st.selectbox('Trang', list(range(1, pages + 1)), key=selector_key,
                         label_visibility='collapsed', on_change=_sync_table_page,
                         args=(page_key, selector_key))
        with next_col:
            st.button('Tiếp →', key=f'{key}_next', disabled=page >= pages,
                      use_container_width=True, on_click=_set_table_page, args=(page_key, page + 1))
    st.download_button('⬇ Export filtered CSV', filtered.to_csv(index=False).encode('utf-8-sig'),
                       file_name=f'defirisk_{key}.csv', mime='text/csv', key=f'{key}_csv')

def render_risk_explorer(data, key='borrower', hf_col='health_factor', title='Borrower positions'):
    # Full-width dark HTML table + native Streamlit controls.
    df = data.copy()
    if hf_col not in df.columns or 'wallet' not in df.columns:
        st.error('Missing wallet / Health Factor columns for Risk Explorer.')
        return
    df['_risk'] = df[hf_col].apply(lambda v: risk_level(float(v))[0])
    st.markdown('#### ▮ QUANT / Borrower Exposure Screener')
    # Bộ lọc dạng chọn sẵn cho Risk Explorer, đồng nhất với mọi bảng khác.
    filter_cols = [c for c in ('_risk', 'main_collateral', 'emode', 'leverage', 'snapshot_id', 'snapshot_block')
                   if c in df.columns and 1 < df[c].nunique(dropna=True) <= 120]
    if filter_cols:
        c_filter, c_value, c_sort = st.columns([1.1, 2, 1.2])
        selected_col = c_filter.selectbox('Chọn tiêu chí', ['Tất cả'] + filter_cols,
            format_func=lambda v: {'_risk': 'Mức rủi ro', 'main_collateral': 'Tài sản thế chấp',
                                   'emode': 'E-Mode', 'leverage': 'Chiến lược'}.get(v, str(v).replace('_', ' ').title())
                                   if v != 'Tất cả' else 'Tất cả dữ liệu', key=f'{key}_filter_col')
        if selected_col != 'Tất cả':
            available = sorted(df[selected_col].dropna().unique().tolist(), key=str)
            selected_values = c_value.multiselect('Chọn giá trị', available, key=f'{key}_filter_values_{selected_col}',
                                                  placeholder='Chọn một hoặc nhiều giá trị...')
            if selected_values:
                df = df[df[selected_col].isin(selected_values)]
        sort_mode = c_sort.selectbox('Sắp xếp', ['Lowest HF', 'Highest debt', 'Highest HF'], key=f'{key}_sort')
    else:
        sort_mode = st.selectbox('Sắp xếp', ['Lowest HF', 'Highest debt', 'Highest HF'], key=f'{key}_sort')
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
      <div class="risk-head"><div><div class="risk-title">▮ {escape(title)}</div>
      <div class="risk-kicker">BORROWER ANALYTICS / AAVE V3</div></div><span class="risk-tag">● RESEARCH DATA</span></div>
      <div class="risk-kpis">
        <div class="risk-kpi"><div class="risk-number" style="color:#70d9ff">{total:,}</div><div class="risk-label">FILTERED POSITIONS</div></div>
        <div class="risk-kpi"><div class="risk-number" style="color:#ff8ca1">{at_risk:,}</div><div class="risk-label">HF BELOW 1</div></div>
        <div class="risk-kpi"><div class="risk-number" style="color:#ffc37a">{compact_money(debt_at_risk)}</div><div class="risk-label">DEBT WITH HF < 1</div></div>
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
    risk_page_key = f'{key}_risk_page_index'
    if risk_page_key not in st.session_state:
        st.session_state[risk_page_key] = 1
    page_number = min(max(1, int(st.session_state[risk_page_key])), pages)
    st.session_state[risk_page_key] = page_number
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
             f' · Page {page_number}/{pages}</div></div>')
    st.markdown(html, unsafe_allow_html=True)
    if pages > 1:
        prev_col, page_col, next_col = st.columns([1, 2, 1])
        selector_key = f'{key}_risk_bottom_page'
        # Đồng bộ selector trước khi Streamlit tạo widget, tránh giá trị cũ ghi đè trang mới.
        if st.session_state.get(selector_key) != page_number:
            st.session_state[selector_key] = page_number
        with prev_col:
            st.button('← Trước', key=f'{key}_risk_prev', disabled=page_number <= 1,
                      use_container_width=True, on_click=_set_table_page, args=(risk_page_key, page_number - 1))
        with page_col:
            st.selectbox('Trang', list(range(1, pages + 1)), key=selector_key,
                         label_visibility='collapsed', on_change=_sync_table_page,
                         args=(risk_page_key, selector_key))
        with next_col:
            st.button('Tiếp →', key=f'{key}_risk_next', disabled=page_number >= pages,
                      use_container_width=True, on_click=_set_table_page, args=(risk_page_key, page_number + 1))
    if not df.empty:
        csv = df.drop(columns=['_risk']).to_csv(index=False).encode('utf-8-sig')
        st.download_button('⬇ Export filtered rows (CSV)', csv, file_name=f'defirisk_{key}.csv', mime='text/csv', key=f'{key}_export')
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
    st.markdown('''<div class="status-panel"><div class="status-title">✦ System status <span class="status-pill">UI READY</span></div><div class="status-desc">Research outputs · File availability checked per page</div></div>''', unsafe_allow_html=True)
    st.divider()

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

# All research tables are read locally. No synthetic fallback.
ROOT = Path(__file__).resolve().parent
OUTPUTS = ROOT / 'repo_data' / 'outputs'
N5 = OUTPUTS / 'n5_completion_20261026'
EXEC = N5 / 'approved_package_with27'

@st.cache_data(show_spinner=False, ttl=3600)
def load_table(path_string):
    p = Path(path_string)
    if p.suffix.lower() == '.csv':
        return pd.read_csv(p)
    return pd.read_parquet(p)

def locate(*relative_paths):
    for relative in relative_paths:
        for p in (OUTPUTS / relative, ROOT / relative, ROOT / 'repo_data' / relative, ROOT / 'data' / 'processed' / Path(relative).name, ROOT / 'repo_data' / 'data' / 'processed' / Path(relative).name):
            if p.is_file():
                return p
    return None

def get_data(*relative_paths):
    path = locate(*relative_paths)
    if path is None:
        return None
    try:
        data = load_table(str(path))
        if data.empty:
            return None
        return data
    except Exception as exc:
        st.error(f'Lỗi đọc {path.name}: {exc}')
        return None

def numeric(df, names):
    for name in names:
        if name in df.columns:
            return name
    return None

def show_table(df, filename):
    """Shared terminal renderer for Stress / Cascade, preserving raw export and filters."""
    key = 'terminal_' + ''.join(ch if ch.isalnum() else '_' for ch in filename)
    accent = '#C47A14' if 'stress' in filename else '#C3445B' if 'cascade' in filename else '#1266D6'
    render_aurora_table(df, filename.replace('_', ' ').removesuffix('.csv').title(),
                        key, accent=accent, subtitle='')

def kpi_sum(df, candidates, title, money=False):
    col = numeric(df, candidates)
    if col:
        val = pd.to_numeric(df[col], errors='coerce').sum()
        st.metric(title, f'${val:,.0f}' if money else f'{val:,.0f}')
    else:
        st.metric(title, 'N/A')


# Bloomberg × Aurora chart system. Presentation only; all values come from loaded data.
def quant_style(fig, height=390, accent='#55B9FF'):
    fig.update_layout(
        template='plotly_white', paper_bgcolor='#F8FCFF', plot_bgcolor='#F8FCFF',
        font=dict(family='Arial, sans-serif', color='#345878', size=12),
        height=height, margin=dict(l=42,r=40,t=38,b=48),
        hoverlabel=dict(bgcolor='#244D70',font_color='#FFFFFF',font_size=12),
        legend=dict(orientation='h',y=1.13,x=0,font=dict(size=11)),
        xaxis=dict(gridcolor='#DCEAF5',zeroline=False,showline=False),
        yaxis=dict(gridcolor='#DCEAF5',zeroline=False,showline=False),
    )
    return fig

def quant_chart(fig, title, accent='#55B9FF', height=390):
    st.markdown(
        f'<div style="background:linear-gradient(120deg,#FFFFFF,#EFF8FF);border:1px solid #C6E0F2;'
        f'border-left:4px solid {accent};border-radius:15px 15px 0 0;'
        f'padding:16px 20px 10px;color:#193D61;font-weight:800;font-size:1.08rem">'
        f'{escape(title)}</div>',unsafe_allow_html=True)
    quant_style(fig,height,accent)
    st.plotly_chart(fig,use_container_width=True,config={'displaylogo':False})

def quant_stress(df, title='Stress · Debt at risk ranking', max_bars=19):
    if not {'scenario_id','debt_at_risk_usd'}.issubset(df.columns) or df.empty: return
    x=df.copy()
    x['debt_at_risk_usd']=pd.to_numeric(x['debt_at_risk_usd'],errors='coerce')
    x=x.dropna(subset=['debt_at_risk_usd'])
    if x.empty:return
    # When multiple snapshots are present, show the mean per scenario and label it honestly.
    if x['scenario_id'].duplicated().any():
        x=x.groupby('scenario_id',as_index=False)['debt_at_risk_usd'].mean()
        title+=' · mean across snapshots'
    x=x.nlargest(max_bars,'debt_at_risk_usd').sort_values('debt_at_risk_usd')
    vals=x['debt_at_risk_usd'].to_numpy()
    q1,q2=np.quantile(vals,[.5,.8])
    colors=['#FF7890' if v>=q2 else '#F4B65F' if v>=q1 else '#53B9FF' for v in vals]
    fig=go.Figure(go.Bar(x=vals,y=x['scenario_id'].astype(str),orientation='h',
        marker=dict(color=colors,line=dict(width=0)),
        customdata=np.array(x['scenario_id'].astype(str))[:,None],
        hovertemplate='Scenario: %{y}<br>Debt at risk: $%{x:,.0f}<extra></extra>'))
    fig.update_xaxes(title='Debt at risk (USD)',tickformat='~s')
    fig.update_yaxes(title='',automargin=True)
    quant_chart(fig,title,'#F4B65F',height=max(350,min(690,120+len(x)*28)))

def quant_historical(df,time_col,value_col,count_col,selected):
    if df.empty:return
    x=df.copy()
    x[time_col]=pd.to_datetime(x[time_col],errors='coerce',utc=True)
    x=x.dropna(subset=[time_col]).sort_values(time_col)
    if x.empty:return
    if value_col:
        x[value_col]=pd.to_numeric(x[value_col],errors='coerce')
        fig=go.Figure(go.Scatter(x=x[time_col],y=x[value_col],mode='lines+markers',
            line=dict(color='#37D9C2',width=3),marker=dict(size=7,color='#7BFCE5'),
            fill='tozeroy',fillcolor='rgba(55,217,194,.10)',
            hovertemplate='%{x|%d %b %Y %H:%M} UTC<br>Repaid: $%{y:,.2f}<extra></extra>'))
        fig.update_yaxes(title='Debt repaid (USD)',tickformat='~s')
        quant_chart(fig,f'Historical · Liquidation volume ({selected.lower()})','#37D9C2',380)
    if count_col:
        x[count_col]=pd.to_numeric(x[count_col],errors='coerce')
        fig=go.Figure(go.Bar(x=x[time_col],y=x[count_col],marker_color='#55B9FF',
            hovertemplate='%{x|%d %b %Y %H:%M} UTC<br>Events: %{y:,.0f}<extra></extra>'))
        fig.update_yaxes(title='Liquidation events',rangemode='tozero')
        quant_chart(fig,'Historical · Liquidation events','#55B9FF',320)

def quant_cascade(df):
    x=df.copy().sort_values('round')
    x['round']=pd.to_numeric(x['round'],errors='coerce')
    x=x.dropna(subset=['round'])
    if x.empty:return
    if 'executed_liquidations' in x:
        vals=pd.to_numeric(x['executed_liquidations'],errors='coerce')
        fig=go.Figure(go.Bar(x=x['round'],y=vals,
            marker=dict(color=['#F5A84C' if v>0 else '#536B87' for v in vals]),
            hovertemplate='Round %{x}<br>Liquidations: %{y:,.0f}<extra></extra>'))
        fig.update_xaxes(title='Execution round',dtick=1)
        fig.update_yaxes(title='Executed liquidations',rangemode='tozero')
        quant_chart(fig,'Cascade · Liquidations per round','#F5A84C',360)
    if 'repaid_debt_usd' in x:
        vals=pd.to_numeric(x['repaid_debt_usd'],errors='coerce')
        fig=go.Figure(go.Scatter(x=x['round'],y=vals,mode='lines+markers',
            line=dict(color='#D79BFF',width=3),marker=dict(size=8,color='#E5BDFF'),
            fill='tozeroy',fillcolor='rgba(175,112,255,.12)',
            hovertemplate='Round %{x}<br>Debt repaid: $%{y:,.2f}<extra></extra>'))
        fig.update_xaxes(title='Execution round',dtick=1)
        fig.update_yaxes(title='Repaid debt (USD)',tickformat='~s')
        quant_chart(fig,'Cascade · Debt repaid by round','#C3445B',360)

def quant_hf(df,hf):
    values=pd.to_numeric(df[hf],errors='coerce').dropna()
    if values.empty:return
    fig=go.Figure(go.Histogram(x=values,nbinsx=40,marker_color='#9A7BFF',
        hovertemplate='Health Factor: %{x:.2f}<br>Positions: %{y}<extra></extra>'))
    fig.add_vline(x=1,line=dict(color='#FF7890',width=2,dash='dash'))
    fig.update_xaxes(title='Health Factor')
    fig.update_yaxes(title='Borrower positions')
    quant_chart(fig,'Borrower · Health Factor distribution','#9A7BFF',360)

if page.startswith('01'):
    st.subheader('✦ Protocol Risk Overview')
    stress_df=get_data('n5_completion_20261026/n4_research/stress_summary.parquet')
    summary=get_data('n5_completion_20261026/approved_package_with27/execution_summary.parquet')
    wallets=get_data('data/processed/fact_wallet_summary.parquet','fact_wallet_summary.parquet')
    # The full borrower snapshot may be absent/empty. Use observed N3 top-wallet
    # evidence only as a clearly labelled fallback, never as a full population.
    top_wallets = (get_data('tables/n3_m6_top50_wallets_AB1B2.csv')
                   if wallets is None else None)
    a,b,c,d=st.columns(4)
    a.metric('Stress results', f'{len(stress_df):,}' if stress_df is not None else 'N/A')
    b.metric('Stress scenarios',f'{stress_df.scenario_id.nunique():,}' if stress_df is not None and 'scenario_id' in stress_df else 'N/A')
    c.metric('Cascade configurations',f'{len(summary):,}' if summary is not None else 'N/A')
    if wallets is not None:
        d.metric('Borrower records',f'{len(wallets):,}')
    elif top_wallets is not None:
        d.metric('N3 top-wallet rows',f'{len(top_wallets):,}',
                 help='Rows in N3 top-wallet evidence across snapshots; not the total borrower population.')
    else:
        d.metric('Borrower records','—',
                 help='No nonempty borrower snapshot or N3 top-wallet evidence was found in the configured data paths.')
    if stress_df is not None and {'scenario_id','debt_at_risk_usd'}.issubset(stress_df):
        st.subheader('📊 Stress Risk Overview')
        chart=stress_df.groupby('scenario_id',as_index=False)['debt_at_risk_usd'].mean().nlargest(12,'debt_at_risk_usd')
        quant_stress(chart, 'Overview · Highest-risk scenarios', 12)
        render_aurora_table(stress_df,'Stress scenario results','overview_stress',subtitle='')
    if wallets is not None:
        st.subheader('🛡 Borrower snapshot')
        hf=numeric(wallets,['health_factor','hf','health_factor_current'])
        debt=numeric(wallets,['total_debt_usd','debt_usd','borrowed_usd','debt_value_usd'])
        if hf:
            vals=pd.to_numeric(wallets[hf],errors='coerce')
            a,b=st.columns(2)
            a.metric('Positions with HF < 1',f'{(vals<1).sum():,}')
            if debt: b.metric('Total recorded debt',dollars(pd.to_numeric(wallets[debt],errors='coerce').sum()))
            quant_hf(wallets,hf)
        render_aurora_table(wallets.head(5000),'Borrower snapshot records','overview_wallets',subtitle='')

elif page.startswith('02'):
    st.subheader('🕒 Historical Liquidation Intelligence')
    daily=get_data('tables/n3_liquidations_daily.csv')
    hourly=get_data('tables/n3_liquidations_hourly.csv')
    historical=get_data('data/processed/fact_historical_liquidations.parquet','fact_historical_liquidations.parquet')
    selected=st.radio('Time resolution',['Daily','Hourly'],horizontal=True)
    frame=daily if selected=='Daily' else hourly
    if frame is not None:
        a,b=st.columns(2)
        a.metric('Observed aggregation rows',f'{len(frame):,}')
        count_col=numeric(frame,['liquidation_events','liquidation_count','liquidations','n_liquidations','event_count','count'])
        b.metric('Observed liquidation events',f'{pd.to_numeric(frame[count_col],errors="coerce").sum():,.0f}' if count_col else 'N/A')
        time_col=numeric(frame,['date_utc','hour_utc','date','day','hour','timestamp','datetime','block_timestamp','event_date'])
        value_col=numeric(frame,['debt_repaid_usd','liquidated_debt_usd','liquidation_usd','amount_usd','total_usd','total_debt_repaid_usd','total_liquidated_usd'])
        if time_col:
            graph=frame.copy()
            graph[time_col]=pd.to_datetime(graph[time_col],errors='coerce',utc=True)
            graph=graph.dropna(subset=[time_col]).sort_values(time_col)
            if value_col:
                graph[value_col]=pd.to_numeric(graph[value_col],errors='coerce')
                pass  # Rendered together below with Bloomberg × Aurora styling
            if count_col:
                graph[count_col]=pd.to_numeric(graph[count_col],errors='coerce')
                pass  # Rendered together below with Bloomberg × Aurora styling
            quant_historical(graph,time_col,value_col,count_col,selected) if time_col else None
        render_aurora_table(frame,'Historical liquidation · '+selected,'historical_'+selected.lower(),accent='#008E83',subtitle='')
    if historical is not None:
        with st.expander('🔎 Raw historical liquidation records'):
            render_aurora_table(historical.head(5000),'Historical event records','historical_raw',accent='#008E83',subtitle='')
    st.subheader('Market alignment evidence')
    market=get_data('tables/n3_liquidation_market_hourly.csv')
    if market is not None:
        render_aurora_table(market,'Hourly liquidation & market alignment','historical_market',accent='#008E83',subtitle='')

elif page.startswith('03'):
    st.subheader('🛡 Aurora Borrower Risk Intelligence')
    wallets=get_data('data/processed/fact_wallet_summary.parquet','fact_wallet_summary.parquet')
    if wallets is None:
        top=get_data('tables/n3_m6_top50_wallets_AB1B2.csv')
        if top is not None:
            render_aurora_table(top,'N3 · Top 50 wallets','borrower_top50',accent='#7048D4',subtitle='')
    else:
        aliases={'wallet':['wallet','wallet_address','user_address','address','borrower'],
                 'health_factor':['health_factor','hf','health_factor_current'],
                 'debt_usd':['debt_usd','total_debt_usd','borrowed_usd','debt_value_usd'],
                 'collateral_usd':['collateral_usd','total_collateral_usd','collateral_value_usd']}
        view=wallets.copy()
        for canonical,candidates in aliases.items():
            source=numeric(view,candidates)
            if source and canonical not in view: view[canonical]=view[source]
        required={'wallet','health_factor','debt_usd','collateral_usd'}
        if required.issubset(view.columns):
            for col in ['health_factor','debt_usd','collateral_usd']:
                view[col]=pd.to_numeric(view[col],errors='coerce')
            view=view.dropna(subset=['wallet','health_factor','debt_usd','collateral_usd'])
            render_risk_explorer(view,key='borrower',title='Observed borrower positions')
        else:
            st.warning('Thiếu cột cần thiết để phân tích Health Factor.')
            render_aurora_table(view,'Borrower snapshot · raw columns','borrower_raw',accent='#7048D4',subtitle='')
    st.subheader('N3 · Health Factor & leverage evidence')
    hf_evidence=get_data('tables/n3_m6_hf_AB1B2.csv')
    if hf_evidence is not None:
        render_aurora_table(hf_evidence,'Health Factor evidence','borrower_n3_hf',accent='#7048D4',subtitle='')

elif page.startswith('04'):
    st.subheader('Stress Simulator · precomputed research scenarios')
    stress=get_data('n5_completion_20261026/n4_research/stress_summary.parquet')
    if stress is not None:
        view=stress.copy()
        for col,label in [('snapshot_block','Snapshot block'),('scenario_id','Scenario')]:
            if col in view and view[col].notna().any():
                options=['All']+sorted(view[col].dropna().astype(str).unique().tolist())
                chosen=st.selectbox(label,options,key='stress_'+col)
                if chosen!='All': view=view[view[col].astype(str)==chosen]
        if view.empty: pass
        else:
            a,b,c=st.columns(3)
            a.metric('Output rows',f'{len(view):,}')
            b.metric('Scenarios',f'{view.scenario_id.nunique():,}' if 'scenario_id' in view else 'N/A')
            if 'debt_at_risk_usd' in view:
                c.metric('Maximum debt at risk',f'${pd.to_numeric(view.debt_at_risk_usd,errors="coerce").max():,.0f}')
                chart=view.copy()
                chart['case_label']=chart['scenario_id'].astype(str) if 'scenario_id' in chart else chart.index.astype(str)
                if 'snapshot_block' in chart: chart['case_label']=chart['snapshot_block'].astype(str)+' · '+chart['case_label']
                quant_stress(chart,'Stress · Debt at risk by scenario')
            else: c.metric('Debt at risk','N/A')
            show_table(view,'stress_summary.csv')

elif page.startswith('05'):
    st.subheader('Liquidation Cascade · N5 execution results')
    summary=get_data('n5_completion_20261026/approved_package_with27/execution_summary.parquet')
    rounds=get_data('n5_completion_20261026/approved_package_with27/execution_rounds.parquet')
    if summary is not None and rounds is not None:
        keys=['snapshot_id','scenario_id','variant','method']
        view=summary.copy()
        cols=st.columns(4)
        selected={}
        for i,(key,label) in enumerate(zip(keys,['Snapshot','Scenario','Variant','Method'])):
            opts=sorted(view[key].dropna().astype(str).unique().tolist()) if key in view else []
            if not opts:
                st.stop()
            choice=cols[i].selectbox(label,opts,key='n5_'+key)
            selected[key]=choice
            view=view[view[key].astype(str)==choice]
        # Additional dimensions must be selected explicitly to avoid silently taking first row.
        for key in ['ordering','pair_rule','seed','oracle_feedback','freeze_proven','pool_state','gas_usd','fee_policy','snapshot_block']:
            if key in view and view[key].nunique(dropna=False)>1:
                options=sorted(view[key].dropna().astype(str).unique().tolist())
                choice=st.selectbox(key.replace('_',' ').title(),options,key='n5_extra_'+key)
                view=view[view[key].astype(str)==choice]
        if view.empty: pass
        elif len(view)!=1:
            st.warning(f'Còn {len(view)} kết quả khác nhau. Hiển thị bảng để tránh chọn nhầm KPI.')
            show_table(view,'cascade_summary_filtered.csv')
        else:
            r=view.iloc[0]
            a,b,c,d=st.columns(4)
            a.metric('Executed liquidations',f'{int(r["executed_liquidations"]):,}')
            b.metric('Simulated repaid debt',f'${r["simulated_repaid_usd"]:,.0f}')
            c.metric('Liquidator profit',f'${r["liquidator_profit_usd"]:,.0f}')
            d.metric('Rounds',f'{int(r["rounds"]):,}')
            st.caption(f'Initial eligible wallets: {r.get("initial_eligible_wallets","N/A")} · Unknown candidate attempts: {r.get("unknown_candidate_attempts","N/A")} · Hit round limit: {r.get("hit_round_limit","N/A")}')
            rr=rounds.copy()
            for key,val in selected.items(): rr=rr[rr[key].astype(str)==val]
            if 'snapshot_block' in rr and 'snapshot_block' in view:
                rr=rr[rr.snapshot_block.astype(str)==str(r['snapshot_block'])]
            if rr.empty: pass
            else:
                # Distinguish multiple runs with same shared dimensions; do not aggregate as one run.
                if rr.duplicated(['round']).any():
                    st.warning('Có nhiều bản ghi cho cùng một vòng trong lựa chọn này. Không cộng dồn các run khác nhau.')
                    show_table(rr,'cascade_rounds_ambiguous.csv')
                else:
                    rr=rr.sort_values('round')
                    quant_cascade(rr)
                    pass  # Included in quant_cascade above
                    show_table(rr,'cascade_rounds.csv')
            st.subheader('Execution transactions')
            if st.checkbox('Tải bảng giao dịch chi tiết (48.114 dòng toàn bộ file)',value=False):
                tx=get_data('n5_completion_20261026/approved_package_with27/execution_transactions.parquet')
                if tx is not None:
                    for key,val in selected.items(): tx=tx[tx[key].astype(str)==val]
                    if 'snapshot_block' in tx: tx=tx[tx.snapshot_block.astype(str)==str(r['snapshot_block'])]
                    st.metric('Matching transaction rows',f'{len(tx):,}')
                    if not tx.empty: show_table(tx,'cascade_transactions.csv')

else:
    # Methodology: Research Command Center + Quant Blueprint (presentation only).
    st.markdown("""
    <style>
    .method-intro{background:linear-gradient(120deg,#d8ecfc,#eaf5ff);border:1px solid #b8d5ed;border-radius:18px;padding:24px 28px;margin:12px 0 20px;box-shadow:0 10px 26px rgba(30,80,130,.07)}
    .method-eyebrow{font-size:12px;letter-spacing:2px;font-weight:800;color:#35749e;text-transform:uppercase}
    .method-heading{font-size:29px;font-weight:850;color:#173653;margin:9px 0}
    .method-copy{font-size:15px;line-height:1.75;color:#47617a;max-width:800px}
    .method-grid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:14px;margin:18px 0 24px}
    .method-card{background:#f8fcff;border:1px solid #bcd8ee;border-radius:16px;padding:20px;box-shadow:0 7px 18px rgba(27,74,119,.06)}
    .method-tag{font-size:12px;letter-spacing:1px;font-weight:850;margin-bottom:12px}
    .method-card h3{font-size:18px;color:#173653;margin:0 0 10px}
    .method-card p{color:#536f88;line-height:1.65;font-size:14px;margin:0}
    .method-section{font-size:21px;font-weight:800;color:#1b3f5c;margin:25px 0 12px}
    .method-flow{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:10px;margin:10px 0 20px}
    .method-step{background:#e2f0fa;border:1px solid #b9d5e9;border-radius:13px;padding:15px 12px;color:#264c6c;font-size:13px;line-height:1.5}
    .method-step b{display:block;font-size:15px;color:#183d5a;margin-bottom:5px}
    .method-notes{display:grid;grid-template-columns:1fr 1fr;gap:13px;margin:18px 0}
    .method-note{background:#f8fcff;border:1px solid #c6dced;border-left:4px solid #6ca8d0;border-radius:12px;padding:16px 18px;color:#46647d;line-height:1.65;font-size:14px}
    .method-note strong{color:#204e70}
    @media(max-width:850px){.method-grid,.method-flow{grid-template-columns:1fr 1fr}.method-notes{grid-template-columns:1fr}}
    @media(max-width:560px){.method-grid,.method-flow{grid-template-columns:1fr}}
    </style>
    <div class="method-intro">
      <div class="method-eyebrow">Research intelligence center / methodology</div>
      <div class="method-heading">From on-chain observations to risk intelligence</div>
      <div class="method-copy">DeFiRisk-Lab nghiên cứu rủi ro thanh lý trong DeFi lending (Aave V3 / Compound), kết hợp dữ liệu thanh lý quan sát với kết quả mô phỏng stress và cascade đã tính sẵn.</div>
    </div>
    <div class="method-grid">
      <div class="method-card"><div class="method-tag" style="color:#8056d4">01 / Q1 · BORROWER</div><h3>Borrower Risk</h3><p>Health Factor, tài sản thế chấp, dư nợ và nhóm vị thế khi dữ liệu hỗ trợ.</p></div>
      <div class="method-card"><div class="method-tag" style="color:#ce8726">02 / Q2 · STRESS</div><h3>Stress Scenarios</h3><p>Cú sốc ETH, BTC và depeg; đánh giá vị thế và dư nợ có nguy cơ theo các kịch bản sẵn có.</p></div>
      <div class="method-card"><div class="method-tag" style="color:#c24c72">03 / Q3 · CASCADE</div><h3>Liquidation Cascade</h3><p>Thực thi thanh lý theo vòng, nợ hoàn trả, lợi nhuận liquidator và slippage mô phỏng.</p></div>
    </div>
    <div class="method-section">Research workflow</div>
    <div class="method-flow">
      <div class="method-step"><b>01 · Observations</b>Dữ liệu thanh lý và trạng thái vị thế</div>
      <div class="method-step"><b>02 · Risk Metrics</b>Health Factor và mức độ phơi nhiễm</div>
      <div class="method-step"><b>03 · Stress Tests</b>Các cú sốc giá và depeg</div>
      <div class="method-step"><b>04 · Execution</b>Kết quả cascade theo vòng</div>
    </div>
    <div class="method-section">Scientific interpretation</div>
    <div class="method-notes">
      <div class="method-note"><strong>Observed vs simulated</strong><br>Historical là dữ liệu thanh lý quan sát. Stress và Cascade là kết quả mô phỏng, không phải các sự kiện on-chain đã xảy ra.</div>
      <div class="method-note"><strong>Research limitations</strong><br>Độ bao phủ và tính đúng đắn của mô hình chưa được dashboard xác nhận. Không suy diễn bad debt khi dữ liệu không có chỉ số tương ứng.</div>
    </div>
    <div class="method-section">Data provenance</div>
    """, unsafe_allow_html=True)
    paths={
      'Borrower snapshot':'data/processed/fact_wallet_summary.parquet',
      'Historical liquidations':'data/processed/fact_historical_liquidations.parquet',
      'Stress research':'n5_completion_20261026/n4_research/stress_summary.parquet',
      'N5 execution summary':'n5_completion_20261026/approved_package_with27/execution_summary.parquet',
      'N5 execution rounds':'n5_completion_20261026/approved_package_with27/execution_rounds.parquet',
      'N5 execution transactions':'n5_completion_20261026/approved_package_with27/execution_transactions.parquet',
      'Legacy contract output (empty)':'tables/out_simulation_results.parquet',
    }
    records=[]
    for name,relative in paths.items():
        p=locate(relative,Path(relative).name)
        records.append({'Dataset':name,'File':relative,'Available':bool(p),'Bytes':p.stat().st_size if p else None})
    render_aurora_table(pd.DataFrame(records),'Research data provenance','methodology_sources',accent='#476682',subtitle='')
