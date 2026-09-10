"""Which side of its account a balance sits on, and how that reads to a person.

    from closethebooks.sides import balance_text, scan

    balance_text(D("-3118447.25"), safe_notes)   # "3,118,447.25 Cr"
    balance_text(D("-18204.67"), checking)       # "18,204.67 Cr"  <- wrong side
    for finding in scan(ledger):
        print(finding.headline())

TWO JOBS, AND THE SECOND IS THE VALUABLE ONE

**Rendering.** Internally every amount in this engine is DEBIT POSITIVE, which
is `model.py`'s convention and does not change here: a trial balance foots to
exactly 0.00 and no module has to guess which way a report rendered a contra
line. Nothing in this module touches that arithmetic. It only decides how a
balance is written down for somebody to read.

Debit-positive is not how anybody reads a balance sheet. An equity account with
a credit balance of 3,118,447.25 printed as `-3,118,447.25` says the company
holds negative SAFE notes, which is not a thing, and a tool that prints it looks
broken before it has said anything useful. Every accountant, founder and tax
preparer reads a liability or an equity balance as a positive number on its own
side, which is also how the exported trial balance prints it: in the Credit
column, unsigned. So a rendered balance is a MAGNITUDE and a SIDE, `Dr` or `Cr`,
and it is never negative.

**Finding the wrong side.** An account carrying a balance on the side opposite
its normal one is a finding, and it is often the most important one in the file.
On a real file the headline finding was a checking account that closed the
year on the CREDIT side, having carried a debit balance a year earlier. A
checking account cannot hold a credit balance. Rendered debit-positive that fact
is a minus sign among other minus signs; rendered here it is a named finding
with the two things it can mean and the document that settles which.

WHAT THIS MODULE WILL NOT DO

It will not tell you WHICH explanation is right. A bank account on the credit
side is either deposits the books never recorded or an account genuinely
overdrawn, and no export can distinguish them. Every `meaning` below names the
alternatives and then names the document that settles it. Wording a guess as a
conclusion is how a workpaper ends up asserting something about a client's own
records that the client can disprove.

WHAT GETS A NATURAL SIDE AND WHAT DOES NOT

A BALANCE does: what an account holds at a point in time, including a statement
balance and an opening balance. A DIFFERENCE does not, and neither does a
movement, a deposit total or the amount of one transaction. A difference of
-100.00 is not "100.00 Cr"; it is the amount by which two figures disagree and
its sign says which way. Those keep `util.fmt`, which renders a negative in
parentheses.

THE SCAN IS BALANCE SHEET ONLY

A revenue account is credit-normal and an expense account is debit-normal, and
`normal_side` says so for both, because rendering needs it. The wrong-side SCAN
skips them. At each year end QuickBooks closes every income and expense balance
into Retained Earnings WITHOUT posting a journal line, so a ledger spanning two
years holds an expense account's cumulative figure while the trial balance holds
only the current year's. Reporting a debit balance in a revenue account off that
cumulative figure would report a defect that the year-end close explains. Exit
test 1 ties those accounts in aggregate for exactly the same reason.

Retained earnings is skipped too, and named in `excluded` rather than dropped
silently. A debit balance there is an accumulated deficit, which is the ordinary
condition of a company that has not yet been profitable.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Optional

from .model import (
    CREDIT_NORMAL_TYPES, DEBIT_NORMAL_TYPES, canonical_type, is_real_account,
)
from .util import ZERO, iso, money, norm_text

__all__ = [
    "DEBIT", "CREDIT", "WrongSide", "balance_text", "natural", "normal_side",
    "normal_side_of_kind", "normal_side_of_type", "on_wrong_side", "render",
    "scan", "side_of", "side_word", "UNDETERMINED",
]

DEBIT = "Dr"
CREDIT = "Cr"

UNDETERMINED = "cannot be determined"

_SIDE_WORD = {DEBIT: "debit", CREDIT: "credit", "": "neither"}

# Which real-world account kinds a profile's AccountSpec can declare, and the
# side that increases each. A profile knows `bank` or `card` and nothing about
# QuickBooks types, so the tie-out can render on the natural side without a
# chart in hand.
_KIND_SIDES = {"bank": DEBIT, "card": CREDIT, "credit card": CREDIT}


def side_word(side: str) -> str:
    """`Dr` as the word "debit", for a sentence rather than a column."""
    return _SIDE_WORD.get(side or "", "neither")


def normal_side_of_type(account_type: str) -> str:
    """The side that INCREASES an account of this QuickBooks type.

    Returns "" when the type is not one the engine recognises, which means every
    sign statement about the account would be a guess. `model.canonical_type`
    does the normalising, so "Other Current Liabilities" and
    "other current liability" answer the same.
    """
    t = canonical_type(account_type or "")
    if t in DEBIT_NORMAL_TYPES:
        return DEBIT
    if t in CREDIT_NORMAL_TYPES:
        return CREDIT
    return ""


def normal_side_of_kind(kind: str) -> str:
    """The normal side of a profile `AccountSpec.kind`: bank or card."""
    return _KIND_SIDES.get(norm_text(kind), "")


def normal_side(account) -> str:
    """The normal side of an `Account`, a type string, or a side already known.

    Accepts a bare "Dr"/"Cr" so a caller that has resolved the side some other
    way (a statement's account spec, say) can pass it straight through.
    """
    if account is None:
        return ""
    if isinstance(account, str):
        if account in (DEBIT, CREDIT):
            return account
        return normal_side_of_type(account)
    known = getattr(account, "type", None)
    if known is not None:
        return normal_side_of_type(known)
    return ""


def side_of(signed) -> str:
    """Which side a debit-positive amount actually sits on. "" for zero.

    Zero sits on neither side. A balance of 0.00 is written "0.00" with no
    marker, because "0.00 Cr" invites a reader to think something is there.
    """
    value = money(signed)
    if value > ZERO:
        return DEBIT
    if value < ZERO:
        return CREDIT
    return ""


def natural(signed, account=None):
    """`(magnitude, side)`. The magnitude is never negative.

    `account` is not needed to work out which side a balance is on, only to work
    out whether that side is the normal one. It is accepted here so a caller can
    pass the same arguments to every function in this module.
    """
    value = money(signed)
    return abs(value), side_of(value)


def on_wrong_side(signed, account) -> bool:
    """True when this balance sits opposite the account's normal side.

    False for zero, and False when the account's type is unknown: an unknown
    type means there is no normal side to be opposite, and inventing one is how
    a check reports findings it cannot support.
    """
    normal = normal_side(account)
    sitting = side_of(signed)
    return bool(normal and sitting and sitting != normal)


def balance_text(signed, account=None, *, undetermined=UNDETERMINED,
                 side_when_unknown=True) -> str:
    """A balance as a person reads it: "3,118,447.25 Cr". Never negative.

    `None` renders as `undetermined`, because a balance that could not be
    worked out is a different answer from 0.00 and must never be printed as one.

    When the account's type is unknown the side is still printed, because the
    side a balance SITS on is a fact about the balance and does not depend on
    knowing the account. Pass `side_when_unknown=False` to omit it.
    """
    if signed is None:
        return undetermined
    magnitude, side = natural(signed, account)
    if not side:
        return f"{magnitude:,.2f}"
    if not normal_side(account) and not side_when_unknown:
        return f"{magnitude:,.2f}"
    return f"{magnitude:,.2f} {side}"


# ------------------------------------------------------------------ findings

# What a balance on the wrong side usually means, keyed by the account's
# canonical type. Each one names the alternatives and then the document that
# settles which, and none of them asserts a cause. `impossible` marks the two
# shapes that cannot be a legitimate presentation: money in a bank account and
# money owed on a card.
_MEANINGS = {
    "bank": (
        True,
        "A bank account holds either money or an overdraft. Money on the credit "
        "side means either deposits the books have not recorded, or an account "
        "genuinely overdrawn.",
        "the bank statement for that month, which states what the bank held",
    ),
    "credit card": (
        True,
        "A card is money owed. A balance on the debit side means either a payment "
        "recorded twice, a refund posted without the charge it reverses, or a real "
        "credit sitting on the card.",
        "the card statement for that month, which states what was owed",
    ),
    "accounts receivable": (
        False,
        "A receivable on the credit side means either a customer overpayment, a "
        "credit memo never applied, or a payment applied to the wrong invoice.",
        "the open invoice list and the customer's own statement",
    ),
    "accounts payable": (
        False,
        "A payable on the debit side means either a bill paid twice, a prepayment "
        "to a vendor, or a payment applied to a bill nobody entered.",
        "the unpaid bills list and the vendor's own statement",
    ),
    "other current asset": (
        False,
        "An asset on the credit side means either something drawn down past what "
        "was there, or a receipt booked against the asset without the entry that "
        "should have gone with it.",
        "the document behind the account: the prepaid schedule, the loan "
        "agreement, or the sub-ledger it is meant to agree with",
    ),
    "fixed asset": (
        False,
        "A fixed asset on the credit side means either a disposal booked without "
        "removing the cost, or depreciation posted to the asset account instead of "
        "to accumulated depreciation.",
        "the fixed asset register and the depreciation schedule",
    ),
    "other asset": (
        False,
        "An asset on the credit side means either something drawn down past what "
        "was there, or a receipt booked against the asset without the entry that "
        "should have gone with it.",
        "the agreement or schedule the account is meant to agree with",
    ),
    "other current liability": (
        False,
        "A liability on the debit side means either more paid against the "
        "obligation than was ever recorded as owed, or an amount repaid twice.",
        "the agreement or payroll report the balance is meant to agree with",
    ),
    "long term liability": (
        False,
        "A liability on the debit side means either more paid against the "
        "obligation than was ever recorded as owed, or an amount repaid twice.",
        "the loan or note agreement and its amortization schedule",
    ),
    "equity": (
        False,
        "Equity on the debit side means either a distribution, a repurchase or an "
        "accumulated deficit sitting in an account that was set up to hold "
        "contributions.",
        "the cap table, the board consents and the instruments themselves",
    ),
    "income": (
        False,
        "Revenue on the debit side means either refunds and chargebacks booked "
        "straight against the revenue account, or a sale reversed after it was "
        "recorded.",
        "the sales records and the deposits behind them",
    ),
    "other income": (
        False,
        "Income on the debit side means either a reversal of something recorded "
        "earlier, or a cost booked to an income account.",
        "the records behind the original amount",
    ),
    "expense": (
        False,
        "An expense on the credit side means either a refund or rebate booked to "
        "the expense account, or an accrual reversed twice.",
        "the vendor credit or refund behind it",
    ),
    "cost of goods sold": (
        False,
        "A cost account on the credit side means either a supplier credit booked "
        "against it, or an accrual reversed twice.",
        "the supplier credit behind it",
    ),
}

# Some accounts are named for a ROLE the engine already knows, and the type
# alone gives the wrong explanation for them. A clearing account is not really
# an asset; opening balance equity is not really equity.
_ROLE_MEANINGS = {
    "clearing": (
        False,
        "A clearing account is a route, not a destination, and either side of it "
        "is money whose other half was never posted. On the opposite side to its "
        "type it usually means payouts recorded without the receipts behind them, "
        "or a batch booked net when the gross was already there.",
        "the processor's own payout report for the period, gross and fee by fee",
    ),
    "obe": (
        False,
        "Opening balance equity is a holding pen QuickBooks fills while an account "
        "is being set up, and any balance in it is an opening figure nobody has "
        "explained yet. Which side it is on says only which way the unexplained "
        "entry went.",
        "the prior accountant's closing trial balance, which says what each "
        "opening figure actually was",
    ),
    "safe": (
        False,
        "A SAFE is money the company owes on an instrument until it converts or is "
        "repaid. On the debit side it usually means issuance or financing costs "
        "booked into the same account as the instrument itself, or a conversion "
        "posted on one side only.",
        "the signed instruments and the cap table, which state the amount "
        "outstanding and whether anything has converted",
    ),
    "intercompany": (
        False,
        "An intercompany balance on the opposite side usually means the two sets of "
        "books disagree about which way the money went, or a repayment was recorded "
        "on one side and not the other.",
        "the other entity's ledger for the same account, tied line by line",
    ),
    "prepaid": (
        False,
        "A prepaid on the credit side usually means more amortized out than was ever "
        "capitalized in, or an invoice expensed straight to the account without the "
        "asset ever being set up.",
        "the prepaid schedule and the invoices behind it",
    ),
    "payroll_liability": (
        False,
        "A payroll liability on the debit side usually means a remittance made "
        "against an accrual that was never recorded, or the provider's own journal "
        "posted alongside a manual one for the same run.",
        "the payroll provider's own report for the period",
    ),
}

# A role the chart's own naming implies, where the account TYPE alone would send
# the reader to the wrong explanation. `_role_for` in `qbo_exports` keys off the
# type first, so an equity-typed "Opening Balance Equity" comes back with the
# role "equity" and would otherwise be explained as a distribution. Same
# vocabulary as `exit_tests._OBE_NAME` and `_CLEARING_NAME`, deliberately.
_ROLE_BY_NAME = (
    (re.compile(r"\bopening balance equity\b", re.I), "obe"),
    (re.compile(r"\b(clearing|suspense|undeposited funds|ask my accountant|"
                r"uncategori[sz]ed|to be (re)?classified)\b", re.I), "clearing"),
    (re.compile(r"\bsafe\b", re.I), "safe"),
    (re.compile(r"\b(due (from|to)|intercompany)\b", re.I), "intercompany"),
)

_GENERIC = (
    False,
    "A balance on the side opposite an account's normal one means either "
    "something was posted the wrong way round, or the account is being used for "
    "something other than what its type says.",
    "the documents behind the postings in it",
)

# Accounts that BELONG on the opposite side, by their own name. A contra
# account exists to sit against another one: accumulated depreciation reduces an
# asset, treasury stock reduces equity, financing costs reduce the instrument
# they were incurred on. Retained earnings on the debit side is an accumulated
# deficit, which is the ordinary condition of a company that has not yet been
# profitable. Reporting any of these as a defect trains a reader to skim the
# list that also holds the checking account, which is the one they must not
# skim. They are named in `excluded` rather than dropped, so nothing disappears
# silently and a reader can disagree with the judgement.
_OPPOSITE_BY_DESIGN = re.compile(
    r"\b(retained earnings|accumulated (deficit|surplus)|net income|"
    r"owner'?s? draws?|distributions?|treasury stock|accumulated depreciation|"
    r"accumulated amortization|allowance for doubtful|contra|"
    r"(financing|issuance|offering|debt) costs?|discount on)\b",
    re.I,
)


@dataclass
class WrongSide:
    """One account whose balance sits on the side opposite its normal one."""

    key: str = ""
    label: str = ""
    account_type: str = ""
    normal: str = ""                  # DEBIT | CREDIT
    sitting: str = ""                 # DEBIT | CREDIT, always the other one
    signed: Decimal = ZERO            # debit-positive, as the engine holds it
    amount: Decimal = ZERO            # the magnitude a person reads
    as_of: object = None
    prior: Optional[Decimal] = None   # the same balance a year earlier, or None
    prior_as_of: object = None
    impossible: bool = False          # this side cannot be a real presentation
    meaning: str = ""
    settled_by: str = ""

    @property
    def prior_was_normal(self) -> bool:
        return self.prior is not None and side_of(self.prior) == self.normal

    def rendered(self) -> str:
        return f"{self.amount:,.2f} {self.sitting}"

    def headline(self) -> str:
        """One line: what it is, and what side it is on. No cause claimed."""
        return (
            f"{self.label} is {side_word(self.normal)}-normal and carries "
            f"{self.rendered()}"
            + (f" at {iso(self.as_of)}" if self.as_of else "")
            + "."
        )

    def lines(self) -> list:
        """The finding as a person reads it. Four short paragraphs at most."""
        out = [self.headline()]
        if self.prior is not None:
            if self.prior == ZERO:
                out.append(
                    f"At {iso(self.prior_as_of)} it was 0.00, so the whole of this "
                    f"arose inside the period."
                )
            elif self.prior_was_normal:
                out.append(
                    f"A year earlier, at {iso(self.prior_as_of)}, it carried "
                    f"{balance_text(self.prior)}, which is the side it belongs on. "
                    f"It crossed sides inside the period."
                )
            else:
                out.append(
                    f"At {iso(self.prior_as_of)} it already carried "
                    f"{balance_text(self.prior)}, so this did not start inside "
                    f"the period."
                )
        out.append(self.meaning)
        out.append(f"What settles it: {self.settled_by}. Nothing in the exports can.")
        return out

    def as_row(self) -> dict:
        return {
            "account": self.label,
            "account_key": self.key,
            "type": self.account_type,
            "normal side": self.normal,
            "sitting on": self.sitting,
            "balance": self.rendered(),
            "as of": iso(self.as_of),
            "a year earlier": (balance_text(self.prior) if self.prior is not None
                               else UNDETERMINED),
            "impossible": "yes" if self.impossible else "no",
        }


@dataclass
class SideScan:
    """Every wrong-side balance in one ledger, plus what was and was not looked at."""

    findings: list = field(default_factory=list)
    checked: int = 0
    undeterminable: list = field(default_factory=list)
    excluded: list = field(default_factory=list)      # (label, why)
    as_of: object = None
    prior_as_of: object = None

    @property
    def impossible(self) -> list:
        return [f for f in self.findings if f.impossible]

    def __bool__(self) -> bool:
        return bool(self.findings)

    def __len__(self) -> int:
        return len(self.findings)

    def __iter__(self):
        return iter(self.findings)


def _meaning_for(account, normal: str):
    """What this account being on the wrong side usually means.

    The account's NAME is consulted before its type, because a type is a
    category and a name is what the bookkeeper meant. "Opening Balance Equity"
    is typed as equity and explaining it as a distribution would send the reader
    to the cap table for something the prior accountant's trial balance
    settles.
    """
    label = f"{getattr(account, 'full_name', '')} {getattr(account, 'name', '')}"
    for pattern, role in _ROLE_BY_NAME:
        if pattern.search(label):
            return _ROLE_MEANINGS[role]
    role = getattr(account, "role", "") or ""
    if role in _ROLE_MEANINGS:
        return _ROLE_MEANINGS[role]
    account_type = getattr(account, "type", "") if not isinstance(account, str) else account
    return _MEANINGS.get(canonical_type(account_type or ""), _GENERIC)


def scan(ledger, *, as_of=None, prior_as_of=None) -> SideScan:
    """Every balance-sheet account whose balance is on the wrong side.

    `as_of` defaults to the ledger's period end. `prior_as_of` defaults to a
    year before that, and exists to answer the question a reader asks next: was
    it always like this, or did it go wrong inside the period.

    Accounts that are not real (parent rollups that only total their children,
    and template rows nothing has ever been posted to) are not scanned, for the
    same reason they are not reconciled. See `model.is_real_account`.
    """
    as_of = as_of or getattr(ledger, "period_end", None)
    if prior_as_of is None and as_of is not None:
        try:
            prior_as_of = as_of.replace(year=as_of.year - 1)
        except ValueError:                       # 29 February
            prior_as_of = as_of.replace(year=as_of.year - 1, day=28)

    out = SideScan(as_of=as_of, prior_as_of=prior_as_of)
    for account in sorted((getattr(ledger, "accounts", {}) or {}).values(),
                          key=lambda a: (a.number or "", a.full_name)):
        label = account.label() or account.full_name
        if not account.is_balance_sheet:
            continue
        if not is_real_account(ledger, account):
            continue
        if _OPPOSITE_BY_DESIGN.search(f"{account.full_name} {account.name}"):
            out.excluded.append(
                (label, "the name says the opposite side is where it belongs, so being "
                        "there is by design"))
            continue
        normal = normal_side(account)
        if not normal:
            out.excluded.append(
                (label, f"the type {account.type or '(blank)'!r} is not one the engine "
                        f"knows, so there is no normal side to be opposite"))
            continue
        out.checked += 1
        balance = (ledger.balance_as_of(account.key, as_of) if as_of is not None
                   else ledger.balance_of(account.key))
        if balance is None:
            out.undeterminable.append(label)
            continue
        if not on_wrong_side(balance, account):
            continue
        prior = None
        if prior_as_of is not None:
            prior = ledger.balance_as_of(account.key, prior_as_of)
        impossible, meaning, settled_by = _meaning_for(account, normal)
        out.findings.append(WrongSide(
            key=account.key, label=label, account_type=account.type,
            normal=normal, sitting=side_of(balance),
            signed=money(balance), amount=abs(money(balance)),
            as_of=as_of, prior=prior, prior_as_of=prior_as_of,
            impossible=impossible, meaning=meaning, settled_by=settled_by,
        ))
    out.findings.sort(key=lambda f: (not f.impossible, -f.amount, f.key))
    return out


_INTRO = (
    "An account carrying a balance on the side opposite its normal one is a "
    "finding. Each one below states what the account is, which side it belongs "
    "on, which side it is on, and the two or three things that normally cause "
    "it. Which of them is true here is not something an export can answer, so "
    "each one names the document that settles it."
)


def _wrap(text, width=76, indent="") -> list:
    """Fold one paragraph to `width`, keeping every word. Nothing is truncated.

    A finding is prose and a terminal is 80 columns. Truncating it would drop
    the half that names the document that settles the question, which is the
    half a reader acts on.
    """
    out, line = [], ""
    for word in str(text).split():
        if line and len(line) + 1 + len(word) > width:
            out.append(indent + line)
            line = word
        else:
            line = f"{line} {word}".strip()
    out.append(indent + line)
    return out


def render(scanned, *, limit=None, heading="Balances on the wrong side") -> list:
    """The findings as terminal lines. Markdown-free, so both renderers use it."""
    if not isinstance(scanned, SideScan):
        scanned = SideScan(findings=list(scanned or []))
    out = [heading, "-" * min(len(heading), 78)]
    if not scanned.findings:
        out += _wrap(f"{len(scanned.findings)} of {scanned.checked} account(s) carry a "
                     f"balance on the side opposite their normal one.", indent="  ")
    else:
        out += _wrap(
            f"{len(scanned.findings)} of {scanned.checked} account(s) carry a balance "
            f"on the side opposite their normal one, {len(scanned.impossible)} of them "
            f"on a side that cannot be a real presentation.", indent="  ")
        out.append("")
        out += _wrap(_INTRO, indent="  ")
    shown = scanned.findings if limit is None else scanned.findings[:limit]
    for finding in shown:
        lines = finding.lines()
        out.append("")
        out += _wrap(lines[0], indent="  ")
        for line in lines[1:]:
            out += _wrap(line, width=74, indent="    ")
    if len(scanned.findings) > len(shown):
        out.append("")
        out += _wrap(f"... {len(scanned.findings) - len(shown)} more, all of them in "
                     f"the written report.", indent="  ")
    if scanned.undeterminable:
        out.append("")
        out += _wrap(f"{len(scanned.undeterminable)} of {scanned.checked} balance(s) "
                     f"could not be determined and were not checked: "
                     + ", ".join(scanned.undeterminable[:5]), indent="  ")
    if scanned.excluded:
        out.append("")
        out += _wrap(f"{len(scanned.excluded)} account(s) were not swept, because the "
                     f"opposite side is by design (retained earnings, accumulated "
                     f"depreciation, contra accounts) or because their type is not one "
                     f"the engine knows: "
                     + ", ".join(label for label, _ in scanned.excluded[:6])
                     + ("" if len(scanned.excluded) <= 6 else
                        f", and {len(scanned.excluded) - 6} more"),
                     indent="  ")
    return out


def _excluded_markdown(scanned) -> list:
    """What was not checked, grouped by the reason, so a reason is said once.

    Nothing is dropped silently: a reader who disagrees with the judgement can
    see exactly which accounts it was applied to.
    """
    if not getattr(scanned, "excluded", None):
        return []
    by_reason = {}
    for label, why in scanned.excluded:
        by_reason.setdefault(why, []).append(label)
    out = [f"{len(scanned.excluded)} account(s) were not swept for a wrong-side "
           f"balance:", ""]
    for why, labels in by_reason.items():
        out.append(f"- {', '.join(labels)}: {why}.")
    out.append("")
    return out


def render_markdown(scanned, *, heading="## Balances on the wrong side") -> list:
    """The same findings for a document. One bullet per finding, prose intact."""
    if not isinstance(scanned, SideScan):
        scanned = SideScan(findings=list(scanned or []))
    out = [heading, ""]
    if not scanned.findings:
        out.append(f"None. {scanned.checked} balance-carrying account(s) were checked, "
                   f"and every one of them holds its balance on its own normal side.")
        out.append("")
        out += _excluded_markdown(scanned)
        return out
    out.append(f"{len(scanned.findings)} of {scanned.checked} account(s) carry a balance "
               f"on the side opposite their normal one, "
               f"{len(scanned.impossible)} of them on a side that cannot be a real "
               f"presentation.")
    out += ["", _INTRO, ""]
    for finding in scanned.findings:
        lines = finding.lines()
        out.append(f"- **{lines[0]}**")
        for line in lines[1:]:
            out.append(f"  {line}")
        out.append("")
    if scanned.undeterminable:
        out.append(f"{len(scanned.undeterminable)} balance(s) could not be determined "
                   f"and were not checked: " + ", ".join(scanned.undeterminable) + ".")
        out.append("")
    out += _excluded_markdown(scanned)
    return out
