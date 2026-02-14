"""Торговый движок: сигналы, позиции, усреднение, трейлинг, мультипары."""

from __future__ import annotations

import logging
import threading
import time
from typing import Any

from indicators import add_bollinger, add_macd, add_rsi, add_volume_ratio
from risk import TrailingStop


class MultiPairManager:
    """Управление количеством одновременных позиций по разным парам."""

    def __init__(self, max_positions: int = 2) -> None:
        self.max_positions = max(int(max_positions), 1)
        self.active_symbols: list[str] = []

    def can_open_new_position(self, symbol: str) -> bool:
        """Проверяет, можно ли открыть новую позицию."""
        if symbol in self.active_symbols:
            return False
        return len(self.active_symbols) < self.max_positions

    def add_position(self, symbol: str) -> None:
        """Регистрирует открытую позицию по символу."""
        if symbol not in self.active_symbols:
            self.active_symbols.append(symbol)

    def remove_position(self, symbol: str) -> None:
        """Удаляет символ из активных позиций."""
        if symbol in self.active_symbols:
            self.active_symbols.remove(symbol)


class TradingBot:
    """Основной класс автоматической торговли."""

    def __init__(
        self,
        exchange,
        config: dict[str, Any],
        risk_manager,
        order_manager,
        logger: logging.Logger | None = None,
        notifier=None,
    ) -> None:
        self.exchange = exchange
        self.config = config
        self.risk_manager = risk_manager
        self.order_manager = order_manager
        self.logger = logger or logging.getLogger("crypto_bot.trader")
        self.notifier = notifier

        self._running = False
        self._thread: threading.Thread | None = None
        self.active_positions: dict[str, dict[str, Any]] = {}
        self.multi_pair_manager = MultiPairManager(config.get("max_positions", 2))

        self.capital_mode = config.get("capital_mode", "fixed")
        self.initial_balance = 0.0

    def _notify(self, message: str, kind: str = "trade") -> None:
        """Отправляет уведомление в Telegram с учетом фильтров."""
        if not self.notifier:
            return
        if kind == "error" and not self.config.get("telegram_errors", True):
            return
        if kind == "trade" and not self.config.get("telegram_trades", True):
            return
        self.notifier.send(message)

    def check_signals(self, symbol: str, timeframe: str) -> str | None:
        """Возвращает сигнал long/short/None."""
        df = self.exchange.fetch_ohlcv(symbol, timeframe, self.config.get("ohlcv_limit", 100))
        if df is None or df.empty:
            return None

        ind = self.config.get("indicators", {})
        if ind.get("rsi", {}).get("enabled"):
            df = add_rsi(df, ind["rsi"].get("period", 14))
        if ind.get("macd", {}).get("enabled"):
            m = ind["macd"]
            df = add_macd(df, m.get("fast", 12), m.get("slow", 26), m.get("signal", 9))
        if ind.get("bollinger", {}).get("enabled"):
            b = ind["bollinger"]
            df = add_bollinger(df, b.get("period", 20), b.get("std_dev", 2.0))
        if ind.get("volume", {}).get("enabled"):
            df = add_volume_ratio(df, ind["volume"].get("period", 20))

        last = df.iloc[-1]
        close = float(last["close"])
        rsi_col = f"rsi_{ind.get('rsi', {}).get('period', 14)}"
        bb_suffix = f"{ind.get('bollinger', {}).get('period', 20)}_{ind.get('bollinger', {}).get('std_dev', 2.0)}"

        rsi_value = last.get(rsi_col)
        bb_low = last.get(f"bb_lower_{bb_suffix}")
        bb_up = last.get(f"bb_upper_{bb_suffix}")
        if rsi_value is None or bb_low is None or bb_up is None:
            return None
        if rsi_value < 30 and close < bb_low:
            return "long"
        if rsi_value > 70 and close > bb_up:
            return "short"
        return None

    def _can_open_position(self, symbol: str) -> bool:
        if not self.multi_pair_manager.can_open_new_position(symbol):
            return False
        return symbol not in self.active_positions

    def execute_signal(self, symbol: str, signal: str) -> bool:
        """Открывает позицию по сигналу, если это разрешено мультипарностью."""
        if signal not in {"long", "short"}:
            return False
        if not self._can_open_position(symbol):
            return False

        balance = self.exchange.fetch_balance() or {}
        usdt_balance = float((balance.get("free") or {}).get("USDT") or 0.0)
        if usdt_balance <= 0:
            self.logger.warning("Недостаточно USDT для сделки")
            self._notify("⚠️ ОШИБКА\nНедостаточно средств для открытия позиции", kind="error")
            return False

        if self.initial_balance <= 0:
            self.initial_balance = usdt_balance

        ticker = self.exchange.fetch_ticker(symbol)
        if not ticker or not ticker.get("last"):
            return False

        entry_price = float(ticker["last"])
        risk_percent = float(self.config.get("risk_percent", 20.0))
        if self.capital_mode == "pool":
            used_balance = sum(pos.get("locked_balance", 0.0) for pos in self.active_positions.values())
            free_for_pool = max(usdt_balance - used_balance, 0.0)
            amount = self.risk_manager.calculate_position_size(
                free_for_pool,
                risk_percent,
                float(self.config.get("leverage", 1.0)),
                entry_price,
            )
        else:
            amount = self.risk_manager.calculate_position_size(
                usdt_balance,
                risk_percent,
                float(self.config.get("leverage", 1.0)),
                entry_price,
            )

        if amount <= 0:
            return False

        if self.config.get("signals_only", True):
            self.logger.info("Signals-only: %s %s", symbol, signal)
            return True

        side = "buy" if signal == "long" else "sell"
        order_mode = self.config.get("entry_order_type", "market")
        if order_mode == "limit":
            deviation = float(self.config.get("limit_deviation_percent", 0.0)) / 100.0
            target_price = entry_price * (1 + deviation)
            fallback_to_market = self.config.get("limit_fallback", "cancel") == "market"
            order_result = self.order_manager.place_limit_order_with_retry(
                symbol=symbol,
                side=side,
                amount=amount,
                price=target_price,
                max_attempts=int(self.config.get("limit_max_attempts", 0)),
                interval=int(self.config.get("limit_interval_sec", 5)),
                fallback_to_market=fallback_to_market,
            )
        else:
            order_result = self.order_manager.place_order(symbol, side, amount, "market")

        if not order_result:
            return False

        is_long = signal == "long"
        stop_loss = self.risk_manager.calculate_stop_loss(entry_price, float(self.config.get("stop_loss_percent", 1.0)), is_long)
        take_profit = self.risk_manager.calculate_take_profit(entry_price, float(self.config.get("take_profit_percent", 2.0)), is_long)
        self.order_manager.set_stop_loss_take_profit(
            symbol=symbol,
            position_side=signal,
            entry_price=entry_price,
            stop_loss_percent=float(self.config.get("stop_loss_percent", 1.0)),
            take_profit_percent=float(self.config.get("take_profit_percent", 2.0)),
            amount=amount,
        )

        avg_cfg = self.config.get("averaging", {})
        levels = []
        if avg_cfg.get("enabled"):
            levels = self.risk_manager.calculate_custom_levels(entry_price, avg_cfg.get("levels", []), is_long)

        trailing_cfg = self.config.get("trailing", {})
        trailing = None
        if trailing_cfg.get("enabled"):
            trailing = TrailingStop(
                activation_percent=float(trailing_cfg.get("activation_percent", 1.0)),
                step_percent=float(trailing_cfg.get("step_percent", 0.5)),
                offset_percent=float(trailing_cfg.get("offset_percent", 0.8)),
                use_atr=bool(trailing_cfg.get("type", "percent") == "atr"),
                atr_multiplier=float(trailing_cfg.get("atr_multiplier", 2.0)),
            )
            trailing.initialize(entry_price)

        locked_balance = (entry_price * amount) / max(float(self.config.get("leverage", 1.0)), 1.0)
        self.active_positions[symbol] = {
            "position_id": str(order_result.get("id", "")),
            "entry_price": entry_price,
            "average_entry": entry_price,
            "total_amount": amount,
            "base_amount": amount,
            "levels_config": levels,
            "levels_filled": [],
            "is_long": is_long,
            "stop_loss": stop_loss,
            "take_profit": take_profit,
            "max_drawdown": float(avg_cfg.get("max_drawdown_percent", 15.0)),
            "trailing_stop": trailing,
            "locked_balance": locked_balance,
        }
        self.multi_pair_manager.add_position(symbol)
        self._notify(
            f"✅ ОТКРЫТА ПОЗИЦИЯ\nПара: {symbol}\nТип: {'LONG' if is_long else 'SHORT'}\nЦена: {entry_price:.8f}\nОбъем: {amount:.8f}",
            kind="trade",
        )
        return True

    def recalculate_with_levels(self, position_data: dict[str, Any], fill_price: float, fill_amount: float) -> None:
        """Пересчитывает среднюю цену и общий объем после усреднения."""
        old_amount = float(position_data.get("total_amount", 0.0))
        old_avg = float(position_data.get("average_entry", position_data.get("entry_price", 0.0)))
        new_amount = old_amount + fill_amount
        if new_amount <= 0:
            return
        new_avg = ((old_avg * old_amount) + (fill_price * fill_amount)) / new_amount
        position_data["total_amount"] = new_amount
        position_data["average_entry"] = new_avg

    def _close_position(self, symbol: str, reason: str, current_price: float) -> None:
        position = self.active_positions.get(symbol)
        if not position:
            return
        side = "sell" if position.get("is_long", True) else "buy"
        amount = float(position.get("total_amount", 0.0))
        if amount > 0:
            self.order_manager.place_order(symbol, side, amount, "market")
        avg_entry = float(position.get("average_entry", position.get("entry_price", current_price)))
        pnl_abs = (current_price - avg_entry) * amount if position.get("is_long", True) else (avg_entry - current_price) * amount
        pnl_pct = ((current_price - avg_entry) / avg_entry * 100.0) if position.get("is_long", True) else ((avg_entry - current_price) / avg_entry * 100.0)

        self.multi_pair_manager.remove_position(symbol)
        self.active_positions.pop(symbol, None)

        if pnl_abs >= 0:
            self._notify(f"💰 ПРИБЫЛЬ\nПара: {symbol}\nPNL: +{pnl_abs:.4f} USDT (+{pnl_pct:.2f}%)\nЦена выхода: {current_price:.8f}")
        else:
            self._notify(f"📉 УБЫТОК\nПара: {symbol}\nPNL: {pnl_abs:.4f} USDT ({pnl_pct:.2f}%)\nЦена выхода: {current_price:.8f}")

        self.logger.info("Позиция %s закрыта (%s)", symbol, reason)

    def check_custom_average_signals(self) -> None:
        """Проверяет уровни усреднения, TP/SL и трейлинг-стоп."""
        for symbol, position in list(self.active_positions.items()):
            ticker = self.exchange.fetch_ticker(symbol)
            if not ticker or not ticker.get("last"):
                continue
            current_price = float(ticker["last"])
            is_long = bool(position.get("is_long", True))

            trailing = position.get("trailing_stop")
            if trailing:
                updated = trailing.update(current_price, is_long=is_long, atr_value=None)
                if updated:
                    self.logger.info("Трейлинг обновлен %s: stop=%s", symbol, updated)
                if trailing.should_stop(current_price, is_long=is_long):
                    self._close_position(symbol, "trailing_stop", current_price)
                    continue

            stop_loss = float(position.get("stop_loss", 0.0))
            take_profit = float(position.get("take_profit", 0.0))
            hit_sl = current_price <= stop_loss if is_long else current_price >= stop_loss
            hit_tp = current_price >= take_profit if is_long else current_price <= take_profit
            if hit_sl or hit_tp:
                self._close_position(symbol, "SL/TP", current_price)
                continue

            entry = float(position.get("entry_price", 0.0))
            if entry > 0:
                dd = ((entry - current_price) / entry * 100.0) if is_long else ((current_price - entry) / entry * 100.0)
                if dd >= float(position.get("max_drawdown", 15.0)):
                    self._close_position(symbol, "max_drawdown", current_price)
                    self._notify("🛑 БОТ ОСТАНОВЛЕН\nПричина: превышена максимальная просадка", kind="error")
                    continue

            levels = position.get("levels_config", [])
            next_level = next((lvl for lvl in levels if not lvl.get("filled")), None)
            if not next_level:
                continue

            trigger = current_price <= float(next_level["price"]) if is_long else current_price >= float(next_level["price"])
            if not trigger:
                continue

            fill_amount = float(position.get("base_amount", 0.0)) * float(next_level.get("multiplier", 1.0))
            if self.config.get("signals_only", True):
                next_level["filled"] = True
                position["levels_filled"].append(next_level["level"])
                continue

            side = "buy" if is_long else "sell"
            order = self.order_manager.place_order(symbol, side, fill_amount, "market")
            if not order:
                continue

            next_level["filled"] = True
            position["levels_filled"].append(next_level["level"])
            self.recalculate_with_levels(position, current_price, fill_amount)
            self.logger.info("Усреднение: %s уровень %s", symbol, next_level["level"])
            self._notify(
                f"🔄 УСРЕДНЕНИЕ\nПара: {symbol}\nУровень: {next_level['level']}\nЦена: {current_price:.8f}\nОбъем: {fill_amount:.8f}\nНовая средняя: {position['average_entry']:.8f}"
            )

    def run_once(self) -> None:
        """Одна итерация цикла: все пары + сопровождение позиций."""
        symbols = self.config.get("symbols", [])
        timeframe = self.config.get("timeframe", "5m")
        for symbol in symbols:
            signal = self.check_signals(symbol, timeframe)
            if signal and self._can_open_position(symbol):
                self.execute_signal(symbol, signal)

        self.check_custom_average_signals()

    def start_loop(self, interval_seconds: int = 10) -> None:
        """Запускает торговый цикл в фоне."""
        if self._running:
            return
        self._running = True

        def worker() -> None:
            self.logger.info("Торговый цикл запущен.")
            while self._running:
                try:
                    self.run_once()
                except Exception as exc:
                    self.logger.exception("Ошибка торгового цикла: %s", exc)
                    self._notify(f"⚠️ ОШИБКА\n{exc}", kind="error")
                time.sleep(max(interval_seconds, 1))
            self.logger.info("Торговый цикл остановлен.")

        self._thread = threading.Thread(target=worker, daemon=True)
        self._thread.start()

    def stop_loop(self) -> None:
        """Останавливает торговый цикл."""
        self._running = False
