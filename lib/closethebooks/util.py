"""Money, dates, text and CSV primitives. Standard library only.

Every amount in this engine is a Decimal quantized to cents. Floats are never
used for money: 0.1 + 0.2 != 0.3 and a trial balance has to foot to exactly
0.00, so a binary float would make the central exit test unreliable.
"""

from __future__ import annotations

import csv
import datetime as _dt
import io
import re
import unicodedata
from decimal import Decimal, ROUND_HALF_UP, InvalidOperation

CENTS = Decimal("0.01")
ZERO = Decimal("0.00")

# QuickBooks writes negatives four ways depending on the report and the export
# path: a leading minus, parentheses, an ASCII hyphen, or an EN DASH (U+2013).
# The en dash is what Mercury's IO card statements use and it is invisible in a
# diff, so it is handled here rather than in each parser.
_MINUS_CHARS = "−–—"  # minus sign, en dash, em dash
_CURRENCY_CHARS = "$£€¥"


class MoneyError(ValueError):
    """A string that was supposed to be an amount could not be read as one."""


def money(value, field: str = "amount") -> Decimal:
    """Parse anything QuickBooks or a bank might emit into a Decimal of cents.

    Handles: None and blank as 0.00, parentheses as negative, currency symbols,
    thousands separators, unicode minus and en/em dashes, and the literal
    formula strings ("=18204.67") that QuickBooks writes into xlsx cells.
    """
    if value is None:
        return ZERO
    if isinstance(value, Decimal):
        return value.quantize(CENTS, rounding=ROUND_HALF_UP)
    if isinstance(value, int):
        return Decimal(value).quantize(CENTS, rounding=ROUND_HALF_UP)
    if isinstance(value, float):
        # Only reached for values openpyxl already coerced. Round-trip through
        # str so we get the shortest representation rather than binary noise.
        return Decimal(repr(value)).quantize(CENTS, rounding=ROUND_HALF_UP)

    s = str(value).strip()
    if not s:
        return ZERO

    # QuickBooks xlsx exports store numbers as literal formulas. Reading such a
    # sheet with data_only=True returns 0.0 for every one of them, because no
    # cached value was ever written by a spreadsheet application. We read with
    # data_only=False and strip the "=" here.
    if s.startswith("="):
        s = s[1:].strip()

    negative = False
    if s.startswith("(") and s.endswith(")"):
        negative = True
        s = s[1:-1].strip()

    for ch in _MINUS_CHARS:
        s = s.replace(ch, "-")
    for ch in _CURRENCY_CHARS:
        s = s.replace(ch, "")
    s = s.replace(",", "").replace(" ", "").replace(" ", "")

    if s in ("", "-", "."):
        return ZERO
    if s.endswith("-"):  # trailing-minus convention
        negative = True
        s = s[:-1]
    if s.startswith("-"):
        negative = not negative
        s = s[1:]

    try:
        d = Decimal(s)
    except InvalidOperation as exc:
        raise MoneyError(f"{field}: cannot read {value!r} as an amount") from exc

    d = d.quantize(CENTS, rounding=ROUND_HALF_UP)
    return -d if negative else d


def fmt(amount: Decimal) -> str:
    """Render for a report: thousands separated, negatives in parentheses."""
    a = money(amount)
    body = f"{abs(a):,.2f}"
    return f"({body})" if a < ZERO else body


def plain(amount: Decimal) -> str:
    """Render for a CSV a machine will re-read: -1234.56, no separators."""
    return f"{money(amount):.2f}"


# QuickBooks reports and exports are MM/DD/YYYY. A slice like date[:4] silently
# matches nothing against them, which is how a year filter can appear to work
# and return zero rows. Every date entering the engine goes through here.
_DATE_FORMATS = (
    "%m/%d/%Y", "%m/%d/%y", "%Y-%m-%d", "%m-%d-%Y", "%d/%m/%Y",
    "%b %d, %Y", "%B %d, %Y", "%d %b %Y", "%d %B %Y", "%m/%d/%Y %H:%M",
    "%Y/%m/%d", "%b %d %Y", "%Y-%m-%d %H:%M:%S",
)


class DateError(ValueError):
    """A string that was supposed to be a date could not be read as one."""


def parse_date(value, field: str = "date", dayfirst: bool = False):
    """Return a datetime.date, or raise. Never guesses silently."""
    if value is None:
        raise DateError(f"{field}: missing")
    if isinstance(value, _dt.datetime):
        return value.date()
    if isinstance(value, _dt.date):
        return value
    s = str(value).strip()
    if not s:
        raise DateError(f"{field}: blank")
    formats = _DATE_FORMATS
    if dayfirst:
        formats = ("%d/%m/%Y", "%d/%m/%y") + formats
    for f in formats:
        try:
            return _dt.datetime.strptime(s, f).date()
        except ValueError:
            continue
    raise DateError(f"{field}: cannot read {value!r} as a date")


def try_date(value, dayfirst: bool = False):
    """parse_date, or None. For columns that are legitimately sometimes blank."""
    try:
        return parse_date(value, dayfirst=dayfirst)
    except (DateError, TypeError):
        return None


def iso(d) -> str:
    return d.isoformat() if d else ""


def month_key(d) -> str:
    return f"{d.year:04d}-{d.month:02d}"


def month_range(start, end):
    """Every YYYY-MM from start to end inclusive."""
    out, y, m = [], start.year, start.month
    while (y, m) <= (end.year, end.month):
        out.append(f"{y:04d}-{m:02d}")
        m += 1
        if m == 13:
            y, m = y + 1, 1
    return out


def norm_text(s) -> str:
    """Collapse a descriptor for matching: casefold, strip accents and runs."""
    if s is None:
        return ""
    s = unicodedata.normalize("NFKD", str(s))
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = re.sub(r"\s+", " ", s).strip().casefold()
    return s


# A cell beginning =, +, - or @ is executed as a formula by Excel, Google
# Sheets and QuickBooks' own import preview. Bank descriptors are attacker
# influenced (anyone who can send money can choose the memo), so every string
# this engine writes into a CSV or workbook goes through here first.
_FORMULA_LEAD = ("=", "+", "-", "@", "\t", "\r")


def csv_safe(value) -> str:
    s = "" if value is None else str(value)
    if s and s[0] in _FORMULA_LEAD:
        return "'" + s
    return s


def write_csv(path, header, rows, safe_columns=None):
    """Write a CSV with formula escaping. Returns the row count written."""
    safe = set(header) if safe_columns is None else set(safe_columns)
    n = 0
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh, quoting=csv.QUOTE_MINIMAL, lineterminator="\r\n")
        w.writerow(header)
        for row in rows:
            out = []
            for col, val in zip(header, row):
                out.append(csv_safe(val) if col in safe else ("" if val is None else str(val)))
            w.writerow(out)
            n += 1
    return n


def csv_text(header, rows) -> str:
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(header)
    for r in rows:
        w.writerow([csv_safe(v) for v in r])
    return buf.getvalue()


def slug(s, maxlen: int = 60) -> str:
    s = re.sub(r"[^a-zA-Z0-9]+", "-", str(s or "")).strip("-").lower()
    return s[:maxlen] or "untitled"


def sha256_of(text: str) -> str:
    import hashlib
    return hashlib.sha256(text.encode("utf-8")).hexdigest()
