# Model decisions and Stage 1 traceability

## Authoritative input and scope

The retrieved Stage 1 baseline is `Pasted text.txt`, version 1.0, headed “RetailPulse — Business Requirements & Data Architecture Specification.” The current request overrides its AWS/S3 deployment and its 580,000–730,000 orders over 24 months. A separate approved architecture attachment could not be located. These implementation decisions are therefore documented rather than represented as a new approved contract.

The current deliverable implements the local source database and synthetic generation stage. It includes reconciliation views, not the complete analytics pipeline or dashboards. It does not claim that all Stage 1 dashboard acceptance criteria have passed.

## Concrete dataset choices

| Decision | Implementation |
|---|---|
| Booking window | 2025-01-01 through 2026-06-30 inclusive: 18 months |
| Final observation date | 2026-07-31, allowing fulfillment, returns and refund attempts after the last booking |
| Default volume | Exactly 100,000 confirmed orders, including one deterministic golden order |
| Assortment | 40 styles × 2 colors × 3 sizes = 240 SKUs; all use synthetic supported apparel HSN 6205 |
| Organization | 12 stores in six cities, 2 fulfillment centers, 1 logical transit location |
| Customers | 20,000 Faker names prefixed “Synthetic”; no real contacts or valid GSTINs |
| Channel draws | POS 60%, Web 24%, App 16%; realized proportions vary |
| Basket distribution | 1/2/3/4 units with probabilities 30%/40%/20%/10%; mean 2.1 |
| Demand seasonality | Weekend weight 1.35; Oct–Nov weight 1.6; Jan/Jun weight 1.25 |
| Promotions | 20% markdown first, then optional INR 100 cart coupon, allocated in paise |
| Campaign selection | Markdown probability 65% in Jan/Jun/Oct/Nov, 25% otherwise; coupon 15% |
| Cancellations | Online whole-order draw 4%; otherwise per-line one-unit cancellation draw 2.5% |
| Returns | Unit-level acceptance draws 13% online and 4% POS, requested 1–14 days after delivery |
| Return condition | 85% sellable; 15% quarantined as damaged, with no COGS reversal |
| Refund failures | 8% of ordinary return refund attempts fail; 60% of these retry successfully with a new reference |
| Store closure | Store 12 closed on the first day of each month, with zero entries and no POS bookings |
| Traffic | Independent Poisson draws from expected demand; not back-solved from actual order counts; separate Web/App sessions |
| Stock policy | FIFO, safety stock 1 per SKU/location; weekly replenishment with low S-size targets |
| Snapshots | Derived at each month-end, plus final observation date; daily stock remains reconstructible from movements |

These probabilities are authored scenarios, not Indian retail research or calibrated forecasts. Low-volume smoke datasets intentionally use traffic floors and will have lower conversion than the full dataset. The full-data verifier checks denominators and stock consistency rather than requiring statistical targets to hold exactly.

## Required schema mapping

The original table names and relationships are retained:

- Organization and merchandise: `states`, `gst_registrations`, `locations`, `customers`, `categories`, `product_styles`, `skus`, `price_versions`, `tax_rules`, `promotions`.
- Commercial events: `sales_orders`, `order_lines`, `order_line_discounts`, `order_line_cancellations`, `order_status_events`, `fulfillments`, `fulfillment_items`, `sales_invoices`, `invoice_lines`, `payment_transactions`.
- Returns: `return_requests`, `return_items`, `credit_notes`, `credit_note_lines`.
- Inventory/traffic/lineage: `stock_lots`, `inventory_movements`, `inventory_reservations`, `store_footfall_daily`, `digital_traffic_daily`, `source_events`, `ingestion_batches`.

Six additive tables make otherwise implicit facts explicit: `fulfillment_cost_allocations`, `return_cost_allocations`, `inventory_policies`, `inventory_snapshots`, `payment_status_events`, `demand_rejections`. `etl.schema_version` is an additional technical metadata table. Refunds remain payment transactions with type `REFUND`; there is no disconnected refund total table.

`ingestion_batches.object_path` replaces the obsolete S3-specific path with a local raw batch directory. A batch is represented by the manifest before seeding and a row after loading. Failed loads roll back completely; the CLI's nonzero exit and local console output report failure. No independently committed failure-log row is maintained.

## Money and tax

- All monetary calculations use Decimal with ROUND_HALF_UP to paise. Parquet retains Decimal values; SQL columns use NUMERIC domains.
- Discounts cannot exceed pre-tax list value. Largest-remainder allocation breaks ties by line order and preserves the exact coupon total.
- Original booking prices remain unchanged after cancellations and returns.
- Unit-level invoice lines simplify exact partial returns and preserve each unit's allocated discount and original taxes. An order line may still contain multiple units.
- Before 2025-09-22: up to INR 1,000 is 5%; above is 12%. From 2025-09-22: up to INR 2,500 is 5%; above is 18%.
- Tax date is dispatch/handover, which shares the booking date in this generator. A delivery may occur in the next regime without changing its issued invoice. Complex statutory time-of-supply cases are not asserted to be modeled.
- Intra-state CGST rounds half the total tax, with the remaining paise assigned to SGST. Inter-state tax is IGST. The total always reconciles.
- Fixed prices are effective-dated; this initial dataset has one price version per SKU/channel. MRP uses a ceiling of list price × 1.18, safely above every simulated payable price.
- Credits copy original invoice merchandise and tax amounts. Successful payment refunds are a separate cash measure, never another deduction from net sales.

## Inventory and lifecycle

Orders are processed chronologically. Available stock is tested before confirmation. Rejected unit demand is recorded in `demand_rejections`; an available alternative SKU is selected before a confirmed order is created. If an entire location has no sellable alternatives, an explicit replenishment receipt is recorded before accepting demand. Stockouts are never hidden as negative inventory.

FIFO consumes the oldest available lot by original receipt timestamp and lot ID. Returned sellable units re-enter the original lot and carry the original unit cost. Historical replenishment costs vary by ±4%, except SKU 1 whose INR 1,200 FIFO cost is fixed to support the golden test.

Operational picking/packing steps are compressed to the confirmation instant. Reservations are consumed at immediate dispatch/handover, so this generated dataset has no active end-of-day reservations. Their schema and timestamps are retained; ongoing open-reservation scenarios are future extensions, not fabricated as populated here. All fulfillment and return outcomes are observed by the final as-of date. The sample is a settled historical dataset, not a live queue simulation.

Online dispatch moves stock from a fulfillment center to transit. Delivery removes transit inventory and recognizes COGS. Split orders use two fulfillment/invoice parents; closure occurs after the last delivery. POS handover recognizes the sale immediately. Return requests are within 14 days of delivery; acceptance is one day later. Damaged returns add non-sellable physical stock, not sellable stock.

Movement types for stock adjustment and general inter-location transfers are supported by DDL; this generator populates opening, purchase, dispatch transfer, sale, return and damage scenarios. It does not fabricate regular inter-store redistribution merely to populate every enum value.

## Golden transaction

`SYNTH-GOLDEN-001`, 2025-10-01, store 1:

| Measure | Expected |
|---|---:|
| Two shirts at INR 3,000 | GMV 6,000 |
| 20% initial discount | 1,200 |
| Booked/recognized merchandise | 4,800 |
| Original GST | 240 |
| Captured payment | 5,040 |
| One accepted restockable return | Merchandise credit 2,400; GST credit 120 |
| Successful refund | 2,520 |
| Net sales / COGS / profit | 2,400 / 1,200 / 1,200 |
| Margin / net units | 50% / 1 |

This is checked in Python and SQL. No dashboard result is claimed at this stage.

## Enforcement boundaries

PostgreSQL enforces foreign keys, unique business identifiers, nonnegative money, positive quantities, fulfillment/cancellation limits, return limits/window, credit caps, refund capture caps/routes, tax validity, and price/tax overlap checks. Posted financial tables reject UPDATE/DELETE; reset deliberately uses TRUNCATE under an exact database-name guard.

Batch verification additionally checks FIFO movement balances, ledger snapshots, traffic, historical costs and the golden transaction. The owner role can always bypass or change database protections; this is an educational local batch system, not an adversarial multi-tenant transactional service. Mutable fulfillment/payment state is intended for a future controlled application, not arbitrary direct SQL writes. All new batch loads must go through the validation path.

## Next-stage infrastructure boundary

The local database remains authoritative. Local Parquet files are replayable batches. PostgreSQL analytics schemas can be rebuilt. A future public Streamlit demonstration should read a small, reviewed, explicitly synthetic export bundled with its repository; it must never connect to a laptop database or require a Supabase mirror. No export or public deployment occurs in this stage.

Incremental extraction, update/delete capture, high-water marks, periodic refresh scheduling, dashboard filters, matured-cohort return rates and sell-through rollups remain next-stage work. The initial seed is deliberately not misrepresented as incremental synchronization.
