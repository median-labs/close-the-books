---
name: stop-the-automation
description: Turn off the bank rules that are still posting into the year being filed, and find the entries they already posted with nobody watching. Use before any figure is produced for a return, and whenever a file has been left alone for months.
---

# Stop the automation

A company stopped being worked in April. Its bookkeeper left, its books went
quiet, and every person who had ever touched the file moved on. In December a
3,187.65 deduction appeared in the tax year, on an expense account, in a month
holding no other entry, with nothing behind it. A bank rule had matched a
descriptor and posted it, eight months after anyone was watching.

That entry is inside the year on the return. Nothing about the queue, the
balances or the trial balance would raise it: it balances, it foots, and it sits
in an account where entries like it belong.

## Hard gates

- **Nothing that produces figures for the return runs while rules are still
  posting into it.** `entries` and `handoff` refuse. Every hour worked before
  the rules stop is worked against a file that is still changing under it.
- **Export the rules before turning them off.** Rules, then Export rules. The
  export is how they go back afterwards, and it is also the only record of what
  was posting.
- **Never state who posted an entry unless an audit log says so.** An entry
  alone in a quiet month that lands exactly where a rule would put it was either
  posted by automation or entered by one person in a month nobody else touched,
  and no export separates the two. Say what was measured and name the audit
  history as what settles it.
- **A flagged entry is a question, not a reversal.** It gets removed or
  supported by the owner, on evidence, like any other entry.

## Run it

```
python3 bin/books.py intake --last-human 2026-02-28 --note "the bookkeeper left"
python3 bin/books.py intake --rules 15 --auto-add 15
```

With auto-add above zero, `entries` and `handoff` refuse and name the count. The
refusal also lists what has already posted into the filing year with nothing to
say a person made it, largest first.

In QuickBooks: the gear, Rules. Turn off "Automatically confirm transactions
this rule applies to" on each rule, or delete them. Then:

```
python3 bin/books.py intake --rules 15 --auto-add 0
```

A rule that only categorizes is not the same as a rule that posts. Rules can
stay; auto-add cannot, while the year being filed is still open.

## The four signals behind a flagged entry

1. **Alone in its period.** The only entry its month holds. A month somebody
   worked has a spread of entries in it.
2. **No attachment where the account's other entries have them.** Needs
   attachment data, which no QuickBooks xlsx export carries. Supplied or
   skipped, never inferred.
3. **An exact pattern match.** The counterparty has landed in that account often
   enough that a rule could be written from the company's own history, and this
   entry lands there too.
4. **A poster naming itself.** The strings an integration or a bank rule writes
   into a memo that a person would not type.

An audit log replaces all four. Pass one and the author is stated as a fact
rather than inferred.

## What to tell the owner

Two sentences. What is still posting, and what it has already put in the year
they are about to file. Then the one thing to do: turn off auto-add, export the
rules first.

Do not describe a flagged entry as fraud or as an error. It is an entry with
nothing behind it, dated inside the year on the return, and the fix is either a
document or a reversal.

## What good looks like

Auto-add at zero on every rule, recorded with the date. The rules file exported
and in the working directory. Every entry flagged in the filing year either
supported by a document or reversed, with the decision recorded.
