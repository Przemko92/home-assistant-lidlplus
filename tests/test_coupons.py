"""Coupon discovery, activation and reward history."""

from datetime import datetime, timezone

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.lidl_plus.const import DOMAIN
from custom_components.lidl_plus.coordinator import LidlPlusCoordinator
from custom_components.lidl_plus.exceptions import LidlPlusError
from custom_components.lidl_plus.models import Coupon, LidlPlusData, flatten_promotions


def _promo(promo_id: str, *, activated: bool = False, **kwargs) -> dict:
    return {
        "id": promo_id,
        "promotionId": kwargs.get("promotion_id", f"p-{promo_id}"),
        "title": kwargs.get("title", f"Coupon {promo_id}"),
        "isActivated": activated,
        "isProcessing": False,
        "availability": {"apologizeStatus": kwargs.get("apologize", False)},
        "discount": {"title": kwargs.get("discount", "-20%")},
        "validity": {
            "start": kwargs.get("start", "2026-01-01T00:00:00Z"),
            "end": kwargs.get("end", "2026-12-31T00:00:00Z"),
        },
    }


class FakeApi:
    def __init__(self, promotions: list[dict]):
        self.promotions = promotions
        self.activated: list[str] = []
        self.fail_on: set[str] = set()
        self.country = "PL"
        self.language = "pl-PL"

    async def loyalty_id(self) -> str:
        return "1234"

    async def tickets(self, year_offset: int = 0):
        return []

    async def ticket(self, ticket_id: str):
        return {}

    async def promotions_list(self, store_id=None):
        return {"sections": [{"name": "Weekly", "promotions": self.promotions}]}

    async def activate_promotion(self, promotion_id: str):
        self.activated.append(promotion_id)
        if promotion_id in self.fail_on:
            raise LidlPlusError("http_409")
        return None


@pytest.fixture
def coordinator_factory(hass):
    def _make(api: FakeApi) -> LidlPlusCoordinator:
        entry = MockConfigEntry(
            domain=DOMAIN,
            data={"card_number": "1234", "country": "PL", "language": "pl-PL"},
            options={},
        )
        entry.add_to_hass(hass)
        return LidlPlusCoordinator(hass, entry, api)

    return _make


def test_flatten_promotions_keeps_section_name():
    payload = {
        "sections": [
            {"name": "A", "promotions": [_promo("1")]},
            {"name": "B", "promotions": [_promo("2")]},
        ]
    }
    items = flatten_promotions(payload)
    assert [item["id"] for item in items] == ["1", "2"]
    assert items[0]["section"] == "A"


def test_ssc_section_from_list_is_not_activatable():
    payload = {"sections": [{"name": "SSC", "promotions": [_promo("ssc")]}]}
    items = flatten_promotions(payload)
    coupon = Coupon.from_payload(items[0])
    assert coupon is not None
    assert coupon.section == "SSC"
    assert coupon.can_activate() is False


def test_flatten_promotions_unwraps_data_and_nested_promotion():
    payload = {
        "data": {
            "sections": [
                {
                    "name": "Weekly",
                    "items": [{"promotion": _promo("nested")}],
                }
            ]
        }
    }
    items = flatten_promotions(payload)
    assert [item["id"] for item in items] == ["nested"]
    assert items[0]["section"] == "Weekly"


def test_string_false_is_not_activated():
    coupon = Coupon.from_payload(
        _promo("a") | {"isActivated": "false", "isProcessing": "false"}
    )
    assert coupon is not None
    assert coupon.is_activated is False
    assert coupon.can_activate() is True


async def test_inactive_coupons_are_available(coordinator_factory):
    api = FakeApi([_promo("a"), _promo("b", activated=True), _promo("c")])
    coordinator = coordinator_factory(api)
    await coordinator.async_load_rewards()

    coupons = await coordinator._load_coupons()
    data = LidlPlusData()
    coordinator._apply_coupons(data, coupons)

    assert [coupon.coupon_id for coupon in data.available_coupons] == ["a", "c"]
    assert sum(1 for coupon in data.coupons if coupon.is_activated) == 1


async def test_assignable_coupons_are_not_offered(coordinator_factory):
    api = FakeApi(
        [
            _promo("a"),
            _promo("pick") | {"type": "Assignable", "groups": [{"id": "g"}]},
        ]
    )
    coordinator = coordinator_factory(api)
    await coordinator.async_load_rewards()
    coupons = await coordinator._load_coupons()
    data = LidlPlusData()
    coordinator._apply_coupons(data, coupons)

    assert [coupon.coupon_id for coupon in data.available_coupons] == ["a"]
    assert [coupon.coupon_id for coupon in data.coupons] == ["a", "pick"]


async def test_other_store_coupons_are_not_offered(coordinator_factory):
    api = FakeApi(
        [
            _promo("a") | {"section": "AllStores", "channel": "Store"},
            _promo("foreign")
            | {
                "section": "OtherStores",
                "type": "Standard",
                "channel": "Store",
                "title": "Wybrane owoce i warzywa",
            },
        ]
    )
    coordinator = coordinator_factory(api)
    await coordinator.async_load_rewards()
    coupons = await coordinator._load_coupons()
    data = LidlPlusData()
    coordinator._apply_coupons(data, coupons)

    assert [coupon.coupon_id for coupon in data.available_coupons] == ["a"]
    assert [coupon.coupon_id for coupon in data.coupons] == ["a", "foreign"]


async def test_future_coupons_go_to_upcoming(coordinator_factory):
    api = FakeApi(
        [
            _promo("now"),
            _promo("later", start="2099-01-01T00:00:00Z", end="2099-12-31T00:00:00Z"),
            _promo("old", start="2020-01-01T00:00:00Z", end="2020-01-02T00:00:00Z"),
        ]
    )
    coordinator = coordinator_factory(api)
    await coordinator.async_load_rewards()
    coupons = await coordinator._load_coupons()
    data = LidlPlusData()
    coordinator._apply_coupons(data, coupons)

    assert [coupon.coupon_id for coupon in data.available_coupons] == ["now"]
    assert [coupon.coupon_id for coupon in data.upcoming_coupons] == ["later"]


async def test_apologize_coupons_are_not_activatable(coordinator_factory):
    api = FakeApi([_promo("sorry", apologize=True)])
    coordinator = coordinator_factory(api)
    coupons = await coordinator._load_coupons()
    assert coupons[0].can_activate() is False


def test_assignable_coupons_are_not_activatable():
    coupon = Coupon.from_payload(_promo("pick") | {"type": "Assignable"})
    assert coupon is not None
    assert coupon.can_activate() is False


def test_group_coupons_are_not_activatable():
    coupon = Coupon.from_payload(
        _promo("pick")
        | {
            "type": "Standard",
            "groups": [{"id": "g1", "title": "Wybierz produkt", "articles": [{"id": "1"}]}],
        }
    )
    assert coupon is not None
    assert coupon.can_activate() is False


def test_locked_level_coupons_are_not_activatable():
    coupon = Coupon.from_payload(
        _promo("lvl") | {"type": "Levels", "levels": {"level": 2, "isLocked": True}}
    )
    assert coupon is not None
    assert coupon.can_activate() is False


def test_game_coupons_are_not_activatable():
    coupon = Coupon.from_payload(
        _promo("game") | {"navigationURL": "https://lidlplus.com/game"}
    )
    assert coupon is not None
    assert coupon.can_activate() is False


def test_standard_coupons_stay_activatable():
    coupon = Coupon.from_payload(_promo("a") | {"type": "Standard"})
    assert coupon is not None
    assert coupon.can_activate() is True


def test_ssc_section_coupons_are_not_activatable():
    coupon = Coupon.from_payload(
        _promo("ssc")
        | {
            "section": "SSC",
            "channel": "SSC",
            "title": "*na zakupy za min. 30 zł i maks. 300 zł",
        }
    )
    assert coupon is not None
    assert coupon.can_activate() is False


def test_ssc_channel_coupons_are_not_activatable():
    coupon = Coupon.from_payload(_promo("ssc") | {"channel": "SSC"})
    assert coupon is not None
    assert coupon.can_activate() is False


def test_store_channel_stays_activatable():
    coupon = Coupon.from_payload(
        _promo("a") | {"section": "Weekly", "channel": "AllStores"}
    )
    assert coupon is not None
    assert coupon.can_activate() is True


def test_other_stores_coupons_are_not_activatable():
    coupon = Coupon.from_payload(
        _promo("fruit")
        | {
            "section": "OtherStores",
            "type": "Standard",
            "channel": "Store",
            "title": "Wybrane owoce i warzywa",
        }
    )
    assert coupon is not None
    assert coupon.can_activate() is False
    assert coupon.other_store is True


def test_future_validity_is_upcoming_not_live():
    coupon = Coupon.from_payload(
        _promo(
            "radish",
            start="2026-09-15T22:00:00Z",
            end="2026-09-16T21:59:59Z",
            title="Rzodkiewki | pęczek",
        )
        | {"section": "AllStores"}
    )
    assert coupon is not None
    before = datetime(2026, 9, 15, 12, 0, tzinfo=timezone.utc)
    start = datetime(2026, 9, 15, 22, 0, tzinfo=timezone.utc)
    assert coupon.can_activate() is True
    assert coupon.is_upcoming(before) is True
    assert coupon.is_live(before) is False
    assert coupon.is_upcoming(start) is False
    assert coupon.is_live(start) is True


async def test_activate_all_skips_failures(coordinator_factory):
    api = FakeApi([_promo("ok"), _promo("bad"), _promo("also")])
    api.fail_on = {"bad"}
    coordinator = coordinator_factory(api)
    coordinator.data = LidlPlusData()
    await coordinator.async_load_rewards()
    coupons = await coordinator._load_coupons()
    coordinator._apply_coupons(coordinator.data, coupons)

    claimed = await coordinator.async_activate_all(refresh=False)

    assert api.activated == ["ok", "bad", "also"]
    assert [item["coupon_id"] for item in claimed] == ["ok", "also"]
    assert "ok" in coordinator._rewards
    assert "also" in coordinator._rewards
    assert "bad" not in coordinator._rewards
    assert "bad" in coordinator._skipped

    leftover = LidlPlusData()
    coordinator._apply_coupons(leftover, coupons)
    assert leftover.available_coupons == []


async def test_claimed_coupon_survives_the_next_one(coordinator_factory):
    api = FakeApi([_promo("first")])
    coordinator = coordinator_factory(api)
    await coordinator.async_load_rewards()
    coupon = Coupon.from_payload(_promo("first"))
    assert coupon is not None
    await coordinator.async_activate_coupon(coupon, refresh=False)

    api.promotions = [_promo("second")]
    coupons = await coordinator._load_coupons()
    data = LidlPlusData()
    coordinator._apply_coupons(data, coupons)

    assert [item.coupon_id for item in coupons] == ["second"]
    assert data.last_reward is not None
    assert data.last_reward.coupon_id == "first"
