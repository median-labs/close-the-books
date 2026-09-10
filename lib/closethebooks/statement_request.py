"""What statements this company needs, which months, and why each one.

    from closethebooks.statement_request import build
    request = build(ledger, profile, statements_dir="statements/")
    print(request.render())
    open("reports/statement-request.md", "w").write(request.render_markdown())

WHY THIS IS A COMMAND AND NOT AN ERROR MESSAGE

A founder starting a catch-up has no statements in a working directory. That is
the NORMAL state, not a mistake, and it is the state the tool should be most
useful in. Four commands used to refuse outright when `statements/` was empty,
which made the whole reconciliation half of the tool unreachable for exactly the
person it was written for, and told them only that a folder was empty.

Everything needed to ask precisely is already in hand. The chart says which
accounts are real bank and card accounts, as opposed to parent rollups and
template rows no institution issues a statement for. The ledger says which
months each of those accounts has activity in. The profile records which feeds
died and when. The balances say which accounts are sitting on the wrong side and
therefore need a statement to settle which way. So the answer to "what do you
need" is a list of accounts and months with a reason against each, which is
something a founder can forward to their bookkeeper or work through in a banking
portal without having to think.

THE REASON IS PER ACCOUNT, BECAUSE THE REASONS DIFFER

A feed that stopped in April 2025 needs everything from April 2025 onward: after
that date the books cannot contain what the bank does, so no amount of
categorizing will find it. An account whose balance is on the wrong side needs
its statement to settle whether the books are missing deposits or the account
really was overdrawn. An account that already reconciles cleanly needs nothing,
and saying so is as much a part of the request as the asking: a list that asks
for everything is a list nobody works.

WHAT IS NEVER ASKED FOR

A parent rollup (`100000 Current Assets`) and a chart row nothing has ever been
posted to (`223000 Credit Card 3`) are not accounts anybody can get a statement
for. `model.is_real_account` is the same filter the reconciliation uses, and on
one real chart it removed 15 of 21 rows. An open item nobody can close is not an
open item; it is noise hiding the ones that can be.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field

from .model import is_real_account
from .sides import balance_text, scan as scan_sides
from .util import iso, month_range, norm_text

__all__ = ["Ask", "StatementRequest", "build"]

# Why one account needs statements, worst first. The order is the order a reader
# should work them in: an account the books cannot possibly be right about comes
# before one that is merely unverified.
WHY_ORDER = ("wrong_side", "dead_feed", "gap", "unreconciled", "unverified",
             "missing_month")

WHY_TEXT = {
    "wrong_side": (
        "Its balance is on the wrong side and only a statement settles which way. "
    ),
    "dead_feed": (
        "The bank feed stopped, so from that month on the books cannot contain what "
        "the bank does. No amount of categorizing finds a transaction that was never "
        "imported. "
    ),
    "gap": (
        "The books are silent for months the account was still open, which is either "
        "a feed that stopped or a genuinely dormant account. "
    ),
    "unreconciled": (
        "The book balance and the statement balance disagree for at least one month "
        "already held. "
    ),
    "unverified": (
        "Nothing external has ever confirmed this account's balance. "
    ),
    "missing_month": (
        "Most of this account is already covered. "
    ),
}


def _month(value) -> str:
    """A "YYYY-MM" out of a month string, an ISO date or a date object."""
    if value is None:
        return ""
    if hasattr(value, "year") and hasattr(value, "month"):
        return f"{value.year:04d}-{value.month:02d}"
    text = str(value).strip()
    return text[:7] if len(text) >= 7 else ""


def _month_start(month: str):
    return dt.date(int(month[:4]), int(month[5:7]), 1)


@dataclass
class Ask:
    """One account, the months wanted, and why. `months` is never empty."""

    account_key: str = ""
    label: str = ""
    kind: str = "bank"                 # bank | card
    institution: str = ""
    mask: str = ""
    months: list = field(default_factory=list)      # "YYYY-MM", ascending
    held: list = field(default_factory=list)        # already in statements/
    why: str = "unverified"                         # a key of WHY_TEXT
    detail: str = ""                                # the sentence specific to this one
    # The months the primary reason accounts for. The rest of `months` are
    # wanted for an ordinary reason (reconciling the months the books DO cover),
    # and `also` says so. Without it the request reads as a contradiction: a
    # reason that says "everything from 2025-05" over a download that says
    # "2024-01 to 2025-12".
    because_months: list = field(default_factory=list)
    also: str = ""

    @property
    def span(self) -> str:
        if not self.months:
            return ""
        if len(self.months) == 1:
            return self.months[0]
        return f"{self.months[0]} to {self.months[-1]}"

    @property
    def contiguous(self) -> bool:
        if len(self.months) < 2:
            return True
        return month_range(_month_start(self.months[0]),
                           _month_start(self.months[-1])) == self.months

    def months_text(self) -> str:
        """The span where the months run straight through, else every month.

        A founder downloading statements works a range. Printing "2025-01 to
        2025-12" for twelve consecutive months and the twelve names for a
        scattered set is the difference between one action and twelve.
        """
        if self.contiguous:
            return f"{self.span} ({len(self.months)} month(s))"
        return ", ".join(self.months)

    def reason(self) -> str:
        return " ".join(x for x in (WHY_TEXT.get(self.why, "").strip(),
                                    self.detail.strip(), self.also.strip()) if x)

    def as_row(self) -> dict:
        return {
            "account": self.label,
            "kind": self.kind,
            "months": self.months_text(),
            "held": f"{len(self.held)}",
            "why": self.reason(),
        }


@dataclass
class StatementRequest:
    """Everything this company has to go and get, and what it already has."""

    company: str = ""
    period_start: object = None
    period_end: object = None
    asks: list = field(default_factory=list)
    settled: list = field(default_factory=list)     # (label, why nothing is needed)
    excluded: list = field(default_factory=list)    # (label, why it can never have one)
    notes: list = field(default_factory=list)
    statements_dir: str = ""

    @property
    def months_wanted(self) -> int:
        return sum(len(a.months) for a in self.asks)

    @property
    def months_held(self) -> int:
        return sum(len(a.held) for a in self.asks) + len(self.settled)

    def __bool__(self) -> bool:
        return bool(self.asks)

    # --------------------------------------------------------------- output
    def _headline(self) -> str:
        if not self.asks:
            return (f"Nothing to ask for. {len(self.settled)} account(s) already have "
                    f"every statement the period needs.")
        return (f"{len(self.asks)} account(s) need statements, "
                f"{self.months_wanted} account-month(s) in total, over "
                f"{iso(self.period_start)} to {iso(self.period_end)}.")

    def render(self, *, limit=None) -> list:
        """Terminal lines. The same content as the file, capped for a screen."""
        out = [self._headline()]
        if not self.asks:
            for label, why in self.settled:
                wrapped = _wrap(f"{label}: {why}", 72)
                out.append(f"  {wrapped[0]}")
                out += [f"    {line}" for line in wrapped[1:]]
            return out
        out.append("")
        rows = self.asks if limit is None else self.asks[:limit]
        for ask in rows:
            out.append(f"  {ask.label}")
            months = _wrap(ask.months_text(), 62)
            out.append(f"    download   {months[0]}")
            for extra in months[1:]:
                out.append(f"               {extra}")
            if ask.held:
                out.append(f"    already in {self.statements_dir or 'statements/'}: "
                           f"{len(ask.held)} month(s), so those are not asked for again")
            for line in _wrap(ask.reason(), 72):
                out.append(f"    {line}")
            out.append("")
        if limit is not None and len(self.asks) > limit:
            out.append(f"  ... {len(self.asks) - limit} more account(s), all of them in "
                       f"the written request.")
            out.append("")
        for label, why in self.settled:
            wrapped = _wrap(f"{label}: nothing needed, {why}", 72)
            out.append(f"  {wrapped[0]}")
            out += [f"    {line}" for line in wrapped[1:]]
        if self.settled:
            out.append("")
        for note in self.notes:
            for line in _wrap(note, 76):
                out.append(f"  {line}")
        return out

    def render_markdown(self) -> str:
        """The file a founder forwards. Written to be pasted into an email."""
        out = [
            f"# Statements needed, {self.company or 'this company'}",
            "",
            self._headline(),
            "",
            "Each account below says which months to download and why that account "
            "needs them. Where an account needs nothing it says so, because a list "
            "that asks for everything is a list nobody works.",
            "",
        ]
        if self.asks:
            out += [
                "| Account | Months to download | Already held | Why |",
                "| --- | --- | ---: | --- |",
            ]
            for ask in self.asks:
                out.append(f"| {ask.label} | {ask.months_text()} | {len(ask.held)} | "
                           f"{ask.reason()} |")
            out.append("")
            out += ["## Account by account", ""]
            for ask in self.asks:
                out.append(f"### {ask.label}")
                out.append("")
                out.append(f"- Download: **{ask.months_text()}**")
                if ask.mask:
                    out.append(f"- Account ends {ask.mask}")
                if ask.held:
                    out.append(f"- Already held: {', '.join(ask.held)}")
                out.append(f"- Why: {ask.reason()}")
                out.append("")
        if self.settled:
            out += ["## Accounts that need nothing", ""]
            for label, why in self.settled:
                out.append(f"- {label}: {why}")
            out.append("")
        if self.excluded:
            out += ["## Chart rows no institution issues a statement for", ""]
            out.append("These are not accounts anybody can get a document for, so they "
                       "are not asked for. They are listed rather than dropped, so a "
                       "reader can disagree with the judgement.")
            out.append("")
            for label, why in self.excluded:
                out.append(f"- {label}: {why}")
            out.append("")
        if self.notes:
            out += ["## What to do with them", ""]
            for note in self.notes:
                out.append(note)
                out.append("")
        return "\n".join(out) + "\n"


def _wrap(text, width=72) -> list:
    out, line = [], ""
    for word in str(text).split():
        if line and len(line) + 1 + len(word) > width:
            out.append(line)
            line = word
        else:
            line = f"{line} {word}".strip()
    out.append(line)
    return [l for l in out if l]


# ------------------------------------------------------------------ building

def _held_months(coverage_grid, account_key) -> set:
    """Months already in the statements folder for this account, from coverage.

    A coverage grid is keyed by whatever the caller declared, which is the
    account's LABEL from the CLI ("102000 Second Checking (3947)") and its bare
    key from a library caller. Both are accepted, because asking a founder twice
    for a statement they already sent is the fastest way to lose their goodwill,
    and a key mismatch here would do exactly that silently.
    """
    if coverage_grid is None or not account_key:
        return set()
    present = getattr(coverage_grid, "present", {}) or {}
    wanted = norm_text(account_key)
    if not wanted:
        return set()
    for key, months in present.items():
        grid_key = norm_text(key)
        if grid_key == wanted or grid_key.startswith(wanted + " ") \
                or wanted.startswith(grid_key + " "):
            return {m for m, hits in months.items() if hits}
    return set()


def _account_months(ledger, account) -> set:
    aliases = ledger.aliases_for(getattr(account, "key", account))
    return {line.month for line in ledger.lines
            if norm_text(line.account) in aliases
            or norm_text(getattr(line, "account_full", "")) in aliases}


def _crossed_sides_at(ledger, account, months) -> str:
    """The first month this account's balance sat on the wrong side, or "".

    Answers the question a reader asks next: how far back does the statement
    have to go. Going back only to the period end would ask for the month the
    balance was noticed rather than the month it went wrong.
    """
    from .sides import on_wrong_side
    for month in months:
        end = _month_end(month)
        balance = ledger.balance_as_of(getattr(account, "key", account), end)
        if balance is not None and on_wrong_side(balance, account):
            return month
    return ""


def _month_end(month: str):
    import calendar
    y, m = int(month[:4]), int(month[5:7])
    return dt.date(y, m, calendar.monthrange(y, m)[1])


def build(ledger, profile, *, coverage_grid=None, recon=None, statements_dir="",
          period_start=None, period_end=None) -> StatementRequest:
    """The request this company's own books imply.

    `coverage_grid` is a `coverage.CoverageGrid` over the statements folder, so
    a month already held is never asked for twice; leave it out and everything
    is treated as missing. `recon` is a `recon.ReconReport`, which supplies the
    one reason the ledger alone cannot: an account-month already checked that
    did not tie.
    """
    period_start = period_start or getattr(ledger, "period_start", None)
    period_end = period_end or getattr(ledger, "period_end", None)
    if period_start is None or period_end is None:
        raise ValueError("a statement request needs a period; the exports state none")
    months = month_range(period_start, period_end)
    last_month = months[-1]

    request = StatementRequest(
        company=(getattr(ledger, "company", "")
                 or getattr(getattr(profile, "entity", None), "name", "")),
        period_start=period_start, period_end=period_end,
        statements_dir=statements_dir,
    )

    specs = {spec.book: spec for spec in (getattr(profile, "accounts", None) or [])}
    wrong = {f.key: f for f in scan_sides(ledger, as_of=period_end).findings}

    unreconciled_by_account = {}
    for row in (getattr(recon, "unreconciled", None) or []):
        unreconciled_by_account.setdefault(row.account_key, []).append(row.month)

    for account in sorted((getattr(ledger, "accounts", {}) or {}).values(),
                          key=lambda a: (a.number or "", a.full_name)):
        if account.role not in ("bank", "card") and account.type not in ("bank", "credit card"):
            continue
        label = account.label() or account.full_name
        if ledger.is_rollup(account):
            request.excluded.append(
                (label, "it is a parent that totals its children, and no institution "
                        "issues a statement for a subtotal"))
            continue
        if not is_real_account(ledger, account):
            request.excluded.append(
                (label, "nothing has ever been posted to it and it carries no balance, "
                        "so there is no account at an institution behind it"))
            continue

        spec = specs.get(account.key)
        kind = "card" if (account.role == "card" or account.type == "credit card") else "bank"
        active = sorted(m for m in _account_months(ledger, account) if m in months)
        held = _held_months(coverage_grid, account.key) | _held_months(
            coverage_grid, getattr(spec, "label", "") or account.key)

        wanted = set(active)
        why, detail = "unverified", ""
        reason_months = set(active)

        # 1. A dead feed. Everything from the month it stopped, because after
        #    that the books cannot contain what the bank does.
        feed_last = _month(getattr(spec, "feed_last", "")) if spec else ""
        feed_dead = bool(spec and getattr(spec, "feed_is_dead", False))
        if feed_dead and feed_last and feed_last <= last_month:
            reason_months = set(month_range(_month_start(feed_last),
                                            _month_start(last_month)))
            wanted |= reason_months
            why = "dead_feed"
            detail = (f"The last transaction it imported was in {feed_last}, and the "
                      f"books run to {last_month}, so everything from {feed_last} "
                      f"onward has to come off the statement.")

        # 2. A balance on the wrong side. Back to the month it crossed, because
        #    asking only for the period end asks for the month somebody noticed.
        finding = wrong.get(account.key)
        if finding is not None:
            crossed = _crossed_sides_at(ledger, account, months) or last_month
            reason_months = set(month_range(_month_start(crossed),
                                            _month_start(last_month)))
            wanted |= reason_months
            why = "wrong_side"
            detail = (
                f"At {iso(period_end)} the books show {finding.rendered()} on an account "
                f"that is {'debit' if finding.normal == 'Dr' else 'credit'}-normal"
                + (f", and they first show it on that side in {crossed}" if crossed else "")
                + ". " + finding.meaning + " The statement is what settles which, and "
                "nothing in the exports can."
            )
            if feed_dead and feed_last:
                detail += (f" Its feed also stopped in {feed_last}, so the months from "
                           f"there are needed either way.")

        # 3. Months the books are silent about while the account was open. A
        #    quiet month is either a stopped feed or a genuinely dormant
        #    account, and the statement is the only thing that says which.
        if why == "unverified" and active:
            silent = [m for m in months if m >= active[0] and m not in active]
            if silent:
                reason_months = set(silent)
                wanted |= reason_months
                why = "gap"
                detail = (f"It posted in {len(active)} of the {len(months)} months in the "
                          f"period and is silent in {len(silent)}, the first being "
                          f"{silent[0]}. A silent month is either a feed that stopped or "
                          f"a dormant account, and only the statement says which.")

        # 4. A month already held that did not tie.
        broken = sorted(set(unreconciled_by_account.get(account.key, [])))
        if broken and why in ("unverified", "gap"):
            reason_months = set(broken) | reason_months
            why = "unreconciled"
            detail = (f"{len(broken)} month(s) already checked do not tie: "
                      + ", ".join(broken[:4])
                      + ". The statement for the month either side is usually what "
                        "explains a difference at a period edge.")

        if why == "unverified":
            reason_months = set(active)
            detail = (f"It posted in {len(active)} month(s) of the period and nothing "
                      f"external has ever been checked against it. An unchecked account "
                      f"and a checked account that agrees are not the same thing, and "
                      f"they never read the same in the reconciliation.")

        missing = sorted(m for m in wanted if m not in held)
        # A partly covered account is a different ask from an unchecked one, and
        # saying "nothing has ever confirmed this balance" over thirteen held
        # months reads as a tool that has not looked in its own folder.
        if why == "unverified" and held:
            covered = sorted(m for m in wanted if m in held)
            gap_ask = Ask(months=missing)
            why = "missing_month"
            reason_months = set(missing)
            detail = (f"{len(covered)} of the {len(covered) + len(missing)} month(s) it "
                      f"needs are already held. The missing one(s), "
                      + (gap_ask.span if gap_ask.contiguous else ", ".join(missing))
                      + ", read \"cannot check\" in the reconciliation, which is a "
                        "different answer from 0.00 and from a month that agrees.")
        # Which of those months the primary reason actually accounts for, so the
        # request can say why the others are on the list too.
        because = sorted(m for m in reason_months if m in missing)
        rest = [m for m in missing if m not in reason_months]
        also = ""
        if rest:
            rest_ask = Ask(months=rest)
            rest_text = (rest_ask.span if rest_ask.contiguous else ", ".join(rest))
            # Short on purpose. The reason a month-by-month tie-out is worth
            # asking for is the same for every account, so it is said once in
            # `notes` rather than repeated down a table.
            also = (f"The other {len(rest)} month(s), {rest_text}, are asked for so "
                    f"the balance can be tied month by month.")
        balance = ledger.balance_as_of(account.key, period_end)
        if not missing:
            request.settled.append((
                label,
                f"every month it was active is already in {statements_dir or 'statements/'}"
                + (f", and the books show {balance_text(balance, account)} at "
                   f"{iso(period_end)}" if balance is not None else "")))
            continue
        if not wanted:
            request.settled.append((
                label,
                "it has no activity in the period and carries "
                + (balance_text(balance, account) if balance is not None
                   else "no determinable balance")
                + ", so there is nothing to check against"))
            continue

        request.asks.append(Ask(
            account_key=account.key, label=label, kind=kind,
            institution=getattr(spec, "institution", "") if spec else "",
            mask=getattr(spec, "mask", "") if spec else "",
            months=missing, held=sorted(held), why=why, detail=detail,
            because_months=because, also=also,
        ))

    request.asks.sort(key=lambda a: (WHY_ORDER.index(a.why) if a.why in WHY_ORDER else 99,
                                     -len(a.months), a.account_key))

    request.notes.append(
        "Download them as CSV where the institution offers it and PDF where it does "
        "not, one file per account per month, and put them in "
        f"{statements_dir or 'statements/'}. The file name only has to contain the "
        "last few digits of the account number and the month; nothing has to be "
        "renamed to a convention.")
    if any(a.also for a in request.asks):
        request.notes.append(
            "Several accounts ask for months beyond the ones their reason names. That "
            "is so each balance can be tied month by month rather than only at the "
            "year end: a difference found inside one month is a morning's work, and "
            "the same difference found once at the year end is a year to search.")
    request.notes.append(
        "Include the accounts nobody thinks are being used. A dormant account with a "
        "forgotten annual fee on it is exactly the kind of thing that turns up here, "
        "and it is cheaper to look than to be told about it by a tax preparer.")
    if any(a.why == "dead_feed" for a in request.asks):
        request.notes.append(
            "For the accounts whose feed stopped, `books.py fill-gaps` turns those "
            "statements into upload files for the months QuickBooks never saw, so the "
            "transactions get into the books rather than only into a reconciliation.")
    return request
