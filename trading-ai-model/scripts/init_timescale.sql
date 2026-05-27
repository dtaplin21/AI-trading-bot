-- TimescaleDB init — runs 001 + 002 + 003 migrations on fresh Docker volume
-- psql $DATABASE_URL -f scripts/init_timescale.sql

\ir ../db/migrations/001_market_data.sql
\ir ../db/migrations/002_trading_signals.sql
\ir ../db/migrations/003_news_intelligence_tables.sql
