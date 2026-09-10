"""Parser and coverage tests. Synthetic fixtures only.

Every figure, company and vendor here is invented. "Acme Robotics Inc." is not
a real company, and no file in this repository comes from a real bank export.

    pytest tests/test_statements.py        # or
    python3 tests/test_statements.py
"""

from __future__ import annotations

import datetime as _dt
import os
import sys
import tempfile
import zipfile

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "lib"))

from closethebooks.coverage import coverage, parse_statement_name          # noqa: E402
from closethebooks.statements import (PARSERS, ParseError, StatementFile,  # noqa: E402
                                      detect_parser, parse)
from closethebooks.statements import brex_csv, generic_csv                 # noqa: E402
from closethebooks.util import money                                       # noqa: E402

COMPANY = "Acme Robotics Inc."
EN_DASH = "–"


def write(tmp, name, text):
    path = os.path.join(tmp, name)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)
    return path


def amounts(statement):
    return [l.amount for l in statement.lines]


# --------------------------------------------------------------- CSV layouts

def test_signed_amount_column_with_balance():
    """Layout 1: one signed Amount column, plus a running Balance."""
    csv_text = (
        "Date,Description,Amount,Running Balance\n"
        "2026-04-02,Bolt Depot supplies,-250.00,9750.00\n"
        "2026-04-11,Northwind Systems invoice 1041,4000.00,13750.00\n"
        "2026-04-27,Pinebrook Realty rent,-1500.00,12250.00\n"
    )
    with tempfile.TemporaryDirectory() as tmp:
        statement = generic_csv.parse(write(tmp, "acme-checking.csv", csv_text),
                                      account_key="checking-1234")
        assert amounts(statement) == [money(-250), money(4000), money(-1500)]
        # opening is read back off the balance column: 9750 + 250
        assert statement.opening_balance == money(10000)
        assert statement.closing_balance == money(12250)
        assert statement.tie_out.ties, statement.tie_out.note
        assert statement.tie_out.deposits == money(4000)
        assert statement.tie_out.withdrawals == money(1750)
        assert statement.period_start == _dt.date(2026, 4, 2)
        assert statement.period_end == _dt.date(2026, 4, 27)


def test_debit_credit_columns():
    """Layout 2: a Debit column is money OUT and a Credit column is money IN."""
    csv_text = (
        "Posted Date,Details,Debit,Credit,Balance\n"
        "04/02/2026,Bolt Depot supplies,250.00,,9750.00\n"
        "04/11/2026,Northwind Systems invoice 1041,,4000.00,13750.00\n"
    )
    with tempfile.TemporaryDirectory() as tmp:
        statement = generic_csv.parse(write(tmp, "acme.csv", csv_text))
        assert amounts(statement) == [money(-250), money(4000)]


def test_debit_credit_columns_reversed_order():
    """The same two columns in the other order map identically: roles, not positions."""
    csv_text = (
        "Date,Memo,Credit,Debit\n"
        "2026-04-02,Bolt Depot supplies,,250.00\n"
        "2026-04-11,Northwind Systems invoice 1041,4000.00,\n"
    )
    with tempfile.TemporaryDirectory() as tmp:
        statement = generic_csv.parse(write(tmp, "acme.csv", csv_text), opening=0)
        assert amounts(statement) == [money(-250), money(4000)]


def test_debit_column_printed_negative_is_still_money_out():
    """Some banks print the debit column already negative. Meaning wins over sign."""
    csv_text = (
        "Date,Description,Debit,Credit\n"
        "2026-04-02,Bolt Depot supplies,-250.00,\n"
        "2026-04-03,Harbor Freightways refund,,75.00\n"
    )
    with tempfile.TemporaryDirectory() as tmp:
        statement = generic_csv.parse(write(tmp, "acme.csv", csv_text), opening=0)
        assert amounts(statement) == [money(-250), money(75)]


def test_withdrawal_deposit_pair():
    """Layout 4: Withdrawal / Deposit is the same shape under other names."""
    csv_text = (
        "Transaction Date,Payee,Withdrawal,Deposit,Balance\n"
        "2026-04-02,Bolt Depot supplies,250.00,,9750.00\n"
        "2026-04-11,Northwind Systems invoice 1041,,4000.00,13750.00\n"
    )
    with tempfile.TemporaryDirectory() as tmp:
        statement = generic_csv.parse(write(tmp, "acme.csv", csv_text))
        assert amounts(statement) == [money(-250), money(4000)]
        assert statement.opening_balance == money(10000)


def test_parenthesised_and_currency_amounts():
    csv_text = (
        "Date,Description,Amount\n"
        "2026-04-02,Bolt Depot supplies,\"($1,250.00)\"\n"
        "2026-04-11,Northwind Systems invoice 1041,\"$4,000.00\"\n"
    )
    with tempfile.TemporaryDirectory() as tmp:
        statement = generic_csv.parse(write(tmp, "acme.csv", csv_text), opening=0)
        assert amounts(statement) == [money(-1250), money(4000)]


def test_en_dash_negative():
    """U+2013 is a real minus sign in bank output and is invisible in a diff."""
    csv_text = (
        "Date,Description,Amount\n"
        f"2026-04-02,Bolt Depot supplies,{EN_DASH}$142.25\n"
        f"2026-04-14,Harbor Freightways,{EN_DASH}$41.50\n"
        "2026-04-20,Northwind Systems refund,$18.00\n"
    )
    with tempfile.TemporaryDirectory() as tmp:
        statement = generic_csv.parse(write(tmp, "acme-card.csv", csv_text), opening=0)
        assert amounts(statement) == [money("-142.25"), money("-41.50"), money(18)]
        assert statement.withdrawals == money("183.75")


def test_blank_date_continuation_rows_carry_the_date_forward():
    """Several transactions on one day: only the first row carries the date."""
    csv_text = (
        "Date,Description,Amount\n"
        "2026-04-02,Bolt Depot supplies,-25.00\n"
        ",Grayline Coffee,-8.75\n"
        ",Meridian Parking,-12.00\n"
        "2026-04-03,Northwind Systems invoice 1041,900.00\n"
    )
    with tempfile.TemporaryDirectory() as tmp:
        statement = generic_csv.parse(write(tmp, "acme.csv", csv_text), opening=0)
        assert [l.date for l in statement.lines] == [
            _dt.date(2026, 4, 2), _dt.date(2026, 4, 2), _dt.date(2026, 4, 2),
            _dt.date(2026, 4, 3),
        ]
        assert len(statement.lines) == 4


def test_newest_first_export_is_reordered():
    csv_text = (
        "Date,Description,Amount,Balance\n"
        "2026-04-27,Pinebrook Realty rent,-1500.00,12250.00\n"
        "2026-04-11,Northwind Systems invoice 1041,4000.00,13750.00\n"
        "2026-04-02,Bolt Depot supplies,-250.00,9750.00\n"
    )
    with tempfile.TemporaryDirectory() as tmp:
        statement = generic_csv.parse(write(tmp, "acme.csv", csv_text))
        assert [l.date.day for l in statement.lines] == [2, 11, 27]
        assert statement.opening_balance == money(10000)
        assert statement.closing_balance == money(12250)
        assert statement.tie_out.ties


def test_preamble_rows_above_the_header_are_skipped():
    csv_text = (
        f"{COMPANY}\n"
        "Account 1234, April 2026\n"
        "\n"
        "Date,Description,Amount\n"
        "2026-04-02,Bolt Depot supplies,-250.00\n"
    )
    with tempfile.TemporaryDirectory() as tmp:
        statement = generic_csv.parse(write(tmp, "acme.csv", csv_text), opening=0)
        assert len(statement.lines) == 1


def test_semicolon_delimiter():
    csv_text = (
        "Date;Description;Amount\n"
        "02/04/2026;Bolt Depot supplies;-250,00\n"
    )
    with tempfile.TemporaryDirectory() as tmp:
        statement = generic_csv.parse(write(tmp, "acme.csv", csv_text),
                                      opening=0, dayfirst=True)
        assert statement.lines[0].date == _dt.date(2026, 4, 2)


def test_unmappable_headers_name_what_was_seen():
    csv_text = "Column A,Column B,Column C\n1,2,3\n"
    with tempfile.TemporaryDirectory() as tmp:
        path = write(tmp, "mystery.csv", csv_text)
        try:
            generic_csv.parse(path)
        except ParseError as exc:
            assert "Column A" in str(exc) and "Column C" in str(exc)
        else:
            raise AssertionError("expected a ParseError naming the headers")


# ------------------------------------------------------- sign normalization

def test_card_export_with_positive_charges_is_flipped():
    """A charge must come out NEGATIVE however the issuer rendered it."""
    csv_text = (
        "Date,Merchant,Amount\n"
        "2026-04-03,Bolt Depot supplies,142.25\n"
        "2026-04-14,Grayline Coffee,41.50\n"
    )
    with tempfile.TemporaryDirectory() as tmp:
        statement = generic_csv.parse(write(tmp, "acme-card.csv", csv_text),
                                      amount_sign="flip", opening=0)
        assert amounts(statement) == [money("-142.25"), money("-41.50")]
        assert all(l.amount < 0 for l in statement.lines)
        assert statement.deposits == money(0)
        assert statement.withdrawals == money("183.75")


def test_unsigned_amount_signed_by_a_type_column():
    csv_text = (
        "Date,Description,Type,Amount\n"
        "2026-04-02,Bolt Depot supplies,DEBIT,250.00\n"
        "2026-04-11,Northwind Systems invoice 1041,CREDIT,4000.00\n"
    )
    with tempfile.TemporaryDirectory() as tmp:
        statement = generic_csv.parse(write(tmp, "acme.csv", csv_text), opening=0)
        assert amounts(statement) == [money(-250), money(4000)]


def test_unsigned_amount_with_an_unreadable_type_column_raises():
    """Direction is never guessed. An unreadable Type column is an error."""
    csv_text = (
        "Date,Description,Type,Amount\n"
        "2026-04-02,Bolt Depot supplies,Hardware,250.00\n"
        "2026-04-11,Northwind Systems invoice 1041,Software,4000.00\n"
    )
    with tempfile.TemporaryDirectory() as tmp:
        path = write(tmp, "acme.csv", csv_text)
        try:
            generic_csv.parse(path, opening=0)
        except ParseError as exc:
            assert "hardware" in str(exc).lower()
            assert "amount_sign" in str(exc)
        else:
            raise AssertionError("expected a ParseError about the unsigned amounts")


# ------------------------------------------------------------------- brex

def test_brex_card_export_is_flipped_and_says_so():
    csv_text = (
        "Purchase Date,Merchant,Amount,Transaction ID\n"
        "2026-04-03,Bolt Depot supplies,142.25,tx_0001\n"
        "2026-04-14,Grayline Coffee,41.50,tx_0002\n"
    )
    with tempfile.TemporaryDirectory() as tmp:
        path = write(tmp, "brex-card-2026-04.csv", csv_text)
        statement = brex_csv.parse(path, account_key="brex-card", opening=0)
        assert all(l.amount < 0 for l in statement.lines)
        assert statement.lines[0].external_id == "tx_0001"
        assert any("spend-positive" in n for n in statement.notes)
        assert any("UNVERIFIED" in n for n in statement.notes)
        assert brex_csv.flavor_of(path) == "card"


def test_brex_cash_export_keeps_its_own_signs():
    csv_text = (
        "Date,Counterparty,Amount,Running Balance\n"
        "2026-04-02,Bolt Depot supplies,-250.00,9750.00\n"
        "2026-04-11,Northwind Systems invoice 1041,4000.00,13750.00\n"
    )
    with tempfile.TemporaryDirectory() as tmp:
        path = write(tmp, "brex-cash-2026-04.csv", csv_text)
        statement = brex_csv.parse(path, account_key="brex-cash")
        assert amounts(statement) == [money(-250), money(4000)]
        assert statement.opening_balance == money(10000)
        assert brex_csv.flavor_of(path) == "cash"
        assert brex_csv.VERIFIED.startswith("no")


# --------------------------------------------------------------- dispatcher

def test_registry_and_dispatch():
    assert set(PARSERS) == {"mercury_checking", "mercury_card", "brex_csv", "generic_csv"}
    csv_text = "Date,Description,Amount\n2026-04-02,Bolt Depot supplies,-250.00\n"
    with tempfile.TemporaryDirectory() as tmp:
        path = write(tmp, "acme-checking-1234-statement-2026-04.csv", csv_text)
        assert detect_parser(path) == "generic_csv"
        statement = parse(path, account_key="checking-1234", opening=0)
        assert isinstance(statement, StatementFile)
        assert statement.parser == "generic_csv"
        assert statement.lines[0].account_key == "checking-1234"

        brex_path = write(tmp, "brex-card-export.csv", csv_text)
        assert detect_parser(brex_path) == "brex_csv"


def test_dispatch_on_an_unreadable_file_names_what_it_tried():
    with tempfile.TemporaryDirectory() as tmp:
        path = write(tmp, "notes.txt", "nothing tabular here\n")
        try:
            parse(path)
        except ParseError as exc:
            assert "generic_csv" in str(exc)
        else:
            raise AssertionError("expected a ParseError")


# ----------------------------------------------------------------- coverage

def _touch(path):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as fh:
        fh.write(b"%PDF-1.4\n")


def test_coverage_reports_a_missing_month():
    with tempfile.TemporaryDirectory() as tmp:
        for month in ("01", "02", "04"):
            _touch(os.path.join(tmp, f"acme-checking-1234-monthly-statement-2026-{month}.pdf"))
        for month in ("01", "02", "03", "04"):
            _touch(os.path.join(tmp, "cards", f"acme-card-5678-statement-2026-{month}.pdf"))

        grid = coverage(tmp, ["checking-1234", "card-5678"], "2026-01", "2026-04")
        assert grid.missing == ["checking-1234 2026-03"]
        assert grid.complete is False
        assert grid.missing_for("card-5678") == []
        assert grid.cell("checking-1234", "2026-02") == "Y"
        assert grid.cell("checking-1234", "2026-03") == "."
        assert "2026-03" in grid.render()


def test_coverage_is_complete_when_every_month_is_held():
    with tempfile.TemporaryDirectory() as tmp:
        for month in ("01", "02"):
            _touch(os.path.join(tmp, f"acme-checking-1234-monthly-statement-2026-{month}.pdf"))
        grid = coverage(tmp, ["checking-1234"], "2026-01", "2026-02")
        assert grid.complete is True
        assert grid.missing == []


def test_coverage_reads_inside_a_zip_without_extracting_it():
    """A founder who sends a zip HAS sent the statements. Do not ask again."""
    with tempfile.TemporaryDirectory() as tmp:
        _touch(os.path.join(tmp, "acme-checking-1234-monthly-statement-2026-01.pdf"))
        archive = os.path.join(tmp, "acme-2026-statements.zip")
        with zipfile.ZipFile(archive, "w") as zf:
            zf.writestr("acme-checking-1234-monthly-statement-2026-02.pdf", "%PDF-1.4\n")
            zf.writestr("acme-checking-1234-monthly-statement-2026-03.pdf", "%PDF-1.4\n")

        grid = coverage(tmp, ["checking-1234"], "2026-01", "2026-03")
        assert grid.missing == []
        assert grid.complete is True
        assert grid.cell("checking-1234", "2026-02") == "Z"
        assert len(grid.in_zip) == 2
        # the archive itself is not left sitting in `unparsed`
        assert not any(n.endswith(".zip") for n in grid.unparsed)
        # and nothing was extracted
        assert sorted(os.listdir(tmp)) == [
            "acme-2026-statements.zip",
            "acme-checking-1234-monthly-statement-2026-01.pdf",
        ]


def test_coverage_surfaces_files_it_cannot_name():
    with tempfile.TemporaryDirectory() as tmp:
        _touch(os.path.join(tmp, "acme-checking-1234-monthly-statement-2026-01.pdf"))
        _touch(os.path.join(tmp, "scan0001.pdf"))
        grid = coverage(tmp, ["checking-1234"], "2026-01", "2026-01")
        assert grid.complete is True
        assert any("scan0001" in n for n in grid.unparsed)


def test_coverage_discovers_accounts_when_none_are_declared():
    with tempfile.TemporaryDirectory() as tmp:
        _touch(os.path.join(tmp, "acme-checking-1234-monthly-statement-2026-01.pdf"))
        _touch(os.path.join(tmp, "acme-card-5678-monthly-statement-2026-01.pdf"))
        grid = coverage(tmp)
        assert grid.accounts == ["1234", "5678"]
        assert grid.months == ["2026-01"]


def test_a_statement_for_an_undeclared_account_is_not_silently_dropped():
    with tempfile.TemporaryDirectory() as tmp:
        _touch(os.path.join(tmp, "acme-checking-1234-monthly-statement-2026-01.pdf"))
        _touch(os.path.join(tmp, "acme-vault-9999-monthly-statement-2026-01.pdf"))
        grid = coverage(tmp, ["checking-1234"], "2026-01", "2026-01")
        assert grid.complete is True
        assert any("9999" in row for row in grid.unassigned)


def test_a_1099_with_a_date_is_not_a_statement():
    assert parse_statement_name("acme-1099-nec-2026-01.pdf", "/x/acme-1099-nec-2026-01.pdf") is None


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
