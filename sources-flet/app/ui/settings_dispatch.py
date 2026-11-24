"""Route settings views to their hub or detail builders."""
from __future__ import annotations

from ui import settings_detail, settings_hub
from ui.ia_map import route_views


def view_keys() -> frozenset[str]:
    return frozenset(str(view["key"]) for view in route_views("settings"))


def build(page, view: str):
    if not view:
        return settings_hub.build(page)
    if view not in view_keys():
        return settings_hub.build(page)
    return settings_detail.build(page, view)
