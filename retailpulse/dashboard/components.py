"""Consistent page headers, KPI cards, tables, labels and export controls."""
from datetime import datetime, timezone
from html import escape
import pandas as pd
import streamlit as st
from .formatting import currency, number, percent, csv_bytes

STYLE='''<style>
.block-container {max-width:1480px;padding-top:2rem;padding-bottom:3rem;}
[data-testid="stSidebar"] {border-right:1px solid #E3E8ED;}
h1,h2,h3 {letter-spacing:-.035em;color:#172C3C;}
h1 {font-size:2rem !important;font-weight:650 !important;}
[data-testid="stMetric"] {background:#FFFFFF;border:1px solid #E2E8ED;border-radius:12px;padding:18px 20px;min-height:132px;}
[data-testid="stMetricLabel"] {color:#607583;font-size:12px;}
[data-testid="stMetricValue"] {font-size:1.8rem;font-weight:650;}
[data-testid="stVerticalBlockBorderWrapper"] {border-radius:12px;}
.rp-kicker {font-size:11px;text-transform:uppercase;letter-spacing:.16em;color:#157A70;font-weight:700;margin-bottom:8px;}
.rp-footer {font-size:12px;color:#657987;border-top:1px solid #E2E8ED;padding-top:16px;margin-top:24px;}
@media(max-width:700px){.block-container{padding-left:1rem;padding-right:1rem;}[data-testid="stMetricValue"]{font-size:1.4rem;}}
</style>'''


def header(title, subtitle):
    st.markdown('<div class="rp-kicker">RetailPulse / Decision workspace</div>',unsafe_allow_html=True)
    st.title(title)
    st.caption(subtitle)


def card(container,label,value,kind='money',delta=None,help=None,delta_points=False):
    formatted=currency(value,True) if kind=='money' else percent(value) if kind=='percent' else number(value,2 if kind=='decimal' else 0)
    change=None if delta is None else number(delta,2)+(' pp' if delta_points else '%')+' vs previous period'
    container.metric(label,formatted,delta=change,help=help,border=False)


def notices(payload):
    for notice in payload.get('notices',[]): st.info(notice)


def plot(fig,key):
    if fig is None: st.info('No matching observations for this selection.')
    else: st.plotly_chart(fig,use_container_width=True,key=key,config={'displayModeBar':False,'responsive':True})


def table(rows,key,limit=1000):
    if not rows:
        st.info('No matching rows. Try a wider date range or reset filters.')
        return
    # Money stays exact in CSV. Only formatted UI cells are rounded for reading.
    formatted=[]
    for row in rows[:limit]:
        formatted.append({k:(currency(v) if k in ('net_sales','gross_profit','net_cogs','booked_aov','booked_merchandise','merchandise_credits','customer_refunds_including_gst')
                               else percent(v) if k.endswith('_percent') else v) for k,v in row.items()})
    st.dataframe(pd.DataFrame(formatted),hide_index=True,use_container_width=True)
    if len(rows)>limit: st.caption(f'Showing {limit:,} of {len(rows):,} filtered rows. Download contains all filtered rows.')
    st.download_button('Download filtered CSV',csv_bytes(rows),file_name=f'retailpulse-{key}.csv',mime='text/csv',key='download_'+key)


def label_rows(rows, metadata, kind):
    maps={'products':{p['sku_id']:f"{p['product_name']} · {p['color']} / {p['size']}" for p in metadata['products']},
          'categories':{p['category_id']:p['category_name'] for p in metadata['products']},
          'stores':{p['location_id']:p['name'] for p in metadata['stores']},
          'channels':{'POS':'Physical stores','WEB':'Website','APP':'Mobile app'}}
    key={'products':'sku_id','categories':'category_id','stores':'store_id','channels':'channel'}[kind]
    return [{**r,'label':maps[kind].get(r[key],'Online / no originating store')} for r in rows]


def freshness(metadata, mode):
    if mode=='demo':
        st.caption('DEMO · 500 synthetic orders · frozen, validated fixture · no database connection')
    else:
        timestamp=metadata['extraction_started_at']
        age=(datetime.now(timezone.utc)-timestamp).total_seconds()/3600
        st.caption(f"LOCAL · Snapshot {metadata['snapshot_id'][:8]} · Extracted {timestamp:%d %b %Y, %H:%M UTC} · {age:.1f} hours old")
        if age>24: st.warning('This warehouse reflects an extraction more than 24 hours old. Refresh outside the read-only dashboard when needed.')
        if metadata.get('latest_snapshot_id')!=metadata['snapshot_id']: st.warning('A newer lake snapshot exists; this dashboard still shows the last published warehouse.')
