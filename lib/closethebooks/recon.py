"""The reconciliation report: book against statement, one row per account-month.

    from closethebooks.recon import reconcile
    report = reconcile(ledger, statements, profile,
                       period_start="2026-01-01", period_end="2026-06-30")
    print(report.render_markdown())
    report.unreconciled      # differences that are not zero
    report.unchecked         # account-months with no statement to check against

TWO RULES, AND THEY ARE THE WHOLE POINT

**A difference is stated even when it is zero.** A report that prints only the
rows with a problem is indistinguishable from a report where nobody looked. The
zeros are the evidence that the account was checked, so every account-month in
the period gets a row whether or not anything is wrong with it, and the balance
sheet accounts nobody thinks about get one too.

**Where there is no statement, the cell does not read 0.00.** It reads
"cannot check, because no statement for this account-month". Zero is a finding.
Absence is a different finding, with a different fix (get the statement), and
collapsing the two is how an account goes four months unexamined while a table
of zeros says it was fine. `unreconciled` and `unchecked` are separate lists for
the same reason.

**A BOOK BALANCE IS AN OPENING POSITION PLUS THE MOVEMENT SINCE IT.** The Book
column is `ledger.opening_of(account)` plus every line posted on or before the
month end, never the lines alone. Summing lines alone gives period movement, and
on a real export that reported every account short by its whole opening
position, including an equity balance in the millions that read as 0.00. Where no export states the opening position
the cell reads "cannot be determined" and the row is not checkable, for the same
reason a missing statement does not read 0.00.

SIGNS

Book balances are debit-positive, the engine's convention: a bank account is
positive, a credit card is negative. Statement balances are signed from the
account holder's point of view by every parser in `statements/`: a bank balance
is positive and an amount OWED on a card is negative. The two conventions agree
on every balance-sheet account, so `difference = book - statement` needs no
per-account-type special case. That is not a coincidence; it is why both
conventions were chosen this way.

WHAT A READER SEES IS NOT WHAT THE ARITHMETIC HOLDS

The arithmetic above is unchanged, and `row.book` and `row.statement` are still
the signed Decimals it works in. `book_cell()` and `statement_cell()` render
them on the ACCOUNT'S OWN SIDE, because a credit balance of 3,118,447.25 in an
equity account printed as `-3,118,447.25` reads as a broken tool rather than as
a liability. See `sides.py`. The DIFFERENCE column keeps its sign: a difference
is not a balance and its sign says which way the two figures disagree.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Optional

from .model import is_real_account
from .sides import balance_text, normal_side, on_wrong_side
from .util import ZERO, fmt, iso, money, month_key, month_range, norm_text, parse_date

__all__ = ["ReconRow", "ReconReport", "reconcile", "CANNOT_CHECK", "CANNOT_DETERMINE"]

CANNOT_CHECK = "cannot check, because no statement for this account-month"
CANNOT_DETERMINE = "cannot be determined, because no export states this account's opening balance"


@dataclass
class ReconRow:
    """One account, one month. `difference` is None only when nothing to check against."""

    account: str = ""                     # the label a human reads
    account_key: str = ""
    month: str = ""
    role: str = "other"
    account_type: str = ""
    # "Dr" or "Cr": the side that INCREASES this account, so a reader can be
    # shown the balance on its own side instead of debit-positive. "" means the
    # account type is not one the engine knows, and then no side is claimed.
    normal_side: str = ""
    # None means the book balance could not be determined, which is a different
    # answer from 0.00 and from "no statement". It happens when no export states
    # the account's opening position, so the ledger holds movement and nothing
    # that movement can be added to.
    book: Optional[Decimal] = ZERO
    statement: Optional[Decimal] = None
    difference: Optional[Decimal] = None
    checkable: bool = False
    reason: str = CANNOT_CHECK
    statement_basis: str = ""             # "stated" | "computed from the lines"
    source_file: str = ""
    externally_verifiable: bool = False   # the profile declares a statement source
    rows_in_month: int = 0
    note: str = ""

    @property
    def ties(self) -> bool:
        return self.checkable and self.difference == ZERO

    def difference_cell(self) -> str:
        """What the report prints. Never a bare 0.00 standing in for 'no idea'."""
        return fmt(self.difference) if self.checkable else self.reason

    @property
    def book_on_wrong_side(self) -> bool:
        """The book balance sits opposite this account's normal side."""
        return self.book is not None and on_wrong_side(self.book, self.normal_side)

    def book_cell(self) -> str:
        """The book balance on its own side, or why there is not one.

        Never a stand-in zero and never a negative: `3,118,447.25 Cr`, which is
        how the exported trial balance prints the same figure.
        """
        return balance_text(self.book, self.normal_side, undetermined=CANNOT_DETERMINE)

    def statement_cell(self) -> str:
        """The statement balance on the same side. Blank when there is none."""
        return "" if self.statement is None else balance_text(self.statement,
                                                              self.normal_side)

    def as_row(self) -> dict:
        return {
            "account": self.account,
            "account_key": self.account_key,
            "month": self.month,
            "book": self.book_cell(),
            "statement": self.statement_cell(),
            "difference": self.difference_cell(),
            "checkable": "yes" if self.checkable else "no",
            "basis": self.statement_basis,
            "source_file": self.source_file,
            "rows": self.rows_in_month,
            "note": self.note,
        }


@dataclass
class ReconReport:
    company: str = ""
    period_start: object = None
    period_end: object = None
    months: list = field(default_factory=list)
    rows: list = field(default_factory=list)
    notes: list = field(default_factory=list)

    # ------------------------------------------------------------- slices
    @property
    def unreconciled(self) -> list:
        """Rows with a real, non-zero difference. A problem with a number on it."""
        return [r for r in self.rows if r.checkable and r.difference != ZERO]

    @property
    def unchecked(self) -> list:
        """Rows with no statement. A different problem: nobody could look."""
        return [r for r in self.rows if not r.checkable]

    @property
    def unchecked_but_expected(self) -> list:
        """The unchecked rows for accounts the profile says we hold statements for.

        An equity account has no statement and never will. A bank account with no
        statement for March is a missing document, and that is the list somebody
        has to work.
        """
        return [r for r in self.unchecked if r.externally_verifiable]

    @property
    def reconciled(self) -> list:
        return [r for r in self.rows if r.ties]

    @property
    def accounts(self) -> list:
        seen = []
        for r in self.rows:
            if r.account_key not in seen:
                seen.append(r.account_key)
        return seen

    def row(self, account_key, month):
        for r in self.rows:
            if r.account_key == account_key and r.month == month:
                return r
        return None

    def cells_stated(self):
        """(stated, total). Stated means a number, not an explanation."""
        return sum(1 for r in self.rows if r.checkable), len(self.rows)

    # ------------------------------------------------------------- output
    def render_markdown(self) -> str:
        stated, total = self.cells_stated()
        head = [
            f"# Reconciliation, {self.company or 'the company'}",
            "",
            f"Period {iso(self.period_start)} to {iso(self.period_end)}. "
            f"{len(self.accounts)} balance-carrying account(s) over {len(self.months)} month(s): "
            f"{total} account-month cells, {stated} with a stated difference, "
            f"{total - stated} that cannot be checked.",
            "",
            f"{len(self.reconciled)} cell(s) tie at 0.00. "
            f"{len(self.unreconciled)} do not. "
            f"{len(self.unchecked_but_expected)} are missing a statement we should hold.",
            "",
            "Book and Statement are shown on each account's own side, `Dr` or `Cr`, "
            "the way the trial balance prints them. Difference keeps its sign, because "
            "it is not a balance and the sign says which way the two disagree.",
            "",
            "| Account | Month | Book | Statement | Difference | Source |",
            "| --- | --- | ---: | ---: | ---: | --- |",
        ]
        for r in self.rows:
            head.append(
                f"| {r.account} | {r.month} | {r.book_cell()} | {r.statement_cell()} | "
                f"{r.difference_cell()} | {r.source_file or ''} |"
            )
        if self.unreconciled:
            head += ["", "## Differences that are not zero", ""]
            for r in self.unreconciled:
                head.append(
                    f"- {r.account}, {r.month}: book {r.book_cell()} against statement "
                    f"{r.statement_cell()}, difference {fmt(r.difference)}. This is reported as a "
                    f"number and is not plugged."
                )
        if self.unchecked_but_expected:
            head += ["", "## Statements we do not have", ""]
            for r in self.unchecked_but_expected:
                head.append(f"- {r.account}, {r.month}: {r.reason}. Book balance reads {r.book_cell()}.")
        for note in self.notes:
            head += ["", note]
        return "\n".join(head) + "\n"

    def evidence_rows(self) -> list:
        """Shaped for `evidence.Evidence.reconciliation()`, including the zeros."""
        out = []
        for r in self.rows:
            if not r.checkable:
                continue
            out.append({
                "account": f"{r.account} {r.month}",
                "book": r.book,
                "statement": r.statement,
                "difference": r.difference,
                "evidence": r.source_file or r.statement_basis,
                # What the reader will actually see, so the evidence ledger
                # records the rendered figure beside the signed one rather than
                # leaving a reviewer to work out which side it was printed on.
                "presented": f"book {r.book_cell()}, statement {r.statement_cell()}",
            })
        return out

    def __bool__(self) -> bool:
        return not self.unreconciled and not self.unchecked_but_expected


# ------------------------------------------------------------------ helpers

def _aliases(account) -> set:
    """Every way a STATEMENT FILE might name this account.

    Loose on purpose: a downloaded statement is named by a human or a bank and
    the leaf name is the commonest spelling. Ledger lines are matched with
    `_line_aliases` instead, which is strict, because there the leaf name is
    ambiguous and a wrong match silently moves one account's money to another.
    """
    out = set()
    for value in (getattr(account, "key", ""), account.name, account.full_name,
                  account.number, account.label()):
        text = norm_text(value)
        if text:
            out.add(text)
    return out


def _line_aliases(ledger, account) -> set:
    """Every UNAMBIGUOUS way a ledger line might name this account.

    `Ledger.aliases_for` drops a leaf name that more than one account answers
    to. Without that, `Credit Cards:Brex Card` picked up the postings of
    QuickBooks' unnumbered default `Brex card` and reported a book balance
    15,803.42 away from the trial balance.
    """
    getter = getattr(ledger, "aliases_for", None)
    key = getattr(account, "key", "") or account.full_name
    if getter is None:
        return {norm_text(v) for v in (key, account.number, account.full_name) if v}
    return getter(key)


def _spec_aliases(spec) -> set:
    out = set()
    for value in (spec.book, spec.label, spec.mask, spec.institution):
        text = norm_text(value)
        if text:
            out.add(text)
    if spec.institution and spec.mask:
        out.add(norm_text(f"{spec.institution}-{spec.mask}"))
        out.add(norm_text(f"{spec.institution} {spec.mask}"))
    return out


def _statement_balance(statement):
    """(value, basis). The statement's own closing balance where it printed one.

    Falls back to the chain identity, exactly as the tie-out does, and says which
    it used. A computed closing is a weaker check than a printed one and the
    report has to show which one it is looking at.
    """
    stated = getattr(statement, "closing_balance", None)
    if stated is not None:
        return money(stated), "stated on the statement"
    opening = money(getattr(statement, "opening_balance", ZERO) or ZERO)
    net = money(sum((l.amount for l in getattr(statement, "lines", []) or []), ZERO))
    return money(opening + net), "computed from the statement lines (it printed no closing balance)"


def reconcile(ledger, statements, profile, *, period_start, period_end,
              include_inactive=False) -> ReconReport:
    """One row per balance-carrying account per month, differences always stated."""
    period_start = parse_date(period_start, field="period_start")
    period_end = parse_date(period_end, field="period_end")
    if period_end < period_start:
        raise ValueError(f"period_end {period_end} is before period_start {period_start}")
    months = month_range(period_start, period_end)

    # ---- statements, indexed by (account alias, month)
    by_alias_month, duplicates = {}, []
    for statement in statements or []:
        key = norm_text(getattr(statement, "account_key", "") or "")
        month = getattr(statement, "month", "") or ""
        if not month:
            continue
        slot = (key, month)
        if slot in by_alias_month:
            duplicates.append(f"{key or '(no account key)'} {month}")
            existing = by_alias_month[slot]
            newer = max(
                (existing, statement),
                key=lambda s: (getattr(s, "period_end", None) or period_start, getattr(s, "source_file", "")),
            )
            by_alias_month[slot] = newer
            continue
        by_alias_month[slot] = statement

    # ---- which chart accounts the profile expects statements for
    spec_alias_to_book = {}
    for spec in getattr(profile, "accounts", []) or []:
        for alias in _spec_aliases(spec):
            spec_alias_to_book[alias] = spec

    # ---- ledger lines, indexed by normalised account label
    lines_by_account = {}
    for line in getattr(ledger, "lines", []) or []:
        for label in (line.account, getattr(line, "account_full", "")):
            text = norm_text(label)
            if text:
                lines_by_account.setdefault(text, []).append(line)

    company = getattr(ledger, "company", "") or getattr(getattr(profile, "entity", None), "name", "")
    rows, notes = [], []
    if duplicates:
        notes.append(
            "More than one statement covered the same account-month, so the later one was used: "
            + ", ".join(sorted(set(duplicates)))
            + ". A re-issued statement is normal; a second DIFFERENT statement is not."
        )

    # WHICH ACCOUNTS GET A ROW AT ALL
    #
    # Balance-sheet accounts, minus the chart rows that are not accounts anybody
    # can reconcile: parent rollups whose children already carry the balance,
    # and template rows that have never held anything. See `is_real_account`.
    # Reconciling a rollup counts its children twice; asking for a statement for
    # `227000 Credit Card 7` asks for a document that does not exist. On one
    # real chart that filter removed 17 of 21 accounts, and it was the noise in
    # the other 17 that made the 4 real ones unworkable.
    excluded_rollups, excluded_unused = [], []
    accounts = []
    for a in (getattr(ledger, "accounts", {}) or {}).values():
        if not a.is_balance_sheet:
            continue
        label = a.label() or a.full_name
        if hasattr(ledger, "is_rollup") and ledger.is_rollup(a):
            excluded_rollups.append(label)
            continue
        if not include_inactive and not is_real_account(ledger, a):
            excluded_unused.append(label)
            continue
        accounts.append(a)

    if excluded_rollups:
        notes.append(
            f"{len(excluded_rollups)} parent account(s) are totals of their children and are "
            f"not reconciled separately, because the children already carry the balance and "
            f"no institution issues a statement for a subtotal: "
            + ", ".join(sorted(excluded_rollups)[:10])
            + (" ..." if len(excluded_rollups) > 10 else "") + "."
        )
    if excluded_unused:
        notes.append(
            f"{len(excluded_unused)} account(s) have never been posted to and carry no balance, "
            f"so they are not open items and are not reconciled: "
            + ", ".join(sorted(excluded_unused)[:10])
            + (" ..." if len(excluded_unused) > 10 else "")
            + ". Pass include_inactive=True to see them anyway."
        )

    opening_known = bool(getattr(ledger, "opening_basis", ""))
    if not opening_known:
        notes.append(
            "No export states an opening balance, so every book balance below reads "
            "\"cannot be determined\" rather than a figure. The posted lines are movement, "
            "not a balance, and printing movement in a Book column is how an account whose "
            "whole balance came in from the prior year reconciles to nothing."
        )

    for account in sorted(accounts, key=lambda a: (a.number or "", a.full_name)):
        aliases = _aliases(account)
        account_lines = []
        seen_ids = set()
        for alias in _line_aliases(ledger, account):
            for line in lines_by_account.get(alias, []):
                marker = id(line)
                if marker not in seen_ids:
                    seen_ids.add(marker)
                    account_lines.append(line)

        # The opening position at `period_start`, which is most of the balance
        # for any account that carried something in. Summing lines alone
        # reported a 3,118,447.25 liability as 0.00 on a real export.
        opening = (ledger.opening_of(getattr(account, "key", account.full_name))
                   if opening_known and hasattr(ledger, "opening_of") else None)

        spec = None
        for alias in aliases:
            if alias in spec_alias_to_book:
                spec = spec_alias_to_book[alias]
                break

        for month in months:
            cutoff = _month_end(month)
            if opening is None:
                book = None
            else:
                book = money(opening + sum((l.signed for l in account_lines if l.date <= cutoff), ZERO))
            rows_in_month = sum(1 for l in account_lines if month_key(l.date) == month)

            statement = None
            for alias in aliases | (_spec_aliases(spec) if spec else set()):
                statement = by_alias_month.get((alias, month))
                if statement is not None:
                    break

            if statement is None or book is None:
                rows.append(ReconRow(
                    account=account.label() or account.full_name,
                    account_key=getattr(account, "key", account.full_name),
                    month=month, role=account.role, account_type=account.type,
                    normal_side=normal_side(account),
                    book=book, statement=None, difference=None, checkable=False,
                    reason=CANNOT_CHECK if book is not None else CANNOT_DETERMINE,
                    rows_in_month=rows_in_month,
                    externally_verifiable=spec is not None,
                ))
                continue

            value, basis = _statement_balance(statement)
            rows.append(ReconRow(
                account=account.label() or account.full_name,
                account_key=getattr(account, "key", account.full_name),
                month=month, role=account.role, account_type=account.type,
                normal_side=normal_side(account),
                book=book, statement=value, difference=money(book - value),
                checkable=True, reason="", statement_basis=basis,
                source_file=_basename(getattr(statement, "source_file", "") or ""),
                rows_in_month=rows_in_month, externally_verifiable=True,
            ))

    return ReconReport(
        company=company,
        period_start=period_start,
        period_end=period_end,
        months=months,
        rows=rows,
        notes=notes,
    )


def _month_end(month: str):
    import calendar
    import datetime as _dt
    y, m = int(month[:4]), int(month[5:7])
    return _dt.date(y, m, calendar.monthrange(y, m)[1])


def _basename(path: str) -> str:
    import os
    return os.path.basename(path) if path else ""
