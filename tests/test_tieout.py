"""Tie-out tests. Synthetic fixtures only.

Every figure and name here is invented. "Acme Robotics Inc." is not a real
company and none of these numbers came from a real statement.

    pytest tests/test_tieout.py            # or
    python3 tests/test_tieout.py
"""

from __future__ import annotations

import datetime as _dt
import os
import sys
from decimal import Decimal

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "lib"))

from closethebooks.model import BankLine                                   # noqa: E402
from closethebooks.statements import StatementFile                         # noqa: E402
from closethebooks.tieout import chain_breaks, tie_out, tie_out_month      # noqa: E402
from closethebooks.util import money                                       # noqa: E402

COMPANY = "Acme Robotics Inc."


def line(day, descriptor, amount, month=4, year=2026):
    return BankLine(date=_dt.date(year, month, day), descriptor=descriptor, amount=money(amount))


APRIL = [
    line(2, "Bolt Depot supplies", "-250.00"),
    line(11, "Northwind Systems invoice 1041", "4000.00"),
    line(18, "Grayline Coffee", "-32.40"),
    line(27, "Pinebrook Realty rent", "-1500.00"),
]
APRIL_OPENING = money("10000.00")
APRIL_DEPOSITS = money("4000.00")
APRIL_WITHDRAWALS = money("1782.40")
APRIL_CLOSING = money("12217.60")


def statement(lines, opening, closing, *, month=4, year=2026, deposits=None,
              withdrawals=None, account_key="checking-1234", source="acme.pdf"):
    return StatementFile(
        account_key=account_key,
        period_start=_dt.date(year, month, 1),
        period_end=_dt.date(year, month, 28),
        opening_balance=opening,
        closing_balance=closing,
        lines=list(lines),
        source_file=source,
        parser="test",
        stated_deposits=deposits,
        stated_withdrawals=withdrawals,
    )


# ------------------------------------------------------------- a month ties

def test_a_month_that_ties_three_ways():
    result = tie_out_month(APRIL, APRIL_OPENING, APRIL_CLOSING, "2026-04",
                           stated_deposits=APRIL_DEPOSITS,
                           stated_withdrawals=APRIL_WITHDRAWALS)
    assert result.ties is True
    assert bool(result) is True
    assert result.rows == 4
    assert result.deposits == APRIL_DEPOSITS
    assert result.withdrawals == APRIL_WITHDRAWALS      # positive magnitude
    assert result.computed_closing == APRIL_CLOSING
    assert result.difference == money(0)
    assert result.note == ""
    assert all(ok for _n, available, ok, _d in result.checks if available)


def test_a_stated_withdrawals_figure_may_be_printed_negative():
    result = tie_out_month(APRIL, APRIL_OPENING, APRIL_CLOSING, "2026-04",
                           stated_deposits=APRIL_DEPOSITS,
                           stated_withdrawals=money("-1782.40"))
    assert result.ties is True


def test_totals_are_signed_from_the_account_holders_point_of_view():
    result = tie_out_month(APRIL, APRIL_OPENING, APRIL_CLOSING, "2026-04",
                           stated_deposits=APRIL_DEPOSITS,
                           stated_withdrawals=APRIL_WITHDRAWALS)
    assert result.deposits > 0 and result.withdrawals > 0
    assert result.net == money("2217.60")
    assert result.closing == result.computed_closing


# --------------------------------------------------------- a month does not

def test_a_month_that_does_not_tie_reports_the_exact_difference():
    """One row short. The gap is reported as a number and never plugged."""
    short = [l for l in APRIL if l.descriptor != "Grayline Coffee"]
    result = tie_out_month(short, APRIL_OPENING, APRIL_CLOSING, "2026-04",
                           stated_deposits=APRIL_DEPOSITS,
                           stated_withdrawals=APRIL_WITHDRAWALS)
    assert result.ties is False
    assert result.rows == 3
    assert result.difference == money("32.40")          # computed - stated
    assert result.withdrawal_difference == money("-32.40")
    assert "differs by 32.40" in result.note
    # the reported closing is the computed one; the stated one is not adopted
    assert result.computed_closing == money("12250.00")
    assert result.stated_closing == APRIL_CLOSING


def test_a_stated_deposits_figure_that_is_wrong_fails_even_when_the_chain_closes():
    result = tie_out_month(APRIL, APRIL_OPENING, APRIL_CLOSING, "2026-04",
                           stated_deposits=money("3900.00"),
                           stated_withdrawals=APRIL_WITHDRAWALS)
    assert result.ties is False
    assert result.difference == money(0)                # the chain itself closes
    assert result.deposit_difference == money("100.00")
    assert "stated deposits differs by 100.00" in result.note


def test_nothing_to_tie_against_is_not_a_pass():
    result = tie_out_month(APRIL, APRIL_OPENING, None, "2026-04")
    assert result.ties is False
    assert "no stated figure to tie against" in result.note
    assert result.computed_closing == APRIL_CLOSING


# ------------------------------------------------- a corrupt summary field

def test_corrupt_stated_closing_falls_back_to_the_chain_identity():
    """A real failure mode: the summary block is unreadable, the rows are fine.

    An April statement printed its withdrawals total as `-$8,1 8.4`. The parser
    reports such a field as None, and the chain identity decides. The month
    ties, and the note says which check was unavailable.
    """
    result = tie_out_month(APRIL, APRIL_OPENING, None, "2026-04",
                           stated_deposits=APRIL_DEPOSITS,
                           stated_withdrawals=APRIL_WITHDRAWALS)
    assert result.ties is True
    assert result.computed_closing == APRIL_CLOSING
    assert result.stated_closing is None
    assert result.difference == money(0)
    assert "stated closing not available" in result.note
    assert "chain identity used" in result.note


def test_corrupt_stated_withdrawals_falls_back_to_the_chain_identity():
    result = tie_out_month(APRIL, APRIL_OPENING, APRIL_CLOSING, "2026-04",
                           stated_deposits=APRIL_DEPOSITS,
                           stated_withdrawals=None)
    assert result.ties is True
    assert "stated withdrawals not available" in result.note


def test_a_corrupt_field_never_reads_as_zero():
    """None is "unreadable", not 0.00. Reading it as zero would fail a good month."""
    unreadable = tie_out_month(APRIL, APRIL_OPENING, APRIL_CLOSING, "2026-04",
                               stated_withdrawals=None)
    as_zero = tie_out_month(APRIL, APRIL_OPENING, APRIL_CLOSING, "2026-04",
                            stated_withdrawals=money(0))
    assert unreadable.ties is True
    assert as_zero.ties is False
    assert as_zero.withdrawal_difference == APRIL_WITHDRAWALS


# -------------------------------------------------------------- filtering

def test_tie_out_month_counts_only_the_month_asked_for():
    march = [line(3, "Bolt Depot supplies", "-100.00", month=3)]
    result = tie_out_month(march + APRIL, APRIL_OPENING, APRIL_CLOSING, "2026-04",
                           stated_deposits=APRIL_DEPOSITS,
                           stated_withdrawals=APRIL_WITHDRAWALS)
    assert result.rows == 4
    assert result.ties is True


def test_tie_out_of_a_statement_counts_every_row_it_holds():
    """A card cycle straddles two calendar months; the late row still counts."""
    card_lines = [
        line(3, "Bolt Depot supplies", "-142.25"),
        line(14, "Grayline Coffee", "-41.50"),
        line(1, "Payment received, Acme Robotics checking", "183.75", month=5),
    ]
    card = statement(card_lines, money(0), money(0), account_key="card-5678")
    result = tie_out(card)
    assert result.rows == 3
    assert result.month == "2026-04"                # labelled by the period end
    assert result.deposits == money("183.75")
    assert result.withdrawals == money("183.75")
    assert result.computed_closing == money(0)
    assert result.ties is True


def test_a_card_charge_reduces_a_liability_balance():
    """Card balances are negative when owed, exactly as the statement renders them."""
    card_lines = [line(3, "Bolt Depot supplies", "-142.25")]
    card = statement(card_lines, money(0), money("-142.25"), account_key="card-5678")
    result = tie_out(card)
    assert result.ties is True
    assert result.computed_closing == money("-142.25")


# ------------------------------------------------------------ chain breaks

def _month_statement(month, opening, closing, amount):
    return statement(
        [line(5, "Northwind Systems invoice 1041", amount, month=month)],
        opening, closing, month=month,
        deposits=money(amount) if money(amount) > 0 else money(0),
        withdrawals=money(0) if money(amount) > 0 else -money(amount),
        source=f"acme-checking-1234-2026-{month:02d}.pdf",
    )


def test_a_continuous_chain_reports_no_breaks():
    months = [
        _month_statement(1, money("1000.00"), money("1500.00"), "500.00"),
        _month_statement(2, money("1500.00"), money("2200.00"), "700.00"),
        _month_statement(3, money("2200.00"), money("2100.00"), "-100.00"),
    ]
    assert all(s.check().ties for s in months)
    assert chain_breaks(months) == []


def test_a_chain_break_between_months_is_reported_with_its_difference():
    months = [
        _month_statement(1, money("1000.00"), money("1500.00"), "500.00"),
        # February opens 250.00 above where January closed: a month is missing,
        # yet each statement ties against its own summary.
        _month_statement(2, money("1750.00"), money("2450.00"), "700.00"),
    ]
    assert all(s.check().ties for s in months)
    breaks = chain_breaks(months)
    assert len(breaks) == 1
    assert breaks[0]["difference"] == money("250.00")
    assert breaks[0]["prior_month"] == "2026-01"
    assert breaks[0]["next_month"] == "2026-02"
    assert breaks[0]["prior_closing"] == money("1500.00")
    assert breaks[0]["next_opening"] == money("1750.00")
    assert "250.00" in breaks[0]["note"]


def test_chain_breaks_are_grouped_by_account():
    checking = [
        _month_statement(1, money("1000.00"), money("1500.00"), "500.00"),
        _month_statement(2, money("1500.00"), money("2200.00"), "700.00"),
    ]
    card_january = statement([line(3, "Bolt Depot supplies", "-100.00", month=1)],
                             money(0), money("-100.00"), month=1, account_key="card-5678")
    card_february = statement([line(3, "Grayline Coffee", "-50.00", month=2)],
                              money("-999.00"), money("-1049.00"), month=2,
                              account_key="card-5678")
    breaks = chain_breaks(checking + [card_january, card_february])
    assert len(breaks) == 1
    assert breaks[0]["account_key"] == "card-5678"
    assert breaks[0]["difference"] == money("-899.00")


def test_a_chain_uses_the_computed_closing_when_the_stated_one_is_corrupt():
    january = statement([line(5, "Northwind Systems invoice 1041", "500.00", month=1)],
                        money("1000.00"), None, month=1)
    february = _month_statement(2, money("1500.00"), money("2200.00"), "700.00")
    breaks = chain_breaks([january, february])
    assert breaks == []


def test_the_difference_is_a_decimal_of_cents_and_is_never_plugged():
    result = tie_out_month(APRIL, APRIL_OPENING, money("12217.61"), "2026-04")
    assert isinstance(result.difference, Decimal)
    assert result.difference == Decimal("-0.01")
    assert result.ties is False
    assert result.computed_closing == APRIL_CLOSING     # unchanged by the gap


def test_as_row_is_reportable():
    result = tie_out_month(APRIL, APRIL_OPENING, APRIL_CLOSING, "2026-04",
                           stated_deposits=APRIL_DEPOSITS,
                           stated_withdrawals=APRIL_WITHDRAWALS,
                           account_key="checking-1234")
    row = result.as_row()
    assert row["ties"] == "yes"
    assert row["difference"] == "0.00"
    assert row["account"] == "checking-1234"
    assert "TIES" in result.describe()


def _run():
    tests = [(n, o) for n, o in sorted(globals().items())
             if n.startswith("test_") and callable(o)]
    failures = 0
    for name, fn in tests:
        try:
            fn()
            print(f"  ok   {name}")
        except Exception as exc:                      # noqa: BLE001
            failures += 1
            print(f"  FAIL {name}: {type(exc).__name__}: {exc}")
    print(f"\n{len(tests) - failures} passed, {failures} failed")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(_run())
