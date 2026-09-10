"""Evidence ledger tests. Synthetic fixtures only.

"Acme Robotics Inc." is invented and every figure here was made up for this file.

The last test proves the point of the whole module: it imports a real deliverable
gate and runs it over a ledger this code generated. The gate lives outside this
repository (it belongs to whoever is using the engine), so the test takes its
path from the CLOSE_THE_BOOKS_GATE environment variable and reports itself as
skipped when that is not set. `verify()` runs the same structural checks with no
gate at all, and it is tested unconditionally.

    python3 tests/test_evidence.py
    CLOSE_THE_BOOKS_GATE=/path/to/client-deliverable-gate.py python3 tests/test_evidence.py
"""

from __future__ import annotations

import importlib.util
import json
import os
import re
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "lib"))

from closethebooks.evidence import Evidence, EvidenceError, verify         # noqa: E402

COMPANY = "Acme Robotics Inc."
PERIOD = "FY2026"

PULLED = "2026-07-01T14:05:00Z"
WRITTEN = "2026-07-01T09:12:00Z"

DELIVERABLE_HTML = """<html><body>
<h1>Acme Robotics Inc., financial statements</h1>
<p>Revenue for the period was <b>{revenue}</b> against operating expenses of
{expenses}, a net result of {net}. The balance sheet balances at {balance}.</p>
<table>
  <tr><td>Operating bank</td><td>{bank_book}</td><td>{bank_statement}</td><td>0.00</td></tr>
  <tr><td>Company card</td><td>{card_book}</td><td>{card_statement}</td><td>{card_difference}</td></tr>
</table>
<p>Open item: the March card statement is missing, so the {card_difference}
difference on the card above is unverified.</p>
</body></html>
"""

FIGURES = {
    "revenue": "124,300.55",
    "expenses": "88,200.10",
    "net": "36,100.45",
    "balance": "812,004.11",
    "bank_book": "41,002.13",
    "bank_statement": "41,002.13",
    "card_book": "-2,150.00",
    "card_statement": "-1,900.00",
    "card_difference": "-250.00",
}


def deliverable(dirpath) -> tuple:
    html = os.path.join(dirpath, "acme-fy2026.html")
    pdf = os.path.join(dirpath, "acme-fy2026.pdf")
    with open(html, "w", encoding="utf-8") as fh:
        fh.write(DELIVERABLE_HTML.format(**FIGURES))
    with open(pdf, "wb") as fh:                        # the gate checks existence and mtime
        fh.write(b"%PDF-1.4 synthetic\n")
    return html, pdf


def build(dirpath, *, truncate=False, wrong_period=False, stale=False,
          hide_difference=False, drop_open_item=False) -> Evidence:
    """A complete ledger for a deliverable, with one thing optionally broken."""
    html, pdf = deliverable(dirpath)
    ev = Evidence(COMPANY, PERIOD, html, deliverable_pdf=pdf)
    ev.ledger_written(WRITTEN)
    ev.source(
        "gl-2026h1",
        kind="General Ledger export",
        pulled_at="2026-06-30T00:00:00Z" if stale else PULLED,
        period_requested="2026-01-01 to 2026-06-30",
        period_claimed="Since June 3, 2020" if wrong_period else "January 1 - June 30, 2026",
        expected=1684,
        returned=500 if truncate else 1684,
    )
    ev.figure(FIGURES["revenue"], "Total revenue", source="gl-2026h1",
              derivation="sum of signed amounts on income accounts, sign flipped")
    ev.figure(FIGURES["expenses"], "Total operating expenses", source="gl-2026h1",
              derivation="sum of signed amounts on expense accounts")
    ev.figure(FIGURES["net"], "Net result", source="gl-2026h1",
              derivation="revenue less operating expenses")
    ev.check("balance sheet balances", lhs=FIGURES["balance"], rhs=FIGURES["balance"])
    ev.reconciliation("Operating bank 2026-06", book=FIGURES["bank_book"],
                      statement=FIGURES["bank_statement"], evidence="acme-1000-2026-06.pdf")
    ev.reconciliation("Company card 2026-03", book=FIGURES["card_book"],
                      statement="-1,900.00" if not hide_difference else FIGURES["card_book"],
                      evidence="no statement held")
    if not drop_open_item:
        ev.open_item("the March card statement is missing",
                     must_appear_in_deliverable="March card statement is missing")
    return ev


def written(dirpath, **kwargs) -> str:
    ev = build(dirpath, **kwargs)
    return ev.write(os.path.join(dirpath, "acme-fy2026.evidence.json"))


# --------------------------------------------------------------- the API

def test_a_ledger_needs_a_client_and_a_period():
    try:
        Evidence("", PERIOD)
    except EvidenceError:
        pass
    else:
        raise AssertionError("a ledger with no client must be refused")


def test_a_source_must_state_how_many_rows_should_have_come_back():
    ev = Evidence(COMPANY, PERIOD)
    try:
        ev.source("gl", period_requested="2026", period_claimed="2026", expected=100)
    except EvidenceError as exc:
        assert "returned" in str(exc)
        assert "truncation looks exactly like success" in str(exc)
    else:
        raise AssertionError("a source with no completeness must be refused")


def test_a_source_must_say_what_the_report_claims_it_covers():
    ev = Evidence(COMPANY, PERIOD)
    try:
        ev.source("gl", period_requested="2026-01-01 to 2026-06-30", expected=1, returned=1)
    except EvidenceError as exc:
        assert "period_claimed" in str(exc)
    else:
        raise AssertionError("a source with no claimed period must be refused")


def test_a_figure_cannot_cite_a_source_that_was_never_declared():
    ev = Evidence(COMPANY, PERIOD)
    try:
        ev.figure("100.00", "Revenue", source="nowhere", derivation="made up")
    except EvidenceError as exc:
        assert "not declared" in str(exc)
    else:
        raise AssertionError("a figure with an unknown source must be refused")


def test_a_figure_needs_a_derivation():
    ev = Evidence(COMPANY, PERIOD)
    ev.source("gl", period_requested="2026", period_claimed="2026", expected=1, returned=1)
    try:
        ev.figure("100.00", "Revenue", source="gl", derivation="")
    except EvidenceError as exc:
        assert "derivation" in str(exc)
    else:
        raise AssertionError("a figure with no derivation must be refused")


def test_a_check_computes_its_own_difference():
    ev = Evidence(COMPANY, PERIOD)
    row = ev.check("foots", lhs="1000.00", rhs="999.75")
    assert row["difference"] == "0.25"
    zero = ev.check("ties", lhs="1000.00", rhs="1000.00")
    assert zero["difference"] == "0.00"


def test_a_reconciliation_records_the_zero_too():
    ev = Evidence(COMPANY, PERIOD)
    row = ev.reconciliation("Operating bank 2026-06", book="41002.13", statement="41002.13")
    assert row["difference"] == "0.00"


def test_write_refuses_an_empty_ledger():
    with tempfile.TemporaryDirectory() as d:
        ev = Evidence(COMPANY, PERIOD)
        try:
            ev.write(os.path.join(d, "x.json"))
        except EvidenceError as exc:
            assert "no sources" in str(exc)
        else:
            raise AssertionError("a ledger with no sources must not be written")

        ev.source("gl", period_requested="2026", period_claimed="2026", expected=1, returned=1)
        try:
            ev.write(os.path.join(d, "x.json"))
        except EvidenceError as exc:
            assert "no figures" in str(exc)
        else:
            raise AssertionError("a ledger with no figures must not be written")


# ------------------------------------------------------------- round trip

def test_the_ledger_round_trips_in_the_shape_the_gate_reads():
    with tempfile.TemporaryDirectory() as d:
        path = written(d)
        data = json.loads(open(path, encoding="utf-8").read())

        for key in ("client", "period", "deliverable", "deliverable_pdf",
                    "last_ledger_write_at", "sources", "figures", "checks",
                    "reconciliations", "open_items"):
            assert key in data, key
        source = data["sources"][0]
        for key in ("id", "kind", "pulled_at", "period_requested", "period_claimed", "completeness"):
            assert key in source, key
        assert source["completeness"] == {"expected": 1684, "returned": 1684}
        for key in ("value", "label", "source", "derivation"):
            assert key in data["figures"][0], key
        for key in ("name", "lhs", "rhs", "difference"):
            assert key in data["checks"][0], key
        for key in ("account", "book", "statement", "difference", "evidence"):
            assert key in data["reconciliations"][0], key
        for key in ("summary", "must_appear_in_deliverable"):
            assert key in data["open_items"][0], key
        assert data["client"] == COMPANY


# ----------------------------------------------------------------- verify

def test_verify_passes_a_complete_ledger():
    with tempfile.TemporaryDirectory() as d:
        result = verify(written(d))
        assert result.ok, result.render()
        assert "RESULT: PASS" in result.render()


def test_verify_catches_a_truncated_source():
    with tempfile.TemporaryDirectory() as d:
        result = verify(written(d, truncate=True))
        assert not result.ok
        assert any("TRUNCATED, 500 rows returned of 1684" in f for f in result.failures)


def test_verify_catches_a_date_filter_that_did_not_apply():
    with tempfile.TemporaryDirectory() as d:
        result = verify(written(d, wrong_period=True))
        assert not result.ok
        assert any("the report says it covers" in f for f in result.failures)


def test_verify_catches_a_source_older_than_the_books():
    with tempfile.TemporaryDirectory() as d:
        result = verify(written(d, stale=True))
        assert not result.ok
        assert any("STALE" in f for f in result.failures)


def test_verify_catches_a_difference_the_reader_is_not_told_about():
    with tempfile.TemporaryDirectory() as d:
        # the deliverable is rewritten without the disclosure sentence
        path = written(d)
        html = os.path.join(d, "acme-fy2026.html")
        quiet = re.sub(
            r"<p>Open item:.*?</p>", "<p>Everything reconciled.</p>",
            DELIVERABLE_HTML.format(**dict(FIGURES, card_difference="")), flags=re.S)
        open(html, "w", encoding="utf-8").write(quiet)
        assert "250.00" not in quiet
        result = verify(path)
        assert not result.ok
        assert any("NOT disclosed anywhere in the deliverable" in f for f in result.failures)
        assert any("open item not disclosed to the reader" in f for f in result.failures)


def test_verify_catches_a_figure_printed_with_no_derivation():
    with tempfile.TemporaryDirectory() as d:
        path = written(d)
        html = os.path.join(d, "acme-fy2026.html")
        text = open(html, encoding="utf-8").read().replace(
            "</body>", "<p>Cash on hand is 99,999.99.</p></body>")
        open(html, "w", encoding="utf-8").write(text)
        result = verify(path)
        assert not result.ok
        assert any("99999.99" in f for f in result.failures)


def test_verify_catches_a_missing_pdf():
    with tempfile.TemporaryDirectory() as d:
        path = written(d)
        os.remove(os.path.join(d, "acme-fy2026.pdf"))
        result = verify(path)
        assert not result.ok
        assert any("deliverable_pdf does not exist" in f for f in result.failures)


# ---------------------------------------------------- the real gate, if present

def _gate_module():
    """The firm's own deliverable gate, when the caller points at one."""
    path = os.environ.get("CLOSE_THE_BOOKS_GATE", "")
    if not path or not os.path.exists(path):
        return None
    spec = importlib.util.spec_from_file_location("deliverable_gate", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_a_generated_ledger_passes_the_real_deliverable_gate():
    gate = _gate_module()
    if gate is None:
        print("       (skipped: set CLOSE_THE_BOOKS_GATE to a gate script to run this)")
        return
    with tempfile.TemporaryDirectory() as d:
        path = written(d)
        result = gate.Gate(path).run()
        assert not result.fail, "\n".join(result.fail)
        assert result.passed


def test_the_real_gate_rejects_a_ledger_this_module_also_rejects():
    gate = _gate_module()
    if gate is None:
        print("       (skipped: set CLOSE_THE_BOOKS_GATE to a gate script to run this)")
        return
    with tempfile.TemporaryDirectory() as d:
        path = written(d, truncate=True)
        result = gate.Gate(path).run()
        assert result.fail
        assert any("TRUNCATED" in f for f in result.fail)
        assert not verify(path).ok          # and the local checks agree


def _run():
    tests = [(n, o) for n, o in sorted(globals().items())
             if n.startswith("test_") and callable(o)]
    failures = 0
    for name, fn in tests:
        try:
            fn()
            print(f"  ok   {name}")
        except Exception as exc:                          # noqa: BLE001
            failures += 1
            print(f"  FAIL {name}: {type(exc).__name__}: {exc}")
    print(f"\n{len(tests) - failures} passed, {failures} failed")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(_run())
