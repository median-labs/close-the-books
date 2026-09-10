#!/usr/bin/env python3
"""PreToolUse gate for browser mode: what an agent may do inside live books.

Exit codes are the Claude Code contract: 0 allows, 2 blocks and shows stderr to
the model. **Any exception in this file exits 2.** A hook that fails open is a
gate that is off and looks exactly like a gate that passed.

WHAT THIS CATCHES, PLAINLY, INCLUDING WHAT IT DOES NOT

`import-approval-gate.py` guards a file, and a file is easy to guard: it has a
path, the path carries a batch name, and the gate reads it. A browser click has
none of that. It arrives as a coordinate or an element reference, and no
inspection of it says whether it lands on Accept or on a column header.

So this gate does four things it can do properly, and does not pretend to do
the one it cannot.

  1. It refuses the six destructive actions outright, wherever their words
     appear: a URL, a typed string, a script, a batched step. Not gated,
     refused, because there is no approval that makes disconnecting a feed safe.
  2. It refuses script-driven writes and file uploads unless a run is open,
     which means a batch is approved and its count before has been recorded.
     A script is the one browser call whose intent is fully readable, and it is
     also the shortest route around everything else here.
  3. It refuses every browser call while a halt stands. A halted run means a
     count did not add up, and the next thing to happen should be a person
     looking, not another page load.
  4. It refuses the two commands that are a person's to run: `books.py approve`
     is already refused by the other gate, and `books.py browser clear` is
     refused here. Clearing a halt is a statement that somebody understood the
     difference, and an agent that can make that statement makes it worthless.

**It does not gate an ordinary click.** It cannot. Working the queue means
clicking filters, column settings and rows, and no rule separates those from a
click that posts. What stands between an unapproved click and the books is the
library: `books.py browser post` refuses to print the step list at all until
the batch is approved, and `books.py browser verify` refuses to let a run end
if the count moved by the wrong amount. Those are the load-bearing controls.
This hook is the coarse one around them, and reading it as more than that would
be the mistake it exists to prevent.

It also stays out of the way outside a working directory. If the folder holds
no `review/` and `import/`, this is somebody browsing, not somebody closing
their books, and nothing here applies.
"""

import json
import os
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
sys.path.insert(0, str(REPO / "lib"))

GATE_FILES = ("browser-write-gate.py", "browser.py", "approval.py",
              "import-approval-gate.py")


def block(msg: str) -> None:
    sys.stderr.write("BLOCKED by the close-the-books browser gate.\n\n" + msg + "\n")
    sys.exit(2)


def allow() -> None:
    sys.exit(0)


# Browser tools, by what they can do. Naming is by suffix so that the same rules
# apply whichever browser connector is wired up.
READ_ONLY_TOOLS = ("read_page", "get_page_text", "find", "read_console_messages",
                   "read_network_requests", "tabs_context", "tabs_context_mcp",
                   "list_connected_browsers", "screenshot")
SCRIPT_TOOLS = ("javascript_tool", "execute_javascript")
UPLOAD_TOOLS = ("file_upload", "upload_image")
BROWSER_TOOLS = READ_ONLY_TOOLS + SCRIPT_TOOLS + UPLOAD_TOOLS + (
    "computer", "navigate", "form_input", "browser_batch", "resize_window",
    "tabs_create", "tabs_create_mcp", "tabs_close", "tabs_close_mcp",
    "tabs_select", "open_url", "get_page_content", "reload_tab",
    "go_back", "go_forward", "switch_to_tab", "close_tab", "list_tabs",
)

# A script that changes something, rather than one that reads something. Reading
# the row model off a virtualized grid is the documented way to read this
# interface, so a blanket ban on scripts would ban the read that works.
MUTATING_JS = re.compile(
    r"\.click\s*\(|\.submit\s*\(|dispatchEvent\s*\(\s*new\s+(?:Mouse|Pointer|Keyboard)Event"
    r"|fetch\s*\([^)]*\bmethod\s*:\s*['\"](?:POST|PUT|PATCH|DELETE)"
    r"|XMLHttpRequest[\s\S]{0,200}\bopen\s*\(\s*['\"](?:POST|PUT|PATCH|DELETE)"
    r"|\.value\s*=|\.innerHTML\s*=|localStorage\.setItem|document\.forms",
    re.I,
)

CLEAR_RE = re.compile(r"books(?:\.py)?\s+browser\s+clear|browser\s+clear\s+[A-Za-z0-9_]",
                      re.I)
RUNFILE_RE = re.compile(r"browser-runs/[^\s;|&'\"]*")
REDIRECT_RE = re.compile(r"(?:^|[^0-9>])>{1,2}\s*([^\s;|&]+)")


def workdir():
    """The working directory this session is closing books in, or None."""
    for root in (os.environ.get("CLAUDE_PROJECT_DIR"), os.getcwd()):
        if not root:
            continue
        p = Path(root)
        if (p / "review").is_dir() and (p / "import").is_dir():
            return p
    return None


def texts_in(value, out=None):
    """Every string anywhere in a tool input, however deeply nested.

    A batched browser call carries its steps as nested objects, and a rule that
    only reads the top level is a rule that reads the wrapper.
    """
    out = [] if out is None else out
    if isinstance(value, str):
        out.append(value)
    elif isinstance(value, dict):
        for v in value.values():
            texts_in(v, out)
    elif isinstance(value, (list, tuple)):
        for v in value:
            texts_in(v, out)
    return out


def check_destructive(blob: str) -> None:
    try:
        from closethebooks.browser import destructive_in, refuse
    except Exception as exc:  # library missing or broken: fail closed
        block(
            f"the browser library could not be loaded ({exc}), so no refusal "
            "could be evaluated. Refusing rather than allowing."
        )
    act = destructive_in(blob)
    if act is not None:
        block(refuse(act, context="a browser call carrying the words for it"))


def run_state(wd):
    """(has_open_run, blocking_message)."""
    try:
        from closethebooks.browser import blocking, open_runs
    except Exception as exc:
        block(
            f"the browser library could not be loaded ({exc}), so no run state "
            "could be read. Refusing rather than allowing."
        )
    halted_or_open = blocking(wd)
    live = open_runs(wd)
    return (bool(live), halted_or_open)


def halted(wd) -> str:
    try:
        from closethebooks.browser import halted_runs
    except Exception as exc:
        block(f"the browser library could not be loaded ({exc}). Refusing.")
    runs = halted_runs(wd)
    if not runs:
        return ""
    r = runs[0]
    first = (r.halt_reason or "").splitlines()[0] if r.halt_reason else ""
    return (
        f"batch-{r.tag} halted and nobody has cleared it.\n\n"
        f"  {first}\n\n"
        "A halt means a count in the books moved by an amount nobody predicted. "
        "Until somebody has found out why, nothing else happens in this browser, "
        "including reading, because the next thing that should happen is a "
        "person looking at the file.\n\n"
        f"  Read reports/browser-runs/{r.tag}.json.\n"
        f"  Then, in your own terminal:\n"
        f"      python3 bin/books.py browser clear {r.tag} --note \"what it was\""
    )


def check_browser(tool: str, ti: dict, wd) -> None:
    blob = "\n".join(texts_in(ti))
    check_destructive(blob)

    stop = halted(wd)
    if stop:
        block(stop)

    short = tool.rsplit("__", 1)[-1]
    if short in READ_ONLY_TOOLS:
        allow()

    scripted = short in SCRIPT_TOOLS and MUTATING_JS.search(blob)
    uploading = short in UPLOAD_TOOLS
    if short == "browser_batch":
        scripted = bool(MUTATING_JS.search(blob))

    if not (scripted or uploading):
        allow()

    has_open, message = run_state(wd)
    if has_open:
        allow()

    what = "a script that changes the page" if scripted else "a file upload"
    block(
        f"{what} needs an approved batch, and there is no run open.\n\n"
        "A run opens when three things are true: you approved a named batch of "
        "named rows in your own terminal, the count before was read off the "
        "screen, and no earlier batch is unfinished. Until then this is a write "
        "into live books that nobody agreed to.\n\n"
        + (f"{message}\n\n" if message else "")
        + "  python3 bin/books.py browser plan     builds the batch and writes it "
          "out to read\n"
          "  python3 bin/books.py approve batch-XX  you run this, in your own "
          "terminal\n"
          "  python3 bin/books.py browser post batch-XX --before N   opens the run"
    )


# In a shell, the only way to reach QuickBooks is to name it. So the refused
# vocabulary is checked against a command only when the command is talking to
# QuickBooks. Without this, `git branch --delete` inside a working directory
# reads as an attempt to delete a transaction, and a gate that fires on
# ordinary work is a gate people learn to route around.
QBO_IN_SHELL = re.compile(r"quickbooks|intuit|\bqbo\b", re.I)


def check_bash(cmd: str, wd) -> None:
    if CLEAR_RE.search(cmd):
        block(
            "`books.py browser clear` retires a halt.\n\n"
            "A halt is a count in somebody's books that did not add up. Clearing "
            "it is a person saying they found out why, and it takes a note "
            "recording what they found. An agent that can write that note has "
            "made the halt decorative.\n\n"
            "Tell them what the difference looks like and what you would check. "
            "They run the command."
        )
    if QBO_IN_SHELL.search(cmd):
        check_destructive(cmd)

    # A run file records that a batch is approved and how far it got. Writing
    # one by hand is the same act as writing an approval by hand.
    targets = [m.group(1).strip("'\"") for m in REDIRECT_RE.finditer(cmd)]
    targets += RUNFILE_RE.findall(cmd) if re.search(
        r"\b(?:tee|cp|mv|install|rsync|truncate|sed\s+-i)\b", cmd) else []
    for t in targets:
        if "browser-runs" in str(t):
            block(
                "writing a run file by hand is not something an agent may do.\n\n"
                "A run file says a batch was approved and records the count read "
                "before it. Both of those are things a person did. Written from "
                "here they record nothing.\n\n"
                "`books.py browser post` writes it, and only after the approval "
                "check passes."
            )


def check_path(path: str) -> None:
    if not path:
        return
    name = Path(path).name
    if name in GATE_FILES and "close-the-books" in str(path):
        block(
            f"{name} is part of the gate on what reaches live books.\n\n"
            "Editing it from inside a run removes the check on the run doing the "
            "editing. If a change is needed, a person makes it, with "
            "tests/test_browser.py and tests/test_approval.py green afterwards."
        )
    if "browser-runs" in str(path).replace("\\", "/"):
        block(
            "run files are written by `books.py browser post` and `verify`, and "
            "not by hand.\n\n"
            "A run file carries the approval it was opened under and the count "
            "read before the batch. Editing one turns a verified claim into an "
            "asserted one."
        )


def main() -> None:
    raw = sys.stdin.read()
    if not raw.strip():
        allow()
    data = json.loads(raw)
    tool = data.get("tool_name", "") or ""
    ti = data.get("tool_input", {}) or {}
    short = tool.rsplit("__", 1)[-1]

    wd = workdir()
    if wd is None:
        # Not a close-the-books working directory. Somebody is browsing.
        allow()

    if short in BROWSER_TOOLS:
        check_browser(tool, ti, wd)
        allow()

    if tool == "Bash":
        check_bash(ti.get("command", "") or "", wd)
        allow()

    if tool in ("Write", "Edit", "MultiEdit", "NotebookEdit"):
        check_path(ti.get("file_path") or ti.get("notebook_path") or "")
        for edit in ti.get("edits", []) or []:
            check_path(edit.get("file_path", ""))
        allow()

    allow()


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception as exc:  # noqa: BLE001 - deliberate catch-all
        sys.stderr.write(
            "BLOCKED by the close-the-books browser gate.\n\n"
            f"The gate could not evaluate this call ({type(exc).__name__}: {exc}).\n"
            "Refusing rather than allowing, because a gate that fails open is\n"
            "indistinguishable from a gate that passed.\n"
        )
        sys.exit(2)
