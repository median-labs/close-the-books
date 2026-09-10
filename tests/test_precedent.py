"""Precedent-mining tests. Synthetic fixtures only.

Every company, vendor, account and figure below is invented. "Acme Robotics
Inc." is not a real company, and none of these descriptors came from a real
statement.

    pytest tests/test_precedent.py         # or
    python3 tests/test_precedent.py
"""

from __future__ import annotations

import datetime as _dt
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "lib"))

from closethebooks.model import Account, JournalLine                       # noqa: E402
from closethebooks.precedent import (                                      # noqa: E402
    MiningResult, explain, mine_rules, vendor_key,
)
from closethebooks.profile import Profile, validate                        # noqa: E402
from closethebooks.util import money                                       # noqa: E402

COMPANY = "Acme Robotics Inc."

# Codepoints for en dash and em dash. House style forbids both in anything a
# human reads, and referring to them by number keeps this file itself clean.
DASH_CODEPOINTS = (0x2013, 0x2014)


# ------------------------------------------------------------- the fixtures

def account(number, name, acct_type, role):
    return Account(number=number, name=name, full_name=name, type=acct_type, role=role)


CHART = {a.number: a for a in [
    account("1000", "Mercury Checking", "Bank", "bank"),
    account("1010", "Treasury Savings", "Bank", "bank"),
    account("2000", "Accounts Payable", "Accounts Payable (A/P)", "ap"),
    account("2100", "Company Card", "Credit Card", "card"),
    account("4000", "Product Revenue", "Income", "revenue"),
    account("6100", "Software Subscriptions", "Expenses", "expense"),
    account("6200", "Office Supplies", "Expenses", "expense"),
    account("6300", "Meals and Entertainment", "Expenses", "expense"),
    account("6400", "Rent", "Expenses", "expense"),
    account("6500", "Payroll Expense", "Expenses", "expense"),
    account("6900", "Merchant Fees", "Expenses", "expense"),
]}

_ROW = [0]


def _next_row():
    _ROW[0] += 1
    return _ROW[0]


def spend(day, descriptor, amount, account_key, *, month=3, year=2026,
          funding="1000", klass="", txn_type="Expense"):
    """One money-out transaction, BOTH legs, the way an export gives them.

    The category leg is a debit; the funding leg is a credit on the bank or the
    card. Both legs carry the payee, which is what makes "ignore the funding
    leg" a real decision rather than a theoretical one.
    """
    amt = money(amount)
    return [
        JournalLine(date=_dt.date(year, month, day), account=account_key,
                    debit=amt, name=descriptor, txn_type=txn_type, klass=klass,
                    account_full=CHART[account_key].full_name,
                    source_file="general-ledger.xlsx", source_row=_next_row()),
        JournalLine(date=_dt.date(year, month, day), account=funding,
                    credit=amt, name=descriptor, txn_type=txn_type, klass=klass,
                    account_full=CHART[funding].full_name,
                    source_file="general-ledger.xlsx", source_row=_next_row()),
    ]


def receive(day, descriptor, amount, account_key, *, month=3, year=2026,
            funding="1000", txn_type="Deposit"):
    """One money-in transaction, both legs. The category leg is a credit."""
    amt = money(amount)
    return [
        JournalLine(date=_dt.date(year, month, day), account=account_key,
                    credit=amt, name=descriptor, txn_type=txn_type,
                    account_full=CHART[account_key].full_name,
                    source_file="general-ledger.xlsx", source_row=_next_row()),
        JournalLine(date=_dt.date(year, month, day), account=funding,
                    debit=amt, name=descriptor, txn_type=txn_type,
                    account_full=CHART[funding].full_name,
                    source_file="general-ledger.xlsx", source_row=_next_row()),
    ]


def build_history():
    """A year of posted history with the shapes that matter in it.

    settled     one vendor, one account, many rows      -> a rule
    mostly      one vendor, one account 4 times in 5    -> a rule, with a conflict
    split       one vendor, two accounts evenly         -> a QUESTION
    thin        one vendor, one row                     -> a QUESTION
    two-way     one vendor with money in AND money out  -> two separate rules
    """
    _ROW[0] = 0
    h = []

    # Settled: 12 payments to a cloud vendor, every one to Software. The
    # descriptor is written a different way nearly every month, which is the
    # normal case and the reason vendor_key exists.
    contoso = [
        "POS DEBIT VISA CHECKCARD 0114 CONTOSO CLOUD 800-555-0142 WA",
        "ACH DEBIT CONTOSO CLOUD SVCS PAYMENT 4471820993",
        "RECURRING CONTOSO CLOUD 03/14 REF#88213",
        "CONTOSO CLOUD INC",
        "Contoso Cloud",
        "CONTOSO CLOUD SEATTLE WA",
    ]
    for i in range(12):
        h += spend(1 + (i % 27), contoso[i % len(contoso)], "412.00", "6100",
                   month=1 + (i % 12), klass="Operations")

    # Mostly settled: 4 of 5 to Office Supplies, one coded to Software by
    # someone in a hurry. 80% clears the default 75% bar, and the odd row is
    # reported as a conflict rather than hidden.
    for i in range(4):
        h += spend(3 + i, "ACH DEBIT NORTHWIND SUPPLY PAYMENT 1234567890",
                   "180.00", "6200", month=2 + i)
    h += spend(9, "NORTHWIND SUPPLY CO", "180.00", "6100", month=7)

    # A genuine split. Two of these were team lunches and two were supplies
    # bought at the same shop. Nothing in the descriptor separates them, so a
    # rule here would be a confident guess.
    for i in range(2):
        h += spend(6 + i, "SQ *GRAYLINE COFFEE  SEATTLE WA", "42.50", "6300", month=4 + i)
    for i in range(2):
        h += spend(8 + i, "PURCHASE AUTHORIZED ON 03/14 GRAYLINE COFFEE SEATTLE WA CARD 4471",
                   "42.50", "6200", month=6 + i)

    # Thin: one row is not a pattern.
    h += spend(1, "WWW.PINEBROOK-REALTY.COM RENT", "4200.00", "6400", month=5)

    # Two-way: the same payment processor deposits money and charges fees. If
    # direction were ignored these 9 rows would be one vendor at 67%, and
    # either no rule at all or a rule that codes deposits to Merchant Fees.
    for i in range(6):
        h += receive(12 + i, "ACH CREDIT RIVERBEND PAYMENTS 8891023", "9500.00", "4000",
                     month=1 + i)
    for i in range(3):
        h += spend(15 + i, "ACH DEBIT RIVERBEND PAYMENTS 8891024", "285.00", "6900",
                   month=1 + i)

    # Payroll, three runs.
    for i in range(3):
        h += spend(15, "DIRECT DEP VERTEX PAYROLL SVC PPD ID: 9812310099",
                   "18400.00", "6500", month=9 + i)
    return h


HISTORY = build_history()

# Worked out by hand from build_history, and the arithmetic is the point of the
# replay test below.
CATEGORIZABLE = 12 + 5 + 4 + 1 + 6 + 3 + 3          # 34 category legs
COVERED = 12 + 5 + 6 + 3 + 3                        # 29 rows under an emitted rule
CORRECT = COVERED - 1                               # the one misfiled Northwind row


# --------------------------------------------------------- the vendor key

def test_vendor_key_strips_the_noise_a_bank_wraps_a_vendor_in():
    """Each row names the real-world noise it is about."""
    table = [
        # the card rail and a support phone number
        ("POS DEBIT VISA CHECKCARD 0114 CONTOSO CLOUD 800-555-0142 WA", "contoso cloud"),
        # the ACH rail, a generic SVCS tail and a trailing reference number
        ("ACH DEBIT CONTOSO CLOUD SVCS PAYMENT 4471820993", "contoso cloud"),
        # a schedule word, an embedded date and a REF#
        ("RECURRING CONTOSO CLOUD 03/14 REF#88213", "contoso cloud"),
        # a corporate suffix
        ("CONTOSO CLOUD INC", "contoso cloud"),
        ("Contoso Cloud", "contoso cloud"),
        # a card-network city and state tail
        ("CONTOSO CLOUD SEATTLE WA", "contoso cloud"),
        # "CO" is a corporate suffix and a state code; either way it goes, and
        # the word before it must survive
        ("NORTHWIND SUPPLY CO", "northwind supply"),
        ("Northwind Supply Inc.", "northwind supply"),
        ("ACH DEBIT NORTHWIND SUPPLY PAYMENT 1234567890", "northwind supply"),
        # a payment-aggregator star prefix
        ("SQ *GRAYLINE COFFEE  SEATTLE WA", "grayline coffee"),
        ("TST* GRAYLINE COFFEE", "grayline coffee"),
        # a Wells-style authorisation preamble plus a card mask
        ("PURCHASE AUTHORIZED ON 03/14 GRAYLINE COFFEE SEATTLE WA CARD 4471", "grayline coffee"),
        # a store number twice over
        ("BOLT DEPOT #0421 STORE 0421 PORTLAND OR", "bolt depot"),
        # a NACHA entry-class code and an ID reference
        ("DIRECT DEP VERTEX PAYROLL SVC PPD ID: 9812310099", "vertex payroll"),
        # a long digit run and an XXXX card mask
        ("RIVERBEND PAYMENTS TRANSFER 20260314 XXXXXX8891", "riverbend payments"),
        # a WWW prefix and a domain tail
        ("WWW.PINEBROOK-REALTY.COM RENT", "pinebrook realty rent"),
        # the wire rail and a corporate suffix
        ("INTL WIRE TRANSFER HELIOS DESIGN STUDIO LTD", "helios design studio"),
        # a channel word in front of the vendor
        ("ONLINE PAYMENT TO LUMEN OFFICE LEASING", "lumen office leasing"),
    ]
    wrong = [(raw, want, vendor_key(raw)) for raw, want in table if vendor_key(raw) != want]
    assert not wrong, "vendor_key did not strip cleanly:\n  " + "\n  ".join(
        f"{raw!r}: wanted {want!r}, got {got!r}" for raw, want, got in wrong
    )


def test_the_same_vendor_reduces_to_one_key_across_every_rail():
    keys = {vendor_key(d) for d in (
        "POS DEBIT VISA CHECKCARD 0114 CONTOSO CLOUD 800-555-0142 WA",
        "ACH DEBIT CONTOSO CLOUD SVCS PAYMENT 4471820993",
        "RECURRING CONTOSO CLOUD 03/14 REF#88213",
        "CONTOSO CLOUD INC",
    )}
    assert keys == {"contoso cloud"}, keys


def test_two_different_vendors_do_not_collapse_into_one_key():
    """Over-stripping is the unsafe failure: it emits a confident wrong rule."""
    assert vendor_key("NORTHWIND SUPPLY CO") != vendor_key("NORTHWIND SYSTEMS INC")
    assert vendor_key("CONTOSO CLOUD") != vendor_key("CONTOSO LOGISTICS")


def test_a_descriptor_that_names_no_vendor_gets_no_key():
    """A blank key is a real answer, not a grouping bucket.

    Two rows reading "ACH DEBIT 4471820993" and "ONLINE PAYMENT" name no vendor.
    Grouping them together would invent a vendor out of two unrelated payments.
    """
    for blind in ("ACH DEBIT 4471820993", "ONLINE PAYMENT", "", None, "   ", "POS DEBIT"):
        assert vendor_key(blind) == "", repr(blind)


# ------------------------------------------------------------ mining rules

def mined():
    return mine_rules(HISTORY, CHART)


def rule_for(result, key, direction):
    for r in result.rules:
        if r.match_contains and r.match_contains[0] == key and r.direction == direction:
            return r
    return None


def skip_for(result, key, direction):
    for s in result.skipped:
        if s["vendor"] == key and s["direction"] == direction:
            return s
    return None


def test_a_clean_single_account_vendor_becomes_a_rule():
    r = rule_for(mined(), "contoso cloud", "out")
    assert r is not None, "the 12-row vendor produced no rule"
    assert r.account == "6100", r.account
    assert r.support == 12 and r.conflicts == 0
    assert r.confidence == 1.0
    assert r.klass == "Operations", "a class every supporting row agreed on was dropped"


def test_a_rule_that_mostly_agrees_reports_the_rows_that_did_not():
    r = rule_for(mined(), "northwind supply", "out")
    assert r is not None
    assert r.account == "6200"
    assert (r.support, r.conflicts) == (4, 1)
    assert abs(r.confidence - 0.8) < 1e-9, r.confidence


def test_a_genuinely_split_vendor_becomes_a_skip_naming_both_accounts():
    """The whole point. A split vendor is a question, never a guess."""
    result = mined()
    assert rule_for(result, "grayline coffee", "out") is None, \
        "a vendor whose history splits 2 and 2 must not produce a rule"
    s = skip_for(result, "grayline coffee", "out")
    assert s is not None and s["reason"] == "split", s
    named = {a for a, _ in s["accounts"]}
    assert named == {"6200", "6300"}, named
    assert "6200" in s["detail"] and "6300" in s["detail"]
    assert s["rows"] == 4


def test_a_split_vendor_turns_into_an_answerable_question():
    qs = [q for q in mined().questions() if "grayline" in q["id"]]
    assert len(qs) == 1, qs
    assert "6200" in qs[0]["question"] and "6300" in qs[0]["question"]
    assert qs[0]["evidence"], "a question with no source rows cannot be checked"


def test_min_support_is_honoured():
    result = mined()
    assert rule_for(result, "pinebrook realty rent", "out") is None
    s = skip_for(result, "pinebrook realty rent", "out")
    assert s is not None and s["reason"] == "thin", s

    # Lower the bar and the same single row becomes a rule.
    loose = mine_rules(HISTORY, CHART, min_support=1)
    r = rule_for(loose, "pinebrook realty rent", "out")
    assert r is not None and r.account == "6400"


def test_min_confidence_is_honoured():
    # 80% passes the default 75% bar and fails a 90% bar. Nothing else changes.
    assert rule_for(mine_rules(HISTORY, CHART, min_confidence=0.75), "northwind supply", "out")
    strict = mine_rules(HISTORY, CHART, min_confidence=0.9)
    assert rule_for(strict, "northwind supply", "out") is None
    assert skip_for(strict, "northwind supply", "out")["reason"] == "split"


def test_money_in_and_money_out_are_kept_apart():
    """A processor's deposit and its fee are not the same rule."""
    result = mined()
    deposit = rule_for(result, "riverbend payments", "in")
    fee = rule_for(result, "riverbend payments", "out")
    assert deposit is not None and fee is not None
    assert deposit.account == "4000", deposit.account
    assert fee.account == "6900", fee.account
    assert (deposit.support, fee.support) == (6, 3)
    assert deposit.confidence == 1.0 and fee.confidence == 1.0


def test_the_bank_leg_never_becomes_the_rule_account():
    """Both legs of every transaction carry the payee.

    Each of these vendors has exactly as many bank-side rows as category-side
    rows, so a miner that counted the funding leg would land on 50% for every
    vendor and emit either nothing or the bank account itself.
    """
    result = mined()
    funding = {"1000", "1010", "2100"}
    offenders = [r.id for r in result.rules if r.account in funding]
    assert not offenders, f"a funding account became a rule: {offenders}"
    assert rule_for(result, "contoso cloud", "out").account == "6100"
    assert result.stats["rows_excluded_funding_leg"] == CATEGORIZABLE, \
        result.stats["rows_excluded_funding_leg"]


def test_the_accounts_payable_leg_is_not_counted_either():
    """On accrual, the bill and its payment both name the vendor.

    Dr expense / Cr AP, then Dr AP / Cr bank. Counting the AP leg would sit
    every accrual vendor at 50% between its real expense account and Accounts
    Payable, and no vendor would ever clear the threshold.
    """
    _ROW[0] = 500
    lines = []
    for i in range(3):
        d = _dt.date(2026, 4 + i, 5)
        lines += [
            JournalLine(date=d, account="6200", debit=money("310.00"),
                        name="HELIOS DESIGN STUDIO LTD", txn_type="Bill", source_row=_next_row()),
            JournalLine(date=d, account="2000", credit=money("310.00"),
                        name="HELIOS DESIGN STUDIO LTD", txn_type="Bill", source_row=_next_row()),
            JournalLine(date=d, account="2000", debit=money("310.00"),
                        name="HELIOS DESIGN STUDIO LTD", txn_type="Bill Payment", source_row=_next_row()),
            JournalLine(date=d, account="1000", credit=money("310.00"),
                        name="HELIOS DESIGN STUDIO LTD", txn_type="Bill Payment", source_row=_next_row()),
        ]
    result = mine_rules(lines, CHART)
    r = rule_for(result, "helios design studio", "out")
    assert r is not None, "the AP leg swallowed an otherwise unanimous vendor"
    assert r.account == "6200" and r.support == 3 and r.confidence == 1.0


def test_transfers_between_the_companys_own_accounts_are_not_mined():
    _ROW[0] = 900
    d = _dt.date(2026, 5, 2)
    lines = [
        JournalLine(date=d, account="1010", debit=money("50000.00"),
                    name="TRANSFER TO TREASURY SAVINGS", txn_type="Transfer", source_row=_next_row()),
        JournalLine(date=d, account="1000", credit=money("50000.00"),
                    name="TRANSFER TO TREASURY SAVINGS", txn_type="Transfer", source_row=_next_row()),
    ]
    result = mine_rules(lines, CHART, min_support=1)
    assert result.rules == [], [r.id for r in result.rules]
    assert result.stats["rows_excluded_transfer"] == 2


def test_since_excludes_the_older_era():
    """A chart change or a prior bookkeeper's year is excluded by date."""
    late = mine_rules(HISTORY, CHART, since=_dt.date(2026, 9, 1))
    assert rule_for(late, "vertex payroll", "out") is not None
    assert rule_for(late, "riverbend payments", "in") is None, \
        "rows before `since` were still counted"
    assert late.stats["rows_out_of_window"] > 0


def test_every_rule_names_the_evidence_behind_it():
    for r in mined().rules:
        assert r.source, f"rule {r.id} has no source"
        assert str(r.support) in r.source, r.source
        assert (r.account_full or r.account) in r.source or r.account in r.source, r.source
        assert "source rows" in r.source, r.source


def test_mined_rules_pass_the_profile_validator():
    """The validator rejects a rule with no source. Mined rules must survive it."""
    prof = Profile()
    prof.what_goes_where = mined().rules
    assert validate(prof) == []


# ---------------------------------------------------------------- the replay

def test_the_replay_accuracy_statistic_is_computed_correctly():
    """The honest measure: replay the rules against the history they came from.

    Of 34 categorisable rows, the emitted rules fire on 29 and agree with the
    books on 28. The single disagreement is the Northwind row a human coded to
    Software instead of Office Supplies, and it must show up here rather than
    being absorbed.
    """
    s = mined().stats
    assert s["rows_categorizable"] == CATEGORIZABLE, s["rows_categorizable"]
    assert s["rows_covered"] == COVERED, s["rows_covered"]
    assert s["replay_rows"] == COVERED, s["replay_rows"]
    assert s["replay_correct"] == CORRECT, s["replay_correct"]
    assert abs(s["accuracy"] - CORRECT / COVERED) < 1e-4, s["accuracy"]
    assert abs(s["accuracy_overall"] - CORRECT / CATEGORIZABLE) < 1e-4, s["accuracy_overall"]
    assert abs(s["coverage"] - COVERED / CATEGORIZABLE) < 1e-4, s["coverage"]


def test_a_perfectly_consistent_history_replays_at_one_hundred_percent():
    _ROW[0] = 300
    lines = []
    for i in range(8):
        lines += spend(4, "CONTOSO CLOUD INC", "412.00", "6100", month=1 + i)
    s = mine_rules(lines, CHART).stats
    assert s["accuracy"] == 1.0 and s["accuracy_overall"] == 1.0
    assert s["coverage"] == 1.0


def test_accuracy_is_zero_when_there_is_nothing_to_replay():
    s = mine_rules([], CHART).stats
    assert s["accuracy"] == 0.0 and s["replay_rows"] == 0
    assert s["rows_categorizable"] == 0


def test_the_headline_number_is_the_first_thing_describe_prints():
    text = mined().describe()
    assert text.splitlines()[0].startswith("REPLAY ACCURACY"), text
    # 28 of 29. The percentage is rendered from the stat, not recomputed.
    assert "96.5%" in text.splitlines()[0], text.splitlines()[0]
    assert "28 of 29" in text.splitlines()[0], text.splitlines()[0]


def test_stats_account_for_every_row_read():
    s = mined().stats
    assert s["rows_in"] == len(HISTORY)
    counted = (s["rows_categorizable"] + s["rows_excluded_funding_leg"]
               + s["rows_excluded_transfer"] + s["rows_unkeyed"]
               + s["rows_out_of_window"] + s["rows_zero"])
    assert counted == s["rows_in"], f"{counted} accounted for out of {s['rows_in']}"


def test_a_result_is_a_mining_result():
    result = mined()
    assert isinstance(result, MiningResult)
    assert result.accuracy == result.stats["accuracy"]


# ------------------------------------------------------------------ explain

def test_explain_is_one_sentence_a_founder_can_check():
    r = rule_for(mined(), "contoso cloud", "out")
    text = explain(r)
    assert text.count(".") == 1 and text.endswith("."), text
    assert "contoso cloud" in text
    assert "Software Subscriptions" in text
    assert "12 of the last 12" in text
    assert "100%" in text
    assert not any(ord(c) in DASH_CODEPOINTS for c in text), text


def test_explain_says_which_direction_the_money_went():
    result = mined()
    assert explain(rule_for(result, "riverbend payments", "in")).startswith("Money coming in")
    assert explain(rule_for(result, "riverbend payments", "out")).startswith("Money going out")


def test_bad_thresholds_are_refused_rather_than_silently_clamped():
    for kwargs in ({"min_support": 0}, {"min_confidence": 0.0}, {"min_confidence": 1.5}):
        try:
            mine_rules(HISTORY, CHART, **kwargs)
        except ValueError:
            continue
        raise AssertionError(f"mine_rules accepted {kwargs}")


def _run():
    tests = [(n, o) for n, o in sorted(globals().items())
             if n.startswith("test_") and callable(o)]
    failures = 0
    for name, fn in tests:
        try:
            fn()
            print(f"  ok   {name}")
        except Exception as exc:                      # noqa: BLE001
            failures += 1
            print(f"  FAIL {name}: {type(exc).__name__}: {exc}")
    print(f"\n{len(tests) - failures} passed, {failures} failed")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(_run())
