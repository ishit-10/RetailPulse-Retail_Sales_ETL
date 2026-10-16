-- RetailPulse v1: fictional operational data only. Run transactionally once.
CREATE SCHEMA source;
CREATE SCHEMA analytics;
CREATE SCHEMA etl;
SET search_path = source, public;
CREATE DOMAIN money_amount AS numeric(16,2) CHECK (VALUE >= 0);
CREATE TABLE ingestion_batches (
 batch_id bigint PRIMARY KEY, source_identifier text UNIQUE NOT NULL,
 object_path text NOT NULL, checksum text NOT NULL, loaded_at timestamptz,
 status text NOT NULL CHECK(status IN ('GENERATED','LOADED'))
);
CREATE TABLE source_events (
 event_id bigint PRIMARY KEY, source_identity text UNIQUE NOT NULL,
 source_system text NOT NULL, event_type text NOT NULL, event_at timestamptz NOT NULL,
 batch_id bigint NOT NULL REFERENCES ingestion_batches
);
CREATE TABLE states (state_id bigint PRIMARY KEY, name text UNIQUE NOT NULL, gst_state_code text UNIQUE NOT NULL);
CREATE TABLE gst_registrations (
 registration_id bigint PRIMARY KEY, state_id bigint NOT NULL REFERENCES states,
 synthetic_gstin text UNIQUE NOT NULL CHECK(synthetic_gstin LIKE 'SYNTH-%'), status text NOT NULL
);
CREATE TABLE locations (
 location_id bigint PRIMARY KEY, state_id bigint NOT NULL REFERENCES states,
 registration_id bigint NOT NULL REFERENCES gst_registrations, name text NOT NULL, city text NOT NULL,
 type text NOT NULL CHECK(type IN ('STORE','FULFILLMENT_CENTER','TRANSIT')), opening_date date NOT NULL
);
CREATE TABLE customers (customer_id bigint PRIMARY KEY, customer_reference text UNIQUE NOT NULL, synthetic_name text NOT NULL, created_at timestamptz NOT NULL);
CREATE TABLE categories (category_id bigint PRIMARY KEY, parent_category_id bigint REFERENCES categories, name text UNIQUE NOT NULL);
CREATE TABLE product_styles (
 style_id bigint PRIMARY KEY, category_id bigint NOT NULL REFERENCES categories,
 style_code text UNIQUE NOT NULL, name text NOT NULL, hsn text NOT NULL, collection text NOT NULL, launch_date date NOT NULL
);
CREATE TABLE skus (
 sku_id bigint PRIMARY KEY, style_id bigint NOT NULL REFERENCES product_styles, barcode text UNIQUE NOT NULL,
 color text NOT NULL, size text NOT NULL, active boolean NOT NULL, UNIQUE(style_id,color,size)
);
CREATE TABLE price_versions (
 price_id bigint PRIMARY KEY, sku_id bigint NOT NULL REFERENCES skus, channel text NOT NULL CHECK(channel IN ('POS','WEB','APP')),
 list_price money_amount NOT NULL, mrp money_amount NOT NULL, effective_from date NOT NULL, effective_to date NOT NULL,
 CHECK(effective_to > effective_from), UNIQUE(sku_id,channel,effective_from)
);
CREATE TABLE tax_rules (
 tax_rule_id bigint PRIMARY KEY, hsn text NOT NULL, lower_exclusive numeric(16,2) NOT NULL,
 upper_inclusive numeric(16,2) NOT NULL, gst_rate numeric(5,4) NOT NULL CHECK(gst_rate BETWEEN 0 AND 1),
 effective_from date NOT NULL, effective_to date NOT NULL,
 CHECK(upper_inclusive > lower_exclusive), CHECK(effective_to > effective_from)
);
CREATE TABLE promotions (
 promotion_id bigint PRIMARY KEY, code text UNIQUE NOT NULL, type text NOT NULL CHECK(type IN ('PERCENT','FIXED')),
 value money_amount NOT NULL, eligibility_rules text NOT NULL, valid_from date NOT NULL, valid_to date NOT NULL,
 CHECK(valid_to >= valid_from)
);
CREATE TABLE sales_orders (
 order_id bigint PRIMARY KEY, external_reference text UNIQUE NOT NULL,
 customer_id bigint REFERENCES customers, channel text NOT NULL CHECK(channel IN ('POS','WEB','APP')),
 originating_store_id bigint REFERENCES locations, place_of_supply_state_id bigint NOT NULL REFERENCES states,
 placed_at timestamptz NOT NULL, confirmed_at timestamptz NOT NULL,
 event_id bigint NOT NULL REFERENCES source_events, CHECK(confirmed_at >= placed_at),
 CHECK((channel='POS' AND originating_store_id IS NOT NULL) OR (channel<>'POS' AND originating_store_id IS NULL))
);
CREATE TABLE order_lines (
 order_line_id bigint PRIMARY KEY, order_id bigint NOT NULL REFERENCES sales_orders, sku_id bigint NOT NULL REFERENCES skus,
 line_number integer NOT NULL, quantity integer NOT NULL CHECK(quantity>0), original_unit_price money_amount NOT NULL,
 price_id bigint NOT NULL REFERENCES price_versions, UNIQUE(order_id,line_number)
);
CREATE TABLE order_line_discounts (
 discount_id bigint PRIMARY KEY, order_line_id bigint NOT NULL REFERENCES order_lines, promotion_id bigint NOT NULL REFERENCES promotions,
 amount money_amount NOT NULL
);
CREATE TABLE order_line_cancellations (
 cancellation_id bigint PRIMARY KEY, order_line_id bigint NOT NULL REFERENCES order_lines, quantity integer NOT NULL CHECK(quantity>0),
 reason text NOT NULL, cancelled_at timestamptz NOT NULL, event_id bigint NOT NULL REFERENCES source_events
);
CREATE TABLE order_status_events (
 status_event_id bigint PRIMARY KEY, order_id bigint NOT NULL REFERENCES sales_orders, old_status text,
 new_status text NOT NULL, event_at timestamptz NOT NULL, event_id bigint NOT NULL REFERENCES source_events
);
CREATE TABLE fulfillments (
 fulfillment_id bigint PRIMARY KEY, order_id bigint NOT NULL REFERENCES sales_orders, source_location_id bigint NOT NULL REFERENCES locations,
 type text NOT NULL CHECK(type IN ('HANDOVER','SHIPMENT')), status text NOT NULL CHECK(status IN ('DISPATCHED','DELIVERED')),
 dispatched_at timestamptz NOT NULL, delivered_at timestamptz, event_id bigint NOT NULL REFERENCES source_events,
 CHECK(delivered_at IS NULL OR delivered_at>=dispatched_at)
);
CREATE TABLE fulfillment_items (
 fulfillment_item_id bigint PRIMARY KEY, fulfillment_id bigint NOT NULL REFERENCES fulfillments,
 order_line_id bigint NOT NULL REFERENCES order_lines, quantity integer NOT NULL CHECK(quantity>0)
);
CREATE TABLE sales_invoices (
 invoice_id bigint PRIMARY KEY, invoice_number text UNIQUE NOT NULL, order_id bigint NOT NULL REFERENCES sales_orders,
 fulfillment_id bigint NOT NULL REFERENCES fulfillments, registration_id bigint NOT NULL REFERENCES gst_registrations,
 invoice_date date NOT NULL, event_id bigint NOT NULL REFERENCES source_events
);
CREATE TABLE invoice_lines (
 invoice_line_id bigint PRIMARY KEY, invoice_id bigint NOT NULL REFERENCES sales_invoices,
 fulfillment_item_id bigint UNIQUE NOT NULL REFERENCES fulfillment_items, tax_rule_id bigint NOT NULL REFERENCES tax_rules,
 quantity integer NOT NULL CHECK(quantity>0), taxable_value money_amount NOT NULL,
 cgst money_amount NOT NULL, sgst money_amount NOT NULL, igst money_amount NOT NULL,
 CHECK((igst=0) OR (cgst=0 AND sgst=0))
);
CREATE TABLE return_requests (
 return_id bigint PRIMARY KEY, order_id bigint NOT NULL REFERENCES sales_orders, requested_at timestamptz NOT NULL,
 accepted_at timestamptz NOT NULL, status text NOT NULL CHECK(status='ACCEPTED'),
 receiving_location_id bigint NOT NULL REFERENCES locations, event_id bigint NOT NULL REFERENCES source_events,
 CHECK(accepted_at>=requested_at)
);
CREATE TABLE return_items (
 return_item_id bigint PRIMARY KEY, return_id bigint NOT NULL REFERENCES return_requests,
 invoice_line_id bigint NOT NULL REFERENCES invoice_lines, quantity integer NOT NULL CHECK(quantity>0),
 reason text NOT NULL, condition text NOT NULL CHECK(condition IN ('SELLABLE','DAMAGED')),
 disposition text NOT NULL CHECK(disposition IN ('RESTOCK','QUARANTINE')),
 CHECK((condition='SELLABLE')=(disposition='RESTOCK'))
);
CREATE TABLE credit_notes (
 credit_note_id bigint PRIMARY KEY, credit_number text UNIQUE NOT NULL, return_id bigint NOT NULL REFERENCES return_requests,
 invoice_id bigint NOT NULL REFERENCES sales_invoices, registration_id bigint NOT NULL REFERENCES gst_registrations,
 issued_at timestamptz NOT NULL, event_id bigint NOT NULL REFERENCES source_events
);
CREATE TABLE credit_note_lines (
 credit_line_id bigint PRIMARY KEY, credit_note_id bigint NOT NULL REFERENCES credit_notes,
 return_item_id bigint UNIQUE NOT NULL REFERENCES return_items, invoice_line_id bigint NOT NULL REFERENCES invoice_lines,
 quantity integer NOT NULL CHECK(quantity>0), taxable_value money_amount NOT NULL,
 cgst money_amount NOT NULL, sgst money_amount NOT NULL, igst money_amount NOT NULL
);
CREATE TABLE payment_transactions (
 payment_txn_id bigint PRIMARY KEY, order_id bigint NOT NULL REFERENCES sales_orders,
 type text NOT NULL CHECK(type IN ('CAPTURE','REFUND')), method text NOT NULL CHECK(method IN ('UPI','CARD','CASH','NETBANKING')),
 amount money_amount NOT NULL, status text NOT NULL CHECK(status IN ('INITIATED','APPROVED','PROCESSING','SUCCEEDED','FAILED')),
 provider_reference text UNIQUE NOT NULL, original_transaction_id bigint REFERENCES payment_transactions,
 credit_note_id bigint REFERENCES credit_notes, event_at timestamptz NOT NULL, event_id bigint NOT NULL REFERENCES source_events,
 CHECK((type='CAPTURE' AND original_transaction_id IS NULL) OR (type='REFUND' AND original_transaction_id IS NOT NULL))
);
CREATE TABLE payment_status_events (
 payment_status_event_id bigint PRIMARY KEY, payment_txn_id bigint NOT NULL REFERENCES payment_transactions,
 status text NOT NULL CHECK(status IN ('INITIATED','APPROVED','PROCESSING','SUCCEEDED','FAILED')), event_at timestamptz NOT NULL
);
CREATE TABLE stock_lots (
 lot_id bigint PRIMARY KEY, sku_id bigint NOT NULL REFERENCES skus, receipt_reference text UNIQUE NOT NULL,
 receipt_at timestamptz NOT NULL, initial_quantity integer NOT NULL CHECK(initial_quantity>0), unit_cost money_amount NOT NULL
);
CREATE TABLE inventory_movements (
 movement_id bigint PRIMARY KEY, sku_id bigint NOT NULL REFERENCES skus, location_id bigint NOT NULL REFERENCES locations,
 lot_id bigint NOT NULL REFERENCES stock_lots, quantity integer NOT NULL CHECK(quantity<>0),
 type text NOT NULL CHECK(type IN ('OPENING_BALANCE','PURCHASE_RECEIPT','TRANSFER_OUT','TRANSFER_IN','SALE','RETURN_RESTOCK','DAMAGE','STOCK_ADJUSTMENT')),
 stock_state text NOT NULL CHECK(stock_state IN ('SELLABLE','TRANSIT','DAMAGED')),
 event_at timestamptz NOT NULL, event_id bigint NOT NULL REFERENCES source_events,
 fulfillment_item_id bigint REFERENCES fulfillment_items, return_item_id bigint REFERENCES return_items
);
CREATE TABLE fulfillment_cost_allocations (
 allocation_id bigint PRIMARY KEY, fulfillment_item_id bigint NOT NULL REFERENCES fulfillment_items,
 lot_id bigint NOT NULL REFERENCES stock_lots, quantity integer NOT NULL CHECK(quantity>0), unit_cost money_amount NOT NULL
);
CREATE TABLE return_cost_allocations (
 return_allocation_id bigint PRIMARY KEY, return_item_id bigint NOT NULL REFERENCES return_items,
 allocation_id bigint NOT NULL REFERENCES fulfillment_cost_allocations, quantity integer NOT NULL CHECK(quantity>0)
);
CREATE TABLE inventory_reservations (
 reservation_id bigint PRIMARY KEY, order_line_id bigint NOT NULL REFERENCES order_lines,
 location_id bigint NOT NULL REFERENCES locations, quantity integer NOT NULL CHECK(quantity>0),
 status text NOT NULL CHECK(status IN ('ACTIVE','RELEASED','CONSUMED')),
 created_at timestamptz NOT NULL, released_at timestamptz, CHECK(released_at IS NULL OR released_at>=created_at)
);
CREATE TABLE inventory_policies (
 sku_id bigint REFERENCES skus, location_id bigint REFERENCES locations, safety_stock integer NOT NULL CHECK(safety_stock>=0),
 PRIMARY KEY(sku_id,location_id)
);
CREATE TABLE inventory_snapshots (
 snapshot_date date NOT NULL, sku_id bigint REFERENCES skus, location_id bigint REFERENCES locations,
 sellable integer NOT NULL CHECK(sellable>=0), reserved integer NOT NULL CHECK(reserved>=0),
 in_transit integer NOT NULL CHECK(in_transit>=0), damaged integer NOT NULL CHECK(damaged>=0),
 PRIMARY KEY(snapshot_date,sku_id,location_id)
);
CREATE TABLE store_footfall_daily (
 store_id bigint REFERENCES locations, business_date date NOT NULL, valid_entries integer NOT NULL CHECK(valid_entries>=0),
 excluded_entries integer NOT NULL CHECK(excluded_entries>=0), sensor_status text NOT NULL,
 PRIMARY KEY(store_id,business_date)
);
CREATE TABLE digital_traffic_daily (
 business_date date NOT NULL, channel text CHECK(channel IN ('WEB','APP')), eligible_sessions integer NOT NULL CHECK(eligible_sessions>=0),
 excluded_sessions integer NOT NULL CHECK(excluded_sessions>=0), PRIMARY KEY(business_date,channel)
);
CREATE TABLE demand_rejections (
 rejection_id bigint PRIMARY KEY, sku_id bigint REFERENCES skus, location_id bigint REFERENCES locations,
 event_at timestamptz NOT NULL, quantity integer NOT NULL CHECK(quantity>0), available_to_promise integer NOT NULL,
 reason text NOT NULL
);
CREATE INDEX orders_confirmed ON sales_orders(confirmed_at);
CREATE INDEX order_lines_order ON order_lines(order_id);
CREATE INDEX fulfillment_order ON fulfillments(order_id);
CREATE INDEX fulfillment_items_line ON fulfillment_items(order_line_id);
CREATE INDEX cancellation_line ON order_line_cancellations(order_line_id);
CREATE INDEX discount_line ON order_line_discounts(order_line_id);
CREATE INDEX invoice_lines_invoice ON invoice_lines(invoice_id);
CREATE INDEX return_invoice ON return_items(invoice_line_id);
CREATE INDEX credits_invoice_line ON credit_note_lines(invoice_line_id);
CREATE INDEX payment_order ON payment_transactions(order_id);
CREATE INDEX payment_parent ON payment_transactions(original_transaction_id);
CREATE INDEX movements_stock ON inventory_movements(location_id,sku_id,event_at,movement_id);
CREATE INDEX allocations_fulfillment ON fulfillment_cost_allocations(fulfillment_item_id);
CREATE INDEX events_time ON source_events(event_at,event_id);
