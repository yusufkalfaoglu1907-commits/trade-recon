-- Position roll-forward: expected EOD = SOD + today's trades, compared with the clearer's EOD.
-- Tables registered from Python: sod, trades, clearer_eod.
WITH flows AS (
    -- A buy adds to the position, a sell reduces it.
    SELECT account, instrument_id,
           SUM(CASE WHEN side = 'BUY' THEN qty ELSE -qty END) AS trade_qty
    FROM trades
    GROUP BY account, instrument_id
),
pairs AS (
    -- Every account/instrument that appears anywhere; UNION also removes duplicates.
    SELECT account, instrument_id FROM sod
    UNION SELECT account, instrument_id FROM flows
    UNION SELECT account, instrument_id FROM clearer_eod
),
rolled AS (
    SELECT p.account, p.instrument_id,
           CAST(COALESCE(s.qty, 0) AS BIGINT)        AS sod_qty,       -- missing means flat
           CAST(COALESCE(f.trade_qty, 0) AS BIGINT)  AS trade_qty,
           CAST(COALESCE(s.qty, 0) + COALESCE(f.trade_qty, 0) AS BIGINT) AS expected_qty,
           CAST(COALESCE(c.qty, 0) AS BIGINT)        AS clearer_qty
    FROM pairs p
    LEFT JOIN sod s         ON s.account = p.account AND s.instrument_id = p.instrument_id
    LEFT JOIN flows f       ON f.account = p.account AND f.instrument_id = p.instrument_id
    LEFT JOIN clearer_eod c ON c.account = p.account AND c.instrument_id = p.instrument_id
)
SELECT account, instrument_id, sod_qty, trade_qty, expected_qty, clearer_qty,
       clearer_qty - expected_qty AS difference
FROM rolled
WHERE clearer_qty <> expected_qty
ORDER BY account, instrument_id;
