---
name: read-the-screen
description: Collect the five facts about a QuickBooks file that no export carries, by telling the owner exactly which screen to open and what to read off it, then recording each answer with the date it was given. Use before any catch-up, and whenever a command says it is missing something only the screen knows.
---

# Read the screen

Exports do not carry the bank's own balance, the For Review count, the bank
rules, whether a feed is connected, or who authored an entry. Four of those five
decide what a catch-up consists of, so a run that skips them is a run doing
arithmetic on an unknown.

**Read this first: there are two ways to get them, and the better one is
[`work-in-the-browser`](../work-in-the-browser/SKILL.md).** Where the owner runs
an agent with browser access and has QuickBooks open in their own signed-in
window, the agent reads four of these five off the screen itself, records each
with the date, and keeps the read on disk so it can be checked afterwards. A
number a person recalls cannot be checked, and a number read off the screen and
saved can be.

    python3 bin/books.py browser confirm --company "the name the header shows"
    python3 bin/books.py browser read --surface banking --from reports/browser-reads/banking.json

This skill is what runs when that is not available: a chat agent with no
browser, a session where the owner would rather not have anything driving their
books, or any moment where the browser read comes back in a shape that does not
prove what it claims. Then ask, because the owner is already logged in and can
read five numbers off a screen faster than any scraper.

The one figure browser mode still cannot get on its own is the bank's own
balance, unless the owner also has their banking portal open. It does not come
from the QuickBooks tile. The tile shows what the books think the account holds,
and that is the figure under test.

## Hard gates

- **Ask, record, and stamp the date.** An undated answer is indistinguishable
  from a guess in a month. Every fact goes in with the day it was given and who
  gave it.
- **Never infer one of these from an export.** A feed that stopped and an
  account that went quiet look identical in a General Ledger. Say which is which
  only when a person has looked.
- **Where QuickBooks can export the thing, ask for the export instead of the
  number.** The For Review list per account (For review, then Export to Excel)
  and the rules file (Rules, then Export rules) are both better than a count,
  because a count cannot be checked afterwards.
- **Keep it short.** Six answers across five screens, not an interview. A long
  list gets no answers at all.

## Run it

```
python3 bin/books.py intake
```

With no arguments it prints each screen, what to read off it, why it matters,
and the exact line to type back. Run it again at any point: it keeps everything
already given and lists only what is still missing, with what each gap blocks.

```
python3 bin/books.py intake --account 101000 --bank-balance 37.14 --as-of 2025-12-31
python3 bin/books.py intake --account 101000 --for-review 241 --feed stopped --feed-last 2025-03-14
python3 bin/books.py intake --account 101000 --reconciled-through 2025-06-30
python3 bin/books.py intake --rules 15 --auto-add 15
python3 bin/books.py intake --last-human 2026-02-28 --note "the bookkeeper left"
```

## The five screens

| What to read | Where | Why it decides something |
|---|---|---|
| The bank's own balance | the bank or card issuer's site, not QuickBooks | The only figure that says whether the books are complete |
| The For Review count | Transactions, Bank transactions, the account, For review | The size of the job, and whether disconnecting that account would destroy anything |
| The feed | the same tile, the line under the balance saying when it last updated | A stopped feed is a hole in the books that no export shows |
| Reconciled through | Transactions, Reconcile, History by account | Items inside a reconciled month are re-downloads, and booking one double-counts |
| The rules | the gear, then Rules | A rule with auto-add on posts into the year being filed with nobody watching |
| Who worked in the file last | the gear, Audit log, all users | Everything after that date came from a rule or a sync rather than a person |

## How to ask

One question per paragraph, each ending in a question mark, ordered by what
unblocks the most. Say where the screen is before you say what to read. Never
ask for something the exports already answer, and never ask them to decide
something that is yours to decide.

A good round reads like this: "Open Transactions, Bank transactions in
QuickBooks. On each account tile there is a line under the balance saying when
it last updated. What does it say for each account?"

## The same facts, read off the screen instead

Where browser mode is available, these are the commands that replace the asking.
Each one validates the shape of what it was given before it records anything, so
a short read of a virtualized grid is refused rather than believed.

```
python3 bin/books.py browser read --surface banking            # tiles: queue depth, feed state
python3 bin/books.py browser read --surface for-review         # one account's queue, in full
python3 bin/books.py browser read --surface reconcile-summary  # reconciled through, every account
python3 bin/books.py browser read --surface rules              # rules, and how many post unseen
python3 bin/books.py browser read --surface audit-log          # who last worked in the file
```

They write into the same `answers/live-screen.json` with the same dates, so
every later command reads them the same way and cannot tell which route a fact
arrived by.

## What is done with the answers

They go in `answers/live-screen.json`, each with its date. Every later command
reads them, and each one says plainly what it still lacks rather than assuming a
zero. Three refusals turn on them directly: a merge cannot proceed without the
For Review count, the queue cannot be called finished without the bank balance,
and nothing produces figures for the return without the rules count.

## What good looks like

Six facts recorded, each dated. Two exports on disk rather than two numbers
recalled. Every command that runs afterwards naming what it used.
