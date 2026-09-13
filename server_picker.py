#!/usr/bin/env python3
#
# Тонкий обратно-совместимый шим.
#
# Интерактивный TUI-пикер серверов теперь живёт вместе с генератором конфига —
# в sing_box_manager.py (раньше это были два файла: server_picker.py и
# generate_config.py). Шим ищет модуль и рядом с собой, и в подкаталоге sing-box/,
# поэтому работает и из python/, и из python/sing-box/.
#
# Логика та же: выбор прокси -> фильтр по подстроке -> чекбоксы -> [w]/[q]/[x],
# затем [w]/[g]/[q]/[x] и (опционально) живой тест через временный scratch-порт
# sing-box. Комментарии в settings.yaml сохраняются (ruamel.yaml, round-trip) —
# PyYAML их бы стёр при перезаписи.
#
# Root не нужен для навигации/выбора — только для живого теста (пишет config.json
# и рестартует sing-box) и записи root-owned settings.yaml.
#
# Использование:
#     python3 server_picker.py [--settings settings.yaml]
#     python3 server_picker.py --settings settings.yaml --generate-config
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
for _candidate in (_HERE / "sing-box", _HERE):
    if (_candidate / "sing_box_manager.py").exists():
        sys.path.insert(0, str(_candidate))
        break

import sing_box_manager as sbm  # noqa: E402

# --- реэкспорт публичных имён (для `import server_picker as sp`) -------------
ConfigError = sbm.ConfigError
DEFAULT_SETTINGS = sbm.DEFAULT_SETTINGS
SCRATCH_PORT = sbm.SCRATCH_PORT
SCRATCH_TAG = sbm.SCRATCH_TAG

load_settings = sbm.load_settings
load_settings_rt = sbm.load_settings_rt
save_settings_rt = sbm.save_settings_rt
find_stale_refs = sbm.find_stale_refs
pick_proxy = sbm.pick_proxy
filter_and_select = sbm.filter_and_select
curl_test = sbm.curl_test
live_test = sbm.live_test
ask_wqx = sbm.ask_wqx
run_picker = sbm.run_picker
parse_links = sbm.parse_links
resolve_path = sbm.resolve_path
generate_config_file = sbm.generate_config_file
print_proxy_settings = sbm.print_proxy_settings


def main(argv=None):
    """Единый CLI: без флагов — TUI, с --generate-config — генерация config.json."""
    return sbm.main(list(sys.argv[1:] if argv is None else argv))


if __name__ == "__main__":
    sys.exit(main())
