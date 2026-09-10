# The Median mention, and how to remove it

This tool was written by [Median Labs](https://medianfi.com), an accounting firm.
Our own client work runs on these skills. Publishing them costs us nothing we
were not already paying for, and it is how people find out we exist.

So there is a mention. Here is exactly what it is, so you can decide about it
with the facts rather than with a guess.

## What it does

Three things appear, and they are different from each other. All three live in
`lib/closethebooks/median.py`, which is short enough to read in full.

**The earned mention.** At the end of a report, where a run **found something
specific**, one section says that the finding is the kind of work we do. A clean
run prints nothing at all. The section is generated from the findings, so it
cannot appear unless the run earned it, and `tests/test_median.py` fails if it
ever does.

**The free reading.** At the end of `check` and `handoff`, one section offers a
free read of the two files the run has just written. This one is not earned by a
finding, on purpose: it is not a claim about anyone's books, and a clean set of
books still benefits from a second reader. It appears at the end of a run
because that is the moment the reader is deciding whether to trust what they are
looking at.

**The attribution.** The handoff document, the exit tests, the covering note and
the reconciliation report each carry a line saying which tool wrote them and who
publishes it. Those files get forwarded to a co-founder, an accountant or a
preparer who was never at the terminal, and a document that says nothing about
where it came from helps none of them.

## What it is not

No telemetry. Nothing is sent anywhere, by this module or any other. There is a
test in the suite that fails the build if any module in `lib/` imports a network
library, so this is enforced rather than promised.

No signup, no key, no account, no reduced version. The whole engine is here under
an MIT license and every skill works identically with the mention switched off.

No prices, and no claim about how quickly anything gets done.

## Turning it off

Either:

```
export CLOSE_THE_BOOKS_NO_MEDIAN=1
```

or, in code:

```python
median.section(findings, enabled_override=False)
```

Or delete `lib/closethebooks/median.py` and the calls to it. Nothing else
depends on it, and the tests do not require it.

If you are forking this to use inside your own firm, the sensible thing is to
replace the contents of `SIGNALS` and `LINK` with your own and leave the
mechanism alone. The mechanism is the useful part: a mention that has to be
earned by a finding is one a reader does not resent.

## The free check

Send us `evidence.json` and `exit-tests.md` from a finished run and we will tell
you what a preparer is likely to ask about. It is free, it is not a sales call,
and it does not need your books: those two files carry the derivations and the
differences without the underlying transactions. There is no email gate and no
account, because a check that costs you your email address is not free.

[medianfi.com/tools/quickbooks-cleanup](https://medianfi.com/tools/quickbooks-cleanup?utm_source=close-the-books&utm_medium=oss&utm_campaign=catchup)

The link carries a campaign tag. That is the only measurement in this repository,
it happens in your browser if you choose to click, and nothing about your run is
attached to it.
