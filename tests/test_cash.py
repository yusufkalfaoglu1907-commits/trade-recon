import pandas as pd
import pytest

from recon.cash import expected_cash, reconcile_cash

REF = pd.DataFrame({"instrument_id": ["XYZ", "XYZ-OPT", "FUT"],
                    "instrument_type": ["equity", "option", "future"], "multiplier": [1, 100, 10]})
FX = pd.DataFrame({"account": ["A"], "sell_currency": ["EUR"], "sell_amount": [1000.0],
                   "buy_currency": ["USD"], "rate": [1.10], "buy_amount": [1100.0]})


def trades(*rows):
    return pd.DataFrame(rows, columns=["account", "instrument_id", "side", "qty", "price", "currency", "fees"])


def test_expected_cash_signs_and_multiplier():
    t = trades(("A", "XYZ", "BUY", 100, 10.0, "EUR", 1.0),      # pay 1,000
               ("A", "XYZ-OPT", "SELL", 2, 3.5, "EUR", 1.0),    # receive 3.50 x 2 x 100 = 700
               ("A", "FUT", "BUY", 1, 5000.0, "EUR", 1.2))      # futures: fees only
    out = expected_cash(t, REF).set_index("category")["amount"]
    assert out["TRADE"] == pytest.approx(-1000 + 700)
    assert out["FEE"] == pytest.approx(-3.2)


def test_fx_rate_difference_is_explained():
    stmt = pd.DataFrame({"account": ["A", "A"], "currency": ["EUR", "USD"], "category": ["FX", "FX"],
                         "amount": [-1000.0, 1090.0]})
    out = reconcile_cash(trades(), trades(), stmt, FX, REF)
    assert list(out.break_type) == ["CASH_FX_DIFF"]
    assert "1.0900" in out.suggested_cause.iloc[0] and "1.1000" in out.suggested_cause.iloc[0]


def test_statement_vs_confirmation_difference():
    t = trades(("A", "XYZ", "BUY", 100, 10.0, "EUR", 0.0))
    stmt = pd.DataFrame({"account": ["A"] * 4, "currency": ["EUR", "EUR", "EUR", "USD"],
                         "category": ["TRADE", "FEE", "FX", "FX"], "amount": [-1150.0, 0.0, -1000.0, 1100.0]})
    out = reconcile_cash(t, t, stmt, FX, REF)
    assert list(out.break_type) == ["CASH_TRADE_DIFF"]
    assert "cash statement differs" in out.suggested_cause.iloc[0]
