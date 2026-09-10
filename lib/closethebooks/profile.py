"""The profile: everything the engine knows about one company's books.

JSON, not YAML, because YAML is not in the standard library and this has to run
inside a ChatGPT sandbox that cannot install anything.

**A profile is the only route by which company-specific facts enter the
engine.** The engine itself knows about bookkeeping in general and nothing about
anyone in particular. That separation is what lets the same code be a public
tool and a firm's client tooling at once: the public repo ships the engine and a
synthetic example, and a real company's profile lives outside it, ignored by
git, held by whoever owns the books.

Most of a profile is not written by hand. `precedent.py` mines what-goes-where
rules from a company's own posted history, so a new user gets a working profile
from their own exports and is asked only what history cannot answer.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from .model import ROLES, Rule
from .util import money, parse_date, try_date, norm_text

SCHEMA_VERSION = 1


class ProfileError(Exception):
    pass


@dataclass
class Entity:
    name: str = ""
    fiscal_year: str = ""
    basis: str = ""              # accrual | cash
    end_use: str = ""            # what the books are FOR: a return, a sale, a loan
    deadline: str = ""           # ISO date
    preparer: str = ""
    materiality: str = ""        # a number, or blank; blank means report everything
    currency: str = "USD"

    def blanks(self):
        """Which engagement facts are missing. These decide what 'correct' means.

        Basis, end use, deadline and materiality are asked before any work
        starts, because they change what counts as a defect. A book that is fine
        for a tax return can be unfit for a sale.
        """
        out = []
        for f in ("basis", "end_use", "deadline", "materiality"):
            if not str(getattr(self, f) or "").strip():
                out.append(f)
        return out


@dataclass
class AccountSpec:
    """One of the company's real-world accounts, and how to read its statements."""
    book: str = ""               # account key in the chart
    label: str = ""
    institution: str = ""
    mask: str = ""
    kind: str = "bank"           # bank | card
    feed: str = "live"           # live | dead | none
    feed_last: str = ""          # ISO date the feed last synced
    parser: str = "generic_csv"
    note: str = ""

    @property
    def feed_is_dead(self) -> bool:
        return self.feed == "dead"


@dataclass
class Profile:
    path: str = ""
    version: int = SCHEMA_VERSION
    entity: Entity = field(default_factory=Entity)
    chart: list = field(default_factory=list)          # dicts from the Account List
    accounts: list = field(default_factory=list)       # AccountSpec
    what_goes_where: list = field(default_factory=list)  # Rule
    recurring_entries: dict = field(default_factory=dict)
    known_figures: list = field(default_factory=list)
    open_questions: list = field(default_factory=list)
    decisions_made: list = field(default_factory=list)
    decisions_reserved_to_founder: list = field(default_factory=list)
    structural_plan: list = field(default_factory=list)
    wind_down: dict = field(default_factory=dict)
    notes: str = ""

    # ------------------------------------------------------------- questions

    def unanswered(self) -> list:
        return [q for q in self.open_questions if not q.get("answer")]

    def answer(self, qid: str, text: str, on: str = "", source: str = "founder"):
        """Record an answer. Every answer carries who said it and when.

        An answer is evidence, and evidence without a date and a source is just
        an assertion that will be indistinguishable from a guess in a month.
        """
        for q in self.open_questions:
            if q.get("id") == qid:
                q["answer"] = text
                q["answered_on"] = on or _today()
                q["source"] = source
                return q
        raise ProfileError(f"no open question with id {qid!r}")

    def blocked_rules(self) -> list:
        """Rules that cannot be applied until a question is answered."""
        pending = {q["id"] for q in self.unanswered()}
        return [r for r in self.what_goes_where
                if pending.intersection(set(getattr(r, "blocked_by", []) or []))]

    # ------------------------------------------------------------- lookups

    def rules(self) -> list:
        return list(self.what_goes_where)

    def account_spec(self, book_key: str):
        for a in self.accounts:
            if a.book == book_key:
                return a
        return None

    def dead_feeds(self) -> list:
        return [a for a in self.accounts if a.feed_is_dead]

    def figure(self, account: str, default=None):
        for f in self.known_figures:
            if f.get("account") == account:
                return money(f.get("value"))
        return default

    def wind_down_ready(self):
        """Wind-down entries need every one of these answered first.

        Liquidating entries are the least reversible thing in this repo, and
        each one rests on a fact only the owner has. Missing any of them, the
        skill refuses rather than assuming.
        """
        required = ("target_date", "receivable_plan", "safe_terms",
                    "cap_table", "resolution_date")
        missing = [k for k in required if not str(self.wind_down.get(k) or "").strip()]
        return (not missing), missing


def _today() -> str:
    import datetime
    return datetime.date.today().isoformat()


# ----------------------------------------------------------------- load/save

def load(path) -> Profile:
    p = Path(path)
    if not p.exists():
        raise ProfileError(
            f"no profile at {p}.\n"
            f"Run `books.py learn` to build one from your own exports, or copy "
            f"profiles/example-acme-robotics.json and edit it."
        )
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ProfileError(f"{p} is not valid JSON: {exc}") from exc

    prof = Profile(path=str(p))
    prof.version = int(data.get("version", SCHEMA_VERSION))
    prof.entity = Entity(**{k: v for k, v in (data.get("entity") or {}).items()
                            if k in Entity.__annotations__})
    prof.chart = data.get("chart") or []
    prof.accounts = [
        AccountSpec(**{k: v for k, v in a.items() if k in AccountSpec.__annotations__})
        for a in (data.get("accounts") or [])
    ]
    prof.what_goes_where = [_rule_from(d) for d in (data.get("what_goes_where") or [])]
    prof.recurring_entries = data.get("recurring_entries") or {}
    prof.known_figures = data.get("known_figures") or []
    prof.open_questions = data.get("open_questions") or []
    prof.decisions_made = data.get("decisions_made") or []
    prof.decisions_reserved_to_founder = data.get("decisions_reserved_to_founder") or []
    prof.structural_plan = data.get("structural_plan") or []
    prof.wind_down = data.get("wind_down") or {}
    prof.notes = data.get("notes", "")
    validate(prof)
    return prof


def _rule_from(d: dict) -> Rule:
    return Rule(
        id=d.get("id", ""),
        account=d.get("account", ""),
        account_full=d.get("account_full", ""),
        match_contains=tuple(d.get("match_contains") or ()),
        match_regex=d.get("match_regex", ""),
        direction=d.get("direction", "any"),
        klass=d.get("class", d.get("klass", "")),
        source=d.get("source", ""),
        confidence=float(d.get("confidence", 0.0) or 0.0),
        support=int(d.get("support", 0) or 0),
        conflicts=int(d.get("conflicts", 0) or 0),
        note=d.get("note", ""),
    )


def _rule_to(r: Rule) -> dict:
    return {
        "id": r.id, "account": r.account, "account_full": r.account_full,
        "match_contains": list(r.match_contains), "match_regex": r.match_regex,
        "direction": r.direction, "class": r.klass, "source": r.source,
        "confidence": round(r.confidence, 4), "support": r.support,
        "conflicts": r.conflicts, "note": r.note,
    }


def save(prof: Profile, path=None) -> str:
    p = Path(path or prof.path)
    data = {
        "version": prof.version,
        "entity": {k: getattr(prof.entity, k) for k in Entity.__annotations__},
        "chart": prof.chart,
        "accounts": [{k: getattr(a, k) for k in AccountSpec.__annotations__} for a in prof.accounts],
        "what_goes_where": [_rule_to(r) for r in prof.what_goes_where],
        "recurring_entries": prof.recurring_entries,
        "known_figures": prof.known_figures,
        "open_questions": prof.open_questions,
        "decisions_made": prof.decisions_made,
        "decisions_reserved_to_founder": prof.decisions_reserved_to_founder,
        "structural_plan": prof.structural_plan,
        "wind_down": prof.wind_down,
        "notes": prof.notes,
    }
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, indent=2, sort_keys=False) + "\n", encoding="utf-8")
    return str(p)


def validate(prof: Profile) -> list:
    """Structural problems that would make the engine behave wrongly.

    Deliberately strict about two things: a rule with no source, and a role
    outside the known set. A sourceless rule is an opinion nobody can check, and
    an unknown role silently disables the checks that key off it.
    """
    problems = []
    seen = set()
    for r in prof.what_goes_where:
        if not r.id:
            problems.append("a what_goes_where rule has no id")
        if r.id in seen:
            problems.append(f"duplicate rule id {r.id!r}")
        seen.add(r.id)
        if not r.account:
            problems.append(f"rule {r.id}: no account")
        if not r.source:
            problems.append(f"rule {r.id}: no source. Every rule must name the rows "
                            f"or the document it came from.")
        if not r.match_contains and not r.match_regex:
            problems.append(f"rule {r.id}: nothing to match on")
    for row in prof.chart:
        role = row.get("role", "other")
        if role not in ROLES:
            problems.append(f"chart account {row.get('number') or row.get('name')}: "
                            f"unknown role {role!r}; expected one of {', '.join(ROLES)}")
    qids = set()
    for q in prof.open_questions:
        if q.get("id") in qids:
            problems.append(f"duplicate question id {q.get('id')!r}")
        qids.add(q.get("id"))
    if problems:
        raise ProfileError("profile is not usable:\n  - " + "\n  - ".join(problems))
    return problems
