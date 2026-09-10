---
name: fill-feed-gaps
description: Build QuickBooks Online bank transaction upload files for the months a bank feed missed, from statements, so a dead or reconnected feed does not leave a hole in the books. Use when a feed stopped syncing, when an account was connected late, or when reconnecting a feed only brought back the last 90 days.
---

# Fill feed gaps

A bank feed that stops does not announce itself. The books simply go quiet for
that account, and everything downstream looks fine: the trial balance still
foots, the reports still render, and the missing months are invisible until
someone reconciles.

Reconnecting the feed does not fix the past either. QuickBooks brings back
roughly the last 90 days. Older gaps have to be uploaded from statements.

## Hard gates

- **Tie out the statements first.** Uploading transactions you have not proved
  complete just moves the gap into the books, where it is harder to see.
- **Never upload a month that is already booked.** Check for existing entries in
  the period first; a duplicate upload is much harder to unpick than a gap.
- **The owner does the uploading.** This writes files. Nothing here connects to
  anyone's QuickBooks.
- **A gap you have not measured is a gap you will not fill.** Run
  `prove-the-period-is-complete` first. It takes the book balance, adds
  everything unbooked, and compares the result to what the bank says it holds,
  which is the only test that says whether a feed was a complete record.
- **Fill the months the deadline needs first.** A gap in next year's months is
  real work and it is not this deadline's work. See `scope-the-year`.

## Run it

```
python3 bin/books.py fill-gaps --profile profiles/mine.local.json --account 101000
```

For each gap month it writes `import/bank-<account>-YYYY-MM-batch-NN.csv` in the
three-column format QuickBooks accepts: `Date, Description, Amount`, with amount
signed and money in positive. It splits automatically: QuickBooks takes at most
1,000 rows and 350 KB per upload.

There is also a four-column format, `Date, Description, Credit, Debit`. Prefer
three-column: one signed number cannot be transposed, and a Credit/Debit pair
can.

## Then, in QuickBooks

Banking, then the account, then Link account (or the dropdown), then Upload from
file. Map the columns when asked, then check the count on screen against the
count in the file before accepting. The uploaded rows land in For Review like any
other feed transaction, where they get categorized with everything else.

**Import your bank rules first if you have any**, because rules apply to
transactions as they arrive. They will not reach back and re-categorize anything
already sitting in For Review.

## Order matters when an account is being merged or disconnected

Disconnecting a bank feed **deletes every unreviewed transaction** in that
account's For Review queue, and QuickBooks will not merge two accounts while
either is connected to a feed. So when a duplicate account has to be merged:
book the pending items on both accounts first, then dispose of the
opening-balance plug, then disconnect, then merge. The other order silently
discards the backlog, and on an account whose activity was never booked that
backlog is the only record of it in the file.

`untangle-duplicate-accounts` carries the whole procedure and refuses the wrong
order rather than warning about it.

## What good looks like

Every gap month uploaded, the row count on screen matching the file, and the
account's book balance now moving in step with its statements. Re-run
`tie-out-statements` and `prove-the-period-is-complete` afterwards. The second
one is what closes the finding: the gap between the books and the bank goes to
zero, or the amount still missing is stated as a number.
