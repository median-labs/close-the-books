"""The payroll accrual entry, drafted from the provider's own summary.

    from closethebooks.entries import payroll
    drafted = payroll.build(profile, periods)

WHAT ONE ENTRY LOOKS LIKE

    DR  Wages and salaries            gross wages
    DR  Employer payroll taxes        the employer's own share
    DR  Employee benefits             the employer's own share
        CR  Operating bank                net pay
        CR  Employee tax withheld           |
        CR  Employee benefit deductions     |  one line per withholding
        CR  401(k) employee deferral        |
        CR  Payroll taxes payable        employer share, until it is remitted
        CR  Benefits payable             employer share, until it is remitted

THE DEFECT THIS MODULE EXISTS TO PREVENT

Withholdings netted into an expense account. It happens because the payroll
provider debits one number from the bank and a bookkeeper books one number to
"Payroll expenses", and the money withheld from employees, which is a LIABILITY
of the company until it is remitted, disappears into the expense line. The books
still balance. The P&L is wrong by the withheld amount, the balance sheet is
missing a liability, and the error compounds every pay period.

So this module will not net. Every withholding named in a period gets its own
credit line to its own declared liability account, and a withholding with no
declared account is a refusal, not a silent merge into benefits.

THE IDENTITY IT CHECKS

    gross wages == net pay + every withholding

That is arithmetic, not opinion: net pay IS gross less what was withheld. When
it does not hold, the summary named a withholding it did not itemise, and this
module raises with all three numbers rather than plugging the difference into
the nearest account. The plug is what makes it invisible.

PROFILE

    "recurring_entries": {
      "payroll": {
        "source": "Payroll provider payroll journal, per pay date",
        "accounts": {
          "wages": "6000",
          "employer_taxes": "6010",
          "benefits": "6020",
          "bank": "1000",
          "employer_tax_liability": "2120",     # optional; else paid from bank
          "benefits_liability": "2130",         # optional; else paid from bank
          "withholding": {
            "employee_income_tax": "2100",
            "employee_fica": "2105",
            "employee_401k": "2110"
          }
        },
        "class": "Operations"
      }
    }

PERIODS  (one dict per pay date; the provider's own summary figures)

    {"pay_date": "2026-04-15", "gross_wages": "40000.00",
     "employer_taxes": "3060.00", "benefits": "1200.00",
     "net_pay": "28000.00",
     "withholdings": {"employee_income_tax": "8000.00", "employee_fica": "3060.00",
                      "employee_401k": "940.00"},
     "source": "Payroll register 2026-04-15", "class": "Operations"}
"""

from __future__ import annotations

from ..util import ZERO, fmt, money, parse_date
from . import Drafted, DraftError, _entry, basis_of, need, section

__all__ = ["build", "BATCH_TAG"]

BATCH_TAG = "payroll"


def build(profile, periods, *, batch_tag=BATCH_TAG) -> Drafted:
    """One `ProposedEntry` per pay date. Raises rather than netting or plugging."""
    config = section(profile, "payroll")
    accounts = need(config, "accounts", "recurring_entries.payroll")
    withholding_accounts = accounts.get("withholding") or {}
    default_class = str(config.get("class") or "").strip()
    fallback_basis = str(config.get("source") or "").strip()

    wages_account = need(accounts, "wages", "recurring_entries.payroll.accounts")
    bank_account = need(accounts, "bank", "recurring_entries.payroll.accounts")
    employer_tax_expense = accounts.get("employer_taxes")
    benefits_expense = accounts.get("benefits")
    employer_tax_liability = accounts.get("employer_tax_liability")
    benefits_liability = accounts.get("benefits_liability")

    entries, notes = [], []
    for i, period in enumerate(periods or [], start=1):
        where = f"payroll period {i}"
        pay_date = parse_date(need(period, "pay_date", where), field=f"{where} pay_date")
        basis = basis_of(period, fallback_basis, where)
        klass = str(period.get("class") or default_class or "").strip()
        name = str(period.get("name") or "").strip()

        gross = money(need(period, "gross_wages", where), f"{where} gross_wages")
        net = money(need(period, "net_pay", where), f"{where} net_pay")
        employer_taxes = money(period.get("employer_taxes", ZERO), f"{where} employer_taxes")
        benefits = money(period.get("benefits", ZERO), f"{where} benefits")
        withholdings = period.get("withholdings") or {}

        if gross <= ZERO:
            raise DraftError(f"{where}: gross_wages is {fmt(gross)}. A pay date with no wages is not a pay date.")
        if not withholdings and net != gross:
            raise DraftError(
                f"{where}: net pay {fmt(net)} is less than gross {fmt(gross)} but the period "
                f"names no withholdings. The difference of {fmt(gross - net)} was withheld from "
                f"employees and is a liability of the company; itemise it under \"withholdings\"."
            )

        withheld_total = ZERO
        withholding_lines = []
        for kind, amount in withholdings.items():
            amount = money(amount, f"{where} withholding {kind}")
            if amount == ZERO:
                continue
            if amount < ZERO:
                raise DraftError(f"{where}: withholding {kind!r} is {fmt(amount)}; withholdings are positive amounts")
            account = withholding_accounts.get(kind)
            if not account:
                raise DraftError(
                    f"{where}: withholding {kind!r} of {fmt(amount)} has no liability account. "
                    f"Declare recurring_entries.payroll.accounts.withholding.{kind}. "
                    f"Money withheld from an employee is owed to somebody else; it is never an expense, "
                    f"and netting it into a benefits or wages account is the defect this refusal exists to stop."
                )
            withheld_total = money(withheld_total + amount)
            withholding_lines.append((account, ZERO, amount, f"{kind} withheld {pay_date.isoformat()}", name, klass))

        residual = money(gross - net - withheld_total)
        if residual != ZERO:
            raise DraftError(
                f"{where}: gross {fmt(gross)} does not equal net {fmt(net)} plus withholdings "
                f"{fmt(withheld_total)}. The difference is {fmt(residual)}. Something was withheld "
                f"that the summary did not name. Report it as a number and find it; it is not "
                f"posted to a convenient account to make the entry balance."
            )

        lines = [(wages_account, gross, ZERO, f"Gross wages {pay_date.isoformat()}", name, klass)]

        if employer_taxes != ZERO:
            if not employer_tax_expense:
                raise DraftError(
                    f"{where}: employer_taxes of {fmt(employer_taxes)} with no "
                    f"recurring_entries.payroll.accounts.employer_taxes expense account declared."
                )
            lines.append((employer_tax_expense, employer_taxes, ZERO,
                          f"Employer payroll taxes {pay_date.isoformat()}", name, klass))
            credit_to = employer_tax_liability or bank_account
            lines.append((credit_to, ZERO, employer_taxes,
                          "Employer payroll taxes " + ("payable" if employer_tax_liability else "paid"),
                          name, klass))
            if not employer_tax_liability:
                notes.append(
                    f"{pay_date.isoformat()}: employer taxes credited straight to the bank because no "
                    f"employer_tax_liability account is declared. That is right only if the provider "
                    f"debited them on the pay date."
                )

        if benefits != ZERO:
            if not benefits_expense:
                raise DraftError(
                    f"{where}: benefits of {fmt(benefits)} with no "
                    f"recurring_entries.payroll.accounts.benefits expense account declared."
                )
            lines.append((benefits_expense, benefits, ZERO,
                          f"Employer benefits {pay_date.isoformat()}", name, klass))
            lines.append((benefits_liability or bank_account, ZERO, benefits,
                          "Employer benefits " + ("payable" if benefits_liability else "paid"),
                          name, klass))

        lines.append((bank_account, ZERO, net, f"Net pay {pay_date.isoformat()}", name, klass))
        lines.extend(withholding_lines)

        entries.append(_entry(
            date=pay_date,
            number=f"PR-{pay_date.strftime('%Y%m%d')}",
            lines=lines,
            memo=f"Payroll {pay_date.isoformat()}",
            kind="payroll",
            basis=basis,
            batch_tag=batch_tag,
        ))

    return Drafted(entries, kind="payroll", notes=notes)
