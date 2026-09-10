"""3,187.65 arrived in December, inside the tax year, with nobody watching.

The file stopped being worked in the spring. Eight months later a bank rule
matched a descriptor and posted an Employee Benefits deduction into the year on
the return: the only entry its month holds, landing exactly where sixteen earlier
entries for the same counterparty had landed, with no document behind it. It
balances, it foots, and it sits in an account where entries like it belong, so
nothing about the trial balance, the queue or the reconciliation would raise it.
A person scoping the file by hand found it.

Two things these tests pin, and the second matters more than the first:

  * the December entry ranks FIRST, ahead of larger and equally automated
    entries, because it is inside a filing year AND alone in its month. Being
    alone in the month is what separates a machine doing its job with people
    around it from a machine posting into a file nobody is reading;
  * no finding names an author unless an audit log was supplied. QuickBooks
    records one for every transaction and no xlsx export carries it, so the
    signals are the fallback and the finding says both of the things they can
    mean.

    python3 tests/test_autoposted.py
"""

from __future__ import annotations

import datetime
import os
import re
import sys
from decimal import Decimal

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "lib"))
sys.path.insert(0, HERE)

from closethebooks import autoposted                                # noqa: E402
from closethebooks.autoposted import scan                           # noqa: E402
from closethebooks.model import JournalLine                         # noqa: E402

import wound_down_company as company                                # noqa: E402

D = Decimal


def _flat(lines) -> str:
    return re.sub(r"\s+", " ", "\n".join(lines))


def _scan(**kw):
    kw.setdefault("filing_years", [company.FILING_YEAR])
    return scan(company.ledger(), **kw)


def _december(found):
    for finding in found:
        if finding.date == company.DECEMBER_DATE:
            return finding
    raise AssertionError("the December entry was not found at all")


# ------------------------------------------------------------- the finding

def test_the_december_entry_is_found_with_its_amount_and_its_account():
    entry = _december(_scan())
    assert entry.amount == company.DECEMBER_AMOUNT == D("3187.65")
    assert entry.category_account == "602005 Employee Benefits"
    assert entry.filing_year == 2025
    assert entry.in_filing_year is True


def test_it_ranks_first_ahead_of_larger_entries():
    found = _scan()
    assert found.findings[0].date == company.DECEMBER_DATE
    bigger = [f for f in found if f.amount > company.DECEMBER_AMOUNT]
    assert bigger, "the ranking is only meaningful if something bigger exists"


def test_the_three_signals_that_raised_it_are_each_measured():
    entry = _december(_scan())
    assert set(entry.signals) == {"alone", "pattern", "after_stop"}
    assert entry.unwatched is True
    assert "1 of" in entry.evidence["alone"] and "2025-12" in entry.evidence["alone"]
    assert "earlier entry(s)" in entry.evidence["pattern"]
    assert "602005 Employee Benefits" in entry.evidence["pattern"]
    assert "day(s) after" in entry.evidence["after_stop"]


def test_the_pattern_is_mined_from_the_companys_own_history():
    entry = _december(_scan())
    # Sixteen monthly premiums precede it, every one to the same account.
    assert "16 earlier entry(s)" in entry.evidence["pattern"]


def test_being_inside_a_filing_year_is_what_makes_it_urgent_and_it_says_so():
    text = " ".join(_december(_scan()).lines())
    assert "urgent rather than untidy" in text
    assert "3,187.65" in text and "reaches the return" in text


def test_outside_every_filing_year_the_same_entry_ranks_below_the_ones_inside():
    found = scan(company.ledger(), filing_years=[2024])
    assert found.findings, "there are 2024 entries with the same signals"
    assert all(f.filing_year in (2024, None) for f in found)
    assert found.findings[0].in_filing_year is True


# ------------------------------------------------------- when people stopped

def test_the_date_people_stopped_is_derived_and_the_derivation_is_stated():
    found = _scan()
    assert found.human_stopped == company.HAND_STOPPED == datetime.date(2025, 2, 18)
    assert "derived" in found.human_stopped_basis
    assert " of " in found.human_stopped_basis, "the derivation must be n of N"


def test_a_caller_can_supply_the_date_instead_and_it_says_that_too():
    found = _scan(human_stopped="2025-04-30")
    assert found.human_stopped == datetime.date(2025, 4, 30)
    assert found.human_stopped_basis == "supplied by the caller"


# --------------------------------------------------------- the audit log

def test_without_an_audit_log_no_finding_names_an_author():
    found = _scan()
    assert found.used_audit_log is False
    assert all(not f.author for f in found)
    notes = " ".join(found.not_checked)
    assert "no audit log was supplied" in notes
    assert "no attachment data was supplied" in notes


def test_with_an_audit_log_the_author_is_used_directly_and_said_so():
    led = company.ledger()
    for line in led.lines:
        if line.date == company.DECEMBER_DATE:
            line.doc_num = "TXN-DEC-1"
    found = scan(led, filing_years=[2025],
                 audit_log={"TXN-DEC-1": "System Administration"})
    entry = _december(found)
    assert found.used_audit_log is True
    assert entry.author == "System Administration"
    assert entry.author_is_automation is True
    text = " ".join(entry.lines())
    assert "The audit log names System Administration" in text
    assert "nothing above is being inferred" in text


def test_an_author_the_log_names_who_is_a_person_is_not_called_automation():
    led = company.ledger()
    for line in led.lines:
        if line.date == company.DECEMBER_DATE:
            line.doc_num = "TXN-DEC-1"
    entry = _december(scan(led, filing_years=[2025],
                           audit_log={"TXN-DEC-1": "M. Okonjo"}))
    assert entry.author == "M. Okonjo"
    assert entry.author_is_automation is False
    assert "read as a person" in " ".join(entry.lines())


def test_attachment_data_is_used_when_supplied_and_never_inferred():
    led = company.ledger()
    for line in led.lines:
        if line.date == company.DECEMBER_DATE:
            line.doc_num = "TXN-DEC-1"
    entry = _december(scan(led, filing_years=[2025], attachments={"TXN-DEC-1": 0}))
    assert "no_attachment" in entry.signals
    assert entry.evidence["no_attachment"] == "the attachment count supplied for it is 0"


# ------------------------------------------------------- one signal is not a finding

def test_one_signal_alone_is_not_raised():
    found = _scan()
    assert all(f.strength >= autoposted.MIN_SIGNALS for f in found)


def test_an_entry_a_poster_names_itself_on_needs_a_second_signal_too():
    """The plug is machine written and still carries only one signal here.

    It is reported, in full, by `twins.py`, which is the module that owns it.
    Raising it twice off one signal would make every routine integration posting
    a finding on a file with two hundred of them.
    """
    found = _scan()
    plugs = [f for f in found if f.date == datetime.date(2025, 1, 1)]
    assert plugs == []


# ------------------------------------------------------------ the wording

def test_the_cause_is_never_asserted():
    text = " ".join(_december(_scan()).lines())
    assert "either posted by automation or entered by one person" in text
    assert "the QuickBooks audit history" in text


def test_no_finding_instructs_a_person():
    text = _flat(_december(_scan()).lines()).lower()
    for phrase in ("you should", "please ", "go and ", "you need to", "make sure",
                   "reverse the entry", "delete it"):
        assert phrase not in text, f"the finding instructs a person: {phrase!r}"


def test_both_renderings_state_n_of_n():
    found = _scan()
    for text in (_flat(autoposted.render(found)),
                 _flat(autoposted.render_markdown(found))):
        assert f"of {found.entries} entry(s)" in text
        assert "inside a year being filed" in text
        assert "3,187.65" in text


# ---------------------------------------------------------- entry grouping

def test_a_reused_document_number_never_merges_two_transactions():
    led = company.ledger()
    led.lines += [
        JournalLine(date=datetime.date(2025, 7, 1), account="101000",
                    credit=D("10.00"), doc_num="1001", memo="one", txn_type="Expense"),
        JournalLine(date=datetime.date(2025, 8, 1), account="101000",
                    credit=D("20.00"), doc_num="1001", memo="two", txn_type="Expense"),
    ]
    before = scan(company.ledger(), filing_years=[2025]).entries
    assert scan(led, filing_years=[2025]).entries == before + 2


if __name__ == "__main__":
    passed = 0
    for name, fn in sorted(list(globals().items())):
        if name.startswith("test_") and callable(fn):
            fn()
            passed += 1
    print(f"{passed} of {passed} autoposted tests pass")
