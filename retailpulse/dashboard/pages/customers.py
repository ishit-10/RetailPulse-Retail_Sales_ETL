import streamlit as st
from .. import components as ui, charts
from ..formatting import number


def render(data,meta):
    ui.header('Customer & order analytics','Understand ordering behavior without displaying names, contact details or individual customer records.')
    ui.notices(data)
    cols=st.columns(3)
    customer=data['customer_summary'][0]
    ui.card(cols[0],'Purchasing customers',customer['purchasing_customers'],'number',help='Distinct identified customers with a recognized invoice unit in the selected period and scope. Guests are excluded.')
    ui.card(cols[1],'Orders per ordering customer',customer['orders_per_customer'],'decimal',help='Confirmed orders / distinct identified ordering customers. Includes later-cancelled orders; guest orders excluded.')
    ui.card(cols[2],'Cancellation rate',data['summary']['cancellation_percent'],'percent',help='Cancelled units / original units in the selected booking cohort, known at publication.')
    a,b=st.columns(2)
    with a,st.container(border=True):
        st.subheader('New & returning ordering customers')
        st.caption('New means first observed booking falls in this period; returning means a prior observed booking. This uses all available booking history, not account creation. Left-censored before dataset coverage.')
        ui.table(data['segments'],'customer-segments')
    with b,st.container(border=True):
        st.subheader('Customer activity')
        ui.plot(charts.trend(data['customer_trend'],'identified_customers','Identified ordering customers'),'customer_trend')
    with st.container(border=True):
        st.subheader('Customer cohorts')
        st.caption('Distinct active ordering customers by first observed booking month and activity month. Counts, not a retention percentage; no unsupported lifetime history is inferred.')
        ui.plot(charts.cohorts(data['cohorts']),'customer_cohorts')
    a,b=st.columns(2)
    with a,st.container(border=True):
        st.subheader('Observed order outcomes')
        st.caption('Derived from original, cancelled and recognized unit quantities for whole orders matching the filters. Detailed picking, shipping and status-event history is not published in this warehouse.')
        ui.plot(charts.bars(data['outcomes'],'outcome','orders','Orders'),'customer_outcomes')
        ui.table(data['outcomes'],'order-outcomes')
    with b,st.container(border=True):
        st.subheader('Returns & refunds')
        st.caption('Return rate uses fulfilled units in cohorts matured for 30 days, observed through the selected cutoff. Refund payments include GST and are not deducted again from revenue.')
        ui.table(data['returns'],'matured-returns')
        ui.table(data['refunds'],'customer-refunds')
        st.caption('A payment-refund rate is not shown: Stage 1 does not define its denominator.')
    with st.expander('Customer activity details and guest coverage'):
        ui.table(data['customer_trend'],'customer-activity')
