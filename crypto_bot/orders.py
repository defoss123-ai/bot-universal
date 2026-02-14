"""Менеджер ордеров: размещение, отмена, перевыставление, SL/TP."""

from __future__ import annotations

import logging
import time
from typing import Any

from risk import RiskManager


class OrderManager:
    """Класс управления торговыми ордерами."""

    def __init__(self, exchange, logger: logging.Logger | None = None) -> None:
        self.exchange = exchange
        self.logger = logger or logging.getLogger("crypto_bot.orders")

    def place_order(
        self,
        symbol: str,
        side: str,
        amount: float,
        order_type: str = "market",
        price: float | None = None,
    ) -> dict[str, Any] | None:
        """Размещает market/limit ордер."""
        try:
            if order_type == "limit":
                result = self.exchange.create_limit_order(symbol, side, amount, price)
            else:
                result = self.exchange.create_market_order(symbol, side, amount)
            self.logger.info("Ордер размещен: %s %s %s %s", symbol, side, amount, order_type)
            return result
        except Exception as exc:
            self.logger.exception("Ошибка размещения ордера: %s", exc)
            return None

    def place_limit_order_with_retry(
        self,
        symbol: str,
        side: str,
        amount: float,
        price: float,
        max_attempts: int,
        interval: int,
        fallback_to_market: bool,
    ) -> dict[str, Any] | None:
        """Пытается разместить и перевыставить лимитный ордер, затем fallback при необходимости."""
        attempts = 0
        while max_attempts == 0 or attempts < max_attempts:
            attempts += 1
            order = self.place_order(symbol, side, amount, order_type="limit", price=price)
            if order is None:
                time.sleep(max(interval, 1))
                continue

            order_id = order.get("id")
            time.sleep(max(interval, 1))
            open_orders = self.exchange.fetch_open_orders(symbol)
            if not open_orders:
                self.logger.info("Лимитный ордер исполнен: %s", order_id)
                return order

            still_open = any(o.get("id") == order_id for o in open_orders)
            if still_open and order_id:
                self.cancel_order(order_id, symbol)
                ticker = self.exchange.fetch_ticker(symbol)
                if ticker and ticker.get("last"):
                    price = float(ticker["last"])
                self.logger.info("Лимитный ордер перевыставляется. Попытка %s", attempts)

        if fallback_to_market:
            self.logger.warning("Достигнут лимит попыток. Переход на market.")
            return self.place_order(symbol, side, amount, order_type="market")

        self.logger.warning("Достигнут лимит попыток. Ордер не исполнен.")
        return None

    def cancel_order(self, order_id: str, symbol: str) -> bool:
        """Отменяет ордер по id."""
        try:
            self.exchange.cancel_order(order_id, symbol)
            self.logger.info("Ордер отменен: %s (%s)", order_id, symbol)
            return True
        except Exception as exc:
            self.logger.exception("Ошибка отмены ордера: %s", exc)
            return False

    def set_stop_loss_take_profit(
        self,
        symbol: str,
        position_side: str,
        entry_price: float,
        stop_loss_percent: float,
        take_profit_percent: float,
        amount: float,
    ) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
        """Ставит защитные SL/TP ордера после открытия позиции."""
        is_long = position_side.lower() == "long"
        close_side = "sell" if is_long else "buy"
        sl_price = RiskManager.calculate_stop_loss(entry_price, stop_loss_percent, is_long)
        tp_price = RiskManager.calculate_take_profit(entry_price, take_profit_percent, is_long)

        sl_order = self.exchange.create_stop_loss_order(symbol, close_side, amount, sl_price)
        tp_order = self.exchange.create_limit_order(symbol, close_side, amount, tp_price)
        self.logger.info("SL/TP выставлены: SL=%s TP=%s", sl_price, tp_price)
        return sl_order, tp_order
