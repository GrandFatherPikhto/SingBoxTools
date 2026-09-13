# sing_box_manager — генератор config.json и TUI-пикер серверов для sing-box

Инструмент решает одну задачу: из **одного YAML-файла** получить рабочий
`config.json` для sing-box и поддерживать его в актуальном состоянии, когда
провайдер переименовывает или удаляет ноды.

Раньше это был набор из двух скриптов, где один импортировал другой, а выбор
серверов приходилось делать вручную в текстовом редакторе (список из ~150 тегов
`🇫🇮 Finland - Helsinki 1` по строкам читать глазами бесполезно) и потом вручную
же сверять, что теги из `servers:` всё ещё существуют в подписке. Теперь это один
модуль [`python/sing-box/sing_box_manager.py`](../sing_box_manager.py:1):

* **генерация конфига** — детерминированная, без интерактива (`--generate-config`);
* **выбор серверов** — интерактивный TUI: фильтр по подстроке, чекбоксы,
  курл-проверка каждого кандидата через живой sing-box;
* **проверка актуальности** — ссылки на исчезнувшие ноды подсвечиваются до сохранения.

Коротко: «зачем» — чтобы добавить/поменять прокси-инбаунд или переопределить
набор серверов за десяток секунд, не переписывая YAML руками и не ломая конфиг
синтаксической ошибкой.

## Из чего состоит проект

| Файл | Роль |
| --- | --- |
| [`python/sing-box/sing_box_manager.py`](../sing_box_manager.py:1) | вся логика: парсинг VLESS, валидация YAML, сборка `config.json`, TUI-пикер, CLI |
| [`gui.py`](../gui.py:1) | единая точка входа Qt6-GUI (PyQt6) для правки `settings.yaml` |
| [`generator/`](../generator/__init__.py:1) | библиотека GUI: Qt-независимая модель, формы-виджеты, главное окно |
| [`python/sing-box/tests/`](../tests/test_sing_box_manager.py:1) | pytest-набор: бэкенд (128 проверок) + GUI через pytest-qt, без сети/root/sing-box/дисплея |
| [`python/sing-box/docs/settings.md`](settings.md:1) | справочник по всем полям `settings.yaml` |
| [`python/sing-box/docs/gui.md`](gui.md:1) | Qt6-GUI: структура `generator/`, меню, дерево, тесты |
| [`python/generate_config.py`](../../generate_config.py:1) | тонкий шим: прежняя команда продолжает работать, но всегда в режиме генерации |
| [`python/server_picker.py`](../../server_picker.py:1) | тонкий шим: прежняя команда открывает TUI (как раньше) |

Шимы оставлены намеренно: старые команды, cron-задачи и разложенные по серверам
копии `generate_config.py` продолжают работать без правок. Логики в них нет —
только поиск каталога с модулем (`sys.path`), реэкспорт публичных имён (для
`import generate_config as gc`) и вызов `main()` общего модуля.

Копии шимов лежат и в `python/`, и рядом с модулем в `python/sing-box/`. Модуль
они находят сами — рядом с собой либо в подкаталоге `sing-box/`, — поэтому
запускать можно из любого из этих каталогов, поведение и результат не отличаются.

Зависимости: `pyyaml` (обязательно), `questionary` и `ruamel.yaml` (только для
интерактивного режима — сборка конфига работает и без них), `PyQt6` (GUI),
`pytest` и `pytest-qt` (тесты, GUI-тесты идут на offscreen-платформе Qt).
См. [`python/requirements.txt`](../../requirements.txt:1).

## Как это работает

```
links_file (VLESS-ссылки)
        │  parse_vless + dedup_tags
        ▼
outbounds  ────────────────────────────────────────────┐
        │                                              │
        │  validate_proxies (proxies из YAML)          │
        ▼                                              ▼
inbounds (socks/http) ──► pools (urltest) ──► route.rules ──► config.json
```

### Плоский список серверов и два уровня выбора

1. **`auto-select`** — один общий urltest-пул со *всеми* серверами, кроме тех,
   чьи теги начинаются с префиксов из `exclude_from_auto` (по умолчанию `🇷🇺`).
   Российские ноды исключаются не из-за патриотизма, а потому что у них самый
   низкий RTT: `urltest` выбрал бы именно их, трафик не покидает РФ и блокировки
   не обходятся. Такие ноды остаются доступны как обычные outbounds.
2. **`pool-<tag>`** — персональный urltest-пул для каждого прокси, у которого в
   `servers:` перечислены конкретные теги. Инбаунд `apps-http` со списком
   `[🇫🇮 Finland - Helsinki 1, 🇳🇱 Netherlands - Amsterdam]` получает пул
   `pool-apps-http`, куда эти два сервера попадают безусловно (они же остаются в
   `auto-select`, если не исключены префиксом). Если `servers` пуст/опущен — пул
   не создаётся и инбаунд живёт на `auto-select`.

### Почему маршрутизация идёт правилами, а не `detour`

В sing-box 1.14 поле `inbound.detour` — это **чейнинг инбаундов**, а не
«аутбаунд по умолчанию». Поэтому закрепление прокси за своим пулом делается
route-правилом по инбаунду, и порядок правил важен:

1. `{"protocol": "dns", "action": "hijack-dns"}` — DNS перехватывается;
2. `{"inbound": [...], "action": "sniff"}` — sniff по всем инбаундам, действие
   не финальное, подбор outbound продолжается;
3. по одному правилу `{"inbound": ["<tag>"], "outbound": "pool-<tag>"}` на
   каждый прокси, у которого есть свои `servers` — это и есть «закрепление»;
4. доменные правила из секции `routes` (`domain_suffix` → `outbound`), они
   накладываются поверх: домен может уйти и в конкретный сервер, и в
   `pool-<tag>`, и в `auto-select`, и в `direct`.

Что не задано явно, уезжает в `route.final: auto-select`; DNS по умолчанию
резолвится локально (`route.default_domain_resolver: dns-local`).

### Куда смотреть, если сервер переименовали

Теги привязаны к подписке. Провайдер может переименовать `🇫🇮 Finland - Helsinki 1`
в `🇫🇮 Finland - Helsinki 01` — тогда `servers:` и `routes[].outbound` начинают
ссылаться в никуда. Это ловит `build_pools` на этапе генерации (жёсткая ошибка с
перечислением доступных серверов) и `find_stale_refs` в TUI (мягкое
предупреждение в интерфейсе до сохранения). Это главная причина, по которой
пикер вообще нужен: обновлять `servers` руками по стопке эмодзи — путь к
невнятным ошибкам «auto-select пуст».

## Развёртывание на сервере

Требования минимальные: Python 3, `pyyaml`, для TUI — `questionary` и
`ruamel.yaml`.

```bash
# на сервере
sudo install -d /etc/sing-box
sudo cp python/sing-box/sing_box_manager.py /etc/sing-box/
sudo cp python/generate_config.py /etc/sing-box/ 2>/dev/null || true   # если нужна старая команда
sudo cp python/links.txt python/settings.yaml /etc/sing-box/
sudo chmod +x /etc/sing-box/sing_box_manager.py
```

**Root нужен ровно в двух местах**, и только в них:

| Действие | Root |
| --- | --- |
| навигация по TUI, фильтры, чекбоксы, просмотр текущего выбора | не нужен |
| чтение `links.txt`, парсинг VLESS, сборка конфига в памяти | не нужен |
| запись `settings.yaml`, если файл root-owned | нужен |
| запись `/etc/sing-box/config.json` | нужен |
| живой тест и `systemctl restart sing-box` | нужен |

Скрипт не требует root заранее: он доходит до момента, где права реально
нужны, и говорит об этом на месте. `--generate-config` пишет рядом с
`settings.yaml` (`output_file` резолвится **относительно самого YAML-файла**),
поэтому в тестах и локальных прогонах ничего не мокается — конфиг просто
уезжает в тот же каталог.

Автообновление подписки раз в неделю — обычный cron:

```bash
curl -fsS -o /etc/sing-box/links.txt https://provider/subscription \
  && python3 /etc/sing-box/sing_box_manager.py --generate-config \
       --settings /etc/sing-box/settings.yaml \
  && sing-box check -c /etc/sing-box/config.json \
  && systemctl restart sing-box
```

## CLI

```
python3 sing_box_manager.py [--settings settings.yaml]              # TUI (по умолчанию)
python3 sing_box_manager.py --settings settings.yaml --generate-config
python3 sing_box_manager.py --settings settings.yaml --servers-list
```

| Флаг | Смысл |
| --- | --- |
| `--settings PATH` | YAML-настройки (по умолчанию `settings.yaml` в текущем каталоге) |
| `--generate-config` | нон-интерактивная генерация: аналог старого `generate_config.py` |
| `--servers-list` | напечатать нумерованный список доступных тегов и выйти |
| `--output PATH` | куда писать `config.json` (переопределяет `output_file`) |
| `--links PATH` | файл VLESS-ссылок (переопределяет `links_file`) |
| `--listen-ip IP` | адрес прослушивания (переопределяет `listen_ip`) |
| `--exclude-from-auto PREFIX...` | префиксы, выкидываемые из `auto-select` (переопределяет `exclude_from_auto`) |

Режим выбирается так: `--servers-list` → только список; `--generate-config`
**или любой из** `--output/--links/--listen-ip/--exclude-from-auto` → генерация;
иначе TUI. Автопереключение сделано ради обратной совместимости: старый
`generate_config.py --listen-ip 10.95.2.1` обязан генерировать, а не открывать
интерактивное меню.

Примеры:

```bash
# посмотреть теги перед правкой YAML
python3 sing_box_manager.py --settings settings.yaml --servers-list

# собрать конфиг локально, не трогая /etc/sing-box
python3 sing_box_manager.py --generate-config --settings settings.yaml \
    --output ./config.dev.json

# собрать конфиг с другим списком ссылок и адресом прослушивания
python3 sing_box_manager.py --generate-config --settings settings.yaml \
    --links ./vpnd.vless.reality.io.txt --listen-ip 10.95.2.1

# применить на сервере
sudo python3 sing_box_manager.py --generate-config --settings settings.yaml
sing-box check -c config.json && sudo systemctl restart sing-box
```

Строки, которые печатает генерация, полезны для быстрой диагностики: сколько
серверов/инбаундов/пулов собрано, какой прокси за каким пулом закреплён, кто
выкинут из `auto-select` и предупреждение, если `auto-select` остался пустым
(значит `exclude_from_auto` съел всё).

## Интерактивный режим (TUI)

Сценарий одного прокси:

1. **выбор прокси** — список `tag`-ов, у которых в `servers:` есть исчезнувшие
   теги, помечен `[!] нет в списке серверов: ...`; пункт `+ создать новый`
   спрашивает тег, тип (`socks`/`http`) и порт;
2. **фильтр по подстроке** — `nether` оставляет только нидерландские ноды,
   Enter — показать все, Ctrl+C — закончить и оставить текущий выбор;
3. **чекбоксы** — отметки *мержатся* с уже выбранным: из фильтрованного среза
   можно доотметить и снять, остальные выбранные серверы не теряются;
4. после чекбоксов — меню `[w]/[q]/[x]`: `w` — вернуться к фильтру (выбрать
   ещё из другого пула совпадений), `q` — зафиксировать этот шаг, `x` — откатить
   всё к тому, что было в YAML до входа в фильтр;
5. предложение **живого теста** (по умолчанию — нет);
6. финальное меню `[w]/[g]/[q]/[x]` (`ask_wqx`).

Финальное меню — как в vim, но с одним дополнением:

| Клавиша | Что делает |
| --- | --- |
| `[w]` | сохранить `settings.yaml` и выбрать ещё один прокси |
| `[g]` | сохранить, сгенерировать `config.json` и перезапустить sing-box |
| `[q]` | сохранить и выйти без генерации (подсказывает, что осталось применить) |
| `[x]` | выйти без сохранения (Ctrl+C/Esc — тоже отказ) |

`settings.yaml` пишется через `ruamel.yaml` в round-trip режиме: комментарии,
порядок ключей и кавычки в файле сохраняются. Обычный PyYAML при перезаписи
вытер бы всю документацию внутри YAML — поэтому здесь он используется только
для *чтения* при сборке конфига.

### Живой тест

Проверяет не «пинг», а факт «через эту ноду трафик ходит». Для каждого
кандидата по очереди:

1. собирается временный конфиг, где в `proxies` остаётся единственный прокси
   `picker-test` на scratch-порту `54399` с одним сервером-кандидатом
   (`build_config` на копии обычных настроек, `time.sleep` не нужен — конфиг
   собирается в памяти);
2. этот конфиг кладётся на место рабочего `config.json`;
3. `systemctl restart sing-box`, пауза ~2 с;
4. `curl -x http://10.95.2.1:54399 -s --max-time 8 https://ipinfo.io` —
   код возврата и тело ответа превращаются в `OK`/`FAIL`/`SKIP`
   (`SKIP` — если тег почему-то не собрался в конфиг);
5. исходный `config.json` бэкапится в `config.json.picker-test.bak` до цикла и
   восстанавливается в `finally`, после чего sing-box перезапускается ещё раз —
   рестарт-цикл переживает и Ctrl+C, и падение curl.

Что важно понимать: тест **кратковременно рвёт соединения всем** текущим
пользователям прокси (это рестарт боевого sing-box), поэтому спрашивается явное
подтверждение, а без root шаг просто пропускается с сообщением, что нужен
`sudo`. Провалившиеся кандидаты не выкидываются из выбора автоматически:
вердикты `OK/FAIL/SKIP` остаются на экране, и решение принимается в финальном
меню.

## Тесты

Набор живёт в [`tests/`](../tests/test_sing_box_manager.py:1) и проверяет и
генератор, и интерактивные ветки — без сети, root, curl и реального sing-box:
заглушка `questionary` возвращает заранее заданные ответы, а полный цикл
генерации пишет `config.json` в `tmp_path` (пути в YAML относительные, так что
файловая система не мокается). GUI-тесты идут через `pytest-qt` на
offscreen-платформе Qt — реальный дисплей не нужен.

```bash
# pytest в систему не обязателен (PEP 668) — хватит venv с системными пакетами
python3 -m venv --system-site-packages /tmp/singbox-test-venv
/tmp/singbox-test-venv/bin/python -m pip install pytest pytest-qt

/tmp/singbox-test-venv/bin/python -m pytest tests -q
```

Покрыто (бэкенд): `parse_vless` (reality / tls / plain tcp / битые ссылки /
не-vless), `dedup_tags`, `parse_links` (нет файла, нет валидных ссылок),
`validate_proxies` (дубли тега и порта, неизвестный тип, порт вне диапазона и
не-int, нормализация `servers`), `urltest_block`, `build_pools` (ошибка со
списком доступных серверов), `build_rules` (порядок `hijack-dns` → `sniff` →
закрепление за `pool-<tag>` → домены), `build_config`/`generate_config_file`
end-to-end со сверкой `stats`, `find_stale_refs` (`auto-select`, `direct` и
`pool-*` протухшими не считаются), оба CLI-сценария `main()` и ветки TUI на
заглушке, а также то, что шимы по-прежнему генерируют конфиг.

Покрыто (GUI): `tests/test_gui_model.py` — New/Open, дерево, stale-ссылки,
CRUD прокси/маршрутов, валидация формы, round-trip save без потери комментариев;
`tests/test_gui_window.py` — обработчики окна через `qtbot` и оба сценария
«Сгенерировать конфигурацию» (валидный проект → `config.json`; битая ссылка →
ошибка через сигнал, без падения).

## GUI (PyQt6)

Помимо CLI/TUI есть Qt6-фронтенд поверх того же бэкенда: дерево проекта,
формы прокси/DNS/маршрутов, отметки протухших ссылок и генерация `config.json`
из меню (в том числе по `Ctrl+G`).

```bash
./gui.py                          # новый проект
./gui.py path/to/settings.yaml    # открыть существующий
```

Бэкенд-логика не дублируется: модель GUI и формы опираются на
[`sing_box_manager.py`](../sing_box_manager.py:1). Подробности — в
[`gui.md`](gui.md:1).

## Смотрите также

* [`gui.md`](gui.md:1) — Qt6-GUI: запуск, структура `generator/`, меню и тесты;
* [`settings.md`](settings.md:1) — все поля `settings.yaml` с дефолтами и
  ошибками валидации;
* [`plan_2026_09_13_create_tests.md`](plan_2026_09_13_create_tests.md:1) —
  исходное задание на слияние, документирование и тесты;
* [`docs/generate-config.md`](../../../docs/generate-config.md:1) и
  [`docs/singbox.md`](../../../docs/singbox.md:1) — общая обвязка sing-box на
  сервере (служба, DNS, ipset).
