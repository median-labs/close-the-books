---
name: reconcile
description: Compare every balance-carrying account's book balance to its statement balance, month by month, stating the difference for every account including the zeros. Use after the backlog is categorized and the adjusting entries are in, before declaring a period closed.
---

# Reconcile

The check that makes the books an assertion about the world rather than an
internally consistent story. A trial balance that foots proves the bookkeeping is
arithmetically sound. Reconciliation proves it matches the bank.

## Hard gates

- **State the difference for every account, including the zeros.** A report
  listing only the problems hides every account nobody looked at. The zeros are
  the evidence.
- **Where there is no statement, say "cannot check" and why.** Never 0.00. An
  unchecked account and a checked account that agrees are not the same thing and
  must never render the same.
- **Never plug.** If an account is out by 41.18, the report says 41.18. An entry
  written to make that disappear is the single clearest sign of books that
  cannot be trusted.
- **A reconciliation you cannot reproduce is not one.** Every difference names
  the statement it was measured against.
- **Record the last month each account reconciled clean.** It is what tells the
  queue apart from the feed offering back a month that already closed. Without
  it, a re-download reads as work to do and booking it double-counts a period
  somebody signed off. See `scope-the-year`.

## Run it

```
python3 bin/books.py reconcile --profile profiles/mine.local.json --from 2025-01-01 --to 2025-12-31
```

One row per account per month: book balance, statement balance, difference,
and the statement it used. Two summary lists come out of it, and they are
different problems:

- **Unreconciled**: a real difference. Something is missing, duplicated or
  mis-dated. Work these.
- **Unchecked**: no statement for that account-month. Get the statement, or
  record why it does not exist.

## With no statements at all

This runs with an empty `statements/`, and that is worth doing on day one. Every
cell reads "cannot check", which is not a failure of the tool: it prints every
account's book balance on its own side and names every account-month somebody
has to go and get, then prints the statement request at the end. Do not send an
owner away to collect documents before showing them this.

Balances read as a magnitude and a side, `3,118,447.25 Cr`, the way the trial
balance prints them, and never as a negative number. The difference column keeps
its sign, because a difference is not a balance.

**Under the table is the wrong-side list.** Any account holding a balance on the
side opposite its normal one, what it usually means, and the document that
settles it. That list frequently contains the most important thing in the
engagement, and it is a different question from whether an account reconciles.

## Working a difference

Look in this order, because this is roughly the frequency order:

1. **A timing difference at the period edge.** A deposit in transit or an
   uncleared payment. Real, and it resolves next month.
2. **Something in the feed still sitting in For Review.** Not yet booked.
3. **A duplicate.** The same transaction entered by hand and also accepted from
   the feed. Very common in a catch-up.
4. **A transfer booked as income or expense**, so it hit the P&L instead of
   moving between two accounts.
5. **A wrong sign or a transposition.** A difference divisible by 9 is the
   classic signature of two transposed digits.
6. **A mis-dated transaction**, often a mistyped year, which moves it out of the
   period entirely.

## Then reconcile inside QuickBooks too

This skill produces the evidence. QuickBooks' own reconcile screen produces the
record: the state the accountant, the lender or the buyer will look for. Once an
account and month agree here, reconcile it there, and the account is done.

## What good looks like

Twelve months by however many accounts, every cell filled, every difference
either 0.00 or explained by name. Coverage stated as `n of N`.
