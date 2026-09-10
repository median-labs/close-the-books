#!/usr/bin/env python3
"""books.py, the command line for close-the-books.

One file, no dependency beyond openpyxl, no network. It reads files you
exported and writes files next to them. It never touches QuickBooks.

Run `python3 bin/books.py --help` for the command list, or `books.py init` to
build a working directory and be told what goes where.

THREE EXIT CODES, AND THE DIFFERENCE MATTERS

    0   it ran and everything it checked passed
    1   it ran and something is wrong: a check failed, or a file would not read
    2   it refused: something it needs is missing, or a batch is not approved

A caller has to be able to tell "it said no" from "it broke", which is why a
refusal has its own code and never a traceback. Tracebacks are for genuine
bugs, and --debug is how you see one.

WHY THE LIBRARY IS NEVER MODIFIED FROM HERE

Everything in lib/closethebooks is tested on its own terms. Where a module's
API does not quite fit a subcommand, the adapting happens in this file, in
functions named for what they adapt. That keeps the engine honest and keeps the
convenience in one place a reader can audit.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import os
import re
import sys
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
sys.path.insert(0, str(REPO / "lib"))

# ---------------------------------------------------------------- exit codes

OK = 0
FAILURE = 1        # it ran, and something is wrong
REFUSAL = 2        # it refused, because it is missing something it needs


class Refusal(Exception):
    """Something needed is missing. Exits 2, never with a traceback."""


class Failure(Exception):
    """It ran and found something wrong. Exits 1, never with a traceback."""


# The standard working directory. `init` creates it; every command assumes it.
LAYOUT = (
    ("exports", "the xlsx reports you exported from QuickBooks"),
    ("statements", "one bank or card statement per account per month, CSV or PDF"),
    ("for-review", "the For Review queue, exported per account"),
    ("profiles", "what the engine learned about your books (mine.local.json)"),
    ("review", "the batch workbooks you read and approve"),
    ("import", "the files you upload to QuickBooks yourself, once approved"),
    ("questions", "the questions for the owner, one file per round"),
    ("documents", "the documents the firm filing the return asked for"),
    ("answers", "their answers, each with a date and who said it"),
    ("reports", "reconciliations, exit tests, entry worksheets"),
    ("handoff", "the evidence ledger, change log and archive for whoever files"),
)

PROFILE_DEFAULT = "profiles/mine.local.json"

# openpyxl is the one dependency. Say so in words, rather than letting an
# ImportError traceback be the first thing a non-programmer sees.
try:
    import openpyxl  # noqa: F401
except ImportError:  # pragma: no cover - environment, not logic
    sys.stderr.write(
        "This needs openpyxl, which reads and writes Excel files.\n"
        "Install it with:  python3 -m pip install openpyxl\n"
    )
    sys.exit(REFUSAL)

from closethebooks import (  # noqa: E402
    autoposted, bank_csv, browser, coverage as coverage_mod, evidence as evidence_mod,
    exit_tests, feed_gaps, filing_year as filing_year_mod, twins,
    je_worksheet, matching, median, precedent, profile as profile_mod,
    qbo_exports, readiness,
    recon as recon_mod, review_workbook, rules_xlsx, sides,
    statement_request, statements as statements_mod, tieout,
)
from closethebooks.approval import (  # noqa: E402
    ApprovalError, batch_tag as parse_batch_tag, check as approval_check,
    read_approval, write_approval,
)
from closethebooks.entries import (  # noqa: E402
    DraftError, intangibles, payroll, prepaid, reclass as reclass_mod, stripe,
    wind_down as wind_down_mod,
)
from closethebooks.je_csv import UnresolvedAccount  # noqa: E402
from closethebooks.model import BankLine, is_real_account  # noqa: E402
from closethebooks.profile import (  # noqa: E402
    AccountSpec, Entity, Profile, ProfileError,
)
from closethebooks.util import (  # noqa: E402
    ZERO, DateError, fmt, iso, money, month_key, month_range, norm_text,
    parse_date, plain, try_date,
)

ExportError = qbo_exports.ExportError
ParseError = statements_mod.ParseError

# Everything a user can hit by having the wrong file, no file, or an unapproved
# batch. All of these are refusals, not crashes.
REFUSING_ERRORS = (
    Refusal, ApprovalError, ProfileError, DraftError, ExportError, ParseError,
    UnresolvedAccount, evidence_mod.EvidenceError, DateError,
    review_workbook.ReviewFileError, je_worksheet.WorksheetError,
    rules_xlsx.RuleNotExportable, rules_xlsx.TemplateUnusable,
    bank_csv.UploadTooLarge, bank_csv.BankLineError,
    # Everything browser mode stops on: an unconfirmed company, a read whose
    # shape proves nothing, a run that is already open. Each is a refusal
    # rather than a crash, and each names the one fact that would change it.
    # `browser.CountMismatch` is deliberately caught before it reaches here:
    # a count that did not add up is a finding about the books rather than a
    # missing input, so it exits 1.
    browser.BrowserError,
)


# ------------------------------------------------------------------- output

def say(text=""):
    print(text)


def head(text):
    say("")
    say(text)
    say("-" * min(len(text), 78))


def n_of(n, total, what):
    """Every count in this tool carries its denominator. `n of N thing`."""
    return f"{n:,} of {total:,} {what}"


def bullet(label, value):
    say(f"  {label:<22} {value}")


def table(rows, headers):
    """A plain aligned table. Nothing that needs a terminal library."""
    if not rows:
        return
    body = [[str(c) for c in r] for r in rows]
    widths = [max([len(str(h))] + [len(r[i]) for r in body])
              for i, h in enumerate(headers)]
    say("  " + "  ".join(str(h).ljust(w) for h, w in zip(headers, widths)).rstrip())
    say("  " + "  ".join("-" * w for w in widths))
    for r in body:
        say("  " + "  ".join(c.ljust(w) for c, w in zip(r, widths)).rstrip())


def wrap_cell(rows, column, width=64):
    """Wrap one column, spilling into continuation rows with the rest blank.

    A measured number is the point of a check, so it is never truncated. It
    wraps instead, and the row it belongs to stays readable in a terminal.
    """
    out = []
    for row in rows:
        row = list(row)
        text = str(row[column])
        chunks, line = [], ""
        for word in text.split(" "):
            if line and len(line) + 1 + len(word) > width:
                chunks.append(line)
                line = word
            else:
                line = f"{line} {word}".strip()
        chunks.append(line)
        row[column] = chunks[0]
        out.append(row)
        for extra in chunks[1:]:
            spill = [""] * len(row)
            spill[column] = extra
            out.append(spill)
    return out


def pct(x):
    return f"{100.0 * float(x or 0.0):.1f}%"


# A BALANCE IS SHOWN ON ITS ACCOUNT'S OWN SIDE, EVERYWHERE A PERSON READS IT
#
# The engine is debit-positive internally and stays that way. It is not how
# anyone reads a balance sheet: an equity account holding 3,118,447.25 on the
# credit side printed as `-3,118,447.25` says the company has negative SAFE
# notes, and a tool that prints that looks broken before it has said anything
# useful. `balance` renders the magnitude and the side, `1,862,400.00 Cr`, the
# way the exported trial balance prints the same figure.
#
# `fmt` stays for everything that is NOT a balance: a difference, a movement, a
# deposit total, one transaction's amount. A difference of -100.00 is not
# "100.00 Cr"; its sign says which way two figures disagree.
balance = sides.balance_text


def say_lines(lines):
    for line in lines:
        say(line)


# ---------------------------------------------------- the working directory

class Work:
    """Where everything lives. Default `.`, overridable with --workdir."""

    def __init__(self, root):
        self.root = Path(root).resolve()

    def __truediv__(self, name):
        return self.root / name

    def dir(self, name, *, create=False, must_exist=False, what=""):
        p = self.root / name
        if create:
            p.mkdir(parents=True, exist_ok=True)
        if must_exist and not p.is_dir():
            raise Refusal(
                f"there is no {name}/ folder in {self.root}.\n"
                f"  It holds {what or dict(LAYOUT).get(name, 'your files')}.\n"
                f"  Run `books.py init` to create the folders, then put the files in."
            )
        return p

    def ensure(self, *names):
        for name in names:
            (self.root / name).mkdir(parents=True, exist_ok=True)

    def rel(self, path):
        try:
            return str(Path(path).resolve().relative_to(self.root))
        except (ValueError, OSError):
            return str(path)


def resolve_dir(work, given, default_name, what):
    """A directory argument: what was passed, else the standard place."""
    if given:
        p = Path(given)
        if not p.is_absolute():
            p = Path.cwd() / p
        if not p.is_dir():
            raise Refusal(f"{given} is not a folder. It should hold {what}.")
        return p
    return work.dir(default_name, must_exist=True, what=what)


# ---------------------------------------------------------------- profiles

def profile_path(work, given):
    if given:
        p = Path(given)
        return p if p.is_absolute() else (Path.cwd() / p)
    return work.root / PROFILE_DEFAULT


def load_profile(work, given):
    p = profile_path(work, given)
    if not p.exists():
        raise Refusal(
            f"no profile yet at {p}.\n"
            f"  A profile is what the engine learned about your books.\n"
            f"  Build one from your own exports:\n"
            f"      python3 bin/books.py learn --exports exports/\n"
            f"  Or try the invented example company:\n"
            f"      python3 examples/acme-robotics/build.py\n"
            f"      python3 bin/books.py learn --exports examples/acme-robotics/exports"
        )
    return profile_mod.load(p)


def save_profile(prof, path=None):
    return profile_mod.save(prof, path)


# ------------------------------------------------------------------ ledger

def load_ledger(work, exports_arg):
    """The posted history, from the QuickBooks xlsx exports."""
    d = resolve_dir(work, exports_arg, "exports",
                    "the xlsx reports you exported from QuickBooks")
    if not qbo_exports.discover(d):
        raise Refusal(
            f"{d} holds no QuickBooks xlsx exports.\n"
            f"  Export these from QuickBooks as Excel, not PDF, and put them there:\n"
            f"      Account List, Trial Balance, General Ledger, Journal,\n"
            f"      Balance Sheet, Profit and Loss Detail, Profit and Loss by Month."
        )
    return qbo_exports.load_all(str(d))


def resolver_for(ledger, prof=None):
    """Account key to the full name QuickBooks renders. Used by every writer."""
    index = {}
    for row in (getattr(prof, "chart", None) or []):
        key = str(row.get("number") or row.get("full_name") or row.get("name") or "").strip()
        full = str(row.get("full_name") or row.get("name") or "").strip()
        if key and full:
            index.setdefault(key, full)
            index.setdefault(norm_text(key), full)
    for acct in (getattr(ledger, "accounts", {}) or {}).values():
        index.setdefault(acct.key, acct.full_name)
        index.setdefault(norm_text(acct.key), acct.full_name)
        index.setdefault(norm_text(acct.full_name), acct.full_name)

    def resolve(key):
        k = str(key or "").strip()
        return index.get(k) or index.get(norm_text(k)) or ""

    return resolve


# -------------------------------------------------------------- statements

_MASK_RE = re.compile(r"(?<![0-9])(\d{3,6})(?![0-9])")


def account_for_file(basename, prof):
    """Which declared account a statement or queue file belongs to, or None.

    The mask is checked first because it is the one token a bank always puts in
    a file name and rarely shares with anything else. The account is never
    guessed from the file: which accounts exist is a fact about the company, and
    it lives in the profile.
    """
    name = norm_text(basename)
    digits = _MASK_RE.findall(re.sub(r"[^0-9]+", " ", name))
    for spec in (prof.accounts or []):
        if spec.mask and str(spec.mask) in digits:
            return spec
    for spec in (prof.accounts or []):
        if spec.mask and str(spec.mask) in basename:
            return spec
    for spec in (prof.accounts or []):
        tokens = [t for t in re.split(r"[^a-z0-9]+", norm_text(spec.label)) if len(t) >= 4]
        if tokens and all(t in name for t in tokens):
            return spec
    for spec in (prof.accounts or []):
        if spec.book and norm_text(spec.book) in name:
            return spec
    return None


def load_statements(work, given, prof, quiet=False, *, require=True,
                    note_when_empty=True):
    """Parse every statement, and label each with the account it belongs to.

    `require=False` returns nothing instead of refusing when the folder is empty
    or absent. An empty `statements/` is the NORMAL state of a working directory
    on the first day of a catch-up, and it used to make `reconcile`, `check` and
    `handoff` unreachable, which are the three commands that do most of their
    work without a statement. Each of those passes `require=False` and says
    which parts it could not do. `tieout` cannot: there is nothing to tie out
    without a statement, so it still refuses, and it prints the request first.
    """
    try:
        d = resolve_dir(work, given, "statements",
                        "one bank or card statement per account per month")
    except Refusal:
        if require:
            raise
        return [], [], []
    paths = [str(p) for p in sorted(Path(d).rglob("*"))
             if p.is_file() and p.suffix.lower() in (".csv", ".pdf", ".tsv")]
    if not paths:
        if not require:
            if not quiet and note_when_empty:
                say("")
                say(f"No statements in {work.rel(d)}/, so nothing below is checked "
                    f"against an external document.")
                say("What to get, which months, and why each one:")
                say("      python3 bin/books.py statements")
            return [], [], []
        raise Refusal(
            f"{d} holds no statement files.\n"
            f"  Download every account's statements for the period, CSV where your\n"
            f"  bank offers it, and put them there. Include the ones the owner thinks\n"
            f"  are dormant: a dormant account with a forgotten annual fee is exactly\n"
            f"  the kind of thing that turns up here.\n"
            f"  For the exact list of accounts and months, and why each one is needed:\n"
            f"      python3 bin/books.py statements"
        )
    parsed, failures = statements_mod.parse_many(paths)
    unassigned = []
    for s in parsed:
        spec = account_for_file(os.path.basename(s.source_file), prof)
        if spec is not None:
            s.account_key = spec.book
            for line in s.lines:
                line.account_key = spec.book
        elif not s.account_key:
            unassigned.append(os.path.basename(s.source_file))
    if not quiet:
        say(n_of(len(parsed), len(paths), "statement files parsed"))
        for path, msg in failures:
            say(f"    could not read {os.path.basename(path)}: {msg}")
        if unassigned:
            say("    " + n_of(len(unassigned), len(parsed), "files match no account")
                + " declared in the profile: " + ", ".join(sorted(unassigned)[:5])
                + (" ..." if len(unassigned) > 5 else ""))
            say("    Declare the account and its mask in the profile, or those months "
                "read as missing.")
    return parsed, failures, unassigned


# ----------------------------------------------------- the For Review queue

_DATE_HEADERS = ("date", "transaction date", "posted date", "posting date")
_DESC_HEADERS = ("description", "descriptor", "memo", "payee", "name", "details")
_AMOUNT_HEADERS = ("amount", "value")
_IN_HEADERS = ("received", "money in", "deposit", "deposits", "credit")
_OUT_HEADERS = ("spent", "money out", "withdrawal", "withdrawals", "debit")


def _pick(headers, wanted):
    for want in wanted:
        for h in headers:
            if norm_text(h) == want:
                return h
    for want in wanted:
        for h in headers:
            if want in norm_text(h):
                return h
    return None


def read_queue_file(path, account_key):
    """One For Review export, as BankLines. Tolerant about column names.

    QuickBooks exports the queue with an Amount column on some views and a
    Spent/Received pair on others, and a bank's own CSV uses Debit/Credit. All
    three are read here rather than telling a founder their export is wrong.
    """
    rows = []
    if str(path).lower().endswith((".xlsx", ".xlsm")):
        from openpyxl import load_workbook
        wb = load_workbook(path, data_only=False)
        ws = wb.worksheets[0]
        grid = [[c.value for c in row] for row in ws.iter_rows()]
        wb.close()
        header_index = None
        for i, row in enumerate(grid[:20]):
            cells = [norm_text(c) for c in row if c is not None]
            if any(c in _DATE_HEADERS for c in cells):
                header_index = i
                break
        if header_index is None:
            raise Refusal(
                f"{os.path.basename(path)}: no header row with a Date column in the "
                f"first 20 rows.")
        headers = [str(c or "") for c in grid[header_index]]
        for row in grid[header_index + 1:]:
            rows.append({h: v for h, v in zip(headers, row)})
    else:
        with open(path, newline="", encoding="utf-8-sig", errors="replace") as fh:
            sample = fh.read(8192)
            fh.seek(0)
            try:
                dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
            except csv.Error:
                dialect = csv.excel
            rows = list(csv.DictReader(fh, dialect=dialect))
    if not rows:
        return []

    headers = [h for h in rows[0].keys() if h]
    c_date = _pick(headers, _DATE_HEADERS)
    c_desc = _pick(headers, _DESC_HEADERS)
    c_amount = _pick(headers, _AMOUNT_HEADERS)
    c_in = _pick(headers, _IN_HEADERS)
    c_out = _pick(headers, _OUT_HEADERS)
    if c_date is None or c_desc is None or (c_amount is None and not (c_in or c_out)):
        raise Refusal(
            f"{os.path.basename(path)}: could not find the columns.\n"
            f"  Found: {', '.join(str(h) for h in headers[:10])}\n"
            f"  It needs a date column, a description column, and either an Amount\n"
            f"  column or a Spent/Received (or Debit/Credit) pair."
        )

    out = []
    for i, row in enumerate(rows, start=2):
        date = try_date(row.get(c_date))
        if date is None:
            continue
        if c_amount is not None:
            amount = money(row.get(c_amount), f"{os.path.basename(path)} row {i} amount")
        else:
            got = money(row.get(c_in) if c_in else ZERO, "money in")
            gone = money(row.get(c_out) if c_out else ZERO, "money out")
            amount = money(abs(got) - abs(gone))
        if amount == ZERO:
            continue
        out.append(BankLine(
            date=date,
            descriptor=str(row.get(c_desc) or "").strip(),
            amount=amount,
            account_key=account_key,
            source_file=str(path),
            source_row=i,
            origin="for_review",
        ))
    return out


def load_queue(work, given, prof, quiet=False):
    d = resolve_dir(work, given, "for-review",
                    "your For Review queue, one export per account")
    paths = [str(p) for p in sorted(Path(d).rglob("*"))
             if p.is_file() and p.suffix.lower() in (".csv", ".xlsx", ".xlsm")]
    if not paths:
        raise Refusal(
            f"{d} holds no For Review exports.\n"
            f"  In QuickBooks: Banking, pick the account, For review, Export to Excel.\n"
            f"  Do it for every account and put the files there."
        )
    lines, unassigned = [], []
    for path in paths:
        spec = account_for_file(os.path.basename(path), prof)
        if spec is None:
            unassigned.append(os.path.basename(path))
        lines.extend(read_queue_file(path, spec.book if spec else ""))
    lines.sort(key=lambda l: (l.date, l.account_key, l.source_row))
    if not quiet:
        say(f"{len(lines):,} queue rows read from "
            + n_of(len(paths), len(paths), "files"))
        if unassigned:
            say("    " + n_of(len(unassigned), len(paths), "files")
                + " match no declared account: " + ", ".join(unassigned[:5]))
            say("    Rows from those files carry no account, so no transfer between "
                "the company's own accounts can be spotted on them.")
    return lines, paths, unassigned


# ----------------------------------------------------------------- batches

BATCH_FILE_RE = re.compile(r"^batch-([A-Za-z0-9_]+)\.xlsx$", re.I)


def normalize_batch(text):
    """`batch-01`, `01` and `Batch-01` all mean the same batch."""
    t = str(text or "").strip()
    tag = parse_batch_tag(t) or parse_batch_tag(f"batch-{t}")
    if not tag:
        raise Refusal(
            f"{text!r} is not a batch name. A batch is `batch-` and then letters,\n"
            f"  digits or underscores, for example batch-01."
        )
    return tag


def batch_workbook(work, tag):
    return work.dir("review", create=True) / f"batch-{tag}.xlsx"


def known_batches(work):
    review = work / "review"
    if not review.is_dir():
        return []
    return [m.group(1).lower() for m in
            (BATCH_FILE_RE.match(p.name) for p in sorted(review.glob("*.xlsx"))) if m]


def approval_state(work, tag):
    """(approved, message). Never raises: for reporting, not for gating."""
    try:
        ap = approval_check(batch_workbook(work, tag), workdir=work.root)
        return True, f"approved by {ap.approved_by} at {ap.approved_at}"
    except ApprovalError as exc:
        return False, str(exc)


def gated_write(work, target, writer):
    """Write into import/ only under a live approval. The one gate in this file.

    Every path into import/ goes through here, fill-gaps included, so that
    adding a writer later cannot accidentally add a way around the approval.
    """
    target = Path(target)
    try:
        approval_check(target, workdir=work.root)
    except ApprovalError as exc:
        raise Refusal(str(exc)) from exc
    target.parent.mkdir(parents=True, exist_ok=True)
    return writer(target)


# -------------------------------------------------------------------- misc

def whoami(given=""):
    if given:
        return str(given).strip()
    return (os.environ.get("BOOKS_APPROVER")
            or os.environ.get("USER")
            or os.environ.get("USERNAME")
            or "the owner")


def today_iso():
    return dt.date.today().isoformat()


def read_json(path, default=None):
    p = Path(path)
    if not p.exists():
        return default
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise Refusal(f"{p} is not valid JSON: {exc}")


def write_json(path, data):
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    return p


def write_text(path, text):
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")
    return p


# ==================================================== the year being filed

# Facts no export can carry. `intake` writes them, every later command reads
# them, and each one is stored with the date it was given.
LIVE_FILE = "answers/live-screen.json"

SCOPE_FILE = "reports/scope.json"
DEFERRED_FILE = "reports/deferred.md"
COMPLETENESS_FILE = "reports/completeness.json"

FEED_STATES = ("live", "stopped", "never")


def load_live(work):
    data = read_json(work / LIVE_FILE, {}) or {}
    data.setdefault("accounts", {})
    data.setdefault("rules", {})
    data.setdefault("people", {})
    return data


def save_live(work, data):
    return write_json(work / LIVE_FILE, data)


def live_account(live, key):
    return (live.get("accounts") or {}).get(str(key), {})


def record_live(work, live, path, value, by=""):
    """Store one live-screen fact with the date it was given and who gave it."""
    node = live
    parts = list(path)
    for part in parts[:-1]:
        node = node.setdefault(part, {})
    node[parts[-1]] = value
    node[parts[-1] + "_on"] = today_iso()
    node[parts[-1] + "_by"] = whoami(by)
    save_live(work, live)
    return live


def filing_year_of(live):
    """The year being filed, as an int, or None when nobody has said.

    It is read from the live-screen file and never inferred from an export.
    `learn` fills `entity.fiscal_year` from the period the exports cover, which
    is a fact about the download and not a decision about what is being filed.
    Reading that here would answer the question without anyone being asked it.
    """
    m = re.search(r"(?:19|20)\d{2}", str((live.get("filing") or {}).get("year") or ""))
    return int(m.group(0)) if m else None


def filing_due(live, prof=None):
    due = str((live.get("filing") or {}).get("due") or "").strip()
    if due:
        return due
    return str(getattr(getattr(prof, "entity", None), "deadline", "") or "").strip()


def filing_bounds(year):
    return dt.date(year, 1, 1), dt.date(year, 12, 31)


def days_to(due):
    d = try_date(due)
    return None if d is None else (d - dt.date.today()).days


def require_filing_year(live):
    """Every count is a count of something. Say which year, or the counts lie."""
    year = filing_year_of(live)
    if year is not None:
        return year
    raise Refusal(
        "no filing year has been set, so a count of the queue would be a count of\n"
        "  everything in it rather than a count of what this deadline needs.\n"
        "  A queue holds next year's transactions and re-downloads of months that\n"
        "  already reconciled. Working those is work done twice, and booking a\n"
        "  re-download double-counts a period that already tied out.\n"
        "  Say which year is being filed and when it is due:\n"
        "      python3 bin/books.py filing-year 2025 --due 2026-10-15"
    )


def materiality_of(prof):
    raw = str(getattr(prof.entity, "materiality", "") or "").strip()
    if not raw:
        return ZERO
    try:
        return abs(money(raw))
    except Exception:
        return ZERO


def balance_words(signed, kind="bank"):
    """A balance with its side in words. Never a bare negative number."""
    a = money(signed)
    body = f"{abs(a):,.2f}"
    if a == ZERO:
        return "0.00"
    if str(kind).lower() == "card":
        return f"{body} owed" if a < ZERO else f"{body} in credit"
    return body if a > ZERO else f"{body} overdrawn"


def declared_accounts(prof):
    return [a for a in (prof.accounts or []) if a.book]


def account_label(prof, key):
    spec = prof.account_spec(key)
    if spec is None or not spec.label:
        return str(key)
    label = str(spec.label).strip()
    return label if label.startswith(str(key)) else f"{key} {label}"


# ============================================ the four analyses this rests on
#
# Four modules in lib/closethebooks do the measuring. This file adapts each one
# into the shape the commands print, and holds nothing that decides an answer.
#
#   filing_year.scope(queue, year, reconciled_through=, history_of=)
#       partitions the queue into in scope, already booked, postdates, predates
#   feed_gaps.scan(ledger, queue=, profile=, bank_balances=)
#       the accounts whose feed cannot be a complete record of the period
#   twins.scan(ledger, queue=, profile=, recon=)
#       two chart rows that are one real account, and history_map() for scope
#   autoposted.scan(ledger, filing_years=, audit_log=, attachments=)
#       entries that arrived with nothing to say a person made them
#
# Where a module cannot run on what it was given it says so and measures
# nothing, and every command below prints that rather than a number.


class Deferred:
    """One queue row this deadline does not need, and the reason it waits."""

    def __init__(self, line, reason, detail):
        self.line, self.reason, self.detail = line, reason, detail


DEFER_REASONS = {
    "next_year": "dated after the year being filed",
    "earlier_year": "dated before the year being filed",
    "reconciled_period": "dated inside a month that already reconciled",
    "undated": "carrying no date that can be read",
}

# filing_year's partition names, in the words this file prints.
PARTITION_REASON = {
    filing_year_mod.ALREADY_BOOKED: "reconciled_period",
    filing_year_mod.POSTDATES: "next_year",
    filing_year_mod.PREDATES: "earlier_year",
}


class ScopeReport:
    def __init__(self, filing_year, in_scope, deferred, total, scoped=None):
        self.filing_year = filing_year
        self.in_scope = in_scope
        self.deferred = deferred
        self.total = total
        self.scoped = scoped          # the library's own report, for its detail
        self.counts = {}
        for d in deferred:
            self.counts[d.reason] = self.counts.get(d.reason, 0) + 1

    def by_account(self):
        out = {}
        for line in self.in_scope:
            node = out.setdefault(line.account_key or "(no account)",
                                  {"in_scope": 0, "deferred": 0})
            node["in_scope"] += 1
        for d in self.deferred:
            node = out.setdefault(d.line.account_key or "(no account)",
                                  {"in_scope": 0, "deferred": 0})
            node["deferred"] += 1
        return out


def analyze_scope(lines, *, filing_year, reconciled_through, history_of=None):
    """Split the queue into what this deadline needs and what waits."""
    scoped = filing_year_mod.scope(lines, filing_year,
                                   reconciled_through=reconciled_through or {},
                                   history_of=history_of or {})
    deferred = []
    for name, reason in PARTITION_REASON.items():
        part = scoped.part(name)
        for item in part.items:
            deferred.append(Deferred(item, reason, part.reason))
    for item in scoped.undated:
        deferred.append(Deferred(item, "undated",
                                 "no date on the row could be read, so it was "
                                 "partitioned nowhere"))
    return ScopeReport(filing_year, list(scoped.in_scope), deferred,
                       scoped.total, scoped=scoped)


def reconciled_through_map(prof, live):
    out = {}
    for spec in declared_accounts(prof):
        d = try_date(live_account(live, spec.book).get("reconciled_through"))
        if d is not None:
            out[spec.book] = d
    return out


def history_map(ledger, prof, queue_lines):
    """Which account's reconciled history governs each account.

    On a duplicated account the feed and the queue sit on one row and the
    reconciled months on the other, so without this every item in a period that
    WAS reconciled reads as work to do.
    """
    if ledger is None:
        return {}
    out = twins.scan(ledger, queue=queue_lines, profile=prof).history_map()
    counts = {}
    for line in queue_lines:
        if line.account_key:
            counts[line.account_key] = counts.get(line.account_key, 0) + 1
    for pair in _mask_pairs(ledger, prof, counts):
        out.setdefault(pair.duplicate, pair.keep)
    return out


class CompletenessFinding:
    def __init__(self, account, label, kind):
        self.account, self.label, self.kind = account, label, kind
        self.book = None
        self.bank = None
        self.as_of = None
        self.bank_source = ""
        self.queue_net = ZERO
        self.queue_rows = 0
        self.after = None
        self.difference = None
        self.moves_away = False
        self.settled = False
        self.evidence = ""
        self.why = ""

    @property
    def open(self):
        return not self.settled and bool(self.why)

    def to_json(self):
        return {
            "account": self.account, "label": self.label, "kind": self.kind,
            "book": None if self.book is None else plain(self.book),
            "bank": None if self.bank is None else plain(self.bank),
            "as_of": iso(self.as_of) if self.as_of else "",
            "bank_source": self.bank_source,
            "queue_net": plain(self.queue_net), "queue_rows": self.queue_rows,
            "after": None if self.after is None else plain(self.after),
            "difference": None if self.difference is None else plain(self.difference),
            "moves_away": self.moves_away, "settled": self.settled,
            "evidence": self.evidence, "why": self.why, "open": self.open,
        }


def _bank_balance_for(spec, live, statements, period_end):
    """What the bank itself says, from the live screen or from a statement."""
    node = live_account(live, spec.book)
    if node.get("bank_balance") not in (None, ""):
        return (money(node["bank_balance"]),
                try_date(node.get("bank_balance_as_of")) or period_end,
                "read off the bank's own screen on "
                + str(node.get("bank_balance_on") or "an unrecorded date"))
    best = None
    for s in statements:
        if s.account_key != spec.book or s.closing_balance is None:
            continue
        if best is None or (s.period_end and best.period_end
                            and s.period_end > best.period_end):
            best = s
    if best is not None:
        return (best.closing_balance, best.period_end,
                f"the closing balance on {os.path.basename(best.source_file)}")
    return None, None, ""


def analyze_completeness(prof, ledger, queue_lines, statements, live, *,
                         filing_year, materiality):
    """Books plus what is unbooked, against what the bank says it holds.

    `feed_gaps.scan` does the detection. The per-account walk is repeated here
    because the report has to cover EVERY declared account, including the ones
    the scan found nothing wrong with and the ones nobody has given a bank
    balance for. A report that lists only the problems hides every account
    nobody checked.
    """
    _, period_end = filing_bounds(filing_year)
    settled = (live.get("completeness_settled") or {})

    banks, as_ofs, sources = {}, {}, {}
    for spec in declared_accounts(prof):
        value, when, source = _bank_balance_for(spec, live, statements, period_end)
        as_ofs[spec.book], sources[spec.book] = when, source
        if value is not None:
            banks[spec.book] = value

    scan = feed_gaps.scan(ledger, queue=queue_lines, profile=prof,
                          bank_balances=banks)

    by_account = {}
    for line in queue_lines:
        node = by_account.setdefault(line.account_key or "", {"net": ZERO, "rows": 0})
        node["net"] += line.amount
        node["rows"] += 1

    findings = []
    for spec in declared_accounts(prof):
        f = CompletenessFinding(spec.book, account_label(prof, spec.book), spec.kind)
        q = by_account.get(spec.book, {"net": ZERO, "rows": 0})
        f.queue_net, f.queue_rows = money(q["net"]), q["rows"]
        f.bank = banks.get(spec.book)
        f.as_of, f.bank_source = as_ofs.get(spec.book), sources.get(spec.book, "")
        f.book = ledger.balance_as_of(spec.book, f.as_of or period_end)
        note = settled.get(spec.book) or {}
        f.settled = bool(note.get("evidence"))
        f.evidence = str(note.get("evidence") or "")

        gap = scan.get(spec.book)
        f.signals = list(gap.signals) if gap is not None else []

        if f.bank is None:
            f.why = ("nothing says what the bank holds, so whether the books are "
                     "complete cannot be checked at all")
        elif f.book is None:
            f.why = ("no export establishes this account's opening position, so its "
                     "book balance cannot be determined")
        else:
            f.after = money(f.book + f.queue_net)
            f.difference = money(f.bank - f.after)
            f.moves_away = abs(f.difference) > abs(money(f.bank - f.book))
            if abs(f.difference) > materiality:
                f.why = (f"{fmt(abs(f.difference))} of activity is in neither the "
                         f"books nor the queue")
                if f.moves_away:
                    f.why += (", and working the queue moves this account further "
                              "from the bank rather than toward it")
        if not f.why and f.signals:
            f.why = "the feed shows " + f.signals[0]
        findings.append(f)
    return findings


class DuplicatePair:
    def __init__(self, keep, duplicate, why):
        self.keep, self.duplicate, self.why = keep, duplicate, why
        self.queue_items = 0
        self.opening_plug = None
        self.obe = None
        self.plug_matches_obe = False
        self.detail = []


def _mask_pairs(ledger, prof, queue_counts):
    """Accounts the profile says answer to the same card or account number.

    `twins.scan` matches on the name, which is what a re-link produces. It
    cannot see a pair whose two rows were named differently, and the mask is
    the one token that says these are the same real-world account whatever the
    chart calls them. So both run, and neither is trusted to find the other's.
    """
    groups = {}
    for spec in declared_accounts(prof):
        if spec.mask:
            groups.setdefault(str(spec.mask).strip(), []).append(spec)
    pairs = []
    for mask, specs in sorted(groups.items()):
        if len(specs) < 2:
            continue
        def history(spec):
            acct = ledger.account(spec.book) if ledger is not None else None
            return len(ledger.aliases_for(spec.book)) if acct is None else sum(
                1 for line in ledger.lines
                if norm_text(line.account) in ledger.aliases_for(spec.book)
                or norm_text(line.account_full) in ledger.aliases_for(spec.book))
        ranked = sorted(specs, key=lambda sp: (-history(sp), not str(sp.book).isdigit()))
        keep = ranked[0]
        for dup in ranked[1:]:
            pair = DuplicatePair(keep.book, dup.book,
                                 f"both answer to {mask} in the profile")
            pair.queue_items = int((queue_counts or {}).get(dup.book, 0))
            if ledger is not None:
                pair.opening_plug = ledger.opening_of(dup.book)
            pair.detail = [f"{account_label(prof, keep.book)}: "
                           f"{history(keep)} posted line(s), feed {keep.feed}",
                           f"{account_label(prof, dup.book)}: "
                           f"{history(dup)} posted line(s), feed {dup.feed}"]
            pairs.append(pair)
    return pairs


def analyze_duplicates(ledger, prof, queue_lines, queue_counts):
    """Two chart rows that the evidence says are one real-world account."""
    pairs, seen = [], set()
    for twin in twins.scan(ledger, queue=queue_lines, profile=prof):
        why = "; ".join(name for name, _ in twin.signals) or \
            "the same account under two names"
        pair = DuplicatePair(twin.original.key, twin.duplicate.key, why)
        pair.queue_items = max(int((queue_counts or {}).get(twin.duplicate.key, 0)),
                               int(twin.duplicate.queue_items or 0))
        pair.opening_plug = twin.plug
        pair.obe = twin.obe_balance
        pair.plug_matches_obe = twin.plug_offsets_obe
        pair.detail = [twin.original.describe(), twin.duplicate.describe()]
        pairs.append(pair)
        seen.add(frozenset((twin.original.key, twin.duplicate.key)))
    for pair in _mask_pairs(ledger, prof, queue_counts):
        if frozenset((pair.keep, pair.duplicate)) not in seen:
            pairs.append(pair)
    return pairs


class AutomationReport:
    def __init__(self):
        self.rules = None
        self.auto_add = None
        self.last_human = None
        self.last_human_basis = ""
        self.posted_after = 0
        self.largest = None
        self.total = ZERO
        self.open = True
        self.why = ""


def analyze_automation(ledger, live, *, filing_year):
    """Whether anything is still posting into the year being filed.

    Two halves, and they answer different questions. The rules count is a fact
    read off the screen and it is what the gate turns on: rules that auto-add
    are still posting right now. `autoposted.scan` is the evidence of what they
    have already done, which is what makes the refusal worth reading.
    """
    r = AutomationReport()
    rules = (live.get("rules") or {})
    r.rules = rules.get("count")
    r.auto_add = rules.get("auto_add")
    r.last_human = try_date((live.get("people") or {}).get("last_human_date"))
    r.last_human_basis = ("the date you read off the audit log" if r.last_human
                          else "")

    if ledger is not None:
        scan = autoposted.scan(ledger, filing_years=[filing_year])
        found = list(scan.in_filing_year)
        r.posted_after = len(found)
        r.total = scan.total_in_filing_year
        if found:
            r.largest = max(found, key=lambda e: abs(money(e.amount)))
        if r.last_human is None and scan.human_stopped is not None:
            r.last_human = scan.human_stopped
            r.last_human_basis = scan.human_stopped_basis or (
                "the last month the books hold more than one entry")

    if r.rules is None or r.auto_add is None:
        r.open = True
        r.why = ("nobody has said how many bank rules are on, or how many of them "
                 "post without being seen")
    elif int(r.auto_add) > 0:
        r.open = True
        r.why = (f"{int(r.auto_add)} of {int(r.rules)} bank rules post without "
                 f"anyone seeing them")
    else:
        r.open = False
        r.why = ""
    return r


# ============================================================== the gates
#
# Three refusals, built the way the approval gate is built: each names the one
# reason it fired and the exact command that clears it, exits 2, and never
# reads as a crash. None of them can be passed with a flag, because the whole
# value of each is that it cannot be waved through in a hurry.

def gate_queue_loaded(prof, live, counted, key, action="merge"):
    """Nothing about disconnecting or merging an account while its queue holds items.

    This is the one that prevents irreversible loss. QuickBooks will not merge
    two accounts while either is connected to a feed, and its documented way
    round that is to disconnect first. Disconnecting deletes everything in the
    Pending and For Review tabs, and for an account whose activity was never
    booked, that queue is the only record of it anywhere in the file.

    Two sources, and both have to read zero. The screen is more current than an
    export, and an export is proof the rows existed. Neither on its own is
    enough to risk an action that cannot be undone.
    """
    doing = ("disconnecting or merging" if action == "merge" else "disconnecting")
    stated = live_account(live, key).get("for_review_count")
    rows = int(counted or 0)
    if stated in (None, ""):
        raise Refusal(
            f"nobody has said what is sitting in {account_label(prof, key)}'s For\n"
            f"  Review queue, and {doing} it cannot be undone.\n"
            f"\n"
            f"  QuickBooks will not merge two accounts while either is connected to a\n"
            f"  feed, and the way round that is to disconnect first. Disconnecting\n"
            f"  DELETES every item in the Pending and For Review tabs, and for an\n"
            f"  account whose activity was never booked, that queue is the only record\n"
            f"  of it anywhere in the file.\n"
            f"\n"
            f"  Open Transactions, Bank transactions, that account, For review, and\n"
            f"  read the count off the screen:\n"
            f"      python3 bin/books.py intake --account {shell_arg(key)} --for-review N"
        )
    said = int(money(stated))
    if said <= 0 and rows <= 0:
        return 0

    warning = (
        f"  QuickBooks will not merge two accounts while either is connected to a\n"
        f"  feed, and the documented way round that is to disconnect the feed first.\n"
        f"  Disconnecting DELETES every item in the Pending and For Review tabs. For\n"
        f"  an account whose activity was never booked, those items are the only\n"
        f"  record of it anywhere in the file: they are not in the general ledger,\n"
        f"  they are not on the other account, and nothing exports them once they\n"
        f"  are gone.\n"
    )
    if said > 0:
        raise Refusal(
            "\n".join(_wrapped(
                f"{account_label(prof, key)} has {said:,} items in its For Review "
                f"queue, so nothing about {doing} it goes ahead.", 76))
            + f"\n\n" + warning + f"\n"
            f"  Book them, then dispose of what is left, then disconnect, then merge.\n"
            f"  That order, and no other.\n"
            f"      python3 bin/books.py catchup --batch-size 150\n"
            f"      python3 bin/books.py approve batch-01\n"
            f"      python3 bin/books.py build-imports --batch batch-01\n"
            f"\n"
            f"  When the tab reads zero on screen, record it and run this again:\n"
            f"      python3 bin/books.py intake --account {shell_arg(key)} --for-review 0"
        )
    raise Refusal(
        "\n".join(_wrapped(
            f"the screen says {account_label(prof, key)}'s For Review queue is empty "
            f"and the export in for-review/ still holds {rows:,} rows, so nothing "
            f"about {doing} it goes ahead.", 76))
        + f"\n\n" + warning + f"\n"
        f"  One of the two is out of date, and an export taken before the work is not\n"
        f"  evidence the work is done. Re-export For review for this account into\n"
        f"  for-review/, or remove the stale file, then run this again."
    )


def gate_completeness(work, prof, findings):
    """The queue cannot be called finished while a feed-completeness finding stands."""
    open_findings = [f for f in findings if f.open]
    if not open_findings:
        return
    # Lead with a finding that has numbers behind it. An account nobody has
    # given a bank balance for is also open, but it names a different next step
    # and reading it first hides the one that was actually measured.
    measured = [f for f in open_findings if f.difference is not None]
    f = (measured or open_findings)[0]

    lines = _wrapped(
        "the queue cannot be called finished: "
        + n_of(len(open_findings), len(findings), "accounts")
        + " have an open feed-completeness finding.", 76) + [""]
    lines += ["  " + w for w in _wrapped(f"{f.label}: {f.why}.", 74)]
    if f.bank is not None and f.book is not None:
        lines += ["  " + w for w in _wrapped(
            f"The books say {balance_words(f.book, f.kind)}, the bank says "
            f"{balance_words(f.bank, f.kind)}, and the {f.queue_rows:,} unbooked "
            f"items net to {fmt(f.queue_net)}.", 74)]
    lines += [
        "",
        "  Finishing the queue and the period being complete are different things,",
        "  and the difference is invisible without this check. An account whose feed",
        "  stopped has activity in neither the books nor the queue, so working every",
        "  item and filing would file on incomplete books.",
        "",
        "  See the whole picture, account by account:",
        "      python3 bin/books.py completeness",
        "",
    ]
    missing_bank = [g for g in open_findings if g.bank is None]
    if missing_bank:
        lines += [
            "  " + n_of(len(missing_bank), len(findings), "accounts")
            + " have no bank balance recorded, so completeness on them was not",
            "  checked at all. Read each one off the bank's own screen:",
            f"      python3 bin/books.py intake --account "
            f"{shell_arg(missing_bank[0].account)} --bank-balance 0.00 "
            f"--as-of YYYY-MM-DD",
            "",
        ]
    if measured:
        lines += [
            "  Fill the hole from statements:",
            f"      python3 bin/books.py fill-gaps --account {shell_arg(f.account)}",
            "",
            "  If a document explains the difference, record it with the document "
            "named:",
            f"      python3 bin/books.py completeness --settle {shell_arg(f.account)} "
            f"\\",
            f"          --evidence \"which statement, which page, which line\"",
        ]
    raise Refusal("\n".join(lines))


def gate_automation(work, prof, ledger, live, year, what):
    """Nothing that produces figures for a return while automation is still posting."""
    report = analyze_automation(ledger, live, filing_year=year)
    if not report.open:
        return report
    lines = _wrapped(
        f"{what} produces figures for the {year} return, and {report.why}.", 76) + [
        "",
        "  A rule with auto-add on posts without anyone seeing it, and it posts into",
        f"  the year being filed. Any figure produced now is a figure about a file",
        "  that is still changing under it, so every hour worked before the rules",
        "  stop is worked twice.",
    ]
    if report.last_human is not None:
        lines.append("")
        lines += ["  " + w for w in _wrapped(
            f"{iso(report.last_human)} is the last day a person worked in this file, "
            f"from {report.last_human_basis or 'what was recorded'}.", 74)]
    if report.posted_after:
        lines += ["  " + w for w in _wrapped(
            f"{report.posted_after:,} entries dated in {year} carry nothing to say a "
            f"person made them, {fmt(report.total)} in total.", 74)]
        if report.largest is not None:
            e = report.largest
            lines += ["  " + w for w in _wrapped(
                f"The largest is {fmt(e.amount)} to "
                f"{e.category_account or 'an expense account'} on {iso(e.date)}, "
                f"which is a deduction inside the year on the return.", 74)]
    lines.append("")
    lines.append("  An export cannot say who posted a line. The audit log can.")
    lines += [
        "",
        "  In QuickBooks: the gear, Rules. Turn off \"Automatically confirm",
        "  transactions this rule applies to\" on every rule, or delete the rules.",
        "  Export them first so you can put them back: Rules, then Export rules.",
        "",
        "  Then record what is left on the screen:",
        "      python3 bin/books.py intake --rules 15 --auto-add 0",
    ]
    raise Refusal("\n".join(lines))


# =========================================================== filing-year

def cmd_filing_year(args, work):
    prof = load_profile(work, args.profile)
    live = load_live(work)
    changed = []
    if args.year:
        m = re.fullmatch(r"(?:19|20)\d{2}", str(args.year).strip())
        if not m:
            raise Refusal(
                f"{args.year!r} is not a year. Give the four digits of the year being "
                f"filed, for example 2025.")
        prof.entity.fiscal_year = m.group(0)
        record_live(work, live, ("filing", "year"), m.group(0), args.by)
        changed.append("the year being filed")
    if args.due:
        parse_date(args.due, field="--due")
        prof.entity.deadline = str(args.due).strip()
        record_live(work, live, ("filing", "due"), str(args.due).strip(), args.by)
        changed.append("the date it is due")
    if changed:
        save_profile(prof)
        log = work.dir("answers", create=True) / "answers.jsonl"
        with open(log, "a", encoding="utf-8") as fh:
            fh.write(json.dumps({
                "id": "filing-year", "answer": f"{prof.entity.fiscal_year} "
                f"due {prof.entity.deadline}", "on": today_iso(),
                "by": whoami(args.by),
                "recorded_at": dt.datetime.now().astimezone().isoformat(
                    timespec="seconds"),
            }) + "\n")

    year = filing_year_of(live)
    if year is None:
        raise Refusal(
            "no filing year has been set yet.\n"
            "  Say which year is being filed and when it is due:\n"
            "      python3 bin/books.py filing-year 2025 --due 2026-10-15"
        )
    start, end = filing_bounds(year)
    left = days_to(filing_due(live, prof))

    head(f"Filing {year}")
    bullet("the year being filed", f"{iso(start)} to {iso(end)}")
    bullet("due", filing_due(live, prof) or "not set, and it decides what gets cut")
    if left is not None:
        bullet("days left", f"{left:,}" if left >= 0 else f"{abs(left):,} days ago")
    bullet("basis", prof.entity.basis or "not answered")
    bullet("what these books are for", prof.entity.end_use or "not answered")
    bullet("materiality", prof.entity.materiality or "not answered, so report everything")

    say("")
    say(f"Every count from here on is a count of {year}. A transaction dated {year + 1}")
    say(f"belongs to the {year + 1} return and is set aside rather than hidden, and so")
    say("is anything dated inside a month that already reconciled: booking one of those")
    say("double-counts a period that already tied out.")
    say("")
    say("What that leaves you to do for this deadline:")
    say("      python3 bin/books.py scope")
    return OK


# =============================================================== intake

# Five screens. Four of these decide what a catch-up even consists of, and not
# one of them is in any export QuickBooks produces.
INTAKE_SCREENS = (
    ("the bank's own balance", "per account",
     "your bank or card issuer's own site or app, the account's current balance",
     "It is the only figure that says whether the books are complete. QuickBooks "
     "knows only what it was told, so a feed that stopped leaves books that look "
     "tidy and are wrong by everything it missed.",
     "books.py intake --account 101000 --bank-balance 37.14 --as-of 2025-12-31"),
    ("the For Review count", "per account",
     "QuickBooks: Transactions, Bank transactions, pick the account, For review",
     "It is the size of the job, and it decides whether disconnecting that account "
     "would destroy anything. Export the list instead of counting it where you can: "
     "For review, then Export to Excel, and put the file in for-review/.",
     "books.py intake --account 101000 --for-review 241"),
    ("the feed", "per account",
     "the same screen, the account tile, the line under the balance saying when it "
     "last updated",
     "A feed that stopped leaves a hole that no export shows. Everything inside it "
     "is in neither the books nor the queue.",
     "books.py intake --account 101000 --feed stopped --feed-last 2025-03-14"),
    ("reconciled through", "per account",
     "QuickBooks: Transactions, Reconcile, then History by account",
     "Items dated inside a month that already reconciled are re-downloads. Booking "
     "them double-counts a period that already tied out.",
     "books.py intake --account 101000 --reconciled-through 2025-08-31"),
    ("the rules", "once",
     "QuickBooks: the gear, then Rules",
     "A rule with auto-add on posts into the books without anyone seeing it, "
     "including into the year being filed. Export them rather than counting: Rules, "
     "then Export rules.",
     "books.py intake --rules 15 --auto-add 15"),
    ("who worked in the file last", "once",
     "QuickBooks: the gear, Audit log, all users, and read the last human entry",
     "Everything posted after that date came from a rule or a sync rather than a "
     "person, and no export says who posted anything.",
     "books.py intake --last-human 2026-02-28 --note \"the bookkeeper left\""),
)


def intake_missing(prof, live):
    """What the live screen still has not told us, in the order it blocks work."""
    out = []
    rules = live.get("rules") or {}
    if rules.get("count") is None or rules.get("auto_add") is None:
        out.append(("the rules", "once",
                    "blocks every command that produces figures for the return"))
    for spec in declared_accounts(prof):
        node = live_account(live, spec.book)
        label = account_label(prof, spec.book)
        if node.get("for_review_count") in (None, ""):
            out.append((f"{label}: the For Review count", "per account",
                        "blocks any merge or disconnect on this account"))
        if node.get("bank_balance") in (None, ""):
            out.append((f"{label}: the bank's own balance", "per account",
                        "without it, completeness on this account cannot be checked"))
        if not node.get("feed_state"):
            out.append((f"{label}: the feed state", "per account",
                        "a stopped feed is a hole in the books"))
        if not node.get("reconciled_through"):
            out.append((f"{label}: reconciled through", "per account",
                        "without it, a re-download reads as work to do"))
    if not (live.get("people") or {}).get("last_human_date"):
        out.append(("who worked in the file last", "once",
                    "decides what counts as posted by a machine"))
    return out


def cmd_intake(args, work):
    prof = load_profile(work, args.profile)
    live = load_live(work)
    recorded = []

    if args.rules is not None:
        record_live(work, live, ("rules", "count"), int(args.rules), args.by)
        recorded.append(f"{int(args.rules)} bank rules")
    if args.auto_add is not None:
        record_live(work, live, ("rules", "auto_add"), int(args.auto_add), args.by)
        recorded.append(f"{int(args.auto_add)} of them post without being seen")
    if args.rules_file:
        p = Path(args.rules_file)
        if not p.exists():
            raise Refusal(f"there is no rules file at {p}.")
        record_live(work, live, ("rules", "file"), str(p), args.by)
        recorded.append(f"the exported rules file {p.name}")
    if args.last_human:
        parse_date(args.last_human, field="--last-human")
        record_live(work, live, ("people", "last_human_date"),
                    str(args.last_human).strip(), args.by)
        recorded.append(f"a person last worked in the file on {args.last_human}")
    if args.note:
        record_live(work, live, ("people", "note"), args.note, args.by)

    if args.account:
        spec = prof.account_spec(args.account) or account_for_file(args.account, prof)
        key = spec.book if spec is not None else str(args.account).strip()
        if prof.account_spec(key) is None:
            raise Refusal(
                f"there is no account {args.account!r} in the profile.\n"
                f"  Declared: " + (", ".join(a.book for a in declared_accounts(prof))
                                   or "none") + "\n"
                f"  Declare it in the profile first, or `books.py learn` again.")
        node = live.setdefault("accounts", {}).setdefault(key, {})
        if args.bank_balance is not None:
            money(args.bank_balance, "--bank-balance")
            record_live(work, live, ("accounts", key, "bank_balance"),
                        plain(money(args.bank_balance)), args.by)
            recorded.append(f"{key}: the bank says "
                            f"{balance_words(money(args.bank_balance), spec.kind if spec else 'bank')}")
        if args.as_of:
            parse_date(args.as_of, field="--as-of")
            record_live(work, live, ("accounts", key, "bank_balance_as_of"),
                        str(args.as_of).strip(), args.by)
        if args.for_review is not None:
            record_live(work, live, ("accounts", key, "for_review_count"),
                        int(args.for_review), args.by)
            recorded.append(f"{key}: {int(args.for_review):,} items in For Review")
        if args.feed:
            record_live(work, live, ("accounts", key, "feed_state"), args.feed, args.by)
            recorded.append(f"{key}: the feed is {args.feed}")
        if args.feed_last:
            parse_date(args.feed_last, field="--feed-last")
            record_live(work, live, ("accounts", key, "feed_last"),
                        str(args.feed_last).strip(), args.by)
        if args.reconciled_through:
            parse_date(args.reconciled_through, field="--reconciled-through")
            record_live(work, live, ("accounts", key, "reconciled_through"),
                        str(args.reconciled_through).strip(), args.by)
            recorded.append(f"{key}: reconciled through {args.reconciled_through}")
        node.setdefault("label", account_label(prof, key))
        save_live(work, live)

    if recorded:
        head("Recorded")
        for r in recorded:
            say(f"  {r}")
        say("")
        say(f"Stored in {work.rel(work / LIVE_FILE)}, each with the date it was given.")

    missing = intake_missing(prof, live)
    if not missing:
        head("Every live-screen fact is recorded")
        say("Nothing below is blocked on something only the screen knows.")
        say("")
        say("      python3 bin/books.py scope")
        return OK

    keys = [a.book for a in declared_accounts(prof)]
    example = shell_arg(next((k for k in keys if str(k).isdigit()),
                             keys[0] if keys else "101000"))
    head("Five screens, and what to read off each")
    for name, scope, where, why, how in INTAKE_SCREENS:
        say(f"  {name} ({scope})")
        say_lines(_labelled("      where:  ", where, 66))
        say_lines(_labelled("      why:    ", why, 66))
        say(f"      say it: python3 bin/{how.replace('101000', example)}")
        say("")

    head("Still missing")
    table([[what, scope, why] for what, scope, why in missing[:args.limit]],
          ["fact", "scope", "what it blocks"])
    if len(missing) > args.limit:
        say(f"  and {len(missing) - args.limit:,} more. "
            f"Run this again after each answer.")
    say("")
    say(n_of(len(missing), len(missing), "facts are still missing") + ". Every command "
        "below says what it could not do without them, rather than guessing.")
    say("Come back to this as often as you like: it keeps what you have already given.")
    return OK


def shell_arg(value):
    """A value a person can paste into their own terminal without it breaking."""
    v = str(value)
    if re.fullmatch(r"[A-Za-z0-9._/-]+", v):
        return v
    return '"' + v.replace('"', '\\"') + '"'


def _labelled(label, text, width):
    """A label once, then the rest of the text indented under it."""
    pad = " " * len(label)
    return [(label if i == 0 else pad) + line
            for i, line in enumerate(_wrapped(text, width))]


def _wrapped(text, width):
    out, line = [], ""
    for word in str(text).split():
        if line and len(line) + 1 + len(word) > width:
            out.append(line)
            line = word
        else:
            line = f"{line} {word}".strip()
    if line:
        out.append(line)
    return out


# =============================================================== browser mode
#
# The other commands read a file and write a file, and the founder carries the
# result into QuickBooks. These drive QuickBooks directly, in a browser the
# founder is already signed in to, with the founder watching.
#
# Nothing in this file opens a connection. The agent does the clicking with its
# own browser tools; these commands decide what it may attempt, hold the
# approval it needs, and check afterwards that the books moved by exactly the
# amount that was approved. `lib/closethebooks/browser.py` carries the rules and
# the reason behind each one.

BROWSER_FILE = "answers/browser.json"
BROWSER_READS = "reports/browser-reads"

# What a plan file for each kind is called, so a person reading `review/` can
# tell a categorization batch from an entry batch without opening either.
KIND_PREFIX = {"categorize": "c", "add": "g", "journal": "j"}


def browser_state(work):
    return read_json(work / BROWSER_FILE, {}) or {}


def confirmed_company(work):
    """The company this working directory was confirmed against, or ''."""
    return str((browser_state(work).get("company") or {}).get("observed") or "")


def require_company(work, prof=None):
    """Refuse unless somebody has confirmed which company file this is."""
    seen = confirmed_company(work)
    if not seen:
        raise Refusal(
            "the company has not been confirmed, so nothing in browser mode "
            "runs.\n"
            "  QuickBooks keeps several companies behind one login and switches "
            "between them from a menu, so the file on screen is a fact somebody "
            "has to check rather than assume.\n"
            "  Read the company name in the QuickBooks header and run:\n"
            "      python3 bin/books.py browser confirm --company \"what it says\""
        )
    if prof is not None:
        # Re-checked here as well as at confirm time, because a profile can be
        # replaced between the two and the confirmation would then be about a
        # company this directory is no longer for.
        browser.confirm_company(seen, prof.entity.name)
    return seen


def browser_gate(work, prof=None):
    """Everything that has to be true before any browser step. Refuses, loudly."""
    seen = require_company(work, prof)
    stop = browser.blocking(work.root)
    if stop:
        raise Refusal(stop)
    return seen


def plan_path(work, tag):
    return work.dir("review", create=True) / f"batch-{tag}.json"


def plan_markdown_path(work, tag):
    return work.dir("review", create=True) / f"batch-{tag}.md"


def load_plan(work, tag):
    """Read a plan file back as a PostBatch, refusing a plan that was edited.

    The approval covers the bytes of this file, so an edit voids it anyway. This
    check fires earlier and says something more useful: the rows and the hash
    inside the file disagree, which means it was changed by hand rather than
    rebuilt.
    """
    p = plan_path(work, tag)
    if not p.exists():
        raise Refusal(
            f"there is no plan for batch-{tag} at {work.rel(p)}.\n"
            f"  Plans that exist: "
            + (", ".join(f"batch-{t}" for t in known_plans(work)) or "none yet")
            + "\n  Build them with `books.py browser plan --kind categorize`."
        )
    data = read_json(p, {}) or {}
    try:
        rows = [browser.PostRow(**r) for r in data.get("rows") or []]
        batch = browser.PostBatch(
            tag=data.get("tag") or tag, kind=data.get("kind") or "",
            account=data.get("account") or "", rows=rows,
            company=data.get("company") or "", counter=data.get("counter") or "",
            built=data.get("built") or "", source=data.get("source") or "")
    except TypeError as exc:
        raise Refusal(
            f"{work.rel(p)} is not a plan this version can read ({exc}). "
            f"Rebuild it with `books.py browser plan`."
        ) from exc
    stated = str(data.get("row_hash") or "")
    if stated and stated != batch.row_hash():
        raise Refusal(
            f"{work.rel(p)} carries rows that do not match the hash written into "
            f"it, so it was edited by hand after it was built.\n"
            "  A batch is the thing somebody approves. Editing one after the "
            "fact means the approval and the rows are about different sets of "
            "transactions, which is the exact failure this whole mechanism "
            "exists to stop.\n"
            "  Rebuild it: python3 bin/books.py browser plan --kind "
            f"{batch.kind}"
        )
    return batch


def known_plans(work):
    review = work / "review"
    if not review.is_dir():
        return []
    out = []
    for p in sorted(review.glob("batch-*.json")):
        tag = parse_batch_tag(p.name)
        if tag:
            out.append(tag)
    return out


def write_plan(work, batch):
    p = plan_path(work, batch.tag)
    p.write_text(batch.to_json(), encoding="utf-8")
    md = plan_markdown_path(work, batch.tag)
    md.write_text(batch.to_markdown(), encoding="utf-8")
    return p, md


# ---------------------------------------------------------- browser: help

def cmd_browser_help(args, work):
    """`books.py browser` with no step: the order, and what each step is for."""
    head("Working inside QuickBooks, in your own browser")
    say("  You are signed in. Your agent never sees a credential, never asks for "
        "one, and never types one.")
    say("  It proposes a batch, you approve that batch in your own terminal, and "
        "only then can anything be posted.")
    say("  After every batch it reads the count again and proves the books moved "
        "by exactly what you approved.")
    head("The steps")
    for name, what in (
            ("confirm", "say which company file is on screen. Nothing runs first"),
            ("read", "record what a screen says: the queues, the tiles, the "
                     "reconciliation summary, the rules, the audit log"),
            ("plan", "build batches small enough to read, one approval each"),
            ("post", "open a run for one approved batch and print its steps"),
            ("verify", "prove the count moved by exactly the approved amount"),
            ("clear", "retire a halt, with a note saying what caused it"),
            ("runbook", "write a refused action out as steps you do by hand"),
            ("status", "what is approved, what is open, what is halted")):
        say(f"  {name:<9} {what}")
    head("What is refused here, whatever anyone approves")
    for act in sorted(browser.REFUSED.values(), key=lambda a: a.key):
        say(f"  {act.label}")
    say("")
    say("  Each destroys a record that does not come back, so none of them is "
        "gated. `books.py browser runbook <name>` writes out the order to work "
        "one by hand.")
    say("")
    say("      python3 bin/books.py browser confirm --company \"the name the "
        "header shows\"")
    return OK


# ------------------------------------------------------- browser: confirm

def cmd_browser_confirm(args, work):
    prof = load_profile(work, args.profile)
    company = browser.confirm_company(
        args.company, prof.entity.name, by=whoami(args.by),
        file_id=args.file_id or "")
    state = browser_state(work)
    state["company"] = company.to_json()
    state["confirmed_on"] = today_iso()
    write_json(work / BROWSER_FILE, state)

    head("Company confirmed")
    bullet("on screen", company.observed)
    bullet("in the profile", company.expected)
    if company.file_id:
        bullet("company file", company.file_id)
    bullet("confirmed by", company.confirmed_by or "the owner")
    bullet("at", company.confirmed_at)
    say("")
    say("Every read and every write from here checks this again. If the company "
        "in the header changes, everything stops.")

    head("You sign in, and that never changes")
    say("  Your agent has no credential and cannot be given one. It works in a "
        "browser you signed in to yourself.")
    say("  If a QuickBooks page ever asks for a password mid-run, the session "
        "ended. Sign in again in your own window. Nothing types it for you.")

    head("What to read first")
    for name in ("banking", "for-review", "reconcile-summary", "rules",
                 "audit-log"):
        s = browser.surface(name)
        say(f"  {name}")
        say_lines(_labelled("      answers: ", s.answers, 64))
    say("")
    say("      python3 bin/books.py browser read --surface banking --from "
        "reports/browser-reads/banking.json")
    return OK


# ---------------------------------------------------------- browser: read

def cmd_browser_read(args, work):
    prof = load_profile(work, args.profile)
    seen = require_company(work, prof)
    payload = read_json(Path(args.source), None)
    if payload is None:
        raise Refusal(
            f"there is no read at {args.source}.\n"
            "  Read the screen with your browser tools, write what you read to a "
            "JSON file, then point this at it. The file is the thing that gets "
            "checked, and it stays on disk as the record of what the screen said "
            "on the day."
        )
    claimed = str((payload or {}).get("company") or "").strip() if isinstance(payload, dict) else ""
    if claimed:
        browser.confirm_company(claimed, seen)

    reader = browser.READERS.get(args.surface)
    if reader is None:
        raise Refusal(
            f"{args.surface!r} is not a screen this reads. The five are: "
            + ", ".join(sorted(browser.READERS)) + "."
        )
    result = reader(payload)

    kept = work.dir(BROWSER_READS, create=True) / f"{args.surface}-{today_iso()}.json"
    write_json(kept, payload)

    live = load_live(work)
    recorded = []

    if args.surface == "for-review":
        key = _account_key(prof, result.account)
        queue_file = write_queue_export(work, prof, key, result)
        gap = compare_to_last_read(work, key, result.total)
        record_live(work, live, ("accounts", key, "for_review_count"),
                    result.total, args.by)
        recorded.append(f"{key}: " + n_of(result.total, result.total,
                                          "items in For Review"))
        head(f"For Review on {key}")
        table([[p.filter, f"{p.rows:,}"] for p in result.parts] +
              [["all filters, added", f"{result.total:,}"],
               ["what the tab displayed", f"{result.displayed:,}"]],
              ["filter", "rows"])
        if result.undercount:
            say("")
            say(f"  The tab undercounts by {result.undercount:,}. "
                + n_of(result.undercount, result.total, "rows")
                + " are work that nobody scheduled, because the number people "
                  "quote is the one on the tab.")
            say("  The summed figure is what was recorded.")
        if result.rows_read:
            say("")
            say("  " + n_of(result.rows_read, result.total, "rows were read off "
                            "the grid") + ", so the read covers the queue.")
        if queue_file is not None:
            say("")
            bullet("queue written to", work.rel(queue_file))
            say("  " + n_of(len(result.rows), result.total, "rows are in that "
                            "file") + ". Everything downstream reads it the same "
                "way it reads an export, so nobody has to export the same queue "
                "by hand after it has already been read.")
        else:
            say("")
            say("  The read carried the count and not the rows, so the queue "
                "itself still has to come from an export: Banking, the account, "
                "For review, Export to Excel. `books.py browser plan` needs the "
                "rows, not the number.")
        if gap is not None and gap["unexplained"]:
            head("Rows moved that nobody approved")
            say_lines(["  " + line for line in
                       browser.movement_text(gap).splitlines()])
            drift = gap["unexplained"]
            raise Failure(
                n_of(abs(drift), abs(gap["moved"]) or abs(drift),
                     f"rows that moved on {key} have no approval behind them")
                + ".\n  Nothing else runs on this account until somebody has "
                  "read the audit log and said what it was."
            )

    elif args.surface == "banking":
        head("The account tiles")
        rows = []
        for tile in result:
            key = _account_key(prof, tile["account"])
            if tile["for_review"] is not None:
                record_live(work, live, ("accounts", key, "for_review_count"),
                            tile["for_review"], args.by)
            if tile["feed_state"]:
                record_live(work, live, ("accounts", key, "feed_state"),
                            tile["feed_state"], args.by)
            if tile["feed_last"]:
                record_live(work, live, ("accounts", key, "feed_last"),
                            tile["feed_last"], args.by)
            if tile.get("bank_balance"):
                record_live(work, live, ("accounts", key, "bank_balance"),
                            tile["bank_balance"], args.by)
                record_live(work, live, ("accounts", key, "bank_balance_source"),
                            tile["bank_balance_source"], args.by)
                if tile.get("bank_balance_as_of"):
                    record_live(work, live,
                                ("accounts", key, "bank_balance_as_of"),
                                tile["bank_balance_as_of"], args.by)
            recorded.append(f"{key}: the feed is {tile['feed_state'] or 'unread'}")
            rows.append([key, tile["feed_state"] or "", tile["feed_last"] or "",
                         "" if tile["for_review"] is None else f"{tile['for_review']:,}",
                         tile.get("bank_balance", "")])
        table(rows, ["account", "feed", "last update", "for review",
                     "the bank's own balance"])
        stopped = [t for t in result if t["feed_state"] == "stopped"]
        if stopped:
            say("")
            say("  " + n_of(len(stopped), len(result), "feeds have stopped")
                + ". A stopped feed is a hole in the books that no export shows, "
                  "and the date it stopped decides which months need statements.")
        blank = [t for t in result if not t.get("bank_balance")]
        if blank:
            say("")
            say("  " + n_of(len(blank), len(result), "accounts have no balance "
                            "from the bank's own site") + ". The tile shows what "
                "the books think, which is the figure under test, so it does not "
                "answer this.")

    elif args.surface == "reconcile-summary":
        head("Reconciled through")
        never = [r for r in result if r["never"]]
        for r in result:
            if r["reconciled_through"]:
                key = _account_key(prof, r["account"])
                record_live(work, live, ("accounts", key, "reconciled_through"),
                            r["reconciled_through"], args.by)
                recorded.append(f"{key}: reconciled through "
                                f"{r['reconciled_through']}")
        table([[_account_key(prof, r["account"]),
                r["reconciled_through"] or "never reconciled"] for r in result],
              ["account", "through"])
        say("")
        say("  " + n_of(len(result) - len(never), len(result),
                        "accounts have ever been reconciled") + ".")
        say("  An item dated inside a reconciled month is a re-download. Booking "
            "one counts it twice, and doubled figures are harder to find later "
            "than a gap.")

    elif args.surface == "rules":
        record_live(work, live, ("rules", "count"), result["count"], args.by)
        record_live(work, live, ("rules", "auto_add"), result["auto_add"], args.by)
        recorded.append(f"{result['count']} bank rules, {result['auto_add']} of "
                        f"them posting without being seen")
        head("Bank rules")
        bullet("rules", f"{result['count']:,}")
        bullet("post by themselves", n_of(result["auto_add"], result["count"],
                                          "rules"))
        if result["auto_add"]:
            say("")
            say("  Turn those off before any figure is produced for the return. "
                "A rule with auto-add on keeps posting into the year while the "
                "year is being worked, so every hour worked before it stops is "
                "worked twice.")
            say("  Turning it off does not undo what it already posted. "
                "`books.py browser read --surface audit-log` is how you find "
                "that.")

    elif args.surface == "audit-log":
        record_live(work, live, ("people", "last_human_date"),
                    result["last_human_date"], args.by)
        if result["note"]:
            record_live(work, live, ("people", "note"), result["note"], args.by)
        recorded.append("a person last worked in the file on "
                        + result["last_human_date"])
        head("Who last worked in this file")
        bullet("date", result["last_human_date"])
        if result["by"]:
            bullet("who", result["by"])
        say("")
        say("  Everything posted after that date arrived from a rule or a sync "
            "rather than from a person. The audit history is the only place that "
            "says whose entry something is, and a date never says it.")

    head("Recorded")
    for r in recorded:
        say(f"  {r}")
    say("")
    bullet("stored in", work.rel(work / LIVE_FILE))
    bullet("the read itself", work.rel(kept))
    say("")
    say("Each fact carries the date it was read. Every later command uses these "
        "and says which of them it used.")

    missing = intake_missing(prof, load_live(work))
    if missing:
        say("")
        say(n_of(len(missing), len(missing), "live facts are still missing")
            + ". `books.py intake` lists them, and browser mode can read most of "
              "them off the screen.")
    return OK


def compare_to_last_read(work, key, total):
    """Compare this queue read to the last one, and to what was approved between.

    The count check inside a run proves that one batch did what it said. This
    proves something the run cannot: that nothing ELSE happened to the queue
    between two reads. It is the only check here that would notice an agent
    working rows without asking, because clicking a row is not something a hook
    can tell apart from clicking a filter.
    """
    counter = f"for-review:{key}"
    state = browser_state(work)
    marks = state.get("queue_reads") or {}
    last = marks.get(str(key))
    if not last or last.get("total") is None:
        # The first read of an account has nothing to compare against, so it
        # records every run already finished on this counter as accounted for.
        # Otherwise the second read would attribute the whole of a previous
        # session's work to the gap between these two reads.
        done = [r.tag for r in browser.finished_runs(work.root, counter)]
        save_read_mark(work, key, total, done)
        return None
    gap = browser.unexplained_movement(
        work.root, counter, previous=last["total"], current=total,
        counted=last.get("counted") or [])
    save_read_mark(work, key, total, gap["counted"])
    return gap


def save_read_mark(work, key, total, counted=()):
    state = browser_state(work)
    state.setdefault("queue_reads", {})[str(key)] = {
        "total": int(total),
        "at": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
        "counted": sorted(str(t) for t in counted or ()),
    }
    write_json(work / BROWSER_FILE, state)


def write_queue_export(work, prof, key, result):
    """Write the rows a browser read carried into for-review/, or return None.

    Same three columns a QuickBooks export has, and a filename carrying the
    account's own mask so `account_for_file` places it the way it places an
    export. That is deliberate: the rest of the kit should not be able to tell
    which route a queue arrived by, and a row read off a screen that nobody
    wrote down is a row nobody can check tomorrow.
    """
    if not result.rows:
        return None
    spec = prof.account_spec(key)
    label = (spec.label if spec is not None else key) or key
    safe = re.sub(r"[^A-Za-z0-9]+", "-", str(label)).strip("-").lower() or "account"
    target = work.dir("for-review", create=True) / f"{safe}-browser-{today_iso()}.csv"
    with open(target, "w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["Date", "Description", "Amount"])
        for row in result.rows:
            w.writerow([row["date"], row["description"], row["amount"]])
    return target


def _account_key(prof, given):
    """The profile's own name for an account, given whatever the screen calls it.

    Deliberately strict. A screen says "Northgate Checking 7742" where a profile
    says "1010", so some translation is needed, and the translation is by exact
    name or by the mask the label carries. It is not by resemblance: a fuzzy
    match once turned "101000" into the account "1010", which would have
    recorded a queue of 600 against the wrong account and left the right one
    reading zero.

    Where two accounts share a mask, that is the duplicated-account case, and
    guessing between them is the one thing that must not happen there.
    """
    given_n = norm_text(given)
    if not given_n:
        raise Refusal("a read has to say which account it is for.")
    specs = declared_accounts(prof)
    for spec in specs:
        if given_n in {norm_text(spec.book), norm_text(spec.label),
                       norm_text(spec.mask)}:
            return spec.book
    hits = [s for s in specs if s.mask and norm_text(s.mask) in given_n.split()]
    if len(hits) == 1:
        return hits[0].book
    if len(hits) > 1:
        raise Refusal(
            f"{given!r} matches {len(hits)} accounts by their last four digits: "
            + ", ".join(s.book for s in hits) + ".\n"
            "  That is what one real account looks like after a feed was "
            "relinked and QuickBooks made a second one. Which of the two this "
            "read belongs to decides whether a queue is work or a duplicate, so "
            "it is not guessed here.\n"
            "  Run `books.py merge-plan` first, or name the account exactly."
        )
    raise Refusal(
        f"{given!r} is not an account in the profile, so a read cannot be "
        f"recorded against it.\n"
        "  Declared: " + (", ".join(s.book for s in specs) or "none") + "\n"
        "  Use one of those names, or the label QuickBooks shows for it, or run "
        "`books.py learn` again if the account is new."
    )


# ---------------------------------------------------------- browser: plan

def cmd_browser_plan(args, work):
    prof = load_profile(work, args.profile)
    require_engagement(prof)
    browser_gate(work, prof)
    company = confirmed_company(work)
    size = int(args.size or browser.DEFAULT_BATCH_ROWS)

    if args.kind == "categorize":
        batches, skipped, total = _plan_categorize(args, work, prof, company, size)
    elif args.kind == "add":
        batches, skipped, total = _plan_add(args, work, prof, company, size)
    else:
        batches, skipped, total = _plan_journal(args, work, prof, company, size)

    if not batches:
        raise Refusal(
            n_of(0, total, "rows could be planned") + ".\n"
            "  " + (skipped[0] if skipped else "There is nothing to do here, "
                    "which may be the right answer.")
        )

    written = []
    for b in batches:
        p, md = write_plan(work, b)
        written.append([f"batch-{b.tag}", b.account or "several", f"{len(b.rows):,}",
                        work.rel(md)])

    planned = sum(len(b.rows) for b in batches)
    head(f"{args.kind}: {len(batches)} batches, {planned:,} rows")
    table(written, ["batch", "account", "rows", "what you read"])
    say("")
    say(n_of(planned, total, "rows are planned")
        + f", in batches of at most {size}.")
    say(f"  {size} rows is about a screen and about two minutes of reading. A "
        "batch big enough to scroll past is an approval of the scroll bar, and "
        "these post into books that are live.")
    for note in skipped:
        say("")
        say_lines(_labelled("  ", note, 74))

    first = batches[0]
    head("What happens next")
    say(f"  1. Read {work.rel(plan_markdown_path(work, first.tag))}. Every row is "
        "in it, with the reason on each.")
    say("  2. If you agree with it, run this in your own terminal. Your agent "
        "cannot run it:")
    say(f"         python3 bin/books.py approve batch-{first.tag}")
    say("  3. Then your agent reads the count on the screen and opens the run:")
    say(f"         python3 bin/books.py browser post batch-{first.tag} --before N")
    say("  4. It works the batch, reads the count again, and proves it:")
    say(f"         python3 bin/books.py browser verify batch-{first.tag} --after N")
    say("")
    say("  Step 4 is the one that catches a rule firing underneath the work, a "
        "stale page, and a double click. If the count is not exactly what was "
        "approved, everything stops.")
    return OK


def _qbo_account(proposal):
    """The account written the way QuickBooks wants it typed.

    The interface matches on the number followed by the exact name, and a name
    on its own is ambiguous the moment two accounts share one. The review
    workbook shows the name because a person reads it; a batch that an agent
    types into a field shows both.
    """
    num = (getattr(proposal, "account", "") or "").strip()
    name = (getattr(proposal, "account_full", "") or "").strip()
    if num and name and num != name:
        return f"{num} {name}"
    return name or num


def _plan_categorize(args, work, prof, company, size):
    """Batches from the For Review queue, one account at a time.

    Rows carrying a rule with evidence behind them become batches. Everything
    else stays a question, because a guess posted into live books is a guess
    that is now the record.
    """
    live = load_live(work)
    year = require_filing_year(live)
    ledger = load_ledger(work, args.exports)
    everything, _, _ = load_queue(work, args.for_review, prof, quiet=True)
    if not everything:
        raise Refusal("the For Review exports hold no transactions to work.")
    scoped = analyze_scope(
        everything, filing_year=year,
        reconciled_through=reconciled_through_map(prof, live),
        history_of=history_map(ledger, prof, everything))
    lines = list(scoped.in_scope)
    if not lines:
        raise Refusal(
            n_of(0, scoped.total, f"queue rows belong to the {year} return") + ".")

    proposals = matching.classify(lines, ledger, prof,
                                  window_days=args.window_days)
    postable, parked = [], []
    for p in proposals:
        if p.action != "add" or not p.rule_id or p.needs_human:
            parked.append(p)
            continue
        postable.append(p)

    by_account = {}
    for p in postable:
        by_account.setdefault(p.line.account_key or "unassigned", []).append(p)

    only = norm_text(args.account) if args.account else ""
    batches = []
    for i, key in enumerate(sorted(by_account), start=1):
        if only and norm_text(key) != only:
            continue
        rows = [browser.PostRow(
            ref=f"{iso(p.line.date)}-{p.line.source_row}",
            date=iso(p.line.date),
            descriptor=p.line.descriptor,
            amount=fmt(p.line.amount),
            account=_qbo_account(p),
            klass=p.klass,
            payee=p.matched_to or "",
            why=p.source,
            action="categorize",
            needs_human=p.needs_human,
        ) for p in sorted(by_account[key], key=lambda x: (x.line.date,
                                                          x.line.source_row))]
        batches.extend(browser.slice_batches(
            rows, kind="categorize", account=key, size=size, company=company,
            counter=f"for-review:{key}", prefix=f"c{i}_",
            source=f"the For Review queue for {key}, scoped to {year}"))

    skipped = []
    classless = sum(1 for b in batches for r in b.rows if not r.klass)
    if classless:
        skipped.append(
            n_of(classless, sum(len(b.rows) for b in batches), "planned rows carry "
                 "no class") + ". None of the rules mined from your own history "
            "sets one. If your books use classes, post nothing until that is "
            "fixed: the Class column is off by default in the new grid, and a row "
            "posted without it is written as 'Not specified', which is a defect "
            "rather than a state. If your books do not use classes, this is "
            "nothing.")
    if parked:
        questions = sum(1 for p in parked if p.action == "question")
        matches = sum(1 for p in parked if p.action == "match")
        transfers = sum(1 for p in parked if p.action == "transfer")
        flagged = sum(1 for p in parked if p.needs_human)
        guessed = sum(1 for p in parked
                      if p.action == "add" and not p.rule_id and not p.needs_human)
        skipped.append(
            n_of(len(parked), len(proposals), "rows were left out of these "
                 "batches") + f": {matches} already in the books, {transfers} "
            f"transfers between your own accounts, {questions} questions, "
            f"{guessed} where the account would be a guess, and {flagged} whose "
            "bank description carries text aimed at an automated system. None of "
            "those is a row an agent should click through in a live file. "
            "`books.py catchup` puts them in a workbook, and `books.py questions` "
            "turns the questions into a short list.")
    return batches, skipped, len(proposals)


def _plan_add(args, work, prof, company, size):
    """Batches of transactions the statements show and the feed never delivered."""
    ledger = load_ledger(work, args.exports)
    parsed, _, _ = load_statements(work, args.statements, prof)
    for s in parsed:
        if s.tie_out is None:
            s.check()
    not_tying = [s for s in parsed if not s.ties]
    if not_tying:
        raise Refusal(
            n_of(len(not_tying), len(parsed), "statements do not tie") + ".\n"
            "  Adding transactions you have not proved complete moves the gap "
            "into the books, where it is harder to see. Run `books.py tieout` "
            "and work the differences first."
        )
    gaps = gap_months(ledger, prof, parsed, only_account=args.account)
    if not gaps:
        raise Refusal(
            n_of(0, len(parsed), "statement months need adding")
            + ": every month the statements cover already has transactions in "
              "the books.")

    batches, total = [], 0
    for i, key in enumerate(sorted({a for a, _ in gaps}), start=1):
        lines = []
        for (acct, month), rows in sorted(gaps.items()):
            if acct == key:
                lines.extend(sorted(rows, key=lambda l: (l.date, l.source_row)))
        total += len(lines)
        proposals = matching.classify(lines, ledger, prof)
        rows = [browser.PostRow(
            ref=f"{iso(p.line.date)}-{p.line.source_row}",
            date=iso(p.line.date),
            descriptor=p.line.descriptor,
            amount=fmt(p.line.amount),
            account=_qbo_account(p),
            klass=p.klass,
            why=p.source or "the statement holds this row and the books do not",
            action="add",
            needs_human=p.needs_human,
        ) for p in proposals]
        batches.extend(browser.slice_batches(
            rows, kind="add", account=key, size=size, company=company,
            counter=f"register:{key}", prefix=f"g{i}_",
            source="months your statements cover and your books do not"))
    note = [
        "Every one of these becomes a transaction that was not there before, so "
        "the count on the register has to rise by exactly the number in the "
        "batch. A rise of one more than that is the feed delivering the same "
        "month at the same time, and the run halts on it."
    ]
    return batches, note, total


def _plan_journal(args, work, prof, company, size):
    """Batches of adjusting entries, typed into the journal form.

    This is the one QuickBooks Online in the United States cannot import on any
    plan, so before browser mode the only route was a person typing each entry
    off a worksheet.
    """
    through = parse_date(args.through, field="--through")
    since = parse_date(args.since, field="--since") if args.since else None
    ledger = None
    try:
        ledger = load_ledger(work, args.exports)
    except Refusal:
        pass
    resolve = resolver_for(ledger, prof)
    live = load_live(work)
    year = filing_year_of(live) or through.year
    gate_automation(work, prof, ledger, live, year, "A journal entry batch")

    drafts, missing = draft_entries(prof, through, since=since)
    entries = [e for d in drafts for e in d]
    questions = [q for d in drafts for q in d.questions]
    if not entries:
        raise Refusal(
            n_of(0, 0, "entries were drafted") + ". Nothing is planned, which is "
            "the honest outcome: an entry with no basis is an entry nobody can "
            "defend."
            + ("\n  " + n_of(len(questions), len(questions), "questions block "
                             "one") + ". Run `books.py entries` to read them."
               if questions else ""))

    rows = []
    for e in entries:
        detail = []
        for line in e.lines:
            account, debit, credit, memo = (list(line) + ["", "", "", ""])[:4]
            full = (resolve(account) if resolve else "") or ""
            # The journal form matches on the number then the exact name, so
            # both go in. A name alone is ambiguous the moment two accounts
            # share one, and a number alone is unreadable to the person
            # checking the batch.
            named = f"{account} {full}".strip() if full and full != account \
                else str(account)
            side = f"debit {fmt(debit)}" if money(debit or 0) != ZERO else \
                f"credit {fmt(credit)}"
            detail.append(f"{named}: {side}" + (f", {memo}" if memo else ""))
        rows.append(browser.PostRow(
            ref=str(e.number or ""),
            date=iso(e.date),
            descriptor=e.memo or e.kind,
            amount=fmt(e.total_debits()),
            account=f"{len(e.lines)} lines",
            why=e.basis or "",
            action="journal",
            detail=detail,
        ))
    batches = browser.slice_batches(
        rows, kind="journal", account="", size=size, company=company,
        counter="register:journal", prefix="j",
        source=f"recurring entries through {iso(through)}")
    note = []
    if missing:
        note.append(n_of(len(ENTRY_KINDS) - len(missing), len(ENTRY_KINDS),
                         "kinds of recurring entry are set up") + ". Nothing was "
                    "drafted for " + ", ".join(missing) + ".")
    if questions:
        note.append(n_of(len(questions), len(questions), "questions") + " block "
                    "an entry that is not in these batches. `books.py entries` "
                    "prints them.")
    note.append(
        "QuickBooks Online in the United States cannot import a journal entry on "
        "any plan, which is why these were a worksheet to type before. The "
        "journal form only mounts in a brand new tab, and in a tab that has "
        "already loaded the report builder it fails silently with no error at "
        "all.")
    return batches, note, len(rows)


# ---------------------------------------------------------- browser: post

def require_quiet_file(work):
    """Refuse to post while anything else is posting into the same file.

    The count before and the count after are the whole proof that a batch did
    what it was approved to do, and that proof rests on one assumption: nothing
    else moved the count while the batch ran. A bank rule with auto-add on
    breaks exactly that assumption, quietly, and the result is a halt on every
    batch with no way to tell a rule from a mis-click.

    So this is a refusal rather than a warning, and it comes before the first
    click rather than after the count fails to add up.
    """
    live = load_live(work)
    rules = live.get("rules") or {}
    if rules.get("count") is None or rules.get("auto_add") is None:
        raise Refusal(
            "nobody has said how many bank rules are on, or how many of them "
            "post without being seen.\n"
            "  Posting here is checked by reading a count before and after, and "
            "that check only means something if nothing else is posting at the "
            "same time. A rule with auto-add on is something else posting.\n"
            "  Read the gear, then Rules, and record it:\n"
            "      python3 bin/books.py browser read --surface rules --from "
            "reports/browser-reads/rules.json"
        )
    auto = int(rules.get("auto_add") or 0)
    if auto > 0:
        raise Refusal(
            n_of(auto, int(rules.get("count") or auto), "bank rules post without "
                 "being seen") + ", so nothing may be posted yet.\n"
            "  Every batch here is proved by a count read before and after. A "
            "rule firing underneath the work moves that count, which makes each "
            "batch halt and makes a real mis-click indistinguishable from a rule "
            "doing its job.\n"
            "  In QuickBooks: the gear, then Rules. Turn off \"Automatically "
            "confirm transactions this rule applies to\" on every rule. Export "
            "them first, under Rules then Export rules, so you can put them "
            "back.\n"
            "  Then read the screen again and record what is left."
        )
    return rules


def cmd_browser_post(args, work):
    prof = load_profile(work, args.profile)
    seen = browser_gate(work, prof)
    tag = normalize_batch(args.batch)
    batch = load_plan(work, tag)

    # Approval first, because it is the refusal a person most needs to see, and
    # then the quiet-file check, because a count proves nothing while a rule is
    # posting underneath it.
    ap = approval_check_batch(work.dir("review", create=True), tag)
    require_quiet_file(work)
    if batch.company:
        browser.confirm_company(seen, batch.company)

    run = browser.open_run(batch, before=args.before, approved_by=ap.approved_by,
                           approved_at=ap.approved_at, company=seen)
    path = browser.save_run(work.root, run)

    head(f"batch-{tag} is open")
    bullet("approved by", ap.approved_by)
    bullet("approved at", ap.approved_at)
    bullet("company", seen)
    bullet("rows", f"{run.rows:,}")
    bullet("counter", run.counter)
    bullet("before", f"{int(run.before):,}")
    bullet("expected after", f"{run.expected_after:,}")
    bullet("run file", work.rel(path))

    head("The steps, in order")
    for i, step in enumerate(browser.steps_for(batch), start=1):
        say_lines(_labelled(f"  {i}. ", step, 70))

    s = browser.surface("for-review" if batch.kind == "categorize" else
                        ("journal" if batch.kind == "journal" else "register"))
    head("What this screen does that nothing warns you about")
    for trap in s.traps:
        say_lines(_labelled("  - ", trap, 70))

    head("What is refused here, whatever anyone approves")
    for act in sorted(browser.REFUSED.values(), key=lambda a: a.key):
        say(f"  {act.label}")
    say("")
    say("  None of those is gated. There is no approval that makes them safe, "
        "because each destroys a record that is not recoverable.")
    say("  Where one of them is genuinely the right fix, "
        "`books.py browser runbook <name>` writes the order to work it by hand, "
        "and says what each step destroys.")

    head("When the batch is done")
    say(f"  Read {run.counter} again on a reloaded page with the filters clear, "
        "then:")
    say(f"      python3 bin/books.py browser verify batch-{tag} "
        f"--after {run.expected_after}")
    say("")
    say("  Pass the number you actually read. If it is not "
        f"{run.expected_after:,}, this halts and nothing else runs until somebody "
        "has found out why.")
    return OK


def approval_check_batch(review_dir, tag):
    """The approval gate, for an act that leaves no file behind."""
    from closethebooks.approval import check_batch
    return check_batch(review_dir, tag)


# -------------------------------------------------------- browser: verify

def cmd_browser_verify(args, work):
    prof = load_profile(work, args.profile)
    seen = require_company(work, prof)
    tag = normalize_batch(args.batch)
    run = browser.load_run(work.root, tag)
    if run is None:
        raise Refusal(
            f"there is no run for batch-{tag}. Nothing was opened, so there is "
            f"nothing to verify.\n"
            f"  A batch is opened with `books.py browser post batch-{tag} "
            f"--before N`, after you have approved it."
        )
    try:
        run = browser.verify_run(run, args.after, company=seen)
    except browser.BrowserError:
        browser.save_run(work.root, run)
        raise Failure(run.halt_reason or "the run halted.")
    browser.save_run(work.root, run)

    head(f"batch-{tag} verified")
    bullet("counter", run.counter)
    bullet("before", f"{int(run.before):,}")
    bullet("after", f"{int(run.after):,}")
    bullet("moved", f"{int(run.after) - int(run.before):+,}")
    bullet("approved", f"{run.rows:,} rows")
    say("")
    say("  The count moved by exactly the number of rows you approved. That is "
        "the whole claim, and it is the one that catches a rule firing "
        "underneath the work.")

    remaining = [t for t in known_plans(work)
                 if (browser.load_run(work.root, t) or None) is None]
    head("Next")
    if remaining:
        nxt = sorted(remaining)[0]
        say(f"  {len(remaining)} batches have not been posted. The next one is "
            f"batch-{nxt}.")
        say(f"      python3 bin/books.py approve batch-{nxt}")
    else:
        say("  Every planned batch has been posted and verified.")
        say("  Then: `books.py reconcile`, then `books.py check`, which measure "
            "whether the work landed the way it was meant to.")
    return OK


# --------------------------------------------------------- browser: clear

def cmd_browser_clear(args, work):
    tag = normalize_batch(args.batch)
    run = browser.clear_halt(work.root, tag, note=args.note, by=whoami(args.by))
    head(f"batch-{tag} cleared")
    bullet("note", args.note)
    bullet("by", whoami(args.by))
    say("")
    say("  The note is on the run file, which is where anybody looking at this "
        "later will go. A halt cleared with nothing written down is a halt that "
        "happens again.")
    stop = browser.blocking(work.root)
    if stop:
        say("")
        say_lines(_labelled("  ", stop.splitlines()[0], 74))
    return OK


# ------------------------------------------------------- browser: runbook

def cmd_browser_runbook(args, work):
    prof = None
    try:
        prof = load_profile(work, args.profile)
    except REFUSING_ERRORS:
        pass
    text = browser.runbook_markdown(
        args.action, company=confirmed_company(work)
        or (prof.entity.name if prof else ""), account=args.account or "")
    out = write_text(work.dir("reports", create=True)
                     / f"by-hand-{args.action}.md", text)
    act = browser.REFUSED[args.action]
    head(f"Refused, and written out for you: {act.label}")
    say_lines(_labelled("  ", "It destroys " + act.destroys, 74))
    say("")
    bullet("runbook", work.rel(out))
    say("")
    say("  Your agent will not do this and no flag changes that. You do it, "
        "because the cost of the wrong order is not recoverable.")
    return OK


# -------------------------------------------------------- browser: status

def cmd_browser_status(args, work):
    prof = None
    try:
        prof = load_profile(work, args.profile)
    except REFUSING_ERRORS:
        pass
    seen = confirmed_company(work)
    head("Browser mode")
    bullet("company confirmed", seen or "no, and nothing runs until it is")
    if prof is not None:
        bullet("profile says", prof.entity.name)

    plans = known_plans(work)
    rows = []
    for tag in plans:
        ok, message = approval_state(work, tag)
        run = browser.load_run(work.root, tag)
        rows.append([f"batch-{tag}",
                     "approved" if ok else "not approved",
                     run.state if run else "not posted",
                     "" if run is None or run.after is None
                     else f"{int(run.before):,} to {int(run.after):,}"])
    head("Batches")
    if rows:
        table(rows, ["batch", "approval", "run", "count"])
    else:
        say("  none planned yet")
    say("")
    say(n_of(sum(1 for r in rows if r[2] == "verified"), len(rows) or 0,
             "batches are posted and verified"))

    stop = browser.blocking(work.root)
    if stop:
        head("Nothing may be posted right now")
        say_lines(_labelled("  ", stop.splitlines()[0], 74))
        for line in stop.splitlines()[1:]:
            say("  " + line.strip())
        return FAILURE
    return OK


# ================================================================= scope

def cmd_scope(args, work):
    prof = load_profile(work, args.profile)
    live = load_live(work)
    year = require_filing_year(live)
    lines, queue_files, _ = load_queue(work, args.for_review, prof, quiet=True)
    if not lines:
        raise Refusal("the For Review exports hold no transactions to scope.")

    through = reconciled_through_map(prof, live)
    ledger = None
    try:
        ledger = load_ledger(work, None)
    except Refusal:
        pass
    report = analyze_scope(lines, filing_year=year, reconciled_through=through,
                           history_of=history_map(ledger, prof, lines))
    due = filing_due(live, prof)
    left = days_to(due)

    head(f"Filing {year}"
         + (f", due {due}" if due else "")
         + (f", {left:,} days left" if left is not None and left >= 0 else ""))
    bullet("rows in the queue", f"{report.total:,} from "
           + n_of(len(queue_files), len(queue_files), "files"))
    bullet("this deadline needs", n_of(len(report.in_scope), report.total, "rows"))
    bullet("set aside for now", n_of(len(report.deferred), report.total, "rows"))

    if report.deferred:
        head("Set aside, and why each group waits")
        rows = []
        for reason, count in sorted(report.counts.items(), key=lambda kv: -kv[1]):
            rows.append([DEFER_REASONS.get(reason, reason),
                         f"{count:,}", _defer_meaning(reason, year)])
        table(wrap_cell(rows, column=2), ["group", "rows", "what it means"])
        say("")
        say("Nothing here is thrown away. It is written out in full, and it comes back")
        say("when its own deadline does.")

    head("By account")
    by = report.by_account()
    table([[account_label(prof, k), f"{v['in_scope']:,}", f"{v['deferred']:,}",
            iso(through.get(k)) or "not recorded"]
           for k, v in sorted(by.items())],
          ["account", "this deadline", "set aside", "reconciled through"])
    unstated = [k for k in by if through.get(k) is None]
    if unstated:
        say("")
        say("  " + n_of(len(unstated), len(by), "accounts") + " have no reconciled-through "
            "date, so a re-download of a tied-out month reads as work to do.")
        say(f"      python3 bin/books.py intake --account {shell_arg(sorted(unstated)[0])} "
            f"--reconciled-through YYYY-MM-DD")

    path = write_json(work / SCOPE_FILE, {
        "filing_year": year, "due": due, "written": today_iso(),
        "total": report.total, "in_scope": len(report.in_scope),
        "deferred": len(report.deferred), "counts": report.counts,
        "by_account": by,
    })
    md = write_text(work / DEFERRED_FILE, _render_deferred(prof, report, year))
    say("")
    say(f"Written to {work.rel(path)} and {work.rel(md)}")
    head("What happens next")
    say(f"  {len(report.in_scope):,} rows go into batches. The rest wait.")
    say("      python3 bin/books.py catchup --batch-size 150")
    return OK


def _defer_meaning(reason, year):
    if reason == "next_year":
        return (f"belongs to the {year + 1} return. Working it now is work done "
                f"twice, and it can change before that year closes")
    if reason == "earlier_year":
        return ("dated before the year being filed. If a return has already gone in "
                "for that year, booking it changes a filed period")
    if reason == "reconciled_period":
        return ("a re-download of a month that already tied out. Booking it counts "
                "the same transaction twice")
    return ""


def _render_deferred(prof, report, year):
    out = [f"# Set aside, not dropped", "",
           f"{prof.entity.name or 'This company'}, written {today_iso()}.", "",
           n_of(len(report.deferred), report.total,
                "queue rows are not needed for the " + str(year) + " return") + ".",
           ""]
    for reason in sorted(report.counts, key=lambda r: -report.counts[r]):
        group = [d for d in report.deferred if d.reason == reason]
        out += [f"## {DEFER_REASONS.get(reason, reason)} ({len(group):,} rows)", "",
                _defer_meaning(reason, year) + ".", "",
                "| date | account | amount | description |",
                "|---|---|---|---|"]
        for d in group:
            desc = (d.line.descriptor or "").replace("|", "/")[:60]
            out.append(f"| {iso(d.line.date)} | {d.line.account_key or '(none)'} | "
                       f"{fmt(d.line.amount)} | {desc} |")
        out.append("")
    return "\n".join(out) + "\n"


# ========================================================== completeness

def cmd_completeness(args, work):
    prof = load_profile(work, args.profile)
    live = load_live(work)
    year = require_filing_year(live)
    ledger = load_ledger(work, args.exports)

    if args.settle:
        if not args.evidence:
            raise Refusal(
                "--settle needs --evidence naming the document that explains the\n"
                "  difference: which statement, which page, which line. A difference\n"
                "  closed without one is a plug, and a plug destroys the only signal\n"
                "  there was.")
        key = args.settle
        if prof.account_spec(key) is None:
            raise Refusal(f"there is no account {key!r} in the profile.")
        node = live.setdefault("completeness_settled", {}).setdefault(key, {})
        node.update({"evidence": args.evidence, "on": today_iso(),
                     "by": whoami(args.by)})
        save_live(work, live)
        head(f"Recorded against {account_label(prof, key)}")
        bullet("evidence", args.evidence)
        bullet("who", whoami(args.by))
        bullet("on", today_iso())
        say("")
        say("It stays in the evidence file and in the handoff, so whoever reads the")
        say("books next sees the document this rests on and can disagree with it.")
        say("")

    try:
        queue_lines, _, _ = load_queue(work, args.for_review, prof, quiet=True)
    except Refusal:
        queue_lines = []
    parsed, _, _ = load_statements(work, args.statements, prof, quiet=True,
                                   require=False, note_when_empty=False)
    # The period the BOOK side of every figure below comes from. The bank side
    # carries its own date, recorded when somebody read it off the screen.
    ledger_period_end = None
    for attr in ("period_end", "end", "last_activity"):
        ledger_period_end = getattr(ledger, attr, None)
        if ledger_period_end:
            break
    if ledger_period_end is None:
        months = getattr(ledger, "months_with_activity", lambda: {})()
        if months:
            last = sorted(months)[-1]
            y, m = (int(x) for x in last.split("-")[:2])
            import calendar as _cal
            ledger_period_end = dt.date(y, m, _cal.monthrange(y, m)[1])

    findings = analyze_completeness(prof, ledger, queue_lines, parsed, live,
                                    filing_year=year,
                                    materiality=materiality_of(prof))

    head(f"Is the record complete, {year}")
    say("The books plus everything unbooked, against what the bank itself says it")
    say("holds. A queue that moves an account away from its bank balance is the sign")
    say("that transactions exist in neither place.")
    say("")
    for f in findings:
        say(f"  {f.label}")
        say(f"      the books say          "
            + (balance_words(f.book, f.kind) if f.book is not None
               else "cannot be determined, no export states an opening position"))
        say(f"      the bank says          "
            + (balance_words(f.bank, f.kind) if f.bank is not None
               else "nobody has said"))
        if f.bank_source:
            say(f"                             {f.bank_source}")
        # The two sides of this comparison are almost never as at the same
        # moment. The book balance comes from an export with its own period end;
        # the bank balance was read off a screen on whatever day somebody
        # looked. Printing one date over both asserts something false about
        # half the figures, and a false date is how a wrong number gets quoted
        # to a preparer with confidence. State each side's own date, and say
        # plainly when they differ, because the gap between them is itself
        # activity nobody has accounted for.
        book_as_of = getattr(f, "book_as_of", None) or ledger_period_end
        bank_as_of = f.as_of
        if book_as_of and bank_as_of and book_as_of != bank_as_of:
            say(f"      books as at            {iso(book_as_of)}")
            say(f"      bank as at             {iso(bank_as_of)}")
            gap = (bank_as_of - book_as_of).days
            if gap > 0:
                say(f"      NOTE                   these are {gap:,} day(s) apart, so anything")
                say(f"                             that moved between them is in neither figure")
        elif bank_as_of:
            say(f"      both as at             {iso(bank_as_of)}")
        say(f"      unbooked items         {f.queue_rows:,} rows netting {fmt(f.queue_net)}")
        if f.after is not None:
            say(f"      books plus the queue   {balance_words(f.after, f.kind)}")
            say(f"      difference             {fmt(f.difference)}")
        if f.settled:
            say(f"      settled                {f.evidence}")
        elif f.why:
            say_lines(_labelled("      FINDING                ", f.why, 52))
        else:
            say(f"      complete               nothing is missing on this account")
        say("")

    open_findings = [f for f in findings if f.open]
    path = write_json(work / COMPLETENESS_FILE, {
        "filing_year": year, "written": today_iso(),
        "findings": [f.to_json() for f in findings],
        "open": len(open_findings),
    })
    say(n_of(len(findings) - len(open_findings), len(findings),
             "accounts are complete") + ".")
    if open_findings:
        say("")
        say("An open finding means working every item in the queue would still leave")
        say("the year incomplete. Fill the hole from statements, or name the document")
        say("that explains it. Do not file until each one is closed.")
        for f in open_findings:
            say(f"      python3 bin/books.py fill-gaps --account {shell_arg(f.account)}")
            break
    say("")
    say(f"Written to {work.rel(path)}")
    return FAILURE if open_findings else OK


def completeness_now(work, args):
    """Re-derive the completeness findings from live files, never from a report.

    A gate that reads a report it wrote earlier is a gate that passes because
    somebody ran a command, which is the failure this whole kit exists to avoid.
    """
    prof = load_profile(work, getattr(args, "profile", None))
    live = load_live(work)
    year = require_filing_year(live)
    ledger = load_ledger(work, getattr(args, "exports", None))
    try:
        queue_lines, _, _ = load_queue(work, getattr(args, "for_review", None), prof,
                                       quiet=True)
    except Refusal:
        queue_lines = []
    parsed, _, _ = load_statements(work, getattr(args, "statements", None), prof,
                                   quiet=True, require=False, note_when_empty=False)
    findings = analyze_completeness(prof, ledger, queue_lines, parsed, live,
                                    filing_year=year,
                                    materiality=materiality_of(prof))
    write_json(work / COMPLETENESS_FILE, {
        "filing_year": year, "written": today_iso(),
        "findings": [f.to_json() for f in findings],
        "open": len([f for f in findings if f.open]),
    })
    return prof, findings


# ============================================================ merge-plan

def keep_key(args, pairs, dup):
    return args.keep or next((p.keep for p in pairs if p.duplicate == dup), "")


def cmd_merge_plan(args, work):
    prof = load_profile(work, args.profile)
    ledger = load_ledger(work, args.exports)
    live = load_live(work)
    action = args.action

    queue_counts = {}
    try:
        queue_lines, _, _ = load_queue(work, args.for_review, prof, quiet=True)
        for line in queue_lines:
            if line.account_key:
                queue_counts[line.account_key] = queue_counts.get(line.account_key, 0) + 1
    except Refusal:
        queue_lines = []
    pairs = analyze_duplicates(ledger, prof, queue_lines, queue_counts)
    dup = str(args.duplicate).strip() if args.duplicate else ""

    if not dup:
        head("Accounts that look like one real account twice")
        if not pairs:
            say("None found. Two accounts count as one when they carry the same mask")
            say("or the same label, and nothing in this chart does.")
            return OK
        table([[account_label(prof, p.keep), account_label(prof, p.duplicate),
                f"{p.queue_items:,}", p.why] for p in pairs],
              ["keep", "duplicate", "its queue", "why they look like one"])
        say("")
        say("A bank re-link is the usual cause: the new connection cannot attach to")
        say("the old account, so QuickBooks makes a second one, puts the live feed on")
        say("it, and plugs the opening balance. The old one keeps the reconciled")
        say("history and stops receiving anything.")
        say("")
        say(f"      python3 bin/books.py merge-plan --duplicate {shell_arg(pairs[0].duplicate)}")
        return OK

    if prof.account_spec(dup) is None:
        raise Refusal(
            f"there is no account {dup!r} in the profile.\n"
            f"  Declared: " + (", ".join(a.book for a in declared_accounts(prof))
                               or "none"))

    # The gate. It fires before anything else is printed, because a plan is a
    # thing a person acts on, and the wrong order here is not recoverable.
    gate_queue_loaded(prof, live, queue_counts.get(dup, 0), dup, action=action)
    if keep_key(args, pairs, dup):
        gate_queue_loaded(prof, live, queue_counts.get(keep_key(args, pairs, dup), 0),
                          keep_key(args, pairs, dup), action=action)
    keep = keep_key(args, pairs, dup)
    pair = next((p for p in pairs if p.duplicate == dup), None)

    head(f"{account_label(prof, dup)}: its queue is empty, so this can go ahead")
    if keep:
        bullet("keep", account_label(prof, keep))
    bullet("retire", account_label(prof, dup))
    if pair is not None and pair.opening_plug is not None:
        bullet("its opening balance", balance_words(pair.opening_plug,
                                                    prof.account_spec(dup).kind))
        if pair.plug_matches_obe:
            say("")
            say("  That opening balance equals Opening Balance Equity to the cent, which")
            say("  means it is a plug QuickBooks wrote when the account was created and")
            say("  not a real position. It has to be disposed of by name, not archived:")
            say("  a retired account still carrying a balance moves that balance")
            say("  somewhere, and unnamed is the one place it must not go.")

    head("The order, and it is the whole point")
    say("  1. Book everything in the queue on both accounts, and accept it in")
    say("     QuickBooks. Disconnecting deletes what is left, and nothing exports it.")
    say("  2. Dispose of the opening-balance plug by name, with a reason, so the")
    say("     balance lands somewhere a reader can find.")
    say("         python3 bin/books.py reclass --from 3000 --to 3400 \\")
    say("             --reason \"opening balance plug written when the feed was re-linked\"")
    say("  3. Only then disconnect the feed on the account being retired.")
    say("  4. Then merge it into the numbered account, which holds the reconciled")
    say("     history.")
    say("  5. Re-run the tie-out. A merge moves history, and history that moved is")
    say("     history worth proving again.")
    say("         python3 bin/books.py tieout")
    say("")
    say("Doing 3 before 1 discards the only record of that account's activity.")
    return OK


# =========================================================== init

def cmd_init(args, work):
    work.ensure(*(name for name, _ in LAYOUT))
    say(f"Working directory ready: {work.root}")
    say("")
    say("Put your files here:")
    table([[name + "/", what] for name, what in LAYOUT], ["folder", "what goes in it"])
    say("")
    say("Then, in order:")
    say("  1. books.py learn         --exports exports/")
    say("  2. books.py statements    what to go and get, by account and month")
    say("  3. books.py tieout        --statements statements/")
    say("  4. books.py questions     --round 1")
    say("  5. books.py catchup       --batch-size 150")
    say("  6. books.py approve       batch-01          (you, in your own terminal)")
    say("  7. books.py build-imports --batch batch-01")
    say("  8. books.py entries --through YYYY-MM-DD, then reconcile, then check")
    say("  9. books.py handoff       --out handoff/")
    say("")
    say("Nothing here connects to QuickBooks. Every file that reaches import/ is one")
    say("you approved, and it becomes a transaction only when you upload it.")
    return OK


# =========================================================== learn

ENGAGEMENT_QUESTIONS = {
    "basis": "Are these books on the accrual basis or the cash basis?",
    "end_use": "What are these books for: a tax return, a loan, a sale, a raise?",
    "deadline": "What date do they have to be finished by?",
    "materiality": ("Below what amount is a difference not worth chasing? "
                    "Say 0 to report everything."),
}


def account_months(ledger, account):
    if account is None:
        return set()
    aliases = {norm_text(v) for v in (account.key, account.name, account.full_name,
                                      account.number) if v}
    return {line.month for line in ledger.lines
            if norm_text(line.account) in aliases
            or norm_text(getattr(line, "account_full", "")) in aliases}


def infer_specs(ledger):
    """One AccountSpec per REAL bank and card account in the chart.

    The mask is the digits at the end of the account name, which is how most
    companies name these accounts and how most banks name their files. An
    account with no mask still gets a spec, because an account nobody has ever
    sent a statement for has to show up as missing rather than not show up.

    A spec is a demand for a statement, every month, for the whole period, so it
    is only ever written for an account a statement could exist for. Two kinds
    of chart row are excluded and both were on one real chart:

      - a parent rollup (`100000 Current Assets`, `220000 Credit Cards`), whose
        balance the children already carry and which no bank issues a statement
        for;
      - a row that has never posted and holds no balance (`107000 Bank Account
        7`, `223000` to `227000 Credit Card 3` to `7`, two Bill.com clearing
        accounts left over from a chart template).

    Writing specs for all 21 rows demanded 504 account-months of statements when
    6 accounts had ever posted. An open item that cannot be closed is not an
    open item; it is noise that hides the four real ones.
    """
    specs = []
    for acct in sorted(ledger.accounts.values(),
                       key=lambda a: (a.number or "", a.full_name)):
        if acct.role not in ("bank", "card"):
            continue
        if not is_real_account(ledger, acct):
            continue
        masks = _MASK_RE.findall(acct.name)
        specs.append(AccountSpec(
            book=acct.key,
            label=acct.label() or acct.full_name,
            mask=masks[-1] if masks else "",
            kind="card" if acct.role == "card" else "bank",
            feed="live",
            parser="generic_csv",
        ))
    return specs


def cmd_learn(args, work):
    ledger = load_ledger(work, args.exports)
    out = Path(args.out) if args.out else (work.root / PROFILE_DEFAULT)
    if not out.is_absolute():
        out = Path.cwd() / out

    since = parse_date(args.since, field="--since") if args.since else None
    mining = precedent.mine_rules(
        ledger.lines, ledger.accounts,
        min_support=args.min_support, min_confidence=args.min_confidence, since=since,
    )

    prof = Profile(path=str(out))
    prof.entity = Entity(
        name=ledger.company or "",
        basis=(ledger.basis or ""),
        fiscal_year=str(ledger.period_end.year) if ledger.period_end else "",
    )
    prof.chart = [{
        "number": a.number, "name": a.name, "full_name": a.full_name,
        "type": a.type, "detail_type": a.subtype, "role": a.role,
    } for a in sorted(ledger.accounts.values(),
                      key=lambda a: (a.number or "", a.full_name))]
    prof.accounts = infer_specs(ledger)
    prof.what_goes_where = mining.rules

    # Where activity stops, per account. An account that went quiet while the
    # rest of the books kept going is the shape of a feed that died, and it is
    # also the shape of an account nobody used. Only the owner knows which, so
    # this labels what it found as inferred and asks.
    overall = max((l.month for l in ledger.lines), default="")
    stops, dead = [], []
    for spec in prof.accounts:
        months = sorted(account_months(ledger, ledger.account(spec.book)))
        spec.feed_last = months[-1] if months else ""
        stops.append((spec, months[-1] if months else "never"))
        if overall and (not months or months[-1] < overall):
            last = months[-1] if months else overall
            gap = len(month_range(dt.date(int(last[:4]), int(last[5:7]), 1),
                                  dt.date(int(overall[:4]), int(overall[5:7]), 1))) - 1
            if gap >= 1:
                spec.feed = "dead"
                spec.note = (f"inferred: last posted {months[-1] if months else 'never'}, "
                             f"{gap} month(s) before the books stop at {overall}")
                dead.append((spec, months[-1] if months else "never", gap))

    questions = list(mining.questions())
    for spec, last, gap in dead:
        questions.append({
            "id": f"feed-{norm_text(spec.book).replace(' ', '-') or 'account'}",
            "question": (
                f"{spec.label} has nothing posted after {last}, while the rest of the "
                f"books run to {overall}. Did that bank feed stop, or did the account "
                f"stop being used?"),
            "why": "decides whether those months are a gap to fill or genuinely empty",
            "answer": "",
        })
    for mask, specs in sorted(shared_masks(prof).items()):
        questions.append({
            "id": f"duplicate-mask-{mask}",
            "question": (
                f"{len(specs)} accounts in the chart end in {mask}: "
                + ", ".join(s.label for s in specs)
                + ". Is that one real account entered twice, and if so which one is "
                  "the real one?"),
            "why": ("no statement file name can tell them apart, and merging accounts "
                    "has to happen before any feed is disconnected or the backlog on "
                    "the other one is discarded"),
            "answer": "",
        })
    for field in prof.entity.blanks():
        questions.append({
            "id": f"entity.{field}",
            "question": ENGAGEMENT_QUESTIONS[field],
            "why": "decides what counts as a defect in these books",
            "answer": "",
        })
    prof.open_questions = questions
    prof.notes = (f"Learned on {today_iso()}. Every rule names the rows in this "
                  f"company's own posted history that it came from. The roles on the "
                  f"chart are a starting point and a human may override any of them.")

    profile_mod.validate(prof)
    save_profile(prof, out)

    # ------------------------------------------------------------- report
    months_active = ledger.months_with_activity()
    all_months = (month_range(ledger.period_start, ledger.period_end)
                  if ledger.period_start and ledger.period_end else list(months_active))
    roles = {}
    for a in ledger.accounts.values():
        roles[a.role] = roles.get(a.role, 0) + 1
    unknown_type = [a.label() for a in ledger.accounts.values() if not a.type_known]

    head(f"{ledger.company or 'This company'}: what the books look like")
    bullet("chart", n_of(len(ledger.accounts), len(ledger.accounts), "accounts")
           + "  (" + ", ".join(f"{k} {v}" for k, v in sorted(roles.items())) + ")")
    if unknown_type:
        bullet("unrecognized type",
               n_of(len(unknown_type), len(ledger.accounts), "accounts") + ": "
               + ", ".join(unknown_type[:4]) + (" ..." if len(unknown_type) > 4 else ""))
        say("    Every sign decision about those is a guess. Check their type in "
            "QuickBooks.")
    bullet("history", f"{len(ledger.lines):,} posted lines, "
                      f"{iso(ledger.period_start)} to {iso(ledger.period_end)}")
    bullet("trial balance", "foots to 0.00" if ledger.foots else
           f"DOES NOT FOOT, out by "
           f"{fmt(ledger.total_debits() - ledger.total_credits())}")
    bullet("months with activity", n_of(len(months_active), len(all_months), "months"))
    empty = [m for m in all_months if m not in months_active]
    if empty:
        bullet("months with none", n_of(len(empty), len(all_months), "months") + ": "
               + ", ".join(empty[:6]) + (" ..." if len(empty) > 6 else ""))
        say("    Months of silence in the middle of a year is what an abandoned book "
            "looks like.")
    bullet("activity stops", overall or "nothing is posted at all")

    head("Where each bank and card account stops")
    table([[spec.label, last, spec.feed] for spec, last in stops],
          ["account", "last posted", "feed"])
    say("")
    if dead:
        say(n_of(len(dead), len(prof.accounts), "feeds look dead") + ":")
        for spec, last, gap in dead:
            say(f"    {spec.label}: last posted {last}, {gap} month(s) short of {overall}")
        say("  Inferred from the books rather than asserted, so it is in the questions.")
    else:
        say(n_of(0, len(prof.accounts), "feeds look dead"))
    if shared_masks(prof):
        say("")
        warn_shared_masks(prof)

    head("What was learned from your own history")
    for line in mining.describe().split("\n"):
        say("  " + line)
    say("")
    say(f"  Replay accuracy {pct(mining.accuracy)} is measured IN SAMPLE: on the very rows")
    say("  the rules were mined from, not on rows they have not seen. So it is a floor,")
    say("  not a forecast. A rule set that cannot reproduce its own evidence is broken,")
    say("  and this catches that; it says nothing about how these rules will code next")
    say("  month, and the true rate on new rows will be lower than this number.")

    head("What is needed from the owner")
    say("  " + n_of(len(questions), len(questions), "open questions")
        + ". Write them out with:")
    say("      python3 bin/books.py questions --round 1")
    blanks = prof.entity.blanks()
    if blanks:
        say("")
        say("  " + n_of(len(blanks), 4, "engagement facts are blank") + ". They decide")
        say("  what \"correct\" means here and none can be guessed from an export. Books")
        say("  that are fine for a tax return can be unfit for a sale. Categorizing is")
        say("  blocked until they are answered:")
        for field in blanks:
            say(f"      python3 bin/books.py answer entity.{field} \"...\"")
            say(f"          {ENGAGEMENT_QUESTIONS[field]}")

    say("")
    say(f"Profile written to {out}")
    return OK


# =========================================================== tieout

def cmd_tieout(args, work):
    prof = load_profile(work, args.profile)
    # `note_when_empty=False`: this command prints the whole request when the
    # folder is empty, so a one-line pointer to it above would be noise.
    parsed, failures, _ = load_statements(work, args.statements, prof, require=False,
                                          note_when_empty=False)
    if not parsed:
        # The three-way tie-out IS the statement check: opening plus deposits
        # minus withdrawals against the statement's own closing balance. There
        # is nothing to do without one, so this still refuses. What it refuses
        # WITH is the difference: the exact accounts and months to go and get,
        # and why each one is needed, instead of the news that a folder is
        # empty. The trial balance tie-out needs no statement and lives in
        # `check`, which now runs without one.
        try:
            ledger = load_ledger(work, args.exports)
        except Refusal:
            raise Refusal(
                "there are no statements to tie out, and no exports either, so the "
                "request for statements cannot be worked out.\n"
                "  Put the QuickBooks exports in exports/ and run `books.py learn`, "
                "then `books.py statements`.")
        request, folder = build_statement_request(work, args, prof, ledger)
        head(f"Nothing to tie out yet: {work.rel(folder)}/ holds no statements")
        say_lines(request.render(limit=12))
        path = write_statement_request(work, request)
        say("")
        say(f"Written to {work.rel(path)}.")
        say("")
        raise Refusal(
            "a tie-out compares the books against a statement, so it cannot run "
            "without one.\n"
            "  The request above says which accounts and which months. Meanwhile these "
            "run with no statements at all:\n"
            "      python3 bin/books.py check        the trial balance tie-out and the "
            "other exit tests\n"
            "      python3 bin/books.py reconcile    every account's book balance, with "
            "each month marked unchecked")
    for s in parsed:
        if s.tie_out is None:
            s.check()

    # Which side each account's balances belong on, from the profile's own
    # declaration of what the account IS. A bank is debit-normal and a card is
    # credit-normal, and a card statement that reads "4,120.55 Cr" is money
    # owed, which is what the cardholder means when they say their balance.
    # Deposits and withdrawals are positive magnitudes and carry no side; the
    # difference keeps its sign, because it is not a balance.
    normal = {}
    for spec in (prof.accounts or []):
        side = sides.normal_side_of_kind(spec.kind)
        if side:
            normal[spec.book] = side

    rows, ties = [], 0
    for s in sorted(parsed, key=lambda s: (s.account_key, s.month, s.source_file)):
        t = s.tie_out
        if t.ties:
            ties += 1
        side = normal.get(s.account_key, "")
        rows.append([
            s.account_key or "(no account)", t.month or "?", f"{t.rows:,}",
            balance(t.opening, side), fmt(t.deposits), fmt(t.withdrawals),
            balance(t.computed_closing, side),
            "" if t.stated_closing is None else balance(t.stated_closing, side),
            fmt(t.difference), "yes" if t.ties else "NO",
        ])

    head("Opening + deposits - withdrawals = closing, per account per month")
    table(rows, ["account", "month", "rows", "opening", "deposits", "withdrawals",
                 "computed", "stated", "difference", "ties"])
    say("")
    say("  Balances read on the account's own side. Deposits and withdrawals are")
    say("  magnitudes; the difference keeps its sign, because it is not a balance.")
    say("")
    say(n_of(ties, len(parsed), "account-months tie"))
    for s in parsed:
        for note in s.notes:
            say(f"    {os.path.basename(s.source_file)}: {note}")

    breaks = tieout.chain_breaks(parsed)
    head("Chain breaks: one month's closing is not the next month's opening")
    if breaks:
        for b in breaks:
            say(f"  {b['note']}")
        say("")
        say(n_of(len(breaks), max(len(parsed) - 1, 0), "consecutive pairs break"))
        say("  This is how a missing statement shows up even when every statement you")
        say("  do have ties.")
    else:
        say("  " + n_of(0, max(len(parsed) - 1, 0), "consecutive pairs break"))

    problems = (len(parsed) - ties) + len(breaks) + len(failures)
    if problems:
        say("")
        say(f"NOT COMPLETE: {len(parsed) - ties} month(s) do not tie, {len(breaks)} chain "
            f"break(s), {len(failures)} unreadable file(s).")
        say("A difference is never plugged. Report it as a number and find it: an")
        say("adjustment that makes it disappear destroys the only signal you had.")
        return FAILURE
    say("")
    say("Every statement ties and the chain is continuous, so you have every")
    say("transaction the bank has. Categorizing is now worth starting.")
    return OK


# =========================================================== coverage

def _year_bounds(prof):
    fy = str(getattr(prof.entity, "fiscal_year", "") or "").strip()
    if re.fullmatch(r"\d{4}", fy):
        return f"{fy}-01", f"{fy}-12"
    return None, None


def cmd_coverage(args, work):
    prof = load_profile(work, args.profile)
    d = resolve_dir(work, args.statements, "statements",
                    "one statement per account per month")
    declared = [spec.label or spec.book for spec in (prof.accounts or [])]
    if not declared:
        raise Refusal(
            "the profile declares no bank or card accounts, so there is nothing to\n"
            "  check coverage against. Declaring the accounts is what makes this a gate\n"
            "  rather than a description of whatever happens to be there.\n"
            "  Run `books.py learn` first, or add them under \"accounts\" in the profile."
        )

    # The window is the period the books cover, which is a fact in the exports.
    # Falling back to the fiscal year, and then to the span of whatever
    # statements happen to be present, is progressively weaker: the last one can
    # only report what IS there and can never tell you about a missing month at
    # the end of the period.
    start, end, window_from = args.start, args.end, "you gave it"
    if not (start and end):
        try:
            ledger = load_ledger(work, args.exports)
        except Refusal:
            ledger = None
        if ledger is not None and ledger.period_start and ledger.period_end:
            start = start or month_key(ledger.period_start)
            end = end or month_key(ledger.period_end)
            window_from = "the period your exports cover"
        else:
            fy_start, fy_end = _year_bounds(prof)
            start, end = start or fy_start, end or fy_end
            window_from = ("the fiscal year in the profile" if start else
                           "the span of the statements found, which cannot tell you "
                           "about a missing month at either end")

    grid = coverage_mod.coverage(str(d), accounts=declared, start=start, end=end)

    # The whole grid and every missing account-month go to the file. The
    # terminal gets the grid and the first few of each list, because the missing
    # list on a real run was 504 items on one semicolon-separated line.
    md = write_text(work.dir("reports", create=True) / "statement-coverage.md",
                    grid.render() + "\n")

    head("Which account-months you hold a statement for")
    say(grid.render(limit=args.limit))
    total = len(grid.accounts) * len(grid.months)
    held = total - len(grid.missing)
    say("")
    bullet("window", (f"{grid.months[0]} to {grid.months[-1]}" if grid.months else "empty")
           + f"  ({window_from})")
    warn_shared_masks(prof)
    say(n_of(held, total, "account-months held"))
    say(f"Written to {work.rel(md)}")
    if grid.missing:
        say(n_of(len(grid.missing), total, "account-months have no statement")
            + ". A missing statement means \"cannot check\", never zero.")
        return FAILURE
    return OK


def shared_masks(prof):
    """Declared accounts that share a mask, which no file name can tell apart."""
    by_mask = {}
    for spec in (prof.accounts or []):
        if spec.mask:
            by_mask.setdefault(str(spec.mask), []).append(spec)
    return {mask: specs for mask, specs in by_mask.items() if len(specs) > 1}


def warn_shared_masks(prof):
    shared = shared_masks(prof)
    if not shared:
        return
    for mask, specs in sorted(shared.items()):
        say(f"  {len(specs)} declared accounts share the mask {mask}: "
            + ", ".join(s.label for s in specs))
    say("  No file name can tell those apart, so every statement for that mask binds")
    say("  to the first of them. That is usually one real account entered twice in the")
    say("  chart, which is a structural fix and not a categorizing one.")
    say("")


# =========================================================== statements

def build_statement_request(work, args, prof, ledger, recon=None):
    """The request, and the folder it was measured against.

    The coverage grid is what stops a month already in the folder being asked
    for a second time, which is the single fastest way to lose a founder's
    goodwill in a catch-up.
    """
    d = work.dir("statements", create=True)
    if getattr(args, "statements", None):
        given = Path(args.statements)
        if not given.is_absolute():
            given = Path.cwd() / given
        if given.is_dir():
            d = given
    declared = [spec.label or spec.book for spec in (prof.accounts or [])]
    grid = None
    if declared and ledger.period_start and ledger.period_end:
        try:
            grid = coverage_mod.coverage(
                str(d), accounts=declared,
                start=month_key(ledger.period_start), end=month_key(ledger.period_end))
        except (OSError, ValueError):
            grid = None
    request = statement_request.build(
        ledger, prof, coverage_grid=grid, recon=recon,
        statements_dir=work.rel(d) + "/")
    return request, d


def write_statement_request(work, request):
    return write_text(work.dir("reports", create=True) / "statement-request.md",
                      _with_footer(request.render_markdown()))


def cmd_statements(args, work):
    prof = load_profile(work, args.profile)
    ledger = load_ledger(work, args.exports)
    request, folder = build_statement_request(work, args, prof, ledger)

    head(f"Statements needed, {request.company or 'this company'}")
    say_lines(request.render(limit=args.limit))
    path = write_statement_request(work, request)
    say("")
    say(f"Written to {work.rel(path)}. Forward that file, or work it in the banking")
    say("portal; it names the account and the months and says why each one is needed.")
    if request.excluded:
        say("")
        say(n_of(len(request.excluded), len(request.excluded) + len(request.asks)
                 + len(request.settled), "chart rows are not real accounts")
            + " and no statement is asked for them: "
            + ", ".join(label for label, _ in request.excluded[:5])
            + (" ..." if len(request.excluded) > 5 else ""))
        say("  A parent that totals its children, and a template row nothing was ever")
        say("  posted to. Asking for those asks for a document that does not exist.")
    say("")
    say(f"Put them in {work.rel(folder)}/ and run `books.py tieout`, then `reconcile`.")
    return OK


# =========================================================== catchup

def require_engagement(prof):
    blanks = prof.entity.blanks()
    if not blanks:
        return
    lines = [
        "these books cannot be categorized yet: " + ", ".join(blanks)
        + (" is" if len(blanks) == 1 else " are") + " not answered.",
        "",
        "  Basis, end use, deadline and materiality decide what counts as a defect.",
        "  Books that are fine for a tax return can be unfit for a sale, so guessing",
        "  them produces confident, wrong work. Ask the owner, then:",
        "",
    ]
    for field in blanks:
        lines.append(f"      python3 bin/books.py answer entity.{field} \"...\"")
        lines.append(f"          {ENGAGEMENT_QUESTIONS[field]}")
    raise Refusal("\n".join(lines))


def cmd_catchup(args, work):
    prof = load_profile(work, args.profile)
    require_engagement(prof)
    live = load_live(work)
    year = require_filing_year(live)
    ledger = load_ledger(work, args.exports)
    everything, queue_files, _ = load_queue(work, args.for_review, prof)
    if not everything:
        raise Refusal("the For Review exports hold no transactions to work.")

    # The queue is not the job. What this deadline needs is the job, and the
    # rest is written out in full rather than mixed in or dropped.
    scoped = analyze_scope(
        everything, filing_year=year,
        reconciled_through=reconciled_through_map(prof, live),
        history_of=history_map(ledger, prof, everything))
    lines = list(scoped.in_scope)
    write_json(work / SCOPE_FILE, {
        "filing_year": year, "due": filing_due(live, prof), "written": today_iso(),
        "total": scoped.total, "in_scope": len(scoped.in_scope),
        "deferred": len(scoped.deferred), "counts": scoped.counts,
        "by_account": scoped.by_account(),
    })
    deferred_md = write_text(work / DEFERRED_FILE,
                             _render_deferred(prof, scoped, year))
    if not lines:
        raise Refusal(
            n_of(0, scoped.total, f"queue rows belong to the {year} return") + ".\n"
            "  Every row is dated outside the year or inside a month that already\n"
            f"  reconciled. They are listed in {work.rel(deferred_md)}.\n"
            "  Change the year if that is the wrong one:\n"
            "      python3 bin/books.py filing-year 2026"
        )

    proposals = matching.classify(lines, ledger, prof, window_days=args.window_days)
    counts = {}
    for p in proposals:
        counts[p.action] = counts.get(p.action, 0) + 1
    flagged = sum(1 for p in proposals if p.needs_human)

    size = max(1, int(args.batch_size))
    batches = [proposals[i:i + size] for i in range(0, len(proposals), size)]
    review_dir = work.dir("review", create=True)

    written, skipped = [], []
    for i, batch in enumerate(batches, start=1):
        tag = f"{i:02d}"
        path = review_dir / f"batch-{tag}.xlsx"
        approved = approval_state(work, tag)[0] if path.exists() else False
        if approved and not args.force:
            skipped.append(tag)
            continue
        months = sorted({month_key(p.line.date) for p in batch})
        rf = review_workbook.write_review(path, batch, tag, {
            "company": ledger.company or prof.entity.name,
            "period": f"{months[0]} to {months[-1]}" if months else "",
            "basis": prof.entity.basis,
            "what these books are for": prof.entity.end_use,
            "built": today_iso(),
            "rows already in the books (match)": sum(1 for p in batch if p.action == "match"),
            "questions in this batch": sum(1 for p in batch if p.action == "question"),
        })
        written.append((tag, rf))

    # The questions the categorizer parked, kept for `books.py questions`.
    parked, seen = [], set()
    for p in proposals:
        if p.action != "question":
            continue
        qid = (f"row-{norm_text(p.line.descriptor)[:40].replace(' ', '-') or 'unnamed'}"
               f"-{iso(p.line.date)}")
        if qid in seen:
            continue
        seen.add(qid)
        parked.append({
            "id": qid,
            "question": p.question or f"What is {p.line.descriptor!r} for?",
            "why": p.source,
            "date": iso(p.line.date),
            "amount": plain(p.line.amount),
            "account": p.line.account_key,
            "answer": "",
        })
    write_json(work / "reports" / "parked-questions.json", parked)

    head(f"What the {year} return needs from this queue")
    bullet("rows in the queue", f"{scoped.total:,} transactions from "
                                + n_of(len(queue_files), len(queue_files), "files"))
    bullet("this deadline needs", n_of(len(lines), scoped.total, "rows"))
    bullet("set aside for now", n_of(len(scoped.deferred), scoped.total, "rows"))
    for reason, count in sorted(scoped.counts.items(), key=lambda kv: -kv[1]):
        say(f"      {count:,} {DEFER_REASONS.get(reason, reason)}. "
            + _defer_meaning(reason, year) + ".")
    if scoped.deferred:
        say(f"      Listed in full in {work.rel(deferred_md)}. Nothing is dropped, and")
        say(f"      they come back when their own deadline does.")

    head("What the in-scope rows are")
    for action in ("add", "match", "transfer", "question"):
        bullet(action, n_of(counts.get(action, 0), len(proposals), "rows"))
    say("    match means it is already in the books. Adding it again would count it")
    say("    twice, and doubled revenue is harder to find later than a gap.")
    if flagged:
        bullet("needs a human", n_of(flagged, len(proposals), "rows")
               + " whose bank description holds text aimed at an automated system")
    guessed = sum(1 for p in proposals if p.action == "add" and not p.rule_id)
    bullet("guessed accounts", n_of(guessed, len(proposals), "rows"))

    head("Batches written")
    if written:
        table([[f"batch-{tag}", work.rel(rf.path), f"{rf.rows:,}", f"{rf.needs_human:,}"]
               for tag, rf in written],
              ["batch", "file", "rows", "needs a human"])
    if skipped:
        say("  " + n_of(len(skipped), len(batches), "batches were left alone")
            + " because they are already approved: "
            + ", ".join(f"batch-{t}" for t in skipped))
        say("  Rewriting an approved batch would silently void the approval. Pass")
        say("  --force if that is what you want.")
    say("")
    say(n_of(sum(rf.rows for _, rf in written), len(proposals),
             "rows written into batches"))

    head("What happens next")
    first = written[0][0] if written else "01"
    say("  1. Send the owner the first workbook. The Why column is the point: a row")
    say("     saying \"12 of 12 past payments to this vendor went to Software\" is a")
    say("     claim they are checking, not data entry. They touch only the rows they")
    say("     disagree with, plus the questions.")
    say("  2. They fill in founder_decision, save, and run this themselves:")
    say(f"         python3 bin/books.py approve batch-{first}")
    say("  3. Then, and only then:")
    say(f"         python3 bin/books.py build-imports --batch batch-{first}")
    return OK


# =========================================================== approve


def approve_browser_batch(args, work, tag):
    """Approve a batch that will be clicked into live books rather than uploaded.

    The difference from a workbook approval is what happens next, and it is
    worth saying out loud in the output: there is no file to re-read before it
    lands, and no upload step where somebody looks once more. The next thing
    after this command is an agent working in the books.
    """
    batch = load_plan(work, tag)
    ap = write_approval(work.dir("review", create=True), tag,
                        approved_by=whoami(args.by), rows=len(batch.rows))

    head(f"batch-{tag} approved")
    bullet("what it does", {
        "categorize": "sets the category, class and payee on rows in For Review, "
                      "and accepts them",
        "add": "adds transactions your statements show and your feed never "
               "delivered",
        "journal": "posts journal entries",
    }[batch.kind])
    bullet("account", batch.account or "several")
    bullet("rows", f"{len(batch.rows):,}")
    bullet("company", batch.company or "(none recorded)")
    bullet("approved by", ap.approved_by)
    bullet("at", ap.approved_at)
    bullet("what you read", work.rel(plan_markdown_path(work, tag)))
    flagged = [r for r in batch.rows if r.needs_human]
    if flagged:
        say("")
        say("  " + n_of(len(flagged), len(batch.rows), "rows carry a description "
                        "with text in it aimed at an automated system")
            + ". Whoever sent the money chose that text, and it was quoted rather "
              "than acted on.")

    head("What you just did")
    say("  You took responsibility for these rows reaching your books. That is "
        "what an approval is, and it is why no agent can run this command.")
    say("")
    say("  This one is different from approving an import file. There is no "
        "upload step after it, and no second look at a file before it lands. "
        "The next thing that happens is your agent working in your books, in "
        "front of you.")
    say("")
    say("  The approval covers these rows and nothing else. Rebuild the batch, "
        "or change a row in it, and it stops covering anything until you look "
        "again. It also covers only this batch: the next one gets its own.")

    head("What happens next")
    say("  Your agent reads the count on the screen, opens the run, and works "
        "the rows:")
    say(f"      python3 bin/books.py browser post batch-{tag} --before N")
    say("")
    say("  Then it reads the count again and proves it moved by exactly "
        f"{len(batch.rows):,}:")
    say(f"      python3 bin/books.py browser verify batch-{tag} --after N")
    return OK



def cmd_approve(args, work):
    tag = normalize_batch(args.batch)
    wb = batch_workbook(work, tag)
    if not wb.exists() and plan_path(work, tag).exists():
        # A browser batch is reviewed as a plan file rather than a workbook. The
        # approval it produces is the same object, written the same way, and it
        # goes stale the same way when the batch changes underneath it.
        return approve_browser_batch(args, work, tag)
    if not wb.exists():
        raise Refusal(
            f"there is no batch called batch-{tag}.\n"
            f"  Batches that exist: "
            + (", ".join(f"batch-{t}" for t in
                         sorted(set(known_batches(work)) | set(known_plans(work))))
               or "none yet")
            + "\n  Build them with `books.py catchup --batch-size 150`, or with\n"
              "  `books.py browser plan --kind categorize` to work in the browser."
        )
    decisions = review_workbook.read_decisions(wb)
    bad = review_workbook.unrecognized(decisions)
    if bad:
        raise Refusal(
            n_of(len(bad), len(decisions), f"rows in {wb.name}") + " carry a decision "
            "that is not one of the four.\n"
            "  Rows: " + ", ".join(str(d["row"]) for d in bad[:12])
            + ("" if len(bad) <= 12 else " ...") + "\n"
            "  Allowed: approve, change account, skip, answer. That usually means the\n"
            "  dropdown was lost on a copy and paste. Fix those cells and run this again."
        )
    decided = [d for d in decisions if d["decision"]]
    if not decided:
        raise Refusal(
            f"{wb.name} has no decisions in it yet: "
            + n_of(0, len(decisions), "rows carry one") + ".\n"
            "  Open it, fill in the founder_decision column, save, then approve."
        )

    counts = {}
    for d in decisions:
        key = d["decision"] or "(blank)"
        counts[key] = counts.get(key, 0) + 1
    flagged = [d for d in decisions if d["needs_human"]]

    ap = write_approval(work.dir("review", create=True), tag,
                        approved_by=whoami(args.by), rows=len(decisions))

    head(f"batch-{tag} approved")
    bullet("workbook", work.rel(wb))
    bullet("approved by", ap.approved_by)
    bullet("at", ap.approved_at)
    bullet("rows", n_of(len(decided), len(decisions), "rows carry a decision"))
    say("")
    table([[k, f"{v:,}", pct(v / len(decisions))] for k, v in sorted(counts.items())],
          ["decision", "rows", "share"])
    if flagged:
        undecided = [d for d in flagged if not d["decision"]]
        say("")
        say("  " + n_of(len(flagged), len(decisions), "rows were marked NEEDS HUMAN")
            + (f", {len(undecided)} of them still blank" if undecided else ""))
    if counts.get("(blank)"):
        say("")
        say("  " + n_of(counts["(blank)"], len(decisions), "rows are still blank")
            + ". A blank row is not approved and nothing gets built from it.")

    head("What you just did, and what happens next")
    say("  You have taken responsibility for the decisions in this file. That is what")
    say("  an approval is, and it is why no agent can run this command for you.")
    say("")
    say("  The approval is tied to the contents of the workbook. Change a date, an")
    say("  amount, an account or a decision in it and the approval stops covering it,")
    say("  and everything downstream stops until you look again.")
    say("")
    say("  Now build the import files:")
    say(f"      python3 bin/books.py build-imports --batch batch-{tag}")
    say("")
    say("  Then, in QuickBooks: Banking, Rules, Import rules. Then Banking, Upload")
    say("  transactions for any gap months. Then work the For Review tab and accept.")
    say("  Nothing has reached your books until you do that yourself.")
    return OK


# =========================================================== build-imports

def cmd_build_imports(args, work):
    tag = normalize_batch(args.batch)
    prof = load_profile(work, args.profile)
    wb = batch_workbook(work, tag)
    if not wb.exists():
        raise Refusal(f"there is no workbook for batch-{tag} at {wb}. Nothing to build.")
    ok, message = approval_state(work, tag)
    if not ok:
        raise Refusal(message)

    decisions = review_workbook.read_decisions(wb)
    accepted = [d for d in decisions if d["decision"] in ("approve", "change account")]
    ledger = None
    try:
        ledger = load_ledger(work, args.exports)
    except Refusal:
        pass                      # the rules file does not need the exports
    resolve = resolver_for(ledger, prof)

    # The rules this batch actually leaned on, and only those. A rules file
    # holding a rule no approved row used is a rule nobody agreed to.
    used_ids = set()
    for d in accepted:
        rid = (d["why"] or "").split(":", 1)[0].strip()
        if rid:
            used_ids.add(rid)
    rules = [r for r in prof.rules() if r.id in used_ids]

    import_dir = work.dir("import", create=True)
    written, notes = [], []
    if rules:
        target = import_dir / f"rules-batch-{tag}.xlsx"
        result = gated_write(work, target, lambda p: rules_xlsx.write_rules(
            p, rules, template=args.rules_template, resolve=resolve, auto_add=False))
        written.append([work.rel(result.path), f"{int(result)} rule(s)", result.kind])
        if getattr(result, "hand_path", ""):
            written.append([work.rel(result.hand_path), f"{int(result)} rule(s)",
                            "the same rules, to type"])
        notes = list(getattr(result, "notes", []))

    head(f"Built from batch-{tag}")
    bullet("approval", message)
    bullet("rows approved", n_of(len(accepted), len(decisions), "rows"))
    bullet("rules used", n_of(len(rules), len(prof.rules()), "rules in the profile"))
    say("")
    if written:
        table(written, ["file", "holds", "kind"])
    else:
        say("  Nothing to write: no approved row leaned on a rule.")
    say("")
    say(n_of(len(written), len(written), "files written into import/"))
    say(n_of(0, len(accepted), "approved rows needed a transaction upload")
        + ". Every row in this batch is already in your For Review queue, which is")
    say("where you accept it. Uploading it as well would book it twice. The months")
    say("your feed MISSED are a different job: `books.py fill-gaps`.")
    for note in notes:
        say(f"    note: {note}")

    if rules and not args.rules_template:
        head("Why the rules file is a list and not an import file")
        say("  QuickBooks' rules template carries its own encoding in the condition and")
        say("  output columns. A guessed encoding either fails at import or imports a")
        say("  rule that does not say what you meant.")
        say("  Export your own rules first (Transactions, Rules, the New rule dropdown,")
        say("  Export rules), then:")
        say(f"      python3 bin/books.py build-imports --batch batch-{tag} \\")
        say("          --rules-template path/to/your-rules.xlsx")

    head("In QuickBooks, in this order")
    say("  1. Banking, Rules, Import rules. Rules apply to transactions as they arrive")
    say("     and never reach back into what is already sitting in For Review.")
    say("  2. Banking, Upload transactions, for any gap months.")
    say("  3. For Review: select the rows, Batch actions, Modify selected, then accept.")
    say("")
    say("  Auto-add is off on every rule this writes, deliberately. A rule that")
    say("  auto-adds posts without you seeing it, which defeats the entire design.")
    return OK


# =========================================================== fill-gaps

def gap_months(ledger, prof, statements, only_account=""):
    """Account-months where the statements have rows and the books have none.

    That is a feed gap defined without anyone's memory: the bank says something
    happened, and the books are silent about it.
    """
    out = {}
    for spec in (prof.accounts or []):
        if only_account and norm_text(only_account) not in {
                norm_text(spec.book), norm_text(spec.label), norm_text(spec.mask)}:
            continue
        booked = account_months(ledger, ledger.account(spec.book)) if ledger else set()
        for s in statements:
            if norm_text(s.account_key) != norm_text(spec.book):
                continue
            for line in s.lines:
                m = month_key(line.date)
                if m not in booked:
                    out.setdefault((spec.book, m), []).append(line)
    return out


def cmd_fill_gaps(args, work):
    prof = load_profile(work, args.profile)
    ledger = load_ledger(work, args.exports)
    parsed, _, _ = load_statements(work, args.statements, prof)
    for s in parsed:
        if s.tie_out is None:
            s.check()

    not_tying = [s for s in parsed if not s.ties]
    if not_tying and not args.allow_untied:
        raise Refusal(
            n_of(len(not_tying), len(parsed), "statements do not tie") + ".\n"
            "  Uploading transactions you have not proved complete just moves the gap\n"
            "  into the books, where it is harder to see. Run `books.py tieout` and work\n"
            "  the differences first, or pass --allow-untied if you have a reason."
        )

    breaks = tieout.chain_breaks(parsed)
    if breaks:
        say(n_of(len(breaks), max(len(parsed) - 1, 0), "consecutive pairs break")
            + ". Each break is a statement you do not have, so the months around it")
        say("may still be incomplete after this runs:")
        for b in breaks:
            say(f"    {b['note']}")

    gaps = gap_months(ledger, prof, parsed, only_account=args.account)
    if not gaps:
        say(n_of(0, len(parsed), "statement months need uploading")
            + ": every month the statements cover already has transactions in the books.")
        return OK

    tag = normalize_batch(args.batch)
    wb = work.dir("review", create=True) / f"batch-{tag}.xlsx"
    all_lines = []
    for key in sorted(gaps):
        all_lines.extend(sorted(gaps[key], key=lambda l: (l.date, l.source_row)))

    head("Gap months: the bank has rows here and the books have none")
    table([[acct, month, f"{len(rows):,}", fmt(money(sum((l.amount for l in rows), ZERO)))]
           for (acct, month), rows in sorted(gaps.items())],
          ["account", "month", "rows", "net"])
    say("")
    say(f"{len(all_lines):,} transactions to upload across "
        + n_of(len(gaps), len(gaps), "account-months"))

    if not wb.exists() or args.force:
        proposals = matching.classify(all_lines, ledger, prof)
        review_workbook.write_review(wb, proposals, tag, {
            "company": ledger.company or prof.entity.name,
            "period": f"{min(m for _, m in gaps)} to {max(m for _, m in gaps)}",
            "what this is": "transactions from statements, for months your feed missed",
            "built": today_iso(),
        })
        say(f"Review workbook written: {work.rel(wb)}")

    ok, message = approval_state(work, tag)
    if not ok:
        raise Refusal(
            "nothing was written into import/.\n\n  " + message.replace("\n", "\n  ")
            + f"\n\n  Read {work.rel(wb)} first. These rows become transactions in your\n"
              f"  books, so they get the same approval as anything else."
        )

    import_dir = work.dir("import", create=True)
    files, rows_written = [], 0
    for (acct, month), rows in sorted(gaps.items()):
        ordered = sorted(rows, key=lambda l: (l.date, l.source_row))
        chunks = bank_csv.split_for_upload(ordered, layout=args.layout)
        for i, chunk in enumerate(chunks, start=1):
            suffix = "" if len(chunks) == 1 else f"-part{i:02d}"
            safe = re.sub(r"[^A-Za-z0-9]+", "-", str(acct)).strip("-") or "account"
            target = import_dir / f"bank-{safe}-{month}{suffix}-batch-{tag}.csv"
            n = gated_write(work, target,
                            lambda p, c=chunk: bank_csv.write(p, c, layout=args.layout))
            rows_written += n
            files.append([work.rel(target), f"{n:,}"])

    head("Upload files written")
    table(files, ["file", "rows"])
    say("")
    say(n_of(rows_written, len(all_lines), "transactions written into import/")
        + f" across {len(files)} file(s), split to the 1,000 row and 350 KB limits.")
    if args.layout == "three":
        say("Three-column Date, Description, Amount. One signed number cannot be")
        say("transposed; a Credit/Debit pair can.")

    head("In QuickBooks")
    say("  Import your bank rules FIRST if you have any, because rules apply to")
    say("  transactions as they arrive and will not re-categorize what is already")
    say("  sitting in For Review.")
    say("  Then Banking, the account, Link account or the dropdown, Upload from file.")
    say("  Map the columns when asked, then check the count on screen against the count")
    say("  in the file before you accept.")
    say("")
    say("  If an account is being merged or disconnected: accept the pending items on")
    say("  both accounts first. Disconnecting a feed DELETES that account's unreviewed")
    say("  transactions, and QuickBooks will not merge two accounts while either is")
    say("  connected. The other order silently discards the backlog.")
    say("")
    say("  Then run `books.py tieout` again to confirm the books now move in step with")
    say("  the statements.")
    return OK


# =========================================================== entries

def describe_row(row):
    """One readable line out of a generator's residual or remaining dict.

    The generators return dicts on purpose, so a caller can use every field.
    Printing the dict itself at a person is not reporting, it is dumping.
    """
    if not isinstance(row, dict):
        return str(row)
    name = str(row.get("vendor") or row.get("asset") or row.get("account")
               or row.get("clearing_account") or "").strip()
    when = str(row.get("month") or iso(row.get("through")) or "").strip()
    parts = []
    for key in ("opening", "gross", "fees", "refunds", "payouts", "computed_closing",
                "stated_closing", "difference", "total", "amortized_through",
                "remaining", "cost", "accumulated_through", "net_book_value"):
        if key not in row or row[key] is None:
            continue
        parts.append(f"{key.replace('_', ' ')} {fmt(money(row[key]))}")
    head_text = " ".join(x for x in (name, when) if x) or "row"
    line = f"{head_text}: " + ", ".join(parts) if parts else head_text
    if row.get("note"):
        line += f". {row['note']}"
    return line


def is_finding(residual):
    """A residual that is a finding: a stated difference that is not zero.

    A month the processor never stated a closing balance for is unchecked, not
    wrong, and it says so in its own note. Treating the two the same would make
    every clean month look like a defect.
    """
    if not isinstance(residual, dict):
        return True
    difference = residual.get("difference")
    return difference is not None and money(difference) != ZERO



ENTRY_KINDS = ("payroll", "prepaid", "intangibles", "stripe")


def draft_entries(prof, through, since=None, kinds=ENTRY_KINDS):
    """Run each generator the profile has a block for. Returns (drafts, missing)."""
    drafts, missing = [], []
    for kind in kinds:
        block = (prof.recurring_entries or {}).get(kind)
        if block in (None, [], {}, ""):
            missing.append(kind)
            continue
        if kind == "payroll":
            periods = block.get("periods") or block.get("pay_dates") or []
            if not periods:
                missing.append("payroll (the block is there, but it lists no pay periods, "
                               "and gross is never derived from net)")
                continue
            drafts.append(payroll.build(prof, periods))
        elif kind == "stripe":
            months = block.get("months") or block.get("payouts") or []
            if not months:
                missing.append("stripe (the block is there, but it lists none of the "
                               "processor's own monthly figures)")
                continue
            drafts.append(stripe.build(prof, months, through=through))
        elif kind == "prepaid":
            drafts.append(prepaid.build(prof, through, since=since))
        elif kind == "intangibles":
            drafts.append(intangibles.build(prof, through, since=since))
    return drafts, missing


def write_entry_outputs(work, entries, resolve, tag, meta, stem):
    reports = work.dir("reports", create=True)
    xlsx = reports / f"{stem}.xlsx"
    md = reports / f"{stem}.md"
    ws = je_worksheet.write_worksheet(xlsx, entries, resolve=resolve,
                                      batch_tag=tag, meta=meta)
    write_text(md, je_worksheet.render_markdown(entries, resolve=resolve,
                                                batch_tag=tag, meta=meta))
    return ws, xlsx, md


def cmd_entries(args, work):
    prof = load_profile(work, args.profile)
    through = parse_date(args.through, field="--through")
    since = parse_date(args.since, field="--since") if args.since else None
    kinds = (args.kind,) if args.kind else ENTRY_KINDS

    ledger = None
    try:
        ledger = load_ledger(work, args.exports)
    except Refusal:
        pass
    resolve = resolver_for(ledger, prof)

    live = load_live(work)
    year = filing_year_of(live) or through.year
    gate_automation(work, prof, ledger, live, year, "An entry worksheet")

    drafts, missing = draft_entries(prof, through, since=since, kinds=kinds)
    entries = [e for d in drafts for e in d]
    questions = [q for d in drafts for q in d.questions]
    residuals = [r for d in drafts for r in d.residuals]
    remaining = [r for d in drafts for r in d.remaining]
    notes = [n for d in drafts for n in d.notes]

    head(f"Recurring entries through {iso(through)}")
    table([[d.kind, f"{len(d):,}", f"{len(d.questions):,}", fmt(d.total_debits)]
           for d in drafts] or [["(none configured)", "0", "0", fmt(ZERO)]],
          ["kind", "entries", "questions", "total debits"])
    say("")
    say(n_of(len(kinds) - len(missing), len(kinds),
             "kinds of recurring entry are set up"))
    for m in missing:
        say(f"    nothing drafted for {m}")
    if missing:
        say("    Each kind reads a block under recurring_entries in the profile, naming")
        say("    its accounts and the document its entries rest on. The exact shape is")
        say("    at the top of each module in lib/closethebooks/entries/.")

    if not entries:
        say("")
        say(n_of(0, 0, "entries drafted") + ". Nothing was written, which is the honest")
        say("outcome: an entry with no basis is an entry nobody can defend.")
        for q in questions:
            say("")
            for line in q.describe().split("\n"):
                say("  " + line)
        return FAILURE if questions else OK

    tag = str(args.batch or f"entries_{through.strftime('%Y%m')}").replace("-", "_")
    ws, xlsx, md = write_entry_outputs(
        work, entries, resolve, tag,
        {"company": prof.entity.name, "through": iso(through),
         "basis": prof.entity.basis, "built": today_iso()},
        f"entries-{through.strftime('%Y-%m-%d')}")

    head("Worksheet written, built for typing")
    bullet("workbook", work.rel(xlsx))
    bullet("markdown", work.rel(md))
    bullet("entries", f"{ws.entries:,} entries, {ws.lines:,} lines")
    bullet("recurring families", n_of(ws.repeating_groups, ws.groups, "groups")
           + f", covering {ws.repeating_entries:,} entries")
    bullet("typing time", ws.estimate)
    say("")
    say("QuickBooks Online in the United States cannot import journal entries on any")
    say("plan, so these get typed. Where a group is flagged as recurring, type the")
    say("first and use Make recurring for the rest: twelve monthly amortization entries")
    say("are one entry plus a template, and it is the biggest time saver here.")

    findings = [r for r in residuals if is_finding(r)]
    if residuals:
        head("Clearing account, month by month")
        for r in residuals:
            say(f"  {describe_row(r)}")
        say("")
        say(n_of(len(findings), len(residuals),
                 "months leave a residual that is a finding"))
        if findings:
            say("  A residual is a finding, not a rounding. It usually means a fee")
            say("  category is missing from the entry, or a payout was also booked")
            say("  somewhere else. It is never plugged.")
    if remaining:
        head("Balances these entries leave behind, to tie to the balance sheet")
        for r in remaining[:20]:
            say(f"  {describe_row(r)}")
        if len(remaining) > 20:
            say(f"  ... {len(remaining) - 20} more, all of them in the worksheet")
    if notes:
        head("Notes")
        for note in notes:
            say(f"  {note}")
    if questions:
        head(n_of(len(questions), len(questions), "questions") + " that block an entry")
        for q in questions:
            for line in q.describe().split("\n"):
                say("  " + line)

    say("")
    say("Then re-run `books.py check`: the prepaid balance, the accumulated")
    say("amortization and the clearing account are all exit tests, so they will tell")
    say("you whether the entries landed as intended.")
    return FAILURE if (findings or questions) else OK


# =========================================================== reclass

def cmd_reclass(args, work):
    prof = load_profile(work, args.profile)
    ledger = None
    try:
        ledger = load_ledger(work, args.exports)
    except Refusal:
        pass
    resolve = resolver_for(ledger, prof)

    # The SIGNED balance being moved, debit positive, exactly as the trial
    # balance reads it. Never guessed: it comes from the ledger, from a figure
    # the profile already records, or from the person running the command.
    if args.amount is not None:
        amount = money(args.amount, "--amount")
        amount_basis = "given on the command line"
    else:
        figure = prof.figure(args.from_account)
        if figure is not None:
            amount = money(figure)
            amount_basis = f"the known figure the profile records for {args.from_account}"
        elif (ledger is not None and ledger.account(args.from_account) is not None
                and ledger.balance_of(
                    ledger.account(args.from_account).key) is not None):
            acct = ledger.account(args.from_account)
            # The BALANCE, opening position included. A reclass moves the whole
            # of an account, so reading the period's movement here moves the
            # wrong figure and leaves the opening behind, which is how a
            # 3,118,447.25 account gets a 0.00 reclass entry drafted against it.
            amount = money(ledger.balance_of(acct.key))
            amount_basis = (f"the balance of {acct.label()} in your exports "
                            f"({ledger.opening_basis}, plus the movement since)")
        else:
            raise Refusal(
                f"no --amount, no known figure for {args.from_account}, and no\n"
                f"  determinable balance to read. The exports hold movement for it but\n"
                f"  nothing states what it opened at, and movement is not a balance.\n"
                f"  Pass the SIGNED balance as the trial balance reads it: an asset or\n"
                f"  expense positive, a liability, equity or revenue negative. Moving\n"
                f"  250,000 out of an equity account is --amount -250000.00."
            )

    if args.on:
        date = parse_date(args.on, field="--on")
        date_basis = "you chose it"
    elif ledger is not None and ledger.period_end:
        date = ledger.period_end
        date_basis = "the end of the period in your exports, because you did not say"
    else:
        raise Refusal(
            "no --on date, and no exports to take a period end from.\n"
            "  The date decides which period the correction lands in and therefore which\n"
            "  financial statements change. It is never \"today\" by default."
        )

    for which, key in (("--from", args.from_account), ("--to", args.to_account)):
        if not resolve(key):
            known = [r.get("number") or r.get("full_name")
                     for r in (prof.chart or []) if r.get("number") or r.get("full_name")]
            raise Refusal(
                f"{which} {key!r} is not an account in this chart.\n"
                f"  Writing it anyway would make QuickBooks offer to create a new\n"
                f"  account called {key!r}, which is how a chart grows a duplicate.\n"
                f"  Create the account in QuickBooks first, export the Account List\n"
                f"  again and re-run `books.py learn`, or name an account that exists.\n"
                f"  The chart holds {len(known)} accounts, for example: "
                + ", ".join(str(k) for k in known[:8]) + " ..."
            )

    drafted = reclass_mod.build(prof, [{
        "from_account": args.from_account,
        "to_account": args.to_account,
        "amount": amount,
        "reason": args.reason,
        "effective_date": date,
        "source": args.source or "",
        "class": args.klass or "",
    }])
    entry = list(drafted)[0]

    stem = (f"reclass-{date.strftime('%Y-%m-%d')}"
            f"-{re.sub(r'[^A-Za-z0-9]+', '', args.from_account)}"
            f"-to-{re.sub(r'[^A-Za-z0-9]+', '', args.to_account)}")
    ws, xlsx, md = write_entry_outputs(
        work, list(drafted), resolve, f"reclass_{date.strftime('%Y%m%d')}",
        {"company": prof.entity.name, "built": today_iso()}, stem)

    head("The entry")
    table([[resolve(l[0]) or l[0],
            fmt(money(l[1])) if money(l[1]) else "",
            fmt(money(l[2])) if money(l[2]) else ""] for l in entry.lines],
          ["account", "debit", "credit"])
    say("")
    bullet("amount", balance(amount)
           + f"  (a {'debit' if amount > ZERO else 'credit'} balance leaving "
             f"{args.from_account})")
    bullet("amount from", amount_basis)
    bullet("dated", iso(date) + f"  ({date_basis})")
    bullet("basis", entry.basis)
    bullet("worksheet", work.rel(xlsx))
    say("")
    say(n_of(1, 1, "entry drafted") + f", {len(entry.lines)} lines, balanced at "
        f"{fmt(entry.total_debits())}")

    head("Date it deliberately")
    say("  Dating it at the start of the current year leaves an already-filed prior")
    say("  year untouched. Dating it in the prior year restates that year. Both are")
    say("  legitimate, the choice is the owner's, and it should be recorded either way.")
    say("  If that balance was on a statement that has already gone to a lender, an")
    say("  investor or a tax authority, moving it changes something somebody relied on.")

    reserved = list(prof.decisions_reserved_to_founder or [])
    if reserved:
        head("Decisions this profile reserves to the owner")
        for r in reserved:
            say(f"  - {r}")
        say("")
        say("  If this move is one of them, it is theirs to make and not yours to draft")
        say("  past.")

    if args.record:
        prof.decisions_made.append({
            "id": f"reclass-{date.isoformat()}-{args.from_account}-{args.to_account}",
            "decision": (f"{balance(amount)} moved from {args.from_account} to "
                         f"{args.to_account}, effective {iso(date)}. {args.reason}"),
            "on": today_iso(),
            "source": args.source or amount_basis,
        })
        save_profile(prof)
        say("")
        say(f"Recorded in {prof.path} under decisions_made, so the next person to open")
        say("these books, including the owner's accountant, does not have to")
        say("reconstruct the reasoning from the entry.")
    else:
        say("")
        say("Pass --record to write the treatment, its reason and its date into the")
        say("profile's decisions_made.")
    say("")
    say("QuickBooks Online in the US cannot import journal entries, so this one gets")
    say("typed. It is a single entry, so that is a minute of work.")
    return OK


# =========================================================== wind-down

WIND_DOWN_WHY = {
    "target_date": "decides the final period and what the final return covers",
    "receivable_plan": "settled, written off or distributed are three different entries",
    "safe_terms": "what a holder is actually paid decides it, not the carrying amount",
    "cap_table": "decides who receives a final distribution and in what order",
    "resolution_date": "the authority the entries rest on, and it starts statutory clocks",
}


def cmd_wind_down(args, work):
    prof = load_profile(work, args.profile)
    ready, missing_keys = prof.wind_down_ready()
    if not ready:
        head("Refused, and this is the point")
        say("  Liquidating entries rest on facts only the owner has, and they are close")
        say("  to impossible to unwind once the entity is gone. A final set of books is")
        say("  read by people who cannot ask follow-up questions.")
        say("")
        say("  " + n_of(len(missing_keys), 5, "answers are missing") + ":")
        for key in missing_keys:
            say(f"      {key:<18} {WIND_DOWN_WHY.get(key, '')}")
        say("")
        say("  Record each one:")
        for key in missing_keys:
            say(f"      python3 bin/books.py answer wind_down.{key} \"...\"")
        raise Refusal("nothing was drafted.")

    ledger = None
    try:
        ledger = load_ledger(work, args.exports)
    except Refusal:
        pass
    resolve = resolver_for(ledger, prof)

    drafted = wind_down_mod.build(
        prof, date=parse_date(args.on, field="--on") if args.on else None)
    entries = list(drafted)
    total = len(entries) + len(drafted.questions)

    head("Wind-down draft")
    bullet("entries drafted", n_of(len(entries), total, "of what this profile describes"))
    bullet("questions", n_of(len(drafted.questions), total, "could not be drafted"))
    for note in drafted.notes:
        say("")
        say("  " + note)

    if entries:
        ws, xlsx, md = write_entry_outputs(
            work, entries, resolve, "wind_down",
            {"company": prof.entity.name, "built": today_iso()}, "wind-down")
        say("")
        bullet("worksheet", work.rel(xlsx))
        bullet("typing time", ws.estimate)

    if drafted.questions:
        head("Where a treatment depends on a document this profile does not hold")
        for q in drafted.questions:
            for line in q.describe().split("\n"):
                say("  " + line)
        say("")
        say("  A question instead of an entry is the intended behaviour, not a gap.")

    head("Around the entries, and not this tool's job")
    say("  A dissolution is a filing exercise as much as a bookkeeping one: a final")
    say("  return marked final, a statutory notice of the intent to dissolve,")
    say("  dissolution filings in the state of incorporation with taxes current,")
    say("  closing registrations in other states, final payroll filings, and any")
    say("  foreign-subsidiary or foreign-ownership reporting.")
    say("  Several of those carry per-form penalties that dwarf the tax on a company")
    say("  with no profit. Get the list from whoever prepares the return, early: that")
    say("  conversation changes what the books need to show.")
    return FAILURE if drafted.questions else OK


# =========================================================== reconcile

def build_recon(work, args, prof, ledger, quiet=False):
    # `require=False`: a reconciliation with no statements is not a failed run,
    # it is a run where every cell reads "cannot check". That is worth having on
    # its own, because it prints every account's book balance and names every
    # account-month somebody has to go and get.
    parsed, _, _ = load_statements(work, args.statements, prof, quiet=quiet,
                                   require=False)
    start = (parse_date(args.start, field="--from") if getattr(args, "start", None)
             else ledger.period_start)
    end = (parse_date(args.end, field="--to") if getattr(args, "end", None)
           else ledger.period_end)
    if not start or not end:
        raise Refusal(
            "the period could not be worked out from the exports.\n"
            "  Pass it: --from 2025-01-01 --to 2025-12-31"
        )
    return recon_mod.reconcile(ledger, parsed, prof, period_start=start,
                               period_end=end), parsed


def cmd_reconcile(args, work):
    prof = load_profile(work, args.profile)
    ledger = load_ledger(work, args.exports)
    report, _ = build_recon(work, args, prof, ledger)
    md = write_text(work.dir("reports", create=True) / "reconciliation.md",
                    _with_footer(report.render_markdown()))
    stated, total = report.cells_stated()

    head("Book balance against statement balance, per account per month")
    rows = [[r.account, r.month, r.book_cell(), r.statement_cell(),
             r.difference_cell(), r.source_file or ""]
            for r in report.rows if r.checkable or r.externally_verifiable]
    table(rows[:args.limit], ["account", "month", "book", "statement", "difference",
                              "source"])
    if len(rows) > args.limit:
        say(f"  ... {len(rows) - args.limit} more rows, all of them in {work.rel(md)}")

    scanned = sides.scan(ledger)
    say("")
    say_lines(sides.render(scanned, limit=args.limit))

    head("Coverage")
    bullet("cells", n_of(total, total, "account-month cells"))
    bullet("stated", n_of(stated, total, "carry a difference, zeros included"))
    bullet("cannot check", n_of(total - stated, total, "have no statement"))
    bullet("tie at 0.00", n_of(len(report.reconciled), total, "cells"))
    bullet("do not tie", n_of(len(report.unreconciled), total, "cells"))
    bullet("statement missing", n_of(len(report.unchecked_but_expected), total,
                                     "cells for accounts we should hold statements for"))

    if report.unreconciled:
        head("Differences that are not zero. Work these")
        for r in report.unreconciled[:args.limit]:
            say(f"  {r.account}, {r.month}: book {r.book_cell()} against statement "
                f"{r.statement_cell()}, difference {fmt(r.difference)}")
        say("")
        say("  Look in this order, which is roughly the frequency order:")
        say("    1. a timing difference at the period edge. Real, and it resolves next")
        say("       month")
        say("    2. something in the feed still sitting in For Review, not yet booked")
        say("    3. a duplicate, entered by hand and also accepted from the feed")
        say("    4. a transfer booked as income or expense, so it hit the P&L")
        say("    5. a wrong sign or a transposition. A difference divisible by 9 is the")
        say("       classic signature of two transposed digits")
        say("    6. a mis-dated transaction, often a mistyped year")
        say("  None of them is fixed by an entry that makes the difference disappear.")

    if report.unchecked_but_expected:
        head("Statements we do not have. Get these")
        for r in report.unchecked_but_expected[:args.limit]:
            say(f"  {r.account}, {r.month}: cannot check. The book balance reads "
                f"{r.book_cell()}.")
        if len(report.unchecked_but_expected) > args.limit:
            say(f"  ... {len(report.unchecked_but_expected) - args.limit} more")
        say("")
        say("  An unchecked account and a checked account that agrees are not the same")
        say("  thing, and they never render the same here.")

        # The same gap as a request somebody can act on: which accounts, which
        # months, and why each one. A list of unchecked cells says what is
        # missing; this says what to go and get.
        request, _ = build_statement_request(work, args, prof, ledger, recon=report)
        if request:
            head("What to ask for")
            say_lines(request.render(limit=5))
            path = write_statement_request(work, request)
            say(f"  The whole request, account by account, is in {work.rel(path)}.")

    for note in report.notes:
        say("")
        say(note)

    say("")
    say(f"Written to {work.rel(md)}")
    say("Once an account and a month agree here, reconcile it inside QuickBooks too.")
    say("This produces the evidence; QuickBooks' own reconcile screen produces the")
    say("record an accountant, a lender or a buyer will look for.")
    return FAILURE if (report.unreconciled or report.unchecked_but_expected) else OK


# =========================================================== check

ATTESTATION_FILE = "answers/attestation.json"


def load_attestation(work):
    data = read_json(work / ATTESTATION_FILE)
    return exit_tests.Attestation.of(data) if data else None


def cmd_check(args, work):
    prof = load_profile(work, args.profile)
    ledger = load_ledger(work, args.exports)
    report_recon, parsed = build_recon(work, args, prof, ledger)
    attestation = load_attestation(work)

    entries = []
    if args.through:
        try:
            drafts, _ = draft_entries(prof, parse_date(args.through, field="--through"))
            entries = [e for d in drafts for e in d]
        except DraftError:
            entries = []

    report = exit_tests.run(ledger, parsed, prof, report_recon,
                            attestation=attestation, entries=entries)
    # `exit-tests.md` is one of the two files the free reading asks for, and it
    # is the one that gets forwarded, so the offer and the origin travel with it.
    tests_text = report.render_markdown()
    for block in median.blocks(median_signals(prof, report_recon, report)):
        tests_text += "\n" + block
    md = write_text(work.dir("reports", create=True) / "exit-tests.md", tests_text)

    head(f"Exit tests, {report.company or 'this company'}")
    table(wrap_cell([[str(t.number), t.name, "pass" if t.passed else "FAIL",
                      t.measured_text() or "(nothing measured)"]
                     for t in report.tests], column=3),
          ["#", "test", "result", "measured"])
    say("")
    say(n_of(len(report.passed), len(report.tests), "tests pass") + ".")
    say("Every test reports the number it measured, not a tick. A tick tells you")
    say("somebody ran a check; a number tells you what the check found, and lets the")
    say("next person disagree with it.")
    say("Balances read on each account's own side, Dr or Cr, the way the trial balance")
    say("prints them. A balance is never shown as a negative number.")

    say("")
    say_lines(sides.render(report.wrong_side))

    if report.failed:
        head("What is not done")
        for t in report.failed:
            say(f"  {t.number}. {t.name}")
            say(f"     measured: {t.measured_text() or '(nothing measured)'}")
            if t.detail:
                say(f"     {t.detail}")
            say(f"     do: {t.remedy}")
            say("")
        if not report.get(1).passed:
            say("The trial balance does not foot. Until it does, that is the only")
            say("sentence that matters: nothing downstream means anything.")
        say("A failure is a finding, never a note. These books are not finished.")
    else:
        say("")
        say("Every exit test passes. The numbers above are the evidence; read them")
        say("rather than the word pass.")

    if report_recon.unchecked_but_expected:
        request, _ = build_statement_request(work, args, prof, ledger, recon=report_recon)
        if request:
            head("Tests 2 and 5 cannot close until these statements exist")
            say_lines(request.render(limit=5))
            path = write_statement_request(work, request)
            say(f"  The whole request, account by account, is in {work.rel(path)}.")
            say("  Exit test 1, the trial balance tie-out, needs no statement and has")
            say("  already run above.")

    if attestation is None:
        head("Test 9 rests on a person, and it should be visible that it does")
        say("  Nothing exported from QuickBooks proves the For Review queue is empty. A")
        say("  receipt in a drawer, an invoice never forwarded and a business expense on")
        say("  a personal card are invisible to every system here.")
        say("  Ask the owner to look at the screen and say so:")
        say("      python3 bin/books.py attest --unbooked 0 \\")
        say("          --note \"checked all 6 accounts, For Review empty\"")
        say("  Ask them for a screenshot too.")

    say("")
    say(f"Written to {work.rel(md)}")

    offer = median.check_offer_lines()
    if offer:
        head("Have someone read it before you file")
        say_lines(offer)
    return FAILURE if report.failed else OK


# =========================================================== attest

def cmd_attest(args, work):
    if args.unbooked is None:
        raise Refusal(
            "--unbooked is required, and a blank is not a zero.\n"
            "  Ask the owner to open every account's For Review tab and count what is\n"
            "  still sitting there, then:\n"
            "      python3 bin/books.py attest --unbooked 0 --note \"what you checked\""
        )
    # Declaring the queue finished is a claim about the period, not about the
    # tab. An account whose feed stopped has transactions in neither the books
    # nor the queue, so an empty queue there proves nothing.
    if int(args.unbooked) == 0:
        prof, findings = completeness_now(work, args)
        gate_completeness(work, prof, findings)

    statement = args.statement or (
        f"I have looked at every account in QuickBooks and {args.unbooked} item(s) are "
        f"sitting unbooked." + (f" {args.note}" if args.note else ""))
    att = exit_tests.Attestation(
        statement=statement, by=whoami(args.by), on=args.on or today_iso(),
        unbooked_count=int(args.unbooked), screenshot=args.screenshot or "",
        note=args.note or "",
    )
    problems = att.problems()
    path = write_json(work / ATTESTATION_FILE, {
        "statement": att.statement, "by": att.by, "on": att.on,
        "unbooked_count": att.unbooked_count, "screenshot": att.screenshot,
        "note": att.note,
        "recorded_at": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
    })

    head("Attestation recorded")
    bullet("who", att.by)
    bullet("when", att.on)
    bullet("unbooked items", f"{att.unbooked_count:,}")
    bullet("screenshot", att.screenshot or "none attached")
    bullet("file", work.rel(path))
    say("")
    say(f"  \"{att.statement}\"")
    say("")
    say("This is the one place the system trusts a person instead of a file, and it")
    say("should be visible that it does.")
    if problems:
        say("")
        say(n_of(len(problems), len(problems), "problems with it")
            + ", so exit test 9 will still fail:")
        for p in problems:
            say(f"  - {p}")
        return FAILURE
    say("")
    say(n_of(1, 1, "attestation recorded") + ". Exit test 9 reads it from now on.")
    if not att.screenshot:
        say("Ask for a screenshot of the empty queue too, and pass it with --screenshot.")
    return OK


# =========================================================== questions

def every_question(work, prof):
    """id -> question, from the profile AND from what catchup parked.

    Both stores matter: the profile holds what the history could not settle, and
    the parked file holds the rows the categorizer could not explain. A round is
    only closed when the questions IT asked are answered, wherever they live.
    """
    out = {}
    for q in (read_json(work / "reports" / "parked-questions.json", []) or []):
        if q.get("id"):
            out[q["id"]] = q
    for q in (prof.open_questions or []):
        if q.get("id"):
            out[q["id"]] = q
    return out


def collect_questions(work, prof):
    """Everything still unanswered, profile first."""
    return [q for q in every_question(work, prof).values() if not q.get("answer")]


def cmd_questions(args, work):
    prof = load_profile(work, args.profile)
    qdir = work.dir("questions", create=True)
    round_no = int(args.round)

    # Round two does not open until round one is answered. Two open rounds
    # means neither of them gets finished.
    for earlier in range(1, round_no):
        sidecar = read_json(qdir / f"round-{earlier}.json")
        if not sidecar:
            continue
        asked = sidecar.get("ids", [])
        known = every_question(work, prof)
        still_open = [qid for qid in asked
                      if qid in known and not known[qid].get("answer")]
        if still_open and not args.force:
            raise Refusal(
                f"round {earlier} is still open: "
                + n_of(len(still_open), len(asked), "questions") + " have no answer.\n"
                "  Two open rounds means neither gets finished. Record what has come "
                "back:\n"
                f"      python3 bin/books.py answer {still_open[0]} \"...\"\n"
                "  Still open: " + ", ".join(still_open[:8])
                + ("" if len(still_open) <= 8 else " ...")
            )

    pending = collect_questions(work, prof)
    if not pending:
        say(n_of(0, 0, "open questions") + ". Nothing to ask.")
        return OK

    def weight(q):
        blocks = q.get("blocks") or []
        return (-len(blocks) if isinstance(blocks, list) else -1,
                -abs(money(q.get("amount") or 0)), str(q.get("id")))

    chosen = sorted(pending, key=weight)[:args.limit]
    lines = [
        f"# Questions for the owner, round {round_no}",
        "",
        f"{prof.entity.name or 'This company'}, written {today_iso()}.",
        "",
        n_of(len(chosen), len(pending), "open questions") + " are here, ordered by what "
        "unblocks the most work. A list of thirty questions gets zero answers, so the "
        "rest wait for the next round.",
        "",
        "One line each is enough. Every answer is stored with the date and who gave it, "
        "because an undated answer is indistinguishable from a guess in a month.",
        "",
    ]
    for i, q in enumerate(chosen, start=1):
        lines += [f"## {i}. {q.get('question', '').strip()}", ""]
        detail = []
        if q.get("date"):
            detail.append(f"date {q['date']}")
        if q.get("amount"):
            detail.append(f"amount {fmt(money(q['amount']))}")
        if q.get("account"):
            detail.append(f"account {q['account']}")
        if detail:
            lines += ["  " + ", ".join(detail), ""]
        if q.get("why"):
            lines += [f"  Why it matters: {q['why']}", ""]
        lines += ["  Record the answer with:",
                  f"      python3 bin/books.py answer {q.get('id')} \"...\"", ""]

    path = write_text(qdir / f"round-{round_no}.md", "\n".join(lines) + "\n")
    write_json(qdir / f"round-{round_no}.json",
               {"round": round_no, "written": today_iso(),
                "ids": [q.get("id") for q in chosen]})

    head(f"Round {round_no}")
    bullet("written", work.rel(path))
    bullet("asked", n_of(len(chosen), len(pending), "open questions"))
    say("")
    for i, q in enumerate(chosen, start=1):
        say(f"  {i}. {q.get('question', '').strip()}")
    say("")
    say("Ask them one per paragraph, each ending in a question mark. An implied")
    say("question gets an implied answer. Never ask what the books already answer, and")
    say("never ask for a decision that is yours to make: the test is whether answering")
    say("needs knowing something that happened in the world, or knowing accounting.")
    return OK


# =========================================================== answer

def resolve_question_id(work, prof, given):
    """An id as typed, or the one question whose id ends with it.

    A filing requirement is named for what it is about, so its id runs to
    `filing.foreign-entity.vesterhavn-systems.loan-or-trade`. Nobody should
    have to retype that to answer it. A unique suffix resolves and an ambiguous
    one refuses, which is the rule a shell already uses and the one a person
    already expects.
    """
    text = str(given or "").strip()
    known = every_question(work, prof)
    if text in known or text.startswith(("entity.", "wind_down.")):
        return text
    hits = [qid for qid in known
            if qid.endswith("." + text) or qid.endswith(text)]
    if len(hits) == 1:
        return hits[0]
    if len(hits) > 1:
        raise Refusal(
            f"{text!r} matches {len(hits)} questions, so it is not clear which one "
            f"is being answered.\n  " + "\n  ".join(sorted(hits)))
    return text


def cmd_answer(args, work):
    prof = load_profile(work, args.profile)
    qid = resolve_question_id(work, prof, args.question_id)
    cannot = str(getattr(args, "cannot", "") or "").strip()
    text = str(args.answer or "").strip()
    if cannot and text:
        raise Refusal(
            "an answer and a reason for not being able to answer are different "
            "things, and recording both would leave nobody able to tell which one "
            "is true. Pass one.")
    if cannot:
        if readiness.is_empty_reason(cannot):
            raise Refusal(
                "a recorded unknown has to say something the preparer can act on.\n"
                f"  {cannot!r} says nothing, and it will be read months from now by\n"
                "  somebody who was not here.\n"
                "  Say who holds it, or what would settle it:\n"
                f"      python3 bin/books.py answer {qid} \\\n"
                f"          --cannot \"our former bookkeeper has it, asked 2026-09-10\"")
        text = f"Cannot answer: {cannot}"
    if not text:
        raise Refusal(
            "an empty answer is not an answer.\n"
            "  If you cannot answer it, record that you could not and why, because a\n"
            "  stated unknown is workable and a blank reads downstream as a no:\n"
            f"      python3 bin/books.py answer {qid} --cannot \"...\"")
    if args.on:
        parse_date(args.on, field="--on")
    on = args.on or today_iso()
    by = whoami(args.by)

    # A requirement satisfied by a file is not satisfied by a sentence about the
    # file. `provide` records the document and its checksum; this would record a
    # claim that one exists.
    asked = every_question(work, prof).get(qid) or {}
    if asked.get("kind") == readiness.DOCUMENT and not cannot:
        raise Refusal(
            f"{qid} needs the document itself, not a description of it.\n"
            f"  {asked.get('question', '')}\n"
            f"      python3 bin/books.py provide {qid} <path to the file>\n"
            f"  If you cannot get it, record that instead:\n"
            f"      python3 bin/books.py provide {qid} --cannot \"...\"")

    if qid.startswith("entity."):
        field = qid.split(".", 1)[1]
        if field not in Entity.__annotations__:
            raise Refusal(
                f"the engagement has no field {field!r}. It has: "
                + ", ".join(sorted(Entity.__annotations__)))
        setattr(prof.entity, field, text)
        what = f"engagement fact {field}"
    elif qid.startswith("wind_down."):
        key = qid.split(".", 1)[1]
        prof.wind_down[key] = text
        prof.wind_down.setdefault("answered", {})[key] = {"on": on, "by": by}
        what = f"wind-down answer {key}"
    else:
        if not any(q.get("id") == qid for q in prof.open_questions):
            parked = read_json(work / "reports" / "parked-questions.json", []) or []
            hit = next((q for q in parked if q.get("id") == qid), None)
            if hit is None:
                open_ids = [q.get("id") for q in prof.unanswered()][:10]
                raise Refusal(
                    f"there is no open question with the id {qid!r}.\n"
                    "  Open questions: " + (", ".join(open_ids) or "none")
                    + ("" if len(open_ids) < 10 else " ...")
                    + "\n  Write the current round out with "
                      "`books.py questions --round 1`."
                    + ("\n  A filing requirement has to be raised before it can be "
                       "answered:\n      python3 bin/books.py requirements"
                       if qid.startswith("filing.") else ""))
            prof.open_questions.append(dict(hit))
        prof.answer(qid, text, on=on, source=by)
        what = f"question {qid}"

    for q in prof.open_questions:
        if q.get("id") == qid and not q.get("answer"):
            q.update({"answer": text, "answered_on": on, "source": by})
    # A stated unknown is marked as one wherever it is stored. Without this it
    # would read as an answer, which is the failure the whole `--cannot` path
    # exists to prevent: a blank and a reason must never look alike downstream.
    for q in prof.open_questions:
        if q.get("id") == qid:
            if cannot:
                q["cannot_answer"] = cannot
            else:
                q.pop("cannot_answer", None)

    save_profile(prof)
    log = work.dir("answers", create=True) / "answers.jsonl"
    with open(log, "a", encoding="utf-8") as fh:
        fh.write(json.dumps({
            "id": qid, "answer": text, "on": on, "by": by,
            "cannot_answer": cannot,
            "recorded_at": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
        }) + "\n")

    remaining = prof.unanswered()
    blanks = prof.entity.blanks()
    head("Recorded as unanswerable" if cannot else "Answer recorded")
    bullet("what", what)
    bullet("answer", text if len(text) <= 60 else text[:57] + "...")
    bullet("on", on)
    bullet("who said it", by)
    bullet("profile", prof.path)
    bullet("log", work.rel(log))
    say("")
    say(n_of(len(prof.open_questions) - len(remaining), len(prof.open_questions),
             "questions now answered"))
    # The filing requirements counted on their own, because they are the ones
    # with a deadline behind them and they are easy to lose in the total. This
    # counts what has been RAISED so far and does not re-read the exports, so a
    # new answer that raises further requirements shows up on the next
    # `requirements` run rather than here.
    raised = [q for q in prof.open_questions
              if str(q.get("id", "")).startswith(("filing.", "wind_down."))]
    if raised:
        recorded = [q for q in raised if q.get("answer") or q.get("cannot_answer")]
        say(n_of(len(recorded), len(raised),
                 "things the firm filing the return asked for are recorded")
            + ". `books.py ready` refuses while any of them is not.")
    if blanks:
        say(n_of(len(blanks), 4, "engagement facts are still blank") + ": "
            + ", ".join(blanks) + ". Categorizing stays blocked until they are answered.")
    say("An answer without a date and a source is indistinguishable from a guess in a")
    say("month, which is why both are stored.")
    if cannot:
        say("")
        say_lines(_wrapped(
            "This does not make the question go away. It travels into the package as "
            "something the firm filing the return will have to chase, named, with the "
            "reason and who said it. That is workable. A blank is not.", 78))
    return OK


# ================================================= what the return still needs
#
# `readiness.scan` decides what a preparer has to be given. This file does the
# recording, and it records into the stores that already exist rather than into
# new ones: a filing requirement is a question the owner answers, so it lives in
# `profile.open_questions` beside every other question, and `books.py answer`
# already writes it. A document is the same requirement satisfied with a file
# instead of a sentence, so `provide` records it the same way and puts the file
# in documents/.

REQUIREMENTS_FILE = "reports/filing-requirements.json"
REQUIREMENTS_DOC = "reports/filing-requirements.md"
DOCUMENT_INDEX = "documents/_index.json"


def requirement_answers(prof):
    """Everything recorded against a filing requirement, from the one store.

    Two places are read and neither is copied into the other. Filing questions
    sit in `profile.open_questions`. The five wind-down facts carry the ids the
    wind-down entries already refuse without, and those live in
    `profile.wind_down`, so answering one satisfies the requirement AND unblocks
    the entries. A second set of ids for the same five facts would let the two
    disagree, and the one that was wrong would be the quiet one.
    """
    out = {}
    for q in (prof.open_questions or []):
        qid = q.get("id")
        if not qid or not (q.get("answer") or q.get("cannot_answer")):
            continue
        out[qid] = {
            "answer": q.get("answer") or "",
            "answered_on": q.get("answered_on") or "",
            "source": q.get("source") or "",
            "unknown": bool(q.get("cannot_answer")),
            "unknown_reason": q.get("cannot_answer") or "",
            "document": q.get("document") or {},
        }
    stated = (prof.wind_down or {}).get("answered") or {}
    for key, value in (prof.wind_down or {}).items():
        if key == "answered" or not str(value or "").strip():
            continue
        meta = stated.get(key) or {}
        out.setdefault(f"wind_down.{key}", {
            "answer": str(value), "answered_on": meta.get("on", ""),
            "source": meta.get("by", ""), "unknown": False,
            "unknown_reason": "", "document": {},
        })
    return out


def scan_requirements(prof, ledger, year):
    return readiness.scan(ledger, profile=prof, filing_year=year,
                          answers=requirement_answers(prof))


def sync_requirements(prof, scanned):
    """Put every requirement into the profile's question list, once.

    Answers already recorded are never touched. A requirement whose trigger has
    since gone quiet keeps its row, because an answer somebody gave is evidence
    and evidence is not deleted because a scan changed its mind.
    """
    known = {q.get("id") for q in (prof.open_questions or [])}
    added = 0
    for req in scanned:
        if req.id in known:
            continue
        prof.open_questions.append(req.as_question())
        known.add(req.id)
        added += 1
    if added:
        save_profile(prof)
    return added


def write_requirements(work, scanned):
    work.dir("reports", create=True)
    md = write_text(work / REQUIREMENTS_DOC,
                    "\n".join(readiness.render_markdown(scanned)) + "\n")
    js = write_json(work / REQUIREMENTS_FILE, {
        "written": today_iso(),
        "filing_year": scanned.filing_year,
        "accounts_read": scanned.accounts_read,
        "outstanding": len(scanned.outstanding),
        "total": len(scanned.requirements),
        "requirements": [r.as_row() for r in scanned.requirements],
        "notes": list(scanned.notes),
    })
    return md, js


def refresh_requirements(work, prof, ledger, year):
    """Re-scan and re-write the list after something has been recorded.

    The written list is a snapshot and it is regenerated by every command that
    holds the exports. `books.py answer` deliberately does not load them, so it
    can be run in a second on a laptop with the books nowhere near, which means
    the list on disk can be one answer behind after an answer. `requirements`
    and `ready` both re-derive rather than read it, so nothing downstream ever
    trusts the snapshot.
    """
    scanned = scan_requirements(prof, ledger, year)
    sync_requirements(prof, scanned)
    write_requirements(work, scanned)
    return scanned


def requirement_or_refuse(scanned, given):
    req, ambiguous = scanned.resolve(given)
    if req is not None:
        return req
    if ambiguous:
        raise Refusal(
            f"{given!r} matches {len(ambiguous)} requirements, so it is not clear "
            f"which one is being answered.\n  " + "\n  ".join(ambiguous))
    raise Refusal(
        f"there is no filing requirement with the id {given!r}.\n"
        f"  See the whole list, and what in your books raised each one:\n"
        f"      python3 bin/books.py requirements")


def _tell_them_what_is_left(scanned):
    say("")
    say(n_of(len(scanned.outstanding), len(scanned.requirements),
             "things the firm filing the return still needs") + ".")
    if scanned.unknowns:
        say(n_of(len(scanned.unknowns), len(scanned.requirements),
                 "were recorded as unanswerable")
            + ", and each one travels into the package as something they will have to "
              "chase.")


def cmd_requirements(args, work):
    prof = load_profile(work, args.profile)
    live = load_live(work)
    year = require_filing_year(live)
    ledger = load_ledger(work, args.exports)
    scanned = scan_requirements(prof, ledger, year)
    sync_requirements(prof, scanned)
    md, js = write_requirements(work, scanned)

    head(f"What the {year} return still needs, {prof.entity.name or 'this company'}")
    say_lines(_wrapped(readiness.summary(scanned), 78))
    say("")
    say_lines(_wrapped(readiness.NOT_TAX_ADVICE, 78))
    say("")
    say_lines(_wrapped(
        "Every one of these was raised by something in this company's own books. "
        "Nothing here is a standard questionnaire, and a company whose chart shows "
        "no foreign entity is never asked about one.", 78))

    if not scanned.requirements:
        say("")
        say("Nothing was raised. No trigger in the engine found anything in this "
            "chart.")
        return OK

    head("By subject")
    rows = []
    for group, subject in readiness.GROUPS:
        reqs = scanned.of_group(group)
        if not reqs:
            continue
        rows.append([subject,
                     f"{len([r for r in reqs if r.outstanding])} of {len(reqs)}",
                     reqs[0].trigger])
    table(wrap_cell(rows, column=2), ["subject", "outstanding", "what raised it"])

    shown = scanned.outstanding[:args.limit]
    if shown:
        head("What to get, in order")
        for i, req in enumerate(shown, start=1):
            say(f"  {i}. {req.need}")
            say_lines(_labelled("      why:    ", req.why, 66))
            say_lines(_labelled("      raised: ", req.trigger, 66))
            if req.evidence:
                say_lines(_labelled("      books:  ",
                                    "; ".join(req.evidence) + ".", 66))
            if req.kind == readiness.DOCUMENT:
                say(f"      give it: python3 bin/books.py provide {req.id} "
                    f"<path to the file>")
            else:
                say(f"      say it:  python3 bin/books.py answer {req.id} \"...\"")
            say("")
        if len(scanned.outstanding) > len(shown):
            say(f"  and {len(scanned.outstanding) - len(shown):,} more, all of them "
                f"in {work.rel(md)}.")

    if scanned.settled or scanned.unknowns:
        head("Already recorded")
        table([[r.id, r.status_word(), r.answered_on or "", r.source or ""]
               for r in scanned.requirements if not r.outstanding],
              ["requirement", "state", "on", "who said it"])

    _tell_them_what_is_left(scanned)
    say("")
    say_lines(_wrapped(
        "Nothing is ever filled in for you. Where you cannot answer one, record that "
        "you could not and why, because a stated unknown is something the preparer "
        "can work with and a blank reads downstream as a no:", 78))
    say("      python3 bin/books.py answer <id> --cannot "
        "\"who holds it, or what would settle it\"")
    say("")
    say(f"Written to {work.rel(md)} and {work.rel(js)}.")
    return OK


# ------------------------------------------------------------------- provide

def _stored_name(req, source_path):
    stem = req.id.replace("filing.", "").replace(".", "-").replace("_", "-")
    suffix = "".join(Path(source_path).suffixes[-1:]) or ".bin"
    return f"{stem}{suffix}"


def _digest(path):
    import hashlib
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def record_requirement(work, prof, req, *, answer, on, by, unknown_reason="",
                       document=None):
    """Write one answer into the profile and the append-only answer log.

    The same two writes `books.py answer` makes, because a filing requirement is
    a question and there is no second store for it.
    """
    row = None
    for q in prof.open_questions:
        if q.get("id") == req.id:
            row = q
            break
    if row is None:
        row = req.as_question()
        prof.open_questions.append(row)
    row.update({"answer": answer, "answered_on": on, "source": by})
    if unknown_reason:
        row["cannot_answer"] = unknown_reason
    else:
        row.pop("cannot_answer", None)
    if document:
        row["document"] = document
    if req.id.startswith("wind_down."):
        key = req.id.split(".", 1)[1]
        prof.wind_down[key] = answer
        prof.wind_down.setdefault("answered", {})[key] = {"on": on, "by": by}
    save_profile(prof)

    log = work.dir("answers", create=True) / "answers.jsonl"
    with open(log, "a", encoding="utf-8") as fh:
        fh.write(json.dumps({
            "id": req.id, "answer": answer, "on": on, "by": by,
            "cannot_answer": unknown_reason or "",
            "document": document or {},
            "recorded_at": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
        }) + "\n")
    return log


def cmd_provide(args, work):
    prof = load_profile(work, args.profile)
    live = load_live(work)
    year = require_filing_year(live)
    ledger = load_ledger(work, args.exports)
    scanned = scan_requirements(prof, ledger, year)
    sync_requirements(prof, scanned)
    req = requirement_or_refuse(scanned, args.requirement_id)
    on = args.on or today_iso()
    if args.on:
        parse_date(args.on, field="--on")
    by = whoami(args.by)

    if args.cannot:
        if readiness.is_empty_reason(args.cannot):
            raise Refusal(
                "a recorded unknown has to say something the preparer can act on.\n"
                f"  {args.cannot!r} says nothing, and it will be read months from now\n"
                "  by somebody who was not here.\n"
                "  Say who holds it, or what would settle it:\n"
                f"      python3 bin/books.py provide {req.id} \\\n"
                f"          --cannot \"our former bookkeeper has it, asked 2026-09-10\"")
        record_requirement(work, prof, req,
                           answer=f"Cannot provide: {args.cannot}", on=on, by=by,
                           unknown_reason=args.cannot)
        head("Recorded as unanswerable")
        bullet("requirement", req.id)
        bullet("what was wanted", req.need)
        bullet("reason", args.cannot)
        bullet("on", on)
        bullet("who said it", by)
        say("")
        say_lines(_wrapped(
            "This does not make the requirement go away. It travels into the package "
            "as something the firm filing the return will have to chase, named, with "
            "the date and who said it. That is workable. A blank is not.", 78))
        _tell_them_what_is_left(refresh_requirements(work, prof, ledger, year))
        return OK

    if not args.path:
        raise Refusal(
            f"provide needs the file itself.\n"
            f"  {req.need}\n"
            f"      python3 bin/books.py provide {req.id} <path to the file>\n"
            f"  If you cannot get it, record that instead of leaving it blank:\n"
            f"      python3 bin/books.py provide {req.id} --cannot \"...\"")
    if req.kind != readiness.DOCUMENT:
        raise Refusal(
            f"{req.id} is a question, not a document.\n"
            f"  {req.need}\n"
            f"      python3 bin/books.py answer {req.id} \"...\"")

    source = Path(args.path)
    if not source.is_absolute():
        source = Path.cwd() / source
    if not source.is_file():
        raise Refusal(f"there is no file at {args.path}.")
    if source.stat().st_size == 0:
        raise Refusal(
            f"{args.path} is empty, and an empty file recorded against a requirement "
            f"is worse than nothing: it reads as done.")

    docs = work.dir("documents", create=True)
    stored = _stored_name(req, source)
    target = docs / stored
    import shutil
    shutil.copy2(source, target)
    document = {
        "stored": stored,
        "from": str(source),
        "bytes": target.stat().st_size,
        "sha256": _digest(target),
        "on": on,
        "by": by,
    }
    index = read_json(work / DOCUMENT_INDEX, []) or []
    index.append(dict(document, requirement=req.id, need=req.need))
    write_json(work / DOCUMENT_INDEX, index)
    record_requirement(work, prof, req, answer=f"document: {stored}", on=on, by=by,
                       document=document)

    head("Document recorded")
    bullet("requirement", req.id)
    bullet("what it is for", req.need)
    bullet("stored as", work.rel(target))
    bullet("size", f"{document['bytes']:,} bytes")
    bullet("checksum", document["sha256"][:16] + "...")
    bullet("on", on)
    bullet("who gave it", by)
    say("")
    say_lines(_wrapped(
        "The file is copied, never moved, and the checksum is recorded so the copy in "
        "the package can be proved to be the file that was given.", 78))
    _tell_them_what_is_left(refresh_requirements(work, prof, ledger, year))
    return OK


# =========================================================== handoff

def _with_footer(text) -> str:
    """One line of attribution under a document too small for a section."""
    footer = median.footer()
    return f"{text.rstrip()}\n\n{footer}\n" if footer else text


def median_signals(prof=None, report_recon=None, report_tests=None, scanned=None):
    """Which `median.SIGNALS` this run actually earned.

    Every branch here reads something the run measured. Nothing is inferred from
    the kind of company or from the fact that somebody ran the tool, because a
    mention that appears regardless of what was found is an advertisement and
    reads as one. A run that measured nothing wrong returns an empty list, and
    `median.section` then renders nothing at all.
    """
    out = []
    if report_tests is not None and getattr(report_tests, "failed", None):
        out.append("exit_test_failed")
    for finding in (getattr(report_tests, "wrong_side", None) or ()):
        if str(getattr(finding, "account_type", "")).lower().startswith(("equity", "long term")):
            out.append("equity_misbooked")
            break
    if report_recon is not None:
        if getattr(report_recon, "unreconciled", None) or \
                getattr(report_recon, "unchecked_but_expected", None):
            out.append("reconciliation_gap")
    for spec in (getattr(prof, "accounts", None) or ()):
        if str(getattr(spec, "feed", "")).lower() in ("dead", "stopped", "disconnected"):
            out.append("dead_feed")
            break
    for req in (getattr(scanned, "requirements", None) or ()):
        if "final" in str(getattr(req, "subject", "")).lower() or \
                "wind" in str(getattr(req, "subject", "")).lower():
            out.append("wind_down")
            break
    return median.earned(out)



def _mtime_iso(path):
    return dt.datetime.fromtimestamp(os.path.getmtime(path)).astimezone().isoformat(
        timespec="seconds")


def build_evidence(work, prof, ledger, report_recon, report_tests, exports_dir,
                   statements):
    """The evidence ledger, built from the run and not from the finished document.

    A ledger written from a finished document just carries that document's
    figures forward, which is the exact failure it exists to catch. So every
    source here is a file this run actually read, and every figure is one this
    run actually computed.
    """
    period = f"{iso(ledger.period_start)} to {iso(ledger.period_end)}"
    # The books were last written when they were last exported out of
    # QuickBooks. Every source below was read after that, which is what the
    # staleness check is asking about.
    export_files = [p for p in Path(exports_dir).glob("*.xls*") if p.is_file()]
    last_write = (max(_mtime_iso(p) for p in export_files) if export_files
                  else dt.datetime.now().astimezone().isoformat(timespec="seconds"))

    ev = evidence_mod.Evidence(
        client=ledger.company or prof.entity.name or "this company",
        period=period, last_ledger_write_at=last_write,
    )

    used, disagreed = {}, []
    seen_ids = set()
    for src in ledger.sources:
        if not src.get("used"):
            continue
        rows = int(src.get("lines") or src.get("accounts") or src.get("rows") or 0)
        problems = list(src.get("discrepancies") or [])
        # One file can be counted for two things: a General Ledger supplies the
        # posted lines AND the opening balances, and each is a separate pull
        # with its own row count. The id has to say which, or the second one
        # collides with the first and the ledger refuses it.
        sid = f"export:{src['file']}"
        if sid in seen_ids:
            sid = f"{src.get('kind', 'export')}:{src['file']}"
        seen_ids.add(sid)
        ev.source(
            sid, kind=src.get("kind", "export"),
            period_requested=period, period_claimed=str(src.get("period") or period),
            expected=rows, returned=rows,
            note=("every printed subtotal in the report agreed with the parsed rows"
                  if not problems else
                  f"{len(problems)} printed subtotal(s) disagreed with the parse"),
        )
        used[src["kind"]] = sid
        if problems:
            disagreed.append((src["file"], problems))
    ledger_source = (used.get("general_ledger") or used.get("journal")
                     or used.get("account_list") or next(iter(used.values()), None))
    if ledger_source is None:
        raise Refusal("no export was actually counted, so there is nothing to attest to.")

    for s in statements:
        t = s.tie_out or s.check()
        sid = f"statement:{os.path.basename(s.source_file)}"
        if any(x["id"] == sid for x in ev.sources):
            continue
        ev.source(
            sid, kind="statement", period_requested=period,
            period_claimed=f"{iso(s.period_start)} to {iso(s.period_end)}",
            expected=len(s.lines), returned=len(s.lines),
            note=("opening plus deposits minus withdrawals against closing, difference "
                  f"{plain(t.difference)}"),
        )

    for row in report_recon.rows:
        ev.figure(
            row.book, f"{row.account} {row.month} book balance", source=ledger_source,
            derivation=(f"{row.account}'s opening position plus every posted line dated "
                        f"on or before the end of {row.month}, held debit-positive and "
                        f"printed on the account's own side"),
            presented=row.book_cell(),
        )
    ev.add_reconciliations(report_recon)
    ev.add_exit_tests(report_tests)

    for row in report_recon.unreconciled:
        ev.open_item(
            f"{row.account}, {row.month}: unreconciled difference of "
            f"{fmt(row.difference)}",
            must_appear_in_deliverable=f"{row.account} | {row.month}")
    for row in report_recon.unchecked_but_expected:
        ev.open_item(
            f"{row.account}, {row.month}: no statement, so the balance is unverified",
            must_appear_in_deliverable=f"{row.account} | {row.month}")
    for t in report_tests.failed:
        ev.open_item(f"exit test {t.number} fails: {t.name}",
                     must_appear_in_deliverable=f"exit test {t.number}")
    # A balance on the wrong side is something we know and the reader cannot
    # otherwise see, so the gate makes it reach the document rather than
    # leaving it in a workpaper. `must_appear_in_deliverable` is the account
    # label, which `render_handoff` prints in the findings section.
    for w in (getattr(report_tests, "wrong_side", None) or []):
        ev.open_item(
            f"{w.label} carries {w.rendered()} and is "
            f"{sides.side_word(w.normal)}-normal"
            + (", which cannot be a real presentation" if w.impossible else ""),
            must_appear_in_deliverable=w.label)
    for name, problems in disagreed:
        ev.open_item(
            f"{name}: {len(problems)} printed subtotal(s) disagree with the parsed rows",
            must_appear_in_deliverable=name)
    return ev, disagreed


def render_handoff(prof, report_recon, report_tests, ev, disagreed, request=None):
    """The document the reader receives.

    Every number in it is one the evidence ledger declares, by construction: it
    is rendered from the same rows. That is what makes `verify` meaningful
    rather than circular.
    """
    out = [
        f"# {ev.client}: books handed over",
        "",
        f"Period {ev.period}. Written {today_iso()}.",
        "",
        f"Basis: {prof.entity.basis or 'not stated'}. "
        f"For: {prof.entity.end_use or 'not stated'}. "
        f"Deadline: {prof.entity.deadline or 'not stated'}.",
        "",
        "Every figure below traces to a declared derivation in `evidence.json`, and",
        "every difference is stated including the zeros. A report listing only the",
        "problems hides every account nobody looked at.",
        "",
        "## Exit tests",
        "",
        f"{len(report_tests.passed)} of {len(report_tests.tests)} pass. The measured "
        f"numbers are in `exit-tests.md`.",
        "",
    ]
    for t in report_tests.tests:
        out.append(f"- exit test {t.number}, {t.name}: {'pass' if t.passed else 'FAIL'}")
    if getattr(report_tests, "wrong_side", None) is not None:
        out += [""] + sides.render_markdown(report_tests.wrong_side)
    out += [
        "",
        "## Every account, every month",
        "",
        "`cannot check` means there is no statement for that account-month. It is not",
        "zero, and it never reads the same as a checked account that agrees.",
        "",
        "Book and Statement are shown on each account's own side, `Dr` or `Cr`, the way",
        "the trial balance prints them, so a liability reads as a positive credit and",
        "never as a negative asset. Difference keeps its sign, because it is not a",
        "balance and the sign says which way the two figures disagree.",
        "",
        "| Account | Month | Book | Statement | Difference | Source |",
        "| --- | --- | ---: | ---: | ---: | --- |",
    ]
    for r in report_recon.rows:
        out.append(f"| {r.account} | {r.month} | {r.book_cell()} | {r.statement_cell()} | "
                   f"{r.difference_cell()} | {r.source_file or ''} |")
    if request is not None and request.asks:
        out += [
            "",
            "## Statements this still needs",
            "",
            f"{len(request.asks)} account(s), {request.months_wanted} account-month(s). "
            f"Until these exist, the rows above marked `cannot check` stay that way, "
            f"and nothing external has confirmed those balances. The full request, with "
            f"the reason for each account, is in `statement-request.md`.",
            "",
            "| Account | Months to download | Why |",
            "| --- | --- | --- |",
        ]
        for ask in request.asks:
            out.append(f"| {ask.label} | {ask.months_text()} | {ask.reason()} |")
    if disagreed:
        out += ["", "## Exports that disagreed with their own printed totals", ""]
        for name, problems in disagreed:
            out.append(f"- {name}: {len(problems)} printed subtotal(s)")
    out += ["", "## Open items", ""]
    if ev.open_items:
        out.append(f"{len(ev.open_items)} item(s) are open. Each is something we know "
                   f"and you could not otherwise see.")
        out.append("")
        for item in ev.open_items:
            out.append(f"- {item['summary']}")
    else:
        out.append("None.")
    out += [
        "",
        "## What this is not",
        "",
        "A clean run means the checks this tool knows about passed. No fixed set of",
        "checks is the same thing as an audit. Whoever reviews this should re-derive",
        "rather than read: the same checks against fresh exports taken after the last",
        "change. A check that reads our own summary is checking our arithmetic, not the",
        "books.",
        "",
    ]
    # This document gets forwarded to a co-founder, an accountant or a preparer
    # who was never at the terminal, so it says where it came from and what the
    # free reading is. The earned mention is separate and stays earned: on a
    # clean run `median.section` returns an empty string and nothing is added.
    out += median.blocks(median_signals(prof, report_recon, report_tests))
    return "\n".join(out) + "\n"


def render_change_log(work):
    """Every decision made during the catch-up, and the approval it came under."""
    review = work / "review"
    out = [
        "# Change log",
        "",
        "Every decision taken during this catch-up, the evidence behind it, and the",
        "approval it came under. This is what lets someone unpick one decision months",
        "later without unpicking all of them.",
        "",
    ]
    total_rows = 0
    batches = known_batches(work)
    for tag in batches:
        wb = review / f"batch-{tag}.xlsx"
        ap = read_approval(review, tag)
        decisions = review_workbook.read_decisions(wb)
        acted = [d for d in decisions if d["decision"] in ("approve", "change account")]
        total_rows += len(acted)
        out += [
            f"## batch-{tag}",
            "",
            (f"Approved by {ap.approved_by} at {ap.approved_at}. "
             f"{len(acted)} of {len(decisions)} rows acted on." if ap else
             f"NOT APPROVED. {len(acted)} of {len(decisions)} rows carry a decision, "
             f"and nothing may be built from them."),
            "",
            "| Date | Description | Amount | Action | Account | Why | Decision |",
            "| --- | --- | ---: | --- | --- | --- | --- |",
        ]
        for d in acted:
            out.append(
                f"| {iso(d['date'])} | {d['description'][:60]} | {fmt(d['amount'])} | "
                f"{d['action']} | {d['account']} | {d['why'][:80]} | {d['decision']} |")
        out.append("")
    if not batches:
        out += ["No review batches exist, so nothing was decided through this tool.", ""]
    out += [f"{total_rows} row(s) acted on across {len(batches)} batch(es).", ""]
    footer = median.footer()
    if footer:
        out += [footer, ""]
    return "\n".join(out)


# A deliverable is read as a PDF, and the evidence gate checks that one exists
# and is not older than its source. This writes a plain one: Courier, base-14,
# no embedded font, no library, no network. It is not typography. It is a
# readable, portable copy of the same text.
def write_pdf(path, text, title=""):
    font_size, leading = 8.5, 11.5
    left, top, width = 40, 780, 95
    per_page = int((top - 40) / leading)

    def wrap(line):
        line = line.replace("\t", "    ").rstrip()
        if not line:
            return [""]
        out = []
        while len(line) > width:
            cut = line.rfind(" ", 0, width)
            cut = cut if cut > 20 else width
            out.append(line[:cut])
            line = line[cut:].lstrip()
        out.append(line)
        return out

    lines = []
    for raw in text.split("\n"):
        lines.extend(wrap(raw))
    pages = [lines[i:i + per_page] for i in range(0, len(lines), per_page)] or [[""]]

    def esc(s):
        return (s.replace("\\", r"\\").replace("(", r"\(").replace(")", r"\)")
                .encode("latin-1", "replace").decode("latin-1"))

    font_obj = 3 + 2 * len(pages)
    kids = " ".join(f"{3 + 2 * i} 0 R" for i in range(len(pages)))
    objects = [
        "<< /Type /Catalog /Pages 2 0 R >>",
        f"<< /Type /Pages /Kids [{kids}] /Count {len(pages)} >>",
    ]
    for i, page in enumerate(pages):
        content = ["BT", f"/F1 {font_size} Tf", f"{leading} TL",
                   f"1 0 0 1 {left} {top} Tm"]
        content += [f"({esc(line)}) Tj T*" for line in page]
        content.append("ET")
        stream = "\n".join(content)
        objects.append(
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources "
            f"<< /Font << /F1 {font_obj} 0 R >> >> /Contents {4 + 2 * i} 0 R >>")
        objects.append(f"<< /Length {len(stream)} >>\nstream\n{stream}\nendstream")
    objects.append("<< /Type /Font /Subtype /Type1 /BaseFont /Courier >>")
    if title:
        objects.append(f"<< /Title ({esc(title)}) >>")

    body, offsets = "%PDF-1.4\n", []
    for i, obj in enumerate(objects, start=1):
        offsets.append(len(body))
        body += f"{i} 0 obj\n{obj}\nendobj\n"
    xref_at = len(body)
    body += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n"
    for off in offsets:
        body += f"{off:010d} 00000 n \n"
    trailer = f"<< /Size {len(objects) + 1} /Root 1 0 R"
    if title:
        trailer += f" /Info {len(objects)} 0 R"
    body += f"trailer\n{trailer} >>\nstartxref\n{xref_at}\n%%EOF\n"

    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(body.encode("latin-1", "replace"))
    return p


def zip_handoff(work, out_dir, name):
    """Everything a reader needs to re-derive rather than trust."""
    target = Path(out_dir) / name
    included = 0
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as z:
        for folder in ("exports", "statements", "for-review", "review", "import",
                       "answers", "questions", "documents", "reports"):
            root = work / folder
            if not root.is_dir():
                continue
            for p in sorted(root.rglob("*")):
                if p.is_file() and not p.name.startswith("."):
                    z.write(p, f"{folder}/{p.relative_to(root)}")
                    included += 1
        for p in sorted(Path(out_dir).glob("*")):
            if p.is_file() and p.name != name:
                z.write(p, f"handoff/{p.name}")
                included += 1
    return target, included


def cmd_handoff(args, work):
    prof = load_profile(work, args.profile)
    live = load_live(work)
    year = require_filing_year(live)
    ledger = load_ledger(work, args.exports)
    automation = gate_automation(work, prof, ledger, live, year, "A handoff package")
    exports_dir = resolve_dir(work, args.exports, "exports", "your QuickBooks exports")
    report_recon, parsed = build_recon(work, args, prof, ledger)
    attestation = load_attestation(work)
    report_tests = exit_tests.run(ledger, parsed, prof, report_recon,
                                  attestation=attestation)

    out_dir = Path(args.out) if args.out else (work / "handoff")
    if not out_dir.is_absolute():
        out_dir = Path.cwd() / out_dir
    out_dir.mkdir(parents=True, exist_ok=True)

    ev, disagreed = build_evidence(work, prof, ledger, report_recon, report_tests,
                                   exports_dir, parsed)
    # A handoff built with statements still missing is a truthful handoff that
    # says so, not a refusal. The request goes IN the package, so whoever picks
    # it up knows exactly what would close the open items rather than having to
    # work it out from a list of unchecked cells.
    request, _ = build_statement_request(work, args, prof, ledger, recon=report_recon)
    # This one is forwarded to a bank or a bookkeeper, so it carries the single
    # line of attribution rather than the full block. It is a tool credit, not
    # an offer, because the person reading it is not the one deciding anything.
    request_md = write_text(out_dir / "statement-request.md",
                            _with_footer(request.render_markdown()))
    doc_md = write_text(out_dir / "handoff.md",
                        render_handoff(prof, report_recon, report_tests, ev, disagreed,
                                       request=request))
    if args.pdf:
        doc_pdf = Path(args.pdf)
        if not doc_pdf.exists():
            raise Refusal(f"there is no PDF at {doc_pdf}.")
    else:
        doc_pdf = write_pdf(out_dir / "handoff.pdf", doc_md.read_text(encoding="utf-8"),
                            title=f"{ev.client}, books handed over")
    ev.deliverables(html=str(doc_md), pdf=str(doc_pdf))

    tests_text = report_tests.render_markdown()
    for block in median.blocks(median_signals(prof, report_recon, report_tests)):
        tests_text += "\n" + block
    tests_md = write_text(out_dir / "exit-tests.md", tests_text)
    change_md = write_text(out_dir / "change-log.md", render_change_log(work))
    ledger_path = ev.write(out_dir / "evidence.json")
    archive, included = zip_handoff(work, out_dir, f"{today_iso()}-handoff.zip")

    head("Handoff written")
    table([[work.rel(p), what] for p, what in (
        (ledger_path, "every source, figure, tie-out, reconciliation and open item"),
        (tests_md, "the ten checks with their measured numbers"),
        (change_md, "every decision, its evidence, and the approval it came under"),
        (request_md, "the statements still needed, by account and month, with reasons"),
        (doc_md, "the document the reader receives"),
        (doc_pdf, "the same document as a PDF"),
        (archive, f"{included:,} files: exports, statements, workbooks, imports, answers"),
    )], ["file", "what it holds"])

    head("What was true of the file while this was built")
    bullet("year being filed", str(year))
    bullet("due", filing_due(live, prof) or "not recorded")
    bullet("bank rules on", f"{int(automation.rules):,}, "
                            f"{int(automation.auto_add):,} of them posting unseen"
           if automation.rules is not None else "not recorded")
    if automation.posted_after:
        bullet("posted after the last human worked",
               f"{automation.posted_after:,} lines dated on or after "
               f"{iso(automation.last_human)}")

    head("What the ledger records")
    bullet("sources", n_of(len(ev.sources), len(ev.sources),
                           "pulls, each with rows expected against rows returned"))
    bullet("figures", n_of(len(ev.figures), len(ev.figures),
                           "printed figures, each with a derivation"))
    bullet("checks", n_of(len(ev.checks), len(ev.checks), "tie-outs"))
    bullet("reconciliations", n_of(len(ev.reconciliations), len(report_recon.rows),
                                   "account-month cells, zeros included"))
    bullet("open items", n_of(len(ev.open_items), len(ev.open_items),
                              "things the reader has to be able to see"))

    result = evidence_mod.verify(ledger_path)
    head("Structural check of what was just written")
    # Capped for the same reason `verify` is: `books.py verify <ledger> --all`
    # prints every one, and the ledger itself holds them.
    say(result.render(limit=12))
    say(n_of(len(result.passes), len(result.passes) + len(result.failures),
             "structural checks pass"))
    if len(result.passes) > 12:
        say(f"Showing 12 of {len(result.passes)} passing checks. For all of them: "
            f"python3 bin/books.py verify {work.rel(ledger_path)} --all")
    if not result.ok:
        say("")
        say("A failure here is a stop. Fix it before this goes anywhere.")

    offer = median.check_offer_lines()
    if offer:
        head("Have someone read it before you file")
        say_lines(offer)
        return FAILURE

    head("Having someone check it")
    say("  If an accounting firm reviews this before a filing, they should re-derive")
    say("  rather than read: the same checks against fresh exports taken after the last")
    say("  change. A check that reads our own summary is checking our arithmetic, not")
    say("  the books.")
    return OK


# =========================================================== verify

def cmd_verify(args, work):
    path = Path(args.path)
    if not path.exists():
        raise Refusal(
            f"there is no evidence ledger at {path}.\n"
            f"  Build one with `books.py handoff --out handoff/`.")
    result = evidence_mod.verify(path)
    # Failures print first, then the passes, and both are capped: a real ledger
    # has one pass line per traced figure and that ran to 539 terminal lines.
    limit = None if getattr(args, "all", False) else args.limit
    say(result.render(limit=limit))
    checks = len(result.passes) + len(result.failures)
    say(n_of(len(result.passes), checks, "structural checks pass"))
    if limit is not None and len(result.passes) > limit:
        say(f"Showing {limit} of {len(result.passes)} passing checks. "
            f"Add --all to print them all, or read {path}.")
    if not result.ok:
        say("")
        say("A failure is a stop, not a note. Each one means a number in the document")
        say("cannot be traced, a source was truncated, a date filter did not apply, a")
        say("difference does not close at zero, or something we know is not visible to")
        say("the reader.")
        return FAILURE
    say("")
    say("Every printed figure traces to a derivation, every source is complete, every")
    say("period applied, every difference is stated including the zeros, and every open")
    say("item reaches the reader.")
    return OK


# =========================================================== ready

class ReadyCheck:
    """One thing that has to be true before a set of books can be called ready.

    Each carries the number it measured, for the same reason the exit tests do:
    "reconciled" is a claim and "reconciled 11 of 12 months" is information.
    """

    def __init__(self, name, done, measured, remedy=""):
        self.name = name
        self.done = bool(done)
        self.measured = measured
        self.remedy = remedy

    def row(self):
        return [self.name, "done" if self.done else "OUTSTANDING", self.measured]


def ready_checks(work, args, prof, ledger, live, year, scanned):
    """The bookkeeping half of readiness, re-derived from files every time.

    Nothing here reads a report this tool wrote earlier. A gate that reads its
    own earlier output is a gate that passes because somebody ran a command.
    """
    checks = []

    automation = analyze_automation(ledger, live, filing_year=year)
    checks.append(ReadyCheck(
        "Bank rules have stopped posting into the year being filed",
        not automation.open,
        (f"{int(automation.rules):,} rules, {int(automation.auto_add):,} of them "
         f"posting unseen" if automation.rules is not None
         else "nobody has said how many rules are on"),
        "python3 bin/books.py intake --rules 15 --auto-add 0"))

    _, findings = completeness_now(work, args)
    open_findings = [f for f in findings if f.open]
    checks.append(ReadyCheck(
        "Every account's books plus its queue reach what the bank says it holds",
        not open_findings,
        n_of(len(findings) - len(open_findings), len(findings),
             "accounts have no open completeness finding"),
        "python3 bin/books.py completeness"))

    queue_counts = {}
    try:
        queue_lines, _, _ = load_queue(work, getattr(args, "for_review", None), prof,
                                       quiet=True)
        for line in queue_lines:
            if line.account_key:
                queue_counts[line.account_key] = queue_counts.get(
                    line.account_key, 0) + 1
    except Refusal:
        queue_lines = []
    pairs = analyze_duplicates(ledger, prof, queue_lines, queue_counts)
    checks.append(ReadyCheck(
        "No real account is still entered twice",
        not pairs,
        (n_of(len(pairs), len(pairs), "pairs still look like one account twice")
         if pairs else n_of(0, 0, "duplicate pairs")),
        (f"python3 bin/books.py merge-plan --duplicate {shell_arg(pairs[0].duplicate)}"
         if pairs else "")))

    attestation = load_attestation(work)
    unbooked = None if attestation is None else int(attestation.unbooked_count)
    checks.append(ReadyCheck(
        "The For Review queue is empty, said by the owner looking at the screen",
        attestation is not None and unbooked == 0 and not attestation.problems(),
        ("nobody has looked at the screen and said so" if attestation is None
         else f"{unbooked:,} item(s) unbooked, said by {attestation.by} on "
              f"{attestation.on}"),
        "python3 bin/books.py attest --unbooked 0 --note \"what you checked\""))

    report_recon, parsed = build_recon(work, args, prof, ledger, quiet=True)
    stated, cells = report_recon.cells_stated()
    checks.append(ReadyCheck(
        "Every account-month carries a stated difference, zeros included",
        stated == cells and cells > 0,
        n_of(stated, cells, "account-month cells carry a difference"),
        "python3 bin/books.py statements"))

    report_tests = exit_tests.run(ledger, parsed, prof, report_recon,
                                  attestation=attestation)
    checks.append(ReadyCheck(
        "The ten exit tests pass",
        not report_tests.failed,
        n_of(len(report_tests.passed), len(report_tests.tests), "tests pass"),
        "python3 bin/books.py check"))

    checks.append(ReadyCheck(
        "Everything the firm filing the return asked for has been recorded",
        not scanned.outstanding,
        n_of(len(scanned.requirements) - len(scanned.outstanding),
             len(scanned.requirements), "requirements recorded"),
        "python3 bin/books.py requirements"))

    return checks, report_recon, report_tests, parsed


def ready_is_declarable(checks, scanned):
    """The one condition under which `ready` may say yes, in one testable place.

    It is written as a function rather than as an `if` inside the command for a
    single reason: this is the sentence that would be loosened in a hurry, by
    somebody who wants a package built and has one requirement they cannot get
    an answer for. A test asserts it directly, so loosening it fails the build
    rather than shipping a package that says the books are ready while the firm
    filing the return is still waiting on something.

    A requirement recorded as unanswerable is not outstanding. It carries a
    reason, a date and a name, and it travels into the package as something to
    chase. A blank carries none of those, so it stops this.
    """
    return not [c for c in checks if not c.done] and not scanned.outstanding


def render_filing_answers(prof, scanned, year):
    """Every requirement, what came back, and who said it."""
    out = [
        f"# What the {year} return was given",
        "",
        f"{(prof.entity.name or 'This company').rstrip('.')}. "
        f"Written {today_iso()}.",
        "",
        readiness.NOT_TAX_ADVICE,
        "",
        readiness.summary(scanned),
        "",
        "Every requirement below was raised by something in this company's own "
        "books, and the line that raised it is printed with it. Nothing was "
        "assumed and nothing was left blank.",
        "",
    ]
    for group, subject in readiness.GROUPS:
        reqs = scanned.of_group(group)
        if not reqs:
            continue
        out += [f"## {subject[:1].upper() + subject[1:]}", "", reqs[0].trigger, ""]
        for req in reqs:
            out.append(f"### {req.need}")
            out += ["", f"Why the return needs it: {req.why}", ""]
            if req.evidence:
                out += ["In the books: " + "; ".join(req.evidence) + ".", ""]
            if req.unknown:
                out += [f"**Nobody could answer this.** Recorded on {req.answered_on} "
                        f"by {req.source}: {req.unknown_reason}", "",
                        "The firm filing the return will have to chase this.", ""]
            elif req.document:
                out += [f"Document given on {req.answered_on} by {req.source}: "
                        f"`documents/{req.document.get('stored')}`, "
                        f"{req.document.get('bytes', 0):,} bytes, "
                        f"sha256 `{req.document.get('sha256', '')[:32]}`.", ""]
            elif req.answer:
                out += [f"Answered on {req.answered_on} by {req.source}:", "",
                        f"> {req.answer}", ""]
            else:
                out += ["**Outstanding.** Nothing has been recorded against this.", ""]
    return "\n".join(out) + "\n"


def render_ready_summary(prof, live, year, checks, scanned, report_tests):
    """The covering note the firm filing the return reads first."""
    given = [r for r in scanned.settled if not r.document]
    docs = [r for r in scanned.settled if r.document]
    out = [
        f"# {prof.entity.name or 'This company'}: ready to file {year}",
        "",
        f"Written {today_iso()}. Due {filing_due(live, prof) or 'not recorded'}. "
        f"Basis {prof.entity.basis or 'not stated'}.",
        "",
        readiness.NOT_TAX_ADVICE,
        "",
        "## What was checked",
        "",
        "| Check | Result | Measured |",
        "| --- | --- | --- |",
    ]
    for c in checks:
        out.append(f"| {c.name} | {'done' if c.done else 'OUTSTANDING'} "
                   f"| {c.measured} |")
    out += [
        "",
        f"{len(report_tests.passed)} of {len(report_tests.tests)} exit tests pass, "
        f"with the number each one measured in `exit-tests.md`.",
        "",
        "## What was decided, and by whom",
        "",
        f"{len(scanned.requirements)} requirement(s) were raised by this company's own "
        f"books. {len(given)} were answered, {len(docs)} were satisfied with a "
        f"document, and {len(scanned.unknowns)} came back as unanswerable.",
        "",
        "| What was needed | What came back | Who | When |",
        "| --- | --- | --- | --- |",
    ]
    for req in scanned.requirements:
        if req.unknown:
            came = "could not be answered"
        elif req.document:
            came = f"`documents/{req.document.get('stored')}`"
        elif req.answer:
            came = req.answer.replace("|", "/")[:110]
        else:
            came = "**nothing**"
        out.append(f"| {req.need.replace('|', '/')[:110]} | {came} | "
                   f"{req.source or ''} | {req.answered_on or ''} |")
    out += ["", "## What is still to chase", ""]
    if scanned.unknowns:
        out.append(f"{len(scanned.unknowns)} of {len(scanned.requirements)} came back "
                   f"as something nobody here could answer. Each one is named, with "
                   f"the reason and the date, so it can be chased rather than "
                   f"discovered.")
        out.append("")
        for req in scanned.unknowns:
            out.append(f"- **{req.need}** Recorded {req.answered_on} by {req.source}: "
                       f"{req.unknown_reason}")
        out.append("")
    else:
        out += ["Nothing. Every requirement raised by these books carries an answer or "
                "a document.", ""]
    out += [
        "## What this package is not",
        "",
        "It is a record of what was asked, what was given, and who said it. It does "
        "not decide any treatment and it is not tax advice. Where a governing "
        "document decides an answer, the document is in `documents/` and the reading "
        "of it belongs to whoever signs the return.",
        "",
        "The checks above are the ones this tool knows about. No fixed set of checks "
        "is the same thing as an audit, and whoever reviews this should re-derive "
        "rather than read: the same checks against fresh exports taken after the last "
        "change.",
        "",
    ]
    # The covering note is the page the preparer opens first, so it says who
    # wrote the tool and what the free reading is. The earned mention is
    # rendered from what this run measured, and is empty when nothing was found.
    out += median.blocks(median_signals(prof, None, report_tests, scanned))
    return "\n".join(out) + "\n"


def cmd_ready(args, work):
    prof = load_profile(work, args.profile)
    live = load_live(work)
    year = require_filing_year(live)
    ledger = load_ledger(work, args.exports)
    scanned = scan_requirements(prof, ledger, year)
    sync_requirements(prof, scanned)
    write_requirements(work, scanned)

    checks, report_recon, report_tests, parsed = ready_checks(
        work, args, prof, ledger, live, year, scanned)
    outstanding = [c for c in checks if not c.done]
    declarable = ready_is_declarable(checks, scanned)

    head(f"Ready to file {year}? {prof.entity.name or 'this company'}")
    table(wrap_cell([c.row() for c in checks], column=2),
          ["what has to be true", "state", "measured"])
    say("")
    say(n_of(len(checks) - len(outstanding), len(checks), "checks pass") + ".")

    if not declarable:
        head("Not ready")
        say_lines(_wrapped(
            n_of(len(outstanding), len(checks), "checks are outstanding")
            + ", and nothing here calls a set of books ready while a preparer would "
              "still have to ask for something.", 78))
        say("")
        for c in outstanding:
            say(f"  {c.name}")
            say(f"     measured: {c.measured}")
            if c.remedy:
                say(f"     do: {c.remedy}")
            say("")
        if scanned.outstanding:
            say_lines(_wrapped(
                n_of(len(scanned.outstanding), len(scanned.requirements),
                     "things the firm filing the return asked for")
                + " have nothing recorded against them. The first few:", 78))
            say("")
            for req in scanned.outstanding[:5]:
                say(f"  {req.need}")
                say(f"     raised by: {req.trigger}")
                say(f"     {'give it: python3 bin/books.py provide ' + req.id + ' <file>'}"
                    if req.kind == readiness.DOCUMENT else
                    f"     say it: python3 bin/books.py answer {req.id} \"...\"")
                say("")
            if len(scanned.outstanding) > 5:
                say(f"  and {len(scanned.outstanding) - 5:,} more:")
                say("      python3 bin/books.py requirements")
                say("")
        say_lines(_wrapped(
            "Every one of these becomes a question somebody asks in the week before "
            "the deadline instead. Answering it then costs exactly the same and "
            "arrives too late to change anything, which is the whole reason this "
            "refuses now.", 78))
        say("")
        say_lines(_wrapped(
            "Where you genuinely cannot answer one, record that you could not and "
            "why. That is a record, and the preparer can chase it. A blank is not, "
            "and it reads downstream as a no.", 78))
        say("")
        say("There is no flag that turns this off.")
        return REFUSAL

    out_dir = Path(args.out) if args.out else (work / "handoff")
    if not out_dir.is_absolute():
        out_dir = Path.cwd() / out_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    answers_md = write_text(out_dir / "filing-answers.md",
                            render_filing_answers(prof, scanned, year))
    ready_md = write_text(out_dir / "ready.md",
                          render_ready_summary(prof, live, year, checks, scanned,
                                               report_tests))

    head("Ready")
    say_lines(_wrapped(
        n_of(len(checks), len(checks), "checks pass") + ", and "
        + n_of(len(scanned.requirements), len(scanned.requirements),
               "things the firm filing the return asked for")
        + " carry an answer, a document, or a written reason nobody could give one.",
        78))
    say("")
    bullet("what was decided and by whom", work.rel(ready_md))
    bullet("every requirement and its answer", work.rel(answers_md))
    if scanned.unknowns:
        say("")
        say_lines(_wrapped(
            n_of(len(scanned.unknowns), len(scanned.requirements),
                 "requirements came back as unanswerable")
            + ", and they are named in both files with the reason and the date. They "
              "are what the preparer still has to chase.", 78))

    # The package must never exist saying the books are ready while its own
    # structural check failed. A reader would have the covering note and not the
    # failure, which is the one arrangement worse than no package at all.
    result = cmd_handoff(args, work)
    if result != OK:
        for path in (ready_md, answers_md):
            Path(path).unlink(missing_ok=True)
        say("")
        say_lines(_wrapped(
            "The package was not completed, so the covering note and the answer file "
            "have been removed. They would have said these books are ready while the "
            "structural check above says they are not.", 78))
    return result


# =========================================================== the parser

EPILOG = """
the order a catch-up actually goes in:

  books.py init                                   make the folders
  books.py filing-year 2025 --due 2026-10-15      which year, and by when
  books.py learn      --exports exports/          read the books, mine the rules
  books.py intake                                 what only the screen can tell you
  books.py merge-plan                             one real account entered twice
  books.py statements                             what to go and get, and why
  books.py tieout     --statements statements/    prove you have every transaction
  books.py coverage   --statements statements/    find the months with no statement
  books.py completeness                           is anything in neither place
  books.py questions  --round 1                   ask what only the owner knows
  books.py answer     Q03 "..."                   record what they said
  books.py scope                                  what this deadline actually needs
  books.py catchup    --batch-size 150            propose, in batches they can read
  books.py approve    batch-01                    THEM, in their own terminal
  books.py build-imports --batch batch-01         the files they upload
  books.py fill-gaps                              months a dead feed missed
  books.py entries    --through 2025-12-31        the recurring adjusting entries
  books.py reconcile  --from 2025-01-01 --to 2025-12-31
  books.py check                                  the ten exit tests
  books.py attest     --unbooked 0 --note "..."   the one thing no file can prove
  books.py requirements                           what the return still needs
  books.py provide    filing.contractors.w9 w9.pdf   a document, recorded
  books.py answer     filing.meals.split "..."    a question, recorded
  books.py ready      --out handoff/              refuses while anything stands
  books.py verify     handoff/evidence.json       before it goes anywhere

or, in a browser they are signed in to, with them watching:

  books.py browser confirm --company "..."         which company file is on screen
  books.py browser read --surface for-review --from read.json
  books.py browser plan --kind categorize          batches of 25, one approval each
  books.py approve batch-c1_01                     THEM, in their own terminal
  books.py browser post batch-c1_01 --before 600   opens the run, prints the steps
  books.py browser verify batch-c1_01 --after 575  proves the books moved by 25
  books.py browser runbook disconnect              a refused action, for them to do
  books.py browser status                          approved, open, halted

exit codes:
  0  it ran and everything it checked passed
  1  it ran and something is wrong: a check failed, or a file would not read
  2  it refused, because something it needs is missing or a batch is not approved

the refusals, none of which takes a flag:

  merge-plan       refuses while the account has anything in its For Review
                   queue, because disconnecting deletes it and nothing else
                   holds it
  attest --unbooked 0
                   refuses while an account's books plus its queue do not reach
                   what the bank says it holds
  entries, handoff refuse while bank rules are still posting into the year
                   being filed
  ready            refuses while anything the firm filing the return would
                   have to ask for has nothing recorded against it. Where you
                   cannot answer one, `--cannot "why"` records that you could
                   not, which is a record. A blank is not
  browser post     refuses without an approval for that exact batch, without a
                   confirmed company, while another run is open, while any halt
                   stands, and while any bank rule still posts by itself
  browser verify   halts when the count in the books did not move by exactly
                   the number of rows approved, and a halt stops every batch
  browser          refuses six actions outright, whatever anyone approves:
                   disconnecting a feed, merging accounts, excluding
                   transactions, deleting, voiding, undoing a reconciliation

Nothing here connects to QuickBooks. Every file that reaches import/ is one a
person approved, and it becomes a transaction only when they upload it.

Browser mode is the exception, and it is the one to read about before using:
the agent works in a window the owner signed in to, so what it posts reaches
their books directly. It never sees a credential, it posts only rows inside an
approved batch, and it reads the count before and after to prove it.
"""


def common(parser):
    # SUPPRESS rather than a default: argparse merges the subparser's namespace
    # over the main one, so a real default here would quietly discard a
    # --workdir given before the subcommand name.
    parser.add_argument("--workdir", default=argparse.SUPPRESS, metavar="DIR",
                        help="the folder holding exports/, statements/ and the rest "
                             "(default: the current folder)")
    parser.add_argument("--debug", default=argparse.SUPPRESS, action="store_true",
                        help="show the full traceback when something breaks")
    return parser


def add_profile(parser):
    parser.add_argument("--profile", metavar="PATH",
                        help=f"the profile `learn` built (default: {PROFILE_DEFAULT})")


def add_exports(parser):
    parser.add_argument("--exports", metavar="DIR",
                        help="the folder of QuickBooks xlsx exports (default: exports/)")


def add_statements(parser):
    parser.add_argument("--statements", metavar="DIR",
                        help="the folder of bank and card statements "
                             "(default: statements/)")


def build_parser():
    parent = common(argparse.ArgumentParser(add_help=False))
    p = argparse.ArgumentParser(
        prog="books.py",
        description=("Catch up and close a year of QuickBooks Online. It reads files "
                     "you exported and writes files you review and upload yourself. It "
                     "never connects to QuickBooks and it sends nothing anywhere."),
        epilog=EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
        parents=[parent],
    )
    sub = p.add_subparsers(dest="command", metavar="COMMAND")

    s = sub.add_parser("init", parents=[parent],
                       help="make the working folders and say what goes where")
    s.set_defaults(fn=cmd_init)

    s = sub.add_parser("filing-year", parents=[parent],
                       help="say which year is being filed and when it is due, which "
                            "decides what counts as work")
    s.add_argument("year", nargs="?", metavar="YYYY",
                   help="the year being filed, for example 2025")
    s.add_argument("--due", metavar="DATE",
                   help="the date the return is due, for example 2026-10-15")
    s.add_argument("--by", metavar="NAME", help="who said it")
    add_profile(s)
    s.set_defaults(fn=cmd_filing_year)

    s = sub.add_parser("intake", parents=[parent],
                       help="the five things only the QuickBooks screen and your bank "
                            "can tell you, and how to read each one")
    add_profile(s)
    s.add_argument("--account", metavar="KEY",
                   help="the account these answers are about")
    s.add_argument("--bank-balance", metavar="AMOUNT",
                   help="what the bank itself says the account holds")
    s.add_argument("--as-of", metavar="DATE", help="the date that balance was read")
    s.add_argument("--for-review", type=int, metavar="N",
                   help="how many items are sitting in that account's For review tab")
    s.add_argument("--feed", choices=FEED_STATES,
                   help="live, stopped, or never connected")
    s.add_argument("--feed-last", metavar="DATE", help="when the feed last brought "
                                                       "anything in")
    s.add_argument("--reconciled-through", metavar="DATE",
                   help="the last date this account reconciled clean")
    s.add_argument("--rules", type=int, metavar="N",
                   help="how many bank rules exist")
    s.add_argument("--auto-add", type=int, metavar="N",
                   help="how many of them post without anyone seeing it")
    s.add_argument("--rules-file", metavar="PATH",
                   help="the rules file you exported, which is better than a count")
    s.add_argument("--last-human", metavar="DATE",
                   help="the last day a person worked in the file, from the audit log")
    s.add_argument("--note", metavar="TEXT", help="anything worth recording with it")
    s.add_argument("--by", metavar="NAME", help="who read the screen")
    s.add_argument("--limit", type=int, default=12, metavar="N",
                   help="missing facts to list here (default: 12)")
    s.set_defaults(fn=cmd_intake)

    s = sub.add_parser("learn", parents=[parent],
                       help="read your exports and learn your chart, your vendors and "
                            "where each one goes")
    add_exports(s)
    s.add_argument("--out", metavar="PATH",
                   help=f"where to write the profile (default: {PROFILE_DEFAULT})")
    s.add_argument("--since", metavar="DATE",
                   help="ignore history before this date, for example a chart change")
    s.add_argument("--min-support", type=int, default=2, metavar="N",
                   help="how many past rows a rule needs behind it (default: 2)")
    s.add_argument("--min-confidence", type=float, default=0.75, metavar="F",
                   help="the share of a vendor's history that must agree (default: 0.75)")
    s.set_defaults(fn=cmd_learn)

    s = sub.add_parser("statements", parents=[parent],
                       help="ask for the statements this company needs, by account and "
                            "month, with the reason for each")
    add_profile(s)
    add_exports(s)
    add_statements(s)
    s.add_argument("--limit", type=int, default=12, metavar="N",
                   help="accounts to print here. The written request always holds them "
                        "all (default: 12)")
    s.set_defaults(fn=cmd_statements)

    s = sub.add_parser("tieout", parents=[parent],
                       help="prove each month's transactions are complete, and report "
                            "chain breaks")
    add_statements(s)
    add_profile(s)
    # The exports are read only when there are no statements, so the refusal can
    # say which ones to go and get rather than that a folder is empty.
    add_exports(s)
    s.set_defaults(fn=cmd_tieout)

    s = sub.add_parser("coverage", parents=[parent],
                       help="report which account-months you have no statement for")
    add_statements(s)
    add_profile(s)
    add_exports(s)
    s.add_argument("--from", dest="start", metavar="YYYY-MM", help="first month to check")
    s.add_argument("--to", dest="end", metavar="YYYY-MM", help="last month to check")
    s.add_argument("--limit", type=int, default=12, metavar="N",
                   help="items to print in each list here. The file always holds them "
                        "all (default: 12)")
    s.set_defaults(fn=cmd_coverage)

    s = sub.add_parser("scope", parents=[parent],
                       help="split the queue into what this deadline needs and what "
                            "waits, with a reason against every row set aside")
    add_profile(s)
    s.add_argument("--for-review", metavar="DIR",
                   help="the folder of For Review exports (default: for-review/)")
    s.set_defaults(fn=cmd_scope)

    s = sub.add_parser("completeness", parents=[parent],
                       help="the books plus everything unbooked, against what the bank "
                            "itself says it holds")
    add_profile(s)
    add_exports(s)
    add_statements(s)
    s.add_argument("--for-review", metavar="DIR",
                   help="the folder of For Review exports (default: for-review/)")
    s.add_argument("--settle", metavar="KEY",
                   help="close one account's finding by naming the document that "
                        "explains it")
    s.add_argument("--evidence", metavar="TEXT",
                   help="which statement, which page, which line. Required with "
                        "--settle")
    s.add_argument("--by", metavar="NAME", help="who is recording it")
    s.set_defaults(fn=cmd_completeness)

    s = sub.add_parser("merge-plan", parents=[parent],
                       help="the order to merge or disconnect a duplicated account, "
                            "and a refusal while its queue still holds anything")
    add_profile(s)
    add_exports(s)
    s.add_argument("--duplicate", metavar="KEY",
                   help="the account being retired. Leave it out to list the pairs")
    s.add_argument("--keep", metavar="KEY", help="the account being kept")
    s.add_argument("--action", choices=("merge", "disconnect"), default="merge",
                   help="what is being contemplated (default: merge)")
    s.add_argument("--for-review", metavar="DIR",
                   help="the folder of For Review exports (default: for-review/)")
    s.set_defaults(fn=cmd_merge_plan)

    b = sub.add_parser(
        "browser", parents=[parent],
        help="work inside QuickBooks in your own browser, with your approval")
    bsub = b.add_subparsers(dest="browser_command", metavar="STEP")
    b.set_defaults(fn=cmd_browser_help, browser_parser=b)

    s = bsub.add_parser("confirm", parents=[parent],
                        help="say which company file is on screen, before anything else")
    add_profile(s)
    s.add_argument("--company", required=True, metavar="NAME",
                   help="the company name exactly as the QuickBooks header shows it")
    s.add_argument("--file-id", metavar="ID",
                   help="the company file id from the address bar, if you can read it")
    s.add_argument("--by", metavar="NAME", help="who confirmed it")
    s.set_defaults(fn=cmd_browser_confirm)

    s = bsub.add_parser("read", parents=[parent],
                        help="record what a QuickBooks screen says, and check the shape "
                             "of the read")
    add_profile(s)
    s.add_argument("--surface", required=True,
                   choices=sorted(browser.READERS),
                   help="which screen was read")
    s.add_argument("--from", dest="source", required=True, metavar="PATH",
                   help="the JSON file holding what was read")
    s.add_argument("--by", metavar="NAME", help="who read it")
    s.set_defaults(fn=cmd_browser_read)

    s = bsub.add_parser("plan", parents=[parent],
                        help="build batches small enough to read, one approval each")
    add_profile(s)
    add_exports(s)
    add_statements(s)
    s.add_argument("--kind", required=True, choices=browser.KINDS,
                   help="categorize a For Review queue, add what the feed missed, "
                        "or post journal entries")
    s.add_argument("--account", metavar="KEY", help="just one account")
    s.add_argument("--size", type=int, default=browser.DEFAULT_BATCH_ROWS,
                   metavar="N",
                   help=f"rows per batch (default: {browser.DEFAULT_BATCH_ROWS}). "
                        f"This posts into live books, so a batch is what a person "
                        f"actually reads")
    s.add_argument("--for-review", metavar="DIR",
                   help="the folder of For Review exports (default: for-review/)")
    s.add_argument("--window-days", type=int, default=5, metavar="N",
                   help="days either side to look for an entry already in the books "
                        "(default: 5)")
    s.add_argument("--through", metavar="DATE",
                   help="for --kind journal: the last month to draft")
    s.add_argument("--since", metavar="DATE", help="for --kind journal: skip months "
                                                   "already posted")
    s.set_defaults(fn=cmd_browser_plan)

    s = bsub.add_parser("post", parents=[parent],
                        help="open a run for one approved batch and print its steps")
    add_profile(s)
    s.add_argument("batch", metavar="BATCH", help="for example batch-c1_01")
    s.add_argument("--before", required=True, type=int, metavar="N",
                   help="the count you read on the screen before anything was clicked")
    s.set_defaults(fn=cmd_browser_post)

    s = bsub.add_parser("verify", parents=[parent],
                        help="prove the books moved by exactly what was approved")
    add_profile(s)
    s.add_argument("batch", metavar="BATCH", help="for example batch-c1_01")
    s.add_argument("--after", required=True, type=int, metavar="N",
                   help="the count you read after the batch, on a reloaded page")
    s.set_defaults(fn=cmd_browser_verify)

    s = bsub.add_parser("clear", parents=[parent],
                        help="THE HUMAN-ONLY COMMAND: retire a halt, with what caused it")
    s.add_argument("batch", metavar="BATCH", help="the batch that halted")
    s.add_argument("--note", required=True, metavar="TEXT",
                   help="what the difference turned out to be")
    s.add_argument("--by", metavar="NAME", help="who found it")
    s.set_defaults(fn=cmd_browser_clear)

    s = bsub.add_parser("runbook", parents=[parent],
                        help="write out a refused action as steps for you to do by hand")
    add_profile(s)
    s.add_argument("action", metavar="ACTION", choices=sorted(browser.REFUSED),
                   help="one of: " + ", ".join(sorted(browser.REFUSED)))
    s.add_argument("--account", metavar="KEY", help="which account it concerns")
    s.set_defaults(fn=cmd_browser_runbook)

    s = bsub.add_parser("status", parents=[parent],
                        help="what is approved, what is open, and what is halted")
    add_profile(s)
    s.set_defaults(fn=cmd_browser_status)

    s = sub.add_parser("catchup", parents=[parent],
                       help="work the For Review backlog into batches the owner can read")
    add_profile(s)
    add_exports(s)
    s.add_argument("--for-review", metavar="DIR",
                   help="the folder of For Review exports (default: for-review/)")
    s.add_argument("--batch-size", type=int, default=150, metavar="N",
                   help="rows per batch (default: 150). Nobody reviews nine hundred rows "
                        "in one sitting")
    s.add_argument("--window-days", type=int, default=5, metavar="N",
                   help="days either side to look for an entry already in the books "
                        "(default: 5)")
    s.add_argument("--force", action="store_true",
                   help="rewrite a batch even though it has already been approved")
    s.set_defaults(fn=cmd_catchup)

    s = sub.add_parser("approve", parents=[parent],
                       help="THE HUMAN-ONLY COMMAND: take responsibility for one batch")
    s.add_argument("batch", metavar="BATCH", help="for example batch-01")
    s.add_argument("--by", metavar="NAME",
                   help="who is approving (default: your username)")
    s.set_defaults(fn=cmd_approve)

    s = sub.add_parser("build-imports", parents=[parent],
                       help="after approval, write one batch's files into import/")
    s.add_argument("--batch", required=True, metavar="BATCH", help="for example batch-01")
    add_profile(s)
    add_exports(s)
    s.add_argument("--rules-template", metavar="PATH",
                   help="a rules file exported from your own QuickBooks, which makes the "
                        "output importable rather than a list to type")
    s.set_defaults(fn=cmd_build_imports)

    s = sub.add_parser("fill-gaps", parents=[parent],
                       help="bank upload files for the months a dead feed missed")
    add_profile(s)
    add_exports(s)
    add_statements(s)
    s.add_argument("--account", metavar="KEY",
                   help="just this account, by its number, label or mask")
    s.add_argument("--batch", default="gaps", metavar="TAG",
                   help="the batch these belong to (default: gaps)")
    s.add_argument("--layout", choices=("three", "four"), default="three",
                   help="three-column Date/Description/Amount, or four-column "
                        "Date/Description/Credit/Debit (default: three)")
    s.add_argument("--allow-untied", action="store_true",
                   help="build even though some statements do not tie")
    s.add_argument("--force", action="store_true", help="rewrite the review workbook")
    s.set_defaults(fn=cmd_fill_gaps)

    s = sub.add_parser("entries", parents=[parent],
                       help="draft the recurring adjusting entries and write the typing "
                            "worksheet")
    add_profile(s)
    add_exports(s)
    s.add_argument("--through", required=True, metavar="DATE",
                   help="the last month to draft, for example 2025-12-31")
    s.add_argument("--since", metavar="DATE", help="skip months already posted")
    s.add_argument("--kind", choices=ENTRY_KINDS, help="just one kind")
    s.add_argument("--batch", metavar="TAG", help="tag the entries with a batch")
    s.set_defaults(fn=cmd_entries)

    s = sub.add_parser("reclass", parents=[parent],
                       help="draft one reclassification entry, for a stated reason")
    add_profile(s)
    add_exports(s)
    s.add_argument("--from", dest="from_account", required=True, metavar="ACCT",
                   help="the account the balance is leaving")
    s.add_argument("--to", dest="to_account", required=True, metavar="ACCT",
                   help="the account it belongs in")
    s.add_argument("--reason", required=True, metavar="TEXT",
                   help="why it belongs there. This is the justification, not a memo")
    s.add_argument("--amount", metavar="AMOUNT",
                   help="the SIGNED balance as the trial balance reads it. Read from "
                        "your own books when you leave it out")
    s.add_argument("--on", metavar="DATE",
                   help="the effective date. It decides which period changes")
    s.add_argument("--source", metavar="TEXT", help="the document and clause it rests on")
    s.add_argument("--class", dest="klass", metavar="TEXT",
                   help="the class, where classes are in use")
    s.add_argument("--record", action="store_true",
                   help="write the decision into the profile's decisions_made")
    s.set_defaults(fn=cmd_reclass)

    s = sub.add_parser("wind-down", parents=[parent],
                       help="draft closing entries for a dissolution, or refuse and name "
                            "what is unanswered")
    add_profile(s)
    add_exports(s)
    s.add_argument("--on", metavar="DATE", help="override the target date")
    s.set_defaults(fn=cmd_wind_down)

    s = sub.add_parser("reconcile", parents=[parent],
                       help="book balance against statement balance, per account per "
                            "month")
    add_profile(s)
    add_exports(s)
    add_statements(s)
    s.add_argument("--from", dest="start", metavar="DATE", help="first day of the period")
    s.add_argument("--to", dest="end", metavar="DATE", help="last day of the period")
    s.add_argument("--limit", type=int, default=40, metavar="N",
                   help="rows to print here. The file always holds them all (default: 40)")
    s.set_defaults(fn=cmd_reconcile)

    s = sub.add_parser("check", parents=[parent],
                       help="run the ten exit tests and print the number each one "
                            "measured")
    add_profile(s)
    add_exports(s)
    add_statements(s)
    s.add_argument("--from", dest="start", metavar="DATE")
    s.add_argument("--to", dest="end", metavar="DATE")
    s.add_argument("--through", metavar="DATE",
                   help="also draft the recurring entries through this date, so test 8 "
                        "can read them")
    s.set_defaults(fn=cmd_check)

    s = sub.add_parser("attest", parents=[parent],
                       help="record the owner's typed statement that nothing is unbooked")
    s.add_argument("--unbooked", type=int, metavar="N",
                   help="how many items are still sitting unbooked. A blank is not a zero")
    s.add_argument("--note", metavar="TEXT", help="what they actually checked")
    s.add_argument("--statement", metavar="TEXT",
                   help="their own words, if you have them")
    s.add_argument("--by", metavar="NAME", help="who said it")
    s.add_argument("--on", metavar="DATE", help="when they said it (default: today)")
    s.add_argument("--screenshot", metavar="PATH", help="a screenshot of the empty queue")
    add_profile(s)
    add_exports(s)
    add_statements(s)
    s.add_argument("--for-review", metavar="DIR",
                   help="the folder of For Review exports (default: for-review/)")
    s.set_defaults(fn=cmd_attest)

    s = sub.add_parser("questions", parents=[parent],
                       help="write the next round of questions for the owner")
    add_profile(s)
    s.add_argument("--round", type=int, default=1, metavar="N",
                   help="which round (default: 1)")
    s.add_argument("--limit", type=int, default=10, metavar="N",
                   help="how many to ask (default: 10, which is also the most that gets "
                        "answered)")
    s.add_argument("--force", action="store_true",
                   help="open a new round with the last one still open")
    s.set_defaults(fn=cmd_questions)

    s = sub.add_parser("answer", parents=[parent],
                       help="record one answer, with the date and who gave it")
    s.add_argument("question_id", metavar="ID",
                   help="the question id, or entity.<field>, or wind_down.<key>")
    s.add_argument("answer", metavar="TEXT", nargs="?", default="",
                   help="what they said")
    s.add_argument("--cannot", metavar="REASON",
                   help="record that they could not answer it, and why. A stated "
                        "unknown is workable; a blank reads downstream as a no")
    s.add_argument("--on", metavar="DATE", help="when they said it (default: today)")
    s.add_argument("--by", metavar="NAME", help="who said it")
    add_profile(s)
    s.set_defaults(fn=cmd_answer)

    s = sub.add_parser("requirements", parents=[parent],
                       help="what the firm filing the return still has to be given, "
                            "raised by this company's own books")
    add_profile(s)
    add_exports(s)
    s.add_argument("--limit", type=int, default=10, metavar="N",
                   help="how many to print in full (default: 10). The written list "
                        "holds all of them")
    s.set_defaults(fn=cmd_requirements)

    s = sub.add_parser("provide", parents=[parent],
                       help="record a document against one filing requirement")
    add_profile(s)
    add_exports(s)
    s.add_argument("requirement_id", metavar="ID",
                   help="the requirement id, or the end of it where that is unique")
    s.add_argument("path", metavar="PATH", nargs="?",
                   help="the file itself. It is copied, never moved")
    s.add_argument("--cannot", metavar="REASON",
                   help="record that it cannot be got, and why")
    s.add_argument("--on", metavar="DATE", help="when it was given (default: today)")
    s.add_argument("--by", metavar="NAME", help="who gave it")
    s.set_defaults(fn=cmd_provide)

    s = sub.add_parser("ready", parents=[parent],
                       help="refuses while anything a preparer would have to ask for "
                            "is still missing, and packages the books when nothing is")
    add_profile(s)
    add_exports(s)
    add_statements(s)
    s.add_argument("--for-review", metavar="DIR",
                   help="the exported For Review queue (default: for-review/)")
    s.add_argument("--out", metavar="DIR", help="where to write it (default: handoff/)")
    s.add_argument("--from", dest="start", metavar="DATE")
    s.add_argument("--to", dest="end", metavar="DATE")
    s.add_argument("--pdf", metavar="PATH",
                   help="use this PDF as the deliverable instead of writing one")
    s.set_defaults(fn=cmd_ready)

    s = sub.add_parser("handoff", parents=[parent],
                       help="evidence ledger, change log, exit tests and an archive")
    add_profile(s)
    add_exports(s)
    add_statements(s)
    s.add_argument("--out", metavar="DIR", help="where to write it (default: handoff/)")
    s.add_argument("--from", dest="start", metavar="DATE")
    s.add_argument("--to", dest="end", metavar="DATE")
    s.add_argument("--pdf", metavar="PATH",
                   help="use this PDF as the deliverable instead of writing one")
    s.set_defaults(fn=cmd_handoff)

    s = sub.add_parser("verify", parents=[parent],
                       help="structural check of an evidence ledger before it goes "
                            "anywhere")
    s.add_argument("path", metavar="PATH", help="for example handoff/evidence.json")
    s.add_argument("--limit", type=int, default=12, metavar="N",
                   help="checks of each kind to print. Every failure is printed first; "
                        "the ledger itself holds them all (default: 12)")
    s.add_argument("--all", action="store_true",
                   help="print every check, however many there are")
    s.set_defaults(fn=cmd_verify)

    return p


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "command", None):
        parser.print_help()
        return OK

    # Read these through getattr: the shared parent parser suppresses them so a
    # subparser cannot discard a value given before the subcommand name.
    debug = getattr(args, "debug", False)
    args.debug = debug
    work = Work(getattr(args, "workdir", "."))

    def report(exc, code):
        """Say what happened, and keep the exit code the caller relies on.

        --debug ADDS a traceback. It never changes an exit code, because a
        caller that switches on the code should not get a different answer just
        because somebody wanted to see where a message came from.
        """
        sys.stdout.flush()
        if debug:
            import traceback
            traceback.print_exc()
        sys.stderr.write("\n" + str(exc).rstrip() + "\n")
        return code

    try:
        return args.fn(args, work)
    except REFUSING_ERRORS as exc:
        return report(exc, REFUSAL)
    except Failure as exc:
        return report(exc, FAILURE)
    except KeyboardInterrupt:
        sys.stdout.flush()
        sys.stderr.write("\nstopped.\n")
        return FAILURE
    except Exception as exc:  # noqa: BLE001
        sys.stdout.flush()
        if debug:
            import traceback
            traceback.print_exc()
        sys.stderr.write(
            f"\nSomething broke, and this one is a bug rather than a missing file.\n"
            f"  {type(exc).__name__}: {exc}\n"
            f"  Run it again with --debug to see where, and please report it at\n"
            f"  https://github.com/median-labs/close-the-books/issues\n"
            f"  Please do not attach real financial data to an issue.\n"
        )
        return FAILURE


if __name__ == "__main__":
    sys.exit(main())
