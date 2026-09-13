Задание: документация, объединение и тесты для sing-box config-тулинга

Репозиторий: /home/yevstigneyevda/Projects/MD/Router/python/sing-box (или соответствующий путь на твоей стороне). В нём два скрипта:

generate_config.py — генератор config.json для sing-box из YAML (settings.yaml): парсит VLESS-ссылки (links.txt/links_file), валидирует секцию proxies, строит inbounds/urltest-пулы (pool-<tag>)/route.rules, пишет итоговый JSON. Ошибки конфигурации — через исключение ConfigError. Ключевые функции: parse_vless, dedup_tags, parse_links, load_settings, validate_proxies, urltest_block, validate_exclude, build_inbounds, build_pools, build_rules, build_config, resolve_path, write_json, print_proxy_settings, generate_config_file (главная точка входа для генерации — читает settings, резолвит пути, парсит ссылки, строит и пишет конфиг, возвращает (output_file, stats)), main (CLI: --settings, --servers-list, --output, --links, --listen-ip, --exclude-from-auto).
server_picker.py — интерактивный TUI (на questionary) для редактирования servers: внутри proxies в settings.yaml: выбор прокси → фильтр по подстроке → чекбоксы → меню [w]/[q]/[x] (после фильтрации) и [w]/[g]/[q]/[x] (в конце: сохранить и продолжить / сохранить+сгенерировать конфиг+рестарт sing-box / сохранить и выйти / выйти без сохранения). Есть --generate-config — нон-интерактивный режим, просто вызывает generate_config_file из первого файла. Есть find_stale_refs — проверка, что теги серверов в proxies[].servers и routes[].outbound всё ещё существуют в текущем links_file (иначе предупреждение в интерфейсе). YAML для перезаписи читается/пишется через ruamel.yaml в round-trip режиме (load_settings_rt/save_settings_rt), чтобы не терять комментарии — это ВАЖНО, не менять на pyyaml при записи. Root не нужен для навигации, только для живого теста (live_test — временно подменяет конфиг, делает systemctl restart sing-box, curl'ит ipinfo.io, восстанавливает бэкап) и генерации/записи, если файлы root-owned.
Зависимости: pyyaml, questionary, ruamel.yaml (см. requirements.txt).

Важное наблюдение про тесты: output_file/links_file в settings.yaml резолвятся ОТНОСИТЕЛЬНО самого YAML-файла (resolve_path), поэтому тестовый settings.yaml, положенный во временную директорию (tmp_path/pytest tmp_path fixture) с output_file: config.json, спокойно пишет config.json туда же — root не нужен вообще, реальный /etc/sing-box/ не трогается. Не надо мокать файловую систему — просто гонять полный цикл на temp-директории.

Три задачи:

1. Документация — ./python/sing-box/docs (файл README.md или директория docs/ с несколькими файлами — что уместнее)
На русском языке. Должна описывать (по итогам задачи 2, то есть уже объединённый модуль): назначение инструмента, формат settings.yaml (все поля: listen_ip, links_file, output_file, exclude_from_auto, urltest, log, dns, proxies[].{tag,type,port,servers}, routes[].{outbound,domains}), как это разворачивается на сервере (root нужен только для генерации/рестарта, не для навигации), CLI-примеры (интерактивный режим, --generate-config, флаги generate_config.py, если их отдельно оставите), пояснение по pool-<tag> и как достраиваются route-правила, механику живого теста и меню [w]/[g]/[q]/[x]. Не переписывать код построчно — объяснять "зачем", а не "что".

2. Слияние generate_config.py + server_picker.py в один файл-библиотеку
Сейчас server_picker.py импортирует generate_config.py как gc — то есть они и так одна логическая единица, просто раскиданы по двум файлам. Нужно свести в один модуль (имя на твоё усмотрение, например sing_box_manager.py, либо оставить server_picker.py как итоговое имя и удалить generate_config.py). Требования:

Сохранить ВСЕ существующие имена функций как есть (для тестируемости и обратной совместимости) — просто убрать import generate_config as gc и все gc. префиксы.
Сохранить оба сценария CLI: интерактивный TUI по умолчанию, и --generate-config (нон-интерактивная генерация, аналог старого python3 generate_config.py). Если разумно — можно сохранить и остальные флаги generate_config.py (--servers-list, --output, --links, --listen-ip, --exclude-from-auto) в объединённом CLI, раз уж это теперь один инструмент.
ConfigError остаётся публичным исключением модуля.
После слияния — прогнать существующий вручную протестированный сценарий: python3 <итоговый_файл>.py --generate-config --settings settings.yaml должен отработать так же, как сейчас работают оба скрипта по отдельности.
3. Тесты — ./python/sing-box/tests
pytest, без сети/root/реального sing-box/curl. Покрыть как минимум:

parse_vless: валидная ссылка с security=reality (проверить flow, tls.reality.public_key, short_id), с security=tls, без security (plain tcp — убедиться что flow/tls не выставляются), битая ссылка без uuid/сервера (должна вернуть None), не-vless-схема.
dedup_tags: дубли тегов получают суффикс #2, #3.
parse_links: несуществующий файл → ConfigError; файл без валидных ссылок → ConfigError.
validate_proxies: валидный случай; дубль tag; дубль port; неизвестный type; port вне диапазона/не int; servers как строка нормализуется в список; пустой/невалидный tag.
build_pools: ссылка на несуществующий сервер → ConfigError с текстом, включающим доступные серверы.
build_rules: порядок правил (hijack-dns первым, sniff вторым), закрепление прокси с servers за pool-<tag> по inbound.
build_config / generate_config_file: end-to-end на маленьком фикстурном settings.yaml + links.txt (2-3 сервера) во tmp_path — проверить, что config.json реально записан, валиден как JSON, и stats (servers, inbounds, pools, auto_count, excluded) верны.
find_stale_refs: находит отсутствующий тег в proxies[].servers и в routes[].outbound, но НЕ считает протухшими auto-select, direct и pool-*.
(по желанию, не обязательно) pick_proxy/filter_and_select/меню — эти функции завязаны на интерактивный ввод через questionary, тестировать через мок questionary.select/checkbox/text/confirm не обязательно, если сочтёшь избыточным для объёма задачи — но если легко, замокай .ask() и проверь базовые ветки (выбор существующего тега, + создать новый, отмена Ctrl+C → None).
Структура: python/sing-box/tests/test_<module>.py, фикстуры через tmp_path/conftest.py. Никаких моков файловой системы для happy-path генерации конфига — просто писать в tmp_path, как обсуждали выше.