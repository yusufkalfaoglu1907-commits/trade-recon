# trade-recon

## What this is

This is a learning project. It reconciles synthetic trades, positions and cash between an internal trade blotter and a clearer, and reports the breaks.

I built it to practise the daily work of a trading operations desk: match the firm's own books against the clearer's, find the differences, explain them and suggest what to do next. All data is made up by the project itself. No real firm, broker or file format is used.

The code is about 720 lines of Python plus one SQL file. It uses pandas, DuckDB (for the SQL version of the position rec) and pytest.

## How to run

```bash
pip install -r requirements.txt
```

```bash
python -m recon run --date 2026-11-20
```

```bash
pytest
```

`run` generates the synthetic day in `data/2026-11-20/`, runs the three reconciliations and writes `output/2026-11-20/exceptions.csv` and `output/2026-11-20/summary.md`. The random seed is fixed, so the same breaks appear every time.

The code is written for Python 3.11. I have only run it on Python 3.14 so far.

## Input files

| File | Side | Contents |
|---|---|---|
| `internal_trades.csv` | internal | trade blotter, one row per fill |
| `clearer_trades.csv` | clearer | the clearer's trades; some lines have no exec_id, partial fills can be one line |
| `sod_positions.csv` | internal | start-of-day positions |
| `clearer_eod_positions.csv` | clearer | end-of-day positions |
| `cash_statement.csv` | clearer | cash lines: trade settlements, fees, FX, option exercise |
| `internal_fx.csv` | internal | the FX conversion as the firm booked it |
| `reference_data.csv` | both | instrument type, underlying, multiplier, currency, close price, expiry, strike, split |
| `injected_breaks.csv` | answer key | the breaks the generator injected; read only by the tests |

## Break types

| Break | How it is detected | Typical real-world cause |
|---|---|---|
| `MISSING_AT_CLEARER` | internal trade left unmatched after both matching steps | late or failed give-up; trade booked in error |
| `MISSING_INTERNAL` | clearer trade left unmatched after both matching steps | trade not booked by the desk; trade that is not ours |
| `QTY_MISMATCH` | matched trade, quantities differ | fat-finger booking; partial allocation |
| `PRICE_MISMATCH` | matched trade, price differs by more than 0.005 | wrong price booked; price correction at the venue |
| `FEE_MISMATCH` | matched trade, fees differ by more than 0.01 | fee schedule out of date on one side |
| `DUPLICATE_INTERNAL` | the same exec_id appears twice internally | trade booked twice, for example after a system retry |
| `SIDE_MISMATCH` | matched on exec_id, buy and sell reversed | booking error |
| `ACCOUNT_MISMATCH` | matched on exec_id, account differs | wrong allocation |
| `POSITION_CORPORATE_ACTION` | position difference equals SOD x (split ratio - 1) on the ex-date | split processed by the clearer, not yet internally |
| `POSITION_EXPIRY` | option expiring today is flat at the clearer, and the underlying differs by the shares it delivers | exercise or assignment not booked internally |
| `POSITION_TRADE_BREAK` | position difference on an account/instrument that has open trade breaks | follows from the trade breaks |
| `POSITION_UNKNOWN` | none of the above | transfer, late trade, or something not modelled |
| `CASH_TRADE_DIFF` | settlement amounts per account and currency differ from the statement | trade breaks; settlement error at the clearer |
| `CASH_FEE_DIFF` | fee totals differ | as for fee breaks |
| `CASH_FX_DIFF` | FX amounts differ from the booked deal | conversion at a different rate |
| `CASH_EXERCISE_DIFF` | exercise cash on the statement with no internal booking | exercise not booked internally |

Two cases must not break, and the tests check this: a price that differs by less than the tolerance, and three partial fills that the clearer reports as one line.

Trades are matched in two steps. First on exec_id, where both sides have it. Then, for what is left, on account, instrument and side, with quantities summed and the price taken as the volume-weighted average. The second step is what lets three partial fills match one clearer line.

## The position roll-forward

For each account and instrument:

```
expected EOD = start-of-day position + today's buys - today's sells (+ lifecycle events booked internally)
difference   = clearer EOD - expected EOD
```

Any non-zero difference is a break. The rec then looks for the most likely cause, in this order: a stock split on its ex-date, an option expiring today, open trade breaks on the same account and instrument, and otherwise unknown.

Worked example from the generated data, account ACC-A, shares in ALFA:

| | Quantity |
|---|---|
| Start of day | -800 |
| Today's trades (net) | +1,400 |
| Expected EOD | 600 |
| Clearer EOD | 1,100 |
| Difference | +500 |

ACC-A also held 5 ALFA 50 calls expiring today, with ALFA closing at 62. They were in the money, so the clearer exercised them: the option position went to zero and 5 x 100 = 500 shares were delivered. Nothing was booked internally, so the rec classes both differences as `POSITION_EXPIRY`. The same roll-forward is written in SQL in `recon/positions.sql`, and a test checks that both versions give the same result.

## Limitations

- All data is synthetic. Real clearer files have their own formats, which I do not try to copy.
- One trading day, one clearer, two accounts and two currencies.
- No settlement fails, buy-ins, ageing of breaks or transaction reporting.
- Tolerances (0.005 on price, 0.01 on fees, 5.00 per cash bucket) are illustrative.
- The cash statement shows the contractual settlement amounts for today's trades. In reality EU cash equities settle on T+2, so these amounts would only hit the account two days later.
- Futures only affect cash through fees. In reality futures cash moves through daily variation margin, which I do not model.
- Expiry: an option is treated as exercised if it is in the money at the close price. There is no exercise threshold or contrary instruction, and the generator does not trade options on their expiry day.
- The split is a simple 2-for-1 with no cash in lieu.
- The generator puts each injected break on its own account and instrument, so causes do not overlap. Real breaks often do overlap.
- The total difference per currency in the summary counts one root cause more than once, because a single missing trade breaks trades, positions and cash.

## How this was built

The specification and the code were written with AI assistants. I chose the scope (the break types, the matching rules, the roll-forward and the tests), reviewed and ran the code, and can explain every part.
