"""A synthetic company with every shape the four scoping analyses exist for.

Not collected by pytest: the name does not start with `test_`. It is imported by
`test_filing_year.py`, `test_feed_gaps.py`, `test_twins.py` and
`test_autoposted.py` the same way `test_sides.py` imports `test_balances.py`.

Every company, account, vendor, person and figure here is invented. What is
real is the STRUCTURE, which is the shape a wind-down on extension takes, and it
carries, in one ledger:

  * a queue of 863 unbooked items of which only 312 are work, because 354 are
    dated in the following year and 197 sit in months already reconciled clean;
  * a card whose feed was relinked, leaving TWO chart rows, the numbered one
    holding all the posted history and no feed and the unnumbered one holding
    the whole queue, no history, and a 15,803.42 opening-balance plug that
    Opening Balance Equity offsets exactly;
  * an operating account with a seven-month hole in the middle of the filing
    year, bracketed by activity on both sides;
  * a second account whose books say (41,876.20) while the bank says 37.14 and
    whose unbooked items net (52,309.55), so working the queue moves it further
    from the bank rather than closer;
  * one 3,187.65 entry dated in December of the filing year, alone in its month,
    matching a pattern the company's own history established, posted eight
    months after the last hand-worked entry.

The figures are invented and they are exact, because a rounded fixture proves
nothing about arithmetic. Every relationship the tests assert holds: the three
partitions sum to the queue total, the plug equals the Opening Balance Equity
balance, and the four unbooked items sum to the queue net the balance walk uses.
"""

from __future__ import annotations

import datetime
import os
import sys
from decimal import Decimal

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "lib"))

from closethebooks.model import Account, BankLine, JournalLine, Ledger  # noqa: E402
from closethebooks.profile import AccountSpec, Profile                 # noqa: E402

D = Decimal

COMPANY = "Northwind Instruments Inc."
PERIOD_START = datetime.date(2024, 1, 1)
PERIOD_END = datetime.date(2025, 12, 31)
FILING_YEAR = 2025

# The queue, as everybody kept quoting it and as it actually partitions.
QUEUE_TOTAL = 863
QUEUE_NEXT_YEAR = 354
QUEUE_ALREADY_BOOKED = 197
QUEUE_REAL_WORK = 312

# The plug on the relinked card, and the equity balance that offsets it.
PLUG = D("15803.42")

# The account whose queue moves it away from the bank.
DRIFT_BOOK = D("-41876.20")
DRIFT_BANK = D("37.14")
DRIFT_QUEUE_NET = D("-52309.55")

# The deduction that appeared in December with nobody watching.
DECEMBER_AMOUNT = D("3187.65")
DECEMBER_DATE = datetime.date(2025, 12, 5)

# Reconciled clean through here, on the account that holds the history.
RECONCILED_THROUGH = datetime.date(2025, 2, 28)

# The last entry a person is visible in: a payee categorised for the first time.
HAND_STOPPED = datetime.date(2025, 2, 18)

CARD_NUMBERED = "222000"
CARD_TWIN = "Northwind card"
OPERATING = "101000"
DRIFT = "102000"


def _account(number, full_name, acct_type, role, balance=D("0.00")):
    return Account(name=full_name.split(":")[-1], number=number, full_name=full_name,
                   type=acct_type, role=role, balance=balance)


def chart() -> dict:
    accounts = [
        _account(OPERATING, "Current Assets:Operating Checking (4015)", "Bank", "bank"),
        _account(DRIFT, "Current Assets:Second Checking (3947)", "Bank", "bank"),
        _account(CARD_NUMBERED, "Credit Cards:Northwind Card", "Credit Card", "card"),
        # The row the rebuilt feed landed on: top level, no number, same name
        # once case is normalized.
        _account("", CARD_TWIN, "Credit Card", "card", balance=-PLUG),
        _account("", "Opening balance equity", "Equity", "obe", balance=PLUG),
        _account("602005", "Payroll Expense:Employee Benefits", "Expenses", "expense"),
        _account("601000", "Software", "Expenses", "expense"),
        _account("602001", "Payroll Expense:Wages", "Expenses", "expense"),
        _account("400000", "Sales", "Income", "revenue"),
    ]
    return {a.key: a for a in accounts}


def _pair(when, bank_key, expense_key, amount, name, memo, txn_type="Expense",
          doc_num=""):
    """One transaction, both legs, money leaving `bank_key`."""
    amount = D(amount)
    return [
        JournalLine(date=when, account=bank_key, credit=amount, name=name, memo=memo,
                    txn_type=txn_type, doc_num=doc_num),
        JournalLine(date=when, account=expense_key, debit=amount, name=name, memo=memo,
                    txn_type=txn_type, doc_num=doc_num),
    ]


# One vendor per month, each appearing once. A person categorising a new payee
# is the closest thing an export has to somebody's own work, and a vendor that
# never repeats can never become a pattern a rule could be written from. That is
# what makes the last of these the date the file stopped being worked.
_HAND_WORKED = [
    ("Cedarline Design", "1250.00"), ("Portage Legal LLP", "980.00"),
    ("Harborview Realty", "3100.00"), ("Tessellate Print Co", "412.50"),
    ("Ravenswood Supply", "745.00"), ("Halcyon Freight", "1890.25"),
    ("Ambler Analytics", "560.00"), ("Kestrel Fabrication", "2310.00"),
    ("Lindenmere Catering", "418.90"), ("Sawtooth Metals", "1675.00"),
    ("Verdant Grounds Co", "295.00"), ("Ostergaard Tooling", "3420.00"),
    ("Blythe Instruments", "880.00"), ("Marchetti Consulting", "1540.00"),
]


def lines() -> list:
    out = []

    # Thirteen monthly health premiums, one vendor, one account, every one of
    # them the same shape. This is what makes the December entry a pattern match
    # rather than a novelty.
    premium_months = [(2024, m) for m in range(1, 13)] + [(2025, m) for m in range(1, 5)]
    for year, month in premium_months:
        out += _pair(datetime.date(year, month, 5), OPERATING, "602005", "2321.60",
                     "Northshore Health", "NORTHSHORE HEALTH; ISA*00* *ZZ*XXXXXX9245")

    # Hand-worked entries, ending in February 2025. After this nobody touches
    # the file, which is what makes December's arrival a finding.
    for (year, month), (vendor, amount) in zip(premium_months[:14], _HAND_WORKED):
        out += _pair(datetime.date(year, month, 18), OPERATING, "601000", amount,
                     vendor, f"{vendor} invoice {year}{month:02d}")

    # The numbered card: two years of history, stopping when the feed moved.
    for year, month in premium_months[:14]:
        out += _pair(datetime.date(year, month, 9), CARD_NUMBERED, "601000", "640.00",
                     "Quarterdeck Software", "QUARTERDECK SOFTWARE MONTHLY")

    # The second checking account, which keeps posting into May 2025.
    for year, month in premium_months + [(2025, 5)]:
        out += [
            JournalLine(date=datetime.date(year, month, 14), account=DRIFT,
                        debit=D("2400.00"), name="Deposits", memo="CUSTOMER DEPOSIT",
                        txn_type="Deposit"),
            JournalLine(date=datetime.date(year, month, 14), account="400000",
                        credit=D("2400.00"), name="Deposits", memo="CUSTOMER DEPOSIT",
                        txn_type="Deposit"),
        ]

    # A payroll integration writing its own name into every memo, twice a month
    # until the file went quiet. This is ROUTINE automation: it posts into months
    # full of other work, with people around it. It is here so the tests can show
    # that the entry alone in an empty month outranks it even at twice the size.
    for year, month in premium_months:
        out += _pair(datetime.date(year, month, 28), OPERATING, "602001", "5803.04",
                     "", f"[Payroll Systems] Employee net pay for check date "
                         f"{month:02d}/28/{year}", txn_type="Journal Entry")

    # The plug QuickBooks writes when a feed is linked onto a fresh account, and
    # the Opening Balance Equity leg that carries the other side of it.
    out += [
        JournalLine(date=datetime.date(2025, 1, 1), account=CARD_TWIN, credit=PLUG,
                    memo="Opening Balance from Bank", txn_type="Credit Card Expense"),
        JournalLine(date=datetime.date(2025, 1, 1), account="Opening balance equity",
                    debit=PLUG, memo="", txn_type="Credit Card Expense"),
    ]

    # December. One entry, in a month that holds nothing else, eight months
    # after the last hand-worked entry, for an amount nobody chose.
    out += _pair(DECEMBER_DATE, OPERATING, "602005", DECEMBER_AMOUNT,
                 "Northshore Health",
                 "NORTHSHORE HEALTH; ISA*00* *ZZ*XXXXXX9245 *Z")
    return out


def ledger() -> Ledger:
    """The whole company. Opening balances are stated, so balances are knowable.

    `DRIFT`'s opening is set so its closing balance is exactly (41,876.20), the
    figure the balance walk is asserted against.
    """
    posted = lines()
    led = Ledger(accounts=chart(), lines=posted, company=COMPANY, basis="accrual",
                 period_start=PERIOD_START, period_end=PERIOD_END,
                 opening_basis="the Beginning Balance rows of the 2024 general ledger",
                 sources=["synthetic"])
    movement = {}
    for line in posted:
        movement[line.account] = movement.get(line.account, D("0.00")) + line.signed
    led.opening_balances = {
        OPERATING: D("41905.22"),
        DRIFT: DRIFT_BOOK - movement.get(DRIFT, D("0.00")),
        CARD_NUMBERED: D("-6160.76"),
        CARD_TWIN: D("0.00"),
        "Opening balance equity": D("0.00"),
        "601000": D("0.00"), "602001": D("0.00"), "602005": D("0.00"),
        "400000": D("0.00"),
    }
    return led


def profile() -> Profile:
    prof = Profile()
    prof.entity.name = COMPANY
    prof.entity.basis = "accrual"
    prof.entity.end_use = "the 2025 federal return, on extension"
    prof.accounts = [
        AccountSpec(book=OPERATING, label="Operating Checking (4015)", kind="bank",
                    feed="live"),
        AccountSpec(book=DRIFT, label="Second Checking (3947)", kind="bank", feed="live"),
        AccountSpec(book=CARD_NUMBERED, label="Northwind Card", kind="card", feed="none"),
        AccountSpec(book=CARD_TWIN, label="Northwind card", kind="card", feed="live"),
    ]
    return prof


# ---------------------------------------------------------------- the queue

def _queue_item(when, account_key, amount, descriptor, row):
    return BankLine(date=when, descriptor=descriptor, amount=D(amount),
                    account_key=account_key, source_row=row, origin="for_review")


def queue() -> list:
    """863 unbooked items, in the three shapes that make 863 the wrong number.

    The 197 already-booked items sit on the TWIN, whose own reconciliation
    history is empty. Only by following the twin back to the numbered account do
    they read as already booked, which is the trap the whole partition exists
    for.
    """
    out, row = [], 1

    # 197 re-downloads of January and February, on the relinked card, in months
    # the numbered account was reconciled clean.
    for i in range(QUEUE_ALREADY_BOOKED):
        day = (i % 27) + 1
        month = 1 if i < 99 else 2
        out.append(_queue_item(datetime.date(2025, month, day), CARD_TWIN,
                               D("-118.40"), f"QUARTERDECK SOFTWARE {i:04d}", row))
        row += 1

    # 312 items that are actually work: inside the filing year, after the last
    # reconciled month.
    for i in range(QUEUE_REAL_WORK):
        month = 3 + (i % 10)
        day = (i % 27) + 1
        out.append(_queue_item(datetime.date(2025, month, day), CARD_TWIN,
                               D("-64.25"), f"CEDARLINE DESIGN {i:04d}", row))
        row += 1

    # 354 items dated in the following year, which change nothing about the
    # return that is due.
    for i in range(QUEUE_NEXT_YEAR):
        month = (i % 12) + 1
        day = (i % 27) + 1
        out.append(_queue_item(datetime.date(2026, month, day), CARD_TWIN,
                               D("-51.10"), f"PORTAGE LEGAL {i:04d}", row))
        row += 1

    assert len(out) == QUEUE_TOTAL
    return out


def drift_queue() -> list:
    """The second checking account's unbooked items, netting (52,309.55).

    Four items, because the walk is about the NET and not the count, and a
    fixture with four rows is one a reader can add up by hand.
    """
    amounts = ["-22000.00", "-18309.55", "-11000.00", "-1000.00"]
    return [_queue_item(datetime.date(2025, 6, 10 + i), DRIFT, amount,
                        f"WIRE OUT {i}", 900 + i)
            for i, amount in enumerate(amounts)]


def reconciled_through() -> dict:
    """What a reconciliation actually establishes on this file.

    The numbered card is reconciled clean through February. The twin is not in
    the mapping at all, because nobody has ever reconciled it, and that absence
    is what the partition has to survive.
    """
    return {CARD_NUMBERED: RECONCILED_THROUGH, OPERATING: RECONCILED_THROUGH}
