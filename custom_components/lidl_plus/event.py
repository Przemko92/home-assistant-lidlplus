"""Event entities announcing new coupons, scratch cards, gifts, Kupon Plus and stamp cards."""

from __future__ import annotations

from homeassistant.components.event import EventEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import LidlPlusConfigEntry
from .coordinator import LidlPlusCoordinator
from .entity import LidlPlusEntity
from .extras import EVENT_KINDS

PARALLEL_UPDATES = 0

EVENT_NEW = "new"


async def async_setup_entry(
    hass: HomeAssistant,
    entry: LidlPlusConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator = entry.runtime_data
    async_add_entities(LidlPlusNewEvent(coordinator, kind) for kind in EVENT_KINDS)


class LidlPlusNewEvent(LidlPlusEntity, EventEntity):
    """Fires once per new item (new coupons come together as one event).

    The event data is the item with a ready-made Polish `summary`.
    """

    _attr_event_types = [EVENT_NEW]

    def __init__(self, coordinator: LidlPlusCoordinator, kind: str) -> None:
        super().__init__(coordinator)
        self._kind = kind
        self._attr_translation_key = f"new_{kind}"
        self._attr_unique_id = f"{self._device_id}_new_{kind}"
        # The snapshot already announced; listeners also fire without a refresh
        # (e.g. after a shopping-list change) and must not repeat it.
        self._announced_data = None

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        # The first refresh runs before entities exist; items that appeared
        # while Home Assistant was down would otherwise never be announced.
        self._announce()

    @callback
    def _handle_coordinator_update(self) -> None:
        self._announce()
        super()._handle_coordinator_update()

    @callback
    def _announce(self) -> None:
        data = self.coordinator.data
        if data is None or data is self._announced_data:
            return
        self._announced_data = data
        for item in data.new_offers:
            if item.category == self._kind:
                self._trigger_event(EVENT_NEW, item.as_dict())
                self.async_write_ha_state()
