"""DataUpdateCoordinator for Lidl Plus."""

from __future__ import annotations

import logging
from collections.abc import Callable
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
    CONF_AUTO_SCRATCH,
    CONF_COUNTRY,
    CONF_LANGUAGE,
    CONF_SCAN_INTERVAL,
    CONF_SHOPPING_LISTS,
    DEFAULT_AUTO_COUPONS,
    DEFAULT_AUTO_SCRATCH,
    DEFAULT_SCAN_INTERVAL,
    DEFAULT_SHOPPING_LISTS,
    DOMAIN,
    MAX_RECEIPTS,
    MAX_REWARD_HISTORY,
    STORAGE_VERSION,
)
from .countries import get_country
from .exceptions import LidlPlusAuthError, LidlPlusCannotConnect, LidlPlusError
from .extras import (
    EVENT_KINDS,
    HOME_MODULES,
    KIND_COUPON,
    Deposits,
    ScratchCard,
    ScratchResult,
    ShoppingList,
    StoreSchedule,
    coupon_plus,
    gift_boxes,
    home_message,
    scratch_cards,
    stamp_cards,
)
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
HOME_CONFIG_TTL = timedelta(hours=24)
STORE_TTL = timedelta(hours=6)
MAX_COUPONS_IN_SUMMARY = 5


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
        self.store_id: str | None = None
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
        # The shopping-list option as it was when the entry was set up.
        self.shopping_lists_loaded = self.shopping_lists_enabled
        self._lottery_details: dict[str, Any] = {}
        # Scratch results waiting to be announced by the next refresh.
        self._scratch_results: list[ScratchResult] = []
        self._favorite: str | None = None
        self._favorite_fetched: datetime | None = None
        self._store_details_cache: tuple[str, datetime, Any] | None = None
        self._home_layout: tuple[str, list[str]] | None = None
        self._home_layout_fetched: datetime | None = None
        # Key -> valid_to of things already announced as new.
        self._notified: dict[str, str | None] = {}
        # Categories already tracked; the first refresh of a new one is silent.
        self._notified_kinds: set[str] = set()

    @property
    def auto_coupons(self) -> bool:
        return bool(self.entry.options.get(CONF_AUTO_COUPONS, DEFAULT_AUTO_COUPONS))

    @property
    def shopping_lists_enabled(self) -> bool:
        """Whether the app's shopping lists are synced (integration option)."""
        return bool(
            self.entry.options.get(CONF_SHOPPING_LISTS, DEFAULT_SHOPPING_LISTS)
        )

    @property
    def auto_scratch(self) -> bool:
        return bool(self.entry.options.get(CONF_AUTO_SCRATCH, DEFAULT_AUTO_SCRATCH))

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
        currency = _currency_code(details, self.country.currency)
        receipt_store = store_id_from_payloads(
            details,
            last,
            *[receipt.payload for receipt in receipts],
            *tickets[:MAX_RECEIPTS],
        )
        # The store chosen in the app wins, like the app's own header and
        # coupons; the latest receipt is only a fallback when none is set.
        favorite = await self._favorite_store()
        store_id = favorite or receipt_store
        self.store_id = store_id
        coupons = await self._load_coupons(store_id)

        if self.auto_coupons:
            available = [coupon for coupon in coupons if self._is_ready(coupon)]
            if await self._activate_coupons(available, refresh=False):
                coupons = await self._load_coupons(store_id)

        data = LidlPlusData(
            loyalty_id=loyalty_id or None,
            tickets=tickets,
            last_ticket=last,
            last_details=details,
            today_total=today_total,
            currency=currency,
            receipts=receipts,
        )
        self._apply_coupons(data, coupons)
        await self._apply_home(data, store_id)
        data.deposits = await self._optional(self.api.deposits(), "Deposits")
        data.deposits = Deposits.from_payload(data.deposits)
        data.alerts_unread = await self._optional(self.api.alerts_unread(), "Alerts")
        if self.shopping_lists_enabled:
            data.shopping_lists = await self._load_shopping_lists()
        if store_id:
            schedule = await self._optional(
                self.api.store_schedule(store_id, datetime.now(self.zone).date().isoformat()),
                "Store schedule",
            )
            data.store_schedule = StoreSchedule.from_payload(store_id, schedule)
            if data.store_schedule:
                data.store_schedule.favorite = store_id == favorite
                data.store_schedule.add_details(await self._store_details(store_id))
        # Coupons for other shops and self-scan sessions are listed by the app
        # but never usable here, so they are not announced.
        live_coupons = [
            coupon
            for coupon in coupons
            if not coupon.is_expired(datetime.now(self.zone))
            and not coupon.other_store
            and not coupon.session_only
        ]
        results, self._scratch_results = self._scratch_results, []
        data.new_offers = results + await self._find_new(
            [
                *live_coupons,
                *data.scratch_cards,
                *data.gift_boxes,
                *([data.coupon_plus] if data.coupon_plus else []),
                *data.stamp_cards,
            ]
        )
        return data

    async def _optional(self, call: Any, label: str) -> Any:
        """Extra data the integration can live without."""
        try:
            return await call
        except LidlPlusAuthError:
            raise
        except LidlPlusError as err:
            _LOGGER.debug("%s failed: %s", label, err)
            return None

    async def _load_shopping_lists(self) -> list[ShoppingList]:
        lists = await self._optional(self.api.shopping_lists(), "Shopping lists")
        result = []
        for item in lists or []:
            if not item.get("id"):
                continue
            shopping_list = ShoppingList(str(item["id"]), item.get("title"))
            previous = next(
                (
                    known
                    for known in (self.data.shopping_lists if self.data else [])
                    if known.list_id == shopping_list.list_id and known.loaded
                ),
                None,
            )
            # Send the order we already know so a read never reorders the list;
            # the first read sends none, like a freshly installed app.
            shopping_list.apply(
                await self._optional(
                    self.api.sync_shopping_list(
                        shopping_list.list_id,
                        [],
                        item_ids=previous.item_ids if previous else [],
                        store_id=self.store_id,
                    ),
                    "Shopping list items",
                )
            )
            result.append(shopping_list)
        return result

    async def async_shopping_list_change(
        self,
        shopping_list: ShoppingList,
        items: list[dict[str, Any]],
        *,
        item_ids: list[str] | None = None,
        deleted: list[dict[str, str]] | None = None,
        saved: Callable[[ShoppingList], bool] | None = None,
        retry_items: list[dict[str, Any]] | None = None,
    ) -> None:
        """Send a change made in Home Assistant and make sure the server kept it.

        The sync answer echoes what was sent even when the server drops it, so
        the list is read back. If the change is missing, it is sent once more
        with the server's own `lastUpdate` (`retry_items`).
        """
        attempts = [items] + ([retry_items] if retry_items else [])
        for attempt, payload in enumerate(attempts):
            await self.api.sync_shopping_list(
                shopping_list.list_id,
                payload,
                item_ids=item_ids,
                deleted=deleted,
                store_id=self.store_id,
            )
            shopping_list.apply(
                await self.api.sync_shopping_list(
                    shopping_list.list_id,
                    [],
                    item_ids=item_ids if item_ids is not None else shopping_list.item_ids,
                    store_id=self.store_id,
                )
            )
            self.async_update_listeners()
            if saved is None or saved(shopping_list):
                if attempt:
                    _LOGGER.debug("Shopping list change kept on retry %s", attempt)
                return
        raise LidlPlusError("shopping_list_change_not_saved")

    async def _lottery_detail(self, lottery_id: str) -> Any:
        """The card's prize; fetched once per card, it does not change."""
        if lottery_id not in self._lottery_details:
            detail = await self._optional(
                self.api.lottery_detail(lottery_id), "Scratch card detail"
            )
            if detail is None:
                return None
            self._lottery_details[lottery_id] = detail
        return self._lottery_details[lottery_id]

    async def _scratch(self, cards: list[ScratchCard]) -> list[ScratchResult]:
        """Scratch cards the way the app does when the animation ends."""
        results = []
        for card in cards:
            try:
                await self.api.redeem_lottery(card.card_id)
                results.append(ScratchResult(card))
            except LidlPlusAuthError:
                raise
            except LidlPlusError as err:
                _LOGGER.warning("Scratching card %s failed: %s", card.card_id, err)
                results.append(ScratchResult(card, error=str(err)))
        self._scratch_results.extend(results)
        return results

    async def async_scratch_all(self) -> list[dict[str, Any]]:
        """Scratch every card waiting now (button), then refresh to announce it."""
        cards = list(self.data.scratch_cards) if self.data else []
        results = await self._scratch(cards)
        await self.async_request_refresh()
        return [result.as_dict() for result in results]

    async def _favorite_store(self) -> str | None:
        """The app's chosen store, refreshed every few hours."""
        now = datetime.now(self.zone)
        if self._favorite_fetched and now - self._favorite_fetched < STORE_TTL:
            return self._favorite
        self._favorite = await self._optional(self.api.favorite_store(), "Favorite store")
        self._favorite_fetched = now
        return self._favorite

    async def _store_details(self, store_id: str) -> Any:
        now = datetime.now(self.zone)
        cached = self._store_details_cache
        if cached and cached[0] == store_id and now - cached[1] < STORE_TTL:
            return cached[2]
        details = await self._optional(self.api.store_details(store_id), "Store details")
        self._store_details_cache = (store_id, now, details)
        return details

    async def _apply_home(self, data: LidlPlusData, store_id: str | None) -> None:
        """Scratch cards, gift boxes, Kupon Plus and stamp cards from the home screen."""
        now = datetime.now(self.zone)
        if self._home_layout is None or (
            self._home_layout_fetched and now - self._home_layout_fetched > HOME_CONFIG_TTL
        ):
            config = await self._optional(self.api.home_config(), "Home config")
            layout = _home_layout(config)
            if layout:
                self._home_layout = layout
                self._home_layout_fetched = now
        if self._home_layout is None:
            return
        home_id, available = self._home_layout
        modules = [name for name in HOME_MODULES if name in available]
        if not modules:
            return
        home = await self._optional(self.api.home(home_id, modules, store_id), "Home")
        if not isinstance(home, dict):
            return
        data.scratch_cards = [
            card
            for card in scratch_cards(home)
            if not ((end := _parse_dt(card.expires)) and end <= now)
        ]
        current = {card.card_id for card in data.scratch_cards}
        self._lottery_details = {
            key: value for key, value in self._lottery_details.items() if key in current
        }
        for card in data.scratch_cards:
            card.add_detail(await self._lottery_detail(card.card_id))
        if self.auto_scratch and data.scratch_cards:
            await self._scratch(data.scratch_cards)
            data.scratch_cards = []
        data.gift_boxes = gift_boxes(home)
        data.coupon_plus = coupon_plus(home)
        data.stamp_cards = stamp_cards(home)
        data.home_message = home_message(home)

    async def _find_new(self, items: list[Any]) -> list[Any]:
        """Things not announced before; new coupons are folded into one batch.

        The first refresh of every category only takes note of what is
        already there, so installing or upgrading does not flood you.
        """
        await self.async_load_rewards()
        now = datetime.now(self.zone)
        for key, valid_to in list(self._notified.items()):
            end = _parse_dt(valid_to)
            if end and end < now - timedelta(days=1):
                del self._notified[key]
        new = [item for item in items if item.key not in self._notified]
        silent = set(EVENT_KINDS) - self._notified_kinds
        if not new and not silent:
            return []
        for item in new:
            self._notified[item.key] = item.valid_to
        self._notified_kinds.update(EVENT_KINDS)
        await self._save_rewards()
        announced = [item for item in new if item.category not in silent]
        coupons = [item for item in announced if item.category == KIND_COUPON]
        others = [item for item in announced if item.category != KIND_COUPON]
        return [*([CouponBatch(coupons)] if coupons else []), *others]

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
        """Every coupon the app lists, with the user's store recognised.

        With `Store-Id` the server marks that store's offers FavoriteStore but
        leaves out other shops and self-scan coupons; without it, it lists those
        too but files the user's store under OtherStores. Asking both ways (the
        store's answer first) gives the app's full list with the right sections.
        """
        try:
            payloads = [await self.api.promotions_list(store_id=store_id)]
        except LidlPlusError as err:
            _LOGGER.warning("Promotions list failed: %s", err)
            return []
        if store_id:
            try:
                payloads.append(await self.api.promotions_list(store_id=None))
            except LidlPlusError as err:
                _LOGGER.debug("Promotions list without store failed: %s", err)
        coupons: list[Coupon] = []
        seen: set[str] = set()
        for payload in payloads:
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
        notified = stored.get("notified") if isinstance(stored, dict) else None
        if isinstance(notified, dict):
            self._notified = {str(key): value for key, value in notified.items()}
        kinds = stored.get("notified_kinds") if isinstance(stored, dict) else None
        if isinstance(kinds, list):
            self._notified_kinds = {str(kind) for kind in kinds}
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
                "notified": self._notified,
                "notified_kinds": sorted(self._notified_kinds),
            }
        )

    async def async_activate_all(self, *, refresh: bool = True) -> list[dict[str, Any]]:
        await self.async_load_rewards()
        coupons = list(self.data.available_coupons) if self.data else []
        if not coupons:
            # Same store as the last refresh: the app's choice, else the receipts'.
            store_id = self.store_id
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


def _home_layout(config: Any) -> tuple[str, list[str]] | None:
    """(home id, module names) of the main home screen from the home config."""
    configurations = config.get("configurations") if isinstance(config, dict) else None
    for configuration in configurations or []:
        if not isinstance(configuration, dict) or not configuration.get("id"):
            continue
        names = [
            module if isinstance(module, str) else module.get("moduleName")
            for module in configuration.get("modules") or []
            if isinstance(module, (str, dict))
        ]
        return str(configuration["id"]), [name for name in names if name]
    return None


class CouponBatch:
    """All coupons that appeared in one refresh, announced as a single event."""

    category = KIND_COUPON

    def __init__(self, coupons: list[Coupon]) -> None:
        self.coupons = coupons

    @property
    def summary(self) -> str:
        shown = [
            f"{coupon.title} {coupon.discount}".strip() if coupon.discount else coupon.title
            for coupon in self.coupons[:MAX_COUPONS_IN_SUMMARY]
            if coupon.title
        ]
        more = len(self.coupons) - len(shown)
        text = f"Nowe kupony Lidl Plus ({len(self.coupons)}): " + ", ".join(shown)
        if more > 0:
            text += f" i {more} więcej"
        return text + "."

    def as_dict(self) -> dict[str, Any]:
        return {
            "count": len(self.coupons),
            "summary": self.summary if len(self.coupons) > 1 else self.coupons[0].summary,
            "image_url": next(
                (coupon.image_url for coupon in self.coupons if coupon.image_url), None
            ),
            "coupons": [coupon.as_dict() for coupon in self.coupons],
        }


def _sort_tickets(tickets: list[dict[str, Any]]) -> list[dict[str, Any]]:
    def _key(ticket: dict[str, Any]) -> datetime:
        parsed = _parse_dt(ticket.get("date"))
        return parsed or datetime.min.replace(tzinfo=dt_util.UTC)

    return sorted(tickets, key=_key, reverse=True)
