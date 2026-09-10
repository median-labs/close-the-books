"""Entries nobody made: postings that arrived without a person behind them.

    from closethebooks.autoposted import scan
    found = scan(ledger, filing_years=[2025])
    for entry in found:
        print(entry.headline())

WHAT HAPPENED

A company stopped being worked in April. Its bookkeeper left, its books went
quiet, and every human who had ever touched the file moved on. In December, a
3,187.65 deduction appeared in the tax year, on an expense account, in a month
that holds no other entry, with nothing behind it. A bank rule matched a
descriptor and posted it, eight months after anyone was watching.

That entry is inside the year on the return. It is a deduction nobody can
support, in a period nobody reviewed, and the only reason anyone found it was
that a person scoped the file by hand. Nothing about the queue, the balances or
the trial balance would have raised it: it balances, it foots, and it sits in an
account where entries like it belong.

WHAT AN AUDIT LOG WOULD SETTLE, AND WHAT REPLACES IT

QuickBooks knows who created every transaction, and none of that reaches the
General Ledger, Journal or Trial Balance exports: there is no author column, no
created-at, no attachment count. Pass an `audit_log` and this module uses the
author directly and says so in the finding. Without one it falls back to four
signals, and it never states the author as a fact it does not have:

  1. **Alone in its period.** The entry is the only one its month holds. A month
     somebody worked has a spread of entries in it.
  2. **No attachment where the account's other entries have them.** Needs
     attachment data, which no QBO xlsx export carries. Supplied or skipped;
     never inferred.
  3. **An exact pattern match.** The counterparty has landed in the same account
     enough times that a rule could be written from the company's own history,
     and this entry lands there too. `precedent.vendor_key` does the matching, so
     the pattern is the one the company's own books already established.
  4. **A poster naming itself.** `[Gusto]`, `Opening Balance from Bank`, and
     the other strings an integration or a bank rule writes into a memo that a
     person would not type.

THE CAUSE IS NOT CLAIMED

An entry alone in a quiet month, matching a pattern exactly, is either machine
posted or is one person's single piece of work in a month nobody else touched.
The exports cannot separate those, so every finding names both and then names
the audit history that settles it. What IS claimed, and is a fact, is that the
entry sits inside a year being filed with nothing in the file supporting it.

RANKING IS BY TAX YEAR, NOT BY SIZE

A machine-posted entry outside every year being filed is untidy. The same entry
inside one is a deduction on a return, so `filing_years` sorts first and the
amount only breaks ties inside it.
"""

from __future__ import annotations

import datetime
import re
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Optional

from .precedent import NON_CATEGORY_ROLES, vendor_key
from .util import ZERO, fmt, iso, money, month_key, norm_text

__all__ = ["AutoEntry", "AutoScan", "scan", "render", "render_markdown",
           "POSTER_TELLS", "MIN_SIGNALS"]

# An entry is raised at this many signals. One signal is a coincidence: plenty
# of ordinary entries are the only one in a quiet month, and plenty of ordinary
# entries repeat a pattern.
MIN_SIGNALS = 2

# How many prior entries a counterparty needs in one account before "this one
# went there too" is a pattern rather than a repeat.
MIN_SUPPORT = 3

# Strings a poster writes about itself. Each one is a name an integration or a
# bank feed puts in a memo, a transaction type or a document number, and none of
# them is something a person types while categorising.
POSTER_TELLS = (
    re.compile(r"\[[a-z][a-z0-9 .&+-]{2,24}\]", re.I),   # "[Gusto]", "[Payroll Systems]"
    re.compile(r"\bopening balance from bank\b", re.I),
    re.compile(r"\b(bank|banking) rule\b", re.I),
    re.compile(r"\bauto[- ]?(add|added|match|matched|categori[sz]ed|post|posted)\b", re.I),
    re.compile(r"\brule (applied|matched)\b", re.I),
    re.compile(r"\b(system administration|qb ?o? ?auto|quickbooks online)\b", re.I),
    re.compile(r"\bimported by\b", re.I),
    re.compile(r"\brecurring (transaction|template)\b", re.I),
)

# Author names in an audit log that are automation rather than a person. The
# list is deliberately short: an author the log names and this list does not is
# reported as the author, never as automation.
AUTOMATED_AUTHORS = re.compile(
    r"\b(system|automat|bank rule|quickbooks|qbo|intuit|recurring|api|integration|"
    r"rippling|gusto|bill\.com|stripe|brex|ramp)\b", re.I)

_SIGNALS = {
    "alone": "the only entry its month holds",
    "no_attachment": "no attachment where this account's other entries carry them",
    "pattern": "lands exactly where this counterparty's own history says it would",
    "poster": "a memo, type or number naming an automated poster",
    "after_stop": "posted after the last hand-worked entry in the file",
}


# ------------------------------------------------------------------ helpers

def _entry_key(line):
    """What identifies one transaction across its legs.

    A document number where the export carries one, because both legs of a
    transaction share it. Where it does not, the date, type, counterparty and
    memo together, which is what the General Ledger gives and is enough: the
    same counterparty posting the same memo twice on one day in one transaction
    type is one transaction in every file this has been run against.
    """
    ident = (getattr(line, "txn_id", "") or getattr(line, "doc_num", "") or "").strip()
    if ident:
        # The date is carried alongside the number on purpose. Both legs of one
        # transaction always share a date, and a document number is unique only
        # within an export: pairing them means a reused number can never merge
        # two transactions into one finding.
        return ("id", ident, iso(line.date))
    return ("shape", iso(line.date), norm_text(line.txn_type),
            norm_text(line.name), norm_text(line.memo)[:60])


def _in_years(when, filing_years) -> Optional[int]:
    """The filing year `when` falls inside, or None.

    A year is an int for a calendar year, or a (start, end) pair for a fiscal
    year that is not the calendar year.
    """
    if when is None:
        return None
    for year in filing_years or ():
        if isinstance(year, (tuple, list)) and len(year) == 2:
            start, end = year
            if start <= when <= end:
                return getattr(end, "year", None)
        elif int(year) == when.year:
            return int(year)
    return None


# ------------------------------------------------------------------ results

@dataclass
class AutoEntry:
    """One transaction that arrived without evidence of a person behind it."""

    key: object = None
    date: object = None
    txn_type: str = ""
    name: str = ""
    memo: str = ""
    doc_num: str = ""
    amount: Decimal = ZERO
    accounts: list = field(default_factory=list)     # labels, funding leg first
    category_account: str = ""
    signals: list = field(default_factory=list)      # signal ids, in order
    evidence: dict = field(default_factory=dict)     # signal id -> what was measured
    filing_year: object = None
    author: str = ""                                 # only ever from an audit log
    author_is_automation: Optional[bool] = None

    @property
    def in_filing_year(self) -> bool:
        return self.filing_year is not None

    @property
    def strength(self) -> int:
        return len(self.signals)

    @property
    def from_audit_log(self) -> bool:
        return bool(self.author)

    @property
    def unwatched(self) -> bool:
        """True when it is the only entry its month holds.

        This is what separates expected automation from the entry that matters.
        A payroll integration posting every fortnight into a month full of other
        work is a machine doing its job with people around it. An entry alone in
        a month is a machine posting into a file nobody is reading, and on the
        file this was written from that was the one that reached a return.
        """
        return "alone" in self.signals

    def headline(self) -> str:
        where = self.category_account or (self.accounts[0] if self.accounts else "")
        head = (f"{fmt(self.amount)} posted to {where} on {iso(self.date)}"
                + (f", inside the {self.filing_year} year" if self.in_filing_year else "")
                + ".")
        if self.from_audit_log:
            return head + f" The audit log names {self.author} as its author."
        return head + f" {self.strength} of 4 signal(s) say nobody made it."

    def lines(self) -> list:
        out = [self.headline()]
        detail = [f"{self.txn_type or 'entry'}"]
        if self.name:
            detail.append(f"counterparty {self.name}")
        if self.doc_num:
            detail.append(f"number {self.doc_num}")
        if self.memo:
            detail.append(f"memo {self.memo[:70]!r}")
        if len(self.accounts) > 1:
            detail.append("accounts " + " and ".join(self.accounts))
        out.append(", ".join(detail) + ".")
        for signal in self.signals:
            out.append(f"{_SIGNALS[signal].capitalize()}: {self.evidence.get(signal, '')}.")
        if self.from_audit_log:
            out.append(
                f"The audit history names {self.author} as the author, so nothing above "
                f"is being inferred. "
                + ("That author is automation." if self.author_is_automation else
                   "That author is not one this engine recognises as automation, so it "
                   "is read as a person."))
        else:
            out.append(
                "What this is not: proof of who posted it. An entry alone in a quiet "
                "month that lands exactly where a rule would put it was either posted "
                "by automation or entered by one person in a month nobody else touched, "
                "and no export separates the two. What settles it: the QuickBooks audit "
                "history for this transaction, which names its author and the date it "
                "was created.")
        if self.in_filing_year:
            out.append(
                f"Why this one is urgent rather than untidy: it is dated inside the "
                f"{self.filing_year} year, so {fmt(self.amount)} of it reaches the "
                f"return.")
        return out

    def as_row(self) -> dict:
        return {
            "date": iso(self.date),
            "amount": fmt(self.amount),
            "account": self.category_account,
            "type": self.txn_type,
            "counterparty": self.name,
            "number": self.doc_num,
            "filing year": str(self.filing_year or ""),
            "signals": f"{self.strength} of 4",
            "which": ", ".join(self.signals),
            "author": self.author,
        }


@dataclass
class AutoScan:
    findings: list = field(default_factory=list)
    entries: int = 0                      # N, every entry in the ledger
    filing_years: tuple = ()
    human_stopped: object = None
    human_stopped_basis: str = ""
    not_checked: list = field(default_factory=list)
    used_audit_log: bool = False

    @property
    def in_filing_year(self) -> list:
        return [f for f in self.findings if f.in_filing_year]

    @property
    def total_in_filing_year(self) -> Decimal:
        return sum((f.amount for f in self.in_filing_year), ZERO)

    @property
    def unwatched(self) -> list:
        """Findings alone in their month: posted into a file nobody was reading."""
        return [f for f in self.findings if f.unwatched]

    def __bool__(self) -> bool:
        return bool(self.findings)

    def __len__(self) -> int:
        return len(self.findings)

    def __iter__(self):
        return iter(self.findings)


# --------------------------------------------------------------- the scan

def _group_entries(ledger) -> dict:
    out = {}
    for line in getattr(ledger, "lines", []):
        if line.date is None:
            continue
        out.setdefault(_entry_key(line), []).append(line)
    return out


def _category_leg(ledger, lines):
    """The account the entry is ABOUT, not the one it was funded from."""
    for line in lines:
        account = ledger.account(line.account) or ledger.account(line.account_full)
        role = getattr(account, "role", "other")
        if role not in NON_CATEGORY_ROLES:
            return account, (account.label() if account is not None else line.account)
    first = lines[0]
    account = ledger.account(first.account)
    return account, (account.label() if account is not None else first.account)


def _poster_tell(lines) -> str:
    for line in lines:
        for field_name in ("memo", "txn_type", "doc_num", "name"):
            text = str(getattr(line, field_name, "") or "")
            for rx in POSTER_TELLS:
                found = rx.search(text)
                if found:
                    return f"{field_name} carries {found.group(0)!r}"
    return ""


def _pattern_support(entries, ledger) -> dict:
    """{(vendor, account label): [dates]} from the company's own posted history."""
    out = {}
    for key, lines in entries.items():
        vendor = vendor_key(lines[0].name or lines[0].memo)
        if not vendor:
            continue
        _, label = _category_leg(ledger, lines)
        out.setdefault((vendor, label), []).append(lines[0].date)
    return out


def scan(ledger, *, filing_years=(), audit_log=None, attachments=None,
         human_stopped=None, min_signals=MIN_SIGNALS) -> AutoScan:
    """Every entry the exports cannot attribute to a person.

    `filing_years` is the years whose returns are being prepared: an int per
    calendar year, or a `(start_date, end_date)` pair for a fiscal year that is
    not the calendar year. It decides the ORDER of the findings, because an
    unsupported entry inside a year on a return is a deduction and the same
    entry outside every one of them is untidiness.

    `audit_log` maps an entry's document number (or its `txn_id`) to the author
    QuickBooks recorded. When it is supplied the author is stated as a fact and
    the inference signals become supporting detail. When it is not, no finding
    ever names an author.

    `attachments` maps the same keys to an attachment count. Without it signal 2
    does not run and the scan says so, because no QBO xlsx export carries
    attachment data and inferring one from silence would be inventing evidence.

    `human_stopped` is the last date a person is known to have worked the file.
    Left out, it is derived: the last date carrying an entry with neither a
    poster tell nor an exact pattern match, and the derivation is stated on the
    result.
    """
    entries = _group_entries(ledger)
    out = AutoScan(entries=len(entries), filing_years=tuple(filing_years or ()))
    if not entries:
        return out

    audit_log = {str(k): v for k, v in (audit_log or {}).items()}
    attachments = {str(k): v for k, v in (attachments or {}).items()} if attachments else None
    out.used_audit_log = bool(audit_log)
    if not audit_log:
        out.not_checked.append(
            "no audit log was supplied, so no finding names an author. QuickBooks "
            "records one for every transaction and no xlsx export carries it")
    if attachments is None:
        out.not_checked.append(
            "no attachment data was supplied, so the missing-attachment signal did not "
            "run. It is not in any QBO xlsx export")

    per_month = {}
    for key, lines in entries.items():
        per_month[month_key(lines[0].date)] = per_month.get(month_key(lines[0].date), 0) + 1

    support = _pattern_support(entries, ledger)

    # Pass one: the two signals that do not depend on when people stopped.
    prepared = {}
    for key, lines in entries.items():
        first = lines[0]
        vendor = vendor_key(first.name or first.memo)
        account, label = _category_leg(ledger, lines)
        dates = sorted(support.get((vendor, label), []))
        prior = [d for d in dates if d < first.date]
        pattern = (vendor and len(prior) >= MIN_SUPPORT)
        poster = _poster_tell(lines)
        prepared[key] = (lines, first, vendor, label, prior, pattern, poster)

    # Pass two: when the last hand-worked entry posted. An entry with neither an
    # automated poster naming itself nor a pattern its own history already
    # established is the closest the exports come to somebody's own work.
    if human_stopped is None:
        hand = [first.date for (_, first, _, _, _, pattern, poster) in prepared.values()
                if not pattern and not poster]
        human_stopped = max(hand) if hand else None
        out.human_stopped_basis = (
            f"derived: the last of {len(hand)} of {len(entries)} entries carrying "
            f"neither a poster tell nor a pattern its own history already established"
            if hand else
            "could not be derived: every entry in the file carries a poster tell or "
            "matches a pattern")
    else:
        human_stopped = (human_stopped if isinstance(human_stopped, datetime.date)
                         else datetime.date.fromisoformat(str(human_stopped)))
        out.human_stopped_basis = "supplied by the caller"
    out.human_stopped = human_stopped

    for key, (lines, first, vendor, label, prior, pattern, poster) in prepared.items():
        signals, evidence = [], {}
        month = month_key(first.date)
        if per_month.get(month, 0) == 1:
            signals.append("alone")
            evidence["alone"] = (
                f"1 of {len(entries)} entries in the file is dated {month}, and it is "
                f"this one")
        ident = (first.txn_id or first.doc_num or "").strip()
        if attachments is not None and ident:
            if int(attachments.get(ident, 0)) == 0:
                signals.append("no_attachment")
                evidence["no_attachment"] = "the attachment count supplied for it is 0"
        if pattern:
            signals.append("pattern")
            evidence["pattern"] = (
                f"{len(prior)} earlier entry(s) for {vendor!r} posted to {label}, "
                f"{iso(prior[0])} through {iso(prior[-1])}, and so does this one")
        if poster:
            signals.append("poster")
            evidence["poster"] = poster
        if human_stopped is not None and first.date > human_stopped:
            signals.append("after_stop")
            evidence["after_stop"] = (
                f"{(first.date - human_stopped).days} day(s) after {iso(human_stopped)}, "
                f"the last hand-worked entry in the file")

        author = ""
        automated = None
        if ident and ident in audit_log:
            author = str(audit_log[ident])
            automated = bool(AUTOMATED_AUTHORS.search(author))

        if not author and len(signals) < min_signals:
            continue
        if author and not automated and len(signals) < min_signals:
            continue

        amount = sum((l.debit for l in lines), ZERO) or sum((l.credit for l in lines), ZERO)
        out.findings.append(AutoEntry(
            key=key, date=first.date, txn_type=first.txn_type, name=first.name,
            memo=first.memo, doc_num=first.doc_num, amount=money(amount),
            accounts=sorted({(ledger.account(l.account).label()
                              if ledger.account(l.account) is not None else l.account)
                             for l in lines}),
            category_account=label, signals=signals, evidence=evidence,
            filing_year=_in_years(first.date, filing_years),
            author=author, author_is_automation=automated,
        ))

    # Inside a filing year first, because that is what makes it a deduction.
    # Then alone-in-its-month, because that is the entry nobody was there to
    # see. Strength and amount only break ties inside those two.
    out.findings.sort(key=lambda f: (not f.in_filing_year, not f.unwatched,
                                     -f.strength, -f.amount, f.date))
    return out


# ------------------------------------------------------------------ render

_INTRO = (
    "Each entry below arrived without evidence in the exports of a person behind "
    "it. The signals are counted, never summed into a verdict, and the ones inside "
    "a year being filed come first: an unsupported entry in a closed year is "
    "untidy, and the same entry inside a return is a deduction."
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


def _summary(scanned) -> str:
    unwatched = [f for f in scanned.unwatched if f.in_filing_year]
    return (f"{len(scanned.findings)} of {scanned.entries} entry(s) carry no evidence "
            f"of a person behind them, {len(scanned.in_filing_year)} of them inside a "
            f"year being filed, totalling {fmt(scanned.total_in_filing_year)}. "
            f"{len(unwatched)} of those {len(scanned.in_filing_year)} are the only entry "
            f"their month holds, totalling "
            f"{fmt(sum((f.amount for f in unwatched), ZERO))}.")


def render(scanned, *, limit=None, heading="Entries nobody made") -> list:
    if not isinstance(scanned, AutoScan):
        scanned = AutoScan(findings=list(scanned or []))
    out = [heading, "-" * min(len(heading), 78)]
    out += _wrap(_summary(scanned), indent="  ")
    if scanned.human_stopped:
        out += _wrap(f"The last hand-worked entry in the file is dated "
                     f"{iso(scanned.human_stopped)} ({scanned.human_stopped_basis}).",
                     indent="  ")
    if scanned.findings:
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
    if scanned.not_checked:
        out.append("")
        out += _wrap(f"{len(scanned.not_checked)} check(s) could not run: "
                     + "; ".join(scanned.not_checked), indent="  ")
    return out


def render_markdown(scanned, *, heading="## Entries nobody made") -> list:
    if not isinstance(scanned, AutoScan):
        scanned = AutoScan(findings=list(scanned or []))
    out = [heading, "", _summary(scanned), ""]
    if scanned.human_stopped:
        out += [f"The last hand-worked entry in the file is dated "
                f"{iso(scanned.human_stopped)} ({scanned.human_stopped_basis}).", ""]
    if scanned.findings:
        out += [_INTRO, ""]
        for finding in scanned.findings:
            lines = finding.lines()
            out.append(f"- **{lines[0]}**")
            for line in lines[1:]:
                out.append(f"  {line}")
            out.append("")
    if scanned.not_checked:
        out.append(f"{len(scanned.not_checked)} check(s) could not run:")
        out.append("")
        for note in scanned.not_checked:
            out.append(f"- {note}.")
        out.append("")
    return out
