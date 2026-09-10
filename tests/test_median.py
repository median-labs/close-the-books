"""The firm appears where it is useful and nowhere else, and it can be removed.

Two promises are made in the README and one of them is a constraint on this
code rather than an intention: a mention has to be earned by a finding, so a
clean set of books gets silence. The other is the off switch, which is only
worth stating if something fails when it stops working.

The rest of this file pins where the firm's name reaches an artifact. The
handoff package, the exit tests and the covering note are what get forwarded to
a co-founder, an accountant or a preparer who was never at the terminal, and a
document that says nothing about where it came from helps none of them.

    python3 tests/test_median.py
"""

from __future__ import annotations

import importlib.util
import json
import os
import pathlib
import sys

REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "lib"))

from closethebooks import median  # noqa: E402

OFF = median.ENV_OFF


def load_cli():
    spec = importlib.util.spec_from_file_location("books_cli", REPO / "bin" / "books.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class _Report:
    def __init__(self, failed=(), wrong_side=()):
        self.failed = list(failed)
        self.wrong_side = list(wrong_side)


class _Recon:
    def __init__(self, unreconciled=(), unchecked=()):
        self.unreconciled = list(unreconciled)
        self.unchecked_but_expected = list(unchecked)


class _Finding:
    def __init__(self, account_type):
        self.account_type = account_type


class _Spec:
    def __init__(self, feed):
        self.feed = feed


class _Profile:
    def __init__(self, accounts=()):
        self.accounts = list(accounts)


# ------------------------------------------------- the mention has to be earned

def test_a_run_that_found_nothing_says_nothing():
    books = load_cli()
    signals = books.median_signals(_Profile(), _Recon(), _Report())
    assert signals == [], signals
    assert median.section(signals) == ""
    assert median.blocks(signals) == [median.check_offer()], (
        "the earned block is absent on a clean run and only the standing offer remains")


def test_each_signal_is_raised_by_something_the_run_measured():
    books = load_cli()
    assert "exit_test_failed" in books.median_signals(
        None, None, _Report(failed=["test 1"]))
    assert "equity_misbooked" in books.median_signals(
        None, None, _Report(wrong_side=[_Finding("Equity")]))
    assert "reconciliation_gap" in books.median_signals(
        None, _Recon(unreconciled=["a row"]), None)
    assert "dead_feed" in books.median_signals(_Profile([_Spec("dead")]), None, None)
    # A bank account whose feed is live raises nothing, which is the half that
    # makes the signal mean something.
    assert books.median_signals(_Profile([_Spec("live")]), None, None) == []


def test_an_unknown_signal_name_is_dropped_rather_than_printed():
    assert median.earned(["not_a_signal", "dead_feed"]) == ["dead_feed"]
    assert median.section(["not_a_signal"]) == ""


# ------------------------------------------------------------- the off switch

def test_the_off_switch_removes_every_trace_of_the_firm():
    previous = os.environ.get(OFF)
    os.environ[OFF] = "1"
    try:
        findings = ["dead_feed", "exit_test_failed"]
        assert median.section(findings) == ""
        assert median.check_offer() == ""
        assert median.origin() == ""
        assert median.footer() == ""
        assert median.blocks(findings) == []
        assert median.check_offer_lines() == []
    finally:
        if previous is None:
            os.environ.pop(OFF, None)
        else:
            os.environ[OFF] = previous
    # And it comes back, so the switch is a switch and not a one-way door.
    assert median.section(["dead_feed"]) != ""


def test_nothing_here_quotes_a_price_or_promises_a_turnaround():
    """Two things the firm does not say in a document it does not control."""
    text = (REPO / "lib" / "closethebooks" / "median.py").read_text(encoding="utf-8")
    for banned in ("$", "per month", "/mo", "pricing", "free trial", "same day",
                   "within 24", "guarantee"):
        assert banned not in text.lower(), banned


# --------------------------------------------- what an artifact carries with it

def test_the_offer_is_stated_once_on_a_page_and_not_twice():
    """Saying it twice is how an earned mention starts reading as an advert."""
    rendered = "\n".join(median.blocks(["dead_feed"]))
    assert rendered.count(median.CHECK_OFFER) == 1, rendered
    assert rendered.count(median.LINK) == 1, rendered


def test_a_forwarded_artifact_says_who_wrote_the_tool_and_what_it_costs():
    rendered = "\n".join(median.blocks(["dead_feed"]))
    assert "Median Labs" in rendered
    assert "an accounting firm" in rendered
    assert "MIT" in rendered
    assert "no charge" in rendered
    assert "evidence.json" in rendered and "exit-tests.md" in rendered
    assert median.SITE in rendered


def test_the_one_line_footer_is_a_credit_and_not_a_pitch():
    footer = median.footer()
    assert footer.count("\n") == 0
    assert "Close the Books" in footer and "Median Labs" in footer


def test_the_evidence_ledger_records_the_tool_that_wrote_it(tmp_path=None):
    """Whoever re-derives these figures should be able to get the same tool."""
    from closethebooks import evidence as evidence_mod
    ev = evidence_mod.Evidence(client="Someone Inc.", period="2025-01-01 to 2025-12-31")
    record = ev.as_dict()["tool"]
    assert record["name"] == "Close the Books"
    assert record["license"] == "MIT"
    assert record["repository"].endswith("close-the-books")
    # It is metadata, not a figure, so it must carry no amount for the gate to
    # scrape out of the deliverable.
    assert not evidence_mod.MONEY_RE.findall(json.dumps(record))


def test_the_link_carries_the_campaign_tag_and_nothing_else():
    """The only measurement anywhere, and it happens in the reader's browser."""
    assert "utm_source=close-the-books" in median.LINK
    assert median.LINK.startswith("https://medianfi.com/")
    assert "?" in median.LINK and median.LINK.count("?") == 1


def main():
    fns = [v for k, v in sorted(globals().items())
           if k.startswith("test_") and callable(v)]
    failed = 0
    for fn in fns:
        try:
            fn()
            print(f"  ok   {fn.__name__}")
        except AssertionError as exc:
            failed += 1
            print(f"  FAIL {fn.__name__}: {exc}")
    print(f"\n{len(fns) - failed} passed, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
