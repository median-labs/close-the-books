---
name: scope-the-year
description: Work out which unbooked transactions belong to the tax year being filed and which belong to next year or to a month that already reconciled, so a queue of hundreds becomes the number of items this deadline actually needs. Use at the start of any catch-up with a return due, or whenever somebody quotes a For Review count as the size of the job.
---

# Scope the year

The number on the For Review tab is the queue depth. It is not the work.

On the file this was built from, 863 items were quoted for a month, in status
notes and to the owner, and an estimate of 60 to 88 hours was built on it. The
first honest partition:

| | |
|---|---|
| 863 | items in the queue |
| 354 | dated in the year AFTER the one being filed |
| 509 | dated inside the filing year |
| 197 | of those sit in months reconciled clean on the account whose history governs them |
| 312 | left, and that is the work |

863 and 312 differ by a factor of 2.7, and every number downstream, the hours,
the fee, the date the return can go in, came off the wrong one.

## Hard gates

- **Ask which year is being filed and when it is due, before counting anything.**
  Never read it off an export. The period an export covers is a fact about the
  download, not a decision about what is being filed.
- **Never book an item dated on or before an account's reconciled-through date.**
  A reconciled month has been tied to a statement and closed. The feed offering
  that month back is offering a transaction the books already hold, and booking
  it puts a second copy inside a period somebody signed off.
- **On a duplicated account, read the reconciliation history from the account
  that has it.** The feed and the queue sit on one row and the reconciled
  months on the other, so asking the queue's account whether it was ever
  reconciled gives the answer no, and every already-booked item reads as work.
  `untangle-duplicate-accounts` finds the pair.
- **Set aside is not the same as hidden.** Every deferred row is written out
  with its date, its account, its amount and the reason it waits.

## Run it

```
python3 bin/books.py filing-year 2025 --due 2026-10-15
python3 bin/books.py intake --account 101000 --reconciled-through 2025-06-30
python3 bin/books.py scope
```

`filing-year` records the year and the due date and says how many days are left.
`scope` partitions the queue and writes two files: `reports/scope.json` with the
counts, and `reports/deferred.md` with every row it set aside.

`catchup` then batches only the in-scope rows, and prints the same partition at
the top so nobody has to remember which number they are looking at.

## Where the reconciled-through date comes from

QuickBooks: Transactions, Reconcile, then History by account. It is the last
date each account reconciled clean. Record one per account.

With no date recorded, the already-booked group is empty because the check could
not run, not because nothing is already booked. The output says which, and the
difference matters: an empty group that was measured is a result, an empty group
that was skipped is a hole.

## What to tell the owner

Give them the partition, not the queue depth. The sentence that lands is "863
items on the tab, 312 of them belong to the year you are filing, and 197 of the
rest are the feed offering back transactions your books already hold."

Then say what happens to the other 551: they are written out in
`reports/deferred.md` and they come back when their own deadline does.

## What good looks like

Four counts that add to the queue depth with nothing unexplained. Every account
carrying a reconciled-through date or named as not having one. Batches holding
only in-scope rows. A deferred file a person can open and disagree with.
