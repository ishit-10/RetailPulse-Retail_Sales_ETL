-- Additional final reconciliation. These are not valid intermediate checkpoints.
WITH d AS (SELECT order_line_id,sum(amount) amount FROM source.order_line_discounts GROUP BY 1),
c AS (SELECT order_line_id,sum(quantity) q FROM source.order_line_cancellations GROUP BY 1),
v AS (SELECT fi.order_line_id,sum(il.taxable_value) amount FROM source.invoice_lines il
 JOIN source.fulfillment_items fi USING(fulfillment_item_id) GROUP BY 1),
line_values AS (
 SELECT l.*,COALESCE(c.q,0) cancelled,COALESCE(v.amount,0) invoiced,
 (l.quantity*l.original_unit_price-COALESCE(d.amount,0))*100 cents
 FROM source.order_lines l LEFT JOIN d USING(order_line_id) LEFT JOIN c USING(order_line_id) LEFT JOIN v USING(order_line_id)
), credit_values AS (
 SELECT credit_note_id,sum(taxable_value+cgst+sgst+igst) amount FROM source.credit_note_lines GROUP BY 1
), refunds AS (
 SELECT credit_note_id,sum(amount) amount FROM source.payment_transactions WHERE type='REFUND' AND status='SUCCEEDED' AND credit_note_id IS NOT NULL GROUP BY 1
), expected_tax AS (
 SELECT il.*,l.state_id<>o.place_of_supply_state_id interstate,
 CASE WHEN i.invoice_date<DATE '2025-09-22' THEN
   CASE WHEN il.taxable_value/il.quantity<=1000 THEN 1 ELSE 2 END
 ELSE CASE WHEN il.taxable_value/il.quantity<=2500 THEN 3 ELSE 4 END END expected_rule
 FROM source.invoice_lines il JOIN source.sales_invoices i USING(invoice_id)
 JOIN source.fulfillments f USING(fulfillment_id) JOIN source.locations l ON l.location_id=f.source_location_id
 JOIN source.sales_orders o ON o.order_id=i.order_id
), tax_amounts AS (
 SELECT *,round(taxable_value * CASE expected_rule WHEN 1 THEN .05 WHEN 2 THEN .12 WHEN 3 THEN .05 ELSE .18 END,2) expected_tax
 FROM expected_tax
)
SELECT 'discounted_invoice_reconciliation' check_name,count(*) failures FROM line_values
 WHERE invoiced*100 <> (quantity-cancelled)*floor(cents/quantity)+greatest(mod(cents,quantity)-cancelled,0)
UNION ALL SELECT 'historical_price',count(*) FROM source.order_lines l JOIN source.price_versions p USING(price_id)
 JOIN source.sales_orders o USING(order_id) WHERE l.sku_id<>p.sku_id OR l.original_unit_price<>p.list_price OR o.channel<>p.channel
UNION ALL SELECT 'mrp_ceiling',count(*) FROM source.invoice_lines il JOIN source.fulfillment_items fi USING(fulfillment_item_id)
 JOIN source.order_lines l USING(order_line_id) JOIN source.price_versions p USING(price_id)
 WHERE il.taxable_value+il.cgst+il.sgst+il.igst>il.quantity*p.mrp
UNION ALL SELECT 'refund_route',count(*) FROM source.payment_transactions r JOIN source.payment_transactions p ON p.payment_txn_id=r.original_transaction_id
 WHERE r.type='REFUND' AND (p.type<>'CAPTURE' OR p.status<>'SUCCEEDED' OR r.order_id<>p.order_id OR r.method<>p.method)
UNION ALL SELECT 'credit_refund_limit',count(*) FROM refunds r LEFT JOIN credit_values v USING(credit_note_id) WHERE r.amount>COALESCE(v.amount,0)
UNION ALL SELECT 'return_quantity',count(*) FROM (SELECT invoice_line_id,sum(quantity) q FROM source.return_items GROUP BY 1) r
 JOIN source.invoice_lines il USING(invoice_line_id) WHERE r.q>il.quantity
UNION ALL SELECT 'credit_lineage',count(*) FROM source.credit_note_lines cl JOIN source.return_items ri USING(return_item_id)
 JOIN source.credit_notes cn USING(credit_note_id) JOIN source.invoice_lines il ON il.invoice_line_id=cl.invoice_line_id
 WHERE ri.invoice_line_id<>cl.invoice_line_id OR ri.return_id<>cn.return_id OR cn.invoice_id<>il.invoice_id OR cl.quantity<>ri.quantity
UNION ALL SELECT 'reserved_snapshot',count(*) FROM source.inventory_snapshots WHERE reserved<>0
UNION ALL SELECT 'all_channels',CASE WHEN count(DISTINCT channel)=3 THEN 0 ELSE 1 END FROM source.sales_orders
UNION ALL SELECT 'exact_cart_coupons',count(*) FROM (
 SELECT l.order_id,sum(d.amount) amount FROM source.order_line_discounts d JOIN source.order_lines l USING(order_line_id)
 WHERE d.promotion_id=2 GROUP BY 1 HAVING sum(d.amount)<>100
) coupon
UNION ALL SELECT 'exact_tax_schedule_and_split',count(*) FROM tax_amounts
 WHERE tax_rule_id<>expected_rule
 OR igst<>CASE WHEN interstate THEN expected_tax ELSE 0 END
 OR cgst<>CASE WHEN interstate THEN 0 ELSE round(expected_tax/2,2) END
 OR sgst<>CASE WHEN interstate THEN 0 ELSE expected_tax-round(expected_tax/2,2) END
UNION ALL SELECT 'digital_traffic',count(*) FROM source.digital_traffic_daily t LEFT JOIN (
 SELECT channel,(confirmed_at AT TIME ZONE 'Asia/Kolkata')::date business_date,count(*) n
 FROM source.sales_orders WHERE channel<>'POS' GROUP BY 1,2
) o USING(channel,business_date) WHERE t.eligible_sessions<COALESCE(o.n,0)
UNION ALL SELECT 'confirmed_store_traffic',count(*) FROM source.store_footfall_daily t LEFT JOIN (
 SELECT originating_store_id store_id,(confirmed_at AT TIME ZONE 'Asia/Kolkata')::date business_date,count(*) n
 FROM source.sales_orders o WHERE channel='POS'
 AND EXISTS(SELECT 1 FROM source.order_status_events e WHERE e.order_id=o.order_id AND new_status='COMPLETED')
 GROUP BY 1,2
) o USING(store_id,business_date) WHERE t.valid_entries<COALESCE(o.n,0);
