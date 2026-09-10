"""QuickBooks Online bank-transaction CSV upload files.

What this is for: a bank feed that is dead, was never connected, or predates
the roughly 90 days QuickBooks pulls when you reconnect it. QuickBooks lets you
upload the missing window as a CSV, and the uploaded rows land in the same "For
Review" queue a live feed writes into. Nothing here posts anything. A person
uploads the file and accepts the rows themselves.

Two layouts, both documented by Intuit:

    three column   Date, Description, Amount        (one signed amount)
    four column    Date, Description, Credit, Debit (money in, money out)

We default to three column because one signed number cannot be transposed, and
a Credit/Debit pair can: get those two the wrong way round and every deposit in
the file becomes a payment. `BankLine.amount` is already signed money in
positive, so the three column layout is a straight copy.

LIMITS (reference/qbo-import-formats.md holds the citation)
    verified-on: 2026-09-09   VERIFIED: no
    At most 1,000 transactions per file, and the file must be under 350 KB.
    Both numbers come from Intuit's upload help and neither has been confirmed
    here against a live upload. They are enforced as hard refusals rather than
    warnings: a file that QuickBooks rejects halfway through leaves a partial
    import behind, which is worse than not uploading at all. Use
    `split_for_upload` first.

SECURITY
    A bank descriptor is attacker chosen text. Anyone who can send the company
    money picks the memo that arrives with it. A descriptor of `=cmd|...` lands
    in a spreadsheet as a live formula, so every string cell goes through
    `util.csv_safe`. The Date and Amount columns are the exception and are
    deliberately NOT passed through it: this engine formats them itself from a
    `datetime.date` and a `Decimal`, they are never attacker text, and the
    apostrophe `csv_safe` prefixes onto a leading `-` would turn every negative
    amount into a string QuickBooks cannot read.
"""

from __future__ import annotations

import csv
import io

from .util import ZERO, csv_safe, money, parse_date, plain

QBO_DATE = "%m/%d/%Y"

# See LIMITS above. verified-on: 2026-09-09  VERIFIED: no
MAX_ROWS = 1000
MAX_BYTES = 350_000

THREE_COLUMN_HEADER = ("Date", "Description", "Amount")
FOUR_COLUMN_HEADER = ("Date", "Description", "Credit", "Debit")

# Only the free text column is attacker influenced. See SECURITY above.
SAFE_COLUMNS = ("Description",)

LAYOUTS = ("three", "four")


class UploadTooLarge(ValueError):
    """The file would exceed what QuickBooks accepts. Split it first."""


class BankLineError(ValueError):
    """A line cannot be expressed in a QuickBooks upload file."""


# ------------------------------------------------------------------ rows

def _date(line) -> str:
    d = parse_date(getattr(line, "date", None), field="bank line date")
    return d.strftime(QBO_DATE)


def _descriptor(line) -> str:
    text = (getattr(line, "descriptor", "") or "").strip()
    if not text:
        raise BankLineError(
            f"bank line dated {getattr(line, 'date', '?')} for "
            f"{getattr(line, 'amount', '?')} has a blank description. QuickBooks "
            f"rejects a row with no description; give it one before writing."
        )
    return text


def _amount(line):
    amt = money(getattr(line, "amount", ZERO), "bank amount")
    if amt == ZERO:
        raise BankLineError(
            f"bank line dated {getattr(line, 'date', '?')} has a zero amount. "
            f"QuickBooks rejects a zero row; drop it or give it its real amount."
        )
    return amt


def three_column_rows(lines) -> list:
    """Date, Description, Amount. Money in positive, money out negative."""
    return [[_date(l), _descriptor(l), plain(_amount(l))] for l in lines]


def four_column_rows(lines) -> list:
    """Date, Description, Credit, Debit.

    Credit is money into the account (a deposit), Debit is money out (a
    payment). Exactly one of the two carries a magnitude and the other is
    blank, which is what the QuickBooks parser expects.
    """
    rows = []
    for l in lines:
        amt = _amount(l)
        credit = plain(amt) if amt > ZERO else ""
        debit = plain(-amt) if amt < ZERO else ""
        rows.append([_date(l), _descriptor(l), credit, debit])
    return rows


# --------------------------------------------------------------- rendering

def render(header, rows) -> str:
    """Exactly the bytes `util.write_csv` would produce for this input.

    Kept as its own function so a file can be measured before it is written.
    `tests/test_writers.py` asserts this stays byte identical to `write_csv`;
    if that test fails, this function has drifted and the size guard is lying.
    """
    buf = io.StringIO(newline="")
    w = csv.writer(buf, quoting=csv.QUOTE_MINIMAL, lineterminator="\r\n")
    w.writerow(list(header))
    for row in rows:
        out = []
        for col, val in zip(header, row):
            out.append(csv_safe(val) if col in SAFE_COLUMNS else ("" if val is None else str(val)))
        w.writerow(out)
    return buf.getvalue()


def _write(path, header, rows) -> int:
    if len(rows) > MAX_ROWS:
        raise UploadTooLarge(
            f"{len(rows)} transactions is more than the {MAX_ROWS} QuickBooks "
            f"accepts in one upload. Call split_for_upload(lines) and write each "
            f"chunk to its own file."
        )
    text = render(header, rows)
    size = len(text.encode("utf-8"))
    if size > MAX_BYTES:
        raise UploadTooLarge(
            f"{size} bytes is over the {MAX_BYTES} byte limit QuickBooks accepts "
            f"in one upload, with {len(rows)} transactions. Call "
            f"split_for_upload(lines, max_bytes={MAX_BYTES}) and write each chunk "
            f"to its own file."
        )
    with open(path, "w", newline="", encoding="utf-8") as fh:
        fh.write(text)
    return len(rows)


def write_three_column(path, lines) -> int:
    """Write the Date, Description, Amount layout. Returns rows written."""
    return _write(path, THREE_COLUMN_HEADER, three_column_rows(lines))


def write_four_column(path, lines) -> int:
    """Write the Date, Description, Credit, Debit layout. Returns rows written."""
    return _write(path, FOUR_COLUMN_HEADER, four_column_rows(lines))


def write(path, lines, layout: str = "three") -> int:
    if layout not in LAYOUTS:
        raise ValueError(f"unknown layout {layout!r}, expected one of {LAYOUTS}")
    if layout == "three":
        return write_three_column(path, lines)
    return write_four_column(path, lines)


# ---------------------------------------------------------------- splitting

def split_for_upload(lines, max_rows: int = MAX_ROWS, max_bytes: int = MAX_BYTES,
                     layout: str = "three") -> list:
    """Chop a list of lines into chunks QuickBooks will accept.

    Splits on both limits, because 1,000 rows of long descriptors can pass the
    row count and still fail the size check. Order is preserved and no line is
    dropped: the concatenation of the chunks is the input.
    """
    if layout not in LAYOUTS:
        raise ValueError(f"unknown layout {layout!r}, expected one of {LAYOUTS}")
    if max_rows < 1:
        raise ValueError("max_rows must be at least 1")
    header = THREE_COLUMN_HEADER if layout == "three" else FOUR_COLUMN_HEADER
    to_rows = three_column_rows if layout == "three" else four_column_rows

    header_bytes = len(render(header, []).encode("utf-8"))
    chunks, current, current_bytes = [], [], header_bytes
    for line in lines:
        row_bytes = len(render(header, to_rows([line])).encode("utf-8")) - header_bytes
        too_many = len(current) >= max_rows
        too_big = current and (current_bytes + row_bytes) > max_bytes
        if too_many or too_big:
            chunks.append(current)
            current, current_bytes = [], header_bytes
        current.append(line)
        current_bytes += row_bytes
    if current:
        chunks.append(current)
    return chunks
