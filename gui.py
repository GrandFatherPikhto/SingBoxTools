#!/usr/bin/env python3
"""Единая точка входа Qt6-редактора settings.yaml.

Запуск:

    ./gui.py                      # новый проект
    ./gui.py path/to/settings.yaml

Вся библиотечная часть лежит в пакете ``generator/``, бэкенд (парсинг VLESS,
валидация, сборка config.json) — в ``sing_box_manager.py``. Этот файл только
создаёт QApplication и показывает главное окно.
"""
from __future__ import annotations

import sys
from pathlib import Path

#: Корень проекта — пакет generator/ и sing_box_manager.py лежат рядом с gui.py.
PROJECT_ROOT = Path(__file__).resolve().parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from PyQt6.QtWidgets import QApplication  # noqa: E402

from generator.main_window import MainWindow  # noqa: E402


def main(argv=None):
    argv = list(sys.argv if argv is None else argv)
    app = QApplication(argv)
    app.setApplicationName("SingBoxTools")
    app.setApplicationDisplayName("sing-box settings editor")

    window = MainWindow()
    args = argv[1:]
    if args:
        window.open_path(args[0])
    else:
        window.new_project(skip_confirm=True)
    window.show()

    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
