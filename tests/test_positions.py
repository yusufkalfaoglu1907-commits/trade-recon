import pandas as pd

from recon.common import load_day
from recon.generate import generate
from recon.positions import roll_forward, roll_forward_sql

DATE = "2026-11-20"


def test_worked_example():
    # SOD 1,000; buy 300; sell 100 -> expected 1,200. Clearer shows 1,100 -> difference -100.
    sod = pd.DataFrame({"account": ["A"], "instrument_id": ["XYZ"], "qty": [1000]})
    trades = pd.DataFrame({"account": ["A", "A"], "instrument_id": ["XYZ", "XYZ"],
                           "side": ["BUY", "SELL"], "qty": [300, 100]})
    eod = pd.DataFrame({"account": ["A"], "instrument_id": ["XYZ"], "qty": [1100]})
    row = roll_forward(sod, trades, eod).iloc[0]
    assert (row.expected_qty, row.clearer_qty, row.difference) == (1200, 1100, -100)


def test_new_and_closed_positions():
    # A position opened today (no SOD) and one closed at the clearer (no EOD row) both roll correctly.
    sod = pd.DataFrame({"account": ["A"], "instrument_id": ["OLD"], "qty": [50]})
    trades = pd.DataFrame({"account": ["A"], "instrument_id": ["NEW"], "side": ["BUY"], "qty": [10]})
    eod = pd.DataFrame({"account": ["A"], "instrument_id": ["NEW"], "qty": [10]})
    out = roll_forward(sod, trades, eod)
    assert out[["instrument_id", "difference"]].values.tolist() == [["OLD", -50]]


def test_sql_and_pandas_agree(tmp_path):
    for clean in (False, True):
        generate(DATE, tmp_path / str(clean), clean=clean)
        d = load_day(tmp_path / str(clean), DATE)
        args = (d["sod_positions"], d["internal_trades"], d["clearer_eod_positions"])
        pd.testing.assert_frame_equal(roll_forward(*args), roll_forward_sql(*args), check_dtype=False)
