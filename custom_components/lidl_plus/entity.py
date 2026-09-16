"""Shared entity base."""

from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import ATTRIBUTION, CONF_CARD_NUMBER, CONF_COUNTRY, DOMAIN
from .coordinator import LidlPlusCoordinator


class LidlPlusEntity(CoordinatorEntity[LidlPlusCoordinator]):
    """Device-bound entity."""

    _attr_has_entity_name = True
    _attr_attribution = ATTRIBUTION

    def __init__(self, coordinator: LidlPlusCoordinator) -> None:
        super().__init__(coordinator)
        country = coordinator.entry.data.get(CONF_COUNTRY) or coordinator.country.code
        card = (
            coordinator.entry.data.get(CONF_CARD_NUMBER)
            or coordinator.entry.unique_id
            or coordinator.entry.entry_id
        )
        self._device_id = f"{country}_{card}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, self._device_id)},
            manufacturer="Lidl",
            name="Lidl Plus",
            model=f"Loyalty card ({country})",
        )
