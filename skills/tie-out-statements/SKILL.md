---
name: tie-out-statements
description: Parse bank and credit card statements and prove each month's transactions are complete by tying opening balance plus deposits minus withdrawals to the closing balance. Use before categorizing anything, and whenever a bank feed has gaps or has stopped syncing.
---

# Tie out statements

Before you categorize a transaction you need to know you have all of them. A feed
that quietly stopped, a month that downloaded half, a card whose statement cycle
runs mid-month: each leaves a hole that no amount of careful categorizing fixes.

The check is arithmetic and it is absolute:

```
opening balance + deposits - withdrawals = closing balance
```

If that holds for every month, you have every transaction. If it does not, you
are missing some, and the difference tells you how much.

## Hard gates

- **Never plug a difference.** Report it as a number and find it. An adjustment
  that makes a difference disappear destroys the only signal you had.
- **A month that ties is not a period that is complete.** The chain proves the
  statements you have are internally consistent. It says nothing about a month
  the feed never delivered and no statement was requested for.
  `prove-the-period-is-complete` is the check that catches that, by walking the
  book balance and everything unbooked against what the bank says it holds.
- **The chain identity beats the statement's own summary field.** Printed totals
  are sometimes mangled in the PDF. If the running balance chain ties and the
  printed "total withdrawals" does not parse, trust the chain and say which
  check you skipped.
- **A missing statement is reported, never assumed empty.** No statement for an
  account-month means "cannot check", not zero.

## Run it

```
python3 bin/books.py tieout --statements statements/ --profile profiles/mine.local.json
```

Per account per month it reports opening, deposits, withdrawals, computed
closing, stated closing, and the difference. It also reports **chain breaks**,
where one month's closing does not equal the next month's opening, which is how
a missing statement shows up even when every statement you do have ties.

## Getting the statements

**Ask for them precisely, and ask before you have any.**

```
python3 bin/books.py statements --profile profiles/mine.local.json
```

This needs no statements to run. It works out which accounts and which months
from the books themselves and writes `reports/statement-request.md`, which the
owner can forward to a bookkeeper or work through in a banking portal. The reason
is stated per account because the reasons differ: a feed that stopped in April
2025 needs everything from April 2025, an account whose balance is on the wrong
side needs its statement to settle which way, and an account that already
reconciles cleanly needs nothing. A month already in `statements/` is never asked
for twice.

`tieout` itself still refuses with an empty `statements/`, because a tie-out
compares the books against a statement and there is nothing to compare to. What
it refuses with is the request above, not the news that a folder is empty. Say
that plainly to the owner and give them the list; do not report it as a failure
of the books.

CSV where the bank offers it, PDF otherwise. Ask for every account, including the
ones the owner thinks are dormant: a dormant account with a forgotten annual fee
is exactly the kind of thing that turns up here.

If a feed has been dead for a while, note that QuickBooks downloads only about
the last 90 days when a feed is reconnected. Everything older has to come from
statements, which is what `fill-feed-gaps` does with the output of this skill.

## Two traps worth knowing

**Card statements do not tie the same way as bank statements.** A card's printed
"spending" figure is usually net of refunds and excludes the payment that clears
the balance, so it will not equal your withdrawals total. Tie a card on its own
balance chain: previous balance plus charges minus payments and credits equals
new balance.

**Some PDFs extract badly.** Text extraction can return scrambled fragments or
nothing at all for a statement that looks perfect on screen. The parser tries
more than one extraction mode and picks whichever one ties. If none tie, say so
and ask for a CSV rather than working from a bad parse.

## What good looks like

Every account-month either ties, or has a stated difference and a named reason.
No chain breaks, or each one explained by a statement you know is missing. Then,
and only then, categorizing is worth starting.
