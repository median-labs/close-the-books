"""Browser mode, which is the one part of this kit that can change live books.

Everywhere else, the worst a mistake does is write a file somebody then declines
to upload. Here an agent is clicking in a company file that belongs to somebody,
in front of them, and the only things between a bad batch and their books are
the checks in this file. Treat a failure here as a stop.

WHAT CANNOT BE TESTED, AND WHAT IS DONE INSTEAD

There is no QuickBooks sandbox that behaves like the real interface, and a
mistake in a real file does not come back. So nothing here touches QuickBooks.
`FakeGrid` reproduces the four behaviours that make the real For Review grid
dangerous, and the flow is driven against that:

  the grid is virtualized, so a naive read returns about 34 rows
  the tab undercounts, because it shows one type filter
  accepting a row takes it out of the queue, which is what the count measures
  a bank rule can fire underneath the work and move the count by one more

`tests/fixtures/for-review-grid.html` is the same four behaviours as a page, for
exercising the reading recipe in a real browser. This file is the one that runs
in the suite.
"""

import json
import pathlib
import shutil
import subprocess
import sys
import tempfile

REPO = pathlib.Path(__file__).resolve().parent.parent
BOOKS = REPO / "bin" / "books.py"
FIXTURES = pathlib.Path(__file__).resolve().parent / "fixtures"
EXAMPLE_PROFILE = REPO / "profiles" / "example-acme-robotics.json"
sys.path.insert(0, str(REPO / "lib"))

from closethebooks import browser  # noqa: E402
from closethebooks.approval import (  # noqa: E402
    ApprovalError, check_batch, write_approval,
)

OK, FAILURE, REFUSAL = 0, 1, 2
COMPANY = "Acme Robotics Inc."


# ------------------------------------------------------------------ the fake


class FakeGrid:
    """A For Review queue that misleads the way the real one misleads.

    Every method here corresponds to something an agent does with a browser
    tool, and every trap corresponds to a line in `context/tools/qbo.md` that
    somebody paid for on a real file.
    """

    WINDOW = 34   # rows in the document at any moment, whatever the count says

    def __init__(self, total=600, rule_fires=False):
        self.rows = [
            {"id": f"row-{i + 1}",
             "type": "Received" if i % 5 == 3 else "Spent",
             "accepted": False, "category": "", "klass": ""}
            for i in range(total)
        ]
        self.rule_fires = rule_fires
        self.scroll = 0

    # what is actually in the queue
    def live(self, kind=None):
        return [r for r in self.rows
                if not r["accepted"] and (kind is None or r["type"] == kind)]

    def total(self):
        return len(self.live())

    def displayed(self):
        """The number on the tab: one type filter, so smaller than the queue."""
        return len(self.live("Spent"))

    def parts(self):
        return [{"filter": k, "rows": len(self.live(k))}
                for k in ("Received", "Spent")]

    def naive_read(self):
        """Read the rows in the document. This is the trap, not a bug."""
        return self.live()[self.scroll:self.scroll + self.WINDOW]

    def set_scroll_top(self, value):
        """Assigning scrollTop reports the value back and re-renders nothing."""
        return value

    def wheel(self, rows):
        """A real wheel event, which is the only thing that re-renders."""
        self.scroll = max(0, min(self.scroll + rows, max(0, self.total() - 1)))
        return self.naive_read()

    def full_read(self):
        """The whole row model, off the table instance. What a read should do."""
        out, self.scroll = [], 0
        while len(out) < self.total():
            chunk = self.naive_read()
            if not chunk:
                break
            out.extend(r for r in chunk if r not in out)
            self.wheel(self.WINDOW)
        self.scroll = 0
        return out

    def accept(self, n, *, category="6100 Contract Engineering",
               klass="Operations"):
        """Post n rows the way an agent works a batch, one row at a time."""
        done = 0
        for row in self.live():
            if done >= n:
                break
            row["accepted"] = True
            row["category"] = category or "Uncategorized Expense"
            row["klass"] = klass or "Not specified"
            done += 1
        if self.rule_fires and done:
            self._rule()
        return done

    def _rule(self):
        """A rule with auto-add on, accepting a row nobody looked at."""
        rest = self.live()
        if rest:
            rest[0]["accepted"] = True
            rest[0]["category"] = "Uncategorized Expense"
            rest[0]["klass"] = "Not specified"

    def read_payload(self, account, *, full=True):
        """What an agent hands to `books.py browser read`."""
        return {
            "account": account,
            "company": COMPANY,
            "displayed": self.displayed(),
            "parts": self.parts(),
            "rows_read": len(self.full_read() if full else self.naive_read()),
        }


# ------------------------------------------------------- a working directory


def workdir():
    """A directory with a profile and nothing else. No exports, no ledger.

    The planning commands need a year of exports and are covered by the library
    tests below. Everything that guards a write needs only a plan file, and a
    plan file is small enough to write here, which keeps these tests fast enough
    that somebody will actually run them.
    """
    wd = pathlib.Path(tempfile.mkdtemp(prefix="ctb-browser-"))
    for name in ("review", "import", "answers", "reports", "profiles"):
        (wd / name).mkdir(parents=True, exist_ok=True)
    shutil.copy2(EXAMPLE_PROFILE, wd / "profiles" / "mine.local.json")
    return wd


def run(*args, workdir=None, expect=None):
    cmd = [sys.executable, str(BOOKS)]
    if workdir is not None:
        cmd += ["--workdir", str(workdir)]
    cmd += [str(a) for a in args]
    p = subprocess.run(cmd, capture_output=True, text=True, cwd=str(REPO))
    if expect is not None and p.returncode != expect:
        raise AssertionError(
            f"`books.py {' '.join(str(a) for a in args)}` exited {p.returncode}, "
            f"expected {expect}\n--- stdout ---\n{p.stdout}\n--- stderr ---\n{p.stderr}"
        )
    return p.returncode, p.stdout, p.stderr


def rows(n, start=1):
    return [browser.PostRow(
        ref=f"row-{i}", date="2025-10-01", descriptor=f"ACH DEBIT VENDOR {i}",
        amount="(100.00)", account="6100 Contract Engineering",
        klass="Operations", why="9 of 9 past payments went there")
        for i in range(start, start + n)]


def plan(wd, tag, *, kind="categorize", account="1010", n=25, company=COMPANY):
    batch = browser.PostBatch(tag=tag, kind=kind, account=account, rows=rows(n),
                              company=company, counter=f"for-review:{account}")
    (wd / "review" / f"batch-{tag}.json").write_text(batch.to_json(),
                                                     encoding="utf-8")
    (wd / "review" / f"batch-{tag}.md").write_text(batch.to_markdown(),
                                                   encoding="utf-8")
    return batch


def confirm(wd, company=COMPANY):
    run("browser", "confirm", "--company", company, workdir=wd, expect=OK)


def quiet(wd, auto_add=0):
    """Record the rules, which posting requires: a count only proves something
    when nothing else is posting."""
    p = wd / "rules.json"
    p.write_text(json.dumps({"count": 15, "auto_add": auto_add}), encoding="utf-8")
    run("browser", "read", "--surface", "rules", "--from", str(p),
        workdir=wd, expect=OK)


# ================================================ the fake behaves like QBO


def test_the_fake_grid_undercounts_the_way_the_real_one_does():
    g = FakeGrid(total=600)
    assert g.total() == 600
    assert g.displayed() < g.total()
    assert sum(p["rows"] for p in g.parts()) == g.total()


def test_a_naive_read_of_a_virtualized_grid_is_short():
    g = FakeGrid(total=600)
    assert len(g.naive_read()) == FakeGrid.WINDOW
    # And setting scrollTop changes nothing, which is what makes it invisible.
    g.set_scroll_top(9999)
    assert len(g.naive_read()) == FakeGrid.WINDOW
    assert g.full_read() and len(g.full_read()) == 600


# ============================================================ reading a page


def test_a_short_read_is_refused_and_names_the_virtualization():
    g = FakeGrid(total=600)
    try:
        browser.read_for_review(g.read_payload("1010", full=False))
        raise AssertionError("a 34 row read of a 600 row queue was accepted")
    except browser.ShapeError as exc:
        assert "virtualized" in str(exc)
        assert "wheel event" in str(exc)


def test_a_full_read_is_accepted_and_carries_the_undercount():
    g = FakeGrid(total=600)
    q = browser.read_for_review(g.read_payload("1010"))
    assert q.total == 600
    assert q.rows_read == 600
    assert q.undercount == 600 - g.displayed()


def test_an_unsplit_read_is_refused():
    try:
        browser.read_for_review({"account": "1010", "displayed": 401, "parts": []})
        raise AssertionError("a read with no parts was accepted")
    except browser.ShapeError as exc:
        assert "Split the type filter" in str(exc)


def test_the_same_filter_twice_is_refused():
    try:
        browser.read_for_review({
            "account": "1010", "displayed": 10,
            "parts": [{"filter": "Spent", "rows": 5}, {"filter": "spent", "rows": 5}]})
        raise AssertionError("a doubled filter was accepted")
    except browser.ShapeError as exc:
        assert "twice" in str(exc)


def test_a_stopped_feed_with_no_date_is_refused():
    try:
        browser.read_account_tiles({"accounts": [
            {"account": "1010", "feed_state": "stopped"}]})
        raise AssertionError("a stopped feed with no date was accepted")
    except browser.ShapeError as exc:
        assert "starts on a day" in str(exc)


def test_a_bank_balance_has_to_say_which_bank_screen_it_came_from():
    """The tile is what the books think, and comparing that to itself proves
    nothing."""
    try:
        browser.read_account_tiles({"accounts": [
            {"account": "1010", "feed_state": "live", "bank_balance": "37.14"}]})
        raise AssertionError("a bank balance with no source was accepted")
    except browser.ShapeError as exc:
        assert "own site" in str(exc)
    ok = browser.read_account_tiles({"accounts": [
        {"account": "1010", "feed_state": "live", "bank_balance": "37.14",
         "bank_balance_source": "the bank portal, accounts page"}]})
    assert ok[0]["bank_balance"] == "37.14"


def test_rules_without_the_auto_add_split_are_refused():
    try:
        browser.read_rules({"count": 15})
        raise AssertionError("a rules count with no auto-add split was accepted")
    except browser.ShapeError as exc:
        assert "without being seen" in str(exc)


def test_a_date_that_was_not_normalized_is_refused():
    try:
        browser.read_reconcile_summary({"accounts": [
            {"account": "1010", "reconciled_through": "06/30/2025"}]})
        raise AssertionError("an MM/DD/YYYY date was accepted")
    except browser.ShapeError as exc:
        assert "YYYY-MM-DD" in str(exc)


def test_a_read_that_carries_the_rows_stands_in_for_the_export():
    """Reading the queue off the screen and then asking for the same queue as an
    export is asking twice for one thing."""
    wd = workdir()
    confirm(wd)
    g = FakeGrid(total=60)
    payload = g.read_payload("1010")
    payload["rows"] = [
        {"date": "10/01/2025", "description": f"ACH DEBIT VENDOR {i}",
         "amount": "-100.25"} for i in range(g.total())]
    payload.pop("rows_read")
    src = wd / "queue.json"
    src.write_text(json.dumps(payload), encoding="utf-8")
    run("browser", "read", "--surface", "for-review", "--from", str(src),
        workdir=wd, expect=OK)
    written = sorted((wd / "for-review").glob("*.csv"))
    assert len(written) == 1, "the rows were read and never written down"
    lines = written[0].read_text().strip().splitlines()
    assert lines[0] == "Date,Description,Amount"
    assert len(lines) == 61, "the file does not hold every row that was read"


def test_a_queue_row_missing_a_field_is_refused_rather_than_filled_in():
    try:
        browser.read_for_review({
            "account": "1010", "displayed": 1,
            "parts": [{"filter": "Spent", "rows": 1}],
            "rows": [{"date": "", "description": "X", "amount": "-1.00"}]})
        raise AssertionError("a row with no date was accepted")
    except browser.ShapeError as exc:
        assert "no date" in str(exc)


def queue_read(wd, total, account="1010"):
    src = wd / f"queue-{total}.json"
    src.write_text(json.dumps({
        "account": account, "displayed": total - 12,
        "parts": [{"filter": "Received", "rows": 12},
                  {"filter": "Spent", "rows": total - 12}],
        "rows_read": total}), encoding="utf-8")
    return run("browser", "read", "--surface", "for-review", "--from", str(src),
               workdir=wd)


def test_a_queue_that_moved_with_no_approval_behind_it_is_a_finding():
    """The one check that would notice an agent clicking rows without asking.

    A hook cannot tell a click that posts from a click that opens a filter. Two
    reads of the same queue, against the batches approved between them, can.
    """
    wd = workdir()
    confirm(wd)
    quiet(wd)
    assert queue_read(wd, 600)[0] == OK
    code, out, err = queue_read(wd, 588)
    assert code == FAILURE
    assert "no approval behind them" in err
    assert "12 rows" in out
    assert "an agent working rows without asking" in out


def test_a_queue_that_moved_by_what_was_approved_is_clean():
    wd = workdir()
    confirm(wd)
    quiet(wd)
    assert queue_read(wd, 600)[0] == OK
    plan(wd, "c1_01")
    run("approve", "batch-c1_01", workdir=wd, expect=OK)
    run("browser", "post", "batch-c1_01", "--before", "600", workdir=wd, expect=OK)
    run("browser", "verify", "batch-c1_01", "--after", "575", workdir=wd, expect=OK)
    code, out, err = queue_read(wd, 575)
    assert code == OK, err
    assert "no approval behind them" not in out


def test_a_batch_is_only_counted_once_against_the_queue():
    """A run credited twice would hide a second, real, unapproved movement."""
    wd = workdir()
    confirm(wd)
    quiet(wd)
    queue_read(wd, 600)
    plan(wd, "c1_01")
    run("approve", "batch-c1_01", workdir=wd, expect=OK)
    run("browser", "post", "batch-c1_01", "--before", "600", workdir=wd, expect=OK)
    run("browser", "verify", "batch-c1_01", "--after", "575", workdir=wd, expect=OK)
    assert queue_read(wd, 575)[0] == OK
    # Nothing approved since, and five rows gone.
    code, out, err = queue_read(wd, 570)
    assert code == FAILURE
    assert "5 of 5 rows" in err


def test_a_queue_that_grew_says_what_that_usually_is():
    wd = workdir()
    confirm(wd)
    quiet(wd)
    queue_read(wd, 600)
    code, out, err = queue_read(wd, 620)
    assert code == FAILURE
    assert "The queue grew" in out
    assert "already booked month" in out


def test_a_page_that_said_something_was_wrong_stops_the_read():
    """The figures under a banner are usually the ones that were there before
    it appeared, which is what makes this the quiet failure."""
    for surface, payload in (
        (browser.read_for_review,
         {"account": "1010", "displayed": 5,
          "parts": [{"filter": "Spent", "rows": 5}],
          "error": "We could not load this page"}),
        (browser.read_rules,
         {"count": 15, "auto_add": 0, "banner": "Please sign in again"}),
        (browser.read_account_tiles,
         {"accounts": [{"account": "1010", "feed_state": "live"}],
          "error": "You do not have permission to view this"}),
    ):
        try:
            surface(payload)
            raise AssertionError(f"{surface.__name__} read across a banner")
        except browser.ShapeError as exc:
            assert "the page carried a message" in str(exc)


def test_a_banner_about_signing_in_is_never_a_prompt_for_a_password():
    try:
        browser.read_rules({"count": 1, "auto_add": 0,
                            "banner": "Your session expired, please sign in"})
        raise AssertionError("a session banner was read past")
    except browser.ShapeError as exc:
        assert "signs in again in their own window" in str(exc)
        assert "does not change because a run is" in str(exc)


# ============================================================== the company


def test_a_different_company_stops_everything():
    try:
        browser.confirm_company("Globex Industries LLC", COMPANY)
        raise AssertionError("the wrong company was accepted")
    except browser.CompanyMismatch as exc:
        assert "Everything stops here" in str(exc)


def test_punctuation_and_legal_form_spelling_do_not_count_as_a_difference():
    c = browser.confirm_company("Acme Robotics, Inc.", "Acme Robotics Inc.")
    assert c.observed == "Acme Robotics, Inc."


def test_an_inc_and_an_llc_with_the_same_name_are_different_companies():
    try:
        browser.confirm_company("Acme Robotics LLC", "Acme Robotics Inc.")
        raise AssertionError("an LLC was accepted as its Inc")
    except browser.CompanyMismatch as exc:
        assert "legal suffix" in str(exc)


def test_a_blank_company_read_is_refused():
    try:
        browser.confirm_company("", COMPANY)
        raise AssertionError("a blank company was accepted")
    except browser.CompanyMismatch as exc:
        assert "did not finish loading" in str(exc)


# =========================================================== the refused six


def test_every_destructive_action_is_refused_with_an_ordered_runbook():
    for key, act in browser.REFUSED.items():
        text = browser.refuse(act)
        assert "Refused:" in text
        assert "1." in text and "2." in text, f"{key} has no ordered runbook"
        assert act.destroys[:20] in text
        md = browser.runbook_markdown(key, company=COMPANY)
        assert "## What it destroys" in md and "## The order" in md


def test_the_words_for_a_destructive_action_are_caught_wherever_they_appear():
    cases = {
        "https://qbo.intuit.com/app/banking?action=disconnect": "disconnect",
        "click Merge accounts": "merge",
        "Exclude selected transactions": "exclude",
        "Delete this transaction": "delete",
        "Void this invoice": "void",
        "Undo last reconciliation": "undo-reconciliation",
    }
    for text, key in cases.items():
        act = browser.destructive_in(text)
        assert act is not None and act.key == key, f"{text!r} was not caught"
    assert browser.destructive_in("set the category and accept the row") is None
    assert browser.destructive_in("") is None


def test_disconnect_says_the_queue_is_the_only_record():
    text = browser.refuse("disconnect")
    assert "only record" in text
    assert "Export the For Review and Pending tabs" in text


# ================================================================ the batches


def test_a_batch_is_small_enough_to_read():
    assert browser.DEFAULT_BATCH_ROWS == 25
    batches = browser.slice_batches(rows(113), kind="categorize", account="1010")
    assert [len(b.rows) for b in batches] == [25, 25, 25, 25, 13]
    assert [b.tag for b in batches] == ["s01", "s02", "s03", "s04", "s05"]


def test_a_batch_past_the_limit_is_refused():
    try:
        browser.slice_batches(rows(10), kind="categorize", account="1010", size=600)
        raise AssertionError("a 600 row batch was accepted")
    except browser.BrowserError as exc:
        assert "scroll bar" in str(exc)


def test_an_empty_batch_is_refused():
    try:
        browser.PostBatch(tag="s01", kind="categorize", account="1010", rows=[])
        raise AssertionError("an empty batch was accepted")
    except browser.BrowserError as exc:
        assert "covers nothing" in str(exc)


def test_the_hash_moves_when_a_single_account_changes():
    a = browser.PostBatch(tag="s01", kind="categorize", account="1010",
                          rows=rows(3))
    b = browser.PostBatch(tag="s01", kind="categorize", account="1010",
                          rows=rows(3))
    assert a.row_hash() == b.row_hash()
    b.rows[1].account = "6800 Professional Fees"
    assert a.row_hash() != b.row_hash()


def test_the_expected_movement_has_the_right_sign_for_each_kind():
    assert browser.PostBatch(tag="a", kind="categorize", account="1010",
                             rows=rows(4)).expected_delta == -4
    assert browser.PostBatch(tag="b", kind="add", account="1010",
                             rows=rows(4)).expected_delta == 4
    assert browser.PostBatch(tag="c", kind="journal", account="",
                             rows=rows(4)).expected_delta == 4


def test_every_row_is_written_out_where_a_person_reads_it():
    batch = browser.PostBatch(tag="s01", kind="categorize", account="1010",
                              rows=rows(25), company=COMPANY)
    md = batch.to_markdown()
    for r in batch.rows:
        assert r.descriptor in md
        assert r.why in md
    assert "25 of 25 rows listed above, in full" in md
    assert "python3 bin/books.py approve batch-s01" in md


# =================================================== the count after a batch


def test_the_count_check_passes_when_the_queue_falls_by_the_approved_amount():
    g = FakeGrid(total=600)
    batch = browser.PostBatch(tag="s01", kind="categorize", account="1010",
                              rows=rows(25), company=COMPANY)
    before = g.total()
    run_ = browser.open_run(batch, before=before, approved_by="the owner",
                            approved_at="now", company=COMPANY)
    g.accept(25)
    done = browser.verify_run(run_, g.total(), company=COMPANY)
    assert done.state == "verified"
    assert done.after == before - 25


def test_a_rule_firing_underneath_the_work_halts_the_run():
    """The whole reason the count is read twice."""
    g = FakeGrid(total=600, rule_fires=True)
    batch = browser.PostBatch(tag="s01", kind="categorize", account="1010",
                              rows=rows(25), company=COMPANY)
    run_ = browser.open_run(batch, before=g.total(), approved_by="the owner",
                            approved_at="now", company=COMPANY)
    g.accept(25)
    try:
        browser.verify_run(run_, g.total(), company=COMPANY)
        raise AssertionError("a queue that fell by 26 was accepted as 25")
    except browser.CountMismatch as exc:
        assert "1 more rows moved than were approved" in str(exc)
        assert "auto-add" in str(exc)
    assert run_.state == "halted"


def test_a_batch_that_did_not_land_halts_the_run():
    g = FakeGrid(total=600)
    batch = browser.PostBatch(tag="s01", kind="categorize", account="1010",
                              rows=rows(25), company=COMPANY)
    run_ = browser.open_run(batch, before=g.total(), approved_by="x",
                            approved_at="now", company=COMPANY)
    g.accept(20)
    try:
        browser.verify_run(run_, g.total(), company=COMPANY)
        raise AssertionError("a partial batch was accepted")
    except browser.CountMismatch as exc:
        assert "fewer rows moved" in str(exc)


def test_a_count_that_moved_the_wrong_way_is_the_loudest_signal():
    batch = browser.PostBatch(tag="s01", kind="categorize", account="1010",
                              rows=rows(5))
    run_ = browser.open_run(batch, before=100, approved_by="x", approved_at="now")
    try:
        browser.verify_run(run_, 105)
        raise AssertionError("a queue that grew was accepted as a categorization")
    except browser.CountMismatch as exc:
        assert "wrong way" in str(exc)


def test_a_count_read_in_another_company_halts_the_run():
    batch = browser.PostBatch(tag="s01", kind="categorize", account="1010",
                              rows=rows(5), company=COMPANY)
    run_ = browser.open_run(batch, before=100, approved_by="x", approved_at="now",
                            company=COMPANY)
    try:
        browser.verify_run(run_, 95, company="Globex Industries LLC")
        raise AssertionError("a count from another company file was accepted")
    except browser.CompanyMismatch as exc:
        assert "proves nothing about this one" in str(exc)
    assert run_.state == "halted"


def test_a_run_is_verified_once():
    batch = browser.PostBatch(tag="s01", kind="categorize", account="1010",
                              rows=rows(5))
    run_ = browser.open_run(batch, before=100, approved_by="x", approved_at="now")
    browser.verify_run(run_, 95)
    try:
        browser.verify_run(run_, 90)
        raise AssertionError("a verified run was verified again")
    except browser.RunError as exc:
        assert "already verified" in str(exc)


# ================================================== the approval, end to end


def test_an_unapproved_batch_cannot_be_posted():
    wd = workdir()
    confirm(wd)
    quiet(wd)
    plan(wd, "s01")
    code, _, err = run("browser", "post", "batch-s01", "--before", "600",
                       workdir=wd, expect=REFUSAL)
    assert "has not been approved" in err
    assert not browser.all_runs(wd), "a run was opened without an approval"


def test_an_approval_for_one_batch_does_not_cover_another():
    wd = workdir()
    confirm(wd)
    quiet(wd)
    plan(wd, "s01")
    plan(wd, "s02")
    run("approve", "batch-s01", workdir=wd, expect=OK)
    code, _, err = run("browser", "post", "batch-s02", "--before", "600",
                       workdir=wd, expect=REFUSAL)
    assert "batch-s02 has not been approved" in err
    # And the one that was approved still works.
    run("browser", "post", "batch-s01", "--before", "600", workdir=wd, expect=OK)


def test_editing_a_batch_after_approval_voids_it():
    wd = workdir()
    confirm(wd)
    quiet(wd)
    batch = plan(wd, "s01")
    run("approve", "batch-s01", workdir=wd, expect=OK)
    batch.rows[0].account = "6800 Professional Fees"
    (wd / "review" / "batch-s01.json").write_text(batch.to_json(), encoding="utf-8")
    code, _, err = run("browser", "post", "batch-s01", "--before", "600",
                       workdir=wd, expect=REFUSAL)
    assert "has changed since" in err


def test_a_plan_edited_by_hand_is_refused_before_anything_else():
    wd = workdir()
    confirm(wd)
    quiet(wd)
    plan(wd, "s01")
    data = json.loads((wd / "review" / "batch-s01.json").read_text())
    data["rows"][0]["account"] = "1000 Cash"          # hash left as it was
    (wd / "review" / "batch-s01.json").write_text(json.dumps(data))
    code, _, err = run("browser", "post", "batch-s01", "--before", "600",
                       workdir=wd, expect=REFUSAL)
    assert "edited by hand" in err


def test_an_agent_cannot_approve_and_the_gate_says_who_can():
    """The library refuses to write an approval for a batch that is not there,
    and the hook refuses the command itself. This covers the first half."""
    wd = workdir()
    try:
        write_approval(wd / "review", "nothing_here", "an agent")
        raise AssertionError("an approval was written for a batch that is not there")
    except ApprovalError as exc:
        assert "Nothing to approve" in str(exc)


def test_a_count_mismatch_halts_the_run_and_stops_the_next_batch():
    wd = workdir()
    confirm(wd)
    quiet(wd)
    plan(wd, "s01")
    plan(wd, "s02")
    run("approve", "batch-s01", workdir=wd, expect=OK)
    run("approve", "batch-s02", workdir=wd, expect=OK)
    run("browser", "post", "batch-s01", "--before", "600", workdir=wd, expect=OK)
    code, _, err = run("browser", "verify", "batch-s01", "--after", "573",
                       workdir=wd, expect=FAILURE)
    assert "Halted after batch-s01" in err
    assert "auto-add" in err

    # Everything waits on a halt, including a batch that was approved before it.
    code, _, err = run("browser", "post", "batch-s02", "--before", "573",
                       workdir=wd, expect=REFUSAL)
    assert "halted and has not been cleared" in err

    # Clearing takes a note, and an empty one is refused.
    code, _, err = run("browser", "clear", "batch-s01", "--note", "  ",
                       workdir=wd, expect=REFUSAL)
    assert "needs a note" in err

    run("browser", "clear", "batch-s01", "--note",
        "a rule with auto-add on posted two rows while the batch ran",
        workdir=wd, expect=OK)
    run("browser", "post", "batch-s02", "--before", "573", workdir=wd, expect=OK)


def test_only_one_run_is_open_at_a_time():
    wd = workdir()
    confirm(wd)
    quiet(wd)
    plan(wd, "s01")
    plan(wd, "s02")
    run("approve", "batch-s01", workdir=wd, expect=OK)
    run("approve", "batch-s02", workdir=wd, expect=OK)
    run("browser", "post", "batch-s01", "--before", "600", workdir=wd, expect=OK)
    code, _, err = run("browser", "post", "batch-s02", "--before", "575",
                       workdir=wd, expect=REFUSAL)
    assert "is still open" in err


def test_nothing_is_posted_while_a_rule_still_posts_by_itself():
    wd = workdir()
    confirm(wd)
    quiet(wd, auto_add=3)
    plan(wd, "s01")
    run("approve", "batch-s01", workdir=wd, expect=OK)
    code, _, err = run("browser", "post", "batch-s01", "--before", "600",
                       workdir=wd, expect=REFUSAL)
    assert "post without being seen" in err


def test_nothing_is_posted_before_anyone_says_what_the_rules_are():
    wd = workdir()
    confirm(wd)
    plan(wd, "s01")
    run("approve", "batch-s01", workdir=wd, expect=OK)
    code, _, err = run("browser", "post", "batch-s01", "--before", "600",
                       workdir=wd, expect=REFUSAL)
    assert "how many bank rules are on" in err


def test_nothing_runs_before_the_company_is_confirmed():
    wd = workdir()
    plan(wd, "s01")
    for args in (("browser", "post", "batch-s01", "--before", "600"),
                 ("browser", "plan", "--kind", "categorize")):
        code, _, err = run(*args, workdir=wd, expect=REFUSAL)
        assert "company has not been confirmed" in err


def test_confirming_the_wrong_company_refuses_at_the_command():
    wd = workdir()
    code, _, err = run("browser", "confirm", "--company", "Globex Industries LLC",
                       workdir=wd, expect=REFUSAL)
    assert "Everything stops here" in err


def test_the_whole_flow_against_the_fake_grid():
    """Plan, approve, post, work the queue, verify. Twice, to prove the second
    approval is its own."""
    wd = workdir()
    confirm(wd)
    quiet(wd)
    g = FakeGrid(total=600)

    # The read the queue count comes from is checked before it is believed.
    payload = wd / "queue.json"
    payload.write_text(json.dumps(g.read_payload("1010")), encoding="utf-8")
    run("browser", "read", "--surface", "for-review", "--from", str(payload),
        workdir=wd, expect=OK)

    for tag in ("s01", "s02"):
        plan(wd, tag)
        run("approve", f"batch-{tag}", workdir=wd, expect=OK)
        before = g.total()
        run("browser", "post", f"batch-{tag}", "--before", str(before),
            workdir=wd, expect=OK)
        g.accept(25)
        run("browser", "verify", f"batch-{tag}", "--after", str(g.total()),
            workdir=wd, expect=OK)

    assert g.total() == 550
    assert all(r["klass"] for r in g.rows if r["accepted"]), \
        "a row was posted with no class, which QuickBooks writes as Not specified"
    states = {r.tag: r.state for r in browser.all_runs(wd)}
    assert states == {"s01": "verified", "s02": "verified"}


def test_status_reports_what_is_approved_and_what_is_not():
    wd = workdir()
    confirm(wd)
    plan(wd, "s01")
    plan(wd, "s02")
    run("approve", "batch-s01", workdir=wd, expect=OK)
    code, out, _ = run("browser", "status", workdir=wd, expect=OK)
    assert "batch-s01" in out and "approved" in out
    assert "not approved" in out


def test_the_runbook_command_writes_the_by_hand_order():
    wd = workdir()
    confirm(wd)
    code, out, _ = run("browser", "runbook", "disconnect", workdir=wd, expect=OK)
    written = wd / "reports" / "by-hand-disconnect.md"
    assert written.exists()
    text = written.read_text()
    assert "only record" in text
    assert text.index("1. Export") < text.index("4. Only now, disconnect")


# ============================================================== the fixture


def test_the_page_fixture_still_reproduces_every_trap():
    """The page is how the reading recipe gets exercised in a real browser.

    A fixture that quietly stops reproducing a trap is worse than no fixture,
    because the recipe then passes against a page that is easier than the real
    one.
    """
    page = FIXTURES / "for-review-grid.html"
    assert page.exists(), "the For Review fixture page is missing"
    text = page.read_text(encoding="utf-8")
    for trap in ("wheel",            # only a real wheel event re-renders
                 "__rowModel",       # the whole row model, off the instance
                 "phantom",          # the checkbox that only exists on hover
                 "Not specified",    # what a class-less post writes
                 "fireRule"):        # a rule moving the count underneath
        assert trap in text, f"the fixture no longer reproduces {trap}"


def test_the_traps_in_the_module_match_the_ones_in_the_fixture():
    traps = " ".join(t for s in browser.SURFACES.values() for t in s.traps)
    for phrase in ("virtualized", "hover", "Class column", "fresh tab",
                   "Match toggle"):
        assert phrase in traps, f"{phrase} is not written down anywhere in the module"


def test_the_report_tokens_that_hang_are_recorded():
    assert "PANDL" in browser.REPORT_TOKENS_GOOD
    assert "PROFITANDLOSS" in browser.REPORT_TOKENS_HANG
    assert not set(browser.REPORT_TOKENS_GOOD) & set(browser.REPORT_TOKENS_HANG)


def main():
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failed = 0
    for fn in fns:
        try:
            fn()
            print(f"PASS {fn.__name__}")
        except AssertionError as exc:
            failed += 1
            print(f"FAIL {fn.__name__}: {exc}")
    print(f"\n{len(fns) - failed}/{len(fns)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
