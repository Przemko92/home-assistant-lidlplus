"""The app's shopping lists as Home Assistant to-do lists."""

from __future__ import annotations

import uuid
from typing import Any

from homeassistant.components.todo import (
    TodoItem,
    TodoItemStatus,
    TodoListEntity,
    TodoListEntityFeature,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.util import dt as dt_util

from . import LidlPlusConfigEntry
from .coordinator import LidlPlusCoordinator
from .entity import LidlPlusEntity
from .exceptions import LidlPlusError
from .extras import ShoppingList

PARALLEL_UPDATES = 1

# Items typed in by hand; products picked from the catalog are "ProductCatalog".
FREE_TEXT = "FreeText"


async def async_setup_entry(
    hass: HomeAssistant,
    entry: LidlPlusConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator = entry.runtime_data
    if not coordinator.shopping_lists_enabled:
        # Option off: drop to-do entities left from when it was on.
        registry = er.async_get(hass)
        for entity in er.async_entries_for_config_entry(registry, entry.entry_id):
            if entity.domain == "todo":
                registry.async_remove(entity.entity_id)
        return
    known: set[str] = set()

    @callback
    def add_new_lists() -> None:
        lists = coordinator.data.shopping_lists if coordinator.data else []
        new = [item for item in lists if item.list_id not in known]
        known.update(item.list_id for item in new)
        if new:
            async_add_entities(
                LidlPlusShoppingList(coordinator, item.list_id, item.title) for item in new
            )

    add_new_lists()
    entry.async_on_unload(coordinator.async_add_listener(add_new_lists))


def _now() -> str:
    """lastUpdate like the app writes it: local time, no zone, milliseconds.

    Local time is never earlier than UTC here, so the change also wins when the
    server compares against UTC stamps.
    """
    return dt_util.now().strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3]


class LidlPlusShoppingList(LidlPlusEntity, TodoListEntity):
    """One shopping list from the Lidl Plus app, synced both ways."""

    _attr_supported_features = (
        TodoListEntityFeature.CREATE_TODO_ITEM
        | TodoListEntityFeature.UPDATE_TODO_ITEM
        | TodoListEntityFeature.DELETE_TODO_ITEM
    )

    def __init__(
        self, coordinator: LidlPlusCoordinator, list_id: str, title: str | None
    ) -> None:
        super().__init__(coordinator)
        self._list_id = list_id
        self._attr_unique_id = f"{self._device_id}_shopping_list_{list_id}"
        self._attr_name = title or "Lista zakupów"

    @property
    def _list(self) -> ShoppingList | None:
        data = self.coordinator.data
        return next(
            (
                item
                for item in (data.shopping_lists if data else [])
                if item.list_id == self._list_id
            ),
            None,
        )

    @property
    def available(self) -> bool:
        return super().available and self._list is not None

    @property
    def todo_items(self) -> list[TodoItem] | None:
        shopping_list = self._list
        if shopping_list is None:
            return None
        return [
            TodoItem(
                uid=str(item["id"]),
                summary=self._summary(item),
                status=TodoItemStatus.COMPLETED
                if item.get("isChecked")
                else TodoItemStatus.NEEDS_ACTION,
            )
            for item in shopping_list.items
            if item.get("id")
        ]

    @staticmethod
    def _summary(item: dict[str, Any]) -> str:
        title = str(item.get("title") or "")
        quantity = item.get("quantity")
        if isinstance(quantity, (int, float)) and quantity > 1:
            return f"{title} ×{quantity:g}"
        return title

    def _writable(self) -> ShoppingList:
        shopping_list = self._list
        if shopping_list is None or not shopping_list.loaded:
            raise HomeAssistantError("Lidl Plus shopping list is not loaded yet")
        return shopping_list

    async def async_create_todo_item(self, item: TodoItem) -> None:
        shopping_list = self._writable()
        new = {
            "id": str(uuid.uuid4()),
            "type": FREE_TEXT,
            "title": item.summary or "",
            "quantity": 1,
            "isChecked": item.status == TodoItemStatus.COMPLETED,
            "lastUpdate": _now(),
        }
        await self._change(
            shopping_list,
            [new],
            item_ids=[*shopping_list.item_ids, new["id"]],
            saved=lambda lst: new["id"] in lst.item_ids,
        )

    async def async_update_todo_item(self, item: TodoItem) -> None:
        shopping_list = self._writable()
        current = next(
            (entry for entry in shopping_list.items if str(entry.get("id")) == item.uid),
            None,
        )
        if current is None:
            raise HomeAssistantError(f"Item {item.uid} is not on the list")
        changed = {
            **current,
            "isChecked": item.status == TodoItemStatus.COMPLETED,
            "lastUpdate": _now(),
        }
        # Renaming only touches hand-typed items; catalog products keep their title.
        if item.summary and item.summary != self._summary(current):
            changed["title"] = item.summary

        def saved(lst: ShoppingList) -> bool:
            stored = next(
                (entry for entry in lst.items if str(entry.get("id")) == item.uid), {}
            )
            return bool(stored.get("isChecked")) == changed["isChecked"] and (
                stored.get("title") == changed["title"]
            )

        # Retry with the server's own lastUpdate in case it versions items.
        retry = {**changed, "lastUpdate": current.get("lastUpdate")}
        await self._change(
            shopping_list,
            [changed],
            item_ids=shopping_list.item_ids,
            saved=saved,
            retry_items=[retry] if current.get("lastUpdate") else None,
        )

    async def async_delete_todo_items(self, uids: list[str]) -> None:
        shopping_list = self._writable()
        remove = set(uids)
        await self._change(
            shopping_list,
            [],
            item_ids=[item_id for item_id in shopping_list.item_ids if item_id not in remove],
            # The app sends removed items as UpdatableItem: id plus removal time.
            deleted=[{"itemId": uid, "lastUpdate": _now()} for uid in uids],
            saved=lambda lst: not remove & set(lst.item_ids),
        )

    async def _change(self, shopping_list: ShoppingList, items, **kwargs) -> None:
        try:
            await self.coordinator.async_shopping_list_change(shopping_list, items, **kwargs)
        except LidlPlusError as err:
            raise HomeAssistantError(f"Lidl Plus did not save the change ({err})") from err
