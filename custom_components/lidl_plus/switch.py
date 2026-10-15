"""Switches for automatic coupon activation and scratch cards."""

from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import LidlPlusConfigEntry
from .const import (
    CONF_AUTO_COUPONS,
    CONF_AUTO_SCRATCH,
    DEFAULT_AUTO_COUPONS,
    DEFAULT_AUTO_SCRATCH,
)
from .coordinator import LidlPlusCoordinator
from .entity import LidlPlusEntity

PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: LidlPlusConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator = entry.runtime_data
    async_add_entities(
        [
            LidlPlusOptionSwitch(coordinator, CONF_AUTO_COUPONS, DEFAULT_AUTO_COUPONS),
            LidlPlusOptionSwitch(coordinator, CONF_AUTO_SCRATCH, DEFAULT_AUTO_SCRATCH),
        ]
    )


class LidlPlusOptionSwitch(LidlPlusEntity, SwitchEntity):
    """Auto coupons / auto scratch cards, stored in the entry options."""

    _attr_entity_category = EntityCategory.CONFIG

    def __init__(
        self, coordinator: LidlPlusCoordinator, option: str, default: bool
    ) -> None:
        super().__init__(coordinator)
        self._option = option
        self._default = default
        self._attr_translation_key = option
        self._attr_unique_id = f"{self._device_id}_{option}"

    @property
    def is_on(self) -> bool:
        return bool(self.coordinator.entry.options.get(self._option, self._default))

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self._async_set(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._async_set(False)

    async def _async_set(self, enabled: bool) -> None:
        options = {**self.coordinator.entry.options, self._option: enabled}
        self.hass.config_entries.async_update_entry(
            self.coordinator.entry, options=options
        )
        if enabled:
            await self.coordinator.async_request_refresh()
        self.async_write_ha_state()
