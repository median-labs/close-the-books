"""Reconciliation and exit-test tests. Synthetic fixtures only.

"Acme Robotics Inc." is invented, and so is every account, date and amount here.

Each exit test is run twice: once over books that are actually finished, and
once over the same books with exactly one thing broken. The second half also
asserts that the MEASURED NUMBER reaches the rendered report, because a report
that says FAIL without saying by how much sends the reader back into the source.

    python3 tests/test_exit_tests.py
"""

from __future__ import annotations

import datetime as _dt
import os
import sys
from decimal import Decimal

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "lib"))

from closethebooks.exit_tests import Attestation, ExitTestReport, run                 # noqa: E402
from closethebooks.model import Account, JournalLine, Ledger, ProposedEntry           # noqa: E402
from closethebooks.profile import AccountSpec, Profile                               # noqa: E402
from closethebooks.qbo_exports import TrialBalance                                   # noqa: E402
from closethebooks.recon import CANNOT_CHECK, reconcile                              # noqa: E402
from closethebooks.statements import StatementFile                                   # noqa: E402
from closethebooks.util import ZERO, money, month_key                                # noqa: E402

COMPANY = "Acme Robotics Inc."
START = _dt.date(2026, 1, 1)
END = _dt.date(2026, 3, 31)
MONTHS = ("2026-01", "2026-02", "2026-03")

CHART = (
    ("1000", "Operating bank", "Bank", "bank"),
    ("1200", "Accounts receivable", "Accounts Receivable (A/R)", "ar"),
    ("1250", "Payment processor clearing", "Other Current Assets", "clearing"),
    ("2000", "Company card", "Credit Card", "card"),
    ("3000", "Opening Balance Equity", "Equity", "obe"),
    ("3400", "Retained earnings", "Equity", "equity"),
    ("4000", "Product revenue", "Income", "revenue"),
    ("6000", "Operating expenses", "Expenses", "expense"),
)


def accounts() -> dict:
    out = {}
    for number, name, kind, role in CHART:
        account = Account(name=name, number=number, full_name=f"Chart:{name}",
                          type=kind, role=role)
        out[account.key] = account
    return out


def line(day, month, account, debit=ZERO, credit=ZERO, memo="", txn_type="Expense",
         klass="Operations"):
    return JournalLine(date=_dt.date(2026, month, day), account=account,
                       debit=money(debit), credit=money(credit), memo=memo,
                       txn_type=txn_type, klass=klass)


def ledger_lines() -> list:
    rows = []
    for month in (1, 2, 3):
        rows += [
            line(5, month, "1000", debit="10000.00", memo="Customer deposit", txn_type="Deposit"),
            line(5, month, "4000", credit="10000.00", memo="Customer deposit", txn_type="Deposit"),
            line(12, month, "6000", debit="2000.00", memo="Rent"),
            line(12, month, "1000", credit="2000.00", memo="Rent"),
            line(20, month, "6000", debit="500.00", memo="Software on the card"),
            line(20, month, "2000", credit="500.00", memo="Software on the card"),
        ]
    # A clearing account that is used and then emptied, which is what a healthy
    # one looks like: the balance exists inside the month and not at the end of it.
    rows += [
        line(6, 1, "1250", debit="1000.00", memo="Processor holds a payout",
             txn_type="Journal Entry"),
        line(6, 1, "4000", credit="1000.00", memo="Processor holds a payout",
             txn_type="Journal Entry"),
        line(28, 1, "1000", debit="1000.00", memo="Processor paid out", txn_type="Journal Entry"),
        line(28, 1, "1250", credit="1000.00", memo="Processor paid out", txn_type="Journal Entry"),
    ]
    return rows


def balance_at(lines, account_key, month) -> Decimal:
    cutoff = month
    total = ZERO
    for l in lines:
        if l.account == account_key and month_key(l.date) <= cutoff:
            total = money(total + l.signed)
    return total


# What each account carried INTO the period, as a General Ledger's own
# "Beginning Balance" rows state it. These foot to 0.00, because a set of
# opening balances is a trial balance as of the first day and a trial balance
# foots. Accounts absent from here opened at 0.00, which is what QuickBooks
# means by printing no Beginning Balance row for them.
OPENING = {
    "1200": money("40000.00"),      # an invoice raised in the prior year
    "3400": money("-40000.00"),     # and the prior year's result it sits against
}
OPENING_BASIS = ("the Beginning Balance rows of Acme Robotics Inc_General Ledger.xlsx, "
                 "as of 2026-01-01")


def ledger(lines=None, opening="default", opening_basis="default",
           trial_balances="default") -> Ledger:
    lines = ledger_lines() if lines is None else lines
    opening = dict(OPENING) if opening == "default" else dict(opening or {})
    basis = OPENING_BASIS if opening_basis == "default" else opening_basis
    led = Ledger(accounts=accounts(), lines=list(lines), company=COMPANY,
                 basis="accrual", period_start=START, period_end=END,
                 opening_balances=opening, opening_basis=basis)
    led.trial_balances = ([trial_balance(led)] if trial_balances == "default"
                          else list(trial_balances or []))
    return led


def trial_balance(led, overrides=None) -> TrialBalance:
    """The trial balance QuickBooks would export for these books at END.

    Built from the ledger's own balances, so `clean()` books tie exactly and a
    test can break exactly one figure to prove the tie is real.
    """
    overrides = dict(overrides or {})
    rows, debits, credits = [], ZERO, ZERO
    for key, account in sorted(led.accounts.items()):
        balance = overrides.get(key, led.balance_as_of(key, END))
        if balance is None or balance == ZERO:
            continue
        debit = balance if balance > ZERO else ZERO
        credit = -balance if balance < ZERO else ZERO
        rows.append((f"{key} {account.full_name}", money(debit), money(credit)))
        debits += debit
        credits += credit
    return TrialBalance(as_of=END, basis="accrual", rows=rows,
                        total_debits=money(debits), total_credits=money(credits),
                        foots=debits == credits,
                        source_file="Acme+Robotics+Inc_Trial+Balance.xlsx")


def statements(lines=None, skip=()) -> list:
    """One statement per externally verifiable account per month, tying exactly."""
    lines = ledger_lines() if lines is None else lines
    out = []
    for account_key in ("1000", "2000"):
        for i, month in enumerate(MONTHS, start=1):
            if (account_key, month) in skip:
                continue
            out.append(StatementFile(
                account_key=account_key,
                period_start=_dt.date(2026, i, 1),
                period_end=_dt.date(2026, i, 28),
                opening_balance=balance_at(lines, account_key, MONTHS[i - 2]) if i > 1 else ZERO,
                closing_balance=balance_at(lines, account_key, month),
                lines=[],
                source_file=f"acme-{account_key}-{month}.pdf",
                parser="test",
            ))
    return out


def profile(**overrides) -> Profile:
    p = Profile()
    p.entity.name = COMPANY
    p.entity.basis = "accrual"
    p.accounts = [
        AccountSpec(book="1000", label="Operating bank", kind="bank"),
        AccountSpec(book="2000", label="Company card", kind="card"),
    ]
    p.decisions_made = [
        {"id": "D-1", "classes_in_use": True, "source": "founder answer 2026-01-04"},
        {"id": "D-2", "inception_date": "2024-02-01", "source": "certificate of incorporation"},
    ]
    for key, value in overrides.items():
        setattr(p, key, value)
    return p


def clean(**kwargs):
    """(ledger, statements, profile, recon) for books that are actually finished."""
    lines = kwargs.pop("lines", None) or ledger_lines()
    prof = kwargs.pop("prof", None) or profile()
    skip = kwargs.pop("skip", ())
    led = ledger(lines)
    stmts = statements(lines, skip=skip)
    recon = reconcile(led, stmts, prof, period_start=START, period_end=END)
    return led, stmts, prof, recon


def report(**kwargs) -> ExitTestReport:
    entries = kwargs.pop("entries", None)
    attestation = kwargs.pop("attestation", "default")
    led, stmts, prof, recon = clean(**kwargs)
    if attestation == "default":
        attestation = Attestation(statement="Nothing is sitting unbooked; every receipt is in.",
                                  by="A. Founder", on="2026-04-02", unbooked_count=0)
    if entries is None:
        entries = [ProposedEntry(date=END, number="RC-01", lines=[("6000", money("1.00"), ZERO, "")],
                                 basis="founder answer 2026-04-01", memo="x", batch_tag="t")]
    return run(led, stmts, prof, recon, attestation=attestation, entries=entries)


# ------------------------------------------------------------ reconciliation

def test_recon_states_a_zero_difference_rather_than_hiding_the_row():
    _led, _s, _p, recon = clean()
    row = recon.row("1000", "2026-01")
    assert row.checkable is True
    assert row.difference == money("0.00")
    assert row.difference_cell() == "0.00"
    assert row.book == money("9000.00")
    assert row.statement == money("9000.00")
    assert row in recon.reconciled
    assert row not in recon.unreconciled


def test_recon_covers_every_REAL_balance_carrying_account_for_every_month():
    """Every balance-sheet account that exists, and nothing that only exists in
    the chart. 3000 Opening Balance Equity has never been posted to and carries
    no balance, so it is not an open item and does not get three rows demanding
    a statement nobody can produce."""
    _led, _s, _p, recon = clean()
    expected = ["1000", "1200", "1250", "2000", "3400"]
    assert sorted(recon.accounts) == expected
    assert "3000" not in recon.accounts
    assert len(recon.rows) == len(expected) * len(MONTHS)
    assert any("never been posted to" in n for n in recon.notes), (
        "an excluded account has to be named, not silently dropped")


def test_recon_marks_a_month_with_no_statement_as_unchecked_not_as_zero():
    _led, _s, _p, recon = clean(skip=(("1000", "2026-03"),))
    row = recon.row("1000", "2026-03")
    assert row.checkable is False
    assert row.difference is None
    assert row.difference_cell() == CANNOT_CHECK
    assert row in recon.unchecked
    assert row in recon.unchecked_but_expected
    assert row not in recon.unreconciled          # a different problem, a different list


def test_recon_separates_an_account_that_can_never_have_a_statement():
    _led, _s, _p, recon = clean()
    equity = recon.row("3400", "2026-02")
    assert equity.checkable is False
    assert equity.externally_verifiable is False
    assert equity in recon.unchecked
    assert equity not in recon.unchecked_but_expected


def test_recon_reports_a_difference_as_a_number():
    lines = ledger_lines()
    stmts = statements(lines)
    for s in stmts:
        if s.account_key == "1000" and s.month == "2026-02":
            s.closing_balance = money("16000.00")     # book says 17,000.00
    led, prof = ledger(lines), profile()
    recon = reconcile(led, stmts, prof, period_start=START, period_end=END)
    row = recon.row("1000", "2026-02")
    assert row.difference == money("1000.00")
    assert row in recon.unreconciled
    text = recon.render_markdown()
    assert "1,000.00" in text
    assert "is not plugged" in text


def test_recon_markdown_shows_the_zeros_and_the_cannot_check_cells():
    _led, _s, _p, recon = clean(skip=(("2000", "2026-03"),))
    text = recon.render_markdown()
    assert CANNOT_CHECK in text
    assert "| 0.00 |" in text
    assert "Statements we do not have" in text


def test_recon_evidence_rows_carry_the_zeros():
    _led, _s, _p, recon = clean()
    rows = recon.evidence_rows()
    assert rows, "the evidence rows must include the ties, not only the breaks"
    assert all(r["difference"] is not None for r in rows)
    assert any(r["difference"] == ZERO for r in rows)


# ---------------------------------------------------------------- exit tests

def test_finished_books_pass_all_ten():
    result = report()
    assert len(result.tests) == 10
    assert result.failed == [], [t.describe() for t in result.failed]
    assert bool(result) is True
    text = result.render_markdown()
    assert "10 of 10 pass" in text
    assert "1,284" not in text                    # nothing invented
    assert "debits 39,500.00" in text             # the measured number, not just a tick


def test_1_measures_the_exported_trial_balance_and_not_the_ledger_turnover():
    """The figure test 1 prints has to be the trial balance's own total.

    It used to print the ledger's debits, which on a real export read the
    ledger's whole turnover against a much smaller trial balance total. A
    general ledger foots by construction, so that version could never fail.
    """
    result = report()
    test = result.get(1)
    assert test.passed, test.describe()
    led = ledger()
    assert test.measured["trial balance total"] == led.trial_balances[0].total_debits
    assert test.measured["trial balance total"] != led.total_debits()
    assert "Acme+Robotics+Inc_Trial+Balance.xlsx" in str(test.measured["trial balance"])
    assert test.measured["accounts tied one by one"] == "3 of 3"


def test_1_fails_when_the_trial_balance_and_the_ledger_disagree():
    """One account moved by 1,000.00 on the trial balance and nowhere else."""
    led = ledger()
    true_balance = led.balance_as_of("1000", END)
    led.trial_balances = [trial_balance(led, overrides={"1000": true_balance + money("1000.00")})]
    prof = profile()
    recon = reconcile(led, statements(), prof, period_start=START, period_end=END)
    test = run(led, statements(), prof, recon).get(1)
    assert not test.passed
    assert test.measured["accounts that disagree"] == 1
    assert "1,000.00" in test.measured_text()
    assert "1000 Chart:Operating bank" in test.measured_text()
    assert "1000" in test.remedy


def test_1_fails_when_no_trial_balance_was_exported():
    """A check that could not run is not a pass. It says so and fails."""
    led = ledger(trial_balances=[])
    prof = profile()
    recon = reconcile(led, statements(), prof, period_start=START, period_end=END)
    test = run(led, statements(), prof, recon).get(1)
    assert not test.passed
    assert test.measured["trial balance"] == "none covering this period"
    assert "Trial Balance" in test.remedy
    assert "proves nothing" in test.detail


def test_1_fails_when_no_export_states_an_opening_balance():
    led = ledger(opening={}, opening_basis="")
    prof = profile()
    recon = reconcile(led, statements(), prof, period_start=START, period_end=END)
    test = run(led, statements(), prof, recon).get(1)
    assert not test.passed
    assert test.measured["ledger balances"] == "cannot be determined"


def test_1_fails_when_an_account_is_missing_from_the_ledger_entirely():
    """Both sides foot to 0.00 and every listed row still ties.

    A whole account dropped from the ledger takes its debit and its credit
    together, so nothing about the footing gives it away. The tie has to run
    over the union of both sides, not over the trial balance's rows alone.
    """
    # Both legs of every card transaction, so the remainder still foots.
    lines = [l for l in ledger_lines() if "Software on the card" not in l.memo]
    led = ledger(lines)
    led.trial_balances = [trial_balance(ledger())]        # the full books' own
    prof = profile()
    recon = reconcile(led, statements(), prof, period_start=START, period_end=END)
    test = run(led, statements(), prof, recon).get(1)
    assert test.measured["ledger difference"] == money("0.00"), (
        "the point of this case is that the footing check cannot see it")
    assert not test.passed
    assert test.measured["accounts that disagree"] == 1
    assert "1,500.00" in test.measured_text()


def test_1_fails_when_the_ledger_itself_does_not_foot():
    lines = ledger_lines() + [line(31, 3, "6000", debit="0.05", memo="stray")]
    led = ledger(lines)
    prof = profile()
    recon = reconcile(led, statements(), prof, period_start=START, period_end=END)
    result = run(led, statements(), prof, recon)
    test = result.get(1)
    assert not test.passed
    assert test.measured["ledger difference"] == money("0.05")
    assert "0.05" in result.render_markdown()


def test_2_a_missing_statement_fails_the_stated_difference_test():
    result = report(skip=(("1000", "2026-02"),))
    test = result.get(2)
    assert not test.passed
    assert test.measured["missing a statement we hold"] == 1
    assert test.measured["with a stated difference"] == test.measured["account-month cells"] - test.measured["cannot check"]
    assert "Operating bank" in test.remedy


def test_2_passes_when_every_account_month_is_addressed():
    test = report().get(2)
    assert test.passed
    assert test.measured["account-month cells"] == 15          # 5 real accounts, 3 months
    assert test.measured["with a stated difference"] == 6      # bank and card, three months
    assert test.measured["missing a statement we hold"] == 0


def test_3_opening_balance_equity_with_a_balance_fails():
    lines = ledger_lines() + [
        line(2, 1, "3000", credit="4200.00", memo="opening balance", txn_type="Journal Entry"),
        line(2, 1, "1000", debit="4200.00", memo="opening balance", txn_type="Journal Entry"),
    ]
    result = report(lines=lines, skip=())
    test = result.get(3)
    assert not test.passed
    # The engine holds it debit-positive; a reader sees it on its own side.
    # Opening balance equity is EQUITY, so a credit balance of 4,200.00 reads
    # "4,200.00 Cr" and never "(4,200.00)".
    assert test.measured["3000 Opening Balance Equity"] == money("-4200.00")
    assert "4,200.00 Cr" in result.render_markdown()
    assert "(4,200.00)" not in result.render_markdown()
    assert "reclassify" in test.remedy


def test_4_a_clearing_account_that_does_not_empty_fails():
    lines = [l for l in ledger_lines()
             if not (l.account in ("1250", "1000") and l.memo == "Processor paid out")]
    result = report(lines=lines)
    test = result.get(4)
    assert not test.passed
    assert test.measured["1250 Payment processor clearing"] == money("1000.00")
    assert "do not write the balance off" in test.remedy.lower()


def test_5_an_overdrawn_bank_account_fails_until_it_is_acknowledged():
    lines = ledger_lines() + [
        line(30, 3, "1000", credit="99000.00", memo="wire out", txn_type="Journal Entry"),
        line(30, 3, "6000", debit="99000.00", memo="wire out", txn_type="Journal Entry"),
    ]
    test = report(lines=lines).get(5)
    assert not test.passed
    assert test.measured["1000 Operating bank (bank)"] == money("-74000.00")
    assert test.measured["impossible balances"] == 1

    prof = profile()
    prof.decisions_made.append({"id": "D-3", "account": "1000",
                                "acknowledges": "the account really was overdrawn in March",
                                "source": "founder answer 2026-04-02"})
    acknowledged = report(lines=lines, prof=prof).get(5)
    assert acknowledged.passed
    assert acknowledged.measured["acknowledged in the profile"] == 1


def test_6_a_month_with_no_activity_fails_until_it_is_declared_dormant():
    lines = [l for l in ledger_lines() if l.date.month != 2]
    test = report(lines=lines).get(6)
    assert not test.passed
    assert test.measured["unexplained gaps"] == ["2026-02"]

    prof = profile()
    prof.decisions_made.append({"id": "D-4", "dormant_months": ["2026-02"],
                                "source": "founder answer: no trading in February"})
    declared = report(lines=lines, prof=prof).get(6)
    assert declared.passed
    assert declared.measured["declared dormant"] == ["2026-02"]
    assert declared.measured["empty months"] == 1


def test_7_a_line_with_no_class_fails_where_classes_are_in_use():
    lines = ledger_lines()
    lines[0].klass = ""
    lines[1].klass = "not specified"
    test = report(lines=lines).get(7)
    assert not test.passed
    assert test.measured["without a class"] == 2
    assert test.measured['literally "not specified"'] == 1


def test_7_passes_when_the_profile_does_not_declare_classes():
    prof = profile()
    prof.decisions_made = [d for d in prof.decisions_made if "classes_in_use" not in d]
    lines = ledger_lines()
    lines[0].klass = ""
    test = report(lines=lines, prof=prof).get(7)
    assert test.passed
    assert test.measured["classes in use"] is False


def test_8_an_entry_with_no_basis_fails_and_so_does_a_posted_one_with_no_memo():
    naked = ProposedEntry(date=END, number="RC-02",
                          lines=[("6000", money("1.00"), ZERO, "")], basis="")
    test = report(entries=[naked]).get(8)
    assert not test.passed
    assert test.measured["drafted with a basis"] == 0
    assert "RC-02" in test.remedy

    lines = ledger_lines() + [
        line(15, 3, "6000", debit="10.00", memo="", txn_type="Journal Entry"),
        line(15, 3, "1000", credit="10.00", memo="", txn_type="Journal Entry"),
    ]
    posted = report(lines=lines).get(8)
    assert not posted.passed
    assert posted.measured["posted with no memo at all"] >= 1


def test_9_unbooked_items_need_a_named_person_and_a_date():
    missing = report(attestation=None).get(9)
    assert not missing.passed
    assert missing.measured["unbooked items"] is None
    assert "in their own words" in missing.remedy

    partial = report(attestation={"statement": "all in", "unbooked_count": 2}).get(9)
    assert not partial.passed
    assert partial.measured["unbooked items"] == 2
    assert "2 item(s) are still unbooked" in partial.detail
    assert "nobody is named" in partial.detail


def test_9_passes_on_a_complete_attestation_and_keeps_the_words():
    test = report().get(9)
    assert test.passed
    assert test.measured["attested by"] == "A. Founder"
    assert test.measured["on"] == "2026-04-02"


def test_10_a_transaction_after_the_period_end_fails_with_its_date():
    lines = ledger_lines() + [
        line(15, 4, "6000", debit="75.00", memo="next period", txn_type="Journal Entry"),
        line(15, 4, "1000", credit="75.00", memo="next period", txn_type="Journal Entry"),
    ]
    test = report(lines=lines).get(10)
    assert not test.passed
    assert test.measured["dated after the period end"] == 2
    assert "2026-04-15" in test.measured["latest"]


def test_10_fails_when_the_inception_date_was_never_declared():
    prof = profile()
    prof.decisions_made = [d for d in prof.decisions_made if "inception_date" not in d]
    test = report(prof=prof).get(10)
    assert not test.passed
    assert test.measured["inception date"] is None
    assert "not a pass" in test.remedy


def test_10_catches_a_transaction_dated_before_the_company_existed():
    lines = ledger_lines() + [
        line(3, 1, "6000", debit="40.00", memo="typo in the year", txn_type="Journal Entry"),
        line(3, 1, "1000", credit="40.00", memo="typo in the year", txn_type="Journal Entry"),
    ]
    for l in lines[-2:]:
        l.date = _dt.date(2023, 1, 3)
    result = report(lines=lines)
    test = result.get(10)
    assert not test.passed
    assert test.measured["dated before the company existed"] == 2
    assert "2023-01-03" in test.measured["earliest"]


def test_the_report_prints_a_number_on_every_row_not_a_tick():
    text = report(skip=(("2000", "2026-01"),)).render_markdown()
    for number in range(1, 11):
        assert f"| {number} |" in text
    assert "(nothing measured)" not in text
    assert "What to do" in text


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
