"""The app's shopping list as a to-do list, synced through PATCH v5/lists/{id}."""

from unittest.mock import patch

import pytest
from homeassistant.components.todo import DOMAIN as TODO_DOMAIN
from homeassistant.exceptions import HomeAssistantError
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.lidl_plus.const import DOMAIN

from .test_setup_entities import FullFakeApi

LIST_ID = "a752dbf1-a4ee-4809-92c6-305b672c8079"
ENTITY = "todo.lidl_plus_testowa"


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


@pytest.fixture
def api():
    api = FullFakeApi()
    # Shaped like the live GET v4/lists answer.
    api.lists = [
        {
            "id": LIST_ID,
            "title": "testowa",
            "type": "Standard",
            "eTag": "54ac5372",
            "lastUpdate": "2026-10-13T12:28:41.083",
            "membersCount": 1,
            "itemsCount": 1,
        }
    ]
    api.list_items = {
        LIST_ID: {
            "i1": {
                "id": "i1",
                "type": "FreeText",
                "title": "koncentrat pomidorowy",
                "quantity": 1,
                "isChecked": False,
                "lastUpdate": "2026-10-13T12:28:41.083",
            }
        }
    }
    return api


async def _setup(hass, entry, api):
    with (
        patch("custom_components.lidl_plus.LidlPlusApi", return_value=api),
        patch("custom_components.lidl_plus.async_get_clientsession", return_value=None),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()


def _last_change(api):
    """The last sync call that changed something (reads send no items or deletes)."""
    return next(call for call in reversed(api.sync_calls) if call[1] or call[3])


async def _items(hass):
    result = await hass.services.async_call(
        TODO_DOMAIN,
        "get_items",
        {"entity_id": ENTITY},
        blocking=True,
        return_response=True,
    )
    return result[ENTITY]["items"]


async def test_list_items_are_read_without_changing_anything(
    hass, entry, api, enable_custom_integrations
):
    await _setup(hass, entry, api)

    assert hass.states.get(ENTITY).state == "1"
    items = await _items(hass)
    assert [(i["summary"], i["status"]) for i in items] == [
        ("koncentrat pomidorowy", "needs_action")
    ]
    # The first read sends no items and an empty order, like a fresh app.
    assert api.sync_calls[0] == (LIST_ID, [], [], None)


async def test_later_reads_keep_the_known_order(
    hass, entry, api, enable_custom_integrations
):
    await _setup(hass, entry, api)
    await entry.runtime_data.async_refresh()
    await hass.async_block_till_done()
    assert api.sync_calls[-1] == (LIST_ID, [], ["i1"], None)


async def test_add_check_and_delete(hass, entry, api, enable_custom_integrations):
    await _setup(hass, entry, api)

    await hass.services.async_call(
        TODO_DOMAIN, "add_item", {"entity_id": ENTITY, "item": "mleko"}, blocking=True
    )
    _, items, order, deleted = _last_change(api)
    [new] = items
    assert new["type"] == "FreeText"
    assert new["title"] == "mleko"
    assert new["isChecked"] is False
    assert order == ["i1", new["id"]]
    assert deleted is None
    assert [i["summary"] for i in await _items(hass)] == ["koncentrat pomidorowy", "mleko"]

    await hass.services.async_call(
        TODO_DOMAIN,
        "update_item",
        {"entity_id": ENTITY, "item": "koncentrat pomidorowy", "status": "completed"},
        blocking=True,
    )
    _, items, _, _ = _last_change(api)
    assert items[0]["id"] == "i1"
    assert items[0]["isChecked"] is True
    assert items[0]["title"] == "koncentrat pomidorowy"

    await hass.services.async_call(
        TODO_DOMAIN, "remove_item", {"entity_id": ENTITY, "item": "mleko"}, blocking=True
    )
    _, items, order, deleted = _last_change(api)
    assert items == []
    assert order == ["i1"]
    [removed] = deleted
    assert removed["itemId"] == new["id"]
    assert removed["lastUpdate"]
    assert [(i["summary"], i["status"]) for i in await _items(hass)] == [
        ("koncentrat pomidorowy", "completed")
    ]


async def test_no_writes_when_items_could_not_be_read(
    hass, entry, api, enable_custom_integrations
):
    from custom_components.lidl_plus.exceptions import LidlPlusError

    async def broken(*args, **kwargs):
        raise LidlPlusError("http_500", status=500)

    api.sync_shopping_list = broken
    await _setup(hass, entry, api)

    with pytest.raises(HomeAssistantError, match="not loaded"):
        await hass.services.async_call(
            TODO_DOMAIN, "add_item", {"entity_id": ENTITY, "item": "mleko"}, blocking=True
        )


async def test_update_retries_with_the_servers_last_update(
    hass, entry, api, enable_custom_integrations
):
    api.versioned = True
    await _setup(hass, entry, api)

    await hass.services.async_call(
        TODO_DOMAIN,
        "update_item",
        {"entity_id": ENTITY, "item": "koncentrat pomidorowy", "status": "completed"},
        blocking=True,
    )
    sent = [call[1][0]["lastUpdate"] for call in api.sync_calls if call[1]]
    assert sent[-1] == "2026-10-13T12:28:41.083"
    assert len(sent) == 2
    assert [i["status"] for i in await _items(hass)] == ["completed"]


async def test_update_that_never_sticks_is_an_error(
    hass, entry, api, enable_custom_integrations
):
    await _setup(hass, entry, api)
    original = api.sync_shopping_list

    async def ignore_items(list_id, items, **kwargs):
        return await original(list_id, [], **kwargs)

    api.sync_shopping_list = ignore_items
    with pytest.raises(HomeAssistantError, match="did not save"):
        await hass.services.async_call(
            TODO_DOMAIN,
            "update_item",
            {"entity_id": ENTITY, "item": "koncentrat pomidorowy", "status": "completed"},
            blocking=True,
        )


async def test_option_off_skips_lists_and_removes_their_entities(
    hass, entry, api, enable_custom_integrations
):
    await _setup(hass, entry, api)
    assert hass.states.get(ENTITY) is not None
    calls = len(api.sync_calls)

    with (
        patch("custom_components.lidl_plus.LidlPlusApi", return_value=api),
        patch("custom_components.lidl_plus.async_get_clientsession", return_value=None),
    ):
        hass.config_entries.async_update_entry(
            entry, options={**entry.options, "shopping_lists": False}
        )
        await hass.async_block_till_done()

    from homeassistant.helpers import entity_registry as er

    assert er.async_get(hass).async_get(ENTITY) is None
    assert hass.states.get(ENTITY) is None
    assert entry.runtime_data.data.shopping_lists == []
    # Nothing is fetched from the shopping-list service any more.
    assert len(api.sync_calls) == calls
