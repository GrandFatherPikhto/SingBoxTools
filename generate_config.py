#!/usr/bin/env python3
#
# Тонкий обратно-совместимый шим.
#
# Вся логика (парсинг VLESS, валидация YAML, сборка и запись config.json,
# интерактивный TUI) живёт в sing_box_manager.py — раньше это были два отдельных
# файла (generate_config.py и server_picker.py), теперь один модуль. Шим ищет
# модуль и рядом с собой, и в подкаталоге sing-box/, поэтому работает и из python/,
# и из python/sing-box/.
#
# Файл оставлен только ради совместимости: прежние команды, cron-задачи и деплой
# в /etc/sing-box/generate_config.py продолжают работать без изменений. Шим всегда
# работает в нон-интерактивном режиме генерации (как старый скрипт). Для TUI
# запускайте sing_box_manager.py без флагов.
#
# Использование:
#     python3 generate_config.py [--settings settings.yaml]
#     python3 generate_config.py --settings settings.yaml --servers-list
#     python3 generate_config.py --settings settings.yaml --output /etc/sing-box/config.json
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
for _candidate in (_HERE / "sing-box", _HERE):
    if (_candidate / "sing_box_manager.py").exists():
        sys.path.insert(0, str(_candidate))
        break

import sing_box_manager as sbm  # noqa: E402

# --- реэкспорт публичных имён (для `import generate_config as gc`) -----------
ConfigError = sbm.ConfigError
DEFAULT_SETTINGS = sbm.DEFAULT_SETTINGS
ALLOWED_PROXY_TYPES = sbm.ALLOWED_PROXY_TYPES
DEFAULT_EXCLUDE = sbm.DEFAULT_EXCLUDE

get_first = sbm.get_first
parse_vless = sbm.parse_vless
dedup_tags = sbm.dedup_tags
parse_links = sbm.parse_links
load_settings = sbm.load_settings
as_list = sbm.as_list
require_mapping = sbm.require_mapping
validate_proxies = sbm.validate_proxies
urltest_block = sbm.urltest_block
validate_exclude = sbm.validate_exclude
build_inbounds = sbm.build_inbounds
build_pools = sbm.build_pools
build_rules = sbm.build_rules
build_config = sbm.build_config
resolve_path = sbm.resolve_path
write_json = sbm.write_json
generate_config_file = sbm.generate_config_file
print_proxy_settings = sbm.print_proxy_settings
print_servers_list = sbm.print_servers_list


def main(argv=None):
    """Нон-интерактивный CLI (--generate-config добавляется автоматически,
    если не запрошен только список серверов)."""
    args = list(sys.argv[1:] if argv is None else argv)
    if "--servers-list" not in args and "--generate-config" not in args:
        args = ["--generate-config", *args]
    return sbm.main(args)


if __name__ == "__main__":
    sys.exit(main())
