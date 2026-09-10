"""Parse the CSV a bank or card issuer exports, whatever shape it is in.

This is the parser most people will actually use, because almost every
institution can export a CSV even when nobody has written a parser for its
PDF. So it is deliberately generous about layout and deliberately strict about
one thing: it never guesses a direction. A row whose direction cannot be
established raises rather than being counted as money in.

Layouts handled, sniffed from the header row:

  1. one signed `Amount` column                     (Mercury, Chase, most)
  2. separate `Debit` and `Credit` columns          (Citizens, many banks)
  3. the same two in the other order                (column order is irrelevant,
                                                     roles are matched by name)
  4. a `Withdrawal` / `Deposit` pair                (Wells Fargo style)
  5. any of the above plus a running `Balance`      (used to derive the opening
                                                     and closing balances)
  6. an unsigned `Amount` plus a `Type` column of DEBIT/CREDIT              

Conventions this module enforces, once, so no caller has to think about them:

* `BankLine.amount` is signed from the account holder's point of view: money in
  positive, money out negative, for a bank account AND for a card.
* A `Debit` column is money OUT no matter how its sign is printed, and a
  `Credit` column is money IN. Some exports print a debit as -25.00 and some as
  25.00, so the magnitude is taken and the sign is applied by the column's
  meaning. Same for `Withdrawal` / `Deposit`.
* A card export that renders a purchase as a POSITIVE number is normalized by
  passing `amount_sign="flip"`. There is no auto-detection of that, because
  "most rows are positive" is true of a deposit-heavy month too.
* A blank date cell carries the previous row's date forward. Statement exports
  really do leave the date blank on the second and later rows of a day, and the
  same convention leaks into the CSVs generated from them.

Standard library only.
"""

from __future__ import annotations

import csv
import io
import os
import re

from ..model import BankLine
from ..util import ZERO, money, norm_text, try_date
from .base import ParseError, StatementFile

__all__ = [
    "parse", "parse_table", "read_table", "map_headers", "detect",
    "ROLE_ALIASES", "PARSER_NAME",
]

PARSER_NAME = "generic_csv"

# Header names seen in the wild, normalized (lowercased, punctuation to spaces,
# runs collapsed). Order inside each tuple does not matter; the ORDER OF THE
# ROLES below does, because the fuzzy pass checks them in that order and
# "withdrawal amount" must reach `debit` before it reaches `amount`.
ROLE_ALIASES = {
    "balance": (
        "balance", "running balance", "running bal", "ending balance",
        "end of day balance", "account balance", "available balance",
        "balance usd", "resulting balance", "closing balance",
    ),
    "posted_date": (
        "posted date", "post date", "posting date", "date posted",
        "settlement date", "settled date", "cleared date", "clearing date",
    ),
    "date": (
        "date", "transaction date", "trans date", "txn date", "activity date",
        "effective date", "value date", "booking date", "date utc",
        "completion date", "initiated date", "created at", "time",
    ),
    "debit": (
        "debit", "debits", "debit amount", "withdrawal", "withdrawals",
        "withdrawal amount", "withdrawals amount", "money out", "paid out",
        "funds out", "amount debit", "debit usd", "outflow",
    ),
    "credit": (
        "credit", "credits", "credit amount", "deposit", "deposits",
        "deposit amount", "deposits amount", "money in", "paid in",
        "funds in", "amount credit", "credit usd", "inflow",
    ),
    "amount": (
        "amount", "transaction amount", "amt", "amount usd", "value",
        "net amount", "signed amount", "gross amount", "amount in usd",
        "amount in account currency",
    ),
    "external_id": (
        "transaction id", "txn id", "id", "reference id", "reference number",
        "transaction reference", "external id", "trace number",
        "transaction number", "unique id",
    ),
    "type": (
        "type", "transaction type", "txn type", "debit credit", "dr cr",
        "dr cr indicator", "debit or credit", "direction", "entry type",
    ),
    "description": (
        "description", "details", "detail", "memo", "payee", "name",
        "merchant", "merchant name", "narrative", "transaction description",
        "particulars", "note", "notes", "bank description", "counterparty",
        "description1", "original description", "vendor", "reference",
    ),
}
_ROLE_ORDER = tuple(ROLE_ALIASES)

# Values a Type column uses to say which way the money went. Read only when the
# Amount column is entirely unsigned; see `_sign_from_type`.
_TYPE_OUT = {
    "debit", "dr", "d", "withdrawal", "withdrawl", "w", "wd", "w d", "payment",
    "purchase", "charge", "sale", "fee", "check", "cheque", "atm", "transfer out",
    "ach debit", "card purchase", "bill payment", "outflow", "out",
}
_TYPE_IN = {
    "credit", "cr", "c", "deposit", "dep", "refund", "return", "interest",
    "reversal", "transfer in", "ach credit", "payment received", "inflow", "in",
    "rebate", "cashback", "adjustment credit",
}

_MAX_HEADER_SCAN = 25    # how deep to look for the header row past a preamble


def _norm_header(value) -> str:
    """Normalize one header cell for matching. 'Amount (USD)' -> 'amount usd'."""
    s = norm_text(value)
    s = re.sub(r"[^a-z0-9]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def map_headers(headers, extra_aliases=None) -> dict:
    """Map header cells to roles. Returns {role: column index}.

    `extra_aliases` is {role: (name, ...)} merged on top of ROLE_ALIASES, which
    is how an institution-specific module (brex_csv) adds its own column names
    without forking this one.

    Exact alias matches are taken first across all columns, then a substring
    pass in role order fills what is left. The first column to claim a role
    keeps it; later ones are ignored except for `description`, which collects
    every match so a blank Description can fall back to Memo.
    """
    aliases_by_role = {role: tuple(names) for role, names in ROLE_ALIASES.items()}
    for role, names in (extra_aliases or {}).items():
        aliases_by_role[role] = tuple(names) + aliases_by_role.get(role, ())
    normalized = [_norm_header(h) for h in headers]
    roles: dict = {}
    description_cols: list = []
    claimed: set = set()

    def claim(role, idx):
        if role == "description":
            if idx not in description_cols:
                description_cols.append(idx)
            claimed.add(idx)
            roles.setdefault("description", idx)
            return
        if role not in roles and idx not in claimed:
            roles[role] = idx
            claimed.add(idx)

    for role in _ROLE_ORDER:
        aliases = aliases_by_role[role]
        for idx, name in enumerate(normalized):
            if name and name in aliases:
                claim(role, idx)

    for role in _ROLE_ORDER:
        aliases = aliases_by_role[role]
        for idx, name in enumerate(normalized):
            if not name or idx in claimed:
                continue
            if any(alias in name or name in alias for alias in aliases if len(alias) > 2):
                claim(role, idx)

    roles["description_cols"] = description_cols
    return roles


def _layout_name(roles) -> str:
    if "amount" in roles:
        return "amount"
    if "debit" in roles or "credit" in roles:
        return "debit_credit"
    return "unknown"


def _usable(roles) -> bool:
    has_date = "date" in roles or "posted_date" in roles
    has_money = "amount" in roles or "debit" in roles or "credit" in roles
    return has_date and has_money


# ------------------------------------------------------------------ reading

def _decode(path) -> str:
    with open(path, "rb") as fh:
        raw = fh.read()
    for encoding in ("utf-8-sig", "utf-8", "cp1252", "latin-1"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("latin-1", errors="replace")


def _delimiter(text) -> str:
    sample = "\n".join(text.splitlines()[:20])
    try:
        return csv.Sniffer().sniff(sample, delimiters=",;\t|").delimiter
    except csv.Error:
        first = sample.splitlines()[0] if sample.splitlines() else ""
        counts = {d: first.count(d) for d in ",;\t|"}
        best = max(counts, key=counts.get)
        return best if counts[best] else ","


def read_table(path, extra_aliases=None):
    """Return (headers, rows, meta). Skips any preamble above the header row."""
    text = _decode(path)
    if not text.strip():
        raise ParseError(f"{os.path.basename(str(path))}: file is empty")
    delimiter = _delimiter(text)
    all_rows = [r for r in csv.reader(io.StringIO(text), delimiter=delimiter)]
    all_rows = [r for r in all_rows if any((c or "").strip() for c in r)]
    if not all_rows:
        raise ParseError(f"{os.path.basename(str(path))}: no non-blank rows")

    best_idx, best_roles, best_score = None, None, -1
    for idx, row in enumerate(all_rows[:_MAX_HEADER_SCAN]):
        roles = map_headers(row, extra_aliases)
        score = len([k for k in roles if k != "description_cols"])
        if _usable(roles) and score > best_score:
            best_idx, best_roles, best_score = idx, roles, score

    if best_idx is None:
        seen = all_rows[0]
        raise ParseError(
            f"{os.path.basename(str(path))}: no header row maps to a date column and an "
            f"amount column. Headers seen: {seen!r}. Expected a date column named one of "
            f"{ROLE_ALIASES['date'][:5]}... and either an Amount column or a Debit/Credit "
            f"(or Withdrawal/Deposit) pair."
        )

    headers = all_rows[best_idx]
    rows = all_rows[best_idx + 1:]
    meta = {
        "delimiter": delimiter,
        "header_row": best_idx + 1,
        "preamble_rows": best_idx,
        "roles": best_roles,
        "layout": _layout_name(best_roles),
    }
    return headers, rows, meta


# ------------------------------------------------------------------ signing

def _cell(row, idx):
    if idx is None or idx < 0 or idx >= len(row):
        return ""
    return (row[idx] or "").strip()


def _sign_from_type(rows, roles):
    """Decide whether an unsigned Amount column has to be signed by Type.

    Only ever engaged when NO amount in the file is negative. If some rows are
    negative the file already carries its own signs and the Type column is
    decoration.
    """
    a_idx, t_idx = roles.get("amount"), roles.get("type")
    if a_idx is None or t_idx is None:
        return False, set()
    values, types = [], set()
    for row in rows:
        cell = _cell(row, a_idx)
        if not cell:
            continue
        try:
            values.append(money(cell))
        except Exception:
            continue
        types.add(_norm_header(_cell(row, t_idx)))
    if not values or any(v < ZERO for v in values):
        return False, set()
    unmapped = {t for t in types if t and t not in _TYPE_OUT and t not in _TYPE_IN}
    return True, unmapped


def _amount_of(row, roles, use_type, flip):
    """The signed amount for one row, or None when the row carries no amount."""
    a_idx = roles.get("amount")
    if a_idx is not None:
        cell = _cell(row, a_idx)
        if cell:
            amount = money(cell, "amount")
            if use_type:
                kind = _norm_header(_cell(row, roles.get("type")))
                if kind in _TYPE_OUT:
                    amount = -abs(amount)
                elif kind in _TYPE_IN:
                    amount = abs(amount)
            return -amount if flip else amount

    d_idx, c_idx = roles.get("debit"), roles.get("credit")
    debit_cell = _cell(row, d_idx) if d_idx is not None else ""
    credit_cell = _cell(row, c_idx) if c_idx is not None else ""
    if not debit_cell and not credit_cell:
        return None
    # The column's MEANING sets the sign. Some banks print a debit as -25.00,
    # some as 25.00; taking the magnitude makes both read the same way.
    debit = abs(money(debit_cell, "debit")) if debit_cell else ZERO
    credit = abs(money(credit_cell, "credit")) if credit_cell else ZERO
    amount = credit - debit
    return -amount if flip else amount


# ------------------------------------------------------------------ parsing

def parse_table(headers, rows, meta, *, account_key="", source_file="",
                amount_sign="auto", dayfirst=False):
    """Turn a sniffed table into BankLines. Returns (lines, notes)."""
    roles = meta["roles"]
    notes, lines = [], []
    flip = amount_sign == "flip"

    use_type, unmapped = (False, set())
    if amount_sign == "auto":
        use_type, unmapped = _sign_from_type(rows, roles)
        if use_type and unmapped:
            raise ParseError(
                f"{os.path.basename(str(source_file)) or 'table'}: every Amount is "
                f"non-negative, so the direction has to come from the "
                f"{headers[roles['type']]!r} column, but these of its values are not "
                f"a direction: {sorted(unmapped)}. Pass amount_sign='as_is' if the "
                f"amounts are already signed, or amount_sign='flip' if every row is "
                f"money out (a card export)."
            )
        if use_type:
            notes.append(f"direction taken from the {headers[roles['type']]!r} column")

    date_idx = roles.get("date", roles.get("posted_date"))
    posted_idx = roles.get("posted_date")
    if posted_idx == date_idx:
        posted_idx = None
    desc_cols = roles.get("description_cols") or []
    bal_idx = roles.get("balance")
    id_idx = roles.get("external_id")

    carried_date = None
    skipped_no_amount = 0
    skipped_no_date = 0

    for offset, row in enumerate(rows):
        row_no = meta.get("header_row", 1) + offset + 1
        raw_date = _cell(row, date_idx)
        # A blank date cell means "same day as the row above". Statement exports
        # do this on the second and later rows of a day; carrying the last date
        # forward is the only correct reading.
        if raw_date:
            date = try_date(raw_date, dayfirst=dayfirst)
            if date is None:
                skipped_no_date += 1
                continue
            carried_date = date
        else:
            date = carried_date

        try:
            amount = _amount_of(row, roles, use_type, flip)
        except Exception as exc:
            raise ParseError(
                f"{os.path.basename(str(source_file)) or 'table'} row {row_no}: {exc}"
            ) from exc
        if amount is None:
            skipped_no_amount += 1
            continue
        if date is None:
            skipped_no_date += 1
            continue

        descriptor = ""
        for idx in desc_cols:
            descriptor = _cell(row, idx)
            if descriptor:
                break

        balance = None
        if bal_idx is not None:
            bal_cell = _cell(row, bal_idx)
            if bal_cell:
                balance = money(bal_cell, "balance")

        lines.append(BankLine(
            date=date,
            descriptor=descriptor,
            amount=amount,
            account_key=account_key,
            balance=balance,
            external_id=_cell(row, id_idx) if id_idx is not None else "",
            posted_date=try_date(_cell(row, posted_idx)) if posted_idx is not None else None,
            source_file=str(source_file),
            source_row=row_no,
            origin="csv",
        ))

    if skipped_no_amount:
        notes.append(f"{skipped_no_amount} rows carried no amount and were skipped")
    if skipped_no_date:
        notes.append(f"{skipped_no_date} rows carried no readable date and were skipped")
    if flip:
        notes.append("amount signs flipped (amount_sign='flip')")
    return lines, notes


def derive_balances(lines):
    """Order the lines and read an opening and closing balance off the file.

    Returns (ordered_lines, opening, closing, notes). Opening and closing are
    None when the export carries no running balance column; the caller then
    passes them in or leaves the tie-out to report the missing check.
    """
    notes = []
    if not lines:
        return [], None, None, notes

    ordered = list(lines)
    if len(ordered) > 1 and ordered[0].date > ordered[-1].date:
        # Newest-first export. Reverse before sorting so that the order of the
        # rows WITHIN a day is preserved, which is what makes the running
        # balance column readable.
        ordered.reverse()
        notes.append("export was newest-first; rows reversed into date order")
    ordered.sort(key=lambda l: l.date)   # stable, so intra-day order survives

    if all(l.balance is not None for l in ordered):
        opening = money(ordered[0].balance - ordered[0].amount)
        closing = money(ordered[-1].balance)
        return ordered, opening, closing, notes

    if any(l.balance is not None for l in ordered):
        notes.append(
            "running balance column is populated on some rows only; "
            "opening and closing balances not derived from it"
        )
    return ordered, None, None, notes


def parse(path, account_key="", *, amount_sign="auto", dayfirst=False,
          opening=None, closing=None, parser=PARSER_NAME,
          stated_deposits=None, stated_withdrawals=None,
          extra_aliases=None, extra_notes=()) -> StatementFile:
    """Parse one CSV export into a StatementFile, tie-out included.

    `opening` / `closing` override whatever the running balance column implies,
    for the common case where the CSV has no balance column and the founder
    reads the two figures off the PDF.
    """
    headers, rows, meta = read_table(path, extra_aliases)
    lines, notes = parse_table(
        headers, rows, meta,
        account_key=account_key, source_file=str(path),
        amount_sign=amount_sign, dayfirst=dayfirst,
    )
    if not lines:
        raise ParseError(
            f"{os.path.basename(str(path))}: header row {meta['header_row']} mapped as "
            f"{meta['layout']} but no data row produced an amount. Headers: {headers!r}. "
            f"A statement parser that returns zero rows is a defect, not an empty month."
        )

    ordered, derived_open, derived_close, balance_notes = derive_balances(lines)
    notes.extend(balance_notes)
    notes.insert(0, f"layout: {meta['layout']}, delimiter {meta['delimiter']!r}, "
                    f"header on row {meta['header_row']}")
    notes.extend(extra_notes)

    opening_balance = derived_open if opening is None else money(opening)
    closing_balance = derived_close if closing is None else money(closing)
    if opening_balance is None:
        opening_balance = ZERO
        if closing_balance is not None:
            notes.append("no opening balance available; treated as 0.00 for the chain")

    statement = StatementFile(
        account_key=account_key,
        period_start=ordered[0].date,
        period_end=ordered[-1].date,
        opening_balance=opening_balance,
        closing_balance=closing_balance,
        lines=ordered,
        source_file=str(path),
        parser=parser,
        stated_deposits=stated_deposits,
        stated_withdrawals=stated_withdrawals,
        notes=notes,
    )
    statement.check()
    return statement


def detect(path, extra_aliases=None) -> bool:
    """True when this file reads as a transaction CSV at all."""
    if os.path.splitext(str(path))[1].lower() not in (".csv", ".tsv", ".txt"):
        return False
    try:
        read_table(path, extra_aliases)
        return True
    except (ParseError, OSError):
        return False
