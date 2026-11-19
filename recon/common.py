"""Small helpers shared by the generator and the three reconciliations."""
from pathlib import Path

import numpy as np
import pandas as pd

# Every break from every rec uses this one schema, so all of them fit in one exceptions file.
COLUMNS = [
    "rec", "break_type", "account", "instrument_id", "currency", "ref",
    "internal_value", "clearer_value", "difference", "amount",
    "suggested_cause", "suggested_action",
]

FILES = [
    "internal_trades", "clearer_trades", "sod_positions", "clearer_eod_positions",
    "cash_statement", "reference_data", "internal_fx", "injected_breaks",
]


def load_day(data_dir, run_date):
    """Read all CSV files for one day into a dict of DataFrames."""
    day = Path(data_dir) / run_date
    return {name: pd.read_csv(day / f"{name}.csv") for name in FILES}


def signed_qty(trades):
    """A buy adds to the position and a sell reduces it."""
    return np.where(trades["side"] == "BUY", trades["qty"], -trades["qty"])


def to_frame(rows):
    """Turn a list of break dicts into a DataFrame with the shared schema."""
    return pd.DataFrame(rows, columns=COLUMNS)
