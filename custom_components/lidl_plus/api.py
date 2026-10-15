"""REST client for Lidl Plus profile, tickets and coupons APIs."""

from __future__ import annotations

import json as json_lib
import logging
import time
from collections.abc import Awaitable, Callable
from typing import Any

import aiohttp

from .auth import jwt_payload, refresh_tokens
from .const import (
    ACTION_LOCATION,
    ALERTS_BASE,
    API_USER_AGENT,
    APP_PACKAGE,
    APP_VERSION,
    COUNTRIES_URL,
    COUPONS_BASE,
    DEPOSITS_BASE,
    HOME_BASE,
    LOTTERY_BASE,
    OPERATING_SYSTEM,
    OS_VERSION,
    PROFILE_BASE,
    SEGMENTS_BASE,
    SHOPPING_LIST_BASE,
    STORES_BASE,
    TICKETS_BASE,
)
from .exceptions import LidlPlusAuthError, LidlPlusCannotConnect, LidlPlusError

_LOGGER = logging.getLogger(__name__)

SaveTokens = Callable[[dict[str, str]], Awaitable[None]]


def _loads_json(text: str) -> Any:
    if not text:
        return None
    try:
        return json_lib.loads(text)
    except json_lib.JSONDecodeError:
        return text


def as_ticket_list(payload: Any) -> list[dict[str, Any]]:
    """Normalize ticket list payloads into a flat list of dicts."""
    if isinstance(payload, list):
        tickets: list[dict[str, Any]] = []
        for item in payload:
            if not isinstance(item, dict):
                continue
            if item.get("id"):
                tickets.append(item)
                continue
            nested = None
            for key in ("tickets", "items", "data", "results"):
                value = item.get(key)
                if isinstance(value, list):
                    nested = value
                    break
            if nested is not None:
                tickets.extend(as_ticket_list(nested))
        return tickets
    if isinstance(payload, dict):
        if payload.get("id") and ("totalAmount" in payload or "date" in payload):
            return [payload]
        for key in ("tickets", "items", "data", "results"):
            value = payload.get(key)
            if isinstance(value, list):
                return as_ticket_list(value)
            if isinstance(value, dict):
                found = as_ticket_list(value)
                if found:
                    return found
    return []


def api_accept_language(language: str, country: str = "") -> str:
    """Ticket backends want a 2-letter tag, not a locale like pl-PL."""
    primary = (language or "").strip().split("-", 1)[0].lower()
    if len(primary) >= 2:
        return primary[:2]
    fallback = (country or "en").strip().lower()
    return fallback[:2] if fallback else "en"


def _filled(value: Any) -> bool:
    if value is None or value == "":
        return False
    if isinstance(value, (list, dict)) and not value:
        return False
    return True


def as_ticket_detail(payload: Any) -> dict[str, Any]:
    """Unwrap ticket-detail envelopes used by some country backends."""
    if not isinstance(payload, dict):
        return {}
    for key in ("data", "ticket", "result", "payload"):
        nested = payload.get(key)
        if not isinstance(nested, dict):
            continue
        if any(
            field in nested
            for field in (
                "itemsLine",
                "store",
                "sequenceNumber",
                "htmlPrintedReceipt",
                "items",
            )
        ):
            merged = dict(payload)
            for nested_key, nested_value in nested.items():
                if _filled(nested_value) or not _filled(merged.get(nested_key)):
                    merged[nested_key] = nested_value
            merged.pop(key, None)
            return merged
    return payload


def _as_segment_ids(payload: Any) -> list[str]:
    """Normalize the user-segments payload into a list of id strings."""
    values: Any = payload
    if isinstance(payload, dict):
        for key in (
            "segmentIds",
            "segments",
            "userSegments",
            "userSegmentIds",
            "ids",
            "data",
            "items",
            "values",
            "results",
        ):
            nested = payload.get(key)
            if nested is not None:
                values = nested
                break
    if isinstance(values, dict):
        values = list(values.values())
    if not isinstance(values, list):
        return [str(values)] if values else []
    segments: list[str] = []
    for item in values:
        if isinstance(item, dict):
            raw = item.get("id") or item.get("segmentId") or item.get("value")
        else:
            raw = item
        if raw is None or raw is False:
            continue
        text = str(raw).strip()
        if text:
            segments.append(text)
    return segments


class LidlPlusApi:
    """Thin authenticated JSON client for Lidl Plus backends."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        access_token: str,
        refresh_token: str,
        *,
        country: str,
        language: str,
        save_tokens: SaveTokens | None = None,
    ) -> None:
        self._session = session
        self._access_token = access_token
        self._refresh_token = refresh_token
        self.country = country.upper()
        self.language = language
        self._save_tokens = save_tokens

    @property
    def access_token(self) -> str:
        return self._access_token

    @property
    def refresh_token(self) -> str:
        return self._refresh_token

    async def ensure_fresh_token(self) -> None:
        payload = jwt_payload(self._access_token)
        exp = payload.get("exp")
        if isinstance(exp, (int, float)):
            if exp - 60 > time.time():
                return
        await self._refresh()

    async def _refresh(self) -> None:
        tokens = await refresh_tokens(self._session, self._refresh_token)
        self._access_token = tokens["access_token"]
        self._refresh_token = tokens["refresh_token"]
        if self._save_tokens:
            await self._save_tokens(tokens)

    async def request(
        self,
        method: str,
        url: str,
        *,
        params: dict[str, Any] | None = None,
        json: Any = None,
        headers: dict[str, str] | None = None,
        force_json: bool = False,
        auth: bool = True,
    ) -> Any:
        if auth:
            await self.ensure_fresh_token()
        return await self._request_once(
            method,
            url,
            params=params,
            json=json,
            extra_headers=headers,
            force_json=force_json,
            retry=auth,
            auth=auth,
        )

    async def _request_once(
        self,
        method: str,
        url: str,
        *,
        params: dict[str, Any] | None,
        json: Any,
        extra_headers: dict[str, str] | None,
        force_json: bool,
        retry: bool,
        auth: bool,
    ) -> Any:
        headers = {
            "User-Agent": API_USER_AGENT,
            "App": APP_PACKAGE,
            "App-Version": APP_VERSION,
            "Operating-System": OPERATING_SYSTEM,
            "OS-Version": OS_VERSION,
            "Accept-Language": api_accept_language(self.language, self.country),
            "Accept": "application/json",
        }
        if auth:
            headers["Authorization"] = f"Bearer {self._access_token}"
        if extra_headers:
            headers.update(extra_headers)
        try:
            async with self._session.request(
                method,
                url,
                params=params,
                json=json,
                headers=headers,
                allow_redirects=True,
            ) as response:
                if response.status == 401 and retry and auth:
                    await self._refresh()
                    return await self._request_once(
                        method,
                        url,
                        params=params,
                        json=json,
                        extra_headers=extra_headers,
                        force_json=force_json,
                        retry=False,
                        auth=auth,
                    )
                if response.status == 401:
                    raise LidlPlusAuthError("unauthorized")
                if response.status == 204:
                    return None
                if response.status >= 400:
                    body = await response.text()
                    _LOGGER.debug(
                        "API %s %s: %s", response.method, response.url, body[:500]
                    )
                    raise LidlPlusError(
                        f"http_{response.status}", status=response.status, body=body
                    )
                if force_json:
                    return _loads_json(await response.text())
                if response.content_type and "json" not in response.content_type:
                    return await response.text()
                return await response.json(content_type=None)
        except TimeoutError as err:
            raise LidlPlusCannotConnect("timeout") from err
        except aiohttp.ClientError as err:
            raise LidlPlusCannotConnect(str(err)) from err

    async def loyalty_id(self) -> str:
        result = await self.request("GET", f"{PROFILE_BASE}/v1/{self.country}/loyalty")
        if isinstance(result, str):
            return result.strip().strip('"')
        if isinstance(result, dict):
            for key in ("loyaltyId", "loyalty_id", "id", "cardNumber"):
                value = result.get(key)
                if value:
                    return str(value)
        return str(result or "")

    async def tickets(self, year_offset: int = 0) -> list[dict[str, Any]]:
        result = await self.request(
            "GET",
            f"{TICKETS_BASE}/v3/{self.country}/tickets",
            params={"yearOffset": year_offset},
            headers={
                "Country": self.country,
                "Accept-Language": api_accept_language(self.language, self.country),
            },
        )
        return as_ticket_list(result)

    async def ticket(self, ticket_id: str) -> dict[str, Any]:
        result = await self.request(
            "GET",
            f"{TICKETS_BASE}/v3/{self.country}/tickets/{ticket_id}",
            headers={
                "Country": self.country,
                "Accept-Language": api_accept_language(self.language, self.country),
            },
        )
        return as_ticket_detail(result)

    async def user_segments(self) -> list[str]:
        try:
            result = await self.request(
                "GET",
                f"{SEGMENTS_BASE}/v1/usersegments/{self.country}",
            )
        except LidlPlusError as err:
            _LOGGER.debug("User segments failed: %s", err)
            return []
        return _as_segment_ids(result)

    async def promotions_list(self, store_id: str | None = None) -> dict[str, Any]:
        headers = {"Country": self.country}
        if store_id:
            headers["Store-Id"] = store_id
        segments = await self.user_segments()
        if segments:
            # Personalized coupons (Dla Ciebie) are omitted without this header.
            headers["Segment-ids"] = ",".join(segments)
        result = await self.request(
            "GET",
            f"{COUPONS_BASE}/v4/promotionslist",
            headers=headers,
        )
        return result if isinstance(result, dict) else {"sections": result or []}

    async def activate_promotion(self, promotion_id: str) -> Any:
        url = f"{COUPONS_BASE}/v2/promotions/{promotion_id}/activation"
        headers = {
            "Country": self.country,
            "Action-Location": ACTION_LOCATION,
        }
        try:
            return await self.request("POST", url, json={}, headers=headers)
        except LidlPlusError as err:
            # Assignable coupons (Dla Ciebie, Kupon Plus, some store offers)
            # reject an empty body and activate with an empty article selection.
            if err.status not in {400, 404, 422}:
                raise
            _LOGGER.debug(
                "Empty activation body failed for %s (%s); retrying",
                promotion_id,
                err,
            )
            return await self.request(
                "POST",
                url,
                json={"articleSelection": []},
                headers=headers,
            )


    async def _common_headers(self) -> dict[str, str]:
        headers = {"Country": self.country}
        segments = await self.user_segments()
        if segments:
            headers["Segment-Ids"] = ",".join(segments)
        return headers

    async def home_config(self) -> dict[str, Any]:
        """Home-screen layouts: `configurations[].id` and their module names."""
        result = await self.request(
            "GET",
            f"{HOME_BASE}/configuration/v1/config/{self.country}",
            headers=await self._common_headers(),
        )
        return result if isinstance(result, dict) else {}

    async def home(
        self, home_id: str, modules: list[str], store_id: str | None
    ) -> dict[str, Any]:
        """The logged-in home screen, one key per requested module."""
        result = await self.request(
            "POST",
            f"{HOME_BASE}/api/v2/{self.country}/home/logged",
            headers={**await self._common_headers(), "homeId": home_id},
            json={
                "storeId": store_id or "",
                "modules": [
                    {"moduleName": name, "aggregateVersion": 1} for name in modules
                ],
            },
        )
        return result if isinstance(result, dict) else {}

    async def deposits(self) -> Any:
        return await self.request(
            "GET",
            f"{DEPOSITS_BASE}/v1/{self.country}/deposits",
            headers={"Country": self.country},
        )

    async def store_schedule(self, store_id: str, day: str) -> Any:
        """Opening hours of a store on `day` (YYYY-MM-DD); ids look like PL2338."""
        if not store_id.upper().startswith(self.country):
            store_id = f"{self.country}{store_id}"
        return await self.request(
            "GET",
            f"{STORES_BASE}/v3/schedule/{store_id}",
            params={"date": day},
            headers={"Country": self.country},
        )

    async def favorite_store(self) -> str | None:
        """Store chosen in the app ("Twój Lidl"), e.g. PL2338; None when not set."""
        result = await self.request(
            "GET",
            f"{STORES_BASE}/v1/users/me/favorite/{self.country}",
            headers={"Country": self.country},
        )
        key = result.get("storeKey") if isinstance(result, dict) else None
        return str(key) if key else None

    async def store_details(self, store_key: str) -> dict[str, Any] | None:
        """Name, address and weekly opening hours of a store."""
        result = await self.request(
            "GET",
            f"{STORES_BASE}/v5/{self.country}/details",
            headers={"Country": self.country, "storesIds": store_key},
        )
        if isinstance(result, list):
            return next(
                (item for item in result if isinstance(item, dict)), None
            )
        return result if isinstance(result, dict) else None

    async def lottery_detail(self, lottery_id: str) -> dict[str, Any] | None:
        """A scratch card with its prize (`prize.type`: Coupon / None …)."""
        result = await self.request(
            "GET", f"{LOTTERY_BASE}/v2/{self.country}/lotteries/{lottery_id}"
        )
        return result if isinstance(result, dict) else None

    async def redeem_lottery(self, lottery_id: str) -> None:
        """Mark a scratch card as scratched, like finishing it in the app."""
        await self.request(
            "PATCH", f"{LOTTERY_BASE}/v2/{self.country}/lotteries/{lottery_id}/redeemed"
        )

    async def shopping_lists(self) -> list[dict[str, Any]]:
        """Lists of the app's shopping list: id, title, eTag, itemsCount."""
        result = await self.request(
            "GET",
            f"{SHOPPING_LIST_BASE}/v4/lists",
            headers={"Country": self.country},
        )
        lists = result.get("lists") if isinstance(result, dict) else None
        return [item for item in lists or [] if isinstance(item, dict)]

    async def sync_shopping_list(
        self,
        list_id: str,
        items: list[dict[str, Any]],
        *,
        item_ids: list[str] | None = None,
        deleted: list[dict[str, str]] | None = None,
        store_id: str | None = None,
    ) -> dict[str, Any]:
        """The app's sync call: send changed items, get the whole list back.

        `item_ids` is the list's full order after the change (the server
        requires it); `deleted` lists removed items as {"itemId", "lastUpdate"}.
        A fresh app sends an empty order and no items to download a list it has
        never seen.
        """
        body: dict[str, Any] = {
            "items": items,
            "list": {"itemIds": item_ids or [], "deletedItems": deleted or []},
        }
        result = await self.request(
            "PATCH",
            f"{SHOPPING_LIST_BASE}/v5/lists/{list_id}",
            json=body,
            # The server requires the store (prices and products are per store).
            headers={"Country": self.country, "Store-Id": store_id or ""},
        )
        return result if isinstance(result, dict) else {}

    async def alerts_unread(self) -> int | None:
        result = await self.request(
            "GET",
            f"{ALERTS_BASE}/v2/{self.country}/alerts/unread",
            headers={"Country": self.country},
        )
        count = result.get("count") if isinstance(result, dict) else result
        return int(count) if isinstance(count, (int, float)) else None


async def fetch_gateway_countries(session: aiohttp.ClientSession) -> Any:
    """Unauthenticated country list used during config flow."""
    try:
        async with session.get(
            COUNTRIES_URL,
            headers={
                "User-Agent": API_USER_AGENT,
                "App-Version": APP_VERSION,
                "Accept": "application/json",
                "isBeta": "false",
            },
            timeout=aiohttp.ClientTimeout(total=10),
        ) as response:
            if response.status >= 400:
                return None
            return await response.json(content_type=None)
    except (TimeoutError, aiohttp.ClientError):
        return None
