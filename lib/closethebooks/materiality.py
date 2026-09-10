"""Materiality: what has to be stated separately, and what may be summarised.

WHY THIS MODULE EXISTS, IN ONE PARAGRAPH
    QuickBooks Online US cannot import journal entries, so a year of catch-up
    is a person at a keyboard typing every adjusting entry by hand. Forty
    entries is about two hours. The honest expectation for two hours of manual
    data entry is that it does not happen in one sitting and that some of it
    never happens at all, and a half-posted set of adjusting entries is worse
    than none: the books look finished and are not. The only real fix is a
    shorter list. This module makes the list shorter in the one way an
    accountant can defend, which is materiality.

WHAT MATERIALITY IS HERE
    An engagement fixes a threshold: the amount below which a misstatement or a
    separate presentation does not change what a reader of these books would
    decide. That threshold belongs to the engagement, not to this code, and not
    to a default. It comes from the profile (`entity.materiality`), which is
    one of the four facts `learn-my-books` refuses to start without.

    Below the threshold, several small entries that do the same thing to the
    same accounts inside one period can be stated once. Above it, never.

THE FIVE RULES THAT KEEP IT HONEST
    1. Never across different accounts. Where an amount lands is the entire
       content of a journal entry. Two entries that hit different accounts are
       two different facts, whatever they cost.
    2. Never across a period the books will be judged on, a fiscal year end
       above all. Combining entries across a year end moves income between
       years. That is the one thing nobody may do quietly, and it is enforced
       structurally here: every grouping key carries its fiscal year, so a
       group cannot span one.
    3. The replacement entry lists every entry it replaced, with dates and
       amounts, in its basis. A summary whose detail cannot be recovered is
       not a summary, it is a deletion.
    4. A percentage threshold resolves against a base the profile names. If
       the base is not named, or the ledger cannot supply it, the threshold is
       reported as absent and said out loud. A guessed base is a guessed
       threshold, and a guessed threshold silently decides what disappears.
    5. No threshold means no roll-up. A missing threshold is a missing
       engagement fact, not permission to use judgement. The caller is told to
       go and get it.

WHAT IT DOES NOT DO
    It does not net entries against each other, it does not move a date, and
    it does not touch anything at or above the threshold. It only states
    several small same-shaped facts once instead of several times.
"""

from __future__ import annotations

import calendar
import datetime as _dt
import re
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation

from .model import ProposedEntry
from .util import CENTS, ZERO, iso, money, parse_date

# Periods a roll-up may be scoped to. A quarter is the default because it is
# short enough that a summary still sits in the right quarter of a P&L, and
# long enough to actually shorten a twelve-month catch-up.
PERIODS = ("quarter", "month")

# What "same kind of thing" may mean. Neither value ever permits crossing
# accounts; see rule 1. "account" means the same accounts on the same sides,
# "account_class" additionally requires the same QuickBooks class on each line.
GROUPINGS = ("account", "account_class")

# Bases a percentage threshold may name, and how each is computed from a
# ledger. Anything else is unavailable rather than approximated.
REVENUE_TYPES = {"income", "other income"}
ASSET_TYPES = {"bank", "accounts receivable", "other current asset",
               "fixed asset", "other asset"}

_PERCENT_RE = re.compile(r"^\s*([0-9]*\.?[0-9]+)\s*%\s*(?:of\s+(?P<base>.+?))?\s*$", re.I)
_NOTHING = {"", "none", "n/a", "na", "null", "unknown", "tbd", "not set"}

_BASE_ALIASES = {
    "revenue": "revenue", "revenues": "revenue", "income": "revenue",
    "total revenue": "revenue", "total revenues": "revenue", "sales": "revenue",
    "gross revenue": "revenue", "top line": "revenue",
    "assets": "total_assets", "total assets": "total_assets",
    "total_assets": "total_assets", "asset total": "total_assets",
}


class MaterialityError(ValueError):
    """A threshold could not be read, or a roll-up was asked for illegally."""


class Percentage(Decimal):
    """A threshold written as a percentage, before any base has been applied.

    It is a Decimal so it can be carried around and compared, and it is a
    distinct type so nothing can mistake "1" meaning one percent for "1"
    meaning one dollar. `roll_up` refuses one of these outright: a percentage
    that has not been resolved against a real base is not a threshold yet.
    """

    def __new__(cls, value, base: str = ""):
        self = super().__new__(cls, value)
        self.base = str(base or "").strip().lower()
        return self

    @property
    def percent(self) -> bool:
        return True

    def resolve(self, base_amount) -> Decimal:
        """Turn 1% into an amount, given the base the profile named."""
        amount = money(base_amount, "materiality base")
        return (Decimal(self) / Decimal(100) * amount).quantize(CENTS)


@dataclass
class Resolution:
    """The full answer to "what is this engagement's threshold", with its why.

    `threshold_for` returns only the number, because that is what callers
    compare against. Everything that would otherwise be lost, above all the
    reason a percentage could not be resolved, lives here so a skill can print
    it instead of proceeding on a silent None.
    """
    threshold: object = None          # Decimal, or None when unavailable
    raw: str = ""                     # what the profile actually said
    base: str = ""                    # "revenue", "total_assets", or ""
    base_amount: object = None        # Decimal the percentage was applied to
    available: bool = False
    note: str = ""

    def __bool__(self) -> bool:
        return self.available and self.threshold is not None


@dataclass
class RollUpResult:
    """What a roll-up did, in enough detail to argue with."""
    entries: list = field(default_factory=list)    # what to type: kept + rolled
    rolled: list = field(default_factory=list)     # one dict per group combined
    summary: dict = field(default_factory=dict)
    saved_count: int = 0
    note: str = ""

    @property
    def before(self) -> int:
        return int(self.summary.get("before", 0))

    @property
    def after(self) -> int:
        return int(self.summary.get("after", 0))


# ------------------------------------------------------------- parsing

def parse_threshold(value):
    """Read a materiality threshold. Returns a Decimal, a Percentage, or None.

    Accepts what a human writes in a profile: "1000", "1,000.00", "$1,000",
    a Decimal or an int, and "1%" or "1% of revenue". Blank, None and the
    words people use for blank all return None, which means "no threshold set"
    and never "zero".
    """
    if value is None:
        return None
    if isinstance(value, Percentage):
        return value
    if isinstance(value, (Decimal, int)):
        d = money(value, "materiality")
        return _non_negative(d, value)
    if isinstance(value, float):
        # Money never travels as a float in this engine, but a JSON profile
        # can hand us one, so it is read through `money` like everything else.
        d = money(value, "materiality")
        return _non_negative(d, value)

    s = str(value).strip()
    if s.strip().lower() in _NOTHING:
        return None

    m = _PERCENT_RE.match(s)
    if m:
        try:
            pct = Decimal(m.group(1))
        except InvalidOperation as exc:
            raise MaterialityError(f"materiality: cannot read {value!r} as a percentage") from exc
        if pct < 0:
            raise MaterialityError(f"materiality: {value!r} is negative")
        base = _canonical_base(m.group("base") or "")
        return Percentage(pct, base)

    try:
        d = money(s, "materiality")
    except Exception as exc:                        # MoneyError and friends
        raise MaterialityError(
            f"materiality: cannot read {value!r} as an amount or a percentage. "
            f"Write it as a number (\"500.00\"), or as a percentage of a named "
            f"base (\"1% of revenue\")."
        ) from exc
    return _non_negative(d, value)


def _non_negative(d: Decimal, original):
    if d < ZERO:
        raise MaterialityError(f"materiality: {original!r} is negative; a threshold is a size")
    return d


def _canonical_base(text: str) -> str:
    t = re.sub(r"\s+", " ", str(text or "")).strip().lower().strip(".")
    if not t:
        return ""
    return _BASE_ALIASES.get(t, t)


# ---------------------------------------------------------- the engagement

def threshold_detail(profile, ledger=None) -> Resolution:
    """Resolve the engagement's threshold and say exactly how, or why not."""
    raw = _profile_materiality(profile)
    res = Resolution(raw=raw)

    parsed = parse_threshold(raw)
    if parsed is None:
        res.note = (
            "No materiality threshold is set on this engagement, so nothing may be "
            "rolled up. That is a missing engagement fact, not a licence to use "
            "judgement: ask whoever owns the books what a reader of these books "
            "would not care about, and write it into the profile as entity.materiality."
        )
        return res

    if not isinstance(parsed, Percentage):
        res.threshold = parsed
        res.available = True
        res.note = f"Materiality is {parsed} per the engagement (entity.materiality = {raw!r})."
        return res

    # A percentage without a base is not a threshold. Rule 4: say so, and treat
    # it as absent, rather than picking a base and quietly deciding what
    # disappears.
    res.base = parsed.base
    if not parsed.base:
        res.note = (
            f"Materiality is written as {raw!r} but the profile names no base for the "
            f"percentage. Write it as \"{raw.strip()} of revenue\" or \"{raw.strip()} of "
            f"total assets\". Until then the threshold is treated as absent and nothing "
            f"is rolled up."
        )
        return res
    if parsed.base not in ("revenue", "total_assets"):
        res.note = (
            f"Materiality is {raw!r}, and this engine can compute a percentage of revenue "
            f"or of total assets, not of {parsed.base!r}. Resolve it to an amount by hand "
            f"and put that amount in the profile. Nothing is rolled up meanwhile."
        )
        return res

    base_amount = _base_amount(ledger, parsed.base)
    if base_amount is None:
        res.note = (
            f"Materiality is {raw!r}, but {parsed.base.replace('_', ' ')} is not available "
            f"from the ledger that was passed in (no ledger, or no accounts of that type "
            f"with any activity). The threshold is treated as absent rather than guessed, "
            f"and nothing is rolled up."
        )
        return res

    res.base_amount = base_amount
    res.threshold = parsed.resolve(base_amount)
    res.available = True
    res.note = (
        f"Materiality is {res.threshold}, being {raw} of "
        f"{parsed.base.replace('_', ' ')} of {base_amount}, computed from the ledger."
    )
    return res


def threshold_for(profile, ledger=None):
    """The engagement's threshold as a Decimal, or None when there is not one.

    None covers three different situations, and they are not the same thing:
    no threshold set, a percentage with no base named, and a percentage whose
    base the ledger cannot supply. Call `threshold_detail` to get the sentence
    that says which; a caller that prints "no threshold" without saying why is
    the reason this pair exists.
    """
    return threshold_detail(profile, ledger).threshold


def _profile_materiality(profile) -> str:
    if profile is None:
        return ""
    entity = getattr(profile, "entity", None)
    if entity is None and isinstance(profile, dict):
        entity = profile.get("entity")
    if entity is None:
        entity = profile
    if isinstance(entity, dict):
        return str(entity.get("materiality", "") or "").strip()
    return str(getattr(entity, "materiality", "") or "").strip()


def _base_amount(ledger, base: str):
    """Revenue or total assets out of a ledger, or None if it cannot be had."""
    if ledger is None:
        return None
    accounts = getattr(ledger, "accounts", None) or {}
    lines = getattr(ledger, "lines", None) or []
    if not accounts or not lines:
        return None

    wanted = REVENUE_TYPES if base == "revenue" else ASSET_TYPES
    keys = {k for k, a in accounts.items() if getattr(a, "type", "") in wanted}
    if not keys:
        return None

    # `signed` is debit minus credit everywhere in this engine. Revenue is
    # credit-normal, so its natural size is the negative of that sum.
    #
    # REVENUE IS THE PERIOD'S MOVEMENT AND TOTAL ASSETS IS A BALANCE. Revenue
    # accounts start each year at zero, so summing the lines is right for them.
    # An asset carries its opening position in, so summing the lines there
    # understates total assets by whatever the company already held, which on
    # one real file was almost all of it. Assets therefore go through
    # `balance_of`, and where no opening position is known this returns None
    # rather than a materiality threshold computed off a number that is short.
    if base == "revenue":
        total = -sum((l.signed for l in lines if l.account in keys), ZERO)
    else:
        balance_of = getattr(ledger, "balance_of", None)
        if balance_of is None:
            return None
        total = ZERO
        for key in keys:
            value = balance_of(key)
            if value is None:
                return None
            total += value
    if total <= ZERO:
        return None
    return money(total, "materiality base")


# -------------------------------------------------------------- periods

def fiscal_year_end_month(profile, default: int = 12) -> int:
    """The month a fiscal year ends in. December unless the profile says else.

    A profile records `entity.fiscal_year` as a label ("2025", "FY2026",
    "2025 (June year end)"). Only an explicit month named there moves this off
    December, because a wrong year end is exactly the error rule 2 exists to
    stop.
    """
    text = ""
    entity = getattr(profile, "entity", None)
    if entity is None and isinstance(profile, dict):
        entity = profile.get("entity") or {}
    if isinstance(entity, dict):
        text = str(entity.get("fiscal_year", "") or "")
    elif entity is not None:
        text = str(getattr(entity, "fiscal_year", "") or "")
    t = text.lower()
    for i, name in enumerate(calendar.month_name):
        if i and name.lower() in t:
            return i
    for i, name in enumerate(calendar.month_abbr):
        if i and re.search(rf"\b{name.lower()}\b", t):
            return i
    return default


def _fiscal_index(d, fye_month: int):
    """(fiscal year label, month index 0..11 inside that fiscal year)."""
    if not 1 <= int(fye_month) <= 12:
        raise MaterialityError(f"fiscal year end month {fye_month!r} is not a month")
    fye_month = int(fye_month)
    fy = d.year if d.month <= fye_month else d.year + 1
    idx = (d.month - fye_month - 1) % 12
    return fy, idx


def _period_key(d, period: str, fye_month: int):
    """A grouping key that CANNOT span a fiscal year end, by construction.

    The fiscal year is the first element of the key, so two dates either side
    of a year end never land in the same group no matter how close together
    they are. This is rule 2, enforced structurally rather than by a check
    somebody can forget to run.
    """
    fy, idx = _fiscal_index(d, fye_month)
    if period == "month":
        return (fy, "M", idx)
    return (fy, "Q", idx // 3)


def _from_ordinal(o: int):
    """Absolute month number back to (year, month). o = year * 12 + (month - 1)."""
    return o // 12, o % 12 + 1


def _period_bounds(key, fye_month: int):
    """First and last calendar date of a period key.

    Worked through month ordinals rather than modular arithmetic on month
    numbers, because a June year end is where the off-by-one lives and an
    off-by-one here would print the wrong period on a summary entry.
    """
    fy, unit, n = key
    months = 1 if unit == "M" else 3
    first_index = n if unit == "M" else n * 3
    fy_start = fy * 12 + (int(fye_month) - 1) - 11        # month 0 of this fiscal year
    y0, m0 = _from_ordinal(fy_start + first_index)
    y1, m1 = _from_ordinal(fy_start + first_index + months - 1)
    return _dt.date(y0, m0, 1), _dt.date(y1, m1, calendar.monthrange(y1, m1)[1])


def period_label(key, fye_month: int) -> str:
    fy, unit, n = key
    start, end = _period_bounds(key, fye_month)
    name = f"FY{fy} {'Q' if unit == 'Q' else 'M'}{n + 1}"
    return f"{name} ({iso(start)} to {iso(end)})"


# -------------------------------------------------------------- roll-up

def _entry_view(entry):
    """Validate an entry and pull out what grouping needs. Never mutates it."""
    entry.check()
    number = str(getattr(entry, "number", "") or "").strip()
    if not number:
        raise MaterialityError("an entry has no number; a roll-up has to name what it replaced")
    date = parse_date(getattr(entry, "date", None), field=f"entry {number} date")
    lines = []
    for i, line in enumerate(entry.lines, start=1):
        acct, debit, credit, memo, name, klass = _parts(line, f"entry {number} line {i}")
        lines.append({
            "account": str(acct or "").strip(),
            "debit": money(debit, f"entry {number} line {i} debit"),
            "credit": money(credit, f"entry {number} line {i} credit"),
            "memo": str(memo or "").strip(),
            "name": str(name or "").strip(),
            "class": str(klass or "").strip(),
        })
    total = sum((l["debit"] for l in lines), ZERO)
    return {"entry": entry, "number": number, "date": date, "lines": lines,
            "kind": str(getattr(entry, "kind", "") or "").strip() or "other",
            "amount": total}


def _parts(line, where):
    from .je_csv import _line_parts
    return _line_parts(line, where)


def _shape(view, by: str):
    """The accounts and sides of an entry. Never the amounts, never the date.

    Rule 1 lives in this function: the account, and the side it is on, is what
    makes two entries the same fact stated twice. Two entries with the same
    amounts but different accounts share nothing.
    """
    out = []
    for l in view["lines"]:
        side = "D" if l["debit"] != ZERO else "C"
        if by == "account_class":
            out.append((l["account"], side, l["class"]))
        else:
            out.append((l["account"], side))
    return tuple(out)


def roll_up(entries, threshold, *, period="quarter", by="account",
            fiscal_year_end_month=12) -> RollUpResult:
    """Combine immaterial same-shaped entries inside one period. Never across one.

    `entries` are ProposedEntry objects. `threshold` is what
    `threshold_for` returned: a Decimal, or None. None rolls up nothing and
    says why, because absence of a threshold is a missing engagement fact and
    not permission (rule 5).

    Returns a RollUpResult whose `entries` is the shorter list to type,
    `rolled` describes every group that was combined, and `saved_count` is
    how many fewer entries the person has to type. The benefit is reported as
    a number so it can be argued with.
    """
    if period not in PERIODS:
        raise MaterialityError(f"period must be one of {PERIODS}, not {period!r}")
    if by not in GROUPINGS:
        raise MaterialityError(f"by must be one of {GROUPINGS}, not {by!r}")

    originals = list(entries)
    views = [_entry_view(e) for e in originals]
    before = len(views)

    if threshold is None:
        return RollUpResult(
            entries=originals, rolled=[],
            summary={"before": before, "after": before, "rolled_groups": 0,
                     "threshold": None, "period": period, "by": by,
                     "fiscal_year_end_month": int(fiscal_year_end_month)},
            saved_count=0,
            note=("No materiality threshold, so nothing was rolled up and all "
                  f"{before} entries stay as they are. A threshold is an engagement "
                  "fact: ask for it and pass it in. Absence of one is not permission "
                  "to summarise."),
        )
    if isinstance(threshold, Percentage):
        raise MaterialityError(
            f"threshold {threshold}% has not been resolved against a base. Call "
            f"threshold_for(profile, ledger) so the percentage becomes an amount, "
            f"or pass None. A percentage cannot be compared to an entry."
        )
    threshold = money(threshold, "threshold")
    if threshold < ZERO:
        raise MaterialityError("threshold is negative; a threshold is a size")

    # Sub-threshold means strictly below. An entry AT the threshold is material:
    # the threshold is the amount a reader starts caring at, so the boundary
    # belongs on the side that states more, not less.
    groups, order = {}, []
    for v in views:
        if v["amount"] >= threshold:
            continue
        key = (v["kind"], _period_key(v["date"], period, fiscal_year_end_month),
               _shape(v, by))
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append(v)

    combined = {}                 # key -> the replacement entry
    member_of = {}                # id(entry) -> key, for the entries actually replaced
    rolled = []
    replaced_numbers = set()
    seq = 0
    for key in order:
        members = sorted(groups[key], key=lambda v: (v["date"], v["number"]))
        if len(members) < 2:
            # One entry is not a summary of anything, and renaming it would
            # only lose its own number.
            continue
        seq += 1
        kind, pkey, shape = key
        entry = _combine(members, kind, pkey, shape, threshold, seq,
                         int(fiscal_year_end_month), by)
        combined[key] = entry
        rolled.append({
            "entry": entry,
            "key": key,
            "kind": kind,
            "period": pkey,
            "period_label": period_label(pkey, int(fiscal_year_end_month)),
            "accounts": [a for a, *_ in shape],
            "replaced": [{"number": v["number"], "date": iso(v["date"]),
                          "amount": str(v["amount"]), "basis": str(
                              getattr(v["entry"], "basis", "") or "")}
                         for v in members],
            "amount": sum((v["amount"] for v in members), ZERO),
        })
        replaced_numbers.update(v["number"] for v in members)
        # Membership is recorded per entry, never re-derived from the key. A
        # material entry can share a key with a rolled-up group (same accounts,
        # same quarter, just bigger), and re-deriving swallowed it: the one
        # entry that must never be summarised, silently gone, with the summary
        # still footing. Found by a test that counted the survivors.
        for v in members:
            member_of[id(v["entry"])] = key

    # Output order: every kept entry where it was, and each replacement at the
    # position of its earliest member, so the worksheet reads in the same order
    # the person was expecting.
    out, emitted = [], set()
    for v in views:
        key = member_of.get(id(v["entry"]))
        if key is not None:
            if key not in emitted:
                out.append(combined[key])
                emitted.add(key)
            continue
        out.append(v["entry"])

    after = len(out)
    saved = before - after
    summary = {
        "before": before, "after": after, "saved": saved,
        "rolled_groups": len(rolled),
        "entries_replaced": len(replaced_numbers),
        "threshold": str(threshold), "period": period, "by": by,
        "fiscal_year_end_month": int(fiscal_year_end_month),
    }
    if not rolled:
        note = (f"Nothing was rolled up. All {before} entries are either at or above the "
                f"{threshold} threshold, or no two immaterial entries share both their "
                f"accounts and a {period}.")
    else:
        note = (
            f"{before} entries became {after}: {saved} fewer to type. "
            f"{len(replaced_numbers)} entries below {threshold} were combined into "
            f"{len(rolled)} summary entr{'y' if len(rolled) == 1 else 'ies'}, one per set of "
            f"accounts per {period}. Nothing at or above the threshold was touched, no "
            f"summary crosses a fiscal year end, and each summary's basis lists every "
            f"entry it replaced with its date and amount."
        )
    return RollUpResult(entries=out, rolled=rolled, summary=summary,
                        saved_count=saved, note=note)


def _combine(members, kind, pkey, shape, threshold, seq, fye_month, by) -> ProposedEntry:
    """Build the one entry that replaces several, with a recoverable basis."""
    # Amounts add per (account, side) in the order the accounts first appeared,
    # so the summary reads like the entries it replaces.
    totals, order, classes = {}, [], {}
    for v in members:
        for l in v["lines"]:
            side = "D" if l["debit"] != ZERO else "C"
            k = (l["account"], side, l["class"] if by == "account_class" else "")
            if k not in order:
                order.append(k)
                totals[k] = ZERO
                classes[k] = set()
            totals[k] += l["debit"] if side == "D" else l["credit"]
            classes[k].add(l["class"])

    label = period_label(pkey, fye_month)
    memo = f"Summary of {len(members)} {kind} entries, {label.split(' (')[0]}"
    lines, dropped = [], []
    for account, side, klass in order:
        k = (account, side, klass)
        amount = totals[k]
        seen = {c for c in classes[k] if c}
        # A class is a reporting dimension, so it is carried through when every
        # entry agrees on it, and never invented when they do not. Where they
        # disagree the summary carries no class AND says which classes it
        # merged, because an entry that quietly loses its class reports into
        # the wrong column and nothing in the ledger says why.
        resolved = klass or (seen.pop() if len(seen) == 1 else "")
        if not resolved and len(seen) > 1:
            dropped.append(f"{account} ({', '.join(sorted(classes[k] - {''}))})")
        lines.append((account, amount if side == "D" else ZERO,
                      amount if side == "C" else ZERO, memo, "", resolved))

    # Rule 3. Every replaced entry is named with its date and its amount, so an
    # accountant reading the summary can rebuild the detail without this repo,
    # and each original basis is carried forward rather than dropped.
    detail = "; ".join(f"{v['number']} {iso(v['date'])} {v['amount']}" for v in members)
    bases = " ".join(
        f"{v['number']}: {str(getattr(v['entry'], 'basis', '') or '').strip()}"
        for v in members if str(getattr(v["entry"], "basis", "") or "").strip()
    )
    basis = (
        f"Roll-up of {len(members)} entries, each below the {threshold} materiality "
        f"threshold, all with the same accounts and the same kind ({kind}), all inside "
        f"{label}. Replaces: {detail}. No entry at or above the threshold is included, "
        f"and no entry from another fiscal year is included."
    )
    if dropped:
        basis += (
            f" The entries combined here carry more than one class on: {'; '.join(dropped)}. "
            f"The summary therefore carries no class on those lines. If class reporting "
            f"matters on this engagement, roll up with by='account_class' instead, or type "
            f"these entries separately."
        )
    if bases:
        basis += f" Original bases: {bases}"

    fy, unit, n = pkey
    number = f"RU-{fy}{unit}{n + 1}-{seq:02d}"
    return ProposedEntry(
        date=max(v["date"] for v in members),      # never later than the period
        number=number,
        lines=lines,
        memo=memo,
        kind=kind,
        basis=basis,
        batch_tag=str(getattr(members[0]["entry"], "batch_tag", "") or ""),
    )
