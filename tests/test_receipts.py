"""Last five tickets: download, in-session cache and prune."""

import html
from datetime import datetime, timedelta

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.lidl_plus.const import DEFAULT_AUTO_COUPONS, DOMAIN, MAX_RECEIPTS
from custom_components.lidl_plus.coordinator import LidlPlusCoordinator
from custom_components.lidl_plus.exceptions import LidlPlusError
from custom_components.lidl_plus.models import slim_ticket_lines


def _iso(offset_hours: float, zone) -> str:
    return (datetime.now(zone) + timedelta(hours=offset_hours)).isoformat()


def _ticket(tx_id: str, *, hours: float = 0, zone=None, **kwargs) -> dict:
    date = kwargs.get("date")
    if date is None and zone is not None:
        date = _iso(hours, zone)
    return {
        "id": tx_id,
        "date": date or "2026-01-01T12:00:00+01:00",
        "totalAmount": kwargs.get("totalAmount", 12.5),
        "vendor": {"name": kwargs.get("store_name", f"Store {tx_id}")},
    }


class ReceiptFakeApi:
    """Stand-in covering ticket list and detail."""

    def __init__(self, tickets: list[dict]):
        self._tickets = tickets
        self.ticket_calls: list[str] = []
        self.fail_ids: set[str] = set()
        self.detail_payload: dict | None = None
        self.country = "PL"
        self.language = "pl-PL"

    async def loyalty_id(self) -> str:
        return "1234"

    async def tickets(self, year_offset: int = 0):
        return list(self._tickets)

    async def ticket(self, ticket_id: str):
        self.ticket_calls.append(ticket_id)
        if ticket_id in self.fail_ids:
            raise LidlPlusError("http_404")
        if self.detail_payload is not None:
            return self.detail_payload
        return {
            "id": ticket_id,
            "sequenceNumber": f"R{ticket_id}",
            "store": {"name": f"Store {ticket_id}"},
            "totalAmount": 12.5,
            "currency": {"code": "PLN", "symbol": "zł"},
            "itemsLine": [
                {
                    "name": "Bread",
                    "codeInput": "123",
                    "quantity": "1",
                    "currentUnitPrice": "12.50",
                    "originalAmount": "12.50",
                    "isWeight": False,
                    "taxGroupName": "A",
                }
            ],
        }

    async def promotions_list(self):
        return {"sections": []}


@pytest.fixture
def coordinator_factory(hass):
    def _make(api: ReceiptFakeApi) -> LidlPlusCoordinator:
        entry = MockConfigEntry(
            domain=DOMAIN,
            data={
                "card_number": "1234",
                "country": "PL",
                "language": "pl-PL",
            },
            options={},
        )
        entry.add_to_hass(hass)
        return LidlPlusCoordinator(hass, entry, api)

    return _make


def test_auto_coupons_is_off_by_default(coordinator_factory):
    assert DEFAULT_AUTO_COUPONS is False
    coordinator = coordinator_factory(ReceiptFakeApi([]))
    assert coordinator.auto_coupons is False


async def test_missing_receipts_are_fetched_once(coordinator_factory):
    coordinator = coordinator_factory(ReceiptFakeApi([]))
    api = ReceiptFakeApi(
        [
            _ticket("a", zone=coordinator.zone),
            _ticket("b", zone=coordinator.zone),
            _ticket("c", zone=coordinator.zone),
        ]
    )
    coordinator.api = api

    first = await coordinator._sync_receipts(api._tickets)
    second = await coordinator._sync_receipts(api._tickets)

    assert [receipt.id for receipt in first] == ["a", "b", "c"]
    assert api.ticket_calls == ["a", "b", "c"]
    assert [receipt.id for receipt in second] == ["a", "b", "c"]
    assert api.ticket_calls == ["a", "b", "c"]


async def test_only_the_newest_five_are_kept(coordinator_factory):
    coordinator = coordinator_factory(ReceiptFakeApi([]))
    newest = [_ticket(str(i), hours=-i, zone=coordinator.zone) for i in range(7)]
    api = ReceiptFakeApi(newest)
    coordinator.api = api

    receipts = await coordinator._sync_receipts(newest)

    assert [receipt.id for receipt in receipts] == [str(i) for i in range(MAX_RECEIPTS)]
    assert api.ticket_calls == [str(i) for i in range(MAX_RECEIPTS)]
    assert "5" not in coordinator._receipts
    assert "6" not in coordinator._receipts


async def test_older_cached_receipts_are_pruned(coordinator_factory):
    coordinator = coordinator_factory(ReceiptFakeApi([]))
    first_page = [_ticket(str(i), hours=-i, zone=coordinator.zone) for i in range(5)]
    api = ReceiptFakeApi(first_page)
    coordinator.api = api
    await coordinator._sync_receipts(first_page)

    newer = [_ticket("new", hours=1, zone=coordinator.zone)] + first_page[:4]
    api._tickets = newer
    receipts = await coordinator._sync_receipts(newer)

    assert [receipt.id for receipt in receipts] == ["new", "0", "1", "2", "3"]
    assert "4" not in coordinator._receipts
    assert api.ticket_calls[-1] == "new"


async def test_restart_fetches_receipts_again(coordinator_factory, hass):
    coordinator = coordinator_factory(ReceiptFakeApi([]))
    api = ReceiptFakeApi([_ticket("kept", zone=coordinator.zone)])
    coordinator.api = api
    await coordinator._sync_receipts(api._tickets)

    revived = LidlPlusCoordinator(hass, coordinator.entry, api)
    receipts = await revived._sync_receipts(api._tickets)

    assert [receipt.id for receipt in receipts] == ["kept"]
    assert api.ticket_calls == ["kept", "kept"]


async def test_one_failed_receipt_does_not_drop_the_rest(coordinator_factory):
    coordinator = coordinator_factory(ReceiptFakeApi([]))
    api = ReceiptFakeApi(
        [
            _ticket("ok", zone=coordinator.zone),
            _ticket("bad", zone=coordinator.zone),
            _ticket("also", zone=coordinator.zone),
        ]
    )
    api.fail_ids = {"bad"}
    coordinator.api = api

    receipts = await coordinator._sync_receipts(api._tickets)

    assert [receipt.id for receipt in receipts] == ["ok", "bad", "also"]
    assert api.ticket_calls == ["ok", "bad", "also"]
    assert receipts[1].total_price == 12.5
    assert receipts[1].lines == []


def test_ticket_lines_keep_native_fields_only():
    payload = {
        "itemsLine": [
            {
                "name": "Milk",
                "codeInput": "590",
                "quantity": "2",
                "currentUnitPrice": "3.00",
                "originalAmount": "6.00",
                "isWeight": False,
                "taxGroupName": "A",
                "giftSerialNumber": "secret",
            }
        ]
    }
    assert slim_ticket_lines(payload) == [
        {
            "name": "Milk",
            "codeInput": "590",
            "quantity": "2",
            "currentUnitPrice": "3.00",
            "originalAmount": "6.00",
            "isWeight": False,
        }
    ]


_HTML_UK = """
<span class="article" data-art-id="0000000012345"
      data-art-description="Mleko 2.27L"
      data-art-quantity="1" data-unit-price="1.45" data-tax-type="A">
  Mleko 2.27L  1.45
</span>
<span class="article" data-art-id="0000000067890"
      data-art-description="Banany luz"
      data-art-quantity="0.74" data-unit-price="0.89" data-tax-type="A">
  Banany luz  0.66
</span>
"""

_HTML_DE_FRAGMENTS = (
    '<span id="purchase_list_line_2" class="article css_bold" '
    'data-art-id="0082388" data-unit-price="2,99" data-tax-type="A" '
    'data-art-description="Cherrystrauchtomaten">Cherrystrauchtomaten</span>'
    '<span id="purchase_list_line_2" class="article" data-art-id="0082388" '
    'data-unit-price="2,99" data-tax-type="A" '
    'data-art-description="Cherrystrauchtomaten">         </span>'
    '<span id="purchase_list_line_2" class="article css_bold" data-art-id="0082388" '
    'data-unit-price="2,99" data-tax-type="A" '
    'data-art-description="Cherrystrauchtomaten">2,99</span>'
    '<span id="purchase_list_line_2" class="article" data-art-id="0082388" '
    'data-unit-price="2,99" data-tax-type="A" '
    'data-art-description="Cherrystrauchtomaten">A</span>'
)


def test_ticket_lines_from_html_when_items_line_empty():
    payload = {"itemsLine": [], "htmlPrintedReceipt": _HTML_UK}
    lines = slim_ticket_lines(payload)
    assert [line["name"] for line in lines] == ["Mleko 2.27L", "Banany luz"]
    assert lines[0]["codeInput"] == "0000000012345"
    assert lines[0]["originalAmount"] == "1.45"
    assert lines[0]["isWeight"] is False
    assert lines[1]["quantity"] == "0.74"
    assert lines[1]["isWeight"] is True
    assert lines[1]["originalAmount"] == "0.66"


def test_ticket_lines_merge_html_fragments_with_same_id():
    lines = slim_ticket_lines({"htmlPrintedReceipt": _HTML_DE_FRAGMENTS})
    assert len(lines) == 1
    assert lines[0]["name"] == "Cherrystrauchtomaten"
    assert lines[0]["codeInput"] == "0082388"
    assert lines[0]["currentUnitPrice"] == "2,99"
    assert lines[0]["originalAmount"] == "2.99"


async def test_html_detail_fills_receipt_lines(coordinator_factory):
    coordinator = coordinator_factory(ReceiptFakeApi([]))
    api = ReceiptFakeApi([_ticket("html1", zone=coordinator.zone)])
    api.detail_payload = {
        "id": "html1",
        "sequenceNumber": "Rhtml1",
        "store": {"name": "Lidl Test"},
        "totalAmount": 2.11,
        "currency": {"code": "PLN", "symbol": "zł"},
        "itemsLine": [],
        "htmlPrintedReceipt": _HTML_UK,
    }
    coordinator.api = api

    receipts = await coordinator._sync_receipts(api._tickets)

    assert receipts[0].store_name == "Lidl Test"
    assert [line["name"] for line in receipts[0].lines] == [
        "Mleko 2.27L",
        "Banany luz",
    ]


def test_ticket_lines_from_escaped_html():
    escaped = html.escape(_HTML_UK)
    lines = slim_ticket_lines({"htmlPrintedReceipt": escaped})
    assert [line["name"] for line in lines] == ["Mleko 2.27L", "Banany luz"]


def test_ticket_lines_from_nested_html_key():
    payload = {
        "store": {"name": "Lidl"},
        "ticket": {"htmlPrintedReceipt": _HTML_UK},
        "itemsLine": [],
    }
    assert slim_ticket_lines(payload)[0]["name"] == "Mleko 2.27L"


def test_ticket_lines_from_pascal_case_and_values_wrapper():
    payload = {
        "itemsLine": {
            "$values": [
                {
                    "Name": "Mleko",
                    "CodeInput": "590",
                    "Quantity": "1",
                    "CurrentUnitPrice": "3.49",
                    "OriginalAmount": "3.49",
                    "IsWeight": False,
                }
            ]
        }
    }
    assert slim_ticket_lines(payload)[0]["name"] == "Mleko"
    assert slim_ticket_lines(payload)[0]["codeInput"] == "590"


def test_ticket_lines_from_preformatted_html():
    payload = {
        "htmlPrintedReceipt": (
            "<pre>MLEKO UHT 1L              3,49 A\n"
            "BANANY LUZ               6,20 A\n"
            "SUMA PLN                    9,69\n"
            "</pre>"
        )
    }
    lines = slim_ticket_lines(payload)
    assert [line["name"] for line in lines] == ["MLEKO UHT 1L", "BANANY LUZ"]
    assert lines[0]["originalAmount"] == "3.49"


async def test_empty_cached_lines_are_refetched(coordinator_factory):
    coordinator = coordinator_factory(ReceiptFakeApi([]))
    api = ReceiptFakeApi([_ticket("html1", zone=coordinator.zone)])
    api.detail_payload = {
        "id": "html1",
        "sequenceNumber": "Rhtml1",
        "store": {"name": "Lidl Test"},
        "totalAmount": 2.11,
        "itemsLine": [],
    }
    coordinator.api = api
    first = await coordinator._sync_receipts(api._tickets)
    assert first[0].lines == []

    api.detail_payload = {
        "id": "html1",
        "sequenceNumber": "Rhtml1",
        "store": {"name": "Lidl Test"},
        "totalAmount": 2.11,
        "itemsLine": [],
        "htmlPrintedReceipt": _HTML_UK,
    }
    second = await coordinator._sync_receipts(api._tickets)
    assert [line["name"] for line in second[0].lines] == [
        "Mleko 2.27L",
        "Banany luz",
    ]


async def test_receipt_is_stored_without_payload_in_as_dict(coordinator_factory):
    coordinator = coordinator_factory(ReceiptFakeApi([]))
    api = ReceiptFakeApi([_ticket("t1", zone=coordinator.zone)])
    coordinator.api = api

    receipts = await coordinator._sync_receipts(api._tickets)
    exported = receipts[0].as_dict()

    assert exported["id"] == "t1"
    assert "payload" not in exported
    assert exported["lines"][0]["name"] == "Bread"
    assert receipts[0].payload is not None


async def test_empty_lines_expose_parse_hints(coordinator_factory):
    coordinator = coordinator_factory(ReceiptFakeApi([]))
    api = ReceiptFakeApi([_ticket("hint", zone=coordinator.zone)])
    api.detail_payload = {
        "id": "hint",
        "ticketType": "HTML",
        "store": {"name": "Lidl"},
        "sequenceNumber": "1",
        "itemsLine": [],
        "htmlPrintedReceipt": "",
    }
    coordinator.api = api

    receipts = await coordinator._sync_receipts(api._tickets)
    exported = receipts[0].as_dict()

    assert exported["lines"] == []
    assert exported["ticket_type"] == "HTML"
    assert exported["html_len"] == 0
    assert exported["items_line"] == 0


async def test_failed_detail_keeps_list_ticket(coordinator_factory):
    coordinator = coordinator_factory(ReceiptFakeApi([]))
    api = ReceiptFakeApi(
        [_ticket("kept", zone=coordinator.zone, totalAmount=52.84, store_name="List")]
    )
    api.fail_ids = {"kept"}
    coordinator.api = api

    receipts = await coordinator._sync_receipts(api._tickets)

    assert len(receipts) == 1
    assert receipts[0].id == "kept"
    assert receipts[0].total_price == 52.84
    assert receipts[0].date is not None
