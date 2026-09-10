"""The three-way monthly tie-out, which is the point of the whole engine.

One month of one account ties when all three of these hold:

    opening + deposits - withdrawals == closing      (the chain identity)
    deposits    == the statement's own stated deposits total
    withdrawals == the statement's own stated withdrawals total

`deposits` and `withdrawals` are both POSITIVE magnitudes here, which is why
the identity subtracts rather than adds. The underlying `BankLine.amount` is
signed from the account holder's point of view (money in positive, money out
negative) for both a bank account and a card, so `deposits` is the sum of the
positive amounts and `withdrawals` is the absolute value of the sum of the
negative ones.

Two rules this module exists to enforce:

1. **A difference is reported as a number, never plugged.** There is no
   tolerance parameter, no rounding fudge and no "immaterial" branch. Amounts
   are Decimals of cents, so an equal comparison is exact and a tie is a tie.

2. **The chain identity is authoritative when the statement's own printed
   summary is corrupt.** This is not hypothetical. A real April statement
   extracted its "Total withdrawals" field as `-$8,1 8.4` because the PDF
   carried broken glyph widths, while its transaction list was complete and
   correct. Believing the summary field there would have condemned a month
   that was fine. When a stated total is missing or unreadable, the check it
   would have driven is recorded as unavailable in `note`, and the chain
   decides.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Optional

from .util import ZERO, fmt, money, month_key

__all__ = ["TieOutResult", "tie_out", "tie_out_month", "chain_breaks"]


@dataclass
class TieOutResult:
    """The answer for one account-month. Truthy only when everything ties."""

    month: str = ""
    opening: Decimal = ZERO
    deposits: Decimal = ZERO           # money in, positive magnitude
    withdrawals: Decimal = ZERO        # money out, positive magnitude
    computed_closing: Decimal = ZERO   # opening + deposits - withdrawals
    stated_closing: Optional[Decimal] = None
    difference: Decimal = ZERO         # computed_closing - stated_closing
    ties: bool = False
    rows: int = 0
    note: str = ""

    # Context and the other two legs of the three-way check. These carry no
    # judgement; they are here so a report can print the whole story.
    stated_deposits: Optional[Decimal] = None
    stated_withdrawals: Optional[Decimal] = None
    deposit_difference: Decimal = ZERO
    withdrawal_difference: Decimal = ZERO
    account_key: str = ""
    source_file: str = ""
    checks: list = field(default_factory=list)   # (name, available, ok, difference)

    def __bool__(self) -> bool:
        return bool(self.ties)

    @property
    def closing(self) -> Decimal:
        """The closing balance a downstream module should believe.

        The computed one. When the statement prints a closing balance that
        disagrees, `difference` is non-zero and `ties` is False; nothing here
        silently prefers the printed figure.
        """
        return self.computed_closing

    @property
    def net(self) -> Decimal:
        return self.deposits - self.withdrawals

    def as_row(self) -> dict:
        return {
            "account": self.account_key,
            "month": self.month,
            "rows": self.rows,
            "opening": str(self.opening),
            "deposits": str(self.deposits),
            "withdrawals": str(self.withdrawals),
            "computed_closing": str(self.computed_closing),
            "stated_closing": "" if self.stated_closing is None else str(self.stated_closing),
            "difference": str(self.difference),
            "ties": "yes" if self.ties else "NO",
            "note": self.note,
            "source_file": os.path.basename(self.source_file or ""),
        }

    def describe(self) -> str:
        head = (
            f"{self.account_key or 'account'} {self.month}: "
            f"{self.rows} rows, opening {fmt(self.opening)}, "
            f"deposits {fmt(self.deposits)}, withdrawals {fmt(self.withdrawals)}, "
            f"closing {fmt(self.computed_closing)}"
        )
        if self.ties:
            return head + "  TIES"
        return head + f"  DOES NOT TIE, difference {fmt(self.difference)}" + (
            f"  ({self.note})" if self.note else ""
        )

    def __str__(self) -> str:
        return self.describe()


def _totals(lines):
    deposits = sum((l.amount for l in lines if l.amount > ZERO), ZERO)
    withdrawals = -sum((l.amount for l in lines if l.amount < ZERO), ZERO)
    return money(deposits), money(withdrawals)


def tie_out_month(lines, opening, closing, month="", *, stated_deposits=None,
                  stated_withdrawals=None, account_key="", source_file="") -> TieOutResult:
    """Tie out one month.

    `lines` may be a whole account's history; when `month` is given ("YYYY-MM")
    only the lines in that month are counted. `closing` is the statement's own
    stated closing balance, or None when the statement does not print one or
    prints one that could not be read.
    """
    if month:
        lines = [l for l in lines if month_key(l.date) == month]
    elif lines:
        month = month_key(max(l.date for l in lines))

    opening = money(opening, "opening balance")
    deposits, withdrawals = _totals(lines)
    computed = money(opening + deposits - withdrawals)

    stated_closing = None if closing is None else money(closing, "stated closing")
    stated_dep = None if stated_deposits is None else money(stated_deposits, "stated deposits")
    stated_wd = None
    if stated_withdrawals is not None:
        # A statement may print its withdrawals total as a negative. The
        # magnitude is what the identity uses, so both renderings agree.
        stated_wd = abs(money(stated_withdrawals, "stated withdrawals"))

    difference = ZERO if stated_closing is None else money(computed - stated_closing)
    dep_diff = ZERO if stated_dep is None else money(deposits - stated_dep)
    wd_diff = ZERO if stated_wd is None else money(withdrawals - stated_wd)

    checks = [
        ("closing", stated_closing is not None, difference == ZERO, difference),
        ("deposits", stated_dep is not None, dep_diff == ZERO, dep_diff),
        ("withdrawals", stated_wd is not None, wd_diff == ZERO, wd_diff),
    ]

    notes = []
    for name, available, ok, diff in checks:
        if not available:
            # The statement did not give us this figure, or the figure it gave
            # could not be read. Say so; do not let it read as a pass.
            notes.append(f"stated {name} not available, chain identity used")
        elif not ok:
            notes.append(f"stated {name} differs by {fmt(diff)}")

    # Every available check must pass. An unavailable check is not a failure,
    # but it is always named in the note so nobody reads a partial tie-out as a
    # full one.
    ties = all(ok for _n, available, ok, _d in checks if available)
    if not any(available for _n, available, _ok, _d in checks):
        # Nothing at all to compare against: this is not a tie-out, it is an
        # arithmetic total. Refuse to call it a pass.
        ties = False
        notes.append("no stated figure to tie against")

    return TieOutResult(
        month=month,
        opening=opening,
        deposits=deposits,
        withdrawals=withdrawals,
        computed_closing=computed,
        stated_closing=stated_closing,
        difference=difference,
        ties=ties,
        rows=len(lines),
        note="; ".join(notes),
        stated_deposits=stated_dep,
        stated_withdrawals=stated_wd,
        deposit_difference=dep_diff,
        withdrawal_difference=wd_diff,
        account_key=account_key,
        source_file=source_file,
        checks=checks,
    )


def tie_out(statement) -> TieOutResult:
    """Tie out one parsed statement, using every line it holds.

    Deliberately does NOT filter by month. A card statement's cycle regularly
    straddles two calendar months (a June cycle whose autopay posts on July 1),
    and dropping the row that falls outside the calendar month would break the
    very chain we are testing. The result is LABELLED with the month the period
    ends in, but every line counts.

    Duck-typed on purpose: anything with `lines`, `opening_balance`,
    `closing_balance`, `stated_deposits`, `stated_withdrawals`, `account_key`,
    `source_file` and `period_end` works. That keeps this module free of any
    import from `statements`, so the parsers depend on the tie-out and not the
    other way around.
    """
    result = tie_out_month(
        list(getattr(statement, "lines", []) or []),
        getattr(statement, "opening_balance", ZERO) or ZERO,
        getattr(statement, "closing_balance", None),
        month="",                      # never filter; see the docstring
        stated_deposits=getattr(statement, "stated_deposits", None),
        stated_withdrawals=getattr(statement, "stated_withdrawals", None),
        account_key=getattr(statement, "account_key", "") or "",
        source_file=getattr(statement, "source_file", "") or "",
    )
    period_end = getattr(statement, "period_end", None)
    if period_end:
        result.month = month_key(period_end)
    return result


def _closing_of(statement):
    """The closing balance to chain from: the stated one, else the computed one."""
    stated = getattr(statement, "closing_balance", None)
    if stated is not None:
        return money(stated), "stated"
    result = getattr(statement, "tie_out", None) or tie_out(statement)
    return result.computed_closing, "computed"


def chain_breaks(statements) -> list:
    """Where one month's closing balance is not the next month's opening.

    A statement set can have every month tie against its own summary and still
    be wrong as a set: a missing month, a re-issued statement, or an account
    that was renumbered shows up here and nowhere else. Statements are grouped
    by `account_key` and ordered by period, and every consecutive pair is
    checked.

    Returns one dict per break. An empty list means the chain is continuous.
    """
    groups = {}
    unknown = []
    for s in statements:
        key = getattr(s, "account_key", "") or ""
        if not key:
            # A statement whose account we cannot name must not be chained.
            # Comparing a card's opening balance against a checking account's
            # closing balance manufactures a break out of nothing, and a reader
            # who sees forty false breaks stops reading the real one.
            unknown.append(s)
            continue
        groups.setdefault(key, []).append(s)

    if unknown and not groups:
        return [{
            "account_key": "",
            "problem": "no account could be identified",
            "detail": (
                f"{len(unknown)} statement(s) carry no account key, so no chain "
                f"can be checked. Parse them with an account_key, or let "
                f"statements.parse_many infer one from each file name."
            ),
            "statements": [getattr(s, "source_file", "") for s in unknown],
        }]

    breaks = []
    for account_key, group in sorted(groups.items()):
        ordered = sorted(
            group,
            key=lambda s: (
                getattr(s, "period_start", None) or getattr(s, "period_end", None) or _dt_min(),
                getattr(s, "source_file", "") or "",
            ),
        )
        for prior, nxt in zip(ordered, ordered[1:]):
            prior_closing, basis = _closing_of(prior)
            next_opening = money(getattr(nxt, "opening_balance", ZERO) or ZERO)
            difference = money(next_opening - prior_closing)
            if difference == ZERO:
                continue
            breaks.append({
                "account_key": account_key,
                "prior_month": _month_of(prior),
                "prior_closing": prior_closing,
                "prior_closing_basis": basis,
                "prior_source": os.path.basename(getattr(prior, "source_file", "") or ""),
                "next_month": _month_of(nxt),
                "next_opening": next_opening,
                "next_source": os.path.basename(getattr(nxt, "source_file", "") or ""),
                "difference": difference,
                "note": (
                    f"{account_key or 'account'}: {_month_of(prior)} closes at "
                    f"{fmt(prior_closing)} then {_month_of(nxt)} opens at "
                    f"{fmt(next_opening)}, a difference of {fmt(difference)}"
                ),
            })
    return breaks


def _month_of(statement) -> str:
    end = getattr(statement, "period_end", None) or getattr(statement, "period_start", None)
    return month_key(end) if end else "?"


def _dt_min():
    import datetime as _dt
    return _dt.date.min
