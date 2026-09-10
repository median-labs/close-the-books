"""A balance is shown on its account's own side, and never as a negative number.

The tool reported

    340000 SAFE Notes    -3,118,447.25

which says the company holds negative SAFE notes. It does not; it owes
3,118,447.25, and the exported trial balance prints exactly that figure in its
Credit column. A tool that prints a liability as a negative asset looks broken
before it has said anything useful, and people stop trusting the numbers that
are right.

The internal convention is unchanged and `test_balances.py` still pins it: the
engine is debit-positive and a trial balance foots to 0.00. What changed is the
rendering, and `test_a_credit_normal_balance_is_never_rendered_negative` is the
test that fails if debit-positive ever leaks back into something a person reads.

The second half of this file is the finding. An account carrying a balance on
the side opposite its normal one is usually the most important thing in the
file. Here `101000 Operating Checking` closes 2025 with 18,204.67 on the CREDIT
side, having carried 65,520.12 on the debit side a year earlier, and a checking
account cannot hold a credit balance. Rendered debit-positive that is one minus
sign among many. This is the shape a real file was carrying when the defect was
found; the figures below are invented.

    python3 tests/test_sides.py
"""

from __future__ import annotations

import datetime
import os
import re
import sys
import tempfile
from decimal import Decimal

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "lib"))
sys.path.insert(0, HERE)

from closethebooks import sides                                     # noqa: E402
from closethebooks.exit_tests import run as run_exit_tests          # noqa: E402
from closethebooks.model import Account                             # noqa: E402
from closethebooks.qbo_exports import load_all                      # noqa: E402
from closethebooks.recon import reconcile                           # noqa: E402

import test_balances as company                                     # noqa: E402

D = Decimal

try:
    import pytest

    @pytest.fixture(name="tmp")
    def _tmp_dir(tmp_path):
        return str(tmp_path)

except ImportError:                     # pragma: no cover - pytest is optional
    pytest = None


# Anything that reads as a negative amount to a person: a leading minus, a
# unicode minus, or accounting parentheses. This is the shape a balance must
# never take in a rendered report.
NEGATIVE_MONEY = re.compile(r"[-−]\s?\d[\d,]*\.\d{2}|\(\d[\d,]*\.\d{2}\)")


def _ledger(tmp):
    return load_all(company.build(os.path.join(tmp, "exports")))


def _reports(tmp):
    ledger = _ledger(tmp)
    prof = company._profile()
    recon = reconcile(ledger, [], prof, period_start="2024-01-01",
                      period_end="2025-12-31")
    tests = run_exit_tests(ledger, [], prof, recon)
    return ledger, prof, recon, tests


# ------------------------------------------------------------- the rendering

def test_the_normal_side_of_every_type_the_engine_knows():
    for account_type in ("Bank", "Banks", "Accounts Receivable (A/R)", "Fixed Assets",
                         "Other Current Assets", "Expenses", "Cost of Goods Sold"):
        assert sides.normal_side_of_type(account_type) == sides.DEBIT, account_type
    for account_type in ("Accounts Payable (A/P)", "Credit Card", "Credit Cards",
                         "Other Current Liabilities", "Long Term Liabilities",
                         "Equity", "Income", "Other Income"):
        assert sides.normal_side_of_type(account_type) == sides.CREDIT, account_type
    # An unknown type has no normal side, and inventing one would make every
    # wrong-side finding about it unsupportable.
    assert sides.normal_side_of_type("Widgets") == ""
    assert sides.normal_side_of_type("") == ""


def test_a_balance_renders_as_a_magnitude_and_a_side():
    equity = Account(name="SAFE Notes", number="340000", type="Equity")
    bank = Account(name="Operating Checking", number="101000", type="Bank")
    assert sides.balance_text(D("-3118447.25"), equity) == "3,118,447.25 Cr"
    assert sides.balance_text(D("65520.12"), bank) == "65,520.12 Dr"
    # Zero sits on neither side, and "0.00 Cr" would invite a reader to think
    # something is there.
    assert sides.balance_text(D("0.00"), equity) == "0.00"
    # A balance that could not be worked out is a different answer from zero.
    assert sides.balance_text(None, equity) == sides.UNDETERMINED


def test_a_credit_normal_balance_is_never_rendered_negative(tmp):
    """The regression guard. If debit-positive leaks into a report, this fails.

    Every document a person reads is rendered here and swept for anything that
    looks like a negative amount: a minus sign, a unicode minus, or accounting
    parentheses. The DIFFERENCE column is exempt by construction, because none
    of these reports has a non-zero difference to print; a difference is not a
    balance and keeps its sign deliberately.
    """
    ledger, prof, recon, tests = _reports(tmp)

    # The account that started this: 3,118,447.25 owed, no activity in either
    # exported year, and it used to print as -3,118,447.25.
    safe = recon.row("340000", "2025-12")
    assert safe is not None
    assert safe.book == D("-3118447.25"), "the arithmetic is unchanged and stays signed"
    assert safe.book_cell() == "3,118,447.25 Cr"

    for text, what in ((recon.render_markdown(), "the reconciliation report"),
                       (tests.render_markdown(), "the exit tests report")):
        found = NEGATIVE_MONEY.findall(text)
        assert not found, f"{what} renders a balance as a negative number: {found[:5]}"

    # And cell by cell, so a future column cannot slip past the sweep above by
    # rendering into a document this test does not read.
    for row in recon.rows:
        for cell in (row.book_cell(), row.statement_cell()):
            assert not NEGATIVE_MONEY.search(cell), f"{row.account} {row.month}: {cell}"


def test_the_five_pinned_balances_render_on_their_own_side(tmp):
    """The same five figures `test_balances.py` pins, as a person reads them."""
    ledger = _ledger(tmp)
    expected = {
        "101000": "18,204.67 Cr",       # a bank account on the WRONG side
        "102000": "2,416.24 Dr",
        "104000": "631.85 Dr",
        "174000": "1,655,761.81 Dr",
        "340000": "3,118,447.25 Cr",
    }
    stated = ledger.trial_balance_as_of(datetime.date(2025, 12, 31)).by_key()
    for key, rendered in expected.items():
        account = ledger.account(key)
        balance = ledger.balance_of(key)
        # Still the cent-exact figure the trial balance states. The rendering
        # layer must not be able to change an amount, only how it is written.
        assert balance == stated[key], key
        assert balance == company.TRUE_BALANCES[key], key
        assert sides.balance_text(balance, account) == rendered, key


def test_a_difference_keeps_its_sign_because_it_is_not_a_balance():
    """A difference of -100.00 is not "100.00 Cr". Its sign says which way."""
    from closethebooks.util import fmt
    assert fmt(D("-100.00")) == "(100.00)"
    assert sides.balance_text(D("-100.00")) == "100.00 Cr"


# --------------------------------------------------------------- the finding

def test_a_bank_account_on_the_credit_side_is_a_named_finding(tmp):
    ledger = _ledger(tmp)
    scanned = sides.scan(ledger)
    found = {f.key: f for f in scanned}
    assert "101000" in found, "a bank account with a credit balance must be surfaced"

    finding = found["101000"]
    assert finding.impossible, "a bank account cannot hold a credit balance"
    assert finding.normal == sides.DEBIT
    assert finding.sitting == sides.CREDIT
    assert finding.rendered() == "18,204.67 Cr"
    # It was on the right side a year earlier, which is the fact that tells a
    # reader this went wrong inside the period rather than being inherited.
    assert finding.prior == D("65520.12")
    assert finding.prior_was_normal

    text = " ".join(finding.lines())
    assert "18,204.67 Cr" in text
    assert "65,520.12 Dr" in text
    assert "debit-normal" in text
    # It names what it could be, both ways, and it does NOT pick one.
    assert "missing" in text.lower() or "not recorded" in text.lower()
    assert "overdrawn" in text
    assert "statement" in text
    for claim in ("is overdrawn", "the books are missing", "this means",
                  "caused by", "because the"):
        assert claim not in text.lower(), f"the finding asserts a cause: {claim!r}"


def test_the_finding_reaches_every_report_a_person_reads(tmp):
    ledger, prof, recon, tests = _reports(tmp)
    markdown = tests.render_markdown()
    assert "Balances on the wrong side" in markdown
    assert "101000" in markdown and "18,204.67 Cr" in markdown
    assert "the bank statement" in markdown

    # And it is on the report object, so the handoff document and the terminal
    # render the same finding without scanning a second time.
    assert tests.wrong_side is not None
    assert any(f.key == "101000" for f in tests.wrong_side)


def test_an_account_on_its_normal_side_is_not_a_finding(tmp):
    ledger = _ledger(tmp)
    scanned = sides.scan(ledger)
    for key in ("102000", "104000", "174000", "340000"):
        assert not any(f.key == key for f in scanned), (
            f"{key} holds its balance on its own normal side")
    assert scanned.checked >= 4


def test_an_account_whose_type_is_unknown_is_excluded_rather_than_guessed(tmp):
    """No normal side means no finding. A guess here would be unsupportable."""
    ledger = _ledger(tmp)
    account = ledger.account("174000")
    account.type = "widgets"
    scanned = sides.scan(ledger)
    assert not any(f.key == "174000" for f in scanned)
    assert any(label.startswith("174000") for label, _ in scanned.excluded)


def test_a_rollup_and_an_unused_template_row_are_never_findings(tmp):
    """The same filter the reconciliation uses. See `model.is_real_account`."""
    ledger = _ledger(tmp)
    scanned = sides.scan(ledger)
    keys = {f.key for f in scanned}
    for key in ("100000", "170000", "220000", "300000", "107000", "111000", "223000"):
        assert key not in keys, key


def test_retained_earnings_on_the_debit_side_is_not_reported_as_a_defect(tmp):
    """An accumulated deficit is the ordinary condition of an unprofitable company.

    Reporting it trains a reader to skim the list that also holds the checking
    account, which is the one they must not skim. It is named in `excluded`
    rather than dropped, so nothing disappears silently.
    """
    ledger = _ledger(tmp)
    scanned = sides.scan(ledger)
    assert not any("retained earnings" in f.label.lower() for f in scanned)
    assert any("retained earnings" in label.lower() for label, _ in scanned.excluded)


def test_the_wrong_side_check_reports_but_does_not_fail_exit_test_5(tmp):
    """Only a bank or card on the wrong side fails. The rest are surfaced.

    A receivable with a credit balance is a customer overpayment often enough
    to be ordinary, and failing on it would bury the checking account.
    """
    ledger, prof, recon, tests = _reports(tmp)
    test5 = tests.get(5)
    assert not test5.passed, "the checking account's credit balance still fails test 5"
    assert test5.measured["impossible balances"] == 1
    assert test5.measured["balance-sheet accounts swept for a wrong-side balance"] >= 4
    assert "18,204.67 Cr" in test5.measured_text()
    # The engine's own signed figure is still what a caller asserts against;
    # only the string beside it changed.
    key = next(k for k in test5.measured if str(k).startswith("101000"))
    assert test5.measured[key] == D("-18204.67")
    assert test5.presented[key] == "18,204.67 Cr"


def _run():
    tests = [(n, o) for n, o in sorted(globals().items())
             if n.startswith("test_") and callable(o)]
    failures = 0
    for name, fn in tests:
        with tempfile.TemporaryDirectory(prefix="close-the-books-sides-") as tmp:
            try:
                fn(tmp) if fn.__code__.co_argcount else fn()
                print(f"  ok   {name}")
            except Exception as exc:                      # noqa: BLE001
                failures += 1
                print(f"  FAIL {name}: {type(exc).__name__}: {exc}")
    print(f"\n{len(tests) - failures} passed, {failures} failed")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(_run())
