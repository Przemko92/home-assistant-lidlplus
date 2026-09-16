"""Config flow for Lidl Plus — country, then Browser Companion."""

from __future__ import annotations

from typing import Any

import voluptuous as vol
from ha_browser_companion import CompanionLoginFlow, CompanionStart, captured_query
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
)

from .api import LidlPlusApi, fetch_gateway_countries
from .auth import OAuthLogin, auth_code_from_redirect
from .const import (
    COMPANION_WAIT,
    CONF_ACCESS_TOKEN,
    CONF_AUTO_COUPONS,
    CONF_CARD_NUMBER,
    CONF_COUNTRY,
    CONF_LANGUAGE,
    CONF_REFRESH_TOKEN,
    CONF_SCAN_INTERVAL,
    DEFAULT_AUTO_COUPONS,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
)
from .countries import (
    COUNTRIES,
    Country,
    country_options,
    default_country_code,
    get_country,
    language_options,
    merge_gateway_countries,
    needs_language_step,
)
from .exceptions import LidlPlusAuthError, LidlPlusCannotConnect


class LidlPlusConfigFlow(CompanionLoginFlow, ConfigFlow, domain=DOMAIN):
    """Sign in through the Browser Companion add-on."""

    VERSION = 1
    companion_client_id = DOMAIN

    def __init__(self) -> None:
        self._login: OAuthLogin | None = None
        self._reauth_entry: ConfigEntry | None = None
        self._countries = dict(COUNTRIES)
        self._country: Country | None = None
        self._language: str | None = None

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if not self.companion_supervisor_present():
            return self.async_abort(reason="companion_requires_supervisor")
        if self._reauth_entry:
            self._country = get_country(self._reauth_entry.data.get(CONF_COUNTRY))
            self._language = str(
                self._reauth_entry.data.get(CONF_LANGUAGE) or self._country.locale()
            )
            return await self.async_step_companion()
        await self._load_countries()
        if user_input is not None:
            self._country = get_country(str(user_input[CONF_COUNTRY]))
            if needs_language_step(self._country):
                return await self.async_step_language()
            self._language = self._country.locale()
            return await self.async_step_companion()
        default = default_country_code(getattr(self.hass.config, "country", None))
        if default not in self._countries:
            default = next(iter(self._countries))
        schema = vol.Schema(
            {
                vol.Required(CONF_COUNTRY, default=default): SelectSelector(
                    SelectSelectorConfig(
                        options=country_options(self._countries),
                        mode=SelectSelectorMode.DROPDOWN,
                    )
                )
            }
        )
        return self.async_show_form(step_id="user", data_schema=schema)

    async def async_step_language(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        country = self._country or get_country(None)
        if user_input is not None:
            self._language = str(user_input[CONF_LANGUAGE])
            return await self.async_step_companion()
        schema = vol.Schema(
            {
                vol.Required(CONF_LANGUAGE, default=country.locale()): SelectSelector(
                    SelectSelectorConfig(
                        options=language_options(country),
                        mode=SelectSelectorMode.DROPDOWN,
                    )
                )
            }
        )
        return self.async_show_form(step_id="language", data_schema=schema)

    async def _load_countries(self) -> None:
        session = async_get_clientsession(self.hass)
        payload = await fetch_gateway_countries(session)
        if payload is not None:
            self._countries = merge_gateway_countries(payload)

    async def async_companion_start(self) -> CompanionStart:
        country = self._country or get_country(None)
        language = self._language or country.locale()
        if self._login is None:
            self._login = OAuthLogin.create(country=country.code, language=language)
        return CompanionStart(start_url=self._login.prepare(), wait=COMPANION_WAIT)

    async def async_companion_finish(
        self, captured: dict[str, Any]
    ) -> ConfigFlowResult:
        code = captured_query(captured, "code")
        if not code:
            try:
                code = auth_code_from_redirect(str(captured.get("url") or ""))
            except LidlPlusAuthError:
                return await self.async_step_companion_failed()
        if self._login is None or not self._login.verifier:
            return await self.async_step_companion_failed()
        try:
            tokens = await self._login.exchange_code(str(code), self._login.verifier)
            return await self._async_finish(tokens)
        except LidlPlusCannotConnect:
            return self.async_abort(reason="cannot_connect")
        except LidlPlusAuthError:
            return await self.async_step_companion_failed()

    async def async_companion_on_close(self) -> None:
        if self._login is not None:
            await self._login.close()
            self._login = None

    async def async_step_reauth(self, entry_data: dict[str, Any]) -> ConfigFlowResult:
        self._reauth_entry = self.hass.config_entries.async_get_entry(
            self.context["entry_id"]
        )
        return await self.async_step_user()

    async def _async_finish(self, tokens: dict[str, str]) -> ConfigFlowResult:
        country = self._country or get_country(None)
        language = self._language or country.locale()
        session = async_get_clientsession(self.hass)
        api = LidlPlusApi(
            session,
            tokens[CONF_ACCESS_TOKEN],
            tokens[CONF_REFRESH_TOKEN],
            country=country.code,
            language=language,
        )
        try:
            card = await api.loyalty_id()
        except LidlPlusAuthError:
            return self.async_abort(reason="invalid_token")
        except LidlPlusCannotConnect:
            return self.async_abort(reason="cannot_connect")

        card = card or "unknown"
        unique_id = f"{country.code}_{card}"
        await self.async_set_unique_id(unique_id)
        data = {
            CONF_ACCESS_TOKEN: api.access_token,
            CONF_REFRESH_TOKEN: api.refresh_token,
            CONF_COUNTRY: country.code,
            CONF_LANGUAGE: language,
            CONF_CARD_NUMBER: card,
        }
        if self._reauth_entry:
            if self._reauth_entry.unique_id not in (None, unique_id):
                return self.async_abort(reason="wrong_account")
            return self.async_update_reload_and_abort(self._reauth_entry, data=data)
        self._abort_if_unique_id_configured()

        return self.async_create_entry(
            title=f"Lidl Plus {country.code} {card}",
            data=data,
            options={
                CONF_AUTO_COUPONS: DEFAULT_AUTO_COUPONS,
                CONF_SCAN_INTERVAL: int(DEFAULT_SCAN_INTERVAL.total_seconds() // 60),
            },
        )

    @staticmethod
    @callback
    def async_get_options_flow(_config_entry: ConfigEntry) -> OptionsFlow:
        return LidlPlusOptionsFlow()


class LidlPlusOptionsFlow(OptionsFlow):
    """Poll interval and auto coupon activation."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)
        options = self.config_entry.options
        schema = vol.Schema(
            {
                vol.Required(
                    CONF_SCAN_INTERVAL,
                    default=int(
                        options.get(
                            CONF_SCAN_INTERVAL,
                            DEFAULT_SCAN_INTERVAL.total_seconds() // 60,
                        )
                    ),
                ): NumberSelector(
                    NumberSelectorConfig(
                        min=5,
                        max=120,
                        step=5,
                        mode=NumberSelectorMode.BOX,
                        unit_of_measurement="min",
                    )
                ),
                vol.Required(
                    CONF_AUTO_COUPONS,
                    default=options.get(CONF_AUTO_COUPONS, DEFAULT_AUTO_COUPONS),
                ): bool,
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema)
