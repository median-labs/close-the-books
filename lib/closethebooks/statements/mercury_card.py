"""Mercury IO credit card statements: PDF, and CSV when you have one.

Ported from a script that parsed twelve months of real IO card statements and
tied every one of them out against the statement's own figures. Everything
below was learned the expensive way.

**Negatives are an EN DASH (U+2013), not a hyphen.** A charge renders as
`-$142.25` where that leading character is U+2013. `float(s.replace("-", ""))`
silently keeps the sign and doubles your month. Every amount here goes through
`util.money`, which handles the en dash, the em dash, the true minus sign,
parentheses and the currency symbol in one place.

**The date column is blank on continuation rows.** When several transactions
share a day, only the first carries `Feb 28`; the rest start with the
description. The last date seen carries forward, so a continuation row is never
dropped and never dated to the wrong day.

**A January statement returned ZERO rows under `pdftotext -layout`** because
that PDF declares broken glyph widths. `pdftotext -raw` with the spaces
stripped recovers it exactly. Both modes are always run and whichever TIES is
kept, so an empty result is never believed on its own.

**Sign convention.** `BankLine.amount` is money in positive, money out
negative, from the account holder's point of view, on a card exactly as on a
bank account: a purchase is negative, a refund is positive, a payment of the
card balance is positive (it is money arriving at the card). The card's own
running "End of Day Balance" column already follows this, rendering an amount
OWED as a negative number, so a balance parsed off the statement needs no
adjustment.

**"Spending" is not the withdrawals total.** Mercury's Overview prints
"Spending" NET of refunds and EXCLUDING the payment that clears the balance. On
one real February statement, gross charges were $1,203.43, a refund was $125.99
and Spending read $1,077.44. So Spending is never fed to the generic
withdrawals check; it is checked separately, against the same net figure the
statement means by it.

**The last row belongs to the next cycle.** A June statement carries the July 1
autopay. That row is KEPT, because the statement's own balance chain includes
it and dropping it would break the tie-out, but it is flagged in the notes: on
the bank side the same payment appears again, and booking both is a double
count.
"""

from __future__ import annotations

import calendar
import datetime as _dt
import os
import re

from ..model import BankLine
from ..util import ZERO, fmt, money, norm_text
from . import generic_csv
from .base import ParseError, StatementFile, pdf_text

__all__ = ["parse", "detect", "PARSER_NAME"]

PARSER_NAME = "mercury_card"

MON3 = {m: i + 1 for i, m in enumerate(
    ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"])}
FULL = {m: i + 1 for i, m in enumerate(
    ["January", "February", "March", "April", "May", "June", "July",
     "August", "September", "October", "November", "December"])}

NEG = "".join(map(chr, (0x2013, 0x2014, 0x2212))) + "-"
MONEY_RE = re.compile("[" + NEG + r"]?\$[\d,]+\.\d{2}")
BULLET = chr(0x2022)

# Layout mode: the columns are separated by runs of two or more spaces, and the
# date group is absent on a continuation row.
ROW_LAYOUT = re.compile(
    r"^(?:(?P<mon>[A-Z][a-z]{2})\s+(?P<day>\d{1,2}))?\s{2,}"
    r"(?P<desc>\S.*?)\s{2,}(?P<type>\S.*?)\s{2,}"
    r"(?P<amt>[" + NEG + r"]?\$[\d,]+\.\d{2})"
)
# Raw mode: spaces already stripped, so the type is whatever known token sits
# immediately before the first amount.
TYPE_SUFFIX = re.compile(
    "(" + "|".join([
        BULLET * 2 + r"\d{4}", r"CreditAccountPayment", r"CreditCashbackDeposit",
        r"Refund", r"Reversal", r"Dispute[A-Za-z]*",
    ]) + ")$"
)

PAYMENT_TOKEN = "creditaccountpayment"


def _is_payment(descriptor) -> bool:
    return PAYMENT_TOKEN in norm_text(descriptor).replace(" ", "")


def month_year(lines):
    for ln in lines[:25]:
        s = ln.replace(" ", "")
        m = re.search("(" + "|".join(FULL) + r")(\d{4})statement", s)
        if m:
            return FULL[m.group(1)], int(m.group(2))
    return None, None


def summary_of(text) -> dict:
    """The Overview block. A field that could not be read is None, never 0.00."""
    flat = [ln.replace(" ", "") for ln in text.split("\n")]

    def grab(key):
        for ln in flat:
            if ln.lower().startswith(key):
                tokens = MONEY_RE.findall(ln)
                if tokens:
                    return money(tokens[-1], key)
                return None
        return None

    return {
        "spending": grab("spending"),
        "starting": grab("startingbalance"),
        "posted": grab("postedtransactions"),
        "manual_payments": grab("manualpayments"),
        "automatic_payments": grab("automaticpayments"),
        "total": _total_row(flat),
    }


def _total_row(flat_lines):
    """The trailing Total row of the transaction table: the closing balance."""
    for ln in reversed(flat_lines):
        if ln.lower().startswith("total"):
            tokens = MONEY_RE.findall(ln)
            if tokens:
                return money(tokens[-1], "total")
    return None


# Two header shapes, both real. The current one is a calendar month
# ("June 2025 statement" over "June 2025-June 30, 2025 (30 days)"); older cards
# billed on a mid-month cycle and print only "March 11-April 10, 2024 (31 days)"
# with no "<Month> <Year> statement" line at all. A statement is labelled by the
# month its cycle ENDS in.
_PERIOD_RE = re.compile(
    r"([A-Z][a-z]+)(\d{1,4})[" + NEG + r"]([A-Z][a-z]+)(\d{1,2}),(\d{4})"
)


def period_of(text):
    """(period_start, period_end) from the header line, or (None, None)."""
    for ln in text.split("\n")[:25]:
        s = ln.replace(" ", "")
        m = _PERIOD_RE.search(s)
        if not m or m.group(1) not in FULL or m.group(3) not in FULL:
            continue
        m1, v1, m2 = FULL[m.group(1)], m.group(2), FULL[m.group(3)]
        end = _dt.date(int(m.group(5)), m2, int(m.group(4)))
        if len(v1) == 4:                      # "June 2025-June 30, 2025"
            start = _dt.date(int(v1), m1, 1)
        else:                                 # "March 11-April 10, 2024"
            start = _dt.date(end.year if m1 <= m2 else end.year - 1, m1, int(v1))
        return start, end
    month, year = month_year(text.split("\n"))
    if month:
        return _dt.date(year, month, 1), _dt.date(year, month, calendar.monthrange(year, month)[1])
    return None, None


def _date_for(month, day, period_start, period_end):
    """Date a row whose statement prints only "Feb 28", no year.

    A cycle can straddle a year end (a December statement carrying the January
    autopay), so the year is whichever candidate lands closest to the statement
    period rather than whichever one the file name suggests.
    """
    best = None
    for year in (period_start.year - 1, period_start.year, period_end.year,
                 period_end.year + 1):
        try:
            candidate = _dt.date(year, month, day)
        except ValueError:
            continue
        if candidate < period_start:
            distance = (period_start - candidate).days
        elif candidate > period_end:
            distance = (candidate - period_end).days
        else:
            distance = 0
        if best is None or distance < best[0]:
            best = (distance, candidate)
    return best[1] if best else None


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


def parse_layout(text, period_start, period_end, source_file="") -> list:
    body = text.split("All Transactions")[-1]
    out, date = [], None
    for offset, ln in enumerate(body.split("\n")):
        m = ROW_LAYOUT.match(ln)
        if not m:
            continue
        if m.group("mon"):
            month = MON3.get(m.group("mon"))
            if not month:
                continue
            date = _date_for(month, int(m.group("day")), period_start, period_end)
        if date is None:
            continue
        tail = ln[m.end("amt"):]
        balances = MONEY_RE.findall(tail)
        descriptor = (m.group("desc").strip() + " " + m.group("type").strip()).strip()
        out.append(_line(
            date.year, date.month, date.day, descriptor,
            money(m.group("amt"), "card amount"),
            money(balances[0], "balance") if balances else None,
            source_file, offset + 1,
        ))
    return out


def parse_raw(text, period_start, period_end, source_file="") -> list:
    """Glyph-order parse for statements whose fonts declare broken widths."""
    lines = [ln.replace(" ", "").strip() for ln in text.split("\n")]
    out, date, buf, started = [], None, "", False
    for offset, s in enumerate(lines):
        if not s:
            continue
        if s.startswith("Date(UTC)Description"):
            started = True
            continue
        if not started:
            continue
        if (s.startswith(("Total$", "TheMercuryIOCard", "Mercury|", "BankingServices"))
                or s.endswith("AllTransactions/")):
            continue
        m = re.match(r"^([A-Z][a-z]{2})(\d{1,2})(?![\d])", s)
        if m and m.group(1) in MON3:
            month = MON3[m.group(1)]
            date = _date_for(month, int(m.group(2)), period_start, period_end)
            s = s[m.end():]
            buf = ""
        if date is None:
            continue
        tokens = [(t.group(0), t.start()) for t in MONEY_RE.finditer(s)]
        if not tokens:
            buf += s
            continue
        head = buf + s[:tokens[0][1]]
        buf = ""
        if not head:
            continue
        tm = TYPE_SUFFIX.search(head)
        descriptor, kind = (head[:tm.start()], tm.group(0)) if tm else (head, "")
        out.append(_line(
            date.year, date.month, date.day,
            (descriptor + " " + kind).strip(),
            money(tokens[0][0], "card amount"),
            money(tokens[1][0], "balance") if len(tokens) > 1 else None,
            source_file, offset + 1,
        ))
    return out


def _build(mode, lines, summary, start, end, path, account_key, opening_sign):
    starting = summary.get("starting")
    # A card balance is a LIABILITY, so an amount owed is negative in the same
    # way the statement's own running balance column renders it. The Overview
    # prints "Starting Balance" WITHOUT a sign.
    # VERIFIED: no (2026-09-09). Every statement inspected while writing this
    # opened at 0.00, so the sign of a NON-ZERO opening could not be confirmed
    # against a real file. When the chain does not tie with a negative opening
    # and does tie with a positive one, the positive one is used and a note
    # says so; that inference is reported, never silent.
    opening = ZERO if starting is None else opening_sign * abs(starting)

    notes = [f"extraction mode: pdftotext {mode}"]
    if opening_sign > 0 and starting not in (None, ZERO):
        notes.append(
            "opening balance taken as POSITIVE: the statement prints it unsigned "
            "and the chain only closes with that sign. Confirm against the prior "
            "statement's closing balance."
        )
    late = [l for l in lines if l.date > end]
    if late:
        notes.append(
            f"{len(late)} row(s) dated after {end.isoformat()} (the next cycle's "
            f"payment). Kept, because the statement's balance chain includes them, "
            f"but the same payment also appears on the funding account: book it once."
        )

    statement = StatementFile(
        account_key=account_key,
        period_start=start,
        period_end=end,
        opening_balance=opening,
        closing_balance=summary.get("total"),
        lines=lines,
        source_file=str(path),
        parser=PARSER_NAME,
        stated_deposits=None,      # Mercury prints no gross deposits figure
        stated_withdrawals=None,   # "Spending" is NET of refunds; checked below
        notes=notes,
    )
    result = statement.check()
    _apply_card_checks(statement, result, summary)
    return statement


def _apply_card_checks(statement, result, summary):
    """The card's own two extra legs: posted transactions, and payments.

    Folded into the same TieOutResult so that `ties` means "everything the
    statement asserts about itself is true", not just "the chain closes".
    """
    posted_rows = [l for l in statement.lines if not _is_payment(l.descriptor)]
    payment_rows = [l for l in statement.lines if _is_payment(l.descriptor)]
    computed_posted = money(sum((l.amount for l in posted_rows), ZERO))
    computed_payments = money(sum((l.amount for l in payment_rows), ZERO))

    stated_posted = summary.get("posted")
    if stated_posted is None and summary.get("spending") is not None:
        stated_posted = -abs(summary["spending"])
    stated_payments = None
    manual, auto = summary.get("manual_payments"), summary.get("automatic_payments")
    if manual is not None or auto is not None:
        stated_payments = money((manual or ZERO) + (auto or ZERO))

    notes = []
    for name, stated, computed in (
        ("posted transactions", stated_posted, computed_posted),
        ("payments", stated_payments, computed_payments),
    ):
        if stated is None:
            result.checks.append((name, False, False, ZERO))
            notes.append(f"stated {name} not available")
            continue
        difference = money(computed - stated)
        result.checks.append((name, True, difference == ZERO, difference))
        if difference != ZERO:
            notes.append(f"stated {name} differs by {fmt(difference)}")
            result.ties = False

    if summary.get("spending") is not None:
        difference = money(-computed_posted - summary["spending"])
        if difference != ZERO:
            notes.append(f"stated Spending differs by {fmt(difference)}")
            result.ties = False

    if notes:
        result.note = "; ".join([n for n in [result.note] if n] + notes)
        statement.notes.extend(notes)


def parse(path, account_key="", **kwargs) -> StatementFile:
    """Parse one Mercury IO card statement. PDF, or CSV if that is what you have."""
    if str(path).lower().endswith((".csv", ".tsv")):
        # Mercury's card CSV carries the same signed Amount column as its bank
        # CSV: a purchase is already negative. If a future export flips that,
        # pass amount_sign="flip" rather than editing this module.
        # VERIFIED: no (2026-09-09), no real Mercury card CSV was available.
        return generic_csv.parse(path, account_key, parser=PARSER_NAME, **kwargs)

    layout_text = pdf_text(path, "-layout")
    raw_text = pdf_text(path, "-raw")

    summary = summary_of(layout_text)
    if summary["starting"] is None or summary["total"] is None:
        recovered = summary_of(raw_text)
        for key, value in recovered.items():
            if summary.get(key) is None and value is not None:
                summary[key] = value

    start, end = period_of(layout_text)
    if not start:
        start, end = period_of(raw_text)
    if not start:
        raise ParseError(
            f"{os.path.basename(str(path))}: no statement period found. Expected either "
            f"a \"<Month> <Year> statement\" line or a \"<Month> <day>-<Month> <day>, "
            f"<year>\" cycle line. This does not look like a Mercury card statement."
        )

    candidates = []
    for mode, lines in (("-layout", parse_layout(layout_text, start, end, path)),
                        ("-raw", parse_raw(raw_text, start, end, path))):
        for sign in (-1, 1):
            candidates.append(_build(mode, lines, summary, start, end, path,
                                     account_key, sign))
            if candidates[-1].ties:
                break
            if not summary.get("starting"):
                break      # opening is 0.00, so the sign cannot be the problem

    ties = [c for c in candidates if c.ties]
    if ties:
        return ties[0]

    best = max(candidates, key=lambda c: len(c.lines))
    if not best.lines:
        quiet = (summary.get("spending") in (ZERO, None)
                 and summary.get("posted") in (ZERO, None)
                 and summary.get("total") == ZERO)
        if quiet:
            best.notes.append("statement has no transactions and its summary agrees")
            return best
        raise ParseError(
            f"{os.path.basename(str(path))}: both -layout and -raw returned ZERO "
            f"transactions while the summary reports spending {summary.get('spending')}. "
            f"Refusing to report an empty month; the extraction is broken."
        )
    best.notes.append(
        "neither -layout nor -raw ties; reporting the parse with more rows "
        f"({len(best.lines)}) and the difference as a number"
    )
    return best


def detect(path) -> bool:
    if not str(path).lower().endswith(".pdf"):
        return False
    try:
        head = pdf_text(path, "-layout")[:4000]
    except ParseError:
        return False
    flat = head.replace(" ", "")
    return "Mercury" in head and "CreditCard" in flat and "StartingBalance" in flat
