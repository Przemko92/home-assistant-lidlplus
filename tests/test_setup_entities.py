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
