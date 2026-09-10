"""Exit tests: did the work actually finish.

    from closethebooks.exit_tests import run, Attestation
    report = run(ledger, statements, profile, recon,
                 attestation=Attestation(statement="I have no unbooked receipts",
                                         by="A. Founder", on="2026-07-02",
                                         unbooked_count=0),
                 entries=drafted)
    print(report.render_markdown())
    report.failed        # what is not done, with the measured numbers

WHY EVERY TEST REPORTS A NUMBER

A tick is not evidence. "Trial balance: PASS" and "Trial balance: PASS, debits
1,284,220.19 against credits 1,284,220.19, difference 0.00" cost the same to
print and only one of them can be checked by the person reading it. So an
`ExitTest` carries `measured`, always, on a pass as well as on a failure, and
`render_markdown` prints the numbers in the table rather than a symbol.

WHY A FAILURE NAMES THE REMEDY

The report is read by whoever has to fix it, often months later. "Test 4 failed"
sends them back into this source; "clearing account 1250 holds 1,700.00, find
the payout it belongs to" does not.

WHAT COUNTS AS A PASS

Only a check that actually ran and found what it was looking for. A check that
could not run says so and FAILS, in every case where not running it would leave
a real risk unexamined. Test 10 is the clearest example: with no declared
inception date, half of it cannot run, and it fails rather than reporting a pass
on the half that did. This is the same rule the tie-out uses when a statement
prints no total to tie against.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Optional

from .model import is_real_account as _is_real_account
from .sides import balance_text as _balance_on_its_side
from .sides import render_markdown as _render_sides_markdown
from .sides import scan as _scan_sides
from .sides import side_word as _side_word
from .util import ZERO, fmt, iso, money, month_range, norm_text, parse_date, try_date

__all__ = ["ExitTest", "ExitTestReport", "Attestation", "run", "TESTS"]

CANNOT_DETERMINE = "cannot be determined"

TESTS = (
    (1, "The exported trial balance foots and ties to the ledger"),
    (2, "Every balance-carrying account has a stated difference for every month"),
    (3, "Opening balance equity is 0.00"),
    (4, "Every clearing and suspense account is 0.00"),
    (5, "No account carries an impossible balance"),
    (6, "Every month has activity or is declared dormant"),
    (7, "The class column is complete where classes are in use"),
    (8, "Every adjusting entry carries a basis"),
    (9, "Unbooked items are zero, by founder attestation"),
    (10, "No transaction is dated outside the company's own life"),
)

_CLEARING_NAME = re.compile(
    r"\b(clearing|suspense|undeposited funds|ask my accountant|uncategori[sz]ed|to be (re)?classified)\b",
    re.I,
)
_OBE_NAME = re.compile(r"\bopening balance equity\b", re.I)
_MISSING_CLASS = {"", "not specified", "unspecified", "none", "n/a", "na", "-"}


# ------------------------------------------------------------------- results

@dataclass
class ExitTest:
    number: int
    name: str
    passed: bool
    measured: dict = field(default_factory=dict)
    detail: str = ""
    remedy: str = ""
    # key -> how that measured value reads to a PERSON, where the two differ.
    # `measured` holds the engine's own debit-positive Decimals, which is what a
    # caller asserts against and what the arithmetic works in. A balance is read
    # on its account's own side, so `3,118,447.25 Cr` rather than
    # `(3,118,447.25)`, and that string lives here. Nothing is duplicated that
    # renders the same both ways.
    presented: dict = field(default_factory=dict)

    def measured_text(self) -> str:
        parts = []
        for key, value in self.measured.items():
            shown = self.presented.get(key)
            parts.append(f"{key} {shown if shown is not None else _render(value)}")
        return "; ".join(parts)

    def describe(self) -> str:
        head = f"{self.number}. {self.name}: {'PASS' if self.passed else 'FAIL'}"
        if self.measured:
            head += f"  [{self.measured_text()}]"
        if self.detail:
            head += f"\n    {self.detail}"
        if not self.passed and self.remedy:
            head += f"\n    do: {self.remedy}"
        return head

    def as_row(self) -> dict:
        return {
            "number": self.number, "test": self.name,
            "result": "pass" if self.passed else "FAIL",
            "measured": self.measured_text(), "detail": self.detail,
            "remedy": "" if self.passed else self.remedy,
        }

    def __bool__(self) -> bool:
        return bool(self.passed)


@dataclass
class ExitTestReport:
    company: str = ""
    period_start: object = None
    period_end: object = None
    tests: list = field(default_factory=list)
    # Every account whose balance sits on the side opposite its normal one, as
    # a `sides.SideScan`. It is carried on the report rather than left inside
    # test 5 because it is a finding a reader acts on directly, and because the
    # handoff document and the terminal both render it without rescanning.
    wrong_side: object = None

    @property
    def passed(self) -> list:
        return [t for t in self.tests if t.passed]

    @property
    def failed(self) -> list:
        return [t for t in self.tests if not t.passed]

    def get(self, number: int) -> Optional[ExitTest]:
        for t in self.tests:
            if t.number == number:
                return t
        return None

    def __bool__(self) -> bool:
        return not self.failed

    def render_markdown(self) -> str:
        out = [
            f"# Exit tests, {self.company or 'the company'}",
            "",
            f"Period {iso(self.period_start)} to {iso(self.period_end)}. "
            f"{len(self.passed)} of {len(self.tests)} pass.",
            "",
            "Balances are shown on each account's own side, `Dr` or `Cr`, the way the "
            "trial balance prints them. A balance is never negative here; a balance on "
            "the side opposite its account's normal one is a finding, and the findings "
            "are listed at the end.",
            "",
            "| # | Test | Result | Measured | What to do |",
            "| ---: | --- | --- | --- | --- |",
        ]
        for t in self.tests:
            remedy = "" if t.passed else t.remedy
            out.append(
                f"| {t.number} | {t.name} | {'pass' if t.passed else 'FAIL'} | "
                f"{t.measured_text() or '(nothing measured)'} | {remedy} |"
            )
        if self.failed:
            out += ["", "## What is not done", ""]
            for t in self.failed:
                out.append(f"**{t.number}. {t.name}**  ")
                out.append(f"Measured: {t.measured_text() or '(nothing measured)'}  ")
                if t.detail:
                    out.append(f"{t.detail}  ")
                out.append(f"Do: {t.remedy}")
                out.append("")
        else:
            out += ["", "Every exit test passes. The numbers above are the evidence; "
                        "read them rather than the word pass.", ""]
        if self.wrong_side is not None:
            out += [""] + _render_sides_markdown(self.wrong_side)
        return "\n".join(out) + "\n"

    def as_rows(self) -> list:
        return [t.as_row() for t in self.tests]


def _render(value) -> str:
    if isinstance(value, Decimal):
        return fmt(value)
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, (list, tuple)):
        return ", ".join(str(v) for v in value) if value else "none"
    if value is None:
        return "not stated"
    return str(value)


# --------------------------------------------------------------- attestation

@dataclass
class Attestation:
    """A founder's typed statement that nothing is sitting unbooked.

    No export proves this. A shoebox of receipts, an unforwarded invoice and a
    personal card used for a business expense are all invisible to every system
    the engine can read, so the only evidence available is somebody saying so, on
    a date, in their own words. That is worth having precisely because it is
    attributable: it names who said it and when, which a silent assumption does
    not.

    `unbooked_count` must be stated. A blank is not zero.
    """

    statement: str = ""
    by: str = ""
    on: str = ""
    unbooked_count: Optional[int] = None
    screenshot: str = ""
    note: str = ""

    @classmethod
    def of(cls, value):
        if value is None or isinstance(value, cls):
            return value
        if isinstance(value, dict):
            return cls(**{k: v for k, v in value.items() if k in cls.__annotations__})
        raise TypeError(f"attestation must be an Attestation, a dict or None, got {type(value).__name__}")

    def problems(self) -> list:
        out = []
        if not str(self.statement or "").strip():
            out.append("no typed statement")
        if not str(self.by or "").strip():
            out.append("nobody is named as saying it")
        if not str(self.on or "").strip():
            out.append("no date")
        elif try_date(self.on) is None:
            out.append(f"the date {self.on!r} cannot be read")
        if self.unbooked_count is None:
            out.append("unbooked_count is not stated (a blank is not a zero)")
        elif int(self.unbooked_count) != 0:
            out.append(f"{int(self.unbooked_count)} item(s) are still unbooked")
        return out


# ------------------------------------------------------------------ helpers

def _declared(profile, key) -> list:
    """Values declared under `key` anywhere in `profile.decisions_made`.

    One mechanism for every declaration the exit tests read, so a user learns it
    once: a decision is a dict with an id, a source and the fact it declares.
    """
    out = []
    for decision in (getattr(profile, "decisions_made", None) or []):
        if isinstance(decision, dict) and key in decision:
            out.append(decision[key])
    return out


def _accounts_where(ledger, predicate) -> list:
    return [a for a in (getattr(ledger, "accounts", {}) or {}).values() if predicate(a)]


def _balance(ledger, account) -> Optional[Decimal]:
    """Balance of one account: its opening position plus the movement since.

    None means the balance CANNOT BE DETERMINED, and every caller here prints
    that phrase rather than a number. It happens when no export starts on the
    ledger's own first day, so nothing states what each account carried in.

    This used to sum the posted lines alone and call the result a balance. On a
    real export that reported a SAFE liability of several million as 0.00,
    because the account had no activity in either exported year, and every
    check downstream passed on it. Movement is not a balance, and the difference is the whole
    opening position. The Account List's balance column is not the answer
    either; see `qbo_exports.load_all` for why it is rejected.
    """
    key = getattr(account, "key", None) or getattr(account, "full_name", "") or account
    getter = getattr(ledger, "balance_of", None)
    if getter is None:
        return None
    value = getter(key)
    return None if value is None else money(value)


def _balance_text(value, account=None) -> str:
    """A balance as a person reads it: on its own side, never negative."""
    return _balance_on_its_side(value, account, undetermined=CANNOT_DETERMINE)


def _presented(balances) -> dict:
    """{label: "18,204.67 Cr"} for a {label: (account, balance)} mapping."""
    return {label: _balance_text(value, account)
            for label, (account, value) in balances.items()}


def _undeterminable(ledger) -> str:
    """Why no balance can be stated, in one sentence, or "" when they can."""
    if getattr(ledger, "opening_basis", ""):
        return ""
    notes = list(getattr(ledger, "opening_notes", []) or [])
    return notes[0] if notes else (
        "no export states an opening balance, so no account's balance can be determined"
    )


def _period(recon, ledger):
    start = getattr(recon, "period_start", None) or getattr(ledger, "period_start", None)
    end = getattr(recon, "period_end", None) or getattr(ledger, "period_end", None)
    return start, end


# ---------------------------------------------------------------- the tests

def run(ledger, statements, profile, recon, attestation=None, *, entries=()) -> ExitTestReport:
    """Run all ten. Nothing here mutates anything; it only measures."""
    attestation = Attestation.of(attestation)
    start, end = _period(recon, ledger)
    tests = [
        _t1_foots(ledger),
        _t2_differences_stated(ledger, recon),
        _t3_opening_balance_equity(ledger),
        _t4_clearing(ledger),
        _t5_impossible(ledger, profile),
        _t6_months(ledger, profile, start, end),
        _t7_classes(ledger, profile, start, end),
        _t8_basis(ledger, entries),
        _t9_attestation(attestation),
        _t10_dates(ledger, profile, end),
    ]
    return ExitTestReport(
        company=getattr(ledger, "company", "") or getattr(getattr(profile, "entity", None), "name", ""),
        period_start=start, period_end=end, tests=tests,
        wrong_side=_scan_sides(ledger),
    )


_RETAINED_EARNINGS = re.compile(
    r"\b(retained earnings|accumulated (deficit|surplus)|net income|profit for the (year|period))\b",
    re.I,
)


def _closes_into_retained_earnings(account, label="") -> bool:
    """True for the accounts QuickBooks rolls up at year end without a journal.

    At the end of a fiscal year QuickBooks moves every income and expense
    balance into Retained Earnings, and it posts NO journal line to do it. So a
    trial balance dated after a year end shows income statement accounts holding
    only the current year, and Retained Earnings holding every prior year's
    result, while the general ledger shows neither of those moves. Comparing
    those accounts one by one against the ledger reports a difference that is
    not a defect; comparing them in aggregate is exact, because the close only
    ever moves amounts between them.
    """
    if account is None:
        # A trial balance can name an account the Account List does not, and
        # Retained Earnings is the commonest one: QuickBooks maintains it and a
        # filtered chart export can leave it out. Fall back to the printed label
        # rather than treating it as an ordinary balance-sheet row.
        return bool(_RETAINED_EARNINGS.search(label or ""))
    if not getattr(account, "is_balance_sheet", True):
        return True
    name = f"{getattr(account, 'full_name', '')} {getattr(account, 'name', '')} {label}"
    return bool(_RETAINED_EARNINGS.search(name))


def _trial_balance_to_tie(ledger):
    """Which exported trial balance the ledger is tied to, or None.

    The one stated as of the ledger's own period end where it exists, because
    that is the figure a reader will compare against. Otherwise the latest one
    falling inside the period, tied at ITS date rather than at the period end.
    """
    loaded = list(getattr(ledger, "trial_balances", []) or [])
    if not loaded:
        return None
    end = getattr(ledger, "period_end", None)
    start = getattr(ledger, "period_start", None)
    exact = [t for t in loaded if end is not None and t.as_of == end]
    if exact:
        return exact[-1]
    inside = [t for t in loaded
              if t.as_of is not None
              and (start is None or t.as_of >= start)
              and (end is None or t.as_of <= end)]
    if inside:
        return max(inside, key=lambda t: t.as_of)
    return None


def _t1_foots(ledger) -> ExitTest:
    """Tie the exported trial balance to the loaded ledger, account by account.

    THIS TEST USED TO BE UNFAILABLE. It summed the ledger's own debits and
    credits and called that "the trial balance". A general ledger always foots,
    by construction, so the check passed on every input including books with a
    liability in the millions reported as zero. It never opened either trial
    balance in the folder, both of which `load_all` recorded as "read for its
    face only".
    The trial balance total and the ledger's turnover are different numbers,
    and printing the second in place of the first is what made this
    unfailable. `tests/test_exit_tests.py` pins both.

    A check that cannot fail is not a check, so the trial balance is now
    REQUIRED. Where none was exported the test fails saying it could not be
    checked, which is the same rule test 10 applies to a missing inception date.
    """
    debits, credits = money(ledger.total_debits()), money(ledger.total_credits())
    ledger_difference = money(debits - credits)
    common = {"ledger debits": debits, "ledger credits": credits,
              "ledger difference": ledger_difference,
              "lines": len(getattr(ledger, "lines", []) or [])}

    tb = _trial_balance_to_tie(ledger)
    if tb is None:
        loaded = len(getattr(ledger, "trial_balances", []) or [])
        return ExitTest(
            1, TESTS[0][1], False,
            dict(common, **{"trial balance": "none covering this period",
                            "trial balances loaded": loaded}),
            detail=("The ledger's own debits and credits are shown above and they are not a "
                    "trial balance: a general ledger foots by construction, so measuring it "
                    "proves nothing. Without an exported trial balance there is no independent "
                    "figure to tie to, and a check that did not run is not a pass."),
            remedy=("export the Trial Balance for this period from QuickBooks (Reports, Trial "
                    "Balance, set the date to the period end) and put it in the exports folder."),
        )

    blocked = _undeterminable(ledger)
    if blocked:
        return ExitTest(
            1, TESTS[0][1], False,
            dict(common, **{"trial balance": tb.source_file,
                            "as of": iso(tb.as_of),
                            "trial balance total": money(tb.total_debits),
                            "ledger balances": CANNOT_DETERMINE}),
            detail=blocked,
            remedy=("export a General Ledger that starts on the first day of the period. Its "
                    "Beginning Balance rows are the opening position, and without them the "
                    "ledger has movement but no balances to tie."),
        )

    stated = tb.by_key()
    chart = dict(getattr(ledger, "accounts", {}) or {})
    # Every account the trial balance names, plus every account the ledger has
    # a balance for that the trial balance does NOT name. The second half is
    # what catches a whole account dropped from one side: the two would foot to
    # 0.00 independently and every listed row would still tie.
    keys = set(stated)
    for key in chart:
        value = ledger.balance_as_of(key, tb.as_of)
        if value not in (None, ZERO):
            keys.add(key)

    tied, differences, undeterminable, closing_book = 0, [], [], ZERO
    closing_stated = ZERO
    for key in sorted(keys):
        account = ledger.account(key)
        label = tb.label_for(key)
        book = ledger.balance_as_of(key, tb.as_of)
        printed = money(stated.get(key, ZERO))
        if _closes_into_retained_earnings(account, label):
            # Tied in aggregate below, never row by row. See the helper.
            closing_stated += printed
            if book is not None:
                closing_book += book
            continue
        if book is None:
            undeterminable.append(label)
            continue
        difference = money(book - printed)
        if difference == ZERO:
            tied += 1
            continue
        differences.append((label, book, printed, difference))

    closing_difference = money(closing_book - closing_stated)
    per_account = len(keys) - sum(
        1 for k in keys
        if _closes_into_retained_earnings(ledger.account(k), tb.label_for(k))
    )
    passed = (
        tb.foots
        and money(tb.total_debits - tb.total_credits) == ZERO
        and ledger_difference == ZERO
        and not differences
        and not undeterminable
        and closing_difference == ZERO
    )
    measured = dict(common)
    measured.update({
        "trial balance": f"{tb.source_file} as of {iso(tb.as_of)}",
        "trial balance total": money(tb.total_debits),
        "trial balance difference": money(tb.total_debits - tb.total_credits),
        "accounts tied one by one": f"{tied} of {per_account}",
        "accounts that disagree": len(differences),
        "balances that cannot be determined": len(undeterminable),
        "retained earnings and P&L, in aggregate": closing_difference,
    })
    for label, book, printed, difference in differences[:8]:
        measured[label] = f"ledger {fmt(book)} against trial balance {fmt(printed)}, difference {fmt(difference)}"
    if len(differences) > 8:
        measured["further differences not listed"] = len(differences) - 8

    return ExitTest(
        1, TESTS[0][1], passed, measured,
        detail=(
            "Exact equality of Decimals of cents, with no tolerance and no rounding branch. "
            "Every balance-sheet account is tied one by one against the exported trial "
            "balance, using its opening position plus movement to the trial balance's own "
            "date. Retained earnings and the income statement accounts are tied in aggregate "
            "instead, because QuickBooks closes the year into retained earnings without "
            "posting a journal line, so a row-by-row comparison there reports a difference "
            "that is not a defect. The aggregate is exact: the close only moves amounts "
            "between those accounts."
        ),
        remedy=(
            ("the ledger itself does not foot by " + fmt(ledger_difference) + "; find that first. ")
            if ledger_difference != ZERO else ""
        ) + (
            (f"{len(differences)} account(s) disagree with the trial balance, worst first: "
             + "; ".join(f"{label} by {fmt(difference)}"
                         for label, _, _, difference in sorted(
                             differences, key=lambda d: -abs(d[3]))[:5]))
            if differences else ""
        ) + (
            (f" {len(undeterminable)} account(s) have no determinable balance: "
             + ", ".join(undeterminable[:5]))
            if undeterminable else ""
        ) + (
            (f" retained earnings and the income statement accounts are out by "
             f"{fmt(closing_difference)} in aggregate, which no year-end close explains")
            if closing_difference != ZERO else ""
        ) or "nothing: this test passes",
    )


def _t2_differences_stated(ledger, recon) -> ExitTest:
    stated, total = (recon.cells_stated() if hasattr(recon, "cells_stated") else (0, 0))
    unchecked = list(getattr(recon, "unchecked", []) or [])
    expected = list(getattr(recon, "unchecked_but_expected", []) or [])
    covered = len(getattr(recon, "accounts", []) or [])
    # The denominator is REAL balance-carrying accounts. Counting the chart's
    # parent rollups and its never-used template rows put 108 in a denominator
    # the reconciliation could only ever fill 25 of, so the test read as 83
    # accounts nobody had looked at when in fact none of the 83 exists to look
    # at. See `model.is_real_account`.
    carrying = _accounts_where(
        ledger,
        lambda a: a.is_balance_sheet and _is_real_account(ledger, a),
    )
    missing_accounts = max(0, len(carrying) - covered)
    passed = total > 0 and not expected and missing_accounts == 0
    return ExitTest(
        2, TESTS[1][1], passed,
        {"account-month cells": total, "with a stated difference": stated,
         "cannot check": len(unchecked), "missing a statement we hold": len(expected),
         "accounts in the report": covered, "balance-carrying accounts": len(carrying)},
        detail=(
            "Zeros count: a cell that ties is stated as 0.00, because a report of only the "
            "non-zero rows cannot be told apart from a report where nobody looked. Cells with no "
            f"statement read \"{'cannot check' if unchecked else 'n/a'}\" rather than 0.00. An "
            "account with no external statement (equity, accruals) can never be stated and is not "
            "counted against this test; one the profile says we hold statements for is."
        ),
        remedy=(
            "get the missing statements: "
            + ", ".join(sorted({f"{r.account} {r.month}" for r in expected})[:8])
            if expected else
            "include every balance-carrying account in the reconciliation, not only the ones with statements"
        ),
    )


def _t3_opening_balance_equity(ledger) -> ExitTest:
    accounts = _accounts_where(
        ledger, lambda a: a.role == "obe" or _OBE_NAME.search(a.full_name or a.name or "")
    )
    held = {a.label() or a.full_name: (a, _balance(ledger, a)) for a in accounts}
    balances = {k: v for k, (_, v) in held.items()}
    unknown = {k for k, v in balances.items() if v is None}
    non_zero = {k: v for k, v in balances.items() if v is not None and v != ZERO}
    measured = {k: (CANNOT_DETERMINE if v is None else v) for k, v in balances.items()}
    presented = _presented(held)
    measured = measured or {"opening balance equity accounts": 0}
    return ExitTest(
        3, TESTS[2][1], not non_zero and not unknown,
        measured, presented=presented,
        detail=(
            "Opening balance equity is a holding pen QuickBooks uses while an account is being set "
            "up. A balance left in it means an opening balance was entered and never explained."
            if accounts else "The chart has no opening balance equity account, so there is nothing to clear."
        ),
        remedy=(
            ("trace each opening balance to what it actually was (owner contribution, prior-year "
             "retained earnings, a loan) and reclassify it with a reason: "
             + ", ".join(f"{k} {_balance_text(v, held[k][0])}" for k, v in non_zero.items()))
            if non_zero else ""
        ) + (
            (("; " if non_zero else "")
             + f"{len(unknown)} of these has no determinable balance ({_undeterminable(ledger)})")
            if unknown else ""
        ),
    )


def _t4_clearing(ledger) -> ExitTest:
    accounts = _accounts_where(
        ledger, lambda a: a.role == "clearing" or _CLEARING_NAME.search(a.full_name or a.name or "")
    )
    held = {a.label() or a.full_name: (a, _balance(ledger, a)) for a in accounts}
    balances = {k: v for k, (_, v) in held.items()}
    unknown = {k for k, v in balances.items() if v is None}
    non_zero = {k: v for k, v in balances.items() if v is not None and v != ZERO}
    measured = {k: (CANNOT_DETERMINE if v is None else v) for k, v in balances.items()}
    return ExitTest(
        4, TESTS[3][1], not non_zero and not unknown,
        measured or {"clearing and suspense accounts": 0}, presented=_presented(held),
        detail=(
            "A clearing account is a route, not a destination. A balance in one is money whose "
            "other side was never posted, and it grows quietly month over month."
            if accounts else "The chart has no clearing or suspense account."
        ),
        remedy=(
            ("find the missing other side for each: "
             + ", ".join(f"{k} {_balance_text(v, held[k][0])}" for k, v in non_zero.items())
             + ". Do not write the balance off; a write-off moves the error into the P&L.")
            if non_zero else ""
        ) + (
            (f" {len(unknown)} of these has no determinable balance "
             f"({_undeterminable(ledger)}), so it cannot be shown to be clear.")
            if unknown else ""
        ),
    )


def _t5_impossible(ledger, profile) -> ExitTest:
    """Every account whose balance sits on the side opposite its normal one.

    TWO SEVERITIES, AND ONLY ONE OF THEM FAILS THIS TEST.

    A bank account on the credit side and a card on the debit side cannot be a
    real presentation of anything. Either the books are missing transactions or
    the account is genuinely overdrawn, and only a statement settles which. Those
    fail, as they always have, until the profile acknowledges one as real.

    Every OTHER wrong-side balance is reported and does not fail. A receivable
    with a credit balance is a customer overpayment often enough to be ordinary;
    so is a payable with a debit balance, and an equity account holding a
    deficit. Failing on them would train a reader to skim the list that also
    holds the checking account, which is the one finding they must not skim. They
    are surfaced by `sides.scan`, with what the account is, which side it belongs
    on and what that difference normally means, and the report says which
    document settles it rather than guessing.
    """
    acknowledged = set()
    for decision in (getattr(profile, "decisions_made", None) or []):
        if isinstance(decision, dict) and decision.get("acknowledges") and decision.get("account"):
            acknowledged.add(norm_text(decision["account"]))

    findings, checked, unknown, skipped = [], 0, [], 0
    accounts_by_label = {}
    for account in (getattr(ledger, "accounts", {}) or {}).values():
        kind = ""
        if account.role == "bank" or account.type == "bank":
            kind = "bank"
        elif account.role == "card" or account.type == "credit card":
            kind = "card"
        if not kind:
            continue
        # A parent rollup and an account that has never held anything are not
        # real accounts to check. Counting them inflates the denominator with
        # rows nobody can act on: on one real chart 15 of 21 "bank and card
        # accounts" were empty placeholders and two were rollups.
        if not _is_real_account(ledger, account):
            skipped += 1
            continue
        checked += 1
        label = account.label() or account.full_name
        accounts_by_label[label] = account
        balance = _balance(ledger, account)
        if balance is None:
            unknown.append(label)
            continue
        impossible = (kind == "bank" and balance < ZERO) or (kind == "card" and balance > ZERO)
        if not impossible:
            continue
        keys = {norm_text(label), norm_text(account.name), norm_text(account.full_name),
                norm_text(account.number), norm_text(getattr(account, "key", ""))}
        findings.append({
            "label": label, "kind": kind, "balance": balance,
            "acknowledged": bool(keys & acknowledged),
        })

    # The wider sweep: every balance-sheet account, not only the bank and card
    # ones. This is where a large liability sitting on the debit side would
    # surface, and it is reported rather than failed. See the docstring.
    swept = _scan_sides(ledger)
    impossible_labels = {f["label"] for f in findings}
    other_side = [w for w in swept.findings if w.label not in impossible_labels]

    unacknowledged = [f for f in findings if not f["acknowledged"]]
    measured = {"bank and card accounts checked": checked,
                "placeholders and rollups not checked": skipped,
                "impossible balances": len(findings),
                "acknowledged in the profile": len(findings) - len(unacknowledged),
                "balances that cannot be determined": len(unknown),
                "balance-sheet accounts swept for a wrong-side balance": swept.checked,
                "other accounts on the wrong side, reported not failed": len(other_side)}
    presented = {}
    for f in findings:
        key = f"{f['label']} ({f['kind']})"
        measured[key] = f["balance"]
        presented[key] = _balance_text(f["balance"], accounts_by_label.get(f["label"]))
    for w in other_side[:8]:
        key = f"{w.label} ({w.account_type or 'unknown type'})"
        measured[key] = w.signed
        presented[key] = f"{w.rendered()}, and it is {_side_word(w.normal)}-normal"
    if len(other_side) > 8:
        measured["further wrong-side accounts not listed"] = len(other_side) - 8
    return ExitTest(
        5, TESTS[4][1], not unacknowledged and not unknown, measured,
        presented=presented,
        detail=(
            "A bank account cannot hold less than nothing and a card cannot be an asset. Either is "
            "normally a missing deposit, a duplicated payment, or a card booked with the sign "
            "inverted. An overdraft is real and is allowed here, once the profile says so. A "
            "balance here is the account's opening position plus its movement, not the movement "
            "alone: an account overdrawn at the start of the period looks fine on movement. "
            + (f"{len(other_side)} further account(s) carry a balance on the side opposite their "
               f"normal one. Those are reported, not failed, because a receivable with a credit "
               f"balance and a payable with a debit balance are ordinary often enough that "
               f"failing on them would train a reader to skim this list."
               if other_side else
               f"Every one of the {swept.checked} balance-sheet accounts swept holds its balance "
               f"on its own normal side.")
        ),
        remedy=(
            ("explain each: "
             + ", ".join(f"{f['label']} {_balance_text(f['balance'], accounts_by_label.get(f['label']))}"
                         for f in unacknowledged)
             + ". If it is genuinely real, record it in profile.decisions_made as "
               "{\"account\": \"<key>\", \"acknowledges\": \"why this balance is real\"}.")
            if unacknowledged else ""
        ) + (
            (f" {len(unknown)} account(s) have no determinable balance and so cannot be checked "
             f"at all ({_undeterminable(ledger)}): " + ", ".join(unknown[:5]))
            if unknown else ""
        ),
    )


def _t6_months(ledger, profile, start, end) -> ExitTest:
    if not start or not end:
        return ExitTest(
            6, TESTS[5][1], False, {"period": "not declared"},
            detail="Neither the reconciliation nor the ledger carries a period.",
            remedy="pass period_start and period_end to reconcile() so this test has a window to check.",
        )
    months = month_range(parse_date(start), parse_date(end))
    activity = getattr(ledger, "months_with_activity", lambda: {})()
    dormant = set()
    for value in _declared(profile, "dormant_months"):
        dormant.update(str(v) for v in (value or []))
    for value in _declared(profile, "dormant_month"):
        dormant.add(str(value))

    empty = [m for m in months if not activity.get(m)]
    undeclared = [m for m in empty if m not in dormant]
    measured = {"months in the period": len(months),
                "months with activity": len(months) - len(empty),
                "empty months": len(empty),
                "declared dormant": sorted(dormant & set(months)) or "none",
                "unexplained gaps": undeclared or "none"}
    return ExitTest(
        6, TESTS[5][1], not undeclared, measured,
        detail=(
            "A month with no transactions is the commonest symptom of a book somebody stopped "
            "keeping. It is occasionally true, which is why a declared dormant month passes, and it "
            "has to be declared rather than assumed."
        ),
        remedy=(
            "account for " + ", ".join(undeclared) + ": either the feed stopped and the "
            "transactions were never imported, or the company really was dormant, in which case "
            "declare it in profile.decisions_made as {\"dormant_months\": [...], \"source\": \"...\"}."
        ),
    )


def _t7_classes(ledger, profile, start, end) -> ExitTest:
    in_use = any(bool(v) for v in _declared(profile, "classes_in_use"))
    lines = list(getattr(ledger, "lines", []) or [])
    if start and end:
        s, e = parse_date(start), parse_date(end)
        lines = [l for l in lines if s <= l.date <= e]
    if not in_use:
        return ExitTest(
            7, TESTS[6][1], True,
            {"lines in the period": len(lines), "classes in use": False},
            detail=("The profile does not declare classes in use, so there is nothing to be "
                    "complete. Declare {\"classes_in_use\": true} in decisions_made if that is wrong."),
            remedy="",
        )
    missing = [l for l in lines if norm_text(getattr(l, "klass", "")) in _MISSING_CLASS]
    literal = [l for l in missing if norm_text(getattr(l, "klass", "")) == "not specified"]
    by_account = {}
    for line in missing:
        by_account[line.account] = by_account.get(line.account, 0) + 1
    worst = sorted(by_account.items(), key=lambda kv: -kv[1])[:5]
    return ExitTest(
        7, TESTS[6][1], not missing,
        {"lines in the period": len(lines),
         "with a class": len(lines) - len(missing),
         "without a class": len(missing),
         "literally \"not specified\"": len(literal),
         "worst accounts": [f"{a} ({n})" for a, n in worst] or "none"},
        detail=(
            "\"Not specified\" is a defect, not a state. Every entry posted, accepted or "
            "reclassified carries a class where the company uses them; where the class cannot be "
            "evidenced from a document or from the company's own precedent, the item is parked and "
            "the document that would settle it is named, never guessed."
        ),
        remedy=(
            f"class the {len(missing)} line(s) with no class, worst first: "
            + ", ".join(f"{a} ({n})" for a, n in worst)
        ),
    )


def _t8_basis(ledger, entries) -> ExitTest:
    drafted = list(entries or [])
    without = [e for e in drafted if not str(getattr(e, "basis", "") or "").strip()]
    posted = [l for l in (getattr(ledger, "lines", []) or [])
              if norm_text(getattr(l, "txn_type", "")) == "journal entry"]
    posted_ids, posted_blank = set(), set()
    for line in posted:
        marker = line.txn_id or line.doc_num or f"{iso(line.date)}|{line.account}"
        posted_ids.add(marker)
        if not str(line.memo or "").strip():
            posted_blank.add(marker)
    return ExitTest(
        8, TESTS[7][1], not without and not posted_blank,
        {"entries drafted this run": len(drafted),
         "drafted with a basis": len(drafted) - len(without),
         "journal entries already posted": len(posted_ids),
         "posted with no memo at all": len(posted_blank)},
        detail=(
            "A basis is the sentence answering \"why does this entry exist\". "
            "`ProposedEntry.check()` refuses a drafted entry without one, so a failure here on the "
            "drafted side means something bypassed the writer. A journal entry already in the books "
            "with an empty memo is the same defect a year earlier: nobody can now say why it is there."
        ),
        remedy=(
            "give a basis to " + (", ".join(str(getattr(e, "number", "?")) for e in without[:8]) if without else "")
            + (f" and a memo to {len(posted_blank)} posted journal entr(y/ies) that have none"
               if posted_blank else "")
        ),
    )


def _t9_attestation(attestation) -> ExitTest:
    if attestation is None:
        return ExitTest(
            9, TESTS[8][1], False,
            {"attestation": None, "unbooked items": None},
            detail=("No export can prove this. A receipt in a drawer, an invoice never forwarded and "
                    "a business expense on a personal card are invisible to every system here."),
            remedy=("ask the owner to state in their own words that nothing is unbooked, record who "
                    "said it and on what date, and pass it as attestation=. Optionally attach a "
                    "screenshot path of an empty inbox or receipt queue."),
        )
    problems = attestation.problems()
    return ExitTest(
        9, TESTS[8][1], not problems,
        {"unbooked items": attestation.unbooked_count,
         "attested by": attestation.by or None,
         "on": attestation.on or None,
         "screenshot": attestation.screenshot or "none",
         "statement": (attestation.statement[:80] + "...") if len(attestation.statement or "") > 80
                      else (attestation.statement or None)},
        detail="; ".join(problems) if problems else
               "A named person stated on a date that nothing is sitting unbooked.",
        remedy="fix the attestation: " + "; ".join(problems),
    )


def _t10_dates(ledger, profile, end) -> ExitTest:
    inception_values = [v for v in _declared(profile, "inception_date") if v]
    inception = try_date(inception_values[0]) if inception_values else None
    period_end = parse_date(end) if end else None
    lines = list(getattr(ledger, "lines", []) or [])

    after = [l for l in lines if period_end and l.date > period_end]
    before = [l for l in lines if inception and l.date < inception]
    measured = {
        "lines": len(lines),
        "period end": iso(period_end) if period_end else None,
        "dated after the period end": len(after),
        "inception date": iso(inception) if inception else None,
        "dated before the company existed": len(before) if inception else None,
    }
    if after:
        measured["latest"] = f"{iso(max(l.date for l in after))} on {after[0].account}"
    if before:
        measured["earliest"] = f"{iso(min(l.date for l in before))} on {before[0].account}"

    passed = bool(period_end) and not after and inception is not None and not before
    if not period_end:
        remedy = "declare the period so the future-dated half of this test can run"
    elif inception is None:
        remedy = ("declare the company's inception date in profile.decisions_made as "
                  "{\"inception_date\": \"YYYY-MM-DD\", \"source\": \"certificate of incorporation\"}. "
                  "Half of this test cannot run without it, and a check that did not run is not a pass.")
    else:
        found = []
        if after:
            found.append(f"{len(after)} line(s) dated after {iso(period_end)}")
        if before:
            found.append(f"{len(before)} line(s) dated before {iso(inception)}")
        remedy = (
            "investigate " + ", ".join(found) + ". A transaction dated outside the company's own "
            "life is a typo in the year, an import that read the wrong date column, or an entry "
            "posted into a period nobody reviews."
        ) if found else ""
    return ExitTest(
        10, TESTS[9][1], passed, measured,
        detail=("A date after the period end moves income and expense into a period the statements "
                "do not cover. A date before the company existed cannot be a transaction of this "
                "company at all."),
        remedy=remedy,
    )
