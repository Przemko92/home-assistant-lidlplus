"""Sign-in: Browser Companion menu with Supervisor, pasted redirect without it."""

from unittest.mock import AsyncMock, patch

import pytest
from homeassistant import config_entries
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.lidl_plus.const import DOMAIN
from custom_components.lidl_plus.exceptions import (
    LidlPlusAuthError,
    LidlPlusCannotConnect,
)

TOKENS = {"access_token": "access", "refresh_token": "refresh"}
REDIRECT = (
    "Failed to launch 'com.lidlplus.app://callback?code=abc.def&scope=openid"
    "&state=xyz' because the scheme does not have a registered handler."
)


@pytest.fixture(autouse=True)
def _custom(enable_custom_integrations):
    return


@pytest.fixture(autouse=True)
def _no_gateway():
    """Keep the country list offline (static table)."""
    with patch(
        "custom_components.lidl_plus.config_flow.fetch_gateway_countries",
        new=AsyncMock(return_value=None),
    ):
        yield


def _supervisor(present: bool):
    return patch(
        "custom_components.lidl_plus.config_flow.LidlPlusConfigFlow.companion_supervisor_present",
        return_value=present,
    )


@pytest.fixture
def no_supervisor():
    with _supervisor(False):
        yield


@pytest.fixture
def exchange():
    with patch(
        "custom_components.lidl_plus.config_flow.OAuthLogin.exchange_code",
        new=AsyncMock(return_value=TOKENS),
    ) as mock:
        yield mock


@pytest.fixture
def loyalty():
    with (
        patch(
            "custom_components.lidl_plus.config_flow.LidlPlusApi.loyalty_id",
            new=AsyncMock(return_value="4000000001234"),
        ),
        patch("custom_components.lidl_plus.async_setup_entry", return_value=True),
    ):
        yield


async def _pick_poland(hass):
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["step_id"] == "user"
    return await hass.config_entries.flow.async_configure(
        result["flow_id"], {"country": "PL"}
    )


async def test_without_supervisor_country_then_manual_form(hass, no_supervisor):
    result = await _pick_poland(hass)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "manual"
    url = result["description_placeholders"]["auth_url"]
    assert "client_id=LidlPlusNativeClient" in url
    assert "code_challenge=" in url
    assert "Country=PL" in url


async def test_with_supervisor_offers_companion_first(hass):
    with _supervisor(True):
        result = await _pick_poland(hass)
    assert result["type"] is FlowResultType.MENU
    assert result["step_id"] == "sign_in"
    assert result["menu_options"] == ["companion", "manual"]


async def test_manual_creates_entry(hass, no_supervisor, exchange, loyalty):
    result = await _pick_poland(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"redirect_url": REDIRECT}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Lidl Plus PL 4000000001234"
    assert result["data"]["refresh_token"] == "refresh"
    assert result["data"]["country"] == "PL"
    code, verifier = exchange.await_args.args
    assert code == "abc.def"
    assert verifier


async def test_manual_keeps_link_between_attempts(hass, no_supervisor):
    result = await _pick_poland(hass)
    first_url = result["description_placeholders"]["auth_url"]
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"redirect_url": "  "}
    )
    assert result["errors"] == {"base": "invalid_code"}
    assert result["description_placeholders"]["auth_url"] == first_url


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (LidlPlusAuthError("token_rejected"), "invalid_code"),
        (LidlPlusCannotConnect("timeout"), "cannot_connect"),
    ],
)
async def test_manual_exchange_errors(hass, no_supervisor, error, expected):
    result = await _pick_poland(hass)
    with patch(
        "custom_components.lidl_plus.config_flow.OAuthLogin.exchange_code",
        new=AsyncMock(side_effect=error),
    ):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"redirect_url": REDIRECT}
        )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": expected}


def _entry(hass, card: str = "4000000001234") -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            "access_token": "old",
            "refresh_token": "old",
            "country": "PL",
            "language": "pl-PL",
            "card_number": card,
        },
        unique_id=f"PL_{card}",
    )
    entry.add_to_hass(hass)
    return entry


async def test_manual_reauth_skips_country_and_updates_entry(
    hass, no_supervisor, exchange, loyalty
):
    entry = _entry(hass)
    result = await entry.start_reauth_flow(hass)
    assert result["step_id"] == "manual"
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"redirect_url": REDIRECT}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert entry.data["refresh_token"] == "refresh"


async def test_manual_reconfigure_updates_entry(hass, no_supervisor, exchange, loyalty):
    entry = _entry(hass)
    result = await entry.start_reconfigure_flow(hass)
    assert result["step_id"] == "manual"
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"redirect_url": REDIRECT}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    assert entry.data["refresh_token"] == "refresh"
    assert len(hass.config_entries.async_entries(DOMAIN)) == 1


async def test_reconfigure_rejects_other_card(hass, no_supervisor, exchange, loyalty):
    entry = _entry(hass, card="4000000009999")
    result = await entry.start_reconfigure_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"redirect_url": REDIRECT}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "wrong_account"
    assert entry.data["refresh_token"] == "old"
