#!/usr/bin/env python3
"""PreToolUse gate: nothing lands in import/ that a person has not approved.

Wired for Write, Edit, MultiEdit and Bash. Bash is covered on purpose, because a
hook that only watches the file tools is defeated by `cat > import/je.csv`.

Exit codes are the Claude Code contract: 0 allows, 2 blocks and shows stderr to
the model. **Any exception in this file exits 2.** A hook that fails open is a
gate that is off and looks exactly like a gate that passed.

What it refuses, and why each one matters:

  1. A write into import/ with no live approval. The core rule.
  2. A write into import/ whose review workbook changed after approval. A stale
     approval covers decisions that are no longer the ones in the file.
  3. Any write to a .APPROVED file. Approval is a human act; an agent that can
     write the approval owns the whole system.
  4. Any shell command running `books.py approve`. Same reason as 3, by the
     other route.
  5. Any edit to this hook or to approval.py. An agent that can edit the gate
     has no gate. A human editing them directly is unaffected.
"""

import json
import os
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
sys.path.insert(0, str(REPO / "lib"))


def block(msg: str) -> None:
    sys.stderr.write("BLOCKED by the close-the-books approval gate.\n\n" + msg + "\n")
    sys.exit(2)


def allow() -> None:
    sys.exit(0)


# Paths whose modification would disable the gate itself.
GATE_FILES = ("import-approval-gate.py", "approval.py")

# A shell command that creates or truncates a file at a path, in the forms that
# actually occur: redirects, tee, cp, mv, and the usual writers.
REDIRECT_RE = re.compile(r"(?:^|[^0-9>])>{1,2}\s*([^\s;|&]+)")
WRITER_RE = re.compile(
    r"\b(?:tee|cp|mv|install|rsync|dd\s+of=|truncate|sed\s+-i|python3?\s|touch)\b"
)
APPROVE_RE = re.compile(r"books(?:\.py)?\s+approve|approve\s+batch-", re.I)
APPROVED_FILE_RE = re.compile(r"\.APPROVED\b")


def paths_from_command(cmd: str):
    """Every path the command looks like it might write to."""
    out = []
    for m in REDIRECT_RE.finditer(cmd):
        out.append(m.group(1).strip("'\""))
    if WRITER_RE.search(cmd):
        for tok in re.findall(r"[^\s;|&'\"]+", cmd):
            if "import/" in tok or tok.endswith(".APPROVED"):
                out.append(tok)
    return out


def targets_import_dir(path: str) -> bool:
    p = str(path).replace("\\", "/")
    return "/import/" in p or p.startswith("import/") or "/import/" in p + "/"


def check_path(path: str) -> None:
    if not path:
        return
    name = Path(path).name

    if APPROVED_FILE_RE.search(str(path)):
        block(
            "Writing an approval file is not something an agent may do.\n\n"
            "An approval records that a person read the proposed decisions and\n"
            "took responsibility for them. If it could be written from here, it\n"
            "would record nothing.\n\n"
            "Ask the person you are working with to run, in their own terminal:\n"
            "    python3 bin/books.py approve batch-<tag>"
        )

    if name in GATE_FILES and "close-the-books" in str(path):
        block(
            f"{name} is the approval gate itself.\n\n"
            "Editing it from inside a run would remove the only check on what\n"
            "reaches the import files. If a change is genuinely needed, a person\n"
            "should make it directly, with the tests in tests/test_approval.py\n"
            "green afterwards."
        )

    if not targets_import_dir(path):
        return

    try:
        from closethebooks.approval import guard
    except Exception as exc:  # library missing or broken: fail closed
        block(
            f"the approval library could not be loaded ({exc}), so no approval\n"
            "could be verified. Refusing the write."
        )

    ok, msg = guard(path)
    if not ok:
        block(
            f"{name} is an import file, and import files are what get uploaded\n"
            "into QuickBooks Online.\n\n" + msg
        )


def main() -> None:
    raw = sys.stdin.read()
    if not raw.strip():
        allow()
    data = json.loads(raw)

    tool = data.get("tool_name", "")
    ti = data.get("tool_input", {}) or {}

    if tool in ("Write", "Edit", "MultiEdit", "NotebookEdit"):
        check_path(ti.get("file_path") or ti.get("notebook_path") or "")
        for edit in ti.get("edits", []) or []:
            check_path(edit.get("file_path", ""))
        allow()

    if tool == "Bash":
        cmd = ti.get("command", "") or ""
        if APPROVE_RE.search(cmd):
            block(
                "`books.py approve` records a person's approval of a batch.\n\n"
                "Running it from here would mean the system approved itself. The\n"
                "person reviewing the batch runs it in their own terminal, after\n"
                "they have filled in the founder_decision column."
            )
        for p in paths_from_command(cmd):
            check_path(p)
        allow()

    allow()


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception as exc:  # noqa: BLE001 - deliberate catch-all
        # Fail closed. See the module docstring.
        sys.stderr.write(
            "BLOCKED by the close-the-books approval gate.\n\n"
            f"The gate could not evaluate this call ({type(exc).__name__}: {exc}).\n"
            "Refusing rather than allowing, because a gate that fails open is\n"
            "indistinguishable from a gate that passed.\n"
        )
        sys.exit(2)
