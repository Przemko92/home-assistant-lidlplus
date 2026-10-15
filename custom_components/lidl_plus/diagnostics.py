"""Diagnostics: raw API answers, to see what the app shows besides coupons.

Besides the data the integration already uses, this probes read-only
endpoints found in the Lidl Plus app (home modules, scratch cards, shopping
list, deposits, alerts, leaflets). Each probe records its URL and either the
answer or the HTTP error, so new features can be built on real data.
"""

from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from . import LidlPlusConfigEntry
from .api import LidlPlusApi
from .auth import jwt_payload
from .const import CONF_ACCESS_TOKEN, CONF_CARD_NUMBER, CONF_REFRESH_TOKEN
from .exceptions import LidlPlusError

TO_REDACT = {
    CONF_ACCESS_TOKEN,
    CONF_REFRESH_TOKEN,
    CONF_CARD_NUMBER,
    "loyaltyId",
    "loyalty_id",
    "cardNumber",
    "userId",
    "email",
    "phoneNumber",
    "firstName",
    "lastName",
    "name_first",
    "name_last",
    "birthDate",
    "sub",
}

HOME_BASE = "https://home.lidlplus.com"
REWARD_MODULES = {
    "openGift",
    "purchaseLottery",
    "couponPlus",
    "stampCardRewards",
    "stampCardBenefits",
    "stampCardLottery",
    "promotions",
    "homeMessage",
    "announcements",
}


async def _probe(
    api: LidlPlusApi,
    method: str,
    url: str,
    *,
    headers: dict[str, str] | None = None,
    json: Any = None,
) -> dict[str, Any]:
    try:
        data = await api.request(
            method, url, headers=headers, json=json, force_json=True
        )
    except LidlPlusError as err:
        return {
            "url": url,
            "error": str(err),
            "status": err.status,
            "body": (err.body or "")[:1000],
        }
    return {"url": url, "data": data}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: LidlPlusConfigEntry
) -> dict[str, Any]:
    coordinator = entry.runtime_data
    api = coordinator.api
    country = api.country
    segments = await api.user_segments()
    common = {"Country": country}
    if segments:
        common["Segment-Ids"] = ",".join(segments)
    store_id = coordinator.store_id

    probes: dict[str, Any] = {}
    for label, sid in (("promotions_list", store_id), ("promotions_list_no_store", None)):
        try:
            probes[label] = {"data": await api.promotions_list(sid)}
        except LidlPlusError as err:
            probes[label] = {"error": str(err), "status": err.status}

    # Home screen: configuration (module list), then the logged-in home.
    config: dict[str, Any] | None = None
    for zone in (country, country.lower(), f"home-{country.lower()}"):
        result = await _probe(
            api,
            "GET",
            f"{HOME_BASE}/configuration/v1/config/{zone}",
            headers=common,
        )
        probes[f"home_config_{zone}"] = result
        if isinstance(result.get("data"), dict):
            config = result["data"]
            break
    configurations = (config or {}).get("configurations") or []
    for configuration in configurations[:3]:
        if not isinstance(configuration, dict):
            continue
        names = [
            module if isinstance(module, str) else module.get("moduleName")
            for module in configuration.get("modules") or []
        ]
        names = [name for name in names if name]
        home_id = str(configuration.get("id"))
        # The app sends each module with an aggregate version; try the
        # plausible defaults, the full list first, then just the reward modules.
        variants = {
            "all_v1": [{"moduleName": n, "aggregateVersion": 1} for n in names],
            "rewards_v1": [
                {"moduleName": n, "aggregateVersion": 1}
                for n in names
                if n in REWARD_MODULES
            ],
            "rewards_v0": [
                {"moduleName": n, "aggregateVersion": 0}
                for n in names
                if n in REWARD_MODULES
            ],
        }
        for label, modules in variants.items():
            result = await _probe(
                api,
                "POST",
                f"{HOME_BASE}/api/v2/{country}/home/logged",
                headers={**common, "homeId": home_id},
                json={"storeId": store_id or "", "modules": modules},
            )
            probes[f"home_logged_{home_id}_{label}"] = result
            if "data" in result:
                break

    for name, url in (
        ("alerts_unread", f"https://alerts.lidlplus.com/api/v2/{country}/alerts/unread"),
        ("deposits", f"https://deposits.lidlplus.com/api/v1/{country}/deposits"),
        ("brochures", f"https://brochures.lidlplus.com/api/v2/{country}/Brochures"),
    ):
        probes[name] = await _probe(api, "GET", url, headers=common)
    user_id = jwt_payload(api.access_token).get("sub")
    if user_id:
        result = await _probe(
            api,
            "GET",
            f"https://purchaselottery.lidlplus.com/api/v2/{country}/lotteries?userId={user_id}",
            headers=common,
        )
        result["url"] = result["url"].replace(str(user_id), "**REDACTED**")
        probes["lotteries"] = result
    favorite = await _probe(
        api, "GET", f"https://stores.lidlplus.com/api/v1/users/me/favorite/{country}"
    )
    probes["favorite_store"] = favorite
    if store_id:
        probes["store_details"] = await _probe(
            api,
            "GET",
            f"https://stores.lidlplus.com/api/v5/{country}/details",
            headers={"Country": country, "storesIds": store_id},
        )
        today = dt_util.now().date().isoformat()
        for label, sid in (
            ("store_schedule", store_id),
            ("store_schedule_num", store_id.removeprefix(country)),
        ):
            probes[label] = await _probe(
                api,
                "GET",
                f"https://stores.lidlplus.com/api/v3/schedule/{sid}?date={today}",
                headers=common,
            )
    data = coordinator.data
    probes["shopping_list_items"] = {
        shopping_list.list_id: shopping_list.items
        for shopping_list in (data.shopping_lists if data else [])
    }
    return async_redact_data(
        {
            "entry": {"data": dict(entry.data), "options": dict(entry.options)},
            "store_id": store_id,
            "segments": segments,
            "probes": probes,
        },
        TO_REDACT,
    )
