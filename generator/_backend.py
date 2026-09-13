"""Доступ к бэкенду sing_box_manager.py из пакета generator.

GUI — это новый фронтенд поверх существующего модуля sing_box_manager.py (он
лежит в корне проекта и не знает про Qt). Здесь единственное место, где пакет
правит sys.path и импортирует бэкенд, чтобы остальные модули писали просто
``from ._backend import sbm`` и не дублировали логику поиска корня проекта.
"""
from __future__ import annotations

import sys
from pathlib import Path

#: Корень проекта (там, где лежит sing_box_manager.py и gui.py).
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import sing_box_manager as sbm  # noqa: E402

__all__ = ["sbm", "PROJECT_ROOT"]
