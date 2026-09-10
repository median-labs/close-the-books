---
name: untangle-duplicate-accounts
description: Handle one real bank or card account that appears twice in the chart after a feed was re-linked, in the order that does not destroy evidence. Use when two accounts have almost the same name, when the reconciled history and the live feed are on different rows, or before anyone disconnects or merges anything.
---

# Untangle duplicate accounts

A bank link breaks and is rebuilt. QuickBooks does not always reattach the feed
to the account already there. It creates a new one, at the top level of the
chart, with no account number, named almost the same. From that moment there are
two rows for one card:

```
222000 Credit Cards:Acme Card    the reconciled history, no feed, no queue
       Acme card                 the live feed, the whole queue, never
                                 reconciled, and an opening-balance plug
```

The plug is the tell. QuickBooks books the balance the bank reported at the link
date and puts the other side in Opening Balance Equity, because there is nowhere
else for it. So the plug and the Opening Balance Equity balance are the same
figure, which is how a check can say these two rows are one account rather than
these two rows have similar names.

## Hard gates

- **Nothing about disconnecting or merging proceeds while either account has
  anything in its For Review queue.** This is the gate that prevents the only
  irreversible loss in this kit, and it cannot be passed with a flag.
- **Disconnecting a feed DELETES every item in that account's Pending and For
  Review tabs.** On an account whose activity was never booked, those items are
  the only record of it in the file. They are not in the general ledger, they
  are not on the other row, and nothing exports them once they are gone.
- **The order is book, dispose, disconnect, merge. No other order is safe.**
  QuickBooks will not merge two accounts while either is connected, and its own
  documented workaround is to disconnect first, which is exactly the step that
  deletes the queue. So the queue has to be empty before that step, not after.
- **The opening-balance plug gets a named disposition, never an archive.** A
  retired account carrying a balance moves that balance somewhere, and unnamed
  is the one place it must not go.

## Run it

```
python3 bin/books.py merge-plan
```

Lists every pair, which row holds the history, which holds the feed, and how
many items sit in each queue.

```
python3 bin/books.py merge-plan --duplicate "Acme card"
```

Refuses while that account's queue holds anything, and says why. Once both
sources read zero it prints the order and the disposition for the plug.

## The order, and what each step is for

1. **Book everything in both queues and accept it in QuickBooks.** Work them
   through `catch-up` like any other backlog. Until the tab reads zero on screen
   and the export folder no longer holds a stale copy, stop here.
2. **Dispose of the opening-balance plug by name.** It is not a real position;
   it is what QuickBooks wrote so the new account would balance on day one.
   `books.py reclass --from 3000 --to 3400 --reason "..."` drafts the entry, and
   the reason is the justification rather than a memo.
3. **Disconnect the feed on the account being retired.** Only now is this safe,
   because the tab it empties is already empty.
4. **Merge it into the account holding the reconciled history.** In QuickBooks:
   the gear, Chart of accounts, edit the account being retired, and give it the
   exact name and detail type of the one being kept.
5. **Tie out again.** A merge moves history, and history that moved is history
   worth proving again. `books.py tieout`.

## Why this matters beyond tidiness

The reconciled history is on the row with no feed, and the queue is on the row
with no history. Ask "has this account ever been reconciled" of the account the
queue is on and the answer is no, so every queue item in a period that WAS
reconciled reads as unbooked work. On the file this was written from that was
197 items, and booking them would have posted a second copy of each into a
closed period.

Balances split across the two rows as well, so neither ties to a statement and
the difference on each looks real.

## What good looks like

One row per real account. The reconciled history intact. The plug disposed of by
name with a reason recorded. Nothing deleted at any point, and a tie-out that
still closes afterwards.
