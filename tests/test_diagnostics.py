"""Diagnostics probe read-only endpoints and redact personal data."""

from unittest.mock import patch

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.lidl_plus.const import DOMAIN
from custom_components.lidl_plus.diagnostics import async_get_config_entry_diagnostics
from custom_components.lidl_plus.exceptions import LidlPlusError

from .test_setup_entities import FullFakeApi


@pytest.fixture
def lidl_entry(hass):
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


class ProbeApi(FullFakeApi):
    """Answers the home config and lotteries; everything else is a 404."""

    # JWT whose payload is {"sub": "u-1"}: the lottery probe needs the user id.
    access_token = "x.eyJzdWIiOiAidS0xIn0.y"

    def __init__(self):
        self.calls: list[tuple[str, str, object]] = []

    async def user_segments(self):
        return ["seg1"]

    async def request(self, method, url, *, headers=None, json=None, force_json=False):
        self.calls.append((method, url, json))
        if url.endswith("/configuration/v1/config/PL"):
            return {
                "configurations": [
                    {"id": "home-1", "modules": ["openGift", "recipes"]}
                ]
            }
        if url.endswith("/home/logged"):
            return {"openGift": {"activeBoxes": [{"id": "box"}]}, "userId": "u-1"}
        if "/lotteries?userId=" in url:
            return [{"id": "lot-1"}]
        raise LidlPlusError("http_404", status=404, body="not found")


async def test_diagnostics_probe_and_redact(
    hass, lidl_entry, enable_custom_integrations
):
    entry = lidl_entry
    api = ProbeApi()
    with (
        patch("custom_components.lidl_plus.LidlPlusApi", return_value=api),
        patch("custom_components.lidl_plus.async_get_clientsession", return_value=None),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    result = await async_get_config_entry_diagnostics(hass, entry)

    assert result["entry"]["data"]["refresh_token"] == "**REDACTED**"
    assert result["entry"]["data"]["card_number"] == "**REDACTED**"
    probes = result["probes"]
    assert probes["promotions_list"]["data"]["sections"][0]["name"] == "Weekly"
    home = probes["home_logged_home-1_all_v1"]
    assert home["data"]["openGift"]["activeBoxes"] == [{"id": "box"}]
    assert home["data"]["userId"] == "**REDACTED**"
    assert probes["lotteries"]["data"] == [{"id": "lot-1"}]
    assert probes["lotteries"]["url"].endswith("userId=**REDACTED**")
    assert "/lotteries?userId=u-1" in next(c[1] for c in api.calls if "lotteries" in c[1])
    assert probes["deposits"]["status"] == 404
    assert probes["deposits"]["body"] == "not found"
    method, _url, body = next(c for c in api.calls if c[1].endswith("/home/logged"))
    assert method == "POST"
    assert body["modules"] == [
        {"moduleName": "openGift", "aggregateVersion": 1},
        {"moduleName": "recipes", "aggregateVersion": 1},
    ]
