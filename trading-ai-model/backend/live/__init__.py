"""Live trading — market data adapters and unified execution via BrokerRouter."""

from live.broker_router import BrokerRouter, get_broker_router
from live.coinbase_executor import CoinbaseExecutor, get_coinbase_executor
from live.live_execution_agent import LiveExecutionAgent, get_live_execution_agent
from live.live_position_monitor import LivePosition, get_position_monitor
from live.oanda_executor import OandaExecutor, get_oanda_executor
from live.broker_adapter import (
    BrokerAdapter,
    CoinbaseBrokerAdapter,
    OandaBrokerAdapter,
    PolygonBrokerAdapter,
    get_broker_adapter,
    default_worker_broker,
    register_broker_adapter,
)
from live.market_data_router import resolve_market_data_adapter, resolve_market_data_broker_id
from live.order_router import OrderRouter
from live.sync_broker import run_broker

__all__ = [
    "BrokerAdapter",
    "BrokerRouter",
    "CoinbaseBrokerAdapter",
    "CoinbaseExecutor",
    "LiveExecutionAgent",
    "LivePosition",
    "OandaBrokerAdapter",
    "OandaExecutor",
    "OrderRouter",
    "PolygonBrokerAdapter",
    "get_broker_adapter",
    "get_broker_router",
    "get_coinbase_executor",
    "get_live_execution_agent",
    "get_oanda_executor",
    "get_position_monitor",
    "default_worker_broker",
    "register_broker_adapter",
    "resolve_market_data_adapter",
    "resolve_market_data_broker_id",
    "run_broker",
]
