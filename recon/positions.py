"""Position reconciliation: roll start-of-day forward and compare with the clearer's EOD.

Expected internal EOD = SOD position + today's internal trades. Internal lifecycle
bookings (exercises, splits) would also be added here; none are booked in this
synthetic data, which is why the split and the expiry show up as breaks.
"""
from pathlib import Path

import duckdb
import pandas as pd

from recon.common import signed_qty, to_frame

PAIR = ["account", "instrument_id"]
OUT_COLS = PAIR + ["sod_qty", "trade_qty", "expected_qty", "clearer_qty", "difference"]
SQL_FILE = Path(__file__).with_name("positions.sql")

# classification -> (suggested_cause, suggested_action)
CAUSES = {
    "POSITION_CORPORATE_ACTION": ("Stock split effective today applied by the clearer but not in internal positions.",
                                  "Check the corporate action notice, then book the split internally "
                                  "(quantity up, price down by the ratio)."),
    "POSITION_EXPIRY": ("Option expired in the money today: the clearer closed it and delivered the underlying.",
                        "Book the exercise or assignment internally: close the option and book the shares at the strike."),
    "POSITION_TRADE_BREAK": ("Open trade breaks on this account and instrument explain the difference.",
                             "Resolve the trade breaks first; the position break should clear when they are fixed."),
    "POSITION_UNKNOWN": ("No trade break or lifecycle event explains the difference.",
                         "Investigate: compare trade by trade with the clearer and check for transfers or late trades."),
}


def roll_forward(sod, trades, clearer_eod):
    """pandas version. Return one row per account/instrument where expected EOD != clearer EOD."""
    flows = trades.assign(trade_qty=signed_qty(trades)).groupby(PAIR, as_index=False)["trade_qty"].sum()
    df = (sod.rename(columns={"qty": "sod_qty"})
          .merge(flows, on=PAIR, how="outer")
          .merge(clearer_eod.rename(columns={"qty": "clearer_qty"}), on=PAIR, how="outer"))
    # A pair missing from one file has a position of zero there.
    df[["sod_qty", "trade_qty", "clearer_qty"]] = df[["sod_qty", "trade_qty", "clearer_qty"]].fillna(0).astype(int)
    df["expected_qty"] = df["sod_qty"] + df["trade_qty"]
    df["difference"] = df["clearer_qty"] - df["expected_qty"]
    df = df[df["difference"] != 0]
    return df[OUT_COLS].sort_values(PAIR).reset_index(drop=True)


def roll_forward_sql(sod, trades, clearer_eod):
    """Same roll-forward, written in SQL (positions.sql) and run through DuckDB."""
    con = duckdb.connect()
    for name, df in [("sod", sod), ("trades", trades), ("clearer_eod", clearer_eod)]:
        con.register(name, df)
    return con.execute(SQL_FILE.read_text()).df()


def expected_deliveries(diffs, ref, run_date):
    """Shares we expect to move when an option expiring today is closed at the clearer in the money.

    Works for long (exercise) and short (assignment) positions: a long call receives shares,
    a long put delivers them, and short positions the opposite."""
    inst = ref.set_index("instrument_id")
    deliveries = {}
    for r in diffs.itertuples():
        o = inst.loc[r.instrument_id]
        if o.instrument_type != "option" or o.expiry_date != run_date or r.clearer_qty != 0:
            continue
        spot = inst.loc[o.underlying, "close_price"]
        in_the_money = spot > o.strike if o.option_type == "C" else spot < o.strike
        if in_the_money:
            shares = r.expected_qty * o.multiplier * (1 if o.option_type == "C" else -1)
            deliveries[(r.account, o.underlying)] = deliveries.get((r.account, o.underlying), 0) + shares
    return deliveries


def classify(diffs, ref, trade_breaks, run_date):
    """Give each position difference its most likely cause, checked in order."""
    inst = ref.set_index("instrument_id")
    touched = set(zip(trade_breaks["account"], trade_breaks["instrument_id"]))
    # A trade booked to the wrong account also moves the position of the account it belongs to.
    wrong_acct = trade_breaks[trade_breaks["break_type"] == "ACCOUNT_MISMATCH"]
    touched |= set(zip(wrong_acct["clearer_value"], wrong_acct["instrument_id"]))
    deliveries = expected_deliveries(diffs, ref, run_date)

    rows = []
    for r in diffs.itertuples():
        i = inst.loc[r.instrument_id]
        if i.split_ex_date == run_date and r.difference == r.sod_qty * (i.split_ratio - 1):
            kind = "POSITION_CORPORATE_ACTION"   # clearer scaled the opening holding by the split ratio
        elif i.instrument_type == "option" and i.expiry_date == run_date and r.clearer_qty == 0:
            kind = "POSITION_EXPIRY"             # the option itself was closed at expiry
        elif deliveries.get((r.account, r.instrument_id)) == r.difference:
            kind = "POSITION_EXPIRY"             # the shares delivered by that exercise
        elif (r.account, r.instrument_id) in touched:
            kind = "POSITION_TRADE_BREAK"
        else:
            kind = "POSITION_UNKNOWN"
        cause, action = CAUSES[kind]
        rows.append(dict(rec="POSITION", break_type=kind, account=r.account, instrument_id=r.instrument_id,
                         currency=i.currency, ref="", internal_value=r.expected_qty, clearer_value=r.clearer_qty,
                         difference=r.difference,
                         # Size of the break in money: quantity x close price x contract multiplier.
                         amount=round(abs(r.difference) * i.close_price * i.multiplier, 2),
                         suggested_cause=cause, suggested_action=action))
    return to_frame(rows)


def reconcile_positions(sod, trades, clearer_eod, ref, trade_breaks, run_date):
    return classify(roll_forward(sod, trades, clearer_eod), ref, trade_breaks, run_date)
