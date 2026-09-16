"""Sensors: card, last ticket, today spend, coupons."""

from __future__ import annotations

from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import LidlPlusConfigEntry
from .const import MAX_RECEIPTS
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
            LidlPlusCardSensor(coordinator),
            LidlPlusLastTransactionSensor(coordinator),
            LidlPlusTodaySpendSensor(coordinator),
            LidlPlusCouponsSensor(coordinator),
            LidlPlusCouponsAvailableSensor(coordinator),
            LidlPlusCouponsUpcomingSensor(coordinator),
        ]
    )


class LidlPlusCardSensor(LidlPlusEntity, SensorEntity):
    _attr_translation_key = "card"

    def __init__(self, coordinator: LidlPlusCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{self._device_id}_card"

    @property
    def native_value(self) -> str | None:
        return self.coordinator.data.loyalty_id if self.coordinator.data else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {
            "country": self.coordinator.country.code,
            "language": self.coordinator.language,
        }


class LidlPlusLastTransactionSensor(LidlPlusEntity, SensorEntity):
    _attr_translation_key = "last_transaction"
    _attr_device_class = SensorDeviceClass.MONETARY
    _attr_suggested_display_precision = 2
    _unrecorded_attributes = frozenset({"transactions", "items"})

    def __init__(self, coordinator: LidlPlusCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{self._device_id}_last_transaction"

    @property
    def native_unit_of_measurement(self) -> str:
        return self.coordinator.currency

    @property
    def native_value(self) -> float | None:
        data = self.coordinator.data
        if not data:
            return None
        if data.receipts and data.receipts[0].total_price is not None:
            return data.receipts[0].total_price
        if not data.last_ticket:
            return None
        amount = data.last_ticket.get("totalAmount")
        try:
            return float(amount or 0)
        except (TypeError, ValueError):
            return None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        data = self.coordinator.data
        if not data:
            return {}
        receipts = data.receipts[:MAX_RECEIPTS]
        latest = receipts[0] if receipts else None
        attributes: dict[str, Any] = {
            "transactions": [receipt.as_dict() for receipt in receipts]
        }
        if latest:
            attributes.update(
                {
                    "id": latest.id,
                    "date": latest.date,
                    "store_name": latest.store_name,
                    "receipt_num": latest.receipt_num,
                    "items": latest.lines,
                }
            )
            return attributes
        tx = data.last_ticket
        if not tx:
            return attributes
        attributes.update(
            {
                "id": tx.get("id"),
                "date": tx.get("date"),
                "store_name": None,
                "receipt_num": None,
                "items": [],
            }
        )
        return attributes


class LidlPlusTodaySpendSensor(LidlPlusEntity, SensorEntity):
    _attr_translation_key = "today_spend"
    _attr_device_class = SensorDeviceClass.MONETARY
    _attr_state_class = SensorStateClass.TOTAL
    _attr_suggested_display_precision = 2

    def __init__(self, coordinator: LidlPlusCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{self._device_id}_today_spend"

    @property
    def native_unit_of_measurement(self) -> str:
        return self.coordinator.currency

    @property
    def native_value(self) -> float:
        return self.coordinator.data.today_total if self.coordinator.data else 0.0


class LidlPlusCouponsSensor(LidlPlusEntity, SensorEntity):
    """How many coupons are currently activated."""

    _attr_translation_key = "coupons"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _unrecorded_attributes = frozenset({"coupons"})

    def __init__(self, coordinator: LidlPlusCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{self._device_id}_coupons"

    @property
    def native_value(self) -> int:
        data = self.coordinator.data
        if not data:
            return 0
        return len(data.coupons)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        data = self.coordinator.data
        if not data:
            return {}
        return {
            "activated": sum(1 for coupon in data.coupons if coupon.is_activated),
            "coupons": [coupon.as_dict() for coupon in data.coupons],
        }


class LidlPlusCouponsAvailableSensor(LidlPlusEntity, SensorEntity):
    """How many coupons can be activated right now."""

    _attr_translation_key = "coupons_available"
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, coordinator: LidlPlusCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{self._device_id}_coupons_available"

    @property
    def native_value(self) -> int:
        return (
            len(self.coordinator.data.available_coupons) if self.coordinator.data else 0
        )

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        data = self.coordinator.data
        if not data:
            return {}
        return {
            "offer_ids": [
                coupon.coupon_id
                for coupon in data.available_coupons
                if coupon.coupon_id
            ]
        }


class LidlPlusCouponsUpcomingSensor(LidlPlusEntity, SensorEntity):
    """Coupons that can be activated once their validity window starts."""

    _attr_translation_key = "coupons_upcoming"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _unrecorded_attributes = frozenset({"coupons"})

    def __init__(self, coordinator: LidlPlusCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{self._device_id}_coupons_upcoming"

    @property
    def native_value(self) -> int:
        return (
            len(self.coordinator.data.upcoming_coupons) if self.coordinator.data else 0
        )

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        data = self.coordinator.data
        if not data:
            return {}
        upcoming = data.upcoming_coupons
        return {
            "offer_ids": [coupon.coupon_id for coupon in upcoming if coupon.coupon_id],
            "next_valid_from": upcoming[0].valid_from if upcoming else None,
            "coupons": [coupon.as_dict() for coupon in upcoming],
        }
