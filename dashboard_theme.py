"""One warm palette for every chart, including explicit trace colors and replay frames."""
import math
import re
import streamlit as st

CHART_COLORS = ['#B73D17', '#C38A08', '#C62828', '#76452B', '#FF5A36', '#E3AE46', '#7A766F']
WARM_SCALE = [[0,'#FFF1CF'],[.35,'#FFB800'],[.7,'#FF5A36'],[1,'#C62828']]


def palette_color(value):
    if not isinstance(value,str): return value
    match=re.fullmatch(r'#([0-9a-fA-F]{6})([0-9a-fA-F]{2})?',value)
    if not match:
        return {'blue':'#B73D17','purple':'#76452B','pink':'#C62828','cyan':'#C38A08','teal':'#C38A08','green':'#76452B'}.get(value.lower(),value)
    rgb=tuple(int(match[1][i:i+2],16) for i in (0,2,4))
    # White/black and almost neutral outlines are not data series.
    if max(rgb)-min(rgb)<20: return value
    nearest=min(CHART_COLORS,key=lambda h:sum((int(h[i+1:i+3],16)-rgb[n])**2 for n,i in enumerate((0,2,4))))
    return nearest


def _warm_properties(value, key=''):
    if key=='colorscale':return WARM_SCALE
    if isinstance(value,dict):return {k:_warm_properties(v,k) for k,v in value.items()}
    if key in ('color','fillcolor'):
        if isinstance(value,str):
            rgba=re.fullmatch(r'rgba\(\s*\d+\s*,\s*\d+\s*,\s*\d+\s*,\s*([.\d]+)\s*\)',value)
            return 'rgba(255,90,54,'+rgba[1]+')' if rgba else palette_color(value)
        if isinstance(value,(list,tuple)) and all(isinstance(v,str) for v in value):return [palette_color(v) for v in value]
    return value


def apply_warm_theme(fig):
    for trace in list(fig.data)+[trace for frame in fig.frames for trace in frame.data]:
        trace.update(_warm_properties(trace.to_plotly_json()))
        if trace.type in ('heatmap','contour'):
            trace.update(colorscale=WARM_SCALE)
        name=str(trace.name or '').lower()
        # Same meaning receives the same color throughout the app.
        if name=='collateral_exposure':trace.update(line=dict(color='#B73D17',width=2))
        elif name=='debt_exposure':trace.update(line=dict(color='#C38A08',width=2))
        elif name=='eth hourly close':trace.update(line=dict(color='#B73D17',width=2.5))
        elif name=='observed liquidation':trace.update(marker=dict(color='#C62828'))
    fig.update_layout(
        template='plotly_white', paper_bgcolor='#FFFCF7', plot_bgcolor='#FFFCF7',
        colorway=CHART_COLORS,font=dict(family='Arial, sans-serif',size=14,color='#3F2A1E'),
        hoverlabel=dict(bgcolor='#493124',font_color='#FFF7EC',font_size=14),
        legend=dict(font=dict(color='#493124',size=13)),
    )
    fig.update_xaxes(gridcolor='#E9D8C0',zerolinecolor='#C7A77F',tickfont=dict(color='#5B4130'),title_font=dict(color='#493124'))
    fig.update_yaxes(gridcolor='#E9D8C0',zerolinecolor='#C7A77F',tickfont=dict(color='#5B4130'),title_font=dict(color='#493124'))
    return fig


def themed_plot(fig, *args, **kwargs):
    apply_warm_theme(fig)
    kwargs['theme']=None
    return st.plotly_chart(fig,*args,**kwargs)
