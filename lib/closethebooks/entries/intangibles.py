"""Amortization of an intangible asset, against accumulated amortization.

Same straight-line schedule as `prepaid`, one difference that matters: the
credit goes to a contra-asset account, not to the asset itself.

    DR  Amortization expense          416.67
        CR  Accumulated amortization      416.67

The asset stays on the books at cost and the accumulated account grows beside
it. Crediting the asset directly destroys the cost figure, and cost is what a
tax return, a purchase-price allocation and an impairment test all need years
later. This module refuses to draft without an `accumulated_account` for exactly
that reason.

PROFILE

    "recurring_entries": {
      "intangibles": [
        {"asset": "Acquired customer list", "cost": "60000.00",
         "in_service": "2026-01-01", "life_months": 60,
         "asset_account": "1700", "accumulated_account": "1710",
         "expense_account": "6800", "class": "General",
         "source": "Asset purchase agreement dated 2025-12-20, schedule 2.1"}
      ]
    }

`remaining` reports cost, accumulated amortization and net book value per asset,
so the three tie to the balance sheet as a group rather than one net number
nobody can check.
"""

from __future__ import annotations

from ..util import ZERO, fmt, money, month_key, parse_date
from . import (
    Drafted, DraftError, _entry, basis_of, need, section, straight_line_schedule,
)

__all__ = ["build", "BATCH_TAG"]

BATCH_TAG = "intangibles"


def build(profile, through, *, since=None, batch_tag=BATCH_TAG) -> Drafted:
    """One entry per asset per month from in-service date through `through`."""
    assets = section(profile, "intangibles")
    through = parse_date(through, field="through")
    since = parse_date(since, field="since") if since else None
    through_month = month_key(through)
    since_month = month_key(since) if since else None

    entries, remaining, notes = [], [], []
    for i, asset in enumerate(assets, start=1):
        where = f"recurring_entries.intangibles[{i - 1}]"
        label = str(need(asset, "asset", where)).strip()
        cost = money(need(asset, "cost", where), f"{where} cost")
        life = int(need(asset, "life_months", where))
        in_service = parse_date(need(asset, "in_service", where), field=f"{where} in_service")
        expense_account = need(asset, "expense_account", where)
        accumulated_account = asset.get("accumulated_account")
        if not accumulated_account:
            raise DraftError(
                f"{where} ({label}): no accumulated_account. Amortization credits a contra-asset "
                f"account so the asset stays on the books at cost; crediting the asset itself "
                f"destroys the cost figure a return or a sale will need years from now."
            )
        basis = basis_of(asset, "", f"{where} ({label})")
        klass = str(asset.get("class") or "").strip()

        schedule = straight_line_schedule(cost, in_service, life)
        accumulated = ZERO
        for row in schedule:
            if row["month"] > through_month:
                continue
            accumulated = money(accumulated + row["amount"])
            if since_month and row["month"] < since_month:
                continue
            memo = (
                f"Amortize {label}, month {row['index']} of {len(schedule)}"
                + (f" ({row['days']} of {row['days_in_month']} days)" if row["partial"] else "")
            )
            entries.append(_entry(
                date=row["date"],
                number=f"IA-{row['month'].replace('-', '')}-{i:02d}",
                lines=[
                    (expense_account, row["amount"], ZERO, memo, label, klass),
                    (accumulated_account, ZERO, row["amount"], memo, label, klass),
                ],
                memo=memo,
                kind="intangibles",
                basis=basis,
                batch_tag=batch_tag,
            ))

        remaining.append({
            "asset": label,
            "asset_account": asset.get("asset_account", ""),
            "accumulated_account": accumulated_account,
            "cost": cost,
            "life_months": life,
            "in_service": in_service,
            "accumulated_through": accumulated,
            "net_book_value": money(cost - accumulated),
            "through": through,
            "fully_amortized": money(cost - accumulated) == ZERO,
            "basis": basis,
        })

    net = money(sum((r["net_book_value"] for r in remaining), ZERO))
    notes.append(
        f"Net book value of {len(remaining)} intangible(s) at {through.isoformat()}: {fmt(net)}. "
        f"Cost and accumulated amortization tie separately; a single net figure hides both."
    )
    drafted = Drafted(entries, kind="intangibles", remaining=remaining, notes=notes)
    drafted.total_net_book_value = net
    return drafted
