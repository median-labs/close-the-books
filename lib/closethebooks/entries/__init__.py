"""Adjusting-entry generators: what the engine drafts, and what it refuses to.

Nothing in this package posts anything. Every generator returns `Drafted`, a
list of `ProposedEntry` objects that becomes a CSV a human imports into their
own accounting system, after they have read it and approved the batch in their
own terminal. There is no write path from here to anybody's books.

    from closethebooks.entries import payroll, prepaid, stripe, reclass

    drafted = prepaid.build(profile, through="2026-06-30")
    drafted.check_all()                 # raises unless every entry is postable
    for row in drafted.remaining:       # what is left on the balance sheet
        print(row["vendor"], row["remaining"])

THE THREE RULES THIS PACKAGE ENFORCES

1. **Every entry names its basis.** `ProposedEntry.check()` refuses an entry
   with an empty `basis`, so a generator that cannot name the profile key, the
   document or the founder answer an entry rests on must refuse to draft it at
   all rather than draft it with a blank. Each module raises `DraftError` with
   the exact key that is missing, because "no basis" is useless to the person
   who has to fix it.

2. **A residual is reported, never plugged.** Where the inputs do not add up,
   the generator raises with both totals and their difference. The one thing it
   never does is post the difference somewhere convenient to make the entry
   balance. A plugged entry balances and is wrong, and nothing downstream can
   see it.

3. **Where the answer depends on a document nobody holds, the output is a
   QUESTION, not an entry.** `Drafted.questions` is a first class result. An
   empty entry list with three questions in it is a successful run.
"""

from __future__ import annotations

import calendar
import datetime as _dt
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal

from ..model import ProposedEntry
from ..util import CENTS, ZERO, fmt, money, month_key, month_range, parse_date

__all__ = [
    "DraftError", "Question", "Drafted",
    "add_months", "month_end", "last_day", "straight_line_schedule",
    "section", "need", "role_account", "basis_of", "signed_move",
]


class DraftError(ValueError):
    """A generator was asked to draft something it cannot draft honestly.

    Always names the profile key, the period or the field that is missing, and
    where a residual is involved, prints both totals and the difference. The
    caller has to be able to act on the message without reading this source.
    """


# ------------------------------------------------------------------ results

@dataclass
class Question:
    """Something a human has to answer before an entry can exist.

    `needs` is the specific document or fact that would settle it. A question
    that does not say what would answer it just moves the problem.
    """

    id: str
    text: str
    needs: str = ""
    blocks: str = ""
    kind: str = ""
    source: str = ""

    def describe(self) -> str:
        out = f"QUESTION {self.id}: {self.text}"
        if self.needs:
            out += f"\n    settled by: {self.needs}"
        if self.blocks:
            out += f"\n    blocks: {self.blocks}"
        return out

    def as_row(self) -> dict:
        return {
            "id": self.id, "question": self.text, "needs": self.needs,
            "blocks": self.blocks, "kind": self.kind, "source": self.source,
        }


class Drafted(list):
    """The entries a generator produced, plus everything else it learned.

    It IS a list of `ProposedEntry`, so a caller that only wants the entries can
    treat it as one and the declared return type stays honest. The extras hang
    off it because they are the parts a report must show and a naive caller
    would otherwise drop:

      `questions`  what could not be drafted, and what would settle it
      `residuals`  numbers that must return to zero and did not
      `remaining`  balances the entries leave behind, to tie to the balance sheet
      `notes`      anything a reader of the batch needs to know
    """

    def __init__(self, entries=(), *, kind="", questions=(), residuals=(),
                 remaining=(), notes=()):
        super().__init__(entries)
        self.kind = kind
        self.questions = list(questions)
        self.residuals = list(residuals)
        self.remaining = list(remaining)
        self.notes = list(notes)

    # ------------------------------------------------------------- checks
    def check_all(self):
        """Raise unless every entry is postable. Returns self so it can chain."""
        for entry in self:
            entry.check()
        return self

    @property
    def total_debits(self) -> Decimal:
        return money(sum((e.total_debits() for e in self), ZERO))

    @property
    def balanced(self) -> bool:
        return all(e.balanced for e in self)

    def by_month(self) -> dict:
        out = {}
        for entry in self:
            out.setdefault(month_key(entry.date), []).append(entry)
        return dict(sorted(out.items()))

    # ------------------------------------------------------------- report
    def describe(self) -> str:
        head = (
            f"{self.kind or 'entries'}: {len(self)} drafted, "
            f"{len(self.questions)} question(s), total debits {fmt(self.total_debits)}"
        )
        parts = [head]
        for note in self.notes:
            parts.append(f"  note: {note}")
        for q in self.questions:
            parts.append("  " + q.describe().replace("\n", "\n  "))
        return "\n".join(parts)

    def __str__(self) -> str:
        return self.describe()


# -------------------------------------------------------------------- dates

def add_months(d, n: int):
    """The same day-of-month n months on, clamped to the end of a short month.

    31 January plus one month is 28 February, not 3 March. A naive
    `timedelta(days=30)` walk drifts, and after twelve months of a prepaid
    schedule it has drifted far enough to put an entry in the wrong period.
    """
    d = parse_date(d, field="date")
    total = d.month - 1 + int(n)
    year = d.year + total // 12
    month = total % 12 + 1
    return _dt.date(year, month, min(d.day, calendar.monthrange(year, month)[1]))


def last_day(year: int, month: int):
    return _dt.date(year, month, calendar.monthrange(year, month)[1])


def month_end(d):
    d = parse_date(d, field="date")
    return last_day(d.year, d.month)


def _days_in(month: str) -> int:
    y, m = int(month[:4]), int(month[5:7])
    return calendar.monthrange(y, m)[1]


def straight_line_schedule(total, start, term_months: int, monthly=None) -> list:
    """The full life of one straight-line amortization, month by month.

    Returns one dict per calendar month the asset is in service:
    `{"month", "date", "amount", "days", "days_in_month", "partial", "index"}`.

    Two behaviours that exist because both cases are ordinary and both are
    routinely got wrong:

    **A contract that starts mid-month** gets a first month prorated by the days
    actually in service, and therefore a stub month at the far end. Charging a
    full month on a 15 March start overstates March and leaves the contract
    fully amortized a month before it expires.

    **Rounding never leaves a residual.** A 10,000.00 contract over 3 months is
    3,333.33 a month, and three of those is 9,999.99. The final month of the
    schedule is written as `total - everything already scheduled`, so the sum of
    the schedule is exactly `total` and the prepaid account reaches 0.00 rather
    than 0.01. The 0.01 is the kind of balance that sits on a balance sheet for
    four years because it is too small to investigate and too visible to ignore.
    """
    total = money(total, "amortization total")
    term_months = int(term_months)
    if term_months <= 0:
        raise DraftError(f"term_months must be positive, got {term_months}")
    if total <= ZERO:
        raise DraftError(f"amortization total must be positive, got {fmt(total)}")

    start = parse_date(start, field="amortization start")
    end = add_months(start, term_months) - _dt.timedelta(days=1)

    if monthly is None:
        monthly = (total / Decimal(term_months)).quantize(CENTS, rounding=ROUND_HALF_UP)
    else:
        monthly = money(monthly, "monthly amortization")

    rows = []
    for i, mk in enumerate(month_range(start, end)):
        dim = _days_in(mk)
        y, m = int(mk[:4]), int(mk[5:7])
        first, last = _dt.date(y, m, 1), last_day(y, m)
        in_service_from = max(first, start)
        in_service_to = min(last, end)
        days = (in_service_to - in_service_from).days + 1
        partial = days != dim
        amount = monthly if not partial else money(
            (monthly * Decimal(days) / Decimal(dim)).quantize(CENTS, rounding=ROUND_HALF_UP)
        )
        rows.append({
            "index": i + 1, "month": mk, "date": last,
            "amount": amount, "days": days, "days_in_month": dim,
            "partial": partial,
        })

    if rows:
        booked = money(sum((r["amount"] for r in rows[:-1]), ZERO))
        rows[-1]["amount"] = money(total - booked)
        rows[-1]["remainder_adjusted"] = True
    return rows


# ------------------------------------------------------------------ profile

def section(profile, name: str, *, required=True):
    """One block of `profile.recurring_entries`, or a message saying it is absent."""
    data = (profile.recurring_entries or {}).get(name)
    if data in (None, [], {}, ""):
        if required:
            raise DraftError(
                f"profile has no recurring_entries.{name}. Nothing to draft. "
                f"Add the block, or do not run this generator."
            )
        return None
    return data


def need(mapping, key: str, where: str):
    """A required field, or a refusal that names the field and where it belongs."""
    if not isinstance(mapping, dict):
        raise DraftError(f"{where}: expected an object with a {key!r} field, got {type(mapping).__name__}")
    value = mapping.get(key)
    if value in (None, "", [], {}):
        raise DraftError(f"{where}: {key!r} is missing. It has no default; the engine will not guess it.")
    return value


def role_account(profile, role: str, where: str = "", *, required=True):
    """The chart account carrying a role, by key (number where numbered).

    No rule in this engine may key off an account number, because every chart
    numbers differently. Roles are the vocabulary; this is the lookup.
    """
    hits = [row for row in (profile.chart or []) if (row.get("role") or "other") == role]
    if not hits:
        if required:
            raise DraftError(
                f"{where or 'this entry'} needs an account with role {role!r} and the "
                f"profile chart declares none. Set the role on the right account."
            )
        return ""
    if len(hits) > 1 and required:
        labels = ", ".join(str(h.get("number") or h.get("full_name") or h.get("name")) for h in hits)
        raise DraftError(
            f"{where or 'this entry'} needs THE account with role {role!r} and the chart "
            f"declares {len(hits)}: {labels}. Name the one you mean explicitly."
        )
    row = hits[0]
    return str(row.get("number") or row.get("full_name") or row.get("name") or "").strip()


def basis_of(item, fallback, where: str) -> str:
    """The sentence an entry rests on, or a refusal.

    Checked here as well as in `ProposedEntry.check()` so the message names the
    profile key rather than an entry number the caller has never seen.
    """
    text = str((item or {}).get("source") or (item or {}).get("basis") or "").strip()
    if not text:
        text = str(fallback or "").strip()
    if not text:
        raise DraftError(
            f"{where}: no source. Every entry names the profile key, the document or "
            f"the founder answer it rests on; add a \"source\" field."
        )
    return text


def signed_move(from_account: str, to_account: str, signed_balance, memo="", klass="", name=""):
    """The two lines that move a SIGNED balance out of one account into another.

    `signed_balance` is debit-positive, the engine's convention everywhere: an
    asset or expense balance is positive, a liability, equity or revenue balance
    is negative. So moving a convertible instrument out of equity (a credit
    balance, negative) debits equity and credits the liability, and moving a
    misfiled asset debits the destination and credits the source. Callers pass
    the balance as it reads on the trial balance and do not have to work out the
    sides themselves, which is where this normally goes wrong.
    """
    amount = money(signed_balance, "balance to move")
    if amount == ZERO:
        raise DraftError(
            f"move from {from_account!r} to {to_account!r} is for 0.00. An entry that "
            f"moves nothing is noise in the audit trail; drop it instead."
        )
    if str(from_account).strip() == str(to_account).strip():
        raise DraftError(f"move from {from_account!r} to itself is not a move")
    if amount > ZERO:                      # a debit balance leaving
        return [
            (to_account, amount, ZERO, memo, name, klass),
            (from_account, ZERO, amount, memo, name, klass),
        ]
    return [                               # a credit balance leaving
        (from_account, -amount, ZERO, memo, name, klass),
        (to_account, ZERO, -amount, memo, name, klass),
    ]


def _entry(date, number, lines, memo, kind, basis, batch_tag) -> ProposedEntry:
    entry = ProposedEntry(
        date=parse_date(date, field=f"entry {number} date"),
        number=number,
        lines=list(lines),
        memo=memo,
        kind=kind,
        basis=basis,
        batch_tag=batch_tag,
    )
    entry.check()
    return entry
