"""QuickBooks Online bank rules: an import file only when we have a template.

A bank rule is the durable half of the work. Categorising 400 rows once is
bookkeeping; teaching QuickBooks the 30 rules behind them means the next 400
arrive already categorised, in the founder's own file, whether or not this
engine ever runs again.

RULES ARE FOR THE FUTURE. THEY NEVER TOUCH THE BACKLOG.
    Intuit: "Bank rules will not retroactively apply to previous
    transactions."
    https://quickbooks.intuit.com/learn-support/en-us/banking/i-have-created-rules-in-qb-online-and-i-need-to-apply-them-to-transactions-from-last-year-how-can-i-apply-rules-retroactively-just-re-categorizing/00/454978
    verified-on: 2026-09-09  VERIFIED: intuit-doc

    A rule fires on transactions that arrive after it exists. The 400 rows
    already sitting in For Review are not touched by any rule, however well
    written. Those get categorised in the For Review tab itself: tick the
    rows, Batch actions, Modify selected, set the category, Apply. Anyone who
    writes rules expecting the backlog to clear has just done the work twice.

WHY THIS MODULE NO LONGER SYNTHESISES AN IMPORT FILE FROM NOTHING
    The rules export/import template is three columns:
        Rule Name, Rule Condition, Rule Outputs
    https://quickbooks.intuit.com/learn-support/en-us/banking/bank-rules-export-to-excel/00/253987
    verified-on: 2026-09-09  VERIFIED: intuit-doc

    The last two hold QuickBooks' own generated encoding, not plain English.
    Nothing published says how that encoding is spelled, which means a file
    written from an assumption about it is a file that either fails at import
    or, far worse, imports rules that do not say what we meant. An earlier
    version of this module assumed a seven column layout with readable
    Conditions and Category cells. That assumption is now known to be wrong.

    So there are two honest outputs:

    WITH a template (a rules file the user exported from their OWN QuickBooks
    company: Transactions > Rules > New rule dropdown > Export rules), the
    writer learns the encoding by example. It reads the template's columns and
    infers, from the rows already in it, the separator and which slot in each
    cell carries the free text, then writes new rows in that same shape. It
    reports how confident it is and never claims certainty, because nothing
    here has been round-tripped through a live import.

    WITHOUT a template, it refuses to write an import file at all and writes a
    plain-language list instead: one row per rule, in the words of the
    QuickBooks rule screen, ordered by how many transactions in the company's
    own history each rule would have caught, so the person typing them in gets
    the valuable ones done first and can stop whenever they run out of
    patience without having wasted the effort.

AUTO-ADD IS OFF, ALWAYS
    A rule with auto-add on does not suggest a category. It posts the
    transaction, on its own, the moment the feed delivers it, with nobody
    looking. That is precisely the thing this whole design exists to prevent:
    the founder reviews, the founder approves, the founder imports. An
    auto-adding rule quietly moves the approval from the person to a rule that
    was mined by an agent from the company's own past behaviour. So the writer
    emits "No" unless a caller explicitly passes auto_add=True, and nothing in
    this repo passes it.

SECURITY
    A rule condition is built out of bank descriptor text, which is attacker
    chosen. Every string cell goes through `util.csv_safe` before it reaches
    the workbook, because openpyxl writes a leading `=` as a live formula. A
    template is somebody's own export and is read as data: values learned from
    it are re-escaped on the way out, never trusted as formatting.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

from .je_csv import UnresolvedAccount, resolve_full_name
from .util import csv_safe, norm_text, slug

# The real export/import template, from Intuit's own help page.
# verified-on: 2026-09-09  VERIFIED: intuit-doc
HEADER = ("Rule Name", "Rule Condition", "Rule Outputs")

INTUIT_RULES_EXPORT_URL = (
    "https://quickbooks.intuit.com/learn-support/en-us/banking/"
    "bank-rules-export-to-excel/00/253987"
)
INTUIT_RULES_RETRO_URL = (
    "https://quickbooks.intuit.com/learn-support/en-us/banking/"
    "i-have-created-rules-in-qb-online-and-i-need-to-apply-them-to-transactions-"
    "from-last-year-how-can-i-apply-rules-retroactively-just-re-categorizing/00/454978"
)

# The hand-entry list. Columns 1 to 7 line up with the fields of the New rule
# screen, in the order that screen asks for them, so the list can be typed
# straight down. Columns 8 and 9 are for the person doing the typing.
HAND_HEADER = (
    "Rule name", "Apply to", "Condition to type", "Categorize as", "Payee",
    "Class", "Auto-add", "Catches", "Done",
)

SHEET = "Rules"                 # the hand-entry list
INTRO_SHEET = "Start here"
TICK = "[ ]"

# Assumed, still. verified-on: 2026-09-09  VERIFIED: no
MAX_RULE_NAME = 100
MAX_CONDITIONS = 5

APPLIES_TO = {"in": "Money in", "out": "Money out", "any": "All"}

# Separators a generated encoding plausibly uses. Order matters only for
# tie-breaking; the one that splits every sampled row into the same number of
# fields wins.
_SEPARATORS = ("~", "|", "\t", ";", "::", ":", ",")

# Confidence is capped below certainty on purpose. Nothing in this module has
# been round-tripped through a live QuickBooks import, so a 1.0 would be a lie.
MAX_CONFIDENCE = 0.85


class RuleNotExportable(ValueError):
    """A rule cannot be expressed as a QuickBooks bank rule."""


class TemplateUnusable(ValueError):
    """A template cannot teach an encoding, so nothing may be inferred from it."""


@dataclass
class TemplateShape:
    """What one exported rules file taught us about the encoding."""
    path: str
    sheet: str
    columns: tuple
    rows_sampled: int
    name_column: int                    # 0-based index
    condition_column: int
    outputs_column: int
    separators: dict = field(default_factory=dict)   # column index -> separator or ""
    skeletons: dict = field(default_factory=dict)    # column index -> list of field values
    slots: dict = field(default_factory=dict)        # column index -> free-text slot index
    confidence: float = 0.0
    notes: list = field(default_factory=list)


class RulesFile(int):
    """The number of rules written, plus what kind of file this is.

    An int subclass, like `je_csv.EntryFile`: a caller counting rules keeps
    working, and one that needs to know whether the file can be imported, and
    how much to trust it, can ask.
    """

    def __new__(cls, rules, path="", kind="hand-entry", importable=False,
                template="", confidence=0.0, sheet=SHEET, hand_path="", notes=None):
        self = super().__new__(cls, int(rules))
        self.rules = int(rules)
        self.path = str(path)
        self.kind = kind
        self.importable = bool(importable)
        self.template = str(template)
        self.confidence = float(confidence)
        self.sheet = sheet
        self.hand_path = str(hand_path)
        self.notes = list(notes or [])
        return self

    def __repr__(self):
        return (f"RulesFile(rules={self.rules}, kind={self.kind!r}, "
                f"importable={self.importable}, confidence={self.confidence:.2f})")


# ------------------------------------------------------------------ naming

def rule_name(rule, taken=None) -> str:
    """A deterministic, unique, human-readable name for one rule.

    Deterministic because a founder who re-runs the engine should see the same
    rule updated rather than a second rule with a new random name sitting
    beside it. Prefixed CTB so that every rule this tool created can be found
    and deleted in one search.
    """
    base = (getattr(rule, "id", "") or "").strip()
    if not base:
        needle = (list(getattr(rule, "match_contains", ()) or [""]) or [""])[0]
        base = slug(f"{needle}-{getattr(rule, 'account', '')}", maxlen=60)
    name = f"CTB {base}"[:MAX_RULE_NAME].strip()
    if taken is None:
        return name
    if name not in taken:
        taken.add(name)
        return name
    for n in range(2, 1000):
        suffix = f" {n}"
        candidate = f"{name[:MAX_RULE_NAME - len(suffix)]}{suffix}"
        if candidate not in taken:
            taken.add(candidate)
            return candidate
    raise RuleNotExportable(f"cannot make a unique rule name from {name!r}")


def needles(rule) -> list:
    """The plain-text needles of one rule, de-duplicated, in first-seen order.

    QuickBooks bank rules match on plain text, so a rule that only has a regex
    cannot be expressed at all, by import or by hand. We refuse rather than
    approximate it: an approximated rule silently categorises rows the real
    rule would not have touched, and the founder has no way to see the
    difference.
    """
    raw = [str(n).strip() for n in (getattr(rule, "match_contains", ()) or ()) if str(n).strip()]
    if not raw:
        if getattr(rule, "match_regex", ""):
            raise RuleNotExportable(
                f"rule {getattr(rule, 'id', '?')!r} matches only by regular "
                f"expression, and QuickBooks bank rules match plain text only. "
                f"Give it match_contains needles, or keep it in the engine and "
                f"leave it out of the export."
            )
        raise RuleNotExportable(f"rule {getattr(rule, 'id', '?')!r} has nothing to match on")
    seen, uniq = set(), []
    for n in raw:
        k = norm_text(n)
        if k not in seen:
            seen.add(k)
            uniq.append(n)
    if len(uniq) > MAX_CONDITIONS:
        raise RuleNotExportable(
            f"rule {getattr(rule, 'id', '?')!r} has {len(uniq)} conditions and "
            f"QuickBooks accepts {MAX_CONDITIONS}. Split it into more than one "
            f"rule before exporting."
        )
    return uniq


def conditions_text(rule) -> str:
    """The condition in the words the QuickBooks rule screen uses."""
    return " OR ".join(f'Description contains "{n}"' for n in needles(rule))


def applies_to(rule) -> str:
    direction = (getattr(rule, "direction", "any") or "any").strip().lower()
    if direction not in APPLIES_TO:
        raise RuleNotExportable(
            f"rule {getattr(rule, 'id', '?')!r} has direction {direction!r}; "
            f"expected one of {tuple(APPLIES_TO)}"
        )
    return APPLIES_TO[direction]


def catches(rule) -> int:
    """How many rows of the company's own history this rule would have caught.

    `support` is the count the miner recorded. It is the ordering key for the
    hand-entry list: whoever is typing thirty rules into a web form is going to
    stop early, so the ones that pay must be at the top.
    """
    try:
        return int(getattr(rule, "support", 0) or 0)
    except (TypeError, ValueError):
        return 0


def ordered(rules) -> list:
    """Most valuable first, ties in the order they arrived (so re-runs match)."""
    return [r for _, r in sorted(
        ((i, r) for i, r in enumerate(rules)),
        key=lambda pair: (-catches(pair[1]), pair[0]),
    )]


# --------------------------------------------------------------- the rows

def hand_rows(rules, resolve=None, auto_add: bool = False) -> list:
    """One row per rule for the hand-entry list, most valuable first."""
    rows, taken, unresolved = [], set(), []
    for rule in ordered(rules):
        where = f"rule {getattr(rule, 'id', '?')!r}"
        account = getattr(rule, "account_full", "") or getattr(rule, "account", "")
        try:
            category = resolve_full_name(account, resolve, where)
        except UnresolvedAccount as exc:
            unresolved.append(str(exc))
            category = ""
        rows.append([
            rule_name(rule, taken),
            applies_to(rule),
            conditions_text(rule),
            category,
            str(getattr(rule, "payee", "") or "").strip(),
            str(getattr(rule, "klass", "") or "").strip(),
            "Yes" if auto_add else "No",
            catches(rule),
            TICK,
        ])
    if unresolved:
        raise UnresolvedAccount("; ".join(unresolved))
    return rows


# Kept as the old public name so a caller counting rows does not have to care
# which of the two files it is looking at.
def rule_rows(rules, resolve=None, auto_add: bool = False) -> list:
    return hand_rows(rules, resolve=resolve, auto_add=auto_add)


# ------------------------------------------------------- learning by example

def _read_table(path) -> tuple:
    """(sheet title, header, rows) from an exported rules file. xlsx or csv."""
    ext = os.path.splitext(str(path))[1].lower()
    if ext in (".csv", ".txt"):
        import csv as _csv
        with open(path, newline="", encoding="utf-8-sig") as fh:
            table = [r for r in _csv.reader(fh)]
        if not table:
            raise TemplateUnusable(f"{path}: empty file")
        return "", tuple(str(c or "").strip() for c in table[0]), table[1:]
    from openpyxl import load_workbook
    # data_only=False: QuickBooks-authored sheets store values as literal
    # formulas with no cached value, which data_only=True reads back as None.
    wb = load_workbook(path, data_only=False)
    ws = wb.worksheets[0]
    table = [[("" if c.value is None else str(c.value)) for c in row] for row in ws.iter_rows()]
    title = ws.title
    wb.close()
    if not table:
        raise TemplateUnusable(f"{path}: empty workbook")
    return title, tuple(str(c or "").strip() for c in table[0]), table[1:]


def _pick_column(columns, *words) -> int:
    for i, name in enumerate(columns):
        low = str(name).lower()
        if all(w in low for w in words):
            return i
    return -1


def _split(value: str, sep: str) -> list:
    return value.split(sep) if sep else [value]


def _learn_column(samples, notes, label) -> tuple:
    """(separator, skeleton, free-text slot) inferred from sample cells."""
    values = [v for v in samples if str(v).strip()]
    if not values:
        notes.append(f"{label}: every sampled cell was blank, nothing to learn from")
        return "", [], -1

    separator = ""
    for sep in _SEPARATORS:
        counts = {len(_split(v, sep)) for v in values}
        if len(counts) == 1 and counts.pop() > 1:
            separator = sep
            break
    if not separator:
        notes.append(
            f"{label}: no separator splits every sampled row into the same number of "
            f"fields, so the cell is treated as one field")

    fields = [_split(v, separator) for v in values]
    width = min(len(f) for f in fields)
    skeleton, varying = [], []
    for i in range(width):
        column = [f[i] for f in fields]
        skeleton.append(max(set(column), key=column.count))
        if len(set(column)) > 1:
            varying.append(i)

    if len(varying) == 1:
        slot = varying[0]
    elif varying:
        # More than one slot moves between rows. The free text is the one that
        # looks like free text: longest, and most likely to contain a space.
        def freeness(i):
            column = [f[i] for f in fields]
            return (sum(" " in c for c in column), sum(len(c) for c in column) / len(column))
        slot = max(varying, key=freeness)
        notes.append(
            f"{label}: {len(varying)} fields vary between the sampled rows, so which one "
            f"holds the text is a guess. Field {slot} was chosen because it reads most "
            f"like free text. Import a copy first and open the first rule to check.")
    else:
        slot = -1
        notes.append(
            f"{label}: every sampled row is identical, so nothing shows where the text "
            f"goes. Export a template with at least two different rules in it.")
    return separator, skeleton, slot


def read_template(path) -> TemplateShape:
    """Learn the encoding from a rules file exported out of a real company.

    The one honest way to write an importable rules file: copy the shape of a
    file QuickBooks itself produced. Export yours at Transactions > Rules >
    the New rule dropdown > Export rules, with at least two rules in it.
    """
    sheet, columns, rows = _read_table(path)
    if not columns:
        raise TemplateUnusable(f"{path}: no header row")
    body = [r for r in rows if any(str(c).strip() for c in r)]
    if not body:
        raise TemplateUnusable(
            f"{path}: the template has a header and no rules in it. An empty export "
            f"teaches the column names and nothing about how QuickBooks encodes a "
            f"condition, and that encoding is the whole reason to use a template. "
            f"Export again from a company that has at least two rules."
        )

    notes = []
    name_col = _pick_column(columns, "name")
    cond_col = _pick_column(columns, "condition")
    out_col = _pick_column(columns, "output")
    if name_col < 0:
        name_col = 0
        notes.append("no column named like a rule name; using the first column")
    if cond_col < 0:
        cond_col = 1 if len(columns) > 1 else 0
        notes.append("no column named like a condition; using the second column")
    if out_col < 0:
        out_col = 2 if len(columns) > 2 else cond_col
        notes.append("no column named like an output; using the third column")

    def cell(row, i):
        return row[i] if i < len(row) else ""

    separators, skeletons, slots = {}, {}, {}
    for idx, label in ((cond_col, "condition"), (out_col, "outputs")):
        sep, skeleton, slot = _learn_column([cell(r, idx) for r in body], notes, label)
        separators[idx], skeletons[idx], slots[idx] = sep, skeleton, slot

    confidence = 0.30
    if len(body) >= 2:
        confidence += 0.20
    else:
        notes.append("only one rule in the template; an encoding learned from a single "
                     "example is close to a guess")
    if separators.get(cond_col):
        confidence += 0.20
    if slots.get(cond_col, -1) >= 0:
        confidence += 0.15
    if slots.get(out_col, -1) >= 0:
        confidence += 0.15
    confidence = round(min(MAX_CONFIDENCE, confidence), 2)

    return TemplateShape(
        path=str(path), sheet=sheet, columns=tuple(columns), rows_sampled=len(body),
        name_column=name_col, condition_column=cond_col, outputs_column=out_col,
        separators=separators, skeletons=skeletons, slots=slots,
        confidence=confidence, notes=notes,
    )


def _encode(shape: TemplateShape, column: int, text: str) -> str:
    """Put our text into the slot the template says free text lives in."""
    skeleton = list(shape.skeletons.get(column) or [])
    slot = shape.slots.get(column, -1)
    if not skeleton:
        return text
    if slot < 0:
        # We could not see where the text goes. Refuse rather than write a cell
        # that imports as somebody else's rule.
        raise TemplateUnusable(
            f"the template does not show where the text goes in the "
            f"{'condition' if column == shape.condition_column else 'outputs'} column, so "
            f"this file cannot be written. Export a template with at least two different "
            f"rules in it."
        )
    skeleton[slot] = text
    return (shape.separators.get(column) or "").join(skeleton)


def template_rows(rules, shape: TemplateShape, resolve=None, auto_add: bool = False) -> list:
    """New rows in the template's own shape, one per rule."""
    rows, taken, unresolved = [], set(), []
    width = len(shape.columns)
    for rule in ordered(rules):
        where = f"rule {getattr(rule, 'id', '?')!r}"
        found = needles(rule)
        if len(found) > 1:
            raise RuleNotExportable(
                f"{where} has {len(found)} conditions, and the template you exported only "
                f"shows single-condition rules, so how QuickBooks encodes a second "
                f"condition is not visible. Split it into one rule per condition, or "
                f"export a template that contains a rule with two conditions."
            )
        account = getattr(rule, "account_full", "") or getattr(rule, "account", "")
        try:
            category = resolve_full_name(account, resolve, where)
        except UnresolvedAccount as exc:
            unresolved.append(str(exc))
            category = ""
        row = [""] * width
        # Columns the template holds constant are carried over as they were:
        # matching the shape includes the parts we do not understand.
        for i in range(width):
            skeleton = shape.skeletons.get(i)
            if skeleton and i not in (shape.condition_column, shape.outputs_column):
                row[i] = (shape.separators.get(i) or "").join(skeleton)
        row[shape.name_column] = rule_name(rule, taken)
        row[shape.condition_column] = _encode(shape, shape.condition_column, found[0])
        row[shape.outputs_column] = _encode(shape, shape.outputs_column, category)
        rows.append(row)
    if unresolved:
        raise UnresolvedAccount("; ".join(unresolved))
    return rows


# ----------------------------------------------------------------- writing

def _intro_lines(rules_count, kind, shape, auto_add) -> list:
    out = [
        f"{rules_count} bank rule(s) for QuickBooks Online",
        "",
        "Rules only affect transactions that arrive AFTER the rule exists. Intuit: \"Bank "
        "rules will not retroactively apply to previous transactions.\"",
        INTUIT_RULES_RETRO_URL,
        "",
        "The rows already sitting in For Review are not touched by any of these. To "
        "categorise those: Transactions > Bank transactions > For Review, tick the rows, "
        "Batch actions, Modify selected, set the category, Apply.",
        "",
    ]
    if kind == "hand-entry":
        out += [
            "This is a list to type in, not a file to import. A rules import file can only "
            "be written by copying the shape of a rules file exported out of your own "
            "QuickBooks company, because the condition and output columns hold "
            "QuickBooks' own encoding rather than plain words.",
            INTUIT_RULES_EXPORT_URL,
            "",
            "To type them: Transactions > Rules > New rule. The Rules sheet is in the "
            "order the New rule screen asks for the fields, and is sorted by how many "
            "transactions in your own history each rule would have caught. Do them from "
            "the top and stop whenever you like; the ones you skip are the ones that "
            "matter least. Tick the Done column as you go.",
            "",
            "To produce a real import file instead: Transactions > Rules > the New rule "
            "dropdown > Export rules, then re-run with that file as the template.",
        ]
    else:
        out += [
            f"An import file was written from your own exported template "
            f"({os.path.basename(shape.path)}), by copying the shape of the "
            f"{shape.rows_sampled} rule(s) already in it.",
            f"Confidence in the learned encoding: {shape.confidence:.2f} out of a capped "
            f"{MAX_CONFIDENCE}. Nothing here has been round-tripped through a live import, "
            f"so import it, then open the first rule and read it before trusting the rest.",
            "",
            "Import at Transactions > Rules > the New rule dropdown > Import rules.",
        ]
        for note in shape.notes:
            out.append(f"  - {note}")
    out += [
        "",
        f"Auto-add is {'ON, which this repo never does by default' if auto_add else 'off'}. "
        f"An auto-adding rule posts the transaction the moment the feed delivers it, with "
        f"nobody looking, which moves the approval from you to a rule an agent wrote.",
    ]
    return out


def _write_hand_workbook(path, rows, rules_count, auto_add) -> None:
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill

    wb = Workbook()
    intro = wb.active
    intro.title = INTRO_SHEET
    for i, text in enumerate(_intro_lines(rules_count, "hand-entry", None, auto_add), start=1):
        cell = intro.cell(row=i, column=1, value=csv_safe(text))
        if i == 1:
            cell.font = Font(bold=True, size=14)
        elif i == 3:
            cell.font = Font(bold=True, color="9C0006")
        cell.alignment = Alignment(vertical="center")
    intro.column_dimensions["A"].width = 120

    # The list itself is a clean table: header on row 1, one rule per row after
    # it, nothing above it. Somebody is reading across this while typing into a
    # browser, and an instruction block would push the first rule off screen.
    ws = wb.create_sheet(SHEET)
    ws.append([csv_safe(h) for h in HAND_HEADER])
    for cell in ws[1]:
        cell.font = Font(bold=True)
        cell.fill = PatternFill("solid", fgColor="DDEBF7")
    for row in rows:
        ws.append([v if isinstance(v, int) else csv_safe(v) for v in row])

    widths = (34, 12, 60, 40, 22, 18, 10, 10, 8)
    for i, width in enumerate(widths, start=1):
        ws.column_dimensions[ws.cell(row=1, column=i).column_letter].width = width
    for row in ws.iter_rows(min_row=2, min_col=3, max_col=3):
        for cell in row:
            cell.alignment = Alignment(wrap_text=True, vertical="top")
    for row in ws.iter_rows(min_row=2, min_col=9, max_col=9):
        for cell in row:
            cell.alignment = Alignment(horizontal="center")
    ws.freeze_panes = "A2"
    wb.save(path)


def render_markdown(rules, resolve=None, auto_add: bool = False) -> str:
    """The hand-entry list as text, for a terminal, a PR or a printed page."""
    rows = hand_rows(rules, resolve=resolve, auto_add=auto_add)
    out = [f"# {len(rows)} bank rule(s) to enter by hand", ""]
    for text in _intro_lines(len(rows), "hand-entry", None, auto_add)[2:]:
        out.append(text)
    out += ["", "| " + " | ".join(HAND_HEADER) + " |",
            "|" + "---|" * len(HAND_HEADER)]
    for row in rows:
        cells = [str(v).replace("|", "\\|") for v in row]
        cells[-1] = "[ ]"
        out.append("| " + " | ".join(cells) + " |")
    return "\n".join(out).rstrip() + "\n"


def _write_import_workbook(path, columns, rows, sheet_title) -> None:
    from openpyxl import Workbook
    from openpyxl.styles import Font

    wb = Workbook()
    ws = wb.active
    # One sheet only. This file is destined for an importer that we have never
    # watched read a file, so it gets nothing but the table it expects.
    ws.title = (sheet_title or SHEET)[:31]
    ws.append([csv_safe(c) for c in columns])
    for cell in ws[1]:
        cell.font = Font(bold=True)
    for row in rows:
        ws.append([csv_safe(v) for v in row])
    for i in range(1, len(columns) + 1):
        ws.column_dimensions[ws.cell(row=1, column=i).column_letter].width = 46
    wb.save(path)


def write_rules(path, rules, template=None, resolve=None, auto_add: bool = False) -> RulesFile:
    """Write the rules file. Returns a RulesFile (an int of rules written).

    With `template=` (a rules file exported out of the user's own QuickBooks
    company) this writes an import file in that file's own shape, plus a
    hand-entry list beside it as a fallback, and reports how much of the
    encoding it could actually learn.

    Without a template it refuses to write an import file, because the
    condition and output columns carry QuickBooks' generated encoding and a
    guessed encoding either fails at import or imports a rule that does not say
    what we meant. It writes the plain-language list instead, ordered so the
    rules worth typing come first.

    A `.md` path writes the plain-language list as markdown. Nothing is written
    unless every rule passes, so a refusal never leaves a half file behind.
    """
    rules = list(rules)
    rows = hand_rows(rules, resolve=resolve, auto_add=auto_add)

    if str(path).lower().endswith(".md"):
        text = render_markdown(rules, resolve=resolve, auto_add=auto_add)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text)
        return RulesFile(len(rows), path=path, kind="hand-entry", importable=False,
                         sheet="", notes=["markdown list; no import file was written"])

    if template is None:
        _write_hand_workbook(path, rows, len(rows), auto_add)
        return RulesFile(
            len(rows), path=path, kind="hand-entry", importable=False, confidence=0.0,
            sheet=SHEET,
            notes=[
                "no template, so no import file was written: the QuickBooks rules "
                "template is Rule Name / Rule Condition / Rule Outputs and the last two "
                "hold QuickBooks' own encoding, which cannot be synthesised.",
                "export your own rules (Transactions > Rules > New rule dropdown > Export "
                "rules) and pass it as template= to get an import file.",
                "rules never apply to the For Review backlog; use Batch actions > Modify "
                "selected for those.",
            ],
        )

    shape = read_template(template)
    learned = template_rows(rules, shape, resolve=resolve, auto_add=auto_add)
    _write_import_workbook(path, shape.columns, learned, shape.sheet)
    hand_path = f"{os.path.splitext(str(path))[0]}-by-hand.xlsx"
    _write_hand_workbook(hand_path, rows, len(rows), auto_add)
    return RulesFile(
        len(learned), path=path, kind="import", importable=True,
        template=str(template), confidence=shape.confidence,
        sheet=shape.sheet or SHEET, hand_path=hand_path,
        notes=shape.notes + [
            f"encoding learned from {shape.rows_sampled} rule(s) in "
            f"{os.path.basename(str(template))}; confidence {shape.confidence:.2f}, capped "
            f"at {MAX_CONFIDENCE} because no file from this repo has been round-tripped "
            f"through a live import.",
            f"import it, then open the first rule and read it before trusting the rest.",
            "only the free-text slot of each cell is understood, so Money in / Money out, "
            "payee, class and auto-add come from whatever the template's own rows held. "
            "Check those on the imported rules; the hand-entry list beside this file has "
            "what they should be.",
            f"a hand-entry list was written beside it at {os.path.basename(hand_path)} in "
            f"case the import is rejected.",
        ],
    )
