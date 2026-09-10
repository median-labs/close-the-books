"""Brex CSV exports: cash, card, treasury and vault.

VERIFIED: no (2026-09-09). Nothing in this repository has ever been tested
against a real Brex export. The column names below were inferred from Brex's
documented export screens and from the shapes other issuers use; the parsing
machinery underneath is the same `generic_csv` layer that IS exercised against
real files. A maintainer with a real export should: run
`python3 -m closethebooks.statements.brex_csv <file.csv>`, check the mapping it
prints against the real header row, correct `BREX_ALIASES` and the sign note
below, and change this marker to `VERIFIED: yes (<date>, <account type>)`.

The two things to confirm first, because they are the two that would silently
corrupt a month:

1. **Sign on a card export.** Card issuers split roughly evenly between
   rendering a purchase as positive (a "spend" column) and as negative (an
   account-holder view). This module assumes SPEND-POSITIVE for the card and
   vault flavours, and only applies the flip when every amount in the file is
   non-negative, which is the only shape where the assumption can be true. If
   any row is negative the file is taken as already signed. Whatever it does,
   it says so in `statement.notes`.
2. **Whether a separate settlement/original amount column exists** for foreign
   currency. If it does, decide which one the ledger should carry; this module
   maps `amount` and leaves any original-currency column unread.

Brex cash and treasury exports are ordinary bank-shaped exports and need no
special handling beyond the column names.
"""

from __future__ import annotations

import os
import sys

from . import generic_csv
from .base import StatementFile

__all__ = ["parse", "detect", "flavor_of", "BREX_ALIASES", "PARSER_NAME", "VERIFIED"]

PARSER_NAME = "brex_csv"
VERIFIED = "no (2026-09-09): header set inferred, never run against a real Brex export"

# Column names layered on top of generic_csv.ROLE_ALIASES.
# VERIFIED: no. Each of these is a guess about Brex's wording.
BREX_ALIASES = {
    "date": (
        "purchase date", "swipe date", "authorization date", "initiated date",
        "transaction date", "date time", "created date",
    ),
    "posted_date": ("settlement date", "settled date", "clearing date"),
    "description": (
        "merchant", "merchant name", "vendor", "counterparty", "counterparty name",
        "memo", "expense description", "description",
    ),
    "amount": (
        "amount", "purchase amount", "settlement amount", "amount usd",
        "posted amount", "billing amount",
    ),
    "type": ("transaction type", "type", "direction"),
    "external_id": ("transaction id", "id", "brex transaction id"),
    "balance": ("running balance", "balance", "ending balance"),
}

_CARD_MARKERS = ("card", "merchant", "spend", "expense", "swipe")
_CASH_MARKERS = ("cash", "checking", "account", "counterparty")
_TREASURY_MARKERS = ("treasury", "vault", "yield", "fund")


def flavor_of(path, headers=()) -> str:
    """cash | card | treasury | vault, from the file name and then the headers.

    Only used to decide the sign assumption and to label the statement. It is
    never allowed to change which rows are read.
    """
    hay = os.path.basename(str(path)).lower() + " " + " ".join(str(h).lower() for h in headers)
    if "vault" in hay:
        return "vault"
    if any(marker in hay for marker in _TREASURY_MARKERS):
        return "treasury"
    if any(marker in hay for marker in _CARD_MARKERS):
        return "card"
    if any(marker in hay for marker in _CASH_MARKERS):
        return "cash"
    return "cash"


def parse(path, account_key="", *, flavor=None, amount_sign=None, **kwargs) -> StatementFile:
    """Parse a Brex export. `flavor` overrides the guess; `amount_sign` overrides the sign."""
    headers, rows, meta = generic_csv.read_table(path, BREX_ALIASES)
    flavor = flavor or flavor_of(path, headers)

    notes = [
        f"brex flavour: {flavor}",
        f"brex column mapping is UNVERIFIED ({VERIFIED}); "
        f"confirm the header row against a real export before trusting a close",
    ]

    if amount_sign is None:
        amount_sign = "auto"
        if flavor in ("card", "vault"):
            amounts = _amounts(headers, rows, meta)
            if amounts and all(a >= 0 for a in amounts):
                # Every row non-negative on a card export means the file is a
                # spend column, not an account-holder view. Flip it so a
                # purchase is money out. UNVERIFIED assumption; see the header.
                amount_sign = "flip"
                notes.append(
                    "every amount in this card export is non-negative, so it was read "
                    "as spend-positive and the signs were flipped: a purchase is now "
                    "money out. If that is wrong, pass amount_sign='as_is'."
                )
            else:
                notes.append("card export already carries signs; taken as-is")

    return generic_csv.parse(
        path, account_key,
        amount_sign=amount_sign,
        parser=PARSER_NAME,
        extra_aliases=BREX_ALIASES,
        extra_notes=notes,
        **kwargs,
    )


def _amounts(headers, rows, meta):
    idx = meta["roles"].get("amount")
    if idx is None:
        return []
    from ..util import money
    out = []
    for row in rows:
        cell = (row[idx] or "").strip() if idx < len(row) else ""
        if cell:
            try:
                out.append(money(cell))
            except Exception:
                continue
    return out


def detect(path) -> bool:
    """True when the file names Brex, or maps cleanly with Brex's column names."""
    name = os.path.basename(str(path)).lower()
    if os.path.splitext(name)[1] not in (".csv", ".tsv"):
        return False
    if "brex" in name:
        return True
    return False      # never claim a plain CSV as Brex; generic_csv handles it


def _main(argv):
    """`python3 -m closethebooks.statements.brex_csv <file.csv>` prints the mapping.

    This is the tool for confirming the inferred header set against a real
    export. It writes nothing.
    """
    if len(argv) != 2:
        print(__doc__)
        return 2
    headers, rows, meta = generic_csv.read_table(argv[1], BREX_ALIASES)
    roles = meta["roles"]
    print(f"file:    {argv[1]}")
    print(f"flavour: {flavor_of(argv[1], headers)}")
    print(f"layout:  {meta['layout']}, header on row {meta['header_row']}, "
          f"{len(rows)} data rows")
    print("mapping:")
    for role, idx in sorted(roles.items()):
        if role == "description_cols":
            continue
        print(f"  {role:<12} <- column {idx}: {headers[idx]!r}")
    unmapped = [h for i, h in enumerate(headers)
                if i not in {v for k, v in roles.items() if k != 'description_cols'}]
    print(f"unmapped columns: {unmapped}")
    return 0


if __name__ == "__main__":
    sys.exit(_main(sys.argv))
