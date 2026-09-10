"""Which account-months are missing, before anyone asks the founder for anything.

The question this answers is the one that wastes the most goodwill in a
catch-up: "please send me the statements", sent to someone who already sent
them. The three ways that goes wrong, all of them observed:

  1. Only the tidy folder gets searched. The one statement saved at the top
     level of the folder is invisible.
  2. Nobody looks inside the zip. A whole quarter can sit in an unextracted
     archive that the founder is certain they sent, because they did.
  3. The file name convention differs per institution, so a month that IS
     present reads as missing and gets asked for again.

So this walks the whole tree, reads zip archives WITHOUT extracting them, and
recognizes the naming conventions banks actually use. Anything it cannot place
goes in `unparsed` rather than being dropped: a coverage checker that silently
ignores a file it does not understand is worse than none, because it reports a
clean grid over an incomplete scan.

    grid = coverage("statements/", ["checking-1234", "card-5678"], "2026-01", "2026-06")
    if not grid.complete:
        print(grid.render())
        print("ask for:", ", ".join(grid.missing))

Standard library only. Reads file NAMES, never file contents; a PDF is never
opened here, which is what keeps it fast over a few thousand files.
"""

from __future__ import annotations

import os
import re
import zipfile
from dataclasses import dataclass, field

from .util import month_range as _month_range
from .util import norm_text

__all__ = ["coverage", "CoverageGrid", "StatementHit", "parse_statement_name"]

SCAN_EXTS = {".pdf", ".csv", ".zip"}
SKIP_DIRS = {".git", ".venv", "venv", "node_modules", "__pycache__", ".ruff_cache",
             ".idea", ".vscode", "__MACOSX"}

# A file with a date in its name that is NOT a periodic statement. Checked
# first, so a 1099 or a trade confirm never occupies a row in the grid.
DENY_TOKENS = (
    "confirm", "invoice", "1099", "w-9", "w9", "tax-form", "tax_return",
    "agreement", "engagement", "receipt", "notice", "void", "returned-payment",
    "duplicate", "letter", "disclosure",
)

# A weak pattern (a bare date suffix) only counts as a statement when one of
# these appears in the name or the path.
STATEMENT_TOKENS = (
    "statement", "stmt", "checking", "savings", "credit", "card", "sweep",
    "treasury", "vault", "brokerage", "securities", "money-market", "payroll",
    "activity", "transactions", "bank",
)

ACCOUNT_KEYWORDS = (
    "money-market", "business-cash", "choice-sweep", "treasury", "brokerage",
    "securities", "vault", "credit", "savings", "checking", "sweep", "card",
    "payroll", "cash",
)

MONTH_NAMES = {
    "january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6,
    "july": 7, "august": 8, "september": 9, "october": 10, "november": 11,
    "december": 12, "jan": 1, "feb": 2, "mar": 3, "apr": 4, "jun": 6, "jul": 7,
    "aug": 8, "sep": 9, "sept": 9, "oct": 10, "nov": 11, "dec": 12,
}

UUID_RE = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
DUP_SUFFIX_RE = re.compile(r"\s*\(\d+\)$")


# --------------------------------------------------------------- name parsing
# Each row is (label, strength, regex, handler). Handlers return
# (year, month, name_part) where name_part is whatever is left to name the
# account with. Every pattern here corresponds to a convention a real
# institution uses; the comment names the shape.

def _p(pattern):
    return re.compile(pattern)


def _h_yyyymmdd_statement(m):
    # 20260130-statements-6133.pdf: the date is the CYCLE END date
    return int(m.group(1)), int(m.group(2)), m.group(4)


def _h_name_monthly_statement(m):
    # acme-checking-1234-monthly-statement-2026-05.pdf
    return int(m.group(2)), int(m.group(3)), m.group(1)


def _h_name_statement_month(m):
    # acme-card-5678-statement-2026-05.pdf (and -full)
    return int(m.group(2)), int(m.group(3)), m.group(1)


def _h_month_name_statement(m):
    # 2026-05-checking-1234-statement.pdf
    return int(m.group(1)), int(m.group(2)), m.group(3)


def _h_dotted_period(m):
    # acme-treasury_statement_2026.01.01-2026.02.01.pdf: the END month wins
    return int(m.group(2)), int(m.group(3)), m.group(1)


def _h_period(m):
    # acme-card-2026-05-08-to-2026-06-07.pdf: the END month wins
    return int(m.group(2)), int(m.group(3)), m.group(1)


def _h_comma_download(m):
    # STATEMENTS,July2026-6250.pdf (a real download convention)
    month = MONTH_NAMES.get(m.group(1).lower())
    return (int(m.group(2)), month, m.group(3)) if month else None


def _h_month_word(m):
    # acme-checking-january-2026.pdf / acme-checking-jan-2026.pdf
    month = MONTH_NAMES.get(m.group(2).lower())
    return (int(m.group(3)), month, m.group(1)) if month else None


def _h_close_date(m):
    # acme-credit-2026-07-04.pdf: a close date, mapped to the month it closes in
    return int(m.group(2)), int(m.group(3)), m.group(1)


def _h_leading_date(m):
    # 2026-01-31-checking-1234-statement.pdf
    return int(m.group(1)), int(m.group(2)), m.group(4)


def _h_month_suffix(m):
    # acme-checking-1234-2026-06.pdf
    return int(m.group(2)), int(m.group(3)), m.group(1)


PATTERNS = [
    ("yyyymmdd-statement-mask", "strong",
     _p(r"^(\d{4})(\d{2})(\d{2})-statements?-?([a-z0-9-]*)-?$"), _h_yyyymmdd_statement),
    ("name-monthly-statement-month", "strong",
     _p(r"^(.*)-monthly-statements?-(\d{4})-(\d{2})$"), _h_name_monthly_statement),
    ("name-statement-month", "strong",
     _p(r"^(.*)-statements?-(\d{4})-(\d{2})(?:-full)?$"), _h_name_statement_month),
    ("month-name-statement", "strong",
     _p(r"^(\d{4})-(\d{2})-(.*)-statements?$"), _h_month_name_statement),
    ("dotted-period", "strong",
     _p(r"^(.*)_statements?_\d{4}\.\d{2}\.\d{2}-(\d{4})\.(\d{2})\.\d{2}$"), _h_dotted_period),
    ("comma-download", "strong",
     _p(r"^statements?,\s*([a-z]+)\s*(\d{4})-([a-z0-9]+)$"), _h_comma_download),
    ("period-start-to-end", "weak",
     _p(r"^(.*)-\d{4}-\d{2}-\d{2}-to-(\d{4})-(\d{2})-\d{2}$"), _h_period),
    ("name-month-word", "weak",
     _p(r"^(.*?)[-_]([a-z]{3,9})[-_](\d{4})$"), _h_month_word),
    ("name-close-date", "weak",
     _p(r"^(.*)-(\d{4})-(\d{2})-(\d{2})$"), _h_close_date),
    ("leading-close-date", "weak",
     _p(r"^(\d{4})-(\d{2})-(\d{2})-(.*)$"), _h_leading_date),
    ("name-month", "weak",
     _p(r"^(.*)-(\d{4})-(\d{2})$"), _h_month_suffix),
]


def normalize_stem(name) -> str:
    stem = os.path.splitext(str(name))[0]
    stem = DUP_SUFFIX_RE.sub("", stem).strip()
    return stem.lower().strip()


def account_token(name_part, full_path="") -> str:
    """Group by the account mask, else the distinctive account word."""
    cleaned = UUID_RE.sub("", str(name_part).lower())
    cleaned = re.sub(r"\b(?:account|monthly|statements?|selected|pdfs?)\b", " ", cleaned)
    cleaned = re.sub(r"\b(19|20)\d{2}\b", " ", cleaned)          # strip years
    masks = re.findall(r"(?<![0-9a-z])(\d{3,6})(?![0-9a-z])", cleaned)
    if masks:
        return masks[-1]
    hay = cleaned + " " + str(full_path).lower()
    for keyword in ACCOUNT_KEYWORDS:
        if keyword in hay:
            return keyword
    residual = re.sub(r"[^a-z]+", "-", cleaned).strip("-")
    return residual or "unknown"


def parse_statement_name(name, full_path=""):
    """(month "YYYY-MM", account token, pattern label), or None."""
    stem = normalize_stem(name)
    hay = stem + " " + str(full_path).lower()
    if any(token in hay for token in DENY_TOKENS):
        return None
    for label, strength, regex, handler in PATTERNS:
        m = regex.match(stem)
        if not m:
            continue
        result = handler(m)
        if not result:
            continue
        year, month, name_part = result
        if not (1 <= month <= 12) or not (2000 <= year <= 2100):
            continue
        if strength == "weak" and not any(t in hay for t in STATEMENT_TOKENS):
            continue
        return "%04d-%02d" % (year, month), account_token(name_part, full_path), label
    return None


# ------------------------------------------------------------------- walking

@dataclass
class StatementHit:
    month: str
    account: str
    path: str
    zip_entry: str = ""
    pattern: str = ""

    @property
    def in_zip(self) -> bool:
        return bool(self.zip_entry)

    @property
    def display(self) -> str:
        return f"{self.path}!{self.zip_entry}" if self.zip_entry else self.path


def walk_statements(dirpath):
    """Yield (path, zip_entry_or_empty) for every file worth naming.

    A zip is read through its central directory, so nothing is extracted, no
    temporary file is written, and a corrupt archive is reported rather than
    crashing the scan.
    """
    dirpath = str(dirpath)
    if os.path.isfile(dirpath):
        roots = [(os.path.dirname(dirpath) or ".", [], [os.path.basename(dirpath)])]
    else:
        roots = os.walk(dirpath)
    for root, dirnames, filenames in roots:
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS and not d.startswith(".")]
        for filename in sorted(filenames):
            extension = os.path.splitext(filename)[1].lower()
            if extension not in SCAN_EXTS:
                continue
            path = os.path.join(root, filename)
            if extension != ".zip":
                yield path, ""
                continue
            try:
                with zipfile.ZipFile(path) as archive:
                    entries = archive.namelist()
            except (zipfile.BadZipFile, OSError):
                yield path, ""      # unreadable archive: surfaced as unparsed
                continue
            for entry in entries:
                if entry.endswith("/") or os.path.basename(entry).startswith("."):
                    continue
                if os.path.splitext(entry)[1].lower() not in (".pdf", ".csv"):
                    continue
                yield path, entry


# ----------------------------------------------------------- account matching

def _account_aliases(account_key) -> set:
    """Every token a declared account may show up as in a file name."""
    slug = re.sub(r"[^a-z0-9]+", " ", norm_text(account_key)).strip()
    tokens = {t for t in slug.split() if t}
    tokens.add(slug.replace(" ", "-"))
    tokens.add(slug.replace(" ", ""))
    return {t for t in tokens if t}


def _match_account(token, path, declared_aliases):
    """Which declared account this hit belongs to, or None."""
    hay = norm_text(path)
    for account_key, aliases in declared_aliases.items():
        if token in aliases:
            return account_key
        for alias in aliases:
            if len(alias) >= 4 and alias in hay:
                return account_key
    return None


# ------------------------------------------------------------------- the grid

@dataclass
class CoverageGrid:
    months: list = field(default_factory=list)
    accounts: list = field(default_factory=list)
    hits: list = field(default_factory=list)          # StatementHit
    present: dict = field(default_factory=dict)       # account -> month -> [hit]
    missing: list = field(default_factory=list)       # "account YYYY-MM"
    in_zip: list = field(default_factory=list)        # present, still archived
    unparsed: list = field(default_factory=list)      # named nothing we know
    unassigned: list = field(default_factory=list)    # a statement for no declared account
    root: str = ""

    @property
    def complete(self) -> bool:
        """The gate. True only when every declared account-month is held."""
        return not self.missing

    def missing_for(self, account_key) -> list:
        prefix = account_key + " "
        return [m[len(prefix):] for m in self.missing if m.startswith(prefix)]

    def cell(self, account_key, month) -> str:
        found = self.present.get(account_key, {}).get(month, [])
        if not found:
            return "."
        return "Y" if any(not h.in_zip for h in found) else "Z"

    def render(self, limit=None) -> str:
        """The grid and the lists. `limit` caps each list for a terminal.

        The grid itself always prints whole: it is one row per account and it is
        the point of the report. The lists under it are what ran away, because
        21 accounts over 24 months is 504 items on ONE semicolon-separated line,
        which is not readable and not skimmable. With a limit the first few are
        named, the rest are counted, and the file keeps all of them.
        """
        out = [
            f"# Statement coverage, {self.months[0]} to {self.months[-1]}"
            if self.months else "# Statement coverage",
            "",
            "Y held, Z held but still inside a zip, . MISSING.",
            "",
        ]
        if self.accounts and self.months:
            out.append("| account | " + " | ".join(m[2:] for m in self.months) + " |")
            out.append("|---|" + "---|" * len(self.months))
            for account_key in self.accounts:
                cells = [self.cell(account_key, m) for m in self.months]
                out.append(f"| {account_key} | " + " | ".join(cells) + " |")
            out.append("")

        def capped(items):
            shown = items if limit is None else items[:limit]
            return list(shown), len(items) - len(shown)

        shown, rest = capped(self.missing)
        out.append(f"MISSING ({len(self.missing)}): "
                   + ("; ".join(shown) if shown else "none")
                   + (f"; ... {rest} more" if rest else ""))
        for label, items in (
            ("STILL IN A ZIP (held, but extract before parsing)", self.in_zip),
            ("NOT MATCHED TO A DECLARED ACCOUNT", self.unassigned),
        ):
            if not items:
                continue
            shown, rest = capped(items)
            out.append(f"{label} ({len(items)}):")
            out.extend("  " + line for line in shown)
            if rest:
                out.append(f"  ... {rest} more")
        if self.unparsed:
            shown, rest = capped(self.unparsed)
            out.append(
                f"UNRECOGNIZED FILE NAMES ({len(self.unparsed)}). Nothing is dropped "
                f"silently; check these by eye before believing a missing month:")
            out.extend("  " + line for line in shown)
            if rest:
                out.append(f"  ... {rest} more")
        return "\n".join(out)

    def __str__(self) -> str:
        return self.render()


def _as_month(value, field_name) -> str:
    if hasattr(value, "year") and hasattr(value, "month"):
        return "%04d-%02d" % (value.year, value.month)
    s = str(value).strip()
    if re.fullmatch(r"\d{4}-\d{2}", s):
        return s
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", s):
        return s[:7]
    raise ValueError(f"{field_name}: expected YYYY-MM or a date, got {value!r}")


def coverage(dirpath, accounts=(), start=None, end=None) -> CoverageGrid:
    """Walk `dirpath` and report every missing account-month in the window.

    `accounts` is what the founder says they have; leave it empty and the
    accounts are discovered from the file names instead, which reports what IS
    there but cannot tell you about an account nobody has ever sent a statement
    for. Declaring the accounts is what makes the answer a gate.

    `start` and `end` are "YYYY-MM" or dates. Leave them out and the window is
    the span of what was found.
    """
    hits, unparsed = [], []
    root = str(dirpath)
    for path, entry in walk_statements(root):
        name = os.path.basename(entry) if entry else os.path.basename(path)
        display = f"{path}!{entry}" if entry else path
        parsed = parse_statement_name(name, display)
        if not parsed:
            unparsed.append(os.path.relpath(display, root) if not entry else display)
            continue
        month, token, label = parsed
        hits.append(StatementHit(month=month, account=token, path=path,
                                 zip_entry=entry, pattern=label))

    declared = [a for a in (accounts or []) if str(a).strip()]
    if declared:
        aliases = {a: _account_aliases(a) for a in declared}
        assigned, unassigned = {}, []
        for hit in hits:
            account_key = _match_account(hit.account, hit.display, aliases)
            if account_key is None:
                unassigned.append(f"{hit.account} {hit.month}  {hit.display}")
                continue
            hit.account = account_key
            assigned.setdefault(account_key, {}).setdefault(hit.month, []).append(hit)
        account_keys = list(declared)
    else:
        assigned, unassigned = {}, []
        for hit in hits:
            assigned.setdefault(hit.account, {}).setdefault(hit.month, []).append(hit)
        account_keys = sorted(assigned)

    found_months = sorted({h.month for h in hits})
    first = _as_month(start, "start") if start else (found_months[0] if found_months else "")
    last = _as_month(end, "end") if end else (found_months[-1] if found_months else "")
    if first and last:
        import datetime as _dt
        months = _month_range(_dt.date(int(first[:4]), int(first[5:7]), 1),
                              _dt.date(int(last[:4]), int(last[5:7]), 1))
    else:
        months = []

    missing, in_zip = [], []
    for account_key in account_keys:
        for month in months:
            found = assigned.get(account_key, {}).get(month, [])
            if not found:
                missing.append(f"{account_key} {month}")
                continue
            if all(h.in_zip for h in found):
                in_zip.append(f"{account_key} {month} -> {found[0].display}")

    return CoverageGrid(
        months=months,
        accounts=account_keys,
        hits=hits,
        present=assigned,
        missing=missing,
        in_zip=in_zip,
        unparsed=sorted(unparsed),
        unassigned=sorted(unassigned),
        root=root,
    )
