# QuickBooks Online import formats

**This file is the most likely thing in the repo to be wrong.** Everything else
here is code we control and tests we run. This is a description of somebody
else's product, which changes without telling us, and a wrong column header
here costs a founder an afternoon.

**Maintainer: unassigned.** That is a defect, not a style. Someone has to own
re-checking this against a live QuickBooks Online company file, and until a
name is in this line, nothing in this file has been confirmed by anyone.

**How to read the markers.** Every claim carries a `verified-on:` date and a
`VERIFIED:` marker.

| Marker | What it means |
|---|---|
| `VERIFIED: no` | We wrote it down from our own experience or inference. Nobody has checked it against anything. |
| `VERIFIED: intuit-doc` | Quoted from Intuit's own help on the date shown, with the URL beside it. Intuit's help is sometimes stale or wrong, and it is not the same as watching the screen. |
| `VERIFIED: yes` | A file this repo wrote went into a live QuickBooks company and came out right. Put your name and the date on it. |

Nothing on this page is `VERIFIED: yes`.

**How to verify one properly.** Not by reading another help article. Open a
real QuickBooks Online company (a sandbox or a client's with permission), do
the import by hand with a two row file, and write down what the screen actually
said.

---

## What you can and cannot import into QuickBooks Online (US)

| What | Can you import it? | Where |
|---|---|---|
| Bank transactions (CSV) | **Yes**, on all plans | Transactions > Bank transactions > Upload transactions |
| Bank rules (xlsx) | **Yes, but only by matching a file you exported from your own company**, and it never touches transactions already in For Review | Transactions > Rules > New rule dropdown > Import rules |
| Journal entries | **No.** Type them at +New > Journal Entry, or buy a third party importer app | +New > Journal Entry |

verified-on: 2026-09-09  VERIFIED: intuit-doc

Two of those three corrected a wrong assumption this repo shipped on:

1. **Journal entries cannot be imported into the US version at all.** Intuit:
   "Although data can be imported into the US version of QuickBooks Online,
   journal entry import is currently not an option."
   <https://quickbooks.intuit.com/learn-support/en-us/reports-and-accounting/importing-journal-entries-in-quickbooks-online-us-version/00/1312709>
   verified-on: 2026-09-09  VERIFIED: intuit-doc

   It **is** available in the Canada and UK versions, on all plans. This page
   previously recorded an internal disagreement about which US plans supported
   it. The disagreement is settled and both sides of it were wrong: it is not
   an Advanced-only feature, it is a not-in-the-US feature.

2. **Bank rules never apply to transactions already sitting in For Review.**
   Intuit: "Bank rules will not retroactively apply to previous transactions."
   <https://quickbooks.intuit.com/learn-support/en-us/banking/i-have-created-rules-in-qb-online-and-i-need-to-apply-them-to-transactions-from-last-year-how-can-i-apply-rules-retroactively-just-re-categorizing/00/454978>
   verified-on: 2026-09-09  VERIFIED: intuit-doc

   Rules are for what arrives next. The existing backlog is categorised in the
   For Review tab: select the rows, **Batch actions**, **Modify selected**.

---

## 1. Bank transactions (CSV upload)

Fills a feed gap: an account that was never connected, a feed that broke, or
the window before the roughly 90 days QuickBooks pulls when you reconnect.
Uploaded rows land in the same For Review queue a live feed writes into. They
are not posted until somebody accepts them.

Works on every plan. Written by `lib/closethebooks/bank_csv.py`.

### Columns

Three column layout, which is what we write by default:

| Date | Description | Amount |
|---|---|---|
| 01/05/2026 | STRIPE TRANSFER ACME ROBOTICS | 4820.15 |
| 01/07/2026 | BOLTWORKS SUPPLY CO INVOICE 5512 | -1290.00 |

Four column layout:

| Date | Description | Credit | Debit |
|---|---|---|---|
| 01/05/2026 | STRIPE TRANSFER ACME ROBOTICS | 4820.15 | |
| 01/07/2026 | BOLTWORKS SUPPLY CO INVOICE 5512 | | 1290.00 |

- Credit is money into the account. Debit is money out. Exactly one of the two
  carries a number on any row.
  verified-on: 2026-09-09  VERIFIED: intuit-doc
- We default to three column on purpose. One signed number cannot be
  transposed; a Credit/Debit pair can, and a transposition turns every deposit
  in the file into a payment.

### What actually matters at upload time

QuickBooks walks the founder through a column mapping step, so the header text
is a convenience rather than a contract: what has to be right is the number of
columns, their order, and the date format. We still write the documented
headers so the mapping screen pre-fills correctly.
verified-on: 2026-09-09  VERIFIED: no

- Date format: we write `MM/DD/YYYY`. The mapping step asks which format the
  file uses, so a founder can correct it if their company is set to
  `DD/MM/YYYY`.
  verified-on: 2026-09-09  VERIFIED: no
- File type: `.csv`. QuickBooks also accepts `.qbo`, `.qfx` and `.ofx` from a
  bank, but this writer emits CSV only.
  verified-on: 2026-09-09  VERIFIED: no
- A row with a blank description or a zero amount is rejected. `bank_csv`
  refuses to write either one rather than let the upload fail halfway.
  verified-on: 2026-09-09  VERIFIED: no

### Limits

| Limit | Value | Enforced in code as |
|---|---|---|
| Transactions per file | 1,000 | `bank_csv.MAX_ROWS` |
| File size | 350 KB | `bank_csv.MAX_BYTES` |

verified-on: 2026-09-09  VERIFIED: intuit-doc

Both are hard refusals, not warnings, because a file QuickBooks rejects part
way through leaves a partial import behind and no clean way to tell which rows
landed. Use `bank_csv.split_for_upload(lines)`, which splits on both limits at
once: 1,000 rows of long descriptors can pass the row count and still be too
big.

### Click path

1. Left menu, **Transactions**, then **Bank transactions**.
2. Pick the account tile the file belongs to.
3. **Link account** dropdown (top right), then **Upload from file**. On a
   company with no connected accounts the screen offers **Upload transactions**
   directly.
4. Drag the CSV in, **Continue**.
5. Choose the QuickBooks account the transactions belong to.
6. Map the columns and confirm the date format, **Continue**.
7. Tick the rows to bring in, **Continue**, then **Yes**.
8. The rows are now in **For Review**. Nothing has posted. Somebody still has
   to accept each one.

verified-on: 2026-09-09  VERIFIED: no

---

## 2. Categorising the existing For Review backlog

Not an import at all, and the reason it is on this page: it is what people
reach for bank rules to do, and rules cannot do it.

A rule fires on transactions that arrive after the rule exists. The rows
already in For Review are untouched by every rule in the file. To clear them:

1. Left menu, **Transactions**, then **Bank transactions**, **For Review** tab.
2. Filter or sort so the rows you want are together.
3. Tick the checkbox on each row, or the one in the header to take the page.
4. **Batch actions** (above the table), then **Modify selected**.
5. Set the category, and the payee or class if the company uses them.
6. **Apply**, then accept the rows.

verified-on: 2026-09-09  VERIFIED: intuit-doc
<https://quickbooks.intuit.com/learn-support/en-us/banking/i-have-created-rules-in-qb-online-and-i-need-to-apply-them-to-transactions-from-last-year-how-can-i-apply-rules-retroactively-just-re-categorizing/00/454978>

Rules and this are complementary, not alternatives: batch-modify clears what is
already there, rules stop it coming back.

---

## 3. Bank rules (xlsx import)

Written by `lib/closethebooks/rules_xlsx.py`.

### The template is three columns, and two of them are not English

| Rule Name | Rule Condition | Rule Outputs |
|---|---|---|

verified-on: 2026-09-09  VERIFIED: intuit-doc
<https://quickbooks.intuit.com/learn-support/en-us/banking/bank-rules-export-to-excel/00/253987>

`Rule Condition` and `Rule Outputs` hold QuickBooks' own generated encoding,
not readable text, and nothing published says how that encoding is spelled.

**This corrects the worst assumption on the page.** Until 2026-09-09 this file
described a seven column layout, `Rule Name, Applies To, Conditions, Category,
Payee, Class, Auto-add`, with readable cells. That layout does not exist. A
file written to it either fails at import or, worse, imports rules that do not
say what we meant.

So `rules_xlsx.write_rules` has two modes:

- **With `template=`**, a rules file exported out of the user's own QuickBooks
  company, it learns the encoding by example: it reads the template's columns,
  infers the separator and which slot in each cell holds free text from the
  rows already there, and writes new rows in that same shape. It reports a
  confidence, capped at `MAX_CONFIDENCE` (0.85), because nothing from this repo
  has been round-tripped through a live import. Direction, payee, class and
  auto-add are carried over from the template's own rows, because only the
  free-text slot is understood; check those on the imported rules.
- **With no template** it refuses to write an import file and writes a
  plain-language list instead: the fields of the New rule screen, in the order
  that screen asks for them, sorted by how many transactions in the company's
  own history each rule would have caught, with a Done column to tick. Whoever
  is typing thirty rules into a web form will stop early, so the valuable ones
  go first.

To produce a template: **Transactions > Rules >** the **New rule** dropdown
arrow **> Export rules**. Two or more rules in it, or there is nothing to
learn from and `read_template` refuses.

**Auto-add is always `No`.** A rule with auto-add on does not suggest a
category, it posts the transaction on its own the moment the feed delivers it,
with nobody looking. That moves the approval from the founder to a rule an
agent mined out of the company's own past behaviour, which is the exact thing
this repo exists to prevent. `write_rules` takes an `auto_add` argument and
nothing in this repo passes `True`.

Rule names are prefixed `CTB ` so every rule this tool created can be found,
reviewed or deleted in one search of the rules list.

QuickBooks bank rules match plain text only, so a rule that matches by regular
expression cannot be exported. `rules_xlsx` refuses it rather than approximate
it, because an approximated rule categorises rows the real rule would not have
touched and the founder has no way to see the difference.

Still assumed, and still unchecked: the 5-conditions-per-rule ceiling
(`rules_xlsx.MAX_CONDITIONS`) and the 100 character rule name
(`rules_xlsx.MAX_RULE_NAME`).
verified-on: 2026-09-09  VERIFIED: no

### Click path

1. Left menu, **Transactions**, then **Rules**.
2. **New rule** dropdown arrow, then **Import rules** (or **Export rules** to
   produce a template first).
3. Browse to the `.xlsx`, **Next**.
4. Confirm the column mapping, **Next**.
5. Review the rules and the accounts they point at, **Import**.
6. QuickBooks reports how many imported. Rules that reference an account name
   it cannot find are the usual failure.

verified-on: 2026-09-09  VERIFIED: no

---

## 4. Journal entries: no import in the US

**QuickBooks Online US cannot import journal entries.** Intuit: "Although data
can be imported into the US version of QuickBooks Online, journal entry import
is currently not an option."
<https://quickbooks.intuit.com/learn-support/en-us/reports-and-accounting/importing-journal-entries-in-quickbooks-online-us-version/00/1312709>
verified-on: 2026-09-09  VERIFIED: intuit-doc

A US company has two options, and neither is a file this repo hands over:

- **Type them.** +New > Journal Entry, one entry at a time. This is what most
  companies do, and it is what `lib/closethebooks/je_worksheet.py` exists to
  make survivable.
- **Buy a third party importer app.** Then the CSV below is the input.

Journal entry import **is** available in the **Canada** and **UK** versions, on
all plans, at the gear icon > **Import data** > **Journal entries**.

### The typing worksheet (the US primary output)

Written by `lib/closethebooks/je_worksheet.py`. `write_worksheet` for xlsx,
`render_markdown` for text. It is built for the person typing, not for the
person who drafted the entries:

- one block per entry, asking for journal date, then journal no., then the line
  rows, in the same order the Journal Entry screen asks for them;
- a running debit and credit total on every line and a bold `MUST EQUAL` line
  under each block, to catch a transposition before the entry is saved rather
  than in next month's trial balance;
- a tick column, because with forty entries the real failure is losing your
  place, and a half entered batch with no record of which half is worse than
  not starting;
- identical repeating entries grouped together with a note to type the first
  and then use **Make recurring**, which is the biggest time saver on the
  screen and most owners have never noticed it;
- a count and a time estimate at the top, so someone can decide whether to
  start rather than finding out forty minutes in.

`je_csv.write_entries` defaults to `region="US"` and writes this worksheet
beside the CSV, plus a companion note saying the CSV cannot be imported in the
US. Nothing in this repo hands over a file the recipient cannot use without
saying so on the file.

### Click path for typing one entry

1. **+ New** (top left), then under Other, **Journal entry**.
2. **Journal date**, then **Journal no.** (top right).
3. For each line: **Account** (type to search, pick the exact full name),
   **Debits** or **Credits**, **Description**, and **Name** if the line needs a
   customer, vendor or employee.
4. Check the totals at the bottom. They must be equal.
5. **Save**, or **Make recurring** at the bottom of the screen for the first of
   a repeating set, then use that template for the rest.

verified-on: 2026-09-09  VERIFIED: no

### The CSV, for CA, UK and third party importers

Written by `lib/closethebooks/je_csv.py`.

| Journal No. | Journal Date | Account Name | Debits | Credits | Description | Name | Class |
|---|---|---|---|---|---|---|---|
| JE-1001 | 01/31/2026 | Operating Expenses:Software Subscriptions | 249.00 | | Gearhouse Cloud, January [batch-01] | | |
| JE-1001 | 01/31/2026 | Current Assets:Mercury Checking (4015) | | 249.00 | Gearhouse Cloud, January [batch-01] | | |

verified-on: 2026-09-09  VERIFIED: no

- One row per line. **The Journal No. repeats across every line of one entry**
  and is how QuickBooks groups them, so two different entries sharing a number
  merge into one. `je_csv` refuses duplicate numbers.
- **Account Name must be the full name exactly as QuickBooks renders it**,
  parent and child joined by a colon. A near miss does not fail loudly:
  QuickBooks offers to create a new account with that name, and a founder
  clicking through an import wizard says yes. This is why `write_entries`
  takes a `resolve` callback and raises `UnresolvedAccount` listing every key
  it could not turn into a real account name. The worksheet raises the same
  way, because a wrong name typed by hand creates the same new account.
- Exactly one of Debits and Credits carries a number on each row. The other is
  blank, not zero.
- `Name` is a customer, vendor or employee that already exists in the file.
  `Class` is only usable if class tracking is switched on, which is Plus and
  Advanced only.
  verified-on: 2026-09-09  VERIFIED: no

### Every description carries its batch tag

Every Description ends with `[batch-<tag>]`, matching the batch in the file
name and in the approval record, in the CSV and in the worksheet alike. It is
there so that a founder who wants the whole batch gone can search that tag in
QuickBooks, see exactly the entries this batch created, and void them. A batch
you cannot reverse in one search is not reversible in practice.

---

## Which plans support which import

| Import | Simple Start | Essentials | Plus | Advanced |
|---|---|---|---|---|
| Bank transactions (CSV) | yes | yes | yes | yes |
| Bank rules (xlsx) | yes | yes | yes | yes |
| Journal entries (CSV), US | **no** | **no** | **no** | **no** |
| Journal entries (CSV), CA and UK | yes | yes | yes | yes |

verified-on: 2026-09-09  VERIFIED: intuit-doc

Class tracking is a separate question from plan support: the `Class` column is
only usable in Plus and Advanced, where class tracking exists at all.
verified-on: 2026-09-09  VERIFIED: no

---

## The rule that outranks all of the above

Nothing in this repo writes to QuickBooks. There is no API call, no OAuth
token, no automated posting path, by design. Every file described on this page
is something a person uploads or types themselves, after approving the batch in
their own terminal. If a future change to this repo makes that untrue, this
page is wrong in a way that matters more than a column header.

## Related files

- `lib/closethebooks/bank_csv.py`
- `lib/closethebooks/je_csv.py`
- `lib/closethebooks/je_worksheet.py`
- `lib/closethebooks/rules_xlsx.py`
- `lib/closethebooks/review_workbook.py`
- `lib/closethebooks/approval.py`
- `tests/test_writers.py`
