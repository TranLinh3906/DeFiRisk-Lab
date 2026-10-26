"""Focused wallet investigation and controlled comparisons of existing outputs."""
from pathlib import Path
from html import escape
import json
import hashlib
import numpy as np
import pandas as pd
import plotly.express as px
px.defaults.color_discrete_sequence = ['#357CC1', '#63A7D7', '#24527F', '#82C5D5', '#D49A42', '#C65B72', '#6088A9']
import streamlit as st
from dashboard_spaces import read_source

STRESS='outputs/n5_completion_20261026/n4_research/stress_positions.parquet'
RUNS='outputs/n5_completion_20261026/approved_package_with27/execution_summary.parquet'
TX='outputs/n5_completion_20261026/approved_package_with27/execution_transactions.parquet'
TOP='outputs/tables/n3_m6_top50_wallets_AB1B2.csv'


def export_record(root, name, selection, records, sources, limitations, plots=None):
    payload=dict(selection=selection,records=records,sources=[dict(path=p,sha256=hashlib.sha256((Path(root)/p).read_bytes()).hexdigest()) for p in sources],limitations=limitations)
    text=json.dumps(payload,ensure_ascii=False,indent=2,default=str)
    st.download_button('Tải hồ sơ khám phá · JSON',text,file_name=name+'.json',mime='application/json',key=name+'_download')
    table=pd.DataFrame(records)
    st.download_button('Tải kết quả · CSV',table.to_csv(index=False).encode('utf-8-sig'),file_name=name+'.csv',mime='text/csv',key=name+'_csv')
    charts=''.join(fig.to_html(full_html=False,include_plotlyjs=True if i==0 else False) for i,fig in enumerate(plots or []))
    report='<!doctype html><html lang="vi"><meta charset="utf-8"><title>DeFiRisk · exploration record</title><body style="font-family:Arial;max-width:1100px;margin:40px auto;padding:20px;color:#23405c"><h1>DeFiRisk · hồ sơ khám phá</h1><h2>Lựa chọn</h2><pre>'+escape(json.dumps(selection,ensure_ascii=False,indent=2,default=str))+'</pre>'+charts+'<h2>Kết quả</h2>'+table.to_html(index=False,escape=True)+'<h2>Giới hạn</h2><p>'+escape(limitations)+'</p><h2>Nguồn và hash</h2><pre>'+escape(json.dumps(payload['sources'],ensure_ascii=False,indent=2))+'</pre></body></html>'
    st.download_button('Tải báo cáo có biểu đồ · HTML',report,file_name=name+'.html',mime='text/html',key=name+'_html')
    with st.expander('Nguồn và phạm vi hồ sơ'):
        st.code('\n'.join(sources),language=None)
        st.caption(limitations)
        st.caption('HTML giữ biểu đồ tương tác, lựa chọn và nguồn; JSON giữ số liệu/hash; CSV để phân tích tiếp. Báo cáo không phải biên bản nghiệm thu.')


def wallet_flow(root):
    st.subheader('Một ví · từ vị thế đến cú sốc và thanh lý')
    top=read_source(str(Path(root)/TOP))
    snapshots=['A','B1','B2']
    preferred=st.session_state.get('_flow_snapshot','A')
    sid=st.radio('Snapshot',snapshots,index=snapshots.index(preferred),horizontal=True,key='flow_snapshot')
    sample=top.loc[top.snapshot_id==sid].sort_values('debt_usd',ascending=False)
    wallets=sample.wallet.tolist();preferred=st.session_state.get('_flow_wallet')
    wallet=st.selectbox('Ví trong mẫu N3 · chọn từ bản đồ hoặc tại đây',wallets,
        index=wallets.index(preferred) if preferred in wallets else 0,key='flow_wallet_'+sid)
    row=sample.loc[sample.wallet==wallet].iloc[0]
    st.code(wallet,language=None)
    data=read_source(str(Path(root)/STRESS))
    data=data.loc[(data.snapshot_block==row.snapshot_block)&(data.wallet==wallet)].copy()
    if data.empty:
        st.info('Ví này chưa có hàng N4 tại snapshot đang chọn; không thay bằng ví khác.');return
    scenario=st.selectbox('Cú sốc đã tính cho chính ví này',sorted(data.scenario_id.unique()),key='flow_scenario')
    result=data.loc[data.scenario_id==scenario]
    if len(result)!=1:
        st.warning('Kết quả chưa duy nhất; không tự chọn một hàng.');st.dataframe(result);return
    result=result.iloc[0]
    before=float(result.hf_before);after=float(result.hf_after)
    c1,c2,c3=st.columns(3)
    c1.metric('HF trước shock',f'{before:.3f}')
    c2.metric('HF sau shock',f'{after:.3f}',delta=f'{after-before:+.3f}',delta_color='normal')
    c3.metric('Debt at risk · N4',f'${result.debt_at_risk_usd:,.0f}')
    status='chuyển từ HF ≥ 1 sang HF < 1' if before>=1 and after<1 else 'vẫn có HF < 1' if before<1 and after<1 else 'có HF ≥ 1 sau shock'
    st.info(f'Trong kịch bản {scenario}, ví {status}. HF thay đổi {after-before:+.3f}; đây là kết quả mô hình N4, chưa phải giao dịch thanh lý thực tế.')
    comparison=pd.DataFrame({'Trạng thái':['Trước shock','Sau shock'],'Health Factor':[before,after]})
    fig=px.bar(comparison,x='Trạng thái',y='Health Factor',color='Trạng thái',color_discrete_sequence=['#759fc8','#438EC2'])
    fig.add_hline(y=1,line_dash='dash',line_color='#c44d69',annotation_text='HF = 1')
    fig.update_layout(height=300,showlegend=False)
    st.plotly_chart(fig,width='stretch',key='wallet_before_after')
    st.caption('Cùng ví, snapshot và scenario. Mẫu top-wallet có chọn lọc; không dùng để ước lượng tỷ lệ toàn bộ người vay.')
    st.markdown('#### Đối chiếu thực thi · cùng ví và cú sốc')
    runs=read_source(str(Path(root)/RUNS))
    available=runs.loc[(runs.snapshot_block==row.snapshot_block)&(runs.scenario_id==scenario)]
    sources=[TOP,STRESS];records=[dict(layer='N4',**result.to_dict())]
    if available.empty:
        st.info('Chưa có cấu hình thực thi N5 khớp snapshot/scenario.');return
    variants=available.variant.unique().tolist()
    variant=st.selectbox('Cấu hình thực thi N5',variants,key='flow_variant')
    run=available.loc[available.variant==variant]
    if len(run)!=1:
        st.info('Cần phân biệt thêm run; chưa ghép giao dịch.');return
    run=run.iloc[0]
    if st.checkbox('Tra giao dịch mô phỏng cho ví này',key='flow_load_execution'):
        tx=read_source(str(Path(root)/TX))
        keys=[c for c in ['snapshot_id','snapshot_block','scenario_id','variant','method'] if c in tx]
        # Require unique run identity in the entire source, not merely the filtered view.
        if runs.duplicated(keys).any():
            st.warning('Định danh giao dịch chưa đủ phân biệt run; không tự ghép.')
        else:
            tx=tx.loc[tx.wallet_address==wallet]
            for key in keys:tx=tx.loc[tx[key]==run[key]]
            if tx.empty:
                st.info('Không thấy giao dịch của ví trong cấu hình này. Không suy ra ví an toàn: có thể nằm ngoài cohort hoặc không có quote/lợi nhuận phù hợp.')
            else:
                st.metric('Debt repaid mô phỏng của ví',f'${tx.repaid_debt_usd.sum():,.0f}')
                st.dataframe(tx[['round','repaid_debt_usd','sold_usd','slippage_pct','liquidator_profit_usd']],hide_index=True)
                records += [dict(layer='N5 transaction',**r) for r in tx.to_dict('records')]
            sources.append(TX)
    export_record(root,'wallet_investigation',dict(snapshot=sid,wallet=wallet,scenario=scenario,variant=variant),records,sources,
        'HF trước/sau là N4; giao dịch là thực thi mô phỏng N5. Ghép danh tính không biến hai mô hình thành một chuỗi causal đã kiểm định; không có giao dịch không đồng nghĩa không rủi ro.',plots=[fig])


def controlled_comparison(root):
    st.subheader('So sánh có kiểm soát · chỉ đổi một yếu tố')
    data=read_source(str(Path(root)/RUNS))
    sid=st.selectbox('Snapshot',sorted(data.snapshot_id.unique()),key='control_snapshot')
    source=data.loc[data.snapshot_id==sid]
    scenario=st.selectbox('Scenario',sorted(source.scenario_id.unique()),key='control_scenario')
    source=source.loc[source.scenario_id==scenario]
    factor=st.radio('Yếu tố thay đổi',['gas_usd','ordering','fee_policy'],horizontal=True,
        format_func=lambda v:{'gas_usd':'Gas cost','ordering':'Thứ tự thanh lý','fee_policy':'Chính sách phí'}[v],key='control_factor')
    dimensions=['snapshot_id','snapshot_block','scenario_id','method','ordering','pair_rule','seed','oracle_feedback','freeze_proven','pool_state','gas_usd','fee_policy']
    fixed=[k for k in dimensions if k!=factor and k in source]
    # variant is the run's label, not an additional economic parameter.
    groups=[]
    for _,group in source.groupby(fixed,dropna=False):
        if group[factor].nunique()>1 and not group.duplicated([factor]).any():groups.append(group)
    if not groups:
        st.info('Chưa có cặp chỉ khác yếu tố này và giữ nguyên toàn bộ điều kiện còn lại. Không dùng cặp gần giống.');return
    if len(groups)>1:
        index=st.selectbox('Nhóm điều kiện cố định',range(len(groups)),format_func=lambda i:' / '.join(str(groups[i].iloc[0][c]) for c in fixed if groups[i][c].nunique()==1),key='control_group')
    else:index=0
    group=groups[index]
    options=sorted(group[factor].unique().tolist())
    left,right=st.columns(2)
    with left:
        a=st.selectbox('Đối chứng',options,key='control_baseline_'+factor)
    with right:
        b=st.selectbox('Phương án so sánh',options,index=len(options)-1,key='control_alternative_'+factor)
    arow=group.loc[group[factor]==a].iloc[0];brow=group.loc[group[factor]==b].iloc[0]
    st.caption('Cố định: '+' · '.join(f'{c}={arow[c]}' for c in fixed)+'. Variant là nhãn cấu hình; có thể đổi tên cùng yếu tố.')
    metrics={'simulated_repaid_usd':'Nợ hoàn trả (USD)','executed_liquidations':'Số thanh lý','liquidator_profit_usd':'Lợi nhuận liquidator (USD)','unknown_candidate_attempts':'Lượt thử chưa rõ kết quả'}
    comparison=[]
    for key,label in metrics.items():
        delta=brow[key]-arow[key]
        comparison.append(dict(metric=label,baseline=arow[key],alternative=brow[key],difference=delta))
    st.dataframe(pd.DataFrame(comparison),hide_index=True)
    metric=st.radio('Chỉ số biểu đồ',list(metrics),format_func=lambda k:metrics[k],horizontal=True,key='control_metric')
    bars=pd.DataFrame({'Phương án':['Đối chứng','So sánh'],'Giá trị':[arow[metric],brow[metric]]})
    fig=px.bar(bars,x='Phương án',y='Giá trị',color='Phương án',labels={'Giá trị':metrics[metric]},color_discrete_sequence=['#7a9ec6','#3C89BF'])
    st.plotly_chart(fig,width='stretch',key='control_plot')
    delta=brow.simulated_repaid_usd-arow.simulated_repaid_usd
    st.info(f'Đổi {factor} từ {a} sang {b}: nợ hoàn trả mô phỏng chênh ${delta:+,.0f}. Những chiều được ghi trong bảng nguồn còn lại giữ nguyên. Chênh lệch là bằng chứng có điều kiện trong mô hình, không phải hiệu quả can thiệp thực tế.')
    st.caption('Nguồn execution_summary chưa cung cấp tổn thất ròng giao thức và slippage tổng hợp; không thay bằng lợi nhuận liquidator.')
    export_record(root,'controlled_comparison',dict(snapshot=sid,scenario=scenario,factor=factor,baseline=a,alternative=b),[arow.to_dict(),brow.to_dict()],[RUNS],
        'Kiểm soát theo toàn bộ chiều cấu hình có trong bảng. Không loại trừ các giả định/chiều ẩn chưa lưu. So sánh mô hình tính sẵn, không chạy mới và không chứng minh nhân quả thực tế.',plots=[fig])
