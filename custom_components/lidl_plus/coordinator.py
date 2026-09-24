"""DataUpdateCoordinator for Lidl Plus."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .api import LidlPlusApi
from .const import (
    CONF_AUTO_COUPONS,
    CONF_COUNTRY,
    CONF_LANGUAGE,
    CONF_SCAN_INTERVAL,
    DEFAULT_AUTO_COUPONS,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    MAX_RECEIPTS,
    MAX_REWARD_HISTORY,
    STORAGE_VERSION,
)
from .countries import get_country
from .exceptions import LidlPlusAuthError, LidlPlusCannotConnect, LidlPlusError
from .models import (
    Coupon,
    CouponReward,
    LidlPlusData,
    Receipt,
    flatten_promotions,
    slim_ticket_lines,
    store_id_from_payloads,
)

_LOGGER = logging.getLogger(__name__)

# 4xx codes that mean "this coupon cannot be activated by a tap".
_UNACTIVATABLE_HTTP = frozenset({400, 404, 409, 422})


def _http_status(err: BaseException) -> int | None:
    status = getattr(err, "status", None)
    if isinstance(status, int):
        return status
    text = str(err)
    if text.startswith("http_"):
        suffix = text[5:].split()[0]
        try:
            return int(suffix)
        except ValueError:
            return None
    return None


def _parse_dt(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        text = value.replace("Z", "+00:00")
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError:
            return None
    else:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt_util.UTC)
    return parsed


def _as_float(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip().replace(" ", "").replace(",", ".")
    try:
        return float(text)
    except ValueError:
        return None


def _store_name(
    ticket: dict[str, Any], details: dict[str, Any] | None = None
) -> str | None:
    for payload in (details, ticket):
        if not isinstance(payload, dict):
            continue
        store = payload.get("store")
        if isinstance(store, dict) and store.get("name"):
            return str(store["name"])
        vendor = payload.get("vendor")
        if isinstance(vendor, dict) and vendor.get("name"):
            return str(vendor["name"])
        if payload.get("store_name"):
            return str(payload["store_name"])
    return None


def _currency_code(details: dict[str, Any] | None, fallback: str) -> str:
    if isinstance(details, dict):
        currency = details.get("currency")
        if isinstance(currency, dict) and currency.get("code"):
            return str(currency["code"])
        if isinstance(currency, str) and currency:
            return currency
    return fallback


def _ticket_date(ticket: dict[str, Any]) -> Any:
    for key in ("date", "ticketDate", "purchaseDate", "printedDate"):
        if ticket.get(key):
            return ticket[key]
    return None


class LidlPlusCoordinator(DataUpdateCoordinator[LidlPlusData]):
    """Poll loyalty id, tickets and coupons."""

    def __init__(
        self, hass: HomeAssistant, entry: ConfigEntry, api: LidlPlusApi
    ) -> None:
        interval = entry.options.get(CONF_SCAN_INTERVAL)
        update_interval = (
            timedelta(minutes=int(interval)) if interval else DEFAULT_SCAN_INTERVAL
        )
        super().__init__(hass, _LOGGER, name=DOMAIN, update_interval=update_interval)
        self.entry = entry
        self.api = api
        self.country = get_country(entry.data.get(CONF_COUNTRY) or api.country)
        self.language = str(entry.data.get(CONF_LANGUAGE) or api.language)
        try:
            self.zone = ZoneInfo(self.country.time_zone)
        except ZoneInfoNotFoundError:
            self.zone = dt_util.DEFAULT_TIME_ZONE
        self._store: Store[dict[str, Any]] = Store(
            hass, STORAGE_VERSION, f"{DOMAIN}.{entry.entry_id}.coupons"
        )
        self._rewards: dict[str, CouponReward] = {}
        self._skipped: dict[str, dict[str, Any]] = {}
        self._rewards_loaded = False
        self._receipts: dict[str, Receipt] = {}

    @property
    def auto_coupons(self) -> bool:
        return bool(self.entry.options.get(CONF_AUTO_COUPONS, DEFAULT_AUTO_COUPONS))

    @property
    def currency(self) -> str:
        if self.data and self.data.currency:
            return self.data.currency
        return self.country.currency

    async def _async_update_data(self) -> LidlPlusData:
        await self.async_load_rewards()
        try:
            loyalty_id = await self.api.loyalty_id()
            tickets = await self.api.tickets(year_offset=0)
        except LidlPlusAuthError as err:
            raise ConfigEntryAuthFailed from err
        except LidlPlusCannotConnect as err:
            raise UpdateFailed(str(err)) from err
        except LidlPlusError as err:
            raise UpdateFailed(str(err)) from err

        tickets = _sort_tickets(tickets)
        last = tickets[0] if tickets else None
        receipts = await self._sync_receipts(tickets)
        details = (
            receipts[0].payload
            if receipts and isinstance(receipts[0].payload, dict)
            else None
        )
        today_total = await self._today_total(tickets)
        total_spend = self._total_spend(tickets)
        currency = _currency_code(details, self.country.currency)
        store_id = store_id_from_payloads(
            details,
            last,
            *[receipt.payload for receipt in receipts],
            *tickets[:MAX_RECEIPTS],
        )
        coupons = await self._load_coupons(store_id)

        if self.auto_coupons:
            available = [coupon for coupon in coupons if self._is_ready(coupon)]
            if await self._activate_coupons(available, refresh=False):
                coupons = await self._load_coupons()

        data = LidlPlusData(
            loyalty_id=loyalty_id or None,
            tickets=tickets,
            last_ticket=last,
            last_details=details,
            today_total=today_total,
            total_spend=total_spend,
            currency=currency,
            receipts=receipts,
        )
        self._apply_coupons(data, coupons)
        return data

    async def _today_total(self, tickets: list[dict[str, Any]]) -> float:
        today = datetime.now(self.zone).date()
        total = 0.0
        items = list(tickets)
        if today.month == 1 and today.day <= 2:
            try:
                previous = await self.api.tickets(year_offset=1)
            except LidlPlusError:
                previous = []
            items = items + previous
        for ticket in items:
            parsed = _parse_dt(_ticket_date(ticket))
            if parsed is None:
                continue
            if parsed.astimezone(self.zone).date() != today:
                continue
            amount = _as_float(ticket.get("totalAmount") or ticket.get("total_price"))
            if amount is not None:
                total += amount
        return round(total, 2)

    def _total_spend(self, tickets: list[dict[str, Any]]) -> float:
        total = 0.0
        for ticket in tickets:
            amount = _as_float(ticket.get("totalAmount") or ticket.get("total_price"))
            if amount is not None:
                total += amount
        return round(total, 2)

    def _is_ready(self, coupon: Coupon, now: datetime | None = None) -> bool:
        now = now or datetime.now(self.zone)
        return (
            coupon.can_activate()
            and coupon.is_live(now)
            and coupon.coupon_id not in self._skipped
            and coupon.coupon_id not in self._rewards
        )

    def _is_upcoming(self, coupon: Coupon, now: datetime | None = None) -> bool:
        now = now or datetime.now(self.zone)
        return (
            coupon.can_activate()
            and coupon.is_upcoming(now)
            and coupon.coupon_id not in self._skipped
            and coupon.coupon_id not in self._rewards
        )

    def _apply_coupons(self, data: LidlPlusData, coupons: list[Coupon]) -> None:
        live_ids = {coupon.coupon_id for coupon in coupons}
        for coupon_id in list(self._skipped):
            if coupon_id not in live_ids:
                del self._skipped[coupon_id]
        now = datetime.now(self.zone)
        available = [coupon for coupon in coupons if self._is_ready(coupon, now)]
        upcoming = sorted(
            (coupon for coupon in coupons if self._is_upcoming(coupon, now)),
            key=lambda coupon: coupon.starts_at()
            or datetime.max.replace(tzinfo=dt_util.UTC),
        )
        history = list(reversed(self._rewards.values()))
        data.coupons = coupons
        data.available_coupons = available
        data.upcoming_coupons = upcoming
        data.reward_history = history
        data.last_reward = history[0] if history else None

    async def _load_coupons(self, store_id: str | None = None) -> list[Coupon]:
        try:
            payload = await self.api.promotions_list(store_id=store_id)
        except LidlPlusError as err:
            _LOGGER.warning("Promotions list failed: %s", err)
            return []
        coupons: list[Coupon] = []
        seen: set[str] = set()
        for item in flatten_promotions(payload):
            coupon = Coupon.from_payload(item)
            if coupon is None or coupon.coupon_id in seen:
                continue
            seen.add(coupon.coupon_id)
            coupons.append(coupon)
        return coupons

    async def async_load_rewards(self) -> None:
        if self._rewards_loaded:
            return
        self._rewards_loaded = True
        stored = await self._store.async_load()
        entries = stored.get("rewards") if isinstance(stored, dict) else None
        for entry in entries or []:
            if not isinstance(entry, dict):
                continue
            reward = CouponReward.from_dict(entry)
            if reward:
                self._rewards[reward.coupon_id] = reward
        skipped = stored.get("skipped") if isinstance(stored, dict) else None
        for entry in skipped or []:
            if not isinstance(entry, dict) or not entry.get("coupon_id"):
                continue
            self._skipped[str(entry["coupon_id"])] = entry
        self._prune_rewards()

    async def _sync_receipts(self, tickets: list[dict[str, Any]]) -> list[Receipt]:
        wanted = [tx for tx in tickets if isinstance(tx, dict) and tx.get("id")][
            :MAX_RECEIPTS
        ]
        ordered: dict[str, Receipt] = {}
        for tx in wanted:
            tx_id = str(tx["id"])
            cached = self._receipts.get(tx_id)
            if cached and cached.lines:
                ordered[tx_id] = cached
                continue
            receipt = await self._fetch_receipt(tx)
            if receipt is None:
                continue
            ordered[tx_id] = receipt
        self._receipts = ordered
        return list(self._receipts.values())

    async def _fetch_receipt(self, ticket: dict[str, Any]) -> Receipt | None:
        tx_id = str(ticket["id"])
        details: dict[str, Any] = {}
        try:
            payload = await self.api.ticket(tx_id)
            if isinstance(payload, dict):
                details = payload
        except LidlPlusError as err:
            _LOGGER.debug("Ticket %s failed: %s", tx_id, err)
        total = ticket.get("totalAmount")
        if total is None:
            total = details.get("totalAmount")
        receipt = Receipt(
            id=tx_id,
            date=str(_ticket_date(ticket) or _ticket_date(details) or "") or None,
            store_name=_store_name(ticket, details),
            receipt_num=str(
                details.get("sequenceNumber") or ticket.get("sequenceNumber") or ""
            )
            or None,
            total_price=_as_float(total),
            currency=_currency_code(details, self.country.currency),
            lines=slim_ticket_lines(details),
            payload=details or None,
        )
        if not receipt.lines and details:
            html_blob = details.get("htmlPrintedReceipt")
            html_len = len(html_blob) if isinstance(html_blob, str) else 0
            items = details.get("itemsLine")
            _LOGGER.debug(
                "Ticket %s has no lines (ticketType=%s itemsLine=%s html_len=%s keys=%s)",
                tx_id,
                details.get("ticketType"),
                len(items) if isinstance(items, list) else items,
                html_len,
                sorted(details)[:20],
            )
        return receipt

    def _prune_rewards(self) -> None:
        now = datetime.now(self.zone)
        for coupon_id, reward in list(self._rewards.items()):
            end = _parse_dt(reward.valid_to)
            if end and end < now:
                del self._rewards[coupon_id]
        while len(self._rewards) > MAX_REWARD_HISTORY:
            self._rewards.pop(next(iter(self._rewards)))

    async def _save_rewards(self) -> None:
        await self._store.async_save(
            {
                "rewards": [reward.as_dict() for reward in self._rewards.values()],
                "skipped": list(self._skipped.values()),
            }
        )

    async def async_activate_all(self, *, refresh: bool = True) -> list[dict[str, Any]]:
        await self.async_load_rewards()
        coupons = list(self.data.available_coupons) if self.data else []
        if not coupons:
            store_id = None
            if self.data:
                store_id = store_id_from_payloads(
                    self.data.last_details,
                    self.data.last_ticket,
                    *[receipt.payload for receipt in self.data.receipts],
                    *self.data.tickets[:MAX_RECEIPTS],
                )
            coupons = [
                coupon
                for coupon in await self._load_coupons(store_id)
                if self._is_ready(coupon)
            ]
        return await self._activate_coupons(coupons, refresh=refresh)

    async def _activate_coupons(
        self, coupons: list[Coupon], *, refresh: bool
    ) -> list[dict[str, Any]]:
        claimed: list[dict[str, Any]] = []
        skipped = False
        for coupon in coupons:
            if not coupon.coupon_id or not self._is_ready(coupon):
                continue
            try:
                claimed.append(await self.async_activate_coupon(coupon, refresh=False))
            except LidlPlusError as err:
                _LOGGER.warning(
                    "Coupon %s activation failed: %s", coupon.coupon_id, err
                )
                if _http_status(err) in _UNACTIVATABLE_HTTP:
                    self._skipped[coupon.coupon_id] = {
                        "coupon_id": coupon.coupon_id,
                        "reason": str(err),
                        "title": coupon.title,
                    }
                    skipped = True
        if skipped:
            await self._save_rewards()
        if refresh and (claimed or skipped):
            await self.async_request_refresh()
        return claimed

    async def async_activate_coupon(
        self, coupon: Coupon, *, refresh: bool = True
    ) -> dict[str, Any]:
        await self.async_load_rewards()
        await self.api.activate_promotion(coupon.coupon_id)
        reward = CouponReward(
            coupon_id=coupon.coupon_id,
            claimed_at=datetime.now(self.zone).isoformat(timespec="seconds"),
            title=coupon.title,
            discount=coupon.discount,
            valid_from=coupon.valid_from,
            valid_to=coupon.valid_to,
        )
        self._rewards.pop(coupon.coupon_id, None)
        self._rewards[coupon.coupon_id] = reward
        self._prune_rewards()
        await self._save_rewards()
        if refresh:
            await self.async_request_refresh()
        return reward.as_dict()


def _sort_tickets(tickets: list[dict[str, Any]]) -> list[dict[str, Any]]:
    def _key(ticket: dict[str, Any]) -> datetime:
        parsed = _parse_dt(ticket.get("date"))
        return parsed or datetime.min.replace(tzinfo=dt_util.UTC)

    return sorted(tickets, key=_key, reverse=True)
