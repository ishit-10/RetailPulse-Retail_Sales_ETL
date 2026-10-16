-- Executed with search_path set to a private, per-run build schema.
-- Source replicas are named src_*; no query in this file reads source.*.
CREATE TABLE dim_product AS
SELECT s.*,p.category_id,p.style_code,p.name product_name,p.hsn,p.collection,p.launch_date,c.name category_name
FROM src_skus s JOIN src_product_styles p USING(style_id) JOIN src_categories c USING(category_id);
ALTER TABLE dim_product ADD PRIMARY KEY(sku_id);
CREATE TABLE dim_state AS SELECT * FROM src_states;
ALTER TABLE dim_state ADD PRIMARY KEY(state_id);
CREATE TABLE dim_store AS SELECT l.*,s.name state_name FROM src_locations l JOIN src_states s USING(state_id);
ALTER TABLE dim_store ADD PRIMARY KEY(location_id);
ALTER TABLE dim_store ADD FOREIGN KEY(state_id) REFERENCES dim_state;
CREATE TABLE dim_customer AS SELECT * FROM src_customers;
ALTER TABLE dim_customer ADD PRIMARY KEY(customer_id);
CREATE TABLE dim_channel(channel text PRIMARY KEY);
INSERT INTO dim_channel VALUES('POS'),('WEB'),('APP');
CREATE TABLE dim_date AS
WITH bounds AS (
 SELECT min(d) lo,max(d) hi FROM (
 SELECT (confirmed_at AT TIME ZONE 'Asia/Kolkata')::date d FROM src_sales_orders
 UNION ALL SELECT snapshot_date FROM src_inventory_snapshots
 UNION ALL SELECT business_date FROM src_store_footfall_daily
 UNION ALL SELECT (event_at AT TIME ZONE 'Asia/Kolkata')::date FROM src_source_events
 ) x)
SELECT d::date date_key,extract(year FROM d)::int calendar_year,extract(month FROM d)::int calendar_month,
 extract(isodow FROM d)::int iso_weekday,date_trunc('week',d)::date week_start,
 date_trunc('month',d)::date month_start,
 extract(year FROM d-interval '3 months')::int fiscal_start_year
FROM bounds CROSS JOIN LATERAL generate_series(lo::timestamp,hi::timestamp,interval '1 day') d;
ALTER TABLE dim_date ADD PRIMARY KEY(date_key);
CREATE TABLE dim_order AS SELECT * FROM src_sales_orders;
ALTER TABLE dim_order ADD PRIMARY KEY(order_id);
ALTER TABLE dim_order ADD FOREIGN KEY(place_of_supply_state_id) REFERENCES dim_state;
ALTER TABLE dim_order ADD FOREIGN KEY(customer_id) REFERENCES dim_customer;
ALTER TABLE dim_order ADD FOREIGN KEY(originating_store_id) REFERENCES dim_store;
ALTER TABLE dim_order ADD FOREIGN KEY(channel) REFERENCES dim_channel;
CREATE TABLE fact_order_items AS
WITH discounts AS (SELECT order_line_id,sum(amount)::numeric(20,2) discount FROM src_order_line_discounts GROUP BY 1),
cancellations AS (SELECT order_line_id,sum(quantity)::bigint cancelled_units FROM src_order_line_cancellations GROUP BY 1)
SELECT l.order_line_id,l.order_id,l.sku_id,o.customer_id,o.channel,o.originating_store_id store_id,
 o.place_of_supply_state_id state_id,(o.confirmed_at AT TIME ZONE 'Asia/Kolkata')::date booking_date,
 l.quantity ordered_units,COALESCE(c.cancelled_units,0) cancelled_units,
 (l.quantity*l.original_unit_price)::numeric(20,2) booked_gmv,
 COALESCE(d.discount,0)::numeric(20,2) discount,
 (l.quantity*l.original_unit_price-COALESCE(d.discount,0))::numeric(20,2) booked_merchandise
FROM src_order_lines l JOIN src_sales_orders o USING(order_id)
LEFT JOIN discounts d USING(order_line_id) LEFT JOIN cancellations c USING(order_line_id);
ALTER TABLE fact_order_items ADD PRIMARY KEY(order_line_id);
ALTER TABLE fact_order_items ADD FOREIGN KEY(order_id) REFERENCES dim_order;
ALTER TABLE fact_order_items ADD FOREIGN KEY(state_id) REFERENCES dim_state;
ALTER TABLE fact_order_items ADD FOREIGN KEY(sku_id) REFERENCES dim_product;
ALTER TABLE fact_order_items ADD FOREIGN KEY(customer_id) REFERENCES dim_customer;
ALTER TABLE fact_order_items ADD FOREIGN KEY(store_id) REFERENCES dim_store;
ALTER TABLE fact_order_items ADD FOREIGN KEY(channel) REFERENCES dim_channel;
ALTER TABLE fact_order_items ADD FOREIGN KEY(booking_date) REFERENCES dim_date;
CREATE INDEX ON fact_order_items(booking_date,store_id,channel,sku_id);
CREATE INDEX ON fact_order_items(order_id);
CREATE TABLE fact_sales AS
WITH costs AS (SELECT fulfillment_item_id,sum(quantity*unit_cost)::numeric(20,2) cogs FROM src_fulfillment_cost_allocations GROUP BY 1)
SELECT il.invoice_line_id,fi.order_line_id,f.source_location_id,
 (f.delivered_at AT TIME ZONE 'Asia/Kolkata')::date recognition_date,il.quantity units,
 il.taxable_value::numeric(20,2) gross_sales,(il.cgst+il.sgst+il.igst)::numeric(20,2) tax,c.cogs
FROM src_invoice_lines il JOIN src_sales_invoices i USING(invoice_id)
JOIN src_fulfillments f USING(fulfillment_id)
JOIN src_fulfillment_items fi ON fi.fulfillment_item_id=il.fulfillment_item_id
JOIN costs c ON c.fulfillment_item_id=fi.fulfillment_item_id WHERE f.delivered_at IS NOT NULL;
ALTER TABLE fact_sales ADD PRIMARY KEY(invoice_line_id);
ALTER TABLE fact_sales ADD FOREIGN KEY(order_line_id) REFERENCES fact_order_items;
ALTER TABLE fact_sales ADD FOREIGN KEY(source_location_id) REFERENCES dim_store;
ALTER TABLE fact_sales ADD FOREIGN KEY(recognition_date) REFERENCES dim_date;
CREATE INDEX ON fact_sales(recognition_date,order_line_id);
CREATE TABLE fact_returns AS
WITH costs AS (SELECT ra.return_item_id,sum(ra.quantity*fa.unit_cost)::numeric(20,2) cost
 FROM src_return_cost_allocations ra JOIN src_fulfillment_cost_allocations fa USING(allocation_id) GROUP BY 1)
SELECT ri.return_item_id,cl.credit_line_id,cn.credit_note_id,ri.invoice_line_id,ri.quantity units,
 r.receiving_location_id,ri.disposition,(r.accepted_at AT TIME ZONE 'Asia/Kolkata')::date accepted_date,
 (cn.issued_at AT TIME ZONE 'Asia/Kolkata')::date credit_date,
 cl.taxable_value::numeric(20,2) merchandise_credit,(cl.cgst+cl.sgst+cl.igst)::numeric(20,2) tax_credit,
 (CASE WHEN ri.disposition='RESTOCK' THEN c.cost ELSE 0 END)::numeric(20,2) reversed_cogs
FROM src_return_items ri JOIN src_return_requests r USING(return_id)
JOIN src_credit_note_lines cl USING(return_item_id) JOIN src_credit_notes cn USING(credit_note_id)
JOIN costs c USING(return_item_id);
ALTER TABLE fact_returns ADD PRIMARY KEY(return_item_id);
ALTER TABLE fact_returns ADD UNIQUE(credit_line_id);
ALTER TABLE fact_returns ADD FOREIGN KEY(invoice_line_id) REFERENCES fact_sales;
ALTER TABLE fact_returns ADD FOREIGN KEY(receiving_location_id) REFERENCES dim_store;
ALTER TABLE fact_returns ADD FOREIGN KEY(credit_date) REFERENCES dim_date;
ALTER TABLE fact_returns ADD FOREIGN KEY(accepted_date) REFERENCES dim_date;
CREATE INDEX ON fact_returns(credit_date,invoice_line_id);
CREATE TABLE fact_refunds AS
SELECT p.payment_txn_id,p.order_id,p.credit_note_id,p.original_transaction_id,p.method,p.status,
 p.amount::numeric(20,2) customer_refund_value,(p.event_at AT TIME ZONE 'Asia/Kolkata')::date transaction_date
FROM src_payment_transactions p WHERE p.type='REFUND';
ALTER TABLE fact_refunds ADD PRIMARY KEY(payment_txn_id);
ALTER TABLE fact_refunds ADD FOREIGN KEY(order_id) REFERENCES dim_order;
ALTER TABLE fact_refunds ADD FOREIGN KEY(transaction_date) REFERENCES dim_date;
CREATE INDEX ON fact_refunds(transaction_date,order_id);
CREATE TABLE fact_inventory_daily AS
SELECT s.*,p.safety_stock,(s.sellable-s.reserved-p.safety_stock)::bigint available_to_promise
FROM src_inventory_snapshots s LEFT JOIN src_inventory_policies p USING(sku_id,location_id);
ALTER TABLE fact_inventory_daily ADD PRIMARY KEY(snapshot_date,sku_id,location_id);
ALTER TABLE fact_inventory_daily ADD FOREIGN KEY(sku_id) REFERENCES dim_product;
ALTER TABLE fact_inventory_daily ADD FOREIGN KEY(location_id) REFERENCES dim_store;
ALTER TABLE fact_inventory_daily ADD FOREIGN KEY(snapshot_date) REFERENCES dim_date;
CREATE TABLE fact_inventory_movements AS
SELECT (event_at AT TIME ZONE 'Asia/Kolkata')::date movement_date,sku_id,location_id,type,stock_state,
 sum(quantity)::bigint quantity FROM src_inventory_movements GROUP BY 1,2,3,4,5;
ALTER TABLE fact_inventory_movements ADD PRIMARY KEY(movement_date,sku_id,location_id,type,stock_state);
ALTER TABLE fact_inventory_movements ADD FOREIGN KEY(sku_id) REFERENCES dim_product;
ALTER TABLE fact_inventory_movements ADD FOREIGN KEY(location_id) REFERENCES dim_store;
CREATE TABLE fact_footfall_daily AS
SELECT * FROM store_conversion;
ALTER TABLE fact_footfall_daily ADD PRIMARY KEY(store_id,business_date);
ALTER TABLE fact_footfall_daily ADD FOREIGN KEY(store_id) REFERENCES dim_store;
ALTER TABLE fact_footfall_daily ADD FOREIGN KEY(business_date) REFERENCES dim_date;
-- Separate facts are combined as signed events, never joined by order_id.
CREATE VIEW warehouse_sales_events AS
SELECT s.invoice_line_id event_id,'SALE'::text event_type,s.order_line_id,s.recognition_date event_date,
 s.gross_sales net_sales,s.cogs net_cogs,s.units::bigint net_units,s.tax tax_value
FROM fact_sales s
UNION ALL
SELECT r.return_item_id,'CREDIT',s.order_line_id,r.credit_date,-r.merchandise_credit,-r.reversed_cogs,-r.units,-r.tax_credit
FROM fact_returns r JOIN fact_sales s USING(invoice_line_id);
CREATE VIEW warehouse_sales_detail AS
SELECT e.*,o.order_id,o.sku_id,o.customer_id,o.channel,o.store_id,o.state_id,
 p.category_id,p.category_name,p.style_id,p.product_name,l.city,st.name state_name
FROM warehouse_sales_events e JOIN fact_order_items o USING(order_line_id)
JOIN dim_product p USING(sku_id) LEFT JOIN dim_store l ON l.location_id=o.store_id
JOIN dim_state st ON st.state_id=o.state_id;
CREATE VIEW warehouse_monthly_revenue AS
WITH months AS (SELECT DISTINCT month_start FROM dim_date),
 totals AS (SELECT date_trunc('month',event_date)::date month_start,sum(net_sales) net_sales FROM warehouse_sales_events GROUP BY 1),
 series AS (SELECT m.month_start,COALESCE(t.net_sales,0) net_sales FROM months m LEFT JOIN totals t USING(month_start)),
 compared AS (SELECT *,lag(net_sales) OVER(ORDER BY month_start) previous_month,
 lag(net_sales,12) OVER(ORDER BY month_start) previous_year FROM series)
SELECT *,CASE WHEN previous_month>0 THEN 100*(net_sales-previous_month)/previous_month END mom_percent,
 CASE WHEN previous_year>0 THEN 100*(net_sales-previous_year)/previous_year END yoy_percent FROM compared;

-- CTAS does not inherit NOT NULL; enforce essential warehouse measures explicitly.
ALTER TABLE fact_order_items ALTER COLUMN ordered_units SET NOT NULL,
 ALTER COLUMN booked_gmv SET NOT NULL, ALTER COLUMN discount SET NOT NULL,
 ALTER COLUMN booked_merchandise SET NOT NULL, ALTER COLUMN cancelled_units SET NOT NULL;
ALTER TABLE fact_sales ALTER COLUMN units SET NOT NULL, ALTER COLUMN gross_sales SET NOT NULL,
 ALTER COLUMN tax SET NOT NULL, ALTER COLUMN cogs SET NOT NULL;
ALTER TABLE fact_returns ALTER COLUMN units SET NOT NULL, ALTER COLUMN merchandise_credit SET NOT NULL,
 ALTER COLUMN tax_credit SET NOT NULL, ALTER COLUMN reversed_cogs SET NOT NULL;
ALTER TABLE fact_refunds ALTER COLUMN customer_refund_value SET NOT NULL;
CREATE INDEX ON fact_returns(invoice_line_id,accepted_date);
