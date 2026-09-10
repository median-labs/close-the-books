"""The approval gate: the one thing standing between a proposal and your books.

The design in one sentence: **an agent can propose, only a human can approve,
and only a human can import.**

Three locks, deliberately redundant, because each fails differently.

1. This module. Every writer refuses to emit a file into `import/` unless a
   matching approval exists whose hash still matches the review workbook.
2. `hooks/import-approval-gate.py`. A Claude Code PreToolUse hook that blocks
   the file write itself, including a shell redirect, so an agent that ignores
   the library cannot route around it.
3. QuickBooks Online. Nothing here touches your books. A file only becomes a
   transaction when you upload it yourself and accept it.

Lock 3 is the real one. Locks 1 and 2 exist so that a mistake is caught before
it reaches a file you might upload without re-reading.

WHEN THERE IS NO FILE: BROWSER MODE

Browser mode has no import file. An agent working in your own signed-in
QuickBooks session clicks a row and the row is posted, so lock 3 is gone and
the approval has to gate the act rather than the artifact.

Two things change and nothing else does. `workbook_path` also finds a `.json`
plan, because a browser batch is written out as a plan file and a markdown page
rather than a workbook, and `check_batch` asks the same question about a batch
name with no path attached. The rules are the ones above: a batch is named, its
rows are written where you can read them, you run `approve` yourself, and
editing the batch afterwards voids the approval.

`hooks/browser-write-gate.py` is lock 2 for this mode. There is no lock 3.
Read the honesty note in that file before relying on this.
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import json
import os
import re
from dataclasses import dataclass, asdict
from pathlib import Path

# Every file written into import/ must name the batch it was approved under.
# No batch tag, no import: an unlabelled file is one nobody agreed to.
BATCH_RE = re.compile(r"batch-([A-Za-z0-9_]+)", re.I)

APPROVAL_SUFFIX = ".APPROVED"
APPROVAL_VERSION = 1


class ApprovalError(Exception):
    """Raised when a write into import/ is not covered by a live approval."""


@dataclass
class Approval:
    batch: str
    workbook: str
    row_hash: str
    approved_at: str
    approved_by: str
    rows: int = 0
    version: int = APPROVAL_VERSION

    def to_json(self) -> str:
        return json.dumps(asdict(self), indent=2, sort_keys=True) + "\n"


def batch_tag(path) -> str | None:
    """The batch a filename claims to belong to, or None."""
    m = BATCH_RE.search(Path(path).name)
    return m.group(1).lower() if m else None


def workbook_hash(path) -> str:
    """Hash the decision-bearing content of a review workbook.

    Prefers `review_workbook.row_hash`, which hashes only the cells that carry a
    decision, so that opening the file and saving it without edits does not
    invalidate an approval. Falls back to hashing the whole file, which is
    stricter and never wrong, only more annoying.
    """
    if str(path).lower().endswith(".xlsx"):
        try:
            from . import review_workbook  # imported lazily: optional at gate time
            if hasattr(review_workbook, "row_hash"):
                return review_workbook.row_hash(path)
        except Exception:
            pass
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def approval_path(review_dir, batch: str) -> Path:
    return Path(review_dir) / f"batch-{batch}{APPROVAL_SUFFIX}"


def workbook_path(review_dir, batch: str) -> Path:
    """The artifact a batch was approved against.

    An import batch is reviewed as a workbook and a browser batch is reviewed as
    a plan file, so both suffixes are looked for. The order matters only when a
    batch somehow has both, and then the workbook wins because that is the older
    shape and the one every existing approval was written against.
    """
    for suffix in (".xlsx", ".json"):
        p = Path(review_dir) / f"batch-{batch}{suffix}"
        if p.exists():
            return p
    for suffix in (".xlsx", ".json"):
        for cand in sorted(Path(review_dir).glob(f"*batch-{batch}*{suffix}")):
            return cand
    return Path(review_dir) / f"batch-{batch}.xlsx"


def find_workdir(path) -> Path | None:
    """Walk up from a path to the working directory holding review/ and import/."""
    p = Path(path).resolve()
    for parent in [p] + list(p.parents):
        if (parent / "review").is_dir() and (parent / "import").is_dir():
            return parent
        if parent.name in ("import", "review") and (parent.parent / "review").is_dir():
            return parent.parent
    return None


def write_approval(review_dir, batch: str, approved_by: str, rows: int = 0) -> Approval:
    """Record a human's approval of one batch. Called ONLY by `books.py approve`.

    The hook refuses to let an agent create this file, and refuses to let an
    agent run the command that creates it. A person types it in their own
    terminal, which is the point: it is the moment a human takes responsibility.
    """
    review_dir = Path(review_dir)
    wb = workbook_path(review_dir, batch)
    if not wb.exists():
        raise ApprovalError(
            f"no review workbook for batch {batch!r} at {wb}. "
            f"Nothing to approve: generate the batch first."
        )
    ap = Approval(
        batch=batch,
        workbook=wb.name,
        row_hash=workbook_hash(wb),
        approved_at=_dt.datetime.now().astimezone().isoformat(timespec="seconds"),
        approved_by=approved_by,
        rows=rows,
    )
    approval_path(review_dir, batch).write_text(ap.to_json(), encoding="utf-8")
    return ap


def read_approval(review_dir, batch: str) -> Approval | None:
    p = approval_path(review_dir, batch)
    if not p.exists():
        return None
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return Approval(**{k: data[k] for k in data if k in Approval.__annotations__})
    except Exception as exc:
        raise ApprovalError(f"approval file {p} is unreadable: {exc}") from exc


def check(target_path, workdir=None) -> Approval:
    """Raise unless writing `target_path` into import/ is currently approved.

    Every failure mode gets its own sentence, because the person reading it is
    mid-task and needs to know which of these five things happened.
    """
    target = Path(target_path)
    batch = batch_tag(target)
    if not batch:
        raise ApprovalError(
            f"{target.name} carries no batch tag. Every import file must be named "
            f"with the batch it was approved under, for example "
            f"'rules-batch-01.xlsx', so that a person can tell what they agreed to."
        )

    wd = Path(workdir) if workdir else find_workdir(target)
    if not wd:
        raise ApprovalError(
            f"cannot locate the working directory for {target}. Expected a folder "
            f"containing both review/ and import/."
        )

    review_dir = wd / "review"
    ap = read_approval(review_dir, batch)
    if ap is None:
        raise ApprovalError(
            f"batch {batch} has not been approved. Open "
            f"{workbook_path(review_dir, batch)}, fill in the founder_decision "
            f"column, then run this in your own terminal:\n"
            f"    python3 bin/books.py approve batch-{batch}"
        )

    wb = review_dir / ap.workbook
    if not wb.exists():
        raise ApprovalError(
            f"batch {batch} was approved against {ap.workbook}, which is no longer "
            f"there. Regenerate the batch and approve it again."
        )

    current = workbook_hash(wb)
    if current != ap.row_hash:
        raise ApprovalError(
            f"batch {batch} was approved at {ap.approved_at}, but {ap.workbook} has "
            f"changed since. That approval is stale and does not cover the current "
            f"decisions. Re-run:\n"
            f"    python3 bin/books.py approve batch-{batch}"
        )
    return ap


def check_batch(review_dir, batch: str) -> Approval:
    """Raise unless `batch` is approved right now. For acts with no file.

    `check` asks whether writing a path is allowed. This asks the same question
    about a batch name, because a browser write produces no file: the act is
    clicking a row in somebody's live books, and there is nothing on disk
    afterwards to have gated.

    Same three failure modes, same wording, because a person hitting one of
    these should not have to work out which mode they were in.
    """
    batch = str(batch or "").strip().lower()
    if not batch:
        raise ApprovalError("no batch was named, so there is nothing to check.")
    review_dir = Path(review_dir)
    ap = read_approval(review_dir, batch)
    plan = workbook_path(review_dir, batch)
    if ap is None:
        # Name the page a person reads, not the machine-readable twin beside it.
        readable = plan.with_suffix(".md")
        raise ApprovalError(
            f"batch-{batch} has not been approved, so nothing may be posted for "
            f"it.\n"
            f"  Read {readable if readable.exists() else plan}, and if you agree "
            f"with it run this in your own terminal:\n"
            f"    python3 bin/books.py approve batch-{batch}"
        )
    artifact = review_dir / ap.workbook
    if not artifact.exists():
        raise ApprovalError(
            f"batch-{batch} was approved against {ap.workbook}, which is no "
            f"longer there. Nothing can be posted against an approval whose "
            f"rows cannot be read back. Rebuild the batch and approve it again."
        )
    if workbook_hash(artifact) != ap.row_hash:
        raise ApprovalError(
            f"batch-{batch} was approved at {ap.approved_at}, and {ap.workbook} "
            f"has changed since.\n"
            f"  That approval covers the rows as they were, not the rows as they "
            f"are, and the difference is exactly what nobody would notice.\n"
            f"  Read it again, then:\n"
            f"    python3 bin/books.py approve batch-{batch}"
        )
    return ap


def guard(target_path, workdir=None):
    """check(), but returns (ok, message) instead of raising. For the hook."""
    try:
        ap = check(target_path, workdir=workdir)
        return True, f"batch {ap.batch} approved by {ap.approved_by} at {ap.approved_at}"
    except ApprovalError as exc:
        return False, str(exc)
