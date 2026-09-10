---
name: book-a-safe
description: Work out where a convertible instrument such as a SAFE or a convertible note belongs on the balance sheet, and draft the reclassification entry if it is in the wrong place. Use when an instrument is sitting in equity, when a raise has not been booked, or before a return, an audit or a diligence process.
---

# Book a SAFE

A convertible instrument put in the wrong place moves every ratio on the balance
sheet. It is also one of the most common things to find in a startup's books,
because the money arrives, someone has to code it, and equity is where it looks
like it belongs.

## Hard gates

- **Check the reclassification is not landing in a period that already closed.**
  An instrument moved across a reconciled month or a filed year changes a period
  somebody signed off, and the effective date decides which. See
  `scope-the-year` for the dates each account is reconciled through.
- **Get the instrument before deciding anything.** The document governs. Not the
  founder's summary of it, not the wire memo, not what the last company did. Ask
  for the executed copy, and if it is not available, stop and say that this
  cannot be settled without it.
- **This is a classification question with real judgement in it.** Present what
  the instrument says and what each treatment rests on. The company's accountant
  or auditor decides. Do not assert a conclusion as if it were arithmetic.
- **Tax treatment is a separate question from book treatment**, decided by
  whoever signs the return. Never conflate the two.
- **Never restate a filed period without saying so.** If the instrument was on a
  balance sheet that has already gone to a lender, an investor or a tax
  authority, moving it is a change to something someone has relied on. Date the
  entry deliberately and flag it.

## What decides it

Read the instrument for these, and record what you find with the clause you
found it in:

- **Is there any obligation to repay cash?** A right to a cash payment on a
  change of control or a dissolution points away from permanent equity.
- **What happens on dissolution**, and where the holder sits relative to common.
  On most standard forms the holder is ahead of common shareholders.
- **Is the conversion at a fixed number of shares or a variable one?** A variable
  count settling a fixed value behaves differently from a fixed count.
- **Is it a standard published form, unmodified?** A modified side letter is
  where the surprises live.
- **Any discount, cap, most-favoured-nation clause, or pro-rata right.**

Then the treatment follows from what the document actually says, and the choice
gets written down with its reasoning, because the next person will ask.

## If it needs moving

```
python3 bin/books.py reclass --profile profiles/mine.local.json \
    --from 340000 --to 240000 --reason "convertible instruments, per executed agreements"
```

Which drafts the entry into the worksheet. Note that **QuickBooks Online in the
US cannot import journal entries**, so this one gets typed: it is a single entry,
so that is a minute of work.

Two practical points:

- **Date it deliberately.** Dating it at the start of the current year leaves an
  already-filed prior year untouched. Dating it in the prior year restates that
  year. Both are legitimate; the choice is the owner's and it should be recorded.
- **Financing costs follow the instrument.** If issuance costs were booked
  alongside it, they move with it, or you have split one transaction across two
  places on the balance sheet.

## Record the decision

Write the treatment, the clause it rests on, who decided it and when, into the
profile's `decisions_made`. The next person to open these books, including the
owner's accountant, should not have to reconstruct the reasoning from the entry.

## The preparer needs the instrument too

`ready-to-file` raises a convertible instrument sitting in equity as something
the firm filing the return has to be given: the signed instrument, the schedule
of who put in how much and when, any side letter, and whether any of them
converted or was repaid during the year. Recording the document there with
`books.py provide` carries it into the package with its checksum, so the reading
this skill does is checkable by whoever signs the return.

## What this does not do

It does not tell you the tax treatment, it does not value anything, and it does
not decide whether an instrument has converted. Those are questions for the
people who sign things.
