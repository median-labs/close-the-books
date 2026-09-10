"""What a preparer has to be given, and what a simple company must never be asked.

Two invented companies, and the pair is the point. `plain_company` is a small
consulting corporation with one bank account, wages, rent, contractors and one
class of stock. `complicated_company` is a corporation with a German subsidiary
it is owed money by, convertible notes in equity, a research credit already
posted, intangible assets, a prior year, and payroll that stops in April of a
year that runs to December.

Every test here runs in one of two directions:

  * the trigger fires, and the requirement names what raised it, or
  * the trigger does NOT fire, and the simple company is never asked.

The second direction is the one that decays quietly. A questionnaire that asks
a one-bank-account company about controlled foreign corporations is not merely
noise: the first wrong question is what teaches somebody to skim the rest, and
after that the real questions go unanswered too.

Two of these tests exist because the code was wrong in exactly that way while it
was being written:

  * `test_a_subscription_called_tokenridge_is_not_a_digital_asset`. Searching
    memos for subject words matched 27 lines on a real file, every one a monthly
    login-service subscription.
  * `test_money_sent_to_a_subsidiary_is_not_a_receipt_from_it`. The engine is
    debit-positive, so a credit on a bank account is money LEAVING. A first
    draft tested the credit side and reported six payments out as receipts in.

    python3 tests/test_readiness.py
"""

from __future__ import annotations

import datetime
import os
import re
import sys
from decimal import Decimal

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "lib"))
sys.path.insert(0, HERE)

from closethebooks import readiness                                    # noqa: E402
from closethebooks.model import Account, JournalLine, Ledger           # noqa: E402
from closethebooks.profile import Entity, Profile                      # noqa: E402
from closethebooks.readiness import DOCUMENT, QUESTION, scan           # noqa: E402

D = Decimal
YEAR = 2025
START = datetime.date(2024, 1, 1)
END = datetime.date(2025, 12, 31)

# En dash and em dash, by codepoint. House style forbids both in anything a
# person reads, and writing them literally here would put them in this file.
DASHES = tuple(chr(c) for c in (0x2013, 0x2014))


def _flat(lines) -> str:
    return re.sub(r"\s+", " ", "\n".join(lines))


def _account(number, full_name, acct_type, role):
    return Account(name=full_name.split(":")[-1], number=number, full_name=full_name,
                   type=acct_type, role=role)


def _line(when, account, amount, memo="", name="", txn="Expense"):
    """One posted line, debit positive. A negative amount is a credit."""
    amount = D(str(amount))
    return JournalLine(date=when, account=account, txn_type=txn, memo=memo, name=name,
                       debit=amount if amount > 0 else D("0.00"),
                       credit=-amount if amount < 0 else D("0.00"))


def _ledger(company, accounts, lines, openings=None, basis="the Beginning Balance rows"):
    return Ledger(company=company, period_start=START, period_end=END,
                  accounts={a.key: a for a in accounts}, lines=list(lines),
                  opening_basis=basis,
                  opening_balances={k: D(str(v)) for k, v in (openings or {}).items()})


def _profile(name, end_use="the 2025 federal corporate income tax return"):
    return Profile(entity=Entity(name=name, basis="accrual", end_use=end_use,
                                 deadline="2026-10-15", materiality="500.00"))


# ------------------------------------------------------------ the two companies

def plain_company():
    """One bank account, wages every month, rent, contractors, one class of stock.

    Nothing foreign, nothing convertible, no digital assets, no research credit,
    and no prior year. Its whole job is to be asked as little as possible.
    """
    accounts = [
        _account("1010", "Harbor Checking 4411", "Bank", "bank"),
        _account("1100", "Accounts Receivable", "Accounts Receivable (A/R)", "ar"),
        _account("2010", "Accounts Payable", "Accounts Payable (A/P)", "ap"),
        _account("3000", "Common Stock", "Equity", "equity"),
        _account("4000", "Consulting Revenue", "Income", "revenue"),
        _account("6000", "Salaries and Wages", "Expenses", "expense"),
        _account("6100", "Contract Labor", "Expenses", "expense"),
        _account("6400", "Rent", "Expenses", "expense"),
        _account("6900", "Travel and Meals", "Expenses", "expense"),
        _account("6200", "Software Subscriptions", "Expenses", "expense"),
    ]
    lines = [_line(datetime.date(YEAR, 1, 4), "3000", -1000, "founder stock")]
    for month in range(1, 13):
        day = datetime.date(YEAR, month, 15)
        lines += [
            _line(day, "6000", 12000, "payroll"),
            _line(day, "6400", 3500, "office rent"),
            _line(day, "1010", -15500, "payroll and rent"),
            _line(day, "4000", -30000, "client invoice"),
            _line(day, "1010", 30000, "client payment"),
            # A monthly subscription to a login service. The word "token" is in
            # the vendor's name and nothing about it is a digital asset.
            _line(day, "6200", 400, "TOKENRIDGE monthly", name="Tokenridge"),
            _line(day, "1010", -400, "TOKENRIDGE monthly", name="Tokenridge"),
        ]
    lines += [_line(datetime.date(YEAR, 6, 30), "6100", 44000, "contract engineering"),
              _line(datetime.date(YEAR, 6, 30), "1010", -44000, "contract engineering"),
              _line(datetime.date(YEAR, 8, 2), "6900", 1800, "client dinner"),
              _line(datetime.date(YEAR, 8, 2), "1010", -1800, "client dinner")]
    return _ledger("Harbor Consulting Inc.", accounts, lines)


def complicated_company():
    """A German subsidiary, convertible notes, a research credit, a prior year."""
    accounts = [
        _account("101000", "Current Assets:Mercury Checking (4015)", "Bank", "bank"),
        _account("138500", "Other Current Assets:R&D Tax Credit Receivable",
                 "Other Current Assets", "other"),
        _account("161000", "Intangible Assets:Domain", "Fixed Assets", "intangible"),
        _account("172500", "Other Assets:SAFE Notes in Bright Harbor Labs",
                 "Other Assets", "safe"),
        _account("173000", "Other Assets:Investments into Vesterhavn Systems GmbH",
                 "Other Assets", "other"),
        _account("174000", "Other Assets:Due From Vesterhavn Systems GmbH",
                 "Other Assets", "intercompany"),
        _account("310001", "Equity:Common Stock", "Equity", "equity"),
        _account("340000", "Equity:SAFE Notes", "Equity", "equity"),
        _account("401000", "Sales:Subscription Revenue", "Income", "revenue"),
        _account("602001", "Payroll Expense:Wages", "Expenses", "expense"),
        _account("604400", "Professional Fees:Contractors & Consultants",
                 "Expenses", "expense"),
        _account("605000", "Travel Expenses", "Expenses", "expense"),
        _account("607000", "Entertainment / Team Events", "Expenses", "expense"),
        _account("614000", "Rent Expense", "Expenses", "expense"),
        _account("905000", "Other (Income) / Expenses:R&D Tax Credit Income",
                 "Other Expenses", "expense"),
        _account("Uncategorized Income", "Uncategorized Income", "Income", "revenue"),
    ]
    openings = {
        "174000": "1174300.55", "340000": "-1862400.00", "310001": "-74.00",
        "161000": "10000.00", "138500": "6028.00", "172500": "2000.00",
        "173000": "10000.00", "101000": "65520.12",
    }
    lines = []
    # The prior year, which is inside the exported period but outside the filing
    # year. Nothing here may raise a requirement about the 2025 return.
    lines += [_line(datetime.date(2024, 5, 9), "Uncategorized Income", -7605,
                    "unexplained receipt", txn="Deposit"),
              _line(datetime.date(2024, 5, 9), "101000", 7605, "unexplained receipt",
                    txn="Deposit")]
    # The filing year. Payroll runs to April and then stops.
    for month in (1, 2, 3, 4):
        day = datetime.date(YEAR, month, 16)
        lines += [_line(day, "602001", 14000, "payroll"),
                  _line(day, "101000", -14000, "payroll")]
    for month, amount in ((1, 30000), (2, 31577.85), (3, 5000)):
        day = datetime.date(YEAR, month, 8)
        lines += [
            _line(day, "174000", amount, "Vesterhavn Systems GmbH; Invoice",
                  name="Vesterhavn Systems GmbH"),
            _line(day, "101000", -amount, "Vesterhavn Systems GmbH; Invoice",
                  name="Vesterhavn Systems GmbH"),
        ]
    lines += [
        _line(datetime.date(YEAR, 6, 3), "604400", 34061.27, "contractor invoices"),
        _line(datetime.date(YEAR, 6, 3), "101000", -34061.27, "contractor invoices"),
        _line(datetime.date(YEAR, 7, 1), "607000", 1022.52, "team offsite"),
        _line(datetime.date(YEAR, 7, 1), "101000", -1022.52, "team offsite"),
        _line(datetime.date(YEAR, 2, 1), "614000", 504.65, "office rent"),
        _line(datetime.date(YEAR, 2, 1), "101000", -504.65, "office rent"),
        _line(datetime.date(YEAR, 3, 5), "401000", -7600, "subscriptions"),
        _line(datetime.date(YEAR, 3, 5), "101000", 7600, "subscriptions"),
    ]
    return _ledger("Vantage Meridian Inc.", accounts, lines, openings)


def _plain(**kw):
    kw.setdefault("profile", _profile("Harbor Consulting Inc."))
    kw.setdefault("filing_year", YEAR)
    return scan(plain_company(), **kw)


def _complicated(**kw):
    kw.setdefault("profile", _profile("Vantage Meridian Inc."))
    kw.setdefault("filing_year", YEAR)
    return scan(complicated_company(), **kw)


def _groups(scanned):
    return set(scanned.groups)


# ------------------------------------------------- the simple company, not asked

def test_a_one_bank_account_company_is_never_asked_about_a_foreign_subsidiary():
    assert "foreign-entity" not in _groups(_plain())
    assert "foreign-owner" not in _groups(_plain())


def test_a_company_with_no_convertible_instrument_is_never_asked_about_one():
    assert "convertible" not in _groups(_plain())
    assert "convertible-held" not in _groups(_plain())


def test_a_company_with_no_digital_asset_account_is_never_asked_about_crypto():
    assert "digital-assets" not in _groups(_plain())


def test_a_subscription_called_tokenridge_is_not_a_digital_asset():
    """The one that says why triggers read the chart and not the memo field.

    The plain company pays a login service called Tokenridge every month, so
    its ledger holds 24 lines containing the word "token". A trigger that read
    descriptors would ask it about cryptocurrency.
    """
    ledger = plain_company()
    hits = [l for l in ledger.lines if "token" in (l.memo + l.name).lower()]
    assert len(hits) == 24, "the fixture has to carry the tempting rows"
    assert "digital-assets" not in _groups(_plain())


def test_the_simple_company_is_asked_only_what_its_own_books_call_for():
    assert _groups(_plain()) == {
        "ownership-roster", "officer-pay", "contractors", "meals", "state-footprint",
    }


def test_a_first_year_company_is_not_asked_for_last_years_return():
    """Nothing carried in, so there is no prior return to ask for."""
    assert "prior-return" not in _groups(_plain())


def test_a_company_whose_payroll_runs_all_year_is_not_asked_if_it_is_closing():
    assert "still-trading" not in _groups(_plain())
    assert "wind-down" not in _groups(_plain())


# ------------------------------------------- the complicated company, asked fully

def test_a_german_subsidiary_raises_the_questions_that_filing_needs():
    scanned = _complicated()
    ids = {r.id for r in scanned.of_group("foreign-entity")}
    for tail in ("balance", "loan-or-trade", "agreement", "interest", "accounts",
                 "ownership"):
        assert any(i.endswith("." + tail) for i in ids), tail
    assert len(ids) == 6


def test_the_foreign_trigger_names_the_account_and_the_figure_behind_it():
    req = _complicated().get("filing.foreign-entity.vesterhavn-systems.balance")
    assert req is not None
    assert "Vesterhavn Systems GmbH" in req.trigger
    assert any("174000" in e for e in req.evidence)
    assert any("Due From Vesterhavn Systems GmbH" in e for e in req.evidence)


def test_the_subsidiarys_own_accounts_are_a_document_and_not_a_question():
    assert _complicated().get(
        "filing.foreign-entity.vesterhavn-systems.accounts").kind == DOCUMENT
    assert _complicated().get(
        "filing.foreign-entity.vesterhavn-systems.interest").kind == QUESTION


def test_convertible_notes_in_equity_raise_the_instrument_itself():
    req = _complicated().get("filing.convertible.instrument")
    assert req.kind == DOCUMENT
    assert "equity" in req.trigger
    assert any("1,862,400.00 Cr" in e for e in req.evidence)


def test_a_convertible_the_company_holds_is_a_different_subject_from_one_it_issued():
    scanned = _complicated()
    assert scanned.of_group("convertible")
    held = scanned.of_group("convertible-held")
    assert held and "Bright Harbor Labs" in held[0].trigger


def test_payroll_raises_officer_compensation():
    req = _complicated().get("filing.officer-pay.split")
    assert req is not None
    assert "officers" in req.need.lower()
    assert "payroll" in req.trigger.lower()


def test_a_research_credit_already_in_the_books_raises_the_study_behind_it():
    req = _complicated().get("filing.rd-credit.study")
    assert req.kind == DOCUMENT
    assert any("138500" in e for e in req.evidence)


def test_a_company_that_traded_before_is_asked_for_last_years_return():
    req = _complicated().get("filing.prior-return.copy")
    assert req.kind == DOCUMENT
    assert "traded before" in req.trigger


def test_payroll_stopping_in_april_asks_whether_the_company_was_still_trading():
    req = _complicated().get("filing.still-trading.status")
    assert req is not None
    assert "2025-04-16" in req.trigger
    assert "8 month(s)" in req.trigger


# ------------------------------------------------------ triggers in both directions

def test_a_foreign_company_form_alone_does_not_raise_a_foreign_entity():
    """Both halves are required: a foreign form AND a relationship.

    An account merely NAMED for a company abroad is not a related company. The
    relationship word is what says the books hold a balance with it.
    """
    ledger = plain_company()
    ledger.accounts["6300"] = _account("6300", "Cloud Hosting Hetzner GmbH",
                                       "Expenses", "expense")
    ledger.lines.append(_line(datetime.date(YEAR, 4, 1), "6300", 900, "hosting"))
    scanned = scan(ledger, profile=_profile("Harbor"), filing_year=YEAR)
    assert "foreign-entity" not in _groups(scanned)


def test_a_relationship_alone_does_not_raise_a_foreign_entity():
    ledger = plain_company()
    ledger.accounts["1400"] = _account("1400", "Due From Crestwood Holdings Inc",
                                       "Other Assets", "intercompany")
    ledger.lines.append(_line(datetime.date(YEAR, 4, 1), "1400", 5000, "advance"))
    scanned = scan(ledger, profile=_profile("Harbor"), filing_year=YEAR)
    assert "foreign-entity" not in _groups(scanned)


def test_a_short_company_form_counts_only_as_the_last_word():
    assert readiness.foreign_form("Due From Vesterhavn Systems GmbH") == "gmbh"
    assert readiness.foreign_form("Due From Nordvind AB") == "ab"
    assert readiness.foreign_form("AB Testing Expense") == ""
    assert readiness.foreign_form("Northgate Checking 7742") == ""
    assert readiness.foreign_form("Vantage Card 3391") == ""


def test_money_sent_to_a_subsidiary_is_not_a_receipt_from_it():
    """Direction is the whole of the related-party check and it is easy to invert.

    The complicated company sends its subsidiary three payments during the
    filing year and receives nothing. An earlier draft matched the bank leg of
    those payments and called them receipts.
    """
    assert "related-party-receipts" not in _groups(_complicated())


def test_money_received_from_a_subsidiary_is_a_receipt_from_it():
    ledger = complicated_company()
    ledger.lines += [
        _line(datetime.date(YEAR, 9, 12), "101000", 60000,
              "Vesterhavn Systems GmbH", name="Vesterhavn Systems GmbH",
              txn="Deposit"),
        _line(datetime.date(YEAR, 9, 12), "174000", -60000,
              "Vesterhavn Systems GmbH", name="Vesterhavn Systems GmbH",
              txn="Deposit"),
    ]
    scanned = scan(ledger, profile=_profile("Vantage Meridian"), filing_year=YEAR)
    req = scanned.get("filing.related-party-receipts.nature")
    assert req is not None
    assert "60,000.00 arrived in 101000" in " ".join(req.evidence)


def test_a_profit_and_loss_balance_from_the_prior_year_raises_nothing_this_year():
    """The uncategorized receipt is dated 2024. A 2025 return is not about it."""
    ledger = complicated_company()
    assert any(l.date.year == 2024 for l in ledger.lines)
    scanned = scan(ledger, profile=_profile("Vantage Meridian"), filing_year=YEAR)
    assert all("Uncategorized" not in " ".join(r.evidence) for r in scanned)


def test_an_account_nobody_ever_posted_to_raises_nothing():
    ledger = plain_company()
    ledger.accounts["1900"] = _account("1900", "Cryptocurrency Wallet",
                                       "Other Current Assets", "other")
    scanned = scan(ledger, profile=_profile("Harbor"), filing_year=YEAR)
    assert "digital-assets" not in _groups(scanned), (
        "an empty template row is not a holding")


def test_a_digital_asset_account_with_a_balance_does_raise_it():
    ledger = plain_company()
    ledger.accounts["1900"] = _account("1900", "Cryptocurrency Wallet",
                                       "Other Current Assets", "other")
    ledger.lines.append(_line(datetime.date(YEAR, 4, 1), "1900", 12500, "bought"))
    scanned = scan(ledger, profile=_profile("Harbor"), filing_year=YEAR)
    assert "digital-assets" in _groups(scanned)


def _with_equipment():
    ledger = plain_company()
    ledger.accounts["1500"] = _account("1500", "Machinery and Equipment",
                                       "Fixed Assets", "other")
    ledger.accounts["1510"] = _account("1510", "Accumulated Depreciation",
                                       "Fixed Assets", "other")
    ledger.lines += [_line(datetime.date(YEAR, 3, 2), "1500", 48000, "lathe"),
                     _line(datetime.date(YEAR, 12, 31), "1510", -10400, "for the year")]
    return ledger


def test_equipment_on_the_books_raises_what_it_is_and_when_it_started():
    scanned = scan(_with_equipment(), profile=_profile("Harbor"), filing_year=YEAR)
    ids = {r.id for r in scanned.of_group("fixed-assets")}
    assert ids == {"filing.fixed-assets.schedule", "filing.fixed-assets.invoice"}
    assert "Machinery and Equipment" in scanned.get(
        "filing.fixed-assets.schedule").trigger


def test_accumulated_depreciation_is_not_a_thing_anybody_bought():
    """It is typed as a fixed asset and is a running subtotal of write-offs."""
    scanned = scan(_with_equipment(), profile=_profile("Harbor"), filing_year=YEAR)
    trigger = scanned.get("filing.fixed-assets.schedule").trigger
    assert "Accumulated Depreciation" not in trigger
    assert "1 account(s)" in trigger


def test_an_intangible_is_never_asked_about_as_though_it_were_equipment():
    """QuickBooks types a domain as a fixed asset. A domain is not a van."""
    ledger = complicated_company()
    domain = ledger.accounts["161000"]
    assert domain.type == "fixed asset" and domain.role == "intangible"
    scanned = scan(ledger, profile=_profile("Vantage Meridian"), filing_year=YEAR)
    assert "fixed-assets" not in _groups(scanned)
    assert "intangibles" in _groups(scanned)


def test_a_company_with_no_equipment_is_not_asked_about_any():
    assert "fixed-assets" not in _groups(_plain())


# ---------------------------------------------------- answers raise requirements

def test_saying_the_company_is_winding_up_raises_what_a_final_year_needs():
    before = _complicated()
    assert "wind-down" not in _groups(before)
    after = _complicated(answers={"filing.still-trading.status": {
        "answer": "We stopped trading in April and the company is being wound up.",
        "answered_on": "2026-09-10", "source": "A. Founder"}})
    ids = {r.id for r in after.of_group("wind-down")}
    assert "wind_down.target_date" in ids
    assert "wind_down.receivable_plan" in ids
    assert "filing.wind-down.certificate" in ids
    assert len(after) > len(before)


def test_the_wind_down_requirements_reuse_the_ids_the_entries_already_need():
    """One id per fact, so answering the requirement unblocks the entries too."""
    after = _complicated(answers={"filing.still-trading.status": {
        "answer": "winding down", "answered_on": "2026-09-10", "source": "A"}})
    required = ("target_date", "receivable_plan", "safe_terms", "cap_table",
                "resolution_date")
    ids = {r.id for r in after.of_group("wind-down")}
    for key in required:
        assert f"wind_down.{key}" in ids, key
    assert Profile().wind_down_ready() == (False, list(required))


def test_saying_the_company_is_still_trading_raises_nothing_further():
    after = _complicated(answers={"filing.still-trading.status": {
        "answer": "Still trading. We let our last employee go and kept selling.",
        "answered_on": "2026-09-10", "source": "A. Founder"}})
    assert "wind-down" not in _groups(after)


def test_a_prior_return_that_reported_digital_assets_raises_the_disposal_questions():
    assert "digital-assets" not in _groups(_complicated())
    after = _complicated(answers={"filing.prior-return.digital-assets": {
        "answer": "Yes, the 2024 return reported that we held them.",
        "answered_on": "2026-09-10", "source": "A. Founder"}})
    req = after.get("filing.digital-assets.activity")
    assert req is not None
    assert req.raised_by == "an answer already given"
    assert "last return filed" in req.trigger


def test_a_prior_return_that_reported_none_raises_nothing():
    after = _complicated(answers={"filing.prior-return.digital-assets": {
        "answer": "No, it said we held none.", "answered_on": "2026-09-10",
        "source": "A. Founder"}})
    assert "digital-assets" not in _groups(after)


def test_a_cap_table_naming_a_foreign_owner_raises_the_owner_questions():
    assert "foreign-owner" not in _groups(_plain())
    after = _plain(answers={"filing.ownership-roster.cap-table": {
        "answer": "Two holders, one of them a non-US resident in Portugal at 40%.",
        "answered_on": "2026-09-10", "source": "A. Founder"}})
    ids = {r.id for r in after.of_group("foreign-owner")}
    assert ids == {"filing.foreign-owner.owners",
                   "filing.foreign-owner.transactions",
                   "filing.foreign-owner.agreements"}


# --------------------------------------------------------- answered and unknown

def test_an_answer_settles_a_requirement_and_an_unknown_does_not_read_as_one():
    answers = {
        "filing.meals.split": {"answer": "About 300 of it was the team dinner.",
                               "answered_on": "2026-09-10", "source": "A. Founder"},
        "filing.contractors.w9": {
            "answer": "Cannot provide: our former bookkeeper holds the folder.",
            "answered_on": "2026-09-10", "source": "A. Founder", "unknown": True,
            "unknown_reason": "our former bookkeeper holds the folder"},
    }
    scanned = _plain(answers=answers)
    settled = scanned.get("filing.meals.split")
    unknown = scanned.get("filing.contractors.w9")
    assert settled.settled and not settled.outstanding and not settled.unknown
    assert unknown.unknown and not unknown.settled
    assert not unknown.outstanding, (
        "a stated unknown is a record, so it is not outstanding")
    assert unknown not in scanned.settled


def test_a_stated_unknown_travels_with_its_reason_and_who_said_it():
    scanned = _plain(answers={"filing.contractors.w9": {
        "answer": "Cannot provide: our former bookkeeper holds the folder.",
        "answered_on": "2026-09-10", "source": "A. Founder", "unknown": True,
        "unknown_reason": "our former bookkeeper holds the folder"}})
    text = " ".join(scanned.get("filing.contractors.w9").lines())
    assert "former bookkeeper" in text
    assert "A. Founder" in text
    assert "2026-09-10" in text
    assert "will have to chase" in text


def test_a_reason_that_states_nothing_is_refused_before_it_is_recorded():
    for empty in ("", "unknown", "n/a", "none", "?", "tbd", "idk", "no idea"):
        assert readiness.is_empty_reason(empty), empty
    assert not readiness.is_empty_reason("our former counsel holds the signed copy")


# ------------------------------------------------------------- shape and wording

def test_every_requirement_names_a_trigger_a_why_and_what_kind_it_is():
    for scanned in (_plain(), _complicated()):
        for req in scanned:
            assert req.trigger.strip(), req.id
            assert req.why.strip(), req.id
            assert req.need.strip(), req.id
            assert req.kind in (DOCUMENT, QUESTION), req.id
            assert req.group in dict(readiness.GROUPS), req.id


def test_no_requirement_wording_carries_a_long_dash():
    for scanned in (_plain(), _complicated()):
        for req in scanned:
            for text in (req.need, req.why, req.trigger):
                for dash in DASHES:
                    assert dash not in text, req.id


def test_no_requirement_pretends_it_is_optional_or_gives_advice():
    for scanned in (_plain(), _complicated()):
        for req in scanned:
            text = f"{req.need} {req.why}".lower()
            for phrase in ("if you like", "you may want to", "consider whether",
                           "we recommend", "you should probably", "optional"):
                assert phrase not in text, f"{req.id}: {phrase}"


def test_both_renderings_say_it_is_not_tax_advice():
    scanned = _complicated()
    for text in (_flat(readiness.render(scanned)),
                 _flat(readiness.render_markdown(scanned))):
        assert "none of it is tax advice" in text


def test_both_renderings_state_n_of_n():
    scanned = _complicated()
    for text in (_flat(readiness.render(scanned)),
                 _flat(readiness.render_markdown(scanned))):
        assert f"of {len(scanned.requirements)} thing(s)" in text


def test_ids_do_not_move_between_scans():
    first = [r.id for r in _complicated()]
    second = [r.id for r in _complicated()]
    assert first == second
    assert len(set(first)) == len(first), "an id appears twice"


def test_a_unique_id_suffix_resolves_and_an_ambiguous_one_does_not():
    scanned = _complicated()
    req, ambiguous = scanned.resolve("still-trading.status")
    assert req is not None and not ambiguous
    req, ambiguous = scanned.resolve("instrument")
    assert req is None and len(ambiguous) == 2, (
        "two subjects both want an instrument, so the short form must refuse")


def test_a_scan_with_no_ledger_says_so_rather_than_reporting_nothing_to_do():
    scanned = scan(None, filing_year=YEAR)
    assert not scanned.requirements
    assert scanned.notes and "no ledger" in scanned.notes[0]


def test_a_scan_with_no_filing_year_says_which_triggers_widened():
    scanned = scan(plain_company(), profile=_profile("Harbor"))
    assert any("no filing year" in n for n in scanned.notes)


if __name__ == "__main__":
    passed = 0
    for name, fn in sorted(list(globals().items())):
        if name.startswith("test_") and callable(fn):
            fn()
            passed += 1
    print(f"{passed} of {passed} readiness tests pass")
