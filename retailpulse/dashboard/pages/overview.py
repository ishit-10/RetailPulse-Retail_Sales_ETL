import streamlit as st
from .. import components as ui, charts


def render(data, meta):
    ui.header('Executive overview','Revenue, demand and profitability — one consistent warehouse snapshot.')
    s=data['summary']; cols=st.columns(3)
    ui.card(cols[0],'Net sales',s['net_sales'],delta=s['revenue_growth_percent'],help='Recognized merchandise minus issued merchandise credits; excludes GST.')
    ui.card(cols[1],'Booked GMV',s['gmv'],delta=s['gmv_growth_percent'],help='Original confirmed quantity × original price. Cancellations and returns do not reduce booked GMV.')
    ui.card(cols[2],'Orders per day',s['opd'],'decimal',delta=s['orders_growth_percent'],help='Confirmed orders divided by every calendar day selected, including zero-order days.')
    cols=st.columns(3)
    ui.card(cols[0],'Booked average order value',s['booked_aov'],delta=s['aov_growth_percent'],help='Initial post-discount merchandise / distinct confirmed orders; product filters narrow basket merchandise.')
    ui.card(cols[1],'Gross profit',s['gross_profit'],delta=s['profit_growth_percent'],help='Net sales minus FIFO net COGS; restockable returns reverse original cost.')
    ui.card(cols[2],'Gross margin',s['gross_margin_percent'],'percent',delta=s['margin_change_points'],delta_points=True)
    ui.notices(data)
    st.caption('Comparisons use the immediately preceding period of equal length and the same filters. — means no valid comparison denominator or insufficient history.')
    with st.container(border=True):
        st.subheader('Sales momentum')
        ui.plot(charts.trend(data['trend']), 'overview_trend')
    a,b=st.columns([1,1])
    with a,st.container(border=True):
        st.subheader('Where revenue comes from')
        rows=ui.label_rows(data['channels'],meta,'channels')
        ui.plot(charts.bars(rows,'label','net_sales','Net sales · ₹'), 'overview_channels')
    with b,st.container(border=True):
        st.subheader('Leading categories')
        rows=ui.label_rows(data['categories'],meta,'categories')[:8]
        ui.plot(charts.bars(rows,'label','net_sales','Net sales · ₹'), 'overview_categories')
    with st.container(border=True):
        st.subheader('Order-volume context')
        st.caption('Booking dates for orders; recognition and credit dates for revenue. They are deliberately not treated as the same event.')
        ui.plot(charts.trend(data['order_trend'],'orders','Confirmed orders'), 'overview_orders')
    with st.expander('Explore and download the revenue series'):
        ui.table(data['trend'],'overview-revenue')
