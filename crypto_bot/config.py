"""Конфигурация торгового бота и загрузка переменных окружения."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
ENV_PATH = BASE_DIR / ".env"
USER_SETTINGS_PATH = BASE_DIR / "user_settings.json"

# Загружаем .env, если файл существует
load_dotenv(dotenv_path=ENV_PATH if ENV_PATH.exists() else None)


DEFAULT_PAIRS = [
    "BTC/USDT",
    "ETH/USDT",
    "PEPE/USDT",
    "WIF/USDT",
    "DOGE/USDT",
    "NOT/USDT",
    "PEOPLE/USDT",
    "BONK/USDT",
]

DEFAULT_SETTINGS: dict[str, Any] = {
    "default_timeframe": "5m",
    "ohlcv_limit": 100,
    "default_market_type": "spot",
    "request_timeout_ms": 15000,
    "enable_rate_limit": True,
}


def _to_bool(value: str | None, default: bool = False) -> bool:
    """Преобразует текстовое значение в bool."""
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def load_user_settings() -> dict[str, Any]:
    """Загружает пользовательские настройки из JSON-файла."""
    if not USER_SETTINGS_PATH.exists():
        return {}
    try:
        return json.loads(USER_SETTINGS_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_user_settings(settings: dict[str, Any]) -> bool:
    """Сохраняет пользовательские настройки в JSON-файл."""
    try:
        USER_SETTINGS_PATH.write_text(json.dumps(settings, ensure_ascii=False, indent=2), encoding="utf-8")
        return True
    except Exception:
        return False


MEXC_API_KEY = os.getenv("MEXC_API_KEY", "")
MEXC_SECRET_KEY = os.getenv("MEXC_SECRET_KEY", "")
USE_TESTNET = _to_bool(os.getenv("USE_TESTNET"), default=True)

TELEGRAM_ENABLED = _to_bool(os.getenv("TELEGRAM_ENABLED"), default=False)
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")
