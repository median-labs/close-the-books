"""The evidence ledger: where every number in a deliverable came from.

    from closethebooks.evidence import Evidence, verify

    ev = Evidence("Acme Robotics Inc.", "FY2026", "out/acme-fy2026.html",
                  deliverable_pdf="out/acme-fy2026.pdf")
    ev.ledger_written("2026-07-01T09:12:00Z")
    ev.source("qbo-gl", kind="QuickBooks General Ledger export",
              period_requested="2026-01-01 to 2026-06-30",
              period_claimed="January 1 - June 30, 2026",
              expected=1684, returned=1684)
    ev.figure("124300.55", "Total revenue", source="qbo-gl",
              derivation="sum of signed amounts on income accounts, sign flipped")
    ev.check("balance sheet balances", lhs="812004.11", rhs="812004.11")
    ev.reconciliation("Operating bank 2026-06", book="41002.13",
                      statement="41002.13", evidence="acme-checking-2026-06.pdf")
    ev.write("out/acme-fy2026.evidence.json")

WHY IT IS BUILT AS THE WORK HAPPENS

A ledger written from the finished document carries the finished document's
figures forward, including the wrong ones. It can only ever say "this number is
the number", which is what the gate exists to disbelieve. Written as you go, it
says where each number was standing when you took it: which pull, at what time,
over what period, with how many rows of how many. That is a different claim and
it is checkable.

The three defects this shape was designed against, all real, all on the same
day, none visible to anyone reading the finished document:

  1. A figure that was live in every table and stale in one sentence of prose.
     `figures[]` covers prose, because the gate reads the rendered text and not
     the tables.
  2. A report that returned 500 rows of 1,684 and reported status COMPLETE.
     `completeness{expected, returned}` is what catches that; a status field
     never will.
  3. A report run over the wrong period whose date inputs silently no-opped.
     `period_requested` against `period_claimed` catches it, because the report
     says on its own face what it covered.

SCHEMA

Exactly what `client-deliverable-gate.py` reads:

    client, period, deliverable, deliverable_pdf, last_ledger_write_at,
    sources[]         id, kind, pulled_at, period_requested, period_claimed,
                      completeness{expected, returned}
    figures[]         value, label, source, derivation
    checks[]          name, lhs, rhs, difference
    reconciliations[] account, book, statement, difference, evidence
    open_items[]      summary, must_appear_in_deliverable
    allow_undeclared[]  literal figures the deliverable prints that are not
                        claims about the client (a page number, a year in a
                        money-shaped string)

`verify(path)` runs the same structural checks here, so somebody using this
engine outside the firm that wrote the gate gets the same refusals.
"""

from __future__ import annotations

import datetime as _dt
import json
import os
import re
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation

from . import median
from .util import ZERO, money, plain

__all__ = ["Evidence", "EvidenceError", "VerifyResult", "verify", "MONEY_RE"]

# The same pattern the gate uses to find money in the rendered deliverable.
MONEY_RE = re.compile(r"\$?\(?-?\d{1,3}(?:,\d{3})*\.\d{2}\)?")


class EvidenceError(ValueError):
    """The ledger was asked to record something that would not be evidence."""


def _now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _amount(value, field_name: str) -> str:
    """Money as the ledger stores it: a plain signed string, two decimals.

    Decimals and ints are formatted here; a string is kept as the caller wrote
    it (it may carry a currency symbol or parentheses, both of which the gate
    normalises) after proving it can be read as an amount at all.
    """
    if value is None:
        raise EvidenceError(f"{field_name} is required and was None")
    if isinstance(value, (Decimal, int, float)):
        return plain(money(value, field_name))
    text = str(value).strip()
    if not text:
        raise EvidenceError(f"{field_name} is required and was blank")
    try:
        money(text, field_name)
    except Exception as exc:                                   # noqa: BLE001
        raise EvidenceError(f"{field_name}: {text!r} cannot be read as an amount") from exc
    return text


def _norm(value):
    """The gate's own normalisation, so local checks agree with it exactly."""
    s = str(value).strip().replace("$", "").replace(",", "").replace("−", "-")
    negative = s.startswith("(") and s.endswith(")")
    s = s.strip("()")
    try:
        f = Decimal(s)
    except (InvalidOperation, ValueError):
        return None
    if negative:
        f = -f
    return f"{f:.2f}"


def _strip_tags(html: str) -> str:
    html = re.sub(r"<script.*?</script>", " ", html, flags=re.S | re.I)
    html = re.sub(r"<style.*?</style>", " ", html, flags=re.S | re.I)
    html = re.sub(r"<!--.*?-->", " ", html, flags=re.S)
    return re.sub(r"<[^>]+>", " ", html)


def _text_of(path) -> str:
    if not path or not os.path.exists(path):
        return ""
    with open(path, encoding="utf-8", errors="replace") as fh:
        return _strip_tags(fh.read())


# ------------------------------------------------------------------- ledger

class Evidence:
    """An evidence ledger, appended to while the deliverable is being built."""

    def __init__(self, client, period, deliverable="", deliverable_pdf="",
                 last_ledger_write_at=None):
        self.client = str(client or "").strip()
        self.period = str(period or "").strip()
        self.deliverable = str(deliverable or "")
        self.deliverable_pdf = str(deliverable_pdf or "")
        self.last_ledger_write_at = last_ledger_write_at
        self.sources = []
        self.figures = []
        self.checks = []
        self.reconciliations = []
        self.open_items = []
        self.allow_undeclared = []
        self.allow_notes = []
        if not self.client or not self.period:
            raise EvidenceError("an evidence ledger needs a client and a period")

    # ------------------------------------------------------------ metadata
    def ledger_written(self, when=None):
        """When the books were last written. Any source older than this is stale."""
        self.last_ledger_write_at = when or _now()
        return self.last_ledger_write_at

    def deliverables(self, html=None, pdf=None):
        if html is not None:
            self.deliverable = str(html)
        if pdf is not None:
            self.deliverable_pdf = str(pdf)
        return self

    def allow(self, value, why=""):
        """A money-shaped string in the document that is not a claim about the client.

        Used sparingly and always with a reason, because every use of it is a
        figure nobody has to justify.
        """
        if not str(why or "").strip():
            raise EvidenceError(
                "allow() needs a reason. Every use of it is a printed figure nobody has to "
                "justify, which is the exact hole the ledger exists to close."
            )
        self.allow_undeclared.append(_amount(value, "allow_undeclared value"))
        self.allow_notes.append({"value": self.allow_undeclared[-1], "why": str(why)})
        return self

    # -------------------------------------------------------------- append
    def source(self, id, kind="", *, pulled_at=None, period_requested="",
               period_claimed="", expected=None, returned=None, note=""):
        """One pull. Every figure must name one of these.

        `expected` is what the system said exists (the report's own row count,
        the API's total, the statement's own transaction count) and `returned`
        is what actually arrived. They are both required and they are compared,
        because a truncated pull presents itself as a successful one.

        `period_claimed` is what the report says on its face about what it
        covers, quoted, not what you asked for. Those two being different is the
        only evidence that a date filter silently failed.
        """
        sid = str(id or "").strip()
        if not sid:
            raise EvidenceError("a source needs an id; figures reference it")
        if any(s["id"] == sid for s in self.sources):
            raise EvidenceError(f"source {sid!r} is already declared")
        if expected is None or returned is None:
            raise EvidenceError(
                f"source {sid!r}: expected and returned row counts are both required. "
                f"A pull that cannot say how many rows should have come back cannot be "
                f"shown to be complete, and truncation looks exactly like success."
            )
        if not period_requested or not period_claimed:
            raise EvidenceError(
                f"source {sid!r}: period_requested and period_claimed are both required. "
                f"The second is what the report says on its own face; without it there is no "
                f"evidence the date filter applied."
            )
        self.sources.append({
            "id": sid,
            "kind": str(kind or "").strip(),
            "pulled_at": pulled_at or _now(),
            "period_requested": str(period_requested),
            "period_claimed": str(period_claimed),
            "completeness": {"expected": int(expected), "returned": int(returned)},
            "note": str(note or ""),
        })
        return sid

    def figure(self, value, label, *, source, derivation, note="", presented=""):
        """One number that will be printed, and how it was arrived at.

        Including numbers that appear only in a sentence. The gate reads the
        rendered text, so a figure in prose is exactly as much of a claim as one
        in a table, and the one that shipped stale was in prose.

        `value` is the engine's own signed figure, debit-positive, because that
        is what a machine re-reads and what every tie-out is computed from.
        `presented` is how the SAME figure appears in the document a person
        reads: on the account's own side, `3,118,447.25 Cr`, never negative. A
        reviewer holding only the signed value has to work out which side it was
        printed on before they can check anything, so the ledger records both.
        """
        sid = str(source or "").strip()
        if not sid:
            raise EvidenceError(f"figure {label!r}: no source. Which pull did this number come from?")
        if not any(s["id"] == sid for s in self.sources):
            raise EvidenceError(
                f"figure {label!r} cites source {sid!r}, which is not declared. Declare the "
                f"source when you pull it, not afterwards from memory."
            )
        if not str(derivation or "").strip():
            raise EvidenceError(
                f"figure {label!r}: no derivation. Say what was summed, filtered or subtracted to "
                f"get it. \"From the report\" is not a derivation."
            )
        self.figures.append({
            "value": _amount(value, f"figure {label!r} value"),
            "presented": str(presented or ""),
            "label": str(label),
            "source": sid,
            "derivation": str(derivation),
            "note": str(note or ""),
        })
        return self.figures[-1]

    def check(self, name, *, lhs, rhs, difference=None, note=""):
        """A tie-out that must close at exactly zero.

        `difference` is computed when it is not given, so the ledger cannot
        record a difference that disagrees with its own two sides.
        """
        left, right = _amount(lhs, f"check {name!r} lhs"), _amount(rhs, f"check {name!r} rhs")
        if difference is None:
            difference = money(money(left) - money(right))
        self.checks.append({
            "name": str(name),
            "lhs": left,
            "rhs": right,
            "difference": _amount(difference, f"check {name!r} difference"),
            "note": str(note or ""),
        })
        return self.checks[-1]

    def reconciliation(self, account, *, book, statement, difference=None, evidence="",
                       note="", presented=""):
        """One balance-carrying account against its statement. Zeros included.

        A reconciliation list holding only the accounts that did not tie is
        indistinguishable from one where nobody checked the rest.
        """
        b, s = _amount(book, f"{account} book"), _amount(statement, f"{account} statement")
        if difference is None:
            difference = money(money(b) - money(s))
        self.reconciliations.append({
            "account": str(account),
            "book": b,
            "statement": s,
            # Book and statement as the document prints them, on the account's
            # own side. The signed figures above are what the difference is
            # computed from and are unchanged.
            "presented": str(presented or ""),
            "difference": _amount(difference, f"{account} difference"),
            "evidence": str(evidence or ""),
            "note": str(note or ""),
        })
        return self.reconciliations[-1]

    def open_item(self, summary, must_appear_in_deliverable="", note=""):
        """Something still open. `must_appear_in_deliverable` is checked literally.

        It is a substring the rendered document must contain, case-insensitively.
        An open item known only to us is not disclosed to anyone.
        """
        must = str(must_appear_in_deliverable or summary).strip()
        if not must:
            raise EvidenceError("an open item needs text the deliverable must contain")
        self.open_items.append({
            "summary": str(summary),
            "must_appear_in_deliverable": must,
            "note": str(note or ""),
        })
        return self.open_items[-1]

    # ------------------------------------------------------- bulk convenience
    def add_reconciliations(self, recon) -> int:
        """Every checkable row of a `recon.ReconReport`, zeros and all.

        Unchecked rows are deliberately NOT added: a reconciliation entry with a
        made-up statement balance would assert a check that never happened. They
        belong in `open_item`, and `add_open_items_from_recon` puts them there.
        """
        n = 0
        for row in recon.evidence_rows():
            self.reconciliation(row["account"], book=row["book"], statement=row["statement"],
                                difference=row["difference"], evidence=row["evidence"],
                                presented=row.get("presented", ""))
            n += 1
        return n

    def add_open_items_from_recon(self, recon) -> int:
        n = 0
        for row in getattr(recon, "unchecked_but_expected", []) or []:
            self.open_item(
                f"{row.account} {row.month}: no statement, so the balance is unverified",
                must_appear_in_deliverable=f"{row.account}",
            )
            n += 1
        for row in getattr(recon, "unreconciled", []) or []:
            self.open_item(
                f"{row.account} {row.month}: unreconciled difference",
                must_appear_in_deliverable=plain(row.difference),
            )
            n += 1
        return n

    def add_exit_tests(self, report, *, source=None) -> int:
        """The exit tests that are genuinely two-sided, as checks.

        A failing test therefore becomes a check that does not close at zero,
        which is what should stop the deliverable.
        """
        n = 0
        t1 = report.get(1)
        if t1 is not None:
            self.check("trial balance foots",
                       lhs=t1.measured.get("debits", ZERO), rhs=t1.measured.get("credits", ZERO))
            n += 1
        for number, name in ((3, "opening balance equity is zero"), (4, "clearing accounts are zero")):
            test = report.get(number)
            if test is None:
                continue
            total = ZERO
            for key, value in test.measured.items():
                if isinstance(value, Decimal):
                    total = money(total + value)
            self.check(name, lhs=total, rhs=ZERO)
            n += 1
        return n

    # --------------------------------------------------------------- output
    def as_dict(self) -> dict:
        return {
            "client": self.client,
            "period": self.period,
            "deliverable": self.deliverable,
            "deliverable_pdf": self.deliverable_pdf,
            "last_ledger_write_at": self.last_ledger_write_at,
            "sources": self.sources,
            "figures": self.figures,
            "checks": self.checks,
            "reconciliations": self.reconciliations,
            "open_items": self.open_items,
            "allow_undeclared": self.allow_undeclared,
            "allow_undeclared_notes": self.allow_notes,
            "written_at": _now(),
            "written_by": "closethebooks.evidence",
            # Whoever re-derives these figures should be able to get the same
            # tool and read what it did. This is metadata about the writer; it
            # carries no amount and nothing in `verify` reads it as a figure.
            "tool": median.tool_record(),
        }

    def write(self, path) -> str:
        """Write the ledger. Refuses an empty one.

        A ledger with no sources proves nothing, and a ledger with no figures is
        not about a deliverable. Both are what an after-the-fact ledger looks
        like when somebody remembers it at the end.
        """
        if not self.sources:
            raise EvidenceError(
                "refusing to write an evidence ledger with no sources. Every figure rests on a "
                "pull; declare the pulls as you make them."
            )
        if not self.figures:
            raise EvidenceError(
                "refusing to write an evidence ledger with no figures. If the deliverable prints "
                "no numbers it needs no ledger; if it prints numbers, they go in here."
            )
        if self.last_ledger_write_at is None:
            self.ledger_written()
        path = str(path)
        parent = os.path.dirname(os.path.abspath(path))
        if parent:
            os.makedirs(parent, exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(self.as_dict(), fh, indent=2)
            fh.write("\n")
        return path


# ------------------------------------------------------------------- verify

@dataclass
class VerifyResult:
    path: str = ""
    passes: list = field(default_factory=list)
    failures: list = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.failures

    def __bool__(self) -> bool:
        return self.ok

    def render(self, limit=None) -> str:
        """Every check, or the first `limit` of each kind with a count of the rest.

        A terminal is not a report. On a real handoff this printed 539 lines,
        which nobody reads, so the caller passes a limit and the full text still
        goes to the file. Failures are listed before the passes are truncated,
        because a failure is the only line here anyone has to act on.
        """
        out = [f"evidence ledger: {self.path}"]

        def block(lines, prefix):
            shown = lines if limit is None else lines[:limit]
            for line in shown:
                out.append(f"  {prefix}  {line}")
            if len(lines) > len(shown):
                out.append(f"  ... {len(lines) - len(shown)} more {prefix.strip().lower()} "
                           f"check(s), all of them in the file")

        block(self.failures, "FAIL")
        block(self.passes, "pass")
        out.append("")
        out.append(
            f"RESULT: {'PASS' if self.ok else 'FAIL'} "
            f"({len(self.passes)} of {len(self.passes) + len(self.failures)} checks)"
        )
        return "\n".join(out) + "\n"


def verify(path) -> VerifyResult:
    """The gate's structural checks, run locally.

    V1 to V8 correspond one for one to the firm gate's G1 to G8, so a ledger
    that passes here passes there for the same reasons. It does not re-do the
    accounting; it checks that every asserted number has a live, complete,
    correctly scoped derivation behind it, which is a different thing and the
    one a reader of the finished document cannot check for themselves.
    """
    result = VerifyResult(path=str(path))
    with open(path, encoding="utf-8") as fh:
        led = json.load(fh)

    def bad(code, msg):
        result.failures.append(f"{code}  {msg}")

    def ok(code, msg):
        result.passes.append(f"{code}  {msg}")

    for key in ("client", "period"):
        if not str(led.get(key) or "").strip():
            bad("V0", f"{key} is not recorded")
    doc = led.get("deliverable")
    text = _text_of(doc)

    # ---- V1: every printed figure is declared
    declared = set()
    for f in led.get("figures", []):
        n = _norm(f.get("value"))
        if n is None:
            bad("V1", f"figures[] entry has an unparseable value: {f.get('value')!r}")
        else:
            declared.add(n)
        if not str(f.get("source") or "").strip():
            bad("V1", f"figure {f.get('label')!r} names no source")
        elif not any(s.get("id") == f.get("source") for s in led.get("sources", [])):
            bad("V1", f"figure {f.get('label')!r} cites undeclared source {f.get('source')!r}")
        if not str(f.get("derivation") or "").strip():
            bad("V1", f"figure {f.get('label')!r} has no derivation")
    for c in led.get("checks", []):
        for key in ("lhs", "rhs"):
            n = _norm(c.get(key))
            if n:
                declared.add(n)
    for r in led.get("reconciliations", []):
        for key in ("book", "statement", "difference"):
            n = _norm(r.get(key))
            if n:
                declared.add(n)
    for value in led.get("allow_undeclared", []):
        n = _norm(value)
        if n:
            declared.add(n)

    if not doc:
        bad("V1", "no deliverable declared, so its printed figures cannot be checked")
    elif not os.path.exists(doc):
        bad("V1", f"deliverable not found: {doc!r}")
    else:
        printed = {p for p in (_norm(m) for m in MONEY_RE.findall(text)) if p}
        magnitudes = {f"{abs(Decimal(d)):.2f}" for d in declared}
        orphans = sorted(p for p in printed
                         if p not in declared and f"{abs(Decimal(p)):.2f}" not in magnitudes)
        if orphans:
            bad("V1", "figures printed in the deliverable with NO declared derivation: "
                      + ", ".join(orphans[:20]))
        else:
            ok("V1", f"all {len(printed)} printed figures trace to a declared derivation")

    # ---- V2: sources complete
    sources = led.get("sources", [])
    if not sources:
        bad("V2", "no sources[] declared; a deliverable with no sources cannot be verified")
    for s in sources:
        sid = s.get("id", "?")
        comp = s.get("completeness") or {}
        expected, returned = comp.get("expected"), comp.get("returned")
        if expected is None or returned is None:
            bad("V2", f"source {sid!r}: completeness.expected/returned not recorded")
        elif expected != returned:
            bad("V2", f"source {sid!r}: TRUNCATED, {returned} rows returned of {expected} reported")
        else:
            ok("V2", f"source {sid!r}: complete, {returned} of {expected} rows")

    # ---- V3: the period actually applied
    for s in sources:
        sid = s.get("id", "?")
        requested, claimed = s.get("period_requested"), s.get("period_claimed")
        if not requested:
            bad("V3", f"source {sid!r}: period_requested not recorded")
        elif not claimed:
            bad("V3", f"source {sid!r}: period_claimed not recorded; cannot prove the filter applied")
        elif not (set(re.findall(r"(20\d{2})", requested)) & set(re.findall(r"(20\d{2})", claimed))):
            bad("V3", f"source {sid!r}: asked for {requested!r} but the report says it covers {claimed!r}")
        else:
            ok("V3", f"source {sid!r}: period applied ({claimed})")

    # ---- V4: checks close at zero
    checks = led.get("checks", [])
    if not checks:
        bad("V4", "no checks[] declared; at minimum a balance check is required")
    for c in checks:
        name = c.get("name", "?")
        d = _norm(c.get("difference", "0.00"))
        if d is None:
            bad("V4", f"check {name!r}: difference unparseable")
        elif Decimal(d) != ZERO:
            bad("V4", f"check {name!r}: difference is {d}, not 0.00")
        else:
            ok("V4", f"check {name!r}: 0.00")

    # ---- V5: every reconciliation states a difference, non-zero ones disclosed
    recs = led.get("reconciliations", [])
    if not recs:
        bad("V5", "no reconciliations[] declared")
    printed_text = {p for p in (_norm(m) for m in MONEY_RE.findall(text)) if p}
    for r in recs:
        account = r.get("account", "?")
        d = _norm(r.get("difference"))
        if d is None:
            bad("V5", f"{account}: no difference stated (a zero must be stated too)")
        elif Decimal(d) == ZERO:
            ok("V5", f"{account}: 0.00")
        elif d in printed_text:
            ok("V5", f"{account}: {d} UNRECONCILED and disclosed in the deliverable")
        else:
            bad("V5", f"{account}: difference of {d} is NOT disclosed anywhere in the deliverable")

    # ---- V6: no source older than the last write to the books
    last_write = led.get("last_ledger_write_at")
    if not last_write:
        bad("V6", "last_ledger_write_at not recorded; staleness cannot be ruled out")
    else:
        try:
            lw = _dt.datetime.fromisoformat(str(last_write).replace("Z", "+00:00"))
        except ValueError:
            bad("V6", f"last_ledger_write_at unparseable: {last_write!r}")
            lw = None
        for s in sources:
            sid = s.get("id", "?")
            pulled = s.get("pulled_at")
            if not pulled:
                bad("V6", f"source {sid!r}: pulled_at not recorded")
                continue
            try:
                pt = _dt.datetime.fromisoformat(str(pulled).replace("Z", "+00:00"))
            except ValueError:
                bad("V6", f"source {sid!r}: pulled_at unparseable: {pulled!r}")
                continue
            if lw is not None and pt < lw:
                bad("V6", f"source {sid!r}: pulled {pulled} but the books were written at {last_write}. STALE.")
            else:
                ok("V6", f"source {sid!r}: pulled after the last ledger write")

    # ---- V7: the PDF exists and is not older than its source
    pdf = led.get("deliverable_pdf")
    if not pdf:
        bad("V7", "deliverable_pdf not declared; a client deliverable is a PDF, not HTML or markdown")
    elif not os.path.exists(pdf):
        bad("V7", f"deliverable_pdf does not exist: {pdf}")
    elif doc and os.path.exists(doc) and os.path.getmtime(pdf) < os.path.getmtime(doc):
        bad("V7", f"{os.path.basename(pdf)} is OLDER than its source. Re-render before sending.")
    else:
        ok("V7", f"{os.path.basename(pdf)} exists and is current")

    # ---- V8: open items reach the reader
    lowered = text.lower()
    for item in led.get("open_items", []):
        key = str(item.get("must_appear_in_deliverable", "")).strip().lower()
        if not key:
            bad("V8", f"open item {item.get('summary', '?')!r} says nothing that must appear")
        elif key not in lowered:
            bad("V8", f"open item not disclosed to the reader: {item.get('summary', key)!r}")
        else:
            ok("V8", f"open item disclosed: {item.get('summary', key)!r}")

    return result
