"""The objects every skill and module shares.

Sign convention, chosen once and applied everywhere: **debit positive**.
`signed` is always `debit - credit`. An asset or expense with a normal balance
is positive; a liability, equity or revenue account with a normal balance is
negative. A trial balance therefore foots to exactly 0.00, which is the
engine's most-used exit test, and no module has to guess which way a report
happened to render a contra line.
"""

from __future__ import annotations

import dataclasses
import re
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Optional

from .util import ZERO, money, iso, month_key, norm_text

# ---------------------------------------------------------------- accounts

# Roles are the engine's vocabulary for "what this account is for". The chart
# numbering differs in every company, so no rule may key off an account number.
ROLES = (
    "bank", "card", "clearing", "obe", "ar", "ap", "prepaid", "intangible",
    "amortization", "intercompany", "safe", "equity", "revenue", "expense",
    "payroll_liability", "shareholder", "tax", "other",
)

# Account types, as QuickBooks actually writes them, mapped to the side that
# increases them.
#
# Two facts learned from real exports, both of which cost real money to relearn:
#
# 1. QuickBooks writes types in the PLURAL and with parenthetical suffixes:
#    "Expenses", "Other Current Assets", "Fixed Assets", "Accounts Payable (A/P)".
#    An earlier singular vocabulary here left most of a real chart unclassified,
#    and silently, because an unknown type simply reads as not debit-normal.
# 2. This vocabulary is load-bearing for signs, not just for reporting. The
#    General Ledger export signs its Amount column in the ACCOUNT'S OWN
#    direction: positive is a debit on a bank account and a CREDIT on a card,
#    payable, liability, equity or income account. Nothing inside the report
#    contradicts itself, so the error is invisible to any single-file check.
#    Getting this wrong inverted a real FY by 13,785.36 across 7 of 38 accounts.
#    `canonical_type` is what the GL loader uses to put the sign back.

def canonical_type(raw: str) -> str:
    """Normalize a QuickBooks account type to a single lowercase singular form."""
    t = (raw or "").strip().lower()
    t = re.sub(r"\s*\([^)]*\)", "", t)          # drop "(A/P)", "(A/R)"
    t = re.sub(r"\s+", " ", t).strip()
    aliases = {
        "banks": "bank", "cash": "bank", "cash and cash equivalents": "bank",
        "accounts receivable": "accounts receivable", "a/r": "accounts receivable",
        "receivables": "accounts receivable",
        "accounts payable": "accounts payable", "a/p": "accounts payable",
        "payables": "accounts payable",
        "credit cards": "credit card",
        "other current assets": "other current asset",
        "current assets": "other current asset",
        "fixed assets": "fixed asset", "property plant and equipment": "fixed asset",
        "other assets": "other asset", "non-current assets": "other asset",
        "other current liabilities": "other current liability",
        "current liabilities": "other current liability",
        "long term liabilities": "long term liability",
        "long-term liabilities": "long term liability",
        "other liabilities": "long term liability",
        "expenses": "expense", "other expenses": "other expense",
        "cost of goods sold": "cost of goods sold", "cogs": "cost of goods sold",
        "costs of goods sold": "cost of goods sold",
        "income": "income", "revenue": "income", "sales": "income",
        "other income": "other income", "income - other": "other income",
        "equity": "equity", "owners equity": "equity", "shareholders equity": "equity",
    }
    if t in aliases:
        return aliases[t]
    if t.endswith("s") and t[:-1] in DEBIT_NORMAL_TYPES | CREDIT_NORMAL_TYPES:
        return t[:-1]
    return t


DEBIT_NORMAL_TYPES = {
    "bank", "accounts receivable", "other current asset", "fixed asset",
    "other asset", "expense", "cost of goods sold", "other expense",
}
CREDIT_NORMAL_TYPES = {
    "accounts payable", "credit card", "other current liability",
    "long term liability", "equity", "income", "other income",
}


@dataclass
class Account:
    """One row of the chart of accounts."""
    name: str
    number: str = ""
    full_name: str = ""          # "Current Assets:Mercury Checking (4015)"
    type: str = ""               # QuickBooks account type, lowercased
    subtype: str = ""
    role: str = "other"
    balance: Decimal = ZERO
    active: bool = True
    parent: str = ""
    source_row: int = 0

    def __post_init__(self):
        self.balance = money(self.balance, f"account {self.name} balance")
        self.type = canonical_type(self.type)
        if not self.full_name:
            self.full_name = self.name

    @property
    def key(self) -> str:
        """Stable identity. Number where the chart is numbered, else name."""
        return self.number.strip() or self.full_name

    @property
    def debit_normal(self) -> bool:
        return self.type in DEBIT_NORMAL_TYPES

    @property
    def type_known(self) -> bool:
        """False means every sign decision about this account is a guess."""
        return self.type in DEBIT_NORMAL_TYPES or self.type in CREDIT_NORMAL_TYPES

    @property
    def is_balance_sheet(self) -> bool:
        return self.type not in {
            "expense", "cost of goods sold", "other expense", "income", "other income",
        }

    def label(self) -> str:
        return f"{self.number} {self.name}".strip()


# ------------------------------------------------------------ journal lines

@dataclass
class JournalLine:
    """One posted line already in the general ledger.

    This is history, never a proposal. `debit` and `credit` are both
    non-negative; exactly one of them is normally non-zero.
    """
    date: object                      # datetime.date
    account: str                      # account key as it appears in the export
    debit: Decimal = ZERO
    credit: Decimal = ZERO
    memo: str = ""
    name: str = ""                    # customer / vendor / employee
    doc_num: str = ""
    txn_type: str = ""                # "Journal Entry", "Expense", "Deposit"...
    txn_id: str = ""
    account_full: str = ""
    klass: str = ""                   # QuickBooks "Class", never `class`
    source_file: str = ""
    source_row: int = 0

    def __post_init__(self):
        self.debit = money(self.debit, "debit")
        self.credit = money(self.credit, "credit")

    @property
    def signed(self) -> Decimal:
        return self.debit - self.credit

    @property
    def amount(self) -> Decimal:
        """Magnitude, for matching against a bank row."""
        return abs(self.signed)

    @property
    def month(self) -> str:
        return month_key(self.date)

    def as_row(self):
        return {
            "date": iso(self.date), "account": self.account, "debit": str(self.debit),
            "credit": str(self.credit), "memo": self.memo, "name": self.name,
            "doc_num": self.doc_num, "txn_type": self.txn_type, "class": self.klass,
            "source_file": self.source_file, "source_row": self.source_row,
        }


# --------------------------------------------------- bank and card activity

@dataclass
class BankLine:
    """One row from a bank or card statement, or from the For Review queue.

    `amount` is signed from the account holder's point of view: money in is
    positive, money out is negative, for both a checking account and a card.
    Every parser normalizes to this so no downstream module has to know whether
    a particular institution renders a card charge as positive or negative.
    """
    date: object
    descriptor: str
    amount: Decimal
    account_key: str = ""             # which of the company's accounts
    balance: Optional[Decimal] = None  # running balance where the statement gives one
    external_id: str = ""
    posted_date: object = None
    source_file: str = ""
    source_row: int = 0
    origin: str = "statement"         # "statement" | "for_review" | "csv"

    def __post_init__(self):
        self.amount = money(self.amount, "bank amount")
        if self.balance is not None:
            self.balance = money(self.balance, "bank balance")
        self.descriptor = (self.descriptor or "").strip()

    @property
    def money_in(self) -> bool:
        return self.amount > ZERO

    @property
    def month(self) -> str:
        return month_key(self.date)

    @property
    def norm(self) -> str:
        return norm_text(self.descriptor)


# ------------------------------------------------------------------- rules

@dataclass
class Rule:
    """A what-goes-where rule, mined from history or written in a profile.

    A rule never carries an opinion that is not traceable. `source` names the
    rows or the document it came from, and `confidence` is the share of the
    company's own history that agrees with it.
    """
    id: str
    account: str
    match_contains: tuple = ()
    match_regex: str = ""
    direction: str = "any"            # "in" | "out" | "any"
    klass: str = ""
    source: str = ""
    confidence: float = 0.0
    support: int = 0                  # how many historical rows back it
    conflicts: int = 0                # how many went elsewhere
    account_full: str = ""
    note: str = ""

    def matches(self, line: BankLine) -> bool:
        if self.direction == "in" and not line.money_in:
            return False
        if self.direction == "out" and line.money_in:
            return False
        hay = line.norm
        for needle in self.match_contains:
            if norm_text(needle) in hay:
                return True
        if self.match_regex:
            import re
            if re.search(self.match_regex, hay, re.I):
                return True
        return False


# --------------------------------------------------------------- proposals

# What the engine can propose for one unbooked bank row. `question` is a first
# class outcome: a row nothing can explain is escalated, never guessed.
ACTIONS = ("add", "match", "transfer", "question")


@dataclass
class Proposal:
    """One decision offered to the founder about one bank row.

    Nothing here has been posted. It becomes real only when the founder marks
    it in the review workbook, approves the batch in their own terminal, and
    imports the resulting file into QuickBooks themselves.
    """
    line: BankLine
    action: str = "question"
    account: str = ""
    account_full: str = ""
    klass: str = ""
    rule_id: str = ""
    confidence: float = 0.0
    source: str = ""
    question: str = ""
    matched_to: str = ""              # txn_id of the existing entry, if a match
    counter_account: str = ""         # the other side, if a transfer
    needs_human: bool = False         # descriptor contained agent-directed text
    founder_decision: str = ""

    def __post_init__(self):
        if self.action not in ACTIONS:
            raise ValueError(f"unknown action {self.action!r}, expected one of {ACTIONS}")


# ------------------------------------------------------------ journal entry

@dataclass
class ProposedEntry:
    """An adjusting entry the engine drafted, before any human has seen it."""
    date: object
    number: str
    lines: list = field(default_factory=list)   # (account, debit, credit, memo)
    memo: str = ""
    kind: str = ""                    # payroll | prepaid | intangibles | stripe | safe | intercompany | wind_down
    basis: str = ""                   # the rule, document or founder answer it rests on
    batch_tag: str = ""

    def total_debits(self) -> Decimal:
        return sum((money(l[1]) for l in self.lines), ZERO)

    def total_credits(self) -> Decimal:
        return sum((money(l[2]) for l in self.lines), ZERO)

    @property
    def balanced(self) -> bool:
        return self.total_debits() == self.total_credits()

    def check(self):
        """Raise unless this entry is postable. Called before it is written."""
        if not self.lines:
            raise ValueError(f"entry {self.number}: no lines")
        if not self.balanced:
            raise ValueError(
                f"entry {self.number}: debits {self.total_debits()} != credits {self.total_credits()}"
            )
        if not self.basis:
            raise ValueError(f"entry {self.number}: no basis recorded")
        return True


# ------------------------------------------------------------------ ledger

@dataclass
class Ledger:
    """Everything parsed out of one company's exports.

    A BALANCE IS AN OPENING POSITION PLUS THE MOVEMENT SINCE IT

    `lines` holds only what was posted inside the exported period, so summing
    them gives period movement and not a balance. Every account that carried
    anything into the period opens at some figure, and for a company that has
    traded before, that figure is most of the balance. `tests/test_balances.py`
    pins the shape that costs the most: an equity account with no activity in
    either exported year, so movement reads 0.00 against a balance in the
    millions.

    `opening_balances` is that opening position, at `period_start`, and
    `balance_of` is opening plus movement. It is populated from the General
    Ledger's own "Beginning Balance" rows, which is the one source that is
    stated as of the ledger's own start date. See `qbo_exports.load_all` for why
    that source and not the Account List's balance column or the trial balance.

    When nothing establishes the opening position, `opening_basis` is empty,
    `opening_of` and `balance_of` return None, and every caller has to say the
    balance could not be determined rather than print movement in its place.
    """
    accounts: dict = field(default_factory=dict)      # key -> Account
    lines: list = field(default_factory=list)         # JournalLine
    company: str = ""
    basis: str = ""
    period_start: object = None
    period_end: object = None
    sources: list = field(default_factory=list)
    # account key -> signed (debit-positive) balance at `period_start`
    opening_balances: dict = field(default_factory=dict)
    # Where those came from, in words. Empty means no opening position is known
    # for ANY account, which makes every balance undeterminable.
    opening_basis: str = ""
    opening_notes: list = field(default_factory=list)
    trial_balances: list = field(default_factory=list)   # qbo_exports.TrialBalance

    def account(self, key) -> Optional[Account]:
        if key in self.accounts:
            return self.accounts[key]
        k = norm_text(key)
        for acct in self.accounts.values():
            if norm_text(acct.full_name) == k or norm_text(acct.name) == k:
                return acct
        return None

    def by_role(self, role) -> list:
        return [a for a in self.accounts.values() if a.role == role]

    # ----------------------------------------------------------- balances

    def aliases_for(self, key) -> set:
        """Every UNAMBIGUOUS spelling of one account.

        `key`, `number` and `full_name` are each unique in a chart, so they are
        always included. The bare leaf name is included only when it is unique.
        A chart can carry `Credit Cards:Brex Card` numbered 222000 alongside
        QuickBooks' own unnumbered default `Brex card`, and matching on the leaf
        name gave one account the other's 15,803.42, which then read as a
        15,803.42 disagreement with the trial balance. `Prepaid Rent` under two
        parents is the same shape and is entirely ordinary.

        The leaf name is not simply dropped, because an unnumbered chart's
        General Ledger labels its sections with the leaf name and nothing else,
        and dropping it there would lose every line.
        """
        out = {key} if key else set()
        acct = self.account(key) if key else None
        if acct is not None:
            for alias in (acct.key, acct.number, acct.full_name):
                if alias:
                    out.add(alias)
            if acct.name and norm_text(acct.name) not in self._ambiguous_names():
                out.add(acct.name)
        return {norm_text(k) for k in out if k}

    def _ambiguous_names(self) -> set:
        """Leaf names more than one account in the chart answers to."""
        cached = getattr(self, "_ambiguous_cache", None)
        if cached is not None and cached[0] == len(self.accounts):
            return cached[1]
        seen, ambiguous = {}, set()
        for acct in self.accounts.values():
            n = norm_text(acct.name)
            if not n:
                continue
            if n in seen and seen[n] is not acct:
                ambiguous.add(n)
            seen[n] = acct
        object.__setattr__(self, "_ambiguous_cache", (len(self.accounts), ambiguous))
        return ambiguous

    def movement_of(self, key) -> Decimal:
        """What the posted lines add up to. Period movement, never a balance."""
        aliases = self.aliases_for(key)
        total = ZERO
        for line in self.lines:
            if (norm_text(line.account) in aliases
                    or norm_text(line.account_full) in aliases):
                total += line.signed
        return total

    def opening_of(self, key) -> Optional[Decimal]:
        """Balance at `period_start`, or None when nothing establishes one.

        Zero and unknown are different answers. An account with no beginning
        balance row in a ledger that HAS them opened at zero: QuickBooks prints
        the row for every account carrying a balance in, including ones with no
        activity. An account in a ledger with no opening basis at all is
        unknown, and saying zero there is the bug this method exists to stop.
        """
        if not self.opening_basis:
            return None
        aliases = self.aliases_for(key)
        for candidate, value in self.opening_balances.items():
            if norm_text(candidate) in aliases:
                return value
        return ZERO

    def balance_of(self, key) -> Optional[Decimal]:
        """Opening position plus movement. None when the opening is unknown."""
        opening = self.opening_of(key)
        if opening is None:
            return None
        return opening + self.movement_of(key)

    def balance_as_of(self, key, cutoff) -> Optional[Decimal]:
        """The same balance, restricted to lines posted on or before `cutoff`."""
        opening = self.opening_of(key)
        if opening is None:
            return None
        aliases = self.aliases_for(key)
        total = opening
        for line in self.lines:
            if line.date is None or line.date > cutoff:
                continue
            if (norm_text(line.account) in aliases
                    or norm_text(line.account_full) in aliases):
                total += line.signed
        return total

    # ------------------------------------------------- chart relationships

    def children_of(self, account) -> list:
        """Accounts sitting under this one in the chart's colon-delimited tree."""
        full = getattr(account, "full_name", "") or ""
        if not full:
            return []
        prefix = full + ":"
        return [a for a in self.accounts.values()
                if a is not account and (a.full_name or "").startswith(prefix)]

    def ever_posted(self, account) -> bool:
        """True when at least one posted line names this account.

        A zero net movement is not the test: an account can post 5,000.00 in and
        5,000.00 out and still be a real account somebody has to reconcile.
        """
        aliases = self.aliases_for(getattr(account, "key", account))
        if not aliases:
            return False
        for line in self.lines:
            if (norm_text(line.account) in aliases
                    or norm_text(line.account_full) in aliases):
                return True
        return False

    def is_rollup(self, account) -> bool:
        """True for a parent that only totals its children.

        A parent rollup carries its children's balances a second time, so
        reconciling one against a statement double counts, and asking for a
        statement for it asks for a document no institution issues. A parent
        that has postings of its own is NOT a rollup: it is a real account that
        also happens to have children, and it keeps its row.
        """
        return bool(self.children_of(account)) and not self.ever_posted(account)

    def total_debits(self) -> Decimal:
        return sum((l.debit for l in self.lines), ZERO)

    def total_credits(self) -> Decimal:
        return sum((l.credit for l in self.lines), ZERO)

    @property
    def foots(self) -> bool:
        return self.total_debits() == self.total_credits()

    def trial_balance_as_of(self, when) -> Optional[object]:
        """The loaded trial balance stated as of `when`, if one was exported."""
        for tb in self.trial_balances:
            if when is not None and getattr(tb, "as_of", None) == when:
                return tb
        return None

    def months_with_activity(self) -> dict:
        out = {}
        for l in self.lines:
            out[l.month] = out.get(l.month, 0) + 1
        return dict(sorted(out.items()))


def is_real_account(ledger, account) -> bool:
    """False for a chart row nobody can reconcile or get a statement for.

    Two shapes fail this and both are ordinary in a chart of any size:

    A PARENT ROLLUP. `100000 Current Assets` and `220000 Credit Cards` exist to
    total their children. Their balance is already stated by the children, so
    reconciling one double counts, and no institution issues a statement for a
    subtotal. `is_rollup` lets a parent through when it has postings of its own.

    AN ACCOUNT THAT HAS NEVER HELD ANYTHING. `107000 Bank Account 7`,
    `223000` through `227000 Credit Card 3` to `7` and the two Bill.com clearing
    accounts are unused rows from a chart template. Never posted, no opening
    position, no balance. Demanding a statement for one is demanding a document
    that does not exist, and it made 15 of 21 accounts in a reconciliation
    unworkable noise.

    The Account List's balance column is consulted here and ONLY here. It is
    the wrong source for the SIZE of a balance (see `qbo_exports.load_all`), but
    a non-zero figure in it is still evidence the account is in use, including
    in a period nobody exported.
    """
    if account is None:
        return False
    if ledger.is_rollup(account):
        return False
    if ledger.ever_posted(account):
        return True
    opening = ledger.opening_of(getattr(account, "key", account))
    if opening is not None and opening != ZERO:
        return True
    return getattr(account, "balance", ZERO) != ZERO


def asdict(obj):
    return dataclasses.asdict(obj)
