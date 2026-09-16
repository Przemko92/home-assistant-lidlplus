"""Switch for automatic coupon activation."""

from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import LidlPlusConfigEntry
from .const import CONF_AUTO_COUPONS, DEFAULT_AUTO_COUPONS
from .coordinator import LidlPlusCoordinator
from .entity import LidlPlusEntity

PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: LidlPlusConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    async_add_entities([LidlPlusAutoCouponsSwitch(entry.runtime_data)])


class LidlPlusAutoCouponsSwitch(LidlPlusEntity, SwitchEntity):
    _attr_translation_key = "auto_coupons"
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator: LidlPlusCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{self._device_id}_auto_coupons"

    @property
    def is_on(self) -> bool:
        return self.coordinator.auto_coupons

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self._async_set(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._async_set(False)

    async def _async_set(self, enabled: bool) -> None:
        options = {**self.coordinator.entry.options, CONF_AUTO_COUPONS: enabled}
        self.hass.config_entries.async_update_entry(
            self.coordinator.entry, options=options
        )
        if not options.get(CONF_AUTO_COUPONS, DEFAULT_AUTO_COUPONS):
            self.async_write_ha_state()
            return
        await self.coordinator.async_request_refresh()
        self.async_write_ha_state()
