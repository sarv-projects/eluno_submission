# Stakeholder reply

**To:** Rohan, Meena, Dr. Iyer, Vikram, Farhan
**Cc:** Product-Tech, Retail, Optometry, Finance, Ops

Thanks for the thread. For this exercise, I’d build the billing flow around Shopify, which stays the record for customers, stock, orders and payments. The till handles prescription checks, offers, GST and Eluno invoice numbers. Where a policy is unclear, I’ll ask rather than make one up.

## Questions for v1

**Vikram:** What is the exact invoice format and when does the financial year roll over? If a number is reserved but payment fails, do we keep it as cancelled or reuse it? Should day-end count a refund on the day it happened or on the original sale date? These answers affect invoice numbering and Finance’s reconciliation.

**Dr. Iyer:** One external prescription in the seed has an 87° axis, outside the 5° grid. Should the till block it for confirmation, or may a named optometrist approve the source value as written? Rounding changes the prescription, so I would not do that silently. I’d also leave remakes out until you and Farhan agree who approves them and who pays the lab cost.

**Meena and Vikram:** Meena wants stackable offers and a manager discount without an approval chain. Vikram requires a reason and approver identity for every manual discount. The offer data also marks FESTIVE10 and PGX_CAMPAIGN15 as non-stackable. Which rule should the till follow when offers conflict? For this build, I follow the data: permitted offers compete by customer saving, and a manager-requested override is applied on its own with its reason and approver recorded. Please confirm that this matches how you want the button to work.

**Rohan:** Does the 10-second target end when the terminal is started or only after Shopify confirms the order? What should staff do if Wi-Fi drops after payment? I would stop and reconcile an uncertain payment rather than promise offline billing or risk charging twice.

**Meena:** For a family bill, which customer owns the Shopify order, and how should the other customers be linked to their prescriptions? Each prescription belongs to a person; I would not attach it to someone else’s line.

The email says 72 hours, but the attached brief says 48. Please confirm the deadline.

## What I’d build and leave out

For the exercise I built customer lookup, prescription and lens checks, offers, per-line GST, terminal payment, the Shopify order with an Eluno invoice number, full refunds with frame-only restocking, and a Shopify-based day-end report. A terminal app is enough here; the brief does not score a screen.

I left family billing, remakes, partial refunds and cash-drawer sessions out. Each needs policy the mock does not provide: who owns a family order, how remake costs are approved, how partial credit notes work, and how the till float is counted and reconciled. I also left out the 10 a.m. email until the day-end numbers are trusted. The queue beverage question and Indiranagar camera issue belong to other tools, not this billing change.

## Risks and what a real launch needs

Payment happens before the Shopify order is created. The app saves an intent and a stable key before external writes, retries known-safe failures, and stops for reconciliation if the result is unclear. The supplied terminal mock says a successful charge is idempotent by `order_ref`, but repeating one actually creates another charge. I would confirm the real terminal’s idempotency and status lookup before launch.

For a real rollout I’d need Shopify credentials and a reliable way to find an order by our key after a timeout; the terminal’s charge/refund and status-lookup contract; and authentication for the store tool and staff. Finance needs to approve invoice rollover, voids and refund reporting. Optometry needs to confirm the off-grid prescription rule, Retail needs to approve offer precedence, and Ops needs to provide the OMS/lab handoff. Seven stores also need a shared invoice counter, backups, monitoring and a clear way to resolve stuck checkouts.

— Sarvesh
