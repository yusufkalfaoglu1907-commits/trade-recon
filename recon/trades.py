"""Trade reconciliation: the internal blotter against the clearer's trade file.

Step 1 matches on exec_id where both sides have it. Step 2 matches what is left
on (account, instrument, side) with quantities summed, which handles a clearer
that reports several partial fills as one line.
"""
import pandas as pd

from recon.common import to_frame

KEY = ["account", "instrument_id", "side"]
PRICE_TOL = 0.005   # absolute, per unit: absorbs rounding to fewer decimals at the clearer
FEE_TOL = 0.01

# break_type -> (suggested_cause, suggested_action)
CAUSES = {
    "DUPLICATE_INTERNAL": ("Same execution booked twice internally.",
                           "Confirm with the execution report and cancel the duplicate booking."),
    "MISSING_AT_CLEARER": ("Booked internally but not reported by the clearer: late or failed give-up, "
                           "or an internal booking error.",
                           "Check the venue execution report. If the fill is real, chase the clearer; "
                           "if not, cancel the internal booking."),
    "MISSING_INTERNAL": ("Reported by the clearer but not booked internally: missed booking, "
                         "or a trade that is not ours.",
                         "Confirm with the trader and the venue; book it internally or dispute it with the clearer."),
    "ACCOUNT_MISMATCH": ("Trade booked to a different account internally.",
                         "Check the allocation; amend the internal booking to the right account."),
    "SIDE_MISMATCH": ("Buy and sell reversed on one side.",
                      "Check the booking against the execution report; amend the internal trade."),
    "QTY_MISMATCH": ("Quantity differs: fat-finger booking, a fill not yet booked, or a partial allocation.",
                     "Check the booking against the execution report; amend the internal trade."),
    "PRICE_MISMATCH": ("Price differs by more than the tolerance: wrong price booked, or a price correction.",
                       "Check the execution report price; amend the internal trade or dispute with the clearer."),
    "FEE_MISMATCH": ("Fees differ: fee schedule out of date on one side.",
                     "Compare with the agreed fee schedule; amend internal fees or raise with the clearer."),
}


def make_break(break_type, r, internal, clearer, difference, amount):
    cause, action = CAUSES[break_type]
    return dict(rec="TRADE", break_type=break_type, account=r["account"], instrument_id=r["instrument_id"],
                currency=r["currency"], ref=r["ref"], internal_value=internal, clearer_value=clearer,
                difference=difference, amount=round(abs(amount), 2),
                suggested_cause=cause, suggested_action=action)


def find_duplicates(internal, mult):
    """An exec_id must appear once. Extra copies are breaks and are removed before matching."""
    dup = internal.duplicated("exec_id", keep="first")
    breaks = [make_break("DUPLICATE_INTERNAL", {**r, "ref": r["exec_id"]}, r["qty"], "", None,
                         r["qty"] * r["price"] * mult[r["instrument_id"]])
              for r in internal[dup].to_dict("records")]
    return internal[~dup], breaks


def aggregate(df, id_col):
    """Sum quantity and fees per (account, instrument, side); price becomes the volume-weighted average."""
    df = df.assign(notional=df["price"] * df["qty"])
    g = df.groupby(KEY, as_index=False).agg(qty=("qty", "sum"), notional=("notional", "sum"),
                                            fees=("fees", "sum"), currency=("currency", "first"),
                                            ref=(id_col, lambda s: ",".join(s.astype(str))))
    g["price"] = g["notional"] / g["qty"]   # VWAP, so three fills compare fairly with one aggregated line
    return g.drop(columns="notional")


def compare(pairs, mult, price_tol, fee_tol):
    """Compare matched internal (_int) and clearer (_clr) values. One row can give several breaks."""
    breaks = []
    for r in pairs.to_dict("records"):
        r = {**r, "account": r["account_int"], "instrument_id": r["instrument_id_int"],
             "currency": r["currency_int"]}
        notional = r["qty_int"] * r["price_int"] * mult[r["instrument_id"]]
        if r["account_int"] != r["account_clr"]:
            breaks.append(make_break("ACCOUNT_MISMATCH", r, r["account_int"], r["account_clr"], None, notional))
        if r["side_int"] != r["side_clr"]:
            # The position moves the wrong way, so the error is twice the trade size.
            breaks.append(make_break("SIDE_MISMATCH", r, r["side_int"], r["side_clr"], None, 2 * notional))
        if r["qty_int"] != r["qty_clr"]:
            d = r["qty_clr"] - r["qty_int"]
            breaks.append(make_break("QTY_MISMATCH", r, r["qty_int"], r["qty_clr"], d,
                                     d * r["price_int"] * mult[r["instrument_id"]]))
        if abs(r["price_clr"] - r["price_int"]) > price_tol:
            d = round(r["price_clr"] - r["price_int"], 6)
            breaks.append(make_break("PRICE_MISMATCH", r, round(r["price_int"], 6), round(r["price_clr"], 6), d,
                                     d * r["qty_clr"] * mult[r["instrument_id"]]))
        if abs(r["fees_clr"] - r["fees_int"]) > fee_tol:
            d = round(r["fees_clr"] - r["fees_int"], 2)
            breaks.append(make_break("FEE_MISMATCH", r, r["fees_int"], r["fees_clr"], d, d))
    return breaks


def suffix(df, tag):
    return df.rename(columns={c: f"{c}_{tag}" for c in df.columns})


def reconcile_trades(internal, clearer, ref, price_tol=PRICE_TOL, fee_tol=FEE_TOL):
    """Return one row per trade exception, in the shared exceptions schema."""
    mult = ref.set_index("instrument_id")["multiplier"]
    internal, breaks = find_duplicates(internal, mult)

    # Step 1: exact match on exec_id, the venue's id for the fill.
    with_id = clearer.dropna(subset=["exec_id"])
    step1 = suffix(internal, "int").merge(suffix(with_id, "clr"), left_on="exec_id_int", right_on="exec_id_clr")
    step1["ref"] = step1["exec_id_int"]
    breaks += compare(step1, mult, price_tol, fee_tol)

    # Step 2: whatever is left, matched on account/instrument/side with quantities summed.
    rest_int = aggregate(internal[~internal["exec_id"].isin(step1["exec_id_int"])], "exec_id")
    rest_clr = aggregate(clearer[~clearer["trade_ref"].isin(step1["trade_ref_clr"])], "trade_ref")
    step2 = rest_int.merge(rest_clr, on=KEY, how="outer", suffixes=("_int", "_clr"), indicator=True)
    both = step2[step2["_merge"] == "both"].copy()
    for col in KEY:   # same on both sides by construction; named like step 1 so compare() can be reused
        both[f"{col}_int"] = both[f"{col}_clr"] = both[col]
    both["ref"] = both["ref_int"] + " / " + both["ref_clr"]
    breaks += compare(both, mult, price_tol, fee_tol)

    for r in step2[step2["_merge"] != "both"].to_dict("records"):
        side = "int" if r["_merge"] == "left_only" else "clr"
        r = {**r, "currency": r[f"currency_{side}"], "ref": r[f"ref_{side}"]}
        notional = r[f"qty_{side}"] * r[f"price_{side}"] * mult[r["instrument_id"]]
        if side == "int":
            breaks.append(make_break("MISSING_AT_CLEARER", r, int(r["qty_int"]), "", None, notional))
        else:
            breaks.append(make_break("MISSING_INTERNAL", r, "", int(r["qty_clr"]), None, notional))
    return to_frame(breaks)
