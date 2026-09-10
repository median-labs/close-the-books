"""Adjusting-entry generator tests. Synthetic fixtures only.

Every figure, vendor and account here is invented. "Acme Robotics Inc." is not a
real company, "Northwind Insurance" is not a real vendor, and none of these
numbers came from anybody's books.

    python3 tests/test_entries.py
"""

from __future__ import annotations

import datetime as _dt
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "lib"))

from closethebooks.entries import (                                        # noqa: E402
    DraftError, Drafted, Question, add_months, straight_line_schedule, signed_move,
)
from closethebooks.entries import (                                        # noqa: E402
    intangibles, payroll, prepaid, reclass, stripe, wind_down,
)
from closethebooks.je_csv import write_entries                             # noqa: E402
from closethebooks.profile import Profile                                  # noqa: E402
from closethebooks.util import money                                       # noqa: E402

COMPANY = "Acme Robotics Inc."


def profile(**recurring) -> Profile:
    p = Profile()
    p.entity.name = COMPANY
    p.recurring_entries = dict(recurring)
    return p


PAYROLL_ACCOUNTS = {
    "wages": "6000", "employer_taxes": "6010", "benefits": "6020", "bank": "1000",
    "employer_tax_liability": "2120", "benefits_liability": "2130",
    "withholding": {"employee_income_tax": "2100", "employee_fica": "2105"},
}

PAY_PERIOD = {
    "pay_date": "2026-04-15",
    "gross_wages": "40000.00",
    "employer_taxes": "3060.00",
    "benefits": "1200.00",
    "net_pay": "28940.00",
    "withholdings": {"employee_income_tax": "8000.00", "employee_fica": "3060.00"},
    "source": "Payroll register 2026-04-15",
}

PREPAID_CONTRACT = {
    "vendor": "Northwind Insurance", "total": "12000.00", "start": "2026-01-01",
    "term_months": 12, "prepaid_account": "1400", "expense_account": "6300",
    "source": "Northwind policy NW-88213",
}

STRIPE_MONTH = {
    "month": "2026-04", "gross": "50000.00", "fees": "1500.00", "refunds": "800.00",
    "payouts": "46000.00", "stated_closing": "1700.00",
    "source": "Processor balance report 2026-04",
}
STRIPE_ACCOUNTS = {"clearing": "1250", "revenue": "4000", "fees": "6400",
                   "refunds": "4100", "bank": "1000"}


def balanced(entry) -> bool:
    entry.check()
    return entry.balanced and entry.total_debits() > money("0.00")


# ------------------------------------------------------------------ helpers

def test_add_months_clamps_to_a_short_month():
    assert add_months(_dt.date(2026, 1, 31), 1) == _dt.date(2026, 2, 28)
    assert add_months(_dt.date(2026, 12, 15), 2) == _dt.date(2027, 2, 15)


def test_a_schedule_sums_to_the_total_with_no_rounding_residual():
    rows = straight_line_schedule("10000.00", "2026-01-01", 3)
    assert [r["amount"] for r in rows] == [money("3333.33"), money("3333.33"), money("3333.34")]
    assert sum(r["amount"] for r in rows) == money("10000.00")


def test_signed_move_reads_the_sign_and_not_the_words():
    debit_balance = signed_move("1500", "1600", "250.00")
    assert debit_balance[0][:3] == ("1600", money("250.00"), money("0.00"))
    credit_balance = signed_move("3200", "2400", "-250.00")
    assert credit_balance[0][:3] == ("3200", money("250.00"), money("0.00"))
    assert credit_balance[1][:3] == ("2400", money("0.00"), money("250.00"))


# ------------------------------------------------------------------ payroll

def test_payroll_drafts_one_balanced_entry_per_pay_date():
    drafted = payroll.build(profile(payroll={"accounts": PAYROLL_ACCOUNTS}), [PAY_PERIOD])
    assert len(drafted) == 1
    entry = drafted[0]
    assert balanced(entry)
    assert entry.date == _dt.date(2026, 4, 15)
    assert entry.total_debits() == money("44260.00")      # gross + employer taxes + benefits


def test_payroll_exposes_withholdings_as_separate_liability_lines():
    drafted = payroll.build(profile(payroll={"accounts": PAYROLL_ACCOUNTS}), [PAY_PERIOD])
    credits = {account: credit for account, _d, credit, *_rest in drafted[0].lines if credit}
    assert credits["2100"] == money("8000.00")
    assert credits["2105"] == money("3060.00")
    assert credits["1000"] == money("28940.00")           # the bank sees only net pay
    assert "6020" not in credits                          # benefits expense is never a credit


def test_payroll_refuses_a_withholding_with_no_declared_liability_account():
    accounts = dict(PAYROLL_ACCOUNTS, withholding={"employee_income_tax": "2100"})
    try:
        payroll.build(profile(payroll={"accounts": accounts}), [PAY_PERIOD])
    except DraftError as exc:
        assert "employee_fica" in str(exc)
        assert "netting" in str(exc)
    else:
        raise AssertionError("a withholding with no liability account must be refused, not netted")


def test_payroll_refuses_to_plug_a_gross_that_does_not_equal_net_plus_withholdings():
    period = dict(PAY_PERIOD, net_pay="27000.00")
    try:
        payroll.build(profile(payroll={"accounts": PAYROLL_ACCOUNTS}), [period])
    except DraftError as exc:
        assert "1,940.00" in str(exc)                     # the difference, as a number
    else:
        raise AssertionError("an unexplained difference must be reported, not plugged")


def test_payroll_refuses_a_period_with_no_source():
    period = dict(PAY_PERIOD)
    period.pop("source")
    try:
        payroll.build(profile(payroll={"accounts": PAYROLL_ACCOUNTS}), [period])
    except DraftError as exc:
        assert "source" in str(exc)
    else:
        raise AssertionError("an entry with no basis must be refused")


# ------------------------------------------------------------------ prepaid

def test_prepaid_amortizes_one_entry_per_month():
    drafted = prepaid.build(profile(prepaid=[PREPAID_CONTRACT]), "2026-06-30")
    assert len(drafted) == 6
    assert all(balanced(e) for e in drafted)
    assert drafted[0].lines[0][1] == money("1000.00")
    assert drafted[0].date == _dt.date(2026, 1, 31)


def test_prepaid_reports_the_remaining_unamortized_balance():
    drafted = prepaid.build(profile(prepaid=[PREPAID_CONTRACT]), "2026-06-30")
    remaining = drafted.remaining[0]
    assert remaining["amortized_through"] == money("6000.00")
    assert remaining["remaining"] == money("6000.00")
    assert drafted.total_remaining == money("6000.00")


def test_prepaid_handles_a_contract_that_starts_mid_period():
    contract = dict(PREPAID_CONTRACT, start="2026-03-15", term_months=12)
    drafted = prepaid.build(profile(prepaid=[contract]), "2026-12-31")
    first = drafted[0]
    assert first.date == _dt.date(2026, 3, 31)
    # 17 of 31 days of a 1,000.00 month
    assert first.lines[0][1] == money("548.39")
    assert "17 of 31 days" in first.memo


def test_prepaid_handles_a_term_that_ends_inside_the_window():
    contract = dict(PREPAID_CONTRACT, start="2026-01-01", term_months=3, total="10000.00")
    drafted = prepaid.build(profile(prepaid=[contract]), "2026-06-30")
    assert len(drafted) == 3                              # nothing after the term ends
    assert sum(e.lines[0][1] for e in drafted) == money("10000.00")
    assert drafted.remaining[0]["remaining"] == money("0.00")
    assert drafted.remaining[0]["fully_amortized"] is True


def test_prepaid_refuses_a_contract_with_no_source():
    contract = dict(PREPAID_CONTRACT)
    contract.pop("source")
    try:
        prepaid.build(profile(prepaid=[contract]), "2026-06-30")
    except DraftError as exc:
        assert "source" in str(exc)
    else:
        raise AssertionError("a contract with no source must be refused")


# -------------------------------------------------------------- intangibles

def test_intangibles_credit_accumulated_amortization_not_the_asset():
    asset = {"asset": "Acquired customer list", "cost": "60000.00", "in_service": "2026-01-01",
             "life_months": 60, "asset_account": "1700", "accumulated_account": "1710",
             "expense_account": "6800", "source": "Asset purchase agreement, schedule 2.1"}
    drafted = intangibles.build(profile(intangibles=[asset]), "2026-06-30")
    assert len(drafted) == 6
    assert all(balanced(e) for e in drafted)
    assert drafted[0].lines[0][0] == "6800"
    assert drafted[0].lines[1][0] == "1710"
    assert drafted.remaining[0]["accumulated_through"] == money("6000.00")
    assert drafted.remaining[0]["net_book_value"] == money("54000.00")


def test_intangibles_refuse_without_an_accumulated_account():
    asset = {"asset": "Acquired customer list", "cost": "60000.00", "in_service": "2026-01-01",
             "life_months": 60, "asset_account": "1700", "expense_account": "6800",
             "source": "Asset purchase agreement"}
    try:
        intangibles.build(profile(intangibles=[asset]), "2026-06-30")
    except DraftError as exc:
        assert "accumulated_account" in str(exc)
    else:
        raise AssertionError("amortization must never credit the asset itself")


# ------------------------------------------------------------------- stripe

def test_stripe_books_gross_revenue_not_the_deposit():
    drafted = stripe.build(profile(stripe={"accounts": STRIPE_ACCOUNTS}), [STRIPE_MONTH], "2026-06-30")
    entry = drafted[0]
    assert balanced(entry)
    credits = {}
    for account, _debit, credit, *_rest in entry.lines:
        if credit:
            credits[account] = credits.get(account, money("0.00")) + credit
    assert credits["4000"] == money("50000.00")           # revenue is gross, not the payout
    debits = {}
    for account, debit, _credit, *_rest in entry.lines:
        if debit:
            debits[account] = debits.get(account, money("0.00")) + debit
    assert debits["6400"] == money("1500.00")
    assert debits["4100"] == money("800.00")
    assert debits["1000"] == money("46000.00")


def test_stripe_exposes_the_clearing_residual():
    drafted = stripe.build(profile(stripe={"accounts": STRIPE_ACCOUNTS}), [STRIPE_MONTH], "2026-06-30")
    residual = drafted.residuals[0]
    assert residual["computed_closing"] == money("1700.00")
    assert residual["difference"] == money("0.00")        # stated, including the zero
    assert residual["checkable"] is True
    assert drafted.clearing_residual == money("1700.00")
    assert any("does not return to 0.00" in n for n in drafted.notes)


def test_stripe_marks_a_month_with_no_stated_balance_as_unchecked():
    month = dict(STRIPE_MONTH)
    month.pop("stated_closing")
    drafted = stripe.build(profile(stripe={"accounts": STRIPE_ACCOUNTS}), [month], "2026-06-30")
    residual = drafted.residuals[0]
    assert residual["checkable"] is False
    assert residual["difference"] is None                 # not 0.00
    assert "unchecked" in residual["note"]


def test_stripe_refuses_refunds_with_no_contra_revenue_account():
    accounts = dict(STRIPE_ACCOUNTS)
    accounts.pop("refunds")
    try:
        stripe.build(profile(stripe={"accounts": accounts}), [STRIPE_MONTH], "2026-06-30")
    except DraftError as exc:
        assert "refund" in str(exc).lower()
    else:
        raise AssertionError("refunds must not be netted into revenue")


def test_stripe_refuses_a_month_with_no_source():
    month = dict(STRIPE_MONTH)
    month.pop("source")
    try:
        stripe.build(profile(stripe={"accounts": STRIPE_ACCOUNTS}), [month], "2026-06-30")
    except DraftError as exc:
        assert "source" in str(exc)
    else:
        raise AssertionError("an entry with no basis must be refused")


# ------------------------------------------------------------------ reclass

MOVE = {"from_account": "3200", "to_account": "2400", "amount": "-250000.00",
        "reason": "the 2024 instrument is a SAFE and stays a liability until it converts",
        "effective_date": "2026-06-30", "source": "SAFE dated 2024-03-11, section 1(a)"}


def test_reclass_moves_a_credit_balance_the_right_way():
    drafted = reclass.build(Profile(), [MOVE])
    entry = drafted[0]
    assert balanced(entry)
    assert entry.lines[0][:3] == ("3200", money("250000.00"), money("0.00"))
    assert entry.lines[1][:3] == ("2400", money("0.00"), money("250000.00"))
    assert "SAFE" in entry.basis


def test_reclass_refuses_a_move_with_no_reason():
    move = dict(MOVE)
    move.pop("reason")
    try:
        reclass.build(Profile(), [move])
    except DraftError as exc:
        assert "reason" in str(exc)
    else:
        raise AssertionError("a reclassification with no reason must be refused")


def test_reclass_refuses_a_move_of_nothing_and_a_move_to_itself():
    for broken, expect in (
        (dict(MOVE, amount="0.00"), "0.00"),
        (dict(MOVE, to_account="3200"), "itself"),
    ):
        try:
            reclass.build(Profile(), [broken])
        except DraftError as exc:
            assert expect in str(exc)
        else:
            raise AssertionError(f"expected a refusal mentioning {expect}")


# ---------------------------------------------------------------- wind down

WIND_DOWN = {
    "target_date": "2026-12-31",
    "receivable_plan": "settle the intercompany receivable at 40 cents",
    "safe_terms": "return of purchase amount, no preference",
    "cap_table": "founders 80, 2024 SAFE 20",
    "resolution_date": "2026-12-15",
    "accounts": {"bank": "1000", "retained_earnings": "3400", "write_off": "6900",
                 "gain_on_extinguishment": "4900"},
    "intercompany": {"account": "1500", "book_balance": "50000.00", "cash_expected": "20000.00",
                     "authority": "founder answer WD-2, 2026-11-01"},
    "instruments": [{"name": "2024 SAFE", "account": "2400", "book_balance": "-250000.00",
                     "cash_paid": "50000.00", "authority": "founder answer WD-4, 2026-11-03",
                     "instrument_on_file": "SAFE dated 2024-03-11, section 3(b)"}],
    "distributions": [{"account": "3100", "amount": "75000.00", "to": "common holders",
                       "authority": "board resolution 2026-12-15"}],
    "closing": [{"account": "3300", "balance": "-12000.00", "to": "3400",
                 "authority": "founder answer WD-7, 2026-12-20"}],
}


def wind_down_profile(**overrides) -> Profile:
    p = Profile()
    p.entity.name = COMPANY
    p.wind_down = dict(WIND_DOWN, **overrides)
    return p


def test_wind_down_refuses_and_names_exactly_which_answers_are_missing():
    p = Profile()
    p.wind_down = {"target_date": "2026-12-31", "cap_table": "founders 100"}
    try:
        wind_down.build(p)
    except DraftError as exc:
        named = str(exc).splitlines()[0]
        assert "receivable_plan" in named and "safe_terms" in named and "resolution_date" in named
        # the two that ARE answered are not listed as missing
        assert "target_date" not in named and "cap_table" not in named
    else:
        raise AssertionError("wind-down entries must be refused until every answer is in")


def test_wind_down_drafts_every_treatment_when_it_is_answered():
    drafted = wind_down.build(wind_down_profile())
    assert len(drafted) == 4
    assert drafted.questions == []
    assert all(balanced(e) for e in drafted)
    assert all("founder answer" in e.basis for e in drafted)
    settlement = drafted[0]
    assert settlement.lines[0][:3] == ("1000", money("20000.00"), money("0.00"))
    assert settlement.lines[1][:3] == ("6900", money("30000.00"), money("0.00"))


def test_wind_down_asks_rather_than_guessing_when_the_instrument_is_not_held():
    instrument = dict(WIND_DOWN["instruments"][0])
    instrument.pop("instrument_on_file")
    drafted = wind_down.build(wind_down_profile(instruments=[instrument]))
    assert len(drafted) == 3                              # the SAFE entry was NOT drafted
    assert len(drafted.questions) == 1
    question = drafted.questions[0]
    assert isinstance(question, Question)
    assert "2024 SAFE" in question.blocks
    assert "instrument_on_file" in question.needs


def test_wind_down_asks_before_a_distribution_nobody_authorised():
    distribution = dict(WIND_DOWN["distributions"][0])
    distribution.pop("authority")
    drafted = wind_down.build(wind_down_profile(distributions=[distribution]))
    assert len(drafted.questions) == 1
    assert "distribution" in drafted.questions[0].blocks
    assert not any(e.kind == "wind_down" and "distribution" in e.memo.lower() for e in drafted)


# -------------------------------------------------------- the batch is real

def test_every_generator_produces_a_batch_the_journal_writer_accepts():
    import tempfile
    drafted = Drafted(
        list(payroll.build(profile(payroll={"accounts": PAYROLL_ACCOUNTS}), [PAY_PERIOD]))
        + list(prepaid.build(profile(prepaid=[PREPAID_CONTRACT]), "2026-02-28"))
        + list(reclass.build(Profile(), [MOVE])),
        kind="mixed",
    )
    drafted.check_all()
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "batch.csv")
        rows = write_entries(path, list(drafted), resolve=lambda key: f"Chart:{key}",
                             batch_tag="test_batch")
        assert rows == sum(len(e.lines) for e in drafted), rows
        text = open(path, encoding="utf-8").read()
        # each generator stamps its own batch tag, so one import can be voided per kind
        for tag in ("[batch-payroll]", "[batch-prepaid]", "[batch-reclass]"):
            assert tag in text, tag


def test_an_entry_with_no_basis_is_refused_by_the_model_itself():
    drafted = reclass.build(Profile(), [MOVE])
    entry = drafted[0]
    entry.basis = ""
    try:
        entry.check()
    except ValueError as exc:
        assert "basis" in str(exc)
    else:
        raise AssertionError("ProposedEntry.check() must refuse an entry with no basis")


def _run():
    tests = [(n, o) for n, o in sorted(globals().items())
             if n.startswith("test_") and callable(o)]
    failures = 0
    for name, fn in tests:
        try:
            fn()
            print(f"  ok   {name}")
        except Exception as exc:                          # noqa: BLE001
            failures += 1
            print(f"  FAIL {name}: {type(exc).__name__}: {exc}")
    print(f"\n{len(tests) - failures} passed, {failures} failed")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(_run())
