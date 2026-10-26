"""Interactive research spaces using existing observed and precomputed artifacts."""
from pathlib import Path
import numpy as np
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import streamlit as st


@st.cache_data(show_spinner=False)
def read_source(path):
    path = Path(path)
    return pd.read_csv(path) if path.suffix == '.csv' else pd.read_parquet(path)


def ecosystem(root):
    st.subheader('The Observatory · bản đồ phơi nhiễm')
    sid = st.radio('Snapshot mạng lưới', ['A', 'B1', 'B2'], horizontal=True, key='space_network_snapshot')
    source = Path(root)/f'outputs/n5_preparation/n5_AB1B2_20261025_compound_complete/{sid}/graph_network_edges_candidate.parquet'
    top = read_source(str(Path(root)/'outputs/tables/n3_m6_top50_wallets_AB1B2.csv'))
    wallets = top.loc[top.snapshot_id == sid].nlargest(12, 'debt_usd').copy()
    if not source.exists() or wallets.empty:
        st.info('Chưa có dữ liệu mạng tương ứng.'); return
    edges = read_source(str(source))
    wallet_nodes = ['wallet:'+str(v) for v in wallets.wallet]
    links = edges.loc[edges.source_node_id.isin(wallet_nodes) | edges.target_node_id.isin(wallet_nodes)].copy()
    nodes = sorted(set(links.source_node_id)|set(links.target_node_id))
    token_nodes = [v for v in nodes if v not in wallet_nodes]
    coords = {}
    for group, radius in [(wallet_nodes, 1), (token_nodes, 0.48)]:
        for i, node in enumerate(group):
            angle = 2*np.pi*i/max(1,len(group))
            coords[node]=(radius*np.cos(angle), radius*np.sin(angle))
    fig = go.Figure()
    for kind, color in [('collateral_exposure','#8e58a1'),('debt_exposure','#a385cb')]:
        xx=[]; yy=[]
        for row in links.loc[links.edge_type == kind].itertuples():
            if row.source_node_id in coords and row.target_node_id in coords:
                x,y=coords[row.source_node_id]; x2,y2=coords[row.target_node_id]
                xx += [x,x2,None]; yy += [y,y2,None]
        fig.add_trace(go.Scatter(x=xx,y=yy,mode='lines',line=dict(color=color,width=1),name=kind,hoverinfo='skip'))
    wallet_map=wallets.set_index('wallet')
    visible=[n for n in nodes if n in coords]
    text=[];colors=[]
    for n in visible:
        if n.startswith('wallet:') and n[7:] in wallet_map.index:
            row=wallet_map.loc[n[7:]]
            text.append(f'Wallet: {n[7:]}<br>HF: {row.hf_value:.3f}<br>Debt: ${row.debt_usd:,.0f}<br>Collateral: ${row.counted_collateral_usd:,.0f}<br>Snapshot: {sid}<br>Source: n3_m6_top50_wallets_AB1B2.csv')
            colors.append('#c44d69' if row.hf_value < 1 else '#bb8b34' if row.hf_value < 1.1 else '#319c9b')
        else:
            text.append(n+'<br>Token exposure node; address from source graph'); colors.append('#ab7abc')
    fig.add_trace(go.Scatter(x=[coords[n][0] for n in visible],y=[coords[n][1] for n in visible],
        mode='markers',marker=dict(size=[17 if n.startswith('wallet:') else 12 for n in visible],color=colors,line=dict(width=2,color='white')),
        customdata=visible,text=text,hovertemplate='%{text}<extra></extra>',name='Wallets / tokens'))
    fig.update_layout(height=500,paper_bgcolor='#FFF9FD',plot_bgcolor='#FFF9FD',margin=dict(l=10,r=10,t=25,b=10),
        xaxis=dict(visible=False),yaxis=dict(visible=False,scaleanchor='x'),legend=dict(orientation='h'),clickmode='event+select')
    left,right=st.columns([2,1])
    with left:
        event=st.plotly_chart(fig,key='ecosystem_plot',on_select='rerun',selection_mode='points',width='stretch')
        st.caption('Mẫu tối đa 12 ví có dư nợ lớn trong top-wallet N3. Cạnh là phơi nhiễm ví–token có trong dữ liệu; không phải giao dịch hay bằng chứng lây lan. Bố trí vòng tròn chỉ để hiển thị. Chưa vẽ tuyến DEX/Compound vì chưa có cạnh được ghép cho mẫu này.')
    with right:
        selected=None
        if event and event.selection.points:
            node=event.selection.points[0].get('customdata','')
            if isinstance(node,str) and node.startswith('wallet:') and node[7:] in wallet_map.index:
                selected=node[7:]
        wallet=st.selectbox('Risk X-Ray · ví đang soi',wallets.wallet.tolist(),
            index=wallets.wallet.tolist().index(selected) if selected else 0,key='network_wallet_'+str(selected))
        row=wallet_map.loc[wallet]
        st.code(wallet,language=None)
        st.metric('Health Factor',f'{row.hf_value:.3f}')
        st.metric('Debt (USD)',f'${row.debt_usd:,.0f}')
        st.metric('Collateral (USD)',f'${row.counted_collateral_usd:,.0f}')
        st.caption(f'Snapshot {sid} · block {int(row.snapshot_block)}. Click điểm ví hoặc chọn tại đây. Hover để xem panel nhanh.')
    with st.expander('Bằng chứng mạng và giới hạn'):
        st.code(str(source.relative_to(root)),language=None)
        st.dataframe(links.head(30),hide_index=True)
        st.caption('Candidate graph / chưa nghiệm thu nghiên cứu; HF và dư nợ lấy từ top-wallet N3 cùng snapshot, cạnh lấy từ gói chuẩn bị.')


def crisis_replay(root):
    st.subheader('The Crisis Time Machine · phát lại lịch sử')
    sid=st.radio('Cửa sổ khủng hoảng',['B1 · Aug 2024','B2 · Oct 2025'],horizontal=True,key='crisis_window')
    start,end = ('2024-08-03','2024-08-09') if sid.startswith('B1') else ('2025-10-08','2025-10-13')
    start,end=pd.Timestamp(start,tz='UTC'),pd.Timestamp(end,tz='UTC')
    prices=read_source(str(Path(root)/'data/n3_history_2024_2026/final/market_crashes_hourly.parquet'))
    times=pd.to_datetime(prices.timestamp_utc,utc=True)
    prices=prices.loc[(prices.asset=='ETH')&(times>=start)&(times<end)].sort_values('timestamp_utc')
    if prices.empty:
        st.info('Không có giá ETH hourly trong cửa sổ này.');return
    events=read_source(str(Path(root)/'data/n3_history_2024_2026/final/n3_liquidations_observed.parquet')).copy()
    events['hour']=pd.to_datetime(events.event_timestamp,utc=True).dt.floor('h')
    events=events.loc[(events.hour>=start)&(events.hour<end)]
    hourly=events.groupby('hour').agg(events=('hour','size'),debt_repaid_usd=('debt_repaid_usd',lambda x:x.sum(min_count=1))).reset_index()
    moments=prices.timestamp_utc.tolist()
    cutoff=st.select_slider('Dừng tại thời điểm UTC',moments,value=moments[-1],format_func=lambda x:str(x),key='crisis_cutoff_'+sid)
    p=prices.loc[prices.timestamp_utc<=cutoff]; e=hourly.loc[hourly.hour<=cutoff]
    fig=make_subplots(rows=2,cols=1,shared_xaxes=True,vertical_spacing=.12,subplot_titles=['ETH close · USD','Liquidation debt repaid · USD'])
    def traces(p,e):
        return [go.Scatter(x=p.timestamp_utc,y=p.close,name='ETH hourly close',line=dict(color='#319c9b',width=2)),
                go.Bar(x=e.hour,y=e.debt_repaid_usd,name='Observed liquidation',marker_color='#c36b85')]
    data=traces(p,e);fig.add_trace(data[0],row=1,col=1);fig.add_trace(data[1],row=2,col=1)
    frames=[]
    for i,t in enumerate(moments):
        if i%2==0 or i==len(moments)-1:
            frames.append(go.Frame(name=str(t),data=traces(prices.loc[prices.timestamp_utc<=t],hourly.loc[hourly.hour<=t]),traces=[0,1]))
    fig.frames=frames
    fig.update_xaxes(range=[moments[0],moments[-1]])
    fig.update_layout(height=530,paper_bgcolor='#FFF9FD',plot_bgcolor='#FFF9FD',margin=dict(t=70,b=80),
        legend=dict(orientation='h',y=1.2),updatemenus=[dict(type='buttons',direction='left',x=0,y=-.18,buttons=[
            dict(label='▶ Phát lại',method='animate',args=[None,dict(frame=dict(duration=180,redraw=True),transition=dict(duration=0),fromcurrent=False)]),
            dict(label='Ⅱ Dừng',method='animate',args=[[None],dict(mode='immediate',frame=dict(duration=0,redraw=False))])])])
    st.plotly_chart(fig,key='crisis_replay_plot',width='stretch')
    st.caption('Thanh thời gian chọn điểm dừng để soi bằng chứng. Play/Pause chỉ phát lại biểu đồ; không thay đổi bảng bằng chứng bên dưới. Khoảng giờ không có bản ghi giữ trống, không coi là không có thanh lý. Không mô phỏng lại lịch sử.')
    with st.expander('Bằng chứng tại điểm dừng đã chọn'):
        st.write(f'Cutoff UTC: {cutoff}')
        st.dataframe(events.loc[events.hour<=cutoff].tail(30),hide_index=True)
        st.caption('Nguồn: market_crashes_hourly.parquet (Binance ETH hourly), n3_liquidations_observed.parquet. Chưa thể biểu diễn áp lực thanh khoản hay depeg theo giờ từ hai bảng này.')


def scenario_journey(root):
    """One pinned, real precomputed configuration across four explanatory phases."""
    root=Path(root)
    base=root/'outputs/n5_completion_20261026'
    summaries=read_source(str(base/'approved_package_with27/execution_summary.parquet'))
    cases=summaries.copy()
    st.caption('Chọn một cấu hình để theo dõi xuyên suốt bốn bước. Chuyển bước là cách khám phá kết quả đã tính, không phải chạy mô hình mới.')
    for col,key in zip(st.columns(4),['snapshot_id','scenario_id','variant','method']):
        opts=sorted(cases[key].dropna().astype(str).unique())
        widget='journey_'+key
        if widget in st.session_state and st.session_state[widget] not in opts:
            del st.session_state[widget]
        choice=col.selectbox(key.replace('_',' ').title(),opts,key=widget)
        cases=cases.loc[cases[key].astype(str)==choice]
    if len(cases)!=1:
        st.info('Cấu hình còn nhiều chiều chưa phân biệt; xem bảng chi tiết trong chế độ Thực thi cascade.');return
    case=cases.iloc[0]
    identity='|'.join(str(case[k]) for k in ['snapshot_id','scenario_id','variant','method'])
    if st.session_state.get('journey_identity')!=identity:
        st.session_state.journey_identity=identity
        st.session_state.journey_phase=0
    phase=st.session_state.get('journey_phase',0)
    titles=['Observe','Shock','Liquidate','Cascade']
    descriptions=[('BORROWER BASELINE','Soi trạng thái vị thế trước cú sốc.'),
                  ('MARKET SHOCK','Đối chiếu Health Factor trước và sau cú sốc đã tính.'),
                  ('LIQUIDATION EXECUTION','Xem thanh lý được thực thi theo cơ chế và cấu hình đã chọn.'),
                  ('CASCADE ROUNDS','Theo dõi diễn biến theo vòng; không đồng nhất nhiều vòng với bằng chứng lây lan nhân quả.')]
    colors=['#61b6a5','#b59ae5','#e4b16b','#ed8cae']
    steps=''.join(f'<div class="journey-node {"journey-active" if i==phase else ""}" style="--phase:{colors[i]}"><b>{["◉","↘","≋","⌘"][i]}</b><span>{t}</span></div>'+('<span class="journey-arrow">›</span>' if i<3 else '') for i,t in enumerate(titles))
    st.markdown('''<style>
.journey-shell{background:linear-gradient(125deg,#291e35,#382440);border:1px solid #765283;border-radius:22px;padding:26px 30px;margin:16px 0 12px;animation:journeyReveal .35s ease-out}
.journey-top{display:flex;justify-content:space-between;font:12px monospace;letter-spacing:1.4px;color:#d6bce2}.journey-steps{display:flex;align-items:center;justify-content:space-between;margin:24px 0 28px}.journey-node{text-align:center;color:#c3adc9;font-size:13px}.journey-node b{display:block;border-radius:50%;width:58px;height:58px;line-height:58px;margin:0 auto 9px;background:#503c5d;font-size:24px;border:1px solid #7d6389;transition:all .3s}.journey-active{color:#fff!important}.journey-active b{background:var(--phase);color:#291e35;box-shadow:0 0 24px #d0a0d638;transform:scale(1.08)}.journey-arrow{color:#9a7da7}.journey-body{border-top:1px solid #765283;padding-top:22px}.journey-body h3{color:var(--phase)!important;font-size:24px!important;margin:0 0 10px!important}.journey-body p{color:#ead9f0!important;font-size:14px!important;line-height:1.7}
@keyframes journeyReveal{from{opacity:.65;transform:translateY(6px)}to{opacity:1;transform:translateY(0)}}
@media(prefers-reduced-motion:reduce){.journey-shell{animation:none}.journey-node b{transition:none;transform:none}}
@media(max-width:600px){.journey-shell{padding:20px 14px}.journey-node b{width:40px;height:40px;line-height:40px}.journey-node{font-size:11px}}
</style>'''+f'<div class="journey-shell"><div class="journey-top"><span>SCENARIO JOURNEY / {case.snapshot_id}</span><span>{phase+1} / 4</span></div><div class="journey-steps">{steps}</div><div class="journey-body" style="--phase:{colors[phase]}"><h3>{descriptions[phase][0]}</h3><p>{descriptions[phase][1]}</p></div></div>',unsafe_allow_html=True)
    def change(delta):
        st.session_state.journey_phase=max(0,min(3,st.session_state.get('journey_phase',0)+delta))
    c1,c2,c3=st.columns([1,2,1])
    c1.button('← Bước trước',disabled=phase==0,on_click=change,args=(-1,),key='journey_prev',width='stretch')
    c2.caption(f'{case.scenario_id} · {case.variant} · {case.method} · block {int(case.snapshot_block)}')
    c3.button('Bước tiếp →' if phase<3 else 'Đã đến bước cuối',disabled=phase==3,on_click=change,args=(1,),key='journey_next',width='stretch')
    positions=read_source(str(base/'n4_research/stress_positions.parquet'))
    positions=positions.loc[(positions.snapshot_block==case.snapshot_block)&(positions.scenario_id==case.scenario_id)].copy()
    rounds=read_source(str(base/'approved_package_with27/execution_rounds.parquet'))
    for key in ['snapshot_id','scenario_id','variant','method','snapshot_block']:
        rounds=rounds.loc[rounds[key]==case[key]]
    if phase in (0,1):
        if positions.empty:
            st.info('Chưa có dữ liệu vị thế tương ứng trong bảng N4.');return
        hf='hf_before' if phase==0 else 'hf_after'
        hf_values=pd.to_numeric(positions[hf],errors='coerce')
        c1,c2,c3=st.columns(3)
        c1.metric('Vị thế có HF',f'{hf_values.notna().sum():,}')
        c2.metric('HF < 1',f'{(hf_values<1).sum():,}')
        c3.metric('Dư nợ mẫu (USD)',f'${pd.to_numeric(positions.debt_usd,errors="coerce").sum():,.0f}')
        fig=go.Figure()
        fig.add_trace(go.Histogram(x=positions.hf_before,nbinsx=60,name='Trước shock',marker_color='#bba1d3',opacity=.6))
        if phase==1:
            fig.add_trace(go.Histogram(x=positions.hf_after,nbinsx=60,name='Sau shock',marker_color='#cf81a6',opacity=.6))
            st.caption('Sau shock là kết quả N4, chưa phải trạng thái ví sau từng vòng cascade.')
        fig.add_vline(x=1,line_color='#c54d72',line_dash='dash')
        fig.update_layout(barmode='overlay',height=340,paper_bgcolor='#FFF9FD',plot_bgcolor='#FFF9FD',xaxis_title='Health Factor',yaxis_title='Số vị thế')
        st.plotly_chart(fig,width='stretch',key='journey_hf')
    else:
        c1,c2,c3=st.columns(3)
        c1.metric('Thanh lý thực thi',f'{int(case.executed_liquidations):,}')
        c2.metric('Nợ hoàn trả mô phỏng',f'${case.simulated_repaid_usd:,.0f}')
        c3.metric('Lượt thử chưa rõ kết quả',f'{int(case.unknown_candidate_attempts):,}')
        if rounds.duplicated('round').any():
            st.warning('Nhiều run trùng số vòng; chưa ghép để tránh nhầm cấu hình.')
        elif not rounds.empty:
            rounds=rounds.sort_values('round')
            if phase==2:
                rounds=rounds.head(1)
                st.caption('Bước Liquidate tập trung vào vòng đầu; KPI phía trên là toàn run.')
            else:
                cutoff=st.select_slider('Dừng tại vòng',rounds['round'].tolist(),value=rounds['round'].max(),key='journey_round_'+identity)
                rounds=rounds.loc[rounds['round']<=cutoff]
            fig=make_subplots(specs=[[{'secondary_y':True}]])
            fig.add_trace(go.Bar(x=rounds['round'],y=rounds.executed_liquidations,name='Số thanh lý',marker_color='#bc82b7'),secondary_y=False)
            fig.add_trace(go.Scatter(x=rounds['round'],y=rounds.repaid_debt_usd,name='Nợ hoàn trả USD',mode='lines+markers',line_color='#9271ba'),secondary_y=True)
            fig.update_yaxes(title_text='Số thanh lý',secondary_y=False);fig.update_yaxes(title_text='USD',secondary_y=True)
            fig.update_xaxes(title='Vòng thực thi',dtick=1)
            fig.update_layout(height=350,paper_bgcolor='#FFF9FD',plot_bgcolor='#FFF9FD',legend=dict(orientation='h'))
            st.plotly_chart(fig,width='stretch',key='journey_execution')
        if phase==3:
            st.info('Chưa có danh tính ví được thanh lý theo từng vòng và cạnh DEX/Compound đã ghép cho panel này. Không tô mạng như thể đã chứng minh truyền nhiễm; hiện chỉ phát lại kết quả thực thi theo vòng.')
    with st.expander('Bằng chứng của hành trình này'):
        st.write('N4: stress_positions.parquet → N5: execution_summary.parquet / execution_rounds.parquet. Ghép theo snapshot block, scenario, variant và method; không suy ra quan hệ nhân quả từ thứ tự giao diện.')
        st.dataframe(cases,hide_index=True)
        st.dataframe(rounds,hide_index=True)
