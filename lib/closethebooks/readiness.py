"""What the firm filing the return still has to be given.

    from closethebooks.readiness import scan, render_markdown
    found = scan(ledger, profile=prof, filing_year=2025, answers=answered)
    for req in found.outstanding:
        print(req.headline())

WHY THIS IS NOT THE EXIT TESTS AGAIN

The exit tests ask whether the bookkeeping finished. This asks whether the
return can be signed, and those are different questions with different evidence
behind them. A set of books can foot, reconcile, carry a class on every line and
pass all ten tests while nobody has yet said what the 1,174,300.55 sitting in
"Due From" a German company actually is. The books cannot answer that. Only the
owner can, and a firm that does not ask ends up asking in April, one question at
a time, by email, against a deadline.

So this module reads the chart, the ledger and what has already been recorded,
and produces the list a preparer would otherwise chase.

EVERY REQUIREMENT COMES FROM THIS COMPANY'S OWN BOOKS

There is no standard questionnaire here and there is deliberately no requirement
that fires for everyone. A single-member consulting LLC with one bank account is
asked about its contractors and nothing else. A company with a foreign
subsidiary, convertible notes in equity and payroll that stopped in April is
asked about all three, because all three are visible in its chart. Each
requirement carries `trigger`, the sentence naming what raised it, and
`evidence`, the accounts and figures behind that sentence. A requirement that
cannot name its trigger is a bug.

WHY THE MEMO FIELD IS NOT SCANNED FOR SUBJECT WORDS

Triggers read the CHART, the account roles and the posted totals. They do not
read descriptors looking for keywords, and one real file says why: searching its
memos for "token" returns 27 lines, every one of them a monthly subscription to
a login service called Tokenridge. A trigger built that way would have asked
that company about its cryptocurrency holdings, which it has none of, and the
first wrong question is what teaches somebody to skim the rest.

Names ARE read in one place, `related_party_receipts`, where the counterparty
name on a receipt is the whole signal and the entity it is matched against was
already established from the chart.

ANSWERS RAISE REQUIREMENTS

Some of what a return needs cannot be seen in a ledger at all, and is reachable
only through something the owner says. The books show payroll stopping in April
of a year that ran to December; that raises one question, was the company still
trading, and the answer "we are winding it up" raises the six that a final year
needs. The prior return raises the digital-asset question, and the cap table
raises the foreign-owner questions. So `scan` is re-run after every answer and
the list can grow. That is the design working, not a fault: a questionnaire
fixed in advance would have had to ask everybody everything.

A STATED UNKNOWN IS A RECORD. A BLANK IS NOT.

Nothing here ever fills in an answer. Where the owner cannot answer, that is
recorded as an answer of its own, with the reason and the date, and it travels
into the package as something the preparer will have to chase. The failure this
exists to prevent is the silent one, where a blank is read downstream as a zero,
a no, or a nothing-to-report.

THIS IS NOT TAX ADVICE

Nothing in this module decides a treatment, computes a liability, or says what a
return should contain. It collects what a preparer asks for and records who said
what, when. Where a governing document decides an answer, the requirement asks
for the document rather than for an opinion about it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from decimal import Decimal

from .model import is_real_account
from .sides import balance_text
from .util import ZERO, fmt, iso, money, norm_text

__all__ = [
    "Requirement", "RequirementScan", "scan", "render", "render_markdown",
    "DOCUMENT", "QUESTION", "GROUPS", "NOT_TAX_ADVICE",
]

DOCUMENT = "document"
QUESTION = "question"

NOT_TAX_ADVICE = (
    "What is collected here is what the firm filing the return has to be given. "
    "Nothing here decides a treatment, and none of it is tax advice."
)

# Every group this module can raise, with the one line that says what subject it
# covers. A group appears in a company's list only when its trigger fires.
GROUPS = (
    ("prior-return", "the last return filed, and what it said"),
    ("foreign-entity", "a related company outside the United States"),
    ("foreign-owner", "an owner outside the United States"),
    ("convertible", "convertible instruments the company issued"),
    ("convertible-held", "a convertible instrument the company holds in someone else"),
    ("ownership-roster", "who owns the company"),
    ("officer-pay", "what the officers were paid"),
    ("contractors", "people paid without going through payroll"),
    ("digital-assets", "cryptocurrency and other digital assets"),
    ("related-party-receipts", "money received from a related company or an owner"),
    ("fixed-assets", "what the equipment and property on the books are"),
    ("intangibles", "what the intangible assets are"),
    ("rd-credit", "a research credit already in the books"),
    ("meals", "meals and entertainment, which are not treated alike"),
    ("state-footprint", "the states the company was present in"),
    ("still-trading", "whether the company was still trading at the year end"),
    ("wind-down", "closing the company down"),
)

GROUP_SUBJECT = dict(GROUPS)


# --------------------------------------------------------------- vocabulary

# Company-form suffixes from outside the United States. Two lists, because the
# short ones are also ordinary English. A long form matches as a whole token
# anywhere in the name; a short one only as the LAST token, which is where a
# company form actually sits.
_FOREIGN_LONG = (
    "gmbh", "sarl", "s.a.r.l", "s.a.r.l.", "aktiengesellschaft", "kabushiki",
    "limited", "ltda", "pte", "pty", "aps", "oyj", "s.p.a", "s.p.a.", "s.r.l",
    "s.r.l.", "s.r.o", "s.r.o.", "b.v", "b.v.", "n.v", "n.v.", "s.a", "s.a.",
    "s.a.s", "s.a.s.", "kft", "sp.z.o.o", "ug", "ohg", "unlimited",
)
_FOREIGN_SHORT = ("ag", "ab", "bv", "nv", "oy", "kk", "ltd", "plc", "sas",
                  "sasu", "kg", "srl", "spa", "sro", "oü", "uab")

# A name saying this account holds a relationship with another company rather
# than being an ordinary asset. The foreign trigger needs BOTH this and a
# foreign form, because "Vantage Card" and "Northgate Checking" would otherwise
# be one loose regex away from a question about controlled foreign companies.
_RELATIONSHIP = re.compile(
    r"\b(due (from|to)|intercompany|inter-company|subsidiar(y|ies)|affiliate|"
    r"investments? (in|into)|loan (to|from)|receivable from|payable to|"
    r"advances? to)\b", re.I)

_CONVERTIBLE = re.compile(
    r"\b(safe|simple agreement for future equity|convertible|conv\.? note[s]?|"
    r"kiss)\b", re.I)

_PAYROLL_EXPENSE = re.compile(
    r"\b(wages?|salar(y|ies)|payroll|officer compensation|compensation of "
    r"officers)\b", re.I)

_CONTRACTOR = re.compile(
    r"\b(contractor[s]?|consultant[s]?|contract (labor|labour|engineering|"
    r"work)|subcontractor[s]?|freelance[r]?[s]?|1099)\b", re.I)

_DIGITAL_ASSET = re.compile(
    r"\b(crypto(currency)?|bitcoin|ethereum|digital asset[s]?|stablecoin|"
    r"usdc|nft[s]?|coinbase|wallet)\b", re.I)

_RD_CREDIT = re.compile(
    r"\b(r ?& ?d|research (and|&) development|research)\b.{0,24}\bcredit\b", re.I)

_MEALS = re.compile(r"\b(meals?|entertainment|dining)\b", re.I)

_RENT = re.compile(r"\b(rent|lease|office space)\b", re.I)

_INTANGIBLE_ROLES = ("intangible",)

# Accumulated depreciation is typed as a fixed asset and is not one. It is the
# running total written off against the assets above it, so asking what it is
# and when it was bought asks about a subtotal.
_ACCUMULATED = re.compile(r"\baccumulated\s+(depreciation|amortization|amortisation)\b",
                          re.I)

# Words in an answer that mean the company is being closed. Matched only
# against the answer to `still-trading`, which is a yes-or-no question about
# one thing, so a loose match here cannot reach anything else.
_WINDING_UP = re.compile(
    r"\b(wind(ing)? (up|down)|wound (up|down)|dissolv|dissolution|shut(ting)? "
    r"down|closing (the company|down)|ceased|liquidat|final year)\b", re.I)

_YES = re.compile(r"^\s*(yes|y|true|we did|it did|correct)\b", re.I)

# Answers that record nothing. `--cannot` needs a reason, and these are not
# reasons: they are the blank this module exists to stop.
_EMPTY_REASON = {"", "unknown", "n/a", "na", "none", "no idea", "dont know",
                 "do not know", "-", "?", "tbd", "idk"}


def is_empty_reason(text) -> bool:
    """True when a stated unknown states nothing.

    A recorded unknown travels into the package as something the preparer has
    to chase, and "unknown" is not a thing anybody can chase. The reason has to
    say what would settle it or who holds it.
    """
    cleaned = re.sub(r"[^a-z ]", "", str(text or "").strip().lower()).strip()
    return cleaned in _EMPTY_REASON or len(cleaned) < 8


# ------------------------------------------------------------- requirements

@dataclass
class Requirement:
    """One thing the preparer needs, and what in these books called for it."""

    id: str
    group: str
    kind: str = QUESTION           # DOCUMENT or QUESTION
    need: str = ""                 # what is needed, in a sentence
    why: str = ""                  # why the return needs it
    trigger: str = ""              # what in this company's books raised it
    evidence: list = field(default_factory=list)   # account/figure strings
    raised_by: str = "the books"   # "the books" or "an answer already given"

    # Filled from what has already been recorded. Never written here.
    answer: str = ""
    answered_on: str = ""
    source: str = ""
    unknown: bool = False
    unknown_reason: str = ""
    document: dict = field(default_factory=dict)

    @property
    def outstanding(self) -> bool:
        """Nothing has been recorded against it. This is what `ready` refuses on."""
        return not self.answer and not self.unknown

    @property
    def settled(self) -> bool:
        return bool(self.answer) and not self.unknown

    @property
    def wanted(self) -> str:
        return "a document to upload" if self.kind == DOCUMENT else "a question to answer"

    def headline(self) -> str:
        return self.need

    def status_word(self) -> str:
        if self.unknown:
            return "recorded as unanswerable"
        if self.document:
            return f"document recorded, {self.document.get('stored') or 'filed'}"
        if self.answer:
            return "answered"
        return "outstanding"

    def lines(self) -> list:
        out = [self.need,
               f"Why the return needs it: {self.why}",
               f"What raised it: {self.trigger}"]
        if self.evidence:
            out.append("In your books: " + "; ".join(self.evidence) + ".")
        out.append(f"This is {self.wanted}.")
        if self.unknown:
            out.append(f"Recorded as unanswerable on {self.answered_on} by "
                       f"{self.source or 'whoever was asked'}: {self.unknown_reason} "
                       f"The firm filing the return will have to chase this.")
        elif self.document:
            out.append(f"Given on {self.answered_on} by {self.source}: "
                       f"{self.document.get('stored')}, "
                       f"{self.document.get('bytes', 0):,} bytes.")
        elif self.answer:
            out.append(f"Answered on {self.answered_on} by {self.source}: "
                       f"{self.answer}")
        return out

    def as_row(self) -> dict:
        return {
            "id": self.id,
            "subject": GROUP_SUBJECT.get(self.group, self.group),
            "needed": "document" if self.kind == DOCUMENT else "answer",
            "what": self.need,
            "why": self.why,
            "raised by": self.trigger,
            "status": self.status_word(),
            "on": self.answered_on,
            "who": self.source,
        }

    def as_question(self) -> dict:
        """The shape `profile.open_questions` holds, so one store answers both.

        A filing requirement is a question the owner has to answer, which is
        exactly what that list already is. Keeping them there means
        `books.py questions`, `books.py answer` and the round machinery work on
        these with no second code path and no second place to look.
        """
        return {
            "id": self.id,
            "question": self.need,
            "why": self.why,
            "kind": self.kind,
            "group": self.group,
            "trigger": self.trigger,
            "evidence": list(self.evidence),
            "blocks": ["the return"],
            "source_of_requirement": "readiness",
        }


@dataclass
class RequirementScan:
    requirements: list = field(default_factory=list)
    filing_year: object = None
    accounts_read: int = 0
    notes: list = field(default_factory=list)     # what could not be looked at

    @property
    def outstanding(self) -> list:
        return [r for r in self.requirements if r.outstanding]

    @property
    def settled(self) -> list:
        return [r for r in self.requirements if r.settled]

    @property
    def unknowns(self) -> list:
        return [r for r in self.requirements if r.unknown]

    @property
    def documents(self) -> list:
        return [r for r in self.requirements if r.kind == DOCUMENT]

    @property
    def questions(self) -> list:
        return [r for r in self.requirements if r.kind == QUESTION]

    @property
    def groups(self) -> list:
        out = []
        for r in self.requirements:
            if r.group not in out:
                out.append(r.group)
        return out

    def of_group(self, group) -> list:
        return [r for r in self.requirements if r.group == group]

    def get(self, req_id):
        for r in self.requirements:
            if r.id == req_id:
                return r
        return None

    def resolve(self, given):
        """One requirement matching `given` exactly, or by unique id suffix.

        Ids are long because they say what they are about, and nobody should
        have to retype `filing.foreign-entity.vesterhavn-systems.loan-or-trade`
        to answer it. `loan-or-trade` resolves when it is unambiguous and
        refuses when it is not, which is the same rule a shell uses.
        """
        text = str(given or "").strip()
        exact = self.get(text)
        if exact is not None:
            return exact, []
        hits = [r for r in self.requirements
                if r.id.endswith("." + text) or r.id.endswith(text)]
        if len(hits) == 1:
            return hits[0], []
        return None, [r.id for r in hits]

    def __bool__(self) -> bool:
        return bool(self.requirements)

    def __len__(self) -> int:
        return len(self.requirements)

    def __iter__(self):
        return iter(self.requirements)


# ------------------------------------------------------------------ helpers

def _leaf(name) -> str:
    return str(name or "").split(":")[-1].strip()


def _tokens(name) -> list:
    return [t for t in re.split(r"[^A-Za-z0-9&.üöäåø]+", str(name or "").lower()) if t]


def foreign_form(name) -> str:
    """The company form outside the United States in this name, or "".

    Public because the two-list rule is the thing a reader most wants to check.
    A long form counts anywhere; a short one only as the last token, which is
    the only place a company form sits and which keeps "AB Testing Expense"
    from reading as a Swedish company.
    """
    toks = _tokens(_leaf(name))
    if not toks:
        return ""
    for tok in toks:
        if tok in _FOREIGN_LONG:
            return tok
    if toks[-1] in _FOREIGN_SHORT:
        return toks[-1]
    return ""


def _entity_name(account_name) -> str:
    """The other company's name, taken out of the account's own label."""
    leaf = _leaf(account_name)
    cut = re.sub(_RELATIONSHIP.pattern, "", leaf, flags=re.I).strip(" -:,")
    cut = re.sub(r"\s{2,}", " ", cut)
    return cut or leaf


def _slug(text, words=2) -> str:
    parts = [re.sub(r"[^a-z0-9]", "", w) for w in str(text or "").lower().split()]
    parts = [p for p in parts if p][:words]
    return "-".join(parts) or "entity"


def _balance(ledger, key):
    try:
        return ledger.balance_of(key)
    except Exception:                       # pragma: no cover - defensive
        return None


def _movement_in_year(ledger, key, year) -> Decimal:
    if year is None:
        return ledger.movement_of(key)
    aliases = ledger.aliases_for(key)
    total = ZERO
    for line in ledger.lines:
        if line.date is None or line.date.year != int(year):
            continue
        if (norm_text(line.account) in aliases
                or norm_text(line.account_full) in aliases):
            total += line.signed
    return total


def _live_accounts(ledger):
    """Chart rows worth reading. A template row nobody ever used is not one.

    Cached on the ledger, keyed on the account and line counts, because every
    subject calls this and every call walks the whole ledger twice per account.
    """
    shape = (len(ledger.accounts), len(ledger.lines))
    cached = getattr(ledger, "_readiness_live_cache", None)
    if cached is not None and cached[0] == shape:
        return cached[1]
    out = []
    for key, acct in sorted(ledger.accounts.items(), key=lambda kv: str(kv[0])):
        if ledger.is_rollup(acct):
            continue
        if not is_real_account(ledger, acct):
            continue
        out.append((key, acct))
    object.__setattr__(ledger, "_readiness_live_cache", (shape, out))
    return out


def _shows(ledger, key, acct, year) -> str:
    """One evidence string: the account, and the figure that made it interesting."""
    if acct.is_balance_sheet:
        return f"{acct.label()} holds {balance_text(_balance(ledger, key), acct)}"
    moved = _movement_in_year(ledger, key, year)
    when = f"in {year}" if year else "across the exported period"
    return f"{acct.label()} carries {fmt(abs(moved))} {when}"


def _interesting(ledger, key, acct, year) -> bool:
    """Whether this account has anything in it worth asking about.

    A balance-sheet account is read on its year-end balance, a profit-and-loss
    account on what moved inside the year being filed. The difference matters:
    an "Uncategorized Income" line carrying 7,605.00 from the year BEFORE the
    one being filed is not a question about this return, and asking it anyway
    is how a list of real questions turns into a list somebody skims.
    """
    if acct.is_balance_sheet:
        bal = _balance(ledger, key)
        return bal is not None and bal != ZERO
    return _movement_in_year(ledger, key, year) != ZERO


def _prior_year_balances(ledger) -> bool:
    """Whether the company carried anything into the exported period."""
    if not getattr(ledger, "opening_basis", ""):
        return False
    return any(money(v) != ZERO for v in (ledger.opening_balances or {}).values())


# ------------------------------------------------------------ group builders
#
# One function per subject. Each returns a list of Requirements or an empty
# list, and each names its own trigger in the same sentence it would say out
# loud. Nothing here reads an answer except through `answers`, and nothing
# writes one.

def _req(group, slug, kind, need, why, trigger, evidence=(), raised_by="the books"):
    return Requirement(
        id=f"filing.{group}.{slug}", group=group, kind=kind, need=need, why=why,
        trigger=trigger, evidence=list(evidence), raised_by=raised_by)


def _prior_return(ledger, year, answers) -> list:
    if not _prior_year_balances(ledger):
        return []
    opened = sum(1 for v in ledger.opening_balances.values() if money(v) != ZERO)
    trigger = (f"{opened} account(s) open the exported period with a balance already "
               f"on them, so the company traded before {year or 'this year'} and a "
               f"return was filed for that.")
    out = [
        _req("prior-return", "copy", DOCUMENT,
             "Last year's filed federal return, complete, with every schedule and "
             "attachment that went with it.",
             "The preparer starts this year's return from last year's. The figures "
             "carried forward have to match what the books now open with, and the "
             "choices made last year govern this one.",
             trigger,
             [f"{opened} account(s) carry an opening balance as of "
              f"{iso(ledger.period_start)}"]),
        _req("prior-return", "digital-assets", QUESTION,
             "Did last year's return say the company held, received or used any "
             "digital assets, such as cryptocurrency?",
             "The return asks that question every year. Answering it one way last "
             "year and the other way this year without a reason is the kind of "
             "difference that gets a return looked at.",
             trigger),
        _req("prior-return", "information-returns", QUESTION,
             "Which extra forms were filed alongside last year's return, and who "
             "filed them?",
             "Anything filed last year is usually filed again this year. The "
             "preparer needs the list to know what would be missing.",
             trigger),
    ]
    return out


def _foreign_entities(ledger, profile, year) -> list:
    """Every related company outside the United States the chart names."""
    found = {}
    for key, acct in _live_accounts(ledger):
        name = acct.full_name or acct.name
        form = foreign_form(name)
        if not form:
            continue
        if not _RELATIONSHIP.search(_leaf(name)):
            continue
        if not _interesting(ledger, key, acct, year):
            continue
        entity = _entity_name(name)
        found.setdefault(_slug(entity), {"name": entity, "accounts": [], "form": form})
        found[_slug(entity)]["accounts"].append((key, acct))
    for entry in (getattr(profile, "related_entities", None) or []):
        if not isinstance(entry, dict) or not entry.get("foreign"):
            continue
        name = str(entry.get("name") or "").strip()
        if name and _slug(name) not in found:
            found[_slug(name)] = {"name": name, "accounts": [], "form": "declared"}
    return list(found.values())


def _foreign_entity_group(ledger, profile, year) -> list:
    out = []
    for entity in _foreign_entities(ledger, profile, year):
        name = entity["name"]
        slug = _slug(name)
        evidence = [_shows(ledger, key, acct, year)
                    for key, acct in entity["accounts"]]
        balances = [(_balance(ledger, key), acct)
                    for key, acct in entity["accounts"] if acct.is_balance_sheet]
        biggest = max(balances, key=lambda b: abs(b[0] or ZERO), default=(None, None))
        amount = balance_text(biggest[0], biggest[1]) if biggest[0] is not None else ""
        if entity["accounts"]:
            trigger = (f"The chart carries {len(entity['accounts'])} account(s) naming "
                       f"{name}, a company registered outside the United States, and "
                       f"they are not empty.")
        else:
            trigger = f"{name} is recorded as a related company outside the United States."

        def r(slugged, kind, need, why):
            return _req("foreign-entity", f"{slug}.{slugged}", kind, need, why,
                        trigger, evidence)

        out += [
            r("balance", QUESTION,
              f"Confirm what the company was owed by or owed to {name} at the year "
              f"end" + (f", which the books put at {amount}" if amount else "")
              + ", and say what that figure is made up of.",
              "A balance between two related companies is reported by both of them, "
              "and the two have to agree. The preparer cannot work out what a single "
              "running total is made of from the ledger alone."),
            r("loan-or-trade", QUESTION,
              f"Is the money that moved between the company and {name} a loan, or is "
              f"it payment for work {name} did?",
              "The two are reported differently and nothing in the books settles "
              "which it is. A running account that was never called a loan and never "
              "invoiced is the case that has to be sorted out before the return."),
            r("agreement", DOCUMENT,
              f"The signed agreement covering the money between the company and "
              f"{name}, whichever it is: a loan agreement, or the services agreement "
              f"and its invoices.",
              "The document decides how this is reported. Without it the answer above "
              "is an intention, and an intention is not a position anybody can file."),
            r("interest", QUESTION,
              f"Does the company charge {name} interest, at what rate, and was any "
              f"charged, paid or accrued during the year?",
              "Money lent between related companies is expected to carry interest, "
              "and if any was charged it belongs in both companies' figures."),
            r("accounts", DOCUMENT,
              f"{name}'s own year-end accounts for the year, in the currency it keeps "
              f"them in: its profit and loss, and its balance sheet.",
              "A company that owns a company abroad reports that company's own "
              "figures with its return. They come from the subsidiary's books, not "
              "from ours."),
            r("ownership", QUESTION,
              f"What share of {name} does the company own, who owns the rest, and on "
              f"what date was it formed or bought?",
              "The share owned decides what has to be reported about it, and the date "
              "decides which year that starts in."),
        ]
    return out


def _foreign_owner_group(profile, answers) -> list:
    """Raised by an answer, not by the books. A cap table is not in a ledger."""
    declared = []
    for entry in (getattr(profile, "owners", None) or []):
        if not isinstance(entry, dict):
            continue
        country = str(entry.get("country") or "").strip().upper()
        share = entry.get("percent")
        try:
            share = float(share)
        except (TypeError, ValueError):
            share = None
        if country and country not in ("US", "USA", "UNITED STATES") and (
                share is None or share >= 25):
            declared.append(entry)

    roster = (answers or {}).get("filing.ownership-roster.cap-table") or {}
    said = str(roster.get("answer") or "")
    from_answer = bool(re.search(
        r"\b(non-?us|non-?resident|foreign|overseas|abroad)\b", said, re.I))

    if not declared and not from_answer:
        return []
    if declared:
        who = ", ".join(str(e.get("name") or "an owner") for e in declared)
        trigger = (f"The recorded ownership shows {who} outside the United States at "
                   f"25 percent or more.")
        raised = "the books"
    else:
        trigger = ("The cap table given for this company names an owner outside the "
                   "United States.")
        raised = "an answer already given"

    def r(slug, kind, need, why):
        return _req("foreign-owner", slug, kind, need, why, trigger,
                    raised_by=raised)

    return [
        r("owners", QUESTION,
          "Name every owner outside the United States, the share each one holds, and "
          "the country each one is in.",
          "A company owned a quarter or more from outside the United States files an "
          "extra return naming those owners. It cannot be prepared without the list."),
        r("transactions", QUESTION,
          "List every payment, loan, repayment, sale, purchase or reimbursement "
          "between the company and each of those owners during the year, with dates "
          "and amounts.",
          "That extra return reports each of those movements separately. A total will "
          "not do, and the ledger does not group them by owner."),
        r("agreements", DOCUMENT,
          "Any agreement between the company and those owners: a loan, a services "
          "agreement, a licence, or a management fee arrangement.",
          "Each agreement decides how the movements above are reported, and the "
          "preparer has to read them rather than be told about them."),
    ]


def _convertible_group(ledger, year) -> list:
    issued, held = [], []
    for key, acct in _live_accounts(ledger):
        name = acct.full_name or acct.name
        if not (_CONVERTIBLE.search(_leaf(name)) or acct.role == "safe"):
            continue
        if not _interesting(ledger, key, acct, year):
            continue
        if acct.type in ("equity", "other current liability", "long term liability"):
            issued.append((key, acct))
        elif acct.type in ("other asset", "other current asset", "fixed asset"):
            held.append((key, acct))
        else:
            issued.append((key, acct))

    out = []
    if issued:
        evidence = [_shows(ledger, key, acct, year) for key, acct in issued]
        where = ", ".join(sorted({a.type for _, a in issued}))
        trigger = (f"{len(issued)} account(s) named for convertible instruments sit in "
                   f"{where} and carry a balance.")
        out += [
            _req("convertible", "instrument", DOCUMENT,
                 "Every signed convertible instrument the company has issued, as "
                 "signed, including any that were signed years ago and are still "
                 "outstanding.",
                 "Where one of these sits on the balance sheet is decided by what the "
                 "document says, and no tool can work that out without reading it. "
                 "Moving one changes the balance sheet the return is built from.",
                 trigger, evidence),
            _req("convertible", "schedule", QUESTION,
                 "For each instrument: who put the money in, how much, and on what "
                 "date.",
                 "The total in the books is one figure. The return and the cap table "
                 "need it broken down by holder and date.",
                 trigger, evidence),
            _req("convertible", "side-letters", DOCUMENT,
                 "Any amendment, side letter or extension signed after the original "
                 "instrument, or say in writing that there are none.",
                 "A side letter can change the terms that decide the treatment, and a "
                 "missing one is only discovered when somebody goes looking for it "
                 "later.",
                 trigger, evidence),
            _req("convertible", "events", QUESTION,
                 "Did any of these convert, get repaid, get cancelled or expire "
                 "during the year, and on what date?",
                 "Any of those is an event inside the year that the return has to "
                 "show, and the balance carrying forward unchanged does not prove "
                 "none of them happened.",
                 trigger, evidence),
        ]
    if held:
        evidence = [_shows(ledger, key, acct, year) for key, acct in held]
        names = ", ".join(_leaf(a.full_name or a.name) for _, a in held)
        trigger = (f"{len(held)} asset account(s) hold convertible instruments the "
                   f"company owns in another company: {names}.")
        out += [
            _req("convertible-held", "instrument", DOCUMENT,
                 "The signed instrument for each convertible the company holds in "
                 "another company.",
                 "The company owns this, so the return has to say what it is worth "
                 "and on what basis. The document is the basis.",
                 trigger, evidence),
            _req("convertible-held", "status", QUESTION,
                 "Is each one still outstanding, or did it convert into shares, get "
                 "repaid, or become worthless during the year?",
                 "If it converted or became worthless, that happened inside this year "
                 "and belongs on this return.",
                 trigger, evidence),
        ]
    return out


def _ownership_roster_group(ledger, year) -> list:
    hits = [(key, acct) for key, acct in _live_accounts(ledger)
            if acct.type == "equity"
            and not re.search(r"opening balance equity|retained earnings",
                              _leaf(acct.full_name or acct.name), re.I)
            and _interesting(ledger, key, acct, year)]
    if not hits:
        return []
    evidence = [_shows(ledger, key, acct, year) for key, acct in hits]
    trigger = (f"{len(hits)} equity account(s) carry a balance, so somebody owns "
               f"shares in this company.")
    return [
        _req("ownership-roster", "cap-table", DOCUMENT,
             "The current cap table: every holder, what they hold, how much they put "
             "in, and the country each holder is in.",
             "The return reports who owns the company and how much of it, and several "
             "other questions only get asked once the owners are known. The ledger "
             "records the money that came in and never records who it came from.",
             trigger, evidence),
    ]


def _officer_pay_group(ledger, year) -> list:
    hits = [(key, acct) for key, acct in _live_accounts(ledger)
            if not acct.is_balance_sheet
            and _PAYROLL_EXPENSE.search(_leaf(acct.full_name or acct.name))
            and _interesting(ledger, key, acct, year)]
    if not hits:
        return []
    evidence = [_shows(ledger, key, acct, year) for key, acct in hits]
    total = sum((abs(_movement_in_year(ledger, key, year)) for key, _ in hits), ZERO)
    trigger = (f"The books ran payroll during the year: {len(hits)} payroll account(s) "
               f"carry {fmt(total)} between them.")
    return [
        _req("officer-pay", "split", QUESTION,
             "Of the wages the books show, how much went to officers of the company? "
             "Name each officer and the amount.",
             "A corporate return states what the officers were paid on a line of its "
             "own, separately from everybody else's wages. The ledger has one wages "
             "total and no way to split it.",
             trigger, evidence),
        _req("officer-pay", "other-pay", QUESTION,
             "Was any owner or officer paid anything during the year that did not go "
             "through payroll, such as a transfer, a reimbursement above expenses, or "
             "a loan?",
             "Money out to an owner that skipped payroll is reported differently and "
             "often sits in the books as an ordinary payment with nothing marking it.",
             trigger, evidence),
    ]


def _contractor_group(ledger, year) -> list:
    hits = [(key, acct) for key, acct in _live_accounts(ledger)
            if not acct.is_balance_sheet
            and _CONTRACTOR.search(_leaf(acct.full_name or acct.name))
            and _interesting(ledger, key, acct, year)]
    if not hits:
        return []
    evidence = [_shows(ledger, key, acct, year) for key, acct in hits]
    total = sum((abs(_movement_in_year(ledger, key, year)) for key, _ in hits), ZERO)
    trigger = (f"{fmt(total)} went to contractors and consultants during the year, "
               f"across {len(hits)} account(s).")
    return [
        _req("contractors", "list", QUESTION,
             "Who was paid as a contractor during the year, and how much did each one "
             "get? Include anyone paid through a payment app or a card.",
             "Anyone paid six hundred dollars or more for work has to be told what "
             "they were paid, and so does the government, on a form due before the "
             "return. The ledger has the amounts and usually not the people.",
             trigger, evidence),
        _req("contractors", "w9", DOCUMENT,
             "The tax form each of those contractors filled in before they were paid, "
             "the W-9, or a list of the ones you do not have.",
             "That form carries the name, address and tax number the filing needs. "
             "Chasing it after the year has ended is the single most common reason "
             "this deadline slips.",
             trigger, evidence),
    ]


def _digital_assets_group(ledger, year, answers) -> list:
    hits = [(key, acct) for key, acct in _live_accounts(ledger)
            if _DIGITAL_ASSET.search(_leaf(acct.full_name or acct.name))
            and _interesting(ledger, key, acct, year)]
    prior = (answers or {}).get("filing.prior-return.digital-assets") or {}
    said_yes = bool(_YES.match(str(prior.get("answer") or "")))

    if hits:
        evidence = [_shows(ledger, key, acct, year) for key, acct in hits]
        names = ", ".join(_leaf(a.full_name or a.name) for _, a in hits)
        trigger = f"The chart carries {len(hits)} account(s) named for digital assets: {names}."
        raised = "the books"
    elif said_yes:
        evidence = [f"the answer given on {prior.get('answered_on') or 'an earlier date'} "
                    f"by {prior.get('source') or 'the owner'}"]
        trigger = ("The last return filed reported that the company held or used "
                   "digital assets, so this year's return has to answer the same "
                   "question again.")
        raised = "an answer already given"
    else:
        return []

    return [
        _req("digital-assets", "activity", QUESTION,
             "During the year, did the company receive, sell, swap, spend or give "
             "away any digital asset? If it did, say what and roughly when.",
             "The return asks this on its front page and the answer has to be right. "
             "Selling or spending one is a disposal that has to be worked out and "
             "reported.",
             trigger, evidence, raised),
        _req("digital-assets", "history", DOCUMENT,
             "The full transaction history for the year from every exchange and every "
             "wallet the company used, exported as a file.",
             "Each disposal needs what was paid for the asset and what it was worth "
             "when it went. Only the exchange or the wallet has both.",
             trigger, evidence, raised),
    ]


def _related_party_receipts_group(ledger, profile, year) -> list:
    """Money RECEIVED from a company or person the books already show is related.

    The counterparty name is read here, and only here, because it is the whole
    signal: money arriving from a name the chart already carries an
    intercompany account for is either revenue or a repayment, and the ledger
    cannot say which.

    Direction is the whole of this check and it is easy to get backwards. The
    engine is debit-positive, so money ARRIVING is a DEBIT on a bank or card and
    a CREDIT on a revenue account. An earlier draft of this tested the credit
    side alone and matched the bank leg of six payments the company SENT to its
    own subsidiary, then described them as receipts. Nothing in the totals looks
    wrong when that happens, which is why both legs are named explicitly here.
    """
    entities = _foreign_entities(ledger, profile, year)
    names = [e["name"] for e in entities]
    for key, acct in _live_accounts(ledger):
        if acct.role in ("intercompany", "shareholder") and _RELATIONSHIP.search(
                _leaf(acct.full_name or acct.name)):
            names.append(_entity_name(acct.full_name or acct.name))
    stems = {norm_text(n).split()[0] for n in names if norm_text(n).split()}
    if not stems:
        return []

    receipts = []
    for line in ledger.lines:
        if line.date is None or (year is not None and line.date.year != int(year)):
            continue
        who = norm_text(f"{line.name} {line.memo}")
        if not who or not any(stem in who for stem in stems):
            continue
        acct = ledger.account(line.account)
        role = getattr(acct, "role", "")
        if role in ("bank", "card") and line.signed > ZERO:
            receipts.append((line, "arrived in"))
        elif role == "revenue" and line.signed < ZERO:
            receipts.append((line, "was booked as income in"))
    if not receipts:
        return []

    total = sum((abs(l.signed) for l, _ in receipts), ZERO)
    shown = sorted(receipts, key=lambda pair: abs(pair[0].signed), reverse=True)[:5]
    evidence = [f"{iso(l.date)}, {fmt(abs(l.signed))} {where} {l.account}, "
                f"{(l.memo or l.name or '').strip()[:60]}" for l, where in shown]
    trigger = (f"{len(receipts)} receipt(s) totalling {fmt(total)} came in from a name "
               f"the chart already carries a related-company account for.")
    return [
        _req("related-party-receipts", "nature", QUESTION,
             "For each of the receipts listed below, is this money the company earned, "
             "or is it money moving back from a related company?",
             "One is income on the return and the other is not. The books record the "
             "money arriving and record nothing that separates the two.",
             trigger, evidence),
    ]


def _fixed_assets_group(ledger, year) -> list:
    """Equipment and property on the books, which a return writes off over years.

    Intangibles are excluded and have their own subject: QuickBooks types a
    domain and a patent as fixed assets, and what a preparer needs to know about
    a domain is not what they need to know about a van. Accumulated depreciation
    is excluded too, because it is a subtotal of what has already been written
    off rather than a thing anybody bought.
    """
    hits = []
    for key, acct in _live_accounts(ledger):
        if acct.type not in ("fixed asset",):
            continue
        if acct.role in _INTANGIBLE_ROLES or acct.role == "amortization":
            continue
        name = _leaf(acct.full_name or acct.name)
        if _ACCUMULATED.search(name):
            continue
        if not _interesting(ledger, key, acct, year):
            continue
        hits.append((key, acct))
    if not hits:
        return []
    evidence = [_shows(ledger, key, acct, year) for key, acct in hits]
    names = ", ".join(_leaf(a.full_name or a.name) for _, a in hits)
    trigger = (f"{len(hits)} account(s) hold equipment or property the company owns: "
               f"{names}.")
    return [
        _req("fixed-assets", "schedule", QUESTION,
             "For each thing in these accounts, say what it is, what was paid for "
             "it, and the month it was first put to use. Say too whether anything "
             "was sold, scrapped or given away during the year.",
             "The return writes these off over several years, and that needs the "
             "date each one started and what it cost. One balance covering several "
             "purchases at different times cannot be split by anyone but you.",
             trigger, evidence),
        _req("fixed-assets", "invoice", DOCUMENT,
             "The invoice or bill of sale for anything in these accounts that was "
             "bought during the year.",
             "It is what proves the amount and the date, and it is the first thing "
             "asked for if the figure is ever questioned.",
             trigger, evidence),
    ]


def _intangibles_group(ledger, year) -> list:
    hits = [(key, acct) for key, acct in _live_accounts(ledger)
            if acct.role in _INTANGIBLE_ROLES and _interesting(ledger, key, acct, year)]
    if not hits:
        return []
    evidence = [_shows(ledger, key, acct, year) for key, acct in hits]
    names = ", ".join(_leaf(a.full_name or a.name) for _, a in hits)
    trigger = f"{len(hits)} intangible asset account(s) carry a balance: {names}."
    return [
        _req("intangibles", "what", QUESTION,
             "For each intangible asset on the books, say what it actually is, "
             "whether it was bought from somebody or made in-house, and the date it "
             "was bought or finished.",
             "What it is and how it was got decide how it is written off on the "
             "return. An account name is not enough to tell them apart.",
             trigger, evidence),
        _req("intangibles", "invoice", DOCUMENT,
             "The purchase agreement, invoice or assignment for each intangible asset "
             "that was bought.",
             "The document is what proves the amount on the books and the date it "
             "starts from.",
             trigger, evidence),
    ]


def _rd_credit_group(ledger, year) -> list:
    hits = [(key, acct) for key, acct in _live_accounts(ledger)
            if _RD_CREDIT.search(_leaf(acct.full_name or acct.name))
            and _interesting(ledger, key, acct, year)]
    if not hits:
        return []
    evidence = [_shows(ledger, key, acct, year) for key, acct in hits]
    trigger = (f"{len(hits)} account(s) in the books are named for a research credit "
               f"and are not empty.")
    return [
        _req("rd-credit", "study", DOCUMENT,
             "The study or the computation behind the research credit already sitting "
             "in the books, whoever prepared it.",
             "The figure in the books came from somewhere. The preparer needs that "
             "working, because the return carries the credit and the working is what "
             "supports it.",
             trigger, evidence),
        _req("rd-credit", "claimed", QUESTION,
             "Which return claimed this credit, and was it taken against payroll tax "
             "or carried forward?",
             "Those are two different things with two different pieces of paper "
             "behind them, and the books show the same number either way.",
             trigger, evidence),
        _req("rd-credit", "collected", QUESTION,
             "Has any of the amount the books show as receivable actually come in, "
             "and when?",
             "A receivable that was collected, reduced or written off changes the "
             "balance sheet the return is built from.",
             trigger, evidence),
    ]


def _meals_group(ledger, year) -> list:
    hits = [(key, acct) for key, acct in _live_accounts(ledger)
            if not acct.is_balance_sheet
            and _MEALS.search(_leaf(acct.full_name or acct.name))
            and _interesting(ledger, key, acct, year)]
    if not hits:
        return []
    evidence = [_shows(ledger, key, acct, year) for key, acct in hits]
    names = ", ".join(_leaf(a.full_name or a.name) for _, a in hits)
    trigger = f"{len(hits)} expense account(s) cover meals or entertainment: {names}."
    return [
        _req("meals", "split", QUESTION,
             "How much of the meals and entertainment total was entertainment, such "
             "as tickets, an outing or a party, rather than food?",
             "The two are treated differently on the return and the books put them in "
             "the same place.",
             trigger, evidence),
    ]


def _state_footprint_group(ledger, year) -> list:
    payroll = [(key, acct) for key, acct in _live_accounts(ledger)
               if not acct.is_balance_sheet
               and _PAYROLL_EXPENSE.search(_leaf(acct.full_name or acct.name))
               and _interesting(ledger, key, acct, year)]
    rent = [(key, acct) for key, acct in _live_accounts(ledger)
            if not acct.is_balance_sheet
            and _RENT.search(_leaf(acct.full_name or acct.name))
            and _interesting(ledger, key, acct, year)]
    if not payroll and not rent:
        return []
    evidence = [_shows(ledger, key, acct, year) for key, acct in (payroll + rent)]
    parts = []
    if payroll:
        parts.append("paid wages")
    if rent:
        parts.append("paid rent")
    trigger = ("The books show the company " + " and ".join(parts)
               + " during the year, which means it was somewhere.")
    return [
        _req("state-footprint", "states", QUESTION,
             "Which states did the company have people, an office, or property in "
             "during the year, and from roughly when to when in each?",
             "A state return is due wherever the company was present, and the federal "
             "return is not the only filing. The ledger shows the rent and the wages "
             "and never shows the state.",
             trigger, evidence),
    ]


def _still_trading_group(ledger, profile, year, answers) -> list:
    """Payroll that stops long before the year does is a question, not a fact."""
    if year is None:
        return []
    months = []
    for key, acct in _live_accounts(ledger):
        if acct.is_balance_sheet:
            continue
        if not _PAYROLL_EXPENSE.search(_leaf(acct.full_name or acct.name)):
            continue
        aliases = ledger.aliases_for(key)
        for line in ledger.lines:
            if line.date is None or line.date.year != int(year):
                continue
            if (norm_text(line.account) in aliases
                    or norm_text(line.account_full) in aliases):
                months.append(line.date)
    if not months:
        return []
    last = max(months)
    quiet = (12 - last.month)
    if quiet < 3:
        return []
    trigger = (f"Payroll runs to {iso(last)} and then stops, while the year being "
               f"filed runs to the end of December: {quiet} month(s) with wages in "
               f"neither the books nor anywhere else.")
    return [
        _req("still-trading", "status", QUESTION,
             f"Was the company still trading at the end of {year}, or was it being "
             f"closed down?",
             "A final year is prepared differently from an ordinary one, and payroll "
             "stopping mid-year is the only thing in the books that hints either way. "
             "It could equally be a company that let its last employee go and carried "
             "on.",
             trigger,
             [f"the last wages line in {year} is dated {iso(last)}"]),
    ]


def _wind_down_group(profile, year, answers) -> list:
    """Raised by the answer to `still-trading`, or by a wind-down already declared."""
    said = str(((answers or {}).get("filing.still-trading.status") or {}).get(
        "answer") or "")
    end_use = str(getattr(getattr(profile, "entity", None), "end_use", "") or "")
    declared = bool(getattr(profile, "wind_down", None))
    from_answer = bool(_WINDING_UP.search(said))
    from_use = bool(_WINDING_UP.search(end_use))
    if not (from_answer or from_use or declared):
        return []

    if from_answer:
        trigger = ("The company was said to be closing down, in the answer given to "
                   "the question about whether it was still trading.")
        raised = "an answer already given"
    elif from_use:
        trigger = f"These books were prepared for {end_use}."
        raised = "the books"
    else:
        trigger = "A wind-down is already recorded against this company."
        raised = "the books"

    # These five reuse the ids the wind-down entries already refuse without, so
    # one answer satisfies the requirement AND unblocks the entries. A second
    # set of ids for the same five facts would let the two disagree.
    out = [
        Requirement(
            id="wind_down.target_date", group="wind-down", kind=QUESTION,
            need="On what date was the company dissolved, or on what date will it be?",
            why=("The final return covers the period up to that date and says it is "
                 "the final one. Everything after it belongs to nobody."),
            trigger=trigger, raised_by=raised),
        Requirement(
            id="wind_down.receivable_plan", group="wind-down", kind=QUESTION,
            need=("For each balance still on the books at the year end, say what "
                  "happens to it: collected, written off, forgiven, or passed to "
                  "somebody."),
            why=("Nothing can be left sitting on a closing company's balance sheet, "
                 "and each of those endings is reported differently. Writing off and "
                 "forgiving are not the same thing."),
            trigger=trigger, raised_by=raised),
        Requirement(
            id="wind_down.safe_terms", group="wind-down", kind=QUESTION,
            need=("What do the convertible instruments say happens if the company is "
                  "dissolved, and what will each holder actually receive?"),
            why=("A dissolution clause decides whether holders are paid ahead of "
                 "shareholders or alongside them, and that decides the final "
                 "balance sheet."),
            trigger=trigger, raised_by=raised),
        Requirement(
            id="wind_down.cap_table", group="wind-down", kind=QUESTION,
            need="Who receives what is left, and in what order?",
            why=("Anything paid out to owners on a dissolution is reported to each of "
                 "them, so the split has to be settled before the final return."),
            trigger=trigger, raised_by=raised),
        Requirement(
            id="wind_down.resolution_date", group="wind-down", kind=QUESTION,
            need=("On what date did the board or the members agree to close the "
                  "company?"),
            why=("That date starts the wind-down and decides which year the closing "
                 "entries belong in."),
            trigger=trigger, raised_by=raised),
        _req("wind-down", "certificate", DOCUMENT,
             "The certificate of dissolution, or the board consent agreeing to close "
             "the company, whichever exists yet.",
             "It is the proof of the date everything else hangs on, and the state "
             "wants its own filings settled before it issues one.",
             trigger, raised_by=raised),
    ]
    return out


# ------------------------------------------------------------------ the scan

_BUILDERS = (
    "prior-return", "foreign-entity", "foreign-owner", "convertible",
    "ownership-roster", "officer-pay", "contractors", "digital-assets",
    "related-party-receipts", "fixed-assets", "intangibles", "rd-credit", "meals",
    "state-footprint", "still-trading", "wind-down",
)


def scan(ledger, *, profile=None, filing_year=None, answers=None) -> RequirementScan:
    """Everything a preparer would have to ask this company for.

    `answers` maps a requirement id to what has been recorded against it:
    `{"answer": ..., "answered_on": ..., "source": ..., "unknown": bool,
    "unknown_reason": ..., "document": {...}}`. It is read for two purposes: to
    mark a requirement satisfied, and to raise the requirements that only an
    answer can raise. Nothing is written back.

    `filing_year` scopes every profit-and-loss trigger. Balance-sheet triggers
    read the year-end balance instead, because a balance is a position and a
    movement is not.
    """
    answers = dict(answers or {})
    out = RequirementScan(filing_year=filing_year)
    if ledger is None:
        out.notes.append("no ledger was loaded, so nothing could be read")
        return out

    out.accounts_read = len(_live_accounts(ledger))
    if filing_year is None:
        out.notes.append(
            "no filing year is set, so every profit-and-loss trigger was measured "
            "across the whole exported period rather than inside one year")

    found = []
    found += _prior_return(ledger, filing_year, answers)
    found += _foreign_entity_group(ledger, profile, filing_year)
    found += _foreign_owner_group(profile, answers)
    found += _convertible_group(ledger, filing_year)
    found += _ownership_roster_group(ledger, filing_year)
    found += _officer_pay_group(ledger, filing_year)
    found += _contractor_group(ledger, filing_year)
    found += _digital_assets_group(ledger, filing_year, answers)
    found += _related_party_receipts_group(ledger, profile, filing_year)
    found += _fixed_assets_group(ledger, filing_year)
    found += _intangibles_group(ledger, filing_year)
    found += _rd_credit_group(ledger, filing_year)
    found += _meals_group(ledger, filing_year)
    found += _state_footprint_group(ledger, filing_year)
    found += _still_trading_group(ledger, profile, filing_year, answers)
    found += _wind_down_group(profile, filing_year, answers)

    seen = set()
    for req in found:
        if req.id in seen:
            continue
        seen.add(req.id)
        record = answers.get(req.id) or {}
        req.answer = str(record.get("answer") or "")
        req.answered_on = str(record.get("answered_on") or "")
        req.source = str(record.get("source") or "")
        req.unknown = bool(record.get("unknown"))
        req.unknown_reason = str(record.get("unknown_reason") or "")
        req.document = dict(record.get("document") or {})
        if not req.trigger:                  # pragma: no cover - guarded by tests
            raise ValueError(f"requirement {req.id} names no trigger")
        out.requirements.append(req)
    return out


# ----------------------------------------------------------------- rendering

def _wrap(text, width=76, indent="") -> list:
    words, line, out = str(text).split(), "", []
    for w in words:
        if line and len(line) + 1 + len(w) > width - len(indent):
            out.append(indent + line)
            line = w
        else:
            line = f"{line} {w}".strip()
    if line:
        out.append(indent + line)
    return out or [indent.rstrip()]


def summary(scanned) -> str:
    total = len(scanned.requirements)
    return (f"{len(scanned.outstanding)} of {total} thing(s) the firm filing the "
            f"return still needs, across {len(scanned.groups)} subject(s), read off "
            f"{scanned.accounts_read} account(s) in this company's own chart. "
            f"{len(scanned.settled)} of {total} have been given, and "
            f"{len(scanned.unknowns)} of {total} were recorded as unanswerable.")


def render(scanned, *, limit=None, heading="What the return still needs") -> list:
    if not isinstance(scanned, RequirementScan):
        scanned = RequirementScan(requirements=list(scanned or []))
    out = [heading, "-" * min(len(heading), 78)]
    out += _wrap(summary(scanned), indent="  ")
    out.append("")
    out += _wrap(NOT_TAX_ADVICE, indent="  ")
    shown = scanned.outstanding if limit is None else scanned.outstanding[:limit]
    for req in shown:
        lines = req.lines()
        out.append("")
        out += _wrap(lines[0], indent="  ")
        for line in lines[1:]:
            out += _wrap(line, width=74, indent="    ")
        out.append(f"      python3 bin/books.py "
                   + (f"provide {req.id} <file>" if req.kind == DOCUMENT
                      else f"answer {req.id} \"...\""))
    if len(scanned.outstanding) > len(shown):
        out.append("")
        out += _wrap(f"... {len(scanned.outstanding) - len(shown)} more, all of them "
                     f"in the written list.", indent="  ")
    if scanned.notes:
        out.append("")
        out += _wrap("; ".join(scanned.notes) + ".", indent="  ")
    return out


def render_markdown(scanned, *, heading="# What the return still needs") -> list:
    if not isinstance(scanned, RequirementScan):
        scanned = RequirementScan(requirements=list(scanned or []))
    out = [heading, "", summary(scanned), "", NOT_TAX_ADVICE, ""]
    if not scanned.requirements:
        out += ["Nothing was raised. Every trigger in this engine reads this "
                "company's own chart and ledger, and none of them fired.", ""]
        return out
    for group, subject in GROUPS:
        reqs = scanned.of_group(group)
        if not reqs:
            continue
        out += [f"## {subject[:1].upper() + subject[1:]}", "",
                f"{len([r for r in reqs if r.outstanding])} of {len(reqs)} still "
                f"outstanding. {reqs[0].trigger}", ""]
        for req in reqs:
            mark = "outstanding" if req.outstanding else req.status_word()
            out.append(f"- **{req.need}** ({req.wanted}, {mark})")
            out.append(f"  Why the return needs it: {req.why}")
            if req.evidence:
                out.append("  In your books: " + "; ".join(req.evidence) + ".")
            out.append(f"  Record it with: `python3 bin/books.py "
                       + (f"provide {req.id} <file>`" if req.kind == DOCUMENT
                          else f"answer {req.id} \"...\"`"))
            if req.unknown:
                out.append(f"  Recorded as unanswerable on {req.answered_on} by "
                           f"{req.source}: {req.unknown_reason}")
            elif req.answer:
                out.append(f"  Recorded on {req.answered_on} by {req.source}: "
                           f"{req.answer}")
            out.append("")
    if scanned.notes:
        out += ["## What could not be read", ""]
        for note in scanned.notes:
            out.append(f"- {note}.")
        out.append("")
    return out
