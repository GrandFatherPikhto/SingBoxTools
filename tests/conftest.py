"""Общие фикстуры тестов для sing_box_manager и Qt6-GUI.

Тесты не требуют root, сети и реального sing-box: output_file/links_file
резолвятся относительно самого YAML-файла (см. resolve_path), поэтому весь цикл
генерации гоняется в tmp_path и реальный config.json не трогается.

GUI-тесты запускаются через qtbot на offscreen-платформе Qt (без реального
дисплея) — QT_QPA_PLATFORM выставляется здесь до первого импорта PyQt6.
"""
import os
import sys
import urllib.parse
from pathlib import Path

#: Обязательно до создания QApplication: qtbot работает без дисплея.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
import yaml

MODULE_DIR = Path(__file__).resolve().parents[1]  # python/sing-box
if str(MODULE_DIR) not in sys.path:
    sys.path.insert(0, str(MODULE_DIR))

import sing_box_manager as sbm  # noqa: E402

RU_TAG = "🇷🇺 Russia - Moscow"
FI_TAG = "🇫🇮 Finland - Helsinki 1"
NL_TAG = "🇳🇱 Netherlands - Amsterdam"

ALL_TAGS = (FI_TAG, NL_TAG, RU_TAG)

FI_UUID = "11111111-1111-1111-1111-111111111111"
NL_UUID = "22222222-2222-2222-2222-222222222222"
RU_UUID = "33333333-3333-3333-3333-333333333333"


def vless_link(uuid, host, tag, query="", port=443):
    """Собирает VLESS-ссылку ровно в том виде, в каком их отдаёт провайдер."""
    query_part = f"?{query}" if query else ""
    return f"vless://{uuid}@{host}:{port}{query_part}#{urllib.parse.quote(tag)}"


#: reality + tls + plain tcp — по одной ссылке каждого вида
DEFAULT_LINKS = "\n".join([
    vless_link(FI_UUID, "fi.example.com", FI_TAG,
               "security=reality&pbk=FI_PUBKEY&sid=ab12&sni=fi.example.com"
               "&flow=xtls-rprx-vision&fp=chrome"),
    vless_link(NL_UUID, "nl.example.com", NL_TAG,
               "security=tls&sni=nl.example.com&fp=firefox"),
    vless_link(RU_UUID, "ru.example.com", RU_TAG),
]) + "\n"


#: минимальный settings.yaml: 3 сервера, 2 прокси (один со своим пулом)
DEFAULT_SETTINGS = {
    "listen_ip": "127.0.0.1",
    "links_file": "links.txt",
    "output_file": "config.json",
    "exclude_from_auto": ["🇷🇺"],
    "urltest": {"url": "https://gstatic.com", "interval": "3m", "tolerance": 50},
    "log": {"level": "info", "timestamp": True},
    "dns": {"servers": [{"type": "local", "tag": "dns-local"}], "final": "dns-local"},
    "proxies": [
        {"tag": "main-socks", "type": "socks", "port": 54321},
        {"tag": "apps-http", "type": "http", "port": 54323, "servers": [FI_TAG, NL_TAG]},
    ],
    "routes": {
        "telegram": {"outbound": "auto-select", "domains": ["t.me", "telegram.org"]},
    },
}


@pytest.fixture
def manager():
    """Сам модуль под тестом."""
    return sbm


@pytest.fixture
def links_file(tmp_path):
    """links.txt с тремя валидными ссылками (reality / tls / plain)."""
    path = tmp_path / "links.txt"
    path.write_text(DEFAULT_LINKS, encoding="utf-8")
    return path


@pytest.fixture
def write_settings(tmp_path):
    """Фабрика settings.yaml в tmp_path (пути внутри YAML — относительные)."""
    def _write(**overrides):
        data = {**DEFAULT_SETTINGS, **overrides}
        path = tmp_path / "settings.yaml"
        path.write_text(
            yaml.safe_dump(data, allow_unicode=True, sort_keys=False),
            encoding="utf-8")
        return path
    return _write


@pytest.fixture
def settings_file(write_settings, links_file):
    """settings.yaml с дефолтным содержимым, лежащий рядом с links.txt."""
    return write_settings()


# ---------------------------------------------------------------------------
# Заглушка questionary: у каждой функции свой сценарий ответов
# ---------------------------------------------------------------------------

class FakeAnswer:
    def __init__(self, value):
        self.value = value

    def ask(self):
        return self.value


class FakeQuestionary:
    """Подменяет questionary: .ask() возвращает заранее заданные ответы по очереди."""

    def __init__(self, select=None, text=None, checkbox=None, confirm=None):
        self.scripted = {
            "select": list(select or []),
            "text": list(text or []),
            "checkbox": list(checkbox or []),
            "confirm": list(confirm or []),
        }
        self.seen = []
        self.last_choices = {}

    def _next(self, kind):
        values = self.scripted[kind]
        assert values, f"нет заготовленного ответа для questionary.{kind}"
        value = values.pop(0)
        self.seen.append((kind, value))
        return FakeAnswer(value)

    def select(self, message, choices=None, **_kwargs):
        self.last_choices["select"] = choices
        return self._next("select")

    def text(self, message, **_kwargs):
        return self._next("text")

    def checkbox(self, message, choices=None, **_kwargs):
        self.last_choices["checkbox"] = choices
        return self._next("checkbox")

    def confirm(self, message, **_kwargs):
        return self._next("confirm")

    @staticmethod
    def Choice(title=None, value=None, checked=False, **_kwargs):
        # у questionary.Choice значение по умолчанию — сам заголовок
        return {"title": title, "value": title if value is None else value, "checked": checked}


@pytest.fixture
def fake_questionary(monkeypatch, manager):
    """Возвращает фабрику: fake = install(select=[...], text=[...], ...)."""
    def install(**scripted):
        fake = FakeQuestionary(**scripted)
        monkeypatch.setattr(manager, "questionary", fake)
        return fake
    return install


# ---------------------------------------------------------------------------
# Фикстуры GUI (generator/ + gui.py)
# ---------------------------------------------------------------------------

FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"
FIXTURE_SETTINGS = FIXTURES_DIR / "settings.yaml"

#: тег, который есть в tests/fixtures/settings.yaml, но отсутствует в links.txt
STALE_TAG = "🇩🇪 Germany - Berlin"


@pytest.fixture
def fixtures_dir():
    return FIXTURES_DIR


@pytest.fixture
def fixture_settings_path():
    """Образец проекта из репозитория (только чтение)."""
    return FIXTURE_SETTINGS


@pytest.fixture
def project_dir(tmp_path):
    """Копия tests/fixtures в tmp_path — Save в тестах не трогает репозиторий."""
    import shutil

    dst = tmp_path / "project"
    shutil.copytree(FIXTURES_DIR, dst)
    return dst


@pytest.fixture
def project_settings(project_dir):
    """Путь к settings.yaml внутри временной копии фикстур."""
    return project_dir / "settings.yaml"


@pytest.fixture
def gui_model():
    """Qt-независимая модель проекта."""
    from generator.model import ProjectModel

    return ProjectModel()


@pytest.fixture
def main_window(qtbot):
    """MainWindow с заглушёнными модальными диалогами."""
    from generator.main_window import MainWindow

    window = MainWindow()
    qtbot.addWidget(window)
    window._info = lambda message: None
    window._error = lambda message: None
    window._ask_save_discard_cancel = lambda: "discard"
    return window
