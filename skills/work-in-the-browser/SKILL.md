---
name: work-in-the-browser
description: Work inside QuickBooks Online directly, in a browser the owner is already signed in to, instead of handing them files to upload. Categorize the For Review queue, add transactions a dead feed never delivered, and post journal entries, one approved batch at a time, with the count checked before and after every batch. Use when the owner can run an agent with browser access and wants the work done in their books rather than in files.
---

# Work in the browser

Everything else in this kit reads a file and writes a file, and the owner
carries the result into QuickBooks. This mode does the work in QuickBooks, in a
browser window the owner is signed in to, while they watch.

That is more useful and more dangerous, and the second half is what most of
this skill is about.

## What the owner needs before any of this is possible

Three things, and if any is missing the file-based path is still there and still
works.

| | |
|---|---|
| An agent with browser control | Claude Code with browser access. Nothing here works from a chat window that cannot drive a browser |
| Their own signed-in QuickBooks session | They sign in themselves, in their own window, in the company they want worked on |
| The plugin, and the hook that comes with it | `/plugin install median-labs/close-the-books`. The hook is what refuses a write with no approval behind it |

Say this to them plainly before starting, because it decides which path they are
on: **you sign in, this agent never sees a password, and it will never ask for
one or type one.** If a QuickBooks page asks for a password mid-run, the session
ended. They sign in again themselves and the run picks up.

Where they cannot run a local agent with a browser, use the file path instead:
`catch-up`, `fill-feed-gaps` and `post-recurring-entries` produce files they
upload. Nothing about that path changed.

## Hard gates

- **Confirm the company before reading anything.** QuickBooks keeps several
  companies behind one login and switches between them from a menu. Read the
  name in the header and run `books.py browser confirm --company "..."`. It
  refuses if that is not the company in the profile, and every later step checks
  it again.
- **Never ask for, store, or type a credential.** There is no case where this is
  the right answer, including a session that expired mid-batch.
- **Nothing is posted without an approval of that exact batch.** The owner runs
  `books.py approve batch-XX` in their own terminal. An agent that runs it has
  approved on their behalf, which records nothing.
- **One batch, one approval, 25 rows.** An approval covers the rows in that
  batch and no others. Rebuilding the batch or changing a row voids it.
- **Read the count before, post, read the count after.** If the count did not
  move by exactly the number of rows approved, the run halts and nothing else
  happens until a person has found out why.
- **Read the queue again between sessions.** Every read of a For Review queue is
  compared to the previous read and to the batches approved in between. Rows
  that moved with no approval behind them stop the account, and that check is
  the only one here that would notice an agent working rows without asking,
  because nothing can tell a click that posts from a click that opens a filter.
- **Turn the automatic rules off first.** The count check assumes nothing else
  is posting. `books.py browser post` refuses while any rule has auto-add on,
  because a rule firing underneath the work makes every batch halt and makes a
  real mis-click impossible to tell from a rule doing its job.
- **Six actions are refused outright and there is no flag.** Disconnecting a
  feed, merging accounts, excluding transactions, deleting, voiding, undoing a
  reconciliation. Where one of them is the right fix,
  `books.py browser runbook <name>` writes the order for the owner to work by
  hand, and says what each step destroys.
- **A page that reads wrong stops the run.** A row count that does not match, a
  company name that is not the confirmed one, a read that comes back in an
  unexpected shape, an error banner. Say what was seen. Never retry blindly into
  a live ledger.
- **Pass on what the page said.** Put any banner or error into the read as
  `"error"` or `"banner"` and the read is refused rather than recorded. The
  numbers under a banner are usually the ones that were there before it
  appeared, so a read taken across one passes every other check and is still
  wrong. A banner about signing in means their session ended, which is a thing
  to tell them and never a thing to solve.

## Ask for the statements, and then use them

The owner is not sending these to anybody. They put them where their own agent
can read them, in `statements/`, one file per account per month, CSV where their
bank offers it. Also the processor export, if they take card payments, and the
payroll reports for every quarter of the year being filed.

Those files are not paperwork. They are what makes three things possible that
the queue on its own cannot do:

1. **Book what the feed never delivered.** A feed that stopped in April means
   the bank holds rows the books have never seen, and no amount of queue work
   finds them. `browser plan --kind add` builds those batches from the
   statements.
2. **Tie out each month.** Opening balance plus deposits minus withdrawals
   equals closing balance, per account per month. Until that holds, nobody knows
   the transactions are all there.
3. **Settle a balance that is on the wrong side.** A card showing a debit
   balance or a checking account showing a credit one is a symptom. The
   statement says which way it really is.

`books.py statements` says which accounts and which months are needed, with the
reason against each, and it says so before there is a single statement on disk.

## Run it

```
python3 bin/books.py browser confirm --company "the name the header shows"
```

Then read the screens. Each read is written to a JSON file first, so what the
screen said on the day stays on disk and can be checked afterwards:

```
python3 bin/books.py browser read --surface banking --from reports/browser-reads/banking.json
python3 bin/books.py browser read --surface for-review --from reports/browser-reads/queue-1010.json
python3 bin/books.py browser read --surface reconcile-summary --from reports/browser-reads/reconcile.json
python3 bin/books.py browser read --surface rules --from reports/browser-reads/rules.json
python3 bin/books.py browser read --surface audit-log --from reports/browser-reads/audit.json
```

Those five answer everything `read-the-screen` used to ask the owner for. The
guided intake is now the fallback rather than the path.

**Carry the rows, not only the count.** Put the For Review rows in the read as
`"rows": [{"date": ..., "description": ..., "amount": ...}]` and they are
written into `for-review/` as a three column file, which is the same shape a
QuickBooks export has. Everything downstream then reads them the way it reads an
export, and the owner is not asked to export a queue that has already been read.
A read that carries only the count still records the count, and planning then
needs the export: Banking, the account, For review, Export to Excel.

Then plan, and let them approve:

```
python3 bin/books.py browser plan --kind categorize
python3 bin/books.py approve batch-c1_01          # they run this, in their terminal
python3 bin/books.py browser post batch-c1_01 --before 600
python3 bin/books.py browser verify batch-c1_01 --after 575
python3 bin/books.py browser status
```

`plan` also takes `--kind add` for months a dead feed missed, and
`--kind journal --through 2025-12-31` for the adjusting entries. Journal entries
matter more here than anywhere else: QuickBooks Online in the United States
cannot import one on any plan, so before this mode the only route was a person
typing each entry off a worksheet.

## What each read answers

| Screen | What only it knows |
|---|---|
| Banking, the account tiles | the QuickBooks balance, the queue depth per account, and the line saying when each feed last updated |
| For review, per account | the queue itself, row by row |
| Reconcile, summary view | reconciled-through for every account in one view |
| The gear, then Rules | how many rules there are, and how many post without being seen |
| The gear, then Audit log | when a person last worked in the file, so everything after that date is a rule or a sync |

The bank's own balance is not on any of those. It comes from the bank or the
card issuer's own site, and a read that claims one has to say which screen it
came from. The QuickBooks tile shows what the books think, which is the figure
under test.

## What this interface does that nothing warns you about

Every one of these cost somebody real work.

- **The For Review grid is virtualized.** Only the visible window is in the
  document, so a naive read returns about 34 rows whatever the count says.
  Setting `scrollTop` reports the new value back and re-renders nothing. Scroll
  with a real wheel event, or read the whole row model off the table instance,
  and then check the number of rows against the number the header claims.
  `browser read` refuses a short read and says which trap it looks like.
- **The count on the tab undercounts.** It is the default view, which is one
  type filter. Split the filter, read each part, add the parts. On one file the
  tab said 401 and the parts summed to 600.
- **The row checkbox only exists on hover.** So does the row's own post control.
  A click at its coordinates lands on nothing until the pointer is over the row.
- **The Class column is off by default.** Turn it on under the gear, then
  Columns. Posting without it writes "Not specified", which is a defect rather
  than a state.
- **Bulk selection does not survive a re-render.** Post row by row.
- **On a card payment row, leave the Match toggle alone.**
- **The journal form only mounts in a fresh tab.** In a tab that has already
  loaded the report builder it fails silently, with about 125 characters of body
  text, no console error and no failed request. Chain entries with Save and new.
  Escape closes the whole panel and discards it.
- **Verify a write on the register, not the report builder.** The register is
  plain text with a running balance, it paginates at 300 rows, and it defaults
  to date ascending.
- **A wrong report token spins forever rather than failing.** `PANDL`,
  `BAL_SHEET`, `ACCTL_QUICKREPORT`, `TRIAL_BAL` and `GEN_LEDGER` work.
  `PROFITANDLOSS`, `P_AND_L` and `GENERAL_LEDGER` all hang.
- **Report dates are set in the interface, never in the address bar.** The query
  string is ignored and the report renders empty, which looks exactly like a
  period with nothing in it.

## The refusals, and what to do instead

| Refused | What it destroys |
|---|---|
| Disconnecting a feed | every unreviewed row in that account's Pending and For Review tabs. On an account whose activity was never booked, that queue is the only record of it in the file |
| Merging accounts | nothing by itself, but QuickBooks will not merge while either account is connected, so its own documented fix starts with the disconnection |
| Excluding transactions | the row, as far as every later view is concerned. The Excluded tab paginates, so a partial read of it produces a reconciliation that cannot close |
| Deleting | the record and its place in the audit history |
| Voiding | the amount, in a period that may be closed and may have been filed |
| Undoing a reconciliation | the marks for that period and often for every period after it |

`books.py browser runbook <name>` writes the by-hand order for any of them. Give
that to the owner. Do not do it for them, and do not offer to.

## What good looks like

The company confirmed and named in the output of every step. Five screens read
and recorded with the date. Every rule with auto-add turned off before the first
batch. Batches of 25, each approved on its own, each proved by a count that
moved by exactly the number of rows in it. Any halt investigated and cleared
with a note saying what it was. And when the owner asks what changed in their
books, `books.py browser status` answers with a number rather than a claim.
