"""
live/broker_router.py

Single lookup: given a symbol, return the correct broker adapter.

  BTCUSD, ETHUSD, SOLUSD, BNBUSD, XRPUSD  →  CoinbaseBroker
  EURUSD, GBPUSD, USDJPY, USDCHF, AUDUSD  →  OANDABroker
  MES, ES, MNQ, NQ, CL, GC, ZB, RTY       →  TastytradeBroker
  TSLA, NVDA, AAPL, MSFT, AMZN            →  TastytradeBroker

Brokers are singletons — one instance shared across all symbols.
If a broker fails to initialize (bad credentials, SDK missing) it raises
immediately at startup so you know before any orders are attempted.
"""
from __future__ import annotations

import logging
from typing import Dict

from live.brokers.base_broker import BaseBroker

logger = logging.getLogger("BrokerRouter")

# ── Symbol → broker name mapping ─────────────────────────────────────────────

SYMBOL_BROKER: Dict[str, str] = {
    # Crypto → Coinbase
    "BTCUSD":  "coinbase",
    "ETHUSD":  "coinbase",
    "SOLUSD":  "coinbase",
    "BNBUSD":  "coinbase",
    "XRPUSD":  "coinbase",
    # Forex → OANDA
    "EURUSD":  "oanda",
    "GBPUSD":  "oanda",
    "USDJPY":  "oanda",
    "USDCHF":  "oanda",
    "AUDUSD":  "oanda",
    # Futures → tastytrade
    "MES":     "webull",
    "ES":      "webull",
    "MNQ":     "webull",
    "NQ":      "webull",
    "CL":      "webull",
    "GC":      "webull",
    "ZB":      "webull",
    "RTY":     "webull",
    # Equities → tastytrade
    "TSLA":    "webull",
    "NVDA":    "webull",
    "AAPL":    "webull",
    "MSFT":    "webull",
    "AMZN":    "webull",
}


class BrokerRouter:
    """
    Lazily initializes each broker on first use.
    Raises immediately if credentials are missing.
    """

    def __init__(self) -> None:
        self._brokers: Dict[str, BaseBroker] = {}

    def get(self, symbol: str) -> BaseBroker:
        """Return the broker adapter for this symbol."""
        broker_name = SYMBOL_BROKER.get(symbol.upper())
        if broker_name is None:
            raise ValueError(f"No broker mapped for symbol '{symbol}'. "
                             f"Add it to SYMBOL_BROKER in broker_router.py")

        if broker_name not in self._brokers:
            self._brokers[broker_name] = self._init_broker(broker_name)

        return self._brokers[broker_name]

    def broker_name(self, symbol: str) -> str:
        return SYMBOL_BROKER.get(symbol.upper(), "unknown")

    def _init_broker(self, name: str) -> BaseBroker:
        logger.info("Initializing broker: %s", name)
        if name == "coinbase":
            from live.brokers.coinbase_broker import CoinbaseBroker
            return CoinbaseBroker()
        elif name == "oanda":
            from live.brokers.oanda_broker import OANDABroker
            return OANDABroker()
        elif name == "webull":
            from live.brokers.webull_broker import WebullBroker
            return WebullBroker()
        else:
            raise ValueError(f"Unknown broker name: {name}")

    def health_check(self) -> Dict[str, bool]:
        """
        Attempt to initialize all three brokers and call get_account().
        Returns a dict of broker_name → success.
        Call this at startup to catch bad credentials early.
        """
        import asyncio
        results = {}
        for name in ("coinbase", "oanda", "webull"):
            try:
                broker = self._init_broker(name)
                acct   = asyncio.get_event_loop().run_until_complete(broker.get_account())
                logger.info(
                    "Health check PASS | %s | balance=%.2f buying_power=%.2f",
                    name, acct.cash_balance, acct.buying_power
                )
                results[name] = True
                self._brokers[name] = broker
            except Exception as e:
                logger.error("Health check FAIL | %s | %s", name, e)
                results[name] = False
        return results


# ── Module-level singleton ────────────────────────────────────────────────────

_router: BrokerRouter | None = None

def get_broker_router() -> BrokerRouter:
    global _router
    if _router is None:
        _router = BrokerRouter()
    return _router
