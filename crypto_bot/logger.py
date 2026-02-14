"""Настройка логирования в файл и GUI."""

from __future__ import annotations

import logging
import queue
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Optional


class GUILogHandler(logging.Handler):
    """Лог-хендлер, отправляющий сообщения в очередь для GUI."""

    def __init__(self, log_queue: queue.Queue[str]) -> None:
        super().__init__()
        self.log_queue = log_queue

    def emit(self, record: logging.LogRecord) -> None:
        """Форматирует и помещает запись лога в очередь."""
        try:
            message = self.format(record)
            self.log_queue.put(message)
        except Exception:  # pragma: no cover - защита от любых ошибок логирования
            self.handleError(record)


def setup_logger(
    name: str = "crypto_bot",
    log_file: str | Path = "logs/bot.log",
    level: int = logging.INFO,
    gui_queue: Optional[queue.Queue[str]] = None,
) -> logging.Logger:
    """Создаёт и настраивает общий логгер для приложения."""
    logger = logging.getLogger(name)
    logger.setLevel(level)
    logger.propagate = False

    if logger.handlers:
        return logger

    log_path = Path(log_file)
    log_path.parent.mkdir(parents=True, exist_ok=True)

    formatter = logging.Formatter(
        fmt="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    file_handler = RotatingFileHandler(log_path, maxBytes=2_000_000, backupCount=5, encoding="utf-8")
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)

    if gui_queue is not None:
        gui_handler = GUILogHandler(gui_queue)
        gui_handler.setFormatter(formatter)
        logger.addHandler(gui_handler)

    return logger
