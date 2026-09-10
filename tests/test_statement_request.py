"""The tool asks for the statements it needs instead of refusing.

`tieout`, `reconcile`, `check` and `handoff` all exited 2 when `statements/` was
empty, which is the NORMAL state of a working directory on the first day of a
catch-up. Four of the main commands were unreachable for exactly the person the
tool is written for, and what they got instead was the news that a folder was
empty.

Everything needed to ask precisely was already in hand: the chart says which
accounts are real bank and card accounts, the ledger says which months each one
has activity in, the profile records which feeds died and when, and the balances
say which accounts are on the wrong side and therefore need a statement to
settle which way. These tests pin that the request says WHICH ACCOUNTS, WHICH
MONTHS and WHY, per account, and that the reason differs where the reason
differs.

    python3 tests/test_statement_request.py
"""

from __future__ import annotations

import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "lib"))
sys.path.insert(0, HERE)

from closethebooks.coverage import coverage                          # noqa: E402
from closethebooks.profile import AccountSpec, Profile               # noqa: E402
from closethebooks.qbo_exports import load_all                       # noqa: E402
from closethebooks.recon import reconcile                            # noqa: E402
from closethebooks.statement_request import build                    # noqa: E402

import test_balances as company                                      # noqa: E402

try:
    import pytest

    @pytest.fixture(name="tmp")
    def _tmp_dir(tmp_path):
        return str(tmp_path)

except ImportError:                     # pragma: no cover - pytest is optional
    pytest = None


def _profile():
    """Three declared accounts: one live, one whose feed died, one never used."""
    p = Profile()
    p.entity.name = "Northwind Instruments Inc."
    p.accounts = [
        AccountSpec(book="101000", label="101000 Operating Checking (4015)",
                    mask="4015", kind="bank", feed="live"),
        AccountSpec(book="102000", label="102000 Second Checking (3947)",
                    mask="3947", kind="bank", feed="dead", feed_last="2024-05"),
        AccountSpec(book="107000", label="107000 Bank Account 7", kind="bank"),
    ]
    return p


def _ledger(tmp):
    return load_all(company.build(os.path.join(tmp, "exports")))


def _request(tmp, statements_dir="", recon=None):
    ledger = _ledger(tmp)
    grid = None
    if statements_dir:
        # The CLI declares accounts by their LABEL, so the grid is keyed that
        # way. `build` accepts either.
        grid = coverage(statements_dir,
                        accounts=[s.label or s.book for s in _profile().accounts],
                        start="2024-01", end="2025-12")
    return build(ledger, _profile(), coverage_grid=grid, recon=recon,
                 statements_dir=statements_dir or "statements/")


def _ask(request, key):
    for ask in request.asks:
        if ask.account_key == key:
            return ask
    return None


# ------------------------------------------------------------- what it asks

def test_it_asks_for_the_real_accounts_and_only_those(tmp):
    request = _request(tmp)
    keys = {a.account_key for a in request.asks}
    assert "101000" in keys and "102000" in keys
    # A parent that totals its children, and a template row nothing has ever
    # been posted to. Asking for either asks for a document that does not exist.
    assert "100000" not in keys, "a rollup has no statement"
    assert "107000" not in keys, "an unused chart row has no account behind it"
    excluded = {label.split()[0] for label, _ in request.excluded}
    assert "100000" in excluded and "107000" in excluded, (
        "what was not asked for is listed with a reason, never dropped silently")


def test_a_dead_feed_asks_for_everything_from_the_month_it_stopped(tmp):
    request = _request(tmp)
    ask = _ask(request, "102000")
    assert ask is not None
    assert ask.why == "dead_feed"
    # It stopped in 2024-05 and the books run to 2025-12, so every month from
    # 2024-05 on has to come off the statement: nothing categorized in
    # QuickBooks can find a transaction that was never imported.
    for month in ("2024-05", "2024-12", "2025-06", "2025-12"):
        assert month in ask.months, month
    assert "2024-05" in ask.reason()
    assert "feed" in ask.reason().lower()
    assert "never imported" in ask.reason()


def test_an_account_on_the_wrong_side_says_the_statement_settles_which_way(tmp):
    request = _request(tmp)
    ask = _ask(request, "101000")
    assert ask is not None
    assert ask.why == "wrong_side", "a bank account on the credit side outranks the rest"
    reason = ask.reason()
    assert "18,204.67 Cr" in reason
    assert "debit-normal" in reason
    assert "overdrawn" in reason and "not recorded" in reason
    assert "settles" in reason
    # It goes back to the month the balance CROSSED, not just the period end.
    assert "2025-04" in ask.months
    assert "2025-12" in ask.months


def test_the_reason_for_the_months_the_primary_reason_does_not_cover(tmp):
    """The download span and the reason must not read as a contradiction."""
    ask = _ask(_request(tmp), "102000")
    assert ask.because_months, "the feed months are the primary reason"
    if len(ask.months) > len(ask.because_months):
        assert ask.also, ("months are being asked for that the stated reason does "
                          "not cover, and nothing says why")
        assert "tied month by month" in ask.also
        # The rationale is said once, in the notes, not down every row.
        assert "morning's work" not in ask.also


def test_a_month_already_held_is_never_asked_for_twice(tmp):
    """The fastest way to lose a founder's goodwill is asking twice."""
    folder = os.path.join(tmp, "statements")
    os.makedirs(folder, exist_ok=True)
    # A file named the way a bank names one: the mask and the month. The mask
    # rather than a word like "checking", which more than one declared account
    # answers to and which coverage binds to the first of them.
    open(os.path.join(folder, "second-3947-statement-2024-05.csv"), "w").write(
        "Date,Description,Amount\n")
    request = _request(tmp, statements_dir=folder)
    ask = _ask(request, "102000")
    assert ask is not None
    assert "2024-05" not in ask.months, "that month is already in the folder"
    assert "2024-05" in ask.held
    assert "2024-06" in ask.months


def test_an_account_that_needs_nothing_says_so(tmp):
    """A list that asks for everything is a list nobody works."""
    folder = os.path.join(tmp, "statements")
    os.makedirs(folder, exist_ok=True)
    for year in (2024, 2025):
        for month in range(1, 13):
            open(os.path.join(
                folder, f"second-3947-statement-{year}-{month:02d}.csv"), "w").write(
                "Date,Description,Amount\n")
    request = _request(tmp, statements_dir=folder)
    assert _ask(request, "102000") is None, "every month it needs is already held"
    settled = " ".join(f"{label} {why}" for label, why in request.settled)
    assert "102000" in settled
    assert "already in" in settled
    # And the accounts that DO still need something are unaffected.
    assert _ask(request, "101000") is not None


def test_a_month_that_did_not_tie_becomes_a_reason_of_its_own(tmp):
    ledger = _ledger(tmp)
    prof = _profile()
    recon = reconcile(ledger, [], prof, period_start="2024-01-01",
                      period_end="2025-12-31")
    # Force one already-checked month to disagree, the way a real one would.
    row = recon.row("104000", "2025-06")
    row.statement, row.difference, row.checkable = row.book + 10, -10, True
    row.externally_verifiable = True
    request = build(ledger, prof, recon=recon, statements_dir="statements/")
    ask = _ask(request, "104000")
    assert ask is not None
    assert ask.why in ("unreconciled", "gap", "unverified")
    if ask.why == "unreconciled":
        assert "2025-06" in ask.reason()


# ------------------------------------------------------------- how it reads

def test_a_run_of_months_prints_as_a_span_and_a_scatter_prints_in_full(tmp):
    request = _request(tmp)
    for ask in request.asks:
        text = ask.months_text()
        if ask.contiguous:
            assert ask.months[0] in text and ask.months[-1] in text
            assert f"{len(ask.months)} month(s)" in text
        else:
            for month in ask.months:
                assert month in text, (
                    "a scattered set is printed in full, because a span would "
                    "ask for months nobody needs")


def test_the_written_request_is_something_a_founder_can_forward(tmp):
    markdown = _request(tmp).render_markdown()
    assert "# Statements needed" in markdown
    assert "Months to download" in markdown
    for key in ("101000", "102000"):
        assert key in markdown, key
    assert "no institution issues a statement for" in markdown
    # It says what to do with them, not only what to get.
    assert "CSV" in markdown and "PDF" in markdown
    assert "fill-gaps" in markdown, "a dead feed's statements become upload files"


def test_the_terminal_form_says_the_same_thing_and_wraps(tmp):
    request = _request(tmp)
    lines = request.render(limit=1)
    assert lines[0].startswith(f"{len(request.asks)} account(s) need statements")
    assert any("download" in line for line in lines)
    assert any("more account(s)" in line for line in lines)
    assert all(len(line) <= 100 for line in lines), "the terminal is not that wide"


def test_the_period_has_to_come_from_somewhere(tmp):
    ledger = _ledger(tmp)
    ledger.period_start = ledger.period_end = None
    try:
        build(ledger, _profile())
    except ValueError as exc:
        assert "period" in str(exc)
    else:
        raise AssertionError("a request with no period should not be built silently")


def _run():
    tests = [(n, o) for n, o in sorted(globals().items())
             if n.startswith("test_") and callable(o)]
    failures = 0
    for name, fn in tests:
        with tempfile.TemporaryDirectory(prefix="close-the-books-request-") as tmp:
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
