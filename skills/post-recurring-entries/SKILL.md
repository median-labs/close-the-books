---
name: post-recurring-entries
description: Draft the repeating adjusting entries a year of catch-up needs (payroll, prepaid amortization, intangible amortization, month-end payment processor revenue) as a worksheet ready to enter, with each entry citing what it rests on. Use after the transaction backlog is categorized, before reconciling.
---

# Post recurring entries

Categorizing the bank feed gets the cash right. It does not get the books right.
The entries that make a set of accrual books mean something are the ones with no
bank transaction behind them: payroll split into its parts, prepaid contracts
amortized over their term, an intangible written down monthly, and the month-end
entry that turns a payment processor's gross deposits into revenue and fees.

These are also the entries most likely to have stopped when a bookkeeper left,
because nothing prompts them.

## Read this before you start: you will be typing these

**QuickBooks Online in the United States cannot import journal entries.** Not on
any plan. The Canada and UK versions can, and a third-party importer app can, but
in the US the choices are typing them or buying an app.

So this skill's main output is a **worksheet built for typing**: entries in the
order the Journal Entry screen asks for them, a running total per entry so a
transposition is caught before saving, and a tick column so nobody loses their
place halfway through.

**Use QuickBooks' recurring templates.** Twelve monthly amortization entries are
one entry plus a recurring template, not twelve pieces of typing. The worksheet
groups repeating entries and flags where this works. It is the single biggest
time saver here and most owners do not know the feature exists.

## Hard gates

- **Nothing drafts while rules are still auto-posting into the year being
  filed.** `entries` refuses. An adjusting entry computed against a file that is
  still changing is computed against a number that has moved by the time it is
  typed. See `stop-the-automation`.
- **Every entry carries a basis**, naming the document, schedule or answer it
  rests on. An entry nobody can explain is an entry nobody can defend, and the
  library refuses to write one without it.
- **Never invent a payroll number.** These come from the payroll provider's own
  reports. If they are not to hand, ask for them; do not derive gross from net.
- **Clearing accounts must return to zero.** If the month-end processor entry
  leaves a residual, that residual is a finding, not a rounding.
- **Withholdings belong in liability accounts**, not netted inside a benefits
  expense. Netting them is a common defect and it hides a real obligation.

## Run it

```
python3 bin/books.py entries --profile profiles/mine.local.json --through 2025-12-31
```

Produces, per kind:

| Kind | What it needs | What it writes |
|---|---|---|
| Payroll | the provider's period reports: gross, employer taxes, benefits, net | one entry per pay date, withholdings shown separately |
| Prepaid | each contract: vendor, amount, start, term | one entry per month, plus the remaining unamortized balance to tie to the balance sheet |
| Intangibles | asset, monthly amount, accumulated account | one entry per month |
| Processor revenue | the processor's own monthly balance report | gross to revenue, fees, refunds, clearing back to zero |

## Then

Enter them, ticking the worksheet as you go. Where a group is flagged as
recurring, enter the first and use Make recurring for the rest. Then re-run
`check-the-books`: the prepaid balance, the accumulated amortization and the
clearing account are all exit tests, so they will tell you whether the entries
landed as intended.
