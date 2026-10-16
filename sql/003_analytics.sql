CREATE VIEW analytics.bookings AS
SELECT o.order_id,o.external_reference,o.channel,o.originating_store_id,
 (o.confirmed_at AT TIME ZONE 'Asia/Kolkata')::date booking_date,
 sum(l.quantity*l.original_unit_price) booked_gmv,
 sum(COALESCE(d.discount,0)) discount,
 sum(l.quantity*l.original_unit_price-COALESCE(d.discount,0)) booked_merchandise,
 sum(l.quantity) ordered_units
FROM source.sales_orders o JOIN source.order_lines l USING(order_id)
LEFT JOIN (SELECT order_line_id,sum(amount) discount FROM source.order_line_discounts GROUP BY 1) d USING(order_line_id)
GROUP BY o.order_id;
CREATE VIEW analytics.recognized_sales AS
SELECT o.order_id,o.channel,o.originating_store_id, l.sku_id,il.invoice_line_id,
 (f.delivered_at AT TIME ZONE 'Asia/Kolkata')::date recognition_date,
 il.quantity,il.taxable_value gross_sales,il.cgst+il.sgst+il.igst tax,
 c.cogs
FROM source.invoice_lines il JOIN source.sales_invoices i USING(invoice_id)
JOIN source.fulfillments f ON f.fulfillment_id=i.fulfillment_id
JOIN source.fulfillment_items fi ON fi.fulfillment_item_id=il.fulfillment_item_id
JOIN source.order_lines l ON l.order_line_id=fi.order_line_id
JOIN source.sales_orders o ON o.order_id=i.order_id
JOIN (SELECT fulfillment_item_id,sum(quantity*unit_cost) cogs FROM source.fulfillment_cost_allocations GROUP BY 1) c ON c.fulfillment_item_id=fi.fulfillment_item_id
WHERE f.delivered_at IS NOT NULL;
CREATE VIEW analytics.return_credits AS
SELECT r.order_id,ri.invoice_line_id,ri.return_item_id,
 (cn.issued_at AT TIME ZONE 'Asia/Kolkata')::date credit_date,cl.quantity,
 cl.taxable_value merchandise_credit,cl.cgst+cl.sgst+cl.igst tax_credit,
 CASE WHEN ri.disposition='RESTOCK' THEN ca.cost ELSE 0 END reversed_cogs
FROM source.credit_note_lines cl JOIN source.credit_notes cn USING(credit_note_id)
JOIN source.return_items ri USING(return_item_id)
JOIN source.return_requests r ON r.return_id=ri.return_id
JOIN (SELECT ra.return_item_id,sum(ra.quantity*fa.unit_cost) cost FROM source.return_cost_allocations ra
 JOIN source.fulfillment_cost_allocations fa USING(allocation_id) GROUP BY 1) ca USING(return_item_id);
-- Events retain separate recognition and credit dates; refunds never reduce revenue again.
CREATE VIEW analytics.sales_events AS
SELECT order_id,recognition_date event_date,gross_sales net_sales,cogs net_cogs,quantity net_units FROM analytics.recognized_sales
UNION ALL
SELECT order_id,credit_date,-merchandise_credit,-reversed_cogs,-quantity FROM analytics.return_credits;
CREATE VIEW analytics.order_financials AS
WITH sales AS (SELECT order_id,sum(net_sales) net_sales,sum(net_cogs) net_cogs,sum(net_units) net_units FROM analytics.sales_events GROUP BY 1),
refunds AS (SELECT order_id,sum(amount) FILTER(WHERE type='REFUND' AND status='SUCCEEDED') customer_refunds FROM source.payment_transactions GROUP BY 1)
SELECT b.*,COALESCE(s.net_sales,0) net_sales,COALESCE(s.net_cogs,0) net_cogs,COALESCE(s.net_units,0) net_units,
 COALESCE(s.net_sales-s.net_cogs,0) gross_profit,
 CASE WHEN s.net_sales>0 THEN (s.net_sales-s.net_cogs)/s.net_sales END gross_margin,
 COALESCE(r.customer_refunds,0) customer_refunds
FROM analytics.bookings b LEFT JOIN sales s USING(order_id) LEFT JOIN refunds r USING(order_id);
CREATE VIEW analytics.store_conversion AS
SELECT f.*,COALESCE(o.completed_orders,0) completed_orders,
 CASE WHEN f.valid_entries>0 THEN COALESCE(o.completed_orders,0)::numeric/f.valid_entries END conversion
FROM source.store_footfall_daily f LEFT JOIN (
 SELECT originating_store_id store_id,(event_at AT TIME ZONE 'Asia/Kolkata')::date business_date,count(DISTINCT order_id) completed_orders
 FROM source.order_status_events e JOIN source.sales_orders USING(order_id)
 WHERE new_status='COMPLETED' AND channel='POS' GROUP BY 1,2
) o USING(store_id,business_date);
