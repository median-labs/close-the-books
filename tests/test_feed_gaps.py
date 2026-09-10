"""A queue is not a period, and one piece of arithmetic proves when it is not.

On the file these tests are shaped after, the company's main operating account
had an eight-month hole in the year being filed. Everything downstream of a For
Review queue assumes the queue IS the period, so that account could have been
worked to zero and the return built on it would still have been wrong, with
nothing anywhere saying so.

The check that would have caught it is the balance walk, and it is the reason
`test_working_the_queue_moves_this_account_away_from_the_bank` is the most
important test in this file:

    book        (41,876.20)   what the ledger holds
  + queue net   (52,309.55)   every unbooked item on the account, netted
  = projected   (94,185.75)   where working the whole queue would land it
    bank             37.14    what the bank says it holds
    before       41,913.34    how far apart they are now
    after        94,222.89    how far apart they would be

A queue that moves a book AWAY from the bank cannot be the rest of the story.
The other three signals say a period looks thin; this one says it cannot be
complete, in arithmetic anybody can check.

    python3 tests/test_feed_gaps.py
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

from closethebooks import feed_gaps                                 # noqa: E402
from closethebooks.feed_gaps import LEDGER_SERIES, QUEUE_SERIES, scan  # noqa: E402
from closethebooks.model import BankLine                            # noqa: E402

import wound_down_company as company                                # noqa: E402

D = Decimal


def _flat(lines) -> str:
    """One string with runs of whitespace collapsed.

    The renderers fold prose to 76 columns, so a phrase a test looks for is
    routinely split across two lines. Collapsing first tests the words rather
    than where the wrap happened to land.
    """
    return re.sub(r"\s+", " ", "\n".join(lines))


def _scan(**kw):
    kw.setdefault("profile", company.profile())
    return scan(company.ledger(), **kw)


def _drift_scan():
    return _scan(queue=company.queue() + company.drift_queue(),
                 bank_balances={company.DRIFT: company.DRIFT_BANK})


# ------------------------------------------------------- the balance walk

def test_working_the_queue_moves_this_account_away_from_the_bank():
    finding = _drift_scan().get(company.DRIFT)
    walk = finding.walk
    assert walk.book == company.DRIFT_BOOK == D("-41876.20")
    assert walk.queue_net == company.DRIFT_QUEUE_NET == D("-52309.55")
    assert walk.bank == company.DRIFT_BANK == D("37.14")
    assert walk.projected == D("-94185.75")
    assert walk.before == D("41913.34")
    assert walk.after == D("94222.89")
    assert walk.moves_away is True
    assert walk.implied_missing == D("94222.89")
    assert finding.proven_incomplete is True


def test_the_arithmetic_is_written_into_the_finding_and_not_just_computed():
    lines = " ".join(_drift_scan().get(company.DRIFT).lines())
    for figure in ("(41,876.20)", "(52,309.55)", "(94,185.75)", "37.14",
                   "41,913.34", "94,222.89"):
        assert figure in lines, f"{figure} is computed but never stated"
    assert "in neither" in lines


def test_a_queue_that_moves_the_book_towards_the_bank_is_not_called_proof():
    """The same account, with a queue that closes the gap instead of widening it."""
    closing = [BankLine(date=datetime.date(2025, 6, 10), descriptor="deposit",
                        amount=D("41913.34"), account_key=company.DRIFT)]
    finding = _scan(queue=closing,
                    bank_balances={company.DRIFT: company.DRIFT_BANK}).get(company.DRIFT)
    assert finding is None or finding.walk.moves_away is False
    if finding is not None:
        assert finding.proven_incomplete is False
        assert "towards the bank" in " ".join(finding.lines())


def test_without_a_bank_balance_the_walk_says_it_did_not_run():
    scanned = _scan(queue=company.queue() + company.drift_queue())
    assert not scanned.proven_incomplete
    notes = " ".join(scanned.not_checked)
    assert "no bank balance was supplied" in notes
    assert company.DRIFT in notes or "Second Checking" in notes


def test_the_walk_leads_the_finding_when_it_fires():
    """The proof must not sit under a month count a reader skims past."""
    assert "moves further from its bank balance" in _drift_scan().get(
        company.DRIFT).headline()


# --------------------------------------------------------- the empty run

def test_the_interior_run_of_empty_months_is_found_and_named():
    finding = _drift_scan().get(company.OPERATING)
    assert finding is not None
    assert finding.longest_run == ["2025-05", "2025-06", "2025-07", "2025-08",
                                   "2025-09", "2025-10", "2025-11"]
    assert "2025-05 through 2025-11" in finding.headline()


def test_the_run_states_the_activity_that_brackets_it():
    finding = _drift_scan().get(company.OPERATING)
    before, after = finding.bracketed_by
    assert before == datetime.date(2025, 2, 18) or before.year == 2025
    assert after == company.DECEMBER_DATE
    text = " ".join(finding.lines())
    assert "2025-12-05" in text
    assert "days apart" in text


def test_a_run_at_the_end_of_the_series_is_not_an_interior_run():
    """Nothing bounds it, so nothing proves the account was live across it."""
    window = [f"2025-{m:02d}" for m in range(1, 13)]
    assert feed_gaps._interior_runs(["2025-01", "2025-02"], window) == []
    assert feed_gaps._interior_runs(["2025-01", "2025-06"], window) == [
        ["2025-02", "2025-03", "2025-04", "2025-05"]]


def test_one_empty_month_is_not_a_finding():
    window = [f"2025-{m:02d}" for m in range(1, 13)]
    assert feed_gaps._interior_runs(["2025-01", "2025-03"], window) == []


# ------------------------------------------------------ what is measured

def test_with_no_queue_the_months_come_from_the_ledger_and_the_finding_says_so():
    finding = _scan().get(company.OPERATING)
    assert finding.series == LEDGER_SERIES
    text = " ".join(finding.lines())
    assert "No For Review queue was supplied" in text
    assert "weaker evidence" in text


def test_with_a_queue_the_months_come_from_the_queue():
    finding = _drift_scan().get(company.DRIFT)
    assert finding.series == QUEUE_SERIES


def test_months_the_books_post_in_and_the_feed_does_not_reach_are_counted():
    finding = _drift_scan().get(company.DRIFT)
    assert finding.ledger_only_months, "the queue holds one month and the books hold 17"
    assert "2024-01" in finding.ledger_only_months


def test_an_account_stale_only_because_the_whole_file_stopped_is_marked_as_such():
    scanned = _drift_scan()
    stale = [f.key for f in scanned.stale_only]
    assert company.CARD_NUMBERED in stale
    assert company.DRIFT not in stale and company.OPERATING not in stale
    assert "only because the account stopped" in _flat(feed_gaps.render(scanned))


# ---------------------------------------------------- causes and wording

def test_no_finding_asserts_which_cause_it_is():
    for finding in _drift_scan():
        text = " ".join(finding.lines())
        assert "either dormant" in text and "missing its records" in text, (
            "a gap has two explanations and the finding must name both")
        assert "the statement for those months" in text


def test_no_finding_instructs_a_person():
    for finding in _drift_scan():
        text = " ".join(finding.lines()).lower()
        for phrase in ("you should", "please ", "request the", "go and ",
                       "ask the client", "you need to"):
            assert phrase not in text, f"the finding instructs a person: {phrase!r}"


def test_an_account_nothing_was_ever_posted_to_is_not_swept():
    scanned = _drift_scan()
    assert all(f.key != "103000" for f in scanned)
    assert scanned.checked == 4, "four bank and card accounts have held something"


def test_both_renderings_state_n_of_n():
    scanned = _drift_scan()
    for text in (_flat(feed_gaps.render(scanned)),
                 _flat(feed_gaps.render_markdown(scanned))):
        assert f"of {scanned.checked} bank and card account(s)" in text
        assert "proven by arithmetic" in text


if __name__ == "__main__":
    passed = 0
    for name, fn in sorted(list(globals().items())):
        if name.startswith("test_") and callable(fn):
            fn()
            passed += 1
    print(f"{passed} of {passed} feed-gap tests pass")
