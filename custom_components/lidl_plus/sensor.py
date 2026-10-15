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
            LidlPlusScratchCardsSensor(coordinator),
            LidlPlusGiftBoxesSensor(coordinator),
            LidlPlusCouponPlusSensor(coordinator),
            LidlPlusStampCardSensor(coordinator),
            LidlPlusDepositsSensor(coordinator),
            LidlPlusAlertsSensor(coordinator),
            LidlPlusStoreHoursSensor(coordinator),
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


class LidlPlusScratchCardsSensor(LidlPlusEntity, SensorEntity):
    """Scratch cards (zdrapki) waiting to be scratched in the app."""

    _attr_translation_key = "scratch_cards"
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, coordinator: LidlPlusCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{self._device_id}_scratch_cards"

    @property
    def native_value(self) -> int:
        data = self.coordinator.data
        return len(data.scratch_cards) if data else 0

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        data = self.coordinator.data
        if not data:
            return {}
        cards = sorted(data.scratch_cards, key=lambda card: card.expires or "")
        return {
            "next_expiry": cards[0].expires if cards else None,
            "cards": [card.as_dict() for card in cards],
        }


class LidlPlusGiftBoxesSensor(LidlPlusEntity, SensorEntity):
    """Gift boxes (secret boxes / daily deals) to open in the app."""

    _attr_translation_key = "gift_boxes"
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, coordinator: LidlPlusCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{self._device_id}_gift_boxes"

    @property
    def native_value(self) -> int:
        data = self.coordinator.data
        return len(data.gift_boxes) if data else 0

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        data = self.coordinator.data
        return {"boxes": [box.as_dict() for box in data.gift_boxes]} if data else {}


class LidlPlusCouponPlusSensor(LidlPlusEntity, SensorEntity):
    """Kupon Plus: how much has been spent towards the target."""

    _attr_translation_key = "coupon_plus"
    _attr_device_class = SensorDeviceClass.MONETARY

    def __init__(self, coordinator: LidlPlusCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{self._device_id}_coupon_plus"

    @property
    def native_unit_of_measurement(self) -> str:
        return self.coordinator.currency

    @property
    def native_value(self) -> float | None:
        data = self.coordinator.data
        plus = data.coupon_plus if data else None
        return plus.reached if plus else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        data = self.coordinator.data
        plus = data.coupon_plus if data else None
        return plus.as_dict() if plus else {}


class LidlPlusStampCardSensor(LidlPlusEntity, SensorEntity):
    """Stamps collected on the current stamp card."""

    _attr_translation_key = "stamp_card"
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, coordinator: LidlPlusCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{self._device_id}_stamp_card"

    @property
    def native_value(self) -> float | None:
        data = self.coordinator.data
        card = data.stamp_cards[0] if data and data.stamp_cards else None
        return card.units if card else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        data = self.coordinator.data
        if not data or not data.stamp_cards:
            return {}
        return {
            **data.stamp_cards[0].as_dict(),
            "cards": [card.as_dict() for card in data.stamp_cards],
        }


class LidlPlusDepositsSensor(LidlPlusEntity, SensorEntity):
    """Bottle-deposit money ready to use (system kaucyjny)."""

    _attr_translation_key = "deposits"
    _attr_device_class = SensorDeviceClass.MONETARY

    def __init__(self, coordinator: LidlPlusCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{self._device_id}_deposits"

    @property
    def native_unit_of_measurement(self) -> str:
        return self.coordinator.currency

    @property
    def native_value(self) -> float | None:
        data = self.coordinator.data
        return data.deposits.available if data and data.deposits else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        data = self.coordinator.data
        deposits = data.deposits if data else None
        if not deposits:
            return {}
        return {
            "pending": deposits.pending,
            "automatic_redemption": deposits.automatic,
            "deposits": deposits.items,
        }


class LidlPlusAlertsSensor(LidlPlusEntity, SensorEntity):
    """Unread messages in the app's inbox."""

    _attr_translation_key = "alerts"
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, coordinator: LidlPlusCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{self._device_id}_alerts"

    @property
    def native_value(self) -> int | None:
        data = self.coordinator.data
        return data.alerts_unread if data else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        data = self.coordinator.data
        return {"home_message": data.home_message} if data else {}


class LidlPlusStoreHoursSensor(LidlPlusEntity, SensorEntity):
    """Today's opening hours of the store chosen in the app (else the latest receipt's)."""

    _attr_translation_key = "store_hours"

    def __init__(self, coordinator: LidlPlusCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{self._device_id}_store_hours"

    @property
    def native_value(self) -> str | None:
        data = self.coordinator.data
        schedule = data.store_schedule if data else None
        if not schedule or self._closed(schedule):
            return None
        return schedule.today

    @staticmethod
    def _closed(schedule: Any) -> bool:
        return bool(
            schedule.permanently_closed or schedule.temporarily_closed or not schedule.hours
        )

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        data = self.coordinator.data
        schedule = data.store_schedule if data else None
        if not schedule:
            return {}
        return {
            "store_id": schedule.store_id,
            "favorite_store": schedule.favorite,
            "name": schedule.name,
            "address": schedule.full_address,
            "postal_code": schedule.postal_code,
            "closed_today": self._closed(schedule),
            "week": schedule.week,
        }
