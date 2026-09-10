"""Browser mode: your own agent works inside QuickBooks Online, with you watching.

Every other module in this kit reads a file and writes a file. This one is the
plan and the guard for something different: an agent driving the QuickBooks web
interface in a browser you are already signed in to, categorizing rows in For
Review, adding a transaction the feed never received, and typing a journal
entry, after you have approved that exact batch.

THIS MODULE STILL MAKES NO NETWORK CALLS

It holds no browser driver and it opens no connection. The agent does the
clicking with its own browser tools, and this module decides what it is allowed
to attempt, what shape the answer has to have, and whether the result was
exactly what you approved. The split is deliberate. A gate that lives in the
same process as the thing it drives can be routed around by the thing it drives.
Here the gate is a separate program the agent has to satisfy in between, and the
approval it checks is a file only you can write.

THE FIVE RULES, IN THE ORDER THEY FIRE

1. You sign in. No credential ever reaches the agent. It cannot ask for one, it
   cannot store one, and it cannot type one. If a QuickBooks page asks for a
   password, that is your cue that the session ended, and the run stops there.

2. The company is confirmed before anything is read. QuickBooks keeps several
   company files behind one login and switches between them from a menu. A run
   that reads the wrong file produces work that is correct about a company
   nobody asked about. `confirm_company` compares what the header actually says
   against the company in your profile, and every later step re-checks it.

3. Nothing is written without your approval of that specific batch. The
   mechanism is the one that already gates import files: a batch is named, its
   rows are written out where you can read them, you run `approve` in your own
   terminal, and editing the batch afterwards voids the approval.

4. Every write is verified against a count read before and after. Approved 25
   rows, the queue fell by 25, the run continues. The queue fell by 26 and the
   run halts, because the extra one is a rule that fired underneath, a stale
   page, or a mis-click, and none of those get to reach the next batch.

5. Six actions are refused outright rather than gated. Disconnecting a feed,
   merging accounts, excluding transactions, deleting, voiding, and undoing a
   reconciliation. Where one of those is the right fix, this module writes the
   ordered runbook for you to work by hand, and says what each step destroys.

WHY 25 ROWS

Because that is about what a person actually reads. A 600 row approval is an
approval of a scroll bar, and the signature on it means whatever the last screen
happened to say. Twenty five rows is roughly one screen, two minutes of
attention, and a number you can hold in your head while you look at the queue
next to it. The cost is that a 300 row queue takes twelve approvals, which is
the honest price of writing into books that are live.
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import json
import re
from dataclasses import dataclass, field, asdict
from pathlib import Path

from .util import iso, money, norm_text, plain

# --------------------------------------------------------------- exceptions


class BrowserError(Exception):
    """Base for everything this module stops on."""


class CompanyMismatch(BrowserError):
    """The company on screen is not the company this working directory is for."""


class ShapeError(BrowserError):
    """A read came back in a shape that does not prove what it claims to prove."""


class CountMismatch(BrowserError):
    """The count after a batch is not the count the approval predicted."""


class RunError(BrowserError):
    """A run was asked to do something its state does not allow."""


# ------------------------------------------------------------- the surfaces

@dataclass(frozen=True)
class Surface:
    """One QuickBooks screen, what it answers, and how it misleads.

    Every trap here was paid for on a real file. They are in the module rather
    than in a skill because an agent that skips the skill still calls the code.
    """
    name: str
    path: str
    answers: str
    traps: tuple = ()
    fresh_tab: bool = False


SURFACES = {
    "banking": Surface(
        name="banking",
        path="/app/banking",
        answers="the account tiles: the QuickBooks balance, the For Review count, "
                "and the line under each balance saying when the feed last updated",
        traps=(
            "The count on the tab is the default view, and the default view "
            "undercounts. Split the type filter, read each part, and add the parts.",
            "A tile showing a balance says nothing about whether the feed is live. "
            "Read the updated line, and read the bank's own site for the real balance.",
        ),
    ),
    "for-review": Surface(
        name="for-review",
        path="/app/banking",
        answers="the For Review queue for one account, row by row",
        traps=(
            "The grid is virtualized. A naive read returns about 34 rows whatever "
            "the count says, because only the visible window is in the document. "
            "Scroll with a real wheel event, or read the whole row model off the "
            "table instance, and check the number you got against the number the "
            "header claims.",
            "The row checkbox is a phantom and only becomes clickable on hover. So "
            "is a row's Post link.",
            "The Class column is off by default in the new grid. Turn it on under "
            "the gear, then Columns. Posting without it writes 'Not specified', "
            "which is a defect rather than a state.",
            "Bulk selection does not hold across a re-render. Post row by row.",
            "On a card payment row, never touch the Match toggle.",
        ),
    ),
    "register": Surface(
        name="register",
        path="/app/register",
        answers="every transaction in one account, with a running balance",
        traps=(
            "This is where a write gets verified, and the report builder is not. "
            "The register is plain text with a running balance and it does not lie "
            "about how many rows it holds.",
            "It paginates at 300 rows and defaults to date ascending.",
        ),
    ),
    "journal": Surface(
        name="journal",
        path="/app/journal",
        answers="the journal entry form, which is the only way an adjusting entry "
                "gets into a United States QuickBooks Online file without typing",
        traps=(
            "It only mounts in a fresh tab. In a tab that has already loaded the "
            "report builder it fails silently, with about 125 characters of body "
            "text, no console error and no failed request.",
            "Chain entries with Save and new rather than navigating back.",
            "Escape closes the whole panel and discards what is in it.",
        ),
        fresh_tab=True,
    ),
    "reconcile-summary": Surface(
        name="reconcile-summary",
        path="/app/reconcile?ReconcileSummary=true",
        answers="reconciled-through for every account in one view",
        traps=(
            "An item dated inside a reconciled month is a re-download. Booking it "
            "counts it twice.",
        ),
    ),
    "rules": Surface(
        name="rules",
        path="/app/rules",
        answers="every bank rule, and which of them post without being seen",
        traps=(
            "A rule never applies to transactions already sitting in For Review. "
            "It applies to the next ones to arrive, which is why turning auto-add "
            "off does not undo what it already posted.",
        ),
    ),
    "audit-log": Surface(
        name="audit-log",
        path="/app/auditlog",
        answers="who last worked in the file, and what a rule or a sync posted "
                "with nobody watching",
        traps=(
            "The audit history is the only place that says whose entry something "
            "is. A date does not say it, and neither does a memo.",
        ),
    ),
}

# Report tokens that work, and the ones that hang forever rather than failing.
# A wrong token does not 404, it spins, and an agent waiting on it looks like an
# agent doing careful work.
REPORT_TOKENS_GOOD = ("PANDL", "BAL_SHEET", "ACCTL_QUICKREPORT", "TRIAL_BAL",
                      "GEN_LEDGER")
REPORT_TOKENS_HANG = ("PROFITANDLOSS", "P_AND_L", "GENERAL_LEDGER")

# Report dates are set in the interface. The query string is ignored, and the
# report renders empty with no sign that the filter never applied.
REPORT_DATES_ARE_UI_ONLY = True

# What a naive read of a virtualized grid returns, whatever the real row count
# is. Measured, not guessed. Used to tell a short read from a small queue.
VIRTUAL_WINDOW = 34

# The rows in one approvable batch. See the module docstring for why.
DEFAULT_BATCH_ROWS = 25
MAX_BATCH_ROWS = 60

KINDS = ("categorize", "add", "journal")


def surface(name) -> Surface:
    try:
        return SURFACES[str(name)]
    except KeyError:
        raise BrowserError(
            f"{name!r} is not a screen this mode knows. Known: "
            + ", ".join(sorted(SURFACES)) + "."
        ) from None


# ---------------------------------------------------- the refused six

@dataclass(frozen=True)
class RefusedAction:
    """An action that destroys records, and the by-hand order that does not."""
    key: str
    label: str
    destroys: str
    steps: tuple
    watch_for: str = ""


REFUSED = {
    "disconnect": RefusedAction(
        key="disconnect",
        label="disconnecting a bank or card feed",
        destroys=(
            "every unreviewed transaction in that account's Pending and For "
            "Review tabs. On an account whose activity was never booked, that "
            "queue is the only record of a year of card use anywhere in the file, "
            "and nothing warns you before it goes."
        ),
        steps=(
            "Export the For Review and Pending tabs for that account to Excel, "
            "both of them, and keep the files. This is the copy that survives.",
            "Book everything in those tabs, or move it out deliberately, until "
            "each tab reads zero. `books.py completeness` is what says the account "
            "reaches its bank balance once you have.",
            "Deal with any opening balance the account was created with, and say "
            "in words where that balance goes. Archive is not a disposition.",
            "Only now, disconnect the feed.",
            "Do whatever needed the disconnection, such as a merge.",
        ),
        watch_for="A count of zero in For Review that came from a filter rather "
                  "than from work. Clear the filters and read it again.",
    ),
    "merge": RefusedAction(
        key="merge",
        label="merging two accounts",
        destroys=(
            "nothing by itself, but QuickBooks refuses to merge while either "
            "account is connected, so its own documented fix begins with a "
            "disconnection, and that is the step that destroys the queue. A merge "
            "is also not reversible."
        ),
        steps=(
            "Run `books.py merge-plan`. It refuses the wrong order and it names "
            "which of the two accounts holds the history.",
            "Work both queues to zero first, following the disconnect runbook.",
            "Decide which name survives, because the other one is gone afterwards.",
            "Disconnect the feed on the account being merged away.",
            "Merge, then read the register of the surviving account and check the "
            "row count against the two you started with.",
        ),
    ),
    "exclude": RefusedAction(
        key="exclude",
        label="excluding transactions from For Review",
        destroys=(
            "the row, as far as every later view is concerned. Excluded rows leave "
            "the queue, are not in the books, and sit on a tab that paginates, so "
            "a partial read of it produces a reconciliation that cannot close. "
            "Reading 217 of 651 excluded rows once sent six consecutive "
            "hypotheses down the wrong path over two days."
        ),
        steps=(
            "Decide in writing why the row is not a transaction of the company.",
            "If it is a duplicate of something already booked, mark it as a match "
            "instead, which keeps the link.",
            "If it genuinely does not belong, exclude it yourself, one row at a "
            "time, and record the reason against each one.",
            "Read the Excluded tab in full afterwards, all pages, and note the "
            "count. Any later reconciliation has to account for it.",
        ),
    ),
    "delete": RefusedAction(
        key="delete",
        label="deleting a transaction, an account or a rule",
        destroys=(
            "the record and its place in the audit history. A deleted transaction "
            "leaves an entry in the audit log and nothing in the books, which "
            "means the only way to know what it was is to have looked first."
        ),
        steps=(
            "Open the transaction and screenshot it, or export the register page "
            "holding it, before anything else.",
            "Establish whether the period is closed and whether the year has been "
            "filed. Changing a filed year is a different decision with a different "
            "cost, and it is yours to make.",
            "If it is a duplicate, delete the later one and keep the one carrying "
            "the reconciliation mark.",
            "Delete it yourself, then read the account balance before and after "
            "and check the movement is the amount you expected.",
        ),
    ),
    "void": RefusedAction(
        key="void",
        label="voiding a transaction",
        destroys=(
            "the amount while keeping the record, which is usually the right "
            "answer and is still a change to a period that may be closed and may "
            "have been filed."
        ),
        steps=(
            "Check the date against the last filed year and against the last "
            "reconciled month.",
            "Where the transaction is inside a filed year, void it in the current "
            "period with an entry instead, so the filed figures stay as filed.",
            "Void it yourself, and note the reason in the memo, because a voided "
            "transaction with a blank memo is a question somebody asks later.",
        ),
    ),
    "undo-reconciliation": RefusedAction(
        key="undo-reconciliation",
        label="undoing a reconciliation",
        destroys=(
            "the reconciliation marks for that period and often for every period "
            "after it. Rebuilding them means reconciling each month again against "
            "statements you may no longer have."
        ),
        steps=(
            "Read the reconciliation report for the period first and save it. It "
            "is the only record of what was ticked.",
            "Work out whether the difference can be fixed with an entry in an open "
            "period instead, which is almost always true.",
            "If it genuinely has to be undone, undo one month, fix the cause, and "
            "reconcile that month again before touching the next one.",
        ),
    ),
}

# What the words look like in a URL, a button label, a menu item or a script.
# Matched against anything an agent is about to click, type or evaluate.
_REFUSAL_PATTERNS = (
    ("disconnect", r"\bdisconnect(?:ing|ed)?\b|\bunlink(?:ing)?\s+(?:the\s+)?(?:bank|account|feed)\b"),
    ("merge", r"\bmerge\s+(?:the\s+)?(?:account|accounts|two)\b|\bmergeaccount\b"),
    ("exclude", r"\bexclude(?:d|s|ing)?\s+(?:the\s+)?(?:transaction|row|rows|selected)\b|\bbatchexclude\b"),
    ("delete", r"\bdelete(?:d|s|ing)?\b|\bdeletetxn\b|\bremove\s+(?:this\s+)?(?:transaction|account|rule)\b"),
    ("void", r"\bvoid(?:ed|s|ing)?\s+(?:this\s+)?(?:transaction|check|invoice|entry)?\b"),
    ("undo-reconciliation", r"\bundo\s+(?:the\s+)?(?:last\s+)?reconcil\w*|\bunreconcile\w*"),
)


def destructive_in(text) -> RefusedAction | None:
    """The refused action a piece of text is asking for, or None.

    Deliberately blunt. It reads a URL, a button label, a typed string or a
    script the same way, because the cost of stopping a run that was not going
    to destroy anything is a sentence, and the cost of the other mistake is a
    year of card activity.
    """
    t = str(text or "").lower()
    if not t.strip():
        return None
    for key, pattern in _REFUSAL_PATTERNS:
        if re.search(pattern, t):
            return REFUSED[key]
    return None


def refuse(action, *, context="") -> str:
    """The message a refusal prints. Names what it saw and what to do instead."""
    if isinstance(action, str):
        act = REFUSED.get(action) or destructive_in(action)
        if act is None:
            raise BrowserError(f"{action!r} is not one of the refused actions.")
    else:
        act = action
    lines = [
        f"Refused: {act.label}.",
        "",
        "This is not a step that gets approved. It destroys " + act.destroys,
        "",
        "Where it is genuinely the right fix, you do it by hand, in this order:",
    ]
    for i, step in enumerate(act.steps, start=1):
        lines.append(f"  {i}. {step}")
    if act.watch_for:
        lines += ["", "Watch for: " + act.watch_for]
    if context:
        lines += ["", f"What was about to happen: {context}"]
    lines += [
        "",
        "`books.py browser runbook " + act.key + "` writes this out as a file you "
        "can work from.",
    ]
    return "\n".join(lines)


def runbook_markdown(key, *, company="", account="") -> str:
    """The same refusal as a document, for the person doing it by hand."""
    act = REFUSED.get(str(key))
    if act is None:
        raise BrowserError(
            f"{key!r} is not one of the refused actions. Known: "
            + ", ".join(sorted(REFUSED)) + "."
        )
    head = [f"# By hand: {act.label}", ""]
    if company:
        head.append(f"**Company:** {company}  ")
    if account:
        head.append(f"**Account:** {account}  ")
    head += [
        f"**Written:** {_dt.date.today().isoformat()}",
        "",
        "Your agent will not do this and there is no flag that changes that.",
        "You do it, because the cost of getting the order wrong is not "
        "recoverable and the decision is yours.",
        "",
        "## What it destroys",
        "",
        act.destroys[0].upper() + act.destroys[1:],
        "",
        "## The order",
        "",
    ]
    for i, step in enumerate(act.steps, start=1):
        head.append(f"{i}. {step}")
    if act.watch_for:
        head += ["", "## Watch for", "", act.watch_for]
    head += [
        "",
        "## When you are done",
        "",
        "Read the account back and say the number you saw. "
        "`books.py browser read` records it with the date, and the checks that "
        "follow use it rather than assuming the step worked.",
        "",
    ]
    return "\n".join(head)


# ------------------------------------------------------ confirming the file


# Legal suffixes, and what each one is called once the punctuation is gone. The
# suffix is KEPT rather than dropped, because a group commonly holds an Inc and
# an LLC with the same base name, and they are different companies with
# different books. Dropping it would make those two compare equal, which is the
# one comparison that must never pass.
_SUFFIXES = {
    "inc": "inc", "incorporated": "inc",
    "llc": "llc", "lc": "llc",
    "ltd": "ltd", "limited": "ltd",
    "corp": "corp", "corporation": "corp",
    "co": "co", "company": "co",
    "plc": "plc", "lp": "lp", "llp": "llp", "pc": "pc", "pllc": "pllc",
    "gmbh": "gmbh", "bv": "bv", "sa": "sa", "ag": "ag", "pty": "pty",
}


def _split_company(name):
    """(base, suffix) for a company name, with punctuation and case removed.

    'Acme Robotics, Inc.' and 'Acme Robotics Inc' both give ('acme robotics',
    'inc'), which is the difference between a trading name on a screen and a
    legal name in a profile. 'Acme Robotics LLC' gives a different suffix and
    does not match, which is the point.
    """
    t = norm_text(str(name or "")).lower()
    t = re.sub(r"[^a-z0-9]+", " ", t)
    words = t.split()
    suffix = ""
    while words and words[-1] in _SUFFIXES:
        suffix = _SUFFIXES[words[-1]] + (" " + suffix if suffix else "")
        words.pop()
    return " ".join(words), suffix.strip()


def _company_key(name) -> str:
    base, suffix = _split_company(name)
    return f"{base}|{suffix}"


@dataclass
class Company:
    """The file a run is pointed at, as read off the screen and as confirmed."""
    observed: str
    expected: str
    confirmed_at: str = ""
    confirmed_by: str = ""
    file_id: str = ""

    def to_json(self):
        return asdict(self)


def confirm_company(observed, expected, *, by="", file_id="") -> Company:
    """Raise unless the company on screen is the company this run is for.

    Called before the first read and again before every write. QuickBooks holds
    several companies behind one login and the switcher is two clicks away, so
    the wrong file is not an exotic failure. It is a Tuesday.
    """
    seen = str(observed or "").strip()
    want = str(expected or "").strip()
    if not seen:
        raise CompanyMismatch(
            "nothing was read for the company name.\n"
            "  Read it off the QuickBooks header, where the company name sits at "
            "the top of the page, and pass what it actually says.\n"
            "  A blank here means the page did not finish loading, the session "
            "ended, or the read went to the wrong tab. None of those are a "
            "company file to work in."
        )
    if not want:
        raise CompanyMismatch(
            "the profile does not name a company, so there is nothing to check "
            f"{seen!r} against.\n"
            "  Run `books.py learn` first, or set entity.name in the profile."
        )
    if _company_key(seen) != _company_key(want):
        seen_base, seen_suffix = _split_company(seen)
        want_base, want_suffix = _split_company(want)
        detail = (
            "  Everything stops here. QuickBooks keeps several companies behind "
            "one login and switches between them from a menu, so a run that "
            "reads the wrong one produces work that is correct about a company "
            "nobody asked about.\n"
        )
        if seen_base == want_base:
            detail = (
                f"  The names differ only in the legal suffix: "
                f"{seen_suffix or 'none'} on screen against "
                f"{want_suffix or 'none'} in the profile.\n"
                "  That is not treated as the same company, because a group "
                "commonly holds an Inc and an LLC with the same base name, and "
                "they have separate books and separate returns. Where the two "
                "really are one company, fix the profile so it says what the "
                "header says.\n"
            )
        raise CompanyMismatch(
            f"the company on screen is {seen!r} and this working directory is for "
            f"{want!r}.\n" + detail +
            "  Switch companies in QuickBooks using the gear menu, check the "
            "header says the right name, and confirm again."
        )
    return Company(observed=seen, expected=want, file_id=str(file_id or "").strip(),
                   confirmed_at=_now(), confirmed_by=str(by or "").strip())


def _now() -> str:
    return _dt.datetime.now().astimezone().isoformat(timespec="seconds")


# ------------------------------------------------------------ reading a page


@dataclass
class QueuePart:
    """One slice of a For Review queue, read under one filter."""
    filter: str
    rows: int


@dataclass
class QueueRead:
    """What one account's For Review queue actually holds.

    `displayed` is the number on the tab, which undercounts. `total` is the sum
    of the parts, which is the number that is true. On one real file the tab
    said 401 and the parts summed to 600, and the 199 rows in the difference
    were a quarter of the work.
    """
    account: str
    displayed: int
    parts: list = field(default_factory=list)
    rows_read: int = 0
    read_at: str = ""
    rows: list = field(default_factory=list)

    @property
    def total(self) -> int:
        return sum(int(p.rows) for p in self.parts)

    @property
    def undercount(self) -> int:
        return max(0, self.total - int(self.displayed))


def check_page(payload) -> None:
    """Stop on a page that told you something was wrong before you read it.

    QuickBooks reports a failed load, an expired session and a permission
    problem as a banner on an otherwise normal looking page, and the numbers
    underneath a banner are whatever was there before. A read taken across one
    is a read of a stale page, and it will pass every other check here.

    So a read carries what the page said, and anything in `error` or `banner`
    stops the run. Two banners get their own sentence because they mean
    something specific.
    """
    if not isinstance(payload, dict):
        return
    said = " ".join(str(payload.get(k) or "") for k in ("error", "banner")).strip()
    if not said:
        return
    low = said.lower()
    extra = ""
    if "sign in" in low or "session" in low or "log in" in low:
        extra = (
            "\n  That reads like the session ending. The owner signs in again in "
            "their own window. Nothing here asks for a password, offers to store "
            "one, or types one, and that does not change because a run is "
            "half finished."
        )
    elif "permission" in low or "access" in low:
        extra = (
            "\n  That reads like the signed-in user not having rights to this "
            "screen. Whatever is read next would be about what they can see "
            "rather than about the company."
        )
    raise ShapeError(
        f"the page carried a message: {said!r}\n"
        "  Nothing is recorded from a page that said something was wrong. The "
        "figures under a banner are usually the ones that were there before it "
        "appeared, which is what makes this the quiet failure rather than the "
        "loud one." + extra
    )


def read_for_review(payload) -> QueueRead:
    check_page(payload)
    """Validate a read of one For Review queue, or say why it proves nothing.

    Three ways a read of this grid is wrong, and each gets its own sentence:
    the default view undercounts, the grid is virtualized so a naive read stops
    at about 34 rows, and a filter left on from a previous step silently narrows
    everything after it.
    """
    if not isinstance(payload, dict):
        raise ShapeError(
            "a For Review read has to be an object carrying the account, the "
            f"count the tab displayed, and the parts it was split into. Got "
            f"{type(payload).__name__}."
        )
    account = str(payload.get("account") or "").strip()
    if not account:
        raise ShapeError(
            "the read does not say which account it is for, so it cannot be "
            "recorded against one. Every queue belongs to exactly one account."
        )
    if "displayed" not in payload:
        raise ShapeError(
            f"the read for {account} does not carry `displayed`, the count "
            "QuickBooks shows on the tab.\n"
            "  It is needed precisely because it is wrong: the difference "
            "between it and the parts is the undercount, and that difference is "
            "work nobody has scheduled."
        )
    raw_parts = payload.get("parts") or []
    if not raw_parts:
        raise ShapeError(
            f"the read for {account} carries no parts.\n"
            "  The default view undercounts. Split the type filter, read each "
            "part on its own, and pass them all, for example "
            '[{"filter": "Received", "rows": 220}, {"filter": "Spent", "rows": 380}].\n'
            "  A single unsplit number is the number that was wrong on the tab."
        )
    parts = []
    seen = set()
    for p in raw_parts:
        if not isinstance(p, dict) or "filter" not in p or "rows" not in p:
            raise ShapeError(
                f"a part of the {account} read is not shaped as a filter and a "
                f"row count: {p!r}."
            )
        name = str(p["filter"]).strip()
        if not name:
            raise ShapeError(f"a part of the {account} read has no filter name.")
        if name.lower() in seen:
            raise ShapeError(
                f"the {account} read counts the filter {name!r} twice. Two reads "
                "of the same slice add up to a total that is not there."
            )
        seen.add(name.lower())
        parts.append(QueuePart(filter=name, rows=_count(p["rows"], f"{account} {name}")))

    # The rows themselves, where the read carried them. Reading the queue off
    # the screen and then asking the owner to export the same queue by hand is
    # asking twice for one thing, so a read that brings the rows is allowed to
    # stand in for the export. They are written to a file either way, because a
    # number nobody can check afterwards is the thing this mode is replacing.
    rows = _queue_rows(payload.get("rows"), account)
    q = QueueRead(
        account=account,
        displayed=_count(payload["displayed"], f"{account} displayed"),
        parts=parts,
        rows_read=_count(payload.get("rows_read", len(rows)),
                         f"{account} rows_read"),
        read_at=str(payload.get("read_at") or _dt.date.today().isoformat()),
        rows=rows,
    )

    if q.rows_read:
        if q.rows_read != q.total:
            hint = ""
            if _looks_virtualized(q.rows_read, q.total):
                hint = (
                    f"\n  {q.rows_read} rows is about the size of the visible "
                    f"window in this grid, which is virtualized: only what is on "
                    f"screen is in the document. Setting scrollTop reports the "
                    f"new value back and re-renders nothing. Scroll with a real "
                    f"wheel event, or read the whole row model off the table "
                    f"instance, then read the count again."
                )
            raise ShapeError(
                f"the {account} read returned {q.rows_read:,} rows and the filters "
                f"say there are {q.total:,}." + hint +
                "\n  A short read is indistinguishable from a short queue, so "
                "nothing downstream can use this."
            )
    return q


QUEUE_ROW_FIELDS = ("date", "description", "amount")


def _queue_rows(raw, account) -> list:
    """The queue rows a read carried, checked one field at a time.

    Three fields and no more, because that is what a For Review export holds and
    what every parser in this kit already reads. A row missing one of them is
    refused rather than filled in: a blank date lands the row in no month, and a
    blank amount is not a transaction.
    """
    if raw in (None, ""):
        return []
    if not isinstance(raw, list):
        raise ShapeError(
            f"the rows in the {account} read are a {type(raw).__name__} rather "
            "than a list of rows."
        )
    out = []
    for i, r in enumerate(raw, start=1):
        if not isinstance(r, dict):
            raise ShapeError(f"row {i} of the {account} read is not an object: {r!r}")
        missing = [f for f in QUEUE_ROW_FIELDS if str(r.get(f) or "").strip() == ""]
        if missing:
            raise ShapeError(
                f"row {i} of the {account} read has no "
                + " and no ".join(missing) + ".\n"
                "  A For Review row is a date, a description and an amount. A row "
                "missing one of those cannot be scoped to a year, matched against "
                "the books, or checked afterwards, so it is not carried forward."
            )
        out.append({f: str(r[f]).strip() for f in QUEUE_ROW_FIELDS})
    return out


def _looks_virtualized(rows_read, total) -> bool:
    """Is this the visible window of a virtualized grid rather than the queue?"""
    return total > rows_read and abs(int(rows_read) - VIRTUAL_WINDOW) <= 6


def _count(value, what) -> int:
    try:
        n = int(value)
    except (TypeError, ValueError):
        raise ShapeError(f"{what} is {value!r}, which is not a count.") from None
    if n < 0:
        raise ShapeError(f"{what} is {n}, and a count is never negative.")
    return n


FEED_STATES = ("live", "stopped", "never")


def read_account_tiles(payload) -> list:
    """Validate a read of the Banking screen tiles.

    One tile carries three separate facts that no export holds: the balance
    QuickBooks thinks the account has, the size of its queue, and whether the
    feed is still delivering. The third is the one that decides whether the
    other two mean anything.
    """
    check_page(payload)
    rows = payload.get("accounts") if isinstance(payload, dict) else payload
    if not isinstance(rows, list) or not rows:
        raise ShapeError(
            "a Banking read has to carry a list of account tiles. An empty list "
            "means the page did not render, and it is never the same thing as a "
            "company with no bank accounts."
        )
    out = []
    for r in rows:
        if not isinstance(r, dict):
            raise ShapeError(f"a tile is not an object: {r!r}")
        account = str(r.get("account") or "").strip()
        if not account:
            raise ShapeError(f"a tile does not say which account it is: {r!r}")
        state = str(r.get("feed_state") or "").strip().lower()
        if state and state not in FEED_STATES:
            raise ShapeError(
                f"the feed on {account} is recorded as {state!r}, and the three "
                f"states are {', '.join(FEED_STATES)}. 'unknown' is not one of "
                "them: read the line under the balance and say what it says."
            )
        tile = {
            "account": account,
            "feed_state": state,
            "for_review": (_count(r["for_review"], f"{account} for_review")
                           if "for_review" in r else None),
            "feed_last": str(r.get("feed_last") or "").strip(),
            "qbo_balance": (plain(money(r["qbo_balance"], f"{account} balance"))
                            if r.get("qbo_balance") not in (None, "") else ""),
        }
        # The bank's own balance is the one figure that says whether the books
        # are complete, and the QuickBooks tile is not it: the tile is what the
        # books think, which is the number under test. So a read that claims a
        # bank balance has to say which bank screen it came from.
        if r.get("bank_balance") not in (None, ""):
            src = str(r.get("bank_balance_source") or "").strip()
            if not src:
                raise ShapeError(
                    f"{account} carries a bank balance with no source named.\n"
                    "  The QuickBooks tile shows what the books think the account "
                    "holds, and comparing that figure to itself proves nothing. "
                    "A bank balance comes from the bank or the card issuer's own "
                    "site, and the read has to say which one, for example "
                    '"bank_balance_source": "the bank portal, accounts page".'
                )
            tile["bank_balance"] = plain(money(r["bank_balance"],
                                               f"{account} bank balance"))
            tile["bank_balance_source"] = src
            tile["bank_balance_as_of"] = str(r.get("bank_balance_as_of") or "").strip()

        if state == "stopped" and not tile["feed_last"]:
            raise ShapeError(
                f"{account} is recorded as a stopped feed with no date on it.\n"
                "  A stopped feed is a hole in the books that starts on a day, "
                "and the day decides which months need statements. Read the line "
                "under the balance and pass the date it gives."
            )
        out.append(tile)
    return out


def read_reconcile_summary(payload) -> list:
    """Validate a read of Reconcile, which answers reconciled-through per account."""
    check_page(payload)
    rows = payload.get("accounts") if isinstance(payload, dict) else payload
    if not isinstance(rows, list) or not rows:
        raise ShapeError(
            "a reconciliation read has to carry a row per account. This one view "
            "answers reconciled-through for the whole file, so an empty read "
            "means it did not load."
        )
    out = []
    for r in rows:
        account = str((r or {}).get("account") or "").strip()
        if not account:
            raise ShapeError(f"a reconciliation row does not name an account: {r!r}")
        through = str(r.get("reconciled_through") or "").strip()
        if through and not re.match(r"^\d{4}-\d{2}-\d{2}$", through):
            raise ShapeError(
                f"{account} is reconciled through {through!r}, which is not a "
                "date in YYYY-MM-DD. Dates in this interface are MM/DD/YYYY, so "
                "normalize before recording, or a year comparison silently "
                "matches nothing."
            )
        out.append({"account": account, "reconciled_through": through,
                    "never": not through})
    return out


def read_rules(payload) -> dict:
    """Validate a read of the bank rules screen.

    The count matters less than the split. A rule that only suggests is a rule
    a person still sees. A rule with auto-add on posts into the year being filed
    while the year is being worked, and every hour worked before it stops is
    worked twice.
    """
    check_page(payload)
    if not isinstance(payload, dict):
        raise ShapeError("a rules read has to be an object with a count.")
    if "count" not in payload:
        raise ShapeError(
            "the rules read carries no count. Open the gear, then Rules, and "
            "read how many there are."
        )
    total = _count(payload["count"], "rules count")
    auto = payload.get("auto_add")
    if auto is None:
        raise ShapeError(
            f"the rules read says {total:,} rules and does not say how many of "
            "them post without being seen.\n"
            "  That is the number that decides whether any figure produced today "
            "is about a file that is still changing. The Auto-add column on the "
            "Rules screen carries it."
        )
    auto = _count(auto, "auto-add count")
    if auto > total:
        raise ShapeError(
            f"the read says {auto:,} rules post automatically out of {total:,} "
            "rules in total, which cannot both be true. Read the screen again."
        )
    return {"count": total, "auto_add": auto,
            "read_at": str(payload.get("read_at") or _dt.date.today().isoformat())}


def read_audit_log(payload) -> dict:
    """Validate a read of the audit log, which is who last touched the file."""
    check_page(payload)
    if not isinstance(payload, dict):
        raise ShapeError("an audit log read has to be an object.")
    last = str(payload.get("last_human_date") or "").strip()
    if not last:
        raise ShapeError(
            "the audit log read does not say when a person last worked in the "
            "file.\n"
            "  Filter to all users, find the most recent entry that is not a rule "
            "or a sync, and pass its date. Everything after that date arrived "
            "without anybody looking at it."
        )
    if not re.match(r"^\d{4}-\d{2}-\d{2}$", last):
        raise ShapeError(
            f"the last human date is {last!r}. The audit log prints MM/DD/YYYY, "
            "so normalize it to YYYY-MM-DD before recording it."
        )
    return {"last_human_date": last,
            "by": str(payload.get("by") or "").strip(),
            "note": str(payload.get("note") or "").strip(),
            "read_at": str(payload.get("read_at") or _dt.date.today().isoformat())}


READERS = {
    "for-review": read_for_review,
    "banking": read_account_tiles,
    "reconcile-summary": read_reconcile_summary,
    "rules": read_rules,
    "audit-log": read_audit_log,
}


# --------------------------------------------------------------- the batches


@dataclass
class PostRow:
    """One thing that is about to happen in the books, written out in full.

    Everything a person needs to agree or disagree is on this row, including the
    reason and how many past transactions back it. A row whose reason is blank
    is a row nobody can check, so it never reaches a batch.
    """
    ref: str
    date: str
    descriptor: str
    amount: str
    account: str = ""
    klass: str = ""
    payee: str = ""
    why: str = ""
    action: str = "categorize"
    counter_account: str = ""
    needs_human: bool = False
    detail: list = field(default_factory=list)

    def cells(self):
        return [self.ref, self.date, self.descriptor, self.amount, self.account,
                self.klass, self.payee, self.action, self.counter_account,
                "yes" if self.needs_human else "", self.why,
                " ; ".join(str(d) for d in self.detail)]


@dataclass
class PostBatch:
    """One approvable unit: a named batch of a named set of rows.

    The hash covers the rows and nothing else, so re-writing the file with the
    same decisions keeps an approval alive and changing a single account voids
    it. That is the same contract the review workbook has, and it exists because
    the failure it prevents is quiet: approve a batch, edit a row, post.
    """
    tag: str
    kind: str
    account: str
    rows: list
    company: str = ""
    counter: str = ""
    built: str = ""
    source: str = ""

    def __post_init__(self):
        if self.kind not in KINDS:
            raise BrowserError(
                f"{self.kind!r} is not a kind of write this mode does. The three "
                f"are {', '.join(KINDS)}."
            )
        if not self.rows:
            raise BrowserError(
                f"batch {self.tag} holds no rows. An empty batch that can be "
                "approved is an approval that covers nothing and looks like one "
                "that covers something."
            )
        if len(self.rows) > MAX_BATCH_ROWS:
            raise BrowserError(
                f"batch {self.tag} holds {len(self.rows):,} rows, and the most "
                f"one batch may hold is {MAX_BATCH_ROWS}.\n"
                "  A batch is the unit somebody reads before it reaches their "
                "books. Past a screenful, an approval stops being a decision."
            )
        self.built = self.built or _dt.date.today().isoformat()

    @property
    def expected_delta(self) -> int:
        """How the counter has to move if this batch does exactly what it says.

        Categorizing takes rows out of the queue, so the queue falls. Adding a
        transaction and posting an entry both put rows into a register, so the
        register rises. A sign that is the wrong way round is the loudest
        possible signal that something else fired.
        """
        return -len(self.rows) if self.kind == "categorize" else len(self.rows)

    def row_hash(self) -> str:
        h = hashlib.sha256()
        for r in self.rows:
            h.update("\x1f".join(str(c) for c in r.cells()).encode("utf-8"))
            h.update(b"\x1e")
        h.update(f"{self.kind}|{self.account}|{self.counter}".encode("utf-8"))
        return h.hexdigest()

    def to_json(self) -> str:
        return json.dumps({
            "tag": self.tag,
            "kind": self.kind,
            "account": self.account,
            "company": self.company,
            "counter": self.counter,
            "built": self.built,
            "source": self.source,
            "rows": [asdict(r) for r in self.rows],
            "row_hash": self.row_hash(),
            "expected_delta": self.expected_delta,
        }, indent=2, sort_keys=True) + "\n"

    def to_markdown(self) -> str:
        """What the founder reads. The rows, in full, with the reason on each."""
        what = {
            "categorize": ("set the category and payee on these rows in For Review "
                           "and accept them"),
            "add": ("add these transactions, which your statement shows and your "
                    "feed never delivered"),
            "journal": "post these journal entries",
        }[self.kind]
        n = len(self.rows)
        plural = "row" if n == 1 else "rows"
        out = [
            f"# batch-{self.tag}: {n} {plural}",
            "",
            f"**Company:** {self.company or '(not confirmed)'}  ",
            f"**Account:** {self.account or '(several)'}  ",
            f"**What happens if you approve this:** your agent will {what}, in "
            f"your own browser, in your live books.  ",
            f"**Built:** {self.built}",
            "",
        ]
        if self.source:
            out += [f"Sourced from {self.source}.", ""]
        out += [
            "Read the reason on each row. A row saying twelve of twelve past "
            "payments to this vendor went to one account is a claim you are "
            "checking, and a row with a thin reason is the one to look at "
            "hardest.",
            "",
            "| # | date | description | amount | account | class | payee | why |",
            "|---|---|---|---|---|---|---|---|",
        ]
        for i, r in enumerate(self.rows, start=1):
            flag = " **NEEDS YOU**" if r.needs_human else ""
            out.append(
                f"| {i} | {r.date} | {_cell(r.descriptor)}{flag} | {r.amount} | "
                f"{_cell(r.account)} | {_cell(r.klass)} | {_cell(r.payee)} | "
                f"{_cell(r.why)} |"
            )
        detailed = [r for r in self.rows if r.detail]
        if detailed:
            out += ["", "### The lines on each", ""]
            for i, r in enumerate(self.rows, start=1):
                if not r.detail:
                    continue
                out.append(f"**{i}. {_cell(r.descriptor)}** ({r.date})")
                out += [f"- {_cell(d)}" for d in r.detail]
                out.append("")
        flagged = [r for r in self.rows if r.needs_human]
        out += ["", f"{n} of {n} {plural} listed above, in full. "
                    "Nothing is hidden behind a scroll."]
        if flagged:
            out += [
                "",
                f"{len(flagged)} of {n} rows carry a description with "
                "text in it aimed at an automated system. Whoever sent the money "
                "chose that text. It is quoted here and it was not acted on. "
                "Decide those rows yourself.",
            ]
        out += [
            "",
            "## To approve this batch",
            "",
            "Run this in your own terminal. Your agent cannot run it, and there "
            "is no flag that lets it.",
            "",
            "```",
            f"python3 bin/books.py approve batch-{self.tag}",
            "```",
            "",
            "If you change anything in this batch after approving it, the "
            "approval stops covering it and everything downstream stops until you "
            "look again.",
            "",
        ]
        return "\n".join(out)


def _cell(text) -> str:
    t = str(text or "").replace("|", "/").replace("\n", " ").strip()
    return t if t else " "


def slice_batches(rows, *, kind, account, size=DEFAULT_BATCH_ROWS, company="",
                  counter="", prefix="s", source="") -> list:
    """Cut a list of rows into batches a person can actually read.

    Numbering is stable and one-based, so batch-s01 means the same rows on a
    second run over the same input and an approval survives a re-plan that
    changed nothing.
    """
    size = int(size or DEFAULT_BATCH_ROWS)
    if size < 1:
        raise BrowserError("a batch of fewer than one row is not a batch.")
    if size > MAX_BATCH_ROWS:
        raise BrowserError(
            f"a batch of {size:,} rows is past the {MAX_BATCH_ROWS} row limit.\n"
            "  The limit is there because an approval over more rows than a "
            "person reads is an approval of the scroll bar. Work in smaller "
            "batches, which is slower to set up and faster to finish, because "
            "a wrong batch costs one batch."
        )
    rows = list(rows)
    out = []
    for i in range(0, len(rows), size):
        tag = f"{prefix}{(i // size) + 1:02d}"
        out.append(PostBatch(tag=tag, kind=kind, account=account,
                             rows=rows[i:i + size], company=company,
                             counter=counter, source=source))
    return out


# ------------------------------------------------------------------ the run

# A run ends verified, or halted, or halted and then cleared by a person
# who wrote down what the difference was.
RUN_STATES = ("open", "verified", "halted", "cleared")


@dataclass
class Run:
    """One batch being posted, from the count before to the count after.

    A run is open for exactly one batch. It carries the count that was read
    before anything was clicked, the delta the approval predicts, and nothing
    else may be posted while it is open. Two open runs would mean two counts
    moving at once and neither of them provable.
    """
    tag: str
    kind: str
    account: str
    rows: int
    row_hash: str
    company: str
    counter: str
    before: int
    expected_delta: int
    approved_by: str = ""
    approved_at: str = ""
    opened_at: str = ""
    state: str = "open"
    after: object = None
    verified_at: str = ""
    halt_reason: str = ""
    posted: list = field(default_factory=list)

    @property
    def expected_after(self) -> int:
        return int(self.before) + int(self.expected_delta)

    def to_json(self) -> str:
        return json.dumps(asdict(self), indent=2, sort_keys=True) + "\n"

    @classmethod
    def from_dict(cls, data):
        known = {k: data[k] for k in data if k in cls.__annotations__}
        return cls(**known)


def open_run(batch: PostBatch, *, before, approved_by, approved_at,
             company="") -> Run:
    """Start a run for one approved batch, holding the count read beforehand."""
    return Run(
        tag=batch.tag,
        kind=batch.kind,
        account=batch.account,
        rows=len(batch.rows),
        row_hash=batch.row_hash(),
        company=company or batch.company,
        counter=batch.counter or _default_counter(batch),
        before=_count(before, f"the count before batch-{batch.tag}"),
        expected_delta=batch.expected_delta,
        approved_by=str(approved_by or ""),
        approved_at=str(approved_at or ""),
        opened_at=_now(),
        state="open",
    )


def _default_counter(batch: PostBatch) -> str:
    if batch.kind == "categorize":
        return f"for-review:{batch.account}"
    if batch.kind == "add":
        return f"register:{batch.account}"
    return "register:journal"


def verify_run(run: Run, after, *, company="") -> Run:
    """Close a run, or halt it. There is no third outcome and no flag.

    This is the check that catches what nothing else can see: a bank rule that
    fired underneath the work, a page that was stale when it was clicked, a
    double click on Accept, a batch that was posted twice because the first
    attempt looked like it failed. Each of those leaves the count wrong by a
    small number, and a small number is exactly what nobody notices.
    """
    if run.state != "open":
        raise RunError(
            f"batch-{run.tag} is {run.state}, and only an open run gets verified.\n"
            + ("  It was already verified at " + run.verified_at + "."
               if run.state == "verified" else
               "  It halted: " + (run.halt_reason or "no reason recorded") + ".")
        )
    if company and run.company and _company_key(company) != _company_key(run.company):
        run.state = "halted"
        run.halt_reason = (
            f"the count after the batch was read in {company!r} and the batch was "
            f"posted in {run.company!r}. A count from another company file proves "
            "nothing about this one."
        )
        raise CompanyMismatch(run.halt_reason)

    n = _count(after, f"the count after batch-{run.tag}")
    run.after = n
    if n == run.expected_after:
        run.state = "verified"
        run.verified_at = _now()
        return run

    run.state = "halted"
    moved = n - int(run.before)
    run.halt_reason = _halt_text(run, n, moved)
    run.verified_at = _now()
    raise CountMismatch(run.halt_reason)


def _halt_text(run: Run, after, moved) -> str:
    """Say what was approved, what was read, and what the difference usually is."""
    want = int(run.expected_delta)
    direction = "fell" if moved < 0 else ("rose" if moved > 0 else "did not move")
    lines = [
        f"Halted after batch-{run.tag}. The count is not what the approval "
        f"predicted, so nothing else runs.",
        "",
        f"  approved      {run.rows:,} rows, {run.kind}",
        f"  counter       {run.counter}",
        f"  before        {int(run.before):,}",
        f"  after         {int(after):,}",
        f"  expected      {run.expected_after:,}",
        f"  it {direction:<11} {abs(moved):,}, and it should have moved "
        f"{abs(want):,}",
        "",
    ]
    surplus = abs(moved) - abs(want)
    if moved == 0:
        lines += [
            "Nothing moved. Either the batch did not post at all, or the count "
            "was read from a stale page, or a filter on the queue is hiding the "
            "rows that changed.",
            "Read the count again on a reloaded page before concluding anything.",
        ]
    elif (moved > 0) != (want > 0):
        lines += [
            "It moved the wrong way, which is the loudest signal here. A "
            "categorization that adds rows to a queue, or an entry that removes "
            "them, is something other than the batch that was approved.",
            "Stop, and look at the audit log for what posted in the last few "
            "minutes and who posted it.",
        ]
    elif surplus > 0:
        lines += [
            f"{surplus:,} more rows moved than were approved. The usual cause is "
            "a bank rule with auto-add on, firing underneath the work. It is also "
            "what a double click on Accept looks like, and what a batch posted "
            "twice looks like.",
            "Check the rules screen for auto-add, then the audit log for the last "
            "few minutes.",
        ]
    else:
        lines += [
            f"{abs(surplus):,} fewer rows moved than were approved. Some of the "
            "batch did not land. A row whose account was blank, a row where the "
            "Class column was off, and a row where the checkbox never registered "
            "all look like this.",
            "Read the queue and find which of the approved rows are still in it.",
        ]
    lines += [
        "",
        "Nothing here is fixed by running the next batch. Find the difference, "
        "then clear the halt with a note saying what it was:",
        f"    python3 bin/books.py browser clear batch-{run.tag} --note "
        f"\"what it was\"",
    ]
    return "\n".join(lines)


# ------------------------------------------------------- the run directory

RUNS_DIR = "reports/browser-runs"


def run_path(workdir, tag) -> Path:
    return Path(workdir) / RUNS_DIR / f"{_tag(tag)}.json"


def _tag(tag) -> str:
    t = re.sub(r"^batch-", "", str(tag or "").strip(), flags=re.I)
    if not re.fullmatch(r"[A-Za-z0-9_]+", t or ""):
        raise BrowserError(
            f"{tag!r} is not a batch name. A batch is letters, digits or "
            "underscores, for example s01."
        )
    return t.lower()


def save_run(workdir, run: Run) -> Path:
    p = run_path(workdir, run.tag)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(run.to_json(), encoding="utf-8")
    return p


def load_run(workdir, tag) -> Run | None:
    p = run_path(workdir, tag)
    if not p.exists():
        return None
    try:
        return Run.from_dict(json.loads(p.read_text(encoding="utf-8")))
    except (json.JSONDecodeError, TypeError) as exc:
        raise RunError(f"the run file {p} is unreadable: {exc}") from exc


def all_runs(workdir) -> list:
    d = Path(workdir) / RUNS_DIR
    if not d.is_dir():
        return []
    out = []
    for p in sorted(d.glob("*.json")):
        try:
            out.append(Run.from_dict(json.loads(p.read_text(encoding="utf-8"))))
        except (json.JSONDecodeError, TypeError):
            raise RunError(
                f"{p} is not a readable run file. A run file that cannot be read "
                "is a run whose outcome is unknown, and this mode does not "
                "continue past one."
            )
    return out


def open_runs(workdir) -> list:
    return [r for r in all_runs(workdir) if r.state == "open"]


def halted_runs(workdir) -> list:
    return [r for r in all_runs(workdir) if r.state == "halted"]


def blocking(workdir) -> str:
    """Why nothing may be posted right now, or an empty string.

    One halt anywhere stops every batch, not only its own. That is the point of
    the check: a count that did not add up means the file moved in a way nobody
    predicted, and the next batch would be posted on top of it.
    """
    halted = halted_runs(workdir)
    if halted:
        r = halted[0]
        return (
            f"batch-{r.tag} halted and has not been cleared.\n"
            f"  {(r.halt_reason or '').splitlines()[0] if r.halt_reason else ''}\n"
            "  Every batch waits while a halt stands, not only that one. The "
            "count went somewhere nobody predicted, and posting on top of it "
            "makes the difference harder to find rather than smaller.\n"
            f"  Read {RUNS_DIR}/{r.tag}.json, find the cause, then:\n"
            f"      python3 bin/books.py browser clear batch-{r.tag} --note "
            f"\"what it was\""
        )
    live = open_runs(workdir)
    if live:
        r = live[0]
        return (
            f"batch-{r.tag} is still open. It was opened at {r.opened_at} with "
            f"{int(r.before):,} on {r.counter}, and no count has been read since.\n"
            "  One run at a time, because two counts moving at once cannot be "
            "attributed to either batch.\n"
            f"  Finish it by reading the count and running:\n"
            f"      python3 bin/books.py browser verify batch-{r.tag} --after N"
        )
    return ""


def finished_runs(workdir, counter) -> list:
    """Every run on one counter that reached a count after it."""
    return [r for r in all_runs(workdir)
            if r.counter == counter and r.after is not None
            and r.state in ("verified", "cleared")]


def accounted_movement(workdir, counter, counted=()) -> tuple:
    """How much of a counter's movement is explained by approved batches.

    Returns (rows, tags). A run counts once, and `counted` says which ones a
    previous read already accounted for. Runs are matched by name rather than
    by time on purpose: two events inside the same second are ordinary, and a
    check that silently drops one is worse than no check.

    Anything left over is movement in somebody's books that no approval
    predicted, which is the thing that has no other way of being noticed: an
    agent that clicked without asking, a rule somebody switched back on, or a
    second person working the same queue.
    """
    already = {str(t) for t in counted or ()}
    rows, tags = 0, []
    for r in finished_runs(workdir, counter):
        if r.tag in already:
            continue
        rows += int(r.before) - int(r.after)
        tags.append(r.tag)
    return rows, tags


def unexplained_movement(workdir, counter, *, previous, current,
                         counted=()) -> dict:
    """The gap between what a queue did and what anybody approved.

    `previous` and `current` are two reads of the same counter. Movement is
    stated as rows that left the queue, so a positive number means the queue got
    shorter.
    """
    moved = int(previous) - int(current)
    approved, tags = accounted_movement(workdir, counter, counted=counted)
    return {"counter": counter, "moved": moved, "approved": approved,
            "unexplained": moved - approved, "batches": tags,
            "counted": sorted(set(str(t) for t in counted or ()) | set(tags))}


def movement_text(gap) -> str:
    """Say what moved, what was approved, and what nobody can account for."""
    n = gap["unexplained"]
    if n == 0:
        return ""
    lines = [
        f"{abs(n):,} rows on {gap['counter']} moved with no approval behind them.",
        "",
        f"  the queue moved   {gap['moved']:,} rows since it was last read",
        f"  approved batches  {gap['approved']:,} rows"
        + (f", in {', '.join('batch-' + t for t in gap['batches'])}"
           if gap["batches"] else ""),
        f"  unexplained       {n:,} rows",
        "",
    ]
    if n > 0:
        lines += [
            "Rows left this queue that no batch was approved for. Three things "
            "look like this, and all three matter:",
            "  a bank rule that was switched back on, which the rules screen "
            "shows",
            "  somebody else working the same queue, which the audit log shows",
            "  an agent working rows without asking, which is the one nothing "
            "else here would catch",
            "",
            "Read the audit log for the period between the two reads before "
            "doing anything else. Nothing about this is fixed by carrying on.",
        ]
    else:
        lines += [
            "The queue grew. That is the feed delivering, which is ordinary, and "
            "it is also what a re-download of an already booked month looks "
            "like. Check the dates on the new rows against the last reconciled "
            "month before working them.",
        ]
    return "\n".join(lines)


def clear_halt(workdir, tag, *, note, by="") -> Run:
    """Retire a halt, with a note saying what the difference actually was."""
    run = load_run(workdir, tag)
    if run is None:
        raise RunError(f"there is no run called batch-{_tag(tag)} to clear.")
    if run.state != "halted":
        raise RunError(
            f"batch-{run.tag} is {run.state} rather than halted. There is nothing "
            "to clear."
        )
    if not str(note or "").strip():
        raise RunError(
            "clearing a halt needs a note saying what the difference was.\n"
            "  A halt cleared with no explanation is a halt that will happen "
            "again, and the note is the only place the cause is ever written "
            "down."
        )
    run.state = "cleared"
    run.halt_reason = (run.halt_reason or "") + (
        f"\n\nCLEARED {_now()} by {by or 'the owner'}: {str(note).strip()}"
    )
    save_run(workdir, run)
    return run


# --------------------------------------------------------- the step lists

def steps_for(batch: PostBatch) -> list:
    """The exact clicks, in order, with the trap that applies to each one.

    This is what the agent follows and what the founder can read along with.
    Written out rather than left to judgement, because the traps in this
    interface are not discoverable by trying: a checkbox that only exists on
    hover looks like a checkbox that is not there.
    """
    if batch.kind == "categorize":
        return [
            "Confirm the company name in the header is still the one confirmed "
            "for this run. Stop if it is not.",
            f"Open Transactions, Bank transactions, and select {batch.account}.",
            "Turn the Class column on under the gear, then Columns. Posting "
            "without it writes 'Not specified', which is a defect.",
            "Clear every filter, then read the queue count and record it as the "
            "count before.",
            "For each row in the batch, in order: hover the row so its controls "
            "appear, set the payee, set the category, set the class, then post "
            "that row. Row by row, because bulk selection does not survive a "
            "re-render.",
            "On a card payment row, leave the Match toggle alone.",
            "When the batch is done, reload the page, clear the filters again, "
            "and read the count. That is the count after.",
        ]
    if batch.kind == "add":
        return [
            "Confirm the company name in the header is still the one confirmed "
            "for this run. Stop if it is not.",
            f"Open the register for {batch.account} at /app/register, and read "
            "the row count for the period. That is the count before.",
            "Add each transaction from the batch with its date, amount, payee, "
            "account and class.",
            "Save each one before starting the next, and check the running "
            "balance moved by the amount you entered.",
            "Read the register row count again for the same period. That is the "
            "count after.",
        ]
    return [
        "Confirm the company name in the header is still the one confirmed for "
        "this run. Stop if it is not.",
        "Open a brand new tab for the journal form. It only mounts in a fresh "
        "tab, and in a tab that has loaded the report builder it fails silently "
        "with about 125 characters of body text and no error at all.",
        "Read the journal report row count for the period, or the register of "
        "the account the entries touch. That is the count before.",
        "Type each entry, and check debits equal credits before saving it.",
        "Chain them with Save and new. Escape closes the whole panel and "
        "discards what is in it.",
        "Read the same count again. That is the count after.",
    ]
