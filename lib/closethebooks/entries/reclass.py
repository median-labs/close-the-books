"""Reclassification: move a balance from one account to another, for a stated reason.

    from closethebooks.entries import reclass
    drafted = reclass.build(profile, [
        {"from_account": "3200", "to_account": "2400", "amount": "-250000.00",
         "reason": "the 2024 instrument is a SAFE, not equity, until it converts",
         "effective_date": "2026-06-30",
         "source": "SAFE dated 2024-03-11, section 1(a)"},
    ])

This is the module the structural fixes run through: an instrument sitting in
equity that is a liability, an opening-balance-equity mirror that has to be
cleared, an expense in the wrong account since June. It is deliberately generic
and deliberately strict.

SIGN

`amount` is the SIGNED balance being moved, debit-positive, exactly as it reads
on the trial balance. An asset or expense balance is positive. A liability,
equity or revenue balance is negative. So moving 250,000 out of an equity
account is `-250000.00`, and the module works out that this debits equity and
credits the liability. Callers never decide the sides, because deciding the
sides from a description is where this goes wrong: "move it out of equity" reads
identically for a debit and a credit balance and means the opposite entry.

WHAT IT REFUSES

    A move with no reason. A reclassification is a change to what the books SAY
    happened, and one nobody can explain is indistinguishable from an error
    being tidied out of sight. `reason` is not a memo, it is the justification,
    and it is what lands in the basis.

    A move of 0.00, or a move to the same account. Both put a row in the audit
    trail that means nothing.

    A move with no effective date. The date decides which period the correction
    lands in and therefore which financial statements change; it is never
    "today" by default.
"""

from __future__ import annotations

from ..util import ZERO, fmt, money, parse_date
from . import Drafted, DraftError, _entry, need, signed_move

__all__ = ["build", "BATCH_TAG"]

BATCH_TAG = "reclass"


def build(profile, moves, *, batch_tag=BATCH_TAG) -> Drafted:
    """One entry per declared move. Raises on the first one that is not justified."""
    entries, notes = [], []
    for i, move in enumerate(moves or [], start=1):
        where = f"move {i}"
        from_account = str(need(move, "from_account", where)).strip()
        to_account = str(need(move, "to_account", where)).strip()
        reason = str(move.get("reason") or "").strip()
        if not reason:
            raise DraftError(
                f"{where} ({from_account} -> {to_account}): no reason. A reclassification "
                f"without a stated reason is an error being tidied out of sight. Say why the "
                f"balance belongs in the other account."
            )
        date = parse_date(need(move, "effective_date", where), field=f"{where} effective_date")
        amount = money(need(move, "amount", where), f"{where} amount")
        klass = str(move.get("class") or "").strip()
        name = str(move.get("name") or "").strip()
        memo = str(move.get("memo") or f"Reclass {from_account} to {to_account}").strip()

        source = str(move.get("source") or "").strip()
        basis = reason if not source else f"{reason} (source: {source})"

        lines = signed_move(from_account, to_account, amount, memo=memo, klass=klass, name=name)
        entries.append(_entry(
            date=date,
            number=str(move.get("number") or f"RC-{date.strftime('%Y%m%d')}-{i:02d}"),
            lines=lines,
            memo=memo,
            kind="reclass",
            basis=basis,
            batch_tag=batch_tag,
        ))
        notes.append(
            f"{date.isoformat()}: {fmt(abs(amount))} moved from {from_account} to {to_account} "
            f"({'debit' if amount > ZERO else 'credit'} balance). {reason}"
        )

    return Drafted(entries, kind="reclass", notes=notes)
