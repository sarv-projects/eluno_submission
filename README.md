# Eluno POS exercise

## What the exercise asks for

The exercise has three parts: reply to the stakeholder thread, build the billing app, and record a short Loom demo. This repo has the reply, app, tests and mock data; the Loom link is sent separately. The app runs in Python 3.8+ and uses only the standard library.

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

I picked the sales-channel item from Should. Checkout records `walk_in`, `whatsapp_booked` or `online_pickup`, and the report splits totals by channel. I left the other Should items out:

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

## Run a command separately

Start the mock in one terminal:

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

I keep money in paise and convert it to decimal strings only when sending it to Shopify. For offers, the app picks the permitted combination with the biggest saving. If a manager requests the non-stackable override, it replaces the automatic offers.

Before checkout calls another system, it saves an intent and a key. If an attempt is interrupted, use the same key and inputs to resume it. If the app can't tell whether a payment or order went through, it stops for reconciliation. Don't retry with a new key; that could create a second charge or order.

Some limits come from the mock; others come from keeping this a local, single-process exercise:

- The mock says terminal charges are idempotent by `order_ref`, but repeating a successful charge can create another one. Terminal refunds have no idempotency key or status lookup, so an uncertain refund needs manual reconciliation.
- Shopify order creation isn't idempotent. The app searches for its checkout key before retrying, but the mock only returns the 250 oldest orders. A real integration needs pagination and a reliable lookup by key or a webhook.
- Invoice numbers live in one local SQLite database. That works for this single-process exercise, not seven stores or several tills. Finance still needs to confirm the fiscal-year rollover and void rules.
- The report reads from Shopify and counts sales on the sale date, refunds on the refund date. Finance should confirm that rule. The 250-order limit can also leave the report incomplete.
- This isn't offline billing. If the network drops, the app saves the pending intent; it can't promise stock or take payment offline.

One mismatch in the exercise materials: the email says 72 hours, the brief says 48. I asked about it in `STAKEHOLDER_REPLY.md`.

## Tests

`python3 -m unittest discover -v` runs 14 tests covering tax and rounding, offer selection, second-pair discounts, manager authorization, prescription validation, checkout replay, refund/restock behaviour, channel reporting and 429/503 retries. The supplied fixtures also pass `python3 starter-kit/validate_seed.py`.

See [ARCHITECTURE.md](ARCHITECTURE.md) for the diagrams and [STAKEHOLDER_REPLY.md](STAKEHOLDER_REPLY.md) for the Part A response.
