"""Button to activate Lidl Plus coupons."""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import LidlPlusConfigEntry
from .coordinator import LidlPlusCoordinator
from .entity import LidlPlusEntity

PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: LidlPlusConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    async_add_entities([LidlPlusActivateCouponsButton(entry.runtime_data)])


class LidlPlusActivateCouponsButton(LidlPlusEntity, ButtonEntity):
    """Activates every coupon that is ready."""

    _attr_translation_key = "coupons_activate"

    def __init__(self, coordinator: LidlPlusCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{self._device_id}_coupons_activate"

    @property
    def available(self) -> bool:
        if not super().available or not self.coordinator.data:
            return False
        return any(
            coupon.coupon_id for coupon in self.coordinator.data.available_coupons
        )

    async def async_press(self) -> None:
        await self.coordinator.async_activate_all()
