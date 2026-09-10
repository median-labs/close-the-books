"""The PreToolUse gate on browser calls.

It is the coarse lock, and these tests hold it to what it actually claims:
refuse the destructive vocabulary anywhere, refuse a scripted write with no run
open, refuse everything while a halt stands, refuse the command that clears a
halt, and stay out of the way otherwise. Where it does not claim to catch
something, that is tested too, so nobody later reads a passing suite as a
promise the gate never made.

Exit codes are the Claude Code contract: 0 allows, 2 blocks.
"""

import json
import pathlib
import shutil
import subprocess
import sys
import tempfile

REPO = pathlib.Path(__file__).resolve().parent.parent
HOOK = REPO / "hooks" / "browser-write-gate.py"
EXAMPLE_PROFILE = REPO / "profiles" / "example-acme-robotics.json"
sys.path.insert(0, str(REPO / "lib"))

from closethebooks import browser  # noqa: E402


def workdir(*, halted=False, open_run=False):
    """A close-the-books working directory, which is what turns the gate on."""
    wd = pathlib.Path(tempfile.mkdtemp(prefix="ctb-gate-"))
    for name in ("review", "import", "reports", "profiles"):
        (wd / name).mkdir(parents=True, exist_ok=True)
    shutil.copy2(EXAMPLE_PROFILE, wd / "profiles" / "mine.local.json")
    if halted or open_run:
        run = browser.Run(
            tag="s01", kind="categorize", account="1010", rows=25,
            row_hash="x", company="Acme Robotics Inc.", counter="for-review:1010",
            before=600, expected_delta=-25, approved_by="the owner",
            approved_at="now", opened_at="now",
            state="halted" if halted else "open",
            halt_reason="Halted after batch-s01. The count is not what the "
                        "approval predicted." if halted else "")
        browser.save_run(wd, run)
    return wd


def hook(payload, cwd):
    p = subprocess.run(
        [sys.executable, str(HOOK)],
        input=json.dumps(payload), capture_output=True, text=True, cwd=str(cwd),
        env={"PATH": "/usr/bin:/bin:/usr/sbin:/sbin", "HOME": str(cwd)},
    )
    return p.returncode, (p.stderr or "").strip()


def browser_call(tool, **inp):
    return {"tool_name": f"mcp__claude-in-chrome__{tool}", "tool_input": inp}


# ------------------------------------------------------------ staying quiet


def test_outside_a_working_directory_it_does_nothing():
    """Somebody browsing is not somebody closing their books."""
    plain = pathlib.Path(tempfile.mkdtemp(prefix="not-books-"))
    code, _ = hook(browser_call("javascript_tool",
                                text="document.querySelector('b').click()"), plain)
    assert code == 0


def test_a_read_is_allowed_when_nothing_has_halted():
    wd = workdir()
    for tool in ("read_page", "get_page_text", "find", "read_console_messages"):
        code, err = hook(browser_call(tool), wd)
        assert code == 0, f"{tool} was blocked: {err}"


def test_the_documented_way_to_read_a_virtualized_grid_is_allowed():
    """A blanket ban on scripts would ban the read that works."""
    wd = workdir()
    code, err = hook(browser_call(
        "javascript_tool",
        text="tbl.getRowModel().rows.map(r => r.original)"), wd)
    assert code == 0, err


def test_an_ordinary_shell_command_is_not_read_as_a_quickbooks_action():
    """`git branch --delete` is not an attempt to delete a transaction, and a
    gate that fires on ordinary work is a gate people route around."""
    wd = workdir()
    for cmd in ("git branch --delete old-work",
                "rm -rf build/",
                "python3 bin/books.py browser runbook delete"):
        code, err = hook({"tool_name": "Bash", "tool_input": {"command": cmd}}, wd)
        assert code == 0, f"{cmd!r} was blocked: {err}"


# ------------------------------------------------------------- the refusals


def test_a_destructive_action_is_refused_wherever_it_appears():
    wd = workdir()
    cases = [
        browser_call("navigate", url="https://x.example/app/banking?disconnect=1"),
        browser_call("computer", action="type", text="Delete this transaction"),
        browser_call("find", query="Merge accounts"),
        browser_call("browser_batch", actions=[
            {"name": "computer", "input": {"action": "left_click", "ref": "ref_1"}},
            {"name": "computer", "input": {"action": "type",
                                           "text": "Exclude selected transactions"}},
        ]),
    ]
    for payload in cases:
        code, err = hook(payload, wd)
        assert code == 2, f"not blocked: {payload}"
        assert "Refused:" in err
        assert "you do it by hand, in this order" in err


def test_a_destructive_word_nested_deep_in_a_batch_is_still_caught():
    """A rule that only reads the top level is a rule that reads the wrapper."""
    wd = workdir()
    code, err = hook(browser_call("browser_batch", actions=[
        {"name": "navigate", "input": {"url": "https://x.example/ok"}},
        {"name": "computer", "input": {"action": "left_click",
                                       "label": "Undo last reconciliation"}},
    ]), wd)
    assert code == 2 and "Refused:" in err


def test_a_scripted_write_with_no_run_open_is_refused():
    wd = workdir()
    code, err = hook(browser_call(
        "javascript_tool",
        text="document.querySelector('#accept').click()"), wd)
    assert code == 2
    assert "there is no run open" in err
    assert "python3 bin/books.py approve batch-XX" in err


def test_a_scripted_write_is_allowed_once_a_run_is_open():
    wd = workdir(open_run=True)
    code, err = hook(browser_call(
        "javascript_tool",
        text="document.querySelector('#accept').click()"), wd)
    assert code == 0, err


def test_an_upload_with_no_run_open_is_refused():
    wd = workdir()
    code, err = hook({"tool_name": "mcp__claude-in-chrome__file_upload",
                      "tool_input": {"path": "import/bank.csv"}}, wd)
    assert code == 2 and "a file upload needs an approved batch" in err


def test_a_halt_stops_every_browser_call_including_a_read():
    wd = workdir(halted=True)
    for tool in ("read_page", "navigate", "computer"):
        code, err = hook(browser_call(tool), wd)
        assert code == 2, f"{tool} ran while a halt stood"
        assert "halted and nobody has cleared it" in err
        assert "browser clear" in err


def test_clearing_a_halt_is_refused_to_an_agent():
    wd = workdir(halted=True)
    code, err = hook({"tool_name": "Bash", "tool_input": {
        "command": 'python3 bin/books.py browser clear batch-s01 --note "a rule"'}}, wd)
    assert code == 2
    assert "made the halt decorative" in err


def test_writing_a_run_file_by_hand_is_refused():
    wd = workdir()
    for payload in (
        {"tool_name": "Write", "tool_input": {
            "file_path": str(wd / "reports/browser-runs/s01.json")}},
        {"tool_name": "Bash", "tool_input": {
            "command": 'echo "{}" > reports/browser-runs/s01.json'}},
    ):
        code, err = hook(payload, wd)
        assert code == 2, f"not blocked: {payload}"
        assert "run file" in err


def test_editing_the_gate_from_inside_a_run_is_refused():
    wd = workdir()
    code, err = hook({"tool_name": "Edit", "tool_input": {
        "file_path": str(REPO / "lib/closethebooks/browser.py")}}, wd)
    assert code == 2 and "part of the gate" in err


# ------------------------------------------------------------- failing shut


def test_a_broken_call_is_refused_rather_than_allowed():
    """A gate that fails open is a gate that is off and looks like one that
    passed."""
    wd = workdir()
    p = subprocess.run([sys.executable, str(HOOK)], input="not json at all",
                       capture_output=True, text=True, cwd=str(wd))
    assert p.returncode == 2
    assert "Refusing rather than allowing" in p.stderr


def test_an_empty_call_is_allowed():
    wd = workdir()
    p = subprocess.run([sys.executable, str(HOOK)], input="",
                       capture_output=True, text=True, cwd=str(wd))
    assert p.returncode == 0


def test_an_unreadable_run_file_stops_the_run():
    """A run whose outcome cannot be read is a run whose outcome is unknown."""
    wd = workdir()
    (wd / "reports" / "browser-runs").mkdir(parents=True, exist_ok=True)
    (wd / "reports" / "browser-runs" / "s01.json").write_text("{ broken")
    code, err = hook(browser_call(
        "javascript_tool", text="document.querySelector('#a').click()"), wd)
    assert code == 2
    assert "Refusing" in err or "not a readable run file" in err


# ------------------------------------------------- what it does NOT promise


def test_it_does_not_claim_to_gate_an_ordinary_click():
    """Working a queue means clicking filters and column settings, and no rule
    separates those from a click that posts. The library gate and the count
    check are what stand there, and the hook says so in its own header rather
    than pretending otherwise."""
    wd = workdir()
    code, _ = hook(browser_call("computer", action="left_click", ref="ref_12"), wd)
    assert code == 0
    header = HOOK.read_text(encoding="utf-8")
    assert "It does not gate an ordinary click" in header
    assert "coarse" in header


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
