"""Plotly rendering only. Aggregation and KPI calculations happen in SQL."""
from decimal import Decimal
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go

PALETTE=['#157A70','#E7A858','#456A94','#A78AA8','#86A7A0','#C46E69']


def frame(rows):
    return pd.DataFrame([{k:float(v) if isinstance(v,Decimal) else v for k,v in r.items()} for r in rows])


def polish(fig, height=330):
    fig.update_layout(template='plotly_white',height=height,font=dict(family='Arial, sans-serif',color='#405262',size=12),
        margin=dict(l=12,r=12,t=20,b=15),paper_bgcolor='rgba(0,0,0,0)',plot_bgcolor='rgba(0,0,0,0)',
        legend=dict(orientation='h',y=-.18,title=None),colorway=PALETTE,hovermode='x unified')
    fig.update_xaxes(showgrid=False,title=None)
    fig.update_yaxes(gridcolor='#E9EDF1',zerolinecolor='#DEE5EA')
    return fig


def trend(rows, y='net_sales', label='Net sales · ₹'):
    df=frame(rows)
    if df.empty: return None
    fig=px.line(df,x='period_start',y=y,markers=True,color_discrete_sequence=PALETTE)
    fig.update_traces(line=dict(width=2.5),marker=dict(size=4),hovertemplate='%{x|%d %b %Y}<br>%{y:,.2f}<extra></extra>')
    fig.update_yaxes(title=label)
    return polish(fig)


def bars(rows, category, value, label, horizontal=True):
    df=frame(rows)
    if df.empty: return None
    df=df.copy(); df[category]=df[category].fillna('Online / no store').astype(str)
    fig=px.bar(df,x=value if horizontal else category,y=category if horizontal else value,
               orientation='h' if horizontal else 'v',color_discrete_sequence=PALETTE,text_auto='.3s')
    if horizontal:
        fig.update_yaxes(autorange='reversed',title=None); fig.update_xaxes(title=label)
    else: fig.update_yaxes(title=label)
    fig.update_traces(marker_line_width=0,textposition='outside',cliponaxis=False)
    return polish(fig)


def cohorts(rows):
    df=frame(rows)
    if df.empty: return None
    matrix=df.pivot(index='cohort_month',columns='activity_month',values='active_customers')
    fig=go.Figure(go.Heatmap(z=matrix.values,x=[str(x)[:7] for x in matrix.columns],y=[str(y)[:7] for y in matrix.index],
                            colorscale=[[0,'#EEF5F3'],[1,'#157A70']],hovertemplate='First observed: %{y}<br>Active month: %{x}<br>Customers: %{z}<extra></extra>'))
    fig.update_yaxes(title='First observed booking month');fig.update_xaxes(title='Activity month')
    return polish(fig,380)
