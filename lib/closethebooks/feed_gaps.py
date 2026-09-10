"""Where a bank feed is not a complete record of the period.

    from closethebooks.feed_gaps import scan
    found = scan(ledger, queue=queue, profile=prof,
                 bank_balances={"101000": D("37.14")})
    for finding in found:
        print(finding.headline())

THE ASSUMPTION THIS EXISTS TO BREAK

Everything downstream of a For Review queue assumes the queue is the period. Work
every item and the account is done; the count of items is the amount of work
left. That assumption is not stated anywhere, which is why nothing checks it, and
on the file this module was written from it was false on the company's main
operating account: the feed carried nothing for eight months of the year being
filed. The queue for that account could be worked to zero and the account would
still be wrong, by roughly the amount of a year's inflow, and a return built on
it would be wrong with nothing anywhere saying so.

FOUR SIGNALS, AND THE LAST ONE IS PROOF

1. **Ledger months the feed does not reach.** The books post activity in a month
   and the feed contributes nothing to it. Only measured where the queue covers
   the account at all, because on an account with no queue every month reads
   missing and the finding would be about the export rather than the feed.

2. **An interior run of empty months.** Months with nothing, bounded by months
   with something on either side. A run at the END of a series is ordinary (the
   account may simply have stopped); a run in the MIDDLE is bounded by activity
   that proves the account was live before it and live after it.

3. **The last item against the period end.** A feed whose newest item is months
   before the period end is either an account that stopped or a feed that did.

4. **The balance walk.** With a bank balance known, take the book balance, add
   the net of everything sitting unbooked in the queue, and compare the result to
   the bank. If working the whole queue moves the book TOWARDS the bank, the
   queue is plausibly the rest of the story. If it moves the book AWAY, then
   transactions exist that are in neither the books nor the queue, and the
   arithmetic says how much:

       book        (41,876.20)   what the ledger holds
     + queue net   (52,309.55)   every unbooked item, netted
     = projected   (94,185.75)   where working the queue would land
       bank             37.14    what the bank says
       before       41,913.34    how far apart they are now
       after        94,222.89    how far apart they would be
       implied      94,222.89    net that is in neither place

   Signal 4 is the one that would have caught this file. The first three say a
   period looks thin. The fourth says it cannot be complete, in arithmetic
   anybody can check.

WHAT THIS WILL NOT DO

It will not say WHY a month is empty. An account with no activity in a month was
either dormant or is missing its records, and no export distinguishes them: a
dormant account and an account whose feed dropped both look like nothing. Every
finding names both and then names the document that settles it, which for all
four signals is the same one, the statement for the months in question.

WHEN NO QUEUE IS SUPPLIED

The gap signals fall back to the POSTED LEDGER as the series they measure, and
say so in the finding. A run of empty months in the ledger is a weaker claim
than one in the feed, because the books can lag the feed, and the wording
changes to match. What does not change is that the run is real and the months
are named.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Optional

from .model import is_real_account
from .util import ZERO, fmt, iso, money, month_key, month_range, norm_text

__all__ = [
    "FeedGap", "FeedScan", "BalanceWalk", "scan", "render", "render_markdown",
    "LEDGER_SERIES", "QUEUE_SERIES",
]

QUEUE_SERIES = "the For Review queue"
LEDGER_SERIES = "the posted ledger"

# An interior run shorter than this is ordinary. A month with no card activity
# happens; three in a row on an account that was live either side does not.
MIN_RUN = 2

# A feed whose newest item is older than this against the period end is stale
# enough to be worth naming.
STALE_DAYS = 45

_EITHER_CAUSE = (
    "An account with no items for a stretch of months was either dormant through "
    "them or is missing its records for them, and no export distinguishes the two: "
    "a dormant account and a feed that stopped both look like nothing."
)

_SETTLED_BY = (
    "the statement for those months, which states what the account actually did in "
    "them"
)


# ------------------------------------------------------------------ helpers

def _months_of(items) -> dict:
    out = {}
    for item in items:
        when = getattr(item, "date", None)
        if when is None:
            continue
        key = month_key(when)
        out[key] = out.get(key, 0) + 1
    return out


def _interior_runs(months_present, window) -> list:
    """Runs of consecutive empty months bounded by a present month either side.

    `window` is every month in the period, in order. Returns a list of lists of
    month keys. A run touching either end of the window is not interior and is
    not returned: nothing bounds it, so nothing proves the account was live
    across it.
    """
    present = set(months_present)
    if len(present) < 2:
        return []
    inside = [m for m in window if m >= min(present) and m <= max(present)]
    runs, current = [], []
    for month in inside:
        if month in present:
            if len(current) >= MIN_RUN:
                runs.append(current)
            current = []
        else:
            current.append(month)
    return runs


def _span_days(first, last) -> int:
    if first is None or last is None:
        return 0
    return (last - first).days


def _dates_of(items) -> list:
    return sorted(d for d in (getattr(i, "date", None) for i in items) if d is not None)


# ------------------------------------------------------------------ results

@dataclass
class BalanceWalk:
    """Book plus the unbooked queue against the bank. Signal 4, with its sums."""

    book: Optional[Decimal] = None
    queue_net: Decimal = ZERO
    bank: Optional[Decimal] = None
    queue_items: int = 0

    @property
    def ran(self) -> bool:
        return self.book is not None and self.bank is not None

    @property
    def projected(self) -> Optional[Decimal]:
        return None if self.book is None else money(self.book + self.queue_net)

    @property
    def before(self) -> Optional[Decimal]:
        if not self.ran:
            return None
        return abs(money(self.book - self.bank))

    @property
    def after(self) -> Optional[Decimal]:
        if not self.ran:
            return None
        return abs(money(self.projected - self.bank))

    @property
    def moves_away(self) -> bool:
        return bool(self.ran and self.after > self.before)

    @property
    def implied_missing(self) -> Optional[Decimal]:
        """The net that is in neither the books nor the queue."""
        if not self.ran:
            return None
        return money(self.bank - self.projected)

    def arithmetic(self) -> list:
        """The walk, one labelled figure per line, in the order it is computed."""
        if not self.ran:
            return []
        return [
            f"book {fmt(self.book)}",
            f"plus the net of {self.queue_items} unbooked item(s) {fmt(self.queue_net)}",
            f"gives {fmt(self.projected)}",
            f"against a bank balance of {fmt(self.bank)}",
            f"so the gap goes from {fmt(self.before)} to {fmt(self.after)}",
            f"leaving {fmt(self.implied_missing)} that is in neither place",
        ]


@dataclass
class FeedGap:
    """One account whose feed cannot be a complete record of the period."""

    key: str = ""
    label: str = ""
    series: str = QUEUE_SERIES
    period_start: object = None
    period_end: object = None
    # Signal 1: months the ledger posts in and the feed does not reach.
    ledger_only_months: list = field(default_factory=list)
    ledger_months: int = 0
    # Signal 2: interior runs of empty months, longest first.
    runs: list = field(default_factory=list)
    bracketed_by: tuple = ()             # (last date before, first date after)
    # Signal 3. `stale_days` is measured against the period end, which is what a
    # reader asks; `behind_file_days` against the newest item anywhere in the
    # file, which is what decides whether this ACCOUNT stopped or the whole file
    # did. Flagging every account on a file that stopped in April turns seven
    # findings into one fact said seven times.
    last_item: object = None
    stale_days: int = 0
    behind_file_days: int = 0
    # Signal 4.
    walk: BalanceWalk = field(default_factory=BalanceWalk)
    months_in_period: int = 0
    items: int = 0

    @property
    def missing_months(self) -> list:
        """Every month named by any gap signal, in order, without repeats."""
        out = []
        for month in list(self.ledger_only_months) + [m for run in self.runs for m in run]:
            if month not in out:
                out.append(month)
        return sorted(out)

    @property
    def longest_run(self) -> list:
        return max(self.runs, key=len) if self.runs else []

    @property
    def signals(self) -> list:
        out = []
        if self.ledger_only_months:
            out.append("months the books post in and the feed does not reach")
        if self.runs:
            out.append("an interior run of empty months")
        if self.stale_days > STALE_DAYS and self.behind_file_days > STALE_DAYS:
            out.append("the newest item is well before the period end")
        if self.walk.moves_away:
            out.append("working the queue moves the book away from the bank")
        return out

    @property
    def stale_only(self) -> bool:
        """Nothing wrong inside the period; it simply stopped before the end."""
        return self.signals == ["the newest item is well before the period end"]

    @property
    def proven_incomplete(self) -> bool:
        """True where the arithmetic, not an appearance, says records are missing."""
        return self.walk.moves_away

    def headline(self) -> str:
        run = self.longest_run
        # The walk comes first when it fires. The other three signals say a
        # period looks thin; the walk says it cannot be complete, so leading
        # with a month count would bury the only proof on the page.
        if self.walk.moves_away:
            return (f"{self.label} moves further from its bank balance when its queue "
                    f"is worked, from {fmt(self.walk.before)} apart to "
                    f"{fmt(self.walk.after)} apart.")
        if run:
            return (f"{self.label} has {len(run)} consecutive month(s) with no items in "
                    f"{self.series}, {run[0]} through {run[-1]}, on an account with "
                    f"items on both sides of them.")
        if self.ledger_only_months:
            return (f"{self.label} posts in {len(self.ledger_only_months)} of "
                    f"{self.ledger_months} month(s) that {self.series} does not reach: "
                    f"{', '.join(self.ledger_only_months)}.")
        return (f"{self.label} has no item in {self.series} for the {self.stale_days} "
                f"day(s) to {iso(self.period_end)}, and is {self.behind_file_days} "
                f"day(s) behind the newest item anywhere in the file.")

    def lines(self) -> list:
        out = [self.headline()]
        if self.bracketed_by and all(self.bracketed_by):
            before, after = self.bracketed_by
            out.append(
                f"The last item before the run is dated {iso(before)} and the first "
                f"after it {iso(after)}, {_span_days(before, after)} days apart.")
        if self.ledger_only_months and self.runs:
            out.append(
                f"{len(self.ledger_only_months)} of {self.ledger_months} month(s) with "
                f"posted activity have nothing in {self.series}: "
                f"{', '.join(self.ledger_only_months)}.")
        if self.walk.ran:
            walk = "; ".join(self.walk.arithmetic()) + "."
            if self.walk.moves_away:
                out.append(
                    "Working every item in the queue moves this account further from "
                    "the bank, not closer: " + walk)
                out.append(
                    "A queue that moves a book away from the bank cannot be the rest "
                    "of the story. Either transactions exist that are in neither the "
                    "books nor the queue, or the two balances are stated as of "
                    "different dates. Nothing in the exports separates those.")
            else:
                out.append("Working every item in the queue moves this account towards "
                           "the bank: " + walk)
        if self.series == LEDGER_SERIES:
            out.append(
                "No For Review queue was supplied for this account, so the months above "
                "were measured on the posted ledger. Books can lag a feed, so an empty "
                "month here is weaker evidence than an empty month in the feed.")
        out.append(_EITHER_CAUSE)
        out.append(
            f"What this means for the period: the queue cannot complete "
            f"{', '.join(self.missing_months) if self.missing_months else 'this period'}, "
            f"because it holds nothing for those months. What settles it: {_SETTLED_BY}.")
        return out

    def as_row(self) -> dict:
        return {
            "account": self.label,
            "account_key": self.key,
            "measured on": self.series,
            "months missing": ", ".join(self.missing_months),
            "longest run": (f"{len(self.longest_run)} month(s)" if self.runs else ""),
            "items": f"{self.items} of {self.items}",
            "newest item": iso(self.last_item),
            "book": fmt(self.walk.book) if self.walk.book is not None else "",
            "queue net": fmt(self.walk.queue_net),
            "bank": fmt(self.walk.bank) if self.walk.bank is not None else "",
            "gap before": fmt(self.walk.before) if self.walk.ran else "",
            "gap after": fmt(self.walk.after) if self.walk.ran else "",
            "moves away from the bank": "yes" if self.walk.moves_away else "no",
        }


@dataclass
class FeedScan:
    findings: list = field(default_factory=list)
    checked: int = 0
    excluded: list = field(default_factory=list)     # (label, why)
    not_checked: list = field(default_factory=list)  # what could not be measured
    period_start: object = None
    period_end: object = None
    file_last: object = None       # newest posted line anywhere in the ledger
    queue_last: object = None      # newest item anywhere in the queue

    @property
    def stale_only(self) -> list:
        return [f for f in self.findings if f.stale_only]

    @property
    def proven_incomplete(self) -> list:
        return [f for f in self.findings if f.proven_incomplete]

    def __bool__(self) -> bool:
        return bool(self.findings)

    def __len__(self) -> int:
        return len(self.findings)

    def __iter__(self):
        return iter(self.findings)

    def get(self, key):
        for f in self.findings:
            if f.key == key:
                return f
        return None


# --------------------------------------------------------------- the scan

def scan(ledger, *, queue=(), profile=None, bank_balances=None,
         period_start=None, period_end=None) -> FeedScan:
    """Every account whose feed cannot be a complete record of the period.

    `queue` is the For Review items, any iterable of objects with `date`,
    `account_key` and `amount`. Where an account has none, its gap signals are
    measured on the posted ledger instead and every finding says so.

    `bank_balances` maps an account key to what the bank says it holds, signed
    the way `model.BankLine.amount` and `statements.StatementFile` sign it: a
    bank balance positive, an amount owed on a card negative. That is the same
    convention the book balance already carries, which is why the walk needs no
    per-account-type special case.

    `profile` is read only for `AccountSpec.feed_last`, the date a feed last
    synced, which no export states.
    """
    start = period_start or getattr(ledger, "period_start", None)
    end = period_end or getattr(ledger, "period_end", None)
    out = FeedScan(period_start=start, period_end=end)
    if start is None or end is None:
        out.not_checked.append(
            "the ledger states no period, so no month could be walked")
        return out

    window = month_range(start, end)
    balances = {str(k): money(v) for k, v in (bank_balances or {}).items()}
    queue_items = list(queue or [])
    # The newest item in each series separately. Comparing a ledger-measured
    # account against a queue that runs into next year would report every posted
    # account as months behind, which is a fact about the queue and not about
    # the account.
    file_last = {
        QUEUE_SERIES: max(_dates_of(queue_items), default=None),
        LEDGER_SERIES: max(_dates_of(getattr(ledger, "lines", []) or []), default=None),
    }

    for account in sorted((getattr(ledger, "accounts", {}) or {}).values(),
                          key=lambda a: (a.number or "", a.full_name)):
        label = account.label() or account.full_name
        if account.role not in ("bank", "card"):
            continue
        if not is_real_account(ledger, account):
            out.excluded.append(
                (label, "nothing has ever been posted to it and it carries no balance, "
                        "so there is no period for a feed to be incomplete about"))
            continue
        out.checked += 1
        aliases = ledger.aliases_for(account.key)
        mine = [i for i in queue_items
                if norm_text(getattr(i, "account_key", "")) in aliases]
        posted = [l for l in getattr(ledger, "lines", [])
                  if norm_text(l.account) in aliases
                  or norm_text(l.account_full) in aliases]

        ledger_months = _months_of(posted)
        feed_months = _months_of(mine)
        series = QUEUE_SERIES if mine else LEDGER_SERIES
        measured = feed_months if mine else ledger_months

        ledger_only = ([m for m in sorted(ledger_months) if m not in feed_months]
                       if mine else [])
        runs = _interior_runs(measured, window)
        runs.sort(key=len, reverse=True)

        dates = _dates_of(mine if mine else posted)
        last = dates[-1] if dates else None
        stale = _span_days(last, end)
        behind = _span_days(last, file_last.get(series))

        bank = balances.get(account.key)
        if bank is None:
            bank = balances.get(account.full_name)
        walk = BalanceWalk(
            book=ledger.balance_of(account.key),
            queue_net=sum((money(getattr(i, "amount", ZERO)) for i in mine), ZERO),
            bank=bank, queue_items=len(mine),
        )
        if bank is None:
            out.not_checked.append(
                f"{label}: no bank balance was supplied, so the balance walk, the one "
                f"signal that can PROVE records are missing rather than suggest it, "
                f"did not run")

        bracket = ()
        if runs:
            longest = max(runs, key=len)
            before = [d for d in dates if month_key(d) < longest[0]]
            after = [d for d in dates if month_key(d) > longest[-1]]
            bracket = (before[-1] if before else None, after[0] if after else None)

        finding = FeedGap(
            key=account.key, label=label, series=series,
            period_start=start, period_end=end,
            ledger_only_months=ledger_only, ledger_months=len(ledger_months),
            runs=runs, bracketed_by=bracket, last_item=last, stale_days=stale,
            behind_file_days=behind,
            walk=walk, months_in_period=len(window),
            items=len(mine) if mine else len(posted),
        )
        if finding.signals:
            out.findings.append(finding)

    out.file_last = file_last.get(LEDGER_SERIES)
    out.queue_last = file_last.get(QUEUE_SERIES)
    out.findings.sort(key=lambda f: (not f.proven_incomplete, f.stale_only,
                                     -len(f.longest_run), -len(f.missing_months),
                                     f.key))
    return out


# ------------------------------------------------------------------ render

_INTRO = (
    "Everything downstream of a For Review queue assumes the queue is the period: "
    "work every item and the account is done. Each finding below is a reason that "
    "assumption does not hold for one account, with the months it does not hold "
    "for. Where a bank balance was known, the arithmetic of working the whole "
    "queue is shown, because a queue that moves a book AWAY from the bank is proof "
    "that transactions exist in neither place."
)


def _wrap(text, width=76, indent="") -> list:
    out, line = [], ""
    for word in str(text).split():
        if line and len(line) + 1 + len(word) > width:
            out.append(indent + line)
            line = word
        else:
            line = f"{line} {word}".strip()
    out.append(indent + line)
    return out


def _summary(scanned) -> str:
    return (f"{len(scanned.findings)} of {scanned.checked} bank and card account(s) "
            f"carry a period the feed cannot complete, "
            f"{len(scanned.proven_incomplete)} of them proven by arithmetic rather than "
            f"by appearance and {len(scanned.stale_only)} of them only because the "
            f"account stopped before the period end"
            + (f", against a file whose newest item anywhere is {iso(scanned.file_last)}"
               if scanned.file_last else "") + ".")


def render(scanned, *, limit=None, heading="Periods the feed cannot complete") -> list:
    if not isinstance(scanned, FeedScan):
        scanned = FeedScan(findings=list(scanned or []))
    out = [heading, "-" * min(len(heading), 78)]
    out += _wrap(_summary(scanned), indent="  ")
    if scanned.findings:
        out.append("")
        out += _wrap(_INTRO, indent="  ")
    shown = scanned.findings if limit is None else scanned.findings[:limit]
    for finding in shown:
        lines = finding.lines()
        out.append("")
        out += _wrap(lines[0], indent="  ")
        for line in lines[1:]:
            out += _wrap(line, width=74, indent="    ")
    if len(scanned.findings) > len(shown):
        out.append("")
        out += _wrap(f"... {len(scanned.findings) - len(shown)} more, all of them in "
                     f"the written report.", indent="  ")
    if scanned.not_checked:
        out.append("")
        out += _wrap(f"{len(scanned.not_checked)} check(s) could not run: "
                     + "; ".join(scanned.not_checked[:4]), indent="  ")
    return out


def render_markdown(scanned, *, heading="## Periods the feed cannot complete") -> list:
    if not isinstance(scanned, FeedScan):
        scanned = FeedScan(findings=list(scanned or []))
    out = [heading, ""]
    if not scanned.findings:
        out += [f"None. {scanned.checked} bank and card account(s) were checked and "
                f"every one of them has items across the whole period.", ""]
    else:
        out += [_summary(scanned), "", _INTRO, ""]
        for finding in scanned.findings:
            lines = finding.lines()
            out.append(f"- **{lines[0]}**")
            for line in lines[1:]:
                out.append(f"  {line}")
            out.append("")
    if scanned.not_checked:
        out.append(f"{len(scanned.not_checked)} check(s) could not run:")
        out.append("")
        for note in scanned.not_checked:
            out.append(f"- {note}.")
        out.append("")
    if scanned.excluded:
        out.append(f"{len(scanned.excluded)} account(s) were not swept: "
                   + "; ".join(f"{label}, because {why}"
                               for label, why in scanned.excluded[:6]) + ".")
        out.append("")
    return out
