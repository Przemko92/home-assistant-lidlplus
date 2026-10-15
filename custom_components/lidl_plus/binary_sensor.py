"""Whether the user's Lidl store is open right now."""

from __future__ import annotations

from typing import Any

from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.util import dt as dt_util

from . import LidlPlusConfigEntry
from .coordinator import LidlPlusCoordinator
from .entity import LidlPlusEntity

PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: LidlPlusConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    async_add_entities([LidlPlusStoreOpenSensor(entry.runtime_data)])


class LidlPlusStoreOpenSensor(LidlPlusEntity, BinarySensorEntity):
    """On while the store chosen in the app (else the latest receipt's) is open."""

    _attr_translation_key = "store_open"

    def __init__(self, coordinator: LidlPlusCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{self._device_id}_store_open"

    @property
    def is_on(self) -> bool | None:
        data = self.coordinator.data
        schedule = data.store_schedule if data else None
        return schedule.open_at(dt_util.now()) if schedule else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        data = self.coordinator.data
        schedule = data.store_schedule if data else None
        if not schedule:
            return {}
        return {
            "store_id": schedule.store_id,
            "address": schedule.full_address,
            "today": schedule.today,
            "temporarily_closed": schedule.temporarily_closed,
        }
