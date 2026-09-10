"""A balance is an opening position plus the movement since it.

These tests exist because the tool reported PERIOD MOVEMENT as though it were a
balance. Every figure it printed was understated by whatever the account carried
into the period, silently, on a public tool, against real books. Every figure
below is invented. The shape is not:

    account                      true       reported          error
    101000 operating bank   -18,204.67     -60,109.89      41,905.22
    102000 second bank        2,416.24    -485,901.16     488,317.40
    104000 treasury             631.85    -812,028.90     812,660.75
    174000 intercompany   1,655,761.81     911,571.81     744,190.00
    340000 SAFE notes    -3,118,447.25           0.00   3,118,447.25

The SAFE liability is the shape that matters most: the account had no activity
in either exported year, so its movement was 0.00, and a liability in the
millions read as zero and passed every check the tool ran.

The workbooks below are synthetic and every company, account, vendor and figure
is invented, but the STRUCTURE is the real one: two General Ledger exports with
Beginning Balance rows, amounts signed in each account's own natural direction,
a chart whose balance column is stated as of the export date rather than the
period end, parent rollups, unused template rows, and a trial balance for each
year end. The five balances above are the ones pinned here.

    python3 tests/test_balances.py
"""

from __future__ import annotations

import datetime
import os
import sys
import tempfile
from decimal import Decimal

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "lib"))

import openpyxl  # noqa: E402

from closethebooks.exit_tests import run as run_exit_tests  # noqa: E402
from closethebooks.model import is_real_account  # noqa: E402
from closethebooks.profile import AccountSpec, Profile  # noqa: E402
from closethebooks.qbo_exports import load_all  # noqa: E402
from closethebooks.recon import reconcile  # noqa: E402

D = Decimal

try:
    import pytest

    @pytest.fixture(name="tmp")
    def _tmp_dir(tmp_path):
        return str(tmp_path)

except ImportError:                     # pragma: no cover - pytest is optional
    pytest = None


FOOTER = "Accrual Basis Monday, March 02, 2026 08:01 PM GMTZ"
FOOTER_TB = "Monday, Mar 02, 2026 12:01:05 PM GMT-8 - Accrual Basis"

# The five balances this file exists to pin, at 2025-12-31.
TRUE_BALANCES = {
    "101000": D("-18204.67"),
    "102000": D("2416.24"),
    "104000": D("631.85"),
    "174000": D("1655761.81"),
    "340000": D("-3118447.25"),
}
# What the old code reported for the same accounts: the lines alone.
MOVEMENT_ONLY = {
    "101000": D("-60109.89"),
    "102000": D("-485901.16"),
    "104000": D("-812028.90"),
    "174000": D("911571.81"),
    "340000": D("0.00"),
}
OPENING_AT_2024_01_01 = {
    "Retained Earnings": D("1031373.88"),
    "101000": D("41905.22"),
    "102000": D("488317.40"),
    "104000": D("812660.75"),
    "174000": D("744190.00"),
    "340000": D("-3118447.25"),
}


def write_sheet(path, rows, sheet_title="Sheet1"):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = sheet_title
    for row in rows:
        ws.append(list(row))
    os.makedirs(os.path.dirname(path), exist_ok=True)
    wb.save(path)
    return path


# --------------------------------------------------------------- the chart
#
# Column 6 is "Total balance", which QuickBooks states as of the moment the
# export ran and NOT as of the period end. 101000 and 174000 carry eight months
# of 2026 activity in it, which is exactly why it is not the source of a
# balance: it is right for the three accounts that stopped moving and wrong for
# the two that did not.
ACCOUNT_LIST = [
    ["Account List", "", "", "", "", ""],
    ["Northwind Instruments Inc.", "", "", "", "", ""],
    ["", "", "", "", "", ""],
    ["Account #", "Full name", "Type", "Detail type", "Description", "Total balance"],
    ["100000", "Current Assets", "Bank", "", "", 0.00],
    ["101000", "Current Assets:Operating Checking (4015)", "Bank", "Checking", "", -24910.83],
    ["102000", "Current Assets:Second Checking (3947)", "Bank", "Checking", "", 2416.24],
    ["104000", "Current Assets:Treasury", "Bank", "Checking", "", 631.85],
    ["107000", "Current Assets:Bank Account 7", "Bank", "Checking", "", 0.00],
    ["111000", "Current Assets:Payables Clearing", "Bank", "Checking", "", 0.00],
    ["170000", "Other Assets", "Other Assets", "", "", 0.00],
    ["174000", "Other Assets:Due From Affiliate", "Other Assets", "", "", 1658904.60],
    ["220000", "Credit Cards", "Credit Card", "", "", 0.00],
    ["223000", "Credit Cards:Credit Card 3", "Credit Card", "", "", 0.00],
    ["300000", "Equity", "Equity", "", "", 0.00],
    ["340000", "Equity:SAFE Notes", "Equity", "", "", -3118447.25],
    # QuickBooks' own Retained Earnings, which carries no account number and
    # which the year-end close writes into WITHOUT posting a journal line.
    ["", "Retained Earnings", "Equity", "", "", 0.00],
    ["600000", "Operating Expenses", "Expenses", "", "", 0.00],
    ["", "", "", "", "", ""],
    [FOOTER, "", "", "", "", ""],
]


def _gl_header(period):
    return [
        ["General Ledger", "", "", "", "", "", "", "", ""],
        ["Northwind Instruments Inc.", "", "", "", "", "", "", "", ""],
        [period, "", "", "", "", "", "", "", ""],
        ["", "", "", "", "", "", "", "", ""],
        ["", "Distribution account", "Transaction date", "Transaction type", "Num",
         "Name", "Memo/Description", "Amount", "Balance"],
    ]


def _section(rows, key, label, beginning, movements):
    """One General Ledger account section, in the report's own natural sign.

    `movements` are (date, amount) in that natural sign: on a credit-normal
    account a POSITIVE amount is a credit, which is the trap the loader's flip
    exists to undo. The Beginning Balance row follows the same convention.
    """
    full = f"{key} {label}".strip()
    rows.append([full, "", "", "", "", "", "", "", ""])
    first = len(rows) + 1                     # 1-based row of the beginning line
    rows.append(["", "Beginning Balance", "", "", "", "", "", "", beginning])
    refs = []
    for when, amount in movements:
        rows.append(["", full, when, "Expense", "", "A Counterparty", "activity",
                     amount, 0.00])
        refs.append(f"H{len(rows)}")
    total = "=" + "+".join(refs) if refs else "=0"
    rows.append([f"Total for {full}", "", "", "", "", "", "", total, ""])
    assert first                              # the row exists; silences linters
    return rows


def general_ledger_2024():
    """Opening balances at 2024-01-01, and the 2024 movement."""
    rows = _gl_header("January 1-December 31, 2024")
    _section(rows, "101000", "Operating Checking (4015)", 41905.22,
             [("03/04/2024", 23614.90)])
    _section(rows, "102000", "Second Checking (3947)", 488317.40,
             [("05/09/2024", -461092.55)])
    _section(rows, "104000", "Treasury", 812660.75,
             [("07/01/2024", -812033.10)])
    _section(rows, "174000", "Due From Affiliate", 744190.00,
             [("09/15/2024", 812455.36)])
    # No activity at all, and a beginning balance of 3,118,447.25. Credit-normal,
    # so the export writes it POSITIVE. This is the account that read as 0.00.
    _section(rows, "340000", "SAFE Notes", 3118447.25, [])
    _section(rows, "600000", "Operating Expenses", 0.00,
             [("12/31/2024", 437055.39)])
    # Credit-normal, so the export writes a DEBIT balance as a negative. With
    # this row the openings foot to 0.00, which is how a complete set of
    # opening balances announces itself and how a truncated one gives itself
    # away.
    _section(rows, "", "Retained Earnings", -1031373.88, [])
    rows.append(["", "", "", "", "", "", "", "", ""])
    rows.append([FOOTER, "", "", "", "", "", "", "", ""])
    return rows


def general_ledger_2025():
    rows = _gl_header("January-December, 2025")
    _section(rows, "101000", "Operating Checking (4015)", 65520.12,
             [("04/02/2025", -83724.79)])
    _section(rows, "102000", "Second Checking (3947)", 27224.85,
             [("05/09/2025", -24808.61)])
    _section(rows, "104000", "Treasury", 627.65, [("02/11/2025", 4.20)])
    _section(rows, "174000", "Due From Affiliate", 1556645.36,
             [("06/30/2025", 99116.45)])
    _section(rows, "340000", "SAFE Notes", 3118447.25, [])
    _section(rows, "600000", "Operating Expenses", 0.00,
             [("12/31/2025", 9412.75)])
    # The 2024 result has been closed into it. No journal line did that, which
    # is exactly why retained earnings is tied in aggregate and not row by row.
    _section(rows, "", "Retained Earnings", -1468429.27, [])
    rows.append(["", "", "", "", "", "", "", "", ""])
    rows.append([FOOTER, "", "", "", "", "", "", "", ""])
    return rows


def _tb(as_of, entries):
    """Amounts as literal formula strings, which is how QuickBooks writes them."""
    rows = [
        ["Northwind Instruments Inc.", "", ""],
        ["Trial Balance", "", ""],
        [f"As of {as_of}", "", ""],
        ["", "", ""],
        ["", "Debit", "Credit"],
    ]
    debits = credits = D("0.00")
    for label, amount in entries:
        amount = D(amount)
        if amount >= 0:
            rows.append([label, f"={amount:.2f}", ""])
            debits += amount
        else:
            rows.append([label, "", f"={-amount:.2f}"])
            credits += -amount
    rows.append(["TOTAL", f"={debits:.2f}", f"={credits:.2f}"])
    rows.append(["", "", ""])
    rows.append([FOOTER_TB, "", ""])
    return rows


TB_2025 = _tb("December 31, 2025", [
    ("101000 Current Assets:Operating Checking (4015)", "-18204.67"),
    ("102000 Current Assets:Second Checking (3947)", "2416.24"),
    ("104000 Current Assets:Treasury", "631.85"),
    ("174000 Other Assets:Due From Affiliate", "1655761.81"),
    ("340000 Equity:SAFE Notes", "-3118447.25"),
    ("600000 Operating Expenses", "9412.75"),
    # Prior-year expense, closed into retained earnings by QuickBooks with no
    # journal line. Tied in aggregate rather than row by row; see exit test 1.
    ("Retained Earnings", "1468429.27"),
])


def build(folder):
    write_sheet(os.path.join(folder, "Northwind Instruments Inc_Account List.xlsx"),
                ACCOUNT_LIST)
    write_sheet(os.path.join(folder, "Northwind Instruments Inc_General Ledger (1).xlsx"),
                general_ledger_2024())
    write_sheet(os.path.join(folder, "Northwind Instruments Inc_General Ledger.xlsx"),
                general_ledger_2025())
    write_sheet(os.path.join(folder, "Northwind+Instruments+Inc_Trial+Balance.xlsx"),
                TB_2025, "Trial Balance")
    return folder


def _profile():
    p = Profile()
    p.entity.name = "Northwind Instruments Inc."
    p.entity.basis = "accrual"
    p.accounts = [AccountSpec(book="101000", label="101000 Operating Checking (4015)",
                              kind="bank")]
    return p


# ------------------------------------------------------------------- tests

def test_the_opening_balances_are_carried_through_load(tmp):
    ledger = load_all(build(os.path.join(tmp, "exports")))
    assert ledger.period_start == datetime.date(2024, 1, 1)
    assert ledger.opening_basis, "no opening basis means no balance can be stated"
    assert "General Ledger (1)" in ledger.opening_basis, (
        "the openings must come from the EARLIEST ledger, not the latest: adding "
        "2025's openings to 2024's lines counts 2024 twice")
    for key, expected in OPENING_AT_2024_01_01.items():
        assert ledger.opening_of(key) == expected, key


def test_every_balance_equals_its_trial_balance_figure_to_the_cent(tmp):
    ledger = load_all(build(os.path.join(tmp, "exports")))
    stated = ledger.trial_balance_as_of(datetime.date(2025, 12, 31)).by_key()
    for key, expected in TRUE_BALANCES.items():
        assert ledger.balance_of(key) == expected, key
        assert ledger.balance_of(key) == stated[key], key


def test_movement_is_reported_separately_and_is_never_called_a_balance(tmp):
    """The exact figures the tool used to print, pinned as NOT balances."""
    ledger = load_all(build(os.path.join(tmp, "exports")))
    for key, movement in MOVEMENT_ONLY.items():
        assert ledger.movement_of(key) == movement, key
        assert ledger.balance_of(key) != movement or movement == TRUE_BALANCES[key], key
    # The one that cost the most: no activity in either year, millions owed.
    assert ledger.movement_of("340000") == D("0.00")
    assert ledger.balance_of("340000") == D("-3118447.25")


def test_a_balance_with_no_opening_source_cannot_be_determined(tmp):
    """Movement is not printed in a balance's place. It says so instead."""
    folder = build(os.path.join(tmp, "exports"))
    os.remove(os.path.join(folder, "Northwind Instruments Inc_General Ledger (1).xlsx"))
    write_sheet(os.path.join(folder, "Northwind Instruments Inc_General Ledger (1).xlsx"),
                _gl_header("January 1-December 31, 2024")
                + [["", "", "", "", "", "", "", "", ""], [FOOTER, "", "", "", "", "", "", "", ""]])
    ledger = load_all(folder)
    ledger.opening_balances, ledger.opening_basis = {}, ""
    assert ledger.balance_of("101000") is None
    assert ledger.opening_of("101000") is None
    report = run_exit_tests(ledger, [], _profile(),
                            reconcile(ledger, [], _profile(),
                                      period_start="2024-01-01", period_end="2025-12-31"))
    assert not report.get(1).passed
    assert report.get(1).measured["ledger balances"] == "cannot be determined"


def test_exit_test_1_measures_the_real_trial_balance_total(tmp):
    ledger = load_all(build(os.path.join(tmp, "exports")))
    prof = _profile()
    recon = reconcile(ledger, [], prof, period_start="2024-01-01", period_end="2025-12-31")
    test = run_exit_tests(ledger, [], prof, recon).get(1)
    tb = ledger.trial_balance_as_of(datetime.date(2025, 12, 31))
    assert test.measured["trial balance total"] == tb.total_debits
    assert test.measured["trial balance total"] != ledger.total_debits(), (
        "the ledger's own turnover is not the trial balance total, and printing it "
        "made this test unfailable")
    assert test.measured["accounts that disagree"] == 0
    assert test.passed, test.describe()


def test_exit_test_1_fails_when_the_ledger_disagrees_with_the_trial_balance(tmp):
    folder = build(os.path.join(tmp, "exports"))
    # One extra posting in the ledger that the trial balance does not know about.
    rows = general_ledger_2025()
    rows.insert(-2, ["", "101000 Operating Checking (4015)", "12/30/2025", "Expense", "",
                     "A Counterparty", "unposted", -250.00, 0.00])
    write_sheet(os.path.join(folder, "Northwind Instruments Inc_General Ledger.xlsx"), rows)
    ledger = load_all(folder)
    prof = _profile()
    recon = reconcile(ledger, [], prof, period_start="2024-01-01", period_end="2025-12-31")
    test = run_exit_tests(ledger, [], prof, recon).get(1)
    assert not test.passed
    assert test.measured["accounts that disagree"] >= 1
    assert "250.00" in test.measured_text()


def test_the_reconciliation_book_column_is_the_balance_not_the_movement(tmp):
    ledger = load_all(build(os.path.join(tmp, "exports")))
    recon = reconcile(ledger, [], _profile(),
                      period_start="2024-01-01", period_end="2025-12-31")
    for key, expected in TRUE_BALANCES.items():
        row = recon.row(key, "2025-12")
        assert row is not None, f"{key} is a real account and needs a row"
        assert row.book == expected, key
        assert row.book != MOVEMENT_ONLY[key] or expected == MOVEMENT_ONLY[key], key
        assert row.book_cell() != "cannot be determined", key


def test_only_real_accounts_are_reconciled_and_asked_for_statements(tmp):
    """A rollup and an unused template row are not open items.

    `100000 Current Assets` and `220000 Credit Cards` total their children.
    `107000 Bank Account 7`, `111000 Payables Clearing` and `223000 Credit Card
    3` have never been posted to and carry no balance. Demanding a statement for
    any of them demands a document that does not exist.
    """
    ledger = load_all(build(os.path.join(tmp, "exports")))
    for key in ("100000", "170000", "220000", "300000"):
        assert ledger.is_rollup(ledger.account(key)), key
        assert not is_real_account(ledger, ledger.account(key)), key
    for key in ("107000", "111000", "223000"):
        assert not is_real_account(ledger, ledger.account(key)), key
    for key in ("101000", "102000", "104000", "174000", "340000"):
        assert is_real_account(ledger, ledger.account(key)), key

    recon = reconcile(ledger, [], _profile(),
                      period_start="2024-01-01", period_end="2025-12-31")
    assert sorted(recon.accounts) == [
        "101000", "102000", "104000", "174000", "340000", "Retained Earnings"]
    assert any("never been posted to" in n for n in recon.notes)
    assert any("totals of their children" in n for n in recon.notes)


def test_a_parent_that_has_its_own_postings_is_not_a_rollup(tmp):
    """The rule is about subtotals, not about having children."""
    folder = build(os.path.join(tmp, "exports"))
    rows = general_ledger_2025()
    _section(rows, "170000", "Other Assets", 0.00, [("06/30/2025", 25.00)])
    write_sheet(os.path.join(folder, "Northwind Instruments Inc_General Ledger.xlsx"), rows)
    ledger = load_all(folder)
    parent = ledger.account("170000")
    assert ledger.children_of(parent), "170000 still has 174000 under it"
    assert not ledger.is_rollup(parent)
    assert is_real_account(ledger, parent)


def _run():
    tests = [(n, o) for n, o in sorted(globals().items())
             if n.startswith("test_") and callable(o)]
    failures = 0
    for name, fn in tests:
        with tempfile.TemporaryDirectory(prefix="close-the-books-balances-") as tmp:
            try:
                fn(tmp)
                print(f"  ok   {name}")
            except Exception as exc:                      # noqa: BLE001
                failures += 1
                print(f"  FAIL {name}: {type(exc).__name__}: {exc}")
    print(f"\n{len(tests) - failures} passed, {failures} failed")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(_run())
