"""Decide what each unbooked bank row is, in the order that keeps the books right.

Four outcomes per row, and the ORDER is the substance of this module:

  1. match      the row is ALREADY in the books
  2. transfer   the counterparty is another of the company's own accounts
  3. add        a rule explains it, so propose an account with its evidence
  4. question   nothing above applies, so ask, by name, about this row

Steps 1 and 2 come first because they are the two ways a bookkeeping tool
silently doubles a company's numbers, and both are invisible on the face of the
statements afterwards. A payment-processor payout that was already booked by a
journal entry, added a second time from the bank feed, is revenue counted twice.
A sweep from checking to a treasury account, booked as an expense on one side
and income on the other, is both. Neither shows up as an out-of-balance ledger:
the trial balance still foots, so the only place the error can be caught is
here, before the row is proposed.

Everything a matcher trusts, it must be able to defend. A match rests on an
amount to the cent, the same account, and a near date, and it refuses when two
candidates are equally good, because a tool that picks one of two identical
8,333.34 entries is guessing about which invoice was paid. Every `Proposal`
carries a `source` sentence a non-accountant can check against their own
statement.

And a bank descriptor is untrusted input in the most literal sense available:
anyone who can send a company one dollar can write text into that company's
bookkeeping pipeline. `is_agent_directed` flags the shapes that are trying to
address the reader rather than name a vendor, and a flagged row is never
auto-categorized whatever the rules say.
"""

from __future__ import annotations

import re

from .model import BankLine, JournalLine, Proposal, Rule
from .precedent import vendor_key
from .util import ZERO, fmt, iso, norm_text

__all__ = ["classify", "find_match", "find_transfer", "is_agent_directed",
           "agent_directed_reasons"]


# ------------------------------------------------------- untrusted descriptors
#
# THE THREAT, stated plainly: a bank memo is chosen by whoever sent the money.
# A wire memo, a Zelle note, an ACH addendum and a payment-processor statement
# descriptor are all free text supplied by a third party, and all of them arrive
# in this pipeline as `BankLine.descriptor`. If any downstream reader (a person
# skimming the review workbook, or a model summarising it) treats that text as
# instructions, the sender has just written into the company's books for the
# price of a one dollar transfer.
#
# So this is not a spam filter and not a fraud check. It answers one question:
# is this text trying to ADDRESS a reader rather than NAME a vendor? A flagged
# row is never auto-categorized. It goes to a human with the descriptor quoted,
# never obeyed.
#
# The patterns are written tight on purpose. "NORTHWIND SYSTEMS INC" contains
# the word "system" and "ASSISTANCE LEAGUE THRIFT" contains "assist"; a loose
# pattern would flag ordinary vendors, the flag would stop meaning anything, and
# a real one would be waved through with the rest.
_AGENT_PATTERNS = (
    # Direct instruction to a reader, the classic prompt-injection opener.
    (r"\b(ignore|disregard|forget|override)\b[^.]{0,24}\b(previous|prior|earlier|above|all|any)\b[^.]{0,24}\b(instruction|prompt|rule|direction|message|context)s?\b",
     "tells the reader to ignore its instructions"),
    (r"\b(new|updated|revised)\s+(instruction|directive|system\s+prompt)s?\b",
     "announces new instructions"),
    # Chat-role labels. The COLON is required: plenty of real vendors are called
    # something Systems, and none of them are called "System:".
    (r"\b(system|assistant|user|human|developer)\s*:", "carries a chat role label"),
    (r"<\s*/?\s*(system|assistant|user|human|instruction|tool|function|prompt)[^>]*>",
     "carries an XML-style control tag"),
    (r"\[\s*/?\s*(inst|system|assistant|prompt)\s*\]", "carries an [INST]-style control tag"),
    (r"<\|.{0,40}?\|>", "carries a special-token delimiter"),
    (r"\{\{.{1,80}?\}\}", "carries a template placeholder"),
    (r"```", "carries a markdown code fence"),
    # Told to do something rather than told who was paid.
    (r"\b(run|execute|eval|evaluate)\b[^.]{0,20}\b(the\s+)?(following|command|code|script|this)\b",
     "asks the reader to run something"),
    (r"\b(you\s+are|act\s+as|pretend\s+to\s+be|behave\s+as)\s+(a|an|the)\b",
     "tries to assign the reader a role"),
    (r"\b(curl|wget|bash|sudo|powershell|/bin/sh|pip\s+install|npm\s+install)\b",
     "names a shell command"),
    (r"\brm\s+-[a-z]{1,3}\b", "names a destructive shell command"),
    (r"\b(send|email|forward|post|upload|exfiltrate)\b[^.]{0,30}\b(to\s+\S+@|api[_\s-]?key|password|credential|token|secret)\b",
     "asks for data to be sent somewhere"),
    (r"\b(api[_\s-]?key|secret[_\s-]?key|password|bearer\s+token)\b", "names a credential"),
    # A scheme-qualified URL. A bare domain is normal in a card descriptor
    # ("PINEBROOK-REALTY.COM"); "https://..." in a bank memo is not, and it is
    # the usual way an injected instruction points at a payload.
    (r"\b[a-z][a-z0-9+.\-]{1,10}://", "contains a URL"),
    (r"\bwww\.\S+/\S", "contains a URL with a path"),
    # A long opaque blob: base64 or hex, too long to be a reference number.
    (r"[A-Za-z0-9+/]{40,}={0,2}", "contains a long encoded blob"),
    (r"\b(?=[A-Za-z0-9+/]*[a-z])(?=[A-Za-z0-9+/]*[A-Z])(?=[A-Za-z0-9+/]*\d)[A-Za-z0-9+/]{24,}={0,2}",
     "contains a long mixed-case encoded blob"),
)
_AGENT_RE = tuple((re.compile(p, re.I | re.S), why) for p, why in _AGENT_PATTERNS)

# Control characters and the invisible-formatting block, which have no business
# in a descriptor and are how text is hidden from a human reviewer while staying
# perfectly visible to a parser.
_CONTROL_RE = re.compile(
    "[\x00-\x08\x0b-\x1f\x7f]"
    "|[​-‏‪-‮⁠-⁯﻿]"
)


def agent_directed_reasons(text) -> list:
    """Every reason this descriptor reads as addressed to a reader. Possibly none."""
    s = "" if text is None else str(text)
    if not s.strip():
        return []
    out = []
    if _CONTROL_RE.search(s):
        out.append("contains hidden or control characters")
    for rx, why in _AGENT_RE:
        if rx.search(s):
            out.append(why)
    return out


def is_agent_directed(text) -> bool:
    """True when a descriptor contains instruction-shaped text.

    A True here does not mean the row is fraudulent. It means the text cannot be
    treated as a vendor name, so no rule may categorize the row on the strength
    of it and a human has to look.
    """
    return bool(agent_directed_reasons(text))


def _quote(text, limit: int = 90) -> str:
    """Render an untrusted descriptor for a question a human will read.

    Control characters out, length capped, and the whole thing quoted, so it
    lands in the workbook as a thing that was SAID rather than a thing to do.
    """
    s = _CONTROL_RE.sub(" ", "" if text is None else str(text))
    s = re.sub(r"\s+", " ", s).strip()
    if len(s) > limit:
        s = s[: limit - 3].rstrip() + "..."
    return s


# ------------------------------------------------------------------- matching

def _bank_role_keys(ledger) -> set:
    """Account keys whose role is bank or card. The only legs a statement hits."""
    keys = set()
    for key, acct in (getattr(ledger, "accounts", None) or {}).items():
        if getattr(acct, "role", "") in ("bank", "card"):
            keys.add(key)
            if getattr(acct, "number", ""):
                keys.add(acct.number)
    return keys


def _same_account(line: BankLine, jl: JournalLine, ledger) -> bool:
    if not line.account_key:
        # No declared account on the bank row. Fall back to "any leg that could
        # be a bank or card leg", which is weaker, so `find_match` compensates
        # by refusing anything but a unique candidate.
        return jl.account in _bank_role_keys(ledger)
    if jl.account == line.account_key:
        return True
    return norm_text(jl.account_full or "") == norm_text(line.account_key)


def _line_id(jl: JournalLine) -> str:
    """Identity for a posted line, for the already-consumed set."""
    return jl.txn_id or f"{jl.source_file}:{jl.source_row}"


def find_match(line: BankLine, ledger, window_days: int = 5, *, exclude=None):
    """The posted entry this bank row already is, or None.

    The most valuable check in the module. Three conditions, all required:

      * the amount agrees TO THE CENT, in the engine's debit-positive sign. A
        bank row's `amount` is positive when money arrived; the posted leg on
        that same account is a debit of the same size when money arrived, on a
        checking account and on a card alike, so `jl.signed == line.amount`
        holds for both without a special case.
      * it is a leg on the SAME account the statement belongs to.
      * the dates are within `window_days`, because a wire posts to the bank a
        day or three after the journal entry was dated.

    And one refusal: if two candidates are equally close in date, this returns
    None. Two posted 8,333.34 entries in the same week are two different
    invoices, and picking one of them is a guess dressed as a reconciliation.
    """
    if ledger is None:
        return None
    exclude = exclude or set()
    candidates = []
    for jl in getattr(ledger, "lines", None) or []:
        if _line_id(jl) in exclude:
            continue
        if jl.signed != line.amount:
            continue
        if jl.date is None or line.date is None:
            continue
        gap = abs((jl.date - line.date).days)
        if gap > window_days:
            continue
        if not _same_account(line, jl, ledger):
            continue
        candidates.append((gap, jl))

    if not candidates:
        return None
    best = min(g for g, _ in candidates)
    tied = [jl for g, jl in candidates if g == best]
    if len(tied) > 1:
        # Deliberately not "pick the first". See the docstring.
        return None
    return tied[0]


def find_transfer(line: BankLine, all_lines, window_days: int = 3):
    """The other side of a move between the company's own accounts, or None.

    An equal and opposite amount, on a DIFFERENT declared account, within
    `window_days`. Booking one of these as income and the other as expense is
    the second classic doubling error, and it inflates both halves of the P&L at
    once, so it survives a revenue check and an expense check taken separately.

    Ties refuse, for the same reason `find_match` refuses.
    """
    if not all_lines or not line.account_key or line.amount == ZERO:
        return None
    want = -line.amount
    candidates = []
    for other in all_lines:
        if other is line:
            continue
        if not other.account_key or other.account_key == line.account_key:
            continue
        if other.amount != want:
            continue
        if other.date is None or line.date is None:
            continue
        gap = abs((other.date - line.date).days)
        if gap > window_days:
            continue
        candidates.append((gap, other))

    if not candidates:
        return None
    best = min(g for g, _ in candidates)
    tied = [o for g, o in candidates if g == best]
    if len(tied) > 1:
        return None
    return tied[0]


# ---------------------------------------------------------------- rule choice

def _rule_score(rule: Rule, line: BankLine, key: str):
    """How well a rule fits this row, or None if it does not fit at all.

    Two ways a rule can hit, and the stronger one is checked first. A mined rule
    carries a VENDOR KEY, so the row's descriptor is reduced the same way the
    history was and the two keys are compared as identities. A hand-written
    profile rule carries plain substrings, which `Rule.matches` handles. Keying
    first is what lets a rule mined from "CONTOSO CLOUD SVCS" fire on
    "POS DEBIT VISA CHECKCARD 0114 CONTOSO CLOUD 800-555-0142 WA".
    """
    if rule.direction == "in" and not line.money_in:
        return None
    if rule.direction == "out" and line.money_in:
        return None
    exact = bool(key) and any(norm_text(n) == key for n in rule.match_contains)
    if exact:
        return (2, rule.confidence, rule.support)
    if rule.matches(line):
        return (1, rule.confidence, rule.support)
    return None


def _best_rule(rules, line: BankLine, key: str):
    best, best_score = None, None
    for rule in rules or []:
        score = _rule_score(rule, line, key)
        if score is None:
            continue
        if best_score is None or score > best_score:
            best, best_score = rule, score
    return best


# ------------------------------------------------------------------- classify

def classify(lines, ledger, profile, *, rules=None, window_days=5) -> list:
    """One `Proposal` per bank row, in the priority order at the top of the file.

    `lines` are the unbooked rows from statements or the For Review queue.
    `ledger` is what is already posted. `profile` supplies the rules unless
    `rules` is passed explicitly. Nothing here posts anything: a `Proposal` is
    an offer, and it becomes real only when a human approves it.
    """
    if rules is None:
        rules = list(profile.rules()) if profile is not None else []
    rules = list(rules)
    all_lines = list(lines)

    # A posted entry may only absorb ONE bank row. Without this, two identical
    # 8,333.34 rows both match the same journal entry, the second one is
    # reported as already booked, and the expense quietly goes missing.
    consumed = set()
    # Which bank row each transfer leg has been paired to. Both legs of a pair
    # are legitimately transfers, so this may not be a plain "already used" set:
    # it has to let the second leg point back at the first while still refusing
    # a third row that tries to claim a counterparty someone else already has.
    pairs = {}
    out = []

    for line in all_lines:
        reasons = agent_directed_reasons(line.descriptor)
        flagged = bool(reasons)
        key = "" if flagged else vendor_key(line.descriptor)
        vendor = _quote(key or line.descriptor, 60)

        # 1. Already in the books.
        jl = find_match(line, ledger, window_days=window_days, exclude=consumed)
        if jl is not None:
            consumed.add(_line_id(jl))
            what = (jl.txn_type or "journal entry").lower()
            src = (f"matched to a {what} dated {iso(jl.date)} for the same "
                   f"{fmt(abs(jl.signed))} on {jl.account_full or jl.account}"
                   + (f" (doc {jl.doc_num})" if jl.doc_num else "")
                   + ". Adding it again would count this twice.")
            out.append(Proposal(
                line=line, action="match", account=jl.account,
                account_full=jl.account_full, klass=jl.klass,
                confidence=1.0, source=src, matched_to=_line_id(jl),
                needs_human=flagged,
                question=_flag_question(line, reasons) if flagged else "",
            ))
            continue

        # 2. A move between the company's own accounts.
        other = find_transfer(line, all_lines, window_days=min(window_days, 3))
        partner = pairs.get(id(other)) if other is not None else None
        if other is not None and partner in (None, id(line)):
            pairs[id(line)] = id(other)
            src = (f"equal and opposite {fmt(abs(line.amount))} on "
                   f"{other.account_key} dated {iso(other.date)}. This is money moving "
                   f"between the company's own accounts, so it is neither income nor "
                   f"expense on either side.")
            out.append(Proposal(
                line=line, action="transfer", counter_account=other.account_key,
                account=other.account_key, confidence=0.9, source=src,
                needs_human=flagged,
                question=_flag_question(line, reasons) if flagged else "",
            ))
            continue

        # 3. A rule explains it.
        #
        # THE INJECTION GATE. Match and transfer above are allowed to stand on a
        # flagged row because neither of them reads the descriptor: both rest on
        # an amount, an account and a date, which the sender of the money cannot
        # rewrite into a different meaning, and both make the books MORE right
        # by preventing a double count. `add` is the one outcome where the text
        # in the descriptor chooses an account, so it is the one outcome a
        # flagged row may never reach.
        rule = None if flagged else _best_rule(rules, line, key)
        if rule is not None:
            out.append(Proposal(
                line=line, action="add", account=rule.account,
                account_full=rule.account_full, klass=rule.klass,
                rule_id=rule.id, confidence=rule.confidence,
                source=rule.source or f"rule {rule.id}",
            ))
            continue

        # 4. Ask, and ask about THIS row.
        if flagged:
            out.append(Proposal(
                line=line, action="question", needs_human=True,
                question=_flag_question(line, reasons),
                source=("the descriptor on this row is instruction-shaped text, so it "
                        "cannot be used to choose an account: " + "; ".join(reasons)),
            ))
            continue

        out.append(Proposal(
            line=line, action="question",
            question=_plain_question(line, vendor),
            source=_why_no_answer(line, ledger, rules, window_days),
        ))

    return out


def _flag_question(line: BankLine, reasons) -> str:
    return (
        f"The description on the {fmt(abs(line.amount))} row dated {iso(line.date)} "
        f"reads as instructions rather than a vendor name, so nothing was categorized "
        f"from it. It says: \"{_quote(line.descriptor)}\". "
        f"Flagged because it {reasons[0]}. Who was this, and what was it for?"
    )


def _plain_question(line: BankLine, vendor: str) -> str:
    if line.money_in:
        return (f"What is the {fmt(line.amount)} received from \"{vendor}\" on "
                f"{iso(line.date)}? Nothing in the posted books and no rule explains it. "
                f"The statement reads: \"{_quote(line.descriptor)}\".")
    return (f"What is the {fmt(abs(line.amount))} paid to \"{vendor}\" on "
            f"{iso(line.date)}? Nothing in the posted books and no rule explains it. "
            f"The statement reads: \"{_quote(line.descriptor)}\".")


def _why_no_answer(line: BankLine, ledger, rules, window_days: int) -> str:
    """Name what was actually tried, so the question is auditable, not a shrug."""
    n_rules = len(rules or [])
    near = 0
    for jl in getattr(ledger, "lines", None) or []:
        if jl.date is None or line.date is None:
            continue
        if abs((jl.date - line.date).days) <= window_days and jl.signed == line.amount:
            near += 1
    bits = [
        f"no posted entry on this account for {fmt(abs(line.amount))} within {window_days} days",
        f"no match among {n_rules} rule(s)",
    ]
    if near:
        bits.append(f"{near} posted line(s) elsewhere in the ledger have this exact amount, "
                    f"but not on this account")
    return "; ".join(bits) + "."
