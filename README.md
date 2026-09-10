# Close the Books

Catch up a year of QuickBooks Online with the AI agent you already run.

You give it your exported reports and your bank statements. It works out what
your books already say about where things go, and proposes what to do with every
uncategorized transaction. Then it either writes files you upload yourself, or,
if you run an agent with browser access, does the work inside QuickBooks in your
own signed-in window, twenty five rows at a time, with you approving each batch
before it is posted.

Nothing posts without you approving that exact batch, in your own terminal.

It is free and open, there is nothing to sign up for, and it is written by
[Median Labs](https://medianfi.com), an accounting firm that runs its own client
work on it. When you finish a run, we will read what it produced and tell you
what a preparer will ask about, at no charge. That is further down under
[Have someone read it before you file](#have-someone-read-it-before-you-file).

```
npx skills add median-labs/close-the-books
```

Then, in your agent: **"catch up my books"**.

## The kit sends nothing anywhere

The engine makes no network calls. It reads files on your machine and writes
files next to them. There is a test that fails the build if any module imports a
network library, because a promise like this should be enforced rather than
stated.

That holds in browser mode too, and it is worth being precise about why. The
engine still opens nothing. Your agent drives the browser with its own tools, in
a window you signed in to, and the engine decides what it may attempt and checks
afterwards that your books moved by exactly what you approved. The parts are
kept separate on purpose: a gate that runs inside the thing it is gating is a
gate that thing can route around.

Where your files go depends on which agent you run it in, and that part is not
ours to promise: Claude Code and Codex CLI read them locally, while ChatGPT
uploads whatever you attach to OpenAI. If that matters for your books, use a
local agent or the plain command line, which needs no agent at all.

## Why this exists

Books stop. A bookkeeper leaves, a founder gets busy, a bank feed quietly
disconnects, and eight months later there is a return due and a year of
transactions nobody has looked at.

The work of catching up is not hard. It is long, and it needs someone who knows
that a transfer between your own accounts is not income, that a payout already
booked by a journal entry must not be added a second time, and that a checking
account showing a negative balance is a symptom rather than a fact.

That knowledge is what these skills carry. Your agent does the reading and the
arithmetic. You make the decisions, because they are yours to make.

## What you get

| | |
|---|---|
| **A profile of your own books** | Your chart, and what your history says about where each vendor goes, mined from your own posted transactions rather than guessed |
| **A review workbook per batch** | Every uncategorized transaction with a proposal, the reason for it, and how many past transactions back that reason |
| **Bank upload files** | For the months a dead feed missed, formatted for QuickBooks and split to its size limits |
| **An entry worksheet** | The recurring adjusting entries a year needs, laid out to be typed quickly, grouped so recurring templates do most of the work |
| **A statement request** | Which accounts and which months you have to go and get, with the reason against each one, so you can forward it or work it in your banking portal |
| **A reconciliation report** | Every account, every month, with the difference stated even when it is zero |
| **Exit tests** | Ten checks that report the number they measured, so "done" is a claim with evidence behind it |
| **The balances on the wrong side** | Every account holding a balance on the side opposite its normal one, what that usually means, and the document that settles it |
| **An evidence file** | Every figure traced to how it was derived, for whoever checks the books before they are filed |
| **The list a preparer would otherwise chase** | What the firm filing your return has to be given, raised from your own chart: the questions, the documents, and what in your books called for each one |
| **Or the same work done in your books** | With browser mode, batches of twenty five rows posted into QuickBooks itself, each one approved by you and each one proved by a count read before and after |

## Install

| Where | How |
|---|---|
| **Claude Code** | `/plugin install median-labs/close-the-books`, which brings the skills, the approval hook, and browser mode |
| **Codex CLI, Cursor, Gemini CLI, Copilot and others** | `npx skills add median-labs/close-the-books` |
| **ChatGPT** | Download the repo as a ZIP, make a Project, upload the ZIP and `PLAYBOOK.md`, and set the playbook as the project instructions |
| **No agent at all** | `git clone`, then `python3 bin/books.py --help` |

Python 3.10 or newer, and `openpyxl`. Nothing else.

## What to export from QuickBooks

Reports, then export each as **Excel**, for the period you are catching up:

Account List, Trial Balance, General Ledger, Journal, Balance Sheet, Profit and
Loss Detail, Profit and Loss by Month.

From Banking, also useful: your For Review list per account (Export to Excel),
and your existing bank rules (Rules, then Export), which gives the exact template
your company file accepts.

Then your bank and card statements for every account and every month, CSV where
your bank offers it.

## How a run goes

1. **Say which year you are filing and when it is due.** Everything after this
   is counted against that year. A For Review queue holds next year's
   transactions too, and the feed offering back months that already reconciled,
   and neither is work this deadline needs.
2. **Learn.** It reads your exports, works out your chart, and mines what your
   own history says about where each vendor goes. It reports how much of your
   past year those rules would have coded correctly, so you can judge them.
3. **Read five things off the screen.** Your bank's own balance, the For Review
   count, whether each feed is connected, the last date each account reconciled,
   and how many bank rules are on. No export carries any of them, and four of
   them decide what the work is. It names the exact screen for each one.
4. **Turn off the rules that post by themselves.** A rule with auto-add on keeps
   putting entries into the year you are filing while you work on it.
5. **Scope the queue.** The count on the tab is not the job. On the file this
   was rebuilt around, 863 items were 312 items of work: 354 belonged to next
   year, and 197 were the feed offering back transactions already in the books.
   The rest is written out in full and comes back when its own deadline does.
6. **Get the statements.** `books.py statements` says which accounts and which
   months, with the reason for each: a feed that stopped in April needs
   everything from April, an account whose balance is on the wrong side needs
   its statement to settle which way, and an account that already reconciles
   needs nothing. It does not need you to have any statements to tell you that.
7. **Tie out.** Opening balance plus deposits minus withdrawals equals closing
   balance, for every account and every month. Until that holds you do not know
   you have all the transactions.
8. **Prove the period is complete.** Book balance, plus everything sitting
   unbooked, against what your bank says it holds. Where the queue moves an
   account away from its bank balance rather than toward it, transactions exist
   in neither place and working the queue will not find them.
9. **Ask.** A short list of questions your books cannot answer. Ten at most, each
   answerable from memory.
10. **Catch up.** Batches of a couple of hundred transactions. You review, you
    mark what you disagree with, and you approve the batch in your own terminal.
    In browser mode the same rows come in batches of twenty five and get posted
    into your books instead of written to a file.
11. **Fill the gaps and post the entries.** Upload files for the months a dead
    feed missed, and a worksheet for the recurring entries.
12. **Reconcile and check.** Every account against its statements, then the ten
    exit tests. Both run before you have a single statement, and say which parts
    they could not do and why, rather than refusing.
13. **Answer what the return needs.** Your books show the money. They do not
    show which of the wages went to officers, whether the balance owed by your
    German subsidiary is a loan or unpaid invoices, or who was paid as a
    contractor. Those come out of your chart as a list of questions and
    documents, each naming the account that raised it.
14. **Hand off.** `ready` refuses while anything on that list has nothing
    recorded against it, and produces the package when nothing does: the books,
    the evidence, the answers, the documents, and a covering note saying what
    was decided and by whom.

## Have someone read it before you file

Books that are right and books you can show are right are different things. The
second one is what a preparer, a lender or a buyer is actually testing, and it is
hard to judge from inside your own file.

So: finish a run, send us the two files it wrote, and one of our accountants will
read them and tell you what a preparer is likely to ask about. It costs nothing,
there is no email gate and no account, and you do not send your books. Send
`evidence.json`, which carries every figure and how it was derived, and
`exit-tests.md`, which carries the ten checks and the number each one measured.
Those two hold the derivations and the differences without the transactions
underneath them.

[medianfi.com/tools/quickbooks-cleanup](https://medianfi.com/tools/quickbooks-cleanup?utm_source=close-the-books&utm_medium=oss&utm_campaign=catchup)

## Importing into QuickBooks Online, honestly

This is the part most tools are vague about, so here it is plainly, for the US
version as of September 2026:

| | |
|---|---|
| **Bank transactions from CSV** | Yes, every plan. Banking, then Upload transactions. 1,000 rows and 350 KB per file, which the kit splits for you |
| **Bank rules from a file** | Yes, but only using a rules file you exported from your own company first, since the template holds QuickBooks' own encoding. And rules never apply to transactions already sitting in For Review, only to new ones |
| **Journal entries** | **No.** The US version cannot import them on any plan. You type them, you use a third-party importer app, or you let browser mode type them for you against an approved batch. That is why the entry output is a worksheet built for typing |

To categorize a backlog already in For Review, the path is selecting rows there
and using Batch actions, then Modify selected. The kit orders your review
workbook to make that fast.

One ordering trap worth knowing, and the kit refuses rather than warning about
it: disconnecting a bank feed **deletes** every unreviewed transaction in that
account's Pending and For Review tabs, and QuickBooks will not merge two
accounts while either is connected. So the documented fix for the same card
appearing twice starts with the step that destroys the evidence. Book what is
pending on both accounts, then dispose of the opening-balance plug, then
disconnect, then merge. On an account whose activity was never booked, that
queue is the only record of it anywhere in your file.

## What it will not do

On the file path it does not connect to QuickBooks at all, so it cannot change
anything in your books. In browser mode it can, and only inside the batch you
approved: it does not decide anything for you, and it never answers a question on your
behalf: what it collects for your preparer is what you said, with the date you
said it. It will not tell you your books are correct,
because a clean run means the checks it knows about passed, and no fixed set of
checks is the same thing as an audit. It is not tax advice or legal advice. It
does not value anything, and it does not decide the treatment of an instrument or
a transaction where the governing document is the answer.

Where something is genuinely ambiguous, it asks you rather than guessing. A tool
that guesses confidently is worse than one that stops.

## What it will refuse to do

These are not warnings. Each one stops, says which fact made it stop, and gives
you the command that changes the answer.

| It refuses | Because |
|---|---|
| In browser mode, disconnecting a feed, merging accounts, excluding transactions, deleting, voiding, or undoing a reconciliation | Each destroys a record that does not come back, so none of them is something an approval can make safe. It writes you the by-hand order instead, and says what every step destroys |
| In browser mode, continuing after a batch whose count did not move by exactly what you approved | A queue that fell by twenty six when twenty five were approved has something else posting into it. The next batch would be posted on top of it |
| Anything about disconnecting or merging an account while its For Review queue holds items | Disconnecting deletes them, and for an account whose activity was never booked they are the only record of it in your file |
| Calling the queue finished while an account's books plus its queue do not reach what the bank says it holds | An empty tab is not a complete period, and the difference does not show up anywhere else |
| Producing figures for a return while bank rules are still posting into the year you are filing | Any figure it gave you would be about a file that is still changing under it |
| Calling your books ready to file while something your preparer would have to ask for has nothing recorded against it | The chasing happens now or in the week before the deadline, and it costs the same either way. Where you cannot answer one, `--cannot "why"` records that you could not, which your preparer can work with. A blank reads as a no |

## Nothing posts without you

Three locks, and the third is the one that matters.

The library refuses to write a file into `import/` unless you have approved that
batch. In Claude Code a hook refuses the write as well, including through a shell
redirect, and refuses to let the agent approve a batch on your behalf. And
QuickBooks itself has no idea this exists: a file becomes a transaction when you
upload it and accept it, and not before.

You approve a batch by running this yourself:

```
python3 bin/books.py approve batch-01
```

If the workbook changes after you approve it, the approval no longer covers it
and everything downstream stops until you look again.

## Working in your books instead of in files

If you run Claude Code with browser access, there is a second way to do the same
work. You sign in to QuickBooks yourself, and your agent works in that window:
setting the category, class and payee on rows in For Review and accepting them,
adding transactions a dead feed never delivered, and typing the adjusting
entries, which is the one thing QuickBooks Online in the United States cannot
import at all.

```
python3 bin/books.py browser confirm --company "the name your header shows"
python3 bin/books.py browser read --surface for-review --from reports/browser-reads/queue.json
python3 bin/books.py browser plan --kind categorize
python3 bin/books.py approve batch-c1_01          # you, in your own terminal
python3 bin/books.py browser post batch-c1_01 --before 600
python3 bin/books.py browser verify batch-c1_01 --after 575
```

The whole thing is built around the fact that this can go wrong in a file that
belongs to somebody.

| | |
|---|---|
| **You sign in** | Your agent never sees a password. It cannot ask for one, store one, or type one. If QuickBooks asks for one mid-run, your session ended and you sign in again yourself |
| **The company is confirmed first** | QuickBooks keeps several companies behind one login. It reads the name off your header, checks it against your books, and re-checks it at every step |
| **Twenty five rows to a batch** | Twenty five rows a person reads is worth more than six hundred they scroll past. A three hundred row queue is twelve approvals, and that is the honest price of writing into books that are live |
| **Every batch is proved by a count** | It reads the queue before, works the batch, and reads the queue again. If the count did not fall by exactly the number of rows you approved, it halts. That difference is a rule firing underneath the work, a stale page, or a double click, and it is exactly the size nobody notices |
| **A halt stops everything** | Every batch waits, including ones you approved earlier. Clearing a halt takes a note saying what the difference was, and you are the one who writes it |
| **Two reads of a queue have to add up** | Every time it reads a For Review queue it compares that count to the previous one and to what you approved in between. Rows that moved with nothing approved behind them stop the account. That is the check that would catch an agent working rows without asking, which no gate on a click can do |
| **Six actions are refused rather than approved** | Disconnecting a feed, merging accounts, excluding transactions, deleting, voiding, undoing a reconciliation. Each destroys something that does not come back, so instead it writes you the by-hand order and says what every step destroys |

It needs a browser-capable agent and your own signed-in session. Without either
of those, the file path above is the path, and nothing about it changed.

The full rules are in [`skills/work-in-the-browser`](skills/work-in-the-browser/SKILL.md).
The one thing to know before starting is that browser mode has no upload step.
On the file path, your own hands on the upload are the last check. Here your
approval is, so read the batch.

## Try it on a company that does not exist

```
git clone https://github.com/median-labs/close-the-books
cd close-the-books
python3 examples/acme-robotics/build.py
python3 bin/books.py learn --exports examples/acme-robotics/exports
```

Acme Robotics is invented, and its books contain every defect this kit is built
to find: a feed that dies, a card that appears twice, a clearing account that
bleeds, amortization that stops, transfers that look like income, and a payout
that is already booked and would otherwise be booked again.

## Accuracy

This is a first pass by a careful process, not a professional opinion. Every
proposal carries the evidence behind it precisely so you can disagree with it.
Check anything that matters against your source records before you act on it, and
have an accountant look at the result before it goes anywhere that counts.

## Who made this, and why it is free

Median Labs is an accounting firm. Close the Books is the tool our own
accountants use, and almost every rule in it was put there by a real file that
broke the version before: a bank feed that had been dead for eight months with
nothing saying so, one card sitting in the chart twice because a feed was
relinked, an exit test that reported a passing number it had never opened a
report to measure.

We publish it because the useful part of an accounting firm is the judgement,
and judgement does not stop being ours when the checklist is public. A firm that
will show you the checks it runs is easier to judge than one that will not.
Whoever reads this and catches their own books up has our respect and cost us
nothing, and whoever reads it and decides they would rather hand the year over
knows what they would be buying.

Median is the finance team for owner-led businesses. Your books are current
through yesterday, in the QuickBooks Online or Xero file you already use.
[medianfi.com](https://medianfi.com?utm_source=close-the-books&utm_medium=oss&utm_campaign=readme).

### The mention inside the reports

Where a run finds something we would fix, the report says so in a sentence and
moves on. A clean run says nothing, because there is nothing to say. There is no
telemetry, no signup and no reduced version: the whole engine is here under an
MIT license, and the outbound link is the only thing in the repository that
could ever tell us anyone used it. One setting in `reference/MEDIAN.md` turns
the mention off and nothing else changes.

## Related

- [Books health check](https://github.com/median-labs/median-books-health-skill), for finding out what shape your books are in before you start
- [Company compliance check](https://github.com/median-labs/median-compliance-skill), for the filings a company owes

## License

MIT. Use it, fork it, ship it.

Intuit, QuickBooks, and QuickBooks Online are registered trademarks of Intuit
Inc. This project is not affiliated with, endorsed by, or sponsored by Intuit.
