import streamlit as st
from .. import components as ui, charts


def render(data,meta):
    ui.header('Product & inventory intelligence','Separate product demand from physical stock. Inventory reflects observed snapshots only.')
    ui.notices(data)
    s=data['summary']; columns=st.columns(3)
    ui.card(columns[0],'Product-scope net sales',s['net_sales'])
    ui.card(columns[1],'Net units',s['net_units'],'number',help='Recognized units minus credited units, using recognition/credit dates.')
    ui.card(columns[2],'Gross margin',s['gross_margin_percent'],'percent')
    products=ui.label_rows(data['products'],meta,'products')
    sort=st.selectbox('Rank products by',['net_sales','net_units','gross_profit'],format_func=lambda s:s.replace('_',' ').title(),key='product_sort')
    ranked=sorted(products,key=lambda r:r[sort],reverse=True)
    a,b=st.columns(2)
    with a,st.container(border=True):
        st.subheader('Leading products')
        ui.plot(charts.bars(ranked[:10],'label',sort,sort.replace('_',' ').title()),'products_rank')
    with b,st.container(border=True):
        st.subheader('Category contribution')
        ui.plot(charts.bars(ui.label_rows(data['categories'],meta,'categories'),'label','net_sales','Net sales · ₹'),'product_categories')
    with st.container(border=True):
        st.subheader('Product performance over time')
        ui.plot(charts.trend(data['trend']),'product_trend')
    with st.expander('Product profitability and units',expanded=True): ui.table(ranked,'product-profitability')
    with st.container(border=True):
        st.subheader('Stock availability')
        st.caption('Latest observed snapshot within the selected dates. Stock uses physical locations, not originating online stores. No daily values are interpolated.')
        inventory=data['inventory']
        if inventory: st.caption(f"Observed on {inventory[0]['snapshot_date']} · {len(inventory):,} SKU/location observations")
        ui.table(inventory,'inventory')
    a,b=st.columns(2)
    with a,st.container(border=True):
        st.subheader('Inventory sell-through')
        st.caption('Net fulfilled units / available sellable pool. Company inflows exclude internal transfers; store inflows include inward transfers.')
        ui.table(data['sell_through'],'sell-through')
    with b,st.container(border=True):
        st.subheader('Stock with no recognized sales')
        st.caption('Positive sellable stock at the selected observation and zero fulfilled units during the period. An investigation list, not an aged-stock classification.')
        ui.table([r for r in data['inventory'] if r['no_sales_with_stock']],'no-sales-stock')
    st.caption('Brand contribution is unavailable because the warehouse has no brand field.')
