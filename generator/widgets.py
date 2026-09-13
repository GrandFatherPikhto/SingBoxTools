"""Qt-виджеты правой панели (QStackedWidget) редактора settings.yaml.

Каждая страница умеет: загрузить состояние из модели (``load_*``), собрать
значения формы (``collect``) и применить их (``apply``). Применение всегда идёт
через :class:`generator.model.ProjectModel`, а валидация формы — через
:mod:`generator.validation` (то есть через правила sing_box_manager).

Страницы не открывают модальных диалогов сами: об ошибке они сообщают сигналом
``validation_failed``, а окно решает, как её показать. Это позволяет тестировать
формы без «живого» модального QMessageBox.
"""
from __future__ import annotations

import yaml
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPlainTextEdit,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from ._backend import sbm
from .validation import validate_non_empty, validate_proxy_candidate


class PlaceholderPage(QWidget):
    """Заглушка для узлов, которым отдельная форма не нужна."""

    def __init__(self, text="", parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        label = QLabel(text)
        label.setWordWrap(True)
        layout.addWidget(label)
        layout.addStretch(1)
        self.label = label


class StringListEditor(QWidget):
    """Редактируемый список строк (аналог checkbox-списка, но без фильтра)."""

    changed = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._list = QListWidget()
        self._edit = QLineEdit()
        self._edit.setPlaceholderText("Добавить строку и нажать Enter")
        add_btn = QPushButton("Добавить")
        remove_btn = QPushButton("Удалить")
        buttons = QHBoxLayout()
        buttons.addWidget(add_btn)
        buttons.addWidget(remove_btn)
        buttons.addStretch(1)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._list)
        layout.addWidget(self._edit)
        layout.addLayout(buttons)

        self._edit.returnPressed.connect(self.add_current)
        add_btn.clicked.connect(self.add_current)
        remove_btn.clicked.connect(self.remove_selected)

    def add_current(self):
        text = self._edit.text().strip()
        if not text:
            return
        QListWidgetItem(text, self._list)
        self._edit.clear()
        self.changed.emit()

    def remove_selected(self):
        for item in self._list.selectedItems():
            self._list.takeItem(self._list.row(item))
        self.changed.emit()

    def values(self):
        return [self._list.item(i).text() for i in range(self._list.count())]

    def set_values(self, values):
        self._list.clear()
        for value in values or []:
            QListWidgetItem(str(value), self._list)


class ProxyEditorPage(QWidget):
    """Форма конкретного прокси: tag/type/port + чекбоксы серверов с фильтром."""

    applied = pyqtSignal(str)          # тег применённого прокси
    validation_failed = pyqtSignal(str)

    def __init__(self, model, parent=None):
        super().__init__(parent)
        self.model = model
        self._current_tag = None
        self._all_tags = []
        self._selected = set()

        self.tag_edit = QLineEdit()
        self.type_combo = QComboBox()
        # Список типов берём из бэкенда — единственное место, где он задан.
        self.type_combo.addItems(list(sbm.ALLOWED_PROXY_TYPES))
        self.port_spin = QSpinBox()
        self.port_spin.setRange(1, 65535)

        form = QFormLayout()
        form.addRow("Тег:", self.tag_edit)
        form.addRow("Тип:", self.type_combo)
        form.addRow("Порт:", self.port_spin)

        self.stale_label = QLabel("")
        self.stale_label.setWordWrap(True)

        self.filter_edit = QLineEdit()
        self.filter_edit.setPlaceholderText("Фильтр по подстроке…")
        self.filter_edit.textChanged.connect(self._repopulate)
        self.count_label = QLabel("")

        self.server_list = QListWidget()
        self.server_list.itemChanged.connect(self._on_item_changed)

        self.apply_btn = QPushButton("Применить")
        self.apply_btn.clicked.connect(self.apply)

        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(self.stale_label)
        layout.addWidget(QLabel("Серверы (ничего не отмечено → auto-select):"))
        layout.addWidget(self.filter_edit)
        layout.addWidget(self.count_label)
        layout.addWidget(self.server_list)
        layout.addWidget(self.apply_btn)

    # -- загрузка -----------------------------------------------------------
    def load_proxy(self, proxy, all_tags):
        if not proxy:
            self._current_tag = None
            return
        self._current_tag = proxy.get("tag")
        self._all_tags = list(all_tags or [])
        self._selected = set(proxy.get("servers") or [])

        self.tag_edit.setText(proxy.get("tag") or "")
        idx = self.type_combo.findText(proxy.get("type") or "socks")
        self.type_combo.setCurrentIndex(idx if idx >= 0 else 0)
        try:
            self.port_spin.setValue(int(proxy.get("port") or 1))
        except (TypeError, ValueError):
            self.port_spin.setValue(1)

        stale = sorted(t for t in self._selected if t not in set(self._all_tags))
        if stale:
            self.stale_label.setText(
                "⚠ Ссылки на серверы, которых нет в списке: " + ", ".join(stale))
        else:
            self.stale_label.setText("")

        self.filter_edit.blockSignals(True)
        self.filter_edit.clear()
        self.filter_edit.blockSignals(False)
        self._repopulate()

    # -- внутреннее ---------------------------------------------------------
    def _display_tags(self):
        known = set(self._all_tags)
        return list(self._all_tags) + sorted(t for t in self._selected if t not in known)

    def _repopulate(self):
        known = set(self._all_tags)
        substr = self.filter_edit.text().strip().lower()
        self.server_list.blockSignals(True)
        self.server_list.clear()
        for tag in self._display_tags():
            if substr and substr not in tag.lower():
                continue
            text = f"{tag}  [!] нет в списке серверов" if tag not in known else tag
            item = QListWidgetItem(text)
            item.setData(Qt.ItemDataRole.UserRole, tag)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(
                Qt.CheckState.Checked if tag in self._selected else Qt.CheckState.Unchecked)
            self.server_list.addItem(item)
        self.server_list.blockSignals(False)
        self.count_label.setText(
            f"Показано: {self.server_list.count()}, выбрано: {len(self._selected)}")

    def _on_item_changed(self, item):
        tag = item.data(Qt.ItemDataRole.UserRole)
        if item.checkState() == Qt.CheckState.Checked:
            self._selected.add(tag)
        else:
            self._selected.discard(tag)
        self.count_label.setText(
            f"Показано: {self.server_list.count()}, выбрано: {len(self._selected)}")

    # -- применение ---------------------------------------------------------
    def collect(self):
        return {
            "tag": self.tag_edit.text().strip(),
            "type": self.type_combo.currentText(),
            "port": int(self.port_spin.value()),
            "servers": sorted(self._selected),
        }

    def apply(self):
        candidate = self.collect()
        error = validate_proxy_candidate(
            self.model.proxies(), candidate, current_tag=self._current_tag)
        if error:
            self.validation_failed.emit(error)
            return False
        self.model.upsert_proxy(candidate, current_tag=self._current_tag)
        self._current_tag = candidate["tag"]
        self.applied.emit(candidate["tag"])
        return True


class RouteEditorPage(QWidget):
    """Форма маршрута: outbound (combo) + редактируемый список domains."""

    applied = pyqtSignal(str)          # имя применённого маршрута
    validation_failed = pyqtSignal(str)

    def __init__(self, model, parent=None):
        super().__init__(parent)
        self.model = model
        self._current_name = None

        self.name_label = QLabel("")
        self.outbound_combo = QComboBox()
        self.outbound_combo.setEditable(True)

        self.domains_editor = StringListEditor()

        form = QFormLayout()
        form.addRow("Маршрут:", self.name_label)
        form.addRow("Outbound:", self.outbound_combo)

        self.apply_btn = QPushButton("Применить")
        self.apply_btn.clicked.connect(self.apply)

        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(QLabel("Домены (domain_suffix):"))
        layout.addWidget(self.domains_editor)
        layout.addWidget(self.apply_btn)

    def load_route(self, name, route, all_tags, proxy_tags):
        self._current_name = name
        self.name_label.setText(name or "")
        route = route or {}

        self.outbound_combo.clear()
        self.outbound_combo.addItems(["auto-select", "direct"])
        for tag in all_tags or []:
            self.outbound_combo.addItem(tag)
        for tag in proxy_tags or []:
            self.outbound_combo.addItem(f"pool-{tag}")
        self.outbound_combo.setCurrentText(route.get("outbound") or "auto-select")
        self.domains_editor.set_values(route.get("domains") or [])

    def collect(self):
        return {
            "outbound": self.outbound_combo.currentText().strip(),
            "domains": self.domains_editor.values(),
        }

    def apply(self):
        data = self.collect()
        if not data["outbound"]:
            self.validation_failed.emit("outbound маршрута не может быть пустым")
            return False
        name = self._current_name or self.model.next_free_route_name()
        self.model.upsert_route(name, data, current_name=self._current_name)
        self._current_name = name
        self.applied.emit(name)
        return True


class DnsEditorPage(QWidget):
    """Форма секции dns: servers/rules (YAML-списки) и final."""

    applied = pyqtSignal()
    validation_failed = pyqtSignal(str)

    def __init__(self, model, parent=None):
        super().__init__(parent)
        self.model = model

        self.final_edit = QLineEdit()
        self.servers_edit = QPlainTextEdit()
        self.servers_edit.setPlaceholderText("- type: local\n  tag: dns-local")
        self.rules_edit = QPlainTextEdit()
        self.rules_edit.setPlaceholderText("[]")

        form = QFormLayout()
        form.addRow("final:", self.final_edit)

        self.apply_btn = QPushButton("Применить")
        self.apply_btn.clicked.connect(self.apply)

        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(QLabel("dns.servers (YAML-список):"))
        layout.addWidget(self.servers_edit)
        layout.addWidget(QLabel("dns.rules (YAML-список):"))
        layout.addWidget(self.rules_edit)
        layout.addWidget(self.apply_btn)

    def load_dns(self, dns):
        dns = dns or {}
        self.final_edit.setText(dns.get("final") or "")
        self.servers_edit.setPlainText(_dump_yaml(dns.get("servers") or []))
        self.rules_edit.setPlainText(_dump_yaml(dns.get("rules") or []))

    def collect(self):
        return {
            "final": self.final_edit.text().strip(),
            "servers": _load_yaml_list(self.servers_edit.toPlainText(), "dns.servers"),
            "rules": _load_yaml_list(self.rules_edit.toPlainText(), "dns.rules"),
        }

    def apply(self):
        try:
            values = self.collect()
        except ValueError as e:
            self.validation_failed.emit(str(e))
            return False
        self.model.apply_dns(values["servers"], values["rules"], values["final"])
        self.applied.emit()
        return True


class GeneralEditorPage(QWidget):
    """Форма общих настроек: listen_ip, urltest, log, exclude_from_auto."""

    applied = pyqtSignal()
    validation_failed = pyqtSignal(str)

    def __init__(self, model, parent=None):
        super().__init__(parent)
        self.model = model

        self.listen_ip_edit = QLineEdit()

        self.urltest_url = QLineEdit()
        self.urltest_interval = QLineEdit()
        self.urltest_tolerance = QSpinBox()
        self.urltest_tolerance.setRange(0, 1_000_000)

        self.log_level = QLineEdit()
        self.log_timestamp = QCheckBox("timestamp")

        self.exclude_editor = StringListEditor()

        form = QFormLayout()
        form.addRow("listen_ip:", self.listen_ip_edit)
        form.addRow("urltest.url:", self.urltest_url)
        form.addRow("urltest.interval:", self.urltest_interval)
        form.addRow("urltest.tolerance:", self.urltest_tolerance)
        form.addRow("log.level:", self.log_level)
        form.addRow("log:", self.log_timestamp)

        self.apply_btn = QPushButton("Применить")
        self.apply_btn.clicked.connect(self.apply)

        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(QLabel("exclude_from_auto:"))
        layout.addWidget(self.exclude_editor)
        layout.addWidget(self.apply_btn)

    def load_general(self, values):
        values = values or {}
        self.listen_ip_edit.setText(values.get("listen_ip") or "")
        urltest = values.get("urltest") or {}
        self.urltest_url.setText(str(urltest.get("url", "")))
        self.urltest_interval.setText(str(urltest.get("interval", "")))
        try:
            self.urltest_tolerance.setValue(int(urltest.get("tolerance", 0)))
        except (TypeError, ValueError):
            self.urltest_tolerance.setValue(0)
        log = values.get("log") or {}
        self.log_level.setText(str(log.get("level", "")))
        self.log_timestamp.setChecked(bool(log.get("timestamp", True)))
        self.exclude_editor.set_values(values.get("exclude_from_auto") or [])

    def collect(self):
        return {
            "listen_ip": self.listen_ip_edit.text().strip(),
            "urltest": {
                "url": self.urltest_url.text().strip(),
                "interval": self.urltest_interval.text().strip(),
                "tolerance": int(self.urltest_tolerance.value()),
            },
            "log": {
                "level": self.log_level.text().strip(),
                "timestamp": self.log_timestamp.isChecked(),
            },
            "exclude_from_auto": self.exclude_editor.values(),
        }

    def apply(self):
        values = self.collect()
        error = validate_non_empty(values["listen_ip"], "listen_ip")
        if error:
            self.validation_failed.emit(error)
            return False
        self.model.apply_general(
            values["listen_ip"], values["urltest"], values["log"],
            values["exclude_from_auto"])
        self.applied.emit()
        return True


class LinksPage(QWidget):
    """links_file + предпросмотр распарсенных тегов серверов (read-only)."""

    applied = pyqtSignal()
    refresh_requested = pyqtSignal()

    def __init__(self, model, parent=None):
        super().__init__(parent)
        self.model = model

        self.path_edit = QLineEdit()
        browse_btn = QPushButton("Обзор…")
        apply_btn = QPushButton("Применить")
        refresh_btn = QPushButton("Обновить превью")

        row = QHBoxLayout()
        row.addWidget(self.path_edit)
        row.addWidget(browse_btn)

        self.preview = QListWidget()
        self.status_label = QLabel("")

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Файл ссылок (links_file, относительно settings.yaml):"))
        layout.addLayout(row)
        layout.addLayout(_buttons(apply_btn, refresh_btn))
        layout.addWidget(self.status_label)
        layout.addWidget(QLabel("Доступные теги серверов:"))
        layout.addWidget(self.preview)

        browse_btn.clicked.connect(self.browse)
        apply_btn.clicked.connect(self.apply)
        refresh_btn.clicked.connect(self.refresh_requested.emit)

    def load(self):
        self.path_edit.setText(self.model.links_file)

    def browse(self):
        from PyQt6.QtWidgets import QFileDialog
        path, _ = QFileDialog.getOpenFileName(
            self, "Файл ссылок", str(self.model.settings_dir), "Текст (*.txt);;Все файлы (*)")
        if path:
            self.path_edit.setText(path)

    def apply(self):
        self.model.set_links_file(self.path_edit.text().strip())
        self.applied.emit()

    def show_tags(self, tags, error=None):
        self.preview.clear()
        if error:
            self.status_label.setText(f"⚠ {error}")
        else:
            self.status_label.setText(f"Тегов: {len(tags)}")
        for tag in tags:
            self.preview.addItem(QListWidgetItem(tag))


class OutputPage(QWidget):
    """output_file — куда пишется итоговый config.json."""

    applied = pyqtSignal()

    def __init__(self, model, parent=None):
        super().__init__(parent)
        self.model = model
        self.path_edit = QLineEdit()
        browse_btn = QPushButton("Обзор…")
        apply_btn = QPushButton("Применить")

        row = QHBoxLayout()
        row.addWidget(self.path_edit)
        row.addWidget(browse_btn)

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Output (output_file, относительно settings.yaml):"))
        layout.addLayout(row)
        layout.addLayout(_buttons(apply_btn))
        layout.addStretch(1)

        browse_btn.clicked.connect(self.browse)
        apply_btn.clicked.connect(self.apply)

    def load(self):
        self.path_edit.setText(self.model.output_file)

    def browse(self):
        from PyQt6.QtWidgets import QFileDialog
        path, _ = QFileDialog.getSaveFileName(
            self, "Куда писать config.json", str(self.model.settings_dir),
            "JSON (*.json);;Все файлы (*)")
        if path:
            self.path_edit.setText(path)

    def apply(self):
        self.model.set_output_file(self.path_edit.text().strip())
        self.applied.emit()


# ---------------------------------------------------------------------------
# утилиты
# ---------------------------------------------------------------------------

def _buttons(*widgets):
    row = QHBoxLayout()
    for widget in widgets:
        row.addWidget(widget)
    row.addStretch(1)
    return row


def _dump_yaml(value):
    return yaml.safe_dump(value, allow_unicode=True, sort_keys=False).strip()


def _load_yaml_list(text, where):
    text = (text or "").strip()
    if not text:
        return []
    try:
        value = yaml.safe_load(text)
    except yaml.YAMLError as e:
        raise ValueError(f"{where}: некорректный YAML — {e}")
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError(f"{where}: ожидается YAML-список")
    return value
