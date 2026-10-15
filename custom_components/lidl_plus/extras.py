"""Home-screen rewards (scratch cards, gifts, Kupon Plus, stamp cards) and deposits.

Shapes follow the live `home/logged` answer and the app's models (17.11.6).
Modules the user has nothing in come back as `error_<module>-1` with HTTP 204,
so every parser accepts a missing module and returns an empty result.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

KIND_COUPON = "coupon"
KIND_SCRATCH_CARD = "scratch_card"
KIND_GIFT = "gift"
KIND_COUPON_PLUS = "coupon_plus"
KIND_STAMP_CARD = "stamp_card"
KIND_SCRATCH_RESULT = "scratch_result"
# Every kind that gets a "new ..." event entity.
EVENT_KINDS = (
    KIND_COUPON,
    KIND_SCRATCH_CARD,
    KIND_GIFT,
    KIND_COUPON_PLUS,
    KIND_STAMP_CARD,
    KIND_SCRATCH_RESULT,
)
# Prize types of a scratch card that mean "nothing won".
NO_PRIZE = frozenset({"none", ""})

# Home modules the integration reads. The app asks for them by name.
HOME_MODULES = (
    "purchaseLottery",
    "openGift",
    "couponPlus",
    "stampCardRewards",
    "stampCardBenefits",
    "homeMessage",
)

_WARSAW = ZoneInfo("Europe/Warsaw")
_CARD_NAME = re.compile(r"\b(kart(?:ę|ą|a|y|ie)) (?:Lidl Plus|Moja Lidl Plus)\b")


def _text(value: Any) -> str | None:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return str(value)
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def _number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value.replace(",", ".").strip())
        except ValueError:
            return None
    if isinstance(value, dict):
        for key in ("value", "amount"):
            if key in value:
                return _number(value[key])
    return None


def _local(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=_WARSAW)
    return parsed.astimezone(_WARSAW)


def _until(value: str | None, *, with_time: bool = True) -> str:
    end = _local(value)
    if not end:
        return ""
    return f"{end:%d.%m %H:%M}" if with_time else f"{end:%d.%m}"


def _pln(value: float | None) -> str | None:
    if value is None:
        return None
    return f"{value:.2f}".replace(".", ",") + " zł"


def _translated_title(item: dict[str, Any]) -> str | None:
    translations = item.get("translations")
    if isinstance(translations, dict):
        for key in ("title", "name", "description"):
            title = translations.get(key)
            if isinstance(title, dict):
                title = title.get("text") or title.get("value")
            if _text(title):
                return _text(title)
    return _text(item.get("title"))


def module(home: Any, name: str) -> Any:
    """A module from the home answer; None when absent or reported as an error."""
    if not isinstance(home, dict):
        return None
    return home.get(name)


@dataclass
class ScratchCard:
    """A scratch card (zdrapka) given after a purchase."""

    card_id: str
    promotion_id: str | None = None
    promotion_code: str | None = None
    card_type: str | None = None
    created: str | None = None
    expires: str | None = None
    image_url: str | None = None
    # Known before scratching: the app draws the prize when the card is made.
    prize_type: str | None = None
    prize_text: str | None = None

    category = KIND_SCRATCH_CARD

    @property
    def wins(self) -> bool | None:
        if self.prize_type is None:
            return None
        return self.prize_type.strip().lower() not in NO_PRIZE

    def add_detail(self, detail: Any) -> None:
        if not isinstance(detail, dict):
            return
        prize = detail.get("prize") if isinstance(detail.get("prize"), dict) else {}
        promotion = (
            detail.get("promotion") if isinstance(detail.get("promotion"), dict) else {}
        )
        self.prize_type = _text(prize.get("type")) or "None"
        self.prize_text = _text(promotion.get("text"))

    @property
    def key(self) -> str:
        return f"scratch_{self.card_id}"

    @property
    def valid_to(self) -> str | None:
        return self.expires

    @property
    def summary(self) -> str:
        until = _until(self.expires)
        return "Nowa zdrapka Lidl Plus" + (f" — odkryj do {until}." if until else ".")

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.card_id,
            "promotion_id": self.promotion_id,
            "promotion_code": self.promotion_code,
            "type": self.card_type,
            "created": self.created,
            "expires": self.expires,
            "image_url": self.image_url,
            "prize_type": self.prize_type,
            "wins": self.wins,
            "prize_text": self.prize_text,
            "summary": self.summary,
        }


@dataclass
class ScratchResult:
    """What a scratch card gave when the integration scratched it."""

    card: ScratchCard
    error: str | None = None

    category = KIND_SCRATCH_RESULT

    @property
    def summary(self) -> str:
        if self.error:
            return "Nie udało się odkryć zdrapki Lidl Plus — odkryj ją w aplikacji."
        if self.card.wins:
            prize = "kupon" if (self.card.prize_type or "").lower() == "coupon" else self.card.prize_type
            return f"Zdrapka odkryta: wygrana — {prize}. Znajdziesz go w kuponach Lidl Plus."
        return "Zdrapka odkryta: tym razem bez nagrody."

    def as_dict(self) -> dict[str, Any]:
        return {
            **self.card.as_dict(),
            "error": self.error,
            "summary": self.summary,
        }


def scratch_cards(home: Any) -> list[ScratchCard]:
    items = module(home, "purchaseLottery")
    if isinstance(items, dict):
        items = [items]
    cards: list[ScratchCard] = []
    for item in items if isinstance(items, list) else []:
        if not isinstance(item, dict) or not item.get("id"):
            continue
        cards.append(
            ScratchCard(
                card_id=str(item["id"]),
                promotion_id=_text(item.get("promotionId")),
                promotion_code=_text(item.get("promotionCode")),
                card_type=_text(item.get("type")),
                created=_text(item.get("creationDate")),
                expires=_text(item.get("expirationDate")),
                image_url=_text(item.get("background")),
            )
        )
    return cards


@dataclass
class GiftBox:
    """A box to open in the "Open Gift" campaign (secret box or daily deal)."""

    box_id: str
    campaign_id: str | None = None
    campaign_type: str | None = None
    title: str | None = None
    available_from: str | None = None
    available_to: str | None = None
    image_url: str | None = None

    category = KIND_GIFT

    @property
    def key(self) -> str:
        return f"gift_{self.campaign_id}_{self.box_id}"

    @property
    def valid_to(self) -> str | None:
        return self.available_to

    @property
    def summary(self) -> str:
        name = self.title or (
            "Sekretne pudełko"
            if (self.campaign_type or "").lower().startswith("secret")
            else "Prezent dnia"
        )
        until = _until(self.available_to)
        return f"{name} do otwarcia w Lidl Plus" + (f" — do {until}." if until else ".")

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.box_id,
            "campaign_id": self.campaign_id,
            "type": self.campaign_type,
            "title": self.title,
            "available_from": self.available_from,
            "available_to": self.available_to,
            "image_url": self.image_url,
            "summary": self.summary,
        }


def gift_boxes(home: Any) -> list[GiftBox]:
    gift = module(home, "openGift")
    if not isinstance(gift, dict):
        return []
    detail = gift.get("detailInformation") if isinstance(gift.get("detailInformation"), dict) else {}
    campaign_id = _text(detail.get("id")) or _text(gift.get("id"))
    title = _translated_title(gift)
    boxes: list[GiftBox] = []
    for box in gift.get("activeBoxes") or gift.get("boxes") or []:
        if not isinstance(box, dict) or not box.get("id"):
            continue
        logo = box.get("logo")
        boxes.append(
            GiftBox(
                box_id=str(box["id"]),
                campaign_id=campaign_id,
                campaign_type=_text(gift.get("type")),
                title=title,
                available_from=_text(box.get("availableDate")),
                available_to=_text(box.get("lastAvailableDate")),
                image_url=_text(logo.get("url") if isinstance(logo, dict) else logo)
                or _text(detail.get("backgroundImage")),
            )
        )
    return boxes


@dataclass
class CouponPlus:
    """Kupon Plus: spend a target amount in the period to unlock a reward."""

    plus_id: str
    status: str | None = None
    reached: float | None = None
    target: float | None = None
    reward: str | None = None
    ends: str | None = None
    goals: list[dict[str, Any]] = field(default_factory=list)

    category = KIND_COUPON_PLUS

    @property
    def key(self) -> str:
        return f"couponplus_{self.plus_id}"

    @property
    def valid_to(self) -> str | None:
        return self.ends

    @property
    def missing(self) -> float | None:
        if self.reached is None or self.target is None:
            return None
        return max(round(self.target - self.reached, 2), 0.0)

    @property
    def summary(self) -> str:
        parts = ["Kupon Plus"]
        if self.reached is not None and self.target is not None:
            parts.append(f": {_pln(self.reached)} z {_pln(self.target)}")
            if self.missing:
                parts.append(f" (brakuje {_pln(self.missing)})")
        if self.reward:
            parts.append(f". Nagroda: {self.reward}")
        until = _until(self.ends, with_time=False)
        if until:
            parts.append(f". Do {until}")
        return "".join(parts) + "."

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.plus_id,
            "status": self.status,
            "reached": self.reached,
            "target": self.target,
            "missing": self.missing,
            "reward": self.reward,
            "ends": self.ends,
            "goals": self.goals,
            "summary": self.summary,
        }


def coupon_plus(home: Any) -> CouponPlus | None:
    data = module(home, "couponPlus")
    if isinstance(data, dict) and isinstance(data.get("couponPlus"), dict):
        data = data["couponPlus"]
    if not isinstance(data, dict) or not data:
        return None
    goals = [goal for goal in data.get("goals") or [] if isinstance(goal, dict)]
    target = _number(data.get("amount"))
    reward = None
    if goals:
        last = goals[-1]
        target = target if target is not None else _number(last.get("amount"))
        reward = _text(last.get("title")) or _text(last.get("discount"))
    return CouponPlus(
        plus_id=_text(data.get("id")) or _text(data.get("promotionId")) or "current",
        status=_text(data.get("status")),
        reached=_number(data.get("reachedAmount")),
        target=target,
        reward=reward or _text(data.get("title")),
        ends=_text(data.get("endDate")),
        goals=goals,
    )


@dataclass
class StampCard:
    """A stamp card (karta z naklejkami): collect units for a prize."""

    promotion_id: str
    title: str | None = None
    status: str | None = None
    units: float | None = None
    units_per_prize: float | None = None
    completed: int | None = None
    ends: str | None = None
    image_url: str | None = None

    category = KIND_STAMP_CARD

    @property
    def key(self) -> str:
        return f"stampcard_{self.promotion_id}"

    @property
    def valid_to(self) -> str | None:
        return self.ends

    @property
    def summary(self) -> str:
        parts = [f"Karta z naklejkami: {self.title or 'Lidl Plus'}"]
        if self.units is not None and self.units_per_prize:
            parts.append(f" — {self.units:g}/{self.units_per_prize:g}")
        until = _until(self.ends, with_time=False)
        if until:
            parts.append(f", do {until}")
        return "".join(parts) + "."

    def as_dict(self) -> dict[str, Any]:
        return {
            "promotion_id": self.promotion_id,
            "title": self.title,
            "status": self.status,
            "units": self.units,
            "units_per_prize": self.units_per_prize,
            "completed": self.completed,
            "ends": self.ends,
            "image_url": self.image_url,
            "summary": self.summary,
        }


def stamp_cards(home: Any) -> list[StampCard]:
    data = module(home, "stampCardRewards")
    items = data if isinstance(data, list) else [data] if isinstance(data, dict) else []
    cards: list[StampCard] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        promotion = item.get("userPromotion") if isinstance(item.get("userPromotion"), dict) else {}
        config = item.get("configuration") if isinstance(item.get("configuration"), dict) else {}
        promotion_id = _text(item.get("promotionId")) or _text(promotion.get("promotionId"))
        if not promotion_id:
            continue
        completed = promotion.get("completedCards", item.get("completedCards"))
        cards.append(
            StampCard(
                promotion_id=promotion_id,
                title=_text(item.get("title")) or _text(config.get("title")),
                status=_text(item.get("status")),
                units=_number(promotion.get("unitsAchieved", item.get("unitsAchieved"))),
                units_per_prize=_number(
                    config.get("unitsPerPrize", item.get("unitsPerPrize"))
                ),
                completed=int(completed) if isinstance(completed, (int, float)) else None,
                ends=_text(item.get("endDate")) or _text(config.get("endDate")),
                image_url=_text(item.get("imageUrl")),
            )
        )
    return cards


def home_message(home: Any) -> str | None:
    data = module(home, "homeMessage")
    return _text(data.get("text")) if isinstance(data, dict) else None


@dataclass
class StoreSchedule:
    """Today's opening hours of the user's store, plus its address and week."""

    store_id: str
    hours: list[tuple[str, str]] = field(default_factory=list)
    favorite: bool = False
    name: str | None = None
    address: str | None = None
    postal_code: str | None = None
    locality: str | None = None
    week: list[dict[str, Any]] = field(default_factory=list)
    is_open: bool | None = None
    temporarily_closed: bool = False
    permanently_closed: bool = False

    @property
    def full_address(self) -> str | None:
        """'Szczecinek, ul. Koszalińska 87' — like the app's header."""
        parts = [part for part in (self.locality, self.address) if part]
        return ", ".join(parts) or None

    def add_details(self, details: Any) -> None:
        if not isinstance(details, dict):
            return
        self.name = _text(details.get("name"))
        self.address = _text(details.get("address"))
        self.postal_code = _text(details.get("postalCode"))
        self.locality = _text(details.get("locality"))
        self.week = week_hours(details.get("weekDays"))

    @property
    def today(self) -> str | None:
        """'06:00–22:00', or None when the store is closed all day."""
        if not self.hours:
            return None
        return ", ".join(f"{start}–{end}" for start, end in self.hours)

    def open_at(self, now: datetime) -> bool:
        """Open right now, from today's hours (the API flag is only as fresh as the poll)."""
        if self.temporarily_closed or self.permanently_closed:
            return False
        if not self.hours:
            return bool(self.is_open)
        clock = now.astimezone(_WARSAW).strftime("%H:%M")
        return any(start <= clock < end for start, end in self.hours)

    @classmethod
    def from_payload(cls, store_id: str, payload: Any) -> StoreSchedule | None:
        if not isinstance(payload, dict):
            return None
        hours = [
            (str(slot["from"]), str(slot["to"]))
            for slot in payload.get("openingHours") or []
            if isinstance(slot, dict) and slot.get("from") and slot.get("to")
        ]
        is_open = payload.get("isOpen")
        return cls(
            store_id=store_id,
            hours=hours,
            is_open=is_open if isinstance(is_open, bool) else None,
            temporarily_closed=payload.get("isTemporarilyClosed") is True,
            permanently_closed=payload.get("isPermanentlyClosed") is True,
        )


def week_hours(days: Any) -> list[dict[str, Any]]:
    """Weekly hours as [{'day': ..., 'hours': '06:00–22:00'}]; tolerant of key names."""
    week: list[dict[str, Any]] = []
    for item in days if isinstance(days, list) else []:
        if not isinstance(item, dict):
            continue
        day = next(
            (item[key] for key in ("day", "dayOfWeek", "weekDay", "name") if item.get(key) is not None),
            None,
        )
        slots = next(
            (item[key] for key in ("openingHours", "hours", "schedule", "timeRanges") if isinstance(item.get(key), list)),
            [item] if item.get("from") else [],
        )
        ranges = [
            f"{slot['from']}–{slot['to']}"
            for slot in slots
            if isinstance(slot, dict) and slot.get("from") and slot.get("to")
        ]
        week.append({"day": day, "hours": ", ".join(ranges) or None})
    return week


@dataclass
class ShoppingList:
    """One list from the app's shopping list, with its items as the app keeps them."""

    list_id: str
    title: str | None = None
    items: list[dict[str, Any]] = field(default_factory=list)
    # False until the items were read; writing then could drop unseen items.
    loaded: bool = False

    @property
    def item_ids(self) -> list[str]:
        return [str(item["id"]) for item in self.items if item.get("id")]

    def apply(self, payload: Any) -> None:
        """Take the items (in the list's order) from a sync answer."""
        if not isinstance(payload, dict):
            return
        self.loaded = True
        items = [item for item in payload.get("items") or [] if isinstance(item, dict)]
        order = (payload.get("list") or {}).get("itemIds") if isinstance(payload.get("list"), dict) else None
        if isinstance(order, list) and order:
            position = {str(item_id): index for index, item_id in enumerate(order)}
            items.sort(key=lambda item: position.get(str(item.get("id")), len(position)))
        self.items = items


@dataclass
class Deposits:
    """Bottle-deposit refunds (system kaucyjny) kept in the app."""

    available: float = 0.0
    pending: float = 0.0
    automatic: bool | None = None
    items: list[dict[str, Any]] = field(default_factory=list)

    @classmethod
    def from_payload(cls, payload: Any) -> Deposits | None:
        if not isinstance(payload, dict):
            return None
        auto = payload.get("isAutomaticRedemption")
        return cls(
            available=_number(payload.get("availableAmount")) or 0.0,
            pending=_number(payload.get("pendingAmount")) or 0.0,
            automatic=auto if isinstance(auto, bool) else None,
            items=[d for d in payload.get("deposits") or [] if isinstance(d, dict)],
        )


def coupon_summary(title: str | None, discount: str | None, valid_to: str | None) -> str:
    """'Donut Pinky LUB Serek: -50%. Ważny do 17.10.' for a coupon notification."""
    line = title or "Nowy kupon Lidl Plus"
    if discount:
        line = f"{line}: {discount.strip()}"
    parts = [line if line.endswith((".", "!", "?")) else f"{line}."]
    until = _until(valid_to, with_time=False)
    if until:
        parts.append(f"Ważny do {until}.")
    return _CARD_NAME.sub(r"\1", " ".join(parts))
