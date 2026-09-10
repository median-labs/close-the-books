"""Mine what-goes-where rules out of a company's own posted history.

The founder already answered "what goes where" for most of their spending. If
Contoso Cloud was coded to 6100 Software forty times last year, asking where
Contoso Cloud goes spends the one resource a founder has least of. So this
module reads the general ledger the company already has and turns the settled
part of it into rules, leaving only the genuinely unsettled part as questions.

That is also the difference between a tool that needs an accounting firm to
hand-write a profile and one a founder can run on their own exports.

The method, in four steps:

  1. Reduce each posted line's payee/memo to a **vendor key**: the descriptor
     with the bank's noise stripped off, so the same vendor reads the same way
     whether the money left by card, by ACH or by wire.
  2. Count, per vendor key and per direction, which account the **category leg**
     went to. The bank or card leg is never the answer: a payment to a software
     vendor credits checking and debits software, and the rule is about the
     debit.
  3. Emit a rule only where that vendor's history actually agrees with itself.
     Support is the row count, confidence is the winning account's share. A
     vendor that genuinely splits (an online marketplace used for software, for
     office supplies and once for a personal order) must become a QUESTION.
     A wrong rule is worse than no rule, because it is applied silently.
  4. Replay the emitted rules against the history they came from and report how
     much of it they would have got right. That number is the honest measure of
     whether the mining worked, and it is the first thing `describe()` prints.

Nothing here reaches the network and nothing here posts anything. It reads
`JournalLine` records and returns `Rule` records for a profile a human reviews.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .model import Account, Rule
from .util import norm_text, slug

__all__ = ["mine_rules", "vendor_key", "explain", "MiningResult",
           "NON_CATEGORY_ROLES"]


# ---------------------------------------------------------------- vendor key
#
# A bank descriptor is a vendor name wrapped in operational noise, and every
# institution wraps it differently. The SAME vendor arrives as
#
#     POS DEBIT VISA CHECKCARD 0114 CONTOSO CLOUD 800-555-0142 WA
#     ACH DEBIT CONTOSO CLOUD SVCS PAYMENT 4471820993
#     RECURRING CONTOSO CLOUD 03/14 REF#88213
#
# Group those three as written and you get three vendors with one row each,
# nothing clears the support threshold, and the founder is asked three times
# about a vendor their own books already answered.
#
# So the noise is stripped in three passes: things that only ever appear at the
# FRONT (the rail, the card network, the channel), things that can appear
# ANYWHERE (masks, reference numbers, dates, phone numbers, store numbers), and
# things that only ever appear at the END (a US state code, a corporate suffix).
# Front and back run in a loop, because banks stack them: "ACH DEBIT RECURRING".
#
# Every pattern below carries a comment naming the real-world noise it removes.
# Over-stripping and under-stripping fail in different directions, and only one
# of them is safe: under-stripping splits one vendor into two keys, which loses
# coverage and asks an extra question. Over-stripping merges two vendors into
# one key, which can emit a CONFIDENT rule for the wrong account. Where a
# pattern is a judgement call it is written to under-strip.

_LEADING = (
    # The card rail, stamped on the front by nearly every US bank.
    (r"(pos|pin|sig)?\s*(debit|credit)\s*card(\s+(purchase|payment|pmt|withdrawal))?",
     "'DEBIT CARD PURCHASE', 'POS CREDIT CARD PMT'"),
    (r"check\s*card", "'CHECKCARD' / 'CHECK CARD'"),
    (r"pos\s+(debit|credit|purchase|withdrawal|adj|adjustment)", "'POS DEBIT'"),
    (r"(visa|mastercard|master card|maestro|amex|american express|discover|interac)",
     "the card network name, which says nothing about the vendor"),
    (r"purchase\s+authorized\s+on", "Wells-style 'PURCHASE AUTHORIZED ON 03/14'"),
    # The clearing rail. Same vendor, different pipe.
    (r"ach(\s+(debit|credit|pmt|payment|transaction|trans|withdrawal|deposit|settlement|hold))?",
     "'ACH DEBIT', 'ACH SETTLEMENT'"),
    (r"(eft|eftpos|dda|ext)(\s+(debit|credit|withdrawal|deposit|transfer|xfer))?",
     "'EFT WITHDRAWAL', 'DDA TO'"),
    (r"(intl|international|domestic|incoming|outgoing|book)?\s*wire(\s+(transfer|xfer|in|out|fee))?",
     "'INTL WIRE TRANSFER'"),
    (r"(direct\s+dep(osit)?|dir\s+dep|direct\s+debit)", "'DIRECT DEP' on a payroll or a payout"),
    (r"(pre-?auth(orized)?|preauthorized|recurring|recur|autopay|auto\s*pay|automatic\s+(payment|pmt))",
     "'RECURRING' on a subscription, which is a schedule and not a vendor"),
    (r"(online|internet|mobile|electronic|external|scheduled)(\s+(payment|pmt|transfer|xfer|withdrawal|deposit|banking|bkg))?",
     "'ONLINE PAYMENT', 'ELECTRONIC WITHDRAWAL'"),
    (r"bill\s*pay(ment)?", "'BILLPAY' from an online bill-pay service"),
    (r"(payment|pmt|purchase|withdrawal|deposit|debit|credit|charge|sale|xfer|transfer|from|to|paid|misc)",
     "a bare channel word left at the front once the rail above is gone"),
    # Payment-aggregator prefixes: a short token then a star, e.g. "SQ *",
    # "TST*", "PP*". Capped at 4 characters BECAUSE a longer token before a star
    # is usually the vendor itself, and merging real vendors is the unsafe
    # direction.
    (r"[a-z0-9]{2,4}\s*\*+\s*", "a payment-aggregator prefix such as 'SQ *'"),
    # A bare number left at the front once the rail is gone: the four digits a
    # bank prints after CHECKCARD are the card, not the vendor.
    (r"\d{1,6}", "a leading card fragment or sequence number"),
)

_ANYWHERE = (
    # A scheme-qualified URL. Nothing legitimate puts one in a descriptor, and
    # it is also the shape an injected instruction arrives in.
    (r"\b[a-z][a-z0-9+.\-]*://\S+", "a URL pasted into a wire memo"),
    (r"\bwww\.", "the 'WWW.' on a web-billed charge"),
    (r"\.(com|net|org|io|co|ai|app|shop|store|us)\b", "a domain tail on a web-billed charge"),
    # Card masks. The last four digits of the CARD, not of the vendor, so two
    # cards at the same vendor would otherwise be two vendors.
    (r"\bx{2,}\s*\d{0,6}\b", "an 'XXXX1234' card mask"),
    (r"\*{2,}\s*\d{2,6}\b", "a '****1234' card mask"),
    (r"\bending\s+in\s+\d{2,6}\b", "'ENDING IN 1234'"),
    (r"\bcard\s*#?\s*\d{4}\b", "'CARD 1234'"),
    # Reference, trace, authorisation and confirmation numbers: unique per
    # transaction, so each one is its own vendor if left in.
    # The keyword needs its own word boundary and the token after it has to
    # contain a digit. Without both, "auth" ate the front of "AUTHORIZED" and
    # "no" ate the front of "NORTHWIND SUPPLY", which is exactly the
    # over-stripping that merges two real vendors into one key.
    (r"\b(ref|reference|conf|confirmation|trace|txn|tran|transaction|auth|arn|seq|batch|order|ord|inv|invoice|id|no|num)\b\s*[#:.\-]?\s*(?=[a-z0-9\-]*\d)[a-z0-9\-]{3,}\b",
     "'REF#88213', 'AUTH 0042Z', 'TRACE 998812'"),
    # NACHA standard entry class codes, printed by most banks on ACH rows.
    (r"\b(ppd|ccd|ctx|web|tel|arc|rck|cie)\b", "an ACH entry-class code such as 'PPD'"),
    (r"#\s*\d{2,8}\b", "a bare '#1234', usually a store or an order number"),
    (r"\b(store|str|shop|unit|location|loc|term|terminal|lane|reg)\s*#?\s*\d{1,6}\b",
     "'STORE 0421', the branch and not the vendor"),
    # Dates embedded in the descriptor. A date makes every row unique.
    (r"\b\d{4}-\d{1,2}-\d{1,2}\b", "an ISO date inside the text"),
    (r"\b\d{1,2}[/\-]\d{1,2}([/\-]\d{2,4})?\b", "'03/14' or '03/14/26' inside the text"),
    (r"\b(0?[1-9]|1[0-2])[/\-](19|20)\d{2}\b", "'03/2026', the billed month on a rent or a subscription"),
    (r"\b(jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\.?\s+\d{1,2}(,?\s*\d{2,4})?\b",
     "'MAR 14, 2026' inside the text"),
    (r"\b\d{1,2}:\d{2}(:\d{2})?\s*(am|pm)?\b", "a timestamp"),
    # A customer service phone number, which many card descriptors carry.
    (r"\b(\+?1[\s.\-]?)?\d{3}[\s.\-]\d{3}[\s.\-]\d{4}\b", "a support phone number"),
    (r"\b\d{3}\-?[a-z]{3,7}\b(?=\s|$)", "a vanity support number such as '800-FLOWERS'"),
    # A dollar amount written into the memo.
    (r"[$]\s*\d[\d,]*(\.\d{2})?", "an amount repeated inside the descriptor"),
    # Long digit runs and long mixed alphanumeric blobs: account numbers,
    # invoice ids, opaque processor tokens.
    (r"\b\d{5,}\b", "a long digit run, normally an account or invoice number"),
    (r"\b(?=[a-z0-9]*\d)(?=[a-z0-9]*[a-z])[a-z0-9]{12,}\b",
     "a 12-character-plus alphanumeric blob, normally a processor token"),
)

_TRAILING = (
    (r"(usa?|us\s*dollars?|usd|can|cad|gbr|gbp|eur)",
     "a trailing country or currency tag"),
    (r"(inc|llc|l\.?l\.?c|ltd|limited|corp|corporation|co|company|lp|llp|plc|gmbh|nv|bv|pty|pte|sa|ag)",
     "a corporate suffix, so 'Northwind Supply' and 'Northwind Supply Inc' are one vendor"),
    (r"(svc|svcs|serv|service|services)",
     "a generic 'SVCS' tail, so 'Contoso Cloud' and 'Contoso Cloud Svcs' are one vendor"),
    (r"(payment|pmt|purchase|withdrawal|deposit|debit|credit|charge|xfer|transfer|paid|sale)",
     "a channel word the bank appends after the vendor rather than before it"),
)

_LEADING_RE = tuple(re.compile(r"^\s*(?:" + p + r")\b\s*") for p, _ in _LEADING)
_ANYWHERE_RE = tuple(re.compile(p) for p, _ in _ANYWHERE)
# A trailing suffix may carry a full stop ("Northwind Supply Inc."), so the
# anchor allows trailing punctuation rather than requiring end-of-string.
_TRAILING_RE = tuple(re.compile(r"\s*\b(?:" + p + r")[.\s]*$") for p, _ in _TRAILING)

# The location tail a card network appends: "GRAYLINE COFFEE SEATTLE WA".
# The state code alone is easy; the city is the hard half, because there is no
# gazetteer in the standard library. The rule used here is positional: a
# two-letter state at the very end is preceded by the city, so drop both, but
# ONLY when at least two tokens survive. That keeps "NORTHWIND SUPPLY CO" at
# "northwind supply" (dropping "supply" would leave a single token) while
# collapsing the same coffee shop billed from two states into one vendor.
# Multi-word cities leave a fragment ("... NEW YORK NY" leaves "... new"),
# which is stable across rows and therefore still groups correctly.
_STATE_TAIL = re.compile(
    r"\s+(a[klrz]|c[aot]|d[ce]|fl|ga|hi|i[adln]|k[sy]|la|m[adeinost]|"
    r"n[cdehjmvy]|o[hkr]|pa|ri|s[cd]|tn|tx|ut|v[at]|w[aivy])[.\s]*$"
)


def _strip_to_fixpoint(s: str, patterns) -> str:
    """Apply each pattern once per pass until a pass changes nothing."""
    for _ in range(6):
        before = s
        for rx in patterns:
            s = rx.sub(" ", s, count=1).strip()
        s = re.sub(r"\s+", " ", s).strip()
        if s == before:
            break
    return s


def _strip_location_tail(s: str) -> str:
    if not _STATE_TAIL.search(s):
        return s
    tokens = _STATE_TAIL.sub("", s).split()
    if len(tokens) >= 3:
        tokens = tokens[:-1]          # the city, immediately before the state
    return " ".join(tokens)

# A key shorter than this is not an identity. "SQ" or "CO" left on its own after
# stripping would group every unrelated row that happened to reduce to it.
MIN_KEY_LEN = 3
MAX_KEY_LEN = 48


def vendor_key(descriptor_or_name) -> str:
    """Reduce a payee or a bank descriptor to a stable vendor identity.

    Returns "" when nothing identifying survives, which is a real answer: a row
    reading "ACH DEBIT 4471820993" names no vendor and must not be grouped with
    any other row that also names none.
    """
    s = norm_text(descriptor_or_name)
    if not s:
        return ""

    for rx in _ANYWHERE_RE:
        s = rx.sub(" ", s)
    s = re.sub(r"\s+", " ", s).strip()

    # Front first, to a fixpoint, THEN back. Banks stack prefixes ("POS DEBIT
    # VISA CHECKCARD 0114"), so one pass is never enough. The order matters:
    # the location-tail rule counts the tokens that are left, and counting them
    # while the rail is still attached made it drop a real word off the vendor.
    s = _strip_to_fixpoint(s, _LEADING_RE)
    for _ in range(6):
        before = s
        s = _strip_location_tail(s)
        for rx in _TRAILING_RE:
            s = rx.sub(" ", s, count=1).strip()
        s = re.sub(r"\s+", " ", s).strip()
        if s == before:
            break
    s = _strip_to_fixpoint(s, _LEADING_RE)

    # Whatever is left: punctuation to spaces, runs collapsed.
    s = re.sub(r"[^a-z0-9]+", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    if len(s) < MIN_KEY_LEN:
        return ""
    return s[:MAX_KEY_LEN].strip()


# ------------------------------------------------------- what counts as a leg
#
# The rule is about the CATEGORY leg, never the funding leg. These roles are
# excluded from ever becoming a rule's account:
#
#   bank, card      the funding leg. A payment to a software vendor credits
#                   checking and debits software; "checking" is not where AWS
#                   goes, it is where the money came from.
#   clearing, obe   holding pens. Undeposited Funds and Opening Balance Equity
#                   are places money waits, not answers to "what was this for".
#   ap, ar          the accrual leg of the SAME vendor's transaction. On an
#                   accrual book a bill posts Dr expense / Cr AP and the payment
#                   posts Dr AP / Cr bank. Counting the AP leg would put every
#                   accrual vendor at roughly 50% confidence between its real
#                   expense account and Accounts Payable, and no vendor would
#                   ever clear the threshold.
NON_CATEGORY_ROLES = frozenset({"bank", "card", "clearing", "obe", "ap", "ar"})

# Transaction types that are a movement of the company's own money, never a
# categorisation. A transfer has no category leg at all.
_TRANSFER_TXN_TYPES = frozenset({"transfer", "credit card payment", "cc payment",
                                 "bank transfer", "funds transfer"})


def _resolve(accounts, key):
    """Find the Account for a journal line's account key.

    Journal lines carry the account NUMBER where the chart is numbered and the
    full label where it is not, so a plain dict lookup misses roughly half the
    time on a numbered chart loaded from a differently-shaped report.
    """
    if not accounts:
        return None
    if key in accounts:
        return accounts[key]
    k = norm_text(key)
    if not k:
        return None
    for acct in accounts.values():
        if not isinstance(acct, Account):
            continue
        if norm_text(acct.full_name) == k or norm_text(acct.name) == k or acct.number == key:
            return acct
    return None


def _line_key(line) -> str:
    """The vendor key for one posted line.

    Payee first, memo second. QuickBooks puts the tidy vendor name in Name when
    the transaction was matched to a vendor record and leaves the raw bank
    descriptor in Memo when it was not, so preferring Name gives a cleaner
    grouping without losing the unmatched rows.
    """
    k = vendor_key(getattr(line, "name", ""))
    if k:
        return k
    return vendor_key(getattr(line, "memo", ""))


def _direction_of(line) -> str:
    """Which way the money went, read off the CATEGORY leg.

    A debit to the category side means money left the company (an expense, an
    asset bought). A credit means money arrived (revenue, a refund). Keeping
    these apart is the whole reason a payment processor's deposit and its
    monthly fee do not collapse into one wrong rule.
    """
    return "out" if line.signed > 0 else "in"


# ----------------------------------------------------------------- the result

@dataclass
class MiningResult:
    rules: list = field(default_factory=list)      # Rule
    skipped: list = field(default_factory=list)    # dicts, each with a reason
    stats: dict = field(default_factory=dict)

    @property
    def accuracy(self) -> float:
        """Share of the rows a rule fired on that it got right, IN SAMPLE.

        Measured over the same rows the rules were mined from. That makes it a
        floor and not a forecast: it catches a rule set that cannot reproduce
        its own evidence, which is a real and otherwise silent failure, and it
        says nothing about rows the miner has not seen. Anything reporting this
        number to a person has to say so; presenting it as "how well the rules
        work" overstates it, and by an unknown amount.
        """
        return float(self.stats.get("accuracy", 0.0))

    def questions(self) -> list:
        """The skipped vendors, as open questions for a profile.

        A split vendor is the point of this module, not its failure. These are
        exactly the rows worth a founder's attention.
        """
        out = []
        for s in self.skipped:
            if s["reason"] != "split":
                continue
            names = ", ".join(f"{a} ({n})" for a, n in s["accounts"][:4])
            out.append({
                "id": f"vendor-{slug(s['vendor'])}-{s['direction']}",
                "question": (
                    f"{s['vendor']!r} money {s['direction']} has gone to more than one "
                    f"account in your own books: {names}. Which one should new rows go to, "
                    f"or does it depend on something in the description?"
                ),
                "answer": "",
                "evidence": s["source_rows"][:8],
            })
        return out

    def describe(self) -> str:
        s = self.stats
        pct = lambda x: f"{100.0 * float(x):.1f}%"          # noqa: E731
        lines = [
            f"REPLAY ACCURACY {pct(s.get('accuracy', 0))} IN SAMPLE "
            f"({s.get('replay_correct', 0)} of {s.get('replay_rows', 0)} rows the mined "
            f"rules would have coded, coded the way the books actually did; measured on "
            f"the same rows the rules came from, so it is a floor and not a forecast)",
            f"  history covered   {pct(s.get('coverage', 0))} "
            f"({s.get('rows_covered', 0)} of {s.get('rows_categorizable', 0)} categorisable rows)",
            f"  rows read         {s.get('rows_in', 0)}"
            + (f", {s.get('rows_out_of_window', 0)} outside the date window" if s.get("rows_out_of_window") else ""),
            f"  vendors found     {s.get('vendors', 0)}",
            f"  rules emitted     {s.get('rules', 0)}",
            f"  questions raised  {s.get('skipped_split', 0)} split, {s.get('skipped_thin', 0)} too few rows",
        ]
        if s.get("rows_unkeyed"):
            lines.append(f"  no vendor named   {s['rows_unkeyed']} rows (nothing to group on)")
        if s.get("unknown_accounts"):
            u = s["unknown_accounts"]
            lines.append(f"  accounts not in the chart: {', '.join(u[:5])}"
                         + (" ..." if len(u) > 5 else ""))
        return "\n".join(lines)


# ------------------------------------------------------------------- mining

def mine_rules(lines, accounts, *, min_support=2, min_confidence=0.75, since=None) -> MiningResult:
    """Turn posted history into what-goes-where rules.

    `lines` are `JournalLine` records already in the general ledger. `accounts`
    is a dict of account key to `Account` (a `Ledger.accounts`). `since` is an
    optional `datetime.date`; rows before it are ignored, which is how a chart
    change or a prior bookkeeper's era gets excluded from the evidence.

    Returns a `MiningResult`. Nothing is written and no rule is applied here.
    """
    if min_support < 1:
        raise ValueError("min_support must be at least 1")
    if not (0.0 < min_confidence <= 1.0):
        raise ValueError("min_confidence must be between 0 (exclusive) and 1")

    rows_in = len(lines)
    rows_out_of_window = 0
    rows_unkeyed = 0
    rows_excluded_role = 0
    rows_excluded_transfer = 0
    rows_zero = 0
    unknown_accounts = []

    # (vendor_key, direction) -> account key -> list of lines
    buckets: dict = {}
    considered = []

    for line in lines:
        if since is not None and line.date is not None and line.date < since:
            rows_out_of_window += 1
            continue
        if line.signed == 0:
            # A zero line carries no direction and no evidence.
            rows_zero += 1
            continue
        if norm_text(getattr(line, "txn_type", "")) in _TRANSFER_TXN_TYPES:
            rows_excluded_transfer += 1
            continue

        acct = _resolve(accounts, line.account)
        if acct is None:
            if line.account and line.account not in unknown_accounts:
                unknown_accounts.append(line.account)
        elif acct.role in NON_CATEGORY_ROLES:
            rows_excluded_role += 1
            continue

        key = _line_key(line)
        if not key:
            rows_unkeyed += 1
            continue

        bucket = buckets.setdefault((key, _direction_of(line)), {})
        bucket.setdefault(line.account, []).append(line)
        considered.append(line)

    rules, skipped = [], []
    skipped_split = skipped_thin = 0

    for (key, direction), by_account in sorted(buckets.items()):
        tally = sorted(by_account.items(), key=lambda kv: (-len(kv[1]), kv[0]))
        total = sum(len(v) for v in by_account.values())
        top_account, top_lines = tally[0]
        support = len(top_lines)
        conflicts = total - support
        confidence = support / total if total else 0.0
        competing = [(a, len(v)) for a, v in tally]
        source_rows = [l.source_row for l in top_lines if l.source_row][:8]

        if support < min_support:
            skipped_thin += 1
            skipped.append({
                "vendor": key, "direction": direction, "reason": "thin",
                "detail": (f"{support} posted row(s) is not enough to call a pattern "
                           f"(min_support={min_support})"),
                "rows": total, "accounts": competing, "top_account": top_account,
                "confidence": round(confidence, 4), "source_rows": source_rows,
            })
            continue

        if confidence < min_confidence:
            # The valuable case. This vendor really does split, so a rule here
            # would be a confident guess. It becomes a question instead.
            skipped_split += 1
            skipped.append({
                "vendor": key, "direction": direction, "reason": "split",
                "detail": (f"this vendor's history splits across "
                           f"{len(competing)} accounts: "
                           + ", ".join(f"{a} ({n} of {total})" for a, n in competing)),
                "rows": total, "accounts": competing, "top_account": top_account,
                "confidence": round(confidence, 4), "source_rows": source_rows,
            })
            continue

        acct = _resolve(accounts, top_account)
        klasses = {(l.klass or "").strip() for l in top_lines}
        klass = klasses.pop() if len(klasses) == 1 else ""

        moved = "payments" if direction == "out" else "receipts"
        source = (
            f"{support} of {total} posted {moved} keyed to {key!r} went to "
            f"{acct.label() if acct else top_account}"
            + (f"; source rows {', '.join(str(r) for r in source_rows)}" if source_rows else "")
        )
        rules.append(Rule(
            id=f"mined-{slug(key, 40)}-{direction}",
            account=top_account,
            account_full=acct.full_name if acct else "",
            match_contains=(key,),
            direction=direction,
            klass=klass,
            source=source,
            confidence=round(confidence, 4),
            support=support,
            conflicts=conflicts,
            note="mined from posted history; review before relying on it",
        ))

    # ------------------------------------------------------------- the replay
    #
    # The only honest test of a mined rule set is whether it reproduces the
    # history it was mined from. Anything else is the rules grading their own
    # homework on data they have not seen. This is deliberately run over the
    # SAME rows: a rule set that cannot even reproduce its own evidence is
    # broken, and that failure is silent otherwise.
    index = {(r.match_contains[0], r.direction): r for r in rules if r.match_contains}
    replay_rows = replay_correct = 0
    for line in considered:
        rule = index.get((_line_key(line), _direction_of(line)))
        if rule is None:
            continue
        replay_rows += 1
        if rule.account == line.account:
            replay_correct += 1

    rows_categorizable = len(considered)
    rows_covered = sum(r.support + r.conflicts for r in rules)
    stats = {
        "rows_in": rows_in,
        "rows_out_of_window": rows_out_of_window,
        "rows_zero": rows_zero,
        "rows_excluded_funding_leg": rows_excluded_role,
        "rows_excluded_transfer": rows_excluded_transfer,
        "rows_unkeyed": rows_unkeyed,
        "rows_categorizable": rows_categorizable,
        "vendors": len(buckets),
        "rules": len(rules),
        "rows_covered": rows_covered,
        "coverage": round(rows_covered / rows_categorizable, 4) if rows_categorizable else 0.0,
        "replay_rows": replay_rows,
        "replay_correct": replay_correct,
        # The headline. Share of the rows a mined rule fires on that it codes
        # the way the books actually did.
        "accuracy": round(replay_correct / replay_rows, 4) if replay_rows else 0.0,
        # The same correct count over ALL categorisable history, so a rule set
        # that is accurate on a tiny slice cannot look finished.
        "accuracy_overall": round(replay_correct / rows_categorizable, 4) if rows_categorizable else 0.0,
        "skipped_split": skipped_split,
        "skipped_thin": skipped_thin,
        "unknown_accounts": unknown_accounts,
    }
    return MiningResult(rules=rules, skipped=skipped, stats=stats)


def explain(rule: Rule) -> str:
    """One plain sentence a non-accountant can check against their own books."""
    what = rule.match_contains[0] if rule.match_contains else (rule.match_regex or "this vendor")
    where = rule.account_full or rule.account
    if rule.direction == "in":
        moved = f"Money coming in from {what!r}"
    elif rule.direction == "out":
        moved = f"Money going out to {what!r}"
    else:
        moved = f"Anything matching {what!r}"
    total = rule.support + rule.conflicts
    if total:
        evidence = (f"{rule.support} of the last {total} went there "
                    f"({100.0 * rule.confidence:.0f}% of that vendor's history)")
    else:
        evidence = "there is no posted history behind this rule"
    return f"{moved} is coded to {where}, because {evidence}."
