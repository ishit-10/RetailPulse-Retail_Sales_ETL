import streamlit as st
from .. import components as ui, charts


def render(data,meta):
    ui.header('Store & channel performance','Compare selling locations fairly. Select a store in the sidebar to drill into its trend.')
    ui.notices(data)
    rows=ui.label_rows(data['stores'],meta,'stores')
    stores={s['location_id']:s for s in meta['stores']}
    for r in rows:
        store=stores.get(r['store_id'],{})
        r['city']=store.get('city','Not attributed')
        r['state']=next((s['name'] for s in meta['states'] if s['state_id']==store.get('state_id')),'Not attributed')
    sort=st.selectbox('Rank stores by',['net_sales','opd','booked_aov','gross_margin_percent'],format_func=lambda s:s.replace('_',' ').title(),key='store_sort')
    ranked=sorted(rows,key=lambda r:(r[sort] is not None,r[sort] or 0),reverse=True)
    with st.container(border=True):
        st.subheader('Selling-location scorecard')
        st.caption('Online orders retain a separate “no originating store” row. State is available; region is not part of the source.')
        ui.table(ranked,'store-scorecard')
    a,b=st.columns(2)
    with a,st.container(border=True):
        st.subheader('Channel contribution')
        ui.plot(charts.bars(ui.label_rows(data['channels'],meta,'channels'),'label','net_sales','Net sales · ₹'),'stores_channels')
    with b,st.container(border=True):
        st.subheader('Selected sales trend')
        ui.plot(charts.trend(data['trend']),'stores_trend')
    with st.container(border=True):
        st.subheader('Footfall & conversion')
        st.caption('Distinct completed POS orders / valid entries at matching store and completion date. Missing days are not zero traffic; covered days are shown.')
        footfall=ui.label_rows(data['footfall'],meta,'stores')
        ui.table(footfall,'footfall')
    with st.expander('Stores to investigate'):
        st.caption('Lowest five net-sales stores in the current scope, not a claim of missed targets. Store size, opening days and trading area are not normalized.')
        physical=[r for r in rows if r['store_id'] is not None]
        ui.table(sorted(physical,key=lambda r:r['net_sales'])[:5],'stores-to-investigate')
    with st.expander('Channel details'):
        ui.table(ui.label_rows(data['channels'],meta,'channels'),'channels')
