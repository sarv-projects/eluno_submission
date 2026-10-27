# Stakeholder reply

**To:** Rohan, Meena, Dr. Iyer, Vikram, Farhan
**Cc:** Product-Tech, Retail, Optometry, Finance, Ops

Thanks everyone, there's enough here to start. The short version of how I'd approach this: Shopify stays the source of truth for customers, stock, orders and money, and the new till sits in front of it and knows the things Shopify doesn't — lenses, prescriptions, offers, invoice numbers. For this exercise I'm building that narrow billing path end to end, and I'm stopping where a policy or an API contract is missing instead of guessing at one.

## Questions, and who can answer them

**Vikram** — invoice series. The location data gives me the prefix (`ELU/HSR/FY26/00001`) but not the rules around it. Does FY26 run April–March and reset on 1 April? When we reserve a number and the payment then fails, it stays in the series as cancelled — does the number ever go back in the pool, or is it burned? And for day-end: does a refund count on the day it happened or against the original bill's date? These three decide what "gapless" actually means, so I'd rather get them from you than assume.

**Dr. Iyer** — one of the external prescriptions in the test data has an axis of 87°, which isn't on the 5° grid. The till currently blocks it and asks for an optometrist to confirm, rather than quietly rounding it to 85° or 90°. Is that what you want? Related: if a source prescription is transcribed from paper and the axis really is 87°, should a named optometrist be able to accept it as written without a new test? On remakes I'm deliberately not building anything — the cost allocation question is yours and Farhan's to settle first.

**Meena** — you asked for stackable offers and a manager discount button; both are in. The open question is precedence: FESTIVE10 and PGX_CAMPAIGN15 are marked non-stackable in the offer data, so when they compete with the stackable ₹500 and second-pair offers, which should win? I've gone with "whichever combination gives the customer the biggest saving, unless a manager explicitly asks for a discount, in which case that one is applied and everything else drops away" — with the manager's name and a reason code written onto the order. If that's not how it should work on the floor, it's a small change now and a painful one later.

**Rohan** — the 10-second target: measured from the customer saying yes, or all the way through to Shopify confirming the order? It changes whether payment and order creation can happen in parallel. Second, when the Wi-Fi drops mid-bill, may staff take payment and hold the bill to send later, or do we stop at payment? I don't want to promise offline billing we can't back with real stock and duplicate-order guarantees.

One admin thing — the email says 72 hours, the attached brief says 48. Could you confirm which it is? It doesn't change what I build, just when I stop.

**Family billing** (Meena): one Shopify order can only belong to one customer, and each lens line carries one prescription that belongs to one person. Before v1 I need to know who owns the bill when three family members are on it — and how the other two customers are linked for the WhatsApp assistant and future recalls. I'd rather defer this than put one person's prescription on another person's line.

## What I'm building and what I'm waiting on

Building now: customer lookup by phone, latest valid prescription, a frame-plus-lens cart that refuses lens choices outside the prescription with a message staff can act on, offers, per-line GST, payment on the terminal, the Shopify order with our invoice number on it, full refunds that restock frames but not lenses, and a day-end report read back out of Shopify. A CLI, since the brief says a screen isn't scored.

Waiting on before I'd call it done: family billing (above), remake flow and cost allocation, partial refunds — Finance needs to say whether a credit note can be partial, the restock rules are Farhan's — and cash-drawer movements, which Vikram should spec in terms of what he needs to reconcile. The 10 a.m. report I've left out; it's a small job once day-end is trusted. The queue-tool beverage question and the Indiranagar camera are real but they're someone else's this week.

## The risky parts, and what I'd need to ship for real

The uncomfortable moment in any till is paying before the order exists. I'm writing a checkout intent to a local database with a stable key before anything is sent, retrying only failures we know are safe, and stopping for reconciliation when we don't know — rather than replaying a charge and hoping. Worth knowing: the mock terminal doesn't actually de-duplicate repeated charges with the same reference, despite what its README says, so that's a contract I'd want confirmed with the vendor before launch.

To ship v1 in real stores I'd need: Shopify API credentials and a way to look up an order by our own key after a timeout (the API we have now only lets me scan); the terminal's idempotency and status-lookup documentation; store-tool authentication; Vikram's invoice/void rules and sign-off on how refunds appear in day-end; Dr. Iyer's answer on off-grid axes; and Farhan's OMS handoff contract. On my side, a single local database is fine for this exercise but not for seven stores — real rollout needs a shared invoice allocator, staff logins, monitoring, and someone paged when a bill is stuck.

— Sarvesh

