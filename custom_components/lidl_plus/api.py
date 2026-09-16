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
    API_USER_AGENT,
    APP_PACKAGE,
    APP_VERSION,
    COUNTRIES_URL,
    COUPONS_BASE,
    OPERATING_SYSTEM,
    OS_VERSION,
    PROFILE_BASE,
    SEGMENTS_BASE,
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
    return (fallback[:2] if fallback else "en")


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
        for key in ("segmentIds", "segments", "ids", "data", "items"):
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
                        f"http_{response.status}", status=response.status
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
            headers["Segment-Ids"] = ",".join(segments)
        result = await self.request(
            "GET",
            f"{COUPONS_BASE}/v4/promotionslist",
            headers=headers,
        )
        return result if isinstance(result, dict) else {"sections": result or []}

    async def activate_promotion(self, promotion_id: str) -> Any:
        return await self.request(
            "POST",
            f"{COUPONS_BASE}/v2/promotions/{promotion_id}/activation",
            json={},
            headers={
                "Country": self.country,
                "Action-Location": ACTION_LOCATION,
            },
        )


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
