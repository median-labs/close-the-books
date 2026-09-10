"""Prepaid expense amortization, straight line, one entry per contract per month.

    from closethebooks.entries import prepaid
    drafted = prepaid.build(profile, through="2026-06-30")
    drafted.remaining        # what is still on the balance sheet, per contract

    DR  Insurance expense       1,000.00
        CR  Prepaid expenses        1,000.00

PROFILE

    "recurring_entries": {
      "prepaid": [
        {"vendor": "Northwind Insurance", "total": "12000.00",
         "start": "2026-03-15", "term_months": 12,
         "prepaid_account": "1400", "expense_account": "6300",
         "class": "Operations",
         "source": "Northwind policy NW-88213, invoice dated 2026-03-10"}
      ]
    }

Either `total` (the whole contract) or `monthly` (the per-month charge) may be
given. With `monthly` alone the total is `monthly * term_months`.

WHY ONE ENTRY PER CONTRACT RATHER THAN ONE COMBINED MONTHLY ENTRY

Because the basis differs per contract. A combined entry would carry one basis
string covering four unrelated documents, and when one of the four turns out to
be wrong there is no way to reverse just that part. Separate entries cost more
rows and are worth it.

WHAT `remaining` IS FOR

The unamortized balance per contract as of `through`. Their sum is what the
prepaid account should read on the balance sheet at that date. If it does not,
either an entry is missing or something was booked to prepaid that no contract
here explains, and both of those are found by comparing these two numbers.
`build` never adjusts anything to make them agree.
"""

from __future__ import annotations

from ..util import ZERO, fmt, money, month_key, parse_date
from . import (
    Drafted, DraftError, _entry, basis_of, need, section, straight_line_schedule,
)

__all__ = ["build", "BATCH_TAG"]

BATCH_TAG = "prepaid"


def _contract_total(contract, where):
    total = contract.get("total")
    monthly = contract.get("monthly")
    term = int(need(contract, "term_months", where))
    if total in (None, ""):
        if monthly in (None, ""):
            raise DraftError(f"{where}: needs either \"total\" or \"monthly\"")
        monthly = money(monthly, f"{where} monthly")
        return money(monthly * term), monthly, term
    total = money(total, f"{where} total")
    return total, (money(monthly, f"{where} monthly") if monthly not in (None, "") else None), term


def build(profile, through, *, since=None, batch_tag=BATCH_TAG) -> Drafted:
    """One entry per contract per month from the contract start through `through`.

    `since` skips months already posted, so a monthly run does not redraft the
    whole history. The schedule is always computed over the WHOLE contract life
    first and only then filtered, because the final month carries the rounding
    remainder and a schedule computed over a window would put that remainder in
    the wrong month.
    """
    contracts = section(profile, "prepaid")
    through = parse_date(through, field="through")
    since = parse_date(since, field="since") if since else None
    through_month = month_key(through)
    since_month = month_key(since) if since else None

    entries, remaining, notes = [], [], []
    for i, contract in enumerate(contracts, start=1):
        where = f"recurring_entries.prepaid[{i - 1}]"
        vendor = str(need(contract, "vendor", where)).strip()
        prepaid_account = need(contract, "prepaid_account", where)
        expense_account = need(contract, "expense_account", where)
        basis = basis_of(contract, "", f"{where} ({vendor})")
        klass = str(contract.get("class") or "").strip()
        start = parse_date(need(contract, "start", where), field=f"{where} start")
        total, monthly, term = _contract_total(contract, where)

        schedule = straight_line_schedule(total, start, term, monthly=monthly)
        term_end = schedule[-1]["date"] if schedule else start

        amortized_through = ZERO
        for row in schedule:
            if row["month"] > through_month:
                continue
            amortized_through = money(amortized_through + row["amount"])
            if since_month and row["month"] < since_month:
                continue
            memo = (
                f"Amortize {vendor} prepaid, month {row['index']} of {len(schedule)}"
                + (f" ({row['days']} of {row['days_in_month']} days)" if row["partial"] else "")
            )
            entries.append(_entry(
                date=row["date"],
                number=f"PP-{row['month'].replace('-', '')}-{i:02d}",
                lines=[
                    (expense_account, row["amount"], ZERO, memo, vendor, klass),
                    (prepaid_account, ZERO, row["amount"], memo, vendor, klass),
                ],
                memo=memo,
                kind="prepaid",
                basis=basis,
                batch_tag=batch_tag,
            ))

        left = money(total - amortized_through)
        remaining.append({
            "vendor": vendor,
            "prepaid_account": prepaid_account,
            "expense_account": expense_account,
            "total": total,
            "start": start,
            "term_months": term,
            "term_end": term_end,
            "amortized_through": amortized_through,
            "remaining": left,
            "through": through,
            "fully_amortized": left == ZERO,
            "basis": basis,
        })
        if term_end <= through and left != ZERO:      # cannot happen; say so if it does
            notes.append(
                f"{vendor}: term ended {term_end.isoformat()} but {fmt(left)} is still "
                f"unamortized. The schedule and the total disagree; do not post this batch."
            )

    total_remaining = money(sum((r["remaining"] for r in remaining), ZERO))
    notes.append(
        f"Unamortized prepaid at {through.isoformat()} across {len(remaining)} contract(s): "
        f"{fmt(total_remaining)}. Tie this to the prepaid account on the balance sheet."
    )
    drafted = Drafted(entries, kind="prepaid", remaining=remaining, notes=notes)
    drafted.total_remaining = total_remaining
    return drafted
