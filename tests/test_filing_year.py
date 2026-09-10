"""863 is not the amount of work, and 197 of it would break a closed period.

A wind-down was once quoted a queue depth and a month of hours off a number
like this one. Both were wrong and the tool would have repeated them. Every
figure here is invented; the partition is not:

    863   in the queue
    354   dated 2026, so nothing about them touches the 2025 return
    197   dated January and February 2025, in months already reconciled clean.
          Booking one posts a SECOND copy of a transaction the books hold, into
          a period somebody has already tied to a statement.
    312   the work.

The 197 are the reason this file exists rather than a date filter. They sit on a
duplicate account created when a bank feed was relinked, and THAT account has
never been reconciled: ask it whether it has, and the answer is no, and all 197
read as work. Ask the account whose history governs it and the answer is
"reconciled clean through February". `test_the_double_count_trap_...` is the pair
of tests that pins both readings.

    python3 tests/test_filing_year.py
"""

from __future__ import annotations

import datetime
import os
import sys
from decimal import Decimal

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "lib"))
sys.path.insert(0, HERE)

from closethebooks import filing_year, twins                        # noqa: E402
from closethebooks.filing_year import (                             # noqa: E402
    ALREADY_BOOKED, IN_SCOPE, PARTITIONS, POSTDATES, PREDATES,
    reconciled_through_from, scope,
)
from closethebooks.model import BankLine                            # noqa: E402
from closethebooks.recon import ReconRow, ReconReport               # noqa: E402

import wound_down_company as company                                # noqa: E402

D = Decimal


def _queue():
    return company.queue()


def _scoped(**kw):
    return scope(_queue(), company.FILING_YEAR,
                 reconciled_through=company.reconciled_through(), **kw)


def _history():
    return twins.scan(company.ledger()).history_map()


# ------------------------------------------------------- nothing is dropped

def test_every_item_lands_in_exactly_one_partition():
    scoped = _scoped(history_of=_history())
    counted = sum(scoped.part(name).count for name in PARTITIONS)
    assert counted + len(scoped.undated) == scoped.total == company.QUEUE_TOTAL
    seen = [id(item) for name in PARTITIONS for item in scoped.part(name).items]
    assert len(seen) == len(set(seen)), "an item was counted in two partitions"


def test_an_item_with_no_readable_date_is_kept_rather_than_dropped():
    queue = _queue() + [BankLine(date=None, descriptor="no date", amount=D("-10.00"),
                                 account_key=company.CARD_TWIN)]
    # BankLine keeps date=None, which is what a blank export cell produces.
    scoped = scope(queue, 2025, reconciled_through=company.reconciled_through())
    assert len(scoped.undated) == 1
    assert scoped.total == company.QUEUE_TOTAL + 1
    assert "no date that can be read" in " ".join(scoped.lines())


# ----------------------------------------------------- the headline number

def test_the_headline_is_the_in_scope_count_and_not_the_queue_depth():
    scoped = _scoped(history_of=_history())
    assert scoped.part(IN_SCOPE).count == company.QUEUE_REAL_WORK == 312
    assert "312 of 863" in scoped.headline()
    assert "863 of 863" not in scoped.headline()


def test_the_partitions_are_the_real_files_own_numbers():
    scoped = _scoped(history_of=_history())
    assert scoped.counts() == {
        IN_SCOPE: 312, ALREADY_BOOKED: 197, POSTDATES: 354, PREDATES: 0,
    }


def test_out_of_scope_items_are_explained_rather_than_hidden():
    scoped = _scoped(history_of=_history())
    text = " ".join(scoped.lines())
    for name in (ALREADY_BOOKED, POSTDATES, PREDATES):
        assert scoped.part(name).reason, f"{name} carries no reason"
        assert scoped.part(name).n_of_n() in text, f"{name} is not stated n of N"
    assert "2025-01" in text and "2025-02" in text, "the reconciled months are not named"


# ------------------------------------------------------- the double count

def test_the_double_count_trap_fires_without_the_history_mapping():
    """The shape that made 509 look like work: the queue is on the new account.

    This is not a failure of the module. It is the module reporting honestly
    that it could not run the check, which is what the second half asserts.
    """
    scoped = _scoped()
    assert scoped.part(ALREADY_BOOKED).count == 0
    assert scoped.part(IN_SCOPE).count == 509
    assert company.CARD_TWIN in scoped.never_reconciled
    assert scoped.unchecked_for_double_count == company.QUEUE_TOTAL
    text = " ".join(scoped.lines())
    assert "could not be tested" in text or "could not run" in text


def test_the_double_count_trap_closes_when_the_history_mapping_is_passed():
    scoped = _scoped(history_of=_history())
    assert scoped.part(ALREADY_BOOKED).count == company.QUEUE_ALREADY_BOOKED == 197
    assert scoped.part(IN_SCOPE).count == 312
    assert scoped.history_taken_from[company.CARD_TWIN] == company.CARD_NUMBERED
    assert not scoped.never_reconciled


def test_the_already_booked_partition_states_what_would_be_double_counted():
    part = _scoped(history_of=_history()).part(ALREADY_BOOKED)
    assert part.net == D("197") * D("-118.40")
    assert part.months() == ["2025-01", "2025-02"]
    assert "reconciliation that closed at 0.00" in part.reason


def test_already_booked_is_decided_before_the_year_bounds():
    """An item can be both before the year and inside a reconciled period.

    It is reported as already booked, because that is the reason that carries a
    consequence: working it damages a closed period, whichever year it is in.
    """
    item = BankLine(date=datetime.date(2024, 6, 1), descriptor="old",
                    amount=D("-10.00"), account_key="101000")
    scoped = scope([item], 2025,
                   reconciled_through={"101000": datetime.date(2025, 2, 28)})
    assert scoped.part(ALREADY_BOOKED).count == 1
    assert scoped.part(PREDATES).count == 0


# -------------------------------------------- when nothing was reconciled

def test_no_reconciliation_anywhere_is_said_out_loud_and_not_read_as_clean():
    scoped = scope(_queue(), 2025)
    assert scoped.part(ALREADY_BOOKED).count == 0
    assert scoped.notes, "an empty already-booked partition must say why"
    assert "could not run" in " ".join(scoped.notes)
    assert "not because nothing is already booked" in " ".join(scoped.notes)


def test_one_account_can_be_declared_never_reconciled_against_a_blanket_date():
    queue = [BankLine(date=datetime.date(2025, 1, 5), descriptor="a",
                      amount=D("-1.00"), account_key="101000"),
             BankLine(date=datetime.date(2025, 1, 5), descriptor="b",
                      amount=D("-1.00"), account_key="102000")]
    scoped = scope(queue, 2025, reconciled_through={
        "101000": datetime.date(2025, 2, 28), "102000": None})
    assert scoped.part(ALREADY_BOOKED).count == 1
    assert scoped.never_reconciled == ["102000"]


# ----------------------------------------------------- learning the date

def test_reconciled_through_stops_at_the_first_month_that_does_not_tie():
    """A later month that happens to tie does not extend a broken chain."""
    rows = [
        ReconRow(account_key="101000", month="2025-01", checkable=True,
                 difference=D("0.00")),
        ReconRow(account_key="101000", month="2025-02", checkable=True,
                 difference=D("0.00")),
        ReconRow(account_key="101000", month="2025-03", checkable=True,
                 difference=D("-412.50")),
        ReconRow(account_key="101000", month="2025-04", checkable=True,
                 difference=D("0.00")),
    ]
    learned = reconciled_through_from(ReconReport(rows=rows))
    assert learned == {"101000": datetime.date(2025, 2, 28)}


def test_an_account_with_no_month_that_ties_gets_no_date_at_all():
    rows = [ReconRow(account_key="102000", month="2025-01", checkable=False,
                     difference=None)]
    assert reconciled_through_from(ReconReport(rows=rows)) == {}


def test_a_month_with_no_statement_breaks_the_chain_like_a_difference_does():
    rows = [
        ReconRow(account_key="101000", month="2025-01", checkable=True,
                 difference=D("0.00")),
        ReconRow(account_key="101000", month="2025-02", checkable=False,
                 difference=None),
        ReconRow(account_key="101000", month="2025-03", checkable=True,
                 difference=D("0.00")),
    ]
    assert reconciled_through_from(ReconReport(rows=rows)) == {
        "101000": datetime.date(2025, 1, 31)}


# -------------------------------------------------------- a fiscal year

def test_a_fiscal_year_that_is_not_the_calendar_year_is_given_not_guessed():
    queue = [BankLine(date=datetime.date(2025, 7, 15), descriptor="in",
                      amount=D("-1.00"), account_key="101000"),
             BankLine(date=datetime.date(2026, 7, 15), descriptor="out",
                      amount=D("-1.00"), account_key="101000")]
    scoped = scope(queue, None, year_start="2025-07-01", year_end="2026-06-30")
    assert scoped.part(IN_SCOPE).count == 1
    assert scoped.part(POSTDATES).count == 1
    assert scoped.year_end == datetime.date(2026, 6, 30)


# ------------------------------------------------------------- rendering

def test_every_partition_reaches_both_renderings_as_n_of_n():
    scoped = _scoped(history_of=_history())
    for text in ("\n".join(filing_year.render(scoped)),
                 "\n".join(filing_year.render_markdown(scoped))):
        assert "312 of 863" in text
        assert "197 of 863" in text
        assert "354 of 863" in text
        assert "0 of 863" in text, "a zero partition must be stated, not omitted"


def test_no_finding_instructs_a_person():
    scoped = _scoped(history_of=_history())
    text = " ".join(scoped.lines()).lower()
    for phrase in ("you should", "please ", "make sure", "go and ", "ask the client"):
        assert phrase not in text, f"the partition instructs a person: {phrase!r}"


if __name__ == "__main__":
    passed = 0
    for name, fn in sorted(list(globals().items())):
        if name.startswith("test_") and callable(fn):
            fn()
            passed += 1
    print(f"{passed} of {passed} filing-year tests pass")
