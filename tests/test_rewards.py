"""Home-screen rewards, deposits, unread messages and new-item events."""

from datetime import datetime, timedelta
from unittest.mock import patch
from zoneinfo import ZoneInfo

import pytest
from homeassistant.const import STATE_UNKNOWN
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.lidl_plus.const import DOMAIN
from custom_components.lidl_plus.extras import (
    coupon_plus,
    coupon_summary,
    gift_boxes,
    scratch_cards,
    stamp_cards,
)

from .test_setup_entities import FullFakeApi

WARSAW = ZoneInfo("Europe/Warsaw")


def _iso(hours: float) -> str:
    return (datetime.now(WARSAW) + timedelta(hours=hours)).isoformat()


def _scratch(card_id: str, expires: str) -> dict:
    """Shaped like the live purchaseLottery module."""
    return {
        "background": "https://example.invalid/bg.png",
        "creationDate": _iso(-48),
        "expirationDate": expires,
        "id": card_id,
        "logo": "https://www.lidl.com/",
        "promotionCode": "RP0000017177",
        "promotionId": "2a299a9c",
        "type": "Scratch",
    }


# Modules with nothing to show come back like this (live answer).
EMPTY_MODULES = {
    "error_openGift-1": {"http_status_code": 204},
    "error_stampCardRewards-1": {"http_status_code": 204},
    "homeMessage": {"id": "HM1", "text": "Odkryj Max & Mila's World! 🐾"},
}


@pytest.fixture
def entry(hass):
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            "card_number": "1234",
            "country": "PL",
            "language": "pl-PL",
            "access_token": "access",
            "refresh_token": "refresh",
        },
        options={"auto_coupons": False},
        unique_id="PL_1234",
    )
    entry.add_to_hass(hass)
    return entry


async def _setup(hass, entry, api):
    with (
        patch("custom_components.lidl_plus.LidlPlusApi", return_value=api),
        patch("custom_components.lidl_plus.async_get_clientsession", return_value=None),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()


async def _refresh(hass, entry):
    await entry.runtime_data.async_refresh()
    await hass.async_block_till_done()


def test_parsers_on_the_live_home_shape():
    home = {"purchaseLottery": [_scratch("s1", "2026-10-13T23:59:59.999+02:00")], **EMPTY_MODULES}
    [card] = scratch_cards(home)
    assert card.card_type == "Scratch"
    assert card.summary == "Nowa zdrapka Lidl Plus — odkryj do 13.10 23:59."
    assert gift_boxes(home) == []
    assert stamp_cards(home) == []
    assert coupon_plus(home) is None


def test_gift_coupon_plus_and_stamp_card_parsers():
    home = {
        "openGift": {
            "type": "SecretBoxes",
            "detailInformation": {"id": "camp1", "backgroundImage": "https://example.invalid/g.png"},
            "activeBoxes": [{"id": "b1", "availableDate": _iso(-1), "lastAvailableDate": "2026-10-20T21:59:59Z"}],
            "translations": {"title": "Sekretne pudełka"},
        },
        "couponPlus": {
            "couponPlus": {
                "id": "cp1",
                "status": "Active",
                "amount": 200,
                "reachedAmount": 142.5,
                "endDate": "2026-10-31T22:59:59Z",
                "goals": [{"amount": 200, "title": "Kupon -20 zł"}],
            }
        },
        "stampCardRewards": {
            "promotionId": "st1",
            "title": "Naklejki na noże",
            "status": "Started",
            "endDate": "2026-11-30T22:59:59Z",
            "configuration": {"unitsPerPrize": 10},
            "userPromotion": {"unitsAchieved": 4, "completedCards": 1},
        },
    }
    [box] = gift_boxes(home)
    assert box.key == "gift_camp1_b1"
    assert box.summary == "Sekretne pudełka do otwarcia w Lidl Plus — do 20.10 23:59."
    plus = coupon_plus(home)
    assert plus.missing == 57.5
    assert plus.summary == (
        "Kupon Plus: 142,50 zł z 200,00 zł (brakuje 57,50 zł). "
        "Nagroda: Kupon -20 zł. Do 31.10."
    )
    [card] = stamp_cards(home)
    assert (card.units, card.units_per_prize, card.completed) == (4, 10, 1)
    assert card.summary == "Karta z naklejkami: Naklejki na noże — 4/10, do 30.11."


def test_coupon_summary():
    assert coupon_summary(
        "Donut Pinky LUB Serek - różne rodzaje", "-50%", "2026-10-17T21:59:59.999Z"
    ) == "Donut Pinky LUB Serek - różne rodzaje: -50%. Ważny do 17.10."


async def test_sensors_for_scratch_cards_deposits_and_messages(
    hass, entry, enable_custom_integrations
):
    api = FullFakeApi()
    api.home_payload = {
        "purchaseLottery": [_scratch("s1", _iso(10)), _scratch("old", _iso(-1))],
        **EMPTY_MODULES,
    }
    api.deposits_payload = {
        "availableAmount": 3.5,
        "pendingAmount": 0.5,
        "isAutomaticRedemption": True,
        "deposits": [{"id": "d1"}],
    }
    api.unread = 11
    await _setup(hass, entry, api)

    cards = hass.states.get("sensor.lidl_plus_scratch_cards")
    assert cards.state == "1"
    assert cards.attributes["cards"][0]["id"] == "s1"
    deposits = hass.states.get("sensor.lidl_plus_deposit_refunds")
    assert deposits.state == "3.5"
    assert deposits.attributes["pending"] == 0.5
    alerts = hass.states.get("sensor.lidl_plus_unread_messages")
    assert alerts.state == "11"
    assert alerts.attributes["home_message"] == "Odkryj Max & Mila's World! 🐾"
    assert hass.states.get("sensor.lidl_plus_gift_boxes").state == "0"


async def test_events_are_silent_first_then_once_per_item(
    hass, entry, enable_custom_integrations
):
    api = FullFakeApi()
    api.home_payload = {"purchaseLottery": [_scratch("s1", _iso(10))]}
    await _setup(hass, entry, api)

    scratch_event = "event.lidl_plus_new_scratch_card"
    coupon_event = "event.lidl_plus_new_coupons"
    assert hass.states.get(scratch_event).state == STATE_UNKNOWN
    assert hass.states.get(coupon_event).state == STATE_UNKNOWN

    api.home_payload = {
        "purchaseLottery": [_scratch("s1", _iso(10)), _scratch("s2", _iso(20))]
    }
    original = api.promotions_list

    async def more_coupons(store_id=None):
        payload = await original(store_id)
        payload["sections"][0]["promotions"].append(
            {
                "id": "c",
                "promotionId": "p-c",
                "title": "Donut Pinky LUB Serek",
                "type": "AssignablePromotion",
                "isActivated": False,
                "availability": {"apologizeStatus": False},
                "discount": {"title": "-50%"},
                "validity": {"start": "2026-01-01T00:00:00Z", "end": "2026-12-31T00:00:00Z"},
            }
        )
        return payload

    api.promotions_list = more_coupons
    await _refresh(hass, entry)

    scratch = hass.states.get(scratch_event)
    assert scratch.attributes["id"] == "s2"
    assert scratch.attributes["summary"].startswith("Nowa zdrapka Lidl Plus — odkryj do ")
    coupons = hass.states.get(coupon_event)
    assert coupons.attributes["count"] == 1
    assert coupons.attributes["summary"] == "Donut Pinky LUB Serek: -50%. Ważny do 31.12."

    fired = scratch.state
    await _refresh(hass, entry)
    assert hass.states.get(scratch_event).state == fired


class StoreApi(FullFakeApi):
    """One receipt from store PL2338, so the store schedule is fetched."""

    async def tickets(self, year_offset: int = 0):
        return [{"id": "t1", "storeId": "PL2338", "date": _iso(-30), "totalAmount": 10}]


async def test_store_hours_and_open_sensor(hass, entry, enable_custom_integrations):
    api = StoreApi()
    api.schedule_payload = {
        "openingHours": [{"from": "00:00", "to": "23:59"}],
        "isTemporarilyClosed": False,
        "isPermanentlyClosed": False,
        "isOpen": True,
    }
    await _setup(hass, entry, api)

    hours = hass.states.get("sensor.lidl_plus_store_opening_hours")
    assert hours.state == "00:00–23:59"
    assert hours.attributes["store_id"] == "PL2338"
    assert hass.states.get("binary_sensor.lidl_plus_store_open").state == "on"


def test_store_schedule_closed_and_hours():
    from custom_components.lidl_plus.extras import StoreSchedule

    closed = StoreSchedule.from_payload("PL1", {"openingHours": [], "isOpen": False})
    assert closed.today is None
    assert closed.open_at(datetime.now(WARSAW)) is False
    day = StoreSchedule.from_payload("PL1", {"openingHours": [{"from": "06:00", "to": "22:00"}]})
    noon = datetime.now(WARSAW).replace(hour=12, minute=0)
    night = noon.replace(hour=23)
    assert day.open_at(noon) is True
    assert day.open_at(night) is False


async def test_favorite_store_wins_over_the_receipt(
    hass, entry, enable_custom_integrations
):
    api = StoreApi()
    api.favorite = "PL9999"
    api.details_payload = {
        "storeKey": "PL9999",
        "name": "Lidl Szczecinek",
        "address": "ul. Koszalińska 87",
        "locality": "Szczecinek",
        "postalCode": "78-400",
        "weekDays": [
            {"day": "Monday", "openingHours": [{"from": "06:00", "to": "22:00"}]},
            {"day": "Sunday", "openingHours": []},
        ],
    }
    api.schedule_payload = {"openingHours": [{"from": "06:00", "to": "22:00"}]}
    seen: list = []
    original = api.promotions_list

    async def remember(store_id=None):
        seen.append(store_id)
        return await original(store_id)

    api.promotions_list = remember
    await _setup(hass, entry, api)

    hours = hass.states.get("sensor.lidl_plus_store_opening_hours")
    assert hours.state == "06:00–22:00"
    assert hours.attributes["store_id"] == "PL9999"
    assert hours.attributes["favorite_store"] is True
    assert hours.attributes["address"] == "Szczecinek, ul. Koszalińska 87"
    assert hours.attributes["week"] == [
        {"day": "Monday", "hours": "06:00–22:00"},
        {"day": "Sunday", "hours": None},
    ]
    assert seen[0] == "PL9999"


async def test_scratch_button_reveals_prize_and_announces_it(
    hass, entry, enable_custom_integrations
):
    api = FullFakeApi()
    api.home_payload = {"purchaseLottery": [_scratch("win", _iso(10))]}
    api.prizes = {"win": "Coupon"}
    await _setup(hass, entry, api)

    cards = hass.states.get("sensor.lidl_plus_scratch_cards")
    assert cards.attributes["cards"][0]["wins"] is True
    assert cards.attributes["cards"][0]["prize_type"] == "Coupon"

    await hass.services.async_call(
        "button", "press", {"entity_id": "button.lidl_plus_scratch_cards"}, blocking=True
    )
    await hass.async_block_till_done()

    assert api.redeemed == ["win"]
    assert hass.states.get("sensor.lidl_plus_scratch_cards").state == "0"
    result = hass.states.get("event.lidl_plus_scratch_card_result")
    assert result.attributes["summary"] == (
        "Zdrapka odkryta: wygrana — kupon. Znajdziesz go w kuponach Lidl Plus."
    )


async def test_auto_scratch_on_refresh(hass, enable_custom_integrations):
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            "card_number": "1234",
            "country": "PL",
            "language": "pl-PL",
            "access_token": "access",
            "refresh_token": "refresh",
        },
        options={"auto_coupons": False, "auto_scratch": True},
        unique_id="PL_1234",
    )
    entry.add_to_hass(hass)
    api = FullFakeApi()
    api.home_payload = {"purchaseLottery": [_scratch("lose", _iso(10))]}
    await _setup(hass, entry, api)

    assert api.redeemed == ["lose"]
    assert hass.states.get("sensor.lidl_plus_scratch_cards").state == "0"
    result = hass.states.get("event.lidl_plus_scratch_card_result")
    assert result.attributes["summary"] == "Zdrapka odkryta: tym razem bez nagrody."
    assert hass.states.get("switch.lidl_plus_auto_scratch_cards").state == "on"


def _promotion(promo_id: str, title: str) -> dict:
    return {
        "id": promo_id,
        "promotionId": f"p-{promo_id}",
        "title": title,
        "type": "Standard",
        "isActivated": False,
        "availability": {"apologizeStatus": False},
        "discount": {"title": "-10%"},
        "validity": {"start": "2026-01-01T00:00:00Z", "end": "2026-12-31T00:00:00Z"},
    }


class SectionsApi(StoreApi):
    """Like the live server: Store-Id swaps OtherStores/SSC for FavoriteStore."""

    async def promotions_list(self, store_id=None):
        common = {"name": "AllStores", "promotions": [_promotion("a", "Mleko")]}
        if store_id:
            return {
                "sections": [
                    common,
                    {"name": "FavoriteStore", "promotions": [_promotion("mine", "Gruszki")]},
                ]
            }
        return {
            "sections": [
                common,
                {"name": "OtherStores", "promotions": [
                    _promotion("mine", "Gruszki"),
                    _promotion("far", "Burrata"),
                ]},
                {"name": "SSC", "promotions": [_promotion("ssc", "Na zakupy za 100 zł")]},
            ]
        }


async def test_coupon_count_is_the_full_list_with_my_store_recognised(
    hass, entry, enable_custom_integrations
):
    api = SectionsApi()
    api.favorite = "PL2338"
    await _setup(hass, entry, api)

    coupons = hass.states.get("sensor.lidl_plus_coupons")
    assert coupons.state == "4"
    sections = {c["id"]: c["section"] for c in coupons.attributes["coupons"]}
    assert sections == {
        "a": "AllStores",
        "mine": "FavoriteStore",
        "far": "OtherStores",
        "ssc": "SSC",
    }


async def test_listener_update_without_refresh_does_not_repeat_events(
    hass, entry, enable_custom_integrations
):
    """A shopping-list change updates listeners; it must not re-fire the last news."""
    api = FullFakeApi()
    api.home_payload = {"purchaseLottery": []}
    await _setup(hass, entry, api)

    fired = []
    hass.bus.async_listen(
        "state_changed",
        lambda event: fired.append(event)
        if event.data["entity_id"] == "event.lidl_plus_new_scratch_card"
        else None,
    )
    api.home_payload = {"purchaseLottery": [_scratch("s1", _iso(10))]}
    await _refresh(hass, entry)
    assert len(fired) == 1

    entry.runtime_data.async_update_listeners()
    await hass.async_block_till_done()
    assert len(fired) == 1
