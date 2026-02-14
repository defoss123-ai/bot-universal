"""Индикаторы технического анализа на основе pandas_ta."""

from __future__ import annotations

import logging

import pandas as pd
import pandas_ta as ta

LOGGER = logging.getLogger("crypto_bot.indicators")


def add_rsi(df: pd.DataFrame, period: int = 14) -> pd.DataFrame:
    """Добавляет колонку RSI."""
    try:
        df[f"rsi_{period}"] = ta.rsi(df["close"], length=period)
    except Exception as exc:
        LOGGER.exception("Ошибка расчета RSI: %s", exc)
    return df


def add_macd(df: pd.DataFrame, fast: int = 12, slow: int = 26, signal: int = 9) -> pd.DataFrame:
    """Добавляет линии MACD, Signal и Histogram."""
    try:
        macd_df = ta.macd(df["close"], fast=fast, slow=slow, signal=signal)
        if macd_df is not None:
            df[f"macd_{fast}_{slow}_{signal}"] = macd_df.iloc[:, 0]
            df[f"macd_signal_{fast}_{slow}_{signal}"] = macd_df.iloc[:, 2]
            df[f"macd_hist_{fast}_{slow}_{signal}"] = macd_df.iloc[:, 1]
    except Exception as exc:
        LOGGER.exception("Ошибка расчета MACD: %s", exc)
    return df


def add_bollinger(df: pd.DataFrame, period: int = 20, std_dev: float = 2) -> pd.DataFrame:
    """Добавляет верхнюю/среднюю/нижнюю полосы Bollinger Bands."""
    try:
        bbands = ta.bbands(df["close"], length=period, std=std_dev)
        if bbands is not None:
            df[f"bb_lower_{period}_{std_dev}"] = bbands.iloc[:, 0]
            df[f"bb_middle_{period}_{std_dev}"] = bbands.iloc[:, 1]
            df[f"bb_upper_{period}_{std_dev}"] = bbands.iloc[:, 2]
    except Exception as exc:
        LOGGER.exception("Ошибка расчета Bollinger Bands: %s", exc)
    return df


def add_volume_ratio(df: pd.DataFrame, volume_period: int = 20) -> pd.DataFrame:
    """Добавляет отношение текущего объема к среднему объему за период."""
    try:
        avg_col = f"volume_sma_{volume_period}"
        ratio_col = f"volume_ratio_{volume_period}"
        df[avg_col] = ta.sma(df["volume"], length=volume_period)
        df[ratio_col] = df["volume"] / df[avg_col]
    except Exception as exc:
        LOGGER.exception("Ошибка расчета volume ratio: %s", exc)
    return df
