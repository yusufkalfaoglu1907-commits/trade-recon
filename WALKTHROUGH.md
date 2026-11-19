# Walkthrough

My study notes for this project. The modules are in the order the data flows through them. Under each one are the questions an operations interviewer might ask, with short answers.

Order of a run: `generate.py` writes the files, then `report.reconcile_day` runs `trades.py`, then `positions.py` (which uses the trade breaks), then `cash.py`, and `report.py` writes the output.

---

## 1. `recon/common.py`

- `COLUMNS`: the one schema every break uses (rec, break_type, account, instrument_id, currency, ref, internal_value, clearer_value, difference, amount, suggested_cause, suggested_action). One schema means one exceptions file that a team can sort, filter and assign.
- `load_day`: reads all CSVs for a date into a dict.
- `signed_qty`: +qty for a buy, -qty for a sell. Used by the generator, the position rec and the clearer EOD.
- `to_frame`: list of dicts to a DataFrame with the shared columns, which also gives an empty frame the right columns.

**Q: Why does `amount` exist when there is already `difference`?**
`difference` is in the natural unit of the break: shares, contracts, a price, or cash. You cannot sort a quantity against a price. `amount` turns each break into money (for example quantity x price x multiplier), so the list can be sorted by size and the biggest problems are looked at first.

---

## 2. `recon/generate.py`

- `reference_data`: 8 equities, 6 options (two expire on the run date), 2 futures, in EUR and USD. Multiplier is 1 for equities, 100 for options, 10 or 100 for futures. On a break day BRVO has a 2-for-1 split with ex-date today.
- `sod_positions`: random positions, plus two that the breaks need: long 5 ALFA 50 calls expiring today, and 1,000 BRVO before the split.
- `fee`: 2 basis points with a 1.00 minimum for equities; per contract for options and futures.
- `internal_trades`: 195 single-fill orders plus one order filled in three pieces (100, 100 and 200 DLTA). The prices are picked so the average is exactly 109.85, so the aggregated clearer line matches exactly.
- `pick`: chooses the trade that gets a break. It skips ALFA and BRVO (reserved for the split and expiry) and any account/instrument pair already used, so each break has exactly one cause.
- `clearer_eod_positions`: clearer EOD = SOD + clearer trades, then the events only the clearer has processed: the split scales the SOD holding, and the expiring call is closed and 500 shares delivered at the strike. It also returns the exercise cash line (-500 x 50 = -25,000 EUR).
- `cash_statement`: one TRADE and one FEE line per clearer trade, the FX pair, and the exercise line.
- `generate`: builds everything, injects the breaks unless `clean=True`, and writes the answer key. The features that must not break (partial fills, a price inside tolerance, clearer lines without exec_id) are added on the clean day too.

**Q: Why generate your own data rather than use a sample file?**
I need to know the right answer. With an answer key, a test can prove each break is found and that the clean cases are not flagged. Real data is also confidential.

**Q: Why the fixed seed?**
So a run is reproducible. If a test fails I can rerun and see the same data. In operations the same idea applies: you want to be able to rerun yesterday's rec and get yesterday's result.

**Q: Why is the in-tolerance price difference also on the clean day?**
Because it is not a break. A clean day is "nothing the desk needs to act on", not "every number identical".

---

## 3. `recon/trades.py`

- `find_duplicates`: an exec_id must appear once. Extra copies become `DUPLICATE_INTERNAL` and are removed before matching, so they cannot match the clearer line a second time.
- Step 1 in `reconcile_trades`: inner join on exec_id between internal and the clearer lines that have one. Both sides are renamed with `_int` and `_clr` so the comparison is explicit.
- `compare`: for each matched pair, checks account, side, quantity, price (against `PRICE_TOL`) and fees (against `FEE_TOL`). One pair can give several breaks.
- `aggregate`: for step 2, sums quantity and fees per (account, instrument, side) and works out the volume-weighted average price.
- Step 2: outer join of the two aggregated sides on that key. Both sides present means compare. Only internal means `MISSING_AT_CLEARER`. Only clearer means `MISSING_INTERNAL`.

**Q: Why match on exec_id first?**
It is the venue's unique id for a fill, so a match on it is certain. Matching on economics alone (account, instrument, side, quantity) could pair the wrong two trades when there are many similar ones.

**Q: Why aggregate in step 2 and not match fill by fill?**
Clearers often report several fills of one order as one line, without our exec_ids. Summing quantity and averaging the price lets 100 + 100 + 200 match one line of 400 at the average price.

**Q: Why is the price tolerance absolute and not a percentage?**
The typical harmless difference is rounding to fewer decimals, which is a fixed amount per unit whatever the price. In a real desk the tolerance would depend on the instrument's tick size. It is a parameter, so it can be changed.

**Q: Why is a wrong side sized at twice the trade?**
If we booked a buy of 100 as a sell of 100, our position is 200 away from the truth: -100 instead of +100. The cash is also wrong in the other direction.

**Q: What is the risk in step 2?**
If two unrelated trades on the same account, instrument and side are both unmatched, they get summed together, and one break can hide another. The generator avoids this on purpose. In practice you would add a time window or the order id to the key.

---

## 4. `recon/positions.py` and `recon/positions.sql`

- `roll_forward`: SOD + signed trades, outer-joined with the clearer EOD. A pair missing from a file counts as zero. It returns only rows where the difference is not zero.
- `roll_forward_sql`: the same logic in SQL, run in DuckDB on the same DataFrames. The `pairs` CTE uses `UNION` to collect every account/instrument from all three tables. This does the job of the pandas outer joins.
- `expected_deliveries`: for options expiring today that are flat at the clearer and in the money, the shares that should have moved: contracts x multiplier, plus for a call, minus for a put. A short position gives the opposite sign, which covers assignment.
- `classify`: checks the most specific cause first. Split: difference equals SOD x (ratio - 1). Expiry: the option is flat at the clearer, or the underlying is off by exactly the expected delivery. Trade break: the pair has an open trade break (for a wrong account, both accounts count). Otherwise unknown.

**Q: Walk me through the roll-forward.**
Take yesterday's closing position, which both sides agreed. Add today's buys and subtract today's sells to get what we expect tonight. Compare with the clearer. If they differ, either a trade differs or an event happened on one side only.

**Q: Why does the split difference equal SOD x (ratio - 1)?**
The ex-date is today, so the split applies to the holding at the start of the day. 1,000 shares become 2,000. Today's trades are already at post-split prices and quantities on both sides. So the gap is 1,000 x (2 - 1) = 1,000.

**Q: Why use the trade breaks to explain position breaks?**
Most position breaks are a result of trade breaks, not separate problems. Linking them stops two people working on the same issue, and tells you the position break will clear once the trade is fixed.

**Q: Why write it in SQL too?**
Many operations teams keep books in a database, and a rec is a natural fit for SQL: group, join, compare. The test that both versions agree also checks each against the other.

---

## 5. `recon/cash.py`

- `expected_cash`: for any set of trades, cash per account, currency and category. TRADE = -price x qty x multiplier for a buy, + for a sell. Futures give zero consideration. FEE = -fees.
- `booked_fx`: our FX deal as two cash amounts: EUR out, USD in.
- `reconcile_cash`: three figures per bucket. `internal` (our trades and FX), `statement` (the clearer's cash lines) and `confirmed` (the same formula applied to the clearer's own trades). Buckets where statement minus internal is more than `CASH_TOL` become breaks.
- `explain`: splits the difference into "from trade breaks" (confirmed minus internal) and "the statement differs from the clearer's own trades" (statement minus confirmed). For FX it works out the rate actually used: USD received / EUR sold.

**Q: Why are futures excluded from settlement cash?**
Buying a future does not cost the notional. The position is margined: gains and losses are paid every day as variation margin. Only fees move on the trade itself. I have not modelled margin.

**Q: Why compare by bucket and not line by line?**
Cash statements are often netted or arrive in a different shape from the trades. A bucket rec (account, currency, category) is quick and catches everything. The `confirmed` figure then says whether to look at our trades or at the clearer's cash lines.

**Q: Why a tolerance of 5.00 on cash?**
Each line is rounded to cents, and over a few hundred lines the rounding adds up. The in-tolerance price difference also creates a small cash difference. The number is illustrative. A real desk would agree it with finance.

**Q: How did you find the FX rate issue?**
We sold 100,000 EUR at a booked rate of 1.0850, so we expected 108,500 USD. The statement shows 108,420, which implies 1.0842. The difference of 80 USD is a rate difference, not a missing payment.

---

## 6. `recon/report.py` and `recon/__main__.py`

- `reconcile_day`: runs the recs in order. Trades come first, because positions use the trade breaks.
- `write_report`: writes `exceptions.csv` (all breaks, one schema) and `summary.md` (counts by break type, total absolute amount per currency, and open exceptions largest first).
- `print_summary`: the short terminal version.
- `__main__.main`: `python -m recon run --date ...` regenerates the data and runs everything.

**Q: Why sort by size?**
With limited time before a cut-off, you work on the biggest exposures first. In practice you would also sort by age.

**Q: Why does the summary say the totals overstate the risk?**
The missing DLTA trade appears as a trade break, a position break and part of a cash break. Adding all three counts it three times.

---

## 7. Tests

- `test_generate.py`: same seed gives identical files; the universe and trade count are right; the answer key covers every break type.
- `test_trades.py`: small hand-made examples of each trade rule, including partial fills and the option multiplier.
- `test_positions.py`: the worked example, new and closed positions, and SQL versus pandas.
- `test_cash.py`: signs and multiplier, the FX explanation, and a statement-only difference.
- `test_answer_key.py`: every injected break is found with the right type; the cases that must match produce no trade exception; the trade rec reports nothing extra; no position break is "unknown"; a clean day gives zero exceptions.

**Q: How do you know the tests would catch a bug?**
I broke the code on purpose (price tolerance set to zero, step 2 switched off) and checked that the tests failed.

---

## Glossary

- **Trade lifecycle**: the steps a trade goes through after the order. **Execution**: the order fills on a venue. **Booking**: the trade is recorded in the firm's own system. **Matching**: our record is checked against the counterparty's or the clearer's. **Clearing**: a central counterparty steps in between buyer and seller and works out what each side owes. **Settlement**: securities and cash actually change hands.
- **CCP vs clearing member**: a CCP (central counterparty) is the clearing house that becomes buyer to every seller and seller to every buyer. A clearing member is a firm that has direct access to the CCP and clears trades for itself or for clients. A trading firm that is not a member clears through one. In this project, that is "the clearer".
- **T+2 settlement**: EU cash equities settle two business days after the trade date. A trade on Friday settles on Tuesday. Until then, the cash and shares are owed but have not moved.
- **Exercise vs assignment**: the holder of an option exercises it, meaning they use the right to buy (call) or sell (put) at the strike. The writer of the option is assigned, meaning they must deliver the other side. In-the-money options are usually exercised automatically at expiry.
- **Contract multiplier**: how many units of the underlying one contract covers. For an equity option it is usually 100, so a price of 2.40 costs 240 per contract. For a future it is set by the exchange.
- **Corporate action**: an event set by the issuer that changes a holding, for example a split, a dividend or a merger. A 2-for-1 split doubles the shares and halves the price.
- **Break**: a difference between two records that should agree.
- **Ageing**: how long a break has been open. Older breaks are more urgent because they can turn into settlement fails, costs or capital charges.
- **Tolerance**: the size of difference that is accepted as noise (for example rounding) and not flagged.
- **Four-eyes check**: a second person reviews and approves a change, such as an amendment to a booked trade, before it takes effect.
