SET search_path = source, public;
-- All guards use a transaction-scoped lock per order to serialize related writes.
-- The loader is a single writer. External multiwriter apps must use READ COMMITTED.
CREATE FUNCTION immutable_record() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN RAISE EXCEPTION 'Posted financial records are immutable: use corrective events'; END $$;
DO $$ DECLARE t text; BEGIN
 FOREACH t IN ARRAY ARRAY['sales_orders','order_lines','order_line_discounts','sales_invoices','invoice_lines','credit_notes','credit_note_lines','inventory_movements','fulfillment_cost_allocations','return_cost_allocations'] LOOP
 EXECUTE format('CREATE TRIGGER immutable BEFORE UPDATE OR DELETE ON source.%I FOR EACH ROW EXECUTE FUNCTION source.immutable_record()',t);
 END LOOP;
END $$;
CREATE FUNCTION check_order_quantities() RETURNS trigger LANGUAGE plpgsql SET search_path=source,public AS $$
DECLARE line_id bigint; ordered integer; done bigint; cancelled bigint; order_a bigint; order_b bigint;
BEGIN
 line_id:=NEW.order_line_id;
 SELECT quantity,order_id INTO ordered,order_a FROM order_lines WHERE order_line_id=line_id FOR UPDATE;
 IF TG_TABLE_NAME='fulfillment_items' THEN
  SELECT order_id INTO order_b FROM fulfillments WHERE fulfillment_id=NEW.fulfillment_id;
  IF order_a<>order_b THEN RAISE EXCEPTION 'Fulfillment belongs to another order'; END IF;
 END IF;
 SELECT COALESCE(sum(quantity),0) INTO done FROM fulfillment_items WHERE order_line_id=line_id;
 SELECT COALESCE(sum(quantity),0) INTO cancelled FROM order_line_cancellations WHERE order_line_id=line_id;
 IF done+cancelled>ordered THEN RAISE EXCEPTION 'Fulfilled plus cancelled exceeds ordered'; END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER check_quantities AFTER INSERT OR UPDATE ON fulfillment_items FOR EACH ROW EXECUTE FUNCTION check_order_quantities();
CREATE TRIGGER check_quantities AFTER INSERT OR UPDATE ON order_line_cancellations FOR EACH ROW EXECUTE FUNCTION check_order_quantities();
CREATE FUNCTION check_return_quantity() RETURNS trigger LANGUAGE plpgsql SET search_path=source,public AS $$
DECLARE original invoice_lines; n bigint; a bigint; b bigint; delivered timestamptz; requested timestamptz;
BEGIN
 SELECT * INTO original FROM invoice_lines WHERE invoice_line_id=NEW.invoice_line_id FOR UPDATE;
 SELECT sum(quantity) INTO n FROM return_items WHERE invoice_line_id=NEW.invoice_line_id;
 IF n>original.quantity THEN RAISE EXCEPTION 'Returns exceed invoiced quantity'; END IF;
 SELECT i.order_id,f.delivered_at INTO a,delivered FROM sales_invoices i JOIN fulfillments f USING(fulfillment_id) WHERE i.invoice_id=original.invoice_id;
 SELECT order_id,requested_at INTO b,requested FROM return_requests WHERE return_id=NEW.return_id;
 IF a<>b OR delivered IS NULL OR requested<delivered OR requested>delivered+interval '14 days' THEN
 RAISE EXCEPTION 'Invalid return order or return window'; END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER return_limit AFTER INSERT OR UPDATE ON return_items FOR EACH ROW EXECUTE FUNCTION check_return_quantity();
CREATE FUNCTION check_credit() RETURNS trigger LANGUAGE plpgsql SET search_path=source,public AS $$
DECLARE original invoice_lines; ri return_items; cn credit_notes; sums record;
BEGIN
 SELECT * INTO original FROM invoice_lines WHERE invoice_line_id=NEW.invoice_line_id FOR UPDATE;
 SELECT * INTO ri FROM return_items WHERE return_item_id=NEW.return_item_id;
 SELECT * INTO cn FROM credit_notes WHERE credit_note_id=NEW.credit_note_id;
 IF ri.invoice_line_id<>NEW.invoice_line_id OR ri.return_id<>cn.return_id OR cn.invoice_id<>original.invoice_id OR NEW.quantity<>ri.quantity THEN
 RAISE EXCEPTION 'Credit lineage mismatch'; END IF;
 SELECT sum(quantity) q,sum(taxable_value) v,sum(cgst) c,sum(sgst) s,sum(igst) i INTO sums FROM credit_note_lines WHERE invoice_line_id=NEW.invoice_line_id;
 IF sums.q>original.quantity OR sums.v>original.taxable_value OR sums.c>original.cgst OR sums.s>original.sgst OR sums.i>original.igst THEN
 RAISE EXCEPTION 'Credit exceeds original invoice'; END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER credit_limit AFTER INSERT OR UPDATE ON credit_note_lines FOR EACH ROW EXECUTE FUNCTION check_credit();
CREATE FUNCTION check_refund() RETURNS trigger LANGUAGE plpgsql SET search_path=source,public AS $$
DECLARE capture payment_transactions; refunded numeric;
BEGIN
 IF NEW.type='REFUND' THEN
 SELECT * INTO capture FROM payment_transactions WHERE payment_txn_id=NEW.original_transaction_id FOR UPDATE;
 IF capture.type<>'CAPTURE' OR capture.status<>'SUCCEEDED' OR capture.order_id<>NEW.order_id OR capture.method<>NEW.method THEN
 RAISE EXCEPTION 'Refund must reference captured funds from the same order and route'; END IF;
 SELECT COALESCE(sum(amount),0) INTO refunded FROM payment_transactions WHERE original_transaction_id=capture.payment_txn_id AND status='SUCCEEDED';
 IF refunded>capture.amount THEN RAISE EXCEPTION 'Refunds exceed captured funds'; END IF;
 END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER refund_limit AFTER INSERT OR UPDATE ON payment_transactions FOR EACH ROW EXECUTE FUNCTION check_refund();
CREATE FUNCTION check_discount() RETURNS trigger LANGUAGE plpgsql SET search_path=source,public AS $$
DECLARE available numeric; allocated numeric;
BEGIN
 SELECT quantity*original_unit_price INTO available FROM order_lines WHERE order_line_id=NEW.order_line_id FOR UPDATE;
 SELECT sum(amount) INTO allocated FROM order_line_discounts WHERE order_line_id=NEW.order_line_id;
 IF allocated>available THEN RAISE EXCEPTION 'Discount exceeds merchandise value'; END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER discount_limit AFTER INSERT ON order_line_discounts FOR EACH ROW EXECUTE FUNCTION check_discount();
CREATE FUNCTION check_effective_overlap() RETURNS trigger LANGUAGE plpgsql SET search_path=source,public AS $$
BEGIN
 PERFORM pg_advisory_xact_lock(847212);
 IF TG_TABLE_NAME='price_versions' THEN
  IF EXISTS(SELECT 1 FROM price_versions p WHERE p.price_id<>NEW.price_id AND p.sku_id=NEW.sku_id AND p.channel=NEW.channel
   AND daterange(p.effective_from,p.effective_to,'[)') && daterange(NEW.effective_from,NEW.effective_to,'[)')) THEN
   RAISE EXCEPTION 'Overlapping price versions'; END IF;
 ELSE
  IF EXISTS(SELECT 1 FROM tax_rules t WHERE t.tax_rule_id<>NEW.tax_rule_id AND t.hsn=NEW.hsn
   AND daterange(t.effective_from,t.effective_to,'[)') && daterange(NEW.effective_from,NEW.effective_to,'[)')
   AND numrange(t.lower_exclusive,t.upper_inclusive,'(]') && numrange(NEW.lower_exclusive,NEW.upper_inclusive,'(]')) THEN
   RAISE EXCEPTION 'Overlapping tax rules'; END IF;
 END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER effective_overlap AFTER INSERT OR UPDATE ON price_versions FOR EACH ROW EXECUTE FUNCTION check_effective_overlap();
CREATE TRIGGER effective_overlap AFTER INSERT OR UPDATE ON tax_rules FOR EACH ROW EXECUTE FUNCTION check_effective_overlap();
CREATE FUNCTION check_invoice_line() RETURNS trigger LANGUAGE plpgsql SET search_path=source,public AS $$
DECLARE item fulfillment_items; invoice sales_invoices; fulfillment fulfillments; rule tax_rules; order_row sales_orders; location_row locations;
BEGIN
 SELECT * INTO item FROM fulfillment_items WHERE fulfillment_item_id=NEW.fulfillment_item_id;
 SELECT * INTO invoice FROM sales_invoices WHERE invoice_id=NEW.invoice_id;
 SELECT * INTO fulfillment FROM fulfillments WHERE fulfillment_id=item.fulfillment_id;
 SELECT * INTO rule FROM tax_rules WHERE tax_rule_id=NEW.tax_rule_id;
 SELECT * INTO order_row FROM sales_orders WHERE order_id=invoice.order_id;
 SELECT * INTO location_row FROM locations WHERE location_id=fulfillment.source_location_id;
 IF item.fulfillment_id<>invoice.fulfillment_id OR fulfillment.order_id<>invoice.order_id OR item.quantity<>NEW.quantity
 OR invoice.registration_id<>location_row.registration_id THEN RAISE EXCEPTION 'Invoice lineage mismatch'; END IF;
 IF NOT(invoice.invoice_date>=rule.effective_from AND invoice.invoice_date<rule.effective_to
 AND NEW.taxable_value/NEW.quantity>rule.lower_exclusive AND NEW.taxable_value/NEW.quantity<=rule.upper_inclusive)
 OR NEW.cgst+NEW.sgst+NEW.igst<>round(NEW.taxable_value*rule.gst_rate,2) THEN RAISE EXCEPTION 'Invalid invoice tax'; END IF;
 IF (location_row.state_id=order_row.place_of_supply_state_id AND (NEW.igst<>0 OR abs(NEW.cgst-NEW.sgst)>.01))
 OR (location_row.state_id<>order_row.place_of_supply_state_id AND (NEW.cgst<>0 OR NEW.sgst<>0)) THEN RAISE EXCEPTION 'Invalid GST jurisdiction'; END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER invoice_guard AFTER INSERT ON invoice_lines FOR EACH ROW EXECUTE FUNCTION check_invoice_line();
CREATE FUNCTION check_stock_lot() RETURNS trigger LANGUAGE plpgsql SET search_path=source,public AS $$
BEGIN
 IF NOT EXISTS(SELECT 1 FROM stock_lots WHERE lot_id=NEW.lot_id AND sku_id=NEW.sku_id) THEN RAISE EXCEPTION 'Stock lot SKU mismatch'; END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER stock_lot_guard BEFORE INSERT ON inventory_movements FOR EACH ROW EXECUTE FUNCTION check_stock_lot();
