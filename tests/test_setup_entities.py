"""Entity set-up for Lidl Plus."""

from unittest.mock import patch

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.lidl_plus.const import DOMAIN


class FullFakeApi:
    country = "PL"
    language = "pl-PL"

    async def loyalty_id(self) -> str:
        return "1234"

    async def tickets(self, year_offset: int = 0):
        return []

    async def ticket(self, ticket_id: str):
        return {}

    async def promotions_list(self, store_id=None):
        return {
            "sections": [
                {
                    "name": "Weekly",
                    "promotions": [
                        {
                            "id": "a",
                            "promotionId": "p-a",
                            "title": "Milk",
                            "isActivated": False,
                            "isProcessing": False,
                            "availability": {"apologizeStatus": False},
                            "discount": {"title": "-1"},
                            "validity": {
                                "start": "2026-01-01T00:00:00Z",
                                "end": "2026-12-31T00:00:00Z",
                            },
                        },
                        {
                            "id": "b",
                            "promotionId": "p-b",
                            "title": "Bread",
                            "isActivated": False,
                            "isProcessing": False,
                            "availability": {"apologizeStatus": False},
                            "discount": {"title": "-2"},
                            "validity": {
                                "start": "2026-01-01T00:00:00Z",
                                "end": "2026-12-31T00:00:00Z",
                            },
                        },
                    ],
                }
            ]
        }

    async def activate_promotion(self, promotion_id: str):
        return None

    home_payload: dict | None = None
    deposits_payload: dict | None = None
    unread: int | None = 0

    async def home_config(self):
        return {"configurations": [{"id": "mainHome", "modules": ["purchaseLottery", "openGift"]}]}

    async def home(self, home_id, modules, store_id):
        return self.home_payload or {}

    async def deposits(self):
        return self.deposits_payload

    async def alerts_unread(self):
        return self.unread

    schedule_payload: dict | None = None

    async def store_schedule(self, store_id, day):
        return self.schedule_payload

    favorite: str | None = None
    details_payload: dict | None = None

    async def favorite_store(self):
        return self.favorite

    async def store_details(self, store_key):
        return self.details_payload

    lists: list | None = None
    versioned = False
    list_items: dict | None = None
    sync_calls: list | None = None

    async def shopping_lists(self):
        return self.lists or []

    async def sync_shopping_list(
        self, list_id, items, *, item_ids=None, deleted=None, store_id=None
    ):
        """Behaves like the app's sync: merge changed items, apply order and deletes."""
        if self.sync_calls is None:
            self.sync_calls = []
        self.sync_calls.append((list_id, items, item_ids, deleted))
        store = self.list_items.setdefault(list_id, {})
        for item in items:
            known = store.get(item["id"])
            # Versioned mode: an update only sticks with the stored lastUpdate.
            if (
                self.versioned
                and known
                and item.get("lastUpdate") != known.get("lastUpdate")
            ):
                continue
            store[item["id"]] = {**(known or {}), **item}
        for removed in deleted or []:
            # Like the server: removals are {"itemId", "lastUpdate"} objects.
            if isinstance(removed, dict):
                store.pop(removed["itemId"], None)
        order = item_ids if item_ids else list(store)
        return {
            "items": list(store.values()),
            "list": {"itemIds": [i for i in order if i in store], "deletedItems": []},
        }

    prizes: dict | None = None
    redeemed: list | None = None

    async def lottery_detail(self, lottery_id):
        prize = (self.prizes or {}).get(lottery_id, "None")
        return {"id": lottery_id, "prize": {"type": prize}, "promotion": {"text": "Zdrapka"}}

    async def redeem_lottery(self, lottery_id):
        if self.redeemed is None:
            self.redeemed = []
        self.redeemed.append(lottery_id)
        if self.home_payload:
            self.home_payload["purchaseLottery"] = [
                card
                for card in self.home_payload.get("purchaseLottery", [])
                if card["id"] != lottery_id
            ]


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


async def test_entities_are_created(hass, entry, enable_custom_integrations):
    await _setup(hass, entry, FullFakeApi())

    assert hass.states.get("sensor.lidl_plus_coupons").state == "2"
    assert hass.states.get("sensor.lidl_plus_coupons_ready").state == "2"
    assert hass.states.get("sensor.lidl_plus_upcoming_coupons").state == "0"
    assert hass.states.get("sensor.lidl_plus_last_transactions") is not None
    assert hass.states.get("sensor.lidl_plus_recent_receipts") is None
    assert hass.states.get("button.lidl_plus_activate_coupons") is not None
    assert hass.states.get("sensor.lidl_plus_loyalty_card").state == "1234"
