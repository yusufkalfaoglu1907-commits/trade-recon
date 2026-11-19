"""Cash reconciliation: what our books say should move, against the clearer's cash statement.

Compared per account, currency and category (TRADE, FEE, FX, EXERCISE). To explain a
difference we also work out what the clearer's own trade confirmations imply, which
splits it into two parts:
    internal  vs confirmations -> caused by trade breaks (fix those first)
    confirmations vs statement -> the clearer's cash differs from its own trades
"""
import numpy as np
import pandas as pd

from recon.common import to_frame

GROUP = ["account", "currency", "category"]
CASH_TOL = 5.00   # per account/currency/category; small rounding differences add up across many lines

ACTIONS = {
    "TRADE": "Fix the trade breaks first; raise any remaining statement difference with the clearer.",
    "FEE": "Compare with the agreed fee schedule; amend internal fees or raise with the clearer.",
    "FX": "Check the FX deal confirmation and the agreed rate; raise with the clearer if the rate is wrong.",
    "EXERCISE": "Book the exercise or assignment internally; its cash leg will then match.",
}


def expected_cash(trades, ref):
    """Cash implied by a set of trades: one TRADE and one FEE amount per account and currency."""
    t = trades.drop(columns="instrument_type", errors="ignore").merge(
        ref[["instrument_id", "instrument_type", "multiplier"]], on="instrument_id")
    sign = np.where(t["side"] == "BUY", -1, 1)   # buying pays cash out, selling brings it in
    # Consideration = price x quantity x multiplier (an option contract is on 100 shares).
    consideration = t["price"] * t["qty"] * t["multiplier"]
    # Futures: no premium is paid at trade time; cash moves via daily variation margin (not modelled).
    consideration = consideration.where(t["instrument_type"] != "future", 0)
    trade = t.assign(category="TRADE", amount=sign * consideration)
    fees = t.assign(category="FEE", amount=-t["fees"])   # fees are always a cost
    return pd.concat([trade, fees]).groupby(GROUP, as_index=False)["amount"].sum()


def booked_fx(fx):
    """Our FX bookings as cash: the sold currency goes out, the bought currency comes in."""
    sell = fx.assign(currency=fx["sell_currency"], amount=-fx["sell_amount"])
    buy = fx.assign(currency=fx["buy_currency"], amount=fx["buy_amount"])
    return pd.concat([sell, buy]).assign(category="FX")[GROUP + ["amount"]]


def explain(r, fx, tol):
    """Build a plain-English cause for one cash difference."""
    if r["category"] == "FX":
        deal = fx[(fx["account"] == r["account"]) & (fx["buy_currency"] == r["currency"])]
        if len(deal):
            rate = r["statement"] / deal["sell_amount"].iloc[0]   # rate implied by what actually arrived
            return f"FX converted at {rate:.4f}; we booked {deal['rate'].iloc[0]:.4f}."
        return "FX amount differs from the booked FX deal."
    if r["category"] == "EXERCISE":
        return "Cash leg of an option exercise or assignment not booked internally (see POSITION_EXPIRY)."
    parts = []
    from_trades = r["confirmed"] - r["internal"]
    from_statement = r["statement"] - r["confirmed"]
    if abs(from_trades) > tol:
        parts.append(f"{from_trades:,.2f} comes from open trade breaks in this account and currency.")
    if abs(from_statement) > tol:
        parts.append(f"{from_statement:,.2f}: the cash statement differs from the clearer's own trade "
                     "confirmations; check the settlement lines.")
    return " ".join(parts) or "Small differences below tolerance on each part add up past it."


def reconcile_cash(internal, clearer, statement, fx, ref, tol=CASH_TOL):
    """Return one row per account/currency/category where our cash differs from the statement."""
    ours = pd.concat([expected_cash(internal, ref), booked_fx(fx)]).rename(columns={"amount": "internal"})
    confirmed = expected_cash(clearer, ref).rename(columns={"amount": "confirmed"})
    actual = statement.groupby(GROUP, as_index=False)["amount"].sum().rename(columns={"amount": "statement"})
    df = ours.merge(actual, on=GROUP, how="outer").merge(confirmed, on=GROUP, how="left")
    df[["internal", "statement"]] = df[["internal", "statement"]].fillna(0)
    # FX and exercise lines have no trade confirmation, so the statement is the clearer's figure.
    df["confirmed"] = df["confirmed"].fillna(df["statement"])
    df["difference"] = (df["statement"] - df["internal"]).round(2)

    rows = []
    for r in df[df["difference"].abs() > tol].to_dict("records"):
        rows.append(dict(rec="CASH", break_type=f"CASH_{r['category']}_DIFF", account=r["account"],
                         instrument_id="", currency=r["currency"], ref="",
                         internal_value=round(r["internal"], 2), clearer_value=round(r["statement"], 2),
                         difference=r["difference"], amount=abs(r["difference"]),
                         suggested_cause=explain(r, fx, tol), suggested_action=ACTIONS[r["category"]]))
    return to_frame(rows)
