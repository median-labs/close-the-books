"""Load the QuickBooks Online xlsx exports into the `Ledger` model.

QuickBooks Online can export eight report kinds that together describe a
company's year: Account List, Trial Balance, Balance Sheet, Balance Sheet
Detail, General Ledger, Journal, Profit and Loss Detail, and Profit and Loss
by Month. This module reads them.

Every rule in here exists because a real export broke a naive reader. The
comments say which, because the naive version of each is the one a maintainer
will reach for when "simplifying" this file.

Sign convention is the engine's: debit positive, `signed = debit - credit`.
Amounts are `Decimal` via `util.money`; dates are `datetime.date` via
`util.parse_date`. No network access, no dependency beyond openpyxl.
"""

from __future__ import annotations

import calendar
import os
import re
import warnings
from dataclasses import dataclass, field
from decimal import Decimal

from .model import (
    CREDIT_NORMAL_TYPES, DEBIT_NORMAL_TYPES, Account, JournalLine, Ledger,
)
from .util import ZERO, MoneyError, iso, money, month_key, norm_text, parse_date, try_date

try:
    import openpyxl
except ImportError as _exc:  # pragma: no cover - environment problem, not logic
    raise ImportError("qbo_exports needs openpyxl: pip install openpyxl") from _exc


class ExportError(Exception):
    """An export could not be read, or did not foot.

    Raised rather than returned. A trial balance that does not foot is not a
    warning to be logged and stepped over: every downstream number rests on it.
    """


# ---------------------------------------------------------------- the sheet
#
# TRAP 1, the one that matters most. QuickBooks writes numeric cells into xlsx
# as literal formula strings: the cell's stored value is the eight characters
# "=15000.00", not the number 15000.00. It never writes a cached result,
# because no spreadsheet application ever opened the file to compute one.
#
# `openpyxl.load_workbook(..., data_only=True)` returns *the cached result*.
# With no cache it returns 0.0 or None. So the data_only=True reading of a real
# trial balance is a full-length, correctly-shaped, entirely zero report, which
# foots perfectly (0.00 == 0.00) and is completely false. On a real export
# every single amount cell read as zero this way, and it still "balanced".
#
# Therefore: this module opens every workbook with data_only=False and pushes
# every cell through util.money, which strips the leading "=".
#
# DO NOT "fix" this back to data_only=True. It looks like the obviously correct
# flag, it is what every tutorial shows, and it silently zeroes the books.
_DATA_ONLY = False

# QuickBooks also writes *cell-reference* formulas, and these are a different
# animal from the literal ones above. Its subtotal rows hold things like
# "=I7+I8" (a real one-year Journal carried hundreds of them) and its report
# total rows hold a deeply parenthesised "=((((B6)+(B7))+(B8))...)". These are
# not numbers and util.money cannot read them; Decimal("I7+I8") raises.
#
# They are still worth having, because a printed total is the report's own
# claim about itself and checking our parse against it is the only way to prove
# we did not silently drop rows. So this module resolves them, by looking the
# referenced cells up in the sheet it already has, with a tiny parser that
# understands exactly + - and parentheses. `eval` is never used: these files
# are third-party input.
_REF_RE = re.compile(r"^\$?([A-Za-z]{1,3})\$?([0-9]{1,7})$")
_TOKEN_RE = re.compile(r"\s*(\$?[A-Za-z]{1,3}\$?[0-9]{1,7}|[0-9]+(?:\.[0-9]+)?|[-+()])")
_LITERAL_FORMULA_RE = re.compile(r"^=\s*-?[0-9][0-9,]*(?:\.[0-9]+)?$")

# The footer QuickBooks stamps on the last row: a weekday, a date, a time zone,
# and sometimes the basis. Two shapes are in the wild, and the basis moves:
#   "Accrual Basis Monday, March 02, 2026 08:03 PM GMTZ"
#   "Monday, Mar 02, 2026 12:01:05 PM GMT-8 - Accrual Basis"
# and on some reports (Account List, Journal) there is no basis at all.
_FOOTER_RE = re.compile(
    r"(monday|tuesday|wednesday|thursday|friday|saturday|sunday)|(accrual|cash)\s+basis",
    re.I,
)
_BASIS_RE = re.compile(r"\b(accrual|cash)\s+basis\b", re.I)
_STAMP_RE = re.compile(r"\b([A-Z][a-z]{2,8})\.?\s+(\d{1,2}),\s*(\d{4})\b")


def _col_letter(index0: int) -> str:
    """0 -> A, 25 -> Z, 26 -> AA. openpyxl has this, spelled out to stay cheap."""
    out, n = "", index0 + 1
    while n:
        n, r = divmod(n - 1, 26)
        out = chr(65 + r) + out
    return out


class _Sheet:
    """One worksheet, fully read, with formula resolution.

    Read whole rather than streamed. The largest real export seen is ~3,600
    rows by 11 columns, which is nothing, and having the whole grid is what
    makes resolving "=I7+I8+..." possible without a second pass over the file.
    """

    def __init__(self, path):
        self.path = str(path)
        self.name = os.path.basename(self.path)
        if not os.path.exists(self.path):
            raise ExportError(f"{self.name}: no such file: {self.path}")
        try:
            with warnings.catch_warnings():
                # QBO omits the default style block; openpyxl warns once per
                # file about it and it means nothing for our purposes.
                warnings.filterwarnings("ignore", message=".*no default style.*")
                wb = openpyxl.load_workbook(self.path, data_only=_DATA_ONLY, read_only=True)
        except Exception as exc:
            raise ExportError(f"{self.name}: cannot open as xlsx: {exc}") from exc
        try:
            ws = wb[wb.sheetnames[0]]
            self.rows = [list(r) for r in ws.iter_rows(values_only=True)]
        finally:
            wb.close()
        self._num_cache = {}
        self.footer_index = self._find_footer()

    # -- raw access ------------------------------------------------------
    def cell(self, r, c):
        if 0 <= r < len(self.rows) and 0 <= c < len(self.rows[r]):
            return self.rows[r][c]
        return None

    def text(self, r, c) -> str:
        v = self.cell(r, c)
        return "" if v is None else str(v).strip()

    def width(self, r) -> int:
        return len(self.rows[r]) if 0 <= r < len(self.rows) else 0

    def row_is_blank(self, r) -> bool:
        return all(self.text(r, c) == "" for c in range(self.width(r)))

    def first_text(self, r) -> str:
        for c in range(self.width(r)):
            t = self.text(r, c)
            if t:
                return t
        return ""

    def _find_footer(self):
        """Index of the trailing stamp row, or None.

        Found from the bottom: the last non-blank row, but only if it looks
        like a stamp (a weekday or a basis) and only its first cell is filled.
        A report whose last row is real data keeps that data.
        """
        for r in range(len(self.rows) - 1, -1, -1):
            if self.row_is_blank(r):
                continue
            only_first = all(self.text(r, c) == "" for c in range(1, self.width(r)))
            if only_first and _FOOTER_RE.search(self.first_text(r)):
                return r
            return None
        return None

    def footer_text(self) -> str:
        return self.first_text(self.footer_index) if self.footer_index is not None else ""

    # -- numbers ---------------------------------------------------------
    def number(self, r, c):
        """The cell as a Decimal, or None if it is not a number at all.

        Blank is 0.00, not None: in a Debit/Credit pair exactly one side is
        filled and the other is genuinely zero.
        """
        return self._number(r, c, frozenset())

    def _number(self, r, c, seen):
        key = (r, c)
        if key in self._num_cache and not seen:
            return self._num_cache[key]
        raw = self.cell(r, c)
        out = None
        if raw is None:
            out = ZERO
        elif isinstance(raw, (int, float, Decimal)):
            out = money(raw)
        else:
            s = str(raw).strip()
            if s == "":
                out = ZERO
            elif s.startswith("="):
                if _LITERAL_FORMULA_RE.match(s):
                    # "=15000.00" -- util.money strips the "=".
                    out = money(s)
                else:
                    out = self._eval(s[1:], (r, c), seen)
            else:
                try:
                    out = money(s)
                except MoneyError:
                    out = None
        if not seen:
            self._num_cache[key] = out
        return out

    def _eval(self, expr, origin, seen):
        """Resolve a +/- expression over cell references. None if unsupported."""
        if origin in seen:
            return None  # circular reference; the file is broken, say so quietly
        seen = seen | {origin}
        tokens, pos = [], 0
        while pos < len(expr):
            m = _TOKEN_RE.match(expr, pos)
            if not m:
                return None  # a function call, a range, a sheet reference: not ours
            tokens.append(m.group(1))
            pos = m.end()
        if not tokens:
            return None
        try:
            value, idx = self._parse_expr(tokens, 0, seen)
        except _EvalUnsupported:
            return None
        return None if idx != len(tokens) else value

    def _parse_expr(self, toks, i, seen):
        value, i = self._parse_atom(toks, i, seen)
        while i < len(toks) and toks[i] in "+-":
            op = toks[i]
            rhs, i = self._parse_atom(toks, i + 1, seen)
            value = value + rhs if op == "+" else value - rhs
        return value, i

    def _parse_atom(self, toks, i, seen):
        if i >= len(toks):
            raise _EvalUnsupported()
        tok = toks[i]
        if tok == "-":
            value, i = self._parse_atom(toks, i + 1, seen)
            return -value, i
        if tok == "+":
            return self._parse_atom(toks, i + 1, seen)
        if tok == "(":
            value, i = self._parse_expr(toks, i + 1, seen)
            if i >= len(toks) or toks[i] != ")":
                raise _EvalUnsupported()
            return value, i + 1
        m = _REF_RE.match(tok)
        if m:
            col = 0
            for ch in m.group(1).upper():
                col = col * 26 + (ord(ch) - 64)
            value = self._number(int(m.group(2)) - 1, col - 1, seen)
            if value is None:
                raise _EvalUnsupported()
            return value, i + 1
        try:
            return money(tok), i + 1
        except MoneyError:
            raise _EvalUnsupported() from None


class _EvalUnsupported(Exception):
    pass


# ------------------------------------------------------------ report face
#
# TRAP 7. Rows 1 to 4 carry the face of the report, and the face is evidence:
# `period_text` is the only proof inside the file that the date filter the
# operator typed actually applied. A General Ledger headed "January 1-December
# 31, 2024" is not an FY2025 general ledger no matter what it was asked for,
# and that mistake is invisible in the rows themselves.
#
# The face is NOT laid out consistently. Measured across one real export set:
#   Trial Balance:  row 1 company, row 2 title, row 3 period
#   every other:    row 1 title,   row 2 company, row 3 period
# so the parser identifies the title by vocabulary and takes the company from
# whichever of the first two rows is left over. And the basis is not on the
# face at all: it is in the footer stamp on the last row.

_REPORT_TITLES = (
    "profit and loss by month", "profit and loss detail", "profit and loss",
    "balance sheet detail", "balance sheet", "trial balance", "general ledger",
    "account list", "journal", "transaction list", "statement of cash flows",
    "transaction detail by account", "chart of accounts",
)

_FACE_ROWS = 6


@dataclass
class ReportFace:
    """What the report says it is. Every field is evidence, not decoration."""
    company: str = ""
    title: str = ""
    period_text: str = ""          # verbatim, e.g. "January 1-December 31, 2024"
    basis: str = ""                # "accrual" | "cash" | ""
    as_of: object = None           # date, for an "As of ..." report
    generated_at: object = None    # date the export was produced
    period_start: object = None
    period_end: object = None
    source_file: str = ""

    def covers(self, other) -> bool:
        """True when this face's period overlaps another's. Used to stop
        load_all counting the same year twice out of two different reports."""
        a1, a2 = self.period_start or self.as_of, self.period_end or self.as_of
        b1, b2 = other.period_start or other.as_of, other.period_end or other.as_of
        if not (a1 and a2 and b1 and b2):
            return False
        return a1 <= b2 and b1 <= a2


def _parse_period(text):
    """(as_of, start, end) from a QuickBooks period line, or (None, None, None).

    Four shapes are in the wild and all four appear in one export set:
      "As of December 31, 2025"
      "January-December, 2025"
      "January 1-December 31, 2024"
      "January 1, 2024-December 31, 2025"
    """
    t = (text or "").strip()
    if not t:
        return None, None, None
    low = t.lower()
    if low.startswith("as of"):
        d = try_date(t[5:].strip().lstrip(","))
        return (d, None, d) if d else (None, None, None)

    m = re.match(
        r"^([A-Za-z]{3,9})\.?\s*(\d{1,2})?(?:\s*,\s*(\d{4}))?\s*[-‒–—]\s*"
        r"([A-Za-z]{3,9})\.?\s*(\d{1,2})?(?:\s*,\s*(\d{4}))?$",
        t,
    )
    if not m:
        return None, None, None
    m1, d1, y1, m2, d2, y2 = m.groups()
    y1 = y1 or y2
    y2 = y2 or y1
    if not (y1 and y2):
        return None, None, None
    # Everything goes through util.parse_date; no string slicing on dates.
    start = try_date(f"{m1} {int(d1 or 1)}, {y1}")
    end_first = try_date(f"{m2} 1, {y2}")
    if not (start and end_first):
        return None, None, None
    last = calendar.monthrange(end_first.year, end_first.month)[1]
    end = try_date(f"{m2} {int(d2 or last)}, {y2}")
    if not end:
        return None, None, None
    return None, start, end


def _parse_face(sheet: _Sheet) -> ReportFace:
    face = ReportFace(source_file=sheet.name)
    candidates = []
    for r in range(min(_FACE_ROWS, len(sheet.rows))):
        if sheet.footer_index is not None and r == sheet.footer_index:
            continue
        t = sheet.first_text(r)
        if t:
            candidates.append((r, t))

    title_row = None
    for r, t in candidates[:3]:
        n = norm_text(t)
        for known in _REPORT_TITLES:
            if n == known or n.startswith(known):
                face.title, title_row = t, r
                break
        if title_row is not None:
            break

    # The company is the other of the first two face rows.
    head = [c for c in candidates if c[0] < 2]
    for r, t in head:
        if r != title_row:
            face.company = t
            break
    if title_row is None and head:
        face.title = head[0][1]
        face.company = head[1][1] if len(head) > 1 else ""

    for r, t in candidates:
        if r == title_row or t == face.company:
            continue
        as_of, start, end = _parse_period(t)
        if as_of or start:
            face.period_text = t            # verbatim, trap 7
            face.as_of, face.period_start, face.period_end = as_of, start, end
            break

    stamp = sheet.footer_text()
    hay = stamp + " " + " ".join(t for _, t in candidates)
    m = _BASIS_RE.search(hay)
    if m:
        face.basis = m.group(1).lower()
    m = _STAMP_RE.search(stamp)
    if m:
        face.generated_at = try_date(f"{m.group(1)} {m.group(2)}, {m.group(3)}")
    return face


def report_face(path) -> ReportFace:
    """Read only the face of a report. Cheap enough to run over a whole folder."""
    return _parse_face(_Sheet(path))


# ------------------------------------------------------------- header find

def _headers(sheet: _Sheet, row: int) -> dict:
    """{normalised header text: column index} for one row."""
    out = {}
    for c in range(sheet.width(row)):
        t = norm_text(sheet.text(row, c))
        if t and t not in out:
            out[t] = c
    return out


def _find_header(sheet: _Sheet, predicate, limit: int = 14):
    """First row in the top `limit` whose header map satisfies `predicate`.

    TRAP 3. Column positions are not fixed. A trial balance's Debit and Credit
    are right-aligned into whatever columns the report happened to use, the
    account column has no header at all on some exports, and a General Ledger
    pushes every column one to the right of where a Journal puts it. Reading by
    a hard-coded index works on the file you tested and silently reads the
    wrong column on the next one, so everything here reads by header position.
    """
    for r in range(min(limit, len(sheet.rows))):
        h = _headers(sheet, r)
        if h and predicate(h):
            return r, h
    return None, {}


def _col(headers: dict, *names):
    """Column index for the first header name present, else None."""
    for n in names:
        if n in headers:
            return headers[n]
    return None


def _is_total_label(text: str) -> bool:
    """TRAP 6. A subtotal row is not a transaction.

    QuickBooks closes every section with "Total for <account>" and every report
    with "TOTAL" or "Total". Parsing one as a transaction double counts the
    whole section beneath it, which is exactly how a general ledger comes out
    at twice its real size and still, infuriatingly, balances.
    """
    n = norm_text(text)
    return n.startswith("total for") or n in {"total", "totals"} or n.startswith("total ")


def _account_number(label: str) -> str:
    """Leading account number, if the chart is numbered.

    QuickBooks writes the same account three different ways across three
    reports: "1000 Assets:Operating Checking" on the trial balance, then
    "1000 Operating Checking" on the general ledger, and the bare "1000" in
    the Journal's distribution-account column. The number is
    the only part all three agree on, so it is the key. Where a chart is
    unnumbered the full label is the key, which is what Account.key already
    falls back to.
    """
    m = re.match(r"^\s*(\d{3,10})\b", label or "")
    return m.group(1) if m else ""


def _account_key(label: str) -> str:
    return _account_number(label) or (label or "").strip()


# ----------------------------------------------------------- account list

class AccountSet(list):
    """`list[Account]` that also carries the report's own footing evidence.

    A plain list is what callers want; the extra attributes are how the caller
    proves the parse was complete rather than assuming it.
    """

    def __init__(self, accounts=(), *, face=None, source_file="",
                 printed_total=None, discrepancies=None):
        super().__init__(accounts)
        self.face = face or ReportFace()
        self.source_file = source_file
        self.printed_total = printed_total
        self.discrepancies = list(discrepancies or [])

    @property
    def sum_of_balances(self) -> Decimal:
        return sum((a.balance for a in self), ZERO)

    @property
    def foots(self) -> bool:
        return not self.discrepancies


# QuickBooks' Account List writes account types in the PLURAL, and the model's
# DEBIT_NORMAL_TYPES vocabulary is singular. On a real export the majority of
# the chart carried a type outside that vocabulary ("expenses", "other
# current assets", "fixed assets", "other current liabilities", "other assets",
# "long term liabilities", "accounts payable (A/P)"), which silently made
# Account.debit_normal False for every expense and every asset in the company.
# That in turn decides which way a General Ledger amount has to be flipped, so
# a loader that passes the raw string through corrupts the sign of the ledger.
# Normalise here, at the seam; model.py is a fixed interface and is not edited.
_CANONICAL_TYPE = {
    "accounts receivable (a/r)": "accounts receivable",
    "accounts receivables": "accounts receivable",
    "accounts payable (a/p)": "accounts payable",
    "accounts payables": "accounts payable",
    "other current assets": "other current asset",
    "other assets": "other asset",
    "fixed assets": "fixed asset",
    "current assets": "other current asset",
    "other current liabilities": "other current liability",
    "long term liabilities": "long term liability",
    "credit cards": "credit card",
    "expenses": "expense",
    "other expenses": "other expense",
    "cost of goods sold": "cost of goods sold",
    "income": "income",
    "other income": "other income",
    "equity": "equity",
    "bank": "bank",
}


def _canonical_type(raw: str) -> str:
    """QuickBooks' account type in the vocabulary model.py speaks."""
    t = norm_text(raw)
    if not t:
        return ""
    if t in _CANONICAL_TYPE:
        return _CANONICAL_TYPE[t]
    t = re.sub(r"\s*\([^)]*\)\s*$", "", t).strip()   # drop a trailing "(A/P)"
    if t in _CANONICAL_TYPE:
        return _CANONICAL_TYPE[t]
    if t.endswith("s") and t[:-1] in DEBIT_NORMAL_TYPES | CREDIT_NORMAL_TYPES:
        return t[:-1]
    return t


# Account type decides the role wherever the type is unambiguous. Only the
# deliberately vague QuickBooks types fall through to name keywords, so a
# "Bank" account named "Prepaid Card" stays a bank. No rule keys off an
# account number: chart numbering differs in every company.
_ROLE_BY_TYPE = {
    "bank": "bank",
    "credit card": "card",
    "accounts receivable": "ar",
    "accounts payable": "ap",
    "equity": "equity",
    "income": "revenue",
    "other income": "revenue",
    "expense": "expense",
    "cost of goods sold": "expense",
    "other expense": "expense",
}
_VAGUE_TYPES = {
    "other current asset", "other asset", "fixed asset",
    "other current liability", "long term liability",
}
_ROLE_BY_NAME = (
    ("clearing", "clearing"),
    ("opening balance equity", "obe"),
    ("undeposited", "clearing"),
    ("prepaid", "prepaid"),
    ("accumulated amortization", "amortization"),
    ("amortization", "amortization"),
    ("intangible", "intangible"),
    ("due from", "intercompany"),
    ("due to", "intercompany"),
    ("intercompany", "intercompany"),
    ("safe", "safe"),
    ("shareholder", "shareholder"),
    ("payroll", "payroll_liability"),
)


def _role_for(full_name: str, acct_type: str) -> str:
    """A starting role. A company profile may override it; nothing infers a
    role from an account number."""
    t = _canonical_type(acct_type)
    if t in _ROLE_BY_TYPE:
        return _ROLE_BY_TYPE[t]
    n = norm_text(full_name)
    if t in _VAGUE_TYPES or not t:
        for needle, role in _ROLE_BY_NAME:
            if needle in n:
                return role
    return "other"


def load_account_list(path) -> AccountSet:
    """The chart of accounts, as `Account` records."""
    sheet = _Sheet(path)
    face = _parse_face(sheet)
    hrow, h = _find_header(
        sheet,
        lambda x: ("type" in x) and any(k in x for k in ("full name", "name", "account #", "account number", "account")),
    )
    if hrow is None:
        raise ExportError(f"{sheet.name}: no Account List header row (need Type and a name column)")

    c_num = _col(h, "account #", "account number", "number", "account no")
    c_name = _col(h, "full name", "account", "name", "account name")
    c_type = _col(h, "type", "account type")
    c_detail = _col(h, "detail type", "subtype", "detail")
    c_desc = _col(h, "description")
    c_bal = _col(h, "total balance", "balance", "balance total")
    if c_name is None:
        raise ExportError(f"{sheet.name}: Account List has no name column")

    accounts, printed_total, discrepancies = [], None, []
    for r in range(hrow + 1, len(sheet.rows)):
        if sheet.footer_index is not None and r == sheet.footer_index:
            continue
        if sheet.row_is_blank(r):
            continue
        label = sheet.text(r, c_name)
        first = sheet.first_text(r)
        if _is_total_label(first) and not label:
            if c_bal is not None:
                printed_total = sheet.number(r, c_bal)
            continue
        if not label:
            continue
        number = sheet.text(r, c_num) if c_num is not None else ""
        number = number if re.fullmatch(r"[\w.\-]{1,12}", number or "") else ""
        balance = sheet.number(r, c_bal) if c_bal is not None else ZERO
        if balance is None:
            discrepancies.append(f"row {r + 1}: balance {sheet.text(r, c_bal)!r} is not a number")
            balance = ZERO
        parts = [p.strip() for p in label.split(":") if p.strip()]
        acct_type = sheet.text(r, c_type) if c_type is not None else ""
        accounts.append(Account(
            name=parts[-1] if parts else label,
            number=number,
            full_name=label,
            type=_canonical_type(acct_type),
            subtype=sheet.text(r, c_detail) if c_detail is not None else "",
            role=_role_for(label, acct_type),
            balance=balance,
            parent=":".join(parts[:-1]),
            source_row=r + 1,
        ))
        if c_desc is not None:
            pass  # description is not on Account; deliberately dropped

    out = AccountSet(accounts, face=face, source_file=sheet.name,
                     printed_total=printed_total, discrepancies=discrepancies)
    # TRAP 8. Check the parse against the report's own total. The Account List
    # total is a plain sum of the balance column, parents included, so summing
    # every parsed row is the right comparison here.
    if printed_total is not None and out.sum_of_balances != printed_total:
        out.discrepancies.append(
            f"account list total: parsed {out.sum_of_balances} vs printed {printed_total} "
            f"(difference {out.sum_of_balances - printed_total})"
        )
    return out


# ---------------------------------------------------------- trial balance

@dataclass
class TrialBalance:
    as_of: object = None
    basis: str = ""
    rows: list = field(default_factory=list)     # (account, debit, credit)
    total_debits: Decimal = ZERO
    total_credits: Decimal = ZERO
    foots: bool = False
    printed_debits: object = None
    printed_credits: object = None
    face: ReportFace = field(default_factory=ReportFace)
    source_file: str = ""
    discrepancies: list = field(default_factory=list)

    @property
    def difference(self) -> Decimal:
        return self.total_debits - self.total_credits

    def account_keys(self) -> list:
        return [_account_key(a) for a, _, _ in self.rows]

    def by_key(self) -> dict:
        """{account key: signed balance}, debit-positive, the engine convention.

        A trial balance prints one row per account, so a repeated key means the
        parse read one account twice; the amounts are summed rather than
        overwritten so that shows up as a difference instead of vanishing.
        """
        out = {}
        for label, debit, credit in self.rows:
            key = _account_key(label)
            out[key] = out.get(key, ZERO) + (debit - credit)
        return out

    def label_for(self, key) -> str:
        for label, _, _ in self.rows:
            if _account_key(label) == key:
                return label
        return key


def load_trial_balance(path) -> TrialBalance:
    """The trial balance, with footing as a hard gate.

    TRAP 4. If debits do not equal credits this raises. It is not a warning.
    A trial balance is the one artifact whose entire purpose is that it foots;
    a version of it that does not is not a slightly-imperfect trial balance,
    it is evidence that the parse or the books are wrong, and every number
    derived from it afterwards would inherit that.
    """
    sheet = _Sheet(path)
    face = _parse_face(sheet)
    hrow, h = _find_header(sheet, lambda x: "debit" in x and "credit" in x)
    if hrow is None:
        raise ExportError(f"{sheet.name}: no Debit/Credit header row found in the first 14 rows")
    c_debit, c_credit = h["debit"], h["credit"]
    c_acct = _col(h, "account", "account name", "distribution account", "name")
    if c_acct is None:
        # The real exports leave the account column header blank. Fall back to
        # the leftmost column to the left of Debit, which is where it sits.
        c_acct = 0 if c_debit > 0 else None
    if c_acct is None:
        raise ExportError(f"{sheet.name}: trial balance has no account column")

    rows, discrepancies = [], []
    printed_debits = printed_credits = None
    for r in range(hrow + 1, len(sheet.rows)):
        if sheet.footer_index is not None and r == sheet.footer_index:
            continue
        if sheet.row_is_blank(r):
            continue
        label = sheet.text(r, c_acct)
        if _is_total_label(label):
            printed_debits = sheet.number(r, c_debit)
            printed_credits = sheet.number(r, c_credit)
            continue
        if not label:
            continue  # TRAP 6: a blank account cell is never a transaction
        d = sheet.number(r, c_debit)
        c = sheet.number(r, c_credit)
        if d is None or c is None:
            discrepancies.append(
                f"row {r + 1} ({label}): unreadable amount "
                f"{sheet.text(r, c_debit)!r}/{sheet.text(r, c_credit)!r}"
            )
            continue
        if d == ZERO and c == ZERO and not sheet.text(r, c_debit) and not sheet.text(r, c_credit):
            continue  # a section header carrying no amounts
        rows.append((label, d, c))

    total_debits = sum((d for _, d, _ in rows), ZERO)
    total_credits = sum((c for _, _, c in rows), ZERO)

    tb = TrialBalance(
        as_of=face.as_of or face.period_end,
        basis=face.basis,
        rows=rows,
        total_debits=total_debits,
        total_credits=total_credits,
        printed_debits=printed_debits,
        printed_credits=printed_credits,
        face=face,
        source_file=sheet.name,
        discrepancies=discrepancies,
    )

    if not rows:
        raise ExportError(f"{sheet.name}: trial balance has no account rows")
    if total_debits != total_credits:
        raise ExportError(
            f"{sheet.name}: trial balance does not foot: "
            f"debits {total_debits} vs credits {total_credits} "
            f"(difference {total_debits - total_credits})"
        )
    # TRAP 8. Also tie to the report's own printed total. Equal-but-wrong is a
    # real outcome: dropping a whole section loses a debit and its credit
    # together, and the remainder still balances.
    for side, parsed, printed in (
        ("debits", total_debits, printed_debits),
        ("credits", total_credits, printed_credits),
    ):
        if printed is not None and parsed != printed:
            raise ExportError(
                f"{sheet.name}: parsed {side} {parsed} do not match the report's "
                f"printed total {printed} (difference {parsed - printed})"
            )
    tb.foots = True
    return tb


# ------------------------------------------------- general ledger / journal

class LineSet(list):
    """`list[JournalLine]` carrying the report's own footing evidence."""

    def __init__(self, lines=(), *, face=None, source_file="",
                 discrepancies=None, beginning_balances=None, notes=None,
                 sign_basis="debit_positive"):
        super().__init__(lines)
        self.face = face or ReportFace()
        self.source_file = source_file
        self.discrepancies = list(discrepancies or [])
        self.beginning_balances = dict(beginning_balances or {})
        self.notes = list(notes or [])
        # "debit_positive": debit - credit, the engine convention, safe to post.
        # "report": the export's own natural sign, credit-normal accounts not
        # yet flipped because no chart was supplied. See load_general_ledger.
        self.sign_basis = sign_basis

    @property
    def total_debits(self) -> Decimal:
        return sum((l.debit for l in self), ZERO)

    @property
    def total_credits(self) -> Decimal:
        return sum((l.credit for l in self), ZERO)

    @property
    def difference(self) -> Decimal:
        return self.total_debits - self.total_credits

    @property
    def balanced(self) -> bool:
        return self.total_debits == self.total_credits

    @property
    def foots(self) -> bool:
        """Balanced AND agreeing with every printed subtotal in the file."""
        return self.balanced and not self.discrepancies


def _credit_normal(index: dict, label: str) -> bool:
    """True when this account's natural balance is a credit.

    Unknown accounts answer False: leaving an amount in the report's own sign
    is visible in the footing, whereas flipping one on a guess is not.
    """
    a = _lookup_account(index, label)
    return bool(a) and a.type in CREDIT_NORMAL_TYPES


def _chart_index(accounts) -> dict:
    """Every way an export might name an account -> its Account.

    A chart arrives as an AccountSet, a plain list of Account, or a dict from
    Ledger.accounts. The same account is spelled three ways across three
    reports, so index all of them.
    """
    if not accounts:
        return {}
    values = accounts.values() if isinstance(accounts, dict) else accounts
    index = {}
    for a in values:
        if not isinstance(a, Account):
            continue
        for alias in (a.key, a.number, a.full_name, a.name,
                      norm_text(a.full_name), norm_text(a.name)):
            if alias and alias not in index:
                index[alias] = a
    return index


def _lookup_account(index: dict, label: str):
    if not index:
        return None
    for alias in (label, _account_key(label), _account_number(label), norm_text(label)):
        if alias and alias in index:
            return index[alias]
    return None


def _split_signed(amount: Decimal):
    """A signed amount into the (debit, credit) pair the model wants."""
    return (amount, ZERO) if amount >= ZERO else (ZERO, -amount)


def _amount_columns(headers: dict):
    """(mode, columns) for whichever amount layout this export uses.

    TRAP 5. Two layouts are in the wild and both are normal QuickBooks output:
    a General Ledger prints one signed `Amount` column, a Journal prints
    separate `Debit` and `Credit` columns. Reading a signed Amount as if it
    were a Debit turns every credit into a negative debit, which balances
    against nothing and quietly halves the ledger.
    """
    c_debit, c_credit = _col(headers, "debit"), _col(headers, "credit")
    if c_debit is not None and c_credit is not None:
        return "debit_credit", (c_debit, c_credit)
    c_amount = _col(headers, "amount", "amount (signed)", "signed amount")
    if c_amount is not None:
        return "amount", (c_amount,)
    return None, ()


def _read_amount(sheet, r, mode, cols):
    """(debit, credit) or None if the row's amount cells are unreadable."""
    if mode == "amount":
        v = sheet.number(r, cols[0])
        return None if v is None else _split_signed(v)
    d, c = sheet.number(r, cols[0]), sheet.number(r, cols[1])
    if d is None or c is None:
        return None
    # Some exports render one side as a negative on the other side.
    if d < ZERO and c == ZERO:
        return ZERO, -d
    if c < ZERO and d == ZERO:
        return -c, ZERO
    return d, c


def load_general_ledger(path, accounts=None) -> LineSet:
    """The General Ledger: one section per account, transactions beneath.

    The section header, the "Beginning Balance" line and the "Total for ..."
    line all live in the same columns as the transactions. The reliable
    discriminator is the transaction date: only a real posted line has one.

    THE SIGN OF THE `Amount` COLUMN IS NOT DEBIT-POSITIVE. This is the trap
    that costs the most and it is not visible in any single row. QuickBooks
    signs a General Ledger amount in the *account's own* natural direction: on
    a debit-normal account (bank, asset, expense) a positive Amount is a debit,
    but on a credit-normal account (credit card, A/P, liability, equity,
    income) a positive Amount is a CREDIT. The running Balance column follows
    the same convention, and so does the Beginning Balance row, so nothing
    inside the report contradicts itself and every "Total for" line still ties.
    The report is internally consistent and externally back to front.

    On a real one-year export the accounts that came out inverted against the
    same period's Journal were exactly the credit-normal ones with activity.
    Debits and credits differed by a five-figure sum in a file whose every
    section total agreed with our parse. Negating the credit-normal accounts
    closes that difference to 0.00, makes the ledger agree with the Journal
    account by account, and makes the beginning balances agree with the
    trial balance.

    Which accounts are credit-normal cannot be known from this report: it never
    states a type. So pass `accounts` (an AccountSet from load_account_list, a
    list of Account, or Ledger.accounts). Without it the amounts are returned
    in the report's own sign, `sign_basis` says "report", and a note explains
    why the set will not balance. `load_all` always passes the chart.
    """
    sheet = _Sheet(path)
    face = _parse_face(sheet)
    hrow, h = _find_header(
        sheet,
        lambda x: ("transaction date" in x or "date" in x)
        and (("amount" in x) or ("debit" in x and "credit" in x)),
    )
    if hrow is None:
        raise ExportError(f"{sheet.name}: no General Ledger header row (need a date and an amount)")
    mode, cols = _amount_columns(h)
    if mode is None:
        raise ExportError(f"{sheet.name}: General Ledger has neither an Amount nor a Debit/Credit pair")

    c_date = _col(h, "transaction date", "date", "posting date")
    c_type = _col(h, "transaction type", "type")
    c_num = _col(h, "num", "number", "doc num", "no.")
    c_name = _col(h, "name", "vendor", "customer", "payee")
    c_memo = _col(h, "memo/description", "memo", "description", "memo / description")
    c_acct = _col(h, "distribution account", "account", "account full name", "distribution account number")
    c_class = _col(h, "class", "class full name", "location")
    c_split = _col(h, "split account", "split", "item split account")

    index = _chart_index(accounts)
    # Only the single signed `Amount` layout carries the natural-sign problem.
    # Where the export prints explicit Debit and Credit columns the sides are
    # already stated and flipping them would introduce the very error the flip
    # exists to remove.
    natural_sign = (mode == "amount")
    sign_basis = "debit_positive" if (index or not natural_sign) else "report"
    lines, discrepancies, beginning, notes = [], [], {}, []
    unknown = []
    if natural_sign and not index:
        notes.append(
            "no chart supplied: amounts are in the report's own natural sign, so "
            "credit-normal accounts are inverted and this set will not balance. "
            "Pass accounts=load_account_list(...) to normalise to debit-positive."
        )
    section = ""                # current account section, from column A
    section_total = ZERO        # running sum in the report's own sign
    section_rows = 0

    def close_section(r, label):
        nonlocal section_total, section_rows
        if not section:
            return
        printed = None
        if mode == "amount":
            printed = sheet.number(r, cols[0])
        else:
            d, c = sheet.number(r, cols[0]), sheet.number(r, cols[1])
            printed = None if (d is None or c is None) else d - c
        if printed is not None and printed != section_total:
            # TRAP 8, non-fatal here: recorded, not raised, so a caller can
            # see every bad section at once instead of one per run.
            discrepancies.append(
                f"{label or section}: parsed {section_rows} rows summing {section_total}, "
                f"report printed {printed} (difference {section_total - printed})"
            )
        section_total, section_rows = ZERO, 0

    for r in range(hrow + 1, len(sheet.rows)):
        if sheet.footer_index is not None and r == sheet.footer_index:
            continue
        if sheet.row_is_blank(r):
            continue
        first = sheet.text(r, 0)
        d = try_date(sheet.text(r, c_date)) if c_date is not None else None

        if d is None:
            if _is_total_label(first):
                close_section(r, first)
                section = ""
                continue
            if first:
                # A new account section begins. Column A carries it; on this
                # layout every transaction row leaves column A empty, which is
                # the inverse of the trial balance and worth stating out loud.
                close_section(r, "")
                section = first
                continue
            label = sheet.text(r, c_acct) if c_acct is not None else ""
            if norm_text(label).startswith("beginning balance"):
                bal = sheet.number(r, sheet.width(r) - 1)
                if bal is not None and section:
                    # Same convention as the Amount column, so the same flip.
                    if natural_sign and _credit_normal(index, section):
                        bal = -bal
                    beginning[_account_key(section)] = bal
            continue

        amt = _read_amount(sheet, r, mode, cols)
        if amt is None:
            discrepancies.append(f"row {r + 1}: unreadable amount")
            continue
        raw_debit, raw_credit = amt
        acct_label = (sheet.text(r, c_acct) if c_acct is not None else "") or section
        if natural_sign and index and _lookup_account(index, acct_label) is None:
            if acct_label not in unknown:
                unknown.append(acct_label)
        if natural_sign and _credit_normal(index, acct_label):
            debit, credit = raw_credit, raw_debit    # the flip, see the docstring
        else:
            debit, credit = raw_debit, raw_credit
        lines.append(JournalLine(
            date=d,
            account=_account_key(acct_label),
            debit=debit,
            credit=credit,
            memo=sheet.text(r, c_memo) if c_memo is not None else "",
            name=sheet.text(r, c_name) if c_name is not None else "",
            doc_num=sheet.text(r, c_num) if c_num is not None else "",
            txn_type=sheet.text(r, c_type) if c_type is not None else "",
            account_full=acct_label,
            klass=sheet.text(r, c_class) if c_class is not None else "",
            source_file=sheet.name,
            source_row=r + 1,
        ))
        if c_split is not None:
            pass  # split account is context, not a posting; deliberately dropped
        # The section check compares like with like: the printed "Total for"
        # line is in the report's sign, so accumulate the unflipped amount.
        section_total += raw_debit - raw_credit
        section_rows += 1

    close_section(len(sheet.rows), "")
    if unknown:
        notes.append(
            f"{len(unknown)} account(s) in this ledger are not in the chart and were "
            f"left in the report's sign: {', '.join(unknown[:6])}"
            + (" ..." if len(unknown) > 6 else "")
        )
    return LineSet(lines, face=face, source_file=sheet.name,
                   discrepancies=discrepancies, beginning_balances=beginning,
                   notes=notes, sign_basis=sign_basis)


def load_journal(path) -> LineSet:
    """The Journal: every transaction, both legs, grouped by transaction.

    Column A holds the transaction id on a group header and "Total for <id>"
    on the group's subtotal; transaction rows leave it blank. Each group's
    printed subtotal is checked against the lines parsed under it.
    """
    sheet = _Sheet(path)
    face = _parse_face(sheet)
    hrow, h = _find_header(
        sheet,
        lambda x: ("transaction date" in x or "date" in x)
        and (("debit" in x and "credit" in x) or "amount" in x),
    )
    if hrow is None:
        raise ExportError(f"{sheet.name}: no Journal header row (need a date and an amount)")
    mode, cols = _amount_columns(h)
    if mode is None:
        raise ExportError(f"{sheet.name}: Journal has neither Debit/Credit nor Amount")

    c_date = _col(h, "transaction date", "date", "posting date")
    c_type = _col(h, "transaction type", "type")
    c_num = _col(h, "num", "number", "doc num", "no.")
    c_name = _col(h, "name", "vendor", "customer", "payee")
    c_memo = _col(h, "memo/description", "memo", "description")
    c_acct_no = _col(h, "distribution account number", "account number", "account #")
    c_acct_full = _col(h, "account full name", "distribution account", "account", "split account")
    c_class = _col(h, "class", "class full name", "location")

    lines, discrepancies = [], []
    txn_id = ""
    group_debit = group_credit = ZERO
    group_rows = 0

    def close_group(r, label):
        nonlocal group_debit, group_credit, group_rows
        if group_rows:
            if mode == "debit_credit":
                pd, pc = sheet.number(r, cols[0]), sheet.number(r, cols[1])
            else:
                v = sheet.number(r, cols[0])
                pd, pc = (None, None) if v is None else _split_signed(v)
            for side, parsed, printed in (("debits", group_debit, pd), ("credits", group_credit, pc)):
                if printed is not None and parsed != printed:
                    discrepancies.append(
                        f"{label or txn_id}: parsed {side} {parsed} vs printed {printed} "
                        f"(difference {parsed - printed})"
                    )
        group_debit = group_credit = ZERO
        group_rows = 0

    for r in range(hrow + 1, len(sheet.rows)):
        if sheet.footer_index is not None and r == sheet.footer_index:
            continue
        if sheet.row_is_blank(r):
            continue
        first = sheet.text(r, 0)
        d = try_date(sheet.text(r, c_date)) if c_date is not None else None

        if d is None:
            if _is_total_label(first):
                close_group(r, first)
                txn_id = ""
            elif first:
                close_group(r, "")
                txn_id = first
            continue

        amt = _read_amount(sheet, r, mode, cols)
        if amt is None:
            discrepancies.append(f"row {r + 1}: unreadable amount")
            continue
        debit, credit = amt
        full = sheet.text(r, c_acct_full) if c_acct_full is not None else ""
        number = sheet.text(r, c_acct_no) if c_acct_no is not None else ""
        key = number.strip() if re.fullmatch(r"\d{3,10}", number.strip() or "") else _account_key(full)
        lines.append(JournalLine(
            date=d,
            account=key,
            debit=debit,
            credit=credit,
            memo=sheet.text(r, c_memo) if c_memo is not None else "",
            name=sheet.text(r, c_name) if c_name is not None else "",
            doc_num=sheet.text(r, c_num) if c_num is not None else "",
            txn_type=sheet.text(r, c_type) if c_type is not None else "",
            txn_id=txn_id,
            account_full=full,
            klass=sheet.text(r, c_class) if c_class is not None else "",
            source_file=sheet.name,
            source_row=r + 1,
        ))
        group_debit += debit
        group_credit += credit
        group_rows += 1

    close_group(len(sheet.rows), "")
    return LineSet(lines, face=face, source_file=sheet.name, discrepancies=discrepancies)


# ----------------------------------------------------- profit and loss/month

_MONTH_HEADER_RE = re.compile(r"^([A-Za-z]{3,9})\.?\s+(\d{4})$")


def _month_header(text: str):
    """"January 2025" -> "2025-01". Anything else -> None."""
    m = _MONTH_HEADER_RE.match((text or "").strip())
    if not m:
        return None
    d = try_date(f"{m.group(1)} 1, {m.group(2)}")
    return month_key(d) if d else None


@dataclass
class MonthlyPL:
    months: list = field(default_factory=list)          # ["2025-01", ...]
    rows: dict = field(default_factory=dict)            # {account: {month: Decimal}}
    totals: dict = field(default_factory=dict)          # {month: Decimal} net income
    row_totals: dict = field(default_factory=dict)      # {account: printed annual total}
    subtotals: dict = field(default_factory=dict)       # {"Total for Income": {month: Decimal}}
    foots: bool = True
    face: ReportFace = field(default_factory=ReportFace)
    source_file: str = ""
    discrepancies: list = field(default_factory=list)

    def month_total(self, m) -> Decimal:
        return sum((v.get(m, ZERO) for v in self.rows.values()), ZERO)


# These are the report's own computed lines, not accounts. Keeping them out of
# `rows` is what stops a caller adding "Total for Income" to Income.
_PL_COMPUTED_PREFIXES = ("total for", "total ", "net ", "gross ")


def load_profit_and_loss_by_month(path) -> MonthlyPL:
    """The month-column P&L.

    Values are kept in the report's own presentation sign (income positive,
    expense positive), NOT converted to debit-positive: this report is a
    presentation, its rows are section rollups as often as accounts, and
    flipping signs per row would need a chart the report does not carry. Use
    the general ledger when you need postings.
    """
    sheet = _Sheet(path)
    face = _parse_face(sheet)
    hrow, month_cols = None, {}
    for r in range(min(14, len(sheet.rows))):
        found = {}
        for c in range(sheet.width(r)):
            mk = _month_header(sheet.text(r, c))
            if mk:
                found[mk] = c
        if len(found) >= 2:
            hrow, month_cols = r, found
            break
    if hrow is None:
        raise ExportError(f"{sheet.name}: no month-column header row found")

    h = _headers(sheet, hrow)
    c_total = _col(h, "total")
    c_label = _col(h, "distribution account", "account", "") or 0

    months = sorted(month_cols)
    rows, row_totals, subtotals, discrepancies = {}, {}, {}, []
    totals = {}

    for r in range(hrow + 1, len(sheet.rows)):
        if sheet.footer_index is not None and r == sheet.footer_index:
            continue
        if sheet.row_is_blank(r):
            continue
        label = sheet.text(r, c_label)
        if not label:
            continue
        values, filled = {}, False
        for mk in months:
            c = month_cols[mk]
            if sheet.text(r, c) == "":
                continue
            v = sheet.number(r, c)
            if v is None:
                discrepancies.append(f"row {r + 1} ({label}) {mk}: unreadable value")
                continue
            values[mk] = v
            filled = True
        printed_total = sheet.number(r, c_total) if c_total is not None else None
        if not filled and printed_total in (None, ZERO):
            continue  # a section header such as "Income" carries no figures

        n = norm_text(label)
        if n in ("net income", "net profit"):
            totals = dict(values)
        if n.startswith(_PL_COMPUTED_PREFIXES):
            subtotals[label] = values
            continue

        rows[label] = values
        if printed_total is not None:
            row_totals[label] = printed_total
            # TRAP 8. The annual Total column is the row's own claim about its
            # twelve months. It is a formula ("=B8+C8+...") so it only exists
            # once the formula is resolved, which is the whole reason the
            # resolver is here.
            computed = sum(values.values(), ZERO)
            if computed != printed_total:
                discrepancies.append(
                    f"{label}: months sum to {computed}, printed total {printed_total} "
                    f"(difference {computed - printed_total})"
                )

    if not totals:
        totals = {m: sum((v.get(m, ZERO) for v in rows.values()), ZERO) for m in months}

    return MonthlyPL(
        months=months, rows=rows, totals=totals, row_totals=row_totals,
        subtotals=subtotals, foots=not discrepancies, face=face,
        source_file=sheet.name, discrepancies=discrepancies,
    )


# ----------------------------------------------------------------- discovery
#
# TRAP 9. Filenames arrive as the browser saved them: spaces, "+" where the
# download turned a space into one, a "(1)" suffix where a second export of the
# same report landed in the same folder, and the company name glued to the
# report name by an underscore. Matching has to be on tokens, case-insensitively,
# with the more specific token tried first: "profit and loss" is a prefix of
# "profit and loss detail", so testing it first would capture both.
_KINDS = (
    ("profit_and_loss_by_month", ("profit and loss by month", "profit loss by month", "p and l by month")),
    ("profit_and_loss_detail", ("profit and loss detail", "profit loss detail")),
    ("profit_and_loss", ("profit and loss", "profit loss", "income statement")),
    ("balance_sheet_detail", ("balance sheet detail",)),
    ("balance_sheet", ("balance sheet",)),
    ("trial_balance", ("trial balance",)),
    ("general_ledger", ("general ledger",)),
    ("account_list", ("account list", "chart of accounts")),
    ("journal", ("journal",)),
)

# Which kinds carry postings, in the order load_all prefers them as a source of
# journal lines. A General Ledger over every account already contains both legs
# of every transaction, so a Journal for the same period would double it.
_LINE_SOURCES = ("general_ledger", "journal")


def _normalise_filename(name: str) -> str:
    stem = os.path.splitext(os.path.basename(name))[0]
    stem = stem.replace("+", " ").replace("_", " ").replace("-", " ")
    stem = re.sub(r"\(\d+\)", " ", stem)          # the "(1)" of a second download
    return norm_text(stem)


def discover(dirpath) -> dict:
    """{kind: [paths]} for the exports in a folder. Public: callers audit it."""
    if not os.path.isdir(dirpath):
        raise ExportError(f"not a directory: {dirpath}")
    found = {}
    for entry in sorted(os.listdir(dirpath)):
        if not entry.lower().endswith((".xlsx", ".xlsm")):
            continue
        if entry.startswith("~$") or entry.startswith("."):
            continue          # Excel lock files and dotfiles are not exports
        n = _normalise_filename(entry)
        for kind, tokens in _KINDS:
            if any(tok in n for tok in tokens):
                found.setdefault(kind, []).append(os.path.join(dirpath, entry))
                break
    return found


def load_all(dirpath) -> Ledger:
    """Load a folder of exports into one `Ledger`.

    Accounts come from the Account List. Journal lines come from the General
    Ledger exports, one per period; a Journal is used only for a period no
    General Ledger covers, because loading both would post every transaction
    twice. Everything discovered, used or not, is recorded in `ledger.sources`
    with its face, so a caller can see what was in the folder and what was
    actually counted.

    WHERE THE OPENING BALANCES COME FROM, AND WHY NOT THE OTHER TWO SOURCES

    Three exports state something balance-shaped, and only one of them is
    stated as of the ledger's own start date.

    1. The General Ledger's own "Beginning Balance" rows. THIS IS THE SOURCE.
       They are printed per account section, as of the first day of that
       report's period, in the same natural sign as the Amount column, so the
       loader's credit-normal flip already applies to them. On a real export
       the earliest ledger's beginning balances plus every posted line
       reproduced the year-end trial balance to the cent on every
       balance-sheet account. They also foot to 0.00 on their own, which is
       how a truncated set announces itself.
    2. The Account List's `Total Balance` column. REJECTED. It is a
       point-in-time figure from whenever the export ran, not as of the period
       end: in `tests/test_balances.py` it reads -24,910.83 for the operating
       account against a 12/31/2025 balance of -18,204.67, because eight months
       of 2026 sit in between, and that is drawn from a real export. It is right often enough to look reliable and is wrong exactly
       when the books are still moving.
    3. The trial balance. Correct, but it exists only for the dates somebody
       happened to export, states no opening for a company's first year, and is
       frequently not in the folder at all. It is used here as the CHECK on the
       loaded ledger rather than as the source of it, which is exit test 1.

    When no accepted line source starts on the ledger's own start date, no
    opening basis is set: `ledger.opening_basis` stays empty and `balance_of`
    answers None for every account, so a caller reports "cannot be determined"
    instead of printing movement as though it were a balance.
    """
    files = discover(dirpath)
    if not files:
        raise ExportError(f"{dirpath}: no QuickBooks xlsx exports found")

    ledger = Ledger()
    faces = {}

    for kind, paths in files.items():
        for p in paths:
            try:
                faces[p] = report_face(p)
            except ExportError as exc:
                ledger.sources.append({"kind": kind, "file": os.path.basename(p),
                                       "used": False, "note": str(exc)})

    for p in files.get("account_list", []):
        accounts = load_account_list(p)
        for a in accounts:
            ledger.accounts[a.key] = a
        ledger.sources.append({
            "kind": "account_list", "file": accounts.source_file, "used": True,
            "accounts": len(accounts), "foots": accounts.foots,
            "discrepancies": list(accounts.discrepancies),
        })

    # Line sources, most-preferred kind first, earliest period first inside a
    # kind so the accepted set is deterministic regardless of directory order.
    accepted = []
    accepted_sets = []          # (face, LineSet), in acceptance order
    for kind in _LINE_SOURCES:
        ordered = sorted(
            files.get(kind, []),
            key=lambda p: (faces.get(p, ReportFace()).period_start or faces.get(p, ReportFace()).as_of
                           or __import__("datetime").date.min, p),
        )
        for p in ordered:
            face = faces.get(p) or ReportFace()
            overlap = next((f for f in accepted if f.covers(face)), None)
            if overlap is not None:
                ledger.sources.append({
                    "kind": kind, "file": os.path.basename(p), "used": False,
                    "period": face.period_text,
                    "note": f"period overlaps {overlap.source_file}; not counted twice",
                })
                continue
            # The chart is what makes a General Ledger's signs readable.
            lines = (load_general_ledger(p, accounts=ledger.accounts)
                     if kind == "general_ledger" else load_journal(p))
            ledger.lines.extend(lines)
            accepted.append(face)
            accepted_sets.append((face, lines))
            ledger.sources.append({
                "kind": kind, "file": lines.source_file, "used": True,
                "period": face.period_text, "lines": len(lines),
                "debits": str(lines.total_debits), "credits": str(lines.total_credits),
                "balanced": lines.balanced, "foots": lines.foots,
                "sign_basis": lines.sign_basis,
                "discrepancies": list(lines.discrepancies),
                "notes": list(lines.notes),
            })

    # The trial balance is OPENED, not glanced at. Exit test 1 ties the loaded
    # ledger to it, and a test that never opens the artifact it claims to check
    # is a test that cannot fail. `load_trial_balance` refuses a set that does
    # not foot or that disagrees with the report's own printed totals, so a bad
    # file is recorded as unusable here rather than quietly averaged in.
    for p in sorted(files.get("trial_balance", [])):
        try:
            tb = load_trial_balance(p)
        except ExportError as exc:
            ledger.sources.append({
                "kind": "trial_balance", "file": os.path.basename(p), "used": False,
                "period": (faces.get(p) or ReportFace()).period_text, "note": str(exc),
            })
            continue
        ledger.trial_balances.append(tb)
        ledger.sources.append({
            "kind": "trial_balance", "file": tb.source_file, "used": True,
            "period": tb.face.period_text, "as_of": iso(tb.as_of),
            "rows": len(tb.rows), "debits": str(tb.total_debits),
            "credits": str(tb.total_credits), "foots": tb.foots,
            "discrepancies": list(tb.discrepancies),
        })
    ledger.trial_balances.sort(key=lambda t: (t.as_of or _DATE_MIN, t.source_file))

    for kind in ("trial_balance", "balance_sheet", "balance_sheet_detail",
                 "profit_and_loss", "profit_and_loss_detail", "profit_and_loss_by_month",
                 "journal", "general_ledger"):
        for p in files.get(kind, []):
            if any(s.get("file") == os.path.basename(p) for s in ledger.sources):
                continue
            face = faces.get(p) or ReportFace()
            ledger.sources.append({
                "kind": kind, "file": os.path.basename(p), "used": False,
                "period": face.period_text, "note": "read for its face only",
            })

    company = ""
    basis = ""
    starts, ends = [], []
    for f in faces.values():
        company = company or f.company
        basis = basis or f.basis
        if f.period_start:
            starts.append(f.period_start)
        for d in (f.period_end, f.as_of):
            if d:
                ends.append(d)
    ledger.company = company
    ledger.basis = basis
    ledger.period_start = min(starts) if starts else None
    ledger.period_end = max(ends) if ends else None
    _apply_opening_balances(ledger, accepted_sets)
    return ledger


_DATE_MIN = __import__("datetime").date.min


def _apply_opening_balances(ledger, accepted_sets) -> None:
    """Set `ledger.opening_balances` from the earliest accepted line source.

    Only a source that STARTS on the ledger's own start date can state the
    opening position: a 2025 ledger's beginning balances are the balances at
    2025-01-01, and adding them to lines that also include 2024 would count the
    2024 movement twice. So the source is the accepted set whose period starts
    where the ledger does, and nothing else qualifies.

    Everything about the outcome is recorded on `ledger.opening_notes` and in
    `ledger.sources`, including the failure to find one, because "no opening
    basis" is the finding that turns every reported balance into "cannot be
    determined" and it has to be visible.
    """
    earliest = None
    for face, lines in accepted_sets:
        start = getattr(face, "period_start", None) or getattr(face, "as_of", None)
        if start is None or ledger.period_start is None or start != ledger.period_start:
            continue
        earliest = (face, lines)
        break

    if earliest is None:
        ledger.opening_basis = ""
        ledger.opening_notes.append(
            "no export starts on " + (iso(ledger.period_start) or "the period start")
            + ", so no account's opening position is known and every balance reads "
              "\"cannot be determined\". Export a General Ledger covering the first "
              "day of the period; its Beginning Balance rows are the opening position."
        )
        return

    face, lines = earliest
    beginning = dict(getattr(lines, "beginning_balances", {}) or {})
    ledger.opening_balances = beginning
    ledger.opening_basis = (
        f"the Beginning Balance rows of {lines.source_file}, as of {iso(ledger.period_start)}"
    )
    total = sum(beginning.values(), ZERO)
    ledger.sources.append({
        "kind": "opening_balances", "file": lines.source_file, "used": True,
        "as_of": iso(ledger.period_start), "accounts": len(beginning),
        "sum": str(total), "foots": total == ZERO,
        "note": ledger.opening_basis,
    })
    if total != ZERO:
        # Openings are a trial balance at the period start, so they foot. A
        # non-zero sum means a section was dropped or a sign was not flipped,
        # and every balance built on them inherits it.
        ledger.opening_notes.append(
            f"the opening balances from {lines.source_file} sum to {total} and not 0.00. "
            f"A set of opening balances is a trial balance as of the first day and foots; "
            f"this one does not, so treat every balance derived from it as suspect."
        )


__all__ = [
    "ExportError", "ReportFace", "TrialBalance", "MonthlyPL", "AccountSet", "LineSet",
    "report_face", "load_account_list", "load_trial_balance", "load_general_ledger",
    "load_journal", "load_profit_and_loss_by_month", "discover", "load_all",
]
