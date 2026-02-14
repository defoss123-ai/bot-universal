"""Модуль работы с MEXC через ccxt."""

from __future__ import annotations

import logging
from typing import Any

import ccxt
import pandas as pd

from config import DEFAULT_SETTINGS


class MEXCExchange:
    """Обёртка над ccxt.mexc для спота и фьючерсов."""

    def __init__(
        self,
        api_key: str = "",
        secret_key: str = "",
        use_testnet: bool = True,
        market_type: str = "spot",
        logger: logging.Logger | None = None,
    ) -> None:
        self.api_key = api_key
        self.secret_key = secret_key
        self.use_testnet = use_testnet
        self.market_type = market_type
        self.logger = logger or logging.getLogger("crypto_bot.exchange")

        options = {
            "defaultType": "swap" if market_type == "futures" else "spot",
        }

        self.exchange = ccxt.mexc(
            {
                "apiKey": api_key,
                "secret": secret_key,
                "enableRateLimit": DEFAULT_SETTINGS["enable_rate_limit"],
                "timeout": DEFAULT_SETTINGS["request_timeout_ms"],
                "options": options,
            }
        )

        if self.use_testnet:
            try:
                self.exchange.set_sandbox_mode(True)
                self.logger.info("Включен testnet/sandbox режим MEXC.")
            except Exception as exc:
                self.logger.warning("Не удалось включить sandbox режим: %s", exc)

    def test_connection(self) -> tuple[bool, float | None]:
        """Проверяет подключение к API и возвращает баланс USDT."""
        try:
            balance = self.exchange.fetch_balance()
            usdt_total = ((balance.get("total") or {}).get("USDT"))
            self.logger.info("Подключение к MEXC успешно. USDT total=%s", usdt_total)
            return True, usdt_total
        except Exception as exc:
            self.logger.exception("Ошибка проверки подключения к MEXC: %s", exc)
            return False, None

    def fetch_ohlcv(self, symbol: str, timeframe: str, limit: int = 100) -> pd.DataFrame | None:
        """Загружает OHLCV и возвращает DataFrame со стандартными колонками."""
        try:
            candles = self.exchange.fetch_ohlcv(symbol=symbol, timeframe=timeframe, limit=limit)
            df = pd.DataFrame(candles, columns=["timestamp", "open", "high", "low", "close", "volume"])
            df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
            return df
        except Exception as exc:
            self.logger.exception("Ошибка получения свечей для %s: %s", symbol, exc)
            return None

    def fetch_ticker(self, symbol: str) -> dict[str, Any] | None:
        """Возвращает тикер по символу."""
        try:
            return self.exchange.fetch_ticker(symbol)
        except Exception as exc:
            self.logger.exception("Ошибка получения тикера %s: %s", symbol, exc)
            return None

    def fetch_balance(self) -> dict[str, Any] | None:
        """Возвращает балансы total/free/used по активам."""
        try:
            balance = self.exchange.fetch_balance()
            return {
                "total": balance.get("total", {}),
                "free": balance.get("free", {}),
                "used": balance.get("used", {}),
            }
        except Exception as exc:
            self.logger.exception("Ошибка получения баланса: %s", exc)
            return None

    def fetch_markets(self) -> list[str] | None:
        """Возвращает список доступных символов, оканчивающихся на /USDT."""
        try:
            markets = self.exchange.fetch_markets()
            symbols = sorted(
                {
                    m.get("symbol")
                    for m in markets
                    if isinstance(m, dict) and str(m.get("symbol", "")).endswith("/USDT")
                }
            )
            return [s for s in symbols if s]
        except Exception as exc:
            self.logger.exception("Ошибка получения рынков: %s", exc)
            return None

    def create_market_order(self, symbol: str, side: str, amount: float) -> dict[str, Any] | None:
        """Создает market ордер."""
        try:
            return self.exchange.create_market_order(symbol=symbol, side=side, amount=amount)
        except Exception as exc:
            self.logger.exception("Ошибка market ордера %s %s: %s", symbol, side, exc)
            return None

    def create_limit_order(self, symbol: str, side: str, amount: float, price: float | None) -> dict[str, Any] | None:
        """Создает limit ордер."""
        if price is None:
            self.logger.error("Не указана цена для limit ордера: %s", symbol)
            return None
        try:
            return self.exchange.create_limit_order(symbol=symbol, side=side, amount=amount, price=price)
        except Exception as exc:
            self.logger.exception("Ошибка limit ордера %s %s: %s", symbol, side, exc)
            return None

    def create_stop_loss_order(self, symbol: str, side: str, amount: float, stop_price: float) -> dict[str, Any] | None:
        """Создает stop-loss ордер через универсальный create_order."""
        try:
            return self.exchange.create_order(
                symbol=symbol,
                type="stop",
                side=side,
                amount=amount,
                price=None,
                params={"stopPrice": stop_price},
            )
        except Exception as exc:
            self.logger.exception("Ошибка SL ордера %s %s: %s", symbol, side, exc)
            return None

    def fetch_open_positions(self) -> list[dict[str, Any]] | None:
        """Возвращает открытые позиции (актуально для фьючерсов)."""
        try:
            if hasattr(self.exchange, "fetch_positions"):
                positions = self.exchange.fetch_positions()
                return positions
            return []
        except Exception as exc:
            self.logger.exception("Ошибка получения открытых позиций: %s", exc)
            return None

    def fetch_open_orders(self, symbol: str | None = None) -> list[dict[str, Any]] | None:
        """Возвращает открытые ордера."""
        try:
            return self.exchange.fetch_open_orders(symbol=symbol)
        except Exception as exc:
            self.logger.exception("Ошибка получения открытых ордеров: %s", exc)
            return None

    def cancel_order(self, order_id: str, symbol: str) -> dict[str, Any] | None:
        """Отменяет ордер по ID."""
        try:
            return self.exchange.cancel_order(order_id, symbol)
        except Exception as exc:
            self.logger.exception("Ошибка отмены ордера %s: %s", order_id, exc)
            return None
