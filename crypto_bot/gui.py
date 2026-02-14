"""Графический интерфейс торгового бота на Tkinter."""

from __future__ import annotations

import queue
import threading
import tkinter as tk
from datetime import datetime, timezone
from tkinter import messagebox, scrolledtext, ttk

from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
from matplotlib.figure import Figure

from config import (
    DEFAULT_PAIRS,
    DEFAULT_SETTINGS,
    MEXC_API_KEY,
    MEXC_SECRET_KEY,
    TELEGRAM_BOT_TOKEN,
    TELEGRAM_CHAT_ID,
    TELEGRAM_ENABLED,
    USE_TESTNET,
    load_user_settings,
    save_user_settings,
)
from exchange import MEXCExchange
from indicators import add_bollinger, add_macd, add_rsi, add_volume_ratio
from logger import setup_logger
from notifier import TelegramNotifier
from orders import OrderManager
from risk import RiskManager
from trader import TradingBot


class AverageLevelsFrame(ttk.LabelFrame):
    """Динамический UI-блок для настройки уровней усреднения."""

    def __init__(self, parent: tk.Widget, on_change_callback=None) -> None:
        super().__init__(parent, text="УСРЕДНЕНИЕ (СТРАХОВОЧНЫЕ ОРДЕРА)")
        self.on_change_callback = on_change_callback
        self.levels: list[dict] = []

        self.enabled_var = tk.BooleanVar(value=False)
        self.max_drawdown_var = tk.DoubleVar(value=15.0)

        top = ttk.Frame(self)
        top.pack(fill="x", padx=5, pady=5)
        ttk.Checkbutton(top, text="Включить усреднение", variable=self.enabled_var, command=self._trigger_change).pack(side="left")
        ttk.Button(top, text="➕ ДОБАВИТЬ УРОВЕНЬ", command=self.add_level).pack(side="left", padx=8)

        self.levels_container = ttk.Frame(self)
        self.levels_container.pack(fill="x", padx=5)

        bottom = ttk.Frame(self)
        bottom.pack(fill="x", padx=5, pady=5)
        ttk.Label(bottom, text="Макс. просадка для усреднения, %").pack(side="left")
        ttk.Entry(bottom, textvariable=self.max_drawdown_var, width=8).pack(side="left", padx=5)

        self.preview = scrolledtext.ScrolledText(self, height=5, wrap="word")
        self.preview.pack(fill="both", expand=True, padx=5, pady=(0, 5))
        self.preview.configure(state="disabled")

    def _trigger_change(self) -> None:
        if self.on_change_callback:
            self.on_change_callback()

    def set_editable(self, editable: bool) -> None:
        """Блокирует/разблокирует редактирование."""
        state = "normal" if editable else "disabled"
        for level in self.levels:
            level["step_entry"].configure(state=state)
            level["mult_entry"].configure(state=state)
            level["delete_btn"].configure(state=state)

    def add_level(self, step: float = -2.0, multiplier: float = 1.5) -> None:
        """Добавляет новый уровень."""
        row = ttk.Frame(self.levels_container)
        row.pack(fill="x", pady=2)

        ttk.Label(row, text="Шаг %").pack(side="left")
        step_var = tk.StringVar(value=str(step))
        step_entry = ttk.Entry(row, textvariable=step_var, width=8)
        step_entry.pack(side="left", padx=3)

        ttk.Label(row, text="Множитель").pack(side="left", padx=(10, 0))
        mult_var = tk.StringVar(value=str(multiplier))
        mult_entry = ttk.Entry(row, textvariable=mult_var, width=8)
        mult_entry.pack(side="left", padx=3)

        delete_btn = ttk.Button(row, text="X", command=lambda: self.delete_level(row))
        delete_btn.pack(side="right")

        step_var.trace_add("write", lambda *_: self._trigger_change())
        mult_var.trace_add("write", lambda *_: self._trigger_change())

        self.levels.append(
            {
                "frame": row,
                "step_var": step_var,
                "mult_var": mult_var,
                "step_entry": step_entry,
                "mult_entry": mult_entry,
                "delete_btn": delete_btn,
            }
        )
        self._trigger_change()

    def delete_level(self, frame: tk.Widget) -> None:
        """Удаляет уровень."""
        for item in list(self.levels):
            if item["frame"] == frame:
                self.levels.remove(item)
                frame.destroy()
                break
        self._trigger_change()

    def get_levels_config(self) -> list[dict]:
        """Собирает конфиг уровней из UI."""
        result = []
        for lvl in self.levels:
            try:
                result.append(
                    {
                        "step_percent": float(lvl["step_var"].get()),
                        "multiplier": float(lvl["mult_var"].get()),
                    }
                )
            except ValueError:
                continue
        return result

    def set_levels_config(self, levels: list[dict]) -> None:
        """Восстанавливает уровни из сохраненного конфига."""
        for lvl in list(self.levels):
            lvl["frame"].destroy()
        self.levels.clear()
        for level in levels:
            self.add_level(
                step=float(level.get("step_percent", level.get("step", -2.0))),
                multiplier=float(level.get("multiplier", 1.5)),
            )

    def update_preview(self, current_price: float | None, risk_manager: RiskManager) -> None:
        """Обновляет информационное поле расчета уровней."""
        lines: list[str] = []
        levels = self.get_levels_config()
        if not levels:
            lines.append("Уровни не добавлены")
        elif current_price is None:
            lines.append("Текущая цена: недоступна")
        else:
            lines.append(f"Текущая цена: {current_price:.8f}")
            is_long = float(levels[0].get("step_percent", 0.0)) < 0
            if not risk_manager.validate_levels(levels):
                lines.append("⚠️ Некорректные шаги: проверьте дубли/порядок")
            for row in risk_manager.calculate_custom_levels(current_price, levels, is_long):
                lines.append(
                    f"Уровень {row['level']}: цена {row['price']:.8f} (шаг {row['step']}%, объем x{row['multiplier']})"
                )

        self.preview.configure(state="normal")
        self.preview.delete("1.0", tk.END)
        self.preview.insert(tk.END, "\n".join(lines))
        self.preview.configure(state="disabled")


class TradingBotGUI(tk.Tk):
    """Главное окно приложения."""

    def __init__(self) -> None:
        super().__init__()
        self.title("Crypto Bot MEXC (Spot/Futures)")
        self.geometry("1300x930")

        self.log_queue: queue.Queue[str] = queue.Queue()
        self.logger = setup_logger(gui_queue=self.log_queue)
        saved = load_user_settings()

        self.api_key_var = tk.StringVar(value=MEXC_API_KEY)
        self.secret_key_var = tk.StringVar(value=MEXC_SECRET_KEY)
        self.use_testnet_var = tk.BooleanVar(value=USE_TESTNET)

        self.telegram_enabled_var = tk.BooleanVar(value=saved.get("telegram_enabled", TELEGRAM_ENABLED))
        self.telegram_token_var = tk.StringVar(value=saved.get("telegram_token", TELEGRAM_BOT_TOKEN))
        self.telegram_chat_id_var = tk.StringVar(value=saved.get("telegram_chat_id", TELEGRAM_CHAT_ID))
        self.telegram_trades_var = tk.BooleanVar(value=saved.get("telegram_trades", True))
        self.telegram_errors_var = tk.BooleanVar(value=saved.get("telegram_errors", True))
        self.telegram_daily_var = tk.BooleanVar(value=saved.get("telegram_daily", False))

        self.market_type_var = tk.StringVar(value=saved.get("market_type", DEFAULT_SETTINGS["default_market_type"]))
        self.timeframe_var = tk.StringVar(value=saved.get("timeframe", DEFAULT_SETTINGS["default_timeframe"]))
        self.new_pair_var = tk.StringVar()

        self.rsi_enabled = tk.BooleanVar(value=saved.get("rsi_enabled", True))
        self.rsi_period = tk.IntVar(value=saved.get("rsi_period", 14))
        self.macd_enabled = tk.BooleanVar(value=saved.get("macd_enabled", True))
        self.macd_fast = tk.IntVar(value=saved.get("macd_fast", 12))
        self.macd_slow = tk.IntVar(value=saved.get("macd_slow", 26))
        self.macd_signal = tk.IntVar(value=saved.get("macd_signal", 9))
        self.bb_enabled = tk.BooleanVar(value=saved.get("bb_enabled", True))
        self.bb_period = tk.IntVar(value=saved.get("bb_period", 20))
        self.bb_std = tk.DoubleVar(value=saved.get("bb_std", 2.0))
        self.vol_enabled = tk.BooleanVar(value=saved.get("vol_enabled", True))
        self.vol_period = tk.IntVar(value=saved.get("vol_period", 20))

        self.trade_pair_var = tk.StringVar(value=saved.get("trade_pair", DEFAULT_PAIRS[0]))
        self.trade_risk_percent_var = tk.DoubleVar(value=saved.get("trade_risk_percent", 20.0))
        self.trade_leverage_var = tk.IntVar(value=saved.get("trade_leverage", 1))
        self.trade_tp_var = tk.DoubleVar(value=saved.get("trade_tp", 2.0))
        self.trade_sl_var = tk.DoubleVar(value=saved.get("trade_sl", 1.0))
        self.trade_interval_var = tk.IntVar(value=saved.get("trade_interval", 10))

        self.entry_order_type_var = tk.StringVar(value=saved.get("entry_order_type", "market"))
        self.limit_deviation_var = tk.DoubleVar(value=saved.get("limit_deviation", -0.1))
        self.limit_retry_var = tk.BooleanVar(value=saved.get("limit_retry", True))
        self.limit_interval_var = tk.IntVar(value=saved.get("limit_interval", 5))
        self.limit_max_attempts_var = tk.IntVar(value=saved.get("limit_max_attempts", 0))
        self.limit_fallback_var = tk.StringVar(value=saved.get("limit_fallback", "cancel"))

        self.trailing_enabled_var = tk.BooleanVar(value=saved.get("trailing_enabled", False))
        self.trailing_activation_var = tk.DoubleVar(value=saved.get("trailing_activation", 1.0))
        self.trailing_step_var = tk.DoubleVar(value=saved.get("trailing_step", 0.5))
        self.trailing_offset_var = tk.DoubleVar(value=saved.get("trailing_offset", 0.8))
        self.trailing_type_var = tk.StringVar(value=saved.get("trailing_type", "percent"))
        self.trailing_atr_mult_var = tk.DoubleVar(value=saved.get("trailing_atr_mult", 2.0))

        self.multipair_enabled_var = tk.BooleanVar(value=saved.get("multipair_enabled", True))
        self.max_positions_var = tk.IntVar(value=saved.get("max_positions", 2))
        self.capital_mode_var = tk.StringVar(value=saved.get("capital_mode", "fixed"))

        self.signals_only_var = tk.BooleanVar(value=saved.get("signals_only", True))
        self.status_balance_var = tk.StringVar(value="USDT: --")
        self.status_connection_var = tk.StringVar(value="Нет данных")
        self.last_price_var = tk.StringVar(value="--")

        self.balance_history: list[float] = []
        self.win_count = 0
        self.loss_count = 0
        self.pnl_history: list[float] = []
        self.max_drawdown_value = 0.0

        self.notifier: TelegramNotifier | None = None
        if self.telegram_enabled_var.get() and self.telegram_token_var.get().strip() and self.telegram_chat_id_var.get().strip():
            self.notifier = TelegramNotifier(self.telegram_token_var.get().strip(), self.telegram_chat_id_var.get().strip())

        self.exchange = self._build_exchange()
        self.risk_manager = RiskManager()
        self.order_manager = OrderManager(self.exchange, logger=self.logger)
        self.trading_bot: TradingBot | None = None

        self._build_layout(saved)
        self.after(200, self._poll_log_queue)
        self.after(1000, self._periodic_status_update)

    def _build_exchange(self) -> MEXCExchange:
        return MEXCExchange(
            api_key=self.api_key_var.get().strip(),
            secret_key=self.secret_key_var.get().strip(),
            use_testnet=self.use_testnet_var.get(),
            market_type=self.market_type_var.get(),
            logger=self.logger,
        )

    def _rebuild_services(self) -> None:
        self.exchange = self._build_exchange()
        self.order_manager = OrderManager(self.exchange, logger=self.logger)

    def _save_settings(self) -> None:
        data = {
            "market_type": self.market_type_var.get(),
            "timeframe": self.timeframe_var.get(),
            "trade_pair": self.trade_pair_var.get(),
            "trade_risk_percent": self.trade_risk_percent_var.get(),
            "trade_leverage": self.trade_leverage_var.get(),
            "trade_tp": self.trade_tp_var.get(),
            "trade_sl": self.trade_sl_var.get(),
            "trade_interval": self.trade_interval_var.get(),
            "entry_order_type": self.entry_order_type_var.get(),
            "limit_deviation": self.limit_deviation_var.get(),
            "limit_retry": self.limit_retry_var.get(),
            "limit_interval": self.limit_interval_var.get(),
            "limit_max_attempts": self.limit_max_attempts_var.get(),
            "limit_fallback": self.limit_fallback_var.get(),
            "signals_only": self.signals_only_var.get(),
            "rsi_enabled": self.rsi_enabled.get(),
            "rsi_period": self.rsi_period.get(),
            "macd_enabled": self.macd_enabled.get(),
            "macd_fast": self.macd_fast.get(),
            "macd_slow": self.macd_slow.get(),
            "macd_signal": self.macd_signal.get(),
            "bb_enabled": self.bb_enabled.get(),
            "bb_period": self.bb_period.get(),
            "bb_std": self.bb_std.get(),
            "vol_enabled": self.vol_enabled.get(),
            "vol_period": self.vol_period.get(),
            "averaging_enabled": self.average_levels_frame.enabled_var.get(),
            "averaging_levels": self.average_levels_frame.get_levels_config(),
            "averaging_max_drawdown": self.average_levels_frame.max_drawdown_var.get(),
            "trailing_enabled": self.trailing_enabled_var.get(),
            "trailing_activation": self.trailing_activation_var.get(),
            "trailing_step": self.trailing_step_var.get(),
            "trailing_offset": self.trailing_offset_var.get(),
            "trailing_type": self.trailing_type_var.get(),
            "trailing_atr_mult": self.trailing_atr_mult_var.get(),
            "multipair_enabled": self.multipair_enabled_var.get(),
            "max_positions": self.max_positions_var.get(),
            "capital_mode": self.capital_mode_var.get(),
            "telegram_enabled": self.telegram_enabled_var.get(),
            "telegram_token": self.telegram_token_var.get(),
            "telegram_chat_id": self.telegram_chat_id_var.get(),
            "telegram_trades": self.telegram_trades_var.get(),
            "telegram_errors": self.telegram_errors_var.get(),
            "telegram_daily": self.telegram_daily_var.get(),
        }
        save_user_settings(data)

    def _build_layout(self, saved: dict) -> None:
        notebook = ttk.Notebook(self)
        notebook.pack(fill="both", expand=True, padx=10, pady=10)

        settings_tab = ttk.Frame(notebook)
        indicators_tab = ttk.Frame(notebook)
        trading_tab = ttk.Frame(notebook)
        logs_tab = ttk.Frame(notebook)
        stats_tab = ttk.Frame(notebook)

        notebook.add(settings_tab, text="Настройки")
        notebook.add(indicators_tab, text="Индикаторы")
        notebook.add(trading_tab, text="Торговля")
        notebook.add(logs_tab, text="Логи")
        notebook.add(stats_tab, text="Статистика")

        self._build_settings_tab(settings_tab)
        self._build_indicators_tab(indicators_tab)
        self._build_trading_tab(trading_tab, saved)
        self._build_logs_tab(logs_tab)
        self._build_stats_tab(stats_tab)

        bottom = ttk.Frame(self)
        bottom.pack(fill="x", padx=10, pady=(0, 10))
        ttk.Button(bottom, text="Анализировать", command=self.analyze_market).pack(side="left")
        self.result_text = scrolledtext.ScrolledText(bottom, height=8, wrap="word")
        self.result_text.pack(fill="both", expand=True, padx=(10, 0))
        self.result_text.configure(state="disabled")

    def _build_settings_tab(self, parent: ttk.Frame) -> None:
        conn = ttk.LabelFrame(parent, text="Подключение к бирже")
        conn.pack(fill="x", padx=10, pady=8)
        ttk.Label(conn, text="API Key:").grid(row=0, column=0, sticky="w", padx=5, pady=5)
        ttk.Entry(conn, textvariable=self.api_key_var, width=60).grid(row=0, column=1, sticky="ew", padx=5, pady=5)
        ttk.Label(conn, text="Secret:").grid(row=1, column=0, sticky="w", padx=5, pady=5)
        ttk.Entry(conn, textvariable=self.secret_key_var, show="*", width=60).grid(row=1, column=1, sticky="ew", padx=5, pady=5)
        ttk.Checkbutton(conn, text="Использовать Testnet", variable=self.use_testnet_var).grid(row=2, column=0, sticky="w", padx=5, pady=5)
        ttk.Button(conn, text="Проверить подключение", command=self.check_connection).grid(row=2, column=1, sticky="e", padx=5, pady=5)
        conn.columnconfigure(1, weight=1)

        telegram = ttk.LabelFrame(parent, text="TELEGRAM")
        telegram.pack(fill="x", padx=10, pady=8)
        ttk.Checkbutton(telegram, text="Включить Telegram-уведомления", variable=self.telegram_enabled_var).grid(row=0, column=0, columnspan=2, sticky="w", padx=5, pady=3)
        ttk.Label(telegram, text="Bot Token").grid(row=1, column=0, sticky="w", padx=5)
        ttk.Entry(telegram, textvariable=self.telegram_token_var, show="*", width=60).grid(row=1, column=1, sticky="ew", padx=5, pady=3)
        ttk.Label(telegram, text="Chat ID").grid(row=2, column=0, sticky="w", padx=5)
        ttk.Entry(telegram, textvariable=self.telegram_chat_id_var, width=60).grid(row=2, column=1, sticky="ew", padx=5, pady=3)
        ttk.Button(telegram, text="Тест", command=self.test_telegram).grid(row=3, column=1, sticky="e", padx=5, pady=3)
        ttk.Checkbutton(telegram, text="Уведомления о сделках", variable=self.telegram_trades_var).grid(row=4, column=0, sticky="w", padx=5)
        ttk.Checkbutton(telegram, text="Уведомления об ошибках", variable=self.telegram_errors_var).grid(row=4, column=1, sticky="w", padx=5)
        ttk.Checkbutton(telegram, text="Ежедневный отчет (00:00 UTC)", variable=self.telegram_daily_var).grid(row=5, column=0, sticky="w", padx=5, pady=3)
        telegram.columnconfigure(1, weight=1)

        pairs = ttk.LabelFrame(parent, text="Торговые пары")
        pairs.pack(fill="both", expand=True, padx=10, pady=8)
        self.pairs_listbox = tk.Listbox(pairs, selectmode=tk.MULTIPLE, height=10)
        for pair in DEFAULT_PAIRS:
            self.pairs_listbox.insert(tk.END, pair)
        self.pairs_listbox.grid(row=0, column=0, columnspan=3, sticky="nsew", padx=5, pady=5)
        ttk.Entry(pairs, textvariable=self.new_pair_var).grid(row=1, column=0, sticky="ew", padx=5, pady=5)
        ttk.Button(pairs, text="Добавить", command=self.add_pair).grid(row=1, column=1, sticky="ew", padx=5, pady=5)
        ttk.Button(pairs, text="Удалить выбранные", command=self.remove_selected_pairs).grid(row=1, column=2, sticky="ew", padx=5, pady=5)
        ttk.Button(pairs, text="Обновить список пар", command=self.refresh_pairs).grid(row=2, column=0, columnspan=3, sticky="ew", padx=5, pady=5)
        pairs.columnconfigure(0, weight=1)
        pairs.rowconfigure(0, weight=1)

        mode_frame = ttk.LabelFrame(parent, text="Режим торговли")
        mode_frame.pack(fill="x", padx=10, pady=8)
        ttk.Radiobutton(mode_frame, text="Спот", value="spot", variable=self.market_type_var).pack(side="left", padx=5, pady=5)
        ttk.Radiobutton(mode_frame, text="Фьючерсы", value="futures", variable=self.market_type_var).pack(side="left", padx=5, pady=5)

        timeframe_frame = ttk.LabelFrame(parent, text="Таймфрейм")
        timeframe_frame.pack(fill="x", padx=10, pady=8)
        ttk.Combobox(
            timeframe_frame,
            textvariable=self.timeframe_var,
            values=["1m", "3m", "5m", "15m", "30m", "1h", "4h", "1d"],
            state="readonly",
        ).pack(fill="x", padx=5, pady=5)

    def _build_indicators_tab(self, parent: ttk.Frame) -> None:
        rows = [
            ("RSI", self.rsi_enabled, self.rsi_period),
            ("MACD fast", self.macd_enabled, self.macd_fast),
            ("MACD slow", self.macd_enabled, self.macd_slow),
            ("MACD signal", self.macd_enabled, self.macd_signal),
            ("BB период", self.bb_enabled, self.bb_period),
            ("BB отклонение", self.bb_enabled, self.bb_std),
            ("Объём период", self.vol_enabled, self.vol_period),
        ]
        for title, enabled, value in rows:
            row = ttk.Frame(parent)
            row.pack(fill="x", padx=10, pady=3)
            ttk.Label(row, text=title, width=20).pack(side="left")
            ttk.Entry(row, textvariable=value, width=10).pack(side="left", padx=5)
            ttk.Checkbutton(row, text="Использовать", variable=enabled).pack(side="left", padx=5)

    def _build_trading_tab(self, parent: ttk.Frame, saved: dict) -> None:
        status = ttk.LabelFrame(parent, text="Статус")
        status.pack(fill="x", padx=10, pady=6)
        ttk.Label(status, textvariable=self.status_balance_var, font=("Arial", 11, "bold")).pack(side="left", padx=5)
        self.connection_indicator = tk.Canvas(status, width=16, height=16, highlightthickness=0)
        self.connection_indicator.pack(side="left", padx=5)
        self.connection_indicator.create_oval(2, 2, 14, 14, fill="red", tags="conn")
        ttk.Label(status, textvariable=self.status_connection_var).pack(side="left", padx=4)
        ttk.Label(status, text="Текущая цена:").pack(side="left", padx=(12, 2))
        ttk.Label(status, textvariable=self.last_price_var).pack(side="left")
        ttk.Button(status, text="Обновить баланс", command=self.refresh_balance).pack(side="right", padx=5)

        params = ttk.LabelFrame(parent, text="Параметры сделки")
        params.pack(fill="x", padx=10, pady=6)
        ttk.Label(params, text="Пара").grid(row=0, column=0, sticky="w", padx=5)
        self.trade_pair_combo = ttk.Combobox(params, textvariable=self.trade_pair_var, state="readonly")
        self.trade_pair_combo.grid(row=0, column=1, padx=5, pady=4, sticky="ew")
        self.trade_pair_combo.bind("<<ComboboxSelected>>", lambda _: self.update_average_preview())
        self._sync_trade_pairs()

        ttk.Label(params, text="% баланса").grid(row=0, column=2, sticky="w", padx=5)
        ttk.Entry(params, textvariable=self.trade_risk_percent_var, width=8).grid(row=0, column=3, padx=5)
        ttk.Label(params, text="Плечо").grid(row=0, column=4, sticky="w", padx=5)
        self.leverage_entry = ttk.Entry(params, textvariable=self.trade_leverage_var, width=8)
        self.leverage_entry.grid(row=0, column=5, padx=5)

        ttk.Label(params, text="TP %").grid(row=1, column=0, sticky="w", padx=5)
        ttk.Entry(params, textvariable=self.trade_tp_var, width=8).grid(row=1, column=1, padx=5, sticky="w")
        ttk.Label(params, text="SL %").grid(row=1, column=2, sticky="w", padx=5)
        ttk.Entry(params, textvariable=self.trade_sl_var, width=8).grid(row=1, column=3, padx=5, sticky="w")
        ttk.Label(params, text="Интервал, сек").grid(row=1, column=4, sticky="w", padx=5)
        ttk.Entry(params, textvariable=self.trade_interval_var, width=8).grid(row=1, column=5, padx=5)
        params.columnconfigure(1, weight=1)

        mp = ttk.LabelFrame(parent, text="МУЛЬТИПАРНОСТЬ")
        mp.pack(fill="x", padx=10, pady=6)
        ttk.Checkbutton(mp, text="Торговать по нескольким парам одновременно", variable=self.multipair_enabled_var).grid(row=0, column=0, columnspan=2, sticky="w", padx=5, pady=3)
        ttk.Label(mp, text="Макс. одновременных позиций").grid(row=1, column=0, sticky="w", padx=5)
        ttk.Entry(mp, textvariable=self.max_positions_var, width=8).grid(row=1, column=1, sticky="w", padx=5)
        ttk.Radiobutton(mp, text="Фиксированный % на каждую пару", value="fixed", variable=self.capital_mode_var).grid(row=2, column=0, sticky="w", padx=5)
        ttk.Radiobutton(mp, text="Общий пул", value="pool", variable=self.capital_mode_var).grid(row=2, column=1, sticky="w", padx=5)

        order = ttk.LabelFrame(parent, text="Тип ордера для входа")
        order.pack(fill="x", padx=10, pady=6)
        ttk.Radiobutton(order, text="Маркет", value="market", variable=self.entry_order_type_var, command=self._toggle_limit_options).grid(row=0, column=0, sticky="w", padx=5)
        ttk.Radiobutton(order, text="Лимит", value="limit", variable=self.entry_order_type_var, command=self._toggle_limit_options).grid(row=0, column=1, sticky="w", padx=5)
        ttk.Label(order, text="Отклонение %").grid(row=1, column=0, sticky="w", padx=5)
        self.limit_deviation_entry = ttk.Entry(order, textvariable=self.limit_deviation_var, width=8)
        self.limit_deviation_entry.grid(row=1, column=1, sticky="w", padx=5)
        self.limit_retry_check = ttk.Checkbutton(order, text="Перевыставлять", variable=self.limit_retry_var)
        self.limit_retry_check.grid(row=1, column=2, sticky="w", padx=5)
        ttk.Label(order, text="Интервал, сек").grid(row=2, column=0, sticky="w", padx=5)
        self.limit_interval_entry = ttk.Entry(order, textvariable=self.limit_interval_var, width=8)
        self.limit_interval_entry.grid(row=2, column=1, sticky="w", padx=5)
        ttk.Label(order, text="Макс попыток").grid(row=2, column=2, sticky="w", padx=5)
        self.limit_attempts_entry = ttk.Entry(order, textvariable=self.limit_max_attempts_var, width=8)
        self.limit_attempts_entry.grid(row=2, column=3, sticky="w", padx=5)
        self.limit_fallback_cancel = ttk.Radiobutton(order, text="Отменить", value="cancel", variable=self.limit_fallback_var)
        self.limit_fallback_cancel.grid(row=3, column=0, sticky="w", padx=5)
        self.limit_fallback_market = ttk.Radiobutton(order, text="Перейти на маркет", value="market", variable=self.limit_fallback_var)
        self.limit_fallback_market.grid(row=3, column=1, sticky="w", padx=5)

        trailing = ttk.LabelFrame(parent, text="ТРЕЙЛИНГ-СТОП")
        trailing.pack(fill="x", padx=10, pady=6)
        ttk.Checkbutton(trailing, text="Включить трейлинг-стоп", variable=self.trailing_enabled_var, command=self._toggle_trailing_mode).grid(row=0, column=0, columnspan=2, sticky="w", padx=5)
        ttk.Label(trailing, text="Активация, %").grid(row=1, column=0, sticky="w", padx=5)
        ttk.Entry(trailing, textvariable=self.trailing_activation_var, width=8).grid(row=1, column=1, sticky="w", padx=5)
        ttk.Label(trailing, text="Шаг, %").grid(row=1, column=2, sticky="w", padx=5)
        ttk.Entry(trailing, textvariable=self.trailing_step_var, width=8).grid(row=1, column=3, sticky="w", padx=5)
        ttk.Label(trailing, text="Отступ, %").grid(row=1, column=4, sticky="w", padx=5)
        ttk.Entry(trailing, textvariable=self.trailing_offset_var, width=8).grid(row=1, column=5, sticky="w", padx=5)
        ttk.Radiobutton(trailing, text="По проценту", value="percent", variable=self.trailing_type_var, command=self._toggle_trailing_mode).grid(row=2, column=0, sticky="w", padx=5)
        ttk.Radiobutton(trailing, text="По ATR", value="atr", variable=self.trailing_type_var, command=self._toggle_trailing_mode).grid(row=2, column=1, sticky="w", padx=5)
        ttk.Label(trailing, text="Множитель ATR").grid(row=2, column=2, sticky="w", padx=5)
        self.trailing_atr_entry = ttk.Entry(trailing, textvariable=self.trailing_atr_mult_var, width=8)
        self.trailing_atr_entry.grid(row=2, column=3, sticky="w", padx=5)

        self.average_levels_frame = AverageLevelsFrame(parent, on_change_callback=self.update_average_preview)
        self.average_levels_frame.pack(fill="x", padx=10, pady=6)
        self.average_levels_frame.enabled_var.set(bool(saved.get("averaging_enabled", False)))
        self.average_levels_frame.max_drawdown_var.set(float(saved.get("averaging_max_drawdown", 15.0)))
        self.average_levels_frame.set_levels_config(saved.get("averaging_levels", []))

        control = ttk.LabelFrame(parent, text="Управление")
        control.pack(fill="x", padx=10, pady=6)
        ttk.Button(control, text="СТАРТ", command=self.start_trading).pack(side="left", padx=5, pady=5)
        ttk.Button(control, text="СТОП", command=self.stop_trading).pack(side="left", padx=5, pady=5)
        ttk.Button(control, text="Закрыть все позиции", command=self.close_all_positions).pack(side="left", padx=5, pady=5)
        ttk.Checkbutton(control, text="Только сигналы (без сделок)", variable=self.signals_only_var).pack(side="right", padx=5)

        self.emergency_btn = tk.Button(
            parent,
            text="⛔ ЭКСТРЕННЫЙ СТОП",
            bg="#d32f2f",
            fg="white",
            font=("Arial", 12, "bold"),
            command=self.emergency_stop,
        )
        self.emergency_btn.pack(fill="x", padx=10, pady=6)

        pos = ttk.LabelFrame(parent, text="Открытые позиции")
        pos.pack(fill="both", expand=True, padx=10, pady=6)
        self.positions_tree = ttk.Treeview(pos, columns=("symbol", "side", "entry", "amount", "pnl"), show="headings", height=7)
        for col, txt in [("symbol", "Пара"), ("side", "Тип"), ("entry", "Цена входа"), ("amount", "Объём"), ("pnl", "PNL")]:
            self.positions_tree.heading(col, text=txt)
            self.positions_tree.column(col, width=140, anchor="center")
        self.positions_tree.pack(fill="both", expand=True, padx=5, pady=5)
        ttk.Button(pos, text="Закрыть выбранную", command=self.close_selected_position).pack(anchor="e", padx=5, pady=5)

        self._toggle_limit_options()
        self._toggle_trailing_mode()
        self.update_average_preview()

    def _build_logs_tab(self, parent: ttk.Frame) -> None:
        frame = ttk.Frame(parent)
        frame.pack(fill="both", expand=True, padx=10, pady=10)
        self.logs_text = scrolledtext.ScrolledText(frame, state="disabled", wrap="word")
        self.logs_text.pack(fill="both", expand=True, side="left")
        ttk.Button(frame, text="Очистить", command=self.clear_logs).pack(side="right", padx=5)

    def _build_stats_tab(self, parent: ttk.Frame) -> None:
        top = ttk.Frame(parent)
        top.pack(fill="x", padx=10, pady=8)
        self.stats_label = tk.StringVar(value="Winrate: 0% | Avg PnL: 0 | Max DD: 0%")
        ttk.Label(top, textvariable=self.stats_label, font=("Arial", 11, "bold")).pack(side="left")

        fig = Figure(figsize=(8, 4), dpi=100)
        self.balance_ax = fig.add_subplot(111)
        self.balance_ax.set_title("Кривая баланса")
        self.balance_ax.set_xlabel("Итерация")
        self.balance_ax.set_ylabel("USDT")
        self.balance_line, = self.balance_ax.plot([], [], color="blue")

        self.canvas = FigureCanvasTkAgg(fig, master=parent)
        self.canvas.get_tk_widget().pack(fill="both", expand=True, padx=10, pady=10)

    def _toggle_limit_options(self) -> None:
        state = "normal" if self.entry_order_type_var.get() == "limit" else "disabled"
        for w in [self.limit_deviation_entry, self.limit_retry_check, self.limit_interval_entry, self.limit_attempts_entry, self.limit_fallback_cancel, self.limit_fallback_market]:
            w.configure(state=state)

    def _toggle_trailing_mode(self) -> None:
        self.trailing_atr_entry.configure(state="normal" if self.trailing_type_var.get() == "atr" else "disabled")

    def _set_connection_status(self, connected: bool, text: str) -> None:
        self.status_connection_var.set(text)
        self.connection_indicator.itemconfig("conn", fill="green" if connected else "red")

    def _poll_log_queue(self) -> None:
        while True:
            try:
                message = self.log_queue.get_nowait()
            except queue.Empty:
                break
            self.logs_text.configure(state="normal")
            self.logs_text.insert(tk.END, message + "\n")
            self.logs_text.see(tk.END)
            self.logs_text.configure(state="disabled")
        self.after(200, self._poll_log_queue)

    def _periodic_status_update(self) -> None:
        self.refresh_balance(background=True)
        self.refresh_positions(background=True)
        self.update_average_preview(background=True)
        self._daily_report_check()
        self.after(10_000, self._periodic_status_update)

    def _daily_report_check(self) -> None:
        if not self.notifier or not self.telegram_daily_var.get():
            return
        now = datetime.now(timezone.utc)
        if now.hour == 0 and now.minute == 0:
            self.notifier.send(
                f"📊 Ежедневный отчет\nWin: {self.win_count}\nLoss: {self.loss_count}\nMaxDD: {self.max_drawdown_value:.2f}%"
            )

    def _sync_trade_pairs(self) -> None:
        values = list(self.pairs_listbox.get(0, tk.END)) if hasattr(self, "pairs_listbox") else DEFAULT_PAIRS
        self.trade_pair_combo["values"] = values
        if values and self.trade_pair_var.get() not in values:
            self.trade_pair_var.set(values[0])

    def clear_logs(self) -> None:
        self.logs_text.configure(state="normal")
        self.logs_text.delete("1.0", tk.END)
        self.logs_text.configure(state="disabled")

    def test_telegram(self) -> None:
        token = self.telegram_token_var.get().strip()
        chat_id = self.telegram_chat_id_var.get().strip()
        if not token or not chat_id:
            messagebox.showwarning("Telegram", "Введите token и chat_id")
            return
        notifier = TelegramNotifier(token, chat_id)
        ok = notifier.send_sync("✅ Тестовое сообщение от crypto_bot")
        notifier.stop()
        messagebox.showinfo("Telegram", "Отправлено" if ok else "Ошибка отправки")

    def update_average_preview(self, background: bool = False) -> None:
        def worker() -> None:
            symbol = self.trade_pair_var.get().strip()
            current_price = None
            if symbol:
                ticker = self.exchange.fetch_ticker(symbol)
                if ticker and ticker.get("last"):
                    current_price = float(ticker["last"])
            self.after(0, lambda: self._apply_average_preview(current_price))

        if background:
            threading.Thread(target=worker, daemon=True).start()
        else:
            worker()

    def _apply_average_preview(self, price: float | None) -> None:
        if price is not None:
            self.last_price_var.set(f"{price:.8f}")
        self.average_levels_frame.update_preview(price, self.risk_manager)

    def add_pair(self) -> None:
        pair = self.new_pair_var.get().strip().upper()
        if pair and pair not in self.pairs_listbox.get(0, tk.END):
            self.pairs_listbox.insert(tk.END, pair)
        self.new_pair_var.set("")
        self._sync_trade_pairs()

    def remove_selected_pairs(self) -> None:
        for idx in reversed(self.pairs_listbox.curselection()):
            self.pairs_listbox.delete(idx)
        self._sync_trade_pairs()

    def refresh_pairs(self) -> None:
        def worker() -> None:
            self._rebuild_services()
            symbols = self.exchange.fetch_markets()
            self.after(0, self._apply_refreshed_pairs, symbols)

        threading.Thread(target=worker, daemon=True).start()

    def _apply_refreshed_pairs(self, symbols: list[str] | None) -> None:
        if not symbols:
            messagebox.showwarning("Пары", "Не удалось обновить список")
            return
        existing = set(self.pairs_listbox.get(0, tk.END))
        for s in symbols:
            if s not in existing:
                self.pairs_listbox.insert(tk.END, s)
        self._sync_trade_pairs()

    def check_connection(self) -> None:
        def worker() -> None:
            self._rebuild_services()
            ok, bal = self.exchange.test_connection()
            self.after(0, self._show_connection_result, ok, bal)

        threading.Thread(target=worker, daemon=True).start()

    def _show_connection_result(self, ok: bool, usdt_balance: float | None) -> None:
        if ok:
            self.status_balance_var.set(f"USDT: {usdt_balance}")
            self._set_connection_status(True, "Подключено")
            messagebox.showinfo("MEXC", f"Подключение успешно. USDT={usdt_balance}")
        else:
            self._set_connection_status(False, "Ошибка")
            messagebox.showerror("MEXC", "Ошибка подключения")

    def refresh_balance(self, background: bool = False) -> None:
        def worker() -> None:
            self._rebuild_services()
            bal = self.exchange.fetch_balance() or {}
            usdt = float((bal.get("total") or {}).get("USDT") or 0.0)
            self.after(0, lambda: self._apply_balance(usdt))

        if background:
            threading.Thread(target=worker, daemon=True).start()
        else:
            worker()

    def _apply_balance(self, usdt: float) -> None:
        self.status_balance_var.set(f"USDT: {usdt:.4f}")
        self._set_connection_status(True, "Подключено")
        self.balance_history.append(usdt)
        peak = max(self.balance_history) if self.balance_history else usdt
        if peak > 0:
            self.max_drawdown_value = max(self.max_drawdown_value, ((peak - usdt) / peak) * 100.0)
        self._refresh_stats_plot()

    def _refresh_stats_plot(self) -> None:
        x = list(range(1, len(self.balance_history) + 1))
        self.balance_line.set_data(x, self.balance_history)
        self.balance_ax.relim()
        self.balance_ax.autoscale_view()
        self.canvas.draw_idle()

        total = self.win_count + self.loss_count
        winrate = (self.win_count / total * 100.0) if total > 0 else 0.0
        avg_pnl = (sum(self.pnl_history) / len(self.pnl_history)) if self.pnl_history else 0.0
        self.stats_label.set(f"Winrate: {winrate:.1f}% | Avg PnL: {avg_pnl:.4f} | Max DD: {self.max_drawdown_value:.2f}%")

    def refresh_positions(self, background: bool = False) -> None:
        def worker() -> None:
            self._rebuild_services()
            positions = self.exchange.fetch_open_positions() or []
            self.after(0, lambda: self._apply_positions(positions))

        if background:
            threading.Thread(target=worker, daemon=True).start()
        else:
            worker()

    def _apply_positions(self, positions) -> None:
        for item in self.positions_tree.get_children():
            self.positions_tree.delete(item)
        for p in positions:
            amount = float(p.get("contracts") or p.get("positionAmt") or 0.0)
            if amount == 0:
                continue
            self.positions_tree.insert("", "end", values=(p.get("symbol", ""), "лонг" if amount > 0 else "шорт", p.get("entryPrice") or p.get("average") or 0, abs(amount), p.get("unrealizedPnl") or p.get("unrealizedProfit") or 0))

    def _build_trader_config(self) -> dict:
        selected = [self.pairs_listbox.get(i) for i in self.pairs_listbox.curselection()] or [self.trade_pair_var.get()]
        levels = self.average_levels_frame.get_levels_config()
        max_positions = int(self.max_positions_var.get()) if self.multipair_enabled_var.get() else 1
        return {
            "symbols": selected,
            "timeframe": self.timeframe_var.get(),
            "ohlcv_limit": DEFAULT_SETTINGS["ohlcv_limit"],
            "risk_percent": float(self.trade_risk_percent_var.get()),
            "leverage": float(self.trade_leverage_var.get()) if self.market_type_var.get() == "futures" else 1.0,
            "take_profit_percent": float(self.trade_tp_var.get()),
            "stop_loss_percent": float(self.trade_sl_var.get()),
            "signals_only": self.signals_only_var.get(),
            "entry_order_type": self.entry_order_type_var.get(),
            "limit_deviation_percent": float(self.limit_deviation_var.get()),
            "limit_retry": self.limit_retry_var.get(),
            "limit_interval_sec": int(self.limit_interval_var.get()),
            "limit_max_attempts": int(self.limit_max_attempts_var.get()),
            "limit_fallback": self.limit_fallback_var.get(),
            "max_positions": max_positions,
            "capital_mode": self.capital_mode_var.get(),
            "trailing": {
                "enabled": self.trailing_enabled_var.get(),
                "activation_percent": float(self.trailing_activation_var.get()),
                "step_percent": float(self.trailing_step_var.get()),
                "offset_percent": float(self.trailing_offset_var.get()),
                "type": self.trailing_type_var.get(),
                "atr_multiplier": float(self.trailing_atr_mult_var.get()),
            },
            "averaging": {
                "enabled": self.average_levels_frame.enabled_var.get(),
                "levels": levels,
                "max_drawdown_percent": float(self.average_levels_frame.max_drawdown_var.get()),
            },
            "telegram_trades": self.telegram_trades_var.get(),
            "telegram_errors": self.telegram_errors_var.get(),
            "indicators": {
                "rsi": {"enabled": self.rsi_enabled.get(), "period": int(self.rsi_period.get())},
                "macd": {"enabled": self.macd_enabled.get(), "fast": int(self.macd_fast.get()), "slow": int(self.macd_slow.get()), "signal": int(self.macd_signal.get())},
                "bollinger": {"enabled": self.bb_enabled.get(), "period": int(self.bb_period.get()), "std_dev": float(self.bb_std.get())},
                "volume": {"enabled": self.vol_enabled.get(), "period": int(self.vol_period.get())},
            },
        }

    def start_trading(self) -> None:
        cfg = self._build_trader_config()
        if cfg["averaging"]["enabled"] and cfg["averaging"]["levels"] and not self.risk_manager.validate_levels(cfg["averaging"]["levels"]):
            messagebox.showerror("Усреднение", "Проверьте уровни")
            return

        if self.telegram_enabled_var.get() and self.telegram_token_var.get().strip() and self.telegram_chat_id_var.get().strip():
            if not self.notifier:
                self.notifier = TelegramNotifier(self.telegram_token_var.get().strip(), self.telegram_chat_id_var.get().strip())

        self._rebuild_services()
        self.trading_bot = TradingBot(self.exchange, cfg, self.risk_manager, self.order_manager, self.logger, notifier=self.notifier)
        self.trading_bot.start_loop(interval_seconds=int(self.trade_interval_var.get()))
        self.average_levels_frame.set_editable(False)
        self._save_settings()

    def stop_trading(self) -> None:
        if self.trading_bot:
            self.trading_bot.stop_loop()
        self.average_levels_frame.set_editable(True)

        def worker() -> None:
            for o in self.exchange.fetch_open_orders() or []:
                if o.get("id") and o.get("symbol"):
                    self.exchange.cancel_order(o["id"], o["symbol"])

        threading.Thread(target=worker, daemon=True).start()

    def emergency_stop(self) -> None:
        """Экстренно останавливает торговлю и закрывает всё."""
        self.stop_trading()

        def worker() -> None:
            for p in self.exchange.fetch_open_positions() or []:
                symbol = p.get("symbol")
                amount = float(p.get("contracts") or p.get("positionAmt") or 0.0)
                if symbol and amount != 0:
                    self.exchange.create_market_order(symbol, "sell" if amount > 0 else "buy", abs(amount))
            for o in self.exchange.fetch_open_orders() or []:
                if o.get("id") and o.get("symbol"):
                    self.exchange.cancel_order(o["id"], o["symbol"])
            if self.notifier:
                self.notifier.send("⚠️ ЭКСТРЕННЫЙ СТОП: все позиции закрыты")

        threading.Thread(target=worker, daemon=True).start()

    def close_all_positions(self) -> None:
        def worker() -> None:
            for p in self.exchange.fetch_open_positions() or []:
                symbol = p.get("symbol")
                amount = float(p.get("contracts") or p.get("positionAmt") or 0.0)
                if symbol and amount != 0:
                    self.exchange.create_market_order(symbol, "sell" if amount > 0 else "buy", abs(amount))
            self.after(0, lambda: self.refresh_positions(background=True))

        threading.Thread(target=worker, daemon=True).start()

    def close_selected_position(self) -> None:
        selected = self.positions_tree.selection()
        if not selected:
            return
        symbol, side_text, _entry, amount, _pnl = self.positions_tree.item(selected[0], "values")

        def worker() -> None:
            self.exchange.create_market_order(symbol, "sell" if side_text == "лонг" else "buy", float(amount))
            self.after(0, lambda: self.refresh_positions(background=True))

        threading.Thread(target=worker, daemon=True).start()

    def analyze_market(self) -> None:
        selection = self.pairs_listbox.curselection()
        if not selection:
            messagebox.showwarning("Анализ", "Выберите пару")
            return
        symbol = self.pairs_listbox.get(selection[0])

        def worker() -> None:
            self._rebuild_services()
            df = self.exchange.fetch_ohlcv(symbol, self.timeframe_var.get(), DEFAULT_SETTINGS["ohlcv_limit"])
            if df is None or df.empty:
                self.after(0, lambda: messagebox.showerror("Анализ", "Не удалось получить свечи"))
                return
            if self.rsi_enabled.get():
                df = add_rsi(df, self.rsi_period.get())
            if self.macd_enabled.get():
                df = add_macd(df, self.macd_fast.get(), self.macd_slow.get(), self.macd_signal.get())
            if self.bb_enabled.get():
                df = add_bollinger(df, self.bb_period.get(), self.bb_std.get())
            if self.vol_enabled.get():
                df = add_volume_ratio(df, self.vol_period.get())
            self.after(0, self._show_analysis_result, self._format_analysis_result(symbol, df))

        threading.Thread(target=worker, daemon=True).start()

    def _format_analysis_result(self, symbol: str, df) -> str:
        last = df.iloc[-1]
        close = float(last["close"])
        lines = [f"Пара: {symbol}", f"Текущая цена: {close:.8f}"]

        rsi_value = None
        bb_lower = None
        bb_upper = None
        if self.rsi_enabled.get():
            rsi_col = f"rsi_{self.rsi_period.get()}"
            rsi_value = float(last.get(rsi_col)) if rsi_col in last else None
            lines.append(f"RSI: {rsi_value:.2f}" if rsi_value is not None else "RSI: n/a")
        if self.bb_enabled.get():
            s = f"{self.bb_period.get()}_{self.bb_std.get()}"
            bb_lower = last.get(f"bb_lower_{s}")
            bb_upper = last.get(f"bb_upper_{s}")

        signal = "НЕТ"
        if rsi_value is not None and bb_lower is not None and bb_upper is not None:
            if rsi_value < 30 and close < bb_lower:
                signal = "ЛОНГ"
            elif rsi_value > 70 and close > bb_upper:
                signal = "ШОРТ"

        lines.append(f"Сигнал: {signal}")
        return "\n".join(lines)

    def _show_analysis_result(self, result: str) -> None:
        self.result_text.configure(state="normal")
        self.result_text.delete("1.0", tk.END)
        self.result_text.insert(tk.END, result)
        self.result_text.configure(state="disabled")


__all__ = ["TradingBotGUI", "AverageLevelsFrame"]
