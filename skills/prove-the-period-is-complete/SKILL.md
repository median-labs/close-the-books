---
name: prove-the-period-is-complete
description: Test whether the bank feed is a complete record of the period, by taking the book balance, adding everything sitting unbooked, and comparing the result to what the bank says it holds. Use before calling a queue finished, and whenever an account's feed may have stopped.
---

# Prove the period is complete

Everything downstream of a For Review queue assumes the queue is the period.
Work every item and the account is done. Nothing states that assumption, so
nothing checks it, and on the file this was built from it was false on the main
operating account: the feed carried nothing for eight months of the year being
filed.

That account's books said (41,876.20). The bank said 37.14. Its unbooked items
netted (52,309.55), which moves the books further from the bank rather than
toward it. Roughly 79,000 of real activity was in neither place. The queue could
have been worked to zero and the return would still have been wrong, with
nothing anywhere saying so.

## Hard gates

- **The queue cannot be called finished while a completeness finding is open.**
  `attest --unbooked 0` refuses. Finishing the queue and the period being
  complete are different things, and the difference is invisible without this.
- **Get the bank's own balance from the bank.** QuickBooks knows only what it
  was told, so its figure cannot test its own completeness. A statement closing
  balance works; a number read off the bank's site works; a QuickBooks screen
  does not.
- **Never plug a difference.** Report it as a number and find it. A difference
  closes on a document that explains it, and the document gets named.
- **State every account, including the ones that tie.** A report listing only
  the problems hides every account nobody checked.

## Run it

```
python3 bin/books.py intake --account 101000 --bank-balance 37.14 --as-of 2025-12-31
python3 bin/books.py completeness
```

For each declared account it prints the book balance, the bank balance, the net
of everything unbooked, the two added together, and the difference. Where
working the queue would move an account away from its bank balance, it says so:
that is the signal that transactions exist in neither the books nor the queue.

## The walk, in the order it is computed

```
book balance
  plus the net of everything sitting unbooked
  gives the balance the books would reach
  against what the bank says it holds
  so the gap goes from X to Y
  leaving Z that is in neither place
```

A gap that shrinks means the queue is plausibly the rest of the story. A gap
that grows means it is not, and Z is how much is missing.

## Closing a finding

Two honest ways, and no third.

**Fill the hole.** The months a dead feed missed get uploaded from statements:

```
python3 bin/books.py fill-gaps --account 101000
```

Then re-run `completeness`. The finding closes when the arithmetic closes.

**Name the document that explains it.** Where a difference is real and
understood, record it with the evidence:

```
python3 bin/books.py completeness --settle 101000 \
    --evidence "Northgate statement 2025-12, page 2, the wire in on the 14th"
```

The evidence is required, and it carries the date and who gave it into the
handoff package. A difference settled without a document is a plug, and a plug
destroys the only signal there was.

## What to tell the owner

The three numbers and the sentence they make. "Your books say one thing, your
bank says another, and the unbooked items move the two further apart rather than
closer. About 79,000 of real activity is in neither. Working the queue will not
find it; the statements will."

## What good looks like

Every declared account with a bank balance recorded and its difference stated,
including the zeros. Every open finding either filled from statements or settled
against a named document. `attest --unbooked 0` accepted, which means the queue
being empty now means the period is complete.
