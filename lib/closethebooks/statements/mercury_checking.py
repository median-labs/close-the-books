"""Mercury checking (and savings) statements: PDF, and CSV when you have one.

Ported from a script that parsed twelve months of real Mercury statements and
tied every one of them out. The facts worth not rediscovering are all here.

**Some Mercury PDFs carry broken glyph widths.** In those files `pdftotext
-layout` returns scrambled fragments, or ZERO transactions, while the file
itself is perfectly readable on screen. A January statement extracted its first
row as `Jan01  Mer cur yCr edi t` spread over five lines, and an IO card
January extracted no rows at all. The fix is to ALSO run `pdftotext -raw` and
strip every space: in a broken file each glyph is emitted separately, so
concatenation restores the exact character sequence. The cost is word-joined
descriptions ("AmazonWebServices"); the dates and the amounts come back exact.
So both modes are always run and whichever one TIES OUT is the one kept. Never
decide the mode from the file name or the month.

**A summary field can be corrupt while the transaction list is fine.** A real
April statement rendered its "Total withdrawals" as `-$8,1 8.4`. That figure is
unreadable, the transaction list beside it was complete, and the chain identity
(beginning + deposits - withdrawals == ending) proved it. When a summary field
cannot be read it is reported as unavailable and the chain decides. It is never
patched, and the difference is never plugged.

**Negatives use an EN DASH (U+2013), not a hyphen.** `util.money` handles it;
that is why every amount in this module goes through `money()` and no local
float parsing exists.

**The date column is blank on continuation rows.** When several transactions
share a day, only the first row carries the date, so the last date seen is
carried forward.
"""

from __future__ import annotations

import calendar
import datetime as _dt
import os
import re

from ..model import BankLine
from ..util import ZERO, money
from . import generic_csv
from .base import ParseError, StatementFile, pdf_text

__all__ = ["parse", "detect", "PARSER_NAME"]

PARSER_NAME = "mercury_checking"

MON3 = {m: i + 1 for i, m in enumerate(
    ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"])}
FULL = {m: i + 1 for i, m in enumerate(
    ["January", "February", "March", "April", "May", "June", "July",
     "August", "September", "October", "November", "December"])}

# The en dash is Mercury's minus sign. The em dash and the true minus sign are
# included because the same PDF pipeline has produced both.
NEG = "".join(map(chr, (0x2013, 0x2014, 0x2212))) + "-"
MONEY_RE = re.compile("[" + NEG + r"]?\$[\d,]+\.\d{2}")
BULLET = chr(0x2022)

# Transaction "Type" values, used in raw mode to split the run-together
# description from the type that follows it.
TYPES = [
    BULLET * 2 + r"\d{4}", r"ACHIn", r"ACHPayment", r"ACHPull", r"ACHOut",
    r"Intl\.?WirePayment", r"WirePayment", r"Wire", r"CreditAccountPayment",
    r"CreditCashbackDeposit", r"CreditCashback", r"Deposit", r"Refund",
    r"Transfer", r"Check\d*",
]
TYPE_SUFFIX = re.compile("(" + "|".join(TYPES) + ")$")


def _amount(token):
    return money(token, "statement amount")


def summary_of(lines) -> dict:
    """Read the Account activity block. A field that cannot be read is None.

    None means "unreadable or absent", never zero. The tie-out treats it as an
    unavailable check rather than as a figure of 0.00, which is the whole
    difference between reporting a corrupt statement and inventing one.
    """
    def grab(label, collapse=False):
        key = label.replace(" ", "").lower() if collapse else label.lower()
        for ln in lines:
            hay = ln.replace(" ", "").lower() if collapse else ln.lower()
            if key in hay:
                tokens = MONEY_RE.findall(ln.replace(" ", "") if collapse else ln)
                if tokens:
                    return _amount(tokens[-1])
                return None
        return None

    collapse = not any("Beginning Balance" in ln for ln in lines)
    return {
        "beginning": grab("Beginning Balance", collapse),
        "withdrawals": grab("Total withdrawals", collapse),
        "deposits": grab("Total deposits", collapse),
        "ending": grab("Statement balance", collapse),
    }


def month_year(lines):
    """The statement month, from the "August 2025 statement" line on page 1."""
    for ln in lines[:25]:
        s = ln.replace(" ", "")
        m = re.search("(" + "|".join(FULL) + r")(\d{4})statement", s)
        if m:
            return FULL[m.group(1)], int(m.group(2))
    return None, None


def _line(year, month, day, descriptor, amount, balance, source_file, row):
    return BankLine(
        date=_dt.date(year, month, day),
        descriptor=descriptor,
        amount=amount,
        balance=balance,
        source_file=str(source_file),
        source_row=row,
        origin="statement",
    )


def parse_layout(text, source_file="") -> list:
    """Column-position parse of `pdftotext -layout` output."""
    lines = text.split("\n")
    month, year = month_year(lines)
    if not month:
        return []
    header_idx = next((i for i, ln in enumerate(lines)
                       if "Description" in ln and "Amount" in ln and "Type" in ln), None)
    if header_idx is None:
        return []
    header = lines[header_idx]
    bal_col = header.find("End of Day Balance")
    desc_col = header.find("Description")
    type_col = header.find("Type")
    if min(bal_col, desc_col, type_col) < 0:
        return []

    out, day, mon = [], None, month
    for offset, ln in enumerate(lines[header_idx + 1:]):
        s = ln.strip()
        if not s or s.startswith(("Total", "Banking services", "Mercury |")):
            continue
        if "Description" in ln and "Amount" in ln:
            continue
        m = re.match(r"^([A-Z][a-z]{2}) (\d{1,2})\b", ln)
        if m:
            day, mon = int(m.group(2)), MON3[m.group(1)]
        elif day is None:
            # A continuation row before any date has been seen is not a
            # transaction row; the date carries forward only once one exists.
            continue
        tokens = [(t.group(0), t.start()) for t in MONEY_RE.finditer(ln)]
        amounts = [t for t in tokens if t[1] < bal_col - 6]
        if not amounts:
            continue
        descriptor = ln[desc_col:type_col].strip()
        if not descriptor:
            continue
        kind = ln[type_col:amounts[0][1]].strip()
        balances = [t for t in tokens if t[1] >= bal_col - 6]
        out.append(_line(
            year, mon, day,
            (descriptor + " " + kind).strip(),
            _amount(amounts[0][0]),
            _amount(balances[0][0]) if balances else None,
            source_file, header_idx + offset + 2,
        ))
    return out


def parse_raw(text, source_file="") -> list:
    """Glyph-order parse of `pdftotext -raw`, with every space removed.

    This is the mode that recovers a statement whose embedded font declares
    broken widths. Descriptions come back word-joined; dates and amounts are
    exact.
    """
    lines = [ln.replace(" ", "").strip() for ln in text.split("\n")]
    month, year = month_year(lines)
    if not month:
        return []
    out, day, mon, buf, started = [], None, month, "", False
    for offset, s in enumerate(lines):
        if not s:
            continue
        if s.startswith("Date(UTC)Description"):
            started = True
            continue
        if not started:
            continue
        if s.startswith(("Total$", "Bankingservices", "Mercury|")):
            continue
        m = re.match(r"^([A-Z][a-z]{2})(\d{1,2})(?![\d])", s)
        if m and m.group(1) in MON3:
            day, mon = int(m.group(2)), MON3[m.group(1)]
            s = s[m.end():]
            buf = ""
        if day is None:
            continue
        tokens = [(t.group(0), t.start()) for t in MONEY_RE.finditer(s)]
        if not tokens:
            buf += s               # descriptor fragment, keep accumulating
            continue
        head = buf + s[:tokens[0][1]]
        buf = ""
        if not head:
            continue
        tm = TYPE_SUFFIX.search(head)
        descriptor, kind = (head[:tm.start()], tm.group(0)) if tm else (head, "")
        out.append(_line(
            year, mon, day,
            (descriptor + " " + kind).strip(),
            _amount(tokens[0][0]),
            _amount(tokens[1][0]) if len(tokens) > 1 else None,
            source_file, offset + 1,
        ))
    return out


def _period(month, year):
    if not month:
        return None, None
    last = calendar.monthrange(year, month)[1]
    return _dt.date(year, month, 1), _dt.date(year, month, last)


def _candidate(mode, lines, summary, month, year, path, account_key, extra_notes):
    start, end = _period(month, year)
    notes = [f"extraction mode: pdftotext {mode}"] + list(extra_notes)
    for field in ("deposits", "withdrawals", "beginning", "ending"):
        if summary.get(field) is None:
            notes.append(
                f"the statement's own {field} field could not be read; "
                f"reported as unavailable, the chain identity decides"
            )
    statement = StatementFile(
        account_key=account_key,
        period_start=start,
        period_end=end,
        opening_balance=summary.get("beginning") or ZERO,
        closing_balance=summary.get("ending"),
        lines=lines,
        source_file=str(path),
        parser=PARSER_NAME,
        stated_deposits=summary.get("deposits"),
        stated_withdrawals=summary.get("withdrawals"),
        notes=notes,
    )
    statement.check()
    return statement


def parse(path, account_key="", **kwargs) -> StatementFile:
    """Parse one Mercury checking statement. PDF, or CSV if that is what you have."""
    if str(path).lower().endswith((".csv", ".tsv")):
        # Mercury exports CSV as well, with a signed Amount column. The generic
        # layer already handles it; there is nothing Mercury-specific to add,
        # so do not duplicate a parser here.
        return generic_csv.parse(path, account_key, parser=PARSER_NAME, **kwargs)

    layout_text = pdf_text(path, "-layout")
    raw_text = pdf_text(path, "-raw")

    summary = summary_of(layout_text.split("\n"))
    extra_notes = []
    if summary["beginning"] is None or summary["ending"] is None:
        collapsed = [l.replace(" ", "") for l in raw_text.split("\n")]
        recovered = summary_of(collapsed)
        for key, value in recovered.items():
            if summary[key] is None and value is not None:
                summary[key] = value
                extra_notes.append(f"{key} balance recovered from raw-mode text")

    month, year = month_year(layout_text.split("\n"))
    if not month:
        month, year = month_year([l.replace(" ", "") for l in raw_text.split("\n")])
    if not month:
        raise ParseError(
            f"{os.path.basename(str(path))}: no \"<Month> <Year> statement\" line found. "
            f"This does not look like a Mercury statement."
        )

    candidates = []
    for mode, lines in (("-layout", parse_layout(layout_text, path)),
                        ("-raw", parse_raw(raw_text, path))):
        candidates.append(_candidate(mode, lines, summary, month, year, path,
                                     account_key, extra_notes))

    # Whichever mode TIES is the right one. Falling back to "the one with more
    # rows" is only for the case where neither ties, and it comes with a note;
    # the difference is still reported as a number.
    ties = [c for c in candidates if c.ties]
    if ties:
        return ties[0]

    best = max(candidates, key=lambda c: len(c.lines))
    if not best.lines:
        empty = (summary["deposits"] == ZERO and summary["withdrawals"] == ZERO
                 and summary["beginning"] is not None
                 and summary["beginning"] == summary["ending"])
        if empty:
            best.notes.append("statement has no transactions and its summary agrees")
            return best
        raise ParseError(
            f"{os.path.basename(str(path))}: both -layout and -raw returned ZERO "
            f"transactions, but the summary block reports deposits "
            f"{summary['deposits']} and withdrawals {summary['withdrawals']}. "
            f"Refusing to report an empty month; the extraction is broken."
        )
    best.notes.append(
        "neither -layout nor -raw ties; reporting the parse with more rows "
        f"({len(best.lines)}) and the difference as a number"
    )
    return best


def detect(path) -> bool:
    """Face-text sniff: a Mercury statement for a deposit account."""
    if not str(path).lower().endswith(".pdf"):
        return False
    try:
        head = pdf_text(path, "-layout")[:4000]
    except ParseError:
        return False
    flat = head.replace(" ", "")
    return ("Mercury" in head
            and "BeginningBalance" in flat
            and "AccountType" in flat
            and "CreditCard" not in flat)
