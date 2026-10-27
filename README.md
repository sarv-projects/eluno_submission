# Eluno POS exercise

## What the exercise asks for

Build a local billing app to replace Shopify POS at the store counter, while Shopify remains the system of record. It should work against the supplied Shopify, store-tool and payment-terminal mocks. The submission also includes a stakeholder reply and a short Loom demo; the Loom is shared separately from this repository.

This repository contains the stakeholder reply, the billing app, tests and the supplied mock data. The app runs in Python 3.8+ and uses only the standard library.

## Must requirements

All seven Must items are implemented:

1. Look up a Shopify customer by phone and fetch their latest valid prescription from the store tool.
2. Build frame-and-lens pairs from catalog variants; check the lens against the prescription and explain invalid choices so staff can fix them.
3. Apply active offers from `offers.json` using their dates, conditions and stackability rules.
4. Calculate inclusive GST by HSN for each line, with taxable value, CGST and SGST reconciled to paise.
5. Charge the terminal, then create the Shopify order with its line items, taxes, discounts, sale transaction, prescription details and a separate per-store invoice number.
6. Process a full refund through Shopify, reverse the tax, restock the frame at the selling store and leave the made-to-order lens out of stock.
7. Build a store-and-date day-end report from Shopify orders, including bill count, gross, taxable, tax, discounts, refunds and payment-method totals.

## Should and Stretch

Of the Should items, the sales-channel work is implemented: checkout records `walk_in`, `whatsapp_booked` or `online_pickup`, and the day-end report breaks totals down by channel. The other Should items are not included:

- Cash drawer sessions, paid-ins, paid-outs and closing variance.
- Partial refunds.
- A printable HTML or PDF invoice.
- A separate audit log. Manager overrides do record the approver and reason on the Shopify order, but that is not a full audit-history system.

None of the Stretch items are implemented: offer authoring from plain English, prescription-entry assistance, or billing-time anomaly flags.

## Run it

From this directory, run the end-to-end demo or the tests:

```bash
./run_demo.sh
python3 -m unittest discover -v
```

The demo starts the mock on port `18787`, completes a checkout and refund, prints the day-end report, then stops the mock. It stores its local invoice and checkout state in `pos_demo.sqlite3`; the file is ignored by Git. To use a different port or database:

```bash
PORT=19000 DB=/tmp/eluno-demo.sqlite3 ./run_demo.sh
```

The tests start their own mock. No separate server is needed for either command.

## Try an individual command

If you want to see a quote, checkout, refund or report separately, start the mock in one terminal:

```bash
python3 starter-kit/mock-apis/mock_server.py --port 9000
```

Then run the app from another terminal in this directory. `quote` validates the cart without charging:

```bash
python3 -m pos.cli --base-url http://127.0.0.1:9000 quote \
  --phone +919000000005 --store HSR --staff S105 \
  --pair 91000003:91000083 --date 2026-10-27
```

`checkout` charges the terminal and creates the Shopify order. The pair format is `FRAME_VARIANT:LENS_VARIANT[:RX_ID]`; repeat `--pair` to add more pairs for the same customer. Leave off `RX_ID` to use the latest valid prescription.

```bash
python3 -m pos.cli --base-url http://127.0.0.1:9000 checkout \
  --phone +919000000005 --store HSR --staff S105 \
  --pair 91000003:91000083 --channel walk_in --payment upi \
  --date 2026-10-27 --checkout-key HSR-DEMO-001
```

A refund uses the invoice number returned by checkout. The supplied HSR data has two invoices already, so a fresh mock starts the next one at `ELU/HSR/FY26/00003`.

```bash
python3 -m pos.cli --base-url http://127.0.0.1:9000 refund \
  --invoice ELU/HSR/FY26/00003 --store HSR \
  --reason 'Customer changed mind' --reason-code CHANGED_MIND \
  --refund-key HSR-DEMO-REFUND-001

python3 -m pos.cli --base-url http://127.0.0.1:9000 report \
  --store HSR --date 2026-10-27
```

A manager discount needs an authorized manager, a reason code and an approver ID. It is marked non-stackable in the offer data, so asking for it replaces the automatic offers; run a quote without the manager flag to compare first.

```bash
python3 -m pos.cli --base-url http://127.0.0.1:9000 checkout \
  --phone +919000000005 --store HSR --staff S105 \
  --pair 91000003:91000083 --channel walk_in --payment upi \
  --date 2026-10-27 --checkout-key HSR-MANAGER-001 \
  --manager-discount-percent 5 --reason-code WALKOUT --approver-id S108
```

## Decisions and limits

Money stays in integer paise in the app; decimal strings are used at the Shopify boundary. Offers are selected by maximum customer saving among the permitted combinations. A requested manager override is mandatory, and because it is non-stackable it stands alone. These are explicit choices in the exercise, not production policy signed off by Retail or Finance.

Checkout writes a local intent before external calls. Reuse the same checkout key and inputs to resume or reconcile an interrupted attempt; don't create a new key just because the terminal response timed out. Ambiguous writes stop for reconciliation rather than being replayed blindly.

There are limits in the supplied mocks that matter:

- Repeating a successful terminal charge with the same `order_ref` can create another charge, despite the mock documentation. Terminal refunds have no idempotency key or status lookup, so an uncertain terminal result is not retried automatically.
- Shopify order creation is not idempotent, and the mock order list returns at most 250 oldest orders. The app scans for its checkout key before retrying, but a production integration needs pagination and a reliable lookup-by-key or webhook.
- Invoice counters are kept in one local SQLite database. That is enough for this single-process exercise, not a shared allocator for seven stores. Finance still needs to confirm fiscal-year rollover and void rules.
- The report reads from Shopify; sales are counted on the sale date and refunds on the refund date. Finance should confirm that treatment. The mock's 250-order limit can also affect reports.
- This is not offline billing. A network failure leaves a saved pending intent; it does not reserve stock or authorize taking payment offline.

The email says the exercise window is 72 hours, while the attached brief says 48. That discrepancy is noted in `STAKEHOLDER_REPLY.md`.

## Tests

`python3 -m unittest discover -v` runs 14 tests covering tax and rounding, offer selection, second-pair discounts, manager authorization, prescription validation, checkout replay, refund/restock behaviour, channel reporting and 429/503 retries. The supplied fixtures also pass `python3 starter-kit/validate_seed.py`.

See [ARCHITECTURE.md](ARCHITECTURE.md) for the diagrams and [STAKEHOLDER_REPLY.md](STAKEHOLDER_REPLY.md) for the Part A response.
