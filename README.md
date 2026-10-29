# Eluno POS

Till app for a single store, built against the mocks in `starter-kit/`. Shopify stays the system of record: customers, stock, orders, payments and refunds live there. This app adds what Shopify doesn't do for Eluno: prescription checks, offers, GST, invoice numbers and a day-end report.

## What the brief asked for

Three parts: the stakeholder reply, the app, and a short Loom. Part way through, the brief changed to require a working screen, so the whole Must flow runs from a browser page. The same logic is also available from the command line.

## Must, and where it lives

| Requirement | Where |
|---|---|
| Customer by phone, latest valid prescription | Customer card |
| Frame and lens checked against the prescription | Cart card, blocked with a reason staff can act on |
| Offers by date, condition, stackability | Quote card, `pos/offers.py` |
| Inclusive GST per line by HSN | Quote card, `pos/money.py` |
| Terminal charge, then Shopify order with Eluno invoice number | Checkout card |
| Full refund, tax reversed, frame restocked, lens not | Refund card |
| Day-end report read from Shopify | Day end card |

All seven work end to end against the mock.

## Should and Stretch

I picked the channel item from Should. Every order records walk-in, WhatsApp booked or online pickup, and the report splits by channel. I left the rest out:

- cash drawer sessions and variance
- partial refunds
- a printable invoice
- a separate audit log. Manager overrides do write the approver and reason onto the order, but that is not a full audit trail.

None of the Stretch items are done.

## Run the app

One command, then open the page:

```bash
python3 server.py
```

Open http://127.0.0.1:8000. The same process serves the page, the JSON API and the mock, so nothing else needs starting. Change the port or database with:

```bash
PORT=8123 DB=/tmp/eluno.sqlite3 python3 server.py
```

Tests:

```bash
python3 -m unittest discover -v
```

The tests start their own mock on a free port.

## Run the CLI

The same flows without a browser. Start the mock first:

```bash
python3 starter-kit/mock-apis/mock_server.py --port 9000
```

Then in another terminal:

```bash
python3 -m pos.cli --base-url http://127.0.0.1:9000 quote \
  --phone +919000000005 --store HSR --staff S105 \
  --pair 91000003:91000083 --date 2026-10-29
```

`checkout` charges the terminal and creates the Shopify order:

```bash
python3 -m pos.cli --base-url http://127.0.0.1:9000 checkout \
  --phone +919000000005 --store HSR --staff S105 \
  --pair 91000003:91000083 --channel walk_in --payment upi \
  --date 2026-10-29 --checkout-key HSR-DEMO-001
```

Refund by the invoice number checkout returned, then run the report:

```bash
python3 -m pos.cli --base-url http://127.0.0.1:9000 refund \
  --invoice ELU/HSR/FY26/00003 --store HSR \
  --reason 'Customer changed mind' --reason-code CHANGED_MIND \
  --refund-key HSR-DEMO-REFUND-001

python3 -m pos.cli --base-url http://127.0.0.1:9000 report \
  --store HSR --date 2026-10-29
```

A few things worth knowing:

- `--pair` is `FRAME_VARIANT:LENS_VARIANT[:RX_ID]`. Repeat it for a multi-pair bill.
- The HSR seed already has two invoices, so a fresh mock starts at `00003`. Use the number checkout prints.
- The same checkout key with the same cart resumes an interrupted bill. A new key means a new bill.
- A manager discount needs `--manager-discount-percent`, `--reason-code` and `--approver-id`. The rule is non-stackable, so it replaces the automatic offers.

## Hosting

The page and the mock run in one process, so one free web service is enough. Nothing to install: `requirements.txt` is intentionally empty because the app is standard library only.

On Render, either:

- **Blueprint:** New > Blueprint, pick this repo. `render.yaml` sets the start command and health check.
- **Manual:** New > Web Service, pick this repo, runtime Python, build command `pip install -r requirements.txt`, start command `python3 -u server.py`. Render sets `PORT` itself, so don't hardcode it.

Two things to expect on the free tier:

- It sleeps after a while idle, so the first hit takes 30-60 seconds to wake. Open the URL once before recording anything.
- The disk is temporary. A restart resets the mock and the SQLite file together, so invoice numbers go back to `00003` and the day-end report starts empty. That is fine for a demo, but don't be surprised by it.

`Procfile` is there for Railway or Heroku style hosts; Render uses the start command above.

Live demo: _add the deployed URL here before submitting._

## Decisions and limits

Money stays in paise inside the app and becomes decimal strings only at the Shopify boundary. Offers follow the rules in `offers.json`: stackable offers can combine, non-stackable ones compete alone, and the combination with the biggest saving wins. A manager discount that was asked for has to be applied, and because its rule is non-stackable it replaces the automatic offers.

Checkout saves an intent and a key before calling anything. If a payment or order write times out and the app can't tell whether it landed, it stops for reconciliation instead of retrying with a new key.

Limits worth knowing:

- The mock's docs say a terminal charge is idempotent by `order_ref`, but repeating a successful charge creates another one. Terminal refunds have no status lookup at all.
- Shopify order create isn't idempotent, and the mock returns at most the 250 oldest orders. The app searches for its checkout key, but a real integration needs pagination plus a lookup by key or a webhook.
- Invoice numbers live in one local SQLite file. That is fine for one process, not for seven stores or several tills. Finance still needs to confirm the fiscal-year rollover and void rules.
- The report counts sales on the sale date and refunds on the refund date. Finance should confirm that. The 250-order cap can also leave a report short.
- This is not offline billing. A dropped network leaves a pending intent; it does not let the till take payment offline.
- The mock has no real staff login, so a manager approval is recorded, not authenticated.

## Where AI tools helped and where they misled me

**Where I used them.** Building the browser UI was the main one: the HTML, the CSS, and the JavaScript that calls the API. I also used AI for first drafts of some tests and for tightening wording in this README and the stakeholder reply.

**What stayed with me.** The design and the coding. I decided the screen flow, which steps a cashier works through and in what order, what the page shows and what it hides behind a hint. I wrote the domain logic that matters: paise-only money, the offer precedence rules, the invoice and credit-note counters, and the checkout/refund recovery states. Where AI produced code I read it, ran it, and changed it, and where it produced prose I cut most of it.

**Where it got things wrong.** All of these were found by running the thing, not by reading what the AI said about it:

- It told me the UI was done. It had never been opened in a browser. The first version had leftover shell lines pasted at the end of `app.js`, which is a syntax error, so none of the JavaScript ran. I found it by loading the page and watching the console.
- It wrote README text saying the quote showed both the automatic and the manager-discount totals. The app never did that.
- The cart sent variant ids to the inventory endpoint, which expects inventory item ids. Stock showed blank every time, and it never noticed because it only tested the API, not the page.
- It listed the HQ lab as a billable store, and defaulted the till to the wrong branch instead of HSR.
- The quote said "manager discount must be requested within the configured limit" even when nobody had asked for a manager discount. Wrong message, and confusing on the floor.
- The day-end line read "1 bills".

I treat AI output as a draft to verify, not as evidence that something works. Everything described in this README was run against the mock.

## Tests

14 tests: GST rounding against the reference invoice, offer selection and per-line allocation, second-pair targeting, manager authorisation, expired and off-grid prescriptions, a full checkout with stock decrement, same-key replay, refund restock rules, channel split and 429/503 retries.

`python3 starter-kit/validate_seed.py` also passes on the untouched fixtures.

See [ARCHITECTURE.md](ARCHITECTURE.md) for the diagrams and [STAKEHOLDER_REPLY.md](STAKEHOLDER_REPLY.md) for the reply.
