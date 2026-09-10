"""Two chart rows for one card, and the 15,803.42 that proves it.

A bank link was rebuilt and QuickBooks made a second account rather than
reattaching the feed. From then on the company had a numbered card account
holding every posted line and no feed, and an unnumbered top-level account of the
same name holding the live feed, all 863 queue items, no history, and an opening
balance plug of 15,803.42 that Opening Balance Equity carried the other side of.

Two rows in a chart is untidiness. What it does to every other check is not: the
reconciled history is on the row with no feed and the queue is on the row with no
history, so asking "has this account ever been reconciled" of the queued account
answers no, and 197 items sitting in months that were reconciled clean read as
unbooked work. `test_the_history_map_...` is the seam that closes that, and
`test_filing_year.py` asserts the effect.

`test_nothing_here_proposes_a_merge` is a rule, not a preference. Which row
survives, what happens to the plug and to Opening Balance Equity, and the ORDER
those happen in, decide whether a closed period stays closed.

    python3 tests/test_twins.py
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

from closethebooks import twins                                     # noqa: E402
from closethebooks.model import Account, JournalLine, Ledger        # noqa: E402
from closethebooks.twins import normalized_name, scan               # noqa: E402

import wound_down_company as company                                # noqa: E402

D = Decimal


def _flat(lines) -> str:
    return re.sub(r"\s+", " ", "\n".join(lines))


def _scan():
    return scan(company.ledger(), queue=company.queue(), profile=company.profile(),
                reconciled_through=company.reconciled_through())


def _twin():
    found = _scan()
    assert len(found) == 1, f"expected one pair, got {len(found)}"
    return found.findings[0]


# ---------------------------------------------------------- the detection

def test_the_pair_is_found_on_every_signal():
    twin = _twin()
    assert twin.strength == 5
    assert twin.certain is True
    assert "5 of 5" in twin.headline()


def test_the_row_with_the_history_and_the_row_with_the_feed_are_named_apart():
    twin = _twin()
    assert twin.original.key == company.CARD_NUMBERED
    assert twin.duplicate.key == company.CARD_TWIN
    assert twin.original.lines > twin.duplicate.lines
    assert twin.duplicate.queue_items == company.QUEUE_TOTAL
    assert twin.original.queue_items == 0
    text = " ".join(twin.lines())
    assert "The history is on 222000" in text
    assert "The feed and the queue are on Northwind card" in text


def test_the_plug_is_stated_and_opening_balance_equity_offsets_it_exactly():
    twin = _twin()
    assert abs(twin.plug) == company.PLUG == D("15803.42")
    assert twin.obe_balance == company.PLUG
    assert twin.plug_offsets_obe is True
    text = " ".join(twin.lines())
    assert "15,803.42" in text
    assert "Opening Balance from Bank" in text


def test_the_plug_is_recognised_by_its_words_and_not_by_being_the_earliest_line():
    """Every account has an earliest line. Only a plug says what it is."""
    led = company.ledger()
    led.lines = [line for line in led.lines
                 if "Opening Balance from Bank" not in (line.memo or "")]
    found = scan(led, queue=company.queue(), profile=company.profile())
    assert not found.certain, "a first transaction is not a plug"


def test_the_history_map_points_the_queue_back_at_the_reconciled_account():
    assert _scan().history_map() == {company.CARD_TWIN: company.CARD_NUMBERED}


# ------------------------------------------------------------ the naming

def test_numbering_case_and_a_masked_suffix_all_normalize_away():
    def name(full):
        return normalized_name(Account(name=full.split(":")[-1], full_name=full))

    assert name("Credit Cards:Northwind Card") == "northwind card"
    assert name("Northwind card") == "northwind card"
    assert name("Current Assets:Brand Cash (3947)") == "brand cash"
    assert name("102000 Brand Cash") == "brand cash"


# -------------------------------------------------------- what is not a twin

def _tiny_ledger(accounts, lines):
    led = Ledger(accounts={a.key: a for a in accounts}, lines=lines,
                 period_start=datetime.date(2025, 1, 1),
                 period_end=datetime.date(2025, 12, 31),
                 opening_basis="stated")
    led.opening_balances = {a.key: D("0.00") for a in accounts}
    return led


def test_the_same_name_under_two_parents_is_not_a_twin():
    """`Prepaid Rent` in two places is ordinary and must not be raised."""
    accounts = [
        Account(name="Prepaid Rent", number="140000",
                full_name="Other Current Assets:Prepaid Rent",
                type="Other Current Assets", role="prepaid"),
        Account(name="Prepaid Rent", number="141000",
                full_name="Other Assets:Prepaid Rent",
                type="Other Current Assets", role="prepaid"),
    ]
    lines = [JournalLine(date=datetime.date(2025, 3, 1), account="140000",
                         debit=D("1000.00"), memo="rent"),
             JournalLine(date=datetime.date(2025, 3, 1), account="141000",
                         debit=D("1000.00"), memo="rent")]
    found = scan(_tiny_ledger(accounts, lines))
    assert len(found) == 0
    assert found.considered, "the pair must be listed as considered, not dropped"
    assert "one coincidence of naming" in found.considered[0][1]


def test_two_accounts_of_different_types_are_never_one_row_twice():
    accounts = [
        Account(name="Reserve", number="108000", full_name="Current Assets:Reserve",
                type="Bank", role="bank"),
        Account(name="Reserve", number="", full_name="Reserve",
                type="Credit Card", role="card"),
    ]
    lines = [JournalLine(date=datetime.date(2025, 3, 1), account="108000",
                         debit=D("50.00"), memo="a"),
             JournalLine(date=datetime.date(2025, 3, 2), account="Reserve",
                         credit=D("50.00"), memo="Opening Balance from Bank")]
    assert len(scan(_tiny_ledger(accounts, lines))) == 0


def test_an_unused_chart_row_with_the_same_name_is_said_to_be_unused():
    """The second row of this shape usually never holds anything."""
    accounts = [
        Account(name="Second Checking", number="102000",
                full_name="Current Assets:Second Checking (3947)", type="Bank",
                role="bank"),
        Account(name="Second Checking", number="", full_name="Second Checking",
                type="Bank", role="bank"),
    ]
    lines = [JournalLine(date=datetime.date(2025, 3, 1), account="102000",
                         debit=D("50.00"), memo="a")]
    found = scan(_tiny_ledger(accounts, lines))
    assert len(found) == 0
    assert "only one of them has ever held anything" in found.considered[0][1]


# ---------------------------------------------------------- what it says

def test_nothing_here_proposes_a_merge():
    text = _flat(_twin().lines() + twins.render(_scan()) + twins.render_markdown(_scan()))
    for phrase in ("merge ", "merging", "combine the", "delete the", "should be merged"):
        assert phrase not in text.lower(), f"a disposition is proposed: {phrase!r}"
    assert "No disposition is proposed here" in text


def test_the_finding_says_what_it_breaks_elsewhere():
    text = " ".join(_twin().lines())
    assert "read as unbooked work" in text
    assert "never reconciled" in text


def test_no_finding_instructs_a_person():
    text = _flat(_twin().lines()).lower()
    for phrase in ("you should", "please ", "go and ", "you need to", "make sure"):
        assert phrase not in text, f"the finding instructs a person: {phrase!r}"


def test_both_renderings_state_n_of_n():
    found = _scan()
    for text in (_flat(twins.render(found)), _flat(twins.render_markdown(found))):
        assert f"1 of {found.checked} same-name group(s)" in text


if __name__ == "__main__":
    passed = 0
    for name, fn in sorted(list(globals().items())):
        if name.startswith("test_") and callable(fn):
            fn()
            passed += 1
    print(f"{passed} of {passed} twin tests pass")
