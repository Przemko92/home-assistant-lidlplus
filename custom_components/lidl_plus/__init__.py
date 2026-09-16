"""Unofficial Lidl Plus Home Assistant integration."""

from __future__ import annotations

from datetime import timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import LidlPlusApi
from .const import (
    CONF_ACCESS_TOKEN,
    CONF_COUNTRY,
    CONF_LANGUAGE,
    CONF_REFRESH_TOKEN,
    CONF_SCAN_INTERVAL,
    DEFAULT_SCAN_INTERVAL,
    PLATFORMS,
)
from .coordinator import LidlPlusCoordinator
from .countries import get_country

type LidlPlusConfigEntry = ConfigEntry[LidlPlusCoordinator]


async def async_setup_entry(hass: HomeAssistant, entry: LidlPlusConfigEntry) -> bool:
    session = async_get_clientsession(hass)
    country = get_country(entry.data.get(CONF_COUNTRY))
    language = str(entry.data.get(CONF_LANGUAGE) or country.locale())

    async def _save_tokens(tokens: dict[str, str]) -> None:
        hass.config_entries.async_update_entry(
            entry,
            data={
                **entry.data,
                CONF_ACCESS_TOKEN: tokens[CONF_ACCESS_TOKEN],
                CONF_REFRESH_TOKEN: tokens[CONF_REFRESH_TOKEN],
            },
        )

    api = LidlPlusApi(
        session,
        entry.data[CONF_ACCESS_TOKEN],
        entry.data[CONF_REFRESH_TOKEN],
        country=country.code,
        language=language,
        save_tokens=_save_tokens,
    )
    coordinator = LidlPlusCoordinator(hass, entry, api)
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: LidlPlusConfigEntry) -> bool:
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def _async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    coordinator: LidlPlusCoordinator = entry.runtime_data
    minutes = entry.options.get(CONF_SCAN_INTERVAL)
    coordinator.update_interval = (
        timedelta(minutes=int(minutes)) if minutes else DEFAULT_SCAN_INTERVAL
    )
