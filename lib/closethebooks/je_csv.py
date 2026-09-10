"""Journal-entry import CSV, for the places that can actually import one.

READ THIS FIRST: THE US CANNOT IMPORT JOURNAL ENTRIES
    Intuit, on the US version: "Although data can be imported into the US
    version of QuickBooks Online, journal entry import is currently not an
    option."
    https://quickbooks.intuit.com/learn-support/en-us/reports-and-accounting/importing-journal-entries-in-quickbooks-online-us-version/00/1312709
    verified-on: 2026-09-09  VERIFIED: intuit-doc

    Journal entry import IS available in the Canada and UK versions, on all
    plans. So this writer has exactly two audiences:

      (a) QuickBooks Online Canada and UK, where the file goes in through
          the gear icon > Import data > Journal entries; and
      (b) third party importer apps in the US, which is the other way a US
          company gets a batch of entries in without typing them.

    A US company with no importer app types the entries by hand at
    +New > Journal Entry, and the artifact they need is
    `je_worksheet.write_worksheet`, not this CSV. That is why `write_entries`
    defaults to region="US" and writes the worksheet alongside the CSV: a file
    a US founder cannot use, handed over with no explanation, costs them the
    afternoon it takes to find out.

A journal entry is the sharpest tool in the box, so this writer is the most
suspicious module in the library. It refuses more than it accepts.

WHAT IT REFUSES, AND WHY
    An entry whose `check()` fails. Unbalanced, no lines, or no recorded basis.
    A basis is not paperwork: it is the sentence that answers "why does this
    entry exist", and an entry nobody can answer that about should never reach
    a file a founder might upload.

    An account key the caller cannot resolve to a full name. QuickBooks matches
    the Account Name column against the account's full name exactly as it
    renders it, parent and child joined by a colon, for example
    `Operating Expenses:Software Subscriptions`. A near miss does not fail
    loudly: QuickBooks offers to create a brand new account with that name, and
    a founder clicking through an import wizard will say yes. So the writer
    raises instead, and lists every key it could not resolve.

    A line with a debit and a credit on the same row, or neither.

    An entry with no batch tag. Every Description carries `[batch-<tag>]` so
    that a founder who wants the whole batch gone can search that tag in
    QuickBooks, see exactly the lines this import created, and void them. An
    import you cannot reverse in one search is not reversible in practice.

COLUMNS  (verified-on: 2026-09-09  VERIFIED: no)
    Journal No., Journal Date, Account Name, Debits, Credits, Description,
    Name, Class

    One row per line. The Journal No. repeats across every line of one entry
    and is what QuickBooks uses to group them, so two different entries sharing
    a number would silently merge into one. The writer refuses duplicates.

    These columns are what the Canada and UK import screens ask for, written
    down from Intuit's help rather than reproduced against a live company file.
    Nobody here has run this CSV into a real CA or UK company yet.

SECURITY
    Account names, descriptions, names and classes all go through
    `util.csv_safe`. Descriptions can carry a bank descriptor forward, which is
    attacker chosen text. The Date, Debits and Credits columns are formatted
    here from a `datetime.date` and a `Decimal` and are deliberately not passed
    through `csv_safe`, whose apostrophe would break a negative number.
"""

from __future__ import annotations

import re

from .util import ZERO, csv_safe, money, parse_date, plain, write_csv

QBO_DATE = "%m/%d/%Y"

HEADER = (
    "Journal No.", "Journal Date", "Account Name", "Debits", "Credits",
    "Description", "Name", "Class",
)

# The columns that can carry free text, and therefore attacker text.
SAFE_COLUMNS = ("Account Name", "Description", "Name", "Class")

# Matches approval.BATCH_RE, so a filename and an in-book description agree.
BATCH_TAG_RE = re.compile(r"^[A-Za-z0-9_]+$")

# QuickBooks memo and description fields. verified-on: 2026-09-09  VERIFIED: no
MAX_DESCRIPTION = 4000


class UnresolvedAccount(ValueError):
    """An account key had no full name. Writing it would create a new account."""


class EntryError(ValueError):
    """An entry cannot be expressed as a QuickBooks journal entry import."""


# ------------------------------------------------------------- resolution

def resolve_full_name(key, resolve, where: str) -> str:
    """Turn an account key into the full name QuickBooks renders.

    `resolve` is a callable key -> full name. Passing None means the caller is
    asserting the keys are already full names, which is only safe when they
    came straight out of a chart-of-accounts export.
    """
    key = "" if key is None else str(key).strip()
    if not key:
        raise UnresolvedAccount(f"{where}: no account given")
    if resolve is None:
        return key
    try:
        full = resolve(key)
    except UnresolvedAccount:
        raise
    except Exception as exc:
        raise UnresolvedAccount(f"{where}: resolving {key!r} failed: {exc}") from exc
    if not full or not str(full).strip():
        raise UnresolvedAccount(
            f"{where}: account key {key!r} does not resolve to a QuickBooks "
            f"account name. Add it to the chart mapping. Writing the key would "
            f"make QuickBooks offer to create a new account called {key!r}."
        )
    return str(full).strip()


# ------------------------------------------------------------------ lines

def _line_parts(line, where: str):
    """Accept (account, debit, credit, memo[, name[, klass]]) or a mapping."""
    if isinstance(line, dict):
        acct = line.get("account")
        debit = line.get("debit", ZERO)
        credit = line.get("credit", ZERO)
        memo = line.get("memo", "")
        name = line.get("name", "")
        klass = line.get("class", line.get("klass", ""))
        return acct, debit, credit, memo, name, klass
    parts = list(line)
    if len(parts) < 4 or len(parts) > 6:
        raise EntryError(
            f"{where}: expected (account, debit, credit, memo) with optional "
            f"name and class, got {len(parts)} values"
        )
    parts += [""] * (6 - len(parts))
    return parts[0], parts[1], parts[2], parts[3], parts[4], parts[5]


def _batch_tag(entry, fallback) -> str:
    tag = (getattr(entry, "batch_tag", "") or "").strip() or (fallback or "").strip()
    if not tag:
        raise EntryError(
            f"entry {getattr(entry, 'number', '?')}: no batch tag. Every imported "
            f"line carries its batch in the Description so the whole batch can be "
            f"found and voided in QuickBooks later. Pass batch_tag= to "
            f"write_entries, or set it on the entry."
        )
    if not BATCH_TAG_RE.match(tag):
        raise EntryError(
            f"batch tag {tag!r} must be letters, digits and underscores only, so "
            f"that it survives a QuickBooks search and matches the file name."
        )
    return tag


def entry_rows(entry, resolve=None, batch_tag=None) -> list:
    """The CSV rows for one entry. Raises unless the entry is postable."""
    entry.check()
    number = str(getattr(entry, "number", "") or "").strip()
    if not number:
        raise EntryError("an entry has no number; QuickBooks groups lines by it")
    date = parse_date(getattr(entry, "date", None), field=f"entry {number} date")
    tag = _batch_tag(entry, batch_tag)

    rows, unresolved = [], []
    for i, line in enumerate(entry.lines, start=1):
        where = f"entry {number} line {i}"
        acct, debit, credit, memo, name, klass = _line_parts(line, where)
        d, c = money(debit, f"{where} debit"), money(credit, f"{where} credit")
        if d < ZERO or c < ZERO:
            raise EntryError(f"{where}: debits and credits are never negative; flip the side")
        if d != ZERO and c != ZERO:
            raise EntryError(f"{where}: has both a debit ({d}) and a credit ({c})")
        if d == ZERO and c == ZERO:
            raise EntryError(f"{where}: has neither a debit nor a credit")
        try:
            full = resolve_full_name(acct, resolve, where)
        except UnresolvedAccount as exc:
            unresolved.append(str(exc))
            full = ""
        text = str(memo or getattr(entry, "memo", "") or "").strip()
        description = f"{text} [batch-{tag}]".strip()
        if len(description) > MAX_DESCRIPTION:
            raise EntryError(
                f"{where}: description is {len(description)} characters, over the "
                f"{MAX_DESCRIPTION} QuickBooks accepts. Shorten the memo."
            )
        rows.append([
            number,
            date.strftime(QBO_DATE),
            full,
            plain(d) if d != ZERO else "",
            plain(c) if c != ZERO else "",
            description,
            str(name or "").strip(),
            str(klass or "").strip(),
        ])
    if unresolved:
        raise UnresolvedAccount("; ".join(unresolved))
    return rows


# ----------------------------------------------------------------- regions

# Where journal entry import exists at all. verified-on: 2026-09-09
# VERIFIED: intuit-doc  (Intuit's own help, not reproduced in a live company)
IMPORT_REGIONS = ("CA", "UK")
NO_IMPORT_REGIONS = ("US",)
REGION_ALIASES = {
    "US": "US", "USA": "US", "UNITED STATES": "US",
    "CA": "CA", "CAN": "CA", "CANADA": "CA",
    "UK": "UK", "GB": "UK", "UNITED KINGDOM": "UK", "GREAT BRITAIN": "UK",
}

INTUIT_JE_URL = (
    "https://quickbooks.intuit.com/learn-support/en-us/reports-and-accounting/"
    "importing-journal-entries-in-quickbooks-online-us-version/00/1312709"
)


class UnknownRegion(ValueError):
    """A region this writer has no facts about. Guessing would be worse."""


def normalize_region(region) -> str:
    key = str(region or "").strip().upper()
    if key not in REGION_ALIASES:
        raise UnknownRegion(
            f"region {region!r} is not one this module has checked. Known: "
            f"{sorted(set(REGION_ALIASES.values()))}. Whether journal entry "
            f"import exists is a per-country fact, so it is not guessed."
        )
    return REGION_ALIASES[key]


def region_note(region: str, path, worksheet_path="") -> str:
    """The companion note. Says who can use this file and who cannot."""
    region = normalize_region(region)
    name = _basename(path)
    if region == "US":
        body = [
            f"# {name}: you cannot import this into QuickBooks Online US",
            "",
            "QuickBooks Online US does not import journal entries. Intuit: \"Although data "
            "can be imported into the US version of QuickBooks Online, journal entry import "
            "is currently not an option.\"",
            f"{INTUIT_JE_URL}",
            "verified-on: 2026-09-09",
            "",
            "This CSV is still worth keeping. It is usable by:",
            "",
            "- a third party journal entry importer app (the paid route), and",
            "- QuickBooks Online Canada and UK, where the import does exist.",
            "",
            "In the US the two real options are to type the entries in at "
            "+New > Journal Entry, or to buy an importer app.",
        ]
        if worksheet_path:
            body += [
                "",
                f"The typing worksheet was written alongside this file: "
                f"{_basename(worksheet_path)}. Work from that one. It lays every entry out "
                f"in the order the QuickBooks screen asks for it, carries a running total "
                f"and a tick column, and groups the repeats so QuickBooks' Make recurring "
                f"button can do them for you.",
            ]
    else:
        body = [
            f"# {name}: import path for QuickBooks Online {region}",
            "",
            f"Journal entry import exists in the {region} version, on all plans. Gear icon "
            f"(top right) > Import data > Journal entries, then upload this CSV and map the "
            f"columns.",
            "",
            "It would NOT work in the US version, which has no journal entry import at all "
            f"({INTUIT_JE_URL}). If this company is US, re-run with region=\"US\" and type "
            "the entries from the worksheet instead.",
            "verified-on: 2026-09-09",
        ]
    return "\n".join(body).rstrip() + "\n"


def _basename(path) -> str:
    import os
    return os.path.basename(str(path))


def _sibling(path, suffix: str, ext: str):
    """A file beside `path`, sharing its stem. Keeps a batch together."""
    import os
    root, _ = os.path.splitext(str(path))
    return f"{root}{suffix}{ext}"


class EntryFile(int):
    """The number of rows written, plus what actually happened.

    An int subclass on purpose. Callers and tests that treat the result as a
    row count keep working, and the ones that need to know whether the file is
    importable, where the note went and where the worksheet went can ask.
    """

    def __new__(cls, rows, path="", region="US", importable=False,
                worksheet="", note_path="", note="", message=""):
        self = super().__new__(cls, int(rows))
        self.rows = int(rows)
        self.path = str(path)
        self.region = region
        self.importable = bool(importable)
        self.worksheet = str(worksheet)
        self.note_path = str(note_path)
        self.note = note
        self.message = message
        return self

    def __repr__(self):
        return (f"EntryFile(rows={self.rows}, region={self.region!r}, "
                f"importable={self.importable}, worksheet={self.worksheet!r})")


def write_entries(path, entries, resolve=None, batch_tag=None, region="US",
                  worksheet=True) -> EntryFile:
    """Write the journal-entry import CSV. Returns an EntryFile (an int of rows).

    Rows, not entries: one entry of four lines is four rows. Nothing is written
    unless every entry passes, so a refusal never leaves a half file behind.

    `region` decides whether the file is usable at all. "CA" and "UK" import
    journal entries; "US" does not, and for "US" this writer also produces the
    typing worksheet beside the CSV and says so in the returned EntryFile and
    in a companion note written next to the file. Silently handing a US founder
    a file their QuickBooks will never accept is the failure this argument
    exists to prevent.

    Pass worksheet=False only when the caller is writing the worksheet itself.
    """
    region = normalize_region(region)
    entries = list(entries)

    rows, seen = [], {}
    for entry in entries:
        number = str(getattr(entry, "number", "") or "").strip()
        if number in seen:
            raise EntryError(
                f"two entries share Journal No. {number!r}. QuickBooks groups "
                f"lines by that number, so they would import as one merged entry."
            )
        seen[number] = True
        rows.extend(entry_rows(entry, resolve=resolve, batch_tag=batch_tag))
    written = write_csv(path, list(HEADER), rows, safe_columns=SAFE_COLUMNS)

    worksheet_path = ""
    if region in NO_IMPORT_REGIONS and worksheet:
        from . import je_worksheet
        worksheet_path = _sibling(path, "-worksheet", ".xlsx")
        je_worksheet.write_worksheet(
            worksheet_path, entries, resolve=resolve, batch_tag=(batch_tag or ""))

    note = region_note(region, path, worksheet_path)
    note_path = _sibling(path, "-import-note", ".md")
    with open(note_path, "w", encoding="utf-8") as fh:
        fh.write(note)

    if region in NO_IMPORT_REGIONS:
        message = (
            f"QuickBooks Online {region} cannot import journal entries, so this CSV is for a "
            f"third party importer app only. The typing worksheet was written alongside it"
            + (f": {_basename(worksheet_path)}." if worksheet_path else ".")
        )
    else:
        message = (
            f"QuickBooks Online {region} imports journal entries: gear icon > Import data > "
            f"Journal entries. This CSV would not work in the US version."
        )

    return EntryFile(
        written,
        path=str(path),
        region=region,
        importable=region in IMPORT_REGIONS,
        worksheet=worksheet_path,
        note_path=note_path,
        note=note,
        message=message,
    )


# ------------------------------------------------------------------- split

# An entry is "sure" only when something outside the engine's own guesswork
# backs it: a document, a rule the company's own history supports, or an answer
# the founder already gave. These words in a basis mean the engine was filling a
# gap, and a filled gap goes in front of a human.
REVIEW_WORDS = (
    "assum", "estimat", "guess", "unverified", "unclear", "probabl", "likely",
    "appears", "seems", "tbd", "to be confirmed", "needs founder", "question",
    "ask the founder", "no document", "placeholder",
)

# Used only when an entry carries its own confidence. `ProposedEntry` has no
# such field today; `getattr` keeps this working if one is added later without
# this module having to change.
SURE_THRESHOLD = 0.9


def is_sure(entry) -> bool:
    """Whether one entry can go in the file a founder imports without reading.

    Convention, since `ProposedEntry` carries a `basis` string and no
    confidence number:
      1. `entry.check()` must pass. An entry that cannot be posted is neither.
      2. If the entry has a `confidence` attribute, it decides, at
         SURE_THRESHOLD.
      3. Otherwise the basis decides: any REVIEW_WORDS in it, or a basis that
         opens with `?`, means review.
    """
    entry.check()
    conf = getattr(entry, "confidence", None)
    if isinstance(conf, (int, float)):
        return float(conf) >= SURE_THRESHOLD
    basis = (getattr(entry, "basis", "") or "").strip()
    if basis.startswith("?"):
        return False
    low = basis.lower()
    return not any(word in low for word in REVIEW_WORDS)


def split_sure_and_review(entries):
    """(sure, review). Raises on an entry that is not postable at all.

    Deliberately fail closed: an unbalanced or basis-less entry is not sorted
    into a pile, it stops the run. Sorting it into `review` would produce a
    review file that `write_entries` then refuses to write, which hides the
    real problem one step further from the person who has to fix it.
    """
    sure, review = [], []
    for entry in entries:
        (sure if is_sure(entry) else review).append(entry)
    return sure, review
