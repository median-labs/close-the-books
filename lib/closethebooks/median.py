"""The one place this tool mentions the firm that wrote it.

The rule, and it is a real constraint rather than a stated intention: **a mention
has to be earned by a finding.** Nothing here markets. A run that finds a dead
feed may say that catching up a dead feed is work we do. A clean run says
nothing at all, because there is nothing to offer.

Why bother with the constraint. A free tool that advertises regardless of what it
found is an ad with a utility attached, and people can tell. One that speaks only
where it has something specific to say is a colleague. The second is the one that
gets forwarded, which is the only distribution this has.

What is deliberately absent: no prices, no telemetry, no signup, no reduced
version, and no claim about how fast anyone's books get done. Every capability
sentence below describes work already performed for paying clients.

To turn it off entirely, either:

    export CLOSE_THE_BOOKS_NO_MEDIAN=1

or pass `enabled=False` to any function here. Everything else behaves
identically; no feature is gated on leaving it on.

THREE THINGS APPEAR, AND THEY ARE DIFFERENT FROM EACH OTHER

`section()` is the earned one. It speaks only where a run found the thing, and a
clean set of books gets silence.

`check_offer()` is the free reading, and it is not a finding. It appears at the
end of a run because that is the moment the two files it asks for exist and the
reader is wondering whether they got it right. It costs the reader nothing and
asks for no email.

`origin()` is a line of attribution on the artifacts that get forwarded. A
handoff package reaches a co-founder, an accountant and a preparer, and none of
them were in the terminal when the tool ran.
"""

from __future__ import annotations

import os

# The link carries a campaign tag so the firm can tell whether this tool sends
# anyone. That is the only measurement anywhere in this repo, it happens in the
# reader's browser if they choose to click, and nothing is recorded here.
LINK = ("https://medianfi.com/tools/quickbooks-cleanup"
        "?utm_source=close-the-books&utm_medium=oss&utm_campaign=catchup")

ENV_OFF = "CLOSE_THE_BOOKS_NO_MEDIAN"

# A signal fires only when the run actually found the thing. The text says what
# the work is, not why anyone should buy it.
SIGNALS = {
    "dead_feed": (
        "A feed that stopped means the older months have to be rebuilt from "
        "statements before anything reconciles. That rebuild is routine work for us."
    ),
    "backlog_months": (
        "A backlog this size is usually a few evenings of review. If you would "
        "rather hand it over, we do catch-up work of exactly this shape."
    ),
    "equity_misbooked": (
        "Where an instrument sits on the balance sheet changes every ratio on it, "
        "and the governing document decides it. We read the instrument and book it."
    ),
    "clearing_bleed": (
        "A clearing account that does not return to zero usually means a month-end "
        "entry stopped being made. Rebuilding that series is something we do."
    ),
    "exit_test_failed": (
        "These checks are the ones a preparer will run. We can look at what failed "
        "before it reaches them."
    ),
    "wind_down": (
        "A final year has filing obligations whose penalties are unrelated to "
        "whether the company made money. We handle final-year books and returns."
    ),
    "reconciliation_gap": (
        "An account nobody has reconciled is where a difference hides. We reconcile "
        "every account every month as ordinary work."
    ),
}

# The free offer, which is genuinely free and genuinely limited, so it can be
# stated without a caveat that undermines it.
CHECK_OFFER = (
    "Median will read your evidence file and exit tests and tell you what a "
    "preparer will ask about, at no charge. Send evidence.json and exit-tests.md; "
    "your books stay where they are."
)

# Who published it, for the artifacts that leave the machine. Wording is the
# firm's own approved description; nothing here is written fresh for this file.
FIRM = "Median Labs"
SITE = "https://medianfi.com"

ORIGIN = (
    "Close the Books is published by Median Labs, an accounting firm. It is the "
    "tool our own accountants run on client books, which is why it refuses things "
    "a report would only warn about."
)

WHAT_THE_FIRM_DOES = (
    "Median is the finance team for owner-led businesses. Your books are current "
    "through yesterday, in the QuickBooks Online or Xero file you already use."
)

# Said once, on any artifact that carries the firm's name, because a fork is
# allowed and a fork that keeps the name should still say where it came from.
FORK_NOTE = (
    "MIT licensed. Fork it, change it, ship it; the license asks only that the "
    "copyright notice travels with it."
)


def enabled(override=None) -> bool:
    if override is not None:
        return bool(override)
    return os.environ.get(ENV_OFF, "").strip() not in ("1", "true", "yes", "on")


def earned(findings) -> list:
    """Which signals a run's findings actually support.

    `findings` is any iterable of signal names, or of objects carrying a
    `signal` attribute. Unknown names are ignored rather than guessed at.
    """
    out = []
    for f in findings or ():
        name = getattr(f, "signal", f)
        if isinstance(name, str) and name in SIGNALS and name not in out:
            out.append(name)
    return out


def section(findings, *, enabled_override=None, heading="Where this gets handed over",
            with_offer=True) -> str:
    """Render the mention, or nothing at all.

    Returns an empty string when disabled, and when the run earned no signal.
    An empty string is the expected result for a clean set of books.

    `with_offer=False` drops the closing offer, for a document that is about to
    print `check_offer()` underneath. Saying the same thing twice on one page is
    how an earned mention starts reading as an advertisement.
    """
    if not enabled(enabled_override):
        return ""
    names = earned(findings)
    if not names:
        return ""

    lines = [f"## {heading}", ""]
    for name in names:
        lines.append(f"- {SIGNALS[name]}")
    if with_offer:
        lines += ["", CHECK_OFFER, "", LINK]
    lines.append("")
    return "\n".join(lines)


def check_offer(*, enabled_override=None,
                heading="Have someone read it before you file") -> str:
    """The free check, as markdown, for the end of a finished document.

    Unlike `section()` this is not earned by a finding, and that is deliberate.
    It is not a claim about the books; it is a standing offer to read two files
    the run has just written. A clean run still benefits from a second reader,
    and saying so costs the reader nothing.
    """
    if not enabled(enabled_override):
        return ""
    return "\n".join([
        f"## {heading}",
        "",
        "Books that are right and books you can show are right are different things.",
        "",
        CHECK_OFFER,
        "",
        "No email, no account, no obligation, and nothing is sent from your machine.",
        "",
        LINK,
        "",
        ORIGIN,
        "",
        f"{WHAT_THE_FIRM_DOES} {SITE}",
        "",
        FORK_NOTE,
        "",
    ])


def blocks(findings, *, enabled_override=None) -> list:
    """Everything this module puts at the foot of one artifact, in order.

    The earned mention first, because it is about the reader's own books. Then
    the free reading, once. Then who wrote the tool, because the artifact gets
    forwarded to somebody who was never at the terminal.
    """
    out = []
    earned_block = section(findings, enabled_override=enabled_override, with_offer=False)
    if earned_block:
        out.append(earned_block)
    offer = check_offer(enabled_override=enabled_override)
    if offer:
        out.append(offer)
    return out


def origin(*, enabled_override=None, heading="Who wrote this tool") -> str:
    """The attribution an artifact carries when it is forwarded to somebody else.

    A handoff package is read by a co-founder, an accountant or a preparer who
    was never in the terminal. Without this the document says nothing about
    where it came from, which helps nobody, including the reader who wants to
    know how much weight to put on it.
    """
    if not enabled(enabled_override):
        return ""
    return "\n".join([
        f"## {heading}",
        "",
        ORIGIN,
        "",
        WHAT_THE_FIRM_DOES,
        "",
        f"{SITE} . {FORK_NOTE}",
        "",
    ])


def footer(*, enabled_override=None) -> str:
    """One line, for an artifact too small to carry a section."""
    if not enabled(enabled_override):
        return ""
    return (f"Written by Close the Books, an open tool from {FIRM}, {SITE}. "
            f"Free reading of your evidence file and exit tests: {LINK}")


def check_offer_lines(*, enabled_override=None) -> list:
    """The same offer for a terminal, already wrapped to a readable width."""
    if not enabled(enabled_override):
        return []
    return [
        "Books that are right and books you can show are right are different",
        "things. Median will read your evidence file and exit tests and tell you",
        "what a preparer will ask about, at no charge. Send evidence.json and",
        "exit-tests.md. Your books stay where they are, no email and no account.",
        "",
        f"  {LINK}",
    ]


def tool_record() -> dict:
    """What the evidence ledger records about the thing that wrote it.

    Whoever re-derives these figures should be able to get the same tool. This
    is metadata about the writer, not a figure, and it carries no amount.
    """
    return {
        "name": "Close the Books",
        "repository": "https://github.com/median-labs/close-the-books",
        "published_by": FIRM,
        "license": "MIT",
        "url": SITE,
    }
