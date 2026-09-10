"""The approval gate is the safety-critical part of this repo.

If these tests fail, an agent can put a file into import/ that no person agreed
to, and a founder may upload it to their books. Treat a failure here as a stop.
"""

import json
import pathlib
import subprocess
import sys
import tempfile

REPO = pathlib.Path(__file__).resolve().parent.parent
HOOK = REPO / "hooks" / "import-approval-gate.py"
sys.path.insert(0, str(REPO / "lib"))

from closethebooks.approval import (  # noqa: E402
    ApprovalError, batch_tag, check, guard, read_approval, write_approval,
)


def workdir():
    wd = pathlib.Path(tempfile.mkdtemp())
    (wd / "review").mkdir()
    (wd / "import").mkdir()
    (wd / "review" / "batch-01.xlsx").write_bytes(b"workbook v1")
    return wd


def hook(payload):
    p = subprocess.run(
        [sys.executable, str(HOOK)],
        input=json.dumps(payload) if isinstance(payload, dict) else payload,
        capture_output=True, text=True,
    )
    return p.returncode, (p.stderr or "").strip()


# ------------------------------------------------------------ library level

def test_batch_tag_parsing():
    assert batch_tag("rules-batch-01.xlsx") == "01"
    assert batch_tag("je-payroll-batch-2025q1.csv") == "2025q1"
    assert batch_tag("je-payroll.csv") is None


def test_unapproved_batch_is_refused():
    wd = workdir()
    ok, msg = guard(wd / "import" / "rules-batch-01.xlsx")
    assert not ok and "has not been approved" in msg


def test_approval_then_allowed():
    wd = workdir()
    write_approval(wd / "review", "01", "tester", rows=5)
    ok, _ = guard(wd / "import" / "rules-batch-01.xlsx")
    assert ok
    assert read_approval(wd / "review", "01").approved_by == "tester"


def test_edit_after_approval_invalidates_it():
    """The failure this prevents: approve a batch, quietly change a row, import."""
    wd = workdir()
    write_approval(wd / "review", "01", "tester")
    (wd / "review" / "batch-01.xlsx").write_bytes(b"workbook v2 EDITED")
    ok, msg = guard(wd / "import" / "rules-batch-01.xlsx")
    assert not ok and "stale" in msg


def test_untagged_import_file_is_refused():
    wd = workdir()
    ok, msg = guard(wd / "import" / "whatever.csv")
    assert not ok and "no batch tag" in msg


def test_approving_a_batch_with_no_workbook_raises():
    wd = workdir()
    try:
        write_approval(wd / "review", "99", "tester")
        raise AssertionError("should have raised")
    except ApprovalError as exc:
        assert "Nothing to approve" in str(exc)


# --------------------------------------------------------------- hook level

def test_hook_blocks_unapproved_write():
    wd = workdir()
    rc, _ = hook({"tool_name": "Write", "tool_input": {"file_path": str(wd / "import" / "rules-batch-01.xlsx")}})
    assert rc == 2


def test_hook_blocks_agent_writing_its_own_approval():
    wd = workdir()
    rc, out = hook({"tool_name": "Write", "tool_input": {"file_path": str(wd / "review" / "batch-01.APPROVED")}})
    assert rc == 2 and "not something an agent may do" in out


def test_hook_blocks_the_approve_command():
    rc, _ = hook({"tool_name": "Bash", "tool_input": {"command": "python3 bin/books.py approve batch-01"}})
    assert rc == 2


def test_hook_blocks_shell_redirect_into_import():
    """A hook watching only Write is defeated by `cat > import/je.csv`."""
    wd = workdir()
    rc, _ = hook({"tool_name": "Bash", "tool_input": {"command": f"cat > {wd}/import/je-batch-01.csv"}})
    assert rc == 2


def test_hook_blocks_editing_the_gate_itself():
    rc, out = hook({"tool_name": "Edit", "tool_input": {"file_path": "/x/close-the-books/lib/closethebooks/approval.py"}})
    assert rc == 2 and "approval gate itself" in out


def test_hook_allows_ordinary_work():
    wd = workdir()
    assert hook({"tool_name": "Write", "tool_input": {"file_path": str(wd / "reports" / "summary.md")}})[0] == 0
    assert hook({"tool_name": "Read", "tool_input": {"file_path": str(wd / "import" / "x.csv")}})[0] == 0
    assert hook({"tool_name": "Bash", "tool_input": {"command": "ls -la"}})[0] == 0


def test_hook_allows_an_approved_write():
    wd = workdir()
    write_approval(wd / "review", "01", "tester")
    rc, _ = hook({"tool_name": "Write", "tool_input": {"file_path": str(wd / "import" / "rules-batch-01.xlsx")}})
    assert rc == 0


def test_hook_does_not_block_things_that_merely_say_import():
    """A gate that cries wolf gets switched off.

    The word "import" appears in ordinary filenames, in directory names, and in
    every second line of Python. Blocking those would make the hook unusable
    inside its own repository, and a hook people disable protects nothing.
    """
    allowed = [
        {"tool_name": "Write", "tool_input": {"file_path": "/home/u/important-notes.txt"}},
        {"tool_name": "Write", "tool_input": {"file_path": "/home/u/my-imports/data.csv"}},
        {"tool_name": "Write", "tool_input": {"file_path": "/home/u/reports/imported-summary.md"}},
        {"tool_name": "Bash", "tool_input": {"command": "grep import lib/*.py"}},
        {"tool_name": "Bash", "tool_input": {"command": "python3 -c 'import os'"}},
    ]
    for payload in allowed:
        rc, out = hook(payload)
        assert rc == 0, f"wrongly blocked: {payload} -> {out[:120]}"


def test_hook_blocks_the_real_import_directory_in_both_forms():
    for path in ("/home/u/books/import/je-batch-01.csv", "import/rules-batch-01.xlsx"):
        rc, _ = hook({"tool_name": "Write", "tool_input": {"file_path": path}})
        assert rc == 2, f"failed to block {path}"


def test_hook_fails_closed_on_malformed_input():
    """A gate that errors open is a gate that is off and looks like a pass."""
    rc, out = hook("{ not json")
    assert rc == 2 and "could not evaluate" in out


def main():
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failed = 0
    for fn in fns:
        try:
            fn()
            print(f"PASS {fn.__name__}")
        except Exception as exc:  # noqa: BLE001
            failed += 1
            print(f"FAIL {fn.__name__}: {type(exc).__name__}: {exc}")
    print(f"\n{len(fns) - failed}/{len(fns)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
