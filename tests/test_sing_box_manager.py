"""Тесты sing_box_manager.py (слитый генератор конфига + TUI-пикер серверов).

Сеть, root и реальный sing-box не нужны: конфиг собирается и пишется целиком в
tmp_path, интерактивные функции проверяются на заглушке questionary
(см. fake_questionary в conftest.py).
"""
import json
import subprocess
import sys
from pathlib import Path

import pytest

from conftest import (
    ALL_TAGS,
    DEFAULT_LINKS,
    FI_TAG,
    MODULE_DIR,
    NL_TAG,
    RU_TAG,
    vless_link,
)

REPO_PYTHON_DIR = MODULE_DIR.parent  # python/


def find_shim(name):
    """Ищет шим и рядом с модулем (плоская раскладка), и в python/ (source layout)."""
    for candidate in (MODULE_DIR / name, REPO_PYTHON_DIR / name):
        if candidate.exists():
            return candidate
    pytest.skip(f"{name} отсутствует в этой копии проекта")


# ---------------------------------------------------------------------------
# parse_vless / dedup_tags / parse_links
# ---------------------------------------------------------------------------

def test_parse_vless_reality(manager):
    url = vless_link("uuid-1", "fi.example.com", FI_TAG,
                     "security=reality&pbk=PUBKEY&sid=ab12&sni=fi.example.com"
                     "&flow=xtls-rprx-vision&fp=firefox")
    ob = manager.parse_vless(url)

    assert ob["type"] == "vless"
    assert ob["tag"] == FI_TAG
    assert ob["server"] == "fi.example.com"
    assert ob["server_port"] == 443
    assert ob["uuid"] == "uuid-1"
    assert ob["flow"] == "xtls-rprx-vision"
    assert ob["tls"]["enabled"] is True
    assert ob["tls"]["server_name"] == "fi.example.com"
    assert ob["tls"]["utls"] == {"enabled": True, "fingerprint": "firefox"}
    assert ob["tls"]["reality"]["enabled"] is True
    assert ob["tls"]["reality"]["public_key"] == "PUBKEY"
    assert ob["tls"]["reality"]["short_id"] == "ab12"


def test_parse_vless_reality_defaults(manager):
    """Без flow/fp в ссылке подставляются xtls-rprx-vision и chrome."""
    url = vless_link("uuid-1", "fi.example.com", FI_TAG, "security=reality&pbk=PUBKEY")
    ob = manager.parse_vless(url)

    assert ob["flow"] == "xtls-rprx-vision"
    assert ob["tls"]["utls"]["fingerprint"] == "chrome"
    assert ob["tls"]["server_name"] == ""
    assert ob["tls"]["reality"]["short_id"] == ""


def test_parse_vless_tls(manager):
    url = vless_link("uuid-2", "nl.example.com", NL_TAG, "security=tls&sni=nl.example.com")
    ob = manager.parse_vless(url)

    assert ob["tls"]["enabled"] is True
    assert ob["tls"]["server_name"] == "nl.example.com"
    assert ob["tls"]["utls"]["fingerprint"] == "chrome"
    assert "reality" not in ob["tls"]
    assert "flow" not in ob


def test_parse_vless_plain_tcp_has_no_tls_nor_flow(manager):
    """security отсутствует -> plain tcp: ни flow, ни tls не выставляем."""
    url = vless_link("uuid-3", "ru.example.com", RU_TAG)
    ob = manager.parse_vless(url)

    assert "flow" not in ob
    assert "tls" not in ob
    assert ob["server_port"] == 443


def test_parse_vless_defaults_port_and_tag(manager):
    """Без порта — 443, без fragment — тег vpnd-<host>."""
    ob = manager.parse_vless("vless://uuid-9@plain.example.com")

    assert ob["server_port"] == 443
    assert ob["tag"] == "vpnd-plain.example.com"


def test_parse_vless_reality_without_pbk_is_skipped(manager):
    url = vless_link("uuid-1", "fi.example.com", FI_TAG, "security=reality&sni=fi.example.com")

    assert manager.parse_vless(url) is None


@pytest.mark.parametrize("url", [
    "vless://@fi.example.com:443#no-uuid",      # нет uuid
    "vless://uuid-1@:443#no-server",            # нет сервера
    "vless://uuid-1@fi.example.com:notaport#bad-port",
])
def test_parse_vless_broken_links_return_none(manager, url):
    assert manager.parse_vless(url) is None


@pytest.mark.parametrize("url", [
    "",
    "vmess://uuid-1@fi.example.com:443#vmess",
    "https://fi.example.com/sub#thing",
    "ss://YWVzOnBhc3M@fi.example.com:8388#shadowsocks",
])
def test_parse_vless_non_vless_scheme_return_none(manager, url):
    assert manager.parse_vless(url) is None


def test_dedup_tags_adds_numeric_suffix(manager):
    outbounds = [{"tag": "dup"}, {"tag": "dup"}, {"tag": "dup"}, {"tag": "other"}]
    manager.dedup_tags(outbounds)

    assert [ob["tag"] for ob in outbounds] == ["dup", "dup #2", "dup #3", "other"]


def test_parse_links_missing_file(manager, tmp_path):
    with pytest.raises(manager.ConfigError, match="не найден"):
        manager.parse_links(str(tmp_path / "nope.txt"))


def test_parse_links_without_valid_links(manager, tmp_path):
    path = tmp_path / "links.txt"
    path.write_text("не ссылка\nhttps://example.com/x\n\nvmess://also-not-vless\n",
                    encoding="utf-8")

    with pytest.raises(manager.ConfigError, match="валидных VLESS"):
        manager.parse_links(str(path))


def test_parse_links_skips_junk_and_dedups_tags(manager, tmp_path):
    path = tmp_path / "links.txt"
    path.write_text(
        "\n".join([
            "мусор",
            vless_link("uuid-1", "fi.example.com", FI_TAG, "security=tls"),
            vless_link("uuid-2", "fi2.example.com", FI_TAG, "security=tls"),
            "",
        ]) + "\n",
        encoding="utf-8")

    outbounds = manager.parse_links(str(path))

    assert [ob["tag"] for ob in outbounds] == [FI_TAG, f"{FI_TAG} #2"]


def test_load_settings_missing_file(manager, tmp_path):
    with pytest.raises(manager.ConfigError, match="не найден"):
        manager.load_settings(str(tmp_path / "nope.yaml"))


# ---------------------------------------------------------------------------
# validate_proxies / urltest_block / validate_exclude
# ---------------------------------------------------------------------------

def _proxy(**overrides):
    data = {"tag": "main-socks", "type": "socks", "port": 54321}
    data.update(overrides)
    return data


def test_validate_proxies_valid(manager):
    result = manager.validate_proxies([
        _proxy(),
        _proxy(tag="apps-http", type="http", port=54323, servers=[FI_TAG, NL_TAG]),
    ])

    assert result == [
        {"tag": "main-socks", "type": "socks", "port": 54321, "servers": []},
        {"tag": "apps-http", "type": "http", "port": 54323, "servers": [FI_TAG, NL_TAG]},
    ]


def test_validate_proxies_accepts_mixed_type(manager):
    """mixed — легальный тип инбаунда (SOCKS и HTTP на одном порту)."""
    result = manager.validate_proxies([_proxy(type="mixed")])

    assert result == [{"tag": "main-socks", "type": "mixed", "port": 54321, "servers": []}]


def test_validate_proxies_servers_string_is_normalized_to_list(manager):
    result = manager.validate_proxies([_proxy(servers=FI_TAG)])

    assert result[0]["servers"] == [FI_TAG]


def test_validate_proxies_empty_section(manager):
    with pytest.raises(manager.ConfigError, match="отсутствует секция proxies"):
        manager.validate_proxies(None)


def test_validate_proxies_duplicate_tag(manager):
    with pytest.raises(manager.ConfigError, match="дубль тега"):
        manager.validate_proxies([_proxy(), _proxy(port=54399)])


def test_validate_proxies_duplicate_port(manager):
    with pytest.raises(manager.ConfigError, match="дубль порта"):
        manager.validate_proxies([_proxy(), _proxy(tag="second")])


def test_validate_proxies_unknown_type(manager):
    with pytest.raises(manager.ConfigError, match="неизвестный тип 'socks5'"):
        manager.validate_proxies([_proxy(type="socks5")])


@pytest.mark.parametrize("port", [0, -1, 65536, 70000])
def test_validate_proxies_port_out_of_range(manager, port):
    with pytest.raises(manager.ConfigError, match="вне диапазона"):
        manager.validate_proxies([_proxy(port=port)])


@pytest.mark.parametrize("port", ["54321", None, True, 54321.5])
def test_validate_proxies_port_not_int(manager, port):
    with pytest.raises(manager.ConfigError, match="port должен быть целым числом"):
        manager.validate_proxies([_proxy(port=port)])


@pytest.mark.parametrize("tag", [None, "", 123, ["list"]])
def test_validate_proxies_bad_tag(manager, tag):
    with pytest.raises(manager.ConfigError, match="не указан тег"):
        manager.validate_proxies([_proxy(tag=tag)])


@pytest.mark.parametrize("servers", [[""], [FI_TAG, ""], [123], {"a": 1}])
def test_validate_proxies_bad_servers(manager, servers):
    with pytest.raises(manager.ConfigError, match="servers должен быть списком"):
        manager.validate_proxies([_proxy(servers=servers)])


def test_validate_proxies_proxy_is_not_mapping(manager):
    with pytest.raises(manager.ConfigError, match="ожидается mapping"):
        manager.validate_proxies(["main-socks"])


def test_urltest_block_defaults(manager):
    assert manager.urltest_block(None) == {
        "url": "https://gstatic.com", "interval": "3m", "tolerance": 50}


@pytest.mark.parametrize("cfg, pattern", [
    ({"url": ""}, "urltest.url"),
    ({"interval": 5}, "urltest.interval"),
    ({"tolerance": "50"}, "urltest.tolerance"),
    ({"tolerance": True}, "urltest.tolerance"),
])
def test_urltest_block_type_errors(manager, cfg, pattern):
    with pytest.raises(manager.ConfigError, match=pattern):
        manager.urltest_block(cfg)


def test_validate_exclude(manager):
    assert manager.validate_exclude(["🇷🇺"]) == ["🇷🇺"]

    with pytest.raises(manager.ConfigError, match="exclude_from_auto"):
        manager.validate_exclude([""])


# ---------------------------------------------------------------------------
# build_inbounds / build_pools / build_rules / build_config
# ---------------------------------------------------------------------------

def test_build_inbounds(manager):
    proxies = manager.validate_proxies([_proxy(), _proxy(tag="apps-http", type="http", port=54323)])
    inbounds, tags = manager.build_inbounds(proxies, "10.95.2.1")

    assert inbounds == [
        {"type": "socks", "tag": "main-socks", "listen": "10.95.2.1", "listen_port": 54321},
        {"type": "http", "tag": "apps-http", "listen": "10.95.2.1", "listen_port": 54323},
    ]
    assert tags == ["main-socks", "apps-http"]


def test_build_inbounds_mixed(manager):
    """mixed-инбаунд уходит в config.json с тем же набором listen-полей."""
    proxies = manager.validate_proxies([_proxy(type="mixed")])
    inbounds, tags = manager.build_inbounds(proxies, "10.95.2.1")

    assert inbounds == [
        {"type": "mixed", "tag": "main-socks", "listen": "10.95.2.1", "listen_port": 54321},
    ]
    assert tags == ["main-socks"]


def test_build_pools_creates_urltest_pool(manager):
    proxies = manager.validate_proxies([_proxy(servers=[FI_TAG, NL_TAG])])
    pools = manager.build_pools(proxies, list(ALL_TAGS), None)

    assert pools == [{
        "type": "urltest",
        "tag": "pool-main-socks",
        "outbounds": [FI_TAG, NL_TAG],
        "url": "https://gstatic.com",
        "interval": "3m",
        "tolerance": 50,
    }]


def test_build_pools_skips_proxies_without_servers(manager):
    proxies = manager.validate_proxies([_proxy()])

    assert manager.build_pools(proxies, list(ALL_TAGS), None) == []


def test_build_pools_unknown_server_error_lists_available(manager):
    proxies = manager.validate_proxies([_proxy(servers=[FI_TAG, "🇦🇶 Antarctica"])])

    with pytest.raises(manager.ConfigError) as exc:
        manager.build_pools(proxies, list(ALL_TAGS), None)

    message = str(exc.value)
    assert "🇦🇶 Antarctica" in message
    assert "Доступные серверы" in message
    assert FI_TAG in message


def test_build_rules_order_and_pool_pinning(manager):
    proxies = manager.validate_proxies([
        _proxy(),
        _proxy(tag="apps-http", type="http", port=54323, servers=[FI_TAG]),
    ])
    known = set(ALL_TAGS) | {"auto-select", "direct", "pool-apps-http"}

    rules = manager.build_rules(proxies, None, known)

    assert rules[0] == {"protocol": "dns", "action": "hijack-dns"}
    assert rules[1] == {"inbound": ["main-socks", "apps-http"], "action": "sniff"}
    assert rules[2] == {"inbound": ["apps-http"], "outbound": "pool-apps-http"}
    assert len(rules) == 3  # у main-socks своих серверов нет — правила нет


def test_build_rules_domain_rules_appended(manager):
    proxies = manager.validate_proxies([_proxy()])
    routes = {
        "telegram": {"outbound": FI_TAG, "domains": ["t.me"]},
        "youtube": {"domains": "youtube.com"},  # outbound по умолчанию auto-select
    }
    known = set(ALL_TAGS) | {"auto-select", "direct"}

    rules = manager.build_rules(proxies, routes, known)

    assert rules[-2] == {"domain_suffix": ["t.me"], "outbound": FI_TAG}
    assert rules[-1] == {"domain_suffix": ["youtube.com"], "outbound": "auto-select"}


def test_build_rules_unknown_outbound_warns(manager, capsys):
    proxies = manager.validate_proxies([_proxy()])
    routes = {"broken": {"outbound": "nope-tag", "domains": ["example.com"]}}

    manager.build_rules(proxies, routes, set(ALL_TAGS) | {"auto-select", "direct"})

    assert "неизвестный outbound 'nope-tag'" in capsys.readouterr().err


def test_build_config_structure_and_stats(manager, settings_file):
    settings = manager.load_settings(str(settings_file))
    links = settings_file.parent / "links.txt"
    outbounds = manager.parse_links(str(links))

    config, stats = manager.build_config(settings, outbounds, "127.0.0.1")

    assert config["log"] == {"level": "info", "timestamp": True}
    assert config["route"]["final"] == "auto-select"
    assert config["route"]["default_domain_resolver"] == "dns-local"
    assert [ob["tag"] for ob in config["outbounds"]] == [
        "auto-select", "direct", "pool-apps-http", FI_TAG, NL_TAG, RU_TAG]
    assert config["outbounds"][0]["outbounds"] == [FI_TAG, NL_TAG]  # 🇷🇺 вне auto-select
    assert config["outbounds"][0]["url"] == "https://gstatic.com"
    assert [ib["tag"] for ib in config["inbounds"]] == ["main-socks", "apps-http"]

    assert stats["servers"] == 3
    assert stats["inbounds"] == 2
    assert stats["pools"] == 1
    assert stats["auto_count"] == 2
    assert stats["excluded"] == [RU_TAG]
    assert stats["listen_ip"] == "127.0.0.1"


# ---------------------------------------------------------------------------
# generate_config_file: end-to-end в tmp_path (без моков файловой системы)
# ---------------------------------------------------------------------------

def test_generate_config_file_writes_valid_json(manager, settings_file):
    output_file, stats = manager.generate_config_file(str(settings_file))

    assert Path(output_file) == settings_file.parent / "config.json"
    assert Path(output_file).exists()

    config = json.loads(Path(output_file).read_text(encoding="utf-8"))
    assert config["dns"]["final"] == "dns-local"
    assert config["inbounds"][0]["listen"] == "127.0.0.1"
    assert config["route"]["rules"][0] == {"protocol": "dns", "action": "hijack-dns"}
    assert config["route"]["rules"][1]["action"] == "sniff"
    assert config["route"]["rules"][2] == {"inbound": ["apps-http"], "outbound": "pool-apps-http"}
    assert stats["servers"] == 3


def test_generate_config_file_relative_paths_resolved_from_settings_dir(manager, tmp_path,
                                                                        write_settings,
                                                                        links_file):
    """Скрипт можно запускать из любого CWD: пути берутся от папки YAML."""
    settings = write_settings(output_file="nested/deep/config.json")

    output_file, _ = manager.generate_config_file(str(settings))

    assert Path(output_file) == tmp_path / "nested" / "deep" / "config.json"
    assert Path(output_file).exists()


def test_generate_config_file_cli_overrides(manager, tmp_path, write_settings, links_file):
    # прокси без привязанных серверов: links_file переопределяем на файл с одним сервером
    settings = write_settings(proxies=[{"tag": "main-socks", "type": "socks", "port": 54321}])
    other_links = tmp_path / "other-links.txt"
    other_links.write_text(vless_link("uuid-7", "solo.example.com", "🇩🇪 Germany - Berlin",
                                      "security=tls") + "\n", encoding="utf-8")

    output_file, stats = manager.generate_config_file(
        str(settings), output=str(tmp_path / "elsewhere.json"), links=str(other_links),
        listen_ip="10.95.2.1", exclude_from_auto=[])

    assert Path(output_file) == tmp_path / "elsewhere.json"
    assert stats["servers"] == 1
    assert stats["auto_count"] == 1          # exclude переопределён пустым списком
    assert stats["excluded"] == []
    assert stats["listen_ip"] == "10.95.2.1"


def test_generate_config_file_bad_listen_ip(manager, write_settings, links_file):
    settings = write_settings(listen_ip=None)

    with pytest.raises(manager.ConfigError, match="listen_ip"):
        manager.generate_config_file(str(settings))


def test_generate_config_file_missing_links(manager, write_settings, tmp_path):
    settings = write_settings(links_file="nowhere.txt")

    with pytest.raises(manager.ConfigError, match="файл ссылок"):
        manager.generate_config_file(str(settings))


def test_print_proxy_settings(manager, capsys):
    proxies = manager.validate_proxies([
        _proxy(),
        _proxy(tag="apps-http", type="http", port=54323, servers=[FI_TAG]),
    ])

    manager.print_proxy_settings(proxies, "127.0.0.1")

    out = capsys.readouterr().out
    assert "[SOCKS] main-socks" in out
    assert "[HTTP] apps-http" in out
    assert "auto-select (все, кроме exclude_from_auto)" in out
    assert FI_TAG in out


def test_print_servers_list(manager, settings_file, capsys):
    tags = manager.print_servers_list(str(settings_file))

    assert tags == list(ALL_TAGS)
    out = capsys.readouterr().out
    assert "[1]" in out and FI_TAG in out


# ---------------------------------------------------------------------------
# find_stale_refs
# ---------------------------------------------------------------------------

def test_find_stale_refs_in_proxies_and_routes(manager):
    rt_settings = {
        "proxies": [
            {"tag": "main-socks"},
            {"tag": "apps-http", "servers": [FI_TAG, "🇦🇶 Antarctica - Station"]},
        ],
        "routes": {"telegram": {"outbound": "🇦🇶 Antarctica - Station"}},
    }

    stale = manager.find_stale_refs(rt_settings, list(ALL_TAGS))

    assert ("proxies.apps-http.servers", "🇦🇶 Antarctica - Station") in stale
    assert ("routes.telegram.outbound", "🇦🇶 Antarctica - Station") in stale
    assert len(stale) == 2


def test_find_stale_refs_ignores_service_and_pool_outbounds(manager):
    rt_settings = {
        "proxies": [{"tag": "apps-http", "servers": [FI_TAG, NL_TAG]}],
        "routes": {
            "youtube": {"outbound": "auto-select"},
            "local": {"outbound": "direct"},
            "apps": {"outbound": "pool-apps-http"},
            "extra": {"outbound": RU_TAG},
        },
    }

    assert manager.find_stale_refs(rt_settings, list(ALL_TAGS)) == []


def test_find_stale_refs_empty_settings(manager):
    assert manager.find_stale_refs({}, list(ALL_TAGS)) == []
    assert manager.find_stale_refs({"proxies": None, "routes": None}, []) == []


# ---------------------------------------------------------------------------
# CLI (main): оба сценария + авто-переключение в нон-интерактивный режим
# ---------------------------------------------------------------------------

def test_main_generate_config_flag(manager, settings_file, capsys):
    code = manager.main(["--generate-config", "--settings", str(settings_file)])

    assert code == 0
    assert (settings_file.parent / "config.json").exists()
    out = capsys.readouterr().out
    assert "Готово!" in out
    assert "Серверов: 3, инбаундов: 2, пулов: 1" in out


def test_main_override_flags_switch_off_tui(manager, settings_file):
    """Без --generate-config, но с флагами-переопределениями TUI не запускается."""
    code = manager.main(["--settings", str(settings_file), "--listen-ip", "10.95.2.1"])

    assert code == 0
    config = json.loads((settings_file.parent / "config.json").read_text(encoding="utf-8"))
    assert config["inbounds"][0]["listen"] == "10.95.2.1"


def test_main_servers_list(manager, settings_file, capsys):
    code = manager.main(["--servers-list", "--settings", str(settings_file)])

    assert code == 0
    out = capsys.readouterr().out
    assert "Доступные прокси-серверы" in out
    for tag in ALL_TAGS:
        assert tag in out


def test_main_missing_settings_returns_error(manager, tmp_path, capsys):
    code = manager.main(["--generate-config", "--settings", str(tmp_path / "nope.yaml")])

    assert code == 1
    assert "Ошибка:" in capsys.readouterr().err


def test_main_invalid_config_returns_error(manager, write_settings, links_file, capsys):
    settings = write_settings(proxies=[
        {"tag": "apps-http", "type": "http", "port": 54323, "servers": ["🇦🇶 Antarctica"]}])

    code = manager.main(["--generate-config", "--settings", str(settings)])

    assert code == 1
    err = capsys.readouterr().err
    assert "несуществующие серверы" in err
    assert "Доступные серверы" in err


def test_main_reports_excluded_and_empty_auto_select(manager, write_settings, links_file,
                                                     capsys):
    settings = write_settings(exclude_from_auto=FI_TAG)

    assert manager.main(["--generate-config", "--settings", str(settings)]) == 0
    err = capsys.readouterr().err
    assert "Исключены из auto-select (1)" in err
    assert FI_TAG in err

    settings = write_settings(exclude_from_auto=[FI_TAG, NL_TAG, RU_TAG])

    assert manager.main(["--generate-config", "--settings", str(settings)]) == 0
    assert "auto-select пуст" in capsys.readouterr().err


def test_main_without_questionary_returns_error(manager, monkeypatch, capsys):
    monkeypatch.setattr(manager, "questionary", None)

    code = manager.main([])

    assert code == 1
    assert "questionary" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# Обратная совместимость: шимы и имена функций
# ---------------------------------------------------------------------------

ORIGINAL_FUNCTION_NAMES = [
    # generate_config.py
    "get_first", "parse_vless", "dedup_tags", "parse_links", "load_settings", "as_list",
    "require_mapping", "validate_proxies", "urltest_block", "validate_exclude",
    "build_inbounds", "build_pools", "build_rules", "build_config", "resolve_path",
    "write_json", "generate_config_file", "print_proxy_settings", "main",
    # server_picker.py
    "load_settings_rt", "save_settings_rt", "find_stale_refs", "pick_proxy",
    "filter_and_select", "curl_test", "live_test", "ask_wqx",
]


@pytest.mark.parametrize("name", ORIGINAL_FUNCTION_NAMES)
def test_merged_module_keeps_original_names(manager, name):
    assert callable(getattr(manager, name))


def test_merged_module_keeps_constants(manager):
    assert manager.DEFAULT_SETTINGS == "settings.yaml"
    assert manager.ALLOWED_PROXY_TYPES == ("socks", "http", "mixed")
    assert manager.DEFAULT_EXCLUDE == ["🇷🇺"]
    assert issubclass(manager.ConfigError, Exception)


@pytest.mark.parametrize("location", [REPO_PYTHON_DIR, MODULE_DIR],
                         ids=["python", "python/sing-box"])
@pytest.mark.parametrize("shim", ["generate_config.py", "server_picker.py"])
def test_shims_generate_config(manager, shim, location, settings_file, tmp_path):
    """python3 generate_config.py --generate-config --settings settings.yaml и т.п.
    работают как раньше, но через общий модуль — из любого из двух каталогов."""
    script = location / shim
    if not script.exists():
        pytest.skip(f"{script} отсутствует в этой копии проекта")
    result = subprocess.run(
        [sys.executable, str(script), "--generate-config", "--settings", str(settings_file)],
        cwd=tmp_path, capture_output=True, text=True, timeout=120)

    assert result.returncode == 0, result.stderr
    assert (settings_file.parent / "config.json").exists()


def test_legacy_shim_is_non_interactive_by_default(settings_file, tmp_path):
    """Старый вызов без флагов (как в cron) по-прежнему просто генерирует конфиг."""
    script = find_shim("generate_config.py")
    result = subprocess.run(
        [sys.executable, str(script), "--settings", str(settings_file)],
        cwd=tmp_path, capture_output=True, text=True, timeout=120)

    assert result.returncode == 0, result.stderr
    assert (settings_file.parent / "config.json").exists()


def test_legacy_shim_servers_list_stays_non_interactive(settings_file, tmp_path):
    script = find_shim("generate_config.py")
    result = subprocess.run(
        [sys.executable, str(script), "--servers-list", "--settings", str(settings_file)],
        cwd=tmp_path, capture_output=True, text=True, timeout=120)

    assert result.returncode == 0, result.stderr
    assert "Доступные прокси-серверы" in result.stdout


# ---------------------------------------------------------------------------
# Интерактивные функции на заглушке questionary (базовые ветки)
# ---------------------------------------------------------------------------

def test_pick_proxy_existing_tag(manager, fake_questionary):
    proxies = [{"tag": "main-socks", "servers": []},
               {"tag": "apps-http", "servers": [FI_TAG]}]
    fake_questionary(select=["apps-http"])

    assert manager.pick_proxy(proxies, list(ALL_TAGS)) is proxies[1]


def test_pick_proxy_marks_missing_servers_in_label(manager, fake_questionary):
    proxies = [{"tag": "apps-http", "servers": ["🇦🇶 Antarctica"]}]
    fake = fake_questionary(select=["apps-http"])

    manager.pick_proxy(proxies, list(ALL_TAGS))

    labels = [choice["title"] if isinstance(choice, dict) else choice
              for choice in fake.last_choices["select"]]
    assert "нет в списке серверов" in labels[0]


def test_pick_proxy_create_new(manager, fake_questionary):
    proxies = []
    fake_questionary(select=["+ создать новый", "socks"], text=["new-socks", "54330"])

    entry = manager.pick_proxy(proxies, list(ALL_TAGS))

    assert entry == {"tag": "new-socks", "type": "socks", "port": 54330, "servers": []}
    assert proxies == [entry]


def test_pick_proxy_ctrl_c_exits(manager, fake_questionary):
    fake_questionary(select=[None])

    with pytest.raises(SystemExit) as exc:
        manager.pick_proxy([{"tag": "main-socks"}], list(ALL_TAGS))

    assert exc.value.code == 0


def test_pick_proxy_empty_new_tag_exits(manager, fake_questionary):
    fake_questionary(select=["+ создать новый"], text=[None])

    with pytest.raises(SystemExit) as exc:
        manager.pick_proxy([], list(ALL_TAGS))

    assert "пустым" in str(exc.value)


def test_pick_proxy_bad_port_exits(manager, fake_questionary):
    fake_questionary(select=["+ создать новый", "http"], text=["new-http", "не-число"])

    with pytest.raises(SystemExit) as exc:
        manager.pick_proxy([], list(ALL_TAGS))

    assert "некорректный порт" in str(exc.value)


def test_filter_and_select_ctrl_c_returns_current(manager, fake_questionary):
    fake_questionary(text=[None])

    assert manager.filter_and_select(list(ALL_TAGS), [FI_TAG]) == [FI_TAG]


def test_filter_and_select_filters_by_substring(manager, fake_questionary):
    fake_questionary(text=["nether"], checkbox=[[NL_TAG]], select=["q"])

    assert manager.filter_and_select(list(ALL_TAGS), []) == [NL_TAG]


def test_filter_and_select_w_loops_then_q(manager, fake_questionary):
    fake_questionary(text=["finland", "nether"],
                     checkbox=[[FI_TAG], [NL_TAG]],
                     select=["w", "q"])

    assert manager.filter_and_select(list(ALL_TAGS), []) == sorted([FI_TAG, NL_TAG])


def test_filter_and_select_x_restores_original(manager, fake_questionary):
    fake_questionary(text=["nether"], checkbox=[[NL_TAG]], select=["x"])

    assert manager.filter_and_select(list(ALL_TAGS), [FI_TAG]) == [FI_TAG]


def test_filter_and_select_empty_substring_lists_all(manager, fake_questionary):
    fake = fake_questionary(text=[""], checkbox=[[FI_TAG, RU_TAG]], select=["q"])

    assert manager.filter_and_select(list(ALL_TAGS), []) == sorted([FI_TAG, RU_TAG])
    assert len(fake.last_choices["checkbox"]) == len(ALL_TAGS)


def test_filter_and_select_no_matches_asks_again(manager, fake_questionary, capsys):
    fake_questionary(text=["ничего-такого-нет", None])

    assert manager.filter_and_select(list(ALL_TAGS), [FI_TAG]) == [FI_TAG]
    assert "Ничего не найдено" in capsys.readouterr().out


def test_ask_wqx(manager, fake_questionary):
    fake_questionary(select=["g"])
    assert manager.ask_wqx() == "g"


def test_ask_wqx_ctrl_c_is_cancel(manager, fake_questionary):
    fake_questionary(select=[None])
    assert manager.ask_wqx() == "x"


@pytest.mark.parametrize("func_name", ["pick_proxy", "filter_and_select", "ask_wqx"])
def test_tui_helpers_require_questionary(manager, monkeypatch, func_name):
    monkeypatch.setattr(manager, "questionary", None)

    with pytest.raises(manager.ConfigError, match="questionary"):
        if func_name == "filter_and_select":
            manager.filter_and_select(list(ALL_TAGS), [])
        elif func_name == "ask_wqx":
            manager.ask_wqx()
        else:
            manager.pick_proxy([], [])


def test_run_picker_requires_ruamel(manager, monkeypatch, settings_file):
    monkeypatch.setattr(manager, "YAML", None)

    with pytest.raises(manager.ConfigError, match="ruamel"):
        manager.run_picker(str(settings_file))


def test_run_picker_requires_questionary(manager, monkeypatch, settings_file):
    monkeypatch.setattr(manager, "questionary", None)

    with pytest.raises(manager.ConfigError, match="questionary"):
        manager.run_picker(str(settings_file))


# ---------------------------------------------------------------------------
# Живой тест: только чистые куски (сеть/root/systemd не трогаем)
# ---------------------------------------------------------------------------

def test_curl_test_ok(manager, monkeypatch):
    class Result:
        returncode = 0
        stdout = '{"ip": "1.2.3.4"}'
        stderr = ""

    calls = []

    def fake_run(args, **kwargs):
        calls.append(args)
        return Result()

    monkeypatch.setattr(manager.subprocess, "run", fake_run)

    ok, output = manager.curl_test(54399)

    assert ok is True
    assert output == '{"ip": "1.2.3.4"}'
    assert "-x" in calls[0]
    assert "https://ipinfo.io" in calls[0]


def test_curl_test_timeout(manager, monkeypatch):
    def raise_timeout(args, **kwargs):
        raise manager.subprocess.TimeoutExpired(cmd="curl", timeout=1)

    monkeypatch.setattr(manager.subprocess, "run", raise_timeout)

    ok, output = manager.curl_test(54399)

    assert ok is False
    assert "таймаут" in output


# ---------------------------------------------------------------------------
# Фикстурный links.txt должен оставаться валидным (страховка от правок conftest)
# ---------------------------------------------------------------------------

def test_default_links_fixture_is_parseable(manager, links_file):
    outbounds = manager.parse_links(str(links_file))

    assert [ob["tag"] for ob in outbounds] == list(ALL_TAGS)
    assert DEFAULT_LINKS.strip().splitlines()[0].startswith("vless://")
