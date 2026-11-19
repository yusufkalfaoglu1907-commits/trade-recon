import pandas as pd

from recon.trades import reconcile_trades

REF = pd.DataFrame({"instrument_id": ["XYZ", "XYZ-OPT"], "multiplier": [1, 100]})


def internal(*rows):
    cols = ["exec_id", "account", "instrument_id", "side", "qty", "price", "currency", "fees"]
    return pd.DataFrame(rows, columns=cols)


def clearer(*rows):
    cols = ["trade_ref", "exec_id", "account", "instrument_id", "side", "qty", "price", "currency", "fees"]
    return pd.DataFrame(rows, columns=cols)


def test_identical_trades_give_no_breaks():
    i = internal(("E1", "A", "XYZ", "BUY", 100, 10.0, "EUR", 1.0))
    c = clearer(("C1", "E1", "A", "XYZ", "BUY", 100, 10.0, "EUR", 1.0))
    assert reconcile_trades(i, c, REF).empty


def test_partial_fills_match_one_aggregated_line():
    i = internal(("E1", "A", "XYZ", "BUY", 100, 10.00, "EUR", 1.0),
                 ("E2", "A", "XYZ", "BUY", 100, 10.10, "EUR", 1.0),
                 ("E3", "A", "XYZ", "BUY", 200, 10.05, "EUR", 1.0))
    c = clearer(("C1", None, "A", "XYZ", "BUY", 400, 10.05, "EUR", 3.0))
    assert reconcile_trades(i, c, REF).empty


def test_price_tolerance():
    i = internal(("E1", "A", "XYZ", "BUY", 100, 10.000, "EUR", 1.0),
                 ("E2", "A", "XYZ", "SELL", 100, 10.000, "EUR", 1.0))
    c = clearer(("C1", "E1", "A", "XYZ", "BUY", 100, 10.003, "EUR", 1.0),    # inside 0.005
                ("C2", "E2", "A", "XYZ", "SELL", 100, 10.010, "EUR", 1.0))  # outside
    out = reconcile_trades(i, c, REF)
    assert list(out.break_type) == ["PRICE_MISMATCH"]
    assert out.ref.iloc[0] == "E2"


def test_option_amount_uses_multiplier():
    i = internal(("E1", "A", "XYZ-OPT", "BUY", 2, 3.00, "EUR", 1.0))
    c = clearer(("C1", "E1", "A", "XYZ-OPT", "BUY", 2, 3.25, "EUR", 1.0))
    out = reconcile_trades(i, c, REF)
    assert out.amount.iloc[0] == 0.25 * 2 * 100   # price diff x contracts x 100


def test_missing_both_ways_and_duplicate():
    i = internal(("E1", "A", "XYZ", "BUY", 100, 10.0, "EUR", 1.0),
                 ("E1", "A", "XYZ", "BUY", 100, 10.0, "EUR", 1.0),     # booked twice
                 ("E2", "A", "XYZ", "SELL", 50, 10.0, "EUR", 1.0))     # not at the clearer
    c = clearer(("C1", "E1", "A", "XYZ", "BUY", 100, 10.0, "EUR", 1.0),
                ("C9", "E9", "B", "XYZ", "BUY", 70, 10.0, "EUR", 1.0))  # not booked internally
    out = reconcile_trades(i, c, REF)
    assert sorted(out.break_type) == ["DUPLICATE_INTERNAL", "MISSING_AT_CLEARER", "MISSING_INTERNAL"]


def test_wrong_side_and_wrong_account():
    i = internal(("E1", "A", "XYZ", "SELL", 100, 10.0, "EUR", 1.0),
                 ("E2", "B", "XYZ", "BUY", 100, 10.0, "EUR", 1.0))
    c = clearer(("C1", "E1", "A", "XYZ", "BUY", 100, 10.0, "EUR", 1.0),
                ("C2", "E2", "A", "XYZ", "BUY", 100, 10.0, "EUR", 1.0))
    out = reconcile_trades(i, c, REF).set_index("ref")
    assert out.loc["E1", "break_type"] == "SIDE_MISMATCH"
    assert out.loc["E2", "break_type"] == "ACCOUNT_MISMATCH"
