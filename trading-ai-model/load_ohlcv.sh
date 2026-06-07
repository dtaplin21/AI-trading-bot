#!/bin/bash
export PGSSLMODE=require
export PGSSLROOTCERT=""
DB_URL="postgresql://trading:4ixNOaOwwy4lEnVqCYdjv4LMjo5e2mwM@dpg-d8ed4h4m0tmc73eof7a0-a.oregon-postgres.render.com/trading_ai_mu7s"
DATA_DIR="./backend/data/ohlcv"

psql "$DB_URL" -c "
CREATE TABLE IF NOT EXISTS ohlcv (
  timestamp TIMESTAMPTZ NOT NULL,
  symbol TEXT NOT NULL,
  open FLOAT, high FLOAT, low FLOAT, close FLOAT, volume FLOAT,
  CONSTRAINT ohlcv_unique UNIQUE (timestamp, symbol)
);
CREATE TABLE IF NOT EXISTS ohlcv_staging (
  timestamp TIMESTAMPTZ,
  open FLOAT, high FLOAT, low FLOAT, close FLOAT, volume FLOAT
);"

for file in "$DATA_DIR"/*.csv; do
  [ -f "$file" ] || continue
  symbol=$(basename "$file" _1m.csv)
  echo "Loading $symbol..."
  psql "$DB_URL" -c "TRUNCATE ohlcv_staging;"
  psql "$DB_URL" -c "\copy ohlcv_staging FROM '$file' WITH (FORMAT csv, HEADER true);"
  psql "$DB_URL" -c "
    INSERT INTO ohlcv (timestamp, symbol, open, high, low, close, volume)
    SELECT timestamp, '$symbol', open, high, low, close, volume
    FROM ohlcv_staging
    ON CONFLICT (timestamp, symbol) DO NOTHING;"
  echo "  ✓ $symbol done"
done

psql "$DB_URL" -c "DROP TABLE ohlcv_staging;"
echo "All files loaded."
