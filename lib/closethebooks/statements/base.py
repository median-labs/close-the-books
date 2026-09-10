"""What every statement parser returns, and the bits they all share.

`StatementFile` lives here rather than in `__init__.py` so that a parser module
can import it without importing the registry that imports the parser. It is
re-exported from `closethebooks.statements`, which is where callers should
import it from.
"""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Optional

from ..model import BankLine
from ..tieout import TieOutResult, tie_out
from ..util import ZERO, money, month_key

__all__ = ["StatementFile", "ParseError", "pdf_text", "have_pdftotext"]


class ParseError(ValueError):
    """A file could not be read as a statement.

    The message always names what was actually seen (the headers, the modes
    tried, the count of rows found), because the single most expensive failure
    in this domain is a parser that returns zero rows and lets the caller
    believe the month was empty.
    """


@dataclass
class StatementFile:
    """One statement: its period, its balances, and its lines.

    `opening_balance` and `closing_balance` are signed the same way as
    `BankLine.amount`, from the account holder's point of view. For a bank
    account that is the ordinary positive balance. For a credit card an amount
    OWED is negative, which is how the card statements themselves render their
    running balance column.

    `closing_balance` is Optional and None means "the statement did not print
    one, or the one it printed could not be read". It never means zero. The
    tie-out treats None as an unavailable check and falls back to the chain
    identity rather than silently passing.
    """

    account_key: str = ""
    period_start: object = None          # datetime.date
    period_end: object = None            # datetime.date
    opening_balance: Decimal = ZERO
    closing_balance: Optional[Decimal] = None
    lines: list = field(default_factory=list)     # list[BankLine]
    source_file: str = ""
    parser: str = ""
    tie_out: Optional[TieOutResult] = None

    # The other two legs of the three-way check, where the statement prints
    # them. None means the statement gave no figure or the figure was corrupt.
    stated_deposits: Optional[Decimal] = None
    stated_withdrawals: Optional[Decimal] = None
    # Everything a reader would want to know about HOW this was parsed:
    # extraction mode, unreadable summary fields, rows dated outside the
    # period, inferred signs. Never empty for a file that needed a workaround.
    notes: list = field(default_factory=list)

    def __post_init__(self):
        self.opening_balance = money(self.opening_balance, "opening balance")
        if self.closing_balance is not None:
            self.closing_balance = money(self.closing_balance, "closing balance")
        if self.stated_deposits is not None:
            self.stated_deposits = money(self.stated_deposits, "stated deposits")
        if self.stated_withdrawals is not None:
            self.stated_withdrawals = money(self.stated_withdrawals, "stated withdrawals")
        for line in self.lines:
            if self.account_key and not line.account_key:
                line.account_key = self.account_key

    # ------------------------------------------------------------- totals
    @property
    def deposits(self) -> Decimal:
        return money(sum((l.amount for l in self.lines if l.amount > ZERO), ZERO))

    @property
    def withdrawals(self) -> Decimal:
        """Positive magnitude of the money that left."""
        return money(-sum((l.amount for l in self.lines if l.amount < ZERO), ZERO))

    @property
    def net(self) -> Decimal:
        return money(sum((l.amount for l in self.lines), ZERO))

    @property
    def computed_closing(self) -> Decimal:
        return money(self.opening_balance + self.net)

    @property
    def month(self) -> str:
        end = self.period_end or self.period_start
        return month_key(end) if end else ""

    def months(self) -> list:
        return sorted({month_key(l.date) for l in self.lines})

    # ------------------------------------------------------------ tie-out
    def check(self) -> TieOutResult:
        """Run the tie-out, store it on the statement, and return it."""
        self.tie_out = tie_out(self)
        return self.tie_out

    @property
    def ties(self) -> bool:
        return bool(self.tie_out and self.tie_out.ties)

    def describe(self) -> str:
        head = (
            f"{os.path.basename(self.source_file) or '(memory)'} "
            f"[{self.parser}] {self.month}: {len(self.lines)} rows"
        )
        if self.tie_out:
            head += "  " + self.tie_out.describe()
        for note in self.notes:
            head += f"\n    note: {note}"
        return head

    def __str__(self) -> str:
        return self.describe()


# --------------------------------------------------------------------- PDF

def have_pdftotext() -> bool:
    try:
        subprocess.run(["pdftotext", "-v"], capture_output=True, check=False)
        return True
    except (OSError, FileNotFoundError):
        return False


def pdf_text(path, mode="-layout") -> str:
    """Extract text with the `pdftotext` binary. No Python PDF library, no network.

    `mode` is "-layout" (column positions preserved) or "-raw" (glyph order).
    Both are needed: see the comment in `mercury_checking.py` about PDFs whose
    embedded font declares broken glyph widths, where -layout returns scrambled
    fragments or nothing at all and -raw is the only way to recover the file.
    """
    try:
        proc = subprocess.run(
            ["pdftotext", mode, str(path), "-"],
            capture_output=True, text=True, check=False,
        )
    except FileNotFoundError as exc:
        raise ParseError(
            "pdftotext is not installed. It is part of poppler-utils "
            "(macOS: brew install poppler, Debian/Ubuntu: apt install poppler-utils). "
            "This engine shells out to it rather than depending on a Python PDF library."
        ) from exc
    if proc.returncode != 0:
        raise ParseError(
            f"pdftotext {mode} failed on {os.path.basename(str(path))} "
            f"(exit {proc.returncode}): {(proc.stderr or '').strip()[:200]}"
        )
    return proc.stdout
