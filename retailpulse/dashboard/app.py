"""Entrypoint orchestration, shared widgets, caching and failure boundaries."""
import logging
import os
import streamlit as st
from .. import db
from . import data, components, filters
from .pages import overview, stores, products, customers, health

LOG=logging.getLogger(__name__)
PAGE_INFO={
 'overview':('Executive overview',overview,':material/dashboard:'),
 'stores':('Store & channel',stores,':material/storefront:'),
 'products':('Product & inventory',products,':material/inventory_2:'),
 'customers':('Customer & orders',customers,':material/group:'),
 'health':('Pipeline health',health,':material/monitor_heart:'),
}


@st.cache_resource
def local_engine():
    return db.engine()


@st.cache_data(ttl=60,max_entries=96,show_spinner=False)
def cached_payload(mode,page,start,end,selected,run_id,grain,as_of):
    return data.payload(mode,page,start,end,selected,run_id,grain,as_of,
                        engine=local_engine() if mode=='local' else None)


def main(page_override=None):
    st.set_page_config(page_title='RetailPulse · Retail intelligence',page_icon='◈',layout='wide')
    st.markdown(components.STYLE,unsafe_allow_html=True)
    mode=os.getenv('RETAILPULSE_DASHBOARD_MODE','demo').lower()
    if mode not in ('demo','local'):
        st.error('RETAILPULSE_DASHBOARD_MODE must be demo or local.');st.stop()
    st.sidebar.markdown('## ◈ RetailPulse')
    st.sidebar.caption('RETAIL INTELLIGENCE / '+mode.upper())
    try:
        # Uncached head lookup on every rerun prevents serving data from an old
        # publication under a new lineage label. The cached payload has the run ID.
        meta=data.metadata(mode,local_engine() if mode=='local' else None)
        start,end,selected=filters.render(meta)
        with st.sidebar:
            grain=st.selectbox('Trend interval',['day','week','month'],index=2,
                               format_func=lambda x:{'day':'Daily','week':'Weekly','month':'Monthly'}[x],key='global_grain')
            with st.expander('Return-cohort observation'):
                as_of=st.date_input('Return observation cutoff',value=meta['coverage']['max_date'],
                                   min_value=meta['coverage']['min_date'],max_value=meta['coverage']['max_date'],key='global_as_of')
                st.caption('Used only for matured return rates. Other metrics retain the main date range.')
        def show(name):
            title,module,_=PAGE_INFO[name]
            components.freshness(meta,mode)
            with st.spinner('Reading the published analytics snapshot…'):
                payload=cached_payload(mode,name,start,end,selected,meta['run_id'],grain,as_of)
            module.render(payload,meta)
            st.markdown('<div class="rp-footer">RetailPulse · Synthetic Indian omnichannel apparel retail · Merchandise excludes GST unless stated</div>',unsafe_allow_html=True)
        if page_override:
            if page_override not in PAGE_INFO: raise ValueError('Unknown dashboard page')
            show(page_override)
        else:
            pages=[st.Page(lambda name=name:show(name),title=title,icon=icon,url_path=name,default=name=='overview')
                   for name,(title,_,icon) in PAGE_INFO.items()]
            st.navigation(pages).run()
    except data.SnapshotChanged:
        st.info('A new warehouse snapshot was published. Reload to use it consistently.')
        if st.button('Reload published snapshot'): st.rerun()
    except Exception as exc:
        # Never render raw SQLAlchemy exception text, URLs, SQL parameters or secrets.
        LOG.error('dashboard_failed error_class=%s mode=%s',type(exc).__name__,mode)
        st.error('Analytics could not be loaded. Check the local PostgreSQL service, read permissions and published warehouse, or validate the demo bundle.')
        st.caption('No data was written. Use warehouse-status / verify-warehouse locally for diagnostics. Demo mode requires no database credentials.')
        if st.button('Retry loading'): cached_payload.clear();st.rerun()
