"""Тесты Qt-независимой модели GUI (generator/model.py, generator/validation.py).

qtbot здесь не нужен: модель и построение спецификации дерева — чистые данные,
поэтому их можно гонять как обычные unit-тесты. Save проверяется round-trip'ом
в tmp_path (исходные фикстуры репозитория не трогаются).
"""
import shutil

import pytest
import sing_box_manager as sbm

from conftest import FI_TAG, FIXTURE_SETTINGS, NL_TAG, STALE_TAG
from generator.model import ProjectModel, format_stats
from generator.validation import validate_proxy_candidate


# ---------------------------------------------------------------------------
# вспомогательное
# ---------------------------------------------------------------------------

def children_by_kind(node, kind):
    return [child for child in node.children if child.kind == kind]


def node_by_key(tree, key):
    for child in tree.children:
        if child.key == key:
            return child
    return None


# ---------------------------------------------------------------------------
# New
# ---------------------------------------------------------------------------

def test_new_project_is_minimal_valid_skeleton(gui_model):
    data = gui_model.new()

    assert data["proxies"] == []
    assert data["routes"] == {}
    assert data["listen_ip"] == "127.0.0.1"
    assert data["links_file"] == "links.txt"
    assert data["output_file"] == "config.json"
    # новый проект ещё нигде не сохранён и не считается изменённым
    assert gui_model.path is None
    assert gui_model.dirty is False


def test_new_project_tree_has_expected_nodes(gui_model):
    gui_model.new()
    tree = gui_model.tree_spec(all_tags=[])

    assert tree.kind == "root"
    keys = [child.key for child in tree.children]
    assert keys == ["links", "output", "general", "dns", "proxies", "routes"]
    assert children_by_kind(tree, "proxies")[0].children == []
    assert children_by_kind(tree, "routes")[0].children == []


def test_new_project_is_saveable_and_readable_back(gui_model, tmp_path):
    gui_model.new()
    target = tmp_path / "fresh.yaml"
    gui_model.add_proxy("p1", "socks", 54321)
    gui_model.save(target)

    assert target.exists()
    reloaded = sbm.load_settings(str(target))
    assert reloaded["proxies"][0]["tag"] == "p1"
    assert reloaded["routes"] == {}
    assert gui_model.dirty is False


# ---------------------------------------------------------------------------
# Open + дерево
# ---------------------------------------------------------------------------

def test_open_fixture_builds_expected_tree(gui_model):
    gui_model.open(FIXTURE_SETTINGS)
    tree = gui_model.tree_spec()

    proxy_node = node_by_key(tree, "proxies")
    assert [child.detail for child in proxy_node.children] == ["main-socks", "apps-http"]

    route_node = node_by_key(tree, "routes")
    assert [child.detail for child in route_node.children] == ["telegram", "broken"]

    assert node_by_key(tree, "dns").kind == "dns"
    assert node_by_key(tree, "general").kind == "general"
    assert "links.txt" in node_by_key(tree, "links").title
    assert "config.json" in node_by_key(tree, "output").title
    assert gui_model.dirty is False


def test_open_resolves_links_relative_to_settings(gui_model):
    gui_model.open(FIXTURE_SETTINGS)
    tags, error = gui_model.load_server_tags()

    assert error is None
    assert FI_TAG in tags and NL_TAG in tags


# ---------------------------------------------------------------------------
# Stale-ссылки = find_stale_refs
# ---------------------------------------------------------------------------

def test_stale_map_matches_backend_find_stale_refs(gui_model):
    gui_model.open(FIXTURE_SETTINGS)
    all_tags, _ = gui_model.load_server_tags()

    expected = sbm.find_stale_refs(gui_model.data, all_tags)
    assert expected, "фикстура должна содержать хотя бы одну протухшую ссылку"

    stale = gui_model.stale_map(all_tags)
    assert STALE_TAG in stale[("proxies", "apps-http")]
    assert STALE_TAG in stale[("routes", "broken")]
    # количество мест совпадает с бэкендом
    assert sum(len(v) for v in stale.values()) == len(expected)


def test_stale_nodes_are_flagged_in_tree(gui_model):
    gui_model.open(FIXTURE_SETTINGS)
    tree = gui_model.tree_spec()

    apps = [c for c in node_by_key(tree, "proxies").children if c.detail == "apps-http"][0]
    healthy = [c for c in node_by_key(tree, "proxies").children if c.detail == "main-socks"][0]
    broken = [c for c in node_by_key(tree, "routes").children if c.detail == "broken"][0]

    assert apps.stale is True
    assert "[!]" in apps.title
    assert healthy.stale is False
    assert broken.stale is True
    assert "[!]" in broken.title


def test_missing_links_file_is_flagged(gui_model, tmp_path):
    gui_model.new()
    gui_model.data["links_file"] = "does-not-exist.txt"
    tree = gui_model.tree_spec()

    links = node_by_key(tree, "links")
    assert links.stale is True
    assert "файл не найден" in links.title


# ---------------------------------------------------------------------------
# proxy / route CRUD + dirty-флаг
# ---------------------------------------------------------------------------

def test_add_proxy_assigns_defaults_and_marks_dirty(gui_model):
    gui_model.new()
    entry = gui_model.add_proxy()

    assert entry["tag"] == "new-proxy"
    assert entry["port"] == 54321
    assert entry["type"] == "socks"
    assert "servers" not in entry
    assert gui_model.dirty is True

    second = gui_model.add_proxy()
    assert second["tag"] == "new-proxy-2"
    assert second["port"] == 54322


def test_remove_and_rename_proxy(gui_model):
    gui_model.new()
    gui_model.add_proxy("a", "socks", 1001)
    gui_model.add_proxy("b", "http", 1002)
    gui_model.mark_clean()

    assert gui_model.rename_proxy("a", "a2") is True
    assert gui_model.proxy_tags() == ["a2", "b"]
    assert gui_model.dirty is True
    # переименование в занятый тег не проходит
    assert gui_model.rename_proxy("a2", "b") is False

    assert gui_model.remove_proxy("a2") is True
    assert gui_model.proxy_tags() == ["b"]
    assert gui_model.remove_proxy("nope") is False


def test_upsert_proxy_replaces_in_place(gui_model):
    gui_model.new()
    gui_model.add_proxy("main", "socks", 54321)
    gui_model.upsert_proxy(
        {"tag": "main", "type": "http", "port": 55555, "servers": [FI_TAG]},
        current_tag="main")

    assert len(gui_model.proxies()) == 1
    assert gui_model.proxies()[0]["type"] == "http"
    assert gui_model.proxies()[0]["servers"] == [FI_TAG]


def test_upsert_proxy_omits_empty_servers(gui_model):
    gui_model.new()
    gui_model.add_proxy("main", "socks", 54321, servers=[FI_TAG])
    gui_model.upsert_proxy({"tag": "main", "type": "socks", "port": 54321, "servers": []},
                           current_tag="main")

    assert "servers" not in gui_model.proxies()[0]


def test_route_crud(gui_model):
    gui_model.new()
    entry = gui_model.add_route()
    assert entry["outbound"] == "auto-select"
    assert gui_model.route_names() == ["route"]

    gui_model.upsert_route("route", {"outbound": "direct", "domains": ["a.com"]},
                           current_name="route")
    assert gui_model.get_route("route")["domains"] == ["a.com"]

    assert gui_model.rename_route("route", "renamed") is True
    assert gui_model.route_names() == ["renamed"]

    assert gui_model.remove_route("renamed") is True
    assert gui_model.route_names() == []


def test_add_route_with_explicit_name(gui_model):
    gui_model.new()
    gui_model.add_route("telegram", domains=["t.me"])
    gui_model.add_route("telegram", domains=["x"])
    assert gui_model.route_names() == ["telegram"]
    assert gui_model.next_free_route_name("telegram") == "telegram-2"


# ---------------------------------------------------------------------------
# Save round-trip (комментарии и остальной YAML не ломаются)
# ---------------------------------------------------------------------------

def test_save_round_trip_preserves_comments_and_other_sections(gui_model, tmp_path):
    target = tmp_path / "settings.yaml"
    shutil.copy(FIXTURE_SETTINGS, target)

    gui_model.open(target)
    gui_model.add_proxy("extra-http", "http", 56000, servers=[NL_TAG])
    gui_model.upsert_route("youtube", {"outbound": "auto-select", "domains": ["youtube.com"]})
    gui_model.save()

    text = target.read_text(encoding="utf-8")
    # комментарии фикстуры пережили программную правку
    assert "# ВАЖНО: этот комментарий проверяется тестом round-trip" in text
    assert "# секция dns" in text

    reloaded = sbm.load_settings(str(target))
    # правки на месте
    assert reloaded["proxies"][-1]["tag"] == "extra-http"
    assert reloaded["routes"]["youtube"]["domains"] == ["youtube.com"]
    # а остальной YAML не разрушен
    assert reloaded["listen_ip"] == "127.0.0.1"
    assert reloaded["log"]["level"] == "info"
    assert reloaded["dns"]["final"] == "dns-local"
    assert reloaded["proxies"][0]["tag"] == "main-socks"
    assert reloaded["routes"]["telegram"]["outbound"] == FI_TAG


def test_save_without_path_raises(gui_model):
    gui_model.new()
    with pytest.raises(sbm.ConfigError):
        gui_model.save()


def test_dirty_flag_resets_after_save(gui_model, tmp_path):
    gui_model.new()
    target = tmp_path / "s.yaml"
    gui_model.add_proxy("p", "socks", 5000)
    assert gui_model.dirty is True

    gui_model.save(target)
    assert gui_model.dirty is False


# ---------------------------------------------------------------------------
# Валидация формы прокси (переиспользует validate_proxies бэкенда)
# ---------------------------------------------------------------------------

PROXIES = [
    {"tag": "main", "type": "socks", "port": 54321},
    {"tag": "apps", "type": "http", "port": 54323, "servers": [FI_TAG]},
]


@pytest.mark.parametrize("candidate, current_tag, fragment", [
    ({"tag": "main", "type": "http", "port": 5555, "servers": []}, None, "дубль тега"),
    ({"tag": "fresh", "type": "http", "port": 54323, "servers": []}, None, "дубль порта"),
    ({"tag": "fresh", "type": "smtp", "port": 5555, "servers": []}, None, "неизвестный тип"),
    ({"tag": "fresh", "type": "http", "port": 70000, "servers": []}, None, "вне диапазона"),
    ({"tag": "", "type": "http", "port": 5555, "servers": []}, None, "тег"),
])
def test_validate_proxy_candidate_rejects(candidate, current_tag, fragment):
    error = validate_proxy_candidate(PROXIES, candidate, current_tag=current_tag)
    assert error is not None and fragment in error


def test_validate_proxy_candidate_allows_edit_of_self():
    candidate = {"tag": "main", "type": "http", "port": 54321, "servers": []}
    assert validate_proxy_candidate(PROXIES, candidate, current_tag="main") is None


def test_validate_proxy_candidate_accepts_valid_new():
    candidate = {"tag": "fresh", "type": "http", "port": 56000, "servers": [FI_TAG]}
    assert validate_proxy_candidate(PROXIES, candidate, current_tag=None) is None


# ---------------------------------------------------------------------------
# format_stats
# ---------------------------------------------------------------------------

def test_format_stats_mentions_output_and_counts():
    stats = {"servers": 3, "inbounds": 2, "pools": 1, "auto_count": 2,
             "excluded": ["🇷🇺"], "proxies": [
                 {"tag": "main", "type": "socks", "port": 54321, "servers": []},
             ], "listen_ip": "127.0.0.1"}
    text = format_stats("/tmp/config.json", stats)

    assert "/tmp/config.json" in text
    assert "Серверов: 3" in text
    assert "[SOCKS] main" in text
    assert "auto-select" in text
