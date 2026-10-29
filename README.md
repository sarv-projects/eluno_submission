# Eluno POS

Till app for a single store, built against the mocks in `starter-kit/`. Shopify stays the system of record: customers, stock, orders, payments and refunds live there. This app is the bit in front of it that knows about prescriptions, offers, GST, Eluno invoice numbers and the day-end report.

Python 3.8+, standard library only, no build step.

Live demo: https://eluno-submission.onrender.com/ — free tier, so the first hit after idle takes up to a minute.

## What the brief asked for

Three parts: the stakeholder reply, the app, and a short Loom. Part way through, the brief changed to require a working screen, so the whole Must flow runs from a browser page. The same logic is still available from the command line.

## Must, and where it lives

| Requirement | Where |
|---|---|
| Customer by phone, latest valid prescription | Step 2, `pos/service.py` |
| Frame and lens checked against the prescription | Step 3, blocked with a reason staff can act on |
| Offers by date, condition, stackability | Step 4, `pos/offers.py` |
| Inclusive GST per line by HSN | Step 4, `pos/money.py` |
| Terminal charge, then Shopify order with Eluno invoice number | Step 5 |
| Full refund, tax reversed, frame restocked, lens not | Step 6 |
| Day-end report read from Shopify | Step 7 |

All seven work end to end against the mock.

## What the screen does

The page is seven numbered steps, in the order a cashier works:

1. **Store and cashier** — store list from Shopify locations, cashier list from the store tool. Only real stores can bill; the HQ lab isn't offered.
2. **Customer** — lookup by phone, then the Shopify customer and their prescriptions. The latest unexpired one is picked automatically; you can force a specific `rx_id` if needed.
3. **Cart** — pick a frame and a lens, add a pair, repeat for a two-pair bill. The stock line is the real inventory level for that frame at that store, and it warns if the bill needs more than is on hand.
4. **Quote** — shows prices, charges nothing. Shows per-line price, discount, taxable value, CGST and SGST, then the offers that applied and the ones that didn't with the reason for each. Manager override lives here as a separate block.
5. **Checkout** — charges the terminal, creates the Shopify order, returns the Eluno invoice number and the Shopify order reference.
6. **Refund** — full refund by invoice. The invoice number fills in from checkout.
7. **Day end** — bills, gross, discounts, net, tax, refunds, net after refunds, split by payment method, and a channel table.

A few behaviours that matter when you use it:

- **The cart survives a page reload** (kept in `sessionStorage`). Before this it was in memory only, and any refresh silently emptied it.
- **A completed checkout clears the cart.** Otherwise the same pair could be billed twice with a fresh key.
- **Buttons disable and say what they're doing** ("Charging…", "Refunding…"), so a slow terminal doesn't invite a second click.
- **If the server is asleep, the first load retries** and says "Waking the server up…" instead of failing dead.

## Should and Stretch

I picked the channel item from Should. Every order records walk-in, WhatsApp booked or online pickup, and the report splits by channel. I left the rest out:

- cash drawer sessions and variance
- partial refunds
- a printable invoice
- a separate audit log. Manager overrides do write the approver and reason onto the order, but that is not a full audit trail.

None of the Stretch items are done.

## Run it

### The web app

```bash
python3 server.py
```

Open http://127.0.0.1:8000. That one process serves the page, the JSON API and the mock, so nothing else needs starting. To change the port or database:

```bash
PORT=8123 DB=/tmp/eluno.sqlite3 python3 server.py
```

### Tests

```bash
python3 -m unittest discover -v          # all 14
python3 -m unittest tests.test_integration -v   # just the end-to-end ones
```

The integration tests start their own mock on a free port.

### The CLI

Same flows, no browser. Start the mock first:

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

Things worth knowing:

- `--pair` is `FRAME_VARIANT:LENS_VARIANT[:RX_ID]`. Repeat it for a multi-pair bill.
- The HSR seed already has two invoices, so a fresh mock starts at `00003`. Use the number checkout prints.
- The same checkout key with the same cart resumes an interrupted bill. A new key means a new bill.
- A manager discount needs `--manager-discount-percent`, `--reason-code` and `--approver-id`. The rule is non-stackable, so it replaces the automatic offers.

### One-command demo

```bash
./run_demo.sh
```

Starts the mock on port 18787, bills, refunds, prints the day-end report, then stops the mock. `PORT` and `DB` can be overridden.

## Repo layout

```
server.py            page + JSON API + mock in one process
web/                 the till screen (plain HTML, CSS, JS, no build)
pos/                 domain code
  money.py           paise maths, inclusive GST
  offers.py          offer rules, stacking, allocation
  adapters.py        all HTTP; the only place Shopify shapes appear
  storage.py         SQLite: invoice counters and checkout/refund state
  service.py         quote, checkout, refund, day end
  cli.py             command line entry point
tests/               14 tests, including a full flow against the mock
starter-kit/         the supplied mock and seed data (unchanged)
```

### API the page uses

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/locations` | stores and HQ from Shopify |
| GET | `/api/staff?store=` | cashier list from the store tool |
| GET | `/api/customers?phone=` | Shopify customer lookup |
| GET | `/api/prescriptions?phone=` | prescriptions from the store tool |
| GET | `/api/products?product_type=Frame\|Lens` | catalog |
| GET | `/api/inventory?item_ids=&location_id=` | stock for the cart |
| GET | `/api/report?store=&date=` | day-end |
| POST | `/api/quote` | price a cart, no side effects |
| POST | `/api/checkout` | charge, then create the order |
| POST | `/api/refund` | full refund by invoice |
| GET | `/api/health` | used by the host health check |

The mock's own routes (`/admin/api/...`, `/smt/...`, `/terminal/...`) are served by the same process.

## Hosting

The page and the mock run in one process, so one free web service is enough. Nothing to install: `requirements.txt` is intentionally empty because the app is standard library only.

On Render, either:

- **Blueprint:** New > Blueprint, pick this repo. `render.yaml` sets the start command and health check.
- **Manual:** New > Web Service, pick this repo, runtime Python, build command `pip install -r requirements.txt`, start command `python3 -u server.py`. Render sets `PORT` itself, so don't hardcode it.

Two things to expect on the free tier:

- It sleeps when idle, so the first hit takes 30-60 seconds to wake. Open the URL once before showing it to anyone.
- The disk is temporary. A restart resets the mock and the SQLite file together, so invoice numbers go back to `00003` and the report starts empty. That is fine for a demo, but don't be surprised by it.

`Procfile` is there for Railway or Heroku style hosts; Render uses the start command above.

## Decisions and limits

Money stays in paise inside the app and becomes decimal strings only at the Shopify boundary. Offers follow the rules in `offers.json`: stackable offers can combine, non-stackable ones compete alone, and the combination with the biggest saving wins. A manager discount that was asked for has to be applied, and because its rule is non-stackable it replaces the automatic offers.

Checkout saves an intent and a key before calling anything. If a payment or order write times out and the app can't tell whether it landed, it stops for reconciliation instead of retrying with a new key. On the screen, that's why the cart clears after a successful bill and why nothing auto-retries a charge.

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
- The cart lived only in the page, so a refresh emptied it silently, and a completed bill left the pair sitting there ready to be charged again. Both were design mistakes, not typos, and neither showed up until I used the screen like a cashier would.

I treat AI output as a draft to verify, not as evidence that something works. Everything described in this README was run against the mock.

## Tests

14 tests:

- GST rounding against the supplied reference invoice, and half-up money conversion.
- Offer selection: best permitted stackable combination, per-line allocation, second-pair targeting, manager override authorisation and its audit fields.
- Prescriptions: expired, off-grid axis, sphere/cylinder outside the lens, missing ADD.
- A full mock checkout with stock decrement, same-key replay, refund with frame restock and lens no-restock, channel split in day-end.
- 429 retry with `Retry-After`, and terminal 503 retry on the same reference.

`python3 starter-kit/validate_seed.py` also passes on the untouched fixtures.

See [ARCHITECTURE.md](ARCHITECTURE.md) for the diagrams and [STAKEHOLDER_REPLY.md](STAKEHOLDER_REPLY.md) for the Part A reply.
