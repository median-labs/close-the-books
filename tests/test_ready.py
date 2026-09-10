"""`ready` is fail-closed, and this file is what keeps it that way.

The whole value of the command is the refusal. A package that says a set of
books is ready while the firm filing the return is still waiting on a signed
instrument is worse than no package, because somebody acts on it.

The test that matters most here is
`test_ready_never_passes_while_one_requirement_is_outstanding`. It puts every
other check in the passing state and leaves one requirement with nothing
recorded against it, which is exactly the shape somebody would be in at four in
the afternoon on the day of a deadline. If that test ever passes for the wrong
reason, treat it as a stop and never fix it by loosening the gate.

The rest run the command itself against the invented example company, because a
gate that is right in a unit test and unreachable from the command line is not a
gate.

    python3 tests/test_ready.py
"""

import json
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile

REPO = pathlib.Path(__file__).resolve().parent.parent
BOOKS = REPO / "bin" / "books.py"
EXAMPLE = REPO / "examples" / "acme-robotics"
sys.path.insert(0, str(REPO / "lib"))

OK, FAILURE, REFUSAL = 0, 1, 2

_WORK = None


def load_cli():
    import importlib.util
    spec = importlib.util.spec_from_file_location("books_cli", BOOKS)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def run(*args, workdir=None, expect=None):
    cmd = [sys.executable, str(BOOKS)]
    if workdir is not None:
        cmd += ["--workdir", str(workdir)]
    cmd += [str(a) for a in args]
    p = subprocess.run(cmd, capture_output=True, text=True, cwd=str(REPO))
    if expect is not None and p.returncode != expect:
        raise AssertionError(
            f"`books.py {' '.join(str(a) for a in args)}` exited {p.returncode}, "
            f"expected {expect}\n--- stdout ---\n{p.stdout}\n--- stderr ---\n{p.stderr}")
    return p.returncode, p.stdout, p.stderr


def ensure_example():
    exports = EXAMPLE / "exports"
    if exports.is_dir() and any(exports.glob("*.xlsx")):
        return
    p = subprocess.run([sys.executable, str(EXAMPLE / "build.py")],
                       capture_output=True, text=True, cwd=str(REPO))
    if p.returncode != 0:
        raise AssertionError(f"could not build the example company:\n{p.stderr}")


def work():
    """A working directory holding the example company's files, built once."""
    global _WORK
    if _WORK is not None:
        return _WORK
    ensure_example()
    root = pathlib.Path(tempfile.mkdtemp(prefix="ctb-ready-"))
    run("init", workdir=root, expect=OK)
    for name in ("exports", "statements", "for-review"):
        src = EXAMPLE / name
        if src.is_dir():
            for p in src.iterdir():
                if p.is_file():
                    shutil.copy2(p, root / name / p.name)
    run("learn", "--exports", str(root / "exports"), workdir=root, expect=OK)
    run("filing-year", "2025", "--due", "2026-04-15", workdir=root, expect=OK)
    _WORK = root
    return root


def requirements_json(wd):
    return json.loads(
        (wd / "reports" / "filing-requirements.json").read_text(encoding="utf-8"))


# ------------------------------------------------------------------ the gate

def _scan(outstanding=0, unknown=0, settled=0):
    from closethebooks.readiness import Requirement, RequirementScan
    reqs = []
    for i in range(outstanding):
        reqs.append(Requirement(id=f"filing.test.open{i}", group="contractors",
                                need="n", why="w", trigger="t"))
    for i in range(unknown):
        reqs.append(Requirement(id=f"filing.test.unknown{i}", group="contractors",
                                need="n", why="w", trigger="t",
                                answer="Cannot answer: our former bookkeeper has it",
                                answered_on="2026-09-10", source="A. Founder",
                                unknown=True,
                                unknown_reason="our former bookkeeper has it"))
    for i in range(settled):
        reqs.append(Requirement(id=f"filing.test.done{i}", group="contractors",
                                need="n", why="w", trigger="t", answer="yes",
                                answered_on="2026-09-10", source="A. Founder"))
    return RequirementScan(requirements=reqs, filing_year=2025)


def _checks(cli, done=4, failing=0):
    out = [cli.ReadyCheck(f"check {i}", True, f"{i} of {i}") for i in range(done)]
    out += [cli.ReadyCheck(f"broken {i}", False, "0 of 1", "do the thing")
            for i in range(failing)]
    return out


def test_ready_never_passes_while_one_requirement_is_outstanding():
    """Every bookkeeping check green, one thing nobody has answered. Still no."""
    cli = load_cli()
    checks = _checks(cli, done=7)
    assert all(c.done for c in checks), "the fixture has to be otherwise perfect"
    scanned = _scan(outstanding=1, settled=20)
    assert cli.ready_is_declarable(checks, scanned) is False


def test_ready_passes_when_every_check_is_done_and_nothing_is_outstanding():
    cli = load_cli()
    assert cli.ready_is_declarable(_checks(cli, done=7), _scan(settled=20)) is True


def test_a_recorded_unknown_carries_its_reason_and_does_not_block():
    """A stated unknown is a record. A blank is not, and that is the difference."""
    cli = load_cli()
    scanned = _scan(unknown=2, settled=18)
    assert cli.ready_is_declarable(_checks(cli, done=7), scanned) is True
    for req in scanned.unknowns:
        assert req.unknown_reason
        assert req.source and req.answered_on


def test_ready_refuses_while_any_bookkeeping_check_is_outstanding():
    cli = load_cli()
    assert cli.ready_is_declarable(_checks(cli, done=6, failing=1),
                                   _scan(settled=20)) is False


def test_the_gate_reads_the_requirements_and_not_a_report_of_them():
    """The one condition is written once, so there is one place to loosen."""
    import inspect
    cli = load_cli()
    body = inspect.getsource(cli.ready_is_declarable)
    assert "scanned.outstanding" in body
    assert "not c.done" in body


# ------------------------------------------------------------- the command

def test_ready_refuses_on_the_example_company_and_names_what_is_missing():
    wd = work()
    code, out, err = run("ready", workdir=wd, expect=REFUSAL)
    assert "Not ready" in out
    assert "checks are outstanding" in out
    assert "no flag that turns this off" in out
    assert "Traceback" not in err


def test_a_refused_ready_writes_no_package():
    wd = work()
    run("ready", workdir=wd, expect=REFUSAL)
    assert not (wd / "handoff" / "ready.md").exists()
    assert not (wd / "handoff" / "filing-answers.md").exists()


def test_requirements_are_raised_from_the_example_companys_own_books():
    wd = work()
    code, out, _ = run("requirements", workdir=wd, expect=OK)
    data = requirements_json(wd)
    subjects = {r["subject"] for r in data["requirements"]}
    assert "convertible instruments the company issued" in subjects, (
        "the example company carries a 750,000.00 SAFE in equity")
    assert "a related company outside the United States" not in subjects
    assert "cryptocurrency and other digital assets" not in subjects
    assert data["total"] > 0
    for row in data["requirements"]:
        assert row["raised by"].strip(), row["id"]
        assert row["why"].strip(), row["id"]


def test_every_requirement_reaches_the_owner_through_the_one_question_store():
    """A filing requirement is a question, so it lives where questions live."""
    wd = work()
    run("requirements", workdir=wd, expect=OK)
    prof = json.loads(
        (wd / "profiles" / "mine.local.json").read_text(encoding="utf-8"))
    ids = {q["id"] for q in prof["open_questions"]}
    for row in requirements_json(wd)["requirements"]:
        assert row["id"] in ids, row["id"]


def test_answering_a_document_requirement_with_a_sentence_is_refused():
    wd = work()
    run("requirements", workdir=wd, expect=OK)
    code, out, err = run("answer", "filing.convertible.instrument",
                         "we have it somewhere", workdir=wd, expect=REFUSAL)
    assert "needs the document itself" in err
    assert "books.py provide" in err


def test_a_reason_that_states_nothing_is_refused_rather_than_recorded():
    wd = work()
    run("requirements", workdir=wd, expect=OK)
    code, out, err = run("answer", "filing.meals.split", "--cannot", "unknown",
                         workdir=wd, expect=REFUSAL)
    assert "says nothing" in err


def test_a_document_is_copied_recorded_and_checksummed():
    wd = work()
    run("requirements", workdir=wd, expect=OK)
    source = wd / "cap-table.csv"
    source.write_text("holder,percent\nA. Founder,100\n", encoding="utf-8")
    code, out, _ = run("provide", "filing.ownership-roster.cap-table", str(source),
                       "--by", "A. Founder", workdir=wd, expect=OK)
    assert "Document recorded" in out
    stored = wd / "documents" / "ownership-roster-cap-table.csv"
    assert stored.is_file()
    assert source.is_file(), "the file is copied, never moved"
    index = json.loads((wd / "documents" / "_index.json").read_text(encoding="utf-8"))
    row = next(r for r in index if r["requirement"] ==
               "filing.ownership-roster.cap-table")
    assert len(row["sha256"]) == 64
    assert row["by"] == "A. Founder"
    rows = requirements_json(wd)["requirements"]
    hit = next(r for r in rows if r["id"] == "filing.ownership-roster.cap-table")
    assert "document recorded" in hit["status"]


def test_an_empty_file_is_refused_because_it_reads_as_done():
    wd = work()
    run("requirements", workdir=wd, expect=OK)
    empty = wd / "nothing.pdf"
    empty.write_bytes(b"")
    code, out, err = run("provide", "filing.convertible.side-letters", str(empty),
                         workdir=wd, expect=REFUSAL)
    assert "empty" in err


def test_recording_that_nobody_can_answer_keeps_the_reason_and_the_name():
    wd = work()
    run("requirements", workdir=wd, expect=OK)
    run("answer", "filing.meals.split", "--cannot",
        "our former bookkeeper holds the receipts, asked them on 2026-09-10",
        "--by", "A. Founder", workdir=wd, expect=OK)
    # `answer` does not load the exports, so the written list is regenerated by
    # the next command that does. Nothing downstream reads the snapshot.
    run("requirements", workdir=wd, expect=OK)
    rows = requirements_json(wd)["requirements"]
    hit = next(r for r in rows if r["id"] == "filing.meals.split")
    assert hit["status"] == "recorded as unanswerable"
    assert hit["who"] == "A. Founder"
    text = (wd / "reports" / "filing-requirements.md").read_text(encoding="utf-8")
    assert "former bookkeeper" in text


def test_the_written_list_says_what_raised_every_subject():
    wd = work()
    run("requirements", workdir=wd, expect=OK)
    text = (wd / "reports" / "filing-requirements.md").read_text(encoding="utf-8")
    assert "none of it is tax advice" in text
    assert "Why the return needs it:" in text
    assert "In your books:" in text


def test_ready_still_refuses_after_every_requirement_is_recorded():
    """The bookkeeping half is a separate half, and it has to hold on its own."""
    wd = work()
    # An answer can raise the next requirement, so this loops until the list
    # stops growing rather than assuming one pass empties it.
    for _ in range(6):
        run("requirements", workdir=wd, expect=OK)
        outstanding = [r for r in requirements_json(wd)["requirements"]
                       if r["status"] == "outstanding"]
        if not outstanding:
            break
        for row in outstanding:
            if row["needed"] == "document":
                run("provide", row["id"], "--cannot",
                    "our former bookkeeper holds this one, asked on 2026-09-10",
                    workdir=wd, expect=OK)
            else:
                run("answer", row["id"], "recorded for the test", workdir=wd,
                    expect=OK)
    run("requirements", workdir=wd, expect=OK)
    assert requirements_json(wd)["outstanding"] == 0
    code, out, _ = run("ready", workdir=wd, expect=REFUSAL)
    assert "The ten exit tests pass" in out
    assert "Not ready" in out


# ----------------------------------------------------- what the package says

def _passing_package(unknowns=1):
    """The two documents `ready` writes when it passes, rendered."""
    from closethebooks import exit_tests
    from closethebooks.profile import Entity, Profile
    cli = load_cli()
    prof = Profile(entity=Entity(name="Harbor Consulting Inc.", basis="accrual",
                                 end_use="the 2025 return", deadline="2026-04-15"))
    scanned = _scan(unknown=unknowns, settled=2)
    scanned.requirements[-1].need = "The current cap table"
    scanned.requirements[-1].answer = "Two holders, both in the United States."
    report = exit_tests.ExitTestReport(
        company="Harbor Consulting Inc.",
        tests=[exit_tests.ExitTest(number=n, name=f"check {n}", passed=True,
                                   measured={"difference": 0}) for n in range(1, 11)])
    checks = _checks(cli, done=7)
    live = {"filing": {"year": "2025", "due": "2026-04-15"}}
    return (cli.render_ready_summary(prof, live, 2025, checks, scanned, report),
            cli.render_filing_answers(prof, scanned, 2025))


def test_the_package_records_what_was_decided_and_by_whom():
    summary, answers = _passing_package()
    assert "A. Founder" in summary
    assert "2026-09-10" in summary
    assert "Two holders, both in the United States." in summary
    assert "The current cap table" in answers


def test_the_package_names_what_is_still_to_chase():
    summary, answers = _passing_package(unknowns=2)
    assert "What is still to chase" in summary
    assert summary.count("our former bookkeeper has it") >= 2
    assert "will have to chase" in answers


def test_a_package_with_nothing_to_chase_says_so_rather_than_going_quiet():
    summary, _ = _passing_package(unknowns=0)
    assert "Nothing. Every requirement raised by these books" in summary


def test_the_package_says_it_is_not_tax_advice():
    for text in _passing_package():
        assert "none of it is tax advice" in text


def test_the_package_carries_no_long_dash():
    for text in _passing_package():
        for dash in (chr(0x2013), chr(0x2014)):
            assert dash not in text


if __name__ == "__main__":
    passed = 0
    for name, fn in sorted(list(globals().items())):
        if name.startswith("test_") and callable(fn):
            fn()
            passed += 1
    print(f"{passed} of {passed} ready-gate tests pass")
    if _WORK is not None:
        shutil.rmtree(_WORK, ignore_errors=True)
