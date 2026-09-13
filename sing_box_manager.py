#!/usr/bin/env python3
#
# sing_box_manager.py — единый инструмент для sing-box: генерация config.json
# из YAML-настроек и интерактивный TUI-подбор серверов по прокси.
#
# Это результат слияния двух прежних скриптов:
#   * generate_config.py — сборка config.json из settings.yaml;
#   * server_picker.py   — интерактивный выбор серверов (questionary TUI).
# Все прежние имена функций сохранены (для обратной совместимости и тестов),
# исчезли только префиксы `gc.` и импорт generate_config как модуля.
#
# Единый источник правды — YAML-файл (по умолчанию settings.yaml):
#   - список VLESS-ссылок (links.txt / links_file);
#   - набор прокси (inbounds) с привязкой к своим пулам серверов;
#   - сервисные маршруты по доменам (routes);
#   - log / dns / urltest-параметры.
#
# Для каждого прокси со списком servers создаётся отдельный urltest-пул
# pool-<tag>, а весь трафик инбаунда направляется в него route-правилом по inbound
# (в sing-box 1.14 поле inbound.detour — это чейнинг инбаундов, а не аутбаунд по умолчанию).
#
# Все ошибки конфигурации бросаются как ConfigError и завершают процесс
# с кодом 1 (main возвращает код, а не просто печатает и выходит).
#
# Режимы CLI:
#   * по умолчанию — интерактивный TUI-пикер серверов;
#   * --generate-config (или любой из --output/--links/--listen-ip/--exclude-from-auto)
#     — нон-интерактивная генерация config.json (прежнее поведение generate_config.py);
#   * --servers-list — просто напечатать доступные теги серверов.
#
# Root не нужен для навигации/выбора — только в момент, когда он реально
# требуется: живой тест (пишет config.json и рестартует sing-box, кратковременно
# рвёт соединения всем текущим пользователям прокси) и сохранение settings.yaml,
# если файл root-owned. Если root не хватает, скрипт скажет об этом на месте,
# а не потребует его заранее.
#
# Комментарии в settings.yaml сохраняются (ruamel.yaml, round-trip режим) —
# обычный PyYAML стёр бы их при перезаписи.
import argparse
import copy
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.parse
from pathlib import Path

import yaml

try:  # TUI и round-trip запись нужны только для интерактивного режима:
    import questionary  # noqa: F401
except ImportError:  # pragma: no cover - зависит от окружения
    questionary = None

try:
    from ruamel.yaml import YAML
except ImportError:  # pragma: no cover - зависит от окружения
    YAML = None

DEFAULT_SETTINGS = "settings.yaml"

ALLOWED_PROXY_TYPES = ("socks", "http", "mixed")
DEFAULT_EXCLUDE = ["🇷🇺"]

SCRATCH_PORT = 54399
SCRATCH_TAG = "picker-test"

TOOL_NAME = "sing_box_manager.py"


class ConfigError(Exception):
    """Ошибка конфигурации: печатается в stderr, процесс завершается с кодом 1."""


# ---------------------------------------------------------------------------
# Парсинг VLESS-ссылок
# ---------------------------------------------------------------------------

def get_first(query_dict, key, default=""):
    """Извлекает первую чистую строку из списка значений parse_qs."""
    val = query_dict.get(key, [""])
    if isinstance(val, list) and val:
        clean_val = val[0].strip()
        return clean_val if clean_val else default
    return default


def parse_vless(url):
    """Разбирает одну VLESS-ссылку в outbound sing-box (или None)."""
    try:
        parsed = urllib.parse.urlparse(url.strip())
        if parsed.scheme != "vless":
            return None

        uuid = parsed.username
        server = parsed.hostname
        if not uuid or not server:
            print(f"Пропущена ссылка без UUID или сервера: {url.strip()[:80]}", file=sys.stderr)
            return None

        port = parsed.port if parsed.port else 443
        tag = urllib.parse.unquote(parsed.fragment) if parsed.fragment else f"vpnd-{server}"
        query = urllib.parse.parse_qs(parsed.query)
        security = get_first(query, "security")

        outbound = {
            "type": "vless",
            "tag": tag,
            "server": server,
            "server_port": int(port),
            "uuid": uuid,
        }

        if security == "reality":
            pbk = get_first(query, "pbk")
            if not pbk:
                print(f"Пропущена reality-ссылка без pbk: {url.strip()[:80]}", file=sys.stderr)
                return None
            outbound["flow"] = get_first(query, "flow", "xtls-rprx-vision")
            outbound["tls"] = {
                "enabled": True,
                "server_name": get_first(query, "sni"),
                "utls": {"enabled": True, "fingerprint": get_first(query, "fp", "chrome")},
                "reality": {
                    "enabled": True,
                    "public_key": pbk,
                    "short_id": get_first(query, "sid"),
                },
            }
        elif security == "tls":
            outbound["tls"] = {
                "enabled": True,
                "server_name": get_first(query, "sni"),
                "utls": {"enabled": True, "fingerprint": get_first(query, "fp", "chrome")},
            }
        # Без security (plain tcp): flow и tls не выставляем вовсе

        return outbound
    except Exception as e:
        print(f"Ошибка парсинга ссылки: {e}", file=sys.stderr)
        return None


def dedup_tags(outbounds):
    """Меняет теги-дубликаты на 'тег #2', 'тег #3', ..."""
    seen = {}
    for ob in outbounds:
        base = ob["tag"]
        n = seen.get(base, 0) + 1
        seen[base] = n
        if n > 1:
            ob["tag"] = f"{base} #{n}"


def parse_links(path):
    """Читает links.txt и возвращает список outbounds (кидает ConfigError)."""
    if not os.path.exists(path):
        raise ConfigError(f"файл ссылок {path} не найден")

    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            urls = f.readlines()
    except OSError as e:
        raise ConfigError(f"ошибка чтения {path}: {e}")

    outbounds = []
    for url in urls:
        if url.strip():
            ob = parse_vless(url)
            if ob:
                outbounds.append(ob)

    if not outbounds:
        raise ConfigError("валидных VLESS-ссылок не обнаружено")

    dedup_tags(outbounds)
    return outbounds


# ---------------------------------------------------------------------------
# Загрузка и валидация YAML
# ---------------------------------------------------------------------------

def load_settings(path):
    """Загружает YAML-конфиг скрипта (кидает ConfigError)."""
    if not os.path.exists(path):
        raise ConfigError(f"файл настроек {path} не найден")
    try:
        with open(path, "r", encoding="utf-8") as f:
            return yaml.safe_load(f)
    except Exception as e:
        raise ConfigError(f"ошибка чтения YAML {path}: {e}")


def load_settings_rt(path):
    """Comment-preserving загрузка YAML (ruamel, round-trip режим)."""
    require_ruamel()
    yaml_rt = YAML()
    yaml_rt.preserve_quotes = True
    yaml_rt.default_flow_style = False
    with open(path, "r", encoding="utf-8") as f:
        return yaml_rt.load(f), yaml_rt


def save_settings_rt(path, data, yaml_rt):
    """Записывает YAML через ruamel round-trip (комментарии остаются на месте)."""
    with open(path, "w", encoding="utf-8") as f:
        yaml_rt.dump(data, f)


def as_list(value):
    """Нормализует значение в список."""
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


def require_mapping(value, where):
    if not isinstance(value, dict):
        raise ConfigError(f"{where}: ожидается mapping, получено {type(value).__name__}")


def validate_proxies(proxies):
    """Валидирует и нормализует секцию proxies. Возвращает список dict; кидает ConfigError."""
    if not proxies:
        raise ConfigError("в настройках отсутствует секция proxies (нужен хотя бы один прокси)")

    result = []
    seen_tags = set()
    seen_ports = set()

    for i, p in enumerate(proxies):
        where = f"proxies[{i}]"
        require_mapping(p, where)

        tag = p.get("tag")
        ptype = p.get("type")
        port = p.get("port")

        if not tag or not isinstance(tag, str):
            raise ConfigError(f"{where}: не указан тег (tag) или это не строка")
        if ptype not in ALLOWED_PROXY_TYPES:
            raise ConfigError(
                f"{where} '{tag}': неизвестный тип '{ptype}' "
                f"(ожидается {'|'.join(ALLOWED_PROXY_TYPES)})")
        if isinstance(port, bool) or not isinstance(port, int):
            raise ConfigError(f"{where} '{tag}': port должен быть целым числом, получено {port!r}")
        if not 0 < port < 65536:
            raise ConfigError(f"{where} '{tag}': порт {port} вне диапазона 1..65535")
        if tag in seen_tags:
            raise ConfigError(f"дубль тега инбаунда: {tag}")
        if port in seen_ports:
            raise ConfigError(f"дубль порта инбаунда: {port}")

        seen_tags.add(tag)
        seen_ports.add(port)

        servers = p.get("servers")
        if servers is None:
            servers = []
        elif isinstance(servers, str):
            servers = [servers]
        elif not isinstance(servers, list) or not all(isinstance(s, str) and s for s in servers):
            raise ConfigError(f"{where} '{tag}': servers должен быть списком непустых строк")

        result.append({"tag": tag, "type": ptype, "port": port, "servers": list(servers)})

    return result


def urltest_block(urltest_cfg):
    """Возвращает параметры urltest из YAML с дефолтами и проверкой типов."""
    cfg = urltest_cfg or {}
    require_mapping(cfg, "urltest")

    url = cfg.get("url", "https://gstatic.com")
    interval = cfg.get("interval", "3m")
    tolerance = cfg.get("tolerance", 50)

    if not isinstance(url, str) or not url:
        raise ConfigError(f"urltest.url должен быть непустой строкой, получено {url!r}")
    if not isinstance(interval, str) or not interval:
        raise ConfigError(f"urltest.interval должен быть непустой строкой, получено {interval!r}")
    if isinstance(tolerance, bool) or not isinstance(tolerance, int):
        raise ConfigError(f"urltest.tolerance должен быть целым числом, получено {tolerance!r}")

    return {"url": url, "interval": interval, "tolerance": tolerance}


def validate_exclude(prefixes):
    if not all(isinstance(p, str) and p for p in prefixes):
        raise ConfigError("exclude_from_auto должен быть списком непустых строк")
    return prefixes


# ---------------------------------------------------------------------------
# Сборка конфига sing-box
# ---------------------------------------------------------------------------

def build_inbounds(proxies, listen_ip):
    """Строит inbounds из валидированных proxies и возвращает (inbounds, теги инбаундов).

    Примечание: поле `detour` на инбаунде в sing-box 1.14 — это перенаправление на другой
    inbound (чейнинг), а НЕ аутбаунд по умолчанию. Закрепление за пулом делается через
    route-правила по inbound (см. build_rules), поэтому здесь detour не выставляем.
    """
    inbounds = []
    inbound_tags = []
    for p in proxies:
        inbounds.append({
            "type": p["type"],
            "tag": p["tag"],
            "listen": listen_ip,
            "listen_port": p["port"],
        })
        inbound_tags.append(p["tag"])
    return inbounds, inbound_tags


def build_pools(proxies, all_tags, urltest_cfg):
    """Строит urltest-пулы pool-<tag> для прокси с явным набором servers (кидает ConfigError)."""
    pools = []
    for p in proxies:
        servers = p["servers"]
        if not servers:
            continue
        missing = [s for s in servers if s not in all_tags]
        if missing:
            raise ConfigError(
                f"прокси '{p['tag']}' ссылается на несуществующие серверы: {', '.join(missing)}\n"
                f"Доступные серверы: {', '.join(all_tags) or '(нет)'}")
        pool = {
            "type": "urltest",
            "tag": f"pool-{p['tag']}",
            "outbounds": list(servers),
        }
        pool.update(urltest_block(urltest_cfg))
        pools.append(pool)
    return pools


def build_rules(proxies, routes, known_outbounds):
    """Строит route.rules: hijack-dns, sniff, закрепление инбаундов за пулами и доменные правила.

    Порядок важен:
      1) hijack-dns — перехват DNS;
      2) sniff (все инбаунды) — не завершает подбор (IsFinalAction == false);
      3) закрепление каждого прокси с servers за его pool-<tag> (по inbound) —
         весь трафик инбаунда идёт только через его серверы;
      4) доменные правила поверх.
    """
    rules = [
        {"protocol": "dns", "action": "hijack-dns"},
        {"inbound": [p["tag"] for p in proxies], "action": "sniff"},
    ]

    for p in proxies:
        if p["servers"]:
            rules.append({"inbound": [p["tag"]], "outbound": f"pool-{p['tag']}"})

    for name, data in (routes or {}).items():
        require_mapping(data, f"маршрут '{name}'")
        outbound = data.get("outbound", "auto-select")
        if outbound not in known_outbounds:
            print(f"Предупреждение: маршрут '{name}' ссылается на неизвестный outbound '{outbound}'",
                  file=sys.stderr)
        rules.append({
            "domain_suffix": as_list(data.get("domains")),
            "outbound": outbound,
        })
    return rules


def build_config(settings, outbounds, listen_ip):
    """Собирает полный конфиг sing-box и возвращает (config, stats)."""
    require_mapping(settings, "settings.yaml")

    tags = [ob["tag"] for ob in outbounds]
    urltest_cfg = settings.get("urltest")
    ublock = urltest_block(urltest_cfg)

    proxies = validate_proxies(settings.get("proxies"))
    inbounds, inbound_tags = build_inbounds(proxies, listen_ip)
    pools = build_pools(proxies, tags, urltest_cfg)

    exclude_prefixes = validate_exclude(as_list(settings.get("exclude_from_auto", DEFAULT_EXCLUDE)))
    auto_tags = [t for t in tags if not any(t.startswith(p) for p in exclude_prefixes)]
    excluded_tags = [t for t in tags if t not in auto_tags]

    known_outbounds = set(tags) | {"auto-select", "direct"} | {p["tag"] for p in pools}

    log_cfg = settings.get("log")
    if log_cfg is not None:
        require_mapping(log_cfg, "log")
    dns_cfg = settings.get("dns")
    if dns_cfg is not None:
        require_mapping(dns_cfg, "dns")
    if not dns_cfg:
        print("Предупреждение: в YAML нет секции dns — она будет пустой.", file=sys.stderr)

    config = {
        "log": log_cfg or {},
        "dns": dns_cfg or {},
        "inbounds": inbounds,
        "outbounds": [
            {
                "type": "urltest",
                "tag": "auto-select",
                "outbounds": auto_tags,
                **ublock,
            },
            {"type": "direct", "tag": "direct"},
            *pools,
            *outbounds,
        ],
        "route": {
            "rules": build_rules(proxies, settings.get("routes"), known_outbounds),
            "final": "auto-select",
            "default_domain_resolver": "dns-local",
        },
    }

    stats = {
        "servers": len(outbounds),
        "inbounds": len(inbounds),
        "pools": len(pools),
        "auto_count": len(auto_tags),
        "excluded": excluded_tags,
        "proxies": proxies,
        "listen_ip": listen_ip,
    }
    return config, stats


# ---------------------------------------------------------------------------
# I/O
# ---------------------------------------------------------------------------

def resolve_path(base_dir, path):
    """Приводит path к абсолютному относительно base_dir, если он относительный."""
    return path if os.path.isabs(path) else os.path.join(base_dir, path)


def write_json(path, config):
    """Пишет config в path, создавая директории при необходимости."""
    config_dir = os.path.dirname(path)
    if config_dir and not os.path.exists(config_dir):
        os.makedirs(config_dir, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(config, f, indent=2, ensure_ascii=False)


# ---------------------------------------------------------------------------
# Генерация config.json и её CLI-обвязка
# ---------------------------------------------------------------------------

def generate_config_file(settings_path, output=None, links=None, listen_ip=None,
                         exclude_from_auto=None):
    """Строит и пишет config.json по settings_path (с опциональными переопределениями
    из CLI-флагов). Бросает ConfigError/OSError. Возвращает (output_file, stats)."""
    settings = load_settings(settings_path)
    settings_dir = os.path.dirname(os.path.abspath(settings_path))

    links_file = resolve_path(settings_dir, links or settings.get("links_file", "links.txt"))
    output_file = resolve_path(settings_dir, output or settings.get("output_file", "config.json"))
    resolved_listen_ip = listen_ip or settings.get("listen_ip", "127.0.0.1")
    if not isinstance(resolved_listen_ip, str) or not resolved_listen_ip:
        raise ConfigError("listen_ip должен быть непустой строкой")

    if exclude_from_auto is not None:
        settings = {**settings, "exclude_from_auto": exclude_from_auto}

    outbounds = parse_links(links_file)
    config, stats = build_config(settings, outbounds, resolved_listen_ip)
    write_json(output_file, config)
    return output_file, stats


def print_proxy_settings(proxies, listen_ip):
    """Печатает сводку настроек прокси: ip, port и привязанные серверы."""
    print("\n=== Настройки прокси ===")
    for p in proxies:
        servers = p["servers"]
        if servers:
            servers_desc = ", ".join(servers)
        else:
            servers_desc = "auto-select (все, кроме exclude_from_auto)"
        print(f"  [{p['type'].upper()}] {p['tag']}")
        print(f"      ip:      {listen_ip}")
        print(f"      port:    {p['port']}")
        print(f"      servers: {servers_desc}")


def print_servers_list(settings_path, links=None):
    """Печатает список доступных тегов серверов и возвращает его."""
    settings = load_settings(settings_path)
    settings_dir = os.path.dirname(os.path.abspath(settings_path))
    links_file = resolve_path(settings_dir, links or settings.get("links_file", "links.txt"))
    tags = [ob["tag"] for ob in parse_links(links_file)]
    print("\n=== Доступные прокси-серверы ===")
    for idx, tag in enumerate(tags, 1):
        print(f"[{idx}] {tag}")
    return tags


def generate_and_report(settings_path, output=None, links=None, listen_ip=None,
                        exclude_from_auto=None):
    """generate_config_file + человекочитаемая сводка (общая для обоих CLI-сценариев)."""
    output_file, stats = generate_config_file(
        settings_path, output=output, links=links,
        listen_ip=listen_ip, exclude_from_auto=exclude_from_auto)
    print(f"Готово! Конфиг сгенерирован и сохранён в: {output_file}")
    print(f"Серверов: {stats['servers']}, инбаундов: {stats['inbounds']}, "
          f"пулов: {stats['pools']}")
    print_proxy_settings(stats["proxies"], stats["listen_ip"])
    if stats["excluded"]:
        print(f"Исключены из auto-select ({len(stats['excluded'])}): "
              f"{', '.join(stats['excluded'])}", file=sys.stderr)
    if not stats["auto_count"]:
        print("Предупреждение: auto-select пуст (все серверы исключены).", file=sys.stderr)
    return output_file, stats


# ---------------------------------------------------------------------------
# TUI-пикер серверов
# ---------------------------------------------------------------------------

def require_questionary():
    if questionary is None:
        raise ConfigError(
            "интерактивный режим требует пакет questionary "
            "(pip install -r requirements.txt); для генерации используйте --generate-config")


def require_ruamel():
    if YAML is None:
        raise ConfigError(
            "интерактивный режим требует пакет ruamel.yaml "
            "(pip install -r requirements.txt) — им settings.yaml пишется без потери комментариев")


def find_stale_refs(rt_settings, all_tags):
    """Ищет ссылки на теги серверов, которых больше нет в текущем links_file
    (переименованы/удалены при обновлении списка) — proxies[].servers и routes[].outbound."""
    known = set(all_tags) | {"auto-select", "direct"}
    stale = []
    for p in rt_settings.get("proxies") or []:
        for s in (p.get("servers") or []):
            if s not in known:
                stale.append((f"proxies.{p['tag']}.servers", s))
    for name, data in (rt_settings.get("routes") or {}).items():
        outbound = (data or {}).get("outbound")
        if outbound and outbound not in known and not str(outbound).startswith("pool-"):
            stale.append((f"routes.{name}.outbound", outbound))
    return stale


def pick_proxy(proxies, all_tags):
    require_questionary()
    known = set(all_tags)

    def label(p):
        missing = [s for s in (p.get("servers") or []) if s not in known]
        if missing:
            return f"{p['tag']}  [!] нет в списке серверов: {', '.join(missing)}"
        return p["tag"]

    choices = [questionary.Choice(label(p), value=p["tag"]) for p in proxies]
    choices.append("+ создать новый")
    choice = questionary.select("Какой прокси редактируем?", choices=choices).ask()
    if choice is None:
        sys.exit(0)
    if choice == "+ создать новый":
        tag = questionary.text("Тег нового прокси:").ask()
        if not tag:
            sys.exit("тег не может быть пустым")
        ptype = questionary.select("Тип:", choices=list(ALLOWED_PROXY_TYPES)).ask()
        port_str = questionary.text("Порт:").ask()
        try:
            port = int(port_str)
        except (TypeError, ValueError):
            sys.exit(f"некорректный порт: {port_str!r}")
        entry = {"tag": tag, "type": ptype, "port": port, "servers": []}
        proxies.append(entry)
        return entry
    for p in proxies:
        if p["tag"] == choice:
            return p
    sys.exit(f"внутренняя ошибка: тег '{choice}' не найден")


def filter_and_select(all_tags, preselected):
    """Итеративный цикл: фильтр по подстроке -> чекбоксы -> [w]/[q]/[x]."""
    require_questionary()
    original = sorted(preselected)
    selected = set(preselected)
    while True:
        substr = questionary.text(
            "Фильтр по подстроке (Enter — показать все, Ctrl+C — закончить):"
        ).ask()
        if substr is None:
            return sorted(selected)
        pool = [t for t in all_tags if substr.lower() in t.lower()] if substr else all_tags
        if not pool:
            print(f"Ничего не найдено по '{substr}'.")
            continue
        chosen = questionary.checkbox(
            f"Отметь нужные ({len(pool)} совпадений, уже выбрано всего: {len(selected)}):",
            choices=[questionary.Choice(t, checked=(t in selected)) for t in pool],
        ).ask()
        if chosen is None:
            continue
        chosen_set = set(chosen)
        for t in pool:
            if t in chosen_set:
                selected.add(t)
            else:
                selected.discard(t)

        action = questionary.select(
            "Что дальше?",
            choices=[
                questionary.Choice(f"[w] искать/отмечать ещё (выбрано: {len(selected)})", value="w"),
                questionary.Choice("[q] закончить, использовать этот выбор", value="q"),
                questionary.Choice("[x] отменить всё, вернуть как было", value="x"),
            ],
        ).ask() or "x"
        if action == "q":
            return sorted(selected)
        if action == "x":
            return original


def curl_test(port, timeout=8):
    try:
        result = subprocess.run(
            ["curl", "-x", f"http://10.95.2.1:{port}", "-s", "--max-time", str(timeout),
             "https://ipinfo.io"],
            capture_output=True, text=True, timeout=timeout + 3,
        )
        return result.returncode == 0, (result.stdout.strip() or result.stderr.strip())
    except subprocess.TimeoutExpired:
        return False, "таймаут скрипта"


def live_test(plain_settings, outbounds, listen_ip, config_path, candidates):
    """По очереди подключает каждый candidate как единственный сервер
    scratch-прокси на SCRATCH_PORT, рестартует sing-box, curl'ит ipinfo.io.
    Исходный config.json бэкапится и восстанавливается в finally —
    переживает и Ctrl+C внутри цикла."""
    backup_path = config_path.with_suffix(config_path.suffix + ".picker-test.bak")
    shutil.copy(config_path, backup_path)
    results = []
    try:
        for tag in candidates:
            scratch_settings = copy.deepcopy(plain_settings)
            scratch_settings["proxies"] = [
                {"tag": SCRATCH_TAG, "type": "http", "port": SCRATCH_PORT, "servers": [tag]}
            ]
            try:
                config, _ = build_config(scratch_settings, outbounds, listen_ip)
            except ConfigError as e:
                print(f"  {tag}: SKIP — {e}")
                results.append((tag, "SKIP", str(e)))
                continue
            write_json(str(config_path), config)
            subprocess.run(["systemctl", "restart", "sing-box"], check=True)
            time.sleep(2)
            ok, info = curl_test(SCRATCH_PORT)
            verdict = "OK" if ok else "FAIL"
            print(f"  {tag}: {verdict} — {info[:120]}")
            results.append((tag, verdict, info))
    finally:
        shutil.copy(backup_path, config_path)
        subprocess.run(["systemctl", "restart", "sing-box"], check=False)
        backup_path.unlink()
    return results


def ask_wqx():
    """[w] сохранить и продолжить, [g] сохранить+сгенерировать+рестарт,
    [q] сохранить и выйти, [x] выйти без сохранения."""
    require_questionary()
    choice = questionary.select(
        "Что дальше?",
        choices=[
            questionary.Choice("[w] сохранить и выбрать ещё один прокси", value="w"),
            questionary.Choice("[g] сохранить, сгенерировать config.json и "
                                "перезапустить sing-box", value="g"),
            questionary.Choice("[q] сохранить и выйти (без генерации конфига)", value="q"),
            questionary.Choice("[x] выйти без сохранения", value="x"),
        ],
    ).ask()
    return choice or "x"  # Ctrl+C/Esc — как явный отказ


def run_picker(settings_path):
    """Интерактивный режим: правка servers: в settings.yaml и (опционально)
    живой тест + генерация config.json + рестарт sing-box."""
    require_questionary()
    require_ruamel()

    settings_path = Path(settings_path).resolve()
    settings_dir = settings_path.parent

    # ruamel round-trip — для финальной записи, сохраняет комментарии
    rt_settings, yaml_rt = load_settings_rt(settings_path)
    # обычный PyYAML — для сборки конфига при живом тесте
    plain_settings = load_settings(str(settings_path))

    if not rt_settings.get("proxies"):
        raise ConfigError("в настройках отсутствует секция proxies (нужен хотя бы один прокси)")

    links_file = resolve_path(str(settings_dir), plain_settings.get("links_file", "links.txt"))
    output_file = resolve_path(str(settings_dir), plain_settings.get("output_file", "config.json"))
    listen_ip = plain_settings.get("listen_ip", "127.0.0.1")

    outbounds = parse_links(links_file)
    all_tags = [ob["tag"] for ob in outbounds]
    print(f"Настройки: {settings_path}")
    print(f"Список серверов: {links_file} ({len(all_tags)} тегов)")

    stale = find_stale_refs(rt_settings, all_tags)
    if stale:
        print(f"\n⚠ Ссылки на серверы, которых больше нет в {Path(links_file).name} "
              f"(переименованы/удалены):")
        for where, tag in stale:
            print(f"  {where}: {tag!r}")
        print("Открой соответствующий прокси ниже и переопредели servers.\n")

    while True:
        proxy = pick_proxy(rt_settings["proxies"], all_tags)
        current = list(proxy.get("servers") or [])
        print(f"\nТекущие серверы '{proxy['tag']}': {', '.join(current) or '(нет — auto-select)'}\n")

        missing = [t for t in current if t not in all_tags]
        if missing:
            print(f"⚠ Внимание: {', '.join(missing)} отсутствует в {Path(links_file).name} "
                  f"— переименован или удалён, автоматически снят с выбора. "
                  f"Переопредели ниже через фильтр.\n")

        selected = filter_and_select(all_tags, [t for t in current if t in all_tags])
        if not selected:
            print("Ничего не выбрано, пропускаю.\n")
            if not questionary.confirm("Выбрать ещё один прокси?", default=False).ask():
                return 0
            continue

        print(f"\nВыбрано ({len(selected)}): {', '.join(selected)}")

        if questionary.confirm(f"Прогнать живой тест ({len(selected)} серверов, по очереди)?",
                               default=False).ask():
            if os.geteuid() != 0:
                print("Живой тест требует root (пишет config.json и "
                      "рестартует sing-box) — пропускаю, перезапусти скрипт через sudo.")
            else:
                print("\nЖивой тест (каждый — временный рестарт sing-box, ~2-3 сек):")
                live_test(plain_settings, outbounds, listen_ip, Path(output_file), selected)

        action = ask_wqx()
        if action == "x":
            print("Отменено, ничего не сохранено.\n")
        else:
            proxy["servers"] = selected
            try:
                save_settings_rt(settings_path, rt_settings, yaml_rt)
            except PermissionError:
                sys.exit(f"нет прав на запись {settings_path} — перезапусти скрипт через sudo")
            print(f"\nСохранено в {settings_path} ('{proxy['tag']}': {', '.join(selected)}).")

            if action == "g":
                if os.geteuid() != 0:
                    print("Генерация config.json и рестарт sing-box требуют root — "
                          "перезапусти скрипт через sudo (settings.yaml уже сохранён).")
                else:
                    try:
                        out_path, gstats = generate_config_file(str(settings_path))
                        print(f"Конфиг сгенерирован: {out_path} "
                              f"(серверов: {gstats['servers']}, пулов: {gstats['pools']})")
                        subprocess.run(["systemctl", "restart", "sing-box"], check=True)
                        print("sing-box перезапущен.")
                    except ConfigError as e:
                        print(f"Ошибка генерации конфига: {e}", file=sys.stderr)
                    except subprocess.CalledProcessError as e:
                        print(f"Ошибка рестарта sing-box: {e}", file=sys.stderr)

        if action in ("q", "x", "g"):
            if action == "q":
                entry = Path(sys.argv[0]).name or TOOL_NAME
                print("Осталось применить:")
                print(f"  cd {settings_dir} && sudo python3 {entry} --generate-config "
                      f"--settings {settings_path.name} && sudo systemctl restart sing-box")
            return 0


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def build_parser():
    parser = argparse.ArgumentParser(
        prog=TOOL_NAME,
        description="sing-box: генератор config.json из YAML-настроек + интерактивный "
                    "TUI-пикер серверов.",
        epilog="Без флагов запускается интерактивный TUI. Любой из флагов "
               "--output/--links/--listen-ip/--exclude-from-auto (или явный "
               "--generate-config) переводит запуск в нон-интерактивную генерацию.")
    parser.add_argument("--settings", default=DEFAULT_SETTINGS,
                        help=f"Путь к YAML-настройкам (по умолчанию: {DEFAULT_SETTINGS})")
    parser.add_argument("--generate-config", action="store_true",
                        help="Не входить в интерактивный режим — просто сгенерировать "
                             "config.json из текущего settings.yaml")
    parser.add_argument("--servers-list", action="store_true",
                        help="Показать список всех доступных серверов и выйти")
    parser.add_argument("--output", "--config", dest="output", default=None,
                        help="Путь для сохранения config.json (переопределяет output_file из YAML)")
    parser.add_argument("--links", default=None,
                        help="Путь к файлу с VLESS-ссылками (переопределяет links_file из YAML)")
    parser.add_argument("--listen-ip", default=None,
                        help="IP для listen (переопределяет listen_ip из YAML)")
    parser.add_argument("--exclude-from-auto", nargs="*", default=None,
                        help="Префиксы тегов, исключаемые из auto-select "
                             "(переопределяет exclude_from_auto из YAML)")
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)

    overrides_given = any(v is not None for v in
                          (args.output, args.links, args.listen_ip, args.exclude_from_auto))

    try:
        if args.servers_list:
            print_servers_list(args.settings, args.links)
            return 0

        if args.generate_config or overrides_given:
            generate_and_report(args.settings, output=args.output, links=args.links,
                                listen_ip=args.listen_ip,
                                exclude_from_auto=args.exclude_from_auto)
            return 0

        return run_picker(args.settings)

    except ConfigError as e:
        print(f"Ошибка: {e}", file=sys.stderr)
        return 1
    except OSError as e:
        print(f"Ошибка ввода-вывода: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
