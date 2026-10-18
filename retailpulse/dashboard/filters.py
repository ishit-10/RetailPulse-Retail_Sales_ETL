"""Shared session-state filters. Only supported warehouse fields become SQL binds."""
from datetime import date

KEYS = ('date','state_id','channel','city','store_id','category_id','sku_id','grain','as_of')


def store_options(metadata, state_id=None, city=None, channel=None):
    if channel in ('WEB','APP'): return []
    return [s for s in metadata['stores'] if s['type']=='STORE'
            and (state_id is None or s['state_id']==state_id)
            and (city is None or s['city']==city)]


def product_options(metadata, category_id=None):
    return [p for p in metadata['products'] if category_id is None or p['category_id']==category_id]


def render(metadata):
    import streamlit as st
    low=metadata['coverage']['min_date']; high=metadata['coverage']['max_date']
    # Default bookings window; outcome tail can be selected explicitly.
    default=(max(low,metadata['booking_dates']['min_date'].replace(day=1)),metadata['booking_dates']['max_date'])
    def reset():
        # Assign widget values so Streamlit sends the reset to the browser.
        # Deleting keys can rehydrate the previous frontend selection on rerun.
        defaults=dict.fromkeys(KEYS)
        defaults.update(date=default, grain='month', as_of=high)
        for key, value in defaults.items():
            st.session_state['global_'+key]=value
    def choice(label, key, values, labels=None, disabled=False):
        full = [None]+list(values)
        state_key='global_'+key
        if st.session_state.get(state_key) not in full: st.session_state[state_key]=None
        return st.selectbox(label,full,key=state_key,format_func=lambda v:'All' if v is None else (labels or {}).get(v,str(v)),disabled=disabled)
    with st.sidebar:
        st.markdown('### Explore the business')
        st.button('Reset filters', on_click=reset, use_container_width=True)
        dates=st.date_input('Business dates',value=default,min_value=low,max_value=high,key='global_date',format='DD/MM/YYYY')
        if len(dates)!=2:
            st.info('Choose both a start and end date.'); st.stop()
        state=choice('State','state_id',[r['state_id'] for r in metadata['states']],{r['state_id']:r['name'] for r in metadata['states']})
        channel=choice('Sales channel','channel',['POS','WEB','APP'],{'POS':'Physical stores','WEB':'Website','APP':'Mobile app'})
        available=store_options(metadata,state_id=state,channel=channel)
        city=choice('City','city',sorted({r['city'] for r in available}),disabled=channel in ('WEB','APP'))
        stores=store_options(metadata,state,city,channel)
        store=choice('Store','store_id',[r['location_id'] for r in stores],{r['location_id']:r['name'] for r in stores},disabled=channel in ('WEB','APP'))
        categories={p['category_id']:p['category_name'] for p in metadata['products']}
        category=choice('Category','category_id',sorted(categories),categories)
        products=product_options(metadata,category)
        product=choice('Product / SKU','sku_id',[p['sku_id'] for p in products],
                       {p['sku_id']:f"{p['product_name']} · {p['color']} / {p['size']} · {p['sku_id']}" for p in products})
        with st.expander('Source coverage'):
            st.selectbox('Region',['Not available in source'],disabled=True)
            st.selectbox('Brand',['Not available in source'],disabled=True)
            st.caption('State is supported. Region and brand are not inferred. Online orders have no originating city or store.')
        st.caption('Dates use Asia/Kolkata. Money is INR. All records are synthetic.')
    filters={k:v for k,v in dict(state_id=state,channel=channel,city=city,store_id=store,category_id=category,sku_id=product).items() if v is not None}
    return dates[0],dates[1],filters
