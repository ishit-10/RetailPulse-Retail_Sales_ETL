-- Each result is a named count of failures; all must be zero.
WITH
line_totals AS (
 SELECT l.*,COALESCE(f.q,0) fulfilled,COALESCE(c.q,0) cancelled,COALESCE(d.amount,0) discount
 FROM source.order_lines l
 LEFT JOIN (SELECT order_line_id,sum(quantity) q FROM source.fulfillment_items GROUP BY 1) f USING(order_line_id)
 LEFT JOIN (SELECT order_line_id,sum(quantity) q FROM source.order_line_cancellations GROUP BY 1) c USING(order_line_id)
 LEFT JOIN (SELECT order_line_id,sum(amount) amount FROM source.order_line_discounts GROUP BY 1) d USING(order_line_id)
), stock AS (
 SELECT *,sum(quantity) OVER(PARTITION BY sku_id,location_id,lot_id,stock_state ORDER BY event_at,movement_id) balance
 FROM source.inventory_movements
), refunds AS (
 SELECT original_transaction_id,sum(amount) amount FROM source.payment_transactions
 WHERE type='REFUND' AND status='SUCCEEDED' GROUP BY 1
), credits AS (
 SELECT invoice_line_id,sum(quantity) q,sum(taxable_value) v,sum(cgst) c,sum(sgst) s,sum(igst) i FROM source.credit_note_lines GROUP BY 1
), snapshot_ledger AS (
 SELECT s.snapshot_date,s.sku_id,s.location_id,s.sellable,s.in_transit,s.damaged,
 COALESCE(sum(m.quantity) FILTER(WHERE m.stock_state='SELLABLE'),0) ls,
 COALESCE(sum(m.quantity) FILTER(WHERE m.stock_state='TRANSIT'),0) lt,
 COALESCE(sum(m.quantity) FILTER(WHERE m.stock_state='DAMAGED'),0) ld
 FROM source.inventory_snapshots s LEFT JOIN source.inventory_movements m
 ON m.sku_id=s.sku_id AND m.location_id=s.location_id
 AND (m.event_at AT TIME ZONE 'Asia/Kolkata')::date<=s.snapshot_date
 GROUP BY s.snapshot_date,s.sku_id,s.location_id
)
SELECT 'order_quantity' check_name,count(*) failures FROM line_totals WHERE fulfilled+cancelled<>quantity
UNION ALL SELECT 'discount_limit',count(*) FROM line_totals WHERE discount>quantity*original_unit_price
UNION ALL SELECT 'fulfillment_order',count(*) FROM source.fulfillment_items fi JOIN source.order_lines l USING(order_line_id) JOIN source.fulfillments f USING(fulfillment_id) WHERE l.order_id<>f.order_id
UNION ALL SELECT 'invoice_lineage',count(*) FROM source.invoice_lines il JOIN source.fulfillment_items fi USING(fulfillment_item_id) JOIN source.sales_invoices i USING(invoice_id) WHERE fi.fulfillment_id<>i.fulfillment_id OR fi.quantity<>il.quantity
UNION ALL SELECT 'tax_rule',count(*) FROM source.invoice_lines il JOIN source.sales_invoices i USING(invoice_id) JOIN source.tax_rules t USING(tax_rule_id)
 WHERE NOT(i.invoice_date>=t.effective_from AND i.invoice_date<t.effective_to AND il.taxable_value/il.quantity>t.lower_exclusive AND il.taxable_value/il.quantity<=t.upper_inclusive)
 OR il.cgst+il.sgst+il.igst<>round(il.taxable_value*t.gst_rate,2)
UNION ALL SELECT 'tax_jurisdiction',count(*) FROM source.invoice_lines il JOIN source.sales_invoices i USING(invoice_id)
 JOIN source.fulfillments f USING(fulfillment_id) JOIN source.locations l ON l.location_id=f.source_location_id JOIN source.sales_orders o ON o.order_id=i.order_id
 WHERE (l.state_id=o.place_of_supply_state_id AND (il.igst<>0 OR abs(il.cgst-il.sgst)>.01)) OR (l.state_id<>o.place_of_supply_state_id AND (il.cgst<>0 OR il.sgst<>0))
UNION ALL SELECT 'credit_limit',count(*) FROM credits c JOIN source.invoice_lines il USING(invoice_line_id) WHERE c.q>il.quantity OR c.v>il.taxable_value OR c.c>il.cgst OR c.s>il.sgst OR c.i>il.igst
UNION ALL SELECT 'return_window',count(*) FROM source.return_items ri JOIN source.return_requests r USING(return_id) JOIN source.invoice_lines il USING(invoice_line_id)
 JOIN source.sales_invoices i USING(invoice_id) JOIN source.fulfillments f USING(fulfillment_id)
 WHERE r.order_id<>i.order_id OR r.requested_at<f.delivered_at OR r.requested_at>f.delivered_at+interval '14 days'
UNION ALL SELECT 'refund_limit',count(*) FROM refunds r JOIN source.payment_transactions p ON p.payment_txn_id=r.original_transaction_id WHERE r.amount>p.amount
UNION ALL SELECT 'negative_stock',count(*) FROM stock WHERE balance<0
UNION ALL SELECT 'snapshot_reconciliation',count(*) FROM snapshot_ledger WHERE sellable<>ls OR in_transit<>lt OR damaged<>ld
UNION ALL SELECT 'lot_sku',count(*) FROM source.inventory_movements m JOIN source.stock_lots l USING(lot_id) WHERE m.sku_id<>l.sku_id
UNION ALL SELECT 'historical_cost',count(*) FROM source.fulfillment_cost_allocations a JOIN source.stock_lots l USING(lot_id) WHERE a.unit_cost<>l.unit_cost
UNION ALL SELECT 'cost_quantity',count(*) FROM source.fulfillment_items fi LEFT JOIN (SELECT fulfillment_item_id,sum(quantity) q FROM source.fulfillment_cost_allocations GROUP BY 1) a USING(fulfillment_item_id) WHERE COALESCE(a.q,0)<>fi.quantity
UNION ALL SELECT 'store_conversion',count(*) FROM analytics.store_conversion WHERE completed_orders>valid_entries OR (valid_entries=0 AND conversion IS NOT NULL)
UNION ALL SELECT 'golden',CASE WHEN count(*)=1 THEN 0 ELSE 1 END FROM analytics.order_financials
 WHERE external_reference='SYNTH-GOLDEN-001' AND booked_gmv=6000 AND discount=1200 AND booked_merchandise=4800
 AND net_sales=2400 AND net_cogs=1200 AND gross_profit=1200 AND gross_margin=.5 AND customer_refunds=2520 AND net_units=1
UNION ALL SELECT 'golden_tax',CASE WHEN sum(s.tax)=240 AND sum(s.gross_sales)=4800 THEN 0 ELSE 1 END
 FROM analytics.recognized_sales s JOIN source.sales_orders o USING(order_id) WHERE external_reference='SYNTH-GOLDEN-001';
