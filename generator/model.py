"""Qt-независимая модель проекта settings.yaml.

Здесь живёт вся «голова» GUI: загрузка/сохранение через ruamel round-trip
(``load_settings_rt``/``save_settings_rt`` из sing_box_manager), dirty-флаг,
правки секций proxies/routes, поиск stale-ссылок и построение спецификации
дерева. Qt-слоты окна (см. ``generator.main_window``) — тонкая обвязка над
этими методами, поэтому логику можно тестировать без qtbot.

Ключевой принцип: модуль ничего не знает о PyQt6 и не дублирует логику бэкенда —
валидация/парсинг берутся из sing_box_manager.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from ._backend import sbm

DEFAULT_LISTEN_IP = "127.0.0.1"
DEFAULT_LINKS_FILE = "links.txt"
DEFAULT_OUTPUT_FILE = "config.json"
DEFAULT_URLTEST_URL = "https://gstatic.com"
DEFAULT_URLTEST_INTERVAL = "3m"
DEFAULT_URLTEST_TOLERANCE = 50
DEFAULT_LOG_LEVEL = "info"

#: Минимальный валидный скелет «нового проекта» (с комментариями — грузится
#: через ruamel, поэтому комментарии переживают последующее сохранение).
NEW_SETTINGS_TEMPLATE = """\
# settings.yaml — источник правды для sing-box (создано Qt-редактором).
# Относительные пути (links_file, output_file) резолвятся от папки этого файла.

# IP, на котором слушают входящие прокси
listen_ip: 127.0.0.1

# Файл с VLESS-ссылками (по одной на строку)
links_file: links.txt

# Куда сохранять итоговый конфиг sing-box
output_file: config.json

# Префиксы тегов, которые НЕ попадают в пул auto-select
exclude_from_auto:
- "🇷🇺"

# Параметры urltest-пулов (auto-select и pool-<tag>)
urltest:
  url: "https://gstatic.com"
  interval: "3m"
  tolerance: 50

# Секция log итогового конфига
log:
  level: info
  timestamp: true

# Секция dns итогового конфига
dns:
  servers: []
  rules: []
  final: dns-local

# Прокси для приложений: каждый = отдельный inbound (socks, http или mixed)
proxies: []

# Доменные маршруты поверх прокси
routes: {}
"""


@dataclass
class TreeNode:
    """Узел дерева проекта — чистые данные, без Qt."""

    key: str
    title: str
    kind: str
    stale: bool = False
    detail: str = ""
    children: list = field(default_factory=list)


def new_yaml_rt():
    """ruamel round-trip с теми же настройками, что и в бэкенде."""
    sbm.require_ruamel()
    yaml_rt = sbm.YAML()
    yaml_rt.preserve_quotes = True
    yaml_rt.default_flow_style = False
    return yaml_rt


def to_plain(value):
    """Рекурсивно нормализует дерево ruamel в чистые builtin-типы.

    Round-trip загрузка (``new_yaml_rt``) отдаёт не встроенные типы, а
    ``CommentedMap``/``CommentedSeq`` (подклассы ``dict``/``list``), а
    ``preserve_quotes = True`` превращает закавыченные скаляры в
    ``DoubleQuotedScalarString`` (подкласс ``str``). PyYAML подбирает
    представитель по *точному* типу (``self.yaml_representers[type(data)]``),
    поэтому на любом из этих подклассов ``yaml.safe_dump`` падает в
    ``represent_undefined`` с обманчивым сообщением (объект печатается как
    обычный dict, потому что ``CommentedMap`` наследует ``dict``).

    Возвращает новое дерево из ``dict``/``list``/``str``/``int``/``float``/
    ``bool``/``None``. Порядок ключей сохраняется; ``bool`` проверяется до
    ``int`` (в Python ``bool`` — подкласс ``int``); всё остальное — как есть.
    """
    if isinstance(value, bool):
        return bool(value)
    if isinstance(value, dict):
        return {to_plain(key): to_plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [to_plain(item) for item in value]
    if isinstance(value, str):
        return str(value)
    if isinstance(value, int):
        return int(value)
    if isinstance(value, float):
        return float(value)
    return value


def _split_stale_where(where):
    """Восстанавливает (секция, имя) из строки place из find_stale_refs.

    Тег/имя может содержать точки, поэтому split('.') небезопасен —
    отрезаем известные префикс и суффикс.
    """
    if where.startswith("proxies.") and where.endswith(".servers"):
        return "proxies", where[len("proxies."):-len(".servers")]
    if where.startswith("routes.") and where.endswith(".outbound"):
        return "routes", where[len("routes."):-len(".outbound")]
    return None, None


class ProjectModel:
    """Состояние одного открытого проекта settings.yaml."""

    def __init__(self):
        self.path = None
        self.yaml_rt = new_yaml_rt()
        self.data = self.yaml_rt.load(NEW_SETTINGS_TEMPLATE)
        self._dirty = False

    # ------------------------------------------------------------------
    # Состояние / dirty-флаг
    # ------------------------------------------------------------------
    @property
    def dirty(self):
        return self._dirty

    def mark_dirty(self):
        self._dirty = True

    def mark_clean(self):
        self._dirty = False

    @property
    def display_name(self):
        return self.path.name if self.path else "settings.yaml (новый)"

    @property
    def settings_dir(self):
        """Каталог проекта: от него резолвятся links_file/output_file."""
        if self.path:
            return Path(self.path).parent
        return Path.cwd()

    # ------------------------------------------------------------------
    # New / Open / Save
    # ------------------------------------------------------------------
    def new(self, path=None):
        """Новый минимальный проект; path опционально (Save As — сразу в файл)."""
        self.data = self.yaml_rt.load(NEW_SETTINGS_TEMPLATE)
        self.path = Path(path).resolve() if path else None
        self.mark_clean()
        return self.data

    def open(self, path):
        """Загружает settings.yaml с сохранением комментариев (кидает ConfigError)."""
        data, yaml_rt = sbm.load_settings_rt(str(path))
        if data is None:
            data = self.yaml_rt.load("{}")
        self.data = data
        self.yaml_rt = yaml_rt
        self.path = Path(path).resolve()
        self.mark_clean()
        return self.data

    def save(self, path=None):
        """Пишет проект через round-trip save_settings_rt."""
        if path is not None:
            self.path = Path(path).resolve()
        if self.path is None:
            raise sbm.ConfigError("не задан путь для сохранения settings.yaml")
        sbm.save_settings_rt(str(self.path), self.data, self.yaml_rt)
        self.mark_clean()
        return self.path

    # ------------------------------------------------------------------
    # links_file / output_file / listen_ip
    # ------------------------------------------------------------------
    @property
    def links_file(self):
        value = (self.data or {}).get("links_file")
        return value if isinstance(value, str) and value else DEFAULT_LINKS_FILE

    @property
    def output_file(self):
        value = (self.data or {}).get("output_file")
        return value if isinstance(value, str) and value else DEFAULT_OUTPUT_FILE

    @property
    def listen_ip(self):
        value = (self.data or {}).get("listen_ip")
        return value if isinstance(value, str) and value else DEFAULT_LISTEN_IP

    def resolved_links_path(self):
        return sbm.resolve_path(str(self.settings_dir), self.links_file)

    def resolved_output_path(self):
        return sbm.resolve_path(str(self.settings_dir), self.output_file)

    def set_links_file(self, value):
        self.data["links_file"] = value
        self.mark_dirty()

    def set_output_file(self, value):
        self.data["output_file"] = value
        self.mark_dirty()

    def load_server_tags(self):
        """(теги, ошибка): превью доступных серверов через parse_links."""
        try:
            outbounds = sbm.parse_links(self.resolved_links_path())
        except sbm.ConfigError as e:
            return [], str(e)
        return [ob["tag"] for ob in outbounds], None

    # ------------------------------------------------------------------
    # proxies
    # ------------------------------------------------------------------
    def proxies(self):
        value = (self.data or {}).get("proxies")
        return value if isinstance(value, list) else []

    def _ensure_proxies(self):
        if not isinstance(self.data.get("proxies"), list):
            self.data["proxies"] = []
        return self.data["proxies"]

    def proxy_tags(self):
        return [p.get("tag") for p in self.proxies() if isinstance(p, dict)]

    def get_proxy(self, tag):
        for p in self.proxies():
            if isinstance(p, dict) and p.get("tag") == tag:
                return p
        return None

    def next_free_port(self, start=54321):
        used = {p.get("port") for p in self.proxies() if isinstance(p, dict)}
        port = start
        while port in used and port < 65536:
            port += 1
        return port

    def next_free_tag(self, base="new-proxy"):
        tags = set(self.proxy_tags())
        if base not in tags:
            return base
        n = 2
        while f"{base}-{n}" in tags:
            n += 1
        return f"{base}-{n}"

    def add_proxy(self, tag=None, type_="socks", port=None, servers=None):
        """Добавляет прокси и возвращает его запись (по умолчанию — свободный тег/порт)."""
        proxies = self._ensure_proxies()
        entry = {
            "tag": tag or self.next_free_tag(),
            "type": type_,
            "port": port if port is not None else self.next_free_port(),
        }
        if servers:
            entry["servers"] = list(servers)
        proxies.append(entry)
        self.mark_dirty()
        return entry

    def upsert_proxy(self, candidate, current_tag=None):
        """Заменяет прокси с тегом current_tag (или добавляет новый)."""
        proxies = self._ensure_proxies()
        entry = {
            "tag": candidate["tag"],
            "type": candidate["type"],
            "port": candidate["port"],
        }
        servers = list(candidate.get("servers") or [])
        if servers:
            entry["servers"] = servers
        if current_tag is not None:
            for i, p in enumerate(proxies):
                if isinstance(p, dict) and p.get("tag") == current_tag:
                    proxies[i] = entry
                    self.mark_dirty()
                    return entry
        proxies.append(entry)
        self.mark_dirty()
        return entry

    def remove_proxy(self, tag):
        proxies = self._ensure_proxies()
        for i, p in enumerate(proxies):
            if isinstance(p, dict) and p.get("tag") == tag:
                del proxies[i]
                self.mark_dirty()
                return True
        return False

    def rename_proxy(self, old, new):
        if not new or old == new:
            return False
        proxy = self.get_proxy(old)
        if proxy is None or self.get_proxy(new) is not None:
            return False
        proxy["tag"] = new
        self.mark_dirty()
        return True

    # ------------------------------------------------------------------
    # routes
    # ------------------------------------------------------------------
    def routes(self):
        value = (self.data or {}).get("routes")
        return value if isinstance(value, dict) else {}

    def _ensure_routes(self):
        if not isinstance(self.data.get("routes"), dict):
            self.data["routes"] = {}
        return self.data["routes"]

    def route_names(self):
        return [name for name in self.routes()]

    def get_route(self, name):
        value = self.routes().get(name)
        return value

    def next_free_route_name(self, base="route"):
        names = set(self.route_names())
        if base not in names:
            return base
        n = 2
        while f"{base}-{n}" in names:
            n += 1
        return f"{base}-{n}"

    def add_route(self, name=None, outbound="auto-select", domains=None):
        routes = self._ensure_routes()
        entry = {"outbound": outbound or "auto-select"}
        if domains:
            entry["domains"] = list(domains)
        routes[name or self.next_free_route_name()] = entry
        self.mark_dirty()
        return entry

    def upsert_route(self, name, data, current_name=None):
        routes = self._ensure_routes()
        entry = {"outbound": (data or {}).get("outbound") or "auto-select"}
        domains = list((data or {}).get("domains") or [])
        if domains:
            entry["domains"] = domains

        if current_name is not None and current_name in routes:
            if current_name == name:
                routes[name] = entry
            else:
                items = list(routes.items())
                routes.clear()
                for k, v in items:
                    routes[name if k == current_name else k] = entry if k == current_name else v
            self.mark_dirty()
            return entry

        routes[name] = entry
        self.mark_dirty()
        return entry

    def remove_route(self, name):
        routes = self._ensure_routes()
        if name in routes:
            del routes[name]
            self.mark_dirty()
            return True
        return False

    def rename_route(self, old, new):
        routes = self._ensure_routes()
        if not new or old == new or old not in routes or new in routes:
            return False
        items = list(routes.items())
        routes.clear()
        for k, v in items:
            routes[new if k == old else k] = v
        self.mark_dirty()
        return True

    # ------------------------------------------------------------------
    # Общие настройки / DNS
    # ------------------------------------------------------------------
    def general_values(self):
        data = self.data or {}
        urltest = data.get("urltest") if isinstance(data.get("urltest"), dict) else {}
        log = data.get("log") if isinstance(data.get("log"), dict) else {}
        return {
            "listen_ip": self.listen_ip,
            "urltest": {
                "url": urltest.get("url", DEFAULT_URLTEST_URL),
                "interval": urltest.get("interval", DEFAULT_URLTEST_INTERVAL),
                "tolerance": urltest.get("tolerance", DEFAULT_URLTEST_TOLERANCE),
            },
            "log": {
                "level": log.get("level", DEFAULT_LOG_LEVEL),
                "timestamp": bool(log.get("timestamp", True)),
            },
            "exclude_from_auto": list(data.get("exclude_from_auto") or []),
        }

    def apply_general(self, listen_ip, urltest, log, exclude_from_auto):
        """Правка скалярных полей с сохранением комментариев (мутируем на месте)."""
        self.data["listen_ip"] = listen_ip
        self._update_mapping("urltest", urltest)
        self._update_mapping("log", log)
        self.data["exclude_from_auto"] = list(exclude_from_auto or [])
        self.mark_dirty()

    def dns_values(self):
        """Значения секции dns в чистых builtin-типах (без ruamel-объектов).

        Наружу из модели не должны утекать ``CommentedMap``/``CommentedSeq``/
        ``DoubleQuotedScalarString``: виджеты сериализуют их PyYAML-ом, а он
        падает на подклассах. Нормализуем на границе — тогда любой будущий
        получатель данных получает безопасные типы, а не только DnsEditorPage.
        """
        dns = self.data.get("dns") if isinstance(self.data.get("dns"), dict) else {}
        return {
            "servers": to_plain(dns.get("servers") or []),
            "rules": to_plain(dns.get("rules") or []),
            "final": to_plain(dns.get("final", "")),
        }

    def apply_dns(self, servers, rules, final):
        if not isinstance(self.data.get("dns"), dict):
            self.data["dns"] = {}
        dns = self.data["dns"]
        dns["servers"] = servers
        dns["rules"] = rules
        dns["final"] = final
        self.mark_dirty()

    def _update_mapping(self, key, values):
        node = self.data.get(key)
        if not isinstance(node, dict):
            node = {}
            self.data[key] = node
        for k, v in (values or {}).items():
            node[k] = v

    # ------------------------------------------------------------------
    # Stale-ссылки
    # ------------------------------------------------------------------
    def stale_refs(self, all_tags):
        """Обёртка над find_stale_refs бэкенда (единый источник правды)."""
        return sbm.find_stale_refs(self.data, all_tags)

    def stale_map(self, all_tags):
        """{('proxies'|'routes', имя): [протухшие теги]} для подсветки узлов."""
        result = {}
        for where, tag in self.stale_refs(all_tags):
            section, name = _split_stale_where(where)
            if section is None:
                continue
            result.setdefault((section, name), []).append(tag)
        return result

    # ------------------------------------------------------------------
    # Спецификация дерева (чистые данные)
    # ------------------------------------------------------------------
    def tree_spec(self, all_tags=None):
        """TreeNode-дерево: одинаково пригодно и для QTreeWidget, и для тестов."""
        if all_tags is None:
            all_tags, _ = self.load_server_tags()
        all_tags = list(all_tags or [])
        stale = self.stale_map(all_tags)

        proxy_nodes = []
        for p in self.proxies():
            if not isinstance(p, dict):
                continue
            tag = p.get("tag") or ""
            missing = stale.get(("proxies", tag), [])
            title = tag
            if missing:
                title = f"{tag}  [!] нет в списке серверов: {', '.join(missing)}"
            proxy_nodes.append(TreeNode(
                key=f"proxy:{tag}", title=title, kind="proxy",
                stale=bool(missing), detail=tag))

        route_nodes = []
        for name in self.routes():
            data = self.routes().get(name)
            outbound = data.get("outbound") if isinstance(data, dict) else None
            missing = stale.get(("routes", name), [])
            title = name
            if missing:
                shown = missing[0] if missing else ""
                title = f"{name}  [!] неизвестный outbound: {shown}"
            route_nodes.append(TreeNode(
                key=f"route:{name}", title=title, kind="route",
                stale=bool(missing), detail=name))

        links_missing = ""
        links_path = self.resolved_links_path()
        if not os.path.exists(links_path):
            links_missing = "  [!] файл не найден"

        root = TreeNode(
            key="root", title=self.display_name, kind="root",
            children=[
                TreeNode(key="links", kind="links",
                         title=f"Файл ссылок: {self.links_file}{links_missing}",
                         stale=bool(links_missing)),
                TreeNode(key="output", kind="output",
                         title=f"Output: {self.output_file}"),
                TreeNode(key="general", kind="general", title="Общие настройки"),
                TreeNode(key="dns", kind="dns", title="DNS"),
                TreeNode(key="proxies", kind="proxies", title="Прокси",
                         children=proxy_nodes),
                TreeNode(key="routes", kind="routes", title="Routes",
                         children=route_nodes),
            ],
        )
        return root


def format_stats(output_file, stats):
    """Человекочитаемая сводка генерации (аналог print_proxy_settings в CLI)."""
    lines = [
        f"Конфиг сгенерирован: {output_file}",
        f"Серверов: {stats['servers']}, инбаундов: {stats['inbounds']}, "
        f"пулов: {stats['pools']}",
    ]
    for p in stats.get("proxies", []):
        servers = p["servers"]
        servers_desc = ", ".join(servers) if servers else "auto-select"
        lines.append(f"  [{p['type'].upper()}] {p['tag']} : port {p['port']} -> {servers_desc}")
    if stats.get("excluded"):
        lines.append(f"Исключены из auto-select ({len(stats['excluded'])}): "
                     f"{', '.join(stats['excluded'])}")
    if not stats.get("auto_count"):
        lines.append("Предупреждение: auto-select пуст (все серверы исключены).")
    return "\n".join(lines)
