import streamlit as st
from .. import components as ui
from ..formatting import number


def render(data,meta):
    ui.header('Pipeline health & data quality','Actual audit records and publication lineage. Business filters do not apply to pipeline monitoring.')
    head=data['lineage']
    if head['status']=='demo':
        st.info('Demo mode reads a frozen, validated warehouse fixture. No extraction or warehouse job is running, and no production audit history is bundled.')
        st.write('**Bundle:**',head['run_id'])
        st.write('**Fixture packaged at:**',head['fixture_created_at'])
        st.write('**Validation:** file hashes and row counts checked before queries; financial reconciliation covered by automated demo tests.')
    else:
        cols=st.columns(3)
        cols[0].metric('Published warehouse','Completed',help='Only completed, atomically published builds are read.')
        cols[1].metric('Rows scanned in linked ETL',number(head['rows_scanned']))
        duration=(head['warehouse_completed_at']-head['warehouse_started_at']).total_seconds()
        cols[2].metric('Warehouse load duration',number(duration,1)+' s')
        st.write('**Last published warehouse completion:**',str(head['warehouse_completed_at']))
        st.write('**Last successful ETL completion:**',str(head['latest_extraction_completed_at']))
        st.write('**Linked successful ETL completion:**',str(head['extraction_completed_at']))
        st.code(f"Warehouse run: {head['run_id']}\nPublished snapshot: {head['snapshot_id']}\nLatest lake snapshot: {head['latest_snapshot_id']}",language=None)
        st.success('Publication audit records successful warehouse reconciliation. This is the last-load result, not a fresh full database verification.')
        st.caption('Run verify-warehouse outside the dashboard to recheck current counts and totals. Extraction and warehouse errors are shown by class/phase, without raw exception text or credentials.')
    with st.container(border=True):
        st.subheader('Published object counts')
        ui.table([{'object':k,'rows':v} for k,v in head['counts'].items()],'published-counts')
    with st.container(border=True):
        st.subheader('Recent pipeline runs')
        if head['status']=='demo': st.caption('Not available in the offline demo; no monitoring events are fabricated.')
        else:
            ui.table(data['history'],'pipeline-history')
            failures=[r for r in data['history'] if r['status']=='failed']
            if failures:
                st.warning(f'{len(failures)} failed runs in the displayed history. A prior successful publication may still be serving readers.')
                ui.table(failures,'pipeline-failures')
