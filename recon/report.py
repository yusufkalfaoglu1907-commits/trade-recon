"""Run the three recs for one day and write exceptions.csv and summary.md."""
from pathlib import Path

import pandas as pd

from recon.cash import reconcile_cash
from recon.common import COLUMNS, load_day
from recon.positions import reconcile_positions
from recon.trades import reconcile_trades


def reconcile_day(data_dir, run_date):
    """Trades first: the position and cash recs use the trade breaks to explain their own."""
    d = load_day(data_dir, run_date)
    trades = reconcile_trades(d["internal_trades"], d["clearer_trades"], d["reference_data"])
    positions = reconcile_positions(d["sod_positions"], d["internal_trades"], d["clearer_eod_positions"],
                                    d["reference_data"], trades, run_date)
    cash = reconcile_cash(d["internal_trades"], d["clearer_trades"], d["cash_statement"],
                          d["internal_fx"], d["reference_data"])
    frames = [f for f in (trades, positions, cash) if len(f)]
    if not frames:
        return pd.DataFrame(columns=COLUMNS)
    return pd.concat(frames, ignore_index=True)[COLUMNS]


def md_table(df):
    """Minimal Markdown table, to avoid an extra dependency."""
    lines = ["| " + " | ".join(df.columns) + " |", "|" + "---|" * len(df.columns)]
    lines += ["| " + " | ".join("" if pd.isna(v) else str(v) for v in row) + " |" for row in df.values]
    return "\n".join(lines)


def counts(exceptions):
    return exceptions.groupby(["rec", "break_type"]).size().rename("count").reset_index()


def totals(exceptions):
    return exceptions.groupby("currency")["amount"].sum().round(2).rename("total_abs_amount").reset_index()


def write_report(exceptions, out_dir, run_date):
    out = Path(out_dir) / run_date
    out.mkdir(parents=True, exist_ok=True)
    exceptions.to_csv(out / "exceptions.csv", index=False)

    biggest = exceptions.sort_values("amount", ascending=False).copy()
    biggest["instrument / ccy"] = biggest["instrument_id"].where(biggest["instrument_id"] != "", biggest["currency"])
    biggest["amount"] = biggest["amount"].map("{:,.2f}".format)
    biggest["difference"] = biggest["difference"].map(lambda v: "" if pd.isna(v) else f"{v:,.6g}")
    open_list = biggest[["rec", "break_type", "account", "instrument / ccy", "internal_value",
                         "clearer_value", "difference", "amount", "suggested_action"]]
    text = "\n\n".join([
        f"# Reconciliation summary: {run_date}",
        "Synthetic data from a learning project. Amounts are in the currency of the instrument or cash line.",
        f"**{len(exceptions)} open exceptions.**",
        "## Exceptions by break type", md_table(counts(exceptions)),
        "## Total absolute difference per currency",
        md_table(totals(exceptions).assign(total_abs_amount=lambda t: t.total_abs_amount.map("{:,.2f}".format))),
        "One root cause often shows up in more than one rec (a missing trade breaks trades, positions "
        "and cash), so these totals overstate the money actually at risk.",
        "## Open exceptions, largest first", md_table(open_list),
    ]) + "\n"
    (out / "summary.md").write_text(text)
    return out


def print_summary(exceptions, out):
    print(f"{len(exceptions)} open exceptions")
    for r in counts(exceptions).itertuples():
        print(f"  {r.rec:<9}{r.break_type:<28}{r.count:>3}")
    for r in totals(exceptions).itertuples():
        print(f"  total abs difference {r.currency}: {r.total_abs_amount:,.2f}")
    print(f"Written to {out}/exceptions.csv and {out}/summary.md")
