"""Statement parsers: one registry, one dispatcher, one return type.

    from closethebooks.statements import parse, detect_parser, PARSERS

    statement = parse("acme-checking-2026-04.pdf", account_key="checking-1234")
    print(statement.tie_out.describe())

Every parser returns the same `StatementFile`, whose `lines` are `BankLine`s
signed from the account holder's point of view (money in positive, money out
negative, on a bank account and on a card alike) and whose `tie_out` has
already been run.

Adding a parser: write a module with `parse(path, account_key="", **kwargs)`
and `detect(path)`, add it to `PARSERS`, and add it to `_DETECT_ORDER` ahead of
anything more general than itself. `generic_csv` is deliberately last: it is
the fallback that reads almost anything with a date column and an amount.
"""

from __future__ import annotations

import os

from ..tieout import TieOutResult, chain_breaks, tie_out, tie_out_month
from . import brex_csv, generic_csv, mercury_card, mercury_checking
from .base import ParseError, StatementFile, have_pdftotext, pdf_text

__all__ = [
    "PARSERS", "parse", "detect_parser", "parse_many",
    "StatementFile", "ParseError", "TieOutResult",
    "tie_out", "tie_out_month", "chain_breaks",
    "have_pdftotext", "pdf_text",
]

PARSERS = {
    "mercury_checking": mercury_checking.parse,
    "mercury_card": mercury_card.parse,
    "brex_csv": brex_csv.parse,
    "generic_csv": generic_csv.parse,
}

# Most specific first. A detector may open the file; none of them may guess.
_DETECT_ORDER = (
    ("mercury_card", mercury_card.detect),
    ("mercury_checking", mercury_checking.detect),
    ("brex_csv", brex_csv.detect),
    ("generic_csv", generic_csv.detect),
)

# File-name hints, used ONLY when the face text is unreadable (an image-only
# scan, say). A name is not evidence of a format, so this never overrides a
# detector that actually read the file.
_NAME_HINTS = (
    (("mercury", "credit"), "mercury_card"),
    (("mercury", "io"), "mercury_card"),
    (("mercury", "card"), "mercury_card"),
    (("mercury",), "mercury_checking"),
    (("brex",), "brex_csv"),
)


def detect_parser(path):
    """Which parser reads this file, or None.

    Sniffs in this order: the header row for a CSV, the PDF's face text for a
    PDF, and only then the file name. Returns a key of `PARSERS`.
    """
    path = str(path)
    if not os.path.exists(path):
        raise ParseError(f"{path}: no such file")

    for name, detector in _DETECT_ORDER:
        try:
            if detector(path):
                return name
        except (ParseError, OSError):
            continue

    stem = os.path.basename(path).lower()
    extension = os.path.splitext(stem)[1]
    for tokens, name in _NAME_HINTS:
        if all(token in stem for token in tokens):
            if extension == ".pdf" and name.startswith("mercury"):
                return name
            if extension in (".csv", ".tsv") and name == "brex_csv":
                return name
    return None


def parse(path, parser=None, account_key="", **kwargs) -> StatementFile:
    """Parse one statement file. `parser` forces a parser; otherwise it is sniffed."""
    path = str(path)
    if parser is None:
        parser = detect_parser(path)
    if parser is None:
        raise ParseError(
            f"{os.path.basename(path)}: no parser recognized this file. Tried "
            f"{', '.join(name for name, _ in _DETECT_ORDER)}. Pass parser= to force one, "
            f"or export a CSV: generic_csv reads any export with a date column and "
            f"either an Amount column or a Debit/Credit pair."
        )
    if parser not in PARSERS:
        raise ParseError(
            f"unknown parser {parser!r}. Known parsers: {', '.join(sorted(PARSERS))}"
        )
    return PARSERS[parser](path, account_key, **kwargs)


def infer_account_key(path) -> str:
    """Guess which account a statement belongs to, from its file name.

    Statements arrive named for their account far more often than not
    ("acme-checking-7742-statement-2025-03.csv"), and the mask is the most
    reliable part. `coverage.account_token` already knows how to find it, so
    this reuses that rather than inventing a second convention.

    Returns "" when nothing can be inferred, which callers must treat as
    unknown rather than as a group.
    """
    try:
        from .. import coverage
    except Exception:
        return ""
    name = os.path.basename(str(path))
    stem = os.path.splitext(name)[0]
    try:
        parsed = coverage.parse_statement_name(name, str(path))
        if parsed and parsed[1] and parsed[1] != "unknown":
            return parsed[1]
        token = coverage.account_token(stem, str(path))
        return "" if token == "unknown" else token
    except Exception:
        return ""


def parse_many(paths, account_key="", *, infer_keys=True, **kwargs):
    """Parse several files. Returns (statements, failures) and never raises.

    A close runs over a directory of statements where one bad file must not
    stop the other eleven. Failures come back as (path, message) pairs so the
    caller can report them rather than lose them.

    **When no `account_key` is given, one is inferred per file from its name.**
    Without this, every statement in a directory lands in one nameless group,
    and `tieout.chain_breaks` then compares the closing balance of a checking
    account against the opening balance of a card. On a real directory of 41
    statements that produced 40 reported breaks where one was real: a wall of
    false alarms, which is worse than silence because it teaches the reader to
    ignore the check. Pass `infer_keys=False` to opt out.
    """
    statements, failures = [], []
    for path in paths:
        key = account_key
        if not key and infer_keys:
            key = infer_account_key(path)
        try:
            statements.append(parse(path, account_key=key, **kwargs))
        except (ParseError, ValueError, OSError) as exc:
            failures.append((str(path), str(exc)))
    return statements, failures
