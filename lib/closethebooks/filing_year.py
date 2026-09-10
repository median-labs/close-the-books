"""Which unbooked items belong to the return being filed, and which do not.

    from closethebooks.filing_year import scope, reconciled_through_from

    scoped = scope(queue, 2025,
                   reconciled_through=reconciled_through_from(recon),
                   history_of=twins.scan(ledger).history_map())
    scoped.headline()          # "312 of 863 queue item(s) are in scope ..."
    for line in scoped.render():
        print(line)

THE NUMBER EVERYONE REPEATS IS THE QUEUE DEPTH, AND IT IS THE WRONG NUMBER

On the file this module was written from, "863 unbooked items" was quoted for a
month, in status notes and to the client, and an estimate of 60 to 88 hours was
built on it. Neither figure survived the first partition:

    863   items in the For Review queue
    354   dated in the year AFTER the one being filed. Nothing about them
          touches the return that is due.
    509   dated inside the filing year
    197   of those 509 sit in months that were reconciled clean, with zero
          changes, on the account the history belongs to. They are the feed
          offering back transactions that are already booked.
    312   left. That is the work.

`863` and `312` differ by a factor of 2.7, and every downstream number, the
hours, the fee, the date the return can be filed, was derived from the wrong
one.

THE DANGEROUS PARTITION IS THE THIRD ONE

An item that postdates the filing year is merely irrelevant. An item that
predates the reconciled-through date is a TRAP: booking it posts a second copy
of a transaction that is already in the books, inside a period somebody has
already tied to a statement and signed off. The reconciliation that used to
close at 0.00 no longer does, and the person who finds that later has no way to
tell which of the two copies was the original. So this partition is reported
with its net amount, which is the amount that would be double counted.

RECONCILED HISTORY DOES NOT ALWAYS LIVE ON THE ACCOUNT THE FEED IS ON

This is the trap inside the trap, and it is why `history_of` exists. When a bank
link is rebuilt, QuickBooks can create a SECOND account beside the original.
The new one carries the live feed and the whole queue and has never been
reconciled; the original carries the reconciled history and no feed. Ask "has
the account these 197 items are on ever been reconciled" and the answer is no,
and all 197 read as real work. Ask it of the account whose history governs them
and the answer is "reconciled clean through February", which is the true one.

`twins.TwinScan.history_map()` produces that mapping. Passing it is what makes
the check see the reconciled period; without it this module is honest about
what it could not check rather than quietly counting the items as work.

NOTHING HERE DECIDES WHAT TO DO WITH ANY OF IT

A partition is a statement about dates and a reconciliation, not a disposition.
Whether an already-booked item is excluded, matched or investigated is a
decision, and it is made where decisions are made.
"""

from __future__ import annotations

import datetime
from dataclasses import dataclass, field
from decimal import Decimal

from .util import ZERO, fmt, iso, money, parse_date

__all__ = [
    "IN_SCOPE", "POSTDATES", "PREDATES", "ALREADY_BOOKED", "PARTITIONS",
    "Partition", "FilingScope", "scope", "reconciled_through_from",
    "render", "render_markdown",
]

# The four buckets. Every item lands in exactly one, so the four counts sum to
# N and a reader can add them up.
IN_SCOPE = "in scope"
POSTDATES = "postdates the filing year"
PREDATES = "predates the filing year"
ALREADY_BOOKED = "predates the reconciled-through date"

PARTITIONS = (IN_SCOPE, ALREADY_BOOKED, POSTDATES, PREDATES)

_WHY = {
    IN_SCOPE: (
        "dated inside the filing year, and after the last date the account was "
        "reconciled through, so nothing in the books already accounts for it"
    ),
    ALREADY_BOOKED: (
        "dated on or before the date the account was reconciled through. A "
        "reconciled month has been tied to a statement and closed, so an item the "
        "feed offers for that month is a transaction the books already hold. "
        "Booking it posts a second copy and breaks a reconciliation that closed "
        "at 0.00"
    ),
    POSTDATES: (
        "dated after the end of the filing year, so it belongs to a later return "
        "and changes nothing about this one"
    ),
    PREDATES: (
        "dated before the start of the filing year and outside any reconciled "
        "period, so it belongs to an earlier return"
    ),
}

_NO_RECONCILIATION = (
    "No reconciliation date is known for this account, so no item on it could be "
    "tested for being already booked. That is not the same as none being already "
    "booked; it means the check could not run."
)


# ------------------------------------------------------------------ helpers

def _as_date(value, field_name="date"):
    if value is None:
        return None
    if isinstance(value, datetime.datetime):
        return value.date()
    if isinstance(value, datetime.date):
        return value
    return parse_date(value, field=field_name)


def _year_bounds(filing_year, year_start=None, year_end=None):
    """The first and last day of the year being filed.

    A fiscal year that is not the calendar year is passed explicitly, because
    guessing it from a year number is how a June year end silently becomes a
    December one.
    """
    start = _as_date(year_start)
    end = _as_date(year_end)
    if start is not None and end is not None:
        return start, end
    if filing_year is None:
        raise ValueError("filing_year is required unless both year_start and "
                         "year_end are given")
    year = int(filing_year)
    return (start or datetime.date(year, 1, 1)), (end or datetime.date(year, 12, 31))


def _item_date(item):
    return _as_date(getattr(item, "date", None) or (
        item.get("date") if isinstance(item, dict) else None))


def _item_account(item) -> str:
    if isinstance(item, dict):
        return str(item.get("account_key") or "")
    return str(getattr(item, "account_key", "") or "")


def _item_amount(item) -> Decimal:
    if isinstance(item, dict):
        return money(item.get("amount"))
    return money(getattr(item, "amount", ZERO))


def _normalise_reconciled(reconciled_through):
    """Accept a single date, a mapping, or None. Always return a mapping.

    `(mapping, blanket)`: `blanket` is a date that applies to every account, or
    None. A mapping entry always wins over the blanket, including a mapping
    entry whose value is None, which is how a caller says "this one account has
    never been reconciled" against a blanket date for the rest.
    """
    if reconciled_through is None:
        return {}, None
    if isinstance(reconciled_through, dict):
        return ({k: _as_date(v) for k, v in reconciled_through.items()}, None)
    return {}, _as_date(reconciled_through)


def _resolve_history(account_key, history_of) -> str:
    """Which account's reconciliation history governs `account_key`.

    Follows the mapping at most four hops and stops on a cycle, because a
    mapping is data and a cycle in it must not hang the scan.
    """
    seen = {account_key}
    key = account_key
    for _ in range(4):
        nxt = (history_of or {}).get(key)
        if not nxt or nxt in seen:
            break
        seen.add(nxt)
        key = nxt
    return key


# ------------------------------------------------------------------ results

@dataclass
class Partition:
    """One bucket of the queue: what is in it, why, and what it adds up to."""

    name: str = ""
    reason: str = ""
    items: list = field(default_factory=list)
    total: int = 0                       # N, the whole queue

    @property
    def count(self) -> int:
        return len(self.items)

    @property
    def net(self) -> Decimal:
        return sum((_item_amount(i) for i in self.items), ZERO)

    def n_of_n(self) -> str:
        return f"{self.count} of {self.total}"

    def months(self) -> list:
        out = []
        for item in self.items:
            when = _item_date(item)
            if when is None:
                continue
            key = f"{when.year:04d}-{when.month:02d}"
            if key not in out:
                out.append(key)
        return sorted(out)

    def accounts(self) -> list:
        out = []
        for item in self.items:
            key = _item_account(item)
            if key and key not in out:
                out.append(key)
        return sorted(out)

    def headline(self) -> str:
        return (f"{self.n_of_n()} item(s) {self.name}, netting {fmt(self.net)}"
                + (f", across {', '.join(self.months())}" if self.months() else "")
                + ".")

    def as_row(self) -> dict:
        return {
            "partition": self.name,
            "items": self.n_of_n(),
            "net": fmt(self.net),
            "months": ", ".join(self.months()),
            "accounts": ", ".join(self.accounts()),
            "why": self.reason,
        }


@dataclass
class FilingScope:
    """The queue, partitioned against one filing year and one reconciliation."""

    filing_year: object = None
    year_start: object = None
    year_end: object = None
    total: int = 0
    partitions: dict = field(default_factory=dict)      # name -> Partition
    # account key -> the date it is reconciled through, as used. A key mapping
    # to None means nothing establishes one, and every item on that account
    # went unchecked for the already-booked trap.
    reconciled_through: dict = field(default_factory=dict)
    # account key -> the account whose history was consulted, where it differs.
    history_taken_from: dict = field(default_factory=dict)
    undated: list = field(default_factory=list)
    notes: list = field(default_factory=list)

    # ---------------------------------------------------------- accessors

    def part(self, name) -> Partition:
        return self.partitions.get(name, Partition(name=name, total=self.total))

    @property
    def in_scope(self) -> list:
        return self.part(IN_SCOPE).items

    @property
    def already_booked(self) -> list:
        return self.part(ALREADY_BOOKED).items

    @property
    def out_of_scope(self) -> list:
        return (self.part(POSTDATES).items + self.part(PREDATES).items
                + self.part(ALREADY_BOOKED).items)

    @property
    def never_reconciled(self) -> list:
        """Accounts with items and no reconciliation date to test them against."""
        return sorted(k for k, v in self.reconciled_through.items() if v is None)

    @property
    def unchecked_for_double_count(self) -> int:
        """How many items could not be tested for the double-count trap."""
        blind = set(self.never_reconciled)
        if not blind:
            return 0
        return sum(1 for name in PARTITIONS
                   for item in self.part(name).items
                   if _item_account(item) in blind)

    def counts(self) -> dict:
        return {name: self.part(name).count for name in PARTITIONS}

    def __len__(self) -> int:
        return len(self.in_scope)

    # ------------------------------------------------------------- output

    def headline(self) -> str:
        part = self.part(IN_SCOPE)
        return (f"{part.n_of_n()} queue item(s) are in scope for the "
                f"{self._year_text()} return.")

    def _year_text(self) -> str:
        if self.filing_year is not None:
            return str(self.filing_year)
        return f"{iso(self.year_start)} to {iso(self.year_end)}"

    def lines(self) -> list:
        """The partition as a person reads it. Every bucket, including empty ones."""
        out = [self.headline(),
               f"The filing year runs {iso(self.year_start)} to {iso(self.year_end)}. "
               f"{self.total} item(s) were partitioned; "
               + " plus ".join(f"{self.part(n).count} {n}" for n in PARTITIONS)
               + f" is {sum(self.counts().values())}."]
        for name in PARTITIONS:
            part = self.part(name)
            if name == IN_SCOPE:
                continue
            out.append(f"{part.headline()} Why: {part.reason}.")
        blind = self.never_reconciled
        if blind:
            out.append(
                f"{self.unchecked_for_double_count} of {self.total} item(s) sit on "
                f"{len(blind)} account(s) with no known reconciliation date "
                f"({', '.join(blind[:6])}), so none of them could be tested for "
                f"already being booked. {_NO_RECONCILIATION.split('. ', 1)[1]}")
        for key, source in sorted(self.history_taken_from.items()):
            out.append(
                f"The reconciliation history for {key} was read from {source}, "
                f"which is where that account's reconciled months are recorded.")
        if self.undated:
            out.append(f"{len(self.undated)} of {self.total} item(s) carry no date "
                       f"that can be read and were partitioned nowhere.")
        out += list(self.notes)
        return out

    def as_rows(self) -> list:
        return [self.part(name).as_row() for name in PARTITIONS]


# --------------------------------------------------------------- the scan

def scope(queue, filing_year=None, *, year_start=None, year_end=None,
          reconciled_through=None, history_of=None) -> FilingScope:
    """Partition a queue of unbooked items against one filing year.

    `queue` is any iterable of objects carrying `date`, `account_key` and
    `amount`, which `model.BankLine` does and a plain dict may. Nothing is
    mutated and nothing is dropped: the four partition counts plus `undated`
    add up to the length of `queue`.

    `filing_year` is a year number. A fiscal year that is not the calendar year
    is given as `year_start` and `year_end` instead, because inferring a June
    year end from the number 2025 is a guess.

    `reconciled_through` is a date applying to every account, a mapping of
    account key to date, or None. None means no reconciliation is known, and
    then no item is placed in the already-booked partition and the scope says
    so rather than reporting a clean sweep it did not do.

    `history_of` maps an account key to the account whose reconciliation
    history governs it. See the module docstring: on a duplicate account pair
    the feed and the queue sit on one account and the reconciled history on the
    other, and without this mapping every item in the reconciled period reads as
    work.
    """
    start, end = _year_bounds(filing_year, year_start, year_end)
    per_account, blanket = _normalise_reconciled(reconciled_through)
    items = list(queue or [])

    out = FilingScope(filing_year=filing_year, year_start=start, year_end=end,
                      total=len(items))
    buckets = {name: [] for name in PARTITIONS}

    for item in items:
        when = _item_date(item)
        if when is None:
            out.undated.append(item)
            continue
        account = _item_account(item)
        governs = _resolve_history(account, history_of)
        if governs != account:
            out.history_taken_from[account] = governs
        if governs in per_account:
            through = per_account[governs]
        elif account in per_account:
            through = per_account[account]
        else:
            through = blanket
        out.reconciled_through.setdefault(account, through)

        # Order matters. Already-booked is tested FIRST because it is the only
        # partition whose members cause damage when they are worked, and that
        # is true whether or not they also fall outside the filing year.
        if through is not None and when <= through:
            buckets[ALREADY_BOOKED].append(item)
        elif when > end:
            buckets[POSTDATES].append(item)
        elif when < start:
            buckets[PREDATES].append(item)
        else:
            buckets[IN_SCOPE].append(item)

    for name in PARTITIONS:
        out.partitions[name] = Partition(name=name, reason=_WHY[name],
                                         items=buckets[name], total=len(items))
    if not per_account and blanket is None:
        out.notes.append(
            "No reconciliation date was supplied for any account, so the "
            "already-booked partition is empty because the check could not run, "
            "not because nothing is already booked.")
    return out


def reconciled_through_from(recon, *, require_zero=True) -> dict:
    """{account key: the date its books are reconciled clean through}.

    Derived from a `recon.ReconReport`: for each account, walk its months in
    order from the start of the period and stop at the first month that is not
    checkable or whose difference is not exactly 0.00. The date returned is the
    last day covered by the last month that passed, which is the month end the
    report itself used.

    Stopping at the first failure is the point. A February that ties and a March
    that does not means the books are reconciled through February, and an April
    that happens to tie afterwards does not extend that: the chain is broken and
    every balance after the break rests on the unexplained difference.

    An account with no month that passes is absent from the result, which is a
    different answer from a date and is what makes `never_reconciled` truthful.
    """
    by_account = {}
    for row in (getattr(recon, "rows", None) or []):
        by_account.setdefault(row.account_key, []).append(row)
    out = {}
    for key, rows in by_account.items():
        rows.sort(key=lambda r: r.month)
        last = None
        for row in rows:
            ties = bool(getattr(row, "checkable", False))
            if require_zero:
                ties = ties and money(getattr(row, "difference", None) or ZERO) == ZERO
            if not ties:
                break
            last = row.month
        if last:
            out[key] = _month_end(last)
    return out


def _month_end(month_key: str):
    year, month = (int(p) for p in str(month_key).split("-")[:2])
    if month == 12:
        return datetime.date(year, 12, 31)
    return datetime.date(year, month + 1, 1) - datetime.timedelta(days=1)


# ------------------------------------------------------------------ render

_INTRO = (
    "The queue depth is not the amount of work. An item dated after the filing "
    "year changes nothing about the return that is due, and an item dated inside "
    "a period that has already been reconciled is a transaction the books "
    "already hold: booking it posts a second copy and breaks a reconciliation "
    "that closed at 0.00. Both are counted below rather than dropped, so the "
    "four partitions add up to the number everybody has been quoting."
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


def render(scoped: FilingScope, *, heading="Unbooked items in scope for the return") -> list:
    """Terminal lines. Markdown-free, so both renderers share it."""
    out = [heading, "-" * min(len(heading), 78)]
    lines = scoped.lines()
    out += _wrap(lines[0], indent="  ")
    out.append("")
    out += _wrap(_INTRO, indent="  ")
    out.append("")
    for line in lines[1:]:
        out += _wrap(line, width=74, indent="    ")
    return out


def render_markdown(scoped: FilingScope,
                    *, heading="## Unbooked items in scope for the return") -> list:
    out = [heading, "", scoped.headline(), "", _INTRO, "",
           "| Partition | Items | Net | Months | Why |",
           "| --- | ---: | ---: | --- | --- |"]
    for name in PARTITIONS:
        row = scoped.part(name).as_row()
        out.append(f"| {row['partition']} | {row['items']} | {row['net']} | "
                   f"{row['months'] or '(none)'} | {row['why']} |")
    out.append("")
    for line in scoped.lines()[1:]:
        out.append(f"- {line}")
    out.append("")
    return out
