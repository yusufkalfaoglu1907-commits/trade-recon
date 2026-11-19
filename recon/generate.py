"""Generate one synthetic trading day, then inject a known list of breaks.

All names, accounts, venues and prices are made up. A fixed seed means the
same files, and so the same breaks, come out on every run.
"""
from pathlib import Path

import numpy as np
import pandas as pd

from recon.common import signed_qty

SEED = 42
ACCOUNTS = ["ACC-A", "ACC-B"]
VENUES = ["VENUE-1", "VENUE-2", "VENUE-3"]
N_ORDERS = 195              # one fill each, plus one order with three partial fills
LOT = {"equity": 100, "option": 1, "future": 1}   # equities trade in round lots of 100
# The split and expiry breaks sit on these underlyings. No trade breaks are injected
# on them, so each of their position differences has exactly one cause.
PROTECTED_UNDERLYINGS = {"ALFA", "BRVO"}
FX_SELL_EUR = 100_000.00
FX_BOOKED_RATE = 1.0850     # EUR/USD rate the firm booked
FX_CLEARER_RATE = 1.0842    # rate the clearer actually used (break 12)

INTERNAL_COLS = ["exec_id", "order_id", "account", "instrument_id", "instrument_type",
                 "underlying", "side", "qty", "price", "currency", "trade_time", "venue", "fees"]
CLEARER_COLS = ["exec_id", "account", "instrument_id", "side", "qty", "price", "currency", "fees"]
REF_COLS = ["instrument_id", "instrument_type", "underlying", "multiplier", "currency",
            "close_price", "expiry_date", "strike", "option_type", "split_ratio", "split_ex_date"]


def reference_data(run_date, clean):
    """About 8 equities, 6 options (two expire today) and 2 futures, in EUR and USD."""
    later = (pd.Timestamp(run_date) + pd.Timedelta(days=28)).strftime("%Y-%m-%d")
    t, m = run_date.replace("-", ""), later.replace("-", "")
    eq = [("ALFA", "EUR", 62.00), ("BRVO", "EUR", 20.00), ("CHRL", "EUR", 25.50),
          ("DLTA", "EUR", 110.00), ("ECHO", "USD", 75.00), ("FXTR", "USD", 18.40),
          ("GOLF", "USD", 230.00), ("HOTL", "USD", 54.20)]
    rows = [(i, "equity", i, 1, ccy, px, None, None, None) for i, ccy, px in eq]
    # Options: one contract is on 100 shares, so the multiplier is 100.
    rows += [
        (f"ALFA-{t}-C50", "option", "ALFA", 100, "EUR", 12.10, run_date, 50.0, "C"),
        (f"CHRL-{t}-P20", "option", "CHRL", 100, "EUR", 0.01, run_date, 20.0, "P"),
        (f"ALFA-{m}-C65", "option", "ALFA", 100, "EUR", 1.85, later, 65.0, "C"),
        (f"DLTA-{m}-C115", "option", "DLTA", 100, "EUR", 2.40, later, 115.0, "C"),
        (f"ECHO-{m}-C80", "option", "ECHO", 100, "USD", 1.60, later, 80.0, "C"),
        (f"GOLF-{m}-P220", "option", "GOLF", 100, "USD", 4.75, later, 220.0, "P"),
        (f"IDX-{m[:6]}", "future", "IDX", 10, "EUR", 4850.0, later, None, None),
        (f"RATE-{m[:6]}", "future", "RATE", 100, "USD", 108.25, later, None, None),
    ]
    ref = pd.DataFrame([r + (None, None) for r in rows], columns=REF_COLS)
    if not clean:
        # One corporate action: BRVO splits 2-for-1 with ex-date today (close price is post-split).
        ref.loc[ref.instrument_id == "BRVO", ["split_ratio", "split_ex_date"]] = [2, run_date]
    return ref


def sod_positions(rng, ref, run_date):
    """Random start-of-day positions, plus two hand-picked ones the breaks rely on."""
    pos = {}
    for acct in ACCOUNTS:
        for inst in ref.itertuples():
            if inst.expiry_date == run_date:
                continue   # expiring options only get the hand-picked position below
            lots = int(rng.integers(-10, 21))   # mostly long, some short, some flat
            if lots:
                pos[(acct, inst.instrument_id)] = lots * LOT[inst.instrument_type]
    expiring_call = ref.loc[(ref.expiry_date == run_date) & (ref.option_type == "C"), "instrument_id"]
    pos[("ACC-A", expiring_call.iloc[0])] = 5   # long 5 in-the-money calls expiring today
    pos[("ACC-A", "BRVO")] = 1000                # pre-split holding
    return pd.DataFrame([(a, i, q) for (a, i), q in pos.items()],
                        columns=["account", "instrument_id", "qty"])


def fee(inst_type, qty, price):
    """Illustrative fee schedule: 2 bps with a 1.00 minimum for equities, per contract otherwise."""
    if inst_type == "equity":
        return round(max(1.0, 0.0002 * qty * price), 2)
    return round((0.50 if inst_type == "option" else 1.20) * qty, 2)


def make_fill(rng, inst, account, side, qty, price, order_id, seconds):
    return dict(order_id=order_id, account=account, instrument_id=inst.instrument_id,
                instrument_type=inst.instrument_type, underlying=inst.underlying, side=side,
                qty=int(qty), price=price, currency=inst.currency, seconds=seconds,
                venue=str(rng.choice(VENUES)), fees=fee(inst.instrument_type, qty, price))


def internal_trades(rng, ref, run_date):
    """The firm's blotter: one fill per order, plus one order filled in three pieces."""
    # In this toy world nobody trades an option on its expiry day.
    tradeable = ref[ref.expiry_date != run_date].reset_index(drop=True)
    fills = []
    for n in range(N_ORDERS):
        inst = tradeable.iloc[int(rng.integers(len(tradeable)))]
        qty = int(rng.integers(1, 11)) * LOT[inst.instrument_type]
        price = round(inst.close_price * (1 + rng.normal(0, 0.003)), 2)
        fills.append(make_fill(rng, inst, str(rng.choice(ACCOUNTS)), str(rng.choice(["BUY", "SELL"])),
                               qty, price, f"OR{n + 1:05d}", int(rng.integers(8 * 3600, 17 * 3600))))
    # Partial fills: 100 @ p, 100 @ p+0.10, 200 @ p+0.05, chosen so the average price is exactly p+0.05.
    dlta = ref.set_index("instrument_id").loc["DLTA"].copy()
    dlta["instrument_id"] = "DLTA"
    for k, (q, px) in enumerate([(100, 109.80), (100, 109.90), (200, 109.85)]):
        fills.append(make_fill(rng, dlta, "ACC-B", "BUY", q, px, "OR09999", 11 * 3600 + 5 * k))
    df = pd.DataFrame(fills).sort_values("seconds", kind="stable").reset_index(drop=True)
    df["exec_id"] = [f"EX{n + 1:05d}" for n in range(len(df))]
    df["trade_time"] = pd.Timestamp(run_date) + pd.to_timedelta(df.pop("seconds"), unit="s")
    return df[INTERNAL_COLS]


def pick(trades, used, **match):
    """Return the first trade on an account/instrument pair that no other injected break uses.

    One break per pair keeps the answer key unambiguous: two breaks on the same pair
    could be merged by the step-2 matching or net off in the position rec."""
    for i, t in trades.iterrows():
        pair = (t.account, t.instrument_id)
        if pair in used or t.underlying in PROTECTED_UNDERLYINGS:
            continue
        if all(t[k] == v for k, v in match.items()):
            used.add(pair)
            return i
    raise ValueError(f"no free trade matching {match}")


def clearer_eod_positions(sod, clearer, ref, run_date, clean):
    """Clearer EOD = SOD + clearer trades, plus the lifecycle events only the clearer applies."""
    flows = clearer.assign(q=signed_qty(clearer)).groupby(["account", "instrument_id"])["q"].sum()
    start = sod.set_index(["account", "instrument_id"])["qty"]
    eod = start.add(flows, fill_value=0)
    exercise_cash = []
    if not clean:
        inst = ref.set_index("instrument_id")
        for (acct, iid), q in start.items():
            r = inst.loc[iid]
            if r.split_ex_date == run_date:
                # 10. Split effective today: the clearer scales the start-of-day holding by the ratio.
                eod.loc[(acct, iid)] += q * (r.split_ratio - 1)
            if r.expiry_date == run_date and r.instrument_type == "option":
                # 11. The clearer closes the expiring option. In the money, it is exercised (or
                # assigned, if short) and the shares are delivered at the strike.
                eod.loc[(acct, iid)] = 0
                spot = inst.loc[r.underlying, "close_price"]
                if (spot > r.strike) if r.option_type == "C" else (spot < r.strike):
                    shares = q * r.multiplier * (1 if r.option_type == "C" else -1)
                    eod.loc[(acct, r.underlying)] = eod.get((acct, r.underlying), 0) + shares
                    exercise_cash.append(dict(account=acct, currency=r.currency, category="EXERCISE",
                                              amount=round(-shares * r.strike, 2), trade_ref=None,
                                              description=f"Exercise {iid}: {shares:+.0f} {r.underlying} @ {r.strike}"))
    eod = eod.astype(int).rename("qty").reset_index()
    return eod[eod.qty != 0], exercise_cash


def cash_statement(clearer, ref, extra_lines, clean):
    """The clearer's cash lines: one settlement and one fee line per trade, plus FX and exercise.

    Amounts are the contractual settlement for today's trades (they settle on T+2)."""
    t = clearer.merge(ref[["instrument_id", "instrument_type", "multiplier"]], on="instrument_id")
    lines = []
    for r in t.itertuples():
        # Futures have no premium at trade time (cash moves through variation margin, not modelled).
        if r.instrument_type != "future":
            sign = -1 if r.side == "BUY" else 1   # buying pays cash out
            lines.append(dict(account=r.account, currency=r.currency, category="TRADE",
                              amount=round(sign * r.price * r.qty * r.multiplier, 2), trade_ref=r.trade_ref,
                              description=f"{r.side} {r.qty} {r.instrument_id} @ {r.price}"))
        lines.append(dict(account=r.account, currency=r.currency, category="FEE", amount=-r.fees,
                          trade_ref=r.trade_ref, description="Clearing and exchange fees"))
    rate = FX_BOOKED_RATE if clean else FX_CLEARER_RATE
    lines.append(dict(account="ACC-A", currency="EUR", category="FX", amount=-FX_SELL_EUR,
                      trade_ref=None, description=f"FX sell EUR buy USD @ {rate}"))
    lines.append(dict(account="ACC-A", currency="USD", category="FX", amount=round(FX_SELL_EUR * rate, 2),
                      trade_ref=None, description=f"FX sell EUR buy USD @ {rate}"))
    stmt = pd.DataFrame(lines + extra_lines)
    stmt.insert(0, "line_id", [f"CSH{n + 1:05d}" for n in range(len(stmt))])
    return stmt


def generate(run_date, data_dir="data", clean=False):
    """Write all CSV files for one day. clean=True skips every injected break."""
    rng = np.random.default_rng(SEED)
    ref = reference_data(run_date, clean)
    sod = sod_positions(rng, ref, run_date)
    internal = internal_trades(rng, ref, run_date)
    clearer = internal[CLEARER_COLS].copy()
    key, used = [], set()

    def answer(rec, break_type, account, instrument_id="", currency="", ref_="", note=""):
        key.append(dict(break_id=len(key) + 1, rec=rec, expected_break_type=break_type, account=account,
                        instrument_id=instrument_id, currency=currency, ref=ref_, note=note))

    def answer_trade(i, break_type, note, row=None):
        t = internal.loc[i] if row is None else row
        answer("TRADE", break_type, t.account, t.instrument_id, t.currency, t.exec_id, note)

    # Normal features that must NOT break (present on the clean day too).
    # 9. Partial fills: the clearer reports the three fills as one line at the average price, no exec_id.
    fills = internal[internal.order_id == "OR09999"]
    used.add(("ACC-B", "DLTA"))
    agg = fills.iloc[0][CLEARER_COLS].copy()
    agg[["exec_id", "qty", "fees"]] = [None, fills.qty.sum(), round(fills.fees.sum(), 2)]
    agg["price"] = round((fills.price * fills.qty).sum() / fills.qty.sum(), 4)
    clearer = pd.concat([clearer.drop(fills.index), agg.to_frame().T])
    answer("TRADE", "NONE", "ACC-B", "DLTA", "EUR", ",".join(fills.exec_id), "3 partial fills vs 1 clearer line")
    # 4b. Price difference inside tolerance (a rounding difference at the clearer).
    i = pick(internal, used, instrument_type="equity")
    clearer.loc[i, "price"] = round(internal.loc[i, "price"] + 0.003, 4)
    answer_trade(i, "NONE", "price differs by 0.003, inside tolerance")
    # Some clearer lines arrive without our exec_id; they must still match on account/instrument/side.
    for _ in range(3):
        i = pick(internal, used)
        clearer.loc[i, "exec_id"] = None
        answer_trade(i, "NONE", "clearer line without exec_id")

    if not clean:
        i = pick(internal, used)
        clearer = clearer.drop(i)
        answer_trade(i, "MISSING_AT_CLEARER", "1. booked internally only")
        i = pick(internal, used)
        answer_trade(i, "MISSING_INTERNAL", "2. clearer only")
        internal = internal.drop(i)
        i = pick(internal, used, instrument_type="equity")
        clearer.loc[i, "qty"] += 100
        answer_trade(i, "QTY_MISMATCH", "3. clearer qty +100")
        i = pick(internal, used, instrument_type="option")
        clearer.loc[i, "price"] = round(internal.loc[i, "price"] + 0.25, 2)
        answer_trade(i, "PRICE_MISMATCH", "4a. option price +0.25, outside tolerance")
        i = pick(internal, used, instrument_type="equity")
        clearer.loc[i, "fees"] = round(internal.loc[i, "fees"] + 2.50, 2)
        answer_trade(i, "FEE_MISMATCH", "5. clearer fee +2.50")
        i = pick(internal, used, side="BUY")
        internal.loc[i, "side"] = "SELL"
        answer_trade(i, "SIDE_MISMATCH", "7. BUY booked as SELL")
        i = pick(internal, used)
        right, wrong = internal.loc[i, "account"], [a for a in ACCOUNTS if a != internal.loc[i, "account"]][0]
        internal.loc[i, "account"] = wrong
        used.add((wrong, internal.loc[i, "instrument_id"]))
        answer_trade(i, "ACCOUNT_MISMATCH", f"8. booked to {wrong}, clearer has {right}")
        settle_break = internal.loc[pick(internal, used, instrument_type="equity")]
        i = pick(internal, used)
        internal = pd.concat([internal, internal.loc[[i]]])   # same exec_id booked twice
        answer_trade(i, "DUPLICATE_INTERNAL", "6. booked twice", internal.loc[i].iloc[0])

    internal = internal.sort_values(["trade_time", "exec_id"]).reset_index(drop=True)
    clearer = clearer.reset_index(drop=True)
    clearer.insert(0, "trade_ref", [f"CLR{n + 1:06d}" for n in range(len(clearer))])
    eod, exercise_cash = clearer_eod_positions(sod, clearer, ref, run_date, clean)
    stmt = cash_statement(clearer, ref, exercise_cash, clean)
    fx = pd.DataFrame([dict(account="ACC-A", sell_currency="EUR", sell_amount=FX_SELL_EUR, buy_currency="USD",
                            rate=FX_BOOKED_RATE, buy_amount=round(FX_SELL_EUR * FX_BOOKED_RATE, 2))])

    if not clean:
        for r in sod[sod.instrument_id.isin(ref.loc[ref.split_ratio.notna(), "instrument_id"])].itertuples():
            answer("POSITION", "POSITION_CORPORATE_ACTION", r.account, r.instrument_id, note="10. split at clearer only")
        for line in exercise_cash:
            opt = line["description"].split()[1].rstrip(":")
            answer("POSITION", "POSITION_EXPIRY", line["account"], opt, note="11. option closed at clearer")
            answer("POSITION", "POSITION_EXPIRY", line["account"], opt.split("-")[0], note="11. shares delivered")
            answer("CASH", "CASH_EXERCISE_DIFF", line["account"], currency=line["currency"], note="11. exercise cash")
        # 12a. The clearer's settlement line for one clean trade is 150.00 too large a debit.
        tref = clearer.loc[clearer.exec_id == settle_break.exec_id, "trade_ref"].iloc[0]
        stmt.loc[(stmt.trade_ref == tref) & (stmt.category == "TRADE"), "amount"] -= 150.00
        answer("CASH", "CASH_TRADE_DIFF", settle_break.account, currency=settle_break.currency,
               ref_=tref, note="12a. settlement line differs from the trade")
        answer("CASH", "CASH_FX_DIFF", "ACC-A", currency="USD", note="12b. FX at 1.0842 vs booked 1.0850")

    out = Path(data_dir) / run_date
    out.mkdir(parents=True, exist_ok=True)
    files = dict(internal_trades=internal, clearer_trades=clearer, sod_positions=sod, clearer_eod_positions=eod,
                 cash_statement=stmt, reference_data=ref, internal_fx=fx,
                 injected_breaks=pd.DataFrame(key, columns=["break_id", "rec", "expected_break_type", "account",
                                                            "instrument_id", "currency", "ref", "note"]))
    for name, df in files.items():
        df.to_csv(out / f"{name}.csv", index=False)
    return out
