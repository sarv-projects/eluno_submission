# Eluno POS exercise

A till CLI that replaces Shopify POS while Shopify stays the system of record. Python 3.8+, standard library only, nothing to install. Talks to the mock's three surfaces (Shopify Admin, store tool, terminal) and keeps money in integer paise the whole way through.

## Running it

Two commands, both from this directory:

```bash
./run_demo.sh                      # starts the mock, bills, refunds, prints day-end, stops the mock
python3 -m unittest discover -v    # tests, starts its own mock
```

`run_demo.sh` uses port 18787 and its own `pos_demo.sqlite3` (throwaway — delete it to reset invoice numbers). Change either with `PORT=19000 DB=/tmp/eluno.sqlite3 ./run_demo.sh`.

### Running the pieces by hand

Start the mock in one terminal:

```bash
python3 starter-kit/mock-apis/mock_server.py --port 9000     # add --no-flake to kill the 429/503 behaviour
```

Then in another terminal:

```bash
# 1. price a cart without charging anything
python3 -m pos.cli --base-url http://127.0.0.1:9000 quote \
  --phone +919000000005 --store HSR --staff S105 \
  --pair 91000003:91000083 --date 2026-10-27

# 2. actually charge and create the Shopify order
python3 -m pos.cli --base-url http://127.0.0.1:9000 checkout \
  --phone +919000000005 --store HSR --staff S105 \
  --pair 91000003:91000083 --channel walk_in --payment upi \
  --date 2026-10-27 --checkout-key HSR-DEMO-001

# 3. full refund by invoice number (frame goes back to stock, lens doesn't)
python3 -m pos.cli --base-url http://127.0.0.1:9000 refund \
  --invoice ELU/HSR/FY26/00003 --store HSR \
  --reason 'Customer changed mind' --reason-code CHANGED_MIND \
  --refund-key HSR-DEMO-REFUND-001

# 4. day-end, read out of Shopify
python3 -m pos.cli --base-url http://127.0.0.1:9000 report \
  --store HSR --date 2026-10-27
```

A few things worth knowing before you try it:

- The HSR seed already has two invoices, so a fresh run starts at `ELU/HSR/FY26/00003`. Take the invoice number from checkout's output rather than guessing.
- `--pair` is `FRAME_VARIANT:LENS_VARIANT[:RX_ID]`, repeatable for a multi-pair bill for one customer. Leave RX off and it picks the customer's newest valid prescription.
- **`--checkout-key` is the safety net.** Reuse the same key and same inputs to resume or reconcile an interrupted bill; ambiguous writes stop for reconciliation instead of replaying. A new key means a new bill. Never mint one just because a charge response timed out. Refund keys work the same way.
- Omit `--date` for today.

### Manager discount

The override button exists, but it's audited: 0–10%, the approver has to be an authorized manager in that same store, and both the reason code and approver ID go onto the Shopify order.

```bash
python3 -m pos.cli ... checkout ... \
  --manager-discount-percent 5 --reason-code WALKOUT --approver-id S108
```

One deliberate choice: because the manager rule is marked non-stackable in `offers.json`, requesting it *replaces* the automatic offers rather than stacking on top. Run `quote` without the manager flag first if you want to compare the automatic discount. If Retail wants the override to stack instead, that needs a policy decision and a change to the offer rules.

## What's built

**Must, end to end:** customer by phone → newest unexpired prescription → frame + lens validated against that prescription (sphere/cylinder range, ADD for progressive/bifocal, 5° axis, actionable failure messages) → offers → per-line GST by HSN → terminal charge → Shopify order with invoice/channel/staff/Rx data → full refund with correct restocking → day-end report computed from Shopify.

**Also did, from the Should list:** the **channel report** — day-end now splits bills, gross, discounts, net sales and refunds by `walk_in` / `whatsapp_booked` / `online_pickup`. Channel was already being written to every order; this just reports it. I picked this one because it's pure reporting over data we already store — no new state, no new policy decisions, low risk of being wrong. The others needed things I'd rather not guess at (see below).

**Decisions that are visible in the code:**

- *Offer precedence:* every offer in a combo must be stackable; non-stackable ones compete alone; best total customer saving wins, ties go to fewer offers then alphabetically. Priority only decides application order, not selection. Every offer that was skipped carries a reason.
- *Manager discount is never silent* and never un-audited.
- *Architecture:* Shopify types stop at `pos/adapters.py`. Domain rules (`money.py`, `offers.py`, prescription checks) take and return plain dicts and paise, so swapping Shopify out wouldn't touch them. Rates and offers are read from JSON at runtime — changing a GST rate or adding an offer needs no code change.
- *Recovery:* intent saved locally before each external call; states are `prepared → charge_pending → charged_order_pending → shopify_create_pending → complete`, with `needs_reconciliation` as the dead-end for anything ambiguous. 429s honour `Retry-After`; the terminal's simulated 503 retries on the same key; a timeout mid-write **stops** rather than replaying.

See [ARCHITECTURE.md](ARCHITECTURE.md) for the diagrams (current state, target state, checkout/refund sequence).

## Limits to keep in mind

- **Mock behaviour differs from its own docs.** Repeating a *successful* terminal charge with the same `order_ref` creates a second charge (the README says it's idempotent; it isn't — only the 503-retry path is). Terminal refunds have no idempotency key or status lookup at all. So an ambiguous terminal outcome is held for reconciliation, never auto-retried. This needs the real vendor's contract.
- **No lookup-by-key endpoint in Shopify.** Order create isn't idempotent; I stamp `checkout_key` onto the order and scan for it before retrying, but the mock's order list caps at 250 oldest-first. Production needs pagination plus a searchable external reference or webhook before ambiguous creates can be safely retried.
- **Invoice numbers are per-store sequential in a single SQLite file.** Right for one process, wrong for seven stores — real rollout needs a shared atomic allocator, and Finance still needs to confirm the FY rollover and void rules. Reserved-but-failed numbers stay in the local record rather than being reused.
- **Not true offline mode.** An outage leaves a durable pending intent; it does not mean we can take money or promise stock while Shopify is down.
- **Day-end is Shopify-sourced, sales on sale date, refunds on refund date.** Confirm that treatment with Finance. Mock list endpoint truncation applies here too.
- **Not built:** UI, family billing, remakes, partial refunds, cash drawer, print/PDF, the 10 a.m. email, offer authoring/AI, prescription AI, anomaly flags, queue/camera fixes, OMS handoff. Reasons for each are in `STAKEHOLDER_REPLY.md`.
- Off-grid external prescriptions are blocked for optometrist confirmation — nothing is ever silently rounded.

## Tests

`python3 -m unittest discover -v` — 14 tests: inclusive-GST rounding against the reference invoice, offer selection and per-line allocation, second-pair targeting, manager authorization (and that a requested override lands with its audit fields), expired/off-grid/out-of-range prescription blocks, full mock checkout with stock decrement, same-key idempotent replay, refund restock rules, channel split in day-end, and 429/503 retry behaviour.

`python3 starter-kit/validate_seed.py` still passes on the untouched fixtures.


