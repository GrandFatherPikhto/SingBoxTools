"""Qt6-GUI для редактирования settings.yaml (sing-box config tool).

Пакет — новый фронтенд поверх существующего бэкенда ``sing_box_manager.py``:
логика парсинга VLESS, валидации и сборки config.json не дублируется, а
переиспользуется через :mod:`generator._backend`.

Точка входа — ``gui.py`` в корне проекта.
"""
from .model import ProjectModel, TreeNode, format_stats, new_yaml_rt
from .validation import validate_proxy_candidate

__all__ = [
    "ProjectModel",
    "TreeNode",
    "format_stats",
    "new_yaml_rt",
    "validate_proxy_candidate",
]
