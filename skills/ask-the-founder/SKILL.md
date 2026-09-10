---
name: ask-the-founder
description: Turn the things the books cannot settle into a short, specific list of questions for the owner, and record their answers with a date and a source. Use whenever a categorization, an entry or a treatment is blocked on something only the owner knows.
---

# Ask the founder

The scarcest resource in a catch-up is the owner's attention. Everything here is
about spending it well.

A list of thirty questions gets zero answers. Five specific ones get five
answers, and usually a sixth thing you did not know to ask.

## Five questions that are not in this round

The bank's own balance, the For Review count, the feed state, the reconciled
date and the bank rules come off a QuickBooks screen rather than out of memory,
so they have their own short round with the screen named against each one. See
`read-the-screen`. Asking them here mixes a lookup in with questions that need
the owner to remember something, and the lookup is the half that gets skipped.

## The other list of questions

What a return needs is a separate list, raised out of the chart rather than out
of a stuck transaction, and `ready-to-file` builds it. Those questions live in
the same store and come out in the same rounds, so `books.py questions` batches
both together and `books.py answer` records both the same way. The difference is
where they come from: this skill asks what a categorization is blocked on, and
that one asks what the firm filing the return will otherwise chase in April.

## Hard gates

- **Never ask what the books already answer.** Mine the history first. Asking
  where a vendor goes when the last forty transactions all went to the same place
  wastes the one thing you are short of and makes the tool look like it is not
  paying attention.
- **One question per paragraph, each ending in a question mark.** An implied
  question gets an implied answer.
- **Never ask for a decision you should be making.** "Should this be an expense
  or an asset?" is usually your job. "Was this trip for the Berlin office or
  personal?" is theirs. The test: does answering it require knowing something
  that happened in the world, or knowing accounting?
- **Record every answer with a date and a source.** An undated answer is
  indistinguishable from a guess in a month.
- **Ten at a time, maximum**, ordered by what unblocks the most work.
- **Never answer one on their behalf, and never leave one blank.** Where they
  cannot answer, record that they could not and why:
  `books.py answer Q03 --cannot "the invoice is with our former bookkeeper"`.
  A stated unknown can be chased. A blank reads downstream as a no.

## Run it

```
python3 bin/books.py questions --profile profiles/mine.local.json --round 1
```

Writes `questions/round-1.md`, drawn from what the profile could not settle and
what the categorizer parked. Each question carries the vendor, the amount, the
date and, where it helps, what you will do with each possible answer, which
often lets them answer in one word.

Record answers as they come:

```
python3 bin/books.py answer Q03 "Berlin office, business trip" --on 2026-09-12
```

Round two does not open until round one is answered. Two open rounds means
neither gets finished.

## What a good question looks like

Not: *"Can you clarify some uncategorized transactions?"*

But: *"There's a 4,200 payment to Fabrikam Freight on 14 March that doesn't match
anything else that year. Was that a one-off shipment, or the start of something
recurring?"*

The second one can be answered from memory in ten seconds, standing up, on a
phone. That is the bar.

## Questions that come up in almost every catch-up

Worth asking early, because each one unblocks a category of work: who was keeping
the books and when did they stop; which of these accounts are still open; are
there accounts not in QuickBooks at all; which card is the real one where two
look alike; are any of these payments personal; did anything unusual happen this
year, such as a raise, a loan, a sale, or a new entity.

## What good looks like

Every question specific enough to answer from memory. Every answer stored with
its date and who gave it. Nothing asked twice. And the count going down each
round, which is the only real measure that this is converging.
