"""Валидация форм GUI — без дублирования правил бэкенда.

Правила «уникальный tag/port», «type ∈ {socks, http, mixed}», «port 1..65535» живут
только в ``sing_box_manager.validate_proxies``. Здесь мы лишь собираем список
прокси с подставленным кандидатом и вызываем бэкенд, возвращая текст ошибки
(или None), чтобы форма могла показать его пользователю, а не тихо испортить
settings.yaml.
"""
from __future__ import annotations

from ._backend import sbm


def as_plain_proxy(proxy):
    """dict-представление прокси для validate_proxies (без ruamel-обёрток)."""
    proxy = proxy or {}
    servers = proxy.get("servers")
    if servers is None:
        servers = []
    elif isinstance(servers, str):
        servers = [servers]
    else:
        servers = list(servers)
    return {
        "tag": proxy.get("tag"),
        "type": proxy.get("type"),
        "port": proxy.get("port"),
        "servers": servers,
    }


def validate_proxy_candidate(proxies, candidate, current_tag=None):
    """Проверяет кандидата в контексте остальных прокси.

    current_tag задан при редактировании существующего прокси — его запись
    заменяется кандидатом (иначе он бы конфликтовал сам с собой по тегу/порту).
    Возвращает текст ошибки или None.
    """
    entries = []
    replaced = False
    for p in proxies or []:
        if current_tag is not None and isinstance(p, dict) and p.get("tag") == current_tag:
            entries.append(as_plain_proxy(candidate))
            replaced = True
        else:
            entries.append(as_plain_proxy(p))
    if not replaced:
        entries.append(as_plain_proxy(candidate))

    try:
        sbm.validate_proxies(entries)
    except sbm.ConfigError as e:
        return str(e)
    return None


def validate_non_empty(value, field):
    """Простая проверка непустой строки; возвращает текст ошибки или None."""
    if not isinstance(value, str) or not value.strip():
        return f"{field} должен быть непустой строкой"
    return None


def validate_port(port):
    """Порт из формы — уже int (QSpinBox), но проверим диапазон как в бэкенде."""
    if isinstance(port, bool) or not isinstance(port, int):
        return f"port должен быть целым числом, получено {port!r}"
    if not 0 < port < 65536:
        return f"порт {port} вне диапазона 1..65535"
    return None
