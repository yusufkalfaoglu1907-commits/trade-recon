"""End-to-end checks against the generator's answer key (injected_breaks.csv)."""
import pandas as pd
import pytest

from recon.generate import generate
from recon.report import reconcile_day

DATE = "2026-11-20"


@pytest.fixture(scope="module")
def break_day(tmp_path_factory):
    data = tmp_path_factory.mktemp("breaks")
    generate(DATE, data)
    key = pd.read_csv(data / DATE / "injected_breaks.csv", keep_default_na=False)
    return key, reconcile_day(data, DATE)


def found(exceptions, k):
    """Is this answer-key row matched by an exception of the same rec, type, account and instrument/currency?"""
    m = ((exceptions.rec == k.rec) & (exceptions.break_type == k.expected_break_type)
         & (exceptions.account == k.account))
    if k.instrument_id:
        m &= exceptions.instrument_id == k.instrument_id
    else:
        m &= exceptions.currency == k.currency
    return m.any()


def test_every_injected_break_is_detected_with_the_right_type(break_day):
    key, exceptions = break_day
    missed = [f"{k.break_id} {k.expected_break_type} {k.note}"
              for k in key[key.expected_break_type != "NONE"].itertuples() if not found(exceptions, k)]
    assert missed == []


def test_no_false_positives_on_trades_that_should_match(break_day):
    # In-tolerance price, partial fills and clearer lines without exec_id must give no trade exception.
    key, exceptions = break_day
    refs = " ".join(exceptions.loc[exceptions.rec == "TRADE", "ref"])
    for k in key[key.expected_break_type == "NONE"].itertuples():
        for exec_id in k.ref.split(","):
            assert exec_id not in refs, k.note


def test_trade_rec_reports_only_injected_breaks(break_day):
    key, exceptions = break_day
    trade = exceptions[exceptions.rec == "TRADE"]
    injected = key[(key.rec == "TRADE") & (key.expected_break_type != "NONE")]
    assert len(trade) == len(injected)


def test_every_position_break_gets_a_known_cause(break_day):
    _, exceptions = break_day
    assert "POSITION_UNKNOWN" not in set(exceptions.break_type)


def test_clean_day_has_zero_exceptions(tmp_path):
    generate(DATE, tmp_path, clean=True)
    assert reconcile_day(tmp_path, DATE).empty
