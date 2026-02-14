"""Точка входа для запуска GUI торгового бота."""

from gui import TradingBotGUI


def main() -> None:
    """Запускает GUI приложение."""
    app = TradingBotGUI()
    app.mainloop()


if __name__ == "__main__":
    main()
