"""Модуль риск-менеджмента для расчета параметров сделки."""

from __future__ import annotations


class RiskManager:
    """Класс расчета размера позиции и контрольных уровней."""

    @staticmethod
    def calculate_position_size(balance: float, risk_percent: float, leverage: float, current_price: float) -> float:
        """Возвращает количество монет исходя из доли баланса и плеча."""
        if balance <= 0 or current_price <= 0:
            return 0.0
        risk_fraction = max(risk_percent, 0.0) / 100.0
        notional = balance * risk_fraction * max(leverage, 1.0)
        return max(notional / current_price, 0.0)

    @staticmethod
    def calculate_stop_loss(entry_price: float, stop_loss_percent: float, is_long: bool) -> float:
        """Возвращает цену stop-loss для long/short позиции."""
        p = max(stop_loss_percent, 0.0) / 100.0
        return entry_price * (1 - p) if is_long else entry_price * (1 + p)

    @staticmethod
    def calculate_take_profit(entry_price: float, take_profit_percent: float, is_long: bool) -> float:
        """Возвращает цену take-profit для long/short позиции."""
        p = max(take_profit_percent, 0.0) / 100.0
        return entry_price * (1 + p) if is_long else entry_price * (1 - p)

    @staticmethod
    def check_max_drawdown(current_balance: float, initial_balance: float, max_drawdown_percent: float) -> bool:
        """Проверяет превышение максимальной просадки в процентах."""
        if initial_balance <= 0:
            return False
        dd = ((initial_balance - current_balance) / initial_balance) * 100.0
        return dd >= max(max_drawdown_percent, 0.0)

    @staticmethod
    def validate_levels(levels_config: list[dict]) -> bool:
        """Проверяет, что уровни без дублей и упорядочены по удалению от входа."""
        if not levels_config:
            return True

        steps = [float(level.get("step_percent", level.get("step", 0.0))) for level in levels_config]
        if len(set(steps)) != len(steps):
            return False

        first_sign = 1 if steps[0] > 0 else -1
        if first_sign == 0:
            return False

        for step in steps:
            if step == 0 or (1 if step > 0 else -1) != first_sign:
                return False

        abs_steps = [abs(s) for s in steps]
        return abs_steps == sorted(abs_steps)

    @staticmethod
    def calculate_custom_levels(entry_price: float, levels_config: list[dict], is_long: bool) -> list[dict]:
        """Возвращает список уровней усреднения с рассчитанными ценами."""
        if entry_price <= 0 or not levels_config:
            return []

        prepared: list[dict] = []
        for idx, level in enumerate(levels_config, start=1):
            step = float(level.get("step_percent", level.get("step", 0.0)))
            multiplier = float(level.get("multiplier", 1.0))

            if is_long and step > 0:
                step = -step
            if not is_long and step < 0:
                step = abs(step)

            price = entry_price * (1 + (step / 100.0))
            prepared.append(
                {
                    "level": idx,
                    "price": float(price),
                    "step": float(step),
                    "multiplier": float(multiplier),
                    "filled": bool(level.get("filled", False)),
                }
            )

        prepared.sort(key=lambda x: abs(x["step"]))
        return prepared


class TrailingStop:
    """Управляет логикой трейлинг-стопа для long/short позиции."""

    def __init__(
        self,
        activation_percent: float,
        step_percent: float,
        offset_percent: float,
        use_atr: bool = False,
        atr_multiplier: float = 2.0,
    ) -> None:
        self.activation_percent = max(float(activation_percent), 0.0)
        self.step_percent = max(float(step_percent), 0.0)
        self.offset_percent = max(float(offset_percent), 0.0)
        self.use_atr = use_atr
        self.atr_multiplier = max(float(atr_multiplier), 0.1)
        self.extreme_price = 0.0
        self.entry_price = 0.0
        self.activated = False
        self.current_stop = 0.0

    def initialize(self, entry_price: float) -> None:
        """Инициализирует трейлинг на цене входа."""
        self.entry_price = float(entry_price)
        self.extreme_price = float(entry_price)
        self.activated = False
        self.current_stop = 0.0

    def update(self, current_price: float, is_long: bool = True, atr_value: float | None = None) -> float | None:
        """Обновляет уровень стопа и возвращает новое значение при изменении."""
        price = float(current_price)
        if price <= 0 or self.entry_price <= 0:
            return None

        if is_long:
            if price > self.extreme_price:
                self.extreme_price = price
            profit_percent = ((self.extreme_price - self.entry_price) / self.entry_price) * 100.0
            if not self.activated and profit_percent >= self.activation_percent:
                self.activated = True

            if not self.activated:
                return None

            if self.use_atr and atr_value and atr_value > 0:
                candidate_stop = self.extreme_price - (atr_value * self.atr_multiplier)
            else:
                candidate_stop = self.extreme_price * (1 - self.offset_percent / 100.0)

            if self.current_stop == 0 or candidate_stop > self.current_stop:
                if self.current_stop == 0:
                    self.current_stop = candidate_stop
                    return self.current_stop
                move_percent = ((candidate_stop - self.current_stop) / self.current_stop) * 100.0
                if move_percent >= self.step_percent:
                    self.current_stop = candidate_stop
                    return self.current_stop
            return None

        if self.extreme_price == 0 or price < self.extreme_price:
            self.extreme_price = price
        profit_percent = ((self.entry_price - self.extreme_price) / self.entry_price) * 100.0
        if not self.activated and profit_percent >= self.activation_percent:
            self.activated = True

        if not self.activated:
            return None

        if self.use_atr and atr_value and atr_value > 0:
            candidate_stop = self.extreme_price + (atr_value * self.atr_multiplier)
        else:
            candidate_stop = self.extreme_price * (1 + self.offset_percent / 100.0)

        if self.current_stop == 0 or candidate_stop < self.current_stop:
            if self.current_stop == 0:
                self.current_stop = candidate_stop
                return self.current_stop
            move_percent = ((self.current_stop - candidate_stop) / self.current_stop) * 100.0
            if move_percent >= self.step_percent:
                self.current_stop = candidate_stop
                return self.current_stop
        return None

    def should_stop(self, current_price: float, is_long: bool = True) -> bool:
        """Проверяет факт срабатывания трейлинг-стопа."""
        if not self.activated or self.current_stop <= 0:
            return False
        return current_price <= self.current_stop if is_long else current_price >= self.current_stop
