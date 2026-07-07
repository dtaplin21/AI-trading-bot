-- 011_purge_demoted_polygon_bars.sql
--
-- Remove Polygon-era forex/crypto bars before OANDA/Coinbase primary feeds.
-- Safe to re-run: deletes by symbol class only (worker repopulates from live feeds).

DELETE FROM ohlcv_candles
WHERE close <= 0
  AND symbol IN (
    'EURUSD', 'GBPUSD', 'USDJPY', 'USDCHF', 'AUDUSD',
    'BTCUSD', 'ETHUSD', 'SOLUSD', 'BNBUSD', 'XRPUSD'
  );

DELETE FROM ohlcv_candles
WHERE symbol IN ('EURUSD', 'GBPUSD', 'USDJPY', 'USDCHF', 'AUDUSD');
