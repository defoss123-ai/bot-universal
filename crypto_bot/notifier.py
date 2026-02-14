"""Модуль уведомлений в Telegram."""

from __future__ import annotations

import threading
from queue import Empty, Queue

import requests


class TelegramNotifier:
    """Асинхронный отправщик уведомлений в Telegram."""

    def __init__(self, bot_token: str, chat_id: str) -> None:
        self.bot_token = bot_token
        self.chat_id = chat_id
        self.base_url = f"https://api.telegram.org/bot{bot_token}"
        self.queue: Queue[str] = Queue()
        self.running = True
        self.thread = threading.Thread(target=self._worker, daemon=True)
        self.thread.start()

    def _worker(self) -> None:
        """Отправляет сообщения из очереди в отдельном потоке."""
        while self.running:
            try:
                message = self.queue.get(timeout=1)
            except Empty:
                continue
            self._send_sync(message)

    def _send_sync(self, message: str) -> bool:
        """Синхронная отправка сообщения."""
        try:
            requests.post(
                f"{self.base_url}/sendMessage",
                json={"chat_id": self.chat_id, "text": message, "parse_mode": "HTML"},
                timeout=5,
            )
            return True
        except Exception:
            return False

    def send(self, message: str) -> None:
        """Добавляет сообщение в очередь (неблокирующий вызов)."""
        if self.running:
            self.queue.put(message)

    def send_sync(self, message: str) -> bool:
        """Отправляет сообщение синхронно (блокирующий вызов)."""
        return self._send_sync(message)

    def stop(self) -> None:
        """Останавливает обработчик очереди."""
        self.running = False
