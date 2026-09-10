---
name: wind-down
description: Draft the closing entries for a company that is being dissolved (settling intercompany balances, extinguishing convertible instruments, final distributions, closing accounts to zero) but only after the owner has answered the questions those entries rest on. Use when a company is shutting down and its final books and final return have to be right.
---

# Wind down

The least reversible work in this repo, and the part where being fast is worth
the least.

A final set of books is read by people who cannot ask follow-up questions: a tax
authority looking at a final return years later, a shareholder asking what
happened to their money, a director confirming they were released. Every entry
here should still make sense to a stranger with no context.

## Two things to settle before the first entry

**Which year is being filed, and when it is due.** A wind-down usually arrives
with a return on extension and a queue holding two years of transactions. Only
one of those years is this deadline's work. See `scope-the-year`.

**Whether anything is still posting.** A company nobody is running still has its
bank rules running, and a rule that auto-adds keeps putting entries into the
year on the final return with nobody watching. See `stop-the-automation`.

## Hard gate: this skill refuses to run until it is answered

Liquidating entries rest on facts only the owner has. Missing any of them, the
skill stops and names what is missing rather than assuming:

| Answer needed | Why nothing can be drafted without it |
|---|---|
| **Target date** | Decides the final period, which entries fall inside it, and what the final return covers |
| **Plan for each intercompany or related-party balance** | Settled in cash, written off, or distributed. These are three different entries with three different consequences, and the balance is often the largest number on the sheet |
| **Terms of any convertible instruments** | On dissolution these holders usually rank ahead of common. What they are actually paid decides the entry; the carrying amount does not |
| **Cap table** | Decides who receives a final distribution and in what order |
| **Resolution date** | The formal decision to dissolve starts statutory clocks. In the US, Form 966 is due within 30 days of it |

Answer them with `python3 bin/books.py answer wind_down.<key> "<answer>"`.

`ready-to-file` raises these same five under these same ids, so answering one
here unblocks the entries and satisfies the filing requirement in one act. It
raises them when the books say the company stopped, most often payroll ending
part way through a year that runs to December, and the owner confirms it is
being closed. It also asks for the certificate of dissolution or the board
consent, which is the proof of the date all five rest on.

## What it drafts, once it can

```
python3 bin/books.py wind-down --profile profiles/mine.local.json
```

- **Settlement or write-off of related-party balances**, with the treatment named
  and the answer it rests on cited on the entry.
- **Extinguishment of convertible instruments** against what is actually paid,
  not against the carrying amount. A difference between them is real and has to
  land somewhere deliberate.
- **Final distributions**, in the order the instruments and the cap table set.
- **Closing remaining balances to zero**, so the final balance sheet is a balance
  sheet and not a residue.

Where a treatment depends on a document the profile does not hold, it emits a
**question rather than an entry**. That is the intended behaviour, not a gap.

## Around the entries, and not this skill's job

A dissolution is a filing exercise as much as a bookkeeping one. Depending on
where the company is registered and operates, that can include a final return
marked final, a statutory notice of the intent to dissolve, dissolution filings
in the state of incorporation with taxes current, closing out registrations in
other states, final payroll filings, and closing any foreign-subsidiary or
foreign-ownership reporting. **Several of these carry per-form penalties that
dwarf the tax on a company with no profit.**

Get the list from whoever prepares the return, early. That conversation is worth
having before the books are finished, not after, because it changes what the
books need to show.

## What good looks like

Every entry cites the answer that authorizes it. Every balance is either zero or
explained. The open questions that remain are written down where the accountant
and the owner can both see them, rather than living in someone's head.
