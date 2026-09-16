"""Dataclasses for coordinator payloads."""

from __future__ import annotations

import base64
import gzip
import html
import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from html.parser import HTMLParser
from typing import Any


def _pick(data: dict[str, Any], keys: tuple[str, ...]) -> dict[str, Any]:
    return {key: data[key] for key in keys if key in data}


_TICKET_LINE_KEYS = (
    "name",
    "codeInput",
    "quantity",
    "currentUnitPrice",
    "originalAmount",
    "isWeight",
)


def slim_ticket_lines(payload: Any) -> list[dict[str, Any]]:
    """Keep ticket line fields from native JSON or htmlPrintedReceipt."""
    if isinstance(payload, list):
        return _slim_native_lines(payload)
    if isinstance(payload, str):
        return lines_from_html_receipt(payload)
    if not isinstance(payload, dict):
        return []
    native = _slim_native_lines(
        _dict_get(payload, "itemsLine", "itemLines", "ticketLines", "itemLine")
    )
    if native:
        return native
    html_receipt = _find_html_printed_receipt(payload)
    if html_receipt:
        parsed = lines_from_html_receipt(html_receipt)
        if parsed:
            return parsed
    walked = _walk_payload_lines(payload)
    if walked:
        return walked
    return _slim_hga_lines(_dict_get(payload, "items"))


def _dict_get(payload: dict[str, Any], *names: str) -> Any:
    lower = {str(key).lower(): value for key, value in payload.items()}
    for name in names:
        if name.lower() in lower:
            return lower[name.lower()]
    return None


def _item_get(item: dict[str, Any], *names: str) -> Any:
    lower = {str(key).lower(): value for key, value in item.items()}
    for name in names:
        if name.lower() in lower:
            return lower[name.lower()]
    return None


def _loads_maybe_json(value: str) -> Any:
    text = value.strip()
    if not text or text[0] not in "{[":
        return value
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return value


def _slim_native_lines(items: Any) -> list[dict[str, Any]]:
    if isinstance(items, str):
        items = _loads_maybe_json(items)
    if isinstance(items, dict):
        for key in ("$values", "itemsLine", "items", "lines", "value"):
            nested = _item_get(items, key)
            if isinstance(nested, list):
                items = nested
                break
        else:
            if _item_get(items, "name", "description", "articleName", "codeInput"):
                items = [items]
            else:
                return []
    if isinstance(items, list) and items and isinstance(items[0], str):
        joined = "".join(part for part in items if isinstance(part, str))
        return lines_from_html_receipt(joined) if joined else []
    if not isinstance(items, list):
        return []
    slimmed: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        name = _item_get(item, "name", "description", "articleName", "productName")
        code = _item_get(item, "codeInput", "code", "ean", "articleId")
        if not (name or code):
            continue
        picked = _pick(item, _TICKET_LINE_KEYS)
        if name and "name" not in picked:
            picked["name"] = name
        if code and "codeInput" not in picked:
            picked["codeInput"] = code
        quantity = _item_get(item, "quantity")
        if quantity is not None and "quantity" not in picked:
            picked["quantity"] = quantity
        unit = _item_get(item, "currentUnitPrice")
        if unit is not None and "currentUnitPrice" not in picked:
            picked["currentUnitPrice"] = unit
        amount = _item_get(item, "originalAmount")
        if amount is not None and "originalAmount" not in picked:
            picked["originalAmount"] = amount
        weight = _item_get(item, "isWeight")
        if weight is not None and "isWeight" not in picked:
            picked["isWeight"] = weight
        if picked:
            slimmed.append(picked)
    return slimmed


def _walk_payload_lines(payload: Any, *, depth: int = 0) -> list[dict[str, Any]]:
    if depth > 6:
        return []
    if isinstance(payload, str):
        if "data-art-id" in payload or "purchase_list_line_" in payload.lower():
            return lines_from_html_receipt(payload)
        return []
    if isinstance(payload, list):
        if payload and isinstance(payload[0], dict):
            sample = payload[0]
            if _item_get(sample, "currentUnitPrice", "codeInput", "originalAmount"):
                native = _slim_native_lines(payload)
                if native:
                    return native
        for item in payload:
            found = _walk_payload_lines(item, depth=depth + 1)
            if found:
                return found
        return []
    if not isinstance(payload, dict):
        return []
    for value in payload.values():
        found = _walk_payload_lines(value, depth=depth + 1)
        if found:
            return found
    return []


def _slim_hga_lines(items: Any) -> list[dict[str, Any]]:
    if not isinstance(items, list):
        return []
    slimmed: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        name = item.get("description") or item.get("name")
        if not name:
            continue
        row: dict[str, Any] = {"name": str(name)}
        if item.get("quantity") is not None:
            row["quantity"] = item["quantity"]
        amount = item.get("grossAmount")
        if amount is not None:
            row["originalAmount"] = str(amount)
        slimmed.append(row)
    return slimmed


def _html_printed_receipt(payload: dict[str, Any]) -> str | None:
    raw = _dict_get(
        payload,
        "htmlPrintedReceipt",
        "html_printed_receipt",
        "printedHtml",
        "printedReceiptHtml",
    )
    if isinstance(raw, str) and raw.strip():
        return raw
    if isinstance(raw, list):
        joined = "".join(part for part in raw if isinstance(part, str))
        return joined or None
    return None


def _find_html_printed_receipt(payload: dict[str, Any], *, depth: int = 0) -> str | None:
    found = _html_printed_receipt(payload)
    if found:
        return found
    if depth >= 3:
        return None
    for key in ("data", "ticket", "result", "payload"):
        nested = payload.get(key)
        if isinstance(nested, dict):
            found = _find_html_printed_receipt(nested, depth=depth + 1)
            if found:
                return found
    return None


_MONEY_TEXT = re.compile(r"^\d+(?:[.,]\d+)?$")
_TAX_LETTER = re.compile(r"^[A-Z]\d{0,2}$")
_OPEN_TAG = re.compile(r"<([a-zA-Z][\w:-]*)(\s[^<>]*?)?/?>", re.DOTALL)
_SPAN_ATTR = re.compile(r"""([\w-]+)\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s"'<>/]+))""")
_PRE_LINE = re.compile(
    r"^(?P<name>.+?)\s{2,}(?P<amount>\d+[.,]\d{2})(?:\s+(?P<tax>[A-Z]\d{0,2}))?\s*$"
)
_SKIP_PRE_NAME = re.compile(
    r"^(suma|razem|gotówka|gotowka|karta|reszta|ptu|sprzedaż|sprzedaz|nip|"
    r"paragon|lidl|operator|kasa|nr\b|total|summe|cash|card|zwrot|rabat|"
    r"dziękujemy|dziekujemy)",
    re.IGNORECASE,
)


class _ArticleSpanParser(HTMLParser):
    """Collect article spans, merging fragments that share the same HTML id."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.groups: dict[str, dict[str, Any]] = {}
        self.order: list[str] = []
        self._anon = 0
        self._current: str | None = None
        self._span_is_article: list[bool] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        del tag
        parsed = {key.lower(): (value or "") for key, value in attrs}
        classes = parsed.get("class", "").lower().split()
        html_id = parsed.get("id", "")
        is_article = (
            "article" in classes
            or bool(parsed.get("data-art-id"))
            or html_id.lower().startswith("purchase_list_line_")
        )
        self._span_is_article.append(is_article)
        if not is_article:
            return
        key = html_id or f"anon-{self._anon}"
        if not html_id:
            self._anon += 1
        group = self.groups.get(key)
        if group is None:
            group = {"attrs": {}, "text": []}
            self.groups[key] = group
            self.order.append(key)
        stored = group["attrs"]
        for name, value in parsed.items():
            if value:
                stored[name] = value
        self._current = key

    def handle_endtag(self, tag: str) -> None:
        if not self._span_is_article:
            return
        if self._span_is_article.pop() and self._current is not None:
            self._current = None

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)
        self.handle_endtag(tag)

    def handle_data(self, data: str) -> None:
        if self._current is None:
            return
        text = data.strip()
        if text:
            self.groups[self._current]["text"].append(text)


def lines_from_html_receipt(markup: str) -> list[dict[str, Any]]:
    """Parse article spans from Lidl htmlPrintedReceipt."""
    blob = _decode_html_blob(markup)
    lines = _rows_from_article_groups(_article_groups_from_parser(blob))
    if lines:
        return lines
    lines = _rows_from_article_groups(_article_groups_from_regex(blob))
    if lines:
        return lines
    return _lines_from_preformatted_receipt(blob)


def _decode_html_blob(markup: str) -> str:
    text = markup.strip()
    if not text:
        return text
    decoded = _decode_binary_html(text)
    if decoded != text:
        text = decoded
    for _ in range(2):
        if re.search(r"<[a-zA-Z]", text):
            return text
        unescaped = html.unescape(text)
        if unescaped == text:
            return text
        text = unescaped
    return text


def _decode_binary_html(text: str) -> str:
    compact = "".join(text.split())
    if len(compact) < 80 or not re.fullmatch(r"[A-Za-z0-9+/]+=*", compact):
        return text
    try:
        raw = base64.b64decode(compact, validate=True)
    except ValueError:
        return text
    if raw[:2] == b"\x1f\x8b":
        try:
            raw = gzip.decompress(raw)
        except OSError:
            return text
    try:
        decoded = raw.decode("utf-8")
    except UnicodeDecodeError:
        return text
    if "<" in decoded:
        return decoded
    return text


def _article_groups_from_parser(markup: str) -> list[dict[str, Any]]:
    parser = _ArticleSpanParser()
    parser.feed(markup)
    parser.close()
    return [parser.groups[key] for key in parser.order]


def _article_groups_from_regex(markup: str) -> list[dict[str, Any]]:
    groups: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    anon = 0
    for match in _OPEN_TAG.finditer(markup):
        raw_attrs = match.group(2) or ""
        attrs = {
            name.lower(): html.unescape(quoted or single or unquoted or "")
            for name, quoted, single, unquoted in _SPAN_ATTR.findall(raw_attrs)
        }
        classes = attrs.get("class", "").lower().split()
        html_id = attrs.get("id", "")
        is_article = (
            "article" in classes
            or bool(attrs.get("data-art-id"))
            or html_id.lower().startswith("purchase_list_line_")
        )
        if not is_article:
            continue
        key = html_id or f"anon-{anon}"
        if not html_id:
            anon += 1
        group = groups.get(key)
        if group is None:
            group = {"attrs": {}, "text": []}
            groups[key] = group
            order.append(key)
        stored = group["attrs"]
        for name, value in attrs.items():
            if value:
                stored[name] = value
    return [groups[key] for key in order]


def _rows_from_article_groups(groups: list[dict[str, Any]]) -> list[dict[str, Any]]:
    lines: list[dict[str, Any]] = []
    for group in groups:
        attrs: dict[str, str] = group["attrs"]
        texts: list[str] = group["text"]
        name = (attrs.get("data-art-description") or "").strip()
        if not name:
            name = _name_from_article_texts(texts)
        quantity = (attrs.get("data-art-quantity") or "1").strip() or "1"
        unit = (attrs.get("data-unit-price") or "").strip()
        code = (attrs.get("data-art-id") or "").strip()
        qty_num = _as_float_text(quantity)
        is_weight = qty_num is not None and qty_num != int(qty_num)
        original = _html_line_total(texts, quantity, unit)
        row: dict[str, Any] = {}
        if name:
            row["name"] = name
        if code:
            row["codeInput"] = code
        row["quantity"] = quantity
        if unit:
            row["currentUnitPrice"] = unit
        if original:
            row["originalAmount"] = original
        row["isWeight"] = is_weight
        if name or code:
            lines.append(row)
    return lines


def _name_from_article_texts(texts: list[str]) -> str:
    for text in texts:
        if _MONEY_TEXT.fullmatch(text) or _TAX_LETTER.fullmatch(text):
            continue
        if text:
            return text
    return ""


def _html_line_total(texts: list[str], quantity: str, unit: str) -> str | None:
    qty = _as_float_text(quantity)
    price = _as_float_text(unit)
    if qty is not None and price is not None:
        return f"{qty * price:.2f}"
    for text in reversed(texts):
        parsed = _as_float_text(text) if _MONEY_TEXT.fullmatch(text) else None
        if parsed is not None:
            return f"{parsed:.2f}"
    return None


def _lines_from_preformatted_receipt(markup: str) -> list[dict[str, Any]]:
    text = re.sub(r"(?is)<style.*?>.*?</style>", " ", markup)
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"</p>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text)
    lines: list[dict[str, Any]] = []
    for raw in text.splitlines():
        row_text = " ".join(raw.split())
        match = _PRE_LINE.match(raw.rstrip()) or _PRE_LINE.match(row_text)
        if not match:
            continue
        name = match.group("name").strip(" .")
        if len(name) < 3 or _SKIP_PRE_NAME.match(name):
            continue
        amount = match.group("amount")
        lines.append(
            {
                "name": name,
                "quantity": "1",
                "originalAmount": amount.replace(",", "."),
                "isWeight": False,
            }
        )
    return lines


def _as_float_text(value: str) -> float | None:
    text = value.strip().replace(" ", "").replace(",", ".")
    try:
        return float(text)
    except ValueError:
        return None


def _as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes"}
    return False


def store_id_from_payloads(*payloads: Any) -> str | None:
    """Pick a Lidl store id from ticket list/detail payloads."""
    for payload in payloads:
        if not isinstance(payload, dict):
            continue
        for key in ("storeID", "storeId", "store_id"):
            value = payload.get(key)
            if value:
                return str(value)
        store = payload.get("store")
        if isinstance(store, dict):
            for key in ("id", "storeId", "storeKey"):
                value = store.get(key)
                if value:
                    return str(value)
        vendor = payload.get("vendor")
        if isinstance(vendor, dict):
            for key in ("id", "storeId", "storeKey"):
                value = vendor.get(key)
                if value:
                    return str(value)
    return None


def flatten_promotions(payload: Any) -> list[dict[str, Any]]:
    """Flatten promotionslist sections into a list of promotion dicts."""
    if isinstance(payload, dict):
        for key in ("data", "result", "payload"):
            nested = payload.get(key)
            if (
                isinstance(nested, (dict, list))
                and "sections" not in payload
                and "promotions" not in payload
            ):
                return flatten_promotions(nested)
        if isinstance(payload.get("sections"), list):
            sections = payload["sections"]
        elif isinstance(payload.get("promotions"), list) or isinstance(
            payload.get("items"), list
        ):
            sections = [payload]
        else:
            return []
    elif isinstance(payload, list):
        sections = payload
    else:
        return []
    promotions: list[dict[str, Any]] = []
    for section in sections:
        if (
            isinstance(section, dict)
            and ("id" in section or "promotionId" in section)
            and "promotions" not in section
            and "items" not in section
        ):
            promotions.append(section)
            continue
        if not isinstance(section, dict):
            continue
        section_name = section.get("name")
        items = section.get("promotions") or section.get("items") or []
        if not isinstance(items, list):
            continue
        for item in items:
            promo = item
            if isinstance(item, dict) and isinstance(item.get("promotion"), dict):
                promo = {
                    **{key: value for key, value in item.items() if key != "promotion"},
                    **item["promotion"],
                }
            if not isinstance(promo, dict):
                continue
            row = dict(promo)
            if section_name and "section" not in row:
                row["section"] = section_name
            promotions.append(row)
    return promotions


def discount_text(discount: Any) -> str | None:
    if isinstance(discount, str) and discount.strip():
        return discount.strip()
    if not isinstance(discount, dict):
        return None
    for key in ("title", "description", "value", "text"):
        value = discount.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def validity_field(validity: Any, key: str) -> str | None:
    if isinstance(validity, dict):
        value = validity.get(key)
        if value is None:
            return None
        return str(value)
    return None


def parse_coupon_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    text = value.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


# Types that cannot be activated with a bare POST /activation (pick a product,
# leaflet offer, lottery). STANDARD / SEGMENTED stay click-to-activate.
_UNACTIVATABLE_TYPES = frozenset(
    {
        "ASSIGNABLE",
        "ASSIGNABLEPROMOTION",
        "OFFER",
        "PRIZE",
    }
)

# Scan & Go / self-checkout: apply during a shopping session, not via Activate.
_SESSION_CHANNELS = frozenset({"SSC", "SCO"})

# Offers for stores other than the user's current/favorite shop.
_OTHER_STORE_SECTIONS = frozenset({"OTHERSTORES", "VENDINGOTHERSTORES"})


def _promo_type_key(value: Any) -> str:
    if value is None:
        return ""
    return "".join(ch for ch in str(value).upper() if ch.isalnum())


def _has_article_groups(item: dict[str, Any]) -> bool:
    groups = item.get("groups")
    return isinstance(groups, list) and bool(groups)


def _levels_locked(item: dict[str, Any]) -> bool:
    levels = item.get("levels")
    if isinstance(levels, dict):
        return _as_bool(levels.get("isLocked"))
    if isinstance(levels, list):
        return any(
            isinstance(level, dict) and _as_bool(level.get("isLocked"))
            for level in levels
        )
    return False


def _has_navigation(item: dict[str, Any]) -> bool:
    url = item.get("navigationURL")
    if isinstance(url, str) and url.strip():
        return True
    ecom = item.get("ecommerceNav")
    return isinstance(ecom, dict) and any(ecom.values())


def _channel_keys(*values: Any) -> set[str]:
    keys: set[str] = set()
    for value in values:
        if isinstance(value, str):
            key = _promo_type_key(value)
            if key:
                keys.add(key)
        elif isinstance(value, list):
            keys.update(_channel_keys(*value))
    return keys


def _is_session_coupon(item: dict[str, Any], section: str | None) -> bool:
    return bool(
        _channel_keys(section, item.get("channel"), item.get("channels"))
        & _SESSION_CHANNELS
    )


def _is_other_store_coupon(item: dict[str, Any], section: str | None) -> bool:
    return bool(
        _channel_keys(section, item.get("channel"), item.get("channels"))
        & _OTHER_STORE_SECTIONS
    )


@dataclass
class Coupon:
    """A Lidl Plus promotion from the coupons list."""

    coupon_id: str
    promotion_id: str | None = None
    title: str | None = None
    section: str | None = None
    promo_type: str | None = None
    channel: str | None = None
    is_activated: bool = False
    is_processing: bool = False
    apologize: bool = False
    is_redeemed: bool = False
    is_locked: bool = False
    needs_selection: bool = False
    has_navigation: bool = False
    session_only: bool = False
    other_store: bool = False
    discount: str | None = None
    valid_from: str | None = None
    valid_to: str | None = None

    def can_activate(self) -> bool:
        """True only for one-tap store coupons the API can activate."""
        if (
            not self.coupon_id
            or self.is_activated
            or self.apologize
            or self.is_processing
            or self.is_redeemed
            or self.is_locked
            or self.needs_selection
            or self.has_navigation
            or self.session_only
            or self.other_store
        ):
            return False
        type_key = _promo_type_key(self.promo_type)
        if type_key in _UNACTIVATABLE_TYPES:
            return False
        return True

    def starts_at(self) -> datetime | None:
        return parse_coupon_dt(self.valid_from)

    def is_upcoming(self, now: datetime) -> bool:
        start = self.starts_at()
        return start is not None and start > now

    def is_expired(self, now: datetime) -> bool:
        end = parse_coupon_dt(self.valid_to)
        return end is not None and end < now

    def is_live(self, now: datetime) -> bool:
        return not self.is_upcoming(now) and not self.is_expired(now)

    def as_dict(self) -> dict[str, Any]:
        now = datetime.now(timezone.utc)
        return {
            "id": self.coupon_id,
            "promotion_id": self.promotion_id,
            "title": self.title,
            "section": self.section,
            "type": self.promo_type,
            "channel": self.channel,
            "is_activated": self.is_activated,
            "is_processing": self.is_processing,
            "apologize": self.apologize,
            "activatable": self.can_activate() and self.is_live(now),
            "upcoming": self.can_activate() and self.is_upcoming(now),
            "discount": self.discount,
            "valid_from": self.valid_from,
            "valid_to": self.valid_to,
        }

    @classmethod
    def from_payload(cls, item: dict[str, Any]) -> Coupon | None:
        raw_id = item.get("id") or item.get("promotionId")
        if not raw_id:
            return None
        availability = (
            item.get("availability")
            if isinstance(item.get("availability"), dict)
            else {}
        )
        promo_type = item.get("type")
        channel = item.get("channel")
        section = str(item["section"]) if item.get("section") else None
        return cls(
            coupon_id=str(raw_id),
            promotion_id=str(item["promotionId"]) if item.get("promotionId") else None,
            title=item.get("title"),
            section=section,
            promo_type=str(promo_type) if promo_type else None,
            channel=str(channel) if channel else None,
            is_activated=_as_bool(item.get("isActivated")),
            is_processing=_as_bool(item.get("isProcessing")),
            apologize=_as_bool(availability.get("apologizeStatus")),
            is_redeemed=_as_bool(item.get("isRedeemed")),
            is_locked=_levels_locked(item),
            needs_selection=_has_article_groups(item),
            has_navigation=_has_navigation(item),
            session_only=_is_session_coupon(item, section),
            other_store=_is_other_store_coupon(item, section),
            discount=discount_text(item.get("discount")),
            valid_from=validity_field(item.get("validity"), "start"),
            valid_to=validity_field(item.get("validity"), "end"),
        )


@dataclass
class CouponReward:
    """A coupon activated by the integration, kept after it leaves the list."""

    coupon_id: str
    claimed_at: str | None = None
    title: str | None = None
    discount: str | None = None
    valid_from: str | None = None
    valid_to: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "coupon_id": self.coupon_id,
            "claimed_at": self.claimed_at,
            "title": self.title,
            "discount": self.discount,
            "valid_from": self.valid_from,
            "valid_to": self.valid_to,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CouponReward | None:
        coupon_id = data.get("coupon_id")
        if not coupon_id:
            return None
        return cls(
            coupon_id=str(coupon_id),
            claimed_at=data.get("claimed_at"),
            title=data.get("title"),
            discount=data.get("discount"),
            valid_from=data.get("valid_from"),
            valid_to=data.get("valid_to"),
        )


@dataclass
class Receipt:
    """A ticket fetched on coordinator refresh, with slim native lines."""

    id: str
    date: str | None = None
    store_name: str | None = None
    receipt_num: str | None = None
    total_price: float | None = None
    currency: str | None = None
    lines: list[dict[str, Any]] = field(default_factory=list)
    payload: Any = None

    def as_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "id": self.id,
            "date": self.date,
            "store_name": self.store_name,
            "receipt_num": self.receipt_num,
            "total_price": self.total_price,
            "currency": self.currency,
            "lines": self.lines,
        }
        if self.lines or not isinstance(self.payload, dict):
            return data
        html_blob = self.payload.get("htmlPrintedReceipt")
        items = self.payload.get("itemsLine")
        data["ticket_type"] = self.payload.get("ticketType")
        data["html_len"] = len(html_blob) if isinstance(html_blob, str) else 0
        data["items_line"] = (
            len(items) if isinstance(items, list) else type(items).__name__
        )
        return data


@dataclass
class LidlPlusData:
    loyalty_id: str | None = None
    tickets: list[dict[str, Any]] = field(default_factory=list)
    last_ticket: dict[str, Any] | None = None
    last_details: dict[str, Any] | None = None
    today_total: float = 0.0
    currency: str | None = None
    receipts: list[Receipt] = field(default_factory=list)
    coupons: list[Coupon] = field(default_factory=list)
    available_coupons: list[Coupon] = field(default_factory=list)
    upcoming_coupons: list[Coupon] = field(default_factory=list)
    last_reward: CouponReward | None = None
    reward_history: list[CouponReward] = field(default_factory=list)
