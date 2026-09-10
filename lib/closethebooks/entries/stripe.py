"""The month-end payment-processor entry, drafted from the processor's own balance report.

    from closethebooks.entries import stripe
    drafted = stripe.build(profile, months, through="2026-06-30")
    drafted.residuals      # what is left sitting in the clearing account

WHY A CLEARING ACCOUNT AT ALL

Because the money a customer pays and the money that reaches the bank are two
different numbers on two different dates. The processor takes its fee, holds
refunds and chargebacks against the balance, and pays out on its own schedule.
Booking the bank deposit as revenue is the single most common revenue error in
a startup's books: it understates revenue by the fees, hides refunds entirely,
and moves income into whichever month the payout landed.

    DR  Stripe clearing            gross charges
        CR  Revenue                    gross charges
    DR  Processor fees             fees
        CR  Stripe clearing             fees
    DR  Refunds (contra revenue)   refunds
        CR  Stripe clearing             refunds
    DR  Operating bank             payouts
        CR  Stripe clearing             payouts

WHAT THE RESIDUAL MEANS

After that entry the clearing account holds exactly the money the processor is
still sitting on: charges captured but not yet paid out. That balance is real
and is expected to be small and to clear within days. What it must never do is
GROW month over month, which is what happens when a fee category is missing from
the entry or a payout was also booked somewhere else. The residual is exposed
per month, and the cumulative residual is the number the exit test reads.

Where the processor's own report states its ending balance, `difference` is
`computed - stated`, and it is stated even when it is zero. A month with no
stated balance says so rather than reading as a tie.

PROFILE

    "recurring_entries": {
      "stripe": {
        "source": "Stripe balance report, monthly summary",
        "accounts": {"clearing": "1250", "revenue": "4000", "fees": "6400",
                     "refunds": "4100", "bank": "1000"},
        "class": "Product"
      }
    }

MONTHS  (the processor's own figures, one dict per month)

    {"month": "2026-04", "gross": "50000.00", "fees": "1500.00",
     "refunds": "800.00", "payouts": "46000.00",
     "opening_balance": "0.00", "stated_closing": "1700.00",
     "source": "Stripe balance report 2026-04-01 to 2026-04-30"}
"""

from __future__ import annotations

from ..util import ZERO, fmt, money, month_key, parse_date
from . import Drafted, DraftError, _entry, basis_of, last_day, need, section

__all__ = ["build", "BATCH_TAG"]

BATCH_TAG = "stripe"


def _month_of(row, where) -> str:
    value = row.get("month")
    if value:
        text = str(value).strip()
        if len(text) == 7 and text[4] == "-":
            return text
        return month_key(parse_date(text, field=f"{where} month"))
    return month_key(parse_date(need(row, "date", where), field=f"{where} date"))


def build(profile, payouts, through=None, *, batch_tag=BATCH_TAG) -> Drafted:
    """One entry per month of processor activity, through `through` if given.

    `payouts` is the processor's own monthly summary, not a transaction list.
    The engine deliberately does not re-derive gross from a charge export: the
    processor's balance report is the authority on its own balance, and a
    re-derivation that disagrees with it is a finding to report, not a number to
    quietly prefer.
    """
    config = section(profile, "stripe")
    accounts = need(config, "accounts", "recurring_entries.stripe")
    clearing = need(accounts, "clearing", "recurring_entries.stripe.accounts")
    revenue = need(accounts, "revenue", "recurring_entries.stripe.accounts")
    fees_account = accounts.get("fees")
    refunds_account = accounts.get("refunds")
    bank = need(accounts, "bank", "recurring_entries.stripe.accounts")
    default_class = str(config.get("class") or "").strip()
    fallback_basis = str(config.get("source") or "").strip()

    through = parse_date(through, field="through") if through else None
    through_month = month_key(through) if through else None

    entries, residuals, notes = [], [], []
    running = None
    for i, row in enumerate(sorted(payouts or [], key=lambda r: _month_of(r, "processor month")), start=1):
        where = f"processor month {i}"
        month = _month_of(row, where)
        if through_month and month > through_month:
            continue
        basis = basis_of(row, fallback_basis, where)
        klass = str(row.get("class") or default_class or "").strip()

        gross = money(need(row, "gross", where), f"{where} gross")
        fees = money(row.get("fees", ZERO), f"{where} fees")
        refunds = money(row.get("refunds", ZERO), f"{where} refunds")
        paid_out = money(row.get("payouts", row.get("net_deposits", ZERO)), f"{where} payouts")

        for label, amount in (("gross", gross), ("fees", fees), ("refunds", refunds), ("payouts", paid_out)):
            if amount < ZERO:
                raise DraftError(
                    f"{where}: {label} is {fmt(amount)}. Every figure here is a positive magnitude; "
                    f"the sides are decided by the entry, not by the sign the report happened to use."
                )
        if fees != ZERO and not fees_account:
            raise DraftError(f"{where}: fees of {fmt(fees)} with no recurring_entries.stripe.accounts.fees declared")
        if refunds != ZERO and not refunds_account:
            raise DraftError(
                f"{where}: refunds of {fmt(refunds)} with no recurring_entries.stripe.accounts.refunds "
                f"declared. A refund is not a negative sale and does not belong netted into revenue; "
                f"it goes to its own contra-revenue account so gross and refunds are both visible."
            )

        y, m = int(month[:4]), int(month[5:7])
        date = parse_date(row["date"], field=f"{where} date") if row.get("date") else last_day(y, m)
        if through and date > through:
            date = through

        memo = f"Payment processor activity {month}"
        lines = [
            (clearing, gross, ZERO, f"{memo}: gross charges", "", klass),
            (revenue, ZERO, gross, f"{memo}: gross charges", "", klass),
        ]
        if fees != ZERO:
            lines += [
                (fees_account, fees, ZERO, f"{memo}: processor fees", "", klass),
                (clearing, ZERO, fees, f"{memo}: processor fees", "", klass),
            ]
        if refunds != ZERO:
            lines += [
                (refunds_account, refunds, ZERO, f"{memo}: refunds", "", klass),
                (clearing, ZERO, refunds, f"{memo}: refunds", "", klass),
            ]
        if paid_out != ZERO:
            lines += [
                (bank, paid_out, ZERO, f"{memo}: payout to bank", "", klass),
                (clearing, ZERO, paid_out, f"{memo}: payout to bank", "", klass),
            ]

        entries.append(_entry(
            date=date,
            number=f"ST-{month.replace('-', '')}",
            lines=lines,
            memo=memo,
            kind="stripe",
            basis=basis,
            batch_tag=batch_tag,
        ))

        opening = money(row["opening_balance"], f"{where} opening_balance") \
            if row.get("opening_balance") not in (None, "") else (running if running is not None else ZERO)
        if running is not None and row.get("opening_balance") not in (None, "") and money(row["opening_balance"]) != running:
            notes.append(
                f"{month}: the report opens the clearing account at {fmt(money(row['opening_balance']))} but "
                f"the prior month closed at {fmt(running)}, a break of "
                f"{fmt(money(money(row['opening_balance']) - running))}. A month is missing or restated."
            )
        computed = money(opening + gross - fees - refunds - paid_out)
        stated = money(row["stated_closing"], f"{where} stated_closing") \
            if row.get("stated_closing") not in (None, "") else None
        residuals.append({
            "month": month,
            "clearing_account": clearing,
            "opening": opening,
            "gross": gross,
            "fees": fees,
            "refunds": refunds,
            "payouts": paid_out,
            "computed_closing": computed,
            "stated_closing": stated,
            "difference": (money(computed - stated) if stated is not None else None),
            "checkable": stated is not None,
            "note": "" if stated is not None else "the report stated no ending balance, so this month is unchecked",
            "basis": basis,
        })
        running = computed

    final = residuals[-1]["computed_closing"] if residuals else ZERO
    if final != ZERO:
        notes.append(
            f"The clearing account does not return to 0.00: {fmt(final)} is left at "
            f"{residuals[-1]['month'] if residuals else '?'}. That is money the processor is still "
            f"holding, or an entry that is missing. Do not write it off; find it."
        )
    drafted = Drafted(entries, kind="stripe", residuals=residuals, notes=notes)
    drafted.clearing_residual = final
    return drafted
