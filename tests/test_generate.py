import pandas as pd

from recon.common import FILES
from recon.generate import generate

DATE = "2026-11-20"


def test_same_seed_gives_identical_files(tmp_path):
    a = generate(DATE, tmp_path / "a")
    b = generate(DATE, tmp_path / "b")
    for name in FILES:
        assert (a / f"{name}.csv").read_text() == (b / f"{name}.csv").read_text(), name


def test_universe_and_trade_count(tmp_path):
    day = generate(DATE, tmp_path)
    ref = pd.read_csv(day / "reference_data.csv")
    trades = pd.read_csv(day / "internal_trades.csv")
    assert ref.instrument_type.value_counts().to_dict() == {"equity": 8, "option": 6, "future": 2}
    assert set(ref.currency) == {"EUR", "USD"}
    assert set(trades.account) == {"ACC-A", "ACC-B"}
    assert 150 <= len(trades) <= 250


def test_answer_key_lists_every_break_type(tmp_path):
    key = pd.read_csv(generate(DATE, tmp_path) / "injected_breaks.csv")
    expected = {"NONE", "MISSING_AT_CLEARER", "MISSING_INTERNAL", "QTY_MISMATCH", "PRICE_MISMATCH",
                "FEE_MISMATCH", "DUPLICATE_INTERNAL", "SIDE_MISMATCH", "ACCOUNT_MISMATCH",
                "POSITION_CORPORATE_ACTION", "POSITION_EXPIRY", "CASH_EXERCISE_DIFF",
                "CASH_TRADE_DIFF", "CASH_FX_DIFF"}
    assert set(key.expected_break_type) == expected


def test_clean_day_has_no_injected_breaks(tmp_path):
    key = pd.read_csv(generate(DATE, tmp_path, clean=True) / "injected_breaks.csv")
    assert set(key.expected_break_type) == {"NONE"}
