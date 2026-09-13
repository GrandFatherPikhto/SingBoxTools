"""GUI-тесты через qtbot (offscreen-платформа, без реального дисплея).

Проверяются обработчики окна (не пиксели): построение дерева, добавление и
удаление прокси/маршрутов, валидация формы прокси, подсветка stale-ссылок,
Save в tmp_path и действие «Сгенерировать конфигурацию» — и на валидном проекте,
и на проекте с заведомо битой ссылкой.

Модальные диалоги заглушены фикстурой main_window (_info/_error/
_ask_save_discard_cancel), поэтому тесты не блокируются. Результаты генерации и
ошибки дополнительно перехватываются через сигналы окна.
"""
import pytest
import sing_box_manager as sbm
import yaml
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QCloseEvent

from conftest import DEFAULT_LINKS, DEFAULT_SETTINGS, FI_TAG, NL_TAG
from generator.main_window import ROLE_KEY


@pytest.fixture
def valid_project(tmp_path):
    """Валидный (без stale-ссылок) проект в tmp_path для генерации."""
    (tmp_path / "links.txt").write_text(DEFAULT_LINKS, encoding="utf-8")
    (tmp_path / "settings.yaml").write_text(
        yaml.safe_dump(DEFAULT_SETTINGS, allow_unicode=True, sort_keys=False),
        encoding="utf-8")
    return tmp_path / "settings.yaml"


def _collect(window):
    result = {"done": [], "errors": [], "generated": []}
    window.generation_done.connect(lambda ok, msg: result["done"].append((ok, msg)))
    window.config_error.connect(result["errors"].append)
    window.config_generated.connect(result["generated"].append)
    return result


def _item(window, key):
    return window._items_by_key[key]


def _child(parent_item, key):
    for i in range(parent_item.childCount()):
        child = parent_item.child(i)
        if child.data(0, ROLE_KEY) == key:
            return child
    raise AssertionError(f"узел {key!r} не найден")


def _child_texts(parent_item):
    return [parent_item.child(i).text(0) for i in range(parent_item.childCount())]


def _root(window):
    return window.tree.topLevelItem(0)


# ---------------------------------------------------------------------------
# New / Open
# ---------------------------------------------------------------------------

def test_new_project_builds_tree_with_skeleton(main_window):
    main_window.new_project(skip_confirm=True)

    assert main_window.tree.topLevelItemCount() == 1
    root = _root(main_window)
    assert [child.data(0, ROLE_KEY) for child in
            (root.child(i) for i in range(root.childCount()))] == [
        "links", "output", "general", "dns", "proxies", "routes"]
    assert _child(root, "proxies").childCount() == 0
    assert _child(root, "routes").childCount() == 0
    assert main_window.model.dirty is False


def test_open_fixture_populates_tree(main_window, fixture_settings_path):
    assert main_window.open_path(fixture_settings_path) is True

    root = _root(main_window)
    proxies = _child(root, "proxies")
    assert proxies.childCount() == 2
    assert any("main-socks" in t for t in _child_texts(proxies))
    assert any("apps-http" in t for t in _child_texts(proxies))

    assert _child(root, "routes").childCount() == 2
    assert "config.json" in _child(root, "output").text(0)
    assert "links.txt" in _child(root, "links").text(0)


def test_selecting_proxy_loads_editor_page(main_window, fixture_settings_path):
    main_window.open_path(fixture_settings_path)
    main_window.select_key("proxy:apps-http")

    assert main_window.stack.currentWidget() is main_window.page_proxy
    assert main_window.page_proxy.tag_edit.text() == "apps-http"
    assert main_window.page_proxy.port_spin.value() == 54323


# ---------------------------------------------------------------------------
# Добавление / удаление прокси и маршрута (через обработчики меню)
# ---------------------------------------------------------------------------

def test_add_proxy_handler_updates_model_and_tree(main_window):
    main_window.new_project(skip_confirm=True)
    entry = main_window.add_proxy_handler()

    assert entry["tag"] in main_window.model.proxy_tags()
    assert "proxy:new-proxy" in main_window._items_by_key
    assert main_window.model.dirty is True
    assert _child(_root(main_window), "proxies").childCount() == 1


def test_remove_proxy_updates_model_and_tree(main_window):
    main_window.new_project(skip_confirm=True)
    main_window.add_proxy_handler()
    assert main_window.remove_proxy("new-proxy") is True

    assert main_window.model.proxy_tags() == []
    assert "proxy:new-proxy" not in main_window._items_by_key
    assert _child(_root(main_window), "proxies").childCount() == 0


def test_rename_proxy_updates_tree(main_window, fixture_settings_path):
    main_window.open_path(fixture_settings_path)
    assert main_window.rename_proxy("main-socks", "main-renamed") is True

    assert "main-renamed" in main_window.model.proxy_tags()
    assert "proxy:main-renamed" in main_window._items_by_key


def test_add_and_remove_route(main_window):
    main_window.new_project(skip_confirm=True)
    main_window.add_route_handler()
    assert main_window.model.route_names() == ["route"]
    assert _child(_root(main_window), "routes").childCount() == 1

    assert main_window.remove_route("route") is True
    assert main_window.model.route_names() == []
    assert _child(_root(main_window), "routes").childCount() == 0


# ---------------------------------------------------------------------------
# Валидация формы прокси
# ---------------------------------------------------------------------------

def test_proxy_form_rejects_duplicate_tag_without_touching_model(main_window):
    main_window.new_project(skip_confirm=True)
    main_window.model.add_proxy("main", "socks", 54321)
    main_window.model.add_proxy("apps", "http", 54323)
    main_window.reload_tags()
    main_window.refresh_tree()
    main_window.select_key("proxy:main")

    failures = []
    main_window.page_proxy.validation_failed.connect(failures.append)

    main_window.page_proxy.tag_edit.setText("apps")   # дубль тега
    assert main_window.page_proxy.apply() is False

    assert failures and "дубль тега" in failures[0]
    assert main_window.model.proxy_tags() == ["main", "apps"]   # модель не изменилась


def test_proxy_form_rejects_duplicate_port(main_window):
    main_window.new_project(skip_confirm=True)
    main_window.model.add_proxy("main", "socks", 54321)
    main_window.model.add_proxy("apps", "http", 54323)
    main_window.reload_tags()
    main_window.refresh_tree()
    main_window.select_key("proxy:main")

    failures = []
    main_window.page_proxy.validation_failed.connect(failures.append)
    main_window.page_proxy.port_spin.setValue(54323)   # чужой порт

    assert main_window.page_proxy.apply() is False
    assert failures and "дубль порта" in failures[0]


def test_proxy_form_apply_updates_model_and_tree(main_window):
    main_window.new_project(skip_confirm=True)
    main_window.model.add_proxy("main", "socks", 54321)
    main_window.reload_tags()
    main_window.refresh_tree()
    main_window.select_key("proxy:main")

    page = main_window.page_proxy
    page.tag_edit.setText("main-renamed")
    page.type_combo.setCurrentText("http")
    page.port_spin.setValue(56000)
    assert page.apply() is True

    proxy = main_window.model.get_proxy("main-renamed")
    assert proxy["type"] == "http"
    assert proxy["port"] == 56000
    assert "proxy:main-renamed" in main_window._items_by_key


def test_proxy_form_checkbox_filter_merges_selection(main_window, fixture_settings_path):
    main_window.open_path(fixture_settings_path)
    main_window.select_key("proxy:apps-http")
    page = main_window.page_proxy

    page.filter_edit.setText("netherland")
    assert page.server_list.count() == 1
    item = page.server_list.item(0)
    assert item.checkState() == Qt.CheckState.Checked   # уже выбран в фикстуре

    item.setCheckState(Qt.CheckState.Unchecked)
    assert NL_TAG not in page.collect()["servers"]

    item.setCheckState(Qt.CheckState.Checked)
    assert NL_TAG in page.collect()["servers"]


# ---------------------------------------------------------------------------
# Индикация stale-ссылок
# ---------------------------------------------------------------------------

def test_stale_links_are_marked_in_tree(main_window, fixture_settings_path):
    main_window.open_path(fixture_settings_path)

    all_tags, _ = main_window.model.load_server_tags()
    assert sbm.find_stale_refs(main_window.model.data, all_tags)

    assert "[!]" in _item(main_window, "proxy:apps-http").text(0)
    assert "[!]" in _item(main_window, "route:broken").text(0)
    assert "[!]" not in _item(main_window, "proxy:main-socks").text(0)


# ---------------------------------------------------------------------------
# Save
# ---------------------------------------------------------------------------

def test_save_writes_changes_to_tmp(main_window, project_settings):
    main_window.open_path(project_settings)
    main_window.model.add_proxy("extra", "http", 56001, servers=[FI_TAG])

    assert main_window.save_project() is True

    reloaded = sbm.load_settings(str(project_settings))
    assert reloaded["proxies"][-1]["tag"] == "extra"
    assert main_window.model.dirty is False


def test_save_as_updates_path_and_title(main_window, fixture_settings_path, tmp_path):
    main_window.open_path(fixture_settings_path)
    target = tmp_path / "copy.yaml"

    main_window.model.save(target)
    main_window._update_title()

    assert target.exists()
    assert "copy.yaml" in main_window.windowTitle()


# ---------------------------------------------------------------------------
# Generate Config
# ---------------------------------------------------------------------------

def test_generate_action_is_a_menu_action_with_shortcut(main_window):
    assert main_window.action_generate.shortcut().toString() == "Ctrl+G"


def test_generate_on_valid_project_creates_config(main_window, valid_project):
    main_window.open_path(valid_project)
    result = _collect(main_window)

    assert main_window.generate_config() is True

    config_path = valid_project.parent / "config.json"
    assert config_path.exists()
    assert result["done"] and result["done"][0][0] is True
    assert result["generated"] == [str(config_path)]
    assert not result["errors"]

    config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    assert config["inbounds"]


def test_generate_saves_dirty_project_before_generating(main_window, valid_project):
    main_window.open_path(valid_project)
    main_window.model.add_proxy("late", "socks", 56002)
    assert main_window.model.dirty is True
    main_window._ask_save_discard_cancel = lambda: "save"
    result = _collect(main_window)

    assert main_window.generate_config() is True

    saved = sbm.load_settings(str(valid_project))
    assert saved["proxies"][-1]["tag"] == "late"
    assert (valid_project.parent / "config.json").exists()
    assert result["done"][0][0] is True


def test_generate_with_broken_ref_reports_error_and_does_not_crash(
        main_window, project_settings):
    main_window.open_path(project_settings)
    result = _collect(main_window)

    assert main_window.generate_config() is False
    assert result["done"] and result["done"][0][0] is False
    assert result["errors"] and "несуществующие серверы" in result["errors"][0]
    assert not (project_settings.parent / "config.json").exists()


def test_generate_without_saved_path_reports_error(main_window):
    main_window.new_project(skip_confirm=True)
    result = _collect(main_window)

    assert main_window.generate_config() is False
    assert result["done"][0][0] is False
    assert not result["errors"]   # это подсказка «сначала сохраните», а не ConfigError


# ---------------------------------------------------------------------------
# Закрытие с несохранёнными изменениями
# ---------------------------------------------------------------------------

def test_close_event_asks_when_dirty(main_window):
    main_window.new_project(skip_confirm=True)
    main_window.model.add_proxy("p", "socks", 5000)

    main_window._ask_save_discard_cancel = lambda: "cancel"
    event = QCloseEvent()
    main_window.closeEvent(event)
    assert event.isAccepted() is False

    main_window._ask_save_discard_cancel = lambda: "discard"
    event2 = QCloseEvent()
    main_window.closeEvent(event2)
    assert event2.isAccepted() is True
