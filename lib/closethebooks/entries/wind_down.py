"""Wind-down entries. The least reversible thing in this repository.

    from closethebooks.entries import wind_down
    drafted = wind_down.build(profile)
    drafted.questions      # read these FIRST; an empty entry list is a valid answer

Dissolving a company settles what it is owed, extinguishes what it owes, pays
out what is left and closes the rest. Each of those rests on a fact that only
the owner has and, usually, on a document: a settlement the other side agreed
to, the instrument that says what a convertible note does on a dissolution, a
resolution authorising a distribution. None of it can be inferred from the
books, and every one of these entries is close to impossible to unwind once the
entity is gone.

So this module behaves differently from the rest of the package:

  * It refuses entirely until `profile.wind_down_ready()` is satisfied, and it
    names the exact keys that are missing.
  * Where the treatment depends on a legal instrument the profile does not
    hold, it emits a QUESTION and no entry. Not a best guess with a caveat in
    the memo: no entry at all. In this organisation a client's own signed
    agreement once contradicted the client's description of it, our fix plan and
    a third party's, and only the instrument settled it.
  * Every line carries the founder answer that authorises it in its memo, so
    that a year later the entry says who decided it and on what.

PROFILE

    "wind_down": {
      "target_date": "2026-12-31",          # required, dissolution date
      "receivable_plan": "...",             # required
      "safe_terms": "...",                  # required
      "cap_table": "...",                   # required
      "resolution_date": "2026-12-15",      # required
      "accounts": {"bank": "1000", "retained_earnings": "3400",
                   "write_off": "6900", "gain_on_extinguishment": "4900"},
      "intercompany": {
        "account": "1500", "book_balance": "50000.00", "cash_expected": "20000.00",
        "authority": "founder answer WD-2, 2026-11-01: settle at 40 cents"},
      "instruments": [
        {"name": "2024 SAFE, Northwind", "account": "2400",
         "book_balance": "-250000.00", "cash_paid": "50000.00",
         "instrument_on_file": "SAFE dated 2024-03-11, section 3(b), in the data room",
         "authority": "founder answer WD-4, 2026-11-03"}],
      "distributions": [
        {"account": "3100", "amount": "75000.00", "to": "common holders",
         "authority": "board resolution 2026-12-15"}],
      "closing": [
        {"account": "3300", "balance": "-12000.00", "to": "3400",
         "authority": "founder answer WD-7, 2026-12-20"}]
    }
"""

from __future__ import annotations

from ..util import ZERO, fmt, money, parse_date
from . import Drafted, DraftError, Question, _entry, need, signed_move

__all__ = ["build", "BATCH_TAG"]

BATCH_TAG = "wind_down"

_HEADLINE = (
    "Wind-down entries are close to irreversible. Read every question below, and "
    "have the instrument in front of you for every instrument entry, before any of "
    "this is imported."
)


def _accounts(wind_down):
    return wind_down.get("accounts") or {}


def build(profile, *, date=None, batch_tag=BATCH_TAG) -> Drafted:
    """Draft what can be drafted; ask about everything else.

    Raises `DraftError` only when the engagement facts themselves are missing.
    Once past that, a missing document produces a `Question`, never an entry.
    """
    ready, missing = profile.wind_down_ready()
    if not ready:
        raise DraftError(
            "wind-down entries are refused: the profile has not answered "
            + ", ".join(missing)
            + ".\nThese are not paperwork. target_date decides the period, receivable_plan "
            "and safe_terms decide the treatment, cap_table decides who is paid, and "
            "resolution_date is the authority the entries rest on. Answer them with "
            "`profile.answer(...)` or in the wind_down block, then run this again."
        )

    wd = profile.wind_down
    accounts = _accounts(wd)
    effective = parse_date(date or wd.get("target_date"), field="wind-down date")
    resolution = str(wd.get("resolution_date") or "").strip()
    bank = accounts.get("bank")

    entries, questions, notes = [], [], [_HEADLINE]
    seq = 0

    def number():
        nonlocal seq
        seq += 1
        return f"WD-{seq:02d}"

    def ask(qid, text, needs, blocks):
        questions.append(Question(id=qid, text=text, needs=needs, blocks=blocks, kind="wind_down"))

    # ------------------------------------------------- 1. intercompany receivable
    inter = wd.get("intercompany") or {}
    if inter:
        where = "wind_down.intercompany"
        account = need(inter, "account", where)
        book = money(need(inter, "book_balance", where), f"{where} book_balance")
        authority = str(inter.get("authority") or "").strip()
        cash = inter.get("cash_expected", inter.get("cash_received"))
        write_off = inter.get("write_off_account") or accounts.get("write_off")

        if not authority:
            ask("WD-Q-INTERCO-AUTH",
                f"What settles the intercompany receivable of {fmt(book)} in account {account}, "
                f"and who decided it?",
                needs="the founder answer recorded in wind_down.intercompany.authority, naming the "
                      "amount agreed and the date",
                blocks="settlement or write-off of the intercompany receivable")
        elif cash in (None, ""):
            ask("WD-Q-INTERCO-CASH",
                f"How much cash will actually be received against the {fmt(book)} intercompany "
                f"receivable?",
                needs="wind_down.intercompany.cash_expected, evidenced by the settlement the other "
                      "entity agreed to",
                blocks="settlement or write-off of the intercompany receivable")
        else:
            cash = money(cash, f"{where} cash_expected")
            if book <= ZERO:
                raise DraftError(
                    f"{where}: book_balance is {fmt(book)}. A receivable is a debit balance; a "
                    f"credit balance here is a payable and is not what this entry settles."
                )
            shortfall = money(book - cash)
            if shortfall != ZERO and not write_off:
                ask("WD-Q-INTERCO-WRITEOFF",
                    f"The intercompany receivable is {fmt(book)} and only {fmt(cash)} is coming back. "
                    f"Where does the {fmt(shortfall)} shortfall go?",
                    needs="wind_down.accounts.write_off, and whether the shortfall is a bad debt "
                          "expense or a distribution to the other entity's owner, which are taxed "
                          "differently",
                    blocks="settlement of the intercompany receivable")
            elif shortfall < ZERO:
                raise DraftError(
                    f"{where}: cash_expected {fmt(cash)} exceeds the book balance {fmt(book)} by "
                    f"{fmt(-shortfall)}. That is not a settlement; find what the extra is before booking it."
                )
            else:
                memo = f"Settle intercompany receivable per {authority}"
                lines = []
                if cash != ZERO:
                    if not bank:
                        raise DraftError(f"{where}: cash of {fmt(cash)} with no wind_down.accounts.bank declared")
                    lines.append((bank, cash, ZERO, memo, "", ""))
                if shortfall != ZERO:
                    lines.append((write_off, shortfall, ZERO,
                                  f"Write off uncollectible balance per {authority}", "", ""))
                lines.append((account, ZERO, book, memo, "", ""))
                entries.append(_entry(effective, number(), lines, memo, "wind_down",
                                      f"founder answer: {authority}", batch_tag))

    # --------------------------------------------- 2. convertible instruments
    for i, instrument in enumerate(wd.get("instruments") or [], start=1):
        where = f"wind_down.instruments[{i - 1}]"
        label = str(need(instrument, "name", where)).strip()
        account = need(instrument, "account", where)
        book = money(need(instrument, "book_balance", where), f"{where} book_balance")
        authority = str(instrument.get("authority") or "").strip()
        on_file = str(instrument.get("instrument_on_file") or "").strip()
        cash_paid = instrument.get("cash_paid")
        gain_account = instrument.get("gain_account") or accounts.get("gain_on_extinguishment")

        if not on_file:
            ask(f"WD-Q-INSTR-{i}-DOC",
                f"What does the {label} instrument say happens on a dissolution: a return of the "
                f"purchase amount, a liquidation preference, or nothing?",
                needs=f"the executed {label} document itself, cited in {where}.instrument_on_file. "
                      f"The books, the cap table summary and anyone's recollection are all "
                      f"descriptions of it and none of them governs.",
                blocks=f"extinguishment of {label} ({fmt(book)})")
            continue
        if not authority:
            ask(f"WD-Q-INSTR-{i}-AUTH",
                f"Who authorised the settlement of {label} at {fmt(book)}, and on what date?",
                needs=f"{where}.authority, naming the founder answer or the resolution",
                blocks=f"extinguishment of {label}")
            continue
        if cash_paid in (None, ""):
            ask(f"WD-Q-INSTR-{i}-CASH",
                f"How much cash is actually being paid to extinguish {label}?",
                needs=f"{where}.cash_paid, evidenced by the payment or the settlement agreement. "
                      f"Zero is a legitimate answer and must be stated as 0.00 rather than left blank.",
                blocks=f"extinguishment of {label}")
            continue

        cash_paid = money(cash_paid, f"{where} cash_paid")
        carrying = -book if book < ZERO else book
        if book > ZERO:
            raise DraftError(
                f"{where}: book_balance is {fmt(book)}, a debit balance. An instrument the company "
                f"OWES carries a credit balance; check the sign before extinguishing it."
            )
        difference = money(carrying - cash_paid)
        if difference != ZERO and not gain_account:
            ask(f"WD-Q-INSTR-{i}-GAIN",
                f"{label} carries {fmt(carrying)} and is being settled for {fmt(cash_paid)}. Where "
                f"does the {fmt(difference)} difference go?",
                needs="wind_down.accounts.gain_on_extinguishment, and confirmation from the preparer "
                      "of whether the difference is taxable income to the company",
                blocks=f"extinguishment of {label}")
            continue

        memo = f"Extinguish {label} per {authority}"
        lines = [(account, carrying, ZERO, memo, "", "")]
        if cash_paid != ZERO:
            if not bank:
                raise DraftError(f"{where}: cash_paid of {fmt(cash_paid)} with no wind_down.accounts.bank declared")
            lines.append((bank, ZERO, cash_paid, memo, "", ""))
        if difference > ZERO:
            lines.append((gain_account, ZERO, difference,
                          f"Gain on extinguishment of {label} per {authority}", "", ""))
        elif difference < ZERO:
            lines.append((gain_account, -difference, ZERO,
                          f"Loss on extinguishment of {label} per {authority}", "", ""))
        entries.append(_entry(effective, number(), lines, memo, "wind_down",
                              f"founder answer: {authority}; instrument: {on_file}", batch_tag))

    # --------------------------------------------------- 3. final distributions
    for i, dist in enumerate(wd.get("distributions") or [], start=1):
        where = f"wind_down.distributions[{i - 1}]"
        account = need(dist, "account", where)
        amount = money(need(dist, "amount", where), f"{where} amount")
        to = str(dist.get("to") or "").strip()
        authority = str(dist.get("authority") or "").strip()
        if not authority:
            ask(f"WD-Q-DIST-{i}",
                f"What authorises the final distribution of {fmt(amount)}{' to ' + to if to else ''}?",
                needs=f"{where}.authority, naming the board or member resolution and its date. A "
                      f"distribution made before creditors are settled can be clawed back from the "
                      f"people who received it.",
                blocks="the final distribution")
            continue
        if amount <= ZERO:
            raise DraftError(f"{where}: amount is {fmt(amount)}; a distribution is a positive amount")
        if not bank:
            raise DraftError(f"{where}: no wind_down.accounts.bank declared to pay the distribution from")
        memo = f"Final distribution{' to ' + to if to else ''} per {authority}"
        entries.append(_entry(effective, number(),
                              [(account, amount, ZERO, memo, to, ""),
                               (bank, ZERO, amount, memo, to, "")],
                              memo, "wind_down", f"founder answer: {authority}", batch_tag))

    # ------------------------------------------------ 4. close what is left
    default_to = accounts.get("retained_earnings")
    for i, row in enumerate(wd.get("closing") or [], start=1):
        where = f"wind_down.closing[{i - 1}]"
        account = need(row, "account", where)
        balance = money(need(row, "balance", where), f"{where} balance")
        destination = row.get("to") or default_to
        authority = str(row.get("authority") or "").strip() or (
            f"dissolution resolution dated {resolution}" if resolution else ""
        )
        if not destination:
            ask(f"WD-Q-CLOSE-{i}",
                f"Where does the remaining {fmt(balance)} in account {account} close to?",
                needs="wind_down.accounts.retained_earnings, or a per-row \"to\" account",
                blocks=f"closing account {account}")
            continue
        if not authority:
            ask(f"WD-Q-CLOSE-{i}-AUTH",
                f"What authorises closing {fmt(balance)} out of account {account}?",
                needs=f"{where}.authority or wind_down.resolution_date",
                blocks=f"closing account {account}")
            continue
        memo = f"Close account {account} on dissolution per {authority}"
        entries.append(_entry(effective, number(),
                              signed_move(account, destination, balance, memo=memo),
                              memo, "wind_down", f"founder answer: {authority}", batch_tag))

    if questions:
        notes.append(
            f"{len(questions)} wind-down question(s) unanswered, so {len(questions)} treatment(s) were "
            f"NOT drafted. The batch below is incomplete by design."
        )
    return Drafted(entries, kind="wind_down", questions=questions, notes=notes)
