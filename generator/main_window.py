"""Главное окно Qt6-редактора settings.yaml.

Окно — это тонкая обвязка: меню/тулбар/дерево/QStackedWidget и диалоги. Вся
логика правок живёт в :class:`generator.model.ProjectModel` (Qt-независима),
поэтому обработчики (``add_proxy``, ``remove_route``, ``generate_config``…)
можно вызывать в тестах напрямую, не эмулируя клики, а модальные диалоги
локализованы в нескольких переопределяемых методах (``_info``/``_error``/
``_ask_save_discard_cancel``), которые тесты подменяют.
"""
from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QAction, QBrush, QColor, QKeySequence
from PyQt6.QtWidgets import (
    QFileDialog,
    QInputDialog,
    QMainWindow,
    QMenu,
    QMessageBox,
    QSplitter,
    QStackedWidget,
    QTreeWidget,
    QTreeWidgetItem,
)

from ._backend import sbm
from .model import ProjectModel, format_stats
from .widgets import (
    DnsEditorPage,
    GeneralEditorPage,
    LinksPage,
    OutputPage,
    PlaceholderPage,
    ProxyEditorPage,
    RouteEditorPage,
)

ROLE_KEY = int(Qt.ItemDataRole.UserRole)
ROLE_KIND = ROLE_KEY + 1
ROLE_NAME = ROLE_KEY + 2


class MainWindow(QMainWindow):
    """Одно окно — один открытый проект settings.yaml."""

    #: испускаются вместо/вместе с модальными диалогами (удобно для тестов)
    project_loaded = pyqtSignal(str)
    config_generated = pyqtSignal(str)
    config_error = pyqtSignal(str)
    generation_done = pyqtSignal(bool, str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.model = ProjectModel()
        self._all_tags = []
        self._links_error = None
        self._items_by_key = {}

        self._build_ui()
        self._build_menus()

        self.reload_tags()
        self._load_pages_from_model()
        self.refresh_tree()
        self._update_title()

    # ------------------------------------------------------------------
    # UI
    # ------------------------------------------------------------------
    def _build_ui(self):
        self.tree = QTreeWidget()
        self.tree.setHeaderHidden(True)
        self.tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._on_context_menu)
        self.tree.currentItemChanged.connect(self._on_tree_current)

        self.page_links = LinksPage(self.model)
        self.page_output = OutputPage(self.model)
        self.page_general = GeneralEditorPage(self.model)
        self.page_dns = DnsEditorPage(self.model)
        self.page_proxy = ProxyEditorPage(self.model)
        self.page_route = RouteEditorPage(self.model)
        self.page_proxies_hint = PlaceholderPage(
            "Выберите прокси, чтобы отредактировать его. "
            "Правая кнопка на узле «Прокси» — добавить новый, на самом прокси — "
            "переименовать или удалить.")
        self.page_routes_hint = PlaceholderPage(
            "Выберите маршрут, чтобы отредактировать его. "
            "Правая кнопка на узле «Routes» — добавить новый.")

        self.stack = QStackedWidget()
        for page in (self.page_links, self.page_output, self.page_general,
                     self.page_dns, self.page_proxy, self.page_route,
                     self.page_proxies_hint, self.page_routes_hint):
            self.stack.addWidget(page)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.addWidget(self.tree)
        splitter.addWidget(self.stack)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([300, 700])
        self.setCentralWidget(splitter)

        self.page_proxy.applied.connect(self._on_proxy_applied)
        self.page_proxy.validation_failed.connect(self._on_validation_failed)
        self.page_route.applied.connect(self._on_route_applied)
        self.page_route.validation_failed.connect(self._on_validation_failed)
        self.page_dns.applied.connect(self._on_generic_applied)
        self.page_dns.validation_failed.connect(self._on_validation_failed)
        self.page_general.applied.connect(self._on_generic_applied)
        self.page_general.validation_failed.connect(self._on_validation_failed)
        self.page_links.applied.connect(self._on_links_applied)
        self.page_links.refresh_requested.connect(self._on_links_refresh)
        self.page_output.applied.connect(self._on_generic_applied)

    def _build_menus(self):
        file_menu = self.menuBar().addMenu("&Файл")

        self.action_new = self._make_action(
            "&Создать", QKeySequence.StandardKey.New, self.new_project,
            "Новый settings.yaml (минимальный валидный скелет)")
        self.action_open = self._make_action(
            "&Открыть проект…", QKeySequence.StandardKey.Open, self.open_project,
            "Открыть settings.yaml")
        self.action_save = self._make_action(
            "&Сохранить", QKeySequence.StandardKey.Save, self.save_project,
            "Сохранить settings.yaml")
        self.action_save_as = self._make_action(
            "Сохранить &как…", QKeySequence.StandardKey.SaveAs, self.save_project_as,
            "Сохранить settings.yaml в новый файл")
        self.action_generate = self._make_action(
            "&Сгенерировать конфигурацию", "Ctrl+G", self.generate_config,
            "Собрать config.json из текущего settings.yaml")

        for action in (self.action_new, self.action_open, self.action_save,
                       self.action_save_as):
            file_menu.addAction(action)
        file_menu.addSeparator()
        file_menu.addAction(self.action_generate)
        file_menu.addSeparator()
        self.action_quit = self._make_action(
            "В&ыход", QKeySequence.StandardKey.Quit, self.close,
            "Закрыть приложение")
        file_menu.addAction(self.action_quit)

        tools_menu = self.menuBar().addMenu("&Tools")
        tools_menu.addAction(self.action_generate)

        toolbar = self.addToolBar("Основное")
        toolbar.addAction(self.action_save)
        toolbar.addAction(self.action_generate)

    def _make_action(self, text, shortcut, slot, tip=""):
        action = QAction(text, self)
        if shortcut:
            action.setShortcut(QKeySequence(shortcut))
        if tip:
            action.setStatusTip(tip)
        action.triggered.connect(slot)
        return action

    # ------------------------------------------------------------------
    # Дерево
    # ------------------------------------------------------------------
    def refresh_tree(self):
        current_key = None
        item = self.tree.currentItem()
        if item is not None:
            current_key = item.data(0, ROLE_KEY)

        spec = self.model.tree_spec(self._all_tags)

        self.tree.blockSignals(True)
        self.tree.clear()
        self._items_by_key = {}
        root = QTreeWidgetItem([spec.title])
        root.setData(0, ROLE_KEY, spec.key)
        root.setData(0, ROLE_KIND, spec.kind)
        root.setData(0, ROLE_NAME, spec.detail)
        self._items_by_key[spec.key] = root
        self._add_children(root, spec)
        self.tree.addTopLevelItem(root)
        self.tree.expandAll()
        self.tree.blockSignals(False)

        if current_key is not None:
            self.select_key(current_key)
        else:
            self.tree.setCurrentItem(root)

    def _add_children(self, parent_item, node):
        for child in node.children:
            item = QTreeWidgetItem([child.title])
            item.setData(0, ROLE_KEY, child.key)
            item.setData(0, ROLE_KIND, child.kind)
            item.setData(0, ROLE_NAME, child.detail)
            if child.stale:
                item.setForeground(0, QBrush(QColor("#c0392b")))
            parent_item.addChild(item)
            self._items_by_key[child.key] = item
            self._add_children(item, child)

    def select_key(self, key):
        item = self._items_by_key.get(key)
        if item is not None:
            self.tree.setCurrentItem(item)
        return item

    def _on_tree_current(self, current, previous):
        if current is None:
            return
        kind = current.data(0, ROLE_KIND)
        name = current.data(0, ROLE_NAME)

        if kind == "links":
            self.page_links.load()
            self.page_links.show_tags(self._all_tags, self._links_error)
            self.stack.setCurrentWidget(self.page_links)
        elif kind == "output":
            self.page_output.load()
            self.stack.setCurrentWidget(self.page_output)
        elif kind == "general":
            self.page_general.load_general(self.model.general_values())
            self.stack.setCurrentWidget(self.page_general)
        elif kind == "dns":
            self.page_dns.load_dns(self.model.dns_values())
            self.stack.setCurrentWidget(self.page_dns)
        elif kind == "proxies":
            self.stack.setCurrentWidget(self.page_proxies_hint)
        elif kind == "routes":
            self.stack.setCurrentWidget(self.page_routes_hint)
        elif kind == "proxy":
            proxy = self.model.get_proxy(name)
            if proxy is not None:
                self.page_proxy.load_proxy(proxy, self._all_tags)
                self.stack.setCurrentWidget(self.page_proxy)
        elif kind == "route":
            route = self.model.get_route(name)
            if route is not None:
                self.page_route.load_route(
                    name, route, self._all_tags, self.model.proxy_tags())
                self.stack.setCurrentWidget(self.page_route)

    # ------------------------------------------------------------------
    # Контекстное меню
    # ------------------------------------------------------------------
    def _on_context_menu(self, pos):
        item = self.tree.itemAt(pos)
        if item is None:
            return
        key = item.data(0, ROLE_KEY)
        name = item.data(0, ROLE_NAME)

        menu = QMenu(self)
        if key == "proxies":
            menu.addAction("Добавить прокси", self.add_proxy_handler)
        elif key == "routes":
            menu.addAction("Добавить маршрут", self.add_route_handler)
        elif isinstance(key, str) and key.startswith("proxy:"):
            menu.addAction("Переименовать…",
                           lambda _=False, n=name: self.rename_proxy_handler(n))
            menu.addAction("Удалить", lambda _=False, n=name: self.remove_proxy(n))
        elif isinstance(key, str) and key.startswith("route:"):
            menu.addAction("Переименовать…",
                           lambda _=False, n=name: self.rename_route_handler(n))
            menu.addAction("Удалить", lambda _=False, n=name: self.remove_route(n))
        else:
            return

        menu.exec(self.tree.viewport().mapToGlobal(pos))

    # ------------------------------------------------------------------
    # Правки прокси/маршрутов (вызываются и из меню, и из тестов)
    # ------------------------------------------------------------------
    def add_proxy_handler(self):
        entry = self.model.add_proxy()
        self._after_model_change()
        self.select_key(f"proxy:{entry['tag']}")
        return entry

    def remove_proxy(self, tag):
        if self.model.remove_proxy(tag):
            self._after_model_change()
            return True
        return False

    def rename_proxy_handler(self, tag):
        new_tag, ok = QInputDialog.getText(self, "Переименовать прокси", "Новый тег:", text=tag)
        if not ok or not new_tag.strip():
            return False
        return self.rename_proxy(tag, new_tag.strip())

    def rename_proxy(self, old, new):
        if self.model.rename_proxy(old, new):
            self._after_model_change()
            self.select_key(f"proxy:{new}")
            return True
        return False

    def add_route_handler(self):
        name = self.model.next_free_route_name()
        entry = self.model.add_route(name)
        self._after_model_change()
        self.select_key(f"route:{name}")
        return entry

    def remove_route(self, name):
        if self.model.remove_route(name):
            self._after_model_change()
            return True
        return False

    def rename_route_handler(self, name):
        new_name, ok = QInputDialog.getText(self, "Переименовать маршрут", "Новое имя:", text=name)
        if not ok or not new_name.strip():
            return False
        return self.rename_route(name, new_name.strip())

    def rename_route(self, old, new):
        if self.model.rename_route(old, new):
            self._after_model_change()
            self.select_key(f"route:{new}")
            return True
        return False

    # ------------------------------------------------------------------
    # Реакция на изменения модели
    # ------------------------------------------------------------------
    def reload_tags(self):
        self._all_tags, self._links_error = self.model.load_server_tags()

    def _load_pages_from_model(self):
        self.page_links.load()
        self.page_output.load()

    def _after_model_change(self):
        self.refresh_tree()
        self._update_title()

    def _on_proxy_applied(self, tag):
        self._after_model_change()
        self.select_key(f"proxy:{tag}")

    def _on_route_applied(self, name):
        self._after_model_change()
        self.select_key(f"route:{name}")

    def _on_generic_applied(self):
        self._after_model_change()

    def _on_links_applied(self):
        self.reload_tags()
        self._after_model_change()
        self.page_links.load()
        self.page_links.show_tags(self._all_tags, self._links_error)

    def _on_links_refresh(self):
        self.reload_tags()
        self.refresh_tree()
        self.page_links.show_tags(self._all_tags, self._links_error)

    def _on_validation_failed(self, message):
        self._error(message)

    def _update_title(self):
        mark = "*" if self.model.dirty else ""
        self.setWindowTitle(f"{mark}{self.model.display_name} — sing-box settings")

    # ------------------------------------------------------------------
    # New / Open / Save
    # ------------------------------------------------------------------
    def new_project(self, skip_confirm=False):
        if not skip_confirm and not self.confirm_pending_changes():
            return False
        self.model.new()
        self.reload_tags()
        self._load_pages_from_model()
        self.refresh_tree()
        self._update_title()
        self.statusBar().showMessage("Создан новый проект", 5000)
        self.project_loaded.emit("")
        return True

    def open_project(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Открыть settings.yaml", str(self.model.settings_dir),
            "YAML (*.yaml *.yml);;Все файлы (*)")
        if not path:
            return False
        return self.open_path(path)

    def open_path(self, path, skip_confirm=True):
        if not skip_confirm and not self.confirm_pending_changes():
            return False
        try:
            self.model.open(path)
        except (sbm.ConfigError, OSError) as e:
            self._error(str(e))
            return False
        self.reload_tags()
        self._load_pages_from_model()
        self.refresh_tree()
        self._update_title()
        self.statusBar().showMessage(f"Открыт {self.model.path}", 5000)
        self.project_loaded.emit(str(self.model.path))
        return True

    def save_project(self):
        if self.model.path is None:
            return self.save_project_as()
        try:
            self.model.save()
        except (sbm.ConfigError, OSError) as e:
            self._error(str(e))
            return False
        self.reload_tags()
        self.refresh_tree()
        self._update_title()
        self.statusBar().showMessage(f"Сохранено в {self.model.path}", 5000)
        return True

    def save_project_as(self):
        default = str(self.model.settings_dir / (self.model.path.name if self.model.path
                                                 else "settings.yaml"))
        path, _ = QFileDialog.getSaveFileName(
            self, "Сохранить settings.yaml", default,
            "YAML (*.yaml *.yml);;Все файлы (*)")
        if not path:
            return False
        try:
            self.model.save(path)
        except (sbm.ConfigError, OSError) as e:
            self._error(str(e))
            return False
        self.reload_tags()
        self._load_pages_from_model()
        self.refresh_tree()
        self._update_title()
        self.statusBar().showMessage(f"Сохранено в {self.model.path}", 5000)
        return True

    # ------------------------------------------------------------------
    # Генерация config.json
    # ------------------------------------------------------------------
    def generate_config(self):
        if self.model.path is None:
            message = "Сначала сохраните settings.yaml (Файл → Сохранить как…)"
            self._error(message)
            self.generation_done.emit(False, message)
            return False

        if self.model.dirty and not self.confirm_pending_changes():
            self.generation_done.emit(False, "отменено пользователем")
            return False

        try:
            output_file, stats = sbm.generate_config_file(str(self.model.path))
        except (sbm.ConfigError, OSError) as e:
            message = str(e)
            self._error(message)
            self.config_error.emit(message)
            self.generation_done.emit(False, message)
            return False

        summary = format_stats(output_file, stats)
        self.statusBar().showMessage(summary.replace("\n", " | "), 10000)
        self._info(summary)
        self.config_generated.emit(str(output_file))
        self.generation_done.emit(True, str(output_file))
        return True

    # ------------------------------------------------------------------
    # Диалоги (переопределяются в тестах)
    # ------------------------------------------------------------------
    def _info(self, message):
        QMessageBox.information(self, "Готово", message)

    def _error(self, message):
        QMessageBox.warning(self, "Ошибка", message)

    def _ask_save_discard_cancel(self):
        box = QMessageBox(self)
        box.setWindowTitle("Несохранённые изменения")
        box.setText("В проекте есть несохранённые изменения. Сохранить?")
        box.setStandardButtons(
            QMessageBox.StandardButton.Save
            | QMessageBox.StandardButton.Discard
            | QMessageBox.StandardButton.Cancel)
        box.setDefaultButton(QMessageBox.StandardButton.Save)
        result = box.exec()
        if result == QMessageBox.StandardButton.Save:
            return "save"
        if result == QMessageBox.StandardButton.Discard:
            return "discard"
        return "cancel"

    def confirm_pending_changes(self):
        """True — можно продолжать (сохранено/отброшено), False — отмена."""
        if not self.model.dirty:
            return True
        choice = self._ask_save_discard_cancel()
        if choice == "save":
            return self.save_project()
        return choice == "discard"

    # ------------------------------------------------------------------
    # Закрытие
    # ------------------------------------------------------------------
    def closeEvent(self, event):
        if self.confirm_pending_changes():
            event.accept()
        else:
            event.ignore()
