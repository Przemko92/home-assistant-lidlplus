"""API payload helpers."""

from custom_components.lidl_plus.api import (
    _as_segment_ids,
    api_accept_language,
    as_ticket_detail,
    as_ticket_list,
)
from custom_components.lidl_plus.const import COUPONS_BASE, PROFILE_BASE, TICKETS_BASE
from custom_components.lidl_plus.models import store_id_from_payloads


def test_as_ticket_list_flat():
    assert as_ticket_list([{"id": "a"}, {"id": "b"}]) == [{"id": "a"}, {"id": "b"}]


def test_as_ticket_list_nested_groups():
    payload = [
        {"tickets": [{"id": "a"}]},
        {"tickets": [{"id": "b"}]},
    ]
    assert [item["id"] for item in as_ticket_list(payload)] == ["a", "b"]


def test_as_ticket_list_wrapped_dict():
    payload = {"tickets": [{"id": "a"}]}
    assert as_ticket_list(payload) == [{"id": "a"}]


def test_as_ticket_list_skips_group_headers_without_id():
    payload = {
        "data": {
            "months": [
                {"label": "September", "tickets": [{"id": "new", "totalAmount": 10}]},
            ]
        }
    }
    # months is not a known wrapper; still flatten tickets nested under unknown keys
    assert as_ticket_list(payload["data"]["months"]) == [
        {"id": "new", "totalAmount": 10}
    ]


def test_ticket_detail_unwraps_data_envelope():
    inner = {
        "id": "t1",
        "store": {"name": "Lidl"},
        "itemsLine": [{"name": "Milk"}],
        "sequenceNumber": "12",
    }
    assert as_ticket_detail({"data": inner}) == inner
    assert as_ticket_detail(inner) == inner


def test_ticket_detail_keeps_outer_html_when_unwrapping():
    inner = {"id": "t1", "store": {"name": "Lidl"}, "itemsLine": []}
    html = "<span class='article' data-art-id='1'>x</span>"
    assert as_ticket_detail({"data": inner, "htmlPrintedReceipt": html})[
        "htmlPrintedReceipt"
    ] == html


def test_ticket_detail_keeps_outer_html_when_nested_html_is_null():
    html = "<span class='article' data-art-id='1'>x</span>"
    merged = as_ticket_detail(
        {
            "htmlPrintedReceipt": html,
            "data": {
                "id": "t1",
                "store": {"name": "Lidl"},
                "htmlPrintedReceipt": None,
                "itemsLine": [],
            },
        }
    )
    assert merged["htmlPrintedReceipt"] == html


def test_ticket_accept_language_is_two_letters():
    assert api_accept_language("pl-PL", "PL") == "pl"
    assert api_accept_language("de", "DE") == "de"
    assert api_accept_language("", "HU") == "hu"


def test_country_is_in_api_paths():
    country = "DE"
    assert f"{PROFILE_BASE}/v1/{country}/loyalty" == (
        "https://profile.lidlplus.com/api/v1/DE/loyalty"
    )
    assert f"{TICKETS_BASE}/v3/{country}/tickets" == (
        "https://tickets.lidlplus.com/api/v3/DE/tickets"
    )
    assert f"{COUPONS_BASE}/v4/promotionslist" == (
        "https://coupons.lidlplus.com/app/api/v4/promotionslist"
    )


def test_segment_ids_from_list_and_objects():
    assert _as_segment_ids(["a", "b"]) == ["a", "b"]
    assert _as_segment_ids({"segmentIds": [{"id": "x"}, "y"]}) == ["x", "y"]


def test_store_id_from_ticket_detail():
    assert (
        store_id_from_payloads(
            {"vendor": {"name": "Lidl"}},
            {"store": {"id": "PL1776", "name": "Lidl"}},
        )
        == "PL1776"
    )
