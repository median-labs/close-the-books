"""Two chart rows for one real-world account, and which of them holds what.

    from closethebooks.twins import scan
    found = scan(ledger, queue=queue, profile=prof, recon=recon)
    for twin in found:
        print(twin.headline())
    found.history_map()      # {the twin's key: the key whose history governs it}

WHAT THE PATTERN IS

A bank link breaks and is rebuilt. QuickBooks does not always reattach the feed
to the account that was already there; it creates a NEW one, at the top level of
the chart, with no account number, named almost the same as the original. From
that moment the company has two rows for one card:

    222000 Credit Cards:Acme Card    the real history, reconciled, no feed
           Acme card                 the live feed, the whole queue, never
                                     reconciled, and an opening-balance plug

The plug is the tell that makes it certain. When QuickBooks sets up the new
account it books the balance the bank reports as at the link date, and it books
the other side to Opening Balance Equity, because there is nowhere else to put
it. So a company with this pattern has an Opening Balance Equity balance that
exactly equals the plug on the newer account, which is how a scan can say
"these two rows are one account" rather than "these two rows have similar names".

WHY IT MATTERS MORE THAN IT LOOKS

Two rows is a tidiness problem. What it does to every OTHER check is not:

  * The reconciled history is on the account with no feed, and the queue is on
    the account with no history. Ask "has this account ever been reconciled" of
    the account the queue is on and the answer is no, so every queue item in a
    period that WAS reconciled clean reads as unbooked work. On the file this
    was written from that was 197 items, and booking them would have posted a
    second copy of each into a closed period.
  * Balances are split across two rows, so neither ties to a statement and the
    difference on each looks like a real difference.
  * `Ledger.aliases_for` deliberately refuses to match on a leaf name that two
    accounts answer to, so the two rows stay separate everywhere in the engine.
    That is correct, and it is also why nothing else in the engine notices them.

THIS MODULE PROPOSES NOTHING

It does not merge, and it does not recommend merging. Which row survives, what
happens to the plug, what happens to Opening Balance Equity, and the ORDER those
happen in, are a decision with real consequences for a reconciled period, and
getting the order wrong is worse than leaving two rows in the chart. The
finding states what is where. The disposition belongs to the runbook that owns
chart changes.

FIVE SIGNALS, WEIGHTED, AND NONE OF THEM ALONE

    1  the names match once numbering, case and a masked suffix are normalized
    2  one carries an account number and the other does not
    3  one has the live feed and the other has none
    4  one has never been reconciled while the other has history
    5  an opening-balance plug on the newer one that Opening Balance Equity offsets

Signal 1 is necessary and never sufficient: `Prepaid Rent` under two parents is
entirely ordinary and is not a twin. A finding is raised at two signals and
`certain` is reserved for the plug, because the plug is the only one that is an
artefact of the re-link itself rather than a coincidence of naming.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Optional

from .model import canonical_type, is_real_account
from .sides import balance_text
from .util import fmt, iso, money, norm_text

__all__ = ["Twin", "TwinScan", "scan", "render", "render_markdown",
           "normalized_name"]

# Raised at this many signals. Below it the evidence is one coincidence.
MIN_SIGNALS = 2

_OBE_NAME = re.compile(r"\bopening balance equity\b", re.I)
# What QuickBooks writes on the entry it creates when a feed is first linked.
_PLUG_TELL = re.compile(r"\bopening balance\b", re.I)
# A masked account suffix: "Acme Cash (3947)". Two rows for one account often
# differ only by whether the mask was typed into the name.
_MASK = re.compile(r"\s*\((?:x{0,4}\*{0,4}[\d\-]{2,})\)\s*$", re.I)
_LEADING_NUMBER = re.compile(r"^\s*\d{3,}\s+")


def normalized_name(account) -> str:
    """The leaf name, with numbering, case, a masked suffix and punctuation gone.

    `222000 Credit Cards:Acme Card` and the unnumbered `Acme card` both reduce to
    `acme card`. `Current Assets:Acme Cash (3947)` and `Acme Cash` both reduce to
    `acme cash`. The parent path is dropped on purpose: a re-link creates the new
    row at the TOP level of the chart, so the two rows never share a parent and
    comparing full names would never match them.
    """
    full = getattr(account, "full_name", "") or getattr(account, "name", "") or str(account)
    leaf = str(full).split(":")[-1]
    leaf = _LEADING_NUMBER.sub("", leaf)
    leaf = _MASK.sub("", leaf)
    leaf = re.sub(r"[^a-z0-9]+", " ", norm_text(leaf)).strip()
    return leaf


# ------------------------------------------------------------------ results

@dataclass
class Side:
    """One of the two rows, and what it actually holds."""

    key: str = ""
    label: str = ""
    full_name: str = ""
    number: str = ""
    account_type: str = ""
    lines: int = 0
    first_posting: object = None
    last_posting: object = None
    balance: Optional[Decimal] = None
    queue_items: int = 0
    feed: str = ""                    # "live" | "dead" | "none" | "" (not declared)
    reconciled_through: object = None
    reconciled_months: int = 0
    plug: Optional[Decimal] = None
    plug_date: object = None
    plug_memo: str = ""

    @property
    def numbered(self) -> bool:
        return bool(str(self.number).strip())

    @property
    def has_history(self) -> bool:
        return self.lines > 0

    def describe(self) -> str:
        bits = [f"{self.lines} posted line(s)"]
        if self.first_posting:
            bits.append(f"{iso(self.first_posting)} to {iso(self.last_posting)}")
        bits.append(f"{self.queue_items} queue item(s)")
        if self.feed:
            bits.append(f"feed {self.feed}")
        if self.reconciled_through:
            bits.append(f"reconciled clean through {iso(self.reconciled_through)}")
        elif self.reconciled_months:
            bits.append(f"{self.reconciled_months} month(s) reconciled")
        else:
            bits.append("never reconciled")
        if self.balance is not None:
            bits.append(f"balance {balance_text(self.balance, self.account_type)}")
        return f"{self.label}: " + ", ".join(bits)


@dataclass
class Twin:
    """Two chart rows the evidence says are one real-world account."""

    account_type: str = ""
    normalized: str = ""
    original: Side = field(default_factory=Side)   # holds the history
    duplicate: Side = field(default_factory=Side)  # holds the feed and the queue
    signals: list = field(default_factory=list)    # (name, what was measured)
    obe_balance: Optional[Decimal] = None
    obe_label: str = ""

    @property
    def plug(self) -> Optional[Decimal]:
        return self.duplicate.plug

    @property
    def plug_offsets_obe(self) -> bool:
        """True when the plug and Opening Balance Equity are the same figure."""
        if self.plug is None or self.obe_balance is None:
            return False
        return abs(money(self.plug)) == abs(money(self.obe_balance))

    @property
    def certain(self) -> bool:
        return self.plug_offsets_obe

    @property
    def strength(self) -> int:
        return len(self.signals)

    def headline(self) -> str:
        return (f"{self.original.label} and {self.duplicate.label} are two chart rows "
                f"for one {self.account_type or 'account'}, on "
                f"{self.strength} of 5 signal(s).")

    def lines(self) -> list:
        out = [self.headline()]
        out.append(
            f"The history is on {self.original.label}. The feed and the queue are on "
            f"{self.duplicate.label}. " + self.original.describe() + ". "
            + self.duplicate.describe() + ".")
        if self.plug is not None:
            plugged = (f"{self.duplicate.label} opens with a plug of "
                       f"{balance_text(self.plug, self.duplicate.account_type)} dated "
                       f"{iso(self.duplicate.plug_date)}"
                       + (f", memo {self.duplicate.plug_memo!r}"
                          if self.duplicate.plug_memo else "") + ".")
            if self.plug_offsets_obe:
                plugged += (f" {self.obe_label or 'Opening Balance Equity'} carries "
                            f"{balance_text(self.obe_balance)}, the same figure on the "
                            f"other side, so the plug and that balance are one entry.")
            out.append(plugged)
        out.append("Signals: " + "; ".join(f"{name} ({measured})"
                                           for name, measured in self.signals) + ".")
        if not self.certain:
            out.append(
                "Without an opening-balance plug that Opening Balance Equity offsets, "
                "two rows with the same name are either one account linked twice or two "
                "accounts a person named alike. Which one settles it: the bank's own "
                "account numbers for the two, and the date the feed was relinked.")
        out.append(
            "What this changes elsewhere: reconciliation history sits on the row with no "
            "feed and the queue sits on the row with no history, so a check that asks "
            "whether the queued account has ever been reconciled reads no, and items in "
            "months that were reconciled clean read as unbooked work.")
        out.append(
            "No disposition is proposed here. Which row survives, what happens to the "
            "plug and to Opening Balance Equity, and the order those happen in, are a "
            "decision with consequences for a period that is already closed.")
        return out

    def as_row(self) -> dict:
        return {
            "type": self.account_type,
            "holds the history": self.original.label,
            "holds the feed and the queue": self.duplicate.label,
            "posted lines": f"{self.original.lines} against {self.duplicate.lines}",
            "queue items": f"{self.original.queue_items} against {self.duplicate.queue_items}",
            "plug": fmt(self.plug) if self.plug is not None else "",
            "opening balance equity": (fmt(self.obe_balance)
                                       if self.obe_balance is not None else ""),
            "plug offsets it": "yes" if self.plug_offsets_obe else "no",
            "signals": f"{self.strength} of 5",
        }


@dataclass
class TwinScan:
    findings: list = field(default_factory=list)
    checked: int = 0                              # candidate name groups examined
    accounts: int = 0
    considered: list = field(default_factory=list)  # (names, why it was not raised)
    obe_balance: Optional[Decimal] = None
    not_checked: list = field(default_factory=list)

    @property
    def certain(self) -> list:
        return [t for t in self.findings if t.certain]

    def __bool__(self) -> bool:
        return bool(self.findings)

    def __len__(self) -> int:
        return len(self.findings)

    def __iter__(self):
        return iter(self.findings)

    def history_map(self) -> dict:
        """{duplicate key: original key}, for `filing_year.scope`.

        The mapping says where an account's RECONCILED HISTORY lives, and nothing
        more. It is not a merge, it does not move a balance, and passing it
        changes no number in the ledger.
        """
        return {t.duplicate.key: t.original.key for t in self.findings
                if t.duplicate.key and t.original.key}


# --------------------------------------------------------------- the scan

class _Late:
    """Sorts after every real date, so an account with no posting sorts last."""

    def __lt__(self, other):
        return False

    def __gt__(self, other):
        return True

    def __eq__(self, other):
        return isinstance(other, _Late)

    def __le__(self, other):
        return isinstance(other, _Late)

    def __ge__(self, other):
        return True

    def __hash__(self):
        return hash("_Late")


_LATE = _Late()



def _obe_account(ledger):
    for account in (getattr(ledger, "accounts", {}) or {}).values():
        if account.role == "obe" or _OBE_NAME.search(
                f"{account.full_name} {account.name}"):
            return account
    return None


def _plug_on(ledger, account):
    """The opening-balance entry QuickBooks writes when a feed is first linked.

    Identified by the words on the line, never by being the earliest one: a real
    first transaction is also the earliest, and calling that a plug would report
    a defect on every account in the chart.
    """
    aliases = ledger.aliases_for(account.key)
    for line in getattr(ledger, "lines", []):
        if (norm_text(line.account) not in aliases
                and norm_text(line.account_full) not in aliases):
            continue
        text = f"{line.memo} {line.txn_type} {line.doc_num}"
        if _PLUG_TELL.search(text):
            return money(line.signed), line.date, (line.memo or "").strip()
    return None, None, ""


def _side(ledger, account, queue, profile, reconciled_through, recon) -> Side:
    aliases = ledger.aliases_for(account.key)
    posted = [l for l in getattr(ledger, "lines", [])
              if norm_text(l.account) in aliases or norm_text(l.account_full) in aliases]
    dates = sorted(l.date for l in posted if l.date is not None)
    mine = [i for i in queue
            if norm_text(getattr(i, "account_key", "")) in aliases]
    spec = None
    if profile is not None:
        spec = profile.account_spec(account.key)
    plug, plug_date, plug_memo = _plug_on(ledger, account)
    months = 0
    for row in (getattr(recon, "rows", None) or []):
        if row.account_key == account.key and getattr(row, "ties", False):
            months += 1
    return Side(
        key=account.key, label=account.label() or account.full_name,
        full_name=account.full_name, number=account.number,
        account_type=account.type, lines=len(posted),
        first_posting=dates[0] if dates else None,
        last_posting=dates[-1] if dates else None,
        balance=ledger.balance_of(account.key),
        queue_items=len(mine),
        feed=(spec.feed if spec is not None else ""),
        reconciled_through=(reconciled_through or {}).get(account.key),
        reconciled_months=months, plug=plug, plug_date=plug_date, plug_memo=plug_memo,
    )


def scan(ledger, *, queue=(), profile=None, recon=None,
         reconciled_through=None) -> TwinScan:
    """Every pair of chart rows the evidence says is one real-world account.

    `queue` and `profile` are optional and only sharpen the finding: without
    them the feed signal cannot fire and the finding says so by counting fewer
    signals. `reconciled_through` is the mapping `filing_year` builds, used here
    only to state which row is reconciled and through when.

    Only accounts of the same canonical TYPE are compared. A bank account and a
    credit card with the same name are two accounts, not one row twice.
    """
    queue = list(queue or [])
    out = TwinScan(accounts=len(getattr(ledger, "accounts", {}) or {}))
    obe = _obe_account(ledger)
    if obe is not None:
        out.obe_balance = ledger.balance_of(obe.key)
    else:
        out.not_checked.append(
            "no Opening Balance Equity account is in the chart, so the plug signal, "
            "the only one that is an artefact of the re-link itself, cannot fire")

    groups = {}
    for account in (getattr(ledger, "accounts", {}) or {}).values():
        name = normalized_name(account)
        if not name:
            continue
        groups.setdefault((canonical_type(account.type), name), []).append(account)

    for (acct_type, name), members in sorted(groups.items()):
        if len(members) < 2:
            continue
        out.checked += 1
        real = [a for a in members if is_real_account(ledger, a)]
        if len(real) < 2:
            out.considered.append(
                ([a.label() or a.full_name for a in members],
                 "only one of them has ever held anything, so the other is an unused "
                 "chart row rather than a second row for the same account"))
            continue
        sides = [_side(ledger, a, queue, profile, reconciled_through, recon)
                 for a in real]
        # The original is the one with the history: most posted lines, then the
        # earliest posting. Never "the one with a number": a numbered account can
        # be the newer one in a chart that numbers everything.
        sides.sort(key=lambda s: (-s.lines, s.first_posting or _LATE))
        original, duplicate = sides[0], sides[1]
        # The plug belongs to the row the feed was linked onto. Where it turns up
        # on the row with the history instead, the roles are the other way round
        # and the two are swapped rather than the plug being ignored.
        if duplicate.plug is None and original.plug is not None:
            original, duplicate = duplicate, original

        signals = [("the names match once numbering, case and a masked suffix are "
                    "normalized", f"both reduce to {name!r}")]
        if original.numbered != duplicate.numbered:
            numbered = original if original.numbered else duplicate
            unnumbered = duplicate if original.numbered else original
            signals.append(("one carries an account number and the other does not",
                            f"{numbered.label} is numbered, {unnumbered.label} is not"))
        if duplicate.queue_items != original.queue_items:
            signals.append(("the queue is on one of them only",
                            f"{duplicate.queue_items} item(s) against "
                            f"{original.queue_items}"))
        elif original.feed and duplicate.feed and original.feed != duplicate.feed:
            signals.append(("the feed is on one of them only",
                            f"{duplicate.label} feed {duplicate.feed}, "
                            f"{original.label} feed {original.feed}"))
        if original.lines and not duplicate.lines:
            signals.append(("one has history and the other has none",
                            f"{original.lines} posted line(s) against "
                            f"{duplicate.lines}"))
        elif (original.reconciled_months or original.reconciled_through) and not (
                duplicate.reconciled_months or duplicate.reconciled_through):
            signals.append(("one has been reconciled and the other never has",
                            f"{original.reconciled_months} month(s) against 0"))
        elif duplicate.lines and original.lines > duplicate.lines * 10:
            signals.append(("almost all the history is on one of them",
                            f"{original.lines} posted line(s) against "
                            f"{duplicate.lines}"))

        twin = Twin(account_type=acct_type, normalized=name,
                    original=original, duplicate=duplicate, signals=signals,
                    obe_balance=out.obe_balance,
                    obe_label=(obe.label() or obe.full_name) if obe is not None else "")
        if twin.plug is not None:
            signals.append(("an opening-balance plug sits on the row the feed was "
                            "linked onto",
                            f"{fmt(abs(twin.plug))} dated {iso(duplicate.plug_date)}"
                            + (", which Opening Balance Equity offsets exactly"
                               if twin.plug_offsets_obe else
                               ", which Opening Balance Equity does not offset")))
        if len(members) > len(real):
            out.considered.append(
                ([a.label() or a.full_name for a in members if a not in real],
                 f"named the same as {name!r} but never posted to, so it takes no "
                 f"part in the pair above"))
        if twin.strength < MIN_SIGNALS:
            out.considered.append(
                ([s.label for s in sides],
                 f"only {twin.strength} of 5 signals, which is one coincidence of "
                 f"naming and not evidence of a re-link"))
            continue
        out.findings.append(twin)

    out.findings.sort(key=lambda t: (not t.certain, -t.strength, t.original.key))
    return out


# ------------------------------------------------------------------ render

_INTRO = (
    "A rebuilt bank link can leave two chart rows for one real-world account: the "
    "original, holding the reconciled history and no feed, and a new unnumbered "
    "row holding the live feed, the whole queue and an opening-balance plug. Each "
    "finding names both rows and says which holds what. None of them proposes a "
    "disposition, because which row survives and in what order is a decision with "
    "consequences for a period that is already closed."
)


def _wrap(text, width=76, indent="") -> list:
    out, line = [], ""
    for word in str(text).split():
        if line and len(line) + 1 + len(word) > width:
            out.append(indent + line)
            line = word
        else:
            line = f"{line} {word}".strip()
    out.append(indent + line)
    return out


def render(scanned, *, limit=None, heading="Two chart rows for one account") -> list:
    if not isinstance(scanned, TwinScan):
        scanned = TwinScan(findings=list(scanned or []))
    out = [heading, "-" * min(len(heading), 78)]
    out += _wrap(f"{len(scanned.findings)} of {scanned.checked} same-name group(s) are "
                 f"two rows for one account, {len(scanned.certain)} of them carrying "
                 f"the opening-balance plug that makes it certain.", indent="  ")
    if scanned.findings:
        out.append("")
        out += _wrap(_INTRO, indent="  ")
    shown = scanned.findings if limit is None else scanned.findings[:limit]
    for twin in shown:
        lines = twin.lines()
        out.append("")
        out += _wrap(lines[0], indent="  ")
        for line in lines[1:]:
            out += _wrap(line, width=74, indent="    ")
    if scanned.not_checked:
        out.append("")
        out += _wrap("; ".join(scanned.not_checked), indent="  ")
    return out


def render_markdown(scanned, *, heading="## Two chart rows for one account") -> list:
    if not isinstance(scanned, TwinScan):
        scanned = TwinScan(findings=list(scanned or []))
    out = [heading, ""]
    if not scanned.findings:
        out += [f"None. {scanned.checked} group(s) of accounts share a name once "
                f"numbering and case are normalized, and none of them carries the "
                f"other evidence of a re-link.", ""]
    else:
        out += [f"{len(scanned.findings)} of {scanned.checked} same-name group(s) are "
                f"two rows for one account, {len(scanned.certain)} of them carrying the "
                f"opening-balance plug that makes it certain.", "", _INTRO, ""]
        for twin in scanned.findings:
            lines = twin.lines()
            out.append(f"- **{lines[0]}**")
            for line in lines[1:]:
                out.append(f"  {line}")
            out.append("")
    if scanned.considered:
        out.append(f"{len(scanned.considered)} same-name group(s) were considered and "
                   f"not raised:")
        out.append("")
        for labels, why in scanned.considered:
            out.append(f"- {', '.join(labels)}: {why}.")
        out.append("")
    if scanned.not_checked:
        for note in scanned.not_checked:
            out.append(f"{note}.")
        out.append("")
    return out
